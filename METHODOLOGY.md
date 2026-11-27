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
* **One parser for every node:** `pypdf` is pinned to an exact version in `pyproject.toml`, because the auditor and both peer verifiers must extract the same text from the same file.
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
* **Known limits:** money moved to another Demand (a scheme transferred between ministries), money financed outside the budget for a year (for example by loans to a public sector undertaking), equity infusions paid in kind, and organisational changes during the year (a departmental undertaking corporatised mid-year) are not visible in these statements. Leads state the printed figures and list these explanations; two independent peer verifiers reproduce every lead from the same files.
* **Mandatory Benign Explanations Considered:** delayed approvals, tendering or land; vacant posts; lower demand in a demand-driven scheme; fresh releases held back while earlier releases are unspent with States or implementing agencies; schemes merged, renamed or moved; cess- and fund-financed lines; off-budget financing; and recoveries shown elsewhere in the statement.

### `OR1` — One Road, Two Bills: Union Duplicate Billing & Dual-Agency Claims

The check used by Project P4 (`one-road-two-bills`, `engines/one_road_two_bills.py`, ruleset `OR1-2026.11-v2`). It reads non-overlapping paragraph and section windows from checksummed Union Government CAG Audit Report PDFs from the past two years (`cag.gov.in`) to surface documented irregularities where a finished work, bill, or expenditure was paid twice or claimed across two agencies, departments, ministries, or schemes.

* **Statutory Anchors:** Constitution Article 151 (CAG Audit Reports); CAG (DPC) Act 1971; GFR 2017 Rules 21 & 62.
* **Input:** one official Union Government CAG Audit Report PDF, pinned by its exact CloudFront URL and SHA-256 (`context.one_road_two_bills`), listing 1 to 20 non-overlapping `(page, line_start, line_end)` section windows.
* **Legitimate Co-Funding Exclusion:** Blocks describing standard cost-sharing or joint-funding arrangements (`cost sharing`, `60:40`, `50:50`, `centre-state share`, `matching grant`, `viability gap funding`, `pooled funding`) are explicitly cleared unless an explicit double-billing or paid-twice irregularity appears in the same section.
* **Boilerplate Exclusion:** Table of Contents lines, figure captions, and audit-objective/methodology sentences (`to verify whether...`, `prevent duplicate...`) are excluded before signal matching.
* **Two-Tier Signal Rules:**
  * `duplicate_payment_or_billing`: explicit double payment, duplicate payment, paid/billed/claimed/reimbursed twice, double billing, duplicate voucher/invoice/bill, or two bills/vouchers/receipts.
  * `dual_agency_or_scheme_overlap`: two agencies/departments/ministries/schemes or already-executed overlapping works co-occurring with an audit irregularity indicator and concrete spending context.
* **Evidence and attribution:** A lead cites the triggering sentence, report number, page, section, and SHA-256. An explicit payment actor in that sentence or a supported opening of the same section may name the auditee. Cite the opening separately when it supplies the actor. If attribution is uncertain the lead uses a report or sector label. A closely linked sentence may supply a single amount explicitly tied to the signal. Recovery figures and totals that combine different irregularities stay undisclosed as duplicate exposure.
* **Outcomes:** Non-matching or co-funded blocks record `cleared`. Missing pages and candidate signals in flattened audit tables with unresolved row attribution record `unevaluable`. A task with unevaluable windows and no attributable leads is handed back for review.
* **Mandatory Benign Explanations Considered:** Action Taken Notes (ATNs) or PAC reviews recording subsequent recoveries or adjustments; phased/component billing across Measurement Books; and legitimate cost-sharing routed through separate executing agencies.

### `P5` — One Teacher, Five Grades: Single-Teacher & Primary School Concentration

The check used by Project P5 (`one-teacher-five-grades`, `engines/one_teacher_five_grades.py`, ruleset `P5-2026.11-v1`). It downloads checksummed Ministry of Education **UDISE+** annual national report PDFs (`2023-24`, `2024-25`, `2025-26`) and extracts verified State/UT figures across Table 2.2 (Single-Teacher Schools and Enrolments), Table 3.3 (Government Primary Classes I–V Schools), Table 4.3 (Government Primary I–V Teachers), and Table 5.2 (Government Enrolments).

* **Statutory Anchors:** Constitution Article 21A; Right to Education (RTE) Act 2009 Section 19 & Schedule (`30:1` Primary PTR norm, minimum 2 teachers for Primary Classes I–V); Samagra Shiksha Framework.
* **Concentration & Staffing Rules:** Surfaces States/UTs where single-teacher schools are `>= 10.0%` of schools (`>= 100` schools), pupils per single-teacher school exceed `30.0` (`>= 500` schools), or Government Primary (I–V) schools average `< 2.0` teachers per school (`>= 100` schools). States/UTs within all bounds record `cleared` with full extracted counts.
* **Management Scope Transparency:** Table 2.2 single-teacher totals cover all managements combined while Tables 3.3, 4.3, and 5.2 isolate Government-managed schools; `P5` publishes both metrics explicitly without conflating them.

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
2. **Computational Reproduction vs Editorial Review:** Promotion to `verified` requires at least 2 independent contributors recording explicit `pass` on all 4 checks against the exact entity, fiscal period, engine/ruleset version, and full required metric set. Split votes queue adjudication; privacy/legal flags immediately quarantine public visibility; post-publication corrections are preserved transparently as versioned correction history at the dossier's stable URL.
3. **Jurisdiction-Aware Human-Only RTI Drafts (`core/rti.py`):** `generate_rti_draft()` resolves whether the record holder is a **Central** or **State** public authority (Assam, Andhra Pradesh, Bihar, Himachal Pradesh, Madhya Pradesh, etc.), emitting the appropriate CPIO/SPIO designation, official State or Central portal/filing channel, and statutory fee rules ($\le 500$ words, Section 7(5) BPL exemption, 30-day Section 7(1) timeline). State-authority drafts **never** route citizens to `https://rtionline.gov.in` (which returns state applications without refund). Filing remains strictly human-only.
4. **Anti-Spam Contributor Scoring:** Contributors earn recognition for verified findings, independent peer verifications, and cleared/refuted records — derived from timestamped events.

---

## 4. Visual Epistemic Grammar

Inspired by `pequalnp.com`'s rule *"Only checked looks like a result"*, the web dashboard encodes epistemic status into unmistakable visual typography:

| Status | Visual Treatment | Meaning |
|---|---|---|
| `verified` | Solid upright emerald pill (`#2F6B4F` on `#E6F0E9`, solid `1px` border) | Deterministic math reproduced & all 4 checks passed by $\ge 2$ independent peer nodes. |
| `peer_review` / `raw_lead` | **Dashed** border, *italic* warm stone/amber pill | Preliminary lead awaiting independent peer verification — **not a verified finding**. |
| `cleared` / `resolved_explained` | Calm slate/blue-gray pill | Audited records showing no threshold breach, or anomaly lawfully explained by official response. |
| `unevaluable` | Neutral stone pill | Records lacking required source fields (e.g. zero positive bidder counts, missing physical km, unmatched OBDS units/heads) — kept distinct from `cleared`. |
| `disputed` / `retracted_false_positive` | Muted terracotta/red pill (`#A2321F` on `#F7E4E0`) | Contested in peer review, under editorial correction, or retracted after factual refutation. |
| `rti_filed` | Civic indigo pill (`#2B4D9B` on `#E8EEFA`) | Citizen-reviewed RTI Act 2005 Sec 6(1) application logged against this lead. |
| `[SAMPLE DEMO DATA]` | Archival stone badge | Explicitly marks bundled sample datasets vs. live public ingestions. |
