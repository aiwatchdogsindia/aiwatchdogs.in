from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from plain_samples import SAMPLES

from aiwatchdogs.hub.app import create_app
from aiwatchdogs.plain import available_languages, explain_finding, resolve_lang
from aiwatchdogs.plain import checks, fmt, news, writer
from aiwatchdogs.plain.bakeoff import grade
from aiwatchdogs.plain.packs import PACK_DIR, flatten, placeholders

EN = json.loads((PACK_DIR / "en.json").read_text(encoding="utf-8"))
EN_FLAT = flatten(EN)
META = {"code", "name", "native", "dir", "bcp47"}
CODES = [x["code"] for x in available_languages()]


def _pack(code: str) -> dict:
    return json.loads((PACK_DIR / f"{code}.json").read_text(encoding="utf-8"))


def test_at_least_twenty_languages_including_indian_ones() -> None:
    assert len(CODES) >= 20
    for code in ("en", "hi", "bn", "mr", "te", "ta", "gu", "ur", "kn", "ml", "pa", "or", "es", "fr", "ar", "zh"):
        assert code in CODES


@pytest.mark.parametrize("code", CODES)
def test_every_pack_has_every_key(code: str) -> None:
    flat = flatten(_pack(code))
    keys = {k for k in EN_FLAT if not k.startswith(("num.", "money."))}
    have = {k for k in flat if not k.startswith(("num.", "money."))}
    assert keys - have == set(), f"{code} is missing {sorted(keys - have)}"
    assert have - keys == set(), f"{code} has extra {sorted(have - keys)}"


@pytest.mark.parametrize("code", CODES)
def test_placeholders_match_english(code: str) -> None:
    flat = flatten(_pack(code))
    for key, en_text in EN_FLAT.items():
        if key in META or key.startswith(("num.", "money.")):
            continue
        assert placeholders(flat[key]) == placeholders(en_text), f"{code}:{key}"
        assert flat[key].strip(), f"{code}:{key} is empty"


@pytest.mark.parametrize("code", CODES)
def test_every_sample_renders_in_every_language(code: str) -> None:
    for f in SAMPLES:
        card = explain_finding(f, code)
        assert card is not None
        for k in ("head", "what", "why", "ask", "status", "not"):
            assert card[k] and "{" not in card[k], (code, f["id"], k)


def test_kinds_are_chosen_from_the_data() -> None:
    kinds = [explain_finding(f, "en")["kind"] for f in SAMPLES]
    assert kinds.count("generic") == 2
    for k in ("budget_gap", "unspent", "double_bill", "teachers", "promise_pending", "one_bid", "promise_changed", "exam", "custody", "website"):
        assert k in kinds


def test_numbers_come_from_the_record() -> None:
    card = explain_finding(SAMPLES[0], "en")
    assert card["head"] == "Delhi Police: the plan for next year is 37.7% higher than the revised plan"
    assert "₹616 crore" in card["what"] and "₹447 crore" in card["what"]
    hi = explain_finding(SAMPLES[0], "hi")
    assert "करोड़" in hi["what"] and "37.7%" in hi["head"]
    zh = explain_finding(SAMPLES[3], "zh")
    assert "亿" in zh["what"]


@pytest.mark.parametrize("code", CODES)
def test_template_output_passes_the_checks(code: str) -> None:
    for f in SAMPLES:
        card = explain_finding(f, code)
        allowed = checks.allowed_numbers(writer.finding_facts(f), explain_finding(f, "en")["what"])
        for k in ("head", "what", "why", "ask"):
            assert checks.bad_numbers(card[k], code, allowed) == [], (code, f["id"], k, card[k])
            assert checks.lint_hits(card[k], code) == [], (code, f["id"], k)


def test_english_reads_at_a_12_year_old_level() -> None:
    body = " ".join(" ".join(explain_finding(f, "en")[k] for k in ("what", "why", "ask")) for f in SAMPLES)
    assert checks.grade_level(body) <= 8.0
    explainers = " ".join(v["body"] for v in EN["explainers"].values())
    assert checks.grade_level(explainers) <= 10.0


def test_quarantined_findings_are_not_explained() -> None:
    assert explain_finding({**SAMPLES[0], "quarantined": True}, "en") is None


def test_formatting() -> None:
    assert fmt.number(1234567, "en") == "12,34,567"
    assert fmt.number(1234567, "fr") != "12,34,567"
    assert fmt.money(6.16e9, "en") == "₹616 crore"
    assert "milliard" in fmt.money(6.16e9, "fr")
    assert "亿" in fmt.money(6.16e9, "zh")
    assert fmt.percent(-49.68, "en") == "49.7%"


def test_resolve_lang() -> None:
    assert resolve_lang("hi-IN,en;q=0.8") == "hi"
    assert resolve_lang("xx") == "en"
    assert resolve_lang(None) == "en"
    assert resolve_lang("ta") == "ta"


def test_checks_catch_made_up_numbers_and_loaded_words() -> None:
    f = SAMPLES[0]
    allowed = checks.allowed_numbers(writer.finding_facts(f), explain_finding(f, "en")["what"])
    good = {"head": "Delhi Police plans 37.7% more", "what": "The plan is ₹616 crore, up from ₹447 crore.", "why": "Plans matter.", "ask": "Why the change?"}
    assert checks.check_entry(good, "en", allowed, writer.CARD_FIELDS) == []
    made_up = {**good, "what": "The plan is ₹999 crore."}
    assert any("numbers" in p for p in checks.check_entry(made_up, "en", allowed, writer.CARD_FIELDS))
    loaded = {**good, "why": "This looks like a scam."}
    assert any("loaded" in p for p in checks.check_entry(loaded, "en", allowed, writer.CARD_FIELDS))
    hindi = {**good, "why": "यह घोटाला है।"}
    assert any("loaded" in p for p in checks.check_entry(hindi, "hi", allowed, writer.CARD_FIELDS))
    assert checks.numbers_in("६१६ करोड़", "hi") == [616.0]
    assert checks.numbers_in("6,16 milliards", "fr") == [6.16]


def test_model_text_is_used_only_when_it_matches_the_record() -> None:
    f = SAMPLES[0]
    written = {
        "hash": writer.source_hash(f),
        "model": "test-model",
        "langs": {"en": {"head": "H", "what": "W", "why": "Y", "ask": "A"}},
    }
    card = explain_finding(f, "en", written)
    assert card["head"] == "H" and card["source"] == "model"
    assert "Being checked" in card["status"] and card["not"].startswith("This is a pattern")
    stale = {**written, "hash": "different"}
    assert explain_finding(f, "en", stale)["source"] == "template"
    assert explain_finding(f, "hi", written)["source"] == "template"


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


def _add(app, fid: str, status: str, created: str, verified: str | None = None, quarantined: int = 0, amount: float = 0.0) -> None:
    metrics = json.dumps(SAMPLES[3]["anomaly_metrics"])
    with app.state.db.transaction() as conn:
        m = conn.execute("SELECT id, slug FROM missions WHERE status != 'archived' ORDER BY id LIMIT 1").fetchone()
        conn.execute(
            "INSERT INTO findings (id, mission_id, mission_slug, pillar, title, summary, methodology_code, entity_name, "
            "jurisdiction, fiscal_year, submitted_by, status, quarantined, is_demo_sample, created_at, verified_at, "
            "anomaly_metrics_json, monetary_exposure_inr, confirm_count) "
            "VALUES (?, ?, ?, 'budget', ?, 's', 'UB1', 'Ministry of Rural Development', 'Union of India', 'FY2024-25', "
            "'node', ?, ?, 0, ?, ?, ?, ?, 3)",
            (fid, m["id"], m["slug"], f"Raw title {fid}", status, quarantined, created, verified, metrics, amount),
        )


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

        lst = client.get("/api/findings", params={"lang": "ta"}).json()
        plain = {f["id"]: f["plain"] for f in lst["findings"]}
        assert plain["f-new"]["lang"] == "ta" and plain["f-new"]["source"] == "template"
        detail = client.get("/api/findings/f-new", params={"lang": "bn"}).json()
        assert detail["plain"]["lang"] == "bn"

        assert client.get("/api/plain/languages").json()["languages"][0]["code"] == "en"
        assert client.get("/api/plain/labels", params={"lang": "ur"}).json()["dir"] == "rtl"
        assert client.post("/api/plain/run").status_code == 401


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

        def fake(cfg, system, prompt, schema):
            langs = list(schema["properties"])
            if "lede" in json.dumps(schema):
                return writer.Result({c: {"lede": "Today one finding was confirmed."} for c in langs}, 0.01, "fake")
            data = {c: {"head": "Rural Development left 49.1% unspent", "what": "Only ₹610 crore of ₹1,200 crore was spent.", "why": "Money meant for villages sat unused.", "ask": "What held it back?"} for c in langs}
            data[langs[-1]] = {**data[langs[-1]], "what": "A made-up ₹4,321 crore figure."}
            return writer.Result(data, 0.02, "fake")

        monkeypatch.setattr(writer, "call_model", fake)
        cfg = writer.Config("anthropic", "fake", "low", 5.0, "", ["en", "hi", "ta", "fr"])
        report = writer.run_once(app.state.db, cfg, time_budget_s=600)
        assert report["calls"] >= 2 and report["rejected"] >= 1
        texts = writer.load_texts(app.state.db, ["finding:f-w"])["finding:f-w"]
        assert set(texts["langs"]) == {"en", "hi", "ta"}
        card = client.get("/api/findings/f-w", params={"lang": "en"}).json()["plain"]
        assert card["source"] == "model" and card["head"].startswith("Rural Development")
        fr = client.get("/api/findings/f-w", params={"lang": "fr"}).json()["plain"]
        assert fr["source"] == "template"
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
