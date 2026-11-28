from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable

from . import fmt
from .packs import fill, pack, section, text

KIND_BY_CODE = {
    "P1": "budget_gap",
    "UB1": "unspent",
    "P3": "unspent",
    "OR1": "double_bill",
    "P4": "double_bill",
    "P5": "teachers",
    "P6": "promise_pending",
    "P7": "one_bid",
    "P8": "promise_changed",
    "P9": "exam",
    "P10": "custody",
    "W1": "website",
    "P2": "website",
}
KINDS = (
    "budget_gap", "unspent", "double_bill", "teachers", "promise_pending",
    "one_bid", "promise_changed", "exam", "custody", "website", "generic",
)
UNIT_INR = {"crore": 1e7, "lakh": 1e5, "thousand": 1e3, "rupee": 1.0}
STATUS_KEY = {
    "peer_review": "peer_review",
    "verified": "verified",
    "rti_filed": "verified",
    "cleared": "cleared",
    "resolved_explained": "cleared",
    "disputed": "disputed",
    "retracted": "retracted",
    "retracted_false_positive": "retracted",
    "under_review": "under_review",
}
Frame = tuple[str, str, str, dict[str, Any], list[tuple[str, dict[str, Any]]]]


def _metrics(f: dict[str, Any]) -> dict[str, Any]:
    m = f.get("anomaly_metrics")
    if isinstance(m, str):
        try:
            m = json.loads(m)
        except ValueError:
            m = {}
    return m if isinstance(m, dict) else {}


def _num(v: Any) -> float | None:
    try:
        out = float(v)
    except (TypeError, ValueError):
        return None
    return out if out == out and abs(out) != float("inf") else None


def _clip(v: Any, n: int = 110) -> str:
    s = re.sub(r"\s+", " ", str(v or "")).strip().strip('"').strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _year(f: dict[str, Any], *candidates: Any) -> str:
    for c in candidates:
        s = str(c or "").strip()
        if s:
            return s[2:] if s.upper().startswith("FY") else s
    s = str(f.get("fiscal_year") or "").strip()
    return s[2:] if s.upper().startswith("FY") else s


def _entity(f: dict[str, Any], *candidates: Any) -> str:
    for c in (*candidates, f.get("entity_name"), f.get("jurisdiction")):
        s = _clip(c, 80)
        if s:
            return s
    return ""


def _budget_gap(f: dict[str, Any], m: dict[str, Any], lang: str) -> Frame | None:
    unit = UNIT_INR.get(str(m.get("unit") or "crore"), 1e7)
    entity = _entity(f)
    if str(f.get("rule_triggered") or "") == "P1-RE-EQUALS-BE":
        be = _num(m.get("be"))
        if be is None:
            return None
        return "budget_gap", "head_same", "what_same", {"entity": entity, "amount": fmt.money(be * unit, lang)}, []
    compare = str(m.get("compare") or "")
    if "_vs_" not in compare:
        return None
    a_name, b_name = compare.split("_vs_", 1)
    a, b, pct = _num(m.get(a_name)), _num(m.get(b_name)), _num(m.get("pct_difference"))
    if a is None or b is None or pct is None:
        return None
    terms = section(lang, "terms")
    if a_name not in terms or b_name not in terms:
        return None
    slots = {
        "entity": entity,
        "a_label": terms[a_name],
        "b_label": terms[b_name],
        "a_amount": fmt.money(a * unit, lang) or fmt.number(0, lang),
        "b_amount": fmt.money(b * unit, lang) or fmt.number(0, lang),
        "pct": fmt.percent(pct, lang),
        "limit": fmt.percent(_num(m.get("threshold_pct")) or 10, lang),
    }
    return "budget_gap", "head_more" if pct >= 0 else "head_less", "what", slots, []


def _unspent(f: dict[str, Any], m: dict[str, Any], lang: str) -> Frame | None:
    entity, year = _entity(f), _year(f)
    be = _num(m.get("budget_estimate"))
    if be is None:
        return None
    if str(m.get("compare")) == "actual_vs_budget_estimate":
        actual, unspent, pct = _num(m.get("actual")), _num(m.get("unspent")), _num(m.get("unspent_pct"))
        if actual is None or unspent is None or pct is None:
            return None
        budget = fmt.money(be * 1e7, lang)
        if m.get("actual_printed") is False:
            return "unspent", "head_none", "what_none", {"entity": entity, "year": year, "budget": budget}, []
        slots = {
            "entity": entity, "year": year, "pct": fmt.percent(pct, lang),
            "budget": budget,
            "unspent": fmt.money(unspent * 1e7, lang),
        }
        actual_text = fmt.money(actual * 1e7, lang)
        if not actual_text:
            return "unspent", "head", "what_zero", slots, []
        return "unspent", "head", "what", {**slots, "actual": actual_text}, []
    rev, cut, pct = _num(m.get("revised_estimate")), _num(m.get("revised_cut")), _num(m.get("revised_cut_pct"))
    if rev is None or cut is None or pct is None:
        return None
    slots = {
        "entity": entity, "year": year, "pct": fmt.percent(pct, lang),
        "budget": fmt.money(be * 1e7, lang),
        "revised": fmt.money(rev * 1e7, lang) or fmt.number(0, lang),
        "cut": fmt.money(cut * 1e7, lang),
    }
    return "unspent", "head_cut", "what_cut", slots, []


def _double_bill(f: dict[str, Any], m: dict[str, Any], lang: str) -> Frame | None:
    dup = str(m.get("signal_category") or "") == "duplicate_payment_or_billing"
    extra: list[tuple[str, dict[str, Any]]] = []
    amount = _num(m.get("amount_inr")) if m.get("amount_disclosed") else None
    if amount and amount > 0:
        extra.append(("amount_line", {"amount": fmt.money(amount, lang)}))
    return (
        "double_bill",
        "head_dup" if dup else "head_dual",
        "what_dup" if dup else "what_dual",
        {"entity": _entity(f)},
        extra,
    )


def _teachers(f: dict[str, Any], m: dict[str, Any], lang: str) -> Frame | None:
    state = _entity(f, m.get("state_name"))
    year = _year(f, m.get("academic_year"))
    if str(m.get("signal_category") or "") == "gov_primary_understaffed":
        tps = _num(m.get("gov_primary_teachers_per_school"))
        if tps is None:
            return None
        return "teachers", "head_staff", "what_staff", {"state": state, "year": year, "tps": fmt.number(tps, lang, 1)}, []
    single, share, spt = (
        _num(m.get("single_teacher_schools")),
        _num(m.get("single_teacher_share_pct")),
        _num(m.get("students_per_single_teacher")),
    )
    if single is None or share is None or spt is None:
        return None
    slots = {
        "state": state, "year": year, "share": fmt.percent(share, lang),
        "single": fmt.number(single, lang), "spt": fmt.number(round(spt), lang),
    }
    return "teachers", "head", "what", slots, []


def _promise_pending(f: dict[str, Any], m: dict[str, Any], lang: str) -> Frame | None:
    sig = str(m.get("signal_category") or "")
    slots = {"entity": _entity(f, m.get("ministry")), "subject": _clip(m.get("subject"), 120)}
    if sig == "partly_implemented_assurance":
        return "promise_pending", "head_partly", "what_partly", slots, []
    if sig == "dropped_without_fulfillment":
        return "promise_pending", "head_dropped", "what_dropped", slots, []
    days = _num(m.get("days_elapsed"))
    if days is None:
        return None
    slots["days"] = fmt.number(days, lang)
    return "promise_pending", "head", "what", slots, []


def _single_bid(m: dict[str, Any]) -> bool:
    for key in ("effective_bids", "technically_qualified_bids", "qualified_bid_count", "bids_received", "number_of_bids"):
        n = _num(m.get(key))
        if n is not None:
            return n == 1
    return False


def _one_bid(f: dict[str, Any], m: dict[str, Any], lang: str) -> Frame | None:
    sig = str(m.get("signal_category") or "")
    entity = _entity(f, m.get("authority"))
    title = _clip(m.get("tender_title") or f.get("title"), 110)
    slots: dict[str, Any] = {"entity": entity, "title": title}
    single = _single_bid(m)
    wins = _num(m.get("winner_total_wins"))
    if sig in ("single_bidder_repeat_winner", "repeat_winner_concentration") and wins and wins > 1:
        slots["wins"] = fmt.number(wins, lang)
        return "one_bid", "head_repeat", "what" if single else "what_repeat", slots, []
    days = _num(m.get("bid_window_days"))
    if sig == "short_window_or_process_gap" and days is not None and days >= 0:
        slots["days"] = fmt.number(days, lang)
        return "one_bid", "head_window", "what_window", slots, []
    if not single:
        return None
    amount = _num(f.get("monetary_exposure_inr")) or _num(m.get("award_value_inr"))
    if amount and amount > 0:
        slots["amount"] = fmt.money(amount, lang)
        return "one_bid", "head", "what", slots, []
    return "one_bid", "head_noamount", "what", slots, []


def _promise_changed(f: dict[str, Any], m: dict[str, Any], lang: str) -> Frame | None:
    year = _year(f, m.get("walkback_year"), str(m.get("walkback_date") or "")[:4])
    return "promise_changed", "head", "what", {"entity": _entity(f, m.get("scheme_name")), "year": year}, []


def _exam(f: dict[str, Any], m: dict[str, Any], lang: str) -> Frame | None:
    kinds = section(lang, "exam_kinds")
    kind = kinds.get(str(m.get("disruption_type") or m.get("signal_category") or ""), kinds.get("other", ""))
    slots = {
        "entity": _entity(f, m.get("exam_name")),
        "year": _year(f, m.get("exam_year")),
        "authority": _entity(f, m.get("authority")),
        "kind": kind,
    }
    extra: list[tuple[str, dict[str, Any]]] = []
    delay = _num(m.get("delay_days"))
    if delay and delay > 0:
        extra.append(("delay_line", {"days": fmt.number(delay, lang)}))
    cand = _num(m.get("affected_candidates_count"))
    if cand and cand > 0:
        extra.append(("cand_line", {"count": fmt.number(cand, lang)}))
    return "exam", "head", "what", slots, extra


_CUSTODY_PERSON = {"post_acquittal_contempt_overrun", "bail_or_release_order_contempt"}


def _custody(f: dict[str, Any], m: dict[str, Any], lang: str) -> Frame | None:
    entity = _entity(f, m.get("state_ut"), m.get("jurisdiction"))
    days = _num(m.get("overrun_days"))
    if days and days > 0:
        return "custody", "head", "what", {"entity": entity, "days": fmt.number(days, lang)}, []
    if str(m.get("overrun_mechanism") or "") in _CUSTODY_PERSON or not m.get("overrun_mechanism"):
        return "custody", "head_nodays", "what_nodays", {"entity": entity}, []
    return "custody", "head_record", "what_record", {"entity": entity}, []


def _website(f: dict[str, Any], m: dict[str, Any], lang: str) -> Frame | None:
    return "website", "head", "what", {"entity": _entity(f)}, []


def _generic(f: dict[str, Any], m: dict[str, Any], lang: str) -> Frame:
    rule = _clip(f.get("rule_triggered") or f.get("methodology_code") or "-", 90)
    return "generic", "head", "what", {"entity": _entity(f) or _clip(f.get("title"), 80), "rule": rule}, []


BUILDERS: dict[str, Callable[[dict[str, Any], dict[str, Any], str], Frame | None]] = {
    "budget_gap": _budget_gap,
    "unspent": _unspent,
    "double_bill": _double_bill,
    "teachers": _teachers,
    "promise_pending": _promise_pending,
    "one_bid": _one_bid,
    "promise_changed": _promise_changed,
    "exam": _exam,
    "custody": _custody,
    "website": _website,
}


def kind_for(code: Any) -> str:
    return KIND_BY_CODE.get(str(code or "").strip().upper(), "generic")


def status_line(f: dict[str, Any], lang: str) -> str:
    key = STATUS_KEY.get(str(f.get("status") or ""), "peer_review")
    need = int(_num(f.get("required_quorum")) or 3)
    done = min(need, int(_num(f.get("confirm_count")) or 0))
    return fill(text(lang, "status", key), {"need": fmt.number(need, lang), "done": fmt.number(done, lang) or "0"})


def source_hash(f: dict[str, Any]) -> str:
    metrics = f.get("anomaly_metrics")
    if isinstance(metrics, str):
        try:
            metrics = json.loads(metrics)
        except ValueError:
            metrics = {}
    body = "|".join(
        str(f.get(k) or "")
        for k in ("title", "summary", "entity_name", "fiscal_year", "methodology_code")
    ) + f"|{round(float(f.get('monetary_exposure_inr') or 0.0))}|" + json.dumps(
        metrics if isinstance(metrics, dict) else {}, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:20]


def _render(frame: Frame, lang: str) -> dict[str, str]:
    kind, head_key, what_key, slots, extra = frame
    tpl = section(lang, "kinds", kind)
    what = fill(tpl[what_key], slots)
    for key, extra_slots in extra:
        what = f"{what} {fill(tpl[key], extra_slots)}"
    variant = head_key[len("head"):] if head_key.startswith("head_") else ""
    return {
        "head": fill(tpl[head_key], slots),
        "what": what,
        "why": tpl.get(f"why{variant}") or tpl["why"],
        "ask": tpl.get(f"ask{variant}") or tpl["ask"],
        "project": tpl["name"],
    }


def explain_finding(f: dict[str, Any], lang: str = "en", written: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if not isinstance(f, dict) or f.get("quarantined") or f.get("status") == "quarantined":
        return None
    m = _metrics(f)
    kind = kind_for(f.get("methodology_code"))
    body: dict[str, str] | None = None
    builder = BUILDERS.get(kind)
    if builder is not None:
        try:
            frame = builder(f, m, lang)
            if frame is not None:
                body = _render(frame, lang)
        except (KeyError, TypeError, ValueError):
            body = None
    if body is None:
        kind = "generic"
        try:
            body = _render(_generic(f, m, lang), lang)
        except (KeyError, TypeError, ValueError):
            return None
    p = pack(lang)
    source, model = "template", ""
    if written and isinstance(written.get("langs"), dict):
        entry = written["langs"].get(p["code"])
        if isinstance(entry, dict) and written.get("hash") == source_hash(f) and all(
            isinstance(entry.get(k), str) and entry[k].strip() for k in ("head", "what", "why", "ask")
        ):
            body = {**body, **{k: entry[k] for k in ("head", "what", "why", "ask")}}
            source, model = "model", str(written.get("model") or "")
    out: dict[str, Any] = {
        "lang": p["code"],
        "dir": p.get("dir", "ltr"),
        "kind": kind,
        "source": source,
        "model": model,
        **body,
        "status": status_line(f, lang),
        "not": text(lang, "status", "not_wrongdoing"),
    }
    out["speak"] = " ".join(out[k] for k in ("head", "what", "why", "status", "not"))
    return out


def labels(lang: str) -> dict[str, str]:
    return {
        **section(lang, "ui"),
        "recently_confirmed": text(lang, "news", "sec_recent"),
        "nothing_confirmed": text(lang, "news", "state_none"),
        "news_title": text(lang, "news", "title"),
    }
