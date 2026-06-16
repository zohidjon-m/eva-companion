#!/usr/bin/env python3
"""One-time, out-of-band download of Kokoro's TTS assets (Phase 9 voice out).

Run this ONCE during setup, after ``pip install -r backend/requirements.txt`` has
installed ``kokoro`` into the backend venv. Like the other ``download_*`` scripts,
it intentionally does NOT import Eva's net-guard or ``voice.tts`` (which forces
HuggingFace offline), so the assets can actually be fetched. After it completes,
the backend loads Kokoro fully offline at runtime and the privacy guard is never
tripped.

It materialises the three things Kokoro needs, all of which the runtime net-guard
would otherwise block on first use (which is the "Could not load Eva's voice"
error):
  1. the Kokoro-82M weights + config (from HuggingFace, hexgrad/Kokoro-82M);
  2. the ``af_heart`` voice tensor;
  3. spaCy's ``en_core_web_sm`` — Kokoro's misaki G2P loads it, and on a clean
     machine tries to pip-install it on first use (blocked offline).

    backend/.venv/bin/python scripts/download_kokoro_model.py

Mirrors CLAUDE.md rule 4: "Only the first-run model/voice download is allowed."
"""
from __future__ import annotations

import subprocess
import sys

# Must match voice/tts.py (imported by hardcode rather than `from voice import tts`,
# because importing that module sets HF_HUB_OFFLINE=1 and would block this download).
VOICE = "af_heart"
LANG_CODE = "a"  # American English


def _ensure_spacy_model() -> None:
    """Ensure spaCy's en_core_web_sm is installed (Kokoro's G2P needs it)."""
    try:
        import en_core_web_sm  # noqa: F401

        print("• spaCy en_core_web_sm already present.")
        return
    except ImportError:
        pass
    print("• Installing spaCy en_core_web_sm …")
    subprocess.check_call([sys.executable, "-m", "spacy", "download", "en_core_web_sm"])


def main() -> int:
    _ensure_spacy_model()

    print(f"• Downloading Kokoro-82M weights + voice {VOICE!r} (one-time)…")
    import numpy as np
    from kokoro import KPipeline

    pipe = KPipeline(lang_code=LANG_CODE)
    samples = 0
    # Synthesizing a probe forces the weights AND the voice tensor to materialise.
    for _graphemes, _phonemes, audio in pipe("Eva's voice is ready.", voice=VOICE):
        samples += len(np.asarray(audio).reshape(-1))
    if samples == 0:
        print("WARNING: Kokoro loaded but produced no audio.")
        return 1
    print(f"OK — Kokoro ready ({samples} samples synthesized as a probe).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
