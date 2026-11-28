"""P7 plain-language headlines and briefs (core/plain/p7.py) for only-one-bid findings."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from aiwatchdogs.core.plain import BRIEF_MAX, HEADLINE_MAX, plain_brief
from aiwatchdogs.core.plain.p7 import render
from aiwatchdogs.core.safety import lint_neutrality

# Fixtures are real findings trimmed to the fields the writer reads. Winners who are natural
# persons are replaced with a placeholder, since the writer must never print them anyway.
PERSON = "RAMESH KUMAR EXAMPLE"


def _finding(rule: str, entity: str, state: str, exposure: float, **metrics: Any) -> dict[str, Any]:
    return {
        "methodology_code": "P7",
        "rule_triggered": rule,
        "entity_name": entity,
        "jurisdiction": f"State:{state}",
        "monetary_exposure_inr": exposure,
        "anomaly_metrics": metrics,
    }


COMBINED_QUALIFIED = _finding(
    "P7-COMBINED-SB-RW",
    "Urban Development Department",
    "Assam",
    360217353.3,
    portal="assamtenders.gov.in (OCDS v3)",
    signal_category="single_bidder_repeat_winner",
    triggered_rules=["P7-COMBINED-SB-RW", "P7-SINGLE-QUALIFIED-AFTER-TECH-REJECTION", "P7-SHORT-BID-WINDOW"],
    item_category="Civil Works - Water Works",
    bids_received=4,
    technically_qualified_bids=1,
    effective_bids=1,
    bid_window_days=7,
    publish_date="2020-08-05",
    close_date="2020-08-12",
    award_date="2020-12-15",
    fiscal_year_start="FY2020-21",
    fiscal_year_end="FY2020-21",
    amount_type="award_value",
    award_value_inr=360217353.3,
    tender_estimate_inr=384151000.0,
    winner_name="SEARS INDIA IMPEX LTD.",
    winner_total_wins=4,
    winner_single_bid_wins=3,
    winner_cohort_total_inr=408058281.65,
    tender_title="Execution, Testing , Commissioning of Digboi Town Water Supply Scheme",
)

COMBINED_ONE_BID = _finding(
    "P7-COMBINED-SB-RW",
    "Public Works Roads Department",
    "Assam",
    26210922.0,
    portal="assamtenders.gov.in (OCP #131)",
    signal_category="single_bidder_repeat_winner",
    triggered_rules=["P7-COMBINED-SB-RW", "P7-SINGLE-BID-RECEIVED"],
    item_category="Works",
    bids_received=1,
    effective_bids=1,
    bid_window_days=180,
    publish_date="2018-12-24",
    fiscal_year_start="FY2018-19",
    fiscal_year_end="FY2025-26",
    amount_type="award_value",
    award_value_inr=26210922.0,
    winner_name=PERSON,
    winner_total_wins=6,
    winner_single_bid_wins=3,
    winner_cohort_total_inr=414891219.0,
    tender_title="SOPD work for 2018-19",
)

SINGLE_ESTIMATE = _finding(
    "P7-SINGLE-BID",
    "Guwahati Municipal Corporation",
    "Assam",
    12521000.0,
    portal="assamtenders.gov.in (OCP #131)",
    signal_category="unexplained_single_bidder",
    triggered_rules=["P7-SINGLE-BID", "P7-SINGLE-BID-RECEIVED"],
    item_category="Works",
    bids_received=1,
    effective_bids=1,
    bid_window_days=90,
    publish_date="2021-02-23",
    amount_type="tender_estimate",
    tender_estimate_inr=12521000.0,
    winner_name="",
    winner_total_wins=0,
    authority_single_bid_rate_pct=26.62,
    tender_title="Improvement of Ajanta Path Beltola",
)

SINGLE_QUALIFIED = _finding(
    "P7-SINGLE-BID",
    "Revenue (DM) Department",
    "Andhra Pradesh",
    58482986.0,
    portal="Indian Government (World Bank–assisted projects)",
    signal_category="unexplained_single_bidder",
    triggered_rules=["P7-SINGLE-BID", "P7-SINGLE-QUALIFIED-AFTER-TECH-REJECTION", "P7-HIGH-AUTHORITY-SB-RATE"],
    item_category="Goods",
    bids_received=4,
    technically_qualified_bids=1,
    effective_bids=1,
    award_date="2018-12-21",
    amount_type="award_value",
    award_value_inr=58482986.0,
    winner_name="CEINSYS TECH LIMITED",
    winner_total_wins=1,
    winner_single_bid_wins=1,
    authority_single_bid_rate_pct=57.14,
    tender_title="Andhra Pradesh Disaster Recovery Project",
)

SINGLE_SHORT_WINDOW = _finding(
    "P7-SINGLE-BID",
    "PWD",
    "Himachal Pradesh",
    9629519.0,
    portal="hptenders.gov.in (OCP #77)",
    signal_category="unexplained_single_bidder",
    triggered_rules=["P7-SINGLE-BID", "P7-SINGLE-BID-RECEIVED", "P7-SHORT-BID-WINDOW"],
    item_category="By 2030, achieve universal and equitable access to safe and affordable drinking water for all",
    bids_received=1,
    effective_bids=1,
    bid_window_days=12,
    publish_date="2018-12-14",
    close_date="2018-12-26",
    amount_type="award_value",
    award_value_inr=9629519.0,
    winner_name=PERSON,
    winner_total_wins=1,
    tender_title="C/O Veterinary Hospital building at Kalpa in Distt. Kinnaur (H.P.)",
)

REPEAT_SHARE = _finding(
    "P7-REPEAT-WINNER",
    "Municipal Bodies||Malkangiri Municipal Council",
    "Odisha",
    1428695.77,
    portal="tendersodisha.gov.in (AOC)",
    signal_category="repeat_winner_concentration",
    triggered_rules=[
        "P7-REPEAT-WINNER",
        "P7-PERSISTENT-MULTI-AWARD",
        "P7-CONSECUTIVE-WIN-STREAK",
        "P7-HIGH-AWARD-COUNT-SHARE",
        "P7-HIGH-AWARD-VALUE-SHARE",
    ],
    item_category="Civil Works - Others",
    publish_date="2023-12-12",
    award_date="2024-05-20",
    fiscal_year_start="FY2024-25",
    fiscal_year_end="FY2026-27",
    amount_type="award_value",
    award_value_inr=1428695.77,
    winner_name=PERSON,
    winner_total_wins=14,
    winner_single_bid_wins=0,
    winner_cohort_total_inr=12624309.41,
    winner_share_by_count_pct=36.84,
    winner_share_by_value_pct=30.98,
    tender_title="Construction of Major RCC Drain from LIC Office to Balisagar Down(Reach-2) Ward No-7",
)

REPEAT_COMPANY = _finding(
    "P7-REPEAT-WINNER",
    "Department of Water Resources||Chief Engineer - DoWR",
    "Assam",
    7330253.64,
    portal="assamtenders.gov.in (AOC)",
    signal_category="repeat_winner_concentration",
    triggered_rules=["P7-REPEAT-WINNER", "P7-PERSISTENT-MULTI-AWARD", "P7-CONSECUTIVE-WIN-STREAK"],
    item_category="Supply of Materials",
    publish_date="2025-12-22",
    award_date="2026-03-26",
    fiscal_year_start="FY2025-26",
    fiscal_year_end="FY2026-27",
    amount_type="award_value",
    award_value_inr=7330253.64,
    winner_name="SMILE NONWOVENS PVT. LTD.",
    winner_total_wins=12,
    winner_single_bid_wins=0,
    winner_cohort_total_inr=397395714.86,
    winner_share_by_count_pct=1.63,
    winner_share_by_value_pct=1.28,
)

REPEAT_MULTI_BID = _finding(
    "P7-REPEAT-WINNER",
    "GIM State Forest Department MP",
    "Madhya Pradesh",
    400000.0,
    portal="Indian Government (World Bank–assisted projects)",
    signal_category="repeat_winner_concentration",
    triggered_rules=["P7-REPEAT-WINNER", "P7-PERSISTENT-MULTI-AWARD"],
    item_category="Works",
    bids_received=3,
    technically_qualified_bids=3,
    effective_bids=3,
    award_date="2023-04-24",
    fiscal_year_start="FY2019-20",
    fiscal_year_end="FY2023-24",
    amount_type="award_value",
    award_value_inr=400000.0,
    winner_name="PERFECT SOLUTIONS",
    winner_total_wins=7,
    winner_cohort_total_inr=7084075.0,
    winner_share_by_count_pct=6.14,
    winner_share_by_value_pct=5.12,
)

GAP_NO_BID_COUNT = _finding(
    "P7-PROCESS-GAP",
    "CE RW I||RWCIRCLE-BARIPADA",
    "Odisha",
    30454850.0,
    portal="tendersodisha.gov.in (AOC)",
    signal_category="short_window_or_process_gap",
    triggered_rules=["P7-PROCESS-GAP", "P7-UNRECORDED-BID-COUNT-REPEAT-WINNER"],
    item_category="Civil Works - Bridges",
    publish_date="2023-04-20",
    award_date="2023-08-02",
    fiscal_year_start="FY2020-21",
    fiscal_year_end="FY2023-24",
    amount_type="award_value",
    award_value_inr=30454850.0,
    winner_name=PERSON,
    winner_total_wins=3,
    winner_single_bid_wins=0,
    winner_cohort_total_inr=80468353.18,
)

GAP_LONG_NAME = _finding(
    "P7-PROCESS-GAP",
    "Orissa Community Tank Development and Management Society",
    "Odisha",
    5714398.0,
    portal="tendersodisha.gov.in (AOC)",
    signal_category="short_window_or_process_gap",
    triggered_rules=["P7-PROCESS-GAP", "P7-UNRECORDED-BID-COUNT-REPEAT-WINNER"],
    item_category="Civil Works - Others",
    award_date="2022-09-20",
    fiscal_year_start="FY2022-23",
    fiscal_year_end="FY2022-23",
    amount_type="award_value",
    award_value_inr=5714398.0,
    winner_name=PERSON,
    winner_total_wins=3,
)

# The engine also raises P7-PROCESS-GAP on a short bid window. No live sample carries it yet,
# so this one is the real Baripada record with a window and bid count added.
GAP_SHORT_WINDOW = copy.deepcopy(GAP_NO_BID_COUNT)
GAP_SHORT_WINDOW["anomaly_metrics"].update(
    triggered_rules=["P7-PROCESS-GAP", "P7-SHORT-BID-WINDOW"],
    bid_window_days=5,
    bids_received=2,
    effective_bids=2,
    winner_total_wins=1,
)

EXPECTED: list[tuple[str, dict[str, Any], str, str]] = [
    (
        "combined_qualified",
        COMBINED_QUALIFIED,
        'One qualified bid for an Assam Urban Development Department tender, winner name on 4 contracts',
        ('Assam tender portal records show 4 bids, of which one qualified, for this water works tender, awarded for ₹36.02 crore. Bids were open for 7 days. Sears India Impex Ltd is listed on 4 Urban Development Department contracts in 2020-21.'),
    ),
    (
        "combined_one_bid",
        COMBINED_ONE_BID,
        'One bid for ₹2.62 crore Assam Public Works Roads Department tender, winner name on 6 contracts',
        ('Assam tender portal records show one bid for this works tender, published on 24 Dec 2018 and awarded for ₹2.62 crore. The same winner name is listed on 6 Public Works Roads Department contracts from 2018-19 to 2025-26.'),
    ),
    (
        "single_estimate",
        SINGLE_ESTIMATE,
        "Only one bid recorded for a ₹1.25 crore Guwahati Municipal Corporation tender",
        ('Assam tender portal records show one bid for this Guwahati Municipal Corporation works tender, published on 23 Feb 2021 and estimated at ₹1.25 crore. The record does not name a winner.'),
    ),
    (
        "single_qualified",
        SINGLE_QUALIFIED,
        "Only one of 4 bids qualified for a ₹5.85 crore Andhra Pradesh Revenue (DM) Department tender",
        ("World Bank project procurement records show 4 bids, of which one qualified, for this Andhra Pradesh "
        "Revenue (DM) Department goods supply tender, awarded for ₹5.85 crore. The winner is listed as Ceinsys "
        "Tech Limited."),
    ),
    (
        "single_short_window",
        SINGLE_SHORT_WINDOW,
        "Only one bid recorded for a ₹96.3 lakh Himachal Pradesh PWD tender",
        ('Himachal Pradesh tender portal records show one bid for this PWD tender, published on 14 Dec 2018 and awarded for ₹96.3 lakh. Bids were open for 12 days.'),
    ),
    (
        "repeat_share",
        REPEAT_SHARE,
        'Same winner name listed on 14 Malkangiri Municipal Council contracts',
        ('Odisha tender portal records list the same winner name on 14 Malkangiri Municipal Council contracts from 2024-25 to 2026-27, worth ₹1.26 crore in all. That is 36.8% of the awards listed for the civic body, and 31% of their value.'),
    ),
    (
        "repeat_company",
        REPEAT_COMPANY,
        'Same winner name listed on 12 Assam Water Resources Department contracts',
        ('Assam tender portal records list Smile Nonwovens Pvt Ltd on 12 Water Resources Department contracts from 2025-26 to 2026-27, worth ₹39.74 crore in all. This one, for goods supply, was awarded on 26 Mar 2026 for ₹73.3 lakh.'),
    ),
    (
        "repeat_multi_bid",
        REPEAT_MULTI_BID,
        'Same winner name listed on 7 GIM State Forest Department MP contracts',
        ('World Bank project procurement records list the same winner name on 7 GIM State Forest Department MP contracts from 2019-20 to 2023-24, worth ₹70.8 lakh in all. This one was awarded on 24 Apr 2023 for ₹4 lakh. It drew 3 bids.'),
    ),
    (
        "gap_no_bid_count",
        GAP_NO_BID_COUNT,
        'Same winner name listed on 3 Rural Works Circle Baripada contracts, bid count not shown',
        ('Odisha tender portal records list the same winner name on 3 Rural Works Circle Baripada contracts from 2020-21 to 2023-24, worth ₹8.05 crore in all. The award data does not show how many bids were received.'),
    ),
    (
        "gap_long_name",
        GAP_LONG_NAME,
        'Same winner name listed on 3 Orissa Community Tank Development and Management Society contracts',
        ('Odisha tender portal records list the same winner name on 3 Orissa Community Tank Development and Management Society contracts in 2022-23. The award data does not show how many bids were received.'),
    ),
    (
        "gap_short_window",
        GAP_SHORT_WINDOW,
        "Bids for a ₹3.05 crore Rural Works Circle Baripada tender were open for 5 days",
        ('Odisha tender portal records show bids for this Rural Works Circle Baripada bridge works tender were open for 5 days. It was awarded on 2 Aug 2023 for ₹3.05 crore. It drew 2 bids.'),
    ),
]
FIXTURES = [case[1] for case in EXPECTED]
IDS = [case[0] for case in EXPECTED]


@pytest.mark.parametrize(("finding", "headline", "brief"), [case[1:] for case in EXPECTED], ids=IDS)
def test_exact_text(finding: dict[str, Any], headline: str, brief: str) -> None:
    assert render(finding) == (headline, brief)
    # The dispatcher must pass the writer's text through untouched: no clip, no fallback.
    assert plain_brief(finding) == {"headline": headline, "brief": brief}


@pytest.mark.parametrize("finding", FIXTURES, ids=IDS)
def test_house_style(finding: dict[str, Any]) -> None:
    headline, brief = render(finding)
    assert 0 < len(headline) <= HEADLINE_MAX - 10
    assert 0 < len(brief) <= BRIEF_MAX - 15
    assert not headline.endswith((".", "…")) and brief.endswith(".") and not brief.endswith("…")
    text = f"{headline} {brief}"
    for clutter in ("||", "—", ";", "!", "?", "State:", "P7-", "  ", "(OCP", "none_recorded", "award_value"):
        assert clutter not in text
    assert PERSON.lower() not in text.lower()
    assert lint_neutrality(headline, brief, ["placeholder"]).passed
    assert render(copy.deepcopy(finding)) == (headline, brief)


def test_null_counts_are_left_out_not_guessed() -> None:
    finding = copy.deepcopy(GAP_NO_BID_COUNT)
    finding["anomaly_metrics"].update(winner_total_wins=None, award_date=None, fiscal_year_start=None)
    headline, brief = render(finding)
    assert headline == "Bid count not shown for a ₹3.05 crore Rural Works Circle Baripada contract"
    assert brief == (
        "Odisha tender portal records do not show how many bids were received for this Rural Works Circle "
        "Baripada bridge works contract, awarded for ₹3.05 crore."
    )


def test_implausibly_small_amount_is_dropped() -> None:
    finding = copy.deepcopy(COMBINED_ONE_BID)
    finding["monetary_exposure_inr"] = 140.0
    finding["anomaly_metrics"]["award_value_inr"] = 140.0
    headline, brief = render(finding)
    assert headline == "One bid for an Assam Public Works Roads Department tender, winner name on 6 contracts"
    assert "₹" not in brief


def test_missing_authority_and_state_still_reads() -> None:
    finding = copy.deepcopy(SINGLE_ESTIMATE)
    finding["entity_name"] = None
    finding["jurisdiction"] = ""
    headline, brief = render(finding)
    assert headline == "Only one bid recorded for a ₹1.25 crore public tender"
    assert brief.startswith("Public tender records show one bid for this works tender, published on 23 Feb 2021")


def test_unknown_or_empty_findings_fall_back() -> None:
    assert render({}) == ("", "")
    assert render({"rule_triggered": "P7-SINGLE-BID", "anomaly_metrics": None}) == ("", "")
    assert render({"rule_triggered": "P7-NEW-RULE", "anomaly_metrics": {"effective_bids": 1}}) == ("", "")
    # A single-bid rule whose record no longer shows one bid says nothing rather than guess.
    assert render({"rule_triggered": "P7-SINGLE-BID", "anomaly_metrics": {"effective_bids": None}}) == ("", "")
    out = plain_brief({"methodology_code": "P7", "title": "Procurement Pattern Lead: Example", "summary": "One."})
    assert out["headline"] == "Example"


@pytest.mark.parametrize("rule", ["P7-COMBINED-SB-RW", "P7-SINGLE-BID", "P7-REPEAT-WINNER", "P7-PROCESS-GAP", "", None])
@pytest.mark.parametrize("filler", [None, "", 0, -3, 7.5, "abc", "12", True, [], ["x"], {}, {"a": 1}, float("nan")])
def test_never_raises_on_bad_fields(rule: Any, filler: Any) -> None:
    keys = list(COMBINED_QUALIFIED["anomaly_metrics"]) + ["authority", "state_or_ut", "authority_single_bid_rate_pct"]
    finding = {
        "methodology_code": "P7",
        "rule_triggered": rule,
        "entity_name": filler,
        "jurisdiction": filler,
        "fiscal_year": filler,
        "monetary_exposure_inr": filler,
        "title": filler,
        "summary": filler,
        "anomaly_metrics": {key: filler for key in keys},
    }
    headline, brief = render(finding)
    assert isinstance(headline, str) and isinstance(brief, str)
    assert len(headline) <= HEADLINE_MAX and len(brief) <= BRIEF_MAX
    out = plain_brief(finding)
    assert isinstance(out["headline"], str) and isinstance(out["brief"], str)
    for broken in (None, [], "text", 5):
        assert all(isinstance(part, str) for part in render({**finding, "anomaly_metrics": broken}))
