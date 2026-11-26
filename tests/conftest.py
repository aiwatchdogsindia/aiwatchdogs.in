"""Shared pytest configuration for AI Watchdogs India test suite."""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import pytest

from aiwatchdogs.projects import UNION_BUDGET_METHODOLOGY, UNION_BUDGET_PROJECT_SLUG

_STARLETTE_HTTPX_DEPRECATION = r"Using `httpx` with `starlette\.testclient` is deprecated.*"

# Starlette 1.10 emits a deprecation warning at import time when httpx (0.28) is used
# instead of httpx2. Filter that specific upstream import warning cleanly during both
# collection and test execution.
warnings.filterwarnings("ignore", message=_STARLETTE_HTTPX_DEPRECATION)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "filterwarnings",
        f"ignore:{_STARLETTE_HTTPX_DEPRECATION}",
    )
    config.addinivalue_line(
        "markers",
        "production_missions: run with production mission behaviour (M1–M8 archived, real projects/ loaded)",
    )


@pytest.fixture(autouse=True)
def _hub_fixture_missions(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Most hub tests use the original M1–M8 missions as fixtures for desk mechanics.

    Production archives them in favour of community projects; tests opt into that
    behaviour with ``@pytest.mark.production_missions``. Community project files are
    isolated to an empty directory unless a test points AIWATCHDOGS_PROJECTS_DIR elsewhere.
    """
    # Desk-mechanics tests pick a project by name; production hubs ignore that choice.
    if not request.node.get_closest_marker("production_missions"):
        monkeypatch.setenv("AIWATCHDOGS_ALLOW_PROJECT_CHOICE", "1")
    if request.node.get_closest_marker("production_missions") is None:
        monkeypatch.setenv("AIWATCHDOGS_KEEP_LEGACY_MISSIONS", "1")
        monkeypatch.setenv("AIWATCHDOGS_PROJECTS_DIR", str(tmp_path_factory.mktemp("no-projects")))


def _ub1_task(task_id: str, year: str, demand: int, line: str, name: str, *, with_actuals: bool) -> dict:
    """One UB1 task in exactly the shape of projects/union-budget-unspent-funds.json."""
    nxt = f"{int(year[:4]) + 1}-{int(year[5:]) + 1:02d}"
    after = f"{int(year[:4]) + 2}-{int(year[5:]) + 2:02d}"
    landing = f"https://www.indiabudget.gov.in/budget{nxt[:4]}-{nxt[:2]}{nxt[5:]}/budget.asp"
    spec: dict = {
        "financial_year": year,
        "estimates_document": {"budget": nxt, "demand_no": demand},
        "lines": [{"key": line, "estimates": [{"number": line, "name": name}]}],
    }
    payload: dict = {
        "retrieval_url": f"https://www.indiabudget.gov.in/budget{nxt[:4]}-{nxt[:2]}{nxt[5:]}/ub{nxt}/eb/sbe{demand}.pdf",
        "expected_sha256": "a" * 64,
        "official_publisher": "Ministry of Finance, Government of India",
        "source_landing_page": landing,
        "artifact_title": f"Notes on Demands for Grants {nxt}, Demand No. {demand}",
        "fiscal_year": f"FY{year}",
        "context": {"union_budget": spec},
    }
    if with_actuals:
        spec["actuals_document"] = {"budget": after, "demand_no": demand}
        spec["lines"][0]["actuals"] = [{"number": line, "name": name}]
        spec["lines"][0]["link"] = "name+figure"
        payload["secondary_url"] = (
            f"https://www.indiabudget.gov.in/budget{after[:4]}-{after[:2]}{after[5:]}/ub{after}/eb/sbe{demand}.pdf"
        )
        payload["secondary_expected_sha256"] = "b" * 64
        payload["context"] = {"secondary_document": "pages", "union_budget": spec}
    return {
        "id": task_id,
        "title": f"{year} · Test Department: {name} (line {line})",
        "instructions": "Verify the SHA-256 of the pinned statements and run UB1 on the listed lines.",
        "dataset_key": task_id,
        "type": "run_check",
        "estimated_tokens": 2000,
        "estimate_basis": "About 2,000 agent tokens to run UB1 with the CLI and read the result.",
        "financial_quantity_type": "budget_and_treasury_expenditure",
        "payload": payload,
    }


UB1_TEST_TASKS = (
    ("ub-2019-20-d042-l12-03", "2019-20", 42, "12.03", "Grants for the 50%_share of Test Scheme", True),
    ("ub-2019-20-d042-l14", "2019-20", 42, "14", "Test Scheme Establishment", False),
    ("ub-2020-21-d042-l12", "2020-21", 42, "12", "Grants for the 50 per cent share of Test Scheme", True),
)


def union_budget_project(tasks: list[dict] | None = None) -> dict:
    """A small, valid UB1 project under the real P3 slug (three tasks, two financial years).

    The first title contains a literal "50%_share" and the third "50 per cent share", which an
    unescaped LIKE "%50%_share%" would also match: searches use that pair to prove LIKE escaping."""
    return {
        "slug": UNION_BUDGET_PROJECT_SLUG,
        "project_code": "P3",
        "priority": 5,
        "title": "Union Budget test: money budgeted but not spent",
        "summary": "Reads the printed budget estimate, revised estimate and actual expenditure for test lines.",
        "posted_by": {"name": "Test Collective"},
        "pillar": "budget",
        "status": "active",
        "jurisdiction": "Union of India",
        "authority": "Ministry of Finance",
        "sources": [{
            "name": "Union Budget: Notes on Demands for Grants",
            "publisher": "Ministry of Finance, Government of India",
            "url": "https://www.indiabudget.gov.in/budget2020-21/budget.asp",
        }],
        "methodology_code": UNION_BUDGET_METHODOLOGY,
        "tasks": tasks if tasks is not None else [
            _ub1_task(tid, year, demand, line, name, with_actuals=act)
            for tid, year, demand, line, name, act in UB1_TEST_TASKS
        ],
    }


@pytest.fixture
def union_budget_projects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Install only a small synthetic UB1 project, with no global project choice."""
    projects = tmp_path / "ub1-projects"
    projects.mkdir()
    monkeypatch.setenv("AIWATCHDOGS_PROJECTS_DIR", str(projects))
    monkeypatch.delenv("AIWATCHDOGS_ALLOW_PROJECT_CHOICE", raising=False)
    monkeypatch.delenv("AIWATCHDOGS_KEEP_LEGACY_MISSIONS", raising=False)
    (projects / f"{UNION_BUDGET_PROJECT_SLUG}.json").write_text(json.dumps(union_budget_project()), encoding="utf-8")
    return projects
