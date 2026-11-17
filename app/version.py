"""Version metadata, resolved without a package install."""

from __future__ import annotations

import os
from pathlib import Path

FALLBACK = "1.0.0"
UPSTREAM = "https://github.com/simplytoast1/ha-whisker-ting"


def version() -> str:
    if env := os.environ.get("TING_VERSION"):
        return env
    try:
        text = (Path(__file__).resolve().parent.parent / "VERSION").read_text()
        return text.strip() or FALLBACK
    except OSError:
        return FALLBACK
