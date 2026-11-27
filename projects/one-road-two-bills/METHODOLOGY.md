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
5. **Zero fabricated figures**: Only rupee figures explicitly printed in the matched section are cited.

## Coverage

- **Reports audited:** 44 of 53 Union listings
- **Pages read:** 7,085
- **Non-overlapping blocks:** 17,203
- **Swarm tasks:** 10,000
- **Document index:** [`documents.csv`](./documents.csv)
- **Coverage JSON:** [`coverage.json`](./coverage.json)
