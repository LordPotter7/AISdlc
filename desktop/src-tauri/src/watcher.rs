//! Per-project filesystem watching with a 200 ms debounce.
//!
//! Events are hints only: a debounced batch turns into one
//! `project.reconcile {hint:"watcher"}` request; the bridge compares
//! fingerprints/cursors and emits the generation notification itself. Watcher
//! loss is reported as `bridge://status {state:"stale"}` and the bridge's own
//! 5 s poll keeps the view correct.

use crate::bridge::Supervisor;
use crate::paths::is_excluded;
use notify::{Event, RecommendedWatcher, RecursiveMode, Watcher};
use serde_json::json;
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{channel, RecvTimeoutError, Sender};
use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant};

pub const DEBOUNCE: Duration = Duration::from_millis(200);

/// Paths derived from the `project.open` context payload.
#[derive(Debug, Clone, Default)]
pub struct WatchTargets {
    pub root: PathBuf,
    pub delivery_dir: Option<PathBuf>,
    pub company_pack: Option<PathBuf>,
}

impl WatchTargets {
    /// Read the relevant locations out of the `context` object of a
    /// `project.open` payload. Missing keys are tolerated.
    pub fn from_context(root: &Path, context: &serde_json::Value) -> Self {
        let delivery_dir = context
            .get("delivery")
            .and_then(|e| e.get("dir"))
            .and_then(|v| v.as_str())
            .map(PathBuf::from);
        let company_pack = context
            .get("company_pack")
            .and_then(|c| c.get("path"))
            .and_then(|v| v.as_str())
            .map(PathBuf::from);
        Self {
            root: root.to_path_buf(),
            delivery_dir,
            company_pack,
        }
    }

    /// Concrete (path, recursive) pairs to register. Only existing paths are
    /// returned; absent ones are simply not watched (the poll covers them).
    pub fn plan(&self) -> Vec<(PathBuf, RecursiveMode)> {
        let mut out: Vec<(PathBuf, RecursiveMode)> = Vec::new();
        let mut push = |p: PathBuf, mode: RecursiveMode| {
            if p.exists() && !out.iter().any(|(q, _)| q == &p) {
                out.push((p, mode));
            }
        };
        push(self.root.join(".ai-sdlc"), RecursiveMode::Recursive);
        if let Some(ev) = &self.delivery_dir {
            push(ev.clone(), RecursiveMode::Recursive);
        }
        push(self.root.join(".claude"), RecursiveMode::NonRecursive);
        let git = self.root.join(".git");
        if git.is_dir() {
            push(git.join("HEAD"), RecursiveMode::NonRecursive);
            push(git.join("refs"), RecursiveMode::Recursive);
        } else if git.is_file() {
            // Linked worktree: `.git` is a pointer file; a switch rewrites it.
            push(git, RecursiveMode::NonRecursive);
        }
        if let Some(pack) = &self.company_pack {
            if let Some(parent) = pack.parent() {
                push(parent.to_path_buf(), RecursiveMode::NonRecursive);
            }
        }
        out
    }
}

struct ProjectWatch {
    _watcher: RecommendedWatcher,
    stop: Arc<AtomicBool>,
    _tx: Sender<Signal>,
    pub watched: Vec<PathBuf>,
}

enum Signal {
    Changed,
    Error(String),
}

#[derive(Default)]
pub struct WatcherManager {
    projects: HashMap<String, ProjectWatch>,
}

impl WatcherManager {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn watched(&self, project_id: &str) -> Vec<String> {
        self.projects
            .get(project_id)
            .map(|w| {
                w.watched
                    .iter()
                    .map(|p| p.to_string_lossy().to_string())
                    .collect()
            })
            .unwrap_or_default()
    }

    /// Start (or replace) the watcher for a project.
    pub fn start(
        &mut self,
        project_id: &str,
        targets: &WatchTargets,
        bridge: Supervisor,
    ) -> Result<Vec<PathBuf>, String> {
        self.stop(project_id);
        let (tx, rx) = channel::<Signal>();
        let stop = Arc::new(AtomicBool::new(false));

        let event_tx = tx.clone();
        let mut watcher =
            notify::recommended_watcher(move |res: notify::Result<Event>| match res {
                Ok(event) => {
                    if event.paths.iter().any(|p| !is_excluded(p)) || event.paths.is_empty() {
                        let _ = event_tx.send(Signal::Changed);
                    }
                }
                Err(e) => {
                    let _ = event_tx.send(Signal::Error(e.to_string()));
                }
            })
            .map_err(|e| format!("cannot create watcher: {e}"))?;

        let mut watched = Vec::new();
        for (path, mode) in targets.plan() {
            match watcher.watch(&path, mode) {
                Ok(()) => watched.push(path),
                Err(e) => log::warn!("watch {} failed: {e}", path.display()),
            }
        }

        let pid = project_id.to_string();
        let stop_flag = stop.clone();
        thread::Builder::new()
            .name(format!("watch-{}", &pid[..pid.len().min(12)]))
            .spawn(move || debounce_loop(pid, rx, stop_flag, bridge))
            .map_err(|e| e.to_string())?;

        self.projects.insert(
            project_id.to_string(),
            ProjectWatch {
                _watcher: watcher,
                stop,
                _tx: tx,
                watched: watched.clone(),
            },
        );
        Ok(watched)
    }

    pub fn stop(&mut self, project_id: &str) {
        if let Some(w) = self.projects.remove(project_id) {
            w.stop.store(true, Ordering::SeqCst);
            // Dropping `_watcher` and `_tx` closes the channel; the loop exits.
        }
    }

    pub fn stop_all(&mut self) {
        let ids: Vec<String> = self.projects.keys().cloned().collect();
        for id in ids {
            self.stop(&id);
        }
    }
}

fn debounce_loop(
    project_id: String,
    rx: std::sync::mpsc::Receiver<Signal>,
    stop: Arc<AtomicBool>,
    bridge: Supervisor,
) {
    let mut dirty_since: Option<Instant> = None;
    loop {
        if stop.load(Ordering::SeqCst) {
            return;
        }
        let wait = match dirty_since {
            Some(t) => DEBOUNCE.saturating_sub(t.elapsed()),
            None => Duration::from_secs(1),
        };
        match rx.recv_timeout(wait) {
            Ok(Signal::Changed) => {
                if dirty_since.is_none() {
                    dirty_since = Some(Instant::now());
                }
            }
            Ok(Signal::Error(detail)) => {
                bridge.report_stale(&format!("watcher error for {project_id}: {detail}"));
            }
            Err(RecvTimeoutError::Timeout) => {
                if let Some(t) = dirty_since {
                    if t.elapsed() >= DEBOUNCE {
                        dirty_since = None;
                        let params = json!({ "project_id": project_id, "hint": "watcher" });
                        if let Err(e) = bridge.request("project.reconcile", params) {
                            log::debug!("reconcile hint for {project_id} failed: {e}");
                        }
                    }
                }
            }
            Err(RecvTimeoutError::Disconnected) => {
                if !stop.load(Ordering::SeqCst) {
                    bridge.report_stale(&format!("watcher for {project_id} stopped"));
                }
                return;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn plan_only_lists_existing_targets_and_handles_worktree_pointer() {
        let root = std::env::temp_dir().join(format!("aig-watch-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(root.join(".ai-sdlc")).unwrap();
        std::fs::write(root.join(".git"), "gitdir: ../elsewhere").unwrap();
        let external = root.join("ext-delivery");
        std::fs::create_dir_all(&external).unwrap();
        let ctx = json!({ "delivery": { "dir": external.to_string_lossy() }, "company_pack": { "path": null } });
        let targets = WatchTargets::from_context(&root, &ctx);
        let plan = targets.plan();
        let paths: Vec<PathBuf> = plan.iter().map(|(p, _)| p.clone()).collect();
        assert!(paths.contains(&root.join(".ai-sdlc")));
        assert!(paths.contains(&external));
        assert!(paths.contains(&root.join(".git")));
        assert!(
            !paths.iter().any(|p| p.ends_with(".claude")),
            ".claude absent so not watched"
        );
        assert!(targets.company_pack.is_none());
        let _ = std::fs::remove_dir_all(&root);
    }
}
