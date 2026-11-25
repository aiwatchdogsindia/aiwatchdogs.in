"""Deterministic Indian fiscal pacing (M5) and CAG audit para tracking (M6) engines."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime
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
_NON_FINDING_HEADING_RE = re.compile(
    r"(?i)^(?:receipts?\s+trends?|trends?\s+in\s+(?:receipts?|expenditure|revenue|debt)|"
    r"overview\s+of\s+|macro[\s-]?fiscal|public\s+debt\s+profile|treasury\s+bills?|"
    r"fiscal\s+indicators?|profile\s+of\s+|revenue\s+receipts?|capital\s+receipts?|"
    r"components?\s+of\s+receipts?)"
)
_FIGURE_OR_CHART_LINE_RE = re.compile(
    r"(?i)^\s*(?:fig(?:ure)?\.?|chart|graph|box|exhibit)\s+\d+(?:\.\d+)*\b"
)
# Macro-aggregate / Treasury Bill repayment lines that must NEVER be attributed as audit observation impacts
_MACRO_AGGREGATE_EXCLUSION_RE = re.compile(
    r"(?i)\b(?:"
    r"14[\s-]?day\s+treasury\s+bills?|"
    r"treasury\s+bills?|"
    r"repayments?\s+of\s+(?:14[\s-]?day|treasury|public\s+debt|market\s+loans|internal\s+debt)|"
    r"ways\s+and\s+means\s+advances|"
    r"gross\s+market\s+borrowings?|"
    r"total\s+receipts\s+of\s+the\s+union|"
    r"gross\s+tax\s+revenue|"
    r"net\s+proceeds\s+of\s+treasury|"
    r"consolidated\s+fund\s+of\s+india\s+receipts|"
    r"public\s+debt\s+repayments?"
    r")\b"
)
# Explicit audit-observation / financial-impact linkage keywords required in the same sentence/heading/cell
_AUDIT_IMPACT_LINK_RE = re.compile(
    r"(?i)\b(?:"
    r"infructuous|unfruitful|avoidable|"
    r"excess\s+payment|excess\s+expenditure|extra\s+expenditure|extra\s+cost|"
    r"idle\s+(?:outlay|investment|expenditure|funds?|machinery|equipment|asset)|"
    r"irregular(?:ly)?|diversion|non[\s-]?recovery|short\s+recovery|"
    r"non[\s-]?levy|short\s+levy|undue\s+benefit|cost\s+overrun|"
    r"loss\s+of|wasteful|overpayment|unauthorised|unauthorized|"
    r"unrecovered|unutilised|unutilized|remained\s+unspent|blocking\s+of\s+funds|"
    r"under[\s-]?assessment|short\s+collection|non[\s-]?realisation|non[\s-]?realization|"
    r"non[\s-]?release|depletion|"
    r"audit\s+observed|financial\s+impact|quantified\s+impact|audit\s+impact|"
    r"reconciled\s+and\s+settled|settled\s+by\s+the\s+department|"
    r"incurred\s+(?:an?\s+)?(?:avoidable|infructuous|unfruitful|extra|irregular|wasteful)|"
    r"resulted\s+in\s+(?:a\s+)?(?:loss|excess|avoidable|extra|unfruitful|infructuous)"
    r")\b"
)
_EXPLICIT_PENDING_DURATION_RE = re.compile(
    r"(?i)(?:atn|action\s+taken\s+note|reply|response|para(?:graph)?|observation)"
    r"[^\n.]{0,90}?(?:pending|unresolved|awaited|overdue)\s+(?:for\s+)?(?:over\s+|more\s+than\s+)?"
    r"(\d+(?:\.\d+)?)\s*(years?|yrs?|months?)\b"
)
_EXPLICIT_DATE_IN_TEXT_RE = re.compile(
    r"\b(?:(\d{1,2})\s+(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+(20\d{2})|"
    r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+(20\d{2})|"
    r"(20\d{2}-\d{2}-\d{2}))\b",
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

_OBDS_DOMAIN_STOPWORDS = frozenset(
    {
        "department",
        "dept",
        "of",
        "and",
        "the",
        "for",
        "state",
        "assam",
        "government",
        "govt",
        "ministry",
        "directorate",
        "commissioner",
        "grant",
        "demand",
        "no",
        "capital",
        "revenue",
        "outlay",
        "on",
        "in",
        "other",
        "general",
        "services",
        "projects",
        "scheme",
        "schemes",
        "development",
        "welfare",
        "administration",
    }
)


def _neutralize_text(text: str) -> str:
    """Neutralize prohibited BNS Sec 356 terms in quoted third-party audit text."""
    return _NEUTRAL_REPLACE_RE.sub("compliance-issue", text)


def _to_float(value: Any, default: float = 0.0) -> float:
    import math
    if value is None or value == "" or isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        parsed = float(value)
        return parsed if math.isfinite(parsed) else default
    text = str(value).strip().replace(",", "").replace("₹", "").replace("INR", "").strip()
    try:
        parsed = float(text)
    except ValueError:
        return default
    return parsed if math.isfinite(parsed) else default


def _to_float_opt(value: Any) -> float | None:
    import math
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    text = str(value).strip().replace(",", "").replace("₹", "").replace("INR", "").strip()
    if not text or text in {"--", "-", "NA", "N/A", "null", "None"}:
        return None
    try:
        parsed = float(text)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def _safe_int(value: Any, default: int = 0) -> int:
    import math
    val = _to_float(value, float(default))
    if not math.isfinite(val):
        return default
    try:
        return int(round(val))
    except OverflowError:
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


def _to_bool_opt(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text or text in {"na", "n/a", "null", "none", "unrecorded", "unknown"}:
        return None
    if text in {"1", "true", "yes", "y"}:
        return True
    if text in {"0", "false", "no", "n"}:
        return False
    return None


def _norm_str(value: Any, fallback: str = "") -> str:
    text = str(value).strip() if value is not None else ""
    return text if text else fallback


def _parse_date_opt(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value).strip().split("T", 1)[0]
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _get_inr(rec: dict[str, Any], inr_key: str, crore_key: str) -> float:
    val = _get_inr_opt(rec, inr_key, crore_key)
    return val if val is not None else 0.0


def _get_inr_opt(rec: dict[str, Any], inr_key: str, crore_key: str) -> float | None:
    if rec.get(inr_key) is not None and str(rec.get(inr_key)).strip() != "":
        return _to_float_opt(rec.get(inr_key))
    if rec.get(crore_key) is not None and str(rec.get(crore_key)).strip() != "":
        cr = _to_float_opt(rec.get(crore_key))
        return cr * _INR_CRORE if cr is not None else None
    return None


def _shift_fy_label(fy_raw: str, delta_years: int) -> str:
    m = re.search(r"(20\d{2})\s*[-–]\s*(?:20)?(\d{2})", fy_raw)
    if not m:
        return f"{fy_raw} (offset {delta_years:+d}y)"
    start_y = int(m.group(1)) + delta_years
    return f"FY {start_y}-{str(start_y + 1)[-2:]}"


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
    for idx, rec in enumerate(records):
        month_raw = _norm_str(rec.get("Month") or rec.get("month"))
        m_num = _MONTH_PREFIX_TO_NUM.get(month_raw.upper()[:3], 0)
        year_val = int(round(_to_float(rec.get("Year") or rec.get("year"), 0)))
        amt = _to_float(rec.get("Amount") or rec.get("amount"), 0.0)
        if m_num == 0 or year_val <= 1900:
            continue

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
                "be_inr": None,
                "re_inr": None,
                "actual_inr": 0.0,
                "q4_spend_inr": 0.0,
                "march_spend_inr": 0.0,
                "months_seen": set(),
                "row_indices": [],
                "source_url": _norm_str(rec.get("source_url"), "https://treasury.ap.gov.in"),
            }
            groups[key] = entry

        entry["actual_inr"] += amt
        entry["months_seen"].add(m_num)
        entry["row_indices"].append(int(rec.get("source_row_index") or (idx + 1)))
        if m_num in {1, 2, 3}:
            entry["q4_spend_inr"] += amt
        if m_num == 3:
            entry["march_spend_inr"] += amt

    rolled: list[dict[str, Any]] = []
    for entry in groups.values():
        if len(entry["months_seen"]) < 6 or entry["actual_inr"] <= 0:
            continue
        entry["months_covered"] = len(entry["months_seen"])
        del entry["months_seen"]
        rolled.append(entry)
    return rolled


def _domain_tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z]{3,}", (text or "").lower())
    return {w for w in words if w not in _OBDS_DOMAIN_STOPWORDS}


def _obds_unit_scale(unit_raw: str) -> tuple[float | None, str]:
    unit_str = (unit_raw or "").strip().lower()
    if not unit_str:
        return None, "unrecorded"
    if "crore" in unit_str:
        return _INR_CRORE, "crore"
    if "lakh" in unit_str:
        return _INR_LAKH, "lakh"
    if "thousand" in unit_str:
        return 1_000.0, "thousand"
    if "rupee" in unit_str or "inr" in unit_str:
        return 1.0, "inr"
    return None, f"unrecognized:{unit_str}"


def _rollup_obds_demands(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Roll up OBDS rows strictly requiring matched agency, account head, explicit unit, and fiscal period.

    Fixes Finding #5:
    - Never aggregates rows with mismatched departments/agencies or account heads under one grant label.
    - Never compares ``actuals_last_audited_year`` (FY T-2) against ``budget_estimates_previous_financial_year``
      or ``revised_estimates_previous_financial_year`` (FY T-1) as same-year actual vs estimate.
    - Never guesses blank ``budget_amount_unit``; rows lacking matched agency, account head, or explicit unit
      are marked unevaluable rather than emitted as variances or cleared.
    """
    # Pre-scan: detect demands that carry multiple distinct agencies/departments in the workbook
    agencies_per_demand: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for rec in records:
        demand_no = _norm_str(rec.get("demand_grant_no_en"))
        if not demand_no:
            continue
        state = _norm_str(rec.get("state_ut_name_eng"), "Assam")
        fy = _norm_str(rec.get("budget_financial_year"))
        agency = _norm_str(
            rec.get("dept_name_en")
            or rec.get("department_name_en")
            or rec.get("department_en")
            or rec.get("ministry_dept_en")
            or rec.get("agency_name_en")
            or rec.get("hod_name_en")
        )
        if agency:
            agencies_per_demand[(state, fy, demand_no)].add(agency.lower())

    groups: dict[tuple[str, str, str, str, str, str], dict[str, Any]] = {}
    unevaluable_rows = 0

    for idx, rec in enumerate(records):
        demand_no = _norm_str(rec.get("demand_grant_no_en"))
        if not demand_no:
            unevaluable_rows += 1
            continue
        demand_desc = _norm_str(rec.get("demand_grant_desc_en"), f"Demand {demand_no}")
        state = _norm_str(rec.get("state_ut_name_eng"), "Assam")
        book_fy = _norm_str(rec.get("budget_financial_year"))
        if not book_fy:
            unevaluable_rows += 1
            continue

        # Require explicit unit — never silently default blank unit to Lakhs
        scale, unit_label = _obds_unit_scale(_norm_str(rec.get("budget_amount_unit")))
        if scale is None:
            unevaluable_rows += 1
            continue

        # Require explicit agency/authority AND account head (major head)
        agency = _norm_str(
            rec.get("dept_name_en")
            or rec.get("department_name_en")
            or rec.get("department_en")
            or rec.get("ministry_dept_en")
            or rec.get("agency_name_en")
            or rec.get("hod_name_en")
        )
        major_head_code = _norm_str(
            rec.get("major_head_code")
            or rec.get("major_head_code_en")
            or rec.get("major_head")
        )
        major_head_desc = _norm_str(
            rec.get("major_head_desc_en")
            or rec.get("major_head_en")
            or rec.get("major_head_description")
        )
        if not agency or (not major_head_code and not major_head_desc):
            unevaluable_rows += 1
            continue

        # Reject rows where grant label contradicts agency / major head (e.g., Demand 65 Tourism carrying Power Dept / 4801)
        demand_tokens = _domain_tokens(demand_desc)
        agency_tokens = _domain_tokens(agency)
        head_tokens = _domain_tokens(major_head_desc)
        if demand_tokens and agency_tokens and not (demand_tokens & agency_tokens):
            if not (demand_tokens & head_tokens):
                unevaluable_rows += 1
                continue

        # Same-year fiscal period for previous-year BE and RE is (book_fy - 1 year);
        # actuals_last_audited_year is (book_fy - 2 years) and MUST NOT be compared against T-1 BE/RE!
        prev_fy_label = _shift_fy_label(book_fy, -1)
        prior_audited_fy_label = _shift_fy_label(book_fy, -2)

        head_key = major_head_code or major_head_desc
        key = (state, prev_fy_label, demand_no, agency, head_key, unit_label)
        entry = groups.get(key)
        if entry is None:
            mixed_agencies_in_demand = len(agencies_per_demand[(state, book_fy, demand_no)]) > 1
            head_display = (
                f"MH {major_head_code} ({major_head_desc})"
                if major_head_code and major_head_desc
                else (f"MH {major_head_code}" if major_head_code else major_head_desc)
            )
            entry = {
                "demand_no": f"Demand-{demand_no}/{head_key}",
                "ministry_name": f"{agency} — {demand_desc} (Demand {demand_no}, {head_display})",
                "jurisdiction": f"State:{state}",
                "fiscal_year": prev_fy_label,
                "budget_book_fy": f"FY {book_fy}" if not book_fy.upper().startswith("FY") else book_fy,
                "prior_audited_fy": prior_audited_fy_label,
                "obds_unit": unit_label,
                "obds_agency": agency,
                "obds_major_head": head_display,
                "obds_demand_mixed_agencies_quarantined": mixed_agencies_in_demand,
                "be_inr": 0.0,
                "re_inr": 0.0,
                # Only populate same-year actual_inr if an explicit same-year actual column exists!
                "actual_inr": None,
                "prior_audited_actuals_inr": 0.0,
                "has_same_year_actual": False,
                "row_indices": [],
                "source_url": _norm_str(rec.get("source_url"), "https://finance.assam.gov.in"),
            }
            groups[key] = entry

        be_prev = _to_float_opt(rec.get("budget_estimates_previous_financial_year"))
        re_prev = _to_float_opt(rec.get("revised_estimates_previous_financial_year"))
        act_last_audited = _to_float_opt(rec.get("actuals_last_audited_year"))
        same_year_act = _to_float_opt(
            rec.get("actuals_previous_financial_year") or rec.get("actuals_same_fy")
        )

        if be_prev is not None:
            entry["be_inr"] += be_prev * scale
        if re_prev is not None:
            entry["re_inr"] += re_prev * scale
        if act_last_audited is not None:
            entry["prior_audited_actuals_inr"] += act_last_audited * scale
        if same_year_act is not None:
            entry["actual_inr"] = (entry["actual_inr"] or 0.0) + same_year_act * scale
            entry["has_same_year_actual"] = True
        entry["row_indices"].append(int(rec.get("source_row_index") or (idx + 1)))

    return list(groups.values()), unevaluable_rows


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
                "be_crore": _to_float_opt(rec.get("budget_allocated_cr")),
                "re_crore": _to_float_opt(rec.get("budget_revised_cr")),
                "actual_crore": _to_float_opt(rec.get("budget_spent_cr")),
                "row_indices": [int(rec.get("source_row_index") or (idx + 1))],
                "source_url": _norm_str(rec.get("source_url"), "https://indiabudget.gov.in"),
            }
        )
    return out


def _eval_cga_monthly_accounts(
    records: list[dict[str, Any]],
    ctx: dict[str, Any],
) -> tuple[list[AuditLeadCandidate], int, float, str, dict[str, Any]]:
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
                fiscal_year_start=fy,
                fiscal_year_end=fy,
                source_locator=f"row={rec.get('source_row_index') or (idx + 1)}",
                amount_type="budget_provisional_actuals_vs_be",
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
                    "amount_type": "budget_provisional_actuals_vs_be",
                },
                rule_triggered="CGA Monthly Accounts Pacing & GFR 2017 Rule 62(3) (" + "; ".join(rules_hit) + ")",
                citations=[
                    {
                        "record_id": demand_no,
                        "row_id": _safe_int(rec.get("source_row_index"), idx + 1) or (idx + 1),
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
    return (
        top_leads,
        cleared_count,
        round(total_audited, 2),
        summary_text,
        {"unevaluable_count": 0, "amount_type": "budget_provisional_actuals_vs_be"},
    )


def run_m5_march_rush(
    records: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
) -> tuple[list[AuditLeadCandidate], int, float, str, dict[str, Any]]:
    """M5: March Rush (Q4 > 33% or March > 15% per GFR Rule 62(3)) & >25% same-year BE/RE vs Actuals variance.

    - Requires matched account head, agency/authority, explicit unit, and fiscal period for OBDS workbooks.
    - Preserves ``None`` for unobserved BE/RE/Actuals and PAC explanatory note filing status (never flags
      a missing PAC explanation when estimates or filing status are unobserved).
    """
    ctx = context or {}
    if not records:
        return (
            [],
            0,
            0.0,
            "M5 scanned 0 budget/expenditure accounts: 0 lead(s), 0 cleared record(s).",
            {"unevaluable_count": 0, "amount_type": "budget_expenditure"},
        )

    sample = records[0]
    if sample.get("record_type") == "cga_monthly_account":
        return _eval_cga_monthly_accounts(records, ctx)

    unevaluable_count = 0
    working_records = records
    if "Month" in sample and "Amount" in sample and ("District" in sample or "Major Head Description" in sample):
        working_records = _rollup_monthly_treasury(records)
    elif "demand_grant_no_en" in sample and "budget_estimates_previous_financial_year" in sample:
        working_records, obds_uneval = _rollup_obds_demands(records)
        unevaluable_count += obds_uneval
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
        fiscal_year = _norm_str(rec.get("fiscal_year"), "Unspecified")
        demand_no = _norm_str(rec.get("demand_no") or rec.get("grant_no"), f"Grant-{idx + 1}")

        be_inr = _get_inr_opt(rec, "be_inr", "be_crore")
        re_inr = _get_inr_opt(rec, "re_inr", "re_crore")
        actual_inr = _get_inr_opt(rec, "actual_inr", "actual_crore")

        has_be = be_inr is not None and be_inr > 0
        has_re = re_inr is not None and re_inr > 0
        has_actual = actual_inr is not None and actual_inr > 0

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

        if has_re:
            alloc_inr = re_inr  # type: ignore[assignment]
            allocation_basis = "revised_estimate"
        elif has_be:
            alloc_inr = be_inr  # type: ignore[assignment]
            allocation_basis = "budget_estimate"
        elif has_actual:
            alloc_inr = actual_inr  # type: ignore[assignment]
            allocation_basis = "observed_annual_actuals_estimates_unrecorded"
        else:
            unevaluable_count += 1
            continue

        total_audited += max(actual_inr or 0.0, alloc_inr, 0.0)

        has_q4_data = (
            rec.get("q4_pct") not in (None, "")
            or q4_inr > 0
            or any(rec.get(k) is not None for k in ("q4_spend_inr", "q4_spend_crore", "jan_inr", "feb_inr", "mar_inr"))
        )
        has_march_data = (
            rec.get("march_pct") not in (None, "")
            or march_inr > 0
            or any(rec.get(k) is not None for k in ("march_spend_inr", "march_spend_crore", "mar_inr"))
        )

        q4_pct: float | None = None
        if has_q4_data:
            q4_pct = (
                _to_float(rec.get("q4_pct"))
                if rec.get("q4_pct") is not None and str(rec.get("q4_pct")).strip() != ""
                else (q4_inr / alloc_inr) * 100.0
            )
            if q4_inr <= 0 and q4_pct > 0:
                q4_inr = alloc_inr * (q4_pct / 100.0)

        march_pct: float | None = None
        if has_march_data:
            march_pct = (
                _to_float(rec.get("march_pct"))
                if rec.get("march_pct") is not None and str(rec.get("march_pct")).strip() != ""
                else (march_inr / alloc_inr) * 100.0
            )
            if march_inr <= 0 and march_pct > 0:
                march_inr = alloc_inr * (march_pct / 100.0)

        # Compute same-year budget variances ONLY when both compared figures are actually observed
        be_var_pct: float | None = (
            ((actual_inr - be_inr) / be_inr * 100.0) if (has_be and has_actual) else None  # type: ignore[operator]
        )
        re_var_pct: float | None = (
            ((actual_inr - re_inr) / re_inr * 100.0) if (has_re and has_actual) else None  # type: ignore[operator]
        )
        be_re_shift_pct: float | None = (
            ((re_inr - be_inr) / be_inr * 100.0) if (has_be and has_re) else None  # type: ignore[operator]
        )

        # Preserve None/unknowns for MoF waivers & PAC explanatory note filing status!
        mof_waiver = _to_bool_opt(rec.get("mof_waiver_recorded"))
        pac_note_tabled = _to_bool_opt(rec.get("pac_explanatory_note_tabled"))

        q4_breach = (mof_waiver is not True) and (q4_pct is not None and q4_pct > q4_ceiling_pct)
        march_breach = (mof_waiver is not True) and (march_pct is not None and march_pct > march_ceiling_pct)
        variance_breach = (
            (be_var_pct is not None and abs(be_var_pct) > variance_ceiling_pct)
            or (re_var_pct is not None and abs(re_var_pct) > variance_ceiling_pct)
            or (be_re_shift_pct is not None and abs(be_re_shift_pct) > variance_ceiling_pct)
        )

        if not (q4_breach or march_breach or variance_breach):
            cleared_count += 1
            continue

        rules_hit: list[str] = []
        if q4_breach and q4_pct is not None:
            rules_hit.append(f"Q4_spend={q4_pct:.1f}% > {q4_ceiling_pct:.0f}% ceiling")
        if march_breach and march_pct is not None:
            rules_hit.append(f"March_spend={march_pct:.1f}% > {march_ceiling_pct:.0f}% ceiling")
        if variance_breach:
            vparts: list[str] = []
            if be_var_pct is not None:
                vparts.append(f"BE_variance={be_var_pct:+.1f}%")
            if re_var_pct is not None:
                vparts.append(f"RE_variance={re_var_pct:+.1f}%")
            if be_re_shift_pct is not None:
                vparts.append(f"RE_vs_BE={be_re_shift_pct:+.1f}%")
            rules_hit.append(", ".join(vparts) + f" (>{variance_ceiling_pct:.0f}%)")

        # Compute absolute budget variance ONLY across observed comparable same-year pairs
        comparable_vars: list[float] = []
        if has_actual and has_re:
            comparable_vars.append(abs(actual_inr - re_inr))  # type: ignore[operator]
        if has_actual and has_be:
            comparable_vars.append(abs(actual_inr - be_inr))  # type: ignore[operator]
        if has_be and has_re:
            comparable_vars.append(abs(re_inr - be_inr))  # type: ignore[operator]
        abs_var_inr: float | None = max(comparable_vars) if comparable_vars else None

        # NEVER flag a missing PAC explanation when estimates OR filing status are unobserved!
        pac_note_missing: bool | None
        if abs_var_inr is None or pac_note_tabled is None:
            pac_note_missing = None
        else:
            pac_note_missing = abs_var_inr >= pac_note_threshold_inr and (pac_note_tabled is False)
            if pac_note_missing:
                rules_hit.append("variance>=INR 100 Cr with recorded PAC explanatory note not tabled")

        excess_q4 = max(0.0, q4_inr - (alloc_inr * q4_ceiling_pct / 100.0)) if q4_breach else 0.0
        excess_mar = max(0.0, march_inr - (alloc_inr * march_ceiling_pct / 100.0)) if march_breach else 0.0
        exposure_inr = max(
            excess_q4,
            excess_mar,
            (abs_var_inr if (variance_breach and abs_var_inr is not None) else 0.0),
        )

        severity = "medium"
        if (march_pct is not None and march_pct >= 25.0) or (q4_pct is not None and q4_pct >= 45.0) or exposure_inr >= 500.0 * _INR_CRORE:
            severity = "critical"
        elif (q4_breach and march_breach) or exposure_inr >= 100.0 * _INR_CRORE:
            severity = "high"

        title_tag = (
            "March Rush & Budget Variance"
            if ((q4_breach or march_breach) and variance_breach)
            else ("Q4/March Expenditure Surge" if (q4_breach or march_breach) else "Same-Year BE/RE Budget Variance")
        )
        if q4_breach or march_breach:
            q4_lbl = f"{q4_pct:.1f}%" if q4_pct is not None else "N/A"
            mar_lbl = f"{march_pct:.1f}%" if march_pct is not None else "N/A"
            metric_badge = f"Q4 {q4_lbl} / Mar {mar_lbl}"
        else:
            b_lbl = f"BE {be_var_pct:+.1f}%" if be_var_pct is not None else "BE unrecorded"
            r_lbl = f"RE vs BE {be_re_shift_pct:+.1f}%" if be_re_shift_pct is not None else "RE unrecorded"
            metric_badge = f"{b_lbl} / {r_lbl}"

        if q4_pct is not None and march_pct is not None:
            pacing_clause = (
                f"Q4 spend reached {q4_pct:.1f}% (INR {q4_inr / _INR_CRORE:.2f} Cr) "
                f"and March spend reached {march_pct:.1f}% (INR {march_inr / _INR_CRORE:.2f} Cr) "
                f"against {allocation_basis} INR {alloc_inr / _INR_CRORE:.2f} Cr"
            )
        elif q4_pct is not None:
            pacing_clause = (
                f"Q4 spend reached {q4_pct:.1f}% (INR {q4_inr / _INR_CRORE:.2f} Cr) "
                f"(March share unrecorded) against {allocation_basis} INR {alloc_inr / _INR_CRORE:.2f} Cr"
            )
        elif march_pct is not None:
            pacing_clause = (
                f"March spend reached {march_pct:.1f}% (INR {march_inr / _INR_CRORE:.2f} Cr) "
                f"(Q4 total unrecorded) against {allocation_basis} INR {alloc_inr / _INR_CRORE:.2f} Cr"
            )
        else:
            pacing_clause = f"annual {allocation_basis} stands at INR {alloc_inr / _INR_CRORE:.2f} Cr"
        be_str = f"INR {be_inr / _INR_CRORE:.2f} Cr" if has_be else "unrecorded"
        re_str = f"INR {re_inr / _INR_CRORE:.2f} Cr" if has_re else "unrecorded"
        act_str = f"INR {actual_inr / _INR_CRORE:.2f} Cr" if has_actual else "unrecorded"
        pac_str = (
            "tabled"
            if pac_note_tabled is True
            else ("not tabled (recorded)" if pac_note_tabled is False else "unrecorded in source dataset")
        )
        summary = (
            f"Public record anomaly in fiscal pacing/variance for {entity} ({demand_no}, "
            f"{jurisdiction}, {fiscal_year}): {pacing_clause} (BE={be_str}, RE={re_str}, "
            f"same-year Actual={act_str}; PAC explanatory note status={pac_str}). "
            f"Rule markers: {'; '.join(rules_hit)}."
        )

        row_ids = rec.get("row_indices") if isinstance(rec.get("row_indices"), list) else [_safe_int(rec.get("source_row_index"), idx + 1) or (idx + 1)]
        locator = f"rows={','.join(str(r) for r in row_ids[:10])}"

        leads.append(
            AuditLeadCandidate(
                title=f"Fiscal Pacing Lead: {title_tag} ({metric_badge}) — {entity}",
                summary=summary,
                methodology_code="M5",
                entity_name=entity,
                jurisdiction=jurisdiction,
                fiscal_year=fiscal_year,
                fiscal_year_start=fiscal_year if fiscal_year != "Unspecified" else None,
                fiscal_year_end=fiscal_year if fiscal_year != "Unspecified" else None,
                source_locator=locator,
                amount_type=allocation_basis,
                monetary_exposure_inr=round(exposure_inr, 2),
                severity=severity,
                anomaly_metrics={
                    "demand_no": demand_no,
                    "allocation_basis": allocation_basis,
                    "be_inr": round(be_inr, 2) if has_be else None,
                    "re_inr": round(re_inr, 2) if has_re else None,
                    "actual_inr": round(actual_inr, 2) if has_actual else None,
                    "q4_spend_inr": round(q4_inr, 2) if q4_pct is not None else None,
                    "march_spend_inr": round(march_inr, 2) if march_pct is not None else None,
                    "q4_share_pct": round(q4_pct, 2) if q4_pct is not None else None,
                    "march_share_pct": round(march_pct, 2) if march_pct is not None else None,
                    "be_variance_pct": round(be_var_pct, 2) if be_var_pct is not None else None,
                    "re_variance_pct": round(re_var_pct, 2) if re_var_pct is not None else None,
                    "re_vs_be_shift_pct": round(be_re_shift_pct, 2) if be_re_shift_pct is not None else None,
                    "mof_waiver_recorded": mof_waiver,
                    "pac_explanatory_note_tabled": pac_note_tabled,
                    "pac_explanatory_note_missing": pac_note_missing,
                    "obds_agency": rec.get("obds_agency"),
                    "obds_major_head": rec.get("obds_major_head"),
                    "obds_unit": rec.get("obds_unit"),
                    "prior_audited_fy": rec.get("prior_audited_fy"),
                    "prior_audited_actuals_inr": (
                        round(rec["prior_audited_actuals_inr"], 2)
                        if rec.get("prior_audited_actuals_inr") is not None
                        else None
                    ),
                },
                rule_triggered="GFR 2017 Rule 62(3) & Budget Variance Rules (" + "; ".join(rules_hit) + ")",
                citations=[
                    {
                        "record_id": demand_no,
                        "row_id": row_ids[0] if row_ids else idx + 1,
                        "source_url": _norm_str(rec.get("source_url"), "https://cga.nic.in"),
                        "excerpt": (
                            f"{entity} ({demand_no}, {fiscal_year}): BE={be_str}, RE={re_str}, "
                            f"Actual={act_str}, Q4={f'{q4_pct:.1f}%' if q4_pct is not None else 'N/A'}, "
                            f"March={f'{march_pct:.1f}%' if march_pct is not None else 'N/A'}"
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
        f"M5 scanned {len(working_records)} evaluable budget/expenditure accounts "
        f"(INR {total_audited / _INR_CRORE:.2f} Cr audited): {lead_note}, "
        f"{cleared_count} cleared record(s), {unevaluable_count} unevaluable record(s)."
    )
    return (
        top_leads,
        cleared_count,
        round(total_audited, 2),
        summary_text,
        {"unevaluable_count": unevaluable_count, "amount_type": "budget_expenditure"},
    )


def _extract_amounts_inr(text: str) -> list[float]:
    amounts: list[float] = []
    for amt_match in _RUPEE_AMOUNT_RE.finditer(text):
        num = _to_float(amt_match.group(1))
        unit = amt_match.group(2).lower()
        candidate = num * (_INR_CRORE if unit.startswith("cr") else _INR_LAKH)
        if candidate > 0:
            amounts.append(candidate)
    return amounts


def _strip_figure_blocks(text: str) -> list[str]:
    """Return non-figure/chart lines from a CAG section body so adjacent charts are never misattributed."""
    lines = text.splitlines()
    kept: list[str] = []
    in_figure_block = False
    for line in lines:
        stripped = line.strip()
        if _FIGURE_OR_CHART_LINE_RE.match(stripped):
            in_figure_block = True
            continue
        if in_figure_block:
            if not stripped:
                in_figure_block = False
                continue
            # Stay in figure block while lines look like chart legends, axes, sources, or treasury-bill captions
            if (
                stripped.lower().startswith(("source:", "note:", "repayment", "14-day", "treasury"))
                or len(stripped) < 45
                or _MACRO_AGGREGATE_EXCLUSION_RE.search(stripped)
            ):
                continue
            # Real prose sentence ends figure block
            in_figure_block = False
        kept.append(line)
    return kept


_ABBREV_DOT_RE = re.compile(
    r"\b(Rs|Cr|No|Nos|Sr|Dr|Mr|Mrs|Ms|Fig|Sec|Art|Vol|Ltd|Pvt|Para|Dept|Govt|pp|p)\.\s+",
    re.IGNORECASE,
)


def _split_sentences(text: str) -> list[str]:
    """Split prose into sentences without breaking on Indian abbreviations like 'Rs. 18.75 crore'."""
    protected = _ABBREV_DOT_RE.sub(lambda m: f"{m.group(1)}.__ABBREV_SP__", text)
    return [s.replace("__ABBREV_SP__", " ").strip() for s in re.split(r"(?<=[.!?])\s+", protected) if s.strip()]


def _extract_linked_cag_amount(
    heading: str,
    body_text: str,
) -> tuple[float | None, str, str | None]:
    """Require explicit heading, sentence, or table-cell linkage tying an amount to an audit observation.

    Never attributes nearby macro fiscal aggregates (such as FY 2024-25 14-day Treasury Bill repayments
    in Figure 2.6) to an audit observation paragraph.
    Returns ``(amount_inr_or_None, linkage_kind, linked_evidence_sentence)``.
    """
    # 1. Explicit linkage inside the section heading itself
    if (
        _AUDIT_IMPACT_LINK_RE.search(heading)
        and not _MACRO_AGGREGATE_EXCLUSION_RE.search(heading)
        and not _NON_FINDING_HEADING_RE.search(heading)
    ):
        heading_amounts = _extract_amounts_inr(heading)
        if heading_amounts:
            return max(heading_amounts), "heading_explicit", heading.strip()

    # 2. Explicit sentence or table-row linkage in the section body (excluding Figure/Chart blocks)
    clean_lines = _strip_figure_blocks(body_text)
    best_amount: float | None = None
    best_kind = "unrecorded"
    best_sentence: str | None = None

    # First inspect table-like rows (tab/pipe/multi-space delimited) individually
    prose_lines: list[str] = []
    for line in clean_lines:
        stripped = line.strip()
        if not stripped:
            continue
        if _FIGURE_OR_CHART_LINE_RE.match(stripped) or _MACRO_AGGREGATE_EXCLUSION_RE.search(stripped):
            continue
        is_table_row = ("|" in stripped) or ("\t" in line) or bool(re.search(r"\S\s{3,}\S", stripped))
        if is_table_row and _AUDIT_IMPACT_LINK_RE.search(stripped):
            row_amts = _extract_amounts_inr(stripped)
            if row_amts:
                cand = max(row_amts)
                if best_amount is None or cand > best_amount:
                    best_amount = cand
                    best_kind = "table_cell_explicit"
                    best_sentence = " ".join(stripped.split())[:260]
        else:
            prose_lines.append(stripped)

    # Next inspect joined prose sentences (preserving 'Rs. X crore' intact)
    prose = " ".join(prose_lines)
    for s in _split_sentences(prose):
        if _FIGURE_OR_CHART_LINE_RE.search(s) or _MACRO_AGGREGATE_EXCLUSION_RE.search(s):
            continue
        if not _AUDIT_IMPACT_LINK_RE.search(s):
            continue
        sent_amts = _extract_amounts_inr(s)
        if sent_amts:
            cand = max(sent_amts)
            if best_amount is None or cand > best_amount:
                best_amount = cand
                best_kind = "sentence_explicit"
                best_sentence = s[:260]

    return best_amount, best_kind, best_sentence


def _extract_dated_disposition(
    text: str,
) -> tuple[str, float | None, str | None, bool | None]:
    """Extract ATN status, pending age (years), disposition evidence date, and PAC ATR status.

    Never defaults ATN status to ``pending`` or fabricates ``years_pending = 2`` without dated/explicit evidence.
    """
    lower = text.lower()

    atn_status = "unrecorded"
    if any(w in lower for w in ("reconciled and settled", "para was settled", "settled by the department", "dropped", "closed")):
        atn_status = "settled"
    elif re.search(
        r"(?i)\b(?:atn|action\s+taken\s+note|departmental\s+reply|reply|response)\b[^\n.]{0,60}?"
        r"\b(?:unresolved|awaited|not\s+furnished|not\s+received|pending|still\s+awaited|no\s+reply)\b",
        text,
    ):
        atn_status = "unresolved"

    pac_atr_filed: bool | None = None
    if re.search(
        r"(?i)\bpac\s+atr\b[^\n.]{0,40}?\b(?:(?:not\s+(?:yet\s+)?|yet\s+to\s+be\s+)(?:tabled|filed|submitted)|pending|awaited)\b",
        text,
    ):
        pac_atr_filed = False
    elif re.search(r"(?i)\bpac\s+atr\b[^\n.]{0,40}?\b(?:tabled|filed|submitted)\b", text):
        pac_atr_filed = True

    # Extract pending duration ONLY when explicitly stated or backed by two dated follow-up milestones
    years_pending: float | None = None
    evidence_date: str | None = None

    dur_match = _EXPLICIT_PENDING_DURATION_RE.search(text)
    if dur_match:
        val = float(dur_match.group(1))
        unit = dur_match.group(2).lower()
        years_pending = round(val / 12.0, 2) if unit.startswith("month") else round(val, 2)
        evidence_date = "explicit_duration_in_text"
    else:
        # Look for explicit dated ATN/follow-up sentences containing two parseable dates
        for sent in _split_sentences(text):
            if not re.search(r"(?i)\b(?:atn|action\s+taken\s+note|reply|awaited|pending|unresolved)\b", sent):
                continue
            found_dates: list[date] = []
            for m in _EXPLICIT_DATE_IN_TEXT_RE.finditer(sent):
                if m.group(6):
                    dt = _parse_date_opt(m.group(6))
                elif m.group(1) and m.group(2) and m.group(3):
                    mnum = _MONTH_PREFIX_TO_NUM.get(m.group(2).upper()[:3], 1)
                    try:
                        dt = date(int(m.group(3)), mnum, int(m.group(1)))
                    except ValueError:
                        dt = None
                elif m.group(4) and m.group(5):
                    mnum = _MONTH_PREFIX_TO_NUM.get(m.group(4).upper()[:3], 1)
                    dt = date(int(m.group(5)), mnum, 1)
                else:
                    dt = None
                if dt is not None:
                    found_dates.append(dt)
            if len(found_dates) >= 2:
                earliest, latest = min(found_dates), max(found_dates)
                if latest > earliest:
                    years_pending = round((latest - earliest).days / 365.25, 2)
                    evidence_date = f"{earliest.isoformat()}..{latest.isoformat()}"
                    break

    return atn_status, years_pending, evidence_date, pac_atr_filed


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
    fiscal_year = f"FY {fy_match.group(0).replace('–', '-')}" if fy_match else "Unspecified"
    return report_no, jurisdiction, fiscal_year


def _extract_paras_from_pages(
    page_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Deterministically extract structured CAG Audit Para records with strict amount & disposition linkage."""
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

    legacy_matches = list(_LEGACY_PARA_HEADER_RE.finditer(full_text))
    numbered_matches = list(_NUMBERED_SECTION_RE.finditer(full_text))

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
        body_only = full_text[match.end():end].strip()
        lower_snip = snippet.lower()
        lower_heading = heading.lower()

        typology = "compliance_observation"
        has_typology_keyword = False
        for keyword, label in _TYPOLOGY_KEYWORDS:
            if keyword in lower_heading or keyword in lower_snip:
                typology = label
                has_typology_keyword = True
                break

        if use_numbered and (_BOILERPLATE_HEADING_RE.search(heading) or _NON_FINDING_HEADING_RE.search(heading)) and not has_typology_keyword:
            continue

        # Require explicit heading, sentence, or table-cell linkage tying an amount to the observation!
        amount_inr, amount_linkage, linked_sentence = _extract_linked_cag_amount(heading, body_only)

        # For real numbered CAG PDF sections, require an explicitly linked audit impact amount
        if use_numbered and (amount_inr is None or amount_inr <= 0):
            continue

        # Require explicit disposition/dated follow-up evidence (never fabricate "pending 2 years"!)
        atn_status, years_pending, disposition_date, pac_atr_filed = _extract_dated_disposition(snippet)
        repeat_count = 2 if any(w in lower_snip for w in ("repeat", "recurring", "earlier audit", "previous audit")) else 1
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
            "amount_linkage": amount_linkage,
            "linked_evidence_sentence": _neutralize_text(linked_sentence) if linked_sentence else None,
            "atn_status": atn_status,
            "years_pending": years_pending,
            "disposition_evidence_date": disposition_date,
            "pac_atr_filed": pac_atr_filed,
            "repeat_cycle_count": repeat_count,
            "page": page_num,
            "source_url": source_ref,
            "jurisdiction": jurisdiction,
            "fiscal_year": fiscal_year,
        }

        if dedup_key in seen_keys:
            existing_idx = seen_keys[dedup_key]
            existing_amt = extracted[existing_idx].get("quantified_impact_inr") or 0.0
            if (amount_inr or 0.0) > existing_amt:
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
) -> tuple[list[AuditLeadCandidate], int, float, str, dict[str, Any]]:
    """M6: CAG Audit Para extraction and quantified observation tracker.

    - Requires explicit sentence/heading/table-cell linkage for PDF amounts.
    - Preserves ``atn_status="unrecorded"`` and ``years_pending=None`` when dated follow-up
      evidence is absent (never fabricates "pending 2 years").
    """
    ctx = context or {}
    min_impact_inr = float(ctx.get("min_impact_inr", 1.0 * _INR_CRORE))

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
    unevaluable_count = 0
    total_audited = 0.0

    for idx, rec in enumerate(normalized_records):
        impact_inr = _get_inr_opt(rec, "quantified_impact_inr", "impact_crore")
        if impact_inr is not None and impact_inr > 0:
            total_audited += impact_inr

        status = _norm_str(rec.get("atn_status") or rec.get("status"), "unrecorded").lower()
        years_pending_val = _to_float_opt(rec.get("years_pending"))
        disposition_date = _norm_str(rec.get("disposition_evidence_date")) or None

        # Allow deterministic age calculation from explicit structured dates if provided
        if years_pending_val is None:
            ref_dt = _parse_date_opt(rec.get("audit_date") or rec.get("tabling_date"))
            follow_dt = _parse_date_opt(rec.get("follow_up_date") or rec.get("disposition_date") or ctx.get("as_of_date"))
            if ref_dt and follow_dt and follow_dt > ref_dt and status in {"pending", "unresolved"}:
                years_pending_val = round((follow_dt - ref_dt).days / 365.25, 2)
                disposition_date = f"{ref_dt.isoformat()}..{follow_dt.isoformat()}"

        repeat_cycles = int(round(_to_float(rec.get("repeat_cycle_count"), 1)))
        pac_atr_filed = _to_bool_opt(rec.get("pac_atr_filed"))
        amount_linkage = _norm_str(
            rec.get("amount_linkage"),
            "structured_record" if (impact_inr is not None and impact_inr > 0) else "unrecorded",
        )
        linked_sentence = _norm_str(rec.get("linked_evidence_sentence")) or None

        is_resolved = status in {"settled", "resolved", "closed", "dropped"}
        if is_resolved:
            cleared_count += 1
            continue

        triggers: list[str] = []
        if impact_inr is not None and impact_inr >= min_impact_inr:
            triggers.append(
                f"quantified_para_impact=INR {impact_inr / _INR_CRORE:.2f} Cr (>= INR {min_impact_inr / _INR_CRORE:.1f} Cr; linkage={amount_linkage})"
            )
        if years_pending_val is not None and years_pending_val >= 2.0:
            triggers.append(f"atn_pending={years_pending_val:g}_years (dated_evidence={disposition_date or 'recorded'})")
        if repeat_cycles >= 2:
            triggers.append(f"repeat_audit_cycles={repeat_cycles}")

        if not triggers:
            if impact_inr is None and status == "unrecorded":
                unevaluable_count += 1
            else:
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
        fiscal_year = _norm_str(rec.get("fiscal_year"), "Unspecified")
        typology = _norm_str(rec.get("typology"), "compliance_observation")
        para_title = _neutralize_text(_norm_str(rec.get("title"), f"{report_no} {para_id}"))
        para_excerpt = _neutralize_text(
            _norm_str(linked_sentence or rec.get("summary_text") or rec.get("excerpt"), para_title)
        )
        page_num = int(rec.get("page") or 1)

        exposure_val = impact_inr if (impact_inr is not None and impact_inr > 0) else 0.0
        severity = "medium"
        if exposure_val >= 100.0 * _INR_CRORE or (repeat_cycles >= 3 and (years_pending_val or 0.0) >= 3.0):
            severity = "critical"
        elif exposure_val >= 25.0 * _INR_CRORE or repeat_cycles >= 2 or (years_pending_val or 0.0) >= 3.0:
            severity = "high"

        impact_text = (
            f"INR {exposure_val / _INR_CRORE:.2f} Cr (linkage: {amount_linkage})"
            if impact_inr is not None and impact_inr > 0
            else "unrecorded"
        )
        age_text = (
            f"{years_pending_val:g} yr(s) pending (evidence: {disposition_date or 'recorded'})"
            if years_pending_val is not None
            else "follow-up age unrecorded"
        )
        pac_text = (
            "filed"
            if pac_atr_filed is True
            else ("not filed (recorded)" if pac_atr_filed is False else "unrecorded")
        )
        lead_prefix = "CAG Unresolved Para Lead" if status in {"pending", "unresolved"} else "CAG Audit Observation Lead"

        summary = (
            f"Public record audit follow-up lead on {report_no} ({para_id}, PDF p. {page_num}) concerning {entity} "
            f"({jurisdiction}, {fiscal_year}): typology '{typology}' with linked fiscal impact "
            f"{impact_text}; ATN status='{status}' ({age_text}, {repeat_cycles} audit cycle(s), "
            f"PAC ATR={pac_text}). Evidence excerpt: {para_excerpt}"
        )

        leads.append(
            AuditLeadCandidate(
                title=f"{lead_prefix}: {para_id} ({typology.replace('_', ' ')}) — {entity}",
                summary=summary,
                methodology_code="M6",
                entity_name=entity,
                jurisdiction=jurisdiction,
                fiscal_year=fiscal_year,
                fiscal_year_start=fiscal_year if fiscal_year != "Unspecified" else None,
                fiscal_year_end=fiscal_year if fiscal_year != "Unspecified" else None,
                source_locator=f"page={page_num};para={para_id}",
                amount_type="cag_quantified_impact",
                monetary_exposure_inr=round(exposure_val, 2),
                severity=severity,
                anomaly_metrics={
                    "report_no": report_no,
                    "para_id": para_id,
                    "page": page_num,
                    "typology": typology,
                    "quantified_impact_inr": round(impact_inr, 2) if impact_inr is not None else None,
                    "quantified_impact_crore": round(impact_inr / _INR_CRORE, 2) if impact_inr is not None else None,
                    "amount_linkage": amount_linkage,
                    "linked_evidence_sentence": linked_sentence,
                    "atn_status": status,
                    "years_pending": years_pending_val,
                    "disposition_evidence_date": disposition_date,
                    "repeat_cycle_count": repeat_cycles,
                    "pac_atr_filed": pac_atr_filed,
                    "amount_type": "cag_quantified_impact",
                },
                rule_triggered="CAG Audit Para / APMS ATN Follow-Up Tracker (" + "; ".join(triggers) + ")",
                citations=[
                    {
                        "record_id": f"{report_no}:{para_id}",
                        "para_id": para_id,
                        "page": page_num,
                        "row_id": idx + 1,
                        "source_url": _norm_str(rec.get("source_url"), "https://cag.gov.in"),
                        "excerpt": para_excerpt[:240],
                    }
                ],
                benign_explanations=[
                    "The department may have submitted an updated Action Taken Note (ATN) on APMS currently awaiting vetting by the Office of the CAG or the Public Accounts Committee.",
                    "Quantified audit figures sometimes represent gross project outlay where partial recoveries, asset commissioning, or contractual arbitration occurred after audit cut-off.",
                    "Where ATN disposition or follow-up dates are unrecorded in the tabled report PDF, separate APMS or PAC Action Taken Report records are needed to verify current closure status.",
                ],
                suggested_authority=entity,
            )
        )

    top_leads, total_detected = _cap_leads_deterministically(leads, ctx)
    lead_note = (
        f"top {len(top_leads)} of {total_detected} audit para lead(s)"
        if total_detected > len(top_leads)
        else f"{len(top_leads)} audit para lead(s)"
    )
    summary_text = (
        f"M6 scanned {len(normalized_records)} linked CAG audit para record(s) "
        f"(INR {total_audited / _INR_CRORE:.2f} Cr quantified): {lead_note}, "
        f"{cleared_count} settled/cleared para(s), {unevaluable_count} unevaluable para(s)."
    )
    return (
        top_leads,
        cleared_count,
        round(total_audited, 2),
        summary_text,
        {"unevaluable_count": unevaluable_count, "amount_type": "cag_quantified_impact"},
    )
