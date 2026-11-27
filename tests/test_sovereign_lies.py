"""Comprehensive unit and integration tests for Project 8: Sovereign Lies (P8)."""

from __future__ import annotations

import copy
import json
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
from aiwatchdogs.engines.registry import METHODOLOGY_CATALOG, RULESET_VERSIONS, get_engine_fingerprint, run_engine
from aiwatchdogs.engines.sovereign_lies import (
    P8_DEFAULT_HOOK,
    P8_TASK_ID_RE,
    SCHEME_CATEGORY_LABELS,
    SIGNAL_CATEGORIES,
    SOVEREIGN_LIES_METHODOLOGY,
    SOVEREIGN_LIES_PROJECT_SLUG,
    WALKBACK_MECHANISM_RULES,
    evaluate_walkback_rule,
)
from aiwatchdogs.hub.app import create_app
from aiwatchdogs.projects import CANONICAL_PROJECT_CODES, VALID_QUANTITY_TYPES, validate_project


def _make_valid_aadhaar(prefix_11: str) -> str:
    assert len(prefix_11) == 11 and prefix_11[0] in "23456789"
    return prefix_11 + verhoeff_generate_check_digit(prefix_11)


def _create_pdf(path: Path, pages_lines: list[list[str]]) -> str:
    """Create a real valid multi-page PDF with extractable text and return its SHA-256."""
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


def _sample_sgb_spec(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "case_key": "p8-union-sgb-anchor-0001",
        "scheme_name": "Sovereign Gold Bond (SGB) Scheme",
        "scheme_slug": "sovereign-gold-bond",
        "tranche_or_phase": "All 67 Tranches (2015-16 Series I to 2023-24 Series IV) — Capital Gains Tax Exemption",
        "jurisdiction": "Union of India",
        "jurisdiction_level": "union",
        "state_or_ut": None,
        "authority": "Department of Economic Affairs, Ministry of Finance & Reserve Bank of India",
        "authority_key": "department-of-economic-affairs-ministry-of-finance-and-reserve-bank-of-india",
        "scheme_category": "sovereign_bonds_small_savings",
        "government_era_or_launch_year": "2015 (Union)",
        "promise_date": "2015-10-30",
        "promise_summary": (
            "Original Government of India Gazette notification and Finance Act 2016 Section 47(viic) "
            "provided full capital gains tax exemption on redemption of Sovereign Gold Bonds by individuals."
        ),
        "promise_document_ref": "Finance Act 2016 Sec. 47(viic) & GoI Notification F.No.4(19)-W&M/2014",
        "promise_evidence_tokens": ["Sovereign Gold Bond", "47(viic)", "capital gains", "redemption"],
        "walkback_date": "2026-02-01",
        "walkback_summary": (
            "Union Budget 2026 / Finance Bill 2026 Clause 35 restricted the maturity capital gains tax "
            "exemption exclusively to original subscribers who subscribed at initial issue and held continuously until maturity."
        ),
        "walkback_document_ref": "Finance Bill 2026 Clause 35 / Memorandum Explaining Provisions",
        "walkback_evidence_tokens": ["original subscriber", "Sovereign Gold Bond", "continuously until maturity"],
        "walkback_mechanism": "retroactive_tax_reversal",
        "retroactive_impact": "retroactive_existing_holders",
        "promised_metric": "100% capital gains tax exemption on maturity redemption for all individual holders",
        "revised_metric": "Capital gains exemption limited strictly to original primary subscribers holding continuously to maturity",
        "metric_unit": "tax_exemption_scope",
        "affected_group": "Secondary-market retail Sovereign Gold Bond holders across active tranches",
        "estimated_exposure_inr": None,
        "exposure_basis": None,
        "statutory_anchor": "Income-tax Act 1961 Sec. 47(viic) & Finance Bill 2026 Clause 35",
        "benign_explanation_context": (
            "Parliament exercises plenary statutory taxing authority to align capital gains tax exemptions "
            "with primary-issue investor incentives and curb secondary-market arbitrage."
        ),
        "is_sgb_anchor": True,
        "is_anchor_case": True,
        "source_format": "pdf",
        "secondary_source_format": "pdf",
        "secondary_role": "walkback_document",
        "record_selector": {"promise_page": 1, "walkback_page": 2, "section_hint": "Sec. 47(viic) / Clause 35"},
    }
    base.update(overrides)
    return base


def _sample_p8_task(
    task_id: str,
    spec: dict[str, Any],
    *,
    retrieval_url: str = "https://www.indiabudget.gov.in/budget2016-17/ub2016-17/memo/mem1.pdf",
    expected_sha256: str = "a" * 64,
    secondary_url: str | None = "https://www.indiabudget.gov.in/doc/memo.pdf",
    secondary_expected_sha256: str | None = "b" * 64,
    qty_type: str = "scheme_promises_and_revisions",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "retrieval_url": retrieval_url,
        "expected_sha256": expected_sha256,
        "official_publisher": "Ministry of Finance, Government of India",
        "source_landing_page": "https://www.indiabudget.gov.in/",
        "as_of_date": "2026-11-28",
        "context": {
            "as_of_date": "2026-11-28",
            "sovereign_lies": spec,
        },
    }
    if secondary_url is not None:
        payload["secondary_url"] = secondary_url
        if secondary_expected_sha256 is not None:
            payload["secondary_expected_sha256"] = secondary_expected_sha256
    return {
        "id": task_id,
        "title": f"{spec['scheme_name']} · {spec['tranche_or_phase']}"[:140],
        "instructions": "Verify SHA-256 digests and run deterministic P8 documentary check on launch vs subsequent record.",
        "type": "run_check",
        "methodology_code": "P8",
        "dataset_key": f"p8-{spec['scheme_slug']}",
        "financial_quantity_type": qty_type,
        "estimated_tokens": 1800,
        "estimate_basis": "Deterministic P8 PDF/HTML/JSON documentary verification (zero LLM cost).",
        "payload": payload,
    }


def _sample_p8_project(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "slug": SOVEREIGN_LIES_PROJECT_SLUG,
        "project_code": "P8",
        "priority": 8,
        "title": "Sovereign Lies: Did Indian governments keep their scheme promises?",
        "summary": (
            "Audits Union and State/UT government schemes where launch notifications promised specific "
            "tax exemptions, payouts, subsidies, or exit rules that were subsequently narrowed, cut, or discontinued."
        ),
        "posted_by": {
            "name": "AI Watchdogs India",
            "handle": "@aiwatchdogsIN",
            "url": "https://aiwatchdogs.in",
        },
        "pillar": "scheme",
        "status": "active",
        "methodology_code": "P8",
        "jurisdiction": "India (Union Government, 28 States & Union Territories)",
        "authority": "Ministry of Finance, Reserve Bank of India, Union Ministries & State Finance Departments",
        "statutory_anchors": [
            "Constitution-Art-265",
            "Income-Tax-Act-Sec-47-viic",
            "Finance-Act-2026",
            "RTI-Act-2005-Sec-4-1-b",
        ],
        "scope": [
            "Documentary comparison of launch scheme commitments against subsequent Finance Acts, Gazette notifications, and CAG/Parliament records.",
            "Anchored by the Sovereign Gold Bond (SGB) Scheme and covering Union + State/UT schemes plus Cleared grandfathered controls.",
        ],
        "out_of_scope": [
            "Informal political speeches lacking official Gazette, Budget, RBI, Parliamentary, or CAG records.",
        ],
        "sources": [
            {
                "name": "Union Budget Documents & Finance Bills (Ministry of Finance)",
                "publisher": "Ministry of Finance, Government of India",
                "url": "https://www.indiabudget.gov.in/",
            }
        ],
        "tasks": tasks,
    }


# ---------------------------------------------------------------------------
# 1. Registry, Project Registration & SGB Anchor Dual-Document PDF Test
# ---------------------------------------------------------------------------


def test_p8_registration_and_sgb_anchor_dual_pdf_end_to_end(tmp_path: Path) -> None:
    assert CANONICAL_PROJECT_CODES[SOVEREIGN_LIES_PROJECT_SLUG] == "P8"
    assert "scheme_promises_and_revisions" in VALID_QUANTITY_TYPES
    assert "P8" in METHODOLOGY_CATALOG
    assert RULESET_VERSIONS["P8"] == "P8-2026.11-v1"
    assert "sovereign_lies" in _ALLOWED_TASK_CONTEXT_KEYS
    assert {"walkback_mechanism", "retroactive_impact", "scheme_category", "jurisdiction_level"} <= CRITICAL_SEMANTIC_METRIC_KEYS
    assert "scheme_slug" in RECORD_IDENTIFIER_KEYS

    promise_pdf = tmp_path / "sgb_promise_2016.pdf"
    promise_sha = _create_pdf(
        promise_pdf,
        [
            [
                "Finance Act 2016 - Explanatory Memorandum",
                "Amendment to Section 47(viic) of the Income-tax Act, 1961:",
                "Any transfer of Sovereign Gold Bond issued by the Reserve Bank of India",
                "under the Sovereign Gold Bond Scheme 2015 by way of redemption by an assessee",
                "being an individual shall be exempt from capital gains tax.",
            ]
        ],
    )
    walkback_pdf = tmp_path / "finance_bill_2026.pdf"
    walkback_sha = _create_pdf(
        walkback_pdf,
        [
            ["Union Budget 2026-27 Memorandum Explaining the Provisions in the Finance Bill, 2026"],
            [
                "Clause 35 - Rationalisation of Sovereign Gold Bond redemption exemption:",
                "It is proposed to restrict capital gains exemption on redemption of Sovereign Gold Bond",
                "solely to an original subscriber who subscribed at the time of original issue and held",
                "the bond continuously until maturity.",
            ],
        ],
    )

    spec = _sample_sgb_spec()
    ctx = {
        "as_of_date": "2026-11-28",
        "retrieval_url": "https://www.indiabudget.gov.in/budget2016-17/memo.pdf",
        "expected_sha256": promise_sha,
        "secondary_path": str(walkback_pdf),
        "secondary_url": "https://www.indiabudget.gov.in/doc/memo.pdf",
        "secondary_expected_sha256": walkback_sha,
        "official_publisher": "Ministry of Finance, Government of India",
        "source_landing_page": "https://www.indiabudget.gov.in/",
        "sovereign_lies": spec,
    }
    ds = load_dataset(
        str(promise_pdf),
        context={**ctx, "secondary_url": str(walkback_pdf)},
        allow_local_files=True,
        expected_sha256=promise_sha,
        secondary_expected_sha256=walkback_sha,
    )
    assert ds.digest_verified is True
    assert ds.secondary_digest_verified is True
    assert ds.sha256 == promise_sha
    assert ds.secondary_sha256 == walkback_sha
    assert any(r.get("document") == "primary" for r in ds.records)
    assert any(r.get("document") == "secondary" for r in ds.records)

    res = run_engine("P8", ds.records, context=ctx, as_of_date="2026-11-28")
    assert res.unevaluable_count == 0
    assert res.cleared_count == 0
    assert len(res.leads) == 1

    lead = res.leads[0]
    assert lead.methodology_code == "P8"
    assert lead.rule_triggered == "P8-RETROACTIVE-TAX-REVERSAL"
    assert lead.severity == "critical"
    assert lead.jurisdiction == "Union of India"
    assert lead.monetary_exposure_inr == 0.0
    assert lead.amount_type == "scheme_promises_and_revisions"

    am = lead.anomaly_metrics
    assert am["case_key"] == "p8-union-sgb-anchor-0001"
    assert am["is_sgb_anchor"] is True
    assert am["is_anchor_case"] is True
    assert am["signal_category"] == "retroactive_tax_reversal"
    assert am["walkback_mechanism"] == "retroactive_tax_reversal"
    assert am["retroactive_impact"] == "retroactive_existing_holders"
    assert am["days_from_promise_to_walkback"] == 3747
    assert "P8-RETROACTIVE-TAX-REVERSAL" in am["triggered_rules"]
    assert "P8-RETROACTIVE-EXISTING-HOLDERS" in am["triggered_rules"]
    assert "P8-SGB-SOVEREIGN-COMMITMENT" in am["triggered_rules"]
    assert "47(viic)" in am["promise_excerpt"]
    assert "original subscriber" in am["walkback_excerpt"]
    assert am["promise_locator"].startswith("page=1")
    assert am["walkback_locator"].startswith("page=2")

    assert len(lead.citations) == 3
    roles = {c["role"]: c for c in lead.citations}
    assert roles["original_promise"]["sha256"] == promise_sha
    assert roles["original_promise"]["source_url"] == "https://www.indiabudget.gov.in/budget2016-17/memo.pdf"
    assert roles["subsequent_walkback"]["sha256"] == walkback_sha
    assert roles["subsequent_walkback"]["source_url"] == "https://www.indiabudget.gov.in/doc/memo.pdf"
    assert roles["subsequent_walkback"]["page"] == 2

    assert lint_neutrality(lead.title, lead.summary, lead.benign_explanations).passed
    assert not contains_pii(lead.model_dump())[0]

    # Deterministic fingerprint & reproducibility check
    res2 = run_engine("P8", ds.records, context=ctx, as_of_date="2026-11-28")
    assert res.script_hash == res2.script_hash
    assert res.leads[0].anomaly_metrics == res2.leads[0].anomaly_metrics
    fp = get_engine_fingerprint("P8", config=ctx, as_of_date="2026-11-28")
    assert fp["ruleset_version"] == "P8-2026.11-v1"


# ---------------------------------------------------------------------------
# 2. All 6 Walk-Back Rules, State/UT Cases, & Cleared Grandfathered Control
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mechanism", "impact", "expected_rule", "expected_severity"),
    [
        ("retroactive_tax_reversal", "retroactive_existing_holders", "P8-RETROACTIVE-TAX-REVERSAL", "critical"),
        ("midstream_benefit_cut", "mid_stream_active_scheme", "P8-MIDSTREAM-BENEFIT-CUT", "high"),
        ("premature_discontinuation", "prospective_discontinuation", "P8-PREMATURE-DISCONTINUATION", "medium"),
        ("eligibility_narrowing", "eligibility_narrowing", "P8-ELIGIBILITY-NARROWING", "medium"),
        ("lockin_or_exit_restriction", "retroactive_existing_holders", "P8-LOCKIN-OR-EXIT-RESTRICTION", "critical"),
        ("arrears_or_unfunded_promise", "unfulfilled_arrears", "P8-ARREARS-OR-UNFUNDED-PROMISE", "high"),
    ],
)
def test_p8_all_six_walkback_mechanisms_and_rules(
    mechanism: str,
    impact: str,
    expected_rule: str,
    expected_severity: str,
) -> None:
    flagged, rule, sig, rules, sev = evaluate_walkback_rule(
        walkback_mechanism=mechanism,
        retroactive_impact=impact,
        is_sgb_anchor=False,
        scheme_slug="sample-scheme",
        estimated_exposure_inr=None,
        days_from_promise_to_walkback=500,
    )
    assert flagged is True
    assert rule == expected_rule
    assert sig == mechanism
    assert expected_rule in rules
    assert sev == expected_severity

    # Full run_engine verification in single-document mode
    spec = _sample_sgb_spec(
        case_key=f"p8-state-mh-{mechanism[:12].replace('_', '-')}-0010",
        scheme_name="Maharashtra State Scheme Audit Sample",
        scheme_slug="maharashtra-scheme-sample",
        tranche_or_phase="FY2024-25 Phase I",
        jurisdiction="State:Maharashtra",
        jurisdiction_level="state",
        state_or_ut="Maharashtra",
        authority="Finance Department, Government of Maharashtra",
        scheme_category="cash_transfers_guarantees",
        promise_date="2024-06-28",
        walkback_date="2025-03-15",
        promise_evidence_tokens=["Government Resolution", "INR 1,500 per month"],
        walkback_evidence_tokens=["revised eligibility", "verification drive"],
        walkback_mechanism=mechanism,
        retroactive_impact=impact,
        is_sgb_anchor=False,
        is_anchor_case=False,
        secondary_role="",
        record_selector=None,
    )
    doc_records = [
        {
            "page": 1,
            "raw_text": (
                "Government Resolution dated 28 June 2024 notified INR 1,500 per month assistance. "
                "Subsequent Finance Department circular dated 15 March 2025 introduced revised eligibility "
                "and statewide verification drive modifying beneficiary coverage."
            ),
        }
    ]
    res = run_engine("P8", doc_records, context={"sovereign_lies": spec, "as_of_date": "2026-11-28"})
    assert len(res.leads) == 1
    assert res.cleared_count == 0
    assert res.unevaluable_count == 0
    assert res.leads[0].rule_triggered == expected_rule
    assert res.leads[0].severity == expected_severity
    assert res.leads[0].jurisdiction == "State:Maharashtra"
    assert res.leads[0].anomaly_metrics["state_or_ut"] == "Maharashtra"
    assert "P8-RAPID-WALKBACK-LE-365D" in res.leads[0].anomaly_metrics["triggered_rules"]


def test_p8_cleared_grandfathered_or_honored_benchmark_and_fail_closed() -> None:
    spec = _sample_sgb_spec(
        case_key="p8-union-ppf-grandfathered-0990",
        scheme_name="Public Provident Fund (PPF) Regular Accounts",
        scheme_slug="public-provident-fund-regular",
        tranche_or_phase="Regular Individual EEE Tax Treatment",
        walkback_mechanism="grandfathered_or_honored",
        retroactive_impact="grandfathered_or_honored",
        promise_evidence_tokens=["Public Provident Fund", "exempt from income tax"],
        walkback_evidence_tokens=["regular PPF accounts", "continues exempt"],
        walkback_date=None,
        walkback_summary="Regular PPF accounts retained full EEE tax exemption intact under Finance Act revisions.",
        is_sgb_anchor=False,
        is_anchor_case=False,
        secondary_role="",
        record_selector=None,
    )
    # 1. With matching verified documentary bytes -> Cleared (cleared_count=1, leads=[])
    good_records = [
        {
            "page": 1,
            "raw_text": (
                "Official Ministry of Finance clarification: Public Provident Fund deposits remain "
                "exempt from income tax under Section 10(11). All regular PPF accounts continues exempt "
                "without retrospective curtailment."
            ),
        }
    ]
    res_clear = run_engine("P8", good_records, context={"sovereign_lies": spec, "as_of_date": "2026-11-28"})
    assert res_clear.leads == []
    assert res_clear.cleared_count == 1
    assert res_clear.unevaluable_count == 0
    assert "grandfathered_or_honored" in res_clear.summary

    # 2. Without matching documentary bytes -> MUST fail closed as unevaluable (NEVER auto-clear!)
    bad_records = [
        {
            "page": 1,
            "raw_text": "Unrelated circular about coastal shipping port tariff schedules for FY 2025-26.",
        }
    ]
    res_un = run_engine("P8", bad_records, context={"sovereign_lies": spec, "as_of_date": "2026-11-28"})
    assert res_un.leads == []
    assert res_un.cleared_count == 0
    assert res_un.unevaluable_count == 1


# ---------------------------------------------------------------------------
# 3. Fail-Closed Guarantees, Record Selector, Secondary Roles, & Digest Errors
# ---------------------------------------------------------------------------


def test_p8_fail_closed_on_missing_tokens_empty_pdf_and_selector_miss(tmp_path: Path) -> None:
    spec = _sample_sgb_spec(secondary_role="", record_selector=None)

    # Empty / scanned image-only PDF (<40 chars) -> unevaluable
    res_empty = run_engine("P8", [{"page": 1, "raw_text": "Page 1"}], context={"sovereign_lies": spec})
    assert res_empty.leads == []
    assert res_empty.cleared_count == 0
    assert res_empty.unevaluable_count == 1

    # Dual-doc task where secondary PDF is scanned/empty MUST fail closed (never fall back to primary!)
    scanned_sec_records = [
        {
            "document": "primary",
            "page": 1,
            "raw_text": (
                "Sovereign Gold Bond 47(viic) capital gains redemption and original subscriber "
                "continuously until maturity all mentioned in primary."
            ),
        },
        {"document": "secondary", "page": 1, "raw_text": "  "},
    ]
    res_scanned_sec = run_engine(
        "P8",
        scanned_sec_records,
        context={
            "sovereign_lies": _sample_sgb_spec(secondary_role="walkback_document", record_selector=None),
            "retrieval_url": "https://www.indiabudget.gov.in/p.pdf",
            "secondary_url": "https://www.indiabudget.gov.in/s.pdf",
        },
    )
    assert res_scanned_sec.leads == []
    assert res_scanned_sec.cleared_count == 0
    assert res_scanned_sec.unevaluable_count == 1

    # Missing walk-back tokens -> unevaluable (never fabricates lead from context!)
    res_missing = run_engine(
        "P8",
        [
            {
                "page": 1,
                "raw_text": (
                    "Sovereign Gold Bond Scheme under Section 47(viic) grants capital gains exemption on redemption. "
                    "No subsequent Budget amendment is referenced anywhere in this document."
                ),
            }
        ],
        context={"sovereign_lies": spec},
    )
    assert res_missing.leads == []
    assert res_missing.cleared_count == 0
    assert res_missing.unevaluable_count == 1

    # Structured JSON record_selector matching vs missing
    json_records = [
        {
            "qno": "101",
            "subject": "Unrelated Question",
            "answer": "This row talks about ports and shipping only, nothing about EV subsidies.",
            "source_row_index": 1,
        },
        {
            "qno": "204",
            "subject": "FAME-II Electric Two-Wheeler Subsidy",
            "answer": (
                "Under FAME-II launch guidelines, demand incentive was 40 percent cap at INR 15,000 per kWh. "
                "Effective 1 June 2023 MHI reduced subsidy cap to 15 percent and INR 10,000 per kWh."
            ),
            "source_row_index": 2,
        },
    ]
    sel_spec = _sample_sgb_spec(
        case_key="p8-union-fame2-cut-0045",
        scheme_name="FAME India Phase II",
        scheme_slug="fame-india-phase-ii",
        tranche_or_phase="Electric Two-Wheeler Demand Incentive",
        scheme_category="ev_housing_farm_subsidies",
        walkback_mechanism="midstream_benefit_cut",
        retroactive_impact="mid_stream_active_scheme",
        promise_date="2019-03-08",
        walkback_date="2023-05-19",
        promise_evidence_tokens=["40 percent", "15,000 per kWh"],
        walkback_evidence_tokens=["15 percent", "10,000 per kWh"],
        is_sgb_anchor=False,
        is_anchor_case=False,
        secondary_role="",
        record_selector={"match_field": "qno", "match_value": "204"},
    )
    res_hit = run_engine("P8", json_records, context={"sovereign_lies": sel_spec})
    assert len(res_hit.leads) == 1
    assert res_hit.leads[0].rule_triggered == "P8-MIDSTREAM-BENEFIT-CUT"
    assert "row=2" in res_hit.leads[0].source_locator

    # Selector pointing to non-existent row -> fail closed unevaluable
    miss_spec = copy.deepcopy(sel_spec)
    miss_spec["record_selector"] = {"match_field": "qno", "match_value": "9999"}
    res_miss = run_engine("P8", json_records, context={"sovereign_lies": miss_spec})
    assert res_miss.leads == []
    assert res_miss.unevaluable_count == 1

    # Corroborating secondary document with zero token overlap -> fail closed unevaluable
    corrob_spec = _sample_sgb_spec(
        secondary_role="corroborating_record",
        record_selector=None,
    )
    corrob_records = [
        {
            "document": "primary",
            "page": 1,
            "raw_text": (
                "Sovereign Gold Bond Section 47(viic) capital gains redemption and Finance Bill 2026 "
                "original subscriber continuously until maturity restriction."
            ),
        },
        {
            "document": "secondary",
            "page": 1,
            "raw_text": "Completely unrelated state highway toll tariff notification from 2018 with zero scheme overlap.",
        },
    ]
    res_uncorrob = run_engine("P8", corrob_records, context={"sovereign_lies": corrob_spec})
    assert res_uncorrob.leads == []
    assert res_uncorrob.unevaluable_count == 1

    # Loader fail-closed SHA-256 mismatch on primary, secondary, and same-URL secondary
    p_file = tmp_path / "doc1.txt"
    s_file = tmp_path / "doc2.txt"
    p_file.write_text("Sovereign Gold Bond 47(viic) capital gains redemption launch terms.", encoding="utf-8")
    s_file.write_text("Finance Bill 2026 original subscriber continuously until maturity.", encoding="utf-8")
    p_sha = compute_sha256(p_file.read_bytes())

    with pytest.raises(DigestMismatchError, match="Primary"):
        load_dataset(str(p_file), allow_local_files=True, expected_sha256="0" * 64)

    with pytest.raises(DigestMismatchError, match="Primary|Secondary"):
        load_dataset(
            str(p_file),
            context={"secondary_path": str(s_file), "sovereign_lies": _sample_sgb_spec()},
            allow_local_files=True,
            expected_sha256=p_sha,
            secondary_expected_sha256="f" * 64,
        )

    with pytest.raises(DigestMismatchError, match="Secondary"):
        load_dataset(
            str(p_file),
            context={"secondary_path": str(p_file), "sovereign_lies": _sample_sgb_spec()},
            allow_local_files=True,
            expected_sha256=p_sha,
            secondary_expected_sha256="e" * 64,
        )


def test_p8_loader_html_visible_text_and_promise_document_role(tmp_path: Path) -> None:
    # Primary = walk-back HTML press release (with <script>/<style> noise), Secondary = launch promise TXT
    html_file = tmp_path / "walkback_press_release.html"
    html_file.write_text(
        "<html><head><style>.nav{color:red}</style><script>var secret='ignore';</script></head>"
        "<body><h1>Ministry of Finance Press Release</h1>"
        "<p>Finance Bill 2026 limits Sovereign Gold Bond capital gains exemption exclusively to the "
        "original subscriber holding continuously until maturity.</p></body></html>",
        encoding="utf-8",
    )
    txt_file = tmp_path / "launch_promise.txt"
    txt_file.write_text(
        "Reserve Bank of India Notification: Sovereign Gold Bond under Section 47(viic) provides "
        "capital gains tax exemption upon redemption at maturity for individual investors.",
        encoding="utf-8",
    )
    html_sha = compute_sha256(html_file.read_bytes())
    txt_sha = compute_sha256(txt_file.read_bytes())

    spec = _sample_sgb_spec(
        source_format="html",
        secondary_source_format="txt",
        secondary_role="promise_document",
        record_selector=None,
    )
    ds = load_dataset(
        str(html_file),
        context={"secondary_path": str(txt_file), "sovereign_lies": spec},
        allow_local_files=True,
        expected_sha256=html_sha,
        secondary_expected_sha256=txt_sha,
    )
    assert ds.format == "html"
    assert "ignore" not in ds.raw_text
    assert "original subscriber" in ds.raw_text

    res = run_engine(
        "P8",
        ds.records,
        context={
            "sovereign_lies": spec,
            "retrieval_url": "https://www.pib.gov.in/walkback.html",
            "secondary_url": "https://www.rbi.org.in/launch.txt",
            "expected_sha256": html_sha,
            "secondary_expected_sha256": txt_sha,
        },
    )
    assert len(res.leads) == 1
    cits = {c["role"]: c for c in res.leads[0].citations}
    # Because secondary_role == "promise_document", promise citation maps to secondary URL/SHA!
    assert cits["original_promise"]["source_url"] == "https://www.rbi.org.in/launch.txt"
    assert cits["original_promise"]["sha256"] == txt_sha
    assert cits["subsequent_walkback"]["source_url"] == "https://www.pib.gov.in/walkback.html"
    assert cits["subsequent_walkback"]["sha256"] == html_sha


def test_p8_corroborating_record_provenance_and_mismatched_grandfathered_fail_closed() -> None:
    # Fix 1: In corroborating_record mode, citation URL/SHA/page must derive from the actual matched doc_role!
    # Here, primary (page 7) contains walk-back tokens and secondary (page 3) contains promise tokens.
    corrob_spec = _sample_sgb_spec(
        secondary_role="corroborating_record",
        record_selector=None,
    )
    records = [
        {
            "document": "primary",
            "page": 7,
            "raw_text": (
                "Union Budget 2026-27 Memorandum: Finance Bill 2026 limits Sovereign Gold Bond capital gains "
                "exemption strictly to the original subscriber holding continuously until maturity."
            ),
        },
        {
            "document": "secondary",
            "page": 3,
            "raw_text": (
                "Finance Act 2016 Section 47(viic): transfer by way of redemption of Sovereign Gold Bond "
                "by an individual enjoys full capital gains tax exemption at maturity."
            ),
            "secondary_sha256": "b" * 64,
        },
    ]
    res = run_engine(
        "P8",
        records,
        context={
            "sovereign_lies": corrob_spec,
            "retrieval_url": "https://www.indiabudget.gov.in/primary_walkback.pdf",
            "secondary_url": "https://www.rbi.org.in/secondary_promise.pdf",
            "expected_sha256": "a" * 64,
            "secondary_expected_sha256": "b" * 64,
        },
    )
    assert len(res.leads) == 1
    cits = {c["role"]: c for c in res.leads[0].citations}
    assert cits["original_promise"]["source_url"] == "https://www.rbi.org.in/secondary_promise.pdf"
    assert cits["original_promise"]["sha256"] == "b" * 64
    assert cits["original_promise"]["page"] == 3
    assert cits["subsequent_walkback"]["source_url"] == "https://www.indiabudget.gov.in/primary_walkback.pdf"
    assert cits["subsequent_walkback"]["sha256"] == "a" * 64
    assert cits["subsequent_walkback"]["page"] == 7

    # Also ensure secondary citation never inherits primary SHA when secondary SHA is absent
    records_no_sec_sha = [
        records[0],
        {k: v for k, v in records[1].items() if k != "secondary_sha256"},
    ]
    res_no_sec_sha = run_engine(
        "P8",
        records_no_sec_sha,
        context={
            "sovereign_lies": corrob_spec,
            "retrieval_url": "https://www.indiabudget.gov.in/primary_walkback.pdf",
            "secondary_url": "https://www.rbi.org.in/secondary_promise.pdf",
            "expected_sha256": "a" * 64,
        },
    )
    assert len(res_no_sec_sha.leads) == 1
    cits_no_sec = {c["role"]: c for c in res_no_sec_sha.leads[0].citations}
    assert cits_no_sec["original_promise"]["sha256"] is None
    assert cits_no_sec["subsequent_walkback"]["sha256"] == "a" * 64

    # Fix 3: Direct engine invocation with mismatched grandfathered_or_honored pair MUST fail closed (unevaluable, not Cleared!)
    single_doc = [
        {
            "page": 1,
            "raw_text": (
                "Sovereign Gold Bond 47(viic) capital gains redemption and Finance Bill 2026 "
                "original subscriber continuously until maturity."
            ),
        }
    ]
    for mech, impact in (
        ("retroactive_tax_reversal", "grandfathered_or_honored"),
        ("grandfathered_or_honored", "retroactive_existing_holders"),
    ):
        mismatch_spec = _sample_sgb_spec(
            walkback_mechanism=mech,
            retroactive_impact=impact,
            secondary_role="",
            record_selector=None,
        )
        res_mm = run_engine("P8", single_doc, context={"sovereign_lies": mismatch_spec})
        assert res_mm.leads == []
        assert res_mm.cleared_count == 0
        assert res_mm.unevaluable_count == 1
        assert "grandfathered_or_honored" in res_mm.summary


# ---------------------------------------------------------------------------
# 4. BNS Sec 356 Neutrality & DPDP Act 2023 PII Scrubbing
# ---------------------------------------------------------------------------


def test_p8_neutralizes_quoted_forbidden_terms_and_scrubs_pii() -> None:
    aadhaar = _make_valid_aadhaar("48291037462")
    spec = _sample_sgb_spec(
        case_key="p8-state-kl-pension-0022",
        scheme_name="Kerala Social Security Welfare Pension",
        scheme_slug="kerala-welfare-pension",
        tranche_or_phase="Monthly Welfare Pension Disbursal",
        jurisdiction="State:Kerala",
        jurisdiction_level="state",
        state_or_ut="Kerala",
        authority="Finance Department, Government of Kerala",
        scheme_category="pensions_retirement",
        walkback_mechanism="arrears_or_unfunded_promise",
        retroactive_impact="unfulfilled_arrears",
        promise_date="2021-04-01",
        walkback_date="2024-03-31",
        promise_evidence_tokens=["INR 1,600 monthly", "welfare pension"],
        walkback_evidence_tokens=["five months arrears", "pending disbursal"],
        is_sgb_anchor=False,
        is_anchor_case=False,
        secondary_role="",
        record_selector=None,
    )
    # Inject both BNS Sec 356 forbidden terms AND Indian PII into source document text!
    noisy_records = [
        {
            "page": 1,
            "raw_text": (
                f"Official audit remark referencing Anti-Corruption Bureau and alleged fraud/scam checks: "
                f"INR 1,600 monthly welfare pension notified in 2021 showed five months arrears and pending disbursal. "
                f"Beneficiary sample Aadhaar {aadhaar}, PAN ABCDE1234F, IFSC SBIN0001234, "
                f"Account No 30981234567890, mobile +91 9876543210, email citizen.test@gmail.com."
            ),
        }
    ]
    res = run_engine("P8", noisy_records, context={"sovereign_lies": spec, "as_of_date": "2026-11-28"})
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
    assert aadhaar not in dumped
    assert "ABCDE1234F" not in dumped
    assert "SBIN0001234" not in dumped
    assert "9876543210" not in dumped
    assert "citizen.test@gmail.com" not in dumped
    assert not contains_pii(lead.model_dump())[0]

    # Direct runtime check: even if matched evidence tokens contain a BNS-restricted word,
    # emitted anomaly_metrics tokens are neutralized so lead outputs pass lint_neutrality.
    tok_spec = _sample_sgb_spec(
        promise_evidence_tokens=["47(viic)", "Anti-Corruption check"],
        walkback_evidence_tokens=["original subscriber", "fraud control unit"],
        secondary_role="",
        record_selector=None,
    )
    tok_res = run_engine(
        "P8",
        [
            {
                "page": 1,
                "raw_text": (
                    "Section 47(viic) Anti-Corruption check on Sovereign Gold Bond capital gains and "
                    "Finance Bill 2026 original subscriber review by fraud control unit."
                ),
            }
        ],
        context={"sovereign_lies": tok_spec},
    )
    assert len(tok_res.leads) == 1
    am = tok_res.leads[0].anomaly_metrics
    tok_lint = lint_neutrality(
        tok_res.leads[0].title,
        tok_res.leads[0].summary,
        tok_res.leads[0].benign_explanations,
        extra_fields={
            "promise_tokens_matched": " ".join(am["promise_tokens_matched"]),
            "walkback_tokens_matched": " ".join(am["walkback_tokens_matched"]),
        },
    )
    assert tok_lint.passed, tok_lint.violations


# ---------------------------------------------------------------------------
# 5. Strict Project Validation (`validate_project`) & RTI Drafting
# ---------------------------------------------------------------------------


def test_p8_project_validation_rules_and_rejections() -> None:
    good_task = _sample_p8_task("p8-union-sgb-anchor-0001", _sample_sgb_spec())
    assert validate_project(_sample_p8_project([good_task]), filename_stem=SOVEREIGN_LIES_PROJECT_SLUG) == []

    # 1. Wrong financial_quantity_type
    bad_qty = _sample_p8_project([_sample_p8_task("p8-union-sgb-anchor-0001", _sample_sgb_spec(), qty_type="parliamentary_assurances")])
    assert any("scheme_promises_and_revisions" in e for e in validate_project(bad_qty, filename_stem=SOVEREIGN_LIES_PROJECT_SLUG))

    # 2. Invalid task ID format
    bad_id = _sample_p8_project([_sample_p8_task("bad-task-1", _sample_sgb_spec())])
    assert any("p8-(union|state|ut)" in e for e in validate_project(bad_id, filename_stem=SOVEREIGN_LIES_PROJECT_SLUG))

    # 3. Mismatched jurisdiction_level vs jurisdiction & missing state_or_ut
    bad_jur_spec = _sample_sgb_spec(jurisdiction_level="state", jurisdiction="Union of India", state_or_ut=None)
    bad_jur = _sample_p8_project([_sample_p8_task("p8-state-mh-test-0002", bad_jur_spec)])
    jur_errs = validate_project(bad_jur, filename_stem=SOVEREIGN_LIES_PROJECT_SLUG)
    assert any("State:" in e for e in jur_errs)
    assert any("state_or_ut" in e for e in jur_errs)

    # 4. Mismatched grandfathered_or_honored pair
    bad_gf_spec = _sample_sgb_spec(walkback_mechanism="grandfathered_or_honored", retroactive_impact="prospective_discontinuation")
    bad_gf = _sample_p8_project([_sample_p8_task("p8-union-ppf-0003", bad_gf_spec)])
    assert any("grandfathered_or_honored" in e for e in validate_project(bad_gf, filename_stem=SOVEREIGN_LIES_PROJECT_SLUG))

    # 5. walkback_date before promise_date
    bad_dt_spec = _sample_sgb_spec(promise_date="2024-05-01", walkback_date="2023-01-01")
    bad_dt = _sample_p8_project([_sample_p8_task("p8-union-dt-0004", bad_dt_spec)])
    assert any("must not precede promise_date" in e for e in validate_project(bad_dt, filename_stem=SOVEREIGN_LIES_PROJECT_SLUG))

    # 6. Insufficient evidence tokens (<2)
    bad_tok_spec = _sample_sgb_spec(promise_evidence_tokens=["Only One Token"])
    bad_tok = _sample_p8_project([_sample_p8_task("p8-union-tok-0005", bad_tok_spec)])
    assert any("promise_evidence_tokens" in e for e in validate_project(bad_tok, filename_stem=SOVEREIGN_LIES_PROJECT_SLUG))

    # 7. Forbidden BNS Sec 356 term inside context summary
    bad_bns_spec = _sample_sgb_spec(walkback_summary="This walkback was a blatant fraud on bondholders.")
    bad_bns = _sample_p8_project([_sample_p8_task("p8-union-bns-0006", bad_bns_spec)])
    assert any("non-neutral" in e for e in validate_project(bad_bns, filename_stem=SOVEREIGN_LIES_PROJECT_SLUG))

    # 9. Task ID tier mismatch vs jurisdiction_level rejected
    bad_tier_spec = _sample_sgb_spec(jurisdiction_level="state", jurisdiction="State:Maharashtra", state_or_ut="Maharashtra")
    bad_tier = _sample_p8_project([_sample_p8_task("p8-union-mismatch-0008", bad_tier_spec)])
    assert any("does not match" in e for e in validate_project(bad_tier, filename_stem=SOVEREIGN_LIES_PROJECT_SLUG))

    # 8. Unpinned GitHub branch URL rejected
    bad_url = _sample_p8_project(
        [
            _sample_p8_task(
                "p8-union-gh-0007",
                _sample_sgb_spec(),
                retrieval_url="https://raw.githubusercontent.com/org/repo/main/file.pdf",
            )
        ]
    )
    assert any("40-character commit SHA" in e for e in validate_project(bad_url, filename_stem=SOVEREIGN_LIES_PROJECT_SLUG))

    # 10. Union task with non-null state_or_ut rejected
    bad_union_state = _sample_p8_project(
        [_sample_p8_task("p8-union-st-0009", _sample_sgb_spec(jurisdiction_level="union", state_or_ut="Maharashtra"))]
    )
    assert any("state_or_ut must be null" in e for e in validate_project(bad_union_state, filename_stem=SOVEREIGN_LIES_PROJECT_SLUG))

    # 11. Bare "State:" / "UT:" and mismatched State:<X> vs state_or_ut=<Y> rejected
    bare_state = _sample_p8_project(
        [
            _sample_p8_task(
                "p8-state-bare-0010",
                _sample_sgb_spec(jurisdiction_level="state", jurisdiction="State:", state_or_ut="Maharashtra"),
            )
        ]
    )
    assert any("State:<Name>" in e for e in validate_project(bare_state, filename_stem=SOVEREIGN_LIES_PROJECT_SLUG))

    bare_ut = _sample_p8_project(
        [
            _sample_p8_task(
                "p8-ut-bare-0011",
                _sample_sgb_spec(jurisdiction_level="ut", jurisdiction="UT:", state_or_ut="Delhi"),
            )
        ]
    )
    assert any("UT:<Name>" in e for e in validate_project(bare_ut, filename_stem=SOVEREIGN_LIES_PROJECT_SLUG))

    mismatch_state = _sample_p8_project(
        [
            _sample_p8_task(
                "p8-state-mm-0012",
                _sample_sgb_spec(jurisdiction_level="state", jurisdiction="State:Karnataka", state_or_ut="Maharashtra"),
            )
        ]
    )
    assert any("State:Maharashtra" in e for e in validate_project(mismatch_state, filename_stem=SOVEREIGN_LIES_PROJECT_SLUG))

    # 12. Non-neutral BNS terms rejected inside evidence tokens and record_selector.section_hint
    bad_ptok = _sample_p8_project(
        [_sample_p8_task("p8-union-ptok-0013", _sample_sgb_spec(promise_evidence_tokens=["47(viic)", "blatant scam"]))]
    )
    assert any("promise_evidence_tokens" in e and "non-neutral" in e for e in validate_project(bad_ptok, filename_stem=SOVEREIGN_LIES_PROJECT_SLUG))

    bad_wtok = _sample_p8_project(
        [_sample_p8_task("p8-union-wtok-0014", _sample_sgb_spec(walkback_evidence_tokens=["original subscriber", "corrupt clause"]))]
    )
    assert any("walkback_evidence_tokens" in e and "non-neutral" in e for e in validate_project(bad_wtok, filename_stem=SOVEREIGN_LIES_PROJECT_SLUG))

    bad_hint = _sample_p8_project(
        [_sample_p8_task("p8-union-hint-0015", _sample_sgb_spec(record_selector={"section_hint": "Rigged clause 35"}))]
    )
    assert any("section_hint" in e and "non-neutral" in e for e in validate_project(bad_hint, filename_stem=SOVEREIGN_LIES_PROJECT_SLUG))

    # 13. Non-empty secondary_role requires payload.secondary_url
    missing_sec_url = _sample_p8_project(
        [
            _sample_p8_task(
                "p8-union-sec-0016",
                _sample_sgb_spec(secondary_role="walkback_document"),
                secondary_url=None,
            )
        ]
    )
    assert any("secondary_url is required" in e for e in validate_project(missing_sec_url, filename_stem=SOVEREIGN_LIES_PROJECT_SLUG))


def test_p8_rti_draft_union_state_and_delhi_ut() -> None:
    union_finding = {
        "methodology_code": "P8",
        "entity_name": "Department of Economic Affairs, Ministry of Finance",
        "suggested_authority": "Department of Economic Affairs, Ministry of Finance, Government of India",
        "jurisdiction": "Union of India",
        "fiscal_year": "FY2025-26",
        "monetary_exposure_inr": 0.0,
        "anomaly_metrics": {
            "case_key": "p8-union-sgb-anchor-0001",
            "promise_document_ref": "Finance Act 2016 Sec. 47(viic)",
            "walkback_document_ref": "Finance Bill 2026 Clause 35",
        },
    }
    union_rti = generate_rti_draft(union_finding)
    assert union_rti["jurisdiction_level"] == "central"
    assert union_rti["rti_portal_url"] == "https://rtionline.gov.in"
    assert union_rti["word_count"] <= 500
    assert "Finance Act 2016 Sec. 47(viic)" in union_rti["body_text"]
    assert "Gazette notification" in union_rti["body_text"]

    state_finding = {
        "methodology_code": "P8",
        "entity_name": "Finance Department, Government of Karnataka",
        "suggested_authority": "Finance Department, Government of Karnataka",
        "jurisdiction": "State:Karnataka",
        "fiscal_year": "FY2023-24",
        "monetary_exposure_inr": 500_000_000.0,
        "anomaly_metrics": {
            "case_key": "p8-state-ka-rvn-0050",
            "promise_document_ref": "GO ED-2021-RVN",
        },
    }
    state_rti = generate_rti_draft(state_finding)
    assert state_rti["jurisdiction_level"] == "state"
    assert state_rti["rti_portal_url"] == "https://rtionline.karnataka.gov.in"
    assert state_rti["central_portal_warning"] is not None
    assert state_rti["word_count"] <= 500

    ut_delhi_finding = {
        "methodology_code": "P8",
        "entity_name": "Transport Department, GNCTD",
        "suggested_authority": "Transport Department, Government of NCT of Delhi",
        "jurisdiction": "UT:Delhi",
        "fiscal_year": "FY2024-25",
        "monetary_exposure_inr": 0.0,
        "anomaly_metrics": {"case_key": "p8-ut-dl-ev-0801"},
    }
    delhi_rti = generate_rti_draft(ut_delhi_finding)
    assert delhi_rti["rti_portal_url"] == "https://rtionline.delhi.gov.in"
    assert delhi_rti["word_count"] <= 500


# ---------------------------------------------------------------------------
# 6. Hub API Integration: /api/projects/sovereign-lies/summary, visual_summary, & .md
# ---------------------------------------------------------------------------


def test_p8_hub_summary_endpoint_live_aggregation_and_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj_dir = tmp_path / "projects"
    proj_dir.mkdir()

    sgb_spec = _sample_sgb_spec()
    state_spec = _sample_sgb_spec(
        case_key="p8-state-ka-rvn-0002",
        scheme_name="Raitha Vidya Nidhi Scholarship",
        scheme_slug="raitha-vidya-nidhi",
        tranche_or_phase="Farmers' Children Higher Education Scholarship",
        jurisdiction="State:Karnataka",
        jurisdiction_level="state",
        state_or_ut="Karnataka",
        authority="Department of Agriculture, Government of Karnataka",
        scheme_category="scholarships_education",
        promise_date="2021-08-07",
        walkback_date="2023-07-07",
        walkback_mechanism="premature_discontinuation",
        retroactive_impact="prospective_discontinuation",
        promise_evidence_tokens=["Raitha Vidya Nidhi", "scholarship"],
        walkback_evidence_tokens=["discontinued", "Karnataka Budget 2023"],
        estimated_exposure_inr=4_500_000_000.0,
        is_sgb_anchor=False,
        is_anchor_case=False,
        secondary_role="",
        record_selector=None,
    )
    cleared_spec = _sample_sgb_spec(
        case_key="p8-ut-dl-lifeline-0003",
        scheme_name="Delhi Lifeline Water Honored Control",
        scheme_slug="delhi-lifeline-water",
        tranche_or_phase="20,000 Litre Monthly Domestic Exemption",
        jurisdiction="UT:Delhi",
        jurisdiction_level="ut",
        state_or_ut="Delhi",
        authority="Delhi Jal Board, GNCTD",
        scheme_category="welfare_health_public_services",
        promise_date="2015-03-01",
        walkback_date=None,
        walkback_mechanism="grandfathered_or_honored",
        retroactive_impact="grandfathered_or_honored",
        promise_evidence_tokens=["20,000 litres", "free water"],
        walkback_evidence_tokens=["continued", "domestic households"],
        is_sgb_anchor=False,
        is_anchor_case=False,
        secondary_role="",
        record_selector=None,
    )

    project_data = _sample_p8_project(
        [
            _sample_p8_task("p8-union-sgb-anchor-0001", sgb_spec),
            _sample_p8_task("p8-state-ka-rvn-0002", state_spec, secondary_url=None),
            _sample_p8_task("p8-ut-dl-lifeline-0003", cleared_spec, secondary_url=None),
        ]
    )
    assert validate_project(project_data, filename_stem=SOVEREIGN_LIES_PROJECT_SLUG) == []
    (proj_dir / "sovereign-lies.json").write_text(json.dumps(project_data), encoding="utf-8")

    # 1. Test fallback aggregation WITHOUT coverage.json first!
    monkeypatch.setenv("AIWATCHDOGS_PROJECTS_DIR", str(proj_dir))
    monkeypatch.delenv("AIWATCHDOGS_KEEP_LEGACY_MISSIONS", raising=False)

    app_fallback = create_app(db_path=tmp_path / "p8_fallback.db", seed=True)
    with TestClient(app_fallback) as client:
        r_sum = client.get("/api/projects/sovereign-lies/summary")
        assert r_sum.status_code == 200, r_sum.text
        fb_sum = r_sum.json()
        assert fb_sum["hook"] == P8_DEFAULT_HOOK
        assert fb_sum["total_tasks"] == 3
        assert fb_sum["unique_schemes_covered"] == 3
        assert fb_sum["states_and_uts_covered"] == 2
        assert fb_sum["sgb_tasks_count"] == 1
        assert fb_sum["cleared_control_tasks_embedded"] == 1
        assert fb_sum["by_jurisdiction_level"]["union"]["tasks"] == 1
        assert fb_sum["by_jurisdiction_level"]["state"]["tasks"] == 1
        assert fb_sum["by_jurisdiction_level"]["ut"]["tasks"] == 1
        assert fb_sum["sgb_anchor_case"]["task_id"] == "p8-union-sgb-anchor-0001"

        # Identical payload inside GET /api/missions/sovereign-lies -> visual_summary.sovereign_lies
        r_mission = client.get("/api/missions/sovereign-lies")
        assert r_mission.status_code == 200
        assert r_mission.json()["visual_summary"]["sovereign_lies"] == fb_sum

    # 2. Now add coverage.json + documents.csv and test live finding + cleared submissions!
    p8_assets = proj_dir / "sovereign-lies"
    p8_assets.mkdir()
    coverage_obj = {
        "hook": P8_DEFAULT_HOOK,
        "generated_on": "2026-11-28",
        "as_of_date": "2026-11-28",
        "total_tasks": 3,
        "unique_schemes_covered": 3,
        "states_and_uts_covered": 2,
        "sgb_tasks_count": 1,
        "cleared_control_tasks_embedded": 1,
        "by_jurisdiction_level": {
            "union": {"tasks": 1, "cleared": 5},
            "state": {"tasks": 1, "cleared": 9},
            "ut": {"tasks": 1, "cleared": 4},
        },
        "by_mechanism": {
            "retroactive_tax_reversal": {"tasks": 1},
            "premature_discontinuation": {"tasks": 1},
            "grandfathered_or_honored": {"tasks": 1},
        },
        "by_retroactive_impact": {
            "retroactive_existing_holders": 1,
            "prospective_discontinuation": 1,
            "grandfathered_or_honored": 1,
        },
        "by_category": [
            {"category": "sovereign_bonds_small_savings", "tasks": 1},
            {"category": "scholarships_education", "tasks": 1},
            {"category": "welfare_health_public_services", "tasks": 1},
        ],
        "jurisdictions": [
            {"jurisdiction": "Union of India", "jurisdiction_level": "union", "state_or_ut": None, "tasks": 1, "cleared": 6},
            {"jurisdiction": "State:Karnataka", "jurisdiction_level": "state", "state_or_ut": "Karnataka", "tasks": 1, "cleared": 7},
            {"jurisdiction": "UT:Delhi", "jurisdiction_level": "ut", "state_or_ut": "Delhi", "tasks": 1, "cleared": 8},
        ],
        "top_schemes": [
            {
                "scheme_name": "Sovereign Gold Bond (SGB) Scheme",
                "scheme_slug": "sovereign-gold-bond",
                "jurisdiction": "Union of India",
                "jurisdiction_level": "union",
                "scheme_category": "sovereign_bonds_small_savings",
                "walkback_mechanism": "retroactive_tax_reversal",
                "retroactive_impact": "retroactive_existing_holders",
                "tasks": 1,
                "is_sgb_anchor": True,
            }
        ],
        "sgb_anchor_case": {
            "task_id": "p8-union-sgb-anchor-0001",
            "scheme_name": "Sovereign Gold Bond (SGB) Scheme",
            "tranche_or_phase": sgb_spec["tranche_or_phase"],
            "jurisdiction": "Union of India",
            "authority": sgb_spec["authority"],
            "promise_date": sgb_spec["promise_date"],
            "promise_summary": sgb_spec["promise_summary"],
            "promise_document_ref": sgb_spec["promise_document_ref"],
            "walkback_date": sgb_spec["walkback_date"],
            "walkback_summary": sgb_spec["walkback_summary"],
            "walkback_document_ref": sgb_spec["walkback_document_ref"],
            "walkback_mechanism": "retroactive_tax_reversal",
            "retroactive_impact": "retroactive_existing_holders",
            "promised_metric": sgb_spec["promised_metric"],
            "revised_metric": sgb_spec["revised_metric"],
            "affected_group": sgb_spec["affected_group"],
            "days_from_promise_to_walkback": 3747,
            "source_url": "https://www.indiabudget.gov.in/budget2016-17/ub2016-17/memo/mem1.pdf",
            "secondary_url": "https://www.indiabudget.gov.in/doc/memo.pdf",
            "portal_url": "https://www.indiabudget.gov.in/",
        },
    }
    (p8_assets / "coverage.json").write_text(json.dumps(coverage_obj), encoding="utf-8")
    (p8_assets / "documents.csv").write_text("url,sha256,publisher\nhttps://www.indiabudget.gov.in/doc/memo.pdf," + "b" * 64 + ",Ministry of Finance\n", encoding="utf-8")

    app = create_app(db_path=tmp_path / "p8_live.db", seed=True)
    with TestClient(app) as client:
        # Artifact file routes
        assert client.get("/api/missions/sovereign-lies/coverage.json").status_code == 200
        assert client.get("/api/missions/sovereign-lies/documents.csv").status_code == 200
        assert client.get("/api/missions/sovereign-lies/secret.txt").status_code == 404

        # Register agent and submit SGB anchor finding + Cleared control
        reg = client.post(
            "/api/agents/register",
            json={
                "handle": "p8-auditor-01",
                "display_name": "P8 Auditor",
                "provider": "rule-engine",
                "model": "deterministic-python-v1",
                "coding_agent": "Claude Code",
                "coding_agent_model": "claude-sonnet-4",
                "mode": "auto",
            },
        ).json()
        headers = {"X-Agent-Token": reg["api_token"]}

        # Claim & submit Task #1 (SGB anchor)
        lease1 = client.post(
            "/api/desk/next",
            headers=headers,
            json={"task_id": "prj-p8-union-sgb-anchor-0001"},
        ).json()
        task1_ctx = _build_task_context(lease1["task"]["payload"])
        assert "sovereign_lies" in task1_ctx

        lead_res = run_engine(
            "P8",
            [
                {
                    "document": "primary",
                    "page": 1,
                    "raw_text": "Sovereign Gold Bond 47(viic) grants capital gains exemption on redemption for individuals.",
                },
                {
                    "document": "secondary",
                    "page": 2,
                    "raw_text": "Finance Bill 2026 restricts Sovereign Gold Bond exemption to original subscriber held continuously until maturity.",
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
        sgb_finding_id = sub1.json()["finding_id"]

        # Claim & submit Task #3 (Cleared control)
        lease3 = client.post(
            "/api/desk/next",
            headers=headers,
            json={"task_id": "prj-p8-ut-dl-lifeline-0003"},
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

        # Verify /api/projects/sovereign-lies/summary reflects live progress + findings!
        sum_resp = client.get("/api/projects/sovereign-lies/summary")
        assert sum_resp.status_code == 200
        summary = sum_resp.json()
        assert summary["leads"] == 1
        assert summary["peer_review"] == 1
        assert summary["cleared_tasks"] == 1
        assert summary["by_signal"]["retroactive_tax_reversal"] == 1
        assert summary["by_mechanism"]["retroactive_tax_reversal"]["leads"] == 1
        assert summary["by_jurisdiction_level"]["union"]["done"] == 1
        assert summary["by_jurisdiction_level"]["union"]["leads"] == 1
        assert summary["by_jurisdiction_level"]["union"]["cleared"] == 0
        assert summary["by_jurisdiction_level"]["state"]["done"] == 0
        assert summary["by_jurisdiction_level"]["state"]["cleared"] == 0
        assert summary["by_jurisdiction_level"]["ut"]["done"] == 1
        assert summary["by_jurisdiction_level"]["ut"]["cleared"] == 1

        # Fix 2: live `done` and `cleared` populated on by_category & jurisdictions even with coverage.json present,
        # and stale coverage.json `cleared` numbers are never leaked!
        cats_by_key = {c["category"]: c for c in summary["by_category"]}
        assert cats_by_key["sovereign_bonds_small_savings"]["done"] == 1
        assert cats_by_key["sovereign_bonds_small_savings"]["leads"] == 1
        assert cats_by_key["scholarships_education"]["done"] == 0
        assert cats_by_key["welfare_health_public_services"]["done"] == 1

        jurs_by_name = {j["jurisdiction"]: j for j in summary["jurisdictions"]}
        assert jurs_by_name["Union of India"]["done"] == 1
        assert jurs_by_name["Union of India"]["leads"] == 1
        assert jurs_by_name["Union of India"]["cleared"] == 0
        assert jurs_by_name["State:Karnataka"]["done"] == 0
        assert jurs_by_name["State:Karnataka"]["cleared"] == 0
        assert jurs_by_name["UT:Delhi"]["done"] == 1
        assert jurs_by_name["UT:Delhi"]["cleared"] == 1

        assert summary["sgb_anchor_case"]["finding_id"] == sgb_finding_id
        assert len(summary["top_leads"]) == 1
        assert summary["top_leads"][0]["finding_id"] == sgb_finding_id
        assert summary["top_leads"][0]["is_sgb_anchor"] is True

        # Verify Markdown & Discovery routes render P8 cleanly
        md_resp = client.get("/projects/sovereign-lies.md")
        assert md_resp.status_code == 200
        assert "## Sovereign Lies (P8)" in md_resp.text
        assert "Sovereign Gold Bond (SGB) Anchor Case" in md_resp.text
        assert "/api/projects/sovereign-lies/summary" in md_resp.text

        brief_resp = client.get("/brief.md")
        assert brief_resp.status_code == 200
        assert "sovereign-lies.md" in brief_resp.text

        join_resp = client.get("/join.txt")
        assert join_resp.status_code == 200
        assert "sovereign-lies" in join_resp.text
