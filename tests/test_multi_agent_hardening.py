"""Regression and concurrency tests for multi-agent swarm readiness and hardening."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from aiwatchdogs.cli import config as cfg_mod
from aiwatchdogs.cli.config import AgentConfig, get_handle_config_path, save_config
from aiwatchdogs.cli.main import app as cli_app
from aiwatchdogs.cli.runner import AgentRunner
from aiwatchdogs.connectors import loader
from aiwatchdogs.core.safety import compute_sha256
from aiwatchdogs.hub import database as db_mod
from aiwatchdogs.hub.app import create_app
from aiwatchdogs.hub.database import LATEST_SCHEMA_VERSION, Database
from aiwatchdogs.hub.sse import SSEBroker
from test_projects import SHA, _project


# ---------------------------------------------------------------------------
# 1. Postgres connection pool concurrency & stale-connection recovery (offline)
# ---------------------------------------------------------------------------


class _FakePgCursor:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []
        self.rowcount = len(self._rows)

    def fetchone(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def __enter__(self) -> _FakePgCursor:
        return self

    def __exit__(self, *args: object) -> None:
        return None


class _FakePgConn:
    def __init__(self, cid: int, *, fail_first_stmt: bool = False) -> None:
        self.cid = cid
        self.closed = False
        self.broken = False
        self.read_only = False
        self.commits = 0
        self.rollbacks = 0
        self.fail_first_stmt = fail_first_stmt

    def execute(self, sql: str, params: Any = None) -> _FakePgCursor:
        _ = params
        if self.fail_first_stmt:
            self.fail_first_stmt = False
            self.broken = True
            raise db_mod._PG_TRANSIENT_ERRORS[0]("simulated Neon idle disconnect")
        if "SELECT version FROM schema_migrations" in sql:
            return _FakePgCursor([{"version": v} for v in range(1, LATEST_SCHEMA_VERSION + 1)])
        if "SELECT 1 AS ok" in sql:
            return _FakePgCursor([{"ok": 1}])
        return _FakePgCursor([{"cid": self.cid}])

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


def test_postgres_pool_allows_concurrent_reads_and_recovers_stale_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not db_mod._PG_TRANSIENT_ERRORS:
        pytest.skip("psycopg not installed")

    created: list[_FakePgConn] = []
    lock = threading.Lock()

    def _fake_connect(*_args: Any, **_kwargs: Any) -> _FakePgConn:
        with lock:
            conn = _FakePgConn(len(created) + 1)
            created.append(conn)
            return conn

    monkeypatch.setenv("AIWATCHDOGS_PG_POOL_MAX", "4")
    monkeypatch.setattr(db_mod.psycopg, "connect", _fake_connect)

    db = Database(database_url="postgresql://user:pass@127.0.0.1:5432/testdb")
    try:
        # Four threads must enter db.read() simultaneously; a single process-wide RLock would deadlock here.
        barrier = threading.Barrier(4)

        def _worker(_i: int) -> int:
            with db.read() as conn:
                barrier.wait(timeout=5)
                row = conn.execute("SELECT cid").fetchone()
                # Nested read() on the same thread reuses the active pooled connection without deadlocking.
                with db.read() as inner:
                    inner_row = inner.execute("SELECT cid").fetchone()
                    assert inner_row["cid"] == row["cid"]
                return int(row["cid"])

        with ThreadPoolExecutor(max_workers=4) as pool:
            cids = list(pool.map(_worker, range(4)))
        assert len(set(cids)) == 4

        # Simulate a stale idle connection dropped by Neon: statement 0 transparently reconnects.
        stale = db._pg_pool.get_nowait()
        stale.fail_first_stmt = True
        db._pg_pool.put_nowait(stale)
        assert db.health_check() is True
        assert stale.closed is True
    finally:
        db.close()

    assert all(c.closed for c in created)


# ---------------------------------------------------------------------------
# 2. Migration v11 composite indexes & per-agent rate limiting
# ---------------------------------------------------------------------------


def test_v11_swarm_scale_indexes_created(tmp_path: Path) -> None:
    db_path = tmp_path / "idx.db"
    db = Database(db_path=db_path)
    try:
        assert db.schema_version() == LATEST_SCHEMA_VERSION
    finally:
        db.close()

    conn = sqlite3.connect(db_path)
    indexes = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    conn.close()
    for expected in (
        "idx_tasks_mission_status_type",
        "idx_tasks_claim_queue",
        "idx_tasks_mission_slug_status",
        "idx_tasks_status_updated",
        "idx_tasks_claimed_agent_status",
        "idx_tasks_mission_completed_amt",
        "idx_findings_mission_status_quar",
    ):
        assert expected in indexes, expected


@pytest.mark.production_missions
def test_multi_agent_same_ip_rate_limits_isolated_per_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AIWATCHDOGS_DISABLE_RATE_LIMIT", raising=False)
    pdir = tmp_path / "projects"
    pdir.mkdir()
    proj = _project(slug="police-finances")
    (pdir / "police-finances.json").write_text(json.dumps(proj))
    monkeypatch.setenv("AIWATCHDOGS_PROJECTS_DIR", str(pdir))
    monkeypatch.delenv("AIWATCHDOGS_KEEP_LEGACY_MISSIONS", raising=False)

    with TestClient(create_app(db_path=tmp_path / "rl.db", seed=True)) as client:
        ip_hdr = {"x-vercel-forwarded-for": "198.51.100.42"}

        # 25 distinct coding agents behind one NAT IP can all register cleanly (limit=60/min, was 20/min).
        tokens: list[str] = []
        for i in range(25):
            r = client.post(
                "/api/agents/register",
                headers=ip_hdr,
                json={"handle": f"swarm-agent-{i:02d}", "provider": "rule-engine", "model": "v1"},
            )
            assert r.status_code == 200, (i, r.text)
            tokens.append(r.json()["api_token"])

        # Agent 0 hits its own per-agent desk_next cap (60/min) -> 61st call returns 429 with Retry-After.
        for i in range(60):
            r = client.post(
                "/api/desk/next",
                headers={**ip_hdr, "X-Agent-Token": tokens[0]},
                json={"mode": "verify"},
            )
            assert r.status_code == 204, (i, r.status_code)
        r_over = client.post(
            "/api/desk/next",
            headers={**ip_hdr, "X-Agent-Token": tokens[0]},
            json={"mode": "verify"},
        )
        assert r_over.status_code == 429
        assert "retry-after" in r_over.headers

        # Agent 1 behind the EXACT SAME IP is NOT starved by Agent 0's per-agent 429!
        r_other = client.post(
            "/api/desk/next",
            headers={**ip_hdr, "X-Agent-Token": tokens[1]},
            json={"mode": "verify"},
        )
        assert r_other.status_code == 204


# ---------------------------------------------------------------------------
# 3. --project police-finances + former alias, rung fairness, /back.md, SSE reconnect
# ---------------------------------------------------------------------------


@pytest.mark.production_missions
def test_police_finances_open_choice_and_former_slug_and_rungs_unstarved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdir = tmp_path / "projects"
    pdir.mkdir()
    monkeypatch.setenv("AIWATCHDOGS_PROJECTS_DIR", str(pdir))
    monkeypatch.delenv("AIWATCHDOGS_KEEP_LEGACY_MISSIONS", raising=False)
    monkeypatch.delenv("AIWATCHDOGS_ALLOW_PROJECT_CHOICE", raising=False)
    monkeypatch.setenv("AIWATCHDOGS_DISABLE_RATE_LIMIT", "1")

    p1 = _project(slug="police-finances", formerly=["metro-police-finances"], priority=5)
    p1["tasks"][0]["id"] = "pf-task-01"
    (pdir / "police-finances.json").write_text(json.dumps(p1))

    # A higher-priority project with 600 tasks created after P1 would previously starve P1 rungs on /api/head.
    big_base = _project()["tasks"][0]
    big_tasks = [
        {**big_base, "id": f"big-task-{i:04d}", "dataset_key": f"big-d-{i:04d}", "title": f"Big check {i:04d}"}
        for i in range(600)
    ]
    big = _project(slug="big-priority-one", priority=1, tasks=big_tasks)
    (pdir / "big-priority-one.json").write_text(json.dumps(big))

    with TestClient(create_app(db_path=tmp_path / "hub.db", seed=True)) as client:
        # Both projects must have rungs on /api/head (no global LIMIT 500 starvation).
        head = client.get("/api/head")
        assert head.status_code == 200
        assert "s-maxage=5" in head.headers.get("cache-control", "")
        missions_by_slug = {m["slug"]: m for m in head.json()["missions"]}
        assert len(missions_by_slug["police-finances"]["rungs"]) > 0
        assert len(missions_by_slug["big-priority-one"]["rungs"]) > 0

        # Requesting police-finances by canonical or former slug honors the choice even when another project has priority 1.
        tok = client.post(
            "/api/agents/register",
            json={"handle": "p1-chooser", "provider": "rule-engine", "model": "v1"},
        ).json()["api_token"]
        claim = client.post(
            "/api/desk/next",
            headers={"X-Agent-Token": tok},
            json={"mode": "investigate", "preferred_mission": "metro-police-finances"},
        )
        assert claim.status_code == 200, claim.text
        assert claim.json()["task"]["mission_slug"] == "police-finances"

        # /back.md renders actual sponsor pledge totals instead of ₹0.
        pledge = client.post(
            "/api/sponsors/pledge",
            json={"name": "Civic Supporter", "amount_inr": 25000.0},
        )
        assert pledge.status_code == 201
        back_md = client.get("/back.md")
        assert back_md.status_code == 200
        assert "25,000" in back_md.text


@pytest.mark.production_missions
def test_auto_settled_peer_verify_clears_agent_current_task_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdir = tmp_path / "projects"
    pdir.mkdir()
    (pdir / "test-city-budget.json").write_text(json.dumps(_project()))
    monkeypatch.setenv("AIWATCHDOGS_PROJECTS_DIR", str(pdir))
    monkeypatch.delenv("AIWATCHDOGS_KEEP_LEGACY_MISSIONS", raising=False)
    monkeypatch.setenv("AIWATCHDOGS_DISABLE_RATE_LIMIT", "1")

    app = create_app(db_path=tmp_path / "quorum.db", seed=True)
    with TestClient(app) as client:
        def _reg(h: str) -> dict[str, str]:
            tok = client.post(
                "/api/agents/register",
                json={"handle": h, "provider": "rule-engine", "model": "v1"},
            ).json()["api_token"]
            return {"X-Agent-Token": tok}

        author = _reg("author-01")
        v1 = _reg("verifier-01")
        v2 = _reg("verifier-02")

        lease = client.post("/api/desk/next", headers=author, json={"mode": "investigate"}).json()
        sub = client.post(
            "/api/desk/submit",
            headers=author,
            json={
                "lease_id": lease["lease_id"],
                "outcome": "finding",
                "finding": {
                    "title": "Test City: budget variance lead",
                    "summary": "Neutral public-record comparison for independent review.",
                    "methodology_code": "M5",
                    "entity_name": "Test City",
                    "jurisdiction": "State:Test",
                    "fiscal_year": "FY2024-25",
                    "monetary_exposure_inr": 1e7,
                    "severity": "low",
                    "artifact_sha256": SHA,
                    "anomaly_metrics": {"pct_difference": -15.0},
                    "benign_explanations": ["Mid-year re-appropriation."],
                },
            },
        )
        assert sub.status_code == 200, sub.text
        fid = sub.json()["finding_id"]

        # Both verifiers claim peer_verify leases concurrently.
        l1 = client.post("/api/desk/next", headers=v1, json={"mode": "verify"}).json()
        l2 = client.post("/api/desk/next", headers=v2, json={"mode": "verify"}).json()
        assert l1["task"]["target_finding_id"] == fid
        assert l2["task"]["target_finding_id"] == fid

        # Verifier 1 flags PII/legal -> quarantines finding and auto-settles Verifier 2's claimed lease.
        flag = client.post(
            "/api/desk/submit",
            headers=v1,
            json={
                "lease_id": l1["lease_id"],
                "outcome": "verification",
                "verification": {
                    "finding_id": fid,
                    "verdict": "flag_pii_or_legal",
                    "checks": {
                        "bytes_digest": "pass",
                        "deterministic_reproduction": "pass",
                        "metric_tolerance": "pass",
                        "neutrality_privacy": "fail",
                    },
                    "notes": "Contains unredacted personal details.",
                },
            },
        )
        assert flag.status_code == 200, flag.text

        # Verifier 2's agents.current_task_id must be cleared to NULL (not left stale on the settled task).
        with app.state.db.read() as conn:
            row = conn.execute("SELECT current_task_id FROM agents WHERE handle = 'verifier-02'").fetchone()
        assert row is not None and row["current_task_id"] is None


def test_sse_emits_graceful_reconnect_frame_on_max_duration() -> None:
    broker = SSEBroker(db=None)

    async def _collect() -> list[str]:
        frames: list[str] = []
        async for chunk in broker.subscribe(keepalive_seconds=5.0, max_duration_seconds=0.15):
            frames.append(chunk)
        return frames

    frames = asyncio.run(_collect())
    assert any("event: connected" in f for f in frames)
    assert any("event: reconnect" in f and '"stream_cycle"' in f for f in frames)


# ---------------------------------------------------------------------------
# 4. CLI multi-agent safety: `projects`, sibling lease protection, retries, SHA-256 cache
# ---------------------------------------------------------------------------


def test_cli_projects_works_with_multiple_saved_handles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(cfg_mod.ENV_CONFIG_PATH, raising=False)
    monkeypatch.delenv(cfg_mod.ENV_HANDLE, raising=False)
    legacy = tmp_path / "aiwatchdogs" / "config.json"
    monkeypatch.setattr(cfg_mod, "DEFAULT_CONFIG_PATH", legacy)
    for h in ("agent-01", "agent-02"):
        save_config(AgentConfig(handle=h, hub_url="https://aiwatchdogs.in", api_token=f"tok-{h}"), path=get_handle_config_path(h))

    monkeypatch.setattr(
        "aiwatchdogs.cli.main._bounded_http_get_json",
        lambda url, timeout=10.0: {"missions": [{"methodology_code": "P1", "slug": "police-finances", "pillar": "budget", "jurisdiction": "All-India", "data_mode": "live_public", "completed_tasks": 3, "total_tasks": 100, "verified_findings_count": 0}]},
    )
    res = CliRunner().invoke(cli_app, ["projects"], env={"COLUMNS": "160"})
    assert res.exit_code == 0, res.output
    assert "Projects (https://aiwatchdogs.in)" in res.output
    assert "police-finances" in res.output


def test_sibling_process_lease_note_is_not_revoked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    note = tmp_path / "shared-handle.json.lease"
    released: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/desk/release":
            released.append(json.loads(request.content)["lease_id"])
            return httpx.Response(200, json={"status": "released"})
        return httpx.Response(204)

    cfg = AgentConfig(handle="shared-handle", hub_url="http://mockhub", api_token="tok-1")
    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://mockhub")
    runner = AgentRunner(cfg, http_client=client, config_path=tmp_path / "shared-handle.json")

    # Case 1: lease note owned by a live sibling PID -> must NOT be released or deleted.
    note.write_text(json.dumps({"hub": "http://mockhub", "handle": "shared-handle", "lease_id": "lease-live", "pid": 999999}))
    monkeypatch.setattr("aiwatchdogs.cli.runner._pid_is_alive", lambda pid: pid == 999999)
    assert runner.recover_interrupted_lease() is False
    assert released == []
    assert note.is_file()

    # Case 2: lease note owned by a dead PID -> recovered and released cleanly.
    note.write_text(json.dumps({"hub": "http://mockhub", "handle": "shared-handle", "lease_id": "lease-dead", "pid": 888888}))
    assert runner.recover_interrupted_lease() is True
    assert released == ["lease-dead"]
    assert not note.exists()


def test_fetch_finding_retries_on_429_and_503() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "1"}, json={"detail": "rate limited"})
        if attempts == 2:
            return httpx.Response(503, json={"detail": "temporarily unavailable"})
        return httpx.Response(200, json={"finding": {"id": "fnd-abc123", "title": "Recovered Finding"}})

    cfg = AgentConfig(handle="verifier", hub_url="http://mockhub", api_token="tok-v")
    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://mockhub")
    runner = AgentRunner(cfg, http_client=client)
    runner._sleep = lambda _s: None
    finding = runner._fetch_finding("fnd-abc123")
    assert finding is not None and finding["id"] == "fnd-abc123"
    assert attempts == 3


def test_pinned_sha256_dataset_reuses_verified_cache_across_tasks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AIWATCHDOGS_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("AIWATCHDOGS_DISABLE_CACHE", raising=False)
    body = b"department,budget,actual\nHealth,100,80\n"
    digest = compute_sha256(body)
    url = "https://www.indiabudget.gov.in/doc/eb/test-cache-reuse.csv"
    fetches = 0

    class _FakeResp:
        status_code = 200
        headers = {"content-type": "text/csv"}
        content = body

        def __init__(self, req_url: str) -> None:
            self.url = req_url

        def raise_for_status(self) -> None:
            return None

    def _fake_get(self: Any, req_url: str, *args: Any, **kwargs: Any) -> _FakeResp:
        nonlocal fetches
        _ = (self, args, kwargs)
        fetches += 1
        return _FakeResp(req_url)

    monkeypatch.setattr(loader, "_validate_public_https_url", lambda _u, resolve_dns=False: None)
    monkeypatch.setattr(httpx.Client, "get", _fake_get)
    with loader._PINNED_MEM_CACHE_LOCK:
        loader._PINNED_MEM_CACHE.clear()
        loader._PINNED_MEM_CACHE_BYTES = 0

    cfg = AgentConfig(handle="cache-node", hub_url="http://mockhub", api_token="tok-c")
    client = httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(204)), base_url="http://mockhub")
    runner = AgentRunner(cfg, http_client=client)

    payload = {"retrieval_url": url, "expected_sha256": digest}
    ds1 = runner._load_task_dataset(payload, expected_sha256=digest)
    ds2 = runner._load_task_dataset(payload, expected_sha256=digest)
    assert ds1.sha256 == digest and ds2.sha256 == digest
    assert fetches == 1

    # Unpinned URLs (no expected_sha256) never reuse cache in unattended runner mode.
    unpinned_url = "https://www.indiabudget.gov.in/doc/eb/test-unpinned.csv"
    ds3 = runner._load_task_dataset({"retrieval_url": unpinned_url})
    ds4 = runner._load_task_dataset({"retrieval_url": unpinned_url})
    assert ds3.sha256 == digest and ds4.sha256 == digest
    assert fetches == 3
