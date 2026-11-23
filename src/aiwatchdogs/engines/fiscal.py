"""Deterministic Indian fiscal pacing (M5) and CAG audit para tracking (M6) engines."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from aiwatchdogs.core.models import AuditLeadCandidate

_INR_CRORE = 10_000_000.0
_INR_LAKH = 100_000.0

# Legacy explicit "Para X.Y" header format
_LEGACY_PARA_HEADER_RE = re.compile(
    r"(?im)^[ \t]*(?:Para(?:graph)?\.?[ \t]*|Audit[ \t]+Para[ \t]+)(\d+(?:\.\d+)+)[ \t]*[:\-–]?[ \t]*([^\n]{5,140})"
)
# Real CAG numbered audit section heading format (e.g. "2.1 Infructuous expenditure...", "4.1.3 Depletion of...")
_NUMBERED_SECTION_RE = re.compile(
    r"(?m)^[ \t]*(\d+\.\d+(?:\.\d+)?)[ \t]+([A-Z][^\n]{12,130})$"
)
_RUPEE_AMOUNT_RE = re.compile(
    r"(?i)(?:₹|INR|Rs\.?)\s*([\d,]+(?:\.\d+)?)\s*(crore|cr\.?|lakh|lakhs)\b"
)
_TOC_LINE_RE = re.compile(r"(?:\.{3,}|…|\s{2,}\d{1,3})\s*$|\s+\d{1,3}\s*$")
_UNIT_FALSE_POS_RE = re.compile(
    r"^(?:Cu\.?m|MWh|MW|kWh|MT|Km|Sq\.|Kg|Crore|Lakh|Per\s*cent|%|Rs\.?|INR)\b",
    re.IGNORECASE,
)
_BOILERPLATE_HEADING_RE = re.compile(
    r"^(?:Introduction|Audit\s+(?:Objectives|Scope|Methodology|Criteria|Approach|Sampling|Framework)|"
    r"Organisation(?:al)?\s+(?:Profile|Structure|Setup)|Background|Executive\s+Summary|"
    r"Conclusion|Recommendations|Ministry\s+of\s+[A-Za-z\s&]+$)",
    re.IGNORECASE,
)

_TYPOLOGY_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("unfruitful", "unfruitful_expenditure"),
    ("infructuous", "unfruitful_expenditure"),
    ("idle", "idle_outlay"),
    ("excess payment", "excess_payment"),
    ("avoidable expenditure", "avoidable_expenditure"),
    ("avoidable", "avoidable_expenditure"),
    ("diversion", "fund_diversion"),
    ("non-recovery", "non_recovery"),
    ("short recovery", "non_recovery"),
    ("non-levy", "revenue_shortfall"),
    ("short levy", "revenue_shortfall"),
    ("non-release", "delayed_fund_release"),
    ("depletion", "fund_depletion"),
    ("irregular", "irregular_expenditure"),
    ("undue benefit", "undue_benefit"),
    ("cost overrun", "cost_overrun"),
    ("beyond cerc norms", "norm_variance"),
    ("controllable costs", "cost_variance"),
)

_NEUTRAL_REPLACE_RE = re.compile(
    r"\b(?:scam(?:s|med|mer|mers)?|corrupt(?:ed|ing|ly|ion)?|brib(?:e|es|ed|ing|ery)|"
    r"fraud(?:s|ulent|ulently|ster|sters)?|kickback(?:s)?|guilty|stolen|rigged|"
    r"crook(?:s|ed)?|loot(?:s|ed|ing|er|ers)?|embezzl(?:e|es|ed|ing|ement))\b",
    re.IGNORECASE,
)

_MONTH_PREFIX_TO_NUM: dict[str, int] = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}

_SCHEME_DISPLAY_NAMES: dict[str, str] = {
    "mgnrega": "MGNREGA (Mahatma Gandhi NREGA)",
    "pm_jay": "Ayushman Bharat PM-JAY",
    "pm_kisan": "PM-KISAN",
    "jal_jeevan_mission": "Jal Jeevan Mission (JJM)",
    "pmay": "Pradhan Mantri Awas Yojana (PMAY)",
    "pmay_g": "PMAY-Gramin",
    "pmay_u": "PMAY-Urban",
    "pmgsy": "Pradhan Mantri Gram Sadak Yojana (PMGSY)",
    "poshan": "Saksham Anganwadi & POSHAN 2.0",
    "samagra_shiksha": "Samagra Shiksha Abhiyan",
}


def _neutralize_text(text: str) -> str:
    """Neutralize prohibited BNS Sec 356 terms in quoted third-party audit text."""
    return _NEUTRAL_REPLACE_RE.sub("compliance-issue", text)


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


def _cap_leads_deterministically(
    leads: list[AuditLeadCandidate],
    ctx: dict[str, Any],
    default_cap: int = 5,
) -> tuple[list[AuditLeadCandidate], int]:
    sorted_leads = sorted(
        leads,
        key=lambda lead: (-float(lead.monetary_exposure_inr), lead.entity_name, lead.title),
    )
    total_detected = len(sorted_leads)
    max_leads = int(ctx.get("max_leads", default_cap))
    if max_leads > 0 and len(sorted_leads) > max_leads:
        return sorted_leads[:max_leads], total_detected
    return sorted_leads, total_detected


def _rollup_monthly_treasury(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Roll up monthly treasury rows (e.g. AP Treasury) into Indian FY x District x MajorHead accounts."""
    groups: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for rec in records:
        month_raw = _norm_str(rec.get("Month") or rec.get("month"))
        m_num = _MONTH_PREFIX_TO_NUM.get(month_raw.upper()[:3], 0)
        year_val = int(round(_to_float(rec.get("Year") or rec.get("year"), 0)))
        amt = _to_float(rec.get("Amount") or rec.get("amount"), 0.0)
        if m_num == 0 or year_val <= 1900:
            continue

        # Indian Fiscal Year: Apr..Dec -> FY Y-(Y+1), Jan..Mar -> FY (Y-1)-Y
        fy_start = year_val if m_num >= 4 else (year_val - 1)
        fy_label = f"FY {fy_start}-{str(fy_start + 1)[-2:]}"

        district = _norm_str(rec.get("District") or rec.get("district"), "Statewide").title()
        major_code = _norm_str(rec.get("Major Head") or rec.get("major_head"), "MH")
        major_desc = _norm_str(
            rec.get("Major Head Description") or rec.get("major_head_description"),
            f"Major Head {major_code}",
        )
        key = (fy_label, district, major_code, major_desc)
        entry = groups.get(key)
        if entry is None:
            entry = {
                "demand_no": f"MH-{major_code}",
                "ministry_name": f"{district} — {major_desc}",
                "jurisdiction": _norm_str(rec.get("jurisdiction"), "State:Andhra Pradesh"),
                "fiscal_year": fy_label,
                "actual_inr": 0.0,
                "q4_spend_inr": 0.0,
                "march_spend_inr": 0.0,
                "months_seen": set(),
                "source_url": _norm_str(rec.get("source_url"), "https://treasury.ap.gov.in"),
            }
            groups[key] = entry

        entry["actual_inr"] += amt
        entry["months_seen"].add(m_num)
        if m_num in {1, 2, 3}:
            entry["q4_spend_inr"] += amt
        if m_num == 3:
            entry["march_spend_inr"] += amt

    rolled: list[dict[str, Any]] = []
    for entry in groups.values():
        # Only evaluate FY accounts with >=6 distinct months to avoid partial-year distortion
        if len(entry["months_seen"]) < 6 or entry["actual_inr"] <= 0:
            continue
        entry["months_covered"] = len(entry["months_seen"])
        del entry["months_seen"]
        rolled.append(entry)
    return rolled


def _rollup_obds_demands(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Roll up OBDS line-items (e.g. Assam Expenditure OBDS) into Demand-for-Grant totals."""
    groups: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for rec in records:
        demand_no = _norm_str(rec.get("demand_grant_no_en"), "")
        if not demand_no:
            continue
        demand_desc = _norm_str(rec.get("demand_grant_desc_en"), f"Demand {demand_no}")
        state = _norm_str(rec.get("state_ut_name_eng"), "Assam")
        fy = _norm_str(rec.get("budget_financial_year"), "2022-23")
        fy_label = fy if fy.upper().startswith("FY") else f"FY {fy}"

        unit_str = _norm_str(rec.get("budget_amount_unit"), "").lower()
        if "crore" in unit_str:
            scale = _INR_CRORE
        elif "thousand" in unit_str:
            scale = 1_000.0
        elif "rupee" in unit_str or "inr" in unit_str:
            scale = 1.0
        else:
            # Assam Finance Department OBDS budget books report values in INR Lakhs
            scale = _INR_LAKH

        key = (state, fy_label, demand_no, demand_desc)
        entry = groups.get(key)
        if entry is None:
            entry = {
                "demand_no": f"Demand-{demand_no}",
                "ministry_name": f"{demand_desc} (Demand {demand_no})",
                "jurisdiction": f"State:{state}",
                "fiscal_year": fy_label,
                "be_inr": 0.0,
                "re_inr": 0.0,
                "actual_inr": 0.0,
                "source_url": _norm_str(rec.get("source_url"), "https://finance.assam.gov.in"),
            }
            groups[key] = entry

        entry["be_inr"] += _to_float(rec.get("budget_estimates_previous_financial_year"), 0.0) * scale
        entry["re_inr"] += _to_float(rec.get("revised_estimates_previous_financial_year"), 0.0) * scale
        entry["actual_inr"] += _to_float(rec.get("actuals_last_audited_year"), 0.0) * scale

    return list(groups.values())


def _normalize_policystack_schemes(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map Union Flagship Schemes CSV (scheme_id, fiscal_year, budget_allocated_cr/revised/spent)."""
    out: list[dict[str, Any]] = []
    for idx, rec in enumerate(records):
        sid = _norm_str(rec.get("scheme_id"), f"scheme_{idx + 1}")
        display = _SCHEME_DISPLAY_NAMES.get(sid.lower(), sid.replace("_", " ").title())
        fy = _norm_str(rec.get("fiscal_year"), "2023-24")
        fy_label = fy if fy.upper().startswith("FY") else f"FY {fy}"
        out.append(
            {
                "demand_no": f"SCHEME-{sid.upper()}-{fy}",
                "ministry_name": display,
                "jurisdiction": "Union of India",
                "fiscal_year": fy_label,
                "be_crore": _to_float(rec.get("budget_allocated_cr"), 0.0),
                "re_crore": _to_float(rec.get("budget_revised_cr"), 0.0),
                "actual_crore": _to_float(rec.get("budget_spent_cr"), 0.0),
                "source_url": _norm_str(rec.get("source_url"), "https://indiabudget.gov.in"),
            }
        )
    return out


def _eval_cga_monthly_accounts(
    records: list[dict[str, Any]],
    ctx: dict[str, Any],
) -> tuple[list[AuditLeadCandidate], int, float, str]:
    """Evaluate live CGA Union Government Monthly Accounts HTML tables for pacing divergence & Q4 backlog."""
    q4_ceiling_pct = float(ctx.get("q4_ceiling_pct", 33.0))
    variance_ceiling_pct = float(ctx.get("variance_ceiling_pct", 25.0))

    leads: list[AuditLeadCandidate] = []
    cleared_count = 0
    total_audited = 0.0

    for idx, rec in enumerate(records):
        entity = _neutralize_text(_norm_str(rec.get("ministry_name") or rec.get("entity_name"), f"Account-{idx + 1}"))
        demand_no = _norm_str(rec.get("demand_no"), f"CGA-{idx + 1}")
        fy = _norm_str(rec.get("fiscal_year"), "FY 2024-25")
        period = _norm_str(rec.get("report_period"), "Mid-Year")
        months_elapsed = max(1, min(12, int(round(_to_float(rec.get("months_elapsed_fy"), 9)))))
        be_inr = _get_inr(rec, "be_inr", "be_crore")
        actual_inr = _get_inr(rec, "actual_inr", "actual_crore")
        total_audited += max(abs(be_inr), abs(actual_inr))

        if be_inr <= 0:
            cleared_count += 1
            continue

        actual_pct = (
            _to_float(rec.get("actual_to_be_pct"))
            if rec.get("actual_to_be_pct") not in (None, "")
            else (actual_inr / be_inr) * 100.0
        )
        pro_rata_pct = (months_elapsed / 12.0) * 100.0
        implied_remaining_pct = max(0.0, 100.0 - actual_pct)
        pacing_gap_pp = actual_pct - pro_rata_pct

        is_expenditure = any(
            kw in entity.lower() for kw in ("expenditure", "subsidy", "loan", "capital", "revenue expenditure")
        )
        q4_backlog_flag = (
            months_elapsed == 9
            and is_expenditure
            and implied_remaining_pct > q4_ceiling_pct
            and be_inr >= 5000.0 * _INR_CRORE
        )
        be_exceeded_flag = actual_pct > 100.0 and actual_inr > be_inr
        pro_rata_divergence_flag = abs(pacing_gap_pp) > variance_ceiling_pct and be_inr >= 5000.0 * _INR_CRORE

        if not (q4_backlog_flag or be_exceeded_flag or pro_rata_divergence_flag):
            cleared_count += 1
            continue

        rules_hit: list[str] = []
        if q4_backlog_flag:
            rules_hit.append(
                f"implied_Q4_remaining={implied_remaining_pct:.1f}% > {q4_ceiling_pct:.0f}% GFR Rule 62(3) ceiling (actual={actual_pct:.1f}% by {period})"
            )
        if be_exceeded_flag:
            rules_hit.append(
                f"mid_year_BE_exceeded={actual_pct:.1f}% (>100% by {period})"
            )
        if pro_rata_divergence_flag:
            rules_hit.append(
                f"pro_rata_pacing_gap={pacing_gap_pp:+.1f}pp (actual={actual_pct:.1f}% vs {pro_rata_pct:.1f}% linear pace)"
            )

        remaining_inr = max(0.0, be_inr - actual_inr)
        excess_over_be = max(0.0, actual_inr - be_inr)
        pro_rata_inr = be_inr * (pro_rata_pct / 100.0)
        exposure_inr = max(
            remaining_inr if q4_backlog_flag else 0.0,
            excess_over_be if be_exceeded_flag else 0.0,
            abs(actual_inr - pro_rata_inr) if pro_rata_divergence_flag else 0.0,
        )

        severity = "critical" if exposure_inr >= 50_000.0 * _INR_CRORE else (
            "high" if exposure_inr >= 5_000.0 * _INR_CRORE else "medium"
        )
        summary = (
            f"CGA Union Monthly Accounts pacing signal for '{entity}' ({fy}, up to {period}): "
            f"actual utilization stands at INR {actual_inr / _INR_CRORE:,.2f} Cr ({actual_pct:.1f}% of "
            f"BE INR {be_inr / _INR_CRORE:,.2f} Cr vs {pro_rata_pct:.1f}% pro-rata benchmark; "
            f"remaining implied Q4 balance {implied_remaining_pct:.1f}%). Indicators: {'; '.join(rules_hit)}."
        )
        leads.append(
            AuditLeadCandidate(
                title=f"CGA Monthly Pacing Lead: {entity} ({actual_pct:.1f}% of BE by {period}) — Union of India",
                summary=summary,
                methodology_code="M5",
                entity_name=entity,
                jurisdiction="Union of India",
                fiscal_year=fy,
                monetary_exposure_inr=round(exposure_inr, 2),
                severity=severity,
                anomaly_metrics={
                    "demand_no": demand_no,
                    "be_inr": round(be_inr, 2),
                    "actual_inr": round(actual_inr, 2),
                    "actual_to_be_pct": round(actual_pct, 2),
                    "pro_rata_pct": round(pro_rata_pct, 2),
                    "implied_remaining_pct": round(implied_remaining_pct, 2),
                    "months_elapsed_fy": months_elapsed,
                    "coppy_pct": rec.get("coppy_pct"),
                },
                rule_triggered="CGA Monthly Accounts Pacing & GFR 2017 Rule 62(3) (" + "; ".join(rules_hit) + ")",
                citations=[
                    {
                        "record_id": demand_no,
                        "row_id": idx + 1,
                        "source_url": _norm_str(rec.get("source_url"), "https://cga.nic.in"),
                        "excerpt": (
                            f"CGA {entity} ({period}): BE=INR {be_inr / _INR_CRORE:,.2f} Cr, "
                            f"Actual=INR {actual_inr / _INR_CRORE:,.2f} Cr ({actual_pct:.1f}%)"
                        ),
                    }
                ],
                benign_explanations=[
                    "Capital expenditure and infrastructure milestone disbursements frequently accelerate in Q4 following monsoon completion and tender execution cycles.",
                    "Mid-year pacing variances in financing, receipts, or subsidies may reflect statutory advance tax schedules, RBI dividend timing, or supplementary grants.",
                    "Provisional CGA monthly accounts undergo subsequent reconciliation and year-end book adjustments prior to final Union Finance Accounts certification by CAG.",
                ],
                suggested_authority="Controller General of Accounts (Ministry of Finance)",
            )
        )

    top_leads, total_detected = _cap_leads_deterministically(leads, ctx)
    lead_note = (
        f"top {len(top_leads)} of {total_detected} pacing lead(s)"
        if total_detected > len(top_leads)
        else f"{len(top_leads)} pacing lead(s)"
    )
    summary_text = (
        f"M5 scanned {len(records)} CGA monthly account rows (INR {total_audited / _INR_CRORE:,.2f} Cr audited): "
        f"{lead_note}, {cleared_count} cleared row(s)."
    )
    return top_leads, cleared_count, round(total_audited, 2), summary_text


def run_m5_march_rush(
    records: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
) -> tuple[list[AuditLeadCandidate], int, float, str]:
    """M5: March Rush (Q4 > 33% or March > 15% per GFR Rule 62(3)) & >25% BE/RE vs Actuals variance.

    Supports:
    - Classic pre-aggregated budget/expenditure rows
    - Monthly treasury transaction rollups (e.g. AP Treasury)
    - OBDS state expenditure line-item rollups (e.g. Assam OBDS)
    - Union Flagship Schemes CSV (PolicyStack BE/RE/Spent)
    - CGA Monthly Accounts HTML table rows
    """
    ctx = context or {}
    if not records:
        return [], 0, 0.0, "M5 scanned 0 budget/expenditure accounts: 0 lead(s), 0 cleared record(s)."

    sample = records[0]
    if sample.get("record_type") == "cga_monthly_account":
        return _eval_cga_monthly_accounts(records, ctx)

    working_records = records
    if "Month" in sample and "Amount" in sample and ("District" in sample or "Major Head Description" in sample):
        working_records = _rollup_monthly_treasury(records)
    elif "demand_grant_no_en" in sample and "budget_estimates_previous_financial_year" in sample:
        working_records = _rollup_obds_demands(records)
    elif "scheme_id" in sample and "budget_allocated_cr" in sample:
        working_records = _normalize_policystack_schemes(records)

    q4_ceiling_pct = float(ctx.get("q4_ceiling_pct", 33.0))
    march_ceiling_pct = float(ctx.get("march_ceiling_pct", 15.0))
    variance_ceiling_pct = float(ctx.get("variance_ceiling_pct", 25.0))
    pac_note_threshold_inr = float(ctx.get("pac_note_threshold_inr", 100.0 * _INR_CRORE))

    leads: list[AuditLeadCandidate] = []
    cleared_count = 0
    total_audited = 0.0

    for idx, rec in enumerate(working_records):
        entity = _neutralize_text(
            _norm_str(
                rec.get("ministry_name") or rec.get("entity_name") or rec.get("department"),
                f"Department #{idx + 1}",
            )
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
        be_re_shift_pct = ((re_inr - be_inr) / be_inr * 100.0) if (be_inr > 0 and re_inr > 0) else 0.0

        mof_waiver = _to_bool(rec.get("mof_waiver_recorded"), False)
        pac_note_tabled = _to_bool(rec.get("pac_explanatory_note_tabled"), False)

        q4_breach = (not mof_waiver) and q4_pct > q4_ceiling_pct
        march_breach = (not mof_waiver) and march_pct > march_ceiling_pct
        variance_breach = (
            abs(be_var_pct) > variance_ceiling_pct
            or abs(re_var_pct) > variance_ceiling_pct
            or abs(be_re_shift_pct) > variance_ceiling_pct
        )

        if not (q4_breach or march_breach or variance_breach):
            cleared_count += 1
            continue

        rules_hit: list[str] = []
        if q4_breach:
            rules_hit.append(f"Q4_spend={q4_pct:.1f}% > {q4_ceiling_pct:.0f}% ceiling")
        if march_breach:
            rules_hit.append(f"March_spend={march_pct:.1f}% > {march_ceiling_pct:.0f}% ceiling")
        if variance_breach:
            rules_hit.append(
                f"BE_variance={be_var_pct:+.1f}%, RE_variance={re_var_pct:+.1f}%, RE_vs_BE={be_re_shift_pct:+.1f}% (>25%)"
            )

        abs_var_inr = max(
            abs(actual_inr - (re_inr if re_inr > 0 else be_inr)) if actual_inr > 0 else 0.0,
            abs(re_inr - be_inr) if (be_inr > 0 and re_inr > 0) else 0.0,
        )
        pac_note_missing = abs_var_inr >= pac_note_threshold_inr and not pac_note_tabled
        if pac_note_missing:
            rules_hit.append("variance>=INR 100 Cr without recorded PAC explanatory note")

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
        metric_badge = (
            f"Q4 {q4_pct:.1f}% / Mar {march_pct:.1f}%"
            if (q4_breach or march_breach)
            else f"BE {be_var_pct:+.1f}% / RE {be_re_shift_pct:+.1f}%"
        )
        summary = (
            f"Public record anomaly in expenditure pacing for {entity} ({demand_no}, "
            f"{jurisdiction}, {fiscal_year}): Q4 spend reached {q4_pct:.1f}% (INR {q4_inr / _INR_CRORE:.2f} Cr) "
            f"and March spend reached {march_pct:.1f}% (INR {march_inr / _INR_CRORE:.2f} Cr) of annual "
            f"outlay INR {alloc_inr / _INR_CRORE:.2f} Cr, with BE variance {be_var_pct:+.1f}% "
            f"and RE variance {re_var_pct:+.1f}%. Rule markers under GFR 2017 Rule 62(3) and MoF "
            f"Cash Management Circulars: {'; '.join(rules_hit)}."
        )

        leads.append(
            AuditLeadCandidate(
                title=f"Fiscal Pacing Lead: {title_tag} ({metric_badge}) — {entity}",
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
                    "re_vs_be_shift_pct": round(be_re_shift_pct, 2),
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
                    "The Ministry of Finance or State Finance Department occasionally issues formal Q4 CapEx relaxation or cash-management waivers for priority infrastructure or capital schemes.",
                    "Supplementary Demands for Grants approved late in the Parliamentary or Assembly session can lawfully concentrate releases into Q4 or expand RE above BE.",
                    "Large year-end accounting adjustments, SNA utilization transfers, or final milestone bills can elevate March book expenditure without indicating improper disbursement.",
                ],
                suggested_authority=entity,
            )
        )

    top_leads, total_detected = _cap_leads_deterministically(leads, ctx)
    lead_note = (
        f"top {len(top_leads)} of {total_detected} pacing/variance lead(s)"
        if total_detected > len(top_leads)
        else f"{len(top_leads)} pacing/variance lead(s)"
    )
    summary_text = (
        f"M5 scanned {len(working_records)} budget/expenditure accounts (INR {total_audited / _INR_CRORE:.2f} Cr audited): "
        f"{lead_note}, {cleared_count} cleared record(s)."
    )
    return top_leads, cleared_count, round(total_audited, 2), summary_text


def _extract_amounts_inr(text: str) -> list[float]:
    amounts: list[float] = []
    for amt_match in _RUPEE_AMOUNT_RE.finditer(text):
        num = _to_float(amt_match.group(1))
        unit = amt_match.group(2).lower()
        candidate = num * (_INR_CRORE if unit.startswith("cr") else _INR_LAKH)
        if candidate > 0:
            amounts.append(candidate)
    return amounts


def _detect_report_metadata(full_text: str, source_ref: str) -> tuple[str, str, str]:
    """Infer CAG report title/number, jurisdiction, and fiscal year from cover/opening pages."""
    head = full_text[:8000]
    rep_match = re.search(r"Report\s+No\.?\s*(\d+\s+of\s+20\d{2})", head, re.IGNORECASE)
    report_no = f"CAG Report No. {rep_match.group(1)}" if rep_match else "Extracted CAG Report PDF"

    jurisdiction = "Union of India"
    lower_ref = f"{source_ref} {head[:2000]}".lower()
    if "madhya pradesh" in lower_ref or "mp-jjm" in lower_ref or "mp_jjm" in lower_ref or "bhopal" in lower_ref:
        jurisdiction = "State:Madhya Pradesh"
    elif "government of assam" in lower_ref or "cag_assam" in lower_ref:
        jurisdiction = "State:Assam"

    fy_match = re.search(r"(?:20\d{2})\s*[-–]\s*(?:20)?(\d{2})", head)
    fiscal_year = f"FY {fy_match.group(0).replace('–', '-')}" if fy_match else "FY 2023-24"
    return report_no, jurisdiction, fiscal_year


def _extract_paras_from_pages(
    page_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Deterministically extract structured CAG Audit Para records from multi-page PDF/plain text."""
    page_offsets: list[tuple[int, int]] = []
    chunks: list[str] = []
    cursor = 0
    source_ref = "https://cag.gov.in"
    for rec in page_records:
        pg = int(rec.get("page") or 1)
        txt = str(rec.get("raw_text") or "")
        if rec.get("source_url") or rec.get("source_ref"):
            source_ref = _norm_str(rec.get("source_url") or rec.get("source_ref"), source_ref)
        page_offsets.append((cursor, pg))
        chunks.append(txt)
        cursor += len(txt) + 2

    full_text = "\n\n".join(chunks)
    if not full_text.strip():
        return []

    def _page_for_offset(pos: int) -> int:
        found = 1
        for offset, pg in page_offsets:
            if offset <= pos:
                found = pg
            else:
                break
        return found

    report_no, jurisdiction, fiscal_year = _detect_report_metadata(full_text, source_ref)

    # 1. Try explicit "Para X.Y" format first if present (and not inline cross-references in a huge CAG PDF)
    legacy_matches = list(_LEGACY_PARA_HEADER_RE.finditer(full_text))
    numbered_matches = list(_NUMBERED_SECTION_RE.finditer(full_text))

    # Real multi-page CAG reports use numbered headings ("2.1 Infructuous expenditure..."); prefer them when rich
    if numbered_matches and (not legacy_matches or len(numbered_matches) >= max(3, len(legacy_matches) + 1)):
        use_numbered = True
        matches = numbered_matches
    else:
        use_numbered = False
        matches = legacy_matches
    if not matches:
        return []

    extracted: list[dict[str, Any]] = []
    seen_keys: dict[tuple[str, str], int] = {}

    for i, match in enumerate(matches):
        sec_num = match.group(1).strip()
        heading = " ".join(match.group(2).split()).strip()

        if use_numbered:
            # Validate section number (exclude numeric measurements like 3.50 Cu.m/MWh or 0.12)
            parts = sec_num.split(".")
            try:
                first_n = int(parts[0])
                second_n = int(parts[1])
            except (ValueError, IndexError):
                continue
            if first_n <= 0 or first_n > 25 or second_n > 60:
                continue
            if _UNIT_FALSE_POS_RE.search(heading):
                continue
            if _TOC_LINE_RE.search(heading):
                continue
            if ";" in heading and re.search(r"\d+\.\d+", heading):
                continue

        start = match.start()
        next_start = matches[i + 1].start() if i + 1 < len(matches) else len(full_text)
        end = min(next_start, start + 3000)
        snippet = full_text[start:end].strip()
        lower_snip = snippet.lower()
        lower_heading = heading.lower()

        # Detect typology
        typology = "compliance_observation"
        has_typology_keyword = False
        for keyword, label in _TYPOLOGY_KEYWORDS:
            if keyword in lower_heading or keyword in lower_snip:
                typology = label
                has_typology_keyword = True
                break

        # Skip pure introductory / ministry-overview boilerplate in real multi-page CAG PDFs
        if use_numbered and _BOILERPLATE_HEADING_RE.search(heading) and not has_typology_keyword:
            continue

        # Extract quantified rupee amount: prefer amount explicitly stated in the heading itself!
        heading_amounts = _extract_amounts_inr(heading)
        if heading_amounts:
            amount_inr = max(heading_amounts)
        else:
            body_amounts = _extract_amounts_inr(snippet)
            if not body_amounts:
                amount_inr = 0.0
            else:
                # Filter out macro national-GDP/total-debt table cells (> INR 5,00,000 Cr) when smaller finding figures exist
                bounded = [a for a in body_amounts if a <= 500_000.0 * _INR_CRORE]
                amount_inr = max(bounded) if bounded else max(body_amounts)

        # For real numbered CAG PDF sections, require a quantified rupee amount
        if use_numbered and amount_inr <= 0:
            continue

        atn_status = "pending"
        if any(word in lower_snip for word in ("settled", "dropped", "closed")):
            atn_status = "settled"
        elif any(word in lower_snip for word in ("unresolved", "awaited", "not furnished", "reply was awaited")):
            atn_status = "unresolved"

        repeat_count = 2 if any(w in lower_snip for w in ("repeat", "recurring", "earlier audit", "previous audit")) else 1
        years_pending = 2 if atn_status in {"pending", "unresolved"} else 0
        page_num = _page_for_offset(start)

        para_id = f"Para {sec_num}"
        dedup_key = (para_id, re.sub(r"[^a-z0-9]+", "", lower_heading)[:40])
        record = {
            "para_id": para_id,
            "report_no": report_no,
            "ministry_name": _neutralize_text(heading[:85]),
            "title": _neutralize_text(heading),
            "summary_text": _neutralize_text(" ".join(snippet.split())[:360]),
            "typology": typology,
            "quantified_impact_inr": amount_inr,
            "atn_status": atn_status,
            "years_pending": years_pending,
            "repeat_cycle_count": repeat_count,
            "page": page_num,
            "source_url": source_ref,
            "jurisdiction": jurisdiction,
            "fiscal_year": fiscal_year,
        }

        if dedup_key in seen_keys:
            existing_idx = seen_keys[dedup_key]
            if amount_inr > extracted[existing_idx]["quantified_impact_inr"]:
                extracted[existing_idx] = record
        else:
            seen_keys[dedup_key] = len(extracted)
            extracted.append(record)

    return extracted


def _extract_paras_from_raw_text(
    raw_text: str,
    page: int | None,
    source_ref: str,
) -> list[dict[str, Any]]:
    """Backwards-compatible single-page wrapper around multi-page CAG section extraction."""
    return _extract_paras_from_pages(
        [{"raw_text": raw_text, "page": page or 1, "source_url": source_ref}]
    )


def run_m6_cag_paras(
    records: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
) -> tuple[list[AuditLeadCandidate], int, float, str]:
    """M6: CAG Audit Para extraction and unresolved/repeat high-value para quantification."""
    ctx = context or {}
    min_impact_inr = float(ctx.get("min_impact_inr", 1.0 * _INR_CRORE))

    # Expand any PDF raw_text records into structured audit para dicts deterministically.
    raw_pages: list[dict[str, Any]] = []
    structured_records: list[dict[str, Any]] = []
    for rec in records:
        if "raw_text" in rec and not rec.get("para_id"):
            raw_pages.append(rec)
        else:
            structured_records.append(rec)

    normalized_records = list(structured_records)
    if raw_pages:
        normalized_records.extend(_extract_paras_from_pages(raw_pages))

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
        report_no = _neutralize_text(_norm_str(rec.get("report_no"), "CAG Audit Report"))
        entity = _neutralize_text(
            _norm_str(
                rec.get("ministry_name") or rec.get("entity_name") or rec.get("department"),
                "Concerned Ministry / Department",
            )
        )
        jurisdiction = _norm_str(rec.get("jurisdiction"), "Union of India")
        fiscal_year = _norm_str(rec.get("fiscal_year"), "FY 2023-24")
        typology = _norm_str(rec.get("typology"), "compliance_observation")
        para_title = _neutralize_text(_norm_str(rec.get("title"), f"{report_no} {para_id}"))
        para_excerpt = _neutralize_text(_norm_str(rec.get("summary_text") or rec.get("excerpt"), para_title))

        severity = "medium"
        if impact_inr >= 100.0 * _INR_CRORE or (repeat_cycles >= 3 and years_pending >= 3):
            severity = "critical"
        elif impact_inr >= 25.0 * _INR_CRORE or repeat_cycles >= 2 or years_pending >= 3:
            severity = "high"

        summary = (
            f"Public record audit follow-up lead on {report_no} ({para_id}) concerning {entity} "
            f"({jurisdiction}, {fiscal_year}): typology '{typology}' with quantified fiscal impact "
            f"of INR {impact_inr / _INR_CRORE:.2f} Cr remains '{status}' ({years_pending} yr(s) pending, "
            f"{repeat_cycles} audit cycle(s), PAC ATR filed={pac_atr_filed}). Excerpt: {para_excerpt}"
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

    top_leads, total_detected = _cap_leads_deterministically(leads, ctx)
    lead_note = (
        f"top {len(top_leads)} of {total_detected} unresolved/repeat lead(s)"
        if total_detected > len(top_leads)
        else f"{len(top_leads)} unresolved/repeat lead(s)"
    )
    summary_text = (
        f"M6 scanned {len(normalized_records)} CAG audit para record(s) "
        f"(INR {total_audited / _INR_CRORE:.2f} Cr quantified): {lead_note}, "
        f"{cleared_count} settled/cleared para(s)."
    )
    return top_leads, cleared_count, round(total_audited, 2), summary_text
