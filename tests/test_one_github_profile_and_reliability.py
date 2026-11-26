"""Regression tests for one-GitHub-one-profile enforcement, W1 scoring, deadlock-safe locking,
fast-runner 429 handling, and frontend LiveSwarmViewer recency ordering."""

from __future__ import annotations

import csv
import json
import re
import threading
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from aiwatchdogs.cli import config as cfg_mod
from aiwatchdogs.cli.config import (
    ENV_API_TOKEN,
    ENV_CONFIG_PATH,
    ENV_HANDLE,
    AgentConfig,
    find_saved_config_for_handle,
    get_handle_config_path,
    load_config,
    save_config,
)
from aiwatchdogs.cli.main import app as cli_app
from aiwatchdogs.cli.providers import ProviderInfo
from aiwatchdogs.cli.runner import AgentRunner
from aiwatchdogs.hub.app import create_app
from aiwatchdogs.hub.desk import SCORE_WEIGHTS
from aiwatchdogs.hub.templates import render_join_txt

REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECTS_DIR = REPO_ROOT / "projects"
cli_runner = CliRunner()


# ---------------------------------------------------------------------------
# 1. One GitHub Username = One Public Profile (CLI + Config + /join.txt)
# ---------------------------------------------------------------------------


def test_one_github_one_profile_refuses_dash2_and_prioritizes_canonical_handle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_CONFIG_PATH, raising=False)
    monkeypatch.delenv(ENV_HANDLE, raising=False)
    monkeypatch.delenv(ENV_API_TOKEN, raising=False)
    base_cfg = tmp_path / "aiwatchdogs" / "config.json"
    monkeypatch.setattr(cfg_mod, "DEFAULT_CONFIG_PATH", base_cfg)

    hub_app = create_app(db_path=tmp_path / "one_profile.db", seed=True)
    with TestClient(hub_app, base_url="https://aiwatchdogs.in") as asgi_client:
        orig_init = AgentRunner.__init__

        def _patched_init(self: AgentRunner, config: AgentConfig, **kwargs: Any) -> None:
            kwargs.setdefault("http_client", asgi_client)
            orig_init(self, config, **kwargs)

        monkeypatch.setattr(AgentRunner, "__init__", _patched_init)
        monkeypatch.setattr(
            "aiwatchdogs.cli.main.detect_providers",
            lambda **kw: [
                ProviderInfo(
                    provider="rule-engine",
                    model="deterministic-python-v1",
                    label="CPU",
                    is_local=True,
                    zero_cost=True,
                )
            ],
        )

        # Register @sairam once on the Hub
        reg = asgi_client.post(
            "/api/agents/register",
            json={"handle": "sairam", "provider": "rule-engine", "model": "deterministic-python-v1"},
        )
        assert reg.status_code == 200
        tok = reg.json()["api_token"]

        # 1. Fresh machine with stale agents/sairam-2.json ONLY (no sairam token) MUST NOT use sairam-2 or mint sairam-3!
        save_config(
            AgentConfig(handle="sairam-2", hub_url="https://aiwatchdogs.in", api_token="awd_stale_2"),
            path=get_handle_config_path("sairam-2"),
        )
        monkeypatch.setattr("aiwatchdogs.cli.main.detect_github_handle", lambda: ("sairam", "gh auth status"))

        r_fail = cli_runner.invoke(
            cli_app,
            ["join", "--hub", "https://aiwatchdogs.in", "--provider", "rule-engine", "--non-interactive", "--yes", "--no-run"],
        )
        assert r_fail.exit_code == 1, r_fail.output
        assert "already registered" in r_fail.output.lower()
        assert "one github username = one public profile" in r_fail.output.lower()
        assert "agents/sairam.json" in r_fail.output
        assert "AIWATCHDOGS_API_TOKEN" in r_fail.output
        assert [c["handle"] for c in asgi_client.get("/api/contributors").json()["contributors"]] == ["sairam"]

        # 2. Even if agents/sairam.json exists WITHOUT a token (or wrong hub) while node.json HAS the valid token,
        #    find_saved_config_for_handle + join MUST discover node.json and reconnect AS @sairam!
        save_config(
            AgentConfig(handle="sairam", hub_url="https://aiwatchdogs.in", api_token=""),
            path=get_handle_config_path("sairam"),
        )
        node_json = base_cfg.parent / "node.json"
        node_json.write_text(
            json.dumps(
                {
                    "hub": "https://www.aiwatchdogs.in",
                    "registration": {"handle": "sairam", "api_token": tok},
                }
            ),
            encoding="utf-8",
        )
        found = find_saved_config_for_handle("sairam", hub_url="https://aiwatchdogs.in")
        assert found is not None and found.handle == "sairam" and found.api_token == tok

        r_ok = cli_runner.invoke(
            cli_app,
            ["join", "--hub", "https://aiwatchdogs.in", "--provider", "rule-engine", "--non-interactive", "--yes", "--no-run"],
        )
        assert r_ok.exit_code == 0, r_ok.output
        assert "https://aiwatchdogs.in/a/sairam" in r_ok.stdout
        assert "sairam-2" not in r_ok.stdout
        saved_sairam = load_config(get_handle_config_path("sairam"))
        assert saved_sairam is not None and saved_sairam.api_token == tok

        # 3. `aiwatchdogs run --dry-run` with BOTH agents/sairam.json AND stale agents/sairam-2.json
        #    prioritizes @sairam cleanly instead of erroring on AmbiguousConfigError!
        r_run = cli_runner.invoke(cli_app, ["run", "--dry-run"])
        assert r_run.exit_code == 0, r_run.output
        assert "Handle: sairam" in r_run.stdout
        assert "sairam-2" not in r_run.stdout

    # 4. /join.txt explicitly documents one GitHub username = one public profile (never -2 duplicates)
    join_txt = render_join_txt("https://aiwatchdogs.in")
    assert "ONE GitHub" in join_txt or "one GitHub" in join_txt
    assert "never auto-creates" in join_txt.lower()


# ---------------------------------------------------------------------------
# 2. W1 Website Observation Scoring (All-Time + Hourly + Idempotency)
# ---------------------------------------------------------------------------


def test_w1_observations_award_leaderboard_points_and_are_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert SCORE_WEIGHTS["website_batch_observed"] == 10

    monkeypatch.setenv("AIWATCHDOGS_PROJECTS_DIR", str(PROJECTS_DIR))
    monkeypatch.delenv("AIWATCHDOGS_KEEP_LEGACY_MISSIONS", raising=False)
    app = create_app(db_path=tmp_path / "w1_score.db", seed=True)
    with TestClient(app) as client:
        reg = client.post(
            "/api/agents/register",
            json={"handle": "sai-ram-pendyala", "provider": "rule-engine", "model": "deterministic-cpu-v1"},
        ).json()
        auth = {"X-Agent-Token": reg["api_token"]}

        claim = client.post(
            "/api/desk/next",
            headers=auth,
            json={
                "handle": "sai-ram-pendyala",
                "mode": "investigate",
                "preferred_mission": "gov-in-public-information",
                "website_observations": True,
            },
        )
        assert claim.status_code == 200, claim.text
        lease = claim.json()
        task = lease["task"]
        batch_name = Path(task["payload"]["retrieval_url"]).name
        batch_csv = PROJECTS_DIR / "gov-in-public-information" / batch_name
        from aiwatchdogs.engines import gov_pages

        with batch_csv.open(newline="", encoding="utf-8") as fh:
            domains = [row["domain"] for row in csv.DictReader(fh)]

        now_iso = client.get("/api/swarm/live").json()["generated_at"]
        obs_rows = [
            {
                "domain": dom,
                "observed_at": now_iso,
                "outcome": "unassessable",
                "reason": "fetch_failed",
                "pages": [],
                "checks": dict.fromkeys(gov_pages.CHECKS, "unassessable"),
            }
            for dom in domains
        ]

        idem = f"w1-score-{lease['lease_id']}"
        sub1 = client.post(
            "/api/desk/submit",
            headers={**auth, "Idempotency-Key": idem},
            json={
                "lease_id": lease["lease_id"],
                "outcome": "observations",
                "observations": obs_rows,
                "idempotency_key": idem,
            },
        )
        assert sub1.status_code == 200, sub1.text

        # Idempotent replay MUST NOT double-award points!
        sub2 = client.post(
            "/api/desk/submit",
            headers={**auth, "Idempotency-Key": idem},
            json={
                "lease_id": lease["lease_id"],
                "outcome": "observations",
                "observations": obs_rows,
                "idempotency_key": idem,
            },
        )
        assert sub2.status_code == 200
        assert sub2.json().get("idempotent_replay") is True

        for window in ("all", "hour"):
            data = client.get(f"/api/contributors?window={window}").json()
            entry = next(c for c in data["contributors"] if c["handle"] == "sai-ram-pendyala")
            assert entry["score"] == 10
            assert entry["tasks_completed"] == 1

        profile = client.get("/api/contributors/sai-ram-pendyala").json()
        assert profile["agent"]["score"] == 10
        assert profile["agent"]["tasks_completed"] == 1

        head = client.get("/api/head").json()
        top_entry = next(c for c in head["top_contributors"] if c["handle"] == "sai-ram-pendyala")
        assert top_entry["score"] == 10
    app.state.db.close()


# ---------------------------------------------------------------------------
# 3. Postgres Deadlock-Free Canonical Lock Order & Concurrent Stress Test
# ---------------------------------------------------------------------------


def test_postgres_canonical_lock_order_agents_before_tasks_findings_missions(tmp_path: Path) -> None:
    """Verify every Desk write path acquires `agents` locks (sorted by ID) strictly BEFORE
    `tasks`, `findings`, or `missions` locks when `db.is_postgres` is True."""
    app = create_app(db_path=tmp_path / "lock_order.db", seed=True)
    desk = app.state.desk
    db = app.state.db

    a1 = desk.register_agent(
        {"handle": "lock-author", "provider": "rule-engine", "model": "v1"},
        base_url="http://testserver",
    )
    a2 = desk.register_agent(
        {"handle": "lock-verifier", "provider": "rule-engine", "model": "v1"},
        base_url="http://testserver",
    )

    recorded_sql: list[str] = []
    orig_transaction = db.transaction

    class _PgSpyConn:
        def __init__(self, inner: Any) -> None:
            self._inner = inner

        def execute(self, sql: str, params: Any = ()) -> Any:
            recorded_sql.append(" ".join(sql.split()))
            # Strip Postgres-only row-locking clauses so SQLite executes the exact query path cleanly
            sqlite_sql = re.sub(
                r"\bFOR\s+(?:NO\s+KEY\s+)?UPDATE(?:\s+SKIP\s+LOCKED)?",
                "",
                sql,
                flags=re.IGNORECASE,
            )
            return self._inner.execute(sqlite_sql, params)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

    from contextlib import contextmanager

    @contextmanager
    def _spy_transaction():
        db.is_postgres = False
        try:
            with orig_transaction() as inner:
                db.is_postgres = True
                try:
                    yield _PgSpyConn(inner)
                finally:
                    db.is_postgres = False
        finally:
            db.is_postgres = True

    db.is_postgres = True
    db.transaction = _spy_transaction  # type: ignore[method-assign]
    try:
        # A. claim_next_task: locks agents (FOR NO KEY UPDATE) BEFORE tasks (FOR UPDATE)
        recorded_sql.clear()
        lease1 = desk.claim_next_task(token=a1["api_token"], handle=a1["handle"], mode="investigate")
        assert lease1 is not None
        idx_agent_lock = next(
            i for i, s in enumerate(recorded_sql)
            if "FROM agents WHERE id = ? FOR NO KEY UPDATE" in s
        )
        idx_task_lock = next(
            i for i, s in enumerate(recorded_sql)
            if "FROM tasks" in s and "FOR UPDATE" in s
        )
        assert idx_agent_lock < idx_task_lock

        # B. heartbeat: locks agents (FOR NO KEY UPDATE) BEFORE tasks (FOR UPDATE)
        recorded_sql.clear()
        desk.heartbeat(lease_id=lease1["lease_id"], token=a1["api_token"], progress_pct=50, whisper_text="Checking")
        idx_agent_lock = next(
            i for i, s in enumerate(recorded_sql)
            if "FROM agents WHERE id = ? FOR NO KEY UPDATE" in s
        )
        idx_task_lock = next(
            i for i, s in enumerate(recorded_sql)
            if "FROM tasks WHERE lease_id = ? FOR UPDATE" in s
        )
        assert idx_agent_lock < idx_task_lock

        # C. submit_task (cleared): locks agents BEFORE tasks BEFORE UPDATE missions
        recorded_sql.clear()
        desk.submit_task(
            {"lease_id": lease1["lease_id"], "outcome": "cleared", "metrics": {"cleared_count": 5}},
            token=a1["api_token"],
        )
        idx_agent_lock = next(
            i for i, s in enumerate(recorded_sql)
            if "FROM agents WHERE id = ? FOR NO KEY UPDATE" in s
        )
        idx_task_lock = next(
            i for i, s in enumerate(recorded_sql)
            if "FROM tasks WHERE lease_id = ? FOR UPDATE" in s
        )
        idx_mission_upd = next(i for i, s in enumerate(recorded_sql) if s.startswith("UPDATE missions "))
        assert idx_agent_lock < idx_task_lock < idx_mission_upd

        # D. submit_task (finding) -> then peer_verify submission locks BOTH [verifier, author] in ascending ID order!
        lease2 = desk.claim_next_task(token=a1["api_token"], handle=a1["handle"], mode="investigate")
        assert lease2 is not None
        recorded_sql.clear()
        f_res = desk.submit_task(
            {
                "lease_id": lease2["lease_id"],
                "outcome": "finding",
                "finding": {
                    "title": "Public Record Anomaly: Lock Order Test",
                    "summary": "Deterministic summary for lock ordering verification.",
                    "methodology_code": lease2["task"]["methodology_code"],
                    "entity_name": "Assam PWD",
                    "benign_explanations": ["Lawful statutory exception."],
                },
            },
            token=a1["api_token"],
        )
        idx_agent_lock = next(
            i for i, s in enumerate(recorded_sql)
            if "FROM agents WHERE id = ? FOR NO KEY UPDATE" in s
        )
        idx_task_lock = next(
            i for i, s in enumerate(recorded_sql)
            if "FROM tasks WHERE lease_id = ? FOR UPDATE" in s
        )
        assert idx_agent_lock < idx_task_lock

        pv_lease = desk.claim_next_task(token=a2["api_token"], handle=a2["handle"], mode="verify")
        assert pv_lease is not None
        locked_agent_ids: list[str] = []
        orig_lock_agents = desk._lock_agents_ordered

        def _capture_lock_agents(conn: Any, ids: list[str | None]) -> None:
            locked_agent_ids.extend(sorted({str(x).strip() for x in ids if x and str(x).strip()}))
            orig_lock_agents(conn, ids)

        desk._lock_agents_ordered = _capture_lock_agents  # type: ignore[method-assign]
        recorded_sql.clear()
        pv_sha = (
            pv_lease["task"]["payload"].get("expected_sha256")
            or pv_lease["task"]["payload"].get("artifact_sha256")
            or lease2["task"]["payload"].get("expected_sha256")
        )
        verif_payload: dict[str, Any] = {
            "finding_id": f_res["finding_id"],
            "verdict": "confirm",
            "checks": {
                "bytes_digest": "pass",
                "deterministic_reproduction": "pass",
                "metric_tolerance": "pass",
                "neutrality_privacy": "pass",
            },
            "reproduced_metrics": {"ok": 1},
            "confidence": 0.95,
            "notes": "Reproduced deterministically.",
        }
        if pv_sha:
            verif_payload["artifact_sha256"] = pv_sha
        desk.submit_task(
            {
                "lease_id": pv_lease["lease_id"],
                "outcome": "verification",
                "verification": verif_payload,
            },
            token=a2["api_token"],
        )
        assert locked_agent_ids == sorted({a1["agent_id"], a2["agent_id"]})
        agent_lock_indices = [
            i for i, s in enumerate(recorded_sql)
            if "FROM agents WHERE id = ? FOR NO KEY UPDATE" in s
        ]
        idx_task_lock = next(
            i for i, s in enumerate(recorded_sql)
            if "FROM tasks WHERE lease_id = ? FOR UPDATE" in s
        )
        idx_finding_lock = next(
            i for i, s in enumerate(recorded_sql)
            if "FROM findings WHERE id = ? FOR UPDATE" in s
        )
        assert len(agent_lock_indices) == 2
        assert max(agent_lock_indices) < idx_task_lock < idx_finding_lock

        # E. release_task: locks agents BEFORE tasks
        lease3 = desk.claim_next_task(token=a1["api_token"], handle=a1["handle"], mode="investigate")
        assert lease3 is not None
        recorded_sql.clear()
        desk.release_task(lease_id=lease3["lease_id"], reason="operator_pause", token=a1["api_token"])
        idx_agent_lock = next(
            i for i, s in enumerate(recorded_sql)
            if "FROM agents WHERE id = ? FOR NO KEY UPDATE" in s
        )
        idx_task_lock = next(
            i for i, s in enumerate(recorded_sql)
            if "FROM tasks WHERE lease_id = ? FOR UPDATE" in s
        )
        assert idx_agent_lock < idx_task_lock
    finally:
        db.is_postgres = False
        db.transaction = orig_transaction  # type: ignore[method-assign]
        db.close()


def test_concurrent_same_agent_claim_heartbeat_submit_has_zero_errors(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "concurrent_same_agent.db", seed=True)
    desk = app.state.desk
    with TestClient(app) as client:
        reg = client.post(
            "/api/agents/register",
            json={"handle": "dual-worker-user", "provider": "rule-engine", "model": "v1"},
        ).json()
        token = reg["api_token"]
        errors: list[Exception] = []
        completed = 0
        count_lock = threading.Lock()

        def _worker(worker_idx: int) -> None:
            nonlocal completed
            for step in range(4):
                try:
                    lease = desk.claim_next_task(token=token, handle="dual-worker-user", mode="investigate")
                    if lease is None:
                        break
                    desk.heartbeat(
                        lease_id=lease["lease_id"],
                        token=token,
                        progress_pct=50,
                        whisper_text=f"Worker {worker_idx} step {step}",
                    )
                    desk.submit_task(
                        {"lease_id": lease["lease_id"], "outcome": "cleared", "metrics": {"cleared_count": 2}},
                        token=token,
                    )
                    with count_lock:
                        completed += 1
                except Exception as exc:  # pragma: no cover
                    errors.append(exc)

        t1 = threading.Thread(target=_worker, args=(1,))
        t2 = threading.Thread(target=_worker, args=(2,))
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        assert errors == []
        assert completed == 8
        profile = desk.get_contributor_profile("dual-worker-user")
        assert profile["agent"]["tasks_completed"] == 8
        assert profile["agent"]["score"] == 80


# ---------------------------------------------------------------------------
# 4. Unlimited Desk Contributions, /robots.txt Twitterbot, & Runner 429 Backoff
# ---------------------------------------------------------------------------


def test_fast_runner_rate_limits_and_transient_429_graceful_backoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 1. Verify desk work endpoints have ZERO rate limits (unlimited volunteer contributions)
    #    and /robots.txt includes an explicit Twitterbot stanza BEFORE User-agent: * / Content-Signal!
    monkeypatch.delenv("AIWATCHDOGS_DISABLE_RATE_LIMIT", raising=False)
    app = create_app(db_path=tmp_path / "rate_limits.db", seed=True)
    with TestClient(app) as client:
        robots = client.get("/robots.txt")
        assert robots.status_code == 200
        assert "User-agent: Twitterbot\nAllow: /" in robots.text
        assert robots.text.index("User-agent: Twitterbot") < robots.text.index("User-agent: *")
        assert robots.text.index("User-agent: Twitterbot") < robots.text.index("Content-Signal:")

        reg = client.post(
            "/api/agents/register",
            json={"handle": "fast-runner", "provider": "rule-engine", "model": "v1"},
        ).json()
        auth = {"X-Agent-Token": reg["api_token"]}
        # Multiple parallel leases under one GitHub handle (up to 50) all succeed!
        leases = []
        for _ in range(5):
            r_claim = client.post(
                "/api/desk/next",
                headers=auth,
                json={"handle": "fast-runner", "mode": "investigate"},
            )
            assert r_claim.status_code == 200, r_claim.text
            leases.append(r_claim.json()["lease_id"])

        # 200 rapid heartbeats in 1 minute MUST all return 200 (zero throttling!)
        for _ in range(200):
            r_beat = client.post("/api/desk/beat", headers=auth, json={"lease_id": leases[0], "progress_pct": 25})
            assert r_beat.status_code == 200, r_beat.text

        # Release all 5 leases and fire 200 rapid /api/desk/next calls -> ZERO HTTP 429s!
        for lid in leases:
            r_rel = client.post("/api/desk/release", headers=auth, json={"lease_id": lid, "reason": "test_reset"})
            assert r_rel.status_code == 200
        for _ in range(200):
            r_next = client.post("/api/desk/next", headers=auth, json={"handle": "fast-runner", "mode": "verify"})
            assert r_next.status_code == 204, f"Unexpected status {r_next.status_code}: {r_next.text}"

    # 2. Verify runner backs off gracefully on transient 429s WITHOUT Retry-After (e.g. proxy/CDN rate limit)
    #    AND backs off on lease-cap 429 when continuous=True instead of aborting!
    attempts = {"transient": 0, "continuous_cap": 0}
    sample_lease = {
        "lease_id": "lease-429-ok",
        "lease_expires_at": "2099-01-01T00:00:00Z",
        "role": {"name": "auditor", "title": "Auditor", "brief": "do the work"},
        "task": {
            "id": "tsk-429-ok",
            "task_type": "anomaly_scan",
            "mission_slug": "police-finances",
            "methodology_code": "P1",
            "title": "Test Police",
            "instructions": "Run P1",
            "payload": {
                "records": [{"page": 1, "raw_text": "Demand 9\n2055 Police 100.00 200.00 198.00 210.00\n"}],
                "jurisdiction": "State:Test",
                "fiscal_year": "FY2024-27",
                "context": {
                    "budget_lines": [
                        {
                            "key": "t-2055",
                            "entity": "Test Police",
                            "label": "Major Head 2055",
                            "unit": "crore",
                            "parts": [{"page": 1, "pattern": r"^2055 Police\s+\d", "values": {"actual": 1, "be": 2, "re": 3, "be_next": 4}}],
                            "years": {"actual": "FY2024-25", "be": "FY2025-26", "re": "FY2025-26", "be_next": "FY2026-27"},
                        }
                    ],
                    "comparisons": [{"a": "re", "b": "be", "label": "RE vs BE", "threshold_pct": 10, "kind": "re_be"}],
                },
            },
        },
    }

    def _transient_429_hub(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/desk/next":
            attempts["transient"] += 1
            if attempts["transient"] <= 2:
                return httpx.Response(429, json={"detail": "Rate limit exceeded; please retry shortly."})
            return httpx.Response(200, json=sample_lease) if attempts["transient"] == 3 else httpx.Response(204)
        if request.url.path == "/api/desk/beat":
            return httpx.Response(200, json={"status": "active"})
        if request.url.path == "/api/desk/submit":
            return httpx.Response(200, json={"status": "accepted", "outcome": "cleared"})
        return httpx.Response(404)

    cfg = AgentConfig(handle="retry-node", hub_url="http://mockhub", api_token="awd_tok")
    client1 = httpx.Client(transport=httpx.MockTransport(_transient_429_hub), base_url="http://mockhub")
    runner1 = AgentRunner(cfg, http_client=client1)
    sleeps1: list[float] = []
    runner1._sleep = sleeps1.append
    summary1 = runner1.run_loop(max_tasks=1)
    assert summary1.tasks_completed == 1
    assert summary1.cleared_submitted == 1
    assert len(sleeps1) == 2

    def _continuous_cap_hub(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/desk/next":
            attempts["continuous_cap"] += 1
            if attempts["continuous_cap"] == 1:
                return httpx.Response(429, json={"detail": "Agent already holds 2 active task leases"})
            return httpx.Response(200, json=sample_lease)
        if request.url.path == "/api/desk/beat":
            return httpx.Response(200, json={"status": "active"})
        if request.url.path == "/api/desk/submit":
            return httpx.Response(200, json={"status": "accepted", "outcome": "cleared"})
        return httpx.Response(404)

    client2 = httpx.Client(transport=httpx.MockTransport(_continuous_cap_hub), base_url="http://mockhub")
    runner2 = AgentRunner(cfg, http_client=client2)
    sleeps2: list[float] = []
    runner2._sleep = sleeps2.append
    summary2 = runner2.run_loop(max_tasks=1, continuous=True)
    assert summary2.tasks_completed == 1
    assert len(sleeps2) == 1


# ---------------------------------------------------------------------------
# 5. Frontend Visibility: LiveSwarmViewer Recency Sort & Copy Guardrails
# ---------------------------------------------------------------------------


def test_frontend_live_swarm_viewer_recency_sort_and_homepage_copy() -> None:
    lsv_src = (REPO_ROOT / "web" / "src" / "components" / "LiveSwarmViewer.tsx").read_text(encoding="utf-8")
    home_src = (REPO_ROOT / "web" / "src" / "pages" / "HomePage.tsx").read_text(encoding="utf-8")
    plb_src = (REPO_ROOT / "web" / "src" / "components" / "ProjectLeaderboard.tsx").read_text(encoding="utf-8")
    contrib_src = (REPO_ROOT / "web" / "src" / "pages" / "ContributorsPage.tsx").read_text(encoding="utf-8")

    # Recency (`last_seen_at`, `created_at`) MUST come BEFORE `tasks_completed` in sortLiveSwarmNodes
    assert "export function sortLiveSwarmNodes" in lsv_src
    sort_block = lsv_src.split("export function sortLiveSwarmNodes", 1)[1].split("function formatTickingAge", 1)[0]
    assert sort_block.index("last_seen_at") < sort_block.index("created_at") < sort_block.index("tasks_completed")

    # HomePage leaderboard cap expanded from 6 to 10 rows and "zero questions" removed
    assert "HOME_LEADERBOARD_LIMIT = 10" in home_src
    assert "zero questions" not in home_src.lower()
    assert "zero questions" not in plb_src.lower()
    assert "zero prompts" not in lsv_src.lower()
    assert "auto-uses your public GitHub handle" in home_src
    assert "auto-uses your public GitHub handle" in plb_src

    # GitHub username click-through links across profile, live inspector, and leaderboards (skipping civic-*)
    assert "export function githubProfileUrl" in lsv_src
    assert "https://github.com/" in lsv_src
    for src in (lsv_src, home_src, plb_src, contrib_src):
        assert "githubProfileUrl(" in src
        assert 'rel="noopener noreferrer"' in src
    assert "<GitHubAvatar" in contrib_src
    assert "size={48}" in contrib_src
