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
# CAG front matter uses lowercase Roman pagination.
_ROMAN_PAGE_RE = re.compile(
    r"(?=[ivxlcdm])m{0,3}(?:cm|cd|d?c{0,3})(?:xc|xl|l?x{0,3})(?:ix|iv|v?i{0,3})"
)
_REPORT_HEADER_RE = re.compile(r"Report No\.\s*\d+ of 20\d{2}", re.IGNORECASE)
_EXECUTIVE_SUMMARY_RE = re.compile(r"Executive Summary\s*:?", re.IGNORECASE)
_SUMMARY_REFERENCE_RE = re.compile(r"\(Para(?:graph)?\.?\s+(\d+(?:\.\d+)+)\)", re.IGNORECASE)
_RECOMMENDATION_HEADING_RE = re.compile(r"Recommendations?(?:\s*:\s*(.*))?", re.IGNORECASE)
_RECOMMENDATION_SENTENCE_RE = re.compile(
    r"^(?:(?!(?:that|is|are|was|were|said|reported|stated|observed|found|noted|showed|revealed)\b)"
    r"[\w’/-]+\s+){1,12}(?:may|should|must|shall)\b"
    r"(?!\s+have\b(?!\s+(?:a|an|the|to)\b))",
    re.IGNORECASE,
)
_AMOUNT_FOOTNOTE_RE = re.compile(
    r"\b(crore|cr\.?|lakhs?|thousand)(?:[0-9]{1,3}|[⁰¹²³⁴⁵⁶⁷⁸⁹]{1,3})"
    r"(?:\s+[0-9]{1,3}(?=\s))?(?!\w)", re.IGNORECASE,
)
_NUMBERED_HEADING_RE = re.compile(
    r"^\s*(?:(?:Para(?:graph)?\.?\s*)?(\d+(?:\.\d+)+)\s+([A-Z][^\n]{5,140})|"
    r"((?:Chapter|CHAPTER)\s+[IVXLC\d]+)\s*[:\-–]?\s*([^\n]{0,120}))$"
)
_TOC_LINE_RE = re.compile(r"(?:\.{3,}|…|\s{3,}\d{1,3})\s*$")
_PAGE_NUMBER_ONLY_RE = re.compile(r"^\s*(?:page\s*)?(\d{1,4})\s*$", re.IGNORECASE)
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
_SENTENCE_BREAK_RE = re.compile(r"(?<=[.!?])\s+")

_RUPEE_AMOUNT_PATTERN = (
    r"(?:₹|INR|Rs\.?)\s*([\d,]+(?:\.\d+)?)\s*(crore|cr\.?|lakhs?|thousand)"
    r"(?:[0-9]{1,3}|[⁰¹²³⁴⁵⁶⁷⁸⁹]{1,3})?(?!\w)"
)
_RUPEE_AMOUNT_RE = re.compile(_RUPEE_AMOUNT_PATTERN, re.IGNORECASE)

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
    r"BSNL|MTNL|NTPC|NHPC|ONGC|IOCL|BPCL|HPCL|GAIL|SAIL|BHEL|CIL|NLC|"
    r"AAI|NBCC|HUDCO|NPCC|WAPCOS|PFC|REC|PGCIL|ESIC|EPFO)\b"
)

_AUDIT_TABLE_ENTITY_COLUMN_RE = re.compile(
    r"\b(?:name of (?:the )?(?:ministry|department|cpse|psu|agency|entity|company)|"
    r"(?:company|agency|entity) name|audited entity)\b", re.IGNORECASE,
)
_AUDIT_TABLE_OBSERVATION_COLUMN_RE = re.compile(r"\baudit (?:observations?|findings?)\b", re.IGNORECASE)
_CONTINUED_TABLE_RE = re.compile(
    r"^(?:annexure|appendix|table)[\s-]+.{0,45}\b(?P<marker>continued|contd)\b\.?", re.IGNORECASE,
)
_CONTINUATION_MARKER_RE = re.compile(r"(?:\((?:continued|contd)\.?\)|(?:continued|contd)\.?)", re.IGNORECASE)
_TABLE_TITLE_RE = re.compile(
    r"^(annexure|appendix|table)[\s-]+"
    r"((?:\d+(?:\.\d+)*[a-z]?|[a-z]\d+|[ivxlcdm]+|[a-z])(?:[-–][a-z0-9]+|\([a-z0-9]+\))?)"
    r"(?=$|[\s:])", re.IGNORECASE,
)
_ANNEXURE_BOUNDARY_RE = re.compile(r"^(?:annexure|appendix)\b", re.IGNORECASE)
_TABLE_MONEY_CELL_RE = re.compile(r"(?:^|\s)\d[\d,]*\.\d{2}\s*$")
_PARAGRAPH_ITEM_RE = re.compile(r"^\((?:[ivxlcdm]+|[a-z]|\d+)\)\s+", re.IGNORECASE)
_STANDALONE_ROMAN_ITEM_RE = re.compile(r"i{1,3}|iv|v|vi{1,3}|ix|x")
_LEGAL_ENTITY_PATTERN = (
    r"[A-Z][\w&.’'/-]*(?:\s+(?:[A-Z][\w&.’'/-]*|of|and|for|the)){0,7}"
    r"\s+(?:Limited|Ltd\.?|Corporation|Authority|Board)(?!\w)"
)
_LEGAL_ENTITY_RE = re.compile(rf"\b{_LEGAL_ENTITY_PATTERN}")
_NAMED_PAYER_PATTERN = rf"(?:{_LEGAL_ENTITY_PATTERN}|{_AGENCY_MENTION_RE.pattern})"
_NAMED_PAYER_RE = re.compile(_NAMED_PAYER_PATTERN)
# An unidentified uppercase subject can block attribution without supplying a payer name.
_POSSIBLE_PAYER_PATTERN = rf"(?:{_NAMED_PAYER_PATTERN}|[A-Z][A-Z0-9&-]{{2,11}})"
_RELATED_PAYER_PATTERN = r"(?i:(?:its|their|the)\s+(?:[a-z-]+\s+){0,2}(?:subsidiary|office|agency|department|contractor))"
_PAYMENT_ACTOR_RE = re.compile(
    r"^(?:(?i:audit (?:observed|noticed|found) that|accordingly,|however,)\s+)?"
    rf"(?:(?i:the)\s+)?(?P<actor>{_POSSIBLE_PAYER_PATTERN})"
    rf"(?P<others>(?:\s+(?:(?i:and|or|as well as)|&)\s+(?:{_POSSIBLE_PAYER_PATTERN}|{_RELATED_PAYER_PATTERN}))*)"
    r"(?:\s+(?:had|has|have|also)){0,2}\s+"
    r"(?:paid|billed|reimbursed|made\s+(?:(?:an?|the|another|irregular|duplicate|double|additional|excess)\s+){0,4}"
    r"payments?)\b"
)
_PAYMENT_BY_RE = re.compile(
    r"\b(?:paid|billed|reimbursed|(?:duplicate|double)\s+payments?|"
    rf"payments?(?:\s+of\s+{_RUPEE_AMOUNT_PATTERN})?"
    r"\s+(?:was|were|had been|has been|have been)\s+made)"
    r"(?:\s+(?:twice|again))?(?:\s+to\s+[^.!?;]{1,80}?)?\s+by\s+", re.IGNORECASE,
)
# A bare uppercase token after "by" could be a transfer method or a date, not a named payer.
_PASSIVE_PAYMENT_ACTOR_RE = re.compile(
    rf"(?:(?i:the)\s+)?(?P<actor>{_NAMED_PAYER_PATTERN})(?![\w&-])"
)
_AUDITEE_CONTEXT_RE = re.compile(
    rf"\b(?i:in the case of|audit of)\s+(?:(?i:the)\s+)?(?P<auditee>{_AGENCY_MENTION_RE.pattern})"
)
_ADDITIONAL_PAYMENT_ACTOR_RE = re.compile(
    rf"(?:\s*,\s*(?:(?i:and|or|as well as)\s+)?|\s+(?:(?i:and|or|as well as)|&)\s+)"
    rf"(?:(?i:its|their|the(?:\s+[a-z]+){{0,3}}\s+(?:office|agency|department|contractor))\b|(?:(?i:the)\s+)?"
    rf"{_POSSIBLE_PAYER_PATTERN}(?![\w&-]))"
)
_ACTOR_CONJUNCTION_RE = re.compile(r"\s+(?:and|or|as well as|&)\s+", re.IGNORECASE)
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
# A new subject and finite verb can introduce a different payment within a sentence.
_PAYMENT_CLAUSE_BREAK_RE = re.compile(
    rf"(?P<comma>,)?\s+(?P<conjunction>and|but|whereas|while)\s+|{_CLAUSE_BREAK_RE.pattern}",
    re.IGNORECASE,
)
_INDEPENDENT_SUBJECT_RE = re.compile(
    r"(?!(?:the\s+)?(?:resulting|consequent)\s+)"
    r"(?:(?:the|one|a|an|another|each|both|these|those)\s+|audit\s+)"
    r"(?:(?:[a-z][a-z-]*|Rs\.|[\d,.]+|₹[\d,.]*)\s+){0,8}"
    r"(?:was|were|had|has|have|paid|billed|claimed|cost|observed|noticed|found)\b",
    re.IGNORECASE,
)
_OVERPAYMENT_AMOUNT_RE = re.compile(
    rf"the\s+overpayment\s+(?:was|amounted\s+to)\s+{_RUPEE_AMOUNT_PATTERN}\s*[.!?]?",
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


@dataclass(frozen=True)
class _PageSentence:
    paragraph_start: int
    line: int
    column: int  # Offset in the stripped source line.
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


def _paragraph_sentences(parts: list[str], paragraph_start: int) -> Iterable[_PageSentence]:
    paragraph = "\n".join(parts)
    abbreviations = {match.end(1) + 1 for match in _ABBREV_DOT_RE.finditer(paragraph)}
    breaks = [match.span() for match in _SENTENCE_BREAK_RE.finditer(paragraph) if match.start() not in abbreviations]
    breaks.append((len(paragraph), len(paragraph)))
    start = 0
    line = paragraph_start
    for end, next_start in breaks:
        column = start - (paragraph.rfind("\n", 0, start) + 1)
        sentence = _ABBREV_DOT_RE.sub(r"\1. ", paragraph[start:end].replace("\n", " "))
        yield _PageSentence(paragraph_start, line, column, sentence)
        line += paragraph.count("\n", start, next_start)
        start = next_start


def _page_prefix(lines: list[str]) -> tuple[int, bool]:
    """Read pagination and report headers only before the page's substantive content."""
    has_page_number = False
    is_summary = False
    for index, raw in enumerate(lines[:5]):
        line = raw.strip()
        if not line or _REPORT_HEADER_RE.fullmatch(line):
            continue
        if (heading := _NUMBERED_HEADING_RE.match(line)) and heading[3]:
            continue
        if _EXECUTIVE_SUMMARY_RE.fullmatch(line):
            is_summary = True
            continue
        roman = bool(_ROMAN_PAGE_RE.fullmatch(line))
        if not has_page_number and (roman or _PAGE_NUMBER_ONLY_RE.fullmatch(line)):
            has_page_number = True
            is_summary = is_summary or roman
            continue
        return index, is_summary
    return min(len(lines), 5), is_summary


def _window_sentences(
    lines: list[str], line_start: int, *, prefix_end: int = 0, preceding_line: str = "",
) -> list[_PageSentence]:
    """Join wrapped PDF lines without joining separate paragraphs or numbered sections."""
    paragraphs: list[tuple[int, list[str]]] = []
    parts: list[str] = []
    start = line_start
    previous_heading = _NUMBERED_HEADING_RE.match(preceding_line)
    for offset, raw in enumerate(lines):
        index = line_start + offset
        line = raw.strip()
        continued_title = _CONTINUED_TABLE_RE.match(line)
        table_heading = _TABLE_TITLE_RE.fullmatch(line) or _CONTINUATION_MARKER_RE.fullmatch(line) or (
            continued_title and not line[continued_title.end():].strip(" ):.–-")
        )
        # A bare Roman list marker can follow a numbered heading on its own PDF line.
        item_marker = (
            previous_heading and previous_heading[1] and _STANDALONE_ROMAN_ITEM_RE.fullmatch(line)
            and offset + 1 < len(lines) and lines[offset + 1].strip()[:1].isupper()
        )
        previous_heading = _NUMBERED_HEADING_RE.match(line)
        heading = bool(
            index < prefix_end or table_heading or item_marker or previous_heading or _LEGAL_ENTITY_RE.fullmatch(line)
        )
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
    return [sentence for start, parts in paragraphs for sentence in _paragraph_sentences(parts, start)]


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


def _payment_clauses(sentence: str) -> Iterable[tuple[int, str]]:
    """Use the same clause boundaries for payer and amount attribution."""
    start = 0
    for marker in _PAYMENT_CLAUSE_BREAK_RE.finditer(sentence):
        next_start = marker.end()
        if marker["conjunction"]:
            right = sentence[marker.end():]
            if _PAYMENT_ACTOR_RE.match(right) or _INDEPENDENT_SUBJECT_RE.match(right):
                remainder = sentence[start:]
                actor_text = _PARAGRAPH_ITEM_RE.sub("", remainder.lstrip())
                actor = _PAYMENT_ACTOR_RE.match(actor_text)
                actor_offset = len(remainder) - len(actor_text)
                if actor and marker.start() - start < actor_offset + actor.end():
                    continue  # Keep compound payer names and coordinated payer subjects together.
            elif marker["comma"]:
                next_start = marker.start("conjunction")  # Keep the ordinary comma boundary.
            else:
                continue
        yield start, sentence[start:marker.start()]
        start = next_start
    yield start, sentence[start:]


def _exposure_amount(sentence: str, signal: re.Pattern[str]) -> float | None:
    if _RECOVERY_RE.search(sentence) or _MIXED_EXPOSURE_RE.search(sentence):
        return None
    amounts: set[float] = set()
    preceding_signal = False
    for _, clause in _payment_clauses(sentence):
        has_signal = bool(signal.search(clause))
        if has_signal or (preceding_signal and _OVERPAYMENT_AMOUNT_RE.fullmatch(clause.strip())):
            amounts.update(_extract_amounts_inr(clause))
        preceding_signal = has_signal
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


def _summary_reference(lines: list[str], window_end: int, observation: _PageSentence) -> str:
    """Read the explicit section reference attached to an executive-summary observation."""
    nearby = "\n".join([
        lines[observation.line].strip()[observation.column:],
        *lines[observation.line + 1:window_end + 10],
    ])
    pattern = re.compile(r"\s+".join(re.escape(word) for word in observation.text.split()))
    match = pattern.match(nearby)
    if not match:
        return ""
    following = iter(nearby[match.end():].lstrip().splitlines())
    first = next(following, "").strip()
    if reference := _SUMMARY_REFERENCE_RE.fullmatch(first):
        return reference.group(1)
    heading = _RECOMMENDATION_HEADING_RE.fullmatch(first)
    if not heading:
        return ""

    parts = [heading[1]] if heading[1] else []
    for raw in following:
        line = raw.strip()
        if reference := _SUMMARY_REFERENCE_RE.fullmatch(line):
            if parts and all(
                _RECOMMENDATION_SENTENCE_RE.match(sentence.text) for sentence in _paragraph_sentences(parts, 0)
            ):
                return reference.group(1)
            break
        if _NUMBERED_HEADING_RE.match(line) or _RECOMMENDATION_HEADING_RE.fullmatch(line) or (not line and parts):
            break
        if line:
            parts.append(line)
    return ""


def _exposure_reference(result: dict[str, Any], report_digest: str) -> dict[str, str]:
    if not result["amount_disclosed"] or not _SHA256_RE.fullmatch(report_digest):
        return {}
    kind = result["reference_kind"]
    section = result["reference_section"]
    if not _SECTION_NUMBER_RE.fullmatch(section):
        return {}
    # Match the cited observation even if the detail page supplies additional payer context.
    # Normalize PDF footnote markers so a verbatim summary and detail have the same identity.
    evidence = " ".join(_AMOUNT_FOOTNOTE_RE.sub(r"\1", result["evidence"]).casefold().split())
    identity = (
        report_digest, section, result["signal_category"], f"{result['amount_inr']:.2f}",
        evidence,
    )
    return {
        "exposure_reference_key": sha256("\n".join(identity).encode()).hexdigest(),
        "exposure_reference_kind": kind,
        "exposure_reference_section": section,
    }


def _table_title(line: str, continuation: re.Match[str] | None) -> re.Match[str] | None:
    if continuation:
        line = line[:continuation.start("marker")].rstrip(" (-–—")
    return _TABLE_TITLE_RE.match(line)


def _table_heading(lines: list[str], index: int) -> tuple[re.Match[str] | None, bool]:
    line = lines[index].strip()
    continuation = _CONTINUED_TABLE_RE.match(line)
    title = _table_title(line, continuation)
    if not continuation and title:
        tail = line[title.end():].lstrip()
        is_caption = tail.startswith(":") or (tail[:1] in ("-", "–", "—") and tail[1:2].isspace())
        if not tail or is_caption:
            following = next((raw.strip() for raw in lines[index + 1:] if raw.strip()), "")
            return title, bool(_CONTINUATION_MARKER_RE.fullmatch(following))
    return title, bool(continuation)


def _is_flattened_audit_table(
    pages: dict[int, str], page: int, evidence_line: int, window_start: int, window_end: int,
) -> bool:
    """Find the headers for a contiguous table, stopping at a different section."""
    table_label: tuple[str, ...] = ()
    for table_page in range(page, 0, -1):
        lines = pages.get(table_page, "").splitlines()
        prior_lines = pages.get(table_page - 1, "").splitlines()
        anchor = evidence_line if table_page == page else len(lines) - 1
        start, end = 0, len(lines)
        label: tuple[str, ...] = ()
        new_section = False
        continued = False
        running_chapter = None
        for index, raw in enumerate(lines):
            line = raw.strip()
            title, continuation = _table_heading(lines, index)
            heading = _NUMBERED_HEADING_RE.match(line)
            if title or continuation or heading or _ANNEXURE_BOUNDARY_RE.match(line):
                if index > anchor:
                    end = index
                    break
                if index < 4 and not continuation and _is_running_table_prefix(lines, index, title, heading, prior_lines):
                    if heading and heading[3]:
                        running_chapter = _chapter_label(heading)
                    continue
                start = index + 1
                label = tuple(part.casefold() for part in title.groups()) if title else ()
                continued = continuation
                new_section = new_section or not continued
        if label and table_label and label != table_label:
            return False
        table_label = label or table_label
        section = lines[start:end]
        if _has_audit_table_headers("\n".join(section)):
            return True
        if new_section:
            return False
        # Implicit continuation requires flattened rows in the window, or in the preceding page's trailing section.
        row_lines = lines[max(start, window_start):min(end, window_end)] if table_page == page else section
        row_count = sum(bool(_TABLE_MONEY_CELL_RE.search(line)) for line in row_lines)
        if not continued and row_count < 2:
            # The same running chapter and consecutive printed pages can corroborate a single row.
            if not (
                row_count == 1 and running_chapter
                and (table_page != page or _TABLE_MONEY_CELL_RE.search(lines[evidence_line]))
                and _has_consecutive_chapter_pagination(lines, prior_lines, running_chapter)
            ):
                return False
    return False


def _chapter_label(heading: re.Match[str]) -> tuple[str, str]:
    return " ".join(heading[3].casefold().split()), " ".join(heading[4].casefold().split())


def _same_chapter(current: tuple[str, str], previous: tuple[str, str]) -> bool:
    number, subtitle = current
    prior_number, prior_subtitle = previous
    return number == prior_number and (not subtitle or not prior_subtitle or subtitle == prior_subtitle)


def _has_consecutive_chapter_pagination(
    lines: list[str], prior_lines: list[str], chapter: tuple[str, str],
) -> bool:
    prior_chapter = next((
        _chapter_label(heading) for raw in prior_lines[:4]
        if (heading := _NUMBERED_HEADING_RE.match(raw.strip())) and heading[3]
    ), None)
    if prior_chapter is None or not _same_chapter(chapter, prior_chapter):
        return False
    prior_number, number = (
        next((int(match[1]) for line in page[:4] if (match := _PAGE_NUMBER_ONLY_RE.fullmatch(line))), None)
        for page in (prior_lines, lines)
    )
    return prior_number is not None and number == prior_number + 1


def _is_running_table_prefix(
    lines: list[str], index: int, title: re.Match[str] | None, heading: re.Match[str] | None, prior_lines: list[str],
) -> bool:
    if heading and heading.group(3):
        prior_chapter = next((
            match for raw in reversed(prior_lines)
            if (match := _NUMBERED_HEADING_RE.match(raw.strip())) and match.group(3)
        ), None)
        # Without a conflicting chapter, the caller checks for table headers and evidence of continuation.
        if prior_chapter is None:
            return True
        return _same_chapter(_chapter_label(heading), _chapter_label(prior_chapter))
    if not title:
        return False
    following = next((
        following_index for following_index, raw in enumerate(lines[index + 1:], index + 1) if (line := raw.strip())
        and not (
            _PAGE_NUMBER_ONLY_RE.fullmatch(line) or _ROMAN_PAGE_RE.fullmatch(line) or _REPORT_HEADER_RE.fullmatch(line)
        )
    ), None)
    if following is None:
        return False
    continued_title, continuation = _table_heading(lines, following)
    return bool(continuation and continued_title and title.group().casefold() == continued_title.group().casefold())


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
            if _LEGAL_ENTITY_RE.fullmatch(line) or _ANNEXURE_BOUNDARY_RE.match(line) or _TABLE_TITLE_RE.fullmatch(line):
                return None
    return None


def resolve_enclosing_heading(pages: dict[int, str], page: int, line_start: int) -> tuple[str, str]:
    """Find the closest preceding numbered audit section heading on this or the previous page."""
    section = _find_enclosing_section(pages, page, line_start)
    return (section[0], section[1]) if section else ("", "")


def _payment_actor(sentence: str, signal: re.Pattern[str] | None = None, *, opener: str = "") -> str | None:
    """Return a named payer, None for an unresolved payer, or an empty string for no payer statement."""
    unresolved = False
    for clause_start, clause in _payment_clauses(sentence):
        if signal is not None and not signal.search(clause):
            continue
        if match := _PAYMENT_ACTOR_RE.match(_PARAGRAPH_ITEM_RE.sub("", clause.strip())):
            if match.group("others"):
                return None
            actor = match.group("actor")
            # The opener can establish the full name behind a later bare acronym, e.g. NMDC.
            if opener and _actor_key(actor) == _actor_key(opener):
                return opener
            return _single_payment_actor(actor)
        if passive := _PAYMENT_BY_RE.search(clause):
            payer = _PASSIVE_PAYMENT_ACTOR_RE.match(clause, passive.end())
            # The list of payers can cross a comma that otherwise separates independent clauses.
            if payer and not _ADDITIONAL_PAYMENT_ACTOR_RE.match(sentence, clause_start + payer.end()):
                return _single_payment_actor(payer.group("actor"))
            unresolved = True
    return None if unresolved else ""


def _single_payment_actor(actor: str) -> str | None:
    if not _NAMED_PAYER_RE.fullmatch(actor):
        return None
    for join in _ACTOR_CONJUNCTION_RE.finditer(actor):
        if _LEGAL_ENTITY_RE.fullmatch(actor[:join.start()]) and _LEGAL_ENTITY_RE.fullmatch(actor[join.end():]):
            return None
    return neutral_text(actor)


def _actor_key(name: str) -> str:
    return re.sub(r"\s+(?:limited|ltd\.?)$", "", name.casefold())


def _section_payment_actor(
    pages: dict[int, str], section_page: int, heading_line: int, page: int, evidence: _PageSentence,
) -> tuple[str, str]:
    """Use an explicit payment statement at the start of the enclosing section."""
    preceding = pages[page].splitlines()[:evidence.line + 1]
    preceding[-1] = preceding[-1].strip()[:evidence.column]
    lines = preceding if section_page == page else pages[section_page].splitlines()
    end = min(len(lines), heading_line + 5)
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

    # An explicit but unresolved payer is insufficient evidence for borrowing the opener's actor.
    if _PAYMENT_BY_RE.search(evidence.text):
        return "", ""
    key = _actor_key(actor)
    if any(_actor_key(name.group()) != key for name in _LEGAL_ENTITY_RE.finditer(evidence.text)):
        return "", ""
    if any(_actor_key(match.group("auditee")) != key for match in _AUDITEE_CONTEXT_RE.finditer(evidence.text)):
        return "", ""
    context = _window_sentences(lines[index:], index)
    if section_page != page:
        context.extend(_window_sentences(preceding, 0, prefix_end=_page_prefix(preceding)[0]))
    for sentence in context:
        other = _payment_actor(sentence.text, opener=actor)
        if other is None or (other and _actor_key(other) != key):
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
    prefix_end, is_summary = _page_prefix(page_lines)
    preceding_line = page_lines[line_start - 1].strip() if line_start else ""
    substantive_sentences = [
        sentence for sentence in _window_sentences(
            core_lines, line_start, prefix_end=prefix_end, preceding_line=preceding_line,
        )
        if not _METHODOLOGY_BOILERPLATE_RE.search(sentence.text)
        and not _FIGURE_CHART_RE.match(sentence.text)
        and not _TOC_LINE_RE.search(sentence.text)
    ]
    if not substantive_sentences:
        return {"key": key, "status": "cleared", "excluded_cofunding": False, "reason": "methodology_or_toc"}

    substantive_text = " ".join(sentence.text for sentence in substantive_sentences)
    has_cofunding = bool(_LEGITIMATE_COFUNDING_RE.search(substantive_text))
    strong_matches = [sentence for sentence in substantive_sentences if _STRONG_DUPLICATE_BILLING_RE.search(sentence.text)]

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
            sentence for sentence in substantive_sentences
            if signal_pattern.search(sentence.text) and _PROJECT_SPENDING_CONTEXT_RE.search(sentence.text)
        ]
    else:
        matching_sents = []

    if not matching_sents:
        return {"key": key, "status": "cleared", "excluded_cofunding": False, "reason": "no_duplicate_billing_signal"}

    # Flattened table text supplies neither trustworthy row boundaries nor column units.
    observation = matching_sents[0]
    evidence = observation.text
    if _is_flattened_audit_table(pages, page, observation.line, line_start, line_end):
        return {"key": key, "status": "unevaluable", "reason": "ambiguous_audit_table_attribution"}

    amount = _exposure_amount(evidence, signal_pattern)
    if amount is None:
        next_index = substantive_sentences.index(observation) + 1
        if next_index < len(substantive_sentences):
            following = substantive_sentences[next_index]
            if (
                following.paragraph_start == observation.paragraph_start
                and following in matching_sents
                and _continues_observation(evidence, following.text)
            ):
                following_amount = _exposure_amount(following.text, signal_pattern)
                if following_amount is not None:
                    observation, evidence, amount = following, following.text, following_amount

    matched_signals = list(dict.fromkeys(" ".join(hit.group().lower().split()) for hit in signal_pattern.finditer(evidence)))
    amount_disclosed = amount is not None
    amount_inr = amount if amount is not None else 0.0
    if min_amount_inr > 0 and not amount_disclosed:
        return {"key": key, "status": "unevaluable", "reason": "unattributed_amount"}
    if amount_inr < min_amount_inr:
        return {"key": key, "status": "cleared", "excluded_cofunding": has_cofunding, "reason": "below_threshold"}

    section = _find_enclosing_section(pages, page, observation.line)
    para_number, heading = section[:2] if section else ("", "")
    if is_summary and section and section[2] == page and _SECTION_NUMBER_RE.fullmatch(para_number):
        is_summary = any(_EXECUTIVE_SUMMARY_RE.fullmatch(line.strip()) for line in page_lines[:prefix_end])
    actor = _payment_actor(evidence, signal_pattern)
    actor_source = None
    if actor == "" and section and section[0][0].isdigit():
        _, _, section_page, heading_line = section
        actor, actor_excerpt = _section_payment_actor(
            pages, section_page, heading_line, page, observation,
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
        "actor": actor or "",
        "actor_source": actor_source,
        "amount_disclosed": amount_disclosed,
        "amount_inr": round(amount_inr, 2),
        "legitimate_cofunding_excluded": has_cofunding,
        "reference_kind": "summary" if is_summary else "detail",
        "reference_section": _summary_reference(page_lines, line_end, observation) if is_summary else para_number,
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
                    **_exposure_reference(res, sha256),
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
