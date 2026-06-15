"""Eva's memory layer.

The capture spine of the app. Two stores live here, with a strict hierarchy:

* ``vault.py`` — **L0**, the user's journal as plain Markdown on disk. This is
  the single source of truth. It is human-readable without Eva and never depends
  on any database.
* ``db.py`` — **L1**, a SQLite index *derived* from L0. It is queryable and
  rebuildable; deleting it must never lose anything that L0 already holds.

Later phases add L2 (ChromaDB vectors) and the extraction pipeline on top of the
same package, but the L0→L1 invariant above is the one that must never break.
"""

from __future__ import annotations

import os
from pathlib import Path

# The repo root is two levels up from this file: memory/ -> backend/ -> repo/.
_REPO_ROOT = Path(__file__).resolve().parents[2]


def vault_dir() -> Path:
    """Return the vault root directory (``local_vault/``).

    Single source of truth for where Eva keeps the user's private data, so
    ``vault.py`` and ``db.py`` can never disagree about the location. Defaults to
    ``local_vault/`` at the repo root (matching ``.gitignore``); overridable via
    the ``EVA_VAULT_DIR`` environment variable so tests can redirect it to a
    temporary directory. This function does not create the directory — callers
    that write do that explicitly.
    """
    env = os.environ.get("EVA_VAULT_DIR")
    if env:
        return Path(env).expanduser()
    return _REPO_ROOT / "local_vault"
