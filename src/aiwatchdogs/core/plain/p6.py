"""Plain-language writer for P6: assurances ministers gave in Parliament and what happened to them."""

from __future__ import annotations

import re
from typing import Any

from ._common import clean, fmt_date, fmt_days, metrics, num

_HEADLINE_AIM = 94
_HEADLINE_LONG = 104
_BRIEF_AIM = 235

_SMALL_WORDS = frozenset(
    [
        "a", "an", "and", "as", "at", "by", "for", "from", "in", "into", "of", "on", "or", "the", "to", "under",
        "upon", "via", "vs", "with", "without",
    ]
)
# Abbreviations that must stay upper-case when an ALL CAPS subject is turned into title case.
_ACRONYMS = frozenset(
    [
        "AI", "AIIA", "AIIMS", "AMRUT", "ATM", "AYUSH", "BHEL", "BPCL", "BSNL", "CAG", "CBI", "CCI", "CGHS", "CNG",
        "COTPA", "CRRI", "CSR", "DAE", "DNT", "DPDP", "DRDO", "ED", "ELI", "EMRS", "EPF", "EPFO", "ESIC", "EV",
        "EWS", "FCI", "FDI", "G20", "GDP", "GST", "HAL", "HPCL", "IBC", "ICDS", "ICMR", "IES", "IIM", "IIT", "IOCL",
        "ISRO", "ITI", "IVF", "KYC", "LIC", "LPG", "MGNREGA", "MSME", "MSP", "NAVIC", "NCDC", "NCM", "NE",
        "NEP", "NER", "NFSA", "NGO", "NHAI", "NIA", "NIT", "NPS", "NRI", "NTA", "NTPC", "OBC", "ONGC", "ORIDL",
        "OTT", "PAC", "PACS", "PDS", "PLI", "PM", "PMAY", "PMFBY", "PMGSY", "PMIS", "PMKVY", "POCSO", "PPP", "PSU",
        "QR", "RBI", "SAI", "SAIL", "SC", "SCL", "SCM", "SEBI", "SEZ", "SHG", "SPRM", "ST", "TDB", "TLP", "TRP",
        "UDAN", "UGC", "UPI", "USA", "VB",
    ]
)
_SPECIAL_CASE = {"INDIAAI": "IndiaAI", "G-RAM": "G-RAM", "NAVIC": "NavIC", "MSMES": "MSMEs", "PSUS": "PSUs"}
_ROMAN = re.compile(r"(II|III|IV|VI|VII|VIII|IX)")
_SUPPLEMENTARY = re.compile(r"^supplementary\s+by\s+[^:]{0,80}:\s*", re.IGNORECASE)
_QUOTES = re.compile(r"[\"“”‘’`]")


def _title_word(word: str, first: bool) -> str:
    bare = word.strip("().,:;'")
    if not bare:
        return word
    upper = bare.upper()
    if upper in _SPECIAL_CASE:
        return word.upper().replace(upper, _SPECIAL_CASE[upper])
    if re.fullmatch(r"\d+(ST|ND|RD|TH)", upper):
        return word.lower()
    if upper in _ACRONYMS or _ROMAN.fullmatch(upper) or any(ch.isdigit() for ch in bare):
        return word.upper()
    if upper.endswith("S") and upper[:-1] in _ACRONYMS:
        return word.upper()[:-1] + "s" if word.upper().endswith("S") else word.upper()
    if "-" in bare:
        return "-".join(_title_word(part, first and i == 0) for i, part in enumerate(word.split("-")))
    lower = word.lower()
    if not first and bare.lower() in _SMALL_WORDS:
        return lower
    # Capitalise the first letter, skipping an opening bracket.
    for i, ch in enumerate(lower):
        if ch.isalpha():
            return lower[:i] + ch.upper() + lower[i + 1 :]
    return lower


def _subject(m: dict[str, Any]) -> str:
    """The question's subject line, tidied: no quotes, no trailing stop, no shouting capitals."""
    text = clean(m.get("subject"))
    text = _SUPPLEMENTARY.sub("", text)
    text = _QUOTES.sub("", text)
    text = re.sub(r"\s*[–—]\s*", ", ", text).replace(";", ",")
    text = clean(text).rstrip(".!?").strip()
    letters = [ch for ch in text if ch.isalpha()]
    if letters and sum(ch.isupper() for ch in letters) / len(letters) > 0.8:
        words = text.split(" ")
        text = " ".join(_title_word(w, i == 0) for i, w in enumerate(words))
    return text


def _house(m: dict[str, Any]) -> str:
    house = clean(m.get("house"))
    if house in ("Lok Sabha", "Rajya Sabha"):
        return house
    code = clean(m.get("house_code")).upper()
    return {"LS": "Lok Sabha", "RS": "Rajya Sabha"}.get(code, "")


def _ministry(finding: dict[str, Any], m: dict[str, Any]) -> str:
    """'The Ministry of Railways', 'The Department of Space', 'NITI Aayog'."""
    entity = finding.get("entity_name")
    name = clean(entity) if isinstance(entity, str) else ""
    if not name:
        short = clean(m.get("ministry"))
        name = f"Ministry of {short}" if short else ""
    if not name:
        return "The ministry"
    if name.lower().startswith(("ministry", "department")):
        return f"The {name}"
    return name


def _count(value: Any) -> int | None:
    n = num(value)
    return int(n) if n is not None and n > 0 else None


def _times(n: int) -> str:
    return "once" if n == 1 else "twice" if n == 2 else f"{n} times"


def _lead(house: str, subject: str, tails: tuple[str, ...], ministry: str) -> str:
    """Pick the fullest headline that still fits: house and subject, subject alone, or the ministry.

    ``tails`` lists the ending from most to least detailed, so a long subject gets the shorter ending
    before the subject itself is given up.
    """
    owner = ministry.removeprefix("The ")
    options: list[str] = []
    if subject:
        if house:
            options += [f"{house} promise on {subject} {tail}" for tail in tails[:1]]
        options += [f"Promise on {subject} {tail}" for tail in tails]
    if house:
        options += [f"{owner} promise to {house} {tail}" for tail in tails]
    options += [f"{owner} promise in Parliament {tail}" for tail in tails]
    for option in options:
        if len(option) <= _HEADLINE_AIM:
            return option
    # A slightly long headline that names the subject beats a short one that only names the ministry.
    for option in options:
        if len(option) <= _HEADLINE_LONG and " promise on " in f" {option[0].lower()}{option[1:]}":
            return option
    return min(options, key=len)


def _brief(sentences: list[str]) -> str:
    """Join sentences, dropping the last ones while the brief is over its target length."""
    kept = [s for s in sentences if s]
    while len(kept) > 2 and len(" ".join(kept)) > _BRIEF_AIM:
        kept.pop()
    return " ".join(kept)


def _opening(ministry: str, house: str, subject: str, promise_date: str, headline: str) -> str:
    where = f" in the {house}" if house else " in Parliament"
    when = f" on {promise_date}" if promise_date else ""
    if subject and subject not in headline:
        # A subject too long for the headline is carried here instead. The headline then names the ministry.
        said = "" if house and house in headline else where
        return f"The assurance was given{said}{when}. It was on {subject}."
    return f"{ministry} gave this assurance{where}{when}."


def render(finding: dict[str, Any]) -> tuple[str, str]:
    if not isinstance(finding, dict):
        return "", ""
    # Wrong-typed fields (lists, dicts, booleans) are treated as missing, never printed.
    m = {k: v for k, v in metrics(finding).items() if isinstance(v, (str, int, float)) and not isinstance(v, bool)}
    m.update({k: "" for k in ("subject", "house", "house_code", "ministry") if not isinstance(m.get(k, ""), str)})
    rule = clean(finding.get("rule_triggered")).upper()
    category = clean(m.get("signal_category")).lower()
    status = clean(m.get("recorded_status")).lower()
    text_status = clean(m.get("followup_text_status")).lower()

    subject = _subject(m)
    house = _house(m)
    ministry = _ministry(finding, m)
    if not subject and ministry == "The ministry" and not house:
        return "", ""

    promise_date = fmt_date(m.get("promise_date"))
    as_of = fmt_date(m.get("as_of_date"))
    followup_date = fmt_date(m.get("followup_date"))
    elapsed = fmt_days(m.get("days_elapsed"))
    took = fmt_days(m.get("fulfillment_days"))
    norm = _count(m.get("norm_days"))
    norm_note = f"The norm is {norm} days." if norm else ""
    extensions = _count(m.get("extensions_count"))
    extended_to = fmt_date(m.get("extension_upto"))

    def named(headline: str) -> bool:
        return bool(subject) and subject in headline

    def opening(headline: str) -> str:
        return _opening(ministry, house, subject, promise_date, headline)

    if rule == "P6-OVERDUE-PENDING" or category == "overdue_pending_assurance" or status == "pending":
        tails = (f"still pending after {elapsed}", f"pending {elapsed}") if elapsed else ("still pending",)
        headline = _lead(house, subject, tails, ministry)
        by = f" by {as_of}" if as_of else ""
        second = f"No implementation statement had been laid{by}."
        if extensions:
            latest = f", the latest to {extended_to}" if extended_to else ""
            third = f"The deadline was extended {_times(extensions)}{latest}."
        elif extended_to:
            third = f"The deadline was extended to {extended_to}."
        else:
            third = norm_note
        return headline, _brief([opening(headline), second, third])

    if rule == "P6-DROPPED-ASSURANCE" or category == "dropped_without_fulfillment" or status == "dropped":
        tails = ("dropped with no implementation statement", "dropped without a statement")
        headline = _lead(house, subject, tails, ministry)
        when = f" on {followup_date}" if followup_date else ""
        after = f", {took} after it was given" if took and followup_date else ""
        if not named(headline):
            after = ""
        second = f"Parliament records list it as dropped{when}{after}, with no implementation statement laid."
        return headline, _brief([opening(headline), second])

    if rule == "P6-PARTLY-IMPLEMENTED" or category == "partly_implemented_assurance" or status == "partly_implemented":
        tails = (f"only partly met after {elapsed}",) if elapsed else ("only partly met",)
        headline = _lead(house, subject, tails, ministry)
        by = f" as of {as_of}" if as_of else ""
        second = f"Parliament records list it as partly implemented{by}."
        return headline, _brief([opening(headline), second, norm_note])

    if text_status == "scanned_ir_pdf_no_text_layer":
        tails = ("marked done, but the report is an unreadable scan", "marked done, report unreadable")
        headline = _lead(house, subject, tails, ministry)
        pages = _count(m.get("secondary_pdf_pages"))
        report = f"{pages}-page report" if pages else "report"
        second = f"Records mark it fully implemented, but the linked {report} is "
        second += "a scanned image with no readable text." if named(headline) else "an unreadable scan."
        return headline, _brief([opening(headline), second])

    if took:
        tails = (f"marked fulfilled after {took}", f"fulfilled after {took}")
        headline = _lead(house, subject, tails, ministry)
        laid = f" in a statement laid on {followup_date}" if followup_date else ""
        if followup_date and not named(headline):
            laid = f" on {followup_date}"
        second = f"Records mark it fully implemented{laid}, {took} later."
        note = "The statement's text is not shown online." if text_status == "statement_ref_only" else ""
        return headline, _brief([opening(headline), second, norm_note, note])

    if category == "delayed_or_unreadable_followup" or status == "fully_implemented":
        tails = ("marked fulfilled, but no follow-up text is on record", "marked fulfilled, follow-up missing")
        headline = _lead(house, subject, tails, ministry)
        second = "Records mark it fully implemented, but no readable follow-up statement is linked."
        return headline, _brief([opening(headline), second])

    return "", ""
