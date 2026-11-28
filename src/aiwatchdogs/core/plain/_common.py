"""Shared helpers for plain-language finding headlines and briefs."""

from __future__ import annotations

import math
import re
from datetime import date, datetime
from typing import Any

HEADLINE_MAX = 110
BRIEF_MAX = 260

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_WS = re.compile(r"\s+")


def clean(value: Any) -> str:
    """Collapse whitespace and drop stray separators left by machine-built titles."""
    if value is None:
        return ""
    text = _WS.sub(" ", str(value)).strip()
    text = text.replace("||", ", ")
    return text.strip(" ,;:-—")


def clip(text: str, limit: int) -> str:
    """Cut at a word boundary and end with an ellipsis when the text is longer than limit."""
    text = clean(text)
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    space = cut.rfind(" ")
    if space >= limit * 0.6:
        cut = cut[:space]
    return cut.rstrip(" ,;:-—(") + "…"


def metrics(finding: dict[str, Any]) -> dict[str, Any]:
    value = finding.get("anomaly_metrics")
    return value if isinstance(value, dict) else {}


def num(value: Any) -> float | None:
    """Return a finite float, or None for missing, boolean, or non-numeric values."""
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def fmt_num(value: Any, digits: int = 1) -> str:
    """Format with Indian digit grouping and no trailing zeros: 223142 -> 2,23,142."""
    n = num(value)
    if n is None:
        return ""
    rounded = round(n, digits)
    sign = "-" if rounded < 0 else ""
    whole, _, frac = f"{abs(rounded):.{digits}f}".partition(".")
    frac = frac.rstrip("0")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join([*groups, tail])
    return f"{sign}{whole}.{frac}" if frac else f"{sign}{whole}"


def fmt_inr(rupees: Any) -> str:
    """Rupees in the unit a reader expects: ₹2.61 crore, ₹4.5 lakh, ₹12,500."""
    n = num(rupees)
    if n is None or n <= 0:
        return ""
    if n >= 1e7:
        crore = n / 1e7
        return f"₹{fmt_num(crore, 2 if crore < 100 else 0)} crore"
    if n >= 1e5:
        return f"₹{fmt_num(n / 1e5, 1)} lakh"
    return f"₹{fmt_num(n, 0)}"


def fmt_crore(crore: Any) -> str:
    """A figure already expressed in crore: ₹15.7 crore, ₹1,114 crore."""
    n = num(crore)
    if n is None:
        return ""
    return f"₹{fmt_num(n, 2 if abs(n) < 100 else 0)} crore"


def fmt_pct(value: Any) -> str:
    n = num(value)
    if n is None:
        return ""
    return f"{fmt_num(abs(n), 1 if abs(n) < 100 else 0)}%"


def parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = clean(value)
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


def fmt_date(value: Any) -> str:
    """16 Jul 2024. Empty string when the value is not a recognisable date."""
    d = parse_date(value)
    if d is None:
        return ""
    return f"{d.day} {_MONTHS[d.month - 1]} {d.year}"


def fmt_days(value: Any) -> str:
    """Day count as words: 1 day, 120 days, 1,095 days."""
    n = num(value)
    if n is None or n < 0:
        return ""
    days = round(n)
    if days == 1:
        return "1 day"
    return f"{fmt_num(days, 0)} days"


def place(finding: dict[str, Any]) -> str:
    """Jurisdiction as a reader would say it: 'State:Assam' -> 'Assam', Union -> 'Union government'."""
    raw = clean(metrics(finding).get("state_or_ut") or finding.get("jurisdiction"))
    if ":" in raw:
        raw = clean(raw.split(":", 1)[1])
    if raw.lower().startswith(("union", "central", "india", "national")):
        return "Union government"
    return raw


def fiscal_year(finding: dict[str, Any]) -> str:
    """'FY2024-25' -> '2024-25'."""
    raw = clean(finding.get("fiscal_year"))
    return raw[2:] if raw.upper().startswith("FY") else raw


def first_sentence(text: Any) -> str:
    text = clean(text)
    match = re.search(r"(?<=[a-z0-9\)])\. (?=[A-Z])", text)
    return text[: match.start() + 1] if match else text


_TITLE_PREFIXES = re.compile(
    r"^(Public Examination Conduct Observation|Procurement Pattern Lead|Sovereign Scheme Commitment Divergence|"
    r"Custody Release Compliance Observation|Public Record Anomaly Lead)\s*(\([^)]*\))?\s*[:—-]\s*",
    re.IGNORECASE,
)


def generic(finding: dict[str, Any]) -> tuple[str, str]:
    """Fallback for check types without a dedicated writer: trim boilerplate from the stored text."""
    title = _TITLE_PREFIXES.sub("", clean(finding.get("title"))) or clean(finding.get("title"))
    summary = first_sentence(finding.get("summary"))
    return title or "Public record observation", summary
