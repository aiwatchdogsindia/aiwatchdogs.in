"""Deterministic Indian fiscal pacing (M5) and CAG audit para tracking (M6) engines."""

from __future__ import annotations

import re
from typing import Any

from aiwatchdogs.core.models import AuditLeadCandidate

_INR_CRORE = 10_000_000.0
_INR_LAKH = 100_000.0

_PARA_HEADER_RE = re.compile(
    r"(?im)(?:^|\n)\s*(?:Para(?:graph)?\.?\s*|Audit\s+Para\s+)(\d+(?:\.\d+)+)\s*[:\-–]?\s*([^\n]{5,140})"
)
_RUPEE_AMOUNT_RE = re.compile(
    r"(?i)(?:₹|INR|Rs\.?)\s*([\d,]+(?:\.\d+)?)\s*(crore|cr\.?|lakh|lakhs)\b"
)
_TYPOLOGY_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("unfruitful", "unfruitful_expenditure"),
    ("infructuous", "unfruitful_expenditure"),
    ("idle", "idle_outlay"),
    ("excess payment", "excess_payment"),
    ("avoidable expenditure", "avoidable_expenditure"),
    ("diversion", "fund_diversion"),
    ("non-recovery", "non_recovery"),
    ("short recovery", "non_recovery"),
    ("irregular", "irregular_expenditure"),
)


def _to_float(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("₹", "").replace("INR", "").strip()
    try:
        return float(text)
    except ValueError:
        return default


def _to_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y"}:
        return True
    if text in {"0", "false", "no", "n", ""}:
        return False
    return default


def _norm_str(value: Any, fallback: str = "") -> str:
    text = str(value).strip() if value is not None else ""
    return text if text else fallback


def _get_inr(rec: dict[str, Any], inr_key: str, crore_key: str) -> float:
    if rec.get(inr_key) is not None and str(rec.get(inr_key)).strip() != "":
        return _to_float(rec.get(inr_key))
    if rec.get(crore_key) is not None and str(rec.get(crore_key)).strip() != "":
        return _to_float(rec.get(crore_key)) * _INR_CRORE
    return 0.0


def run_m5_march_rush(
    records: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
) -> tuple[list[AuditLeadCandidate], int, float, str]:
    """M5: March Rush (Q4 > 33% or March > 15% per GFR Rule 62(3)) & >25% BE/RE vs Actuals variance."""
    ctx = context or {}
    q4_ceiling_pct = float(ctx.get("q4_ceiling_pct", 33.0))
    march_ceiling_pct = float(ctx.get("march_ceiling_pct", 15.0))
    variance_ceiling_pct = float(ctx.get("variance_ceiling_pct", 25.0))
    pac_note_threshold_inr = float(ctx.get("pac_note_threshold_inr", 100.0 * _INR_CRORE))

    leads: list[AuditLeadCandidate] = []
    cleared_count = 0
    total_audited = 0.0

    for idx, rec in enumerate(records):
        entity = _norm_str(
            rec.get("ministry_name") or rec.get("entity_name") or rec.get("department"),
            f"Department #{idx + 1}",
        )
        jurisdiction = _norm_str(rec.get("jurisdiction"), "Union of India")
        fiscal_year = _norm_str(rec.get("fiscal_year"), "FY 2024-25")
        demand_no = _norm_str(rec.get("demand_no") or rec.get("grant_no"), f"Grant-{idx + 1}")

        be_inr = _get_inr(rec, "be_inr", "be_crore")
        re_inr = _get_inr(rec, "re_inr", "re_crore")
        actual_inr = _get_inr(rec, "actual_inr", "actual_crore")

        # Support either aggregate q4/march columns or monthly columns (jan/feb/mar).
        q4_inr = _get_inr(rec, "q4_spend_inr", "q4_spend_crore")
        march_inr = _get_inr(rec, "march_spend_inr", "march_spend_crore")
        if q4_inr <= 0 and any(rec.get(k) is not None for k in ("jan_inr", "feb_inr", "mar_inr")):
            q4_inr = (
                _to_float(rec.get("jan_inr"))
                + _to_float(rec.get("feb_inr"))
                + _to_float(rec.get("mar_inr"))
            )
        if march_inr <= 0 and rec.get("mar_inr") is not None:
            march_inr = _to_float(rec.get("mar_inr"))

        alloc_inr = re_inr if re_inr > 0 else (be_inr if be_inr > 0 else actual_inr)
        total_audited += max(actual_inr, alloc_inr, 0.0)

        if alloc_inr <= 0:
            cleared_count += 1
            continue

        q4_pct = (
            _to_float(rec.get("q4_pct"))
            if rec.get("q4_pct") is not None and str(rec.get("q4_pct")).strip() != ""
            else (q4_inr / alloc_inr) * 100.0
        )
        march_pct = (
            _to_float(rec.get("march_pct"))
            if rec.get("march_pct") is not None and str(rec.get("march_pct")).strip() != ""
            else (march_inr / alloc_inr) * 100.0
        )
        if q4_inr <= 0 and q4_pct > 0:
            q4_inr = alloc_inr * (q4_pct / 100.0)
        if march_inr <= 0 and march_pct > 0:
            march_inr = alloc_inr * (march_pct / 100.0)

        be_var_pct = ((actual_inr - be_inr) / be_inr * 100.0) if (be_inr > 0 and actual_inr > 0) else 0.0
        re_var_pct = ((actual_inr - re_inr) / re_inr * 100.0) if (re_inr > 0 and actual_inr > 0) else 0.0

        mof_waiver = _to_bool(rec.get("mof_waiver_recorded"), False)
        pac_note_tabled = _to_bool(rec.get("pac_explanatory_note_tabled"), False)

        q4_breach = (not mof_waiver) and q4_pct > q4_ceiling_pct
        march_breach = (not mof_waiver) and march_pct > march_ceiling_pct
        variance_breach = abs(be_var_pct) > variance_ceiling_pct or abs(re_var_pct) > variance_ceiling_pct

        if not (q4_breach or march_breach or variance_breach):
            cleared_count += 1
            continue

        rules_hit: list[str] = []
        if q4_breach:
            rules_hit.append(f"Q4_spend={q4_pct:.1f}% > {q4_ceiling_pct:.0f}% ceiling")
        if march_breach:
            rules_hit.append(f"March_spend={march_pct:.1f}% > {march_ceiling_pct:.0f}% ceiling")
        if variance_breach:
            rules_hit.append(f"BE_variance={be_var_pct:+.1f}%, RE_variance={re_var_pct:+.1f}% (>25%)")

        abs_var_inr = abs(actual_inr - (re_inr if re_inr > 0 else be_inr))
        pac_note_missing = abs_var_inr >= pac_note_threshold_inr and not pac_note_tabled
        if pac_note_missing:
            rules_hit.append(f"variance>=INR 100 Cr without recorded PAC explanatory note")

        excess_q4 = max(0.0, q4_inr - (alloc_inr * q4_ceiling_pct / 100.0)) if q4_breach else 0.0
        excess_mar = max(0.0, march_inr - (alloc_inr * march_ceiling_pct / 100.0)) if march_breach else 0.0
        exposure_inr = max(excess_q4, excess_mar, abs_var_inr if variance_breach else 0.0)

        severity = "medium"
        if march_pct >= 25.0 or q4_pct >= 45.0 or exposure_inr >= 500.0 * _INR_CRORE:
            severity = "critical"
        elif (q4_breach and march_breach) or exposure_inr >= 100.0 * _INR_CRORE:
            severity = "high"

        title_tag = (
            "March Rush & Budget Variance"
            if ((q4_breach or march_breach) and variance_breach)
            else ("Q4/March Expenditure Surge" if (q4_breach or march_breach) else "BE/RE Budget Variance")
        )
        summary = (
            f"Public record anomaly in expenditure pacing for {entity} (Demand {demand_no}, "
            f"{jurisdiction}, {fiscal_year}): Q4 spend reached {q4_pct:.1f}% (INR {q4_inr / _INR_CRORE:.2f} Cr) "
            f"and March spend reached {march_pct:.1f}% (INR {march_inr / _INR_CRORE:.2f} Cr) of annual "
            f"allocation INR {alloc_inr / _INR_CRORE:.2f} Cr, with BE variance {be_var_pct:+.1f}% "
            f"and RE variance {re_var_pct:+.1f}%. Rule markers under GFR 2017 Rule 62(3) and MoF "
            f"Cash Management Circulars: {'; '.join(rules_hit)}."
        )

        leads.append(
            AuditLeadCandidate(
                title=f"Fiscal Pacing Lead: {title_tag} (Q4 {q4_pct:.1f}% / Mar {march_pct:.1f}%) — {entity}",
                summary=summary,
                methodology_code="M5",
                entity_name=entity,
                jurisdiction=jurisdiction,
                fiscal_year=fiscal_year,
                monetary_exposure_inr=round(exposure_inr, 2),
                severity=severity,
                anomaly_metrics={
                    "demand_no": demand_no,
                    "be_inr": round(be_inr, 2),
                    "re_inr": round(re_inr, 2),
                    "actual_inr": round(actual_inr, 2),
                    "q4_spend_inr": round(q4_inr, 2),
                    "march_spend_inr": round(march_inr, 2),
                    "q4_share_pct": round(q4_pct, 2),
                    "march_share_pct": round(march_pct, 2),
                    "be_variance_pct": round(be_var_pct, 2),
                    "re_variance_pct": round(re_var_pct, 2),
                    "pac_explanatory_note_missing": pac_note_missing,
                },
                rule_triggered="GFR 2017 Rule 62(3) & MoF Cash Management Ceilings (" + "; ".join(rules_hit) + ")",
                citations=[
                    {
                        "record_id": demand_no,
                        "row_id": idx + 1,
                        "source_url": _norm_str(rec.get("source_url"), "https://cga.nic.in"),
                        "excerpt": (
                            f"{entity} ({demand_no}): BE=INR {be_inr / _INR_CRORE:.2f}Cr, "
                            f"RE=INR {re_inr / _INR_CRORE:.2f}Cr, Actual=INR {actual_inr / _INR_CRORE:.2f}Cr, "
                            f"Q4={q4_pct:.1f}%, March={march_pct:.1f}%"
                        ),
                    }
                ],
                benign_explanations=[
                    "The Ministry of Finance occasionally issues formal Q4 CapEx relaxation or cash-management waivers for priority infrastructure or capital schemes.",
                    "Supplementary Demands for Grants approved late in the Parliamentary Winter or Budget Session can lawfully concentrate releases into Q4.",
                    "Large year-end accounting adjustments, SNA utilization transfers, or final milestone bills can elevate March book expenditure without indicating improper disbursement.",
                ],
                suggested_authority=entity,
            )
        )

    summary_text = (
        f"M5 scanned {len(records)} budget/expenditure accounts (INR {total_audited / _INR_CRORE:.2f} Cr audited): "
        f"{len(leads)} pacing/variance lead(s), {cleared_count} cleared record(s)."
    )
    return leads, cleared_count, round(total_audited, 2), summary_text


def _extract_paras_from_raw_text(
    raw_text: str,
    page: int | None,
    source_ref: str,
) -> list[dict[str, Any]]:
    """Deterministically extract structured CAG Audit Para records from PDF/plain text."""
    matches = list(_PARA_HEADER_RE.finditer(raw_text))
    extracted: list[dict[str, Any]] = []
    if not matches:
        return extracted

    for i, match in enumerate(matches):
        para_id = f"Para {match.group(1)}"
        heading = match.group(2).strip()
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else min(len(raw_text), start + 1500)
        snippet = raw_text[start:end].strip()
        lower_snip = snippet.lower()

        amount_inr = 0.0
        for amt_match in _RUPEE_AMOUNT_RE.finditer(snippet):
            num = _to_float(amt_match.group(1))
            unit = amt_match.group(2).lower()
            candidate = num * (_INR_CRORE if unit.startswith("cr") else _INR_LAKH)
            if candidate > amount_inr:
                amount_inr = candidate

        typology = "compliance_observation"
        for keyword, label in _TYPOLOGY_KEYWORDS:
            if keyword in lower_snip:
                typology = label
                break

        atn_status = "pending"
        if any(word in lower_snip for word in ("settled", "dropped", "closed")):
            atn_status = "settled"
        elif "unresolved" in lower_snip or "awaited" in lower_snip or "not furnished" in lower_snip:
            atn_status = "unresolved"

        repeat_count = 2 if any(w in lower_snip for w in ("repeat", "recurring", "earlier audit")) else 1
        years_pending = 2 if atn_status in {"pending", "unresolved"} else 0

        extracted.append(
            {
                "para_id": para_id,
                "report_no": "Extracted CAG Report PDF",
                "ministry_name": heading[:80],
                "title": heading,
                "summary_text": " ".join(snippet.split())[:360],
                "typology": typology,
                "quantified_impact_inr": amount_inr,
                "atn_status": atn_status,
                "years_pending": years_pending,
                "repeat_cycle_count": repeat_count,
                "page": page or 1,
                "source_url": source_ref or "https://cag.gov.in",
                "jurisdiction": "Union of India",
                "fiscal_year": "FY 2023-24",
            }
        )
    return extracted


def run_m6_cag_paras(
    records: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
) -> tuple[list[AuditLeadCandidate], int, float, str]:
    """M6: CAG Audit Para extraction and unresolved/repeat high-value para quantification."""
    ctx = context or {}
    min_impact_inr = float(ctx.get("min_impact_inr", 1.0 * _INR_CRORE))

    # Expand any PDF raw_text records into structured audit para dicts deterministically.
    normalized_records: list[dict[str, Any]] = []
    for rec in records:
        if "raw_text" in rec and not rec.get("para_id"):
            parsed = _extract_paras_from_raw_text(
                str(rec.get("raw_text") or ""),
                int(rec["page"]) if rec.get("page") is not None else None,
                _norm_str(rec.get("source_url") or rec.get("source_ref"), "https://cag.gov.in"),
            )
            normalized_records.extend(parsed)
        else:
            normalized_records.append(rec)

    leads: list[AuditLeadCandidate] = []
    cleared_count = 0
    total_audited = 0.0

    for idx, rec in enumerate(normalized_records):
        impact_inr = _get_inr(rec, "quantified_impact_inr", "impact_crore")
        total_audited += max(0.0, impact_inr)

        status = _norm_str(rec.get("atn_status") or rec.get("status"), "pending").lower()
        years_pending = int(round(_to_float(rec.get("years_pending"), 0)))
        repeat_cycles = int(round(_to_float(rec.get("repeat_cycle_count"), 1)))
        pac_atr_filed = _to_bool(rec.get("pac_atr_filed"), False)

        is_resolved = status in {"settled", "resolved", "closed", "dropped"}
        triggers: list[str] = []
        if not is_resolved:
            if impact_inr >= min_impact_inr:
                triggers.append(
                    f"unresolved_para_impact=INR {impact_inr / _INR_CRORE:.2f} Cr (>= INR {min_impact_inr / _INR_CRORE:.1f} Cr)"
                )
            if years_pending >= 2:
                triggers.append(f"atn_pending={years_pending}_years")
            if repeat_cycles >= 2:
                triggers.append(f"repeat_audit_cycles={repeat_cycles}")

        if is_resolved or not triggers:
            cleared_count += 1
            continue

        para_id = _norm_str(rec.get("para_id"), f"Para-{idx + 1}")
        report_no = _norm_str(rec.get("report_no"), "CAG Audit Report")
        entity = _norm_str(
            rec.get("ministry_name") or rec.get("entity_name") or rec.get("department"),
            "Concerned Ministry / Department",
        )
        jurisdiction = _norm_str(rec.get("jurisdiction"), "Union of India")
        fiscal_year = _norm_str(rec.get("fiscal_year"), "FY 2023-24")
        typology = _norm_str(rec.get("typology"), "compliance_observation")
        para_title = _norm_str(rec.get("title"), f"{report_no} {para_id}")
        para_excerpt = _norm_str(rec.get("summary_text") or rec.get("excerpt"), para_title)

        severity = "medium"
        if impact_inr >= 100.0 * _INR_CRORE or (repeat_cycles >= 3 and years_pending >= 3):
            severity = "critical"
        elif impact_inr >= 25.0 * _INR_CRORE or repeat_cycles >= 2 or years_pending >= 3:
            severity = "high"

        summary = (
            f"Public record audit follow-up lead on {report_no} ({para_id}) concerning {entity} "
            f"({jurisdiction}, {fiscal_year}): typology '{typology}' with quantified fiscal impact "
            f"of INR {impact_inr / _INR_CRORE:.2f} Cr remains '{status}' ({years_pending} yr(s) pending, "
            f"{repeat_cycles} audit cycle(s), PAC ATR filed={pac_atr_filed}). Summary: {para_excerpt}"
        )

        leads.append(
            AuditLeadCandidate(
                title=f"CAG Unresolved Para Lead: {para_id} ({typology.replace('_', ' ')}) — {entity}",
                summary=summary,
                methodology_code="M6",
                entity_name=entity,
                jurisdiction=jurisdiction,
                fiscal_year=fiscal_year,
                monetary_exposure_inr=round(impact_inr, 2),
                severity=severity,
                anomaly_metrics={
                    "report_no": report_no,
                    "para_id": para_id,
                    "typology": typology,
                    "quantified_impact_inr": round(impact_inr, 2),
                    "quantified_impact_crore": round(impact_inr / _INR_CRORE, 2),
                    "atn_status": status,
                    "years_pending": years_pending,
                    "repeat_cycle_count": repeat_cycles,
                    "pac_atr_filed": pac_atr_filed,
                },
                rule_triggered="CAG Audit Para / APMS ATN Follow-Up Tracker (" + "; ".join(triggers) + ")",
                citations=[
                    {
                        "record_id": f"{report_no}:{para_id}",
                        "para_id": para_id,
                        "page": rec.get("page", 1),
                        "row_id": idx + 1,
                        "source_url": _norm_str(rec.get("source_url"), "https://cag.gov.in"),
                        "excerpt": para_excerpt[:240],
                    }
                ],
                benign_explanations=[
                    "The department may have submitted an updated Action Taken Note (ATN) on APMS currently awaiting vetting by the Office of the CAG or the Public Accounts Committee.",
                    "Quantified audit figures sometimes represent gross project outlay where partial recoveries, asset commissioning, or contractual arbitration occurred after audit cut-off.",
                    "Inter-ministerial or Centre-State reconciliation of utilization certificates can extend formal closure timelines beyond two years.",
                ],
                suggested_authority=entity,
            )
        )

    summary_text = (
        f"M6 scanned {len(normalized_records)} CAG audit para record(s) "
        f"(INR {total_audited / _INR_CRORE:.2f} Cr quantified): {len(leads)} unresolved/repeat lead(s), "
        f"{cleared_count} settled/cleared para(s)."
    )
    return leads, cleared_count, round(total_audited, 2), summary_text
