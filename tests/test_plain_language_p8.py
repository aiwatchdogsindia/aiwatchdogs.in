"""Plain-language writer for P8: exact text for real findings, and behaviour on broken input."""

from __future__ import annotations

from typing import Any

import pytest

from aiwatchdogs.core.plain import BRIEF_MAX, HEADLINE_MAX, plain_brief
from aiwatchdogs.core.plain.p8 import render
from aiwatchdogs.core.safety import lint_neutrality

# Trimmed from live findings: only the fields the writer reads.
CASES: list[tuple[str, dict[str, Any], str, str]] = [
    (
        'cag_with_figure',
        {'rule_triggered': 'P8-PREMATURE-DISCONTINUATION',
         'monetary_exposure_inr': 20100000.0,
         'anomaly_metrics': {'scheme_name': 'Madhya Pradesh CAG Audit — Compliance & Sector Audit',
                             'tranche_or_phase': 'CAG Report ID 122250 Finding #1: Compliance & Sector Audit',
                             'promised_metric': 'Full sanctioned scheme outlay, target coverage, and timely '
                                                'statutory delivery',
                             'revised_metric': 'CAG finding: ₹2.01 crore',
                             'metric_unit': 'CAG audited outlay / delivery variance',
                             'state_or_ut': 'Madhya Pradesh',
                             'walkback_document_ref': 'CAG Audit Report ID 122250 (Madhya Pradesh) — Executive '
                                                      'Summary / Audit Findings',
                             'walkback_summary': 'CAG Audit Finding (Report ID 122250, p.20): This resulted in '
                                                 'excess payment of ₹ 2.01 crore to the contractors. (Paragraph '
                                                 '7.8.1) Injudicious approval of time extension and non/short levy '
                                                 'of penalty Audit observed that works were delayed, but '
                                                 'contractors were held responsible for shorter period of delays, '
                                                 'and the remaining period of delays were attributed to employer’s '
                                                 'fault or Force Majeure, such as layout delays, Covid-19, and '
                                                 'non-availability of power supply.',
                             'promise_date': '2020-04-01',
                             'affected_group': 'Targeted public-scheme beneficiaries and taxpayers in Madhya '
                                               'Pradesh'}},
        'CAG audit of Madhya Pradesh records a finding involving ₹2.01 crore',
        "From page 20 of a CAG audit report on Madhya Pradesh. The report says: 'This resulted in excess payment of ₹2.01 crore to the contractors'.",
    ),
    (
        'cag_without_figure',
        {'rule_triggered': 'P8-ARREARS-OR-UNFUNDED-PROMISE',
         'anomaly_metrics': {'scheme_name': 'Kerala CAG Audit — State Finances Audit (2024-25)',
                             'tranche_or_phase': 'CAG Report ID 125399 Finding #1: State Finances Audit (2024-25)',
                             'promised_metric': 'Full sanctioned scheme outlay, target coverage, and timely '
                                                'statutory delivery',
                             'revised_metric': 'CAG Report 125399 p.4: documented implementation/fund shortfall',
                             'metric_unit': 'CAG audited outlay / delivery variance',
                             'state_or_ut': 'Kerala',
                             'walkback_document_ref': 'CAG Audit Report ID 125399 (Kerala) — Executive Summary / '
                                                      'Audit Findings',
                             'walkback_summary': 'CAG Audit Finding (Report ID 125399, p.4): The State Government '
                                                 'also carried forward ₹3,511.62 crore of undischarged liabilities '
                                                 'in 2024-25, including liabilities towards Guarantee Redemption '
                                                 'Fund (GRF), interest payments, pending refund cases, National '
                                                 'Pension System (NPS), Kerala Road Safety Fund etc.',
                             'promise_date': '2022-04-01',
                             'affected_group': 'Targeted public-scheme beneficiaries and taxpayers in Kerala'}},
        'CAG audit of Kerala state finances records an observation on funds or delivery',
        'From page 4 of a CAG audit report on Kerala. The audit agents filed it as a gap between sanctioned funds and what was delivered.',
    ),
    (
        'mgnrega_delay_claims',
        {'rule_triggered': 'P8-ARREARS-OR-UNFUNDED-PROMISE',
         'anomaly_metrics': {'scheme_name': 'Goa — MGNREGA Statutory Delay Compensation Claims (Schedule II, '
                                            '0.05%/Day)',
                             'tranche_or_phase': 'NREGASoft Record: 93792 Delay Compensation Claims Logged vs 0 '
                                                 'Processed/Rejected in Goa',
                             'promised_metric': 'Automatic 0.05%/day delay compensation for wages delayed >16 days '
                                                'under MGNREGA Schedule II',
                             'revised_metric': '93792 delay compensation claims logged in Goa with widespread '
                                               'non-payment/rejections',
                             'metric_unit': 'delay compensation claims',
                             'state_or_ut': 'Goa',
                             'walkback_document_ref': 'Rajya Sabha Unstarred Question No. 3337 (28 March 2025) '
                                                      'Annexure',
                             'promise_date': '2006-02-02',
                             'affected_group': 'Rural MGNREGA wage workers experiencing delayed wage payments in '
                                               'Goa'}},
        'Goa: 93,792 MGNREGA late-wage claims logged, 0 processed or rejected',
        'A Rajya Sabha answer of 28 Mar 2025 shows 93,792 compensation claims for late MGNREGA wages logged in Goa, with 0 shown as processed or rejected. The law provides 0.05% a day in compensation when wages are more than 16 days late.',
    ),
    (
        'parliament_reply_arrears',
        {'rule_triggered': 'P8-ARREARS-OR-UNFUNDED-PROMISE',
         'anomaly_metrics': {'scheme_name': 'Smart Cities Mission (SCM)',
                             'tranche_or_phase': 'Inclusion of cities under the Smart Cities Mission (RS UQ 1819, '
                                                 '2026-03-09)',
                             'promised_metric': 'Completion of 100 Smart Cities projects within 5-year mission '
                                                'period (2015–2020)',
                             'revised_metric': 'Mission timeline extended repeatedly (2023 -> Jun 2024 -> Mar '
                                               '2025) with ongoing uncompleted projects at funding sunset',
                             'metric_unit': 'projects_completed_and_timeline',
                             'walkback_document_ref': 'Rajya Sabha (Session 270) Unstarred Q 1819 (2026-03-09) — '
                                                      'Inclusion of cities under the Smart Cities Mission',
                             'promise_date': '2015-06-25',
                             'affected_group': 'Residents of 100 designated Smart Cities and municipal SPVs'}},
        'Reply on Inclusion of cities under the Smart Cities Mission flagged for unmet promises',
        'Smart Cities Mission promise (2015): completion of 100 Smart Cities projects within 5-year mission period. Scheme record now notes: mission timeline extended repeatedly with ongoing uncompleted projects at funding sunset. The reply is dated 9 Mar 2026.',
    ),
    (
        'parliament_reply_midstream',
        {'rule_triggered': 'P8-MIDSTREAM-BENEFIT-CUT',
         'anomaly_metrics': {'scheme_name': 'Jal Jeevan Mission (Har Ghar Jal)',
                             'tranche_or_phase': 'Implementation of Jal Jeevan Mission 2.0 in Andhra Pradesh (LS '
                                                 'UQ 841, 2026-07-23)',
                             'promised_metric': '100% rural household Functional Household Tap Connections (FHTC) '
                                                'by 2024',
                             'revised_metric': '2024 target missed; mission extended to 2028 with state '
                                               'fund-release slowdowns and pending bills',
                             'metric_unit': 'fhtc_coverage_and_funds',
                             'walkback_document_ref': 'Lok Sabha (18th) Unstarred Q 841 (2026-07-23) — '
                                                      'Implementation of Jal Jeevan Mission 2.0 in Andhra Pradesh',
                             'promise_date': '2019-08-15',
                             'affected_group': 'Rural households awaiting tap water connections and state rural '
                                               'water agencies'}},
        'Reply on Implementation of Jal Jeevan Mission 2.0 in Andhra Pradesh flagged for changed benefits',
        'Jal Jeevan Mission promise (2019): 100% rural household Functional Household Tap Connections by 2024. Scheme record now notes: 2024 target missed, mission extended to 2028 with state fund-release slowdowns and pending bills.',
    ),
    (
        'budget_cut_midstream',
        {'rule_triggered': 'P8-MIDSTREAM-BENEFIT-CUT',
         'monetary_exposure_inr': 119980000000.0,
         'anomaly_metrics': {'scheme_name': 'Bihar Mahila Sashaktikaran & Social Welfare Outlay Cut (FY 2026-27)',
                             'tranche_or_phase': 'Mahila Sashaktikaran Slashed 57% (INR 21,050 Cr 2025-26 RE to '
                                                 'INR 9,052 Cr 2026-27 BE); Social Welfare -52%',
                             'promised_metric': 'INR 21,050 crore FY 2025-26 RE women empowerment allocation',
                             'revised_metric': 'Slashed 57% to INR 9,052 crore in FY 2026-27 BE (-INR 11,998 Cr)',
                             'metric_unit': 'INR crore women welfare outlay',
                             'state_or_ut': 'Bihar',
                             'walkback_document_ref': 'PRS Bihar Budget Analysis 2026-27',
                             'promise_date': '2025-03-03',
                             'affected_group': 'Women beneficiaries under Bihar Mahila Sashaktikaran schemes'}},
        'Bihar Mahila Sashaktikaran and Social Welfare: cut 57% to ₹9,052 crore in 2026-27 budget',
        'Earlier commitment (2025): ₹21,050 crore 2025-26 revised estimate women empowerment allocation. Recorded in a PRS budget analysis. Amount involved: ₹11,998 crore. Those affected: women beneficiaries under Bihar Mahila Sashaktikaran schemes.',
    ),
    (
        'minority_scholarship_discontinued',
        {'rule_triggered': 'P8-PREMATURE-DISCONTINUATION',
         'monetary_exposure_inr': 1500000.0,
         'anomaly_metrics': {'scheme_name': 'Sikkim — Pre-Matric (Classes I–VIII) & Post-Matric Minority '
                                            'Scholarships',
                             'tranche_or_phase': 'Sanctioned Outlay Cut (INR 0.15 Cr in 2021-22 to INR 0.018 Cr in '
                                                 '2022-23) & Not Approved Beyond 2021-22',
                             'promised_metric': 'INR 0.15 Cr FY 2021-22 Post-Matric + Classes I–VIII Pre-Matric '
                                                'coverage in Sikkim',
                             'revised_metric': 'Fell to INR 0.018 Cr in 2022-23; schemes not approved beyond '
                                               '2021-22 (LS Q2056)',
                             'metric_unit': 'INR crore sanctioned scholarships',
                             'state_or_ut': 'Sikkim',
                             'walkback_document_ref': 'Lok Sabha Unstarred Question No. 2056 (11 February 2026) — '
                                                      'Ministry of Minority Affairs',
                             'promise_date': '2008-04-01',
                             'affected_group': 'Notified minority-community school & college students in Sikkim'}},
        'Minority scholarship funds for Sikkim fell from ₹15 lakh to ₹1.8 lakh in 2022-23',
        'A Lok Sabha answer of 11 Feb 2026 shows sanctioned minority scholarship funds for Sikkim fell from ₹15 lakh in 2021-22 to ₹1.8 lakh in 2022-23. The record says the schemes were not approved beyond 2021-22.',
    ),
    (
        'scheme_ended_early',
        {'rule_triggered': 'P8-PREMATURE-DISCONTINUATION',
         'anomaly_metrics': {'scheme_name': 'Chhattisgarh Berojgari Bhatta (Unemployment Allowance Scheme)',
                             'tranche_or_phase': 'INR 2,500/Month Educated-Youth Unemployment Allowance Launched '
                                                 'in FY 2023-24 & Discontinued in FY 2024-25',
                             'promised_metric': 'INR 2,500 per month unemployment allowance to educated unemployed '
                                                'youth',
                             'revised_metric': 'Discontinued within 12 months in FY 2024-25 Budget',
                             'metric_unit': 'INR per youth per month',
                             'state_or_ut': 'Chhattisgarh',
                             'walkback_document_ref': 'PRS Chhattisgarh Budget Analysis 2024-25 (Policy '
                                                      'Highlights)',
                             'promise_date': '2023-03-06',
                             'affected_group': 'Educated unemployed youth registered under Berojgari Bhatta in '
                                               'Chhattisgarh'}},
        'Chhattisgarh Berojgari Bhatta: discontinued within 12 months in 2024-25 Budget',
        'Earlier commitment (2023): ₹2,500 per month unemployment allowance to educated unemployed youth. Recorded in a PRS budget analysis. Those affected: educated unemployed youth registered under Berojgari Bhatta in Chhattisgarh.',
    ),
    (
        'pension_cap_eligibility',
        {'rule_triggered': 'P8-ELIGIBILITY-NARROWING',
         'anomaly_metrics': {'scheme_name': 'Uttar Pradesh — National Social Assistance Programme (NSAP) Frozen '
                                            'Beneficiary Cap & Rate',
                             'tranche_or_phase': 'State/UT Digitized Cap (1432260 OAP Cohort) Frozen Since '
                                                 '21.09.2022 With Zero Rate Revision Proposal',
                             'promised_metric': 'Inflation-adjusted BPL pension coverage for eligible '
                                                'elderly/widows/disabled in Uttar Pradesh',
                             'revised_metric': 'Capped at 21.09.2022 ceiling (1432260 cohort) with central pension '
                                               'rate frozen & no revision proposal',
                             'metric_unit': 'beneficiary cap ceiling',
                             'state_or_ut': 'Uttar Pradesh',
                             'walkback_document_ref': 'Rajya Sabha Unstarred Question No. 2222 (9 August 2024) — '
                                                      'MoRD',
                             'promise_date': '2007-11-19',
                             'affected_group': 'BPL senior citizens, widows, and persons with disabilities in '
                                               'Uttar Pradesh'}},
        'Central pension cap for Uttar Pradesh held at 14,32,260 beneficiaries since 21 Sep 2022',
        'A Rajya Sabha answer of 9 Aug 2024 shows the National Social Assistance Programme cap for Uttar Pradesh fixed at 14,32,260 old-age pensioners since 21 Sep 2022. The central pension rate is unchanged and no revision is proposed.',
    ),
    (
        'eligibility_generic',
        {'rule_triggered': 'P8-ELIGIBILITY-NARROWING',
         'anomaly_metrics': {'scheme_name': 'Atal Pension Yojana (APY) — Guaranteed Monthly Pension Scheme',
                             'tranche_or_phase': 'Eligibility Narrowing — Income-Tax Payers Excluded Effective 1 '
                                                 'Oct 2022',
                             'promised_metric': 'Universal eligibility for all Indian citizens aged 18-40 with a '
                                                'savings bank account',
                             'revised_metric': 'Current and past income-tax payers barred from enrolling from 1 '
                                               'Oct 2022',
                             'metric_unit': 'subscriber_eligibility',
                             'walkback_document_ref': 'PIB Official Factsheet PDF (May 2025) — APY Taxpayer '
                                                      'Exclusion Rule',
                             'promise_date': '2015-05-09',
                             'affected_group': 'Citizens aged 18-40 who are or have been income-tax payers'}},
        'Atal Pension Yojana: current and past income-tax payers barred from enrolling from 1 Oct 2022',
        'Earlier commitment (2015): Universal eligibility for all Indian citizens aged 18-40 with a savings bank account. Recorded in a PIB factsheet. Those affected: citizens aged 18-40 who are or have been income-tax payers.',
    ),
    (
        'fund_freeze_lockin',
        {'rule_triggered': 'P8-LOCKIN-OR-EXIT-RESTRICTION',
         'monetary_exposure_inr': 30380000000.0,
         'anomaly_metrics': {'scheme_name': 'West Bengal MGNREGS Section 27 Complete Fund Freeze',
                             'tranche_or_phase': 'Central Funds Frozen Since 9 March 2022 Under MGNREGA Sec 27; '
                                                 'INR 3,038 Cr Pending Liability',
                             'promised_metric': 'Continuous demand-driven MGNREGS work & 15-day central DBT wages',
                             'revised_metric': '100% Central fund freeze since 9 March 2022 with INR 3,038 Cr '
                                               'pending liabilities',
                             'metric_unit': 'INR crore frozen liabilities',
                             'state_or_ut': 'West Bengal',
                             'walkback_document_ref': 'Lok Sabha Unstarred Question No. 1491 (29 July 2025) — MoRD',
                             'promise_date': '2006-02-02',
                             'affected_group': 'Rural MGNREGS job-card workers across West Bengal'}},
        'West Bengal MGNREGS Section 27: 100% Central fund freeze since 9 March 2022',
        'Earlier commitment (2006): Continuous demand-driven MGNREGS work and 15-day central DBT wages. A Lok Sabha answer of 29 Jul 2025 records: 100% Central fund freeze since 9 March 2022 with ₹3,038 crore pending liabilities.',
    ),
    (
        'gold_bond_tax',
        {'rule_triggered': 'P8-RETROACTIVE-TAX-REVERSAL',
         'monetary_exposure_inr': 33869700000.0,
         'anomaly_metrics': {'scheme_name': 'Sovereign Gold Bond (SGB) Scheme',
                             'tranche_or_phase': 'Tranche #42: SGB 2020-21 Series V (Issued 2020-08-11, Matures '
                                                 '2028-08-11)',
                             'promised_metric': 'Tax-exempt maturity redemption on 2028-08-11 for all individual '
                                                'holders (₹3,386.97 Cr)',
                             'revised_metric': 'Original-subscriber-only exemption from 1 Apr 2026; secondary '
                                               'buyers of 2020-21 Series V taxed at maturity',
                             'metric_unit': 'inr_crore_subscription',
                             'walkback_document_ref': 'Finance Bill, 2026 Clause 35 — Section 70(1)(x) (Effective '
                                                      '1 Apr 2026)',
                             'promise_date': '2020-08-11',
                             'affected_group': 'Secondary-market individual holders of SGB 2020-21 Series V '
                                               '(6,349,781 grams)'}},
        'Gold bond 2020-21 Series V: tax-free redemption now only for original buyers',
        'The Finance Bill 2026 limits the tax exemption at maturity to original subscribers from 1 Apr 2026. This Sovereign Gold Bond was issued on 11 Aug 2020 and matures on 11 Aug 2028, and those who bought it on the exchange will be taxed.',
    ),
    (
        'solar_pumps',
        {'rule_triggered': 'P8-ARREARS-OR-UNFUNDED-PROMISE',
         'anomaly_metrics': {'scheme_name': 'Kerala — PM-KUSUM Solar Agricultural Pump & Feeder Subsidy Delivery',
                             'tranche_or_phase': 'Component-C Feeder Level Solarisation: 22368 Sanctioned vs 11809 '
                                                 'Installed (49.9% Overall Delivery)',
                             'promised_metric': '31,824 sanctioned solar pumps/feeders in Kerala under PM-KUSUM',
                             'revised_metric': 'Only 15,880 installed (49.9% delivery; 15,944 pending backlog)',
                             'metric_unit': 'solar pumps/feeders installed',
                             'state_or_ut': 'Kerala',
                             'walkback_document_ref': 'Lok Sabha Unstarred Question No. 3911 (12 August 2026) '
                                                      'Annexure',
                             'promise_date': '2019-03-08',
                             'affected_group': 'Irrigating farmers awaiting daytime solar power in Kerala'}},
        'Kerala: 15,880 of 31,824 sanctioned PM-KUSUM solar pumps and feeders installed',
        'A Lok Sabha answer of 12 Aug 2026 shows 15,880 of 31,824 sanctioned solar pumps and feeders installed in Kerala under PM-KUSUM, or 49.9%. That leaves 15,944 pending.',
    ),
    (
        'long_case_name_leads_with_place',
        {'rule_triggered': 'P8-RETROACTIVE-TAX-REVERSAL',
         'anomaly_metrics': {'scheme_name': 'Maharashtra Electric Vehicle Motor Vehicle Tax Exemption',
                             'tranche_or_phase': 'FY 2025-26 Budget — 6% Motor Vehicle Tax Re-Imposed on Electric '
                                                 'Vehicles Priced Above INR 30 Lakh',
                             'promised_metric': '0% state motor vehicle tax on electric vehicles',
                             'revised_metric': '6% motor vehicle tax levied on electric vehicles priced above INR '
                                               '30 lakh',
                             'metric_unit': 'percent motor vehicle tax',
                             'state_or_ut': 'Maharashtra',
                             'walkback_document_ref': 'PRS Maharashtra Budget Analysis 2025-26 (Motor Vehicle Tax '
                                                      'Revision)',
                             'promise_date': '2024-07-01',
                             'affected_group': 'Electric four-wheeler buyers above INR 30 lakh in Maharashtra'}},
        'Maharashtra: 6% motor vehicle tax levied on electric vehicles priced above ₹30 lakh',
        'Earlier commitment (2024): 0% state motor vehicle tax on electric vehicles. Recorded in a PRS budget analysis. Those affected: electric four-wheeler buyers above ₹30 lakh in Maharashtra.',
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
    out = plain_brief({**finding, "methodology_code": "P8"})
    assert out == {"headline": headline, "brief": brief}
    assert len(headline) <= HEADLINE_MAX and len(brief) <= BRIEF_MAX
    assert not headline.endswith(".") and brief.endswith(".")
    for text in (headline, brief):
        assert not any(mark in text for mark in ("—", "–", ";", "!", "?", "||", "…", "  "))
    assert "P8-" not in headline + brief


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


def test_cag_rule_code_does_not_change_the_wording() -> None:
    # The engine files every CAG page under a walk-back category, so the category is not restated as fact.
    base = CASES[0][1]
    assert render({**base, "rule_triggered": "P8-ELIGIBILITY-NARROWING"}) == render(base)


def test_cag_quote_is_dropped_when_it_does_not_state_the_figure() -> None:
    finding = _with(CASES[0][1], walkback_summary="CAG Audit Finding (Report ID 1, p.20): Works were not completed.")
    _, brief = render(finding)
    assert brief == (
        "From page 20 of a CAG audit report on Madhya Pradesh. The figure cited is ₹2.01 crore. "
        "The audit agents filed it as a gap between sanctioned funds and what was delivered."
    )


def test_unparsed_family_falls_through_to_the_generic_sentence() -> None:
    solar = next(case[1] for case in CASES if case[0] == "solar_pumps")
    finding = _with(solar, promised_metric="Sanctioned solar pumps and feeders in Kerala under PM-KUSUM")
    assert render(finding) == (
        "Kerala PM-KUSUM Solar Agricultural Pump and Feeder: 15,880 installed",
        (
            "Earlier commitment (2019): Sanctioned solar pumps and feeders in Kerala under PM-KUSUM. "
            "Recorded in a Lok Sabha answer of 12 Aug 2026. "
            "Those affected: irrigating farmers awaiting daytime solar power in Kerala."
        ),
    )


def test_small_crore_figures_are_shown_in_lakh() -> None:
    finding = _with(
        CASES[6][1],
        tranche_or_phase="Sanctioned Outlay Cut (INR 0.15 Cr in 2021-22 to INR 0.005 Cr in 2022-23)",
        revised_metric="Fell to INR 0.005 Cr in 2022-23",
    )
    headline, brief = render(finding)
    assert headline == "Minority scholarship funds for Sikkim fell from ₹15 lakh to ₹0.5 lakh in 2022-23"
    assert "not approved" not in brief


def test_thin_fields_hand_over_to_the_generic_fallback() -> None:
    assert render({"rule_triggered": "P8-MIDSTREAM-BENEFIT-CUT", "anomaly_metrics": {"scheme_name": "Some Scheme"}}) == (
        "",
        "",
    )
    assert render({"anomaly_metrics": {"revised_metric": "Cut to INR 5 Cr"}}) == ("", "")


def test_machine_shorthand_is_cleaned_from_free_text() -> None:
    finding = {
        "rule_triggered": "P8-MIDSTREAM-BENEFIT-CUT",
        "anomaly_metrics": {
            "scheme_name": "Example Yojana (EY) — Phase II",
            "promised_metric": "INR 1,500/month for 1234567 women (FY 2024-25 BE)",
            "revised_metric": "Slashed to INR 500/month from 01.04.2025; arrears pending",
            "walkback_document_ref": "PRS Example Budget Analysis 2025-26",
            "promise_date": "2024-06-28",
        },
    }
    assert render(finding) == (
        "Example Yojana: cut to ₹500 a month from 1 Apr 2025, arrears pending",
        "Earlier commitment (2024): ₹1,500 a month for 12,34,567 women. Recorded in a PRS budget analysis.",
    )
