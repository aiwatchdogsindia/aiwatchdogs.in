"""P9: Auditing Government Exams — deterministic audit engine for Indian public examination conduct.

Hook: "Did India's government exams happen on time and flawlessly — or get delayed, postponed,
rescheduled, or cancelled after students sat them?"

Reads checksummed official Union and State/UT public examination documents (PDF, HTML, JSON, CSV,
or TXT — single-document or paired schedule + outcome/disruption notices covering the 3-year audit
window from 2023-11-28 to 2026-11-28), verifies SHA-256 digests, enforces strict word-boundary
normalized verbatim documentary token checks inside >= 40 characters of extracted text (failing
closed as ``unevaluable`` on any missing/unverified bytes, selector miss, or missing token), and
deterministically evaluates:

1. ``P9-CANCELLED-AFTER-CONDUCT`` (``cancelled_after_conduct``) — candidates sat/took the exam and
   the examination was subsequently cancelled.
2. ``P9-POSTPONED`` (``postponed``) — scheduled examination was deferred/postponed prior to conduct.
3. ``P9-RESCHEDULED`` (``rescheduled``) — examination date/shift/centre schedule was revised.
4. ``P9-DELAYED`` (``delayed``) — examination conduct, cycle, or statutory stage was materially delayed.
5. ``P9-CANCELLED-BEFORE-CONDUCT`` (``cancelled_before_conduct``) — notified examination was
   cancelled/withdrawn prior to conduct.
6. ``P9-PARTIAL-REEXAM-OR-GLITCH`` (``partial_reexam_or_glitch``) — technical/CBT server glitch,
   centre-level disruption, or partial re-examination affecting a subset of candidates or shifts.
7. ``Cleared`` (``flawless_conduct``) — audited and confirmed conducted as scheduled without
   documented disruption, published with equal journalistic standing.

Strict BNS Sec 356 neutrality and DPDP Act 2023 PII scrubbing are enforced on every emitted field
and quoted official excerpt, together with mandatory lawful benign explanations on every lead.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from aiwatchdogs.core.models import AuditLeadCandidate
from aiwatchdogs.core.safety import (
    scan_forbidden_terms,
    scrub_pii,
    validate_safe_http_url,
)

AUDITING_GOVERNMENT_EXAMS_PROJECT_SLUG = "auditing-government-exams"
AUDITING_GOVERNMENT_EXAMS_METHODOLOGY = "P9"
P9_DEFAULT_HOOK = (
    "Did India's government exams happen on time and flawlessly — or get delayed, postponed, "
    "rescheduled, or cancelled after students sat them?"
)

P9_AUDIT_WINDOW_START = date(2023, 11, 28)
P9_AUDIT_WINDOW_END = date(2026, 11, 28)
P9_AUDIT_WINDOW_YEARS: tuple[int, ...] = (2023, 2024, 2025, 2026)
P9_AUDIT_WINDOW_LABEL = "Nov 2023 – Nov 2026 (3 years)"

# Event dates may slightly precede scheduled_date (e.g., pre-exam postponement notice in Nov 2023)
# or fall shortly after 2026-11-28 (e.g., an exam scheduled in Nov 2026 postponed into early 2027).
P9_EVENT_WINDOW_START = date(2023, 11, 1)
P9_EVENT_WINDOW_END = date(2027, 6, 30)

# Canonical P9 task ID format: p9-<jurisdiction_level>-<slug>-<4-digit-seq>
P9_TASK_ID_RE = re.compile(r"^p9-(union|state|ut)-[a-z0-9][a-z0-9-]{1,40}-\d{4}$")

VALID_JURISDICTION_LEVELS: frozenset[str] = frozenset({"union", "state", "ut"})

DISRUPTION_TYPE_RULES: dict[str, str] = {
    "cancelled_after_conduct": "P9-CANCELLED-AFTER-CONDUCT",
    "postponed": "P9-POSTPONED",
    "rescheduled": "P9-RESCHEDULED",
    "delayed": "P9-DELAYED",
    "cancelled_before_conduct": "P9-CANCELLED-BEFORE-CONDUCT",
    "partial_reexam_or_glitch": "P9-PARTIAL-REEXAM-OR-GLITCH",
}

DISRUPTION_TYPE_LABELS: dict[str, str] = {
    "cancelled_after_conduct": "Took Exam, Then Cancelled",
    "postponed": "Postponed",
    "rescheduled": "Rescheduled",
    "delayed": "Delayed",
    "cancelled_before_conduct": "Cancelled Before Conduct",
    "partial_reexam_or_glitch": "Technical Glitch / Partial Re-Exam",
    "flawless_conduct": "Conducted Flawlessly (Cleared)",
}

SIGNAL_CATEGORIES: tuple[str, ...] = (
    "cancelled_after_conduct",
    "postponed",
    "rescheduled",
    "delayed",
    "cancelled_before_conduct",
    "partial_reexam_or_glitch",
)

VALID_DISRUPTION_TYPES: frozenset[str] = frozenset(SIGNAL_CATEGORIES) | {"flawless_conduct"}

AUTHORITY_TYPE_LABELS: dict[str, str] = {
    "central_testing_agency": "Central Testing Agency (NTA / NBEMS / AIIMS)",
    "union_psc_or_recruitment": "Union PSC & Staff Recruitment (UPSC / SSC)",
    "railway_banking_psu": "Railways, Banking & PSU Boards (RRB / IBPS / SBI / RBI)",
    "defence_medical_technical": "Defence, Medical & Technical Bodies",
    "state_psc": "State Public Service Commissions",
    "state_police_or_subordinate": "State Police & Subordinate Services Boards",
    "state_teacher_or_entrance": "State Teacher Eligibility & Entrance Boards",
    "ut_recruitment_or_board": "Union Territory Recruitment & Exam Boards",
}
VALID_AUTHORITY_TYPES: frozenset[str] = frozenset(AUTHORITY_TYPE_LABELS.keys())

EXAM_CATEGORY_LABELS: dict[str, str] = {
    "civil_services_and_psc": "Civil Services & PSC",
    "police_and_defence": "Police & Defence",
    "teaching_and_faculty": "Teaching & Faculty",
    "medical_and_health": "Medical & Health",
    "engineering_and_higher_ed": "Engineering & Higher Education",
    "banking_railways_ssc": "Banking, Railways & SSC",
    "clerical_and_subordinate": "Clerical & Subordinate Services",
    "board_and_other_public": "Board & Other Public Exams",
}
VALID_EXAM_CATEGORIES: frozenset[str] = frozenset(EXAM_CATEGORY_LABELS.keys())

REASON_CATEGORY_LABELS: dict[str, str] = {
    "paper_integrity_compromise": "Paper Integrity / Question Security Safeguard",
    "administrative_or_logistical": "Administrative or Logistical Reasons",
    "technical_or_cbt_glitch": "Technical or CBT Server Glitch",
    "election_or_calendar_clash": "Election Schedule or Calendar Clash",
    "weather_or_disaster": "Severe Weather or Natural Disaster",
    "judicial_or_policy_review": "Judicial Order or Policy Review",
    "none_flawless": "None (Conducted as Scheduled)",
}
VALID_REASON_CATEGORIES: frozenset[str] = frozenset(REASON_CATEGORY_LABELS.keys())

VALID_SOURCE_FORMATS: frozenset[str] = frozenset({"pdf", "html", "json", "csv", "txt"})

VALID_SECONDARY_ROLES: frozenset[str] = frozenset(
    {
        "",
        "outcome_document",
        "schedule_document",
        "corroborating_record",
    }
)

MIN_READABLE_DOC_CHARS = 40
MIN_EVIDENCE_TOKENS = 2
MAX_EVIDENCE_TOKENS = 10
MAX_DELAY_DAYS = 1500
MAX_CANDIDATES_COUNT = 100_000_000
CRITICAL_CANDIDATE_COHORT = 500_000
HIGH_CANDIDATE_COHORT = 50_000
LARGE_CANDIDATE_RULE_COHORT = 100_000
PROLONGED_DELAY_DAYS = 90
HIGH_DELAY_DAYS = 60

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

_BENIGN_EXPLANATIONS: tuple[str, ...] = (
    (
        "Preventive cancellation, postponement, or re-examination may reflect lawful integrity safeguards "
        "under the Public Examinations (Prevention of Unfair Means) Act, 2024 or applicable State examination "
        "rules to protect candidate fairness upon receipt of unverified security alerts or preliminary inquiries."
    ),
    (
        "Schedule revisions, deferrals, or delays may arise from lawful administrative contingencies including "
        "General or State Assembly Election Model Code of Conduct overlaps, severe weather or disaster advisories, "
        "law-and-order deployment constraints, or calendar clashes with other national and state examinations."
    ),
    (
        "Technical disruptions, shift-level re-tests, or evaluation delays may result from third-party computer-based "
        "testing (CBT) server or biometric verification faults, or pending High Court, Supreme Court, or tribunal "
        "interim orders requiring policy, roster, or eligibility review."
    ),
)

_DISRUPTION_NO_SAT_BEFORE_CANCEL: frozenset[str] = frozenset(
    {
        "flawless_conduct",
        "cancelled_before_conduct",
        "postponed",
        "rescheduled",
        "delayed",
    }
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


def neutral_text(text: str) -> str:
    """Neutralize BNS Sec 356 prohibited words in quoted third-party public text."""
    cleaned = _ACB_RE.sub("Vigilance", text or "")
    return _NEUTRAL_REPLACE_RE.sub("compliance-review", cleaned)


def clean_exam_text(text: Any, *, max_len: int | None = None) -> str:
    """Collapse control chars/whitespace, scrub DPDP PII, and apply BNS Sec 356 neutralization."""
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
    """Normalize an exam or authority name into a lowercase hyphenated slug."""
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


def _compile_token_pattern(norm_tok: str) -> re.Pattern[str]:
    """Compile a word-boundary-guarded regex on alphanumeric edges for normalized token matching.

    Prevents incidental substring false matches (e.g., ``NET`` matching inside ``internet``,
    ``Paper I`` matching inside ``Paper II``, or ``2024`` matching inside ``20240``) while
    cleanly handling hyphenated, dotted, or parenthesized tokens such as ``UGC-NET``,
    ``18.06.2024``, or ``NEET (UG)``.
    """
    prefix = r"(?<![^\W_])" if norm_tok[0].isalnum() else ""
    suffix = r"(?![^\W_])" if norm_tok[-1].isalnum() else ""
    return re.compile(prefix + re.escape(norm_tok) + suffix)


def _match_token_in_norm(norm_tok: str, norm_text: str) -> re.Match[str] | None:
    if not norm_tok or not norm_text:
        return None
    return _compile_token_pattern(norm_tok).search(norm_text)


def _indian_fy_from_date(dt: date | None) -> str:
    if dt is None:
        return "Unspecified"
    fy_start = dt.year if dt.month >= 4 else dt.year - 1
    return f"FY{fy_start}-{(fy_start + 1) % 100:02d}"


def _parse_optional_nonneg_int(value: Any, *, max_val: int) -> int | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value <= max_val else None
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            return None
        iv = int(value)
        return iv if 0 <= iv <= max_val else None
    try:
        iv = int(str(value).strip().replace(",", ""))
    except (TypeError, ValueError):
        return None
    return iv if 0 <= iv <= max_val else None


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
        if page_num is not None:
            fmt = str(rec.get("source_format") or "").strip().lower()
            locator = "html_body" if fmt == "html" and page_num == 1 else f"page={page_num}"
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
    """Extract a clean verbatim context window around a word-boundary match for ``token``."""
    compact_raw = _MULTI_SPACE_RE.sub(" ", _CTRL_CHARS_RE.sub(" ", raw_text or "")).strip()
    if not compact_raw:
        return ""
    norm_raw = _norm_search(compact_raw)
    norm_tok = _norm_search(token)
    m = _match_token_in_norm(norm_tok, norm_raw) if norm_tok else None
    if m is None:
        return clean_exam_text(compact_raw[:window_chars], max_len=window_chars)

    pos = m.start()
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
    return clean_exam_text(snippet, max_len=window_chars + 16)


def _verify_evidence_tokens(
    tokens: list[str],
    segments: list[_TextSegment],
    *,
    preferred_page: int | None = None,
) -> _TokenVerification:
    """Verify 100% of required documentary tokens via word-boundary normalized matching."""
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
    compiled: list[tuple[str, str, re.Pattern[str] | None]] = []
    matched: list[str] = []
    missing: list[str] = []
    for tok in tokens:
        ntok = _norm_search(tok)
        pat = _compile_token_pattern(ntok) if ntok else None
        compiled.append((tok, ntok, pat))
        if pat is not None and pat.search(combined_norm) is not None:
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

    matched_patterns = [(tok, pat) for tok, _, pat in compiled if pat is not None and tok in matched]
    best_seg = segments[0]
    best_hits = -1
    best_first_tok = matched[0]
    for seg in segments:
        seg_hits = [tok for tok, pat in matched_patterns if pat.search(seg.norm_text) is not None]
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


def evaluate_exam_disruption_rule(
    *,
    disruption_type: str,
    disruption_reason_category: str,
    students_took_exam_before_cancellation: bool,
    delay_days: int | None,
    affected_candidates_count: int | None,
    is_anchor_case: bool,
) -> tuple[bool, str, str, list[str], str]:
    """Deterministically evaluate P9 disruption type, rule codes, signal category, and severity.

    Returns ``(flagged, rule_triggered, signal_category, triggered_rules, severity)``.
    """
    if disruption_type == "flawless_conduct" or disruption_reason_category == "none_flawless":
        return False, "", "", [], "low"

    rule_triggered = DISRUPTION_TYPE_RULES.get(disruption_type, "")
    if not rule_triggered:
        return False, "", "", [], "low"

    signal_category = disruption_type
    triggered_rules = [rule_triggered]

    if students_took_exam_before_cancellation:
        triggered_rules.append("P9-POST-CONDUCT-CANCELLATION")
    if disruption_reason_category == "paper_integrity_compromise":
        triggered_rules.append("P9-PAPER-INTEGRITY-SAFEGUARD")
    elif disruption_reason_category == "technical_or_cbt_glitch":
        triggered_rules.append("P9-CBT-OR-TECHNICAL-GLITCH")
    if delay_days is not None and delay_days >= PROLONGED_DELAY_DAYS:
        triggered_rules.append("P9-PROLONGED-DELAY-GE-90D")
    if affected_candidates_count is not None and affected_candidates_count >= LARGE_CANDIDATE_RULE_COHORT:
        triggered_rules.append("P9-LARGE-CANDIDATE-COHORT-GE-100K")
    if is_anchor_case:
        triggered_rules.append("P9-ANCHOR-CASE")

    if (
        disruption_type == "cancelled_after_conduct"
        or (is_anchor_case and disruption_reason_category == "paper_integrity_compromise")
        or (affected_candidates_count is not None and affected_candidates_count >= CRITICAL_CANDIDATE_COHORT)
    ):
        severity = "critical"
    elif (
        disruption_type in {"cancelled_before_conduct", "partial_reexam_or_glitch"}
        or disruption_reason_category == "paper_integrity_compromise"
        or (delay_days is not None and delay_days >= HIGH_DELAY_DAYS)
        or (affected_candidates_count is not None and affected_candidates_count >= HIGH_CANDIDATE_COHORT)
        or is_anchor_case
    ):
        severity = "high"
    else:
        severity = "medium"

    return True, rule_triggered, signal_category, triggered_rules, severity


def run_p9_auditing_government_exams(
    records: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
) -> tuple[list[AuditLeadCandidate], int, float, str, dict[str, Any]]:
    """Run deterministic, fail-closed P9 Indian government examination conduct verification."""
    ctx = context or {}
    spec = (
        ctx.get("auditing_government_exams")
        if isinstance(ctx.get("auditing_government_exams"), dict)
        else {}
    )
    as_of_iso = str(ctx.get("as_of_date") or "").strip()[:10] or datetime.now(UTC).date().isoformat()

    meta: dict[str, Any] = {
        "as_of_date": as_of_iso,
        "amount_type": "public_examination_conduct",
        "unevaluable_count": 0,
    }

    case_key = str(spec.get("case_key") or "").strip()

    # Defense-in-depth fail-closed SHA-256 verification if digest flags/hashes were supplied in context.
    if ctx.get("digest_verified") is False or ctx.get("secondary_digest_verified") is False:
        meta.update(
            {
                "verdict": "unevaluable",
                "unevaluable_count": 1,
                "reason": "digest_verification_failed",
                "case_key": case_key,
            }
        )
        return (
            [],
            0,
            0.0,
            f"P9 unevaluable ({case_key or 'unknown'}): artifact SHA-256 verification failed.",
            meta,
        )

    expected_sha = str(ctx.get("expected_sha256") or "").strip().lower() or None
    observed_sha = str(ctx.get("observed_sha256") or "").strip().lower() or None
    if expected_sha and observed_sha and expected_sha != observed_sha:
        meta.update(
            {
                "verdict": "unevaluable",
                "unevaluable_count": 1,
                "reason": "primary_sha256_mismatch",
                "case_key": case_key,
            }
        )
        return (
            [],
            0,
            0.0,
            f"P9 unevaluable ({case_key or 'unknown'}): primary document SHA-256 mismatch.",
            meta,
        )

    if not records or not spec or not case_key:
        meta.update({"verdict": "unevaluable", "unevaluable_count": 1, "reason": "missing_records_or_spec"})
        return (
            [],
            0,
            0.0,
            "P9 unevaluable: missing official document payload or auditing_government_exams specification.",
            meta,
        )

    disruption_type = str(spec.get("disruption_type") or "").strip().lower()
    reason_category = str(spec.get("disruption_reason_category") or "").strip().lower()
    if disruption_type not in VALID_DISRUPTION_TYPES or reason_category not in VALID_REASON_CATEGORIES:
        meta.update(
            {
                "verdict": "unevaluable",
                "unevaluable_count": 1,
                "reason": "invalid_disruption_or_reason",
                "case_key": case_key,
            }
        )
        return (
            [],
            0,
            0.0,
            f"P9 unevaluable ({case_key}): unrecognized disruption_type or disruption_reason_category.",
            meta,
        )

    if (disruption_type == "flawless_conduct") != (reason_category == "none_flawless"):
        meta.update(
            {
                "verdict": "unevaluable",
                "unevaluable_count": 1,
                "reason": "mismatched_flawless_reason_pair",
                "case_key": case_key,
            }
        )
        return (
            [],
            0,
            0.0,
            (
                f"P9 unevaluable ({case_key}): disruption_type='flawless_conduct' and "
                "disruption_reason_category='none_flawless' must be paired together."
            ),
            meta,
        )

    sat_before_cancel_raw = spec.get("students_took_exam_before_cancellation")
    if not isinstance(sat_before_cancel_raw, bool):
        meta.update(
            {
                "verdict": "unevaluable",
                "unevaluable_count": 1,
                "reason": "invalid_sat_before_cancellation_flag",
                "case_key": case_key,
            }
        )
        return (
            [],
            0,
            0.0,
            f"P9 unevaluable ({case_key}): students_took_exam_before_cancellation must be boolean.",
            meta,
        )
    students_sat = sat_before_cancel_raw
    if disruption_type == "cancelled_after_conduct" and not students_sat:
        meta.update(
            {
                "verdict": "unevaluable",
                "unevaluable_count": 1,
                "reason": "cancelled_after_conduct_requires_sat_flag",
                "case_key": case_key,
            }
        )
        return (
            [],
            0,
            0.0,
            f"P9 unevaluable ({case_key}): cancelled_after_conduct requires students_took_exam_before_cancellation=true.",
            meta,
        )
    if disruption_type in _DISRUPTION_NO_SAT_BEFORE_CANCEL and students_sat:
        meta.update(
            {
                "verdict": "unevaluable",
                "unevaluable_count": 1,
                "reason": "unexpected_sat_before_cancellation_flag",
                "case_key": case_key,
            }
        )
        return (
            [],
            0,
            0.0,
            f"P9 unevaluable ({case_key}): students_took_exam_before_cancellation must be false for '{disruption_type}'.",
            meta,
        )

    sched_dt = parse_iso_date(spec.get("scheduled_date"))
    event_dt = parse_iso_date(spec.get("actual_or_event_date"))
    if sched_dt is None or event_dt is None:
        meta.update(
            {
                "verdict": "unevaluable",
                "unevaluable_count": 1,
                "reason": "invalid_exam_dates",
                "case_key": case_key,
            }
        )
        return (
            [],
            0,
            0.0,
            f"P9 unevaluable ({case_key}): valid YYYY-MM-DD scheduled_date and actual_or_event_date are required.",
            meta,
        )
    if disruption_type == "cancelled_after_conduct" and event_dt < sched_dt:
        meta.update(
            {
                "verdict": "unevaluable",
                "unevaluable_count": 1,
                "reason": "cancellation_precedes_scheduled_date",
                "case_key": case_key,
            }
        )
        return (
            [],
            0,
            0.0,
            (
                f"P9 unevaluable ({case_key}): actual_or_event_date ({event_dt.isoformat()}) precedes "
                f"scheduled_date ({sched_dt.isoformat()}) for cancelled_after_conduct."
            ),
            meta,
        )

    schedule_tokens = [
        str(t).strip()
        for t in (spec.get("schedule_evidence_tokens") or [])
        if isinstance(t, str) and str(t).strip()
    ]
    outcome_tokens = [
        str(t).strip()
        for t in (spec.get("outcome_evidence_tokens") or [])
        if isinstance(t, str) and str(t).strip()
    ]
    if len(schedule_tokens) < MIN_EVIDENCE_TOKENS or len(outcome_tokens) < MIN_EVIDENCE_TOKENS:
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
            (
                f"P9 unevaluable ({case_key}): at least {MIN_EVIDENCE_TOKENS} schedule and outcome "
                "evidence tokens are required."
            ),
            meta,
        )

    primary_records, secondary_records, sec_sha_from_recs = _split_primary_and_secondary(records)

    expected_sec_sha = str(ctx.get("secondary_expected_sha256") or "").strip().lower() or None
    observed_sec_sha = (
        str(ctx.get("secondary_sha256") or "").strip().lower() or sec_sha_from_recs or None
    )
    if expected_sec_sha and observed_sec_sha and expected_sec_sha != observed_sec_sha:
        meta.update(
            {
                "verdict": "unevaluable",
                "unevaluable_count": 1,
                "reason": "secondary_sha256_mismatch",
                "case_key": case_key,
            }
        )
        return (
            [],
            0,
            0.0,
            f"P9 unevaluable ({case_key}): secondary document SHA-256 mismatch.",
            meta,
        )

    selector = spec.get("record_selector") if isinstance(spec.get("record_selector"), dict) else {}
    secondary_role = str(spec.get("secondary_role") or "").strip().lower()
    if secondary_role not in VALID_SECONDARY_ROLES:
        secondary_role = "outcome_document" if secondary_records else ""

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
                    f"P9 unevaluable ({case_key}): record_selector matched 0 rows in verified document bytes.",
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
                f"P9 unevaluable ({case_key}): record_selector matched 0 rows in verified document bytes.",
                meta,
            )

    primary_segments = _build_segments(primary_records, "primary")
    secondary_segments = _build_segments(secondary_records, "secondary")

    retrieval_url = str(ctx.get("retrieval_url") or ctx.get("dataset_url") or "").strip()
    secondary_url = str(ctx.get("secondary_url") or ctx.get("secondary_dataset_url") or "").strip()
    has_secondary = bool(secondary_records) or (bool(secondary_url) and secondary_url != retrieval_url)

    schedule_page_hint = selector.get("schedule_page") if isinstance(selector.get("schedule_page"), int) else None
    outcome_page_hint = selector.get("outcome_page") if isinstance(selector.get("outcome_page"), int) else None

    # Assign primary/secondary verified corpora to schedule vs outcome checks (fail closed if secondary is empty).
    if has_secondary and secondary_role == "schedule_document":
        schedule_segments = secondary_segments
        outcome_segments = primary_segments
    elif has_secondary and secondary_role in {"", "outcome_document"}:
        schedule_segments = primary_segments
        outcome_segments = secondary_segments
    elif has_secondary and secondary_role == "corroborating_record":
        schedule_segments = primary_segments + secondary_segments
        outcome_segments = primary_segments + secondary_segments
    else:
        schedule_segments = primary_segments
        outcome_segments = primary_segments

    schedule_ver = _verify_evidence_tokens(schedule_tokens, schedule_segments, preferred_page=schedule_page_hint)
    outcome_ver = _verify_evidence_tokens(outcome_tokens, outcome_segments, preferred_page=outcome_page_hint)
    schedule_uses_secondary = schedule_ver.best_doc_role == "secondary"
    outcome_uses_secondary = outcome_ver.best_doc_role == "secondary"

    if schedule_ver.total_chars < MIN_READABLE_DOC_CHARS or outcome_ver.total_chars < MIN_READABLE_DOC_CHARS:
        meta.update(
            {
                "verdict": "unevaluable",
                "unevaluable_count": 1,
                "reason": "unreadable_or_empty_document",
                "case_key": case_key,
                "schedule_chars": schedule_ver.total_chars,
                "outcome_chars": outcome_ver.total_chars,
            }
        )
        return (
            [],
            0,
            0.0,
            f"P9 unevaluable ({case_key}): official document lacks machine-readable text (<{MIN_READABLE_DOC_CHARS} chars).",
            meta,
        )

    if not schedule_ver.all_matched or not outcome_ver.all_matched:
        meta.update(
            {
                "verdict": "unevaluable",
                "unevaluable_count": 1,
                "reason": "evidence_tokens_not_verified",
                "case_key": case_key,
                "missing_schedule_tokens": schedule_ver.missing_tokens,
                "missing_outcome_tokens": outcome_ver.missing_tokens,
            }
        )
        return (
            [],
            0,
            0.0,
            (
                f"P9 unevaluable ({case_key}): downloaded official bytes did not verify all required evidence tokens "
                f"(missing schedule={len(schedule_ver.missing_tokens)}, missing outcome={len(outcome_ver.missing_tokens)})."
            ),
            meta,
        )

    # In corroborating_record dual-doc mode, require BOTH primary & secondary to contain >=40 chars & token hits.
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
                f"P9 unevaluable ({case_key}): corroborating primary or secondary document lacks readable text.",
                meta,
            )
        all_tokens = schedule_tokens + outcome_tokens
        pri_combined = "\n".join(s.norm_text for s in primary_segments)
        sec_combined = "\n".join(s.norm_text for s in secondary_segments)
        pri_hit = any(_match_token_in_norm(_norm_search(t), pri_combined) is not None for t in all_tokens)
        sec_hit = any(_match_token_in_norm(_norm_search(t), sec_combined) is not None for t in all_tokens)
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
                f"P9 unevaluable ({case_key}): corroborating primary/secondary document lacked evidence token overlap.",
                meta,
            )

    # Extract & neutralize metadata fields
    exam_name = clean_exam_text(spec.get("exam_name"), max_len=200) or "Public Examination"
    exam_slug = str(spec.get("exam_slug") or canonical_key(exam_name)).strip().lower()
    cycle_or_stage = clean_exam_text(spec.get("cycle_or_stage"), max_len=160) or "Scheduled Cycle"
    raw_exam_year = spec.get("exam_year")
    exam_year = (
        int(raw_exam_year)
        if isinstance(raw_exam_year, int) and not isinstance(raw_exam_year, bool) and 2023 <= raw_exam_year <= 2026
        else sched_dt.year
    )

    jurisdiction_level = str(spec.get("jurisdiction_level") or "union").strip().lower()
    if jurisdiction_level not in VALID_JURISDICTION_LEVELS:
        jurisdiction_level = "union"
    state_or_ut_raw = spec.get("state_or_ut")
    state_or_ut = (
        clean_exam_text(state_or_ut_raw, max_len=80) or None
        if jurisdiction_level in {"state", "ut"} and state_or_ut_raw not in (None, "")
        else None
    )
    jurisdiction = clean_exam_text(spec.get("jurisdiction") or ctx.get("jurisdiction"), max_len=120)
    if not jurisdiction:
        if jurisdiction_level == "state" and state_or_ut:
            jurisdiction = f"State:{state_or_ut}"
        elif jurisdiction_level == "ut" and state_or_ut:
            jurisdiction = f"UT:{state_or_ut}"
        else:
            jurisdiction = "Union of India"

    authority = clean_exam_text(spec.get("authority") or ctx.get("official_publisher"), max_len=200) or "Examination Authority"
    authority_key = str(spec.get("authority_key") or canonical_key(authority)).strip().lower()
    authority_type = str(spec.get("authority_type") or "central_testing_agency").strip().lower()
    if authority_type not in VALID_AUTHORITY_TYPES:
        authority_type = "central_testing_agency"

    exam_category = str(spec.get("exam_category") or "board_and_other_public").strip().lower()
    if exam_category not in VALID_EXAM_CATEGORIES:
        exam_category = "board_and_other_public"

    scheduled_date_iso = sched_dt.isoformat()
    actual_or_event_date_iso = event_dt.isoformat()
    delay_days = _parse_optional_nonneg_int(spec.get("delay_days"), max_val=MAX_DELAY_DAYS)
    affected_candidates_count = _parse_optional_nonneg_int(
        spec.get("affected_candidates_count"), max_val=MAX_CANDIDATES_COUNT
    )
    affected_candidates_basis = clean_exam_text(spec.get("affected_candidates_basis"), max_len=240) or (
        "Not disclosed in cited official record"
    )
    schedule_summary = clean_exam_text(spec.get("schedule_summary"), max_len=600)
    schedule_document_ref = clean_exam_text(spec.get("schedule_document_ref"), max_len=200)
    outcome_summary = clean_exam_text(spec.get("outcome_summary"), max_len=600)
    outcome_document_ref = clean_exam_text(spec.get("outcome_document_ref"), max_len=200)
    statutory_anchor = clean_exam_text(spec.get("statutory_anchor"), max_len=200) or (
        "Public Examinations (Prevention of Unfair Means) Act, 2024"
    )
    is_anchor_case = bool(spec.get("is_anchor_case"))

    flagged, rule_triggered, signal_category, triggered_rules, severity = evaluate_exam_disruption_rule(
        disruption_type=disruption_type,
        disruption_reason_category=reason_category,
        students_took_exam_before_cancellation=students_sat,
        delay_days=delay_days,
        affected_candidates_count=affected_candidates_count,
        is_anchor_case=is_anchor_case,
    )

    safe_schedule_tokens = [clean_exam_text(t, max_len=120) for t in schedule_ver.matched_tokens]
    safe_outcome_tokens = [clean_exam_text(t, max_len=120) for t in outcome_ver.matched_tokens]

    if not flagged:
        cleared_summary = clean_exam_text(
            f"P9 cleared ({case_key}, flawless_conduct): {exam_name} ({cycle_or_stage}; {jurisdiction}) — "
            f"scheduled for {scheduled_date_iso} ({schedule_document_ref}) and confirmed conducted as scheduled "
            f"on {actual_or_event_date_iso} ({outcome_document_ref}): {outcome_summary}",
            max_len=1200,
        )
        meta.update(
            {
                "verdict": "cleared_flawless_conduct",
                "case_key": case_key,
                "exam_slug": exam_slug,
                "disruption_type": disruption_type,
                "disruption_reason_category": reason_category,
                "schedule_tokens_matched": safe_schedule_tokens,
                "outcome_tokens_matched": safe_outcome_tokens,
                "unevaluable_count": 0,
            }
        )
        return [], 1, 0.0, cleared_summary, meta

    landing_url = str(
        selector.get("record_url") or ctx.get("source_landing_page") or ctx.get("source_url") or retrieval_url
    ).strip()
    sec_sha_resolved = expected_sec_sha or observed_sec_sha
    official_publisher = clean_exam_text(ctx.get("official_publisher") or authority, max_len=200)

    schedule_url = (secondary_url if schedule_uses_secondary and secondary_url else retrieval_url) or landing_url
    schedule_sha = sec_sha_resolved if schedule_uses_secondary else expected_sha
    outcome_url = (secondary_url if outcome_uses_secondary and secondary_url else retrieval_url) or landing_url
    outcome_sha = sec_sha_resolved if outcome_uses_secondary else expected_sha

    section_hint = clean_exam_text(selector.get("section_hint"), max_len=120)
    schedule_locator = schedule_ver.best_locator + (f"; {section_hint}" if section_hint else "")
    outcome_locator = outcome_ver.best_locator + (f"; {section_hint}" if section_hint else "")
    source_locator = f"schedule={schedule_locator} | outcome={outcome_locator}"

    disruption_label = DISRUPTION_TYPE_LABELS.get(disruption_type, "Examination Conduct Disruption")
    reason_label = REASON_CATEGORY_LABELS.get(reason_category, reason_category)
    lead_title = clean_exam_text(
        f"Public Examination Conduct Observation ({disruption_label}): {exam_name} — {cycle_or_stage}",
        max_len=195,
    )

    delay_clause = f" Documented delay: {delay_days:,} day(s)." if delay_days is not None and delay_days > 0 else ""
    sat_clause = (
        " Candidates sat/attempted the examination prior to the cancellation order."
        if students_sat
        else ""
    )
    cand_clause = (
        f" Documented affected candidates: {affected_candidates_count:,} ({affected_candidates_basis})."
        if affected_candidates_count is not None
        else f" Affected candidate count: {affected_candidates_basis}."
    )

    summary = clean_exam_text(
        f"Official documentary record for {exam_name} ({cycle_or_stage}; {jurisdiction}; {authority}): "
        f"Original schedule ({scheduled_date_iso}, {schedule_document_ref}) stated: {schedule_summary} "
        f"Subsequent conduct/outcome record ({actual_or_event_date_iso}, {outcome_document_ref}) recorded: "
        f"{outcome_summary} Classification: {disruption_label}; recorded reason category: {reason_label}."
        f"{sat_clause}{delay_clause}{cand_clause} "
        f"Verified against checksummed official primary records for neutral public-interest review.",
        max_len=1500,
    )

    benign_list = list(_BENIGN_EXPLANATIONS)
    extra_benign = clean_exam_text(spec.get("benign_explanation_context"), max_len=300)
    if extra_benign:
        benign_list.insert(0, extra_benign)

    fy_event = _indian_fy_from_date(event_dt)
    fy_start = _indian_fy_from_date(min(sched_dt, event_dt))
    fy_end = _indian_fy_from_date(max(sched_dt, event_dt))

    schedule_cit: dict[str, Any] = {
        "role": "original_schedule",
        "title": "Original Examination Schedule",
        "document": schedule_document_ref or f"{exam_name} Schedule Notice",
        "source_url": schedule_url,
        "landing_url": landing_url,
        "source_landing_page": landing_url,
        "official_publisher": official_publisher,
        "source_locator": schedule_locator,
        "sha256": schedule_sha,
        "excerpt": schedule_ver.excerpt or schedule_summary[:340],
    }
    if schedule_ver.best_page is not None:
        schedule_cit["page"] = schedule_ver.best_page

    outcome_cit: dict[str, Any] = {
        "role": "conduct_outcome",
        "title": "Examination Disruption / Outcome Record",
        "document": outcome_document_ref or f"{exam_name} Outcome Notice",
        "source_url": outcome_url,
        "landing_url": landing_url,
        "source_landing_page": landing_url,
        "official_publisher": official_publisher,
        "source_locator": outcome_locator,
        "sha256": outcome_sha,
        "excerpt": outcome_ver.excerpt or outcome_summary[:340],
    }
    if outcome_ver.best_page is not None:
        outcome_cit["page"] = outcome_ver.best_page

    benchmark_cit: dict[str, Any] = {
        "role": "statutory_benchmark",
        "title": "Audit Classification & Statutory Anchor",
        "document": statutory_anchor or f"{rule_triggered} ({jurisdiction})",
        "source_url": landing_url or retrieval_url,
        "landing_url": landing_url or retrieval_url,
        "source_landing_page": landing_url or retrieval_url,
        "official_publisher": official_publisher,
        "source_locator": f"rule={rule_triggered}; disruption={disruption_type}; reason={reason_category}",
        "sha256": expected_sha,
        "excerpt": clean_exam_text(
            f"{rule_triggered}: Scheduled {scheduled_date_iso} vs Event {actual_or_event_date_iso} "
            f"({disruption_label}); verified {len(schedule_ver.matched_tokens)} schedule token(s) "
            f"and {len(outcome_ver.matched_tokens)} outcome token(s) via word-boundary match in official bytes.",
            max_len=360,
        ),
    }

    citations = [schedule_cit, outcome_cit, benchmark_cit]

    lead = AuditLeadCandidate(
        title=lead_title,
        summary=summary,
        methodology_code=AUDITING_GOVERNMENT_EXAMS_METHODOLOGY,
        entity_name=authority[:160],
        jurisdiction=jurisdiction,
        fiscal_year=fy_event,
        fiscal_year_start=fy_start,
        fiscal_year_end=fy_end,
        monetary_exposure_inr=0.0,
        severity=severity,  # type: ignore[arg-type]
        anomaly_metrics={
            "case_key": case_key,
            "verdict": "flagged_disruption",
            "signal_category": signal_category,
            "triggered_rules": triggered_rules,
            "exam_name": exam_name,
            "exam_slug": exam_slug,
            "cycle_or_stage": cycle_or_stage,
            "exam_year": exam_year,
            "jurisdiction": jurisdiction,
            "jurisdiction_level": jurisdiction_level,
            "state_or_ut": state_or_ut,
            "authority": authority,
            "authority_key": authority_key,
            "authority_type": authority_type,
            "exam_category": exam_category,
            "scheduled_date": scheduled_date_iso,
            "actual_or_event_date": actual_or_event_date_iso,
            "delay_days": delay_days,
            "disruption_type": disruption_type,
            "disruption_reason_category": reason_category,
            "students_took_exam_before_cancellation": students_sat,
            "schedule_summary": schedule_summary,
            "schedule_document_ref": schedule_document_ref,
            "schedule_excerpt": schedule_ver.excerpt,
            "schedule_locator": schedule_locator,
            "schedule_tokens_matched": safe_schedule_tokens,
            "outcome_summary": outcome_summary,
            "outcome_document_ref": outcome_document_ref,
            "outcome_excerpt": outcome_ver.excerpt,
            "outcome_locator": outcome_locator,
            "outcome_tokens_matched": safe_outcome_tokens,
            "affected_candidates_count": affected_candidates_count,
            "affected_candidates_basis": affected_candidates_basis,
            "statutory_anchor": statutory_anchor,
            "secondary_role": secondary_role or None,
            "is_anchor_case": is_anchor_case,
            "as_of_date": as_of_iso,
            "source_locator": source_locator,
        },
        rule_triggered=rule_triggered,
        source_locator=source_locator,
        citations=citations,
        benign_explanations=benign_list,
        suggested_authority=authority[:160],
        amount_type="public_examination_conduct",
    )

    run_summary = (
        f"P9 flagged {case_key} ({exam_name}, {jurisdiction}): {rule_triggered} "
        f"({disruption_type} / {reason_category}, severity={severity})."
    )
    meta.update(
        {
            "verdict": "flagged_disruption",
            "signal_category": signal_category,
            "rule_triggered": rule_triggered,
            "case_key": case_key,
            "exam_slug": exam_slug,
            "disruption_type": disruption_type,
            "unevaluable_count": 0,
        }
    )
    return [lead], 0, 0.0, run_summary, meta


def validate_context(ctx: Any, where: str) -> list[str]:
    """Strictly validate ``payload.context.auditing_government_exams`` for a P9 task."""
    errors: list[str] = []
    if not isinstance(ctx, dict):
        return [f"{where}.payload.context.auditing_government_exams is required for P9 tasks."]
    spec = ctx.get("auditing_government_exams")
    if not isinstance(spec, dict):
        return [f"{where}.payload.context.auditing_government_exams must be an object."]

    prefix = f"{where}.payload.context.auditing_government_exams"

    as_of = ctx.get("as_of_date")
    if as_of not in (None, "") and (not isinstance(as_of, str) or parse_iso_date(as_of) is None):
        errors.append(f"{where}.payload.context.as_of_date must be a valid YYYY-MM-DD date when provided.")

    ckey = spec.get("case_key")
    if not isinstance(ckey, str) or not _KEY_RE.fullmatch(ckey):
        errors.append(f"{prefix}.case_key must match [a-z0-9-] (<= 59 chars).")

    eslug = spec.get("exam_slug")
    if not isinstance(eslug, str) or not _SLUG_KEY_RE.fullmatch(eslug):
        errors.append(f"{prefix}.exam_slug must match [a-z0-9-] (<= 80 chars).")

    akey = spec.get("authority_key")
    if not isinstance(akey, str) or not _SLUG_KEY_RE.fullmatch(akey):
        errors.append(f"{prefix}.authority_key must match [a-z0-9-] (<= 80 chars).")

    eyear = spec.get("exam_year")
    if isinstance(eyear, bool) or not isinstance(eyear, int) or not (2023 <= eyear <= 2026):
        errors.append(f"{prefix}.exam_year must be an integer between 2023 and 2026.")

    jlevel = spec.get("jurisdiction_level")
    if jlevel not in VALID_JURISDICTION_LEVELS:
        errors.append(f"{prefix}.jurisdiction_level must be one of {sorted(VALID_JURISDICTION_LEVELS)}.")

    state_or_ut = spec.get("state_or_ut")
    clean_state_or_ut = state_or_ut.strip() if isinstance(state_or_ut, str) else ""
    if jlevel in {"state", "ut"}:
        if not isinstance(state_or_ut, str) or not clean_state_or_ut or len(state_or_ut) > 80:
            errors.append(f"{prefix}.state_or_ut is required (<= 80 chars) for state/ut tasks.")
        elif scan_forbidden_terms(clean_state_or_ut):
            errors.append(f"{prefix}.state_or_ut contains prohibited non-neutral terminology.")
    elif jlevel == "union":
        if state_or_ut is not None:
            errors.append(f"{prefix}.state_or_ut must be null when jurisdiction_level is 'union'.")
    elif state_or_ut is not None and (not isinstance(state_or_ut, str) or len(state_or_ut) > 80):
        errors.append(f"{prefix}.state_or_ut must be null or a short string.")

    jur = spec.get("jurisdiction")
    if not isinstance(jur, str) or not jur.strip() or len(jur) > 120:
        errors.append(f"{prefix}.jurisdiction must be non-empty text (<= 120 chars).")
    elif scan_forbidden_terms(jur):
        errors.append(f"{prefix}.jurisdiction contains prohibited non-neutral terminology.")
    elif jlevel == "union" and jur.strip() != "Union of India":
        errors.append(f"{prefix}.jurisdiction must be 'Union of India' when jurisdiction_level is 'union'.")
    elif jlevel == "state":
        if not jur.strip().startswith("State:") or not jur.strip()[6:].strip():
            errors.append(f"{prefix}.jurisdiction must be 'State:<Name>' when jurisdiction_level is 'state'.")
        elif clean_state_or_ut and jur.strip() != f"State:{clean_state_or_ut}":
            errors.append(f"{prefix}.jurisdiction ('{jur.strip()}') must match 'State:{clean_state_or_ut}'.")
    elif jlevel == "ut":
        if not jur.strip().startswith("UT:") or not jur.strip()[3:].strip():
            errors.append(f"{prefix}.jurisdiction must be 'UT:<Name>' when jurisdiction_level is 'ut'.")
        elif clean_state_or_ut and jur.strip() != f"UT:{clean_state_or_ut}":
            errors.append(f"{prefix}.jurisdiction ('{jur.strip()}') must match 'UT:{clean_state_or_ut}'.")

    atype = spec.get("authority_type")
    if atype not in VALID_AUTHORITY_TYPES:
        errors.append(f"{prefix}.authority_type must be one of {sorted(VALID_AUTHORITY_TYPES)}.")

    ecat = spec.get("exam_category")
    if ecat not in VALID_EXAM_CATEGORIES:
        errors.append(f"{prefix}.exam_category must be one of {sorted(VALID_EXAM_CATEGORIES)}.")

    dtype = spec.get("disruption_type")
    if dtype not in VALID_DISRUPTION_TYPES:
        errors.append(f"{prefix}.disruption_type must be one of {sorted(VALID_DISRUPTION_TYPES)}.")

    rcat = spec.get("disruption_reason_category")
    if rcat not in VALID_REASON_CATEGORIES:
        errors.append(f"{prefix}.disruption_reason_category must be one of {sorted(VALID_REASON_CATEGORIES)}.")

    if (
        dtype in VALID_DISRUPTION_TYPES
        and rcat in VALID_REASON_CATEGORIES
        and (dtype == "flawless_conduct") != (rcat == "none_flawless")
    ):
        errors.append(
            f"{prefix}.disruption_type and disruption_reason_category must pair 'flawless_conduct' "
            "with 'none_flawless' (or neither)."
        )

    sat_flag = spec.get("students_took_exam_before_cancellation")
    if not isinstance(sat_flag, bool):
        errors.append(f"{prefix}.students_took_exam_before_cancellation must be a boolean.")
    elif dtype == "cancelled_after_conduct" and not sat_flag:
        errors.append(
            f"{prefix}.students_took_exam_before_cancellation must be true when disruption_type is 'cancelled_after_conduct'."
        )
    elif dtype in _DISRUPTION_NO_SAT_BEFORE_CANCEL and sat_flag:
        errors.append(
            f"{prefix}.students_took_exam_before_cancellation must be false when disruption_type is '{dtype}'."
        )

    sdate_raw = spec.get("scheduled_date")
    sdt = parse_iso_date(sdate_raw) if isinstance(sdate_raw, str) else None
    if sdt is None or not (P9_AUDIT_WINDOW_START <= sdt <= P9_AUDIT_WINDOW_END):
        errors.append(
            f"{prefix}.scheduled_date must be a valid YYYY-MM-DD date within the 3-year audit window "
            f"({P9_AUDIT_WINDOW_START.isoformat()} to {P9_AUDIT_WINDOW_END.isoformat()})."
        )

    edate_raw = spec.get("actual_or_event_date")
    edt = parse_iso_date(edate_raw) if isinstance(edate_raw, str) else None
    if edt is None or not (P9_EVENT_WINDOW_START <= edt <= P9_EVENT_WINDOW_END):
        errors.append(
            f"{prefix}.actual_or_event_date must be a valid YYYY-MM-DD date "
            f"({P9_EVENT_WINDOW_START.isoformat()} to {P9_EVENT_WINDOW_END.isoformat()})."
        )
    elif sdt is not None and dtype == "cancelled_after_conduct" and edt < sdt:
        errors.append(
            f"{prefix}.actual_or_event_date ({edt.isoformat()}) must not precede scheduled_date "
            f"({sdt.isoformat()}) when disruption_type is 'cancelled_after_conduct'."
        )

    delay_raw = spec.get("delay_days")
    if delay_raw is not None:
        if isinstance(delay_raw, bool) or not isinstance(delay_raw, int) or not (0 <= delay_raw <= MAX_DELAY_DAYS):
            errors.append(f"{prefix}.delay_days must be null or an integer from 0 to {MAX_DELAY_DAYS}.")
        elif dtype == "flawless_conduct" and delay_raw != 0:
            errors.append(f"{prefix}.delay_days must be null or 0 when disruption_type is 'flawless_conduct'.")

    cand_raw = spec.get("affected_candidates_count")
    if cand_raw is not None and (
        isinstance(cand_raw, bool) or not isinstance(cand_raw, int) or not (0 <= cand_raw <= MAX_CANDIDATES_COUNT)
    ):
        errors.append(
            f"{prefix}.affected_candidates_count must be null or a non-negative integer <= {MAX_CANDIDATES_COUNT:,}."
        )

    required_text_fields: tuple[tuple[str, int], ...] = (
        ("exam_name", 200),
        ("cycle_or_stage", 160),
        ("authority", 200),
        ("schedule_summary", 600),
        ("schedule_document_ref", 200),
        ("outcome_summary", 600),
        ("outcome_document_ref", 200),
        ("affected_candidates_basis", 240),
        ("statutory_anchor", 200),
        ("benign_explanation_context", 300),
    )
    for fname, max_len in required_text_fields:
        val = spec.get(fname)
        if not isinstance(val, str) or not val.strip() or len(val) > max_len:
            errors.append(f"{prefix}.{fname} must be non-empty text (<= {max_len} chars).")
        elif scan_forbidden_terms(val):
            errors.append(f"{prefix}.{fname} contains prohibited non-neutral terminology.")

    for tok_field in ("schedule_evidence_tokens", "outcome_evidence_tokens"):
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

    if not isinstance(spec.get("is_anchor_case"), bool):
        errors.append(f"{prefix}.is_anchor_case must be a boolean.")

    sfmt = spec.get("source_format")
    if not isinstance(sfmt, str) or sfmt not in VALID_SOURCE_FORMATS:
        errors.append(f"{prefix}.source_format must be one of {sorted(VALID_SOURCE_FORMATS)}.")

    sec_fmt = spec.get("secondary_source_format")
    if sec_fmt not in (None, "") and (not isinstance(sec_fmt, str) or sec_fmt not in VALID_SOURCE_FORMATS):
        errors.append(f"{prefix}.secondary_source_format must be one of {sorted(VALID_SOURCE_FORMATS)}.")

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
            for pkey in ("schedule_page", "outcome_page"):
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
