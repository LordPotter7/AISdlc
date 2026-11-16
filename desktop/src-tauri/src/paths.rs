//! Path validation shared by the registry, the watcher and the export dialog.
//!
//! Every path that originates in the renderer passes through here before it is
//! used. The rules are deliberately strict: absolute, no `..` components, must
//! exist, canonicalised so two spellings of one directory compare equal.

use std::path::{Component, Path, PathBuf};

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PathError {
    Empty,
    NotAbsolute(String),
    ParentTraversal(String),
    NotFound(String),
    NotADirectory(String),
    Canonicalize(String, String),
}

impl std::fmt::Display for PathError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            PathError::Empty => write!(f, "path is empty"),
            PathError::NotAbsolute(p) => write!(f, "path is not absolute: {p}"),
            PathError::ParentTraversal(p) => write!(f, "path contains '..' components: {p}"),
            PathError::NotFound(p) => write!(f, "path does not exist: {p}"),
            PathError::NotADirectory(p) => write!(f, "path is not a directory: {p}"),
            PathError::Canonicalize(p, e) => write!(f, "cannot canonicalise {p}: {e}"),
        }
    }
}

impl std::error::Error for PathError {}

/// Strip the Windows verbatim prefix (`\\?\`) that `canonicalize` adds, so the
/// stored path is the one users and the Python bridge will recognise.
pub fn strip_verbatim(path: PathBuf) -> PathBuf {
    let s = path.to_string_lossy();
    if let Some(rest) = s.strip_prefix(r"\\?\UNC\") {
        return PathBuf::from(format!(r"\\{rest}"));
    }
    if let Some(rest) = s.strip_prefix(r"\\?\") {
        return PathBuf::from(rest);
    }
    path
}

/// Structural checks only (no filesystem access): non-empty, absolute, no `..`.
pub fn check_shape(raw: &str) -> Result<PathBuf, PathError> {
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        return Err(PathError::Empty);
    }
    let path = PathBuf::from(trimmed);
    if !path.is_absolute() {
        return Err(PathError::NotAbsolute(trimmed.to_string()));
    }
    if path.components().any(|c| matches!(c, Component::ParentDir)) {
        return Err(PathError::ParentTraversal(trimmed.to_string()));
    }
    Ok(path)
}

/// Full validation for a project root or any other user-supplied directory:
/// shape checks, existence, directory-ness and canonicalisation.
pub fn validate_directory(raw: &str) -> Result<PathBuf, PathError> {
    let path = check_shape(raw)?;
    if !path.exists() {
        return Err(PathError::NotFound(raw.to_string()));
    }
    if !path.is_dir() {
        return Err(PathError::NotADirectory(raw.to_string()));
    }
    let canonical = path
        .canonicalize()
        .map_err(|e| PathError::Canonicalize(raw.to_string(), e.to_string()))?;
    Ok(strip_verbatim(canonical))
}

/// Canonicalise a path that may not exist yet (an export destination): the
/// nearest existing ancestor is canonicalised and the remainder re-appended.
pub fn canonicalize_lenient(path: &Path) -> PathBuf {
    let mut existing = path.to_path_buf();
    let mut tail: Vec<std::ffi::OsString> = Vec::new();
    while !existing.exists() {
        match (existing.file_name(), existing.parent()) {
            (Some(name), Some(parent)) => {
                tail.push(name.to_os_string());
                existing = parent.to_path_buf();
            }
            _ => return path.to_path_buf(),
        }
    }
    let mut out = existing
        .canonicalize()
        .map(strip_verbatim)
        .unwrap_or(existing);
    for part in tail.iter().rev() {
        out.push(part);
    }
    out
}

/// `child` is `base` or lies underneath it (component-wise, case-insensitive on
/// Windows because NTFS is).
pub fn is_within(child: &Path, base: &Path) -> bool {
    let norm = |p: &Path| -> Vec<String> {
        p.components()
            .map(|c| {
                let s = c.as_os_str().to_string_lossy().to_string();
                if cfg!(windows) {
                    s.to_lowercase()
                } else {
                    s
                }
            })
            .collect()
    };
    let c = norm(child);
    let b = norm(base);
    c.len() >= b.len() && c[..b.len()] == b[..]
}

/// Directories the watcher never descends into, by component name.
pub const EXCLUDED_COMPONENTS: &[&str] =
    &["node_modules", "target", "dist", ".venv", "__pycache__"];

pub fn is_excluded(path: &Path) -> bool {
    path.components().any(|c| {
        let s = c.as_os_str().to_string_lossy();
        EXCLUDED_COMPONENTS
            .iter()
            .any(|x| s.eq_ignore_ascii_case(x))
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_parent_traversal() {
        let raw = if cfg!(windows) {
            r"C:\projects\..\secret"
        } else {
            "/projects/../secret"
        };
        assert!(matches!(
            check_shape(raw),
            Err(PathError::ParentTraversal(_))
        ));
        assert!(matches!(
            validate_directory(raw),
            Err(PathError::ParentTraversal(_))
        ));
    }

    #[test]
    fn rejects_relative_and_empty() {
        assert!(matches!(check_shape(""), Err(PathError::Empty)));
        assert!(matches!(check_shape("   "), Err(PathError::Empty)));
        assert!(matches!(
            check_shape("projects/app"),
            Err(PathError::NotAbsolute(_))
        ));
    }

    #[test]
    fn rejects_missing_root() {
        let tmp = std::env::temp_dir().join(format!("aig-missing-{}", uuid::Uuid::new_v4()));
        let raw = tmp.to_string_lossy().to_string();
        assert!(matches!(
            validate_directory(&raw),
            Err(PathError::NotFound(_))
        ));
    }

    #[test]
    fn rejects_file_as_root() {
        let tmp = std::env::temp_dir().join(format!("aig-file-{}", uuid::Uuid::new_v4()));
        std::fs::write(&tmp, b"x").unwrap();
        let raw = tmp.to_string_lossy().to_string();
        assert!(matches!(
            validate_directory(&raw),
            Err(PathError::NotADirectory(_))
        ));
        let _ = std::fs::remove_file(&tmp);
    }

    #[test]
    fn accepts_existing_directory_and_canonicalises() {
        let tmp = std::env::temp_dir().join(format!("aig-dir-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&tmp).unwrap();
        let ok = validate_directory(&tmp.to_string_lossy()).unwrap();
        assert!(ok.is_absolute());
        assert!(!ok.to_string_lossy().starts_with(r"\\?\"));
        let _ = std::fs::remove_dir_all(&tmp);
    }

    #[test]
    fn within_and_excluded() {
        let base = if cfg!(windows) { r"C:\p\app" } else { "/p/app" };
        let inner = if cfg!(windows) {
            r"C:\p\app\.ai-sdlc\x"
        } else {
            "/p/app/.ai-sdlc/x"
        };
        let other = if cfg!(windows) {
            r"C:\p\application"
        } else {
            "/p/application"
        };
        assert!(is_within(Path::new(inner), Path::new(base)));
        assert!(!is_within(Path::new(other), Path::new(base)));
        assert!(is_excluded(Path::new(if cfg!(windows) {
            r"C:\p\app\node_modules\a"
        } else {
            "/p/app/node_modules/a"
        })));
        assert!(!is_excluded(Path::new(inner)));
    }
}
