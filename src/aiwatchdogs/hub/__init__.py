"""FastAPI Coordinator Hub, SQLite WAL persistence, SSE stream, and Desk API for AI Watchdogs India."""

from aiwatchdogs.hub.app import app, create_app, run_cli

__all__ = ["app", "create_app", "run_cli"]
