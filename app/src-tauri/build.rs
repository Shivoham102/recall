fn main() {
    // Ensure icon asset updates invalidate Cargo's build cache.
    println!("cargo:rerun-if-changed=icons");
    println!("cargo:rerun-if-changed=../icon.svg");
    println!("cargo:rerun-if-changed=tauri.conf.json");

    tauri_build::build()
}
