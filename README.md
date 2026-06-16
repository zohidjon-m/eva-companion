# Eva

A fully **offline** desktop AI journaling companion. Tauri (Rust shell) +
React/Vite frontend + Python FastAPI backend + llama.cpp `llama-server`
(Gemma 4 E2B). Privacy is a hard law: no telemetry, no analytics, and no
outbound network calls at runtime — only the first-run model/voice download.

> **Status: Phase 0 (Scaffold).** An empty but running app: Tauri window ↔
> FastAPI backend over localhost, a `/health` endpoint, the outbound network
> guard live, and a verified PyInstaller packaging spike. No model or features
> yet — those arrive in later phases. See `EVA_DEMO_IMPLEMENTATION_PLAN.md`.

## Layout

```
backend/            FastAPI app (Python 3.11)
  app.py            /health endpoint; installs the net guard at import
  net_guard.py      outbound socket kill-switch (privacy hard law)
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

## First-run model & voice assets (one-time, needs internet)

Eva runs **fully offline at runtime** — the backend forces the HuggingFace stack
offline and the net-guard blocks outbound traffic. So every model weight must be
downloaded **once** up front, with these out-of-band scripts (they deliberately
skip the net-guard). Each writes into the vault (`local_vault/models/…`) or the
HuggingFace cache, so the weights survive offline use:

```sh
cd backend && source .venv/bin/activate     # the scripts use the backend venv

# 1. The LLM (Gemma 4 E2B GGUF) — see scripts/download_model_mac.sh
bash ../scripts/download_model_mac.sh

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
# backend unit tests (health shape + network guard)
cd backend && source .venv/bin/activate && python -m pytest -q

# manual privacy check: loopback allowed, the open web blocked
python ../scripts/check_net_guard.py

# packaging spike: freeze a FastAPI sidecar and prove it serves HTTP
PYTHON=backend/.venv/bin/python bash packaging/spike/build_spike.sh
```

## The network guard

`backend/net_guard.py` monkeypatches `socket.connect`/`connect_ex` so any
outbound connection to a non-loopback host raises `OutboundBlocked` and is
logged. The single permitted first-run download host is named via the
`EVA_ALLOW_HOST` environment variable (pre-resolved to its IPs at startup). The
guard is installed the moment `backend/app.py` is imported — it is **not**
deferred to a later phase. The "Offline ✓" badge UI is wired in Phase 10; this
is the truth it will report.
