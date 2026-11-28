from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
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


def test_english_plus_21_indian_languages() -> None:
    indian = {"hi", "bn", "mr", "te", "ta", "gu", "ur", "kn", "or", "ml", "pa", "as",
              "mai", "sa", "ne", "kok", "sd", "doi", "ks", "brx", "mni"}
    assert set(CODES) == {"en"} | indian
    assert CODES[0] == "en"
    rtl = {x["code"] for x in available_languages() if x["dir"] == "rtl"}
    assert rtl == {"ur", "sd", "ks"}


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
    ur = explain_finding(SAMPLES[3], "ur")
    assert "کروڑ" in ur["what"]


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
    assert fmt.number(1234567, "ta") == "12,34,567"
    assert fmt.money(6.16e9, "en") == "₹616 crore"
    assert fmt.money(6.16e9, "ne") == "₹616 करोड"
    assert fmt.money(4.5e6, "bn") == "₹45 লাখ"
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
    assert checks.numbers_in("۶۱۶ کروڑ", "ur") == [616.0]


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


def test_projects_lead_with_a_one_line_answer(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "hub.db", seed=True)
    today = news.today_ist()
    start, _ = news.window(today)
    with TestClient(app) as client:
        with app.state.db.read() as conn:
            slug = conn.execute("SELECT slug FROM missions WHERE status != 'archived' ORDER BY id LIMIT 1").fetchone()["slug"]
        before = {m["slug"]: m for m in client.get("/api/missions").json()["missions"]}
        assert before[slug]["plain_answer"] and before[slug]["plain_answer_finding"] is None

        _add(app, "f-review", "peer_review", start)
        mid = {m["slug"]: m for m in client.get("/api/missions").json()["missions"]}
        assert mid[slug]["plain_answer"].startswith("Nothing confirmed yet")

        _add(app, "f-small", "verified", start, start, amount=1e7)
        _add(app, "f-big", "verified", start, start, amount=5.895e9)
        _add(app, "f-hidden", "verified", start, start, quarantined=1, amount=9e12)
        after = {m["slug"]: m for m in client.get("/api/missions").json()["missions"]}
        assert after[slug]["plain_answer_finding"] == "f-big"
        assert "49.1%" in after[slug]["plain_answer"] and "Raw title" not in after[slug]["plain_answer"]

        hi = {m["slug"]: m for m in client.get("/api/missions", params={"lang": "hi"}).json()["missions"]}
        assert hi[slug]["plain_answer"] != after[slug]["plain_answer"] and "49.1%" in hi[slug]["plain_answer"]

        detail = client.get(f"/api/missions/{slug}", params={"lang": "ta"}).json()
        assert detail["plain_answer_finding"] == "f-big"

        head = client.get("/api/head").json()
        assert next(m for m in head["missions"] if m["slug"] == slug)["plain_answer_finding"] == "f-big"


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


def test_zero_or_unprinted_spending_is_never_invented() -> None:
    base = SAMPLES[3]
    zero = {**base, "anomaly_metrics": {**base["anomaly_metrics"], "actual": 0.0, "unspent": base["anomaly_metrics"]["budget_estimate"], "unspent_pct": 100.0}}
    card = explain_finding(zero, "en")
    assert "₹1 " not in card["what"] + " " and "None of it was spent" in card["what"]
    missing = {**zero, "anomaly_metrics": {**zero["anomaly_metrics"], "actual_printed": False}}
    for code in CODES:
        c = explain_finding(missing, code)
        assert c and "100" not in c["head"] and fmt.percent(100, code) not in c["what"]
    assert "no spending figure" in explain_finding(missing, "en")["what"]


def test_repeat_winner_headline_does_not_claim_single_bids() -> None:
    for i in (13, 14):
        head = explain_finding(SAMPLES[i], "en")["head"]
        assert "only bidder" not in head and "won" in head


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
        written = {"finding:f-m": {"hash": writer.source_hash(snap), "model": "m", "langs": {"en": {
            "head": "Rural Development left 49.1% of its money unspent",
            "what": "Only ₹610 crore of ₹1,200 crore was spent.",
            "why": "Money meant for villages sat unused.",
            "ask": "What held it back?",
        }}}}
        r = news.render(ed, "en", written=written)
        top = next(x for x in r["sections"] if x["id"] == "top")
        assert top["stories"][0]["source"] == "model"


def test_custody_records_without_a_person_do_not_claim_one() -> None:
    base = SAMPLES[21]
    rec = {**base, "anomaly_metrics": {**base["anomaly_metrics"], "overrun_mechanism": "institutional_compliance_gap", "overrun_days": None}}
    for code in CODES:
        card = explain_finding(rec, code)
        assert card and card["head"] != explain_finding({**rec, "anomaly_metrics": {**rec["anomaly_metrics"], "overrun_mechanism": "bail_or_release_order_contempt"}}, code)["head"]
    assert "stayed in jail" not in explain_finding(rec, "en")["head"]


def test_single_bid_wording_needs_the_bid_count() -> None:
    base = SAMPLES[12]
    assert "only one bid" in explain_finding(base, "en")["head"]
    unknown = {**base, "anomaly_metrics": {k: v for k, v in base["anomaly_metrics"].items() if k not in ("bids_received", "effective_bids")}}
    assert explain_finding(unknown, "en")["kind"] == "generic"
    many = {**base, "anomaly_metrics": {**base["anomaly_metrics"], "bids_received": 4, "effective_bids": 2}}
    assert explain_finding(many, "en")["kind"] == "generic"
    repeat = SAMPLES[14]
    for code in CODES:
        c, single = explain_finding(repeat, code), explain_finding(base, code)
        assert c["ask"] != single["ask"] and c["why"] != single["why"]
        assert explain_finding(SAMPLES[15], code)["ask"] not in (c["ask"], single["ask"])


def test_number_check_reads_units_and_kinds() -> None:
    f = SAMPLES[3]
    allowed = checks.allowed_numbers(writer.finding_facts(f), explain_finding(f, "en")["what"])
    ok = explain_finding(f, "en")["what"]
    assert checks.bad_numbers(ok, "en", allowed) == []
    crore = next(v for k, v, inr in checks.typed_numbers(ok) if k == "money" and inr >= 1e9)
    assert checks.bad_numbers(f"Only ₹{crore:g} lakh was spent.", "en", allowed) == [crore]
    assert checks.bad_numbers(f"Only ₹{crore:g} crore was spent.", "en", allowed) == []
    hi = explain_finding(f, "hi")["what"]
    assert checks.bad_numbers(hi, "hi", allowed) == []
    assert checks.bad_numbers(hi.replace("करोड़", "लाख"), "hi", allowed)
    pct = next(v for k, v, _ in checks.typed_numbers(ok) if k == "pct") if any(k == "pct" for k, _, _ in checks.typed_numbers(ok)) else 49.1
    assert checks.bad_numbers(f"{crore:g}% of it", "en", allowed) == [crore]
    assert checks.bad_numbers(f"{pct:g}% of it", "en", allowed) == []
    assert checks.typed_numbers("7 કરાર", "gu")[0][0] == "num"


def test_multi_word_lint_entries_match() -> None:
    lint = json.loads((PACK_DIR.parent / "lint.json").read_text(encoding="utf-8"))
    phrases = [(lang, w) for lang, words in lint.items() for w in words if " " in w.strip()]
    assert phrases
    for lang, w in phrases:
        assert w.lower() in [h.lower() for h in checks.lint_hits(f"x {w} y", lang)], (lang, w)
    assert checks.lint_hits("یہ دھوکہ دہی ہے", "ur")
    assert not checks.lint_hits("یہ دھوکہ ہے", "ur") or "دھوکہ" in lint.get("ur", [])


def _correct(app, fid: str, prev: str, new: str, when: str) -> None:
    with app.state.db.transaction() as conn:
        conn.execute("UPDATE findings SET status = ? WHERE id = ?", (new, fid))
        conn.execute(
            "INSERT INTO finding_corrections (finding_id, correction_type, previous_status, new_status, summary, details, "
            "source_evidence_url, reviewed_by, created_at) VALUES (?, 'status', ?, ?, 's', 'd', '', 'editor', ?)",
            (fid, prev, new, when),
        )


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
        writer._save(app.state.db, "finding:f-frozen", writer.source_hash(row), writer.Config("anthropic", "m", "low", 1, "", ["en"]),
                     "m", {"en": card}, {}, 0.0, ["en"])
        after = client.get("/api/news", params={"date": day.isoformat()}).json()
        assert next(s for s in after["sections"] if s["id"] == "top")["stories"][0]["head"] == top["head"]


def test_news_page_negotiates_markdown(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "hub.db", seed=True)
    with TestClient(app) as client:
        for route in ("/news", f"/news/{news.today_ist().isoformat()}"):
            r = client.get(route, headers={"Accept": "text/markdown"})
            assert r.status_code == 200 and r.headers["content-type"].startswith("text/markdown"), route


def test_card_hash_changes_when_metrics_are_corrected() -> None:
    f = SAMPLES[3]
    fixed = {**f, "anomaly_metrics": {**f["anomaly_metrics"], "actual": 1.0}}
    assert writer.source_hash(f) != writer.source_hash(fixed)


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
