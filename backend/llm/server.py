"""Model server supervisor — launch & watch ``python -m llama_cpp.server``.

EVA_SYSTEM_DESIGN §4 makes the model server a separate process spawned and
supervised by the backend, so that if it dies the backend can restart it without
taking down the UI. We standardise on **llama-cpp-python's OpenAI-compatible
server** (``python -m llama_cpp.server``) rather than the standalone
``llama-server`` binary: it serves the identical OpenAI API on :11500, ships as a
pip dependency (no separate binary to vendor per-OS), and is already the path the
Phase-2 extraction pipeline was validated against.

The exact launch command mirrors ``CLAUDE.md`` (run ``python -m llama_cpp.server
--help`` to see every flag):

    python -m llama_cpp.server \\
      --model models/gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf \\
      --n_gpu_layers -1 \\        # all layers on the Metal GPU (M1 Air)
      --n_ctx 8192 \\             # real-time chat context budget
      --type_k 8 --type_v 8 \\    # q8_0 KV cache — halves KV RAM on 8 GB
      --flash_attn true \\        # required for a quantized V cache
      --host 127.0.0.1 --port 11500

    (No ``--chat_format``: this gemma-4 GGUF ships its own correct chat template;
    forcing the gemma-1/2 ``gemma`` handler leaks an ``<end_of_turn>`` marker.)

Sampling (temp / top_p / top_k / max_tokens) is **not** set here — it is chosen
per request by :mod:`llm.client`, because chat and extraction need different
values. ``--n_ctx`` is the server maximum; per-request budgeting is the client's
job via ``max_tokens`` and message truncation.

Failure posture (CLAUDE.md rule + §9): if the GGUF is missing we never crash —
:func:`model_status` reports ``model_present: False`` plus the download command,
and ``/health`` surfaces it so the shell can guide first-run setup.
"""

from __future__ import annotations

import asyncio
import logging
import os
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
# q8_0 KV-cache quantization (ggml type enum 8). Roughly halves KV-cache RAM,
# which matters on the 8 GB M1 Air. A quantized V cache requires flash attention.
KV_CACHE_TYPE = 8  # llama_cpp.GGML_TYPE_Q8_0

# The first-run download is the ONLY permitted network call (CLAUDE.md rule 4).
DOWNLOAD_CMD = "scripts/download_model_mac.sh"


def model_present() -> bool:
    """Return True if the Gemma GGUF exists locally and is non-empty.

    The single source of truth for ``/health``'s ``model_present`` flag and for
    deciding whether the server can be launched at all.
    """
    try:
        return MODEL_PATH.is_file() and MODEL_PATH.stat().st_size > 0
    except OSError:
        return False


def resolve_python() -> str | None:
    """Find a Python interpreter that can ``import llama_cpp``.

    The backend itself runs in a venv that need not contain ``llama-cpp-python``
    (it is a heavy, Metal-compiled dependency). The model server is a *subprocess*,
    so it only needs *some* interpreter on the machine with the package installed.
    We try, in order: ``$EVA_LLAMA_PYTHON`` (explicit override), the current
    interpreter, then a bare ``python3`` on PATH. Returns the first that has
    ``llama_cpp``, or ``None`` if none do (reported via :func:`model_status`).
    """
    candidates: list[str] = []
    override = os.environ.get("EVA_LLAMA_PYTHON")
    if override:
        candidates.append(override)
    candidates.append(sys.executable)
    found = shutil.which("python3")
    if found:
        candidates.append(found)

    seen: set[str] = set()
    for cand in candidates:
        if not cand or cand in seen:
            continue
        seen.add(cand)
        try:
            subprocess.run(
                [cand, "-c", "import llama_cpp"],
                check=True,
                capture_output=True,
                timeout=30,
            )
            return cand
        except (subprocess.SubprocessError, OSError):
            continue
    return None


def server_command(python: str) -> list[str]:
    """Build the exact ``python -m llama_cpp.server`` argv (mirrors CLAUDE.md)."""
    return [
        python,
        "-m",
        "llama_cpp.server",
        "--model",
        str(MODEL_PATH),
        "--n_gpu_layers",
        "-1",  # -1 = offload ALL layers to the Metal GPU.
        "--n_ctx",
        str(N_CTX),
        "--type_k",
        str(KV_CACHE_TYPE),  # q8_0 key cache
        "--type_v",
        str(KV_CACHE_TYPE),  # q8_0 value cache
        "--flash_attn",
        "true",  # mandatory once the V cache is quantized
        "--host",
        HOST,
        "--port",
        str(PORT),
    ]


def model_status() -> dict:
    """Return the model/server readiness state for ``GET /health``.

    Never raises — this is called from a liveness probe. Reports whether the GGUF
    is present, where it is expected, whether a usable interpreter exists, and the
    command to fetch the model if it is missing (so the shell can guide setup).
    """
    present = model_present()
    status: dict = {
        "model_present": present,
        "model_path": str(MODEL_PATH),
        "endpoint": BASE_URL,
    }
    if not present:
        status["hint"] = (
            f"Gemma GGUF not found at {MODEL_PATH}. "
            f"Run `{DOWNLOAD_CMD}` to download it (the only permitted network call)."
        )
        return status
    if resolve_python() is None:
        status["hint"] = (
            "Model file is present but no Python with `llama-cpp-python` was found. "
            "Install it (`pip install 'llama-cpp-python[server]'`) or set "
            "$EVA_LLAMA_PYTHON to an interpreter that has it."
        )
    return status


class LlamaServer:
    """Owns the lifecycle of the ``llama_cpp.server`` subprocess.

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
        python = resolve_python()
        if python is None:
            log.error(
                "llama server not started: no Python with llama-cpp-python found "
                "(set $EVA_LLAMA_PYTHON or pip install 'llama-cpp-python[server]')"
            )
            return False

        cmd = server_command(python)
        log.info("starting model server: %s", " ".join(cmd))
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
    python = resolve_python()
    if python is None:
        print(
            "No Python with llama-cpp-python found. Set $EVA_LLAMA_PYTHON or "
            "pip install 'llama-cpp-python[server]'.",
            file=sys.stderr,
        )
        return 1
    cmd = server_command(python)
    print("exec:", " ".join(cmd), file=sys.stderr)
    os.execvp(cmd[0], cmd)  # noqa: S606 — replace process; never returns on success
    return 0  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(_run_foreground())
