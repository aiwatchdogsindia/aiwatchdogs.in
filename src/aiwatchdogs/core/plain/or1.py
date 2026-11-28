"""OR1: CAG audit report passages that use duplicate-billing or agency-overlap wording.

The engine is a phrase match over the report text, so every field describes the wording of a
passage, not a payment:

* ``matched_signals``: the phrases found, lower-cased ("double payment", "same invoice", "two bills").
  A match can be incidental: "two bills" also matches "two Bills of Entry".
* ``agencies_mentioned``: ministries, departments and public bodies named in the heading or passage,
  in the order they appear. The first one is not necessarily the audited body or the payer.
* ``amount_inr``: the largest rupee figure in the matched sentences, or in the passage when those
  cite none. It is 0 when no figure is cited, and it is not necessarily a sum paid twice.
* ``page``: the page of the report PDF. ``para_number`` falls back to "p.<page>" when no numbered
  paragraph encloses the passage.

So the text reports where the wording sits and what the passage cites, never who paid whom twice,
and no named body is made the subject of the headline.
"""

from __future__ import annotations

import re
from typing import Any

from ._common import clean, fmt_inr, fmt_num, metrics, num

_REPORT_NO = re.compile(r"Report\s+No\.?\s*(\d{1,3})\s+of\s+((?:19|20)\d{2})\b", re.IGNORECASE)
_SECTION = re.compile(r"under section '(.+?)'\) records ")
_SECTOR = re.compile(r"^Union Government \(([^()]+)\)")
_LEADING_NUMBER = re.compile(r"^(?:Chapter\s+[IVXLC\d]+|[\d.]+)\s*[:.-]?\s+", re.IGNORECASE)
_PARA = re.compile(r"^\d{1,3}(?:\.\d{1,3}){1,4}$")
_YEAR = re.compile(r"^(?:19|20)\d{2}$")
# Only the engine's fixed phrases are quoted. Its "already paid ... again" patterns capture free text.
_QUOTABLE = re.compile(r"^(?!already\b)[a-z]+(?:[ -][a-z]+){0,3}$")
_BODY = re.compile(r"^[A-Za-z][A-Za-z&' -]+$")
_SECTOR_OK = re.compile(r"^[A-Za-z][A-Za-z&, -]+$")
_TRAILING_JOINER = re.compile(r"\s+(?:and|of|for|the)$", re.IGNORECASE)
_NOT_TEXT = {"none", "nan", "null", "inf", "true", "false", "n/a", "na", "unknown", "unspecified"}
_GENERIC_SECTIONS = {"summing up", "conclusion", "conclusions", "introduction", "recommendation", "recommendations"}
_HEADLINE_TARGET = 95
_BODY_MAX = 44
_SECTOR_MAX = 60
_SECTION_MAX = 80
_BRIEF_ROOM = 240
_AMOUNT_MAX = 1e14
_CAVEAT = "This is a match on wording, not a finding about any one payment."
_CAVEAT_OVERLAP = "This is a match on wording, not a finding about any one project or payment."


def _text(value: Any) -> str:
    """A cleaned string, or '' for anything that is not real text (None, numbers, lists, 'nan')."""
    if not isinstance(value, str):
        return ""
    text = clean(value)
    return "" if text.lower() in _NOT_TEXT else text


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = _text(item)
        if text and text not in out:
            out.append(text)
    return out


def _phrases(m: dict[str, Any]) -> list[str]:
    return [p for p in (s.lower() for s in _strings(m.get("matched_signals"))) if _QUOTABLE.match(p)]


def _bodies(m: dict[str, Any]) -> list[str]:
    """Named bodies fit to print. The engine's pattern can run a name into the words that follow it."""
    out: list[str] = []
    for raw in _strings(m.get("agencies_mentioned")):
        # A capitalised "The" inside a name marks where a second name began: "... Services The New India ...".
        name = clean(re.split(r"\s+The\s+", raw, maxsplit=1)[0])
        name = clean(_TRAILING_JOINER.sub("", name))
        if name.isupper() and len(name) > 8:
            name = name.title().replace(" Of ", " of ").replace(" And ", " and ")
        if name and len(name) <= _BODY_MAX and _BODY.match(name) and name not in out:
            out.append(name)
    return out


def _report(m: dict[str, Any]) -> str:
    found = _REPORT_NO.search(_text(m.get("report_no")))
    if found:
        return f"CAG Report No. {int(found.group(1))} of {found.group(2)}"
    raw_year = m.get("report_year")
    year = clean(raw_year) if isinstance(raw_year, (str, int)) and not isinstance(raw_year, bool) else ""
    return f"a {year} CAG audit report" if _YEAR.match(year) else "a CAG audit report"


def _sector(finding: dict[str, Any]) -> str:
    found = _SECTOR.match(_text(finding.get("entity_name")))
    if not found:
        return ""
    text = re.sub(r"\s*,\s*", ", ", clean(found.group(1)))
    if len(text) > _SECTOR_MAX or not _SECTOR_OK.match(text):
        return ""
    # Sector labels are generic title-case words. A short all-caps token is an abbreviation and stays as printed.
    if text.isupper():
        return text.lower()
    return " ".join(w if w.isupper() and len(w) > 1 else w.lower() for w in text.split())


def _section(finding: dict[str, Any]) -> str:
    found = _SECTION.search(_text(finding.get("summary")))
    if not found:
        return ""
    text = clean(_LEADING_NUMBER.sub("", clean(found.group(1))))
    if text.isupper():
        text = text.capitalize()
    if len(text) < 4 or len(text) > _SECTION_MAX or '"' in text or not re.search(r"[A-Za-z]{3}", text):
        return ""
    return "" if text.lower() in _GENERIC_SECTIONS else text


def _page(m: dict[str, Any]) -> int:
    n = num(m.get("page"))
    return int(n) if n is not None and n == int(n) and 0 < n <= 20000 else 0


def _amount(finding: dict[str, Any]) -> str:
    """The cited figure as the report prints it: lakh amounts keep two decimals (₹10.08 lakh)."""
    m = metrics(finding)
    if m.get("amount_disclosed") is False:
        return ""
    n = num(m.get("amount_inr"))
    if n is None or n <= 0:
        n = num(finding.get("monetary_exposure_inr"))
    if n is None or n <= 0 or n > _AMOUNT_MAX:
        return ""
    if 1e5 <= n < 1e7:
        return f"₹{fmt_num(n / 1e5, 2)} lakh"
    return fmt_inr(n)


def _in_sentence(body: str) -> str:
    return f"the {body}" if body.startswith(("Ministry of ", "Department of ")) else body


def _list(names: list[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    return f"{', '.join(names[:-1])} and {names[-1]}"


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


def render(finding: dict[str, Any]) -> tuple[str, str]:
    if not isinstance(finding, dict):
        return "", ""
    m = metrics(finding)
    category = _text(m.get("signal_category")).lower()
    rule = _text(finding.get("rule_triggered")).upper()
    phrases = _phrases(m)
    if not phrases and not category and not rule.startswith("OR1"):
        return "", ""
    overlap = category == "dual_agency_or_scheme_overlap" or rule == "OR1-DUAL-AGENCY-OVERLAP"

    if phrases:
        said = f'refers to "{phrases[0]}"'
    elif overlap:
        said = "has wording on overlapping work or funding"
    else:
        said = "has wording on duplicate billing or payment"

    report = _report(m)
    sector = _sector(finding)
    page = _page(m)
    amount = _amount(finding)
    if page:
        tail = f" on page {page}" + (f", cites {amount}" if amount else "")
    else:
        tail = f" and cites {amount}" if amount else ""
    by_report = f"{_capital(report)} {said}{tail}"
    by_sector = f"CAG report on {sector} {said}{tail}" if sector else ""
    headline = by_sector if by_sector and len(by_sector) <= _HEADLINE_TARGET else by_report

    where = f"on page {page} of {report}" if page else f"in {report}"
    para = _text(m.get("para_number"))
    if _PARA.match(para):
        where += f", in paragraph {para}"
    section = _section(finding)
    bodies = _bodies(m)[:2]
    extra = phrases[1] if len(phrases) > 1 else ""

    def brief_with(use_extra: bool, use_bodies: bool, use_section: bool) -> str:
        notes: list[str] = []
        if use_bodies and bodies:
            notes.append(f"mentions {_list([_in_sentence(b) for b in bodies])}")
        if use_extra and extra:
            notes.append(f'also uses "{extra}"')
        if amount:
            notes.append(f"cites {amount}")
        clause = f", which {_list(notes)}," if notes else ""
        place = where + (f' under the heading "{section}"' if use_section and section else "")
        return f"The passage{clause} is {place}. {_CAVEAT_OVERLAP if overlap else _CAVEAT}"

    # The caveat always stays. Detail is dropped, least useful first, until the brief fits.
    options = [(True, True, True), (False, True, True), (False, False, True), (False, True, False), (False, False, False)]
    brief = ""
    for use_extra, use_bodies, use_section in options:
        brief = brief_with(use_extra, use_bodies, use_section)
        if len(brief) <= _BRIEF_ROOM:
            break
    return headline, brief
