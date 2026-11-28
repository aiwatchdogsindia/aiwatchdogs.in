"""Plain-language headline and brief for P10 findings (custody and release records)."""

from __future__ import annotations

from typing import Any

import pytest

from aiwatchdogs.core.plain import BRIEF_MAX, HEADLINE_MAX, plain_brief
from aiwatchdogs.core.plain.p10 import render
from aiwatchdogs.core.safety import lint_neutrality

# Trimmed from live findings: only the fields the writer reads.
# Court fixtures leave out case_title (it names private parties and the writer never reads it for court orders)
# and carry a neutral stand-in for the quoted direction.
CASES: dict[str, dict[str, Any]] = {'gap_detention_struck_down': {'methodology_code': 'P10',
                               'rule_triggered': 'P10-INSTITUTIONAL-COMPLIANCE-GAP',
                               'entity_name': 'Home & Prison Administration, Tamil Nadu',
                               'jurisdiction': 'State:Tamil Nadu',
                               'anomaly_metrics': {'portal_category': 'court_portal',
                                                   'acquittal_court': 'High Court of Judicature at Madras',
                                                   'acquittal_case_number': 'HCP/2503/2024',
                                                   'acquittal_order_type': 'detention_quashed',
                                                   'acquittal_order_date': '2024-10-28',
                                                   'document_date': '2024-10-28',
                                                   'prison_name': 'Central Prison, Salem',
                                                   'overrun_days': 0,
                                                   'release_date': '2024-10-28',
                                                   'release_verified': True,
                                                   'stage_1_acquittal_verified': True,
                                                   'stage_3_contempt_verified': False,
                                                   'judicial_direction_summary': 'The detenu is directed to be set '
                                                                                 'at liberty forthwith, unless '
                                                                                 'required in any other case.'}},
 'gap_parliament_reply': {'methodology_code': 'P10',
                          'rule_triggered': 'P10-INSTITUTIONAL-COMPLIANCE-GAP',
                          'entity_name': 'Lok Sabha (Ministry of Law and Justice)',
                          'jurisdiction': 'Union of India',
                          'anomaly_metrics': {'case_title': 'Lok Sabha UQ (LS-17/S7/UQ-1041): Delay in Judicial '
                                                            'Verdicts',
                                              'portal_category': 'parliament',
                                              'acquittal_court': 'Lok Sabha (Ministry of Law and Justice)',
                                              'acquittal_case_number': 'LS-17/S7/UQ-1041',
                                              'acquittal_order_type': 'institutional_compliance_review',
                                              'document_date': '2021-12-03',
                                              'prison_name': 'Correctional Institutions / Prison Administration '
                                                             '(All-India)',
                                              'release_verified': True,
                                              'stage_1_acquittal_verified': False,
                                              'stage_3_contempt_verified': False}},
 'post_acquittal_extra_custody': {'methodology_code': 'P10',
                                  'rule_triggered': 'P10-POST-ACQUITTAL-CONTEMPT-OVERRUN',
                                  'entity_name': 'Director General of Prisons, Chhattisgarh & Superintendent, '
                                                 'Central Jail, Durg',
                                  'jurisdiction': 'State:Chhattisgarh',
                                  'anomaly_metrics': {'portal_category': 'court_portal',
                                                      'acquittal_court': 'High Court of Chhattisgarh',
                                                      'acquittal_case_number': 'CRA No. 968 of 2013',
                                                      'acquittal_order_type': 'acquittal',
                                                      'acquittal_order_date': '2018-01-20',
                                                      'document_date': '2026-05-01',
                                                      'prison_name': 'Central Jail, Durg, Chhattisgarh',
                                                      'overrun_days': 2676,
                                                      'release_date': '2025-05-19',
                                                      'release_verified': True,
                                                      'stage_1_acquittal_verified': True,
                                                      'stage_3_contempt_verified': False,
                                                      'contempt_court': 'High Court of Chhattisgarh',
                                                      'compensation_inr': 200000.0,
                                                      'judicial_direction_summary': 'Order regarding custody and '
                                                                                    'release compliance.'}},
 'post_acquittal_no_count': {'methodology_code': 'P10',
                             'rule_triggered': 'P10-POST-ACQUITTAL-CONTEMPT-OVERRUN',
                             'entity_name': 'Home & Prison Administration, Sikkim',
                             'jurisdiction': 'State:Sikkim',
                             'anomaly_metrics': {'portal_category': 'court_portal',
                                                 'acquittal_court': 'High Court of Sikkim',
                                                 'acquittal_case_number': 'Crl. A./32/2024',
                                                 'acquittal_order_type': 'acquittal',
                                                 'acquittal_order_date': '2025-06-09',
                                                 'document_date': '2025-06-09',
                                                 'prison_name': 'Central Prison, Rongyek and to the Jail Authority '
                                                                'fo',
                                                 'release_verified': False,
                                                 'stage_1_acquittal_verified': True,
                                                 'stage_3_contempt_verified': False,
                                                 'judicial_direction_summary': 'Order regarding custody and release '
                                                                               'compliance.'}},
 'release_order_contempt': {'methodology_code': 'P10',
                            'rule_triggered': 'P10-RELEASE-ORDER-CONTEMPT-OVERRUN',
                            'entity_name': 'Home & Prison Administration, Odisha',
                            'jurisdiction': 'State:Odisha',
                            'anomaly_metrics': {'portal_category': 'court_portal',
                                                'acquittal_court': 'High Court of Orissa',
                                                'acquittal_case_number': 'CONTC/2538/2026',
                                                'acquittal_order_type': 'bail_release_order',
                                                'document_date': '2026-05-08',
                                                'prison_name': 'Circle Jail, Sambalpur',
                                                'release_date': '2026-05-08',
                                                'release_verified': True,
                                                'stage_1_acquittal_verified': False,
                                                'stage_3_contempt_verified': True,
                                                'contempt_court': 'High Court of Orissa',
                                                'judicial_direction_summary': 'Order regarding custody and release '
                                                                              'compliance.'}},
 'release_order_extra_custody': {'methodology_code': 'P10',
                                 'rule_triggered': 'P10-RELEASE-ORDER-CONTEMPT-OVERRUN',
                                 'entity_name': 'Principal Secretary Home & Jail Dept Jharkhand, IG Prisons & Supdt. '
                                                'Birsa Munda Central Jail, Hotwar',
                                 'jurisdiction': 'State:Jharkhand',
                                 'anomaly_metrics': {'portal_category': 'court_portal',
                                                     'acquittal_court': 'High Court of Jharkhand',
                                                     'acquittal_case_number': 'Contempt Case (Civil) No. 409 of 2022',
                                                     'acquittal_order_type': 'bail_release_order',
                                                     'acquittal_order_date': '2022-06-01',
                                                     'document_date': '2023-02-03',
                                                     'prison_name': 'Birsa Munda Central Jail, Hotwar, Ranchi, '
                                                                    'Jharkhand',
                                                     'overrun_days': 247,
                                                     'release_date': '2023-02-03',
                                                     'release_verified': True,
                                                     'stage_1_acquittal_verified': True,
                                                     'stage_3_contempt_verified': True,
                                                     'contempt_court': 'High Court of Jharkhand',
                                                     'judicial_direction_summary': 'Order regarding custody and '
                                                                                   'release compliance.'}},
 'warrant_release_plea': {'methodology_code': 'P10',
                          'rule_triggered': 'P10-WARRANT-TRANSMISSION-DELAY',
                          'entity_name': 'Home & Prison Administration, Chhattisgarh',
                          'jurisdiction': 'State:Chhattisgarh',
                          'anomaly_metrics': {'portal_category': 'court_portal',
                                              'acquittal_court': 'High Court of Chhattisgarh',
                                              'acquittal_case_number': 'WPCR/28/2026',
                                              'acquittal_order_type': 'bail_release_order',
                                              'acquittal_order_date': '2026-01-15',
                                              'document_date': '2026-01-15',
                                              'prison_name': 'Central Jail Durg',
                                              'release_verified': False,
                                              'stage_1_acquittal_verified': True,
                                              'stage_3_contempt_verified': False,
                                              'judicial_direction_summary': 'Order regarding custody and release '
                                                                            'compliance.'}},
 'warrant_document': {'methodology_code': 'P10',
                      'rule_triggered': 'P10-WARRANT-TRANSMISSION-DELAY',
                      'entity_name': 'National Legal Services Authority (NALSA)',
                      'jurisdiction': 'Union of India',
                      'anomaly_metrics': {'case_title': 'NALSA — Standard Operating Procedure (September 2022) for '
                                                        'Timely Filing of Bail Applications of Undertrial Prisoners',
                                          'portal_category': 'nalsa_slsa',
                                          'acquittal_court': 'National Legal Services Authority (NALSA)',
                                          'acquittal_case_number': 'NALSA-2022-2024041313.pdf',
                                          'acquittal_order_type': 'institutional_compliance_review',
                                          'document_date': '2022-09-12',
                                          'prison_name': 'Correctional Institutions / Prison Administration '
                                                         '(All-India)',
                                          'release_verified': True,
                                          'stage_1_acquittal_verified': False,
                                          'stage_3_contempt_verified': False}},
 'statutory_newsletter': {'methodology_code': 'P10',
                          'rule_triggered': 'P10-STATUTORY-UNLAWFUL-DETENTION',
                          'entity_name': 'National Human Rights Commission (NHRC)',
                          'jurisdiction': 'Union of India',
                          'anomaly_metrics': {'case_title': 'NHRC Human Rights Newsletter (Vol. 33, No. 10 — October '
                                                            '2026): Custodial Rights, Detention Notices and Prison '
                                                            'Oversight',
                                              'portal_category': 'nhrc_shrc',
                                              'acquittal_court': 'National Human Rights Commission (NHRC)',
                                              'acquittal_case_number': 'NHRC-2026-1793269144_cdffc19cfc9e6b8',
                                              'acquittal_order_type': 'institutional_compliance_review',
                                              'document_date': '2026-10-15',
                                              'prison_name': 'Correctional Institutions / Prison Administration '
                                                             '(All-India)',
                                              'release_verified': True,
                                              'stage_1_acquittal_verified': False,
                                              'stage_3_contempt_verified': True}},
 'statutory_parliament_reply': {'methodology_code': 'P10',
                                'rule_triggered': 'P10-STATUTORY-UNLAWFUL-DETENTION',
                                'entity_name': 'Lok Sabha (Ministry of Home Affairs)',
                                'jurisdiction': 'Union of India',
                                'anomaly_metrics': {'case_title': 'Lok Sabha UQ (LS-17/S7/UQ-256): Custodial Deaths '
                                                                  'of SC&ST Children',
                                                    'portal_category': 'parliament',
                                                    'acquittal_court': 'Lok Sabha (Ministry of Home Affairs)',
                                                    'acquittal_case_number': 'LS-17/S7/UQ-256',
                                                    'acquittal_order_type': 'institutional_compliance_review',
                                                    'document_date': '2021-11-30',
                                                    'prison_name': 'Correctional Institutions / Prison '
                                                                   'Administration (All-India)',
                                                    'release_verified': False,
                                                    'stage_1_acquittal_verified': False,
                                                    'stage_3_contempt_verified': False}}}

EXPECTED: dict[str, tuple[str, str]] = {'gap_detention_struck_down': (('Madras High Court struck down a detention order and directed release from Central '
                               'Prison, Salem'),
                               ('In a habeas corpus petition, the Madras High Court on 28 Oct 2024 set aside a '
                               'detention order and directed that the detainee, held at Central Prison, Salem, be '
                               'freed unless wanted in another case. No extra days in custody are on record.')),
 'gap_parliament_reply': ('Lok Sabha reply on delay in judicial verdicts',
                          ('This is a Lok Sabha reply dated 3 Dec 2021. The audit lists it as a general record on '
                          'undertrial detention, release delays or custody rules. It is not about one case and gives '
                          'no count of extra days in custody.')),
 'post_acquittal_extra_custody': (('Chhattisgarh High Court order records 2,676 days of extra custody after an '
                                  'acquittal'),
                                  ('A Chhattisgarh High Court order dated 1 May 2026 records 2,676 days of extra '
                                  'custody at Central Jail, Durg, Chhattisgarh after an acquittal on 20 Jan 2018. '
                                  'Release is recorded on 19 May 2025. The court ordered ₹2 lakh in compensation.')),
 'post_acquittal_no_count': (('Sikkim High Court order refers to an acquittal in a custody case at Central Prison, '
                             'Rongyek'),
                             ('In a criminal appeal, the Sikkim High Court on 9 Jun 2025 gave an order that refers to '
                             'an acquittal and deals with custody or release for a person held at Central Prison, '
                             'Rongyek. No extra days in custody are on record.')),
 'release_order_contempt': (('Orissa High Court heard a contempt plea over a release order in a case from Circle '
                            'Jail, Sambalpur'),
                            ('In a contempt petition, the Orissa High Court on 8 May 2026 dealt with whether an '
                            'earlier release order was followed. The prison named is Circle Jail, Sambalpur. No '
                            'extra days in custody are on record.')),
 'release_order_extra_custody': ('Jharkhand High Court order records 247 days of extra custody',
                                 ('A Jharkhand High Court order dated 3 Feb 2023 records 247 days of extra custody. A '
                                 'contempt action is on record.')),
 'warrant_release_plea': (('Chhattisgarh High Court ruled on a bail or release plea from a prisoner at Central Jail '
                          'Durg'),
                          ('In a criminal writ petition, the Chhattisgarh High Court on 15 Jan 2026 ruled on bail, '
                          'remission or early release of a prisoner at Central Jail Durg. No extra days in custody '
                          'are on record.')),
 'warrant_document': (('NALSA: standard operating procedure for timely filing of bail applications of undertrial '
                      'prisoners'),
                      ('This is a legal services authority record dated 12 Sep 2022. The audit lists it as a general '
                      'record on release warrants and how they reach prisons. It is not about one case and gives no '
                      'count of extra days in custody.')),
 'statutory_newsletter': (('NHRC Human Rights Newsletter, October 2026 covers custodial rights, detention notices and '
                          'prison oversight'),
                          ('This is a human rights commission record dated 15 Oct 2026. The audit lists it as a '
                          'general record on detention, time spent in custody or release. It is not about one case '
                          'and gives no count of extra days in custody.')),
 'statutory_parliament_reply': ('Lok Sabha reply on custodial deaths of SC&ST children',
                                ('This is a Lok Sabha reply dated 30 Nov 2021. The audit lists it as a general record '
                                'on detention, time spent in custody or release. It is not about one case and gives '
                                'no count of extra days in custody.'))}


@pytest.mark.parametrize("name", sorted(CASES))
def test_exact_headline_and_brief(name: str) -> None:
    assert render(CASES[name]) == EXPECTED[name]


@pytest.mark.parametrize("name", sorted(CASES))
def test_fits_without_clipping(name: str) -> None:
    headline, brief = render(CASES[name])
    assert 0 < len(headline) <= HEADLINE_MAX
    assert 0 < len(brief) <= BRIEF_MAX
    out = plain_brief(CASES[name])
    assert out == {"headline": headline, "brief": brief}
    assert "…" not in headline + brief


@pytest.mark.parametrize("name", sorted(CASES))
def test_house_style(name: str) -> None:
    headline, brief = render(CASES[name])
    assert lint_neutrality(headline, brief, ["placeholder"]).passed
    assert not headline.endswith(".")
    assert brief.endswith(".")
    for banned in ("—", ";", "!", "?", "||", "P10-", "fnd-"):
        assert banned not in headline + brief
    assert headline[0].isupper()


@pytest.mark.parametrize("name", sorted(CASES))
def test_deterministic(name: str) -> None:
    assert render(CASES[name]) == render(CASES[name])


@pytest.mark.parametrize(
    "finding",
    [
        {},
        {"methodology_code": "P10"},
        {"methodology_code": "P10", "anomaly_metrics": None},
        {"methodology_code": "P10", "anomaly_metrics": {}},
        {"methodology_code": "P10", "rule_triggered": None, "entity_name": None, "anomaly_metrics": {'acquittal_case_number': None,
 'acquittal_court': None,
 'acquittal_order_date': None,
 'acquittal_order_type': None,
 'case_title': None,
 'compensation_inr': None,
 'contempt_court': None,
 'document_date': None,
 'judicial_direction_summary': None,
 'overrun_days': None,
 'portal_category': None,
 'prison_name': None,
 'release_date': None,
 'release_verified': None,
 'stage_1_acquittal_verified': None,
 'stage_3_contempt_verified': None}},
        {"methodology_code": "P10", "rule_triggered": 7, "entity_name": ["x"], "anomaly_metrics": {'acquittal_case_number': [1, 2],
 'acquittal_court': {'a': 1},
 'acquittal_order_date': True,
 'acquittal_order_type': 3.5,
 'case_title': -1,
 'compensation_inr': 'object',
 'contempt_court': float("nan"),
 'document_date': ('t',),
 'judicial_direction_summary': 'x',
 'overrun_days': 0,
 'portal_category': [1, 2],
 'prison_name': {'a': 1},
 'release_date': True,
 'release_verified': 3.5,
 'stage_1_acquittal_verified': -1,
 'stage_3_contempt_verified': 'object'}},
        {"methodology_code": "P10", "anomaly_metrics": "not a dict", "title": 3, "summary": None},
    ],
)
def test_never_raises_on_thin_or_wrong_typed_input(finding: dict[str, Any]) -> None:
    headline, brief = render(finding)
    assert isinstance(headline, str)
    assert isinstance(brief, str)
    out = plain_brief(finding)
    assert isinstance(out["headline"], str) and out["headline"]
    assert isinstance(out["brief"], str)


def test_non_dict_input_returns_empty() -> None:
    assert render(None) == ("", "")  # type: ignore[arg-type]
    assert render("finding") == ("", "")  # type: ignore[arg-type]

def test_case_title_names_never_reach_the_output() -> None:
    finding = dict(CASES["gap_detention_struck_down"])
    metrics = dict(finding["anomaly_metrics"])
    metrics["case_title"] = "HCP/1/2025 of RAMESH KUMAR Vs THE STATE"
    finding["anomaly_metrics"] = metrics
    headline, brief = render(finding)
    assert (headline, brief) == EXPECTED["gap_detention_struck_down"]
    assert "RAMESH" not in headline + brief


def test_zero_or_missing_overrun_never_claims_extra_custody() -> None:
    for days in (0, None, "", "n/a", -3):
        finding = dict(CASES["post_acquittal_extra_custody"])
        metrics = dict(finding["anomaly_metrics"])
        metrics["overrun_days"] = days
        metrics.pop("compensation_inr", None)
        finding["anomaly_metrics"] = metrics
        headline, brief = render(finding)
        assert "extra custody" not in headline
        assert brief.endswith("No extra days in custody are on record.")


def test_scraping_debris_in_prison_name_is_dropped() -> None:
    finding = dict(CASES["warrant_release_plea"])
    metrics = dict(finding["anomaly_metrics"])
    metrics["prison_name"] = "district jail Saharanpur where properly not calcul"
    finding["anomaly_metrics"] = metrics
    headline, brief = render(finding)
    assert headline == "Chhattisgarh High Court ruled on a bail or release plea on 15 Jan 2026"
    assert "Saharanpur" not in brief


def test_long_court_name_is_shortened_to_keep_the_closing_line() -> None:
    finding = {
        "methodology_code": "P10",
        "rule_triggered": "P10-POST-ACQUITTAL-CONTEMPT-OVERRUN",
        "jurisdiction": "UT:Jammu and Kashmir",
        "anomaly_metrics": {
            "portal_category": "court_portal",
            "acquittal_court": "High Court of Jammu & Kashmir and Ladakh",
            "acquittal_case_number": "HCP/217/2024",
            "acquittal_order_type": "detention_quashed",
            "document_date": "2025-04-24",
            "prison_name": "Central Jail, Kot Bhalwal",
            "overrun_days": 0,
            "stage_1_acquittal_verified": True,
            "judicial_direction_summary": "The detenu shall be released forthwith unless required in any other case.",
        },
    }
    headline, brief = render(finding)
    assert headline == "Jammu & Kashmir and Ladakh High Court struck down a detention order on 24 Apr 2025"
    assert brief == (
        "In a habeas corpus petition, the J&K and Ladakh High Court on 24 Apr 2025 set aside a detention order "
        "and directed that the detainee, held at Central Jail, Kot Bhalwal, be freed unless wanted in another case. "
        "No extra days in custody are on record."
    )


@pytest.mark.parametrize(
    ("direction", "directs"),
    [
        ("Due to the nationality issue, the accused could not be released from jail.", False),
        ("The detention order is sought to be quashed and the detenu set at liberty on the grounds made out.", False),
        ("The petitioner shall not be released from custody till further orders.", False),
        ("The detenue is directed to be set at liberty forthwith, if not required in any other case.", True),
        ("The petitioner is directed to be released forthwith by the Superintendent.", True),
    ],
)
def test_release_wording_needs_a_clear_court_direction(direction: str, directs: bool) -> None:
    finding = {
        "methodology_code": "P10",
        "jurisdiction": "State:Manipur",
        "anomaly_metrics": {
            "acquittal_court": "High Court of Manipur",
            "prison_name": "Central Jail, Sajiwa",
            "stage_1_acquittal_verified": True,
            "judicial_direction_summary": direction,
            "document_date": "2026-02-10",
        },
    }
    headline, brief = render(finding)
    assert ("directed" in f"{headline} {brief}") is directs, (headline, brief)
