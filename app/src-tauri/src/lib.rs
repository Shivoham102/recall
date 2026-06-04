mod mic;

use std::sync::Mutex;
use tauri::{
    menu::{Menu, MenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    Emitter, LogicalPosition, Manager,
};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_deep_link::DeepLinkExt;
use tauri_plugin_shell::ShellExt;
use tauri_plugin_updater::{Update, UpdaterExt};

struct BackendProcess(Mutex<Option<CommandChild>>);
struct BackendPort(Mutex<Option<u16>>);
struct PendingUpdate(Mutex<Option<Update>>);

#[tauri::command]
fn get_backend_port(state: tauri::State<'_, BackendPort>) -> Option<u16> {
    *state.0.lock().unwrap()
}

#[tauri::command]
async fn check_for_updates(app: tauri::AppHandle) -> Result<(), String> {
    let updater = app.updater().map_err(|e| e.to_string())?;
    match updater.check().await.map_err(|e| e.to_string())? {
        Some(update) => {
            let version = update.version.clone();
            *app.state::<PendingUpdate>().0.lock().unwrap() = Some(update);
            let _ = app.emit("update-available", version);
        }
        None => {
            let _ = app.emit("update-not-found", ());
        }
    }
    Ok(())
}

#[tauri::command]
async fn start_update(app: tauri::AppHandle) -> Result<(), String> {
    let update = app.state::<PendingUpdate>().0.lock().unwrap().take();
    let Some(update) = update else { return Ok(()); };
    let h_chunk = app.clone();
    let h_done  = app.clone();
    let downloaded = std::sync::Arc::new(std::sync::atomic::AtomicU64::new(0));
    let dl = downloaded.clone();
    let bytes = update.download(
        move |chunk_size: usize, total_size: Option<u64>| {
            let so_far = dl.fetch_add(chunk_size as u64, std::sync::atomic::Ordering::Relaxed)
                + chunk_size as u64;
            let _ = h_chunk.emit(
                "update-progress",
                serde_json::json!({ "downloaded": so_far, "total": total_size.unwrap_or(0) }),
            );
        },
        move || { let _ = h_done.emit("update-download-done", ()); },
    ).await.map_err(|e| e.to_string())?;
    tokio::time::sleep(std::time::Duration::from_millis(600)).await;
    update.install(bytes).map_err(|e| e.to_string())?;
    Ok(())
}

#[cfg(debug_assertions)]
#[tauri::command]
async fn simulate_update_available(app: tauri::AppHandle) {
    let _ = app.emit("update-available", "9.9.9");
}

#[cfg(debug_assertions)]
#[tauri::command]
async fn simulate_no_update(app: tauri::AppHandle) {
    let _ = app.emit("update-not-found", ());
}

#[cfg(debug_assertions)]
#[tauri::command]
async fn simulate_update(app: tauri::AppHandle) {
    let _ = app.emit("update-available", "9.9.9");
    for i in 1u64..=10 {
        tokio::time::sleep(std::time::Duration::from_millis(250)).await;
        let _ = app.emit(
            "update-progress",
            serde_json::json!({ "downloaded": i * 1_048_576, "total": 10_485_760u64 }),
        );
    }
    tokio::time::sleep(std::time::Duration::from_millis(200)).await;
    let _ = app.emit("update-download-done", ());
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .plugin(tauri_plugin_http::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_deep_link::init())
        .invoke_handler({
            #[cfg(debug_assertions)]
            { tauri::generate_handler![get_backend_port, check_for_updates, start_update, simulate_update_available, simulate_update, simulate_no_update, mic::is_mic_in_use, mic::show_notif, mic::hide_notif] }
            #[cfg(not(debug_assertions))]
            { tauri::generate_handler![get_backend_port, check_for_updates, start_update, mic::is_mic_in_use, mic::show_notif, mic::hide_notif] }
        })
        .setup(|app| {
            let _ = app.deep_link().register_all();
            app.manage(BackendPort(Mutex::new(None)));
            app.manage(PendingUpdate(Mutex::new(None)));
            let port_state = app.handle().clone();

            if let Ok(sidecar) = app.shell().sidecar("recall-backend") {
                if let Ok((rx, child)) = sidecar.spawn() {
                    app.manage(BackendProcess(Mutex::new(Some(child))));
                    tauri::async_runtime::spawn(async move {
                        let mut rx = rx;
                        let mut buf = String::new();
                        while let Some(event) = rx.recv().await {
                            if let CommandEvent::Stdout(chunk) = event {
                                buf.push_str(&String::from_utf8_lossy(&chunk));
                                while let Some(pos) = buf.find('\n') {
                                    let line = buf[..pos].trim().to_string();
                                    buf = buf[pos + 1..].to_string();
                                    if let Some(port_str) = line.strip_prefix("PORT:") {
                                        if let Ok(port) = port_str.parse::<u16>() {
                                            *port_state
                                                .state::<BackendPort>()
                                                .0
                                                .lock()
                                                .unwrap() = Some(port);
                                        }
                                    }
                                }
                            }
                        }
                    });
                } else {
                    app.manage(BackendProcess(Mutex::new(None)));
                }
            } else {
                app.manage(BackendProcess(Mutex::new(None)));
            }

            // Position the orb at bottom-center of the primary monitor
            if let Some(orb) = app.get_webview_window("orb") {
                if let Ok(Some(monitor)) = orb.primary_monitor() {
                    let size = monitor.size();
                    let sf = monitor.scale_factor();
                    let lw = size.width as f64 / sf;
                    let lh = size.height as f64 / sf;
                    let _ = orb.set_position(LogicalPosition::new(
                        lw / 2.0 - 70.0,
                        lh - 140.0 - 80.0,
                    ));
                }
            }

            // Hide to tray on close instead of quitting
            if let Some(main) = app.get_webview_window("main") {
                let win = main.clone();
                main.on_window_event(move |event| {
                    if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                        api.prevent_close();
                        let _ = win.hide();
                    }
                });
            }

            // Background update check: runs on startup then every 6 hours
            let update_handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                loop {
                    if let Ok(updater) = update_handle.updater() {
                        if let Ok(Some(update)) = updater.check().await {
                            let version = update.version.clone();
                            let pending_state = update_handle.state::<PendingUpdate>();
                            let mut pending = pending_state.0.lock().unwrap();
                            if pending.is_none() {
                                *pending = Some(update);
                                drop(pending);
                                let _ = update_handle.emit("update-available", version);
                            }
                        }
                    }
                    tokio::time::sleep(std::time::Duration::from_secs(6 * 60 * 60)).await;
                }
            });

            // System tray
            let show = MenuItem::with_id(app, "show", "Show Recall", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show, &quit])?;

            let icon = app
                .default_window_icon()
                .cloned()
                .ok_or("no window icon configured")?;

            TrayIconBuilder::new()
                .icon(icon)
                .menu(&menu)
                .tooltip("Recall")
                .show_menu_on_left_click(false)
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        let app = tray.app_handle();
                        if let Some(window) = app.get_webview_window("main") {
                            if window.is_visible().unwrap_or(false) {
                                let _ = window.hide();
                            } else {
                                let _ = window.show();
                                let _ = window.set_focus();
                            }
                        }
                    }
                })
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show" => {
                        if let Some(window) = app.get_webview_window("main") {
                            let _ = window.show();
                            let _ = window.set_focus();
                        }
                    }
                    "quit" => {
                        if let Some(child) =
                            app.state::<BackendProcess>().0.lock().unwrap().take()
                        {
                            let _ = child.kill();
                        }
                        app.exit(0);
                    }
                    _ => {}
                })
                .build(app)?;

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            match event {
                tauri::RunEvent::Exit => {
                    if let Some(child) = app_handle
                        .state::<BackendProcess>()
                        .0
                        .lock()
                        .unwrap()
                        .take()
                    {
                        let _ = child.kill();
                    }
                }
                #[cfg(target_os = "macos")]
                tauri::RunEvent::Reopen { .. } => {
                    if let Some(window) = app_handle.get_webview_window("main") {
                        let _ = window.show();
                        let _ = window.set_focus();
                    }
                }
                _ => {}
            }
        });
}
