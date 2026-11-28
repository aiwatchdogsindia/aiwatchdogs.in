"""Comprehensive unit and integration tests for Project 10: Innocent But Locked Up (P10)."""

from __future__ import annotations

import copy
import io
import json
import tarfile
import zipfile
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from aiwatchdogs.cli.runner import (
    CRITICAL_SEMANTIC_METRIC_KEYS,
    RECORD_IDENTIFIER_KEYS,
    _ALLOWED_TASK_CONTEXT_KEYS,
    _build_task_context,
)
from aiwatchdogs.connectors.loader import DigestMismatchError, load_dataset
from aiwatchdogs.core.rti import generate_rti_draft
from aiwatchdogs.core.safety import (
    compute_sha256,
    contains_pii,
    lint_neutrality,
    verhoeff_generate_check_digit,
)
from aiwatchdogs.engines.innocent_but_locked_up import (
    INNOCENT_BUT_LOCKED_UP_METHODOLOGY,
    INNOCENT_BUT_LOCKED_UP_PROJECT_SLUG,
    OVERRUN_MECHANISM_RULES,
    P10_DEFAULT_HOOK,
    P10_TASK_ID_RE,
    PORTAL_CATEGORY_LABELS,
    SIGNAL_CATEGORIES,
    evaluate_custody_overrun_rule,
)
from aiwatchdogs.engines.registry import METHODOLOGY_CATALOG, RULESET_VERSIONS, get_engine_fingerprint, run_engine
from aiwatchdogs.hub.app import create_app
from aiwatchdogs.projects import CANONICAL_PROJECT_CODES, VALID_PILLARS, VALID_QUANTITY_TYPES, validate_project


def _make_valid_aadhaar(prefix_11: str) -> str:
    assert len(prefix_11) == 11 and prefix_11[0] in "23456789"
    return prefix_11 + verhoeff_generate_check_digit(prefix_11)


def _create_pdf_bytes(pages_lines: list[list[str]]) -> bytes:
    """Create a real valid multi-page PDF with extractable text and return its bytes."""
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    for text_lines in pages_lines:
        page = writer.add_blank_page(width=612, height=792)
        font_dict = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        font_ref = writer._add_object(font_dict)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
        )
        ops = ["BT", "/F1 11 Tf", "50 740 Td", "15 TL"]
        for idx, line in enumerate(text_lines):
            safe = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            ops.append(f"({safe}) Tj" if idx == 0 else f"T* ({safe}) Tj")
        ops.append("ET")
        stream = DecodedStreamObject()
        stream.set_data("\n".join(ops).encode("latin-1", errors="replace"))
        page[NameObject("/Contents")] = writer._add_object(stream)

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _create_pdf(path: Path, pages_lines: list[list[str]]) -> str:
    raw = _create_pdf_bytes(pages_lines)
    path.write_bytes(raw)
    return compute_sha256(raw)


def _sample_anchor_spec(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "case_key": "p10-court-2025-ghaziabad-anchor-0001",
        "case_title": "District Jail Ghaziabad — 28-Day Custody Overrun After Supreme Court Release Order",
        "portal_category": "court_portal",
        "court_tier": "supreme_court",
        "state_ut": "Uttar Pradesh",
        "jurisdiction": "State:Uttar Pradesh",
        "year": 2025,
        "document_date": "2025-06-25",
        "document_ref": "Supreme Court Order in M.A. No. 1086 of 2025 in Crl.A. No. 2295 of 2025",
        "overrun_mechanism": "post_acquittal_contempt_overrun",
        "is_anchor_case": True,
        "stage_1_acquittal_verified": True,
        "acquittal_order_date": "2025-04-29",
        "acquittal_court": "Supreme Court of India",
        "acquittal_case_number": "Crl.A. No. 2295 of 2025",
        "acquittal_order_type": "bail_release_order",
        "acquittal_quote": (
            "Supreme Court directed release of the appellant on bail subject to trial court terms, "
            "followed by Trial Court release order dated 27 May 2025."
        ),
        "stage_2_continued_detention_verified": True,
        "prison_name": "District Jail, Ghaziabad",
        "custody_authority": "Superintendent, District Jail, Ghaziabad & Uttar Pradesh Prisons Administration",
        "non_release_reason": "Prison authorities cited omission of a statutory sub-section reference in the release warrant.",
        "overrun_days": 28,
        "overrun_window": "2025-05-27 to 2025-06-24 (28 days)",
        "stage_3_contempt_verified": True,
        "contempt_case_number": "M.A. No. 1086 of 2025 (Contempt & Compliance Direction)",
        "contempt_court": "Supreme Court of India",
        "contempt_filing_date": "2025-06-10",
        "contempt_order_date": "2025-06-25",
        "contempt_quote": (
            "Court summoned the Director General of Prisons and Superintendent, District Jail Ghaziabad "
            "and ordered a judicial inquiry into continued detention despite unambiguous release orders."
        ),
        "stage_4_release_verified": True,
        "release_date": "2025-06-24",
        "release_verified": True,
        "compensation_inr": 500_000.0,
        "judicial_direction_summary": (
            "Prisoner released on 24 June 2025 after Supreme Court intervention; State of Uttar Pradesh directed "
            "to pay INR 5,00,000 provisional compensation and conduct judicial inquiry."
        ),
        "custody_order_evidence_tokens": ["Crl.A. No. 2295", "District Jail, Ghaziabad", "release order"],
        "contempt_or_outcome_evidence_tokens": ["M.A. No. 1086", "5,00,000", "judicial inquiry"],
        "statutory_anchor": "Constitution of India Art. 21; Contempt of Courts Act 1971 Sec. 2(b)/12; SC FASTER Guidelines",
        "benign_explanation_context": (
            "Prison authorities stated they awaited court clarification regarding an omitted sub-section number in the parwana."
        ),
        "source_format": "pdf",
        "secondary_source_format": "pdf",
        "secondary_role": "contempt_order_document",
        "record_selector": {"order_page": 1, "contempt_page": 2, "section_hint": "Order Para 3-9"},
    }
    base.update(overrides)
    return base


def _sample_p10_task(
    task_id: str,
    spec: dict[str, Any],
    *,
    retrieval_url: str = "https://api.sci.gov.in/supremecourt/2025/2295/order_20250429.pdf",
    expected_sha256: str = "a" * 64,
    secondary_url: str | None = "https://api.sci.gov.in/supremecourt/2025/1086/order_20250625.pdf",
    secondary_expected_sha256: str | None = "b" * 64,
    qty_type: str = "custody_overrun_and_contempt",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "retrieval_url": retrieval_url,
        "expected_sha256": expected_sha256,
        "official_publisher": "Supreme Court of India",
        "source_landing_page": "https://www.sci.gov.in/",
        "as_of_date": "2026-11-28",
        "context": {
            "as_of_date": "2026-11-28",
            "innocent_but_locked_up": spec,
        },
    }
    if secondary_url is not None:
        payload["secondary_url"] = secondary_url
        if secondary_expected_sha256 is not None:
            payload["secondary_expected_sha256"] = secondary_expected_sha256
    return {
        "id": task_id,
        "title": f"{spec['case_title']}"[:140],
        "instructions": "Verify SHA-256 digests and run deterministic P10 custody-overrun and contempt verification.",
        "type": "run_check",
        "methodology_code": "P10",
        "dataset_key": f"p10-{spec['portal_category']}",
        "financial_quantity_type": qty_type,
        "estimated_tokens": 1800,
        "estimate_basis": "Deterministic P10 official court/prison PDF verification (zero LLM cost).",
        "payload": payload,
    }


def _sample_p10_project(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "slug": INNOCENT_BUT_LOCKED_UP_PROJECT_SLUG,
        "project_code": "P10",
        "priority": 10,
        "title": "Innocent But Locked Up: Post-Acquittal Detention Overruns & Contempt",
        "summary": (
            "Audits 2021–2026 Indian court, prison, police, NHRC/SHRC, NALSA/SLSA, Parliament, and CAG records "
            "where acquitted or released persons remained detained until contempt or judicial intervention secured release."
        ),
        "posted_by": {
            "name": "AI Watchdogs India",
            "handle": "@aiwatchdogsIN",
            "url": "https://aiwatchdogs.in",
        },
        "pillar": "justice",
        "status": "active",
        "methodology_code": "P10",
        "jurisdiction": "India (Supreme Court, 25 High Courts, States & Union Territories)",
        "authority": "Supreme Court of India, High Courts, State Prison Departments, NHRC & NALSA",
        "statutory_anchors": [
            "Constitution-Art-21",
            "Contempt-of-Courts-Act-1971",
            "BNSS-2023-Sec-479-482",
            "SC-FASTER-Compliance-Directives",
            "RTI-Act-2005-Sec-4-1-b",
        ],
        "scope": [
            "Deterministic verification of 2021–2026 court orders, contempt proceedings, prison compliance records, NHRC/NALSA orders, and Parliament/CAG records.",
            "Tracks the 4-stage custody chain (acquittal/release order -> continued detention -> contempt action -> release) alongside verified timely-release controls.",
        ],
        "out_of_scope": [
            "Unverified media reports lacking checksummed official court, statutory commission, prison, or parliamentary records.",
        ],
        "sources": [
            {
                "name": "Supreme Court of India & AWS Open Data eSCR / High Court Judgments",
                "publisher": "Supreme Court of India & High Courts of India",
                "url": "https://www.sci.gov.in/",
            }
        ],
        "tasks": tasks,
    }


# ---------------------------------------------------------------------------
# 1. Registry, Project Registration & Flagship 4-Stage Dual-PDF Test
# ---------------------------------------------------------------------------


def test_p10_registration_and_anchor_dual_pdf_end_to_end(tmp_path: Path) -> None:
    assert CANONICAL_PROJECT_CODES[INNOCENT_BUT_LOCKED_UP_PROJECT_SLUG] == "P10"
    assert "justice" in VALID_PILLARS
    assert "custody_overrun_and_contempt" in VALID_QUANTITY_TYPES
    assert "P10" in METHODOLOGY_CATALOG
    assert RULESET_VERSIONS["P10"] == "P10-2026.11-v1"
    assert "innocent_but_locked_up" in _ALLOWED_TASK_CONTEXT_KEYS
    assert {
        "overrun_mechanism",
        "portal_category",
        "court_tier",
        "chain_status",
        "stage_1_acquittal_verified",
        "stage_2_continued_detention_verified",
        "stage_3_contempt_verified",
        "stage_4_release_verified",
        "release_verified",
    } <= CRITICAL_SEMANTIC_METRIC_KEYS
    assert {"acquittal_case_number", "contempt_case_number"} <= set(RECORD_IDENTIFIER_KEYS)

    order_pdf = tmp_path / "release_order_2025.pdf"
    order_sha = _create_pdf(
        order_pdf,
        [
            [
                "IN THE SUPREME COURT OF INDIA - Crl.A. No. 2295 of 2025",
                "Order dated 29 April 2025 directing immediate release order for the appellant",
                "lodged in District Jail, Ghaziabad upon furnishing bail bonds.",
            ]
        ],
    )
    contempt_pdf = tmp_path / "contempt_order_2025.pdf"
    contempt_sha = _create_pdf(
        contempt_pdf,
        [
            ["SUPREME COURT OF INDIA - M.A. No. 1086 of 2025"],
            [
                "Order dated 25 June 2025 in M.A. No. 1086 of 2025:",
                "Despite unambiguous release order, prisoner was kept in District Jail, Ghaziabad for 28 days.",
                "State directed to pay Rs. 5,00,000 provisional compensation and initiate a judicial inquiry.",
            ],
        ],
    )

    spec = _sample_anchor_spec()
    ctx = {
        "as_of_date": "2026-11-28",
        "retrieval_url": "https://api.sci.gov.in/supremecourt/2025/2295/order.pdf",
        "expected_sha256": order_sha,
        "secondary_path": str(contempt_pdf),
        "secondary_url": "https://api.sci.gov.in/supremecourt/2025/1086/order.pdf",
        "secondary_expected_sha256": contempt_sha,
        "official_publisher": "Supreme Court of India",
        "source_landing_page": "https://www.sci.gov.in/",
        "innocent_but_locked_up": spec,
    }
    ds = load_dataset(
        str(order_pdf),
        context={**ctx, "secondary_url": str(contempt_pdf)},
        allow_local_files=True,
        expected_sha256=order_sha,
        secondary_expected_sha256=contempt_sha,
    )
    assert ds.digest_verified is True
    assert ds.secondary_digest_verified is True
    assert ds.sha256 == order_sha
    assert ds.secondary_sha256 == contempt_sha
    assert any(r.get("document") == "primary" for r in ds.records)
    assert any(r.get("document") == "secondary" for r in ds.records)

    res = run_engine("P10", ds.records, context=ctx, as_of_date="2026-11-28")
    assert res.unevaluable_count == 0
    assert res.cleared_count == 0
    assert len(res.leads) == 1

    lead = res.leads[0]
    assert lead.methodology_code == "P10"
    assert lead.rule_triggered == "P10-POST-ACQUITTAL-CONTEMPT-OVERRUN"
    assert lead.severity == "critical"
    assert lead.jurisdiction == "State:Uttar Pradesh"
    assert lead.monetary_exposure_inr == 500_000.0
    assert lead.amount_type == "custody_overrun_and_contempt"

    am = lead.anomaly_metrics
    assert am["case_key"] == "p10-court-2025-ghaziabad-anchor-0001"
    assert am["is_anchor_case"] is True
    assert am["stages_verified_count"] == 4
    assert am["chain_status"] == "full_4_stage_chain"
    assert am["overrun_days"] == 28
    assert am["compensation_inr"] == 500_000.0
    assert am["release_verified"] is True
    assert "P10-POST-ACQUITTAL-CONTEMPT-OVERRUN" in am["triggered_rules"]
    assert "P10-FULL-4-STAGE-CHAIN" in am["triggered_rules"]
    assert "P10-CONTEMPT-ACTION-VERIFIED" in am["triggered_rules"]
    assert "P10-OVERRUN-GE-7D" in am["triggered_rules"]
    assert "P10-COMPENSATION-ORDERED" in am["triggered_rules"]
    assert "P10-FLAGSHIP-ANCHOR-CASE" in am["triggered_rules"]
    assert am["custody_order_locator"].startswith("page=1")
    assert am["contempt_or_outcome_locator"].startswith("page=2")

    assert len(lead.citations) == 3
    roles = {c["role"]: c for c in lead.citations}
    assert roles["acquittal_and_custody_order"]["sha256"] == order_sha
    assert roles["acquittal_and_custody_order"]["source_url"] == "https://api.sci.gov.in/supremecourt/2025/2295/order.pdf"
    assert roles["contempt_and_release_outcome"]["sha256"] == contempt_sha
    assert roles["contempt_and_release_outcome"]["source_url"] == "https://api.sci.gov.in/supremecourt/2025/1086/order.pdf"
    assert roles["contempt_and_release_outcome"]["page"] == 2

    assert lint_neutrality(lead.title, lead.summary, lead.benign_explanations).passed
    assert not contains_pii(lead.model_dump())[0]

    # Deterministic fingerprint & reproducibility check
    res2 = run_engine("P10", ds.records, context=ctx, as_of_date="2026-11-28")
    assert res.script_hash == res2.script_hash
    assert res.leads[0].anomaly_metrics == res2.leads[0].anomaly_metrics
    fp = get_engine_fingerprint("P10", config=ctx, as_of_date="2026-11-28")
    assert fp["ruleset_version"] == "P10-2026.11-v1"


# ---------------------------------------------------------------------------
# 2. All 5 Flagged Mechanisms, Overrun Auto-Computation & Cleared Control
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mechanism", "portal_cat", "court_tier", "s3", "days", "comp", "expected_rule", "expected_sev"),
    [
        ("post_acquittal_contempt_overrun", "court_portal", "high_court", True, 120, 3_000_000.0, "P10-POST-ACQUITTAL-CONTEMPT-OVERRUN", "critical"),
        ("bail_or_release_order_contempt", "court_portal", "high_court", True, 14, None, "P10-RELEASE-ORDER-CONTEMPT-OVERRUN", "high"),
        ("warrant_or_faster_transmission_delay", "prison_portal", "statutory_body", False, 5, None, "P10-WARRANT-TRANSMISSION-DELAY", "medium"),
        ("statutory_commission_detention_finding", "nhrc_shrc", "statutory_body", False, 19, 200_000.0, "P10-STATUTORY-UNLAWFUL-DETENTION", "high"),
        ("institutional_compliance_gap", "parliament", "statutory_body", False, None, None, "P10-INSTITUTIONAL-COMPLIANCE-GAP", "medium"),
    ],
)
def test_p10_all_five_mechanisms_and_rules(
    mechanism: str,
    portal_cat: str,
    court_tier: str,
    s3: bool,
    days: int | None,
    comp: float | None,
    expected_rule: str,
    expected_sev: str,
) -> None:
    flagged, rule, sig, rules, sev, scount, cstatus = evaluate_custody_overrun_rule(
        overrun_mechanism=mechanism,
        stage_1_acquittal_verified=True,
        stage_2_continued_detention_verified=True,
        stage_3_contempt_verified=s3,
        stage_4_release_verified=True,
        overrun_days=days,
        compensation_inr=comp,
        is_anchor_case=False,
    )
    assert flagged is True
    assert rule == expected_rule
    assert sig == mechanism
    assert expected_rule in rules
    assert sev == expected_sev
    assert scount == (4 if s3 else 3)
    assert cstatus == ("full_4_stage_chain" if s3 else "partial_3_stage")

    spec = _sample_anchor_spec(
        case_key=f"p10-court-2024-{mechanism[:10].replace('_', '-')}-0010",
        portal_category=portal_cat,
        court_tier=court_tier,
        year=2024,
        document_date="2024-08-15",
        acquittal_order_date=None if days is None else "2024-07-01",
        release_date=None if days is None else "2024-08-10",
        overrun_mechanism=mechanism,
        is_anchor_case=False,
        stage_3_contempt_verified=s3,
        overrun_days=days,
        compensation_inr=comp,
        custody_order_evidence_tokens=["acquittal order", "Central Jail"],
        contempt_or_outcome_evidence_tokens=["compliance directive", "release verified"],
        secondary_role="",
        record_selector=None,
    )
    doc_records = [
        {
            "page": 1,
            "raw_text": (
                "Official record noting acquittal order sent to Central Jail and subsequent "
                "compliance directive confirming release verified with institutional follow-up."
            ),
        }
    ]
    res = run_engine("P10", doc_records, context={"innocent_but_locked_up": spec, "as_of_date": "2026-11-28"})
    assert len(res.leads) == 1
    assert res.cleared_count == 0
    assert res.unevaluable_count == 0
    assert res.leads[0].rule_triggered == expected_rule
    assert res.leads[0].severity == expected_sev


def test_p10_auto_computes_overrun_days_and_clears_compliant_control() -> None:
    # Auto-compute overrun_days when omitted from acquittal_order_date + release_date
    auto_spec = _sample_anchor_spec(
        overrun_days=None,
        acquittal_order_date="2025-03-01",
        release_date="2025-04-05",
        is_anchor_case=False,
        secondary_role="",
        record_selector=None,
        custody_order_evidence_tokens=["acquittal dated 1 March 2025", "Central Jail"],
        contempt_or_outcome_evidence_tokens=["contempt notice", "released on 5 April 2025"],
    )
    records = [
        {
            "page": 1,
            "raw_text": (
                "High Court noted acquittal dated 1 March 2025 was not executed by Central Jail until "
                "contempt notice issued; prisoner finally released on 5 April 2025."
            ),
        }
    ]
    res = run_engine("P10", records, context={"innocent_but_locked_up": auto_spec})
    assert len(res.leads) == 1
    assert res.leads[0].anomaly_metrics["overrun_days"] == 35
    assert "P10-OVERRUN-GE-30D" in res.leads[0].anomaly_metrics["triggered_rules"]

    # Cleared benchmark: timely_release_or_compliant
    clear_spec = _sample_anchor_spec(
        case_key="p10-prison-2025-faster-ok-0901",
        portal_category="prison_portal",
        court_tier="statutory_body",
        overrun_mechanism="timely_release_or_compliant",
        is_anchor_case=False,
        stage_2_continued_detention_verified=False,
        stage_3_contempt_verified=False,
        overrun_days=0,
        acquittal_order_date="2025-07-10",
        release_date="2025-07-10",
        compensation_inr=None,
        custody_order_evidence_tokens=["FASTER portal", "release warrant received"],
        contempt_or_outcome_evidence_tokens=["same-day release", "zero overrun"],
        secondary_role="",
        record_selector=None,
    )
    good_records = [
        {
            "page": 1,
            "raw_text": (
                "Prison compliance log: FASTER portal transmitted the court order and release warrant received "
                "at 14:10 hrs; prisoner processed for same-day release with zero overrun recorded."
            ),
        }
    ]
    res_clear = run_engine("P10", good_records, context={"innocent_but_locked_up": clear_spec})
    assert res_clear.leads == []
    assert res_clear.cleared_count == 1
    assert res_clear.unevaluable_count == 0
    assert "timely_release_or_compliant" in res_clear.summary

    # Cleared benchmark with unrelated bytes MUST fail closed as unevaluable (never auto-clear!)
    bad_records = [{"page": 1, "raw_text": "Unrelated municipal solid waste tender notice from 2024 with zero prison context."}]
    res_un = run_engine("P10", bad_records, context={"innocent_but_locked_up": clear_spec})
    assert res_un.leads == []
    assert res_un.cleared_count == 0
    assert res_un.unevaluable_count == 1


# ---------------------------------------------------------------------------
# 3. Fail-Closed Guarantees, TAR/ZIP Archives, HTML, Selectors & Digests
# ---------------------------------------------------------------------------


def test_p10_fail_closed_chronology_contradictions_and_selectors(tmp_path: Path) -> None:
    single_doc = [
        {
            "page": 1,
            "raw_text": (
                "Crl.A. No. 2295 at District Jail, Ghaziabad release order and M.A. No. 1086 "
                "ordering 5,00,000 compensation and judicial inquiry."
            ),
        }
    ]

    # Empty / scanned PDF -> unevaluable
    res_empty = run_engine("P10", [{"page": 1, "raw_text": "Page 1"}], context={"innocent_but_locked_up": _sample_anchor_spec(secondary_role="", record_selector=None)})
    assert res_empty.leads == [] and res_empty.unevaluable_count == 1

    # Chronology inconsistent: release_date before acquittal_order_date -> unevaluable
    bad_chrono = _sample_anchor_spec(acquittal_order_date="2025-06-10", release_date="2025-05-01", secondary_role="", record_selector=None)
    res_chrono = run_engine("P10", single_doc, context={"innocent_but_locked_up": bad_chrono})
    assert res_chrono.leads == [] and res_chrono.unevaluable_count == 1

    # Contradictory compliant spec (timely_release_or_compliant + stage_2 + overrun_days > 1) -> unevaluable
    bad_comp = _sample_anchor_spec(
        overrun_mechanism="timely_release_or_compliant",
        stage_2_continued_detention_verified=True,
        overrun_days=15,
        secondary_role="",
        record_selector=None,
    )
    res_comp = run_engine("P10", single_doc, context={"innocent_but_locked_up": bad_comp})
    assert res_comp.leads == [] and res_comp.cleared_count == 0 and res_comp.unevaluable_count == 1

    # Structured JSON record_selector matching vs missing
    json_records = [
        {"qno": "101", "subject": "Railways Electrification", "answer": "Unrelated railway line electrification reply.", "source_row_index": 1},
        {
            "qno": "2541",
            "subject": "FASTER System and Prison Release Delays",
            "answer": "Ministry of Home Affairs issued advisory on FASTER electronic transmission to prevent custody overrun after acquittal.",
            "source_row_index": 2,
        },
    ]
    sel_spec = _sample_anchor_spec(
        case_key="p10-parl-2024-ls-faster-0100",
        portal_category="parliament",
        court_tier="statutory_body",
        overrun_mechanism="institutional_compliance_gap",
        is_anchor_case=False,
        stage_3_contempt_verified=False,
        custody_order_evidence_tokens=["FASTER electronic transmission", "Ministry of Home Affairs"],
        contempt_or_outcome_evidence_tokens=["custody overrun", "advisory"],
        secondary_role="",
        record_selector={"match_field": "qno", "match_value": "2541"},
    )
    res_hit = run_engine("P10", json_records, context={"innocent_but_locked_up": sel_spec})
    assert len(res_hit.leads) == 1
    assert "row=2" in res_hit.leads[0].source_locator

    miss_spec = copy.deepcopy(sel_spec)
    miss_spec["record_selector"] = {"match_field": "qno", "match_value": "9999"}
    res_miss = run_engine("P10", json_records, context={"innocent_but_locked_up": miss_spec})
    assert res_miss.leads == [] and res_miss.unevaluable_count == 1

    # Corroborating dual-doc provenance: exact URL/SHA attribution and no primary SHA leakage
    corrob_spec = _sample_anchor_spec(secondary_role="corroborating_record", record_selector=None)
    corrob_records = [
        {
            "document": "primary",
            "page": 4,
            "raw_text": "High Court contempt order M.A. No. 1086 directing Rs. 5,00,000 compensation and judicial inquiry.",
        },
        {
            "document": "secondary",
            "page": 2,
            "raw_text": "Original acquittal and release order in Crl.A. No. 2295 addressed to District Jail, Ghaziabad.",
            "secondary_sha256": "b" * 64,
        },
    ]
    res_corrob = run_engine(
        "P10",
        corrob_records,
        context={
            "innocent_but_locked_up": corrob_spec,
            "retrieval_url": "https://api.sci.gov.in/contempt.pdf",
            "secondary_url": "https://api.sci.gov.in/acquittal.pdf",
            "expected_sha256": "a" * 64,
            "secondary_expected_sha256": "b" * 64,
        },
    )
    assert len(res_corrob.leads) == 1
    cits = {c["role"]: c for c in res_corrob.leads[0].citations}
    assert cits["acquittal_and_custody_order"]["source_url"] == "https://api.sci.gov.in/acquittal.pdf"
    assert cits["acquittal_and_custody_order"]["sha256"] == "b" * 64
    assert cits["acquittal_and_custody_order"]["page"] == 2
    assert cits["contempt_and_release_outcome"]["source_url"] == "https://api.sci.gov.in/contempt.pdf"
    assert cits["contempt_and_release_outcome"]["sha256"] == "a" * 64
    assert cits["contempt_and_release_outcome"]["page"] == 4


def test_p10_loader_tar_zip_html_and_digest_verification(tmp_path: Path) -> None:
    # 1. TAR.GZ archive containing two PDFs; select exact archive_member!
    pdf_a = _create_pdf_bytes([["Unrelated civil property partition appeal from 2022."]])
    pdf_b = _create_pdf_bytes(
        [
            [
                "CHHATTISGARH HIGH COURT - WPCR 505 of 2025",
                "Accused acquitted on 20 January 2018 remained in Central Jail Durg until 19 May 2025.",
                "Court ordered Rs. 30,00,000 compensation and strict prison release audit.",
            ]
        ]
    )
    tar_path = tmp_path / "hc_orders_2026.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tf:
        for name, blob in (("2026/civil_001.pdf", pdf_a), ("2026/wpcr_505_2025.pdf", pdf_b)):
            ti = tarfile.TarInfo(name=name)
            ti.size = len(blob)
            tf.addfile(ti, io.BytesIO(blob))
    tar_sha = compute_sha256(tar_path.read_bytes())

    tar_spec = _sample_anchor_spec(
        case_key="p10-court-2026-cghc-durg-0002",
        state_ut="Chhattisgarh",
        jurisdiction="State:Chhattisgarh",
        year=2026,
        document_date="2026-05-01",
        custody_order_evidence_tokens=["WPCR 505 of 2025", "Central Jail Durg"],
        contempt_or_outcome_evidence_tokens=["30,00,000", "prison release audit"],
        source_format="tar",
        secondary_source_format=None,
        secondary_role="",
        record_selector={"archive_member": "2026/wpcr_505_2025.pdf"},
    )
    ds_tar = load_dataset(
        str(tar_path),
        context={"innocent_but_locked_up": tar_spec},
        allow_local_files=True,
        expected_sha256=tar_sha,
    )
    assert ds_tar.digest_verified is True
    assert ds_tar.format == "pdf"
    assert "tar_member=2026/wpcr_505_2025.pdf" == ds_tar.source_locator

    res_tar = run_engine("P10", ds_tar.records, context={"innocent_but_locked_up": tar_spec})
    assert len(res_tar.leads) == 1
    assert "2026/wpcr_505_2025.pdf:page=1" in res_tar.leads[0].source_locator

    # Missing archive_member -> empty records -> fail closed unevaluable!
    miss_tar_spec = copy.deepcopy(tar_spec)
    miss_tar_spec["record_selector"] = {"archive_member": "2026/does_not_exist.pdf"}
    ds_tar_miss = load_dataset(str(tar_path), context={"innocent_but_locked_up": miss_tar_spec}, allow_local_files=True)
    res_tar_miss = run_engine("P10", ds_tar_miss.records, context={"innocent_but_locked_up": miss_tar_spec})
    assert res_tar_miss.leads == [] and res_tar_miss.unevaluable_count == 1

    # 2. ZIP archive containing PDF + HTML visible text extraction
    zip_path = tmp_path / "orders.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("orders/wpcr_505_2025.pdf", pdf_b)
    zip_sha = compute_sha256(zip_path.read_bytes())
    zip_spec = copy.deepcopy(tar_spec)
    zip_spec["source_format"] = "zip"
    zip_spec["record_selector"] = {"archive_member": "orders/wpcr_505_2025.pdf"}
    ds_zip = load_dataset(str(zip_path), context={"innocent_but_locked_up": zip_spec}, allow_local_files=True, expected_sha256=zip_sha)
    assert ds_zip.digest_verified is True
    assert len(run_engine("P10", ds_zip.records, context={"innocent_but_locked_up": zip_spec}).leads) == 1

    html_path = tmp_path / "nhrc_press.html"
    html_path.write_text(
        "<html><head><script>var ignore='x';</script></head><body><h1>NHRC Order</h1>"
        "<p>Prisoner acquitted in WPCR 505 of 2025 kept in Central Jail Durg; Rs. 30,00,000 relief and prison release audit.</p>"
        "</body></html>",
        encoding="utf-8",
    )
    html_spec = copy.deepcopy(tar_spec)
    html_spec["source_format"] = "html"
    html_spec["record_selector"] = None
    ds_html = load_dataset(str(html_path), context={"innocent_but_locked_up": html_spec}, allow_local_files=True)
    assert ds_html.format == "html"
    assert "ignore" not in ds_html.raw_text
    assert len(run_engine("P10", ds_html.records, context={"innocent_but_locked_up": html_spec}).leads) == 1

    # Digest mismatch fails closed
    with pytest.raises(DigestMismatchError):
        load_dataset(str(tar_path), allow_local_files=True, expected_sha256="0" * 64)


# ---------------------------------------------------------------------------
# 4. BNS Sec 356 Neutrality (incl. "not guilty" / "held guilty of contempt") & PII Scrubbing
# ---------------------------------------------------------------------------


def test_p10_neutralizes_not_guilty_and_scrubs_all_pii() -> None:
    aadhaar = _make_valid_aadhaar("48291037462")
    spec = _sample_anchor_spec(
        secondary_role="",
        record_selector=None,
        custody_order_evidence_tokens=["District Jail, Ghaziabad", "acquittal judgment"],
        contempt_or_outcome_evidence_tokens=["contempt notice", "judicial inquiry"],
    )
    noisy_records = [
        {
            "page": 1,
            "raw_text": (
                f"Judgment: Accused was declared NOT GUILTY in acquittal judgment and ordered released from "
                f"District Jail, Ghaziabad. After contempt notice, official was held guilty of contempt amidst "
                f"Anti-Corruption Bureau fraud/bribery review; Court ordered judicial inquiry. "
                f"PII test: Aadhaar {aadhaar}, PAN ABCDE1234F, IFSC SBIN0001234, "
                f"Account No 30981234567890, mobile +91 9876543210, email prisoner.relative@gmail.com."
            ),
        }
    ]
    res = run_engine("P10", noisy_records, context={"innocent_but_locked_up": spec, "as_of_date": "2026-11-28"})
    assert len(res.leads) == 1
    lead = res.leads[0]

    lint = lint_neutrality(
        lead.title,
        lead.summary,
        lead.benign_explanations,
        extra_fields={
            "entity_name": lead.entity_name,
            "suggested_authority": lead.suggested_authority,
            "excerpts": " ".join(str(c.get("excerpt") or "") for c in lead.citations),
        },
    )
    assert lint.passed, lint.violations

    dumped = json.dumps(lead.model_dump())
    assert "guilty" not in dumped.lower()
    assert aadhaar not in dumped
    assert "ABCDE1234F" not in dumped
    assert "SBIN0001234" not in dumped
    assert "9876543210" not in dumped
    assert "prisoner.relative@gmail.com" not in dumped
    assert not contains_pii(lead.model_dump())[0]


# ---------------------------------------------------------------------------
# 5. Strict Project Validation (`validate_project`) & RTI Drafting
# ---------------------------------------------------------------------------


def test_p10_project_validation_rules_and_rejections() -> None:
    good_task = _sample_p10_task("p10-court-2025-ghaziabad-anchor-0001", _sample_anchor_spec())
    assert validate_project(_sample_p10_project([good_task]), filename_stem=INNOCENT_BUT_LOCKED_UP_PROJECT_SLUG) == []

    # 1. Wrong quantity type
    bad_qty = _sample_p10_project([_sample_p10_task("p10-court-2025-ghaziabad-anchor-0001", _sample_anchor_spec(), qty_type="parliamentary_assurances")])
    assert any("custody_overrun_and_contempt" in e for e in validate_project(bad_qty, filename_stem=INNOCENT_BUT_LOCKED_UP_PROJECT_SLUG))

    # 2. Bad task ID format
    bad_id = _sample_p10_project([_sample_p10_task("bad-p10-task", _sample_anchor_spec())])
    assert any("p10-(court|prison|police|nhrc|nalsa|parl|govt|cag)" in e for e in validate_project(bad_id, filename_stem=INNOCENT_BUT_LOCKED_UP_PROJECT_SLUG))

    # 3. Portal code mismatch vs task ID
    bad_portal = _sample_p10_project([_sample_p10_task("p10-nhrc-2025-mismatch-0002", _sample_anchor_spec(portal_category="court_portal"))])
    assert any("portal code" in e for e in validate_project(bad_portal, filename_stem=INNOCENT_BUT_LOCKED_UP_PROJECT_SLUG))

    # 4. Year mismatch vs task ID & out-of-window year (2020)
    bad_yr_match = _sample_p10_project([_sample_p10_task("p10-court-2024-mismatch-0003", _sample_anchor_spec(year=2025))])
    assert any("does not match" in e for e in validate_project(bad_yr_match, filename_stem=INNOCENT_BUT_LOCKED_UP_PROJECT_SLUG))

    bad_yr_range = _sample_p10_project([_sample_p10_task("p10-court-2024-old-0004", _sample_anchor_spec(year=2020))])
    assert any("between 2021 and 2026" in e for e in validate_project(bad_yr_range, filename_stem=INNOCENT_BUT_LOCKED_UP_PROJECT_SLUG))

    # 5. BNS Sec 356 forbidden term ("not guilty") rejected in validate_project
    bad_guilty = _sample_p10_project([_sample_p10_task("p10-court-2025-guilty-0005", _sample_anchor_spec(acquittal_quote="Court declared accused not guilty."))])
    assert any("non-neutral" in e for e in validate_project(bad_guilty, filename_stem=INNOCENT_BUT_LOCKED_UP_PROJECT_SLUG))

    # 6. Union of India with non-null state_ut rejected; State mismatch rejected
    bad_union = _sample_p10_project([_sample_p10_task("p10-court-2025-union-0006", _sample_anchor_spec(jurisdiction="Union of India", state_ut="Uttar Pradesh"))])
    assert any("state_ut must be null" in e for e in validate_project(bad_union, filename_stem=INNOCENT_BUT_LOCKED_UP_PROJECT_SLUG))

    bad_state_mm = _sample_p10_project([_sample_p10_task("p10-court-2025-stmm-0007", _sample_anchor_spec(jurisdiction="State:Bihar", state_ut="Uttar Pradesh"))])
    assert any("state_ut must match" in e for e in validate_project(bad_state_mm, filename_stem=INNOCENT_BUT_LOCKED_UP_PROJECT_SLUG))

    # 7. Chronology error & path traversal archive_member & missing secondary_url for secondary_role
    bad_rel = _sample_p10_project([_sample_p10_task("p10-court-2025-rel-0008", _sample_anchor_spec(acquittal_order_date="2025-07-01", release_date="2025-06-01"))])
    assert any("must not precede acquittal_order_date" in e for e in validate_project(bad_rel, filename_stem=INNOCENT_BUT_LOCKED_UP_PROJECT_SLUG))

    bad_arc = _sample_p10_project([_sample_p10_task("p10-court-2025-arc-0009", _sample_anchor_spec(record_selector={"archive_member": "../etc/passwd"}))])
    assert any("safe relative archive path" in e for e in validate_project(bad_arc, filename_stem=INNOCENT_BUT_LOCKED_UP_PROJECT_SLUG))

    bad_sec = _sample_p10_project([_sample_p10_task("p10-court-2025-sec-0010", _sample_anchor_spec(secondary_role="contempt_order_document"), secondary_url=None)])
    assert any("secondary_url is required" in e for e in validate_project(bad_sec, filename_stem=INNOCENT_BUT_LOCKED_UP_PROJECT_SLUG))


def test_p10_rti_draft_union_state_and_delhi_ut() -> None:
    up_finding = {
        "methodology_code": "P10",
        "entity_name": "Superintendent, District Jail, Ghaziabad & Uttar Pradesh Prisons Administration",
        "suggested_authority": "Director General of Prisons, Government of Uttar Pradesh",
        "jurisdiction": "State:Uttar Pradesh",
        "fiscal_year": "FY2025-26",
        "monetary_exposure_inr": 500_000.0,
        "anomaly_metrics": {
            "case_key": "p10-court-2025-ghaziabad-anchor-0001",
            "acquittal_case_number": "Crl.A. No. 2295 of 2025",
            "contempt_case_number": "M.A. No. 1086 of 2025",
        },
    }
    up_rti = generate_rti_draft(up_finding)
    assert up_rti["jurisdiction_level"] == "state"
    assert up_rti["rti_portal_url"] == "https://rtionline.up.gov.in"
    assert up_rti["central_portal_warning"] is not None
    assert up_rti["word_count"] <= 500
    assert "Crl.A. No. 2295 of 2025" in up_rti["body_text"]
    assert "FASTER" in up_rti["body_text"]

    union_finding = {
        "methodology_code": "P10",
        "entity_name": "Ministry of Home Affairs, Government of India",
        "suggested_authority": "Ministry of Home Affairs, Government of India",
        "jurisdiction": "Union of India",
        "fiscal_year": "FY2024-25",
        "monetary_exposure_inr": 0.0,
        "anomaly_metrics": {"case_key": "p10-govt-2024-mha-faster-0010"},
    }
    union_rti = generate_rti_draft(union_finding)
    assert union_rti["jurisdiction_level"] == "central"
    assert union_rti["rti_portal_url"] == "https://rtionline.gov.in"
    assert union_rti["word_count"] <= 500

    delhi_finding = {
        "methodology_code": "P10",
        "entity_name": "Director General of Prisons, Central Jail Tihar",
        "suggested_authority": "Office of Director General of Prisons, Tihar, GNCTD",
        "jurisdiction": "UT:Delhi",
        "fiscal_year": "FY2026-27",
        "monetary_exposure_inr": 0.0,
        "anomaly_metrics": {"case_key": "p10-court-2026-tihar-0011"},
    }
    delhi_rti = generate_rti_draft(delhi_finding)
    assert delhi_rti["rti_portal_url"] == "https://rtionline.delhi.gov.in"
    assert delhi_rti["word_count"] <= 500


# ---------------------------------------------------------------------------
# 6. Hub API Integration: /api/projects/innocent-but-locked-up/summary, visual_summary & .md
# ---------------------------------------------------------------------------


def test_p10_hub_summary_endpoint_live_aggregation_and_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj_dir = tmp_path / "projects"
    proj_dir.mkdir()

    anchor_spec = _sample_anchor_spec()
    nhrc_spec = _sample_anchor_spec(
        case_key="p10-nhrc-2024-cg-durg-0002",
        case_title="NHRC Direction on Post-Acquittal Detention Overrun in Chhattisgarh",
        portal_category="nhrc_shrc",
        court_tier="statutory_body",
        state_ut="Chhattisgarh",
        jurisdiction="State:Chhattisgarh",
        year=2024,
        document_date="2024-11-10",
        overrun_mechanism="statutory_commission_detention_finding",
        is_anchor_case=False,
        stage_3_contempt_verified=False,
        overrun_days=60,
        compensation_inr=200_000.0,
        acquittal_court="Sessions Court, Durg",
        contempt_court=None,
        contempt_case_number=None,
        prison_name="Central Jail, Durg",
        secondary_role="",
        secondary_source_format=None,
        record_selector=None,
    )
    cleared_spec = _sample_anchor_spec(
        case_key="p10-prison-2025-tihar-ok-0003",
        case_title="Tihar Jail Same-Day FASTER Release Compliance Control",
        portal_category="prison_portal",
        court_tier="statutory_body",
        state_ut="Delhi",
        jurisdiction="UT:Delhi",
        year=2025,
        document_date="2025-08-12",
        overrun_mechanism="timely_release_or_compliant",
        is_anchor_case=False,
        stage_2_continued_detention_verified=False,
        stage_3_contempt_verified=False,
        overrun_days=0,
        compensation_inr=None,
        acquittal_order_date="2025-08-12",
        release_date="2025-08-12",
        prison_name="Central Jail, Tihar",
        contempt_court=None,
        contempt_case_number=None,
        secondary_role="",
        secondary_source_format=None,
        record_selector=None,
    )

    project_data = _sample_p10_project(
        [
            _sample_p10_task("p10-court-2025-ghaziabad-anchor-0001", anchor_spec),
            _sample_p10_task(
                "p10-nhrc-2024-cg-durg-0002",
                nhrc_spec,
                retrieval_url="https://nhrc.nic.in/assets/uploads/case_2024.pdf",
                secondary_url=None,
            ),
            _sample_p10_task(
                "p10-prison-2025-tihar-ok-0003",
                cleared_spec,
                retrieval_url="https://tiharprisons.delhi.gov.in/faster_2025.pdf",
                secondary_url=None,
            ),
        ]
    )
    assert validate_project(project_data, filename_stem=INNOCENT_BUT_LOCKED_UP_PROJECT_SLUG) == []
    (proj_dir / "innocent-but-locked-up.json").write_text(json.dumps(project_data), encoding="utf-8")

    monkeypatch.setenv("AIWATCHDOGS_PROJECTS_DIR", str(proj_dir))
    monkeypatch.delenv("AIWATCHDOGS_KEEP_LEGACY_MISSIONS", raising=False)

    # 1. Fallback aggregation WITHOUT coverage.json
    app_fallback = create_app(db_path=tmp_path / "p10_fallback.db", seed=True)
    with TestClient(app_fallback) as client:
        r_sum = client.get("/api/projects/innocent-but-locked-up/summary")
        assert r_sum.status_code == 200, r_sum.text
        fb_sum = r_sum.json()
        assert fb_sum["hook"] == P10_DEFAULT_HOOK
        assert fb_sum["total_tasks"] == 3
        assert fb_sum["unique_documents_covered"] == 4
        assert fb_sum["states_and_uts_covered"] == 3
        assert fb_sum["full_4_stage_tasks"] == 1
        assert fb_sum["contempt_tasks"] == 1
        assert fb_sum["cleared_control_tasks_embedded"] == 1
        assert fb_sum["total_overrun_days_documented"] == 88
        assert fb_sum["median_overrun_days"] == 44.0
        assert fb_sum["max_overrun_days"] == 60
        assert fb_sum["total_compensation_ordered_inr"] == 700_000.0
        assert fb_sum["anchor_case"]["task_id"] == "p10-court-2025-ghaziabad-anchor-0001"
        assert fb_sum["by_chain_status"]["full_4_stage_chain"] == 1
        assert fb_sum["by_chain_status"]["partial_3_stage"] == 1
        assert fb_sum["by_chain_status"]["compliant_control"] == 1

        r_mission = client.get("/api/missions/innocent-but-locked-up")
        assert r_mission.status_code == 200
        assert r_mission.json()["visual_summary"]["innocent_but_locked_up"] == fb_sum

    # 2. Now add coverage.json (with intentionally stale done/cleared fields) + documents.csv and submit live audit results!
    p10_assets = proj_dir / "innocent-but-locked-up"
    p10_assets.mkdir()
    coverage_obj = {
        "hook": P10_DEFAULT_HOOK,
        "generated_on": "2026-11-28",
        "as_of_date": "2026-11-28",
        "year_range": [2021, 2026],
        "total_tasks": 3,
        "unique_documents_covered": 4,
        "states_and_uts_covered": 3,
        "courts_covered": 2,
        "prisons_named": 3,
        "full_4_stage_tasks": 1,
        "contempt_tasks": 1,
        "verified_release_tasks": 3,
        "cleared_control_tasks_embedded": 1,
        "total_overrun_days_documented": 88,
        "median_overrun_days": 44.0,
        "max_overrun_days": 60,
        "total_compensation_ordered_inr": 700_000.0,
        "by_portal_category": [
            {"portal_category": "court_portal", "tasks": 1, "cleared": 99},
            {"portal_category": "nhrc_shrc", "tasks": 1, "cleared": 99},
            {"portal_category": "prison_portal", "tasks": 1, "cleared": 99},
        ],
        "by_year": [
            {"year": 2024, "tasks": 1, "full_4_stage": 0, "overrun_days": 60, "cleared": 99},
            {"year": 2025, "tasks": 2, "full_4_stage": 1, "overrun_days": 28, "cleared": 99},
        ],
        "by_court_tier": {
            "supreme_court": {"tasks": 1, "cleared": 99},
            "statutory_body": {"tasks": 2, "cleared": 99},
        },
        "by_mechanism": {
            "post_acquittal_contempt_overrun": {"tasks": 1},
            "statutory_commission_detention_finding": {"tasks": 1},
            "timely_release_or_compliant": {"tasks": 1},
        },
        "by_chain_status": {
            "full_4_stage_chain": 1,
            "partial_3_stage": 1,
            "institutional_record": 0,
            "compliant_control": 1,
        },
        "states": [
            {"state_ut": "Uttar Pradesh", "jurisdiction": "State:Uttar Pradesh", "tasks": 1, "full_4_stage": 1, "total_overrun_days": 28, "compensation_inr": 500_000.0, "cleared": 99},
            {"state_ut": "Chhattisgarh", "jurisdiction": "State:Chhattisgarh", "tasks": 1, "full_4_stage": 0, "total_overrun_days": 60, "compensation_inr": 200_000.0, "cleared": 99},
            {"state_ut": "Delhi", "jurisdiction": "UT:Delhi", "tasks": 1, "full_4_stage": 0, "total_overrun_days": 0, "compensation_inr": 0.0, "cleared": 99},
        ],
        "top_courts": [
            {"court_name": "Supreme Court of India", "court_tier": "supreme_court", "state_ut": "Uttar Pradesh", "tasks": 1, "full_4_stage": 1},
        ],
        "top_prisons": [
            {"prison_name": "District Jail, Ghaziabad", "state_ut": "Uttar Pradesh", "tasks": 1, "total_overrun_days": 28},
        ],
        "anchor_case": {
            "task_id": "p10-court-2025-ghaziabad-anchor-0001",
            "case_title": anchor_spec["case_title"],
            "portal_category": "court_portal",
            "court_tier": "supreme_court",
            "state_ut": "Uttar Pradesh",
            "jurisdiction": "State:Uttar Pradesh",
            "year": 2025,
            "overrun_mechanism": "post_acquittal_contempt_overrun",
            "stages_verified_count": 4,
            "chain_status": "full_4_stage_chain",
            "acquittal_order_date": anchor_spec["acquittal_order_date"],
            "acquittal_court": anchor_spec["acquittal_court"],
            "acquittal_case_number": anchor_spec["acquittal_case_number"],
            "acquittal_quote": anchor_spec["acquittal_quote"],
            "prison_name": anchor_spec["prison_name"],
            "custody_authority": anchor_spec["custody_authority"],
            "non_release_reason": anchor_spec["non_release_reason"],
            "overrun_days": 28,
            "overrun_window": anchor_spec["overrun_window"],
            "contempt_case_number": anchor_spec["contempt_case_number"],
            "contempt_court": anchor_spec["contempt_court"],
            "contempt_filing_date": anchor_spec["contempt_filing_date"],
            "contempt_order_date": anchor_spec["contempt_order_date"],
            "contempt_quote": anchor_spec["contempt_quote"],
            "release_date": anchor_spec["release_date"],
            "release_verified": True,
            "compensation_inr": 500_000.0,
            "judicial_direction_summary": anchor_spec["judicial_direction_summary"],
            "source_url": "https://api.sci.gov.in/supremecourt/2025/2295/order_20250429.pdf",
            "secondary_url": "https://api.sci.gov.in/supremecourt/2025/1086/order_20250625.pdf",
            "portal_url": "https://www.sci.gov.in/",
        },
    }
    (p10_assets / "coverage.json").write_text(json.dumps(coverage_obj), encoding="utf-8")
    (p10_assets / "documents.csv").write_text(
        "url,sha256,publisher\nhttps://api.sci.gov.in/supremecourt/2025/2295/order_20250429.pdf," + "a" * 64 + ",Supreme Court of India\n",
        encoding="utf-8",
    )

    app = create_app(db_path=tmp_path / "p10_live.db", seed=True)
    with TestClient(app) as client:
        assert client.get("/api/missions/innocent-but-locked-up/coverage.json").status_code == 200
        assert client.get("/api/missions/innocent-but-locked-up/documents.csv").status_code == 200
        assert client.get("/api/missions/innocent-but-locked-up/secret.txt").status_code == 404

        reg = client.post(
            "/api/agents/register",
            json={
                "handle": "p10-auditor-01",
                "display_name": "P10 Auditor",
                "provider": "rule-engine",
                "model": "deterministic-python-v1",
                "coding_agent": "Claude Code",
                "coding_agent_model": "claude-sonnet-4",
                "mode": "auto",
            },
        ).json()
        headers = {"X-Agent-Token": reg["api_token"]}

        # Claim & submit Task #1 (flagship anchor lead)
        lease1 = client.post(
            "/api/desk/next",
            headers=headers,
            json={"task_id": "prj-p10-court-2025-ghaziabad-anchor-0001"},
        ).json()
        task1_ctx = _build_task_context(lease1["task"]["payload"])
        assert "innocent_but_locked_up" in task1_ctx

        lead_res = run_engine(
            "P10",
            [
                {
                    "document": "primary",
                    "page": 1,
                    "raw_text": "Crl.A. No. 2295 release order addressed to District Jail, Ghaziabad.",
                },
                {
                    "document": "secondary",
                    "page": 2,
                    "raw_text": "M.A. No. 1086 ordered INR 5,00,000 compensation and judicial inquiry.",
                    "secondary_sha256": "b" * 64,
                },
            ],
            context=task1_ctx,
            as_of_date="2026-11-28",
        )
        assert len(lead_res.leads) == 1
        cand = lead_res.leads[0]
        sub1 = client.post(
            "/api/desk/submit",
            headers=headers,
            json={
                "lease_id": lease1["lease_id"],
                "outcome": "finding",
                "finding": {
                    **cand.model_dump(),
                    "source_url": lease1["task"]["payload"]["retrieval_url"],
                    "artifact_sha256": lease1["task"]["payload"]["expected_sha256"],
                    "secondary_sha256": lease1["task"]["payload"]["secondary_expected_sha256"],
                },
            },
        )
        assert sub1.status_code == 200, sub1.text
        anchor_finding_id = sub1.json()["finding_id"]

        # Claim & submit Task #3 (Cleared control)
        lease3 = client.post(
            "/api/desk/next",
            headers=headers,
            json={"task_id": "prj-p10-prison-2025-tihar-ok-0003"},
        ).json()
        sub3 = client.post(
            "/api/desk/submit",
            headers=headers,
            json={
                "lease_id": lease3["lease_id"],
                "outcome": "cleared",
                "metrics": {
                    "records_scanned": 1,
                    "cleared_count": 1,
                    "unevaluable_count": 0,
                    "monetary_audited_inr": 0.0,
                },
            },
        )
        assert sub3.status_code == 200, sub3.text

        # Verify live aggregation + zero stale coverage.json cleared/done leakage
        summary = client.get("/api/projects/innocent-but-locked-up/summary").json()
        assert summary["leads"] == 1
        assert summary["peer_review"] == 1
        assert summary["cleared_tasks"] == 1
        assert summary["by_signal"]["post_acquittal_contempt_overrun"] == 1
        assert summary["by_mechanism"]["post_acquittal_contempt_overrun"]["leads"] == 1

        portals = {p["portal_category"]: p for p in summary["by_portal_category"]}
        assert portals["court_portal"]["done"] == 1
        assert portals["court_portal"]["leads"] == 1
        assert portals["court_portal"]["cleared"] == 0
        assert portals["nhrc_shrc"]["done"] == 0
        assert portals["nhrc_shrc"]["cleared"] == 0
        assert portals["prison_portal"]["done"] == 1
        assert portals["prison_portal"]["cleared"] == 1

        years = {y["year"]: y for y in summary["by_year"]}
        assert years[2025]["done"] == 2
        assert years[2025]["leads"] == 1
        assert years[2025]["cleared"] == 1
        assert years[2024]["done"] == 0
        assert years[2024]["cleared"] == 0

        assert summary["by_court_tier"]["supreme_court"]["done"] == 1
        assert summary["by_court_tier"]["supreme_court"]["leads"] == 1
        assert summary["by_court_tier"]["supreme_court"]["cleared"] == 0
        assert summary["by_court_tier"]["statutory_body"]["done"] == 1
        assert summary["by_court_tier"]["statutory_body"]["cleared"] == 1

        states = {s["jurisdiction"]: s for s in summary["states"]}
        assert states["State:Uttar Pradesh"]["done"] == 1
        assert states["State:Uttar Pradesh"]["leads"] == 1
        assert states["State:Uttar Pradesh"]["cleared"] == 0
        assert states["UT:Delhi"]["done"] == 1
        assert states["UT:Delhi"]["cleared"] == 1
        assert states["State:Chhattisgarh"]["cleared"] == 0

        assert summary["anchor_case"]["finding_id"] == anchor_finding_id
        assert len(summary["top_leads"]) == 1
        assert summary["top_leads"][0]["finding_id"] == anchor_finding_id
        assert summary["top_leads"][0]["is_anchor_case"] is True

        # Markdown & discovery routes
        md_resp = client.get("/projects/innocent-but-locked-up.md")
        assert md_resp.status_code == 200
        assert "## Innocent But Locked Up (P10)" in md_resp.text
        assert "Flagship 4-Stage Anchor Case" in md_resp.text
        assert "/api/projects/innocent-but-locked-up/summary" in md_resp.text

        brief_resp = client.get("/brief.md")
        assert brief_resp.status_code == 200
        assert "innocent-but-locked-up.md" in brief_resp.text

        join_resp = client.get("/join.txt")
        assert join_resp.status_code == 200
        assert "innocent-but-locked-up" in join_resp.text

        llms_resp = client.get("/llms.txt")
        assert llms_resp.status_code == 200
        assert "P10" in llms_resp.text
