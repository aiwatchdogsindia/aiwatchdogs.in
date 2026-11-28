"""P5: States and UTs with many single-teacher schools or crowded single-teacher schools (UDISE+).

Three engine rules can fire, listed in ``triggered_rules``:

* ``single_teacher_share_ge_10pct``: single-teacher schools are 10% or more of all schools.
* ``students_per_single_teacher_gt_30``: those schools enrol more than 30 students each on average.
* ``gov_primary_avg_teachers_lt_2``: government primary schools average fewer than 2 teachers each.

``single_teacher_schools`` and ``single_teacher_enrolments`` count schools of every management, not
only government schools, so the text never calls them government schools. Only the ``gov_primary_*``
fields (Classes I to V) are government-only. The engine stores 0 for a ratio it could not work out,
so a zero is treated as unknown and left out.
"""

from __future__ import annotations

import re
from typing import Any

from ._common import clean, fmt_num, fmt_pct, metrics, num

_HEADLINE_TARGET = 95
_STATE_MAX = 45
_YEAR = re.compile(r"\d{4}-\d{2}")
_NAME = re.compile(r"^[A-Za-z][A-Za-z .&'-]*$")
_NOT_TEXT = {"none", "nan", "null", "inf", "true", "false", "n/a", "na", "unknown", "unspecified"}
_SHARE = "single_teacher_share_ge_10pct"
_CROWDED = "students_per_single_teacher_gt_30"
_GOV = "gov_primary_avg_teachers_lt_2"
_RULE_SIGNALS: dict[str, tuple[str, ...]] = {
    "P5-CONCENTRATION-OVERCROWDING": (_SHARE,),
    "P5-HIGH-CONCENTRATION": (_SHARE,),
    "P5-SEVERE-OVERCROWDING": (_CROWDED,),
    "P5-GOV-PRIMARY-UNDERSTAFFED": (_GOV,),
}


def _text(value: Any) -> str:
    """A cleaned string, or '' for anything that is not real text (None, numbers, lists, 'nan')."""
    if not isinstance(value, str):
        return ""
    text = clean(value)
    return "" if text.lower() in _NOT_TEXT else text


def _state(finding: dict[str, Any]) -> str:
    entity = re.split(r"\s[—-]\s", _text(finding.get("entity_name")), maxsplit=1)[0]
    for raw in (metrics(finding).get("state_name"), finding.get("jurisdiction"), entity):
        name = _text(raw)
        if ":" in name:
            name = clean(name.split(":", 1)[1])
        if name.isupper() and len(name) > 3:
            name = name.title().replace(" And ", " and ")
        if name and len(name) <= _STATE_MAX and _NAME.match(name):
            return name
    return ""


def _year(finding: dict[str, Any]) -> str:
    for raw in (metrics(finding).get("academic_year"), finding.get("fiscal_year")):
        found = _YEAR.search(_text(raw))
        if found:
            return found.group(0)
    return ""


def _count(value: Any, ceiling: float = 1e10) -> float | None:
    n = num(value)
    return n if n is not None and 0 < n <= ceiling else None


def _signals(finding: dict[str, Any]) -> set[str]:
    raw = metrics(finding).get("triggered_rules")
    signals = {clean(item) for item in raw if isinstance(item, str)} if isinstance(raw, list) else set()
    signals &= {_SHARE, _CROWDED, _GOV}
    if not signals:
        signals = set(_RULE_SIGNALS.get(_text(finding.get("rule_triggered")).upper(), ()))
    return signals


def _possessive(name: str) -> str:
    return f"{name}'" if name.endswith("s") else f"{name}'s"


def _fit(*candidates: str) -> str:
    """First headline within the target length, else the shortest."""
    filled = [c for c in candidates if c]
    for text in filled:
        if len(text) <= _HEADLINE_TARGET:
            return text
    return min(filled, key=len, default="")


def render(finding: dict[str, Any]) -> tuple[str, str]:
    if not isinstance(finding, dict):
        return "", ""
    m = metrics(finding)
    state = _state(finding)
    if not state:
        return "", ""
    year = _year(finding)
    in_year = f" in {year}" if year else ""
    signals = _signals(finding)

    schools = _count(m.get("single_teacher_schools"))
    total = _count(m.get("total_schools"))
    if schools and total and schools > total:
        total = None
    share = _count(m.get("single_teacher_share_pct"), 100.0)
    enrolled = _count(m.get("single_teacher_enrolments"))
    per_school = _count(m.get("students_per_single_teacher"), 100000.0)
    gov_schools = _count(m.get("gov_primary_1_5_schools"))
    gov_teachers = _count(m.get("gov_primary_1_5_teachers"))
    gov_avg = _count(m.get("gov_primary_teachers_per_school"), 1000.0)
    # Counts that contradict the stored ratio are left out, so no sentence prints figures that disagree.
    if schools and total and share and abs(100.0 * schools / total - share) > 0.06:
        total = None
    if schools and enrolled and per_school and abs(enrolled / schools - per_school) > 0.06:
        enrolled = None
    if gov_schools and gov_teachers and gov_avg and abs(gov_teachers / gov_schools - gov_avg) > 0.006:
        gov_schools = gov_teachers = None

    n_schools = fmt_num(schools, 0) if schools else ""
    share_text = fmt_pct(share) if share else ""
    per_text = fmt_num(per_school, 1) if per_school else ""
    gov_avg_text = fmt_num(gov_avg, 2) if gov_avg else ""
    own = _possessive(state)

    gov_headline = ""
    if gov_avg_text:
        gov_headline = _fit(
            f"{state} government primary schools averaged {gov_avg_text} teachers each{in_year}",
            f"{state} government primary schools averaged {gov_avg_text} teachers each",
        )
    headline = ""
    if _SHARE in signals and _CROWDED in signals and n_schools and per_text:
        headline = _fit(
            f"{state} had {n_schools} single-teacher schools{in_year}, averaging {per_text} students each",
            f"{state} had {n_schools} single-teacher schools averaging {per_text} students each",
            f"{state} had {n_schools} single-teacher schools, {per_text} students each",
        )
    elif _SHARE in signals and n_schools and share_text:
        headline = _fit(
            f"{state} had {n_schools} single-teacher schools{in_year}, {share_text} of all its schools",
            f"{state} had {n_schools} single-teacher schools, {share_text} of all its schools",
        )
    elif _CROWDED in signals and n_schools and per_text:
        headline = _fit(
            f"{own} {n_schools} single-teacher schools averaged {per_text} students each{in_year}",
            f"{own} {n_schools} single-teacher schools averaged {per_text} students each",
        )
    elif _GOV in signals and gov_avg_text:
        headline = gov_headline
    elif n_schools:
        headline = _fit(
            f"{state} had {n_schools} single-teacher schools{in_year}",
            f"{state} had {n_schools} single-teacher schools",
        )
    elif share_text:
        headline = _fit(
            f"{share_text} of {own} schools had a single teacher{in_year}",
            f"{share_text} of {own} schools had a single teacher",
        )
    elif gov_avg_text:
        headline = gov_headline
    if not headline:
        return "", ""

    source = f"Official UDISE+ {year} school data" if year else "Official UDISE+ school data"
    sentences: list[str] = []
    gov_first = bool(gov_headline) and headline == gov_headline
    gov_sentence = ""
    if gov_avg_text and (_GOV in signals or gov_first):
        if gov_first and gov_schools and gov_teachers:
            gov_sentence = (
                f"{source} show {fmt_num(gov_teachers, 0)} teachers across {fmt_num(gov_schools, 0)} "
                f"government primary schools in {state}."
            )
        elif gov_first:
            gov_sentence = f"{source} show government primary schools in {state} averaged {gov_avg_text} teachers each."
        else:
            gov_sentence = f"Government primary schools averaged {gov_avg_text} teachers each."

    if gov_first:
        sentences.append(gov_sentence)
        if n_schools and total:
            sentences.append(f"Across all schools, {n_schools} of {fmt_num(total, 0)} had one teacher.")
        elif n_schools:
            sentences.append(f"Across all schools, {n_schools} had one teacher.")
    else:
        if n_schools and total:
            bracket = f" ({share_text})" if share_text else ""
            sentences.append(f"{source} show {n_schools} of {own} {fmt_num(total, 0)} schools{bracket} had one teacher.")
        elif n_schools and share_text and _SHARE not in signals:
            sentences.append(f"{source} show {state} had {n_schools} single-teacher schools, {share_text} of the total.")
        elif n_schools:
            sentences.append(f"{source} show {state} had {n_schools} single-teacher schools.")
        else:
            sentences.append(f"{source} show {share_text} of {own} schools had one teacher.")
        if enrolled and per_text:
            sentences.append(f"They enrolled {fmt_num(enrolled, 0)} students, about {per_text} per school.")
        elif enrolled:
            sentences.append(f"They enrolled {fmt_num(enrolled, 0)} students.")
        elif per_text:
            sentences.append(f"They averaged {per_text} students per school.")
        if gov_sentence:
            sentences.append(gov_sentence)
    return headline, " ".join(s for s in sentences if s)
