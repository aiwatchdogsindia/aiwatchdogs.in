from __future__ import annotations

from typing import Any


def _f(code: str, status: str, metrics: dict[str, Any], **kw: Any) -> dict[str, Any]:
    base = {
        "id": f"fnd-{code.lower()}-{len(metrics)}-{status}",
        "mission_slug": "sample",
        "methodology_code": code,
        "title": kw.pop("title", f"{code} sample title"),
        "entity_name": kw.pop("entity_name", "Sample Department"),
        "jurisdiction": kw.pop("jurisdiction", "Union of India"),
        "fiscal_year": kw.pop("fiscal_year", "FY2024-25"),
        "monetary_exposure_inr": kw.pop("monetary_exposure_inr", 0.0),
        "anomaly_metrics": metrics,
        "rule_triggered": kw.pop("rule_triggered", f"{code}-RULE"),
        "status": status,
        "confirm_count": kw.pop("confirm_count", 1),
        "required_quorum": 3,
        "source_url": "https://www.indiabudget.gov.in/",
        "quarantined": False,
    }
    base.update(kw)
    return base


SAMPLES: list[dict[str, Any]] = [
    _f("P1", "peer_review", {"compare": "be_next_vs_re", "be_next": 616.03, "re": 447.37, "unit": "crore", "pct_difference": 37.7, "threshold_pct": 15.0}, entity_name="Delhi Police", monetary_exposure_inr=1.6866e9),
    _f("P1", "verified", {"compare": "re_vs_be", "re": 447.37, "be": 889.05, "unit": "crore", "pct_difference": -49.68, "threshold_pct": 10.0}, entity_name="Delhi Police", monetary_exposure_inr=4.4168e9, confirm_count=3),
    _f("P1", "peer_review", {"be": 12.5, "re": 12.5, "unit": "lakh"}, rule_triggered="P1-RE-EQUALS-BE", entity_name="Goa Police"),
    _f("UB1", "verified", {"compare": "actual_vs_budget_estimate", "budget_estimate": 1200.0, "revised_estimate": 900.0, "actual": 610.5, "unspent": 589.5, "unspent_pct": 49.13}, entity_name="Ministry of Rural Development", monetary_exposure_inr=5.895e9, confirm_count=3),
    _f("UB1", "cleared", {"compare": "revised_vs_budget_estimate", "budget_estimate": 300.0, "revised_estimate": 150.0, "revised_cut": 150.0, "revised_cut_pct": 50.0}, entity_name="Department of Space"),
    _f("OR1", "peer_review", {"signal_category": "duplicate_payment_or_billing", "amount_disclosed": True, "amount_inr": 2.35e7, "report_no": "12 of 2024"}, entity_name="National Highways Authority of India"),
    _f("OR1", "disputed", {"signal_category": "dual_agency_or_scheme_overlap", "amount_disclosed": False}, entity_name="Ministry of Jal Shakti"),
    _f("P5", "verified", {"signal_category": "high_single_teacher_concentration", "state_name": "Arunachal Pradesh", "academic_year": "2024-25", "single_teacher_schools": 1203, "single_teacher_share_pct": 34.2, "students_per_single_teacher": 21.4}, confirm_count=3),
    _f("P5", "peer_review", {"signal_category": "gov_primary_understaffed", "state_name": "Bihar", "academic_year": "2025-26", "gov_primary_teachers_per_school": 1.8}),
    _f("P6", "peer_review", {"signal_category": "overdue_pending_assurance", "ministry": "Ministry of Railways", "subject": "Safety audit of level crossings", "days_elapsed": 412}),
    _f("P6", "verified", {"signal_category": "partly_implemented_assurance", "ministry": "Ministry of Health and Family Welfare", "subject": "New AIIMS hostels"}, confirm_count=3),
    _f("P6", "retracted", {"signal_category": "dropped_without_fulfillment", "ministry": "Ministry of Coal", "subject": "Mine closure fund report"}),
    _f("P7", "verified", {"signal_category": "unexplained_single_bidder", "authority": "Municipal Corporation of Pune", "tender_title": "Supply of LED street lights", "award_value_inr": 4.5e7, "bids_received": 1, "effective_bids": 1}, monetary_exposure_inr=4.5e7, confirm_count=3),
    _f("P7", "peer_review", {"signal_category": "single_bidder_repeat_winner", "authority": "Kerala Water Authority", "tender_title": "Pipe laying, Ward 12", "winner_total_wins": 7}),
    _f("P7", "peer_review", {"signal_category": "repeat_winner_concentration", "authority": "Delhi Jal Board", "tender_title": "Desilting works", "winner_total_wins": 11}),
    _f("P7", "peer_review", {"signal_category": "short_window_or_process_gap", "authority": "Assam PWD", "tender_title": "Bridge repair", "bid_window_days": 3}),
    _f("P7", "peer_review", {"signal_category": "unexplained_single_bidder", "authority": "Odisha Health Department", "tender_title": "Hospital beds", "bids_received": 3, "technically_qualified_bids": 1, "effective_bids": 1}),
    _f("P8", "verified", {"scheme_name": "Sovereign Gold Bond", "walkback_year": "2024", "authority": "Ministry of Finance"}, confirm_count=3),
    _f("P9", "verified", {"exam_name": "UGC-NET June", "exam_year": "2024", "authority": "National Testing Agency", "disruption_type": "cancelled_after_conduct", "affected_candidates_count": 900000}, confirm_count=3),
    _f("P9", "peer_review", {"exam_name": "State Police Constable Exam", "exam_year": "2025", "authority": "State Recruitment Board", "disruption_type": "postponed", "delay_days": 120}),
    _f("P9", "peer_review", {"exam_name": "Teacher Eligibility Test", "exam_year": "2023", "authority": "State Board", "disruption_type": "unknown_new_type"}),
    _f("P10", "verified", {"state_ut": "Uttar Pradesh", "overrun_days": 46}, confirm_count=3),
    _f("P10", "under_review", {"state_ut": "Maharashtra"}),
    _f("W1", "peer_review", {}, entity_name="ministry.gov.in"),
    _f("ZZ9", "peer_review", {}, entity_name="Unknown Office", rule_triggered="ZZ9-RULE-X"),
    _f("P1", "peer_review", {"compare": "re_vs_be"}, entity_name="Broken Metrics Police"),
]
