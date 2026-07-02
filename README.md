# Eva

A privacy-first **hybrid-provider** desktop AI journaling companion. Tauri
(Rust shell) + React/Vite frontend + Python FastAPI backend + local
llama.cpp `llama-server` by default, with opt-in online API providers for
users who choose them. Privacy is a hard law: no telemetry, no analytics, and
mode-aware network access. Local mode blocks outbound runtime calls except
explicit first-run model/voice downloads; online API mode permits only the
selected provider host.

> **Status: mixed implementation / V2 realignment.** The repo is past the
> original scaffold and includes later UI, memory, voice, and provider surfaces,
> but the V2 memory foundations are being realigned before L3/L4 work resumes.
> Canonical plan: `docs/IMPLEMENTATION_PLAN_V2.md`. Realignment bridge:
> `docs/V2_CODEBASE_REALIGNMENT_PLAN.md`. Provider decision:
> `docs/decisions/2026-07-01-hybrid-llm-provider.md`.

## Layout

```
backend/            FastAPI app (Python 3.11)
  app.py            provider-aware API routes; installs the net guard at import
  net_guard.py      mode-aware outbound socket guard (privacy hard law)
  tests/            pytest: health shape + net-guard behavior
ui/                 React + Vite frontend
  src/App.tsx       status dot that polls /health
  src-tauri/        Tauri 2 shell (needs Rust to build — see prerequisites)
packaging/spike/    PyInstaller sidecar proof (build_spike.sh)
scripts/            check_net_guard.py (manual privacy smoke test)
dev.sh              starts backend + frontend together
```

## Prerequisites

- **Python 3.11** (backend)
- **Node 18+ / npm** (frontend)
- **Rust toolchain** (`cargo`, `rustc`) — required to build/run the **Tauri
  native window**. Install via <https://rustup.rs>. Without it, `dev.sh` falls
  back to the Vite dev server in a browser (the backend + UI still work; you
  just don't get the native window).
- **PyInstaller** — only needed to re-run the packaging spike.

### System dependencies for the voice features

- **espeak-ng** — recommended for **voice out (Kokoro TTS)**. Kokoro's misaki
  grapheme-to-phoneme step uses it as a fallback for words outside its built-in
  dictionary; without it, unusual words may be mispronounced. On macOS:

  ```sh
  brew install espeak-ng
  ```

- **ffmpeg is NOT required.** Voice in (faster-whisper) decodes the browser's
  recording (webm/opus on Chrome, mp4/aac in the macOS Tauri webview) via
  **PyAV**, which bundles its own ffmpeg libraries. You do not need a system
  ffmpeg install.

## Run (development)

```sh
./dev.sh
```

This creates the backend venv on first run, starts the backend on
`http://127.0.0.1:8000`, and launches the frontend — the Tauri window if Rust
is installed, otherwise Vite at `http://localhost:1420`. The status dot turns
green when the backend answers `/health`.

### Run the halves manually

Backend:

```sh
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --host 127.0.0.1 --port 8000 --reload
curl http://localhost:8000/health      # -> {"status":"ok","model_present":false,...}
```

Frontend:

```sh
cd ui
npm install
npm run dev                            # http://localhost:1420
# or, with Rust installed, the native window:
npm run tauri dev
```

## First-run local model & voice assets (one-time, needs internet)

In local AI mode, Eva runs offline at runtime after setup. The backend forces
the HuggingFace stack offline and the net guard blocks non-allowed outbound
traffic, so local model weights must be downloaded **once** up front with these
out-of-band scripts. Online API mode skips the local LLM download, but local
embedding and voice assets are still needed for features that use them. Each
script writes into the vault (`local_vault/models/…`) or the HuggingFace cache,
so the weights survive offline use:

```sh
cd backend && source .venv/bin/activate     # the scripts use the backend venv

# 1. The LLM (Gemma 4 E2B GGUF) — see scripts/download_model.py
python ../scripts/download_model.py

# Windows source/dev fallback: downloads the local model and llama.cpp runtime
powershell -ExecutionPolicy Bypass -File ..\scripts\setup_windows.ps1

# 2. Embedding model (bge-small) — REQUIRED for Library upload + recall.
#    Without it, uploading a PDF/txt/md fails with "embedding model isn't set up".
python ../scripts/download_embed_model.py

# 3. Voice in (faster-whisper STT weights). Downloads the size(s) you use.
python ../scripts/download_whisper_model.py all        # base.en + small.en

# 4. Voice out (Kokoro TTS weights + af_heart voice + spaCy en_core_web_sm).
#    Without it, the app shows "Could not load Eva's voice (Kokoro)".
python ../scripts/download_kokoro_model.py
```

**Python packages for voice** (in `backend/requirements.txt`, installed by
`pip install -r requirements.txt`): `faster-whisper` (STT, pulls PyAV) and
`kokoro` (TTS, pulls torch). If voice input returns *"Could not read that
recording"*, confirm `faster-whisper` actually installed:
`backend/.venv/bin/python -c "import faster_whisper, av"`.

> Why these are separate scripts: fastembed defaults to caching in the **system
> temp dir**, which macOS purges — so the embedding model is pinned into
> `local_vault/models/fastembed` instead, and re-run script (2) if a corpus
> upload ever starts failing after a reboot.

## Tests & checks

```sh
# backend unit tests (POSIX)
cd backend && source .venv/bin/activate && python -m pytest -q --basetemp ../.pytest-tmp

# backend unit tests (Windows PowerShell)
cd backend; .\.venv\Scripts\python.exe -m pytest -q --basetemp ..\.pytest-tmp

# manual privacy check: loopback allowed, the open web blocked
python ../scripts/check_net_guard.py

# frontend build (requires Node/npm and installed ui/node_modules)
cd ui && npm ci && npm run build

# packaging spike: freeze a FastAPI sidecar and prove it serves HTTP
PYTHON=backend/.venv/bin/python bash packaging/spike/build_spike.sh
```

## Demo day (Phase 15)

Three entrypoints get you from any state to a presentable demo, plus the
beat-by-beat script:

```sh
# 1. Reset the vault to the known demo state (backs up real data first).
#    Seeds ~3 weeks of mood + the knowledge graph + the L3 profile + the demo
#    book; preserves models/ and settings.json. Prints READY / NOT READY.
backend/.venv/bin/python scripts/demo_reset.py --yes

# 2. Prove every failure mode fails soft (model down, over-cap upload/audio,
#    rapid-fire, offline guard). Prints a PASS/FAIL report; CI form is
#    backend/tests/test_failure_drills.py.
backend/.venv/bin/python scripts/demo_drills.py

# 3. Launch in demo mode (optionally reset first), then follow the current V2
#    plan and realignment notes. A refreshed DEMO_SCRIPT.md is deferred to the
#    final hardening/demo phase.
./run_demo.sh --reset
```

- **`docs/IMPLEMENTATION_PLAN_V2.md`** — the canonical V2 build plan.
- **`docs/V2_CODEBASE_REALIGNMENT_PLAN.md`** — the bridge from this repo state
  to the V2 target.
- **`packaging/build_macos.sh`** — builds the `.app`/`.dmg` (needs Rust/`cargo`).
- **`packaging/CLEAN_MACHINE_CHECKLIST.md`** — verify the bundle cold on a fresh
  macOS account, twice (one run Wi-Fi off), one deliberate failure per category.

## The network guard

`backend/net_guard.py` monkeypatches `socket.connect`/`connect_ex` so any
outbound connection to a non-loopback host raises `OutboundBlocked` and is
logged unless the active provider mode explicitly allows it. Local mode permits
loopback plus the single first-run download host named by `EVA_ALLOW_HOST`
(pre-resolved to its IPs at startup). Online API mode permits only the selected
provider host. The guard is installed the moment `backend/app.py` is imported —
it is **not** deferred to a later phase. The provider/network status UI reports
this mode-aware policy.
