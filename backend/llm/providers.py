"""General LLM provider adapters behind Eva's single model interface.

The rest of Eva should not know whether a reply comes from bundled llama.cpp, a
local OpenAI-compatible server, or a hosted API. This module owns that boundary:
each provider adapts its native HTTP shape into the same stream/complete/status
contract that ``llm.client`` exposes to chat and extraction.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from typing import AsyncIterator
from urllib.parse import quote, urlparse

import settings as app_settings
from llm import server
from net_guard import set_runtime_allow_host


LOCAL_LLAMA_CPP = "local_llamacpp"
LOCAL_OPENAI_COMPATIBLE = "local_openai_compatible"
OPENAI_COMPATIBLE_API = "openai_compatible_api"
ANTHROPIC = "anthropic"
GEMINI = "gemini"

PROVIDER_IDS = (
    LOCAL_LLAMA_CPP,
    LOCAL_OPENAI_COMPATIBLE,
    OPENAI_COMPATIBLE_API,
    ANTHROPIC,
    GEMINI,
)

LOCAL_PROVIDERS = {LOCAL_LLAMA_CPP, LOCAL_OPENAI_COMPATIBLE}
ONLINE_PROVIDERS = {OPENAI_COMPATIBLE_API, ANTHROPIC, GEMINI}

DEFAULT_LOCAL_DISCOVERY = (
    ("llama.cpp", "http://127.0.0.1:11500/v1"),
    ("Ollama", "http://127.0.0.1:11434/v1"),
    ("LM Studio", "http://127.0.0.1:1234/v1"),
)

_SESSION_API_KEYS: dict[str, str] = {}


class ProviderError(RuntimeError):
    """Raised when the selected provider cannot satisfy a model request."""

    def __init__(self, message: str, *, code: str = "provider_error") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ProviderCapabilities:
    """Static provider metadata returned to the UI and used for validation."""

    provider_id: str
    display_name: str
    mode: str
    requires_api_key: bool
    supports_streaming: bool
    supports_system_messages: bool
    supports_json_mode: bool
    supports_model_listing: bool
    max_context_tokens: int
    privacy_label: str


@dataclass(frozen=True)
class ProviderStatus:
    """Runtime provider readiness without exposing secrets or raw exceptions."""

    provider_id: str
    configured: bool
    reachable: bool
    message: str
    error: str | None = None


@dataclass(frozen=True)
class ModelInfo:
    """One model entry normalized from a provider-specific models endpoint."""

    id: str
    label: str


@dataclass(frozen=True)
class ChatOptions:
    """Sampling and transport options common to every provider adapter."""

    max_tokens: int
    temperature: float
    top_p: float | None
    top_k: int | None
    stop: list[str] | None
    stream: bool


CAPABILITIES: dict[str, ProviderCapabilities] = {
    LOCAL_LLAMA_CPP: ProviderCapabilities(
        provider_id=LOCAL_LLAMA_CPP,
        display_name="Local Gemma with llama.cpp",
        mode="local",
        requires_api_key=False,
        supports_streaming=True,
        supports_system_messages=False,
        supports_json_mode=False,
        supports_model_listing=True,
        max_context_tokens=8192,
        privacy_label="Runs on this computer after the model is downloaded.",
    ),
    LOCAL_OPENAI_COMPATIBLE: ProviderCapabilities(
        provider_id=LOCAL_OPENAI_COMPATIBLE,
        display_name="Existing local OpenAI-compatible server",
        mode="local",
        requires_api_key=False,
        supports_streaming=True,
        supports_system_messages=True,
        supports_json_mode=False,
        supports_model_listing=True,
        max_context_tokens=8192,
        privacy_label="Uses a loopback endpoint already running on this computer.",
    ),
    OPENAI_COMPATIBLE_API: ProviderCapabilities(
        provider_id=OPENAI_COMPATIBLE_API,
        display_name="OpenAI-compatible API",
        mode="online",
        requires_api_key=True,
        supports_streaming=True,
        supports_system_messages=True,
        supports_json_mode=True,
        supports_model_listing=True,
        max_context_tokens=128000,
        privacy_label="Sends prompts to the configured online API provider.",
    ),
    ANTHROPIC: ProviderCapabilities(
        provider_id=ANTHROPIC,
        display_name="Anthropic",
        mode="online",
        requires_api_key=True,
        supports_streaming=True,
        supports_system_messages=True,
        supports_json_mode=False,
        supports_model_listing=False,
        max_context_tokens=200000,
        privacy_label="Sends prompts to Anthropic using the configured API key.",
    ),
    GEMINI: ProviderCapabilities(
        provider_id=GEMINI,
        display_name="Google Gemini",
        mode="online",
        requires_api_key=True,
        supports_streaming=True,
        supports_system_messages=True,
        supports_json_mode=True,
        max_context_tokens=1000000,
        supports_model_listing=True,
        privacy_label="Sends prompts to Google Gemini using the configured API key.",
    ),
}


def list_provider_metadata() -> list[dict]:
    """Return every provider's public capability metadata."""

    return [asdict(CAPABILITIES[p]) for p in PROVIDER_IDS]


def set_session_api_key(provider_id: str, api_key: str | None) -> None:
    """Keep an API key in process memory for the selected provider.

    The key is deliberately not persisted here. Packaged Tauri builds can restore
    it from Stronghold/OS secure storage and call this endpoint each launch; dev
    builds can also use the ``EVA_API_KEY`` environment variable.
    """

    if provider_id not in PROVIDER_IDS:
        raise ValueError(f"unknown provider {provider_id!r}")
    if api_key and api_key.strip():
        _SESSION_API_KEYS[provider_id] = api_key.strip()
    else:
        _SESSION_API_KEYS.pop(provider_id, None)


def clear_session_api_key(provider_id: str) -> None:
    """Remove the in-memory API key for one provider."""

    _SESSION_API_KEYS.pop(provider_id, None)


def _env_key(provider_id: str) -> str | None:
    provider_env = "EVA_" + provider_id.upper().replace("-", "_") + "_API_KEY"
    return os.environ.get(provider_env) or os.environ.get("EVA_API_KEY")


def api_key_for(provider_id: str) -> str | None:
    """Return the API key for ``provider_id`` without reading settings."""

    return _SESSION_API_KEYS.get(provider_id) or _env_key(provider_id)


def public_config() -> dict:
    """Return AI configuration safe for UI display and health payloads."""

    s = app_settings.load()
    provider_id = str(s["ai_provider_id"])
    return {
        "ai_provider_id": provider_id,
        "ai_mode": s["ai_mode"],
        "api_base_url": s["api_base_url"],
        "api_model": s["api_model"],
        "local_endpoint": s["local_endpoint"],
        "local_model_path": s["local_model_path"],
        "local_runtime": s["local_runtime"],
        "requires_api_key": CAPABILITIES[provider_id].requires_api_key,
        "has_session_secret": bool(api_key_for(provider_id)),
        "configured": is_configured(),
    }


def provider_mode(provider_id: str | None = None) -> str:
    """Return ``local`` or ``online`` for a provider id or the selected provider."""

    selected = provider_id or str(app_settings.get("ai_provider_id"))
    return CAPABILITIES.get(selected, CAPABILITIES[LOCAL_LLAMA_CPP]).mode


def selected_provider_id() -> str:
    """Return the selected provider id, falling back to local llama.cpp."""

    provider_id = str(app_settings.get("ai_provider_id"))
    return provider_id if provider_id in PROVIDER_IDS else LOCAL_LLAMA_CPP


def is_configured() -> bool:
    """Return whether the selected provider has enough config to make requests."""

    provider_id = selected_provider_id()
    s = app_settings.load()
    if provider_id == LOCAL_LLAMA_CPP:
        return server.model_present()
    if provider_id == LOCAL_OPENAI_COMPATIBLE:
        return bool(str(s["local_endpoint"]).strip())
    if provider_id == OPENAI_COMPATIBLE_API:
        return bool(str(s["api_base_url"]).strip() and str(s["api_model"]).strip() and api_key_for(provider_id))
    if provider_id in {ANTHROPIC, GEMINI}:
        return bool(str(s["api_model"]).strip() and api_key_for(provider_id))
    return False


def _normalize_base_url(raw: str, *, default: str | None = None) -> str:
    """Normalize a base URL and reject empty/malformed values."""

    value = (raw or default or "").strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ProviderError("Provider base URL is missing or invalid.", code="bad_provider_config")
    return value


def _model_from_settings(default: str = server.MODEL_FILENAME) -> str:
    """Return the configured model name or a safe local default."""

    configured = str(app_settings.get("api_model") or "").strip()
    return configured or default


def _assert_loopback(url: str) -> None:
    """Reject local provider endpoints that are not loopback URLs."""

    host = (urlparse(url).hostname or "").lower()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ProviderError("Local AI endpoints must use localhost or 127.0.0.1.", code="bad_provider_config")


def _openai_payload(messages: list[dict], options: ChatOptions, model: str) -> dict:
    """Build an OpenAI-compatible chat-completions payload."""

    payload: dict = {
        "model": model,
        "messages": messages,
        "max_tokens": options.max_tokens,
        "temperature": options.temperature,
        "stream": options.stream,
    }
    if options.top_p is not None:
        payload["top_p"] = options.top_p
    if options.top_k is not None:
        payload["top_k"] = options.top_k
    if options.stop:
        payload["stop"] = options.stop
    return payload


class BaseProvider:
    """Abstract provider adapter for chat, completion, test, and model listing."""

    provider_id: str

    def capabilities(self) -> ProviderCapabilities:
        """Return static provider capability metadata."""

        return CAPABILITIES[self.provider_id]

    async def stream_chat(self, messages: list[dict], options: ChatOptions) -> AsyncIterator[str]:
        """Stream text chunks from the provider."""

        raise NotImplementedError

    async def complete_chat(self, messages: list[dict], options: ChatOptions) -> str:
        """Return a single non-streamed completion."""

        raise NotImplementedError

    async def test_connection(self) -> ProviderStatus:
        """Check whether the provider is configured and reachable."""

        raise NotImplementedError

    async def list_models(self) -> list[ModelInfo]:
        """Return available models if the provider supports model listing."""

        return []


class OpenAICompatibleProvider(BaseProvider):
    """Adapter for OpenAI-compatible chat-completions APIs."""

    def __init__(
        self,
        provider_id: str,
        *,
        base_url: str,
        model: str,
        api_key: str | None,
        require_loopback: bool = False,
    ) -> None:
        self.provider_id = provider_id
        self.base_url = _normalize_base_url(base_url)
        if require_loopback:
            _assert_loopback(self.base_url)
        self.model = model.strip() or server.MODEL_FILENAME
        self.api_key = api_key

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _allow_network(self) -> None:
        """Permit the configured online host before an outbound API request."""

        if self.capabilities().mode == "online":
            set_runtime_allow_host(urlparse(self.base_url).hostname)

    async def stream_chat(self, messages: list[dict], options: ChatOptions) -> AsyncIterator[str]:
        """Stream OpenAI-compatible SSE ``delta.content`` chunks."""

        import httpx

        self._allow_network()
        payload = _openai_payload(messages, options, self.model)
        url = f"{self.base_url}/chat/completions"
        async with httpx.AsyncClient(timeout=_timeout()) as client:
            async with client.stream("POST", url, headers=self._headers(), json=payload) as resp:
                try:
                    resp.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    raise ProviderError(f"{self.capabilities().display_name} returned HTTP {resp.status_code}.") from exc
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    choices = obj.get("choices") or []
                    piece = (choices[0].get("delta") or {}).get("content") if choices else None
                    if piece:
                        yield piece

    async def complete_chat(self, messages: list[dict], options: ChatOptions) -> str:
        """Return one OpenAI-compatible chat-completions response."""

        import httpx

        self._allow_network()
        payload = _openai_payload(messages, ChatOptions(**{**asdict(options), "stream": False}), self.model)
        url = f"{self.base_url}/chat/completions"
        async with httpx.AsyncClient(timeout=_timeout()) as client:
            resp = await client.post(url, headers=self._headers(), json=payload)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ProviderError(f"{self.capabilities().display_name} returned HTTP {resp.status_code}.") from exc
        data = resp.json()
        return str(data["choices"][0]["message"]["content"])

    async def test_connection(self) -> ProviderStatus:
        """Probe ``/models`` and report whether the provider answers."""

        if self.capabilities().requires_api_key and not self.api_key:
            return ProviderStatus(self.provider_id, False, False, "API key missing.", "api_key_missing")
        import httpx

        self._allow_network()
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{self.base_url}/models", headers=self._headers())
            if resp.status_code in {200, 404, 405}:
                return ProviderStatus(self.provider_id, True, True, "Provider reachable.")
            return ProviderStatus(self.provider_id, True, False, f"Provider returned HTTP {resp.status_code}.", "provider_unreachable")
        except Exception as exc:  # noqa: BLE001
            return ProviderStatus(self.provider_id, True, False, "Provider could not be reached.", str(exc))

    async def list_models(self) -> list[ModelInfo]:
        """List OpenAI-compatible models, returning the configured model on failure."""

        import httpx

        self._allow_network()
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{self.base_url}/models", headers=self._headers())
            resp.raise_for_status()
            data = resp.json()
            models = data.get("data") or []
            return [ModelInfo(id=str(m.get("id")), label=str(m.get("id"))) for m in models if m.get("id")]
        except Exception:  # noqa: BLE001
            return [ModelInfo(id=self.model, label=self.model)] if self.model else []


class AnthropicProvider(BaseProvider):
    """Adapter for Anthropic's native messages API."""

    provider_id = ANTHROPIC

    def __init__(self, *, api_key: str | None, model: str, base_url: str = "https://api.anthropic.com/v1") -> None:
        self.api_key = api_key
        self.model = model.strip()
        self.base_url = _normalize_base_url(base_url)

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "x-api-key": self.api_key or "",
            "anthropic-version": "2023-06-01",
        }

    def _payload(self, messages: list[dict], options: ChatOptions) -> dict:
        system_parts: list[str] = []
        converted: list[dict] = []
        for msg in messages:
            role = msg.get("role")
            content = str(msg.get("content") or "")
            if role == "system":
                system_parts.append(content)
            else:
                converted.append({"role": "assistant" if role == "assistant" else "user", "content": content})
        payload: dict = {
            "model": self.model,
            "max_tokens": options.max_tokens,
            "temperature": options.temperature,
            "messages": converted,
            "stream": options.stream,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        return payload

    def _allow_network(self) -> None:
        set_runtime_allow_host(urlparse(self.base_url).hostname)

    async def stream_chat(self, messages: list[dict], options: ChatOptions) -> AsyncIterator[str]:
        """Stream Anthropic ``content_block_delta`` text chunks."""

        import httpx

        if not self.api_key:
            raise ProviderError("Anthropic API key missing.", code="api_key_missing")
        self._allow_network()
        async with httpx.AsyncClient(timeout=_timeout()) as client:
            async with client.stream("POST", f"{self.base_url}/messages", headers=self._headers(), json=self._payload(messages, options)) as resp:
                try:
                    resp.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    raise ProviderError(f"Anthropic returned HTTP {resp.status_code}.") from exc
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    try:
                        obj = json.loads(line[len("data:"):].strip())
                    except json.JSONDecodeError:
                        continue
                    if obj.get("type") == "content_block_delta":
                        piece = (obj.get("delta") or {}).get("text")
                        if piece:
                            yield piece

    async def complete_chat(self, messages: list[dict], options: ChatOptions) -> str:
        """Return one Anthropic messages response."""

        import httpx

        if not self.api_key:
            raise ProviderError("Anthropic API key missing.", code="api_key_missing")
        self._allow_network()
        payload = self._payload(messages, ChatOptions(**{**asdict(options), "stream": False}))
        async with httpx.AsyncClient(timeout=_timeout()) as client:
            resp = await client.post(f"{self.base_url}/messages", headers=self._headers(), json=payload)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ProviderError(f"Anthropic returned HTTP {resp.status_code}.") from exc
        parts = resp.json().get("content") or []
        return "".join(str(p.get("text") or "") for p in parts if p.get("type") == "text")

    async def test_connection(self) -> ProviderStatus:
        """Report whether Anthropic is configured; model call validates at use."""

        if not self.api_key:
            return ProviderStatus(self.provider_id, False, False, "API key missing.", "api_key_missing")
        if not self.model:
            return ProviderStatus(self.provider_id, False, False, "Model name missing.", "model_missing")
        return ProviderStatus(self.provider_id, True, True, "Anthropic configuration saved.")


class GeminiProvider(BaseProvider):
    """Adapter for Google Gemini's generateContent API."""

    provider_id = GEMINI

    def __init__(self, *, api_key: str | None, model: str, base_url: str = "https://generativelanguage.googleapis.com/v1beta") -> None:
        self.api_key = api_key
        self.model = model.strip()
        self.base_url = _normalize_base_url(base_url)

    def _allow_network(self) -> None:
        set_runtime_allow_host(urlparse(self.base_url).hostname)

    def _payload(self, messages: list[dict], options: ChatOptions) -> dict:
        contents: list[dict] = []
        for msg in messages:
            role = "model" if msg.get("role") == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": str(msg.get("content") or "")}]})
        generation: dict = {
            "temperature": options.temperature,
            "maxOutputTokens": options.max_tokens,
        }
        if options.top_p is not None:
            generation["topP"] = options.top_p
        if options.top_k is not None:
            generation["topK"] = options.top_k
        return {"contents": contents, "generationConfig": generation}

    def _url(self, method: str) -> str:
        encoded = quote(self.model, safe="")
        return f"{self.base_url}/models/{encoded}:{method}?key={quote(self.api_key or '', safe='')}"

    async def stream_chat(self, messages: list[dict], options: ChatOptions) -> AsyncIterator[str]:
        """Stream Gemini SSE text chunks."""

        import httpx

        if not self.api_key:
            raise ProviderError("Gemini API key missing.", code="api_key_missing")
        self._allow_network()
        async with httpx.AsyncClient(timeout=_timeout()) as client:
            async with client.stream("POST", self._url("streamGenerateContent") + "&alt=sse", json=self._payload(messages, options)) as resp:
                try:
                    resp.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    raise ProviderError(f"Gemini returned HTTP {resp.status_code}.") from exc
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    try:
                        obj = json.loads(line[len("data:"):].strip())
                    except json.JSONDecodeError:
                        continue
                    for piece in _gemini_text_parts(obj):
                        yield piece

    async def complete_chat(self, messages: list[dict], options: ChatOptions) -> str:
        """Return one Gemini generateContent response."""

        import httpx

        if not self.api_key:
            raise ProviderError("Gemini API key missing.", code="api_key_missing")
        self._allow_network()
        async with httpx.AsyncClient(timeout=_timeout()) as client:
            resp = await client.post(self._url("generateContent"), json=self._payload(messages, ChatOptions(**{**asdict(options), "stream": False})))
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ProviderError(f"Gemini returned HTTP {resp.status_code}.") from exc
        return "".join(_gemini_text_parts(resp.json()))

    async def test_connection(self) -> ProviderStatus:
        """Report whether Gemini is configured; model call validates at use."""

        if not self.api_key:
            return ProviderStatus(self.provider_id, False, False, "API key missing.", "api_key_missing")
        if not self.model:
            return ProviderStatus(self.provider_id, False, False, "Model name missing.", "model_missing")
        return ProviderStatus(self.provider_id, True, True, "Gemini configuration saved.")


def _gemini_text_parts(obj: dict) -> list[str]:
    """Extract text parts from a Gemini response object."""

    pieces: list[str] = []
    for cand in obj.get("candidates") or []:
        content = cand.get("content") or {}
        for part in content.get("parts") or []:
            text = part.get("text")
            if text:
                pieces.append(str(text))
    return pieces


def _timeout():
    """Return the standard provider HTTP timeout."""

    import httpx

    return httpx.Timeout(300.0, connect=15.0)


def selected_provider() -> BaseProvider:
    """Instantiate the currently selected provider adapter from settings."""

    s = app_settings.load()
    provider_id = str(s["ai_provider_id"])
    if provider_id == LOCAL_LLAMA_CPP:
        return OpenAICompatibleProvider(
            LOCAL_LLAMA_CPP,
            base_url=f"{server.BASE_URL}/v1",
            model=server.MODEL_FILENAME,
            api_key=None,
            require_loopback=True,
        )
    if provider_id == LOCAL_OPENAI_COMPATIBLE:
        return OpenAICompatibleProvider(
            LOCAL_OPENAI_COMPATIBLE,
            base_url=str(s["local_endpoint"]),
            model=_model_from_settings(),
            api_key=None,
            require_loopback=True,
        )
    if provider_id == OPENAI_COMPATIBLE_API:
        return OpenAICompatibleProvider(
            OPENAI_COMPATIBLE_API,
            base_url=str(s["api_base_url"]),
            model=_model_from_settings("gpt-4.1-mini"),
            api_key=api_key_for(OPENAI_COMPATIBLE_API),
        )
    if provider_id == ANTHROPIC:
        return AnthropicProvider(api_key=api_key_for(ANTHROPIC), model=_model_from_settings("claude-3-5-haiku-latest"))
    if provider_id == GEMINI:
        return GeminiProvider(api_key=api_key_for(GEMINI), model=_model_from_settings("gemini-1.5-flash"))
    raise ProviderError("Unknown AI provider selected.", code="bad_provider_config")


async def selected_provider_status() -> ProviderStatus:
    """Return the selected provider's redacted runtime status."""

    provider_id = selected_provider_id()
    if provider_id == LOCAL_LLAMA_CPP:
        status = server.model_status()
        if not status["model_present"]:
            return ProviderStatus(provider_id, False, False, status.get("hint", "Local model missing."), "model_missing")
        if not status.get("launcher"):
            return ProviderStatus(provider_id, False, False, status.get("hint", "llama-server missing."), "runtime_missing")
    try:
        return await selected_provider().test_connection()
    except ProviderError as exc:
        return ProviderStatus(provider_id, False, False, str(exc), exc.code)


async def discover_local_openai_endpoints() -> list[dict]:
    """Probe common loopback OpenAI-compatible endpoints for existing local AI."""

    import httpx

    found: list[dict] = []
    async with httpx.AsyncClient(timeout=2.0) as client:
        for label, base_url in DEFAULT_LOCAL_DISCOVERY:
            try:
                resp = await client.get(f"{base_url}/models")
                if resp.status_code != 200:
                    continue
                data = resp.json()
                models = [
                    {"id": str(m.get("id")), "label": str(m.get("id"))}
                    for m in (data.get("data") or [])
                    if m.get("id")
                ]
                found.append({"label": label, "base_url": base_url, "models": models})
            except Exception:  # noqa: BLE001
                continue
    return found
