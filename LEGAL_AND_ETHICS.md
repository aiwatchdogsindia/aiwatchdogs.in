# Legal Compliance & Journalistic Ethics Charter (India)

AI Watchdogs India (`aiwatchdogs.in`) is built to operate **strictly within Indian constitutional, statutory, privacy, and cyber-law boundaries**. Our mission is transparent, evidence-based civic journalism and public-record auditing — never defamation, harassment, doxxing, or unauthorized system access.

Every line of code, every autonomous agent prompt, and every public ledger entry is bound by the six pillars below.

---

## 1. Defamation Safe Harbor — Bharatiya Nyaya Sanhita (BNS) 2023, Section 356

Under **Section 356 of the Bharatiya Nyaya Sanhita, 2023** *(formerly Sections 499/500 of the Indian Penal Code)*, defamation protections explicitly recognize lawful exceptions for:
* **First Exception:** Imputation of truth which public good requires to be made or published (*Truth for Public Good*).
* **Second Exception:** Good-faith expression of opinion respecting the public conduct of a public servant in the discharge of public functions.
* **Third Exception:** Good-faith expression of opinion respecting the conduct of any person touching any public question.
* **Ninth Exception:** Imputation made in good faith for the protection of the interests of the person making it, or for the public good.

To ensure every output stays squarely inside factual, good-faith public-interest reporting:

1. **Mandatory Automated Neutrality Linter (`aiwatchdogs.core.safety.lint_neutrality`):**
   * Both the CLI agent runner and the Coordinator Hub reject (`HTTP 422`) any finding title or summary containing accusatory or conclusory criminal labels, including: `scam`, `corrupt`, `corruption`, `bribe`, `fraud`, `kickback`, `guilty`, `stolen`, `rigged`, `crook`, `loot`, and `embezzl*`.
   * **Canonical Framing:** Outputs use strictly neutral statistical and statutory terminology: *"Public Record Anomaly"*, *"Procurement Red-Flag Lead"*, *"Statistical Divergence"*, *"Threshold-Proximity Pattern"*, or *"Disclosure / Proximity Lead"*.
   * Findings describe **documents and arithmetic**, never criminal intent or personal culpability.
2. **Mandatory `benign_explanations` Field:**
   * Every submitted lead MUST include at least one concrete, lawful benign explanation (e.g., GFR Rule 166 proprietary OEM exemption, disaster-relief urgency, MoF Q4 CapEx waiver circular, or DPIIT startup tender relaxation).
   * Leads with an empty `benign_explanations` array are rejected automatically by the Hub.
3. **Verifiable Primary Provenance:**
   * Every lead must link to an official primary source reference, record its cryptographic `sha256` hash, and supply exact pinpoint citations (page, paragraph, table/row ID, verbatim excerpt).

---

## 2. Right of Reply, Corrections & Public Lifecycle

Fair public-interest journalism requires giving public authorities and named corporate entities a clear, permanent avenue to clarify or correct the record:

* **Full Status Lifecycle:**
  `open_lead / verified` → `rti_filed` → `official_response_logged` → `resolved_explained` | `escalated` | `retracted_false_positive`
* **Permanent Right of Reply:** Any official clarification, CPIO reply under the RTI Act 2005, Gazette waiver, or audited corporate clarification submitted with verifiable documentary proof is permanently displayed alongside the original finding (never buried in a footnote).
* **Prompt Retraction of False Positives:** If peer reviewers or official records demonstrate a data-parsing error, superseded corrigendum, or mathematical mistake, the finding is moved immediately to `retracted_false_positive` with a transparent audit note explaining the correction.
* **Contact:** Official clarifications and correction requests may be submitted to **corrections@aiwatchdogs.in** referencing the Finding ID and official document URL/hash.

---

## 3. Privacy & Personal Data Protection — DPDP Act 2023 & Aadhaar Act 2016

AI Watchdogs India relies on **Section 3(c)(ii) of the Digital Personal Data Protection (DPDP) Act, 2023**, which exempts personal data made publicly available by a person under a legal obligation (such as statutory public procurement records, CAG reports, and ECI disclosures). Even for publicly disclosed records, we enforce **data minimization and strict pre-storage PII scrubbing**:

* **Mandatory Pre-Storage PII Scrubber (`aiwatchdogs.core.safety.scrub_pii`):**
  Every ingested text block, dataset excerpt, finding, and whisper is scrubbed automatically before persistence:
  1. **Aadhaar Numbers (Aadhaar Act 2016 compliance):** Detected via 12-digit pattern matching **plus true Verhoeff checksum validation** and immediately masked (`[REDACTED_AADHAAR]`).
  2. **Permanent Account Numbers (PAN):** Pattern `[A-Z]{5}[0-9]{4}[A-Z]` masked (`[REDACTED_PAN]`).
  3. **Financial Details:** Indian bank account numbers and IFSC codes masked (`[REDACTED_BANK]`, `[REDACTED_IFSC]`).
  4. **Personal Contact Info:** Indian mobile numbers and personal email addresses masked (`[REDACTED_PHONE]`, `[REDACTED_EMAIL]`).
  5. **Welfare Muster Rolls:** Private citizen beneficiary names on schemes like MGNREGA are never republished; audits operate on aggregated Gram Panchayat / Block / Work-ID level figures.
* **What IS Retained:** Official public-office designations (e.g., *Executive Engineer, PWD Division*), registered corporate legal entities (CIN / GSTIN / company name), ministry/PSU names, statutory tender IDs, and official `.gov.in` portal URLs.
* **Emergency Quarantine:** Any peer reviewer submitting a `flag_pii_or_legal` verdict triggers immediate quarantine of the finding pending maintainer review.

---

## 4. Lawful Portal Access — Information Technology Act 2000, Section 43

Under **Section 43 of the IT Act, 2000**, unauthorized access, extraction, or disruption of computer systems is unlawful. AI Watchdogs India enforces a strict **Zero Bypass Policy**:

* **NO CAPTCHA Solving:** Connectors and agents MUST NEVER solve, bypass, outsource, or automate CAPTCHAs (e.g., on CPPP `eprocure.gov.in`, GeM `gem.gov.in`, or MCA21 `mca.gov.in`).
* **NO Authentication Bypass:** Agents never use scraped credentials, session hijacking, undocumented private APIs, or paywall workarounds.
* **Polite Crawling Only on Tier 1 & Tier 2 Public Sources:** Automated fetches are restricted to open APIs (OpenBudgetsIndia CKAN, `data.gov.in` OGD, CivicDataLab OCDS mirrors) and publicly linked official PDFs/spreadsheets, always sending an honest identifying User-Agent (`AIWatchdogs-India-OpenAudit/0.1`), honoring `robots.txt`, and maintaining strict rate limits.
* **Tier 3 Human Exports:** For CAPTCHA-gated portals, citizens manually download public tender notices or award CSVs in their own browser and analyze them locally via `aiwatchdogs audit-file`.

---

## 5. Human-in-the-Loop Citizen Action — RTI Act 2005 & Vigilance Channels

AI Watchdogs India **never auto-files** RTI applications, CPGRAMS grievances, or CVC complaints. Autonomous bots submitting legal filings would be abusive, unlawful, and counterproductive.

Instead, for every peer-verified finding, the platform generates a **citizen-reviewable draft** under `aiwatchdogs.core.rti.generate_rti_draft()`:

* **Statutory Compliance:**
  * Anchored in **Section 6(1)** (request for obtaining information) and **Section 2(f)** (definition of existing material records — file notings, certificates, comparative statements, Measurement Books, Utilization Certificates) while noting **Section 4(1)(b)** proactive-disclosure gaps.
  * Strictly enforces the **≤ 500-word limit** of **Rule 3 of the Central Right to Information Rules, 2012**.
  * Never asks accusatory or open-ended *"why"* questions (which CPIOs may lawfully decline as outside Section 2(f)); requests only specific, identifiable existing public records.
  * Includes official filing guidance for **`rtionline.gov.in`**, the statutory **₹10 fee** (with **Section 7(5) BPL fee exemption** note), the **30-day statutory reply period**, and **Section 19(1) First Appeal** timeline guidance.
* **Vigilance Dossiers:** Citizens who wish to escalate verified evidence to **CVC CMS** (`portal.cvc.gov.in`), physical double-sealed **PIDPI** whistleblower channels, **CPGRAMS** (`pgportal.gov.in`), or Parliamentary / PAC representatives review, verify, sign, and submit dossiers themselves.

---

## 6. Transparent Labeling of Sample Demo Data

This open-source repository ships with 8 realistic `[SAMPLE DEMO DATASET]` files in `datasets/sample/` so developers and citizens can boot the Hub and run a local 2-node swarm out of the box without hitting live portals or making real-world allegations against living individuals.

* Every demo dataset uses clearly synthetic tender IDs and placeholder entity names while preserving 100% authentic Indian statutory rules, GFR ₹ thresholds, and real `.gov.in` portal URLs.
* Every API response (`is_demo_sample: true`) and UI card (`[SAMPLE DEMO DATA]` badge) visually distinguishes sample demo records from live public-portal audits.
