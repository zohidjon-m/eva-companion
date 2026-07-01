# Hybrid LLM Provider Architecture

**Status:** Accepted  
**Date:** 2026-07-01  
**Owner:** Eva product/engineering  

## Context

Eva began as a privacy-first desktop journaling companion where all AI ran locally through a bundled or installed `llama-server` process and a Gemma GGUF model. That remains the preferred default because journal entries, extracted memories, and profile context are highly sensitive.

The product direction now needs more flexibility:

- Some users will not want to download or run a local model.
- Some Windows machines may be too slow for local inference.
- Some users already run local OpenAI-compatible servers such as Ollama, LM Studio, or llama.cpp.
- Some users may prefer hosted providers because they already have API keys.
- Recruiters should be able to evaluate the app without debugging local model setup first.

The key requirement is that Eva should not hardcode one model backend into chat, extraction, profile, memory, or UI logic. The app should choose a provider through one internal interface.

## Decision

Eva will use one generalized **LLM provider interface** internally.

The first supported provider adapters are:

- `local_llamacpp`: Eva-managed `llama-server` plus Gemma GGUF.
- `local_openai_compatible`: a user-provided local endpoint such as Ollama, LM Studio, llama.cpp, or another loopback OpenAI-compatible server.
- `openai_compatible_api`: hosted OpenAI-compatible APIs such as OpenAI, OpenRouter, Groq, Together, or compatible cloud providers.
- `anthropic`: Anthropic native API.
- `gemini`: Google Gemini native API.

The UI presents this as two simple choices:

- **Run AI on this computer**
- **Use online API**

Provider-specific configuration is shown only after the user makes that choice.

## Interface Shape

Every adapter should expose the same behavior to Eva:

- `stream_chat(messages, options)`
- `complete_chat(messages, options)`
- `test_connection()`
- `list_models()`
- `capabilities()`

Every adapter should also expose capability metadata:

- provider id and display name
- local or online mode
- whether an API key is required
- streaming support
- system-message support
- JSON-mode support
- model-listing support
- approximate context limit
- privacy label

Chat, extraction, profile updates, and memory logic should call this interface instead of knowing whether the model is local Gemma, Anthropic, Gemini, or an OpenAI-compatible endpoint.

## User Flow

On first setup, Eva asks whether the user wants local AI or online API.

For local AI:

- Eva checks loopback-only local endpoints first.
- If a compatible local endpoint is found, the user can select it.
- If no endpoint is found, Eva offers to download the Gemma GGUF model and the llama.cpp runtime.
- Eva checks available disk space before downloading.
- Eva reports download errors clearly, including no internet, insufficient disk, permission denied, interrupted download, checksum or size mismatch, and unsupported platform.

For online API:

- The user chooses OpenAI-compatible, Anthropic, or Gemini.
- The user enters the model name.
- The user enters a base URL when the provider type requires it.
- The user pastes an API key.
- Eva tests the connection before saving the provider as active.

## Privacy And Security

Local mode remains the privacy-first recommendation.

Online API mode is explicitly opt-in. The UI must clearly state that prompts, journal-derived context, extraction text, and memory-related context may be sent to the selected provider.

Eva must never write API keys to:

- settings JSON
- logs
- health responses
- crash output
- Markdown vault files

Non-secret provider settings can be stored in normal Eva settings. API keys must be stored in Tauri Stronghold or OS-backed secure storage before this is considered production-complete. A development-only fallback may read an API key from environment variables.

Network behavior is mode-aware:

- Local mode blocks outbound runtime network calls except explicit model or voice downloads.
- Online mode allows requests only to the selected provider host.

## Local Runtime And Download

The packaged app should not require the end user to install Python, Node, Rust, Homebrew, or llama.cpp.

The app should manage the local runtime and model itself:

- macOS launches `llama-server`.
- Windows launches `llama-server.exe`.
- User-provided local endpoints skip Eva's launcher.
- App-managed downloads are the primary path.
- Windows and macOS setup scripts are fallback tools for development, source checkout, or manual repair.

The existing macOS-only wording and commands should become platform-neutral:

- "this Mac" becomes "this computer"
- "Reveal in Finder" becomes "Reveal in file manager"
- backend reveal commands use the OS-specific file manager

## Consequences

Positive consequences:

- Users can choose privacy-first local AI or hosted model convenience.
- Recruiters can evaluate the product with an API key even if local setup fails.
- Existing local AI users can use Ollama, LM Studio, or another compatible endpoint.
- Future providers can be added without changing chat and memory logic.
- API mode can work even when Gemma is not downloaded.

Tradeoffs:

- Online mode weakens the original offline-only product guarantee, so it must be opt-in and visibly labeled.
- Secure secret persistence becomes required for production.
- Provider-specific streaming formats need normalization.
- Error handling must be consistent across local process failures, HTTP errors, invalid keys, invalid models, and download failures.
- Packaging becomes more important because users should not install developer toolchains.

## Current Implementation Notes

The current implementation adds the generalized provider layer and UI flow, including:

- provider registry and adapters for local llama.cpp, local OpenAI-compatible, hosted OpenAI-compatible, Anthropic, and Gemini
- provider-aware chat routing
- AI setup flow for local AI versus online API
- local endpoint discovery on loopback addresses
- app-managed model download endpoint and status API
- Windows setup script for llama.cpp runtime and model download
- platform-neutral wording in key UI locations
- provider-aware health response and top-bar status

Known remaining work:

- Persistent secure API key storage is not complete yet; current API keys are session-only or development environment variables.
- Full Tauri sidecar packaging for the Python backend and local llama.cpp runtime still needs verification.
- Windows GPU/runtime profiles are not implemented beyond a CPU-oriented llama.cpp fallback.
- Full automated test execution was not verified in the current environment because backend and frontend dependencies were unavailable.
- Download checksum verification should be finalized before production release.

## Follow-Up Checks

Before this decision is considered production-ready:

- Verify packaged macOS install on a clean machine with no developer tools.
- Verify packaged Windows install on a clean machine with no developer tools.
- Verify local model download, cancellation, resume behavior, and disk-space errors.
- Verify API-key persistence through secure storage across app restart.
- Verify API-key removal.
- Verify online mode sends requests only to the configured provider host.
- Verify local mode works with Wi-Fi off after model download.
