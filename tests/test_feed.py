"""Atom feeds: well-formed, linked, and silent about unverified or quarantined leads."""

from __future__ import annotations

import json
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from fastapi.testclient import TestClient
from test_projects import _project

from aiwatchdogs.hub.app import create_app
from aiwatchdogs.hub.feed import render_atom

NS = {"a": "http://www.w3.org/2005/Atom"}


def _add_finding(app, fid: str, status: str, title: str, *, quarantined: int = 0) -> None:
    with app.state.db.transaction() as conn:
        m = conn.execute("SELECT id, slug FROM missions WHERE slug = 'test-city-budget'").fetchone()
        conn.execute(
            "INSERT INTO findings (id, mission_id, mission_slug, pillar, title, summary, methodology_code, entity_name, "
            "jurisdiction, fiscal_year, submitted_by, status, quarantined, is_demo_sample, created_at) "
            "VALUES (?, ?, ?, 'budget', ?, 's', 'M5', 'E', 'State:Test', 'FY2023-24', 'node', ?, ?, 0, '2026-11-20T00:00:00Z')",
            (fid, m["id"], m["slug"], title, status, quarantined),
        )


@pytest.mark.production_missions
def test_feeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pdir = tmp_path / "projects"
    pdir.mkdir()
    (pdir / "test-city-budget.json").write_text(json.dumps(_project()))
    monkeypatch.setenv("AIWATCHDOGS_PROJECTS_DIR", str(pdir))
    monkeypatch.delenv("AIWATCHDOGS_KEEP_LEGACY_MISSIONS", raising=False)
    app = create_app(db_path=tmp_path / "hub.db", seed=True)
    with TestClient(app) as client:
        _add_finding(app, "f-ok", "verified", "Roads <budget> & actuals")
        _add_finding(app, "f-review", "peer_review", "Unverified lead")
        _add_finding(app, "f-hidden", "verified", "Hidden lead", quarantined=1)
        _add_finding(app, "f-gone", "retracted", "Withdrawn lead")
        with app.state.db.transaction() as conn:
            conn.execute(
                "INSERT INTO finding_corrections (id, finding_id, correction_type, new_status, summary, created_at) "
                "VALUES ('c1', 'f-gone', 'retraction', 'retracted', 'Wrong column.', '2026-11-21T00:00:00Z')"
            )
        for path in ("/feed.atom", "/feeds/test-city-budget.atom"):
            resp = client.get(path)
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("application/atom+xml")
            root = ET.fromstring(resp.content)
            titles = [t.text for t in root.findall("a:entry/a:title", NS)]
            assert "Verified: Roads <budget> & actuals" in titles
            assert "Retracted: Withdrawn lead" in titles
            assert "Project added: Test city police budget audit" in titles
            assert "Unverified" not in resp.text and "Hidden" not in resp.text
            assert "Verified: Withdrawn lead" not in titles
            ids = [i.text for i in root.findall("a:entry/a:id", NS)]
            assert len(ids) == len(set(ids))
            stamps = [u.text for u in root.findall("a:entry/a:updated", NS)]
            assert stamps == sorted(stamps, reverse=True)
        assert client.get("/feeds/nope.atom").status_code == 404


def test_control_characters_and_empty_feeds_stay_well_formed() -> None:
    entry = {"key": "k", "updated": "2026-11-20T00:00:00Z", "term": "t", "title": "a\x00b\x0b", "path": "/", "summary": "s"}
    for entries in ([entry], []):
        ET.fromstring(render_atom(entries, base_url="https://aiwatchdogs.in", path="/feed.atom", title="T", page="/"))
