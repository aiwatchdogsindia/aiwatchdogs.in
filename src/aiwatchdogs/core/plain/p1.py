"""P1: police budget lines whose estimate, revised estimate and actuals differ sharply.

The engine compares two printed figures of one budget head, ``a`` against ``b``, and stores
``pct_difference = (a - b) / b * 100`` (rounded to two places) with ``compare = "<a>_vs_<b>"``.
The figure names are ``be`` (budget estimate), ``re`` (revised estimate), ``actual`` (accounts),
``be_next`` (budget estimate of the following year) and ``be_of_actual_year`` (budget estimate of
the accounts year, read from an earlier budget book). ``fiscal_year`` is the year of figure ``a``.
Figures are printed in ``unit`` (crore, lakh or thousand) and ``monetary_exposure_inr`` is the gap
between the two figures in rupees. A printed nil is stored as 0, so a zero is never shown as money.
"""

from __future__ import annotations

import re
from typing import Any

from ._common import clean, fmt_crore, fmt_inr, fmt_pct, metrics, num

_HEADLINE_TARGET = 95
_HEADLINE_MAX = 100
_NAME_MAX = 80
_LONG_NAME = 50
_PLACE_MAX = 40
_PCT_MAX = 100000.0
_UNIT_RUPEES: dict[str, float] = {"crore": 1e7, "lakh": 1e5, "thousand": 1e3, "rupee": 1.0}
_NOT_TEXT = {"none", "nan", "null", "inf", "true", "false", "n/a", "na", "unknown", "unspecified"}
_FIGURES = {"be", "be_next", "re", "actual", "be_of_actual_year"}
_YEAR = re.compile(r"^\d{4}-\d{2}$")
_SUMMARY_YEARS = re.compile(
    r"for (?:BE|RE|Actuals) FY(\d{4}-\d{2}) against [\d,.]+ \w+ for (?:BE|RE|Actuals) FY(\d{4}-\d{2})"
)
_SUMMARY_LABEL = re.compile(r" on (.+?), a difference of ")
_TITLE_LABEL = re.compile(r"\(([^()]*(?:\([^()]*\)[^()]*)*)\)\s*$")
_UNION_NOTE = re.compile(r"\s*\((?:Union|Central)\b[^()]*\)\s*$", re.IGNORECASE)
_PAREN_TAIL = re.compile(r"\s*\([^()]*\)\s*$")
_PAREN_AFTER_COMMA = re.compile(r"\(([^(),]+),[^()]*\)")
_PLACE = re.compile(r"^[A-Z][A-Za-z .&'-]*$")
_TAKES_ARTICLE = re.compile(r"(?:Force|Guard|Bureau|Group|Border Police|[Ss]cheme)$")
_SMALL_WORDS = {"And": "and", "Of": "of", "For": "for", "The": "the", "In": "in", "On": "on"}
_CAPF = "Central Armed Police Forces"


def _text(value: Any) -> str:
    """A cleaned string, or '' for anything that is not real text (None, numbers, lists, 'nan')."""
    if not isinstance(value, str):
        return ""
    text = clean(value)
    return "" if text.lower() in _NOT_TEXT else text


def _entity(finding: dict[str, Any]) -> str:
    name = _text(finding.get("entity_name"))
    while _UNION_NOTE.search(name):
        name = clean(_UNION_NOTE.sub("", name))
    if name.isupper() and len(name) > 6:
        words = name.title().split()
        name = " ".join(w if i == 0 else _SMALL_WORDS.get(w, w) for i, w in enumerate(words))
    if len(name) > _NAME_MAX or not re.search(r"[A-Za-z]{2}", name):
        return ""
    return name


def _headline_name(entity: str, line_key: str) -> str:
    """The name used in headlines: long names are shortened the same way every time. Briefs keep the full name."""
    if len(entity) <= _LONG_NAME:
        return entity
    short = _PAREN_AFTER_COMMA.sub(r"(\1)", entity)
    # The abbreviation is used only where the record's own line key carries it.
    if "capf" in line_key:
        short = short.replace(_CAPF, "CAPFs")
    return short


def _kind(finding: dict[str, Any]) -> str:
    """'capital', 'revenue' or '' (revenue and capital together, or not known)."""
    key = _text(metrics(finding).get("line_key")).lower()
    found = _SUMMARY_LABEL.search(_text(finding.get("summary")))
    if found:
        label = found.group(1).lower()
    else:
        tail = _TITLE_LABEL.search(_text(finding.get("title")))
        label = tail.group(1).lower() if tail else ""
    if "revenue + capital" in label or key.endswith("-total"):
        return ""
    if "4055" in key or "4055" in label or key.endswith("-capital") or label.endswith(", capital"):
        return "capital"
    if "2055" in key or "2055" in label or key.endswith("-revenue") or label.endswith(", revenue"):
        return "revenue"
    return ""


def _papers(finding: dict[str, Any]) -> str:
    raw = _text(finding.get("jurisdiction"))
    if ":" in raw:
        raw = clean(raw.split(":", 1)[1])
    if raw.lower().startswith(("union", "central")):
        return "Union Budget papers"
    where = clean(_PAREN_TAIL.sub("", raw))
    if where.isupper() and len(where) > 3:
        where = where.title().replace(" And ", " and ")
    # A bare "India" is the engine's default when the task names no jurisdiction, so it says nothing.
    if not where or len(where) > _PLACE_MAX or not _PLACE.match(where) or where.lower() == "india":
        return "Budget papers"
    return f"{where} budget papers"


def _year(value: Any) -> str:
    text = _text(value)
    if text.upper().startswith("FY"):
        text = text[2:]
    return text if _YEAR.match(text) else ""


def _amount(value: Any) -> float | None:
    n = num(value)
    return n if n is not None and 0 <= n < 1e15 else None


def _named(entity: str) -> str:
    """The name as it sits inside a sentence: 'the Border Security Force', but 'Assam Rifles'."""
    return f"the {entity}" if _TAKES_ARTICLE.search(entity) else entity


def _adds_up(a_val: float, b_val: float, diff_inr: float) -> bool:
    """False when rounding would print two figures whose gap visibly disagrees with the printed difference."""

    def shown(value: float) -> float:
        return round(value, 2 if abs(value) < 100 else 0)

    return abs(abs(shown(a_val) - shown(b_val)) - shown(diff_inr / 1e7)) <= 0.5


def _join(*parts: str) -> str:
    return " ".join(p for p in parts if p)


def _headline(name: str, sentence: str, compact: str, tight: str = "") -> str:
    """Pick the fullest headline that fits. Every form names what the figure is compared with."""
    forms: list[tuple[str, int]] = []
    # "Plan IV for X budget for 2024-25" stumbles on the doubled "for", so such names take the colon form.
    if " for " not in name:
        forms.append((f"{name} {sentence}", _HEADLINE_TARGET))
    forms.append((f"{name}: {compact}", _HEADLINE_TARGET))
    if tight:
        forms.append((f"{name}: {tight}", _HEADLINE_MAX))
    for text, limit in forms:
        if len(text) <= limit:
            return text
    return min((text for text, _ in forms), key=len)


def _equal(finding: dict[str, Any], entity: str, head_name: str, kind: str, year: str) -> tuple[str, str]:
    m = metrics(finding)
    budget = _join(kind, "budget")
    for_year = f"for {year}" if year else ""
    headline = _headline(
        head_name,
        _join(budget, for_year, "unchanged at the revised estimate stage"),
        f"{_join(year, budget)} unchanged at revised stage",
    )
    brief = (
        f"{_papers(finding)} print the same figure for the {_join(year, budget)} of {_named(entity)} "
        "as both the budget estimate and the revised estimate."
    )
    be = _amount(m.get("be"))
    if _text(m.get("unit")).lower() == "crore" and be:
        brief += f" Both stand at {fmt_crore(be)}."
    return headline, brief


def render(finding: dict[str, Any]) -> tuple[str, str]:
    if not isinstance(finding, dict):
        return "", ""
    m = metrics(finding)
    entity = _entity(finding)
    if not entity:
        return "", ""
    head_name = _headline_name(entity, _text(m.get("line_key")).lower())
    kind = _kind(finding)
    year = _year(finding.get("fiscal_year"))
    rule = _text(finding.get("rule_triggered")).upper()
    compare = _text(m.get("compare")).lower()

    be_only, re_only = _amount(m.get("be")), _amount(m.get("re"))
    if rule == "P1-RE-EQUALS-BE" or (not compare and be_only and be_only == re_only):
        if be_only is not None and re_only is not None and be_only != re_only:
            return "", ""
        return _equal(finding, entity, head_name, kind, year)

    a_name, sep, b_name = compare.partition("_vs_")
    if not sep or a_name not in _FIGURES or b_name not in _FIGURES or a_name == b_name:
        return "", ""

    # Years as the stored summary prints them. They fill in the comparison year, which is not a field.
    found = _SUMMARY_YEARS.search(_text(finding.get("summary")))
    summary_a, summary_b = (found.group(1), found.group(2)) if found else ("", "")
    if year and summary_a and summary_a != year:
        summary_a = summary_b = ""
    year = year or summary_a
    other = year if b_name == "be_of_actual_year" else summary_b
    if (a_name, b_name) == ("re", "be") and not other:
        other = year

    a_val, b_val = _amount(m.get(a_name)), _amount(m.get(b_name))
    stored = num(m.get("pct_difference"))
    exact = (a_val - b_val) / b_val * 100.0 if a_val is not None and b_val else None
    if exact is not None and stored is not None and abs(exact - stored) > 0.011:
        # The two figures and the stored percentage contradict each other, so neither is repeated.
        return "", ""
    # The stored value is already rounded to two places. Rounding it again can land 0.1 away from
    # the stored title, which was built from the exact figure, so the exact one is used when known.
    pct = exact if exact is not None else stored
    if pct is None or pct == 0 or abs(pct) > _PCT_MAX:
        return "", ""
    up = pct > 0
    pct_text = fmt_pct(pct)
    above = "above" if up else "below"

    unit = _text(m.get("unit")).lower()
    gap_inr = num(finding.get("monetary_exposure_inr"))
    if exact is not None and a_val is not None and b_val is not None and unit in _UNIT_RUPEES:
        expected = abs(a_val - b_val) * _UNIT_RUPEES[unit]
        if gap_inr is None or abs(gap_inr - expected) > 0.01 * expected:
            gap_inr = expected
    # Lakh and thousand figures are left out: only the rupee difference is stored in a unit a reader uses.
    figures = exact is not None and unit == "crore" and bool(a_val) and bool(b_val)
    if figures and gap_inr and not _adds_up(a_val or 0.0, b_val or 0.0, gap_inr):
        gap_inr = None
    diff = fmt_inr(gap_inr)
    a_text = fmt_crore(a_val) if figures else ""
    b_text = fmt_crore(b_val) if figures else ""

    budget = _join(kind, "budget")
    spending = _join(kind, "spending")
    for_year = f"for {year}" if year else ""
    in_year = f"in {year}" if year else ""
    year_budget = _join(year, budget)
    papers = _papers(finding)
    named = _named(entity)
    gap = f", a difference of {diff}" if diff else ""

    if (a_name, b_name) == ("re", "be") and other == year:
        moved = "up" if up else "down"
        tail = f", a {'rise' if up else 'fall'} of {diff}" if diff else ""
        headline = _headline(
            head_name,
            _join(budget, for_year, f"revised {moved} {pct_text}"),
            f"{year_budget} revised {moved} {pct_text}",
        )
        if figures:
            brief = (
                f"{papers} show the {year_budget} for {named} was first set at {b_text} "
                f"and later revised to {a_text}{tail}."
            )
        else:
            brief = (
                f"{papers} show the {year_budget} for {named} was revised {moved} by {pct_text} "
                f"from the budget estimate{tail}."
            )
        return headline, brief

    if a_name == "actual" and b_name == "be_of_actual_year":
        headline = _headline(
            head_name,
            _join(spending, in_year, f"was {pct_text} {above} budget"),
            f"{_join(year, spending)} {pct_text} {above} budget",
        )
        if figures:
            tail = f", which is {diff} {'more' if up else 'less'}" if diff else ""
            brief = (
                f"{papers} show a {budget} of {b_text} for {named}{' ' + in_year if in_year else ''}. "
                f"Actual spending was {a_text}{tail}."
            )
        else:
            brief = (
                f"{papers} show actual {spending} for {named}{' ' + in_year if in_year else ''} "
                f"was {pct_text} {above} the budget estimate{gap}."
            )
        return headline, brief

    if a_name not in {"be", "be_next"}:
        return "", ""
    if other == year:
        other = ""
    if b_name == "actual":
        base_head = _join("actual", other, "spending")
        base_tight = _join(other, "spending") if other else "earlier spending"
        base_brief = f"actual spending in {other}" if other else "actual spending in the earlier year"
        base_sentence = f"Actual spending in {other} was" if other else "Actual spending in the earlier year was"
    elif b_name == "re":
        base_head = _join("revised", other, "estimate")
        base_tight = "revised estimate"
        base_brief = f"the revised estimate for {other}" if other else "the revised estimate of the earlier year"
        base_sentence = f"The revised estimate for {other} was" if other else "The earlier revised estimate was"
    elif b_name == "be":
        base_head = f"the {other} budget" if other else "the earlier budget"
        base_tight = f"{other} budget" if other else "earlier budget"
        base_brief = f"the budget estimate for {other}" if other else "the earlier budget estimate"
        base_sentence = f"The budget estimate for {other} was" if other else "The earlier budget estimate was"
    else:
        return "", ""

    verb = "set" if a_name == "be_next" else "is"
    headline = _headline(
        head_name,
        _join(budget, for_year, f"{verb} {pct_text} {above} {base_head}"),
        f"{year_budget} {pct_text} {above} {base_head}",
        f"{year_budget} {pct_text} {above} {base_tight}",
    )
    if figures:
        brief = f"{papers} set the {year_budget} for {named} at {a_text}. {base_sentence} {b_text}{gap}."
    else:
        brief = f"{papers} set the {year_budget} for {named} {pct_text} {above} {base_brief}{gap}."
    return headline, brief
