# Methodology `P7` — Only One Bid

## Simple Hook
> **Which public purchases consistently attract just one bidder — and keep going to the same winner across 10 years of Indian government tenders?**

## Objective
Audit **10,000 real, SHA-256-pinned Indian government tender and contract-award records** spanning **2016–2026** across Union Ministries and **20+ States** to measure two core public-procurement market-structure patterns:
1. **Single-Bidder Tenders (`sb`):** Public tenders that received only one bid (`single_bid_received`), eliminated competing bidders at technical/pre-qualification evaluation leaving only one qualified firm (`single_qualified_after_rejection`), or proceeded through direct/single-source selection (`DIR`/`CDS`) without recorded PAC/OEM justification.
2. **Repeat-Winner Concentration (`rw`):** Procuring authorities where the same normalized supplier, contractor, or corporate entity repeatedly wins contracts (`>= 3` distinct tenders with elevated share, persistent `>= 5` wins, or `>= 3` consecutive wins).
3. **Combined Single-Bidder + Repeat-Winner (`bo`):** Tenders where both signals coincide — a single-bidder or sole-qualified award going to an entity with repeat wins at the same procuring authority.

## Anchor Case (Task #1: `p7-bo-2020-00001`)
- **Tender / Contract Reference:** `IN-MPRRDA-194572-CW-RFB` (World Bank STEP Notice `OP00113760`, Closed Project `P157054`)
- **Procuring Authority:** Madhya Pradesh Rural Roads Development Authority (State:Madhya Pradesh)
- **Fiscal Year:** `FY2020-21`
- **Scope:** Contract awarded & agreement executed - Up-gradation of Rural Roads of Gravel Standards Constructed under CMGSY to Black Top Standards under
- **Awarded Entity:** M/S OSHO ASSOCIATES
- **Awarded Contract Value:** INR 14.52 Cr
- **Observed Competition:** `5` bids submitted, `1` technically qualified (`single_qualified_after_rejection`)
- **Repeat-Winner Pattern:** `7` awards (`7` single-qualified) totaling INR 32.03 Cr within the same MPRRDA project cohort
- **Verifiable Public Record:** https://projects.worldbank.org/en/projects-operations/procurement-detail/OP00113760

## Corpus Coverage (`10,000` Tasks · `2016–2026` · Zero CAPTCHA · Tier-1 Pinned)
- **Cohort Split:** Combined (`bo`): `753` · Single Bidder (`sb`): `3,230` · Repeat Winner (`rw`): `6,017`
- **Coverage Breadth:** `8` procurement portals/registries · `234` procuring authorities · `1,227` distinct repeat-winning entities
- **Verified Tier-1 Sources (all shards `<= 30.03 MiB`, SHA-256 pinned in `documents.csv`):**
  1. **OCP Data Registry Pub #77** (`hptenders.gov.in`, Himachal Pradesh OCDS `full.jsonl.gz`, 2016–2020)
  2. **OCP Data Registry Pub #131** (`assamtenders.gov.in`, Assam State Finance Dept OCDS `full.jsonl.gz`, 2016–2022)
  3. **CivicDataLab Assam OCDS v3** (`assamtenders.gov.in`, `ocds_mapped_data_fiscal_year_2016_2022_v3.json.zip`, 2017–2023)
  4. **CivicDataLab IDS-DRR State AOC Mirrors** (`assamtenders.gov.in` 2016–2026, `hptenders.gov.in` 2016–2025, `tendersodisha.gov.in` 2019–2026, `etender.up.nic.in` 2018–2024 — strictly filtered to `Status == "Accepted-AOC"`)
  5. **World Bank Official India STEP Contract Awards API** (`search.worldbank.org/api/v2/procnotices`, closed/dormant pan-India project shards 2016–2025 + permanently immutable individual `id=OP...` notices 2025–2026)

## Forensic Data-Hygiene Safeguards
- **GePNIC Cover-Count Separation:** Cross-verifying CivicDataLab IDS-DRR CSVs against Assam OCDS v2/v3 and HP OCP #77 established that the CSV column `No of Bids Received` records GePNIC cover counts (`1` or `2` envelopes), not bidder counts. `P7` explicitly disables `bid_count` (`null`) on GePNIC AOC CSV shards and restricts winner attribution strictly to `Status == "Accepted-AOC"` (100.0% verified match across 2,514 cross-checked awards; `Rejected-*` rows are excluded).
- **World Bank STEP Section Hygiene:** Upstream corrupt notices where `bid_reference_no` does not appear in `notice_text` are dropped. `Evaluated Bidder(s)` lists non-winning evaluated bidders (`bids_received = 1 + len(evaluated) + len(rejected)`). Notices listing only `Awarded Bidder(s)` on `RFB`/`RFQ` without `Evaluated` or `Rejected` sections preserve `bids_received = None` (never misclassified as 1 bid).
- **Anti-Double-Counting Exposure:** Every finding records `monetary_exposure_inr` strictly equal to the target tender's own value; `winner_cohort_total_inr` is stored separately inside `anomaly_metrics`.
- **Legal & Privacy Charter:** Strict BNS Section 356 non-accusatory framing (`lint_neutrality`), mandatory lawful benign explanations (GFR 2017 Rule 166 PAC/OEM exemptions, genuine L1 competitive repeats, thin regional markets, emergency/retendered procurements), jurisdiction-aware statutory scope (Central GFR 2017 vs State Procurement Rules), and DPDP Act 2023 PII scrubbing (`scrub_pii`).
