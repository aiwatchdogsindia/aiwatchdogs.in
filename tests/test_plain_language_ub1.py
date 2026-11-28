"""Plain-language headlines and briefs for UB1 Union Budget findings."""

from __future__ import annotations

from typing import Any

import pytest

from aiwatchdogs.core.plain import BRIEF_MAX, HEADLINE_MAX, plain_brief
from aiwatchdogs.core.plain.ub1 import render
from aiwatchdogs.core.safety import lint_neutrality

ACTUAL_RULE = "UB1-ACTUAL-BELOW-BE>10%"
REVISED_RULE = "UB1-RE-BELOW-BE>10%"


def _finding(entity: str, year: str, rule: str, metrics: dict[str, Any]) -> dict[str, Any]:
    compare = "revised_vs_budget_estimate" if rule == REVISED_RULE else "actual_vs_budget_estimate"
    return {
        "methodology_code": "UB1",
        "entity_name": entity,
        "jurisdiction": "Union of India",
        "fiscal_year": year,
        "rule_triggered": rule,
        "anomaly_metrics": {"compare": compare, "unit": "crore", **metrics},
    }


def _case(finding: dict[str, Any], headline: str, brief: str) -> tuple[dict[str, Any], str, str]:
    return finding, headline, brief


# (finding, expected headline, expected brief), trimmed from live findings.
CASES: dict[str, tuple[dict[str, Any], str, str]] = {
    "actual_union_territory": _case(
        _finding(
            "Schemes of UT > Roads (Ministry of Home Affairs (Lakshadweep))",
            "FY2018-19",
            ACTUAL_RULE,
            {
                "budget_estimate": 15.7,
                "revised_estimate": 12.03,
                "actual": 11.22,
                "unspent": 4.48,
                "unspent_pct": 28.54,
                "actual_printed": True,
                "link": "name+figure",
            },
        ),
        "Spending on Roads in Lakshadweep was 28.5% below budget in 2018-19",
        'Union Budget papers show ₹15.7 crore was budgeted for 2018-19, revised to ₹12.03 crore, and ₹11.22 crore was spent, ₹4.48 crore below the budget. The line is in the Ministry of Home Affairs budget for Lakshadweep.',
    ),
    "actual_generic_name_keeps_department": _case(
        _finding(
            "Secretariat (Department of Space)",
            "FY2024-25",
            ACTUAL_RULE,
            {
                "budget_estimate": 161.49,
                "revised_estimate": 132.0,
                "actual": 130.35,
                "unspent": 31.14,
                "unspent_pct": 19.28,
                "actual_printed": True,
                "link": "name+figure",
            },
        ),
        "Spending on Secretariat under the Department of Space was 19.3% below budget in 2024-25",
        "Union Budget papers show ₹161 crore was budgeted for 2024-25, revised to ₹132 crore, and ₹130 crore "
        "was spent, ₹31.14 crore below the budget.",
    ),
    "actual_revised_unchanged_and_wrapped_hyphen": _case(
        _finding(
            "Tertiary Care Programs > Tobacco Control Programme and Drug De- addiction Programme "
            "(Department of Health and Family Welfare)",
            "FY2019-20",
            ACTUAL_RULE,
            {
                "budget_estimate": 65.0,
                "revised_estimate": 65.0,
                "actual": 51.58,
                "unspent": 13.42,
                "unspent_pct": 20.65,
                "actual_printed": True,
                "link": "name+figure",
            },
        ),
        'Spending on Tobacco Control Programme and Drug De-addiction Programme was 20.6% below budget',
        "Union Budget papers show ₹65 crore was budgeted for 2019-20 and ₹51.58 crore was spent, ₹13.42 crore below "
        "the budget. The line is listed under Tertiary Care Programs in the Department of Health and Family Welfare "
        "budget.",
    ),
    "actual_long_name_shortened": _case(
        _finding(
            "Setting up of virtual classrooms and massive open online courses (MOOCs) (Department of Higher Education)",
            "FY2017-18",
            ACTUAL_RULE,
            {
                "budget_estimate": 75.0,
                "revised_estimate": 90.0,
                "actual": 63.08,
                "unspent": 11.92,
                "unspent_pct": 15.89,
                "actual_printed": True,
                "link": "name+figure",
            },
        ),
        "Spending on Setting up of virtual classrooms and massive open online courses was 15.9% below budget",
        'Union Budget papers show ₹75 crore was budgeted for 2017-18, revised to ₹90 crore, and ₹63.08 crore was spent, ₹11.92 crore below the budget. The line is in the Department of Higher Education budget.',
    ),
    "actual_not_printed": _case(
        _finding(
            "Support to Public Sector Undertakings > Grants-in-aid to BSNL for payment of GST "
            "(Department of Telecommunications)",
            "FY2022-23",
            ACTUAL_RULE,
            {
                "budget_estimate": 3550.0,
                "revised_estimate": 0.0,
                "actual": 0.0,
                "unspent": 3550.0,
                "unspent_pct": 100.0,
                "actual_printed": False,
                "link": "name+figure",
            },
        ),
        'No spending shown on Grants-in-aid to BSNL for payment of GST against a ₹3,550 crore budget',
        "Union Budget papers show ₹3,550 crore was budgeted for 2022-23 and no actual spending is printed for this "
        "line. The line is listed under Support to Public Sector Undertakings in the Department of "
        "Telecommunications budget.",
    ),
    "actual_not_printed_after_revision": _case(
        _finding(
            "Champion Service Sector Scheme on Accounting and Finance Services (Ministry of Corporate Affairs)",
            "FY2020-21",
            ACTUAL_RULE,
            {
                "budget_estimate": 5.0,
                "revised_estimate": 1.0,
                "actual": 0.0,
                "unspent": 5.0,
                "unspent_pct": 100.0,
                "actual_printed": False,
                "link": "name+figure",
            },
        ),
        'No spending shown on Champion Service Sector Scheme on Accounting and Finance Services',
        "Union Budget papers show ₹5 crore was budgeted for 2020-21, later revised to ₹1 crore, and no actual "
        "spending is printed for this line. The line is in the Ministry of Corporate Affairs budget.",
    ),
    "revised_cut": _case(
        _finding(
            "National Youth Corps (Ministry of Youth Affairs and Sports)",
            "FY2015-16",
            REVISED_RULE,
            {
                "budget_estimate": 29.7,
                "revised_estimate": 19.19,
                "revised_cut": 10.51,
                "revised_cut_pct": 35.39,
                "revised_printed": True,
            },
        ),
        "2015-16 budget for National Youth Corps was revised down by 35.4%",
        "Union Budget papers show ₹29.7 crore was budgeted for 2015-16 and the estimate was later revised to "
        "₹19.19 crore, ₹10.51 crore lower. The line is in the Ministry of Youth Affairs and Sports budget.",
    ),
    "revised_almost_to_nothing": _case(
        _finding(
            "PMAY-Urban & PMAY-Urban 2.0 > Credit Risk Guarantee Fund Trust (CRGFT) "
            "(Ministry of Housing and Urban Affairs)",
            "FY2025-26",
            REVISED_RULE,
            {
                "budget_estimate": 500.0,
                "revised_estimate": 0.01,
                "revised_cut": 499.99,
                "revised_cut_pct": 100.0,
                "revised_printed": True,
            },
        ),
        '2025-26 budget for Credit Risk Guarantee Fund Trust was revised from ₹500 crore to ₹1 lakh',
        "Union Budget papers show ₹500 crore was budgeted for 2025-26 and the estimate was later revised to ₹1 lakh, "
        "₹500 crore lower. The line is in the Ministry of Housing and Urban Affairs budget.",
    ),
    "revised_not_printed": _case(
        _finding(
            "GSAT-18 Satellite (Department of Space)",
            "FY2014-15",
            REVISED_RULE,
            {
                "budget_estimate": 50.0,
                "revised_estimate": 0.0,
                "revised_cut": 50.0,
                "revised_cut_pct": 100.0,
                "revised_printed": False,
            },
        ),
        'No 2014-15 revised estimate shown for GSAT-18 Satellite against a ₹50 crore budget',
        'Union Budget papers show ₹50 crore was budgeted for 2014-15 and no revised estimate is printed for this line. Actual spending for the year is not compared here. The line is in the Department of Space budget.',
    ),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_render_exact_text(name: str) -> None:
    finding, headline, brief = CASES[name]
    assert render(finding) == (headline, brief)


@pytest.mark.parametrize("name", sorted(CASES))
def test_fixtures_fit_without_clipping_and_pass_lint(name: str) -> None:
    finding, headline, brief = CASES[name]
    assert len(headline) <= HEADLINE_MAX
    assert len(brief) <= BRIEF_MAX
    out = plain_brief(finding)
    assert out == {"headline": headline, "brief": brief}
    assert "…" not in headline + brief
    assert not headline.endswith(".")
    assert brief.endswith(".")
    assert not any(mark in headline + brief for mark in ("—", ";", "!", "?", "||", "  "))
    assert lint_neutrality(headline, brief, ["placeholder"]).passed


def test_printed_nil_actual_is_not_called_unprinted() -> None:
    finding = _finding(
        "National Coastal Mission (Ministry of Earth Sciences)",
        "FY2017-18",
        ACTUAL_RULE,
        {"budget_estimate": 10.0, "actual": 0.0, "unspent": 10.0, "unspent_pct": 100.0, "actual_printed": True},
    )
    headline, brief = render(finding)
    assert headline == "No spending recorded on National Coastal Mission in 2017-18 against a ₹10 crore budget"
    assert brief == (
        "Union Budget papers show ₹10 crore was budgeted for 2017-18 and the actual spending printed is nil. "
        "The line is in the Ministry of Earth Sciences budget."
    )


def test_rows_matched_by_name_are_said_so() -> None:
    finding = _finding(
        "Smart Grids (Ministry of Power)",
        "FY2020-21",
        ACTUAL_RULE,
        {"budget_estimate": 40.0, "actual": 16.08, "unspent": 23.92, "unspent_pct": 59.8, "link": "name"},
    )
    headline, brief = render(finding)
    assert headline == "Spending on Smart Grids under the Ministry of Power was 59.8% below budget in 2020-21"
    assert brief == (
        "Union Budget papers show ₹40 crore was budgeted for 2020-21 and ₹16.08 crore was spent, ₹23.92 crore below "
        "the budget. The rows in the two budgets were matched by name."
    )


def test_capitals_doubled_words_and_title_fallback_are_tidied() -> None:
    finding = {
        "methodology_code": "UB1",
        "title": "NATIONAL HEALTH HEALTH MISSION PROGRAMME (MINISTRY OF HEALTH AND FAMILY WELFARE): "
        "revised estimate for 2018-19 was 20.0% below the budget estimate",
        "fiscal_year": "FY2018-19",
        "rule_triggered": REVISED_RULE,
        "anomaly_metrics": {"budget_estimate": 100.0, "revised_estimate": 80.0},
    }
    headline, brief = render(finding)
    assert headline == "2018-19 budget for National Health Mission Programme was revised down by 20%"
    assert brief == (
        "Union Budget papers show ₹100 crore was budgeted for 2018-19 and the estimate was later revised to "
        "₹80 crore, ₹20 crore lower. The line is in the Ministry of Health and Family Welfare budget."
    )


def test_missing_figures_are_left_out_not_guessed() -> None:
    finding = _finding("Roads (Ministry of Power)", "", ACTUAL_RULE, {"unspent_pct": "28.5"})
    headline, brief = render(finding)
    assert headline == "Spending on Roads under the Ministry of Power was 28.5% below budget"
    assert brief == "Union Budget papers show spending was below the budget estimate."


@pytest.mark.parametrize(
    "finding",
    [
        {},
        {"anomaly_metrics": None},
        dict.fromkeys(["id", "title", "summary", "entity_name", "fiscal_year", "anomaly_metrics", "rule_triggered"]),
        {"entity_name": 123, "anomaly_metrics": "x", "fiscal_year": [1], "rule_triggered": 7},
        {"entity_name": "Roads (Ministry of Power)", "anomaly_metrics": {}},
        {"entity_name": "(((", "anomaly_metrics": {"budget_estimate": 5}},
    ],
)
def test_thin_or_broken_findings_fall_back_quietly(finding: dict[str, Any]) -> None:
    assert render(finding) == ("", "")
    out = plain_brief({"methodology_code": "UB1", **finding})
    assert isinstance(out["headline"], str) and isinstance(out["brief"], str)


@pytest.mark.parametrize(
    "finding",
    [
        _finding("Roads {x} (Ministry of {y})", "FY2018-19", ACTUAL_RULE, {"budget_estimate": "abc", "actual": []}),
        _finding("X" * 300, "2019", ACTUAL_RULE, {"budget_estimate": float("nan"), "unspent_pct": float("inf")}),
        _finding("Roads", "FY2018-19", "", {"budget_estimate": 5, "actual": "4", "actual_printed": "no"}),
        _finding("Word " * 60, "FY2018-19", REVISED_RULE, {"budget_estimate": 1e9, "revised_estimate": -3}),
        _finding("Roads (", "FY2018-19", REVISED_RULE, {"budget_estimate": True, "revised_cut_pct": 12}),
        _finding("Roads…", "FY2018-19", ACTUAL_RULE, {"budget_estimate": 0.004, "actual": 0, "unspent_pct": 100}),
    ],
)
def test_wrong_types_and_noise_never_raise(finding: dict[str, Any]) -> None:
    headline, brief = render(finding)
    assert isinstance(headline, str) and isinstance(brief, str)
    assert len(headline) <= HEADLINE_MAX
    assert len(brief) <= BRIEF_MAX
    assert "…" not in headline + brief


def test_non_dict_input_returns_empty() -> None:
    assert render(None) == ("", "")  # type: ignore[arg-type]
    assert render("finding") == ("", "")  # type: ignore[arg-type]
