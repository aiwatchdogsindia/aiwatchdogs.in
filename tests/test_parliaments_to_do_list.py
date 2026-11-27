"""Comprehensive unit and integration tests for Project P6 (parliaments-to-do-list) and engine P6."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from aiwatchdogs.cli.config import AgentConfig
from aiwatchdogs.cli.runner import AgentRunner
from aiwatchdogs.connectors.loader import DigestMismatchError, load_dataset
from aiwatchdogs.core.rti import generate_rti_draft
from aiwatchdogs.core.safety import compute_sha256
from aiwatchdogs.engines import parliaments_to_do_list as p6_engine
from aiwatchdogs.engines.registry import run_engine
from aiwatchdogs.hub.agent_access import render_finding_detail_md
from aiwatchdogs.hub.app import create_app
from aiwatchdogs.projects import (
    PARLIAMENTS_TO_DO_LIST_METHODOLOGY,
    PARLIAMENTS_TO_DO_LIST_PROJECT_SLUG,
    validate_project,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECTS_DIR = REPO_ROOT / "projects"

ANCHOR_RETRIEVAL_URL = (
    "https://sansad.in/api_ls/govtAssurance/assurances"
    "?loksabha=18&session=7&qNo=544&qType=SQ&page=1&size=20&locale=en"
)
ANCHOR_SECONDARY_URL = (
    "https://sansad.in/getFile/lsapps/loksabhaquestions/annex/187/AS544_VvtUfG.pdf?source=lsapps"
)
RS_FIMP_RETRIEVAL_URL = (
    "https://sansad.in/api_rs/govtAssurance/assurances"
    "?qNo=139&qType=Unstarred&sessionFrom=270&sessionTo=270&page=1&size=20&locale=en"
)
RS_FIMP_IR_URL = "https://sansad.in/getFile/dms/fetch/895b70b8-d3a1-4a03-a572-7e102215808d?source=dsp2rs"
LS_FIMP_RETRIEVAL_URL = (
    "https://sansad.in/api_ls/govtAssurance/assurances"
    "?loksabha=18&session=7&qNo=6060&qType=USQ&page=1&size=20&locale=en"
)


def _minimal_multipage_pdf_bytes(pages: list[str]) -> bytes:
    """Create a minimal valid multi-page PDF readable by pypdf."""
    objs: list[bytes] = []
    num_pages = len(pages)
    kid_refs = " ".join(f"{4 + 2 * i} 0 R" for i in range(num_pages))
    objs.append(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")
    objs.append(f"2 0 obj\n<< /Type /Pages /Kids [{kid_refs}] /Count {num_pages} >>\nendobj\n".encode("ascii"))
    objs.append(b"3 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n")

    for i, text in enumerate(pages):
        page_obj_id = 4 + 2 * i
        stream_obj_id = page_obj_id + 1
        if text:
            escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            lines = escaped.splitlines() or [""]
            cmds = ["BT", "/F1 10 Tf", "40 760 Td", "12 TL"]
            for li, line in enumerate(lines):
                cmds.append(f"({line}) Tj" if li == 0 else f"T* ({line}) Tj")
            cmds.append("ET")
            stream = "\n".join(cmds).encode("latin-1", "replace")
        else:
            # Blank/scanned image-only page with 0 text operators
            stream = b""
        objs.append(
            f"{page_obj_id} 0 obj\n"
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {stream_obj_id} 0 R "
            f"/Resources << /Font << /F1 3 0 R >> >> >>\nendobj\n".encode("ascii")
        )
        objs.append(
            f"{stream_obj_id} 0 obj\n<< /Length {len(stream)} >>\nstream\n".encode("ascii")
            + stream
            + b"\nendstream\nendobj\n"
        )

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


def _anchor_json_payload() -> dict[str, Any]:
    """Real 3-row Digital Sansad structure for SQ-18/07/544 (Jhunjhunu to Fatehpur)."""
    return {
        "records": [
            {
                "assuNo": "SQ-18/07/544",
                "loksabha": 18,
                "session": 7,
                "date": "02/04/2026",
                "ministry": "ROAD TRANSPORT AND HIGHWAYS",
                "member": "Shri Brijendra Singh Ola",
                "subject": "Widening of Nh-11 Road from Jhunjhunu to Fatehpur",
                "status": "PENDING",
                "impRecordNo": "",
                "remarks": None,
                "textOfAssurance": (
                    "(a) to (c) The process for preparation of Detailed Project Report (DPR) for "
                    "development of NH-11 from Jhunjhunu to Fatehpur to four-lanes has been initiated "
                    "in January, 2026. Based on the outcome of the DPR, this stretch would be considered "
                    "for upgradation to four-lanes subject to traffic requirements and synergy with "
                    "PM Gati Shakti National Master Plan (NMP)."
                ),
                "qtype": "SQ",
                "qno": 544,
            },
            {
                "assuNo": "SQ-18/07/544",
                "loksabha": 18,
                "session": 7,
                "date": "02/04/2026",
                "ministry": "ROAD TRANSPORT AND HIGHWAYS",
                "member": "Shri Brijendra Singh Ola",
                "subject": "Widening of Nh-11 Road from Jhunjhunu to Fatehpur",
                "status": "PENDING",
                "impRecordNo": "",
                "remarks": None,
                "textOfAssurance": "DPR preparation is expected to be completed by December 2026.",
                "qtype": "SQ",
                "qno": 544,
            },
            {
                "assuNo": "SQ-18/07/544",
                "loksabha": 18,
                "session": 7,
                "date": "02/04/2026",
                "ministry": "ROAD TRANSPORT AND HIGHWAYS",
                "member": "Shri Ramvir Singh Bidhuri",
                "subject": "Widening of Nh-11 Road from Jhunjhunu to Fatehpur",
                "status": "PENDING",
                "impRecordNo": "",
                "remarks": None,
                "textOfAssurance": "Supplementary assurance noted regarding national highway DPR timelines.",
                "qtype": "SQ",
                "qno": 544,
            },
        ]
    }


def _anchor_question_pdf_pages() -> list[str]:
    return [
        "\n".join(
            [
                "GOVERNMENT OF INDIA",
                "MINISTRY OF ROAD TRANSPORT AND HIGHWAYS",
                "LOK SABHA",
                "STARRED QUESTION NO. 544",
                "TO BE ANSWERED ON 2ND APRIL, 2026",
                "WIDENING OF NH-11 ROAD FROM JHUNJHUNU TO FATEHPUR",
                "544. SHRI BRIJENDRA SINGH OLA:",
                "Will the Minister of ROAD TRANSPORT AND HIGHWAYS be pleased to state:",
                "(a) whether the Government proposes to widen NH-11 from Jhunjhunu to Fatehpur;",
                "ANSWER",
                "THE MINISTER OF ROAD TRANSPORT AND HIGHWAYS",
                "(SHRI NITIN JAIRAM GADKARI)",
                "(a) to (c) A statement is laid on the Table of the House.",
            ]
        ),
        "\n".join(
            [
                "ANNEXURE",
                "STATEMENT REFERRED TO IN REPLY TO PARTS (a) TO (c) OF LOK SABHA STARRED QUESTION NO. 544",
                "The process for preparation of Detailed Project Report (DPR) for development of NH-11",
                "from Jhunjhunu to Fatehpur to four-lanes has been initiated in January, 2026.",
            ]
        ),
    ]


def _p6_ctx(
    *,
    spec_overrides: dict[str, Any] | None = None,
    retrieval_url: str = ANCHOR_RETRIEVAL_URL,
    expected_sha256: str = "a" * 64,
    secondary_url: str | None = None,
    secondary_expected_sha256: str | None = None,
    as_of_date: str = "2026-11-27",
) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "assurance_key": "ls-18-07-sq-544",
        "house": "Lok Sabha",
        "house_code": "LS",
        "loksabha": 18,
        "session": "7",
        "assurance_ref": "SQ-18/07/544",
        "question_type": "SQ",
        "question_no": "544",
        "promise_date": "2026-04-02",
        "ministry": "Road Transport and Highways",
        "ministry_key": "road-transport-and-highways",
        "raw_ministry": "ROAD TRANSPORT AND HIGHWAYS",
        "member": "Shri Brijendra Singh Ola",
        "subject": "Widening of Nh-11 Road from Jhunjhunu to Fatehpur",
        "recorded_status": "pending",
        "raw_status": "PENDING",
        "secondary_role": "question_pdf" if secondary_url else None,
        "portal_url": "https://sansad.in/ls/business/government-assurances",
        "is_anchor_case": True,
    }
    if spec_overrides:
        spec.update(spec_overrides)
    ctx: dict[str, Any] = {
        "official_publisher": "Parliament of India — Digital Sansad (Lok Sabha / Rajya Sabha Secretariat)",
        "source_landing_page": spec["portal_url"],
        "retrieval_url": retrieval_url,
        "expected_sha256": expected_sha256,
        "as_of_date": as_of_date,
        "parliaments_to_do_list": spec,
    }
    if secondary_url:
        ctx["secondary_url"] = secondary_url
    if secondary_expected_sha256:
        ctx["secondary_expected_sha256"] = secondary_expected_sha256
    return ctx


# ---------------------------------------------------------------------------
# 1. Anchor Case (Jhunjhunu-Fatehpur SQ-18/07/544) & Core Signal Tests
# ---------------------------------------------------------------------------


def test_p6_anchor_case_jhunjhunu_fatehpur_extracts_all_three_citations() -> None:
    sec_sha = "b" * 64
    primary_records = [{**r, "document": "primary"} for r in _anchor_json_payload()["records"]]
    secondary_records = [
        {"page": idx, "raw_text": txt, "document": "secondary", "secondary_sha256": sec_sha}
        for idx, txt in enumerate(_anchor_question_pdf_pages(), start=1)
    ]

    res = run_engine(
        PARLIAMENTS_TO_DO_LIST_METHODOLOGY,
        primary_records + secondary_records,
        context=_p6_ctx(
            expected_sha256="a" * 64,
            secondary_url=ANCHOR_SECONDARY_URL,
            secondary_expected_sha256=sec_sha,
            as_of_date="2026-11-27",
        ),
    )

    assert res.unevaluable_count == 0
    assert res.cleared_count == 0
    assert res.as_of_date == "2026-11-27"
    assert len(res.leads) == 1
    lead = res.leads[0]
    assert lead.methodology_code == "P6"
    assert lead.rule_triggered == "P6-OVERDUE-PENDING"
    assert lead.severity == "high"
    assert lead.entity_name == "Ministry of Road Transport and Highways"
    assert lead.jurisdiction == "Union of India"
    assert lead.fiscal_year == "FY 2026-27"

    m = lead.anomaly_metrics
    assert m["assurance_key"] == "ls-18-07-sq-544"
    assert m["assurance_ref"] == "SQ-18/07/544"
    assert m["house"] == "Lok Sabha"
    assert m["ministry"] == "Road Transport and Highways"
    assert m["minister"] == "Shri Nitin Jairam Gadkari"
    assert m["days_elapsed"] == 239
    assert m["recorded_status"] == "pending"
    assert m["signal_category"] == "overdue_pending_assurance"
    assert m["supplementary_rows_count"] == 2
    assert m["is_anchor_case"] is True
    assert "Jhunjhunu to Fatehpur" in m["original_promise"]
    assert "No Implementation Statement has been laid" in m["followup_answer"]

    # Summary and citations explicitly cover Original Promise, Recorded Status, and Follow-Up Answer
    assert "Original promise" in lead.summary
    assert "Recorded status: PENDING" in lead.summary
    assert "Follow-up answer:" in lead.summary
    cit_roles = [c["role"] for c in lead.citations]
    assert cit_roles == [
        "original_promise",
        "original_question_pdf",
        "recorded_status",
        "follow_up_answer",
    ]
    qpdf_cit = lead.citations[1]
    assert qpdf_cit["source_url"] == ANCHOR_SECONDARY_URL
    assert qpdf_cit["sha256"] == sec_sha
    assert qpdf_cit["page"] == 1


def test_p6_pending_severity_tiers_and_within_90_days_cleared() -> None:
    rows = _anchor_json_payload()["records"]

    # Within 90-day parliamentary norm (60 days) -> Cleared
    within = run_engine(
        PARLIAMENTS_TO_DO_LIST_METHODOLOGY,
        rows,
        context=_p6_ctx(as_of_date="2026-06-01"),
    )
    assert within.leads == []
    assert within.cleared_count == 1
    assert within.unevaluable_count == 0
    assert "Original promise" in within.summary
    assert "Recorded status: PENDING" in within.summary

    # 120 days -> medium
    med = run_engine(
        PARLIAMENTS_TO_DO_LIST_METHODOLOGY,
        rows,
        context=_p6_ctx(as_of_date="2026-07-31"),
    )
    assert len(med.leads) == 1
    assert med.leads[0].severity == "medium"
    assert med.leads[0].anomaly_metrics["days_elapsed"] == 120

    # > 365 days -> critical
    crit = run_engine(
        PARLIAMENTS_TO_DO_LIST_METHODOLOGY,
        rows,
        context=_p6_ctx(as_of_date="2027-04-10"),
    )
    assert len(crit.leads) == 1
    assert crit.leads[0].severity == "critical"
    assert crit.leads[0].anomaly_metrics["days_elapsed"] == 373


def test_p6_ls_fulfilled_partly_implemented_and_dropped() -> None:
    # 1. LS Fully Implemented within 180 days (Space USQ-18/07/6060, 126 days) -> Cleared
    fimp_row = {
        "assuNo": "USQ-18/07/6060",
        "loksabha": 18,
        "session": 7,
        "date": "01/04/2026",
        "ministry": "SPACE",
        "member": "Shri Example MP",
        "subject": "Space Venture Capital Appraisal",
        "status": "FULLY IMPLEMENTED",
        "impRecordNo": "SS-1/28/05.08.2026",
        "remarks": None,
        "textOfAssurance": "a to f: With the handholding, SVCL is hopeful to complete the detailed appraisal shortly.",
        "qtype": "USQ",
        "qno": 6060,
    }
    fimp_ctx = _p6_ctx(
        retrieval_url=LS_FIMP_RETRIEVAL_URL,
        spec_overrides={
            "assurance_key": "ls-18-07-usq-6060",
            "assurance_ref": "USQ-18/07/6060",
            "question_type": "USQ",
            "question_no": "6060",
            "promise_date": "2026-04-01",
            "ministry": "Space",
            "ministry_key": "space",
            "recorded_status": "fully_implemented",
            "raw_status": "FULLY IMPLEMENTED",
            "is_anchor_case": False,
        },
    )
    cleared_fimp = run_engine(PARLIAMENTS_TO_DO_LIST_METHODOLOGY, [fimp_row], context=fimp_ctx)
    assert cleared_fimp.leads == []
    assert cleared_fimp.cleared_count == 1
    assert "SS-1/28/05.08.2026" in cleared_fimp.summary

    # 2. LS Fully Implemented delayed (>180 days -> low; >365 days -> medium)
    delayed_row = {**fimp_row, "impRecordNo": "SS-3/12/01.12.2026"}
    delayed = run_engine(PARLIAMENTS_TO_DO_LIST_METHODOLOGY, [delayed_row], context=fimp_ctx)
    assert len(delayed.leads) == 1
    assert delayed.leads[0].rule_triggered == "P6-FOLLOWUP-GAP"
    assert delayed.leads[0].severity == "low"
    assert delayed.leads[0].entity_name == "Department of Space"
    assert delayed.leads[0].anomaly_metrics["fulfillment_days"] == 244

    very_delayed_row = {**fimp_row, "impRecordNo": "SS-9/02/15.05.2027"}
    very_delayed = run_engine(PARLIAMENTS_TO_DO_LIST_METHODOLOGY, [very_delayed_row], context=fimp_ctx)
    assert very_delayed.leads[0].severity == "medium"
    assert very_delayed.leads[0].anomaly_metrics["fulfillment_days"] == 409

    # 3. LS Partly Implemented (USQ-18/05/4774)
    pimp_row = {
        "assuNo": "USQ-18/05/4774",
        "loksabha": 18,
        "session": 5,
        "date": "20/08/2025",
        "ministry": "RAILWAYS",
        "member": "Shri Test Member",
        "subject": "Gauge conversion works",
        "status": "PARTLY IMPLEMENTED",
        "impRecordNo": "SS-5/4/11.02.2026",
        "remarks": None,
        "textOfAssurance": "Detailed survey for the remaining section is under examination.",
        "qtype": "USQ",
        "qno": 4774,
    }
    pimp_ctx = _p6_ctx(
        spec_overrides={
            "assurance_key": "ls-18-05-usq-4774",
            "assurance_ref": "USQ-18/05/4774",
            "session": "5",
            "question_type": "USQ",
            "question_no": "4774",
            "promise_date": "2025-08-20",
            "ministry": "Railways",
            "ministry_key": "railways",
            "recorded_status": "partly_implemented",
            "raw_status": "PARTLY IMPLEMENTED",
            "is_anchor_case": False,
        },
        as_of_date="2026-11-27",
    )
    pimp_res = run_engine(PARLIAMENTS_TO_DO_LIST_METHODOLOGY, [pimp_row], context=pimp_ctx)
    assert len(pimp_res.leads) == 1
    assert pimp_res.leads[0].rule_triggered == "P6-PARTLY-IMPLEMENTED"
    assert pimp_res.leads[0].severity == "high"
    assert pimp_res.leads[0].anomaly_metrics["followup_date"] == "2026-02-11"
    assert pimp_res.leads[0].anomaly_metrics["fulfillment_days"] == 175

    # 4. LS Dropped (USQ-18/02/906)
    drop_row = {
        "assuNo": "USQ-18/02/906",
        "loksabha": 18,
        "session": 2,
        "date": "01/08/2024",
        "ministry": "FINANCE",
        "member": "Smt. Test Member",
        "subject": "Regional rural bank branches",
        "status": "DROPPED",
        "impRecordNo": "",
        "remarks": "Dropped at the sitting held on 02.09.2026",
        "textOfAssurance": "Information is being collected and will be laid on the Table of the House.",
        "qtype": "USQ",
        "qno": 906,
    }
    drop_ctx = _p6_ctx(
        spec_overrides={
            "assurance_key": "ls-18-02-usq-906",
            "assurance_ref": "USQ-18/02/906",
            "session": "2",
            "question_type": "USQ",
            "question_no": "906",
            "promise_date": "2024-08-01",
            "ministry": "Finance",
            "ministry_key": "finance",
            "recorded_status": "dropped",
            "raw_status": "DROPPED",
            "is_anchor_case": False,
        },
        as_of_date="2026-11-27",
    )
    drop_res = run_engine(PARLIAMENTS_TO_DO_LIST_METHODOLOGY, [drop_row], context=drop_ctx)
    assert len(drop_res.leads) == 1
    assert drop_res.leads[0].rule_triggered == "P6-DROPPED-ASSURANCE"
    assert drop_res.leads[0].severity == "medium"
    assert drop_res.leads[0].anomaly_metrics["followup_date"] == "2026-09-02"
    assert "02.09.2026" in drop_res.leads[0].anomaly_metrics["followup_answer"]


def test_p6_rajya_sabha_pending_extensions_and_readable_vs_scanned_ir_pdf() -> None:
    # 1. RS Pending with >= 2 extensions -> critical even at 106 days
    rs_pending_row = {
        "session": "271",
        "date": "2026-08-13",
        "ministry": "DEVELOPMENT OF NORTH EASTERN REGION",
        "member": "Jogen Mohan",
        "subject": "Fund utilisation in NER",
        "questionType": "Starred",
        "status": "Pending",
        "extent": "(c) Under the schemes of MDoNER, project-wise utilisation data is being compiled.",
        "extNo": 2,
        "extUpto": "2027-05-11",
        "irUrl": None,
        "qno": "283",
        "oassuNo": "271/14",
        "esought": "2027-05-11",
        "egranted": "2027-05-11",
    }
    rs_pending_ctx = _p6_ctx(
        retrieval_url="https://sansad.in/api_rs/govtAssurance/assurances?qNo=283&qType=Starred&sessionFrom=271&sessionTo=271&page=1&size=20&locale=en",
        spec_overrides={
            "assurance_key": "rs-271-14",
            "house": "Rajya Sabha",
            "house_code": "RS",
            "loksabha": None,
            "session": "271",
            "assurance_ref": "RS-271/14",
            "oassu_no": "271/14",
            "question_type": "Starred",
            "question_no": "283",
            "promise_date": "2026-08-13",
            "ministry": "Development of North Eastern Region",
            "ministry_key": "development-of-north-eastern-region",
            "recorded_status": "pending",
            "raw_status": "Pending",
            "secondary_role": None,
            "portal_url": "https://sansad.in/rs/house-business/government-assurances",
            "is_anchor_case": False,
        },
        as_of_date="2026-11-27",
    )
    rs_p = run_engine(PARLIAMENTS_TO_DO_LIST_METHODOLOGY, [rs_pending_row], context=rs_pending_ctx)
    assert len(rs_p.leads) == 1
    assert rs_p.leads[0].severity == "critical"
    assert rs_p.leads[0].anomaly_metrics["days_elapsed"] == 106
    assert rs_p.leads[0].anomaly_metrics["extensions_count"] == 2
    assert rs_p.leads[0].anomaly_metrics["extension_upto"] == "2027-05-11"

    # 2. RS FIMP with scanned image-only IR PDF -> flags P6-FOLLOWUP-GAP and links citation to IR PDF
    rs_fimp_row = {
        "session": "270",
        "date": "2026-02-10",
        "ministry": "HEALTH AND FAMILY WELFARE",
        "member": "Dr. Test MP",
        "subject": "AIIMS faculty vacancies",
        "questionType": "Unstarred",
        "status": "FIMP",
        "extent": "Recruitment status across new AIIMS campuses is being compiled and will be laid.",
        "extNo": 0,
        "extUpto": None,
        "irUrl": RS_FIMP_IR_URL,
        "qno": "139",
        "oassuNo": "270/139",
    }
    ir_sha = "c" * 64
    rs_fimp_ctx = _p6_ctx(
        retrieval_url=RS_FIMP_RETRIEVAL_URL,
        expected_sha256="d" * 64,
        secondary_url=RS_FIMP_IR_URL,
        secondary_expected_sha256=ir_sha,
        spec_overrides={
            "assurance_key": "rs-270-139",
            "house": "Rajya Sabha",
            "house_code": "RS",
            "loksabha": None,
            "session": "270",
            "assurance_ref": "RS-270/139",
            "oassu_no": "270/139",
            "question_type": "Unstarred",
            "question_no": "139",
            "promise_date": "2026-02-10",
            "ministry": "Health and Family Welfare",
            "ministry_key": "health-and-family-welfare",
            "recorded_status": "fully_implemented",
            "raw_status": "FIMP",
            "secondary_role": "implementation_report_pdf",
            "portal_url": "https://sansad.in/rs/house-business/government-assurances",
            "is_anchor_case": False,
        },
        as_of_date="2026-11-27",
    )
    scanned_recs = [
        {**rs_fimp_row, "document": "primary"},
        {"page": 1, "raw_text": "", "document": "secondary", "secondary_sha256": ir_sha},
        {"page": 2, "raw_text": "   ", "document": "secondary", "secondary_sha256": ir_sha},
    ]
    rs_scanned = run_engine(PARLIAMENTS_TO_DO_LIST_METHODOLOGY, scanned_recs, context=rs_fimp_ctx)
    assert len(rs_scanned.leads) == 1
    lead = rs_scanned.leads[0]
    assert lead.rule_triggered == "P6-FOLLOWUP-GAP"
    assert lead.anomaly_metrics["followup_text_status"] == "scanned_ir_pdf_no_text_layer"
    assert lead.anomaly_metrics["secondary_pdf_pages"] == 2
    followup_cit = [c for c in lead.citations if c["role"] == "follow_up_answer"][0]
    assert followup_cit["source_url"] == RS_FIMP_IR_URL
    assert followup_cit["sha256"] == ir_sha

    # Verify finding .md links primary citations to Digital Sansad API and follow-up citation to RS IR PDF
    class _StubDesk:
        def get_finding_detail(self, fid: str) -> dict[str, Any]:
            return {
                "id": fid,
                "title": lead.title,
                "summary": lead.summary,
                "methodology_code": "P6",
                "artifact_sha256": "d" * 64,
                "citations": [
                    {**c, "source_ref": RS_FIMP_RETRIEVAL_URL}
                    for c in lead.citations
                ],
            }

    md = render_finding_detail_md(_StubDesk(), "https://aiwatchdogs.in", "fnd-p6-rs")  # type: ignore[arg-type]
    assert f"- {RS_FIMP_RETRIEVAL_URL}" in md
    assert f"- {RS_FIMP_IR_URL} (page 1)" in md

    # 3. RS FIMP with readable digital text IR PDF laid within 180 days -> Cleared
    readable_recs = [
        {**rs_fimp_row, "document": "primary"},
        {
            "page": 1,
            "raw_text": (
                "IMPLEMENTATION REPORT LAID ON 15.05.2026\n"
                "Action Taken: Out of 1,450 sanctioned faculty posts across six new AIIMS, "
                "1,120 posts have been filled and advertisement issued for the remaining 330 posts."
            ),
            "document": "secondary",
            "secondary_sha256": ir_sha,
        },
    ]
    rs_readable = run_engine(PARLIAMENTS_TO_DO_LIST_METHODOLOGY, readable_recs, context=rs_fimp_ctx)
    assert rs_readable.leads == []
    assert rs_readable.cleared_count == 1
    assert "1,120 posts have been filled" in rs_readable.summary


def test_p6_strips_eoffice_watermarks_from_scanned_and_digital_ir_pdfs() -> None:
    """Regression: eOffice footer stamps on scanned RS IR PDFs must not fool readability detection."""
    rs_fimp_row = {
        "session": "260",
        "date": "2023-08-03",
        "ministry": "HEALTH AND FAMILY WELFARE",
        "member": "Dr. Test MP",
        "subject": "Medical colleges progress",
        "questionType": "Unstarred",
        "status": "FIMP",
        "extent": "State-wise construction status is being collected.",
        "extNo": 0,
        "irUrl": RS_FIMP_IR_URL,
        "qno": "28",
        "oassuNo": "260/28",
    }
    ir_sha = "e" * 64
    ctx = _p6_ctx(
        retrieval_url=RS_FIMP_RETRIEVAL_URL,
        expected_sha256="d" * 64,
        secondary_url=RS_FIMP_IR_URL,
        secondary_expected_sha256=ir_sha,
        spec_overrides={
            "assurance_key": "rs-260-28",
            "house": "Rajya Sabha",
            "house_code": "RS",
            "loksabha": None,
            "session": "260",
            "assurance_ref": "RS-260/28",
            "oassu_no": "260/28",
            "question_type": "Unstarred",
            "question_no": "28",
            "promise_date": "2023-08-03",
            "ministry": "Health and Family Welfare",
            "ministry_key": "health-and-family-welfare",
            "recorded_status": "fully_implemented",
            "raw_status": "FIMP",
            "secondary_role": "implementation_report_pdf",
            "portal_url": "https://sansad.in/rs/house-business/government-assurances",
            "is_anchor_case": False,
        },
        as_of_date="2026-11-27",
    )

    # 1. Multi-page scanned image-only PDF with ONLY eOffice header/footer watermarks (AM & pm variants)
    watermark_only_recs = [
        {**rs_fimp_row, "document": "primary"},
        {
            "page": 1,
            "raw_text": (
                "166937/2024/Comm Sec(Gov Asr)\n1\n"
                "File No. LAFEAS-CG11014/202/2023-Comm Sec(Gov Asr)-RSS (Computer No. 31599)\n"
                "Generated from eOffice by Clerk Name, ASO(PS)-Comm Sec(Gov Ass), ASO(S), RSS on 13/11/2026 03:04 PM"
            ),
            "document": "secondary",
            "secondary_sha256": ir_sha,
        },
        {
            "page": 2,
            "raw_text": (
                "265541/2025/Comm Sec(Gov Asr)\n2\n"
                "File No. LAFEAS-CG11011/87/2025-Comm Sec(Gov Asr)-RSS (Computer No. 43056)\n"
                "Generated from eOffice by Clerk Two, SSA(TA) - Gov Asr, SSA(Gov Asr), RSS on 09/10/2025 09:40 am"
            ),
            "document": "secondary",
            "secondary_sha256": ir_sha,
        },
    ]
    res_scan = run_engine(PARLIAMENTS_TO_DO_LIST_METHODOLOGY, watermark_only_recs, context=ctx)
    assert len(res_scan.leads) == 1
    lead = res_scan.leads[0]
    assert lead.rule_triggered == "P6-FOLLOWUP-GAP"
    assert lead.anomaly_metrics["followup_text_status"] == "scanned_ir_pdf_no_text_layer"
    assert lead.anomaly_metrics["secondary_pdf_chars"] == 0
    assert "Generated from eOffice" not in lead.anomaly_metrics["followup_answer"]
    assert "LAFEAS" not in lead.summary

    # 2. Digital IR PDF that ALSO carries an eOffice stamp -> stamp stripped, real reply preserved
    mixed_recs = [
        {**rs_fimp_row, "document": "primary"},
        {
            "page": 1,
            "raw_text": (
                "336767/2026/Comm Sec(Gov Asr)\n1\n"
                "IMPLEMENTATION REPORT LAID ON 10.10.2023\n"
                "Action Taken: Civil construction of 42 new district medical colleges has reached completion.\n"
                "File No. LAFEAS-CG11/78/2026-Comm Sec(Gov Asr)-RSS (Computer No. 50684)\n"
                "Generated from eOffice by Clerk Three, SA(NK)-Gov Asr, SA, RSS on 17/09/2026 05:37 pm"
            ),
            "document": "secondary",
            "secondary_sha256": ir_sha,
        },
    ]
    res_mixed = run_engine(PARLIAMENTS_TO_DO_LIST_METHODOLOGY, mixed_recs, context=ctx)
    assert res_mixed.leads == []
    assert res_mixed.cleared_count == 1
    assert "42 new district medical colleges" in res_mixed.summary
    assert "Generated from eOffice" not in res_mixed.summary
    assert "LAFEAS" not in res_mixed.summary


def test_p6_neutralizes_verbatim_parliament_text_and_handles_missing_record() -> None:
    # Verbatim text containing BNS Sec 356 forbidden words + phone number must neutralize & scrub cleanly
    spicy_row = {
        **_anchor_json_payload()["records"][0],
        "subject": "Measures against cyber fraud and bank scam complaints",
        "textOfAssurance": (
            "Action against anti-corruption and fraud complaints (helpline +91 9876543210) "
            "regarding stolen funds and bribery allegations is under review."
        ),
    }
    res = run_engine(
        PARLIAMENTS_TO_DO_LIST_METHODOLOGY,
        [spicy_row],
        context=_p6_ctx(as_of_date="2026-11-27"),
    )
    assert len(res.leads) == 1
    lead = res.leads[0]
    assert "9876543210" not in lead.summary
    assert "fraud" not in lead.summary.lower()
    assert "scam" not in lead.title.lower()
    assert "bribery" not in lead.summary.lower()

    # Unmatched record -> unevaluable_count = 1
    missing = run_engine(
        PARLIAMENTS_TO_DO_LIST_METHODOLOGY,
        [{"assuNo": "SQ-18/07/999", "status": "PENDING"}],
        context=_p6_ctx(as_of_date="2026-11-27"),
    )
    assert missing.leads == []
    assert missing.cleared_count == 0
    assert missing.unevaluable_count == 1


# ---------------------------------------------------------------------------
# 2. Validation, Loader Provenance, Secondary PDF & RTI Draft
# ---------------------------------------------------------------------------


def test_p6_project_validation_loader_and_rti_draft(tmp_path: Path) -> None:
    json_path = tmp_path / "sq544.json"
    pdf_path = tmp_path / "sq544.pdf"
    json_bytes = json.dumps(_anchor_json_payload()).encode("utf-8")
    pdf_bytes = _minimal_multipage_pdf_bytes(_anchor_question_pdf_pages())
    json_path.write_bytes(json_bytes)
    pdf_path.write_bytes(pdf_bytes)
    json_sha = compute_sha256(json_bytes)
    pdf_sha = compute_sha256(pdf_bytes)

    # Loader attaches secondary PDF pages cleanly for P6 and verifies BOTH digests fail-closed
    ctx = _p6_ctx(
        retrieval_url=str(json_path),
        expected_sha256=json_sha,
        secondary_url=str(pdf_path),
        secondary_expected_sha256=pdf_sha,
    )
    ds = load_dataset(
        str(json_path),
        context={**ctx, "secondary_path": str(pdf_path)},
        allow_local_files=True,
        expected_sha256=json_sha,
        secondary_expected_sha256=pdf_sha,
    )
    assert ds.digest_verified is True
    assert ds.secondary_digest_verified is True
    assert ds.secondary_sha256 == pdf_sha
    assert sum(1 for r in ds.records if r.get("document") == "primary") == 3
    assert sum(1 for r in ds.records if r.get("document") == "secondary") == 2

    with pytest.raises(DigestMismatchError):
        load_dataset(
            str(json_path),
            context={**ctx, "secondary_path": str(pdf_path)},
            allow_local_files=True,
            expected_sha256=json_sha,
            secondary_expected_sha256="f" * 64,
        )

    # Provenance helper recognizes sansad.in as Official Parliament of India (non-mirror)
    from aiwatchdogs.connectors.loader import _resolve_provenance

    prov = _resolve_provenance(
        ANCHOR_RETRIEVAL_URL,
        {},
        sha256=json_sha,
        expected_sha256=json_sha,
        digest_verified=True,
        byte_size=len(json_bytes),
        fetched_at="2026-11-27T00:00:00Z",
        source_locator="records=1-3",
    )
    assert prov["official_publisher"] == "Parliament of India (Digital Sansad)"
    assert prov["mirror_name"] is None

    # Project validation accepts valid P6 schema and rejects mismatched quantity type or bad context
    valid_proj = {
        "slug": PARLIAMENTS_TO_DO_LIST_PROJECT_SLUG,
        "project_code": "P6",
        "title": "Parliament's To-Do List: Tracking ministerial assurances across ministries",
        "summary": "A minister promised Parliament an answer. Did it ever get one?",
        "posted_by": {"name": "AI Watchdogs India", "handle": "@aiwatchdogsin", "url": "https://aiwatchdogs.in"},
        "pillar": "parliament",
        "status": "active",
        "priority": 6,
        "jurisdiction": "Union of India",
        "authority": "Union Ministries & Parliament of India (Digital Sansad)",
        "scope": ["Formal ministerial assurances across Lok Sabha and Rajya Sabha"],
        "out_of_scope": ["Private individuals"],
        "statutory_anchors": ["Lok-Sabha-Rule-323", "Rajya-Sabha-Rule-211A"],
        "sources": [{"name": "Digital Sansad", "publisher": "Parliament of India", "url": "https://sansad.in"}],
        "methodology_code": "P6",
        "tasks": [
            {
                "id": "p6-ls-18-07-sq-544",
                "title": "Road Transport and Highways · LS SQ-18/07/544 (Pending): Widening of NH-11 Road from Jhunjhunu to Fatehpur",
                "instructions": "Fetch Digital Sansad assurance JSON and Question PDF, verify SHA-256, and run P6 check.",
                "dataset_key": "p6-ls-18-07-sq-544",
                "estimated_tokens": 2500,
                "estimate_basis": "JSON + 2-page PDF deterministic check",
                "financial_quantity_type": "parliamentary_assurances",
                "methodology_code": "P6",
                "payload": {
                    "retrieval_url": ANCHOR_RETRIEVAL_URL,
                    "expected_sha256": json_sha,
                    "secondary_url": ANCHOR_SECONDARY_URL,
                    "secondary_expected_sha256": pdf_sha,
                    "official_publisher": "Parliament of India — Digital Sansad",
                    "source_landing_page": "https://sansad.in/ls/business/government-assurances",
                    "context": {
                        "parliaments_to_do_list": _p6_ctx(secondary_url=ANCHOR_SECONDARY_URL)["parliaments_to_do_list"],
                    },
                },
            }
        ],
    }
    assert validate_project(valid_proj, filename_stem=PARLIAMENTS_TO_DO_LIST_PROJECT_SLUG) == []

    bad_qty = json.loads(json.dumps(valid_proj))
    bad_qty["tasks"][0]["financial_quantity_type"] = "budget_and_treasury_expenditure"
    assert any("parliamentary_assurances" in e for e in validate_project(bad_qty, filename_stem=PARLIAMENTS_TO_DO_LIST_PROJECT_SLUG))

    bad_ctx = json.loads(json.dumps(valid_proj))
    bad_ctx["tasks"][0]["payload"]["context"]["parliaments_to_do_list"]["house_code"] = "RS"
    assert any("house and house_code do not match" in e for e in validate_project(bad_ctx, filename_stem=PARLIAMENTS_TO_DO_LIST_PROJECT_SLUG))

    # Citizen RTI draft routes to Central RTI portal and references SQ-18/07/544 + P6 records
    run_res = run_engine(PARLIAMENTS_TO_DO_LIST_METHODOLOGY, ds.records, context=ctx)
    rti = generate_rti_draft(run_res.leads[0])
    assert rti["jurisdiction_level"] == "central"
    assert rti["rti_portal_url"] == "https://rtionline.gov.in"
    assert "SQ-18/07/544" in rti["subject"]
    assert any("Implementation Report" in rec for rec in rti["requested_records"])


# ---------------------------------------------------------------------------
# 3. End-to-End Hub Endpoints, Visual Summary, Markdown & 2-Agent Peer Quorum
# ---------------------------------------------------------------------------


@pytest.mark.production_missions
def test_p6_hub_endpoints_visual_summary_and_two_agent_peer_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdir = tmp_path / "projects"
    p6_dir = pdir / PARLIAMENTS_TO_DO_LIST_PROJECT_SLUG
    p6_dir.mkdir(parents=True)

    anchor_json_bytes = json.dumps(_anchor_json_payload()).encode("utf-8")
    anchor_json_sha = compute_sha256(anchor_json_bytes)
    anchor_pdf_bytes = _minimal_multipage_pdf_bytes(_anchor_question_pdf_pages())
    anchor_pdf_sha = compute_sha256(anchor_pdf_bytes)

    rs_fimp_row = {
        "session": "270",
        "date": "2026-02-10",
        "ministry": "HEALTH AND FAMILY WELFARE",
        "member": "Dr. Test MP",
        "subject": "AIIMS faculty vacancies",
        "questionType": "Unstarred",
        "status": "FIMP",
        "extent": "Recruitment status across new AIIMS campuses is being compiled.",
        "extNo": 0,
        "irUrl": RS_FIMP_IR_URL,
        "qno": "139",
        "oassuNo": "270/139",
    }
    rs_json_bytes = json.dumps({"records": [rs_fimp_row]}).encode("utf-8")
    rs_json_sha = compute_sha256(rs_json_bytes)
    rs_pdf_bytes = _minimal_multipage_pdf_bytes([""])  # Scanned image-only IR PDF
    rs_pdf_sha = compute_sha256(rs_pdf_bytes)

    proj = {
        "slug": PARLIAMENTS_TO_DO_LIST_PROJECT_SLUG,
        "project_code": "P6",
        "title": "Parliament's To-Do List: Tracking ministerial assurances across ministries",
        "summary": "A minister promised Parliament an answer. Did it ever get one? Tracks formal assurances across Union Ministries.",
        "posted_by": {"name": "AI Watchdogs India", "handle": "@aiwatchdogsin", "url": "https://aiwatchdogs.in"},
        "pillar": "parliament",
        "status": "active",
        "priority": 6,
        "jurisdiction": "Union of India",
        "authority": "Union Ministries & Parliament of India (Digital Sansad)",
        "scope": ["Formal ministerial assurances across Lok Sabha and Rajya Sabha"],
        "out_of_scope": ["Private individuals"],
        "statutory_anchors": ["Lok-Sabha-Rule-323", "Rajya-Sabha-Rule-211A"],
        "sources": [{"name": "Digital Sansad", "publisher": "Parliament of India", "url": "https://sansad.in"}],
        "methodology_code": "P6",
        "tasks": [
            {
                "id": "p6-ls-18-07-sq-544",
                "title": "Road Transport and Highways · LS SQ-18/07/544 (Pending): Widening of NH-11 Road from Jhunjhunu to Fatehpur",
                "instructions": "Verify Digital Sansad SQ-18/07/544 JSON and Question PDF.",
                "dataset_key": "p6-ls-18-07-sq-544",
                "estimated_tokens": 2500,
                "estimate_basis": "JSON + 2-page PDF check",
                "financial_quantity_type": "parliamentary_assurances",
                "methodology_code": "P6",
                "payload": {
                    "retrieval_url": ANCHOR_RETRIEVAL_URL,
                    "expected_sha256": anchor_json_sha,
                    "secondary_url": ANCHOR_SECONDARY_URL,
                    "secondary_expected_sha256": anchor_pdf_sha,
                    "official_publisher": "Parliament of India — Digital Sansad",
                    "source_landing_page": "https://sansad.in/ls/business/government-assurances",
                    "as_of_date": "2026-11-27",
                    "context": {
                        "parliaments_to_do_list": _p6_ctx(secondary_url=ANCHOR_SECONDARY_URL)["parliaments_to_do_list"],
                    },
                },
            },
            {
                "id": "p6-rs-270-139",
                "title": "Health and Family Welfare · RS RS-270/139 (Fulfilled): AIIMS faculty vacancies",
                "instructions": "Verify Digital Sansad RS-270/139 JSON and Implementation Report PDF.",
                "dataset_key": "p6-rs-270-139",
                "estimated_tokens": 2500,
                "estimate_basis": "JSON + IR PDF check",
                "financial_quantity_type": "parliamentary_assurances",
                "methodology_code": "P6",
                "payload": {
                    "retrieval_url": RS_FIMP_RETRIEVAL_URL,
                    "expected_sha256": rs_json_sha,
                    "secondary_url": RS_FIMP_IR_URL,
                    "secondary_expected_sha256": rs_pdf_sha,
                    "official_publisher": "Parliament of India — Digital Sansad",
                    "source_landing_page": "https://sansad.in/rs/house-business/government-assurances",
                    "as_of_date": "2026-11-27",
                    "context": {
                        "parliaments_to_do_list": {
                            "assurance_key": "rs-270-139",
                            "house": "Rajya Sabha",
                            "house_code": "RS",
                            "loksabha": None,
                            "session": "270",
                            "assurance_ref": "RS-270/139",
                            "oassu_no": "270/139",
                            "question_type": "Unstarred",
                            "question_no": "139",
                            "promise_date": "2026-02-10",
                            "ministry": "Health and Family Welfare",
                            "ministry_key": "health-and-family-welfare",
                            "member": "Dr. Test MP",
                            "subject": "AIIMS faculty vacancies",
                            "recorded_status": "fully_implemented",
                            "raw_status": "FIMP",
                            "secondary_role": "implementation_report_pdf",
                            "portal_url": "https://sansad.in/rs/house-business/government-assurances",
                            "is_anchor_case": False,
                        }
                    },
                },
            },
        ],
    }
    (pdir / f"{PARLIAMENTS_TO_DO_LIST_PROJECT_SLUG}.json").write_text(json.dumps(proj), encoding="utf-8")

    coverage = {
        "project_slug": PARLIAMENTS_TO_DO_LIST_PROJECT_SLUG,
        "project_code": "P6",
        "methodology_code": "P6",
        "hook": "A minister promised Parliament an answer. Did it ever get one?",
        "generated_on": "2026-11-27",
        "as_of_date": "2026-11-27",
        "total_tasks": 2,
        "ministries_covered": 2,
        "by_house": {
            "Lok Sabha": {"tasks": 1, "pending": 1, "fully_implemented": 0, "partly_implemented": 0, "dropped": 0},
            "Rajya Sabha": {"tasks": 1, "pending": 0, "fully_implemented": 1, "partly_implemented": 0, "dropped": 0},
        },
        "by_recorded_status": {"pending": 1, "fully_implemented": 1, "partly_implemented": 0, "dropped": 0},
        "anchor_case": {
            "task_id": "p6-ls-18-07-sq-544",
            "assurance_ref": "SQ-18/07/544",
            "house": "Lok Sabha",
            "session": "7",
            "promise_date": "2026-04-02",
            "ministry": "Road Transport and Highways",
            "member": "Shri Brijendra Singh Ola",
            "minister": "Shri Nitin Jairam Gadkari",
            "subject": "Widening of Nh-11 Road from Jhunjhunu to Fatehpur",
            "recorded_status": "pending",
            "days_elapsed": 239,
            "original_promise": "DPR for development of NH-11 from Jhunjhunu to Fatehpur to four-lanes has been initiated.",
            "followup_answer": "No Implementation Statement laid; still listed as PENDING on Digital Sansad.",
            "source_url": ANCHOR_RETRIEVAL_URL,
            "secondary_url": ANCHOR_SECONDARY_URL,
        },
        "ministries": [
            {
                "ministry": "Road Transport and Highways",
                "ministry_key": "road-transport-and-highways",
                "tasks": 1,
                "lok_sabha_tasks": 1,
                "rajya_sabha_tasks": 0,
                "pending_count": 1,
                "fulfilled_count": 0,
                "partly_implemented_count": 0,
                "dropped_count": 0,
                "leads_in_dry_run": 1,
            },
            {
                "ministry": "Health and Family Welfare",
                "ministry_key": "health-and-family-welfare",
                "tasks": 1,
                "lok_sabha_tasks": 0,
                "rajya_sabha_tasks": 1,
                "pending_count": 0,
                "fulfilled_count": 1,
                "partly_implemented_count": 0,
                "dropped_count": 0,
                "leads_in_dry_run": 1,
            },
        ],
        "documents_count": 4,
    }
    (p6_dir / "coverage.json").write_text(json.dumps(coverage), encoding="utf-8")
    (p6_dir / "documents.csv").write_text(
        "task_id,role,url,sha256\n"
        f"p6-ls-18-07-sq-544,primary,{ANCHOR_RETRIEVAL_URL},{anchor_json_sha}\n"
        f"p6-ls-18-07-sq-544,question_pdf,{ANCHOR_SECONDARY_URL},{anchor_pdf_sha}\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("AIWATCHDOGS_PROJECTS_DIR", str(pdir))
    monkeypatch.delenv("AIWATCHDOGS_KEEP_LEGACY_MISSIONS", raising=False)
    app = create_app(db_path=str(tmp_path / "p6_hub.db"), seed=True)

    url_map: dict[str, tuple[bytes, str]] = {
        ANCHOR_RETRIEVAL_URL: (anchor_json_bytes, "application/json"),
        ANCHOR_SECONDARY_URL: (anchor_pdf_bytes, "application/pdf"),
        RS_FIMP_RETRIEVAL_URL: (rs_json_bytes, "application/json"),
        RS_FIMP_IR_URL: (rs_pdf_bytes, "application/pdf"),
    }

    import httpx

    orig_get = httpx.Client.get
    orig_stream = httpx.Client.stream

    class _FakeResp:
        def __init__(self, content: bytes, content_type: str) -> None:
            self.status_code = 200
            self.headers = {"Content-Type": content_type, "Content-Length": str(len(content))}
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
        hit = url_map.get(str(url))
        if hit is not None:
            return _FakeResp(hit[0], hit[1])
        return orig_stream(self, method, url, **kwargs)

    def _fake_get(self: Any, url: str, **kwargs: Any):
        hit = url_map.get(str(url))
        if hit is not None:
            return _FakeResp(hit[0], hit[1])
        return orig_get(self, url, **kwargs)

    monkeypatch.setattr(httpx.Client, "stream", _fake_stream)
    monkeypatch.setattr(httpx.Client, "get", _fake_get)

    with TestClient(app, base_url="http://testserver") as client:
        missions = {m["slug"]: m for m in client.get("/api/missions").json()["missions"]}
        assert PARLIAMENTS_TO_DO_LIST_PROJECT_SLUG in missions
        assert missions[PARLIAMENTS_TO_DO_LIST_PROJECT_SLUG]["project_code"] == "P6"
        assert missions[PARLIAMENTS_TO_DO_LIST_PROJECT_SLUG]["pillar"] == "parliament"

        detail = client.get(f"/api/missions/{PARLIAMENTS_TO_DO_LIST_PROJECT_SLUG}").json()
        assert detail["project_code"] == "P6"
        vs = detail["visual_summary"]
        assert vs["methodology_code"] == "P6"
        p6_vs = vs["parliaments_to_do_list"]
        assert p6_vs["total_tasks"] == 2
        assert p6_vs["ministries_covered"] == 2
        assert p6_vs["by_recorded_status"] == {
            "pending": 1,
            "fully_implemented": 1,
            "partly_implemented": 0,
            "dropped": 0,
        }
        assert p6_vs["anchor_case"]["assurance_ref"] == "SQ-18/07/544"
        assert len(p6_vs["ministries"]) == 2
        assert p6_vs["ministries"][0]["ministry"] == "Road Transport and Highways"
        assert p6_vs["ministries"][0]["pending_count"] == 1

        assert client.get(f"/api/missions/{PARLIAMENTS_TO_DO_LIST_PROJECT_SLUG}/documents.csv").status_code == 200
        assert client.get(f"/api/missions/{PARLIAMENTS_TO_DO_LIST_PROJECT_SLUG}/coverage.json").status_code == 200
        md_resp = client.get(f"/projects/{PARLIAMENTS_TO_DO_LIST_PROJECT_SLUG}.md")
        assert md_resp.status_code == 200
        assert "Parliament's To-Do List (P6)" in md_resp.text
        assert "SQ-18/07/544" in md_resp.text
        jt_resp = client.get("/join.txt")
        assert jt_resp.status_code == 200 and PARLIAMENTS_TO_DO_LIST_PROJECT_SLUG in jt_resp.text
        brief_resp = client.get("/brief.md")
        assert brief_resp.status_code == 200 and PARLIAMENTS_TO_DO_LIST_PROJECT_SLUG in brief_resp.text

        # Run Auditor + 2 independent Peer Verifiers on the Jhunjhunu-Fatehpur anchor task!
        auditor = AgentRunner(
            AgentConfig(
                handle="p6-auditor-one",
                hub_url="http://testserver",
                provider="rule-engine",
                model="deterministic-cpu-v1",
                mode="investigate",
                max_tasks=1,
            ),
            http_client=client,
            config_path=tmp_path / "auditor.json",
        )
        res_a = auditor.run_loop(
            max_tasks=1,
            preferred_mission=PARLIAMENTS_TO_DO_LIST_PROJECT_SLUG,
            task_id="prj-p6-ls-18-07-sq-544",
        )
        assert res_a.findings_submitted == 1

        for idx in (1, 2):
            pv = AgentRunner(
                AgentConfig(
                    handle=f"p6-verifier-{idx}",
                    hub_url="http://testserver",
                    provider="rule-engine",
                    model="deterministic-cpu-v1",
                    mode="verify",
                    max_tasks=1,
                ),
                http_client=client,
                config_path=tmp_path / f"pv{idx}.json",
            )
            res_pv = pv.run_loop(max_tasks=1, preferred_mission=PARLIAMENTS_TO_DO_LIST_PROJECT_SLUG)
            assert res_pv.verifications_submitted == 1

        findings = client.get(f"/api/findings?mission={PARLIAMENTS_TO_DO_LIST_PROJECT_SLUG}").json()["findings"]
        assert len(findings) == 1
        assert findings[0]["status"] == "verified"
        assert findings[0]["methodology_code"] == "P6"
        assert findings[0]["anomaly_metrics"]["assurance_ref"] == "SQ-18/07/544"
        assert findings[0]["anomaly_metrics"]["minister"] == "Shri Nitin Jairam Gadkari"

        f_detail = client.get(f"/api/findings/{findings[0]['id']}").json()
        assert len(f_detail["citations"]) == 4
        assert [c["role"] for c in f_detail["citations"]] == [
            "original_promise",
            "original_question_pdf",
            "recorded_status",
            "follow_up_answer",
        ]
        f_md = client.get(f"/findings/{findings[0]['id']}.md")
        assert f_md.status_code == 200
        assert ANCHOR_RETRIEVAL_URL in f_md.text
        assert ANCHOR_SECONDARY_URL in f_md.text

        # Visual summary reflects live verified anchor finding & ministry progress
        after = client.get(f"/api/missions/{PARLIAMENTS_TO_DO_LIST_PROJECT_SLUG}").json()["visual_summary"]["parliaments_to_do_list"]
        assert after["leads"] == 1
        assert after["verified"] == 1
        assert after["anchor_case"]["finding_id"] == findings[0]["id"]
        assert after["top_leads"][0]["assurance_ref"] == "SQ-18/07/544"
        assert after["ministries"][0]["done"] == 1
        assert after["ministries"][0]["leads"] == 1


def test_generated_p6_project_json_and_coverage_when_present() -> None:
    """Validate the real generated P6 dataset (1,000 tasks + coverage + documents) when present in projects/."""
    project_path = PROJECTS_DIR / f"{PARLIAMENTS_TO_DO_LIST_PROJECT_SLUG}.json"
    if not project_path.exists():
        pytest.skip("projects/parliaments-to-do-list.json not yet generated on this branch")

    data = json.loads(project_path.read_text(encoding="utf-8"))
    assert data["project_code"] == "P6"
    assert data["methodology_code"] == "P6"
    assert data["pillar"] == "parliament"
    assert "A minister promised Parliament an answer. Did it ever get one?" in data["summary"]
    assert len(data["tasks"]) == 1000
    assert validate_project(data, filename_stem=PARLIAMENTS_TO_DO_LIST_PROJECT_SLUG) == []

    # Task #1 is the Jhunjhunu-Fatehpur anchor case
    first = data["tasks"][0]
    assert first["id"] == "p6-ls-18-07-sq-544"
    assert first["payload"]["context"]["parliaments_to_do_list"]["assurance_ref"] == "SQ-18/07/544"

    ministries = {t["payload"]["context"]["parliaments_to_do_list"]["ministry"] for t in data["tasks"]}
    assert len(ministries) >= 40

    cov = json.loads((PROJECTS_DIR / PARLIAMENTS_TO_DO_LIST_PROJECT_SLUG / "coverage.json").read_text(encoding="utf-8"))
    assert cov["total_tasks"] == 1000
    assert cov["project_code"] == "P6"
    assert cov["anchor_case"]["assurance_ref"] == "SQ-18/07/544"

    with (PROJECTS_DIR / PARLIAMENTS_TO_DO_LIST_PROJECT_SLUG / "documents.csv").open("r", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) >= 1000
