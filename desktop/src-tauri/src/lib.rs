//! AISDLC Studio desktop host (Tauri 2). See `desktop/README.md`.

pub mod bridge;
pub mod commands;
pub mod frame;
pub mod paths;
pub mod registry;
pub mod runtime;
pub mod watcher;

use commands::AppState;
use std::collections::HashMap;
use std::sync::Mutex;
use tauri::Manager;

pub fn run() {
    let _ = env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info"))
        .try_init();

    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            let data_dir = app.path().app_data_dir()?;
            std::fs::create_dir_all(&data_dir)?;
            let registry = registry::Registry::load(&data_dir).map_err(|e| e.to_string())?;
            let supervisor = bridge::Supervisor::new(app.handle().clone());
            supervisor.start();
            app.manage(AppState {
                data_dir,
                registry: Mutex::new(registry),
                bridge: supervisor,
                watcher: Mutex::new(watcher::WatcherManager::new()),
                open: Mutex::new(HashMap::new()),
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::registry_list,
            commands::registry_add,
            commands::registry_remove,
            commands::registry_relink,
            commands::registry_discover,
            commands::bridge_status,
            commands::bridge_request,
            commands::bridge_restart,
            commands::open_project,
            commands::close_project,
            commands::set_active,
            commands::save_export_dialog,
            commands::app_info,
        ])
        .build(tauri::generate_context!())
        .expect("error while building AISDLC Studio")
        .run(|app, event| {
            if let tauri::RunEvent::Exit = event {
                if let Some(state) = app.try_state::<AppState>() {
                    if let Ok(mut w) = state.watcher.lock() {
                        w.stop_all();
                    }
                    state.bridge.shutdown();
                }
            }
        });
}
