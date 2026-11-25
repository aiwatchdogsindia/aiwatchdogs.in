"""Community projects: file validation, hub sync, and archiving of the original missions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from aiwatchdogs.cli.main import app as cli_app
from aiwatchdogs.hub.app import create_app
from aiwatchdogs.projects import load_project_files, validate_project

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


def test_filename_must_match_slug() -> None:
    errors = validate_project(_project(), filename_stem="something-else")
    assert any("File name must match" in e for e in errors)


def test_needs_data_project_without_tasks_is_valid() -> None:
    proj = _project(status="needs_data", tasks=[])
    assert validate_project(proj, filename_stem="test-city-budget") == []


def test_repository_project_files_are_valid() -> None:
    files = load_project_files(REPO_PROJECTS)
    assert files, "projects/ should contain at least one project"
    for pf in files:
        assert pf.errors == [], f"{pf.path.name}: {pf.errors}"


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

        # Archived legacy missions are still reachable by direct link (public record).
        legacy = client.get("/api/missions/assam-ocds-single-bid")
        assert legacy.status_code == 200
        assert legacy.json()["status"] == "archived"

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
    data = json.loads((REPO_PROJECTS / "metro-police-finances.json").read_text())
    assert data["posted_by"]["name"] == "Copwatch India"
    assert data["methodology_code"] == "P1"
    urls = [t["payload"]["retrieval_url"] for t in data["tasks"]]
    assert all(u.startswith("https://") for u in urls)
    assert any("indiabudget.gov.in/budget2025-26" in u for u in urls)


def test_hex_digests_are_not_mistaken_for_phone_numbers() -> None:
    digest = "22db9256b950b843a3ee77627bf9b43fdc5e89390e565d441059f09055412504"
    assert scrub_pii(digest) == (digest, [])
    assert scrub_pii("Call 09876543210 now")[1] == ["phone"]
    assert scrub_pii("+91 98765 43210")[1] == ["phone"]


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
