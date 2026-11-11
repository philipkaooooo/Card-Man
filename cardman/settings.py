"""Private settings from a local `.env` file (git-ignored), e.g. eBay app keys.

    EBAY_CLIENT_ID=...
    EBAY_CLIENT_SECRET=...

Values already in the environment win. The file never leaves the machine and is never logged.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env(path: Path | str | None = None) -> list[str]:
    """Load KEY=VALUE lines into os.environ; returns the names loaded (never the values)."""
    p = Path(path) if path else ROOT / ".env"
    if not p.exists():
        return []
    loaded = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip().removeprefix("export ").strip(), v.strip().strip('"').strip("'")
        if k and v and k not in os.environ:
            os.environ[k] = v
            loaded.append(k)
    return loaded
