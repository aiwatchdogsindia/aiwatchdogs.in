"""Tests for Project P4 (one-road-two-bills) and deterministic engine OR1."""

from __future__ import annotations

import csv
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from aiwatchdogs.cli.config import AgentConfig
from aiwatchdogs.cli.runner import AgentRunner
from aiwatchdogs.core.models import LoadedDataset
from aiwatchdogs.core.safety import compute_sha256, lint_neutrality
from aiwatchdogs.engines import one_road_two_bills as or1
from aiwatchdogs.engines.registry import run_engine
from aiwatchdogs.hub.app import create_app
from aiwatchdogs.projects import (
    ONE_ROAD_TWO_BILLS_METHODOLOGY,
    ONE_ROAD_TWO_BILLS_PROJECT_SLUG,
    validate_project,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECTS_DIR = REPO_ROOT / "projects"


def _minimal_pdf_bytes(text: str) -> bytes:
    """Create a minimal valid single-page PDF containing ``text`` readable by pypdf."""
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    lines = escaped.splitlines()
    stream_cmds = ["BT", "/F1 11 Tf", "50 750 Td", "14 TL"]
    for i, line in enumerate(lines):
        if i == 0:
            stream_cmds.append(f"({line}) Tj")
        else:
            stream_cmds.append(f"T* ({line}) Tj")
    stream_cmds.append("ET")
    stream = "\n".join(stream_cmds).encode("latin-1", "replace")

    objs: list[bytes] = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n",
        f"4 0 obj\n<< /Length {len(stream)} >>\nstream\n".encode("ascii") + stream + b"\nendstream\nendobj\n",
        b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
    ]
    buf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objs:
        offsets.append(len(buf))
        buf.extend(obj)
    xref_pos = len(buf)
    buf.extend(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for off in offsets[1:]:
        buf.extend(f"{off:010d} 00000 n \n".encode("ascii"))
    buf.extend(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode("ascii"))
    return bytes(buf)


def _or1_ctx(page_text: str, *, page: int = 12, year: str = "2025") -> tuple[list[dict[str, Any]], dict[str, Any]]:
    lines = page_text.splitlines()
    records = [{"page": page, "raw_text": page_text}]
    ctx = {
        "retrieval_url": "https://d1jmc0u14z2tmp.cloudfront.net/uploads/download_audit_report/2025/test.pdf",
        "expected_sha256": "a" * 64,
        "source_landing_page": "https://cag.gov.in/en/audit-report/details/999999",
        "jurisdiction": "Union of India",
        "one_road_two_bills": {
            "report": {
                "cag_id": "999999",
                "report_no": f"Report No. 17 of {year}",
                "year": year,
                "title": "Performance Audit Report on Rural Works, Union Government",
                "sector": "Social Infrastructure",
            },
            "windows": [
                {
                    "key": f"r999999-p{page:03d}-b00",
                    "page": page,
                    "block_index": 0,
                    "line_start": 0,
                    "line_end": len(lines),
                }
            ],
            "thresholds": {"min_amount_inr": 0},
        },
    }
    return records, ctx


def test_or1_flags_duplicate_billing_and_cites_amount() -> None:
    page_text = (
        "6.3.1.2 Duplicate claim for rural electrification connections\n"
        "Despite getting the required grant under DDUGJY, grant of Rs. 7.53 crore from REC "
        "was also claimed under SAUBHAGYA, resulting in double payment to DISCOMs for the same work."
    )
    records, ctx = _or1_ctx(page_text, page=42)
    res = run_engine(ONE_ROAD_TWO_BILLS_METHODOLOGY, records, context=ctx)
    assert len(res.leads) == 1
    assert res.cleared_count == 0
    assert res.unevaluable_count == 0
    lead = res.leads[0]
    assert lead.methodology_code == "OR1"
    assert lead.rule_triggered == "OR1-DUPLICATE-BILLING"
    assert lead.fiscal_year == "FY2025"
    assert lead.monetary_exposure_inr == pytest.approx(75_300_000.0)
    assert lead.severity == "high"
    assert lead.anomaly_metrics["signal_category"] == "duplicate_payment_or_billing"
    assert lead.anomaly_metrics["page"] == 42
    assert lead.anomaly_metrics["para_number"] == "6.3.1.2"
    assert lead.citations[0]["page"] == 42
    assert lead.benign_explanations


def test_or1_flags_dual_agency_overlap_with_irregularity() -> None:
    page_text = (
        "4.2.1 Overlapping road construction across two agencies\n"
        "Audit scrutiny revealed that a 12 km stretch of rural road already constructed by CPWD "
        "was sanctioned and billed again under another agency (NHIDCL), resulting in irregular "
        "and avoidable expenditure of Rs. 4.20 crore."
    )
    records, ctx = _or1_ctx(page_text, page=18)
    res = run_engine(ONE_ROAD_TWO_BILLS_METHODOLOGY, records, context=ctx)
    assert len(res.leads) == 1
    lead = res.leads[0]
    assert lead.rule_triggered == "OR1-DUAL-AGENCY-OVERLAP"
    assert lead.anomaly_metrics["signal_category"] == "dual_agency_or_scheme_overlap"
    assert lead.monetary_exposure_inr == pytest.approx(42_000_000.0)


def _cag_report_12_ctx(page_text: str, *, page: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records, ctx = _or1_ctx(page_text, page=page, year="2024")
    ctx.update(
        retrieval_url=(
            "https://d1jmc0u14z2tmp.cloudfront.net/uploads/download_audit_report/2024/"
            "Final-Report-No.12-English(05.11.2024)-signed-067615b308aac13.57331340.pdf"
        ),
        expected_sha256="9e2856efa2e2272f709195da91ea0005515d7b5dd278403e6b5c6e6ec9311c8e",
        source_landing_page="https://cag.gov.in/en/audit-report/details/121126",
    )
    spec = ctx["one_road_two_bills"]
    spec["report"].update(
        cag_id="121126",
        report_no="Report No. 12 of 2024",
        title="Compliance Audit Report (Commercial) for the year ended 31 March 2022 - Union Government",
        sector="Commercial",
    )
    spec["windows"][0]["key"] = f"r121126-p{page:03d}-b00"
    return records, ctx


def test_or1_table_attribution_does_not_cross_flattened_rows() -> None:
    # PDF p.168 (printed p.122). The window starts after the table header and earlier rows.
    preamble = (
        "Report No. 12 of 2024\n122\nAnnexure-X\n(Referred to in Para 9.1)\n"
        "Recoveries at the instance of Audit\n(₹ in lakh)\n"
        "Name of the\nMinistry/\nDepartment\n"
        "Name of the CPSE Audit Observation in brief Amount recovered by\n"
        "the Management in F.Y.\n2021-22\n"
        "Department of\nFinancial Services\nThe Oriental Insurance\nCompany Limited\n"
        "Excess payment of electricity charges to the lessor due to\n"
        "non-adherence of the lease agreement\n1.30\n\n"
    )
    triggering_quote = "Double payment made to Hospital in respect of medical bill"
    window_text = (
        "Department of\nFinancial Services\nThe New India\nAssurance Company\nLimited\n"
        "Excess payment of claim under\nContractors Plant and Machinery Policy\n1.75\n"
        "Petroleum and\nNatural Gas\nOil and Natural Gas\nCorporation Limited\n"
        "Recovery of HRA paid to non-entitled employees 11.00\n"
        "Road Transport and\nHighways\nNational Highways\nAuthority of India\n"
        "Non-recovery of penalty under fee collection contracts of\npublic funded Plazas\n12.72\n"
        "Steel  Steel Authority of India\nLimited\n"
        "Non-recovery from the agency against converter refractory 304.16\n"
        f"Steel Steel Authority of India\nLimited\n{triggering_quote} 3.61\n"
        "Total 382.37 lakh\nSay 3.82 crore"
    )
    records, ctx = _cag_report_12_ctx(preamble + window_text, page=168)
    window = ctx["one_road_two_bills"]["windows"][0]
    window.update(key="r121126-p168-b01", block_index=1, line_start=len(preamble.splitlines()))

    res = run_engine(ONE_ROAD_TWO_BILLS_METHODOLOGY, records, context=ctx)

    assert res.leads == []
    assert res.cleared_count == 0
    assert res.unevaluable_count == 1


@pytest.mark.parametrize(
    "continuation",
    ["none", "labeled", "contd", "compact-contd", "dash-contd", "captioned-contd", "dash-captioned-contd", "split-with-blank",
     "implicit", "third-labeled", "third-implicit"],
)
def test_or1_flattened_recovery_table_requires_review(continuation: str) -> None:
    header = "Annexure X\nRecoveries at the instance of Audit\n(₹ in lakh)\nAudited entity Audit finding Amount recovered\n"
    continued = "Annexure X (continued)\n"
    affected_row = "Example Steel Corporation Limited\nDouble payment made on a hospital bill 3.61\n"
    other_row = "Example Power Corporation Limited\nExcess payment for work under a road contract 11.00\n"
    # The marked page can have a single row. The unmarked pages need the shapes of multiple rows.
    page_rows = other_row + affected_row
    if continuation == "none":
        current_page = header + page_rows
    elif continuation == "contd":
        current_page = "Annexure-X (Contd.)\n" + affected_row
    elif continuation == "compact-contd":
        current_page = "Annexure-X(Contd.)\n" + affected_row
    elif continuation == "dash-contd":
        current_page = "Annexure-X–Contd.\n" + affected_row
    elif continuation == "captioned-contd":
        current_page = "Annexure X: Recoveries at the instance of Audit\n(Contd.)\n" + affected_row
    elif continuation == "dash-captioned-contd":
        current_page = "Annexure X – Recoveries at the instance of Audit\n(Contd.)\n" + affected_row
    elif continuation == "split-with-blank":
        current_page = "Annexure X\n\n(Contd.)\n" + affected_row
    elif continuation.endswith("labeled"):
        current_page = continued + affected_row
    else:
        current_page = page_rows
    records, ctx = _or1_ctx(current_page)
    if continuation.startswith("third-"):
        records[:0] = [
            {"page": 10, "raw_text": header + "Example Oil Corporation Limited 12.72\n"},
            {"page": 11, "raw_text": current_page},
        ]
    elif continuation != "none":
        records.insert(0, {"page": 11, "raw_text": header + "Example Oil Corporation Limited 12.72\n"})

    res = run_engine(ONE_ROAD_TWO_BILLS_METHODOLOGY, records, context=ctx)

    assert res.leads == []
    assert res.cleared_count == 0
    assert res.unevaluable_count == 1


@pytest.mark.parametrize("boundary_page", [11, 12], ids=["prior-page", "same-page"])
def test_or1_table_context_stops_at_next_section(boundary_page: int) -> None:
    table = (
        "Annexure X (continued)\n"
        "Audited entity Audit finding Amount recovered\n"
        "Example Oil Corporation Limited 12.72\n"
        "Example Power Corporation Limited 11.00\n"
    )
    heading = "6.2 Review of grant payments\n"
    evidence = "Eastern Power Limited paid the same invoice twice."
    prose = f"{evidence}\n\nThe amount under the prior contract was 11.00\nThe new contract amount was 12.72"
    records, ctx = _or1_ctx(table + heading + prose if boundary_page == 12 else prose)
    if boundary_page == 11:
        records.insert(0, {"page": 11, "raw_text": table + heading})

    result = run_engine(ONE_ROAD_TWO_BILLS_METHODOLOGY, records, context=ctx)

    lead, = result.leads
    assert result.unevaluable_count == 0
    assert lead.suggested_authority == "Eastern Power Limited"
    assert lead.citations[0]["excerpt"] == evidence


@pytest.mark.parametrize("prefix", ["Chapter IV: Compliance Audit", "Table X"])
@pytest.mark.parametrize("pagination", ["", "123\n"])
@pytest.mark.parametrize("marker", ["continued", "Contd."])
def test_or1_repeated_page_heading_preserves_a_table_continuation(prefix: str, pagination: str, marker: str) -> None:
    row = "Double payment made on a hospital bill for Rs 3.61 lakh."
    records, ctx = _or1_ctx(f"{prefix}\n{pagination}Table X ({marker})\n{row}")
    records.insert(0, {"page": 11, "raw_text": "Table X\nAudited entity Audit finding Amount recovered\n"})

    result = run_engine(ONE_ROAD_TWO_BILLS_METHODOLOGY, records, context=ctx)

    assert result.leads == []
    assert result.unevaluable_count == 1


@pytest.mark.parametrize("continuation", ["", "Annexure X\n(Contd.)\n"], ids=["implicit-single-row", "split-label"])
@pytest.mark.parametrize("chapter", ["Chapter IV: Compliance Audit", "Chapter IV"])
def test_or1_holds_last_row_of_a_paginated_audit_table(continuation: str, chapter: str) -> None:
    records, ctx = _or1_ctx(
        f"{chapter}\n123\n{continuation}Example Roads Limited\n"
        "Double payment on a road contract for Rs 3.61 lakh 3.61"
    )
    records.insert(0, {"page": 11, "raw_text": (
        "Chapter IV: Compliance Audit\n122\nAnnexure X\nAudited entity Audit finding Amount recovered\n"
        "Example Oil Limited 12.72"
    )})

    result = run_engine(ONE_ROAD_TWO_BILLS_METHODOLOGY, records, context=ctx)

    assert result.leads == []
    assert result.unevaluable_count == 1
    assert result.cleared_count == 0


@pytest.mark.parametrize(
    ("prefix", "prior_prefix", "held"),
    [
        ("Chapter IV: Compliance Audit\n123\n", "Chapter IV: Compliance Audit\n122\n", True),
        ("Chapter IV: Compliance Audit\n", "Chapter IV: Compliance Audit\n122\n", True),
        ("Chapter IV: Compliance Audit\n", "", True),
        ("Chapter V: Other observations\n123\n", "Chapter IV: Compliance Audit\n122\n", False),
        ("Chapter IV: Findings on highway grants\n123\n", "Chapter IV: Compliance Audit\n122\n", False),
        ("Chapter IV: Compliance Audit\n123\n6.2 Review of grant payments\n", "", False),
        ("Chapter IV: Compliance Audit\n123\nAnnexure Y\n", "", False),
    ],
    ids=["paginated-header", "running-header", "no-prior-chapter", "new-chapter", "new-chapter-title",
         "new-subsection", "new-table"],
)
def test_or1_implicit_table_continuation_respects_section_boundaries(prefix: str, prior_prefix: str, held: bool) -> None:
    evidence = "Eastern Power Limited paid the same invoice twice for the road work at Rs 2 crore."
    records, ctx = _or1_ctx(f"{prefix}{evidence}\n\nExample Oil Corporation Limited 12.72\n11.00")
    records.insert(0, {"page": 11, "raw_text": (
        f"{prior_prefix}Annexure X\n"
        "Audited entity Audit finding Amount recovered\nExample Steel Corporation Limited 3.61\n"
    )})

    result = run_engine(ONE_ROAD_TWO_BILLS_METHODOLOGY, records, context=ctx)

    if held:
        assert result.leads == []
        assert result.unevaluable_count == 1
    else:
        lead, = result.leads
        assert result.unevaluable_count == 0
        assert lead.citations[0]["excerpt"] == evidence


@pytest.mark.parametrize("earlier, later_title", [
    ("Table X", "Table Y (continued)"),
    ("Annexure IV-A", "Annexure IV-B (continued)"),
    ("Annexure 6.1(A)", "Annexure 6.1(B) (continued)"),
    ("Annexure X", "Annexure Y(Contd.)"),
    ("Annexure X", "Annexure-Y–Contd."),
])
def test_or1_table_context_does_not_link_different_continuation_labels(earlier: str, later_title: str) -> None:
    evidence = "Eastern Power Limited paid the same invoice twice."
    records, ctx = _or1_ctx(f"{later_title}\n\n{evidence}")
    records.insert(0, {
        "page": 11,
        "raw_text": f"{earlier}\nAudited entity Audit finding Amount recovered\nExample Oil Corporation Limited 12.72",
    })

    result = run_engine(ONE_ROAD_TWO_BILLS_METHODOLOGY, records, context=ctx)

    lead, = result.leads
    assert result.unevaluable_count == 0
    assert lead.citations[0]["excerpt"] == evidence


def test_or1_implicit_table_continuation_requires_row_evidence_in_the_window() -> None:
    evidence = "Eastern Power Limited paid the same invoice twice."
    records, ctx = _or1_ctx(f"{evidence}\n\nThe observation notes an index of 11.00\nThe revised index is 12.72")
    ctx["one_road_two_bills"]["windows"][0]["line_end"] = 1
    records.insert(0, {"page": 11, "raw_text": "Audited entity Audit finding Amount recovered\n"})

    result = run_engine(ONE_ROAD_TWO_BILLS_METHODOLOGY, records, context=ctx)

    lead, = result.leads
    assert result.unevaluable_count == 0
    assert lead.suggested_authority == "Eastern Power Limited"


def test_or1_runner_releases_partial_audit_with_held_attribution(monkeypatch: pytest.MonkeyPatch) -> None:
    records, context = _or1_ctx(
        "Audited entity Audit finding Amount recovered\n"
        "A road contract was paid once.\n"
        "A double payment was made on a hospital bill 3.61"
    )
    first_window = context["one_road_two_bills"]["windows"][0]
    context["one_road_two_bills"]["windows"] = [
        {**first_window, "line_start": 1, "line_end": 2},
        {**first_window, "key": "r999999-p012-b01", "block_index": 1, "line_start": 2, "line_end": 3},
    ]
    dataset = LoadedDataset(
        records=records, sha256=context["expected_sha256"], byte_size=100,
        format="pdf", source_ref=context["retrieval_url"],
    )
    releases = []
    with AgentRunner(AgentConfig(handle="or1-test", hub_url="http://127.0.0.1:8000", provider="rule-engine")) as runner:
        monkeypatch.setattr(runner, "_load_task_dataset", lambda *args, **kwargs: dataset)
        monkeypatch.setattr(runner, "heartbeat", lambda *args, **kwargs: True)
        monkeypatch.setattr(runner, "release", lambda lease_id, **kwargs: releases.append(kwargs["reason"]))
        monkeypatch.setattr(runner, "submit", lambda *args, **kwargs: pytest.fail("OR1 published a partial clearance"))
        result = runner.execute_task({"lease_id": "or1-test-lease", "task": {
            "id": "partial-or1", "methodology_code": "OR1", "payload": {**context, "context": context},
        }})

    assert result["outcome"] == "released"
    assert releases == ["unevaluable_source"]


@pytest.mark.parametrize("include_auditee", [True, False], ids=["named-on-prior-page", "report-fallback"])
def test_or1_prose_attribution_keeps_quote_without_borrowing_subject_or_amount(include_auditee: bool) -> None:
    # PDF p.99 (printed p.65), continuing the NMDC observation from p.98.
    triggering_quote = (
        "Hence,\npayment of additional incentive/reward under Production Profit Index Memento\n"
        "Scheme on the ground of higher production/dispatch and annual reward tantamount\n"
        "to payment of incentive/reward to the Executive twice for the same reason."
    )
    page_text = (
        "Report No. 12 of 2024\n65\n"
        "Enterprises to their employees over and above the entitlement under Payment of\n"
        "Bonus Act or the Executive Instructions issued by Department of Public Enterprises\n"
        "in respect of ex-gratia unless the amount was authorised under a duly approved\n"
        "incentive scheme in accordance with the prescribed procedure.\n"
        f"{triggering_quote}\n"
        "It was noticed that the Scheme brought in 2017 did not have approval\n"
        "from the Ministry of Steel/Board of Directors.\n"
        # An unrelated sentence based on the neighboring observation in section 6.3.
        "A separate audit observation recorded expenditure of ₹27.29 crore under a farewell scheme."
    )
    records, ctx = _cag_report_12_ctx(page_text, page=99)
    preceding_page = (
        "Report No. 12 of 2024\n\n64\n"
        "6.2 Irregular payment of incentives/rewards to Executives in deviation to guidelines\n"
        "of Department of Public Enterprises\n"
    )
    if include_auditee:
        preceding_page += (
            "NMDC Limited made an irregular payment of ₹13.49 crore to its Executives during\n"
            "2018-19 to 2021-22 as incentive/reward for achieving higher\n"
            "production/productivity which was over and above the payment of Performance\n"
            "Related Pay as well as Perks and Allowances at the prescribed ceiling."
        )
    records.insert(0, {"page": 98, "raw_text": preceding_page})

    res = run_engine(ONE_ROAD_TWO_BILLS_METHODOLOGY, records, context=ctx)

    assert len(res.leads) == 1
    assert res.cleared_count == res.unevaluable_count == 0
    lead = res.leads[0]
    citation = lead.citations[0]
    assert " ".join(triggering_quote.split()) in " ".join(citation["excerpt"].split())
    assert citation["source_url"] == ctx["retrieval_url"]
    assert citation["source_landing_page"] == ctx["source_landing_page"]
    assert citation["sha256"] == ctx["expected_sha256"]
    assert citation["report_no"] == "Report No. 12 of 2024"
    assert citation["page"] == 99
    assert citation["para_number"] == "6.2"

    subject = lead.entity_name.casefold()
    unrelated_agencies = [
        name for name in ("department of public enterprises", "ministry of steel") if name in subject
    ]
    assert (unrelated_agencies, lead.monetary_exposure_inr, lead.anomaly_metrics["amount_disclosed"]) == (
        [], 0, False
    )
    if include_auditee:
        assert "nmdc" in subject
    else:
        assert "nmdc" not in subject
        assert any(label in subject for label in ("6.2", "report no. 12 of 2024", "compliance audit report"))


@pytest.mark.parametrize(
    ("evidence", "expected_amount"),
    [
        ("Example Power Corporation Limited paid the same invoice twice for ₹2.25 crore.", 22_500_000.0),
        ("A duplicate payment on a hospital bill was reported, with ₹3.61 lakh recovered.", 0.0),
        ("Two invoices for ₹3.61 lakh and ₹5 lakh were billed twice by the agency.", 0.0),
        ("The road contract was valued at Rs 5 crore and one invoice was paid twice.", 0.0),
        ("One invoice was paid twice and the road contract was valued at Rs 5 crore.", 0.0),
        ("The road contract was valued at Rs 5 crore and one invoice of Rs 2.50 crore was paid twice.", 25_000_000.0),
        ("The same invoice was paid twice and the resulting overpayment was Rs 2.50 crore.", 25_000_000.0),
        ("The same invoice was paid twice for the road work and the overpayment was Rs 2.50 crore.", 25_000_000.0),
        ("The same invoice was paid twice for the road work, and the overpayment was Rs 2.50 crore.", 25_000_000.0),
        ("The same invoice was paid twice for the road work and the invoice value was Rs 2.50 crore.", 0.0),
    ],
    ids=["one-exposure", "recovery-only", "competing-amounts", "prior-contract-value", "following-contract-value",
         "contract-value-and-duplicate-amount", "linked-overpayment", "explicit-overpayment", "comma-overpayment",
         "invoice-face-value"],
)
def test_or1_attribution_requires_one_exposure_amount(evidence: str, expected_amount: float) -> None:
    records, ctx = _or1_ctx(evidence)
    res = run_engine(ONE_ROAD_TWO_BILLS_METHODOLOGY, records, context=ctx)

    lead, = res.leads
    assert lead.citations[0]["excerpt"] == evidence
    assert lead.monetary_exposure_inr == expected_amount
    assert lead.anomaly_metrics["amount_disclosed"] is bool(expected_amount)


def _cag_report_17_ctx(page_text: str, *, page: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records, ctx = _or1_ctx(page_text, page=page)
    ctx.update(
        retrieval_url=(
            "https://d1jmc0u14z2tmp.cloudfront.net/uploads/download_audit_report/2025/"
            "REPORT-NO-17--of-2025-_English-06944e9f0974b28.79742615.pdf"
        ),
        expected_sha256="63ddcc64f80e2f15bc3107c1a2f04fd3d0b23344bfb665314fe77bc47adb07f6",
        source_landing_page="https://cag.gov.in/en/audit-report/details/123660",
    )
    spec = ctx["one_road_two_bills"]
    spec["report"].update(
        cag_id="123660",
        title="Performance Audit Report on DDUGJY and SAUBHAGYA, Union Government",
    )
    spec["windows"][0]["key"] = f"r123660-p{page:03d}-b00"
    return records, ctx


@pytest.mark.parametrize(
    ("page", "prefix", "unit", "suffix", "expected_crore"),
    [
        (
            14,
            "Connections under DDUGJY as well as in SAUBHAGYA led to duplicate claim of same work "
            "by the contractor. ",
            "crore",
            "",
            "7.53",
        ),
        (
            80,
            "6.3.1.2 Claiming of double benefit\n",
            "crore87\n131",
            "\n\nMoP stated (November 2024) that UP and MP both have remitted the recovery of "
            "grant portion of ₹10.37 crore and ₹0.03 crore respectively to GoI towards the amount "
            "claimed against duplicate connections.",
            "7.53",
        ),
        (80, "", "crore9abc", "", "0"),
    ],
    ids=["p14-later-explicit-exposure", "p80-footnote-and-recovery", "malformed-footnote"],
)
def test_or1_attributes_explicit_grant_exposure(
    page: int, prefix: str, unit: str, suffix: str, expected_crore: str,
) -> None:
    # Short extracts and a malformed variant from report 17/2025, tasks -0014 and -0120.
    evidence = (
        f"Despite getting the required grant under DDUGJY, grant of ₹7.53 {unit} from REC "
        "was also claimed under SAUBHAGYA, resulting in double payment to DISCOMs."
    )
    records, ctx = _cag_report_17_ctx(prefix + evidence + suffix, page=page)

    res = run_engine(ONE_ROAD_TWO_BILLS_METHODOLOGY, records, context=ctx)

    lead, = res.leads
    expected_inr = Decimal(expected_crore) * 10_000_000
    assert lead.monetary_exposure_inr == res.monetary_audited_inr == expected_inr
    assert lead.anomaly_metrics["amount_disclosed"] is bool(expected_inr)
    citation = lead.citations[0]
    assert citation["page"] == page
    assert citation["source_url"] == ctx["retrieval_url"]
    assert citation["sha256"] == ctx["expected_sha256"]
    if expected_inr:
        excerpt = " ".join(citation["excerpt"].split())
        assert "₹7.53 crore" in excerpt
        assert "resulting in double payment to DISCOMs" in excerpt
        assert "10.37" not in excerpt


@pytest.mark.parametrize("detail_payer", [False, True], ids=["same-attribution", "payer-in-detail-opener"])
@pytest.mark.parametrize("item_marker", ["", "i\n"], ids=["plain-detail", "standalone-item-marker"])
@pytest.mark.parametrize("summary_heading", ["", "Executive Summary\n2.1 Findings on duplicate payment\n"])
def test_or1_links_repeated_summary_and_detail_without_losing_either_citation(
    detail_payer: bool, item_marker: str, summary_heading: str,
) -> None:
    quote = (
        "Despite getting the required grant under DDUGJY, grant of ₹7.53 crore from REC "
        "was also claimed under SAUBHAGYA, resulting in double payment to DISCOMs."
    )
    summary = (
        f"Report No. 17 of 2025\nxi\n{summary_heading}{quote}\n\n"
        "Recommendation:\nMoP may reconcile connections. The Ministry should have a register of road payments.\n"
        "(Para 6.3.1.2)"
    )
    opener = "Eastern Power Limited made an irregular payment for these works.\n" if detail_payer else ""
    detail = (
        "Report No. 17 of 2025\n80\n6.3.1.2 Claiming of double benefit\n"
        + item_marker
        + opener
        + quote.replace("crore", "crore87\n131")
    )
    records, ctx = _cag_report_17_ctx(summary, page=14)
    records.append({"page": 80, "raw_text": detail})
    windows = ctx["one_road_two_bills"]["windows"]
    windows[0]["line_end"] = 3 + len(summary_heading.splitlines())
    windows.append({
        **windows[0], "key": "r123660-p080-b00", "page": 80, "line_end": len(detail.splitlines()),
    })

    result = run_engine(ONE_ROAD_TWO_BILLS_METHODOLOGY, records, context=ctx)

    summary_lead, detail_lead = sorted(result.leads, key=lambda lead: lead.citations[0]["page"])
    assert [summary_lead.citations[0]["page"], detail_lead.citations[0]["page"]] == [14, 80]
    assert summary_lead.anomaly_metrics["exposure_reference_kind"] == "summary"
    assert detail_lead.anomaly_metrics["exposure_reference_kind"] == "detail"
    assert detail_lead.citations[0]["excerpt"].startswith("Despite getting the required grant")
    if detail_payer:
        assert detail_lead.entity_name.startswith("Eastern Power Limited")
        assert not summary_lead.entity_name.startswith("Eastern Power Limited")
    assert summary_lead.anomaly_metrics["exposure_reference_key"] == detail_lead.anomaly_metrics["exposure_reference_key"]
    assert summary_lead.monetary_exposure_inr == detail_lead.monetary_exposure_inr == 75_300_000
    assert result.monetary_audited_inr == 75_300_000


@pytest.mark.parametrize(
    "next_observation",
    [
        "\nThe Ministry reported delays in the feeder replacement scheme.",
        "\n\nThe Ministry reported delays in the feeder replacement scheme.",
        "\nThe Ministry reported that funds may arrive after the scheduled works.",
        "\nThe Ministry may have issued another duplicate payment for the bridge work.",
    ],
    ids=["next-line", "new-paragraph", "reported-possibility", "possible-past-payment"],
)
def test_or1_summary_reference_stops_at_the_next_observation(next_observation: str) -> None:
    quote = "Audit noticed that the same road invoice was paid twice for Rs 1 crore."
    summary = (
        f"Report No. 17 of 2025\nxi\n{quote}\n\nRecommendation:\nMoP may reconcile road invoices."
        f"{next_observation}\n(Para 6.3.1.2)"
    )
    detail = f"6.3.1.2 Duplicate payment\n{quote}"
    records, ctx = _cag_report_17_ctx(summary, page=14)
    records.append({"page": 80, "raw_text": detail})
    windows = ctx["one_road_two_bills"]["windows"]
    windows[0]["line_end"] = 3
    windows.append({**windows[0], "key": "r123660-p080-b00", "page": 80, "line_end": 2})

    result = run_engine(ONE_ROAD_TWO_BILLS_METHODOLOGY, records, context=ctx)

    summary_lead, detail_lead = sorted(result.leads, key=lambda lead: lead.citations[0]["page"])
    assert summary_lead.citations[0]["excerpt"] == detail_lead.citations[0]["excerpt"] == quote
    assert "exposure_reference_key" not in summary_lead.anomaly_metrics
    assert detail_lead.anomaly_metrics["exposure_reference_kind"] == "detail"
    assert result.monetary_audited_inr == 20_000_000


@pytest.mark.parametrize("prefix", ["", "80\n"], ids=["detail-heading", "arabic-pagination"])
@pytest.mark.parametrize("code", ["CMD", "MD", "DM", "DC", "ID", "MID", "CIVIC", "iiii", "i"])
def test_or1_preserves_non_pagination_in_wrapped_exposure_evidence(code: str, prefix: str) -> None:
    quote = f"The road invoice with code {code} was paid twice for Rs 2 crore."
    summary = f"Report No. 17 of 2025\nxi\n{quote}\n(Para 6.2)"
    detail = f"{prefix}6.2 Duplicate payment\nThe road invoice with code\n{code}\nwas paid twice for Rs 2 crore."
    records, ctx = _or1_ctx(summary, page=4)
    records.append({"page": 12, "raw_text": detail})
    windows = ctx["one_road_two_bills"]["windows"]
    windows.append({**windows[0], "key": "r999999-p012-b00", "page": 12, "line_end": len(detail.splitlines())})

    result = run_engine(ONE_ROAD_TWO_BILLS_METHODOLOGY, records, context=ctx)

    summary_lead, detail_lead = sorted(result.leads, key=lambda lead: lead.citations[0]["page"])
    assert summary_lead.citations[0]["excerpt"] == detail_lead.citations[0]["excerpt"] == quote
    assert summary_lead.anomaly_metrics["exposure_reference_kind"] == "summary"
    assert detail_lead.anomaly_metrics["exposure_reference_kind"] == "detail"
    assert summary_lead.anomaly_metrics["exposure_reference_key"] == detail_lead.anomaly_metrics["exposure_reference_key"]
    assert result.monetary_audited_inr == 20_000_000


@pytest.mark.parametrize(
    ("page", "ending"),
    [(15, "were also noticed in audit."), (81, "were also noticed (Annexure-8).")],
    ids=["p15-executive-summary", "p81-annexure-reference"],
)
def test_or1_leaves_mixed_payment_total_unattributed(page: int, ending: str) -> None:
    # Report 17/2025, tasks -0016 and -0122. Neither assigned window gives a duplicate-only subtotal.
    evidence = (
        "Instances of extending undue benefits to the contractors, viz., making payment without "
        f"ensuring completion of work, and double payment for same work, amounting ₹2.24 crore {ending}"
    )
    records, ctx = _cag_report_17_ctx(evidence, page=page)

    res = run_engine(ONE_ROAD_TWO_BILLS_METHODOLOGY, records, context=ctx)

    lead, = res.leads
    assert lead.monetary_exposure_inr == res.monetary_audited_inr == 0
    assert lead.anomaly_metrics["amount_disclosed"] is False
    assert lead.citations[0]["page"] == page
    assert "double payment for same work" in lead.citations[0]["excerpt"]


@pytest.mark.parametrize("conjunction", ["Although", "While"])
def test_or1_leaves_amount_in_unrelated_leading_clause_unattributed(conjunction: str) -> None:
    evidence = (
        f"{conjunction} the upgrade cost ₹4.20 crore, audit observed that the same invoice "
        "for meter installation was paid twice."
    )
    records, ctx = _or1_ctx(evidence)

    res = run_engine(ONE_ROAD_TWO_BILLS_METHODOLOGY, records, context=ctx)

    lead, = res.leads
    assert lead.monetary_exposure_inr == res.monetary_audited_inr == 0
    assert lead.anomaly_metrics["amount_disclosed"] is False
    assert "was paid twice" in lead.citations[0]["excerpt"]


@pytest.mark.parametrize(
    ("evidence", "payer"),
    [
        ("In the sample, audit observed that Eastern Power Limited paid the same invoice twice.", "Eastern Power Limited"),
        ("The same invoice was paid twice by NTPC.", "NTPC"),
        ("The same invoice was paid twice by Eastern Power Limited.", "Eastern Power Limited"),
        ("The same invoice was paid twice to NTPC.", "Western Power Limited"),
        ("The same invoice was paid twice by the regional office.", None),
        ("The same invoice was paid twice by NEFT.", None),
        ("The same invoice was paid twice by MARCH 2024.", None),
        ("The same invoice was paid twice by NTPC and NHPC.", None),
        ("The same invoice was paid twice to the contractor by NTPC.", "NTPC"),
        ("A double payment was made by the regional office for the road works.", None),
        ("The same invoice was paid twice by NTPC and its contractor.", None),
        ("The same invoice was paid twice by NTPC, NHPC and REC.", None),
        ("The same invoice was paid twice by Eastern Power Limited and Western Power Limited.", None),
        ("Eastern Power Limited and Western Power Limited paid the same invoice twice.", None),
        ("The same invoice was paid twice by NTPC, resulting in exposure of Rs 2 crore.", "NTPC"),
        ("The same invoice was paid twice by Oil and Natural Gas Corporation Limited.", "Oil and Natural Gas Corporation Limited"),
        ("The same invoice was paid twice by NTPC and the regional office.", None),
        ("Audit noticed duplicate payment by NTPC of Rs 2.50 crore for the same road work.", "NTPC"),
        ("Audit noticed duplicate payment to the contractor by NTPC for the same road work.", "NTPC"),
        ("A duplicate payment of Rs 2.50 crore was made by NTPC for the same road work.", "NTPC"),
        ("Audit noticed duplicate payment by the regional office for the same road work.", None),
        ("In the case of NHPC, the contractor was billed twice for the same road work.", None),
        ("The contractor was paid twice under DDUGJY for the same road work.", "Western Power Limited"),
        ("NHPC paid the same invoice twice for the road work.", "NHPC"),
        ("NTPC and NHPC paid the same invoice twice for the road work.", None),
    ],
    ids=[
        "active-legal-name", "passive-acronym", "passive-legal-name", "recipient", "unspecified-payer",
        "payment-method", "deadline", "multiple-payers", "recipient-before-payer", "payment-made-by", "joint-payment",
        "comma-separated-payers", "coordinated-passive-names", "coordinated-active-names", "payer-before-result-clause",
        "conjunction-in-legal-name", "joint-with-regional-office",
        "noun-phrase-named-payer", "noun-phrase-recipient-before-payer", "amount-before-named-payer",
        "noun-phrase-unspecified-payer",
        "other-auditee", "scheme", "active-agency", "active-joint-agencies",
    ],
)
def test_or1_explicit_finding_actor_takes_precedence_over_section_opening_payer(evidence: str, payer: str | None) -> None:
    records, ctx = _or1_ctx(evidence)
    records.insert(0, {
        "page": 11,
        "raw_text": (
            "6.2 Review of grant payments\n"
            "Western Power Limited made an irregular payment for incentive claims."
        ),
    })

    res = run_engine(ONE_ROAD_TWO_BILLS_METHODOLOGY, records, context=ctx)

    lead, = res.leads
    assert lead.entity_name.startswith(payer or "Union Government (Social Infrastructure)")
    if payer:
        assert lead.suggested_authority == payer
    else:
        assert lead.suggested_authority != "Western Power Limited"
    assert lead.citations[0]["para_number"] == "6.2"
    assert lead.citations[0]["excerpt"] == evidence
    has_opener_citation = any("Western Power Limited" in citation["excerpt"] for citation in lead.citations[1:])
    assert has_opener_citation == (payer == "Western Power Limited")


@pytest.mark.parametrize(
    ("evidence", "payer"),
    [
        ("The road contract cost Rs 5 crore and NTPC paid the same invoice twice for the road work.", "NTPC"),
        ("The road contract cost Rs 5 crore, and NTPC paid the same invoice twice for the road work.", "NTPC"),
        ("NTPC paid for bridge works and NHPC paid the same invoice twice for the road work.", "NHPC"),
        ("Western Power Limited paid Rs 5 crore for the road contract and the same invoice was paid twice by NTPC.", "NTPC"),
        ("NTPC and NHPC paid the same invoice twice for the road work.", "Comptroller and Auditor General of India / PAC"),
        ("NTPC and its subsidiary paid the same invoice twice for the road work.", "Comptroller and Auditor General of India / PAC"),
    ],
)
def test_or1_binds_explicit_payer_and_amount_to_payment_clause(evidence: str, payer: str) -> None:
    records, ctx = _or1_ctx(evidence)
    records.insert(0, {"page": 11, "raw_text": (
        "6.2 Review of grant payments\nWestern Power Limited made an irregular payment for incentive claims."
    )})

    result = run_engine(ONE_ROAD_TWO_BILLS_METHODOLOGY, records, context=ctx)

    lead, = result.leads
    assert lead.suggested_authority == payer
    assert lead.monetary_exposure_inr == 0
    assert not lead.anomaly_metrics["amount_disclosed"]
    citation, = lead.citations
    assert citation["excerpt"] == evidence


@pytest.mark.parametrize("subject", ["CPSE", "DISCOM", "PSU", "DDO", "THE", "BOTH", "THEY", "NTPC", "CPWD", "REC"])
def test_or1_active_payer_requires_a_named_entity(subject: str) -> None:
    evidence = f"{subject} paid the same invoice twice for the road work."
    records, ctx = _or1_ctx(evidence)
    records.insert(0, {"page": 11, "raw_text": (
        "6.2 Review of grant payments\nWestern Power Limited made an irregular payment for incentive claims."
    )})

    result = run_engine(ONE_ROAD_TWO_BILLS_METHODOLOGY, records, context=ctx)

    lead, = result.leads
    if subject in {"NTPC", "CPWD", "REC"}:
        assert lead.suggested_authority == subject
        assert lead.entity_name == f"{subject} (6.2)"
    else:
        assert lead.suggested_authority == "Comptroller and Auditor General of India / PAC"
        assert lead.entity_name == "Union Government (Social Infrastructure) (6.2)"
    assert len(lead.citations) == 1
    assert lead.citations[0]["excerpt"] == evidence


@pytest.mark.parametrize("payer_position", ["preceding-line", "same-line-before", "same-line-after"])
def test_or1_checks_for_other_payers_between_the_opener_and_wrapped_evidence(payer_position: str) -> None:
    evidence = "The same invoice was paid twice for the road work."
    other_payment = "During audit of Eastern Power\nLimited, Eastern Power Limited paid excess bills under the scheme."
    if payer_position == "same-line-after":
        text = f"{evidence} " + other_payment.replace("\n", " ")
    else:
        separator = "\n" if payer_position == "preceding-line" else " "
        text = f"{other_payment}{separator}{evidence}"
    records, ctx = _or1_ctx(text)
    records.insert(0, {"page": 11, "raw_text": (
        "6.2 Review of grant payments\nWestern Power Limited made an irregular payment for incentive claims."
    )})

    result = run_engine(ONE_ROAD_TWO_BILLS_METHODOLOGY, records, context=ctx)

    lead, = result.leads
    if payer_position == "same-line-after":
        assert lead.entity_name == "Western Power Limited (6.2)"
        assert lead.suggested_authority == "Western Power Limited"
        assert len(lead.citations) == 2
    else:
        assert lead.entity_name == "Union Government (Social Infrastructure) (6.2)"
        assert lead.suggested_authority == "Comptroller and Auditor General of India / PAC"
        assert len(lead.citations) == 1
    assert lead.citations[0]["excerpt"] == evidence


@pytest.mark.parametrize(
    ("intervening_text", "can_borrow"),
    [
        ("XYZ paid the vendor.", False),
        ("The CPSE made excess payments under the scheme.", False),
        ("The other invoice was paid by the regional office.", False),
        ("Annexure Y (Contd.)", False),
        ("The other invoice was paid to NTPC on 31 March 2024.", True),
    ],
    ids=["unknown-acronym", "generic-payer", "unspecified-passive-payer", "new-annexure", "recipient-and-date"],
)
def test_or1_section_opener_requires_consistent_payer_context(intervening_text: str, can_borrow: bool) -> None:
    evidence = "The same invoice was paid twice for the road work."
    records, ctx = _or1_ctx(f"{intervening_text}\n{evidence}")
    records.insert(0, {"page": 11, "raw_text": (
        "6.2 Review of grant payments\nWestern Power Limited made an irregular payment for incentive claims."
    )})

    result = run_engine(ONE_ROAD_TWO_BILLS_METHODOLOGY, records, context=ctx)

    lead, = result.leads
    assert lead.citations[0]["excerpt"] == evidence
    if can_borrow:
        assert lead.suggested_authority == "Western Power Limited"
        assert [citation["page"] for citation in lead.citations] == [12, 11]
    else:
        assert lead.suggested_authority == "Comptroller and Auditor General of India / PAC"
        assert len(lead.citations) == 1


def test_or1_excludes_legitimate_cofunding_and_cost_sharing() -> None:
    page_text = (
        "3.1 Financing Pattern of Centrally Sponsored Scheme\n"
        "The project is jointly funded on a 60:40 Centre-State cost sharing basis between the "
        "Ministry of Rural Development and the State Government, with matching grant releases "
        "across both agencies totalling Rs. 120 crore as per scheme guidelines."
    )
    records, ctx = _or1_ctx(page_text, page=9)
    leads, cleared, _mon, _summary, meta = or1.run_or1_duplicate_billing(records, ctx)
    assert leads == []
    assert cleared == 1
    assert meta["excluded_legitimate_cofunding"] == 1


def test_or1_excludes_methodology_boilerplate_and_hyphenated_numbers() -> None:
    page_text = (
        "1.3 Audit Objectives and Methodology\n"
        "Audit checked whether internal controls were adequate to prevent duplicate payments across "
        "agencies. Forty-two bills paid between 2021 and 2023 were reviewed for compliance."
    )
    records, ctx = _or1_ctx(page_text, page=4)
    res = run_engine(ONE_ROAD_TWO_BILLS_METHODOLOGY, records, context=ctx)
    assert res.leads == []
    assert res.cleared_count == 1


def test_or1_neutralizes_accusatory_terms_and_scrubs_pii() -> None:
    page_text = (
        "5.1.4 Fraudulent duplicate vouchers submitted by contractor\n"
        "Audit observed double payment of Rs. 15 lakh against duplicate vouchers submitted by the "
        "contractor (contact 9876543210, PAN ABCDE1234F, email test@example.com)."
    )
    records, ctx = _or1_ctx(page_text, page=27)
    res = run_engine(ONE_ROAD_TWO_BILLS_METHODOLOGY, records, context=ctx)
    assert len(res.leads) == 1
    lead = res.leads[0]
    lint = lint_neutrality(lead.title, lead.summary, lead.benign_explanations)
    assert lint.passed
    assert "9876543210" not in lead.summary
    assert "ABCDE1234F" not in lead.summary
    assert "test@example.com" not in lead.summary


def test_or1_validate_context_rejects_malformed_specs() -> None:
    assert or1.validate_context({}, "tasks[0]")
    bad = {
        "one_road_two_bills": {
            "report": {"cag_id": "", "report_no": "R1", "year": "25", "title": ""},
            "windows": [{"key": "BAD KEY", "page": 0, "block_index": -1, "line_start": 5, "line_end": 2}],
        }
    }
    errs = or1.validate_context(bad, "tasks[0]")
    assert len(errs) >= 6


def test_real_p4_project_file_is_valid_and_matches_artifacts() -> None:
    proj_path = PROJECTS_DIR / f"{ONE_ROAD_TWO_BILLS_PROJECT_SLUG}.json"
    assert proj_path.is_file()
    data = json.loads(proj_path.read_text(encoding="utf-8"))
    assert data["slug"] == ONE_ROAD_TWO_BILLS_PROJECT_SLUG
    assert data["project_code"] == "P4"
    assert data["methodology_code"] == "OR1"
    assert len(data["tasks"]) == 10_000
    assert validate_project(data, filename_stem=ONE_ROAD_TWO_BILLS_PROJECT_SLUG) == []

    coverage = json.loads((PROJECTS_DIR / ONE_ROAD_TWO_BILLS_PROJECT_SLUG / "coverage.json").read_text(encoding="utf-8"))
    assert coverage["tasks"] == 10_000
    assert coverage["reports_used"] > 20

    with (PROJECTS_DIR / ONE_ROAD_TWO_BILLS_PROJECT_SLUG / "documents.csv").open("r", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len([r for r in rows if r["status"] == "used"]) == coverage["reports_used"]

    # Every OR1 URL and report title must be free of raw HTML entities or URL fragments.
    for row in rows:
        assert "&#" not in row["pdf_url"] and "&amp;" not in row["pdf_url"] and "#" not in row["pdf_url"]
        assert "&ndash;" not in row["title"] and "&amp;" not in row["title"]
    for task in data["tasks"]:
        url = task["payload"]["retrieval_url"]
        assert "&#" not in url and "&amp;" not in url and "#" not in url
        rep_title = task["payload"]["context"]["one_road_two_bills"]["report"]["title"]
        assert "&ndash;" not in rep_title and "&amp;" not in rep_title


def test_validator_rejects_html_entities_in_urls_and_no_ollama_or_local_cli_remnants() -> None:
    bad_proj = {
        "slug": "entity-url-test",
        "title": "Synthetic project testing HTML entity URL rejection",
        "summary": "Ensures validate_project rejects unescaped HTML entities in artifact URLs.",
        "posted_by": {"name": "AI Watchdogs India", "url": "https://aiwatchdogs.in"},
        "pillar": "audit",
        "status": "active",
        "jurisdiction": "Union of India",
        "authority": "CAG",
        "sources": [{"name": "CAG", "publisher": "CAG", "url": "https://cag.gov.in"}],
        "methodology_code": "OR1",
        "tasks": [
            {
                "id": "or1-entity-0001",
                "title": "Entity URL test task",
                "instructions": "Verify entity URL rejection.",
                "type": "run_check",
                "dataset_key": "cag-entity-1",
                "financial_quantity_type": "cag_report_monetary_figures",
                "methodology_code": "OR1",
                "estimated_tokens": 500,
                "estimate_basis": "test",
                "payload": {
                    "retrieval_url": "https://d1jmc0u14z2tmp.cloudfront.net/uploads/CAG&#039;s-Report.pdf",
                    "expected_sha256": "a" * 64,
                    "official_publisher": "CAG",
                    "source_landing_page": "https://cag.gov.in/en/audit-report/details/1",
                    "context": {
                        "one_road_two_bills": {
                            "report": {
                                "cag_id": "1",
                                "report_no": "Report No. 1 of 2025",
                                "year": "2025",
                                "title": "Test Report",
                                "sector": "Civil",
                            },
                            "windows": [{"key": "r1-p001-b00", "page": 1, "block_index": 0, "line_start": 0, "line_end": 5}],
                        }
                    },
                },
            }
        ],
    }
    errs = validate_project(bad_proj, filename_stem="entity-url-test")
    assert any("HTML entity" in e for e in errs), errs

    # Website, GitHub docs, CLI, and /join.txt must not advertise local Ollama or local CLI onboarding.
    from aiwatchdogs.hub.templates import render_join_txt

    join_txt = render_join_txt("https://aiwatchdogs.in").lower()
    assert "ollama" not in join_txt
    assert "join.sh" not in join_txt
    assert "local checkout" not in join_txt

    for rel in (
        "README.md",
        "CONTRIBUTING.md",
        "SPONSORS.md",
        "web/src/components/JoinModal.tsx",
        "web/src/components/Footer.tsx",
        "web/src/components/EyebrowBar.tsx",
        "web/src/components/LiveAgentStrip.tsx",
        "src/aiwatchdogs/cli/main.py",
    ):
        text = (REPO_ROOT / rel).read_text(encoding="utf-8").lower()
        assert "ollama" not in text, f"Unexpected 'ollama' in {rel}"
        assert "shell installer" not in text, f"Unexpected 'shell installer' in {rel}"
        assert "from a local repo checkout" not in text, f"Unexpected local checkout copy in {rel}"
        assert "cli & frontier api" not in text, f"Unexpected CLI tab in {rel}"


def test_p4_hub_endpoints_and_two_agent_peer_verification(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIWATCHDOGS_PROJECTS_DIR", str(PROJECTS_DIR))
    monkeypatch.setenv("AIWATCHDOGS_ALLOW_PROJECT_CHOICE", "1")
    db_path = tmp_path / "p4_hub.db"
    app = create_app(db_path=str(db_path))

    # Build a small synthetic PDF so the CLI runner can run a real OR1 task end-to-end without network.
    pdf_text = (
        "6.3.1.2 Duplicate claim on rural road project\n"
        "Despite receiving funds under PMGSY, Rs. 3.50 crore was claimed again under another scheme, "
        "resulting in double payment for the same road work."
    )
    pdf_bytes = _minimal_pdf_bytes(pdf_text)
    pdf_sha = compute_sha256(pdf_bytes)
    pdf_url = "https://d1jmc0u14z2tmp.cloudfront.net/uploads/download_audit_report/2025/synthetic-or1.pdf"

    import httpx

    orig_get = httpx.Client.get
    orig_stream = httpx.Client.stream

    class _FakeResp:
        def __init__(self, content: bytes) -> None:
            self.status_code = 200
            self.headers = {"Content-Type": "application/pdf", "Content-Length": str(len(content))}
            self.content = content

        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self, chunk_size: int = 65536):
            yield self.content

        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> None:
            return None

    def _fake_stream(self: Any, method: str, url: str, **kwargs: Any):
        if str(url) == pdf_url:
            return _FakeResp(pdf_bytes)
        return orig_stream(self, method, url, **kwargs)

    def _fake_get(self: Any, url: str, **kwargs: Any):
        if str(url) == pdf_url:
            return _FakeResp(pdf_bytes)
        return orig_get(self, url, **kwargs)

    monkeypatch.setattr(httpx.Client, "stream", _fake_stream)
    monkeypatch.setattr(httpx.Client, "get", _fake_get)

    with TestClient(app, base_url="http://testserver") as client:
        missions_resp = client.get("/api/missions")
        assert missions_resp.status_code == 200
        by_slug = {m["slug"]: m for m in missions_resp.json()["missions"]}
        assert ONE_ROAD_TWO_BILLS_PROJECT_SLUG in by_slug
        p4_meta = by_slug[ONE_ROAD_TWO_BILLS_PROJECT_SLUG]
        assert p4_meta["project_code"] == "P4"
        assert p4_meta["total_tasks"] == 10_000

        detail_resp = client.get(f"/api/missions/{ONE_ROAD_TWO_BILLS_PROJECT_SLUG}")
        assert detail_resp.status_code == 200
        detail = detail_resp.json()
        assert detail["project_code"] == "P4"
        assert detail["tasks_truncated"] is True
        vs = detail["visual_summary"]
        assert vs["methodology_code"] == "OR1"
        assert vs["one_road_two_bills"]["reports"] > 20

        assert client.get(f"/api/missions/{ONE_ROAD_TWO_BILLS_PROJECT_SLUG}/documents.csv").status_code == 200
        assert client.get(f"/api/missions/{ONE_ROAD_TWO_BILLS_PROJECT_SLUG}/coverage.json").status_code == 200

        # Insert one deterministic OR1 task pinned to the synthetic PDF and run Auditor + 2 Peer Verifiers via AgentRunner.
        task_id = "prj-or1-2025-r999999-0001"
        payload = {
            "retrieval_url": pdf_url,
            "dataset_url": pdf_url,
            "expected_sha256": pdf_sha,
            "official_publisher": "CAG of India",
            "source_landing_page": "https://cag.gov.in/en/audit-report/details/999999",
            "jurisdiction": "Union of India",
            "context": {
                "one_road_two_bills": {
                    "report": {
                        "cag_id": "999999",
                        "report_no": "Report No. 99 of 2025",
                        "year": "2025",
                        "title": "Synthetic CAG Union Report",
                        "sector": "Rural Roads",
                    },
                    "windows": [{"key": "r999999-p001-b00", "page": 1, "block_index": 0, "line_start": 0, "line_end": 3}],
                    "thresholds": {"min_amount_inr": 0},
                }
            },
        }
        with app.state.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO tasks (
                    id, mission_id, mission_slug, methodology_code, task_type,
                    title, instructions, payload_json, target_finding_id,
                    excluded_handles_json, status, dataset_key, financial_quantity_type,
                    work_type, estimated_tokens, estimate_basis, created_at, updated_at
                ) VALUES (?, ?, ?, 'OR1', 'anomaly_scan', 'Synthetic OR1 check', 'Run OR1', ?, NULL, '[]',
                          'queued', 'cag-union-2025-999999', 'cag_report_monetary_figures', 'run_check',
                          500, 'test', '2026-11-27T00:00:00Z', '2026-11-27T00:00:00Z')
                """,
                (task_id, f"project-{ONE_ROAD_TWO_BILLS_PROJECT_SLUG}", ONE_ROAD_TWO_BILLS_PROJECT_SLUG, json.dumps(payload)),
            )

        auditor = AgentRunner(
            AgentConfig(
                handle="or1-auditor-one",
                hub_url="http://testserver",
                provider="rule-engine",
                model="deterministic-cpu-v1",
                mode="investigate",
                max_tasks=1,
            ),
            http_client=client,
            config_path=tmp_path / "auditor.json",
        )
        res_a = auditor.run_loop(max_tasks=1, preferred_mission=ONE_ROAD_TWO_BILLS_PROJECT_SLUG, task_id=task_id)
        assert res_a.findings_submitted == 1

        for idx in (1, 2):
            pv = AgentRunner(
                AgentConfig(
                    handle=f"or1-verifier-{idx}",
                    hub_url="http://testserver",
                    provider="rule-engine",
                    model="deterministic-cpu-v1",
                    mode="verify",
                    max_tasks=1,
                ),
                http_client=client,
                config_path=tmp_path / f"pv{idx}.json",
            )
            res_pv = pv.run_loop(max_tasks=1, preferred_mission=ONE_ROAD_TWO_BILLS_PROJECT_SLUG)
            assert res_pv.verifications_submitted == 1

        findings = client.get(f"/api/findings?mission={ONE_ROAD_TWO_BILLS_PROJECT_SLUG}").json()["findings"]
        assert len(findings) == 1
        assert findings[0]["status"] == "verified"
        assert findings[0]["methodology_code"] == "OR1"
