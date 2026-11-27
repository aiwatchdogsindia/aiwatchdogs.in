# Methodology `P8` — Sovereign Lies

## Simple Hook
> **The government made a promise when it launched the scheme. Did it keep it?**

## Objective
Audit **1,000 SHA-256-pinned Indian government scheme commitments** spanning the **Union Government** (`500` tasks) and **all 36 States & Union Territories** (`419` State + `81` UT tasks across all 28 States and 8 UTs) covering **324 unique schemes** and **670 unique live-verified official documents**. Every task compares an explicit launch promise (Union/State Budget, Finance Bill, RBI/NSE SGB notification, Parliamentary answer, or CAG-documented Government Order) against a subsequent statutory amendment, benefit cut, premature discontinuation, eligibility narrowing, exit restriction, or documented arrears record — failing closed as `unevaluable` whenever downloaded bytes fail SHA-256 verification or lack required verbatim documentary tokens.

## Flagship Anchor Case (Task #1: `p8-union-sgb-anchor-0001`)
- **Scheme:** Sovereign Gold Bond (SGB) Scheme — Capital Gains Tax Exemption on Redemption
- **Scope:** All Active Tranches (2015-16 Series I to 2023-24 Series IV) — Sec 47(viic) / Sec 70(1)(x)
- **Jurisdiction:** Union of India
- **Authority:** Ministry of Finance (Department of Economic Affairs) & Reserve Bank of India
- **Original Launch Promise (`2016-02-29`):** Finance Bill 2016 (Clause 28 inserting Section 47(viic) of the Income-tax Act, 1961) and subsequent Ministry of Finance parliamentary replies (LS Starred Q 295, 2021) exempted capital gains arising on redemption of Sovereign Gold Bonds by any individual, while confirming SGBs are tradable on the secondary market without restricting the redemption tax exemption to primary-issue subscribers.
- **Subsequent Walk-Back (`2026-02-01`):** Finance Bill, 2026 (Clause 35 amending Section 70(1)(x) of the Income-tax Act, 2025, effective 1 April 2026) narrowed the capital gains tax exemption on SGB redemption exclusively to individuals who subscribed at the time of original issue and held continuously until redemption on maturity, revoking tax-free maturity redemption for existing secondary-market SGB holders across all series.
- **Promised vs. Revised Metric:** `0% LTCG tax on maturity redemption for any individual holder (primary or secondary)` → `0% LTCG restricted to original subscribers held continuously till maturity; secondary buyers taxed from 1 Apr 2026`
- **Primary Launch Record:** https://www.indiabudget.gov.in/budget2016-2017/ub2016-17/fb/bill.pdf
- **Walk-Back Record:** https://www.indiabudget.gov.in/doc/Finance_Bill.pdf

## Corpus Coverage (`1000` Tasks · All 28 States + 8 UTs + Union · Live Byte-Stability Verified)
- **Jurisdiction Tier Split:** Union of India: `500` · States (28/28): `419` · Union Territories (8/8): `81`
- **Sovereign Gold Bond Cohort:** `73` tasks (Flagship Anchor #1 + ALL 67/67 SGB Tranches 2015-16 Series I through 2023-24 Series IV + CAG Gold Reserve Fund & statutory indexation/discontinuation checks)
- **Walk-Back Mechanism Breakdown:**
  - `retroactive_tax_reversal`: `62`
  - `midstream_benefit_cut`: `143`
  - `premature_discontinuation`: `113`
  - `eligibility_narrowing`: `120`
  - `lockin_or_exit_restriction`: `18`
  - `arrears_or_unfunded_promise`: `425`
  - `grandfathered_or_honored` (Cleared benchmark): `119`
- **Deterministic Dry-Run (`as_of_date=2026-11-27`):** `881` leads · `119` cleared · `0` unevaluable (across all `1000` tasks)
- **Verified Byte-Stable Source Domains (670 unique pinned URLs):**
  - `sansad.in`: `441` unique SHA-256-pinned documents
  - `cag.gov.in`: `110` unique SHA-256-pinned documents
  - `prsindia.org`: `58` unique SHA-256-pinned documents
  - `nsearchives.nseindia.com`: `25` unique SHA-256-pinned documents
  - `www.indiabudget.gov.in`: `20` unique SHA-256-pinned documents
  - `bucketapi.rajyasabha.digital`: `11` unique SHA-256-pinned documents
  - `apfinance.gov.in`: `1` unique SHA-256-pinned documents
  - `d1jmc0u14z2tmp.cloudfront.net`: `1` unique SHA-256-pinned documents
  - `static.pib.gov.in`: `1` unique SHA-256-pinned documents
  - `transport.delhi.gov.in`: `1` unique SHA-256-pinned documents
  - `wcd.delhi.gov.in`: `1` unique SHA-256-pinned documents

## Deterministic Rules (`P8`)
1. **Retrospective Tax Reversal (`P8-RETROACTIVE-TAX-REVERSAL`):** Tax exemption or fiscal treatment promised at launch is later narrowed, capped, or withdrawn.
2. **Mid-Stream Benefit Cut (`P8-MIDSTREAM-BENEFIT-CUT`):** Interest rate, subsidy share, payout, or entitlement reduced during an active scheme tenure.
3. **Premature Discontinuation (`P8-PREMATURE-DISCONTINUATION`):** Notified scheme, tranche series, fellowship, or concession suspended or wound up ahead of schedule.
4. **Eligibility Narrowing (`P8-ELIGIBILITY-NARROWING`):** Post-launch taxpayer bars, income ceilings, or beneficiary pruning narrowing original launch eligibility.
5. **Lock-In or Exit Restriction (`P8-LOCKIN-OR-EXIT-RESTRICTION`):** Lock-in lengthened or premature exit/redemption windows tightened after launch.
6. **Arrears or Unfunded Promise (`P8-ARREARS-OR-UNFUNDED-PROMISE`):** Notified benefit, pension, waiver, or subsidy left unpaid, unimplemented, or in documented arrears.
7. **Grandfathered or Honored (`Cleared`):** Verified launch terms honored or existing holders grandfathered intact (`119` tasks), published with equal journalistic standing.

## Legal, Privacy & Verification Safeguards
- **Fail-Closed Verification:** Every primary and secondary URL is double-fetch byte-stability verified and SHA-256 pinned (`DigestMismatchError` on any byte change). `P8` confirms `>= 40` chars of readable text and 100% verbatim token presence in the verified document bytes before emitting any lead or cleared verdict.
- **BNS Sec 356 Neutrality & DPDP Act 2023:** Zero forbidden accusatory terms and zero personal PII across `projects/sovereign-lies.json`, `coverage.json`, `documents.csv`, and `METHODOLOGY.md`. Every lead emits mandatory lawful benign explanations (parliamentary statutory amendment power, FRBM fiscal consolidation, bullion/macroeconomic cycles, arbitrage prevention, and e-KYC deduplication).
