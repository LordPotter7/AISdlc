//! Python runtime and runner discovery for the bridge child process.
//!
//! The bridge is the runner's own `aisdlc.desktop` package, run from the plugin root
//! that holds `scripts/aisdlc/`. Studio never ships a second copy of the runner.
//!
//! Plugin root (first that contains `scripts/aisdlc/desktop/bridge.py`):
//! 1. `AISDLC_PLUGIN_ROOT`
//! 2. a checkout found by walking up from the executable or the working directory
//!    (development: `desktop/src-tauri/target/...` sits inside the checkout)
//! 3. the `installPath` of an `aisdlc@<marketplace>` entry in
//!    `~/.claude/plugins/installed_plugins.json` (the record Claude Code keeps)
//!
//! Interpreter (first that can import `aisdlc.desktop` with `<root>/scripts` on
//! `PYTHONPATH`):
//! 1. `AISDLC_BRIDGE_PYTHON` (authoritative: if it cannot import, discovery fails)
//! 2. `<root>/.venv`
//! 3. `python`, `python3`, then the `py` launcher on `PATH`
//!
//! Each candidate is probed before it is chosen, so an interpreter that is too old
//! (the runner needs 3.12+) is reported rather than relaunched forever. The interpreter
//! only ever runs with fixed arguments.

use serde::Serialize;
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Serialize)]
pub struct Runtime {
    pub python: PathBuf,
    /// Which rule selected the interpreter (for `app_info` and diagnostics).
    pub source: String,
    /// The plugin root whose `scripts/aisdlc` the bridge runs.
    pub repo: Option<PathBuf>,
    /// How the plugin root was found.
    pub root_source: String,
    /// Kept for wire compatibility with the governance host; unused here.
    pub plugin_data: Option<PathBuf>,
    /// Candidates that were found but failed the import probe, for diagnostics.
    pub skipped: Vec<String>,
}

const PROBE_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(15);
const PROBE: &str = "import sys; assert sys.version_info >= (3, 12), 'Python 3.12+ required'; import aisdlc.desktop";

fn harden(cmd: &mut std::process::Command, root: Option<&Path>) {
    cmd.env("PYTHONUNBUFFERED", "1");
    cmd.env("PYTHONUTF8", "1");
    cmd.env("PYTHONDONTWRITEBYTECODE", "1");
    if let Some(root) = root {
        let mut parts = vec![root.join("scripts")];
        if let Some(existing) = std::env::var_os("PYTHONPATH") {
            parts.extend(std::env::split_paths(&existing));
        }
        if let Ok(joined) = std::env::join_paths(parts) {
            cmd.env("PYTHONPATH", joined);
        }
        cmd.current_dir(root);
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
}

fn base_command(python: &Path, launcher_args: &[String]) -> std::process::Command {
    let mut cmd = std::process::Command::new(python);
    cmd.args(launcher_args);
    cmd
}

/// Run the import probe and report why it failed.
pub fn probe(python: &Path, launcher_args: &[String], root: Option<&Path>) -> Result<(), String> {
    use std::io::Read;

    let mut cmd = base_command(python, launcher_args);
    cmd.args(["-X", "utf8", "-c", PROBE]);
    harden(&mut cmd, root);
    cmd.stdin(std::process::Stdio::null());
    cmd.stdout(std::process::Stdio::null());
    cmd.stderr(std::process::Stdio::piped());
    let mut child = cmd
        .spawn()
        .map_err(|e| format!("{}: cannot start ({e})", python.display()))?;
    let deadline = std::time::Instant::now() + PROBE_TIMEOUT;
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) if std::time::Instant::now() < deadline => {
                std::thread::sleep(std::time::Duration::from_millis(25));
            }
            Ok(None) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(format!("{}: import probe timed out", python.display()));
            }
            Err(e) => return Err(format!("{}: probe failed ({e})", python.display())),
        }
    };
    if status.success() {
        return Ok(());
    }
    let mut stderr = String::new();
    if let Some(mut pipe) = child.stderr.take() {
        let _ = pipe.read_to_string(&mut stderr);
    }
    let last = stderr
        .lines()
        .rev()
        .find(|l| !l.trim().is_empty())
        .unwrap_or("no error output")
        .trim()
        .to_string();
    Err(format!(
        "{}: cannot import aisdlc.desktop ({last})",
        python.display()
    ))
}

fn venv_python(venv: &Path) -> PathBuf {
    if cfg!(windows) {
        venv.join("Scripts").join("python.exe")
    } else {
        venv.join("bin").join("python")
    }
}

/// Wire compatibility with the governance host, which passes plugin data to its bridge.
pub fn plugin_data_dir() -> Option<PathBuf> {
    None
}

pub fn looks_like_root(dir: &Path) -> bool {
    dir.join("scripts")
        .join("aisdlc")
        .join("desktop")
        .join("bridge.py")
        .is_file()
}

fn installed_root() -> Option<PathBuf> {
    let home = std::env::var_os("USERPROFILE").or_else(|| std::env::var_os("HOME"))?;
    let registry = PathBuf::from(home)
        .join(".claude")
        .join("plugins")
        .join("installed_plugins.json");
    let text = std::fs::read_to_string(registry).ok()?;
    let value: serde_json::Value = serde_json::from_str(&text).ok()?;
    let plugins = value.get("plugins")?.as_object()?;
    for (key, entry) in plugins {
        if key.split('@').next() != Some("aisdlc") {
            continue;
        }
        let records: Vec<&serde_json::Value> = match entry {
            serde_json::Value::Array(items) => items.iter().collect(),
            other => vec![other],
        };
        for record in records {
            if let Some(path) = record.get("installPath").and_then(|p| p.as_str()) {
                let candidate = PathBuf::from(path);
                if looks_like_root(&candidate) {
                    return Some(candidate);
                }
            }
        }
    }
    None
}

/// Locate the plugin root the bridge should run from, and say how it was found.
pub fn find_root() -> Option<(PathBuf, String)> {
    if let Some(v) = std::env::var_os("AISDLC_PLUGIN_ROOT") {
        let p = PathBuf::from(v);
        if looks_like_root(&p) {
            return Some((p, "env:AISDLC_PLUGIN_ROOT".into()));
        }
    }
    let mut starts: Vec<PathBuf> = Vec::new();
    if let Ok(exe) = std::env::current_exe() {
        starts.push(exe);
    }
    if let Ok(cwd) = std::env::current_dir() {
        starts.push(cwd);
    }
    for start in starts {
        let mut cur: Option<&Path> = Some(start.as_path());
        for _ in 0..10 {
            match cur {
                Some(dir) => {
                    if looks_like_root(dir) {
                        return Some((dir.to_path_buf(), "checkout".into()));
                    }
                    cur = dir.parent();
                }
                None => break,
            }
        }
    }
    installed_root().map(|p| (p, "installed plugin".into()))
}

/// Kept for callers written against the governance host.
pub fn find_repo() -> Option<PathBuf> {
    find_root().map(|(p, _)| p)
}

fn on_path(name: &str) -> Option<PathBuf> {
    let path = std::env::var_os("PATH")?;
    let exts: Vec<String> = if cfg!(windows) {
        vec![".exe".into(), String::new()]
    } else {
        vec![String::new()]
    };
    for dir in std::env::split_paths(&path) {
        for ext in &exts {
            let candidate = dir.join(format!("{name}{ext}"));
            if candidate.is_file() {
                // Skip the Windows Store alias stub, which opens the store instead.
                if candidate.to_string_lossy().contains("WindowsApps") {
                    continue;
                }
                return Some(candidate);
            }
        }
    }
    None
}

/// The chosen interpreter plus any launcher arguments (`py -3`).
#[derive(Debug, Clone)]
pub struct Interpreter {
    pub runtime: Runtime,
    pub launcher_args: Vec<String>,
}

pub fn discover() -> Result<Runtime, String> {
    discover_interpreter().map(|i| i.runtime)
}

pub fn discover_interpreter() -> Result<Interpreter, String> {
    let (root, root_source) = find_root().ok_or_else(|| {
        "the AISDLC runner was not found: install the aisdlc plugin, run from a checkout, or set AISDLC_PLUGIN_ROOT"
            .to_string()
    })?;

    if let Some(explicit) = std::env::var_os("AISDLC_BRIDGE_PYTHON") {
        let p = PathBuf::from(explicit);
        if !p.is_file() {
            return Err(format!(
                "AISDLC_BRIDGE_PYTHON is set but not a file: {}",
                p.display()
            ));
        }
        probe(&p, &[], Some(&root)).map_err(|e| format!("AISDLC_BRIDGE_PYTHON {e}"))?;
        return Ok(Interpreter {
            runtime: Runtime {
                python: p,
                source: "env:AISDLC_BRIDGE_PYTHON".into(),
                repo: Some(root),
                root_source,
                plugin_data: None,
                skipped: Vec::new(),
            },
            launcher_args: Vec::new(),
        });
    }

    let mut candidates: Vec<(PathBuf, Vec<String>, &str)> = Vec::new();
    let venv = venv_python(&root.join(".venv"));
    if venv.is_file() {
        candidates.push((venv, Vec::new(), "plugin .venv"));
    }
    if let Some(p) = on_path("python") {
        candidates.push((p, Vec::new(), "PATH python"));
    }
    if let Some(p) = on_path("python3") {
        candidates.push((p, Vec::new(), "PATH python3"));
    }
    if cfg!(windows) {
        if let Some(p) = on_path("py") {
            candidates.push((p, vec!["-3".into()], "py launcher"));
        }
    }
    if candidates.is_empty() {
        return Err(
            "no Python interpreter found: install Python 3.12+ or set AISDLC_BRIDGE_PYTHON".into(),
        );
    }
    let mut skipped: Vec<String> = Vec::new();
    for (python, launcher_args, source) in candidates {
        match probe(&python, &launcher_args, Some(&root)) {
            Ok(()) => {
                return Ok(Interpreter {
                    runtime: Runtime {
                        python,
                        source: source.into(),
                        repo: Some(root),
                        root_source,
                        plugin_data: None,
                        skipped,
                    },
                    launcher_args,
                })
            }
            Err(reason) => {
                log::warn!("bridge runtime: {source} rejected: {reason}");
                skipped.push(format!("{source}: {reason}"));
            }
        }
    }
    Err(format!(
        "no Python interpreter can run the AISDLC bridge: {}",
        skipped.join("; ")
    ))
}

/// Build the child command with fixed arguments and a hardened environment.
pub fn command_for(interpreter: &Interpreter) -> std::process::Command {
    let mut cmd = base_command(&interpreter.runtime.python, &interpreter.launcher_args);
    cmd.args(["-X", "utf8", "-m", "aisdlc.desktop"]);
    harden(&mut cmd, interpreter.runtime.repo.as_deref());
    cmd
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn probe_rejects_a_missing_interpreter() {
        let missing = std::env::temp_dir().join("aisdlc-no-such-python-0000");
        let err = probe(&missing, &[], None).expect_err("missing file cannot be probed");
        assert!(err.contains("cannot start"), "{err}");
    }

    #[test]
    fn a_checkout_is_recognised_by_its_bridge_module() {
        let here = Path::new(env!("CARGO_MANIFEST_DIR"));
        let root = here
            .parent()
            .and_then(Path::parent)
            .expect("desktop/src-tauri sits in the checkout");
        assert!(looks_like_root(root), "{}", root.display());
        assert!(!looks_like_root(here));
    }
}
