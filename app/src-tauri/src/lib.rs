use std::sync::Mutex;
use tauri::{
    menu::{Menu, MenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    Emitter, LogicalPosition, Manager,
};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_deep_link::DeepLinkExt;
use tauri_plugin_shell::ShellExt;
use tauri_plugin_updater::UpdaterExt;

struct BackendProcess(Mutex<Option<CommandChild>>);
struct BackendPort(Mutex<Option<u16>>);

#[tauri::command]
fn get_backend_port(state: tauri::State<'_, BackendPort>) -> Option<u16> {
    *state.0.lock().unwrap()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|_app, _args, _cwd| {}))
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .plugin(tauri_plugin_http::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_deep_link::init())
        .invoke_handler(tauri::generate_handler![get_backend_port])
        .setup(|app| {
            let _ = app.deep_link().register_all();
            app.manage(BackendPort(Mutex::new(None)));
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

            // Background update check on startup
            let update_handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                if let Ok(Some(update)) = update_handle.updater().check().await {
                    let version = update.version.clone();
                    let _ = update_handle.emit("update-available", &version);
                    if let Ok(()) = update.download_and_install(|_, _| {}, || {}).await {
                        let _ = update_handle.emit("update-ready", &version);
                    }
                }
            });

            // System tray
            let show = MenuItem::with_id(app, "show", "Show Recall", true, None::<&str>)?;
            let check_update = MenuItem::with_id(app, "update", "Check for updates", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show, &check_update, &quit])?;

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
                    "update" => {
                        let app = app.clone();
                        tauri::async_runtime::spawn(async move {
                            match app.updater().check().await {
                                Ok(Some(update)) => {
                                    let version = update.version.clone();
                                    let _ = app.emit("update-available", &version);
                                    if let Ok(()) = update.download_and_install(|_, _| {}, || {}).await {
                                        let _ = app.emit("update-ready", &version);
                                    }
                                }
                                Ok(None) => {
                                    let _ = app.emit("update-not-found", ());
                                }
                                Err(e) => {
                                    eprintln!("Update check error: {e}");
                                }
                            }
                        });
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
            if let tauri::RunEvent::Exit = event {
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
        });
}
