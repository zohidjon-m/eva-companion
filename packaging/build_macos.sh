#!/usr/bin/env bash
# Eva — macOS packaging build (Phase 15).
#
# Produces the bundled desktop app for the demo machine via `tauri build`:
#   frontend (tsc + vite) → Rust shell → .app + .dmg under
#   ui/src-tauri/target/release/bundle/.
#
# Prerequisites (this script checks them and tells you exactly what's missing):
#   • Node + npm           — the frontend toolchain (verified present in dev).
#   • Rust (cargo/rustc)   — the Tauri shell is compiled native; install via
#                            https://rustup.rs  (`curl --proto '=https' --tlsv1.2
#                            -sSf https://sh.rustup.rs | sh`).
#
# Backend sidecar note (honest scope): full PyInstaller bundling of the Python
# backend into the .app is the explicitly DEFERRED "macOS full packaging" item in
# the plan's §3 table. The Phase-0 spike (packaging/spike/) already proved the
# sidecar mechanism works on macOS. For the demo build, the shell launches the
# backend from its venv (dev parity, EVA_SYSTEM_DESIGN §4); embedding the venv/
# PyInstaller binary as a Tauri `externalBin` is the next packaging step and is
# tracked there, not silently assumed done here.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "── 1/3  Checking prerequisites ─────────────────────────────────────────"
if ! command -v npm >/dev/null 2>&1; then
  echo "ERROR: npm not found. Install Node.js (https://nodejs.org) and retry." >&2
  exit 1
fi
echo "  ✓ npm $(npm --version)"

if ! command -v cargo >/dev/null 2>&1; then
  cat >&2 <<'EOF'
ERROR: cargo/rustc not found — the Tauri shell can't be compiled without Rust.

  Install Rust, then re-run this script:
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
    source "$HOME/.cargo/env"

The frontend bundle builds without Rust (npm run build), but a shippable .app/.dmg
requires the native shell. Stopping here so the build isn't silently partial.
EOF
  exit 1
fi
echo "  ✓ cargo $(cargo --version)"

echo
echo "── 2/3  Building the frontend bundle (tsc + vite) ──────────────────────"
( cd ui && [ -d node_modules ] || npm install )
( cd ui && npm run build )
echo "  ✓ dist/ bundle built"

echo
echo "── 3/3  Building the Tauri app (.app + .dmg) ───────────────────────────"
# `tauri build` re-runs beforeBuildCommand (npm run build) then compiles the shell
# and produces the macOS bundle. `targets: all` in tauri.conf.json emits both the
# .app and a .dmg installer.
( cd ui && npm run tauri build )

BUNDLE_DIR="ui/src-tauri/target/release/bundle"
echo
echo "Done. Bundles are under: $BUNDLE_DIR"
if [ -d "$BUNDLE_DIR" ]; then
  find "$BUNDLE_DIR" -maxdepth 2 \( -name '*.app' -o -name '*.dmg' \) -print
fi
echo
echo "Next: verify the bundle on a CLEAN macOS account — see"
echo "packaging/CLEAN_MACHINE_CHECKLIST.md (the demo runs cold, twice, incl. Wi-Fi off)."
