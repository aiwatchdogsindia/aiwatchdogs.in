"""The upgrade the live hub goes through, on both database backends.

Renaming the police project and growing it from 4 tasks to 100 runs once, on production's
PostgreSQL, at deploy. These checks run the same steps on SQLite always and on a real
PostgreSQL when ``TEST_POSTGRES_URL`` is set (CI's PostgreSQL job runs this file).
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pytest

from aiwatchdogs.hub.database import Database
from aiwatchdogs.hub.desk import DeskService
from aiwatchdogs.hub.sse import SSEBroker
from aiwatchdogs.projects import slug_aliases, sync_projects

TEST_POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "").strip()
REPO_PROJECTS = Path(__file__).resolve().parents[1] / "projects"
BASE = "https://aiwatchdogs.in"
OLD_TASK_IDS = (
    "metro-police-delhi-notes-2024-25",
    "metro-police-delhi-notes-2025-26",
    "metro-police-hyderabad-2026-27",
    "metro-police-mumbai-2026-27",
)

pytestmark = pytest.mark.production_missions


@pytest.fixture()
def pg_url() -> Iterator[str]:
    import psycopg

    base = TEST_POSTGRES_URL.replace("postgres://", "postgresql://", 1)
    schema = f"awd_upgrade_{secrets.token_hex(6)}"
    with psycopg.connect(base, autocommit=True) as admin:
        admin.execute(f'CREATE SCHEMA "{schema}"')
    parts = urlsplit(base)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "options"]
    query.append(("options", f"-csearch_path={schema},public"))
    try:
        yield urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
    finally:
        with psycopg.connect(base, autocommit=True) as admin:
            admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


def _old_project() -> dict[str, Any]:
    """The project as it stood before the upgrade: same slug and task ids as the live hub."""
    new = json.loads((REPO_PROJECTS / "police-finances.json").read_text(encoding="utf-8"))
    tasks = [t for t in new["tasks"] if t["id"] in OLD_TASK_IDS]
    assert len(tasks) == 4
    return {
        **{k: new[k] for k in ("posted_by", "pillar", "statutory_anchors", "methodology_code", "priority")},
        "slug": "metro-police-finances",
        "title": "Audit the books of India's big-city police: Mumbai, Bengaluru, Delhi and Hyderabad",
        "summary": "We go through the public records of money spent by four metropolitan police forces, line by line.",
        "status": "active",
        "jurisdiction": "Delhi (Union), Maharashtra, Telangana and Karnataka",
        "authority": "Ministry of Home Affairs and the Home Departments of Maharashtra, Telangana and Karnataka",
        "sources": new["sources"][:1],
        "tasks": tasks,
    }


def _finding(task: dict[str, Any]) -> dict[str, Any]:
    payload = task["payload"]
    return {
        "title": "Delhi Police: revised estimate vs budget estimate is 12.5% below (Demand 51 line 5, capital)",
        "summary": (
            "The official budget document prints 892.66 crore for RE FY2023-24 against 1,019.92 crore for BE FY2023-24 "
            "on Demand 51 line 5, capital, a difference of -12.5% (threshold 10%). This is a statistical divergence in "
            "published figures for public review, not a finding of wrongdoing."
        ),
        "methodology_code": "P1",
        "entity_name": "Delhi Police",
        "jurisdiction": payload["jurisdiction"],
        "fiscal_year": "FY2023-24",
        "monetary_exposure_inr": 1272600000,
        "severity": "low",
        "anomaly_metrics": {"line_key": "delhi-police-capital", "compare": "re_vs_be", "re": 892.66, "be": 1019.92, "unit": "crore", "pct_difference": -12.48, "threshold_pct": 10.0},
        "rule_triggered": "P1-RE-VS-BE>10%",
        "engine_version": "2.0.0",
        "ruleset_version": "P1-2026.11-v1",
        "artifact_sha256": payload["expected_sha256"],
        "citations": [{"page": 1, "printed_line": "5. Delhi Police 11240.07 287.22 11527.29 10642.11 1019.92 11662.03 11047.67 892.66 11940.33 10291.28 889.05 11180.33"}],
        "benign_explanations": ["Capital works are often re-phased across years because of land, approvals or tendering timelines."],
        "suggested_authority": "Ministry of Home Affairs, Government of India",
    }


def _confirm(finding_id: str, sha: str) -> dict[str, Any]:
    return {
        "finding_id": finding_id,
        "verdict": "confirm",
        "artifact_sha256": sha,
        "engine_version": "2.0.0",
        "ruleset_version": "P1-2026.11-v1",
        "checks": {k: "pass" for k in ("bytes_digest", "deterministic_reproduction", "metric_tolerance", "neutrality_privacy")},
        "reproduced_metrics": {"re": 892.66, "be": 1019.92, "pct_difference": -12.48, "threshold_pct": 10.0},
        "confidence": 0.95,
        "notes": "Independently reproduced on the pinned file.",
    }


def _one(db: Database, sql: str, params: tuple[Any, ...] = ()) -> Any:
    with db.read() as conn:
        row = conn.execute(sql, params).fetchone()
        return None if row is None else row[next(iter(row.keys()))]


def _check_live_upgrade(db: Database, tmp_path: Path, open_db: Any) -> None:
    desk = DeskService(db, SSEBroker(db))
    # Before the deploy the hub carries the old police project next to every other project in the
    # repository (they are already live and must come through the upgrade untouched).
    old_dir = tmp_path / "before"
    old_dir.mkdir()
    (old_dir / "metro-police-finances.json").write_text(json.dumps(_old_project()))
    others = [f for f in REPO_PROJECTS.glob("*.json") if not f.name.startswith("_") and f.name != "police-finances.json"]
    for other in others:
        shutil.copy(other, old_dir / other.name)
    assert sync_projects(db, old_dir)["changed"] is True
    other_tasks_before = _one(db, "SELECT COUNT(*) AS n FROM tasks WHERE mission_slug != 'metro-police-finances'")

    # State of the live hub before the upgrade: one audit done, its lead waiting for two peer checks.
    author = desk.register_agent({"handle": "early-agent", "provider": "antigravity", "model": "Gemini"}, base_url=BASE)
    lease = desk.claim_next_task(token=author["api_token"], mode="investigate")
    assert lease is not None and lease["task"]["id"] == "prj-metro-police-delhi-notes-2024-25"
    accepted = desk.submit_task({"lease_id": lease["lease_id"], "outcome": "finding", "finding": _finding(lease["task"])}, token=author["api_token"])
    finding_id = accepted["finding_id"]
    assert _one(db, "SELECT total_tasks AS n FROM missions WHERE slug = 'metro-police-finances'") == 6

    # ---- the deploy: the repository's project file replaces the old one
    result = sync_projects(db, REPO_PROJECTS)
    assert result["changed"] is True and result["skipped"] == []
    desk.slug_aliases = slug_aliases(REPO_PROJECTS)

    # Renamed in place: not archived, not duplicated, and no other project is touched.
    assert _one(db, "SELECT COUNT(*) AS n FROM missions") == 1 + len(others)
    assert _one(db, "SELECT COUNT(*) AS n FROM missions WHERE status != 'active'") == 0
    assert _one(db, "SELECT COUNT(*) AS n FROM missions WHERE slug = 'metro-police-finances' OR id = 'project-metro-police-finances'") == 0
    assert _one(db, "SELECT status AS s FROM missions WHERE id = 'project-police-finances'") == "active"
    assert _one(db, "SELECT COUNT(*) AS n FROM tasks WHERE mission_slug = 'metro-police-finances' OR mission_id = 'project-metro-police-finances'") == 0
    assert _one(db, "SELECT COUNT(*) AS n FROM tasks WHERE mission_slug != 'police-finances'") == other_tasks_before
    assert _one(db, "SELECT COUNT(*) AS n FROM tasks WHERE mission_id = 'project-police-finances' AND mission_slug != 'police-finances'") == 0
    assert _one(db, "SELECT COUNT(*) AS n FROM tasks WHERE task_type = 'anomaly_scan'") == 100
    assert _one(db, "SELECT COUNT(*) AS n FROM tasks WHERE task_type = 'peer_verify' AND status = 'queued'") == 2
    assert _one(db, "SELECT status AS s FROM tasks WHERE id = 'prj-metro-police-delhi-notes-2024-25'") == "completed"
    assert _one(db, "SELECT mission_slug AS s FROM findings WHERE id = ?", (finding_id,)) == "police-finances"
    assert _one(db, "SELECT COUNT(*) AS n FROM evidence_artifacts WHERE mission_id != 'project-police-finances'") == 0
    assert _one(db, "SELECT COUNT(*) AS n FROM whispers WHERE mission_slug = 'metro-police-finances'") == 0
    assert _one(db, "SELECT total_tasks AS n FROM missions WHERE slug = 'police-finances'") == 102
    assert _one(db, "SELECT completed_tasks AS n FROM missions WHERE slug = 'police-finances'") == 1
    # A queued task that was in the old file is refreshed from the new one.
    hyderabad = json.loads(_one(db, "SELECT payload_json AS p FROM tasks WHERE id = 'prj-metro-police-hyderabad-2026-27'"))
    assert hyderabad["project_slug"] == "police-finances"
    assert _one(db, "SELECT dataset_key AS k FROM tasks WHERE id = 'prj-metro-police-hyderabad-2026-27'") == "telangana-police-revenue-fy2026-27"

    # Deploying again (every cold start runs the sync) changes nothing.
    assert sync_projects(db, REPO_PROJECTS)["changed"] is False
    assert _one(db, "SELECT COUNT(*) AS n FROM tasks WHERE mission_slug = 'police-finances'") == 102

    # Old links keep working, and the page lists every task with its role.
    detail = desk.get_mission_detail("metro-police-finances")
    assert detail["slug"] == "police-finances" and len(detail["tasks"]) == 102
    assert {t["role"] for t in detail["tasks"]} == {"auditor", "peer_verifier"}
    assert [f["id"] for f in detail["findings"]] == [finding_id]

    # ---- the swarm arrives: twelve agents claim at the same moment
    agents = [desk.register_agent({"handle": f"swarm-{i:02d}", "provider": "rule-engine", "model": "v1"}, base_url=BASE) for i in range(12)]
    barrier = threading.Barrier(len(agents))

    def claim(agent: dict[str, Any]) -> tuple[str, str, str] | None:
        w_db = open_db()
        try:
            w_desk = desk if w_db is db else DeskService(w_db, SSEBroker(w_db))
            barrier.wait(timeout=20)
            got = w_desk.claim_next_task(token=agent["api_token"], mode="auto")
            return None if got is None else (got["task"]["id"], got["role"]["name"], got["lease_id"])
        finally:
            if w_db is not db:
                w_db.close()

    with ThreadPoolExecutor(max_workers=len(agents)) as pool:
        claims = list(pool.map(claim, agents))
    assert None not in claims
    assert len({c[0] for c in claims if c}) == 12  # twelve different tasks, no double hand-out
    # Agents that join the ordinary way are only given audit work, never another project's research tasks.
    assert all(c and c[0].startswith(("prj-police-", "prj-metro-police-", "tsk-pv-")) for c in claims)
    # Peer checks come first in auto mode: the two waiting ones are taken, by two different agents.
    assert sorted(c[1] for c in claims if c) == ["auditor"] * 10 + ["peer_verifier"] * 2

    # The two peer verifiers confirm the lead submitted before the upgrade: it becomes verified.
    sha = _one(db, "SELECT artifact_sha256 AS s FROM findings WHERE id = ?", (finding_id,))
    last: dict[str, Any] = {}
    for agent, got in zip(agents, claims, strict=True):
        if got and got[1] == "peer_verifier":
            last = desk.submit_task({"lease_id": got[2], "outcome": "verification", "verification": _confirm(finding_id, sha)}, token=agent["api_token"])
    assert last["finding_status"] == "verified" and last["confirm_count"] == 2
    assert _one(db, "SELECT COUNT(*) AS n FROM rti_drafts WHERE finding_id = ?", (finding_id,)) == 1
    # Delhi Police is run by the Union Home Ministry: its RTI goes to the central portal.
    assert _one(db, "SELECT jurisdiction_level AS j FROM rti_drafts WHERE finding_id = ?", (finding_id,)) == "central"

    # ---- a task parked by three failed attempts comes back by itself
    victim = next(c for c in claims if c and c[1] == "auditor")
    with db.transaction() as conn:
        conn.execute(
            "UPDATE tasks SET status = 'needs_adjudication', retry_count = 3, claimed_by_agent_id = NULL, claimed_by_handle = NULL, "
            "lease_id = NULL, lease_expires_at = NULL, updated_at = '2020-01-01T00:00:00Z' WHERE id = ?",
            (victim[0],),
        )
    rescuer = desk.register_agent({"handle": "late-joiner", "provider": "rule-engine", "model": "v1"}, base_url=BASE)
    assert desk.claim_next_task(token=rescuer["api_token"], mode="auto") is not None
    assert _one(db, "SELECT status AS s FROM tasks WHERE id = ?", (victim[0],)) in ("queued", "claimed")
    assert _one(db, "SELECT COUNT(*) AS n FROM tasks WHERE status = 'needs_adjudication'") == 0


def test_live_upgrade_on_sqlite(tmp_path: Path) -> None:
    db = Database(db_path=tmp_path / "upgrade.db")
    try:
        _check_live_upgrade(db, tmp_path, lambda: db)
    finally:
        db.close()


@pytest.mark.skipif(not TEST_POSTGRES_URL, reason="Set TEST_POSTGRES_URL to run against real PostgreSQL.")
def test_live_upgrade_on_postgres(pg_url: str, tmp_path: Path) -> None:
    db = Database(database_url=pg_url)
    try:
        assert db.is_postgres is True
        _check_live_upgrade(db, tmp_path, lambda: Database(database_url=pg_url))
    finally:
        db.close()
