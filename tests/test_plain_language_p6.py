"""Plain-language writer for P6: exact text for real findings, and behaviour on broken input."""

from __future__ import annotations

from typing import Any

import pytest

from aiwatchdogs.core.plain import BRIEF_MAX, HEADLINE_MAX, plain_brief
from aiwatchdogs.core.plain.p6 import render
from aiwatchdogs.core.safety import lint_neutrality

# Trimmed from live findings: only the fields the writer reads.
CASES: list[tuple[str, dict[str, Any], str, str]] = [
    (
        'pending',
        {'rule_triggered': 'P6-OVERDUE-PENDING',
         'entity_name': 'Department of Space',
         'anomaly_metrics': {'subject': 'NAVIC',
                             'house': 'Lok Sabha',
                             'promise_date': '2025-07-23',
                             'as_of_date': '2026-11-27',
                             'days_elapsed': 492,
                             'norm_days': 90,
                             'extensions_count': 0,
                             'secondary_pdf_pages': 2,
                             'signal_category': 'overdue_pending_assurance',
                             'recorded_status': 'pending',
                             'followup_text_status': 'no_implementation_statement'}},
        'Lok Sabha promise on NavIC still pending after 492 days',
        'The Department of Space gave this assurance in the Lok Sabha on 23 Jul 2025. No implementation statement had been laid by 27 Nov 2026. The norm is 90 days.',
    ),
    (
        'pending_with_extensions',
        {'rule_triggered': 'P6-OVERDUE-PENDING',
         'entity_name': 'Ministry of Communications',
         'anomaly_metrics': {'subject': '5G services in the country',
                             'house': 'Rajya Sabha',
                             'promise_date': '2024-07-25',
                             'as_of_date': '2026-11-27',
                             'days_elapsed': 855,
                             'norm_days': 90,
                             'extensions_count': 4,
                             'extension_upto': '2026-12-31',
                             'secondary_pdf_pages': 0,
                             'signal_category': 'overdue_pending_assurance',
                             'recorded_status': 'pending',
                             'followup_text_status': 'no_implementation_statement'}},
        'Rajya Sabha promise on 5G services in the country still pending after 855 days',
        'The Ministry of Communications gave this assurance in the Rajya Sabha on 25 Jul 2024. No implementation statement had been laid by 27 Nov 2026. The deadline was extended 4 times, the latest to 31 Dec 2026.',
    ),
    (
        'dropped_with_date',
        {'rule_triggered': 'P6-DROPPED-ASSURANCE',
         'entity_name': 'Ministry of Law and Justice',
         'anomaly_metrics': {'subject': 'Health Insurance for Advocates',
                             'house': 'Lok Sabha',
                             'promise_date': '2024-07-26',
                             'as_of_date': '2026-11-27',
                             'followup_date': '2026-09-02',
                             'days_elapsed': 854,
                             'fulfillment_days': 768,
                             'norm_days': 90,
                             'extensions_count': 0,
                             'secondary_pdf_pages': 1,
                             'signal_category': 'dropped_without_fulfillment',
                             'recorded_status': 'dropped',
                             'followup_text_status': 'dropped_by_committee'}},
        'Lok Sabha promise on Health Insurance for Advocates dropped with no implementation statement',
        'The Ministry of Law and Justice gave this assurance in the Lok Sabha on 26 Jul 2024. Parliament records list it as dropped on 2 Sep 2026, 768 days after it was given, with no implementation statement laid.',
    ),
    (
        'dropped_all_caps_subject',
        {'rule_triggered': 'P6-DROPPED-ASSURANCE',
         'entity_name': 'Ministry of Jal Shakti',
         'anomaly_metrics': {'subject': 'KADWAN RESERVOIR PROJECT',
                             'house': 'Rajya Sabha',
                             'promise_date': '2025-02-03',
                             'as_of_date': '2026-11-27',
                             'days_elapsed': 662,
                             'norm_days': 90,
                             'extensions_count': 2,
                             'extension_upto': '2026-04-06',
                             'secondary_pdf_pages': 0,
                             'signal_category': 'dropped_without_fulfillment',
                             'recorded_status': 'dropped',
                             'followup_text_status': 'dropped_by_committee'}},
        'Rajya Sabha promise on Kadwan Reservoir Project dropped with no implementation statement',
        'The Ministry of Jal Shakti gave this assurance in the Rajya Sabha on 3 Feb 2025. Parliament records list it as dropped, with no implementation statement laid.',
    ),
    (
        'fulfilled_late',
        {'rule_triggered': 'P6-FOLLOWUP-GAP',
         'entity_name': 'Ministry of Culture',
         'anomaly_metrics': {'subject': 'Cultural and Higher Training Institutes',
                             'house': 'Lok Sabha',
                             'promise_date': '2024-12-09',
                             'as_of_date': '2026-11-27',
                             'followup_date': '2026-03-25',
                             'days_elapsed': 718,
                             'fulfillment_days': 471,
                             'norm_days': 90,
                             'extensions_count': 0,
                             'secondary_pdf_pages': 3,
                             'signal_category': 'delayed_or_unreadable_followup',
                             'recorded_status': 'fully_implemented',
                             'followup_text_status': 'statement_ref_only'}},
        'Lok Sabha promise on Cultural and Higher Training Institutes marked fulfilled after 471 days',
        "The Ministry of Culture gave this assurance in the Lok Sabha on 9 Dec 2024. Records mark it fully implemented in a statement laid on 25 Mar 2026, 471 days later. The norm is 90 days. The statement's text is not shown online.",
    ),
    (
        'scanned_report',
        {'rule_triggered': 'P6-FOLLOWUP-GAP',
         'entity_name': 'Ministry of Railways',
         'anomaly_metrics': {'subject': 'ESTABLISHMENT OF RAIL LINK BETWEEN INDIA AND BHUTAN',
                             'house': 'Rajya Sabha',
                             'promise_date': '2023-12-15',
                             'as_of_date': '2026-11-27',
                             'days_elapsed': 1078,
                             'norm_days': 90,
                             'extensions_count': 0,
                             'secondary_pdf_pages': 2,
                             'signal_category': 'delayed_or_unreadable_followup',
                             'recorded_status': 'fully_implemented',
                             'followup_text_status': 'scanned_ir_pdf_no_text_layer'}},
        'Promise on Establishment of Rail Link Between India and Bhutan marked done, report unreadable',
        'The Ministry of Railways gave this assurance in the Rajya Sabha on 15 Dec 2023. Records mark it fully implemented, but the linked 2-page report is a scanned image with no readable text.',
    ),
    (
        'long_subject_moves_to_brief',
        {'rule_triggered': 'P6-OVERDUE-PENDING',
         'entity_name': 'Ministry of Defence',
         'anomaly_metrics': {'subject': 'Transfer of Civic Areas from Belagavi Cantonment Area to Belagavi '
                                        'Municipal Corporation',
                             'house': 'Rajya Sabha',
                             'promise_date': '2025-03-17',
                             'as_of_date': '2026-11-27',
                             'days_elapsed': 620,
                             'norm_days': 90,
                             'extensions_count': 3,
                             'extension_upto': '2026-12-17',
                             'secondary_pdf_pages': 0,
                             'signal_category': 'overdue_pending_assurance',
                             'recorded_status': 'pending',
                             'followup_text_status': 'no_implementation_statement'}},
        'Ministry of Defence promise to Rajya Sabha still pending after 620 days',
        'The assurance was given on 17 Mar 2025. It was on Transfer of Civic Areas from Belagavi Cantonment Area to Belagavi Municipal Corporation. No implementation statement had been laid by 27 Nov 2026.',
    ),
]

IDS = [case[0] for case in CASES]

BROKEN: list[Any] = [
    {},
    None,
    "not a finding",
    {"anomaly_metrics": None},
    {"anomaly_metrics": ["a", "list"]},
    {"rule_triggered": None, "entity_name": None, "monetary_exposure_inr": None, "anomaly_metrics": {}},
    {**CASES[0][1], "anomaly_metrics": dict.fromkeys(CASES[0][1]["anomaly_metrics"])},
    {
        **CASES[0][1],
        "entity_name": ["a"],
        "monetary_exposure_inr": "lots",
        "anomaly_metrics": {key: ["wrong", {"type": 1}] for key in CASES[0][1]["anomaly_metrics"]},
    },
    {**CASES[0][1], "anomaly_metrics": dict.fromkeys(CASES[0][1]["anomaly_metrics"], 7)},
    {**CASES[0][1], "anomaly_metrics": dict.fromkeys(CASES[0][1]["anomaly_metrics"], True)},
]


@pytest.mark.parametrize(("finding", "headline", "brief"), [case[1:] for case in CASES], ids=IDS)
def test_exact_text(finding: dict[str, Any], headline: str, brief: str) -> None:
    assert render(finding) == (headline, brief)


@pytest.mark.parametrize("finding", [case[1] for case in CASES], ids=IDS)
def test_fits_without_clipping_and_follows_house_style(finding: dict[str, Any]) -> None:
    headline, brief = render(finding)
    out = plain_brief({**finding, "methodology_code": "P6"})
    assert out == {"headline": headline, "brief": brief}
    assert len(headline) <= HEADLINE_MAX and len(brief) <= BRIEF_MAX
    assert not headline.endswith(".") and brief.endswith(".")
    for text in (headline, brief):
        assert not any(mark in text for mark in ("—", "–", ";", "!", "?", "||", "…", "  "))
    assert "P6-" not in headline + brief


@pytest.mark.parametrize("finding", [case[1] for case in CASES], ids=IDS)
def test_passes_neutrality_lint(finding: dict[str, Any]) -> None:
    headline, brief = render(finding)
    assert lint_neutrality(headline, brief, ["placeholder"]).passed


@pytest.mark.parametrize("finding", BROKEN)
def test_broken_input_never_raises_and_returns_strings(finding: Any) -> None:
    headline, brief = render(finding)
    assert isinstance(headline, str) and isinstance(brief, str)
    assert "[" not in headline + brief and "None" not in headline + brief and "True" not in headline + brief


def test_same_finding_always_gives_same_text() -> None:
    for _, finding, _, _ in CASES:
        assert render(finding) == render(dict(finding))


def _with(base: dict[str, Any], **changes: Any) -> dict[str, Any]:
    return {**base, "anomaly_metrics": {**base["anomaly_metrics"], **changes}}


def test_missing_day_count_is_left_out_not_guessed() -> None:
    headline, brief = render(_with(CASES[0][1], days_elapsed=None, norm_days=None, as_of_date=""))
    assert headline == "Lok Sabha promise on NavIC still pending"
    assert brief == (
        "The Department of Space gave this assurance in the Lok Sabha on 23 Jul 2025. "
        "No implementation statement had been laid."
    )


def test_single_extension_reads_naturally() -> None:
    _, brief = render(_with(CASES[1][1], extensions_count=1))
    assert brief.endswith("The deadline was extended once, the latest to 31 Dec 2026.")


def test_question_asker_is_not_named() -> None:
    headline, _ = render(_with(CASES[0][1], subject="Supplementary by Shri A B Example: RAIL SAFETY"))
    assert headline == "Lok Sabha promise on Rail Safety still pending after 492 days"


def test_partly_implemented_assurance() -> None:
    finding = _with(CASES[0][1], signal_category="partly_implemented_assurance", recorded_status="partly_implemented")
    finding["rule_triggered"] = "P6-PARTLY-IMPLEMENTED"
    headline, brief = render(finding)
    assert headline == "Lok Sabha promise on NavIC only partly met after 492 days"
    assert brief == (
        "The Department of Space gave this assurance in the Lok Sabha on 23 Jul 2025. "
        "Parliament records list it as partly implemented as of 27 Nov 2026. The norm is 90 days."
    )


def test_unknown_rule_falls_back_to_generic_writer() -> None:
    finding = {"rule_triggered": "P6-SOMETHING-NEW", "anomaly_metrics": {"subject": "Rail Safety"}}
    assert render(finding) == ("", "")
