"""Tests for 70/30 task + independent-review weighted project progress."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from aiwatchdogs.hub.app import create_app
from aiwatchdogs.hub.quorum import (
    REVIEW_PROGRESS_WEIGHT,
    TASK_PROGRESS_WEIGHT,
    compute_project_progress,
)
from test_projects import _project


def _register(
    client: TestClient,
    handle: str,
    *,
    coding_agent: str,
    coding_agent_model: str,
) -> dict[str, Any]:
    resp = client.post(
        "/api/agents/register",
        json={
            "handle": handle,
            "display_name": f"Agent {handle}",
            "provider": "rule-engine",
            "model": "deterministic-python-v1",
            "coding_agent": coding_agent,
            "coding_agent_model": coding_agent_model,
            "mode": "auto",
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _submit_p1_finding(client: TestClient, token: str, lease: dict[str, Any]) -> str:
    expected_sha = lease["task"]["payload"]["expected_sha256"]
    resp = client.post(
        "/api/desk/submit",
        headers={"X-Agent-Token": token},
        json={
            "lease_id": lease["lease_id"],
            "outcome": "finding",
            "finding": {
                "title": "Delhi Police: revised estimate vs budget estimate differs by -18.4%",
                "summary": (
                    "Published budget figures for Delhi Police show a -18.4% divergence between "
                    "revised and budget estimates for public review, not a finding of wrongdoing."
                ),
                "methodology_code": lease["task"]["methodology_code"],
                "entity_name": "Delhi Police",
                "jurisdiction": "Union of India (Delhi Police)",
                "fiscal_year": "FY2024-25",
                "monetary_exposure_inr": 250_000_000.0,
                "severity": "high",
                "anomaly_metrics": {
                    "line_key": "delhi-police-cap",
                    "compare": "re_vs_be",
                    "re": 816.0,
                    "be": 1000.0,
                    "unit": "crore",
                    "pct_difference": -18.4,
                    "threshold_pct": 10.0,
                },
                "rule_triggered": "P1-RE-VS-BE>10%",
                "source_url": "https://www.indiabudget.gov.in/budget2024-25/",
                "artifact_sha256": expected_sha,
                "citations": [
                    {
                        "source_ref": "https://www.indiabudget.gov.in/budget2024-25/doc/eb/sbe51.pdf",
                        "page": 1,
                    }
                ],
                "benign_explanations": [
                    "Capital expenditure may be re-phased across financial years due to tendering timelines."
                ],
                "suggested_authority": "Ministry of Home Affairs, Government of India",
            },
        },
    )
    assert resp.status_code == 200, resp.text
    return str(resp.json()["finding_id"])


def _confirm_finding(client: TestClient, token: str, task_id: str, finding_id: str, sha256: str) -> dict[str, Any]:
    claim = client.post(
        "/api/desk/next",
        headers={"X-Agent-Token": token},
        json={"task_id": task_id},
    )
    assert claim.status_code == 200, claim.text
    sub = client.post(
        "/api/desk/submit",
        headers={"X-Agent-Token": token},
        json={
            "lease_id": claim.json()["lease_id"],
            "outcome": "verification",
            "verification": {
                "finding_id": finding_id,
                "verdict": "confirm",
                "artifact_sha256": sha256,
                "checks": {
                    "bytes_digest": "pass",
                    "deterministic_reproduction": "pass",
                    "metric_tolerance": "pass",
                    "neutrality_privacy": "pass",
                },
                "reproduced_metrics": {"re": 816.0, "be": 1000.0, "pct_difference": -18.4},
                "confidence": 0.96,
                "notes": "Independently reproduced source digest and figures.",
            },
        },
    )
    assert sub.status_code == 200, sub.text
    return sub.json()


# ---------------------------------------------------------------------------
# 1. Pure unit tests for compute_project_progress
# ---------------------------------------------------------------------------


def test_compute_project_progress_three_independent_reviews() -> None:
    assert TASK_PROGRESS_WEIGHT == 70.0
    assert REVIEW_PROGRESS_WEIGHT == 30.0

    # All tasks done, 0 of 3 reviews -> 70.0% (10% per review)
    p0 = compute_project_progress(
        total_tasks=100,
        completed_tasks=100,
        review_slots_required=30,
        review_slots_done=0,
        in_review_findings=10,
        required_reviews=3,
    )
    assert p0["progress_pct"] == 70.0
    assert p0["pct_complete"] == 70.0
    assert p0["task_progress_pct"] == 70.0
    assert p0["review_progress_pct"] == 0.0
    assert p0["review_remaining_pct"] == 30.0
    assert p0["per_review_pct"] == 10.0
    assert p0["has_reviews_remaining"] is True

    # 1 of 3 reviews complete across findings -> 80.0% (+10%)
    p1 = compute_project_progress(
        total_tasks=100,
        completed_tasks=100,
        review_slots_required=30,
        review_slots_done=10,
        in_review_findings=10,
        required_reviews=3,
    )
    assert p1["progress_pct"] == 80.0
    assert p1["pct_complete"] == 80.0
    assert p1["review_progress_pct"] == 10.0
    assert p1["review_remaining_pct"] == 20.0

    # 2 of 3 reviews complete -> 90.0% (+10%)
    p2 = compute_project_progress(
        total_tasks=100,
        completed_tasks=100,
        review_slots_required=30,
        review_slots_done=20,
        in_review_findings=10,
        required_reviews=3,
    )
    assert p2["progress_pct"] == 90.0
    assert p2["pct_complete"] == 90.0
    assert p2["review_progress_pct"] == 20.0
    assert p2["review_remaining_pct"] == 10.0

    # 3 of 3 reviews complete (all verified) -> 100.0%
    p3 = compute_project_progress(
        total_tasks=100,
        completed_tasks=100,
        review_slots_required=30,
        review_slots_done=30,
        in_review_findings=0,
        required_reviews=3,
    )
    assert p3["progress_pct"] == 100.0
    assert p3["pct_complete"] == 100.0
    assert p3["review_progress_pct"] == 30.0
    assert p3["review_remaining_pct"] == 0.0
    assert p3["has_reviews_remaining"] is False


def test_compute_project_progress_five_independent_reviews() -> None:
    expected = [
        (0, 70.0, 0.0, 30.0),
        (1, 76.0, 6.0, 24.0),
        (2, 82.0, 12.0, 18.0),
        (3, 88.0, 18.0, 12.0),
        (4, 94.0, 24.0, 6.0),
        (5, 100.0, 30.0, 0.0),
    ]
    for step, total_pct, rev_done_pct, rev_rem_pct in expected:
        res = compute_project_progress(
            total_tasks=100,
            completed_tasks=100,
            review_slots_required=50,
            review_slots_done=step * 10,
            in_review_findings=0 if step == 5 else 10,
            required_reviews=5,
        )
        assert res["per_review_pct"] == 6.0
        assert res["progress_pct"] == total_pct
        assert res["pct_complete"] == total_pct
        assert res["review_progress_pct"] == rev_done_pct
        assert res["review_remaining_pct"] == rev_rem_pct
        assert res["has_reviews_remaining"] is (step < 5)


def test_compute_project_progress_never_100_while_any_review_or_task_remains() -> None:
    # 2,973 verified + 1 in-review finding with 2/3 confirms -> 8,921 / 8,922 slots
    near_done = compute_project_progress(
        total_tasks=10_000,
        completed_tasks=10_000,
        review_slots_required=2974 * 3,
        review_slots_done=2973 * 3 + 2,
        in_review_findings=1,
        required_reviews=3,
    )
    assert near_done["progress_pct"] == 99.9
    assert near_done["pct_complete"] == 99.9
    assert near_done["has_reviews_remaining"] is True
    assert near_done["review_remaining_pct"] == 0.1

    # 9,999 / 10,000 primary tasks completed with zero reviews -> capped below 100%
    one_task_left = compute_project_progress(
        total_tasks=10_000,
        completed_tasks=9_999,
    )
    assert one_task_left["progress_pct"] == 99.9
    assert one_task_left["pct_complete"] == 99.9

    # Zero findings / zero remaining reviews (e.g. website checks or all cleared) -> 100.0%
    no_reviews = compute_project_progress(
        total_tasks=1_000,
        completed_tasks=1_000,
    )
    assert no_reviews["progress_pct"] == 100.0
    assert no_reviews["pct_complete"] == 100.0
    assert no_reviews["review_remaining_pct"] == 0.0

    # Partial primary tasks scale proportionally: 50/100 tasks with 0/3 reviews -> 35.0%
    half = compute_project_progress(
        total_tasks=100,
        completed_tasks=50,
        review_slots_required=30,
        review_slots_done=0,
        in_review_findings=10,
        required_reviews=3,
    )
    assert half["progress_pct"] == 35.0
    assert half["review_remaining_pct"] == 15.0


# ---------------------------------------------------------------------------
# 2. End-to-end Hub API + Markdown integration tests
# ---------------------------------------------------------------------------


@pytest.mark.production_missions
def test_hub_mission_progress_70_plus_10_per_review_for_3_independent_reviews(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdir = tmp_path / "projects"
    pdir.mkdir()
    base_task = _project()["tasks"][0]
    proj = _project(
        slug="review-progress-q3",
        priority=1,
        tasks=[{**base_task, "id": "rpq3-t1", "title": "Single P1 audit task", "dataset_key": "rpq3-ds"}],
    )
    (pdir / "review-progress-q3.json").write_text(json.dumps(proj), encoding="utf-8")
    monkeypatch.setenv("AIWATCHDOGS_PROJECTS_DIR", str(pdir))
    monkeypatch.delenv("AIWATCHDOGS_KEEP_LEGACY_MISSIONS", raising=False)

    app = create_app(db_path=tmp_path / "progress_q3.db", seed=True)
    with TestClient(app) as client:
        author = _register(client, "author-q3", coding_agent="Claude Code", coding_agent_model="claude-opus-4-6")
        rev1 = _register(client, "rev1-q3", coding_agent="Codex", coding_agent_model="gpt-5.4")
        rev2 = _register(client, "rev2-q3", coding_agent="Antigravity", coding_agent_model="gemini-3.1-pro")
        rev3 = _register(client, "rev3-q3", coding_agent="Cursor", coding_agent_model="sonnet-4.5")

        # Claim and complete the only primary task by submitting a finding (spawns 3 peer_verify tasks)
        lease = client.post(
            "/api/desk/next",
            headers={"X-Agent-Token": author["api_token"]},
            json={"task_id": "prj-rpq3-t1"},
        ).json()
        sha = lease["task"]["payload"]["expected_sha256"]
        finding_id = _submit_p1_finding(client, author["api_token"], lease)

        # 0 / 3 reviews -> 70.0% across list, detail, graph, and .md endpoints!
        def _snapshot() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
            listed = next(
                m for m in client.get("/api/missions").json()["missions"] if m["slug"] == "review-progress-q3"
            )
            detail = client.get("/api/missions/review-progress-q3").json()
            graph_node = next(
                n for n in client.get("/api/graph").json()["nodes"] if n["id"] == "mission:review-progress-q3"
            )
            return listed, detail, graph_node

        m0, d0, g0 = _snapshot()
        assert m0["completed_tasks"] == 1 and m0["total_tasks"] == 1
        assert m0["progress_pct"] == 70.0
        assert m0["reviews_required"] == 3 and m0["reviews_done"] == 0 and m0["reviews_remaining"] == 3
        assert m0["required_reviews"] == 3 and m0["per_review_pct"] == 10.0
        assert m0["has_reviews_remaining"] is True
        assert d0["progress_pct"] == 70.0
        assert d0["visual_summary"]["status_counts"]["pct_complete"] == 70.0
        assert d0["visual_summary"]["status_counts"]["review_remaining_pct"] == 30.0
        assert g0["meta"]["progress_pct"] == 70.0
        assert "**70.0%**" in client.get("/projects.md").text
        assert "**70.00%**" in client.get("/projects/review-progress-q3.md").text
        assert "| 70.0% |" in client.get("/index.md").text

        pv_tasks = [t["id"] for t in d0["tasks"] if t["task_type"] == "peer_verify"]
        assert len(pv_tasks) == 3

        # 1 / 3 reviews -> 80.0%
        _confirm_finding(client, rev1["api_token"], pv_tasks[0], finding_id, sha)
        m1, d1, g1 = _snapshot()
        assert m1["progress_pct"] == 80.0
        assert m1["reviews_done"] == 1 and m1["reviews_remaining"] == 2
        assert d1["visual_summary"]["status_counts"]["pct_complete"] == 80.0
        assert d1["visual_summary"]["status_counts"]["review_remaining_pct"] == 20.0
        assert g1["meta"]["progress_pct"] == 80.0

        # 2 / 3 reviews -> 90.0%
        _confirm_finding(client, rev2["api_token"], pv_tasks[1], finding_id, sha)
        m2, d2, g2 = _snapshot()
        assert m2["progress_pct"] == 90.0
        assert m2["reviews_done"] == 2 and m2["reviews_remaining"] == 1
        assert d2["visual_summary"]["status_counts"]["pct_complete"] == 90.0
        assert d2["visual_summary"]["status_counts"]["review_remaining_pct"] == 10.0
        assert g2["meta"]["progress_pct"] == 90.0

        # 3 / 3 reviews (promoted to verified) -> 100.0%
        res3 = _confirm_finding(client, rev3["api_token"], pv_tasks[2], finding_id, sha)
        assert res3["promoted_to_verified"] is True
        m3, d3, g3 = _snapshot()
        assert m3["progress_pct"] == 100.0
        assert m3["reviews_done"] == 3 and m3["reviews_remaining"] == 0
        assert m3["has_reviews_remaining"] is False
        assert d3["visual_summary"]["status_counts"]["pct_complete"] == 100.0
        assert d3["visual_summary"]["status_counts"]["review_remaining_pct"] == 0.0
        assert g3["meta"]["progress_pct"] == 100.0


@pytest.mark.production_missions
def test_hub_mission_progress_70_plus_6_per_review_for_5_independent_reviews(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdir = tmp_path / "projects"
    pdir.mkdir()
    base_task = _project()["tasks"][0]
    proj = _project(
        slug="review-progress-q5",
        priority=1,
        tasks=[{**base_task, "id": "rpq5-t1", "title": "Single P1 audit task (q5)", "dataset_key": "rpq5-ds"}],
    )
    (pdir / "review-progress-q5.json").write_text(json.dumps(proj), encoding="utf-8")
    monkeypatch.setenv("AIWATCHDOGS_PROJECTS_DIR", str(pdir))
    monkeypatch.delenv("AIWATCHDOGS_KEEP_LEGACY_MISSIONS", raising=False)

    app = create_app(db_path=tmp_path / "progress_q5.db", seed=True)
    with TestClient(app) as client:
        author = _register(client, "author-q5", coding_agent="Claude Code", coding_agent_model="claude-opus-4-6")
        lease = client.post(
            "/api/desk/next",
            headers={"X-Agent-Token": author["api_token"]},
            json={"task_id": "prj-rpq5-t1"},
        ).json()
        finding_id = _submit_p1_finding(client, author["api_token"], lease)

        # Configure this finding to require 5 independent reviews (30% / 5 = 6% each)
        for confirms, expected_pct in [(0, 70.0), (1, 76.0), (2, 82.0), (3, 88.0), (4, 94.0)]:
            with app.state.db.transaction() as conn:
                conn.execute(
                    "UPDATE findings SET required_quorum = 5, confirm_count = ?, status = 'peer_review' WHERE id = ?",
                    (confirms, finding_id),
                )
            detail = client.get("/api/missions/review-progress-q5").json()
            assert detail["required_reviews"] == 5
            assert detail["per_review_pct"] == 6.0
            assert detail["progress_pct"] == expected_pct
            assert detail["visual_summary"]["status_counts"]["pct_complete"] == expected_pct

        # Once all 5 independent reviews verify the finding -> 100.0%
        with app.state.db.transaction() as conn:
            conn.execute(
                "UPDATE findings SET required_quorum = 5, confirm_count = 5, status = 'verified' WHERE id = ?",
                (finding_id,),
            )
            conn.execute(
                "UPDATE tasks SET status = 'settled' WHERE target_finding_id = ? AND task_type = 'peer_verify'",
                (finding_id,),
            )
        done_detail = client.get("/api/missions/review-progress-q5").json()
        assert done_detail["progress_pct"] == 100.0
        assert done_detail["visual_summary"]["status_counts"]["pct_complete"] == 100.0
        assert done_detail["has_reviews_remaining"] is False


@pytest.mark.production_missions
def test_hub_mission_progress_reaches_100_when_all_tasks_cleared_without_reviews(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdir = tmp_path / "projects"
    pdir.mkdir()
    base_task = _project()["tasks"][0]
    proj = _project(
        slug="review-progress-cleared",
        priority=1,
        tasks=[{**base_task, "id": "rpc-t1", "title": "Single cleared task", "dataset_key": "rpc-ds"}],
    )
    (pdir / "review-progress-cleared.json").write_text(json.dumps(proj), encoding="utf-8")
    monkeypatch.setenv("AIWATCHDOGS_PROJECTS_DIR", str(pdir))
    monkeypatch.delenv("AIWATCHDOGS_KEEP_LEGACY_MISSIONS", raising=False)

    app = create_app(db_path=tmp_path / "progress_cleared.db", seed=True)
    with TestClient(app) as client:
        agent = _register(client, "clear-agent", coding_agent="Codex", coding_agent_model="gpt-5.4")
        lease = client.post(
            "/api/desk/next",
            headers={"X-Agent-Token": agent["api_token"]},
            json={"task_id": "prj-rpc-t1"},
        ).json()
        sub = client.post(
            "/api/desk/submit",
            headers={"X-Agent-Token": agent["api_token"]},
            json={
                "lease_id": lease["lease_id"],
                "outcome": "cleared",
                "metrics": {"records_scanned": 5, "cleared_count": 5, "monetary_audited_inr": 10_000_000.0},
            },
        )
        assert sub.status_code == 200, sub.text
        detail = client.get("/api/missions/review-progress-cleared").json()
        assert detail["progress_pct"] == 100.0
        assert detail["visual_summary"]["status_counts"]["pct_complete"] == 100.0
        assert detail["has_reviews_remaining"] is False
