#!/usr/bin/env python3
"""One-time, out-of-band download of the faster-whisper STT weights.

Run this ONCE during setup (or in CI) to prime the cache. Like
``download_embed_model.py``, it deliberately does NOT import Eva's net-guard, so
the weights can be fetched from HuggingFace. After this completes the model loads
fully offline at runtime (voice/stt.py tries a local-only load first), so the
privacy guard is never tripped during normal use.

This mirrors CLAUDE.md rule 4: "Only the first-run model/voice download is
allowed." In the shipped app this is driven by the Phase-10 first-run setup
screen; this script is the developer/CI equivalent.

    backend/.venv/bin/python scripts/download_whisper_model.py            # base.en
    backend/.venv/bin/python scripts/download_whisper_model.py small.en   # a size
    backend/.venv/bin/python scripts/download_whisper_model.py all        # both

The weights land in <vault>/models/whisper, exactly where voice/stt.py looks.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Import Eva's settings/paths without dragging in the net-guard (app.py installs
# it). We only need vault_dir + the list of sizes.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from memory import vault_dir  # noqa: E402
from settings import WHISPER_SIZES  # noqa: E402

COMPUTE_TYPE = "int8"


def download(size: str) -> None:
    """Materialise one whisper size into the vault's whisper cache."""
    from faster_whisper import WhisperModel

    cache = str(vault_dir() / "models" / "whisper")
    print(f"Downloading faster-whisper {size} (int8) → {cache} …")
    WhisperModel(size, device="auto", compute_type=COMPUTE_TYPE, download_root=cache)
    print(f"OK — {size} ready.")


def main(argv: list[str]) -> int:
    arg = (argv[0] if argv else "base.en").strip()
    if arg == "all":
        sizes = list(WHISPER_SIZES)
    elif arg in WHISPER_SIZES:
        sizes = [arg]
    else:
        print(f"Unknown size {arg!r}. Choose one of: {', '.join(WHISPER_SIZES)}, or 'all'.")
        return 2
    for size in sizes:
        download(size)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
