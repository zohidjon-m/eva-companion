"""Model server supervisor — launch & watch the native llama.cpp ``llama-server``.

EVA_SYSTEM_DESIGN §4 makes the model server a separate process spawned and
supervised by the backend, so that if it dies the backend can restart it without
taking down the UI.

The model server is the native llama.cpp **``llama-server`` binary**
(``llama-server`` / ``llama-server.exe``). It serves the OpenAI-compatible API on :11500, so
:mod:`llm.client` and the rest of the backend talk to it over plain HTTP (httpx)
and never import a model library themselves. The binary is the single supported
launcher — there is no ``python -m llama_cpp.server`` / ``llama-cpp-python``
fallback (it was removed: the binary is faster, starts quicker, and drops the
dependency on finding a separate Python interpreter with the package installed).

The launch command (run ``llama-server --help`` to see every flag):

    llama-server \\
      --model models/gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf \\
      --n-gpu-layers -1 \\        # all layers on the Metal GPU (M1 Air)
      --ctx-size 8192 \\          # real-time chat context budget
      --cache-type-k q8_0 \\      # q8_0 KV cache — halves KV RAM on 8 GB
      --cache-type-v q8_0 \\
      --flash-attn on \\          # required for a quantized V cache
      --jinja \\                  # use the GGUF's embedded gemma-4 chat template
      --reasoning off \\          # thinking OFF — stream real content tokens
      --host 127.0.0.1 --port 11500

    ``--jinja`` applies the GGUF's own gemma-4 chat template; we deliberately do
    NOT pass a ``--chat_format``-style override, which would select the gemma-1/2
    handler and leak an ``<end_of_turn>`` marker on this model.

Sampling (temp / top_p / top_k / max_tokens) is **not** set here — it is chosen
per request by :mod:`llm.client`, because chat and extraction need different
values. ``--ctx-size`` is the server maximum; per-request budgeting is the
client's job via ``max_tokens`` and message truncation.

Failure posture (CLAUDE.md rule + §9): if the GGUF is missing, or the
``llama-server`` binary is not installed, we never crash — :func:`model_status`
reports it plus the fix command, and ``/health`` surfaces it so the shell can
guide first-run setup.
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("eva.llm.server")

# ── locations & launch settings ──────────────────────────────────────────────
# server.py lives at <repo>/backend/llm/server.py, so the repo root is two up
# from the backend package directory.
REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_DIR = REPO_ROOT / "models"
MODEL_FILENAME = "gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf"
MODEL_PATH = MODEL_DIR / MODEL_FILENAME

HOST = "127.0.0.1"
PORT = 11500
BASE_URL = f"http://{HOST}:{PORT}"

# NOTE on chat format: this is a gemma-**4** GGUF and ships its own correct
# gemma-4 chat template, which llama_cpp picks up automatically. We deliberately
# do NOT pass `--chat_format gemma` — that selects llama_cpp's gemma-1/2 handler,
# which leaks a literal `<end_of_turn>` token into replies on this model (verified
# against a live load). So "gemma chat format" is honoured via the embedded
# template, not the flag.
N_CTX = 8192  # real-time chat budget; consolidation truncates client-side.

# The first-run download is the ONLY permitted network call (CLAUDE.md rule 4).
DOWNLOAD_CMD = "scripts/download_model.py"

# Where to find the native llama.cpp server binary, in resolution order:
# an explicit override, then PATH, then Homebrew's default location.
LLAMA_SERVER_ENV = "EVA_LLAMA_SERVER_BIN"
_HOMEBREW_LLAMA_SERVER = "/opt/homebrew/bin/llama-server"


def configured_model_path() -> Path:
    """Return the selected local GGUF path, falling back to the repo default."""
    try:
        import settings as app_settings

        raw = str(app_settings.get("local_model_path") or "").strip()
    except Exception:  # noqa: BLE001 - health must not fail on settings issues
        raw = ""
    return Path(raw).expanduser() if raw else MODEL_PATH


def model_present() -> bool:
    """Return True if the Gemma GGUF exists locally and is non-empty.

    The single source of truth for ``/health``'s ``model_present`` flag and for
    deciding whether the server can be launched at all.
    """
    try:
        path = configured_model_path()
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _bundled_binary_candidates() -> list[Path]:
    """Return likely bundled/dev llama-server binary locations for this OS."""
    exe = "llama-server.exe" if sys.platform == "win32" else "llama-server"
    os_name = platform.system().lower()
    return [
        REPO_ROOT / "bin" / "llama.cpp" / os_name / exe,
        REPO_ROOT / "ui" / "src-tauri" / "binaries" / exe,
        REPO_ROOT / exe,
    ]


def resolve_llama_server() -> str | None:
    """Find the native ``llama-server`` binary, or ``None`` if not installed.

    Tries, in order: ``$EVA_LLAMA_SERVER_BIN`` (explicit override), ``llama-server``
    on PATH, then Homebrew's default ``/opt/homebrew/bin/llama-server``. This is the
    only launcher; when the binary is absent the backend degrades gracefully (no
    model server) via :func:`model_status`.
    """
    override = os.environ.get(LLAMA_SERVER_ENV)
    if override and Path(override).is_file() and os.access(override, os.X_OK):
        return override
    for candidate in _bundled_binary_candidates():
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    found = shutil.which("llama-server")
    if found:
        return found
    if sys.platform == "win32":
        found_exe = shutil.which("llama-server.exe")
        if found_exe:
            return found_exe
    if Path(_HOMEBREW_LLAMA_SERVER).is_file() and os.access(_HOMEBREW_LLAMA_SERVER, os.X_OK):
        return _HOMEBREW_LLAMA_SERVER
    return None


def llama_profile() -> str:
    """Return the llama.cpp launch profile for the current host."""
    override = os.environ.get("EVA_LLAMA_PROFILE")
    if override:
        return override
    if sys.platform == "darwin":
        return "mac-metal"
    if sys.platform == "win32":
        return "win-cpu"
    return "cpu"


def binary_command(binary: str, *, profile: str | None = None) -> list[str]:
    """Build the native ``llama-server`` argv.

    Full Metal offload, 8192 context and a q8_0 KV cache. ``--jinja`` makes the
    server apply the GGUF's embedded gemma-4 chat template for
    ``/v1/chat/completions``; without it the binary would use a plain prompt format.
    """
    selected = profile or llama_profile()
    if selected != "mac-metal":
        return [
            binary,
            "--model", str(configured_model_path()),
            "--n-gpu-layers", "0",
            "--ctx-size", str(N_CTX),
            "--jinja",
            "--reasoning", "off",
            "--host", HOST,
            "--port", str(PORT),
        ]
    return [
        binary,
        "--model", str(configured_model_path()),
        "--n-gpu-layers", "-1",        # offload ALL layers to the Metal GPU
        "--ctx-size", str(N_CTX),
        "--cache-type-k", "q8_0",      # q8_0 key cache
        "--cache-type-v", "q8_0",      # q8_0 value cache
        "--flash-attn", "on",          # mandatory once the V cache is quantized
        "--jinja",                     # apply the GGUF's embedded gemma-4 template
        # Thinking OFF (project mandate). This gemma-4 build defaults to a
        # reasoning/thinking mode that streams the thought trace as
        # `delta.reasoning_content` and leaves `delta.content` null until the
        # (possibly long) thinking ends — which the OpenAI-style client never
        # surfaces, so a chat turn would appear to hang. `--reasoning off` makes
        # the model answer directly, streaming real `content` tokens.
        "--reasoning", "off",
        "--host", HOST,
        "--port", str(PORT),
    ]


def launch_command() -> list[str] | None:
    """Return the argv to launch the model server, or ``None`` if impossible.

    Uses the native ``llama-server`` binary. ``None`` means the binary is not
    installed — the caller surfaces that via :func:`model_status` / a graceful
    error (the fix is Eva's local AI setup or a manual llama.cpp install).
    """
    binary = resolve_llama_server()
    if binary is not None:
        return binary_command(binary)
    return None


def model_status() -> dict:
    """Return the model/server readiness state for ``GET /health``.

    Never raises — this is called from a liveness probe. Reports whether the GGUF
    is present, where it is expected, whether a usable interpreter exists, and the
    command to fetch the model if it is missing (so the shell can guide setup).
    """
    present = model_present()
    binary = resolve_llama_server()
    status: dict = {
        "model_present": present,
        "model_path": str(configured_model_path()),
        "endpoint": BASE_URL,
        # Which launcher the backend will use ("binary" or None).
        "launcher": "binary" if binary else None,
    }
    if not present:
        status["hint"] = (
            f"Gemma GGUF not found at {configured_model_path()}. "
            f"Run `{DOWNLOAD_CMD}` to download it (the only permitted network call)."
        )
        return status
    if status["launcher"] is None:
        status["hint"] = (
            "Model file is present but the `llama-server` binary was not found. "
            "Install llama.cpp or run Eva's local AI setup to download the bundled runtime."
        )
    return status


class LlamaServer:
    """Owns the lifecycle of the ``llama-server`` subprocess.

    The backend creates one of these at startup, calls :meth:`start` (which is a
    no-op that logs gracefully if the model is missing — never a crash), and
    :meth:`stop` on shutdown. :meth:`supervise` optionally restarts the process if
    it dies unexpectedly, per §4 ("if llama-server dies, the backend restarts it").
    """

    def __init__(self, *, max_restarts: int = 3) -> None:
        self._proc: subprocess.Popen | None = None
        self._max_restarts = max_restarts
        self._restarts = 0
        self._stopping = False

    # -- lifecycle ------------------------------------------------------------
    def start(self) -> bool:
        """Launch the server subprocess. Returns True if it was started.

        Returns False (without raising) when the model is missing or no suitable
        interpreter exists, so a fresh machine degrades to first-run setup instead
        of crashing the backend.
        """
        if self.is_running():
            return True
        if not model_present():
            log.warning(
                "llama server not started: model missing at %s (run %s)",
                MODEL_PATH,
                DOWNLOAD_CMD,
            )
            return False
        cmd = launch_command()
        if cmd is None:
            log.error(
                "llama server not started: `llama-server` binary not found "
                "(install llama.cpp or run Eva's local AI setup)"
            )
            return False

        log.info("starting model server: %s", " ".join(cmd))
        log.info(
            "watch the lines below for 'offloaded N/N layers to GPU' — that "
            "confirms full Metal offload (CPU-only would be 3-5x slower)"
        )
        self._stopping = False
        # Inherit stdout/stderr so llama.cpp's load log (incl. Metal offload
        # lines) is visible in the backend's console.
        self._proc = subprocess.Popen(cmd)  # noqa: S603 — args are ours, not user input
        return True

    def is_running(self) -> bool:
        """True if the subprocess is alive."""
        return self._proc is not None and self._proc.poll() is None

    def stop(self, timeout: float = 10.0) -> None:
        """Terminate the server subprocess, escalating to kill if needed."""
        self._stopping = True
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        log.info("stopping model server (pid %s)", proc.pid)
        proc.terminate()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            log.warning("model server did not exit in %ss; killing", timeout)
            proc.kill()
            proc.wait()

    # -- readiness & supervision ---------------------------------------------
    async def wait_ready(self, timeout: float = 90.0) -> bool:
        """Poll the OpenAI ``/v1/models`` endpoint until the server answers.

        Model load (mmap + Metal warm-up of a ~2.6 GB GGUF) takes a few seconds to
        tens of seconds on an M1 Air. Returns True once the endpoint responds, or
        False if it never comes up within ``timeout`` (or the process died first).
        Only contacts loopback, so the privacy net-guard permits it.
        """
        import httpx

        deadline = asyncio.get_event_loop().time() + timeout
        url = f"{BASE_URL}/v1/models"
        async with httpx.AsyncClient(timeout=5.0) as client:
            while asyncio.get_event_loop().time() < deadline:
                if not self.is_running():
                    log.error("model server process exited during startup")
                    return False
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        log.info("model server ready at %s", BASE_URL)
                        return True
                except httpx.HTTPError:
                    pass  # not up yet
                await asyncio.sleep(0.5)
        log.error("model server did not become ready within %ss", timeout)
        return False

    async def supervise(self, poll_interval: float = 2.0) -> None:
        """Background task: restart the server if it dies unexpectedly (§4).

        Runs until :meth:`stop` is called or the restart budget is exhausted. A
        clean stop (``self._stopping``) is never treated as a crash.
        """
        while not self._stopping:
            await asyncio.sleep(poll_interval)
            if self._stopping or self.is_running():
                continue
            if self._restarts >= self._max_restarts:
                log.error(
                    "model server crashed and restart budget (%d) is exhausted; "
                    "leaving it down",
                    self._max_restarts,
                )
                return
            self._restarts += 1
            log.warning(
                "model server died; restarting (%d/%d)",
                self._restarts,
                self._max_restarts,
            )
            self.start()
            await self.wait_ready()


def _run_foreground() -> int:
    """Entry point for ``python backend/llm/server.py`` — run the server inline.

    Convenient for the Phase-1 manual test: it replaces this process with the
    llama server so its load log (including the Metal/GPU offload lines) streams
    straight to the terminal. Prints the download command and exits non-zero if
    the model is missing.
    """
    if not model_present():
        print(f"Model not found at {MODEL_PATH}", file=sys.stderr)
        print(f"Download it first:  {DOWNLOAD_CMD}", file=sys.stderr)
        return 1
    cmd = launch_command()
    if cmd is None:
        print(
            "No `llama-server` binary found. Install llama.cpp or run Eva's "
            "local AI setup.",
            file=sys.stderr,
        )
        return 1
    print("exec:", " ".join(cmd), file=sys.stderr)
    os.execvp(cmd[0], cmd)  # noqa: S606 — replace process; never returns on success
    return 0  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(_run_foreground())
