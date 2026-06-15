"""Eva LLM runtime & client (component 3, EVA_SYSTEM_DESIGN §5).

Two small modules with one job each:

* :mod:`llm.server`  — launch and supervise the model server
  (``python -m llama_cpp.server``) that serves Gemma 4 E2B over an
  OpenAI-compatible endpoint on :11500.
* :mod:`llm.client`  — the *only* path the rest of the backend uses to reach the
  model: an async ``stream_chat`` for real-time turns and ``complete_chat`` for
  bounded background jobs (extraction), serialised through one ``asyncio.Lock``
  so a chat turn always takes priority over background work (§8).
"""
