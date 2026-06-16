"""Single settings store — the one place Eva's user-configurable options live.

EVA_SYSTEM_DESIGN §9 ("Configuration") calls for *a single settings store* (vault
path, voice + speed, persona default, model path, whisper size) that the UI
surfaces and the backend reads. Phase 8 needs exactly one of those knobs — the
Whisper model size for speech-to-text — so this module is introduced now with
that single setting wired end-to-end. Phase 10 ("Settings screen for real")
extends the same store with the rest; the shape here is the seam it grows into.

Design choices that matter:

* **Stored in the vault, as JSON.** Settings are user-owned, local, and survive
  restarts; they belong next to the user's data, not in app code. The file is
  ``<vault>/settings.json`` (``EVA_VAULT_DIR`` redirects it for tests).
* **Defaults are the source of truth for *which* keys exist.** Only known keys
  are read or written; an unknown key in a hand-edited file is ignored rather
  than trusted. This keeps a stray or stale key from leaking into the app.
* **Validated writes.** A setting with a closed set of valid values (the whisper
  size) is checked on write, so the STT layer can trust whatever it reads back.
* **No network, ever.** Reading/writing settings touches only the local disk.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from memory import vault_dir

log = logging.getLogger("eva.settings")

# The faster-whisper model sizes Phase 8 offers. ``base.en`` (int8) is the
# default — small, fast, accurate enough for clear English; ``small.en`` is the
# fallback the user can switch to in Settings if transcription is poor on their
# accent (plan Phase 8). English-only models only, matching CLAUDE.md.
WHISPER_SIZES = ("base.en", "small.en")

# The complete set of settings keys, with their defaults. Adding a Phase-10
# setting means adding it here (and, if it has a closed value set, to ALLOWED).
DEFAULTS: dict[str, object] = {
    "whisper_model_size": "base.en",
}

# Keys whose value must be one of a fixed set. Checked on every write so readers
# (e.g. voice/stt.py) never have to defend against a bad value from disk.
ALLOWED: dict[str, tuple[str, ...]] = {
    "whisper_model_size": WHISPER_SIZES,
}

# Writes are serialized so two concurrent PATCHes can't interleave a read and a
# write and lose one of the changes (FastAPI may handle requests on threads).
_write_lock = threading.Lock()


def _settings_path() -> Path:
    """Return the on-disk settings file (``<vault>/settings.json``)."""
    return vault_dir() / "settings.json"


def load() -> dict:
    """Return the current settings, defaults filled in for anything unset.

    Reads ``<vault>/settings.json`` if present and overlays only *known* keys
    onto the defaults, so a missing file, an unreadable file, or an unexpected
    key never produces a broken settings object. The returned dict is always
    complete — every key in :data:`DEFAULTS` is present with a valid value.
    """
    data = dict(DEFAULTS)
    path = _settings_path()
    if not path.exists():
        return data
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("settings: could not read %s (%s); using defaults", path, exc)
        return data
    if isinstance(stored, dict):
        for key in DEFAULTS:
            if key in stored:
                value = stored[key]
                # Drop a stored value that is no longer valid (e.g. a removed
                # whisper size), falling back to the default rather than trusting it.
                if key in ALLOWED and value not in ALLOWED[key]:
                    log.warning(
                        "settings: ignoring invalid stored %s=%r; using default %r",
                        key, value, DEFAULTS[key],
                    )
                    continue
                data[key] = value
    return data


def get(key: str):
    """Return one setting's current value (default if the file lacks it)."""
    return load().get(key, DEFAULTS.get(key))


def update(patch: dict) -> dict:
    """Apply a partial update and persist it, returning the full new settings.

    Only keys in :data:`DEFAULTS` may be set, and any key in :data:`ALLOWED` is
    validated against its permitted values — an unknown key or an invalid value
    raises ``ValueError`` so a bad request fails loudly instead of silently
    writing junk. The write is atomic-enough for our needs (whole file rewritten
    under a lock); the vault directory is created if it does not yet exist.
    """
    with _write_lock:
        data = load()
        for key, value in patch.items():
            if key not in DEFAULTS:
                raise ValueError(f"unknown setting {key!r}")
            if key in ALLOWED and value not in ALLOWED[key]:
                raise ValueError(
                    f"invalid value {value!r} for {key!r}; "
                    f"allowed: {', '.join(ALLOWED[key])}"
                )
            data[key] = value
        path = _settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        log.info("settings: updated %s", ", ".join(f"{k}={patch[k]!r}" for k in patch))
        return data


def options() -> dict:
    """Return the valid choices for closed-set settings, for the Settings UI.

    The dropdown in Settings renders straight from this, so the backend stays the
    single source of truth for which whisper sizes exist — the UI never hard-codes
    the list.
    """
    return {key: list(values) for key, values in ALLOWED.items()}
