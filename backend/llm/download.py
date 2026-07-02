"""App-managed local model download state for first-run setup.

Scripts remain useful for dev/manual setup, but packaged users should not need a
terminal. This module provides a small in-process download manager that checks
disk space, writes a ``.part`` file, and exposes status for the setup UI.
"""

from __future__ import annotations

import shutil
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from llm import server
from net_guard import set_runtime_allow_host

HF_MODEL_URL = (
    "https://huggingface.co/unsloth/gemma-4-E2B-it-qat-GGUF/resolve/main/"
    f"{server.MODEL_FILENAME}?download=true"
)
MIN_FREE_BYTES = 5 * 1024 * 1024 * 1024

_lock = threading.Lock()
_cancel = threading.Event()
_thread: threading.Thread | None = None
_status: dict = {
    "state": "idle",
    "path": str(server.configured_model_path()),
    "bytes_downloaded": 0,
    "total_bytes": None,
    "error": None,
}


def status() -> dict:
    """Return the current download state for the setup UI."""
    with _lock:
        data = dict(_status)
    data["model_present"] = server.model_present()
    data["path"] = str(server.configured_model_path())
    return data


def start(force: bool = False) -> dict:
    """Start downloading the Gemma GGUF if no download is already running."""
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return dict(_status)
        if server.model_present() and not force:
            _status.update({
                "state": "complete",
                "path": str(server.configured_model_path()),
                "error": None,
            })
            return dict(_status)
        path = server.configured_model_path()
        free = shutil.disk_usage(path.parent if path.parent.exists() else path.parent.parent).free
        if free < MIN_FREE_BYTES:
            _status.update({
                "state": "error",
                "path": str(path),
                "error": "Not enough free disk space for the local AI model.",
            })
            return dict(_status)
        _cancel.clear()
        _status.update({
            "state": "starting",
            "path": str(path),
            "bytes_downloaded": 0,
            "total_bytes": None,
            "error": None,
        })
        _thread = threading.Thread(target=_download, daemon=True)
        _thread.start()
        return dict(_status)


def cancel() -> dict:
    """Request cancellation of an in-flight model download."""
    _cancel.set()
    with _lock:
        if _status["state"] in {"starting", "downloading"}:
            _status["state"] = "canceling"
        return dict(_status)


def _set(**patch: object) -> None:
    with _lock:
        _status.update(patch)


def _download() -> None:
    """Worker thread that downloads the model to a ``.part`` file."""
    path = server.configured_model_path()
    part = Path(str(path) + ".part")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        set_runtime_allow_host("huggingface.co")
        request = urllib.request.Request(HF_MODEL_URL, headers={"User-Agent": "Eva/0.1"})
        with urllib.request.urlopen(request, timeout=30) as resp:  # noqa: S310 - explicit first-run download URL
            total = resp.headers.get("Content-Length")
            total_bytes = int(total) if total and total.isdigit() else None
            _set(state="downloading", total_bytes=total_bytes)
            downloaded = 0
            with part.open("wb") as out:
                while True:
                    if _cancel.is_set():
                        _set(state="canceled", error=None)
                        return
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    downloaded += len(chunk)
                    _set(bytes_downloaded=downloaded)
                    time.sleep(0)
        if part.stat().st_size <= 0:
            raise RuntimeError("download wrote an empty model file")
        part.replace(path)
        _set(state="complete", bytes_downloaded=path.stat().st_size, error=None)
    except urllib.error.URLError as exc:
        _set(state="error", error=f"Could not download the model: {exc}")
    except OSError as exc:
        _set(state="error", error=f"Could not write the model file: {exc}")
    except Exception as exc:  # noqa: BLE001
        _set(state="error", error=str(exc))
