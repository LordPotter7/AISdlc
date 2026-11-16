/// Declare the app's own commands so `allow-<command>` permissions are
/// generated and the main-window capability must list each one explicitly.
const COMMANDS: &[&str] = &[
    "registry_list",
    "registry_add",
    "registry_remove",
    "registry_relink",
    "registry_discover",
    "bridge_status",
    "bridge_request",
    "bridge_restart",
    "open_project",
    "close_project",
    "set_active",
    "save_export_dialog",
    "app_info",
];

fn main() {
    tauri_build::try_build(
        tauri_build::Attributes::new()
            .app_manifest(tauri_build::AppManifest::new().commands(COMMANDS)),
    )
    .expect("failed to run tauri-build");
}
