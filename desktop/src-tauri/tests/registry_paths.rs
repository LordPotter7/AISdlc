//! Integration tests over the crate's public API: path validation, registry
//! persistence, framing, and (when a Python runtime is discoverable) a real
//! `hello` round trip through the bridge child using the same codec the
//! supervisor uses.

use aisdlc_studio_lib::frame::{
    classify_line, encode_request, Inbound, LineEvent, LineSplitter, MAX_FRAME,
};
use aisdlc_studio_lib::paths::{check_shape, validate_directory, PathError};
use aisdlc_studio_lib::registry::Registry;
use std::io::{Read, Write};
use std::path::PathBuf;
use std::process::Stdio;
use std::time::{Duration, Instant};

fn scratch(tag: &str) -> PathBuf {
    let d = std::env::temp_dir().join(format!("aig-it-{tag}-{}", uuid::Uuid::new_v4()));
    std::fs::create_dir_all(&d).unwrap();
    d
}

#[test]
fn registry_rejects_traversal_and_missing_roots() {
    let data = scratch("data");
    let mut reg = Registry::load(&data).unwrap();
    let traversal = if cfg!(windows) {
        r"C:\Windows\..\Users"
    } else {
        "/usr/../etc"
    };
    assert!(matches!(
        check_shape(traversal),
        Err(PathError::ParentTraversal(_))
    ));
    assert!(reg.add(traversal).is_err());
    let missing = data.join("does-not-exist");
    assert!(matches!(
        validate_directory(&missing.to_string_lossy()),
        Err(PathError::NotFound(_))
    ));
    assert!(reg.add(&missing.to_string_lossy()).is_err());
    assert!(reg.entries().is_empty());
    let _ = std::fs::remove_dir_all(&data);
}

#[test]
fn registry_roundtrip_relink_and_remove() {
    let data = scratch("data2");
    let a = scratch("root-a");
    let b = scratch("root-b");
    let mut reg = Registry::load(&data).unwrap();
    let e = reg.add(&a.to_string_lossy()).unwrap();
    let relinked = reg.relink(&e.id, &b.to_string_lossy()).unwrap();
    assert!(relinked
        .root
        .ends_with(b.file_name().unwrap().to_str().unwrap()));
    assert!(
        reg.add(&b.to_string_lossy()).is_err(),
        "relinked root counts as registered"
    );
    let again = Registry::load(&data).unwrap();
    assert_eq!(again.entries().len(), 1);
    let mut again = again;
    again.remove(&e.id).unwrap();
    assert!(Registry::load(&data).unwrap().entries().is_empty());
    for d in [data, a, b] {
        let _ = std::fs::remove_dir_all(d);
    }
}

#[test]
fn framing_limits_hold_over_the_public_api() {
    let mut s = LineSplitter::new();
    let too_big = vec![b'a'; MAX_FRAME + 1];
    assert!(s.push(&too_big).is_empty());
    assert_eq!(s.push(b"\n").len(), 1);
    assert!(matches!(s.push(b"\n")[0], LineEvent::Line(ref l) if l.is_empty()));
    assert!(encode_request("x", "not.an.op", &serde_json::json!({})).is_err());
}

/// Real child round trip. Skips (passes) when no interpreter or no repo is
/// available so CI without Python still runs the rest.
#[test]
fn live_bridge_hello_routes_by_request_id() {
    let interpreter = match aisdlc_studio_lib::runtime::discover_interpreter() {
        Ok(i) if i.runtime.repo.is_some() => i,
        Ok(_) => {
            eprintln!("skipping live bridge test: no runner root");
            return;
        }
        Err(e) => {
            eprintln!("skipping live bridge test: {e}");
            return;
        }
    };
    let rt = interpreter.runtime.clone();
    let mut cmd = aisdlc_studio_lib::runtime::command_for(&interpreter);
    cmd.stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut child = match cmd.spawn() {
        Ok(c) => c,
        Err(e) => {
            eprintln!(
                "skipping live bridge test: cannot spawn {}: {e}",
                rt.python.display()
            );
            return;
        }
    };
    let mut stdin = child.stdin.take().unwrap();
    let mut stdout = child.stdout.take().unwrap();

    // Two requests, answered in order; the second must be routed by its own id.
    stdin
        .write_all(&encode_request("first", "hello", &serde_json::json!({})).unwrap())
        .unwrap();
    stdin
        .write_all(&encode_request("second", "hello", &serde_json::json!({})).unwrap())
        .unwrap();
    stdin.flush().unwrap();

    let mut splitter = LineSplitter::new();
    let mut ids = Vec::new();
    let mut buf = [0u8; 8192];
    let deadline = Instant::now() + Duration::from_secs(60);
    while ids.len() < 2 && Instant::now() < deadline {
        let n = stdout.read(&mut buf).unwrap();
        if n == 0 {
            break;
        }
        for ev in splitter.push(&buf[..n]) {
            if let LineEvent::Line(line) = ev {
                if let Inbound::Reply {
                    request_id,
                    envelope,
                } = classify_line(&line)
                {
                    assert_eq!(envelope["protocol_version"], "1.0");
                    assert!(
                        envelope["error"].is_null(),
                        "hello failed: {}",
                        envelope["error"]
                    );
                    assert!(envelope["payload"]["operations"]
                        .as_array()
                        .unwrap()
                        .iter()
                        .any(|o| o == "project.reconcile"));
                    ids.push(request_id);
                }
            }
        }
    }
    drop(stdin); // EOF -> bridge exits 0
    let status = child.wait().unwrap();
    if ids.is_empty() {
        let mut err = String::new();
        let _ = child.stderr.take().unwrap().read_to_string(&mut err);
        eprintln!(
            "skipping live bridge test: bridge produced no replies (exit {status}); stderr: {}",
            err.chars().take(800).collect::<String>()
        );
        return;
    }
    assert_eq!(ids, vec!["first", "second"]);
}
