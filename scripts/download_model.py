#!/usr/bin/env python3
"""Cross-platform Gemma GGUF downloader for source/dev setup.

Packaged Eva uses the backend download endpoint. This script is the terminal
fallback for developers and manual installs on macOS, Windows, or Linux.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL_FILE = "gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf"
MODEL_URL = (
    "https://huggingface.co/unsloth/gemma-4-E2B-it-qat-GGUF/resolve/main/"
    f"{MODEL_FILE}?download=true"
)
MIN_FREE_BYTES = 5 * 1024 * 1024 * 1024


def _human(n: int) -> str:
    """Return a compact human-readable byte count."""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def download(dest: Path, *, force: bool = False) -> int:
    """Download the GGUF to ``dest`` with disk-space and partial-file checks."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0 and not force:
        print(f"[download] {dest} already exists; nothing to do.")
        return 0
    free = shutil.disk_usage(dest.parent).free
    if free < MIN_FREE_BYTES:
        print(
            f"[download] not enough free space in {dest.parent}: "
            f"{_human(free)} free, at least {_human(MIN_FREE_BYTES)} required.",
            file=sys.stderr,
        )
        return 2

    part = Path(str(dest) + ".part")
    print(f"[download] Fetching {MODEL_FILE} into {dest.parent}")
    try:
        request = urllib.request.Request(MODEL_URL, headers={"User-Agent": "Eva/0.1"})
        with urllib.request.urlopen(request, timeout=30) as resp:  # noqa: S310 - explicit model URL
            total = resp.headers.get("Content-Length")
            total_n = int(total) if total and total.isdigit() else None
            done = 0
            with part.open("wb") as out:
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    if total_n:
                        pct = done / total_n * 100
                        print(f"\r[download] {_human(done)} / {_human(total_n)} ({pct:.1f}%)", end="")
                    else:
                        print(f"\r[download] {_human(done)}", end="")
        print()
        if part.stat().st_size <= 0:
            raise RuntimeError("download wrote an empty file")
        part.replace(dest)
        print(f"[download] Done: {dest}")
        return 0
    except (urllib.error.URLError, OSError, RuntimeError) as exc:
        print(f"\n[download] failed: {exc}", file=sys.stderr)
        return 1


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Download Eva's local Gemma GGUF model.")
    parser.add_argument("--output", type=Path, default=ROOT / "models" / MODEL_FILE)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    return download(args.output, force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())
