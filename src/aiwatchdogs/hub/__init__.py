"""FastAPI Coordinator Hub, Postgres/SQLite persistence, SSE stream, and Desk API for AI Watchdogs India."""

from typing import Any

from aiwatchdogs.hub.app import create_app, get_default_app, run_cli

__all__ = ["app", "create_app", "get_default_app", "run_cli"]


def __getattr__(name: str) -> Any:
    if name == "app":
        return get_default_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
