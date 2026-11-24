"""P1: Police budget-head variance from official budget documents (PDF pages).

Built for community projects that audit police department finances. Official
Indian budget books print police heads (Major Head 2055 "Police", 4055 "Capital
Outlay on Police") as tables of Actuals, Budget Estimates (BE) and Revised
Estimates (RE). Most are only published as PDFs, so a task tells this engine
exactly where to look:

``context.budget_lines`` — one entry per budget head::

    {
      "key": "delhi-police-total",
      "entity": "Delhi Police",
      "label": "Demand No. 51 line 5 'Delhi Police' (revenue + capital)",
      "unit": "crore" | "lakh" | "thousand",
      "parts": [                                   # one part per printed line
        {"page": 1, "pattern": "^\\s*5\\.\\s*Delhi Police\\b",
         "values": {"actual": 2, "be": 5, "re": 8, "be_next": 11}}
      ],
      "years": {"actual": "FY2023-24", "be": "FY2024-25", "re": "FY2024-25", "be_next": "FY2025-26"},
      "references": {                              # optional figures from another official document
        "be_of_actual_year": {"value": 11662.03, "source_url": "...", "sha256": "...", "page": 1}
      }
    }

``context.comparisons`` — which pairs to compare, with a threshold::

    [{"a": "re", "b": "be", "label": "Revised vs budget estimate (same year)", "threshold_pct": 10}]

``context.flag_re_equals_be`` — also report heads whose RE repeats the BE exactly.

Numbers are read from the matched line as whitespace-separated tokens; ``..``,
``–`` and ``-`` mean nil. Indian digit grouping (``1670,92.50``) is handled by
dropping commas. Every reading is cited with page and the exact printed line,
so a second agent can reproduce it from the same checksummed PDF.
"""

from __future__ import annotations

import re
from typing import Any

from aiwatchdogs.core.models import AuditLeadCandidate

UNIT_MULTIPLIER = {"crore": 1e7, "lakh": 1e5, "thousand": 1e3, "rupee": 1.0}
_NUM_TOKEN = re.compile(r"^\d[\d,]*(?:\.\d+)?$")
_NIL_TOKENS = {"..", "–", "-", "—", "…"}

_BENIGN = {
    "re_be": [
        "Mid-year revisions commonly reflect pay commission arrears, dearness allowance revisions or new posts sanctioned during the year.",
        "Capital works are often re-phased across years because of land, approvals or tendering timelines.",
        "Supplementary grants and re-appropriations approved by the legislature can move the revised estimate.",
    ],
    "growth": [
        "Year-on-year growth can follow pay revisions, recruitment drives or newly sanctioned units.",
        "One-time capital projects (buildings, communication systems, vehicles) can raise a single year's budget.",
    ],
    "actual": [
        "Actual spending can differ from the budget because of savings on vacant posts, delayed procurement or late release of funds.",
        "Accounts are finalised after the year closes and may include book adjustments that are not visible in estimates.",
    ],
    "re_equals_be": [
        "An unchanged revised estimate can mean spending tracked the budget closely and no revision was needed.",
        "Some budget books carry the BE forward as the RE for heads that are revised only at a higher level.",
    ],
}


def _parse_number(token: str) -> float | None:
    if token in _NIL_TOKENS:
        return 0.0
    if _NUM_TOKEN.match(token):
        return float(token.replace(",", ""))
    return None


def _numbers_on_line(line: str) -> list[float]:
    out: list[float] = []
    for tok in line.split():
        val = _parse_number(tok.strip())
        if val is not None:
            out.append(val)
    return out


def _find_line(pages: dict[int, str], page: int, pattern: str) -> str | None:
    text = pages.get(page)
    if text is None:
        return None
    rx = re.compile(pattern)
    for line in text.splitlines():
        if rx.search(line):
            return line.strip()
    return None


def _fmt(value: float, unit: str) -> str:
    return f"{value:,.2f} {unit}" if unit != "thousand" else f"{value:,.0f} thousand"


def _severity(pct: float) -> str:
    a = abs(pct)
    if a >= 50:
        return "high"
    if a >= 25:
        return "medium"
    return "low"


def run_p1_police_budget_heads(
    records: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
) -> tuple[list[AuditLeadCandidate], int, float, str, dict[str, Any]]:
    ctx = context or {}
    lines = ctx.get("budget_lines") or []
    comparisons = ctx.get("comparisons") or [
        {"a": "re", "b": "be", "label": "Revised estimate vs budget estimate (same year)", "threshold_pct": 10.0}
    ]
    flag_equal = bool(ctx.get("flag_re_equals_be", False))
    source_url = str(ctx.get("retrieval_url") or ctx.get("source_url") or "")
    sha = str(ctx.get("expected_sha256") or "")
    jurisdiction = str(ctx.get("jurisdiction") or "India")
    authority = str(ctx.get("suggested_authority") or ctx.get("official_publisher") or "Finance Department")

    pages: dict[int, str] = {}
    for rec in records:
        try:
            pages[int(rec.get("page"))] = str(rec.get("raw_text") or "")
        except (TypeError, ValueError):
            continue

    leads: list[AuditLeadCandidate] = []
    cleared = 0
    unevaluable = 0
    audited_inr = 0.0
    readings: list[dict[str, Any]] = []

    for spec in lines:
        key = str(spec.get("key", "line"))
        entity = str(spec.get("entity", key))
        label = str(spec.get("label", key))
        unit = str(spec.get("unit", "crore"))
        mult = UNIT_MULTIPLIER.get(unit)
        years = spec.get("years") or {}
        parts = spec.get("parts") or [spec]
        if mult is None:
            unevaluable += 1
            continue

        values: dict[str, float] = {}
        cites: list[dict[str, Any]] = []
        ok = True
        for part in parts:
            page = int(part.get("page", 0))
            line = _find_line(pages, page, str(part.get("pattern", "")))
            if line is None:
                ok = False
                break
            nums = _numbers_on_line(line)
            for name, idx in (part.get("values") or {}).items():
                if not isinstance(idx, int) or idx >= len(nums):
                    ok = False
                    break
                values[name] = nums[idx]
            if not ok:
                break
            cites.append({"source_url": source_url, "page": page, "printed_line": line, "sha256": sha})
        if not ok:
            unevaluable += 1
            readings.append({"key": key, "status": "unevaluable", "reason": "printed line not found as specified"})
            continue

        refs = spec.get("references") or {}
        for name, ref in refs.items():
            if isinstance(ref, dict) and isinstance(ref.get("value"), (int, float)):
                values[name] = float(ref["value"])
                cites.append(
                    {
                        "source_url": str(ref.get("source_url", "")),
                        "page": ref.get("page"),
                        "printed_line": str(ref.get("printed_line", "")),
                        "sha256": str(ref.get("sha256", "")),
                        "role": f"reference:{name}",
                    }
                )

        audited_inr = max(audited_inr, max((v for v in values.values()), default=0.0) * mult)
        readings.append({"key": key, "status": "read", "values": values, "unit": unit})

        for comp in comparisons:
            a_name, b_name = str(comp.get("a")), str(comp.get("b"))
            if a_name not in values or b_name not in values:
                continue
            a_val, b_val = values[a_name], values[b_name]
            if b_val <= 0:
                unevaluable += 1
                continue
            pct = (a_val - b_val) / b_val * 100.0
            threshold = float(comp.get("threshold_pct", 10.0))
            kind = str(comp.get("kind", "re_be"))
            if abs(pct) < threshold:
                cleared += 1
                continue
            comp_label = str(comp.get("label", f"{a_name} vs {b_name}"))
            a_year = years.get(a_name, "")
            b_year = years.get(b_name, "")
            direction = "above" if pct > 0 else "below"
            leads.append(
                AuditLeadCandidate(
                    title=f"{entity}: {comp_label} is {abs(pct):.1f}% {direction} ({label})",
                    summary=(
                        f"The official budget document prints {_fmt(a_val, unit)} for {a_name.upper().replace('_', ' ')}"
                        f"{f' {a_year}' if a_year else ''} against {_fmt(b_val, unit)} for "
                        f"{b_name.upper().replace('_', ' ')}{f' {b_year}' if b_year else ''} on {label}, a difference of "
                        f"{pct:+.1f}% (threshold {threshold:.0f}%). This is a statistical divergence in published "
                        "figures for public review, not a finding of wrongdoing."
                    ),
                    methodology_code="P1",
                    entity_name=entity,
                    jurisdiction=jurisdiction,
                    fiscal_year=str(a_year or b_year or ctx.get("fiscal_year") or "Unspecified"),
                    monetary_exposure_inr=abs(a_val - b_val) * mult,
                    severity=_severity(pct),  # type: ignore[arg-type]
                    anomaly_metrics={
                        "line_key": key,
                        "compare": f"{a_name}_vs_{b_name}",
                        a_name: a_val,
                        b_name: b_val,
                        "unit": unit,
                        "pct_difference": round(pct, 2),
                        "threshold_pct": threshold,
                    },
                    rule_triggered=f"P1-{a_name.upper()}-VS-{b_name.upper()}>{threshold:.0f}%",
                    citations=cites,
                    benign_explanations=_BENIGN.get(kind, _BENIGN["re_be"]),
                    suggested_authority=authority,
                    source_locator=", ".join(f"p.{c['page']}" for c in cites if c.get("page")),
                    amount_type="budget_and_treasury_expenditure",
                )
            )

        if flag_equal and values.get("re") is not None and values.get("be") is not None:
            if values["be"] > 0 and values["re"] == values["be"]:
                leads.append(
                    AuditLeadCandidate(
                        title=f"{entity}: revised estimate repeats the budget estimate exactly ({label})",
                        summary=(
                            f"The official budget document prints the same figure, {_fmt(values['be'], unit)}, as both "
                            f"BE and RE{f' for {years.get('re')}' if years.get('re') else ''} on {label}. Worth checking "
                            "whether the head was actually reviewed mid-year. Statistical observation for public review."
                        ),
                        methodology_code="P1",
                        entity_name=entity,
                        jurisdiction=jurisdiction,
                        fiscal_year=str(years.get("re") or ctx.get("fiscal_year") or "Unspecified"),
                        monetary_exposure_inr=0.0,
                        severity="low",
                        anomaly_metrics={"line_key": key, "be": values["be"], "re": values["re"], "unit": unit},
                        rule_triggered="P1-RE-EQUALS-BE",
                        citations=cites,
                        benign_explanations=_BENIGN["re_equals_be"],
                        suggested_authority=authority,
                        source_locator=", ".join(f"p.{c['page']}" for c in cites if c.get("page")),
                        amount_type="budget_and_treasury_expenditure",
                    )
                )

    leads.sort(key=lambda lead: (-float(lead.monetary_exposure_inr), lead.title))
    max_leads = int(ctx.get("max_leads", 8))
    if max_leads > 0:
        leads = leads[:max_leads]
    read_count = sum(1 for r in readings if r["status"] == "read")
    summary = (
        f"P1 read {read_count} of {len(lines)} police budget heads; {len(leads)} divergences above threshold, "
        f"{cleared} comparisons within threshold, {unevaluable} unevaluable."
    )
    meta = {
        "unevaluable_count": unevaluable,
        "amount_type": "budget_and_treasury_expenditure",
        "readings": readings,
    }
    return leads, cleared, audited_inr, summary, meta
