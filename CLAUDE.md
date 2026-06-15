You are building "Eva", a fully offline desktop AI journaling companion.
Hardware target: MacBook M1 Air 8 GB RAM (Apple Silicon; Metal GPU offload
via --n_gpu_layers -1). Voice models (faster-whisper, Kokoro) are lazy-loaded
on first use — never at startup — to stay within the 8 GB memory budget.
Stack: Tauri (Rust shell) + React/Vite frontend + Python FastAPI backend +
llama-cpp-python OpenAI server (python -m llama_cpp.server) running
gemma-4-E2B-it-qat (Q4_K_XL GGUF) on port 11500, all layers on Metal GPU +
ChromaDB + SQLite + faster-whisper + Kokoro TTS.
English only.

Model server command (verified against `python -m llama_cpp.server --help`):
python -m llama_cpp.server \
 --model models/gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf \
 --n_gpu_layers -1 \
 --n_ctx 8192 \
 --type_k 8 --type_v 8 \
 --flash_attn true \
 --host 127.0.0.1 \
 --port 11500

Flag notes (don't guess these — they were checked with --help and a live load):
 gemma chat format   : this is a gemma-4 GGUF; it ships its own correct gemma-4
                       chat template, which llama_cpp uses automatically. Do NOT
                       pass `--chat_format gemma` — that selects the gemma-1/2
                       handler and leaks a literal `<end_of_turn>` token into
                       replies. (Verified live.)
 --n_gpu_layers -1   : offload ALL layers to the Metal GPU (logs must show
                       "offloaded N/N layers to GPU"; CPU-only is 3–5× slower).
 --type_k 8 --type_v 8 : q8_0 KV cache (ggml type 8). Halves KV-cache RAM, which
                       matters on the 8 GB M1 Air. A quantized V cache REQUIRES
                       flash attention, hence --flash_attn true.
 --n_ctx 8192        : real-time chat context budget (server maximum). Never
                       lower it for per-request limiting — the client does that
                       with max_tokens / message truncation.

The model server is launched & supervised by the backend (backend/llm/server.py),
not started by hand in normal use. It needs an interpreter with `llama-cpp-python`
installed; set $EVA_LLAMA_PYTHON if that isn't the backend's own venv.

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
4. Privacy is hard law: no telemetry, no analytics, no outbound network calls
   at runtime. Only the first-run model/voice download is allowed.
5. Full journal entries are plain Markdown on disk — the source of truth.
   Databases are derived and rebuildable; Markdown never depends on them.
6. Stubs (profile, insights) go behind the same interface the real component
   will implement later. Mark them with # DEMO-STUB comments.
7. If anything is ambiguous about data storage, privacy, or Eva's behavior:
   STOP and ask. Do not guess.
8. End every phase with a git commit: "phase-XX: <title>".
