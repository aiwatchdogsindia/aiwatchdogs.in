"""Token estimates on tasks and reported usage after completion."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aiwatchdogs.core.usage import UsageReport, public_usage, summarise_tokens, tokens_used
from aiwatchdogs.hub.app import create_app
from aiwatchdogs.projects import validate_project
from tests.test_projects import PLANNED, _project

CLEARED = {
    "outcome": "cleared",
    "whisper": "All evaluable records passed deterministic checks.",
    "metrics": {"records_scanned": 4, "cleared_count": 4, "unevaluable_count": 0, "monetary_audited_inr": 10},
}


@pytest.mark.parametrize(
    "patch",
    [{"estimated_tokens": 0}, {"estimated_tokens": True}, {"estimated_tokens": "1M"}, {"estimate_basis": ""}, {"type": "magic"}],
)
def test_bad_estimates_are_rejected(patch: dict) -> None:
    planned = _project(status="needs_data", tasks=[], planned_tasks=[{**PLANNED, **patch}])
    assert validate_project(planned, filename_stem="test-city-budget")
    task = {**_project()["tasks"][0], **patch}
    assert validate_project(_project(tasks=[task]), filename_stem="test-city-budget")


def test_a_project_must_say_what_work_it_needs() -> None:
    errors = validate_project(_project(status="needs_data", tasks=[]), filename_stem="test-city-budget")
    assert any("planned_tasks" in e for e in errors)
    errors = validate_project(_project(planned_tasks=[PLANNED] * 10), filename_stem="test-city-budget")
    assert any("at most 10" in e for e in errors)


def test_unreported_usage_is_never_shown_as_zero() -> None:
    assert tokens_used({}) is None
    assert tokens_used({"engine": {"input_tokens": 0, "output_tokens": 0}}) is None
    assert tokens_used({"agent": {"iterations": 3}}) is None
    assert tokens_used({"agent": {"input_tokens": 5, "cached_input_tokens": 10, "output_tokens": 1}}) == 16
    assert summarise_tokens([("completed", None, {})], [])["tokens_used"] is None
    assert public_usage({}, started_at=None, completed_at=None, attempts=0) is None


def test_session_split_keeps_every_token() -> None:
    parts = UsageReport(input_tokens=10, iterations=2).split(3)
    assert sum(p["input_tokens"] for p in parts) == 10
    assert all(p["split_across"] == 3 for p in parts)


@pytest.fixture
def hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    pdir = tmp_path / "projects"
    pdir.mkdir()
    (pdir / "test-city-budget.json").write_text(json.dumps(_project(planned_tasks=[PLANNED])))
    monkeypatch.setenv("AIWATCHDOGS_PROJECTS_DIR", str(pdir))
    monkeypatch.delenv("AIWATCHDOGS_KEEP_LEGACY_MISSIONS", raising=False)
    with TestClient(create_app(db_path=tmp_path / "hub.db", seed=True)) as client:
        yield client


def _register(client: TestClient, handle: str) -> dict[str, str]:
    body = {"handle": handle, "provider": "rule-engine", "model": "deterministic-python-v1"}
    return {"X-Agent-Token": client.post("/api/agents/register", json=body).json()["api_token"]}


@pytest.mark.production_missions
def test_estimate_then_actual_usage_end_to_end(hub: TestClient) -> None:
    summary = hub.get("/api/missions").json()["missions"][0]["token_summary"]
    assert summary["estimated_tokens"] == summary["remaining_tokens"] == 2_300_000
    assert summary["tokens_used"] is None

    auth = _register(hub, "usage-tester")
    ask = {"handle": "usage-tester", "mode": "investigate"}
    assert hub.post("/api/desk/next", headers=auth, json={**ask, "max_estimated_tokens": 1000}).status_code == 204
    lease = hub.post("/api/desk/next", headers=auth, json={**ask, "max_estimated_tokens": 300_000}).json()
    tid = lease["task"]["id"]
    assert (lease["task"]["estimated_tokens"], lease["task"]["work_type"]) == (300_000, "run_check")

    report = {"task_ids": [tid], "usage": {"input_tokens": 1000, "cached_input_tokens": 200_000, "output_tokens": 500, "iterations": 4}}
    assert hub.post("/api/desk/usage", headers=auth, json=report).status_code == 409

    engine = {"source": "engine", "input_tokens": 0, "output_tokens": 0, "duration_seconds": 7}
    done = hub.post("/api/desk/submit", headers=auth, json={**CLEARED, "lease_id": lease["lease_id"], "usage": engine})
    assert done.status_code == 200, done.text
    detail = hub.get("/api/missions/test-city-budget").json()
    assert detail["token_summary"]["tokens_used"] is None
    assert detail["token_summary"]["remaining_tokens"] == 2_000_000
    assert detail["planned_tasks"][0]["type"] == "find_sources"

    assert hub.post("/api/desk/usage", headers=_register(hub, "other-agent"), json=report).status_code == 403
    assert hub.post("/api/desk/usage", json=report).status_code == 401
    bad = {"task_ids": [tid], "usage": {"input_tokens": -1}}
    assert hub.post("/api/desk/usage", headers=auth, json=bad).status_code == 422
    for _ in range(2):  # reporting again corrects the figure; it does not add to it
        assert hub.post("/api/desk/usage", headers=auth, json=report).status_code == 200
    detail = hub.get("/api/missions/test-city-budget").json()
    assert detail["token_summary"]["tokens_used"] == 201_500
    usage = next(t for t in detail["tasks"] if t["id"] == tid)["usage"]
    assert (usage["tokens_used"], usage["iterations"], usage["check_tokens"]) == (201_500, 4, 0)
    assert usage["hub_seconds"] is not None
