"""Plain-language headlines and briefs for P1 police budget findings."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from aiwatchdogs.core.plain import BRIEF_MAX, HEADLINE_MAX, plain_brief
from aiwatchdogs.core.plain.p1 import render
from aiwatchdogs.core.safety import lint_neutrality


def _finding(
    entity: str,
    jurisdiction: str,
    year: str,
    rule: str,
    exposure: float,
    metrics: dict[str, Any],
    summary: str,
) -> dict[str, Any]:
    return {
        "methodology_code": "P1",
        "entity_name": entity,
        "jurisdiction": jurisdiction,
        "fiscal_year": year,
        "monetary_exposure_inr": exposure,
        "anomaly_metrics": metrics,
        "rule_triggered": rule,
        "summary": summary,
    }


RE_VS_BE_CRORE = _finding(
    "Sashastra Seema Bal",
    "Union of India (Sashastra Seema Bal)",
    "FY2023-24",
    "P1-RE-VS-BE>10%",
    487100000.0,
    {"line_key": "ssb-capital", "compare": "re_vs_be", "re": 133.0, "be": 181.71, "unit": "crore", "pct_difference": -26.81},
    "The official budget document prints 133.00 crore for RE FY2023-24 against 181.71 crore for BE FY2023-24 "
    "on Demand 51 line 1.07, capital, a difference of -26.8% (threshold 10%).",
)
RE_VS_BE_LAKH = _finding(
    "Tripura Police",
    "State:Tripura",
    "FY2025-26",
    "P1-RE-VS-BE>10%",
    2395041999.9999986,
    {"line_key": "tripura-police-2055", "compare": "re_vs_be", "re": 247715.33, "be": 223764.91, "unit": "lakh", "pct_difference": 10.7},
    "The official budget document prints 247,715.33 lakh for RE FY2025-26 against 223,764.91 lakh for BE FY2025-26 "
    "on Demand No. 10 Home (Police), Major Head 2055 Police, a difference of +10.7% (threshold 10%).",
)
BE_NEXT_VS_RE_LAKH = _finding(
    "Tripura Police",
    "State:Tripura",
    "FY2026-27",
    "P1-BE_NEXT-VS-RE>15%",
    161435000.0,
    {"line_key": "tripura-police-4055", "compare": "be_next_vs_re", "be_next": 3519.13, "re": 1904.78, "unit": "lakh", "pct_difference": 84.75},
    "The official budget document prints 3,519.13 lakh for BE FY2026-27 against 1,904.78 lakh for RE FY2025-26 "
    "on Demand No. 10 Home (Police), Major Head 4055 Capital Outlay on Police, a difference of +84.8% (threshold 15%).",
)
BE_NEXT_VS_RE_LONG_NAME = _finding(
    "Modernisation Plan IV for Central Armed Police Forces",
    "Union of India (Modernisation Plan IV for Central Armed Police Forces)",
    "FY2026-27",
    "P1-BE_NEXT-VS-RE>15%",
    3112100000.0,
    {"line_key": "capf-modernisation-capital", "compare": "be_next_vs_re", "be_next": 252.18, "re": 563.39, "unit": "crore", "pct_difference": -55.24},
    "The official budget document prints 252.18 crore for BE FY2026-27 against 563.39 crore for RE FY2025-26 "
    "on Demand 51 line 16, capital, a difference of -55.2% (threshold 15%).",
)
BE_VS_ACTUAL = _finding(
    "Gujarat Police",
    "State:Gujarat",
    "FY2025-26",
    "P1-BE-VS-ACTUAL>15%",
    9778604000.0,
    {"line_key": "gujarat-police-4055", "compare": "be_vs_actual", "be": 1490.25, "actual": 512.3896, "unit": "crore", "pct_difference": 190.84},
    "The official budget document prints 1,490.25 crore for BE FY2025-26 against 512.39 crore for Actuals FY2024-25 "
    "on Home Department, Summary by Major Heads, row '4055 Capital outlay on Police', a difference of +190.8% (threshold 15%).",
)
ACTUAL_VS_BE = _finding(
    "Police Infrastructure scheme (Union)",
    "Union of India (Police Infrastructure scheme (Union))",
    "FY2024-25",
    "P1-ACTUAL-VS-BE_OF_ACTUAL_YEAR>10%",
    10193000000.0,
    {
        "line_key": "police-infrastructure-total",
        "compare": "actual_vs_be_of_actual_year",
        "actual": 2132.78,
        "be_of_actual_year": 3152.08,
        "unit": "crore",
        "pct_difference": -32.34,
    },
    "The official budget document prints 2,132.78 crore for Actuals FY2024-25 against 3,152.08 crore for BE FY2024-25 "
    "on Demand 51, Police Infrastructure, total, revenue + capital, a difference of -32.3% (threshold 10%).",
)
BE_NEXT_VS_BE = _finding(
    "Meghalaya Police",
    "State:Meghalaya",
    "FY2026-27",
    "P1-BE_NEXT-VS-BE>15%",
    79666000.0,
    {"line_key": "meghalaya-police-4055", "compare": "be_next_vs_be", "be_next": 457370.0, "be": 377704.0, "unit": "thousand", "pct_difference": 21.09},
    "The official budget document prints 457,370 thousand for BE FY2026-27 against 377,704 thousand for BE FY2025-26 "
    "on Annual Financial Statement, Statement D, Major Head 4055-Capital Outlay on Police, a difference of +21.1% (threshold 15%).",
)
RE_EQUALS_BE = _finding(
    "Lakshadweep Police",
    "Union of India (Lakshadweep Police)",
    "FY2025-26",
    "P1-RE-EQUALS-BE",
    0.0,
    {"line_key": "lakshadweep-police-2055", "be": 794400.0, "re": 794400.0, "unit": "thousand"},
    "The official budget document prints the same figure, 794,400 thousand, as both BE and RE for FY2025-26 "
    "on Union Demand No. 56, Major Head 2055 Police, total.",
)

CASES: list[tuple[dict[str, Any], str, str]] = [
    (
        RE_VS_BE_CRORE,
        "Sashastra Seema Bal capital budget for 2023-24 revised down 26.8%",
        ("Union Budget papers show the 2023-24 capital budget for Sashastra Seema Bal was first set at ₹182 crore "
        "and later revised to ₹133 crore, a fall of ₹48.71 crore."),
    ),
    (
        RE_VS_BE_LAKH,
        "Tripura Police revenue budget for 2025-26 revised up 10.7%",
        ("Tripura budget papers show the 2025-26 revenue budget for Tripura Police was revised up by 10.7% "
        "from the budget estimate, a rise of ₹240 crore."),
    ),
    (
        BE_NEXT_VS_RE_LAKH,
        "Tripura Police capital budget for 2026-27 set 84.8% above revised 2025-26 estimate",
        ("Tripura budget papers set the 2026-27 capital budget for Tripura Police 84.8% above the revised estimate "
        "for 2025-26, a difference of ₹16.14 crore."),
    ),
    (
        BE_NEXT_VS_RE_LONG_NAME,
        "Modernisation Plan IV for CAPFs: 2026-27 capital budget 55.2% below revised 2025-26 estimate",
        ("Union Budget papers set the 2026-27 capital budget for Modernisation Plan IV for Central Armed Police Forces "
        "at ₹252 crore. The revised estimate for 2025-26 was ₹563 crore, a difference of ₹311 crore."),
    ),
    (
        BE_VS_ACTUAL,
        "Gujarat Police capital budget for 2025-26 is 191% above actual 2024-25 spending",
        ("Gujarat budget papers set the 2025-26 capital budget for Gujarat Police at ₹1,490 crore. "
        "Actual spending in 2024-25 was ₹512 crore, a difference of ₹978 crore."),
    ),
    (
        ACTUAL_VS_BE,
        "Police Infrastructure scheme spending in 2024-25 was 32.3% below budget",
        ("Union Budget papers show a budget of ₹3,152 crore for the Police Infrastructure scheme in 2024-25. "
        "Actual spending was ₹2,133 crore, which is ₹1,019 crore less."),
    ),
    (
        BE_NEXT_VS_BE,
        "Meghalaya Police capital budget for 2026-27 set 21.1% above the 2025-26 budget",
        ("Meghalaya budget papers set the 2026-27 capital budget for Meghalaya Police 21.1% above the budget estimate "
        "for 2025-26, a difference of ₹7.97 crore."),
    ),
    (
        RE_EQUALS_BE,
        "Lakshadweep Police revenue budget for 2025-26 unchanged at the revised estimate stage",
        ("Union Budget papers print the same figure for the 2025-26 revenue budget of Lakshadweep Police "
        "as both the budget estimate and the revised estimate."),
    ),
]

JUNK = ("None", "nan", "inf", "True", "False", "[]", "{}", "₹0", "₹-", "₹ crore", "()", "  ", " ,", " .", "—", ";", "!", "?")


@pytest.mark.parametrize(("finding", "headline", "brief"), CASES)
def test_exact_text(finding: dict[str, Any], headline: str, brief: str) -> None:
    assert render(finding) == (headline, brief)


@pytest.mark.parametrize(("finding", "headline", "brief"), CASES)
def test_fits_without_clipping_and_is_neutral(finding: dict[str, Any], headline: str, brief: str) -> None:
    out = plain_brief(finding)
    assert out == {"headline": headline, "brief": brief}
    assert len(headline) <= 100 and len(brief) <= 220
    assert len(headline) <= HEADLINE_MAX and len(brief) <= BRIEF_MAX
    assert not headline.endswith((".", "…")) and brief.endswith(".")
    for mark in ("—", ";", "!", "?", "||", "P1-", "Demand", "threshold"):
        assert mark not in headline and mark not in brief
    assert lint_neutrality(headline, brief, ["placeholder"]).passed


def _pair(
    entity: str,
    key: str,
    compare: str,
    a: float,
    b: float,
    pct: float,
    years: tuple[str, str],
    unit: str = "crore",
    label: str = "Demand 51 line 1, capital",
) -> dict[str, Any]:
    a_name, b_name = compare.split("_vs_")
    labels = {"be": "BE", "be_next": "BE", "re": "RE", "actual": "Actuals", "be_of_actual_year": "BE"}
    return _finding(
        entity,
        "Union of India",
        f"FY{years[0]}",
        "P1-X",
        abs(a - b) * {"crore": 1e7, "lakh": 1e5, "thousand": 1e3}[unit],
        {"line_key": key, "compare": compare, a_name: a, b_name: b, "unit": unit, "pct_difference": pct},
        f"The official budget document prints {a:,.2f} {unit} for {labels[a_name]} FY{years[0]} against {b:,.2f} {unit} "
        f"for {labels[b_name]} FY{years[1]} on {label}, a difference of {pct:+.1f}% (threshold 10%).",
    )


@pytest.mark.parametrize(
    ("finding", "expected"),
    [
        # The stored value is rounded to two places. Rounding 72.35 again gives 72.3, but the exact
        # figure is 72.352 and the stored title says 72.4.
        (_pair("Indo-Tibetan Border Police", "itbp-capital", "be_next_vs_re", 174.61, 101.31, 72.35, ("2023-24", "2022-23")), "72.4%"),
        (_pair("Sashastra Seema Bal", "ssb-capital", "re_vs_be", 133.41, 182.12, -26.75, ("2023-24", "2023-24")), "26.7%"),
        (_pair("Criminology and forensic science", "forensic-science-total", "be_next_vs_re", 95.35, 78.77, 21.05, ("2025-26", "2024-25")), " 21% "),
        (_pair("Central Police Organisations", "cpo-capital", "re_vs_be", 99.58, 181.55, -45.15, ("2024-25", "2024-25")), "45.2%"),
    ],
)
def test_percentage_matches_the_exact_figures_not_a_twice_rounded_one(finding: dict[str, Any], expected: str) -> None:
    headline, _ = render(finding)
    assert expected in f"{headline} "


@pytest.mark.parametrize(
    ("compare", "a", "b", "pct", "years", "headline"),
    [
        ("be_vs_actual", 158.91, 82.33, 93.02, ("2024-25", "2023-24"),
         "Modernisation Plan IV for CAPFs: 2024-25 capital budget 93% above actual 2023-24 spending"),
        ("be_next_vs_re", 290.01, 95.18, 204.7, ("2025-26", "2024-25"),
         "Modernisation Plan IV for CAPFs: 2025-26 capital budget 205% above revised 2024-25 estimate"),
        ("re_vs_be", 95.18, 158.91, -40.1, ("2024-25", "2024-25"),
         "Modernisation Plan IV for CAPFs: 2024-25 capital budget revised down 40.1%"),
        ("actual_vs_be_of_actual_year", 82.33, 170.55, -51.73, ("2023-24", "2023-24"),
         "Modernisation Plan IV for CAPFs: 2023-24 capital spending 51.7% below budget"),
    ],
)
def test_long_name_headline_still_says_what_the_figure_is_compared_with(
    compare: str, a: float, b: float, pct: float, years: tuple[str, str], headline: str
) -> None:
    # A bare "capital budget up 93%" reads as a rise over last year's budget, which the record does not say.
    finding = _pair("Modernisation Plan IV for Central Armed Police Forces", "capf-modernisation-capital", compare, a, b, pct, years)
    got, brief = render(finding)
    assert got == headline
    assert "Modernisation Plan IV for Central Armed Police Forces" in brief


def test_abbreviation_is_used_only_when_the_line_key_carries_it() -> None:
    finding = _pair("Modernisation Plan IV for Central Armed Police Forces", "modernisation-capital", "be_next_vs_re", 252.18, 563.39, -55.24, ("2026-27", "2025-26"))
    headline, _ = render(finding)
    assert "CAPF" not in headline
    assert "below revised estimate" in headline and len(headline) <= HEADLINE_MAX


MUMBAI = "Mumbai Police (Commissionerate of Police, Greater Bombay)"
MUMBAI_LABEL = "MH 2055-108 sub-head (1) Commissionerate of Police, Greater Bombay"
DNH = "Dadra and Nagar Haveli and Daman and Diu Police"
DNH_LABEL = "Union Demand No. 54, Major Head 4055 Capital Outlay on Police, total"


@pytest.mark.parametrize(
    ("entity", "key", "label", "compare", "a", "b", "pct", "years", "headline"),
    [
        (MUMBAI, "mumbai-commissionerate", MUMBAI_LABEL, "re_vs_be", 519425.0, 627819.0, -17.27, ("2025-26", "2025-26"),
         "Mumbai Police (Commissionerate of Police) revenue budget for 2025-26 revised down 17.3%"),
        (MUMBAI, "mumbai-commissionerate", MUMBAI_LABEL, "be_next_vs_re", 626844.0, 519425.0, 20.68, ("2026-27", "2025-26"),
         "Mumbai Police (Commissionerate of Police): 2026-27 revenue budget 20.7% above revised estimate"),
        (DNH, "dnh-dd-police-4055", DNH_LABEL, "be_vs_actual", 203700.0, 358468.0, -43.17, ("2025-26", "2024-25"),
         "Dadra and Nagar Haveli and Daman and Diu Police: 2025-26 capital budget 43.2% below 2024-25 spending"),
    ],
)
def test_other_long_names(
    entity: str, key: str, label: str, compare: str, a: float, b: float, pct: float, years: tuple[str, str], headline: str
) -> None:
    got, brief = render(_pair(entity, key, compare, a, b, pct, years, unit="thousand", label=label))
    assert got == headline and len(got) <= 100
    assert entity in brief and len(brief) <= 220


def test_rounded_figures_never_contradict_the_printed_difference() -> None:
    finding = _finding(
        "Gujarat Police",
        "State:Gujarat",
        "FY2025-26",
        "P1-RE-VS-BE>10%",
        2776800000.0000005,
        {"line_key": "gujarat-police-4055", "compare": "re_vs_be", "re": 1212.57, "be": 1490.25, "unit": "crore", "pct_difference": -18.63},
        "",
    )
    # 1,490 minus 1,213 is 277 but the exact gap rounds to 278, so the difference is left out.
    assert render(finding) == (
        "Gujarat Police capital budget for 2025-26 revised down 18.6%",
        ("Gujarat budget papers show the 2025-26 capital budget for Gujarat Police was first set at ₹1,490 crore "
        "and later revised to ₹1,213 crore."),
    )


def test_missing_year_unit_and_exposure_still_reads() -> None:
    finding = {"entity_name": "X Police", "anomaly_metrics": {"compare": "re_vs_be", "re": 1, "be": 2}}
    assert render(finding) == (
        "X Police budget revised down 50%",
        "Budget papers show the budget for X Police was revised down by 50% from the budget estimate.",
    )


def test_comparison_year_missing_from_summary_is_not_guessed() -> None:
    finding = copy.deepcopy(BE_VS_ACTUAL)
    finding["summary"] = ""
    assert render(finding) == (
        "Gujarat Police capital budget for 2025-26 is 191% above actual spending",
        ("Gujarat budget papers set the 2025-26 capital budget for Gujarat Police at ₹1,490 crore. "
        "Actual spending in the earlier year was ₹512 crore, a difference of ₹978 crore."),
    )


def test_year_is_read_from_the_summary_when_the_field_is_empty() -> None:
    finding = copy.deepcopy(BE_NEXT_VS_RE_LAKH)
    finding["fiscal_year"] = "Unspecified"
    headline, _ = render(finding)
    assert headline == "Tripura Police capital budget for 2026-27 set 84.8% above revised 2025-26 estimate"


@pytest.mark.parametrize(
    ("field", "value"),
    [("pct_difference", -5.0), ("pct_difference", 12.0), ("be_next", 0), ("re", 1.0)],
)
def test_figures_that_contradict_the_stored_percentage_are_not_repeated(field: str, value: float) -> None:
    finding = copy.deepcopy(BE_NEXT_VS_RE_LONG_NAME)
    finding["anomaly_metrics"][field] = value
    assert render(finding) == ("", "")


def test_equal_rule_with_unequal_figures_is_not_called_unchanged() -> None:
    finding = copy.deepcopy(RE_EQUALS_BE)
    finding["anomaly_metrics"]["re"] = 800000.0
    assert render(finding) == ("", "")


def test_equal_rule_gives_the_figure_only_when_printed_in_crore() -> None:
    finding = copy.deepcopy(RE_EQUALS_BE)
    finding["anomaly_metrics"].update({"be": 79.44, "re": 79.44, "unit": "crore"})
    assert render(finding)[1].endswith("as both the budget estimate and the revised estimate. Both stand at ₹79.44 crore.")


def test_nil_figure_is_never_printed_as_money() -> None:
    finding = _pair("Goa Police", "goa-police-4055", "be_next_vs_re", 0.0, 52.5, -100.0, ("2026-27", "2025-26"))
    headline, brief = render(finding)
    assert headline == "Goa Police capital budget for 2026-27 set 100% below revised 2025-26 estimate"
    assert "₹0" not in brief and "₹52.5 crore" in brief
    assert brief == (
        "Union Budget papers set the 2026-27 capital budget for Goa Police 100% below the revised estimate "
        "for 2025-26, a difference of ₹52.5 crore."
    )


@pytest.mark.parametrize(
    ("jurisdiction", "papers"),
    [
        ("State:Maharashtra (Greater Mumbai)", "Maharashtra budget papers"),
        ("Union of India (Delhi Police)", "Union Budget papers"),
        ("State:PUDUCHERRY", "Puducherry budget papers"),
        # The engine writes a bare "India" when a task names no jurisdiction, which is not the Union Budget.
        ("India", "Budget papers"),
        ("", "Budget papers"),
        (None, "Budget papers"),
        (["Goa"], "Budget papers"),
        ("x" * 300, "Budget papers"),
    ],
)
def test_source_papers(jurisdiction: Any, papers: str) -> None:
    finding = copy.deepcopy(RE_VS_BE_LAKH)
    finding["jurisdiction"] = jurisdiction
    assert render(finding)[1].startswith(f"{papers} show the 2025-26 revenue budget")


@pytest.mark.parametrize(
    ("key", "label", "kind"),
    [
        ("delhi-police-capital", "Demand 51 line 5, capital", "capital budget"),
        ("delhi-police-total", "Demand 51 line 5, revenue + capital", "Police budget"),
        ("goa-police-2055", "Demand No. 17, Major Head 2055 Police", "revenue budget"),
        ("hyderabad-capital-4055", "Commissioner of City Police capital outlay, MH 4055", "capital budget"),
        ("mumbai-commissionerate", "MH 2055-108 sub-head (1) Commissionerate of Police, Greater Bombay", "revenue budget"),
        ("x-police", "Some other line", "Police budget"),
    ],
)
def test_capital_revenue_or_combined(key: str, label: str, kind: str) -> None:
    finding = copy.deepcopy(RE_VS_BE_CRORE)
    finding["entity_name"] = "Delhi Police"
    finding["anomaly_metrics"]["line_key"] = key
    finding["summary"] = finding["summary"].replace("Demand 51 line 1.07, capital", label)
    assert f"Delhi {kind} for 2023-24" in render(finding)[0].replace("Delhi Police capital", "Delhi capital").replace(
        "Delhi Police revenue", "Delhi revenue"
    )


@pytest.mark.parametrize("name", [None, "", "   ", [], {}, ["Goa Police"], 5, float("nan"), True, "None", "nan", "x" * 500, "12345"])
def test_unusable_entity_name_falls_back(name: Any) -> None:
    finding = copy.deepcopy(RE_VS_BE_CRORE)
    finding["entity_name"] = name
    assert render(finding) == ("", "")


def test_all_caps_name_is_title_cased_and_union_note_dropped() -> None:
    finding = copy.deepcopy(RE_VS_BE_CRORE)
    finding["entity_name"] = "CRIMINOLOGY AND FORENSIC SCIENCE (Union)"
    assert render(finding)[0] == "Criminology and Forensic Science capital budget for 2023-24 revised down 26.8%"


BAD_VALUES: list[Any] = [None, "", "  ", [], {}, ["x"], {"a": 1}, float("nan"), float("inf"), -5, 0, "abc", True, "N" * 500, "None", 1e18, "12"]


@pytest.mark.parametrize("base", [case[0] for case in CASES])
def test_hostile_field_values_never_raise_or_print_junk(base: dict[str, Any]) -> None:
    targets = [("top", k) for k in base] + [("metrics", k) for k in base["anomaly_metrics"]]
    for level, key in targets:
        if key == "methodology_code":
            continue
        for bad in [*BAD_VALUES, "__missing__"]:
            finding = copy.deepcopy(base)
            holder = finding if level == "top" else finding["anomaly_metrics"]
            if bad == "__missing__":
                holder.pop(key, None)
            else:
                holder[key] = bad
            headline, brief = render(finding)
            assert isinstance(headline, str) and isinstance(brief, str)
            assert bool(headline) == bool(brief)
            assert len(headline) <= HEADLINE_MAX and len(brief) <= BRIEF_MAX, (key, bad, headline, brief)
            for junk in JUNK:
                assert junk not in headline and junk not in brief, (key, bad, headline, brief)
            if headline:
                assert not headline.endswith(".") and brief.endswith(".")
                assert lint_neutrality(headline, brief, ["placeholder"]).passed


@pytest.mark.parametrize(
    "finding",
    [
        {},
        {"anomaly_metrics": None, "entity_name": None, "summary": None, "fiscal_year": None, "rule_triggered": None},
        {"entity_name": ["a"], "jurisdiction": 4, "fiscal_year": 2024, "rule_triggered": {}, "summary": 7,
         "monetary_exposure_inr": "abc", "anomaly_metrics": {"compare": 5, "be": "x", "re": [], "unit": 3}},
        {"entity_name": "Goa Police", "anomaly_metrics": {"compare": "re_vs_be", "re": None, "be": None, "pct_difference": "n/a"}},
        {"entity_name": "Goa Police", "anomaly_metrics": {"compare": "re_vs_be", "re": 5, "be": 0, "unit": "crore"}},
        {"entity_name": "Goa Police", "anomaly_metrics": {"compare": "odd_vs_other", "pct_difference": 12.0}},
        {"entity_name": "Goa Police", "anomaly_metrics": {"compare": "unit_vs_line_key", "unit": "crore", "line_key": "x", "pct_difference": 12.0}},
        {"entity_name": "Goa Police", "anomaly_metrics": {"compare": "re_vs_be", "re": -5, "be": 10, "unit": "crore"}},
        {"entity_name": "Goa Police", "anomaly_metrics": {"compare": "re_vs_be", "pct_difference": 1e18}},
        {"entity_name": "Goa Police", "rule_triggered": "P1-RE-EQUALS-BE", "anomaly_metrics": "not a dict"},
        None,
        "a string",
        [],
    ],
)
def test_never_raises_on_bad_input(finding: Any) -> None:
    headline, brief = render(finding)
    assert isinstance(headline, str) and isinstance(brief, str)
    for junk in JUNK:
        assert junk not in headline and junk not in brief
    if isinstance(finding, dict):
        out = plain_brief({**finding, "methodology_code": "P1"})
        assert isinstance(out["headline"], str) and out["headline"]
