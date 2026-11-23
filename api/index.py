"""Vercel Serverless / Fluid Compute ASGI entrypoint for AI Watchdogs India Hub."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

os.environ.setdefault("AIWATCHDOGS_REPO_ROOT", str(ROOT))
if os.environ.get("VERCEL") and not (os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL")):
    os.environ.setdefault("AIWATCHDOGS_DB_PATH", "/tmp/aiwatchdogs.db")

from aiwatchdogs.hub.app import get_default_app

app = get_default_app()
