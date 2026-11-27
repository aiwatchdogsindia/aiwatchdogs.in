"""P8: Sovereign Lies — deterministic audit engine for Indian government scheme promises vs walk-backs.

Hook: "The government made a promise when it launched the scheme. Did it keep it?"

Reads checksummed official Union and State/UT government documents (PDF, HTML, JSON, CSV, or TXT —
single-document or paired launch-promise + walk-back/amendment artifacts), verifies SHA-256 digests
via the loader, verifies verbatim documentary evidence tokens inside the downloaded official bytes
(failing closed as ``unevaluable`` if bytes are unreadable, selectors miss, or required evidence
tokens are absent), and deterministically evaluates:

1. ``P8-RETROACTIVE-TAX-REVERSAL`` (``retroactive_tax_reversal``) — tax exemption or fiscal treatment
   promised at launch subsequently curtailed or withdrawn (anchored by the Sovereign Gold Bond Scheme
   Finance Act 2026 primary-subscriber-only capital gains tax restriction).
2. ``P8-MIDSTREAM-BENEFIT-CUT`` (``midstream_benefit_cut``) — interest rate, subsidy percentage,
   cash transfer amount, or payout schedule reduced during an active scheme tenure.
3. ``P8-PREMATURE-DISCONTINUATION`` (``premature_discontinuation``) — notified scheme, fellowship,
   tranche series, or concession suspended or wound up ahead of schedule.
4. ``P8-ELIGIBILITY-NARROWING`` (``eligibility_narrowing``) — new exclusions, taxpayer bars,
   income caps, or beneficiary pruning added after launch.
5. ``P8-LOCKIN-OR-EXIT-RESTRICTION`` (``lockin_or_exit_restriction``) — lock-in period lengthened,
   premature exit windows tightened, or withdrawal conditions restricted after launch.
6. ``P8-ARREARS-OR-UNFUNDED-PROMISE`` (``arrears_or_unfunded_promise``) — notified benefit,
   pension, waiver, or subsidy left unpaid, unimplemented, or in documented arrears.
7. ``Cleared`` (``grandfathered_or_honored``) — verified launch terms honored or existing holders
   explicitly grandfathered intact, published with equal journalistic standing.

Strict BNS Sec 356 neutrality and DPDP Act 2023 PII scrubbing are enforced on every emitted field
and quoted official excerpt.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from aiwatchdogs.core.models import AuditLeadCandidate
from aiwatchdogs.core.safety import scan_forbidden_terms, scrub_pii, validate_safe_http_url

SOVEREIGN_LIES_PROJECT_SLUG = "sovereign-lies"
SOVEREIGN_LIES_METHODOLOGY = "P8"
P8_DEFAULT_HOOK = "The government made a promise when it launched the scheme. Did it keep it?"

# Canonical P8 task ID format: p8-<jurisdiction_level>-<slug>-<4-digit-seq>
P8_TASK_ID_RE = re.compile(r"^p8-(union|state|ut)-[a-z0-9][a-z0-9-]{1,40}-\d{4}$")

VALID_JURISDICTION_LEVELS: frozenset[str] = frozenset({"union", "state", "ut"})

SCHEME_CATEGORY_LABELS: dict[str, str] = {
    "sovereign_bonds_small_savings": "Sovereign Bonds & Small Savings",
    "pensions_retirement": "Pensions & Retirement",
    "tax_exempt_instruments": "Tax-Exempt Instruments",
    "cash_transfers_guarantees": "Cash Transfers & Guarantees",
    "ev_housing_farm_subsidies": "EV, Housing & Farm Subsidies",
    "scholarships_education": "Scholarships & Education",
    "industrial_export_incentives": "Industrial & Export Incentives",
    "welfare_health_public_services": "Welfare, Health & Public Services",
}
VALID_SCHEME_CATEGORIES: frozenset[str] = frozenset(SCHEME_CATEGORY_LABELS.keys())

WALKBACK_MECHANISM_RULES: dict[str, str] = {
    "retroactive_tax_reversal": "P8-RETROACTIVE-TAX-REVERSAL",
    "midstream_benefit_cut": "P8-MIDSTREAM-BENEFIT-CUT",
    "premature_discontinuation": "P8-PREMATURE-DISCONTINUATION",
    "eligibility_narrowing": "P8-ELIGIBILITY-NARROWING",
    "lockin_or_exit_restriction": "P8-LOCKIN-OR-EXIT-RESTRICTION",
    "arrears_or_unfunded_promise": "P8-ARREARS-OR-UNFUNDED-PROMISE",
}

SIGNAL_CATEGORIES: tuple[str, ...] = (
    "retroactive_tax_reversal",
    "midstream_benefit_cut",
    "premature_discontinuation",
    "eligibility_narrowing",
    "lockin_or_exit_restriction",
    "arrears_or_unfunded_promise",
)

VALID_WALKBACK_MECHANISMS: frozenset[str] = frozenset(SIGNAL_CATEGORIES) | {"grandfathered_or_honored"}

VALID_RETROACTIVE_IMPACTS: frozenset[str] = frozenset(
    {
        "retroactive_existing_holders",
        "mid_stream_active_scheme",
        "prospective_discontinuation",
        "eligibility_narrowing",
        "unfulfilled_arrears",
        "grandfathered_or_honored",
    }
)

VALID_SOURCE_FORMATS: frozenset[str] = frozenset({"pdf", "html", "json", "csv", "txt"})

VALID_SECONDARY_ROLES: frozenset[str] = frozenset(
    {
        "",
        "walkback_document",
        "promise_document",
        "corroborating_record",
    }
)

MIN_READABLE_DOC_CHARS = 40
MIN_EVIDENCE_TOKENS = 2
MAX_EVIDENCE_TOKENS = 10
HIGH_EXPOSURE_INR = 1_000_000_000.0  # INR 100 Cr
MAJOR_EXPOSURE_INR = 10_000_000_000.0  # INR 1,000 Cr
CRITICAL_EXPOSURE_INR = 50_000_000_000.0  # INR 5,000 Cr
RAPID_WALKBACK_DAYS = 365

_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,58}$")
_SLUG_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CTRL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]+")
_MULTI_SPACE_RE = re.compile(r"\s+")
_NON_ALNUM_SPACE_RE = re.compile(r"[^a-z0-9\s]+")
_DASH_VARIANTS_RE = re.compile(r"[\u2010-\u2015\u2212]")
_QUOTE_SINGLE_RE = re.compile(r"[\u2018\u2019\u201a\u201b]")
_QUOTE_DOUBLE_RE = re.compile(r"[\u201c\u201d\u201e\u201f]")

# BNS Sec 356 neutralizer for quoted third-party statutory/official text.
_ACB_RE = re.compile(r"\banti[\s-]+corruption\b", re.IGNORECASE)
_NEUTRAL_REPLACE_RE = re.compile(
    r"(?<![a-z])(?:scam(?:s|med|mer|mers)?|corrupt(?:ed|ing|ly|ion)?|brib(?:e|es|ed|ing|ery)|"
    r"fraud(?:s|ulent|ulently|ster|sters)?|kickback(?:s)?|guilty|stolen|rigged|"
    r"crook(?:s|ed)?|loot(?:s|ed|ing|er|ers)?|embezzl(?:e|es|ed|ing|ement))(?![a-z])",
    re.IGNORECASE,
)

_MECHANISM_DISPLAY: dict[str, str] = {
    "retroactive_tax_reversal": "Retrospective Tax Exemption Modification",
    "midstream_benefit_cut": "Mid-Scheme Benefit / Subsidy Revision",
    "premature_discontinuation": "Scheme Discontinuation / Suspension",
    "eligibility_narrowing": "Post-Launch Eligibility Restriction",
    "lockin_or_exit_restriction": "Lock-In or Exit Terms Modification",
    "arrears_or_unfunded_promise": "Unfulfilled Outlay / Documented Arrears",
    "grandfathered_or_honored": "Honored / Grandfathered Commitment",
}

_BENIGN_EXPLANATIONS: tuple[str, ...] = (
    "Parliament and State Legislatures possess sovereign statutory authority under the Constitution of India "
    "to amend tax exemptions, interest schedules, or scheme terms prospectively or retrospectively through "
    "Finance Acts, Gazette notifications, or Government Orders.",
    "Post-launch revisions, caps, or discontinuations may reflect fiscal consolidation under FRBM targets, "
    "changing macroeconomic or commodity-price conditions (such as bullion price movements or interest-rate "
    "cycles), or budgetary reprioritisation across departments.",
    "Eligibility tightening, e-KYC deduplication, or restructuring into successor schemes may be intended "
    "to prevent tax arbitrage, reduce fiscal leakage, or target public subsidies toward narrower priority groups.",
)


@dataclass(frozen=True)
class _TextSegment:
    doc_role: str
    locator: str
    page: int | None
    raw_text: str
    norm_text: str


@dataclass(frozen=True)
class _TokenVerification:
    total_chars: int
    all_matched: bool
    matched_tokens: list[str]
    missing_tokens: list[str]
    best_locator: str
    best_page: int | None
    excerpt: str


def neutral_text(text: str) -> str:
    """Neutralize BNS Sec 356 prohibited words in quoted third-party public text."""
    cleaned = _ACB_RE.sub("Vigilance", text or "")
    return _NEUTRAL_REPLACE_RE.sub("compliance-review", cleaned)


def clean_scheme_text(text: Any, *, max_len: int | None = None) -> str:
    """Collapse control chars/whitespace, scrub PII, and apply BNS Sec 356 neutralization."""
    raw = str(text or "")
    raw = _CTRL_CHARS_RE.sub(" ", raw)
    raw = _MULTI_SPACE_RE.sub(" ", raw).strip()
    if not raw:
        return ""
    scrubbed, _ = scrub_pii(raw)
    neutral = _MULTI_SPACE_RE.sub(" ", neutral_text(scrubbed)).strip()
    if max_len is not None and len(neutral) > max_len:
        return neutral[: max(1, max_len - 1)].rstrip() + "…"
    return neutral


def canonical_key(name: str, *, max_len: int = 80) -> str:
    """Normalize a scheme or authority name into a lowercase hyphenated slug."""
    cleaned = _MULTI_SPACE_RE.sub(" ", str(name or "").replace("&", " and ")).strip().lower()
    cleaned = _NON_ALNUM_SPACE_RE.sub(" ", cleaned)
    cleaned = _MULTI_SPACE_RE.sub(" ", cleaned).strip()
    return cleaned.replace(" ", "-")[:max_len].strip("-")


def parse_iso_date(value: Any) -> date | None:
    """Strictly parse a YYYY-MM-DD calendar date."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value).strip()
    if not _ISO_DATE_RE.fullmatch(raw):
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _norm_search(text: str) -> str:
    """Normalize Unicode, dashes, smart quotes, whitespace, and case for deterministic token matching."""
    norm = unicodedata.normalize("NFKC", text or "")
    norm = _DASH_VARIANTS_RE.sub("-", norm)
    norm = _QUOTE_SINGLE_RE.sub("'", norm)
    norm = _QUOTE_DOUBLE_RE.sub('"', norm)
    norm = _CTRL_CHARS_RE.sub(" ", norm)
    norm = _MULTI_SPACE_RE.sub(" ", norm).strip().lower()
    return norm


def _format_inr_brief(amount: float | None) -> str:
    if amount is None or not math.isfinite(amount) or amount <= 0:
        return "unquantified in cited primary record"
    if amount >= 10_000_000:
        return f"INR {amount / 10_000_000:,.2f} Cr"
    if amount >= 100_000:
        return f"INR {amount / 100_000:,.2f} L"
    return f"INR {amount:,.0f}"


def _indian_fy_from_date(dt: date | None) -> str:
    if dt is None:
        return "Unspecified"
    fy_start = dt.year if dt.month >= 4 else dt.year - 1
    return f"FY{fy_start}-{(fy_start + 1) % 100:02d}"


def _parse_optional_exposure(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed < 0:
        return None
    return round(parsed, 2)


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


def _flatten_record_text(rec: Any, *, depth: int = 0) -> str:
    """Recursively flatten a structured JSON/CSV/PDF/HTML record into deterministic searchable text."""
    if depth > 12 or rec is None:
        return ""
    if isinstance(rec, str):
        return rec
    if isinstance(rec, (int, float)) and not isinstance(rec, bool):
        return str(rec)
    if isinstance(rec, bool):
        return "true" if rec else "false"
    if isinstance(rec, list):
        parts = [_flatten_record_text(item, depth=depth + 1) for item in rec[:500]]
        return " ".join(p for p in parts if p)
    if isinstance(rec, dict):
        # Fast path for PDF/HTML/TXT text records produced by loader.py
        if isinstance(rec.get("raw_text"), str) and rec["raw_text"].strip():
            return rec["raw_text"]
        if isinstance(rec.get("text"), str) and rec["text"].strip():
            return rec["text"]
        skip_keys = {
            "document",
            "secondary_sha256",
            "secondary_format",
            "source_ref",
            "source_url",
            "source_row_index",
            "source_format",
            "jurisdiction",
        }
        parts: list[str] = []
        for k, v in rec.items():
            if k in skip_keys or v is None or v == "":
                continue
            val_txt = _flatten_record_text(v, depth=depth + 1)
            if val_txt:
                parts.append(f"{k}: {val_txt}")
        return " | ".join(parts)
    return str(rec)


def _is_page_text_record(rec: dict[str, Any]) -> bool:
    return "page" in rec and ("raw_text" in rec or "text" in rec)


def _split_primary_and_secondary(
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    """Separate primary document records from optional secondary document records."""
    primary: list[dict[str, Any]] = []
    secondary: list[dict[str, Any]] = []
    sec_sha: str | None = None

    for rec in records:
        if not isinstance(rec, dict):
            continue
        doc_tag = str(rec.get("document") or "").strip().lower()
        if doc_tag == "secondary":
            secondary.append(rec)
            if not sec_sha and isinstance(rec.get("secondary_sha256"), str) and rec["secondary_sha256"].strip():
                sec_sha = rec["secondary_sha256"].strip().lower()
        else:
            primary.append(rec)

    return primary, secondary, sec_sha


def _apply_record_selector(
    pool: list[dict[str, Any]],
    selector: dict[str, Any],
) -> tuple[list[dict[str, Any]], bool]:
    """Filter structured JSON/CSV rows using ``record_selector.match_field`` / ``match_value``.

    Returns ``(filtered_rows, selector_failed)``. Never filters out prose page-text records
    when ``match_field`` is absent; fails closed (``selector_failed=True``) when an explicit
    ``match_field`` + ``match_value`` matches zero rows across the pool.
    """
    match_field = str(selector.get("match_field") or "").strip()
    match_value = str(selector.get("match_value") or "").strip()
    if not match_field or not match_value:
        return pool, False

    structured_rows = [r for r in pool if not _is_page_text_record(r)]
    if not structured_rows:
        # Pool consists only of prose/PDF/HTML page records; match_field cannot select a row.
        return [], True

    expected = _norm_search(match_value)
    matched: list[dict[str, Any]] = []
    for row in structured_rows:
        raw_val = _lookup_path(row, match_field)
        if raw_val in (None, ""):
            continue
        if _norm_search(str(raw_val)) == expected:
            matched.append(row)

    if not matched:
        return [], True
    return matched, False


def _build_segments(pool: list[dict[str, Any]], doc_role: str) -> list[_TextSegment]:
    segments: list[_TextSegment] = []
    for idx, rec in enumerate(pool, start=1):
        raw_text = _flatten_record_text(rec)
        norm_text = _norm_search(raw_text)
        if not norm_text:
            continue
        pg_raw = rec.get("page")
        page_num = int(pg_raw) if isinstance(pg_raw, int) and not isinstance(pg_raw, bool) and pg_raw > 0 else None
        if page_num is not None:
            fmt = str(rec.get("source_format") or "").strip().lower()
            locator = f"html_body" if fmt == "html" and page_num == 1 else f"page={page_num}"
        else:
            row_idx = rec.get("source_row_index")
            row_num = int(row_idx) if isinstance(row_idx, int) and not isinstance(row_idx, bool) and row_idx > 0 else idx
            locator = f"row={row_num}"
        segments.append(
            _TextSegment(
                doc_role=doc_role,
                locator=locator,
                page=page_num,
                raw_text=raw_text,
                norm_text=norm_text,
            )
        )
    return segments


def _extract_verbatim_window(raw_text: str, token: str, *, window_chars: int = 340) -> str:
    """Extract a clean verbatim context window around ``token`` inside ``raw_text``."""
    compact_raw = _MULTI_SPACE_RE.sub(" ", _CTRL_CHARS_RE.sub(" ", raw_text or "")).strip()
    if not compact_raw:
        return ""
    norm_raw = _norm_search(compact_raw)
    norm_tok = _norm_search(token)
    pos = norm_raw.find(norm_tok) if norm_tok else -1
    if pos == -1:
        return clean_scheme_text(compact_raw[:window_chars], max_len=window_chars)

    # Approximate corresponding index in compact_raw (lengths match closely after NFKC+space collapse).
    ratio = len(compact_raw) / max(1, len(norm_raw))
    approx_start = int(pos * ratio)
    half = window_chars // 2
    start = max(0, approx_start - half)
    end = min(len(compact_raw), start + window_chars)
    if start > 0:
        space_after = compact_raw.find(" ", start, min(len(compact_raw), start + 24))
        if space_after != -1:
            start = space_after + 1
    snippet = compact_raw[start:end].strip()
    if start > 0:
        snippet = "…" + snippet
    if end < len(compact_raw):
        snippet = snippet.rstrip() + "…"
    return clean_scheme_text(snippet, max_len=window_chars + 16)


def _verify_evidence_tokens(
    tokens: list[str],
    segments: list[_TextSegment],
    *,
    preferred_page: int | None = None,
) -> _TokenVerification:
    """Verify every required documentary token exists in the fetched official bytes."""
    total_chars = sum(len(s.norm_text) for s in segments)
    if not segments or total_chars < MIN_READABLE_DOC_CHARS:
        return _TokenVerification(
            total_chars=total_chars,
            all_matched=False,
            matched_tokens=[],
            missing_tokens=list(tokens),
            best_locator="",
            best_page=None,
            excerpt="",
        )

    combined_norm = "\n".join(s.norm_text for s in segments)
    matched: list[str] = []
    missing: list[str] = []
    for tok in tokens:
        ntok = _norm_search(tok)
        if ntok and ntok in combined_norm:
            matched.append(tok)
        else:
            missing.append(tok)

    if not matched:
        return _TokenVerification(
            total_chars=total_chars,
            all_matched=False,
            matched_tokens=[],
            missing_tokens=missing,
            best_locator="",
            best_page=None,
            excerpt="",
        )

    # Pick the segment with highest token-match density (tie-breaking on preferred_page, then order).
    best_seg = segments[0]
    best_hits = -1
    best_first_tok = matched[0]
    for seg in segments:
        seg_hits = [tok for tok in matched if _norm_search(tok) in seg.norm_text]
        hit_count = len(seg_hits)
        bonus = 1 if (preferred_page is not None and seg.page == preferred_page and hit_count > 0) else 0
        if (hit_count + bonus) > best_hits:
            best_hits = hit_count + bonus
            best_seg = seg
            if seg_hits:
                best_first_tok = seg_hits[0]

    excerpt = _extract_verbatim_window(best_seg.raw_text, best_first_tok, window_chars=340)
    return _TokenVerification(
        total_chars=total_chars,
        all_matched=(len(missing) == 0),
        matched_tokens=matched,
        missing_tokens=missing,
        best_locator=best_seg.locator,
        best_page=best_seg.page,
        excerpt=excerpt,
    )


def evaluate_walkback_rule(
    *,
    walkback_mechanism: str,
    retroactive_impact: str,
    is_sgb_anchor: bool,
    scheme_slug: str,
    estimated_exposure_inr: float | None,
    days_from_promise_to_walkback: int | None,
) -> tuple[bool, str, str, list[str], str]:
    """Deterministically evaluate P8 mechanism, rules, signal category, and severity.

    Returns ``(flagged, rule_triggered, signal_category, triggered_rules, severity)``.
    """
    if walkback_mechanism == "grandfathered_or_honored" or retroactive_impact == "grandfathered_or_honored":
        return False, "", "", [], "low"

    rule_triggered = WALKBACK_MECHANISM_RULES.get(walkback_mechanism, "")
    if not rule_triggered:
        return False, "", "", [], "low"

    signal_category = walkback_mechanism
    triggered_rules = [rule_triggered]

    if retroactive_impact == "retroactive_existing_holders":
        triggered_rules.append("P8-RETROACTIVE-EXISTING-HOLDERS")
    elif retroactive_impact == "mid_stream_active_scheme":
        triggered_rules.append("P8-MIDSTREAM-ACTIVE-TENURE")

    if is_sgb_anchor or scheme_slug == "sovereign-gold-bond":
        triggered_rules.append("P8-SGB-SOVEREIGN-COMMITMENT")

    exposure = estimated_exposure_inr if (estimated_exposure_inr is not None and estimated_exposure_inr > 0) else 0.0
    if exposure >= MAJOR_EXPOSURE_INR:
        triggered_rules.append("P8-HIGH-PUBLISHED-EXPOSURE")

    if days_from_promise_to_walkback is not None and 0 <= days_from_promise_to_walkback <= RAPID_WALKBACK_DAYS:
        triggered_rules.append("P8-RAPID-WALKBACK-LE-365D")

    if (
        is_sgb_anchor
        or (
            retroactive_impact == "retroactive_existing_holders"
            and walkback_mechanism
            in {"retroactive_tax_reversal", "midstream_benefit_cut", "lockin_or_exit_restriction"}
        )
        or exposure >= CRITICAL_EXPOSURE_INR
    ):
        severity = "critical"
    elif (
        retroactive_impact in {"retroactive_existing_holders", "mid_stream_active_scheme", "unfulfilled_arrears"}
        or walkback_mechanism == "retroactive_tax_reversal"
        or exposure >= HIGH_EXPOSURE_INR
    ):
        severity = "high"
    else:
        severity = "medium"

    return True, rule_triggered, signal_category, triggered_rules, severity


def run_p8_sovereign_lies(
    records: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
) -> tuple[list[AuditLeadCandidate], int, float, str, dict[str, Any]]:
    """Run deterministic, fail-closed P8 scheme-promise vs walk-back verification."""
    ctx = context or {}
    spec = ctx.get("sovereign_lies") if isinstance(ctx.get("sovereign_lies"), dict) else {}
    as_of_iso = str(ctx.get("as_of_date") or "").strip()[:10] or datetime.now(timezone.utc).date().isoformat()

    meta: dict[str, Any] = {
        "as_of_date": as_of_iso,
        "amount_type": "scheme_promises_and_revisions",
        "unevaluable_count": 0,
    }

    case_key = str(spec.get("case_key") or "").strip()
    if not records or not spec or not case_key:
        meta.update({"verdict": "unevaluable", "unevaluable_count": 1, "reason": "missing_records_or_spec"})
        return (
            [],
            0,
            0.0,
            "P8 unevaluable: missing official document payload or sovereign_lies specification.",
            meta,
        )

    walkback_mechanism = str(spec.get("walkback_mechanism") or "").strip().lower()
    retroactive_impact = str(spec.get("retroactive_impact") or "").strip().lower()
    if walkback_mechanism not in VALID_WALKBACK_MECHANISMS or retroactive_impact not in VALID_RETROACTIVE_IMPACTS:
        meta.update(
            {
                "verdict": "unevaluable",
                "unevaluable_count": 1,
                "reason": "invalid_mechanism_or_impact",
                "case_key": case_key,
            }
        )
        return (
            [],
            0,
            0.0,
            f"P8 unevaluable ({case_key}): unrecognized walkback_mechanism or retroactive_impact.",
            meta,
        )

    promise_tokens = [
        str(t).strip()
        for t in (spec.get("promise_evidence_tokens") or [])
        if isinstance(t, str) and str(t).strip()
    ]
    walkback_tokens = [
        str(t).strip()
        for t in (spec.get("walkback_evidence_tokens") or [])
        if isinstance(t, str) and str(t).strip()
    ]
    if len(promise_tokens) < MIN_EVIDENCE_TOKENS or len(walkback_tokens) < MIN_EVIDENCE_TOKENS:
        meta.update(
            {
                "verdict": "unevaluable",
                "unevaluable_count": 1,
                "reason": "insufficient_evidence_tokens",
                "case_key": case_key,
            }
        )
        return (
            [],
            0,
            0.0,
            f"P8 unevaluable ({case_key}): at least {MIN_EVIDENCE_TOKENS} promise and walkback evidence tokens are required.",
            meta,
        )

    primary_records, secondary_records, sec_sha_from_recs = _split_primary_and_secondary(records)
    selector = spec.get("record_selector") if isinstance(spec.get("record_selector"), dict) else {}
    secondary_role = str(spec.get("secondary_role") or "").strip().lower()
    if secondary_role not in VALID_SECONDARY_ROLES:
        secondary_role = "walkback_document" if secondary_records else ""

    # Apply structured record selector when match_field + match_value are specified.
    if selector.get("match_field") and selector.get("match_value"):
        if secondary_records and secondary_role == "promise_document":
            # Primary is structured walkback/parliament record; secondary is launch doc (or vice versa)
            filtered_pri, pri_failed = _apply_record_selector(primary_records, selector)
            if not pri_failed:
                primary_records = filtered_pri
            else:
                filtered_sec, sec_failed = _apply_record_selector(secondary_records, selector)
                if not sec_failed:
                    secondary_records = filtered_sec
                else:
                    meta.update(
                        {
                            "verdict": "unevaluable",
                            "unevaluable_count": 1,
                            "reason": "record_selector_no_match",
                            "case_key": case_key,
                        }
                    )
                    return (
                        [],
                        0,
                        0.0,
                        f"P8 unevaluable ({case_key}): record_selector matched 0 rows in verified document bytes.",
                        meta,
                    )
        else:
            filtered_pri, pri_failed = _apply_record_selector(primary_records, selector)
            if not pri_failed:
                primary_records = filtered_pri
            elif secondary_records:
                filtered_sec, sec_failed = _apply_record_selector(secondary_records, selector)
                if not sec_failed:
                    secondary_records = filtered_sec
                else:
                    meta.update(
                        {
                            "verdict": "unevaluable",
                            "unevaluable_count": 1,
                            "reason": "record_selector_no_match",
                            "case_key": case_key,
                        }
                    )
                    return (
                        [],
                        0,
                        0.0,
                        f"P8 unevaluable ({case_key}): record_selector matched 0 rows in verified document bytes.",
                        meta,
                    )
            else:
                meta.update(
                    {
                        "verdict": "unevaluable",
                        "unevaluable_count": 1,
                        "reason": "record_selector_no_match",
                        "case_key": case_key,
                    }
                )
                return (
                    [],
                    0,
                    0.0,
                    f"P8 unevaluable ({case_key}): record_selector matched 0 rows in verified document bytes.",
                    meta,
                )

    primary_segments = _build_segments(primary_records, "primary")
    secondary_segments = _build_segments(secondary_records, "secondary")

    retrieval_url = str(ctx.get("retrieval_url") or ctx.get("dataset_url") or "").strip()
    secondary_url = str(ctx.get("secondary_url") or ctx.get("secondary_dataset_url") or "").strip()
    has_secondary = bool(secondary_records) or (bool(secondary_url) and secondary_url != retrieval_url)

    promise_page_hint = selector.get("promise_page") if isinstance(selector.get("promise_page"), int) else None
    walkback_page_hint = selector.get("walkback_page") if isinstance(selector.get("walkback_page"), int) else None

    # Determine which verified byte corpus must prove the launch promise vs the subsequent walk-back.
    # Use `has_secondary` (not `bool(secondary_segments)`) so a scanned/empty secondary PDF fails closed!
    if has_secondary and secondary_role == "promise_document":
        promise_segments = secondary_segments
        walkback_segments = primary_segments
        promise_uses_secondary = True
        walkback_uses_secondary = False
    elif has_secondary and secondary_role in {"", "walkback_document"}:
        promise_segments = primary_segments
        walkback_segments = secondary_segments
        promise_uses_secondary = False
        walkback_uses_secondary = True
    elif has_secondary and secondary_role == "corroborating_record":
        promise_segments = primary_segments + secondary_segments
        walkback_segments = primary_segments + secondary_segments
        promise_uses_secondary = False
        walkback_uses_secondary = True
    else:
        promise_segments = primary_segments
        walkback_segments = primary_segments
        promise_uses_secondary = False
        walkback_uses_secondary = False

    promise_ver = _verify_evidence_tokens(promise_tokens, promise_segments, preferred_page=promise_page_hint)
    walkback_ver = _verify_evidence_tokens(walkback_tokens, walkback_segments, preferred_page=walkback_page_hint)

    if promise_ver.total_chars < MIN_READABLE_DOC_CHARS or walkback_ver.total_chars < MIN_READABLE_DOC_CHARS:
        meta.update(
            {
                "verdict": "unevaluable",
                "unevaluable_count": 1,
                "reason": "unreadable_or_empty_document",
                "case_key": case_key,
                "promise_chars": promise_ver.total_chars,
                "walkback_chars": walkback_ver.total_chars,
            }
        )
        return (
            [],
            0,
            0.0,
            f"P8 unevaluable ({case_key}): official document lacks machine-readable text (<{MIN_READABLE_DOC_CHARS} chars).",
            meta,
        )

    if not promise_ver.all_matched or not walkback_ver.all_matched:
        meta.update(
            {
                "verdict": "unevaluable",
                "unevaluable_count": 1,
                "reason": "evidence_tokens_not_verified",
                "case_key": case_key,
                "missing_promise_tokens": promise_ver.missing_tokens,
                "missing_walkback_tokens": walkback_ver.missing_tokens,
            }
        )
        return (
            [],
            0,
            0.0,
            (
                f"P8 unevaluable ({case_key}): downloaded official bytes did not verify all required evidence tokens "
                f"(missing promise={len(promise_ver.missing_tokens)}, missing walkback={len(walkback_ver.missing_tokens)})."
            ),
            meta,
        )

    # In corroborating_record dual-doc mode, ensure BOTH primary and secondary bytes independently substantiate tokens.
    if has_secondary and secondary_role == "corroborating_record":
        pri_chars = sum(len(s.norm_text) for s in primary_segments)
        sec_chars = sum(len(s.norm_text) for s in secondary_segments)
        if pri_chars < MIN_READABLE_DOC_CHARS or sec_chars < MIN_READABLE_DOC_CHARS:
            meta.update(
                {
                    "verdict": "unevaluable",
                    "unevaluable_count": 1,
                    "reason": "unreadable_or_empty_document",
                    "case_key": case_key,
                    "primary_chars": pri_chars,
                    "secondary_chars": sec_chars,
                }
            )
            return (
                [],
                0,
                0.0,
                f"P8 unevaluable ({case_key}): corroborating primary or secondary document lacks readable text.",
                meta,
            )
        all_tokens = promise_tokens + walkback_tokens
        pri_combined = "\n".join(s.norm_text for s in primary_segments)
        sec_combined = "\n".join(s.norm_text for s in secondary_segments)
        pri_hit = any(_norm_search(t) in pri_combined for t in all_tokens)
        sec_hit = any(_norm_search(t) in sec_combined for t in all_tokens)
        if not pri_hit or not sec_hit:
            meta.update(
                {
                    "verdict": "unevaluable",
                    "unevaluable_count": 1,
                    "reason": "secondary_document_uncorroborated",
                    "case_key": case_key,
                    "primary_corroborated": pri_hit,
                    "secondary_corroborated": sec_hit,
                }
            )
            return (
                [],
                0,
                0.0,
                f"P8 unevaluable ({case_key}): corroborating primary/secondary document lacked evidence token overlap.",
                meta,
            )

    # Extract & neutralize metadata fields
    scheme_name = clean_scheme_text(spec.get("scheme_name"), max_len=200) or "Public Scheme"
    scheme_slug = str(spec.get("scheme_slug") or canonical_key(scheme_name)).strip().lower()
    tranche_or_phase = clean_scheme_text(spec.get("tranche_or_phase"), max_len=160) or "Scheme Terms"
    jurisdiction_level = str(spec.get("jurisdiction_level") or "union").strip().lower()
    if jurisdiction_level not in VALID_JURISDICTION_LEVELS:
        jurisdiction_level = "union"
    state_or_ut_raw = spec.get("state_or_ut")
    state_or_ut = (
        clean_scheme_text(state_or_ut_raw, max_len=80) or None
        if jurisdiction_level in {"state", "ut"} and state_or_ut_raw not in (None, "")
        else None
    )
    jurisdiction = clean_scheme_text(spec.get("jurisdiction") or ctx.get("jurisdiction"), max_len=120)
    if not jurisdiction:
        if jurisdiction_level == "state" and state_or_ut:
            jurisdiction = f"State:{state_or_ut}"
        elif jurisdiction_level == "ut" and state_or_ut:
            jurisdiction = f"UT:{state_or_ut}"
        else:
            jurisdiction = "Union of India"

    authority = clean_scheme_text(spec.get("authority") or ctx.get("official_publisher"), max_len=200) or "Public Authority"
    authority_key = str(spec.get("authority_key") or canonical_key(authority)).strip().lower()
    scheme_category = str(spec.get("scheme_category") or "welfare_health_public_services").strip().lower()
    if scheme_category not in VALID_SCHEME_CATEGORIES:
        scheme_category = "welfare_health_public_services"

    gov_era = clean_scheme_text(spec.get("government_era_or_launch_year"), max_len=64)
    promise_dt = parse_iso_date(spec.get("promise_date"))
    walkback_dt = parse_iso_date(spec.get("walkback_date"))
    promise_date_iso = promise_dt.isoformat() if promise_dt else str(spec.get("promise_date") or "").strip()
    walkback_date_iso = walkback_dt.isoformat() if walkback_dt else (str(spec.get("walkback_date") or "").strip() or None)
    launch_year = promise_dt.year if promise_dt else (int(spec["launch_year"]) if isinstance(spec.get("launch_year"), int) else None)
    walkback_year = walkback_dt.year if walkback_dt else None
    days_between = (
        max(0, (walkback_dt - promise_dt).days)
        if (promise_dt is not None and walkback_dt is not None and walkback_dt >= promise_dt)
        else None
    )

    promise_summary = clean_scheme_text(spec.get("promise_summary"), max_len=600)
    promise_document_ref = clean_scheme_text(spec.get("promise_document_ref"), max_len=200)
    walkback_summary = clean_scheme_text(spec.get("walkback_summary"), max_len=600)
    walkback_document_ref = clean_scheme_text(spec.get("walkback_document_ref"), max_len=200)
    promised_metric = clean_scheme_text(spec.get("promised_metric"), max_len=160)
    revised_metric = clean_scheme_text(spec.get("revised_metric"), max_len=160)
    metric_unit = clean_scheme_text(spec.get("metric_unit"), max_len=64)
    affected_group = clean_scheme_text(spec.get("affected_group"), max_len=160)
    statutory_anchor = clean_scheme_text(spec.get("statutory_anchor"), max_len=200) or None
    estimated_exposure_inr = _parse_optional_exposure(spec.get("estimated_exposure_inr"))
    exposure_basis = clean_scheme_text(spec.get("exposure_basis"), max_len=200) or None
    audited_inr = float(estimated_exposure_inr) if estimated_exposure_inr is not None else 0.0
    is_sgb_anchor = bool(spec.get("is_sgb_anchor"))
    is_anchor_case = bool(spec.get("is_anchor_case")) or is_sgb_anchor

    flagged, rule_triggered, signal_category, triggered_rules, severity = evaluate_walkback_rule(
        walkback_mechanism=walkback_mechanism,
        retroactive_impact=retroactive_impact,
        is_sgb_anchor=is_sgb_anchor,
        scheme_slug=scheme_slug,
        estimated_exposure_inr=estimated_exposure_inr,
        days_from_promise_to_walkback=days_between,
    )

    if not flagged:
        cleared_summary = clean_scheme_text(
            f"P8 cleared ({case_key}, grandfathered_or_honored): {scheme_name} ({jurisdiction}) — "
            f"launch promise ({promise_date_iso}, {promise_document_ref}) verified in official record and "
            f"confirmed honored or grandfathered intact ({walkback_document_ref}): {walkback_summary}",
            max_len=1200,
        )
        meta.update(
            {
                "verdict": "cleared_honored_or_grandfathered",
                "case_key": case_key,
                "scheme_slug": scheme_slug,
                "walkback_mechanism": walkback_mechanism,
                "retroactive_impact": retroactive_impact,
                "promise_tokens_matched": promise_ver.matched_tokens,
                "walkback_tokens_matched": walkback_ver.matched_tokens,
                "unevaluable_count": 0,
            }
        )
        return [], 1, audited_inr, cleared_summary, meta

    landing_url = str(
        selector.get("record_url") or ctx.get("source_landing_page") or ctx.get("source_url") or retrieval_url
    ).strip()
    expected_sha = str(ctx.get("expected_sha256") or "").strip().lower() or None
    expected_sec_sha = (
        str(ctx.get("secondary_expected_sha256") or ctx.get("secondary_sha256") or "").strip().lower()
        or sec_sha_from_recs
        or None
    )
    official_publisher = clean_scheme_text(ctx.get("official_publisher") or authority, max_len=200)

    promise_url = (secondary_url if promise_uses_secondary and secondary_url else retrieval_url) or landing_url
    promise_sha = expected_sec_sha if (promise_uses_secondary and expected_sec_sha) else expected_sha
    walkback_url = (secondary_url if walkback_uses_secondary and secondary_url else retrieval_url) or landing_url
    walkback_sha = expected_sec_sha if (walkback_uses_secondary and expected_sec_sha) else expected_sha

    section_hint = clean_scheme_text(selector.get("section_hint"), max_len=120)
    promise_locator = promise_ver.best_locator + (f"; {section_hint}" if section_hint else "")
    walkback_locator = walkback_ver.best_locator + (f"; {section_hint}" if section_hint else "")
    source_locator = f"promise={promise_locator} | walkback={walkback_locator}"

    mech_label = _MECHANISM_DISPLAY.get(walkback_mechanism, "Scheme Commitment Divergence")
    lead_title = clean_scheme_text(
        f"Sovereign Scheme Commitment Divergence ({mech_label}): {scheme_name} — {tranche_or_phase}",
        max_len=195,
    )

    elapsed_clause = (
        f" ({days_between:,} days after launch notification)"
        if days_between is not None
        else ""
    )
    exposure_clause = (
        f" Officially published exposure figure: {_format_inr_brief(estimated_exposure_inr)}"
        + (f" ({exposure_basis})" if exposure_basis else "")
        + "."
        if estimated_exposure_inr is not None and estimated_exposure_inr > 0
        else ""
    )
    summary = clean_scheme_text(
        f"Documentary comparison for {scheme_name} ({tranche_or_phase}; {jurisdiction}; {authority}): "
        f"Original launch promise ({promise_date_iso}, {promise_document_ref}) stated: {promise_summary} "
        f"[Promised metric: {promised_metric}]. "
        f"Subsequent record ({walkback_date_iso or 'subsequent amendment'}, {walkback_document_ref}){elapsed_clause} "
        f"recorded: {walkback_summary} [Revised metric: {revised_metric}]. "
        f"Classification: {mech_label} ({retroactive_impact}); affected group: {affected_group}.{exposure_clause} "
        f"Verified against checksummed official primary records for public-interest review.",
        max_len=1500,
    )

    benign_list = list(_BENIGN_EXPLANATIONS)
    extra_benign = clean_scheme_text(spec.get("benign_explanation_context"), max_len=300)
    if extra_benign:
        benign_list.insert(0, extra_benign)

    fy_walkback = _indian_fy_from_date(walkback_dt or promise_dt)
    fy_start = _indian_fy_from_date(promise_dt) if promise_dt else None
    fy_end = _indian_fy_from_date(walkback_dt) if walkback_dt else fy_start

    promise_cit: dict[str, Any] = {
        "role": "original_promise",
        "title": "Original Launch Promise",
        "document": promise_document_ref or f"{scheme_name} Launch Notification",
        "source_url": promise_url,
        "landing_url": landing_url,
        "source_landing_page": landing_url,
        "official_publisher": official_publisher,
        "source_locator": promise_locator,
        "sha256": promise_sha,
        "excerpt": promise_ver.excerpt or promise_summary[:340],
    }
    if promise_ver.best_page is not None:
        promise_cit["page"] = promise_ver.best_page

    walkback_cit: dict[str, Any] = {
        "role": "subsequent_walkback",
        "title": "Subsequent Amendment / Walk-Back",
        "document": walkback_document_ref or f"{scheme_name} Subsequent Revision",
        "source_url": walkback_url,
        "landing_url": landing_url,
        "source_landing_page": landing_url,
        "official_publisher": official_publisher,
        "source_locator": walkback_locator,
        "sha256": walkback_sha,
        "excerpt": walkback_ver.excerpt or walkback_summary[:340],
    }
    if walkback_ver.best_page is not None:
        walkback_cit["page"] = walkback_ver.best_page

    benchmark_cit: dict[str, Any] = {
        "role": "statutory_benchmark",
        "title": "Documentary Comparison & Statutory Anchor",
        "document": statutory_anchor or f"{rule_triggered} ({jurisdiction})",
        "source_url": landing_url or retrieval_url,
        "landing_url": landing_url or retrieval_url,
        "source_landing_page": landing_url or retrieval_url,
        "official_publisher": official_publisher,
        "source_locator": f"rule={rule_triggered}; mechanism={walkback_mechanism}; impact={retroactive_impact}",
        "sha256": expected_sha,
        "excerpt": clean_scheme_text(
            f"{rule_triggered}: Promised '{promised_metric}' ({promise_date_iso}) vs Revised '{revised_metric}' "
            f"({walkback_date_iso or 'subsequent'}); verified {len(promise_ver.matched_tokens)} promise token(s) "
            f"and {len(walkback_ver.matched_tokens)} walk-back token(s) in official bytes.",
            max_len=360,
        ),
    }

    citations = [promise_cit, walkback_cit, benchmark_cit]

    lead = AuditLeadCandidate(
        title=lead_title,
        summary=summary,
        methodology_code=SOVEREIGN_LIES_METHODOLOGY,
        entity_name=authority[:160],
        jurisdiction=jurisdiction,
        fiscal_year=fy_walkback,
        fiscal_year_start=fy_start,
        fiscal_year_end=fy_end,
        monetary_exposure_inr=round(audited_inr, 2),
        severity=severity,  # type: ignore[arg-type]
        anomaly_metrics={
            "case_key": case_key,
            "verdict": "flagged_walkback",
            "signal_category": signal_category,
            "triggered_rules": triggered_rules,
            "scheme_name": scheme_name,
            "scheme_slug": scheme_slug,
            "tranche_or_phase": tranche_or_phase,
            "scheme_category": scheme_category,
            "jurisdiction": jurisdiction,
            "jurisdiction_level": jurisdiction_level,
            "state_or_ut": state_or_ut,
            "authority": authority,
            "authority_key": authority_key,
            "government_era_or_launch_year": gov_era,
            "launch_year": launch_year,
            "walkback_year": walkback_year,
            "promise_date": promise_date_iso,
            "promise_summary": promise_summary,
            "promise_document_ref": promise_document_ref,
            "promise_excerpt": promise_ver.excerpt,
            "promise_locator": promise_locator,
            "promise_tokens_matched": promise_ver.matched_tokens,
            "walkback_date": walkback_date_iso,
            "walkback_summary": walkback_summary,
            "walkback_document_ref": walkback_document_ref,
            "walkback_excerpt": walkback_ver.excerpt,
            "walkback_locator": walkback_locator,
            "walkback_tokens_matched": walkback_ver.matched_tokens,
            "walkback_mechanism": walkback_mechanism,
            "retroactive_impact": retroactive_impact,
            "days_from_promise_to_walkback": days_between,
            "promised_metric": promised_metric,
            "revised_metric": revised_metric,
            "metric_unit": metric_unit,
            "affected_group": affected_group,
            "estimated_exposure_inr": estimated_exposure_inr,
            "exposure_basis": exposure_basis,
            "statutory_anchor": statutory_anchor,
            "secondary_role": secondary_role or None,
            "is_sgb_anchor": is_sgb_anchor,
            "is_anchor_case": is_anchor_case,
            "as_of_date": as_of_iso,
            "source_locator": source_locator,
        },
        rule_triggered=rule_triggered,
        source_locator=source_locator,
        citations=citations,
        benign_explanations=benign_list,
        suggested_authority=authority[:160],
        amount_type="scheme_promises_and_revisions",
    )

    run_summary = (
        f"P8 flagged {case_key} ({scheme_name}, {jurisdiction}): {rule_triggered} "
        f"({walkback_mechanism} / {retroactive_impact}, severity={severity})."
    )
    meta.update(
        {
            "verdict": "flagged_walkback",
            "signal_category": signal_category,
            "rule_triggered": rule_triggered,
            "case_key": case_key,
            "scheme_slug": scheme_slug,
            "unevaluable_count": 0,
        }
    )
    return [lead], 0, audited_inr, run_summary, meta


def validate_context(ctx: Any, where: str) -> list[str]:
    """Strictly validate ``payload.context.sovereign_lies`` for a P8 task."""
    errors: list[str] = []
    if not isinstance(ctx, dict):
        return [f"{where}.payload.context.sovereign_lies is required for P8 tasks."]
    spec = ctx.get("sovereign_lies")
    if not isinstance(spec, dict):
        return [f"{where}.payload.context.sovereign_lies must be an object."]

    as_of = ctx.get("as_of_date")
    if as_of not in (None, "") and (not isinstance(as_of, str) or parse_iso_date(as_of) is None):
        errors.append(f"{where}.payload.context.as_of_date must be a valid YYYY-MM-DD date when provided.")

    ckey = spec.get("case_key")
    if not isinstance(ckey, str) or not _KEY_RE.fullmatch(ckey):
        errors.append(f"{where}.payload.context.sovereign_lies.case_key must match [a-z0-9-] (<= 59 chars).")

    sslug = spec.get("scheme_slug")
    if not isinstance(sslug, str) or not _SLUG_KEY_RE.fullmatch(sslug):
        errors.append(f"{where}.payload.context.sovereign_lies.scheme_slug must match [a-z0-9-] (<= 80 chars).")

    akey = spec.get("authority_key")
    if akey not in (None, ""):
        if not isinstance(akey, str) or not _SLUG_KEY_RE.fullmatch(akey):
            errors.append(
                f"{where}.payload.context.sovereign_lies.authority_key must match [a-z0-9-] (<= 80 chars)."
            )

    jlevel = spec.get("jurisdiction_level")
    if jlevel not in VALID_JURISDICTION_LEVELS:
        errors.append(
            f"{where}.payload.context.sovereign_lies.jurisdiction_level must be one of {sorted(VALID_JURISDICTION_LEVELS)}."
        )

    jur = spec.get("jurisdiction")
    if not isinstance(jur, str) or not jur.strip() or len(jur) > 120:
        errors.append(f"{where}.payload.context.sovereign_lies.jurisdiction must be non-empty text (<= 120 chars).")
    elif jlevel == "union" and jur.strip() != "Union of India":
        errors.append(
            f"{where}.payload.context.sovereign_lies.jurisdiction must be 'Union of India' when jurisdiction_level is 'union'."
        )
    elif jlevel == "state" and not jur.strip().startswith("State:"):
        errors.append(
            f"{where}.payload.context.sovereign_lies.jurisdiction must start with 'State:' when jurisdiction_level is 'state'."
        )
    elif jlevel == "ut" and not jur.strip().startswith("UT:"):
        errors.append(
            f"{where}.payload.context.sovereign_lies.jurisdiction must start with 'UT:' when jurisdiction_level is 'ut'."
        )

    state_or_ut = spec.get("state_or_ut")
    if jlevel in {"state", "ut"}:
        if not isinstance(state_or_ut, str) or not state_or_ut.strip() or len(state_or_ut) > 80:
            errors.append(
                f"{where}.payload.context.sovereign_lies.state_or_ut is required (<= 80 chars) for state/ut tasks."
            )
    elif state_or_ut is not None and (not isinstance(state_or_ut, str) or len(state_or_ut) > 80):
        errors.append(f"{where}.payload.context.sovereign_lies.state_or_ut must be null or a short string.")

    scat = spec.get("scheme_category")
    if scat not in VALID_SCHEME_CATEGORIES:
        errors.append(
            f"{where}.payload.context.sovereign_lies.scheme_category must be one of {sorted(VALID_SCHEME_CATEGORIES)}."
        )

    mech = spec.get("walkback_mechanism")
    if mech not in VALID_WALKBACK_MECHANISMS:
        errors.append(
            f"{where}.payload.context.sovereign_lies.walkback_mechanism must be one of {sorted(VALID_WALKBACK_MECHANISMS)}."
        )

    impact = spec.get("retroactive_impact")
    if impact not in VALID_RETROACTIVE_IMPACTS:
        errors.append(
            f"{where}.payload.context.sovereign_lies.retroactive_impact must be one of {sorted(VALID_RETROACTIVE_IMPACTS)}."
        )

    if (mech == "grandfathered_or_honored") != (impact == "grandfathered_or_honored"):
        errors.append(
            f"{where}.payload.context.sovereign_lies.walkback_mechanism and retroactive_impact must both be "
            "'grandfathered_or_honored' for Cleared benchmark tasks (or neither)."
        )

    required_text_fields: tuple[tuple[str, int], ...] = (
        ("scheme_name", 200),
        ("tranche_or_phase", 160),
        ("authority", 200),
        ("government_era_or_launch_year", 64),
        ("promise_summary", 600),
        ("promise_document_ref", 200),
        ("walkback_summary", 600),
        ("walkback_document_ref", 200),
        ("promised_metric", 160),
        ("revised_metric", 160),
        ("metric_unit", 64),
        ("affected_group", 160),
    )
    for fname, max_len in required_text_fields:
        val = spec.get(fname)
        if not isinstance(val, str) or not val.strip() or len(val) > max_len:
            errors.append(
                f"{where}.payload.context.sovereign_lies.{fname} must be non-empty text (<= {max_len} chars)."
            )
        elif scan_forbidden_terms(val):
            errors.append(
                f"{where}.payload.context.sovereign_lies.{fname} contains prohibited non-neutral terminology."
            )

    for opt_fname, max_len in (
        ("exposure_basis", 200),
        ("statutory_anchor", 200),
        ("benign_explanation_context", 300),
    ):
        val = spec.get(opt_fname)
        if val not in (None, ""):
            if not isinstance(val, str) or len(val) > max_len:
                errors.append(
                    f"{where}.payload.context.sovereign_lies.{opt_fname} must be text (<= {max_len} chars)."
                )
            elif scan_forbidden_terms(val):
                errors.append(
                    f"{where}.payload.context.sovereign_lies.{opt_fname} contains prohibited non-neutral terminology."
                )

    pdate_raw = spec.get("promise_date")
    pdt = parse_iso_date(pdate_raw) if isinstance(pdate_raw, str) else None
    if pdt is None or not (date(1947, 8, 15) <= pdt <= date(2027, 12, 31)):
        errors.append(
            f"{where}.payload.context.sovereign_lies.promise_date must be a valid YYYY-MM-DD date (1947-08-15 to 2027-12-31)."
        )

    wdate_raw = spec.get("walkback_date")
    wdt: date | None = None
    if wdate_raw in (None, ""):
        if mech != "grandfathered_or_honored":
            errors.append(
                f"{where}.payload.context.sovereign_lies.walkback_date is required unless walkback_mechanism is 'grandfathered_or_honored'."
            )
    else:
        wdt = parse_iso_date(wdate_raw) if isinstance(wdate_raw, str) else None
        if wdt is None or not (date(1947, 8, 15) <= wdt <= date(2027, 12, 31)):
            errors.append(
                f"{where}.payload.context.sovereign_lies.walkback_date must be a valid YYYY-MM-DD date."
            )
        elif pdt is not None and wdt < pdt:
            errors.append(
                f"{where}.payload.context.sovereign_lies.walkback_date ({wdt.isoformat()}) must not precede promise_date ({pdt.isoformat()})."
            )

    for tok_field in ("promise_evidence_tokens", "walkback_evidence_tokens"):
        toks = spec.get(tok_field)
        if (
            not isinstance(toks, list)
            or not (MIN_EVIDENCE_TOKENS <= len(toks) <= MAX_EVIDENCE_TOKENS)
            or not all(isinstance(t, str) and 3 <= len(t.strip()) <= 120 for t in toks)
        ):
            errors.append(
                f"{where}.payload.context.sovereign_lies.{tok_field} must list {MIN_EVIDENCE_TOKENS} to "
                f"{MAX_EVIDENCE_TOKENS} verbatim documentary tokens (each 3..120 chars)."
            )

    exp_raw = spec.get("estimated_exposure_inr")
    if exp_raw is not None:
        if isinstance(exp_raw, bool) or not isinstance(exp_raw, (int, float)) or not math.isfinite(float(exp_raw)) or float(exp_raw) < 0:
            errors.append(
                f"{where}.payload.context.sovereign_lies.estimated_exposure_inr must be null or a finite non-negative number."
            )

    if not isinstance(spec.get("is_sgb_anchor"), bool):
        errors.append(f"{where}.payload.context.sovereign_lies.is_sgb_anchor must be a boolean.")

    for fmt_key in ("source_format", "secondary_source_format"):
        fmt_val = spec.get(fmt_key)
        if fmt_val not in (None, ""):
            if not isinstance(fmt_val, str) or fmt_val not in VALID_SOURCE_FORMATS:
                errors.append(
                    f"{where}.payload.context.sovereign_lies.{fmt_key} must be one of {sorted(VALID_SOURCE_FORMATS)}."
                )

    sec_role = spec.get("secondary_role")
    if sec_role is not None and sec_role not in VALID_SECONDARY_ROLES:
        errors.append(
            f"{where}.payload.context.sovereign_lies.secondary_role must be one of {sorted(r for r in VALID_SECONDARY_ROLES if r)} or null."
        )

    selector = spec.get("record_selector")
    if selector is not None:
        if not isinstance(selector, dict):
            errors.append(f"{where}.payload.context.sovereign_lies.record_selector must be an object.")
        else:
            mf = selector.get("match_field")
            mv = selector.get("match_value")
            if bool(mf) != bool(mv):
                errors.append(
                    f"{where}.payload.context.sovereign_lies.record_selector must provide match_field and match_value together."
                )
            if mf not in (None, "") and (not isinstance(mf, str) or len(mf) > 80):
                errors.append(f"{where}.payload.context.sovereign_lies.record_selector.match_field must be <= 80 chars.")
            if mv not in (None, "") and (not isinstance(mv, str) or len(mv) > 160):
                errors.append(f"{where}.payload.context.sovereign_lies.record_selector.match_value must be <= 160 chars.")
            for pkey in ("promise_page", "walkback_page"):
                pval = selector.get(pkey)
                if pval is not None and (isinstance(pval, bool) or not isinstance(pval, int) or not (1 <= pval <= 5000)):
                    errors.append(
                        f"{where}.payload.context.sovereign_lies.record_selector.{pkey} must be a positive page integer (1..5000)."
                    )
            shint = selector.get("section_hint")
            if shint not in (None, "") and (not isinstance(shint, str) or len(shint) > 120):
                errors.append(f"{where}.payload.context.sovereign_lies.record_selector.section_hint must be <= 120 chars.")
            rurl = selector.get("record_url")
            if rurl not in (None, ""):
                try:
                    validate_safe_http_url(str(rurl), require_https=True, allow_private=False)
                except ValueError as exc:
                    errors.append(
                        f"{where}.payload.context.sovereign_lies.record_selector.record_url invalid: {exc}"
                    )

    return errors
