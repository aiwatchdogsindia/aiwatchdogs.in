#!/usr/bin/env python3
"""Build Project P5 (`one-teacher-five-grades`, methodology `OT1`, 100 tasks) from official UDISE+ PDFs.

Downloads and verifies the 3 pinned Ministry of Education (DoSEL) UDISE+ Existing Structure
booklets (2025-26, 2024-25, 2023-24), parses Tables 2.2, 3.3, 4.3, and 5.2 for all States/UTs,
dry-runs every single one of the 100 tasks through `run_engine("OT1", ...)`, validates the
generated project JSON with `validate_project()`, and writes:
  - projects/one-teacher-five-grades.json
  - projects/one-teacher-five-grades/coverage.json
  - projects/one-teacher-five-grades/documents.csv
  - projects/one-teacher-five-grades/METHODOLOGY.md
"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from aiwatchdogs.connectors.loader import load_dataset  # noqa: E402
from aiwatchdogs.engines.one_teacher_five_grades import (  # noqa: E402
    ONE_TEACHER_FIVE_GRADES_METHODOLOGY,
    ONE_TEACHER_FIVE_GRADES_PROJECT_SLUG,
    canonical_state_key,
    extract_state_metrics_from_pages,
    extract_udise_table_rows,
)
from aiwatchdogs.engines.registry import run_engine  # noqa: E402
from aiwatchdogs.projects import validate_project  # noqa: E402

CACHE_DIR = Path.home() / ".cache" / "aiwatchdogs" / "one-teacher-five-grades"
PROJECTS_DIR = REPO_ROOT / "projects"
PROJECT_JSON_PATH = PROJECTS_DIR / f"{ONE_TEACHER_FIVE_GRADES_PROJECT_SLUG}.json"
PROJECT_ASSETS_DIR = PROJECTS_DIR / ONE_TEACHER_FIVE_GRADES_PROJECT_SLUG

OFFICIAL_PUBLISHER = (
    "Department of School Education & Literacy (DoSEL), Ministry of Education, Government of India"
)
SOURCE_LANDING_PAGE = "https://dashboard.udiseplus.gov.in/"

UT_KEYS = frozenset(
    {
        "andaman-nicobar-islands",
        "chandigarh",
        "dadra-nagar-haveli-daman-diu",
        "delhi",
        "jammu-kashmir",
        "ladakh",
        "lakshadweep",
        "puducherry",
    }
)

# Canonical clean display names independent of year-to-year "&" vs "and" formatting differences.
CANONICAL_STATE_DISPLAY: dict[str, str] = {
    "andaman-nicobar-islands": "Andaman & Nicobar Islands",
    "dadra-nagar-haveli-daman-diu": "Dadra & Nagar Haveli and Daman & Diu",
    "jammu-kashmir": "Jammu & Kashmir",
}

BOOKLETS: list[dict[str, Any]] = [
    {
        "academic_year": "2025-26",
        "title": "UDISE+ 2025-26 Booklet (Existing Structure)",
        "url": "https://dashboard.udiseplus.gov.in/report2026/static/media/UDISE+2025_26_Booklet_existing.edd7cd16d934a10377ba.pdf",
        "expected_sha256": "f20bfc4296acdb0f84a36f65587002c7899272ea67b6d91497a172a76982d9c4",
        "table_pages": {"table_2_2": 32, "table_3_3": 42, "table_4_3": 54, "table_5_2": 86},
        "include_uts": True,  # 28 States + 8 UTs = 36 tasks
    },
    {
        "academic_year": "2024-25",
        "title": "UDISE+ 2024-25 Booklet (Existing Structure)",
        "url": "https://dashboard.udiseplus.gov.in/report2026/static/media/UDISE+2024_25_Booklet_existing.118ba29d4773e6372f72.pdf",
        "expected_sha256": "cc66d03e1413722d2f5ab66f29ece3cdb3c21f7d736ac63f21efc3696ed7f4bf",
        "table_pages": {"table_2_2": 32, "table_3_3": 42, "table_4_3": 54, "table_5_2": 86},
        "include_uts": True,  # 28 States + 8 UTs = 36 tasks
    },
    {
        "academic_year": "2023-24",
        "title": "UDISE+ 2023-24 Booklet (Existing Structure)",
        "url": "https://dashboard.udiseplus.gov.in/report2026/static/media/UDISE+2023_24_Booklet_existing.309eabf9e4889db93bcb.pdf",
        "expected_sha256": "6a54fbf78e0ea7158aeeba568066a179aa133736e3bd9102c2c9e352bece71ac",
        "table_pages": {"table_2_2": 31, "table_3_3": 41, "table_4_3": 53, "table_5_2": 85},
        "include_uts": False,  # 28 States = 28 tasks -> 36 + 36 + 28 = 100 exact tasks
    },
]


def _ensure_cached_pdf(yr: str, url: str, expected_sha: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"udise_{yr}_existing.pdf"
    if path.exists():
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual == expected_sha:
            return path
    req = urllib.request.Request(url, headers={"User-Agent": "AIWatchdogs-India-OpenAudit/0.1"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected_sha:
        raise RuntimeError(f"SHA-256 mismatch for UDISE+ {yr}: got {actual}, expected {expected_sha}")
    path.write_bytes(data)
    return path


def _build_tasks_and_dry_run() -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    tasks: list[dict[str, Any]] = []
    documents_rows: list[dict[str, Any]] = []
    by_year: dict[str, Any] = {}
    national_baselines: dict[str, Any] = {}
    dry_run_leads: list[dict[str, Any]] = []
    dry_run_cleared = 0
    dry_run_unevaluable = 0
    states_2025_26_preview: list[dict[str, Any]] = []

    for spec in BOOKLETS:
        yr = str(spec["academic_year"])
        url = str(spec["url"])
        sha = str(spec["expected_sha256"])
        tpages = dict(spec["table_pages"])
        include_uts = bool(spec["include_uts"])

        pdf_path = _ensure_cached_pdf(yr, url, sha)
        loaded = load_dataset(
            str(pdf_path),
            expected_sha256=sha,
            allow_local_files=True,
        )
        pages = {
            int(r["page"]): str(r.get("raw_text") if r.get("raw_text") is not None else r.get("text") or "")
            for r in loaded.records
            if isinstance(r, dict)
            and not isinstance(r.get("page"), bool)
            and isinstance(r.get("page"), (int, float))
            and isinstance(r.get("raw_text") if r.get("raw_text") is not None else r.get("text"), str)
        }
        if not pages:
            raise RuntimeError(f"Failed to extract PDF pages for UDISE+ {yr}")

        # Verify national row baseline first
        india_m = extract_state_metrics_from_pages(
            pages,
            academic_year=yr,
            state_name="India",
            table_pages=tpages,
            state_key="india",
        )
        if india_m is None:
            raise RuntimeError(f"Failed to parse All-India baseline for UDISE+ {yr}")
        national_baselines[yr] = {
            "total_schools": india_m.total_schools,
            "total_enrolments": india_m.total_enrolments,
            "total_teachers": india_m.total_teachers,
            "overall_ptr": india_m.overall_ptr,
            "single_teacher_schools": india_m.single_teacher_schools,
            "single_teacher_enrolments": india_m.single_teacher_enrolments,
            "single_teacher_share_pct": india_m.single_teacher_share_pct,
            "students_per_single_teacher": india_m.students_per_single_teacher,
            "gov_total_schools": india_m.gov_total_schools,
            "gov_primary_1_5_schools": india_m.gov_primary_1_5_schools,
            "gov_primary_1_5_teachers": india_m.gov_primary_1_5_teachers,
            "gov_primary_teachers_per_school": india_m.gov_primary_teachers_per_school,
            "gov_primary_enrolments": india_m.gov_primary_enrolments,
        }

        t22_rows = extract_udise_table_rows(pages[tpages["table_2_2"]], expected_cols=10)
        year_state_keys = [
            k
            for k in t22_rows.keys()
            if k != "india" and (include_uts or k not in UT_KEYS)
        ]
        expected_count = 36 if include_uts else 28
        if len(year_state_keys) != expected_count:
            raise RuntimeError(
                f"Expected {expected_count} States/UTs for {yr}, got {len(year_state_keys)}: {year_state_keys}"
            )

        documents_rows.append(
            {
                "academic_year": yr,
                "document_title": spec["title"],
                "pages": len(pages),
                "table_2_2_page": tpages["table_2_2"],
                "table_3_3_page": tpages["table_3_3"],
                "table_4_3_page": tpages["table_4_3"],
                "table_5_2_page": tpages["table_5_2"],
                "tasks": len(year_state_keys),
                "all_india_single_teacher_schools": india_m.single_teacher_schools,
                "all_india_single_teacher_enrolments": india_m.single_teacher_enrolments,
                "gov_primary_1_5_schools": india_m.gov_primary_1_5_schools,
                "gov_primary_1_5_teachers": india_m.gov_primary_1_5_teachers,
                "sha256": sha,
                "url": url,
            }
        )

        yr_flagged = 0
        for skey in sorted(year_state_keys):
            printed_name, _ = t22_rows[skey]
            display_name = CANONICAL_STATE_DISPLAY.get(skey) or printed_name
            is_ut = skey in UT_KEYS
            tid = f"ot1-{yr}-{skey}"
            assert len(tid) <= 60, tid

            task_context = {
                "official_publisher": OFFICIAL_PUBLISHER,
                "source_landing_page": SOURCE_LANDING_PAGE,
                "retrieval_url": url,
                "expected_sha256": sha,
                "one_teacher_five_grades": {
                    "academic_year": yr,
                    "state_name": display_name,
                    "state_key": skey,
                    "is_ut": is_ut,
                    "source_document_title": spec["title"],
                    "table_pages": tpages,
                },
            }

            result = run_engine("OT1", loaded.records, task_context, as_of_date="2026-11-27")
            if result.unevaluable_count > 0:
                dry_run_unevaluable += result.unevaluable_count
                raise RuntimeError(f"Task {tid} evaluated as unevaluable: {result.summary}")
            dry_run_cleared += result.cleared_count
            if result.leads:
                yr_flagged += 1
                lead = result.leads[0]
                m = lead.anomaly_metrics
                lead_summary = {
                    "task_id": tid,
                    "academic_year": yr,
                    "state_name": display_name,
                    "state_key": skey,
                    "signal_category": m.get("signal_category"),
                    "rule_triggered": lead.rule_triggered,
                    "severity": lead.severity,
                    "single_teacher_schools": m.get("single_teacher_schools"),
                    "total_schools": m.get("total_schools"),
                    "single_teacher_share_pct": m.get("single_teacher_share_pct"),
                    "single_teacher_enrolments": m.get("single_teacher_enrolments"),
                    "students_per_single_teacher": m.get("students_per_single_teacher"),
                    "gov_primary_1_5_schools": m.get("gov_primary_1_5_schools"),
                    "gov_primary_1_5_teachers": m.get("gov_primary_1_5_teachers"),
                    "gov_primary_teachers_per_school": m.get("gov_primary_teachers_per_school"),
                }
                dry_run_leads.append(lead_summary)
                if yr == "2025-26":
                    states_2025_26_preview.append(lead_summary)

            tasks.append(
                {
                    "id": tid,
                    "title": f"{yr} · {display_name}: single-teacher & gov primary staffing check",
                    "instructions": (
                        f"Download the checksummed UDISE+ {yr} Existing Structure report PDF from "
                        f"dashboard.udiseplus.gov.in, extract {display_name} across Table 2.2 (p.{tpages['table_2_2']}), "
                        f"Table 3.3 Government schools (p.{tpages['table_3_3']}), Table 4.3 Government teachers "
                        f"(p.{tpages['table_4_3']}), and Table 5.2 Government enrolments (p.{tpages['table_5_2']}), "
                        f"and evaluate single-teacher concentration (>=10%), students per single-teacher school (>30:1 RTE norm), "
                        f"and Government Primary (Classes I-V) average teachers per school (<2.0 RTE norm)."
                    ),
                    "type": "run_check",
                    "methodology_code": ONE_TEACHER_FIVE_GRADES_METHODOLOGY,
                    "dataset_key": f"udise-{yr}-{skey}",
                    "financial_quantity_type": "school_staffing_and_enrolment",
                    "estimated_tokens": 12000,
                    "estimate_basis": (
                        "Deterministic PDF extraction over 4 pinned UDISE+ tables per State/UT (zero LLM inference needed in rule-engine mode)."
                    ),
                    "payload": {
                        "retrieval_url": url,
                        "expected_sha256": sha,
                        "official_publisher": OFFICIAL_PUBLISHER,
                        "source_landing_page": SOURCE_LANDING_PAGE,
                        "context": task_context,
                    },
                }
            )

        by_year[yr] = {
            "tasks": len(year_state_keys),
            "flagged_in_dry_run": yr_flagged,
            "cleared_in_dry_run": len(year_state_keys) - yr_flagged,
            "all_india": national_baselines[yr],
        }

    states_2025_26_preview.sort(
        key=lambda x: (-float(x["single_teacher_share_pct"] or 0.0), -int(x["single_teacher_schools"] or 0))
    )

    coverage = {
        "project_slug": ONE_TEACHER_FIVE_GRADES_PROJECT_SLUG,
        "methodology_code": ONE_TEACHER_FIVE_GRADES_METHODOLOGY,
        "generated_on": datetime.now(timezone.utc).date().isoformat(),
        "academic_years": sorted(by_year.keys()),
        "total_tasks": len(tasks),
        "states_covered_2025_26": 36,
        "national_baseline_2025_26": national_baselines["2025-26"],
        "by_year": by_year,
        "dry_run_summary": {
            "tasks_tested": len(tasks),
            "leads": len(dry_run_leads),
            "cleared": dry_run_cleared,
            "unevaluable": dry_run_unevaluable,
        },
        "top_2025_26_concentrations": states_2025_26_preview[:15],
        "documents": documents_rows,
    }
    return tasks, coverage, documents_rows


def _write_methodology_md() -> None:
    content = """# Methodology `OT1` — One Teacher, Five Grades (`P5`)

## Objective
Map where **single-teacher schools** and **under-staffed Government Primary (Classes I–V) schools** are concentrated across India's States and Union Territories using official Ministry of Education **UDISE+** annual reports (`2023-24`, `2024-25`, and `2025-26`).

## Official Primary Sources (Zero Auth, Zero CAPTCHA, SHA-256 Pinned)
All three source booklets are published directly by the Department of School Education & Literacy (DoSEL), Ministry of Education at `https://dashboard.udiseplus.gov.in/`:
- **UDISE+ 2025-26 Booklet (Existing Structure)** — all 28 States + 8 UTs (`36` tasks)
- **UDISE+ 2024-25 Booklet (Existing Structure)** — all 28 States + 8 UTs (`36` tasks)
- **UDISE+ 2023-24 Booklet (Existing Structure)** — all 28 States (`28` tasks)

Total: **`100` deterministic tasks** across three consecutive academic cycles (`100,843` single-teacher schools and `29,12,147` enrolled pupils nationwide in 2025-26).

## Four Tables Cross-Checked per Task
1. **Table 2.2** (*State wise highlights: Schools, Enrolments and Teachers*) — Total Schools, Enrolments, Teachers, overall PTR, **Schools with Single Teachers**, and **Enrolments in Single Teacher Schools**.
2. **Table 3.3** (*Number of schools by management: Government*) — Government Total Schools and Government Primary (Classes I–V) schools.
3. **Table 4.3** (*Number of teachers by management: Government*) — Government Total Teachers and Government Primary (Classes I–V) teachers.
4. **Table 5.2** (*Enrolment of students by school management: Government*) — Government Total and Primary-level student enrolments.

## Deterministic Rules (`OT1`)
1. **High Single-Teacher Concentration (`single_teacher_share_ge_10pct`)**: `single_teacher_schools / total_schools >= 10.0%` AND `single_teacher_schools >= 100`.
2. **Severe Single-Teacher Overcrowding (`students_per_single_teacher_gt_30`)**: `single_teacher_enrolments / single_teacher_schools > 30.0` (exceeding the RTE Act 2009 Schedule `30:1` Primary PTR norm) AND `single_teacher_schools >= 500`.
3. **Under-Staffed Government Primary I–V Schools (`gov_primary_avg_teachers_lt_2`)**: `gov_primary_1_5_teachers / gov_primary_1_5_schools < 2.0` (below the RTE Act 2009 statutory 2-teacher minimum for Primary schools) AND `gov_primary_1_5_schools >= 100`.

States and UTs below all thresholds are published as **Cleared** with equal standing and full extracted figures.

## Transparent Methodological Boundaries
- In UDISE+ national booklets, **Table 2.2** reports single-teacher school counts across **all school managements combined**, whereas **Tables 3.3, 4.3, and 5.2** isolate **Government schools** specifically (Government Primary I–V schools account for 612,066 of all 722,498 Primary I–V schools nationally in 2025-26). Every `OT1` output explicitly publishes both sets of figures without conflating them.
- District-level and school-level (`11-digit UDISE code`) concentrations are listed in `planned_tasks` because UDISE+ public booklets publish State/UT matrices while bulk school/district CSV extracts require authenticated access on `udiseplus.gov.in`.
"""
    PROJECT_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    (PROJECT_ASSETS_DIR / "METHODOLOGY.md").write_text(content, encoding="utf-8")


def main() -> None:
    tasks, coverage, doc_rows = _build_tasks_and_dry_run()
    assert len(tasks) == 100, f"Expected 100 tasks, built {len(tasks)}"

    project_obj: dict[str, Any] = {
        "slug": ONE_TEACHER_FIVE_GRADES_PROJECT_SLUG,
        "project_code": "P5",
        "priority": 5,
        "title": "One Teacher, Five Grades: Mapping where single-teacher schools and primary staffing gaps are concentrated",
        "summary": (
            "Maps single-teacher schools and Government Primary (Classes I–V) staffing gaps across all 36 "
            "States & UTs over three consecutive Ministry of Education UDISE+ cycles (2023-24, 2024-25, 2025-26). "
            "100 deterministic tasks cross-check UDISE+ Table 2.2 (single-teacher counts & enrolments) against "
            "Government-only Tables 3.3, 4.3, and 5.2 against RTE Act 2009 Schedule norms (minimum 2 teachers per "
            "primary school and 30:1 PTR). Every figure is verified by two independent agents."
        ),
        "posted_by": {
            "name": "AI Watchdogs India",
            "handle": "@aiwatchdogs",
            "url": "https://aiwatchdogs.in",
        },
        "pillar": "scheme",
        "status": "active",
        "methodology_code": ONE_TEACHER_FIVE_GRADES_METHODOLOGY,
        "jurisdiction": "All India: all 28 States and 8 Union Territories (UDISE+ 2023-24 to 2025-26)",
        "authority": "Department of School Education & Literacy (DoSEL), Ministry of Education, Government of India, and State/UT School Education Departments",
        "statutory_anchors": [
            "Constitution-Art-21A",
            "RTE-Act-2009-Sec19-Schedule",
            "UDISE-Plus-MoE",
            "Samagra-Shiksha-Framework",
        ],
        "scope": [
            "All 36 States & Union Territories in UDISE+ 2025-26 (36 tasks) and UDISE+ 2024-25 (36 tasks), plus all 28 States in UDISE+ 2023-24 (28 tasks) — 100 tasks total",
            "Single-teacher school counts, state share (%), enrolled pupils, and pupils per single-teacher school from UDISE+ Table 2.2",
            "Government Primary (Classes I–V) school counts (Table 3.3), Government Primary teachers (Table 4.3), average teachers per Government Primary school vs the RTE Act 2009 minimum of 2 teachers, and Government Primary PTR (Table 5.2)",
            "Three-year state concentration comparison showing where single-teacher schools declined vs persisted across 2023-24, 2024-25, and 2025-26",
        ],
        "out_of_scope": [
            "Individual school names, 11-digit UDISE codes, teacher names, or student data (DPDP Act 2023)",
            "Private-only or aided-only single-teacher breakdowns: UDISE+ national booklets publish Table 2.2 single-teacher totals across all managements combined, while isolating Government schools in Tables 3.3, 4.3, and 5.2",
            "District- and block-level micro-data exports requiring institutional credentials on data.udiseplus.gov.in (listed below as planned work)",
        ],
        "sources": [
            {
                "name": "UDISE+ 2025-26 Report Booklet (Existing Structure) — Tables 2.2, 3.3, 4.3, 5.2",
                "publisher": OFFICIAL_PUBLISHER,
                "url": BOOKLETS[0]["url"],
            },
            {
                "name": "UDISE+ 2024-25 Report Booklet (Existing Structure) — Tables 2.2, 3.3, 4.3, 5.2",
                "publisher": OFFICIAL_PUBLISHER,
                "url": BOOKLETS[1]["url"],
            },
            {
                "name": "UDISE+ 2023-24 Report Booklet (Existing Structure) — Tables 2.2, 3.3, 4.3, 5.2",
                "publisher": OFFICIAL_PUBLISHER,
                "url": BOOKLETS[2]["url"],
            },
            {
                "name": "UDISE+ Official Dashboard & Reports Portal",
                "publisher": "Ministry of Education, Government of India (dashboard.udiseplus.gov.in)",
                "url": SOURCE_LANDING_PAGE,
            },
        ],
        "planned_tasks": [
            {
                "title": "District-wise single-teacher government school mapping from public UDISE+ state releases or Samagra Shiksha PAB minutes",
                "type": "extract_data",
                "estimated_tokens": 18000,
                "estimate_basis": "Requires district matrix tables not published in the national UDISE+ PDF booklets",
            },
            {
                "title": "Parliamentary unstarred-question annexures (Lok Sabha 1436 / 4920 / 5815) cross-checking Government-only single-teacher counts",
                "type": "cross_check",
                "estimated_tokens": 14000,
                "estimate_basis": "Requires static PDF links or public mirrors for sansad.in annexures",
            },
        ],
        "tasks": tasks,
    }

    errs = validate_project(project_obj, filename_stem=ONE_TEACHER_FIVE_GRADES_PROJECT_SLUG)
    if errs:
        raise RuntimeError(f"Generated project JSON failed validation: {errs[:10]}")

    PROJECT_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    PROJECT_JSON_PATH.write_text(json.dumps(project_obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (PROJECT_ASSETS_DIR / "coverage.json").write_text(
        json.dumps(coverage, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    with (PROJECT_ASSETS_DIR / "documents.csv").open("w", encoding="utf-8", newline="") as f:
        fieldnames = list(doc_rows[0].keys())
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(doc_rows)
    _write_methodology_md()

    print(f"Built {PROJECT_JSON_PATH} with {len(tasks)} tasks.")
    print(f"Dry-run summary: {json.dumps(coverage['dry_run_summary'])}")
    print("Top 2025-26 concentrations:")
    for row in coverage["top_2025_26_concentrations"][:10]:
        print(
            f"  - {row['state_name']}: {row['single_teacher_schools']:,}/{row['total_schools']:,} "
            f"({row['single_teacher_share_pct']:.2f}%), {row['single_teacher_enrolments']:,} students "
            f"({row['students_per_single_teacher']:.1f}/school), Gov Primary avg={row['gov_primary_teachers_per_school']}"
        )


if __name__ == "__main__":
    main()
