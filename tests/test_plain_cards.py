from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from plain_samples import SAMPLES
from plain_samples import add_finding as _add

from aiwatchdogs.hub.app import create_app
from aiwatchdogs.plain import (
    available_languages,
    checks,
    explain_finding,
    fmt,
    resolve_lang,
)
from aiwatchdogs.plain.explain import source_hash
from aiwatchdogs.plain.packs import PACK_DIR, flatten, placeholders

EN = json.loads((PACK_DIR / "en.json").read_text(encoding="utf-8"))
EN_FLAT = flatten(EN)
META = {"code", "name", "native", "dir", "bcp47"}
CODES = [x["code"] for x in available_languages()]
CARD_FIELDS = ("head", "what", "why", "ask")


def news_window() -> tuple[str, str]:
    now = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=5, minutes=30)
    start = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    return start, start


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
        allowed = checks.allowed_numbers(f, explain_finding(f, "en")["what"])
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
    allowed = checks.allowed_numbers(f, explain_finding(f, "en")["what"])
    good = {"head": "Delhi Police plans 37.7% more", "what": "The plan is ₹616 crore, up from ₹447 crore.", "why": "Plans matter.", "ask": "Why the change?"}
    assert checks.check_entry(good, "en", allowed, CARD_FIELDS) == []
    made_up = {**good, "what": "The plan is ₹999 crore."}
    assert any("numbers" in p for p in checks.check_entry(made_up, "en", allowed, CARD_FIELDS))
    loaded = {**good, "why": "This looks like a scam."}
    assert any("loaded" in p for p in checks.check_entry(loaded, "en", allowed, CARD_FIELDS))
    hindi = {**good, "why": "यह घोटाला है।"}
    assert any("loaded" in p for p in checks.check_entry(hindi, "hi", allowed, CARD_FIELDS))
    assert checks.numbers_in("६१६ करोड़", "hi") == [616.0]
    assert checks.numbers_in("۶۱۶ کروڑ", "ur") == [616.0]


def test_model_text_is_used_only_when_it_matches_the_record() -> None:
    f = SAMPLES[0]
    written = {
        "hash": source_hash(f),
        "model": "test-model",
        "langs": {"en": {"head": "H", "what": "W", "why": "Y", "ask": "A"}},
    }
    card = explain_finding(f, "en", written)
    assert card["head"] == "H" and card["source"] == "model"
    assert "Being checked" in card["status"] and card["not"].startswith("This is a pattern")
    stale = {**written, "hash": "different"}
    assert explain_finding(f, "en", stale)["source"] == "template"
    assert explain_finding(f, "hi", written)["source"] == "template"


def test_projects_lead_with_a_one_line_answer(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "hub.db", seed=True)
    start, _ = news_window()
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
    allowed = checks.allowed_numbers(f, explain_finding(f, "en")["what"])
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


def test_card_hash_changes_when_metrics_are_corrected() -> None:
    f = SAMPLES[3]
    fixed = {**f, "anomaly_metrics": {**f["anomaly_metrics"], "actual": 1.0}}
    assert source_hash(f) != source_hash(fixed)


def test_findings_api_has_plain_cards(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "hub.db", seed=True)
    with TestClient(app) as client:
        start, _ = news_window()
        _add(app, "f-new", "verified", start, start, amount=5.895e9)
        lst = client.get("/api/findings", params={"lang": "ta"}).json()
        plain = {f["id"]: f["plain"] for f in lst["findings"]}
        assert plain["f-new"]["lang"] == "ta" and plain["f-new"]["source"] == "template"
        detail = client.get("/api/findings/f-new", params={"lang": "bn"}).json()
        assert detail["plain"]["lang"] == "bn"

        assert client.get("/api/plain/languages").json()["languages"][0]["code"] == "en"
        assert client.get("/api/plain/labels", params={"lang": "ur"}).json()["dir"] == "rtl"
