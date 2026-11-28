"""Deterministic engine execution suite for `aiwatchdogs reproduce` and PR CI verification.

Unlike a file-only hash, this module actually executes every registered deterministic
audit engine (M1–M8, P1, UB1, OR1, P5–P10), the W1 website check engine, and the R1
reading verifier against pinned canonical inputs and hashes their normalized outputs
(leads, anomaly metrics, cleared/unevaluable counts, and rejection gates).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

from aiwatchdogs.core.models import EngineRunResult
from aiwatchdogs.engines import gov_pages
from aiwatchdogs.engines.reading_verifier import verify_budget_extraction
from aiwatchdogs.engines.registry import _ENGINE_DISPATCH, run_engine

REPRODUCE_AS_OF_DATE = "2026-11-28"
_SHA_A = "a" * 64
_SHA_B = "b" * 64


def _canonical_sha256(obj: Any) -> str:
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _normalize_value(val: Any) -> Any:
    if isinstance(val, float):
        return round(val, 4)
    if isinstance(val, dict):
        return {str(k): _normalize_value(v) for k, v in sorted(val.items(), key=lambda kv: str(kv[0]))}
    if isinstance(val, list):
        return [_normalize_value(v) for v in val]
    return val


def normalize_engine_result(res: EngineRunResult) -> dict[str, Any]:
    """Convert an EngineRunResult into a machine-independent canonical summary for hashing."""
    leads_norm = [
        {
            "entity_name": lead.entity_name,
            "rule_triggered": lead.rule_triggered,
            "severity": lead.severity,
            "fiscal_year": lead.fiscal_year,
            "jurisdiction": lead.jurisdiction,
            "monetary_exposure_inr": round(float(lead.monetary_exposure_inr), 2)
            if lead.monetary_exposure_inr is not None
            else None,
            "anomaly_metrics": _normalize_value(lead.anomaly_metrics),
        }
        for lead in res.leads
    ]
    return {
        "methodology_code": res.methodology_code,
        "ruleset_version": res.ruleset_version,
        "script_hash": res.script_hash,
        "records_scanned": res.records_scanned,
        "cleared_count": res.cleared_count,
        "unevaluable_count": res.unevaluable_count,
        "monetary_audited_inr": round(float(res.monetary_audited_inr), 2),
        "leads_count": len(leads_norm),
        "leads": leads_norm,
    }


# ---------------------------------------------------------------------------
# Canonical self-contained inputs for every registered deterministic engine
# ---------------------------------------------------------------------------


def _canonical_cases() -> dict[str, tuple[list[dict[str, Any]], dict[str, Any]]]:
    m1_m2_records = [
        {
            "ocid": f"ocds-rep-{i}",
            "tender_id": f"REP_T_{i}",
            "tender_title": f"Medical Supply {i}",
            "procurement_method": "Open",
            "item_category": "Medical Equipment",
            "tender_value_inr": 2_400_000.0,
            "award_value_inr": 2_400_000.0,
            "award_date": f"2024-02-{10 + i:02d}",
            "bid_window_days": 8 if i <= 3 else 25,
            "bid_count": 1 if i <= 3 else 3,
            "buyer_name": "Health and Family Welfare Assam",
            "vendor_name": "ALPHA MED PVT LTD" if i <= 3 else f"VENDOR {i}",
            "jurisdiction": "State:Assam",
            "fiscal_year": "FY 2023-24",
        }
        for i in range(1, 6)
    ]

    m3_m4_records = [
        {
            "ocid": f"ocds-m34-{i}",
            "tender_id": f"M34-{i}",
            "buyer_name": "Regional Public Works Division",
            "vendor_name": "ALPHA INFRA" if i <= 4 else "BETA BUILDERS",
            "item_category": "Civil Works",
            "tender_value_inr": 10_000_000.0,
            "award_amount_inr": 14_500_000.0 if i == 1 else 10_200_000.0,
            "award_value_inr": 14_500_000.0 if i == 1 else 10_200_000.0,
            "bid_count": 2,
            "jurisdiction": "State:Assam",
            "fiscal_year": "FY 2022-23",
        }
        for i in range(1, 6)
    ]

    m5_records = [
        {"District": "KRISHNA", "Year": "2016", "Month": m, "Major Head": "2202", "Major Head Description": "General Education", "Amount": 5_000_000}
        for m in ("APRIL", "MAY", "JUNE", "JULY", "AUG", "OCT", "DEC")
    ] + [
        {"District": "KRISHNA", "Year": "2017", "Month": "JAN", "Major Head": "2202", "Major Head Description": "General Education", "Amount": 10_000_000},
        {"District": "KRISHNA", "Year": "2017", "Month": "FEB", "Major Head": "2202", "Major Head Description": "General Education", "Amount": 15_000_000},
        {"District": "KRISHNA", "Year": "2017", "Month": "MAR", "Major Head": "2202", "Major Head Description": "General Education", "Amount": 40_000_000},
    ]

    m6_records = [
        {
            "page": 1,
            "raw_text": (
                "Comptroller and Auditor General of India - Report No. 12 of 2025\n"
                "Para 4.3 Unfruitful expenditure on regional cold storage project\n"
                "An expenditure of Rs. 42.75 crore remained unfruitful and ATN is unresolved "
                "despite repeat audit observations from the earlier audit cycle.\n"
                "Para 4.4 Minor procedural note on inventory register\n"
                "An amount of Rs. 1.20 crore was reconciled and settled by the department.\n"
            ),
        }
    ]

    m7_records = [
        {
            "STATE_NAME": "Bihar",
            "DISTRICT_NAME": "Araria",
            "PMGSY_SCHEME": "PMGSY-III",
            "LENGTH_OF_ROAD_WORK_SANCTIONED": "120.0",
            "COST_OF_WORKS_SANCTIONED": "100.0",
            "LENGTH_OF_ROAD_WORK_COMPLETED": "55.0",
            "EXPENDITURE_OCCURED": "94.0",
        },
        {
            "STATE_NAME": "Kerala",
            "DISTRICT_NAME": "Idukki",
            "PMGSY_SCHEME": "PMGSY-I",
            "LENGTH_OF_ROAD_WORK_SANCTIONED": "80.0",
            "COST_OF_WORKS_SANCTIONED": "50.0",
            "LENGTH_OF_ROAD_WORK_COMPLETED": "78.0",
            "EXPENDITURE_OCCURED": "48.0",
        },
    ]

    m8_records = [
        {
            "tender_id": "PROC-REP-99",
            "buyer_name": "Municipal Corp",
            "vendor_name": "Alpha Works Pvt Ltd",
            "cin": "U45200MH2018PTC111111",
            "incorporation_date": "2024-11-01",
            "award_date": "2025-01-15",
            "award_value_inr": 15_000_000,
            "director_dins": "08812345,08812346",
        },
        {
            "tender_id": "PROC-REP-99",
            "buyer_name": "Municipal Corp",
            "vendor_name": "Beta Works Pvt Ltd",
            "cin": "U45200MH2019PTC222222",
            "incorporation_date": "2019-01-01",
            "award_date": "2025-01-15",
            "award_value_inr": 0,
            "director_dins": "08812345,08899999",
        },
    ]

    p1_records = [
        {
            "page": 1,
            "raw_text": (
                "DEMAND NO. 51 POLICE\n"
                "5. Delhi Police 10000.00 500.00 10500.00 11000.00 600.00 11600.00 13000.00 700.00 13700.00 12000.00 800.00 12800.00\n"
            ),
        }
    ]
    p1_ctx = {
        "jurisdiction": "NCT of Delhi",
        "retrieval_url": "https://www.indiabudget.gov.in/budget2025-26/doc/eb/sbe51.pdf",
        "expected_sha256": _SHA_A,
        "budget_lines": [
            {
                "key": "delhi-police-total",
                "entity": "Delhi Police",
                "label": "Demand No. 51 line 5 'Delhi Police' (revenue + capital)",
                "unit": "crore",
                "parts": [
                    {
                        "page": 1,
                        "pattern": r"^\s*5\.\s*Delhi Police\b",
                        "values": {"actual": 2, "be": 5, "re": 8, "be_next": 11},
                    }
                ],
                "years": {
                    "actual": "FY2023-24",
                    "be": "FY2024-25",
                    "re": "FY2024-25",
                    "be_next": "FY2025-26",
                },
            }
        ],
        "comparisons": [
            {"a": "re", "b": "be", "label": "Revised vs budget estimate (same year)", "threshold_pct": 10}
        ],
    }

    ub1_est = "\n".join(
        [
            "Notes on Demands for Grants, 2020-2021  57",
            "No. 42/Department of Health and Family Welfare",
            "MINISTRY OF HEALTH AND FAMILY WELFARE",
            "DEMAND NO. 42",
            "Department of Health and Family Welfare",
            "(In ` crores)",
            "Actual 2018-2019 Budget 2019-2020 Revised 2019-2020 Budget 2020-2021",
            "Revenue Capital Total Revenue Capital Total Revenue Capital Total Revenue Capital Total",
            "A. The Budget allocations, net of recoveries, are given below:",
            "1. Secretariat 10.00 ... 10.00 12.00 ... 12.00 11.00 ... 11.00 13.00 ... 13.00",
            "2. National AIDS Control Programme 50.00 ... 50.00 100.00 ... 100.00 60.00 ... 60.00 120.00 ... 120.00",
            "Grand Total 60.00 ... 60.00 112.00 ... 112.00 71.00 ... 71.00 133.00 ... 133.00",
        ]
    )
    ub1_act = "\n".join(
        [
            "Notes on Demands for Grants, 2021-2022  61",
            "No. 44/Department of Health and Family Welfare",
            "MINISTRY OF HEALTH AND FAMILY WELFARE",
            "DEMAND NO. 44",
            "Department of Health and Family Welfare",
            "(In ` crores)",
            "Actual 2019-2020 Budget 2020-2021 Revised 2020-2021 Budget 2021-2022",
            "Revenue Capital Total Revenue Capital Total Revenue Capital Total Revenue Capital Total",
            "A. The Budget allocations, net of recoveries, are given below:",
            "1. Secretariat 11.50 ... 11.50 13.00 ... 13.00 12.00 ... 12.00 14.00 ... 14.00",
            "2. National AIDS Control Programme 55.00 ... 55.00 120.00 ... 120.00 110.00 ... 110.00 130.00 ... 130.00",
            "Grand Total 66.50 ... 66.50 133.00 ... 133.00 122.00 ... 122.00 144.00 ... 144.00",
        ]
    )
    ub1_records = [
        {"page": 1, "raw_text": ub1_est, "document": "primary"},
        {"page": 1, "raw_text": ub1_act, "document": "secondary"},
    ]
    ub1_ctx = {
        "retrieval_url": "https://www.indiabudget.gov.in/budget2020-21/doc/eb/sbe42.pdf",
        "expected_sha256": _SHA_A,
        "secondary_url": "https://www.indiabudget.gov.in/budget2021-22/doc/eb/sbe44.pdf",
        "secondary_expected_sha256": _SHA_B,
        "union_budget": {
            "financial_year": "2019-20",
            "estimates_document": {"budget": "2020-21", "demand_no": 42},
            "actuals_document": {"budget": "2021-22", "demand_no": 44},
            "lines": [
                {
                    "key": "1",
                    "estimates": [{"number": "1", "name": "Secretariat"}],
                    "actuals": [{"number": "1", "name": "Secretariat"}],
                    "link": "name+figure",
                },
                {
                    "key": "2",
                    "estimates": [{"number": "2", "name": "National AIDS Control Programme"}],
                    "actuals": [{"number": "2", "name": "National AIDS Control Programme"}],
                    "link": "name+figure",
                },
            ],
        },
    }

    or1_text = (
        "6.3.1.2 Duplicate claim for rural electrification connections\n"
        "Despite getting the required grant under DDUGJY, grant of Rs. 7.53 crore from REC "
        "was also claimed under SAUBHAGYA, resulting in double payment to DISCOMs for the same work."
    )
    or1_records = [{"page": 42, "raw_text": or1_text}]
    or1_ctx = {
        "retrieval_url": "https://d1jmc0u14z2tmp.cloudfront.net/uploads/download_audit_report/2025/test.pdf",
        "expected_sha256": _SHA_A,
        "source_landing_page": "https://cag.gov.in/en/audit-report/details/999999",
        "jurisdiction": "Union of India",
        "one_road_two_bills": {
            "report": {
                "cag_id": "999999",
                "report_no": "Report No. 17 of 2025",
                "year": "2025",
                "title": "Performance Audit Report on Rural Works, Union Government",
                "sector": "Social Infrastructure",
            },
            "windows": [
                {"key": "r999999-p042-b00", "page": 42, "block_index": 0, "line_start": 0, "line_end": 2}
            ],
            "thresholds": {"min_amount_inr": 0},
        },
    }

    p5_records = [
        {
            "page": 32,
            "raw_text": (
                "32\nTable 2.2: State wise highlights of the UDISE+ 2025-26 data: Schools, Enrolments and Teachers\n"
                "India 1466682 247219766 10273020 24 7 169 5663 20667 100843 2912147\n"
                "Jharkhand 44378 7505425 209586 36 5 169 125 43 9827 440346\n"
            ),
        },
        {
            "page": 42,
            "raw_text": (
                "42\nTable 3.3: Number of schools by management and school category, 2025-26: Government\n"
                "India 1005245 612066 204617 63260 16040 24942 12082 27683 26426 15465 2664\n"
                "Jharkhand 35788 21261 11426 17 1484 74 223 407 479 350 67\n"
            ),
        },
        {
            "page": 54,
            "raw_text": (
                "54\nTable 4.3: Number of teachers by management and school category, 2025-26: Government\n"
                "India 5134623 1754291 1300692 231309 197668 268252 72904 497095 569629 210581 32202\n"
                "Jharkhand 116372 35945 45088 100 14379 516 1427 7530 5216 5630 541\n"
            ),
        },
        {
            "page": 86,
            "raw_text": (
                "86\nTable 5.2: Enrolment of students by school management and level of school education, 2025-26: Government\n"
                "India 118903585 3537362 53724337 33347829 87072166 16991487 11302570\n"
                "Jharkhand 4811600 41435 2220821 1349141 3569962 731469 468734\n"
            ),
        },
    ]
    p5_ctx = {
        "retrieval_url": "https://dashboard.udiseplus.gov.in/report2026/static/media/test.pdf",
        "expected_sha256": _SHA_B,
        "source_landing_page": "https://dashboard.udiseplus.gov.in/",
        "official_publisher": "Department of School Education & Literacy, Ministry of Education",
        "one_teacher_five_grades": {
            "academic_year": "2025-26",
            "state_name": "Jharkhand",
            "state_key": "jharkhand",
            "is_ut": False,
            "source_document_title": "UDISE+ 2025-26 Booklet (Existing Structure)",
            "table_pages": {"table_2_2": 32, "table_3_3": 42, "table_4_3": 54, "table_5_2": 86},
        },
    }

    p6_records = [
        {
            "document": "primary",
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
                "The process for preparation of Detailed Project Report (DPR) for "
                "development of NH-11 from Jhunjhunu to Fatehpur to four-lanes has been initiated."
            ),
            "qtype": "SQ",
            "qno": 544,
        },
        {
            "document": "secondary",
            "page": 1,
            "secondary_sha256": _SHA_B,
            "raw_text": (
                "LOK SABHA STARRED QUESTION NO. 544\n"
                "WIDENING OF NH-11 ROAD FROM JHUNJHUNU TO FATEHPUR\n"
                "Detailed Project Report (DPR) for NH-11 initiated in January 2026."
            ),
        },
    ]
    p6_ctx = {
        "official_publisher": "Parliament of India — Digital Sansad",
        "source_landing_page": "https://sansad.in/ls/business/government-assurances",
        "retrieval_url": "https://sansad.in/api_ls/govtAssurance/assurances?loksabha=18&session=7&qNo=544&qType=SQ&page=1&size=20&locale=en",
        "expected_sha256": _SHA_A,
        "secondary_url": "https://sansad.in/getFile/lsapps/loksabhaquestions/annex/187/AS544_VvtUfG.pdf?source=lsapps",
        "secondary_expected_sha256": _SHA_B,
        "parliaments_to_do_list": {
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
            "secondary_role": "question_pdf",
            "portal_url": "https://sansad.in/ls/business/government-assurances",
            "is_anchor_case": True,
        },
    }

    p7_records = [
        {
            "tender_id": f"T-00{i}",
            "ocid": f"ocds-9t57fa-T-00{i}",
            "tender_title": f"Widening rural road section {i}",
            "buyer_name": "Public Works Roads Department, Assam",
            "vendor_name": "M/s Brahmaputra Infra Pvt. Ltd.",
            "bid_count": 1,
            "procurement_method": "open",
            "procurement_method_details": "",
            "exemption_reason": "",
            "award_value_inr": 15_000_000.0,
            "tender_published_date": "2021-06-01",
            "tender_end_date": "2021-06-18",
            "award_date": "2021-07-05",
            "item_category": "Civil Works - Roads",
            "fiscal_year": "FY2021-22",
            "jurisdiction": "State:Assam",
            "source_row_index": i,
        }
        for i in range(1, 5)
    ]
    p7_ctx = {
        "retrieval_url": "https://raw.githubusercontent.com/CivicDataLab/assam-tenders-data/1e12e516748130c15398b2501fb092bfa7413941/data/tenders.csv",
        "expected_sha256": _SHA_A,
        "official_publisher": "Public Works Roads Department, Assam",
        "source_landing_page": "https://assamtenders.gov.in",
        "only_one_bid": {
            "case_key": "p7-bo-2021-00001",
            "audit_mode": "both",
            "portal": "assamtenders.gov.in",
            "portal_key": "assam-ocds",
            "jurisdiction": "State:Assam",
            "jurisdiction_level": "state",
            "authority": "Public Works Roads Department, Assam",
            "authority_key": "assam-pwrd",
            "fiscal_year": "FY2021-22",
            "year": 2021,
            "record_selector": {
                "match_field": "tender_id",
                "match_value": "T-001",
                "tender_id": "T-001",
                "record_url": "https://assamtenders.gov.in/nicgep/app?tender=T-001",
            },
            "cohort_filter": {
                "buyer_field": "buyer_name",
                "buyer_value": "Public Works Roads Department, Assam",
                "winner_field": "vendor_name",
            },
            "is_anchor_case": False,
        },
    }

    p8_records = [
        {
            "document": "primary",
            "page": 1,
            "raw_text": (
                "Finance Act 2016 Section 47(viic) Sovereign Gold Bond Scheme: "
                "full capital gains exemption on redemption for individual holders."
            ),
        },
        {
            "document": "secondary",
            "page": 2,
            "secondary_sha256": _SHA_B,
            "raw_text": (
                "Finance Bill 2026 Clause 35 restricts Sovereign Gold Bond maturity exemption "
                "strictly to original subscriber holding continuously until maturity."
            ),
        },
    ]
    p8_ctx = {
        "retrieval_url": "https://www.indiabudget.gov.in/budget2016-17/ub2016-17/memo/mem1.pdf",
        "expected_sha256": _SHA_A,
        "secondary_url": "https://www.indiabudget.gov.in/doc/memo.pdf",
        "secondary_expected_sha256": _SHA_B,
        "official_publisher": "Ministry of Finance, Government of India",
        "source_landing_page": "https://www.indiabudget.gov.in/",
        "sovereign_lies": {
            "case_key": "p8-union-sgb-anchor-0001",
            "scheme_name": "Sovereign Gold Bond (SGB) Scheme",
            "scheme_slug": "sovereign-gold-bond",
            "tranche_or_phase": "All 67 Tranches — Capital Gains Tax Exemption",
            "jurisdiction": "Union of India",
            "jurisdiction_level": "union",
            "state_or_ut": None,
            "authority": "Department of Economic Affairs, Ministry of Finance & Reserve Bank of India",
            "authority_key": "dea-mof-rbi",
            "scheme_category": "sovereign_bonds_small_savings",
            "government_era_or_launch_year": "2015 (Union)",
            "promise_date": "2015-10-30",
            "promise_summary": "Finance Act 2016 Section 47(viic) provided capital gains exemption on redemption.",
            "promise_document_ref": "Finance Act 2016 Sec. 47(viic)",
            "promise_evidence_tokens": ["Sovereign Gold Bond", "47(viic)", "capital gains", "redemption"],
            "walkback_date": "2026-02-01",
            "walkback_summary": "Finance Bill 2026 Clause 35 restricted exemption to original subscribers holding continuously until maturity.",
            "walkback_document_ref": "Finance Bill 2026 Clause 35",
            "walkback_evidence_tokens": ["original subscriber", "Sovereign Gold Bond", "continuously until maturity"],
            "walkback_mechanism": "retroactive_tax_reversal",
            "retroactive_impact": "retroactive_existing_holders",
            "promised_metric": "100% capital gains exemption on redemption for all individual holders",
            "revised_metric": "Exemption restricted to original subscribers holding to maturity",
            "metric_unit": "tax_exemption_scope",
            "affected_group": "Secondary-market retail Sovereign Gold Bond holders",
            "estimated_exposure_inr": None,
            "exposure_basis": None,
            "statutory_anchor": "Income-tax Act 1961 Sec. 47(viic) & Finance Bill 2026 Clause 35",
            "benign_explanation_context": "Parliament exercises plenary statutory taxing authority.",
            "is_sgb_anchor": True,
            "is_anchor_case": True,
            "source_format": "pdf",
            "secondary_source_format": "pdf",
            "secondary_role": "walkback_document",
            "record_selector": {"promise_page": 1, "walkback_page": 2, "section_hint": "Sec. 47(viic)"},
        },
    }

    p9_records = [
        {
            "document": "primary",
            "page": 1,
            "raw_text": "National Testing Agency Public Notice: UGC-NET June 2024 conducted on 18 June 2024.",
        },
        {
            "document": "secondary",
            "page": 2,
            "secondary_sha256": _SHA_B,
            "raw_text": "Ministry of Education Press Release 19 June 2024: UGC-NET June 2024 examination cancelled.",
        },
    ]
    p9_ctx = {
        "retrieval_url": "https://www.nta.ac.in/Download/Notice/Notice_20240618_UGCNET.pdf",
        "expected_sha256": _SHA_A,
        "secondary_url": "https://static.pib.gov.in/WriteReadData/specificdocs/documents/2024/jun/ugcnet_cancel.pdf",
        "secondary_expected_sha256": _SHA_B,
        "official_publisher": "National Testing Agency (NTA)",
        "source_landing_page": "https://www.nta.ac.in/",
        "auditing_government_exams": {
            "case_key": "p9-union-ugc-net-0001",
            "exam_name": "UGC-NET June 2024",
            "exam_slug": "ugc-net-june-2024",
            "cycle_or_stage": "June 2024 Cycle",
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
            "schedule_summary": "NTA conducted UGC-NET June 2024 on 18 June 2024.",
            "schedule_document_ref": "NTA Public Notice (18 June 2024)",
            "schedule_evidence_tokens": ["UGC-NET", "18 June 2024", "National Testing Agency"],
            "outcome_summary": "Ministry of Education cancelled UGC-NET June 2024 on 19 June 2024.",
            "outcome_document_ref": "Ministry of Education Press Release (19 June 2024)",
            "outcome_evidence_tokens": ["UGC-NET", "cancelled", "19 June 2024"],
            "disruption_reason_category": "paper_integrity_compromise",
            "affected_candidates_count": 908580,
            "affected_candidates_basis": "NTA attendance figure of 9,08,580 candidates.",
            "statutory_anchor": "Public Examinations (Prevention of Unfair Means) Act, 2024",
            "benign_explanation_context": "Cancellation was ordered as a preventive integrity safeguard.",
            "source_format": "pdf",
            "secondary_source_format": "pdf",
            "secondary_role": "outcome_document",
            "is_anchor_case": True,
            "record_selector": {"schedule_page": 1, "outcome_page": 2, "section_hint": "UGC-NET Notice"},
        },
    }

    p10_records = [
        {
            "document": "primary",
            "page": 1,
            "raw_text": "Supreme Court Crl.A. No. 2295 of 2025: release order directed for District Jail, Ghaziabad.",
        },
        {
            "document": "secondary",
            "page": 2,
            "secondary_sha256": _SHA_B,
            "raw_text": "M.A. No. 1086 of 2025: judicial inquiry ordered and INR 5,00,000 provisional compensation directed.",
        },
    ]
    p10_ctx = {
        "retrieval_url": "https://api.sci.gov.in/supremecourt/2025/2295/order_20250429.pdf",
        "expected_sha256": _SHA_A,
        "secondary_url": "https://api.sci.gov.in/supremecourt/2025/1086/order_20250625.pdf",
        "secondary_expected_sha256": _SHA_B,
        "official_publisher": "Supreme Court of India",
        "source_landing_page": "https://www.sci.gov.in/",
        "innocent_but_locked_up": {
            "case_key": "p10-court-2025-ghaziabad-anchor-0001",
            "case_title": "District Jail Ghaziabad — 28-Day Custody Overrun",
            "portal_category": "court_portal",
            "court_tier": "supreme_court",
            "state_ut": "Uttar Pradesh",
            "jurisdiction": "State:Uttar Pradesh",
            "year": 2025,
            "document_date": "2025-06-25",
            "document_ref": "Supreme Court Order in M.A. No. 1086 of 2025",
            "overrun_mechanism": "post_acquittal_contempt_overrun",
            "is_anchor_case": True,
            "stage_1_acquittal_verified": True,
            "acquittal_order_date": "2025-04-29",
            "acquittal_court": "Supreme Court of India",
            "acquittal_case_number": "Crl.A. No. 2295 of 2025",
            "acquittal_order_type": "bail_release_order",
            "acquittal_quote": "Supreme Court directed release on bail.",
            "stage_2_continued_detention_verified": True,
            "prison_name": "District Jail, Ghaziabad",
            "custody_authority": "Superintendent, District Jail, Ghaziabad",
            "non_release_reason": "Prison awaited clarification on sub-section number.",
            "overrun_days": 28,
            "overrun_window": "2025-05-27 to 2025-06-24 (28 days)",
            "stage_3_contempt_verified": True,
            "contempt_case_number": "M.A. No. 1086 of 2025",
            "contempt_court": "Supreme Court of India",
            "contempt_filing_date": "2025-06-10",
            "contempt_order_date": "2025-06-25",
            "contempt_quote": "Court ordered a judicial inquiry into continued detention.",
            "stage_4_release_verified": True,
            "release_date": "2025-06-24",
            "release_verified": True,
            "compensation_inr": 500_000.0,
            "judicial_direction_summary": "State directed to pay INR 5,00,000 provisional compensation.",
            "custody_order_evidence_tokens": ["Crl.A. No. 2295", "District Jail, Ghaziabad", "release order"],
            "contempt_or_outcome_evidence_tokens": ["M.A. No. 1086", "5,00,000", "judicial inquiry"],
            "statutory_anchor": "Constitution of India Art. 21; Contempt of Courts Act 1971",
            "benign_explanation_context": "Prison authorities cited clarification on the parwana.",
            "source_format": "pdf",
            "secondary_source_format": "pdf",
            "secondary_role": "contempt_order_document",
            "record_selector": {"order_page": 1, "contempt_page": 2, "section_hint": "Order Para 3-9"},
        },
    }

    return {
        "M1": (m1_m2_records, {}),
        "M2": (m1_m2_records, {}),
        "M3": (m3_m4_records, {}),
        "M4": (m3_m4_records, {}),
        "M5": (m5_records, {}),
        "M6": (m6_records, {}),
        "M7": (m7_records, {}),
        "M8": (m8_records, {}),
        "P1": (p1_records, p1_ctx),
        "UB1": (ub1_records, ub1_ctx),
        "OR1": (or1_records, or1_ctx),
        "P5": (p5_records, p5_ctx),
        "P6": (p6_records, p6_ctx),
        "P7": (p7_records, p7_ctx),
        "P8": (p8_records, p8_ctx),
        "P9": (p9_records, p9_ctx),
        "P10": (p10_records, p10_ctx),
    }


def _run_w1_canonical() -> dict[str, Any]:
    """Execute the W1 gov_pages observation engine deterministically against an offline fixture."""
    host = "reproduce.gov.in"
    home = f"https://{host}/"
    html_hdr = {"content-type": "text/html; charset=utf-8"}
    pages = {
        home + "robots.txt": (404, {}, ""),
        home: (
            200,
            html_hdr,
            "<title>Department of Public Works</title>"
            "<main>Citizen information and public works disclosure portal.</main>"
            "<a href='/contact/'>Contact us</a><a href='/about/'>About us</a>"
            "<a href='/help/'>Help</a><footer>Last updated on 28 November 2026</footer>",
        ),
        home + "contact/": (
            200,
            html_hdr,
            "<h1>Contact Department of Public Works</h1>"
            "Postal address: Nirman Bhawan, New Delhi 110 011 "
            "office[at]reproduce[dot]gov[dot]in Telephone: +91 11 23061234",
        ),
    }

    async def _fake_request(self: Any, url: str, limit: int) -> tuple[int, dict[str, str], str]:
        self.last_request["reproduce.gov.in"] = 0
        return pages.get(url, (404, {}, ""))

    with patch.object(gov_pages.Reader, "request", _fake_request):
        res = gov_pages.observe_site({"domain": host})

    return {
        "methodology_code": "W1",
        "domain": res["domain"],
        "outcome": res["outcome"],
        "reason": res["reason"],
        "pages": res["pages"],
        "checks_count": len(res["checks"]),
        "checks": dict(sorted(res["checks"].items())),
    }


def _run_r1_canonical() -> dict[str, Any]:
    """Execute R1 deterministic reading verifier across valid, adversarial 1-row, scan, and golden cases."""
    page_text = (
        "DEMAND NO. 24 PUBLIC WORKS DEPARTMENT\n"
        "Budget 2024-25 Revised 2024-25 Actual 2023-24\n"
        "1. Capital Outlay on Roads 420.00 380.00 350.00\n"
        "2. Annual Maintenance 310.00 310.00 290.00\n"
        "Grand Total 730.00 690.00 640.00\n"
    )
    gt = {"be": 730.0, "re": 690.0, "actual": 640.0}
    valid_sub = {
        "document_sha256": _SHA_A,
        "page": 1,
        "column_headers": ["Budget 2024-25", "Revised 2024-25", "Actual 2023-24"],
        "printed_grand_total": dict(gt),
        "rows": [
            {"page": 1, "label": "Capital Outlay on Roads", "be": 420.0, "re": 380.0, "actual": 350.0, "verbatim_quote": "1. Capital Outlay on Roads 420.00 380.00 350.00"},
            {"page": 1, "label": "Annual Maintenance", "be": 310.0, "re": 310.0, "actual": 290.0, "verbatim_quote": "2. Annual Maintenance 310.00 310.00 290.00"},
        ],
    }
    invented_one_row = {
        "document_sha256": _SHA_A,
        "page": 1,
        "column_headers": ["Budget 2024-25", "Revised 2024-25", "Actual 2023-24"],
        "printed_grand_total": dict(gt),
        "rows": [
            {"page": 1, "label": "Invented row", "be": 730.0, "re": 690.0, "actual": 640.0, "verbatim_quote": "Grand Total 730.00 690.00 640.00"},
        ],
    }

    r_valid = verify_budget_extraction(
        valid_sub, expected_sha256=_SHA_A, page_texts={1: page_text}, expected_grand_total=gt, min_rows=2, require_text_verification=True, golden_expected=valid_sub, min_golden_accuracy=0.95
    )
    r_fake = verify_budget_extraction(
        invented_one_row, expected_sha256=_SHA_A, page_texts={1: page_text}, expected_grand_total=gt, min_rows=2, require_text_verification=True
    )
    r_scan = verify_budget_extraction(
        valid_sub, expected_sha256=_SHA_A, page_texts={}, expected_grand_total=gt, min_rows=2
    )

    if not (r_valid.accepted and r_valid.verification_mode == "deterministic_verified"):
        raise RuntimeError(f"R1 canonical valid case failed: {r_valid.errors}")
    if r_fake.accepted:
        raise RuntimeError("R1 adversarial 1-row invented table was unexpectedly accepted!")
    if not (r_scan.accepted and r_scan.verification_mode == "requires_second_model"):
        raise RuntimeError(f"R1 scan routing case failed: {r_scan.verification_mode} / {r_scan.errors}")

    return {
        "methodology_code": "R1",
        "valid_mode": r_valid.verification_mode,
        "valid_checks": dict(sorted(r_valid.checks.items())),
        "valid_golden_accuracy": r_valid.golden_accuracy,
        "adversarial_one_row_rejected": not r_fake.accepted,
        "adversarial_checks": dict(sorted(r_fake.checks.items())),
        "scan_mode": r_scan.verification_mode,
    }


def run_deterministic_engine_suite(methods: list[str] | None = None) -> dict[str, Any]:
    """Execute registered deterministic engines over canonical inputs and hash actual outputs."""
    cases = _canonical_cases()
    # Ensure every engine registered in _ENGINE_DISPATCH has a canonical verification fixture
    missing = sorted(set(_ENGINE_DISPATCH.keys()) - set(cases.keys()))
    if missing:
        raise RuntimeError(f"reproduce_suite is missing canonical fixtures for engines: {missing}")

    if methods is None:
        target_codes = sorted(_ENGINE_DISPATCH.keys()) + ["W1", "R1"]
    else:
        seen: set[str] = set()
        target_codes = []
        for m in methods:
            code = str(m or "").strip().upper()
            if code and code not in seen and (code in _ENGINE_DISPATCH or code in {"W1", "R1"}):
                seen.add(code)
                target_codes.append(code)

    executed: dict[str, dict[str, Any]] = {}
    for code in target_codes:
        if code == "W1":
            norm = _run_w1_canonical()
        elif code == "R1":
            norm = _run_r1_canonical()
        else:
            records, ctx = cases[code]
            res = run_engine(code, records, context=ctx, as_of_date=REPRODUCE_AS_OF_DATE)
            norm = normalize_engine_result(res)
        executed[code] = {
            "output_sha256": _canonical_sha256(norm),
            "summary": {
                k: v
                for k, v in norm.items()
                if k in {
                    "methodology_code",
                    "ruleset_version",
                    "script_hash",
                    "leads_count",
                    "cleared_count",
                    "unevaluable_count",
                    "monetary_audited_inr",
                    "outcome",
                    "checks_count",
                    "valid_mode",
                    "adversarial_one_row_rejected",
                    "scan_mode",
                }
            },
            "normalized_output": norm,
        }

    engine_outputs_hash = _canonical_sha256(
        {code: entry["normalized_output"] for code, entry in sorted(executed.items())}
    )
    return {
        "as_of_date": REPRODUCE_AS_OF_DATE,
        "engines_executed": len(executed),
        "engine_outputs_hash": engine_outputs_hash,
        "engines": {
            code: {"output_sha256": entry["output_sha256"], **entry["summary"]}
            for code, entry in sorted(executed.items())
        },
    }


def verify_pinned_reading_bundles(
    projects_dir: Path,
    selected_projects: list[dict[str, Any]],
) -> dict[str, Any]:
    """Verify SHA-256 integrity and adversarial 1-row rejection on every pinned R1 reading bundle."""
    base_dir = projects_dir.resolve()
    bundles: list[dict[str, Any]] = []
    errors: list[str] = []

    for proj in selected_projects:
        slug = str(proj.get("slug") or "")
        for task in proj.get("tasks") or []:
            if not isinstance(task, dict):
                continue
            method = str(task.get("methodology_code") or proj.get("methodology_code") or "").strip().upper()
            if method != "R1":
                continue
            tid = str(task.get("id") or "")
            payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
            ctx = payload.get("context") if isinstance(payload.get("context"), dict) else {}
            rc = ctx.get("reading") if isinstance(ctx.get("reading"), dict) else {}
            rel_path = str(rc.get("page_texts_path") or "").strip()
            expected_sha = str(rc.get("page_texts_sha256") or "").strip().lower()
            expected_pdf_sha = str(payload.get("expected_sha256") or "").strip().lower()
            expected_gt = rc.get("expected_grand_total") if isinstance(rc.get("expected_grand_total"), dict) else None
            min_rows = int(rc.get("min_rows") or 2)

            if not rel_path:
                continue
            target = (base_dir / rel_path).resolve()
            try:
                target.relative_to(base_dir)
            except ValueError:
                errors.append(f"{slug}/{tid}: page_texts_path escapes projects directory ({rel_path!r})")
                continue
            if not target.is_file():
                errors.append(f"{slug}/{tid}: missing pinned page_texts_path ({rel_path!r})")
                continue

            raw_bytes = target.read_bytes()
            actual_sha = hashlib.sha256(raw_bytes).hexdigest()
            if expected_sha and actual_sha != expected_sha:
                errors.append(
                    f"{slug}/{tid}: page_texts SHA-256 mismatch (expected {expected_sha[:12]}, got {actual_sha[:12]})"
                )
                continue

            try:
                bundle_doc = json.loads(raw_bytes.decode("utf-8"))
                raw_pages = (
                    (bundle_doc.get("pages") or bundle_doc.get("page_texts"))
                    if isinstance(bundle_doc, dict)
                    else None
                )
                page_texts = {int(k): str(v) for k, v in (raw_pages or {}).items()}
            except Exception as exc:
                errors.append(f"{slug}/{tid}: unreadable page_texts JSON ({exc})")
                continue

            if not page_texts or not any(len(v.strip()) >= 20 for v in page_texts.values()):
                errors.append(f"{slug}/{tid}: pinned page_texts contains no extractable text")
                continue

            # Live anti-spoofing check against the REAL pinned PDF page texts:
            # an invented 1-row table summing to expected_grand_total MUST be rejected!
            adversarial_rejected = True
            if expected_gt and expected_pdf_sha:
                first_page = min(page_texts.keys())
                fake_row: dict[str, Any] = {
                    "page": first_page,
                    "label": "Invented single-row summary",
                    "verbatim_quote": "Grand Total " + " ".join(str(v) for v in list(expected_gt.values())[:3]),
                }
                fake_row.update(expected_gt)
                fake_sub = {
                    "document_sha256": expected_pdf_sha,
                    "page": first_page,
                    "pages": sorted(page_texts.keys()),
                    "column_headers": ["Budget", "Total"],
                    "printed_grand_total": dict(expected_gt),
                    "rows": [fake_row],
                }
                v_fake = verify_budget_extraction(
                    fake_sub,
                    expected_sha256=expected_pdf_sha,
                    page_texts=page_texts,
                    expected_grand_total=expected_gt,
                    min_rows=min_rows,
                    require_text_verification=True,
                )
                adversarial_rejected = not v_fake.accepted
                if not adversarial_rejected:
                    errors.append(f"{slug}/{tid}: fabricated 1-row submission was NOT rejected!")

            bundles.append(
                {
                    "slug": slug,
                    "task_id": tid,
                    "page_texts_path": rel_path,
                    "page_texts_sha256": actual_sha,
                    "pages": len(page_texts),
                    "adversarial_one_row_rejected": adversarial_rejected,
                }
            )

    return {
        "bundles_verified": len(bundles),
        "reading_bundles_hash": _canonical_sha256(bundles),
        "errors": errors,
        "bundles": bundles,
    }


def execute_live_pinned_tasks(
    selected_projects: list[dict[str, Any]],
    *,
    limit_per_project: int = 3,
) -> dict[str, Any]:
    """Download live pinned task artifacts, verify SHA-256, run engines, and hash actual outputs."""
    from aiwatchdogs.connectors.loader import load_dataset

    results: list[dict[str, Any]] = []
    errors: list[str] = []

    for proj in selected_projects:
        slug = str(proj.get("slug") or "")
        ran = 0
        for task in proj.get("tasks") or []:
            if not isinstance(task, dict):
                continue
            if limit_per_project > 0 and ran >= limit_per_project:
                break
            method = str(task.get("methodology_code") or proj.get("methodology_code") or "").strip().upper()
            if method not in _ENGINE_DISPATCH:
                continue
            tid = str(task.get("id") or "")
            payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
            url = str(payload.get("retrieval_url") or payload.get("dataset_url") or "").strip()
            expected_sha = str(payload.get("expected_sha256") or "").strip() or None
            if not url:
                continue
            ran += 1
            try:
                ctx = dict(payload.get("context") or {}) if isinstance(payload.get("context"), dict) else {}
                ctx.setdefault("retrieval_url", url)
                if expected_sha:
                    ctx.setdefault("expected_sha256", expected_sha)
                ds = load_dataset(url, expected_sha256=expected_sha, context=ctx)
                records = list(ds.records)
                sec_url = str(payload.get("secondary_url") or ctx.get("secondary_url") or "").strip()
                sec_sha = str(payload.get("secondary_expected_sha256") or payload.get("secondary_sha256") or ctx.get("secondary_expected_sha256") or "").strip() or None
                if sec_url:
                    sec_ds = load_dataset(sec_url, expected_sha256=sec_sha, context=ctx)
                    primary_tagged = [{**r, "document": r.get("document", "primary")} for r in records]
                    secondary_tagged = [
                        {**r, "document": "secondary", "secondary_sha256": sec_ds.sha256}
                        for r in sec_ds.records
                    ]
                    records = primary_tagged + secondary_tagged
                eng_res = run_engine(method, records, context=ctx, as_of_date=REPRODUCE_AS_OF_DATE)
                norm = normalize_engine_result(eng_res)
                results.append(
                    {
                        "slug": slug,
                        "task_id": tid,
                        "methodology_code": method,
                        "primary_sha256": ds.sha256,
                        "output_sha256": _canonical_sha256(norm),
                        "leads_count": norm["leads_count"],
                        "cleared_count": norm["cleared_count"],
                    }
                )
            except Exception as exc:
                errors.append(f"{slug}/{tid} ({method}): {exc}")

    return {
        "tasks_executed": len(results),
        "live_outputs_hash": _canonical_sha256(results),
        "errors": errors,
        "tasks": results,
    }
