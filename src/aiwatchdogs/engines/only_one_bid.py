"""P7: Only One Bid — deterministic single-bidder & repeat-winner procurement audit engine.

Hook: "Which public purchases consistently attract just one bidder — and keep going to the
same winner across 10 years of Indian government tenders?"

Reads checksummed public Indian government procurement / OCDS datasets (CSV, gzipped CSV,
JSON, JSONL, ZIP, or XLSX), locates the pinned tender/award record(s) and procuring-authority
cohort directly from verified bytes, and deterministically evaluates:

1. Single-bidder competition status (distinguishing bids received vs technically qualified
   bids, separating blank/unknown from literal zero, and transparently clearing documented
   PAC/OEM/emergency exemptions unless outweighed by sustained multi-award dominance).
2. Repeat-winner concentration across the procuring authority's 10-year award cohort
   (normalized supplier/contractor entity matching, win counts, single-bidder win counts,
   authority award/value share, and consecutive win streaks).
3. Anti-double-counting monetary exposure (``monetary_exposure_inr`` always equals the target
   tender's own value, while ``winner_cohort_total_inr`` is tracked separately in
   ``anomaly_metrics``).
4. Strict BNS Sec 356 neutrality, DPDP Act 2023 PII scrubbing, jurisdiction-aware statutory
   framing (Central GFR 2017/CVC vs State Procurement Rules), and verifiable citations.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from aiwatchdogs.core.models import AuditLeadCandidate
from aiwatchdogs.core.safety import scan_forbidden_terms, scrub_pii, validate_safe_http_url

ONLY_ONE_BID_PROJECT_SLUG = "only-one-bid"
ONLY_ONE_BID_METHODOLOGY = "P7"
P7_DEFAULT_HOOK = (
    "Which public purchases consistently attract just one bidder — and keep going to the "
    "same winner across 10 years of Indian government tenders?"
)

SIGNAL_CATEGORIES: tuple[str, ...] = (
    "single_bidder_repeat_winner",
    "unexplained_single_bidder",
    "repeat_winner_concentration",
    "short_window_or_process_gap",
)

VALID_AUDIT_MODES: frozenset[str] = frozenset({"single_bidder", "repeat_winner", "both"})
VALID_JURISDICTION_LEVELS: frozenset[str] = frozenset({"central", "state"})
VALID_EXEMPTION_STATUSES: frozenset[str] = frozenset(
    {
        "none_recorded",
        "documented_pac_oem",
        "documented_emergency_or_direct",
    }
)

# Canonical P7 task ID format: p7-<cohort>-<start-year>-<seq5>
P7_TASK_ID_RE = re.compile(r"^p7-(sb|rw|bo)-(20\d{2})-(\d{5})$")
_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,58}$")
_AUTH_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
_FY_RE = re.compile(r"^(?:FY)?(20\d{2})(?:-(\d{2}))?$", re.IGNORECASE)
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Deterministic P7 procurement thresholds
DE_MINIMIS_VALUE_INR = 500_000.0  # INR 5 lakh (GFR 2017 Rule 154/155 LPC ceiling)
SHORT_WINDOW_DAYS = 14  # CVC / DoE Manual open-tender bid period benchmark
CRITICAL_SHORT_WINDOW_DAYS = 7
AUTHORITY_MIN_EVALUABLE_TENDERS = 5
AUTHORITY_HIGH_SB_RATE_PCT = 35.0
REPEAT_WINNER_MIN_WINS = 3
REPEAT_WINNER_PERSISTENT_WINS = 5
REPEAT_WINNER_SB_MIN_WINS = 2
REPEAT_WINNER_SHARE_COUNT_PCT = 25.0
REPEAT_WINNER_SHARE_VALUE_PCT = 30.0
REPEAT_WINNER_STREAK_MIN = 3
EXEMPT_OVERRIDE_MIN_WINS = 5
EXEMPT_OVERRIDE_MIN_VALUE_INR = 5_000_000.0  # INR 50 lakh
EXEMPT_OVERRIDE_SHARE_PCT = 40.0
PROCESS_GAP_MIN_VALUE_INR = 2_500_000.0  # INR 25 lakh
PROCESS_GAP_14D_MIN_VALUE_INR = 5_000_000.0  # INR 50 lakh
MAX_RELATED_RECORD_IDS = 25
MAX_FIELD_MAP_KEYS = 32

# BNS Sec 356 external-text neutralizer (preserves institutional "anti-corruption")
_ACB_RE = re.compile(r"\banti[\s-]+corruption\b", re.IGNORECASE)
_NEUTRAL_REPLACE_RE = re.compile(
    r"(?<![a-z])(?:scam(?:s|med|mer|mers)?|corrupt(?:ed|ing|ly|ion)?|brib(?:e|es|ed|ing|ery)|"
    r"fraud(?:s|ulent|ulently|ster|sters)?|kickback(?:s)?|guilty|stolen|rigged|"
    r"crook(?:s|ed)?|loot(?:s|ed|ing|er|ers)?|embezzl(?:e|es|ed|ing|ement))(?![a-z])",
    re.IGNORECASE,
)

# Entity-name canonicalization patterns
_ENTITY_PREFIX_RE = re.compile(r"^(?:m\s*/\s*s\.?|messrs\.?|ms\.?|shri\.?|smt\.?|sri\.?|dr\.?|er\.?)\s+", re.IGNORECASE)
_ENTITY_SUFFIX_RE = re.compile(
    r"\b(?:private\s+limited|pvt\.?\s*ltd\.?|p\.?\s*ltd\.?|public\s+limited|limited|ltd\.?|"
    r"llp|l\.l\.p\.?|opc|one\s+person\s+company|incorporated|inc\.?|corp\.?|corporation|"
    r"proprietorship|proprietary|co\.?)\b\.?$",
    re.IGNORECASE,
)
_NON_ALNUM_SPACE_RE = re.compile(r"[^a-z0-9\s]+")
_MULTI_SPACE_RE = re.compile(r"\s+")
_CTRL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]+")

# Exemption detection patterns
_PAC_OEM_RE = re.compile(
    r"\b(?:pac|proprietary\s+article(?:\s+certificate)?|proprietary|oem|original\s+equipment\s+manufacturer|"
    r"patented|sole\s+(?:source|authorized|authorised|manufacturer|distributor|dealer|oem)|"
    r"gfr[\s-]*166|rule[\s-]*166)\b",
    re.IGNORECASE,
)
_DIRECT_METHOD_RE = re.compile(
    r"\b(?:direct|direct_exempt|nomination|single[\s_-]*tender|emergency|disaster|force[\s_-]*majeure|"
    r"limited[\s_-]*exempt|psu[\s_-]*nomination)\b",
    re.IGNORECASE,
)
_JUSTIFICATION_MARKER_RE = re.compile(
    r"\b(?:emergency|disaster|flood|cyclone|covid|pandemic|force\s+majeure|urgent|urgency|"
    r"inter[\s-]*government|government\s+psu|cpsu|statutory\s+monopoly|pac|proprietary|oem|"
    r"sole\s+source|repeat\s+order|rate\s+contract|competent\s+authority|gfr|rule\s+166|rule\s+194|"
    r"justification|waiver|exempt(?:ed|ion)?)\b",
    re.IGNORECASE,
)

# Default logical-field -> candidate column fallbacks
_DEFAULT_FIELD_FALLBACKS: dict[str, tuple[str, ...]] = {
    "tender_id": ("tender_id", "tender/id", "contract_id", "award_id", "bid_no", "bid_number", "nit_no", "id"),
    "ocid": ("ocid", "release_id"),
    "title": (
        "tender_title",
        "tender/title",
        "work_description",
        "description",
        "title",
        "item_description",
        "subject",
    ),
    "buyer": (
        "buyer_name",
        "buyer/name",
        "procuring_entity",
        "tender/procuringEntity/name",
        "authority",
        "department",
        "organization",
        "organisation",
        "ministry",
        "entity_name",
    ),
    "winner": (
        "vendor_name",
        "supplier_name",
        "winner_name",
        "awardee",
        "contractor_name",
        "bidder_name",
        "awards/0/suppliers/0/name",
        "supplier",
        "vendor",
    ),
    "bid_count": (
        "bid_count",
        "bids_received",
        "num_bids",
        "number_of_bids",
        "number_of_tenderers",
        "tender/numberOfTenderers",
        "total_bids",
        "bidder_count",
    ),
    "qualified_bid_count": (
        "qualified_bid_count",
        "technically_qualified_bids",
        "qualified_bidders",
        "number_of_qualified_tenderers",
        "tender/numberOfQualifiedTenderers",
        "valid_bids",
        "responsive_bids",
    ),
    "procurement_method": (
        "procurement_method",
        "tender/procurementMethod",
        "tender_type",
        "bidding_type",
        "method",
    ),
    "procurement_method_details": (
        "procurement_method_details",
        "tender/procurementMethodDetails",
        "tender_category",
        "method_details",
    ),
    "exemption_reason": (
        "exemption_reason",
        "procurement_method_rationale",
        "tender/procurementMethodRationale",
        "rationale",
        "justification",
        "remarks",
    ),
    "award_value_inr": (
        "award_value_inr",
        "award_amount_inr",
        "awarded_value_inr",
        "contract_value_inr",
        "awards/0/value/amount",
        "award_value",
        "contract_value",
        "awarded_amount",
    ),
    "tender_estimate_inr": (
        "tender_estimate_inr",
        "tender_value_inr",
        "estimated_value_inr",
        "tender/value/amount",
        "estimated_cost",
        "estimate_inr",
    ),
    "publish_date": (
        "tender_published_date",
        "publish_date",
        "tender_start_date",
        "tender/datePublished",
        "tender/tenderPeriod/startDate",
        "nit_date",
    ),
    "close_date": (
        "tender_end_date",
        "close_date",
        "bid_submission_end_date",
        "tender/tenderPeriod/endDate",
        "closing_date",
    ),
    "award_date": (
        "award_date",
        "contract_date",
        "aoc_date",
        "awards/0/date",
        "award/date",
        "awards/0/contractPeriod/startDate",
    ),
    "bid_window_days": (
        "bid_window_days",
        "tender/tenderPeriod/durationInDays",
        "window_days",
    ),
    "category": (
        "item_category",
        "category",
        "product_category",
        "work_category",
        "tender/mainProcurementCategory",
        "tenderclassification/description",
        "tender/classification/description",
    ),
    "record_url": (
        "record_url",
        "tender_url",
        "award_url",
        "source_url",
        "url",
    ),
    "fiscal_year": ("fiscal_year", "fy", "financial_year"),
    "jurisdiction": ("jurisdiction", "state", "region"),
}

_BENIGN_EXPLANATIONS: tuple[str, ...] = (
    "A valid Proprietary Article Certificate (PAC), Original Equipment Manufacturer (OEM) compatibility "
    "requirement, or patented spares/sole-authorized-dealer exemption under GFR 2017 Rule 166 or equivalent "
    "State procurement rules may exist on the physical procurement file even when omitted from tabular feeds.",
    "Specialized technical niches, remote or difficult geographical locations, or high-capital equipment "
    "markets can lawfully attract only one responsive bidder after full open advertisement.",
    "Repeat awards to the same supplier or contractor can lawfully reflect genuinely lowest-evaluated (L1) "
    "pricing across multi-bid competitive tenders, approved rate contracts, or phased maintenance renewals.",
    "Emergency, disaster-response, or retendered procurement following an earlier zero-bid tender may lawfully "
    "proceed with a single bid once the competent authority records rate reasonableness per CVC guidelines.",
)


@dataclass
class _ExtractedRow:
    raw: dict[str, Any]
    doc_role: str
    row_index: int
    tender_id: str
    ocid: str
    dedup_key: str
    title: str
    buyer_name: str
    normalized_buyer: str
    winner_name: str
    normalized_winner: str
    bids_received: int | None
    qualified_bids: int | None
    effective_bids: int | None
    procurement_method: str
    procurement_method_details: str
    exemption_reason: str
    award_value_inr: float | None
    tender_estimate_inr: float | None
    value_inr: float | None
    amount_type: str
    publish_date: date | None
    close_date: date | None
    award_date: date | None
    bid_window_days: int | None
    category: str
    fiscal_year: str
    jurisdiction: str
    record_url: str
    secondary_sha256: str


def _clean_text(value: Any, *, max_len: int = 300) -> str:
    if value is None:
        return ""
    s = _CTRL_CHARS_RE.sub(" ", str(value))
    s = _MULTI_SPACE_RE.sub(" ", s).strip()
    if max_len > 0 and len(s) > max_len:
        s = s[:max_len].rstrip()
    return s


def _neutralize_external_text(text: str) -> str:
    """Scrub PII and neutralize prohibited BNS Sec 356 terms in third-party public record text."""
    if not text:
        return ""
    cleaned, _ = scrub_pii(text)
    cleaned = _CTRL_CHARS_RE.sub(" ", cleaned)
    cleaned = _ACB_RE.sub("Vigilance", cleaned)
    neutral = _NEUTRAL_REPLACE_RE.sub("compliance-review", cleaned)
    return _MULTI_SPACE_RE.sub(" ", neutral).strip()


def normalize_entity_name(name: str) -> str:
    """Canonicalize supplier/buyer names so minor legal-suffix/formatting differences cluster cleanly."""
    raw = _clean_text(name, max_len=240).lower()
    if not raw or raw in {"na", "n/a", "none", "null", "--", "-", "unspecified", "unknown", "unspecified vendor"}:
        return ""
    s = raw.replace("&", " and ")
    s = _ENTITY_PREFIX_RE.sub("", s)
    s = _NON_ALNUM_SPACE_RE.sub(" ", s)
    s = _MULTI_SPACE_RE.sub(" ", s).strip()
    # Iteratively strip trailing legal suffixes (e.g. "private limited", "pvt ltd", "llp")
    for _ in range(3):
        updated = _ENTITY_SUFFIX_RE.sub("", s).strip()
        if updated == s or not updated:
            break
        s = updated
    return _MULTI_SPACE_RE.sub(" ", s).strip()


def _parse_int_strict(value: Any) -> int | None:
    """Parse non-negative integer preserving None (blank/unknown) vs 0 vs 1 vs >=2."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        if not math.isfinite(value) or value < 0:
            return None
        return int(round(value))
    text = str(value).strip()
    if not text or text.lower() in {"--", "-", "na", "n/a", "null", "none", "unknown", "not available"}:
        return None
    cleaned = text.replace(",", "")
    try:
        parsed = float(cleaned)
    except ValueError:
        return None
    if not math.isfinite(parsed) or parsed < 0:
        return None
    return int(round(parsed))


def _parse_float_opt(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        parsed = float(value)
        return parsed if math.isfinite(parsed) and parsed > 0 else None
    text = str(value).strip()
    if not text or text.lower() in {"--", "-", "na", "n/a", "null", "none", "unknown"}:
        return None
    cleaned = text.replace(",", "").replace("₹", "").replace("INR", "").replace("Rs.", "").replace("Rs", "").strip()
    try:
        parsed = float(cleaned)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def _parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value).strip()
    if not raw or raw.lower() in {"--", "-", "na", "n/a", "null", "none"}:
        return None
    iso_part = raw.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(iso_part).date()
    except ValueError:
        pass
    head = raw.split("T", 1)[0].strip()
    for fmt in (
        "%Y-%m-%d",
        "%d-%b-%Y %I:%M %p",
        "%d-%b-%Y %H:%M",
        "%d-%b-%Y",
        "%d/%b/%Y %I:%M %p",
        "%d/%b/%Y",
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y %H:%M",
        "%d-%m-%Y",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y",
        "%Y/%m/%d",
    ):
        try:
            return datetime.strptime(raw if " " in fmt else head, fmt).date()
        except ValueError:
            continue
    return None


def _normalize_fy(raw_fy: Any, fallback_date: date | None = None) -> str:
    text = str(raw_fy or "").strip()
    if text:
        m = _FY_RE.fullmatch(text.replace(" ", ""))
        if m:
            y1 = int(m.group(1))
            y2 = m.group(2) or f"{(y1 + 1) % 100:02d}"
            return f"FY{y1}-{y2}"
        m_full = re.fullmatch(r"(?:FY)?(20\d{2})[-/](20\d{2})", text.replace(" ", ""), re.IGNORECASE)
        if m_full:
            y1 = int(m_full.group(1))
            y2 = int(m_full.group(2)) % 100
            return f"FY{y1}-{y2:02d}"
    if fallback_date is not None:
        fy_start = fallback_date.year if fallback_date.month >= 4 else fallback_date.year - 1
        return f"FY{fy_start}-{(fy_start + 1) % 100:02d}"
    return "Unspecified"


def _fy_sort_key(fy: str) -> tuple[int, str]:
    m = re.search(r"(20\d{2})", fy or "")
    return (int(m.group(1)) if m else 9999, fy or "")


def _lookup_path(rec: dict[str, Any], key: str) -> Any:
    if not key:
        return None
    if key in rec:
        return rec[key]
    if "." in key:
        cur: Any = rec
        for part in key.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
                cur = cur[int(part)]
            else:
                return None
        return cur
    return None


def _get_field(rec: dict[str, Any], logical_key: str, field_map: dict[str, str]) -> Any:
    mapped = field_map.get(logical_key)
    if mapped:
        val = _lookup_path(rec, mapped)
        if val not in (None, ""):
            return val
    for candidate in _DEFAULT_FIELD_FALLBACKS.get(logical_key, ()):
        val = _lookup_path(rec, candidate)
        if val not in (None, ""):
            return val
    return None


def _extract_row(
    rec: dict[str, Any],
    idx: int,
    field_map: dict[str, str],
    default_jurisdiction: str,
    default_fy: str,
) -> _ExtractedRow:
    doc_role = str(rec.get("document") or "primary")
    row_index = _parse_int_strict(rec.get("source_row_index")) or idx
    ocid = _clean_text(_get_field(rec, "ocid", field_map), max_len=120)
    tender_id = _clean_text(_get_field(rec, "tender_id", field_map), max_len=120) or ocid
    title = _clean_text(_get_field(rec, "title", field_map), max_len=240)
    buyer_name = _clean_text(_get_field(rec, "buyer", field_map), max_len=200)
    winner_name = _clean_text(_get_field(rec, "winner", field_map), max_len=200)

    bids_received = _parse_int_strict(_get_field(rec, "bid_count", field_map))
    qualified_bids = _parse_int_strict(_get_field(rec, "qualified_bid_count", field_map))
    if qualified_bids is not None and qualified_bids > 0:
        effective_bids: int | None = qualified_bids
    else:
        effective_bids = bids_received

    proc_method = _clean_text(_get_field(rec, "procurement_method", field_map), max_len=80) or "open"
    proc_details = _clean_text(_get_field(rec, "procurement_method_details", field_map), max_len=160)
    exemption_reason = _clean_text(_get_field(rec, "exemption_reason", field_map), max_len=240)

    award_val = _parse_float_opt(_get_field(rec, "award_value_inr", field_map))
    est_val = _parse_float_opt(_get_field(rec, "tender_estimate_inr", field_map))
    if award_val is not None:
        value_inr: float | None = award_val
        amount_type = "award_value"
    elif est_val is not None:
        value_inr = est_val
        amount_type = "tender_estimate"
    else:
        value_inr = None
        amount_type = "unrecorded"

    pub_dt = _parse_date(_get_field(rec, "publish_date", field_map))
    close_dt = _parse_date(_get_field(rec, "close_date", field_map))
    award_dt = _parse_date(_get_field(rec, "award_date", field_map))

    raw_window = _parse_int_strict(_get_field(rec, "bid_window_days", field_map))
    if raw_window is not None:
        bid_window_days: int | None = raw_window
    elif pub_dt is not None and close_dt is not None and close_dt >= pub_dt:
        bid_window_days = (close_dt - pub_dt).days
    else:
        bid_window_days = None

    category = _clean_text(_get_field(rec, "category", field_map), max_len=120) or "Public Procurement"
    raw_fy = _get_field(rec, "fiscal_year", field_map)
    fy = _normalize_fy(raw_fy, fallback_date=award_dt or pub_dt or close_dt)
    if fy == "Unspecified" and default_fy:
        fy = _normalize_fy(default_fy)
    jurisdiction = _clean_text(_get_field(rec, "jurisdiction", field_map), max_len=120) or default_jurisdiction
    record_url = _clean_text(_get_field(rec, "record_url", field_map), max_len=500)
    sec_sha = _clean_text(rec.get("secondary_sha256"), max_len=64)

    dedup_key = (
        f"ocid:{ocid.lower()}"
        if ocid
        else (f"tid:{tender_id.lower()}" if tender_id else f"row:{doc_role}:{row_index}")
    )

    return _ExtractedRow(
        raw=rec,
        doc_role=doc_role,
        row_index=row_index,
        tender_id=tender_id,
        ocid=ocid,
        dedup_key=dedup_key,
        title=title,
        buyer_name=buyer_name,
        normalized_buyer=normalize_entity_name(buyer_name),
        winner_name=winner_name,
        normalized_winner=normalize_entity_name(winner_name),
        bids_received=bids_received,
        qualified_bids=qualified_bids,
        effective_bids=effective_bids,
        procurement_method=proc_method,
        procurement_method_details=proc_details,
        exemption_reason=exemption_reason,
        award_value_inr=award_val,
        tender_estimate_inr=est_val,
        value_inr=value_inr,
        amount_type=amount_type,
        publish_date=pub_dt,
        close_date=close_dt,
        award_date=award_dt,
        bid_window_days=bid_window_days,
        category=category,
        fiscal_year=fy,
        jurisdiction=jurisdiction,
        record_url=record_url,
        secondary_sha256=sec_sha,
    )


def _detect_exemption(row: _ExtractedRow) -> tuple[str, str]:
    """Classify documented statutory/operational single-bid exemptions."""
    combined_meta = " ".join(
        part for part in (row.procurement_method, row.procurement_method_details, row.exemption_reason) if part
    )
    if _PAC_OEM_RE.search(combined_meta) or re.search(
        r"\b(?:pac|proprietary\s+article(?:\s+certificate)?|sole\s+oem)\b", row.title, re.IGNORECASE
    ):
        basis = row.exemption_reason or row.procurement_method_details or row.procurement_method or "PAC/OEM exemption"
        return "documented_pac_oem", _neutralize_external_text(basis)[:160]

    if _DIRECT_METHOD_RE.search(row.procurement_method) or _DIRECT_METHOD_RE.search(row.procurement_method_details):
        justification_text = " ".join(p for p in (row.exemption_reason, row.procurement_method_details) if p).strip()
        if justification_text and (
            bool(row.exemption_reason.strip()) or _JUSTIFICATION_MARKER_RE.search(justification_text)
        ):
            return "documented_emergency_or_direct", _neutralize_external_text(justification_text)[:160]

    return "none_recorded", ""


def _locate_target_row(
    rows: list[_ExtractedRow],
    selector: dict[str, Any],
    field_map: dict[str, str],
) -> _ExtractedRow | None:
    match_field = str(selector.get("match_field") or "").strip()
    match_value = str(selector.get("match_value") or "").strip()
    target_tid = str(selector.get("tender_id") or "").strip()
    target_row_index = _parse_int_strict(selector.get("row_index"))

    def _matches_field(row: _ExtractedRow, field_name: str, expected: str) -> bool:
        if not field_name or not expected:
            return False
        if field_name == "source_row_index":
            return str(row.row_index) == expected
        raw_val = _lookup_path(row.raw, field_name)
        if raw_val not in (None, "") and str(raw_val).strip().lower() == expected.lower():
            return True
        if field_name in _DEFAULT_FIELD_FALLBACKS:
            val = _get_field(row.raw, field_name, field_map)
            if val not in (None, "") and str(val).strip().lower() == expected.lower():
                return True
        if field_name == "ocid" and row.ocid and row.ocid.lower() == expected.lower():
            return True
        if field_name == "tender_id" and row.tender_id and row.tender_id.lower() == expected.lower():
            return True
        return False

    primary_rows = [r for r in rows if r.doc_role != "secondary"]
    secondary_rows = [r for r in rows if r.doc_role == "secondary"]

    for pool in (primary_rows, secondary_rows):
        if match_field and match_value:
            for row in pool:
                if _matches_field(row, match_field, match_value):
                    return row
        if target_tid:
            tlow = target_tid.lower()
            for row in pool:
                if (row.tender_id and row.tender_id.lower() == tlow) or (row.ocid and row.ocid.lower() == tlow):
                    return row
        if target_row_index is not None and not match_value and not target_tid:
            for row in pool:
                if row.row_index == target_row_index:
                    return row
    return None


def _compute_consecutive_streak(stream_rows: list[_ExtractedRow], winner_key: str) -> int:
    if not winner_key or not stream_rows:
        return 0
    ordered = sorted(
        stream_rows,
        key=lambda r: (
            r.award_date or r.publish_date or r.close_date or date(9999, 12, 31),
            r.row_index,
            r.dedup_key,
        ),
    )
    best = 0
    cur = 0
    for row in ordered:
        if row.normalized_winner == winner_key:
            cur += 1
            if cur > best:
                best = cur
        elif row.normalized_winner:
            cur = 0
    return best


def _governing_instrument(jurisdiction: str, jurisdiction_level: str) -> str:
    if jurisdiction_level == "central":
        return "Central GFR 2017 Rules 166 & 173(xx), CVC Single-Bid Guidelines, and MoF DoE Procurement Manual"
    jur_clean = _neutralize_external_text(jurisdiction or "State")
    return (
        f"{jur_clean} State Public Procurement / Financial Rules & CVC Single-Bid Transparency Benchmarks "
        "(verify applicable State DFP/PAC delegation)"
    )


def _format_inr_brief(amount: float | None) -> str:
    if amount is None or not math.isfinite(amount) or amount <= 0:
        return "unrecorded value"
    if amount >= 10_000_000:
        return f"INR {amount / 10_000_000:,.2f} Cr"
    if amount >= 100_000:
        return f"INR {amount / 100_000:,.2f} L"
    return f"INR {amount:,.0f}"


def run_p7_only_one_bid(
    records: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
) -> tuple[list[AuditLeadCandidate], int, float, str, dict[str, Any]]:
    """Run deterministic P7 single-bidder and repeat-winner verification over pinned procurement records."""
    ctx = context or {}
    spec = ctx.get("only_one_bid")
    if not isinstance(spec, dict):
        raise ValueError("P7 requires payload.context.only_one_bid specification.")

    case_key = str(spec.get("case_key") or "").strip()
    audit_mode = str(spec.get("audit_mode") or "both").strip().lower()
    portal = _clean_text(spec.get("portal"), max_len=120) or "Public Procurement Portal"
    portal_key = _clean_text(spec.get("portal_key"), max_len=80)
    spec_jurisdiction = _clean_text(spec.get("jurisdiction"), max_len=120) or _clean_text(
        ctx.get("jurisdiction"), max_len=120
    ) or "Union of India"
    jurisdiction_level = str(spec.get("jurisdiction_level") or "").strip().lower()
    if jurisdiction_level not in VALID_JURISDICTION_LEVELS:
        jurisdiction_level = "state" if spec_jurisdiction.lower().startswith("state:") else "central"

    spec_authority = _clean_text(spec.get("authority"), max_len=200)
    authority_key = _clean_text(spec.get("authority_key"), max_len=80)
    spec_fy = _normalize_fy(spec.get("fiscal_year") or ctx.get("fiscal_year") or "")
    selector = spec.get("record_selector") if isinstance(spec.get("record_selector"), dict) else {}
    cohort_filter = spec.get("cohort_filter") if isinstance(spec.get("cohort_filter"), dict) else {}
    raw_fmap = spec.get("field_map") if isinstance(spec.get("field_map"), dict) else {}
    field_map: dict[str, str] = {
        str(k).strip(): str(v).strip()
        for k, v in raw_fmap.items()
        if isinstance(k, str) and isinstance(v, str) and k.strip() and v.strip()
    }

    extracted: list[_ExtractedRow] = [
        _extract_row(rec, idx, field_map, spec_jurisdiction, spec_fy)
        for idx, rec in enumerate(records, start=1)
        if isinstance(rec, dict)
    ]

    target = _locate_target_row(extracted, selector, field_map)
    if target is None:
        return (
            [],
            0,
            0.0,
            f"P7 unevaluable ({case_key}): target tender record not found in verified procurement dataset.",
            {
                "verdict": "unevaluable",
                "unevaluable_count": 1,
                "amount_type": "unrecorded",
                "case_key": case_key,
            },
        )

    # Deduplicate all records across primary + secondary shards (primary wins on key collision).
    deduped_by_key: dict[str, _ExtractedRow] = {}
    for row in extracted:
        existing = deduped_by_key.get(row.dedup_key)
        if existing is None or (existing.doc_role == "secondary" and row.doc_role != "secondary"):
            deduped_by_key[row.dedup_key] = row
    deduped_by_key[target.dedup_key] = target
    all_unique = list(deduped_by_key.values())

    resolved_authority_raw = target.buyer_name or str(cohort_filter.get("buyer_value") or "").strip() or spec_authority
    safe_authority = _neutralize_external_text(resolved_authority_raw or "Unspecified Procuring Authority")[:160]
    norm_authority = normalize_entity_name(resolved_authority_raw) or normalize_entity_name(spec_authority)

    target_winner_raw = target.winner_name
    safe_winner = _neutralize_external_text(target_winner_raw)
    target_winner_key = target.normalized_winner

    # Strictly unevaluable if neither competition count nor winning entity can be established.
    if (target.effective_bids is None or target.effective_bids == 0) and not target_winner_key:
        return (
            [],
            0,
            round(target.value_inr or 0.0, 2),
            f"P7 unevaluable ({case_key}): bidder count and winner name are both blank/zero in verified record.",
            {
                "verdict": "unevaluable",
                "unevaluable_count": 1,
                "amount_type": target.amount_type,
                "case_key": case_key,
                "tender_id": target.tender_id,
            },
        )

    # Build procuring-authority cohort from verified dataset records.
    if norm_authority:
        authority_rows = [
            r
            for r in all_unique
            if r.dedup_key == target.dedup_key
            or (r.normalized_buyer and r.normalized_buyer == norm_authority)
        ]
    else:
        authority_rows = [target]

    # Authority-level competition & award denominator metrics
    authority_cohort_tenders = len(authority_rows)
    evaluable_bid_rows = [r for r in authority_rows if r.effective_bids is not None and r.effective_bids >= 1]
    single_bid_authority_rows = [r for r in evaluable_bid_rows if r.effective_bids == 1]
    authority_single_bid_rate_pct = (
        round((len(single_bid_authority_rows) / len(evaluable_bid_rows)) * 100.0, 2)
        if evaluable_bid_rows
        else None
    )

    awarded_authority_rows = [r for r in authority_rows if r.normalized_winner]
    authority_awarded_tenders = len(awarded_authority_rows)
    valued_award_rows = [r for r in awarded_authority_rows if r.value_inr is not None and r.value_inr > 0]
    authority_valued_awards = len(valued_award_rows)
    authority_total_value_inr = sum(r.value_inr or 0.0 for r in valued_award_rows)

    # Winner-level cohort metrics within the same procuring authority
    if target_winner_key:
        winner_rows = [r for r in awarded_authority_rows if r.normalized_winner == target_winner_key]
        if target.dedup_key not in {r.dedup_key for r in winner_rows}:
            winner_rows.append(target)
    else:
        winner_rows = []

    winner_total_wins = len(winner_rows)
    winner_single_bid_wins = sum(1 for r in winner_rows if r.effective_bids == 1)
    winner_cohort_total_inr = round(sum(r.value_inr or 0.0 for r in winner_rows), 2)
    winner_share_by_count_pct = (
        round((winner_total_wins / authority_awarded_tenders) * 100.0, 2)
        if authority_awarded_tenders > 0 and winner_total_wins > 0
        else None
    )
    winner_share_by_value_pct = (
        round((winner_cohort_total_inr / authority_total_value_inr) * 100.0, 2)
        if authority_total_value_inr > 0 and winner_cohort_total_inr > 0
        else None
    )

    # Consecutive win streak (prefer same authority + category stream when category has >=3 awarded rows)
    target_cat_norm = _clean_text(target.category, max_len=120).lower()
    cat_stream = [
        r for r in awarded_authority_rows if _clean_text(r.category, max_len=120).lower() == target_cat_norm
    ]
    streak_stream = cat_stream if len(cat_stream) >= REPEAT_WINNER_STREAK_MIN else awarded_authority_rows
    consecutive_win_streak = _compute_consecutive_streak(streak_stream, target_winner_key)

    # Fiscal year span across winner cohort (or target FY)
    fy_candidates = sorted(
        {r.fiscal_year for r in (winner_rows or [target]) if r.fiscal_year and r.fiscal_year != "Unspecified"},
        key=_fy_sort_key,
    )
    target_fy = target.fiscal_year if target.fiscal_year != "Unspecified" else (spec_fy or "Unspecified")
    fy_start = fy_candidates[0] if fy_candidates else (target_fy if target_fy != "Unspecified" else None)
    fy_end = fy_candidates[-1] if fy_candidates else (target_fy if target_fy != "Unspecified" else None)

    # Classify single-bidder mechanism & statutory exemption
    is_single_bidder = target.effective_bids == 1
    if is_single_bidder:
        if target.bids_received is not None and target.bids_received >= 2 and target.qualified_bids == 1:
            single_bid_mechanism = "single_qualified_after_rejection"
        else:
            single_bid_mechanism = "single_bid_received"
    elif target.effective_bids is not None and target.effective_bids >= 2:
        single_bid_mechanism = "multi_bid"
    else:
        single_bid_mechanism = "unrecorded"

    exemption_status, exemption_basis = _detect_exemption(target)
    exempt_override = bool(
        exemption_status != "none_recorded"
        and target_winner_key
        and (
            (winner_total_wins >= EXEMPT_OVERRIDE_MIN_WINS and winner_cohort_total_inr >= EXEMPT_OVERRIDE_MIN_VALUE_INR)
            or (
                authority_awarded_tenders >= AUTHORITY_MIN_EVALUABLE_TENDERS
                and (winner_share_by_count_pct or 0.0) >= EXEMPT_OVERRIDE_SHARE_PCT
            )
            or (
                authority_valued_awards >= AUTHORITY_MIN_EVALUABLE_TENDERS
                and (winner_share_by_value_pct or 0.0) >= EXEMPT_OVERRIDE_SHARE_PCT
            )
        )
    )

    repeat_winner_triggered = bool(
        target_winner_key
        and winner_total_wins >= REPEAT_WINNER_MIN_WINS
        and (
            winner_total_wins >= REPEAT_WINNER_PERSISTENT_WINS
            or consecutive_win_streak >= REPEAT_WINNER_STREAK_MIN
            or (
                authority_awarded_tenders >= AUTHORITY_MIN_EVALUABLE_TENDERS
                and (winner_share_by_count_pct or 0.0) >= REPEAT_WINNER_SHARE_COUNT_PCT
            )
            or (
                authority_valued_awards >= AUTHORITY_MIN_EVALUABLE_TENDERS
                and (winner_share_by_value_pct or 0.0) >= REPEAT_WINNER_SHARE_VALUE_PCT
            )
        )
    )

    target_value = target.value_inr or 0.0
    audited_inr = round(target_value, 2)

    # 1. Documented statutory/PAC/OEM exemption cleared (unless overridden by sustained dominance)
    if is_single_bidder and exemption_status != "none_recorded" and not exempt_override:
        return (
            [],
            1,
            audited_inr,
            (
                f"P7 cleared ({case_key}, cleared_exempt): {target.tender_id or case_key} records "
                f"{exemption_status} ({exemption_basis or target.procurement_method}); "
                f"winner wins={winner_total_wins}."
            ),
            {
                "verdict": "cleared_exempt",
                "exemption_status": exemption_status,
                "exemption_basis": exemption_basis,
                "bid_count": target.bids_received,
                "effective_bids": target.effective_bids,
                "winner_name": safe_winner,
                "winner_total_wins": winner_total_wins,
                "unevaluable_count": 0,
                "amount_type": target.amount_type,
            },
        )

    # Evaluate flagging rules in priority order
    signal_category = ""
    rule_triggered = ""
    verdict = ""
    severity = "medium"
    triggered_rules: list[str] = []

    if (
        is_single_bidder
        and (exemption_status == "none_recorded" or exempt_override)
        and bool(target_winner_key)
        and (winner_single_bid_wins >= REPEAT_WINNER_SB_MIN_WINS or winner_total_wins >= REPEAT_WINNER_MIN_WINS)
    ):
        signal_category = "single_bidder_repeat_winner"
        rule_triggered = "P7-COMBINED-SB-RW"
        verdict = "flagged_combined"
        triggered_rules.append("P7-COMBINED-SB-RW")
        if single_bid_mechanism == "single_qualified_after_rejection":
            triggered_rules.append("P7-SINGLE-QUALIFIED-AFTER-TECH-REJECTION")
        else:
            triggered_rules.append("P7-SINGLE-BID-RECEIVED")
        if target.bid_window_days is not None and target.bid_window_days < SHORT_WINDOW_DAYS:
            triggered_rules.append("P7-SHORT-BID-WINDOW")
        if (
            len(evaluable_bid_rows) >= AUTHORITY_MIN_EVALUABLE_TENDERS
            and (authority_single_bid_rate_pct or 0.0) > AUTHORITY_HIGH_SB_RATE_PCT
        ):
            triggered_rules.append("P7-HIGH-AUTHORITY-SB-RATE")
        if exempt_override:
            triggered_rules.append("P7-REPEATED-DIRECT-OR-EXEMPT-DOMINANCE")

        if (
            winner_single_bid_wins >= 5
            or (winner_total_wins >= 5 and winner_cohort_total_inr >= 50_000_000.0)
            or target_value >= 100_000_000.0
            or winner_cohort_total_inr >= 100_000_000.0
        ):
            severity = "critical"
        elif winner_single_bid_wins >= 3 or winner_cohort_total_inr >= 10_000_000.0 or target_value >= 10_000_000.0:
            severity = "high"
        else:
            severity = "medium"

    elif is_single_bidder and exemption_status == "none_recorded":
        # Check de minimis clearance (strictly when a positive known value is < INR 5 lakh)
        is_de_minimis = (
            target.value_inr is not None
            and target.value_inr < DE_MINIMIS_VALUE_INR
            and winner_total_wins < REPEAT_WINNER_MIN_WINS
            and (target.bid_window_days is None or target.bid_window_days >= CRITICAL_SHORT_WINDOW_DAYS)
        )
        if is_de_minimis:
            return (
                [],
                1,
                audited_inr,
                (
                    f"P7 cleared ({case_key}, cleared_de_minimis): single-bidder tender {target.tender_id or case_key} "
                    f"valued at {_format_inr_brief(target.value_inr)} is below INR 5 lakh LPC ceiling with no repeat-winner streak."
                ),
                {
                    "verdict": "cleared_de_minimis",
                    "exemption_status": exemption_status,
                    "bid_count": target.bids_received,
                    "effective_bids": target.effective_bids,
                    "winner_name": safe_winner,
                    "winner_total_wins": winner_total_wins,
                    "unevaluable_count": 0,
                    "amount_type": target.amount_type,
                },
            )

        signal_category = "unexplained_single_bidder"
        rule_triggered = "P7-SINGLE-BID"
        verdict = "flagged_single_bidder"
        triggered_rules.append("P7-SINGLE-BID")
        if single_bid_mechanism == "single_qualified_after_rejection":
            triggered_rules.append("P7-SINGLE-QUALIFIED-AFTER-TECH-REJECTION")
        else:
            triggered_rules.append("P7-SINGLE-BID-RECEIVED")
        if target.bid_window_days is not None and target.bid_window_days < SHORT_WINDOW_DAYS:
            triggered_rules.append("P7-SHORT-BID-WINDOW")
        if (
            len(evaluable_bid_rows) >= AUTHORITY_MIN_EVALUABLE_TENDERS
            and (authority_single_bid_rate_pct or 0.0) > AUTHORITY_HIGH_SB_RATE_PCT
        ):
            triggered_rules.append("P7-HIGH-AUTHORITY-SB-RATE")

        if target_value >= 100_000_000.0 or (
            target_value >= 10_000_000.0
            and target.bid_window_days is not None
            and target.bid_window_days < CRITICAL_SHORT_WINDOW_DAYS
        ):
            severity = "critical"
        elif (
            target_value >= 10_000_000.0
            or (target.bid_window_days is not None and target.bid_window_days < SHORT_WINDOW_DAYS)
            or (
                single_bid_mechanism == "single_qualified_after_rejection"
                and (target.bids_received or 0) >= 3
            )
        ):
            severity = "high"
        elif (
            target_value >= 2_500_000.0
            or (
                len(evaluable_bid_rows) >= AUTHORITY_MIN_EVALUABLE_TENDERS
                and (authority_single_bid_rate_pct or 0.0) > AUTHORITY_HIGH_SB_RATE_PCT
            )
        ):
            severity = "medium"
        else:
            severity = "low"

    elif repeat_winner_triggered:
        signal_category = "repeat_winner_concentration"
        rule_triggered = "P7-REPEAT-WINNER"
        verdict = "flagged_repeat_winner"
        triggered_rules.append("P7-REPEAT-WINNER")
        if winner_total_wins >= REPEAT_WINNER_PERSISTENT_WINS:
            triggered_rules.append("P7-PERSISTENT-MULTI-AWARD")
        if consecutive_win_streak >= REPEAT_WINNER_STREAK_MIN:
            triggered_rules.append("P7-CONSECUTIVE-WIN-STREAK")
        if (
            authority_awarded_tenders >= AUTHORITY_MIN_EVALUABLE_TENDERS
            and (winner_share_by_count_pct or 0.0) >= REPEAT_WINNER_SHARE_COUNT_PCT
        ):
            triggered_rules.append("P7-HIGH-AWARD-COUNT-SHARE")
        if (
            authority_valued_awards >= AUTHORITY_MIN_EVALUABLE_TENDERS
            and (winner_share_by_value_pct or 0.0) >= REPEAT_WINNER_SHARE_VALUE_PCT
        ):
            triggered_rules.append("P7-HIGH-AWARD-VALUE-SHARE")
        if exempt_override:
            triggered_rules.append("P7-REPEATED-DIRECT-OR-EXEMPT-DOMINANCE")

        if (
            winner_total_wins >= 10
            or ((winner_share_by_value_pct or 0.0) >= 50.0 and winner_cohort_total_inr >= 50_000_000.0)
            or winner_cohort_total_inr >= 250_000_000.0
        ):
            severity = "critical"
        elif (
            winner_total_wins >= 5
            or (winner_share_by_value_pct or 0.0) >= 40.0
            or winner_cohort_total_inr >= 50_000_000.0
        ):
            severity = "high"
        else:
            severity = "medium"

    elif exemption_status == "none_recorded" and (
        (
            target_value >= PROCESS_GAP_MIN_VALUE_INR
            and target.bid_window_days is not None
            and target.bid_window_days < CRITICAL_SHORT_WINDOW_DAYS
            and (target.effective_bids is None or target.effective_bids <= 2)
        )
        or (
            target_value >= PROCESS_GAP_14D_MIN_VALUE_INR
            and target.bid_window_days is not None
            and target.bid_window_days < SHORT_WINDOW_DAYS
            and (target.effective_bids is None or target.effective_bids <= 2)
        )
        or (target.effective_bids is None and bool(target_winner_key) and winner_total_wins >= REPEAT_WINNER_MIN_WINS)
    ):
        signal_category = "short_window_or_process_gap"
        rule_triggered = "P7-PROCESS-GAP"
        verdict = "flagged_process_gap"
        triggered_rules.append("P7-PROCESS-GAP")
        if target.bid_window_days is not None and target.bid_window_days < SHORT_WINDOW_DAYS:
            triggered_rules.append("P7-SHORT-BID-WINDOW")
        if target.effective_bids is None and winner_total_wins >= REPEAT_WINNER_MIN_WINS:
            triggered_rules.append("P7-UNRECORDED-BID-COUNT-REPEAT-WINNER")

        severity = (
            "high"
            if (
                target_value >= 50_000_000.0
                and target.bid_window_days is not None
                and target.bid_window_days < CRITICAL_SHORT_WINDOW_DAYS
            )
            else "medium"
        )

    else:
        return (
            [],
            1,
            audited_inr,
            (
                f"P7 cleared ({case_key}, cleared_competitive): {target.tender_id or case_key} drew "
                f"{target.effective_bids} effective bid(s) and winner wins={winner_total_wins} stayed within norms."
            ),
            {
                "verdict": "cleared_competitive",
                "exemption_status": exemption_status,
                "bid_count": target.bids_received,
                "effective_bids": target.effective_bids,
                "winner_name": safe_winner,
                "winner_total_wins": winner_total_wins,
                "unevaluable_count": 0,
                "amount_type": target.amount_type,
            },
        )

    # Assemble neutral finding, metrics, and 2-3 verifiable citations.
    safe_title = _neutralize_external_text(target.title or f"Tender {target.tender_id or case_key}")[:180]
    safe_category = _neutralize_external_text(target.category or "Public Procurement")[:100]
    safe_portal = _neutralize_external_text(portal)[:100]
    resolved_jurisdiction = target.jurisdiction or spec_jurisdiction
    governing = _governing_instrument(resolved_jurisdiction, jurisdiction_level)

    related_ids: list[str] = []
    for r in sorted(
        winner_rows,
        key=lambda x: (
            x.award_date or x.publish_date or date(9999, 12, 31),
            x.tender_id or x.ocid or x.dedup_key,
        ),
    ):
        rid = r.tender_id or r.ocid
        if rid and rid not in related_ids:
            related_ids.append(rid)
        if len(related_ids) >= 10:
            break

    locator_parts = [f"row={target.row_index}"]
    if target.tender_id:
        locator_parts.append(f"tender_id={target.tender_id}")
    if target.ocid and target.ocid != target.tender_id:
        locator_parts.append(f"ocid={target.ocid}")
    source_locator = "; ".join(locator_parts)

    retrieval_url = str(ctx.get("retrieval_url") or ctx.get("dataset_url") or "").strip()
    expected_sha = str(ctx.get("expected_sha256") or "").strip().lower()
    sec_url = str(ctx.get("secondary_url") or ctx.get("secondary_dataset_url") or "").strip()
    sec_sha = str(ctx.get("secondary_expected_sha256") or ctx.get("secondary_sha256") or "").strip().lower()
    landing_url = str(
        selector.get("record_url")
        or target.record_url
        or ctx.get("source_landing_page")
        or ctx.get("source_url")
        or retrieval_url
    ).strip()
    official_publisher = str(ctx.get("official_publisher") or safe_authority).strip()

    bid_phrase = (
        f"1 technically qualified bid out of {target.bids_received} received"
        if single_bid_mechanism == "single_qualified_after_rejection"
        else (
            f"{target.effective_bids} bid(s) recorded"
            if target.effective_bids is not None
            else "bidder count unrecorded"
        )
    )
    winner_phrase = safe_winner or "unrecorded supplier"

    if signal_category == "single_bidder_repeat_winner":
        lead_title = (
            f"Procurement Pattern Lead: Single-Bidder & Repeat-Winner Tender "
            f"({winner_total_wins} wins) — {safe_authority}"
        )
    elif signal_category == "unexplained_single_bidder":
        lead_title = f"Procurement Pattern Lead: Single-Bidder Competitive Tender — {safe_authority}"
    elif signal_category == "repeat_winner_concentration":
        lead_title = (
            f"Procurement Pattern Lead: Repeat-Winner Concentration ({winner_total_wins} awards) — {safe_authority}"
        )
    else:
        lead_title = f"Procurement Pattern Lead: Short Bid Window / Process Gap — {safe_authority}"

    share_bits: list[str] = []
    if winner_share_by_count_pct is not None:
        share_bits.append(f"{winner_share_by_count_pct:.1f}% of {authority_awarded_tenders} authority awards")
    if winner_share_by_value_pct is not None:
        share_bits.append(f"{winner_share_by_value_pct:.1f}% by value")
    share_clause = f" ({', '.join(share_bits)})" if share_bits else ""
    window_clause = (
        f" Bid window: {target.bid_window_days} day(s)." if target.bid_window_days is not None else ""
    )
    cohort_clause = (
        f" Same normalized winner ({winner_phrase}) holds {winner_total_wins} award(s) "
        f"({winner_single_bid_wins} single-bid) totaling {_format_inr_brief(winner_cohort_total_inr)}{share_clause} "
        f"with a max consecutive streak of {consecutive_win_streak}."
        if winner_total_wins >= 2
        else ""
    )

    summary = (
        f"Public procurement observation on {safe_portal} ({resolved_jurisdiction}, {target_fy}) for "
        f"{safe_authority}: tender '{safe_title}' (ref: {target.tender_id or case_key}, method: "
        f"{_neutralize_external_text(target.procurement_method)}, exemption: {exemption_status}) recorded "
        f"{bid_phrase} and target {target.amount_type} of {_format_inr_brief(target.value_inr)} awarded to "
        f"{winner_phrase}.{window_clause}{cohort_clause} Governing benchmark: {governing}."
    )

    citations: list[dict[str, Any]] = [
        {
            "role": "primary_tender",
            "source_url": retrieval_url or landing_url,
            "landing_url": landing_url,
            "official_publisher": official_publisher,
            "source_locator": source_locator,
            "tender_id": target.tender_id,
            "ocid": target.ocid,
            "sha256": expected_sha,
            "excerpt": (
                f"{target.tender_id or case_key}: '{safe_title}' | authority={safe_authority} | "
                f"method={_neutralize_external_text(target.procurement_method)} | {bid_phrase} | "
                f"{target.amount_type}={_format_inr_brief(target.value_inr)} | "
                f"award_date={target.award_date.isoformat() if target.award_date else 'unrecorded'} | "
                f"winner={winner_phrase}"
            ),
        }
    ]

    if winner_total_wins >= 2:
        uses_secondary = any(r.doc_role == "secondary" for r in winner_rows) and bool(sec_url)
        cohort_sha = (
            sec_sha
            or next((r.secondary_sha256 for r in winner_rows if r.secondary_sha256), "")
            or expected_sha
        )
        citations.append(
            {
                "role": "repeat_winner_cohort",
                "source_url": sec_url if uses_secondary else (retrieval_url or landing_url),
                "landing_url": landing_url,
                "official_publisher": official_publisher,
                "source_locator": (
                    f"authority={safe_authority}; winner={target_winner_key}; "
                    f"wins={winner_total_wins}; single_bid_wins={winner_single_bid_wins}"
                ),
                "sha256": cohort_sha,
                "excerpt": (
                    f"Repeat-winner cohort for {winner_phrase} at {safe_authority}: {winner_total_wins} award(s) "
                    f"({winner_single_bid_wins} single-bidder), cohort total {_format_inr_brief(winner_cohort_total_inr)}, "
                    f"refs: {', '.join(related_ids[:6])}"
                ),
            }
        )

    citations.append(
        {
            "role": "statutory_benchmark",
            "source_url": landing_url or retrieval_url,
            "landing_url": landing_url or retrieval_url,
            "official_publisher": official_publisher,
            "source_locator": f"rule={rule_triggered}; jurisdiction={resolved_jurisdiction}",
            "sha256": expected_sha,
            "excerpt": (
                f"{rule_triggered} ({signal_category}): evaluated against {governing}; "
                f"authority_single_bid_rate="
                + (f"{authority_single_bid_rate_pct:.1f}%" if authority_single_bid_rate_pct is not None else "unrecorded")
                + f" across {len(evaluable_bid_rows)} evaluable tender(s)."
            ),
        }
    )

    lead = AuditLeadCandidate(
        title=lead_title[:200],
        summary=summary,
        methodology_code=ONLY_ONE_BID_METHODOLOGY,
        entity_name=safe_authority,
        jurisdiction=resolved_jurisdiction,
        fiscal_year=target_fy,
        fiscal_year_start=fy_start,
        fiscal_year_end=fy_end,
        monetary_exposure_inr=round(target_value, 2),  # Target tender ONLY — never double-count cohort!
        severity=severity,  # type: ignore[arg-type]
        anomaly_metrics={
            "case_key": case_key,
            "audit_mode": audit_mode,
            "verdict": verdict,
            "signal_category": signal_category,
            "triggered_rules": triggered_rules,
            "portal": safe_portal,
            "portal_key": portal_key,
            "authority": safe_authority,
            "authority_key": authority_key,
            "jurisdiction": resolved_jurisdiction,
            "jurisdiction_level": jurisdiction_level,
            "tender_id": target.tender_id,
            "ocid": target.ocid,
            "tender_title": safe_title,
            "item_category": safe_category,
            "procurement_method": _neutralize_external_text(target.procurement_method),
            "exemption_status": exemption_status,
            "bids_received": target.bids_received,
            "technically_qualified_bids": target.qualified_bids,
            "effective_bids": target.effective_bids,
            "single_bid_mechanism": single_bid_mechanism,
            "bid_window_days": target.bid_window_days,
            "publish_date": target.publish_date.isoformat() if target.publish_date else None,
            "close_date": target.close_date.isoformat() if target.close_date else None,
            "award_date": target.award_date.isoformat() if target.award_date else None,
            "fiscal_year": target_fy,
            "fiscal_year_start": fy_start,
            "fiscal_year_end": fy_end,
            "amount_type": target.amount_type,
            "award_value_inr": round(target.award_value_inr, 2) if target.award_value_inr is not None else None,
            "tender_estimate_inr": round(target.tender_estimate_inr, 2) if target.tender_estimate_inr is not None else None,
            "winner_name": safe_winner,
            "normalized_winner_key": target_winner_key,
            "winner_total_wins": winner_total_wins,
            "winner_single_bid_wins": winner_single_bid_wins,
            "winner_cohort_total_inr": winner_cohort_total_inr,
            "winner_share_by_count_pct": winner_share_by_count_pct,
            "winner_share_by_value_pct": winner_share_by_value_pct,
            "consecutive_win_streak": consecutive_win_streak,
            "authority_cohort_tenders": authority_cohort_tenders,
            "authority_single_bid_rate_pct": authority_single_bid_rate_pct,
            "related_tender_ids": related_ids,
            "governing_instrument": governing,
            "source_locator": source_locator,
            "is_anchor_case": bool(spec.get("is_anchor_case")),
        },
        rule_triggered=rule_triggered,
        source_locator=source_locator,
        citations=citations,
        benign_explanations=list(_BENIGN_EXPLANATIONS),
        suggested_authority=safe_authority,
        amount_type=target.amount_type,
    )

    run_summary = (
        f"P7 flagged {target.tender_id or case_key} ({safe_authority}): "
        f"{signal_category} (bids={target.effective_bids}, winner_wins={winner_total_wins}, "
        f"target={_format_inr_brief(target.value_inr)})."
    )
    meta = {
        "verdict": verdict,
        "signal_category": signal_category,
        "unevaluable_count": 0,
        "amount_type": target.amount_type,
        "case_key": case_key,
    }
    return [lead], 0, audited_inr, run_summary, meta


def validate_context(ctx: Any, where: str) -> list[str]:
    """Validate ``payload.context.only_one_bid`` for a P7 task."""
    errors: list[str] = []
    if not isinstance(ctx, dict):
        return [f"{where}.payload.context.only_one_bid is required for P7 tasks."]
    spec = ctx.get("only_one_bid")
    if not isinstance(spec, dict):
        return [f"{where}.payload.context.only_one_bid must be an object."]

    as_of = ctx.get("as_of_date")
    if as_of not in (None, "") and (not isinstance(as_of, str) or not _ISO_DATE_RE.fullmatch(as_of.strip())):
        errors.append(f"{where}.payload.context.as_of_date must be YYYY-MM-DD when provided.")

    ckey = spec.get("case_key")
    if not isinstance(ckey, str) or not _KEY_RE.fullmatch(ckey):
        errors.append(f"{where}.payload.context.only_one_bid.case_key must match [a-z0-9-] (<= 59 chars).")

    audit_mode = spec.get("audit_mode")
    if audit_mode not in VALID_AUDIT_MODES:
        errors.append(
            f"{where}.payload.context.only_one_bid.audit_mode must be one of {sorted(VALID_AUDIT_MODES)}."
        )

    for field_name, max_len in (("portal", 120), ("jurisdiction", 120), ("authority", 200)):
        val = spec.get(field_name)
        if not isinstance(val, str) or not val.strip() or len(val) > max_len:
            errors.append(
                f"{where}.payload.context.only_one_bid.{field_name} must be non-empty text (<= {max_len} chars)."
            )
        elif scan_forbidden_terms(val):
            errors.append(
                f"{where}.payload.context.only_one_bid.{field_name} contains prohibited non-neutral terminology."
            )

    portal_key = spec.get("portal_key")
    if not isinstance(portal_key, str) or not _AUTH_KEY_RE.fullmatch(portal_key):
        errors.append(
            f"{where}.payload.context.only_one_bid.portal_key must match [a-z0-9-] (<= 80 chars)."
        )

    auth_key = spec.get("authority_key")
    if not isinstance(auth_key, str) or not _AUTH_KEY_RE.fullmatch(auth_key):
        errors.append(
            f"{where}.payload.context.only_one_bid.authority_key must match [a-z0-9-] (<= 80 chars)."
        )

    jlevel = spec.get("jurisdiction_level")
    if jlevel not in VALID_JURISDICTION_LEVELS:
        errors.append(
            f"{where}.payload.context.only_one_bid.jurisdiction_level must be 'central' or 'state'."
        )

    year = spec.get("year")
    if isinstance(year, bool) or not isinstance(year, int) or not (2015 <= year <= 2027):
        errors.append(f"{where}.payload.context.only_one_bid.year must be an integer between 2015 and 2027.")

    fy = spec.get("fiscal_year")
    if not isinstance(fy, str) or not fy.strip() or len(fy) > 20:
        errors.append(f"{where}.payload.context.only_one_bid.fiscal_year is required.")

    selector = spec.get("record_selector")
    if not isinstance(selector, dict):
        errors.append(f"{where}.payload.context.only_one_bid.record_selector must be an object.")
    else:
        mfield = selector.get("match_field")
        mval = selector.get("match_value")
        if not isinstance(mfield, str) or not mfield.strip() or len(mfield) > 80:
            errors.append(f"{where}.payload.context.only_one_bid.record_selector.match_field is required.")
        if not isinstance(mval, str) or not mval.strip() or len(mval) > 160:
            errors.append(f"{where}.payload.context.only_one_bid.record_selector.match_value is required.")
        rec_url = selector.get("record_url")
        if rec_url not in (None, ""):
            try:
                validate_safe_http_url(rec_url, require_https=True, allow_private=False)
            except ValueError as exc:
                errors.append(f"{where}.payload.context.only_one_bid.record_selector.record_url invalid: {exc}")

    cohort = spec.get("cohort_filter")
    if cohort is not None:
        if not isinstance(cohort, dict):
            errors.append(f"{where}.payload.context.only_one_bid.cohort_filter must be an object.")
        else:
            rel_ids = cohort.get("related_record_ids")
            if rel_ids is not None:
                if (
                    not isinstance(rel_ids, list)
                    or len(rel_ids) > MAX_RELATED_RECORD_IDS
                    or not all(isinstance(x, str) and x.strip() and len(x) <= 160 for x in rel_ids)
                ):
                    errors.append(
                        f"{where}.payload.context.only_one_bid.cohort_filter.related_record_ids must be a list "
                        f"of at most {MAX_RELATED_RECORD_IDS} non-empty IDs."
                    )

    fmap = spec.get("field_map")
    if fmap is not None:
        if (
            not isinstance(fmap, dict)
            or len(fmap) > MAX_FIELD_MAP_KEYS
            or not all(
                isinstance(k, str) and k.strip() and isinstance(v, str) and v.strip() and len(v) <= 120
                for k, v in fmap.items()
            )
        ):
            errors.append(
                f"{where}.payload.context.only_one_bid.field_map must map <= {MAX_FIELD_MAP_KEYS} non-empty string keys to source column names."
            )

    return errors
