from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from plain_samples import SAMPLES
from plain_samples import add_finding as _add

from aiwatchdogs.hub.app import create_app
from aiwatchdogs.plain import explain_finding, news, writer
from aiwatchdogs.plain.bakeoff import grade
from aiwatchdogs.plain.explain import source_hash


def test_writer_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AIWATCHDOGS_PLAIN_WRITER", raising=False)
    assert not writer.load_config().enabled
    cfg = writer.load_config("anthropic")
    assert cfg.enabled and cfg.model == "claude-opus-5-5"
    assert writer.load_config("ollama").model == "gemma3:4b"
    assert writer.load_config("local").provider == "ollama"


def test_bakeoff_grading_without_a_reader() -> None:
    f = SAMPLES[3]
    card = {k: explain_finding(f, "en")[k] for k in writer.CARD_FIELDS}
    row = grade(f, card, None)
    assert row["checks_pass"] and row["score"] > 0.5
    quiz = {"who": "Ministry of Rural Development", "how_much": "₹590 crore", "what_happened": "x", "proven_wrongdoing": "no", "hard_words": []}
    row = grade(f, card, quiz)
    assert row["who_ok"] and row["number_ok"] and row["fair_ok"]
    bad = grade(f, card, {**quiz, "proven_wrongdoing": "yes", "how_much": "₹77 crore"})
    assert not bad["fair_ok"] and not bad["number_ok"] and bad["score"] < row["score"]


def test_run_endpoint_with_secret_and_no_writer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIWATCHDOGS_PLAIN_SECRET", "s3cret")
    monkeypatch.delenv("AIWATCHDOGS_PLAIN_WRITER", raising=False)
    app = create_app(db_path=tmp_path / "hub.db", seed=True)
    with TestClient(app) as client:
        r = client.post("/api/plain/run", headers={"Authorization": "Bearer s3cret"})
        assert r.status_code == 200 and r.json()["enabled"] is False


def test_writer_saves_checked_text_with_a_fake_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_app(db_path=tmp_path / "hub.db", seed=True)
    with TestClient(app) as client:
        start, _ = news.window(news.today_ist())
        _add(app, "f-w", "verified", start, start, amount=5.895e9)

        def fake(cfg, system, prompt, schema, max_tokens, timeout_s):
            assert timeout_s <= writer.MAX_CALL_S and max_tokens <= 16000
            langs = list(schema["properties"])
            if "lede" in json.dumps(schema):
                return writer.Result({c: {"lede": "Today one finding was confirmed."} for c in langs}, 0.01, "fake")
            data = {c: {"head": "Rural Development left 49.1% unspent", "what": "Only ₹610 crore of ₹1,200 crore was spent.", "why": "Money meant for villages sat unused.", "ask": "What held it back?"} for c in langs}
            data[langs[-1]] = {**data[langs[-1]], "what": "A made-up ₹4,321 crore figure."}
            return writer.Result(data, 0.02, "fake")

        monkeypatch.setattr(writer, "_call_anthropic", fake)
        cfg = writer.Config("anthropic", "claude-haiku-5-5", "low", 5.0, "", ["en", "hi", "ta", "sa"])
        report = writer.run_once(app.state.db, cfg, time_budget_s=600)
        assert report["calls"] >= 2 and report["rejected"] >= 1
        texts = writer.load_texts(app.state.db, ["finding:f-w"])["finding:f-w"]
        assert set(texts["langs"]) == {"en", "hi", "ta"}
        card = client.get("/api/findings/f-w", params={"lang": "en"}).json()["plain"]
        assert card["source"] == "model" and card["head"].startswith("Rural Development")
        sa = client.get("/api/findings/f-w", params={"lang": "sa"}).json()["plain"]
        assert sa["source"] == "template"
        assert writer.spent_today(app.state.db) > 0
        lede = client.get("/api/news").json()["lede"]
        assert lede == "Today one finding was confirmed."


def test_budget_stops_the_writer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_app(db_path=tmp_path / "hub.db", seed=True)
    with TestClient(app):
        start, _ = news.window(news.today_ist())
        _add(app, "f-b", "verified", start, start)
        calls = []
        monkeypatch.setattr(writer, "call_model", lambda *a, **k: calls.append(1) or writer.Result({}, 0.0, "x"))
        cfg = writer.Config("anthropic", "fake", "low", 0.0, "", ["en"])
        writer.run_once(app.state.db, cfg)
        assert calls == []


def test_spending_is_reserved_before_each_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_app(db_path=tmp_path / "hub.db", seed=True)
    with TestClient(app):
        db = app.state.db
        assert writer._reserve(db, "2026-01-01", 0.6, 1.0)
        assert not writer._reserve(db, "2026-01-01", 0.6, 1.0)
        writer._settle(db, "2026-01-01", 0.6, 0.1)
        assert writer._reserve(db, "2026-01-01", 0.6, 1.0)

        calls = []
        monkeypatch.setattr(writer, "_call_anthropic", lambda *a, **k: calls.append(1) or writer.Result({}, 0.0, "x"))
        cfg = writer.Config("anthropic", "claude-opus-5-5", "low", 0.01, "", ["en"])
        with pytest.raises(writer.BudgetExhausted):
            writer.call_model(cfg, "s", "p", {"type": "object"}, max_tokens=4000, db=db)
        assert calls == [] and writer.spent_today(db) == 0.0

        cfg = writer.Config("anthropic", "claude-haiku-5-5", "low", 1.0, "", ["en"])
        monkeypatch.setattr(writer, "_call_anthropic", lambda *a, **k: writer.Result({}, 0.0004, "x"))
        writer.call_model(cfg, "s", "p", {"type": "object"}, max_tokens=4000, db=db)
        assert abs(writer.spent_today(db) - 0.0004) < 1e-9


def test_run_stops_starting_calls_near_the_time_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_app(db_path=tmp_path / "hub.db", seed=True)
    with TestClient(app):
        start, _ = news.window(news.today_ist())
        _add(app, "f-t", "verified", start, start)
        calls = []
        monkeypatch.setattr(writer, "_call_anthropic", lambda *a, **k: calls.append(a[5]) or writer.Result({}, 0.0, "x"))
        cfg = writer.Config("anthropic", "claude-haiku-5-5", "low", 5.0, "", ["en"])
        writer.run_once(app.state.db, cfg, time_budget_s=10.0)
        assert calls == []
        writer.run_once(app.state.db, cfg, time_budget_s=45.0)
        assert calls and max(calls) <= 42.0


def test_final_editions_keep_the_text_they_were_frozen_with(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "hub.db", seed=True)
    with TestClient(app) as client:
        day = date(2026, 11, 4)
        start, _ = news.window(day)
        _add(app, "f-frozen", "verified", start, start, amount=5.895e9)
        before = client.get("/api/news", params={"date": day.isoformat()}).json()
        top = next(s for s in before["sections"] if s["id"] == "top")["stories"][0]
        assert top["source"] == "template"
        with app.state.db.read() as conn:
            row = dict(conn.execute("SELECT * FROM findings WHERE id = 'f-frozen'").fetchone())
        row["anomaly_metrics"] = json.loads(row["anomaly_metrics_json"])
        card = {"head": "A new head", "what": "Only ₹610 crore of ₹1,200 crore was spent.", "why": "w", "ask": "a"}
        writer._save(app.state.db, "finding:f-frozen", source_hash(row), writer.Config("anthropic", "m", "low", 1, "", ["en"]),
                     "m", {"en": card}, {}, 0.0, ["en"])
        after = client.get("/api/news", params={"date": day.isoformat()}).json()
        assert next(s for s in after["sections"] if s["id"] == "top")["stories"][0]["head"] == top["head"]


def test_failed_calls_release_the_hold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_app(db_path=tmp_path / "hub.db", seed=True)
    with TestClient(app):
        db = app.state.db
        cfg = writer.Config("anthropic", "claude-opus-5-5", "low", 5.0, "", ["en"])

        def stopped(*a, **k):
            raise writer.CallFailed("model stopped: max_tokens", 0.002)

        monkeypatch.setattr(writer, "_call_anthropic", stopped)
        for _ in range(30):
            with pytest.raises(writer.CallFailed):
                writer.call_model(cfg, "s", "p", {"type": "object"}, max_tokens=4000, db=db)
        assert abs(writer.spent_today(db) - 0.06) < 1e-6


def test_more_languages_and_corrections_bring_cards_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_app(db_path=tmp_path / "hub.db", seed=True)
    with TestClient(app):
        db = app.state.db
        start, _ = news.window(news.today_ist())
        _add(app, "f-l", "verified", start, start, amount=5.895e9)
        seen: list[list[str]] = []

        def fake(cfg, system, prompt, schema, max_tokens, timeout_s):
            langs = list(schema["properties"])
            if "lede" in json.dumps(schema):
                return writer.Result({c: {"lede": "One finding was confirmed today."} for c in langs}, 0.0, "fake")
            seen.append(langs)
            return writer.Result({c: {"head": "Rural Development left 49.1% unspent", "what": "Only ₹610 crore of ₹1,200 crore was spent.", "why": "w", "ask": "a?"} for c in langs}, 0.0, "fake")

        monkeypatch.setattr(writer, "_call_anthropic", fake)
        cfg = writer.Config("anthropic", "claude-haiku-5-5", "low", 5.0, "", ["en"])
        writer.run_once(db, cfg, time_budget_s=600)
        assert ["en"] in seen
        seen.clear()
        writer.run_once(db, cfg, time_budget_s=600)
        assert seen == []
        writer.run_once(db, writer.Config("anthropic", "claude-haiku-5-5", "low", 5.0, "", ["en", "hi"]), time_budget_s=600)
        assert ["hi"] in seen
        assert set(writer.load_texts(db, ["finding:f-l"])["finding:f-l"]["langs"]) == {"en", "hi"}
