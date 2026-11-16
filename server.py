"""Vercel Python entrypoint for the Nullpoint FastAPI application.

Vercel's FastAPI preset loads a top-level ``app`` from ``server.py``. The repository keeps the
API package under ``apps/api``; adding that directory to ``sys.path`` avoids relying on the
build environment having installed the project in editable mode.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT / "apps" / "api"))

from api.main import app  # noqa: E402

__all__ = ["app"]
