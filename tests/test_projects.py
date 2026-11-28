"""Community projects: file validation, hub sync, and archiving of the original missions."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from aiwatchdogs.cli.main import app as cli_app
from aiwatchdogs.hub.app import create_app
from aiwatchdogs.projects import (
    UNION_BUDGET_METHODOLOGY,
    UNION_BUDGET_PROJECT_SLUG,
    load_project_files,
    validate_project,
)
from conftest import union_budget_project

REPO_PROJECTS = Path(__file__).resolve().parents[1] / "projects"
SHA = "a" * 64
COMMIT = "0123456789abcdef0123456789abcdef01234567"


def _project(**overrides: object) -> dict:
    base = {
        "slug": "test-city-budget",
        "title": "Test city police budget audit",
        "summary": "Checks published police budget heads against revised estimates and actuals.",
        "posted_by": {"name": "Test Collective", "handle": "@testcollective", "url": "https://example.org"},
        "pillar": "budget",
        "status": "active",
        "jurisdiction": "State:Test",
        "authority": "Home Department, Government of Test",
        "scope": ["Budget vs actual variance on police heads"],
        "out_of_scope": ["Private individuals"],
        "statutory_anchors": [],
        "sources": [{"name": "Test budget", "publisher": "Finance Dept", "url": "https://finance.test.gov.in"}],
        "methodology_code": "M5",
        "tasks": [
            {
                "id": "test-city-budget-fy24",
                "title": "Police heads FY 2023-24",
                "instructions": "Fetch the pinned file, verify the checksum, run the deterministic check.",
                "dataset_key": "test-city-budget-fy24",
                "estimated_tokens": 300_000,
                "estimate_basis": "6 turns at 50,000 tokens",
                "financial_quantity_type": "budget_and_treasury_expenditure",
                "payload": {
                    "retrieval_url": f"https://raw.githubusercontent.com/org/repo/{COMMIT}/data/budget.csv",
                    "expected_sha256": SHA,
                    "official_publisher": "Finance Department, Government of Test",
                    "source_landing_page": "https://finance.test.gov.in",
                    "jurisdiction": "State:Test",
                    "fiscal_year": "FY2023-24",
                },
            }
        ],
    }
    base.update(overrides)
    return base


def test_valid_project_passes() -> None:
    assert validate_project(_project(), filename_stem="test-city-budget") == []


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"posted_by": {"name": ""}}, "posted_by.name"),
        ({"slug": "Bad Slug"}, "slug"),
        ({"sources": []}, "sources"),
        ({"pillar": "gossip"}, "pillar"),
        ({"title": "Police scam exposed in the city"}, "accusatory term"),
        ({"tasks": [], "status": "active"}, "needs_data"),
    ],
)
def test_invalid_projects_are_rejected(overrides: dict, expected: str) -> None:
    errors = validate_project(_project(**overrides), filename_stem=str(overrides.get("slug", "test-city-budget")))
    assert any(expected in e for e in errors), errors


def test_branch_urls_and_bad_hashes_are_rejected() -> None:
    proj = _project()
    proj["tasks"][0]["payload"]["retrieval_url"] = "https://raw.githubusercontent.com/org/repo/main/data/budget.csv"
    proj["tasks"][0]["payload"]["expected_sha256"] = "abc"
    errors = validate_project(proj, filename_stem="test-city-budget")
    assert any("40-character commit SHA" in e for e in errors)
    assert any("SHA-256" in e for e in errors)


def test_project_can_list_as_many_tasks_as_the_audit_needs() -> None:
    task = _project()["tasks"][0]
    tasks = [{**task, "id": f"test-city-budget-{i}"} for i in range(250)]
    assert validate_project(_project(tasks=tasks), filename_stem="test-city-budget") == []
    planned = [{**PLANNED, "title": f"Find the published budget books, part {i}"} for i in range(40)]
    assert validate_project(_project(tasks=tasks, planned_tasks=planned), filename_stem="test-city-budget") == []
    # Ids must still be unique however long the list is.
    errors = validate_project(_project(tasks=[*tasks, tasks[0]]), filename_stem="test-city-budget")
    assert any("used twice" in e for e in errors)


def test_filename_must_match_slug() -> None:
    errors = validate_project(_project(), filename_stem="something-else")
    assert any("File name must match" in e for e in errors)


PLANNED = {
    "title": "Find the published budget books",
    "type": "find_sources",
    "estimated_tokens": 2_000_000,
    "estimate_basis": "rough guess",
}


def test_needs_data_project_without_tasks_is_valid() -> None:
    proj = _project(status="needs_data", tasks=[], planned_tasks=[PLANNED])
    assert validate_project(proj, filename_stem="test-city-budget") == []


def test_repository_project_files_are_valid() -> None:
    files = load_project_files(REPO_PROJECTS)
    assert files, "projects/ should contain at least one project"
    for pf in files:
        assert pf.errors == [], f"{pf.path.name}: {pf.errors}"


def test_task_cap_dependencies_and_union_budget_context_rules() -> None:
    # Any project may list up to 20,000 tasks ('tasks' and 'planned_tasks' together), and no more.
    task = _project()["tasks"][0]
    tasks = [{**task, "id": f"test-city-budget-{i}"} for i in range(20_000)]
    assert validate_project(_project(tasks=tasks), filename_stem="test-city-budget") == []
    over = validate_project(_project(tasks=tasks, planned_tasks=[PLANNED]), filename_stem="test-city-budget")
    assert any("at most 20,000 tasks" in e for e in over), over[:3]

    # Every task must stand on its own: depends_on is always an error now.
    dependent = _project()
    dependent["tasks"][0]["depends_on"] = []
    assert any("depends_on is not supported" in e for e in validate_project(dependent, filename_stem="test-city-budget"))

    # The synthetic UB1 project is valid; breaking its union_budget context is not.
    ub = union_budget_project()
    assert validate_project(ub, filename_stem=UNION_BUDGET_PROJECT_SLUG) == []
    broken = copy.deepcopy(ub)
    broken["tasks"][0]["payload"]["context"]["union_budget"]["estimates_document"]["budget"] = "2019-20"
    errors = validate_project(broken, filename_stem=UNION_BUDGET_PROJECT_SLUG)
    assert any("estimates_document.budget must be the budget after 2019-20" in e for e in errors), errors
    broken = copy.deepcopy(ub)
    del broken["tasks"][1]["payload"]["context"]["union_budget"]
    errors = validate_project(broken, filename_stem=UNION_BUDGET_PROJECT_SLUG)
    assert any("union_budget is required for UB1 tasks" in e for e in errors), errors
    broken = copy.deepcopy(ub)
    broken["tasks"][1]["payload"]["context"]["union_budget"]["lines"] = []
    errors = validate_project(broken, filename_stem=UNION_BUDGET_PROJECT_SLUG)
    assert any(".lines must list 1 to" in e for e in errors), errors
    # A UB1 task that pins the actuals statement must read it page by page, and vice versa.
    broken = copy.deepcopy(ub)
    del broken["tasks"][0]["payload"]["context"]["secondary_document"]
    errors = validate_project(broken, filename_stem=UNION_BUDGET_PROJECT_SLUG)
    assert any("secondary_url and context.secondary_document 'pages' together" in e for e in errors), errors

    # Reading a second document is a UB1-only control.
    other = _project()
    other["tasks"][0]["payload"]["context"] = {"secondary_document": "pages"}
    errors = validate_project(other, filename_stem="test-city-budget")
    assert any("secondary_document is only 'pages', and only for UB1" in e for e in errors), errors


@pytest.mark.production_missions
def test_research_outcome_is_rejected_and_union_budget_can_be_chosen_without_project_choice(
    tmp_path: Path, union_budget_projects: Path,
) -> None:
    # A priority-1 project sorts ahead of P3 (priority 5) for a general claim.
    (union_budget_projects / "test-city-budget.json").write_text(json.dumps(_project(priority=1)))
    with TestClient(create_app(db_path=tmp_path / "ub-choice.db", seed=True)) as client:
        def register(handle: str) -> dict[str, str]:
            response = client.post("/api/agents/register", json={"handle": handle})
            assert response.is_success, response.text
            return {"X-Agent-Token": response.json()["api_token"]}

        general_agent = register("general-agent")
        ub_agent = register("ub-agent")
        general = client.post("/api/desk/next", headers=general_agent, json={"mode": "auto"})
        assert general.status_code == 200, general.text
        assert general.json()["task"]["mission_slug"] == "test-city-budget"
        # The research flow is gone: the API model refuses the outcome and its fields.
        invalid = client.post("/api/desk/submit", headers=general_agent, json={
            "lease_id": general.json()["lease_id"], "outcome": "research", "research": {"summary": "x"},
        })
        assert invalid.status_code == 422, invalid.text

        # P3 is honoured as a preferred project even though AIWATCHDOGS_ALLOW_PROJECT_CHOICE is unset.
        lease = client.post(
            "/api/desk/next", headers=ub_agent,
            json={"preferred_mission": UNION_BUDGET_PROJECT_SLUG, "mode": "investigate"},
        )
        assert lease.status_code == 200, lease.text
        task = lease.json()["task"]
        assert task["mission_slug"] == UNION_BUDGET_PROJECT_SLUG
        assert task["methodology_code"] == UNION_BUDGET_METHODOLOGY
        assert task["task_type"] == "anomaly_scan"
        assert task["id"].startswith("prj-ub-")
        assert task["payload"]["context"]["union_budget"]["financial_year"] in {"2019-20", "2020-21"}


def test_cli_validate_reports_problems(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    good = tmp_path / "test-city-budget.json"
    good.write_text(json.dumps(_project()))
    bad = tmp_path / "bad-one.json"
    bad.write_text(json.dumps(_project(slug="bad-one", posted_by={"name": ""}, tasks=[], status="needs_data")))
    monkeypatch.setenv("AIWATCHDOGS_PROJECTS_DIR", str(tmp_path))
    runner = CliRunner()
    ok = runner.invoke(cli_app, ["project", "validate", str(good)])
    assert ok.exit_code == 0, ok.output
    fail = runner.invoke(cli_app, ["project", "validate", str(bad)])
    assert fail.exit_code == 1
    assert "posted_by.name" in fail.output


@pytest.mark.production_missions
def test_hub_lists_projects_archives_legacy_missions_and_queues_project_tasks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdir = tmp_path / "projects"
    pdir.mkdir()
    (pdir / "test-city-budget.json").write_text(json.dumps(_project()))
    (pdir / "_template.json").write_text("{}")  # templates are ignored
    monkeypatch.setenv("AIWATCHDOGS_PROJECTS_DIR", str(pdir))
    monkeypatch.delenv("AIWATCHDOGS_KEEP_LEGACY_MISSIONS", raising=False)

    app = create_app(db_path=tmp_path / "hub.db", seed=True)
    with TestClient(app) as client:
        body = client.get("/api/missions").json()
        slugs = [m["slug"] for m in body["missions"]]
        assert slugs == ["test-city-budget"]
        project = body["missions"][0]
        assert project["is_community_project"] is True
        assert project["posted_by"]["name"] == "Test Collective"
        assert project["total_tasks"] == 1
        assert project["project_sources"][0]["url"] == "https://finance.test.gov.in"

        # The original missions are gone: not listed, not reachable, nothing of theirs is served.
        assert client.get("/api/missions/assam-ocds-single-bid").status_code == 404
        assert client.get("/api/sources").json()["sources"] == []
        assert client.get("/api/findings").json()["findings"] == []

        # Only the project's task is offered to a new agent; legacy scans are not.
        reg = client.post(
            "/api/agents/register",
            json={"handle": "proj-tester", "provider": "rule-engine", "model": "deterministic-python-v1"},
        )
        assert reg.status_code in (200, 201), reg.text
        token = reg.json()["api_token"]
        claim = client.post(
            "/api/desk/next",
            headers={"X-Agent-Token": token},
            json={"handle": "proj-tester", "mode": "investigate"},
        )
        assert claim.status_code == 200, claim.text
        task = claim.json().get("task", claim.json())
        assert task["mission_slug"] == "test-city-budget"
        assert task["id"] == "prj-test-city-budget-fy24"


@pytest.mark.production_missions
def test_removed_project_file_archives_the_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pdir = tmp_path / "projects"
    pdir.mkdir()
    (pdir / "test-city-budget.json").write_text(json.dumps(_project()))
    second = _project()
    second["slug"] = "test-state-budget"
    second["title"] = "Test state budget"
    second["tasks"][0]["id"] = "prj-test-state-budget-fy24"
    (pdir / "test-state-budget.json").write_text(json.dumps(second))
    monkeypatch.setenv("AIWATCHDOGS_PROJECTS_DIR", str(pdir))
    monkeypatch.delenv("AIWATCHDOGS_KEEP_LEGACY_MISSIONS", raising=False)
    db_path = tmp_path / "hub.db"

    with TestClient(create_app(db_path=db_path, seed=True)) as client:
        assert sorted(m["slug"] for m in client.get("/api/missions").json()["missions"]) == [
            "test-city-budget",
            "test-state-budget",
        ]

    (pdir / "test-city-budget.json").unlink()
    with TestClient(create_app(db_path=db_path, seed=True)) as client:
        assert [m["slug"] for m in client.get("/api/missions").json()["missions"]] == ["test-state-budget"]
        assert client.get("/api/missions/test-city-budget").json()["status"] == "archived"

    # Removing the final project fails closed (does not wipe active projects on empty dir unless explicit)
    (pdir / "test-state-budget.json").unlink()
    with TestClient(create_app(db_path=db_path, seed=True)) as client:
        assert [m["slug"] for m in client.get("/api/missions").json()["missions"]] == ["test-state-budget"]


# ---------------------------------------------------------------------------
# P1 police budget-head engine
# ---------------------------------------------------------------------------

from aiwatchdogs.core.safety import scrub_pii  # noqa: E402
from aiwatchdogs.engines.registry import run_engine  # noqa: E402

_NOTES_PAGE = (
    "MINISTRY OF HOME AFFAIRS\nDEMAND NO. 51\n"
    " 5. Delhi Police  11123.14 991.56 12114.70 10291.28 889.05 11180.33 11020.25 447.37 11467.62 "
    "11315.63 616.03 11931.66 \n  13.02 Delhi Police  ... 111.18 111.18 ... 220.48 220.48\n"
)
_TS_PAGE = "Establishment\n త  Total M.H. 108 1670,92.50 1876,13.28 1876,13.28 1895,85.25\n త  Total 2055 1670,92.50 1876,13.28 1876,13.28 1895,85.25\n"


def _p1_ctx(lines: list[dict], comparisons: list[dict] | None = None, **extra: object) -> dict:
    ctx = {"budget_lines": lines, "retrieval_url": "https://example.gov.in/doc.pdf", "expected_sha256": SHA}
    if comparisons is not None:
        ctx["comparisons"] = comparisons
    ctx.update(extra)
    return ctx


def test_p1_reads_printed_line_and_flags_capital_revision() -> None:
    ctx = _p1_ctx(
        [
            {
                "key": "capital",
                "entity": "Delhi Police",
                "label": "line 5 capital",
                "unit": "crore",
                "parts": [{"page": 1, "pattern": r"^\s*5\.\s*Delhi Police\s+\d", "values": {"actual": 1, "be": 4, "re": 7, "be_next": 10}}],
            },
            {
                "key": "total",
                "entity": "Delhi Police",
                "label": "line 5 total",
                "unit": "crore",
                "parts": [{"page": 1, "pattern": r"^\s*5\.\s*Delhi Police\s+\d", "values": {"actual": 2, "be": 5, "re": 8, "be_next": 11}}],
            },
        ],
        [{"a": "re", "b": "be", "label": "revised vs budget", "threshold_pct": 10}],
    )
    res = run_engine("P1", [{"page": 1, "raw_text": _NOTES_PAGE}], ctx)
    assert len(res.leads) == 1  # capital revised 889.05 -> 447.37; total within 10%
    lead = res.leads[0]
    assert lead.anomaly_metrics["be"] == 889.05 and lead.anomaly_metrics["re"] == 447.37
    assert round(lead.anomaly_metrics["pct_difference"], 1) == -49.7
    assert lead.monetary_exposure_inr == pytest.approx((889.05 - 447.37) * 1e7)
    assert lead.citations[0]["page"] == 1 and "Delhi Police" in lead.citations[0]["printed_line"]
    assert res.cleared_count == 1


def test_p1_indian_grouping_lakh_units_and_re_equals_be_flag() -> None:
    ctx = _p1_ctx(
        [
            {
                "key": "est",
                "entity": "Hyderabad City Police",
                "label": "establishment MH 2055",
                "unit": "lakh",
                "parts": [{"page": 98, "pattern": r"Total 2055\s+\d", "values": {"actual": 1, "be": 2, "re": 3, "be_next": 4}}],
            }
        ],
        [{"a": "re", "b": "be", "threshold_pct": 10}],
        flag_re_equals_be=True,
    )
    res = run_engine("P1", [{"page": 98, "raw_text": _TS_PAGE}], ctx)
    assert [lead.rule_triggered for lead in res.leads] == ["P1-RE-EQUALS-BE"]
    assert res.leads[0].anomaly_metrics["be"] == 187613.28


def test_p1_missing_line_is_unevaluable_not_a_lead() -> None:
    ctx = _p1_ctx(
        [{"key": "x", "entity": "X", "unit": "crore", "parts": [{"page": 1, "pattern": "Nonexistent head", "values": {"be": 0}}]}]
    )
    res = run_engine("P1", [{"page": 1, "raw_text": _NOTES_PAGE}], ctx)
    assert res.leads == [] and res.unevaluable_count == 1


def test_repository_police_project_uses_p1_on_official_documents() -> None:
    data = json.loads((REPO_PROJECTS / "police-finances.json").read_text())
    assert data["posted_by"]["name"] == "Copwatch India"
    assert data["methodology_code"] == "P1"
    urls = [t["payload"]["retrieval_url"] for t in data["tasks"]]
    assert all(u.startswith("https://") for u in urls)
    assert any("indiabudget.gov.in/budget2025-26" in u for u in urls)


def test_hex_digests_are_not_mistaken_for_phone_numbers() -> None:
    from aiwatchdogs.core.safety import verhoeff_generate_check_digit

    digest = "22db9256b950b843a3ee77627bf9b43fdc5e89390e565d441059f09055412504"
    aadhaar_run_digest = "016a42d6d0a1c8a52425df43e0061866ab0d890755632223a16cc09799e375bb"
    assert scrub_pii(digest) == (digest, [])
    assert scrub_pii(aadhaar_run_digest) == (aadhaar_run_digest, [])
    assert scrub_pii("Call 09876543210 now")[1] == ["phone"]
    assert scrub_pii("+91 98765 43210")[1] == ["phone"]

    # P1 Security regression: checksum-valid Aadhaar numbers glued directly to English/Hindi labels
    # or underscores MUST be redacted, while whole cryptographic hex digests stay intact.
    valid_aadhaar = "23456789012" + verhoeff_generate_check_digit("23456789012")
    for sample in (
        f"Aadhaar{valid_aadhaar}",
        f"aadhaar_{valid_aadhaar}",
        f"UID{valid_aadhaar}",
        f"आधार{valid_aadhaar}",
        f"आधार_{valid_aadhaar}",
        f"{valid_aadhaar}_verified",
        f"{valid_aadhaar}आधार",
        f"cafe{valid_aadhaar}",
    ):
        redacted, cats = scrub_pii(sample)
        assert valid_aadhaar not in redacted, f"Aadhaar escaped redaction in {sample!r}: {redacted!r}"
        assert "[REDACTED_AADHAAR]" in redacted
        assert "aadhaar" in cats

    # Whole 64-char SHA-256 and 32-char MD5 hex digests containing that exact 12-digit run stay intact
    sha256_with_aadhaar = f"a1b2c3d4e5f6{valid_aadhaar}{'deadbeef' * 5}"
    assert len(sha256_with_aadhaar) == 64
    assert scrub_pii(sha256_with_aadhaar) == (sha256_with_aadhaar, [])
    md5_with_aadhaar = f"deadbeef{valid_aadhaar}cafe1234abcd"
    assert len(md5_with_aadhaar) == 32
    assert scrub_pii(md5_with_aadhaar) == (md5_with_aadhaar, [])


@pytest.mark.production_missions
def test_sync_projects_retires_obsolete_queued_tasks_while_preserving_completed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdir = tmp_path / "projects"
    pdir.mkdir()
    base_task = _project()["tasks"][0]
    v1 = _project(
        tasks=[
            {**base_task, "id": "test-city-budget-old-1", "dataset_key": "old-1"},
            {**base_task, "id": "test-city-budget-old-2", "dataset_key": "old-2"},
        ]
    )
    (pdir / "test-city-budget.json").write_text(json.dumps(v1), encoding="utf-8")
    monkeypatch.setenv("AIWATCHDOGS_PROJECTS_DIR", str(pdir))
    monkeypatch.delenv("AIWATCHDOGS_KEEP_LEGACY_MISSIONS", raising=False)
    db_path = tmp_path / "retire.db"

    app1 = create_app(db_path=db_path, seed=True)
    with app1.state.db.transaction() as conn:
        conn.execute(
            "UPDATE tasks SET status = 'completed', completed_at = '2026-11-26T00:00:00Z' "
            "WHERE id = 'prj-test-city-budget-old-1'"
        )

    # Upgrade to new task IDs: unstarted old-2 must be retired, completed old-1 preserved!
    v2 = _project(
        title="Upgraded city police budget audit",
        tasks=[
            {**base_task, "id": "test-city-budget-new-1", "dataset_key": "new-1"},
            {**base_task, "id": "test-city-budget-new-2", "dataset_key": "new-2"},
            {**base_task, "id": "test-city-budget-new-3", "dataset_key": "new-3"},
        ],
    )
    (pdir / "test-city-budget.json").write_text(json.dumps(v2), encoding="utf-8")

    with TestClient(create_app(db_path=db_path, seed=True)) as client:
        detail = client.get("/api/missions/test-city-budget").json()
        assert detail["title"] == "Upgraded city police budget audit"
        task_ids = {t["id"] for t in detail["tasks"]}
        assert "prj-test-city-budget-old-1" in task_ids  # completed preserved
        assert "prj-test-city-budget-old-2" not in task_ids  # obsolete queued retired
        assert {"prj-test-city-budget-new-1", "prj-test-city-budget-new-2", "prj-test-city-budget-new-3"} <= task_ids
        assert detail["total_tasks"] == 4


@pytest.mark.production_missions
def test_sync_projects_retires_superseded_claimed_leases_and_orphaned_w1_manifests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P1 regression: claimed legacy 3-digit batch leases must be safely retired on upgrade,
    unblocking the volunteer agent and never returning broken manifests to the queue."""
    pdir = tmp_path / "projects"
    pdir.mkdir()
    base_task = _project()["tasks"][0]
    v1 = _project(
        tasks=[
            {**base_task, "id": "test-city-budget-old-claimed", "dataset_key": "old-claimed"},
            {**base_task, "id": "test-city-budget-old-parked", "dataset_key": "old-parked"},
            {**base_task, "id": "test-city-budget-old-done", "dataset_key": "old-done"},
        ]
    )
    (pdir / "test-city-budget.json").write_text(json.dumps(v1), encoding="utf-8")
    monkeypatch.setenv("AIWATCHDOGS_PROJECTS_DIR", str(pdir))
    monkeypatch.delenv("AIWATCHDOGS_KEEP_LEGACY_MISSIONS", raising=False)
    db_path = tmp_path / "retire_claimed.db"

    with TestClient(create_app(db_path=db_path, seed=True)) as client1:
        reg = client1.post(
            "/api/agents/register",
            json={"handle": "legacy-volunteer", "provider": "rule-engine", "model": "deterministic-cpu-v1", "mode": "auto"},
        ).json()
        claim = client1.post(
            "/api/desk/next",
            headers={"X-Agent-Token": reg["api_token"]},
            json={"task_id": "prj-test-city-budget-old-claimed"},
        )
        assert claim.status_code == 200, claim.text
        old_lease_id = claim.json()["lease_id"]
        with client1.app.state.db.transaction() as conn:
            conn.execute(
                "UPDATE tasks SET status = 'needs_adjudication' WHERE id = 'prj-test-city-budget-old-parked'"
            )
            conn.execute(
                "UPDATE tasks SET status = 'completed', completed_at = '2026-11-26T00:00:00Z' "
                "WHERE id = 'prj-test-city-budget-old-done'"
            )

    # Upgrade to v2 with new task IDs: claimed & parked legacy tasks must be retired cleanly!
    v2 = _project(
        title="Upgraded v2 project",
        tasks=[
            {**base_task, "id": "test-city-budget-new-0001", "dataset_key": "new-0001"},
            {**base_task, "id": "test-city-budget-new-0002", "dataset_key": "new-0002"},
        ],
    )
    (pdir / "test-city-budget.json").write_text(json.dumps(v2), encoding="utf-8")

    with TestClient(create_app(db_path=db_path, seed=True)) as client2:
        detail = client2.get("/api/missions/test-city-budget").json()
        task_ids = {t["id"] for t in detail["tasks"]}
        assert "prj-test-city-budget-old-claimed" not in task_ids
        assert "prj-test-city-budget-old-parked" not in task_ids
        assert "prj-test-city-budget-old-done" in task_ids
        assert {"prj-test-city-budget-new-0001", "prj-test-city-budget-new-0002"} <= task_ids
        assert detail["total_tasks"] == 3

        # Submitting against the superseded lease returns clean 404/409/410 (never HTTP 503!)
        old_sub = client2.post(
            "/api/desk/submit",
            headers={"X-Agent-Token": reg["api_token"]},
            json={"lease_id": old_lease_id, "outcome": "cleared"},
        )
        assert old_sub.status_code in (404, 409, 410), old_sub.text

        # Volunteer agent's current_task_id was cleared and can immediately claim a fresh v2 task
        new_claim = client2.post(
            "/api/desk/next",
            headers={"X-Agent-Token": reg["api_token"]},
            json={"preferred_mission": "test-city-budget"},
        )
        assert new_claim.status_code == 200, new_claim.text
        assert new_claim.json()["task"]["id"] in {"prj-test-city-budget-new-0001", "prj-test-city-budget-new-0002"}

        # Belt-and-suspenders: if an orphaned W1 task referencing a deleted 3-digit batch CSV ever
        # expires or is released, _reclaim_expired_leases retires it instead of re-queueing it.
        with client2.app.state.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO tasks (
                    id, mission_id, mission_slug, methodology_code, task_type, title, instructions,
                    payload_json, status, claimed_by_agent_id, lease_id, lease_started_at,
                    lease_expires_at, created_at, updated_at
                ) VALUES (
                    'prj-orphan-w1-001', 'project-test-city-budget', 'test-city-budget', 'W1', 'anomaly_scan',
                    'Orphan legacy batch', 'Legacy batch',
                    '{"retrieval_url":"https://aiwatchdogs.in/api/missions/gov-in-public-information/batches/batch-001.csv"}',
                    'claimed', ?, 'lease-orphan-1', '2020-01-01T00:00:00Z', '2020-01-01T00:10:00Z',
                    '2020-01-01T00:00:00Z', '2020-01-01T00:00:00Z'
                )
                """,
                (reg["agent_id"],),
            )
            client2.app.state.desk._reclaim_expired_leases(conn)
            orphan = conn.execute("SELECT id FROM tasks WHERE id = 'prj-orphan-w1-001'").fetchone()
            assert orphan is None

        # Retired finding-referenced tasks must NOT leak into mission rungs or token estimates!
        before_detail = client2.get("/api/missions/test-city-budget").json()
        before_tokens = before_detail["token_summary"]["estimated_tokens"]
        with client2.app.state.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO tasks (
                    id, mission_id, mission_slug, methodology_code, task_type, title, instructions,
                    payload_json, status, estimated_tokens, created_at, updated_at
                ) VALUES (
                    'prj-retired-ref-task', 'project-test-city-budget', 'test-city-budget', 'M1', 'anomaly_scan',
                    'Retired historical task', 'Retired', '{}', 'retired', 999999,
                    '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z'
                )
                """
            )
        after_detail = client2.get("/api/missions/test-city-budget").json()
        assert after_detail["token_summary"]["estimated_tokens"] == before_tokens
        assert all(r["id"] != "prj-retired-ref-task" for r in after_detail["rungs"])
        assert all(t["id"] != "prj-retired-ref-task" for t in after_detail["tasks"])


@pytest.mark.production_missions
def test_sync_projects_handles_postgres_advisory_lock_contention_without_crashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Production regression: concurrent serverless cold starts must wait via pg_try_advisory_xact_lock
    and serve existing live projects cleanly instead of crashing with LockNotAvailable -> 503."""
    from contextlib import contextmanager
    from typing import Any
    from aiwatchdogs.hub.database import Database
    from aiwatchdogs.projects import sync_projects

    pdir = tmp_path / "projects"
    pdir.mkdir()
    proj = _project()
    (pdir / "test-city-budget.json").write_text(json.dumps(proj), encoding="utf-8")
    db = Database(db_path=tmp_path / "lock_contention.db")
    # Initial sync succeeds and populates active community mission
    r0 = sync_projects(db, pdir)
    assert r0["changed"] is True

    # Modify project file so fingerprint changes, then simulate another Postgres container holding the lock
    proj["title"] = "Updated city police budget audit"
    (pdir / "test-city-budget.json").write_text(json.dumps(proj), encoding="utf-8")

    class _ContendedPgDb:
        is_postgres = True

        def __init__(self, inner: Database, *, release_after_polls: int | None) -> None:
            self._inner = inner
            self._polls = 0
            self._release_after = release_after_polls

        @contextmanager
        def read(self):
            with self._inner.read() as conn:
                yield conn

        @contextmanager
        def transaction(self, **_kwargs: Any):
            class _LockOnlyConn:
                def __init__(self, acquired: bool) -> None:
                    self._acquired = acquired

                def execute(self, sql: str, params: Any = None) -> Any:
                    assert "pg_try_advisory_xact_lock" in sql
                    return type("_Cur", (), {"fetchone": lambda _self: {"acquired": self._acquired}})()

            self._polls += 1
            if self._release_after is not None and self._polls >= self._release_after:
                # Simulate peer finishing sync before our next read check
                sync_projects(self._inner, pdir)
            yield _LockOnlyConn(acquired=False)

    # Case 1: Lock stays held past timeout while active missions already exist -> graceful non-crashing skip
    res_warn = sync_projects(_ContendedPgDb(db, release_after_polls=None), pdir, lock_wait_seconds=0.05)
    assert res_warn["changed"] is False
    assert "actively syncing" in res_warn.get("warning", "")

    # Case 2: Peer finishes syncing on 2nd poll -> exits immediately without warning
    res_peer_done = sync_projects(_ContendedPgDb(db, release_after_polls=2), pdir, lock_wait_seconds=2.0)
    assert res_peer_done["changed"] is False
    assert "warning" not in res_peer_done
    db.close()


@pytest.mark.production_missions
def test_peer_verify_tasks_carry_the_server_side_engine_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifiers must re-run P1 with the project's page/line specs, or reproduction yields 0 leads."""
    import sqlite3

    proj = _project(methodology_code="P1")
    proj["tasks"][0]["methodology_code"] = "P1"
    proj["tasks"][0]["payload"]["context"] = {"budget_lines": [{"key": "x", "parts": []}], "flag_re_equals_be": True}
    pdir = tmp_path / "projects"
    pdir.mkdir()
    (pdir / "test-city-budget.json").write_text(json.dumps(proj))
    monkeypatch.setenv("AIWATCHDOGS_PROJECTS_DIR", str(pdir))
    monkeypatch.delenv("AIWATCHDOGS_KEEP_LEGACY_MISSIONS", raising=False)
    db_path = tmp_path / "hub.db"

    with TestClient(create_app(db_path=db_path, seed=True)) as client:
        reg = client.post(
            "/api/agents/register",
            json={"handle": "p1-auditor", "provider": "rule-engine", "model": "deterministic-cpu-v1", "mode": "auto"},
        ).json()
        claim = client.post(
            "/api/desk/next",
            headers={"X-Agent-Token": reg["api_token"]},
            json={"handle": "p1-auditor", "mode": "investigate"},
        )
        assert claim.status_code == 200, claim.text
        finding = {
            "title": "Test Police: revised estimate vs budget estimate is 20.0% below (line 5)",
            "summary": "The official budget document prints 80.00 crore against 100.00 crore. Statistical divergence for public review.",
            "methodology_code": "P1",
            "entity_name": "Test Police",
            "jurisdiction": "State:Test",
            "fiscal_year": "FY2024-25",
            "monetary_exposure_inr": 2e8,
            "severity": "low",
            "anomaly_metrics": {"be": 100.0, "re": 80.0, "pct_difference": -20.0},
            "rule_triggered": "P1-RE-VS-BE>10%",
            "engine_version": "2.0.0",
            "ruleset_version": "P1-2026.11-v1",
            "source_locator": "p.1",
            "artifact_sha256": SHA,
            "citations": [{"page": 1, "printed_line": "5. Test Police 100.00 80.00"}],
            "benign_explanations": ["Mid-year revisions can reflect re-phased capital works."],
            "suggested_authority": "Home Department, Government of Test",
        }
        sub = client.post(
            "/api/desk/submit",
            headers={"X-Agent-Token": reg["api_token"]},
            json={"lease_id": claim.json()["lease_id"], "outcome": "finding", "finding": finding},
        )
        assert sub.status_code == 200, sub.text

    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT payload_json FROM tasks WHERE task_type = 'peer_verify'").fetchall()
    conn.close()
    assert rows, "peer verification tasks should be queued"
    for (payload_json,) in rows:
        assert json.loads(payload_json)["context"]["flag_re_equals_be"] is True


# ---------------------------------------------------------------------------
# Community project listing (/enlist.txt, template, discovery & needs_data sync)
# ---------------------------------------------------------------------------


def test_enlist_txt_endpoint_parity_security_and_guardrails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aiwatchdogs.hub.templates import render_enlist_txt

    repo_root = Path(__file__).resolve().parents[1]
    static_enlist = (repo_root / "web" / "public" / "enlist.txt").read_text(encoding="utf-8")
    rendered_canonical = render_enlist_txt("https://aiwatchdogs.in")
    assert static_enlist == rendered_canonical, "web/public/enlist.txt must stay in exact parity with render_enlist_txt()"

    # Every safety guardrail and current schema detail must be present
    for required in (
        "UNTRUSTED DATA BOUNDARY",
        "IT Act 2000 Sec 43",
        "BNS Sec 356",
        "DPDP Act 2023",
        "Never read secrets",
        "audit, budget, disclosure, infra, parliament, procurement, scheme",
        '"status": "needs_data"',
        "Do not set `project_code`",
        "python3 -I -c",
        "https://github.com/aiwatchdogsindia/aiwatchdogs.in/new/main?",
        "uv run aiwatchdogs project validate projects/<slug>.json",
        "https://aiwatchdogs.in/projects",
        "https://aiwatchdogs.in/join.txt",
    ):
        assert required in rendered_canonical, f"Missing {required!r} in /enlist.txt"
    assert "project listing is closed" not in rendered_canonical.lower()
    assert "outside project submissions are not accepted" not in rendered_canonical.lower()

    # FastAPI GET/HEAD /enlist.txt sets Cache-Control: no-store and resists Host header injection
    monkeypatch.delenv("AIWATCHDOGS_PUBLIC_URL", raising=False)
    monkeypatch.setenv("VERCEL", "1")
    app = create_app(db_path=tmp_path / "enlist.db", seed=False)
    with TestClient(app) as client:
        malicious_headers = {
            "Host": 'evil.example/$(id)";rm -rf /;#',
            "X-Forwarded-Host": "attacker.evil:443/$(touch /tmp/pwn)",
            "X-Forwarded-Proto": "javascript",
        }
        get_resp = client.get("/enlist.txt", headers=malicious_headers)
        assert get_resp.status_code == 200
        assert get_resp.headers["content-type"].startswith("text/plain")
        assert get_resp.headers.get("cache-control") == "no-store"
        assert "evil.example" not in get_resp.text
        assert "attacker.evil" not in get_resp.text
        assert get_resp.text == rendered_canonical

        head_resp = client.head("/enlist.txt")
        assert head_resp.status_code == 200
        assert head_resp.content == b""
        assert head_resp.headers.get("cache-control") == "no-store"


@pytest.mark.production_missions
def test_template_json_and_enlisted_needs_data_project_validate_and_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aiwatchdogs.hub.agent_access import build_enlist_prompt

    assert (
        build_enlist_prompt("https://aiwatchdogs.in")
        == "Fetch and follow https://aiwatchdogs.in/enlist.txt to list my audit project with me"
    )
    template_data = json.loads((REPO_PROJECTS / "_template.json").read_text(encoding="utf-8"))
    assert "project_code" not in template_data
    assert validate_project(template_data, filename_stem=template_data["slug"]) == []

    # A community project drafted by following /enlist.txt (no project_code, no methodology_code, needs_data)
    community_draft = {
        "slug": "pune-road-repair-spending",
        "title": "Pune municipal road repair spending audit",
        "summary": "Cross-checks Pune Municipal Corporation published road repair tenders and ward budget actuals.",
        "posted_by": {"name": "Pune Civic Data", "handle": "@punecivic", "url": "https://example.org/pune"},
        "pillar": "parliament",
        "status": "needs_data",
        "jurisdiction": "City:Pune",
        "authority": "Pune Municipal Corporation",
        "scope": ["Published road repair work orders and ward budget allocations"],
        "out_of_scope": ["Anything about private individuals", "Investigations beyond published public records"],
        "statutory_anchors": ["RTI Act 2005"],
        "sources": [{"name": "PMC Open Budgets", "publisher": "Pune Municipal Corporation", "url": "https://pmc.gov.in"}],
        "tasks": [],
        "planned_tasks": [
            {
                "title": "Collect published FY 2024-25 road repair budget books",
                "type": "find_sources",
                "estimated_tokens": 1_500_000,
                "estimate_basis": "rough guess",
            }
        ],
    }
    assert validate_project(community_draft, filename_stem=community_draft["slug"]) == []

    pdir = tmp_path / "projects"
    pdir.mkdir()
    (pdir / f"{community_draft['slug']}.json").write_text(json.dumps(community_draft), encoding="utf-8")
    monkeypatch.setenv("AIWATCHDOGS_PROJECTS_DIR", str(pdir))
    monkeypatch.delenv("AIWATCHDOGS_KEEP_LEGACY_MISSIONS", raising=False)

    with TestClient(create_app(db_path=tmp_path / "community.db", seed=True)) as client:
        missions = client.get("/api/missions").json()["missions"]
        assert [m["slug"] for m in missions] == ["pune-road-repair-spending"]
        item = missions[0]
        assert item["status"] == "needs_data"
        assert item["total_tasks"] == 0
        assert item["posted_by"]["name"] == "Pune Civic Data"
        assert item["posted_by"]["handle"] == "@punecivic"

        # Agent discovery routes advertise /enlist.txt and the 1-line enlist prompt
        enlist_prompt = "Fetch and follow http://testserver/enlist.txt to list my audit project with me"
        assert "/enlist.txt" in client.get("/llms.txt").text
        assert enlist_prompt in client.get("/projects.md").text
        assert enlist_prompt in client.get("/brief.md").text
        assert "/enlist.txt" in client.get("/how-it-works.md").text
        assert "/enlist.txt" in client.get("/sitemap.xml").text

