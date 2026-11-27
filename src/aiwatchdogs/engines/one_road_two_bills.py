"""OR1: One Road, Two Bills — duplicate billing & dual-agency claim detection on Union CAG audit reports.

Reads pinned official Comptroller and Auditor General (CAG) of India Union Government audit
report PDFs page by page and evaluates non-overlapping paragraph/section windows for
documented irregularities where a finished work, project, or expenditure was billed or paid
twice or claimed across two agencies, departments, ministries, or schemes.

Legitimate co-funding and cost-sharing arrangements (e.g., 60:40 or 50:50 Centre-State
matching grants, VGF, pooled funding) and audit-methodology / criteria / TOC boilerplate
are explicitly separated and cleared unless an explicit duplicate-billing irregularity is
documented. Every lead cites exact report number, page number, paragraph/section anchor,
SHA-256 checksum, and neutralized excerpt.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from aiwatchdogs.core.models import AuditLeadCandidate
from aiwatchdogs.core.safety import validate_safe_http_url

ONE_ROAD_TWO_BILLS_PROJECT_SLUG = "one-road-two-bills"
ONE_ROAD_TWO_BILLS_METHODOLOGY = "OR1"
MAX_WINDOWS_PER_TASK = 20
MAX_ENTITY_LENGTH = 200
MIN_BLOCK_CHARS = 250
_MAX_EXCERPT_CHARS = 360
_INR_CRORE = 10_000_000.0
_INR_LAKH = 100_000.0
_INR_THOUSAND = 1_000.0

SIGNAL_CATEGORIES: tuple[str, ...] = (
    "duplicate_payment_or_billing",
    "dual_agency_or_scheme_overlap",
)

_YEAR_RE = re.compile(r"^20\d{2}$")
_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_SECTION_NUMBER_RE = re.compile(r"\d+(?:\.\d+)+")
_ROMAN_PAGE_RE = re.compile(r"[ivxlcdm]+", re.IGNORECASE)
_REPORT_HEADER_RE = re.compile(r"Report No\.\s*\d+ of 20\d{2}", re.IGNORECASE)
_SUMMARY_REFERENCE_RE = re.compile(r"\(Para(?:graph)?\.?\s+(\d+(?:\.\d+)+)\)", re.IGNORECASE)
_AMOUNT_FOOTNOTE_RE = re.compile(
    r"\b(crore|cr\.?|lakhs?|thousand)(?:[0-9]{1,3}|[⁰¹²³⁴⁵⁶⁷⁸⁹]{1,3})"
    r"(?:\s+[0-9]{1,3}(?=\s))?(?!\w)", re.IGNORECASE,
)
_NUMBERED_HEADING_RE = re.compile(
    r"^\s*(?:(?:Para(?:graph)?\.?\s*)?(\d+(?:\.\d+)+)\s+([A-Z][^\n]{5,140})|"
    r"((?:Chapter|CHAPTER)\s+[IVXLC\d]+)\s*[:\-–]?\s*([^\n]{0,120}))$"
)
_TOC_LINE_RE = re.compile(r"(?:\.{3,}|…|\s{3,}\d{1,3})\s*$")
_PAGE_NUMBER_ONLY_RE = re.compile(r"^\s*(?:page\s*)?\d{1,4}\s*$", re.IGNORECASE)
_FIGURE_CHART_RE = re.compile(r"^\s*(?:fig(?:ure)?\.?|chart|graph|exhibit)\s+\d+(?:\.\d+)*\b", re.IGNORECASE)

# BNS Sec 356 neutralizer (mirrors safety._FORBIDDEN_TERM_PATTERNS).
_ACB_RE = re.compile(r"\banti[\s-]+corruption\b", re.IGNORECASE)
_NEUTRAL_REPLACE_RE = re.compile(
    r"(?<![a-z])(?:scam(?:s|med|mer|mers)?|corrupt(?:ed|ing|ly|ion)?|brib(?:e|es|ed|ing|ery)|"
    r"fraud(?:s|ulent|ulently|ster|sters)?|kickback(?:s)?|guilty|stolen|rigged|"
    r"crook(?:s|ed)?|loot(?:s|ed|ing|er|ers)?|embezzl(?:e|es|ed|ing|ement))(?![a-z])",
    re.IGNORECASE,
)

# Abbreviations whose trailing dot must not split sentences.
_ABBREV_DOT_RE = re.compile(
    r"\b(Rs|Cr|No|Nos|Sr|Dr|Mr|Mrs|Ms|Fig|Sec|Art|Vol|Ltd|Pvt|Para|Dept|Govt|pp|p|M/s|sl)\.\s+",
    re.IGNORECASE,
)

_RUPEE_AMOUNT_RE = re.compile(
    r"(?i)(?:₹|INR|Rs\.?)\s*([\d,]+(?:\.\d+)?)\s*(crore|cr\.?|lakhs?|thousand)"
    r"(?:[0-9]{1,3}|[⁰¹²³⁴⁵⁶⁷⁸⁹]{1,3})?(?!\w)"
)

# Audit-methodology / criteria / recommendation / boilerplate sentences to exclude.
_METHODOLOGY_BOILERPLATE_RE = re.compile(
    r"(?i)\b(?:"
    r"to\s+(?:verify|ascertain|examine|check|assess|ensure)\s+whether|"
    r"should\s+ensure\s+(?:that|no)|"
    r"adequate\s+(?:checks|controls|system)\s+to\s+(?:prevent|avoid|detect)|"
    r"risk\s+of\s+(?:duplicate|double)|"
    r"possibility\s+of\s+(?:duplicate|double)|"
    r"prevent\s+(?:duplicate|double)|"
    r"avoid\s+(?:duplicate|double)|"
    r"audit\s+(?:objective|objectives|criteria|methodology|scope|approach|sampling)|"
    r"manual\s+of\s+(?:standing\s+orders|local\s+audit)|"
    r"table\s+of\s+contents|"
    r"list\s+of\s+(?:abbreviations|acronyms|appendices|annexures|tables)"
    r")\b"
)

# Legitimate co-funding / cost-sharing language (must NOT be flagged unless strong double-billing exists).
_LEGITIMATE_COFUNDING_RE = re.compile(
    r"(?i)\b(?:"
    r"cost[\s-]+shar(?:e|ed|ing)|"
    r"shared\s+(?:on|in)\s+(?:the\s+)?(?:ratio\s+of\s+)?\d+\s*:\s*\d+|"
    r"funding\s+pattern\s+(?:of\s+)?\d+\s*:\s*\d+|"
    r"centre[\s-]+state\s+shar(?:e|ing)|"
    r"central\s+share\s+and\s+state\s+share|"
    r"co[\s-]*fund(?:ed|ing)|"
    r"co[\s-]*financ(?:ed|ing)|"
    r"jointly\s+funded|"
    r"matching\s+(?:grant|contribution|share)|"
    r"counterpart\s+funding|"
    r"viability\s+gap\s+funding|"
    r"pooled\s+funding|"
    r"convergence\s+of\s+schemes?\s+as\s+per\s+guidelines"
    r")\b"
)

# Strong explicit duplicate-billing / double-payment / two-receipt signals.
_STRONG_DUPLICATE_BILLING_RE = re.compile(
    r"(?i)(?<!-)\b(?:"
    r"double\s+payments?|duplicate\s+payments?|"
    r"paid\s+twice|billed\s+twice|"
    r"double\s+billing|duplicate\s+billing|"
    r"duplicate\s+bills?|double\s+bills?|"
    r"double\s+booking|booked\s+twice|"
    r"twice\s+for\s+the\s+same|"
    r"two\s+bills|two\s+vouchers|two\s+receipts|"
    r"duplicate\s+vouchers?|double\s+vouchers?|"
    r"duplicate\s+invoices?|same\s+invoice|same\s+bill|same\s+voucher|"
    r"reimbursed\s+twice|claimed\s+twice|drawn\s+twice|"
    r"double\s+drawal|duplicate\s+claims?|double\s+claims?|"
    r"double\s+reimbursement|double\s+disbursement|"
    r"paid\s+again|re[\s-]?billed|"
    r"already\s+paid\b[^\n.]{0,60}\bagain"
    r")\b"
)

# Dual-agency / dual-department / overlapping-work signals (require irregularity + spending context).
_DUAL_AGENCY_OVERLAP_RE = re.compile(
    r"(?i)\b(?:"
    r"two\s+agencies|two\s+departments|two\s+ministries|"
    r"both\s+agencies|both\s+departments|both\s+ministries|both\s+schemes|"
    r"(?:claimed|billed|paid|funded|sanctioned|disbursed|drawn|reimbursed)\s+(?:from|under|by|to)\s+another\s+(?:agency|department|ministry|scheme)|"
    r"overlapping\s+works?|duplicate\s+works?|"
    r"already\s+(?:executed|constructed|completed)\b[^\n.]{0,60}\b(?:again|another|same)|"
    r"double\s+funding|duplicate\s+funding|"
    r"claimed\s+from\s+both|funded\s+twice|sanctioned\s+twice"
    r")\b"
)

_TAX_EWB_ONLY_RE = re.compile(r"(?i)\b(?:ewb|ewbs|e[\s-]*way\s+bills?)\b")
_GOV_DISBURSEMENT_RE = re.compile(
    r"(?i)\b(?:disburs(?:e|ed|ement)|reimburs(?:e|ed|ement)|drawback|grant|subsidy|contractor|work|works|bill\s+paid|paid\s+twice|double\s+payment)\b"
)

_IRREGULARITY_INDICATOR_RE = re.compile(
    r"(?i)\b(?:"
    r"irregular(?:ly|ity|ities)?|excess\s+payment|overpayment|avoidable|"
    r"infructuous|unfruitful|wasteful|undue\s+benefit|loss\s+to|recoverable|"
    r"audit\s+observed|audit\s+noticed|audit\s+scrutiny|scrutiny\s+revealed|"
    r"test\s+check\s+revealed|lack\s+of\s+reconciliation|without\s+verifying|"
    r"inadmissible|duplicate|double|twice"
    r")\b"
)

_PROJECT_SPENDING_CONTEXT_RE = re.compile(
    r"(?i)\b(?:"
    r"work|works|project|projects|road|roads|bridge|highway|building|railway|"
    r"construction|contract|contractor|voucher|vouchers|bill|bills|invoice|invoices|"
    r"receipt|receipts|claim|claims|grant|grants|scheme|schemes|reimbursement|"
    r"disbursement|payment|payments|expenditure|agency|agencies|division|"
    r"ministry|department|cpse|psu"
    r")\b"
)

_AGENCY_MENTION_RE = re.compile(
    r"\b(?:Ministry\s+of\s+[A-Z][A-Za-z&\-]+(?:\s+(?:and|of|for|[A-Z][A-Za-z&\-]+)){0,6}|"
    r"Department\s+of\s+[A-Z][A-Za-z&\-]+(?:\s+(?:and|of|for|[A-Z][A-Za-z&\-]+)){0,6}|"
    r"NHAI|CPWD|MoRTH|MoRD|NRIDA|MES|BRO|Railways|IRCON|RVNL|NHIDCL|"
    r"BSNL|MTNL|NTPC|ONGC|IOCL|BPCL|HPCL|GAIL|SAIL|BHEL|CIL|NLC|"
    r"AAI|NBCC|HUDCO|NPCC|WAPCOS|PFC|REC|PGCIL|ESIC|EPFO)\b"
)

_AUDIT_TABLE_ENTITY_COLUMN_RE = re.compile(
    r"\b(?:name of (?:the )?(?:ministry|department|cpse|psu|agency|entity|company)|"
    r"(?:company|agency|entity) name|audited entity)\b", re.IGNORECASE,
)
_AUDIT_TABLE_OBSERVATION_COLUMN_RE = re.compile(r"\baudit (?:observations?|findings?)\b", re.IGNORECASE)
_CONTINUED_TABLE_RE = re.compile(r"\b(?:annexure|appendix|table)\s+[^\n]{0,45}\bcontinued\b", re.IGNORECASE)
_ANNEXURE_BOUNDARY_RE = re.compile(r"^(?:annexure|appendix)\b", re.IGNORECASE)
_TABLE_MONEY_CELL_RE = re.compile(r"(?:^|\s)\d[\d,]*\.\d{2}\s*$")
_PARAGRAPH_ITEM_RE = re.compile(r"^\((?:[ivxlcdm]+|[a-z]|\d+)\)\s+", re.IGNORECASE)
_LEGAL_ENTITY_PATTERN = (
    r"[A-Z][\w&.’'/-]*(?:\s+(?:[A-Z][\w&.’'/-]*|of|and|for|the)){0,7}"
    r"\s+(?:Limited|Ltd\.?|Corporation|Authority|Board)(?!\w)"
)
_LEGAL_ENTITY_RE = re.compile(rf"\b{_LEGAL_ENTITY_PATTERN}")
_PAYMENT_ACTOR_RE = re.compile(
    r"^(?:(?i:audit (?:observed|noticed|found) that|accordingly,|however,)\s+)?"
    rf"(?P<actor>(?:{_LEGAL_ENTITY_PATTERN}|[A-Z][A-Z0-9&-]{{2,11}}|"
    + _AGENCY_MENTION_RE.pattern
    + r"))(?:\s+(?:had|has|have|also)){0,2}\s+"
    r"(?:paid|billed|reimbursed|made\s+(?:(?:an?|the|another|irregular|duplicate|double|additional|excess)\s+){0,4}"
    r"payments?)\b"
)
_RECOVERY_RE = re.compile(r"\b(?:recovered|recovery|recoveries)\b", re.IGNORECASE)
_MIXED_EXPOSURE_RE = re.compile(
    r"\b(?:premature payments?|without (?:ensuring )?(?:the )?completion of work|"
    r"(?:before|prior to) (?:the )?completion of work)\b", re.IGNORECASE,
)
# Result and quantification clauses can describe the same event, unlike adjacent independent clauses.
_CLAUSE_BREAK_RE = re.compile(
    r"[;—]\s*|,\s+(?!(?:resulting in|leading to|causing|thereby|amounting|which resulted in)\b)",
    re.IGNORECASE,
)
_FOLLOWUP_RE = re.compile(
    r"^(?:(?:this|that|these)\s+(?:resulted in|led to|accounted for|amounted to)|"
    r"thus|hence|therefore|consequently)\b", re.IGNORECASE,
)
_FOLLOWUP_CONTEXT_RE = re.compile(r"\b(?:also|resulting in|led to|same)\b", re.IGNORECASE)
_SCHEME_CODE_RE = re.compile(r"\b[A-Z][A-Z0-9-]{3,}\b")

_BENIGN_EXPLANATIONS: tuple[str, ...] = (
    "Action Taken Notes (ATNs), departmental reconciliations, or Public Accounts Committee (PAC) "
    "reviews tabled after the audit report may record recoveries, adjustments, or final settlement.",
    "Apparent overlapping vouchers or multiple agency entries can reflect sequential phases, distinct "
    "sub-components, or accounting reclassifications documented in underlying Measurement Books.",
    "Legitimate cost-sharing or convergence guidelines sometimes route counterpart contributions "
    "through separate executing agencies without duplicate expenditure on the same physical work.",
)


@dataclass(frozen=True)
class PageBlock:
    """One non-overlapping contiguous line block extracted from a single PDF page."""

    index: int
    line_start: int  # 0-based inclusive line index on the page
    line_end: int  # 0-based exclusive line index on the page
    heading: str
    para_number: str
    text: str


def aggregate_exposure_inr(entries: Iterable[tuple[dict[str, Any], float]]) -> float:
    """Count a linked executive summary as evidence for the same exposure as its detailed finding."""
    amounts: list[float] = []
    detailed: set[tuple[str, float]] = set()
    summaries: set[tuple[str, float]] = set()
    for metrics, amount in entries:
        if not math.isfinite(amount) or amount <= 0:
            continue
        key = metrics.get("exposure_reference_key")
        kind = metrics.get("exposure_reference_kind")
        if isinstance(key, str) and _SHA256_RE.fullmatch(key):
            if kind == "summary":
                summaries.add((key, amount))
                continue
            if kind == "detail":
                detailed.add((key, amount))
        amounts.append(amount)
    return math.fsum([*amounts, *(amount for _, amount in summaries - detailed)])


def neutral_text(text: str) -> str:
    """Replace BNS Sec 356 prohibited words in quoted source excerpts or headings."""
    cleaned = _ACB_RE.sub("Vigilance", text or "")
    return _NEUTRAL_REPLACE_RE.sub("compliance-review", cleaned)


def _split_sentences(text: str) -> list[str]:
    protected = _ABBREV_DOT_RE.sub(lambda m: f"{m.group(1)}.__ABBREV_SP__", text)
    return [s.replace("__ABBREV_SP__", " ").strip() for s in re.split(r"(?<=[.!?])\s+", protected) if s.strip()]


def _window_sentences(lines: list[str], line_start: int) -> list[tuple[int, str]]:
    """Join wrapped PDF lines without joining separate paragraphs or numbered sections."""
    paragraphs: list[tuple[int, list[str]]] = []
    parts: list[str] = []
    start = line_start
    for index, raw in enumerate(lines, start=line_start):
        line = raw.strip()
        page_header = index < 4 and (
            _REPORT_HEADER_RE.fullmatch(line) or _ROMAN_PAGE_RE.fullmatch(line) or _PAGE_NUMBER_ONLY_RE.fullmatch(line)
        )
        heading = bool(page_header or _NUMBERED_HEADING_RE.match(line) or _LEGAL_ENTITY_RE.fullmatch(line))
        if not line or heading or _PARAGRAPH_ITEM_RE.match(line):
            if parts:
                paragraphs.append((start, parts))
                parts = []
        if line and not heading:
            if not parts:
                start = index
            parts.append(line)
    if parts:
        paragraphs.append((start, parts))
    return [(start, sentence) for start, parts in paragraphs for sentence in _split_sentences(" ".join(parts))]


def _extract_amounts_inr(text: str) -> list[float]:
    amounts: list[float] = []
    for match in _RUPEE_AMOUNT_RE.finditer(text):
        try:
            num = float(match.group(1).replace(",", ""))
        except ValueError:
            continue
        if not math.isfinite(num) or num <= 0:
            continue
        unit = match.group(2).lower()
        if unit.startswith("cr"):
            mult = _INR_CRORE
        elif unit.startswith("lakh"):
            mult = _INR_LAKH
        else:
            mult = _INR_THOUSAND
        amounts.append(round(num * mult, 2))
    return amounts


def _exposure_amount(sentence: str, signal: re.Pattern[str]) -> float | None:
    if _RECOVERY_RE.search(sentence) or _MIXED_EXPOSURE_RE.search(sentence):
        return None
    amounts = {
        amount
        for clause in _CLAUSE_BREAK_RE.split(sentence)
        if signal.search(clause)
        for amount in _extract_amounts_inr(clause)
    }
    return next(iter(amounts)) if len(amounts) == 1 else None


def _continues_observation(first: str, second: str) -> bool:
    if _FOLLOWUP_RE.match(second):
        return True
    return bool(
        _FOLLOWUP_CONTEXT_RE.search(second)
        and set(_SCHEME_CODE_RE.findall(first)).intersection(_SCHEME_CODE_RE.findall(second))
    )


def _has_audit_table_headers(text: str) -> bool:
    normalized = " ".join(text.split())
    return bool(
        _AUDIT_TABLE_ENTITY_COLUMN_RE.search(normalized) and _AUDIT_TABLE_OBSERVATION_COLUMN_RE.search(normalized)
    )


def _summary_reference(lines: list[str], window: dict[str, Any], evidence: str) -> str:
    """Read the explicit section reference attached to an executive-summary observation."""
    start = int(window["line_start"])
    end = int(window["line_end"])
    nearby = "\n".join(lines[start:end + 10])
    pattern = re.compile(r"\s+".join(re.escape(word) for word in evidence.split()))
    match = pattern.search(nearby)
    if not match:
        return ""
    recommendation = False
    for raw_line in nearby[match.end():].splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if reference := _SUMMARY_REFERENCE_RE.fullmatch(line):
            return reference.group(1)
        if line.casefold().startswith("recommendation"):
            recommendation = True
        elif not recommendation or _NUMBERED_HEADING_RE.match(line):
            break
    return ""


def _exposure_reference(
    pages: dict[int, str], window: dict[str, Any], result: dict[str, Any], report_digest: str,
) -> dict[str, str]:
    if not result["amount_disclosed"] or not _SHA256_RE.fullmatch(report_digest):
        return {}
    lines = pages[result["page"]].splitlines()
    kind = "detail"
    section = result["para_number"]
    if any(
        _ROMAN_PAGE_RE.fullmatch(line.strip()) or line.strip().casefold() == "executive summary"
        for line in lines[:5]
    ):
        kind = "summary"
        section = _summary_reference(lines, window, result["evidence"])
    if not _SECTION_NUMBER_RE.fullmatch(section):
        return {}
    # Executive summaries can repeat the same evidence verbatim. Normalize the PDF's
    # inline footnote markers so the detailed sentence has the same identity.
    evidence = " ".join(_AMOUNT_FOOTNOTE_RE.sub(r"\1", result["evidence"]).casefold().split())
    identity = (
        report_digest, section, result["signal_category"], f"{result['amount_inr']:.2f}",
        result["actor"].casefold(), evidence,
    )
    return {
        "exposure_reference_key": sha256("\n".join(identity).encode()).hexdigest(),
        "exposure_reference_kind": kind,
        "exposure_reference_section": section,
    }


def _is_flattened_audit_table(pages: dict[int, str], page: int, window: list[str]) -> bool:
    if _has_audit_table_headers(pages[page]):
        return True
    if not _has_audit_table_headers(pages.get(page - 1, "")):
        return False
    page_start = pages[page].splitlines()[:10]
    if _CONTINUED_TABLE_RE.search("\n".join(page_start)):
        return True
    if any(_NUMBERED_HEADING_RE.match(line.strip()) or _ANNEXURE_BOUNDARY_RE.match(line.strip()) for line in page_start):
        return False
    # Some continuation pages omit headings entirely. Their flattened amount column still has numeric cells.
    return sum(bool(_TABLE_MONEY_CELL_RE.search(line)) for line in window) >= 2

def _is_boilerplate_block(text: str) -> bool:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return True
    if all(_TOC_LINE_RE.search(ln) or _PAGE_NUMBER_ONLY_RE.match(ln) for ln in lines):
        return True
    toc_ratio = sum(1 for ln in lines if _TOC_LINE_RE.search(ln)) / len(lines)
    if toc_ratio >= 0.5:
        return True
    return False


def extract_page_blocks(page_text: str) -> list[PageBlock]:
    """Split one page's text into non-overlapping, sentence-coherent paragraph/section blocks.

    Splits on numbered audit headings and blank lines, then merges tiny fragments
    (< ``MIN_BLOCK_CHARS``) into their neighbor while keeping every line index strictly
    partitioned across blocks with zero overlap.
    """
    raw_lines = (page_text or "").splitlines()
    if not raw_lines:
        return []

    raw_chunks: list[tuple[int, int, str, str]] = []
    cur_start: int | None = None
    cur_heading = ""
    cur_para = ""

    for idx, line in enumerate(raw_lines):
        stripped = line.strip()
        heading_match = _NUMBERED_HEADING_RE.match(stripped) if stripped else None
        if not stripped:
            if cur_start is not None:
                raw_chunks.append((cur_start, idx, cur_para, cur_heading))
                cur_start = None
                cur_para = ""
                cur_heading = ""
            continue
        if heading_match is not None:
            if cur_start is not None:
                raw_chunks.append((cur_start, idx, cur_para, cur_heading))
            cur_start = idx
            cur_para = (heading_match.group(1) or heading_match.group(3) or "").strip()
            h_body = (heading_match.group(2) or heading_match.group(4) or "").strip()
            cur_heading = f"{cur_para} {h_body}".strip() if h_body else cur_para
            continue
        if cur_start is None:
            cur_start = idx
            cur_para = ""
            cur_heading = ""
        # Cap giant single-paragraph pages at ~35 lines so tasks stay finely grained.
        if idx - cur_start >= 35 and stripped.endswith("."):
            raw_chunks.append((cur_start, idx + 1, cur_para, cur_heading))
            cur_start = None
            cur_para = ""
            cur_heading = ""

    if cur_start is not None:
        raw_chunks.append((cur_start, len(raw_lines), cur_para, cur_heading))

    if not raw_chunks:
        return []

    merged: list[tuple[int, int, str, str]] = []
    for start, end, para, heading in raw_chunks:
        chunk_text = "\n".join(raw_lines[start:end]).strip()
        if not chunk_text:
            continue
        if merged and not heading and len(chunk_text) < MIN_BLOCK_CHARS:
            p_start, _p_end, p_para, p_heading = merged[-1]
            prev_text = "\n".join(raw_lines[p_start:_p_end]).strip()
            if len(prev_text) < 2200:
                merged[-1] = (p_start, end, p_para or para, p_heading or heading)
                continue
        if merged and len("\n".join(raw_lines[merged[-1][0] : merged[-1][1]]).strip()) < MIN_BLOCK_CHARS and not heading:
            p_start, _p_end, p_para, p_heading = merged[-1]
            merged[-1] = (p_start, end, p_para or para, p_heading or heading)
            continue
        merged.append((start, end, para, heading))

    blocks: list[PageBlock] = []
    for b_idx, (start, end, para, heading) in enumerate(merged):
        text = "\n".join(raw_lines[start:end]).strip()
        if not text:
            continue
        blocks.append(
            PageBlock(
                index=b_idx,
                line_start=start,
                line_end=end,
                heading=heading,
                para_number=para,
                text=text,
            )
        )
    return blocks


def _find_enclosing_section(
    pages: dict[int, str], page: int, line_start: int,
) -> tuple[str, str, int, int] | None:
    for section_page in (page, page - 1):
        lines = pages.get(section_page, "").splitlines()
        upper = min(line_start + 1, len(lines)) if section_page == page else len(lines)
        for index in range(upper - 1, -1, -1):
            line = lines[index].strip()
            if match := _NUMBERED_HEADING_RE.match(line):
                para = (match.group(1) or match.group(3) or "").strip()
                body = (match.group(2) or match.group(4) or "").strip()
                return para, f"{para} {body}".strip(), section_page, index
            if _LEGAL_ENTITY_RE.fullmatch(line):
                return None
    return None


def resolve_enclosing_heading(pages: dict[int, str], page: int, line_start: int) -> tuple[str, str]:
    """Find the closest preceding numbered audit section heading on this or the previous page."""
    section = _find_enclosing_section(pages, page, line_start)
    return (section[0], section[1]) if section else ("", "")


def _payment_actor(sentence: str, signal: re.Pattern[str] | None = None) -> str:
    for clause in _CLAUSE_BREAK_RE.split(sentence):
        if signal is not None and not signal.search(clause):
            continue
        if match := _PAYMENT_ACTOR_RE.match(_PARAGRAPH_ITEM_RE.sub("", clause.strip())):
            return neutral_text(match.group("actor"))
    return ""


def _actor_key(name: str) -> str:
    return re.sub(r"\s+(?:limited|ltd\.?)$", "", name.casefold())


def _section_payment_actor(
    pages: dict[int, str], section_page: int, heading_line: int, page: int, evidence_line: int, evidence: str,
) -> tuple[str, str]:
    """Use an explicit payment statement at the start of the enclosing section."""
    lines = pages[section_page].splitlines()
    end = min(len(lines), heading_line + 5)
    if section_page == page:
        end = min(end, evidence_line + 1)
    for index in range(heading_line + 1, end):
        line = lines[index].strip()
        if _NUMBERED_HEADING_RE.match(line):
            return "", ""
        if not line or not line[0].isupper():
            continue
        # Include one wrapped line so a company name and its predicate can straddle a line break.
        continuation = lines[index + 1].strip() if index + 1 < end else ""
        if _NUMBERED_HEADING_RE.match(continuation):
            continuation = ""
        excerpt = f"{line} {continuation}".strip()
        actor = _payment_actor(excerpt)
        break
    else:
        return "", ""
    if not actor:
        return "", ""

    key = _actor_key(actor)
    if any(_actor_key(name.group()) != key for name in _LEGAL_ENTITY_RE.finditer(evidence)):
        return "", ""
    between = lines[index:evidence_line + 1] if section_page == page else [
        *lines[index:], "", *pages[page].splitlines()[:evidence_line + 1],
    ]
    for _, sentence in _window_sentences(between, index):
        if (other := _payment_actor(sentence)) and _actor_key(other) != key:
            return "", ""
    return actor, neutral_text(excerpt)


def _pages_from_records(records: list[dict[str, Any]]) -> dict[int, str]:
    pages: dict[int, str] = {}
    for rec in records:
        if not isinstance(rec, dict):
            continue
        raw_page = rec.get("page")
        if isinstance(raw_page, bool):
            continue
        try:
            page = int(raw_page)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        pages[page] = str(rec.get("raw_text") or "")
    return pages


def _safe_url(raw: Any) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    try:
        return validate_safe_http_url(text, require_https=True, allow_private=False)
    except ValueError:
        return ""


def _extract_agencies(text: str) -> list[str]:
    seen: list[str] = []
    for match in _AGENCY_MENTION_RE.finditer(text):
        cleaned = " ".join(match.group(0).split()).strip(" ,.;:-")
        cleaned = neutral_text(cleaned)
        if cleaned and cleaned not in seen:
            seen.append(cleaned)
        if len(seen) >= 6:
            break
    return seen


def _evaluate_window(
    pages: dict[int, str],
    win: dict[str, Any],
    *,
    min_amount_inr: float,
) -> dict[str, Any]:
    """Evaluate one non-overlapping window deterministically."""
    key = str(win.get("key") or "window")
    page = int(win["page"])
    line_start = int(win["line_start"])
    line_end = int(win["line_end"])

    if page not in pages:
        return {"key": key, "status": "unevaluable", "reason": f"page {page} is missing from the PDF"}

    page_lines = (pages[page] or "").splitlines()
    if line_start < 0 or line_end <= line_start or line_start >= len(page_lines):
        return {
            "key": key,
            "status": "unevaluable",
            "reason": f"lines {line_start}..{line_end} are out of range on page {page}",
        }

    core_lines = page_lines[line_start:line_end]
    core_text = "\n".join(core_lines).strip()
    if not core_text or _is_boilerplate_block(core_text):
        return {"key": key, "status": "cleared", "excluded_cofunding": False, "reason": "non_finding_boilerplate"}

    # Include up to 2 adjacent lines on the same page ONLY for sentence continuity.
    # Anchoring strictly to (page, line_start..line_end) guarantees zero duplicate leads across tasks.
    ctx_before = page_lines[max(0, line_start - 2) : line_start]
    ctx_after = page_lines[line_end : min(len(page_lines), line_end + 2)]
    scan_text = "\n".join([*ctx_before, *core_lines, *ctx_after]).strip()

    # Filter out methodology/recommendation/figure sentences before checking signals.
    substantive_sentences = [
        (index, sentence)
        for index, sentence in _window_sentences(core_lines, line_start)
        if not _METHODOLOGY_BOILERPLATE_RE.search(sentence)
        and not _FIGURE_CHART_RE.match(sentence)
        and not _TOC_LINE_RE.search(sentence)
    ]
    if not substantive_sentences:
        return {"key": key, "status": "cleared", "excluded_cofunding": False, "reason": "methodology_or_toc"}

    substantive_text = " ".join(sentence for _, sentence in substantive_sentences)
    has_cofunding = bool(_LEGITIMATE_COFUNDING_RE.search(substantive_text))
    strong_matches = [
        (index, sentence) for index, sentence in substantive_sentences
        if _STRONG_DUPLICATE_BILLING_RE.search(sentence)
    ]

    # Exclude legitimate co-funding / cost-sharing unless explicit double-billing is documented.
    if has_cofunding and not strong_matches:
        return {"key": key, "status": "cleared", "excluded_cofunding": True, "reason": "legitimate_cofunding"}

    if not _PROJECT_SPENDING_CONTEXT_RE.search(scan_text):
        return {"key": key, "status": "cleared", "excluded_cofunding": False, "reason": "no_project_context"}

    if strong_matches:
        # Exclude private taxpayer E-Way Bill (EWB) invoice reuse unless government disbursement is present.
        if _TAX_EWB_ONLY_RE.search(substantive_text) and not _GOV_DISBURSEMENT_RE.search(substantive_text):
            return {"key": key, "status": "cleared", "excluded_cofunding": False, "reason": "taxpayer_ewb_only"}
        signal_category = "duplicate_payment_or_billing"
        signal_pattern = _STRONG_DUPLICATE_BILLING_RE
        matching_sents = strong_matches
    elif _IRREGULARITY_INDICATOR_RE.search(substantive_text):
        signal_category = "dual_agency_or_scheme_overlap"
        signal_pattern = _DUAL_AGENCY_OVERLAP_RE
        matching_sents = [
            (index, sentence)
            for index, sentence in substantive_sentences
            if signal_pattern.search(sentence) and _PROJECT_SPENDING_CONTEXT_RE.search(sentence)
        ]
    else:
        matching_sents = []

    if not matching_sents:
        return {"key": key, "status": "cleared", "excluded_cofunding": False, "reason": "no_duplicate_billing_signal"}

    # Flattened table text supplies neither trustworthy row boundaries nor column units.
    if _is_flattened_audit_table(pages, page, core_lines):
        return {"key": key, "status": "unevaluable", "reason": "ambiguous_audit_table_attribution"}

    evidence_line, evidence = matching_sents[0]
    amount = _exposure_amount(evidence, signal_pattern)
    if amount is None:
        next_index = substantive_sentences.index((evidence_line, evidence)) + 1
        if next_index < len(substantive_sentences):
            next_line, following = substantive_sentences[next_index]
            if (
                next_line == evidence_line
                and (next_line, following) in matching_sents
                and _continues_observation(evidence, following)
            ):
                following_amount = _exposure_amount(following, signal_pattern)
                if following_amount is not None:
                    evidence, amount = following, following_amount

    matched_signals = list(dict.fromkeys(" ".join(hit.group().lower().split()) for hit in signal_pattern.finditer(evidence)))
    amount_disclosed = amount is not None
    amount_inr = amount if amount is not None else 0.0
    if min_amount_inr > 0 and not amount_disclosed:
        return {"key": key, "status": "unevaluable", "reason": "unattributed_amount"}
    if amount_inr < min_amount_inr:
        return {"key": key, "status": "cleared", "excluded_cofunding": has_cofunding, "reason": "below_threshold"}

    section = _find_enclosing_section(pages, page, evidence_line)
    para_number, heading = section[:2] if section else ("", "")
    actor = _payment_actor(evidence, signal_pattern)
    actor_source = None
    if not actor and section and section[0][0].isdigit():
        _, _, section_page, heading_line = section
        actor, actor_excerpt = _section_payment_actor(
            pages, section_page, heading_line, page, evidence_line, evidence,
        )
        if actor:
            actor_source = (section_page, actor_excerpt)

    excerpt = neutral_text(evidence)
    if len(excerpt) > _MAX_EXCERPT_CHARS:
        trigger = signal_pattern.search(excerpt)
        start = max(0, trigger.end() - _MAX_EXCERPT_CHARS // 2) if trigger else 0
        end = start + _MAX_EXCERPT_CHARS - 2
        excerpt = ("…" if start else "") + excerpt[start:end].strip() + ("…" if end < len(excerpt) else "")

    return {
        "key": key,
        "status": "lead",
        "page": page,
        "block_index": int(win.get("block_index", 0)),
        "para_number": para_number or f"p.{page}",
        "heading": neutral_text(heading),
        "signal_category": signal_category,
        "matched_signals": matched_signals[:6],
        "agencies_mentioned": _extract_agencies(evidence),
        "actor": actor,
        "actor_source": actor_source,
        "amount_disclosed": amount_disclosed,
        "amount_inr": round(amount_inr, 2),
        "legitimate_cofunding_excluded": has_cofunding,
        "evidence": evidence,
        "excerpt": excerpt,
    }


def _severity_for(amount_inr: float, signal_category: str) -> str:
    if amount_inr >= _INR_CRORE:
        return "high"
    if amount_inr >= 10.0 * _INR_LAKH or signal_category == "duplicate_payment_or_billing":
        return "medium"
    return "low"


def _entity_label(actor: str, report_title: str, sector: str, para_number: str) -> str:
    if actor:
        base = actor
    elif sector:
        base = f"Union Government ({sector})"
    else:
        base = report_title or "Union Government"
    base = neutral_text(" ".join(base.split()))
    suffix = f" ({para_number})" if para_number else ""
    full = f"{base}{suffix}"
    if len(full) <= MAX_ENTITY_LENGTH:
        return full
    room = max(MAX_ENTITY_LENGTH - len(suffix) - 1, 20)
    return f"{base[:room].rstrip()}…{suffix}"[:MAX_ENTITY_LENGTH]


def _format_inr(amount_inr: float) -> str:
    if amount_inr >= _INR_CRORE:
        return f"Rs {amount_inr / _INR_CRORE:,.2f} crore"
    if amount_inr >= _INR_LAKH:
        return f"Rs {amount_inr / _INR_LAKH:,.2f} lakh"
    return f"Rs {amount_inr:,.0f}"


def run_or1_duplicate_billing(
    records: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
) -> tuple[list[AuditLeadCandidate], int, float, str, dict[str, Any]]:
    """Run deterministic OR1 duplicate-billing & dual-agency check over pinned CAG report windows."""
    ctx = context or {}
    spec = ctx.get("one_road_two_bills") if isinstance(ctx.get("one_road_two_bills"), dict) else {}
    report = spec.get("report") if isinstance(spec.get("report"), dict) else {}
    raw_windows = spec.get("windows")
    windows = [w for w in raw_windows if isinstance(w, dict)][:MAX_WINDOWS_PER_TASK] if isinstance(raw_windows, list) else []
    thresholds = spec.get("thresholds") if isinstance(spec.get("thresholds"), dict) else {}
    raw_min_amt = thresholds.get("min_amount_inr", 0)
    min_amount_inr = (
        float(raw_min_amt)
        if not isinstance(raw_min_amt, bool) and isinstance(raw_min_amt, (int, float)) and math.isfinite(float(raw_min_amt)) and raw_min_amt >= 0
        else 0.0
    )

    cag_id = str(report.get("cag_id") or "").strip()
    report_no = neutral_text(str(report.get("report_no") or "CAG Union Audit Report").strip())
    report_year = str(report.get("year") or "").strip()
    report_title = neutral_text(str(report.get("title") or "").strip())
    sector = neutral_text(str(report.get("sector") or "").strip())

    source_url = _safe_url(ctx.get("retrieval_url") or ctx.get("dataset_url")) or "https://cag.gov.in/en/audit-report"
    landing_url = _safe_url(ctx.get("source_landing_page") or ctx.get("source_url")) or "https://cag.gov.in/en/audit-report"
    sha256 = str(ctx.get("expected_sha256") or "")
    jurisdiction = str(ctx.get("jurisdiction") or "Union of India")

    if not windows or not _YEAR_RE.match(report_year):
        meta = {
            "unevaluable_count": len(windows) or 1,
            "amount_type": "cag_report_monetary_figures",
            "excluded_legitimate_cofunding": 0,
            "windows": [{"key": str(w.get("key", "")), "status": "unevaluable", "reason": "invalid task spec"} for w in windows],
        }
        return [], 0, 0.0, "OR1 read 0 windows: task specification missing report year or windows.", meta

    pages = _pages_from_records(records)
    leads: list[AuditLeadCandidate] = []
    window_results: list[dict[str, Any]] = []
    cleared = 0
    unevaluable = 0
    excluded_cofunding = 0

    for win in windows:
        res = _evaluate_window(pages, win, min_amount_inr=min_amount_inr)
        window_results.append({key: res[key] for key in ("key", "status", "reason") if key in res})
        if res["status"] == "unevaluable":
            unevaluable += 1
            continue
        if res.get("excluded_cofunding"):
            excluded_cofunding += 1
        if res["status"] == "cleared":
            cleared += 1
            continue

        amount_inr = float(res["amount_inr"])
        signal_category = str(res["signal_category"])
        para_number = str(res["para_number"])
        page_num = int(res["page"])
        agencies = list(res["agencies_mentioned"])
        actor = res["actor"]
        entity = _entity_label(actor, report_title, sector, para_number)

        signal_label = (
            "duplicate billing / double payment indicator"
            if signal_category == "duplicate_payment_or_billing"
            else "dual-agency / overlapping work claim indicator"
        )
        amt_clause = (
            f" ({_format_inr(amount_inr)} cited)" if res["amount_disclosed"] and amount_inr > 0 else ""
        )
        title = neutral_text(
            f"{entity}: CAG {report_no} p.{page_num} records {signal_label}{amt_clause}"
        )[:190]

        agencies_text = f" Agencies/bodies referenced: {', '.join(agencies)}." if agencies else ""
        heading_text = f" under section '{res['heading']}'" if res["heading"] else ""
        summary = neutral_text(
            f"CAG {report_no} ({report_title}, p.{page_num}, {para_number}{heading_text}) records a "
            f"{signal_label} (matched phrases: {', '.join(res['matched_signals'])}){amt_clause}.{agencies_text} "
            f"Excerpt: \"{res['excerpt']}\" "
            f"This is a neutral extraction from an official published CAG audit report for public review, "
            f"separate from legitimate cost-sharing arrangements."
        )

        citations = [{
            "source_url": source_url,
            "source_landing_page": landing_url,
            "sha256": sha256,
            "page": page_num,
            "para_number": para_number,
            "report_no": report_no,
            "excerpt": res["excerpt"],
        }]
        if res["actor_source"]:
            actor_page, actor_excerpt = res["actor_source"]
            citations.append({**citations[0], "page": actor_page, "excerpt": actor_excerpt})

        leads.append(
            AuditLeadCandidate(
                title=title,
                summary=summary,
                methodology_code=ONE_ROAD_TWO_BILLS_METHODOLOGY,
                entity_name=entity,
                jurisdiction=jurisdiction,
                fiscal_year=f"FY{report_year}",
                monetary_exposure_inr=amount_inr,
                severity=_severity_for(amount_inr, signal_category),  # type: ignore[arg-type]
                anomaly_metrics={
                    "window_key": res["key"],
                    "report_cag_id": cag_id,
                    "report_no": report_no,
                    "report_year": report_year,
                    "page": page_num,
                    "block_index": res["block_index"],
                    "para_number": para_number,
                    "signal_category": signal_category,
                    "matched_signals": res["matched_signals"],
                    "agencies_mentioned": agencies,
                    "amount_disclosed": bool(res["amount_disclosed"]),
                    "amount_inr": amount_inr,
                    "legitimate_cofunding_excluded": bool(res["legitimate_cofunding_excluded"]),
                    **_exposure_reference(pages, win, res, sha256),
                },
                rule_triggered=(
                    "OR1-DUPLICATE-BILLING"
                    if signal_category == "duplicate_payment_or_billing"
                    else "OR1-DUAL-AGENCY-OVERLAP"
                ),
                citations=citations,
                benign_explanations=list(_BENIGN_EXPLANATIONS),
                suggested_authority=actor or "Comptroller and Auditor General of India / PAC",
                source_locator=f"p.{page_num} ({para_number})",
                amount_type="cag_report_monetary_figures",
            )
        )

    leads.sort(key=lambda lead: (-float(lead.monetary_exposure_inr), lead.title))
    summary = (
        f"OR1 scanned {len(windows)} window(s) in {report_no} ({report_year}): "
        f"{len(leads)} duplicate-billing/dual-claim lead(s), {cleared} cleared "
        f"({excluded_cofunding} legitimate co-funding excluded), {unevaluable} unevaluable."
    )
    meta = {
        "unevaluable_count": unevaluable,
        "amount_type": "cag_report_monetary_figures",
        "excluded_legitimate_cofunding": excluded_cofunding,
        "windows": window_results,
    }
    audited_inr = aggregate_exposure_inr((lead.anomaly_metrics, lead.monetary_exposure_inr) for lead in leads)
    return leads, cleared, round(audited_inr, 2), summary, meta


def validate_context(ctx: Any, where: str) -> list[str]:
    """Validate ``payload.context.one_road_two_bills`` for an OR1 task."""
    errors: list[str] = []
    spec = ctx.get("one_road_two_bills") if isinstance(ctx, dict) else None
    if not isinstance(spec, dict):
        return [f"{where}.payload.context.one_road_two_bills is required for OR1 tasks."]
    base = f"{where}.payload.context.one_road_two_bills"

    report = spec.get("report")
    if not isinstance(report, dict):
        errors.append(f"{base}.report must be an object with 'cag_id', 'report_no', 'year', and 'title'.")
    else:
        for req_key in ("cag_id", "report_no", "title"):
            val = report.get(req_key)
            if not isinstance(val, str) or not val.strip():
                errors.append(f"{base}.report.{req_key} is required.")
        year = report.get("year")
        if not isinstance(year, str) or not _YEAR_RE.match(year.strip()):
            errors.append(f"{base}.report.year must be a 4-digit year such as '2026'.")

    windows = spec.get("windows")
    if not isinstance(windows, list) or not 1 <= len(windows) <= MAX_WINDOWS_PER_TASK:
        errors.append(f"{base}.windows must list 1 to {MAX_WINDOWS_PER_TASK} non-overlapping page blocks.")
    else:
        seen_keys: set[str] = set()
        seen_spans: set[tuple[int, int, int]] = set()
        for i, win in enumerate(windows):
            w_where = f"{base}.windows[{i}]"
            if not isinstance(win, dict):
                errors.append(f"{w_where} must be an object.")
                continue
            key = win.get("key")
            if not isinstance(key, str) or not _KEY_RE.match(key):
                errors.append(f"{w_where}.key must be a lowercase alphanumeric/hyphen identifier <= 40 chars.")
            elif key in seen_keys:
                errors.append(f"{w_where}.key '{key}' is listed twice.")
            else:
                seen_keys.add(key)

            page = win.get("page")
            block_index = win.get("block_index")
            line_start = win.get("line_start")
            line_end = win.get("line_end")
            if isinstance(page, bool) or not isinstance(page, int) or page < 1:
                errors.append(f"{w_where}.page must be an integer >= 1.")
            if isinstance(block_index, bool) or not isinstance(block_index, int) or block_index < 0:
                errors.append(f"{w_where}.block_index must be an integer >= 0.")
            if isinstance(line_start, bool) or not isinstance(line_start, int) or line_start < 0:
                errors.append(f"{w_where}.line_start must be an integer >= 0.")
            if isinstance(line_end, bool) or not isinstance(line_end, int) or (
                isinstance(line_start, int) and not isinstance(line_start, bool) and line_end <= line_start
            ):
                errors.append(f"{w_where}.line_end must be an integer > line_start.")
            if (
                isinstance(page, int)
                and not isinstance(page, bool)
                and isinstance(line_start, int)
                and not isinstance(line_start, bool)
                and isinstance(line_end, int)
                and not isinstance(line_end, bool)
            ):
                span = (page, line_start, line_end)
                if span in seen_spans:
                    errors.append(f"{w_where} duplicates page/line span {span}.")
                seen_spans.add(span)

    thresholds = spec.get("thresholds")
    if thresholds is not None:
        if not isinstance(thresholds, dict):
            errors.append(f"{base}.thresholds must be an object.")
        else:
            min_amt = thresholds.get("min_amount_inr")
            if min_amt is not None and (
                isinstance(min_amt, bool)
                or not isinstance(min_amt, (int, float))
                or not math.isfinite(float(min_amt))
                or float(min_amt) < 0
            ):
                errors.append(f"{base}.thresholds.min_amount_inr must be a finite number >= 0.")

    return errors
