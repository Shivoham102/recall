use std::sync::Mutex;
use tauri::{
    menu::{Menu, MenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    LogicalPosition, Manager,
};
use tauri_plugin_shell::{process::CommandChild, ShellExt};

struct BackendProcess(Mutex<Option<CommandChild>>);

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .plugin(tauri_plugin_http::init())
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            // Spawn Python backend sidecar (graceful — dev mode runs backend separately)
            let backend_child: Option<CommandChild> = app
                .shell()
                .sidecar("recall-backend")
                .ok()
                .and_then(|cmd| cmd.spawn().ok())
                .map(|(_rx, child)| child);
            app.manage(BackendProcess(Mutex::new(backend_child)));

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
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
