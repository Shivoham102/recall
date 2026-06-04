//! Quiet-context detection + notification-card window control.
//!
//! `is_mic_in_use` is the primary "user is on a call" signal. It always
//! **fails open to `false`** (an unknown state behaves like today and lets the
//! assistant speak) so a detection error can never silence Recall.
//!
//! `show_notif` / `hide_notif` drive the borderless `notif` card window. The card
//! is positioned from Rust (bottom-center of the primary monitor) because the
//! window capability does not grant JS `set-position`, mirroring how the orb is
//! placed in `lib.rs`.

use tauri::{LogicalPosition, Manager};

const NOTIF_W: f64 = 420.0;
const NOTIF_H: f64 = 108.0;
const NOTIF_MARGIN: f64 = 80.0;

#[tauri::command]
pub fn is_mic_in_use() -> bool {
    mic_in_use_impl()
}

#[cfg(target_os = "windows")]
fn mic_in_use_impl() -> bool {
    use winreg::enums::HKEY_CURRENT_USER;
    use winreg::RegKey;

    // Every app that uses the mic gets a subkey under ConsentStore\microphone with
    // a LastUsedTimeStop QWORD (FILETIME). While the mic is actively in use that
    // value is 0; it gets stamped with a real time when the app releases the mic.
    // Packaged apps sit directly under the key (keyed by PackageFamilyName);
    // classic desktop apps sit under the NonPackaged subkey.
    fn any_active(key: &RegKey) -> bool {
        for name in key.enum_keys().flatten() {
            if name.eq_ignore_ascii_case("NonPackaged") {
                continue; // walked separately by the caller
            }
            if let Ok(sub) = key.open_subkey(&name) {
                if matches!(sub.get_value::<u64, _>("LastUsedTimeStop"), Ok(0)) {
                    return true;
                }
            }
        }
        false
    }

    let hkcu = RegKey::predef(HKEY_CURRENT_USER);
    let base = match hkcu.open_subkey(
        r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone",
    ) {
        Ok(k) => k,
        Err(_) => return false,
    };

    if any_active(&base) {
        return true;
    }
    if let Ok(nonpkg) = base.open_subkey("NonPackaged") {
        if any_active(&nonpkg) {
            return true;
        }
    }
    false
}

#[cfg(target_os = "macos")]
fn mic_in_use_impl() -> bool {
    use coreaudio_sys::{
        kAudioDevicePropertyDeviceIsRunningSomewhere, kAudioHardwarePropertyDefaultInputDevice,
        kAudioObjectPropertyScopeGlobal, kAudioObjectSystemObject, AudioDeviceID,
        AudioObjectGetPropertyData, AudioObjectPropertyAddress,
    };
    use std::mem;

    // Element 0 is the master/main element. Using the literal avoids a constant
    // that was renamed (Master → Main) across coreaudio-sys versions.
    const ELEMENT_MAIN: u32 = 0;

    unsafe {
        let mut device: AudioDeviceID = 0;
        let mut size = mem::size_of::<AudioDeviceID>() as u32;
        let dev_addr = AudioObjectPropertyAddress {
            mSelector: kAudioHardwarePropertyDefaultInputDevice,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: ELEMENT_MAIN,
        };
        let st = AudioObjectGetPropertyData(
            kAudioObjectSystemObject,
            &dev_addr,
            0,
            std::ptr::null(),
            &mut size,
            &mut device as *mut _ as *mut _,
        );
        if st != 0 || device == 0 {
            return false;
        }

        let mut running: u32 = 0;
        let mut rsize = mem::size_of::<u32>() as u32;
        let run_addr = AudioObjectPropertyAddress {
            mSelector: kAudioDevicePropertyDeviceIsRunningSomewhere,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: ELEMENT_MAIN,
        };
        let st2 = AudioObjectGetPropertyData(
            device,
            &run_addr,
            0,
            std::ptr::null(),
            &mut rsize,
            &mut running as *mut _ as *mut _,
        );
        st2 == 0 && running != 0
    }
}

#[cfg(target_os = "linux")]
fn mic_in_use_impl() -> bool {
    use std::process::Command;
    // A non-empty source-outputs list means at least one app is capturing. Covers
    // PulseAudio and the PipeWire pulse shim. Missing pactl → treat as not in use.
    match Command::new("pactl")
        .args(["list", "short", "source-outputs"])
        .output()
    {
        Ok(out) => out.status.success() && !out.stdout.is_empty(),
        Err(_) => false,
    }
}

#[cfg(not(any(target_os = "windows", target_os = "macos", target_os = "linux")))]
fn mic_in_use_impl() -> bool {
    false
}

#[tauri::command]
pub fn show_notif(app: tauri::AppHandle) -> Result<(), String> {
    if let Some(win) = app.get_webview_window("notif") {
        if let Ok(Some(monitor)) = win.primary_monitor() {
            let size = monitor.size();
            let sf = monitor.scale_factor();
            let lw = size.width as f64 / sf;
            let lh = size.height as f64 / sf;
            let _ = win.set_position(LogicalPosition::new(
                lw / 2.0 - NOTIF_W / 2.0,
                lh - NOTIF_H - NOTIF_MARGIN,
            ));
        }
        win.show().map_err(|e| e.to_string())?;
    }
    Ok(())
}

#[tauri::command]
pub fn hide_notif(app: tauri::AppHandle) -> Result<(), String> {
    if let Some(win) = app.get_webview_window("notif") {
        win.hide().map_err(|e| e.to_string())?;
    }
    Ok(())
}
