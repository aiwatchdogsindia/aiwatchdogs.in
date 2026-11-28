# P4 · One Road, Two Bills: Union Government duplicate billing & dual-agency claims

This project audits official **Union Government Audit Reports** published by the Comptroller and Auditor General (CAG) of India over the past two years (`2024–2026`, tabled on or after `2024-11-27`).

## Why CAG Union Audit Reports

Raw PFMS / PAO payment vouchers and Measurement Books are not publicly downloadable without login, and CPPP/GeM portals only publish tender/award notices behind CAPTCHAs. The CAG's published Union Audit Reports are the authoritative, publicly accessible primary record documenting cases where executed works, bills, or claims were paid twice or claimed across two agencies, departments, ministries, or schemes.

## Deterministic rule (`OR1`)

Implemented in [`src/aiwatchdogs/engines/one_road_two_bills.py`](../../src/aiwatchdogs/engines/one_road_two_bills.py):

1. **Non-overlapping page blocks**: Each substantive PDF page is partitioned along numbered audit section headings and paragraph boundaries into non-overlapping line ranges.
2. **Boilerplate & methodology exclusion**: Table of Contents lines, figure captions, and generic audit-criteria/objective sentences (`to verify whether...`, `prevent duplicate...`) are excluded.
3. **Legitimate co-funding exclusion**: Standard cost-sharing and joint-funding provisions (`cost sharing`, `60:40`, `50:50`, `centre-state share`, `matching grant`, `VGF`, `pooled funding`) are explicitly cleared unless an explicit double-billing or paid-twice irregularity appears.
4. **Two-tier signal detection**:
   - `duplicate_payment_or_billing`: explicit double payment, duplicate billing/voucher/invoice, paid twice, billed twice, reimbursed twice, two receipts/bills/vouchers.
   - `dual_agency_or_scheme_overlap`: two agencies/departments/ministries/schemes or already-executed overlapping works co-occurring with an audit irregularity marker and spending context.
5. **Source attribution**: Name a payment actor only when explicit in the triggering sentence or in the opening of its enclosing section. Cite the section opening separately when it supplies the actor. Use a report or sector label if the source cannot resolve the subject.
6. **Amount scope**: Use a single rupee amount tied to the signal in the triggering sentence or an explicitly linked follow-up. Leave recoveries, mixed totals, and unbound or competing amounts undisclosed. Mark a candidate in a flattened audit table unevaluable when row attribution is unresolved.
7. **Repeated source mentions**: If an executive-summary paragraph explicitly cites the detailed section and repeats the same amount and evidence in the same report, retain both citations and count the exposure once in totals.

## Coverage

- **Reports audited:** 44 of 53 Union listings
- **Pages read:** 7,085
- **Non-overlapping blocks:** 17,203
- **Swarm tasks:** 10,000
- **Document index:** [`documents.csv`](./documents.csv)
- **Coverage JSON:** [`coverage.json`](./coverage.json)
