"""Plain-language headline and brief for P9 findings (government exam delays and disruptions)."""

from __future__ import annotations

from typing import Any

import pytest

from aiwatchdogs.core.plain import BRIEF_MAX, HEADLINE_MAX, plain_brief
from aiwatchdogs.core.plain.p9 import render
from aiwatchdogs.core.safety import lint_neutrality

# Trimmed from live findings: only the fields the writer reads.
CASES: dict[str, dict[str, Any]] = {'delayed_templated': {'methodology_code': 'P9',
                       'rule_triggered': 'P9-DELAYED',
                       'entity_name': 'Haryana Public Service Commission (HPSC)',
                       'jurisdiction': 'State:Haryana',
                       'anomaly_metrics': {'authority': 'Haryana Public Service Commission (HPSC)',
                                           'exam_name': 'Advt No. 23 of 2023 - Treasury Officer and Assistant '
                                                        'Treasury Officer in Finance Department, Haryana',
                                           'cycle_or_stage': 'Preliminary Examination (2024)',
                                           'exam_year': 2024,
                                           'disruption_type': 'delayed',
                                           'disruption_reason_category': 'judicial_or_policy_review',
                                           'delay_days': 120,
                                           'scheduled_date': '2024-07-16',
                                           'actual_or_event_date': '2024-11-13',
                                           'schedule_summary': 'Haryana Public Service Commission (HPSC) '
                                                               'notified/scheduled Advt No. 23 of 2023 - Treasury '
                                                               'Officer and Assistant Treasury Officer in Finance '
                                                               'Department, Haryana — Preliminary Examination (2024) '
                                                               'for 2024-07-16 in Haryana (State).',
                                           'outcome_summary': 'Official Haryana Public Service Commission (HPSC) '
                                                              'record dated 2024-11-13 documents a delayed/revised '
                                                              'result, court-ordered compliance review, or formal '
                                                              'corrigendum modifying Advt No. 23 of 2023 - Treasury '
                                                              'Officer and Assistant Treasury Officer in Finance '
                                                              'Department, Haryana (Preliminary Examination (2024), '
                                                              'scheduled 2024-07-16).',
                                           'outcome_document_ref': 'Haryana Public Service Commission (HPSC) '
                                                                   'Official Notice (notification No. '
                                                                   '2216312024-5HR-III)'}},
 'delayed_no_gap': {'methodology_code': 'P9',
                    'rule_triggered': 'P9-DELAYED',
                    'entity_name': 'Haryana Public Service Commission (HPSC)',
                    'jurisdiction': 'State:Haryana',
                    'anomaly_metrics': {'authority': 'Haryana Public Service Commission (HPSC)',
                                        'exam_name': 'Manager (Utility)',
                                        'cycle_or_stage': 'Recruitment Examination (2024)',
                                        'exam_year': 2024,
                                        'disruption_type': 'delayed',
                                        'disruption_reason_category': 'judicial_or_policy_review',
                                        'delay_days': 0,
                                        'scheduled_date': '2024-11-18',
                                        'actual_or_event_date': '2024-11-18',
                                        'schedule_summary': 'Haryana Public Service Commission (HPSC) '
                                                            'notified/scheduled Manager (Utility) — Recruitment '
                                                            'Examination (2024) for 2024-11-18 in Haryana (State).',
                                        'outcome_summary': 'Official Haryana Public Service Commission (HPSC) record '
                                                           'dated 2024-11-18 documents a delayed/revised result, '
                                                           'court-ordered compliance review, or formal corrigendum '
                                                           'modifying Manager (Utility) (Recruitment Examination '
                                                           '(2024), scheduled 2024-11-18).',
                                        'outcome_document_ref': 'Haryana Public Service Commission (HPSC) Official '
                                                                'Notice (Advt. No. l(8))'}},
 'delayed_vacancy_table': {'methodology_code': 'P9',
                           'rule_triggered': 'P9-DELAYED',
                           'entity_name': 'AIIMS Rishikesh Standing Selection Committee',
                           'jurisdiction': 'State:Uttarakhand',
                           'anomaly_metrics': {'authority': 'AIIMS Rishikesh Standing Selection Committee',
                                               'exam_name': 'AIIMS Rishikesh (Uttarakhand) — Standing Selection '
                                                            'Committee Faculty Recruitment 2025',
                                               'cycle_or_stage': '2025-26 Faculty Selection Backlog (126 Vacant of '
                                                                 '355 Sanctioned Posts)',
                                               'exam_year': 2025,
                                               'disruption_type': 'delayed',
                                               'disruption_reason_category': 'administrative_or_logistical',
                                               'delay_days': 318,
                                               'scheduled_date': '2025-04-01',
                                               'actual_or_event_date': '2026-02-13',
                                               'schedule_summary': 'Mandated to hold four Standing Selection '
                                                                   'Committee (SSC) meetings per year alongside '
                                                                   'bi-annual INI-CET/INI-SS resident exams, AIIMS '
                                                                   'Rishikesh (Uttarakhand) still carried 126 '
                                                                   'faculty vacancies out of 355 sanctioned posts '
                                                                   '(229 in position).',
                                               'outcome_summary': 'MoHFW cited scarcity of eligible '
                                                                  'super-specialists, reservation roster, and '
                                                                  'institute location for the 126 vacant faculty '
                                                                  'posts at AIIMS Rishikesh, permitting '
                                                                  'retired-faculty engagement up to age 70 and '
                                                                  'Visiting Faculty.',
                                               'outcome_document_ref': 'Lok Sabha Starred Question No. 207 '
                                                                       '(13.02.2026): Faculty Shortage Mitigation '
                                                                       'Measures at AIIMS Rishikesh'}},
 'postponed_hand_written': {'methodology_code': 'P9',
                            'rule_triggered': 'P9-POSTPONED',
                            'entity_name': 'Central Board of Secondary Education (CBSE)',
                            'jurisdiction': 'Union of India',
                            'anomaly_metrics': {'authority': 'Central Board of Secondary Education (CBSE)',
                                                'exam_name': 'Central Teacher Eligibility Test (CTET) — 22nd Edition',
                                                'cycle_or_stage': '22nd Edition (2026) — Postponed 06 Sep -> 12–13 '
                                                                  'Dec 2026',
                                                'exam_year': 2026,
                                                'disruption_type': 'postponed',
                                                'disruption_reason_category': 'judicial_or_policy_review',
                                                'delay_days': 97,
                                                'scheduled_date': '2026-09-06',
                                                'actual_or_event_date': '2026-12-12',
                                                'schedule_summary': "CBSE's Public Notice dated 08.05.2026 notified "
                                                                    'conduct of the 22nd Edition of CTET (Paper-I '
                                                                    'and Paper-II) on 06th September, 2026.',
                                                'outcome_summary': 'Following Supreme Court directions in Review '
                                                                   'Petition (Civil) Diary No. 53434/2025 reopening '
                                                                   'registrations, CBSE postponed the 22nd CTET by '
                                                                   '97 days to 12th and 13th December 2026.',
                                                'outcome_document_ref': 'CBSE CTET Public Notice dated 14 September '
                                                                        '2026'}},
 'postponed_templated': {'methodology_code': 'P9',
                         'rule_triggered': 'P9-POSTPONED',
                         'entity_name': 'Meghalaya Public Service Commission (MPSC Meghalaya)',
                         'jurisdiction': 'State:Meghalaya',
                         'anomaly_metrics': {'authority': 'Meghalaya Public Service Commission (MPSC Meghalaya)',
                                             'exam_name': 'Food Safety Officer',
                                             'cycle_or_stage': 'Screening Test (2025)',
                                             'exam_year': 2025,
                                             'disruption_type': 'postponed',
                                             'disruption_reason_category': 'administrative_or_logistical',
                                             'delay_days': 7,
                                             'scheduled_date': '2025-07-12',
                                             'actual_or_event_date': '2025-07-19',
                                             'schedule_summary': 'Meghalaya Public Service Commission (MPSC '
                                                                 'Meghalaya) notified/scheduled Food Safety Officer '
                                                                 '— Screening Test (2025) for 2025-07-12 in '
                                                                 'Meghalaya (State).',
                                             'outcome_summary': 'Official Meghalaya Public Service Commission (MPSC '
                                                                'Meghalaya) notice dated 2025-07-19 '
                                                                'postponed/deferred Food Safety Officer (Screening '
                                                                'Test (2025), scheduled 2025-07-12).',
                                             'outcome_document_ref': 'Meghalaya Public Service Commission (MPSC '
                                                                     'Meghalaya) Official Notice (2025-07-19)'}},
 'rescheduled_templated': {'methodology_code': 'P9',
                           'rule_triggered': 'P9-RESCHEDULED',
                           'entity_name': 'Kerala Public Service Commission (Kerala PSC)',
                           'jurisdiction': 'State:Kerala',
                           'anomaly_metrics': {'authority': 'Kerala Public Service Commission (Kerala PSC)',
                                               'exam_name': 'Kerala Public Service Commission Examination (2026)',
                                               'cycle_or_stage': 'Interview / Viva-Voce (2026)',
                                               'exam_year': 2026,
                                               'disruption_type': 'rescheduled',
                                               'disruption_reason_category': 'administrative_or_logistical',
                                               'delay_days': 8,
                                               'scheduled_date': '2026-09-01',
                                               'actual_or_event_date': '2026-09-09',
                                               'schedule_summary': 'Kerala Public Service Commission (Kerala PSC) '
                                                                   'notified/scheduled Kerala Public Service '
                                                                   'Commission Examination (2026) — Interview / '
                                                                   'Viva-Voce (2026) for 2026-09-01 in Kerala '
                                                                   '(State).',
                                               'outcome_summary': 'Official Kerala Public Service Commission (Kerala '
                                                                  'PSC) notice rescheduled/revised the examination '
                                                                  'date or schedule for Kerala Public Service '
                                                                  'Commission Examination (2026) (Interview / '
                                                                  'Viva-Voce (2026)) from 2026-09-01 to 2026-09-09 '
                                                                  '(8 days shift).',
                                               'outcome_document_ref': 'Kerala Public Service Commission (Kerala '
                                                                       'PSC) Official Notice (2026-09-09)'}},
 'rescheduled_hand_written': {'methodology_code': 'P9',
                              'rule_triggered': 'P9-RESCHEDULED',
                              'entity_name': 'National Testing Agency (NTA)',
                              'jurisdiction': 'State:Assam',
                              'anomaly_metrics': {'authority': 'National Testing Agency (NTA)',
                                                  'exam_name': 'Common University Entrance Test (UG) [CUET (UG)]',
                                                  'cycle_or_stage': '2024 Cycle (Silchar, Assam — Bengali & '
                                                                    'Environmental Studies)',
                                                  'exam_year': 2024,
                                                  'disruption_type': 'rescheduled',
                                                  'disruption_reason_category': 'administrative_or_logistical',
                                                  'delay_days': 5,
                                                  'scheduled_date': '2024-05-24',
                                                  'actual_or_event_date': '2024-05-29',
                                                  'schedule_summary': 'Due to logistical constraints, CUET (UG) 2024 '
                                                                      'candidates opting for Silchar were initially '
                                                                      'assigned CBT centres outside Silchar for '
                                                                      'Bengali (104) and Environmental Studies (307) '
                                                                      'on 24 May 2024.',
                                                  'outcome_summary': 'NTA rescheduled the exam for affected Silchar '
                                                                     'candidates to 29 May 2024 in Pen and Paper '
                                                                     '(OMR-based) mode within Silchar, Assam.',
                                                  'outcome_document_ref': 'NTA Press Release dated 23 May 2024'}},
 'cancelled_before_templated': {'methodology_code': 'P9',
                                'rule_triggered': 'P9-CANCELLED-BEFORE-CONDUCT',
                                'entity_name': 'Haryana Public Service Commission (HPSC)',
                                'jurisdiction': 'State:Haryana',
                                'anomaly_metrics': {'authority': 'Haryana Public Service Commission (HPSC)',
                                                    'exam_name': 'Foreman Instructor',
                                                    'cycle_or_stage': 'Screening Test (2025)',
                                                    'exam_year': 2025,
                                                    'disruption_type': 'cancelled_before_conduct',
                                                    'disruption_reason_category': 'judicial_or_policy_review',
                                                    'scheduled_date': '2025-11-20',
                                                    'actual_or_event_date': '2025-08-26',
                                                    'schedule_summary': 'Haryana Public Service Commission (HPSC) '
                                                                        'notified/scheduled Foreman Instructor — '
                                                                        'Screening Test (2025) for 2025-11-20 in '
                                                                        'Haryana (State).',
                                                    'outcome_summary': 'Official Haryana Public Service Commission '
                                                                       '(HPSC) notice dated 2025-08-26 '
                                                                       'cancelled/withdrew the notified recruitment '
                                                                       'or examination for Foreman Instructor '
                                                                       '(Screening Test (2025), 2025-11-20) prior to '
                                                                       'conduct.',
                                                    'outcome_document_ref': 'Haryana Public Service Commission '
                                                                            '(HPSC) Official Notice (Advt. No. '
                                                                            '8412024))'}},
 'cancelled_before_post_code': {'methodology_code': 'P9',
                                'rule_triggered': 'P9-CANCELLED-BEFORE-CONDUCT',
                                'entity_name': 'Staff Selection Commission (SSC)',
                                'jurisdiction': 'Union of India',
                                'anomaly_metrics': {'authority': 'Staff Selection Commission (SSC)',
                                                    'exam_name': 'SSC Selection Posts Examination (Phase-XIII/2025)',
                                                    'cycle_or_stage': 'Phase-XIII/2025 — Post Code WR12025 (Multi '
                                                                      'Tasking Staff, WR)',
                                                    'exam_year': 2025,
                                                    'disruption_type': 'cancelled_before_conduct',
                                                    'disruption_reason_category': 'administrative_or_logistical',
                                                    'scheduled_date': '2025-07-24',
                                                    'actual_or_event_date': '2026-01-22',
                                                    'schedule_summary': 'SSC advertised recruitment under '
                                                                        'Phase-XIII/2025 — Post Code WR12025 (Multi '
                                                                        'Tasking Staff, WR) as part of SSC Selection '
                                                                        'Posts Examination (Phase-XIII/2025).',
                                                    'outcome_summary': 'Recruitment to Phase-XIII/2025 — Post Code '
                                                                       'WR12025 (Multi Tasking Staff, WR) was '
                                                                       'cancelled/withdrawn due to administrative '
                                                                       'constraints or vacancy withdrawal by the '
                                                                       'indenting User Department.',
                                                    'outcome_document_ref': 'SSC Cancellation Notice dated '
                                                                            '2026-01-22'}},
 'cancelled_after_hand_written': {'methodology_code': 'P9',
                                  'rule_triggered': 'P9-CANCELLED-AFTER-CONDUCT',
                                  'entity_name': 'Staff Selection Commission (SSC)',
                                  'jurisdiction': 'Union of India',
                                  'anomaly_metrics': {'authority': 'Staff Selection Commission (SSC)',
                                                      'exam_name': 'Combined Graduate Level Examination (CGLE)',
                                                      'cycle_or_stage': '2024 Cycle — Tier-II Typing Test (DEST) '
                                                                        'Shift-II Cancelled',
                                                      'exam_year': 2025,
                                                      'disruption_type': 'cancelled_after_conduct',
                                                      'disruption_reason_category': 'technical_or_cbt_glitch',
                                                      'delay_days': 13,
                                                      'scheduled_date': '2025-01-18',
                                                      'actual_or_event_date': '2025-01-31',
                                                      'schedule_summary': 'Technical glitches were reported during '
                                                                          'the CGLE 2024 Tier-II Typing Test (Data '
                                                                          'Entry Speed Test) held in Shift-II on 18 '
                                                                          'January 2025.',
                                                      'outcome_summary': 'SSC cancelled Shift-II DEST of 18 January '
                                                                         '2025 and conducted the re-examination on '
                                                                         '31 January 2025.',
                                                      'outcome_document_ref': 'SSC Important Notice dated 30 January '
                                                                              '2025 (CGLE 2024 DEST Re-Exam)'}},
 'partial_templated': {'methodology_code': 'P9',
                       'rule_triggered': 'P9-PARTIAL-REEXAM-OR-GLITCH',
                       'entity_name': 'Haryana Staff Selection Commission (HSSC)',
                       'jurisdiction': 'State:Haryana',
                       'anomaly_metrics': {'authority': 'Haryana Staff Selection Commission (HSSC)',
                                           'exam_name': 'Addendum for the Post of VLDA against Advt no. 07/2015, '
                                                        'Category no. 14 in compliance of Hon’ble High Court order '
                                                        'dated 18.01.2022 passed in CWP No.7138-2017 & CWP No.9997 '
                                                        'of 2017',
                                           'cycle_or_stage': 'Recruitment Examination (2024)',
                                           'exam_year': 2024,
                                           'disruption_type': 'partial_reexam_or_glitch',
                                           'disruption_reason_category': 'judicial_or_policy_review',
                                           'scheduled_date': '2024-08-13',
                                           'actual_or_event_date': '2024-08-13',
                                           'schedule_summary': 'Haryana Staff Selection Commission (HSSC) '
                                                               'notified/scheduled Addendum for the Post of VLDA '
                                                               'against Advt no. 07/2015, Category no. 14 in '
                                                               'compliance of Hon’ble High Court order dated '
                                                               '18.01.2022 passed in CWP No.7138-2017 & CWP No.9997 '
                                                               'of 2017 — Recruitment Examination (2024) for '
                                                               '2024-08-13 in Haryana (State).',
                                           'outcome_summary': 'Official Haryana Staff Selection Commission (HSSC) '
                                                              'record dated 2024-08-13 documents a partial '
                                                              're-examination, centre/shift re-conduct, or special '
                                                              'opportunity for Addendum for the Post of VLDA against '
                                                              'Advt no. 07/2015, Category no. 14 in compliance of '
                                                              'Hon’ble High Court order dated 18.01.2022 passed in '
                                                              'CWP No.7138-2017 & CWP No.9997 of 2017 (Recruitment '
                                                              'Examination (2024), originally 2024-08-13).',
                                           'outcome_document_ref': 'Haryana Staff Selection Commission (HSSC) '
                                                                   'Official Notice (Advt. No.07/2015)'}},
 'partial_hand_written': {'methodology_code': 'P9',
                          'rule_triggered': 'P9-PARTIAL-REEXAM-OR-GLITCH',
                          'entity_name': 'National Testing Agency (NTA)',
                          'jurisdiction': 'State:Uttar Pradesh',
                          'anomaly_metrics': {'authority': 'National Testing Agency (NTA)',
                                              'exam_name': 'UGC-NET (University Grants Commission National '
                                                           'Eligibility Test)',
                                              'cycle_or_stage': 'June 2024 Cycle — Varanasi Centre Cancellation '
                                                                '(Shift-II)',
                                              'exam_year': 2024,
                                              'disruption_type': 'partial_reexam_or_glitch',
                                              'disruption_reason_category': 'technical_or_cbt_glitch',
                                              'delay_days': 14,
                                              'scheduled_date': '2024-08-21',
                                              'actual_or_event_date': '2024-09-04',
                                              'schedule_summary': 'UGC-NET June 2024 (Rescheduled) Shift-II exam on '
                                                                  '21.08.2024 at Dr. Ghanshyam Singh College of '
                                                                  'Education, Varanasi could not be conducted due to '
                                                                  'technical glitches.',
                                              'outcome_summary': 'NTA scheduled a re-examination on 04 September '
                                                                 '2024 (Shift-II) for all candidates allotted to the '
                                                                 'cancelled Varanasi centre.',
                                              'outcome_document_ref': 'NTA Public Notice dated 30 August 2024 '
                                                                      '(4-Centre Re-Examination Schedule)'}}}

EXPECTED: dict[str, tuple[str, str]] = {'delayed_templated': (('Haryana PSC Treasury Officer and Assistant Treasury Officer preliminary exam delayed by 120 '
                       'days'),
                       ('The official schedule listed it for 16 Jul 2024. An official notice from the Haryana Public '
                       'Service Commission dated 13 Nov 2024, 120 days later, records a delay or revision such as a '
                       'revised result or corrigendum.')),
 'delayed_no_gap': ('Haryana PSC issued a revision notice for the Manager (Utility) exam',
                    ('An official notice from the Haryana Public Service Commission dated 18 Nov 2024 records a delay '
                    'or revision for this exam, such as a revised result or corrigendum. The record puts the reason '
                    'under court order or policy review.')),
 'delayed_vacancy_table': ('AIIMS Rishikesh had 126 of 355 faculty posts vacant in 2025-26',
                           ('A Lok Sabha reply dated 13 Feb 2026 lists 126 of 355 sanctioned faculty posts at AIIMS '
                           'Rishikesh as vacant in 2025-26. The audit files this under delayed recruitment.')),
 'postponed_hand_written': (('CBSE Central Teacher Eligibility Test (CTET), 22nd Edition: postponed 06 Sep to 12–13 '
                            'Dec 2026'),
                            ('Following Supreme Court directions in Review Petition (Civil) Diary No. 53434/2025 '
                            'reopening registrations, CBSE postponed the 22nd CTET by 97 days to 12th and 13th '
                            'December 2026.')),
 'postponed_templated': ('Meghalaya PSC Food Safety Officer screening test postponed by 7 days',
                         ('The official schedule listed it for 12 Jul 2025. An official notice from the Meghalaya '
                         'Public Service Commission dated 19 Jul 2025 postponed or deferred it. The record puts the '
                         'delay at 7 days.')),
 'rescheduled_templated': ('Kerala PSC 2026 interview rescheduled by 8 days',
                           ('An official notice from the Kerala Public Service Commission changed the exam date or '
                           'schedule from 1 Sep 2026 to 9 Sep 2026, a shift of 8 days.')),
 'rescheduled_hand_written': (('NTA CUET (UG) 2024 rescheduled by 5 days: Silchar, Assam, Bengali & Environmental '
                              'Studies'),
                              ('NTA rescheduled the exam for affected Silchar candidates to 29 May 2024 in Pen and '
                              'Paper (OMR-based) mode within Silchar, Assam.')),
 'cancelled_before_templated': ('Haryana PSC Foreman Instructor screening test cancelled before being held',
                                ('An official notice from the Haryana Public Service Commission dated 26 Aug 2025 '
                                'cancelled or withdrew the recruitment or exam before it was held. The official '
                                'schedule listed it for 20 Nov 2025.')),
 'cancelled_before_post_code': (('SSC cancelled Selection Posts Phase-XIII/2025 recruitment for Multi Tasking Staff, '
                                'WR'),
                                ('A cancellation notice dated 22 Jan 2026 cancelled or withdrew this post from the '
                                'Selection Posts Phase-XIII/2025 recruitment. The record does not give the number of '
                                'candidates affected.')),
 'cancelled_after_hand_written': (('SSC Combined Graduate Level Examination (CGLE) 2024: Tier-II typing test (DEST) '
                                  'shift-II cancelled'),
                                  ('Technical glitches were reported during the CGLE 2024 Tier-II Typing Test (Data '
                                  'Entry Speed Test) held in Shift-II on 18 January 2025. SSC cancelled Shift-II '
                                  'DEST of 18 January 2025 and conducted the re-examination on 31 January 2025.')),
 'partial_templated': ('Haryana SSC notified a partial re-test or special chance for the VLDA exam',
                       ('An official notice from the Haryana Staff Selection Commission dated 13 Aug 2024 records a '
                       'partial re-exam, a re-conduct for a centre or shift, or a special chance for candidates. The '
                       'record puts the reason under court order or policy review.')),
 'partial_hand_written': ('NTA UGC-NET June 2024: Varanasi centre cancellation (shift-II)',
                          ('NTA scheduled a re-examination on 04 September 2024 (Shift-II) for all candidates '
                          'allotted to the cancelled Varanasi centre.'))}


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
    for banned in ("—", ";", "!", "?", "||", "P9-", "fnd-"):
        assert banned not in headline + brief
    assert headline[0].isupper()


@pytest.mark.parametrize("name", sorted(CASES))
def test_deterministic(name: str) -> None:
    assert render(CASES[name]) == render(CASES[name])


@pytest.mark.parametrize(
    "finding",
    [
        {},
        {"methodology_code": "P9"},
        {"methodology_code": "P9", "anomaly_metrics": None},
        {"methodology_code": "P9", "anomaly_metrics": {}},
        {"methodology_code": "P9", "rule_triggered": None, "entity_name": None, "anomaly_metrics": {'actual_or_event_date': None,
 'authority': None,
 'cycle_or_stage': None,
 'delay_days': None,
 'disruption_reason_category': None,
 'disruption_type': None,
 'exam_name': None,
 'exam_year': None,
 'outcome_document_ref': None,
 'outcome_summary': None,
 'schedule_summary': None,
 'scheduled_date': None}},
        {"methodology_code": "P9", "rule_triggered": 7, "entity_name": ["x"], "anomaly_metrics": {'actual_or_event_date': [1, 2],
 'authority': {'a': 1},
 'cycle_or_stage': True,
 'delay_days': 3.5,
 'disruption_reason_category': -1,
 'disruption_type': 'object',
 'exam_name': float("nan"),
 'exam_year': ('t',),
 'outcome_document_ref': 'x',
 'outcome_summary': 0,
 'schedule_summary': [1, 2],
 'scheduled_date': {'a': 1}}},
        {"methodology_code": "P9", "anomaly_metrics": "not a dict", "title": 3, "summary": None},
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

def test_missing_dates_and_delay_still_give_a_sentence() -> None:
    finding = {
        "methodology_code": "P9",
        "rule_triggered": "P9-POSTPONED",
        "anomaly_metrics": {
            "authority": "Kerala Public Service Commission (Kerala PSC)",
            "exam_name": "Assistant Engineer",
            "cycle_or_stage": "Written Examination (2025)",
            "disruption_type": "postponed",
            "delay_days": None,
            "scheduled_date": "",
            "actual_or_event_date": "not a date",
        },
    }
    assert render(finding) == (
        "Kerala PSC Assistant Engineer written exam postponed",
        "An official notice from the Kerala Public Service Commission postponed or deferred it.",
    )


def test_unusable_exam_name_falls_back_to_year_and_stage() -> None:
    finding = dict(CASES["delayed_templated"])
    metrics = dict(finding["anomaly_metrics"])
    metrics["exam_name"] = "ADDENDUM_9D ADDENDUM_9A Download Hall Ticket 26/07/2024"
    finding["anomaly_metrics"] = metrics
    headline, _ = render(finding)
    assert headline == "Haryana PSC 2024 preliminary exam delayed by 120 days"


def test_null_delay_is_not_reported_as_zero_or_one_day() -> None:
    finding = dict(CASES["postponed_templated"])
    metrics = dict(finding["anomaly_metrics"])
    metrics["delay_days"] = None
    finding["anomaly_metrics"] = metrics
    headline, brief = render(finding)
    assert headline == "Meghalaya PSC Food Safety Officer screening test postponed"
    assert "day" not in brief


def test_loaded_summary_text_is_replaced_by_the_fixed_sentence() -> None:
    finding = dict(CASES["rescheduled_hand_written"])
    metrics = dict(finding["anomaly_metrics"])
    metrics["schedule_summary"] = "Officials were accused of a rigged schedule."
    metrics["outcome_summary"] = "Reports alleged irregularities and a scam at the centre."
    finding["anomaly_metrics"] = metrics
    headline, brief = render(finding)
    assert lint_neutrality(headline, brief, ["placeholder"]).passed
    assert brief == (
        "An official notice from the National Testing Agency changed the exam date or schedule "
        "from 24 May 2024 to 29 May 2024, a shift of 5 days."
    )
