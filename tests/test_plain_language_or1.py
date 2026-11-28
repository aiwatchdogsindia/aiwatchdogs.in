"""Plain-language headlines and briefs for OR1 CAG duplicate-billing passages."""

from __future__ import annotations

from typing import Any

import pytest

from aiwatchdogs.core.plain import BRIEF_MAX, HEADLINE_MAX, plain_brief
from aiwatchdogs.core.plain.or1 import render
from aiwatchdogs.core.safety import lint_neutrality


def _finding(
    entity: str,
    rule: str,
    category: str,
    report_no: str,
    page: int,
    para: str,
    phrases: list[str],
    agencies: list[str],
    amount: float,
    section: str = "",
) -> dict[str, Any]:
    heading = f" under section '{section}'" if section else ""
    return {
        "methodology_code": "OR1",
        "entity_name": entity,
        "jurisdiction": "Union of India",
        "monetary_exposure_inr": amount,
        "rule_triggered": rule,
        "summary": f"CAG {report_no} (Audit report title, p.{page}, {para}{heading}) records a "
        f"duplicate billing / double payment indicator (matched phrases: {', '.join(phrases)}).",
        "anomaly_metrics": {
            "report_no": report_no,
            "report_year": "2025",
            "page": page,
            "para_number": para,
            "signal_category": category,
            "matched_signals": phrases,
            "agencies_mentioned": agencies,
            "amount_inr": amount,
        },
    }


DUPLICATE = "duplicate_payment_or_billing"
OVERLAP = "dual_agency_or_scheme_overlap"

WITH_AMOUNT = _finding(
    "REC (6.3.1.2)", "OR1-DUPLICATE-BILLING", DUPLICATE, "Report No. 17 of 2025", 80, "6.3.1.2",
    ["double payment"], ["REC"], 103700000.0, "6.3.1.2 Claiming of double benefit",
)
TWO_BODIES = _finding(
    "REC (p.15)", "OR1-DUPLICATE-BILLING", DUPLICATE, "Report No. 17 of 2025", 15, "p.15",
    ["double payment"], ["REC", "Ministry of Power"], 22400000.0,
)
SECTOR_ONLY = _finding(
    "Union Government (Taxes and Duties) (6.3)", "OR1-DUPLICATE-BILLING", DUPLICATE, "Report No. 33 of 2025", 75, "6.3",
    ["double payment", "same invoice"], [], 0.0, "6.3 Double payment of Drawback on the same invoice",
)
LONG_BODY_NAME = _finding(
    "Department of Financial Services The New India Assurance Company (p.168)", "OR1-DUPLICATE-BILLING", DUPLICATE,
    "Report No. 12 of 2024", 168, "p.168", ["double payment"],
    ["Department of Financial Services The New India Assurance Company"], 0.0,
)
OVERLAP_NO_REPORT_NUMBER = _finding(
    "Union Government (Industry and Commerce) (7.3.4.3)", "OR1-DUAL-AGENCY-OVERLAP", OVERLAP,
    "CAG Union Report 123656 (2025)", 134, "7.3.4.3", ["duplicate work"], [], 0.0,
    "7.3.4.3 Continued use of the older version of IIS",
)
OVERLAP_SECTOR = _finding(
    "Union Government (Finance,Information and Communication) (2.2.4.3)", "OR1-DUAL-AGENCY-OVERLAP", OVERLAP,
    "Report No. 1 of 2025", 52, "2.2.4.3", ["two agencies"], [], 0.0, "2.2.4.3 Lapses in appraisal and verification",
)

CAVEAT = "This is a match on wording, not a finding about any one payment."
CAVEAT_OVERLAP = "This is a match on wording, not a finding about any one project or payment."

CASES: list[tuple[dict[str, Any], str, str]] = [
    (
        WITH_AMOUNT,
        'CAG Report No. 17 of 2025 refers to "double payment" on page 80, cites ₹10.37 crore',
        ("The passage, which mentions REC and cites ₹10.37 crore, is on page 80 of CAG Report No. 17 of 2025, "
        f'in paragraph 6.3.1.2 under the heading "Claiming of double benefit". {CAVEAT}'),
    ),
    (
        TWO_BODIES,
        'CAG Report No. 17 of 2025 refers to "double payment" on page 15, cites ₹2.24 crore',
        ("The passage, which mentions REC and the Ministry of Power and cites ₹2.24 crore, "
        f"is on page 15 of CAG Report No. 17 of 2025. {CAVEAT}"),
    ),
    (
        SECTOR_ONLY,
        'CAG report on taxes and duties refers to "double payment" on page 75',
        ('The passage, which also uses "same invoice", is on page 75 of CAG Report No. 33 of 2025, in paragraph 6.3 '
        f'under the heading "Double payment of Drawback on the same invoice". {CAVEAT}'),
    ),
    (
        LONG_BODY_NAME,
        'CAG Report No. 12 of 2024 refers to "double payment" on page 168',
        ("The passage, which mentions the Department of Financial Services, is on page 168 of "
        f"CAG Report No. 12 of 2024. {CAVEAT}"),
    ),
    (
        OVERLAP_NO_REPORT_NUMBER,
        'CAG report on industry and commerce refers to "duplicate work" on page 134',
        ("The passage is on page 134 of a 2025 CAG audit report, in paragraph 7.3.4.3 "
        f'under the heading "Continued use of the older version of IIS". {CAVEAT_OVERLAP}'),
    ),
    (
        OVERLAP_SECTOR,
        'CAG report on finance, information and communication refers to "two agencies" on page 52',
        ("The passage is on page 52 of CAG Report No. 1 of 2025, in paragraph 2.2.4.3 "
        f'under the heading "Lapses in appraisal and verification". {CAVEAT_OVERLAP}'),
    ),
]


@pytest.mark.parametrize(("finding", "headline", "brief"), CASES)
def test_exact_text(finding: dict[str, Any], headline: str, brief: str) -> None:
    assert render(finding) == (headline, brief)


@pytest.mark.parametrize(("finding", "headline", "brief"), CASES)
def test_fits_without_clipping_and_is_neutral(finding: dict[str, Any], headline: str, brief: str) -> None:
    out = plain_brief(finding)
    assert out == {"headline": headline, "brief": brief}
    assert len(headline) <= HEADLINE_MAX and len(brief) <= BRIEF_MAX
    assert not headline.endswith((".", "…")) and brief.endswith(".")
    for mark in ("—", ";", "!", "?", "OR1-", "CAG CAG", "123656", "p.", "indicator"):
        assert mark not in headline and mark not in brief
    assert lint_neutrality(headline, brief, ["placeholder"]).passed


def test_thin_fields_still_give_a_sentence() -> None:
    finding = {"rule_triggered": "OR1-DUAL-AGENCY-OVERLAP", "anomaly_metrics": {"page": None, "matched_signals": None}}
    assert render(finding) == (
        "A CAG audit report has wording on overlapping work or funding",
        f"The passage is in a CAG audit report. {CAVEAT_OVERLAP}",
    )


@pytest.mark.parametrize(
    "finding",
    [
        {},
        {"anomaly_metrics": None, "entity_name": None, "summary": None, "rule_triggered": None},
        {"entity_name": ["x"], "summary": 5, "rule_triggered": {}, "monetary_exposure_inr": "abc",
         "anomaly_metrics": {"matched_signals": "double payment", "agencies_mentioned": 7, "page": "z",
                             "para_number": 6.3, "report_no": None, "report_year": 2025, "amount_inr": []}},
        {"rule_triggered": "OR1-DUPLICATE-BILLING", "anomaly_metrics": {"matched_signals": [None, 3, ""], "page": -4}},
        {"rule_triggered": "OR1-DUPLICATE-BILLING", "anomaly_metrics": "not a dict"},
        None,
    ],
)
def test_never_raises_on_bad_input(finding: Any) -> None:
    headline, brief = render(finding)
    assert isinstance(headline, str) and isinstance(brief, str)
    if isinstance(finding, dict):
        out = plain_brief({**finding, "methodology_code": "OR1"})
        assert isinstance(out["headline"], str) and out["headline"]
