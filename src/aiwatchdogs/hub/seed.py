"""Idempotent production seeder for AI Watchdogs India Coordinator Hub.

Populates ONLY genuine Government of India public infrastructure and verifiable
audit work — ZERO fake agents, ZERO fake findings, ZERO fake peer verifications,
ZERO fake whispers, and ZERO fake sponsors/treasury entries:

- All 15 canonical Indian public data sources (Tiers 1–3, official .gov.in/.nic.in URLs)
- All 8 real Audit Missions (M1–M8) tagged ``data_mode = "live_public"``
- All 16 verified HTTP-200 Government of India / CivicDataLab queued ``anomaly_scan``
  tasks with exact canonical dataset URLs and verified SHA-256 checksums.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiwatchdogs.hub.database import PG_SEED_ADVISORY_LOCK_KEY, Database, dumps_json

INDEPENDENCE_PLEDGE = (
    "Sponsors fund compute & infrastructure; deterministic evidence and independent peer "
    "verification decide every finding. No sponsor can edit, suppress, or expedite any audit lead."
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


PUBLIC_SOURCES: list[dict[str, Any]] = [
    {
        "id": "src-ocds-india",
        "slug": "civicdatalab-ocds-india",
        "name": "CivicDataLab OCDS India (Assam & Himachal Pradesh)",
        "pillar": "procurement",
        "authority": "CivicDataLab / Assam Finance Dept (assamtenders.gov.in) & HP Tenders (hptenders.gov.in)",
        "base_url": "https://assam.open-contracting.in/datasets",
        "ingestion_tier": 1,
        "connector_type": "ocds_json",
        "data_contents": "Full OCDS tender, bids, award, buyer, and supplier lifecycle for Assam (FY 2016–23) & HP (FY 2017–21)",
        "format_access": "OCDS JSON ZIP, CSV, XLSX (Direct Open Bulk Files)",
        "robots_policy": "open-license-github",
    },
    {
        "id": "src-cppp",
        "slug": "cppp-state-gepnic",
        "name": "Central Public Procurement Portal (CPPP) & State GePNIC",
        "pillar": "procurement",
        "authority": "National Informatics Centre (NIC) / Ministry of Finance",
        "base_url": "https://eprocure.gov.in",
        "ingestion_tier": 3,
        "connector_type": "csv_upload",
        "data_contents": "Central, PSU & State NITs, corrigenda, BOQs, technical evaluation & AOC summaries",
        "format_access": "Contributor-uploaded official CSV/PDF/XLS exports (CAPTCHA-gated portal)",
        "robots_policy": "tier3-no-captcha-bypass",
    },
    {
        "id": "src-gem",
        "slug": "gem-portal",
        "name": "Government e-Marketplace (GeM)",
        "pillar": "procurement",
        "authority": "GeM SPV, Ministry of Commerce & Industry",
        "base_url": "https://gem.gov.in",
        "ingestion_tier": 3,
        "connector_type": "csv_upload",
        "data_contents": "Bid/RA results, contract values, buyer orgs, L1 prices, item categories",
        "format_access": "Contributor-uploaded official bid/contract exports (CAPTCHA-gated portal)",
        "robots_policy": "tier3-no-captcha-bypass",
    },
    {
        "id": "src-cag",
        "slug": "cag-india",
        "name": "Comptroller and Auditor General (CAG) of India Reports",
        "pillar": "audit",
        "authority": "Office of the Comptroller and Auditor General of India",
        "base_url": "https://cag.gov.in/en/audit-report",
        "ingestion_tier": 1,
        "connector_type": "http_pdf",
        "data_contents": "Union & State Compliance, Performance, Financial & CPSE Audit Reports (2025–2026)",
        "format_access": "Official full PDF reports (Direct Public Download via cag.gov.in)",
        "robots_policy": "strict-polite-1rps",
    },
    {
        "id": "src-apms-pac",
        "slug": "apms-pac-sansad",
        "name": "Audit Para Monitoring System (APMS) & PAC Reports",
        "pillar": "audit",
        "authority": "Controller General of Accounts & Public Accounts Committee, Parliament",
        "base_url": "https://apms.nic.in",
        "ingestion_tier": 1,
        "connector_type": "http_pdf",
        "data_contents": "Action Taken Notes (ATNs), PAC Action Taken Replies (ATRs), Explanatory Notes (>=Rs 100 Cr)",
        "format_access": "Public PAC PDFs (sansad.in) & APMS public summaries",
        "robots_policy": "strict-polite-1rps",
    },
    {
        "id": "src-obi",
        "slug": "openbudgetsindia",
        "name": "OpenBudgetsIndia & CivicDataLab State Budget OBDS",
        "pillar": "budget",
        "authority": "CBGA / CivicDataLab / State Finance Departments (Assam OBDS & AP Treasury)",
        "base_url": "https://openbudgetsindia.org",
        "ingestion_tier": 1,
        "connector_type": "http_xlsx",
        "data_contents": "Union & State budgets, Demands for Grants (OBDS), AP Monthly Treasury, BE/RE/Actuals",
        "format_access": "Open Budgets Data Standard XLSX & CSV public releases",
        "robots_policy": "open-api",
    },
    {
        "id": "src-cga",
        "slug": "union-budget-cga",
        "name": "Union Budget & CGA Monthly Accounts",
        "pillar": "budget",
        "authority": "Ministry of Finance & Controller General of Accounts (CGA)",
        "base_url": "https://cga.nic.in",
        "ingestion_tier": 1,
        "connector_type": "html_mis",
        "data_contents": "Union Government Monthly Accounts at a Glance, Revenue & CapEx actuals vs BE, Flagship Scheme budgets",
        "format_access": "Official cga.nic.in live monthly reports & Union Budget CSV extracts",
        "robots_policy": "strict-polite-1rps",
    },
    {
        "id": "src-ogd",
        "slug": "data-gov-in-ogd",
        "name": "Open Government Data (OGD) Platform India",
        "pillar": "budget",
        "authority": "NIC / Ministry of Electronics & Information Technology",
        "base_url": "https://data.gov.in",
        "ingestion_tier": 1,
        "connector_type": "ogd_api",
        "data_contents": "Ministry datasets across finance, health, rural roads, infrastructure & welfare",
        "format_access": "REST JSON/CSV API (api.data.gov.in/resource/{id}) & public CSV archives",
        "robots_policy": "open-api",
    },
    {
        "id": "src-pfms",
        "slug": "pfms-portal",
        "name": "Public Financial Management System (PFMS) Reports",
        "pillar": "scheme",
        "authority": "Office of CGA, Ministry of Finance",
        "base_url": "https://pfms.nic.in",
        "ingestion_tier": 2,
        "connector_type": "html_mis",
        "data_contents": "Central sector & CSS fund releases, SNA/agency utilization summaries",
        "format_access": "Public MIS reports & CSV/XLS exports (No login)",
        "robots_policy": "strict-polite-robots",
    },
    {
        "id": "src-mgnrega",
        "slug": "mgnrega-mis",
        "name": "MGNREGA Public MIS & R9.2 Social Audit Portal",
        "pillar": "scheme",
        "authority": "Ministry of Rural Development",
        "base_url": "https://nrega.nic.in",
        "ingestion_tier": 2,
        "connector_type": "html_mis",
        "data_contents": "740-district wage vs material spend (statutory 60:40 ratio), work completion & social audit metrics",
        "format_access": "Public HTML MIS tables & CSV exports (zero citizen PII)",
        "robots_policy": "strict-polite-robots",
    },
    {
        "id": "src-pmgsy",
        "slug": "pmgsy-ommas",
        "name": "PMGSY OMMAS (Rural Roads Management System)",
        "pillar": "scheme",
        "authority": "National Rural Infrastructure Development Agency (NRIDA), MoRD",
        "base_url": "https://ommas.nic.in",
        "ingestion_tier": 2,
        "connector_type": "html_mis",
        "data_contents": "All-India district & phase rural road sanctioned vs completed km and financial expenditure",
        "format_access": "Public OMMAS / data.gov.in CSV & MIS exports",
        "robots_policy": "strict-polite-robots",
    },
    {
        "id": "src-mplads",
        "slug": "mplads-esakshi",
        "name": "MPLADS e-SAKSHI Public Dashboard",
        "pillar": "scheme",
        "authority": "Ministry of Statistics & Programme Implementation (MoSPI)",
        "base_url": "https://mplads.mospi.gov.in",
        "ingestion_tier": 2,
        "connector_type": "html_mis",
        "data_contents": "Constituency allocations, recommended/sanctioned/completed works status across India",
        "format_access": "Public dashboard reports & open CSV mirrors",
        "robots_policy": "strict-polite-robots",
    },
    {
        "id": "src-csr-mca",
        "slug": "national-csr-mca21",
        "name": "National CSR Portal & MCA21 Corporate Disclosures",
        "pillar": "corporate_political",
        "authority": "Ministry of Corporate Affairs (MCA)",
        "base_url": "https://www.csr.gov.in",
        "ingestion_tier": 2,
        "connector_type": "csv_upload",
        "data_contents": "Corporate CINs, Top-100 CSR projects & spend by sector/state under Companies Act Sec 135",
        "format_access": "csr.gov.in open CSV mirrors & user-supplied MCA21 extracts",
        "robots_policy": "tier2-3-compliant",
    },
    {
        "id": "src-eci-myneta",
        "slug": "eci-electoral-myneta",
        "name": "ECI Electoral Bonds Disclosures & MyNeta / ADR Affidavits",
        "pillar": "corporate_political",
        "authority": "Election Commission of India / Supreme Court WP(C) 880/2017 Disclosures",
        "base_url": "https://www.eci.gov.in",
        "ingestion_tier": 1,
        "connector_type": "csv_upload",
        "data_contents": "Supreme Court mandated SBI/ECI bond purchaser & political party redemption disclosures (2019–2024)",
        "format_access": "ECI public PDFs & structured open CSV mirrors",
        "robots_policy": "strict-polite-1rps",
    },
    {
        "id": "src-sansad-rti",
        "slug": "sansad-egazette-rti",
        "name": "Digital Sansad, eGazette & RTI Online / CIC Orders",
        "pillar": "parliament",
        "authority": "Parliament of India, Dept. of Publication & Central Information Commission",
        "base_url": "https://sansad.in",
        "ingestion_tier": 1,
        "connector_type": "http_pdf",
        "data_contents": "Lok Sabha & Rajya Sabha Q&A, statutory Gazette notifications, CIC transparency orders",
        "format_access": "Public PDF/JSON/HTML (Human-only RTI filing at rtionline.gov.in)",
        "robots_policy": "strict-polite-1rps",
    },
]

SEED_MISSIONS: list[dict[str, Any]] = [
    {
        "id": "mission-m1",
        "slug": "assam-ocds-single-bid",
        "title": "Assam State Public Procurement Single-Bidder Audit (FY 2016–22)",
        "description": (
            "Audits 39,502 official Assam OCDS procurement releases (assamtenders.gov.in via CivicDataLab) "
            "for single-bidder concentration (>35% buyer share) and shortened bid windows (<14 days) under "
            "GFR 2017 Rule 166 and CVC Single-Tender Guidelines."
        ),
        "pillar": "procurement",
        "methodology_code": "M1",
        "jurisdiction": "State:Assam",
        "ministry_or_authority": "Finance Department, Government of Assam (assamtenders.gov.in)",
        "statutory_anchors": ["GFR-2017-R166", "CVC-Single-Tender-2007", "OECD-Bid-Rigging"],
        "source_ids": ["src-ocds-india", "src-cppp"],
        "data_mode": "live_public",
        "priority": 1,
    },
    {
        "id": "mission-m2",
        "slug": "assam-gfr-split-tendering",
        "title": "Assam GFR Rule 161 ₹50L Threshold-Hugging Audit (FY 2016–22)",
        "description": (
            "Detects rolling 60-day procurement clusters priced at 85%–99.9% of statutory GFR 2017 "
            "open-tendering ceilings (₹50L Rule 161, ₹10L, ₹2.5L) across 39,502 real Assam OCDS tenders "
            "where aggregate cluster spend exceeds the statutory ceiling."
        ),
        "pillar": "procurement",
        "methodology_code": "M2",
        "jurisdiction": "State:Assam",
        "ministry_or_authority": "Finance Department & State Procuring Entities, Government of Assam",
        "statutory_anchors": ["GFR-2017-R149", "GFR-2017-R154", "GFR-2017-R161", "CVC-Splitting"],
        "source_ids": ["src-ocds-india", "src-gem", "src-cppp"],
        "data_mode": "live_public",
        "priority": 2,
    },
    {
        "id": "mission-m3",
        "slug": "ocds-vendor-concentration",
        "title": "Assam & Himachal Pradesh OCDS Awardee Concentration (HHI) Audit",
        "description": (
            "Computes buyer-level Herfindahl–Hirschman Index (HHI > 0.35) and top-vendor award concentration "
            "across 43,669 Assam OCDS releases (FY 2016–23) and 3,991 Himachal Pradesh Health & I&PH OCDS "
            "tenders (FY 2017–21) to surface concentrated award patterns."
        ),
        "pillar": "procurement",
        "methodology_code": "M3",
        "jurisdiction": "State:Assam & Himachal Pradesh",
        "ministry_or_authority": "Assam Finance Dept (assamtenders.gov.in) & HP Tenders (hptenders.gov.in)",
        "statutory_anchors": ["GFR-2017-R173", "CVC-Vendor-Concentration", "Competition-Act-Sec-3"],
        "source_ids": ["src-ocds-india", "src-cppp"],
        "data_mode": "live_public",
        "priority": 3,
    },
    {
        "id": "mission-m4",
        "slug": "ocds-cost-escalation-watch",
        "title": "Assam & HP Awarded Contract vs Estimate Escalation Audit",
        "description": (
            "Compares awarded contract amounts against published tender estimates across full Assam & HP "
            "OCDS releases to flag awards exceeding estimates by >20% alongside unit-price outliers "
            "under GFR 2017 Rule 173 and CVC post-award variation norms."
        ),
        "pillar": "procurement",
        "methodology_code": "M4",
        "jurisdiction": "State:Assam & Himachal Pradesh",
        "ministry_or_authority": "State Public Works & Health Procurement Authorities (Assam & HP)",
        "statutory_anchors": ["GFR-2017-R173", "GeM-Rate-Reasonability", "CVC-Post-Award-Variation"],
        "source_ids": ["src-ocds-india", "src-gem"],
        "data_mode": "live_public",
        "priority": 4,
    },
    {
        "id": "mission-m5",
        "slug": "fiscal-march-rush-budgets",
        "title": "AP Monthly Treasury March Rush, Assam OBDS & Union Flagship Budget Variance Audit",
        "description": (
            "Audits 24,269 Andhra Pradesh monthly treasury rows for GFR Rule 62(3) March Rush breaches "
            "(Q4 >33% / March >15%), 84 Assam OBDS Demands for Grants (>25% BE/RE/Actuals variance), "
            "Union Flagship Scheme budgets (FY 2020–24), and live CGA Monthly Accounts (cga.nic.in)."
        ),
        "pillar": "budget",
        "methodology_code": "M5",
        "jurisdiction": "Union of India, Andhra Pradesh & Assam",
        "ministry_or_authority": "Ministry of Finance (CGA), AP Finance Dept & Assam Finance Dept",
        "statutory_anchors": ["GFR-2017-R62-3", "MoF-CMS-Circular", "PAC-100Cr-Explanatory-Note"],
        "source_ids": ["src-cga", "src-obi", "src-ogd"],
        "data_mode": "live_public",
        "priority": 5,
    },
    {
        "id": "mission-m6",
        "slug": "cag-union-state-audit-paras",
        "title": "CAG of India 2025–2026 Union & State Audit Report Para Extraction",
        "description": (
            "Parses official full-text CAG of India PDF reports tabled in 2025–2026 (Union Reports 6/2026, "
            "5/2026, 39/2025 & MP Jal Jeevan Mission 5/2026) to extract quantified rupee audit paragraphs "
            "for transparent public tracking and human-reviewed RTI follow-up."
        ),
        "pillar": "audit",
        "methodology_code": "M6",
        "jurisdiction": "Union of India & Madhya Pradesh",
        "ministry_or_authority": "Comptroller and Auditor General of India (cag.gov.in) / PAC",
        "statutory_anchors": ["Constitution-Art-151", "CAG-DPC-Act-1971", "PAC-ATN-Timeline"],
        "source_ids": ["src-cag", "src-apms-pac", "src-sansad-rti"],
        "data_mode": "live_public",
        "priority": 6,
    },
    {
        "id": "mission-m7",
        "slug": "rural-schemes-pmgsy-mgnrega-mplads",
        "title": "All-India PMGSY, MGNREGA 60:40 Ratio & MPLADS Works Verification",
        "description": (
            "Verifies physical-vs-financial disbursement gaps across 2,189 All-India PMGSY district-phase "
            "records (₹3.89 Lakh Cr outlay) and statutory 60:40 wage-material compliance across 740 MGNREGA "
            "districts (₹53,567 Cr) alongside MPLADS works registries."
        ),
        "pillar": "scheme",
        "methodology_code": "M7",
        "jurisdiction": "All-India (States & Districts)",
        "ministry_or_authority": "Ministry of Rural Development (NRIDA / MGNREGA) & MoSPI (MPLADS)",
        "statutory_anchors": ["PMGSY-III-Guidelines", "MGNREGA-Sch-II-60-40", "MPLADS-2023-Guidelines"],
        "source_ids": ["src-pmgsy", "src-mgnrega", "src-mplads", "src-pfms"],
        "data_mode": "live_public",
        "priority": 7,
    },
    {
        "id": "mission-m8",
        "slug": "electoral-bonds-mca-csr-disclosure",
        "title": "SC-Ordered SBI/ECI Electoral Bonds × MCA Top-100 CSR Public Disclosure Ledger",
        "description": (
            "Cross-references 20,551 Supreme Court mandated SBI/ECI Electoral Bond disclosure records "
            "(₹12,769.09 Cr, 2019–2024) against the MCA National CSR Top-100 corporate CIN registry using "
            "strictly factual, neutral BNS Sec 356 public-registry disclosure summaries."
        ),
        "pillar": "corporate_political",
        "methodology_code": "M8",
        "jurisdiction": "Union of India",
        "ministry_or_authority": "Election Commission of India (ECI) & Ministry of Corporate Affairs (MCA)",
        "statutory_anchors": ["SC-WP-880-2017-Electoral-Bonds", "Companies-Act-Sec-135-CSR", "RTI-Sec-4-1-b"],
        "source_ids": ["src-eci-myneta", "src-csr-mca"],
        "data_mode": "live_public",
        "priority": 8,
    },
]

# All 16 verified HTTP-200 real Government of India & open civic mirror audit tasks
REAL_AUDIT_TASKS: list[dict[str, Any]] = [
    {
        "id": "tsk-real-01",
        "mission_id": "mission-m1",
        "mission_slug": "assam-ocds-single-bid",
        "methodology_code": "M1",
        "title": "Assam Health & PWD Single-Bidder Audit (FY 2016–22, 39,502 tenders)",
        "instructions": (
            "Fetch the verified Assam OCDS v2 flat CSV (39,502 tenders), verify SHA-256 checksum, "
            "run deterministic M1 single-bidder (>35%) and compressed bid-window (<14d) checks "
            "(notably Health & Family Welfare, KAAC PWD, Cultural Affairs), and submit neutral "
            "findings with benign explanations or cleared metrics."
        ),
        "payload": {
            "dataset_url": "https://raw.githubusercontent.com/CivicDataLab/assam-tenders-data/main/data/ProcessedData/ocds-mapped-data/v2/ocds_mapped_data_fiscal_year_2016_2022_v2.csv",
            "source_url": "https://assam.open-contracting.in/datasets",
            "expected_sha256": "01a3ab8b7dcd3023e10f2e14cf7685d16825932feb88a363d69bc1177af4a77e",
            "methodology_code": "M1",
            "jurisdiction": "State:Assam",
            "fiscal_year": "FY2016-22",
            "description": "39,502 Assam state OCDS tenders (assamtenders.gov.in via CivicDataLab); M1 single-bidder & short-window audit.",
            "artifact_title": "Assam OCDS Tenders Flat CSV v2 (FY 2016–2022)",
            "is_demo_sample": False,
        },
    },
    {
        "id": "tsk-real-02",
        "mission_id": "mission-m2",
        "mission_slug": "assam-gfr-split-tendering",
        "methodology_code": "M2",
        "title": "Assam GFR Rule 161 (₹50L) Threshold-Hugging Audit (FY 2016–22)",
        "instructions": (
            "Fetch the verified Assam OCDS v2 flat CSV (39,502 tenders), verify SHA-256 checksum, "
            "run deterministic M2 rolling 60-day threshold-hugging detection for ₹42.5L–₹49.95L "
            "clusters under GFR Rule 161 (incl. BTC-PWD & NHM Assam), and submit neutral findings."
        ),
        "payload": {
            "dataset_url": "https://raw.githubusercontent.com/CivicDataLab/assam-tenders-data/main/data/ProcessedData/ocds-mapped-data/v2/ocds_mapped_data_fiscal_year_2016_2022_v2.csv",
            "source_url": "https://assam.open-contracting.in/datasets",
            "expected_sha256": "01a3ab8b7dcd3023e10f2e14cf7685d16825932feb88a363d69bc1177af4a77e",
            "methodology_code": "M2",
            "jurisdiction": "State:Assam",
            "fiscal_year": "FY2016-22",
            "description": "39,502 Assam state OCDS tenders; M2 GFR Rule 161 ₹50L threshold-hugging cluster audit.",
            "artifact_title": "Assam OCDS Tenders Flat CSV v2 (FY 2016–2022)",
            "is_demo_sample": False,
        },
    },
    {
        "id": "tsk-real-03",
        "mission_id": "mission-m3",
        "mission_slug": "ocds-vendor-concentration",
        "methodology_code": "M3",
        "title": "Assam Full OCDS Awardee Concentration (HHI) Audit (43,669 releases)",
        "instructions": (
            "Fetch the full Assam OCDS JSON ZIP package (43,669 releases, 5,258 awards), verify "
            "SHA-256 checksum, run deterministic M3 buyer-level HHI (>0.35) awardee concentration "
            "analysis, and submit neutral findings with benign explanations."
        ),
        "payload": {
            "dataset_url": "https://raw.githubusercontent.com/CivicDataLab/assam-tenders-data/main/data/ProcessedData/ocds-mapped-data/current/ocds_mapped_data.json.zip",
            "source_url": "https://assam.open-contracting.in/datasets",
            "expected_sha256": "c1f8de1058b0203d521d13423fe346d4011bfc824a27ac0f623c76c7de3a7019",
            "methodology_code": "M3",
            "jurisdiction": "State:Assam",
            "fiscal_year": "FY2016-23",
            "description": "43,669 Assam OCDS releases (5,258 awards); M3 buyer awardee HHI concentration audit.",
            "artifact_title": "Assam OCDS Full Procurement Package (FY 2016–2023)",
            "is_demo_sample": False,
        },
    },
    {
        "id": "tsk-real-04",
        "mission_id": "mission-m3",
        "mission_slug": "ocds-vendor-concentration",
        "methodology_code": "M3",
        "title": "Himachal Pradesh Health & I&PH OCDS Audit (FY 2017–21, 3,991 tenders)",
        "instructions": (
            "Fetch the Himachal Pradesh Health & PWD OCDS Excel workbook (3,991 tenders × 4,213 awards), "
            "verify SHA-256 checksum, join tenders and awards on ocid, run deterministic M3 awardee "
            "concentration (HHI > 0.35) checks, and submit neutral findings."
        ),
        "payload": {
            "dataset_url": "https://raw.githubusercontent.com/CivicDataLab/himachal-pradesh-health-procurement-OCDS/master/HP%20Health%20Procurements(FY%202017-18%20to%202020-21%20July).xlsx",
            "source_url": "https://hptenders.gov.in",
            "expected_sha256": "0744e24693c73eb84ff9071a8cef6ba5120b77c19cbc442a2c89b56d8460edcf",
            "methodology_code": "M3",
            "jurisdiction": "State:Himachal Pradesh",
            "fiscal_year": "FY2017-21",
            "description": "Himachal Pradesh Health & I&PH OCDS workbook (hptenders.gov.in via CivicDataLab); 3,991 tenders × 4,213 awards.",
            "artifact_title": "Himachal Pradesh Health & PWD Procurement OCDS (FY 2017–2021)",
            "is_demo_sample": False,
        },
    },
    {
        "id": "tsk-real-05",
        "mission_id": "mission-m4",
        "mission_slug": "ocds-cost-escalation-watch",
        "methodology_code": "M4",
        "title": "Assam Awarded Contract vs Tender Estimate Escalation Audit (FY 2016–23)",
        "instructions": (
            "Fetch the full Assam OCDS JSON ZIP package (43,669 releases), verify SHA-256 checksum, "
            "run deterministic M4 contract-award vs tender-estimate escalation checks (>20% above "
            "published estimate), and submit top neutral findings with benign explanations."
        ),
        "payload": {
            "dataset_url": "https://raw.githubusercontent.com/CivicDataLab/assam-tenders-data/main/data/ProcessedData/ocds-mapped-data/current/ocds_mapped_data.json.zip",
            "source_url": "https://assam.open-contracting.in/datasets",
            "expected_sha256": "c1f8de1058b0203d521d13423fe346d4011bfc824a27ac0f623c76c7de3a7019",
            "methodology_code": "M4",
            "jurisdiction": "State:Assam",
            "fiscal_year": "FY2016-23",
            "description": "43,669 Assam OCDS releases; M4 awarded contract value vs published tender estimate escalation (>20%).",
            "artifact_title": "Assam OCDS Full Procurement Package (FY 2016–2023)",
            "is_demo_sample": False,
        },
    },
    {
        "id": "tsk-real-06",
        "mission_id": "mission-m5",
        "mission_slug": "fiscal-march-rush-budgets",
        "methodology_code": "M5",
        "title": "Andhra Pradesh Monthly Treasury March Rush Audit (FY 2014–18, 24,269 rows)",
        "instructions": (
            "Fetch the Andhra Pradesh Monthly State Treasury CSV (24,269 monthly rows, ₹5,148.33 Cr), "
            "verify SHA-256 checksum, roll up by FY × District × Major Head, and run M5 GFR Rule 62(3) "
            "Q4 (>33%) and March (>15%) expenditure pacing checks."
        ),
        "payload": {
            "dataset_url": "https://raw.githubusercontent.com/CivicDataLab/child-budget-tracker/develop_0.1/data/processed_data/andhra_pradesh_child_treasury.csv",
            "source_url": "https://github.com/CivicDataLab/child-budget-tracker",
            "expected_sha256": "172e14cb28135ce69df5aba7d6cf6816993790732db14718ec7a4212ff378c4e",
            "methodology_code": "M5",
            "jurisdiction": "State:Andhra Pradesh",
            "fiscal_year": "FY2014-18",
            "description": "24,269 monthly AP Finance Dept treasury expenditure rows (₹5,148.33 Cr); M5 Q4/March Rush pacing audit.",
            "artifact_title": "Andhra Pradesh Monthly State Treasury Expenditure (FY 2014–2018)",
            "is_demo_sample": False,
        },
    },
    {
        "id": "tsk-real-07",
        "mission_id": "mission-m5",
        "mission_slug": "fiscal-march-rush-budgets",
        "methodology_code": "M5",
        "title": "Assam State Budget OBDS Demand Variance Audit (FY 2022–23, 84 Demands)",
        "instructions": (
            "Fetch the Assam State Budget OBDS Expenditure Excel workbook (17,744 rows across 84 Demands "
            "for Grants), verify SHA-256 checksum, and run deterministic M5 BE/RE/Actuals (>25% variance) checks."
        ),
        "payload": {
            "dataset_url": "https://raw.githubusercontent.com/CivicDataLab/Assam-Budget-OBDS/main/Budget%20OBDS/assam_expenditure_22-23.xlsx",
            "source_url": "https://github.com/CivicDataLab/Assam-Budget-OBDS",
            "expected_sha256": "18f03d25254f2a6660e2937e9cfe8535ff67128e7217367cd30d680882e82126",
            "methodology_code": "M5",
            "jurisdiction": "State:Assam",
            "fiscal_year": "FY2022-23",
            "description": "17,744 Assam Finance Dept OBDS expenditure rows across 84 Demands for Grants; M5 BE/RE/Actuals variance audit.",
            "artifact_title": "Assam State Budget OBDS Expenditure (FY 2022–23)",
            "is_demo_sample": False,
        },
    },
    {
        "id": "tsk-real-08",
        "mission_id": "mission-m5",
        "mission_slug": "fiscal-march-rush-budgets",
        "methodology_code": "M5",
        "title": "Union Flagship Schemes BE vs RE vs Actuals Variance (FY 2020–24)",
        "instructions": (
            "Fetch the Union Budget Flagship Schemes CSV (MGNREGA, PM-JAY, PM-KISAN, Jal Jeevan Mission, "
            "PMAY for FY 2020–24), verify SHA-256 checksum, and run M5 BE vs RE vs Actuals (>25%) variance checks."
        ),
        "payload": {
            "dataset_url": "https://raw.githubusercontent.com/Varnasr/PolicyStack/main/data/budgets/scheme_budgets_2020_2024.csv",
            "source_url": "https://indiabudget.gov.in",
            "expected_sha256": "646a1059f49d88e35f892a75d95697b1dac18274d29e6be469360fc801d63702",
            "methodology_code": "M5",
            "jurisdiction": "Union of India",
            "fiscal_year": "FY2020-24",
            "description": "Union Budget Flagship Schemes BE/RE/Actuals (FY 2020–24) across MGNREGA, PM-JAY, PM-KISAN, JJM, PMAY.",
            "artifact_title": "Union Budget Flagship Schemes BE/RE/Actuals (FY 2020–24)",
            "is_demo_sample": False,
        },
    },
    {
        "id": "tsk-real-09",
        "mission_id": "mission-m5",
        "mission_slug": "fiscal-march-rush-budgets",
        "methodology_code": "M5",
        "title": "CGA Union Government Monthly Accounts Pacing Check (FY 2024–25 up to Dec 2024)",
        "instructions": (
            "Fetch the official CGA Union Government Monthly Accounts page on cga.nic.in (FY 2024–25 "
            "up to Dec 2024), parse live Revenue & CapEx actuals vs BE tables, and run M5 fiscal pacing verification."
        ),
        "payload": {
            "dataset_url": "https://cga.nic.in/MonthlyReport/Published/12/2024-2025.aspx",
            "source_url": "https://cga.nic.in/MonthlyReport/Published/12/2024-2025.aspx",
            "expected_sha256": "f9b7de509a921b8542f16adae9c5deba5d337dafcf0780670cf818b2506b4f68",
            "methodology_code": "M5",
            "jurisdiction": "Union of India",
            "fiscal_year": "FY2024-25",
            "description": "Official cga.nic.in Union Government Monthly Accounts at a Glance (up to Dec 2024; CapEx 61.7% vs Revenue 68.7%).",
            "artifact_title": "CGA Union Government Monthly Accounts (FY 2024–25 Dec)",
            "is_demo_sample": False,
        },
    },
    {
        "id": "tsk-real-10",
        "mission_id": "mission-m6",
        "mission_slug": "cag-union-state-audit-paras",
        "methodology_code": "M6",
        "title": "CAG Union Report No. 6 of 2026 — Accounts of the Union Government 2024–25 (MoF)",
        "instructions": (
            "Fetch the official CAG Union Report No. 6 of 2026 PDF (156 pages), verify SHA-256 checksum, "
            "extract numbered quantified rupee audit paragraphs with M6, scrub any incidental reference PII, "
            "and submit top neutral audit findings."
        ),
        "payload": {
            "dataset_url": "https://d1jmc0u14z2tmp.cloudfront.net/uploads/download_audit_report/2026/6of2026UGEng-069ce496f1158e0.22682438.pdf",
            "source_url": "https://cag.gov.in/en/audit-report/details/125152",
            "expected_sha256": "21ab04ae408b6f413b3e0e334ed1fe6005907a19ffb0db1d8a9bf9a603ab01fa",
            "methodology_code": "M6",
            "jurisdiction": "Union of India",
            "fiscal_year": "FY2024-25",
            "description": "CAG Union Report No. 6 of 2026 — Financial Audit of Accounts of the Union Government 2024–25 (156 pp).",
            "artifact_title": "CAG Union Report No. 6 of 2026 (Accounts of the Union Government 2024–25)",
            "is_demo_sample": False,
        },
    },
    {
        "id": "tsk-real-11",
        "mission_id": "mission-m6",
        "mission_slug": "cag-union-state-audit-paras",
        "methodology_code": "M6",
        "title": "CAG Union Report No. 5 of 2026 — Compliance Audit on Finance & Communication",
        "instructions": (
            "Fetch the official CAG Union Report No. 5 of 2026 PDF (172 pages, year ended 31 Mar 2024), "
            "verify SHA-256 checksum, extract quantified compliance audit paragraphs (e.g. Para 2.1 ₹78.92 Cr "
            "non-levy of GST on PLI/RPLI), and submit top neutral findings."
        ),
        "payload": {
            "dataset_url": "https://d1jmc0u14z2tmp.cloudfront.net/uploads/download_audit_report/2026/5of2026UGEnglish-069ce38b4a151b0.15050638.pdf",
            "source_url": "https://cag.gov.in/en/audit-report/details/125146",
            "expected_sha256": "4fac5c46681b628fc43db5a6ade2e068e134dea249ce968357961c91ba8b812b",
            "methodology_code": "M6",
            "jurisdiction": "Union of India",
            "fiscal_year": "FY2023-24",
            "description": "CAG Union Report No. 5 of 2026 — Compliance Audit on Finance & Communication (172 pp, 54 numbered sections).",
            "artifact_title": "CAG Union Report No. 5 of 2026 (Finance & Communication Compliance)",
            "is_demo_sample": False,
        },
    },
    {
        "id": "tsk-real-12",
        "mission_id": "mission-m6",
        "mission_slug": "cag-union-state-audit-paras",
        "methodology_code": "M6",
        "title": "CAG Union Report No. 39 of 2025 — Commercial CPSE Compliance Observations",
        "instructions": (
            "Fetch the official CAG Union Report No. 39 of 2025 PDF (228 pages, 280 numbered sections), "
            "verify SHA-256 checksum, extract highest-value quantified CPSE compliance audit paragraphs "
            "via M6, and submit neutral findings with benign explanations."
        ),
        "payload": {
            "dataset_url": "https://d1jmc0u14z2tmp.cloudfront.net/uploads/download_audit_report/2025/Report-of-CAG-March-2024-English-for-Approval_02-Feb-069ca6947a16ae2.43363906.pdf",
            "source_url": "https://cag.gov.in/en/audit-report/details/125046",
            "expected_sha256": "256906afa0276d833821d7666ba2f47ccd05f8fea2cdd205c3d45ac50a46c037",
            "methodology_code": "M6",
            "jurisdiction": "Union of India",
            "fiscal_year": "FY2023-24",
            "description": "CAG Union Report No. 39 of 2025 — Commercial CPSE Compliance Audit Observations (228 pp, 280 sections).",
            "artifact_title": "CAG Union Report No. 39 of 2025 (Union Commercial CPSE Compliance)",
            "is_demo_sample": False,
        },
    },
    {
        "id": "tsk-real-13",
        "mission_id": "mission-m6",
        "mission_slug": "cag-union-state-audit-paras",
        "methodology_code": "M6",
        "title": "CAG MP Report No. 5 of 2026 (Jal Jeevan Mission) & CAG Union 35/2025 (NLC India)",
        "instructions": (
            "Fetch the official CAG Madhya Pradesh Report No. 5 of 2026 on Jal Jeevan Mission (152 pages, "
            "143 numbered sections), verify SHA-256 checksum, extract quantified performance audit paragraphs "
            "with M6 (companion report: CAG Union 35/2025 NLC India Ltd), and submit neutral findings."
        ),
        "payload": {
            "dataset_url": "https://d1jmc0u14z2tmp.cloudfront.net/uploads/download_audit_report/2026/MP-JJM-Report-No.-5-of-2026-Bhopal---English-06a674f93c3faf1.25493877.pdf",
            "source_url": "https://cag.gov.in/en/audit-report",
            "expected_sha256": "8ee505d3cd466f4abf354f578c1a21b810772c159825977b38489d8b887e0566",
            "companion_url": "https://d1jmc0u14z2tmp.cloudfront.net/uploads/download_audit_report/2026/CAG---NLC-India-Limited-2025-English-FINAL-SIGNATORY-10-12-2025-0698c6a3b5d7e42.69686670.pdf",
            "companion_sha256": "0ebeda2a13ccf692064942423886177aafe8d189c2cebf3b7a8cf9c74adbe631",
            "methodology_code": "M6",
            "jurisdiction": "State:Madhya Pradesh",
            "fiscal_year": "FY2024-25",
            "description": "CAG MP State Report No. 5 of 2026 — Performance Audit of Jal Jeevan Mission (152 pp, 143 sections) + companion CAG 35/2025 NLC India Ltd.",
            "artifact_title": "CAG MP Report No. 5 of 2026 — Performance Audit of Jal Jeevan Mission",
            "is_demo_sample": False,
        },
    },
    {
        "id": "tsk-real-14",
        "mission_id": "mission-m7",
        "mission_slug": "rural-schemes-pmgsy-mgnrega-mplads",
        "methodology_code": "M7",
        "title": "All-India PMGSY Physical vs Financial Gap Verification (2,189 district-phases)",
        "instructions": (
            "Fetch the All-India PMGSY OMMAS / data.gov.in district-phase CSV (2,189 rows, ₹3.89 Lakh Cr "
            "outlay), verify SHA-256 checksum, and run M7 checks flagging district-phases with >=90% financial "
            "expenditure and <70% physical road km completion."
        ),
        "payload": {
            "dataset_url": "https://raw.githubusercontent.com/aditya-kr86/PMGSY_Multiclass_ML/main/data/PMGSY_DATASET.csv",
            "source_url": "https://ommas.nic.in",
            "expected_sha256": "07f4df8cc2532814af60b3d3686169a589e414b206e37d886be97691c1253635",
            "methodology_code": "M7",
            "jurisdiction": "All-India (States & Districts)",
            "fiscal_year": "FY2023-24",
            "description": "2,189 All-India PMGSY district × scheme rows (₹3,89,389.1 Cr outlay); M7 financial >=90% vs physical <70% gap audit.",
            "artifact_title": "PMGSY District & Phase Physical-Financial Progress (OMMAS / data.gov.in)",
            "is_demo_sample": False,
        },
    },
    {
        "id": "tsk-real-15",
        "mission_id": "mission-m7",
        "mission_slug": "rural-schemes-pmgsy-mgnrega-mplads",
        "methodology_code": "M7",
        "title": "All-India MGNREGA 60:40 Wage-Material Ratio (740 Districts) & MPLADS Works Audit",
        "instructions": (
            "Fetch the semicolon-delimited All-India MGNREGA MIS CSV (740 districts, ₹53,567.1 Cr), "
            "verify SHA-256 checksum, and run M7 statutory 60:40 wage-to-material ratio checks "
            "(>40% material share; companion dataset: 60,359-row MPLADS works registry)."
        ),
        "payload": {
            "dataset_url": "https://raw.githubusercontent.com/Arpita-deb/NREGA-Data-Analysis/main/NREGA%20Data.csv",
            "source_url": "https://nrega.nic.in",
            "expected_sha256": "72c8ec81e12db3cfd1944668dfabe7c16e723c0caf23525009eebfd50683c2bc",
            "companion_url": "https://raw.githubusercontent.com/Vonter/india-mplads-works/main/csv/MPLADS.csv",
            "companion_sha256": "aa1d0b7c9c6bbe014a1af772a22ab3dc0a6eb24d043695060abaa0f94333e413",
            "methodology_code": "M7",
            "jurisdiction": "All-India (740 Districts)",
            "fiscal_year": "FY2023-24",
            "description": "740-district MGNREGA MIS expenditure dataset (₹53,567.1 Cr); statutory 60:40 material ratio check (+ MPLADS companion).",
            "artifact_title": "MGNREGA District-Wise Expenditure, Wages & Material MIS (nrega.nic.in)",
            "is_demo_sample": False,
        },
    },
    {
        "id": "tsk-real-16",
        "mission_id": "mission-m8",
        "mission_slug": "electoral-bonds-mca-csr-disclosure",
        "methodology_code": "M8",
        "title": "SC-Ordered SBI/ECI Electoral Bonds × MCA Top-100 CSR Cross-Disclosure Audit",
        "instructions": (
            "Fetch the SC-ordered matched SBI/ECI Electoral Bonds CSV (20,551 bonds, ₹12,769.09 Cr) and "
            "secondary MCA National CSR Top-100 CIN CSV, verify SHA-256 checksums, and run M8 strictly "
            "neutral public-registry corporate CIN cross-disclosure analysis (zero accusatory wording)."
        ),
        "payload": {
            "dataset_url": "https://raw.githubusercontent.com/saisantoshv3/electoral_bonds/main/data/final.csv",
            "secondary_url": "https://raw.githubusercontent.com/spfrantz/MCA-CSR-Scraper/master/examples/csr_top_100.csv",
            "secondary_sha256": "c186fed19af3574e83cbb826242757e45e5e634b5c21f080b4c51b30e9fd9f40",
            "csr_projects_url": "https://raw.githubusercontent.com/spfrantz/MCA-CSR-Scraper/master/examples/csr_top_100_results.csv",
            "source_url": "https://www.eci.gov.in",
            "expected_sha256": "192489338f5cfb70df17566776db936ccd7ce3e732fb7dc7cb6d87faca8d62e6",
            "methodology_code": "M8",
            "jurisdiction": "Union of India",
            "fiscal_year": "FY2019-24",
            "description": "20,551 matched SC-ordered SBI/ECI Electoral Bonds (₹12,769.09 Cr) × MCA Top-100 CSR CIN registry (24 corporate overlaps).",
            "artifact_title": "SC-Ordered SBI/ECI Electoral Bonds × MCA CSR Top-100 Registry",
            "is_demo_sample": False,
        },
    },
]


def seed_database(db: Database, repo_root: Path | None = None) -> bool:
    """Populate real Indian public sources, 8 live missions, and 16 queued tasks idempotently.

    Returns True if initial seeding ran, False if already seeded.
    Never seeds fake agents, fake findings, fake peer verifications, fake whispers, or fake sponsors.
    """
    _ = repo_root  # Retained in signature for caller compatibility
    with db.read() as conn:
        row = conn.execute("SELECT COUNT(*) AS cnt FROM public_sources").fetchone()
        if row and row["cnt"] > 0:
            return False

    now_iso = _utc_now_iso()

    with db.transaction() as conn:
        # Serialize concurrent cold-start seeding on Postgres & re-check inside write lock
        if db.is_postgres:
            conn.execute(f"SELECT pg_advisory_xact_lock({PG_SEED_ADVISORY_LOCK_KEY})")
        row = conn.execute("SELECT COUNT(*) AS cnt FROM public_sources").fetchone()
        if row and row["cnt"] > 0:
            return False

        # 1. All 15 canonical Indian public data sources (Tiers 1-3)
        for src in PUBLIC_SOURCES:
            conn.execute(
                """
                INSERT OR IGNORE INTO public_sources (
                    id, slug, name, pillar, authority, base_url, ingestion_tier,
                    connector_type, data_contents, format_access, config_json,
                    robots_policy, last_synced_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    src["id"],
                    src["slug"],
                    src["name"],
                    src["pillar"],
                    src["authority"],
                    src["base_url"],
                    src["ingestion_tier"],
                    src["connector_type"],
                    src["data_contents"],
                    src["format_access"],
                    dumps_json({"tier": src["ingestion_tier"]}),
                    src["robots_policy"],
                    now_iso,
                ),
            )

        # 2. All 8 real Government of India Audit Missions (data_mode = 'live_public')
        task_counts: dict[str, int] = {}
        for task in REAL_AUDIT_TASKS:
            mid = task["mission_id"]
            task_counts[mid] = task_counts.get(mid, 0) + 1

        for m in SEED_MISSIONS:
            conn.execute(
                """
                INSERT OR IGNORE INTO missions (
                    id, slug, title, description, pillar, methodology_code,
                    jurisdiction, ministry_or_authority, statutory_anchors_json,
                    source_ids_json, sample_dataset_path, data_mode, status,
                    priority, total_tasks, completed_tasks, verified_findings_count,
                    cleared_count, monetary_audited_inr, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, 'active', ?, ?, 0, 0, 0, 0.0, ?)
                """,
                (
                    m["id"],
                    m["slug"],
                    m["title"],
                    m["description"],
                    m["pillar"],
                    m["methodology_code"],
                    m["jurisdiction"],
                    m["ministry_or_authority"],
                    dumps_json(m["statutory_anchors"]),
                    dumps_json(m["source_ids"]),
                    m["data_mode"],
                    m["priority"],
                    task_counts.get(m["id"], 0),
                    now_iso,
                ),
            )

        # 3. All 16 verified HTTP-200 real Government of India queued anomaly_scan tasks
        for task in REAL_AUDIT_TASKS:
            conn.execute(
                """
                INSERT OR IGNORE INTO tasks (
                    id, mission_id, mission_slug, methodology_code, task_type,
                    title, instructions, payload_json, target_finding_id,
                    excluded_handles_json, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'anomaly_scan', ?, ?, ?, NULL, '[]', 'queued', ?, ?)
                """,
                (
                    task["id"],
                    task["mission_id"],
                    task["mission_slug"],
                    task["methodology_code"],
                    task["title"],
                    task["instructions"],
                    dumps_json(task["payload"]),
                    now_iso,
                    now_iso,
                ),
            )

    return True
