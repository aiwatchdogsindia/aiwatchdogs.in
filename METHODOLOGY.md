# Audit Methodology & Deterministic Verification Specification

**Golden Rule:** Large Language Models (LLMs) may optionally summarize statutory rules or propose plausible benign explanations as clearly labeled editorial interpretation. **Every numeric red-flag calculation MUST execute inside a deterministic, version-hashed Python rule engine (`aiwatchdogs.engines`)** so that any independent contributor node running on a ₹0 CPU-only machine can reproduce the exact numbers from the same byte-verified artifact.

---

## 1. Public Data Ingestion, Provenance & Defensive Loading (IT Act Sec 43 & DPDP Act 2023)

All Indian public data connectors in `aiwatchdogs.connectors` follow a strict three-tier legality matrix:

| Tier | Ingestion Mode | Policy & Rate Limits | Examples |
|---|---|---|---|
| **Tier 1** | Automated Direct Open API / Bulk Open Files | Zero CAPTCHA, zero auth wall; polite User-Agent (`AIWatchdogs-India-OpenAudit/0.1`); structured JSON, CSV, XLSX, or official PDF. | **CivicDataLab OCDS India** (`assam-tenders-data`, `himachal-pradesh-health-procurement-OCDS`), **OpenBudgetsIndia** (`openbudgetsindia.org` CKAN API), **data.gov.in** (OGD REST API), **CAG of India** (`cag.gov.in` PDFs), **Union Budget** (`indiabudget.gov.in`), **CGA Monthly Accounts** (`cga.nic.in`), **Digital Sansad** (`sansad.in`), **ECI Electoral Bonds** (`eci.gov.in`). |
| **Tier 2** | Polite Rate-Limited Public Dashboards / Official Exports | Publicly accessible without login; strictly honors `robots.txt`, conservative rate caps, or official MIS CSV/XLS exports. | **PFMS** (`pfms.nic.in`), **MGNREGA MIS** (`nrega.nic.in`, `mnregaweb4.nic.in`), **PMGSY OMMAS** (`ommas.nic.in`), **MPLADS e-SAKSHI** (`mplads.mospi.gov.in`), **National CSR Portal** (`csr.gov.in`), **MyNeta / ADR** (`myneta.info`), **APMS** (`apms.nic.in`), **eGazette** (`egazette.gov.in`), **CIC** (`cic.gov.in`). |
| **Tier 3** | Human-Downloaded Official Exports & Open Mirrors ONLY | **Zero automated CAPTCHA solving or authentication bypass.** Agents ingest only user-downloaded official exports (`aiwatchdogs audit-file` with explicit local-file mode) or lawful open mirrors. | **CPPP** (`eprocure.gov.in`), **State GePNIC Portals** (`mahatenders.gov.in`, `assamtenders.gov.in`, `hptenders.gov.in`), **GeM** (`gem.gov.in`), **MCA21** (`mca.gov.in`). |

Every ingested artifact produces a `LoadedDataset` enforcing:
* **Fail-closed SHA-256 verification:** When `expected_sha256` (primary) or `secondary_expected_sha256` (secondary join) is provided, any mismatch raises `DigestMismatchError` immediately — never log-and-continue. Cache hits always revalidate SHA-256 against both metadata and expected digest.
* **Separated provenance:** Stores `official_publisher`, `source_landing_page`, `mirror_name`, `retrieval_url`, `observed_sha256`, `byte_size`, `fetched_at`, and `source_locator` separately — civic mirror URLs are never labeled as primary `.gov.in` endpoints.
* **Privacy & raw-cache restrictions:** All parsed records and text pass through recursive DPDP/Aadhaar PII scrubbing (`aiwatchdogs.core.safety.scrub_pii_in_obj`). Raw disk caching is opt-in, uses `0o700`/`0o600` permissions, and refuses to write raw bytes to disk if any PII is detected.
* **Defensive unattended loading:** `load_dataset()` defaults to `allow_local_files=False` (untrusted task context cannot override it), requires `https://` public hosts (blocking localhost, private/loopback/link-local/metadata IPs), enforces bounded download size (`100 MiB` default), ZIP/XLSX decompression size (`250 MiB`) and ratio (`100×`) limits, XML DTD/ENTITY rejection, and row/page parse caps.

---

## 2. Deterministic Audit Methodologies (`M1` – `M8`)

All eight methodologies are registered in `aiwatchdogs.engines.registry.METHODOLOGY_CATALOG` and executed via `run_engine(methodology_code, records, context, as_of_date=...)`. Every `EngineRunResult` records:
* `engine_version` (`2.0.0`), `ruleset_version` (`M1-2026.11-v2`..`M8-2026.11-v2`), and explicit `as_of_date` (no implicit `date.today()`).
* `fingerprint_manifest` and combined `script_hash` hashing the engine module, `connectors/loader.py`, `core/safety.py`, `engines/registry.py`, `ruleset_version`, canonical rule config, and `as_of_date`.
* Separate `records_scanned`, `cleared_count`, `unevaluable_count`, `monetary_audited_inr`, `amount_type`, and neutrality/PII-checked `AuditLeadCandidate` objects carrying `source_locator`, `fiscal_year_start`, `fiscal_year_end`, `amount_type`, and `ruleset_version`.

### `M1` — Single-Bidder & Tailored-Spec Concentration
* **Statutory Anchors:** General Financial Rules (GFR) 2017 Rule 166 (Single Tender Enquiry) or equivalent State Procurement Rules; Central Vigilance Commission (CVC) Single-Tender Guidelines; OECD Procurement Integrity Red Flags.
* **Target Sources:** CivicDataLab OCDS India (Assam, Himachal Pradesh), CPPP / State GePNIC tender exports.
* **Bidder Coverage & Disposition Rules:**
  * Strictly separates **positive bidder counts** (`denominator`, `bid_count >= 1`), **literal zero bidder counts** (`zero_bid_count`, `bid_count == 0`), and **blank/missing counts** (`unknown_bid_count`, `bid_count is None`).
  * Buyer groups with `denominator == 0` (no positive bidder counts) are assigned an explicit **`unevaluable`** disposition (`unevaluable_count`) and are **never** added to `cleared_count`.
  * Publishes `numerator` (single-bid count), `denominator` (positive bidder counts), `multi_bid_count`, `zero_bid_count`, `unknown_bid_count`, `positive_bid_coverage_pct`, full fiscal-year span (`fiscal_year_start` to `fiscal_year_end`), and `amount_type` (`award_value` vs `tender_estimate`).
* **Deterministic Triggers:**
  1. **Buyer Single-Bid Share:** For a procuring entity with positive-bid competitive tender count `denominator >= min_tenders` (default `4`), flag when `numerator / denominator > 0.35` (**> 35%**).
  2. **Repeat Single-Bid Vendor:** Flag when the same identified supplier wins **`>= 3` single-bid contracts** from the same buyer.
  3. **Compressed Bid Window:** Flag when `>= 2` open competitive tenders have submission window **`< 14 days`** paired with a single-bidder outcome.
* **Mandatory Benign Explanations Considered:** Proprietary OEM equipment covered by a valid GFR Rule 166 / state-rule Proprietary Article Certificate (PAC), genuine emergency/disaster relief procurement, remote geography with sparse vendor base, or re-tendered bids following prior failed open tenders.

### `M2` — Split-Tendering & Jurisdiction-Aware Threshold Hugging
* **Statutory Anchors:** GFR 2017 Rules 149, 154, 155, 161 (pre- and post-10 July 2024 MoF DoE O.M. No. F.1/3/2024-PPD); Assam Public Procurement Act, 2017 & Assam Public Procurement Rules, 2020 (subject to Assam Delegation of Financial Powers Rules); CVC Circulars against demand splitting.
* **Strict Field Separation:**
  * `release_date` (OCDS export date), `tender_published_date`, `bid_opening_date`, and `award_date` are kept strictly distinct — `release_date` is **never** mapped into `award_date`.
  * `tender_estimate_inr` and `award_value_inr` are kept strictly distinct — tender estimates are **never** treated as award values.
* **Jurisdiction & Effective-Date Threshold Schedules (Reconciled with `engines/procurement.py`):**

| Schedule | Applicable Scope & Effective Date | Tracked Threshold Ceilings |
|---|---|---|
| **Central GFR 2017 (Pre-Jul 2024)** | Union of India (`2017-03-08` to `2024-07-09`) | **₹25,000** (R149(i)/154 GeM Direct / Without Quotation) · **₹2.5 Lakh** (R155 Local Purchase Committee) · **₹5 Lakh** (R149(ii) GeM L1 vs Online Bidding/RA) · **₹25 Lakh** (R161 Limited vs Open Advertised Tender) |
| **Central GFR 2017 (Post-Jul 2024)** | Union of India (`2024-07-10`+, MoF DoE O.M. F.1/3/2024-PPD) | **₹50,000** (R149(i)/154 GeM Direct / Without Quotation) · **₹5 Lakh** (R155 Local Purchase Committee) · **₹10 Lakh** (R149(ii) GeM L1 vs Online Bidding/RA) · **₹50 Lakh** (R161 Limited vs Open Advertised Tender) |
| **Assam APPR 2020 Reference** | `State:Assam` (`2020-07-01`+, Assam Public Procurement Rules 2020 & DFP Rules) | **₹25 Lakh** (Assam e-Procurement / Open Tender reference ceiling; `statutory_applicability_verified=False` until category-specific DFP rule & procurement route are verified) |
| **Other State Reference** | Other States where state financial ruleset is unencoded | Reference price-band benchmarks explicitly flagged `threshold_scope="reference_benchmark_only_state_rules_unverified"` and `statutory_applicability_verified=False` |

* **Two Strictly Separated Outputs:**
  1. **Split-Tendering / Order Cluster (`splitting_claim_supported=True`):** Requires genuine **`award_value`** AND genuine **`award_date`/`po_date`** on `>= 3` records priced in **`85%`–`99.9%` of the resolved ceiling `T`** within a rolling **60-day window** whose sum exceeds `T`. Undated groups NEVER enter temporal clustering and NEVER produce `0-day` clusters.
  2. **Tender-Estimate Price-Band Observation (`splitting_claim_supported=False`):** When source rows supply only `tender_estimate` values without `award_date` / `award_value` (such as Assam OCDS v2 CSV), the engine retains a non-accusatory price-band observation labeled `amount_type="tender_estimate"`, sets `span_days=None` if undated (reporting distinct `bid_opening_date` spans separately if present), caps severity at `low`/`medium`, and explicitly records `governing_instrument`, `threshold_scope`, and `threshold_effective_date`.
* **Mandatory Benign Explanations Considered:** Phased quarterly budget releases, separate sub-divisions raising independent indents, distinct State DFP thresholds, or tender estimates that were never awarded or were issued across months/years.

### `M3` — Repeat-Winner Concentration & Collusion Signatures
* **Statutory Anchors:** Competition Commission of India (CCI) Bid-Rigging & Public Procurement Guidelines; OECD Guidelines for Fighting Bid Rigging.
* **Target Sources:** OCDS procurement packages with awarded supplier records, GeM / CPPP multi-bidder evaluation sheets.
* **Deterministic Triggers:**
  1. **Herfindahl–Hirschman Index (HHI):** Over identified suppliers with positive `award_value` in each `(buyer_entity, category)` group (`n >= 4`), compute vendor award shares $s_i$ and $\text{HHI} = \sum s_i^2$. Flag when **`HHI > 0.35`** (`hhi_triggered`).
  2. **Top-Supplier Share:** Flag when any single identified supplier holds **`> 40%`** of awarded value (`top_supplier_share_triggered`).
  3. **Narrow Bid-Spread Clustering:** Flag when `>= 3` multi-bidder tenders (and `>= 60%` of observed spreads) show L2–L1 spread `<= 3%` (`narrow_spread_triggered`).
* **Required Transparency Fields:** Explicitly emits `thresholds_triggered` (`hhi` vs `top_supplier_share` vs `narrow_bid_spread`), full fiscal window (`fiscal_year_start` to `fiscal_year_end`), and the **excluded share** (`excluded_row_count`, `excluded_row_share_pct`, `excluded_estimate_value_inr`) of unawarded or unidentified-supplier rows.
* **Mandatory Benign Explanations Considered:** Niche technical specialization, multi-year rate contracts or framework agreements, thin regional supplier ecosystems, or standardized catalog/Schedule-of-Rates pricing.

### `M4` — Unit-Price Outlier, Award-vs-Estimate Variance & Post-Award Cost Escalation
* **Statutory Anchors:** GFR 2017 Rule 173; GeM Rate Benchmarking & Reasonability Guidelines; CVC Works & Procurement Manuals.
* **Deterministic Triggers:**
  1. **Robust Unit-Price Outlier (MAD & Median Ratio):** Normalized by `(item_category, unit)`. Flag when `(x_i - median) / MAD > 2.5` (with `peer_n >= 4` and ratio `> 1.35`) OR `x_i / median > 1.75`.
  2. **Post-Award Escalation (`is_final_cost_overrun=True`):** Only when explicit `final_value_inr` / `revised_value_inr` exists in the source record and `(final_value - award_value) / award_value > 0.20`.
  3. **Award vs Tender-Estimate Variance (`is_final_cost_overrun=False`):** When explicit `award_value` exceeds `tender_estimate` by `> 20%` (and `>= ₹1 Lakh`). Explicitly labels `award_value_inr`, `tender_estimate_inr`, `tender_estimate_date`, `award_date`, and `final_cost_date=None`, and never describes this comparison as a final contract cost overrun without completion/disbursement records.
* **Mandatory Benign Explanations Considered:** Outdated Schedule-of-Rates estimates, bundled multi-year AMC/warranty, high-altitude/remote logistics surcharges, approved scope revisions, or statutory GST/price-variation clauses.

### `M5` — "March Rush" Expenditure Pacing & Same-Year Budget Variance
* **Statutory Anchors:** GFR 2017 Rule 62(3); Ministry of Finance Cash Management System (CMS) Circulars; Public Accounts Committee (PAC) Explanatory Note Threshold (`>= ₹100 Cr`).
* **Target Sources:** CGA Monthly Accounts, OpenBudgetsIndia / State OBDS workbooks, State Monthly Treasury records, Union Budget Expenditure Profile.
* **Strict Reconciliation & Unknown Preservation:**
  * **OBDS Workbook Ingestion:** Requires **matched agency/department, account head (major head), explicit unit (`budget_amount_unit`), and same fiscal period** before computing any variance. Never aggregates conflicting agencies/heads under one Demand label, never guesses blank units, and **never compares `actuals_last_audited_year` (FY `T-2`) against `budget_estimates_previous_financial_year` / `revised_estimates_previous_financial_year` (FY `T-1`)** as same-year actual vs estimate. Unmatched or unit-less OBDS rows go to `unevaluable_count`.
  * **Preserving Unknowns & PAC Trigger:** Unobserved `be_inr`, `re_inr`, `actual_inr`, `mof_waiver_recorded`, and `pac_explanatory_note_tabled` are preserved strictly as `None` (never defaulted to `0` or `False`). A missing PAC explanatory note (`pac_explanatory_note_missing=True`) is flagged **only** when a comparable same-year budget variance `>= ₹100 Cr` is observed AND `pac_explanatory_note_tabled` is explicitly `False`.
* **Deterministic Triggers:**
  1. **Q4 Ceiling Breach:** `Q4 spend / annual outlay > 33%` (where MoF/Finance waiver is not recorded `True`).
  2. **March Ceiling Breach:** `March spend / annual outlay > 15%`.
  3. **Same-Year BE/RE vs Actuals Variance:** `|Actual - BE| / BE > 25%`, `|Actual - RE| / RE > 25%`, or `|RE - BE| / BE > 25%` across observed same-year figures.
* **Mandatory Benign Explanations Considered:** Formal CapEx relaxation / CMS waivers, late-session Supplementary Demands for Grants, statutory year-end transfers, or SNA accounting adjustments.

### `M6` — CAG Linked Audit Para & Dated Disposition Tracker
* **Statutory Anchors:** Articles 148–151 of the Constitution of India; CAG of India Audit Reports; APMS & Parliamentary PAC Action Taken Reports (ATRs).
* **Strict Extraction & Linkage Discipline:**
  * **Amount Linkage:** Ingested CAG PDFs require an **explicit heading, sentence, or table-cell linkage** tying a rupee figure to an audit observation typology (`amount_linkage` = `heading_explicit` | `sentence_explicit` | `table_cell_explicit`). Adjacent Figure/Chart blocks and macro fiscal aggregates (e.g., 14-day Treasury Bill repayments, gross borrowings, total receipts) are strictly excluded and never attributed to an observation paragraph. Unlinked amounts remain `None` (`unrecorded`).
  * **Dated Disposition Evidence:** Never defaults ATN status to `pending` or fabricates `years_pending = 2`. Sets `atn_status = "unrecorded"` and `years_pending = None` unless explicit disposition wording and explicit dated follow-up duration/milestones appear in the cited record (`disposition_evidence_date`). Preserves `pac_atr_filed = None` when unrecorded.
* **Mandatory Benign Explanations Considered:** ATN uploaded on APMS awaiting CAG vetting, post-cutoff partial recovery or commissioning, sub-judice arbitration, or separate APMS/PAC records needed where report PDFs omit follow-up disposition dates.

### `M7` — Welfare & Infrastructure Scheme Delivery Verification (`PMGSY`, `MPLADS`, `MGNREGA`)
* **Statutory Anchors:** PMGSY Programme Guidelines & OMMAS Norms; MPLADS Guidelines (MoSPI e-SAKSHI, 12-month norm); MGNREGA 2005 Section 17 (Social Audits), Section 22 & Schedule II (60:40 Wage-to-Material Ratio).
* **Deterministic Triggers & Data Honesty:**
  1. **PMGSY Rural Roads:** Flags `financial_progress >= 90%` AND `physical_progress_km < 70%` **only when physical completion (`completed_km` / `physical_progress_pct`) is actually observed** — missing physical progress is preserved as `None` (routed to `unevaluable_count`, never defaulted to `0`). Explicitly records `aggregate_level` (`district_phase_aggregate` vs `package_work`), `financial_unit`, `physical_unit`, and `is_fallback_identifier` / `identifier_source` when an ID is synthesized from phase + district.
  2. **MPLADS Constituency Works:** Computes `months_elapsed` strictly from an explicit deterministic **`as_of_date`** (never `date.today()`). Ongoing works lacking both `months_elapsed` and `as_of_date` are marked `unevaluable` rather than evaluated nondeterministically.
  3. **MGNREGA Expenditure & Social Audit:** Flags `material_share > 40%` (60:40 ratio) or unrecovered R9.2 Social Audit amounts (`>= ₹1 Lakh` and `>= 50%` pending), with explicit financial unit and aggregate-level metadata.
* **Mandatory Benign Explanations Considered:** OMMAS Measurement Book lag, upfront bridge/cross-drainage expenditure, revised sanctions, forest/land clearance holds, or district-level fiscal-year MGNREGA material pooling.

### `M8` — Corporate–Electoral–Procurement Proximity Disclosure Lead
* **Statutory Anchors:** Companies Act 2013 / MCA21 public filings; Supreme Court WP(C) 880/2017 SBI/ECI Electoral Bond disclosures; GFR conflict-of-interest norms.
* **Strict Join & Disclosure Honesty:**
  * **No Fabricated Exact CINs:** The ECI/SBI Electoral Bond file contains purchaser names and **no CIN**. When joined with MCA CSR records, `connectors/loader.py` and `engines/proximity.py` **never copy a CSR record's CIN onto a bond row as an exact CIN match** without a corroborating primary identifier.
  * Explicitly emits `join_basis: "normalized_name"` (vs `"exact_cin"` only when corroborated), `exact_identifier_verified`, `join_ambiguous`, `join_candidate_count`, `join_candidate_cins`, `bond_source_cin` (`None` when absent), `matched_csr_cin`, separate source company/purchaser names, and describes monetary totals as **`disclosed_bond_value`** (`disclosed_bond_value_inr`, `disclosed_bond_value_crore`, `amount_type="disclosed_bond_value"`).
  * Explicitly states `procurement_relationship_established: False` for electoral-bond × CSR disclosures (no tender award is present in those inputs).
  * Also supports procurement-side proximity checks (`< 180` day newly incorporated awardees, shared-DIN/address competing bidders) under strictly neutral **"Disclosure / Proximity Lead"** framing.
* **Mandatory Benign Explanations Considered:** Lawful corporate political contributions under the then-prevailing scheme, normalized-name linkage requiring corroborating corporate identifiers, SPV/JV consortia, or routine group structures disclosed in conflict-of-interest filings.

### `P1` — Police Budget-Head Variance (official budget documents)

The check used by the live police finances project (`engines/police.py`, ruleset `P1-2026.11-v1`). It makes no judgement of its own: it reads figures that an official budget document prints and reports where they diverge.

* **Statutory Anchors:** Constitution Articles 202–203 (Annual Financial Statement and Demands for Grants); GFR 2017 Rule 62; List of Major and Minor Heads, Major Head 2055 "Police" and 4055 "Capital Outlay on Police".
* **Input:** one official PDF, pinned by its exact link and SHA-256. Each task names the page and the printed line of every head it reads (`context.budget_lines`), which figure sits at which position on that line (`values`), the unit the book uses (crore, lakh or thousand), and the fiscal year of each figure.
* **How a line is read:** the first line on the named page that matches the task's pattern. Its whitespace-separated tokens that are numbers are counted from zero; Indian digit grouping (`1670,92.50`) is accepted; `..`, `-` and `–` standing alone count as nil; negative figures, `...` and `--` are not counted. Where a book prints a row one cell per line, the task sets `lines_before` / `lines_after` (0 to 12) and the matched line is read together with that many neighbouring lines, in page order.
* **Comparisons (set per task, the same across the project):** revised estimate against budget estimate of the same year (flag at 10%); the next budget against the revised estimate (15%); the budget estimate against the previous year's accounts (15%); and accounts against the budget estimate of the accounts year (10%), where that estimate is cited from the previous edition of the same document. A head whose revised estimate repeats its budget estimate exactly is reported separately, at low severity.
* **Outcomes:** a divergence at or above the threshold becomes a neutral lead that quotes the page and the printed line. Comparisons inside their thresholds count as cleared. A line that cannot be found as specified is **unevaluable**: it is never guessed, and a task in which nothing could be read is handed back to the queue rather than reported as cleared.
* **One parser for every node:** `pypdf` is pinned to an exact version in `pyproject.toml`, because the auditor and all peer verifiers must extract the same text from the same file.
* **Column order is verified when a task is written, not assumed:** PDF text extraction can return a row's figures in a different order from the printed columns (several State books come out reversed). Every task in the project was checked against the page's visual layout before it was added.
* **Mandatory Benign Explanations Considered:** pay revisions and dearness allowance, posts sanctioned mid-year, capital works re-phased across years, supplementary grants and re-appropriations, savings on vacant posts, late release of funds, and budget books that carry the budget estimate forward as the revised estimate.

### `UB1` — Union Budget Lines: Budget Estimate vs Revised Estimate vs Actual

The check used by the Union Budget project, P3 (`engines/union_budget.py`, ruleset `UB1-2026.11-v1`). Like `P1` it makes no judgement of its own: it reads figures the Ministry of Finance prints and reports how much of a line's budget estimate was not spent.

* **Statutory Anchors:** Constitution Articles 112–114 (Annual Financial Statement, Demands for Grants, Appropriation Bill) and 266; GFR 2017 Rule 62; FRBM Act 2003.
* **Input:** two official PDFs of the "Notes on Demands for Grants" (Statement of Budget Estimates) for the same Demand, each pinned by its exact link and SHA-256: the Union Budget presented one year after the financial year (it prints the budget estimate and the revised estimate of that year on one row) and the Union Budget presented two years after (it prints the actual expenditure). Each task lists the printed line number and name of every row it reads in each statement (`context.union_budget`).
* **How a row is read:** section A of the statement is one table; a row is the tokens of one printed line whose last twelve are figures (four periods of three figures, the third of each being the total; `...` is nil). Rows that wrap over several text lines are joined, and in budgets up to 2016-17 a line split by major head is read from the `Total` row that closes it, only if that total equals the rows above it. A statement is used only if its lines add up to its printed Grand Total in all four periods.
* **What is one line:** a printed row; or, where a scheme is split below its heading by source of funds (Gross Budgetary Support, National Investment Fund, PMSSN, CRIF, cess-funded Koshes) or by component (programme, EAP, grant and loan parts), all the spending rows under that heading added together, so money moving between sources or components is not read as money left unspent.
* **Matching the two budgets:** the estimates row and the actuals row must carry the same printed name under the same heading, and both statements must print the same budget estimate for the year in between (to the paisa). A renamed scheme is matched only when its rows carry the same names and that figure agrees on exactly one candidate. Where neither statement prints that figure, the rows are matched by name only, and only inside a statement that adds up.
* **Rule:** unspent = budget estimate − actual expenditure. A line is reported when the unspent amount is at least 10% of its budget estimate and at least ₹1 crore. Where no actuals row exists (2025-26, whose actuals Union Budget 2027-28 will print; 2015-16, whose lines Union Budget 2017-18 regrouped when Plan and Non-Plan were merged), the revised estimate is compared with the budget estimate instead, under the same thresholds.
* **Nil figures:** a printed nil actual counts as nothing spent only when the line is carried into the next year in both statements and its revised estimate had already been cut to a quarter of the budget estimate or less; otherwise the line is unevaluable, because the spending is most likely printed under another line. A negative actual (recoveries larger than spending) is unevaluable.
* **Out of scope by design:** repayment of debt and interest payments; the Railways statement (its works are printed net of the railway funds that pay for them); transfers into and out of reserve funds and "met from" rows; suspense heads; loan servicing; lump-sum provisions and "New Schemes"; provisions paid in securities rather than cash; and headings whose costs are mostly recovered inside the heading.
* **Outcomes:** a line at or above the thresholds becomes a neutral lead that quotes the page and printed row of every figure in both documents. Lines inside the thresholds count as cleared. A row that is not printed as the task lists it is unevaluable, never guessed; a task in which nothing could be read is handed back to the queue.
* **Known limits:** money moved to another Demand (a scheme transferred between ministries), money financed outside the budget for a year (for example by loans to a public sector undertaking), equity infusions paid in kind, and organisational changes during the year (a departmental undertaking corporatised mid-year) are not visible in these statements. Leads state the printed figures and list these explanations; three independent peer verifiers reproduce every lead from the same files.
* **Mandatory Benign Explanations Considered:** delayed approvals, tendering or land; vacant posts; lower demand in a demand-driven scheme; fresh releases held back while earlier releases are unspent with States or implementing agencies; schemes merged, renamed or moved; cess- and fund-financed lines; off-budget financing; and recoveries shown elsewhere in the statement.

### `OR1` — One Road, Two Bills: Union Duplicate Billing & Dual-Agency Claims

The check used by Project P4 (`one-road-two-bills`, `engines/one_road_two_bills.py`, ruleset `OR1-2026.11-v1`). It reads non-overlapping paragraph and section windows from checksummed Union Government CAG Audit Report PDFs from the past two years (`cag.gov.in`) to surface documented irregularities where a finished work, bill, or expenditure was paid twice or claimed across two agencies, departments, ministries, or schemes.

* **Statutory Anchors:** Constitution Article 151 (CAG Audit Reports); CAG (DPC) Act 1971; GFR 2017 Rules 21 & 62.
* **Input:** one official Union Government CAG Audit Report PDF, pinned by its exact CloudFront URL and SHA-256 (`context.one_road_two_bills`), listing 1 to 20 non-overlapping `(page, line_start, line_end)` section windows.
* **Legitimate Co-Funding Exclusion:** Blocks describing standard cost-sharing or joint-funding arrangements (`cost sharing`, `60:40`, `50:50`, `centre-state share`, `matching grant`, `viability gap funding`, `pooled funding`) are explicitly cleared unless an explicit double-billing or paid-twice irregularity appears in the same section.
* **Boilerplate Exclusion:** Table of Contents lines, figure captions, and audit-objective/methodology sentences (`to verify whether...`, `prevent duplicate...`) are excluded before signal matching.
* **Two-Tier Signal Rules:**
  * `duplicate_payment_or_billing`: explicit double payment, duplicate payment, paid/billed/claimed/reimbursed twice, double billing, duplicate voucher/invoice/bill, or two bills/vouchers/receipts.
  * `dual_agency_or_scheme_overlap`: two agencies/departments/ministries/schemes or already-executed overlapping works co-occurring with an audit irregularity indicator and concrete spending context.
* **Outcomes:** Matching sections emit neutral leads quoting exact report number, page, paragraph/section anchor, SHA-256 checksum, cited rupee amount (never fabricated if unprinted), and neutralized excerpt. Non-matching or co-funded blocks record `cleared`; missing pages record `unevaluable`.
* **Mandatory Benign Explanations Considered:** Action Taken Notes (ATNs) or PAC reviews recording subsequent recoveries or adjustments; phased/component billing across Measurement Books; and legitimate cost-sharing routed through separate executing agencies.

### `P5` — One Teacher, Five Grades: Single-Teacher & Primary School Concentration

The check used by Project P5 (`one-teacher-five-grades`, `engines/one_teacher_five_grades.py`, ruleset `P5-2026.11-v1`). It downloads checksummed Ministry of Education **UDISE+** annual national report PDFs (`2023-24`, `2024-25`, `2025-26`) and extracts verified State/UT figures across Table 2.2 (Single-Teacher Schools and Enrolments), Table 3.3 (Government Primary Classes I–V Schools), Table 4.3 (Government Primary I–V Teachers), and Table 5.2 (Government Enrolments).

* **Statutory Anchors:** Constitution Article 21A; Right to Education (RTE) Act 2009 Section 19 & Schedule (`30:1` Primary PTR norm, minimum 2 teachers for Primary Classes I–V); Samagra Shiksha Framework.
* **Concentration & Staffing Rules:** Surfaces States/UTs where single-teacher schools are `>= 10.0%` of schools (`>= 100` schools), pupils per single-teacher school exceed `30.0` (`>= 500` schools), or Government Primary (I–V) schools average `< 2.0` teachers per school (`>= 100` schools). States/UTs within all bounds record `cleared` with full extracted counts.
* **Management Scope Transparency:** Table 2.2 single-teacher totals cover all managements combined while Tables 3.3, 4.3, and 5.2 isolate Government-managed schools; `P5` publishes both metrics explicitly without conflating them.

### `P6` — Parliament's To-Do List: Ministerial Assurances & Follow-Up Verification

The check used by Project P6 (`parliaments-to-do-list`, `engines/parliaments_to_do_list.py`, ruleset `P6-2026.11-v1`, hook: *"A minister promised Parliament an answer. Did it ever get one?"*). It downloads checksummed official Digital Sansad (`https://sansad.in`) single-question Government Assurance JSON payloads (18th Lok Sabha Sessions 1–7 and Rajya Sabha Sessions 260–271) plus pinned official Lok Sabha Question & Answer PDFs and Rajya Sabha Implementation Report PDFs (`irUrl`) across **1,000 formal ministerial assurances** spanning all **56 active Union Ministries** (anchored by Task #1 `SQ-18/07/544` — Widening of NH-11 Road from Jhunjhunu to Fatehpur, listed as `PENDING`).

* **Statutory & Procedural Anchors:** Constitution Article 75(3) (collective ministerial responsibility to Parliament); Lok Sabha Rules of Procedure Rules 323–324 (Committee on Government Assurances); Rajya Sabha Rules 211A–212; Ministry of Parliamentary Affairs Manual of Parliamentary Procedures (standard **90-day / 3-month** fulfillment norm).
* **Mandatory 3-Part Evidence Citation:** Every `P6` output (both flagged leads and cleared records) explicitly cites: (1) the **Original Promise** (`textOfAssurance` / `extent`, date, house, session, question/assurance ref, and verified Question PDF where attached), (2) the **Recorded Status** (`pending`, `fully_implemented`, `partly_implemented`, or `dropped`, elapsed days as of `as_of_date`, Committee extensions, and remarks), and (3) **What the Follow-Up Actually Answered** (extracted verbatim from machine-readable Rajya Sabha Implementation Report PDFs, flagged when an IR PDF is an image-only scan lacking a text layer, cited via Lok Sabha Implementation Statement reference `impRecordNo`, or confirmed absent when no statement has been laid).
* **Deterministic Rules (`P6`):**
  * `overdue_pending_assurance` (`P6-OVERDUE-PENDING`): `pending` and `days_elapsed > 90` (`medium` at `91–180d`, `high` at `181–365d`, `critical` at `>365d` or `>= 2` Committee extensions).
  * `partly_implemented_assurance` (`P6-PARTLY-IMPLEMENTED`): `partly_implemented` (partial statement laid while core commitment remains open).
  * `dropped_without_fulfillment` (`P6-DROPPED-ASSURANCE`): `dropped` by the Committee on Government Assurances without substantive fulfillment.
  * `delayed_or_unreadable_followup` (`P6-FOLLOWUP-GAP`): `fully_implemented` where fulfillment exceeded `180` days, the pinned Rajya Sabha Implementation Report PDF lacks an extractable text layer (image-only scan), or no follow-up statement reference is recorded.
* **Cleared Standing & Benign Explanations:** Pending assurances within the 90-day window and assurances fulfilled within `<= 180` days with verifiable follow-up records publish as `cleared` with equal standing. Quoted parliamentary text is PII-scrubbed and neutralized under BNS Sec 356, and every lead lists lawful explanations (DPR/technical studies, inter-ministerial or State inputs, Committee-granted extensions, session tabling schedules, and legacy document digitization).

### `P7` — Only One Bid: Single-Bidder Tenders & Repeat-Winner Procurement Patterns (2016–2026)

The check used by Project P7 (`only-one-bid`, `engines/only_one_bid.py`, ruleset `P7-2026.11-v1`, hook: *"Which public purchases consistently attract just one bidder — and keep going to the same winner across 10 years of Indian government tenders?"*). It audits **10,000 real, SHA-256-pinned Indian government tender and contract-award records** spanning **2016–2026** (`FY2016-17` through `FY2026-27`) across Union Ministries and **20+ States** (248 procuring authorities, 8 open procurement registries/portals, 239 immutable shards all `<= 30.03 MiB`), anchored by Task #1 `p7-bo-2020-00001` (`IN-MPRRDA-194572-CW-RFB` / `OP00113760` — Madhya Pradesh Rural Roads Development Authority, ₹14.52 Cr, 5 bids received → 1 technically qualified bidder `M/S OSHO ASSOCIATES`, 7-of-7 single-qualified wins in the same project cohort).

* **Statutory Anchors:** GFR 2017 Rules 166 (Single Tender Enquiry / PAC) & 173(xx) (fair competition); CVC Single-Bid Guidelines (retendering & rate reasonableness); MoF DoE Manual for Procurement of Goods & Works; Competition Act 2002 Sec. 3(3)(d); State Public Procurement & Financial Rules (where State authority); RTI Act 2005 Sec. 4(1)(b).
* **Verified Tier-1 Sources (Zero CAPTCHA, Immutable / Byte-Stable, Strictly India-Only):** Open Contracting Partnership Data Registry Pub #77 (Himachal Pradesh OCDS, 2016–2020) & Pub #131 (Assam Finance Dept OCDS, 2016–2022), CivicDataLab Assam OCDS v3 Release Package (2017–2023), CivicDataLab IDS-DRR State `Accepted-AOC` datasets (Assam 2016–2026, HP 2016–2025, Odisha 2019–2026, UP 2018–2024), and **Indian Government (World Bank–assisted projects)** contract awards published via the World Bank STEP API (strictly `project_ctry_code=IN` domestic Indian Union & State agency tenders: closed/dormant project shards 2016–2025 + permanently immutable individual `id=OP...` notices 2024–2026).
* **Forensic Data-Hygiene Safeguards:**
  * Distinguishes `bids_received` vs `technically_qualified_bids` and strictly separates blank/`None` from literal `0`, `1`, and `>= 2`.
  * Explicitly filters CivicDataLab IDS-DRR CSVs to `Status == "Accepted-AOC"` (excluding non-winning `Rejected-*` bidder rows) and disables `bid_count` (`null`) because the GePNIC CSV column `No of Bids Received` records envelope cover counts rather than bidder counts.
  * Drops malformed World Bank notices where `bid_reference_no` mismatches `notice_text`, counts `Evaluated Bidder(s)` as non-winning evaluated bidders (`bids_received = 1 + len(evaluated) + len(rejected)`), and leaves `bids_received = None` on awarded-only `RFB`/`RFQ` notices without `Evaluated`/`Rejected` sections.
* **Deterministic Signal Rules & Anti-Double-Counting (`P7`):**
  * `single_bidder_repeat_winner` (`P7-COMBINED-SB-RW`): non-exempt `effective_bids == 1` AND same normalized winner has `>= 2` single-bid wins or `>= 3` total wins at the same authority.
  * `unexplained_single_bidder` (`P7-SINGLE-BID`): non-exempt `effective_bids == 1` (`single_bid_received` or `single_qualified_after_rejection`) at `>= ₹5 L` (or `< 14d` window).
  * `repeat_winner_concentration` (`P7-REPEAT-WINNER`): same normalized winner has `>= 3` distinct authority awards with `>= 25%` count share / `>= 30%` value share (over `>= 5` awards), `>= 5` persistent wins, or `>= 3` consecutive wins.
  * `short_window_or_process_gap` (`P7-PROCESS-GAP`): `< 7d` window (`>= ₹25 L`) or `< 14d` (`>= ₹50 L`) with `<= 2` bids, or unrecorded bid count on a `>= 3`-win repeat winner.
  * **Anti-Double-Counting:** `monetary_exposure_inr` always records strictly the target tender's own value; `winner_cohort_total_inr` is stored separately inside `anomaly_metrics`.
* **Cleared Standing & Benign Explanations:** Documented PAC/OEM/emergency single-source awards (absent sustained multi-award dominance), multi-bidder non-repeat competitive tenders, and sub-₹5 L de minimis single bids publish as `cleared` with equal standing. Every lead includes concrete lawful benign explanations and jurisdiction-matched RTI Act 2005 drafts.

### `P8` — Sovereign Lies: Government Scheme Launch Promises vs. Subsequent Walk-Backs

The check used by Project P8 (`sovereign-lies`, `engines/sovereign_lies.py`, ruleset `P8-2026.11-v1`, hook: *"The government made a promise when it launched the scheme. Did it keep it?"*). It audits **1,000 real, SHA-256-pinned Union and State/UT government scheme commitments** across India — comparing explicit statutory, budgetary, regulatory, or parliamentary launch promises against subsequent Finance Act amendments, circular/GO revisions, eligibility exclusions, exit restrictions, arrears, or discontinuations — anchored by Task #1 (`p8-union-sgb-anchor-0001`), the **Sovereign Gold Bond (SGB) Scheme** (Finance Bill 2016 Clause 28 / Section 47(viic) capital-gains tax exemption on individual maturity redemption vs. Union Budget 2026-27 / Finance Bill 2026 Clause 35 restricting Section 70(1)(x) exemption solely to original-issue subscribers held continuously until maturity).

* **Statutory & Documentary Anchors:** Income-tax Act 1961 Section 47(viic) & Income-tax Act 2025 Section 70(1)(x) (Finance Bill 2026 Clause 35); Government Securities Act 2006; Constitution of India Articles 112–114, 148–151 & 202–203; Union & State Finance Acts, Budgets, CAG Audit Reports, Digital Sansad Parliamentary Assurances & Q&A records, and RTI Act 2005 Section 4(1)(b).
* **Fail-Closed Documentary Token Verification:** The loader verifies primary and optional secondary SHA-256 digests (`DigestMismatchError` on any byte mismatch) across PDF, HTML, JSON, CSV, and TXT records. Before emitting any lead or cleared verdict, `P8` confirms machine-readable text (`>= 40` chars), applies any structured `record_selector`, and verifies that **100% of `promise_evidence_tokens`** appear in the launch-promise document and **100% of `walkback_evidence_tokens`** appear in the walk-back/revision document (or both in primary for single-document records, and both corroborated in `corroborating_record` mode). Missing tokens, selector misses, or unreadable documents fail closed as `unevaluable` with zero fabricated leads.
* **Deterministic Walk-Back Rules (`P8`):**
  * `retroactive_tax_reversal` (`P8-RETROACTIVE-TAX-REVERSAL`): Tax exemption or fiscal treatment promised at launch subsequently narrowed, capped, or withdrawn.
  * `midstream_benefit_cut` (`P8-MIDSTREAM-BENEFIT-CUT`): Interest rate, subsidy share, payout, or benefit reduced during active scheme tenure.
  * `premature_discontinuation` (`P8-PREMATURE-DISCONTINUATION`): Notified scheme, tranche series, fellowship, or concession suspended or wound up ahead of announced schedule.
  * `eligibility_narrowing` (`P8-ELIGIBILITY-NARROWING`): Post-launch exclusions, taxpayer bars, income caps, or beneficiary pruning narrowing original eligibility.
  * `lockin_or_exit_restriction` (`P8-LOCKIN-OR-EXIT-RESTRICTION`): Lock-in lengthened or premature exit/redemption flexibility curtailed after launch.
  * `arrears_or_unfunded_promise` (`P8-ARREARS-OR-UNFUNDED-PROMISE`): Notified benefit, pension, waiver, or subsidy left unpaid, unimplemented, or in documented arrears.
* **Cleared Standing, Neutrality & Benign Explanations:** Verified schemes whose launch terms were honored or where existing holders were explicitly grandfathered (`grandfathered_or_honored`) publish as `cleared` with equal journalistic standing. All emitted titles, summaries, quoted excerpts, and citations pass BNS Sec 356 neutrality (`lint_neutrality`) and DPDP Act 2023 PII scrubbing (`scrub_pii`), with mandatory lawful benign explanations (parliamentary statutory amendment power, FRBM fiscal sustainability, arbitrage prevention, macroeconomic/bullion shifts, and e-KYC deduplication).

### `P9` — Auditing Government Exams: 3-Year Public Examination Conduct Audit (Nov 2023 – Nov 2026)

The check used by Project P9 (`auditing-government-exams`, `engines/auditing_government_exams.py`, ruleset `P9-2026.11-v1`, hook: *"Did India's government exams happen on time and flawlessly — or get delayed, postponed, rescheduled, or cancelled after students sat them?"*). It audits **1,000 real, SHA-256-pinned Union and State/UT government examination conduct records** across the **past 3 years (`2023-11-28` to `2026-11-28`, exam years 2023–2026)** spanning the **Union Government** (`347` tasks) and **all 36 States & Union Territories** (`586` State + `67` UT tasks covering all 28 States and 8 UTs, `716` unique examinations, `165` conducting authorities, and `859` unique live-verified byte-stable official documents across `30` official domains) — anchored by Task #1 (`p9-union-ugc-net-june-2024-0001`), **UGC-NET June 2024** (conducted in OMR pen-and-paper mode on 18 June 2024 across 1,205 centres in 317 cities for 9,08,580 appeared candidates and cancelled post-conduct prior to CBT re-conduct).

* **Statutory & Documentary Anchors:** Public Examinations (Prevention of Unfair Means) Act, 2024; Constitution of India Articles 315 & 320 (UPSC & State Public Service Commissions); Union & State Recruitment and Examination Rules; Digital Sansad Lok Sabha & Rajya Sabha Parliamentary Q&A records; Official NTA, SSC, UPSC, CBSE, IBPS, and State/UT PSC Public Notices; and RTI Act 2005 Section 4(1)(b).
* **Fail-Closed Word-Boundary Token Verification:** The loader verifies primary and optional secondary SHA-256 digests (`DigestMismatchError` on any byte mismatch) across PDF, HTML, JSON, CSV, and TXT records. Before emitting any lead or cleared verdict, `P9` confirms `>= 40` chars of extracted text, enforces the strict 3-year schedule window (`2023-11-28` to `2026-11-28`), and verifies that **100% of `schedule_evidence_tokens`** and **100% of `outcome_evidence_tokens`** match verbatim at alphanumeric word boundaries (`(?<![^\W_])token(?![^\W_])`) in the pinned official bytes. Missing tokens, out-of-window dates, or unreadable documents fail closed as `unevaluable` with zero fabricated leads.
* **Deterministic Disruption Rules (`P9`):**
  * `cancelled_after_conduct` (`P9-CANCELLED-AFTER-CONDUCT`): Candidates sat/attempted the scheduled examination and the authority subsequently cancelled it (`students_took_exam_before_cancellation=true`).
  * `postponed` (`P9-POSTPONED`): Scheduled examination was deferred or postponed prior to conduct.
  * `rescheduled` (`P9-RESCHEDULED`): Scheduled examination date, shift, or centre was revised via official notice.
  * `delayed` (`P9-DELAYED`): Examination conduct, notification, or statutory recruitment cycle was materially delayed beyond announced schedule.
  * `cancelled_before_conduct` (`P9-CANCELLED-BEFORE-CONDUCT`): Notified examination or recruitment test was cancelled/withdrawn before candidates sat.
  * `partial_reexam_or_glitch` (`P9-PARTIAL-REEXAM-OR-GLITCH`): Technical/CBT fault, time-loss anomaly, or centre/subject-level disruption requiring partial re-examination.
* **Cleared Standing, Neutrality & Benign Explanations:** Verified examinations conducted on schedule without documented disruption (`flawless_conduct`, `255` benchmark tasks) publish as `cleared` with equal journalistic standing and `affected_candidates_count: null` (so headline affected-candidate totals strictly count disrupted exams). All titles, summaries, excerpts, and citations pass BNS Sec 356 neutrality (`lint_neutrality`) and DPDP Act 2023 PII scrubbing (`scrub_pii`), with mandatory lawful benign explanations (preventive integrity safeguards under the Public Examinations Act 2024, election Model Code of Conduct, severe weather/disaster advisories, CBT vendor faults, and judicial orders).

### `W1` — `.gov.in` GIGW 3.0, RTI Act 2005 & GoI Statutory Conformity Audit

The read-only website observation engine used by `gov-in-public-information` (`engines/gov_pages.py`, `engines/gigw_matrix.py`, matrix version `gigw-3.0-rti-2005-v1`). It audits all 12,537 web `.gov.in` hostnames from Data Libertarian's pinned snapshot across **1,000 batches** (12–13 domains per task) against a unified **159-checkpoint conformity matrix**:

* **Authoritative Frameworks (159 Checkpoints across 8 Sections):**
  1. **GIGW 3.0 Annexure II & Full Guidelines (88 checkpoints):** Quality Guidelines (25), Lifecycle Management (10), Accessibility / WCAG 2.1 Level AA (50: 30 Level A + 20 Level AA), and Cyber Security & Privacy (3).
  2. **RTI Act, 2005 & DoPT / CIC Suo Motu Disclosure Guidelines (42 checkpoints):** Section 4(1)(a), all 17 clauses of Section 4(1)(b)(i)–(xvii) plus annual updating, Sections 4(1)(c)–(d), 4(2)–(4), 5(1)–(2) (CPIO/SPIO/CAPIO), 6(1), 7(4)–(5), 8(1)(i) proviso, 19(1) (FAA), 25, and 26 (34 checkpoints) + DoPT OMs & CIC transparency audit proforma (8 checkpoints).
  3. **Other Verified GoI Statutory & Policy Norms (29 checkpoints):** RPwD Act 2016 & Rules 2017 Rule 15 / BIS IS 17802 (5), Official Languages Act 1963 & Rules 1976 — Union portals only (3), State Emblem of India Act 2005 & Rules 2007 (2), DARPG Citizen's Charter, CPGRAMS & WIM (4), MeitY Domain & E-mail Policy, CERT-In & STQC CQW (7), IT Act SPDI Rules 2011 & DPDP Act 2023 / Rules 2025 (4), and MeitY Open Standards Policy & DST NDSAP (4).
* **Honest Verification Modes:** `48` checkpoints have deterministic automated HTTP/HTML/TLS indicators directly measured by **46 W1 checks** across landing page + up to 2 linked public pages (Contact/Directory and RTI/Proactive Disclosure), scoped per host by each checkpoint's statutory applicability (e.g., Union/Central bodies for `OLA-3-3-web`, Ministries/Departments for `NDSAP-01`, non-exempt public authorities for RTI Act disclosures); `96` require qualitative frontier-agent inspection (e.g. substantive 17-point RTI Sec 4(1)(b) content and WCAG 2.1 AA semantics, reporting `observed_pct = null` until inspected); and `15` are internal-governance controls not verifiable from public HTML alone.
* **Privacy, Safety & Legal Boundaries:** Strictly honors `robots.txt` (`Protego`), pins resolved public IPv4/IPv6 addresses (zero SSRF), never bypasses CAPTCHAs or login walls, never submits forms, never stores personal emails/phones/names or raw server banners, publishes only generic navigation URLs, marks RTI Sec 24 / Second Schedule intelligence & security organisations as `not_applicable` when unobserved, and keeps inaccessible hosts (`robots_restricted`, `fetch_failed`, `unsupported_page`) strictly separate from observed gaps.

---

## 3. Epistemic Lifecycle, 4-Check Quorum & Jurisdiction-Aware RTI Drafts

Every audit finding follows a strict state machine enforced by the Coordinator Hub (`src/aiwatchdogs/hub/desk.py`):

1. **Strict Author Separation & Explicit 4-Check Verification:** Every independent peer vote must emit an explicit `pass`, `fail`, or `unrecorded` state for each of the four checks (`bytes_digest`, `deterministic_reproduction`, `metric_tolerance`, `neutrality_privacy`), an explicit verdict (`confirm`, `dispute`, `needs_review`, `reject_false_positive`, or `flag_pii_or_legal` — never defaulted), and bound manifest metadata (`source_locator`, `engine_version`, `ruleset_version`, `as_of_date`, reproduced metrics).
2. **Computational Reproduction vs Editorial Review:** Promotion to `verified` requires at least 3 independent contributors — with **all four participants (author + all 3 peer verifiers) pairwise distinct in handle, coding-agent harness (`coding_agent`), and model (`coding_agent_model`)** — recording explicit `pass` on all 4 checks against the exact entity, fiscal period, engine/ruleset version, and full required metric set. Split votes queue adjudication; privacy/legal flags immediately quarantine public visibility; post-publication corrections are preserved transparently as versioned correction history at the dossier's stable URL.
3. **Jurisdiction-Aware Human-Only RTI Drafts (`core/rti.py`):** `generate_rti_draft()` resolves whether the record holder is a **Central** or **State** public authority (Assam, Andhra Pradesh, Bihar, Himachal Pradesh, Madhya Pradesh, etc.), emitting the appropriate CPIO/SPIO designation, official State or Central portal/filing channel, and statutory fee rules ($\le 500$ words, Section 7(5) BPL exemption, 30-day Section 7(1) timeline). State-authority drafts **never** route citizens to `https://rtionline.gov.in` (which returns state applications without refund). Filing remains strictly human-only.
4. **Anti-Spam Contributor Scoring:** Contributors earn recognition for verified findings, independent peer verifications, and cleared/refuted records — derived from timestamped events.

---

## 4. Visual Epistemic Grammar

Inspired by `pequalnp.com`'s rule *"Only checked looks like a result"*, the web dashboard encodes epistemic status into unmistakable visual typography:

| Status | Visual Treatment | Meaning |
|---|---|---|
| `verified` | Solid upright emerald pill (`#2F6B4F` on `#E6F0E9`, solid `1px` border) | Deterministic math reproduced & all 4 checks passed by $\ge 3$ independent peer nodes (different harness & model from author). |
| `peer_review` / `raw_lead` | **Dashed** border, *italic* warm stone/amber pill | Preliminary lead awaiting independent peer verification — **not a verified finding**. |
| `cleared` / `resolved_explained` | Calm slate/blue-gray pill | Audited records showing no threshold breach, or anomaly lawfully explained by official response. |
| `unevaluable` | Neutral stone pill | Records lacking required source fields (e.g. zero positive bidder counts, missing physical km, unmatched OBDS units/heads) — kept distinct from `cleared`. |
| `disputed` / `retracted_false_positive` | Muted terracotta/red pill (`#A2321F` on `#F7E4E0`) | Contested in peer review, under editorial correction, or retracted after factual refutation. |
| `rti_filed` | Civic indigo pill (`#2B4D9B` on `#E8EEFA`) | Citizen-reviewed RTI Act 2005 Sec 6(1) application logged against this lead. |
| `[SAMPLE DEMO DATA]` | Archival stone badge | Explicitly marks bundled sample datasets vs. live public ingestions. |
