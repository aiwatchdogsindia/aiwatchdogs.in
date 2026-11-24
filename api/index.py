"""Vercel Serverless / Fluid Compute ASGI entrypoint for AI Watchdogs India Hub.

Fails closed when running on Vercel or with ``AIWATCHDOGS_REQUIRE_POSTGRES=1`` if
neither ``DATABASE_URL`` nor ``POSTGRES_URL`` is configured, rather than silently
falling back to ephemeral instance-local SQLite.
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

from aiwatchdogs.hub.app import create_degraded_app, get_default_app
from aiwatchdogs.hub.database import is_postgres_required, resolve_database_url

if is_postgres_required() and resolve_database_url() is None:
    app = create_degraded_app(
        reason=(
            "Durable PostgreSQL storage is required in production "
            "(DATABASE_URL or POSTGRES_URL is not configured). "
            "Refusing to fall back to ephemeral instance-local SQLite."
        )
    )
else:
    app = get_default_app()
