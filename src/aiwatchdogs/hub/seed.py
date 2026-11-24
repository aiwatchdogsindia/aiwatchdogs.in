"""Versioned idempotent production seeder for AI Watchdogs India Coordinator Hub.

Populates ONLY genuine Government of India public infrastructure and verifiable
audit work — ZERO fake agents, ZERO fake findings, ZERO fake peer verifications,
ZERO fake whispers, and ZERO fake sponsors/treasury entries:

- All 15 canonical Indian public data sources (Tiers 1–3) with separated
  ``official_publisher``, ``source_landing_page``, ``mirror_name``, and ``retrieval_url``
- All 8 real Audit Missions (M1–M8) tagged ``data_mode = "live_public"`` with
  explicit ``financial_quantity_type`` and unit metadata
- All 16 verified Government of India / CivicDataLab queued ``anomaly_scan`` tasks
  pinned to immutable GitHub commit SHAs (zero mutable ``main``/``master`` branch URLs)
  and verified SHA-256 checksums, plus explicit caveat metadata on the PolicyStack
  MGNREGA FY 2020–21 mirror defect (citing official Union Budget Demand No. 87).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiwatchdogs.hub.database import PG_SEED_ADVISORY_LOCK_KEY, Database, dumps_json

SEED_VERSION = 2

INDEPENDENCE_PLEDGE = (
    "Sponsors fund compute & infrastructure; deterministic evidence and independent peer "
    "verification decide every finding. No sponsor can edit, suppress, or expedite any audit lead."
)

# Verified immutable GitHub commit SHAs pinned on 2026-11-24
PINNED_COMMITS: dict[str, str] = {
    "CivicDataLab/assam-tenders-data": "1e12e516748130c15398b2501fb092bfa7413941",
    "CivicDataLab/himachal-pradesh-health-procurement-OCDS": "e28aec0b039ab5c7eba4da758b20c297cd1f83a4",
    "CivicDataLab/child-budget-tracker": "ff03978dbc64fc6eba95fe6ad931b074c52df3c0",
    "CivicDataLab/Assam-Budget-OBDS": "f4965c2d92c02d5ea34319b7d42c78ed1cd03da1",
    "Varnasr/PolicyStack": "1a894d351421c0fc31e2a4617ce8eae783cdfd3a",
    "aditya-kr86/PMGSY_Multiclass_ML": "1538fd11fd6e64233bd494c79af55e6811266f34",
    "Arpita-deb/NREGA-Data-Analysis": "c6c0e2d6b2e8e1b6d86150d8bc122e3927b8072a",
    "Vonter/india-mplads-works": "607d6f3eb99ceadba12619f3bbb9ff6e06d52ae5",
    "saisantoshv3/electoral_bonds": "aa8b9e022d29f762fd174fa9371ad2e02a85e777",
    "spfrantz/MCA-CSR-Scraper": "dc4b0044265d5628adef739bf7cea5b091acb856",
}

FINANCIAL_QUANTITY_LABELS: dict[str, str] = {
    "tender_estimates": "Published tender estimates (not awarded payments)",
    "awarded_contract_values": "Disclosed awarded contract values (OCDS awards)",
    "budget_and_treasury_expenditure": "State/Union budget & monthly treasury figures",
    "cag_report_monetary_figures": "CAG audit report monetary amounts (mixed financial items)",
    "scheme_outlay_and_expenditure": "Welfare scheme sanctioned outlay & reported expenditure",
    "electoral_bond_disclosures": "Disclosed SBI/ECI electoral bond face value",
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _gh_raw(repo: str, path: str) -> str:
    sha = PINNED_COMMITS[repo]
    return f"https://raw.githubusercontent.com/{repo}/{sha}/{path}"


PUBLIC_SOURCES: list[dict[str, Any]] = [
    {
        "id": "src-ocds-india",
        "slug": "civicdatalab-ocds-india",
        "name": "CivicDataLab OCDS India (Assam & Himachal Pradesh)",
        "pillar": "procurement",
        "authority": "Assam Finance Dept (assamtenders.gov.in) & HP Tenders (hptenders.gov.in)",
        "base_url": "https://assamtenders.gov.in",
        "official_publisher": "Finance Department, Government of Assam & Government of Himachal Pradesh",
        "source_landing_page": "https://assamtenders.gov.in",
        "mirror_name": "CivicDataLab Open Contracting India",
        "retrieval_url": "https://assam.open-contracting.in/datasets",
        "ingestion_tier": 1,
        "connector_type": "ocds_json",
        "data_contents": "OCDS-mapped tender, bids, award, buyer, and supplier snapshots for Assam (FY 2016–23) & HP (FY 2017–21)",
        "format_access": "Pinned GitHub OCDS JSON ZIP, CSV, XLSX civic mirrors",
        "robots_policy": "open-license-github",
    },
    {
        "id": "src-cppp",
        "slug": "cppp-state-gepnic",
        "name": "Central Public Procurement Portal (CPPP) & State GePNIC",
        "pillar": "procurement",
        "authority": "National Informatics Centre (NIC) / Ministry of Finance",
        "base_url": "https://eprocure.gov.in",
        "official_publisher": "National Informatics Centre (NIC), Ministry of Finance",
        "source_landing_page": "https://eprocure.gov.in",
        "mirror_name": "",
        "retrieval_url": "https://eprocure.gov.in",
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
        "official_publisher": "Government e-Marketplace (GeM SPV), Ministry of Commerce & Industry",
        "source_landing_page": "https://gem.gov.in",
        "mirror_name": "",
        "retrieval_url": "https://gem.gov.in",
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
        "official_publisher": "Comptroller and Auditor General of India",
        "source_landing_page": "https://cag.gov.in/en/audit-report",
        "mirror_name": "",
        "retrieval_url": "https://cag.gov.in/en/audit-report",
        "ingestion_tier": 1,
        "connector_type": "http_pdf",
        "data_contents": "Union & State Compliance, Performance, Financial & CPSE Audit Reports (2025–2026)",
        "format_access": "Official full PDF reports (Direct Public Download via cag.gov.in CDN)",
        "robots_policy": "strict-polite-1rps",
    },
    {
        "id": "src-apms-pac",
        "slug": "apms-pac-sansad",
        "name": "Audit Para Monitoring System (APMS) & PAC Reports",
        "pillar": "audit",
        "authority": "Controller General of Accounts & Public Accounts Committee, Parliament",
        "base_url": "https://apms.nic.in",
        "official_publisher": "Controller General of Accounts & Parliament of India",
        "source_landing_page": "https://apms.nic.in",
        "mirror_name": "",
        "retrieval_url": "https://apms.nic.in",
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
        "authority": "State Finance Departments (Assam OBDS & AP Treasury) via OpenBudgetsIndia / CivicDataLab",
        "base_url": "https://openbudgetsindia.org",
        "official_publisher": "State Finance Departments (Assam & Andhra Pradesh)",
        "source_landing_page": "https://openbudgetsindia.org",
        "mirror_name": "CivicDataLab / OpenBudgetsIndia",
        "retrieval_url": "https://openbudgetsindia.org",
        "ingestion_tier": 1,
        "connector_type": "http_xlsx",
        "data_contents": "Union & State budgets, Demands for Grants (OBDS), AP Monthly Treasury, BE/RE/Actuals",
        "format_access": "Pinned Open Budgets Data Standard XLSX & CSV mirrors",
        "robots_policy": "open-api",
    },
    {
        "id": "src-cga",
        "slug": "union-budget-cga",
        "name": "Union Budget & CGA Monthly Accounts",
        "pillar": "budget",
        "authority": "Ministry of Finance & Controller General of Accounts (CGA)",
        "base_url": "https://cga.nic.in",
        "official_publisher": "Ministry of Finance & Controller General of Accounts (CGA)",
        "source_landing_page": "https://cga.nic.in",
        "mirror_name": "",
        "retrieval_url": "https://cga.nic.in",
        "ingestion_tier": 1,
        "connector_type": "html_mis",
        "data_contents": "Union Government Monthly Accounts at a Glance, Revenue & CapEx actuals vs BE, Demands for Grants",
        "format_access": "Official cga.nic.in live monthly reports & indiabudget.gov.in Demand PDFs",
        "robots_policy": "strict-polite-1rps",
    },
    {
        "id": "src-ogd",
        "slug": "data-gov-in-ogd",
        "name": "Open Government Data (OGD) Platform India",
        "pillar": "budget",
        "authority": "NIC / Ministry of Electronics & Information Technology",
        "base_url": "https://data.gov.in",
        "official_publisher": "National Informatics Centre (NIC), MeitY",
        "source_landing_page": "https://data.gov.in",
        "mirror_name": "",
        "retrieval_url": "https://data.gov.in",
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
        "official_publisher": "Office of Controller General of Accounts, Ministry of Finance",
        "source_landing_page": "https://pfms.nic.in",
        "mirror_name": "",
        "retrieval_url": "https://pfms.nic.in",
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
        "official_publisher": "Ministry of Rural Development, Government of India",
        "source_landing_page": "https://nrega.nic.in",
        "mirror_name": "Community GitHub Mirror (Arpita-deb/NREGA-Data-Analysis)",
        "retrieval_url": _gh_raw("Arpita-deb/NREGA-Data-Analysis", "NREGA%20Data.csv"),
        "ingestion_tier": 2,
        "connector_type": "html_mis",
        "data_contents": "740-district wage vs material spend (statutory 60:40 ratio), work completion & social audit metrics",
        "format_access": "Public HTML MIS tables & pinned CSV mirrors (zero citizen PII)",
        "robots_policy": "strict-polite-robots",
    },
    {
        "id": "src-pmgsy",
        "slug": "pmgsy-ommas",
        "name": "PMGSY OMMAS (Rural Roads Management System)",
        "pillar": "scheme",
        "authority": "National Rural Infrastructure Development Agency (NRIDA), MoRD",
        "base_url": "https://ommas.nic.in",
        "official_publisher": "National Rural Infrastructure Development Agency (NRIDA), MoRD",
        "source_landing_page": "https://ommas.nic.in",
        "mirror_name": "Community GitHub Mirror (aditya-kr86/PMGSY_Multiclass_ML)",
        "retrieval_url": _gh_raw("aditya-kr86/PMGSY_Multiclass_ML", "data/PMGSY_DATASET.csv"),
        "ingestion_tier": 2,
        "connector_type": "html_mis",
        "data_contents": "All-India district & phase rural road sanctioned vs completed km and financial expenditure",
        "format_access": "Public OMMAS / data.gov.in pinned CSV exports",
        "robots_policy": "strict-polite-robots",
    },
    {
        "id": "src-mplads",
        "slug": "mplads-esakshi",
        "name": "MPLADS e-SAKSHI Public Dashboard",
        "pillar": "scheme",
        "authority": "Ministry of Statistics & Programme Implementation (MoSPI)",
        "base_url": "https://mplads.mospi.gov.in",
        "official_publisher": "Ministry of Statistics & Programme Implementation (MoSPI)",
        "source_landing_page": "https://mplads.mospi.gov.in",
        "mirror_name": "Community GitHub Mirror (Vonter/india-mplads-works)",
        "retrieval_url": _gh_raw("Vonter/india-mplads-works", "csv/MPLADS.csv"),
        "ingestion_tier": 2,
        "connector_type": "html_mis",
        "data_contents": "Constituency allocations, recommended/sanctioned/completed works status across India",
        "format_access": "Public dashboard reports & pinned CSV mirrors",
        "robots_policy": "strict-polite-robots",
    },
    {
        "id": "src-csr-mca",
        "slug": "national-csr-mca21",
        "name": "National CSR Portal & MCA21 Corporate Disclosures",
        "pillar": "corporate_political",
        "authority": "Ministry of Corporate Affairs (MCA)",
        "base_url": "https://www.csr.gov.in",
        "official_publisher": "Ministry of Corporate Affairs (MCA)",
        "source_landing_page": "https://www.csr.gov.in",
        "mirror_name": "Community GitHub Mirror (spfrantz/MCA-CSR-Scraper)",
        "retrieval_url": _gh_raw("spfrantz/MCA-CSR-Scraper", "examples/csr_top_100.csv"),
        "ingestion_tier": 2,
        "connector_type": "csv_upload",
        "data_contents": "Corporate CINs, Top-100 CSR projects & spend by sector/state under Companies Act Sec 135",
        "format_access": "csr.gov.in pinned CSV mirrors & user-supplied MCA21 extracts",
        "robots_policy": "tier2-3-compliant",
    },
    {
        "id": "src-eci-myneta",
        "slug": "eci-electoral-myneta",
        "name": "ECI Electoral Bonds Disclosures & MyNeta / ADR Affidavits",
        "pillar": "corporate_political",
        "authority": "Election Commission of India / Supreme Court WP(C) 880/2017 Disclosures",
        "base_url": "https://www.eci.gov.in",
        "official_publisher": "Election Commission of India (ECI) / State Bank of India SC Disclosures",
        "source_landing_page": "https://www.eci.gov.in",
        "mirror_name": "Community GitHub Mirror (saisantoshv3/electoral_bonds)",
        "retrieval_url": _gh_raw("saisantoshv3/electoral_bonds", "data/final.csv"),
        "ingestion_tier": 1,
        "connector_type": "csv_upload",
        "data_contents": "Supreme Court mandated SBI/ECI bond purchaser & political party redemption disclosures (2019–2024)",
        "format_access": "ECI public PDFs & pinned structured open CSV mirrors",
        "robots_policy": "strict-polite-1rps",
    },
    {
        "id": "src-sansad-rti",
        "slug": "sansad-egazette-rti",
        "name": "Digital Sansad, eGazette & RTI Online / CIC Orders",
        "pillar": "parliament",
        "authority": "Parliament of India, Dept. of Publication & Central Information Commission",
        "base_url": "https://sansad.in",
        "official_publisher": "Parliament of India & Central Information Commission",
        "source_landing_page": "https://sansad.in",
        "mirror_name": "",
        "retrieval_url": "https://sansad.in",
        "ingestion_tier": 1,
        "connector_type": "http_pdf",
        "data_contents": "Lok Sabha & Rajya Sabha Q&A, statutory Gazette notifications, CIC transparency orders",
        "format_access": "Public PDF/JSON/HTML (Human-only RTI filing via respective Central/State portals)",
        "robots_policy": "strict-polite-1rps",
    },
]

SEED_MISSIONS: list[dict[str, Any]] = [
    {
        "id": "mission-m1",
        "slug": "assam-ocds-single-bid",
        "title": "Assam State Public Procurement Single-Bidder Audit (FY 2016–22)",
        "description": (
            "Audits 39,502 Assam OCDS procurement releases (assamtenders.gov.in via CivicDataLab) "
            "for single-bidder concentration (>35% among tenders with positive bidder counts) and "
            "shortened bid windows (<14 days), reporting zero and blank bidder counts separately."
        ),
        "pillar": "procurement",
        "methodology_code": "M1",
        "jurisdiction": "State:Assam",
        "ministry_or_authority": "Finance Department, Government of Assam (assamtenders.gov.in)",
        "statutory_anchors": ["Assam-PPR-2020", "GFR-2017-R166", "CVC-Single-Tender-2007"],
        "source_ids": ["src-ocds-india", "src-cppp"],
        "data_mode": "live_public",
        "monetary_unit": "inr",
        "financial_quantity_type": "tender_estimates",
        "priority": 1,
    },
    {
        "id": "mission-m2",
        "slug": "assam-gfr-split-tendering",
        "title": "Assam Procurement Threshold-Band & Order-Date Audit (FY 2016–22)",
        "description": (
            "Examines 39,502 Assam OCDS releases (shared input file with M1) for near-threshold "
            "price bands and rolling 60-day award clusters where genuine award dates and governing "
            "jurisdiction thresholds are present, keeping tender estimates separate from awards."
        ),
        "pillar": "procurement",
        "methodology_code": "M2",
        "jurisdiction": "State:Assam",
        "ministry_or_authority": "Finance Department & State Procuring Entities, Government of Assam",
        "statutory_anchors": ["Assam-PPR-2020", "GFR-2017-R149", "GFR-2017-R154", "GFR-2017-R161"],
        "source_ids": ["src-ocds-india", "src-gem", "src-cppp"],
        "data_mode": "live_public",
        "monetary_unit": "inr",
        "financial_quantity_type": "tender_estimates",
        "priority": 2,
    },
    {
        "id": "mission-m3",
        "slug": "ocds-vendor-concentration",
        "title": "Assam & Himachal Pradesh OCDS Awardee Concentration (HHI) Audit",
        "description": (
            "Computes buyer-level Herfindahl–Hirschman Index (HHI > 0.35) and top-vendor share (>40%) "
            "across 43,669 Assam OCDS releases (FY 2016–23) and 3,991 Himachal Pradesh Health & I&PH OCDS "
            "tenders (FY 2017–21), disclosing exact triggered threshold, window, and excluded share."
        ),
        "pillar": "procurement",
        "methodology_code": "M3",
        "jurisdiction": "State:Assam & Himachal Pradesh",
        "ministry_or_authority": "Assam Finance Dept (assamtenders.gov.in) & HP Tenders (hptenders.gov.in)",
        "statutory_anchors": ["GFR-2017-R173", "CVC-Vendor-Concentration", "Competition-Act-Sec-3"],
        "source_ids": ["src-ocds-india", "src-cppp"],
        "data_mode": "live_public",
        "monetary_unit": "inr",
        "financial_quantity_type": "awarded_contract_values",
        "priority": 3,
    },
    {
        "id": "mission-m4",
        "slug": "ocds-cost-escalation-watch",
        "title": "Assam & HP Awarded Contract vs Tender Estimate Variance Audit",
        "description": (
            "Compares disclosed awarded contract values against published tender estimates across "
            "Assam OCDS releases (shared package with M3) to flag >20% award-over-estimate variances "
            "while noting that final contract cost and disbursements require separate records."
        ),
        "pillar": "procurement",
        "methodology_code": "M4",
        "jurisdiction": "State:Assam & Himachal Pradesh",
        "ministry_or_authority": "State Public Works & Health Procurement Authorities (Assam & HP)",
        "statutory_anchors": ["GFR-2017-R173", "GeM-Rate-Reasonability", "CVC-Post-Award-Variation"],
        "source_ids": ["src-ocds-india", "src-gem"],
        "data_mode": "live_public",
        "monetary_unit": "inr",
        "financial_quantity_type": "awarded_contract_values",
        "priority": 4,
    },
    {
        "id": "mission-m5",
        "slug": "fiscal-march-rush-budgets",
        "title": "AP Monthly Treasury March Rush, Assam OBDS & Union Budget Variance Audit",
        "description": (
            "Audits 24,269 Andhra Pradesh monthly treasury rows for Q4 (>33%) / March (>15%) "
            "pacing, Assam OBDS Demands for Grants (reconciled by account head, authority, and fiscal "
            "period), Union Flagship Scheme budgets (caveated against Demand No. 87), and live CGA accounts."
        ),
        "pillar": "budget",
        "methodology_code": "M5",
        "jurisdiction": "Union of India, Andhra Pradesh & Assam",
        "ministry_or_authority": "Ministry of Finance (CGA), AP Finance Dept & Assam Finance Dept",
        "statutory_anchors": ["GFR-2017-R62-3", "MoF-CMS-Circular", "PAC-100Cr-Explanatory-Note"],
        "source_ids": ["src-cga", "src-obi", "src-ogd"],
        "data_mode": "live_public",
        "monetary_unit": "inr",
        "financial_quantity_type": "budget_and_treasury_expenditure",
        "priority": 5,
    },
    {
        "id": "mission-m6",
        "slug": "cag-union-state-audit-paras",
        "title": "CAG of India 2025–2026 Union & State Audit Report Para Extraction",
        "description": (
            "Parses official full-text CAG of India PDF reports tabled in 2025–2026 (Union Reports 6/2026, "
            "5/2026, 39/2025 & MP Jal Jeevan Mission 5/2026) requiring sentence/table linkage between "
            "amounts and observations plus dated disposition evidence."
        ),
        "pillar": "audit",
        "methodology_code": "M6",
        "jurisdiction": "Union of India & Madhya Pradesh",
        "ministry_or_authority": "Comptroller and Auditor General of India (cag.gov.in) / PAC",
        "statutory_anchors": ["Constitution-Art-151", "CAG-DPC-Act-1971", "PAC-ATN-Timeline"],
        "source_ids": ["src-cag", "src-apms-pac", "src-sansad-rti"],
        "data_mode": "live_public",
        "monetary_unit": "inr",
        "financial_quantity_type": "cag_report_monetary_figures",
        "priority": 6,
    },
    {
        "id": "mission-m7",
        "slug": "rural-schemes-pmgsy-mgnrega-mplads",
        "title": "All-India PMGSY, MGNREGA 60:40 Ratio & MPLADS Works Verification",
        "description": (
            "Verifies physical-vs-financial disbursement ratios across 2,189 All-India PMGSY "
            "district-phase aggregates and statutory 60:40 wage-material ratios across 740 MGNREGA "
            "districts while preserving missing physical progress and explicit as-of dates."
        ),
        "pillar": "scheme",
        "methodology_code": "M7",
        "jurisdiction": "All-India (States & Districts)",
        "ministry_or_authority": "Ministry of Rural Development (NRIDA / MGNREGA) & MoSPI (MPLADS)",
        "statutory_anchors": ["PMGSY-III-Guidelines", "MGNREGA-Sch-II-60-40", "MPLADS-2023-Guidelines"],
        "source_ids": ["src-pmgsy", "src-mgnrega", "src-mplads", "src-pfms"],
        "data_mode": "live_public",
        "monetary_unit": "inr",
        "financial_quantity_type": "scheme_outlay_and_expenditure",
        "priority": 7,
    },
    {
        "id": "mission-m8",
        "slug": "electoral-bonds-mca-csr-disclosure",
        "title": "SC-Ordered SBI/ECI Electoral Bonds × MCA Top-100 CSR Public Disclosure Ledger",
        "description": (
            "Cross-references 20,551 Supreme Court mandated SBI/ECI Electoral Bond disclosure records "
            "(₹12,769.09 Cr disclosed face value, 2019–2024) against the MCA National CSR Top-100 "
            "corporate registry by normalized company name (1,265 bond rows linked to 19 CSR CINs; "
            "no procurement relationship established; strictly neutral public-disclosure summary)."
        ),
        "pillar": "corporate_political",
        "methodology_code": "M8",
        "jurisdiction": "Union of India",
        "ministry_or_authority": "Election Commission of India (ECI) & Ministry of Corporate Affairs (MCA)",
        "statutory_anchors": ["SC-WP-880-2017-Electoral-Bonds", "Companies-Act-Sec-135-CSR", "RTI-Sec-4-1-b"],
        "source_ids": ["src-eci-myneta", "src-csr-mca"],
        "data_mode": "live_public",
        "monetary_unit": "inr",
        "financial_quantity_type": "electoral_bond_disclosures",
        "priority": 8,
    },
]

# All 16 verified Government of India & pinned civic mirror audit tasks (ZERO mutable branch URLs)
REAL_AUDIT_TASKS: list[dict[str, Any]] = [
    {
        "id": "tsk-real-01",
        "mission_id": "mission-m1",
        "mission_slug": "assam-ocds-single-bid",
        "methodology_code": "M1",
        "dataset_key": "assam-ocds-v2-fy2016-22",
        "financial_quantity_type": "tender_estimates",
        "title": "Assam Health & PWD Single-Bidder Audit (FY 2016–22, 39,502 tenders)",
        "instructions": (
            "Fetch the pinned Assam OCDS v2 flat CSV (39,502 tenders), verify SHA-256 checksum, "
            "run deterministic M1 single-bidder (>35% among positive bidder counts) and short-window "
            "checks while separating zero vs blank bidder counts, and submit neutral findings or cleared metrics."
        ),
        "payload": {
            "source_id": "src-ocds-india",
            "dataset_key": "assam-ocds-v2-fy2016-22",
            "financial_quantity_type": "tender_estimates",
            "official_publisher": "Finance Department, Government of Assam (assamtenders.gov.in)",
            "source_landing_page": "https://assamtenders.gov.in",
            "mirror_name": "CivicDataLab Open Contracting India (GitHub pinned commit)",
            "mirror_catalog_url": "https://assam.open-contracting.in/datasets",
            "retrieval_url": _gh_raw(
                "CivicDataLab/assam-tenders-data",
                "data/ProcessedData/ocds-mapped-data/v2/ocds_mapped_data_fiscal_year_2016_2022_v2.csv",
            ),
            "dataset_url": _gh_raw(
                "CivicDataLab/assam-tenders-data",
                "data/ProcessedData/ocds-mapped-data/v2/ocds_mapped_data_fiscal_year_2016_2022_v2.csv",
            ),
            "source_url": "https://assamtenders.gov.in",
            "expected_sha256": "01a3ab8b7dcd3023e10f2e14cf7685d16825932feb88a363d69bc1177af4a77e",
            "methodology_code": "M1",
            "jurisdiction": "State:Assam",
            "fiscal_year": "FY2016-22",
            "description": "39,502 Assam state OCDS tender estimates (assamtenders.gov.in via pinned CivicDataLab mirror); M1 single-bidder & short-window audit.",
            "artifact_title": "Assam OCDS Tenders Flat CSV v2 (FY 2016–2022, pinned commit)",
            "is_demo_sample": False,
        },
    },
    {
        "id": "tsk-real-02",
        "mission_id": "mission-m2",
        "mission_slug": "assam-gfr-split-tendering",
        "methodology_code": "M2",
        "dataset_key": "assam-ocds-v2-fy2016-22",
        "financial_quantity_type": "tender_estimates",
        "title": "Assam Procurement Threshold-Band & Award-Date Audit (FY 2016–22)",
        "instructions": (
            "Fetch the pinned Assam OCDS v2 flat CSV (39,502 tender estimates; shared input file with M1), "
            "verify SHA-256 checksum, keep release dates separate from award dates and estimates separate from "
            "award values, and emit splitting claims ONLY where genuine award dates and governing rules exist."
        ),
        "payload": {
            "source_id": "src-ocds-india",
            "dataset_key": "assam-ocds-v2-fy2016-22",
            "financial_quantity_type": "tender_estimates",
            "official_publisher": "Finance Department, Government of Assam (assamtenders.gov.in)",
            "source_landing_page": "https://assamtenders.gov.in",
            "mirror_name": "CivicDataLab Open Contracting India (GitHub pinned commit)",
            "mirror_catalog_url": "https://assam.open-contracting.in/datasets",
            "retrieval_url": _gh_raw(
                "CivicDataLab/assam-tenders-data",
                "data/ProcessedData/ocds-mapped-data/v2/ocds_mapped_data_fiscal_year_2016_2022_v2.csv",
            ),
            "dataset_url": _gh_raw(
                "CivicDataLab/assam-tenders-data",
                "data/ProcessedData/ocds-mapped-data/v2/ocds_mapped_data_fiscal_year_2016_2022_v2.csv",
            ),
            "source_url": "https://assamtenders.gov.in",
            "expected_sha256": "01a3ab8b7dcd3023e10f2e14cf7685d16825932feb88a363d69bc1177af4a77e",
            "methodology_code": "M2",
            "jurisdiction": "State:Assam",
            "fiscal_year": "FY2016-22",
            "description": "39,502 Assam state OCDS tender estimates (shared with M1); M2 threshold-band & award-date cluster audit.",
            "artifact_title": "Assam OCDS Tenders Flat CSV v2 (FY 2016–2022, pinned commit)",
            "is_demo_sample": False,
        },
    },
    {
        "id": "tsk-real-03",
        "mission_id": "mission-m3",
        "mission_slug": "ocds-vendor-concentration",
        "methodology_code": "M3",
        "dataset_key": "assam-ocds-full-fy2016-23",
        "financial_quantity_type": "awarded_contract_values",
        "title": "Assam Full OCDS Awardee Concentration (HHI) Audit (43,669 releases)",
        "instructions": (
            "Fetch the pinned full Assam OCDS JSON ZIP package (43,669 releases, 5,258 awards), verify "
            "SHA-256 checksum, run deterministic M3 buyer-level HHI (>0.35) & top-supplier (>40%) "
            "analysis disclosing exact triggered threshold and excluded share."
        ),
        "payload": {
            "source_id": "src-ocds-india",
            "dataset_key": "assam-ocds-full-fy2016-23",
            "financial_quantity_type": "awarded_contract_values",
            "official_publisher": "Finance Department, Government of Assam (assamtenders.gov.in)",
            "source_landing_page": "https://assamtenders.gov.in",
            "mirror_name": "CivicDataLab Open Contracting India (GitHub pinned commit)",
            "mirror_catalog_url": "https://assam.open-contracting.in/datasets",
            "retrieval_url": _gh_raw(
                "CivicDataLab/assam-tenders-data",
                "data/ProcessedData/ocds-mapped-data/current/ocds_mapped_data.json.zip",
            ),
            "dataset_url": _gh_raw(
                "CivicDataLab/assam-tenders-data",
                "data/ProcessedData/ocds-mapped-data/current/ocds_mapped_data.json.zip",
            ),
            "source_url": "https://assamtenders.gov.in",
            "expected_sha256": "c1f8de1058b0203d521d13423fe346d4011bfc824a27ac0f623c76c7de3a7019",
            "methodology_code": "M3",
            "jurisdiction": "State:Assam",
            "fiscal_year": "FY2016-23",
            "description": "43,669 Assam OCDS releases (5,258 awards); M3 buyer awardee HHI & top-vendor concentration audit.",
            "artifact_title": "Assam OCDS Full Procurement Package (FY 2016–2023, pinned commit)",
            "is_demo_sample": False,
        },
    },
    {
        "id": "tsk-real-04",
        "mission_id": "mission-m3",
        "mission_slug": "ocds-vendor-concentration",
        "methodology_code": "M3",
        "dataset_key": "hp-health-ocds-fy2017-21",
        "financial_quantity_type": "awarded_contract_values",
        "title": "Himachal Pradesh Health & I&PH OCDS Audit (FY 2017–21, 3,991 tenders)",
        "instructions": (
            "Fetch the pinned Himachal Pradesh Health & PWD OCDS Excel workbook (3,991 tenders × 4,213 awards), "
            "verify SHA-256 checksum, join tenders and awards on ocid, and run deterministic M3 awardee "
            "concentration checks."
        ),
        "payload": {
            "source_id": "src-ocds-india",
            "dataset_key": "hp-health-ocds-fy2017-21",
            "financial_quantity_type": "awarded_contract_values",
            "official_publisher": "Government of Himachal Pradesh (hptenders.gov.in)",
            "source_landing_page": "https://hptenders.gov.in",
            "mirror_name": "CivicDataLab Himachal Pradesh OCDS (GitHub pinned commit)",
            "mirror_catalog_url": "https://github.com/CivicDataLab/himachal-pradesh-health-procurement-OCDS",
            "retrieval_url": _gh_raw(
                "CivicDataLab/himachal-pradesh-health-procurement-OCDS",
                "HP%20Health%20Procurements(FY%202017-18%20to%202020-21%20July).xlsx",
            ),
            "dataset_url": _gh_raw(
                "CivicDataLab/himachal-pradesh-health-procurement-OCDS",
                "HP%20Health%20Procurements(FY%202017-18%20to%202020-21%20July).xlsx",
            ),
            "source_url": "https://hptenders.gov.in",
            "expected_sha256": "0744e24693c73eb84ff9071a8cef6ba5120b77c19cbc442a2c89b56d8460edcf",
            "methodology_code": "M3",
            "jurisdiction": "State:Himachal Pradesh",
            "fiscal_year": "FY2017-21",
            "description": "Himachal Pradesh Health & I&PH OCDS workbook (hptenders.gov.in via pinned CivicDataLab mirror); 3,991 tenders × 4,213 awards.",
            "artifact_title": "Himachal Pradesh Health & PWD Procurement OCDS (FY 2017–2021, pinned commit)",
            "is_demo_sample": False,
        },
    },
    {
        "id": "tsk-real-05",
        "mission_id": "mission-m4",
        "mission_slug": "ocds-cost-escalation-watch",
        "methodology_code": "M4",
        "dataset_key": "assam-ocds-full-fy2016-23",
        "financial_quantity_type": "awarded_contract_values",
        "title": "Assam Awarded Contract vs Tender Estimate Variance Audit (FY 2016–23)",
        "instructions": (
            "Fetch the pinned full Assam OCDS JSON ZIP package (43,669 releases; shared input with M3), "
            "verify SHA-256 checksum, run deterministic M4 award-vs-estimate (>20%) variance checks "
            "labelling both values and dates distinctly from final payments."
        ),
        "payload": {
            "source_id": "src-ocds-india",
            "dataset_key": "assam-ocds-full-fy2016-23",
            "financial_quantity_type": "awarded_contract_values",
            "official_publisher": "Finance Department, Government of Assam (assamtenders.gov.in)",
            "source_landing_page": "https://assamtenders.gov.in",
            "mirror_name": "CivicDataLab Open Contracting India (GitHub pinned commit)",
            "mirror_catalog_url": "https://assam.open-contracting.in/datasets",
            "retrieval_url": _gh_raw(
                "CivicDataLab/assam-tenders-data",
                "data/ProcessedData/ocds-mapped-data/current/ocds_mapped_data.json.zip",
            ),
            "dataset_url": _gh_raw(
                "CivicDataLab/assam-tenders-data",
                "data/ProcessedData/ocds-mapped-data/current/ocds_mapped_data.json.zip",
            ),
            "source_url": "https://assamtenders.gov.in",
            "expected_sha256": "c1f8de1058b0203d521d13423fe346d4011bfc824a27ac0f623c76c7de3a7019",
            "methodology_code": "M4",
            "jurisdiction": "State:Assam",
            "fiscal_year": "FY2016-23",
            "description": "43,669 Assam OCDS releases (shared with M3); M4 awarded contract value vs published tender estimate variance (>20%).",
            "artifact_title": "Assam OCDS Full Procurement Package (FY 2016–2023, pinned commit)",
            "is_demo_sample": False,
        },
    },
    {
        "id": "tsk-real-06",
        "mission_id": "mission-m5",
        "mission_slug": "fiscal-march-rush-budgets",
        "methodology_code": "M5",
        "dataset_key": "ap-monthly-treasury-fy2014-18",
        "financial_quantity_type": "budget_and_treasury_expenditure",
        "title": "Andhra Pradesh Monthly Treasury March Rush Audit (FY 2014–18, 24,269 rows)",
        "instructions": (
            "Fetch the pinned Andhra Pradesh Monthly State Treasury CSV (24,269 monthly rows, ₹5,148.33 Cr), "
            "verify SHA-256 checksum, roll up by FY × District × Major Head, and run M5 Q4 (>33%) / March "
            "(>15%) pacing checks while preserving unobserved estimates/PAC status as unknown."
        ),
        "payload": {
            "source_id": "src-obi",
            "dataset_key": "ap-monthly-treasury-fy2014-18",
            "financial_quantity_type": "budget_and_treasury_expenditure",
            "official_publisher": "Finance Department, Government of Andhra Pradesh (Treasury)",
            "source_landing_page": "https://treasury.ap.gov.in",
            "mirror_name": "CivicDataLab Child Budget Tracker (GitHub pinned commit)",
            "mirror_catalog_url": "https://github.com/CivicDataLab/child-budget-tracker",
            "retrieval_url": _gh_raw(
                "CivicDataLab/child-budget-tracker",
                "data/processed_data/andhra_pradesh_child_treasury.csv",
            ),
            "dataset_url": _gh_raw(
                "CivicDataLab/child-budget-tracker",
                "data/processed_data/andhra_pradesh_child_treasury.csv",
            ),
            "source_url": "https://treasury.ap.gov.in",
            "expected_sha256": "172e14cb28135ce69df5aba7d6cf6816993790732db14718ec7a4212ff378c4e",
            "methodology_code": "M5",
            "jurisdiction": "State:Andhra Pradesh",
            "fiscal_year": "FY2014-18",
            "description": "24,269 monthly AP Finance Dept treasury expenditure rows (₹5,148.33 Cr); M5 Q4/March Rush pacing audit.",
            "artifact_title": "Andhra Pradesh Monthly State Treasury Expenditure (FY 2014–2018, pinned commit)",
            "is_demo_sample": False,
        },
    },
    {
        "id": "tsk-real-07",
        "mission_id": "mission-m5",
        "mission_slug": "fiscal-march-rush-budgets",
        "methodology_code": "M5",
        "dataset_key": "assam-obds-expenditure-fy2022-23",
        "financial_quantity_type": "budget_and_treasury_expenditure",
        "title": "Assam State Budget OBDS Demand Variance Audit (FY 2022–23, 84 Demands)",
        "instructions": (
            "Fetch the pinned Assam State Budget OBDS Expenditure Excel workbook (17,744 rows across 84 Demands), "
            "verify SHA-256 checksum, and run deterministic M5 variance checks ONLY where account head, "
            "authority, unit, and fiscal period match cleanly."
        ),
        "payload": {
            "source_id": "src-obi",
            "dataset_key": "assam-obds-expenditure-fy2022-23",
            "financial_quantity_type": "budget_and_treasury_expenditure",
            "official_publisher": "Finance Department, Government of Assam",
            "source_landing_page": "https://finance.assam.gov.in",
            "mirror_name": "CivicDataLab Assam Budget OBDS (GitHub pinned commit)",
            "mirror_catalog_url": "https://github.com/CivicDataLab/Assam-Budget-OBDS",
            "retrieval_url": _gh_raw(
                "CivicDataLab/Assam-Budget-OBDS",
                "Budget%20OBDS/assam_expenditure_22-23.xlsx",
            ),
            "dataset_url": _gh_raw(
                "CivicDataLab/Assam-Budget-OBDS",
                "Budget%20OBDS/assam_expenditure_22-23.xlsx",
            ),
            "source_url": "https://finance.assam.gov.in",
            "expected_sha256": "18f03d25254f2a6660e2937e9cfe8535ff67128e7217367cd30d680882e82126",
            "methodology_code": "M5",
            "jurisdiction": "State:Assam",
            "fiscal_year": "FY2022-23",
            "description": "17,744 Assam Finance Dept OBDS expenditure rows across 84 Demands; M5 matched-head/period variance audit.",
            "artifact_title": "Assam State Budget OBDS Expenditure (FY 2022–23, pinned commit)",
            "is_demo_sample": False,
        },
    },
    {
        "id": "tsk-real-08",
        "mission_id": "mission-m5",
        "mission_slug": "fiscal-march-rush-budgets",
        "methodology_code": "M5",
        "dataset_key": "union-flagship-schemes-fy2020-24-caveated",
        "financial_quantity_type": "budget_and_treasury_expenditure",
        "title": "Union Flagship Schemes Budget Check (FY 2020–24; MGNREGA FY20-21 mirror row caveated)",
        "instructions": (
            "Fetch the pinned Union Budget Flagship Schemes CSV mirror, verify SHA-256 checksum, "
            "AND exclude/quarantine the known mislabeled MGNREGA FY 2020–21 row (mirror lists ₹98,000 Cr "
            "which is FY 2021–22 RE; official Demand No. 87 printed p. 300 reports ₹1,11,169.53 Cr actual "
            "for FY 2020–21). Reconcile any lead against official indiabudget.gov.in Demand PDFs."
        ),
        "payload": {
            "source_id": "src-cga",
            "dataset_key": "union-flagship-schemes-fy2020-24-caveated",
            "financial_quantity_type": "budget_and_treasury_expenditure",
            "official_publisher": "Ministry of Finance, Government of India (Union Budget)",
            "source_landing_page": "https://www.indiabudget.gov.in/budget2022-23/doc/eb/sbe87.pdf#page=2",
            "mirror_name": "Community GitHub Mirror (Varnasr/PolicyStack, pinned commit — known row defect)",
            "mirror_catalog_url": "https://github.com/Varnasr/PolicyStack",
            "retrieval_url": _gh_raw("Varnasr/PolicyStack", "data/budgets/scheme_budgets_2020_2024.csv"),
            "dataset_url": _gh_raw("Varnasr/PolicyStack", "data/budgets/scheme_budgets_2020_2024.csv"),
            "source_url": "https://www.indiabudget.gov.in/budget2022-23/doc/eb/sbe87.pdf#page=2",
            "expected_sha256": "646a1059f49d88e35f892a75d95697b1dac18274d29e6be469360fc801d63702",
            "methodology_code": "M5",
            "jurisdiction": "Union of India",
            "fiscal_year": "FY2020-24",
            "input_quality_status": "caveat_mislabeled_mirror_row",
            "known_data_defects": [
                {
                    "scheme": "MGNREGA",
                    "fiscal_year": "FY2020-21",
                    "mirror_reported_inr_cr": 98000.0,
                    "official_actual_inr_cr": 111169.53,
                    "official_next_year_re_inr_cr": 98000.0,
                    "official_citation": "Union Budget 2022-23 Demand No. 87 (Department of Rural Development), printed p. 300",
                    "official_reference_url": "https://www.indiabudget.gov.in/budget2022-23/doc/eb/sbe87.pdf#page=2",
                    "disposition": "quarantine_mirror_row",
                }
            ],
            "quarantined_rows": [{"scheme": "MGNREGA", "fiscal_year": "FY2020-21"}],
            "description": (
                "Union Flagship Schemes CSV mirror (pinned PolicyStack commit) with explicit caveat: "
                "MGNREGA FY 2020–21 mirror row (₹98,000 Cr) is quarantined because official Demand No. 87 "
                "reports ₹1,11,169.53 Cr actual for FY 2020–21 and ₹98,000 Cr as FY 2021–22 RE."
            ),
            "artifact_title": "Union Flagship Schemes Mirror CSV (FY 2020–24, Demand 87 Caveated)",
            "is_demo_sample": False,
        },
    },
    {
        "id": "tsk-real-09",
        "mission_id": "mission-m5",
        "mission_slug": "fiscal-march-rush-budgets",
        "methodology_code": "M5",
        "dataset_key": "cga-monthly-accounts-fy2024-25-dec",
        "financial_quantity_type": "budget_and_treasury_expenditure",
        "title": "CGA Union Government Monthly Accounts Pacing Check (FY 2024–25 up to Dec 2024)",
        "instructions": (
            "Fetch the official CGA Union Government Monthly Accounts page on cga.nic.in (FY 2024–25 "
            "up to Dec 2024), parse live Revenue & CapEx actuals vs BE tables, and run M5 fiscal pacing verification."
        ),
        "payload": {
            "source_id": "src-cga",
            "dataset_key": "cga-monthly-accounts-fy2024-25-dec",
            "financial_quantity_type": "budget_and_treasury_expenditure",
            "official_publisher": "Controller General of Accounts (CGA), Ministry of Finance",
            "source_landing_page": "https://cga.nic.in/MonthlyReport/Published/12/2024-2025.aspx",
            "mirror_name": "",
            "retrieval_url": "https://cga.nic.in/MonthlyReport/Published/12/2024-2025.aspx",
            "dataset_url": "https://cga.nic.in/MonthlyReport/Published/12/2024-2025.aspx",
            "source_url": "https://cga.nic.in/MonthlyReport/Published/12/2024-2025.aspx",
            "expected_sha256": "f9b7de509a921b8542f16adae9c5deba5d337dafcf0780670cf818b2506b4f68",
            "methodology_code": "M5",
            "jurisdiction": "Union of India",
            "fiscal_year": "FY2024-25",
            "description": "Official cga.nic.in Union Government Monthly Accounts at a Glance (up to Dec 2024).",
            "artifact_title": "CGA Union Government Monthly Accounts (FY 2024–25 Dec)",
            "is_demo_sample": False,
        },
    },
    {
        "id": "tsk-real-10",
        "mission_id": "mission-m6",
        "mission_slug": "cag-union-state-audit-paras",
        "methodology_code": "M6",
        "dataset_key": "cag-union-report-6-of-2026",
        "financial_quantity_type": "cag_report_monetary_figures",
        "title": "CAG Union Report No. 6 of 2026 — Accounts of the Union Government 2024–25 (MoF)",
        "instructions": (
            "Fetch the official CAG Union Report No. 6 of 2026 PDF (156 pages), verify SHA-256 checksum, "
            "extract audit observations ONLY where a sentence or table cell directly links an amount to "
            "the observation and dated evidence supports any disposition age."
        ),
        "payload": {
            "source_id": "src-cag",
            "dataset_key": "cag-union-report-6-of-2026",
            "financial_quantity_type": "cag_report_monetary_figures",
            "official_publisher": "Comptroller and Auditor General of India (cag.gov.in)",
            "source_landing_page": "https://cag.gov.in/en/audit-report/details/125152",
            "mirror_name": "",
            "retrieval_url": "https://d1jmc0u14z2tmp.cloudfront.net/uploads/download_audit_report/2026/6of2026UGEng-069ce496f1158e0.22682438.pdf",
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
        "dataset_key": "cag-union-report-5-of-2026",
        "financial_quantity_type": "cag_report_monetary_figures",
        "title": "CAG Union Report No. 5 of 2026 — Compliance Audit on Finance & Communication",
        "instructions": (
            "Fetch the official CAG Union Report No. 5 of 2026 PDF (172 pages, year ended 31 Mar 2024), "
            "verify SHA-256 checksum, extract sentence-linked compliance audit paragraphs, and submit neutral findings."
        ),
        "payload": {
            "source_id": "src-cag",
            "dataset_key": "cag-union-report-5-of-2026",
            "financial_quantity_type": "cag_report_monetary_figures",
            "official_publisher": "Comptroller and Auditor General of India (cag.gov.in)",
            "source_landing_page": "https://cag.gov.in/en/audit-report/details/125146",
            "mirror_name": "",
            "retrieval_url": "https://d1jmc0u14z2tmp.cloudfront.net/uploads/download_audit_report/2026/5of2026UGEnglish-069ce38b4a151b0.15050638.pdf",
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
        "dataset_key": "cag-union-report-39-of-2025",
        "financial_quantity_type": "cag_report_monetary_figures",
        "title": "CAG Union Report No. 39 of 2025 — Commercial CPSE Compliance Observations",
        "instructions": (
            "Fetch the official CAG Union Report No. 39 of 2025 PDF (228 pages, 280 numbered sections), "
            "verify SHA-256 checksum, extract sentence-linked CPSE compliance audit paragraphs via M6, "
            "and submit neutral findings with benign explanations."
        ),
        "payload": {
            "source_id": "src-cag",
            "dataset_key": "cag-union-report-39-of-2025",
            "financial_quantity_type": "cag_report_monetary_figures",
            "official_publisher": "Comptroller and Auditor General of India (cag.gov.in)",
            "source_landing_page": "https://cag.gov.in/en/audit-report/details/125046",
            "mirror_name": "",
            "retrieval_url": "https://d1jmc0u14z2tmp.cloudfront.net/uploads/download_audit_report/2025/Report-of-CAG-March-2024-English-for-Approval_02-Feb-069ca6947a16ae2.43363906.pdf",
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
        "dataset_key": "cag-mp-report-5-of-2026-jjm",
        "financial_quantity_type": "cag_report_monetary_figures",
        "title": "CAG MP Report No. 5 of 2026 (Jal Jeevan Mission) Performance Audit",
        "instructions": (
            "Fetch the official CAG Madhya Pradesh Report No. 5 of 2026 on Jal Jeevan Mission (152 pages, "
            "143 numbered sections), verify SHA-256 checksum, extract sentence-linked performance audit "
            "paragraphs with M6, and submit neutral findings."
        ),
        "payload": {
            "source_id": "src-cag",
            "dataset_key": "cag-mp-report-5-of-2026-jjm",
            "financial_quantity_type": "cag_report_monetary_figures",
            "official_publisher": "Comptroller and Auditor General of India (cag.gov.in)",
            "source_landing_page": "https://cag.gov.in/en/audit-report",
            "mirror_name": "",
            "retrieval_url": "https://d1jmc0u14z2tmp.cloudfront.net/uploads/download_audit_report/2026/MP-JJM-Report-No.-5-of-2026-Bhopal---English-06a674f93c3faf1.25493877.pdf",
            "dataset_url": "https://d1jmc0u14z2tmp.cloudfront.net/uploads/download_audit_report/2026/MP-JJM-Report-No.-5-of-2026-Bhopal---English-06a674f93c3faf1.25493877.pdf",
            "source_url": "https://cag.gov.in/en/audit-report",
            "expected_sha256": "8ee505d3cd466f4abf354f578c1a21b810772c159825977b38489d8b887e0566",
            "companion_url": "https://d1jmc0u14z2tmp.cloudfront.net/uploads/download_audit_report/2026/CAG---NLC-India-Limited-2025-English-FINAL-SIGNATORY-10-12-2025-0698c6a3b5d7e42.69686670.pdf",
            "companion_sha256": "0ebeda2a13ccf692064942423886177aafe8d189c2cebf3b7a8cf9c74adbe631",
            "methodology_code": "M6",
            "jurisdiction": "State:Madhya Pradesh",
            "fiscal_year": "FY2024-25",
            "description": "CAG MP State Report No. 5 of 2026 — Performance Audit of Jal Jeevan Mission (152 pp, 143 sections).",
            "artifact_title": "CAG MP Report No. 5 of 2026 — Performance Audit of Jal Jeevan Mission",
            "is_demo_sample": False,
        },
    },
    {
        "id": "tsk-real-14",
        "mission_id": "mission-m7",
        "mission_slug": "rural-schemes-pmgsy-mgnrega-mplads",
        "methodology_code": "M7",
        "dataset_key": "pmgsy-ommas-district-phase-fy2023-24",
        "financial_quantity_type": "scheme_outlay_and_expenditure",
        "title": "All-India PMGSY Physical vs Financial Gap Verification (2,189 district-phases)",
        "instructions": (
            "Fetch the pinned All-India PMGSY OMMAS / data.gov.in district-phase CSV (2,189 rows), "
            "verify SHA-256 checksum, preserve missing physical progress without defaulting to zero, "
            "and run M7 checks flagging district-phases with >=90% expenditure and <70% completed road km."
        ),
        "payload": {
            "source_id": "src-pmgsy",
            "dataset_key": "pmgsy-ommas-district-phase-fy2023-24",
            "financial_quantity_type": "scheme_outlay_and_expenditure",
            "official_publisher": "National Rural Infrastructure Development Agency (NRIDA), MoRD",
            "source_landing_page": "https://ommas.nic.in",
            "mirror_name": "Community GitHub Mirror (aditya-kr86/PMGSY_Multiclass_ML, pinned commit)",
            "mirror_catalog_url": "https://github.com/aditya-kr86/PMGSY_Multiclass_ML",
            "retrieval_url": _gh_raw("aditya-kr86/PMGSY_Multiclass_ML", "data/PMGSY_DATASET.csv"),
            "dataset_url": _gh_raw("aditya-kr86/PMGSY_Multiclass_ML", "data/PMGSY_DATASET.csv"),
            "source_url": "https://ommas.nic.in",
            "expected_sha256": "07f4df8cc2532814af60b3d3686169a589e414b206e37d886be97691c1253635",
            "methodology_code": "M7",
            "jurisdiction": "All-India (States & Districts)",
            "fiscal_year": "FY2023-24",
            "as_of_date": "2024-03-31",
            "description": "2,189 All-India PMGSY district × scheme aggregate rows (ommas.nic.in via pinned mirror); M7 financial vs physical km gap audit.",
            "artifact_title": "PMGSY District & Phase Physical-Financial Progress (OMMAS, pinned commit)",
            "is_demo_sample": False,
        },
    },
    {
        "id": "tsk-real-15",
        "mission_id": "mission-m7",
        "mission_slug": "rural-schemes-pmgsy-mgnrega-mplads",
        "methodology_code": "M7",
        "dataset_key": "mgnrega-mis-740-districts-fy2023-24",
        "financial_quantity_type": "scheme_outlay_and_expenditure",
        "title": "All-India MGNREGA 60:40 Wage-Material Ratio Audit (740 Districts)",
        "instructions": (
            "Fetch the pinned semicolon-delimited All-India MGNREGA MIS CSV (740 districts, ₹53,567.1 Cr), "
            "verify SHA-256 checksum, and run M7 statutory 60:40 wage-to-material ratio checks "
            "(>40% material share)."
        ),
        "payload": {
            "source_id": "src-mgnrega",
            "dataset_key": "mgnrega-mis-740-districts-fy2023-24",
            "financial_quantity_type": "scheme_outlay_and_expenditure",
            "official_publisher": "Ministry of Rural Development (nrega.nic.in)",
            "source_landing_page": "https://nrega.nic.in",
            "mirror_name": "Community GitHub Mirror (Arpita-deb/NREGA-Data-Analysis, pinned commit)",
            "mirror_catalog_url": "https://github.com/Arpita-deb/NREGA-Data-Analysis",
            "retrieval_url": _gh_raw("Arpita-deb/NREGA-Data-Analysis", "NREGA%20Data.csv"),
            "dataset_url": _gh_raw("Arpita-deb/NREGA-Data-Analysis", "NREGA%20Data.csv"),
            "source_url": "https://nrega.nic.in",
            "expected_sha256": "72c8ec81e12db3cfd1944668dfabe7c16e723c0caf23525009eebfd50683c2bc",
            "companion_url": _gh_raw("Vonter/india-mplads-works", "csv/MPLADS.csv"),
            "companion_sha256": "aa1d0b7c9c6bbe014a1af772a22ab3dc0a6eb24d043695060abaa0f94333e413",
            "methodology_code": "M7",
            "jurisdiction": "All-India (740 Districts)",
            "fiscal_year": "FY2023-24",
            "as_of_date": "2024-03-31",
            "description": "740-district MGNREGA MIS expenditure dataset (₹53,567.1 Cr); statutory 60:40 material ratio check.",
            "artifact_title": "MGNREGA District-Wise Expenditure, Wages & Material MIS (pinned commit)",
            "is_demo_sample": False,
        },
    },
    {
        "id": "tsk-real-16",
        "mission_id": "mission-m8",
        "mission_slug": "electoral-bonds-mca-csr-disclosure",
        "methodology_code": "M8",
        "dataset_key": "eci-electoral-bonds-x-mca-csr-fy2019-24",
        "financial_quantity_type": "electoral_bond_disclosures",
        "title": "SC-Ordered SBI/ECI Electoral Bonds × MCA Top-100 CSR Cross-Disclosure Audit",
        "instructions": (
            "Fetch the pinned SBI/ECI Electoral Bonds CSV (20,551 bonds, ₹12,769.09 Cr disclosed face value) "
            "and pinned secondary MCA National CSR Top-100 CSV, verify BOTH SHA-256 checksums, and run M8 "
            "reporting the normalized-name join basis (1,265 bond rows linked to 19 CSR CINs; no procurement "
            "relationship established)."
        ),
        "payload": {
            "source_id": "src-eci-myneta",
            "secondary_source_id": "src-csr-mca",
            "dataset_key": "eci-electoral-bonds-x-mca-csr-fy2019-24",
            "financial_quantity_type": "electoral_bond_disclosures",
            "official_publisher": "Election Commission of India (ECI) & Ministry of Corporate Affairs (MCA)",
            "source_landing_page": "https://www.eci.gov.in",
            "mirror_name": "Community GitHub Mirrors (saisantoshv3/electoral_bonds & spfrantz/MCA-CSR-Scraper, pinned commits)",
            "mirror_catalog_url": "https://github.com/saisantoshv3/electoral_bonds",
            "retrieval_url": _gh_raw("saisantoshv3/electoral_bonds", "data/final.csv"),
            "dataset_url": _gh_raw("saisantoshv3/electoral_bonds", "data/final.csv"),
            "secondary_url": _gh_raw("spfrantz/MCA-CSR-Scraper", "examples/csr_top_100.csv"),
            "secondary_sha256": "c186fed19af3574e83cbb826242757e45e5e634b5c21f080b4c51b30e9fd9f40",
            "csr_projects_url": _gh_raw("spfrantz/MCA-CSR-Scraper", "examples/csr_top_100_results.csv"),
            "source_url": "https://www.eci.gov.in",
            "expected_sha256": "192489338f5cfb70df17566776db936ccd7ce3e732fb7dc7cb6d87faca8d62e6",
            "methodology_code": "M8",
            "jurisdiction": "Union of India",
            "fiscal_year": "FY2019-24",
            "description": (
                "20,551 matched SC-ordered SBI/ECI Electoral Bonds (₹12,769.09 Cr disclosed bond face value) "
                "× MCA Top-100 CSR registry joined by normalized company name (1,265 bond rows linked to 19 CSR CINs)."
            ),
            "artifact_title": "SC-Ordered SBI/ECI Electoral Bonds × MCA CSR Top-100 Registry (pinned commits)",
            "is_demo_sample": False,
        },
    },
]


def seed_database(db: Database, repo_root: Path | None = None) -> bool:
    """Populate or upgrade real Indian public sources, 8 live missions, and 16 queued tasks idempotently.

    Uses ``seed_state`` versioning so upgrades to pinned commit URLs, provenance fields,
    and mission/task metadata apply safely to populated databases while preserving all
    historical findings, peer verifications, completed tasks, and mission counters.

    Returns True if seeding or a seed-version upgrade ran, False if already current.
    """
    _ = repo_root  # Retained in signature for caller compatibility

    with db.read() as conn:
        state_row = conn.execute(
            "SELECT version FROM seed_state WHERE key = 'canonical_seed'"
        ).fetchone()
        src_cnt = int(conn.execute("SELECT COUNT(*) AS cnt FROM public_sources").fetchone()["cnt"])
        mis_cnt = int(conn.execute("SELECT COUNT(*) AS cnt FROM missions").fetchone()["cnt"])
        if (
            state_row is not None
            and int(state_row["version"]) >= SEED_VERSION
            and src_cnt >= len(PUBLIC_SOURCES)
            and mis_cnt >= len(SEED_MISSIONS)
        ):
            return False

    now_iso = _utc_now_iso()

    with db.transaction() as conn:
        if db.is_postgres:
            conn.execute(f"SELECT pg_advisory_xact_lock({PG_SEED_ADVISORY_LOCK_KEY})")

        state_row = conn.execute(
            "SELECT version FROM seed_state WHERE key = 'canonical_seed'"
        ).fetchone()
        src_cnt = int(conn.execute("SELECT COUNT(*) AS cnt FROM public_sources").fetchone()["cnt"])
        mis_cnt = int(conn.execute("SELECT COUNT(*) AS cnt FROM missions").fetchone()["cnt"])
        if (
            state_row is not None
            and int(state_row["version"]) >= SEED_VERSION
            and src_cnt >= len(PUBLIC_SOURCES)
            and mis_cnt >= len(SEED_MISSIONS)
        ):
            return False

        # 1. Upsert all 15 canonical Indian public data sources (Tiers 1-3)
        for src in PUBLIC_SOURCES:
            conn.execute(
                """
                INSERT INTO public_sources (
                    id, slug, name, pillar, authority, base_url,
                    official_publisher, source_landing_page, mirror_name, retrieval_url,
                    ingestion_tier, connector_type, data_contents, format_access,
                    config_json, robots_policy, last_synced_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    pillar = excluded.pillar,
                    authority = excluded.authority,
                    base_url = excluded.base_url,
                    official_publisher = excluded.official_publisher,
                    source_landing_page = excluded.source_landing_page,
                    mirror_name = excluded.mirror_name,
                    retrieval_url = excluded.retrieval_url,
                    ingestion_tier = excluded.ingestion_tier,
                    connector_type = excluded.connector_type,
                    data_contents = excluded.data_contents,
                    format_access = excluded.format_access,
                    config_json = excluded.config_json,
                    robots_policy = excluded.robots_policy
                """,
                (
                    src["id"],
                    src["slug"],
                    src["name"],
                    src["pillar"],
                    src["authority"],
                    src["base_url"],
                    src.get("official_publisher", ""),
                    src.get("source_landing_page", src["base_url"]),
                    src.get("mirror_name", ""),
                    src.get("retrieval_url", src["base_url"]),
                    src["ingestion_tier"],
                    src["connector_type"],
                    src["data_contents"],
                    src["format_access"],
                    dumps_json({"tier": src["ingestion_tier"], "seed_version": SEED_VERSION}),
                    src["robots_policy"],
                    now_iso,
                ),
            )

        # 2. Upsert all 8 real Government of India Audit Missions without wiping historical counters
        task_counts: dict[str, int] = {}
        for task in REAL_AUDIT_TASKS:
            mid = task["mission_id"]
            task_counts[mid] = task_counts.get(mid, 0) + 1

        for m in SEED_MISSIONS:
            conn.execute(
                """
                INSERT INTO missions (
                    id, slug, title, description, pillar, methodology_code,
                    jurisdiction, ministry_or_authority, statutory_anchors_json,
                    source_ids_json, sample_dataset_path, data_mode, status,
                    priority, total_tasks, completed_tasks, verified_findings_count,
                    cleared_count, cleared_findings_count, unevaluable_count,
                    monetary_audited_inr, monetary_unit, financial_quantity_type, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, 'active', ?, ?, 0, 0, 0, 0, 0, 0.0, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    description = excluded.description,
                    pillar = excluded.pillar,
                    methodology_code = excluded.methodology_code,
                    jurisdiction = excluded.jurisdiction,
                    ministry_or_authority = excluded.ministry_or_authority,
                    statutory_anchors_json = excluded.statutory_anchors_json,
                    source_ids_json = excluded.source_ids_json,
                    data_mode = excluded.data_mode,
                    priority = excluded.priority,
                    monetary_unit = excluded.monetary_unit,
                    financial_quantity_type = excluded.financial_quantity_type
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
                    m.get("monetary_unit", "inr"),
                    m.get("financial_quantity_type", ""),
                    now_iso,
                ),
            )

        # 3. Upsert all 16 pinned Government of India anomaly_scan tasks cleanly:
        #    - New tasks are inserted as 'queued'.
        #    - Existing 'queued' tasks refresh pinned URLs & provenance metadata.
        #    - Existing claimed/completed/settled tasks preserve historical execution state,
        #      updating only dataset_key and financial_quantity_type for accurate deduplication.
        inserted_by_mission: dict[str, int] = {}
        for task in REAL_AUDIT_TASKS:
            existing = conn.execute("SELECT id, status FROM tasks WHERE id = ?", (task["id"],)).fetchone()
            payload_str = dumps_json(task["payload"])
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO tasks (
                        id, mission_id, mission_slug, methodology_code, task_type,
                        title, instructions, payload_json, target_finding_id,
                        excluded_handles_json, status, dataset_key, financial_quantity_type,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'anomaly_scan', ?, ?, ?, NULL, '[]', 'queued', ?, ?, ?, ?)
                    """,
                    (
                        task["id"],
                        task["mission_id"],
                        task["mission_slug"],
                        task["methodology_code"],
                        task["title"],
                        task["instructions"],
                        payload_str,
                        task["dataset_key"],
                        task["financial_quantity_type"],
                        now_iso,
                        now_iso,
                    ),
                )
                if state_row is not None or mis_cnt > 0:
                    inserted_by_mission[task["mission_id"]] = inserted_by_mission.get(task["mission_id"], 0) + 1
            elif existing["status"] == "queued":
                conn.execute(
                    """
                    UPDATE tasks
                    SET title = ?, instructions = ?, payload_json = ?,
                        dataset_key = ?, financial_quantity_type = ?, updated_at = ?
                    WHERE id = ? AND status = 'queued'
                    """,
                    (
                        task["title"],
                        task["instructions"],
                        payload_str,
                        task["dataset_key"],
                        task["financial_quantity_type"],
                        now_iso,
                        task["id"],
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE tasks
                    SET dataset_key = ?, financial_quantity_type = ?
                    WHERE id = ?
                    """,
                    (task["dataset_key"], task["financial_quantity_type"], task["id"]),
                )

        # Ensure mission total_tasks reflects actual total tasks for each mission cleanly
        for m in SEED_MISSIONS:
            mid = m["id"]
            cnt_row = conn.execute(
                "SELECT COUNT(*) AS c FROM tasks WHERE mission_id = ?", (mid,)
            ).fetchone()
            actual_total = int(cnt_row["c"]) if cnt_row else 0
            conn.execute(
                "UPDATE missions SET total_tasks = ? WHERE id = ? AND total_tasks < ?",
                (actual_total, mid, actual_total),
            )

        conn.execute(
            """
            INSERT INTO seed_state (key, version, updated_at)
            VALUES ('canonical_seed', ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                version = excluded.version,
                updated_at = excluded.updated_at
            """,
            (SEED_VERSION, now_iso),
        )

    return True
