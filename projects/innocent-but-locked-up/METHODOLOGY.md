# Methodology `P10` — Innocent But Locked Up

## Simple Hook
> **The court declared them acquitted and ordered release. Why did the prison keep them locked up until contempt was filed?**

## Objective
Audit **1,000 SHA-256-pinned Indian judicial, prison, police, human-rights, legal-aid, parliamentary, ministry, and CAG primary records** from the past five years (**2021–2026**) spanning **48 courts/statutory bodies**, **29 States & Union Territories** (plus Union of India), and **1,002 unique live-verified official PDFs** across **19 verified byte-stable domains**. Every task verifies exact primary-document bytes and tracks the four-stage custody-overrun chain:

1. **Stage 1 — Acquittal / Discharge / Release Order:** Court declared the accused acquitted, discharged, quashed detention, or directed release.
2. **Stage 2 — Continued Prison Detention / Non-Release:** Prison or custodial authorities retained the individual past the court's release order, quantifying documented overrun days where recorded.
3. **Stage 3 — Contempt of Court / Habeas Corpus Action:** Civil/criminal contempt, suo motu contempt, or habeas corpus enforcement initiated against custodial/state officials.
4. **Stage 4 — Subsequent Release & Remedial Outcome:** Verified release, judicial censure, FASTER / release-warrant compliance directions, and Article 21 compensation where ordered.

## Flagship Anchor Case (Task #1: `p10-court-2024-anchor-bashir-ahmad-koka-v-chandraker-bh-0001`)
- **Case:** Bashir Ahmad Koka v. Chandraker Bharti & Ors. (J&K Home Dept) — 98-Day Post-Quashment Illegal Confinement Contempt
- **Jurisdiction:** UT:Jammu and Kashmir · **Year:** `2024` · **Chain:** `4/4 stages verified (full_4_stage_chain)`
- **Stage 1 (Order — `2023-12-26`):** High Court of Jammu & Kashmir and Ladakh (`WP(Crl) No. 498/2022`) — Preventive detention order quashed by High Court of J&K and Ladakh in WP(Crl) No. 498/2022 on 26.12.2023 with direction to release the detenu forthwith.
- **Stage 2 (Continued Detention):** Preventive Custody under District Magistrate Anantnag / J&K Prisons / Home Department, UT of Jammu & Kashmir & District Magistrate, Anantnag — **98 days** (`2023-12-26 to 2024-04-03 (98 days)`); recorded reason: Detenu admittedly remained under illegal confinement for 98 days after detention order quashment on 26.12.2023 until contempt petition under Article 215 was filed.
- **Stage 3 (Contempt — `2024-04-04`):** High Court of Jammu & Kashmir and Ladakh (`CCP(S) No. 123/2024`) — Petitioner approached under Article 215 for willful disobedience of order dated 26.12.2023; Court recorded that detenu admittedly remained under illegal confinement over a period of 98 days and granted liberty to avail appropriate remedy.
- **Stage 4 (Release — `2024-04-03`):** Release verified (`True`) — DM Anantnag issued release order dated 03.04.2024 after Article 215 contempt proceedings; Court directed handover to legal heirs and expressly preserved liberty to claim compensation/remedy for 98 days of illegal incarceration.
- **Primary Verified Document:** https://indian-high-court-judgments.s3.amazonaws.com/data/pdf/year=2024/court=1_12/bench=kashmirhc/JKHC010014582024_1_2024-04-04.pdf
- **Paired Secondary Document:** https://indian-high-court-judgments.s3.amazonaws.com/data/pdf/year=2023/court=1_12/bench=kashmirhc/JKHC010033252022_1_2023-12-26.pdf

## Corpus Coverage (`1,000` Tasks · 2021–2026 · Live Byte-Stability Verified)
- **4-Stage Chain & Impact Totals:** `14` full 4-stage chains · `170` 3-stage chains · `90` verified contempt proceedings · `704` verified release records · `5,445` documented overrun days (median `88.5`d, max `2,676`d) · `INR 1,275,000` verified Article 21 compensation · `280` explicitly named prisons
- **Portal Category Breakdown (All 8 Official Categories):**
  - `court_portal` (Court Portals (SCI & High Courts)): `752` tasks (`63` cleared controls)
  - `parliament` (Parliament (Digital Sansad)): `130` tasks (`46` cleared controls)
  - `nhrc_shrc` (NHRC & State Human Rights Commissions): `66` tasks (`0` cleared controls)
  - `nalsa_slsa` (NALSA, SLSAs & UTRCs): `27` tasks (`13` cleared controls)
  - `govt_ministry` (Union & State Government Ministries): `12` tasks (`7` cleared controls)
  - `prison_portal` (Prison & Correctional Portals): `7` tasks (`3` cleared controls)
  - `police_portal` (Police & ICJS Portals): `4` tasks (`2` cleared controls)
  - `cag_audit` (CAG Audit Reports): `2` tasks (`0` cleared controls)
- **5-Year Temporal Coverage (`2021–2026`):**
  - `2021`: `49` tasks (`0` full 4-stage, `0` documented overrun days)
  - `2022`: `108` tasks (`1` full 4-stage, `1,203` documented overrun days)
  - `2023`: `143` tasks (`2` full 4-stage, `134` documented overrun days)
  - `2024`: `234` tasks (`8` full 4-stage, `343` documented overrun days)
  - `2025`: `270` tasks (`2` full 4-stage, `123` documented overrun days)
  - `2026`: `196` tasks (`1` full 4-stage, `3,642` documented overrun days)
- **Mechanism Breakdown:**
  - `post_acquittal_contempt_overrun`: `174`
  - `bail_or_release_order_contempt`: `123`
  - `warrant_or_faster_transmission_delay`: `30`
  - `statutory_commission_detention_finding`: `78`
  - `institutional_compliance_gap`: `461`
  - `timely_release_or_compliant` (Cleared benchmark): `134`
- **Deterministic Dry-Run (`as_of_date=2026-11-28`):** `866` leads · `134` cleared · `0` unevaluable (across all `1000` tasks)
- **Verified Byte-Stable Official Domains (1,002 unique pinned URLs across 19 domains):**
  - `indian-high-court-judgments.s3.amazonaws.com`: `733` unique SHA-256-pinned official documents
  - `sansad.in`: `91` unique SHA-256-pinned official documents
  - `nhrc.nic.in`: `60` unique SHA-256-pinned official documents
  - `bucketapi.rajyasabha.digital`: `39` unique SHA-256-pinned official documents
  - `cdnbbsr.s3waas.gov.in`: `28` unique SHA-256-pinned official documents
  - `indian-supreme-court-judgments.s3.amazonaws.com`: `17` unique SHA-256-pinned official documents
  - `www.mha.gov.in`: `8` unique SHA-256-pinned official documents
  - `gshrc.gujarat.gov.in`: `4` unique SHA-256-pinned official documents
  - `www.ncrb.mha.gov.in`: `4` unique SHA-256-pinned official documents
  - `api.sci.gov.in`: `3` unique SHA-256-pinned official documents
  - `bprd.mha.gov.in`: `2` unique SHA-256-pinned official documents
  - `d1jmc0u14z2tmp.cloudfront.net`: `2` unique SHA-256-pinned official documents
  - `jhalsa.org`: `2` unique SHA-256-pinned official documents
  - `keralaprisons.gov.in`: `2` unique SHA-256-pinned official documents
  - `kshrc.karnataka.gov.in`: `2` unique SHA-256-pinned official documents
  - `training.delhipolice.gov.in`: `2` unique SHA-256-pinned official documents
  - `doj.gov.in`: `1` unique SHA-256-pinned official documents
  - `keralapolice.gov.in`: `1` unique SHA-256-pinned official documents
  - `static.pib.gov.in`: `1` unique SHA-256-pinned official documents

## Deterministic Rules (`P10`)
1. **Post-Acquittal Detention Overrun & Contempt (`P10-POST-ACQUITTAL-CONTEMPT-OVERRUN`):** Continued prison detention documented after an acquittal, discharge, conviction set-aside, or detention-quashment order.
2. **Release-Order Custody Overrun & Contempt (`P10-RELEASE-ORDER-CONTEMPT-OVERRUN`):** Custodial non-compliance or contempt proceedings following a judicial release or bail-release direction.
3. **Warrant / FASTER Transmission Delay (`P10-WARRANT-TRANSMISSION-DELAY`):** Documented release-warrant (parwana), e-Prisons/ICJS, or Supreme Court FASTER electronic transmission breakdown delaying release.
4. **Statutory Commission Unlawful-Detention Finding (`P10-STATUTORY-UNLAWFUL-DETENTION`):** NHRC or SHRC statutory oversight record documenting unlawful detention or custodial release failures.
5. **Institutional Compliance Gap (`P10-INSTITUTIONAL-COMPLIANCE-GAP`):** Parliamentary, CAG, Ministry, Prison, Police, or NALSA/UTRC oversight finding on undertrial custody overruns and release compliance.
6. **Timely Release / Verified Compliance (`Cleared`):** Verified compliant releases, NIL FASTER backlog returns, and institutional compliance benchmarks (`134` tasks), published with equal journalistic standing after verifying all documentary tokens in fetched bytes.

## Legal, Privacy & Verification Safeguards
- **Fail-Closed Verification:** Every primary and secondary URL is live double-fetch byte-stability verified and SHA-256 pinned (`DigestMismatchError` on any byte drift). `P10` verifies machine-readable text (`>= 40` chars), chronological date consistency, and 100% verbatim documentary token presence in downloaded official bytes before emitting any lead or cleared verdict.
- **Zero CAPTCHA Bypasses (IT Act Sec. 43):** Only public, CAPTCHA-free, strict-TLS official portals and open-data mirrors are queried; CAPTCHA-gated eCourts/ePrisons searches and non-byte-stable dynamic HTML endpoints are excluded by design.
- **BNS Sec 356 Neutrality & DPDP Act 2023:** Zero forbidden accusatory terms and zero personal PII (Aadhaar, PAN, phone, email, bank details) across `projects/innocent-but-locked-up.json`, `coverage.json`, `documents.csv`, and `METHODOLOGY.md`. Every lead includes mandatory lawful benign explanations (concurrent remand/production warrants in separate cases, surety/identity verification, certified-copy/FASTER transmission gaps, or clerical warrant clarifications).
