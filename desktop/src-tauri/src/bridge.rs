//! Supervised Python bridge over stdio.
//!
//! One child (`python -m aisdlc.desktop`), one writer, one reader
//! thread. Requests are routed to waiting callers by `request_id`;
//! notification lines are re-emitted to the window as `bridge://notification`.
//! A child exit is reported as `bridge://status {state:"unavailable"}` and the
//! child is relaunched with exponential backoff; projects that were open are
//! re-opened with their persisted cursors so the UI sees
//! `connecting -> current` without user action.

use crate::frame::{
    backoff_delay, classify_line, encode_request, Inbound, LineEvent, LineSplitter, MAX_PENDING,
    REQUEST_TIMEOUT, STARTUP_WAIT,
};
use crate::runtime::{self, Runtime};
use serde::Serialize;
use serde_json::{json, Value};
use std::collections::HashMap;
use std::io::{Read, Write};
use std::process::{Child, ChildStdin, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64, Ordering};
use std::sync::mpsc::{sync_channel, RecvTimeoutError, SyncSender};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;
use tauri::{AppHandle, Emitter};

pub const EVENT_NOTIFICATION: &str = "bridge://notification";
pub const EVENT_STATUS: &str = "bridge://status";

#[derive(Debug, Clone, Serialize)]
pub struct BridgeError {
    pub code: String,
    pub message: String,
}

impl BridgeError {
    pub fn new(code: &str, message: impl Into<String>) -> Self {
        Self {
            code: code.into(),
            message: message.into(),
        }
    }
}

impl std::fmt::Display for BridgeError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}: {}", self.code, self.message)
    }
}

/// Mirrors the contract's freshness vocabulary for the transport itself.
#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum State {
    Connecting,
    Current,
    Stale,
    Unavailable,
    Error,
}

#[derive(Debug, Clone, Serialize)]
pub struct Status {
    pub state: State,
    pub detail: String,
    pub at: String,
    pub python: Option<String>,
    pub python_source: Option<String>,
    pub python_skipped: Vec<String>,
    pub repo: Option<String>,
    pub pid: Option<u32>,
    pub restarts: u32,
    pub pending: usize,
    pub engine: Option<Value>,
}

/// What is needed to re-open a project after a child restart.
#[derive(Debug, Clone)]
pub struct ReopenSpec {
    pub root: String,
    pub cursors: Option<Value>,
    pub plugin_data: Option<String>,
    pub active: bool,
}

struct Live {
    stdin: ChildStdin,
    pid: u32,
}

struct Inner {
    app: AppHandle,
    runtime: Mutex<Option<Runtime>>,
    live: Mutex<Option<Live>>,
    pending: Mutex<HashMap<String, SyncSender<Value>>>,
    status: Mutex<Status>,
    reopen: Mutex<HashMap<String, ReopenSpec>>,
    seq: AtomicU64,
    failures: AtomicU32,
    restarts: AtomicU32,
    shutdown: AtomicBool,
    restart_scheduled: AtomicBool,
    child_handle: Mutex<Option<Child>>,
}

#[derive(Clone)]
pub struct Supervisor {
    inner: Arc<Inner>,
}

impl Supervisor {
    pub fn new(app: AppHandle) -> Self {
        let status = Status {
            state: State::Connecting,
            detail: "starting".into(),
            at: crate::registry::now_iso(),
            python: None,
            python_source: None,
            python_skipped: Vec::new(),
            repo: None,
            pid: None,
            restarts: 0,
            pending: 0,
            engine: None,
        };
        Self {
            inner: Arc::new(Inner {
                app,
                runtime: Mutex::new(None),
                live: Mutex::new(None),
                pending: Mutex::new(HashMap::new()),
                status: Mutex::new(status),
                reopen: Mutex::new(HashMap::new()),
                seq: AtomicU64::new(1),
                failures: AtomicU32::new(0),
                restarts: AtomicU32::new(0),
                shutdown: AtomicBool::new(false),
                restart_scheduled: AtomicBool::new(false),
                child_handle: Mutex::new(None),
            }),
        }
    }

    /// Launch the child in the background; never blocks the caller.
    pub fn start(&self) {
        let me = self.clone();
        thread::Builder::new()
            .name("bridge-start".into())
            .spawn(move || {
                if let Err(e) = me.spawn_child() {
                    // A child that started but never answered `hello` is still
                    // alive here; kill it first or `on_child_exit` waits forever
                    // and the reader threads stay blocked on its pipes.
                    me.kill_child();
                    me.on_child_exit(format!("launch failed: {e}"));
                }
            })
            .expect("spawn bridge-start thread");
    }

    pub fn status(&self) -> Status {
        let mut s = self.inner.status.lock().unwrap().clone();
        s.pending = self.inner.pending.lock().unwrap().len();
        s.restarts = self.inner.restarts.load(Ordering::Relaxed);
        s
    }

    pub fn runtime(&self) -> Option<Runtime> {
        self.inner.runtime.lock().unwrap().clone()
    }

    pub fn remember_open(&self, project_id: &str, spec: ReopenSpec) {
        let mut map = self.inner.reopen.lock().unwrap();
        if spec.active {
            for other in map.values_mut() {
                other.active = false;
            }
        }
        map.insert(project_id.to_string(), spec);
    }

    pub fn forget_open(&self, project_id: &str) {
        self.inner.reopen.lock().unwrap().remove(project_id);
    }

    pub fn set_active(&self, project_id: &str) {
        let mut map = self.inner.reopen.lock().unwrap();
        for (id, spec) in map.iter_mut() {
            spec.active = id == project_id;
        }
    }

    pub fn open_projects(&self) -> Vec<String> {
        self.inner.reopen.lock().unwrap().keys().cloned().collect()
    }

    /// Kill the child and let the exit path restart it immediately.
    pub fn restart(&self) {
        self.inner.failures.store(0, Ordering::Relaxed);
        self.set_status(State::Connecting, "restart requested", None);
        self.kill_child();
    }

    pub fn shutdown(&self) {
        self.inner.shutdown.store(true, Ordering::SeqCst);
        self.kill_child();
    }

    fn kill_child(&self) {
        // Closing stdin makes the bridge's readline return EOF and exit 0.
        self.inner.live.lock().unwrap().take();
        if let Some(mut child) = self.inner.child_handle.lock().unwrap().take() {
            let deadline = std::time::Instant::now() + Duration::from_secs(2);
            loop {
                match child.try_wait() {
                    Ok(Some(_)) => break,
                    Ok(None) if std::time::Instant::now() < deadline => {
                        thread::sleep(Duration::from_millis(50))
                    }
                    _ => {
                        let _ = child.kill();
                        let _ = child.wait();
                        break;
                    }
                }
            }
        }
    }

    fn set_status(&self, state: State, detail: &str, pid: Option<u32>) {
        let payload = {
            let mut s = self.inner.status.lock().unwrap();
            s.state = state;
            s.detail = detail.to_string();
            s.at = crate::registry::now_iso();
            if pid.is_some() || s.state != State::Current {
                s.pid = pid;
            }
            s.pending = self.inner.pending.lock().unwrap().len();
            s.restarts = self.inner.restarts.load(Ordering::Relaxed);
            s.clone()
        };
        let _ = self.inner.app.emit(EVENT_STATUS, payload);
    }

    /// Public hook for other subsystems (the watcher) to flag staleness.
    pub fn report_stale(&self, detail: &str) {
        let _ = self.inner.app.emit(
            EVENT_STATUS,
            json!({ "state": "stale", "detail": detail, "at": crate::registry::now_iso() }),
        );
    }

    fn spawn_child(&self) -> Result<(), String> {
        if self.inner.shutdown.load(Ordering::SeqCst) {
            return Err("shutting down".into());
        }
        let interpreter = runtime::discover_interpreter()?;
        let rt = interpreter.runtime.clone();
        {
            let mut s = self.inner.status.lock().unwrap();
            s.python = Some(rt.python.to_string_lossy().to_string());
            s.python_source = Some(rt.source.clone());
            s.python_skipped = rt.skipped.clone();
            s.repo = rt.repo.as_ref().map(|p| p.to_string_lossy().to_string());
        }
        *self.inner.runtime.lock().unwrap() = Some(rt.clone());
        self.set_status(State::Connecting, "launching bridge", None);

        let mut cmd = runtime::command_for(&interpreter);
        cmd.stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        let mut child = cmd
            .spawn()
            .map_err(|e| format!("cannot start {}: {e}", rt.python.display()))?;
        let pid = child.id();
        let stdin = child.stdin.take().ok_or("child stdin unavailable")?;
        let stdout = child.stdout.take().ok_or("child stdout unavailable")?;
        let stderr = child.stderr.take().ok_or("child stderr unavailable")?;
        *self.inner.live.lock().unwrap() = Some(Live { stdin, pid });
        *self.inner.child_handle.lock().unwrap() = Some(child);

        let me = self.clone();
        thread::Builder::new()
            .name("bridge-stderr".into())
            .spawn(move || me.drain_stderr(stderr))
            .map_err(|e| e.to_string())?;
        let me = self.clone();
        thread::Builder::new()
            .name("bridge-reader".into())
            .spawn(move || me.read_loop(stdout, pid))
            .map_err(|e| e.to_string())?;

        self.set_status(State::Connecting, "handshake", Some(pid));
        let hello = self.request("hello", json!({}))?;
        if let Some(err) = hello.get("error").filter(|e| !e.is_null()) {
            return Err(format!("hello failed: {err}"));
        }
        {
            let mut s = self.inner.status.lock().unwrap();
            s.engine = hello.get("payload").and_then(|p| p.get("engine")).cloned();
        }
        self.inner.failures.store(0, Ordering::Relaxed);
        let reopened = self.reopen_projects();
        let detail = if reopened == 0 {
            "connected".to_string()
        } else {
            format!("connected; reopened {reopened} project(s)")
        };
        self.set_status(State::Current, &detail, Some(pid));
        Ok(())
    }

    fn reopen_projects(&self) -> usize {
        let specs: Vec<(String, ReopenSpec)> = self
            .inner
            .reopen
            .lock()
            .unwrap()
            .iter()
            .map(|(k, v)| (k.clone(), v.clone()))
            .collect();
        let mut count = 0;
        // Open the active project last so the bridge's "first open becomes
        // active" rule cannot override the explicit `activate`.
        let mut ordered = specs;
        ordered.sort_by_key(|(_, s)| s.active);
        for (project_id, spec) in ordered {
            let mut params = json!({ "root": spec.root, "activate": spec.active });
            if let Some(c) = &spec.cursors {
                params["cursors"] = c.clone();
            }
            if let Some(p) = &spec.plugin_data {
                params["plugin_data"] = Value::String(p.clone());
            }
            match self.request("project.open", params) {
                Ok(env) if env.get("error").map(Value::is_null).unwrap_or(false) => {
                    let new_id = env
                        .get("project_id")
                        .and_then(Value::as_str)
                        .unwrap_or(&project_id)
                        .to_string();
                    if new_id != project_id {
                        let mut map = self.inner.reopen.lock().unwrap();
                        if let Some(s) = map.remove(&project_id) {
                            map.insert(new_id.clone(), s);
                        }
                    }
                    let _ = self.request(
                        "project.subscribe",
                        json!({ "project_id": new_id, "activate": spec.active }),
                    );
                    count += 1;
                }
                Ok(env) => log::warn!("reopen {project_id} refused: {:?}", env.get("error")),
                Err(e) => log::warn!("reopen {project_id} failed: {e}"),
            }
        }
        count
    }

    fn drain_stderr(&self, mut stderr: impl Read) {
        let mut buf = [0u8; 4096];
        let mut splitter = LineSplitter::new();
        loop {
            match stderr.read(&mut buf) {
                Ok(0) | Err(_) => break,
                Ok(n) => {
                    for ev in splitter.push(&buf[..n]) {
                        if let LineEvent::Line(line) = ev {
                            // Never forwarded to the window; local log only.
                            log::warn!(
                                "bridge stderr: {}",
                                line.chars().take(500).collect::<String>()
                            );
                        }
                    }
                }
            }
        }
    }

    fn read_loop(&self, mut stdout: impl Read, pid: u32) {
        let mut buf = vec![0u8; 64 * 1024];
        let mut splitter = LineSplitter::new();
        let detail = loop {
            match stdout.read(&mut buf) {
                Ok(0) => break format!("bridge (pid {pid}) exited"),
                Err(e) => break format!("bridge (pid {pid}) read error: {e}"),
                Ok(n) => {
                    for ev in splitter.push(&buf[..n]) {
                        match ev {
                            LineEvent::Oversized(size) => {
                                log::warn!("dropped oversized bridge line ({size} bytes)")
                            }
                            LineEvent::Line(line) => self.dispatch(&line),
                        }
                    }
                }
            }
        };
        // Only the reader of the *current* child may declare it dead.
        let is_current = self.inner.live.lock().unwrap().as_ref().map(|l| l.pid) == Some(pid);
        let requested = !is_current;
        self.on_child_exit(if requested {
            format!("{detail} (restart requested)")
        } else {
            detail
        });
    }

    fn dispatch(&self, line: &str) {
        match classify_line(line) {
            Inbound::Notification(value) => {
                let _ = self.inner.app.emit(EVENT_NOTIFICATION, value);
            }
            Inbound::Reply {
                request_id,
                envelope,
            } => {
                let waiter = self.inner.pending.lock().unwrap().remove(&request_id);
                match waiter {
                    Some(tx) => {
                        let _ = tx.try_send(envelope);
                    }
                    None => log::debug!("late reply for {request_id} dropped"),
                }
            }
            Inbound::Unrouted(value) => log::warn!("unrouted bridge line: {value}"),
            Inbound::Malformed(err) => {
                if !err.is_empty() {
                    log::warn!("malformed bridge line: {err}");
                }
            }
        }
    }

    fn on_child_exit(&self, detail: String) {
        self.inner.live.lock().unwrap().take();
        if let Some(mut child) = self.inner.child_handle.lock().unwrap().take() {
            let _ = child.wait();
        }
        let pending: Vec<(String, SyncSender<Value>)> =
            self.inner.pending.lock().unwrap().drain().collect();
        for (id, tx) in pending {
            let _ = tx.try_send(error_envelope(&id, "bridge_unavailable", &detail));
        }
        if self.inner.shutdown.load(Ordering::SeqCst) {
            return;
        }
        self.set_status(State::Unavailable, &detail, None);
        self.schedule_restart();
    }

    fn schedule_restart(&self) {
        if self.inner.restart_scheduled.swap(true, Ordering::SeqCst) {
            return;
        }
        let me = self.clone();
        thread::Builder::new()
            .name("bridge-restart".into())
            .spawn(move || {
                let attempt = me.inner.failures.fetch_add(1, Ordering::Relaxed);
                let delay = backoff_delay(attempt);
                log::info!("bridge restart in {delay:?} (attempt {})", attempt + 1);
                thread::sleep(delay);
                me.inner.restart_scheduled.store(false, Ordering::SeqCst);
                if me.inner.shutdown.load(Ordering::SeqCst) {
                    return;
                }
                me.inner.restarts.fetch_add(1, Ordering::Relaxed);
                if let Err(e) = me.spawn_child() {
                    me.kill_child();
                    me.on_child_exit(format!("relaunch failed: {e}"));
                }
            })
            .expect("spawn bridge-restart thread");
    }

    /// Wait (bounded) while a child is being launched but has no pipe yet.
    ///
    /// Without this, the UI's first requests at app start reach `request_typed`
    /// before `spawn_child` has set `live` and fail with "bridge is not running",
    /// a banner that outlives the successful handshake a moment later. Only the
    /// launch window waits: once the supervisor reports `Unavailable` (a crash
    /// and backoff), requests still fail fast.
    fn await_launch(&self) {
        let deadline = std::time::Instant::now() + STARTUP_WAIT;
        loop {
            // One lock at a time: never hold `live` while taking `status`.
            let launched = self.inner.live.lock().unwrap().is_some();
            let connecting = self.inner.status.lock().unwrap().state == State::Connecting;
            if launched
                || !connecting
                || self.inner.shutdown.load(Ordering::SeqCst)
                || std::time::Instant::now() >= deadline
            {
                return;
            }
            thread::sleep(Duration::from_millis(25));
        }
    }

    /// Send one request and wait (bounded) for its envelope. `op` must be on
    /// the allowlist; `params` must be a JSON object.
    pub fn request(&self, op: &str, params: Value) -> Result<Value, String> {
        self.request_typed(op, params).map_err(|e| e.to_string())
    }

    pub fn request_typed(&self, op: &str, params: Value) -> Result<Value, BridgeError> {
        if !params.is_object() {
            return Err(BridgeError::new(
                "invalid_params",
                "params must be a JSON object",
            ));
        }
        self.await_launch();
        let id = format!("r{}", self.inner.seq.fetch_add(1, Ordering::Relaxed));
        let bytes = encode_request(&id, op, &params)
            .map_err(|e| BridgeError::new("invalid_request", e.to_string()))?;
        let (tx, rx) = sync_channel::<Value>(1);
        {
            let mut pending = self.inner.pending.lock().unwrap();
            if pending.len() >= MAX_PENDING {
                return Err(BridgeError::new(
                    "too_many_requests",
                    format!("{MAX_PENDING} requests already in flight"),
                ));
            }
            pending.insert(id.clone(), tx);
        }
        let write_result = {
            let mut live = self.inner.live.lock().unwrap();
            match live.as_mut() {
                Some(l) => l
                    .stdin
                    .write_all(&bytes)
                    .and_then(|_| l.stdin.flush())
                    .map_err(|e| e.to_string()),
                None => Err("bridge is not running".to_string()),
            }
        };
        if let Err(e) = write_result {
            self.inner.pending.lock().unwrap().remove(&id);
            return Err(BridgeError::new("bridge_unavailable", e));
        }
        match rx.recv_timeout(REQUEST_TIMEOUT) {
            Ok(envelope) => Ok(envelope),
            Err(RecvTimeoutError::Timeout) => {
                self.inner.pending.lock().unwrap().remove(&id);
                Err(BridgeError::new(
                    "timeout",
                    format!("{op} did not answer within {}s", REQUEST_TIMEOUT.as_secs()),
                ))
            }
            Err(RecvTimeoutError::Disconnected) => Err(BridgeError::new(
                "bridge_unavailable",
                "bridge stopped while waiting",
            )),
        }
    }
}

/// A host-made envelope in the contract's shape so the renderer has one code path.
pub fn error_envelope(request_id: &str, code: &str, message: &str) -> Value {
    json!({
        "protocol_version": "1.0",
        "schema_version": "host",
        "kind": "error",
        "request_id": request_id,
        "project_id": null,
        "worktree_id": null,
        "generation": null,
        "collected_at": crate::registry::now_iso(),
        "source_fingerprint": null,
        "freshness": "unavailable",
        "omissions": [],
        "warnings": [],
        "cursor": null,
        "scope": null,
        "error": { "code": code, "message": message, "detail": null, "retryable": true },
        "payload": null
    })
}

/// `true` when an envelope carries no error.
pub fn envelope_ok(envelope: &Value) -> bool {
    envelope.get("error").map(Value::is_null).unwrap_or(false)
}
