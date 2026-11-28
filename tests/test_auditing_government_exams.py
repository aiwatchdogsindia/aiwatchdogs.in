"""Comprehensive unit, fail-closed, BNS/DPDP, RTI, and Hub API tests for Project 9: Auditing Government Exams (P9)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from aiwatchdogs.cli.runner import (
    _ALLOWED_TASK_CONTEXT_KEYS,
    CRITICAL_SEMANTIC_METRIC_KEYS,
    RECORD_IDENTIFIER_KEYS,
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
from aiwatchdogs.engines.auditing_government_exams import (
    AUDITING_GOVERNMENT_EXAMS_METHODOLOGY,
    AUDITING_GOVERNMENT_EXAMS_PROJECT_SLUG,
    DISRUPTION_TYPE_LABELS,
    DISRUPTION_TYPE_RULES,
    P9_DEFAULT_HOOK,
    P9_TASK_ID_RE,
    SIGNAL_CATEGORIES,
    evaluate_exam_disruption_rule,
)
from aiwatchdogs.engines.registry import (
    METHODOLOGY_CATALOG,
    RULESET_VERSIONS,
    get_engine_fingerprint,
    run_engine,
)
from aiwatchdogs.hub.app import create_app
from aiwatchdogs.projects import (
    CANONICAL_PROJECT_CODES,
    VALID_QUANTITY_TYPES,
    validate_project,
)


def _make_valid_aadhaar(prefix_11: str) -> str:
    assert len(prefix_11) == 11 and prefix_11[0] in "23456789"
    return prefix_11 + verhoeff_generate_check_digit(prefix_11)


def _create_pdf(path: Path, pages_lines: list[list[str]]) -> str:
    """Create a real multi-page PDF with extractable text and return its SHA-256."""
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

    with path.open("wb") as fh:
        writer.write(fh)
    return compute_sha256(path.read_bytes())


def _sample_ugc_net_spec(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "case_key": "p9-union-ugc-net-0001",
        "exam_name": "UGC-NET June 2024",
        "exam_slug": "ugc-net-june-2024",
        "cycle_or_stage": "June 2024 Cycle (Pen-and-Paper OMR, Two Shifts)",
        "exam_year": 2024,
        "jurisdiction": "Union of India",
        "jurisdiction_level": "union",
        "state_or_ut": None,
        "authority": "National Testing Agency (NTA), Ministry of Education",
        "authority_key": "national-testing-agency-nta",
        "authority_type": "central_testing_agency",
        "exam_category": "teaching_and_faculty",
        "scheduled_date": "2024-06-18",
        "actual_or_event_date": "2024-06-19",
        "delay_days": 64,
        "disruption_type": "cancelled_after_conduct",
        "students_took_exam_before_cancellation": True,
        "schedule_summary": (
            "NTA conducted the UGC-NET June 2024 examination across 317 cities in pen-and-paper OMR mode on 18 June 2024."
        ),
        "schedule_document_ref": "NTA Public Notice F.No. UGC-NET June 2024 (18 June 2024)",
        "schedule_evidence_tokens": ["UGC-NET", "18 June 2024", "National Testing Agency"],
        "outcome_summary": (
            "Ministry of Education cancelled the UGC-NET June 2024 examination on 19 June 2024 following "
            "preliminary inputs from the Indian Cyber Crime Coordination Centre indicating integrity concerns."
        ),
        "outcome_document_ref": "Ministry of Education Press Release (19 June 2024)",
        "outcome_evidence_tokens": ["UGC-NET", "cancelled", "19 June 2024"],
        "disruption_reason_category": "paper_integrity_compromise",
        "affected_candidates_count": 908580,
        "affected_candidates_basis": "Ministry of Education / NTA official attendance figure of 9,08,580 candidates who appeared on 18 June 2024.",
        "statutory_anchor": "Public Examinations (Prevention of Unfair Means) Act, 2024",
        "benign_explanation_context": (
            "Cancellation was ordered as a preventive integrity safeguard following preliminary cyber-intelligence "
            "inputs to ensure fair assessment and protect all candidates."
        ),
        "source_format": "pdf",
        "secondary_source_format": "pdf",
        "secondary_role": "outcome_document",
        "is_anchor_case": True,
        "record_selector": {"schedule_page": 1, "outcome_page": 2, "section_hint": "UGC-NET June 2024 Notice"},
    }
    base.update(overrides)
    return base


def _sample_p9_task(
    task_id: str,
    spec: dict[str, Any],
    *,
    retrieval_url: str = "https://www.nta.ac.in/Download/Notice/Notice_20240618_UGCNET.pdf",
    expected_sha256: str = "a" * 64,
    secondary_url: str | None = "https://static.pib.gov.in/WriteReadData/specificdocs/documents/2024/jun/ugcnet_cancel.pdf",
    secondary_expected_sha256: str | None = "b" * 64,
    secondary_in_context: bool = False,
    qty_type: str = "public_examination_conduct",
) -> dict[str, Any]:
    ctx: dict[str, Any] = {
        "as_of_date": "2026-11-28",
        "auditing_government_exams": spec,
    }
    payload: dict[str, Any] = {
        "retrieval_url": retrieval_url,
        "expected_sha256": expected_sha256,
        "official_publisher": "National Testing Agency (NTA) / Ministry of Education",
        "source_landing_page": "https://www.nta.ac.in/",
        "as_of_date": "2026-11-28",
        "context": ctx,
    }
    if secondary_url is not None:
        if secondary_in_context:
            ctx["secondary_url"] = secondary_url
            if secondary_expected_sha256 is not None:
                ctx["secondary_expected_sha256"] = secondary_expected_sha256
        else:
            payload["secondary_url"] = secondary_url
            if secondary_expected_sha256 is not None:
                payload["secondary_expected_sha256"] = secondary_expected_sha256
    return {
        "id": task_id,
        "title": f"{spec['exam_name']} · {spec['cycle_or_stage']}"[:140],
        "instructions": "Verify SHA-256 digests and run deterministic P9 examination conduct audit.",
        "type": "run_check",
        "methodology_code": "P9",
        "dataset_key": f"p9-{spec['exam_slug']}",
        "financial_quantity_type": qty_type,
        "estimated_tokens": 1800,
        "estimate_basis": "Deterministic P9 PDF/HTML/JSON examination conduct verification (zero LLM cost).",
        "payload": payload,
    }


def _sample_p9_project(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "slug": AUDITING_GOVERNMENT_EXAMS_PROJECT_SLUG,
        "project_code": "P9",
        "priority": 9,
        "title": "Auditing Government Exams: 3-Year Public Examination Conduct Audit",
        "summary": (
            "Audits 1,000 Union, State & UT public examinations over the past 3 years (Nov 2023 – Nov 2026) "
            "to measure flawless conduct versus delays, postponements, reschedules, cancellations, and glitches."
        ),
        "posted_by": {
            "name": "AI Watchdogs India",
            "handle": "@aiwatchdogsIN",
            "url": "https://aiwatchdogs.in",
        },
        "pillar": "audit",
        "status": "active",
        "methodology_code": "P9",
        "jurisdiction": "India (Union Government, 28 States & 8 Union Territories)",
        "authority": "NTA, UPSC, SSC, RRB, NBEMS, State Public Service Commissions & Recruitment Boards",
        "statutory_anchors": [
            "Public-Examinations-Prevention-of-Unfair-Means-Act-2024",
            "Constitution-Art-14-16",
            "Constitution-Art-315-320",
            "RTI-Act-2005-Sec-4-1-b",
        ],
        "scope": [
            "Documentary verification of scheduled dates vs actual conduct outcomes across Union, State & UT government exams (2023-11-28 to 2026-11-28).",
            "Includes both disrupted examinations and a verified flawless_conduct benchmark cohort.",
        ],
        "out_of_scope": [
            "Private university internal tests or unverified social media rumours lacking official notices.",
        ],
        "sources": [
            {
                "name": "National Testing Agency Public Notices & Parliament Replies",
                "publisher": "Ministry of Education & National Testing Agency",
                "url": "https://www.nta.ac.in/",
            }
        ],
        "tasks": tasks,
    }


# ---------------------------------------------------------------------------
# 1. Registration, Dual-PDF Anchor Verification & Determinism
# ---------------------------------------------------------------------------


def test_p9_registration_and_dual_pdf_anchor_end_to_end(tmp_path: Path) -> None:
    assert CANONICAL_PROJECT_CODES[AUDITING_GOVERNMENT_EXAMS_PROJECT_SLUG] == "P9"
    assert "public_examination_conduct" in VALID_QUANTITY_TYPES
    assert "P9" in METHODOLOGY_CATALOG
    assert RULESET_VERSIONS["P9"] == "P9-2026.11-v1"
    assert "auditing_government_exams" in _ALLOWED_TASK_CONTEXT_KEYS
    assert {
        "disruption_type",
        "disruption_reason_category",
        "authority_type",
        "exam_category",
        "students_took_exam_before_cancellation",
    } <= CRITICAL_SEMANTIC_METRIC_KEYS
    assert "exam_slug" in RECORD_IDENTIFIER_KEYS
    assert P9_TASK_ID_RE.fullmatch("p9-union-ugc-net-0001")
    assert P9_TASK_ID_RE.fullmatch("p9-state-up-police-0042")
    assert P9_TASK_ID_RE.fullmatch("p9-ut-dl-dsssb-0901")
    assert not P9_TASK_ID_RE.fullmatch("p9-other-exam-0001")

    sched_pdf = tmp_path / "ugc_net_schedule.pdf"
    sched_sha = _create_pdf(
        sched_pdf,
        [
            [
                "National Testing Agency (NTA) - Public Notice",
                "Subject: Conduct of UGC-NET June 2024 Examination in OMR Mode.",
                "The National Testing Agency conducted UGC-NET June 2024 on 18 June 2024",
                "across 317 cities in two shifts for 9,08,580 appeared candidates.",
            ]
        ],
    )
    cancel_pdf = tmp_path / "ugc_net_cancellation.pdf"
    cancel_sha = _create_pdf(
        cancel_pdf,
        [
            ["Ministry of Education - Press Information Bureau Release"],
            [
                "Dated 19 June 2024: To ensure the highest level of transparency and sanctity",
                "of the examination process, the Ministry of Education has decided that the",
                "UGC-NET June 2024 examination be cancelled and conducted afresh.",
            ],
        ],
    )

    spec = _sample_ugc_net_spec()
    ctx = {
        "as_of_date": "2026-11-28",
        "retrieval_url": "https://www.nta.ac.in/Download/Notice/Notice_20240618_UGCNET.pdf",
        "expected_sha256": sched_sha,
        "secondary_path": str(cancel_pdf),
        "secondary_url": "https://static.pib.gov.in/ugcnet_cancel.pdf",
        "secondary_expected_sha256": cancel_sha,
        "official_publisher": "National Testing Agency (NTA) / Ministry of Education",
        "source_landing_page": "https://www.nta.ac.in/",
        "auditing_government_exams": spec,
    }
    loaded = load_dataset(
        str(sched_pdf),
        context={
            "auditing_government_exams": spec,
            "secondary_path": str(cancel_pdf),
        },
        allow_local_files=True,
        expected_sha256=sched_sha,
        secondary_expected_sha256=cancel_sha,
    )
    assert loaded.digest_verified is True
    assert loaded.secondary_digest_verified is True
    assert loaded.sha256 == sched_sha
    assert loaded.secondary_sha256 == cancel_sha

    res1 = run_engine("P9", loaded.records, context=ctx, as_of_date="2026-11-28")
    res2 = run_engine("P9", loaded.records, context=ctx, as_of_date="2026-11-28")
    assert res1.script_hash == res2.script_hash
    assert res1.model_dump() == res2.model_dump()
    assert res1.methodology_code == AUDITING_GOVERNMENT_EXAMS_METHODOLOGY
    assert res1.ruleset_version == "P9-2026.11-v1"
    assert res1.amount_type == "public_examination_conduct"
    assert res1.cleared_count == 0
    assert res1.unevaluable_count == 0
    assert len(res1.leads) == 1

    lead = res1.leads[0]
    assert lead.rule_triggered == "P9-CANCELLED-AFTER-CONDUCT"
    assert lead.severity == "critical"
    assert lead.monetary_exposure_inr == 0.0
    assert lead.anomaly_metrics["disruption_type"] == "cancelled_after_conduct"
    assert lead.anomaly_metrics["students_took_exam_before_cancellation"] is True
    assert lead.anomaly_metrics["affected_candidates_count"] == 908580
    assert lead.anomaly_metrics["exam_year"] == 2024
    assert lead.anomaly_metrics["delay_days"] == 64
    assert lead.anomaly_metrics["schedule_tokens_matched"] == spec["schedule_evidence_tokens"]
    assert lead.anomaly_metrics["outcome_tokens_matched"] == spec["outcome_evidence_tokens"]
    assert len(lead.citations) == 3
    assert [c["role"] for c in lead.citations] == ["original_schedule", "conduct_outcome", "statutory_benchmark"]
    assert lead.citations[0]["sha256"] == sched_sha
    assert lead.citations[0]["page"] == 1
    assert lead.citations[1]["sha256"] == cancel_sha
    assert lead.citations[1]["page"] == 2
    assert len(lead.benign_explanations) >= 4
    assert lint_neutrality(lead.title, lead.summary, lead.benign_explanations).passed
    assert contains_pii(lead.model_dump())[0] is False

    fp = get_engine_fingerprint("P9", config=ctx, as_of_date="2026-11-28")
    assert fp["methodology_code"] == "P9"
    assert fp["ruleset_version"] == "P9-2026.11-v1"


# ---------------------------------------------------------------------------
# 2. All 7 Disruption Types + Flawless Conduct Cleared Benchmark
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("disruption_type", "expected_rule", "reason", "sat_flag", "delay_days", "cands", "expected_sev"),
    [
        ("cancelled_after_conduct", "P9-CANCELLED-AFTER-CONDUCT", "paper_integrity_compromise", True, 64, 908580, "critical"),
        ("postponed", "P9-POSTPONED", "administrative_or_logistical", False, 49, 228000, "high"),
        ("rescheduled", "P9-RESCHEDULED", "election_or_calendar_clash", False, 14, 15000, "medium"),
        ("delayed", "P9-DELAYED", "judicial_or_policy_review", False, 95, None, "high"),
        ("cancelled_before_conduct", "P9-CANCELLED-BEFORE-CONDUCT", "administrative_or_logistical", False, None, 20000, "high"),
        ("partial_reexam_or_glitch", "P9-PARTIAL-REEXAM-OR-GLITCH", "technical_or_cbt_glitch", False, 12, 1563, "high"),
    ],
)
def test_p9_all_six_flagged_disruption_types(
    disruption_type: str,
    expected_rule: str,
    reason: str,
    sat_flag: bool,
    delay_days: int | None,
    cands: int | None,
    expected_sev: str,
) -> None:
    flagged, rule, sig, rules, sev = evaluate_exam_disruption_rule(
        disruption_type=disruption_type,
        disruption_reason_category=reason,
        students_took_exam_before_cancellation=sat_flag,
        delay_days=delay_days,
        affected_candidates_count=cands,
        is_anchor_case=False,
    )
    assert flagged is True
    assert rule == expected_rule
    assert sig == disruption_type
    assert expected_rule in rules
    assert sev == expected_sev

    spec = _sample_ugc_net_spec(
        case_key=f"p9-union-case-{disruption_type.replace('_', '-')}",
        disruption_type=disruption_type,
        disruption_reason_category=reason,
        students_took_exam_before_cancellation=sat_flag,
        delay_days=delay_days,
        affected_candidates_count=cands,
        is_anchor_case=False,
        secondary_role="",
        secondary_source_format=None,
        record_selector=None,
        schedule_evidence_tokens=["Public Examination 2024", "scheduled date"],
        outcome_evidence_tokens=["official notice", "revised status"],
    )
    records = [
        {
            "document": "primary",
            "page": 1,
            "raw_text": (
                "Official Notification: Public Examination 2024 had a scheduled date in June 2024. "
                "Subsequent official notice confirmed revised status for affected candidates."
            ),
        }
    ]
    res = run_engine("P9", records, context={"auditing_government_exams": spec, "as_of_date": "2026-11-28"})
    assert len(res.leads) == 1
    assert res.cleared_count == 0
    assert res.unevaluable_count == 0
    assert res.leads[0].rule_triggered == expected_rule
    assert res.leads[0].severity == expected_sev
    assert DISRUPTION_TYPE_LABELS[disruption_type] in res.leads[0].title


def test_p9_flawless_conduct_returns_cleared_with_zero_leads() -> None:
    spec = _sample_ugc_net_spec(
        case_key="p9-union-upsc-cse-2024-0010",
        exam_name="UPSC Civil Services Preliminary Examination 2024",
        exam_slug="upsc-cse-prelims-2024",
        cycle_or_stage="Preliminary Examination 2024",
        authority="Union Public Service Commission (UPSC)",
        authority_key="union-public-service-commission-upsc",
        authority_type="union_psc_or_recruitment",
        exam_category="civil_services_and_psc",
        scheduled_date="2024-06-16",
        actual_or_event_date="2024-06-16",
        delay_days=0,
        disruption_type="flawless_conduct",
        disruption_reason_category="none_flawless",
        students_took_exam_before_cancellation=False,
        schedule_evidence_tokens=["Civil Services", "16 June 2024"],
        outcome_evidence_tokens=["conducted smoothly", "Result declared"],
        is_anchor_case=False,
        secondary_role="",
        secondary_source_format=None,
        record_selector=None,
    )
    records = [
        {
            "document": "primary",
            "page": 1,
            "raw_text": (
                "Union Public Service Commission Press Note: Civil Services Preliminary Examination "
                "was held on 16 June 2024 and conducted smoothly across all centres. Result declared on schedule."
            ),
        }
    ]
    res = run_engine("P9", records, context={"auditing_government_exams": spec, "as_of_date": "2026-11-28"})
    assert res.leads == []
    assert res.cleared_count == 1
    assert res.unevaluable_count == 0
    assert "flawless_conduct" in res.summary


# ---------------------------------------------------------------------------
# 3. Word-Boundary Normalized Token Matching & Fail-Closed Guarantees
# ---------------------------------------------------------------------------


def test_p9_word_boundary_prevents_substring_false_matches_and_handles_unicode() -> None:
    # "NET" must NOT match inside "internet", "Paper I" must NOT match "Paper II", "2024" must NOT match "20240"
    spec_net = _sample_ugc_net_spec(
        schedule_evidence_tokens=["NET", "Paper I"],
        outcome_evidence_tokens=["2024", "cancelled"],
        secondary_role="",
        secondary_source_format=None,
        record_selector=None,
    )
    false_match_records = [
        {
            "document": "primary",
            "page": 1,
            "raw_text": (
                "The internet portal published Paper II instructions for notification code 20240 "
                "regarding cancelled administrative hearings in the cabinet secretariat."
            ),
        }
    ]
    res_bad = run_engine("P9", false_match_records, context={"auditing_government_exams": spec_net})
    assert res_bad.leads == []
    assert res_bad.cleared_count == 0
    assert res_bad.unevaluable_count == 1

    # Normalized Unicode en-dash, smart quotes, extra whitespace, and case MUST match cleanly on word boundaries
    spec_unicode = _sample_ugc_net_spec(
        schedule_evidence_tokens=["UGC-NET", "NEET (UG)"],
        outcome_evidence_tokens=["'postponed'", "18.06.2024"],
        disruption_type="postponed",
        disruption_reason_category="administrative_or_logistical",
        students_took_exam_before_cancellation=False,
        secondary_role="",
        secondary_source_format=None,
        record_selector=None,
    )
    unicode_records = [
        {
            "document": "primary",
            "page": 1,
            "raw_text": (
                "Official NTA Bulletin: UGC\u2013NET and NEET  (UG) schedules were reviewed; "
                "the sitting was \u2018postponed\u2019 per notice dated 18.06.2024 across centres."
            ),
        }
    ]
    res_ok = run_engine("P9", unicode_records, context={"auditing_government_exams": spec_unicode})
    assert len(res_ok.leads) == 1
    assert res_ok.unevaluable_count == 0


def test_p9_fail_closed_digest_short_text_missing_tokens_selector_and_html(tmp_path: Path) -> None:
    pdf_path = tmp_path / "exam.pdf"
    real_sha = _create_pdf(
        pdf_path,
        [["National Testing Agency UGC-NET 18 June 2024 examination notice cancelled on 19 June 2024."]],
    )
    # 1. Primary SHA-256 mismatch raises DigestMismatchError in loader
    with pytest.raises(DigestMismatchError):
        load_dataset(str(pdf_path), allow_local_files=True, expected_sha256="0" * 64)

    # 2. Secondary SHA-256 mismatch raises DigestMismatchError in loader
    sec_pdf = tmp_path / "sec.pdf"
    _create_pdf(sec_pdf, [["Ministry of Education cancellation order dated 19 June 2024 for UGC-NET."]])
    with pytest.raises(DigestMismatchError):
        load_dataset(
            str(pdf_path),
            context={"auditing_government_exams": _sample_ugc_net_spec(), "secondary_path": str(sec_pdf)},
            allow_local_files=True,
            expected_sha256=real_sha,
            secondary_expected_sha256="f" * 64,
        )

    # 3. Direct engine call with digest_verified=False or mismatched observed/expected SHA -> unevaluable
    spec = _sample_ugc_net_spec(secondary_role="", secondary_source_format=None, record_selector=None)
    good_records = [
        {
            "document": "primary",
            "page": 1,
            "raw_text": (
                "National Testing Agency conducted UGC-NET on 18 June 2024. "
                "On 19 June 2024 UGC-NET was cancelled following preliminary inputs."
            ),
        }
    ]
    for bad_ctx in (
        {"auditing_government_exams": spec, "digest_verified": False},
        {"auditing_government_exams": spec, "secondary_digest_verified": False},
        {"auditing_government_exams": spec, "expected_sha256": "a" * 64, "observed_sha256": "b" * 64},
        {"auditing_government_exams": spec, "secondary_expected_sha256": "a" * 64, "secondary_sha256": "c" * 64},
    ):
        r = run_engine("P9", good_records, context=bad_ctx)
        assert r.leads == [] and r.cleared_count == 0 and r.unevaluable_count == 1

    # 4. <40 readable chars fails closed even for flawless_conduct (never clears unreadable bytes!)
    flawless_spec = _sample_ugc_net_spec(
        disruption_type="flawless_conduct",
        disruption_reason_category="none_flawless",
        students_took_exam_before_cancellation=False,
        delay_days=0,
        schedule_evidence_tokens=["UPSC", "2024"],
        outcome_evidence_tokens=["held", "smooth"],
        secondary_role="",
        secondary_source_format=None,
        record_selector=None,
    )
    short_res = run_engine(
        "P9",
        [{"document": "primary", "page": 1, "raw_text": "UPSC 2024 held smooth."}],
        context={"auditing_government_exams": flawless_spec},
    )
    assert short_res.leads == [] and short_res.cleared_count == 0 and short_res.unevaluable_count == 1

    # 5. Declared secondary_url with empty secondary records fails closed!
    res_empty_sec = run_engine(
        "P9",
        good_records,
        context={
            "auditing_government_exams": _sample_ugc_net_spec(secondary_role="outcome_document"),
            "retrieval_url": "https://www.nta.ac.in/sched.pdf",
            "secondary_url": "https://static.pib.gov.in/cancel.pdf",
        },
    )
    assert res_empty_sec.leads == [] and res_empty_sec.unevaluable_count == 1

    # 6. Corroborating dual-doc mode requires BOTH primary and secondary token overlap
    corrob_spec = _sample_ugc_net_spec(secondary_role="corroborating_record", record_selector=None)
    uncorrob = run_engine(
        "P9",
        [
            good_records[0],
            {"document": "secondary", "page": 1, "raw_text": "Unrelated administrative circular about office stationery procurement rules."},
        ],
        context={"auditing_government_exams": corrob_spec},
    )
    assert uncorrob.leads == [] and uncorrob.unevaluable_count == 1

    # 7. Structured JSON record_selector match vs miss
    sel_spec = _sample_ugc_net_spec(
        secondary_role="",
        secondary_source_format=None,
        record_selector={"match_field": "exam_code", "match_value": "UGCNET-2024"},
    )
    json_rows = [
        {
            "exam_code": "UGCNET-2024",
            "notice": "National Testing Agency conducted UGC-NET on 18 June 2024; cancelled on 19 June 2024.",
        },
        {"exam_code": "OTHER-2024", "notice": "Completely different recruitment notification text."},
    ]
    sel_ok = run_engine("P9", json_rows, context={"auditing_government_exams": sel_spec})
    assert len(sel_ok.leads) == 1

    sel_miss = run_engine(
        "P9",
        json_rows,
        context={"auditing_government_exams": _sample_ugc_net_spec(secondary_role="", record_selector={"match_field": "exam_code", "match_value": "MISSING-999"})},
    )
    assert sel_miss.leads == [] and sel_miss.unevaluable_count == 1

    # 8. HTML loader extracts visible prose for P9 and strips <script>/<style>
    html_file = tmp_path / "nta_notice.html"
    html_bytes = (
        b"<html><head><style>.x{color:red}</style><script>var secret='UGC-NET';</script></head>"
        b"<body><h1>National Testing Agency</h1>"
        b"<p>Public Notice: UGC-NET conducted on 18 June 2024 stands cancelled as per 19 June 2024 order.</p>"
        b"</body></html>"
    )
    html_file.write_bytes(html_bytes)
    html_sha = compute_sha256(html_bytes)
    html_ds = load_dataset(
        str(html_file),
        context={"auditing_government_exams": spec},
        allow_local_files=True,
        expected_sha256=html_sha,
    )
    assert html_ds.format == "html"
    html_res = run_engine("P9", html_ds.records, context={"auditing_government_exams": spec})
    assert len(html_res.leads) == 1
    assert html_res.leads[0].citations[0]["source_locator"] == "html_body"


# ---------------------------------------------------------------------------
# 4. BNS Sec 356 Neutrality & DPDP Act 2023 PII Scrubbing
# ---------------------------------------------------------------------------


def test_p9_bns_neutralization_and_dpdp_pii_scrubbing() -> None:
    aadhaar = _make_valid_aadhaar("56789012345")
    spec = _sample_ugc_net_spec(
        secondary_role="",
        secondary_source_format=None,
        record_selector=None,
        schedule_evidence_tokens=["UP Police Constable", "17 February 2024"],
        outcome_evidence_tokens=["cancelled", "re-examination"],
    )
    raw_records = [
        {
            "document": "primary",
            "page": 1,
            "raw_text": (
                f"UP Police Constable exam held on 17 February 2024 was cancelled with re-examination ordered. "
                f"Official annexure mentioned Anti-Corruption Bureau inquiry into alleged fraud and rigged papers. "
                f"Candidate contact Rahul Sharma phone +91-9876543210 email rahul.candidate@gmail.com "
                f"PAN ABCDE1234F Aadhaar {aadhaar} IFSC SBIN0001234 Bank Account No 123456789012."
            ),
        }
    ]
    res = run_engine("P9", raw_records, context={"auditing_government_exams": spec})
    assert len(res.leads) == 1
    lead = res.leads[0]
    dumped = json.dumps(lead.model_dump())
    assert aadhaar not in dumped
    assert "ABCDE1234F" not in dumped
    assert "9876543210" not in dumped
    assert "rahul.candidate@gmail.com" not in dumped
    assert "SBIN0001234" not in dumped
    assert "123456789012" not in dumped
    assert contains_pii(lead.model_dump())[0] is False
    assert lint_neutrality(lead.title, lead.summary, lead.benign_explanations).passed
    # Accusatory words inside quoted official text were neutralized
    for cit in lead.citations:
        excerpt_lower = str(cit.get("excerpt") or "").lower()
        assert "fraud" not in excerpt_lower
        assert "rigged" not in excerpt_lower


# ---------------------------------------------------------------------------
# 5. Project Validator (validate_project & validate_context)
# ---------------------------------------------------------------------------


def test_p9_project_validator_accepts_valid_and_rejects_defects() -> None:
    valid_union = _sample_p9_task("p9-union-ugc-net-0001", _sample_ugc_net_spec())
    valid_ctx_sec = _sample_p9_task(
        "p9-state-up-police-0002",
        _sample_ugc_net_spec(
            case_key="p9-state-up-police-0002",
            exam_name="UP Police Constable Recruitment 2024",
            exam_slug="up-police-constable-2024",
            jurisdiction="State:Uttar Pradesh",
            jurisdiction_level="state",
            state_or_ut="Uttar Pradesh",
            authority="UP Police Recruitment and Promotion Board (UPPRPB)",
            authority_key="up-police-recruitment-and-promotion-board",
            authority_type="state_police_or_subordinate",
            exam_category="police_and_defence",
            scheduled_date="2024-02-17",
            actual_or_event_date="2024-02-24",
        ),
        secondary_in_context=True,
    )
    proj = _sample_p9_project([valid_union, valid_ctx_sec])
    assert validate_project(proj, filename_stem=AUDITING_GOVERNMENT_EXAMS_PROJECT_SLUG) == []

    # Bad task ID format
    bad_id = _sample_p9_project([_sample_p9_task("p9-bad-id", _sample_ugc_net_spec())])
    assert any("p9-(union|state|ut)" in e for e in validate_project(bad_id, filename_stem=AUDITING_GOVERNMENT_EXAMS_PROJECT_SLUG))

    # Tier mismatch
    bad_tier = _sample_p9_project(
        [
            _sample_p9_task(
                "p9-union-mismatch-0003",
                _sample_ugc_net_spec(jurisdiction_level="state", jurisdiction="State:Bihar", state_or_ut="Bihar"),
            )
        ]
    )
    assert any("does not match" in e for e in validate_project(bad_tier, filename_stem=AUDITING_GOVERNMENT_EXAMS_PROJECT_SLUG))

    # Wrong financial_quantity_type
    bad_qty = _sample_p9_project([_sample_p9_task("p9-union-qty-0004", _sample_ugc_net_spec(), qty_type="scheme_promises_and_revisions")])
    assert any("public_examination_conduct" in e for e in validate_project(bad_qty, filename_stem=AUDITING_GOVERNMENT_EXAMS_PROJECT_SLUG))

    # Out-of-window exam_year & scheduled_date
    bad_date = _sample_p9_project(
        [_sample_p9_task("p9-union-date-0005", _sample_ugc_net_spec(exam_year=2022, scheduled_date="2023-11-27"))]
    )
    errs_date = validate_project(bad_date, filename_stem=AUDITING_GOVERNMENT_EXAMS_PROJECT_SLUG)
    assert any("exam_year" in e for e in errs_date)
    assert any("3-year audit window" in e for e in errs_date)

    # cancelled_after_conduct with students_took_exam_before_cancellation=False or event < scheduled
    bad_cac = _sample_p9_project(
        [
            _sample_p9_task(
                "p9-union-cac-0006",
                _sample_ugc_net_spec(
                    students_took_exam_before_cancellation=False,
                    scheduled_date="2024-06-18",
                    actual_or_event_date="2024-06-17",
                ),
            )
        ]
    )
    errs_cac = validate_project(bad_cac, filename_stem=AUDITING_GOVERNMENT_EXAMS_PROJECT_SLUG)
    assert any("must be true" in e for e in errs_cac)
    assert any("must not precede scheduled_date" in e for e in errs_cac)

    # flawless_conduct with delay_days > 0 or wrong reason category
    bad_flawless = _sample_p9_project(
        [
            _sample_p9_task(
                "p9-union-flaw-0007",
                _sample_ugc_net_spec(
                    disruption_type="flawless_conduct",
                    disruption_reason_category="administrative_or_logistical",
                    students_took_exam_before_cancellation=False,
                    delay_days=10,
                ),
            )
        ]
    )
    errs_flaw = validate_project(bad_flawless, filename_stem=AUDITING_GOVERNMENT_EXAMS_PROJECT_SLUG)
    assert any("none_flawless" in e for e in errs_flaw)
    assert any("delay_days must be null or 0" in e for e in errs_flaw)

    # BNS forbidden terms inside outcome_summary and tokens
    bad_bns = _sample_p9_project(
        [
            _sample_p9_task(
                "p9-union-bns-0008",
                _sample_ugc_net_spec(
                    outcome_summary="This examination cancellation exposed a giant scam.",
                    schedule_evidence_tokens=["UGC-NET", "corrupt board"],
                ),
            )
        ]
    )
    errs_bns = validate_project(bad_bns, filename_stem=AUDITING_GOVERNMENT_EXAMS_PROJECT_SLUG)
    assert any("outcome_summary" in e and "non-neutral" in e for e in errs_bns)
    assert any("schedule_evidence_tokens" in e and "non-neutral" in e for e in errs_bns)

    # Non-empty secondary_role without secondary_url
    bad_sec = _sample_p9_project(
        [_sample_p9_task("p9-union-sec-0009", _sample_ugc_net_spec(secondary_role="outcome_document"), secondary_url=None)]
    )
    assert any("secondary_url is required" in e for e in validate_project(bad_sec, filename_stem=AUDITING_GOVERNMENT_EXAMS_PROJECT_SLUG))

    # Union with non-null state_or_ut
    bad_union = _sample_p9_project(
        [_sample_p9_task("p9-union-sut-0010", _sample_ugc_net_spec(jurisdiction_level="union", state_or_ut="Bihar"))]
    )
    assert any("state_or_ut must be null" in e for e in validate_project(bad_union, filename_stem=AUDITING_GOVERNMENT_EXAMS_PROJECT_SLUG))

    # Duplicate case_key across distinct task IDs rejected
    dup_case_key_proj = _sample_p9_project(
        [
            _sample_p9_task("p9-union-dup-0011", _sample_ugc_net_spec(case_key="p9-union-shared-case")),
            _sample_p9_task("p9-union-dup-0012", _sample_ugc_net_spec(case_key="p9-union-shared-case")),
        ]
    )
    assert any(
        "case_key 'p9-union-shared-case' is used twice" in e
        for e in validate_project(dup_case_key_proj, filename_stem=AUDITING_GOVERNMENT_EXAMS_PROJECT_SLUG)
    )


def test_p9_primary_citation_falls_back_to_observed_sha256() -> None:
    obs_sha = "d" * 64
    spec = _sample_ugc_net_spec(secondary_role="", secondary_source_format=None, record_selector=None)
    records = [
        {
            "document": "primary",
            "page": 1,
            "raw_text": (
                "National Testing Agency conducted UGC-NET on 18 June 2024; "
                "on 19 June 2024 UGC-NET was cancelled."
            ),
        }
    ]
    res = run_engine(
        "P9",
        records,
        context={"auditing_government_exams": spec, "observed_sha256": obs_sha},
        as_of_date="2026-11-28",
    )
    assert len(res.leads) == 1
    cits = res.leads[0].citations
    assert cits[0]["sha256"] == obs_sha
    assert cits[1]["sha256"] == obs_sha
    assert cits[2]["sha256"] == obs_sha


# ---------------------------------------------------------------------------
# 6. RTI Act 2005 Draft Template (Union, State & UT Routing)
# ---------------------------------------------------------------------------


def test_p9_rti_draft_union_state_and_ut() -> None:
    union_finding = {
        "methodology_code": "P9",
        "entity_name": "National Testing Agency (NTA)",
        "suggested_authority": "National Testing Agency (NTA), Ministry of Education",
        "jurisdiction": "Union of India",
        "fiscal_year": "FY2024-25",
        "monetary_exposure_inr": 0.0,
        "anomaly_metrics": {
            "case_key": "p9-union-ugc-net-0001",
            "exam_slug": "ugc-net-june-2024",
            "schedule_document_ref": "NTA Public Notice 18 June 2024",
            "outcome_document_ref": "MoE Press Release 19 June 2024",
        },
    }
    union_rti = generate_rti_draft(union_finding)
    assert union_rti["jurisdiction_level"] == "central"
    assert union_rti["rti_portal_url"] == "https://rtionline.gov.in"
    assert union_rti["word_count"] <= 500
    assert "Public Examinations (Prevention of Unfair Means) Act, 2024" in union_rti["body_text"]
    assert "NTA Public Notice 18 June 2024" in union_rti["body_text"]

    state_finding = {
        "methodology_code": "P9",
        "entity_name": "UP Police Recruitment and Promotion Board",
        "suggested_authority": "Uttar Pradesh Police Recruitment and Promotion Board",
        "jurisdiction": "State:Uttar Pradesh",
        "fiscal_year": "FY2023-24",
        "monetary_exposure_inr": 0.0,
        "anomaly_metrics": {"case_key": "p9-state-up-police-0002", "exam_slug": "up-police-constable-2024"},
    }
    state_rti = generate_rti_draft(state_finding)
    assert state_rti["jurisdiction_level"] == "state"
    assert state_rti["rti_portal_url"] == "https://rtionline.up.gov.in"
    assert state_rti["central_portal_warning"] is not None
    assert state_rti["word_count"] <= 500

    ut_finding = {
        "methodology_code": "P9",
        "entity_name": "Delhi Subordinate Services Selection Board (DSSSB)",
        "suggested_authority": "DSSSB, Government of NCT of Delhi",
        "jurisdiction": "UT:Delhi",
        "fiscal_year": "FY2024-25",
        "monetary_exposure_inr": 0.0,
        "anomaly_metrics": {"case_key": "p9-ut-dl-dsssb-0003"},
    }
    ut_rti = generate_rti_draft(ut_finding)
    assert ut_rti["rti_portal_url"] == "https://rtionline.delhi.gov.in"
    assert ut_rti["word_count"] <= 500


# ---------------------------------------------------------------------------
# 7. Hub API Integration: Summary, Corpus Fallback, Live Submissions & .md
# ---------------------------------------------------------------------------


def test_p9_hub_summary_corpus_fallback_live_submissions_and_routes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj_dir = tmp_path / "projects"
    proj_dir.mkdir()

    ugc_spec = _sample_ugc_net_spec()
    state_postponed_spec = _sample_ugc_net_spec(
        case_key="p9-state-up-roaro-0002",
        exam_name="UPPSC RO/ARO Prelims 2024",
        exam_slug="uppsc-ro-aro-2024",
        cycle_or_stage="Preliminary Examination 2024",
        exam_year=2024,
        jurisdiction="State:Uttar Pradesh",
        jurisdiction_level="state",
        state_or_ut="Uttar Pradesh",
        authority="Uttar Pradesh Public Service Commission (UPPSC)",
        authority_key="uttar-pradesh-public-service-commission-uppsc",
        authority_type="state_psc",
        exam_category="civil_services_and_psc",
        scheduled_date="2024-02-11",
        actual_or_event_date="2024-03-02",
        delay_days=20,
        disruption_type="postponed",
        disruption_reason_category="administrative_or_logistical",
        students_took_exam_before_cancellation=False,
        affected_candidates_count=1076004,
        affected_candidates_basis="UPPSC published application count of 10,76,004 candidates.",
        schedule_evidence_tokens=["UPPSC", "11 February 2024"],
        outcome_evidence_tokens=["postponed", "Uttar Pradesh"],
        is_anchor_case=False,
        secondary_role="",
        secondary_source_format=None,
        record_selector=None,
    )
    ut_flawless_spec = _sample_ugc_net_spec(
        case_key="p9-ut-dl-dsssb-0003",
        exam_name="DSSSB Assistant Teacher Exam 2025",
        exam_slug="dsssb-asst-teacher-2025",
        cycle_or_stage="Tier-I CBT 2025",
        exam_year=2025,
        jurisdiction="UT:Delhi",
        jurisdiction_level="ut",
        state_or_ut="Delhi",
        authority="Delhi Subordinate Services Selection Board (DSSSB)",
        authority_key="delhi-subordinate-services-selection-board-dsssb",
        authority_type="ut_recruitment_or_board",
        exam_category="teaching_and_faculty",
        scheduled_date="2025-07-12",
        actual_or_event_date="2025-07-12",
        delay_days=0,
        disruption_type="flawless_conduct",
        disruption_reason_category="none_flawless",
        students_took_exam_before_cancellation=False,
        affected_candidates_count=None,
        affected_candidates_basis="Not disclosed in cited official schedule completion notice.",
        schedule_evidence_tokens=["DSSSB", "12 July 2025"],
        outcome_evidence_tokens=["conducted", "Delhi"],
        is_anchor_case=False,
        secondary_role="",
        secondary_source_format=None,
        record_selector=None,
    )

    project_data = _sample_p9_project(
        [
            _sample_p9_task("p9-union-ugc-net-0001", ugc_spec),
            _sample_p9_task("p9-state-up-roaro-0002", state_postponed_spec, secondary_url=None),
            _sample_p9_task("p9-ut-dl-dsssb-0003", ut_flawless_spec, secondary_url=None),
        ]
    )
    assert validate_project(project_data, filename_stem=AUDITING_GOVERNMENT_EXAMS_PROJECT_SLUG) == []
    (proj_dir / "auditing-government-exams.json").write_text(json.dumps(project_data), encoding="utf-8")

    monkeypatch.setenv("AIWATCHDOGS_PROJECTS_DIR", str(proj_dir))
    monkeypatch.delenv("AIWATCHDOGS_KEEP_LEGACY_MISSIONS", raising=False)

    # 1. Fallback aggregation WITHOUT coverage.json (and corpus fallback leads!)
    app_fb = create_app(db_path=tmp_path / "p9_fallback.db", seed=True)
    with TestClient(app_fb) as client:
        r_sum = client.get("/api/projects/auditing-government-exams/summary")
        assert r_sum.status_code == 200, r_sum.text
        fb = r_sum.json()
        assert fb["project_slug"] == AUDITING_GOVERNMENT_EXAMS_PROJECT_SLUG
        assert fb["project_code"] == "P9"
        assert fb["methodology_code"] == "P9"
        assert fb["hook"] == P9_DEFAULT_HOOK
        assert fb["audit_window"]["start_date"] == "2023-11-28"
        assert fb["audit_window"]["end_date"] == "2026-11-28"
        assert fb["total_tasks"] == 3
        assert fb["completed_tasks"] == 0
        assert fb["published_leads"] == 0
        assert fb["cleared_tasks"] == 0
        assert fb["unevaluable_tasks"] == 0
        assert fb["leads_source"] == "corpus_fallback"

        hc = fb["headline_counts"]
        assert hc["total_audited"] == 3
        assert hc["not_flawless_count"] == 2
        assert hc["flawless_count"] == 1
        assert hc["not_flawless_rate_pct"] == 66.67
        assert hc["cancelled_after_conduct"] == 1
        assert hc["postponed"] == 1
        assert hc["rescheduled"] == 0
        assert hc["delayed"] == 0
        assert hc["cancelled_before_conduct"] == 0
        assert hc["partial_reexam_or_glitch"] == 0
        assert hc["documented_affected_candidates_total"] == 908580 + 1076004

        assert len(fb["anchor_cases"]) == 1
        assert fb["anchor_cases"][0]["task_id"] == "p9-union-ugc-net-0001"
        assert len(fb["leads"]) == 2
        assert all(item["status"] == "corpus_queued" and item["finding_id"] is None for item in fb["leads"])
        assert fb["leads"][0]["disruption_type"] == "cancelled_after_conduct"
        assert len(fb["jurisdictions"]) == 3
        assert fb["jurisdictions"][0]["jurisdiction"] == "Union of India"
        assert len(fb["top_authorities"]) == 3
        assert len(fb["by_year"]) == 4
        assert fb["dry_run_summary"]["evaluated_tasks"] == 3
        assert fb["dry_run_summary"]["flagged_leads"] == 2
        assert fb["dry_run_summary"]["cleared_flawless"] == 1

        # Must match GET /api/missions/auditing-government-exams -> visual_summary.auditing_government_exams
        r_mission = client.get("/api/missions/auditing-government-exams")
        assert r_mission.status_code == 200
        assert r_mission.json()["visual_summary"]["auditing_government_exams"] == fb

    # 2. Add coverage.json + documents.csv and verify live findings + cleared submission transition
    p9_assets = proj_dir / "auditing-government-exams"
    p9_assets.mkdir()
    coverage_obj = {
        "project_slug": AUDITING_GOVERNMENT_EXAMS_PROJECT_SLUG,
        "project_code": "P9",
        "methodology_code": "P9",
        "hook": P9_DEFAULT_HOOK,
        "generated_on": "2026-11-28",
        "as_of_date": "2026-11-28",
        "total_tasks": 3,
        "unique_exams_covered": 3,
        "unique_authorities_covered": 3,
        "unique_documents_count": 4,
        "states_and_uts_covered": 2,
        "headline_counts": {
            "total_audited": 3,
            "not_flawless_count": 2,
            "flawless_count": 1,
            "not_flawless_rate_pct": 66.67,
            "flawless_rate_pct": 33.33,
            "cancelled_after_conduct": 1,
            "postponed": 1,
            "rescheduled": 0,
            "delayed": 0,
            "cancelled_before_conduct": 0,
            "partial_reexam_or_glitch": 0,
            "documented_affected_candidates_total": 1984584,
        },
        "by_disruption_type": {
            "cancelled_after_conduct": {"tasks": 1, "documented_affected_candidates": 908580},
            "postponed": {"tasks": 1, "documented_affected_candidates": 1076004},
            "flawless_conduct": {"tasks": 1, "documented_affected_candidates": 0},
        },
        "by_jurisdiction_level": {
            "union": {"tasks": 1, "not_flawless": 1, "flawless": 0, "cancelled_after_conduct": 1, "cleared": 99},
            "state": {"tasks": 1, "not_flawless": 1, "flawless": 0, "cancelled_after_conduct": 0, "cleared": 88},
            "ut": {"tasks": 1, "not_flawless": 0, "flawless": 1, "cancelled_after_conduct": 0, "cleared": 77},
        },
        "by_authority_type": [
            {"authority_type": "central_testing_agency", "tasks": 1, "not_flawless": 1, "flawless": 0, "cancelled_after_conduct": 1},
            {"authority_type": "state_psc", "tasks": 1, "not_flawless": 1, "flawless": 0, "cancelled_after_conduct": 0},
            {"authority_type": "ut_recruitment_or_board", "tasks": 1, "not_flawless": 0, "flawless": 1, "cancelled_after_conduct": 0},
        ],
        "by_exam_category": [
            {"exam_category": "teaching_and_faculty", "tasks": 2, "not_flawless": 1, "flawless": 1, "cancelled_after_conduct": 1},
            {"exam_category": "civil_services_and_psc", "tasks": 1, "not_flawless": 1, "flawless": 0, "cancelled_after_conduct": 0},
        ],
        "by_year": [
            {"year": 2024, "tasks": 2, "not_flawless": 2, "flawless": 0, "cancelled_after_conduct": 1, "postponed": 1},
            {"year": 2025, "tasks": 1, "not_flawless": 0, "flawless": 1},
        ],
        "by_reason_category": [
            {"reason_category": "paper_integrity_compromise", "tasks": 1},
            {"reason_category": "administrative_or_logistical", "tasks": 1},
            {"reason_category": "none_flawless", "tasks": 1},
        ],
        "jurisdictions": [
            {"jurisdiction": "Union of India", "jurisdiction_level": "union", "state_or_ut": None, "tasks": 1, "not_flawless": 1, "flawless": 0, "cancelled_after_conduct": 1, "documented_affected_candidates": 908580},
            {"jurisdiction": "State:Uttar Pradesh", "jurisdiction_level": "state", "state_or_ut": "Uttar Pradesh", "tasks": 1, "not_flawless": 1, "flawless": 0, "postponed": 1, "documented_affected_candidates": 1076004},
            {"jurisdiction": "UT:Delhi", "jurisdiction_level": "ut", "state_or_ut": "Delhi", "tasks": 1, "not_flawless": 0, "flawless": 1},
        ],
        "top_authorities": [
            {"authority": ugc_spec["authority"], "authority_key": ugc_spec["authority_key"], "authority_type": "central_testing_agency", "jurisdiction": "Union of India", "jurisdiction_level": "union", "tasks": 1, "not_flawless": 1, "flawless": 0, "cancelled_after_conduct": 1},
        ],
        "anchor_cases": [
            {
                "task_id": "p9-union-ugc-net-0001",
                "case_key": "p9-union-ugc-net-0001",
                "exam_name": "UGC-NET June 2024",
                "exam_slug": "ugc-net-june-2024",
                "cycle_or_stage": ugc_spec["cycle_or_stage"],
                "exam_year": 2024,
                "jurisdiction": "Union of India",
                "jurisdiction_level": "union",
                "state_or_ut": None,
                "authority": ugc_spec["authority"],
                "authority_key": ugc_spec["authority_key"],
                "authority_type": "central_testing_agency",
                "exam_category": "teaching_and_faculty",
                "scheduled_date": "2024-06-18",
                "actual_or_event_date": "2024-06-19",
                "delay_days": 64,
                "disruption_type": "cancelled_after_conduct",
                "disruption_reason_category": "paper_integrity_compromise",
                "students_took_exam_before_cancellation": True,
                "schedule_summary": ugc_spec["schedule_summary"],
                "schedule_document_ref": ugc_spec["schedule_document_ref"],
                "outcome_summary": ugc_spec["outcome_summary"],
                "outcome_document_ref": ugc_spec["outcome_document_ref"],
                "affected_candidates_count": 908580,
                "affected_candidates_basis": ugc_spec["affected_candidates_basis"],
                "statutory_anchor": ugc_spec["statutory_anchor"],
                "source_url": "https://www.nta.ac.in/Download/Notice/Notice_20240618_UGCNET.pdf",
                "secondary_url": "https://static.pib.gov.in/WriteReadData/specificdocs/documents/2024/jun/ugcnet_cancel.pdf",
                "portal_url": "https://www.nta.ac.in/",
                "severity": "critical",
            }
        ],
        "dry_run_summary": {
            "status": "verified",
            "evaluated_tasks": 3,
            "flagged_leads": 2,
            "cleared_flawless": 1,
            "unevaluable": 0,
            "verified_documents": 4,
            "as_of_date": "2026-11-28",
        },
    }
    (p9_assets / "coverage.json").write_text(json.dumps(coverage_obj), encoding="utf-8")
    (p9_assets / "documents.csv").write_text(
        "url,sha256,publisher\nhttps://www.nta.ac.in/Download/Notice/Notice_20240618_UGCNET.pdf," + "a" * 64 + ",NTA\n",
        encoding="utf-8",
    )

    app = create_app(db_path=tmp_path / "p9_live.db", seed=True)
    with TestClient(app) as client:
        assert client.get("/api/missions/auditing-government-exams/coverage.json").status_code == 200
        assert client.get("/api/missions/auditing-government-exams/documents.csv").status_code == 200
        assert client.get("/api/missions/auditing-government-exams/secret.txt").status_code == 404

        reg = client.post(
            "/api/agents/register",
            json={
                "handle": "p9-auditor-01",
                "display_name": "P9 Auditor",
                "provider": "rule-engine",
                "model": "deterministic-python-v1",
                "coding_agent": "Claude Code",
                "coding_agent_model": "claude-sonnet-4",
                "mode": "auto",
            },
        ).json()
        headers = {"X-Agent-Token": reg["api_token"]}

        # Claim & submit Task #1 (UGC-NET anchor finding)
        lease1 = client.post("/api/desk/next", headers=headers, json={"task_id": "prj-p9-union-ugc-net-0001"}).json()
        task1_ctx = _build_task_context(lease1["task"]["payload"])
        assert "auditing_government_exams" in task1_ctx

        lead_res = run_engine(
            "P9",
            [
                {
                    "document": "primary",
                    "page": 1,
                    "raw_text": "National Testing Agency conducted UGC-NET examination on 18 June 2024 across India.",
                },
                {
                    "document": "secondary",
                    "page": 2,
                    "raw_text": "Ministry of Education order dated 19 June 2024 cancelled UGC-NET June 2024 examination.",
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
        ugc_finding_id = sub1.json()["finding_id"]

        # Claim & submit Task #3 (Flawless cleared control)
        lease3 = client.post("/api/desk/next", headers=headers, json={"task_id": "prj-p9-ut-dl-dsssb-0003"}).json()
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

        # Verify summary reflects live submissions and never leaks stale coverage.json cleared counts (99/88/77)!
        sum_resp = client.get("/api/projects/auditing-government-exams/summary")
        assert sum_resp.status_code == 200
        summary = sum_resp.json()
        assert summary["published_leads"] == 1
        assert summary["peer_review"] == 1
        assert summary["cleared_tasks"] == 1
        assert summary["completed_tasks"] == 2
        assert summary["unevaluable_tasks"] == 0
        assert summary["leads_source"] == "live"
        assert len(summary["leads"]) == 1
        assert summary["leads"][0]["finding_id"] == ugc_finding_id
        assert summary["leads"][0]["disruption_type"] == "cancelled_after_conduct"
        assert (
            summary["leads"][0]["secondary_url"]
            == "https://static.pib.gov.in/WriteReadData/specificdocs/documents/2024/jun/ugcnet_cancel.pdf"
        )
        assert summary["leads"][0]["portal_url"] == "https://www.nta.ac.in/"
        assert summary["anchor_cases"][0]["finding_id"] == ugc_finding_id

        # Claim & submit Task #2 with unevaluable_count=1 to verify missions.unevaluable_count surfaces in summary
        lease2 = client.post("/api/desk/next", headers=headers, json={"task_id": "prj-p9-state-up-roaro-0002"}).json()
        sub2 = client.post(
            "/api/desk/submit",
            headers=headers,
            json={
                "lease_id": lease2["lease_id"],
                "outcome": "cleared",
                "metrics": {
                    "records_scanned": 1,
                    "cleared_count": 0,
                    "unevaluable_count": 1,
                    "monetary_audited_inr": 0.0,
                },
            },
        )
        assert sub2.status_code == 200, sub2.text
        sum_after_uneval = client.get("/api/projects/auditing-government-exams/summary").json()
        assert sum_after_uneval["unevaluable_tasks"] == 1

        assert summary["by_jurisdiction_level"]["union"]["done"] == 1
        assert summary["by_jurisdiction_level"]["union"]["leads"] == 1
        assert summary["by_jurisdiction_level"]["union"]["cleared"] == 0
        assert summary["by_jurisdiction_level"]["state"]["done"] == 0
        assert summary["by_jurisdiction_level"]["state"]["cleared"] == 0
        assert summary["by_jurisdiction_level"]["ut"]["done"] == 1
        assert summary["by_jurisdiction_level"]["ut"]["cleared"] == 1
        assert summary["by_disruption_type"]["cancelled_after_conduct"]["done"] == 1
        assert summary["by_disruption_type"]["cancelled_after_conduct"]["leads"] == 1
        assert summary["by_disruption_type"]["flawless_conduct"]["done"] == 1
        assert summary["by_disruption_type"]["flawless_conduct"]["cleared"] == 1

        # Markdown & Discovery routes render P9 cleanly
        md_resp = client.get("/projects/auditing-government-exams.md")
        assert md_resp.status_code == 200
        assert "## Auditing Government Exams (P9)" in md_resp.text
        assert "/api/projects/auditing-government-exams/summary" in md_resp.text
        assert "UGC-NET June 2024" in md_resp.text

        brief_resp = client.get("/brief.md")
        assert brief_resp.status_code == 200
        assert "auditing-government-exams.md" in brief_resp.text

        join_resp = client.get("/join.txt")
        assert join_resp.status_code == 200
        assert "auditing-government-exams" in join_resp.text

        llms_resp = client.get("/llms.txt")
        assert llms_resp.status_code == 200
        assert "P1, W1, UB1, OR1, P5, P6, P7, P8, P9 and P10" in llms_resp.text
        assert "/projects/auditing-government-exams.md" in llms_resp.text

    # Sanity check signal category alignment with rules dict
    assert set(SIGNAL_CATEGORIES) == set(DISRUPTION_TYPE_RULES.keys())
