use tauri::{LogicalPosition, Manager};

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .plugin(tauri_plugin_http::init())
        .setup(|app| {
            // Position the orb at bottom-center of the primary monitor
            if let Some(orb) = app.get_webview_window("orb") {
                if let Ok(Some(monitor)) = orb.primary_monitor() {
                    let size = monitor.size();
                    let sf = monitor.scale_factor();
                    let lw = size.width as f64 / sf;
                    let lh = size.height as f64 / sf;
                    // 140 = orb window width/height, 80 = clearance above taskbar
                    let _ = orb.set_position(LogicalPosition::new(
                        lw / 2.0 - 70.0,
                        lh - 140.0 - 80.0,
                    ));
                }
            }
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
