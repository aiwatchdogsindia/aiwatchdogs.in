"""Comprehensive unit and integration tests for Project P7 (only-one-bid) and engine P7."""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from aiwatchdogs.connectors.loader import DigestMismatchError, load_dataset
from aiwatchdogs.core.rti import generate_rti_draft
from aiwatchdogs.core.safety import compute_sha256, lint_neutrality
from aiwatchdogs.engines.only_one_bid import (
    ONLY_ONE_BID_METHODOLOGY,
    ONLY_ONE_BID_PROJECT_SLUG,
    P7_DEFAULT_HOOK,
    normalize_entity_name,
    run_p7_only_one_bid,
    validate_context,
)
from aiwatchdogs.engines.registry import METHODOLOGY_CATALOG, run_engine
from aiwatchdogs.hub.app import create_app
from aiwatchdogs.projects import CANONICAL_PROJECT_CODES, validate_project

SHA = "a" * 64
COMMIT = "0123456789abcdef0123456789abcdef01234567"
PINNED_URL = f"https://raw.githubusercontent.com/CivicDataLab/assam-tenders-data/{COMMIT}/data/tenders.csv"
REPO_PROJECTS = Path(__file__).resolve().parents[1] / "projects"


def _p7_ctx(
    *,
    case_key: str = "p7-bo-2021-00001",
    audit_mode: str = "both",
    match_field: str = "tender_id",
    match_value: str = "T-001",
    authority: str = "Public Works Roads Department, Assam",
    authority_key: str = "assam-pwrd",
    jurisdiction: str = "State:Assam",
    jurisdiction_level: str = "state",
    year: int = 2021,
    fiscal_year: str = "FY2021-22",
    field_map: dict[str, str] | None = None,
    is_anchor_case: bool = False,
) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "case_key": case_key,
        "audit_mode": audit_mode,
        "portal": "assamtenders.gov.in",
        "portal_key": "assam-ocds",
        "jurisdiction": jurisdiction,
        "jurisdiction_level": jurisdiction_level,
        "authority": authority,
        "authority_key": authority_key,
        "fiscal_year": fiscal_year,
        "year": year,
        "record_selector": {
            "match_field": match_field,
            "match_value": match_value,
            "tender_id": match_value,
            "record_url": f"https://assamtenders.gov.in/nicgep/app?tender={match_value}",
        },
        "cohort_filter": {
            "buyer_field": "buyer_name",
            "buyer_value": authority,
            "winner_field": "vendor_name",
        },
        "is_anchor_case": is_anchor_case,
    }
    if field_map:
        spec["field_map"] = field_map
    return {
        "as_of_date": "2026-11-27",
        "retrieval_url": PINNED_URL,
        "expected_sha256": SHA,
        "official_publisher": authority,
        "source_landing_page": "https://assamtenders.gov.in",
        "only_one_bid": spec,
    }


def _row(
    tid: str,
    *,
    buyer: str = "Public Works Roads Department, Assam",
    winner: str = "M/s Brahmaputra Infra Pvt. Ltd.",
    bid_count: Any = 1,
    qualified_bid_count: Any = None,
    method: str = "open",
    method_details: str = "",
    exemption_reason: str = "",
    award_value_inr: Any = 15_000_000.0,
    tender_estimate_inr: Any = None,
    publish_date: str = "2021-06-01",
    close_date: str = "2021-06-18",
    award_date: str = "2021-07-05",
    category: str = "Civil Works - Roads",
    title: str = "Widening and resurfacing of rural arterial road",
    fiscal_year: str = "FY2021-22",
    jurisdiction: str = "State:Assam",
    row_index: int = 1,
) -> dict[str, Any]:
    rec: dict[str, Any] = {
        "tender_id": tid,
        "ocid": f"ocds-9t57fa-{tid}",
        "tender_title": title,
        "buyer_name": buyer,
        "vendor_name": winner,
        "bid_count": bid_count,
        "procurement_method": method,
        "procurement_method_details": method_details,
        "exemption_reason": exemption_reason,
        "tender_published_date": publish_date,
        "tender_end_date": close_date,
        "award_date": award_date,
        "item_category": category,
        "fiscal_year": fiscal_year,
        "jurisdiction": jurisdiction,
        "source_row_index": row_index,
    }
    if qualified_bid_count is not None:
        rec["qualified_bid_count"] = qualified_bid_count
    if award_value_inr is not None:
        rec["award_value_inr"] = award_value_inr
    if tender_estimate_inr is not None:
        rec["tender_estimate_inr"] = tender_estimate_inr
    return rec


# ---------------------------------------------------------------------------
# 1. Normalization & Bid-Count Hygiene
# ---------------------------------------------------------------------------


def test_normalize_entity_name_merges_legal_suffixes_and_prefixes() -> None:
    a = normalize_entity_name("M/s Brahmaputra Infra Pvt. Ltd.")
    b = normalize_entity_name("BRAHMAPUTRA INFRA PRIVATE LIMITED")
    c = normalize_entity_name("Messrs. Brahmaputra Infra Ltd.")
    d = normalize_entity_name("brahmaputra infra pvt ltd")
    assert a == b == c == d == "brahmaputra infra"
    assert normalize_entity_name("A & B Constructions LLP") == "a and b constructions"
    assert normalize_entity_name("--") == ""
    assert normalize_entity_name("Unspecified Vendor") == ""


def test_bid_count_hygiene_separates_blank_zero_one_and_multi() -> None:
    # Target with literal 0 bids and blank winner -> unevaluable (never treated as single bid!)
    res_zero = run_engine(
        "P7",
        [_row("T-ZERO", bid_count=0, winner="")],
        context=_p7_ctx(match_value="T-ZERO"),
    )
    assert res_zero.leads == []
    assert res_zero.cleared_count == 0
    assert res_zero.unevaluable_count == 1

    # Target with blank bid count and blank winner -> unevaluable
    res_blank = run_engine(
        "P7",
        [_row("T-BLANK", bid_count="", winner="")],
        context=_p7_ctx(match_value="T-BLANK"),
    )
    assert res_blank.leads == []
    assert res_blank.cleared_count == 0
    assert res_blank.unevaluable_count == 1

    # Missing target record -> unevaluable
    res_missing = run_engine(
        "P7",
        [_row("T-OTHER", bid_count=1)],
        context=_p7_ctx(match_value="T-NOT-THERE"),
    )
    assert res_missing.leads == []
    assert res_missing.unevaluable_count == 1


# ---------------------------------------------------------------------------
# 2. All Verdicts: Cleared (Exempt, Competitive, De Minimis) & 4 Signal Categories
# ---------------------------------------------------------------------------


def test_cleared_exempt_pac_oem_and_emergency_and_dominance_override() -> None:
    # 1. PAC / OEM exemption under GFR Rule 166 -> cleared_exempt
    pac_rows = [
        _row(
            "T-PAC-1",
            buyer="AIIMS New Delhi",
            winner="Siemens Healthineers India Pvt Ltd",
            bid_count=1,
            method="direct",
            exemption_reason="Proprietary Article Certificate (PAC) under GFR 2017 Rule 166 for OEM MRI spares",
            award_value_inr=25_000_000.0,
            jurisdiction="Union of India",
        )
    ]
    leads, cleared, audited, summary, meta = run_p7_only_one_bid(
        pac_rows,
        _p7_ctx(
            case_key="p7-sb-2022-00001",
            audit_mode="single_bidder",
            match_value="T-PAC-1",
            authority="AIIMS New Delhi",
            authority_key="aiims-new-delhi",
            jurisdiction="Union of India",
            jurisdiction_level="central",
            year=2022,
        ),
    )
    assert leads == []
    assert cleared == 1
    assert audited == 25_000_000.0
    assert meta["verdict"] == "cleared_exempt"
    assert meta["exemption_status"] == "documented_pac_oem"
    assert "cleared_exempt" in summary

    # 2. Emergency / disaster relief direct award with explicit justification -> cleared_exempt
    emerg_rows = [
        _row(
            "T-EMERG-1",
            bid_count=1,
            method="direct",
            exemption_reason="Flood disaster relief emergency restoration approved by competent authority",
            award_value_inr=12_000_000.0,
        )
    ]
    leads_e, cleared_e, _, _, meta_e = run_p7_only_one_bid(
        emerg_rows,
        _p7_ctx(case_key="p7-sb-2022-00002", audit_mode="single_bidder", match_value="T-EMERG-1"),
    )
    assert leads_e == []
    assert cleared_e == 1
    assert meta_e["verdict"] == "cleared_exempt"
    assert meta_e["exemption_status"] == "documented_emergency_or_direct"

    # 3. Override: if same winner holds >= 5 "direct" awards totaling >= INR 50L -> NOT auto-cleared!
    dom_rows = [
        _row(
            f"T-DOM-{i}",
            bid_count=1,
            method="direct",
            exemption_reason="Urgent nomination award",
            winner="M/s Repeat Nominee Corp",
            award_value_inr=2_000_000.0,
            award_date=f"2021-0{i}-10",
            row_index=i,
        )
        for i in range(1, 6)
    ]
    leads_d, cleared_d, _, _, meta_d = run_p7_only_one_bid(
        dom_rows,
        _p7_ctx(case_key="p7-bo-2021-00009", match_value="T-DOM-5"),
    )
    assert cleared_d == 0
    assert len(leads_d) == 1
    assert leads_d[0].rule_triggered == "P7-COMBINED-SB-RW"
    assert "P7-REPEATED-DIRECT-OR-EXEMPT-DOMINANCE" in leads_d[0].anomaly_metrics["triggered_rules"]
    assert meta_d["verdict"] == "flagged_combined"


def test_cleared_competitive_and_cleared_de_minimis() -> None:
    # Competitive multi-bid tender with non-concentrated winner -> cleared_competitive
    comp_rows = [
        _row("T-COMP-1", bid_count=5, winner="Alpha Builders", award_value_inr=20_000_000.0, row_index=1),
        _row("T-COMP-2", bid_count=4, winner="Beta Builders", award_value_inr=18_000_000.0, row_index=2),
    ]
    leads_c, cleared_c, _, _, meta_c = run_p7_only_one_bid(
        comp_rows,
        _p7_ctx(case_key="p7-sb-2023-00001", audit_mode="single_bidder", match_value="T-COMP-1"),
    )
    assert leads_c == []
    assert cleared_c == 1
    assert meta_c["verdict"] == "cleared_competitive"

    # Single-bidder tender below INR 5 lakh LPC ceiling with no repeat streak -> cleared_de_minimis
    dm_rows = [
        _row(
            "T-DM-1",
            bid_count=1,
            winner="Local Stationery Mart",
            award_value_inr=320_000.0,
            publish_date="2023-05-01",
            close_date="2023-05-16",
        )
    ]
    leads_dm, cleared_dm, _, _, meta_dm = run_p7_only_one_bid(
        dm_rows,
        _p7_ctx(case_key="p7-sb-2023-00002", audit_mode="single_bidder", match_value="T-DM-1"),
    )
    assert leads_dm == []
    assert cleared_dm == 1
    assert meta_dm["verdict"] == "cleared_de_minimis"


def test_signal_1_combined_single_bidder_repeat_winner_and_anti_double_counting() -> None:
    # 4 tenders won by same normalized entity (varying legal suffixes), 3 single-bid including tech-rejection case
    rows = [
        _row(
            "T-COMB-1",
            winner="M/s Apex Roads Pvt. Ltd.",
            bid_count=1,
            award_value_inr=20_000_000.0,
            publish_date="2019-06-01",
            close_date="2019-06-10",
            award_date="2019-07-01",
            fiscal_year="FY2019-20",
            row_index=1,
        ),
        _row(
            "T-COMB-2",
            winner="APEX ROADS PRIVATE LIMITED",
            bid_count=4,
            qualified_bid_count=1,  # 4 received, 3 rejected -> 1 qualified!
            award_value_inr=30_000_000.0,
            publish_date="2021-06-01",
            close_date="2021-06-10",  # 9-day short window
            award_date="2021-07-01",
            fiscal_year="FY2021-22",
            row_index=2,
        ),
        _row(
            "T-COMB-3",
            winner="Apex Roads Ltd.",
            bid_count=1,
            award_value_inr=15_000_000.0,
            publish_date="2022-06-01",
            close_date="2022-06-20",
            award_date="2022-07-01",
            fiscal_year="FY2022-23",
            row_index=3,
        ),
        _row("T-OTHER-4", winner="Other Infra LLP", bid_count=3, award_value_inr=10_000_000.0, row_index=4),
        _row("T-OTHER-5", winner="Third Works Co", bid_count=2, award_value_inr=10_000_000.0, row_index=5),
    ]

    result = run_engine(
        "P7",
        rows,
        context=_p7_ctx(case_key="p7-bo-2021-00001", match_value="T-COMB-2", is_anchor_case=True),
    )
    assert len(result.leads) == 1
    assert result.cleared_count == 0
    assert result.unevaluable_count == 0
    lead = result.leads[0]
    m = lead.anomaly_metrics

    assert lead.methodology_code == "P7"
    assert lead.rule_triggered == "P7-COMBINED-SB-RW"
    assert m["signal_category"] == "single_bidder_repeat_winner"
    assert m["verdict"] == "flagged_combined"
    assert m["single_bid_mechanism"] == "single_qualified_after_rejection"
    assert m["bids_received"] == 4
    assert m["technically_qualified_bids"] == 1
    assert m["effective_bids"] == 1
    assert m["bid_window_days"] == 9
    assert "P7-SINGLE-QUALIFIED-AFTER-TECH-REJECTION" in m["triggered_rules"]
    assert "P7-SHORT-BID-WINDOW" in m["triggered_rules"]
    assert "P7-HIGH-AUTHORITY-SB-RATE" in m["triggered_rules"]  # 3/5 = 60% > 35%
    assert m["normalized_winner_key"] == "apex roads"
    assert m["winner_total_wins"] == 3
    assert m["winner_single_bid_wins"] == 3
    assert lead.fiscal_year == "FY2021-22"
    assert lead.fiscal_year_start == "FY2019-20"
    assert lead.fiscal_year_end == "FY2022-23"

    # Anti-double-counting invariant: lead exposure = target tender ONLY (INR 3 Cr), cohort = INR 6.5 Cr!
    assert lead.monetary_exposure_inr == 30_000_000.0
    assert result.monetary_audited_inr == 30_000_000.0
    assert m["winner_cohort_total_inr"] == 65_000_000.0
    assert m["is_anchor_case"] is True
    assert len(lead.citations) == 3
    assert [c["role"] for c in lead.citations] == ["primary_tender", "repeat_winner_cohort", "statutory_benchmark"]
    assert len(lead.benign_explanations) >= 3
    assert lint_neutrality(lead.title, lead.summary, lead.benign_explanations).passed


def test_signal_2_unexplained_single_bidder() -> None:
    rows = [
        _row(
            "T-SB-1",
            winner="Solo Diagnostic Supplier",
            bid_count=1,
            method="open",
            award_value_inr=None,
            tender_estimate_inr=8_500_000.0,  # Estimate only — must preserve amount_type="tender_estimate"
            publish_date="2020-08-01",
            close_date="2020-08-10",  # 9-day short bid window
            award_date="",
            fiscal_year="FY2020-21",
        )
    ]
    result = run_engine(
        "P7",
        rows,
        context=_p7_ctx(case_key="p7-sb-2020-00010", audit_mode="single_bidder", match_value="T-SB-1", year=2020),
    )
    assert len(result.leads) == 1
    lead = result.leads[0]
    assert lead.rule_triggered == "P7-SINGLE-BID"
    assert lead.anomaly_metrics["signal_category"] == "unexplained_single_bidder"
    assert lead.anomaly_metrics["verdict"] == "flagged_single_bidder"
    assert lead.amount_type == "tender_estimate"
    assert lead.anomaly_metrics["award_value_inr"] is None
    assert lead.anomaly_metrics["tender_estimate_inr"] == 8_500_000.0
    assert lead.monetary_exposure_inr == 8_500_000.0
    assert lead.severity == "high"  # short window < 14d
    assert len(lead.citations) == 2  # single win -> primary_tender + statutory_benchmark


def test_signal_3_repeat_winner_concentration_multi_bid() -> None:
    # Multi-bid tenders (3 bids each), yet same contractor wins 4 consecutive tenders (4/6 = 66.7%)
    rows = [
        _row("T-RW-1", winner="Omega Civil Works Pvt Ltd", bid_count=3, award_value_inr=12_000_000.0, award_date="2018-05-01", fiscal_year="FY2018-19", row_index=1),
        _row("T-RW-2", winner="M/s Omega Civil Works", bid_count=3, award_value_inr=14_000_000.0, award_date="2019-05-01", fiscal_year="FY2019-20", row_index=2),
        _row("T-RW-3", winner="OMEGA CIVIL WORKS LIMITED", bid_count=4, award_value_inr=16_000_000.0, award_date="2020-05-01", fiscal_year="FY2020-21", row_index=3),
        _row("T-RW-4", winner="Omega Civil Works Ltd", bid_count=3, award_value_inr=18_000_000.0, award_date="2021-05-01", fiscal_year="FY2021-22", row_index=4),
        _row("T-RW-5", winner="Delta Infra", bid_count=4, award_value_inr=10_000_000.0, award_date="2017-05-01", fiscal_year="FY2017-18", row_index=5),
        _row("T-RW-6", winner="Gamma Infra", bid_count=5, award_value_inr=10_000_000.0, award_date="2022-05-01", fiscal_year="FY2022-23", row_index=6),
    ]
    result = run_engine(
        "P7",
        rows,
        context=_p7_ctx(case_key="p7-rw-2021-00004", audit_mode="repeat_winner", match_value="T-RW-4"),
    )
    assert len(result.leads) == 1
    lead = result.leads[0]
    m = lead.anomaly_metrics
    assert lead.rule_triggered == "P7-REPEAT-WINNER"
    assert m["signal_category"] == "repeat_winner_concentration"
    assert m["verdict"] == "flagged_repeat_winner"
    assert m["winner_total_wins"] == 4
    assert m["winner_single_bid_wins"] == 0
    assert m["consecutive_win_streak"] == 4
    assert m["winner_share_by_count_pct"] == pytest.approx(66.67, abs=0.05)
    assert m["winner_share_by_value_pct"] == pytest.approx(75.0, abs=0.05)
    assert lead.monetary_exposure_inr == 18_000_000.0
    assert m["winner_cohort_total_inr"] == 60_000_000.0
    assert lead.severity == "critical"  # >=50% value share & >= INR 5 Cr cohort


def test_signal_4_short_window_or_process_gap() -> None:
    # 2 bids on INR 6 Cr tender with 5-day bid window -> P7-PROCESS-GAP
    rows = [
        _row(
            "T-GAP-1",
            winner="Express Logistics Pvt Ltd",
            bid_count=2,
            award_value_inr=60_000_000.0,
            publish_date="2024-02-01",
            close_date="2024-02-06",  # 5 days < 7
            award_date="2024-02-20",
            fiscal_year="FY2023-24",
        )
    ]
    result = run_engine(
        "P7",
        rows,
        context=_p7_ctx(case_key="p7-sb-2023-00099", audit_mode="single_bidder", match_value="T-GAP-1", year=2023),
    )
    assert len(result.leads) == 1
    lead = result.leads[0]
    assert lead.rule_triggered == "P7-PROCESS-GAP"
    assert lead.anomaly_metrics["signal_category"] == "short_window_or_process_gap"
    assert lead.anomaly_metrics["verdict"] == "flagged_process_gap"
    assert lead.severity == "high"


# ---------------------------------------------------------------------------
# 3. BNS Sec 356 Neutrality, DPDP PII Scrubbing & Jurisdiction-Aware RTI
# ---------------------------------------------------------------------------


def test_external_text_neutralization_pii_scrubbing_and_jurisdiction_rti() -> None:
    dirty_rows = [
        _row(
            "T-DIRTY-1",
            buyer="Department of Rural Roads, Assam",
            title="Anti-corruption vigilance block & alleged scam/fraud repair contact +91 9876543210 PAN ABCDE1234F",
            winner="M/s Rigged Infra Pvt Ltd (personal.owner@gmail.com)",
            bid_count=1,
            award_value_inr=12_000_000.0,
        )
    ]
    result = run_engine(
        "P7",
        dirty_rows,
        context=_p7_ctx(
            case_key="p7-sb-2024-00001",
            audit_mode="single_bidder",
            match_value="T-DIRTY-1",
            authority="Department of Rural Roads, Assam",
            authority_key="assam-rural-roads",
            jurisdiction="State:Assam",
            jurisdiction_level="state",
            year=2024,
        ),
    )
    assert len(result.leads) == 1
    lead = result.leads[0]
    assert lint_neutrality(lead.title, lead.summary, lead.benign_explanations).passed
    assert "9876543210" not in lead.summary
    assert "ABCDE1234F" not in lead.summary
    assert "personal.owner@gmail.com" not in lead.summary
    assert "vigilance" in lead.anomaly_metrics["tender_title"].lower()
    assert "State Public Procurement" in lead.anomaly_metrics["governing_instrument"]

    # State RTI must NOT route to Central rtionline.gov.in!
    state_rti = generate_rti_draft(lead)
    assert state_rti["jurisdiction_level"] == "state"
    assert state_rti["rti_portal_url"] != "https://rtionline.gov.in"
    assert state_rti["central_portal_warning"] is not None
    assert "T-DIRTY-1" in state_rti["body_text"]
    assert "Proprietary Article Certificate" in state_rti["body_text"]
    assert state_rti["word_count"] <= 500

    # Central authority RTI routes cleanly to rtionline.gov.in
    central_res = run_engine(
        "P7",
        [
            _row(
                "T-CEN-1",
                buyer="Ministry of Railways, Government of India",
                jurisdiction="Union of India",
                bid_count=1,
                award_value_inr=45_000_000.0,
            )
        ],
        context=_p7_ctx(
            case_key="p7-sb-2024-00002",
            audit_mode="single_bidder",
            match_value="T-CEN-1",
            authority="Ministry of Railways, Government of India",
            authority_key="ministry-of-railways",
            jurisdiction="Union of India",
            jurisdiction_level="central",
            year=2024,
        ),
    )
    central_lead = central_res.leads[0]
    assert "Central GFR 2017" in central_lead.anomaly_metrics["governing_instrument"]
    central_rti = generate_rti_draft(central_lead)
    assert central_rti["jurisdiction_level"] == "central"
    assert central_rti["rti_portal_url"] == "https://rtionline.gov.in"
    assert central_rti["central_portal_warning"] is None
    assert "T-CEN-1" in central_rti["body_text"]
    assert central_rti["word_count"] <= 500


# ---------------------------------------------------------------------------
# 4. Loader Multi-Format Support (CSV, .csv.gz, JSON, JSONL, OCDS, Secondary Shard)
# ---------------------------------------------------------------------------


def test_loader_parses_gzip_csv_jsonl_ocds_record_package_and_secondary_shard(tmp_path: Path) -> None:
    # A. Gzipped CSV with custom columns mapped via field_map
    csv_text = (
        "ref_code,dept_name,contractor,bids_in,award_inr,pub_dt,end_dt,awd_dt,work_title\n"
        "GZIP-101,Water Resources Assam,M/s River Works Pvt Ltd,1,9500000,2022-04-01,2022-04-10,2022-05-01,Embankment repair\n"
        "GZIP-102,Water Resources Assam,RIVER WORKS PRIVATE LIMITED,1,8500000,2022-06-01,2022-06-18,2022-07-01,Sluice gate works\n"
    )
    gz_csv = tmp_path / "assam_2022.csv.gz"
    gz_csv.write_bytes(gzip.compress(csv_text.encode("utf-8")))
    gz_sha = compute_sha256(gz_csv.read_bytes())

    ds_gz = load_dataset(str(gz_csv), allow_local_files=True, expected_sha256=gz_sha)
    assert ds_gz.format == "csv"
    assert ds_gz.digest_verified is True
    assert len(ds_gz.records) == 2

    res_gz = run_engine(
        "P7",
        ds_gz.records,
        context=_p7_ctx(
            case_key="p7-bo-2022-00001",
            match_field="ref_code",
            match_value="GZIP-101",
            authority="Water Resources Assam",
            authority_key="water-resources-assam",
            year=2022,
            field_map={
                "tender_id": "ref_code",
                "buyer": "dept_name",
                "winner": "contractor",
                "bid_count": "bids_in",
                "award_value_inr": "award_inr",
                "publish_date": "pub_dt",
                "close_date": "end_dt",
                "award_date": "awd_dt",
                "title": "work_title",
            },
        ),
    )
    assert len(res_gz.leads) == 1
    assert res_gz.leads[0].rule_triggered == "P7-COMBINED-SB-RW"
    assert res_gz.leads[0].anomaly_metrics["winner_single_bid_wins"] == 2
    assert res_gz.leads[0].monetary_exposure_inr == 9_500_000.0
    assert res_gz.leads[0].anomaly_metrics["winner_cohort_total_inr"] == 18_000_000.0

    # B. Gzipped OCDS JSONL (.jsonl.gz)
    ocds_lines = [
        json.dumps(
            {
                "ocid": "ocds-in-2023-01",
                "buyer": {"name": "HP Health Dept"},
                "tender": {
                    "id": "HP-2023-01",
                    "title": "ICU Monitors",
                    "procurementMethod": "open",
                    "numberOfTenderers": 1,
                    "tenderPeriod": {"startDate": "2023-01-01", "endDate": "2023-01-12"},
                },
                "awards": [
                    {
                        "id": "AWD-1",
                        "date": "2023-02-01",
                        "value": {"amount": 7500000, "currency": "INR"},
                        "suppliers": [{"name": "MedTech Solutions Pvt Ltd"}],
                    }
                ],
            }
        ),
        json.dumps(
            {
                "ocid": "ocds-in-2023-02",
                "compiledRelease": {
                    "ocid": "ocds-in-2023-02",
                    "buyer": {"name": "HP Health Dept"},
                    "tender": {
                        "id": "HP-2023-02",
                        "title": "Ventilator Units",
                        "procurementMethod": "open",
                        "numberOfTenderers": 1,
                        "tenderPeriod": {"startDate": "2023-03-01", "endDate": "2023-03-16"},
                    },
                    "awards": [
                        {
                            "id": "AWD-2",
                            "date": "2023-04-01",
                            "value": {"amount": 8200000, "currency": "INR"},
                            "suppliers": [{"name": "MEDTECH SOLUTIONS PRIVATE LIMITED"}],
                        }
                    ],
                },
            }
        ),
    ]
    gz_jsonl = tmp_path / "hp_ocds.jsonl.gz"
    gz_jsonl.write_bytes(gzip.compress(("\n".join(ocds_lines) + "\n").encode("utf-8")))
    ds_jsonl = load_dataset(str(gz_jsonl), allow_local_files=True)
    assert ds_jsonl.format == "ocds_jsonl"
    assert len(ds_jsonl.records) == 2
    assert ds_jsonl.records[1]["vendor_name"] == "MEDTECH SOLUTIONS PRIVATE LIMITED"

    # C. OCDS Record Package JSON (records[].compiledRelease) + Secondary Shard Join for P7
    primary_json = tmp_path / "primary_records.json"
    primary_json.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "ocid": "ocds-in-2024-01",
                        "compiledRelease": {
                            "ocid": "ocds-in-2024-01",
                            "buyer": {"name": "NHAI Regional Office"},
                            "tender": {"id": "NHAI-2024-01", "title": "Toll Plaza Upgrade", "procurementMethod": "open", "numberOfTenderers": 1},
                            "awards": [{"id": "A1", "date": "2024-04-10", "value": {"amount": 40000000}, "suppliers": [{"name": "Highway Systems Pvt Ltd"}]}],
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    secondary_jsonl = tmp_path / "history_cohort.jsonl"
    secondary_jsonl.write_text(
        json.dumps(
            {
                "tender_id": "NHAI-2022-99",
                "buyer_name": "NHAI Regional Office",
                "vendor_name": "HIGHWAY SYSTEMS PRIVATE LIMITED",
                "bid_count": 1,
                "award_value_inr": 35000000,
                "award_date": "2022-08-12",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    p_sha = compute_sha256(primary_json.read_bytes())
    s_sha = compute_sha256(secondary_jsonl.read_bytes())
    ctx_join = _p7_ctx(
        case_key="p7-bo-2024-00001",
        match_value="NHAI-2024-01",
        authority="NHAI Regional Office",
        authority_key="nhai-ro",
        jurisdiction="Union of India",
        jurisdiction_level="central",
        year=2024,
    )
    ctx_join["secondary_path"] = str(secondary_jsonl)
    ctx_join["secondary_expected_sha256"] = s_sha

    ds_joined = load_dataset(
        str(primary_json),
        context=ctx_join,
        allow_local_files=True,
        expected_sha256=p_sha,
        secondary_expected_sha256=s_sha,
    )
    assert ds_joined.digest_verified is True
    assert ds_joined.secondary_digest_verified is True
    assert len(ds_joined.records) == 2

    res_joined = run_engine("P7", ds_joined.records, context=ctx_join)
    assert len(res_joined.leads) == 1
    assert res_joined.leads[0].rule_triggered == "P7-COMBINED-SB-RW"
    assert res_joined.leads[0].anomaly_metrics["winner_single_bid_wins"] == 2
    assert res_joined.leads[0].monetary_exposure_inr == 40_000_000.0
    assert res_joined.leads[0].anomaly_metrics["winner_cohort_total_inr"] == 75_000_000.0

    # D. Fail-closed on bad secondary SHA-256 & gzip bomb protection
    with pytest.raises(DigestMismatchError):
        load_dataset(
            str(primary_json),
            context=ctx_join,
            allow_local_files=True,
            expected_sha256=p_sha,
            secondary_expected_sha256="b" * 64,
        )

    bomb_gz = tmp_path / "bomb.csv.gz"
    bomb_gz.write_bytes(gzip.compress(b"a,b\n" + (b"0,0\n" * 600_000)))
    with pytest.raises(ValueError, match="compression ratio|decompressed size"):
        load_dataset(
            str(bomb_gz),
            context={"max_decompressed_bytes": 1_500_000, "max_compression_ratio": 20.0},
            allow_local_files=True,
        )


# ---------------------------------------------------------------------------
# 5. Project Validation (`projects.py` & `validate_context`)
# ---------------------------------------------------------------------------


def _sample_p7_project(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "slug": ONLY_ONE_BID_PROJECT_SLUG,
        "project_code": "P7",
        "title": "Only One Bid: Which purchases consistently attract just one bidder? (2016–2026)",
        "summary": (
            "Audits Indian public procurement tenders and contract awards across 2016–2026 for "
            "unexplained single-bidder tenders and repeat-winner concentration patterns while "
            "transparently clearing documented PAC/OEM and emergency exemptions."
        ),
        "posted_by": {"name": "AI Watchdogs India", "handle": "@aiwatchdogs", "url": "https://aiwatchdogs.in"},
        "pillar": "procurement",
        "status": "active",
        "methodology_code": ONLY_ONE_BID_METHODOLOGY,
        "jurisdiction": "India (Union Ministries, PSUs, and State Public Procurement Portals)",
        "authority": "Central & State Procuring Authorities across India",
        "statutory_anchors": [
            "GFR-2017-Rule-166 (Single Tender Enquiry / PAC)",
            "GFR-2017-Rule-173(xx) (Competition and transparency in public procurement)",
            "CVC-Single-Bid-Guidelines",
        ],
        "scope": ["Single-bidder competitive tenders and repeat-winner concentration across 2016–2026"],
        "out_of_scope": ["Personal phone numbers, PAN, Aadhaar, or unverified accusations"],
        "sources": [
            {
                "name": "CivicDataLab Open Contracting India",
                "publisher": "Finance Department, Government of Assam (via CivicDataLab)",
                "url": "https://assamtenders.gov.in",
            }
        ],
        "tasks": tasks,
    }


def _sample_p7_task(
    task_id: str,
    *,
    authority: str = "Public Works Roads Department, Assam",
    year: int = 2021,
    audit_mode: str = "both",
    qty_type: str = "awarded_contract_values",
) -> dict[str, Any]:
    return {
        "id": task_id,
        "title": f"{authority} · FY{year}-{(year + 1) % 100:02d} · Single-bidder check ({task_id})",
        "instructions": "Fetch pinned procurement dataset, verify SHA-256, and run P7 check.",
        "type": "run_check",
        "methodology_code": ONLY_ONE_BID_METHODOLOGY,
        "dataset_key": task_id,
        "financial_quantity_type": qty_type,
        "estimated_tokens": 4000,
        "estimate_basis": "Deterministic P7 evaluation over pinned procurement dataset.",
        "payload": {
            "retrieval_url": PINNED_URL,
            "expected_sha256": SHA,
            "official_publisher": authority,
            "source_landing_page": "https://assamtenders.gov.in",
            "artifact_title": "Assam OCDS Procurement Snapshot",
            "fiscal_year": f"FY{year}-{(year + 1) % 100:02d}",
            "jurisdiction": "State:Assam",
            "context": _p7_ctx(
                case_key=task_id,
                audit_mode=audit_mode,
                match_value=f"T-{task_id}",
                authority=authority,
                year=year,
                fiscal_year=f"FY{year}-{(year + 1) % 100:02d}",
            ),
        },
    }


def test_project_and_context_validation_accepts_valid_and_rejects_defects() -> None:
    assert CANONICAL_PROJECT_CODES[ONLY_ONE_BID_PROJECT_SLUG] == "P7"
    assert "P7" in METHODOLOGY_CATALOG

    valid_proj = _sample_p7_project(
        [
            _sample_p7_task("p7-sb-2018-00001", year=2018, audit_mode="single_bidder", qty_type="tender_estimates"),
            _sample_p7_task("p7-rw-2021-00002", year=2021, audit_mode="repeat_winner"),
            _sample_p7_task("p7-bo-2024-00003", year=2024, audit_mode="both"),
        ]
    )
    assert validate_project(valid_proj, filename_stem=ONLY_ONE_BID_PROJECT_SLUG) == []

    # Invalid task ID pattern rejected
    bad_id = _sample_p7_project([_sample_p7_task("p7-badid-1")])
    errs_id = validate_project(bad_id, filename_stem=ONLY_ONE_BID_PROJECT_SLUG)
    assert any("p7-(sb|rw|bo)-YYYY-NNNNN" in e for e in errs_id), errs_id

    # Invalid quantity type rejected
    bad_qty = _sample_p7_project([_sample_p7_task("p7-sb-2021-00001", qty_type="parliamentary_assurances")])
    errs_qty = validate_project(bad_qty, filename_stem=ONLY_ONE_BID_PROJECT_SLUG)
    assert any("awarded_contract_values or tender_estimates" in e for e in errs_qty), errs_qty

    # Invalid context fields rejected
    bad_ctx = _p7_ctx(case_key="p7-sb-2021-00001", audit_mode="invalid_mode", jurisdiction_level="moon")
    bad_ctx["only_one_bid"]["authority"] = "Scam Authority"
    bad_ctx["only_one_bid"]["record_selector"]["record_url"] = "http://localhost/private"
    ctx_errs = validate_context(bad_ctx, "tasks[0]")
    assert any("audit_mode" in e for e in ctx_errs)
    assert any("jurisdiction_level" in e for e in ctx_errs)
    assert any("non-neutral" in e for e in ctx_errs)
    assert any("record_url invalid" in e for e in ctx_errs)


# ---------------------------------------------------------------------------
# 6. Hub Integration: Summary SQL (`SUBSTR`), Static Files, Markdown & /llms.txt
# ---------------------------------------------------------------------------


def test_hub_p7_summary_substr_endpoints_and_agent_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx
    from aiwatchdogs.cli.config import AgentConfig
    from aiwatchdogs.cli.runner import AgentRunner

    proj_dir = tmp_path / "projects"
    assets_dir = proj_dir / ONLY_ONE_BID_PROJECT_SLUG
    assets_dir.mkdir(parents=True)

    csv_bytes = (
        "tender_id,ocid,tender_title,buyer_name,vendor_name,bid_count,procurement_method,"
        "award_value_inr,tender_published_date,tender_end_date,award_date,item_category,fiscal_year,jurisdiction\n"
        "T-p7-bo-2024-00003,ocds-9t57fa-3,Arterial bridge works,Assam PWRD,Apex Roads Pvt Ltd,1,open,"
        "45000000,2024-06-01,2024-06-11,2024-07-05,Civil Works - Roads,FY2024-25,State:Assam\n"
        "T-PRIOR-1,ocds-9t57fa-2,Approach road widening,Assam PWRD,APEX ROADS PRIVATE LIMITED,1,open,"
        "25000000,2023-05-01,2023-05-18,2023-06-20,Civil Works - Roads,FY2023-24,State:Assam\n"
    ).encode("utf-8")
    csv_sha = compute_sha256(csv_bytes)

    t1 = _sample_p7_task("p7-sb-2018-00001", authority="Assam PWRD", year=2018, audit_mode="single_bidder")
    t2 = _sample_p7_task("p7-rw-2021-00002", authority="HP Health Dept", year=2021, audit_mode="repeat_winner")
    t3 = _sample_p7_task("p7-bo-2024-00003", authority="Assam PWRD", year=2024, audit_mode="both")
    for t in (t1, t2, t3):
        t["payload"]["expected_sha256"] = csv_sha
        t["payload"]["context"]["expected_sha256"] = csv_sha
    t3["payload"]["context"]["only_one_bid"]["is_anchor_case"] = True

    proj = _sample_p7_project([t1, t2, t3])
    (proj_dir / f"{ONLY_ONE_BID_PROJECT_SLUG}.json").write_text(json.dumps(proj), encoding="utf-8")

    coverage = {
        "hook": P7_DEFAULT_HOOK,
        "generated_on": "2026-11-27",
        "as_of_date": "2026-11-27",
        "total_tasks": 3,
        "year_range": ["2018", "2024"],
        "portals_covered": 2,
        "authorities_covered": 2,
        "distinct_repeat_winners": 1,
        "jurisdictions": [
            {
                "jurisdiction": "State:Assam",
                "portal": "assamtenders.gov.in",
                "tasks": 2,
                "single_bidder_tasks": 1,
                "repeat_winner_tasks": 0,
                "both_tasks": 1,
            }
        ],
        "top_authorities": [
            {
                "authority": "Assam PWRD",
                "authority_key": "assam-pwrd",
                "jurisdiction": "State:Assam",
                "tasks": 2,
                "single_bidder_tasks": 1,
                "repeat_winner_tasks": 0,
                "both_tasks": 1,
            }
        ],
        "top_repeat_winners": [
            {
                "winner_name": "Apex Roads Pvt Ltd",
                "normalized_winner_key": "apex roads",
                "authority": "Assam PWRD",
                "jurisdiction": "State:Assam",
                "win_count": 6,
                "single_bid_wins": 5,
                "total_value_inr_cr": 12.5,
                "tasks": 1,
            }
        ],
        "anchor_case": {
            "task_id": "p7-bo-2024-00003",
            "tender_id": "T-p7-bo-2024-00003",
            "authority": "Assam PWRD",
            "jurisdiction": "State:Assam",
            "fiscal_year": "FY2024-25",
            "tender_title": "Arterial bridge works",
            "winner_name": "Apex Roads Pvt Ltd",
            "bids_received": 1,
            "winner_total_wins": 6,
            "winner_single_bid_wins": 5,
            "award_value_inr": 45_000_000.0,
            "amount_type": "award_value",
            "signal_category": "single_bidder_repeat_winner",
            "record_url": "https://assamtenders.gov.in/tender/3",
        },
    }
    (assets_dir / "coverage.json").write_text(json.dumps(coverage), encoding="utf-8")
    (assets_dir / "documents.csv").write_text(
        "portal,url,sha256,records\nassamtenders.gov.in," + PINNED_URL + "," + csv_sha + ",100\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("AIWATCHDOGS_PROJECTS_DIR", str(proj_dir))
    monkeypatch.delenv("AIWATCHDOGS_KEEP_LEGACY_MISSIONS", raising=False)

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
        if str(url) == PINNED_URL:
            return _FakeResp(csv_bytes, "text/csv")
        return orig_stream(self, method, url, **kwargs)

    def _fake_get(self: Any, url: str, **kwargs: Any):
        if str(url) == PINNED_URL:
            return _FakeResp(csv_bytes, "text/csv")
        return orig_get(self, url, **kwargs)

    monkeypatch.setattr(httpx.Client, "stream", _fake_stream)
    monkeypatch.setattr(httpx.Client, "get", _fake_get)

    app = create_app(db_path=tmp_path / "p7_hub.db", seed=True)
    with TestClient(app, base_url="http://testserver") as client:
        # Full end-to-end Auditor + 3 pairwise-distinct independent Peer Verifiers via AgentRunner!
        auditor = AgentRunner(
            AgentConfig(
                handle="p7-auditor-one",
                hub_url="http://testserver",
                provider="rule-engine",
                model="deterministic-cpu-v1",
                coding_agent="Claude Code",
                coding_agent_model="claude-sonnet-4-6",
                mode="investigate",
                max_tasks=1,
            ),
            http_client=client,
            config_path=tmp_path / "auditor.json",
        )
        res_a = auditor.run_loop(
            max_tasks=1,
            preferred_mission=ONLY_ONE_BID_PROJECT_SLUG,
            task_id="prj-p7-bo-2024-00003",
        )
        assert res_a.findings_submitted == 1

        verifiers = [
            ("p7-verifier-1", "Codex", "gpt-5.4"),
            ("p7-verifier-2", "Antigravity", "gemini-3-pro"),
            ("p7-verifier-3", "Cursor", "composer-2"),
        ]
        for v_handle, v_agent, v_model in verifiers:
            pv = AgentRunner(
                AgentConfig(
                    handle=v_handle,
                    hub_url="http://testserver",
                    provider="rule-engine",
                    model="deterministic-cpu-v1",
                    coding_agent=v_agent,
                    coding_agent_model=v_model,
                    mode="verify",
                    max_tasks=1,
                ),
                http_client=client,
                config_path=tmp_path / f"{v_handle}.json",
            )
            res_pv = pv.run_loop(max_tasks=1, preferred_mission=ONLY_ONE_BID_PROJECT_SLUG)
            assert res_pv.verifications_submitted == 1

        # Check /api/missions/only-one-bid visual_summary.only_one_bid (verifies SUBSTR SQL & verified status!)
        detail_resp = client.get(f"/api/missions/{ONLY_ONE_BID_PROJECT_SLUG}")
        assert detail_resp.status_code == 200
        detail = detail_resp.json()
        assert detail["project_code"] == "P7"
        vs = detail["visual_summary"]
        assert vs["methodology_code"] == "P7"
        p7 = vs["only_one_bid"]
        assert p7["hook"] == P7_DEFAULT_HOOK
        assert p7["by_cohort"]["single_bidder"]["tasks"] == 1
        assert p7["by_cohort"]["single_bidder"]["open"] == 1
        assert p7["by_cohort"]["repeat_winner"]["tasks"] == 1
        assert p7["by_cohort"]["both"]["tasks"] == 1
        assert p7["by_cohort"]["both"]["done"] == 1
        assert p7["by_cohort"]["both"]["pct"] == 100.0
        years_by_yr = {y["year"]: y for y in p7["years"]}
        assert years_by_yr["2018"]["single_bidder_tasks"] == 1
        assert years_by_yr["2021"]["repeat_winner_tasks"] == 1
        assert years_by_yr["2024"]["both_tasks"] == 1
        assert years_by_yr["2024"]["done"] == 1
        assert years_by_yr["2024"]["leads"] == 1
        assert years_by_yr["2024"]["flagged_inr_cr"] == 4.5
        assert p7["leads"] == 1
        assert p7["verified"] == 1
        assert p7["flagged_total_inr_cr"] == 4.5
        assert p7["by_signal"]["single_bidder_repeat_winner"] == 1
        assert p7["anchor_case"]["finding_id"] is not None
        assert len(p7["top_leads"]) == 1
        assert p7["top_leads"][0]["winner_total_wins"] == 2
        assert p7["top_leads"][0]["status"] == "verified"
        assert p7["documents_url"] == "/api/missions/only-one-bid/documents.csv"
        assert p7["coverage_url"] == "/api/missions/only-one-bid/coverage.json"

        # Companion static endpoints
        assert client.get("/api/missions/only-one-bid/documents.csv").status_code == 200
        assert client.get("/api/missions/only-one-bid/coverage.json").status_code == 200
        assert client.get("/api/missions/only-one-bid/other.txt").status_code == 404

        # Markdown, /llms.txt, /join.txt, /brief.md
        md_resp = client.get("/projects/only-one-bid.md")
        assert md_resp.status_code == 200
        assert "## Only One Bid (P7)" in md_resp.text
        assert "Assam PWRD" in md_resp.text

        llms = client.get("/llms.txt")
        assert llms.status_code == 200
        assert "/projects/only-one-bid.md" in llms.text
        assert "P1, W1, UB1, OR1, P5, P6 and P7" in llms.text

        join_txt = client.get("/join.txt")
        assert join_txt.status_code == 200
        assert "only-one-bid" in join_txt.text
        assert "Only One Bid" in join_txt.text

        brief_md = client.get("/brief.md")
        assert brief_md.status_code == 200
        assert "only-one-bid.md" in brief_md.text


def test_real_generated_p7_dataset_when_present() -> None:
    """Validate the real generated P7 dataset (10,000 tasks + coverage + documents) when present in projects/."""
    proj_path = REPO_PROJECTS / f"{ONLY_ONE_BID_PROJECT_SLUG}.json"
    if not proj_path.is_file():
        pytest.skip("projects/only-one-bid.json not yet generated on this branch")
    data = json.loads(proj_path.read_text(encoding="utf-8"))
    assert data["project_code"] == "P7"
    assert data["methodology_code"] == "P7"
    assert validate_project(data, filename_stem=ONLY_ONE_BID_PROJECT_SLUG) == []
    cov_path = REPO_PROJECTS / ONLY_ONE_BID_PROJECT_SLUG / "coverage.json"
    assert cov_path.is_file()
    cov = json.loads(cov_path.read_text(encoding="utf-8"))
    assert cov.get("project_code") == "P7" or cov.get("total_tasks", 0) > 0
