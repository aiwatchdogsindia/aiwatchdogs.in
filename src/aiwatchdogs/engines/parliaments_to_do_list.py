"""P6: Parliament's To-Do List — formal ministerial assurances audit on Digital Sansad records.

Hook: "A minister promised Parliament an answer. Did it ever get one?"

Reads checksummed official Digital Sansad (https://sansad.in) Lok Sabha and Rajya Sabha
Government Assurance API records (and optional pinned Question Answer PDFs or Rajya Sabha
Implementation Report PDFs), deterministically verifies each formal assurance, and cites:

1. The original promise (date, House, session, ministry, question/assurance ref, verbatim text)
2. The recorded status (PENDING / FULLY IMPLEMENTED / PARTLY IMPLEMENTED / DROPPED, elapsed days,
   Committee on Government Assurances extensions and remarks)
3. What the follow-up actually answered (Implementation Report PDF excerpt or scan-only gap,
   Lok Sabha Implementation Statement reference & laid date, dropping record, or verified absence
   of any Implementation Statement as of ``as_of_date``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from aiwatchdogs.core.models import AuditLeadCandidate
from aiwatchdogs.core.safety import scrub_pii, validate_safe_http_url

PARLIAMENTS_TO_DO_LIST_PROJECT_SLUG = "parliaments-to-do-list"
PARLIAMENTS_TO_DO_LIST_METHODOLOGY = "P6"

# Standard parliamentary Committee on Government Assurances fulfillment norms (days)
PARLIAMENTARY_NORM_DAYS = 90
HIGH_PENDING_DAYS = 180
CRITICAL_PENDING_DAYS = 365
FULFILLMENT_DELAY_DAYS = 180
SEVERE_FULFILLMENT_DELAY_DAYS = 365
MIN_READABLE_PDF_CHARS = 40

SIGNAL_CATEGORIES: tuple[str, ...] = (
    "overdue_pending_assurance",
    "partly_implemented_assurance",
    "dropped_without_fulfillment",
    "delayed_or_unreadable_followup",
)

VALID_RECORDED_STATUSES: frozenset[str] = frozenset(
    {
        "pending",
        "fully_implemented",
        "partly_implemented",
        "dropped",
    }
)

VALID_SECONDARY_ROLES: frozenset[str] = frozenset(
    {
        "",
        "question_pdf",
        "implementation_report_pdf",
    }
)

_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,58}$")
_MINISTRY_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MULTI_SPACE_RE = re.compile(r"\s+")
_CTRL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]+")
_NON_ALNUM_SPACE_RE = re.compile(r"[^a-z0-9\s]+")

# BNS Sec 356 neutralizer for quoted third-party parliamentary text (mirrors safety._FORBIDDEN_TERM_PATTERNS).
_ACB_RE = re.compile(r"\banti[\s-]+corruption\b", re.IGNORECASE)
_NEUTRAL_REPLACE_RE = re.compile(
    r"(?<![a-z])(?:scam(?:s|med|mer|mers)?|corrupt(?:ed|ing|ly|ion)?|brib(?:e|es|ed|ing|ery)|"
    r"fraud(?:s|ulent|ulently|ster|sters)?|kickback(?:s)?|guilty|stolen|rigged|"
    r"crook(?:s|ed)?|loot(?:s|ed|ing|er|ers)?|embezzl(?:e|es|ed|ing|ement))(?![a-z])",
    re.IGNORECASE,
)

# Lok Sabha impRecordNo pattern: e.g. "SS-1/28/05.08.2026" or "SS-5/4/11/02/2026"
_LS_IMP_DATE_RE = re.compile(r"(\d{1,2})[./-](\d{1,2})[./-](20\d{2})\s*$")
_ANY_DMY_DATE_RE = re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-](20\d{2})\b")
_ANY_ISO_DATE_RE = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")

# Answering minister header in Lok Sabha Question PDFs
_MINISTER_HEADER_RE = re.compile(
    r"ANSWER[\s\S]{0,260}?\(\s*((?:SHRI|SMT\.?|SHRIMATI|DR\.?|PROF\.?|MS\.?|MR\.?|KM\.?|KUMARI|SUSHRI)\s+"
    r"[A-Za-z][A-Za-z.\s'\-]{2,80}?)\s*\)",
    re.IGNORECASE,
)

_IR_FULFILMENT_DATE_RE = re.compile(
    r"(?:laid\s+on|fulfilled\s+on|date\s+of\s+(?:fulfilment|fulfillment|implementation)|"
    r"implementation\s+report\s*(?:dated|on)?)[^\n]{0,40}?"
    r"(?:(\d{1,2})[./-](\d{1,2})[./-](20\d{2})|(20\d{2})-(\d{2})-(\d{2}))",
    re.IGNORECASE,
)

_IR_SECTION_RE = re.compile(
    r"(?:action\s+taken|how\s+fulfilled|fulfilment\s+details|implementation\s+(?:report|details|status)|"
    r"status\s+of\s+implementation|reply\s+of\s+the\s+ministry|in\s+pursuance\s+of|the\s+assurance\s+has\s+been\s+fulfilled)",
    re.IGNORECASE,
)

# Rajya Sabha / GoI eOffice header & footer stamp patterns stamped onto scanned image-only IR PDFs
_EOFFICE_FULL_STAMP_RE = re.compile(
    r"(?:\b\d{4,10}/20\d{2}/[^\n]{2,80}?\s+\d{1,4}\s+)?"
    r"File\s+No\.?\s+[A-Z0-9][A-Za-z0-9./\-()\s]{3,140}?\(Computer\s+No\.?\s*\d+\)\s*"
    r"Generated\s+from\s+eOffice\s+by\s+.{2,240}?\bon\s+\d{1,2}[./-]\d{1,2}[./-]20\d{2}"
    r"(?:\s+\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM))?",
    re.IGNORECASE,
)
_EOFFICE_GENERATED_RE = re.compile(
    r"\bGenerated\s+from\s+eOffice\s+by\s+.{2,240}?\bon\s+\d{1,2}[./-]\d{1,2}[./-]20\d{2}"
    r"(?:\s+\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM))?",
    re.IGNORECASE,
)
_EOFFICE_FILE_NO_RE = re.compile(
    r"\bFile\s+No\.?\s+[A-Z0-9][A-Za-z0-9./\-()\s]{3,140}?\(Computer\s+No\.?\s*\d+\)",
    re.IGNORECASE,
)
_EOFFICE_RECEIPT_LINE_RE = re.compile(
    r"^\s*\d{4,10}/20\d{2}/[^\n]{2,80}\s*$",
    re.IGNORECASE,
)
_BARE_PAGE_NUM_LINE_RE = re.compile(r"^\s*\d{1,4}\s*$")

_DEPT_WITHOUT_MINISTRY_PREFIX: frozenset[str] = frozenset(
    {
        "atomic energy",
        "space",
    }
)

_STANDALONE_AUTHORITIES: frozenset[str] = frozenset(
    {
        "prime minister's office",
        "prime ministers office",
        "cabinet secretariat",
        "niti aayog",
        "president's secretariat",
        "vice-president's secretariat",
        "lok sabha secretariat",
        "rajya sabha secretariat",
    }
)

_BENIGN_EXPLANATIONS: tuple[str, ...] = (
    "The Committee on Government Assurances (Lok Sabha Rule 323 / Rajya Sabha Rule 211A) routinely "
    "grants extensions when fulfillment requires Detailed Project Reports (DPRs), inter-ministerial "
    "consultation, state-government inputs, or land acquisition.",
    "A ministry may have transmitted an Implementation Report via the Ministry of Parliamentary Affairs "
    "(OAMS) portal that is awaiting tabling in the next parliamentary session or Digital Sansad register sync.",
    "Scanned Implementation Report PDFs or bare Implementation Statement references reflect legacy "
    "document digitization formats rather than the absence of an underlying reply laid on the Table of the House.",
)


def neutral_text(text: str) -> str:
    """Neutralize BNS Sec 356 prohibited words in quoted third-party parliamentary text."""
    cleaned = _ACB_RE.sub("Vigilance", text or "")
    return _NEUTRAL_REPLACE_RE.sub("compliance-review", cleaned)


def clean_parliament_text(text: Any, *, max_len: int | None = None) -> str:
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


def canonical_ministry_key(name: str) -> str:
    """Normalize a Union Ministry/Department name to a canonical hyphenated key."""
    cleaned = _MULTI_SPACE_RE.sub(" ", str(name or "").replace("&", " and ")).strip().lower()
    cleaned = re.sub(r"^(?:ministry|department)\s+of\s+", "", cleaned)
    cleaned = _NON_ALNUM_SPACE_RE.sub(" ", cleaned)
    cleaned = _MULTI_SPACE_RE.sub(" ", cleaned).strip()
    return cleaned.replace(" ", "-")[:80]


def format_ministry_entity(ministry: str) -> str:
    """Format a clean official Union Ministry / Department entity label without duplicate prefixes."""
    cleaned = clean_parliament_text(ministry, max_len=140)
    if not cleaned:
        return "Union Ministry (Government of India)"
    low = cleaned.lower()
    if low.startswith(("ministry of ", "department of ")):
        return cleaned
    if low in _DEPT_WITHOUT_MINISTRY_PREFIX:
        return f"Department of {cleaned}"
    if low in _STANDALONE_AUTHORITIES or "secretariat" in low or "office" in low:
        return cleaned
    return f"Ministry of {cleaned}"


def normalize_recorded_status(raw_status: Any) -> str:
    """Normalize raw Digital Sansad LS/RS assurance status values into canonical keys."""
    text = _MULTI_SPACE_RE.sub(" ", str(raw_status or "")).strip().upper()
    if not text:
        return "unknown"
    if text in {"PENDING", "P"} or "PENDING" in text:
        return "pending"
    if text in {"FULLY IMPLEMENTED", "FIMP", "IMPLEMENTED", "FULFILLED", "FULLY FULFILLED"}:
        return "fully_implemented"
    if text in {"PARTLY IMPLEMENTED", "PIMP", "PARTIALLY IMPLEMENTED", "PARTLY FULFILLED", "PARTIAL"} or (
        "PART" in text and ("IMPL" in text or "FULF" in text)
    ):
        return "partly_implemented"
    if text in {"DROPPED", "DROP", "DRP"} or "DROP" in text:
        return "dropped"
    return _NON_ALNUM_SPACE_RE.sub("_", text.lower()).strip("_") or "unknown"


def parse_parliament_date(value: Any) -> date | None:
    """Parse Digital Sansad date values across ISO, DD/MM/YYYY, DD.MM.YYYY, and DD-MM-YYYY formats."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value).strip()
    if not raw or raw.lower() in {"null", "none", "na", "n/a", "-", "--"}:
        return None
    head = raw.split("T", 1)[0].split(" ", 1)[0].strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d.%m.%Y", "%d-%m-%Y", "%d-%b-%Y", "%d/%b/%Y"):
        try:
            return datetime.strptime(head, fmt).date()
        except ValueError:
            continue
    for fmt in ("%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def parse_ls_imp_record_no(imp_record_no: Any) -> tuple[str | None, date | None]:
    """Parse Lok Sabha ``impRecordNo`` (e.g. ``SS-1/28/05.08.2026``) into ``(ref, laid_date)``."""
    raw = str(imp_record_no or "").strip()
    if not raw or raw.lower() in {"null", "none", "na", "n/a", "-"}:
        return None, None
    m = _LS_IMP_DATE_RE.search(raw)
    if m:
        try:
            laid = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            return raw, laid
        except ValueError:
            return raw, None
    m_iso = _ANY_ISO_DATE_RE.search(raw)
    if m_iso:
        try:
            laid = date(int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3)))
            return raw, laid
        except ValueError:
            return raw, None
    return raw, None


def parse_remarks_date(remarks: Any) -> date | None:
    """Extract an embedded DD.MM.YYYY / DD/MM/YYYY / YYYY-MM-DD date from a remarks string."""
    raw = str(remarks or "").strip()
    if not raw:
        return None
    m_dmy = _ANY_DMY_DATE_RE.search(raw)
    if m_dmy:
        try:
            return date(int(m_dmy.group(3)), int(m_dmy.group(2)), int(m_dmy.group(1)))
        except ValueError:
            pass
    m_iso = _ANY_ISO_DATE_RE.search(raw)
    if m_iso:
        try:
            return date(int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3)))
        except ValueError:
            pass
    return None


def _indian_fy_from_date(dt: date | None, *, fallback_session: str = "") -> str:
    if dt is not None:
        fy_start = dt.year if dt.month >= 4 else dt.year - 1
        return f"FY {fy_start}-{str(fy_start + 1)[-2:]}"
    if fallback_session:
        return f"Session {fallback_session}"
    return "Unspecified"


def _title_case_person(raw: str) -> str:
    cleaned = _MULTI_SPACE_RE.sub(" ", raw).strip()
    if cleaned.isupper():
        parts = []
        for tok in cleaned.split(" "):
            if tok.upper() in {"DR.", "DR", "SMT.", "SMT", "PROF.", "PROF", "SHRI", "MS.", "MR.", "KM."}:
                parts.append(tok.capitalize())
            else:
                parts.append(tok.capitalize())
        return " ".join(parts)
    return cleaned


def extract_minister_from_pdf(pdf_pages: list[dict[str, Any]]) -> str | None:
    """Extract the answering Minister's name from a Lok Sabha Question & Answer PDF."""
    combined = "\n".join(str(p.get("raw_text") or p.get("text") or "") for p in pdf_pages[:3])
    if not combined.strip():
        return None
    m = _MINISTER_HEADER_RE.search(combined)
    if not m:
        return None
    name = _title_case_person(m.group(1))
    return clean_parliament_text(name, max_len=120) or None


def extract_question_pdf_excerpt(
    pdf_pages: list[dict[str, Any]],
    *,
    subject: str = "",
) -> tuple[int, str, int]:
    """Return ``(page_num, excerpt, total_chars)`` from a Lok Sabha Question & Answer PDF."""
    if not pdf_pages:
        return 1, "", 0
    total_chars = 0
    best_page = 1
    best_text = ""
    subj_tokens = [tok.lower() for tok in re.findall(r"[A-Za-z0-9]{4,}", subject)][:6]

    for idx, rec in enumerate(pdf_pages, start=1):
        pg = rec.get("page")
        page_num = int(pg) if isinstance(pg, int) and not isinstance(pg, bool) and pg > 0 else idx
        raw_txt = str(rec.get("raw_text") or rec.get("text") or "")
        compact = _MULTI_SPACE_RE.sub(" ", raw_txt).strip()
        total_chars += len(compact)
        if not compact:
            continue
        ans_idx = compact.upper().find("ANSWER")
        if ans_idx != -1 and not best_text:
            best_page = page_num
            best_text = compact[ans_idx : ans_idx + 380]
        elif not best_text and subj_tokens and any(tok in compact.lower() for tok in subj_tokens):
            best_page = page_num
            best_text = compact[:380]

    if not best_text:
        for idx, rec in enumerate(pdf_pages, start=1):
            compact = _MULTI_SPACE_RE.sub(" ", str(rec.get("raw_text") or rec.get("text") or "")).strip()
            if compact:
                pg = rec.get("page")
                best_page = int(pg) if isinstance(pg, int) and not isinstance(pg, bool) and pg > 0 else idx
                best_text = compact[:380]
                break

    return best_page, clean_parliament_text(best_text, max_len=360), total_chars


def strip_eoffice_stamps(page_text: str) -> str:
    """Remove Rajya Sabha / GoI eOffice receipt, file-number, and generation footer watermarks.

    Many scanned image-only Rajya Sabha Implementation Report PDFs have zero OCR text of the
    actual ministry reply, yet carry a machine-generated eOffice header/footer stamp on every
    page. Stripping those stamps before measuring text length ensures image-only scans are
    classified as ``scanned_ir_pdf_no_text_layer`` and clerk metadata is never quoted as the
    follow-up answer.
    """
    raw = str(page_text or "")
    if not raw.strip():
        return ""
    stripped_lines: list[str] = []
    had_eoffice = False
    for line in raw.splitlines():
        if _EOFFICE_RECEIPT_LINE_RE.match(line):
            had_eoffice = True
            continue
        cleaned_line = _EOFFICE_FULL_STAMP_RE.sub(" ", line)
        cleaned_line = _EOFFICE_GENERATED_RE.sub(" ", cleaned_line)
        cleaned_line = _EOFFICE_FILE_NO_RE.sub(" ", cleaned_line)
        if cleaned_line != line:
            had_eoffice = True
        stripped_lines.append(cleaned_line)
    if had_eoffice:
        stripped_lines = [ln for ln in stripped_lines if not _BARE_PAGE_NUM_LINE_RE.match(ln)]
    joined = _MULTI_SPACE_RE.sub(" ", " ".join(stripped_lines)).strip()
    # Second pass on whitespace-collapsed text in case an eOffice stamp wrapped across lines.
    collapsed = _EOFFICE_FULL_STAMP_RE.sub(" ", joined)
    collapsed = _EOFFICE_GENERATED_RE.sub(" ", collapsed)
    collapsed = _EOFFICE_FILE_NO_RE.sub(" ", collapsed)
    if collapsed != joined:
        had_eoffice = True
    collapsed = _MULTI_SPACE_RE.sub(" ", collapsed).strip()
    if had_eoffice and _BARE_PAGE_NUM_LINE_RE.match(collapsed):
        return ""
    return collapsed


def extract_ir_pdf_summary(pdf_pages: list[dict[str, Any]]) -> tuple[str, int, int, str, date | None]:
    """Inspect a Rajya Sabha Implementation Report PDF for machine-readable follow-up text.

    Returns ``(followup_text_status, num_pages, total_chars, excerpt, extracted_date)``.
    """
    num_pages = len(pdf_pages)
    if num_pages == 0:
        return "ir_pdf_empty", 0, 0, "", None

    cleaned_pages: list[str] = []
    total_chars = 0
    for rec in pdf_pages:
        compact = strip_eoffice_stamps(str(rec.get("raw_text") or rec.get("text") or ""))
        total_chars += len(compact)
        if compact:
            cleaned_pages.append(compact)

    if total_chars < MIN_READABLE_PDF_CHARS:
        return "scanned_ir_pdf_no_text_layer", num_pages, total_chars, "", None

    combined = " ".join(cleaned_pages)
    extracted_date: date | None = None
    m_date = _IR_FULFILMENT_DATE_RE.search(combined)
    if m_date:
        try:
            if m_date.group(1):
                extracted_date = date(int(m_date.group(3)), int(m_date.group(2)), int(m_date.group(1)))
            elif m_date.group(4):
                extracted_date = date(int(m_date.group(4)), int(m_date.group(5)), int(m_date.group(6)))
        except ValueError:
            extracted_date = None

    m_sec = _IR_SECTION_RE.search(combined)
    if m_sec:
        start = max(0, m_sec.start())
        snippet = combined[start : start + 420]
    else:
        snippet = combined[:420]

    return "readable_ir_pdf", num_pages, total_chars, clean_parliament_text(snippet, max_len=380), extracted_date


def _unwrap_possible_json_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Defensively unwrap nested assurance arrays if a raw JSON envelope was passed directly."""
    out: list[dict[str, Any]] = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        unwrapped = False
        if not any(k in rec for k in ("assuNo", "oassuNo", "textOfAssurance", "extent", "page", "raw_text")):
            for key in ("records", "data", "items", "assurances", "content", "listOfAssurances", "rows"):
                nested = rec.get(key)
                if isinstance(nested, list) and any(isinstance(x, dict) for x in nested):
                    out.extend(x for x in nested if isinstance(x, dict))
                    unwrapped = True
                    break
        if not unwrapped:
            out.append(rec)
    return out


def _split_primary_and_secondary(
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    """Separate primary Digital Sansad JSON assurance rows from optional secondary PDF page rows."""
    primary: list[dict[str, Any]] = []
    secondary: list[dict[str, Any]] = []
    sec_sha: str | None = None

    for rec in _unwrap_possible_json_records(records):
        doc_tag = str(rec.get("document") or "").strip().lower()
        if doc_tag == "secondary":
            secondary.append(rec)
            if not sec_sha and isinstance(rec.get("secondary_sha256"), str) and rec["secondary_sha256"].strip():
                sec_sha = rec["secondary_sha256"].strip().lower()
        elif doc_tag == "primary":
            primary.append(rec)
        elif "page" in rec and ("raw_text" in rec or "text" in rec) and not any(
            k in rec for k in ("assuNo", "oassuNo", "textOfAssurance", "extent")
        ):
            secondary.append(rec)
            if not sec_sha and isinstance(rec.get("secondary_sha256"), str) and rec["secondary_sha256"].strip():
                sec_sha = rec["secondary_sha256"].strip().lower()
        else:
            primary.append(rec)

    return primary, secondary, sec_sha


def _norm_ref(val: Any) -> str:
    return _MULTI_SPACE_RE.sub("", str(val or "")).strip().upper()


def _select_matching_assurance_rows(
    primary_rows: list[dict[str, Any]],
    spec: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Deterministically match the task's target assurance row(s) inside a Digital Sansad JSON response."""
    if not primary_rows:
        return None, []

    house_code = str(spec.get("house_code") or "").strip().upper()
    target_ref = _norm_ref(spec.get("assurance_ref"))
    target_oassu = _norm_ref(spec.get("oassu_no") or target_ref.removeprefix("RS-"))
    target_session = str(spec.get("session") or "").strip()
    target_qno = str(spec.get("question_no") or "").strip()
    target_qtype = str(spec.get("question_type") or "").strip().upper()
    target_member = _MULTI_SPACE_RE.sub(" ", str(spec.get("member") or "")).strip().lower()

    matched: list[dict[str, Any]] = []
    for row in primary_rows:
        if house_code == "RS" or ("oassuNo" in row and "assuNo" not in row):
            row_oassu = _norm_ref(row.get("oassuNo"))
            row_sess = str(row.get("session") or "").strip()
            row_qno = str(row.get("qno") or "").strip()
            if target_oassu and row_oassu and row_oassu == target_oassu:
                matched.append(row)
            elif target_ref and row_oassu and f"RS-{row_oassu}" == target_ref:
                matched.append(row)
            elif target_session and target_qno and row_sess == target_session and row_qno == target_qno:
                matched.append(row)
        else:
            row_assu = _norm_ref(row.get("assuNo"))
            row_sess = str(row.get("session") or "").strip()
            row_qno = str(row.get("qno") or "").strip()
            row_qtype = str(row.get("qtype") or "").strip().upper()
            if target_ref and row_assu and row_assu == target_ref:
                matched.append(row)
            elif (
                target_session
                and target_qno
                and row_sess == target_session
                and row_qno == target_qno
                and (not target_qtype or not row_qtype or row_qtype == target_qtype)
            ):
                matched.append(row)

    if not matched:
        return None, []

    def _row_rank(item: tuple[int, dict[str, Any]]) -> tuple[float, int]:
        idx, r = item
        score = 0.0
        rmem = _MULTI_SPACE_RE.sub(" ", str(r.get("member") or "")).strip().lower()
        if target_member and rmem:
            if rmem == target_member:
                score += 20.0
            elif target_member in rmem or rmem in target_member:
                score += 10.0
        txt = str(r.get("textOfAssurance") or r.get("extent") or r.get("assuranceText") or "").strip()
        if txt:
            score += 5.0
            if re.search(r"[A-Za-z]{4,}", txt):
                score += 3.0
            score += min(len(txt), 600) / 600.0
        return (-score, idx)

    ranked = sorted(enumerate(matched), key=_row_rank)
    return ranked[0][1], matched


@dataclass(frozen=True)
class AssuranceAssessment:
    assurance_key: str
    assurance_ref: str
    house: str
    house_code: str
    loksabha: int | None
    session: str
    question_type: str
    question_no: str
    ministry: str
    ministry_key: str
    entity_name: str
    member: str
    minister: str | None
    subject: str
    promise_date: date | None
    promise_date_iso: str
    as_of_date_iso: str
    fiscal_year: str
    original_promise: str
    recorded_status: str
    raw_status: str
    followup_answer: str
    followup_date_iso: str | None
    followup_text_status: str
    imp_record_no: str | None
    ir_url: str | None
    remarks: str | None
    days_elapsed: int | None
    fulfillment_days: int | None
    extensions_count: int
    extension_upto: str | None
    supplementary_rows_count: int
    secondary_role: str | None
    secondary_pdf_pages: int
    secondary_pdf_chars: int
    question_pdf_page: int | None
    question_pdf_excerpt: str
    flagged: bool
    triggered_rules: list[str]
    signal_category: str
    rule_triggered: str
    severity: str


def evaluate_assurance(
    *,
    recorded_status: str,
    days_elapsed: int | None,
    fulfillment_days: int | None,
    extensions_count: int,
    followup_text_status: str,
) -> tuple[bool, list[str], str, str, str]:
    """Deterministically evaluate a single formal parliamentary assurance against P6 rules."""
    if recorded_status == "pending":
        if days_elapsed is not None and days_elapsed > PARLIAMENTARY_NORM_DAYS:
            triggered = ["pending_exceeds_90d_norm"]
            if extensions_count >= 2:
                triggered.append("multiple_extensions_ge_2")
            if days_elapsed > CRITICAL_PENDING_DAYS or extensions_count >= 2:
                severity = "critical"
            elif days_elapsed > HIGH_PENDING_DAYS:
                severity = "high"
            else:
                severity = "medium"
            return True, triggered, "overdue_pending_assurance", "P6-OVERDUE-PENDING", severity
        return False, [], "", "", "low"

    if recorded_status == "partly_implemented":
        triggered = ["status_partly_implemented"]
        if days_elapsed is not None and days_elapsed > HIGH_PENDING_DAYS:
            triggered.append("elapsed_exceeds_180d")
            severity = "high"
        else:
            severity = "medium"
        return True, triggered, "partly_implemented_assurance", "P6-PARTLY-IMPLEMENTED", severity

    if recorded_status == "dropped":
        triggered = ["dropped_without_fulfillment"]
        severity = "medium" if (days_elapsed is not None and days_elapsed > HIGH_PENDING_DAYS) else "low"
        return True, triggered, "dropped_without_fulfillment", "P6-DROPPED-ASSURANCE", severity

    if recorded_status == "fully_implemented":
        triggered = []
        if fulfillment_days is not None and fulfillment_days > FULFILLMENT_DELAY_DAYS:
            triggered.append("fulfillment_delay_gt_180d")
        if followup_text_status == "scanned_ir_pdf_no_text_layer":
            triggered.append("scanned_ir_pdf_no_text_layer")
        if followup_text_status == "missing_followup_reference":
            triggered.append("missing_followup_reference")
        if triggered:
            severity = (
                "medium"
                if (fulfillment_days is not None and fulfillment_days > SEVERE_FULFILLMENT_DELAY_DAYS)
                else "low"
            )
            return True, triggered, "delayed_or_unreadable_followup", "P6-FOLLOWUP-GAP", severity
        return False, [], "", "", "low"

    return False, [], "", "", "low"


def _extract_int_opt(val: Any) -> int:
    if val is None or isinstance(val, bool) or str(val).strip() == "":
        return 0
    try:
        return max(0, int(float(str(val).strip())))
    except (TypeError, ValueError):
        return 0


def assess_assurance_records(
    records: list[dict[str, Any]],
    *,
    spec: dict[str, Any],
    as_of_date_iso: str,
) -> tuple[AssuranceAssessment | None, str | None]:
    """Extract and evaluate one parliamentary assurance from loaded primary JSON + optional secondary PDF."""
    primary_rows, secondary_pages, sec_sha_from_recs = _split_primary_and_secondary(records)
    row, all_matched = _select_matching_assurance_rows(primary_rows, spec)
    if row is None:
        return None, sec_sha_from_recs

    house_code = str(spec.get("house_code") or ("RS" if "oassuNo" in row and "assuNo" not in row else "LS")).strip().upper()
    house = "Rajya Sabha" if house_code == "RS" else "Lok Sabha"
    loksabha_raw = row.get("loksabha") if row.get("loksabha") is not None else spec.get("loksabha")
    loksabha = _extract_int_opt(loksabha_raw) if (house_code == "LS" and loksabha_raw not in (None, "")) else None
    session = str(row.get("session") or spec.get("session") or "").strip()
    question_type = str(row.get("qtype") or row.get("questionType") or spec.get("question_type") or "").strip()
    question_no = str(row.get("qno") or spec.get("question_no") or "").strip()

    raw_ref = str(row.get("assuNo") or "").strip()
    if not raw_ref and row.get("oassuNo"):
        oassu = str(row["oassuNo"]).strip()
        raw_ref = oassu if oassu.upper().startswith("RS-") else f"RS-{oassu}"
    assurance_ref = str(spec.get("assurance_ref") or raw_ref).strip()
    assurance_key = str(spec.get("assurance_key") or "").strip()

    ministry = clean_parliament_text(
        spec.get("ministry") or str(row.get("ministry") or "").title(),
        max_len=140,
    )
    ministry_key = str(spec.get("ministry_key") or canonical_ministry_key(ministry)).strip()
    entity_name = format_ministry_entity(ministry)
    member = clean_parliament_text(row.get("member") or spec.get("member") or "Member of Parliament", max_len=120)
    subject = clean_parliament_text(row.get("subject") or spec.get("subject") or assurance_ref, max_len=240)

    promise_dt = parse_parliament_date(row.get("date")) or parse_parliament_date(spec.get("promise_date"))
    promise_date_iso = promise_dt.isoformat() if promise_dt else str(spec.get("promise_date") or "").strip()
    as_of_dt = parse_parliament_date(as_of_date_iso) or datetime.now(timezone.utc).date()
    days_elapsed = max(0, (as_of_dt - promise_dt).days) if promise_dt is not None else None
    fiscal_year = _indian_fy_from_date(promise_dt, fallback_session=session)

    raw_status = clean_parliament_text(row.get("status") or spec.get("raw_status") or "", max_len=60)
    recorded_status = normalize_recorded_status(raw_status or spec.get("recorded_status"))

    secondary_role_raw = str(spec.get("secondary_role") or "").strip().lower()
    secondary_role = secondary_role_raw if secondary_role_raw in {"question_pdf", "implementation_report_pdf"} else None

    minister: str | None = None
    question_pdf_page: int | None = None
    question_pdf_excerpt = ""
    secondary_pdf_pages = len(secondary_pages)
    secondary_pdf_chars = 0
    ir_text_status = ""
    ir_excerpt = ""
    ir_extracted_date: date | None = None

    if secondary_role == "question_pdf" and secondary_pages:
        minister = extract_minister_from_pdf(secondary_pages)
        question_pdf_page, question_pdf_excerpt, secondary_pdf_chars = extract_question_pdf_excerpt(
            secondary_pages,
            subject=subject,
        )
    elif secondary_role == "implementation_report_pdf" and secondary_pages:
        ir_text_status, secondary_pdf_pages, secondary_pdf_chars, ir_excerpt, ir_extracted_date = extract_ir_pdf_summary(
            secondary_pages
        )

    # 1. Original promise text
    raw_promise = row.get("textOfAssurance") or row.get("extent") or row.get("assuranceText") or ""
    original_promise = clean_parliament_text(raw_promise, max_len=460)
    if not original_promise and question_pdf_excerpt:
        original_promise = question_pdf_excerpt
    if not original_promise:
        original_promise = f"Assurance recorded on {house} {assurance_ref} regarding {subject} (verbatim text field empty in Digital Sansad API)."

    supplementary_rows_count = max(0, len(all_matched) - 1)

    # 2. Extensions, remarks, impRecordNo, irUrl
    extensions_count = _extract_int_opt(row.get("extNo") or row.get("extensions"))
    ext_upto_dt = (
        parse_parliament_date(row.get("extUpto"))
        or parse_parliament_date(row.get("egranted"))
        or parse_parliament_date(row.get("esought"))
    )
    extension_upto = ext_upto_dt.isoformat() if ext_upto_dt else (
        clean_parliament_text(row.get("extUpto") or row.get("egranted") or "", max_len=32) or None
    )
    remarks_clean = clean_parliament_text(row.get("remarks"), max_len=280) or None
    imp_record_no, imp_laid_date = parse_ls_imp_record_no(row.get("impRecordNo"))
    raw_ir_url = str(row.get("irUrl") or "").strip()
    ir_url = raw_ir_url if raw_ir_url.startswith("https://") else None

    # 3. What the follow-up actually answered
    followup_date: date | None = None
    fulfillment_days: int | None = None
    followup_text_status = "no_implementation_statement"

    if recorded_status == "pending":
        followup_text_status = "no_implementation_statement"
        elapsed_clause = f"{days_elapsed} days elapsed since {promise_date_iso}" if days_elapsed is not None else "promise date unrecorded"
        ext_clause = ""
        if extensions_count > 0 or extension_upto:
            ext_parts = []
            if extensions_count > 0:
                ext_parts.append(f"{extensions_count} extension(s) recorded")
            if extension_upto:
                ext_parts.append(f"extended up to {extension_upto}")
            ext_clause = f"; {'; '.join(ext_parts)}"
        followup_answer = (
            f"No Implementation Statement has been laid on the Table of the {house} as of {as_of_date_iso}; "
            f"Digital Sansad still lists {assurance_ref} as {raw_status or 'PENDING'} ({elapsed_clause}{ext_clause})."
        )
    elif recorded_status == "partly_implemented":
        followup_date = imp_laid_date or parse_remarks_date(remarks_clean) or ir_extracted_date
        if followup_date is not None and promise_dt is not None:
            fulfillment_days = max(0, (followup_date - promise_dt).days)
        if secondary_role == "implementation_report_pdf" and ir_text_status == "readable_ir_pdf" and ir_excerpt:
            followup_text_status = "readable_ir_pdf"
            followup_answer = (
                f"Partly implemented; Implementation Report PDF ({secondary_pdf_pages} page(s)) reports: {ir_excerpt}"
            )
        elif secondary_role == "implementation_report_pdf" and ir_text_status == "scanned_ir_pdf_no_text_layer":
            followup_text_status = "scanned_ir_pdf_no_text_layer"
            followup_answer = (
                f"Recorded as {raw_status}; linked Implementation Report PDF ({secondary_pdf_pages} page(s)) is a "
                f"scanned image without a machine-readable text layer ({secondary_pdf_chars} chars extracted), and full fulfillment remains pending."
            )
        elif imp_record_no:
            followup_text_status = "statement_ref_only"
            date_clause = (
                f" (laid {followup_date.isoformat()}, {fulfillment_days} days after assurance)"
                if followup_date is not None and fulfillment_days is not None
                else (f" (laid {followup_date.isoformat()})" if followup_date is not None else "")
            )
            followup_answer = (
                f"Partly implemented under Implementation Statement {imp_record_no}{date_clause}; "
                f"full fulfillment remains pending and Digital Sansad does not attach a full-text follow-up transcript to this record."
            )
        else:
            followup_text_status = "missing_followup_reference"
            followup_answer = (
                f"Recorded as {raw_status} on Digital Sansad without a linked Implementation Statement reference "
                f"or full-text follow-up answer; full fulfillment remains pending."
            )
    elif recorded_status == "dropped":
        followup_text_status = "dropped_by_committee"
        followup_date = parse_remarks_date(remarks_clean)
        if followup_date is not None and promise_dt is not None:
            fulfillment_days = max(0, (followup_date - promise_dt).days)
        remark_clause = f" ({remarks_clean})" if remarks_clean else ""
        followup_answer = (
            f"Assurance was closed as {raw_status or 'DROPPED'} without a laid Implementation Statement{remark_clause}."
        )
    elif recorded_status == "fully_implemented":
        # Resolve fulfillment date from LS impRecordNo, explicit RS fields, remarks, or readable IR PDF
        explicit_rs_date = None
        for dkey in ("implDate", "impDate", "fulfilledDate", "fulfilmentDate", "dateOfFulfilment", "laidDate", "laidOn"):
            explicit_rs_date = parse_parliament_date(row.get(dkey))
            if explicit_rs_date is not None:
                break
        followup_date = imp_laid_date or explicit_rs_date or ir_extracted_date or parse_remarks_date(remarks_clean)
        if followup_date is not None and promise_dt is not None:
            fulfillment_days = max(0, (followup_date - promise_dt).days)

        if secondary_role == "implementation_report_pdf" and secondary_pages:
            if ir_text_status == "readable_ir_pdf" and ir_excerpt:
                followup_text_status = "readable_ir_pdf"
                date_clause = (
                    f", fulfilled in {fulfillment_days} days ({followup_date.isoformat()})"
                    if followup_date is not None and fulfillment_days is not None
                    else ""
                )
                followup_answer = (
                    f"Implementation Report PDF ({secondary_pdf_pages} page(s), {secondary_pdf_chars:,} chars{date_clause}) "
                    f"reports: {ir_excerpt}"
                )
            else:
                followup_text_status = "scanned_ir_pdf_no_text_layer"
                followup_answer = (
                    f"Digital Sansad marks {assurance_ref} as {raw_status or 'FIMP'} and links an Implementation Report PDF "
                    f"({secondary_pdf_pages} page(s)), but it is a scanned image-only PDF ({secondary_pdf_chars} machine-readable chars extracted); "
                    f"what the follow-up actually answered cannot be verified without OCR or manual review."
                )
        elif imp_record_no:
            followup_text_status = "statement_ref_only"
            date_clause = (
                f" (laid {followup_date.isoformat()}, {fulfillment_days} days after assurance)"
                if followup_date is not None and fulfillment_days is not None
                else (f" (laid {followup_date.isoformat()})" if followup_date is not None else "")
            )
            followup_answer = (
                f"Recorded as {raw_status or 'FULLY IMPLEMENTED'} under Implementation Statement {imp_record_no}{date_clause}; "
                f"Digital Sansad lists the statement citation without attaching an inline answer transcript."
            )
        elif ir_url:
            followup_text_status = "ir_url_unloaded"
            followup_answer = (
                f"Recorded as {raw_status or 'FIMP'} with official Implementation Report link ({ir_url}); "
                f"the PDF was not attached to this run for full-text extraction."
            )
        else:
            followup_text_status = "missing_followup_reference"
            followup_answer = (
                f"Recorded as {raw_status or 'FULLY IMPLEMENTED'} on Digital Sansad, but neither an Implementation "
                f"Statement reference (impRecordNo) nor an Implementation Report PDF (irUrl) is linked."
            )
    else:
        followup_text_status = "unknown_status"
        followup_answer = f"Digital Sansad records status '{raw_status or 'unknown'}' with no follow-up transcript attached."

    followup_answer_clean = clean_parliament_text(followup_answer, max_len=520)

    flagged, triggered_rules, signal_category, rule_triggered, severity = evaluate_assurance(
        recorded_status=recorded_status,
        days_elapsed=days_elapsed,
        fulfillment_days=fulfillment_days,
        extensions_count=extensions_count,
        followup_text_status=followup_text_status,
    )

    return (
        AssuranceAssessment(
            assurance_key=assurance_key,
            assurance_ref=assurance_ref,
            house=house,
            house_code=house_code,
            loksabha=loksabha,
            session=session,
            question_type=question_type,
            question_no=question_no,
            ministry=ministry,
            ministry_key=ministry_key,
            entity_name=entity_name,
            member=member,
            minister=minister,
            subject=subject,
            promise_date=promise_dt,
            promise_date_iso=promise_date_iso,
            as_of_date_iso=as_of_date_iso,
            fiscal_year=fiscal_year,
            original_promise=original_promise,
            recorded_status=recorded_status,
            raw_status=raw_status or recorded_status.upper(),
            followup_answer=followup_answer_clean,
            followup_date_iso=followup_date.isoformat() if followup_date is not None else None,
            followup_text_status=followup_text_status,
            imp_record_no=imp_record_no,
            ir_url=ir_url,
            remarks=remarks_clean,
            days_elapsed=days_elapsed,
            fulfillment_days=fulfillment_days,
            extensions_count=extensions_count,
            extension_upto=extension_upto,
            supplementary_rows_count=supplementary_rows_count,
            secondary_role=secondary_role,
            secondary_pdf_pages=secondary_pdf_pages,
            secondary_pdf_chars=secondary_pdf_chars,
            question_pdf_page=question_pdf_page,
            question_pdf_excerpt=question_pdf_excerpt,
            flagged=flagged,
            triggered_rules=triggered_rules,
            signal_category=signal_category,
            rule_triggered=rule_triggered,
            severity=severity,
        ),
        sec_sha_from_recs,
    )


def run_p6_parliamentary_assurances(
    records: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
) -> tuple[list[AuditLeadCandidate], int, float, str, dict[str, Any]]:
    """Run deterministic P6 parliamentary-assurance verification on Digital Sansad records."""
    ctx = context or {}
    spec = ctx.get("parliaments_to_do_list") if isinstance(ctx.get("parliaments_to_do_list"), dict) else {}
    as_of_iso = str(ctx.get("as_of_date") or "").strip()[:10] or datetime.now(timezone.utc).date().isoformat()

    retrieval_url = str(ctx.get("retrieval_url") or ctx.get("dataset_url") or "").strip()
    secondary_url = str(ctx.get("secondary_url") or ctx.get("secondary_dataset_url") or "").strip()
    portal_url = str(
        spec.get("portal_url") or ctx.get("source_landing_page") or "https://sansad.in"
    ).strip()
    expected_sha = str(ctx.get("expected_sha256") or "").strip().lower() or None
    expected_sec_sha = (
        str(ctx.get("secondary_expected_sha256") or ctx.get("secondary_sha256") or "").strip().lower() or None
    )
    official_publisher = str(
        ctx.get("official_publisher")
        or "Parliament of India — Digital Sansad (Lok Sabha / Rajya Sabha Secretariat)"
    ).strip()

    meta: dict[str, Any] = {
        "as_of_date": as_of_iso,
        "amount_type": "parliamentary_assurances",
        "unevaluable_count": 0,
    }

    if not records or not spec or not spec.get("assurance_ref"):
        meta["unevaluable_count"] = 1
        return (
            [],
            0,
            0.0,
            "P6 read 0 assurance records: missing Digital Sansad payload or parliaments_to_do_list specification.",
            meta,
        )

    assessment, sec_sha_from_recs = assess_assurance_records(records, spec=spec, as_of_date_iso=as_of_iso)
    if assessment is None:
        meta["unevaluable_count"] = 1
        return (
            [],
            0,
            0.0,
            f"P6 could not locate matching assurance {spec.get('assurance_ref')!r} in the Digital Sansad response.",
            meta,
        )

    resolved_sec_sha = expected_sec_sha or sec_sha_from_recs
    source_locator = f"{assessment.house} {assessment.assurance_ref} ({assessment.promise_date_iso or 'undated'})"

    if not assessment.flagged:
        cleared_summary = clean_parliament_text(
            f"P6 cleared {assessment.house_code} {assessment.assurance_ref} ({assessment.ministry}): "
            f"Original promise ({assessment.promise_date_iso}): \"{assessment.original_promise[:220]}\". "
            f"Recorded status: {assessment.raw_status}"
            + (
                f" ({assessment.days_elapsed} days elapsed as of {assessment.as_of_date_iso})"
                if assessment.days_elapsed is not None
                else ""
            )
            + f". Follow-up: {assessment.followup_answer}",
            max_len=1200,
        )
        return [], 1, 0.0, cleared_summary, meta

    subject_short = clean_parliament_text(assessment.subject, max_len=78)
    if assessment.signal_category == "overdue_pending_assurance":
        title = (
            f"{assessment.ministry} ({assessment.house_code} {assessment.assurance_ref}): "
            f"assurance pending {assessment.days_elapsed} days — {subject_short}"
        )
    elif assessment.signal_category == "partly_implemented_assurance":
        elapsed_txt = f"{assessment.days_elapsed} days" if assessment.days_elapsed is not None else "open"
        title = (
            f"{assessment.ministry} ({assessment.house_code} {assessment.assurance_ref}): "
            f"partly implemented ({elapsed_txt}) — {subject_short}"
        )
    elif assessment.signal_category == "dropped_without_fulfillment":
        title = (
            f"{assessment.ministry} ({assessment.house_code} {assessment.assurance_ref}): "
            f"assurance dropped without fulfillment — {subject_short}"
        )
    elif assessment.followup_text_status == "scanned_ir_pdf_no_text_layer":
        title = (
            f"{assessment.ministry} ({assessment.house_code} {assessment.assurance_ref}): "
            f"scanned Implementation Report PDF lacks text layer — {subject_short}"
        )
    elif assessment.fulfillment_days is not None and assessment.fulfillment_days > FULFILLMENT_DELAY_DAYS:
        title = (
            f"{assessment.ministry} ({assessment.house_code} {assessment.assurance_ref}): "
            f"fulfilled after {assessment.fulfillment_days} days (>180d) — {subject_short}"
        )
    else:
        title = (
            f"{assessment.ministry} ({assessment.house_code} {assessment.assurance_ref}): "
            f"fulfilled without linked follow-up statement — {subject_short}"
        )
    title = clean_parliament_text(title, max_len=180)

    minister_clause = f", answered by {assessment.minister}" if assessment.minister else ""
    supp_clause = (
        f" (+{assessment.supplementary_rows_count} supplementary assurance row(s) on the same question)"
        if assessment.supplementary_rows_count > 0
        else ""
    )
    elapsed_clause = (
        f"{assessment.days_elapsed} days elapsed as of {assessment.as_of_date_iso}"
        if assessment.days_elapsed is not None
        else f"checked as of {assessment.as_of_date_iso}"
    )
    ext_clause = (
        f"; {assessment.extensions_count} extension(s)"
        + (f" up to {assessment.extension_upto}" if assessment.extension_upto else "")
        if assessment.extensions_count > 0 or assessment.extension_upto
        else ""
    )
    summary = clean_parliament_text(
        f"Original promise ({assessment.house} {assessment.assurance_ref}, {assessment.promise_date_iso}, "
        f"{assessment.entity_name}, asked by {assessment.member}{minister_clause}): "
        f"\"{assessment.original_promise}\"{supp_clause}. "
        f"Recorded status: {assessment.raw_status} ({elapsed_clause}{ext_clause}). "
        f"Follow-up answer: {assessment.followup_answer} "
        f"Published from official Digital Sansad parliamentary assurance records for public review.",
        max_len=1500,
    )

    # Build 3-part (or 4-part with Lok Sabha Question PDF) linked pinpoint citations
    citations: list[dict[str, Any]] = []

    # Citation 1: Original Promise (Digital Sansad API JSON)
    citations.append(
        {
            "title": "Original Promise",
            "document": f"Digital Sansad {assessment.house} Assurances API ({assessment.assurance_ref})",
            "role": "original_promise",
            "source_url": retrieval_url or portal_url,
            "landing_url": portal_url,
            "official_publisher": official_publisher,
            "source_locator": f"{assessment.house} {assessment.assurance_ref} · {assessment.promise_date_iso} · {assessment.ministry}",
            "sha256": expected_sha,
            "excerpt": assessment.original_promise,
        }
    )

    # Optional Citation 1b: Lok Sabha Question & Answer PDF (distinct SHA-256 -> links directly to PDF)
    if assessment.secondary_role == "question_pdf" and secondary_url and assessment.secondary_pdf_pages > 0:
        qpdf_prefix = f"Minister: {assessment.minister}. " if assessment.minister else ""
        qpdf_body = assessment.question_pdf_excerpt or assessment.subject
        citations.append(
            {
                "title": "Original Question & Answer PDF",
                "document": f"Lok Sabha {assessment.question_type} {assessment.question_no} Official Answer PDF",
                "role": "original_question_pdf",
                "source_url": secondary_url,
                "landing_url": portal_url,
                "official_publisher": official_publisher,
                "page": assessment.question_pdf_page or 1,
                "source_locator": f"{assessment.assurance_ref} Question PDF p.{assessment.question_pdf_page or 1}",
                "sha256": resolved_sec_sha,
                "excerpt": clean_parliament_text(f"{qpdf_prefix}{qpdf_body}", max_len=360),
            }
        )

    # Citation 2: Recorded Status (Digital Sansad Assurance Register)
    status_parts = [
        f"Status={assessment.raw_status}",
        f"Promise Date={assessment.promise_date_iso or 'unrecorded'}",
        f"As Of={assessment.as_of_date_iso}",
    ]
    if assessment.days_elapsed is not None:
        status_parts.append(f"Elapsed={assessment.days_elapsed} days (90-day norm)")
    if assessment.extensions_count > 0:
        status_parts.append(f"Extensions={assessment.extensions_count}")
    if assessment.extension_upto:
        status_parts.append(f"Extended Upto={assessment.extension_upto}")
    if assessment.imp_record_no:
        status_parts.append(f"Implementation Ref={assessment.imp_record_no}")
    if assessment.remarks:
        status_parts.append(f"Remarks={assessment.remarks}")
    if assessment.supplementary_rows_count > 0:
        status_parts.append(f"Supplementary Rows={assessment.supplementary_rows_count}")

    citations.append(
        {
            "title": "Recorded Status",
            "document": f"Digital Sansad {assessment.house} Assurance Register ({assessment.assurance_ref})",
            "role": "recorded_status",
            "source_url": retrieval_url or portal_url,
            "landing_url": portal_url,
            "official_publisher": official_publisher,
            "source_locator": f"{assessment.house} {assessment.assurance_ref} · Status: {assessment.raw_status}",
            "sha256": expected_sha,
            "excerpt": clean_parliament_text(" | ".join(status_parts), max_len=400),
        }
    )

    # Citation 3: Follow-Up / Implementation Statement (links directly to RS IR PDF when attached)
    is_ir_pdf = assessment.secondary_role == "implementation_report_pdf" and bool(secondary_url)
    followup_cit: dict[str, Any] = {
        "title": "Follow-Up / Implementation Statement",
        "document": (
            f"Rajya Sabha Implementation Report PDF ({assessment.assurance_ref})"
            if is_ir_pdf
            else (
                f"Lok Sabha Implementation Statement {assessment.imp_record_no}"
                if assessment.imp_record_no
                else f"Digital Sansad Follow-Up Status ({assessment.assurance_ref})"
            )
        ),
        "role": "follow_up_answer",
        "source_url": secondary_url if is_ir_pdf else (retrieval_url or portal_url),
        "landing_url": portal_url,
        "official_publisher": official_publisher,
        "source_locator": (
            f"RS Implementation Report PDF ({assessment.assurance_ref}, {assessment.secondary_pdf_pages}p)"
            if is_ir_pdf
            else (
                f"Implementation Statement {assessment.imp_record_no}"
                if assessment.imp_record_no
                else f"Follow-up check as of {assessment.as_of_date_iso}"
            )
        ),
        "sha256": resolved_sec_sha if is_ir_pdf else expected_sha,
        "excerpt": assessment.followup_answer,
    }
    if is_ir_pdf and assessment.secondary_pdf_pages > 0:
        followup_cit["page"] = 1
    citations.append(followup_cit)

    lead = AuditLeadCandidate(
        title=title,
        summary=summary,
        methodology_code=PARLIAMENTS_TO_DO_LIST_METHODOLOGY,
        entity_name=assessment.entity_name[:160],
        jurisdiction="Union of India",
        fiscal_year=assessment.fiscal_year,
        monetary_exposure_inr=0.0,
        severity=assessment.severity,  # type: ignore[arg-type]
        anomaly_metrics={
            "assurance_key": assessment.assurance_key,
            "assurance_ref": assessment.assurance_ref,
            "house": assessment.house,
            "house_code": assessment.house_code,
            "loksabha": assessment.loksabha,
            "session": assessment.session,
            "question_type": assessment.question_type,
            "question_no": assessment.question_no,
            "ministry": assessment.ministry,
            "ministry_key": assessment.ministry_key,
            "member": assessment.member,
            "minister": assessment.minister,
            "subject": assessment.subject,
            "promise_date": assessment.promise_date_iso or None,
            "as_of_date": assessment.as_of_date_iso,
            "original_promise": assessment.original_promise,
            "recorded_status": assessment.recorded_status,
            "raw_status": assessment.raw_status,
            "followup_answer": assessment.followup_answer,
            "followup_date": assessment.followup_date_iso,
            "followup_text_status": assessment.followup_text_status,
            "imp_record_no": assessment.imp_record_no,
            "ir_url": assessment.ir_url,
            "remarks": assessment.remarks,
            "days_elapsed": assessment.days_elapsed,
            "fulfillment_days": assessment.fulfillment_days,
            "extensions_count": assessment.extensions_count,
            "extension_upto": assessment.extension_upto,
            "supplementary_rows_count": assessment.supplementary_rows_count,
            "secondary_role": assessment.secondary_role,
            "secondary_pdf_pages": assessment.secondary_pdf_pages,
            "secondary_pdf_chars": assessment.secondary_pdf_chars,
            "signal_category": assessment.signal_category,
            "triggered_rules": assessment.triggered_rules,
            "norm_days": PARLIAMENTARY_NORM_DAYS,
            "source_locator": source_locator,
            "is_anchor_case": bool(spec.get("is_anchor_case")),
        },
        rule_triggered=assessment.rule_triggered,
        source_locator=source_locator,
        citations=citations,
        benign_explanations=list(_BENIGN_EXPLANATIONS),
        suggested_authority=f"{assessment.entity_name}, Government of India",
        amount_type="parliamentary_assurances",
    )

    run_summary = (
        f"P6 flagged {assessment.house_code} {assessment.assurance_ref} ({assessment.ministry}): "
        f"{assessment.signal_category} (status={assessment.raw_status}, days_elapsed={assessment.days_elapsed})."
    )
    return [lead], 0, 0.0, run_summary, meta


def validate_context(ctx: Any, where: str) -> list[str]:
    """Validate ``payload.context.parliaments_to_do_list`` for a P6 task."""
    errors: list[str] = []
    if not isinstance(ctx, dict):
        return [f"{where}.payload.context.parliaments_to_do_list is required for P6 tasks."]
    spec = ctx.get("parliaments_to_do_list")
    if not isinstance(spec, dict):
        return [f"{where}.payload.context.parliaments_to_do_list must be an object."]

    akey = spec.get("assurance_key")
    if not isinstance(akey, str) or not _KEY_RE.fullmatch(akey):
        errors.append(
            f"{where}.payload.context.parliaments_to_do_list.assurance_key must match [a-z0-9-] (<= 59 chars)."
        )

    house = spec.get("house")
    house_code = spec.get("house_code")
    if house not in {"Lok Sabha", "Rajya Sabha"}:
        errors.append(
            f"{where}.payload.context.parliaments_to_do_list.house must be 'Lok Sabha' or 'Rajya Sabha'."
        )
    if house_code not in {"LS", "RS"}:
        errors.append(
            f"{where}.payload.context.parliaments_to_do_list.house_code must be 'LS' or 'RS'."
        )
    elif (house == "Lok Sabha" and house_code != "LS") or (house == "Rajya Sabha" and house_code != "RS"):
        errors.append(
            f"{where}.payload.context.parliaments_to_do_list.house and house_code do not match."
        )

    session = spec.get("session")
    if not isinstance(session, str) or not session.strip() or len(session) > 32:
        errors.append(f"{where}.payload.context.parliaments_to_do_list.session is required (<= 32 chars).")

    aref = spec.get("assurance_ref")
    if not isinstance(aref, str) or not aref.strip() or len(aref) > 64:
        errors.append(f"{where}.payload.context.parliaments_to_do_list.assurance_ref is required (<= 64 chars).")

    ministry = spec.get("ministry")
    if not isinstance(ministry, str) or not ministry.strip() or len(ministry) > 160:
        errors.append(f"{where}.payload.context.parliaments_to_do_list.ministry is required (<= 160 chars).")

    mkey = spec.get("ministry_key")
    if not isinstance(mkey, str) or not _MINISTRY_KEY_RE.fullmatch(mkey):
        errors.append(
            f"{where}.payload.context.parliaments_to_do_list.ministry_key must match [a-z0-9-] (<= 80 chars)."
        )

    rec_status = spec.get("recorded_status")
    if rec_status not in VALID_RECORDED_STATUSES:
        errors.append(
            f"{where}.payload.context.parliaments_to_do_list.recorded_status must be one of {sorted(VALID_RECORDED_STATUSES)}."
        )

    pdate = spec.get("promise_date")
    if pdate not in (None, ""):
        if not isinstance(pdate, str) or not _ISO_DATE_RE.fullmatch(pdate.strip()) or parse_parliament_date(pdate) is None:
            errors.append(
                f"{where}.payload.context.parliaments_to_do_list.promise_date must be a valid YYYY-MM-DD date."
            )

    sec_role = spec.get("secondary_role")
    if sec_role is not None and sec_role not in VALID_SECONDARY_ROLES:
        errors.append(
            f"{where}.payload.context.parliaments_to_do_list.secondary_role must be 'question_pdf', 'implementation_report_pdf', or null."
        )

    portal_url = spec.get("portal_url")
    if portal_url not in (None, ""):
        try:
            validate_safe_http_url(str(portal_url), require_https=True, allow_private=False)
        except ValueError:
            errors.append(
                f"{where}.payload.context.parliaments_to_do_list.portal_url must be a public https:// URL."
            )

    ref_url = ctx.get("retrieval_url")
    if ref_url is not None:
        try:
            validate_safe_http_url(str(ref_url), require_https=True, allow_private=False)
        except ValueError:
            errors.append(f"{where}.payload.context.retrieval_url must be a public https:// URL.")

    return errors
