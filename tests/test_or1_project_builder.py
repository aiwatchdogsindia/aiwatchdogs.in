"""Offline regression tests for OR1 project reporting."""

import importlib.util
import json
import sys
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest

from aiwatchdogs.hub import desk
from aiwatchdogs.hub.database import Database
from aiwatchdogs.hub.sse import SSEBroker

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_one_road_two_bills_project.py"
_SPEC = importlib.util.spec_from_file_location(_SCRIPT.stem, _SCRIPT)
assert _SPEC and _SPEC.loader
builder = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = builder
_SPEC.loader.exec_module(builder)


def test_generated_methodology_matches_checked_in_or1_rules():
    project_dir = _SCRIPT.parents[1] / "projects" / builder.SLUG
    coverage = json.loads((project_dir / "coverage.json").read_text(encoding="utf-8"))
    assert builder.render_methodology_md(coverage) == (project_dir / "METHODOLOGY.md").read_text(encoding="utf-8")


@pytest.fixture
def project_input(tmp_path):
    doc = builder.ReportDoc(
        cag_id="121126",
        listing_year=2024,
        report_year="2024",
        report_no="Report No. 12 of 2024",
        title="Example CAG Audit Report",
        department="",
        sector="Commercial",
        tabled_date="",
        sent_date="",
        landing_url="https://cag.gov.in/en/audit-report/details/121126",
        pdf_url="https://cag.gov.in/example.pdf",
        fallback_pdf_url="",
        cache_path=tmp_path / "example.pdf",
        sha256="a" * 64,
        pages={
            1: "Name of the CPSE Audit Observation in brief Amount recovered\n"
               "Double payment made to Hospital in respect of medical bill 3.61",
            2: "The department completed the road project within the approved budget and timetable.",
            3: "The contractor was paid twice for the same road work, resulting in payment of Rs. 2 lakh.",
            4: "The contractor was paid twice for the same road work. The report lists no figure.",
        },
    )
    tasks = builder.pack_tasks(builder.extract_all_blocks([doc]), target_tasks=10)
    assert len(tasks) == 4
    return tasks, {doc.cag_id: doc}


def test_dry_run_counts_attribution_holds_separately(project_input):
    tasks, docs = project_input
    tasks[3]["payload"]["context"]["one_road_two_bills"]["thresholds"]["min_amount_inr"] = 1

    dry = builder.dry_run_all(tasks, docs)

    assert dry["tasks_cleared"] == dry["tasks_with_leads"] == dry["total_leads"] == 1
    assert dry["tasks_with_attribution_holds"] == dry["total_unevaluable"] == 2
    assert dry["attribution_holds_by_reason"] == {
        "ambiguous_audit_table_attribution": 1,
        "unattributed_amount": 1,
    }


def test_dry_run_keeps_leads_from_a_task_with_a_hold(project_input):
    tasks, docs = project_input
    held_spec = tasks[0]["payload"]["context"]["one_road_two_bills"]
    lead_spec = tasks[2]["payload"]["context"]["one_road_two_bills"]
    held_spec["windows"].extend(lead_spec["windows"])

    dry = builder.dry_run_all(tasks[:1], docs)

    assert dry["tasks_cleared"] == 0
    assert dry["tasks_with_attribution_holds"] == dry["tasks_with_leads"] == 1
    assert dry["total_leads"] == dry["total_unevaluable"] == 1


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"key": ""}, "windows\\[1\\]\\.key"),
        ({"page": 99}, "page 99 is missing from the PDF"),
        ({"line_start": 99, "line_end": 100}, "lines 99\\.\\.100 are out of range"),
    ],
)
def test_dry_run_rejects_invalid_windows_even_with_a_hold(project_input, changes, error):
    tasks, docs = project_input
    held_spec = tasks[0]["payload"]["context"]["one_road_two_bills"]
    clear_spec = tasks[1]["payload"]["context"]["one_road_two_bills"]
    held_spec["windows"].append({**clear_spec["windows"][0], **changes})

    with pytest.raises(SystemExit, match=error):
        builder.dry_run_all(tasks[:1], docs)


@pytest.mark.parametrize("summary_header", ["Report No. 17 of 2025\nxi", "Executive Summary"])
def test_dry_run_counts_linked_report_exposure_once(project_input, summary_header):
    _, docs = project_input
    observation = "The contractor was paid twice for the same road work, resulting in payment of Rs. 7.53 crore."
    report = replace(
        next(iter(docs.values())),
        listing_year=2025,
        report_year="2025",
        report_no="Report No. 17 of 2025",
        pages={
            3: "The department was paid twice for the same bridge work, resulting in payment of Rs. 7.53 crore.",
            14: f"{summary_header}\n{observation}\n(Para 6.3.1.2)",
            80: (
                "6.3.1.2 Duplicate payment for road works\n"
                "Eastern Power Limited made an irregular payment for road works.\n"
                f"{observation}"
            ),
        },
    )
    tasks = builder.pack_tasks(builder.extract_all_blocks([report]), target_tasks=10)

    dry = builder.dry_run_all(tasks, {report.cag_id: report})

    assert dry["tasks_with_leads"] == dry["total_leads"] == 3
    assert dry["by_signal"] == {"duplicate_payment_or_billing": 3}
    assert [lead["amount_inr"] for lead in dry["sample_leads"]] == [75_300_000] * 3
    assert dry["total_flagged_inr_cr"] == 15.06


def test_dashboard_counts_linked_report_exposure_once_by_year(tmp_path, monkeypatch):
    monkeypatch.setattr(desk, "_project_coverage", lambda slug: {"report_years": ["2024", "2025"]})
    mission_id = "or1-project"
    slug = builder.SLUG
    key = "a" * 64
    summary = {"exposure_reference_key": key, "exposure_reference_kind": "summary", "page": 14}
    detail = {"exposure_reference_key": key, "exposure_reference_kind": "detail", "page": 80}
    findings = [
        ("summary", "FY2025", "peer_review", 0, summary, "OR1-DUPLICATE-BILLING"),
        ("detail", "FY2025", "verified", 0, detail, "OR1-DUPLICATE-BILLING"),
        ("unlinked", "FY2025", "raw_lead", 0, {"exposure_reference_kind": "detail"}, "OR1-DUAL-AGENCY"),
        ("legacy", "FY2024", "verified", 0, [], "OR1-DUPLICATE-BILLING"),
        ("quarantined", "FY2025", "peer_review", 1, detail, "OR1-DUPLICATE-BILLING"),
        ("retracted", "FY2025", "retracted_false_positive", 0, {}, "OR1-DUPLICATE-BILLING"),
    ]

    with closing(Database(tmp_path / "or1.sqlite")) as db, db.transaction() as conn:
        conn.execute(
            "INSERT INTO missions (id, slug, title, description, pillar, methodology_code, "
            "jurisdiction, ministry_or_authority, created_at) "
            "VALUES (?, ?, 'OR1 project', '', 'audit', 'OR1', 'Union of India', 'CAG', '2025-01-01')",
            (mission_id, slug),
        )
        for finding_id, year, status, quarantined, metrics, rule in findings:
            conn.execute(
                "INSERT INTO findings (id, mission_id, mission_slug, title, pillar, summary, methodology_code, "
                "entity_name, jurisdiction, fiscal_year, monetary_exposure_inr, anomaly_metrics_json, "
                "rule_triggered, status, quarantined, submitted_by, created_at) "
                "VALUES (?, ?, ?, ?, 'audit', '', 'OR1', 'Example entity', 'Union of India', "
                "?, ?, ?, ?, ?, ?, 'test', '2025-01-01')",
                (
                    finding_id, mission_id, slug, finding_id, year, 75_300_000,
                    json.dumps(metrics), rule, status, quarantined,
                ),
            )
        mission = conn.execute("SELECT * FROM missions WHERE id = ?", (mission_id,)).fetchone()
        result = desk.DeskService(db, SSEBroker())._one_road_two_bills_summary(conn, mission_row=mission)

    assert result["flagged_inr_cr"] == 22.59
    assert result["leads"] == 4
    assert result["verified"] == result["peer_review"] == 2
    assert result["by_signal"] == {"duplicate_payment_or_billing": 3, "dual_agency_or_scheme_overlap": 1}
    assert {row["year"]: (row["leads"], row["flagged_inr_cr"]) for row in result["years"]} == {
        "2024": (1, 7.53),
        "2025": (3, 15.06),
    }
    assert {row["finding_id"]: row["flagged_inr_cr"] for row in result["top_leads"]} == {
        "summary": 7.53,
        "detail": 7.53,
        "unlinked": 7.53,
        "legacy": 7.53,
    }
