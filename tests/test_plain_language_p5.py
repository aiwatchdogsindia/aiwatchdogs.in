"""Plain-language headlines and briefs for P5 single-teacher school findings."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from aiwatchdogs.core.plain import BRIEF_MAX, HEADLINE_MAX, plain_brief
from aiwatchdogs.core.plain.p5 import render
from aiwatchdogs.core.safety import lint_neutrality


def _finding(state: str, year: str, rule: str, signals: list[str], **figures: Any) -> dict[str, Any]:
    return {
        "methodology_code": "P5",
        "entity_name": f"{state} — School Education Dept (UDISE+ {year})",
        "jurisdiction": state,
        "fiscal_year": year,
        "rule_triggered": rule,
        "anomaly_metrics": {"academic_year": year, "state_name": state, "triggered_rules": signals, **figures},
    }


SHARE = "single_teacher_share_ge_10pct"
CROWDED = "students_per_single_teacher_gt_30"
GOV = "gov_primary_avg_teachers_lt_2"

OVERCROWDING = _finding(
    "Karnataka", "2024-25", "P5-SEVERE-OVERCROWDING", [CROWDED, GOV],
    total_schools=74859, single_teacher_schools=7349, single_teacher_enrolments=223142,
    single_teacher_share_pct=9.82, students_per_single_teacher=30.36,
    gov_primary_1_5_schools=20091, gov_primary_1_5_teachers=36209, gov_primary_teachers_per_school=1.8,
)
BOTH = _finding(
    "Chhattisgarh", "2024-25", "P5-CONCENTRATION-OVERCROWDING", [SHARE, CROWDED],
    total_schools=56802, single_teacher_schools=5973, single_teacher_enrolments=213237,
    single_teacher_share_pct=10.52, students_per_single_teacher=35.7,
    gov_primary_1_5_schools=30736, gov_primary_1_5_teachers=76638, gov_primary_teachers_per_school=2.49,
)
SHARE_AND_STAFFING = _finding(
    "Himachal Pradesh", "2024-25", "P5-CONCENTRATION-OVERCROWDING", [SHARE, GOV],
    total_schools=17330, single_teacher_schools=2964, single_teacher_enrolments=46329,
    single_teacher_share_pct=17.1, students_per_single_teacher=15.63,
    gov_primary_1_5_schools=9943, gov_primary_1_5_teachers=19620, gov_primary_teachers_per_school=1.97,
)
CONCENTRATION = _finding(
    "Telangana", "2025-26", "P5-HIGH-CONCENTRATION", [SHARE],
    total_schools=41762, single_teacher_schools=4793, single_teacher_enrolments=64057,
    single_teacher_share_pct=11.48, students_per_single_teacher=13.36,
    gov_primary_1_5_schools=17837, gov_primary_1_5_teachers=43867, gov_primary_teachers_per_school=2.46,
)
GOV_PRIMARY = _finding(
    "Rajasthan", "2025-26", "P5-GOV-PRIMARY-UNDERSTAFFED", [GOV],
    total_schools=106445, single_teacher_schools=7200, single_teacher_enrolments=178531,
    single_teacher_share_pct=6.76, students_per_single_teacher=24.8,
    gov_primary_1_5_schools=31822, gov_primary_1_5_teachers=63341, gov_primary_teachers_per_school=1.99,
)

CASES: list[tuple[dict[str, Any], str, str]] = [
    (
        OVERCROWDING,
        "Karnataka's 7,349 single-teacher schools averaged 30.4 students each in 2024-25",
        ("Official UDISE+ 2024-25 school data show 7,349 of Karnataka's 74,859 schools (9.8%) had one teacher. "
        "They enrolled 2,23,142 students, about 30.4 per school. Government primary schools averaged 1.8 teachers each."),
    ),
    (
        BOTH,
        "Chhattisgarh had 5,973 single-teacher schools in 2024-25, averaging 35.7 students each",
        ("Official UDISE+ 2024-25 school data show 5,973 of Chhattisgarh's 56,802 schools (10.5%) had one teacher. "
        "They enrolled 2,13,237 students, about 35.7 per school."),
    ),
    (
        SHARE_AND_STAFFING,
        "Himachal Pradesh had 2,964 single-teacher schools in 2024-25, 17.1% of all its schools",
        ("Official UDISE+ 2024-25 school data show 2,964 of Himachal Pradesh's 17,330 schools (17.1%) had one teacher. "
        "They enrolled 46,329 students, about 15.6 per school. Government primary schools averaged 1.97 teachers each."),
    ),
    (
        CONCENTRATION,
        "Telangana had 4,793 single-teacher schools in 2025-26, 11.5% of all its schools",
        ("Official UDISE+ 2025-26 school data show 4,793 of Telangana's 41,762 schools (11.5%) had one teacher. "
        "They enrolled 64,057 students, about 13.4 per school."),
    ),
    (
        GOV_PRIMARY,
        "Rajasthan government primary schools averaged 1.99 teachers each in 2025-26",
        ("Official UDISE+ 2025-26 school data show 63,341 teachers across 31,822 government primary schools in Rajasthan. "
        "Across all schools, 7,200 of 1,06,445 had one teacher."),
    ),
]

JUNK = ("None", "nan", "inf", "True", "False", "[]", "{}", "()", "  ", " ,", " .", "—", ";", "!", "?", " 0 ", "0%", "'s's")


@pytest.mark.parametrize(("finding", "headline", "brief"), CASES)
def test_exact_text(finding: dict[str, Any], headline: str, brief: str) -> None:
    assert render(finding) == (headline, brief)


@pytest.mark.parametrize(("finding", "headline", "brief"), CASES)
def test_fits_without_clipping_and_is_neutral(finding: dict[str, Any], headline: str, brief: str) -> None:
    out = plain_brief(finding)
    assert out == {"headline": headline, "brief": brief}
    assert len(headline) <= 95 and len(brief) <= 220
    assert len(headline) <= HEADLINE_MAX and len(brief) <= BRIEF_MAX
    assert not headline.endswith((".", "…")) and brief.endswith(".")
    for mark in ("—", ";", "!", "?", "P5-", "_", "School Education Dept"):
        assert mark not in headline and mark not in brief
    assert lint_neutrality(headline, brief, ["placeholder"]).passed


def test_missing_counts_are_left_out_not_shown_as_zero() -> None:
    finding = _finding(
        "Goa", "2023-24", "P5-HIGH-CONCENTRATION", [SHARE],
        single_teacher_schools=238, single_teacher_share_pct=16.0, total_schools=None,
        single_teacher_enrolments=0, students_per_single_teacher=None,
    )
    assert render(finding) == (
        "Goa had 238 single-teacher schools in 2023-24, 16% of all its schools",
        "Official UDISE+ 2023-24 school data show Goa had 238 single-teacher schools.",
    )


def test_rule_code_is_used_when_the_signal_list_is_missing() -> None:
    finding = {
        "jurisdiction": "State:Odisha",
        "fiscal_year": "2024-25",
        "rule_triggered": "P5-SEVERE-OVERCROWDING",
        "anomaly_metrics": {"single_teacher_schools": "1089", "students_per_single_teacher": 44.02},
    }
    assert render(finding) == (
        "Odisha's 1,089 single-teacher schools averaged 44 students each in 2024-25",
        "Official UDISE+ 2024-25 school data show Odisha had 1,089 single-teacher schools. They averaged 44 students per school.",
    )


def test_single_teacher_counts_are_never_called_government_schools() -> None:
    # Table 2.2 counts schools of every management. Only the gov_primary_* fields are government-only.
    for finding in (OVERCROWDING, BOTH, SHARE_AND_STAFFING, CONCENTRATION):
        headline, brief = render(finding)
        assert "government" not in headline.lower()
        assert "government" not in brief.lower().split("government primary schools averaged")[0]


def test_long_union_territory_name_drops_the_year_rather_than_being_clipped() -> None:
    name = "Dadra and Nagar Haveli and Daman and Diu"
    finding = _finding(
        name, "2024-25", "P5-CONCENTRATION-OVERCROWDING", [SHARE, CROWDED],
        total_schools=460, single_teacher_schools=120, single_teacher_enrolments=4284,
        single_teacher_share_pct=26.09, students_per_single_teacher=35.7,
    )
    headline, brief = render(finding)
    assert headline == f"{name} had 120 single-teacher schools, 35.7 students each"
    assert len(headline) <= 95
    assert brief == (
        f"Official UDISE+ 2024-25 school data show 120 of {name}'s 460 schools (26.1%) had one teacher. "
        "They enrolled 4,284 students, about 35.7 per school."
    )


def test_name_ending_in_s_and_national_row() -> None:
    islands = _finding(
        "Andaman and Nicobar Islands", "2024-25", "P5-SEVERE-OVERCROWDING", [CROWDED],
        total_schools=5000, single_teacher_schools=600, single_teacher_enrolments=19200,
        single_teacher_share_pct=12.0, students_per_single_teacher=32.0,
    )
    assert render(islands)[0] == "Andaman and Nicobar Islands' 600 single-teacher schools averaged 32 students each in 2024-25"
    india = _finding(
        "India", "2024-25", "P5-GOV-PRIMARY-UNDERSTAFFED", [GOV],
        single_teacher_schools=104125, gov_primary_teachers_per_school=1.95,
    )
    headline, brief = render(india)
    assert headline == "India government primary schools averaged 1.95 teachers each in 2024-25"
    # The national row and Union Territories are not states.
    assert "state" not in brief.lower()
    assert brief == (
        "Official UDISE+ 2024-25 school data show government primary schools in India averaged 1.95 teachers each. "
        "Across all schools, 1,04,125 had one teacher."
    )


def test_counts_that_contradict_the_stored_ratio_are_dropped() -> None:
    finding = copy.deepcopy(OVERCROWDING)
    finding["anomaly_metrics"]["single_teacher_enrolments"] = 12
    finding["anomaly_metrics"]["total_schools"] = 8000
    headline, brief = render(finding)
    assert headline == "Karnataka's 7,349 single-teacher schools averaged 30.4 students each in 2024-25"
    assert "enrolled 12" not in brief and "8,000" not in brief
    assert brief == (
        "Official UDISE+ 2024-25 school data show Karnataka had 7,349 single-teacher schools, 9.8% of the total. "
        "They averaged 30.4 students per school. Government primary schools averaged 1.8 teachers each."
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("single_teacher_share_pct", 0.0),
        ("single_teacher_share_pct", 140.0),
        ("students_per_single_teacher", 0.0),
        ("students_per_single_teacher", -3),
        ("gov_primary_teachers_per_school", 0),
        ("single_teacher_schools", float("nan")),
        ("total_schools", 1e18),
    ],
)
def test_zero_and_impossible_values_are_treated_as_unknown(field: str, value: float) -> None:
    finding = copy.deepcopy(OVERCROWDING)
    finding["anomaly_metrics"][field] = value
    headline, brief = render(finding)
    assert headline and brief
    for junk in JUNK:
        assert junk not in headline and junk not in brief, (headline, brief)
    assert "140" not in brief and "-3" not in f"{headline} {brief}"


@pytest.mark.parametrize("name", [None, "", [], {}, 7, True, float("nan"), "None", "nan", "x" * 500, "12345", "<b>Goa</b>"])
def test_unusable_state_name_falls_back(name: Any) -> None:
    finding = copy.deepcopy(CONCENTRATION)
    finding["anomaly_metrics"]["state_name"] = name
    finding["jurisdiction"] = name
    finding["entity_name"] = name
    assert render(finding) == ("", "")


def test_state_name_is_taken_from_the_next_usable_field() -> None:
    finding = copy.deepcopy(CONCENTRATION)
    finding["anomaly_metrics"]["state_name"] = None
    finding["jurisdiction"] = "State:TELANGANA"
    assert render(finding)[0] == "Telangana had 4,793 single-teacher schools in 2025-26, 11.5% of all its schools"
    finding["jurisdiction"] = ["bad"]
    assert render(finding)[0] == "Telangana had 4,793 single-teacher schools in 2025-26, 11.5% of all its schools"


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
        {"anomaly_metrics": None, "jurisdiction": None, "entity_name": None, "fiscal_year": None},
        {"jurisdiction": 7, "entity_name": ["x"], "fiscal_year": 2024, "rule_triggered": {},
         "anomaly_metrics": {"state_name": 3, "triggered_rules": {}, "single_teacher_schools": "abc", "total_schools": [1]}},
        {"jurisdiction": "Bihar", "anomaly_metrics": {"triggered_rules": [None, 4, SHARE]}},
        {"jurisdiction": "Bihar", "anomaly_metrics": "not a dict"},
        None,
        "a string",
        [],
    ],
)
def test_never_raises_on_bad_input(finding: Any) -> None:
    headline, brief = render(finding)
    assert isinstance(headline, str) and isinstance(brief, str)
    if isinstance(finding, dict):
        out = plain_brief({**finding, "methodology_code": "P5"})
        assert isinstance(out["headline"], str) and out["headline"]
