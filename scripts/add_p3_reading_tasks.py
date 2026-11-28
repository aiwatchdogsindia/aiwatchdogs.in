#!/usr/bin/env python3
"""Generate and pin the 11 live R1 AI document-reading tasks for P3 (Union Budget).

Reads ``projects/union-budget-unspent-funds/documents.csv``, verifies the exact SHA-256 and
printed 12-column Grand Total of all 11 complete statements whose tables the deterministic regex
parser could not close, and upserts them as live claimable ``methodology_code="R1"`` tasks inside
``projects/union-budget-unspent-funds.json``.

The 6 truncated 2015-16 PDFs on indiabudget.gov.in (Demands 13, 15, 18, 20, 31, 64 — cut off
mid-table on page 2) remain honestly parked in ``planned_tasks`` as ``find_sources``.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import re
from pathlib import Path
from typing import Any

import httpx
from pypdf import PdfReader

from aiwatchdogs.engines.reading_verifier import _check_horizontal_triples
from aiwatchdogs.projects import validate_project

logging.getLogger("pypdf").setLevel(logging.CRITICAL)

REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECT_JSON = REPO_ROOT / "projects" / "union-budget-unspent-funds.json"
DOCS_CSV = REPO_ROOT / "projects" / "union-budget-unspent-funds" / "documents.csv"

_NUM_RE = re.compile(r"^(?:\.\.\.|-?\d[\d,]*\.\d+)$")
_PERIOD_RE = re.compile(
    r"Actual\s+(\d{4}-\d{4})\W*\s+Budget\s+(\d{4}-\d{4})\W*\s+Revised\s+(\d{4}-\d{4})\W*\s+Budget\s+(\d{4}-\d{4})"
)

PLAN_COL_KEYS = (
    "actual_plan",
    "actual_non_plan",
    "actual_total",
    "be_prev_plan",
    "be_prev_non_plan",
    "be_prev_total",
    "re_prev_plan",
    "re_prev_non_plan",
    "re_prev_total",
    "be_plan",
    "be_non_plan",
    "be_total",
)
REV_COL_KEYS = (
    "actual_revenue",
    "actual_capital",
    "actual_total",
    "be_prev_revenue",
    "be_prev_capital",
    "be_prev_total",
    "re_prev_revenue",
    "re_prev_capital",
    "re_prev_total",
    "be_revenue",
    "be_capital",
    "be_total",
)


def _to_val(tok: str) -> float:
    return 0.0 if tok == "..." else float(tok.replace(",", ""))


def _source_landing_for_budget(budget: str) -> str:
    if budget == "2015-16":
        return "https://www.indiabudget.gov.in/budget2015-2016/ub2015-16/eb/stat2.htm"
    if budget == "2016-17":
        return "https://www.indiabudget.gov.in/budget2016-2017/ub2016-17/eb/stat2.htm"
    return f"https://www.indiabudget.gov.in/budget{budget}/doc/eb/stat2.htm"


def _inspect_statement(row: dict[str, str]) -> dict[str, Any]:
    url = row["url"].strip()
    expected_sha = row["sha256"].strip().lower()
    budget = row["union_budget"].strip()
    demand_no = int(row["number"])
    ministry = row["ministry"].strip()
    department = row["department"].strip()

    resp = httpx.get(url, timeout=30.0, follow_redirects=True)
    resp.raise_for_status()
    actual_sha = hashlib.sha256(resp.content).hexdigest()
    if actual_sha != expected_sha:
        raise RuntimeError(f"SHA-256 mismatch for {budget} D{demand_no}: expected {expected_sha}, got {actual_sha}")

    try:
        reader = PdfReader(io.BytesIO(resp.content))
    except Exception:
        reader = PdfReader(io.BytesIO(resp.content), strict=False)
    page_count = len(reader.pages)
    texts = [(p.extract_text() or "").replace("\ufffd", " ") for p in reader.pages]

    periods: list[str] = []
    subcolumns: list[str] = []
    for ln in texts[0].splitlines():
        clean = " ".join(ln.split())
        m = _PERIOD_RE.search(clean)
        if m and not periods:
            periods = [
                f"Actual {m.group(1)}",
                f"Budget {m.group(2)}",
                f"Revised {m.group(3)}",
                f"Budget {m.group(4)}",
            ]
        if not subcolumns:
            if "Revenue" in clean and "Capital" in clean and "Total" in clean:
                subcolumns = ["Revenue", "Capital", "Total"]
            elif "Plan" in clean and "Non-Plan" in clean and "Total" in clean:
                subcolumns = ["Plan", "Non-Plan", "Total"]

    if not periods or not subcolumns:
        raise RuntimeError(f"Could not parse periods/subcolumns for {budget} D{demand_no}")

    gt_page: int | None = None
    gt_vals: list[float] | None = None
    for pno, txt in enumerate(texts, start=1):
        for ln in txt.splitlines():
            clean = " ".join(ln.split())
            if re.match(r"^Grand\s+Total\b", clean, re.I):
                tokens = clean.split()
                nums = tokens[-12:]
                if len(nums) == 12 and all(_NUM_RE.match(t) for t in nums):
                    gt_page = pno
                    gt_vals = [_to_val(t) for t in nums]
                    break
        if gt_vals is not None:
            break

    if gt_page is None or gt_vals is None:
        raise RuntimeError(f"Could not extract 12-column Grand Total for {budget} D{demand_no}")

    col_keys = REV_COL_KEYS if subcolumns[0] == "Revenue" else PLAN_COL_KEYS
    expected_gt = {k: round(v, 2) for k, v in zip(col_keys, gt_vals, strict=True)}
    checked, failed, errs = _check_horizontal_triples(
        expected_gt, label_prefix=f"{budget} D{demand_no}", abs_tol=0.05, rel_tol=0.001
    )
    if checked != 4 or failed != 0:
        raise RuntimeError(f"Grand Total horizontal check failed for {budget} D{demand_no}: {errs}")

    est_tokens = max(12_000, gt_page * 6_000)
    short_dept = department or ministry or f"Demand No. {demand_no}"
    title = f"AI read · {budget} Demand {demand_no}: {short_dept} (p.1–{gt_page}, ₹{expected_gt['be_total']:,.2f} Cr)"
    if len(title) > 150:
        title = title[:147].rstrip() + "..."

    return {
        "id": f"r1-ub-{budget}-d{demand_no:03d}",
        "title": title,
        "instructions": (
            f"Download the pinned Union Budget {budget} Demand No. {demand_no} ({short_dept}) PDF, "
            f"verify SHA-256 {expected_sha[:12]}..., and extract every non-overlapping leaf row of Section A "
            f"(pages 1–{gt_page}) into structured JSON with verbatim page quotes so all 12 columns "
            f"({', '.join(subcolumns)} across {', '.join(periods)}) sum to the official printed Grand Total "
            f"(BE Total ₹{expected_gt['be_total']:,.2f} crore)."
        ),
        "type": "extract_data",
        "methodology_code": "R1",
        "dataset_key": f"ub-reading-{budget}-d{demand_no:03d}",
        "financial_quantity_type": "budget_and_treasury_expenditure",
        "estimated_tokens": est_tokens,
        "estimate_basis": f"AI extraction of {gt_page} printed Section A table pages (~6,000 tokens/page) with ₹0 Grand Total verification.",
        "payload": {
            "artifact_title": f"Union Budget {budget} Demand No. {demand_no} ({short_dept})",
            "retrieval_url": url,
            "expected_sha256": expected_sha,
            "official_publisher": "Ministry of Finance, Government of India (Union Budget)",
            "source_landing_page": _source_landing_for_budget(budget),
            "context": {
                "reading": {
                    "document_type": "union_budget_sbe",
                    "union_budget": budget,
                    "demand_no": demand_no,
                    "ministry": ministry,
                    "department": short_dept,
                    "page_count": page_count,
                    "table_pages": [1, gt_page],
                    "periods": periods,
                    "subcolumns": subcolumns,
                    "expected_grand_total": expected_gt,
                }
            },
        },
    }


def main() -> None:
    with DOCS_CSV.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    unclosed = [r for r in rows if r["status"] == "lines do not add up to the Grand Total"]
    if len(unclosed) != 11:
        raise RuntimeError(f"Expected 11 non-closed statements in documents.csv, found {len(unclosed)}")

    r1_tasks: list[dict[str, Any]] = []
    total_be_cr = 0.0
    for row in unclosed:
        t = _inspect_statement(row)
        r1_tasks.append(t)
        be_tot = t["payload"]["context"]["reading"]["expected_grand_total"]["be_total"]
        total_be_cr += be_tot
        print(
            f"  + {t['id']:22s} | p1-{t['payload']['context']['reading']['table_pages'][1]:>2}/{t['payload']['context']['reading']['page_count']:>2} "
            f"| BE Total = ₹{be_tot:>10,.2f} Cr | {t['payload']['context']['reading']['department']}"
        )

    project = json.loads(PROJECT_JSON.read_text(encoding="utf-8"))
    base_tasks = [t for t in project.get("tasks", []) if str(t.get("methodology_code") or "UB1") != "R1"]
    project["tasks"] = base_tasks + r1_tasks

    # Update the single planned_tasks entry so it accurately reflects ONLY the 6 truncated 2015-16 PDFs.
    new_planned: list[dict[str, Any]] = []
    for pt in project.get("planned_tasks", []):
        title = str(pt.get("title") or "")
        if "cannot open" in title or "do not add up" in title or "truncated 2015-16" in title:
            new_planned.append(
                {
                    "title": "Find complete official copies of 6 truncated 2015-16 budget PDFs (Demands 13, 15, 18, 20, 31, 64)",
                    "type": "find_sources",
                    "estimated_tokens": 2_000_000,
                    "estimate_basis": "indiabudget.gov.in hosts 2-page truncated files cut off mid-table; the 11 complete non-closed statements are now live R1 reading tasks.",
                }
            )
        else:
            new_planned.append(pt)
    project["planned_tasks"] = new_planned

    errors = validate_project(project, filename_stem="union-budget-unspent-funds")
    if errors:
        raise RuntimeError("Project validation failed:\n" + "\n".join(errors[:20]))

    marker = "@@TASKS@@"
    head = json.dumps({**project, "tasks": marker}, ensure_ascii=False, indent=2)
    task_lines = ",\n".join(
        "    " + json.dumps(task, ensure_ascii=False, separators=(",", ":"))
        for task in project["tasks"]
    )
    compact_text = head.replace(json.dumps(marker), "[\n" + task_lines + "\n  ]") + "\n"
    PROJECT_JSON.write_text(compact_text, encoding="utf-8")
    print(
        f"\nUpdated {PROJECT_JSON.name}: {len(base_tasks):,} UB1 + {len(r1_tasks)} R1 = "
        f"{len(project['tasks']):,} live tasks (₹{total_be_cr:,.2f} Cr BE unlocked for AI reading)."
    )


if __name__ == "__main__":
    main()
