"""Tests for specific-task claiming (`task_id`), CLI `--task-id`, project leaderboards, and `visual_summary`."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from aiwatchdogs.cli.config import ENV_CONFIG_PATH, AgentConfig
from aiwatchdogs.cli.main import app as cli_app
from aiwatchdogs.cli.runner import AgentRunner
from aiwatchdogs.connectors import loader
from aiwatchdogs.core.safety import compute_sha256
from aiwatchdogs.engines import gov_pages
from aiwatchdogs.hub.app import create_app
from aiwatchdogs.projects import INFLATION_PROJECT_SLUG
from test_projects import _project

PROJECTS_DIR = Path(__file__).resolve().parents[1] / "projects"
REPO_ROOT = Path(__file__).resolve().parents[1]
cli_runner = CliRunner()


def _register(
    client: TestClient,
    handle: str,
    *,
    coding_agent: str = "Claude Code",
    coding_agent_model: str = "claude-sonnet",
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


def _named_project(slug: str, priority: int, *, task_suffixes: tuple[str, ...] = ("t1",), status: str = "active") -> dict[str, Any]:
    base_task = _project()["tasks"][0]
    tasks = [
        {**base_task, "id": f"{slug}-{suf}", "title": f"Audit {slug} {suf}", "dataset_key": f"{slug}-{suf}-ds"}
        for suf in task_suffixes
    ]
    return _project(slug=slug, priority=priority, tasks=tasks, status=status)


def _sample_p1_finding(
    sha256: str,
    *,
    entity: str = "Delhi Police",
    pct: float = -18.4,
    exposure: float = 250_000_000.0,
    methodology_code: str = "P1",
) -> dict[str, Any]:
    return {
        "title": f"{entity}: revised estimate vs budget estimate differs by {pct:.1f}%",
        "summary": (
            f"Published budget figures for {entity} show a {pct:.1f}% divergence between revised "
            "and budget estimates for public review, not a finding of wrongdoing."
        ),
        "methodology_code": methodology_code,
        "entity_name": entity,
        "jurisdiction": "Union of India (Delhi Police)",
        "fiscal_year": "FY2024-25",
        "monetary_exposure_inr": exposure,
        "severity": "high",
        "anomaly_metrics": {
            "line_key": f"{entity.lower().replace(' ', '-')}-cap",
            "compare": "re_vs_be",
            "re": 816.0,
            "be": 1000.0,
            "unit": "crore",
            "pct_difference": pct,
            "threshold_pct": 10.0,
        },
        "rule_triggered": "P1-RE-VS-BE>10%",
        "source_url": "https://www.indiabudget.gov.in/budget2024-25/",
        "artifact_sha256": sha256,
        "citations": [{"source_ref": "https://www.indiabudget.gov.in/budget2024-25/doc/eb/sbe51.pdf", "page": 1}],
        "benign_explanations": ["Capital expenditure may be re-phased across financial years due to tendering timelines."],
        "suggested_authority": "Ministry of Home Affairs, Government of India",
    }


def _inflation_gap_report() -> dict[str, Any]:
    return {
        "summary": "Documented source availability for the assigned stratum.",
        "sources": [],
        "observations": [],
        "gaps": [{"reason": "historical_panel_unavailable", "detail": "Archive lookup pending additional records."}],
        "deliverable": "Documented gap report for CPI1 stratum verification.",
    }


# ---------------------------------------------------------------------------
# 1. Specific-Task Claiming (`POST /api/desk/next` with `task_id`)
# ---------------------------------------------------------------------------


@pytest.mark.production_missions
def test_specific_task_id_claiming_overrides_priority_and_enforces_all_guards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdir = tmp_path / "projects"
    pdir.mkdir()
    (pdir / "high-prio.json").write_text(
        json.dumps(_named_project("high-prio", 1, task_suffixes=("t1", "t2"))), encoding="utf-8"
    )
    (pdir / "low-prio.json").write_text(
        json.dumps(_named_project("low-prio", 9, task_suffixes=("t1", "t2", "t3"))), encoding="utf-8"
    )
    (pdir / "paused-proj.json").write_text(
        json.dumps(_named_project("paused-proj", 5, task_suffixes=("t1",), status="paused")), encoding="utf-8"
    )
    monkeypatch.setenv("AIWATCHDOGS_PROJECTS_DIR", str(pdir))
    monkeypatch.delenv("AIWATCHDOGS_KEEP_LEGACY_MISSIONS", raising=False)

    app = create_app(db_path=tmp_path / "task_pick.db", seed=True)
    with TestClient(app) as client:
        a1 = _register(client, "picker-one")
        a2 = _register(client, "picker-two")
        a3 = _register(client, "picker-three")
        h1 = {"X-Agent-Token": a1["api_token"]}
        h2 = {"X-Agent-Token": a2["api_token"]}
        h3 = {"X-Agent-Token": a3["api_token"]}

        # 1. Missing task_id -> HTTP 404
        r_404 = client.post("/api/desk/next", headers=h1, json={"task_id": "nonexistent-task-xyz"})
        assert r_404.status_code == 404
        assert "not found" in r_404.json()["detail"].lower()

        # 2. Paused project task -> HTTP 409
        r_paused = client.post("/api/desk/next", headers=h1, json={"task_id": "prj-paused-proj-t1"})
        assert r_paused.status_code == 409
        assert "paused" in r_paused.json()["detail"].lower()

        # 3. Short ID (without `prj-` prefix) overrides priority 1 and claims low-prio task!
        r_short = client.post("/api/desk/next", headers=h1, json={"task_id": "low-prio-t1"})
        assert r_short.status_code == 200, r_short.text
        lease1 = r_short.json()
        assert lease1["task"]["id"] == "prj-low-prio-t1"
        assert lease1["task"]["mission_slug"] == "low-prio"

        # GET /api/missions reflects claimed_tasks == 1 for low-prio
        missions = {m["slug"]: m for m in client.get("/api/missions").json()["missions"]}
        assert missions["low-prio"]["claimed_tasks"] == 1
        assert missions["low-prio"]["queued_tasks"] == 2
        assert missions["high-prio"]["claimed_tasks"] == 0

        # 4. Already claimed task -> HTTP 409
        r_claimed = client.post("/api/desk/next", headers=h2, json={"task_id": "prj-low-prio-t1"})
        assert r_claimed.status_code == 409
        assert "already claimed" in r_claimed.json()["detail"].lower()

        # 5. Settle low-prio-t1 with a finding so we can test completed + self-verify + duplicate-verify
        expected_sha = lease1["task"]["payload"]["expected_sha256"]
        sub = client.post(
            "/api/desk/submit",
            headers=h1,
            json={
                "lease_id": lease1["lease_id"],
                "outcome": "finding",
                "finding": _sample_p1_finding(
                    expected_sha,
                    methodology_code=lease1["task"]["methodology_code"],
                ),
            },
        )
        assert sub.status_code == 200, sub.text
        finding_id = sub.json()["finding_id"]

        # Already completed task -> HTTP 409
        r_done = client.post("/api/desk/next", headers=h2, json={"task_id": "prj-low-prio-t1"})
        assert r_done.status_code == 409
        assert "already completed" in r_done.json()["detail"].lower()

        # Find spawned peer_verify tasks for finding_id
        detail = client.get("/api/missions/low-prio").json()
        pv_tasks = [t for t in detail["tasks"] if t["task_type"] == "peer_verify" and t["target_finding_id"] == finding_id]
        assert len(pv_tasks) == 2

        # 6. Author cannot claim their own peer_verify task -> HTTP 409
        r_self = client.post("/api/desk/next", headers=h1, json={"task_id": pv_tasks[0]["id"]})
        assert r_self.status_code == 409
        assert "own finding" in r_self.json()["detail"].lower()

        # 7. Independent verifier claims first peer_verify task -> HTTP 200, cannot claim second for same finding -> HTTP 409
        r_pv1 = client.post("/api/desk/next", headers=h2, json={"task_id": pv_tasks[0]["id"]})
        assert r_pv1.status_code == 200, r_pv1.text
        r_pv_dup_lease = client.post("/api/desk/next", headers=h2, json={"task_id": pv_tasks[1]["id"]})
        assert r_pv_dup_lease.status_code == 409
        assert "active verification lease" in r_pv_dup_lease.json()["detail"].lower()

        # Submit verification by a2, then verify a2 still cannot claim pv_tasks[1] (duplicate verification)
        sub_pv = client.post(
            "/api/desk/submit",
            headers=h2,
            json={
                "lease_id": r_pv1.json()["lease_id"],
                "outcome": "verification",
                "verification": {
                    "finding_id": finding_id,
                    "verdict": "confirm",
                    "artifact_sha256": expected_sha,
                    "checks": {
                        "bytes_digest": "pass",
                        "deterministic_reproduction": "pass",
                        "metric_tolerance": "pass",
                        "neutrality_privacy": "pass",
                    },
                    "reproduced_metrics": {"re": 816.0, "be": 1000.0, "pct_difference": -18.4},
                    "confidence": 0.96,
                    "notes": "Independently reproduced figures and SHA-256.",
                },
            },
        )
        assert sub_pv.status_code == 200, sub_pv.text
        r_pv_dup = client.post("/api/desk/next", headers=h2, json={"task_id": pv_tasks[1]["id"]})
        assert r_pv_dup.status_code == 409
        assert "already verified" in r_pv_dup.json()["detail"].lower()

        # 8. Future available_at & excluded_handles_json checks -> HTTP 409
        future_iso = (datetime.now(timezone.utc) + timedelta(minutes=30)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        with app.state.db.transaction() as conn:
            conn.execute("UPDATE tasks SET available_at = ? WHERE id = 'prj-low-prio-t2'", (future_iso,))
            conn.execute(
                "UPDATE tasks SET excluded_handles_json = ? WHERE id = 'prj-low-prio-t3'",
                (json.dumps(["picker-three"]),),
            )
        assert client.post("/api/desk/next", headers=h3, json={"task_id": "prj-low-prio-t2"}).status_code == 409
        r_excl = client.post("/api/desk/next", headers=h3, json={"task_id": "prj-low-prio-t3"})
        assert r_excl.status_code == 409
        assert "excluded" in r_excl.json()["detail"].lower()

        # 9. MAX_CONCURRENT_LEASES_PER_AGENT (2) enforced with task_id -> HTTP 429
        assert client.post("/api/desk/next", headers=h1, json={"task_id": "prj-high-prio-t1"}).status_code == 200
        assert client.post("/api/desk/next", headers=h1, json={"task_id": "prj-high-prio-t2"}).status_code == 200
        r_cap = client.post("/api/desk/next", headers=h1, json={"task_id": "prj-low-prio-t3"})
        assert r_cap.status_code == 429

        # 10. Archived mission still permits claiming peer_verify by task_id, and omitting task_id still respects priority
        with app.state.db.transaction() as conn:
            conn.execute("UPDATE tasks SET available_at = NULL WHERE id = 'prj-low-prio-t2'")
            conn.execute("UPDATE missions SET status = 'archived' WHERE slug = 'low-prio'")
        assert client.post("/api/desk/next", headers=h3, json={"task_id": "prj-low-prio-t2"}).status_code == 409
        r_archived_pv = client.post("/api/desk/next", headers=h3, json={"task_id": pv_tasks[1]["id"]})
        assert r_archived_pv.status_code == 200, r_archived_pv.text
        assert r_archived_pv.json()["task"]["id"] == pv_tasks[1]["id"]


@pytest.mark.production_missions
def test_specific_task_id_w1_and_cpi1_prereq_guards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIWATCHDOGS_PROJECTS_DIR", str(PROJECTS_DIR))
    monkeypatch.delenv("AIWATCHDOGS_KEEP_LEGACY_MISSIONS", raising=False)

    app = create_app(db_path=tmp_path / "prod_guards.db", seed=True)
    with TestClient(app) as client:
        a1 = _register(client, "guard-node-1")
        a2 = _register(client, "guard-node-2")
        h1 = {"X-Agent-Token": a1["api_token"]}
        h2 = {"X-Agent-Token": a2["api_token"]}

        # W1 task without website_observations=True -> HTTP 409
        r_w1_no_flag = client.post(
            "/api/desk/next",
            headers=h1,
            json={"task_id": "prj-gov-in-public-information-0005"},
        )
        assert r_w1_no_flag.status_code == 409
        assert "website_observations" in r_w1_no_flag.json()["detail"]

        # W1 task WITH website_observations=True -> HTTP 200
        r_w1_ok = client.post(
            "/api/desk/next",
            headers=h1,
            json={"task_id": "prj-gov-in-public-information-0005", "website_observations": True},
        )
        assert r_w1_ok.status_code == 200
        assert r_w1_ok.json()["task"]["id"] == "prj-gov-in-public-information-0005"

        # CPI1 review task with unmet depends_on -> HTTP 409
        r_cpi_wait = client.post(
            "/api/desk/next",
            headers=h1,
            json={"task_id": "prj-cpi-compile-monthly-index"},
        )
        assert r_cpi_wait.status_code == 409
        assert "prerequisite" in r_cpi_wait.json()["detail"].lower()

        # CPI1 open collection task claimed directly by task_id -> HTTP 200
        r_cpi_open = client.post(
            "/api/desk/next",
            headers=h2,
            json={"task_id": "cpi-kerala-rural"},
        )
        assert r_cpi_open.status_code == 200
        assert r_cpi_open.json()["task"]["id"] == "prj-cpi-kerala-rural"

        # Simulate completing cpi-evidence-coverage-review by a2, then check single-prereq independent reviewer rule
        with app.state.db.transaction() as conn:
            conn.execute(
                "UPDATE tasks SET status = 'completed', claimed_by_agent_id = ?, claimed_by_handle = ? "
                "WHERE id = 'prj-cpi-evidence-coverage-review'",
                (a2["agent_id"], a2["handle"]),
            )
        r_same_agent = client.post(
            "/api/desk/next",
            headers=h2,
            json={"task_id": "prj-cpi-compile-monthly-index"},
        )
        assert r_same_agent.status_code == 409
        assert "independent" in r_same_agent.json()["detail"].lower()

        r_other_agent = client.post(
            "/api/desk/next",
            headers=h1,
            json={"task_id": "prj-cpi-compile-monthly-index"},
        )
        assert r_other_agent.status_code == 200
        assert r_other_agent.json()["task"]["id"] == "prj-cpi-compile-monthly-index"


# ---------------------------------------------------------------------------
# 2. CLI `--task-id` / `--task` and AgentRunner End-to-End
# ---------------------------------------------------------------------------


def test_cli_and_runner_task_id_caps_to_one_and_handles_404_409_gracefully(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_file = tmp_path / "cli_task_cfg.json"
    monkeypatch.setenv(ENV_CONFIG_PATH, str(cfg_file))

    clean_csv = tmp_path / "clean_m2.csv"
    clean_csv.write_text(
        "tender_id,buyer_name,vendor_name,item_category,award_date,award_value_inr,jurisdiction,fiscal_year\n"
        "PO-1,Clean Dept,Vendor A,Books,2024-07-01,10000,Union of India,FY2024-25\n",
        encoding="utf-8",
    )
    clean_sha = compute_sha256(clean_csv.read_bytes())

    hub_app = create_app(db_path=tmp_path / "cli_task_hub.db", seed=True)
    with hub_app.state.db.transaction() as conn:
        t_row = conn.execute(
            "SELECT id, payload_json FROM tasks WHERE mission_slug = 'assam-gfr-split-tendering' ORDER BY id ASC LIMIT 1"
        ).fetchone()
        assert t_row is not None
        target_tid = str(t_row["id"])
        p = json.loads(t_row["payload_json"])
        p["dataset_url"] = str(clean_csv)
        p["retrieval_url"] = str(clean_csv)
        p["dataset_path"] = str(clean_csv)
        p["expected_sha256"] = clean_sha
        conn.execute("UPDATE tasks SET payload_json = ? WHERE id = ?", (json.dumps(p), target_tid))

    with TestClient(hub_app, base_url="https://testserver") as asgi_client:
        orig_init = AgentRunner.__init__

        def _patched_init(self: AgentRunner, config: AgentConfig, **kwargs: Any) -> None:
            kwargs.setdefault("http_client", asgi_client)
            kwargs["allow_local_files"] = True
            orig_init(self, config, **kwargs)

        monkeypatch.setattr(AgentRunner, "__init__", _patched_init)

        # 1. `aiwatchdogs join --task-id <target_tid> --max-tasks 5 --yes` caps to 1 and runs exact task!
        res_join = cli_runner.invoke(
            cli_app,
            [
                "join",
                "--hub",
                "https://testserver",
                "--handle",
                "task-cli-node",
                "--provider",
                "rule-engine",
                "--mode",
                "auto",
                "--max-tasks",
                "5",
                "--task-id",
                target_tid,
                "--yes",
            ],
        )
        assert res_join.exit_code == 0, res_join.output
        assert target_tid in res_join.stdout
        assert "Tasks Claimed" in res_join.stdout

        with hub_app.state.db.read() as conn:
            st = conn.execute("SELECT status FROM tasks WHERE id = ?", (target_tid,)).fetchone()
            assert st is not None and st["status"] == "completed"
            completed_total = conn.execute("SELECT COUNT(*) AS c FROM tasks WHERE status = 'completed'").fetchone()["c"]
            assert int(completed_total) == 1

        # 2. `aiwatchdogs run --task <already-completed>` handles HTTP 409 cleanly with zero traceback!
        res_409 = cli_runner.invoke(cli_app, ["run", "--task", target_tid])
        assert res_409.exit_code == 0, res_409.output
        assert "Traceback" not in res_409.output
        assert "Task unavailable" in res_409.stdout
        assert "already completed" in res_409.stdout.lower()

        # 3. `aiwatchdogs run --task-id <missing>` handles HTTP 404 cleanly with zero traceback!
        res_404 = cli_runner.invoke(cli_app, ["run", "--task-id", "does-not-exist-999"])
        assert res_404.exit_code == 0, res_404.output
        assert "Traceback" not in res_404.output
        assert "Task not found" in res_404.stdout


# ---------------------------------------------------------------------------
# 3. Templates (`/join.txt`, `/join.sh`, `independent-inflation-join.txt`)
# ---------------------------------------------------------------------------


def test_join_templates_document_project_and_task_id(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "tpl.db", seed=True)
    with TestClient(app) as client:
        txt = client.get("/join.txt").text
        assert "--project" in txt
        assert "--task-id" in txt
        assert '"task_id"' in txt
        assert "demo" not in txt.lower()
        assert "sample" not in txt.lower()

    cpi_join = (REPO_ROOT / "web" / "public" / "independent-inflation-join.txt").read_text(encoding="utf-8")
    assert '"task_id"' in cpi_join
    assert "HTTP 404" in cpi_join
    assert "HTTP 409" in cpi_join


# ---------------------------------------------------------------------------
# 4. Project-Scoped Leaderboards & `visual_summary` Across P1, W1, CPI1
# ---------------------------------------------------------------------------


@pytest.mark.production_missions
def test_mission_detail_visual_summary_and_project_leaderboards_all_three_projects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AIWATCHDOGS_PROJECTS_DIR", str(PROJECTS_DIR))
    monkeypatch.delenv("AIWATCHDOGS_KEEP_LEGACY_MISSIONS", raising=False)

    app = create_app(db_path=tmp_path / "visuals.db", seed=True)
    with TestClient(app) as client:
        # ---- Baseline schema checks across all 3 real projects ----
        p1_0 = client.get("/api/missions/police-finances").json()
        assert p1_0["project_code"] == "P1"
        vs_p1 = p1_0["visual_summary"]
        assert vs_p1["project_code"] == "P1"
        assert vs_p1["methodology_code"] == "P1"
        assert vs_p1["status_counts"]["total"] == 100
        assert vs_p1["status_counts"]["queued"] == 100
        assert vs_p1["status_counts"]["waiting"] == 0
        assert vs_p1["status_counts"]["planned"] == len(p1_0["planned_tasks"])
        assert vs_p1["status_counts"]["pct_complete"] == 0.0
        p1_groups = {g["label"]: g for g in vs_p1["budget_audit"]["groups"]}
        assert set(p1_groups.keys()) == {"Union & Central Forces", "Delhi & Union Territories", "State Police"}
        assert sum(g["total"] for g in p1_groups.values()) == 100
        assert p1_groups["Union & Central Forces"]["total"] == 49
        assert p1_groups["Delhi & Union Territories"]["total"] == 17
        assert p1_groups["State Police"]["total"] == 34

        w1_0 = client.get("/api/missions/gov-in-public-information").json()
        assert w1_0["project_code"] == "P2"
        vs_w1 = w1_0["visual_summary"]
        assert vs_w1["project_code"] == "P2"
        assert vs_w1["methodology_code"] == "W1"
        wa0 = vs_w1["website_audit"]
        assert wa0["batches_total"] == 1000
        assert wa0["batches_done"] == 0
        assert wa0["total_directory_hosts"] == 12537
        assert wa0["matrix_version"] == "gigw-3.0-rti-2005-v1"
        assert wa0["matrix_summary"]["total_checkpoints"] == 159
        assert wa0["matrix_summary"]["gigw_checkpoints"] == 88
        assert wa0["matrix_summary"]["rti_checkpoints"] == 42
        assert wa0["matrix_summary"]["statutory_other_checkpoints"] == 29
        assert wa0["matrix_summary"]["w1_automated_checks_count"] == 46
        assert wa0["matrix_summary"]["automated_http_html"] == 48
        assert wa0["matrix_summary"]["agent_inspection"] == 96
        assert wa0["matrix_summary"]["internal_governance"] == 15
        assert len(wa0["sections"]) == 8
        assert len(wa0["checkpoints"]) == 46
        assert len(wa0["matrix"]) == 159
        assert set(wa0["reasons"].keys()) == {
            "robots_restricted",
            "fetch_failed",
            "unsupported_page",
            "contact_unavailable",
            "rti_unavailable",
        }

        cpi_0 = client.get(f"/api/missions/{INFLATION_PROJECT_SLUG}").json()
        assert cpi_0["project_code"] == "P3"
        vs_cpi = cpi_0["visual_summary"]
        assert vs_cpi["project_code"] == "P3"
        assert vs_cpi["methodology_code"] == "CPI1"
        assert vs_cpi["status_counts"]["queued"] == 96
        assert vs_cpi["status_counts"]["waiting"] == 4
        cpi_roles = {r["role"]: r for r in vs_cpi["inflation"]["roles"]}
        assert cpi_roles["collection"]["total"] == 71
        assert cpi_roles["method"]["total"] == 13
        assert cpi_roles["specialist"]["total"] == 12
        assert cpi_roles["review"]["total"] == 4
        strata_by_code = {s["state_code"]: s for s in vs_cpi["inflation"]["strata"]}
        assert len(strata_by_code) == 36
        assert strata_by_code["04"]["state_name"] == "Chandigarh"
        assert strata_by_code["04"]["rural_status"] is None
        assert strata_by_code["04"]["urban_status"] == "queued"
        assert strata_by_code["04"]["urban_task_id"] == "prj-cpi-chandigarh-urban"
        assert strata_by_code["32"]["state_name"] == "Kerala"
        assert strata_by_code["32"]["rural_task_id"] == "prj-cpi-kerala-rural"
        assert strata_by_code["32"]["urban_task_id"] == "prj-cpi-kerala-urban"

        # ---- Populate real P1 activity (2 contributors + 1 finding + 1 cleared + 1 verification) ----
        p1_a = _register(client, "p1-auditor-alpha", coding_agent="Antigravity", coding_agent_model="Gemini 3")
        p1_b = _register(client, "p1-verifier-beta", coding_agent="Claude Code", coding_agent_model="claude-opus")
        w1_a = _register(client, "w1-observer-one", coding_agent="Codex", coding_agent_model="gpt-5")
        cpi_a = _register(client, "cpi-researcher-one", coding_agent="Cursor", coding_agent_model="sonnet-4")

        l_p1_1 = client.post(
            "/api/desk/next",
            headers={"X-Agent-Token": p1_a["api_token"]},
            json={"task_id": "prj-metro-police-delhi-notes-2025-26"},
        ).json()
        sha_p1_1 = l_p1_1["task"]["payload"]["expected_sha256"]
        sub_p1_1 = client.post(
            "/api/desk/submit",
            headers={"X-Agent-Token": p1_a["api_token"]},
            json={
                "lease_id": l_p1_1["lease_id"],
                "outcome": "finding",
                "finding": _sample_p1_finding(sha_p1_1, entity="Delhi Police", pct=-22.5, exposure=400_000_000.0),
            },
        )
        assert sub_p1_1.status_code == 200, sub_p1_1.text
        f1_id = sub_p1_1.json()["finding_id"]

        l_p1_2 = client.post(
            "/api/desk/next",
            headers={"X-Agent-Token": p1_b["api_token"]},
            json={"task_id": "prj-police-union-crpf-2026-27"},
        ).json()
        sub_p1_2 = client.post(
            "/api/desk/submit",
            headers={"X-Agent-Token": p1_b["api_token"]},
            json={
                "lease_id": l_p1_2["lease_id"],
                "outcome": "cleared",
                "metrics": {"records_scanned": 12, "cleared_count": 12, "monetary_audited_inr": 100_000_000.0},
            },
        )
        assert sub_p1_2.status_code == 200, sub_p1_2.text

        # Backdate p1_b's score_event and task completion to 2 hours ago to test contributors_hour filtering
        old_iso = (datetime.now(timezone.utc) - timedelta(hours=2)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        with app.state.db.transaction() as conn:
            conn.execute(
                "UPDATE tasks SET completed_at = ?, updated_at = ? WHERE id = 'prj-police-union-crpf-2026-27'",
                (old_iso, old_iso),
            )
            conn.execute(
                "UPDATE score_events SET created_at = ? WHERE agent_handle = 'p1-verifier-beta'",
                (old_iso,),
            )

        # ---- Populate real W1 batch observations ----
        l_w1 = client.post(
            "/api/desk/next",
            headers={"X-Agent-Token": w1_a["api_token"]},
            json={"task_id": "prj-gov-in-public-information-0001", "website_observations": True},
        ).json()
        w1_runner = AgentRunner(
            AgentConfig(handle=w1_a["handle"], hub_url="http://testserver", api_token=w1_a["api_token"]),
            http_client=client,
            config_path=tmp_path / "w1_agent.json",
        )
        retrieval_url = l_w1["task"]["payload"]["retrieval_url"]
        batch_file = PROJECTS_DIR / "gov-in-public-information" / Path(urlsplit(retrieval_url).path).name
        with batch_file.open(newline="", encoding="utf-8") as fh:
            assignments = list(csv.DictReader(fh))
        unreachable_host = assignments[0]["domain"]

        def _fetch_manifest(url: str, **_kwargs: Any):
            return batch_file.read_bytes(), "text/csv", "2026-11-25T00:00:00Z", False

        async def _fake_page(reader: Any, url: str):
            if reader.domain == unreachable_host:
                raise gov_pages.Unavailable("robots_restricted")
            if urlsplit(url).path == "/contact":
                html = "<p>Public enquiries: help [at] example [dot] gov [dot] in. Phone: 011-23456789.</p>"
            else:
                html = (
                    "<p>Official department portal. Last Updated on 2026-11-20.</p>"
                    "<a href='/contact'>Contact us</a><a href='/about'>About</a>"
                )
            return url, gov_pages.Page(html)

        monkeypatch.setattr(loader, "_fetch_remote_bytes", _fetch_manifest)
        monkeypatch.setattr(gov_pages.Reader, "page", _fake_page)
        w1_res = w1_runner.execute_task(l_w1)
        assert w1_res["status"] == "completed", w1_res

        # ---- Populate real CPI1 research report ----
        l_cpi = client.post(
            "/api/desk/next",
            headers={"X-Agent-Token": cpi_a["api_token"]},
            json={"task_id": "prj-cpi-kerala-rural"},
        ).json()
        sub_cpi = client.post(
            "/api/desk/submit",
            headers={"X-Agent-Token": cpi_a["api_token"]},
            json={
                "lease_id": l_cpi["lease_id"],
                "outcome": "research",
                "research": _inflation_gap_report(),
            },
        )
        assert sub_cpi.status_code == 200, sub_cpi.text

        # ---- Assert P1 detail: scoped leaderboard, hourly filter, divergences, groups ----
        p1_after = client.get("/api/missions/police-finances").json()
        # Even after a finding spawns 2 dynamic peer_verify tasks, primary total_tasks MUST stay 100!
        assert p1_after["total_tasks"] == 100
        assert p1_after["completed_tasks"] == 2
        assert p1_after["peer_checks_total"] == 2
        assert p1_after["peer_checks_done"] == 0
        assert p1_after["peer_checks_open"] == 2
        assert p1_after["visual_summary"]["status_counts"]["total"] == 100
        assert p1_after["visual_summary"]["status_counts"]["completed"] == 2
        assert p1_after["visual_summary"]["status_counts"]["checks_total"] == 2
        p1_all_handles = [c["handle"] for c in p1_after["contributors_all"]]
        p1_hr_handles = [c["handle"] for c in p1_after["contributors_hour"]]
        assert set(p1_all_handles) == {"p1-auditor-alpha", "p1-verifier-beta"}
        assert "w1-observer-one" not in p1_all_handles
        assert "cpi-researcher-one" not in p1_all_handles
        assert p1_hr_handles == ["p1-auditor-alpha"]
        alpha_row = p1_after["contributors_all"][0]
        assert alpha_row["score"] == 10
        assert alpha_row["coding_agent"] == "Antigravity"
        assert alpha_row["profile_url"] == "/a/p1-auditor-alpha"

        ba = p1_after["visual_summary"]["budget_audit"]
        assert ba["peer_review_count"] == 1
        assert ba["cleared_count"] == 1
        assert ba["total_exposure_inr_cr"] == 40.0
        assert len(ba["top_divergences"]) == 1
        assert ba["top_divergences"][0]["finding_id"] == f1_id
        assert ba["top_divergences"][0]["pct_difference"] == -22.5
        p1_groups_after = {g["label"]: g for g in ba["groups"]}
        assert p1_groups_after["Delhi & Union Territories"]["done"] == 1
        assert p1_groups_after["Union & Central Forces"]["done"] == 1
        assert p1_groups_after["State Police"]["done"] == 0

        # ---- Assert W1 detail: aggregated checkpoints, reasons, project-scoped leaderboard ----
        w1_after = client.get("/api/missions/gov-in-public-information").json()
        assert [c["handle"] for c in w1_after["contributors_all"]] == ["w1-observer-one"]
        assert w1_after["contributors_all"][0]["score"] == 1
        assert w1_after["contributors_all"][0]["tasks_completed"] == 1
        assert [c["handle"] for c in w1_after["contributors_hour"]] == ["w1-observer-one"]

        wa = w1_after["visual_summary"]["website_audit"]
        assert wa["batches_done"] == 1
        assert wa["batches_total"] == 1000
        assert wa["hosts_total"] == len(assignments)
        assert wa["hosts_unassessable"] == 1
        assert wa["hosts_observed"] == len(assignments) - 1
        assert wa["reasons"]["robots_restricted"] == 1
        cp_by_key = {cp["key"]: cp for cp in wa["checkpoints"]}
        assert cp_by_key["contact_link"]["observed"] == len(assignments) - 1
        assert cp_by_key["email"]["observed"] == len(assignments) - 1
        assert cp_by_key["gov_email_domain"]["observed"] == len(assignments) - 1
        assert cp_by_key["telephone"]["observed"] == len(assignments) - 1
        assert cp_by_key["postal_address"]["not_observed"] == len(assignments) - 1
        assert cp_by_key["updated_date"]["observed"] == len(assignments) - 1
        assert cp_by_key["contact_link"]["unassessable"] == 1
        assert len(wa["matrix"]) == 159
        q10 = next(row for row in wa["matrix"] if row["id"] == "GIGW-Q-10")
        assert q10["observed_pct"] is not None and q10["observed_pct"] > 0

        # ---- Assert CPI1 detail: strata status, roles, batches_submitted, leaderboard ----
        cpi_after = client.get(f"/api/missions/{INFLATION_PROJECT_SLUG}").json()
        assert [c["handle"] for c in cpi_after["contributors_all"]] == ["cpi-researcher-one"]
        assert cpi_after["contributors_all"][0]["score"] == 1
        assert cpi_after["contributors_all"][0]["tasks_completed"] == 1
        inf = cpi_after["visual_summary"]["inflation"]
        assert inf["batches_submitted"] == 1
        inf_roles = {r["role"]: r for r in inf["roles"]}
        assert inf_roles["collection"]["done"] == 1
        inf_strata = {s["state_code"]: s for s in inf["strata"]}
        assert inf_strata["32"]["rural_status"] == "completed"
        assert inf_strata["32"]["urban_status"] == "queued"


def test_matrix_rollup_enforces_direct_checks_and_per_host_applicability() -> None:
    """P1 regressions:
    1. Non-automated (agent_inspection & internal_governance) checkpoints never receive observed_pct.
    2. STQC-CQW-01 & OPENSTD-01 use their own direct checks, never ownership_statement or viewport.
    3. OLA-3-3-web scopes strictly to Union/Central hosts (excluding State portals).
    4. NDSAP-01 scopes strictly to Ministries/Departments; RTI checkpoints exclude Sec 24 exempt hosts.
    """
    from aiwatchdogs.engines.gigw_matrix import W1_CHECKS, build_website_audit_visual_payload, load_conformity_matrix

    # Static invariant on conformity-matrix.json: zero proxy keys on non-automated checkpoints
    mat_doc = load_conformity_matrix()
    for cp in mat_doc["checkpoints"]:
        if cp["verification_mode"] != "automated_http_html":
            assert cp.get("w1_check_keys") == [], f"Proxy w1_check_keys leaked on {cp['id']}"

    def _obs(domain: str, overrides: dict[str, str]) -> dict[str, Any]:
        checks = dict.fromkeys(W1_CHECKS, "not_observed")
        checks.update(overrides)
        return {"domain": domain, "outcome": "observed", "reason": None, "checks": checks}

    # Case 1: Only a State portal (rajasthan.gov.in) audited:
    # - ownership_statement & responsive_viewport are observed, but STQC badge & no_legacy_plugins are NOT
    # - hindi_english_bilingual is observed on the State portal, but OLA-3-3-web only applies to Union bodies!
    state_only = _obs(
        "rajasthan.gov.in",
        {
            "ownership_statement": "observed",
            "responsive_viewport": "observed",
            "stqc_cqw_badge": "not_observed",
            "no_legacy_plugins": "not_observed",
            "hindi_english_bilingual": "observed",
            "open_data_link": "observed",
            "rti_link": "observed",
        },
    )
    payload_state = build_website_audit_visual_payload(
        batches_done=1,
        batches_total=1000,
        hosts_observed=1,
        hosts_unassessable=0,
        cp_counts={},
        reasons={},
        host_observations=[state_only],
    )
    by_id_state = {row["id"]: row for row in payload_state["matrix"]}
    assert by_id_state["STQC-CQW-01"]["observed_pct"] == 0.0
    assert by_id_state["OPENSTD-01"]["observed_pct"] == 0.0
    # OLA-3-3-web MUST stay pending (None) when only a State portal was observed!
    assert by_id_state["OLA-3-3-web"]["applicable_hosts_assessed"] == 0
    assert by_id_state["OLA-3-3-web"]["observed_pct"] is None
    # Every agent_inspection and internal_governance checkpoint MUST remain None!
    for row in payload_state["matrix"]:
        if row["verification_mode"] != "automated_http_html":
            assert row["observed_pct"] is None, f"{row['id']} ({row['verification_mode']}) leaked observed_pct"

    # Case 2: Mix of State portal (rajasthan.gov.in), Union portal (gst.gov.in),
    # Union Department (dord.gov.in), and RTI Sec 24 exempt agency (bsf.gov.in).
    union_portal = _obs(
        "gst.gov.in",
        {
            "hindi_english_bilingual": "not_observed",
            "open_data_link": "observed",
            "rti_link": "observed",
        },
    )
    union_dept = _obs(
        "dord.gov.in",
        {
            "hindi_english_bilingual": "observed",
            "open_data_link": "not_observed",
            "rti_link": "not_observed",
        },
    )
    exempt_agency = _obs(
        "bsf.gov.in",
        {
            "hindi_english_bilingual": "observed",
            "rti_link": "not_applicable",
            "pio_faa_disclosure": "not_applicable",
        },
    )
    payload_mix = build_website_audit_visual_payload(
        batches_done=1,
        batches_total=1000,
        hosts_observed=4,
        hosts_unassessable=0,
        cp_counts={},
        reasons={},
        host_observations=[state_only, union_portal, union_dept, exempt_agency],
    )
    by_id_mix = {row["id"]: row for row in payload_mix["matrix"]}
    # OLA-3-3-web: Union hosts only -> gst.gov.in (0), dord.gov.in (1), bsf.gov.in (1) = 2/3 = 66.67% (rajasthan excluded!)
    assert by_id_mix["OLA-3-3-web"]["applicable_hosts_assessed"] == 3
    assert by_id_mix["OLA-3-3-web"]["observed_pct"] == 66.67
    # NDSAP-01: ministries_and_departments -> rajasthan.gov.in (department, 1) + dord.gov.in (department, 0) = 1/2 = 50.0%
    # (gst.gov.in is kind='portal', bsf.gov.in is kind='agency', both excluded!)
    assert by_id_mix["NDSAP-01"]["applicable_hosts_assessed"] == 2
    assert by_id_mix["NDSAP-01"]["observed_pct"] == 50.0
    # RTI-4-2: all_public_authorities_non_exempt -> excludes bsf.gov.in -> rajasthan(1), gst(1), dord(0) = 2/3 = 66.67%
    assert by_id_mix["RTI-4-2"]["applicable_hosts_assessed"] == 3
    assert by_id_mix["RTI-4-2"]["observed_pct"] == 66.67

    # Per-checkpoint denominator: when download_metadata is not_applicable on all hosts,
    # GIGW-Q-06 / OPENSTD-02 must report BOTH observed_pct=None AND applicable_hosts_assessed=0!
    no_downloads = [
        _obs("gst.gov.in", {"download_metadata": "not_applicable", "ownership_statement": "observed"}),
        _obs("dord.gov.in", {"download_metadata": "not_applicable", "ownership_statement": "observed"}),
    ]
    payload_na = build_website_audit_visual_payload(
        batches_done=1,
        batches_total=1000,
        hosts_observed=2,
        hosts_unassessable=0,
        cp_counts={},
        reasons={},
        host_observations=no_downloads,
    )
    by_id_na = {row["id"]: row for row in payload_na["matrix"]}
    assert by_id_na["GIGW-Q-06"]["observed_pct"] is None
    assert by_id_na["GIGW-Q-06"]["applicable_hosts_assessed"] == 0
    assert by_id_na["OPENSTD-02"]["observed_pct"] is None
    assert by_id_na["OPENSTD-02"]["applicable_hosts_assessed"] == 0
    assert by_id_na["GIGW-Q-02"]["observed_pct"] == 100.0
    assert by_id_na["GIGW-Q-02"]["applicable_hosts_assessed"] == 2

    # Offline/unassessable Union host must NOT drag OLA-3-3-web down to 0% when no Union host was assessable
    offline_union = {
        "domain": "uidai.gov.in",
        "outcome": "unassessable",
        "reason": "fetch_failed",
        "checks": dict.fromkeys(W1_CHECKS, "unassessable"),
    }
    payload_offline = build_website_audit_visual_payload(
        batches_done=1,
        batches_total=1000,
        hosts_observed=0,
        hosts_unassessable=1,
        cp_counts={},
        reasons={"fetch_failed": 1},
        host_observations=[offline_union],
    )
    ola_offline = next(r for r in payload_offline["matrix"] if r["id"] == "OLA-3-3-web")
    assert ola_offline["applicable_hosts_assessed"] == 0
    assert ola_offline["observed_pct"] is None
