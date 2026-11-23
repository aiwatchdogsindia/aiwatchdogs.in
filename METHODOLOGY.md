# Audit Methodology & Deterministic Verification Specification

**Golden Rule:** Large Language Models (LLMs) extract tables, normalize unstructured tender clauses, summarize statutory rules, and propose plausible benign explanations from messy PDFs and HTML. **Every numeric red-flag calculation MUST execute inside a deterministic, version-hashed Python rule engine (`aiwatchdogs.engines`)** so that any independent contributor node running on a ₹0 CPU-only machine can reproduce the exact numbers from the same primary `.gov.in` artifact.

---

## 1. Public Data Ingestion Tiers (IT Act Sec 43 Compliant)

All Indian public data connectors in `aiwatchdogs.connectors` follow a strict three-tier legality matrix:

| Tier | Ingestion Mode | Policy & Rate Limits | Examples |
|---|---|---|---|
| **Tier 1** | Automated Direct Open API / Bulk Open Files | Zero CAPTCHA, zero auth wall; polite User-Agent (`AIWatchdogs-India-OpenAudit/0.1`); structured JSON, CSV, XLSX, or official PDF. | **CivicDataLab OCDS India** (`assam-tenders-data`, `himachal-pradesh-health-procurement-OCDS`), **OpenBudgetsIndia** (`openbudgetsindia.org` CKAN API), **data.gov.in** (OGD REST API), **CAG of India** (`cag.gov.in` PDFs), **Union Budget** (`indiabudget.gov.in`), **CGA Monthly Accounts** (`cga.nic.in`), **Digital Sansad** (`sansad.in`), **ECI Electoral Bonds** (`eci.gov.in`). |
| **Tier 2** | Polite Rate-Limited Public Dashboards / Official Exports | Publicly accessible without login; strictly honors `robots.txt`, conservative rate caps, or official MIS CSV/XLS exports. | **PFMS** (`pfms.nic.in`), **MGNREGA MIS** (`nrega.nic.in`, `mnregaweb4.nic.in`), **PMGSY OMMAS** (`ommas.nic.in`), **MPLADS e-SAKSHI** (`mplads.mospi.gov.in`), **National CSR Portal** (`csr.gov.in`), **MyNeta / ADR** (`myneta.info`), **APMS** (`apms.nic.in`), **eGazette** (`egazette.gov.in`), **CIC** (`cic.gov.in`). |
| **Tier 3** | Human-Downloaded Official Exports & Open Mirrors ONLY | **Zero automated CAPTCHA solving or authentication bypass.** Agents ingest only user-downloaded official exports (`aiwatchdogs audit-file`) or lawful open OCDS mirrors. | **CPPP** (`eprocure.gov.in`), **State GePNIC Portals** (`mahatenders.gov.in`, `assamtenders.gov.in`, `hptenders.gov.in`), **GeM** (`gem.gov.in`), **MCA21** (`mca.gov.in`). |

Every ingested file produces a `LoadedDataset` containing:
* `sha256`: Cryptographic digest of raw bytes before parsing.
* `byte_size` & `format`: Exact file fingerprint (`csv`, `json`, `pdf`).
* `pii_scrubbed = true`: All extracted text passes through `aiwatchdogs.core.safety.scrub_pii()` prior to storage or submission.
* `is_demo_sample`: Boolean flag explicitly distinguishing bundled `[SAMPLE DEMO DATASET]` files under `datasets/sample/` from live public ingestions.

---

## 2. Deterministic Audit Methodologies (`M1` – `M8`)

All eight methodologies are registered in `aiwatchdogs.engines.registry.METHODOLOGY_CATALOG` and executed via `run_engine(methodology_code, records, context)`. Every `EngineRunResult` records a SHA-256 `script_hash` of the deterministic engine code, total `records_scanned`, total `monetary_audited_inr`, `cleared_count`, and neutrality-checked `AuditLeadCandidate` objects.

### `M1` — Single-Bidder & Tailored-Spec Concentration
* **Statutory Anchors:** General Financial Rules (GFR) 2017 Rule 166 (Single Tender Enquiry); Central Vigilance Commission (CVC) Single-Tender Guidelines; OECD Procurement Integrity Red Flags.
* **Target Sources:** CivicDataLab OCDS India (Assam, Himachal Pradesh), CPPP / State GePNIC tender exports.
* **Deterministic Triggers:**
  1. **Buyer Single-Bid Share:** For a procuring entity with competitive tender count `n >= 10` (or grouped sample threshold), flag when `single_bid_awards / total_competitive_tenders > 0.35` (**> 35%**).
  2. **Repeat Single-Bid Vendor:** Flag when the same supplier wins **`>= 3` single-bid contracts** from the same buyer within a 12-month window.
  3. **Compressed Bid Window:** Flag open advertised tenders with submission window **`< 14 days`** (compared against standard 14–21 day norms) when paired with a single-bidder outcome or repeated technical disqualifications leaving exactly 1 eligible bidder.
* **LLM Assist (Optional):** Extracts brand-specific, proprietary, or unusually restrictive eligibility clauses from NIT/RFP PDFs with exact page and paragraph citations.
* **Mandatory Benign Explanations Considered:** Proprietary OEM equipment covered by a valid GFR Rule 166 Proprietary Article Certificate (PAC), genuine emergency/disaster relief procurement, remote geography with sparse vendor base, or re-tendered bids following prior failed open tenders.

### `M2` — Split-Tendering & GFR 2017 Threshold Hugging
* **Statutory Anchors:** GFR 2017 Rules 149, 154, 155, 161 (including Ministry of Finance PPD July 2024 threshold revisions); CVC Circulars against splitting of demands to evade tendering tiers.
* **Target Sources:** GeM contract award exports, CPPP / State GePNIC purchase order logs.
* **Statutory Thresholds Tracked:**
  * **₹50,000** — GeM Direct Purchase ceiling *(₹25,000 prior to July 2024)*.
  * **₹2,50,000 (₹2.5 Lakh)** — Non-GeM Local Purchase Committee ceiling.
  * **₹10,00,000 (₹10 Lakh)** — GeM L1 comparison ceiling vs. mandatory Online Bidding / Reverse Auction *(₹5 Lakh prior to July 2024)*.
  * **₹50,00,000 (₹50 Lakh)** — Limited Tender Enquiry (LTE) ceiling vs. mandatory Open Advertised Tender *(₹25 Lakh prior to July 2024)*.
* **Deterministic Trigger:**
  * Group purchase orders by `(buyer_entity, item_category)` (or `(buyer_entity, vendor)`).
  * Identify orders whose individual award value sits in the **threshold-hugging band: `85%` to `99.9%` of a statutory threshold `T`** (`0.85 * T <= value < T`).
  * Flag a **Split-Tendering Lead** when within any **60-day rolling window** (and tracked over 30-day windows):
    $$\text{count}(\text{threshold-hugging POs}) \ge 3 \quad \text{AND} \quad \sum \text{values} > T$$
* **Mandatory Benign Explanations Considered:** Phased quarterly budget releases under Cash Management ceilings, geographically separate sub-divisions/hospitals raising independent indents, or distinct non-substitutable technical specifications falling under a broad category label.

### `M3` — Repeat-Winner Concentration & Collusion Signatures
* **Statutory Anchors:** Competition Commission of India (CCI) Bid-Rigging & Public Procurement Guidelines; OECD Guidelines for Fighting Bid Rigging.
* **Target Sources:** OCDS procurement packages, GeM / CPPP multi-bidder evaluation sheets.
* **Deterministic Triggers:**
  1. **Herfindahl–Hirschman Index (HHI):** For each `(buyer_entity, category)` market across observed contracts, compute vendor award-value shares $s_i = \frac{\text{award\_value}_i}{\sum_j \text{award\_value}_j}$ and:
     $$\text{HHI} = \sum_{i=1}^{k} s_i^2$$
     Flag when **`HHI > 0.35`** (high concentration) or any single vendor holds **market share `> 40%`** across the evaluation window (ideally $\ge 4$ quarters).
  2. **Narrow Bid-Spread Clustering:** Flag multi-bidder tenders where losing bids cluster tightly within **1%–3% above the L1 bid** repeatedly across a closed 2–4 vendor group, or where ostensible competitors share corporate attributes (registered address, DIN overlap, or sequential GSTIN patterns).
* **Mandatory Benign Explanations Considered:** Genuine niche technical specialization, long-term rate contracts or framework agreements, thin local supplier ecosystems in specialized districts, or standardized commodity pricing (e.g., fuel, regulated pharma) that naturally compresses bid spreads.

### `M4` — Unit-Price Outlier & Post-Award Cost-Escalation Audit
* **Statutory Anchors:** GFR 2017 Rule 173 (Transparency, competition, fairness and elimination of arbitrariness); GeM Rate Benchmarking & Reasonability Guidelines; CVC Works & Procurement Manuals.
* **Target Sources:** GeM award CSVs, State Health & IT Procurement Bills of Quantities (BOQs).
* **Deterministic Triggers:**
  1. **Robust Unit-Price Outlier (MAD & Median Ratio):** Normalize by `(item_category / SKU / HSN, unit_of_measure)`. Compute peer median $\tilde{x}$ and Median Absolute Deviation $\text{MAD} = \text{median}(|x_i - \tilde{x}|)$ (with modified Z-score $M_i = 0.6745 \cdot (x_i - \tilde{x}) / \text{MAD}$ when $\text{MAD} > 0$). Flag when:
     $$\frac{x_i - \tilde{x}}{\text{MAD}} > 2.5 \quad \text{OR} \quad \frac{x_i}{\tilde{x}} > 1.75$$
  2. **Post-Award Escalation ("Low-Ball & Recover"):** Flag contracts where post-award amendment / variation orders raise final contract value by **`> 20%`** above the awarded L1 contract value:
     $$\frac{\text{final\_value} - \text{awarded\_value}}{\text{awarded\_value}} > 0.20$$
* **Mandatory Benign Explanations Considered:** Extended multi-year AMC/warranty bundled into unit rate, high-altitude or remote-district logistics surcharges, higher technical grade/spec within the same category code, or formally approved price-variation clauses (PVC) linked to RBI/WPI indices.

### `M5` — "March Rush" Expenditure Pacing & Budget Variance
* **Statutory Anchors:** GFR 2017 Rule 62(3); Ministry of Finance (Department of Expenditure) Cash Management System (CMS) Circulars; Public Accounts Committee (PAC) Explanatory Note Threshold (`>= ₹100 Cr`).
* **Target Sources:** CGA Monthly Accounts (`.xlsx`/`.pdf`), OpenBudgetsIndia CKAN API (`openbudgetsindia.org`), Union Budget Expenditure Profile (`indiabudget.gov.in`).
* **Deterministic Triggers:**
  1. **Q4 Expenditure Ceiling Breach:** Flag ministries/departments where **Q4 (Jan–Mar) expenditure exceeds `33%`** of Revised Estimate / Annual Allocation:
     $$\frac{\text{Spend}_{Q4}}{\text{Annual Allocation}} > 0.33$$
  2. **March Expenditure Ceiling Breach:** Flag ministries/departments where **March expenditure exceeds `15%`** of Annual Allocation:
     $$\frac{\text{Spend}_{\text{March}}}{\text{Annual Allocation}} > 0.15$$
  3. **Budget Estimate vs. Actuals Variance:** Flag **`|Actuals - BE| / BE > 0.25`** (**> 25%** savings or excess), highlighting variances **`>= ₹100 Cr`** that require a formal Explanatory Note to the Public Accounts Committee.
* **Mandatory Benign Explanations Considered:** Official Ministry of Finance CapEx relaxation / CMS waiver circulars, statutory year-end transfers to states/PSUs, supplementary demands for grants passed in Q4, or lump-sum debt/subsidy settlement schedules.

### `M6` — CAG Unresolved Audit Para & Repeat-Finding Tracker
* **Statutory Anchors:** Articles 148–151 of the Constitution of India; CAG of India Audit Reports; APMS (Audit Para Monitoring System) & Parliamentary Public Accounts Committee (PAC) Action Taken Reports (ATRs).
* **Target Sources:** Official CAG Compliance, Performance, Financial & PSU Audit PDFs (`cag.gov.in`), PAC ATRs and Parliament Q&A (`sansad.in`).
* **Deterministic Triggers:**
  * Ingests both structured audit para records (JSON/CSV) and extracted text from CAG PDF reports (`pypdf`).
  * Quantifies financial impact in ₹ Crore across standard CAG typologies (*unfruitful expenditure*, *idle investment*, *excess payment*, *diversion of funds*, *non-recovery / revenue loss*).
  * Flags **high-value unresolved paras** (pending ATN / overdue recovery across multiple audit cycles) and **repeat systemic findings** recurring across consecutive CAG reports for the same ministry or PSU.
* **Mandatory Benign Explanations Considered:** Action Taken Note (ATN) submitted to APMS and awaiting CAG vetting, sub-judice litigation/arbitration over contractor recovery, or partial recovery achieved after the audit cut-off date.

### `M7` — Welfare & Infrastructure Scheme Delivery Verification (`PMGSY`, `MPLADS`, `MGNREGA`)
* **Statutory Anchors:** PMGSY Programme Guidelines & OMMAS Norms; MPLADS Guidelines (MoSPI e-SAKSHI, ₹5 Cr/yr constituency cap & 1-year completion norm); MGNREGA 2005 Section 17 (Social Audits), Section 22 & Schedule II (60:40 Wage-to-Material Ratio).
* **Target Sources:** PMGSY OMMAS exports (`ommas.nic.in`), MPLADS e-SAKSHI exports (`mplads.mospi.gov.in`), MGNREGA MIS & R9.2 Social Audit reports (`nrega.nic.in`), `data.gov.in`.
* **Deterministic Triggers:**
  1. **PMGSY Rural Roads:** Flag packages with **`financial_progress >= 90%`** AND **`physical_progress_km < 70%`**, or bids **`> 25%` below DPR cost** paired with severe delay / premature Defect Liability Period (DLP) failure.
  2. **MPLADS Constituency Works:** Flag sanctioned works exceeding **`12 months`** since sanction with **`>= 80%` funds disbursed** that remain incomplete or lack mandatory geotagged stage verification photos.
  3. **MGNREGA Expenditure & Social Audit:** Flag Gram Panchayats/blocks breaching the statutory **60:40 wage-to-material ratio** (`material_share > 40%`), or holding substantial **unrecovered R9.2 Social Audit misappropriation amounts** with low recovery progress.
* **Mandatory Benign Explanations Considered:** Monsoon/landslide/forest-clearance delays (PMGSY), utility shifting or land-acquisition holds (MPLADS), state-level aggregate material pooling permitted under MGNREGA guidelines, or lagging MIS data-entry uploads after physical completion.

### `M8` — Corporate–Electoral–Procurement Proximity Disclosure Lead
* **Statutory Anchors:** Companies Act 2013 / MCA21 public filings; Election Commission of India (ECI) Electoral Bond disclosures (pursuant to Supreme Court judgment); Representation of the People Act candidate affidavits (ADR / MyNeta); GFR 2017 conflict-of-interest & integrity pact clauses.
* **Target Sources:** ECI Electoral Bond public datasets, MyNeta/ADR public affidavit datasets, MCA21 public corporate metadata, OCDS/CPPP tender awards.
* **Deterministic Triggers:**
  1. **Newly Incorporated High-Value Awardee:** Flag suppliers incorporated **`< 180 days`** prior to tender award winning high-value public contracts (where standard prior-experience turnover norms normally apply).
  2. **Shared-Director Bidder Overlap:** Flag ostensibly competing bidders on the same tender who share a Director Identification Number (DIN), registered office address, or common controlling entity.
  3. **Public Disclosure Proximity:** Cross-link registered corporate awardees (by CIN/name) with statutory public ECI/affidavit records.
* **Strict Framing Rule:** Every `M8` output is strictly labeled a **"Disclosure / Proximity Lead"** — it documents verifiable public-record relationships and **never alleges quid pro quo, bribery, or unlawful influence**.
* **Mandatory Benign Explanations Considered:** Special Purpose Vehicle (SPV) or joint-venture consortium validly incorporated post-qualification under tender terms, DPIIT-recognized startup exemption under GFR Rule 173(i), or lawful political contributions unrelated to procurement evaluation.

---

## 3. Epistemic Lifecycle & 2-of-2 Peer Verification Quorum

Every audit finding follows a strict state machine enforced by the Coordinator Hub (`src/aiwatchdogs/hub/desk.py`):

```
[Dataset + SHA-256]
        │
        ▼ (Node A: anomaly_scan + scrub_pii + lint_neutrality)
   ┌──────────┐    0 anomalies found     ┌─────────────────────┐
   │ raw_lead │ ───────────────────────► │  cleared (credited) │
   └────┬─────┘                          └─────────────────────┘
        │  Auto-enqueues 2 independent peer_verify tasks
        ▼
  ┌─────────────┐
  │ peer_review │  (Displayed with dashed italic badge — NOT verified!)
  └─────┬───────┘
        │  Independent Nodes B & C (distinct contributor_id; UNIQUE(finding_id, contributor_id))
        │  Check: (1) Provenance & SHA-256  (2) Math Reproduction (<=1% tol)
        │         (3) Statutory Rule Validity  (4) Legal Neutrality & Zero PII
        ├──── flag_pii_or_legal ──────────────────► [Quarantined Immediately]
        ├──── reject_false_positive ──────────────► [retracted_false_positive]
        ├──── dispute (consensus < 0.75) ─────────► [disputed] (requires >=3 confirms)
        └──── >= 2 confirms & consensus >= 0.75 ──► [verified] (Solid Emerald Badge)
                                                         │
                                                         ▼
                                            [Auto-Generated RTI Act 2005
                                             Sec 6(1) Draft (<=500 words)]
                                                         │
                                                         ▼ (Human-in-the-loop)
                                      [rti_filed] ─► [official_response_logged]
                                                         │
                                                         ▼
                                             [resolved_explained | escalated]
```

### Independent Quorum Scoring Rules
1. **Strict Author Separation:** The Hub rejects (`HTTP 409 Conflict`) any attempt by the submitting contributor to verify their own lead (`verifier_handle == finding.submitted_by`), and enforces a database-level `UNIQUE(finding_id, contributor_id)` constraint so no contributor can vote twice.
2. **Four Structured Verification Checks (`checks_json`):**
   * `provenance_ok`: Source file/URL resolves, SHA-256 matches, and cited excerpts exist verbatim.
   * `math_reproduced`: Re-running the deterministic engine reproduces the reported metric within 1% relative tolerance.
   * `rule_valid`: The cited GFR/CVC/statutory threshold applies to the fiscal year and entity type.
   * `legal_pii_ok`: Title/summary pass `lint_neutrality()`, `benign_explanations` is substantive and non-empty, and zero PII appears in any field.
3. **Consensus Calculation:**
   * Let $V_{\text{confirm}}$ be the count of `confirm` verdicts and $V_{\text{total}}$ be total peer verdicts (`confirm`, `dispute`, `reject_false_positive`).
   * Consensus ratio $= V_{\text{confirm}} / V_{\text{total}}$.
   * **Promotion to `verified`** requires:
     * Zero `flag_pii_or_legal` verdicts;
     * `V_confirm >= 2` (or `V_confirm >= 3` if any `dispute` verdict was recorded);
     * `consensus >= 0.75`;
     * Passing server-side `lint_neutrality()` and `pii_scrubbed == true`.
4. **Anti-Spam Contributor Scoring:** Contributors earn equal recognition for **verified findings**, **independent peer verifications**, and **cleared / validly refuted records**. Raw token counts or unverified lead volume are never rewarded, preventing false-positive farming.

---

## 4. Visual Epistemic Grammar

Inspired by `pequalnp.com`'s rule *"Only checked looks like a result"*, the web dashboard encodes epistemic status into unmistakable visual typography:

| Status | Visual Treatment | Meaning |
|---|---|---|
| `verified` | Solid upright emerald pill (`#2F6B4F` on `#E6F0E9`, solid `1px` border) | Deterministic math reproduced & approved by $\ge 2$ independent peer nodes. |
| `peer_review` / `raw_lead` | **Dashed** border, *italic* warm stone/amber pill | Preliminary lead awaiting independent peer verification — **not a verified finding**. |
| `cleared` / `resolved_explained` | Calm slate/blue-gray pill | Audited records showing no threshold breach, or anomaly lawfully explained by official response. |
| `disputed` / `retracted_false_positive` | Muted terracotta/red pill (`#A2321F` on `#F7E4E0`) | Contested in peer review or retracted after mathematical/factual refutation. |
| `rti_filed` | Civic indigo pill (`#2B4D9B` on `#E8EEFA`) | Citizen-reviewed RTI Act 2005 Sec 6(1) application logged against this lead. |
| `[SAMPLE DEMO DATA]` | Archival stone badge | Explicitly marks bundled sample datasets vs. live `.gov.in` ingestions. |
