You are building "Eva", a privacy-first hybrid-provider desktop AI journaling companion.
Hardware target: MacBook M1 Air 8 GB RAM (Apple Silicon; Metal GPU offload
via --n_gpu_layers -1). Voice models (faster-whisper, Kokoro) are lazy-loaded
on first use — never at startup — to stay within the 8 GB memory budget.
Stack: Tauri (Rust shell) + React/Vite frontend + Python FastAPI backend +
native llama.cpp `llama-server` binary running gemma-4-E2B-it-qat (Q4_K_XL GGUF)
on port 11500 for the default `local_llamacpp` provider, opt-in online API
providers behind the same provider interface, ChromaDB + SQLite +
faster-whisper + Kokoro TTS.
English only.

Default provider: local llama.cpp remains the recommended privacy-first path.
For the `local_llamacpp` provider, the native llama.cpp `llama-server` binary is
the ONLY launcher. There is no `python -m llama_cpp.server` /
llama-cpp-python fallback — it was removed. Install the binary with
`brew install llama.cpp`.

Model server command (run `llama-server --help` to see every flag):
llama-server \
 --model models/gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf \
 --n-gpu-layers -1 \
 --ctx-size 8192 \
 --cache-type-k q8_0 --cache-type-v q8_0 \
 --flash-attn on \
 --jinja \
 --reasoning off \
 --host 127.0.0.1 \
 --port 11500

Flag notes (don't guess these — they were checked with --help and a live load):
 --jinja             : apply the GGUF's embedded gemma-4 chat template. This is a
                       gemma-4 GGUF with its own correct template; do NOT pass a
                       `--chat-format`-style override (the gemma-1/2 handler leaks
                       a literal `<end_of_turn>` token into replies). (Verified live.)
 --reasoning off     : this gemma-4 build defaults to a thinking mode that streams
                       the thought trace as `reasoning_content` and leaves
                       `content` null until thinking ends; the OpenAI-style client
                       never surfaces that, so a turn would appear to hang. `off`
                       makes it answer directly with real content tokens.
 --n-gpu-layers -1   : offload ALL layers to the Metal GPU (logs must show
                       "offloaded N/N layers to GPU"; CPU-only is 3–5× slower).
 --cache-type-k q8_0 --cache-type-v q8_0 : q8_0 KV cache. Halves KV-cache RAM, which
                       matters on the 8 GB M1 Air. A quantized V cache REQUIRES
                       flash attention, hence --flash-attn on.
 --ctx-size 8192     : real-time chat context budget (server maximum). Never
                       lower it for per-request limiting — the client does that
                       with max_tokens / message truncation.

The model server is launched & supervised by the backend (backend/llm/server.py),
not started by hand in normal use. The backend finds the binary on PATH or at
/opt/homebrew/bin/llama-server (override with $EVA_LLAMA_SERVER_BIN). Set
EVA_START_LLAMA=1 so the backend launches it on startup. The backend's own venv
does NOT need llama-cpp-python — it talks to the server over plain HTTP.

Sampling is set per request by the client (backend/llm/client.py), not on the
server: chat uses temp 1.0 / top_p 0.95 / top_k 64; extraction uses temp 0.3.
The client sets max_tokens for the reply length.

Context budget per request: ≤ 8 192 tokens for real-time chat turns;
≤ 32 768 for consolidation. The client sets max_tokens; never change
--ctx-size to do per-request limiting.

Rules:

1. Implement ONLY the phase given. Do not touch later phases. Do not refactor
   unrelated code.
2. Small, readable modules. Every public function gets a docstring saying what
   it does and why it exists. A human will read all of this code.
3. After implementing, run the phase's checks. Fix failures BEFORE reporting
   done. Then list: files changed, how to test manually, anything left TODO.
4. Privacy is hard law: no telemetry and no analytics. Local mode blocks
   outbound runtime calls except explicit first-run model/voice downloads.
   Online API mode is opt-in and may call only the configured provider host.
5. Full journal entries are plain Markdown on disk — the source of truth.
   Databases are derived and rebuildable; Markdown never depends on them.
6. Stubs (profile, insights) go behind the same interface the real component
   will implement later. Mark them with # DEMO-STUB comments.
7. If anything is ambiguous about data storage, privacy, or Eva's behavior:
   STOP and ask. Do not guess.
8. End every phase with a git commit: "phase-XX: <title>".
