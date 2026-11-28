from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .checks import allowed_numbers, check_entry, grade_level, numbers_in, number_ok
from .explain import explain_finding
from .writer import CARD_FIELDS, Config, call_model, finding_facts, load_config, write_card

READER_SYSTEM = """You are an ordinary reader with no special knowledge of government, law or finance. You left school at 14.
Read the short news card and answer only from what it says. If the card does not say something, answer "not stated".
Do not use outside knowledge."""

READER_TASK = """Answer these questions about the card.
- who: which government office, place, exam or scheme is it about?
- how_much: the single most important number in the card, with its unit, or "not stated".
- what_happened: one sentence in your own simple words.
- proven_wrongdoing: does the card say someone has been proven to break the law? Answer yes, no, or unclear.
- hard_words: every word or phrase in the card that you did not understand. An empty list if none."""

READER_SCHEMA = {
    "type": "object",
    "properties": {
        "who": {"type": "string"},
        "how_much": {"type": "string"},
        "what_happened": {"type": "string"},
        "proven_wrongdoing": {"type": "string", "enum": ["yes", "no", "unclear"]},
        "hard_words": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["who", "how_much", "what_happened", "proven_wrongdoing", "hard_words"],
    "additionalProperties": False,
}

_STOP = {"of", "the", "and", "for", "in", "department", "ministry", "government", "india", "union", "state", "office"}


@dataclass
class Candidate:
    label: str
    cfg: Config | None


def parse_candidates(spec: str) -> list[Candidate]:
    out: list[Candidate] = []
    for raw in [s.strip() for s in spec.split(",") if s.strip()]:
        if raw == "template":
            out.append(Candidate("template", None))
            continue
        provider, _, model = raw.partition(":")
        out.append(Candidate(raw, load_config(provider, model or None)))
    return out


def card_text(card: dict[str, Any]) -> str:
    return "\n".join(str(card.get(k, "")) for k in ("head", "what", "why"))


def _who_ok(answer: str, f: dict[str, Any]) -> bool:
    ans = answer.lower()
    names = [str(f.get(k) or "") for k in ("entity_name", "jurisdiction")]
    metrics = f.get("anomaly_metrics") if isinstance(f.get("anomaly_metrics"), dict) else {}
    names += [str(metrics.get(k) or "") for k in ("state_name", "ministry", "exam_name", "scheme_name", "authority", "state_ut")]
    for name in names:
        tokens = [t for t in re.findall(r"[a-z0-9+]+", name.lower()) if len(t) > 2 and t not in _STOP]
        if tokens and any(t in ans for t in tokens):
            return True
    return False


def grade(f: dict[str, Any], card: dict[str, Any], quiz: dict[str, Any] | None, lang: str = "en") -> dict[str, Any]:
    facts = finding_facts(f)
    ref = explain_finding(f, "en") or {}
    allowed = allowed_numbers(facts, ref.get("what", ""), ref.get("head", ""))
    problems = check_entry(card, lang, allowed, CARD_FIELDS)
    g = grade_level(" ".join(str(card.get(k, "")) for k in ("what", "why", "ask"))) if lang == "en" else None
    has_numbers = bool(numbers_in(ref.get("what", "") + " " + ref.get("head", "")))
    row: dict[str, Any] = {
        "checks_pass": not problems,
        "problems": problems,
        "grade": round(g, 1) if g is not None else None,
    }
    parts = [1.0 if not problems else 0.0]
    if g is not None:
        parts.append(max(0.0, min(1.0, (14.0 - g) / 6.0)))
    if quiz:
        who = _who_ok(str(quiz.get("who", "")), f)
        nums = numbers_in(str(quiz.get("how_much", "")))
        how = any(number_ok(n, allowed) for n in nums) if has_numbers else True
        fair = str(quiz.get("proven_wrongdoing", "")) in ("no", "unclear")
        hard = [w for w in quiz.get("hard_words") or [] if str(w).strip()]
        row.update({"who_ok": who, "number_ok": how, "fair_ok": fair, "hard_words": hard})
        parts += [float(who), float(how), float(fair), max(0.0, 1.0 - len(hard) / 5.0)]
    row["score"] = round(sum(parts) / len(parts), 3)
    return row


def ask_reader(reader: Config, card: dict[str, Any]) -> tuple[dict[str, Any], float]:
    res = call_model(reader, READER_SYSTEM, f"{READER_TASK}\n\nCARD:\n{card_text(card)}", READER_SCHEMA)
    return res.data, res.cost_usd


def run_bakeoff(
    findings: list[dict[str, Any]],
    candidates: list[Candidate],
    reader: Config | None,
    lang: str = "en",
) -> dict[str, Any]:
    results: dict[str, Any] = {c.label: {"rows": [], "cost_usd": 0.0} for c in candidates}
    samples: list[dict[str, Any]] = []
    reader_cost = 0.0
    for f in findings:
        sample = {"finding_id": f.get("id"), "original_title": f.get("title"), "cards": {}}
        for c in candidates:
            if c.cfg is None:
                card = explain_finding(f, lang) or {}
                card = {k: card.get(k, "") for k in CARD_FIELDS}
            else:
                try:
                    good, bad, cost, _model = write_card(c.cfg, f, [lang])
                except Exception as exc:
                    results[c.label]["rows"].append({"finding_id": f.get("id"), "error": str(exc)[:200], "score": 0.0})
                    continue
                results[c.label]["cost_usd"] += cost
                card = good.get(lang) or {}
                if not card:
                    results[c.label]["rows"].append({"finding_id": f.get("id"), "checks_pass": False, "problems": bad.get(lang, []), "score": 0.0})
                    sample["cards"][c.label] = {"rejected": bad.get(lang, [])}
                    continue
            quiz = None
            if reader is not None and reader.enabled:
                try:
                    quiz, rc = ask_reader(reader, card)
                    reader_cost += rc
                except Exception as exc:
                    quiz = None
                    sample.setdefault("reader_errors", []).append(str(exc)[:120])
            row = {"finding_id": f.get("id"), **grade(f, card, quiz, lang)}
            results[c.label]["rows"].append(row)
            sample["cards"][c.label] = {**card, "quiz": quiz, "score": row["score"]}
        samples.append(sample)
    summary = []
    for label, data in results.items():
        rows = data["rows"]
        n = max(1, len(rows))

        def rate(key: str) -> float | None:
            vals = [r[key] for r in rows if key in r and r[key] is not None]
            return round(sum(float(v) for v in vals) / len(vals), 3) if vals else None

        summary.append({
            "candidate": label,
            "findings": len(rows),
            "score": round(sum(r.get("score", 0.0) for r in rows) / n, 3),
            "checks_pass": rate("checks_pass"),
            "who_ok": rate("who_ok"),
            "number_ok": rate("number_ok"),
            "fair_ok": rate("fair_ok"),
            "avg_grade": rate("grade"),
            "avg_hard_words": round(sum(len(r.get("hard_words") or []) for r in rows) / n, 2),
            "cost_usd": round(data["cost_usd"], 4),
        })
    summary.sort(key=lambda s: -s["score"])
    return {"lang": lang, "summary": summary, "reader_cost_usd": round(reader_cost, 4), "samples": samples, "details": results}


def estimate_cost(n_findings: int, candidates: list[Candidate], reader: Config | None) -> float:
    from .writer import PRICES

    total = 0.0
    for c in candidates:
        if c.cfg is None or c.cfg.provider != "anthropic":
            continue
        pin, pout = PRICES.get(c.cfg.model, PRICES["claude-opus-5-5"])
        total += n_findings * (2500 * pin + 1500 * pout) / 1e6
    if reader is not None and reader.provider == "anthropic":
        pin, pout = PRICES.get(reader.model, PRICES["claude-opus-5-5"])
        total += n_findings * len(candidates) * (600 * pin + 500 * pout) / 1e6
    return round(total, 2)


def to_markdown(report: dict[str, Any]) -> str:
    lines = [f"# Plain-language bake-off ({report['lang']})", "", "| Candidate | Score | Checks pass | Reader got who | Reader got number | Reader saw no proof claim | Reading grade | Hard words per card | Cost |", "|---|---|---|---|---|---|---|---|---|"]
    for s in report["summary"]:
        lines.append(
            f"| {s['candidate']} | {s['score']} | {s['checks_pass']} | {s['who_ok']} | {s['number_ok']} | {s['fair_ok']} | {s['avg_grade']} | {s['avg_hard_words']} | ${s['cost_usd']} |"
        )
    lines += ["", f"Reader cost: ${report['reader_cost_usd']}", "", "## Side by side", ""]
    for sm in report["samples"]:
        lines += [f"### {sm['finding_id']}", "", f"Original title: {sm['original_title']}", ""]
        for label, card in sm["cards"].items():
            if "rejected" in card:
                lines += [f"**{label}**: rejected by checks: {'; '.join(card['rejected'])}", ""]
                continue
            lines += [f"**{label}** (score {card.get('score')})", "", f"> {card.get('head', '')}", ">", f"> {card.get('what', '')}", ">", f"> {card.get('why', '')}", ""]
    return "\n".join(lines)


def dumps(report: dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2, default=str)
