//! Eva desktop shell (Tauri 2).
//!
//! Phase 0 keeps the Rust side intentionally thin: it opens the window pointing
//! at the frontend and nothing more. In dev, the Python backend is started
//! alongside it by `dev.sh` (see EVA_SYSTEM_DESIGN §4 — "a dev.sh that starts
//! both is fine for now"). The packaged sidecar mechanism is proven separately
//! by the PyInstaller spike in `packaging/spike/`; wiring the bundled backend
//! as a Tauri `externalBin` sidecar that this shell spawns on launch is a
//! Phase 15 task.

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .run(tauri::generate_context!())
        .expect("error while running Eva tauri application");
}
