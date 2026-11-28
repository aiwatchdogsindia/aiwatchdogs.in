"""P10: Innocent But Locked Up — deterministic audit engine for post-acquittal detention overruns & contempt.

Hook: "The court declared them acquitted and ordered release. Why did the prison keep them locked up until contempt was filed?"

Reads checksummed official Indian court orders (Supreme Court & High Courts), prison/jail records,
police/ICJS compliance records, NHRC/SHRC orders, NALSA/SLSA/UTRC reports, Digital Sansad parliamentary
records, Union/State ministry advisories, and CAG audit reports (2021–2026) across PDF, HTML, JSON,
JSONL, CSV, TXT, ZIP, and TAR formats — single-document or paired two-document cross-checks.

Verifies SHA-256 digests via the loader, verifies verbatim documentary evidence tokens inside the
downloaded official bytes (failing closed as ``unevaluable`` if bytes are unreadable, selectors miss,
chronology is inconsistent, or required evidence tokens are absent), tracks the 4-stage custody chain:

1. Stage 1 — Acquittal / Discharge / Release Order (``acquittal_order_date``, ``acquittal_court``,
   ``acquittal_case_number``, ``acquittal_quote``)
2. Stage 2 — Continued Prison Detention / Non-Release (``prison_name``, ``custody_authority``,
   ``non_release_reason``, ``overrun_days``, ``overrun_window``)
3. Stage 3 — Contempt of Court / Habeas Corpus Contempt Action (``contempt_case_number``,
   ``contempt_court``, ``contempt_filing_date``, ``contempt_order_date``, ``contempt_quote``)
4. Stage 4 — Subsequent Release & Remedial Outcome (``release_date``, ``release_verified``,
   ``compensation_inr``, ``judicial_direction_summary``)

Deterministically evaluates five flagged custody-overrun mechanisms plus one Cleared benchmark:
- ``P10-POST-ACQUITTAL-CONTEMPT-OVERRUN`` (``post_acquittal_contempt_overrun``)
- ``P10-RELEASE-ORDER-CONTEMPT-OVERRUN`` (``bail_or_release_order_contempt``)
- ``P10-WARRANT-TRANSMISSION-DELAY`` (``warrant_or_faster_transmission_delay``)
- ``P10-STATUTORY-UNLAWFUL-DETENTION`` (``statutory_commission_detention_finding``)
- ``P10-INSTITUTIONAL-COMPLIANCE-GAP`` (``institutional_compliance_gap``)
- ``Cleared`` (``timely_release_or_compliant``) — published with equal journalistic standing when
  verified primary records confirm timely release or court-verified compliance without unlawful overrun.

Strict BNS Sec 356 fair-reporting neutrality and DPDP Act 2023 PII scrubbing are enforced on every
emitted field and quoted official excerpt.
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

INNOCENT_BUT_LOCKED_UP_PROJECT_SLUG = "innocent-but-locked-up"
INNOCENT_BUT_LOCKED_UP_METHODOLOGY = "P10"
P10_DEFAULT_HOOK = (
    "The court declared them acquitted and ordered release. "
    "Why did the prison keep them locked up until contempt was filed?"
)

PORTAL_CATEGORY_ID_CODES: dict[str, str] = {
    "court_portal": "court",
    "prison_portal": "prison",
    "police_portal": "police",
    "nhrc_shrc": "nhrc",
    "nalsa_slsa": "nalsa",
    "parliament": "parl",
    "govt_ministry": "govt",
    "cag_audit": "cag",
}
ID_CODE_TO_PORTAL_CATEGORY: dict[str, str] = {v: k for k, v in PORTAL_CATEGORY_ID_CODES.items()}

# Canonical P10 task ID format: p10-<portal_code>-<YYYY>-<slug>-<4-digit-seq>
P10_TASK_ID_RE = re.compile(
    r"^p10-(court|prison|police|nhrc|nalsa|parl|govt|cag)-(202[1-6])-[a-z0-9]+(?:-[a-z0-9]+)*-\d{4}$"
)

PORTAL_CATEGORY_LABELS: dict[str, str] = {
    "court_portal": "Court Portals (SCI & High Courts)",
    "prison_portal": "Prison & Correctional Portals",
    "police_portal": "Police & ICJS Portals",
    "nhrc_shrc": "NHRC & State Human Rights Commissions",
    "nalsa_slsa": "NALSA, SLSAs & UTRCs",
    "parliament": "Parliament (Digital Sansad)",
    "govt_ministry": "Union & State Government Ministries",
    "cag_audit": "CAG Audit Reports",
}
VALID_PORTAL_CATEGORIES: frozenset[str] = frozenset(PORTAL_CATEGORY_LABELS.keys())

COURT_TIER_LABELS: dict[str, str] = {
    "supreme_court": "Supreme Court of India",
    "high_court": "High Court",
    "district_court": "District / Sessions Court",
    "statutory_body": "Statutory / Oversight Body",
}
VALID_COURT_TIERS: frozenset[str] = frozenset(COURT_TIER_LABELS.keys())

OVERRUN_MECHANISM_RULES: dict[str, str] = {
    "post_acquittal_contempt_overrun": "P10-POST-ACQUITTAL-CONTEMPT-OVERRUN",
    "bail_or_release_order_contempt": "P10-RELEASE-ORDER-CONTEMPT-OVERRUN",
    "warrant_or_faster_transmission_delay": "P10-WARRANT-TRANSMISSION-DELAY",
    "statutory_commission_detention_finding": "P10-STATUTORY-UNLAWFUL-DETENTION",
    "institutional_compliance_gap": "P10-INSTITUTIONAL-COMPLIANCE-GAP",
}

SIGNAL_CATEGORIES: tuple[str, ...] = (
    "post_acquittal_contempt_overrun",
    "bail_or_release_order_contempt",
    "warrant_or_faster_transmission_delay",
    "statutory_commission_detention_finding",
    "institutional_compliance_gap",
)

VALID_OVERRUN_MECHANISMS: frozenset[str] = frozenset(SIGNAL_CATEGORIES) | {"timely_release_or_compliant"}

VALID_ACQUITTAL_ORDER_TYPES: frozenset[str] = frozenset(
    {
        "acquittal",
        "discharge",
        "detention_quashed",
        "bail_release_order",
        "conviction_set_aside",
        "institutional_compliance_review",
    }
)

VALID_CHAIN_STATUSES: frozenset[str] = frozenset(
    {
        "full_4_stage_chain",
        "partial_3_stage",
        "institutional_record",
        "compliant_control",
    }
)

VALID_SOURCE_FORMATS: frozenset[str] = frozenset({"pdf", "html", "json", "jsonl", "csv", "txt", "zip", "tar"})

VALID_SECONDARY_ROLES: frozenset[str] = frozenset(
    {
        "",
        "contempt_order_document",
        "acquittal_order_document",
        "release_or_compliance_record",
        "corroborating_record",
    }
)

MIN_YEAR = 2021
MAX_YEAR = 2026
MIN_READABLE_DOC_CHARS = 40
MIN_EVIDENCE_TOKENS = 2
MAX_EVIDENCE_TOKENS = 10
LONG_OVERRUN_DAYS = 7
SEVERE_OVERRUN_DAYS = 30
EXTREME_OVERRUN_DAYS = 90
HIGH_COMPENSATION_INR = 100_000.0  # INR 1 Lakh
MAJOR_COMPENSATION_INR = 500_000.0  # INR 5 Lakh

_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,58}$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CTRL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]+")
_MULTI_SPACE_RE = re.compile(r"\s+")
_NON_ALNUM_SPACE_RE = re.compile(r"[^a-z0-9\s]+")
_DASH_VARIANTS_RE = re.compile(r"[\u2010-\u2015\u2212]")
_QUOTE_SINGLE_RE = re.compile(r"[\u2018\u2019\u201a\u201b]")
_QUOTE_DOUBLE_RE = re.compile(r"[\u201c\u201d\u201e\u201f]")

# BNS Sec 356 neutralizers for quoted third-party judicial & official text.
# Convert legal phrases containing "guilty" into strictly neutral equivalents BEFORE general neutralization.
_NOT_GUILTY_RE = re.compile(r"(?<![a-z])not[\s_-]+guilty(?![a-z])", re.IGNORECASE)
_HELD_GUILTY_CONTEMPT_RE = re.compile(
    r"(?<![a-z])(?:held|found)[\s_]+guilty[\s_]+of[\s_]+contempt(?![a-z])",
    re.IGNORECASE,
)
_ACB_RE = re.compile(r"\banti[\s-]+corruption\b", re.IGNORECASE)
_NEUTRAL_REPLACE_RE = re.compile(
    r"(?<![a-z])(?:scam(?:s|med|mer|mers)?|corrupt(?:ed|ing|ly|ion)?|brib(?:e|es|ed|ing|ery)|"
    r"fraud(?:s|ulent|ulently|ster|sters)?|kickback(?:s)?|guilty|stolen|rigged|"
    r"crook(?:s|ed)?|loot(?:s|ed|ing|er|ers)?|embezzl(?:e|es|ed|ing|ement))(?![a-z])",
    re.IGNORECASE,
)

_MECHANISM_DISPLAY: dict[str, str] = {
    "post_acquittal_contempt_overrun": "Post-Acquittal Detention Overrun & Contempt Proceeding",
    "bail_or_release_order_contempt": "Post-Release-Order Custody Overrun & Contempt Action",
    "warrant_or_faster_transmission_delay": "Release Warrant / FASTER Transmission Delay",
    "statutory_commission_detention_finding": "Statutory Commission Unlawful-Detention Finding",
    "institutional_compliance_gap": "Institutional Custody-Release Compliance Gap",
    "timely_release_or_compliant": "Timely Release / Verified Compliance",
}

_BENIGN_EXPLANATIONS: tuple[str, ...] = (
    "Continued custody after an acquittal or release order in one criminal case can lawfully occur when the "
    "prisoner is simultaneously remanded, convicted, or subject to a valid production warrant in another pending case.",
    "Physical release delays may reflect bona fide administrative verification of identity, surety bonds, "
    "certified-copy receipt, or court-to-prison registry transmission gaps prior to full FASTER electronic rollout.",
    "Clerical mismatches in statutory section numbers, party spellings, or warrant particulars in judicial release "
    "orders (parwanas) may cause prison superintendents to seek clarification from the issuing court before release.",
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
    best_doc_role: str
    best_locator: str
    best_page: int | None
    excerpt: str


def _strip_invisible_and_nfkc(text: str) -> str:
    """Apply NFKC normalization and strip invisible Unicode Cf/Cc chars (e.g. soft hyphens, ZWSP, BOM)."""
    normalized = unicodedata.normalize("NFKC", text or "")
    return "".join(
        ch for ch in normalized if unicodedata.category(ch) not in {"Cf", "Cc"} or ch in ("\n", "\t", " ")
    )


def neutral_text(text: str) -> str:
    """Neutralize BNS Sec 356 prohibited words in quoted third-party judicial/official text."""
    cleaned = _strip_invisible_and_nfkc(text or "")
    cleaned = _NOT_GUILTY_RE.sub("acquitted", cleaned)
    cleaned = _HELD_GUILTY_CONTEMPT_RE.sub("held in contempt", cleaned)
    cleaned = _ACB_RE.sub("Vigilance", cleaned)
    return _NEUTRAL_REPLACE_RE.sub("compliance-review", cleaned)


def clean_custody_text(text: Any, *, max_len: int | None = None) -> str:
    """Normalize NFKC, strip invisible Cf/Cc chars, collapse whitespace, scrub PII, and apply BNS Sec 356 neutralization."""
    raw = _strip_invisible_and_nfkc(str(text or ""))
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
    """Normalize a court, prison, or authority name into a lowercase hyphenated slug."""
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
    """Normalize Unicode, strip invisible Cf/Cc chars, normalize dashes/quotes/whitespace/case."""
    norm = _strip_invisible_and_nfkc(text or "")
    norm = _DASH_VARIANTS_RE.sub("-", norm)
    norm = _QUOTE_SINGLE_RE.sub("'", norm)
    norm = _QUOTE_DOUBLE_RE.sub('"', norm)
    norm = _CTRL_CHARS_RE.sub(" ", norm)
    norm = _MULTI_SPACE_RE.sub(" ", norm).strip().lower()
    return norm


def _format_inr_brief(amount: float | None) -> str:
    if amount is None or not math.isfinite(amount) or amount <= 0:
        return "none recorded"
    if amount >= 10_000_000:
        return f"INR {amount / 10_000_000:,.2f} Cr"
    if amount >= 100_000:
        return f"INR {amount / 100_000:,.2f} L"
    return f"INR {amount:,.0f}"


def _indian_fy_from_date(dt: date | None, fallback_year: int | None = None) -> str:
    if dt is not None:
        fy_start = dt.year if dt.month >= 4 else dt.year - 1
        return f"FY{fy_start}-{(fy_start + 1) % 100:02d}"
    if fallback_year is not None and MIN_YEAR <= fallback_year <= MAX_YEAR:
        return f"FY{fallback_year}-{(fallback_year + 1) % 100:02d}"
    return "Unspecified"


def _parse_optional_nonneg_float(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed < 0:
        return None
    return round(parsed, 2)


def _parse_optional_nonneg_int(value: Any) -> int | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        if math.isfinite(value) and value >= 0 and value.is_integer():
            return int(value)
        return None
    try:
        parsed = int(str(value).strip())
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


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
            "archive_member",
            "jurisdiction",
        }
        parts = []
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


def _apply_archive_member_filter(
    pool: list[dict[str, Any]],
    archive_member: str,
) -> tuple[list[dict[str, Any]], bool]:
    """Filter records to a specific archive member when records carry ``archive_member`` metadata."""
    target = archive_member.strip().replace("\\", "/").lstrip("./")
    if not target:
        return pool, False
    tagged = [r for r in pool if isinstance(r.get("archive_member"), str) and r["archive_member"].strip()]
    if not tagged:
        return pool, False
    matched = [
        r
        for r in tagged
        if r["archive_member"].strip().replace("\\", "/").lstrip("./") == target
        or r["archive_member"].strip().replace("\\", "/").endswith("/" + target)
    ]
    if not matched:
        return [], True
    return matched, False


def _apply_record_selector(
    pool: list[dict[str, Any]],
    selector: dict[str, Any],
) -> tuple[list[dict[str, Any]], bool]:
    """Filter structured JSON/CSV rows using ``record_selector.match_field`` / ``match_value``."""
    match_field = str(selector.get("match_field") or "").strip()
    match_value = str(selector.get("match_value") or "").strip()
    if not match_field or not match_value:
        return pool, False

    structured_rows = [r for r in pool if not _is_page_text_record(r)]
    if not structured_rows:
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
        member = str(rec.get("archive_member") or "").strip()
        if page_num is not None:
            fmt = str(rec.get("source_format") or "").strip().lower()
            base_loc = "html_body" if fmt == "html" and page_num == 1 else f"page={page_num}"
        else:
            row_idx = rec.get("source_row_index")
            row_num = int(row_idx) if isinstance(row_idx, int) and not isinstance(row_idx, bool) and row_idx > 0 else idx
            base_loc = f"row={row_num}"
        locator = f"{member}:{base_loc}" if member else base_loc
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
    """Extract a clean BNS-neutral, PII-scrubbed verbatim context window around ``token``."""
    compact_raw = _MULTI_SPACE_RE.sub(" ", _CTRL_CHARS_RE.sub(" ", raw_text or "")).strip()
    if not compact_raw:
        return ""
    norm_raw = _norm_search(compact_raw)
    norm_tok = _norm_search(token)
    pos = norm_raw.find(norm_tok) if norm_tok else -1
    if pos == -1:
        return clean_custody_text(compact_raw[:window_chars], max_len=window_chars)

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
    return clean_custody_text(snippet, max_len=window_chars + 16)


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
            best_doc_role="",
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
            best_doc_role="",
            best_locator="",
            best_page=None,
            excerpt="",
        )

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
        best_doc_role=best_seg.doc_role,
        best_locator=best_seg.locator,
        best_page=best_seg.page,
        excerpt=excerpt,
    )


def evaluate_custody_overrun_rule(
    *,
    overrun_mechanism: str,
    stage_1_acquittal_verified: bool,
    stage_2_continued_detention_verified: bool,
    stage_3_contempt_verified: bool,
    stage_4_release_verified: bool,
    overrun_days: int | None,
    compensation_inr: float | None,
    is_anchor_case: bool,
) -> tuple[bool, str, str, list[str], str, int, str]:
    """Deterministically evaluate P10 mechanism, rules, signal category, severity, and chain completeness.

    Returns ``(flagged, rule_triggered, signal_category, triggered_rules, severity, stages_verified_count, chain_status)``.
    """
    stages_verified_count = sum(
        1
        for flag in (
            stage_1_acquittal_verified,
            stage_2_continued_detention_verified,
            stage_3_contempt_verified,
            stage_4_release_verified,
        )
        if flag
    )

    if overrun_mechanism == "timely_release_or_compliant":
        return False, "", "", [], "low", stages_verified_count, "compliant_control"

    rule_triggered = OVERRUN_MECHANISM_RULES.get(overrun_mechanism, "")
    if not rule_triggered:
        return False, "", "", [], "low", stages_verified_count, "institutional_record"

    if stages_verified_count == 4:
        chain_status = "full_4_stage_chain"
    elif stages_verified_count == 3:
        chain_status = "partial_3_stage"
    else:
        chain_status = "institutional_record"

    signal_category = overrun_mechanism
    triggered_rules = [rule_triggered]

    if stages_verified_count == 4:
        triggered_rules.append("P10-FULL-4-STAGE-CHAIN")
    if stage_3_contempt_verified:
        triggered_rules.append("P10-CONTEMPT-ACTION-VERIFIED")
    if overrun_days is not None:
        if overrun_days >= EXTREME_OVERRUN_DAYS:
            triggered_rules.append("P10-OVERRUN-GE-90D")
        elif overrun_days >= SEVERE_OVERRUN_DAYS:
            triggered_rules.append("P10-OVERRUN-GE-30D")
        elif overrun_days >= LONG_OVERRUN_DAYS:
            triggered_rules.append("P10-OVERRUN-GE-7D")
    comp = compensation_inr if (compensation_inr is not None and compensation_inr > 0) else 0.0
    if comp > 0:
        triggered_rules.append("P10-COMPENSATION-ORDERED")
    if is_anchor_case:
        triggered_rules.append("P10-FLAGSHIP-ANCHOR-CASE")

    if (
        is_anchor_case
        or (
            overrun_mechanism == "post_acquittal_contempt_overrun"
            and stage_1_acquittal_verified
            and stage_2_continued_detention_verified
            and stage_3_contempt_verified
        )
        or (overrun_days is not None and overrun_days >= SEVERE_OVERRUN_DAYS)
        or comp >= MAJOR_COMPENSATION_INR
    ):
        severity = "critical"
    elif (
        overrun_mechanism
        in {
            "post_acquittal_contempt_overrun",
            "bail_or_release_order_contempt",
            "statutory_commission_detention_finding",
        }
        or stage_3_contempt_verified
        or (overrun_days is not None and overrun_days >= LONG_OVERRUN_DAYS)
        or comp >= HIGH_COMPENSATION_INR
    ):
        severity = "high"
    else:
        severity = "medium"

    return True, rule_triggered, signal_category, triggered_rules, severity, stages_verified_count, chain_status


def run_p10_innocent_but_locked_up(
    records: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
) -> tuple[list[AuditLeadCandidate], int, float, str, dict[str, Any]]:
    """Run deterministic, fail-closed P10 post-acquittal detention overrun & contempt verification."""
    ctx = context or {}
    spec = ctx.get("innocent_but_locked_up") if isinstance(ctx.get("innocent_but_locked_up"), dict) else {}
    as_of_iso = str(ctx.get("as_of_date") or "").strip()[:10] or datetime.now(timezone.utc).date().isoformat()

    meta: dict[str, Any] = {
        "as_of_date": as_of_iso,
        "amount_type": "custody_overrun_and_contempt",
        "unevaluable_count": 0,
    }

    case_key = str(spec.get("case_key") or "").strip()
    if not records or not spec or not case_key:
        meta.update({"verdict": "unevaluable", "unevaluable_count": 1, "reason": "missing_records_or_spec"})
        return (
            [],
            0,
            0.0,
            "P10 unevaluable: missing official document payload or innocent_but_locked_up specification.",
            meta,
        )

    overrun_mechanism = str(spec.get("overrun_mechanism") or "").strip().lower()
    portal_category = str(spec.get("portal_category") or "").strip().lower()
    court_tier = str(spec.get("court_tier") or "").strip().lower()
    if (
        overrun_mechanism not in VALID_OVERRUN_MECHANISMS
        or portal_category not in VALID_PORTAL_CATEGORIES
        or court_tier not in VALID_COURT_TIERS
    ):
        meta.update(
            {
                "verdict": "unevaluable",
                "unevaluable_count": 1,
                "reason": "invalid_taxonomy",
                "case_key": case_key,
            }
        )
        return (
            [],
            0,
            0.0,
            f"P10 unevaluable ({case_key}): unrecognized overrun_mechanism, portal_category, or court_tier.",
            meta,
        )

    custody_tokens = [
        str(t).strip()
        for t in (spec.get("custody_order_evidence_tokens") or [])
        if isinstance(t, str) and str(t).strip()
    ]
    contempt_tokens = [
        str(t).strip()
        for t in (spec.get("contempt_or_outcome_evidence_tokens") or [])
        if isinstance(t, str) and str(t).strip()
    ]
    if len(custody_tokens) < MIN_EVIDENCE_TOKENS or len(contempt_tokens) < MIN_EVIDENCE_TOKENS:
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
            f"P10 unevaluable ({case_key}): at least {MIN_EVIDENCE_TOKENS} custody-order and contempt/outcome evidence tokens are required.",
            meta,
        )

    # Parse dates & verify chronological consistency (fail closed on impossible timelines)
    acq_dt = parse_iso_date(spec.get("acquittal_order_date"))
    contempt_filing_dt = parse_iso_date(spec.get("contempt_filing_date"))
    contempt_order_dt = parse_iso_date(spec.get("contempt_order_date"))
    release_dt = parse_iso_date(spec.get("release_date"))
    doc_dt = parse_iso_date(spec.get("document_date"))

    if acq_dt is not None and release_dt is not None and release_dt < acq_dt:
        meta.update(
            {
                "verdict": "unevaluable",
                "unevaluable_count": 1,
                "reason": "chronology_inconsistent",
                "case_key": case_key,
            }
        )
        return (
            [],
            0,
            0.0,
            f"P10 unevaluable ({case_key}): release_date precedes acquittal_order_date.",
            meta,
        )
    if contempt_filing_dt is not None and contempt_order_dt is not None and contempt_order_dt < contempt_filing_dt:
        meta.update(
            {
                "verdict": "unevaluable",
                "unevaluable_count": 1,
                "reason": "chronology_inconsistent",
                "case_key": case_key,
            }
        )
        return (
            [],
            0,
            0.0,
            f"P10 unevaluable ({case_key}): contempt_order_date precedes contempt_filing_date.",
            meta,
        )

    stage_1 = bool(spec.get("stage_1_acquittal_verified"))
    stage_2 = bool(spec.get("stage_2_continued_detention_verified"))
    stage_3 = bool(spec.get("stage_3_contempt_verified"))
    stage_4 = bool(spec.get("stage_4_release_verified"))
    release_verified = bool(spec.get("release_verified")) or stage_4

    overrun_days = _parse_optional_nonneg_int(spec.get("overrun_days"))
    if overrun_days is None and acq_dt is not None and release_dt is not None:
        overrun_days = max(0, (release_dt - acq_dt).days)

    if overrun_mechanism == "timely_release_or_compliant" and stage_2 and (overrun_days or 0) > 1:
        meta.update(
            {
                "verdict": "unevaluable",
                "unevaluable_count": 1,
                "reason": "contradictory_compliant_spec",
                "case_key": case_key,
            }
        )
        return (
            [],
            0,
            0.0,
            f"P10 unevaluable ({case_key}): timely_release_or_compliant conflicts with continued detention overrun > 1 day.",
            meta,
        )

    primary_records, secondary_records, sec_sha_from_recs = _split_primary_and_secondary(records)
    selector = spec.get("record_selector") if isinstance(spec.get("record_selector"), dict) else {}
    secondary_role = str(spec.get("secondary_role") or "").strip().lower()
    if secondary_role not in VALID_SECONDARY_ROLES:
        secondary_role = "contempt_order_document" if secondary_records else ""

    # Apply optional archive_member filter when archive records carry member metadata
    archive_member = str(selector.get("archive_member") or "").strip()
    if archive_member:
        filtered_pri, pri_arc_fail = _apply_archive_member_filter(primary_records, archive_member)
        if not pri_arc_fail:
            primary_records = filtered_pri
        elif secondary_records:
            filtered_sec, sec_arc_fail = _apply_archive_member_filter(secondary_records, archive_member)
            if not sec_arc_fail:
                secondary_records = filtered_sec
            else:
                meta.update(
                    {
                        "verdict": "unevaluable",
                        "unevaluable_count": 1,
                        "reason": "archive_member_not_found",
                        "case_key": case_key,
                    }
                )
                return (
                    [],
                    0,
                    0.0,
                    f"P10 unevaluable ({case_key}): archive_member '{archive_member}' not found in verified archive.",
                    meta,
                )
        else:
            meta.update(
                {
                    "verdict": "unevaluable",
                    "unevaluable_count": 1,
                    "reason": "archive_member_not_found",
                    "case_key": case_key,
                }
            )
            return (
                [],
                0,
                0.0,
                f"P10 unevaluable ({case_key}): archive_member '{archive_member}' not found in verified archive.",
                meta,
            )

    secondary_archive_member = str(selector.get("secondary_archive_member") or "").strip()
    if secondary_archive_member:
        if not secondary_records:
            meta.update(
                {
                    "verdict": "unevaluable",
                    "unevaluable_count": 1,
                    "reason": "secondary_archive_member_not_found",
                    "case_key": case_key,
                }
            )
            return (
                [],
                0,
                0.0,
                f"P10 unevaluable ({case_key}): secondary_archive_member '{secondary_archive_member}' requested without secondary records.",
                meta,
            )
        filtered_sec_arc, sec_arc_only_fail = _apply_archive_member_filter(secondary_records, secondary_archive_member)
        if sec_arc_only_fail:
            meta.update(
                {
                    "verdict": "unevaluable",
                    "unevaluable_count": 1,
                    "reason": "secondary_archive_member_not_found",
                    "case_key": case_key,
                }
            )
            return (
                [],
                0,
                0.0,
                f"P10 unevaluable ({case_key}): secondary_archive_member '{secondary_archive_member}' not found in secondary archive.",
                meta,
            )
        secondary_records = filtered_sec_arc

    # Apply structured record selector when match_field + match_value are specified.
    if selector.get("match_field") and selector.get("match_value"):
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
                    f"P10 unevaluable ({case_key}): record_selector matched 0 rows in verified document bytes.",
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
                f"P10 unevaluable ({case_key}): record_selector matched 0 rows in verified document bytes.",
                meta,
            )

    primary_segments = _build_segments(primary_records, "primary")
    secondary_segments = _build_segments(secondary_records, "secondary")

    retrieval_url = str(ctx.get("retrieval_url") or ctx.get("dataset_url") or "").strip()
    secondary_url = str(ctx.get("secondary_url") or ctx.get("secondary_dataset_url") or "").strip()
    has_secondary = bool(secondary_records) or (bool(secondary_url) and secondary_url != retrieval_url)

    order_page_hint = selector.get("order_page") if isinstance(selector.get("order_page"), int) else None
    contempt_page_hint = selector.get("contempt_page") if isinstance(selector.get("contempt_page"), int) else None

    if has_secondary and secondary_role == "acquittal_order_document":
        custody_segments = secondary_segments
        contempt_segments = primary_segments
    elif has_secondary and secondary_role in {"", "contempt_order_document", "release_or_compliance_record"}:
        custody_segments = primary_segments
        contempt_segments = secondary_segments
    elif has_secondary and secondary_role == "corroborating_record":
        custody_segments = primary_segments + secondary_segments
        contempt_segments = primary_segments + secondary_segments
    else:
        custody_segments = primary_segments
        contempt_segments = primary_segments

    custody_ver = _verify_evidence_tokens(custody_tokens, custody_segments, preferred_page=order_page_hint)
    contempt_ver = _verify_evidence_tokens(contempt_tokens, contempt_segments, preferred_page=contempt_page_hint)
    custody_uses_secondary = custody_ver.best_doc_role == "secondary"
    contempt_uses_secondary = contempt_ver.best_doc_role == "secondary"

    if custody_ver.total_chars < MIN_READABLE_DOC_CHARS or contempt_ver.total_chars < MIN_READABLE_DOC_CHARS:
        meta.update(
            {
                "verdict": "unevaluable",
                "unevaluable_count": 1,
                "reason": "unreadable_or_empty_document",
                "case_key": case_key,
                "custody_chars": custody_ver.total_chars,
                "contempt_chars": contempt_ver.total_chars,
            }
        )
        return (
            [],
            0,
            0.0,
            f"P10 unevaluable ({case_key}): official document lacks machine-readable text (<{MIN_READABLE_DOC_CHARS} chars).",
            meta,
        )

    if not custody_ver.all_matched or not contempt_ver.all_matched:
        meta.update(
            {
                "verdict": "unevaluable",
                "unevaluable_count": 1,
                "reason": "evidence_tokens_not_verified",
                "case_key": case_key,
                "missing_custody_tokens": custody_ver.missing_tokens,
                "missing_contempt_tokens": contempt_ver.missing_tokens,
            }
        )
        return (
            [],
            0,
            0.0,
            (
                f"P10 unevaluable ({case_key}): downloaded official bytes did not verify all required evidence tokens "
                f"(missing custody_order={len(custody_ver.missing_tokens)}, missing contempt_or_outcome={len(contempt_ver.missing_tokens)})."
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
                f"P10 unevaluable ({case_key}): corroborating primary or secondary document lacks readable text.",
                meta,
            )
        all_tokens = custody_tokens + contempt_tokens
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
                f"P10 unevaluable ({case_key}): corroborating primary/secondary document lacked evidence token overlap.",
                meta,
            )

    # Clean & neutralize metadata fields
    case_title = clean_custody_text(spec.get("case_title"), max_len=200) or "Custody Release Compliance Record"
    state_ut_raw = spec.get("state_ut")
    state_ut = (
        clean_custody_text(state_ut_raw, max_len=80) or None
        if state_ut_raw not in (None, "")
        else None
    )
    jurisdiction = clean_custody_text(spec.get("jurisdiction") or ctx.get("jurisdiction"), max_len=120)
    if not jurisdiction:
        jurisdiction = f"State:{state_ut}" if state_ut else "Union of India"

    year_raw = spec.get("year")
    year = (
        int(year_raw)
        if isinstance(year_raw, int) and not isinstance(year_raw, bool) and MIN_YEAR <= year_raw <= MAX_YEAR
        else (doc_dt.year if doc_dt is not None else (contempt_order_dt.year if contempt_order_dt is not None else 2025))
    )
    document_date_iso = doc_dt.isoformat() if doc_dt else (str(spec.get("document_date") or "").strip() or None)
    document_ref = clean_custody_text(spec.get("document_ref"), max_len=200) or case_title

    acquittal_order_date_iso = acq_dt.isoformat() if acq_dt else (str(spec.get("acquittal_order_date") or "").strip() or None)
    acquittal_court = clean_custody_text(spec.get("acquittal_court"), max_len=200) or "Competent Court"
    acquittal_case_number = clean_custody_text(spec.get("acquittal_case_number"), max_len=140) or document_ref[:120]
    acquittal_order_type = str(spec.get("acquittal_order_type") or "acquittal").strip().lower()
    if acquittal_order_type not in VALID_ACQUITTAL_ORDER_TYPES:
        acquittal_order_type = "acquittal"
    acquittal_quote = clean_custody_text(spec.get("acquittal_quote"), max_len=450)

    prison_name = clean_custody_text(spec.get("prison_name"), max_len=180) or "Prison / Correctional Authority"
    custody_authority = (
        clean_custody_text(spec.get("custody_authority") or ctx.get("official_publisher"), max_len=200)
        or prison_name
    )
    non_release_reason = clean_custody_text(spec.get("non_release_reason"), max_len=360)
    overrun_window = clean_custody_text(spec.get("overrun_window"), max_len=160)
    if not overrun_window:
        if acquittal_order_date_iso and release_dt is not None and overrun_days is not None:
            overrun_window = f"{acquittal_order_date_iso} to {release_dt.isoformat()} ({overrun_days} days)"
        elif overrun_days is not None:
            overrun_window = f"{overrun_days} days documented"
        else:
            overrun_window = f"CY {year} compliance record"

    contempt_case_number = clean_custody_text(spec.get("contempt_case_number"), max_len=140) or None
    contempt_court = clean_custody_text(spec.get("contempt_court"), max_len=200) or None
    contempt_filing_date_iso = (
        contempt_filing_dt.isoformat()
        if contempt_filing_dt
        else (str(spec.get("contempt_filing_date") or "").strip() or None)
    )
    contempt_order_date_iso = (
        contempt_order_dt.isoformat()
        if contempt_order_dt
        else (str(spec.get("contempt_order_date") or "").strip() or None)
    )
    contempt_quote = clean_custody_text(spec.get("contempt_quote"), max_len=450) or None

    release_date_iso = release_dt.isoformat() if release_dt else (str(spec.get("release_date") or "").strip() or None)
    compensation_inr = _parse_optional_nonneg_float(spec.get("compensation_inr"))
    audited_inr = float(compensation_inr) if compensation_inr is not None else 0.0
    judicial_direction_summary = clean_custody_text(spec.get("judicial_direction_summary"), max_len=600)
    statutory_anchor = (
        clean_custody_text(spec.get("statutory_anchor"), max_len=240)
        or "Constitution of India Art. 21; Contempt of Courts Act 1971 Sec. 2(b)/12; SC FASTER Guidelines"
    )
    is_anchor_case = bool(spec.get("is_anchor_case"))

    (
        flagged,
        rule_triggered,
        signal_category,
        triggered_rules,
        severity,
        stages_verified_count,
        chain_status,
    ) = evaluate_custody_overrun_rule(
        overrun_mechanism=overrun_mechanism,
        stage_1_acquittal_verified=stage_1,
        stage_2_continued_detention_verified=stage_2,
        stage_3_contempt_verified=stage_3,
        stage_4_release_verified=stage_4,
        overrun_days=overrun_days,
        compensation_inr=compensation_inr,
        is_anchor_case=is_anchor_case,
    )

    safe_custody_tokens = [clean_custody_text(t, max_len=120) for t in custody_ver.matched_tokens]
    safe_contempt_tokens = [clean_custody_text(t, max_len=120) for t in contempt_ver.matched_tokens]

    if not flagged:
        cleared_summary = clean_custody_text(
            f"P10 cleared ({case_key}, timely_release_or_compliant): {case_title} ({jurisdiction}, {year}) — "
            f"primary record ({document_ref}) verified in official bytes with compliant custody release / benchmark outcome: "
            f"{judicial_direction_summary}",
            max_len=1200,
        )
        meta.update(
            {
                "verdict": "cleared_timely_release_or_compliant",
                "case_key": case_key,
                "overrun_mechanism": overrun_mechanism,
                "portal_category": portal_category,
                "court_tier": court_tier,
                "chain_status": chain_status,
                "stages_verified_count": stages_verified_count,
                "custody_order_tokens_matched": safe_custody_tokens,
                "contempt_or_outcome_tokens_matched": safe_contempt_tokens,
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
    official_publisher = clean_custody_text(ctx.get("official_publisher") or acquittal_court, max_len=200)

    custody_url = (secondary_url if custody_uses_secondary and secondary_url else retrieval_url) or landing_url
    custody_sha = expected_sec_sha if custody_uses_secondary else expected_sha
    contempt_url = (secondary_url if contempt_uses_secondary and secondary_url else retrieval_url) or landing_url
    contempt_sha = expected_sec_sha if contempt_uses_secondary else expected_sha

    section_hint = clean_custody_text(selector.get("section_hint"), max_len=120)
    custody_locator = custody_ver.best_locator + (f"; {section_hint}" if section_hint else "")
    contempt_locator = contempt_ver.best_locator + (f"; {section_hint}" if section_hint else "")
    source_locator = f"order={custody_locator} | outcome={contempt_locator}"

    mech_label = _MECHANISM_DISPLAY.get(overrun_mechanism, "Post-Acquittal Custody Overrun Observation")
    overrun_tag = f" (+{overrun_days}d)" if overrun_days is not None and overrun_days > 0 else ""
    lead_title = clean_custody_text(
        f"Custody Release Compliance Observation ({mech_label}){overrun_tag}: {case_title}",
        max_len=195,
    )

    stage1_clause = (
        f"Stage 1 (Order): {acquittal_court} ({acquittal_case_number}, {acquittal_order_date_iso or year}) — {acquittal_quote}"
    )
    stage2_clause = (
        f" Stage 2 (Detention): {prison_name} / {custody_authority}; overrun window: {overrun_window}"
        + (f"; recorded reason: {non_release_reason}" if non_release_reason else "")
        + "."
    )
    stage3_clause = (
        f" Stage 3 (Contempt/Action): {contempt_court or acquittal_court} "
        f"({contempt_case_number or document_ref}, order {contempt_order_date_iso or document_date_iso or year}) — "
        f"{contempt_quote or judicial_direction_summary}"
        if stage_3 or contempt_case_number or contempt_quote
        else ""
    )
    comp_clause = (
        f" Ordered compensation: {_format_inr_brief(compensation_inr)}."
        if compensation_inr is not None and compensation_inr > 0
        else ""
    )
    stage4_clause = (
        f" Stage 4 (Outcome): release {'verified' if release_verified else 'status pending'}"
        + (f" on {release_date_iso}" if release_date_iso else "")
        + f"; direction: {judicial_direction_summary}{comp_clause}"
    )
    summary = clean_custody_text(
        f"Primary-record custody compliance audit ({jurisdiction}; {PORTAL_CATEGORY_LABELS.get(portal_category, portal_category)}; "
        f"{stages_verified_count}/4 stages verified): {stage1_clause}{stage2_clause}{stage3_clause}{stage4_clause} "
        f"Verified deterministically against checksummed official primary records for public-interest review.",
        max_len=1500,
    )

    benign_list = list(_BENIGN_EXPLANATIONS)
    extra_benign = clean_custody_text(spec.get("benign_explanation_context"), max_len=300)
    if extra_benign:
        benign_list.insert(0, extra_benign)

    fy_primary = _indian_fy_from_date(doc_dt or contempt_order_dt or release_dt or acq_dt, fallback_year=year)
    fy_start = _indian_fy_from_date(acq_dt or doc_dt, fallback_year=year)
    fy_end = _indian_fy_from_date(release_dt or contempt_order_dt or doc_dt, fallback_year=year)

    custody_cit: dict[str, Any] = {
        "role": "acquittal_and_custody_order",
        "title": "Acquittal / Release Order & Custody Record",
        "document": f"{acquittal_case_number} ({acquittal_court})",
        "source_url": custody_url,
        "landing_url": landing_url,
        "source_landing_page": landing_url,
        "official_publisher": official_publisher,
        "source_locator": custody_locator,
        "sha256": custody_sha,
        "excerpt": custody_ver.excerpt or acquittal_quote[:340],
    }
    if custody_ver.best_page is not None:
        custody_cit["page"] = custody_ver.best_page

    contempt_cit: dict[str, Any] = {
        "role": "contempt_and_release_outcome",
        "title": "Contempt / Habeas Corpus Order & Release Outcome",
        "document": f"{contempt_case_number or document_ref} ({contempt_court or acquittal_court})",
        "source_url": contempt_url,
        "landing_url": landing_url,
        "source_landing_page": landing_url,
        "official_publisher": official_publisher,
        "source_locator": contempt_locator,
        "sha256": contempt_sha,
        "excerpt": contempt_ver.excerpt or (contempt_quote or judicial_direction_summary)[:340],
    }
    if contempt_ver.best_page is not None:
        contempt_cit["page"] = contempt_ver.best_page

    benchmark_cit: dict[str, Any] = {
        "role": "statutory_benchmark",
        "title": "Constitutional & Statutory Compliance Anchor",
        "document": statutory_anchor,
        "source_url": landing_url or retrieval_url,
        "landing_url": landing_url or retrieval_url,
        "source_landing_page": landing_url or retrieval_url,
        "official_publisher": official_publisher,
        "source_locator": f"rule={rule_triggered}; mechanism={overrun_mechanism}; chain={chain_status}",
        "sha256": expected_sha,
        "excerpt": clean_custody_text(
            f"{rule_triggered}: {stages_verified_count}/4 stages verified ({chain_status}); "
            f"overrun={overrun_days if overrun_days is not None else 'unquantified'}d; "
            f"verified {len(custody_ver.matched_tokens)} custody-order token(s) and "
            f"{len(contempt_ver.matched_tokens)} contempt/outcome token(s) in official bytes.",
            max_len=360,
        ),
    }

    citations = [custody_cit, contempt_cit, benchmark_cit]

    lead = AuditLeadCandidate(
        title=lead_title,
        summary=summary,
        methodology_code=INNOCENT_BUT_LOCKED_UP_METHODOLOGY,
        entity_name=custody_authority[:160],
        jurisdiction=jurisdiction,
        fiscal_year=fy_primary,
        fiscal_year_start=fy_start,
        fiscal_year_end=fy_end,
        monetary_exposure_inr=round(audited_inr, 2),
        severity=severity,  # type: ignore[arg-type]
        anomaly_metrics={
            "case_key": case_key,
            "case_title": case_title,
            "verdict": "flagged_custody_overrun",
            "signal_category": signal_category,
            "overrun_mechanism": overrun_mechanism,
            "triggered_rules": triggered_rules,
            "portal_category": portal_category,
            "court_tier": court_tier,
            "state_ut": state_ut,
            "jurisdiction": jurisdiction,
            "year": year,
            "document_date": document_date_iso,
            "document_ref": document_ref,
            "is_anchor_case": is_anchor_case,
            "stages_verified_count": stages_verified_count,
            "chain_status": chain_status,
            "stage_1_acquittal_verified": stage_1,
            "acquittal_order_date": acquittal_order_date_iso,
            "acquittal_court": acquittal_court,
            "acquittal_case_number": acquittal_case_number,
            "acquittal_order_type": acquittal_order_type,
            "acquittal_quote": acquittal_quote,
            "stage_2_continued_detention_verified": stage_2,
            "prison_name": prison_name,
            "custody_authority": custody_authority,
            "non_release_reason": non_release_reason,
            "overrun_days": overrun_days,
            "overrun_window": overrun_window,
            "stage_3_contempt_verified": stage_3,
            "contempt_case_number": contempt_case_number,
            "contempt_court": contempt_court,
            "contempt_filing_date": contempt_filing_date_iso,
            "contempt_order_date": contempt_order_date_iso,
            "contempt_quote": contempt_quote,
            "stage_4_release_verified": stage_4,
            "release_date": release_date_iso,
            "release_verified": release_verified,
            "compensation_inr": compensation_inr,
            "judicial_direction_summary": judicial_direction_summary,
            "custody_order_excerpt": custody_ver.excerpt,
            "custody_order_locator": custody_locator,
            "custody_order_tokens_matched": safe_custody_tokens,
            "contempt_or_outcome_excerpt": contempt_ver.excerpt,
            "contempt_or_outcome_locator": contempt_locator,
            "contempt_or_outcome_tokens_matched": safe_contempt_tokens,
            "statutory_anchor": statutory_anchor,
            "secondary_role": secondary_role or None,
            "as_of_date": as_of_iso,
            "source_locator": source_locator,
        },
        rule_triggered=rule_triggered,
        source_locator=source_locator,
        citations=citations,
        benign_explanations=benign_list,
        suggested_authority=custody_authority[:160],
        amount_type="custody_overrun_and_contempt",
    )

    run_summary = (
        f"P10 flagged {case_key} ({case_title}, {jurisdiction}): {rule_triggered} "
        f"({overrun_mechanism}, {stages_verified_count}/4 stages, overrun={overrun_days}d, severity={severity})."
    )
    meta.update(
        {
            "verdict": "flagged_custody_overrun",
            "signal_category": signal_category,
            "rule_triggered": rule_triggered,
            "case_key": case_key,
            "portal_category": portal_category,
            "court_tier": court_tier,
            "chain_status": chain_status,
            "stages_verified_count": stages_verified_count,
            "overrun_days": overrun_days,
            "unevaluable_count": 0,
        }
    )
    return [lead], 0, audited_inr, run_summary, meta


def validate_context(ctx: Any, where: str) -> list[str]:
    """Strictly validate ``payload.context.innocent_but_locked_up`` for a P10 task."""
    errors: list[str] = []
    if not isinstance(ctx, dict):
        return [f"{where}.payload.context.innocent_but_locked_up is required for P10 tasks."]
    spec = ctx.get("innocent_but_locked_up")
    if not isinstance(spec, dict):
        return [f"{where}.payload.context.innocent_but_locked_up must be an object."]

    prefix = f"{where}.payload.context.innocent_but_locked_up"

    as_of = ctx.get("as_of_date")
    if as_of not in (None, "") and (not isinstance(as_of, str) or parse_iso_date(as_of) is None):
        errors.append(f"{where}.payload.context.as_of_date must be a valid YYYY-MM-DD date when provided.")

    ckey = spec.get("case_key")
    if not isinstance(ckey, str) or not _KEY_RE.fullmatch(ckey):
        errors.append(f"{prefix}.case_key must match [a-z0-9-] (<= 59 chars).")

    pcat = spec.get("portal_category")
    if pcat not in VALID_PORTAL_CATEGORIES:
        errors.append(f"{prefix}.portal_category must be one of {sorted(VALID_PORTAL_CATEGORIES)}.")

    ctier = spec.get("court_tier")
    if ctier not in VALID_COURT_TIERS:
        errors.append(f"{prefix}.court_tier must be one of {sorted(VALID_COURT_TIERS)}.")

    mech = spec.get("overrun_mechanism")
    if mech not in VALID_OVERRUN_MECHANISMS:
        errors.append(f"{prefix}.overrun_mechanism must be one of {sorted(VALID_OVERRUN_MECHANISMS)}.")

    year = spec.get("year")
    if isinstance(year, bool) or not isinstance(year, int) or not (MIN_YEAR <= year <= MAX_YEAR):
        errors.append(f"{prefix}.year must be an integer between {MIN_YEAR} and {MAX_YEAR}.")

    jur = spec.get("jurisdiction")
    state_ut = spec.get("state_ut")
    clean_state_ut = state_ut.strip() if isinstance(state_ut, str) else ""
    if not isinstance(jur, str) or not jur.strip() or len(jur) > 120:
        errors.append(f"{prefix}.jurisdiction must be non-empty text (<= 120 chars).")
    elif scan_forbidden_terms(jur):
        errors.append(f"{prefix}.jurisdiction contains prohibited non-neutral terminology.")
    else:
        jur_s = jur.strip()
        if jur_s == "Union of India":
            if state_ut is not None:
                errors.append(f"{prefix}.state_ut must be null when jurisdiction is 'Union of India'.")
        elif jur_s.startswith("State:"):
            rest = jur_s[6:].strip()
            if not rest:
                errors.append(f"{prefix}.jurisdiction must be 'State:<Name>'.")
            elif not clean_state_ut or clean_state_ut != rest:
                errors.append(f"{prefix}.state_ut must match '{rest}' when jurisdiction is '{jur_s}'.")
        elif jur_s.startswith("UT:"):
            rest = jur_s[3:].strip()
            if not rest:
                errors.append(f"{prefix}.jurisdiction must be 'UT:<Name>'.")
            elif not clean_state_ut or clean_state_ut != rest:
                errors.append(f"{prefix}.state_ut must match '{rest}' when jurisdiction is '{jur_s}'.")
        else:
            errors.append(f"{prefix}.jurisdiction must be 'Union of India', 'State:<Name>', or 'UT:<Name>'.")

    if state_ut is not None:
        if not isinstance(state_ut, str) or not clean_state_ut or len(state_ut) > 80:
            errors.append(f"{prefix}.state_ut must be null or non-empty text (<= 80 chars).")
        elif scan_forbidden_terms(clean_state_ut):
            errors.append(f"{prefix}.state_ut contains prohibited non-neutral terminology.")

    required_text_fields: tuple[tuple[str, int], ...] = (
        ("case_title", 200),
        ("document_ref", 200),
        ("acquittal_court", 200),
        ("acquittal_case_number", 140),
        ("acquittal_quote", 450),
        ("prison_name", 180),
        ("custody_authority", 200),
        ("non_release_reason", 360),
        ("overrun_window", 160),
        ("judicial_direction_summary", 600),
    )
    for fname, max_len in required_text_fields:
        val = spec.get(fname)
        if not isinstance(val, str) or not val.strip() or len(val) > max_len:
            errors.append(f"{prefix}.{fname} must be non-empty text (<= {max_len} chars).")
        elif scan_forbidden_terms(val):
            errors.append(f"{prefix}.{fname} contains prohibited non-neutral terminology.")

    optional_text_fields: tuple[tuple[str, int], ...] = (
        ("contempt_case_number", 140),
        ("contempt_court", 200),
        ("contempt_quote", 450),
        ("statutory_anchor", 240),
        ("benign_explanation_context", 300),
    )
    for fname, max_len in optional_text_fields:
        val = spec.get(fname)
        if val not in (None, ""):
            if not isinstance(val, str) or len(val) > max_len:
                errors.append(f"{prefix}.{fname} must be text (<= {max_len} chars).")
            elif scan_forbidden_terms(val):
                errors.append(f"{prefix}.{fname} contains prohibited non-neutral terminology.")

    order_type = spec.get("acquittal_order_type")
    if order_type not in VALID_ACQUITTAL_ORDER_TYPES:
        errors.append(f"{prefix}.acquittal_order_type must be one of {sorted(VALID_ACQUITTAL_ORDER_TYPES)}.")

    for bfield in (
        "is_anchor_case",
        "stage_1_acquittal_verified",
        "stage_2_continued_detention_verified",
        "stage_3_contempt_verified",
        "stage_4_release_verified",
        "release_verified",
    ):
        if not isinstance(spec.get(bfield), bool):
            errors.append(f"{prefix}.{bfield} must be a boolean.")

    doc_date_raw = spec.get("document_date")
    doc_dt = parse_iso_date(doc_date_raw) if isinstance(doc_date_raw, str) else None
    if doc_dt is None or not (date(MIN_YEAR, 1, 1) <= doc_dt <= date(MAX_YEAR, 12, 31)):
        errors.append(f"{prefix}.document_date must be a valid YYYY-MM-DD date ({MIN_YEAR}-01-01 to {MAX_YEAR}-12-31).")

    acq_dt: date | None = None
    acq_raw = spec.get("acquittal_order_date")
    if acq_raw not in (None, ""):
        acq_dt = parse_iso_date(acq_raw) if isinstance(acq_raw, str) else None
        if acq_dt is None or not (date(1990, 1, 1) <= acq_dt <= date(MAX_YEAR, 12, 31)):
            errors.append(f"{prefix}.acquittal_order_date must be null or a valid YYYY-MM-DD date.")

    cf_dt: date | None = None
    cf_raw = spec.get("contempt_filing_date")
    if cf_raw not in (None, ""):
        cf_dt = parse_iso_date(cf_raw) if isinstance(cf_raw, str) else None
        if cf_dt is None or not (date(2000, 1, 1) <= cf_dt <= date(MAX_YEAR, 12, 31)):
            errors.append(f"{prefix}.contempt_filing_date must be null or a valid YYYY-MM-DD date.")

    co_dt: date | None = None
    co_raw = spec.get("contempt_order_date")
    if co_raw not in (None, ""):
        co_dt = parse_iso_date(co_raw) if isinstance(co_raw, str) else None
        if co_dt is None or not (date(2000, 1, 1) <= co_dt <= date(MAX_YEAR, 12, 31)):
            errors.append(f"{prefix}.contempt_order_date must be null or a valid YYYY-MM-DD date.")

    rel_dt: date | None = None
    rel_raw = spec.get("release_date")
    if rel_raw not in (None, ""):
        rel_dt = parse_iso_date(rel_raw) if isinstance(rel_raw, str) else None
        if rel_dt is None or not (date(2000, 1, 1) <= rel_dt <= date(MAX_YEAR, 12, 31)):
            errors.append(f"{prefix}.release_date must be null or a valid YYYY-MM-DD date.")

    if acq_dt is not None and rel_dt is not None and rel_dt < acq_dt:
        errors.append(f"{prefix}.release_date ({rel_dt.isoformat()}) must not precede acquittal_order_date ({acq_dt.isoformat()}).")
    if cf_dt is not None and co_dt is not None and co_dt < cf_dt:
        errors.append(f"{prefix}.contempt_order_date ({co_dt.isoformat()}) must not precede contempt_filing_date ({cf_dt.isoformat()}).")

    overrun_raw = spec.get("overrun_days")
    if overrun_raw is not None:
        if isinstance(overrun_raw, bool) or not isinstance(overrun_raw, int) or overrun_raw < 0 or overrun_raw > 20_000:
            errors.append(f"{prefix}.overrun_days must be null or a non-negative integer (<= 20000).")

    if mech == "timely_release_or_compliant" and spec.get("stage_2_continued_detention_verified") is True and isinstance(overrun_raw, int) and overrun_raw > 1:
        errors.append(f"{prefix} timely_release_or_compliant cannot combine with continued detention overrun > 1 day.")

    comp_raw = spec.get("compensation_inr")
    if comp_raw is not None:
        if (
            isinstance(comp_raw, bool)
            or not isinstance(comp_raw, (int, float))
            or not math.isfinite(float(comp_raw))
            or float(comp_raw) < 0
        ):
            errors.append(f"{prefix}.compensation_inr must be null or a finite non-negative number.")

    for tok_field in ("custody_order_evidence_tokens", "contempt_or_outcome_evidence_tokens"):
        toks = spec.get(tok_field)
        if (
            not isinstance(toks, list)
            or not (MIN_EVIDENCE_TOKENS <= len(toks) <= MAX_EVIDENCE_TOKENS)
            or not all(isinstance(t, str) and 3 <= len(t.strip()) <= 120 for t in toks)
        ):
            errors.append(
                f"{prefix}.{tok_field} must list {MIN_EVIDENCE_TOKENS} to {MAX_EVIDENCE_TOKENS} "
                "verbatim documentary tokens (each 3..120 chars)."
            )
        elif any(scan_forbidden_terms(t) for t in toks):
            errors.append(f"{prefix}.{tok_field} contains prohibited non-neutral terminology.")

    for fmt_key in ("source_format", "secondary_source_format"):
        fmt_val = spec.get(fmt_key)
        if fmt_val not in (None, ""):
            if not isinstance(fmt_val, str) or fmt_val not in VALID_SOURCE_FORMATS:
                errors.append(f"{prefix}.{fmt_key} must be one of {sorted(VALID_SOURCE_FORMATS)}.")

    sec_role = spec.get("secondary_role")
    if sec_role is not None and sec_role not in VALID_SECONDARY_ROLES:
        errors.append(
            f"{prefix}.secondary_role must be one of {sorted(r for r in VALID_SECONDARY_ROLES if r)} or null."
        )

    selector = spec.get("record_selector")
    if selector is not None:
        if not isinstance(selector, dict):
            errors.append(f"{prefix}.record_selector must be an object.")
        else:
            mf = selector.get("match_field")
            mv = selector.get("match_value")
            if bool(mf) != bool(mv):
                errors.append(f"{prefix}.record_selector must provide match_field and match_value together.")
            if mf not in (None, "") and (not isinstance(mf, str) or len(mf) > 80):
                errors.append(f"{prefix}.record_selector.match_field must be <= 80 chars.")
            if mv not in (None, "") and (not isinstance(mv, str) or len(mv) > 160):
                errors.append(f"{prefix}.record_selector.match_value must be <= 160 chars.")
            for mkey in ("archive_member", "secondary_archive_member"):
                amem = selector.get(mkey)
                if amem not in (None, ""):
                    if not isinstance(amem, str) or len(amem) > 260:
                        errors.append(f"{prefix}.record_selector.{mkey} must be <= 260 chars.")
                    else:
                        norm_m = amem.replace("\\", "/")
                        if norm_m.startswith("/") or ".." in norm_m.split("/"):
                            errors.append(f"{prefix}.record_selector.{mkey} must be a safe relative archive path.")
            for pkey in ("order_page", "contempt_page"):
                pval = selector.get(pkey)
                if pval is not None and (isinstance(pval, bool) or not isinstance(pval, int) or not (1 <= pval <= 5000)):
                    errors.append(f"{prefix}.record_selector.{pkey} must be a positive page integer (1..5000).")
            shint = selector.get("section_hint")
            if shint not in (None, ""):
                if not isinstance(shint, str) or len(shint) > 120:
                    errors.append(f"{prefix}.record_selector.section_hint must be <= 120 chars.")
                elif scan_forbidden_terms(shint):
                    errors.append(f"{prefix}.record_selector.section_hint contains prohibited non-neutral terminology.")
            rurl = selector.get("record_url")
            if rurl not in (None, ""):
                try:
                    validate_safe_http_url(str(rurl), require_https=True, allow_private=False)
                except ValueError as exc:
                    errors.append(f"{prefix}.record_selector.record_url invalid: {exc}")

    return errors
