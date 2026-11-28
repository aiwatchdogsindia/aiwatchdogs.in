"""Plain-language writer for P8: scheme commitments that a later official record shows changed or unmet.

The engine fields are mostly free text written per case (``promised_metric``, ``revised_metric``,
``tranche_or_phase``), so this writer works in three layers:

1. CAG audit findings, where the only reliable facts are the State, the audit topic, the rupee figure
   and the quoted audit sentence. The rule code on these is not used: the engine files every CAG page
   under a walk-back category whatever the audit sentence says.
2. Large templated families (one finding per State for the same parliamentary table), parsed with
   anchored patterns so the figures can be laid out as a proper sentence.
3. Everything else: a tidy-up of the engine's own short phrases, cut only at clause boundaries.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from aiwatchdogs.core.safety import scan_forbidden_terms

from ._common import clean, fmt_date, fmt_inr, fmt_num, metrics, num, parse_date, place

_HEADLINE_AIM = 96
_HEADLINE_HARD = 108
_BRIEF_AIM = 245

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}  # fmt: skip
_MONTH_ABBR = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# What the audit agents filed the record under, as (longer, shorter) noun phrases after "flagged for".
_FLAG: dict[str, tuple[str, str]] = {
    "P8-ARREARS-OR-UNFUNDED-PROMISE": ("unpaid dues or unmet targets", "unmet promises"),
    "P8-MIDSTREAM-BENEFIT-CUT": ("benefits changed mid-scheme", "changed benefits"),
    "P8-PREMATURE-DISCONTINUATION": ("a scheme or benefit ended early", "early closure"),
    "P8-ELIGIBILITY-NARROWING": ("tighter eligibility rules", "tighter eligibility"),
    "P8-LOCKIN-OR-EXIT-RESTRICTION": ("changed lock-in or exit terms", "changed exit terms"),
    "P8-RETROACTIVE-TAX-REVERSAL": ("a tax benefit changed after launch", "changed tax terms"),
}
_FLAG_DEFAULT = ("a changed scheme commitment", "a changed commitment")

# First words that are safe to lower-case when a phrase follows a colon.
_LOWER_OK = frozenset(
    [
        "actual", "actuals", "ad-valorem", "annual", "automatic", "broad", "capped", "cashless", "central", "claim",
        "collateral-free", "completion", "conditional", "continuous", "current", "cut", "deadline", "declined",
        "dedicated", "defined-benefit", "delayed", "discontinued", "exclusion", "fell", "flat", "full",
        "funding-pattern", "general", "held", "high", "inflation-adjusted", "interest", "large", "mandatory",
        "mission", "multi-year", "nationwide", "no", "ongoing", "open-ended", "original",
        "original-subscriber-only", "parity", "pending", "phased", "placement-linked", "pre-2022", "predictable",
        "quarterly", "recovery", "recurring", "reduced", "regular", "repeated", "replaced", "restricted",
        "sanction", "secondary-market", "state", "statutory", "stayed", "successive", "suspended", "sustained",
        "tax-exempt", "timely", "transparent", "unconditional", "unfunded", "uniform", "uninterrupted", "universal",
        "unlimited", "up", "zero",
    ]
)
_GROUP_LOWER_OK = frozenset(
    [
        "adult", "battery", "bereaved", "citizens", "commuters", "constituency", "crisis-affected", "cultivating",
        "domestic", "drought-affected", "educated", "elderly", "electric", "electricity", "eligible", "enrolled",
        "existing", "farmers", "formal", "government", "households", "indebted", "individual", "industrial",
        "insured", "intellectually", "irrigating", "island", "leveraged", "low-consumption", "merchandise",
        "minority", "notified", "nutrition", "old-age", "post-2004", "private", "public", "residents", "retail",
        "rural", "salaried", "schoolchildren", "secondary-market", "senior", "small", "smallholder", "state",
        "targeted", "taxpayers", "traditional", "university", "urban", "vehicle", "women", "youth",
    ]
)

_CLAUSE_BREAKS = (";", " with ", " amid ", " following ", " despite ", " due to ", " citing ", " while ",
                  " alongside ", " plus ", " leaving ", " resulted in ")  # fmt: skip

_TEXT_FIELDS = (
    "scheme_name", "tranche_or_phase", "promised_metric", "revised_metric", "metric_unit", "state_or_ut",
    "walkback_document_ref", "walkback_summary", "affected_group", "signal_category", "walkback_mechanism",
)  # fmt: skip
_BUCKET_REF = re.compile(r"\s*\((LS|RS) (UQ|SQ) \d+, (\d{4}-\d{2}-\d{2})\)\s*$")
_PAREN = re.compile(r"\s+\([^()]*\)")
_DOTTED_DATE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")
_BIG_INT = re.compile(r"(?<![\d.,/-])(\d{1,3}(?:,\d{2,3})+|\d{5,})(?![\d.,]*\d|-\d)")
_NAME_TAILS = re.compile(
    r"\s+(?:Frozen Beneficiary Cap and Rate|School Education Central Grant Reduction|Central Grant Reduction|"
    r"Outlay Cut|Outlay Reduction|Allocation Reduction|Budget Execution Shortfall|Budget Delivery|"
    r"Welfare Outlay Shortfall|Delivery Shortfall|Staffing Shortfall|Adoption Shortfall|"
    r"Coverage Gap|Release and Utilisation Gap|Non-Implementation|Non-Disbursement|"
    r"Bereavement Grant Non-Release|Non-Release|State Opt-Out|Restructuring|Complete Fund Freeze|"
    r"Disbursement Contraction|Share-Sale Revision and Stay|Revenue Arrears Foregone|Subsidy Delivery|"
    r"Pending Farmer Claims|Bi-Monthly Compensation Arrears|Demand|Rural Housing Completion)$"
)


# ----------------------------------------------------------------------------------------------- text


def _regroup(match: re.Match[str]) -> str:
    return fmt_num(match.group(1).replace(",", ""), 0) or match.group(1)


def _dotted(match: re.Match[str]) -> str:
    day, month, year = int(match.group(1)), int(match.group(2)), match.group(3)
    if 1 <= month <= 12 and 1 <= day <= 31:
        return f"{day} {_MONTH_ABBR[month - 1]} {year}"
    return match.group(0)


def _tidy(value: Any, *, parens: bool = False) -> str:
    """Turn an engine phrase into reader text: rupee units, Indian grouping, no shorthand or brackets."""
    text = clean(value)
    if not text:
        return ""
    if not parens:
        text = _PAREN.sub("", text)
    text = text.replace("`", "₹")
    text = re.sub(r"(?:Rs\.?|INR)\s?(?=[\d~])", "₹", text)
    text = re.sub(r"~\s?₹", "about ₹", text)
    text = re.sub(r"₹\s+(?=\d)", "₹", text)
    text = re.sub(r"(₹[\d,.]+)\s?(?:lakh crore|Lakh Crore)\b", r"\1 lakh crore", text)
    text = re.sub(r"(₹[\d,.]+)\s?(?:Cr|cr|Crore)\b\.?", r"\1 crore", text)
    text = re.sub(r"(₹[\d,.]+)\s?(?:L|Lakh)\b", r"\1 lakh", text)
    text = re.sub(r"\bINR (?=crore|lakh)", "₹ ", text)
    text = re.sub(r"/\s?(?:yr|year)\b", " a year", text)
    text = re.sub(r"/\s?(?:mo|month)\b", " a month", text)
    text = re.sub(r"/\s?[Dd]ay\b", " a day", text)
    text = re.sub(r"/(family|cylinder|unit|kWh|instalment|acre)\b", r" per \1", text)
    text = re.sub(r"\s*->\s*", " to ", text)
    text = re.sub(r">\s?(?=\d)", "over ", text)
    text = re.sub(r"\b(\d[\d,]*)\+(?=\s)", r"over \1", text)
    text = re.sub(r"\s\+\s", " and ", text)
    text = re.sub(r"\s&\s", " and ", text)
    text = re.sub(r"\bFY\s?(?=\d{4}-\d{2})", "", text)
    text = re.sub(r"(\d{4}-\d{2}) BE\b", r"\1 budget", text)
    text = re.sub(r"(\d{4}-\d{2}) RE\b", r"\1 revised estimate", text)
    text = re.sub(r"\bBE\b", "budget", text)
    text = re.sub(r"\b[Ss]lashed\b", lambda mt: "Cut" if mt.group(0)[0] == "S" else "cut", text)
    text = _DOTTED_DATE.sub(_dotted, text)
    text = _BIG_INT.sub(_regroup, text)
    text = re.sub(r"(?<=\S)[–—](?=\S)", "-", text)
    text = _upper_first(re.sub(r"^Only\s+", "", text))
    text = re.sub(r"\s*\b(?:reported |cited )?in (?:RS|LS) Q\s?\d+\b", "", text)
    text = re.sub(r"\s*[–—]\s*", ", ", text)
    text = re.sub(r"\s*;\s*", ", ", text)
    text = re.sub(r"[!?]", "", text)
    text = re.sub(r"\s+,", ",", text)
    return clean(text).rstrip(".").strip()


def _fit(text: str, limit: int, *, floor: int = 18) -> str:
    """Return text if it fits, else its longest opening clause that does. Never cuts mid-clause."""
    text = text.strip()
    if len(text) <= limit:
        return text
    best = -1
    for sep in _CLAUSE_BREAKS:
        start = 0
        while True:
            pos = text.find(sep, start)
            if pos < 0:
                break
            if floor <= pos <= limit:
                best = max(best, pos)
            start = pos + 1
    return text[:best].rstrip(" ,") if best > 0 else ""


def _fit_source(raw: Any, limit: int) -> str:
    """Tidy then fit. Whole semicolon-separated clauses are kept while they fit, then a clause cut is tried."""
    text = clean(raw)
    if not text or limit <= 0:
        return ""
    whole = _tidy(text)
    if len(whole) <= limit:
        return whole
    out = ""
    pieces = [_tidy(p) for p in re.split(r"\s*;\s*", text)]
    for piece in filter(None, pieces):
        joined = f"{out}, {piece}" if out else piece
        if len(joined) > limit:
            break
        out = joined
    return out or _fit(next(filter(None, pieces), whole), limit)


def _lower_first(text: str, allowed: frozenset[str] = _LOWER_OK) -> str:
    if not text:
        return text
    first = re.split(r"[\s,]", text, maxsplit=1)[0]
    if first[:1].isupper() and first[1:] == first[1:].lower() and first.lower() in allowed:
        return text[0].lower() + text[1:]
    return text


def _upper_first(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def _crore(value: Any) -> str:
    """A crore figure as printed in the record, shown in lakh when it is under ₹1 crore."""
    n = num(str(value).replace(",", "") if isinstance(value, str) else value)
    if n is None or n < 0:
        return ""
    if 0 < n < 1:
        return f"₹{fmt_num(n * 100, 2)} lakh"
    return f"₹{fmt_num(n, 2)} crore"


def _int(value: Any) -> int | None:
    n = num(str(value).replace(",", "") if isinstance(value, str) else value)
    return int(n) if n is not None and n >= 0 else None


def _n(value: Any) -> str:
    n = _int(value)
    return fmt_num(n, 0) if n is not None else ""


def _year(value: Any) -> str:
    d = parse_date(value)
    return str(d.year) if d else ""


# -------------------------------------------------------------------------------------------- sources


def _ref_date(ref: str) -> str:
    """The date printed inside a document reference, as '28 Mar 2025'. Empty when none is given."""
    iso = re.search(r"\((\d{4}-\d{2}-\d{2})\)", ref)
    if iso:
        return fmt_date(iso.group(1))
    worded = re.search(r"\((\d{1,2}) ([A-Za-z]{3,9}) (\d{4})\)", ref)
    if worded:
        month = _MONTHS.get(worded.group(2)[:3].lower())
        if month:
            return f"{int(worded.group(1))} {_MONTH_ABBR[month - 1]} {worded.group(3)}"
    return ""


def _source(m: dict[str, Any]) -> str:
    """The kind of record the later figure comes from: 'a Lok Sabha answer of 28 Mar 2025'."""
    ref = clean(m.get("walkback_document_ref"))
    when = _ref_date(ref)
    dated = f" of {when}" if when else ""
    low = ref.lower()
    if low.startswith("lok sabha") or " lok sabha " in f" {low} ":
        return f"a Lok Sabha answer{dated}"
    if low.startswith("rajya sabha") or " rajya sabha " in f" {low} ":
        return f"a Rajya Sabha answer{dated}"
    if "cag" in low or "comptroller" in low:
        return "a CAG audit report"
    bill = re.search(r"Finance (?:\(No\. 2\) )?(Bill|Act),? (\d{4})", ref)
    if bill:
        return f"the Finance {bill.group(1)} {bill.group(2)}"
    budget = re.search(r"Union Budget (\d{4}-\d{2})", ref)
    if budget:
        kind = "speech" if "speech" in low else "memorandum" if "memorandum" in low else "document"
        return f"the Union Budget {budget.group(1)} {kind}"
    if low.startswith("prs"):
        return "a PRS budget analysis"
    if low.startswith("pib"):
        return "a PIB factsheet"
    return "an official record"


def _state(finding: dict[str, Any], m: dict[str, Any]) -> str:
    state = clean(m.get("state_or_ut"))
    if state:
        return state
    where = place(finding)
    return "" if where == "Union government" else where


def _rule(finding: dict[str, Any], m: dict[str, Any]) -> str:
    rule = clean(finding.get("rule_triggered")).upper()
    if rule in _FLAG:
        return rule
    category = clean(m.get("signal_category") or m.get("walkback_mechanism")).upper().replace("_", "-")
    return f"P8-{category}" if category else rule


def _join(sentences: list[str], limit: int = _BRIEF_AIM) -> str:
    """Join sentences, dropping from the end while the brief runs over its target length."""
    kept = [s.strip() for s in sentences if s and s.strip()]
    while len(kept) > 1 and len(" ".join(kept)) > limit:
        kept.pop()
    return " ".join(kept)


def _pick(options: list[str]) -> str:
    """First headline within the target length, else the first within the hard limit, else the shortest."""
    options = [o for o in options if o]
    for limit in (_HEADLINE_AIM, _HEADLINE_HARD):
        for option in options:
            if len(option) <= limit:
                return option
    return min(options, key=len) if options else ""


# -------------------------------------------------------------------------------------------- CAG


_CAG_GENERIC = re.compile(
    r"compliance|sector|report of|comptroller|c&ag|auditor|^of the|government of|performance and|"
    r"^general\b|^social\b|public sector enterprises",
    re.IGNORECASE,
)
_CAG_DANGLING = frozenset(
    [
        "rural", "national", "child", "integrated", "of", "the", "and", "for", "through", "including", "on", "in",
        "to", "year", "period", "ended", "building",
    ]
)


def _cag_subject(m: dict[str, Any], state: str) -> str:
    """What the audit was about, e.g. 'Goa state finances' or 'Jal Jeevan Mission in Chhattisgarh'."""
    name = clean(m.get("scheme_name"))
    topic = clean(name.split("—", 1)[1]) if "—" in name else ""
    if re.match(r"State Finances? Audit", topic, re.IGNORECASE):
        return f"{state} state finances" if state else "state finances"
    if not topic or _CAG_GENERIC.search(topic):
        return state
    topic = re.sub(r"^(?:Report on Performance Audit of|Implementation of|Functioning of|Outcomes of)\s+", "", topic)
    topic = re.sub(r"\s+for the (?:year|period)\b.*$", "", topic)
    topic = re.sub(r"\s+including\b.*$", "", topic)
    topic = re.sub(r"\s+in the State$", "", topic)
    topic = clean(topic)
    words = topic.split()
    if not words or words[-1].lower() in _CAG_DANGLING:
        return state
    if state and state.lower() not in topic.lower() and state.split()[0].lower() not in topic.lower():
        topic = f"{topic} in {state}"
    return topic if len(topic) <= 64 else state


_QUOTE_SIGNAL = re.compile(
    r"\b(?:not|no|short|shortfall|delay\w*|unspent|unutili[sz]ed|pending|excess|incomplete|idle|lapsed?|"
    r"surrender\w*|outstanding|without|failed|lack\w*|arrears?|loss|denied|remained|savings|diver\w+|"
    r"non-\w+|only|against)\b",
    re.IGNORECASE,
)
# Audit language that is fair in the report but too strong to lift into a one-line public brief.
_QUOTE_AVOID = re.compile(
    r"misappropriat|embezzl|fraud|corrupt|scam|bogus|fake|forg|siphon|collusi|favour|theft|criminal|illegal|"
    r"\b(?:Shri|Smt|Mr|Mrs|Ms|Dr)\b\.?",
    re.IGNORECASE,
)


_QUOTE_LEAD = re.compile(r"^(?:However|Further|Thus|Also|Moreover|Besides|Similarly|Hence|Therefore),?\s+")


def _cag_quote(m: dict[str, Any]) -> str:
    """One clean sentence from the quoted audit page, or '' when the page text is too rough to quote.

    When the finding carries a rupee figure, only the sentence that states that figure is used, so the
    quote always explains the number in the headline.
    """
    text = clean(m.get("walkback_summary"))
    text = re.sub(r"^CAG Audit Finding\s*\([^)]*\):\s*", "", text)
    text = re.sub(r"\(Paragraph[^)]*\)", "", text)
    text = clean(text.replace("`", "₹"))
    text = re.sub(r"₹\s+(?=\d)", "₹", text)
    text = re.sub(r"\(\s+", "(", text)
    sentences = [clean(p) for p in re.split(r"(?<=[A-Za-z0-9\)%])\s?\.\s+(?=[A-Z₹])", text)]
    sentences = [_upper_first(_QUOTE_LEAD.sub("", p).rstrip(".").strip()) for p in sentences if p]
    figure = re.search(r"₹\s?([\d,]+(?:\.\d+)?)\s*(?:crore|lakh)", clean(m.get("revised_metric")))
    if figure:
        sentences = [p for p in sentences if figure.group(1) in p]
    if not sentences:
        return ""
    sentence = sentences[0]
    if not (45 <= len(sentence) <= 185) or text.endswith(sentence) and not text.endswith("."):
        return ""
    if re.search(r"[;!?—–\"“”…:]|[A-Za-z]{3,}\d|\d[A-Z][a-z]|\.[A-Za-z]|\s[a-z]{1,2}\s[a-z]{1,2}\s", sentence):
        return ""
    if sentence.startswith(("Of these", "It ", "These ", "They ", "He ", "She ", "The same ")):
        return ""
    if not sentence[0].isupper() or not _QUOTE_SIGNAL.search(sentence) or _QUOTE_AVOID.search(sentence):
        return ""
    return "" if scan_forbidden_terms(sentence) else sentence


def _cag(finding: dict[str, Any], m: dict[str, Any]) -> tuple[str, str] | None:
    name = clean(m.get("scheme_name"))
    if "CAG Audit" not in name and not clean(m.get("metric_unit")).startswith("CAG audited"):
        return None
    state = _state(finding, m)
    subject = _cag_subject(m, state)
    printed = re.search(r"₹\s?([\d,]+(?:\.\d+)?)\s*(crore|lakh)\b", clean(m.get("revised_metric")))
    if printed and num(printed.group(1).replace(",", "")):
        # The figure exactly as the audit page prints it, so the headline matches the quoted sentence.
        amount = f"₹{fmt_num(printed.group(1).replace(',', ''), 2)} {printed.group(2)}"
    else:
        amount = fmt_inr(finding.get("monetary_exposure_inr"))
    lead = f"CAG audit of {subject}" if subject else "CAG audit report"
    if amount:
        headline = _pick([f"{lead} records a finding involving {amount}", f"{lead}: finding involves {amount}"])
    else:
        headline = _pick([f"{lead} records an observation on funds or delivery", f"{lead} records an observation"])
    page = re.search(r"\bp\.\s?(\d+)", clean(m.get("walkback_summary")) + " " + clean(m.get("revised_metric")))
    where = f"page {page.group(1)} of " if page else ""
    on_state = f" on {state}" if state else ""
    quote = _cag_quote(m)
    if quote:
        brief = f"From {where}a CAG audit report{on_state}. The report says: '{quote}'."
        if len(brief) <= 255:
            return headline, brief
    figure = f" The figure cited is {amount}." if amount else ""
    brief = (
        f"From {where}a CAG audit report{on_state}.{figure} "
        "The audit agents filed it as a gap between sanctioned funds and what was delivered."
    )
    return headline, brief


# ------------------------------------------------------------------------------- templated families


def _nsap_cap(finding: dict[str, Any], m: dict[str, Any]) -> tuple[str, str] | None:
    hit = re.search(r"Capped at (\d{1,2}\.\d{1,2}\.\d{4}) ceiling \(([\d,]+) cohort\)", clean(m.get("revised_metric")))
    state = _state(finding, m)
    if not hit or not state:
        return None
    since, count = _DOTTED_DATE.sub(_dotted, hit.group(1)), _n(hit.group(2))
    headline = _pick(
        [
            f"Central pension cap for {state} held at {count} beneficiaries since {since}",
            f"Central pension cap for {state} held at {count} since {since}",
        ]
    )
    brief = _join(
        [
            (f"{_upper_first(_source(m))} shows the National Social Assistance Programme cap for {state} "
            f"fixed at {count} old-age pensioners since {since}."),
            "The central pension rate is unchanged and no revision is proposed.",
        ]
    )
    return headline, brief


def _minority_scholarship(finding: dict[str, Any], m: dict[str, Any]) -> tuple[str, str] | None:
    hit = re.search(
        r"INR ([\d.,]+) Cr in (\d{4}-\d{2}) to INR ([\d.,]+) Cr in (\d{4}-\d{2})", clean(m.get("tranche_or_phase"))
    )
    state = _state(finding, m)
    if not hit or not state or "scholarship" not in clean(m.get("scheme_name")).lower():
        return None
    before, y1, after, y2 = _crore(hit.group(1)), hit.group(2), _crore(hit.group(3)), hit.group(4)
    if not before or not after:
        return None
    headline = _pick(
        [
            f"Minority scholarship funds for {state} fell from {before} to {after} in {y2}",
            f"Minority scholarship funds for {state} fell to {after} in {y2}",
        ]
    )
    more = "The record says the schemes were not approved beyond 2021-22."
    if "not approved beyond 2021-22" not in clean(m.get("revised_metric")):
        more = ""
    brief = _join(
        [
            (f"{_upper_first(_source(m))} shows sanctioned minority scholarship funds for {state} fell from "
            f"{before} in {y1} to {after} in {y2}."),
            more,
        ]
    )
    return headline, brief


def _sgb_tranche(finding: dict[str, Any], m: dict[str, Any]) -> tuple[str, str] | None:
    hit = re.search(
        r"SGB (\d{4}-\d{2}) Series ([IVXLC]+) \(Issued (\d{4}-\d{2}-\d{2}), Matures (\d{4}-\d{2}-\d{2})",
        clean(m.get("tranche_or_phase")),
    )
    revised = clean(m.get("revised_metric"))
    if not hit or "riginal-subscriber-only" not in revised:
        return None
    series = f"{hit.group(1)} Series {hit.group(2)}"
    issued, matures = fmt_date(hit.group(3)), fmt_date(hit.group(4))
    start = re.search(r"from (\d{1,2} [A-Z][a-z]{2} \d{4})", revised)
    since = f" from {start.group(1)}" if start else ""
    headline = f"Gold bond {series}: tax-free redemption now only for original buyers"
    size = fmt_inr(finding.get("monetary_exposure_inr"))
    brief = _join(
        [
            f"{_upper_first(_source(m))} limits the tax exemption at maturity to original subscribers{since}.",
            (f"This Sovereign Gold Bond was issued on {issued} and matures on {matures}, "
            "and those who bought it on the exchange will be taxed."),
            f"The series raised {size}." if size else "",
        ]
    )
    return headline, brief


def _mgnrega_delay(finding: dict[str, Any], m: dict[str, Any]) -> tuple[str, str] | None:
    hit = re.search(
        r"([\d,]+) Delay Compensation Claims Logged vs ([\d,]+) Processed/Rejected", clean(m.get("tranche_or_phase"))
    )
    state = _state(finding, m)
    if not hit or not state:
        return None
    logged, done = _n(hit.group(1)), _n(hit.group(2))
    claims = "claim" if logged == "1" else "claims"
    headline = _pick(
        [
            f"{state}: {logged} MGNREGA late-wage {claims} logged, {done} processed or rejected",
            f"{state}: {logged} MGNREGA late-wage {claims} logged",
        ]
    )
    promise = ""
    if "0.05%" in clean(m.get("promised_metric")) and "16 days" in clean(m.get("promised_metric")):
        promise = "The law provides 0.05% a day in compensation when wages are more than 16 days late."
    brief = _join(
        [
            (f"{_upper_first(_source(m))} shows {logged} compensation {claims} for late MGNREGA wages logged in "
            f"{state}, with {done} shown as processed or rejected."),
            promise,
        ]
    )
    return headline, brief


def _cho_vacancies(finding: dict[str, Any], m: dict[str, Any]) -> tuple[str, str] | None:
    hit = re.search(
        r"([\d,]+) rural Community Health Officer \(CHO\) positions vacant as on (\d{1,2}\.\d{1,2}\.\d{4})",
        clean(m.get("revised_metric")),
    )
    state = _state(finding, m)
    if not hit or not state:
        return None
    count, as_on = _n(hit.group(1)), _DOTTED_DATE.sub(_dotted, hit.group(2))
    posts = "post" if count == "1" else "posts"
    headline = _pick(
        [
            f"{state} has {count} vacant community health officer {posts} in rural centres",
            f"{state}: {count} rural community health officer {posts} vacant",
        ]
    )
    brief = _join(
        [
            (f"{_upper_first(_source(m))} lists {count} vacant Community Health Officer {posts} in rural "
            f"Ayushman Arogya Mandirs in {state} as on {as_on}."),
            "The scheme's aim is a health officer at every such centre.",
        ]
    )
    return headline, brief


def _pm_kusum(finding: dict[str, Any], m: dict[str, Any]) -> tuple[str, str] | None:
    sanctioned = re.search(r"^([\d,]+) sanctioned solar pumps/feeders", clean(m.get("promised_metric")))
    done = re.search(
        r"Only ([\d,]+) installed \(([\d.]+)% delivery; ([\d,]+) pending", clean(m.get("revised_metric"))
    )
    state = _state(finding, m)
    if not sanctioned or not done or not state:
        return None
    total, installed, pct, pending = _n(sanctioned.group(1)), _n(done.group(1)), done.group(2), _n(done.group(3))
    pct = fmt_num(pct, 1)
    headline = _pick(
        [
            f"{state}: {installed} of {total} sanctioned PM-KUSUM solar pumps and feeders installed",
            f"{state}: {installed} of {total} PM-KUSUM solar units installed",
        ]
    )
    brief = _join(
        [
            (f"{_upper_first(_source(m))} shows {installed} of {total} sanctioned solar pumps and feeders "
            f"installed in {state} under PM-KUSUM, or {pct}%."),
            f"That leaves {pending} pending.",
        ]
    )
    return headline, brief


def _gst_compensation(finding: dict[str, Any], m: dict[str, Any]) -> tuple[str, str] | None:
    hit = re.search(r"Compensation Pending: INR ([\d.,]+) Cr", clean(m.get("tranche_or_phase")))
    state = _state(finding, m)
    if not hit or not state or "Apr–Nov 2020" not in clean(m.get("revised_metric")):
        return None
    due = _crore(hit.group(1))
    headline = f"{state} was owed {due} in GST compensation for April to November 2020"
    borrowed = "The record says the gap was moved to a special borrowing window."
    if "special borrowing window" not in clean(m.get("revised_metric")):
        borrowed = ""
    brief = _join(
        [
            (f"{_upper_first(_source(m))} shows {due} of GST compensation for April to November 2020 "
            f"was pending for {state}."),
            borrowed,
        ]
    )
    return headline, brief


def _pm_kisan(finding: dict[str, Any], m: dict[str, Any]) -> tuple[str, str] | None:
    hit = re.search(
        r"Instalment Outlay Declined from INR ([\d.,]+) Cr to INR ([\d.,]+) Cr Across Recent Cycles"
        r"(?: \(([\d,]+) Farmers\))?",
        clean(m.get("tranche_or_phase")),
    )
    state = _state(finding, m)
    if not hit or not state:
        return None
    before, after = _crore(hit.group(1)), _crore(hit.group(2))
    farmers = _n(hit.group(3)) if hit.group(3) else ""
    headline = _pick(
        [
            f"PM-KISAN payout per instalment in {state} fell from {before} to {after}",
            f"PM-KISAN instalment payout in {state} fell to {after}",
        ]
    )
    covering = f", covering {farmers} farmers" if farmers else ""
    after_checks = ""
    if "e-KYC" in clean(m.get("revised_metric")):
        after_checks = "The record places the fall after e-KYC and land record checks became compulsory."
    brief = _join(
        [
            (f"{_upper_first(_source(m))} shows the PM-KISAN payout per instalment in {state} fell from "
            f"{before} to {after}{covering}."),
            after_checks,
        ]
    )
    return headline, brief


def _pmfby_pending(finding: dict[str, Any], m: dict[str, Any]) -> tuple[str, str] | None:
    hit = re.search(
        r"INR ([\d.,]+) Cr Pending Claims Out of INR ([\d.,]+) Cr Reported \(.*?as on (\d{1,2}\.\d{1,2}\.\d{4})\)",
        clean(m.get("tranche_or_phase")),
    )
    state = _state(finding, m)
    if not hit or not state:
        return None
    pending, total, as_on = _crore(hit.group(1)), _crore(hit.group(2)), _DOTTED_DATE.sub(_dotted, hit.group(3))
    if not pending or not total:
        return None
    headline = f"{state}: {pending} of {total} in crop insurance claims still unpaid"
    brief = _join(
        [
            (f"{_upper_first(_source(m))} shows {pending} of {total} in reported crop insurance claims "
            f"for farmers in {state} was pending on {as_on}."),
            "The scheme's aim is timely settlement of every verified claim.",
        ]
    )
    return headline, brief


def _ev_share(finding: dict[str, Any], m: dict[str, Any]) -> tuple[str, str] | None:
    hit = re.search(
        r"([\d.]+)% EV Share \(([\d,]+) EVs of ([\d,]+) Total Vehicles Registered, vs ([\d.]+)% National Average\)",
        clean(m.get("tranche_or_phase")),
    )
    state = _state(finding, m)
    if not hit or not state:
        return None
    share, evs, total, national = hit.group(1), _n(hit.group(2)), _n(hit.group(3)), hit.group(4)
    headline = _pick(
        [
            f"Electric vehicles were {share}% of registrations in {state}, against {national}% nationally",
            f"Electric vehicles were {share}% of registrations in {state}",
        ]
    )
    brief = _join(
        [
            (f"{_upper_first(_source(m))} shows {evs} of {total} vehicles registered in {state} "
            f"were electric over the FAME-II subsidy period."),
            f"That is {share}%, against a national average of {national}%.",
        ]
    )
    return headline, brief


def _nfbs(finding: dict[str, Any], m: dict[str, Any]) -> tuple[str, str] | None:
    hit = re.search(
        r"Zero Central NFBS Releases Across FY (\d{4}-\d{2}) & FY (\d{4}-\d{2})", clean(m.get("tranche_or_phase"))
    )
    state = _state(finding, m)
    if not hit or not state:
        return None
    y1, y2 = hit.group(1), hit.group(2)
    headline = _pick(
        [
            f"{state} got no central family benefit scheme funds in {y1} or {y2}",
            f"{state}: no central family benefit funds in {y1} or {y2}",
        ]
    )
    grant = ""
    if "INR 20,000" in clean(m.get("promised_metric")):
        grant = "The scheme gives ₹20,000 to poor families that lose their breadwinner."
    brief = _join(
        [
            (f"{_upper_first(_source(m))} shows no central release to {state} under the National Family "
            f"Benefit Scheme in {y1} or {y2}."),
            grant,
        ]
    )
    return headline, brief


_FAMILIES: tuple[Callable[[dict[str, Any], dict[str, Any]], tuple[str, str] | None], ...] = (
    _cag,
    _nsap_cap,
    _minority_scholarship,
    _sgb_tranche,
    _mgnrega_delay,
    _cho_vacancies,
    _pm_kusum,
    _gst_compensation,
    _pm_kisan,
    _pmfby_pending,
    _ev_share,
    _nfbs,
)


# -------------------------------------------------------------------------------------------- generic


def _scheme_name(m: dict[str, Any], state: str) -> str:
    """A short, readable name for the scheme, without bracketed lists or trailing diagnosis words."""
    raw = clean(m.get("scheme_name"))
    if not raw:
        return ""
    parts = [clean(p) for p in re.split(r"\s+[—–]\s+", raw) if clean(p)]
    name = raw
    if len(parts) >= 2:
        name = f"{parts[0]} {parts[1]}" if state and parts[0].lower() == state.lower() else parts[0]
    acronym = re.fullmatch(r"(.{30,}?) \((PM-[A-Z]+|MGNREGA)\)", name)
    if acronym:
        return acronym.group(2)
    for _ in range(3):
        name = re.sub(r"\s*\([^()]*\)", "", name)
    name = re.sub(r",? \d{4}$", "", name)
    name = re.sub(r"\s&\s", " and ", name)
    name = re.sub(r"\bINR\s?(?=\d)", "₹", name)
    name = re.sub(r"\s*[—–]\s*", " ", name)
    name = _NAME_TAILS.sub("", clean(name))
    name = re.sub(r"\s+FY\s?\d{4}-\d{2}$", "", name)
    return clean(name)


def _already_stated(rupees: Any, text: str) -> bool:
    """True when text already gives this rupee amount (to within 1%), so it is not printed twice."""
    target = num(rupees)
    if not target or target <= 0:
        return False
    scale = {"lakh crore": 1e12, "crore": 1e7, "lakh": 1e5}
    for figure, unit in re.findall(r"₹([\d,]+(?:\.\d+)?) (lakh crore|crore|lakh)", text):
        value = num(figure.replace(",", ""))
        if value is not None and abs(value * scale[unit] - target) <= 0.01 * target:
            return True
    return False


def _launch(m: dict[str, Any]) -> str:
    year = _year(m.get("promise_date"))
    if not year:
        n = _int(m.get("launch_year"))
        year = str(n) if n and 1900 < n < 2100 else ""
    return year


def _bucket(finding: dict[str, Any], m: dict[str, Any]) -> tuple[str, str] | None:
    """A parliamentary answer the engine matched to a national scheme by key words in the reply."""
    tranche = clean(m.get("tranche_or_phase"))
    ref = _BUCKET_REF.search(tranche)
    if not ref:
        return None
    house = "Lok Sabha" if ref.group(1) == "LS" else "Rajya Sabha"
    dated = fmt_date(ref.group(3))
    subject = clean(_BUCKET_REF.sub("", tranche)).rstrip(".")
    subject = re.sub(r"\s&\s", " and ", re.sub(r"[;!?\"“”]", "", subject))
    scheme = _scheme_name(m, "")
    long_flag, short_flag = _FLAG.get(_rule(finding, m), _FLAG_DEFAULT)
    headline = ""
    if subject:
        headline = _pick(
            [
                f"{house} reply on {subject} flagged for {long_flag}",
                f"{house} reply on {subject} flagged for {short_flag}",
                f"Reply on {subject} flagged for {short_flag}",
            ]
        )
    if not headline or len(headline) > _HEADLINE_HARD:
        # Scheme names here are often lists ("Skill India, NSAP Pensions and Electoral Bonds"): the first item
        # is enough to place the reply when the full name does not fit.
        first = clean(re.split(r",| and | / ", scheme)[0]) if scheme else ""
        fallback = [f"{house} reply on {name} flagged for {short_flag}" for name in (scheme, first) if name]
        headline = _pick([*fallback, f"{house} reply on a central scheme flagged for {short_flag}"])

    year = _launch(m)
    when = f" ({year})" if year else ""
    tail = f"The reply is dated {dated}." if dated else ""
    revised_raw, promised_raw = m.get("revised_metric"), m.get("promised_metric")
    later = _lower_first(_fit_source(revised_raw, 150) or _tidy(revised_raw))
    promise_frame = f"{scheme or 'Scheme'} promise{when}: "
    if later:
        later_sentence = f"Scheme record now notes: {later}."
        promised = _fit_source(promised_raw, 250 - len(later_sentence) - len(promise_frame) - 2)
        if promised:
            return headline, _join([f"{promise_frame}{_lower_first(promised)}.", later_sentence, tail], 255)
        named = f"Under {scheme}, the scheme" if scheme else "The scheme"
        return headline, _join([f"{named} record now notes: {later}.", tail], 255)
    promised = _fit_source(promised_raw, 150)
    if promised:
        return headline, _join([f"{promise_frame}{_lower_first(promised)}.", tail], 255)
    return headline, _join([f"The reply was matched to {scheme or 'a central scheme'}.", tail])


def _generic(finding: dict[str, Any], m: dict[str, Any]) -> tuple[str, str] | None:
    state = _state(finding, m)
    name = _scheme_name(m, state)
    if not name:
        return None
    long_flag, short_flag = _FLAG.get(_rule(finding, m), _FLAG_DEFAULT)
    revised_raw, promised_raw = m.get("revised_metric"), m.get("promised_metric")
    whole = _tidy(revised_raw)
    if not whole and not _tidy(promised_raw):
        return None
    options = []
    carried = ""
    for limit in (_HEADLINE_AIM, _HEADLINE_HARD):
        what = _fit_source(revised_raw, limit - len(name) - 2)
        if what:
            carried = what
            options.append(f"{name}: {_lower_first(what)}")
            break
    if not options and state and len(name) > 55:
        # A long case name leaves no room for the figure, so the place leads and the brief names the scheme.
        what = _fit_source(revised_raw, _HEADLINE_HARD - len(state) - 2)
        if what:
            carried = what
            options.append(f"{state}: {_lower_first(what)}")
    options += [f"{name}: flagged for {long_flag}", f"{name}: flagged for {short_flag}"]
    headline = _pick(options)
    in_headline = bool(carried) and not headline.endswith((long_flag, short_flag))
    full_in_headline = in_headline and carried == whole

    year = _launch(m)
    when = f" ({year})" if year else ""
    source = _source(m)
    amount = fmt_inr(finding.get("monetary_exposure_inr"))
    money = f"Amount involved: {amount}." if amount else ""
    group = _tidy(m.get("affected_group"))
    affected = f"Those affected: {_lower_first(group, _GROUP_LOWER_OK)}." if group and len(group) <= 95 else ""
    recorded = f"Recorded in {source}."

    sentences: list[str] = []
    if full_in_headline:
        promised = _fit_source(promised_raw, 140)
        if promised:
            sentences.append(f"Earlier commitment{when}: {promised}.")
        sentences += [recorded, money, affected]
    else:
        later = _fit_source(revised_raw, 150) or whole
        promise_frame = f"Earlier commitment{when}: "
        later_sentence = f"{_upper_first(source)} records: {_lower_first(later)}." if later else recorded
        promised = _fit_source(promised_raw, 253 - len(later_sentence) - len(promise_frame) - 2)
        if promised:
            sentences.append(f"{promise_frame}{promised}.")
        sentences += [later_sentence, money]
    said = " ".join([headline, *sentences]).replace(money, "") if money else ""
    if money and _already_stated(finding.get("monetary_exposure_inr"), said):
        sentences = [s for s in sentences if s != money]
    brief = _join(sentences, 255)
    return (headline, brief) if headline and brief else None


# ---------------------------------------------------------------------------------------------- entry


def _finish(headline: str, brief: str) -> tuple[str, str]:
    def scrub(text: str) -> str:
        text = re.sub(r"\s*[—–]\s*", ", ", text)
        text = re.sub(r"\s*;\s*", ", ", text)
        text = re.sub(r"[!?]", "", text)
        text = re.sub(r"\.{2,}", ".", text)
        text = re.sub(r"\s+([,.])", r"\1", text)
        return re.sub(r"\s+", " ", text).strip()

    headline = scrub(headline).rstrip(".").strip()
    brief = scrub(brief)
    if brief and not brief.endswith("."):
        brief += "."
    return headline, brief


def render(finding: dict[str, Any]) -> tuple[str, str]:
    if not isinstance(finding, dict):
        return "", ""
    # Wrong-typed fields (lists, dicts, booleans) are treated as missing, never printed.
    m = {k: v for k, v in metrics(finding).items() if isinstance(v, (str, int, float)) and not isinstance(v, bool)}
    m.update({k: "" for k in _TEXT_FIELDS if not isinstance(m.get(k, ""), str)})
    if not m:
        return "", ""
    for writer in (*_FAMILIES, _bucket, _generic):
        try:
            out = writer(finding, m)
        except Exception:  # noqa: BLE001 - a malformed field falls through to the next, more general writer
            out = None
        if not out or not out[0] or not out[1]:
            continue
        headline, brief = _finish(*out)
        if not headline or not brief or scan_forbidden_terms(f"{headline} {brief}"):
            continue
        return headline, brief
    return "", ""
