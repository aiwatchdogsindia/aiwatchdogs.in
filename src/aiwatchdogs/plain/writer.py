from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..core.safety import scrub_pii_in_obj
from .checks import allowed_numbers, check_entry
from .explain import explain_finding
from .packs import available_languages

log = logging.getLogger("aiwatchdogs.plain")

CARD_FIELDS = ("head", "what", "why", "ask")
LEDE_FIELDS = ("lede",)
LANG_GROUP = 4
DEFAULT_MODELS = {"anthropic": "claude-opus-5-5", "ollama": "gemma3:4b"}
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-1", "claude-fable-5-2"}
PRICES = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-fable-5-2": (8.0, 40.0),
}

STYLE = """You turn findings from AI Watchdogs India, a public audit of Indian government records, into plain language.

Who reads this: any adult in India or abroad, including people who left school early. A 12-year-old should understand every sentence, but it must read like a serious newspaper, never like a children's book.

How to write:
- Short sentences, everyday words, active voice. Explain any technical word in the same sentence, or leave it out.
- Say what was found, in whose records, for which year, and how big it is.
- Neutral and fair. Describe patterns in public records. Never accuse anyone, never say or hint at crime, theft, fraud, bribes or corruption, and never guess at motives.
- Use only the facts given. Do not add numbers, dates, names or causes that are not in the facts.
- Every number you write must come from the facts. You may round it, or write it with the money units shown in the reference text.
- Keep official names of offices, places, exams and schemes. You may write them in the target script if a standard form exists.
- Do not add a disclaimer or say whether the finding is checked. The site adds those lines itself.
- Write each language natively, as a good journalist in that language would. Do not translate word by word from English."""

CARD_TASK = """Write the plain-language card for this finding in each requested language.

Fields:
- head: one headline, at most 14 words, that says the main fact.
- what: two or three short sentences on what the records show, with the key numbers.
- why: one or two sentences on why this matters to ordinary people.
- ask: one fair question a citizen could ask the office responsible.

The reference text below was made by a fixed template from the same facts. It is accurate. Make your version clearer and more natural, but keep every fact and number consistent with it."""

LEDE_TASK = """Write the opening paragraph of today's daily briefing in each requested language.

Field:
- lede: three or four short sentences that tell a busy reader what matters most today, using only the stories and numbers given. Lead with the most important confirmed finding if there is one. If nothing new was confirmed, say so plainly and point to what is being checked."""


@dataclass
class Config:
    provider: str
    model: str
    effort: str
    budget_usd: float
    ollama_url: str
    languages: list[str] = field(default_factory=list)

    @property
    def enabled(self) -> bool:
        return self.provider in ("anthropic", "ollama")


def load_config(provider: str | None = None, model: str | None = None) -> Config:
    prov = (provider or os.environ.get("AIWATCHDOGS_PLAIN_WRITER", "")).strip().lower()
    prov = "ollama" if prov == "local" else prov
    try:
        budget = float(os.environ.get("AIWATCHDOGS_PLAIN_DAILY_BUDGET_USD", "2"))
    except ValueError:
        budget = 2.0
    langs = [x["code"] for x in available_languages()]
    wanted = [c.strip() for c in os.environ.get("AIWATCHDOGS_PLAIN_LANGS", "").split(",") if c.strip()]
    if wanted:
        langs = [c for c in langs if c in wanted] or langs
    return Config(
        provider=prov,
        model=(model or os.environ.get("AIWATCHDOGS_PLAIN_MODEL", "") or DEFAULT_MODELS.get(prov, "")).strip(),
        effort=os.environ.get("AIWATCHDOGS_PLAIN_EFFORT", "low").strip() or "low",
        budget_usd=max(0.0, budget),
        ollama_url=os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/"),
        languages=langs,
    )


def source_hash(f: dict[str, Any]) -> str:
    body = "|".join(
        str(f.get(k) or "")
        for k in ("title", "summary", "entity_name", "fiscal_year", "methodology_code")
    ) + f"|{round(float(f.get('monetary_exposure_inr') or 0.0))}"
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:20]


def _schema(langs: list[str], fields: tuple[str, ...]) -> dict[str, Any]:
    entry = {
        "type": "object",
        "properties": {k: {"type": "string"} for k in fields},
        "required": list(fields),
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {c: entry for c in langs},
        "required": list(langs),
        "additionalProperties": False,
    }


def _lang_lines(langs: list[str]) -> str:
    meta = {x["code"]: x for x in available_languages()}
    return "\n".join(f"- {c}: {meta[c]['name']} ({meta[c]['native']})" for c in langs if c in meta)


@dataclass
class Result:
    data: dict[str, Any]
    cost_usd: float
    model: str


def _cost(model: str, usage: Any) -> float:
    pin, pout = PRICES.get(model, PRICES["claude-opus-5-5"])
    tokens_in = float(getattr(usage, "input_tokens", 0) or 0)
    cache_w = float(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    cache_r = float(getattr(usage, "cache_read_input_tokens", 0) or 0)
    tokens_out = float(getattr(usage, "output_tokens", 0) or 0)
    return (tokens_in * pin + cache_w * pin * 1.25 + cache_r * pin * 0.1 + tokens_out * pout) / 1e6


def _call_anthropic(cfg: Config, system: str, prompt: str, schema: dict[str, Any]) -> Result:
    import anthropic

    client = anthropic.Anthropic(max_retries=2, timeout=120.0)
    kwargs: dict[str, Any] = {
        "model": cfg.model,
        "max_tokens": 32000,
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": prompt}],
        "output_config": {"effort": cfg.effort, "format": {"type": "json_schema", "schema": schema}},
    }
    if cfg.model in FALLBACK_MODELS:
        kwargs["betas"] = ["server-side-fallback-2026-07-01"]
        kwargs["fallbacks"] = "default"
        stream_ctx = client.beta.messages.stream(**kwargs)
    else:
        stream_ctx = client.messages.stream(**kwargs)
    with stream_ctx as stream:
        msg = stream.get_final_message()
    if msg.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"model stopped: {msg.stop_reason}")
    text = next((b.text for b in msg.content if getattr(b, "type", "") == "text"), "")
    return Result(json.loads(text), _cost(cfg.model, msg.usage), str(getattr(msg, "model", cfg.model)))


def _call_ollama(cfg: Config, system: str, prompt: str, schema: dict[str, Any]) -> Result:
    import httpx

    body = {
        "model": cfg.model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        "format": schema,
        "stream": False,
        "options": {"temperature": 0.2},
    }
    with httpx.Client(timeout=600.0) as client:
        resp = client.post(f"{cfg.ollama_url}/api/chat", json=body)
        if resp.status_code == 404:
            client.post(f"{cfg.ollama_url}/api/pull", json={"model": cfg.model, "stream": False}, timeout=3600.0).raise_for_status()
            resp = client.post(f"{cfg.ollama_url}/api/chat", json=body)
        resp.raise_for_status()
        content = resp.json()["message"]["content"]
    return Result(json.loads(content), 0.0, cfg.model)


def call_model(cfg: Config, system: str, prompt: str, schema: dict[str, Any]) -> Result:
    if cfg.provider == "anthropic":
        return _call_anthropic(cfg, system, prompt, schema)
    if cfg.provider == "ollama":
        return _call_ollama(cfg, system, prompt, schema)
    raise RuntimeError("plain writer is not configured")


def finding_facts(f: dict[str, Any]) -> dict[str, Any]:
    metrics = f.get("anomaly_metrics") if isinstance(f.get("anomaly_metrics"), dict) else {}
    slim = {
        k: v for k, v in metrics.items()
        if not str(k).endswith(("_excerpt", "_locator", "_tokens_matched", "_quote")) and k not in ("source_locator",)
    }
    slim, _ = scrub_pii_in_obj(slim)
    return {
        "project_code": f.get("methodology_code"),
        "title": f.get("title"),
        "summary": f.get("summary"),
        "office_or_place": f.get("entity_name"),
        "jurisdiction": f.get("jurisdiction"),
        "year": f.get("fiscal_year"),
        "amount_in_rupees": f.get("monetary_exposure_inr") or None,
        "metrics": json.loads(json.dumps(slim, default=str)),
    }


def write_card(cfg: Config, f: dict[str, Any], langs: list[str]) -> tuple[dict[str, Any], dict[str, Any], float, str]:
    facts = finding_facts(f)
    ref = explain_finding(f, "en") or {}
    prompt = (
        f"{CARD_TASK}\n\nLanguages:\n{_lang_lines(langs)}\n\n"
        f"FACTS:\n{json.dumps(facts, ensure_ascii=False, indent=1)}\n\n"
        f"REFERENCE (English, from the template):\n"
        + json.dumps({k: ref.get(k, "") for k in CARD_FIELDS}, ensure_ascii=False, indent=1)
    )
    res = call_model(cfg, STYLE, prompt, _schema(langs, CARD_FIELDS))
    allowed = allowed_numbers(facts, ref.get("what", ""), ref.get("head", ""))
    good, bad = {}, {}
    for lang in langs:
        entry = res.data.get(lang)
        problems = check_entry(entry, lang, allowed, CARD_FIELDS)
        if problems:
            bad[lang] = problems
        else:
            good[lang] = {k: " ".join(str(entry[k]).split()) for k in CARD_FIELDS}
    return good, bad, res.cost_usd, res.model


def write_lede(cfg: Config, rendered_en: dict[str, Any], langs: list[str]) -> tuple[dict[str, Any], dict[str, Any], float, str]:
    stories = [
        {"section": sec["title"], "head": st["head"], "what": st["what"]}
        for sec in rendered_en.get("sections", [])
        for st in sec.get("stories", [])
    ]
    state = [
        {"project": pr["name"], "facts": pr["lines"]}
        for sec in rendered_en.get("sections", [])
        for pr in sec.get("projects", [])
    ]
    facts = {"date": rendered_en.get("date"), "stats": rendered_en.get("stats"), "stories": stories, "projects": state}
    prompt = (
        f"{LEDE_TASK}\n\nLanguages:\n{_lang_lines(langs)}\n\n"
        f"TODAY:\n{json.dumps(facts, ensure_ascii=False, indent=1)}"
    )
    res = call_model(cfg, STYLE, prompt, _schema(langs, LEDE_FIELDS))
    allowed = allowed_numbers(facts)
    good, bad = {}, {}
    for lang in langs:
        entry = res.data.get(lang)
        problems = check_entry(entry, lang, allowed, LEDE_FIELDS)
        if problems:
            bad[lang] = problems
        else:
            good[lang] = {"lede": " ".join(str(entry["lede"]).split())}
    return good, bad, res.cost_usd, res.model


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def spent_today(db: Any) -> float:
    with db.read() as conn:
        row = conn.execute("SELECT cost_usd FROM plain_spend WHERE day = ?", (_today(),)).fetchone()
    return float(row["cost_usd"] or 0.0) if row is not None else 0.0


def _record_spend(conn: Any, cost: float) -> None:
    conn.execute(
        "INSERT INTO plain_spend (day, cost_usd, calls, updated_at) VALUES (?, ?, 1, ?) "
        "ON CONFLICT(day) DO UPDATE SET cost_usd = plain_spend.cost_usd + excluded.cost_usd, "
        "calls = plain_spend.calls + 1, updated_at = excluded.updated_at",
        (_today(), cost, _now()),
    )


def load_texts(db: Any, keys: list[str]) -> dict[str, dict[str, Any]]:
    keys = [k for k in dict.fromkeys(keys) if k]
    if not keys:
        return {}
    out: dict[str, dict[str, Any]] = {}
    try:
        with db.read() as conn:
            for i in range(0, len(keys), 200):
                chunk = keys[i : i + 200]
                ph = ",".join("?" for _ in chunk)
                for r in conn.execute(
                    f"SELECT key, source_hash, model, payload_json FROM plain_texts WHERE key IN ({ph})", chunk
                ).fetchall():
                    try:
                        payload = json.loads(r["payload_json"] or "{}")
                    except ValueError:
                        payload = {}
                    out[str(r["key"])] = {"hash": str(r["source_hash"]), "model": str(r["model"]), "langs": payload}
    except Exception:
        return {}
    return out


def _save(db: Any, key: str, h: str, cfg: Config, model: str, good: dict[str, Any], bad: dict[str, Any], cost: float, langs_all: list[str]) -> None:
    with db.transaction() as conn:
        row = conn.execute("SELECT source_hash, payload_json, rejected_json, cost_usd FROM plain_texts WHERE key = ?", (key,)).fetchone()
        payload: dict[str, Any] = {}
        rejected: dict[str, Any] = {}
        prev_cost = 0.0
        if row is not None and str(row["source_hash"]) == h:
            payload = json.loads(row["payload_json"] or "{}")
            rejected = json.loads(row["rejected_json"] or "{}")
            prev_cost = float(row["cost_usd"] or 0.0)
        payload.update(good)
        for lang in good:
            rejected.pop(lang, None)
        rejected.update(bad)
        done = all(lang in payload or lang in rejected for lang in langs_all)
        now = _now()
        conn.execute(
            "INSERT INTO plain_texts (key, source_hash, provider, model, payload_json, rejected_json, complete, cost_usd, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET source_hash = excluded.source_hash, "
            "provider = excluded.provider, model = excluded.model, payload_json = excluded.payload_json, "
            "rejected_json = excluded.rejected_json, complete = excluded.complete, cost_usd = excluded.cost_usd, "
            "updated_at = excluded.updated_at",
            (key, h, cfg.provider, model, json.dumps(payload, ensure_ascii=False), json.dumps(rejected, ensure_ascii=False),
             1 if done else 0, prev_cost + cost, now, now),
        )
        if cost:
            _record_spend(conn, cost)


def _missing_langs(db: Any, key: str, h: str, langs: list[str]) -> list[str]:
    with db.read() as conn:
        row = conn.execute("SELECT source_hash, payload_json, rejected_json FROM plain_texts WHERE key = ?", (key,)).fetchone()
    if row is None or str(row["source_hash"]) != h:
        return list(langs)
    have = set(json.loads(row["payload_json"] or "{}")) | set(json.loads(row["rejected_json"] or "{}"))
    return [c for c in langs if c not in have]


def _pending_findings(db: Any, limit: int) -> list[dict[str, Any]]:
    with db.read() as conn:
        rows = conn.execute(
            f"SELECT f.id, f.title, f.summary, f.entity_name, f.jurisdiction, f.fiscal_year, f.methodology_code, "
            f"f.monetary_exposure_inr, f.anomaly_metrics_json, f.rule_triggered, f.status, f.confirm_count, "
            f"f.required_quorum FROM findings f LEFT JOIN plain_texts p ON p.key = 'finding:' || f.id "
            "WHERE COALESCE(f.quarantined, 0) = 0 AND f.status != 'quarantined' AND COALESCE(f.is_demo_sample, 0) = 0 "
            "AND (p.key IS NULL OR p.complete = 0) "
            "ORDER BY CASE f.status WHEN 'verified' THEN 0 WHEN 'rti_filed' THEN 0 WHEN 'cleared' THEN 1 "
            "WHEN 'resolved_explained' THEN 1 WHEN 'peer_review' THEN 2 ELSE 3 END, "
            "COALESCE(f.verified_at, f.created_at) DESC, f.id ASC LIMIT ?",
            (limit,),
        ).fetchall()
    out = []
    for r in rows:
        f = dict(r)
        try:
            f["anomaly_metrics"] = json.loads(f.pop("anomaly_metrics_json") or "{}")
        except ValueError:
            f["anomaly_metrics"] = {}
        out.append(f)
    return out


def run_once(db: Any, cfg: Config | None = None, *, time_budget_s: float = 40.0, base_url: str = "https://aiwatchdogs.in") -> dict[str, Any]:
    cfg = cfg or load_config()
    report: dict[str, Any] = {"enabled": cfg.enabled, "provider": cfg.provider, "model": cfg.model, "calls": 0, "written": 0, "rejected": 0, "spent_usd": 0.0}
    if not cfg.enabled or getattr(db, "read_only", False):
        return report
    start = time.monotonic()

    def time_left() -> bool:
        return time.monotonic() - start < time_budget_s * 0.4

    def budget_left() -> bool:
        return cfg.provider == "ollama" or spent_today(db) < cfg.budget_usd

    from . import news as plain_news

    try:
        d = plain_news.today_ist()
        edition = plain_news.get_edition(db, d)
        h = plain_news.lede_hash(edition)
        key = f"news:{d.isoformat()}"
        missing = _missing_langs(db, key, h, cfg.languages)
        if missing and budget_left():
            rendered = plain_news.render(edition, "en", base_url)
            for i in range(0, len(missing), LANG_GROUP * 2):
                if not (time_left() and budget_left()):
                    break
                group = missing[i : i + LANG_GROUP * 2]
                good, bad, cost, model = write_lede(cfg, rendered, group)
                _save(db, key, h, cfg, model, good, bad, cost, cfg.languages)
                report["calls"] += 1
                report["written"] += len(good)
                report["rejected"] += len(bad)
                report["spent_usd"] += cost
    except Exception as exc:
        log.warning("plain writer lede failed: %s", exc)
        report["error"] = str(exc)[:200]

    for f in _pending_findings(db, 25):
        if not (time_left() and budget_left()):
            break
        key, h = f"finding:{f['id']}", source_hash(f)
        missing = _missing_langs(db, key, h, cfg.languages)
        for i in range(0, len(missing), LANG_GROUP):
            if not (time_left() and budget_left()):
                break
            group = missing[i : i + LANG_GROUP]
            try:
                good, bad, cost, model = write_card(cfg, f, group)
            except Exception as exc:
                log.warning("plain writer failed for %s: %s", f["id"], exc)
                report["error"] = str(exc)[:200]
                return report
            _save(db, key, h, cfg, model, good, bad, cost, cfg.languages)
            report["calls"] += 1
            report["written"] += len(good)
            report["rejected"] += len(bad)
            report["spent_usd"] += cost
    report["spent_usd"] = round(report["spent_usd"], 4)
    report["spent_today_usd"] = round(spent_today(db), 4) if cfg.provider == "anthropic" else 0.0
    return report


def start_background_loop(db: Any, interval_s: float = 120.0) -> threading.Thread | None:
    cfg = load_config()
    if not cfg.enabled or os.environ.get("VERCEL") or os.environ.get("AIWATCHDOGS_PLAIN_LOOP", "1") == "0":
        return None

    def loop() -> None:
        while True:
            try:
                run_once(db, cfg, time_budget_s=600.0)
            except Exception as exc:
                log.warning("plain writer loop error: %s", exc)
            time.sleep(interval_s)

    t = threading.Thread(target=loop, name="plain-writer", daemon=True)
    t.start()
    return t
