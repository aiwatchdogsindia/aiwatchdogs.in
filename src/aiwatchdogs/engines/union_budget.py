"""UB1: Union Budget lines, budget estimate vs revised estimate vs actual expenditure.

Every Union Budget prints, for each Demand for Grants, a "Notes on Demands for Grants"
statement (the Statement of Budget Estimates). Section A of that statement is one table:
a numbered line for every scheme, body or establishment head, with four periods of figures
in crore of rupees::

    Actual (two years back) | Budget (last year) | Revised (last year) | Budget (this year)

and three figures per period (Plan / Non-Plan / Total up to Budget 2016-17, Revenue /
Capital / Total from Budget 2017-18). So the money Parliament was asked to vote for a
financial year (BE), the mid-year revision (RE) and what was finally spent (Actual) are
printed by the Ministry of Finance itself, in two consecutive budgets:

* the budget presented one year later prints BE and RE for the year, on one row;
* the budget presented two years later prints the Actual for the year.

``UB1`` reads those printed rows and reports how much of a line's budget estimate was not
spent. It reads text only: a row is the tokens of one printed line, the last twelve of which
are figures (``...`` is the printed nil). Every node extracts the same text because the PDF
reader is pinned, so a peer verifier reads the very same line.

Task context::

    {
      "financial_year": "2019-20",
      "estimates_document": {            # the pinned primary file
        "budget": "2020-21", "demand_no": 42,
        "url": "https://www.indiabudget.gov.in/budget2020-21/doc/eb/sbe42.pdf", "sha256": "..."},
      "actuals_document": {              # the pinned secondary file, absent until it is published
        "budget": "2021-22", "demand_no": 44, "url": "...", "sha256": "..."},
      "lines": [
        {"key": "l12-03", "name": "National AIDS and STD Control Programme",
         "estimates": {"number": "12.03", "page": 2},
         "actuals": {"number": "13.03", "page": 2}}
      ],
      "thresholds": {"unspent_pct": 10.0, "unspent_crore": 1.0}
    }

A line is unevaluable, never guessed, when its row is not printed where the task says, when
its printed name differs, or when the two budgets print different figures for the year they
both carry (the budget estimate of the year in between).
"""

from __future__ import annotations

from collections import Counter
import math
import re
from dataclasses import dataclass, field
from typing import Any

from aiwatchdogs.core.models import AuditLeadCandidate
from aiwatchdogs.core.safety import validate_safe_http_url

CRORE = 1e7
VALUES_PER_ROW = 12
NIL_TOKEN = "..."
SUBCOLUMNS_OLD = ("Plan", "Non-Plan", "Total")
SUBCOLUMNS_NEW = ("Revenue", "Capital", "Total")
# Column of the "Total" figure in each of the four printed periods.
COL_ACTUAL, COL_BUDGET_PREV, COL_REVISED_PREV, COL_BUDGET = 2, 5, 8, 11
MAX_LINES_PER_TASK = 40
MAX_ROWS_PER_LINE = 12  # printed rows one line may add together (a scheme split by source of funds)
MAX_NAME_LENGTH = 240
# The hub stores a finding's entity name in at most 200 characters, and a peer verifier must
# reproduce it exactly, so the engine never produces a longer one.
MAX_ENTITY_LENGTH = 200

# Book entries, not money to be spent: transfers into and out of reserve funds, appropriations to
# funds, ways and means advances, suspense heads, and the servicing of a body's loans.
BOOK_ENTRY_RE = re.compile(
    r"^(?:tran\w*fers?\s+(?:to|of)\b|appropriation\s+to\b|amount\s+met\s+from\b|met\s+(?:from|through)\b|less\b|deduct\b"
    r"|ways\s+and\s+means\b|inter\s+railway\s+financial\s+adjus|inter\s+account\s+transfer"
    r"|from\s+(?:railways'?\s+resources|general\s+revenues)\b|.*\bfund\s+transfer$|.*\bsuspense$"
    r"|repayment\s+of\b|payment\s+of\s+interest\b|interest\s+payments?\b|interest\s+on\b|interest\s+under\b.*\b(?:loans?|ebr)\b|recoveries$"
    r"|.*\bdividend\s+reliefs?\b.*)",
    re.IGNORECASE,
)

_VALUE_RE = re.compile(r"^-?\d[\d,]*\.\d+$")
_TOP_NUMBER_RE = re.compile(r"^(\d{1,3})\.$")
_SUB_NUMBER_RE = re.compile(r"^\d{1,3}(?:\.\d{2}){1,2}$")
_LINE_NUMBER_RE = re.compile(r"^\d{1,3}(?:\.\d{2}){0,2}$")
_MAJOR_HEAD_RE = re.compile(r"^\d{4,5}$")
_BUDGET_TITLE_RE = re.compile(r"Notes on Demands for Grants,\s*(\d{4})\s*-\s*(\d{4})")
# 'DEMAND NO. 42', and for charged expenditure 'No. 39 (APPROPRIATION)'.
_DEMAND_RE = re.compile(r"^\s*(?:DEMAND\s+NO\.?|No\.)\s*(\d{1,3})(\s*\(APPROPRIATION\))?\s*$", re.IGNORECASE)
_PERIOD_RE = re.compile(
    r"Actual\s+(\d{4})-(\d{4})\W*\s+Budget\s+(\d{4})-(\d{4})\W*\s+Revised\s+(\d{4})-(\d{4})\W*\s+Budget\s+(\d{4})-(\d{4})"
)
_GRAND_TOTAL_RE = re.compile(r"^\s*Grand\s+Total\b", re.IGNORECASE)
_TOTAL_LABEL_RE = re.compile(r"^(?:Total|Net)\b", re.IGNORECASE)
_NAME_NORMALISE_RE = re.compile(r"[^a-z0-9]+")
_YEAR_RE = re.compile(r"^(\d{4})-(\d{2})$")
_PART_LABELS = frozenset({"revenue", "capital", "loans", "loans and advances"})


_FURNITURE_RES = (
    re.compile(r"Notes on Demands for Grants,\s*\d{4}\s*-\s*\d{4}"),
    re.compile(r"^No\.\s*\d{1,3}\s*(?:\(APPROPRIATION\))?\s*/"),
    re.compile(r"^\(In\s+.{0,3}\s*crores?(?:\s+of\s+Rupees)?\s*\)$", re.IGNORECASE),
    re.compile(r"^(?:Major|Head|Major\s+Head)$"),
    re.compile(r"^Actual\s+\d{4}-\d{4}\W*\s+Budget\s+\d{4}-\d{4}"),
    re.compile(r"^(?:Plan\s+Non-Plan\s+Total|Revenue\s+Capital\s+Total)\b"),
    re.compile(r"^\d{1,4}$"),  # a bare page number
)


def _is_page_furniture(text: str) -> bool:
    clean = " ".join(text.split())
    return any(rx.search(clean) for rx in _FURNITURE_RES)


def _is_value(token: str) -> bool:
    return token == NIL_TOKEN or bool(_VALUE_RE.match(token))


def _to_number(token: str) -> float | None:
    """A printed figure in crore of rupees; the printed nil (``...``) is ``None``."""
    if token == NIL_TOKEN:
        return None
    try:
        value = float(token.replace(",", ""))
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def normalise_name(text: str) -> str:
    """A printed line name reduced to lowercase letters and digits, for comparing two prints of it."""
    return _NAME_NORMALISE_RE.sub("", (text or "").lower())


def financial_year(start: str | int) -> str:
    """``2019`` -> ``2019-20``."""
    year = int(start)
    return f"{year}-{(year + 1) % 100:02d}"


def shift_year(label: str, by: int) -> str:
    """``shift_year('2019-20', 1)`` -> ``2020-21``."""
    match = _YEAR_RE.match(label or "")
    if not match:
        raise ValueError(f"Not a financial year label: {label!r}")
    return financial_year(int(match.group(1)) + by)


@dataclass(frozen=True)
class SbeRow:
    """One printed row segment of a statement: a label, and twelve figures when it carries any."""

    page: int
    order: int  # position in the document, pages then lines then segments
    text: str  # the printed segment, single-spaced
    label: str  # the words before the figures (major head removed)
    number: str | None  # '12' or '12.03' when the segment opens with a line number
    major_head: str | None
    values: tuple[float | None, ...] | None

    @property
    def has_values(self) -> bool:
        return self.values is not None


@dataclass
class SbeDocument:
    """A parsed Notes on Demands for Grants statement (one Demand, one Union Budget)."""

    budget: str = ""  # '2020-21'
    demand_no: int | None = None
    appropriation: bool = False  # charged expenditure, printed as 'No. 39 (APPROPRIATION)'
    ministry: str = ""
    department: str = ""
    periods: tuple[str, str, str, str] | None = None  # actual, budget, revised, budget
    subcolumns: tuple[str, str, str] | None = None
    rows: list[SbeRow] = field(default_factory=list)
    table_start: int | None = None  # order of the first row of section A
    table_end: int | None = None  # order of the Grand Total row of section A
    pages: int = 0

    @property
    def demand_label(self) -> str:
        """'Demand No. 42', or 'Appropriation No. 39' for charged expenditure."""
        if self.demand_no is None:
            return "the Demand"
        return f"{'Appropriation' if self.appropriation else 'Demand'} No. {self.demand_no}"

    def table_rows(self) -> list[SbeRow]:
        """Rows of section A, without the heading lines repeated at the top of each page."""
        if self.table_end is None:
            return []
        start = self.table_start or 0
        return [
            row
            for row in self.rows
            if start <= row.order <= self.table_end and not (not row.has_values and _is_page_furniture(row.text))
        ]


def split_segments(line: str) -> list[tuple[list[str], list[str] | None]]:
    """Split one extracted text line into ``(label tokens, twelve value tokens or None)`` segments.

    The PDF reader sometimes returns two printed rows on one text line; each run of twelve
    figures closes a row. Figure-like words inside a name (a rate, a year) stay in the label.
    """
    segments: list[tuple[list[str], list[str] | None]] = []
    label: list[str] = []
    run: list[str] = []
    for token in line.split():
        if _is_value(token):
            run.append(token)
            continue
        if len(run) >= VALUES_PER_ROW:
            segments.append((label + run[:-VALUES_PER_ROW], run[-VALUES_PER_ROW:]))
            label = []
        else:
            label.extend(run)
        run = []
        label.append(token)
    if len(run) >= VALUES_PER_ROW:
        segments.append((label + run[:-VALUES_PER_ROW], run[-VALUES_PER_ROW:]))
    elif label or run:
        segments.append((label + run, None))
    return segments


def _row_from_segment(
    page: int, order: int, label_tokens: list[str], value_tokens: list[str] | None, by_major_head: bool = False
) -> SbeRow:
    tokens = list(label_tokens)
    number: str | None = None
    if tokens:
        top = _TOP_NUMBER_RE.match(tokens[0])
        if top:
            number = top.group(1)
            tokens = tokens[1:]
        elif _SUB_NUMBER_RE.match(tokens[0]) and (len(tokens) > 1 or value_tokens is not None):
            number = tokens[0]
            tokens = tokens[1:]
    major_head: str | None = None
    if by_major_head and value_tokens is not None and tokens and _MAJOR_HEAD_RE.match(tokens[-1]):
        major_head = tokens[-1]
        tokens = tokens[:-1]
    values = tuple(_to_number(tok) for tok in value_tokens) if value_tokens is not None else None
    text = " ".join(label_tokens + (value_tokens or []))
    return SbeRow(
        page=page,
        order=order,
        text=text,
        label=" ".join(tokens).strip(),
        number=number,
        major_head=major_head,
        values=values,
    )


def parse_statement(pages: dict[int, str]) -> SbeDocument:
    """Parse the extracted page texts of one Notes on Demands for Grants statement."""
    doc = SbeDocument(pages=len(pages))
    order = 0
    previous_text_line = ""
    await_department = False
    for page in sorted(pages):
        for raw_line in (pages[page] or "").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if not doc.budget:
                title = _BUDGET_TITLE_RE.search(line)
                if title:
                    doc.budget = financial_year(title.group(1))
            if doc.demand_no is None:
                demand = _DEMAND_RE.match(line)
                if demand:
                    doc.demand_no = int(demand.group(1))
                    doc.appropriation = bool(demand.group(2))
                    doc.ministry = " ".join(previous_text_line.split())
                    await_department = True
                    previous_text_line = line
                    continue
            if await_department:
                doc.department = " ".join(line.split())
                await_department = False
            if doc.periods is None:
                period = _PERIOD_RE.search(line)
                if period:
                    g = period.groups()
                    doc.periods = (
                        financial_year(g[0]),
                        financial_year(g[2]),
                        financial_year(g[4]),
                        financial_year(g[6]),
                    )
            if doc.subcolumns is None:
                head = line.split()[:3]
                if tuple(head) == SUBCOLUMNS_OLD:
                    doc.subcolumns = SUBCOLUMNS_OLD
                elif tuple(head) == SUBCOLUMNS_NEW:
                    doc.subcolumns = SUBCOLUMNS_NEW
            for label_tokens, value_tokens in split_segments(line):
                row = _row_from_segment(page, order, label_tokens, value_tokens, doc.subcolumns == SUBCOLUMNS_OLD)
                doc.rows.append(row)
                if doc.periods is not None and doc.table_end is None:
                    if doc.table_start is None and (row.number is not None or row.has_values):
                        doc.table_start = row.order
                    if row.has_values and _GRAND_TOTAL_RE.match(row.label):
                        doc.table_end = row.order
                order += 1
            previous_text_line = line
    return doc


@dataclass(frozen=True)
class LineReading:
    """The figures printed for one numbered line of section A."""

    number: str
    name: str
    page: int
    printed: tuple[str, ...]  # the printed row or rows the figures were read from
    values: tuple[float | None, ...]
    basis: str = "row"  # 'row' (one printed row), 'total' (printed Total of its major heads), 'sum' (major heads added)
    orders: tuple[int, ...] = ()  # positions of the rows used, in the document
    parents: tuple[str, ...] = ()  # names of the numbered headings above a sub-line ('4.' for '4.01')

    @property
    def full_name(self) -> str:
        """The line's name under its headings: 'Pradhan Mantri Swasthya Suraksha Yojana > Support from GBS'."""
        return " > ".join([*self.parents, self.name])

    def total(self, column: int) -> float | None:
        return self.values[column]


def _add_rows(rows: list[SbeRow]) -> tuple[float | None, ...]:
    """Add printed rows column by column; a column that is nil in every row stays nil."""
    out: list[float | None] = []
    for col in range(VALUES_PER_ROW):
        cells = [row.values[col] for row in rows if row.values is not None and row.values[col] is not None]
        out.append(round(math.fsum(cells), 2) if cells else None)
    return tuple(out)


def _same_figures(a: tuple[float | None, ...], b: tuple[float | None, ...], tolerance: float = 0.021) -> bool:
    return all(abs((x or 0.0) - (y or 0.0)) <= tolerance for x, y in zip(a, b, strict=True))


def numbered_lines(doc: SbeDocument) -> list[LineReading]:
    """Every numbered line of section A that carries its own figures, in printed order.

    A line carries figures when they are printed on its own row; or, for a name that wraps
    over several text lines, on the row that completes the name; or, in budgets up to 2016-17
    where a line is split by major head, on its major-head rows (closed by a printed ``Total``
    when there are several). A numbered heading followed by its own sub-lines (``4.`` then
    ``4.01``) is read only for figures printed against the heading itself.
    """
    rows = doc.table_rows()
    starts = [i for i, row in enumerate(rows) if row.number is not None]
    readings: list[LineReading] = []
    seen: set[str] = set()
    heading_names: dict[str, str] = {}
    for k, i in enumerate(starts):
        head = rows[i]
        number = head.number or ""
        if number in seen:
            continue
        stop = starts[k + 1] if k + 1 < len(starts) else len(rows)
        block = rows[i + 1 : stop]
        reading = _read_line(head, block)
        heading_names.setdefault(number, reading.name if reading is not None else _heading_name(head, block))
        if reading is not None:
            parts = number.split(".")
            parents = tuple(
                heading_names[key]
                for key in (".".join(parts[:depth]) for depth in range(1, len(parts)))
                if heading_names.get(key)
            )
            readings.append(
                LineReading(
                    reading.number, reading.name, reading.page, reading.printed, reading.values,
                    reading.basis, reading.orders, parents,
                )
            )
            seen.add(number)
    return readings


def _heading_name(head: SbeRow, block: list[SbeRow]) -> str:
    """The printed name of a numbered heading that carries no figures of its own."""
    parts = [head.label] if head.label else []
    for row in block:
        if row.has_values:
            break
        if row.label.strip():
            parts.append(row.label.strip())
    return " ".join(parts).strip()


def _read_line(head: SbeRow, block: list[SbeRow]) -> LineReading | None:
    """Read one numbered line from its own row and the unnumbered rows printed under it."""
    name_parts = [head.label] if head.label else []
    figure_rows: list[SbeRow] = [head] if head.has_values else []
    closing_total: SbeRow | None = None
    for row in block:
        label = row.label.strip()
        if not row.has_values:
            if figure_rows:
                break  # words after the figures open the next section
            if label:
                name_parts.append(label)
            continue
        if label.lower() == "total":
            closing_total = row
            break
        if _TOTAL_LABEL_RE.match(label) or _GRAND_TOTAL_RE.match(label):
            break  # 'Net', 'Total-Seeds', 'Grand Total': not this line's own figures
        if row.major_head is not None:
            # One major head of this line. Its words either complete a wrapped name or name the
            # part of the line the head pays for ('Revenue', 'Capital', 'Loans and Advances').
            if label and not figure_rows and label.lower() not in _PART_LABELS:
                name_parts.append(label)
            figure_rows.append(row)
            continue
        if figure_rows:
            break  # another printed row (recoveries, a sub-total): not this line's figures
        if label:
            name_parts.append(label)  # the row that completes a wrapped name
        figure_rows.append(row)
    if not figure_rows:
        return None
    name = " ".join(part for part in name_parts if part).strip()
    number = head.number or ""
    if len(figure_rows) == 1:
        row = figure_rows[0]
        return LineReading(number, name, row.page, (row.text,), row.values or (), "row", (row.order,))
    if closing_total is not None:
        values = closing_total.values or ()
        if not _same_figures(_add_rows(figure_rows), values):
            return None  # the printed total is not the sum of the rows above it: not read
        used = [*figure_rows, closing_total]
        return LineReading(
            number, name, closing_total.page, tuple(r.text for r in used), values, "total", tuple(r.order for r in used)
        )
    if any(row.label.strip() for row in figure_rows[1:]):
        return None  # named rows without a printed total: they may be separate lines
    return LineReading(
        number,
        name,
        figure_rows[0].page,
        tuple(r.text for r in figure_rows),
        _add_rows(figure_rows),
        "sum",
        tuple(r.order for r in figure_rows),
    )


def read_line(doc: SbeDocument, number: str, *, name: str | None = None) -> LineReading | None:
    """The reading of numbered line ``number``; ``None`` when it is not printed or its name differs."""
    for reading in numbered_lines(doc):
        if reading.number == number:
            if name is not None and not names_match(reading.name, name):
                return None
            return reading
    return None


def names_match(printed: str, expected: str) -> bool:
    a, b = normalise_name(printed), normalise_name(expected)
    if not a or not b:
        return False
    return a == b or (len(b) >= 12 and (a.startswith(b) or b.startswith(a)))


# --------------------------------------------------------------------------------------
# The check
# --------------------------------------------------------------------------------------

DEFAULT_UNSPENT_PCT = 10.0
DEFAULT_UNSPENT_CRORE = 1.0
LINK_BASES = ("name+figure", "name")
# A printed nil actual is read as "nothing was spent" only when the ministry's own revised estimate
# had already written at least three quarters of the budget estimate off. Otherwise the spending is
# most likely printed under another line of the statement.
NIL_ACTUAL_MAX_REVISED_SHARE = 0.25
_FIGURE_TOLERANCE = 0.011  # figures are printed to two decimals
_SMALL_WORDS = frozenset({"of", "and", "the", "for", "in", "to", "on", "with"})
_ACB_RE = re.compile(r"\banti[\s-]+corruption\b", re.IGNORECASE)
_NEUTRAL_REPLACE_RE = re.compile(
    r"(?<![a-z])(?:scam(?:s|med|mer|mers)?|corrupt(?:ed|ing|ly|ion)?|brib(?:e|es|ed|ing|ery)|"
    r"fraud(?:s|ulent|ulently|ster|sters)?|kickback(?:s)?|guilty|stolen|rigged|"
    r"crook(?:s|ed)?|loot(?:s|ed|ing|er|ers)?|embezzl(?:e|es|ed|ing|ement))(?![a-z])",
    re.IGNORECASE,
)

_BENIGN_ACTUAL = [
    "Spending can fall short of the budget estimate because of delayed approvals, tendering or land, vacant posts, "
    "or lower demand in a demand-driven scheme.",
    "Fresh releases are often held back while money released earlier is still unspent with States or implementing "
    "agencies, or their utilisation certificates are pending.",
    "A scheme may have been merged, renamed or moved to another Demand during the year, with its spending shown on "
    "another line.",
    "A line paid for from a cess or a reserve fund spends what the fund receives, and a body can be financed "
    "outside the budget for a year, for example by loans, which this statement does not show.",
    "The figures are gross of recoveries shown elsewhere in the statement; the Appropriation Accounts give the "
    "audited saving and the reasons recorded for it.",
]
_BENIGN_REVISED = [
    "A revised estimate is the ministry's own mid-year reading of how much it can spend; it is lowered when a scheme "
    "starts late, approvals are pending or earlier releases are still unspent.",
    "Money can be re-appropriated to another line of the same Demand during the year, so a lower revised estimate "
    "on one line may match a higher one elsewhere.",
    "The actual expenditure for the year is printed only in the budget presented two years later, and can differ "
    "from the revised estimate.",
]


def neutral_text(text: str) -> str:
    """A printed name with the words the neutrality lint rejects replaced (an official body may carry one)."""
    cleaned = _ACB_RE.sub("Vigilance", text or "")
    return _NEUTRAL_REPLACE_RE.sub("compliance-review", cleaned)


def display_ministry(printed: str) -> str:
    """'MINISTRY OF HEALTH AND FAMILY WELFARE' -> 'Ministry of Health and Family Welfare'."""
    words = " ".join((printed or "").split()).split(" ")
    if not any(w.isupper() and len(w) > 3 for w in words):
        return " ".join(words)
    out = []
    for i, word in enumerate(words):
        low = word.lower()
        out.append(low if i and low in _SMALL_WORDS else low.capitalize())
    return " ".join(out)


def entity_label(name: str, department: str) -> str:
    """'<line name> (<department>)', shortened to fit MAX_ENTITY_LENGTH the same way on every node."""
    suffix = f" ({department})" if department else ""
    full = f"{name}{suffix}"
    if len(full) <= MAX_ENTITY_LENGTH:
        return full
    leaf = name.split(" > ")[-1].strip() or name  # drop the headings above the line first
    if len(leaf) + len(suffix) <= MAX_ENTITY_LENGTH:
        return f"{leaf}{suffix}"
    room = max(MAX_ENTITY_LENGTH - len(suffix) - 1, 20)
    return f"{leaf[:room].rstrip()}…{suffix}"[:MAX_ENTITY_LENGTH]


def _crore(value: float) -> str:
    return f"Rs {value:,.2f} crore"


def severity_for(pct: float, amount_crore: float) -> str:
    """How material a shortfall is: both its share of the budget estimate and its size count."""
    if (pct >= 50.0 and amount_crore >= 100.0) or amount_crore >= 1000.0:
        return "high"
    if (pct >= 25.0 and amount_crore >= 10.0) or amount_crore >= 100.0:
        return "medium"
    return "low"


def _pages_by_document(records: list[dict[str, Any]]) -> tuple[dict[int, str], dict[int, str]]:
    primary: dict[int, str] = {}
    secondary: dict[int, str] = {}
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
        target = secondary if rec.get("document") == "secondary" else primary
        target[page] = str(rec.get("raw_text") or "")
    return primary, secondary


def _safe_url(raw: Any) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    try:
        return validate_safe_http_url(text, require_https=True, allow_private=False)
    except ValueError:
        return ""


def _threshold(raw: Any, default: float) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return default
    value = float(raw)
    return value if math.isfinite(value) and value >= 0 else default


def _document_matches(doc: SbeDocument, spec: Any) -> bool:
    """The statement is the one the task names: same Union Budget and same Demand number."""
    if not isinstance(spec, dict):
        return True
    budget = spec.get("budget")
    demand = spec.get("demand_no")
    if budget not in (None, "") and str(budget) != doc.budget:
        return False
    if demand not in (None, "") and (isinstance(demand, bool) or demand != doc.demand_no):
        return False
    return True


def _read_rows(lines_by_number: dict[str, LineReading], rows: Any) -> tuple[list[LineReading], str]:
    """Find every printed row a task lists for one side of a line; the reason is given when one cannot be read."""
    if not isinstance(rows, list) or not rows:
        return [], "no row was specified"
    found: list[LineReading] = []
    for locator in rows[:MAX_ROWS_PER_LINE]:
        if not isinstance(locator, dict):
            return [], "a row was not specified as an object"
        number = str(locator.get("number") or "").strip()
        reading = lines_by_number.get(number)
        if reading is None:
            return [], f"line {number or '?'} is not printed with figures in this statement"
        expected = locator.get("name")
        if isinstance(expected, str) and expected.strip():
            if normalise_name(reading.full_name) != normalise_name(expected):
                return [], f"line {number} is printed under a different name than the task gives"
        if reading in found:
            return [], f"line {number} is listed twice"
        found.append(reading)
    spending = [row for row in found if not BOOK_ENTRY_RE.match(row.name)]
    return (spending, "") if spending else ([], "the listed rows contain only book entries")


def unmatched_allocations(estimates: list[LineReading], actuals: list[LineReading]) -> list[str]:
    """Find funded components missing from a cross-year group, allowing for renumbering."""
    if len(estimates) == len(actuals) == 1 and not (estimates[0].parents or actuals[0].parents):
        return []  # The linked unit itself is the counterpart.
    available = Counter(normalise_name(row.name) for row in actuals)
    missing = []
    for row in estimates:
        if (row.values[COL_BUDGET_PREV] or 0.0) <= 0:
            continue
        name = normalise_name(row.name)
        if available[name]:
            available[name] -= 1
        else:
            missing.append(row.number)
    return missing


def _column(rows: list[LineReading], column: int) -> float | None:
    """The figure of a line in one column: its rows added, or nil when every row prints nil."""
    cells = [row.values[column] for row in rows if row.values[column] is not None]
    return round(math.fsum(cells), 2) if cells else None


def _numbers(rows: list[LineReading]) -> str:
    numbers = [row.number for row in rows]
    if len(numbers) == 1:
        return f"line {numbers[0]}"
    return "lines " + ", ".join(numbers[:-1]) + f" and {numbers[-1]} (added together)"


def _citations(url: str, sha: str, rows: list[LineReading], role: str, budget: str) -> list[dict[str, Any]]:
    return [
        {
            "source_url": url,
            "page": row.page,
            "printed_line": " | ".join(row.printed),
            "sha256": sha,
            "role": role,
            "document": f"Notes on Demands for Grants, Union Budget {budget}",
        }
        for row in rows
    ]


def run_ub1_union_budget_lines(
    records: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
) -> tuple[list[AuditLeadCandidate], int, float, str, dict[str, Any]]:
    ctx = context or {}
    spec = ctx.get("union_budget") if isinstance(ctx.get("union_budget"), dict) else {}
    year = str(spec.get("financial_year") or "").strip()
    raw_lines = spec.get("lines")
    line_specs = [item for item in raw_lines if isinstance(item, dict)][:MAX_LINES_PER_TASK] if isinstance(raw_lines, list) else []
    raw_thresholds = spec.get("thresholds") if isinstance(spec.get("thresholds"), dict) else {}
    threshold_pct = _threshold(raw_thresholds.get("unspent_pct"), DEFAULT_UNSPENT_PCT)
    threshold_crore = _threshold(raw_thresholds.get("unspent_crore"), DEFAULT_UNSPENT_CRORE)

    estimates_url = _safe_url(ctx.get("retrieval_url") or ctx.get("dataset_url"))
    estimates_sha = str(ctx.get("expected_sha256") or "")
    actuals_url = _safe_url(ctx.get("secondary_url") or ctx.get("secondary_dataset_url"))
    actuals_sha = str(ctx.get("secondary_expected_sha256") or ctx.get("secondary_sha256") or "")
    jurisdiction = str(ctx.get("jurisdiction") or "Union of India")

    primary_pages, secondary_pages = _pages_by_document(records)
    estimates_doc = parse_statement(primary_pages)
    actuals_doc = parse_statement(secondary_pages) if secondary_pages else None

    readings: list[dict[str, Any]] = []
    leads: list[AuditLeadCandidate] = []
    cleared = 0
    unevaluable = 0

    def skip_all(reason: str) -> tuple[list[AuditLeadCandidate], int, float, str, dict[str, Any]]:
        meta = {
            "unevaluable_count": len(line_specs) or 1,
            "amount_type": "budget_and_treasury_expenditure",
            "readings": [{"key": str(item.get("key", "")), "status": "unevaluable", "reason": reason} for item in line_specs],
        }
        return [], 0, 0.0, f"UB1 read 0 of {len(line_specs)} budget lines: {reason}.", meta

    if not _YEAR_RE.match(year) or not line_specs:
        return skip_all("the task names no financial year or no lines")
    # The year must be the one whose budget and revised estimates this statement prints.
    if estimates_doc.periods is None or estimates_doc.periods[1] != year or estimates_doc.periods[2] != year:
        return skip_all(f"the estimates statement does not print budget and revised estimates for {year}")
    if not _document_matches(estimates_doc, spec.get("estimates_document")):
        return skip_all("the estimates statement is not the Demand the task names")
    actuals_usable = (
        actuals_doc is not None
        and actuals_doc.periods is not None
        and actuals_doc.periods[0] == year
        and _document_matches(actuals_doc, spec.get("actuals_document"))
    )

    estimates_lines = {reading.number: reading for reading in numbered_lines(estimates_doc)}
    actuals_lines = (
        {reading.number: reading for reading in numbered_lines(actuals_doc)} if actuals_usable and actuals_doc else {}
    )
    ministry = neutral_text(display_ministry(estimates_doc.ministry)) or "Ministry of Finance"
    department = neutral_text(estimates_doc.department)
    demand_label = estimates_doc.demand_label
    where = f"{demand_label}, {department}" if department else demand_label

    for item in line_specs:
        key = str(item.get("key") or "line")
        est_rows, why = _read_rows(estimates_lines, item.get("estimates"))
        if not est_rows:
            unevaluable += 1
            readings.append({"key": key, "status": "unevaluable", "reason": why})
            continue
        budget_estimate = _column(est_rows, COL_BUDGET_PREV)
        if budget_estimate is None or budget_estimate <= 0:
            unevaluable += 1
            readings.append({"key": key, "status": "unevaluable", "reason": "no budget estimate is printed for the year"})
            continue
        revised_printed = _column(est_rows, COL_REVISED_PREV)
        revised = revised_printed if revised_printed is not None else 0.0
        given_name = item.get("name")
        name = neutral_text(given_name if isinstance(given_name, str) and given_name.strip() else est_rows[0].full_name)
        name = name[:MAX_NAME_LENGTH]
        entity = entity_label(name, department)
        on_lines = _numbers(est_rows)
        # When a scheme heading has funded siblings outside this comparison, name
        # the included rows in its title too, so the percentage has a clear scope.
        scope = est_rows[0].parents
        title_entity = entity
        if scope and normalise_name(name.split(" > ")[-1]) == normalise_name(scope[-1]) and any(
            row.parents == scope and row not in est_rows and (row.values[COL_BUDGET_PREV] or 0) > 0
            for row in estimates_lines.values()
        ):
            numbers = ", ".join(row.number for row in est_rows)
            title_entity = f"{entity} (budget lines {numbers})"
        cites = _citations(estimates_url, estimates_sha, est_rows, "estimates", estimates_doc.budget)
        reading: dict[str, Any] = {
            "key": key,
            "status": "read",
            "rows": [row.number for row in est_rows],
            "budget_estimate": budget_estimate,
            "revised_estimate": revised,
            "unit": "crore",
        }

        act_rows: list[LineReading] = []
        if item.get("actuals") is not None:
            if not actuals_usable or actuals_doc is None:
                unevaluable += 1
                reading.update(status="unevaluable", reason=f"the actuals statement does not print actuals for {year}")
                readings.append(reading)
                continue
            act_rows, why = _read_rows(actuals_lines, item.get("actuals"))
            if not act_rows:
                unevaluable += 1
                reading.update(status="unevaluable", reason=f"actuals: {why}")
                readings.append(reading)
                continue
            missing = unmatched_allocations(est_rows, act_rows)
            if missing:
                unevaluable += 1
                reading.update(status="unevaluable", reason=f"No actuals counterpart for estimates line(s) {', '.join(missing)}")
                readings.append(reading)
                continue
            link = str(item.get("link") or "name+figure")
            common_est, common_act = _column(est_rows, COL_BUDGET), _column(act_rows, COL_BUDGET_PREV)
            both_printed = common_est is not None and common_act is not None
            if both_printed and abs(common_est - common_act) > _FIGURE_TOLERANCE:  # type: ignore[operator]
                unevaluable += 1
                reading.update(
                    status="unevaluable",
                    reason="the two budgets print different budget estimates for the year in between, so the line was regrouped",
                )
                readings.append(reading)
                continue
            if link != "name" and not both_printed:
                unevaluable += 1
                reading.update(status="unevaluable", reason="the two budgets do not print a common figure for this line")
                readings.append(reading)
                continue
            reading["link"] = "name+figure" if both_printed else "name"
            if _column(act_rows, COL_ACTUAL) is None and (
                not both_printed or revised > NIL_ACTUAL_MAX_REVISED_SHARE * budget_estimate
            ):
                # '...' is read as nothing spent only for a line that is carried into the next year
                # and whose revised estimate had already been written down. Otherwise the spending
                # is most likely printed under another line of the statement.
                unevaluable += 1
                reading.update(
                    status="unevaluable",
                    reason="no actual is printed on this line although its revised estimate stood, so it may be printed under another line",
                )
                readings.append(reading)
                continue

        if act_rows and actuals_doc is not None:
            actual_printed = _column(act_rows, COL_ACTUAL)
            actual = actual_printed if actual_printed is not None else 0.0
            if actual < 0:
                unevaluable += 1
                reading.update(status="unevaluable", reason="the printed actual is negative (net of recoveries)")
                readings.append(reading)
                continue
            unspent = round(budget_estimate - actual, 2)
            pct = unspent / budget_estimate * 100.0
            reading.update(actual=actual, unspent=unspent, unspent_pct=round(pct, 2))
            readings.append(reading)
            if unspent < threshold_crore or pct < threshold_pct:
                cleared += 1
                continue
            cites.extend(_citations(actuals_url, actuals_sha, act_rows, "actuals", actuals_doc.budget))
            revised_cut = round(budget_estimate - revised, 2)
            revised_words = (
                f"a revised estimate of {_crore(revised)}" if revised_printed is not None else "no revised estimate ('...')"
            )
            if actual_printed is not None:
                title = f"{title_entity}: actual expenditure in {year} was {pct:.1f}% below the budget estimate"
                actual_words = f"an actual expenditure of {_crore(actual)}"
            else:
                title = f"{title_entity}: no actual expenditure is printed for {year} against a budget estimate of {_crore(budget_estimate)}"
                actual_words = "no actual expenditure ('...')"
            matched = (
                "The two statements print the same budget estimate for the year in between, which ties the rows together. "
                if reading["link"] == "name+figure"
                else "The two statements print no common figure for this line; the rows are matched by printed name and heading. "
            )
            leads.append(
                AuditLeadCandidate(
                    title=title,
                    summary=(
                        f"Union Budget {estimates_doc.budget} prints a budget estimate of {_crore(budget_estimate)} and "
                        f"{revised_words} for {year} on {on_lines} '{name}' of {where}. "
                        f"Union Budget {actuals_doc.budget} prints {actual_words} for {year} on {_numbers(act_rows)}: "
                        f"{_crore(unspent)} ({pct:.1f}%) below the budget estimate"
                        + (
                            f", of which {_crore(revised_cut)} was already dropped at the revised-estimate stage. "
                            if 0.01 <= revised_cut <= unspent
                            else ". "
                        )
                        + matched
                        + "This is a comparison of figures printed in official budget documents for public review, "
                        "not a finding of wrongdoing."
                    ),
                    methodology_code="UB1",
                    entity_name=entity,
                    jurisdiction=jurisdiction,
                    fiscal_year=f"FY{year}",
                    monetary_exposure_inr=unspent * CRORE,
                    severity=severity_for(pct, unspent),  # type: ignore[arg-type]
                    anomaly_metrics={
                        "line_key": key,
                        "compare": "actual_vs_budget_estimate",
                        "budget_estimate": budget_estimate,
                        "revised_estimate": revised,
                        "actual": actual,
                        "unspent": unspent,
                        "unspent_pct": round(pct, 2),
                        "revised_cut": revised_cut,
                        "actual_printed": actual_printed is not None,
                        "rows_added": len(est_rows),
                        "unit": "crore",
                        "threshold_pct": threshold_pct,
                        "threshold_crore": threshold_crore,
                        "link": reading["link"],
                    },
                    rule_triggered=f"UB1-ACTUAL-BELOW-BE>{threshold_pct:.0f}%",
                    citations=cites,
                    benign_explanations=_BENIGN_ACTUAL,
                    suggested_authority=ministry,
                    source_locator=", ".join(dict.fromkeys(f"p.{c['page']}" for c in cites)),
                    amount_type="budget_and_treasury_expenditure",
                )
            )
            continue

        # No actuals row for this line (not yet published, or not traceable): estimates stage only.
        if revised_printed is None and _column(est_rows, COL_BUDGET) is None:
            unevaluable += 1
            reading.update(
                status="unevaluable",
                reason="no revised estimate is printed and the line is not carried into the next year, so it may have been regrouped",
            )
            readings.append(reading)
            continue
        cut = round(budget_estimate - revised, 2)
        pct = cut / budget_estimate * 100.0
        reading.update(revised_cut=cut, revised_cut_pct=round(pct, 2))
        readings.append(reading)
        if cut < threshold_crore or pct < threshold_pct:
            cleared += 1
            continue
        if revised_printed is not None:
            title = f"{title_entity}: revised estimate for {year} was {pct:.1f}% below the budget estimate"
            revised_words = f"a revised estimate of {_crore(revised)}"
        else:
            title = f"{title_entity}: no revised estimate is printed for {year} against a budget estimate of {_crore(budget_estimate)}"
            revised_words = "no revised estimate ('...')"
        leads.append(
            AuditLeadCandidate(
                title=title,
                summary=(
                    f"Union Budget {estimates_doc.budget} prints a budget estimate of {_crore(budget_estimate)} and "
                    f"{revised_words} for {year} on {on_lines} '{name}' of {where}: {_crore(cut)} ({pct:.1f}%) "
                    f"of the budget estimate was dropped at the revised-estimate stage. No actual expenditure for "
                    f"{year} is compared here. This is a comparison of figures printed in an official budget document "
                    "for public review, not a finding of wrongdoing."
                ),
                methodology_code="UB1",
                entity_name=entity,
                jurisdiction=jurisdiction,
                fiscal_year=f"FY{year}",
                monetary_exposure_inr=cut * CRORE,
                severity=severity_for(pct, cut),  # type: ignore[arg-type]
                anomaly_metrics={
                    "line_key": key,
                    "compare": "revised_vs_budget_estimate",
                    "budget_estimate": budget_estimate,
                    "revised_estimate": revised,
                    "revised_cut": cut,
                    "revised_cut_pct": round(pct, 2),
                    "revised_printed": revised_printed is not None,
                    "rows_added": len(est_rows),
                    "unit": "crore",
                    "threshold_pct": threshold_pct,
                    "threshold_crore": threshold_crore,
                },
                rule_triggered=f"UB1-RE-BELOW-BE>{threshold_pct:.0f}%",
                citations=cites,
                benign_explanations=_BENIGN_REVISED,
                suggested_authority=ministry,
                source_locator=", ".join(dict.fromkeys(f"p.{c['page']}" for c in cites)),
                amount_type="budget_and_treasury_expenditure",
            )
        )

    leads.sort(key=lambda lead: (-float(lead.monetary_exposure_inr), lead.title))
    read_count = sum(1 for r in readings if r["status"] == "read")
    summary = (
        f"UB1 read {read_count} of {len(line_specs)} budget lines for {year} ({where}): {len(leads)} below the budget "
        f"estimate by {threshold_pct:.0f}% or more, {cleared} within it, {unevaluable} unevaluable."
    )
    meta = {
        "unevaluable_count": unevaluable,
        "amount_type": "budget_and_treasury_expenditure",
        "readings": readings,
    }
    audited_crore = math.fsum(r["budget_estimate"] for r in readings if r["status"] == "read")
    return leads, cleared, audited_crore * CRORE, summary, meta


def _validate_rows(rows: Any, where: str, errors: list[str]) -> None:
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_ROWS_PER_LINE:
        errors.append(f"{where} must list 1 to {MAX_ROWS_PER_LINE} printed rows, each with 'number' and 'name'.")
        return
    seen: set[str] = set()
    for i, loc in enumerate(rows):
        rwhere = f"{where}[{i}]"
        if not isinstance(loc, dict):
            errors.append(f"{rwhere} must be an object with 'number' and 'name'.")
            continue
        number = loc.get("number")
        if not isinstance(number, str) or not _LINE_NUMBER_RE.match(number):
            errors.append(f"{rwhere}.number must be a printed line number such as '12' or '12.03'.")
        elif number in seen:
            errors.append(f"{rwhere}.number '{number}' is listed twice.")
        else:
            seen.add(number)
        row_name = loc.get("name")
        if not isinstance(row_name, str) or not row_name.strip() or len(row_name) > 600:
            errors.append(f"{rwhere}.name must be the printed name of the row.")


def validate_context(ctx: Any, where: str) -> list[str]:
    """Problems in a UB1 task's ``payload.context`` (an empty list means it is well formed)."""
    errors: list[str] = []
    spec = ctx.get("union_budget") if isinstance(ctx, dict) else None
    if not isinstance(spec, dict):
        return [f"{where}.payload.context.union_budget is required for UB1 tasks."]
    base = f"{where}.payload.context.union_budget"
    year = spec.get("financial_year")
    if not isinstance(year, str) or not _YEAR_RE.match(year):
        errors.append(f"{base}.financial_year must look like '2019-20'.")
    for doc_key, required in (("estimates_document", True), ("actuals_document", False)):
        doc = spec.get(doc_key)
        if doc is None and not required:
            continue
        if not isinstance(doc, dict):
            errors.append(f"{base}.{doc_key} must be an object with 'budget' and 'demand_no'.")
            continue
        budget = doc.get("budget")
        if not isinstance(budget, str) or not _YEAR_RE.match(budget):
            errors.append(f"{base}.{doc_key}.budget must look like '2020-21'.")
        demand = doc.get("demand_no")
        if isinstance(demand, bool) or not isinstance(demand, int) or not 1 <= demand <= 200:
            errors.append(f"{base}.{doc_key}.demand_no must be the Demand number.")
    if isinstance(year, str) and _YEAR_RE.match(year):
        est = spec.get("estimates_document")
        if isinstance(est, dict) and est.get("budget") != shift_year(year, 1):
            errors.append(f"{base}.estimates_document.budget must be the budget after {year}.")
        act = spec.get("actuals_document")
        if isinstance(act, dict) and act.get("budget") != shift_year(year, 2):
            errors.append(f"{base}.actuals_document.budget must be the second budget after {year}.")
    lines = spec.get("lines")
    if not isinstance(lines, list) or not 1 <= len(lines) <= MAX_LINES_PER_TASK:
        errors.append(f"{base}.lines must list 1 to {MAX_LINES_PER_TASK} budget lines.")
        return errors
    seen: set[str] = set()
    for i, item in enumerate(lines):
        lwhere = f"{base}.lines[{i}]"
        if not isinstance(item, dict):
            errors.append(f"{lwhere} must be an object.")
            continue
        key = item.get("key")
        if not isinstance(key, str) or not key.strip() or len(key) > 40 or key in seen:
            errors.append(f"{lwhere}.key must be a short key, unique in the task.")
        else:
            seen.add(key)
        line_name = item.get("name")
        if line_name is not None and (not isinstance(line_name, str) or not line_name.strip() or len(line_name) > 600):
            errors.append(f"{lwhere}.name must be the name the line is reported under.")
        _validate_rows(item.get("estimates"), f"{lwhere}.estimates", errors)
        if item.get("actuals") is not None:
            _validate_rows(item.get("actuals"), f"{lwhere}.actuals", errors)
            if spec.get("actuals_document") is None:
                errors.append(f"{lwhere}.actuals needs {base}.actuals_document.")
            if item.get("link", "name+figure") not in LINK_BASES:
                errors.append(f"{lwhere}.link must be one of: {', '.join(LINK_BASES)}.")
    thresholds = spec.get("thresholds")
    if thresholds is not None:
        if not isinstance(thresholds, dict):
            errors.append(f"{base}.thresholds must be an object.")
        else:
            for tkey in ("unspent_pct", "unspent_crore"):
                value = thresholds.get(tkey)
                if value is not None and (
                    isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value < 0
                ):
                    errors.append(f"{base}.thresholds.{tkey} must be a number, zero or more.")
    return errors
