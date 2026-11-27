#!/usr/bin/env python3
"""Build project P3, ``projects/union-budget-unspent-funds.json``, from official budget papers.

    uv run python scripts/build_union_budget_project.py --cache ~/.cache/aiwatchdogs/union-budget --download

Every Union Budget prints, for each Demand for Grants, a "Notes on Demands for Grants" statement
(``sbe<N>.pdf`` on indiabudget.gov.in). This script:

1. keeps a local copy of every statement of Union Budgets 2015-16 to 2026-27 (``--download``
   fetches the missing ones; nothing else is ever requested);
2. loads each PDF through the same loader the volunteer CLI uses, and parses it with the UB1
   engine (``aiwatchdogs.engines.union_budget``);
3. accepts a statement only if its numbered lines add up to its printed Grand Total in all four
   periods, so a line is never offered to the swarm from a table the reader got wrong;
4. links each line that carried a budget estimate for a financial year (printed, with the
   revised estimate, one budget later) to the row that prints its actual expenditure (one budget
   later still), by printed name and by the one figure both budgets print for the year between;
5. groups the lines into exactly 10,000 tasks: the largest lines get a task each, smaller lines
   of one Demand and year share a task;
6. runs every task through the UB1 engine, and refuses to write a project in which any listed
   line cannot be read.

The project file, ``documents.csv`` (every statement, its URL and SHA-256) and ``coverage.json``
are written under ``projects/``. Re-run it when a new Union Budget is published: the current
budget is served from the site root and moves to its own folder a year later.
"""

from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
import math
import multiprocessing
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from aiwatchdogs.cli.runner import _build_task_context  # noqa: E402
from aiwatchdogs.connectors.loader import load_dataset  # noqa: E402
from aiwatchdogs.engines import union_budget as ub  # noqa: E402
from aiwatchdogs.engines.registry import run_engine  # noqa: E402

SLUG = "union-budget-unspent-funds"
TARGET_TASKS = 10_000
SET_SIZE = 10  # most lines one shared task may carry
FIRST_YEAR, LAST_YEAR = 2014, 2025  # financial years 2014-15 to 2025-26
ROOT = "https://www.indiabudget.gov.in/"
USER_AGENT = "Mozilla/5.0 (compatible; aiwatchdogs-project-build; +https://aiwatchdogs.in)"

# Where each Union Budget's statements are published. The current budget sits at the site root.
BUDGET_BASE: dict[str, str] = {
    "2015-16": ROOT + "budget2015-2016/ub2015-16/eb/",
    "2016-17": ROOT + "budget2016-2017/ub2016-17/eb/",
    "2017-18": ROOT + "budget2017-2018/ub2017-18/eb/",
    "2018-19": ROOT + "budget2018-2019/ub2018-19/eb/",
    "2019-20": ROOT + "budget2019-20/doc/eb/",
    "2020-21": ROOT + "budget2020-21/doc/eb/",
    "2021-22": ROOT + "budget2021-22/doc/eb/",
    "2022-23": ROOT + "budget2022-23/doc/eb/",
    "2023-24": ROOT + "budget2023-24/doc/eb/",
    "2024-25": ROOT + "budget2024-25/doc/eb/",
    "2025-26": ROOT + "budget2025-26/doc/eb/",
    "2026-27": ROOT + "doc/eb/",
}
BUDGET_LANDING: dict[str, str] = {
    "2015-16": ROOT + "budget2015-2016/budget.asp",
    "2016-17": ROOT + "budget2016-2017/budget.asp",
    "2017-18": ROOT + "budget2017-2018/budget.asp",
    "2018-19": ROOT + "budget2018-2019/budget.asp",
    "2019-20": ROOT + "budget2019-20/index.php",
    "2020-21": ROOT + "budget2020-21/index.php",
    "2021-22": ROOT + "budget2021-22/index.php",
    "2022-23": ROOT + "budget2022-23/index.php",
    "2023-24": ROOT + "budget2023-24/index.php",
    "2024-25": ROOT + "budget2024-25/index.php",
    "2025-26": ROOT + "budget2025-26/index.php",
    "2026-27": ROOT,
}
MAX_DEMAND_PROBE = 125
# Left out as whole statements. Debt servicing is not money voted to be spent on anything:
# repayment is the rolling over of borrowing, and interest is whatever the debt costs. The
# Railways statement prints its works net of the railway funds that pay for them, so a line can
# fall to nil in a year when the work went ahead on fund money.
EXCLUDED_DEPARTMENTS = frozenset({"repaymentofdebt", "interestpayments", "ministryofrailways"})
_FUND = (
    r"(?:gross\s+budget\w*\s+support|gbs|national\s+investment\s+funds?|nif|pmssn"
    r"|central\s+roads?\s+(?:and|&)\s+infrastructure\s+fund|crif|central\s+road\s+fund|crf"
    r"|madhyamik\s+\w+\s+uch\w+\s+shiksha\s+kosh|musk|prarambhik\s+shiksha\s+kosh|psk"
    r"|senior\s+citizens?\s+welfare\s+fund|scwf|nirbhaya\s+fund|national\s+clean\s+energy\s+fund|ncef)"
)
# A row named only for where the money comes from, or for which part of the scheme it is (the
# externally aided part, the programme part), not for what it is spent on.
FUNDING_ROW_RE = re.compile(
    rf"^(?:support\s*from.*|from\s+{_FUND}.*|funded\s+from\b.*|schemes?\s+financed\s+from\b.*"
    rf"|{_FUND}(?:\s*\([a-z]+\))?"
    r"|(?:non[-\s]*)?eap(?:\s+components?)?|program(?:me)?\s+component(?:\s*-\s*.*)?"
    r"|[\w&]+\s*-\s*(?:program(?:me)?|eap)\s+components?)$",
    re.IGNORECASE,
)
# A row that names a scheme and, at the end, the source or the part: '(NIF)', '(EAP Component)',
# '- EAP Component', '- financed from CRIF', '(Funded from Nirbhaya Fund)'.
FUNDING_SUFFIX_RE = re.compile(
    rf"\s*(?:[\(\[]\s*(?:support\s+from\s+)?{_FUND}\s*[\)\]]"
    r"|[\(\[]\s*(?:non[-\s]*)?eap(?:\s+components?)?\s*[\)\]]"
    r"|[-\u2013]\s*(?:eap|programme)\s+components?"
    r"|(?:[-\u2013]\s*)?(?:schemes\s+)?financed\s+from\s+.*"
    r"|[\(\[]\s*(?:funded\s+from|met\s+out\s+of)\s+[^\)\]]*[\)\]]"
    r"|[-\u2013]\s*(?:grants?|loans?)|[\(\[]\s*(?:block\s+)?(?:grants?|loans?)\s*[\)\]])\s*$",
    re.IGNORECASE,
)
# A heading whose costs are mostly recovered inside the heading itself (a departmental undertaking
# that sells what it makes, a line netted against its own receipts). A row "met from" a fund is
# different: the fund pays, but the money is still spent on the scheme.
OFFSET_ROW_RE = re.compile(r"^(?:less|deduct)\b(?!.*\bmet\s+from\b)", re.IGNORECASE)
OFFSET_SHARE = 0.5
FUND_HEADING_RE = re.compile(r"\b(?:fund|kosh|nidhi)\b", re.IGNORECASE)
# Headings that only group fund accounts or book entries.
HEADING_BOOK_RE = re.compile(r"^(?:reserve\s+funds?|amount\s+met\s+from|transfers?\s+to|appropriation\s+to)\b", re.IGNORECASE)
PASS_THROUGH_RE = re.compile(r"^(?:amount\s+met\s+from|met\s+from)\b", re.IGNORECASE)
# Provisions held in one place and spent, by design, on other lines.
LUMP_SUM_RE = re.compile(
    r"lump\s*-?\s*sum|lumpsump|^new\s+schemes?\b|provision\s+for\s+(?:projects?|schemes?)\b.*north\s*-?\s*east",
    re.IGNORECASE,
)
NON_CASH_RE = re.compile(r"issue\s+of\s+(?:special\s+)?securities", re.IGNORECASE)

COL_A, COL_BP, COL_RP, COL_B = ub.COL_ACTUAL, ub.COL_BUDGET_PREV, ub.COL_REVISED_PREV, ub.COL_BUDGET


# --------------------------------------------------------------------------------------
# 1-3. Documents
# --------------------------------------------------------------------------------------


@dataclass
class Statement:
    budget: str
    number: int  # the N of sbe<N>.pdf
    url: str
    path: Path
    sha256: str = ""
    size: int = 0
    error: str = ""
    pages: dict[int, str] = field(default_factory=dict)
    doc: ub.SbeDocument | None = None
    lines: list[ub.LineReading] = field(default_factory=list)
    closed: bool = False

    @property
    def readable(self) -> bool:
        return not self.error and self.doc is not None and self.doc.table_end is not None


def _download(url: str, dest: Path) -> bool:
    """Fetch one statement. ``False`` when the site has no such file."""
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 - fixed https host
                data = resp.read()
                ctype = resp.headers.get("Content-Type", "")
            if not data.startswith(b"%PDF-") or "html" in ctype.lower():
                return False
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            return True
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 404, 410):
                return False
        except OSError:
            pass
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Could not download {url}")


def ensure_documents(cache: Path, download: bool) -> list[Statement]:
    statements: list[Statement] = []
    for budget, base in BUDGET_BASE.items():
        misses = 0
        for n in range(1, MAX_DEMAND_PROBE + 1):
            path = cache / budget / f"sbe{n}.pdf"
            url = f"{base}sbe{n}.pdf"
            if not path.exists():
                if not download or not _download(url, path):
                    misses += 1
                    if misses >= 3:
                        break
                    continue
                time.sleep(0.2)
            misses = 0
            statements.append(Statement(budget=budget, number=n, url=url, path=path))
    return statements


def _load_one(path_str: str) -> dict[str, Any]:
    """Read one PDF exactly as the volunteer CLI does (same loader, same pinned PDF reader)."""
    try:
        ds = load_dataset(path_str, allow_local_files=True, use_cache=False)
    except Exception as exc:  # noqa: BLE001 - a damaged official file is recorded, not fatal
        raw = Path(path_str).read_bytes()
        return {"sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw), "error": type(exc).__name__, "pages": {}}
    pages = {int(rec["page"]): str(rec.get("raw_text") or "") for rec in ds.records}
    return {"sha256": ds.sha256, "size": ds.byte_size, "error": "", "pages": pages}


def is_closed(doc: ub.SbeDocument, lines: list[ub.LineReading]) -> bool:
    """Do the numbered lines add up to the printed Grand Total, in all four periods?

    Recoveries printed on an unnumbered row of their own (always nil or negative) are part of
    the total, so they are allowed for.
    """
    if doc.table_end is None:
        return False
    grand = next(row for row in doc.rows if row.order == doc.table_end)
    used = {order for line in lines for order in line.orders}
    recoveries = [
        row
        for row in doc.table_rows()
        if row.has_values
        and not row.label
        and row.number is None
        and row.major_head is None
        and row.order not in used
        and all((v or 0.0) <= 0 for v in row.values or ())
    ]
    for col in (COL_A, COL_BP, COL_RP, COL_B):
        total = grand.values[col] or 0.0 if grand.values else 0.0
        own = math.fsum((line.values[col] or 0.0) for line in lines)
        with_recoveries = own + math.fsum((row.values[col] or 0.0) for row in recoveries if row.values)
        tolerance = max(0.06, 1e-6 * abs(total))
        if abs(own - total) > tolerance and abs(with_recoveries - total) > tolerance:
            return False
    return True


def load_statements(statements: list[Statement], workers: int) -> None:
    ctx = multiprocessing.get_context("fork")
    with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as pool:
        for st, res in zip(statements, pool.map(_load_one, [str(s.path) for s in statements], chunksize=8), strict=True):
            st.sha256, st.size, st.error, st.pages = res["sha256"], res["size"], res["error"], res["pages"]
            if st.error:
                continue
            doc = ub.parse_statement(st.pages)
            if doc.budget != st.budget or doc.demand_no != st.number or doc.periods is None:
                st.error = "header_not_recognised"
                continue
            st.doc = doc
            st.lines = ub.numbered_lines(doc)
            st.closed = is_closed(doc, st.lines)


# --------------------------------------------------------------------------------------
# 4. Linking a year's estimates rows to its actuals rows
# --------------------------------------------------------------------------------------


@dataclass
class Unit:
    """What one check compares in one statement: a printed row, or the rows of one scheme added together."""

    name: str  # the name it is reported under
    leaf: str  # the last part of that name
    rows: list[ub.LineReading]

    @property
    def values(self) -> tuple[float | None, ...]:
        out: list[float | None] = []
        for col in range(ub.VALUES_PER_ROW):
            cells = [row.values[col] for row in self.rows if row.values[col] is not None]
            out.append(round(math.fsum(cells), 2) if cells else None)
        return tuple(out)

    @property
    def order(self) -> tuple[int, ...]:
        return self.rows[0].orders

    @property
    def number(self) -> str:
        return self.rows[0].number


@dataclass
class Item:
    """One budget line of one financial year, as the swarm will check it."""

    year: str
    estimates: Statement
    est: Unit
    actuals: Statement | None = None
    act: Unit | None = None
    link: str = ""  # 'name+figure' or 'name' when an actuals row was found
    rank: int = 0
    _values: tuple[float | None, ...] | None = None

    @property
    def budget_estimate(self) -> float:
        return float(self.est.values[COL_BP] or 0.0)


def _eq(a: float | None, b: float | None) -> bool:
    return a is not None and b is not None and abs(a - b) <= 0.011


def _clean(name: str) -> str:
    return " ".join(name.split())


def units_of(st: Statement, left_out: collections.Counter[str] | None = None) -> list[Unit]:
    """The spending lines of one statement.

    Rows that only say where a scheme's money comes from are added together under the scheme, so
    money moving from one source to another is not read as money left unspent. Book entries,
    lump-sum provisions and rows that are financing in themselves are left out.
    """
    by_parent: dict[tuple[str, ...], list[ub.LineReading]] = collections.defaultdict(list)
    groups: list[list[ub.LineReading]] = []
    for line in st.lines:
        if line.parents:
            by_parent[line.parents].append(line)
        else:
            groups.append([line])
    units: list[Unit] = []
    for rows in [*groups, *by_parent.values()]:
        parents = rows[0].parents
        names = [_clean(row.name) for row in rows]
        if not parents and FUNDING_ROW_RE.match(names[0]) and not FUNDING_SUFFIX_RE.search(names[0]):
            if left_out is not None and (rows[0].values[COL_BP] or 0) > 0:
                left_out["funding source without a named scheme"] += 1
            continue
        if any(NON_CASH_RE.search(name) for name in names):
            continue  # paid in securities, not in cash
        if any(LUMP_SUM_RE.search(_clean(part)) for part in parents):
            continue
        if parents:
            # A departmental undertaking's costs set against its own recoveries: what the row prints
            # is not what was left to be found from the budget.
            gross = math.fsum(max(row.values[COL_BP] or 0.0, 0.0) for row in rows)
            offset = -math.fsum(
                min(row.values[COL_BP] or 0.0, 0.0)
                for row, name in zip(rows, names, strict=True)
                if OFFSET_ROW_RE.match(name)
            )
            if gross > 0 and offset >= OFFSET_SHARE * gross:
                continue
        spending = [
            row
            for row, name in zip(rows, names, strict=True)
            if name and not ub.BOOK_ENTRY_RE.match(name) and not LUMP_SUM_RE.search(name)
        ]
        funding = [row for row in spending if parents and FUNDING_ROW_RE.match(_clean(row.name))]
        schemes = [row for row in spending if row not in funding]
        if parents and funding:
            heading = _clean(parents[-1])
            if HEADING_BOOK_RE.match(heading):
                continue
            passes_through = any(PASS_THROUGH_RE.match(name) for name in names)
            if not schemes and FUND_HEADING_RE.search(heading) and passes_through:
                continue  # the heading is a fund: money in, the same money out
            # Money moves between a scheme's sources and components during the year, so the
            # scheme is compared as a whole: every spending row under its heading, added.
            units.append(Unit(" > ".join(_clean(part) for part in parents), heading, spending))
            continue
        merged: dict[str, list[ub.LineReading]] = {}
        for row in schemes:
            base = FUNDING_SUFFIX_RE.sub("", _clean(row.name)).strip() or _clean(row.name)
            # Rows of one name are parts of one scheme only under a heading; at the top level two
            # rows of the same name are different lines (the Army's and the Navy's, say).
            key = ub.normalise_name(base) if parents else f"{ub.normalise_name(base)}#{row.number}"
            merged.setdefault(key, []).append(row)
        for same in merged.values():
            base = FUNDING_SUFFIX_RE.sub("", _clean(same[0].name)).strip() or _clean(same[0].name)
            if len(same) == 1 and base == _clean(same[0].name):
                units.append(Unit(same[0].full_name, _clean(same[0].name), same))
            else:
                units.append(Unit(" > ".join([*(_clean(part) for part in parents), base]), base, same))
    units.sort(key=lambda unit: unit.order)
    return units


def _absorbed(unit: Unit, others: list[Unit], grew: int, empty: int) -> bool:
    """Did a line of a longer, similar name pick up money in a column where this one has no say?

    'Special Assistance' cut at the revised stage while a new 'Special Assistance as Loan ...'
    appears there with no budget estimate of its own: the money moved, it was not left unspent.
    """
    mine = ub.normalise_name(unit.leaf)
    if len(mine) < 8:
        return False
    for other in others:
        if other is unit:
            continue
        theirs = ub.normalise_name(other.leaf)
        if theirs != mine and theirs.startswith(mine):
            values = other.values
            if (values[grew] or 0.0) > 0 and values[empty] is None:
                return True
    return False


def link_year(year: str, by_budget: dict[str, dict[int, Statement]], left_out: collections.Counter[str]) -> list[Item]:
    est_budget, act_budget = ub.shift_year(year, 1), ub.shift_year(year, 2)
    est_docs = by_budget.get(est_budget, {})
    act_docs = {n: st for n, st in by_budget.get(act_budget, {}).items() if st.readable and st.closed}
    # Union Budget 2017-18 dropped the Plan / Non-Plan split and regrouped the schemes: the lines of
    # the budget before it cannot be followed into it by name, so no actual is compared for 2015-16.
    if est_docs and act_docs:
        first_est = next((st.doc for st in est_docs.values() if st.doc is not None), None)
        first_act = next((st.doc for st in act_docs.values() if st.doc is not None), None)
        if first_est is None or first_act is None or first_est.subcolumns != first_act.subcolumns:
            act_docs = {}
    act_units = {n2: units_of(st2) for n2, st2 in act_docs.items()}
    index: dict[tuple[str, float | None], set[int]] = collections.defaultdict(set)
    for n2, units2 in act_units.items():
        for unit in units2:
            values = unit.values
            index[(ub.normalise_name(unit.name), values[COL_BP])].add(n2)
            index[(ub.normalise_name(unit.leaf), values[COL_BP])].add(n2)

    items: list[Item] = []
    for n in sorted(est_docs):
        st = est_docs[n]
        if not st.readable or st.doc is None:
            continue
        if ub.normalise_name(st.doc.department) in EXCLUDED_DEPARTMENTS:
            continue
        units = units_of(st, left_out)
        allocation = [unit for unit in units if (unit.values[COL_BP] or 0.0) > 0]
        if not st.closed:
            left_out["statement does not add up to its Grand Total"] += len(allocation)
            continue
        target: Statement | None = None
        if act_docs:
            votes: collections.Counter[int] = collections.Counter()
            for unit in units:
                figure = unit.values[COL_B]
                if figure is None:
                    continue
                for n2 in index.get((ub.normalise_name(unit.name), figure), ()):
                    votes[n2] += 2
                for n2 in index.get((ub.normalise_name(unit.leaf), figure), ()):
                    votes[n2] += 1
            if votes:
                target = act_docs[sorted(votes.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]]
            else:
                department = ub.normalise_name(st.doc.department)
                same = [s2 for s2 in act_docs.values() if s2.doc and ub.normalise_name(s2.doc.department) == department]
                if len(same) == 1:
                    target = same[0]

        pairs: dict[int, tuple[Unit, str]] = {}
        if target is not None:
            free = list(act_units[target.number])
            todo = list(allocation)

            def take(unit: Unit, found: Unit, basis: str) -> None:
                pairs[id(unit)] = (found, basis)
                free.remove(found)
                todo.remove(unit)

            def full(unit: Unit) -> str:
                return ub.normalise_name(unit.name)

            # Same name under the same headings, and the same budget estimate for the year between.
            for unit in list(todo):
                found = [c for c in free if full(c) == full(unit) and _eq(unit.values[COL_B], c.values[COL_BP])]
                if found:
                    take(unit, found[0], "name+figure")
            # Same name (the heading above it changed), same figure, and only one such row.
            for unit in list(todo):
                found = [
                    c
                    for c in free
                    if ub.normalise_name(c.leaf) == ub.normalise_name(unit.leaf) and _eq(unit.values[COL_B], c.values[COL_BP])
                ]
                if len(found) == 1:
                    take(unit, found[0], "name+figure")
            # A scheme renamed between the two budgets: the same rows by name under its heading,
            # and the same budget estimate for the year between, on exactly one candidate.
            def parts(unit: Unit) -> tuple[str, ...]:
                return tuple(sorted(ub.normalise_name(_clean(row.name)) for row in unit.rows))

            for unit in list(todo):
                if len(unit.rows) < 2 or unit.values[COL_B] is None:
                    continue
                found = [
                    c for c in free if len(c.rows) >= 2 and parts(c) == parts(unit) and _eq(unit.values[COL_B], c.values[COL_BP])
                ]
                if len(found) == 1:
                    take(unit, found[0], "name+figure")
            # Same name, and neither budget prints a figure for the year between.
            for unit in list(todo):
                if unit.values[COL_B] is not None:
                    continue
                found = [c for c in free if full(c) == full(unit) and c.values[COL_BP] is None]
                rivals = [x for x in todo if full(x) == full(unit) and x.values[COL_B] is None]
                if len(found) == 1 and len(rivals) == 1:
                    take(unit, found[0], "name")

        for unit in allocation:
            values = unit.values
            budget_estimate = float(values[COL_BP] or 0.0)
            revised = float(values[COL_RP] or 0.0)
            item = Item(year=year, estimates=st, est=unit)
            # A longer-named line that appears at the revised stage with no budget estimate of its
            # own took this line's money: nothing can be said about this one.
            if _absorbed(unit, units, COL_RP, COL_BP):
                left_out["money moved to a line of a similar name"] += 1
                continue
            if id(unit) in pairs and target is not None:
                found, basis = pairs[id(unit)]
                found_values = found.values
                actual = found_values[COL_A]
                continues = values[COL_B] is not None and found_values[COL_BP] is not None
                written_down = revised <= ub.NIL_ACTUAL_MAX_REVISED_SHARE * budget_estimate
                # Both estimates and actuals statements are already verified to add up to their Grand
                # Totals. A negative actual (recoveries above spending) cannot be compared with an
                # estimate. A nil actual is read as nothing spent only under the engine's own
                # conditions. And an actual is not compared when a line of a longer, similar name
                # carries one too.
                if (
                    (actual is None or actual >= 0)
                    and (actual is not None or (continues and written_down))
                    and not ub.unmatched_allocations(unit.rows, found.rows)
                    and not _absorbed(found, act_units[target.number], COL_A, COL_BP)
                ):
                    item.actuals, item.act, item.link = target, found, basis
            if item.act is None and values[COL_RP] is None and values[COL_B] is None:
                left_out["no revised estimate and no following year"] += 1
                continue
            items.append(item)
    return items


# --------------------------------------------------------------------------------------
# 5. Tasks
# --------------------------------------------------------------------------------------


def plan(items: list[Item]) -> tuple[list[list[Item]], int]:
    """Group lines into exactly TARGET_TASKS tasks. Returns the groups and how many lines stand alone."""
    ranked = sorted(items, key=lambda it: (-it.budget_estimate, it.year, it.estimates.number, it.est.order))
    for rank, item in enumerate(ranked):
        item.rank = rank
    groups: dict[tuple[str, int], list[Item]] = collections.defaultdict(list)
    for item in items:
        groups[(item.year, item.estimates.number)].append(item)
    shared = {key: len(members) for key, members in groups.items()}
    total = sum(math.ceil(count / SET_SIZE) for count in shared.values())
    alone = 0
    if total > TARGET_TASKS:
        raise SystemExit(f"{total} tasks even with every line shared; raise SET_SIZE.")
    while total < TARGET_TASKS:
        if alone >= len(ranked):
            raise SystemExit(f"Only {total} tasks with every line alone; lower TARGET_TASKS.")
        key = (ranked[alone].year, ranked[alone].estimates.number)
        before = math.ceil(shared[key] / SET_SIZE)
        shared[key] -= 1
        total += 1 + math.ceil(shared[key] / SET_SIZE) - before
        alone += 1

    tasks: list[list[Item]] = []
    for key in sorted(groups):
        members = sorted(groups[key], key=lambda it: it.est.order)
        tasks.extend([it] for it in members if it.rank < alone)
        rest = [it for it in members if it.rank >= alone]
        if rest:
            sets = math.ceil(len(rest) / SET_SIZE)
            size, extra = divmod(len(rest), sets)
            start = 0
            for i in range(sets):
                stop = start + size + (1 if i < extra else 0)
                tasks.append(rest[start:stop])
                start = stop
    if len(tasks) != TARGET_TASKS:
        raise SystemExit(f"Planned {len(tasks)} tasks, expected {TARGET_TASKS}.")
    return tasks, alone


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _amount(crore: float) -> str:
    return f"Rs {crore:,.2f} crore"


def _slug_number(number: str) -> str:
    return number.replace(".", "-")


def build_task(members: list[Item], set_index: int) -> dict[str, Any]:
    first = members[0]
    st = first.estimates
    assert st.doc is not None
    year = first.year
    department = ub.neutral_text(st.doc.department)
    targets = {it.actuals.number for it in members if it.actuals is not None}
    assert len(targets) <= 1, "one task reads at most one actuals statement"
    actuals = next((it.actuals for it in members if it.actuals is not None), None)
    total_be = math.fsum(it.budget_estimate for it in members)

    if len(members) == 1:
        task_id = f"ub-{year}-d{st.number:03d}-l{_slug_number(first.est.number)}"
        numbers = [row.number for row in first.est.rows]
        where = f"line {numbers[0]}" if len(numbers) == 1 else "lines " + " + ".join(numbers)
        title = f"{year} · {_clip(department, 70)}: {_clip(ub.neutral_text(first.est.name), 90)} ({where}, BE {_amount(total_be)})"
    else:
        task_id = f"ub-{year}-d{st.number:03d}-s{set_index:02d}"
        title = (
            f"{year} · {_clip(department, 70)}: {len(members)} smaller lines, {members[0].est.number} to "
            f"{members[-1].est.number} (BE {_amount(total_be)} in all)"
        )

    def rows_of(unit: Unit) -> list[dict[str, str]]:
        return [{"number": row.number, "name": row.full_name} for row in unit.rows]

    lines: list[dict[str, Any]] = []
    for it in members:
        entry: dict[str, Any] = {"key": it.est.number}
        if len(it.est.rows) > 1 or it.est.name != it.est.rows[0].full_name:
            entry["name"] = it.est.name  # several rows of one scheme, reported under the scheme's name
        entry["estimates"] = rows_of(it.est)
        if it.act is not None:
            entry["actuals"] = rows_of(it.act)
            entry["link"] = it.link
        lines.append(entry)

    spec: dict[str, Any] = {
        "financial_year": year,
        "estimates_document": {"budget": st.budget, "demand_no": st.number},
    }
    payload: dict[str, Any] = {
        "retrieval_url": st.url,
        "expected_sha256": st.sha256,
    }
    with_actuals = sum(1 for it in members if it.act is not None)
    if actuals is not None:
        spec["actuals_document"] = {"budget": actuals.budget, "demand_no": actuals.number}
        payload["secondary_url"] = actuals.url
        payload["secondary_expected_sha256"] = actuals.sha256
        publisher = f"Ministry of Finance, Government of India (Union Budgets {st.budget} and {actuals.budget})"
        instructions = (
            f"Verify the SHA-256 of both pinned statements and run UB1 on the listed lines: the budget and revised "
            f"estimates for {year} from Union Budget {st.budget}, the actual expenditure from Union Budget "
            f"{actuals.budget}. Cite page and printed row for every figure. A line that is not printed as listed is "
            "unevaluable, never guessed."
        )
        if with_actuals < len(members):
            instructions += " A line listed without actuals rows is compared with its revised estimate only."
    else:
        publisher = f"Ministry of Finance, Government of India (Union Budget {st.budget})"
        why = (
            f"Union Budget {ub.shift_year(year, 2)}, which will print the actuals, is not yet published"
            if ub.shift_year(year, 2) not in BUDGET_BASE
            else f"These lines cannot be followed by name into Union Budget {ub.shift_year(year, 2)}"
        )
        instructions = (
            f"Verify the SHA-256 of the pinned statement and run UB1 on the listed lines: the budget and revised "
            f"estimates for {year} from Union Budget {st.budget}. {why}, so the revised estimate is compared with "
            "the budget estimate and no actual is read. Cite page and printed row for every figure."
        )
    spec["lines"] = lines
    payload.update(
        {
            "official_publisher": publisher,
            "source_landing_page": BUDGET_LANDING[st.budget],
            "artifact_title": _clip(
                f"Notes on Demands for Grants {st.budget}, {st.doc.demand_label} ({department})", 180
            ),
            "fiscal_year": f"FY{year}",
            "context": {
                **({"secondary_document": "pages"} if actuals is not None else {}),
                "union_budget": spec,
            },
        }
    )
    return {
        "id": task_id,
        "title": title,
        "instructions": instructions,
        "dataset_key": task_id,
        "type": "run_check",
        "estimated_tokens": 2000,
        "estimate_basis": "About 2,000 agent tokens to run UB1 with the CLI and read the result; the PDFs are read in code.",
        "financial_quantity_type": "budget_and_treasury_expenditure",
        "payload": payload,
    }


# --------------------------------------------------------------------------------------
# 6. Dry run
# --------------------------------------------------------------------------------------

_PAGES: dict[str, dict[int, str]] = {}  # statement URL -> page texts, shared with forked workers


def _dry_run(task: dict[str, Any]) -> dict[str, Any]:
    payload = task["payload"]
    records = [{"page": p, "raw_text": t, "document": "primary"} for p, t in sorted(_PAGES[payload["retrieval_url"]].items())]
    if payload.get("secondary_url"):
        records += [
            {"page": p, "raw_text": t, "document": "secondary"} for p, t in sorted(_PAGES[payload["secondary_url"]].items())
        ]
    result = run_engine("UB1", records, context=_build_task_context(payload))
    top = max(result.leads, key=lambda lead: lead.monetary_exposure_inr, default=None)
    return {
        "id": task["id"],
        "lines": len(payload["context"]["union_budget"]["lines"]),
        "leads": len(result.leads),
        "cleared": result.cleared_count,
        "unevaluable": result.unevaluable_count,
        "audited_inr": result.monetary_audited_inr,
        "exposure_inr": sum(lead.monetary_exposure_inr for lead in result.leads),
        "top_exposure_inr": top.monetary_exposure_inr if top else 0.0,
        "rules": [lead.rule_triggered for lead in result.leads],
        "top": (
            {
                "title": top.title,
                "severity": top.severity,
                "metrics": top.anomaly_metrics,
                "summary": top.summary,
                "citations": top.citations,
            }
            if top
            else None
        ),
    }


def dry_run(tasks: list[dict[str, Any]], statements: list[Statement], workers: int) -> list[dict[str, Any]]:
    _PAGES.clear()
    _PAGES.update({st.url: st.pages for st in statements if st.pages})
    ctx = multiprocessing.get_context("fork")
    with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as pool:
        return list(pool.map(_dry_run, tasks, chunksize=50))


# --------------------------------------------------------------------------------------
# 7. Project file
# --------------------------------------------------------------------------------------


def project_document(tasks: list[dict[str, Any]], coverage: dict[str, Any]) -> dict[str, Any]:
    lines = coverage["lines"]
    with_actuals = coverage["lines_with_actuals"]
    statements = coverage["statements_used"]
    return {
        "slug": SLUG,
        "project_code": "P3",
        "priority": 5,
        "title": "Union Budget since 2014-15: money that was budgeted but not spent, line by line",
        "summary": (
            "Every year Parliament is asked to vote thousands of crores for each scheme, body and department. "
            f"We go through 12 financial years, 2014-15 to 2025-26: {lines:,} budget lines from {statements:,} "
            "official Notes on Demands for Grants statements. No investigative work: each check reads the budget "
            "estimate, the revised estimate and the actual expenditure that the Ministry of Finance itself printed, "
            f"and reports the part that was not spent. {len(tasks):,} checks, each verified by three independent agents."
        ),
        "posted_by": {"name": "AI Watchdogs India", "url": "https://aiwatchdogs.in/projects"},
        "pillar": "budget",
        "status": "active",
        "jurisdiction": "Union of India",
        "authority": (
            "Ministry of Finance (Union Budget) and the Union ministries and departments that hold each Demand for Grants"
        ),
        "scope": [
            "Every numbered line of section A of the Notes on Demands for Grants, for every Demand and Appropriation "
            "of the Union Government: schemes, autonomous bodies, establishment heads and transfers to States.",
            "The budget estimate and the revised estimate of a financial year, read from the Union Budget presented "
            "one year later, where both are printed on one row.",
            f"The actual expenditure of that year, read from the Union Budget presented two years later ({with_actuals:,} "
            "lines traced so far by printed name and by the figure both budgets print for the year in between).",
            "How much of each line's budget estimate was not spent, and how much of that was already dropped at the "
            "revised-estimate stage. A line is reported when 10% or more of its budget estimate, and at least "
            "Rs 1 crore, was not spent.",
            f"The {coverage['lines_alone']:,} largest lines (budget estimate of Rs {coverage['alone_from_crore']:,.2f} "
            f"crore or more) get a check each; smaller lines of one Demand and year share a check, up to {SET_SIZE} at a time.",
            "Only statements whose numbered lines add up to their printed Grand Total in all four periods are used.",
        ],
        "out_of_scope": [
            "Any individual officer, employee or beneficiary.",
            "Investigations, leaked or non-public records, RTI replies and anything behind a login.",
            "Why money was not spent: the check reports printed figures, and lists the ordinary reasons, not a cause.",
            "Money released to States and implementing agencies that stayed unspent with them. The Union's accounts "
            "count it as spent on release; it is listed below as planned work.",
            "Repayment of debt (the rolling over of treasury bills and loans), which is not money voted to be spent.",
            f"Actual expenditure for 2025-26, printed only in Union Budget 2027-28 (February 2027). Those "
            f"{coverage['by_year']['2025-26']['lines']:,} lines are checked against the revised estimate for now.",
            "Most 2015-16 lines after the revised-estimate stage: Union Budget 2017-18 dropped the Plan and Non-Plan "
            "split and regrouped the schemes, so their actuals cannot be traced by name.",
        ],
        "statutory_anchors": [
            "Article-112-114-Constitution",
            "Article-266-Constitution",
            "GFR-2017-R62",
            "FRBM-Act-2003",
        ],
        "sources": [
            *(
                {
                    "name": f"Union Budget {budget}: Notes on Demands for Grants (Statements of Budget Estimates)",
                    "publisher": "Ministry of Finance, Government of India",
                    "url": BUDGET_LANDING[budget],
                }
                for budget in BUDGET_BASE
            ),
            {
                "name": "Every statement used, with its URL and SHA-256",
                "publisher": "AI Watchdogs India, from indiabudget.gov.in",
                "url": f"https://aiwatchdogs.in/api/missions/{SLUG}/documents.csv",
            },
            {
                "name": "Appropriation Accounts of the Union Government (savings and surrenders by grant)",
                "publisher": "Controller General of Accounts, Ministry of Finance",
                "url": "https://cga.nic.in/",
            },
            {
                "name": "Reports on the Accounts of the Union Government (financial audit)",
                "publisher": "Comptroller and Auditor General of India",
                "url": "https://cag.gov.in/en/audit-report",
            },
        ],
        "methodology_code": "UB1",
        "tasks": tasks,
        "planned_tasks": [
            {
                "title": "Actual expenditure for 2025-26, once Union Budget 2027-28 prints it",
                "type": "run_check",
                "estimated_tokens": 3600000,
                "estimate_basis": "About 1,800 lines at 2,000 tokens each, re-run with the new statements as the second file.",
            },
            {
                "title": "Appropriation Accounts: savings and surrendered amounts for every grant, 2014-15 to 2024-25",
                "type": "extract_data",
                "estimated_tokens": 60000000,
                "estimate_basis": "About 1,100 grant-years of printed tables that need a reader of their own.",
            },
            {
                "title": "CAG reports on Union Government accounts: savings of Rs 100 crore or more, 2014-15 to 2024-25",
                "type": "extract_data",
                "estimated_tokens": 22000000,
                "estimate_basis": "11 reports, about 20 chapters and annexures on savings, surrenders and unspent provisions.",
            },
            {
                "title": "Money released to States and agencies but not used: Standing Committee reports on Demands for Grants",
                "type": "find_sources",
                "estimated_tokens": 90000000,
                "estimate_basis": "About 55 ministries and departments over 12 years; reports are prose, not tables.",
            },
            {
                "title": "2015-16 lines regrouped in Union Budget 2017-18: trace each to the line that carries its actuals",
                "type": "cross_check",
                "estimated_tokens": 40000000,
                "estimate_basis": f"About {coverage['by_year']['2015-16']['lines'] - coverage['by_year']['2015-16']['with_actuals']:,} lines whose names changed when Plan and Non-Plan were merged.",
            },
            {
                "title": "Statements the pinned PDF reader cannot open or that do not add up to their Grand Total",
                "type": "extract_data",
                "estimated_tokens": 6000000,
                "estimate_basis": f"{coverage['statements_unreadable']} damaged files and {coverage['statements_not_closed']} statements with a table the reader gets wrong.",
            },
        ],
    }


def dump_project(project: dict[str, Any]) -> str:
    """The project file: indented at the top, one task per line (ten thousand tasks, small diffs)."""
    marker = "@@TASKS@@"
    head = json.dumps({**project, "tasks": marker}, ensure_ascii=False, indent=2)
    tasks = ",\n".join("    " + json.dumps(task, ensure_ascii=False, separators=(",", ":")) for task in project["tasks"])
    return head.replace(json.dumps(marker), "[\n" + tasks + "\n  ]") + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--cache", required=True, type=Path, help="Folder holding <budget>/sbe<N>.pdf")
    parser.add_argument("--download", action="store_true", help="Fetch statements missing from the cache")
    parser.add_argument("--out", type=Path, default=REPO / "projects", help="The projects folder to write into")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--generated-on", default=date.today().isoformat(), help="Date recorded in coverage.json")
    parser.add_argument("--report", type=Path, help="Also write each task's dry-run result to this JSON file")
    args = parser.parse_args()

    statements = ensure_documents(args.cache, args.download)
    print(f"{len(statements)} statements in {args.cache}", flush=True)
    load_statements(statements, args.workers)
    by_budget: dict[str, dict[int, Statement]] = collections.defaultdict(dict)
    for st in statements:
        by_budget[st.budget][st.number] = st

    items: list[Item] = []
    left_out: collections.Counter[str] = collections.Counter()
    for start in range(FIRST_YEAR, LAST_YEAR + 1):
        items.extend(link_year(ub.financial_year(start), by_budget, left_out))
    groups, alone = plan(items)

    tasks: list[dict[str, Any]] = []
    set_counter: collections.Counter[tuple[str, int]] = collections.Counter()
    for members in groups:
        key = (members[0].year, members[0].estimates.number)
        if len(members) > 1:
            set_counter[key] += 1
        tasks.append(build_task(members, set_counter[key]))
    ids = [task["id"] for task in tasks]
    if len(set(ids)) != len(ids):
        raise SystemExit("Task ids are not unique.")

    results = dry_run(tasks, statements, args.workers)
    unreadable = [r for r in results if r["unevaluable"]]
    if unreadable:
        for r in unreadable[:20]:
            print("UNEVALUABLE", r, file=sys.stderr)
        raise SystemExit(f"{len(unreadable)} tasks list a line the engine cannot read; not writing the project.")
    if any(r["leads"] + r["cleared"] != r["lines"] for r in results):
        raise SystemExit("A task did not account for every listed line.")

    if args.report:
        args.report.write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")

    ranked = sorted(items, key=lambda it: it.rank)
    used = {it.estimates.url for it in items} | {it.actuals.url for it in items if it.actuals is not None}
    by_year: dict[str, dict[str, Any]] = {}
    for start in range(FIRST_YEAR, LAST_YEAR + 1):
        year = ub.financial_year(start)
        mine = [it for it in items if it.year == year]
        year_tasks = [t for t in tasks if t["payload"]["context"]["union_budget"]["financial_year"] == year]
        by_year[year] = {
            "lines": len(mine),
            "with_actuals": sum(1 for it in mine if it.act is not None),
            "tasks": len(year_tasks),
            "budget_estimate_crore": round(math.fsum(it.budget_estimate for it in mine), 2),
        }
    coverage = {
        "generated_on": args.generated_on,
        "financial_years": [ub.financial_year(y) for y in range(FIRST_YEAR, LAST_YEAR + 1)],
        "tasks": len(tasks),
        "lines": len(items),
        "lines_with_actuals": sum(1 for it in items if it.act is not None),
        "lines_alone": alone,
        "alone_from_crore": round(ranked[alone - 1].budget_estimate, 2) if alone else 0.0,
        "set_size": SET_SIZE,
        "links": dict(sorted(collections.Counter(it.link or "estimates_only" for it in items).items())),
        "lines_left_out": dict(sorted(left_out.items())),
        "rows_added_together": sum(1 for it in items if len(it.est.rows) > 1),
        "statements": len(statements),
        "statements_used": len(used),
        "statements_unreadable": sum(1 for st in statements if st.error),
        "statements_not_closed": sum(1 for st in statements if st.readable and not st.closed),
        "by_year": by_year,
        "dry_run": {
            "tasks_with_a_lead": sum(1 for r in results if r["leads"]),
            "tasks_cleared": sum(1 for r in results if not r["leads"]),
            "lines_below_threshold": sum(r["leads"] for r in results),
            "lines_within_threshold": sum(r["cleared"] for r in results),
        },
    }

    out_dir = args.out / SLUG
    out_dir.mkdir(parents=True, exist_ok=True)
    project = project_document(tasks, coverage)
    (args.out / f"{SLUG}.json").write_text(dump_project(project), encoding="utf-8")
    (out_dir / "coverage.json").write_text(json.dumps(coverage, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (out_dir / "documents.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(
            ["union_budget", "number", "kind", "ministry", "department", "url", "sha256", "bytes", "pages", "lines", "status"]
        )
        for st in statements:
            if st.error:
                status = f"not read ({st.error})"
            elif not st.closed:
                status = "lines do not add up to the Grand Total"
            else:
                status = "adds up to the Grand Total"
            doc = st.doc
            writer.writerow(
                [
                    st.budget,
                    st.number,
                    ("appropriation" if doc.appropriation else "demand") if doc else "",
                    ub.display_ministry(doc.ministry) if doc else "",
                    doc.department if doc else "",
                    st.url,
                    st.sha256,
                    st.size,
                    len(st.pages),
                    len(st.lines),
                    status,
                ]
            )
    print(json.dumps({k: v for k, v in coverage.items() if k != "by_year"}, indent=2))
    for year, row in by_year.items():
        print(year, row)
    exposure = math.fsum(r["exposure_inr"] for r in results) / 1e7
    top = math.fsum(r["top_exposure_inr"] for r in results) / 1e7
    print(f"dry run: Rs {exposure:,.0f} crore below estimate across all listed lines; Rs {top:,.0f} crore in the top lead of each task")


if __name__ == "__main__":
    main()
