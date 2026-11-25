"""The team's project priorities decide where compute goes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_projects import _project

from aiwatchdogs.hub.app import create_app
from aiwatchdogs.projects import sync_projects, validate_project


def _named(slug: str, priority: int | None) -> dict:
    task = {**_project()["tasks"][0], "id": f"{slug}-t1", "dataset_key": f"{slug}-d"}
    data = _project(slug=slug, tasks=[task])
    return data if priority is None else {**data, "priority": priority}


@pytest.mark.parametrize("bad", [0, 11, "1", True, 1.5])
def test_bad_priorities_are_rejected(bad: object) -> None:
    assert validate_project(_project(priority=bad), filename_stem="test-city-budget")


@pytest.mark.production_missions
def test_claims_follow_priority_not_agent_choice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pdir = tmp_path / "projects"
    pdir.mkdir()
    for slug, pr in (("aaa-low", 9), ("bbb-default", None), ("ccc-high", 1)):
        (pdir / f"{slug}.json").write_text(json.dumps(_named(slug, pr)))
    monkeypatch.setenv("AIWATCHDOGS_PROJECTS_DIR", str(pdir))
    monkeypatch.delenv("AIWATCHDOGS_KEEP_LEGACY_MISSIONS", raising=False)
    app = create_app(db_path=tmp_path / "hub.db", seed=True)
    with TestClient(app) as client:
        assert [m["slug"] for m in client.get("/api/missions").json()["missions"]] == ["ccc-high", "bbb-default", "aaa-low"]

        def claim(handle: str) -> str:
            reg = client.post("/api/agents/register", json={"handle": handle, "provider": "rule-engine", "model": "x"}).json()
            resp = client.post(
                "/api/desk/next",
                headers={"X-Agent-Token": reg["api_token"]},
                json={"mode": "investigate", "preferred_mission": "aaa-low"},
            )
            return resp.json()["task"]["mission_slug"]

        assert claim("agent-one") == "ccc-high"
        (pdir / "aaa-low.json").write_text(json.dumps(_named("aaa-low", 2)))
        sync_projects(app.state.db, pdir)
        assert claim("agent-two") == "aaa-low"
        assert claim("agent-three") == "bbb-default"
