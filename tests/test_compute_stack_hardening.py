"""Regression tests for compute-stack hardening:
- Hub capability gate (claim queue + explicit task_id)
- CLI UnsupportedMethodology release (peer_verify + anomaly_scan)
- Retry-budget preservation on capability refusal release
- Server-side negative-verdict evidence gate + quorum defensive filter
- CLI User-Agent 0.8 upgrade gate (426 for 0.7 and earlier)
- Historical unsupported-vote reconciliation script (dry-run + apply)
- Deterministic AI reading verifier (Grand Total, horizontal totals, quotes, scans, goldens) + API
- Deterministic release fingerprint (`aiwatchdogs reproduce`)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from aiwatchdogs.cli.config import AgentConfig
from aiwatchdogs.cli.main import app as cli_app
from aiwatchdogs.cli.runner import CLI_USER_AGENT, AgentRunner
from aiwatchdogs.engines.reading_verifier import verify_budget_extraction
from aiwatchdogs.hub.app import MIN_OFFICIAL_CLI_VERSION, create_app
from aiwatchdogs.hub.database import Database, dumps_json
from aiwatchdogs.hub.quorum import evaluate_pairwise_quorum

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
from reconcile_unsupported_votes import reconcile_unsupported_votes  # noqa: E402

SHA = "a" * 64
COMMIT = "0123456789abcdef0123456789abcdef01234567"


def _seed_two_projects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a lightweight 2-project directory (P1 + UB1 with planned reading backlog)."""
    pdir = tmp_path / "projects"
    pdir.mkdir(parents=True, exist_ok=True)
    p1 = {
        "slug": "test-police-p1",
        "project_code": "P1",
        "priority": 1,
        "title": "Test police budget audit project",
        "summary": "Checks published police budget estimates against actuals neutrally.",
        "posted_by": {"name": "Test Team", "handle": "@testteam", "url": "https://example.org"},
        "pillar": "budget",
        "status": "active",
        "jurisdiction": "Test State",
        "authority": "Home Department, Test State",
        "scope": ["Police expenditure checks"],
        "out_of_scope": ["Private individuals"],
        "statutory_anchors": [],
        "sources": [{"name": "Test Budget", "publisher": "Finance Dept", "url": "https://finance.test.gov.in"}],
        "methodology_code": "P1",
        "tasks": [
            {
                "id": "test-p1-001",
                "title": "Test police FY2024-25",
                "instructions": "Run P1 deterministic check.",
                "dataset_key": "test-p1-001",
                "estimated_tokens": 50_000,
                "estimate_basis": "test fixture",
                "financial_quantity_type": "budget_and_treasury_expenditure",
                "payload": {
                    "retrieval_url": f"https://raw.githubusercontent.com/org/repo/{COMMIT}/p1.csv",
                    "expected_sha256": SHA,
                    "official_publisher": "Finance Dept",
                    "source_landing_page": "https://finance.test.gov.in",
                    "jurisdiction": "Test State",
                    "fiscal_year": "FY2024-25",
                },
            }
        ],
        "planned_tasks": [
            {
                "title": "Read 11 scanned State police budget PDFs",
                "type": "extract_data",
                "estimated_tokens": 19_000_000,
                "estimate_basis": "11 state image-only budget books",
            }
        ],
    }
    p3 = {
        "slug": "test-budget-m5",
        "project_code": "P3",
        "priority": 2,
        "title": "Test fiscal pacing budget audit project",
        "summary": "Checks fiscal pacing and budget estimates against actuals neutrally.",
        "posted_by": {"name": "Test Team", "handle": "@testteam", "url": "https://example.org"},
        "pillar": "budget",
        "status": "active",
        "jurisdiction": "Union of India",
        "authority": "Ministry of Finance",
        "scope": ["Notes on Demands for Grants"],
        "out_of_scope": ["Private individuals"],
        "statutory_anchors": [],
        "sources": [{"name": "Union Budget", "publisher": "Ministry of Finance", "url": "https://www.indiabudget.gov.in/"}],
        "methodology_code": "M5",
        "tasks": [
            {
                "id": "test-m5-001",
                "title": "Test M5 Demand 10",
                "instructions": "Run M5 deterministic check.",
                "dataset_key": "test-m5-001",
                "estimated_tokens": 2000,
                "estimate_basis": "test fixture",
                "financial_quantity_type": "budget_and_treasury_expenditure",
                "payload": {
                    "retrieval_url": f"https://raw.githubusercontent.com/org/repo/{COMMIT}/m5.csv",
                    "expected_sha256": "b" * 64,
                    "official_publisher": "Ministry of Finance",
                    "source_landing_page": "https://www.indiabudget.gov.in/",
                    "jurisdiction": "Union of India",
                    "fiscal_year": "FY2024-25",
                },
            }
        ],
        "planned_tasks": [
            {
                "title": "17 broken Union Budget statements that do not add up to their Grand Total",
                "type": "extract_data",
                "estimated_tokens": 6_000_000,
                "estimate_basis": "6 damaged files and 11 misread tables",
            }
        ],
    }
    (pdir / "test-police-p1.json").write_text(json.dumps(p1), encoding="utf-8")
    (pdir / "test-budget-m5.json").write_text(json.dumps(p3), encoding="utf-8")
    monkeypatch.setenv("AIWATCHDOGS_PROJECTS_DIR", str(pdir))
    monkeypatch.delenv("AIWATCHDOGS_KEEP_LEGACY_MISSIONS", raising=False)
    return pdir


def _register(client: TestClient, handle: str, *, coding_agent: str, model: str, ua: str = CLI_USER_AGENT) -> dict[str, Any]:
    resp = client.post(
        "/api/agents/register",
        headers={"User-Agent": ua},
        json={
            "handle": handle,
            "provider": "rule-engine",
            "model": "deterministic-cpu-v1",
            "coding_agent": coding_agent,
            "coding_agent_model": model,
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_min_cli_version_blocks_v07_and_allows_v08(tmp_path: Path) -> None:
    assert MIN_OFFICIAL_CLI_VERSION == (0, 8)
    assert CLI_USER_AGENT == "AIWatchdogs-India-AgentCLI/0.8"

    app = create_app(db_path=tmp_path / "ver.db", seed=True)
    with TestClient(app, base_url="http://testserver") as client:
        old_ua = {"User-Agent": "AIWatchdogs-India-AgentCLI/0.7"}
        assert client.post("/api/agents/register", json={"handle": "stale-07"}, headers=old_ua).status_code == 426
        assert client.post("/api/desk/next", json={}, headers=old_ua).status_code == 426

        ok = client.post(
            "/api/agents/register",
            json={"handle": "fresh-08"},
            headers={"User-Agent": CLI_USER_AGENT},
        )
        assert ok.status_code == 200


def test_capability_gate_filters_claims_and_blocks_explicit_incompatible_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_two_projects(tmp_path, monkeypatch)
    app = create_app(db_path=tmp_path / "cap.db", seed=True)
    with TestClient(app, base_url="http://testserver") as client:
        agent = _register(client, "cap-worker", coding_agent="Claude Code", model="claude-sonnet-4")
        tok = agent["api_token"]

        # Only advertise M5 -> skips priority-1 P1 task and receives M5 task!
        claim = client.post(
            "/api/desk/next",
            headers={"X-Agent-Token": tok, "User-Agent": CLI_USER_AGENT},
            json={"mode": "investigate", "supported_methodologies": ["M5"]},
        )
        assert claim.status_code == 200, claim.text
        task = claim.json()["task"]
        assert task["methodology_code"] == "M5"

        # Explicit claim of P1 task while advertising only M5 -> rejected with 409!
        bad_explicit = client.post(
            "/api/desk/next",
            headers={"X-Agent-Token": tok, "User-Agent": CLI_USER_AGENT},
            json={"task_id": "prj-test-p1-001", "supported_methodologies": ["M5"]},
        )
        assert bad_explicit.status_code == 409
        assert "P1" in bad_explicit.json()["detail"]


def test_unsupported_methodology_release_preserves_retry_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_two_projects(tmp_path, monkeypatch)
    app = create_app(db_path=tmp_path / "retry.db", seed=True)
    with TestClient(app, base_url="http://testserver") as client:
        agent = _register(client, "rel-worker", coding_agent="Codex", model="gpt-5")
        tok = agent["api_token"]

        claim1 = client.post(
            "/api/desk/next",
            headers={"X-Agent-Token": tok, "User-Agent": CLI_USER_AGENT},
            json={"mode": "investigate", "supported_methodologies": ["P1"]},
        )
        assert claim1.status_code == 200
        tid = claim1.json()["task"]["id"]
        lease_id = claim1.json()["lease_id"]

        # Release 5 times with reason='unsupported_methodology' -> retry_count stays 0 and status stays 'queued'!
        for _ in range(5):
            rel = client.post(
                "/api/desk/release",
                headers={"X-Agent-Token": tok, "User-Agent": CLI_USER_AGENT},
                json={
                    "lease_id": lease_id,
                    "reason": "unsupported_methodology",
                    "notes": "Old build lacks engine.",
                },
            )
            assert rel.status_code == 200, rel.text
            body = rel.json()
            assert body["retry_count"] == 0
            assert body["task_status"] == "queued"

            reclaim = client.post(
                "/api/desk/next",
                headers={"X-Agent-Token": tok, "User-Agent": CLI_USER_AGENT},
                json={"task_id": tid, "supported_methodologies": ["P1"]},
            )
            assert reclaim.status_code == 200, reclaim.text
            lease_id = reclaim.json()["lease_id"]


def test_cli_runner_releases_unknown_methodology_without_voting_or_downloading(tmp_path: Path) -> None:
    submitted: list[dict[str, Any]] = []
    released: list[dict[str, Any]] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/desk/beat":
            return httpx.Response(200, json={"status": "active"})
        if request.url.path == "/api/desk/submit":
            submitted.append(json.loads(request.content))
            return httpx.Response(200, json={"status": "accepted"})
        if request.url.path == "/api/desk/release":
            released.append(json.loads(request.content))
            return httpx.Response(200, json={"status": "released"})
        return httpx.Response(404)

    cfg = AgentConfig(handle="old-build", hub_url="http://mockhub", api_token="awd_tok")
    with httpx.Client(transport=httpx.MockTransport(_handler), base_url="http://mockhub") as http_client:
        runner = AgentRunner(cfg, http_client=http_client, allow_local_files=True)
        # 1. Peer verify with unknown methodology -> releases with unsupported_methodology, zero submissions.
        res_pv = runner.execute_task(
            {
                "lease_id": "lease-pv-zz9",
                "task": {
                    "id": "t-pv-zz9",
                    "task_type": "peer_verify",
                    "methodology_code": "ZZ9",
                    "payload": {
                        "finding_id": "fnd-zz9",
                        "methodology_code": "ZZ9",
                        "title": "Hypothetical future audit finding",
                        "summary": "Neutral public record summary for review.",
                        "dataset_url": "https://example.gov.in/should-not-be-fetched.pdf",
                    },
                },
            }
        )
        assert res_pv["status"] == "task_error" or res_pv.get("outcome") in ("released", "error")
        assert submitted == []
        assert len(released) == 1
        assert released[0]["reason"] == "unsupported_methodology"

        # 2. Primary anomaly_scan with unknown methodology -> releases immediately, zero submissions.
        res_scan = runner.execute_task(
            {
                "lease_id": "lease-scan-zz9",
                "task": {
                    "id": "t-scan-zz9",
                    "task_type": "anomaly_scan",
                    "methodology_code": "ZZ9",
                    "payload": {"retrieval_url": "https://example.gov.in/never-downloaded.pdf"},
                },
            }
        )
        assert submitted == []
        assert len(released) == 2
        assert released[1]["reason"] == "unsupported_methodology"


def test_hub_rejects_unsupported_engine_and_empty_dispute_votes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_two_projects(tmp_path, monkeypatch)
    app = create_app(db_path=tmp_path / "evgate.db", seed=True)
    with TestClient(app, base_url="http://testserver") as client:
        author = _register(client, "auth-ev", coding_agent="Claude Code", model="claude-opus-4")
        peer = _register(client, "peer-ev", coding_agent="Codex", model="gpt-5")

        lease = client.post(
            "/api/desk/next",
            headers={"X-Agent-Token": author["api_token"], "User-Agent": CLI_USER_AGENT},
            json={"mode": "investigate", "supported_methodologies": ["P1"]},
        ).json()
        task = lease["task"]
        sub = client.post(
            "/api/desk/submit",
            headers={"X-Agent-Token": author["api_token"], "User-Agent": CLI_USER_AGENT},
            json={
                "lease_id": lease["lease_id"],
                "outcome": "finding",
                "finding": {
                    "title": "Police capital outlay variance noted in public budget",
                    "summary": "Budget estimate and revised estimate differ by over 15 percent for public review, not wrongdoing.",
                    "methodology_code": "P1",
                    "entity_name": "Test State Police",
                    "jurisdiction": "Test State",
                    "fiscal_year": "FY2024-25",
                    "monetary_exposure_inr": 50_000_000.0,
                    "source_url": "https://finance.test.gov.in/budget.pdf",
                    "artifact_sha256": task["payload"]["expected_sha256"],
                    "anomaly_metrics": {"variance_pct": 20.0},
                    "rule_triggered": "P1-TEST",
                    "benign_explanations": ["Capital outlay timing can vary across financial years."],
                },
            },
        )
        assert sub.status_code == 200, sub.text
        fid = sub.json()["finding_id"]

        pv_lease = client.post(
            "/api/desk/next",
            headers={"X-Agent-Token": peer["api_token"], "User-Agent": CLI_USER_AGENT},
            json={"mode": "verify", "supported_methodologies": ["P1"]},
        ).json()

        # 1. Dispute claiming "Unknown methodology_code" MUST be rejected with 422.
        bad1 = client.post(
            "/api/desk/submit",
            headers={"X-Agent-Token": peer["api_token"], "User-Agent": CLI_USER_AGENT},
            json={
                "lease_id": pv_lease["lease_id"],
                "outcome": "verification",
                "verification": {
                    "finding_id": fid,
                    "verdict": "dispute",
                    "checks": {"deterministic_reproduction": "fail", "metric_tolerance": "fail"},
                    "notes": "Unknown methodology_code 'P9'.",
                },
            },
        )
        assert bad1.status_code == 422

        # 2. Dispute with zero performed checks and no reproduction evidence MUST be rejected with 422.
        bad2 = client.post(
            "/api/desk/submit",
            headers={"X-Agent-Token": peer["api_token"], "User-Agent": CLI_USER_AGENT},
            json={
                "lease_id": pv_lease["lease_id"],
                "outcome": "verification",
                "verification": {
                    "finding_id": fid,
                    "verdict": "dispute",
                    "checks": {},
                    "notes": "I could not run this check.",
                },
            },
        )
        assert bad2.status_code == 422


def test_quorum_ignores_historical_unknown_methodology_votes_and_reconcile_script_repairs_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_two_projects(tmp_path, monkeypatch)
    # Unit check on evaluate_pairwise_quorum directly
    q = evaluate_pairwise_quorum(
        author_handle="author1",
        author_harness="Claude Code",
        author_model="claude-opus-4",
        votes=[
            {
                "contributor_id": "stale1",
                "verdict": "dispute",
                "checks_json": "{}",
                "reproduced_metrics_json": "{}",
                "notes": "Unknown methodology_code 'P10'.",
            },
            {
                "contributor_id": "stale2",
                "verdict": "dispute",
                "checks_json": "{}",
                "reproduced_metrics_json": "{}",
                "notes": "Unknown methodology_code 'P10'.",
            },
        ],
        loads_json_fn=lambda s, d: json.loads(s) if s else d,
    )
    assert q["dispute_count"] == 0
    assert q["effective_votes"] == 0

    # End-to-end DB reconciliation test
    db_path = tmp_path / "reconcile.db"
    app = create_app(db_path=db_path, seed=True)
    with TestClient(app, base_url="http://testserver") as client:
        author = _register(client, "rec-author", coding_agent="Claude Code", model="claude-opus-4")
        stale = _register(client, "rec-stale", coding_agent="Codex", model="gpt-5")
        lease = client.post(
            "/api/desk/next",
            headers={"X-Agent-Token": author["api_token"], "User-Agent": CLI_USER_AGENT},
            json={"mode": "investigate", "supported_methodologies": ["P1"]},
        ).json()
        task = lease["task"]
        sub = client.post(
            "/api/desk/submit",
            headers={"X-Agent-Token": author["api_token"], "User-Agent": CLI_USER_AGENT},
            json={
                "lease_id": lease["lease_id"],
                "outcome": "finding",
                "finding": {
                    "title": "Police budget variance for reconciliation test",
                    "summary": "Budget estimate exceeded actual expenditure by over 10 percent for public review.",
                    "methodology_code": "P1",
                    "entity_name": "Reconcile Police",
                    "jurisdiction": "Test State",
                    "fiscal_year": "FY2024-25",
                    "monetary_exposure_inr": 25_000_000.0,
                    "source_url": "https://finance.test.gov.in/budget.pdf",
                    "artifact_sha256": task["payload"]["expected_sha256"],
                    "anomaly_metrics": {"variance_pct": 14.0},
                    "rule_triggered": "P1-TEST",
                    "benign_explanations": ["Timing difference."],
                },
            },
        )
        fid = sub.json()["finding_id"]

    # Simulate legacy pre-fix bad dispute rows injected directly into DB
    db = Database(db_path=db_path)
    try:
        with db.transaction() as conn:
            stale_id = conn.execute("SELECT id FROM agents WHERE handle = 'rec-stale'").fetchone()["id"]
            conn.execute(
                "UPDATE agents SET compute_score = 30, verifications_done = 2, tasks_completed = 2 WHERE id = ?",
                (stale_id,),
            )
            conn.execute(
                """
                INSERT INTO peer_verifications (
                    id, finding_id, verifier_node_id, contributor_id, verifier_handle,
                    provider, model_id, review_type, verdict, checks_json,
                    reproduced_metrics_json, manifest_json, confidence, notes, created_at
                ) VALUES ('pv-bad-1', ?, ?, 'rec-stale', 'rec-stale', 'rule-engine', 'cpu',
                          'computational_reproduction', 'dispute', '{}', '{}', '{}', 0.5,
                          'Unknown methodology_code ''P10''.', '2026-11-28T05:00:00Z')
                """,
                (fid, stale_id),
            )
            conn.execute("UPDATE findings SET status = 'disputed', editorial_status = 'disputed' WHERE id = ?", (fid,))
            conn.execute("UPDATE tasks SET status = 'completed' WHERE target_finding_id = ?", (fid,))

        # Dry run reports exact damage without mutating DB
        dry = reconcile_unsupported_votes(db, apply=False)
        assert dry["bad_votes_count"] == 1
        assert dry["affected_findings_count"] == 1
        assert dry["points_adjustment_by_handle"] == {"rec-stale": 15}
        assert dry["finding_transitions"][0]["new_status"] == "peer_review"
        assert dry["finding_transitions"][0]["requeue_slots"] == 3

        # Apply repairs DB cleanly and idempotently
        applied = reconcile_unsupported_votes(db, apply=True)
        assert applied["bad_votes_count"] == 1
        assert applied["requeued_peer_verify_tasks"] == 3

        with db.transaction() as conn:
            f_after = conn.execute("SELECT status FROM findings WHERE id = ?", (fid,)).fetchone()
            a_after = conn.execute("SELECT compute_score, verifications_done, tasks_completed FROM agents WHERE handle = 'rec-stale'").fetchone()
            assert f_after["status"] == "peer_review"
            assert a_after["compute_score"] == 15
            assert a_after["verifications_done"] == 1
            assert a_after["tasks_completed"] == 1

        # Second run is a no-op
        second = reconcile_unsupported_votes(db, apply=True)
        assert second["bad_votes_count"] == 0
    finally:
        db.close()


def test_reading_verifier_math_quotes_swaps_scans_and_goldens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_two_projects(tmp_path, monkeypatch)
    page_text = (
        "DEMAND NO. 24 PUBLIC WORKS DEPARTMENT\n"
        "Budget 2024-25 Revised 2024-25 Actual 2023-24\n"
        "1. Capital Outlay on Roads 420.00 380.00 350.00\n"
        "2. Annual Maintenance 310.00 310.00 290.00\n"
        "3. Grants-in-Aid 190.00 180.00 160.00\n"
        "Grand Total 920.00 870.00 800.00\n"
    )
    good_sub = {
        "document_sha256": SHA,
        "page": 14,
        "column_headers": ["Budget 2024-25", "Revised 2024-25", "Actual 2023-24"],
        "rows": [
            {"label": "Capital Outlay on Roads", "be": 420.0, "re": 380.0, "actual": 350.0, "revenue": 0.0, "capital": 420.0, "total": 420.0, "verbatim_quote": "1. Capital Outlay on Roads 420.00 380.00 350.00"},
            {"label": "Annual Maintenance", "be": 310.0, "re": 310.0, "actual": 290.0, "revenue": 310.0, "capital": 0.0, "total": 310.0, "verbatim_quote": "2. Annual Maintenance 310.00 310.00 290.00"},
            {"label": "Grants-in-Aid", "be": 190.0, "re": 180.0, "actual": 160.0, "revenue": 190.0, "capital": 0.0, "total": 190.0, "verbatim_quote": "3. Grants-in-Aid 190.00 180.00 160.00"},
        ],
        "printed_grand_total": {"be": 920.0, "re": 870.0, "actual": 800.0},
    }

    # 1. Clean text-layer extraction -> deterministically verified!
    r_ok = verify_budget_extraction(good_sub, expected_sha256=SHA, page_text=page_text, golden_expected=good_sub)
    assert r_ok.accepted is True
    assert r_ok.verification_mode == "deterministic_verified"
    assert r_ok.golden_accuracy == 1.0

    # 2. Image-only scan (no text layer) -> math closes, routed for 2nd model verification.
    r_scan = verify_budget_extraction(good_sub, expected_sha256=SHA, page_text="")
    assert r_scan.accepted is True
    assert r_scan.verification_mode == "requires_second_model"

    # 3. Wrong Grand Total -> rejected!
    bad_gt = json.loads(json.dumps(good_sub))
    bad_gt["printed_grand_total"]["be"] = 999.0
    r_gt = verify_budget_extraction(bad_gt, expected_sha256=SHA, page_text=page_text)
    assert r_gt.accepted is False
    assert r_gt.checks["vertical_grand_total"] == "fail"

    # 4. Horizontal row mismatch (swapped revenue/capital) -> rejected!
    bad_horiz = json.loads(json.dumps(good_sub))
    bad_horiz["rows"][0]["revenue"] = 100.0
    r_horiz = verify_budget_extraction(bad_horiz, expected_sha256=SHA, page_text=page_text)
    assert r_horiz.accepted is False
    assert r_horiz.checks["horizontal_row_totals"] == "fail"

    # 5. Hallucinated quote -> rejected!
    bad_quote = json.loads(json.dumps(good_sub))
    bad_quote["rows"][0]["verbatim_quote"] = "99. Invented Scheme 420.00"
    r_quote = verify_budget_extraction(bad_quote, expected_sha256=SHA, page_text=page_text)
    assert r_quote.accepted is False
    assert r_quote.checks["verbatim_quotes"] == "fail"

    # 6. Hub API endpoints: /api/reading/backlog & /api/reading/verify
    app = create_app(db_path=tmp_path / "reading.db", seed=True)
    with TestClient(app, base_url="http://testserver") as client:
        bl = client.get("/api/reading/backlog")
        assert bl.status_code == 200
        bl_data = bl.json()
        assert bl_data["count"] == 2
        assert bl_data["total_estimated_tokens"] == 25_000_000

        vr = client.post(
            "/api/reading/verify",
            json={"submission": good_sub, "expected_sha256": SHA, "page_text": page_text},
        )
        assert vr.status_code == 200
        assert vr.json()["accepted"] is True
        assert vr.json()["verification_mode"] == "deterministic_verified"


@pytest.mark.production_missions
def test_reproduce_cli_outputs_repeatable_release_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AIWATCHDOGS_PROJECTS_DIR", str(REPO_ROOT / "projects"))
    runner = CliRunner()
    out1 = tmp_path / "r1.json"
    out2 = tmp_path / "r2.json"
    res1 = runner.invoke(cli_app, ["reproduce", "--project", "police-finances", "--json-out", str(out1)])
    res2 = runner.invoke(cli_app, ["reproduce", "--project", "police-finances", "--json-out", str(out2)])
    assert res1.exit_code == 0, res1.output
    assert res2.exit_code == 0, res2.output
    d1 = json.loads(out1.read_text(encoding="utf-8"))
    d2 = json.loads(out2.read_text(encoding="utf-8"))
    assert d1["release_hash"] == d2["release_hash"]
    assert d1["active_projects"] == 1
    assert d1["total_tasks"] == 100
