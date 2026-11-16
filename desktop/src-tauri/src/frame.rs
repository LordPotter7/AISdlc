//! Wire framing for the Python bridge (docs/desktop-bridge-contract.md).
//!
//! One JSON object per line, UTF-8, at most `MAX_FRAME` bytes. This module is
//! pure (no I/O) so the codec, the routing classification and the restart
//! backoff can be unit-tested without a child process.

use serde_json::Value;
use std::time::Duration;

/// Maximum bytes in one line, in either direction (matches `bridge.MAX_FRAME`).
pub const MAX_FRAME: usize = 4 * 1024 * 1024;
/// A request waits this long for its reply before the command fails.
pub const REQUEST_TIMEOUT: Duration = Duration::from_secs(30);
/// Requests in flight at once; more are refused rather than queued.
pub const MAX_PENDING: usize = 256;
/// While the first child is still launching, a request waits this long for it
/// instead of failing: the UI's first calls race the spawn at app start.
pub const STARTUP_WAIT: Duration = Duration::from_secs(20);

/// Operations the host forwards. Copied from the contract table; anything else
/// is refused in Rust before it reaches the child.
pub const ALLOWED_OPS: &[&str] = &[
    "hello",
    "project.open",
    "project.close",
    "project.snapshot",
    "project.reconcile",
    "project.subscribe",
    "project.unsubscribe",
    "project.cursors",
    "intent.inspect",
    "gate.packet",
    "spec.inspect",
    "trace.inspect",
    "story.inspect",
    "events.page",
    "diagnostics.inspect",
    "learn.inspect",
    "artifact.read",
    "artifact.check",
    "authoring.compose",
    "overview.inspect",
    "inbox.inspect",
    "codebase.inspect",
    "portfolio.inspect",
    "pipeline.inspect",
    "operation.preview",
    "operation.execute",
    "operation.cancel",
];

pub fn is_allowed_op(op: &str) -> bool {
    ALLOWED_OPS.contains(&op)
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum FrameError {
    OpNotAllowed(String),
    TooLarge(usize),
    Serialize(String),
}

impl std::fmt::Display for FrameError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            FrameError::OpNotAllowed(op) => write!(f, "operation not allowed: {op}"),
            FrameError::TooLarge(n) => write!(f, "frame of {n} bytes exceeds {MAX_FRAME}"),
            FrameError::Serialize(e) => write!(f, "cannot serialise request: {e}"),
        }
    }
}

/// Build the bytes for one request line (including the trailing newline).
pub fn encode_request(id: &str, op: &str, params: &Value) -> Result<Vec<u8>, FrameError> {
    if !is_allowed_op(op) {
        return Err(FrameError::OpNotAllowed(op.to_string()));
    }
    let params = if params.is_object() {
        params.clone()
    } else {
        Value::Object(Default::default())
    };
    let body = serde_json::json!({ "id": id, "op": op, "params": params });
    let mut bytes = serde_json::to_vec(&body).map_err(|e| FrameError::Serialize(e.to_string()))?;
    if bytes.len() > MAX_FRAME {
        return Err(FrameError::TooLarge(bytes.len()));
    }
    bytes.push(b'\n');
    Ok(bytes)
}

/// What an inbound line turned out to be.
#[derive(Debug, Clone, PartialEq)]
pub enum Inbound {
    /// Unsolicited `{"kind":"notification", ...}` line: forward to the window.
    Notification(Value),
    /// A reply envelope carrying `request_id`: route to the waiting request.
    Reply { request_id: String, envelope: Value },
    /// Valid JSON, but neither of the above (e.g. the bridge's own
    /// `malformed`/`oversized` error envelopes, which have no waiter).
    Unrouted(Value),
    /// Not JSON at all.
    Malformed(String),
}

pub fn classify_line(line: &str) -> Inbound {
    let trimmed = line.trim();
    if trimmed.is_empty() {
        return Inbound::Malformed(String::new());
    }
    let value: Value = match serde_json::from_str(trimmed) {
        Ok(v) => v,
        Err(e) => return Inbound::Malformed(e.to_string()),
    };
    if value.get("kind").and_then(Value::as_str) == Some("notification") {
        return Inbound::Notification(value);
    }
    match value.get("request_id").and_then(Value::as_str) {
        Some(id)
            if !id.is_empty() && id != "anonymous" && id != "malformed" && id != "oversized" =>
        {
            Inbound::Reply {
                request_id: id.to_string(),
                envelope: value,
            }
        }
        _ => Inbound::Unrouted(value),
    }
}

/// Incremental splitter: feed byte chunks, take complete lines out. A line
/// longer than `MAX_FRAME` is discarded (its bytes are skipped until the next
/// newline) and reported once as `LineEvent::Oversized`.
#[derive(Debug, Default)]
pub struct LineSplitter {
    buf: Vec<u8>,
    skipping: bool,
    skipped: usize,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LineEvent {
    Line(String),
    Oversized(usize),
}

impl LineSplitter {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn push(&mut self, chunk: &[u8]) -> Vec<LineEvent> {
        let mut out = Vec::new();
        for &b in chunk {
            if self.skipping {
                self.skipped += 1;
                if b == b'\n' {
                    out.push(LineEvent::Oversized(self.skipped));
                    self.skipping = false;
                    self.skipped = 0;
                }
                continue;
            }
            if b == b'\n' {
                let raw = std::mem::take(&mut self.buf);
                out.push(LineEvent::Line(String::from_utf8_lossy(&raw).into_owned()));
                continue;
            }
            self.buf.push(b);
            if self.buf.len() > MAX_FRAME {
                self.skipped = self.buf.len();
                self.buf.clear();
                self.skipping = true;
            }
        }
        out
    }

    /// Bytes currently buffered for an incomplete line.
    pub fn pending_len(&self) -> usize {
        self.buf.len()
    }
}

/// Restart delay for the n-th consecutive failure (0-based): 1s, 2s, 4s ... 30s.
pub fn backoff_delay(attempt: u32) -> Duration {
    let secs = 1u64.checked_shl(attempt.min(10)).unwrap_or(30).min(30);
    Duration::from_secs(secs)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn encode_rejects_unknown_op() {
        let err = encode_request("r1", "shell.exec", &serde_json::json!({})).unwrap_err();
        assert_eq!(err, FrameError::OpNotAllowed("shell.exec".into()));
    }

    #[test]
    fn encode_produces_one_line_with_object_params() {
        let bytes = encode_request("r1", "hello", &Value::Null).unwrap();
        assert!(bytes.ends_with(b"\n"));
        assert_eq!(bytes.iter().filter(|b| **b == b'\n').count(), 1);
        let v: Value = serde_json::from_slice(&bytes[..bytes.len() - 1]).unwrap();
        assert_eq!(v["id"], "r1");
        assert_eq!(v["op"], "hello");
        assert!(v["params"].is_object());
    }

    #[test]
    fn encode_rejects_oversized() {
        let big = "x".repeat(MAX_FRAME);
        let err =
            encode_request("r1", "events.page", &serde_json::json!({ "cursor": big })).unwrap_err();
        assert!(matches!(err, FrameError::TooLarge(_)));
    }

    #[test]
    fn splitter_yields_lines_across_chunks() {
        let mut s = LineSplitter::new();
        assert!(s.push(b"{\"a\":1").is_empty());
        let ev = s.push(b"}\n{\"b\":2}\n{\"c\"");
        assert_eq!(
            ev,
            vec![
                LineEvent::Line("{\"a\":1}".into()),
                LineEvent::Line("{\"b\":2}".into())
            ]
        );
        assert_eq!(s.pending_len(), 4);
        assert_eq!(s.push(b":3}\n"), vec![LineEvent::Line("{\"c\":3}".into())]);
    }

    #[test]
    fn splitter_discards_oversized_line_and_recovers() {
        let mut s = LineSplitter::new();
        let huge = vec![b'x'; MAX_FRAME + 10];
        assert!(s.push(&huge).is_empty());
        assert_eq!(s.pending_len(), 0);
        let ev = s.push(b"yy\n{\"ok\":true}\n");
        assert_eq!(ev.len(), 2);
        assert!(matches!(ev[0], LineEvent::Oversized(n) if n > MAX_FRAME));
        assert_eq!(ev[1], LineEvent::Line("{\"ok\":true}".into()));
    }

    #[test]
    fn classify_routes_by_request_id() {
        let reply = classify_line(
            r#"{"protocol_version":"1.0","request_id":"r7","kind":"context","payload":{}}"#,
        );
        assert!(matches!(reply, Inbound::Reply { ref request_id, .. } if request_id == "r7"));
        let note = classify_line(
            r#"{"kind":"notification","event":"generation","project_id":"p","generation":3}"#,
        );
        assert!(matches!(note, Inbound::Notification(_)));
        assert!(matches!(
            classify_line(r#"{"request_id":"malformed","kind":"error"}"#),
            Inbound::Unrouted(_)
        ));
        assert!(matches!(classify_line("not json"), Inbound::Malformed(_)));
    }

    #[test]
    fn backoff_sequence_is_capped() {
        let seq: Vec<u64> = (0..8).map(|i| backoff_delay(i).as_secs()).collect();
        assert_eq!(seq, vec![1, 2, 4, 8, 16, 30, 30, 30]);
        assert_eq!(backoff_delay(200).as_secs(), 30);
    }

    #[test]
    fn allowlist_matches_contract_table() {
        // The allowlist must equal the Python bridge's HANDLERS table exactly: an op the
        // bridge lacks would fail at runtime, and an op the host lacks is unreachable.
        let bridge = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../scripts/aisdlc/desktop/bridge.py");
        let source = std::fs::read_to_string(&bridge).expect("bridge.py is in the checkout");
        let start = source
            .find("HANDLERS: dict[str, Handler] = {")
            .expect("HANDLERS table");
        let table = &source[start..start + source[start..].find("\n}").expect("table end")];
        let mut python_ops: Vec<&str> = table
            .lines()
            .filter_map(|line| line.trim().strip_prefix('"'))
            .filter_map(|rest| rest.split('"').next())
            .collect();
        python_ops.sort_unstable();
        let mut host_ops: Vec<&str> = ALLOWED_OPS.to_vec();
        host_ops.sort_unstable();
        assert_eq!(host_ops, python_ops);
        assert!(!is_allowed_op("project.delete"));
    }
}
