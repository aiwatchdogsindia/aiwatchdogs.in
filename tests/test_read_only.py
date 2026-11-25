"""Preview deployments share the live database, so they must never write to it."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aiwatchdogs.hub.app import create_app
from aiwatchdogs.hub.database import Database, ReadOnlyDatabaseError, is_read_only_deployment


@pytest.mark.parametrize(
    ("env", "flag", "expected"),
    [("production", "", False), ("", "", False), ("preview", "", True), ("development", "", True), ("production", "1", True)],
)
def test_which_deployments_are_read_only(monkeypatch: pytest.MonkeyPatch, env: str, flag: str, expected: bool) -> None:
    monkeypatch.setenv("VERCEL_ENV", env)
    monkeypatch.setenv("AIWATCHDOGS_READ_ONLY", flag)
    assert is_read_only_deployment() is expected


def test_preview_serves_pages_but_cannot_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VERCEL_ENV", raising=False)
    path = tmp_path / "hub.db"
    with TestClient(create_app(db_path=path, seed=True)):
        pass
    monkeypatch.setenv("VERCEL_ENV", "preview")
    monkeypatch.setattr("aiwatchdogs.hub.app.seed_database", lambda *a, **k: pytest.fail("seeded"))
    monkeypatch.setattr("aiwatchdogs.hub.app.sync_projects", lambda *a, **k: pytest.fail("synced"))
    app = create_app(db_path=path, seed=True)
    with TestClient(app) as client:
        for url in ("/health", "/api/head", "/api/missions", "/api/findings", "/api/contributors", "/api/live",
                    "/api/graph", "/api/sponsors", "/api/ledger", "/api/events", "/api/sources", "/feed.atom", "/join.txt"):
            assert client.get(url).status_code == 200, url
        for url in ("/api/agents/register", "/api/sponsors/pledge", "/api/desk/next", "/api/desk/usage"):
            assert client.post(url, json={}).status_code == 403, url
        with pytest.raises(ReadOnlyDatabaseError), app.state.db.transaction():
            pass
        with app.state.db.read() as conn, pytest.raises(sqlite3.OperationalError):
            conn.execute("UPDATE missions SET title = 'x'")


def test_read_only_open_does_not_create_or_migrate(tmp_path: Path) -> None:
    db = Database(db_path=tmp_path / "empty.db", read_only=True)
    with db.read() as conn:
        assert conn.execute("SELECT COUNT(*) AS c FROM sqlite_master").fetchone()["c"] == 0
