//! Typed IPC commands exposed to the main window. Every path that arrives
//! from the renderer is validated here or in `registry`/`paths` before use.
//!
//! The command names and JSON shapes are documented in `desktop/README.md`
//! ("IPC commands"); keep both in sync.

use crate::bridge::{envelope_ok, ReopenSpec, Status, Supervisor};
use crate::paths::{canonicalize_lenient, check_shape, is_within, validate_directory};
use crate::registry::{discover, Discovered, Entry, Registry};
use crate::watcher::{WatchTargets, WatcherManager};
use serde::Serialize;
use serde_json::{json, Value};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use tauri::{AppHandle, State};
use tauri_plugin_dialog::DialogExt;

#[derive(Debug, Clone, Serialize)]
pub struct CmdError {
    pub code: String,
    pub message: String,
}

impl CmdError {
    fn new(code: &str, message: impl std::fmt::Display) -> Self {
        Self {
            code: code.into(),
            message: message.to_string(),
        }
    }
}

impl From<crate::registry::RegistryError> for CmdError {
    fn from(e: crate::registry::RegistryError) -> Self {
        use crate::registry::RegistryError::*;
        let code = match &e {
            Path(_) => "invalid_path",
            Io(_) => "registry_io",
            Duplicate(_) => "duplicate_root",
            UnknownId(_) => "unknown_registry_id",
        };
        CmdError::new(code, e)
    }
}

impl From<crate::paths::PathError> for CmdError {
    fn from(e: crate::paths::PathError) -> Self {
        CmdError::new("invalid_path", e)
    }
}

impl From<crate::bridge::BridgeError> for CmdError {
    fn from(e: crate::bridge::BridgeError) -> Self {
        CmdError {
            code: e.code,
            message: e.message,
        }
    }
}

type CmdResult<T> = Result<T, CmdError>;

/// A project currently open in the bridge, keyed by bridge `project_id`.
#[derive(Debug, Clone, Serialize)]
pub struct OpenProject {
    pub registry_id: String,
    pub project_id: String,
    pub root: String,
    pub opened_at: String,
}

pub struct AppState {
    pub data_dir: PathBuf,
    pub registry: Mutex<Registry>,
    pub bridge: Supervisor,
    pub watcher: Mutex<WatcherManager>,
    pub open: Mutex<HashMap<String, OpenProject>>,
}

fn lock<'a, T>(m: &'a Mutex<T>) -> std::sync::MutexGuard<'a, T> {
    m.lock().unwrap_or_else(|p| p.into_inner())
}

fn pick_folder(app: &AppHandle, title: &str) -> CmdResult<Option<PathBuf>> {
    let picked = app.dialog().file().set_title(title).blocking_pick_folder();
    match picked {
        None => Ok(None),
        Some(fp) => fp.into_path().map(Some).map_err(|e| {
            CmdError::new(
                "invalid_path",
                format!("dialog returned a non-path location: {e}"),
            )
        }),
    }
}

// ---------------------------------------------------------------- registry --

#[tauri::command]
pub async fn registry_list(state: State<'_, AppState>) -> CmdResult<Vec<Entry>> {
    Ok(lock(&state.registry).entries().to_vec())
}

/// Register a root. With `root` empty/absent a native folder picker is shown;
/// `Ok(None)` means the user cancelled.
#[tauri::command]
pub async fn registry_add(
    app: AppHandle,
    state: State<'_, AppState>,
    root: Option<String>,
) -> CmdResult<Option<Entry>> {
    let chosen = match root.as_deref().map(str::trim).filter(|s| !s.is_empty()) {
        Some(r) => PathBuf::from(r),
        None => match pick_folder(&app, "Add a governed project")? {
            Some(p) => p,
            None => return Ok(None),
        },
    };
    let entry = lock(&state.registry).add(&chosen.to_string_lossy())?;
    Ok(Some(entry))
}

#[tauri::command]
pub async fn registry_remove(state: State<'_, AppState>, id: String) -> CmdResult<Entry> {
    if lock(&state.open).contains_key(&id) {
        return Err(CmdError::new(
            "project_open",
            "close the project before removing it",
        ));
    }
    Ok(lock(&state.registry).remove(&id)?)
}

#[tauri::command]
pub async fn registry_relink(
    app: AppHandle,
    state: State<'_, AppState>,
    id: String,
    new_root: Option<String>,
) -> CmdResult<Option<Entry>> {
    if lock(&state.open).contains_key(&id) {
        return Err(CmdError::new(
            "project_open",
            "close the project before relinking it",
        ));
    }
    let chosen = match new_root.as_deref().map(str::trim).filter(|s| !s.is_empty()) {
        Some(r) => PathBuf::from(r),
        None => match pick_folder(&app, "Locate the moved project")? {
            Some(p) => p,
            None => return Ok(None),
        },
    };
    Ok(Some(
        lock(&state.registry).relink(&id, &chosen.to_string_lossy())?,
    ))
}

/// Scan one parent folder (picked natively when `parent` is empty) one level
/// deep for `.ai-sdlc/` or `.git`. Never recursive, never a whole disk.
#[tauri::command]
pub async fn registry_discover(
    app: AppHandle,
    state: State<'_, AppState>,
    parent: Option<String>,
) -> CmdResult<Option<Vec<Discovered>>> {
    let chosen = match parent.as_deref().map(str::trim).filter(|s| !s.is_empty()) {
        Some(r) => validate_directory(r)?,
        None => match pick_folder(&app, "Choose a folder that contains projects")? {
            Some(p) => validate_directory(&p.to_string_lossy())?,
            None => return Ok(None),
        },
    };
    let reg = lock(&state.registry);
    Ok(Some(discover(&chosen, &reg)?))
}

// ------------------------------------------------------------------ bridge --

#[tauri::command]
pub async fn bridge_status(state: State<'_, AppState>) -> CmdResult<Status> {
    Ok(state.bridge.status())
}

/// Generic pass-through. `op` must be on the contract allowlist; the reply is
/// the full envelope (check `error` before reading `payload`).
#[tauri::command]
pub async fn bridge_request(
    state: State<'_, AppState>,
    op: String,
    params: Option<Value>,
) -> CmdResult<Value> {
    let params = params.unwrap_or_else(|| json!({}));
    if op == "project.open" {
        // Only `open_project` may open roots, and only registered ones.
        return Err(CmdError::new(
            "use_open_project",
            "project.open is only available through open_project",
        ));
    }
    if let Some(pid) = params.get("project_id").and_then(Value::as_str) {
        if !lock(&state.open).contains_key(pid) {
            return Err(CmdError::new(
                "project_not_open",
                format!("project {pid} is not open"),
            ));
        }
    }
    Ok(state.bridge.request_typed(&op, params)?)
}

#[tauri::command]
pub async fn bridge_restart(state: State<'_, AppState>) -> CmdResult<Status> {
    state.bridge.restart();
    Ok(state.bridge.status())
}

// ---------------------------------------------------------------- projects --

#[derive(Debug, Clone, Serialize)]
pub struct OpenResult {
    /// Registry id after the open (equals `project_id`).
    pub registry_id: String,
    /// The id the caller passed in; differs from `registry_id` on first open.
    pub previous_registry_id: String,
    pub project_id: String,
    pub root: String,
    /// The `project.open` envelope: `payload.context`, `payload.generation`, `payload.catch_up`.
    pub envelope: Value,
    /// Paths the watcher registered for this project.
    pub watched: Vec<String>,
}

#[tauri::command]
pub async fn open_project(
    state: State<'_, AppState>,
    registry_id: String,
    activate: Option<bool>,
) -> CmdResult<OpenResult> {
    let entry = lock(&state.registry)
        .get(&registry_id)
        .cloned()
        .ok_or_else(|| {
            CmdError::new(
                "unknown_registry_id",
                format!("no registry entry {registry_id}"),
            )
        })?;
    let root = validate_directory(&entry.root)
        .map_err(|e| CmdError::new("root_missing", format!("{e}; relink the project")))?;
    let activate = activate.unwrap_or(true);

    let mut params = json!({ "root": root.to_string_lossy(), "activate": activate });
    if let Some(c) = &entry.cursors {
        params["cursors"] = c.clone();
    }
    if let Some(p) = &entry.plugin_data {
        params["plugin_data"] = Value::String(p.clone());
    }
    let envelope = state.bridge.request_typed("project.open", params)?;
    if !envelope_ok(&envelope) {
        return Err(CmdError::new(
            envelope["error"]["code"].as_str().unwrap_or("bridge_error"),
            envelope["error"]["message"]
                .as_str()
                .unwrap_or("project.open failed"),
        ));
    }
    let project_id = envelope
        .get("project_id")
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty())
        .ok_or_else(|| CmdError::new("bridge_error", "project.open returned no project_id"))?
        .to_string();

    if let Err(e) = state.bridge.request_typed(
        "project.subscribe",
        json!({ "project_id": project_id, "activate": activate }),
    ) {
        // The bridge has the project open but the host would have no record of
        // it; close it again so the two sides stay consistent.
        let _ = state
            .bridge
            .request_typed("project.close", json!({ "project_id": project_id }));
        return Err(CmdError::from(e));
    }

    let new_registry_id = lock(&state.registry).mark_opened(&registry_id, &project_id)?;
    state.bridge.remember_open(
        &project_id,
        ReopenSpec {
            root: root.to_string_lossy().to_string(),
            cursors: entry.cursors.clone(),
            plugin_data: entry.plugin_data.clone(),
            active: activate,
        },
    );
    lock(&state.open).insert(
        project_id.clone(),
        OpenProject {
            registry_id: new_registry_id.clone(),
            project_id: project_id.clone(),
            root: root.to_string_lossy().to_string(),
            opened_at: crate::registry::now_iso(),
        },
    );

    let context = envelope
        .get("payload")
        .and_then(|p| p.get("context"))
        .cloned()
        .unwrap_or(Value::Null);
    let targets = WatchTargets::from_context(&root, &context);
    let watched = match lock(&state.watcher).start(&project_id, &targets, state.bridge.clone()) {
        Ok(paths) => paths
            .into_iter()
            .map(|p| p.to_string_lossy().to_string())
            .collect(),
        Err(e) => {
            state
                .bridge
                .report_stale(&format!("watcher unavailable for {project_id}: {e}"));
            Vec::new()
        }
    };

    Ok(OpenResult {
        registry_id: new_registry_id,
        previous_registry_id: registry_id,
        project_id,
        root: root.to_string_lossy().to_string(),
        envelope,
        watched,
    })
}

#[derive(Debug, Clone, Serialize)]
pub struct CloseResult {
    pub project_id: String,
    pub cursors_saved: bool,
}

#[tauri::command]
pub async fn close_project(
    state: State<'_, AppState>,
    project_id: String,
) -> CmdResult<CloseResult> {
    let open = lock(&state.open).get(&project_id).cloned().ok_or_else(|| {
        CmdError::new(
            "project_not_open",
            format!("project {project_id} is not open"),
        )
    })?;
    lock(&state.watcher).stop(&project_id);
    let _ = state
        .bridge
        .request_typed("project.unsubscribe", json!({ "project_id": project_id }));
    let cursors = match state
        .bridge
        .request_typed("project.close", json!({ "project_id": project_id }))
    {
        Ok(env) if envelope_ok(&env) => env.get("payload").and_then(|p| p.get("cursors")).cloned(),
        _ => None,
    };
    let saved = cursors.is_some();
    if saved {
        lock(&state.registry).store_cursors(&open.registry_id, cursors)?;
    }
    state.bridge.forget_open(&project_id);
    lock(&state.open).remove(&project_id);
    Ok(CloseResult {
        project_id,
        cursors_saved: saved,
    })
}

#[tauri::command]
pub async fn set_active(state: State<'_, AppState>, project_id: String) -> CmdResult<Value> {
    if !lock(&state.open).contains_key(&project_id) {
        return Err(CmdError::new(
            "project_not_open",
            format!("project {project_id} is not open"),
        ));
    }
    let env = state.bridge.request_typed(
        "project.subscribe",
        json!({ "project_id": project_id, "activate": true }),
    )?;
    state.bridge.set_active(&project_id);
    Ok(env)
}

// ------------------------------------------------------------------ export --

/// Native save dialog for an export. The chosen path must not be inside any
/// registered root's runner-owned `.ai-sdlc/`.
#[tauri::command]
pub async fn save_export_dialog(
    app: AppHandle,
    state: State<'_, AppState>,
    default_name: Option<String>,
) -> CmdResult<Option<String>> {
    let name = default_name.unwrap_or_else(|| "aisdlc-gate-packet.md".to_string());
    if name.contains(['/', '\\']) || name.contains("..") || name.trim().is_empty() {
        return Err(CmdError::new(
            "invalid_name",
            "default_name must be a bare file name",
        ));
    }
    let picked = app
        .dialog()
        .file()
        .set_title("Export")
        .set_file_name(&name)
        .blocking_save_file();
    let Some(fp) = picked else { return Ok(None) };
    let path = fp.into_path().map_err(|e| {
        CmdError::new(
            "invalid_path",
            format!("dialog returned a non-path location: {e}"),
        )
    })?;
    let shaped = check_shape(&path.to_string_lossy())?;
    let canonical = canonicalize_lenient(&shaped);

    let mut forbidden: Vec<PathBuf> = Vec::new();
    {
        let reg = lock(&state.registry);
        for e in reg.entries() {
            forbidden.push(Path::new(&e.root).join(".ai-sdlc"));
            if let Some(pd) = &e.plugin_data {
                forbidden.push(PathBuf::from(pd));
            }
        }
    }
    if let Some(rt) = state.bridge.runtime() {
        if let Some(pd) = rt.plugin_data {
            forbidden.push(pd);
        }
    }
    if let Some(pd) = crate::runtime::plugin_data_dir() {
        forbidden.push(pd);
    }
    for f in forbidden {
        let f = canonicalize_lenient(&f);
        if is_within(&canonical, &f) {
            return Err(CmdError::new(
                "forbidden_destination",
                format!("exports may not be written under {}", f.display()),
            ));
        }
    }
    Ok(Some(canonical.to_string_lossy().to_string()))
}

// --------------------------------------------------------------------- info --

#[tauri::command]
pub async fn app_info(app: AppHandle, state: State<'_, AppState>) -> CmdResult<Value> {
    let status = state.bridge.status();
    let rt = state.bridge.runtime();
    let open: Vec<OpenProject> = lock(&state.open).values().cloned().collect();
    Ok(json!({
        "app_version": app.package_info().version.to_string(),
        "tauri_version": tauri::VERSION,
        "platform": std::env::consts::OS,
        "arch": std::env::consts::ARCH,
        "data_dir": state.data_dir.to_string_lossy(),
        "registry_path": lock(&state.registry).path().to_string_lossy(),
        "python": status.python,
        "python_source": status.python_source,
        "python_skipped": status.python_skipped,
        "repo": status.repo,
        "plugin_data": rt.and_then(|r| r.plugin_data).map(|p| p.to_string_lossy().to_string()),
        "engine": status.engine,
        "bridge": { "state": status.state, "detail": status.detail, "pid": status.pid, "restarts": status.restarts },
        "open_projects": open,
    }))
}
