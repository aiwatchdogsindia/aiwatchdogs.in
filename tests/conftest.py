"""Shared pytest configuration for AI Watchdogs India test suite."""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import pytest

from aiwatchdogs.projects import INFLATION_METHODOLOGY, INFLATION_PROJECT_SLUG

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


@pytest.fixture
def inflation_projects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Install a two-task research project with a dependency and no global project choice."""
    projects = tmp_path / "research-projects"
    projects.mkdir()
    monkeypatch.setenv("AIWATCHDOGS_PROJECTS_DIR", str(projects))
    monkeypatch.delenv("AIWATCHDOGS_ALLOW_PROJECT_CHOICE", raising=False)
    monkeypatch.delenv("AIWATCHDOGS_KEEP_LEGACY_MISSIONS", raising=False)
    source_url = "https://example.org/public-price-data.json"
    task = {
        "id": "cpi-test-collection",
        "title": "Collect test market prices",
        "instructions": "Check dated price evidence and document any coverage gaps.",
        "dataset_key": "cpi-test-collection",
        "financial_quantity_type": "consumer_prices",
        "type": "extract_data",
        "estimated_tokens": 1200,
        "estimate_basis": "Read the fixture and draft one report",
        "payload": {
            "retrieval_url": source_url,
            "expected_sha256": "a" * 64,
            "official_publisher": "Test Office",
            "source_landing_page": source_url,
            "context": {"inflation_scope": {"role": "collection", "state_code": "01", "sector": "rural"}},
        },
    }
    (projects / f"{INFLATION_PROJECT_SLUG}.json").write_text(json.dumps({
        "slug": INFLATION_PROJECT_SLUG,
        "title": "Independent test inflation project",
        "summary": "Collect reproducible price observations and review the independent evidence.",
        "posted_by": {"name": "Test Collective"},
        "pillar": "audit",
        "status": "active",
        "jurisdiction": "India",
        "authority": "Test Office",
        "sources": [{"name": "Test public prices", "publisher": "Test Office", "url": source_url}],
        "methodology_code": INFLATION_METHODOLOGY,
        "tasks": [task, {
            **task,
            "id": "cpi-test-review",
            "title": "Review test market prices",
            "dataset_key": "cpi-test-review",
            "type": "cross_check",
            "depends_on": [task["id"]],
            "payload": {**task["payload"], "context": {"inflation_scope": {"role": "review"}}},
        }],
    }))
    return projects


@pytest.fixture
def inflation_gap_report() -> dict:
    return {
        "summary": "The assigned source did not contain a published price for the pilot period.",
        "sources": [],
        "observations": [],
        "gaps": [{"reason": "source_gap", "detail": "The public source does not cover the requested month."}],
        "deliverable": "The public evidence gap was recorded for the assigned market.",
    }
