"""Vercel Serverless / Fluid Compute ASGI entrypoint for AI Watchdogs India Hub.

Delegates production database readiness checks to ``get_default_app()`` (which fails
closed with HTTP 503 degraded responses when ``VERCEL=1`` or
``AIWATCHDOGS_REQUIRE_POSTGRES=1`` is set without ``DATABASE_URL`` / ``POSTGRES_URL``)
while preserving a top-level ``app`` assignment required by Vercel's FastAPI detector.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

os.environ.setdefault("AIWATCHDOGS_REPO_ROOT", str(ROOT))

from aiwatchdogs.hub.app import get_default_app

app = get_default_app()
