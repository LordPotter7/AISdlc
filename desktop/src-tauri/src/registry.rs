//! Approved project registry, persisted as JSON in the app data directory.
//!
//! The registry is the only source of roots the host will ever open, watch or
//! hand to the bridge. Entries carry the cursors returned by `project.close`
//! so a restart catches up exactly once (plan section 6).

use crate::paths::{validate_directory, PathError};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::fs;
use std::path::{Path, PathBuf};

pub const REGISTRY_FILE: &str = "registry.json";
pub const REGISTRY_VERSION: u32 = 1;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Entry {
    /// Bridge `project_id` once the project has been opened; a local UUID before.
    pub id: String,
    /// Canonical absolute root.
    pub root: String,
    pub label: String,
    pub added_at: String,
    pub last_opened: Option<String>,
    /// Cursors from the last `project.close`, re-sent on the next `project.open`.
    #[serde(default)]
    pub cursors: Option<Value>,
    /// Explicit plugin-data override; `None` means the bridge resolves it.
    #[serde(default)]
    pub plugin_data: Option<String>,
    /// Set when the root no longer exists on disk (needs `registry_relink`).
    #[serde(default)]
    pub missing: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct FileShape {
    version: u32,
    entries: Vec<Entry>,
}

#[derive(Debug)]
pub struct Registry {
    path: PathBuf,
    entries: Vec<Entry>,
}

#[derive(Debug)]
pub enum RegistryError {
    Path(PathError),
    Io(String),
    Duplicate(String),
    UnknownId(String),
}

impl std::fmt::Display for RegistryError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            RegistryError::Path(e) => write!(f, "{e}"),
            RegistryError::Io(e) => write!(f, "registry I/O: {e}"),
            RegistryError::Duplicate(r) => write!(f, "root is already registered: {r}"),
            RegistryError::UnknownId(id) => write!(f, "unknown registry id: {id}"),
        }
    }
}

impl From<PathError> for RegistryError {
    fn from(e: PathError) -> Self {
        RegistryError::Path(e)
    }
}

pub fn now_iso() -> String {
    // RFC 3339 UTC without pulling in chrono: seconds precision is enough here.
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let days = secs / 86_400;
    let rem = secs % 86_400;
    let (h, m, s) = (rem / 3600, (rem % 3600) / 60, rem % 60);
    // civil-from-days (Howard Hinnant), valid for the range we care about.
    let z = days as i64 + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let mo = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if mo <= 2 { y + 1 } else { y };
    format!("{y:04}-{mo:02}-{d:02}T{h:02}:{m:02}:{s:02}Z")
}

fn default_label(root: &Path) -> String {
    root.file_name()
        .map(|n| n.to_string_lossy().to_string())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| root.to_string_lossy().to_string())
}

impl Registry {
    /// Load from `<data_dir>/registry.json`, tolerating a missing file. Roots
    /// that vanished are flagged `missing` rather than dropped.
    pub fn load(data_dir: &Path) -> Result<Self, RegistryError> {
        fs::create_dir_all(data_dir).map_err(|e| RegistryError::Io(e.to_string()))?;
        let path = data_dir.join(REGISTRY_FILE);
        let mut entries = Vec::new();
        if path.exists() {
            let text = fs::read_to_string(&path).map_err(|e| RegistryError::Io(e.to_string()))?;
            match serde_json::from_str::<FileShape>(&text) {
                Ok(shape) => entries = shape.entries,
                Err(e) => {
                    log::warn!("registry unreadable, starting empty: {e}");
                    let backup = path.with_extension("json.corrupt");
                    let _ = fs::rename(&path, backup);
                }
            }
        }
        for entry in entries.iter_mut() {
            entry.missing = !Path::new(&entry.root).is_dir();
        }
        Ok(Self { path, entries })
    }

    #[cfg(test)]
    pub fn in_memory(dir: &Path) -> Self {
        Self {
            path: dir.join(REGISTRY_FILE),
            entries: Vec::new(),
        }
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn entries(&self) -> &[Entry] {
        &self.entries
    }

    pub fn get(&self, id: &str) -> Option<&Entry> {
        self.entries.iter().find(|e| e.id == id)
    }

    pub fn get_mut(&mut self, id: &str) -> Option<&mut Entry> {
        self.entries.iter_mut().find(|e| e.id == id)
    }

    pub fn find_by_root(&self, root: &Path) -> Option<&Entry> {
        self.entries.iter().find(|e| {
            crate::paths::is_within(root, Path::new(&e.root))
                && crate::paths::is_within(Path::new(&e.root), root)
        })
    }

    /// True when `path` is inside any registered root.
    pub fn contains_path(&self, path: &Path) -> bool {
        self.entries
            .iter()
            .any(|e| crate::paths::is_within(path, Path::new(&e.root)))
    }

    pub fn save(&self) -> Result<(), RegistryError> {
        let shape = FileShape {
            version: REGISTRY_VERSION,
            entries: self.entries.clone(),
        };
        let text =
            serde_json::to_string_pretty(&shape).map_err(|e| RegistryError::Io(e.to_string()))?;
        let tmp = self.path.with_extension("json.tmp");
        fs::write(&tmp, text).map_err(|e| RegistryError::Io(e.to_string()))?;
        // Windows rename does not replace; remove first (the write is tiny and
        // a crash between the two calls loses nothing the next save cannot redo).
        if self.path.exists() {
            fs::remove_file(&self.path).map_err(|e| RegistryError::Io(e.to_string()))?;
        }
        fs::rename(&tmp, &self.path).map_err(|e| RegistryError::Io(e.to_string()))?;
        Ok(())
    }

    pub fn add(&mut self, raw_root: &str) -> Result<Entry, RegistryError> {
        let root = validate_directory(raw_root)?;
        if let Some(existing) = self.find_by_root(&root) {
            return Err(RegistryError::Duplicate(existing.root.clone()));
        }
        let entry = Entry {
            id: uuid::Uuid::new_v4().to_string(),
            label: default_label(&root),
            root: root.to_string_lossy().to_string(),
            added_at: now_iso(),
            last_opened: None,
            cursors: None,
            plugin_data: None,
            missing: false,
        };
        self.entries.push(entry.clone());
        self.save()?;
        Ok(entry)
    }

    pub fn remove(&mut self, id: &str) -> Result<Entry, RegistryError> {
        let idx = self
            .entries
            .iter()
            .position(|e| e.id == id)
            .ok_or_else(|| RegistryError::UnknownId(id.to_string()))?;
        let removed = self.entries.remove(idx);
        self.save()?;
        Ok(removed)
    }

    /// Point an entry at a new root (moved checkout). Cursors are dropped: they
    /// identify files by path and would be wrong for the new location.
    pub fn relink(&mut self, id: &str, raw_root: &str) -> Result<Entry, RegistryError> {
        let root = validate_directory(raw_root)?;
        if let Some(other) = self.find_by_root(&root) {
            if other.id != id {
                return Err(RegistryError::Duplicate(other.root.clone()));
            }
        }
        let entry = self
            .get_mut(id)
            .ok_or_else(|| RegistryError::UnknownId(id.to_string()))?;
        entry.root = root.to_string_lossy().to_string();
        entry.label = default_label(&root);
        entry.cursors = None;
        entry.missing = false;
        let out = entry.clone();
        self.save()?;
        Ok(out)
    }

    /// Record a successful open: adopt the bridge project id as the entry id.
    /// Returns the (possibly new) id.
    pub fn mark_opened(&mut self, id: &str, project_id: &str) -> Result<String, RegistryError> {
        // If another entry already carries this project id (same canonical
        // root registered twice under different spellings), merge into it.
        if let Some(pos) = self
            .entries
            .iter()
            .position(|e| e.id == project_id && e.id != id)
        {
            self.entries.remove(pos);
        }
        let entry = self
            .get_mut(id)
            .ok_or_else(|| RegistryError::UnknownId(id.to_string()))?;
        entry.id = project_id.to_string();
        entry.last_opened = Some(now_iso());
        entry.missing = false;
        self.save()?;
        Ok(project_id.to_string())
    }

    pub fn store_cursors(&mut self, id: &str, cursors: Option<Value>) -> Result<(), RegistryError> {
        let entry = self
            .get_mut(id)
            .ok_or_else(|| RegistryError::UnknownId(id.to_string()))?;
        entry.cursors = cursors;
        self.save()
    }
}

/// One candidate found by `registry_discover`.
#[derive(Debug, Clone, Serialize)]
pub struct Discovered {
    pub root: String,
    pub label: String,
    pub has_aisdlc: bool,
    pub has_git: bool,
    pub registered: bool,
}

/// Scan one parent directory, one level deep, for folders that look like
/// projects. Never recursive, never a whole drive.
pub fn discover(parent: &Path, registry: &Registry) -> Result<Vec<Discovered>, RegistryError> {
    let mut out = Vec::new();
    let read = fs::read_dir(parent).map_err(|e| RegistryError::Io(e.to_string()))?;
    for item in read.flatten().take(2000) {
        let path = item.path();
        if !path.is_dir() {
            continue;
        }
        let name = default_label(&path);
        if name.starts_with('.') || crate::paths::is_excluded(&path) {
            continue;
        }
        let has_aisdlc = path.join(".ai-sdlc").is_dir();
        let has_git = path.join(".git").exists();
        if !(has_aisdlc || has_git) {
            continue;
        }
        let canonical = path
            .canonicalize()
            .map(crate::paths::strip_verbatim)
            .unwrap_or(path);
        out.push(Discovered {
            registered: registry.find_by_root(&canonical).is_some(),
            root: canonical.to_string_lossy().to_string(),
            label: name,
            has_aisdlc,
            has_git,
        });
    }
    out.sort_by_key(|d| d.label.to_lowercase());
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(name: &str) -> PathBuf {
        let dir =
            std::env::temp_dir().join(format!("aig-registry-{name}-{}", uuid::Uuid::new_v4()));
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn add_validates_and_persists_roundtrip() {
        let data = scratch("data");
        let project = scratch("project");
        let mut reg = Registry::in_memory(&data);
        let entry = reg.add(&project.to_string_lossy()).unwrap();
        assert!(!entry.id.is_empty());
        assert!(
            reg.add(&project.to_string_lossy()).is_err(),
            "duplicate root refused"
        );
        let bad = project.join("..").join("elsewhere");
        assert!(matches!(
            reg.add(&bad.to_string_lossy()),
            Err(RegistryError::Path(PathError::ParentTraversal(_)))
        ));
        assert!(matches!(
            reg.add("relative/path"),
            Err(RegistryError::Path(PathError::NotAbsolute(_)))
        ));

        let reloaded = Registry::load(&data).unwrap();
        assert_eq!(reloaded.entries().len(), 1);
        assert_eq!(reloaded.entries()[0].id, entry.id);
        assert!(!reloaded.entries()[0].missing);

        let _ = fs::remove_dir_all(&project);
        let reloaded = Registry::load(&data).unwrap();
        assert!(
            reloaded.entries()[0].missing,
            "vanished root is flagged, not dropped"
        );
        let _ = fs::remove_dir_all(&data);
    }

    #[test]
    fn mark_opened_adopts_project_id_and_cursors_persist() {
        let data = scratch("data2");
        let project = scratch("project2");
        let mut reg = Registry::in_memory(&data);
        let entry = reg.add(&project.to_string_lossy()).unwrap();
        let new_id = reg.mark_opened(&entry.id, "proj-abc").unwrap();
        assert_eq!(new_id, "proj-abc");
        assert!(reg.get(&entry.id).is_none());
        reg.store_cursors("proj-abc", Some(serde_json::json!({"s1": {"offset": 10}})))
            .unwrap();
        let reloaded = Registry::load(&data).unwrap();
        assert_eq!(
            reloaded.get("proj-abc").unwrap().cursors.as_ref().unwrap()["s1"]["offset"],
            10
        );
        let _ = fs::remove_dir_all(&project);
        let _ = fs::remove_dir_all(&data);
    }

    #[test]
    fn discover_is_one_level_deep() {
        let parent = scratch("parent");
        fs::create_dir_all(parent.join("alpha").join(".git")).unwrap();
        fs::create_dir_all(parent.join("beta").join(".ai-sdlc")).unwrap();
        fs::create_dir_all(parent.join("plain")).unwrap();
        fs::create_dir_all(parent.join("nested").join("deep").join(".git")).unwrap();
        let reg = Registry::in_memory(&parent);
        let found = discover(&parent, &reg).unwrap();
        let labels: Vec<&str> = found.iter().map(|d| d.label.as_str()).collect();
        assert_eq!(labels, vec!["alpha", "beta"]);
        assert!(found[0].has_git && !found[0].has_aisdlc);
        assert!(found[1].has_aisdlc);
        let _ = fs::remove_dir_all(&parent);
    }

    #[test]
    fn now_iso_shape() {
        let s = now_iso();
        assert_eq!(s.len(), 20);
        assert!(s.starts_with("20"));
        assert!(s.ends_with('Z'));
    }
}
