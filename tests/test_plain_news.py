from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from plain_samples import add_finding as _add
from plain_samples import correct_finding as _correct

from aiwatchdogs.hub.app import create_app
from aiwatchdogs.plain import news
from aiwatchdogs.plain.explain import source_hash


def test_news_api_and_feeds(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "hub.db", seed=True)
    today = news.today_ist()
    start, _ = news.window(today)
    with TestClient(app) as client:
        _add(app, "f-new", "verified", start, start, amount=5.895e9)
        _add(app, "f-review", "peer_review", start)
        _add(app, "f-hidden", "verified", start, start, quarantined=1)

        r = client.get("/api/news")
        assert r.status_code == 200
        body = r.json()
        assert body["state"] == "live" and body["date"] == today.isoformat()
        ids = {s["id"] for s in body["sections"]}
        assert {"state", "top", "desk", "explainer", "spotlight", "act", "cta"} <= ids
        top = next(s for s in body["sections"] if s["id"] == "top")
        assert [st["finding_id"] for st in top["stories"]] == ["f-new"]
        assert "Raw title" not in json.dumps(top)
        assert "f-hidden" not in r.text and "f-review" not in json.dumps(top)
        cta = next(s for s in body["sections"] if s["id"] == "cta")
        assert "join.txt" in cta["prompt"]
        assert body["reading_minutes"] >= 1 and len(body["fingerprint"]) == 16

        hi = client.get("/api/news", params={"lang": "hi"}).json()
        assert hi["lang"] == "hi" and hi["title"] != body["title"]

        assert client.get("/api/news", params={"date": "1999-01-01"}).status_code == 404
        assert client.get("/api/news/editions").json()["dates"][0] == today.isoformat()

        md = client.get("/news.md")
        assert md.status_code == 200 and md.headers["content-type"].startswith("text/markdown")
        assert "join.txt" in md.text

        atom = client.get("/news.atom")
        assert atom.status_code == 200 and "application/atom+xml" in atom.headers["content-type"]


def test_past_editions_are_frozen(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "hub.db", seed=True)
    with TestClient(app):
        day = date(2026, 11, 1)
        start, _ = news.window(day)
        _add(app, "f-old", "verified", start, start)
        first = news.get_edition(app.state.db, day)
        assert first["state"] == "final"
        _add(app, "f-later", "verified", start, start)
        again = news.get_edition(app.state.db, day)
        assert again["fingerprint"] == first["fingerprint"]


def test_withheld_findings_leave_frozen_editions_and_wipes_do_not_change_them(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "hub.db", seed=True)
    with TestClient(app) as client:
        db = app.state.db
        day = date(2026, 11, 2)
        start, _ = news.window(day)
        _add(app, "f-keep", "verified", start, start, amount=1e9)
        _add(app, "f-pull", "verified", start, start, amount=5e9)
        first = news.get_edition(db, day)
        assert first["number"] == "f-pull"
        with db.transaction() as conn:
            conn.execute("DELETE FROM summary_cache")
            conn.execute("UPDATE findings SET quarantined = 1 WHERE id = 'f-pull'")
        _add(app, "f-new", "verified", start, start)
        again = news.get_edition(db, day)
        assert again["fingerprint"] == first["fingerprint"]
        ids = {x["id"] for x in again["top"]}
        assert ids == {"f-keep"} and again["number"] is None and again["withheld"] == 1
        body = client.get("/api/news", params={"date": day.isoformat()}).text
        assert "f-pull" not in body
        assert "f-pull" not in client.get(f"/news/{day.isoformat()}.md").text


def test_daily_counts_are_not_capped_by_the_story_list(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "hub.db", seed=True)
    with TestClient(app):
        day = news.today_ist()
        start, _ = news.window(day)
        for i in range(8):
            _add(app, f"f-c{i}", "verified", start, start, amount=1e7 * (i + 1))
        for i in range(5):
            _add(app, f"f-x{i}", "cleared", start, start)
        ed = news.get_edition(app.state.db, day)
        assert len(ed["top"]) == 6 and ed["stats"]["confirmed"] == 8
        assert len(ed["cleared"]) == 3 and ed["stats"]["cleared"] == 5


def test_news_uses_saved_model_cards(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "hub.db", seed=True)
    with TestClient(app):
        day = news.today_ist()
        start, _ = news.window(day)
        _add(app, "f-m", "verified", start, start, amount=5.895e9)
        ed = news.get_edition(app.state.db, day)
        snap = ed["top"][0]
        assert snap["summary"] == "s"
        written = {"finding:f-m": {"hash": source_hash(snap), "model": "m", "langs": {"en": {
            "head": "Rural Development left 49.1% of its money unspent",
            "what": "Only ₹610 crore of ₹1,200 crore was spent.",
            "why": "Money meant for villages sat unused.",
            "ask": "What held it back?",
        }}}}
        r = news.render(ed, "en", written=written)
        top = next(x for x in r["sections"] if x["id"] == "top")
        assert top["stories"][0]["source"] == "model"


def test_clearings_are_dated_by_when_they_happened(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "hub.db", seed=True)
    with TestClient(app):
        today = news.today_ist()
        old_day = today - timedelta(days=5)
        old_start, _ = news.window(old_day)
        start, _ = news.window(today)
        _add(app, "f-old", "peer_review", old_start)
        _correct(app, "f-old", "peer_review", "cleared", start)
        ed = news.get_edition(app.state.db, today)
        assert [x["id"] for x in ed["cleared"]] == ["f-old"] and ed["stats"]["cleared"] == 1
        past = news.get_edition(app.state.db, old_day)
        assert past["stats"]["cleared"] == 0 and past["cleared"] == []


def test_past_editions_use_the_state_on_their_day(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "hub.db", seed=True)
    with TestClient(app):
        today = news.today_ist()
        two_ago, yesterday = today - timedelta(days=2), today - timedelta(days=1)
        _add(app, "f-late", "verified", news.window(two_ago)[0], news.window(today)[0])
        ed = news.get_edition(app.state.db, yesterday)
        assert ed["totals"]["confirmed"] == 0 and ed["totals"]["review"] >= 1
        assert all(p["largest"] is None or p["largest"]["id"] != "f-late" for p in ed["projects"])
        assert news.get_edition(app.state.db, today)["stats"]["confirmed"] == 1


def test_withdrawn_stories_leave_final_editions(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "hub.db", seed=True)
    with TestClient(app) as client:
        day = date(2026, 11, 3)
        start, _ = news.window(day)
        _add(app, "f-gone", "verified", start, start, amount=5e9)
        _add(app, "f-stay", "verified", start, start, amount=1e9)
        first = news.get_edition(app.state.db, day)
        assert {x["id"] for x in first["top"]} == {"f-gone", "f-stay"}
        _correct(app, "f-gone", "verified", "retracted", news.window(day + timedelta(days=1))[0])
        again = news.get_edition(app.state.db, day)
        assert {x["id"] for x in again["top"]} == {"f-stay"} and again["withdrawn"] == 1
        assert "f-gone" not in client.get(f"/news/{day.isoformat()}.md").text


def test_lede_hash_covers_project_examples() -> None:
    ed = {"date": "2026-11-01", "top": [], "recent": [], "cleared": [], "corrections": [], "stats": {},
          "projects": [{"slug": "p", "largest": {"id": "f-x"}}]}
    assert news.lede_hash(ed) != news.lede_hash({**ed, "projects": [{"slug": "p", "largest": None}]})


def test_news_page_negotiates_markdown(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "hub.db", seed=True)
    with TestClient(app) as client:
        for route in ("/news", f"/news/{news.today_ist().isoformat()}"):
            r = client.get(route, headers={"Accept": "text/markdown"})
            assert r.status_code == 200 and r.headers["content-type"].startswith("text/markdown"), route
