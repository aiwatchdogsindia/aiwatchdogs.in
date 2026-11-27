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
from dataclasses import dataclass
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
    r"(?i)(?:₹|INR|Rs\.?)\s*([\d,]+(?:\.\d+)?)\s*(crore|cr\.?|lakh|lakhs|thousand)\b"
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


def neutral_text(text: str) -> str:
    """Replace BNS Sec 356 prohibited words in quoted source excerpts or headings."""
    cleaned = _ACB_RE.sub("Vigilance", text or "")
    return _NEUTRAL_REPLACE_RE.sub("compliance-review", cleaned)


def _split_sentences(text: str) -> list[str]:
    protected = _ABBREV_DOT_RE.sub(lambda m: f"{m.group(1)}.__ABBREV_SP__", text)
    return [s.replace("__ABBREV_SP__", " ").strip() for s in re.split(r"(?<=[.!?])\s+", protected) if s.strip()]


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


def resolve_enclosing_heading(pages: dict[int, str], page: int, line_start: int) -> tuple[str, str]:
    """Find the closest preceding numbered audit section heading on ``page`` or ``page - 1``."""
    lines_here = (pages.get(page) or "").splitlines()
    upper = min(max(line_start + 1, 0), len(lines_here))
    for i in range(upper - 1, -1, -1):
        m = _NUMBERED_HEADING_RE.match(lines_here[i].strip())
        if m:
            para = (m.group(1) or m.group(3) or "").strip()
            h_body = (m.group(2) or m.group(4) or "").strip()
            return para, (f"{para} {h_body}".strip() if h_body else para)

    prev_lines = (pages.get(page - 1) or "").splitlines()
    for i in range(len(prev_lines) - 1, -1, -1):
        m = _NUMBERED_HEADING_RE.match(prev_lines[i].strip())
        if m:
            para = (m.group(1) or m.group(3) or "").strip()
            h_body = (m.group(2) or m.group(4) or "").strip()
            return para, (f"{para} {h_body}".strip() if h_body else para)
    return "", ""


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

    para_number, heading = resolve_enclosing_heading(pages, page, line_start)
    sentences = _split_sentences(" ".join(ln.strip() for ln in core_text.splitlines() if ln.strip()))
    if not sentences:
        sentences = [core_text]

    # Filter out methodology/recommendation/figure sentences before checking signals.
    substantive_sentences = [
        s
        for s in sentences
        if not _METHODOLOGY_BOILERPLATE_RE.search(s)
        and not _FIGURE_CHART_RE.match(s)
        and not _TOC_LINE_RE.search(s)
    ]
    if not substantive_sentences:
        return {"key": key, "status": "cleared", "excluded_cofunding": False, "reason": "methodology_or_toc"}

    substantive_text = " ".join(substantive_sentences)
    has_cofunding = bool(_LEGITIMATE_COFUNDING_RE.search(substantive_text))
    strong_hits = [m.group(0) for m in _STRONG_DUPLICATE_BILLING_RE.finditer(substantive_text)]
    dual_hits = [m.group(0) for m in _DUAL_AGENCY_OVERLAP_RE.finditer(substantive_text)]

    # Exclude legitimate co-funding / cost-sharing unless explicit double-billing is documented.
    if has_cofunding and not strong_hits:
        return {"key": key, "status": "cleared", "excluded_cofunding": True, "reason": "legitimate_cofunding"}

    if not _PROJECT_SPENDING_CONTEXT_RE.search(scan_text):
        return {"key": key, "status": "cleared", "excluded_cofunding": False, "reason": "no_project_context"}

    signal_category: str | None = None
    matched_signals: list[str] = []
    if strong_hits:
        # Exclude private taxpayer E-Way Bill (EWB) invoice reuse unless government disbursement is present.
        if _TAX_EWB_ONLY_RE.search(substantive_text) and not _GOV_DISBURSEMENT_RE.search(substantive_text):
            return {"key": key, "status": "cleared", "excluded_cofunding": False, "reason": "taxpayer_ewb_only"}
        # Even for strong hits, require at least one sentence without pure methodology framing.
        matching_sents = [s for s in substantive_sentences if _STRONG_DUPLICATE_BILLING_RE.search(s)]
        if matching_sents:
            signal_category = "duplicate_payment_or_billing"
            matched_signals = list(dict.fromkeys(" ".join(h.lower().split()) for h in strong_hits))
    elif dual_hits and _IRREGULARITY_INDICATOR_RE.search(substantive_text):
        matching_sents = [
            s
            for s in substantive_sentences
            if _DUAL_AGENCY_OVERLAP_RE.search(s) and _PROJECT_SPENDING_CONTEXT_RE.search(s)
        ]
        if matching_sents:
            signal_category = "dual_agency_or_scheme_overlap"
            matched_signals = list(dict.fromkeys(" ".join(h.lower().split()) for h in dual_hits))

    if signal_category is None:
        return {"key": key, "status": "cleared", "excluded_cofunding": False, "reason": "no_duplicate_billing_signal"}

    # Prefer rupee amount in the matched sentence(s); fall back to the substantive block text.
    matched_sent_text = " ".join(
        s
        for s in substantive_sentences
        if _STRONG_DUPLICATE_BILLING_RE.search(s) or _DUAL_AGENCY_OVERLAP_RE.search(s)
    )
    amounts = _extract_amounts_inr(matched_sent_text) or _extract_amounts_inr(substantive_text)
    amount_disclosed = bool(amounts)
    amount_inr = float(max(amounts)) if amounts else 0.0
    if min_amount_inr > 0 and amount_inr < min_amount_inr:
        return {"key": key, "status": "cleared", "excluded_cofunding": has_cofunding, "reason": "below_threshold"}

    excerpt_raw = matched_sent_text.strip() or substantive_text.strip()
    excerpt = neutral_text(" ".join(excerpt_raw.split()))
    if len(excerpt) > _MAX_EXCERPT_CHARS:
        excerpt = excerpt[: _MAX_EXCERPT_CHARS - 1].rstrip() + "…"

    agencies = _extract_agencies(f"{heading} {substantive_text}")
    return {
        "key": key,
        "status": "lead",
        "page": page,
        "block_index": int(win.get("block_index", 0)),
        "para_number": para_number or f"p.{page}",
        "heading": neutral_text(heading),
        "signal_category": signal_category,
        "matched_signals": matched_signals[:6],
        "agencies_mentioned": agencies,
        "amount_disclosed": amount_disclosed,
        "amount_inr": round(amount_inr, 2),
        "legitimate_cofunding_excluded": has_cofunding,
        "excerpt": excerpt,
    }


def _severity_for(amount_inr: float, signal_category: str) -> str:
    if amount_inr >= _INR_CRORE:
        return "high"
    if amount_inr >= 10.0 * _INR_LAKH or signal_category == "duplicate_payment_or_billing":
        return "medium"
    return "low"


def _entity_label(agencies: list[str], report_title: str, sector: str, para_number: str) -> str:
    if agencies:
        base = agencies[0]
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
    audited_inr = 0.0

    for win in windows:
        res = _evaluate_window(pages, win, min_amount_inr=min_amount_inr)
        window_results.append({"key": res["key"], "status": res["status"]})
        if res["status"] == "unevaluable":
            unevaluable += 1
            continue
        if res.get("excluded_cofunding"):
            excluded_cofunding += 1
        if res["status"] == "cleared":
            cleared += 1
            continue

        amount_inr = float(res["amount_inr"])
        audited_inr += amount_inr
        signal_category = str(res["signal_category"])
        para_number = str(res["para_number"])
        page_num = int(res["page"])
        agencies = list(res["agencies_mentioned"])
        entity = _entity_label(agencies, report_title, sector, para_number)

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
                },
                rule_triggered=(
                    "OR1-DUPLICATE-BILLING"
                    if signal_category == "duplicate_payment_or_billing"
                    else "OR1-DUAL-AGENCY-OVERLAP"
                ),
                citations=[
                    {
                        "source_url": source_url,
                        "source_landing_page": landing_url,
                        "sha256": sha256,
                        "page": page_num,
                        "para_number": para_number,
                        "report_no": report_no,
                        "excerpt": res["excerpt"],
                    }
                ],
                benign_explanations=list(_BENIGN_EXPLANATIONS),
                suggested_authority=agencies[0] if agencies else "Comptroller and Auditor General of India / PAC",
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
