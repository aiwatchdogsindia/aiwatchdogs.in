#!/usr/bin/env python3
"""Build Project P7 (`only-one-bid`, methodology `P7`, 10,000 tasks) from verified Indian procurement records.

Hook: "Which public purchases consistently attract just one bidder — and keep going to the
same winner across 10 years of Indian government tenders?"

Assembles exactly 10,000 deduplicated, SHA-256-pinned, real Indian government procurement tasks
spanning 2016–2026 across Union Ministries and 20+ States from strictly Tier-1, zero-CAPTCHA,
publicly verifiable sources:

  1. Open Contracting Partnership (OCP) Data Registry Publication #77 — Himachal Pradesh OCDS
     (`full.jsonl.gz`, 0.79 MiB, frozen snapshot, 2016–2020)
  2. Open Contracting Partnership (OCP) Data Registry Publication #131 — Assam State Finance Dept OCDS
     (`full.jsonl.gz`, 1.90 MiB, frozen snapshot, 2016–2022)
  3. CivicDataLab Assam OCDS v3 Release Package (`assam-tenders-data`, 13.36 MiB zip, commit-SHA pinned,
     2017–2023, bidder-level technical qualification & award records)
  4. CivicDataLab IDS-DRR Multi-State GePNIC AOC CSVs (Assam 2016–2026, Himachal Pradesh 2016–2025,
     Odisha 2019–2026, Uttar Pradesh 2018–2024; commit-SHA pinned, strictly filtered to verified
     `Status == "Accepted-AOC"` rows and never using the mislabeled `No of Bids Received` cover-count column)
  5. Indian Government (World Bank–assisted projects) Contract Awards API
     (`search.worldbank.org/api/v2/procnotices`, strictly filtered to `project_ctry_code=IN`,
     pan-India Central + 20+ States, 2016–2026: closed/dormant project shards `total <= 1,000`
     with `last_notice < 2025-06-01`, plus permanently immutable individual `id=OP...` notices for 2024–2026)

Every shard is <= 32 MiB (fitting cleanly in `connectors/loader.py`'s `_PINNED_MEM_CACHE`),
SHA-256 verified, checked for BNS Sec 356 neutrality and DPDP Act 2023 PII cleanliness, and written to:
  - projects/only-one-bid.json (header < 10 KiB before `"tasks":[`, 1 compact JSON line/task, <= 20 MiB)
  - projects/only-one-bid/documents.csv
  - projects/only-one-bid/coverage.json
  - projects/only-one-bid/METHODOLOGY.md
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import math
import re
import sys
import time
import urllib.error
import urllib.request
import zipfile
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from aiwatchdogs.core.safety import (  # noqa: E402
    contains_pii,
    scan_forbidden_terms,
    scrub_pii,
    validate_safe_http_url,
)

PROJECT_SLUG = "only-one-bid"
PROJECT_CODE = "P7"
METHODOLOGY_CODE = "P7"
TARGET_TASKS = 10_000
AS_OF_DATE = "2026-11-27"
YEAR_MIN = 2016
YEAR_MAX = 2026

HOOK_TEXT = (
    "Which public purchases consistently attract just one bidder — and keep going to the "
    "same winner across 10 years of Indian government tenders?"
)
PROJECT_TITLE = "Only One Bid: Which purchases consistently attract just one bidder?"
USER_AGENT = "AIWatchdogs-India-OpenAudit/0.1 (aiwatchdogs.in; lawful-public-audit)"

DEFAULT_CACHE_DIR = Path.home() / ".cache" / "aiwatchdogs" / PROJECT_SLUG
PROJECTS_DIR = REPO_ROOT / "projects"
PROJECT_JSON_PATH = PROJECTS_DIR / f"{PROJECT_SLUG}.json"
PROJECT_ASSETS_DIR = PROJECTS_DIR / PROJECT_SLUG

ANCHOR_WB_ID = "OP00113760"  # MPRRDA P157054, IN-MPRRDA-194572-CW-RFB, M/S OSHO ASSOCIATES, 5->1 qual, Rs 14.52 Cr, 7/7 SQ wins

# Static Tier-1 immutable shards (all verified HTTP 200, zero CAPTCHA, <= 30.03 MiB)
STATIC_SHARDS: dict[str, dict[str, Any]] = {
    "p7-ocp77-hp": {
        "cache_name": "ocp77_hp.jsonl.gz",
        "source_format": "ocp_jsonl_gz",
        "portal": "hptenders.gov.in (OCP #77)",
        "portal_key": "hp-ocds-ocp77",
        "jurisdiction": "State:Himachal Pradesh",
        "jurisdiction_level": "state",
        "official_publisher": "HP Finance Dept / CivicDataLab (OCP #77)",
        "retrieval_url": "https://data.open-contracting.org/en/publication/77/download?name=full.jsonl.gz",
        "landing_url": "https://data.open-contracting.org/en/publication/77",
        "expected_sha256": "aa91e75dd71172f68870e718810e101ada1eec2a63a303c9590ef3e9cd160c94",
        "artifact_title": "HP Procurement OCDS (OCP #77, 2016–20)",
    },
    "p7-ocp131-assam": {
        "cache_name": "ocp131_assam.jsonl.gz",
        "source_format": "ocp_jsonl_gz",
        "portal": "assamtenders.gov.in (OCP #131)",
        "portal_key": "assam-ocds-ocp131",
        "jurisdiction": "State:Assam",
        "jurisdiction_level": "state",
        "official_publisher": "Assam Finance Dept (OCP #131)",
        "retrieval_url": "https://data.open-contracting.org/en/publication/131/download?name=full.jsonl.gz",
        "landing_url": "https://data.open-contracting.org/en/publication/131",
        "expected_sha256": "ecf3ee1e37689d1df542a5bfddc86d8017201e18511e1cace31e779f37b87b0e",
        "artifact_title": "Assam Procurement OCDS (OCP #131, 2016–22)",
    },
    "p7-assam-ocds-v3": {
        "cache_name": "assam_v3.json.zip",
        "source_format": "ocds_v3_json_zip",
        "portal": "assamtenders.gov.in (OCDS v3)",
        "portal_key": "assam-ocds-v3",
        "jurisdiction": "State:Assam",
        "jurisdiction_level": "state",
        "official_publisher": "Assam Finance Dept / CivicDataLab OCDS v3",
        "retrieval_url": (
            "https://raw.githubusercontent.com/CivicDataLab/assam-tenders-data/"
            "1e12e516748130c15398b2501fb092bfa7413941/"
            "data/ProcessedData/ocds-mapped-data/v3/ocds_mapped_data_fiscal_year_2016_2022_v3.json.zip"
        ),
        "landing_url": "https://github.com/CivicDataLab/assam-tenders-data",
        "expected_sha256": "debccc31d18818a809a4bde582d8eb39cc7be8e9072a0dfb8defecfa5f21ca35",
        "artifact_title": "Assam Procurement OCDS v3 (2017–23)",
    },
    "p7-assam-aoc": {
        "cache_name": "assam_aoc.csv",
        "source_format": "gepnic_aoc_csv",
        "portal": "assamtenders.gov.in (AOC)",
        "portal_key": "assam-gepnic-aoc",
        "jurisdiction": "State:Assam",
        "jurisdiction_level": "state",
        "state_suffix": "Assam",
        "official_publisher": "Assam e-Procurement (CivicDataLab IDS-DRR)",
        "retrieval_url": (
            "https://raw.githubusercontent.com/CivicDataLab/flood-data-ecosystem-Assam/"
            "9bbc3a695715482d145fdf9e8ea804b32c26dd43/Sources/TENDERS/data/all_tenders.csv"
        ),
        "landing_url": "https://assamtenders.gov.in/nicgep/app",
        "expected_sha256": "54ee154bb7bda5c83ca34f7070b4b451dd7b4bcb671a319fcfb106e90aeecfe8",
        "artifact_title": "Assam Accepted-AOC Awards (2016–26)",
        "award_date_col": "Contract Date :",
    },
    "p7-hp-aoc": {
        "cache_name": "hp_aoc.csv",
        "source_format": "gepnic_aoc_csv",
        "portal": "hptenders.gov.in (AOC)",
        "portal_key": "hp-gepnic-aoc",
        "jurisdiction": "State:Himachal Pradesh",
        "jurisdiction_level": "state",
        "state_suffix": "Himachal Pradesh",
        "official_publisher": "HP e-Procurement (CivicDataLab IDS-DRR)",
        "retrieval_url": (
            "https://raw.githubusercontent.com/CivicDataLab/flood-data-ecosystem-Himachal-Pradesh/"
            "404f0ee6bdbd00ccb8d41e9f0b9b633a48f589df/Sources/TENDERS/data/all_tenders.csv"
        ),
        "landing_url": "https://hptenders.gov.in/nicgep/app",
        "expected_sha256": "d4a0846bbe9179f7e1c9df7e7cf275785c9a99ef1b1e573fa3104a4c9a170383",
        "artifact_title": "HP Accepted-AOC Awards (2016–25)",
        "award_date_col": "Contract Date :",
    },
    "p7-odisha-aoc": {
        "cache_name": "odisha_flood_aoc.csv",
        "source_format": "gepnic_aoc_csv",
        "portal": "tendersodisha.gov.in (AOC)",
        "portal_key": "odisha-gepnic-aoc",
        "jurisdiction": "State:Odisha",
        "jurisdiction_level": "state",
        "state_suffix": "Odisha",
        "official_publisher": "Odisha e-Procurement (CivicDataLab IDS-DRR)",
        "retrieval_url": (
            "https://raw.githubusercontent.com/CivicDataLab/flood-data-ecosystem-Odisha/"
            "d13602b011d87f4fb61d0d3182b05714c05e0f82/Sources/TENDERS/data/flood_tenders_all.csv"
        ),
        "landing_url": "https://tendersodisha.gov.in/nicgep/app",
        "expected_sha256": "7f5ed19f98b405a374078862d4b76856a88e46cda84875c4b54d9563937fb541",
        "artifact_title": "Odisha Accepted-AOC Awards (2019–26)",
        "award_date_col": "Contract Date :",
    },
    "p7-up-aoc": {
        "cache_name": "up_aoc.csv",
        "source_format": "gepnic_aoc_csv",
        "portal": "etender.up.nic.in (AOC)",
        "portal_key": "up-gepnic-aoc",
        "jurisdiction": "State:Uttar Pradesh",
        "jurisdiction_level": "state",
        "state_suffix": "Uttar Pradesh",
        "official_publisher": "UP e-Procurement (CivicDataLab IDS-DRR)",
        "retrieval_url": (
            "https://raw.githubusercontent.com/CivicDataLab/flood-data-ecosystem-UP/"
            "0fa594f2020a54658fbc2608e9f2290043d7e739/Sources/TENDERS/data/all_UP_tenders.csv"
        ),
        "landing_url": "https://etender.up.nic.in/nicgep/app",
        "expected_sha256": "346359da0d227c2587363e5f9e3a58a8721711fa884be342c1ac79f518e8cedf",
        "artifact_title": "UP Accepted-AOC Awards (2018–24)",
        "award_date_col": "Contract Date",
    },
}

# Live-verified 100% India (`project_ctry_code == "IN"`) closed/dormant Indian Government
# (World Bank–assisted) projects: all last_notice < 2025-06-01, total <= 1,000,
# permanently byte-stable single-request JSON shards spanning Union Ministries + 20+ States.
WB_CLOSED_PROJECTS: tuple[tuple[str, str, str, str], ...] = (
    # (project_id, jurisdiction, jurisdiction_level, exact_official_project_name)
    ("P157054", "State:Madhya Pradesh", "state", "Madhya Pradesh Rural Connectivity Project"),
    ("P151744", "State:Himachal Pradesh", "state", "Himachal Pradesh Horticulture Development Project"),
    ("P105311", "State:West Bengal", "state", "West Bengal Accelerated Development of Minor Irrigation"),
    ("P124639", "Union of India", "central", "PMGSY Rural Roads Project"),
    ("P122486", "State:Karnataka", "state", "Karnataka Watershed Development Project - II"),
    ("P124614", "State:Rajasthan", "state", "Rajasthan Agricultural Competitiveness Project"),
    ("P154847", "State:Andhra Pradesh", "state", "Andhra Pradesh Disaster Recovery Project"),
    ("P155522", "State:Bihar", "state", "Bihar Rural Roads Project"),
    ("P133803", "Union of India", "central", "India Ecosystems Service Improvement Project"),
    ("P120836", "State:Maharashtra", "state", "Maharashtra Agricultural Competitiveness Project"),
    ("P102627", "State:Bihar", "state", "Bihar Panchayat Strengthening Project"),
    ("P159576", "State:Bihar", "state", "Bihar Transformative Development Project"),
    ("P144726", "Union of India", "central", "National Cyclone Risk Mitigation Project-II"),
    ("P146653", "State:Uttarakhand", "state", "Uttarakhand Disaster Recovery Project"),
    ("P157836", "State:Meghalaya", "state", "Meghalaya Community-led Landscapes Management Project"),
    ("P127725", "State:Bihar", "state", "Bihar Kosi Basin Development Project"),
    ("P149340", "State:Nagaland", "state", "Nagaland Health Project"),
    ("P112033", "State:Uttar Pradesh", "state", "Uttar Pradesh Sodic Lands Reclamation III Project"),
    ("P150395", "State:Tamil Nadu", "state", "Tamil Nadu Sustainable Urban Development Project"),
    ("P145502", "Union of India", "central", "Technology Center Systems Project (TCSP)"),
    ("P130339", "State:Kerala", "state", "Second Kerala State Transport Project"),
    ("P160463", "State:Andhra Pradesh", "state", "AP Integrated Irrigation & Agriculture Transformation Project"),
    ("P158798", "State:Jharkhand", "state", "Jharkhand Opportunities for Harnessing Rural Growth Project"),
    ("P154525", "State:Uttarakhand", "state", "Uttarakhand Workforce Development Project"),
    ("P156363", "Union of India", "central", "Nai Manzil - Education and Skills Training"),
    ("P127974", "Union of India", "central", "North Eastern Region Power System Improvement Project"),
    ("P071250", "State:Andhra Pradesh", "state", "Andhra Pradesh and Telangana Municipal Development Project"),
    ("P118826", "State:Bihar", "state", "Bihar Integrated Social Protection Strengthening Project"),
    ("P148531", "State:Uttarakhand", "state", "Uttarakhand Health Systems Development Project"),
    ("P155038", "State:Andhra Pradesh", "state", "Andhra Pradesh 24x7 Power for All"),
    ("P156869", "State:Rajasthan", "state", "Strengthening Public Financial Management in Rajasthan"),
    ("P157198", "State:Assam", "state", "Assam State Public Finance Institutional Reforms (ASPIRe)"),
    ("P119085", "Union of India", "central", "National Ganga River Basin Project"),
    ("P132418", "Union of India", "central", "Efficient & Sustainable City Bus Services"),
    ("P121774", "State:Kerala", "state", "Second Kerala Rural Water Supply and Sanitation (Jalanidhi II)"),
    ("P150308", "State:Assam", "state", "Assam Citizen-Centric Service Delivery Project"),
    ("P167350", "Union of India", "central", "Green National Highways Corridor Project"),
    ("P162086", "State:Jharkhand", "state", "Jharkhand Power System Improvement Project"),
    ("P146936", "State:Uttar Pradesh", "state", "Uttar Pradesh Pro-Poor Tourism Development Project"),
    ("P143751", "State:Tamil Nadu", "state", "Second Tamil Nadu Road Sector Project"),
    ("P155303", "State:Madhya Pradesh", "state", "Madhya Pradesh Urban Development Project"),
    ("P160408", "State:Maharashtra", "state", "Maharashtra Project on Climate Resilient Agriculture"),
    ("P114827", "State:Gujarat", "state", "Second Gujarat State Highway Project (GSHP II)"),
    ("P145778", "State:Mizoram", "state", "Mizoram State Roads II Project"),
    ("P152210", "State:Andhra Pradesh", "state", "Andhra Pradesh Rural Inclusive Growth Project"),
    ("P150394", "State:Madhya Pradesh", "state", "MP Higher Education Quality Improvement Project"),
    ("P102330", "Union of India", "central", "North East Rural Livelihoods Project (NERLP)"),
    ("P170645", "State:Chhattisgarh", "state", "Chhattisgarh Inclusive Rural and Accelerated Agriculture Project"),
    ("P130544", "State:Karnataka", "state", "Karnataka Urban Water Supply Modernization Project"),
    ("P157141", "State:Rajasthan", "state", "Rajasthan State Highways Development Program II"),
    ("P154283", "Union of India", "central", "Shared Infrastructure for Solar Parks Project"),
    ("P132739", "Union of India", "central", "Neeranchal National Watershed Project"),
)

WB_FIELDS = (
    "id,noticedate,submission_date,bid_reference_no,agency_name,project_ctry_code,project_ctry_name,"
    "procurement_method_code,procurement_method_name,procurement_group_desc,"
    "project_id,project_name,noticetitle,notice_text"
)
WB_PORTAL_LABEL = "Indian Government (World Bank–assisted projects)"

STATE_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("himachal", "State:Himachal Pradesh"),
    ("uttarakhand", "State:Uttarakhand"),
    ("maharashtra", "State:Maharashtra"),
    ("tamil nadu", "State:Tamil Nadu"),
    ("west bengal", "State:West Bengal"),
    ("madhya pradesh", "State:Madhya Pradesh"),
    ("uttar pradesh", "State:Uttar Pradesh"),
    ("andhra pradesh", "State:Andhra Pradesh"),
    ("arunachal", "State:Arunachal Pradesh"),
    ("karnataka", "State:Karnataka"),
    ("rajasthan", "State:Rajasthan"),
    ("punjab", "State:Punjab"),
    ("odisha", "State:Odisha"),
    ("orissa", "State:Odisha"),
    ("assam", "State:Assam"),
    ("bihar", "State:Bihar"),
    ("kerala", "State:Kerala"),
    ("gujarat", "State:Gujarat"),
    ("jharkhand", "State:Jharkhand"),
    ("chhattisgarh", "State:Chhattisgarh"),
    ("mizoram", "State:Mizoram"),
    ("meghalaya", "State:Meghalaya"),
    ("nagaland", "State:Nagaland"),
    ("manipur", "State:Manipur"),
    ("tripura", "State:Tripura"),
    ("sikkim", "State:Sikkim"),
    ("haryana", "State:Haryana"),
    ("telangana", "State:Telangana"),
    ("goa", "State:Goa"),
    ("delhi", "State:NCT of Delhi"),
    ("jammu", "State:Jammu and Kashmir"),
)

# Entity-name canonicalization matching `engines/only_one_bid.py`
_ENTITY_PREFIX_RE = re.compile(
    r"^(?:m\s*/\s*s\.\s*|m\s*/\s*s\s+|messrs\.\s*|messrs\s+|ms\.\s*|ms\s+|"
    r"shri\.\s*|shri\s+|smt\.\s*|smt\s+|sri\.\s*|sri\s+|dr\.\s*|dr\s+|er\.\s*|er\s+)",
    re.IGNORECASE,
)
_ENTITY_SUFFIX_RE = re.compile(
    r"\b(?:private\s+limited|pvt\.?\s*ltd\.?|p\.?\s*ltd\.?|public\s+limited|limited|ltd\.?|"
    r"llp|l\.l\.p\.?|opc|one\s+person\s+company|incorporated|inc\.?|corp\.?|corporation|"
    r"proprietorship|proprietary|co\.?)\b\.?$",
    re.IGNORECASE,
)
_NON_ALNUM_SPACE_RE = re.compile(r"[^a-z0-9\s]+")
_MULTI_SPACE_RE = re.compile(r"\s+")
_CTRL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]+")
_ACB_RE = re.compile(r"\banti[\s-]+corruption\b", re.IGNORECASE)
_NEUTRAL_REPLACE_RE = re.compile(
    r"(?<![a-z])(?:scam(?:s|med|mer|mers)?|corrupt(?:ed|ing|ly|ion)?|brib(?:e|es|ed|ing|ery)|"
    r"fraud(?:s|ulent|ulently|ster|sters)?|kickback(?:s)?|guilty|stolen|rigged|"
    r"crook(?:s|ed)?|loot(?:s|ed|ing|er|ers)?|embezzl(?:e|es|ed|ing|ement))(?![a-z])",
    re.IGNORECASE,
)


def normalize_entity_name(name: str) -> str:
    raw = _MULTI_SPACE_RE.sub(" ", _CTRL_CHARS_RE.sub(" ", str(name or ""))).strip()[:240].lower()
    if not raw or raw in {"na", "n/a", "none", "null", "--", "-", "unspecified", "unknown", "unspecified vendor"}:
        return ""
    s = raw.replace("&", " and ")
    s = _ENTITY_PREFIX_RE.sub("", s)
    s = _NON_ALNUM_SPACE_RE.sub(" ", s)
    s = _MULTI_SPACE_RE.sub(" ", s).strip()
    for _ in range(3):
        updated = _ENTITY_SUFFIX_RE.sub("", s).strip()
        if updated == s or not updated:
            break
        s = updated
    return _MULTI_SPACE_RE.sub(" ", s).strip()


def neutralize_clean(text: Any, *, max_len: int = 200) -> str:
    if text is None:
        return ""
    s = _CTRL_CHARS_RE.sub(" ", str(text))
    s, _ = scrub_pii(s)
    s = _ACB_RE.sub("Vigilance", s)
    s = _NEUTRAL_REPLACE_RE.sub("compliance-review", s)
    s = _MULTI_SPACE_RE.sub(" ", s).strip()
    if max_len > 0 and len(s) > max_len:
        s = s[:max_len].rstrip(" ,;:-/")
    return s


def slugify_key(text: str, *, max_len: int = 75) -> str:
    k = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    if not k:
        return "unspecified-authority"
    return k[:max_len].strip("-") or "unspecified-authority"


def parse_float_opt(val: Any) -> float | None:
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        f = float(val)
        return f if math.isfinite(f) and f > 0 else None
    t = re.sub(r"[^\d.]", "", str(val))
    if not t:
        return None
    try:
        f = float(t)
        return f if math.isfinite(f) and f > 0 else None
    except ValueError:
        return None


def parse_int_opt(val: Any) -> int | None:
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, int):
        return val if val >= 0 else None
    if isinstance(val, float):
        return int(round(val)) if math.isfinite(val) and val >= 0 else None
    t = str(val).strip().replace(",", "")
    if not t or t.lower() in {"na", "n/a", "none", "null", "-", "--"}:
        return None
    try:
        f = float(t)
        return int(round(f)) if math.isfinite(f) and f >= 0 else None
    except ValueError:
        return None


def parse_date_opt(val: Any) -> date | None:
    if val is None or val == "":
        return None
    raw = str(val).strip()
    if not raw or raw.lower() in {"na", "n/a", "none", "null", "-", "--"}:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    head = raw.split("T", 1)[0].strip()
    for fmt in (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%d-%b-%Y %I:%M %p",
        "%d-%b-%Y %H:%M",
        "%d-%b-%Y",
        "%d/%b/%Y",
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%Y-%d-%m %H:%M",
        "%Y-%d-%m",
    ):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            try:
                return datetime.strptime(head, fmt).date()
            except ValueError:
                continue
    return None


def date_to_fy_start(dt: date) -> int:
    """Return Indian Financial Year start year (Apr–Mar): e.g. 2026-02-15 -> 2025 (FY2025-26)."""
    return dt.year if dt.month >= 4 else dt.year - 1


def fy_start_to_label(fy_start: int) -> str:
    return f"FY{fy_start}-{(fy_start + 1) % 100:02d}"


_STATE_ABBREV_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?:mprrda|mp)\b", re.IGNORECASE), "State:Madhya Pradesh"),
    (re.compile(r"\b(?:hpsebl|hp)\b", re.IGNORECASE), "State:Himachal Pradesh"),
    (re.compile(r"\b(?:brrda)\b", re.IGNORECASE), "State:Bihar"),
    (re.compile(r"\b(?:up)\b", re.IGNORECASE), "State:Uttar Pradesh"),
    (re.compile(r"\b(?:ap)\b", re.IGNORECASE), "State:Andhra Pradesh"),
    (re.compile(r"\b(?:wb)\b", re.IGNORECASE), "State:West Bengal"),
    (re.compile(r"\b(?:tn)\b", re.IGNORECASE), "State:Tamil Nadu"),
    (re.compile(r"\b(?:uk|uttaranchal)\b", re.IGNORECASE), "State:Uttarakhand"),
    (re.compile(r"\b(?:j&k|j\s+and\s+k)\b", re.IGNORECASE), "State:Jammu and Kashmir"),
)


def infer_wb_jurisdiction(project_name: str, agency_name: str) -> tuple[str, str]:
    blob = f"{project_name} {agency_name}".lower()
    for kw, jur in STATE_KEYWORDS:
        if kw in blob:
            return jur, "state"
    for pat, jur in _STATE_ABBREV_PATTERNS:
        if pat.search(blob):
            return jur, "state"
    return "Union of India", "central"


def format_inr_short(amount: float | None) -> str:
    if amount is None or amount <= 0:
        return "value unrecorded"
    if amount >= 10_000_000:
        return f"INR {amount / 10_000_000:,.2f} Cr"
    if amount >= 100_000:
        return f"INR {amount / 100_000:,.2f} L"
    return f"INR {amount:,.0f}"


def http_get_bytes(url: str, *, timeout: int = 60, retries: int = 3) -> bytes:
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json, */*"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
            last_err = exc
            if isinstance(exc, urllib.error.HTTPError) and exc.code == 404:
                raise
            time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"Failed to fetch {url}: {last_err}")


def fetch_cached_shard(cache_dir: Path, cache_name: str, url: str, expected_sha256: str = "") -> tuple[bytes, str]:
    path = cache_dir / cache_name
    if path.exists():
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if not expected_sha256 or digest == expected_sha256:
            return raw, digest
    raw = http_get_bytes(url)
    digest = hashlib.sha256(raw).hexdigest()
    if expected_sha256 and digest != expected_sha256:
        raise ValueError(f"SHA-256 mismatch for {cache_name}: got {digest}, expected {expected_sha256}")
    path.write_bytes(raw)
    return raw, digest


@dataclass
class Candidate:
    shard_id: str
    source_format: str
    portal: str
    portal_key: str
    jurisdiction: str
    jurisdiction_level: str
    authority: str
    authority_key: str
    raw_buyer: str
    buyer_field: str
    tender_id: str
    ocid: str
    match_field: str
    match_value: str
    row_index: int
    record_url: str
    retrieval_url: str
    expected_sha256: str
    official_publisher: str
    source_landing_page: str
    artifact_title: str
    year: int
    fiscal_year: str
    tender_title: str
    category: str
    winner_name: str
    normalized_winner_key: str
    winner_field: str
    bids_received: int | None
    qualified_bids: int | None
    effective_bids: int | None
    single_bid_mechanism: str
    bid_window_days: int | None
    procurement_method: str
    award_value_inr: float | None
    tender_estimate_inr: float | None
    financial_quantity_type: str
    winner_total_wins: int
    winner_single_bid_wins: int
    winner_cohort_total_inr: float
    related_record_ids: list[str]
    cohort: str  # "sb" | "rw" | "bo"
    priority_score: float
    authority_total_awards: int = 0
    authority_total_value_inr: float = 0.0
    is_cleared_control: bool = False
    secondary_url: str = ""
    secondary_expected_sha256: str = ""
    secondary_source_format: str = ""
    status_equals: str = ""
    field_map: dict[str, Any] = field(default_factory=dict)
    is_anchor: bool = False


def clean_authority_name(raw_org: str, state_suffix: str = "") -> str:
    top = str(raw_org or "").split("||", 1)[0].strip()
    top = neutralize_clean(top, max_len=140)
    if not top:
        top = f"State Procurement Department, {state_suffix}" if state_suffix else "Public Procuring Authority"
    if state_suffix and state_suffix.lower() not in top.lower():
        if len(top) + len(state_suffix) + 2 <= 155:
            top = f"{top}, {state_suffix}"
    return top


def parse_wb_notice_html(nt: str) -> dict[str, Any]:
    if not nt:
        return {}
    sec = re.split(
        r"(Awarded Bidder\(s\):|Evaluated Bidder\(s\):|Rejected Bidder\(s\):|Awarded Firm/Individual:)",
        nt,
    )
    blocks = {"awarded": "", "evaluated": "", "rejected": ""}
    cur = None
    for part in sec:
        if part in ("Awarded Bidder(s):", "Awarded Firm/Individual:"):
            cur = "awarded"
        elif part == "Evaluated Bidder(s):":
            cur = "evaluated"
        elif part == "Rejected Bidder(s):":
            cur = "rejected"
        elif cur:
            blocks[cur] += part

    def extract_bidders(html_part: str) -> list[tuple[str, str]]:
        if not html_part.strip():
            return []
        text = re.sub(r"\n+", "\n", re.sub(r"<[^>]+>", "\n", html_part))
        out: list[tuple[str, str]] = []
        for m in re.finditer(r"([^\n]{2,150}?)\s*\((\d{4,10})\)", text):
            nm = m.group(1).strip()
            if nm and not nm.lower().startswith(("usd", "inr", "date", "month", "day", "eur")):
                out.append((nm, m.group(2)))
        return out

    aw = extract_bidders(blocks["awarded"])
    ev = extract_bidders(blocks["evaluated"])
    rj = extract_bidders(blocks["rejected"])

    clean = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", nt))
    awarded_clean = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", blocks["awarded"]))
    currency, amount = None, None
    for blob in (awarded_clean, clean):
        if not blob:
            continue
        m_price = re.search(
            r"(?:Signed\s+Contract\s+price|Contract\s+Price|Contract\s+Value|Awarded\s+Price)\s*:?\s*(INR|USD|EUR)\s*([\d,]+(?:\.\d+)?)",
            blob,
            re.I,
        )
        if not m_price:
            m_price = re.search(
                r"(?:Signed\s+Contract\s+price|Contract\s+Price)\s*:?\s*([\d,]+(?:\.\d+)?)\s*(INR|USD|EUR)",
                blob,
                re.I,
            )
            if m_price:
                currency = m_price.group(2).upper()
                amount = parse_float_opt(m_price.group(1))
        else:
            currency = m_price.group(1).upper()
            amount = parse_float_opt(m_price.group(2))
        if amount is not None:
            break

    m_scope = re.search(r"Scope of Contract:\s*(.*?)(?:Notice Version No:|Date Notification|Contract Signature)", clean, re.I)
    scope = m_scope.group(1).strip() if m_scope else ""

    m_dt = re.search(
        r"(?:Contract Signature Date|Date Notification of Award Issued)\s*(?:\(YYYY/MM/DD\))?\s*(\d{4}[/-]\d{2}[/-]\d{2})",
        clean,
        re.I,
    )
    award_dt = parse_date_opt(m_dt.group(1)) if m_dt else None

    return {
        "awarded": aw,
        "evaluated": ev,
        "rejected": rj,
        "has_ev_section": "Evaluated Bidder(s):" in nt,
        "has_rj_section": "Rejected Bidder(s):" in nt,
        "currency": currency,
        "amount": amount,
        "scope": scope,
        "award_date": award_dt,
    }


def load_ocp77_candidates(cache_dir: Path, doc_stats: dict[str, dict[str, Any]]) -> list[Candidate]:
    meta = STATIC_SHARDS["p7-ocp77-hp"]
    raw, sha = fetch_cached_shard(cache_dir, meta["cache_name"], meta["retrieval_url"], meta["expected_sha256"])
    rows: list[tuple[int, dict[str, Any]]] = []
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gz:
        for idx, line in enumerate(gz, start=1):
            if line.strip():
                rows.append((idx, json.loads(line)))
    doc_stats["p7-ocp77-hp"] = {
        **meta,
        "bytes": len(raw),
        "sha256": sha,
        "total_rows": len(rows),
        "year_min": 2016,
        "year_max": 2020,
    }

    # First pass: compute repeat-winner & single-bid counts per (normalized_winner, normalized_buyer)
    wb_rows: dict[tuple[str, str], list[tuple[str, float, int | None]]] = defaultdict(list)
    buyer_totals: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    parsed: list[dict[str, Any]] = []
    for idx, r in rows:
        t = r.get("tender") or {}
        buyer_raw = ((r.get("buyer") or {}).get("name") or "").strip()
        if not buyer_raw:
            continue
        nb = parse_int_opt(t.get("numberOfTenderers"))
        pd = parse_date_opt(t.get("datePublished") or (t.get("tenderPeriod") or {}).get("startDate"))
        aw_obj = r.get("award") or {}
        ad = parse_date_opt(aw_obj.get("date"))
        awards = r.get("awards") or []
        a0 = awards[0] if awards and isinstance(awards[0], dict) else {}
        if not ad:
            ad = parse_date_opt(a0.get("date"))
        dt = ad or pd
        if dt is None:
            continue
        fy_start = date_to_fy_start(dt)
        if not (YEAR_MIN <= fy_start <= YEAR_MAX):
            continue
        sups = a0.get("suppliers") or []
        w_raw = (sups[0].get("name") or "").strip() if sups and isinstance(sups[0], dict) else ""
        w_key = normalize_entity_name(w_raw)
        b_key = normalize_entity_name(buyer_raw)
        av = parse_float_opt((a0.get("value") or {}).get("amount"))
        tv = parse_float_opt((t.get("value") or {}).get("amount"))
        ocid = str(r.get("ocid") or "").strip()
        tid = str(t.get("id") or ocid).strip()
        if not ocid or not tid:
            continue
        if w_key and b_key:
            wb_rows[(w_key, b_key)].append((tid, av or tv or 0.0, nb))
        if b_key:
            buyer_totals[b_key][0] += 1.0
            buyer_totals[b_key][1] += av or tv or 0.0
        parsed.append(
            {
                "idx": idx,
                "ocid": ocid,
                "tid": tid,
                "buyer_raw": buyer_raw,
                "b_key": b_key,
                "w_raw": w_raw,
                "w_key": w_key,
                "nb": nb,
                "dt": dt,
                "fy_start": fy_start,
                "av": av,
                "tv": tv,
                "title": str(t.get("title") or "").strip(),
                "cat": str(((t.get("items") or [{}])[0]).get("id") or "Civil Works").strip(),
            }
        )

    cands: list[Candidate] = []
    for p in parsed:
        w_list = wb_rows.get((p["w_key"], p["b_key"]), []) if p["w_key"] else []
        wins = len(w_list)
        sb_wins = sum(1 for _, _, b in w_list if b == 1)
        coh_inr = round(sum(v for _, v, _ in w_list), 2)
        rel_ids = list(dict.fromkeys(t for t, _, _ in w_list))[:15]
        nb = p["nb"]
        is_sb = nb == 1
        is_rw = bool(p["w_key"]) and wins >= 3

        if is_sb and is_rw:
            cohort = "bo"
            score = 100.0 + sb_wins * 10 + wins * 2 + math.log10(max(p["av"] or p["tv"] or 1.0, 1.0))
            cleared = False
        elif is_sb:
            cohort = "sb"
            score = 60.0 + math.log10(max(p["av"] or p["tv"] or 1.0, 1.0)) * 5
            cleared = (p["av"] or p["tv"] or 0.0) < 500_000.0
        elif is_rw:
            cohort = "rw"
            score = 45.0 + wins * 3 + math.log10(max(coh_inr, 1.0)) * 2
            cleared = False
        elif nb is not None and nb >= 2 and p["w_key"] and wins <= 2:
            cohort = "rw"
            score = 5.0 + math.log10(max(p["av"] or p["tv"] or 1.0, 1.0))
            cleared = True
        else:
            continue

        auth = clean_authority_name(p["buyer_raw"], "Himachal Pradesh")
        cands.append(
            Candidate(
                shard_id="p7-ocp77-hp",
                source_format=meta["source_format"],
                portal=meta["portal"],
                portal_key=meta["portal_key"],
                jurisdiction=meta["jurisdiction"],
                jurisdiction_level=meta["jurisdiction_level"],
                authority=auth,
                authority_key=slugify_key(auth),
                raw_buyer=p["buyer_raw"],
                buyer_field="buyer_name",
                tender_id=p["tid"],
                ocid=p["ocid"],
                match_field="ocid",
                match_value=p["ocid"],
                row_index=p["idx"],
                record_url=meta["landing_url"],
                retrieval_url=meta["retrieval_url"],
                expected_sha256=sha,
                official_publisher=meta["official_publisher"],
                source_landing_page=meta["landing_url"],
                artifact_title=meta["artifact_title"],
                year=p["fy_start"],
                fiscal_year=fy_start_to_label(p["fy_start"]),
                tender_title=neutralize_clean(p["title"] or p["tid"], max_len=140),
                category=neutralize_clean(p["cat"], max_len=80),
                winner_name=neutralize_clean(p["w_raw"], max_len=120),
                normalized_winner_key=p["w_key"],
                winner_field="vendor_name",
                bids_received=nb,
                qualified_bids=nb,
                effective_bids=nb,
                single_bid_mechanism="single_bid_received" if nb == 1 else ("multi_bid" if (nb or 0) >= 2 else "unrecorded"),
                bid_window_days=None,
                procurement_method="open",
                award_value_inr=p["av"],
                tender_estimate_inr=p["tv"],
                financial_quantity_type="awarded_contract_values" if p["av"] else "tender_estimates",
                winner_total_wins=wins,
                winner_single_bid_wins=sb_wins,
                winner_cohort_total_inr=coh_inr,
                related_record_ids=rel_ids,
                cohort=cohort,
                priority_score=score,
                authority_total_awards=int(buyer_totals[p["b_key"]][0]),
                authority_total_value_inr=round(buyer_totals[p["b_key"]][1], 2),
                is_cleared_control=cleared,
            )
        )
    return cands


def load_assam_v3_candidates(cache_dir: Path, doc_stats: dict[str, dict[str, Any]]) -> list[Candidate]:
    meta = STATIC_SHARDS["p7-assam-ocds-v3"]
    raw, sha = fetch_cached_shard(cache_dir, meta["cache_name"], meta["retrieval_url"], meta["expected_sha256"])
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        jname = [n for n in zf.namelist() if n.endswith(".json") and not n.startswith("__MACOSX")][0]
        rels = json.loads(zf.read(jname))["releases"]
    doc_stats["p7-assam-ocds-v3"] = {
        **meta,
        "bytes": len(raw),
        "sha256": sha,
        "total_rows": len(rels),
        "year_min": 2017,
        "year_max": 2023,
    }

    wb_rows: dict[tuple[str, str], list[tuple[str, float, int | None]]] = defaultdict(list)
    buyer_totals: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    parsed: list[dict[str, Any]] = []
    for idx, r in enumerate(rels, start=1):
        awards = r.get("awards") or []
        if not awards:
            continue
        t = r.get("tender") or {}
        if isinstance(t, list):
            t = t[0] if t else {}
        buyer = r.get("buyer") or {}
        if isinstance(buyer, list):
            buyer = buyer[0] if buyer else {}
        buyer_raw = (buyer.get("name") or "").strip()
        if not buyer_raw:
            continue
        a0 = awards[0] if isinstance(awards[0], dict) else {}
        sups = a0.get("suppliers") or []
        w_raw = (sups[0].get("name") or "").strip() if sups and isinstance(sups[0], dict) else ""
        w_key = normalize_entity_name(w_raw)
        b_key = normalize_entity_name(buyer_raw)
        if not w_key or not b_key:
            continue
        pd = parse_date_opt(t.get("datePublished"))
        ad = parse_date_opt(a0.get("date") or (a0.get("contractPeriod") or {}).get("startDate"))
        dt = ad or pd
        if dt is None:
            continue
        fy_start = date_to_fy_start(dt)
        if not (YEAR_MIN <= fy_start <= YEAR_MAX):
            continue
        bids_d = (r.get("bids") or {}).get("details", []) if isinstance(r.get("bids"), dict) else []
        if not bids_d:
            continue
        bids_rec = len(bids_d)
        tech_passed = [
            b
            for b in bids_d
            if any(k in str(b.get("status", "")).lower() for k in ("accepted", "rejected-aoc", "rejected-finance"))
        ]
        qual = len(tech_passed) if tech_passed else None
        eff = qual if (qual is not None and qual > 0) else bids_rec
        av = parse_float_opt((a0.get("value") or {}).get("amount"))
        tv = parse_float_opt((t.get("value") or {}).get("amount"))
        ocid = str(r.get("ocid") or "").strip()
        tid = str(t.get("id") or ocid).strip()
        if not ocid or not tid:
            continue
        wb_rows[(w_key, b_key)].append((tid, av or tv or 0.0, eff))
        buyer_totals[b_key][0] += 1.0
        buyer_totals[b_key][1] += av or tv or 0.0
        parsed.append(
            {
                "idx": idx,
                "ocid": ocid,
                "tid": tid,
                "buyer_raw": buyer_raw,
                "b_key": b_key,
                "w_raw": w_raw,
                "w_key": w_key,
                "bids_rec": bids_rec,
                "qual": qual,
                "eff": eff,
                "dt": dt,
                "fy_start": fy_start,
                "av": av,
                "tv": tv,
                "title": str(t.get("title") or "").strip(),
                "cat": str(t.get("mainProcurementCategory") or "Works").strip(),
            }
        )

    cands: list[Candidate] = []
    for p in parsed:
        w_list = wb_rows[(p["w_key"], p["b_key"])]
        wins = len(w_list)
        sb_wins = sum(1 for _, _, e in w_list if e == 1)
        coh_inr = round(sum(v for _, v, _ in w_list), 2)
        rel_ids = list(dict.fromkeys(t for t, _, _ in w_list))[:15]
        is_sb = p["eff"] == 1
        is_rw = wins >= 3
        mech = (
            "single_qualified_after_rejection"
            if (is_sb and p["bids_rec"] >= 2 and p["qual"] == 1)
            else ("single_bid_received" if is_sb else "multi_bid")
        )

        if is_sb and is_rw:
            cohort = "bo"
            score = 120.0 + sb_wins * 12 + wins * 3 + math.log10(max(p["av"] or p["tv"] or 1.0, 1.0)) * 4
            cleared = False
        elif is_sb:
            cohort = "sb"
            score = 70.0 + math.log10(max(p["av"] or p["tv"] or 1.0, 1.0)) * 5
            cleared = (p["av"] or p["tv"] or 0.0) < 500_000.0
        elif is_rw:
            cohort = "rw"
            score = 50.0 + wins * 3 + math.log10(max(coh_inr, 1.0)) * 2
            cleared = False
        elif wins <= 2 and p["eff"] >= 3:
            cohort = "rw"
            score = 6.0 + math.log10(max(p["av"] or p["tv"] or 1.0, 1.0))
            cleared = True
        else:
            continue

        auth = clean_authority_name(p["buyer_raw"], "Assam")
        cands.append(
            Candidate(
                shard_id="p7-assam-ocds-v3",
                source_format=meta["source_format"],
                portal=meta["portal"],
                portal_key=meta["portal_key"],
                jurisdiction=meta["jurisdiction"],
                jurisdiction_level=meta["jurisdiction_level"],
                authority=auth,
                authority_key=slugify_key(auth),
                raw_buyer=p["buyer_raw"],
                buyer_field="buyer_name",
                tender_id=p["tid"],
                ocid=p["ocid"],
                match_field="ocid",
                match_value=p["ocid"],
                row_index=p["idx"],
                record_url=meta["landing_url"],
                retrieval_url=meta["retrieval_url"],
                expected_sha256=sha,
                official_publisher=meta["official_publisher"],
                source_landing_page=meta["landing_url"],
                artifact_title=meta["artifact_title"],
                year=p["fy_start"],
                fiscal_year=fy_start_to_label(p["fy_start"]),
                tender_title=neutralize_clean(p["title"] or p["tid"], max_len=140),
                category=neutralize_clean(p["cat"], max_len=80),
                winner_name=neutralize_clean(p["w_raw"], max_len=120),
                normalized_winner_key=p["w_key"],
                winner_field="vendor_name",
                bids_received=p["bids_rec"],
                qualified_bids=p["qual"],
                effective_bids=p["eff"],
                single_bid_mechanism=mech,
                bid_window_days=None,
                procurement_method="open",
                award_value_inr=p["av"],
                tender_estimate_inr=p["tv"],
                financial_quantity_type="awarded_contract_values" if p["av"] else "tender_estimates",
                winner_total_wins=wins,
                winner_single_bid_wins=sb_wins,
                winner_cohort_total_inr=coh_inr,
                related_record_ids=rel_ids,
                cohort=cohort,
                priority_score=score,
                authority_total_awards=int(buyer_totals[p["b_key"]][0]),
                authority_total_value_inr=round(buyer_totals[p["b_key"]][1], 2),
                is_cleared_control=cleared,
            )
        )
    return cands


def load_gepnic_aoc_candidates(
    shard_id: str, cache_dir: Path, doc_stats: dict[str, dict[str, Any]]
) -> tuple[list[Candidate], dict[str, dict[str, Any]]]:
    """Load strictly verified `Status == 'Accepted-AOC'` rows from CivicDataLab IDS-DRR state CSVs.

    NEVER uses the mislabeled `No of Bids Received` column (forensically proven to hold GePNIC
    cover counts). Uses exact `(normalized_winner, exact_raw_org_chain)` counts so repeat-winner
    metrics hold under both exact-division and top-department buyer matching.
    """
    meta = STATIC_SHARDS[shard_id]
    raw, sha = fetch_cached_shard(cache_dir, meta["cache_name"], meta["retrieval_url"], meta["expected_sha256"])
    text = raw.decode("utf-8", "replace")
    rdr = csv.DictReader(io.StringIO(text))
    award_col = meta["award_date_col"]

    total_rows = 0
    accepted: list[dict[str, Any]] = []
    # Track exact raw org chain counts AND by Tender ID (for Assam OCP#131 join)
    exact_wb: dict[tuple[str, str], list[tuple[str, float]]] = defaultdict(list)
    buyer_totals: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    by_tid: dict[str, dict[str, Any]] = {}
    years_seen: list[int] = []

    for idx, row in enumerate(rdr, start=2):
        total_rows += 1
        if (row.get("Status") or "").strip() != "Accepted-AOC":
            continue
        tid = (row.get("Tender ID") or "").strip()
        org_raw = (row.get("Organisation Chain") or "").strip()
        w_raw = (row.get("Bidder Name") or "").strip()
        w_key = normalize_entity_name(w_raw)
        b_key = normalize_entity_name(org_raw)
        if not tid or not org_raw or not w_key or not b_key:
            continue
        ad = parse_date_opt(row.get(award_col))
        pd = parse_date_opt(row.get("Published Date"))
        dt = ad or pd
        if dt is None:
            continue
        fy_start = date_to_fy_start(dt)
        if not (YEAR_MIN <= fy_start <= YEAR_MAX):
            continue
        av = parse_float_opt(row.get("Awarded Value"))
        tv = parse_float_opt(row.get("Tender Value in ₹"))
        val = av or tv or 0.0
        years_seen.append(fy_start)
        exact_wb[(w_key, b_key)].append((tid, val))
        buyer_totals[b_key][0] += 1.0
        buyer_totals[b_key][1] += val
        rec = {
            "idx": idx,
            "tid": tid,
            "org_raw": org_raw,
            "b_key": b_key,
            "w_raw": w_raw,
            "w_key": w_key,
            "dt": dt,
            "fy_start": fy_start,
            "av": av,
            "tv": tv,
            "title": (row.get("tender_title") or row.get("Work Description") or tid).strip(),
            "cat": (row.get("Product Category") or row.get("Tender Category") or "Civil Works").strip(),
            "method": (row.get("Tender Type") or "Open Tender").strip(),
        }
        accepted.append(rec)
        by_tid[tid] = rec

    doc_stats[shard_id] = {
        **meta,
        "bytes": len(raw),
        "sha256": sha,
        "total_rows": total_rows,
        "accepted_rows": len(accepted),
        "year_min": min(years_seen) if years_seen else YEAR_MIN,
        "year_max": max(years_seen) if years_seen else YEAR_MAX,
    }

    fmap = {
        "tender_id": "Tender ID",
        "title": "tender_title",
        "buyer": "Organisation Chain",
        "winner": "Bidder Name",
        "bid_count": None,
        "award_value_inr": "Awarded Value",
        "tender_estimate_inr": "Tender Value in ₹",
        "award_date": award_col,
    }

    cands: list[Candidate] = []
    for p in accepted:
        w_list = exact_wb[(p["w_key"], p["b_key"])]
        wins = len(w_list)
        if wins < 3:
            continue
        coh_inr = round(sum(v for _, v in w_list), 2)
        rel_ids = list(dict.fromkeys(t for t, _ in w_list))[:15]
        auth = clean_authority_name(p["org_raw"], meta["state_suffix"])
        # Prioritize persistent >=5 wins and recent FY2023-24..FY2026-27 coverage
        recency_bonus = 15.0 if p["fy_start"] >= 2023 else 0.0
        score = 40.0 + min(wins, 40) * 2.0 + math.log10(max(p["av"] or p["tv"] or 1.0, 1.0)) * 3.0 + recency_bonus
        cands.append(
            Candidate(
                shard_id=shard_id,
                source_format="gepnic_aoc_csv",
                portal=meta["portal"],
                portal_key=meta["portal_key"],
                jurisdiction=meta["jurisdiction"],
                jurisdiction_level=meta["jurisdiction_level"],
                authority=auth,
                authority_key=slugify_key(auth),
                raw_buyer=p["org_raw"],
                buyer_field="Organisation Chain",
                tender_id=p["tid"],
                ocid="",
                match_field="Tender ID",
                match_value=p["tid"],
                row_index=p["idx"],
                record_url=meta["landing_url"],
                retrieval_url=meta["retrieval_url"],
                expected_sha256=sha,
                official_publisher=meta["official_publisher"],
                source_landing_page=meta["landing_url"],
                artifact_title=meta["artifact_title"],
                year=p["fy_start"],
                fiscal_year=fy_start_to_label(p["fy_start"]),
                tender_title=neutralize_clean(p["title"], max_len=140),
                category=neutralize_clean(p["cat"], max_len=80),
                winner_name=neutralize_clean(p["w_raw"], max_len=120),
                normalized_winner_key=p["w_key"],
                winner_field="Bidder Name",
                bids_received=None,
                qualified_bids=None,
                effective_bids=None,
                single_bid_mechanism="unrecorded",
                bid_window_days=None,
                procurement_method=neutralize_clean(p["method"], max_len=60),
                award_value_inr=p["av"],
                tender_estimate_inr=p["tv"],
                financial_quantity_type="awarded_contract_values" if p["av"] else "tender_estimates",
                winner_total_wins=wins,
                winner_single_bid_wins=0,
                winner_cohort_total_inr=coh_inr,
                related_record_ids=rel_ids,
                cohort="rw",
                priority_score=score,
                authority_total_awards=int(buyer_totals[p["b_key"]][0]),
                authority_total_value_inr=round(buyer_totals[p["b_key"]][1], 2),
                is_cleared_control=False,
                status_equals="Accepted-AOC",
                field_map=dict(fmap),
            )
        )
    # Attach exact_wb lookup onto by_tid entries so OCP#131 join knows Assam repeat-winner counts
    for tid, rec in by_tid.items():
        w_list = exact_wb[(rec["w_key"], rec["b_key"])]
        rec["wins"] = len(w_list)
        rec["coh_inr"] = round(sum(v for _, v in w_list), 2)
        rec["rel_ids"] = list(dict.fromkeys(t for t, _ in w_list))[:15]
        rec["auth_awards"] = int(buyer_totals[rec["b_key"]][0])
        rec["auth_val"] = round(buyer_totals[rec["b_key"]][1], 2)
    return cands, by_tid


def load_ocp131_candidates(
    cache_dir: Path,
    doc_stats: dict[str, dict[str, Any]],
    assam_aoc_by_tid: dict[str, dict[str, Any]],
) -> list[Candidate]:
    meta = STATIC_SHARDS["p7-ocp131-assam"]
    aoc_meta = STATIC_SHARDS["p7-assam-aoc"]
    raw, sha = fetch_cached_shard(cache_dir, meta["cache_name"], meta["retrieval_url"], meta["expected_sha256"])
    rows: list[tuple[int, dict[str, Any]]] = []
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gz:
        for idx, line in enumerate(gz, start=1):
            if line.strip():
                rows.append((idx, json.loads(line)))
    doc_stats["p7-ocp131-assam"] = {
        **meta,
        "bytes": len(raw),
        "sha256": sha,
        "total_rows": len(rows),
        "year_min": 2016,
        "year_max": 2022,
    }

    cands: list[Candidate] = []
    for idx, r in rows:
        t = r.get("tender") or {}
        if isinstance(t, list):
            t = t[0] if t else {}
        buyer = r.get("buyer") or {}
        if isinstance(buyer, list):
            buyer = buyer[0] if buyer else {}
        buyer_raw = (buyer.get("name") or "").strip()
        if not buyer_raw:
            continue
        nb = parse_int_opt(t.get("numberOfTenderers"))
        if nb is None or nb <= 0:
            continue
        pd = parse_date_opt(t.get("datePublished") or (t.get("tenderPeriod") or {}).get("startDate"))
        if pd is None:
            continue
        fy_start = date_to_fy_start(pd)
        if not (YEAR_MIN <= fy_start <= YEAR_MAX):
            continue
        ocid = str(r.get("ocid") or "").strip()
        tid = str(t.get("id") or ocid).strip()
        if not ocid or not tid:
            continue
        tv = parse_float_opt((t.get("value") or {}).get("amount"))
        period = t.get("tenderPeriod") or {}
        bwd = parse_int_opt(period.get("durationInDays"))

        aoc_match = assam_aoc_by_tid.get(tid)
        if nb == 1 and aoc_match and aoc_match["wins"] >= 3:
            # Combined BOTH: OCP#131 genuine single-bid + Assam Accepted-AOC repeat winner!
            auth = clean_authority_name(buyer_raw, "Assam")
            cands.append(
                Candidate(
                    shard_id="p7-ocp131-assam",
                    source_format="ocp_jsonl_gz",
                    portal=meta["portal"],
                    portal_key=meta["portal_key"],
                    jurisdiction=meta["jurisdiction"],
                    jurisdiction_level=meta["jurisdiction_level"],
                    authority=auth,
                    authority_key=slugify_key(auth),
                    raw_buyer=buyer_raw,
                    buyer_field="buyer_name",
                    tender_id=tid,
                    ocid=ocid,
                    match_field="ocid",
                    match_value=ocid,
                    row_index=idx,
                    record_url=meta["landing_url"],
                    retrieval_url=meta["retrieval_url"],
                    expected_sha256=sha,
                    official_publisher=meta["official_publisher"],
                    source_landing_page=meta["landing_url"],
                    artifact_title=meta["artifact_title"],
                    year=fy_start,
                    fiscal_year=fy_start_to_label(fy_start),
                    tender_title=neutralize_clean(t.get("title") or tid, max_len=140),
                    category=neutralize_clean(t.get("mainProcurementCategory") or "Works", max_len=80),
                    winner_name=neutralize_clean(aoc_match["w_raw"], max_len=120),
                    normalized_winner_key=aoc_match["w_key"],
                    winner_field="Bidder Name",
                    bids_received=1,
                    qualified_bids=1,
                    effective_bids=1,
                    single_bid_mechanism="single_bid_received",
                    bid_window_days=bwd,
                    procurement_method=neutralize_clean(t.get("procurementMethod") or "open", max_len=60),
                    award_value_inr=aoc_match["av"],
                    tender_estimate_inr=tv,
                    financial_quantity_type="awarded_contract_values" if aoc_match["av"] else "tender_estimates",
                    winner_total_wins=aoc_match["wins"],
                    winner_single_bid_wins=1,
                    winner_cohort_total_inr=aoc_match["coh_inr"],
                    related_record_ids=aoc_match["rel_ids"],
                    cohort="bo",
                    priority_score=110.0 + aoc_match["wins"] * 2 + math.log10(max(aoc_match["av"] or tv or 1.0, 1.0)) * 4,
                    authority_total_awards=int(aoc_match.get("auth_awards") or 0),
                    authority_total_value_inr=float(aoc_match.get("auth_val") or 0.0),
                    secondary_url=aoc_meta["retrieval_url"],
                    secondary_expected_sha256=aoc_meta["expected_sha256"],
                    secondary_source_format="gepnic_aoc_csv",
                    status_equals="Accepted-AOC",
                )
            )
            continue

        if nb == 1:
            auth = clean_authority_name(buyer_raw, "Assam")
            val = tv or 0.0
            is_de_minimis = val > 0 and val < 500_000.0 and (bwd is None or bwd >= 7)
            short_bonus = 20.0 if (bwd is not None and bwd < 14) else 0.0
            score = (10.0 if is_de_minimis else 65.0) + short_bonus + math.log10(max(val, 1.0)) * 4.0
            cands.append(
                Candidate(
                    shard_id="p7-ocp131-assam",
                    source_format="ocp_jsonl_gz",
                    portal=meta["portal"],
                    portal_key=meta["portal_key"],
                    jurisdiction=meta["jurisdiction"],
                    jurisdiction_level=meta["jurisdiction_level"],
                    authority=auth,
                    authority_key=slugify_key(auth),
                    raw_buyer=buyer_raw,
                    buyer_field="buyer_name",
                    tender_id=tid,
                    ocid=ocid,
                    match_field="ocid",
                    match_value=ocid,
                    row_index=idx,
                    record_url=meta["landing_url"],
                    retrieval_url=meta["retrieval_url"],
                    expected_sha256=sha,
                    official_publisher=meta["official_publisher"],
                    source_landing_page=meta["landing_url"],
                    artifact_title=meta["artifact_title"],
                    year=fy_start,
                    fiscal_year=fy_start_to_label(fy_start),
                    tender_title=neutralize_clean(t.get("title") or tid, max_len=140),
                    category=neutralize_clean(t.get("mainProcurementCategory") or "Works", max_len=80),
                    winner_name=neutralize_clean(aoc_match["w_raw"], max_len=120) if aoc_match else "",
                    normalized_winner_key=aoc_match["w_key"] if aoc_match else "",
                    winner_field="Bidder Name" if aoc_match else "vendor_name",
                    bids_received=1,
                    qualified_bids=1,
                    effective_bids=1,
                    single_bid_mechanism="single_bid_received",
                    bid_window_days=bwd,
                    procurement_method=neutralize_clean(t.get("procurementMethod") or "open", max_len=60),
                    award_value_inr=aoc_match["av"] if aoc_match else None,
                    tender_estimate_inr=tv,
                    financial_quantity_type="awarded_contract_values" if (aoc_match and aoc_match["av"]) else "tender_estimates",
                    winner_total_wins=aoc_match["wins"] if aoc_match else 0,
                    winner_single_bid_wins=1 if aoc_match else 0,
                    winner_cohort_total_inr=aoc_match["coh_inr"] if aoc_match else 0.0,
                    related_record_ids=aoc_match["rel_ids"] if aoc_match else [],
                    cohort="sb",
                    priority_score=score,
                    is_cleared_control=is_de_minimis,
                    secondary_url=aoc_meta["retrieval_url"] if aoc_match else "",
                    secondary_expected_sha256=aoc_meta["expected_sha256"] if aoc_match else "",
                    secondary_source_format="gepnic_aoc_csv" if aoc_match else "",
                    status_equals="Accepted-AOC" if aoc_match else "",
                )
            )
        elif nb >= 3 and tv and tv >= 5_000_000.0 and (idx % 29 == 0):
            # Sample genuine multi-bidder cleared control records across 2016–2022
            auth = clean_authority_name(buyer_raw, "Assam")
            cands.append(
                Candidate(
                    shard_id="p7-ocp131-assam",
                    source_format="ocp_jsonl_gz",
                    portal=meta["portal"],
                    portal_key=meta["portal_key"],
                    jurisdiction=meta["jurisdiction"],
                    jurisdiction_level=meta["jurisdiction_level"],
                    authority=auth,
                    authority_key=slugify_key(auth),
                    raw_buyer=buyer_raw,
                    buyer_field="buyer_name",
                    tender_id=tid,
                    ocid=ocid,
                    match_field="ocid",
                    match_value=ocid,
                    row_index=idx,
                    record_url=meta["landing_url"],
                    retrieval_url=meta["retrieval_url"],
                    expected_sha256=sha,
                    official_publisher=meta["official_publisher"],
                    source_landing_page=meta["landing_url"],
                    artifact_title=meta["artifact_title"],
                    year=fy_start,
                    fiscal_year=fy_start_to_label(fy_start),
                    tender_title=neutralize_clean(t.get("title") or tid, max_len=140),
                    category=neutralize_clean(t.get("mainProcurementCategory") or "Works", max_len=80),
                    winner_name="",
                    normalized_winner_key="",
                    winner_field="vendor_name",
                    bids_received=nb,
                    qualified_bids=nb,
                    effective_bids=nb,
                    single_bid_mechanism="multi_bid",
                    bid_window_days=bwd,
                    procurement_method="open",
                    award_value_inr=None,
                    tender_estimate_inr=tv,
                    financial_quantity_type="tender_estimates",
                    winner_total_wins=0,
                    winner_single_bid_wins=0,
                    winner_cohort_total_inr=0.0,
                    related_record_ids=[],
                    cohort="sb",
                    priority_score=4.0 + math.log10(max(tv, 1.0)),
                    is_cleared_control=True,
                )
            )
    return cands


def load_wb_closed_projects(cache_dir: Path, doc_stats: dict[str, dict[str, Any]]) -> list[Candidate]:
    """Fetch and parse strictly India (`project_ctry_code=IN`) closed/dormant Indian Government (World Bank–assisted) projects."""

    def _fetch_one(item: tuple[str, str, str, str]) -> tuple[str, str, str, str, str, bytes, str] | None:
        pid, jur, jlevel, label = item
        url = (
            f"https://search.worldbank.org/api/v2/procnotices?format=json&project_ctry_code=IN"
            f"&project_id={pid}&notice_type_exact=Contract%20Award&srt=id&order=asc&rows=1000&fl={WB_FIELDS}"
        )
        cname = f"wb_in_{pid}.json"
        raw, sha = fetch_cached_shard(cache_dir, cname, url)
        data = json.loads(raw.decode("utf-8", "replace"))
        total = int(data.get("total") or 0)
        notices = data.get("procnotices") or []
        if total == 0 or total > 1000 or not notices:
            return None
        # Strict India-only provenance assertion (zero non-India leakage!)
        if any((str(n.get("project_ctry_code") or "").strip().upper() != "IN") for n in notices):
            raise RuntimeError(f"Non-India notice detected inside project {pid}!")
        # Safety check: verify no notice newer than 2025-06-01 so closed/dormant shard stays byte-stable
        max_sub = max((str(n.get("submission_date") or "")[:10] for n in notices), default="")
        if max_sub >= "2025-06-01":
            return None
        return (pid, jur, jlevel, label, url, raw, sha)

    fetched: list[tuple[str, str, str, str, str, bytes, str]] = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        for res in ex.map(_fetch_one, WB_CLOSED_PROJECTS):
            if res is not None:
                fetched.append(res)

    cands: list[Candidate] = []
    for pid, jur, jlevel, label, url, raw, sha in fetched:
        shard_id = f"p7-wb-{pid.lower()}"
        notices = json.loads(raw.decode("utf-8", "replace")).get("procnotices") or []
        wb_rows: dict[tuple[str, str], list[tuple[str, float, int | None]]] = defaultdict(list)
        buyer_totals: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
        parsed: list[dict[str, Any]] = []
        years_seen: list[int] = []

        for idx, n in enumerate(notices, start=1):
            if str(n.get("project_ctry_code") or "").strip().upper() != "IN":
                continue
            nt = n.get("notice_text") or ""
            ref = (n.get("bid_reference_no") or "").strip()
            # Drop malformed upstream records where notice_text mismatches bid_reference_no
            if ref and ref not in nt:
                continue
            phtml = parse_wb_notice_html(nt)
            aw = phtml.get("awarded") or []
            if not aw:
                continue
            # Ensure INR or explicit India provenance (never foreign-only notices)
            if phtml.get("currency") and phtml.get("currency") != "INR" and "india" not in nt.lower():
                continue
            w_raw = aw[0][0].strip()
            w_key = normalize_entity_name(w_raw)
            ag_raw = (n.get("agency_name") or label).strip()
            b_key = normalize_entity_name(ag_raw)
            if not w_key or not b_key:
                continue
            mc = (n.get("procurement_method_code") or "").strip().upper()
            ev = phtml.get("evaluated") or []
            rj = phtml.get("rejected") or []
            has_ev = bool(phtml.get("has_ev_section"))
            has_rj = bool(phtml.get("has_rj_section"))

            # Strict WB STEP bidder-count hygiene (forensically verified):
            if mc in ("DIR", "CDS"):
                bids_rec: int | None = 1
                qual: int | None = 1
                eff: int | None = 1
                mech = "single_bid_received"
            elif has_rj and not has_ev and len(rj) > 0:
                bids_rec = 1 + len(rj)
                qual = 1
                eff = 1
                mech = "single_qualified_after_rejection"
            elif has_ev and len(ev) > 0:
                bids_rec = 1 + len(ev) + len(rj)
                qual = 1 + len(ev)
                eff = qual
                mech = "multi_bid" if qual >= 2 else "single_bid_received"
            else:
                # Awarded-only or unparsed-section RFB/RFQ notice: bid count is genuinely unrecorded!
                bids_rec = None
                qual = None
                eff = None
                mech = "unrecorded"

            dt = phtml.get("award_date") or parse_date_opt(n.get("submission_date")) or parse_date_opt(n.get("noticedate"))
            if dt is None:
                continue
            fy_start = date_to_fy_start(dt)
            if not (YEAR_MIN <= fy_start <= YEAR_MAX):
                continue
            years_seen.append(fy_start)
            amt_inr = phtml["amount"] if phtml.get("currency") == "INR" else None
            wb_id = str(n.get("id") or "").strip()
            if not wb_id:
                continue
            tid = ref or wb_id
            wb_rows[(w_key, b_key)].append((tid, amt_inr or 0.0, eff))
            buyer_totals[b_key][0] += 1.0
            buyer_totals[b_key][1] += amt_inr or 0.0
            parsed.append(
                {
                    "idx": idx,
                    "wb_id": wb_id,
                    "tid": tid,
                    "ag_raw": ag_raw,
                    "b_key": b_key,
                    "w_raw": w_raw,
                    "w_key": w_key,
                    "mc": mc,
                    "method_name": str(n.get("procurement_method_name") or mc or "RFB").strip(),
                    "bids_rec": bids_rec,
                    "qual": qual,
                    "eff": eff,
                    "mech": mech,
                    "dt": dt,
                    "fy_start": fy_start,
                    "amt_inr": amt_inr,
                    "title": str(n.get("noticetitle") or phtml.get("scope") or tid).strip(),
                    "cat": str(n.get("procurement_group_desc") or "Works").strip(),
                }
            )

        doc_stats[shard_id] = {
            "source_format": "worldbank_procnotices_json",
            "portal": WB_PORTAL_LABEL,
            "portal_key": "indian-govt-wb-assisted",
            "jurisdiction": jur,
            "jurisdiction_level": jlevel,
            "official_publisher": f"{WB_PORTAL_LABEL} — {label} ({pid})",
            "retrieval_url": url,
            "landing_url": f"https://projects.worldbank.org/en/projects-operations/project-detail/{pid}",
            "bytes": len(raw),
            "sha256": sha,
            "total_rows": len(notices),
            "year_min": min(years_seen) if years_seen else YEAR_MIN,
            "year_max": max(years_seen) if years_seen else YEAR_MAX,
        }

        for p in parsed:
            w_list = wb_rows[(p["w_key"], p["b_key"])]
            wins = len(w_list)
            sb_wins = sum(1 for _, _, e in w_list if e == 1)
            coh_inr = round(sum(v for _, v, _ in w_list), 2)
            rel_ids = list(dict.fromkeys(t for t, _, _ in w_list))[:15]
            is_sb = p["eff"] == 1
            is_rw = wins >= 3
            is_anchor = p["wb_id"] == ANCHOR_WB_ID

            if is_sb and is_rw:
                cohort = "bo"
                score = (1000.0 if is_anchor else 130.0) + sb_wins * 10 + wins * 2 + math.log10(max(p["amt_inr"] or 1.0, 1.0)) * 4
                cleared = False
            elif is_sb:
                cohort = "sb"
                score = 75.0 + math.log10(max(p["amt_inr"] or 1.0, 1.0)) * 5
                cleared = p["mc"] in ("DIR", "CDS") or ((p["amt_inr"] or 0.0) > 0 and (p["amt_inr"] or 0.0) < 500_000.0)
            elif is_rw:
                cohort = "rw"
                score = 52.0 + wins * 3 + math.log10(max(coh_inr, 1.0)) * 2
                cleared = False
            elif p["eff"] is not None and p["eff"] >= 3 and wins <= 2 and p["amt_inr"] and p["amt_inr"] >= 1_000_000.0:
                cohort = "rw"
                score = 7.0 + math.log10(max(p["amt_inr"], 1.0))
                cleared = True
            else:
                continue

            auth = neutralize_clean(p["ag_raw"], max_len=150) or label
            cand_jur, cand_jlevel = jur, jlevel
            if cand_jlevel == "central":
                ag_jur, ag_jlevel = infer_wb_jurisdiction("", p["ag_raw"])
                if ag_jlevel == "state":
                    cand_jur, cand_jlevel = ag_jur, ag_jlevel
            cands.append(
                Candidate(
                    shard_id=shard_id,
                    source_format="worldbank_procnotices_json",
                    portal=WB_PORTAL_LABEL,
                    portal_key="indian-govt-wb-assisted",
                    jurisdiction=cand_jur,
                    jurisdiction_level=cand_jlevel,
                    authority=auth,
                    authority_key=slugify_key(auth),
                    raw_buyer=p["ag_raw"],
                    buyer_field="agency_name",
                    tender_id=p["tid"],
                    ocid="",
                    match_field="id",
                    match_value=p["wb_id"],
                    row_index=p["idx"],
                    record_url=f"https://projects.worldbank.org/en/projects-operations/procurement-detail/{p['wb_id']}",
                    retrieval_url=url,
                    expected_sha256=sha,
                    official_publisher=f"{WB_PORTAL_LABEL} — {pid}",
                    source_landing_page=f"https://projects.worldbank.org/en/projects-operations/procurement-detail/{p['wb_id']}",
                    artifact_title=f"Indian Government (WB-assisted) {pid}",
                    year=p["fy_start"],
                    fiscal_year=fy_start_to_label(p["fy_start"]),
                    tender_title=neutralize_clean(p["title"], max_len=140),
                    category=neutralize_clean(p["cat"], max_len=80),
                    winner_name=neutralize_clean(p["w_raw"], max_len=120),
                    normalized_winner_key=p["w_key"],
                    winner_field="vendor_name",
                    bids_received=p["bids_rec"],
                    qualified_bids=p["qual"],
                    effective_bids=p["eff"],
                    single_bid_mechanism=p["mech"],
                    bid_window_days=None,
                    procurement_method=neutralize_clean(p["method_name"], max_len=60),
                    award_value_inr=p["amt_inr"],
                    tender_estimate_inr=None,
                    financial_quantity_type="awarded_contract_values",
                    winner_total_wins=wins,
                    winner_single_bid_wins=sb_wins,
                    winner_cohort_total_inr=coh_inr,
                    related_record_ids=rel_ids,
                    cohort=cohort,
                    priority_score=score,
                    authority_total_awards=int(buyer_totals[p["b_key"]][0]),
                    authority_total_value_inr=round(buyer_totals[p["b_key"]][1], 2),
                    is_cleared_control=cleared,
                    is_anchor=is_anchor,
                )
            )
    return cands


def load_wb_recent_2025_2026_notices(
    cache_dir: Path, doc_stats: dict[str, dict[str, Any]], *, max_notices: int = 240
) -> list[Candidate]:
    """Fetch permanently immutable India-only (`project_ctry_code=IN`) single-notice JSON records (`id=OP...`, total=1) for 2024–2026."""
    index_cache = cache_dir / "wb_in_recent_2024_2026_index.json"
    if index_cache.exists():
        recent_pool = json.loads(index_cache.read_text(encoding="utf-8"))
    else:
        recent_pool = []
        # Scan 2025–2026 (os=0..2000) + 2024 slice (os=9000, 10000) in 7 polite pages
        for os_val in (0, 500, 1000, 1500, 2000, 9000, 10000):
            u = (
                "https://search.worldbank.org/api/v2/procnotices?format=json&project_ctry_code=IN"
                f"&notice_type_exact=Contract%20Award&srt=submission_date&order=desc&os={os_val}&rows=500&fl={WB_FIELDS}"
            )
            batch = json.loads(http_get_bytes(u).decode("utf-8", "replace")).get("procnotices") or []
            recent_pool.extend(batch)
        index_cache.write_text(json.dumps(recent_pool), encoding="utf-8")

    selected_meta: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for n in recent_pool:
        if str(n.get("project_ctry_code") or "").strip().upper() != "IN":
            continue
        wb_id = str(n.get("id") or "").strip()
        if not wb_id or wb_id in seen_ids:
            continue
        nt = n.get("notice_text") or ""
        ref = (n.get("bid_reference_no") or "").strip()
        if ref and ref not in nt:
            continue
        phtml = parse_wb_notice_html(nt)
        aw = phtml.get("awarded") or []
        if not aw:
            continue
        dt = phtml.get("award_date") or parse_date_opt(n.get("submission_date"))
        if dt is None:
            continue
        fy_start = date_to_fy_start(dt)
        if fy_start not in (2024, 2025, 2026):
            continue
        mc = (n.get("procurement_method_code") or "").strip().upper()
        has_ev = bool(phtml.get("has_ev_section"))
        has_rj = bool(phtml.get("has_rj_section"))
        rj = phtml.get("rejected") or []
        is_sq = has_rj and not has_ev and len(rj) > 0
        is_dir = mc in ("DIR", "CDS")
        if not (is_sq or is_dir):
            continue
        if phtml.get("currency") != "INR" or not phtml.get("amount"):
            continue
        seen_ids.add(wb_id)
        selected_meta.append({"n": n, "phtml": phtml, "dt": dt, "fy_start": fy_start, "is_sq": is_sq, "is_dir": is_dir})

    # Balanced across FY2024-25, FY2025-26, FY2026-27 (up to 80 per FY), prioritizing single_qualified_after_rejection & highest INR
    chosen: list[dict[str, Any]] = []
    per_yr = max(1, max_notices // 3)
    for yr in (2026, 2025, 2024):
        sub = [x for x in selected_meta if x["fy_start"] == yr]
        sub.sort(key=lambda x: (0 if x["is_sq"] else 1, -(x["phtml"]["amount"] or 0.0)))
        chosen.extend(sub[:per_yr])

    def _pin_single(item: dict[str, Any]) -> tuple[dict[str, Any], str, bytes, str] | None:
        wb_id = str(item["n"]["id"]).strip()
        u = f"https://search.worldbank.org/api/v2/procnotices?format=json&project_ctry_code=IN&id={wb_id}&fl={WB_FIELDS}"
        raw, sha = fetch_cached_shard(cache_dir, f"wb_in_notice_{wb_id}.json", u)
        d = json.loads(raw.decode("utf-8", "replace"))
        if str(d.get("total")) != "1" or not d.get("procnotices"):
            return None
        if str(d["procnotices"][0].get("project_ctry_code") or "").strip().upper() != "IN":
            return None
        return (item, u, raw, sha)

    pinned: list[tuple[dict[str, Any], str, bytes, str]] = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        for res in ex.map(_pin_single, chosen):
            if res is not None:
                pinned.append(res)

    cands: list[Candidate] = []
    for item, u, raw, sha in pinned:
        n = item["n"]
        phtml = item["phtml"]
        dt = item["dt"]
        fy_start = item["fy_start"]
        wb_id = str(n["id"]).strip()
        ref = (n.get("bid_reference_no") or wb_id).strip()
        proj_name = str(n.get("project_name") or "").strip()
        ag_raw = str(n.get("agency_name") or proj_name or "Indian Implementing Agency").strip()
        jur, jlevel = infer_wb_jurisdiction(proj_name, ag_raw)
        w_raw = phtml["awarded"][0][0].strip()
        w_key = normalize_entity_name(w_raw)
        if not w_key:
            continue
        rj = phtml.get("rejected") or []
        bids_rec = (1 + len(rj)) if item["is_sq"] else 1
        mech = "single_qualified_after_rejection" if item["is_sq"] else "single_bid_received"
        shard_id = f"p7-wb-notice-{wb_id.lower()}"
        doc_stats[shard_id] = {
            "source_format": "worldbank_procnotices_json",
            "portal": WB_PORTAL_LABEL,
            "portal_key": "indian-govt-wb-assisted",
            "jurisdiction": jur,
            "jurisdiction_level": jlevel,
            "official_publisher": f"{WB_PORTAL_LABEL} — {proj_name or wb_id}",
            "retrieval_url": u,
            "landing_url": f"https://projects.worldbank.org/en/projects-operations/procurement-detail/{wb_id}",
            "bytes": len(raw),
            "sha256": sha,
            "total_rows": 1,
            "year_min": fy_start,
            "year_max": fy_start,
        }
        auth = neutralize_clean(ag_raw, max_len=150)
        cands.append(
            Candidate(
                shard_id=shard_id,
                source_format="worldbank_procnotices_json",
                portal=WB_PORTAL_LABEL,
                portal_key="indian-govt-wb-assisted",
                jurisdiction=jur,
                jurisdiction_level=jlevel,
                authority=auth,
                authority_key=slugify_key(auth),
                raw_buyer=ag_raw,
                buyer_field="agency_name",
                tender_id=ref,
                ocid="",
                match_field="id",
                match_value=wb_id,
                row_index=1,
                record_url=f"https://projects.worldbank.org/en/projects-operations/procurement-detail/{wb_id}",
                retrieval_url=u,
                expected_sha256=sha,
                official_publisher=f"{WB_PORTAL_LABEL} — {wb_id}",
                source_landing_page=f"https://projects.worldbank.org/en/projects-operations/procurement-detail/{wb_id}",
                artifact_title=f"Indian Government (WB-assisted) {wb_id}",
                year=fy_start,
                fiscal_year=fy_start_to_label(fy_start),
                tender_title=neutralize_clean(n.get("noticetitle") or phtml.get("scope") or ref, max_len=140),
                category=neutralize_clean(n.get("procurement_group_desc") or "Works", max_len=80),
                winner_name=neutralize_clean(w_raw, max_len=120),
                normalized_winner_key=w_key,
                winner_field="vendor_name",
                bids_received=bids_rec,
                qualified_bids=1,
                effective_bids=1,
                single_bid_mechanism=mech,
                bid_window_days=None,
                procurement_method=neutralize_clean(n.get("procurement_method_name") or n.get("procurement_method_code") or "RFB", max_len=60),
                award_value_inr=phtml["amount"],
                tender_estimate_inr=None,
                financial_quantity_type="awarded_contract_values",
                winner_total_wins=1,
                winner_single_bid_wins=1,
                winner_cohort_total_inr=round(phtml["amount"] or 0.0, 2),
                related_record_ids=[ref],
                cohort="sb",
                priority_score=95.0 + (20.0 if item["is_sq"] else 0.0) + math.log10(max(phtml["amount"] or 1.0, 1.0)) * 4,
                is_cleared_control=bool(item["is_dir"] and not item["is_sq"]),
            )
        )
    return cands


def build_task_dict(c: Candidate, seq_num: int) -> dict[str, Any]:
    task_id = f"p7-{c.cohort}-{c.year}-{seq_num:05d}"
    audit_mode = {"sb": "single_bidder", "rw": "repeat_winner", "bo": "both"}[c.cohort]
    short_title = neutralize_clean(c.tender_title or c.tender_id, max_len=64)
    tid_clean = neutralize_clean(c.tender_id, max_len=50)
    # Exact title prefix `${authority} · ${fiscal_year} · ...` (no double FYFY)
    title = f"{c.authority} · {c.fiscal_year} · {short_title} ({tid_clean})"
    if len(title) > 170:
        avail = max(18, 170 - len(c.authority) - len(c.fiscal_year) - len(tid_clean) - 10)
        title = f"{c.authority} · {c.fiscal_year} · {short_title[:avail].rstrip()} ({tid_clean})"

    val_str = format_inr_short(c.award_value_inr or c.tender_estimate_inr)
    sha_prefix = c.expected_sha256[:12]
    instructions = f"Verify SHA-256 {sha_prefix}…; run P7 on {tid_clean} ({val_str})."

    cohort_filter: dict[str, Any] = {}
    if c.normalized_winner_key:
        cohort_filter["normalized_winner_key"] = c.normalized_winner_key
    if c.status_equals:
        cohort_filter["status_equals"] = c.status_equals
    if c.related_record_ids and (c.is_anchor or c.cohort == "bo"):
        cohort_filter["related_record_ids"] = c.related_record_ids[:3]

    selector: dict[str, Any] = {
        "match_field": c.match_field,
        "match_value": c.match_value,
        "tender_id": c.tender_id,
        "row_index": c.row_index,
    }
    if c.is_anchor or c.record_url != c.source_landing_page:
        selector["record_url"] = c.record_url

    spec: dict[str, Any] = {
        "case_key": task_id,
        "audit_mode": audit_mode,
        "source_format": c.source_format,
        "portal": c.portal,
        "portal_key": c.portal_key,
        "jurisdiction": c.jurisdiction,
        "jurisdiction_level": c.jurisdiction_level,
        "authority": c.authority,
        "authority_key": c.authority_key,
        "fiscal_year": c.fiscal_year,
        "year": c.year,
        "record_selector": selector,
    }
    if cohort_filter:
        spec["cohort_filter"] = cohort_filter
    if c.is_anchor:
        spec["is_anchor_case"] = True
    if c.secondary_source_format:
        spec["secondary_source_format"] = c.secondary_source_format
    if c.field_map:
        spec["field_map"] = c.field_map

    payload: dict[str, Any] = {
        "retrieval_url": c.retrieval_url,
        "expected_sha256": c.expected_sha256,
        "official_publisher": c.official_publisher,
        "source_landing_page": c.source_landing_page,
        "artifact_title": c.artifact_title,
        "fiscal_year": c.fiscal_year,
        "jurisdiction": c.jurisdiction,
        "context": {
            "as_of_date": AS_OF_DATE,
            "only_one_bid": spec,
        },
    }
    if c.secondary_url and c.secondary_expected_sha256:
        payload["secondary_url"] = c.secondary_url
        payload["secondary_expected_sha256"] = c.secondary_expected_sha256

    return {
        "id": task_id,
        "title": title,
        "instructions": instructions,
        "type": "run_check",
        "dataset_key": c.shard_id,
        "financial_quantity_type": c.financial_quantity_type,
        "methodology_code": METHODOLOGY_CODE,
        "estimated_tokens": 1600 if c.secondary_url else 1250,
        "estimate_basis": "Deterministic P7 scan.",
        "payload": payload,
    }


def is_task_safe(task: dict[str, Any]) -> bool:
    if scan_forbidden_terms(task["title"]) or scan_forbidden_terms(task["instructions"]):
        return False
    spec = task["payload"]["context"]["only_one_bid"]
    for fld in ("authority", "portal", "jurisdiction"):
        if scan_forbidden_terms(str(spec.get(fld) or "")):
            return False
    has_pii, _ = contains_pii(task)
    if has_pii:
        return False
    return True


def select_10000_tasks(all_cands: list[Candidate]) -> list[Candidate]:
    """Deterministically select exactly 10,000 deduplicated tasks across cohorts, years 2016-2026, and portals."""
    # Deduplicate by (portal_key, tender_id.lower()) keeping highest priority_score (anchor always wins!)
    best_by_key: dict[tuple[str, str], Candidate] = {}
    for c in all_cands:
        if not (YEAR_MIN <= c.year <= YEAR_MAX):
            continue
        k = (c.portal_key, c.tender_id.strip().lower())
        prev = best_by_key.get(k)
        if prev is None or c.is_anchor or (not prev.is_anchor and c.priority_score > prev.priority_score):
            best_by_key[k] = c

    # Also drop cross-portal duplicates for same Tender ID within same state (keep richer OCDS/v3 over plain AOC)
    best_by_state_tid: dict[tuple[str, str], Candidate] = {}
    for c in best_by_key.values():
        k2 = (c.jurisdiction, c.tender_id.strip().lower())
        prev = best_by_state_tid.get(k2)
        if prev is None or c.is_anchor or (not prev.is_anchor and c.priority_score > prev.priority_score):
            best_by_state_tid[k2] = c

    # Sort deterministically, checking PII/neutrality lazily on attempted takes (100% of selected tasks verified!)
    clean_pool = list(best_by_state_tid.values())
    clean_pool.sort(key=lambda c: (-1 if c.is_anchor else 0, -c.priority_score, c.year, c.shard_id, c.tender_id))

    selected: list[Candidate] = []
    picked_keys: set[tuple[str, str]] = set()
    rejected_keys: set[tuple[str, str]] = set()

    def _take(c: Candidate) -> bool:
        k = (c.jurisdiction, c.tender_id.strip().lower())
        if k in picked_keys or k in rejected_keys:
            return False
        if not is_task_safe(build_task_dict(c, 1)):
            rejected_keys.add(k)
            return False
        picked_keys.add(k)
        selected.append(c)
        return True

    # 1. Anchor case MUST be Task #1
    anchors = [c for c in clean_pool if c.is_anchor]
    if not anchors:
        raise RuntimeError(f"Anchor case {ANCHOR_WB_ID} not found in clean candidate pool!")
    _take(anchors[0])

    # 2. Target quotas by (cohort, shard_family) for balanced 10-year pan-India coverage
    def _family(c: Candidate) -> str:
        if c.shard_id.startswith("p7-wb-notice-"):
            return "wb_recent"
        if c.shard_id.startswith("p7-wb-"):
            return "wb_closed"
        return c.shard_id

    # Helper to take up to `limit` from a filtered slice with per-winner cap so no single vendor floods all tasks
    winner_taken: Counter[tuple[str, str]] = Counter()
    auth_taken: Counter[str] = Counter()

    def _take_batch(
        pool: list[Candidate],
        limit: int,
        *,
        max_per_winner: int = 18,
        max_per_auth: int = 1200,
    ) -> int:
        taken = 0
        for c in pool:
            if taken >= limit or len(selected) >= TARGET_TASKS:
                break
            wk = (c.authority_key, c.normalized_winner_key) if c.normalized_winner_key else None
            if wk and winner_taken[wk] >= max_per_winner:
                continue
            if auth_taken[c.authority_key] >= max_per_auth:
                continue
            if _take(c):
                taken += 1
                if wk:
                    winner_taken[wk] += 1
                auth_taken[c.authority_key] += 1
        return taken

    # Phase A: Combined `both` (single-bidder + repeat-winner) — target ~1,400–1,600
    for fam, lim in [
        ("wb_closed", 700),
        ("p7-assam-ocds-v3", 450),
        ("p7-ocp131-assam", 450),
        ("p7-ocp77-hp", 100),
    ]:
        sub = [c for c in clean_pool if c.cohort == "bo" and _family(c) == fam]
        _take_batch(sub, lim, max_per_winner=15, max_per_auth=900)

    # Phase B: Single-bidder (`sb`) — target ~3,800 (balanced across all 11 years 2016–2026!)
    # First take all 2024–2026 immutable World Bank individual notices
    _take_batch([c for c in clean_pool if c.cohort == "sb" and _family(c) == "wb_recent"], 240, max_per_winner=8)
    # Closed WB single_qualified & DIR across 2016-2025
    _take_batch([c for c in clean_pool if c.cohort == "sb" and _family(c) == "wb_closed" and not c.is_cleared_control], 900, max_per_winner=12)
    _take_batch([c for c in clean_pool if c.cohort == "sb" and _family(c) == "wb_closed" and c.is_cleared_control], 250, max_per_winner=8)
    # Assam v3 + HP OCP#77 single-bid
    _take_batch([c for c in clean_pool if c.cohort == "sb" and _family(c) == "p7-assam-ocds-v3"], 350, max_per_winner=12)
    _take_batch([c for c in clean_pool if c.cohort == "sb" and _family(c) == "p7-ocp77-hp"], 100, max_per_winner=10)
    # Assam OCP#131 genuine single-bid stratified across 2016–2022 (+ ~250 cleared de-minimis / multi-bid controls!)
    for yr, y_lim in [(2016, 180), (2017, 360), (2018, 420), (2019, 360), (2020, 360), (2021, 360), (2022, 180)]:
        sub_flag = [c for c in clean_pool if c.cohort == "sb" and _family(c) == "p7-ocp131-assam" and c.year == yr and not c.is_cleared_control]
        _take_batch(sub_flag, y_lim, max_per_winner=15, max_per_auth=1400)
    _take_batch([c for c in clean_pool if c.cohort == "sb" and _family(c) == "p7-ocp131-assam" and c.is_cleared_control], 300, max_per_winner=10, max_per_auth=1400)

    # Phase C: Repeat-winner (`rw`) across all 11 years 2016–2026 (including rich 2023–2026 Assam/HP/Odisha + UP + WB!)
    # Reserve explicit year-stratified slices for 2023, 2024, 2025, 2026 first!
    for fam, yr_limits in [
        ("p7-assam-aoc", {2016: 30, 2017: 60, 2018: 90, 2019: 110, 2020: 120, 2021: 120, 2022: 120, 2023: 240, 2024: 260, 2025: 320, 2026: 320}),
        ("p7-odisha-aoc", {2021: 120, 2022: 120, 2023: 180, 2024: 280, 2025: 320, 2026: 280}),
        ("p7-hp-aoc", {2018: 30, 2019: 180, 2020: 160, 2021: 180, 2022: 180, 2023: 240, 2024: 180}),
        ("p7-up-aoc", {2018: 25, 2019: 120, 2020: 280, 2024: 60}),
    ]:
        for yr, lim in sorted(yr_limits.items()):
            sub = [c for c in clean_pool if c.cohort == "rw" and _family(c) == fam and c.year == yr]
            _take_batch(sub, lim, max_per_winner=15, max_per_auth=1200)

    # Add multi-bid repeat-winners & competitive cleared controls from WB closed, Assam v3, HP OCP#77
    _take_batch([c for c in clean_pool if c.cohort == "rw" and _family(c) == "wb_closed" and not c.is_cleared_control], 600, max_per_winner=12)
    _take_batch([c for c in clean_pool if c.cohort == "rw" and _family(c) == "wb_closed" and c.is_cleared_control], 300, max_per_winner=6)
    _take_batch([c for c in clean_pool if c.cohort == "rw" and _family(c) == "p7-assam-ocds-v3" and not c.is_cleared_control], 400, max_per_winner=12)
    _take_batch([c for c in clean_pool if c.cohort == "rw" and _family(c) == "p7-assam-ocds-v3" and c.is_cleared_control], 150, max_per_winner=6)
    _take_batch([c for c in clean_pool if c.cohort == "rw" and _family(c) == "p7-ocp77-hp" and not c.is_cleared_control], 350, max_per_winner=12)
    _take_batch([c for c in clean_pool if c.cohort == "rw" and _family(c) == "p7-ocp77-hp" and c.is_cleared_control], 150, max_per_winner=6)

    # Phase D: Deterministic top-up to exact 10,000 if still short
    if len(selected) < TARGET_TASKS:
        _take_batch(clean_pool, TARGET_TASKS - len(selected), max_per_winner=30, max_per_auth=2500)
    if len(selected) < TARGET_TASKS:
        _take_batch(clean_pool, TARGET_TASKS - len(selected), max_per_winner=200, max_per_auth=10000)

    if len(selected) != TARGET_TASKS:
        raise RuntimeError(f"Expected {TARGET_TASKS} tasks, only assembled {len(selected)}")

    # Keep Task #1 = Anchor, sort remaining 9,999 deterministically (high-signal cohorts first, newest years interspersed cleanly)
    anchor = selected[0]
    rest = selected[1:]
    rest.sort(key=lambda c: ({"bo": 0, "sb": 1, "rw": 2}[c.cohort], -c.year, -c.priority_score, c.shard_id, c.tender_id))
    return [anchor] + rest


def write_project_json_compact(path: Path, header: dict[str, Any], tasks: list[dict[str, Any]]) -> tuple[int, int]:
    """Write pretty JSON header (< 10 KiB before `"tasks":[`) + 1 compact JSON object per line."""
    header_only = dict(header)
    header_only.pop("tasks", None)
    prefix = json.dumps(header_only, indent=2, ensure_ascii=False)
    assert prefix.endswith("\n}") or prefix.endswith("}")
    head_text = prefix[:-1].rstrip() + ',\n  "tasks": [\n'
    header_bytes = len(head_text.encode("utf-8"))
    if header_bytes >= 10 * 1024:
        raise RuntimeError(f"Header before '\"tasks\":[' is {header_bytes:,} bytes (must be < 10,240 bytes)!")

    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(head_text)
        last_idx = len(tasks) - 1
        for i, task in enumerate(tasks):
            line = "    " + json.dumps(task, ensure_ascii=False, separators=(",", ":"))
            f.write(line + (",\n" if i < last_idx else "\n"))
        f.write("  ]\n}\n")

    total_size = path.stat().st_size
    if total_size > 20 * 1024 * 1024:
        raise RuntimeError(f"Generated project JSON is {total_size / 1048576:.2f} MiB (must be <= 20.00 MiB)!")
    return header_bytes, total_size


def write_methodology_md(coverage: dict[str, Any]) -> None:
    anchor = coverage["anchor_case"]
    bc = coverage["by_cohort"]
    text = f"""# Methodology `P7` — Only One Bid

## Simple Hook
> **{HOOK_TEXT}**

## Objective
Audit **10,000 real, SHA-256-pinned Indian government tender and contract-award records** spanning **2016–2026** across Union Ministries and **20+ States** to measure two core public-procurement market-structure patterns:
1. **Single-Bidder Tenders (`sb`):** Public tenders that received only one bid (`single_bid_received`), eliminated competing bidders at technical/pre-qualification evaluation leaving only one qualified firm (`single_qualified_after_rejection`), or proceeded through direct/single-source selection (`DIR`/`CDS`) without recorded PAC/OEM justification.
2. **Repeat-Winner Concentration (`rw`):** Procuring authorities where the same normalized supplier, contractor, or corporate entity repeatedly wins contracts (`>= 3` distinct tenders with elevated share, persistent `>= 5` wins, or `>= 3` consecutive wins).
3. **Combined Single-Bidder + Repeat-Winner (`bo`):** Tenders where both signals coincide — a single-bidder or sole-qualified award going to an entity with repeat wins at the same procuring authority.

## Anchor Case (Task #1: `{anchor["task_id"]}`)
- **Tender / Contract Reference:** `{anchor["tender_id"]}` (World Bank STEP Notice `{anchor["match_value"]}`, Closed Project `P157054`)
- **Procuring Authority:** {anchor["authority"]} ({anchor["jurisdiction"]})
- **Fiscal Year:** `{anchor["fiscal_year"]}`
- **Scope:** {anchor["tender_title"]}
- **Awarded Entity:** {anchor["winner_name"]}
- **Awarded Contract Value:** {format_inr_short(anchor["award_value_inr"])}
- **Observed Competition:** `{anchor["bids_received"]}` bids submitted, `{anchor["technically_qualified_bids"]}` technically qualified (`{anchor["single_bid_mechanism"]}`)
- **Repeat-Winner Pattern:** `{anchor["winner_total_wins"]}` awards (`{anchor["winner_single_bid_wins"]}` single-qualified) totaling {format_inr_short(anchor["winner_cohort_total_inr"])} within the same MPRRDA project cohort
- **Verifiable Public Record:** {anchor["record_url"]}

## Corpus Coverage (`{coverage["total_tasks"]:,}` Tasks · `2016–2026` · Zero CAPTCHA · Tier-1 Pinned)
- **Cohort Split:** Combined (`bo`): `{bc["both"]["tasks"]:,}` · Single Bidder (`sb`): `{bc["single_bidder"]["tasks"]:,}` · Repeat Winner (`rw`): `{bc["repeat_winner"]["tasks"]:,}`
- **Coverage Breadth:** `{coverage["portals_covered"]}` procurement portals/registries · `{coverage["authorities_covered"]:,}` procuring authorities · `{coverage["distinct_repeat_winners"]:,}` distinct repeat-winning entities
- **Verified Tier-1 Sources (all shards `<= 30.03 MiB`, SHA-256 pinned in `documents.csv`):**
  1. **OCP Data Registry Pub #77** (`hptenders.gov.in`, Himachal Pradesh OCDS `full.jsonl.gz`, 2016–2020)
  2. **OCP Data Registry Pub #131** (`assamtenders.gov.in`, Assam State Finance Dept OCDS `full.jsonl.gz`, 2016–2022)
  3. **CivicDataLab Assam OCDS v3** (`assamtenders.gov.in`, `ocds_mapped_data_fiscal_year_2016_2022_v3.json.zip`, 2017–2023)
  4. **CivicDataLab IDS-DRR State AOC Mirrors** (`assamtenders.gov.in` 2016–2026, `hptenders.gov.in` 2016–2025, `tendersodisha.gov.in` 2019–2026, `etender.up.nic.in` 2018–2024 — strictly filtered to `Status == "Accepted-AOC"`)
  5. **Indian Government (World Bank–assisted projects)** (`search.worldbank.org/api/v2/procnotices`, strictly `project_ctry_code=IN` domestic Indian Union & State agency tenders: closed/dormant project shards 2016–2025 + permanently immutable individual `id=OP...` notices 2024–2026)

## Forensic Data-Hygiene Safeguards
- **GePNIC Cover-Count Separation:** Cross-verifying CivicDataLab IDS-DRR CSVs against Assam OCDS v2/v3 and HP OCP #77 established that the CSV column `No of Bids Received` records GePNIC cover counts (`1` or `2` envelopes), not bidder counts. `P7` explicitly disables `bid_count` (`null`) on GePNIC AOC CSV shards and restricts winner attribution strictly to `Status == "Accepted-AOC"` (100.0% verified match across 2,514 cross-checked awards; `Rejected-*` rows are excluded).
- **World Bank STEP Section Hygiene:** Malformed upstream notices where `bid_reference_no` does not appear in `notice_text` are dropped. `Evaluated Bidder(s)` lists non-winning evaluated bidders (`bids_received = 1 + len(evaluated) + len(rejected)`). Notices listing only `Awarded Bidder(s)` on `RFB`/`RFQ` without `Evaluated` or `Rejected` sections preserve `bids_received = None` (never misclassified as 1 bid).
- **Anti-Double-Counting Exposure:** Every finding records `monetary_exposure_inr` strictly equal to the target tender's own value; `winner_cohort_total_inr` is stored separately inside `anomaly_metrics`.
- **Legal & Privacy Charter:** Strict BNS Section 356 non-accusatory framing (`lint_neutrality`), mandatory lawful benign explanations (GFR 2017 Rule 166 PAC/OEM exemptions, genuine L1 competitive repeats, thin regional markets, emergency/retendered procurements), jurisdiction-aware statutory scope (Central GFR 2017 vs State Procurement Rules), and DPDP Act 2023 PII scrubbing (`scrub_pii`).
"""
    (PROJECT_ASSETS_DIR / "METHODOLOGY.md").write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Project P7 (only-one-bid) 10,000-task dataset.")
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE_DIR, help="Local cache folder for downloaded shards")
    args = parser.parse_args()

    cache_dir: Path = args.cache
    cache_dir.mkdir(parents=True, exist_ok=True)
    doc_stats: dict[str, dict[str, Any]] = {}

    print(f"[P7] Loading verified Tier-1 procurement shards (cache={cache_dir})...")
    all_cands: list[Candidate] = []

    c_hp77 = load_ocp77_candidates(cache_dir, doc_stats)
    print(f"  OCP#77 HP: {len(c_hp77):,} candidates")
    all_cands.extend(c_hp77)

    c_v3 = load_assam_v3_candidates(cache_dir, doc_stats)
    print(f"  Assam OCDS v3: {len(c_v3):,} candidates")
    all_cands.extend(c_v3)

    assam_aoc_by_tid: dict[str, dict[str, Any]] = {}
    for sid in ("p7-assam-aoc", "p7-hp-aoc", "p7-odisha-aoc", "p7-up-aoc"):
        cs, by_tid = load_gepnic_aoc_candidates(sid, cache_dir, doc_stats)
        print(f"  {sid}: {len(cs):,} Accepted-AOC repeat-winner candidates")
        all_cands.extend(cs)
        if sid == "p7-assam-aoc":
            assam_aoc_by_tid = by_tid

    c_131 = load_ocp131_candidates(cache_dir, doc_stats, assam_aoc_by_tid)
    print(f"  OCP#131 Assam (+ joined AOC): {len(c_131):,} candidates")
    all_cands.extend(c_131)

    c_wb_closed = load_wb_closed_projects(cache_dir, doc_stats)
    print(f"  World Bank closed/dormant projects: {len(c_wb_closed):,} candidates")
    all_cands.extend(c_wb_closed)

    c_wb_recent = load_wb_recent_2025_2026_notices(cache_dir, doc_stats, max_notices=240)
    print(f"  World Bank 2024–2026 immutable notices: {len(c_wb_recent):,} candidates")
    all_cands.extend(c_wb_recent)

    print(f"[P7] Selecting exactly {TARGET_TASKS:,} deduplicated, PII-clean, neutral tasks across 2016–2026...")
    selected = select_10000_tasks(all_cands)

    tasks: list[dict[str, Any]] = []
    shard_task_counts: Counter[str] = Counter()
    cohort_counts: Counter[str] = Counter()
    year_stats: dict[int, dict[str, int]] = {
        y: {"tasks": 0, "single_bidder_tasks": 0, "repeat_winner_tasks": 0, "both_tasks": 0}
        for y in range(YEAR_MIN, YEAR_MAX + 1)
    }
    jur_stats: dict[str, dict[str, Any]] = {}
    jur_portals: dict[str, Counter[str]] = defaultdict(Counter)
    auth_stats: dict[str, dict[str, Any]] = {}
    auth_portals: dict[str, Counter[str]] = defaultdict(Counter)
    auth_known_bids: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    rw_clusters: dict[tuple[str, str], dict[str, Any]] = {}
    rw_meta: dict[tuple[str, str], dict[str, Any]] = {}
    portals_used: set[str] = set()
    distinct_rw_keys: set[str] = set()
    cleared_controls = 0

    for seq_num, c in enumerate(selected, start=1):
        t = build_task_dict(c, seq_num)
        spec_chk = t["payload"]["context"]["only_one_bid"]
        id_yr = int(t["id"].split("-")[2])
        fy_yr = int(spec_chk["fiscal_year"][2:6])
        if not (YEAR_MIN <= id_yr <= YEAR_MAX and id_yr == fy_yr == spec_chk["year"]):
            raise RuntimeError(
                f"Year/FY invariant violated on {t['id']}: id_yr={id_yr}, fy={spec_chk['fiscal_year']}, year={spec_chk['year']}"
            )
        tasks.append(t)
        shard_task_counts[c.shard_id] += 1
        cname = {"sb": "single_bidder", "rw": "repeat_winner", "bo": "both"}[c.cohort]
        cohort_counts[cname] += 1
        if c.is_cleared_control:
            cleared_controls += 1
        portals_used.add(c.portal)

        ys = year_stats[c.year]
        ys["tasks"] += 1
        ys[f"{cname}_tasks"] += 1

        js = jur_stats.setdefault(
            c.jurisdiction,
            {
                "jurisdiction": c.jurisdiction,
                "portal": c.portal,
                "tasks": 0,
                "single_bidder_tasks": 0,
                "repeat_winner_tasks": 0,
                "both_tasks": 0,
            },
        )
        js["tasks"] += 1
        js[f"{cname}_tasks"] += 1
        jur_portals[c.jurisdiction][c.portal] += 1

        au = auth_stats.setdefault(
            c.authority,
            {
                "authority": c.authority,
                "authority_key": c.authority_key,
                "jurisdiction": c.jurisdiction,
                "portal": c.portal,
                "tasks": 0,
                "single_bidder_tasks": 0,
                "repeat_winner_tasks": 0,
                "both_tasks": 0,
            },
        )
        au["tasks"] += 1
        au[f"{cname}_tasks"] += 1
        auth_portals[c.authority][c.portal] += 1
        if c.effective_bids is not None:
            auth_known_bids[c.authority][0] += 1
            if c.effective_bids == 1:
                auth_known_bids[c.authority][1] += 1

        if c.normalized_winner_key and c.winner_total_wins >= 3:
            distinct_rw_keys.add(c.normalized_winner_key)
            rkey = (c.normalized_winner_key, c.authority)
            entry = rw_clusters.get(rkey)
            val_cr = round(c.winner_cohort_total_inr / 1e7, 2)
            share_cnt = (
                round(min(100.0, c.winner_total_wins / c.authority_total_awards * 100.0), 1)
                if c.authority_total_awards >= 5
                else None
            )
            share_val = (
                round(min(100.0, c.winner_cohort_total_inr / c.authority_total_value_inr * 100.0), 1)
                if c.authority_total_value_inr > 0 and c.winner_cohort_total_inr > 0
                else None
            )
            if entry is None:
                entry = {
                    "winner_name": c.winner_name,
                    "normalized_winner_key": c.normalized_winner_key,
                    "authority": c.authority,
                    "jurisdiction": c.jurisdiction,
                    "win_count": c.winner_total_wins,
                    "single_bid_wins": c.winner_single_bid_wins,
                    "total_value_inr_cr": val_cr,
                    "tasks": 1,
                }
                if share_cnt is not None:
                    entry["share_by_count_pct"] = share_cnt
                if share_val is not None:
                    entry["share_by_value_pct"] = share_val
                rw_clusters[rkey] = entry
                rw_meta[rkey] = {
                    "min_y": c.year,
                    "max_y": c.year,
                    "tids": list(dict.fromkeys([c.tender_id] + list(c.related_record_ids[:6])))[:6],
                }
            else:
                entry["tasks"] += 1
                if c.winner_total_wins > entry["win_count"]:
                    entry["win_count"] = c.winner_total_wins
                    if share_cnt is not None:
                        entry["share_by_count_pct"] = share_cnt
                entry["single_bid_wins"] = max(entry["single_bid_wins"], c.winner_single_bid_wins)
                if val_cr > entry["total_value_inr_cr"]:
                    entry["total_value_inr_cr"] = val_cr
                    if share_val is not None:
                        entry["share_by_value_pct"] = share_val
                rm = rw_meta[rkey]
                rm["min_y"] = min(rm["min_y"], c.year)
                rm["max_y"] = max(rm["max_y"], c.year)
                if c.tender_id and c.tender_id not in rm["tids"] and len(rm["tids"]) < 6:
                    rm["tids"].append(c.tender_id)

    for jur_name, js in jur_stats.items():
        if jur_portals[jur_name]:
            js["portal"] = jur_portals[jur_name].most_common(1)[0][0]
    for auth_name, au in auth_stats.items():
        if auth_portals[auth_name]:
            au["portal"] = auth_portals[auth_name].most_common(1)[0][0]
        known_n, sb_n = auth_known_bids[auth_name]
        if known_n >= 3:
            au["single_bid_rate_pct"] = round(sb_n / known_n * 100.0, 1)
    for rkey, entry in rw_clusters.items():
        rm = rw_meta[rkey]
        entry["year_range"] = f"{rm['min_y']}–{rm['max_y']}" if rm["min_y"] != rm["max_y"] else str(rm["min_y"])
        if rm["tids"]:
            entry["sample_tender_ids"] = rm["tids"][:6]

    # Build top-level project header (strictly < 10 KiB before "tasks":[)
    project_header: dict[str, Any] = {
        "slug": PROJECT_SLUG,
        "project_code": PROJECT_CODE,
        "title": PROJECT_TITLE,
        "summary": (
            "10,000 deterministic checks across 10 years (2016–2026) of domestic Indian government "
            "procurement records — Assam, Himachal Pradesh, Odisha, and Uttar Pradesh state OCDS/AOC "
            "tenders plus Indian Government (World Bank–assisted projects) across Union Ministries and "
            "20+ States — surfacing single-bidder tenders, sole-qualified evaluations, and repeat-winner "
            "patterns while transparently clearing documented exemptions."
        ),
        "jurisdiction": "India (Union Ministries & 20+ States)",
        "authority": "Central Ministries, State Public Works / Irrigation / Power / Health Departments & Indian Implementing Agencies",
        "pillar": "procurement",
        "methodology_code": METHODOLOGY_CODE,
        "priority": 7,
        "status": "active",
        "posted_by": {
            "name": "AI Watchdogs India",
            "handle": "@aiwatchdogsIN",
            "url": "https://aiwatchdogs.in",
        },
        "statutory_anchors": [
            "GFR 2017 Rule 166 (Single Tender Enquiry & Proprietary Article Certificate)",
            "GFR 2017 Rule 173(xx) (Fair competition & transparency in public bidding)",
            "CVC Single-Bid Guidelines (retendering norms & rate reasonableness)",
            "MoF DoE Manual for Procurement of Goods & Works (bid window & evaluation)",
            "Competition Act 2002 Sec. 3(3)(d) (market-structure concentration indicators)",
            "State Public Procurement & Financial Rules (Assam APPR 2020, HP, Odisha, UP, MP)",
            "RTI Act 2005 Sec. 4(1)(b) (proactive disclosure of public contract awards)",
        ],
        "scope": [
            "10,000 real, SHA-256-pinned Indian tender and contract-award records spanning 2016–2026 across Union Ministries and 20+ States",
            "Single-bidder awards (1 bid received, 1 technically qualified after rejections, or unexempted direct selection)",
            "Repeat-winner concentration clusters (same normalized entity winning >= 3 contracts at the same procuring authority)",
            "Cleared competitive controls and documented PAC/OEM/emergency exemptions reported with equal standing",
        ],
        "out_of_scope": [
            "CAPTCHA-gated portals (CPPP ResultOfTenders, SPPP Rajasthan, KPPP Karnataka, GeM bidlists) — zero bypass per IT Act Sec 43",
            "Mislabeled GePNIC cover-count columns (`No of Bids Received` on IDS-DRR CSVs) and non-winning `Rejected-*` bidder rows",
            "Personal phone numbers, personal emails, Aadhaar, or PAN identifiers (scrubbed per DPDP Act 2023)",
        ],
        "sources": [
            {
                "name": "OCP Data Registry #77 — Himachal Pradesh Procurement OCDS (2016–2020)",
                "publisher": "Himachal Pradesh Finance Dept / CivicDataLab / Open Contracting Partnership",
                "url": "https://data.open-contracting.org/en/publication/77",
            },
            {
                "name": "OCP Data Registry #131 — Assam Public Procurement OCDS (2016–2022)",
                "publisher": "Assam State Government Finance Department / Open Contracting Partnership",
                "url": "https://data.open-contracting.org/en/publication/131",
            },
            {
                "name": "Assam Public Procurement OCDS v3 Release Package (2017–2023)",
                "publisher": "CivicDataLab Open Contracting India",
                "url": "https://github.com/CivicDataLab/assam-tenders-data",
            },
            {
                "name": "CivicDataLab IDS-DRR Multi-State e-Procurement AOC Datasets (Assam, HP, Odisha, UP; 2016–2026)",
                "publisher": "CivicDataLab (mirroring State GePNIC portals)",
                "url": "https://drr.open-contracting.in/en",
            },
            {
                "name": "Indian Government (World Bank–assisted projects) — Official Contract Awards API (2016–2026, project_ctry_code=IN)",
                "publisher": "Indian Union & State Implementing Agencies (published via World Bank STEP)",
                "url": "https://projects.worldbank.org/en/projects-operations/procurement",
            },
        ],
    }

    # Final safety sweep over header + all 10,000 tasks before writing
    for fld in ("title", "summary", "authority"):
        terms = scan_forbidden_terms(str(project_header[fld]))
        if terms:
            raise RuntimeError(f"Header field {fld} failed neutrality check: {terms}")
    for key in ("scope", "out_of_scope", "statutory_anchors"):
        for item in project_header[key]:
            if scan_forbidden_terms(item):
                raise RuntimeError(f"Header {key} item failed neutrality: {item}")
    pii_header, tags = contains_pii(project_header)
    if pii_header:
        raise RuntimeError(f"Header contains PII: {tags}")

    PROJECT_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    header_bytes, total_bytes = write_project_json_compact(PROJECT_JSON_PATH, project_header, tasks)

    # Write documents.csv (one row per used pinned shard/notice)
    used_shards = sorted(shard_task_counts.keys())
    doc_rows: list[dict[str, Any]] = []
    for sid in used_shards:
        info = doc_stats[sid]
        doc_rows.append(
            {
                "shard_id": sid,
                "source_format": info["source_format"],
                "portal": info["portal"],
                "jurisdiction": info["jurisdiction"],
                "official_publisher": info["official_publisher"],
                "year_min": info["year_min"],
                "year_max": info["year_max"],
                "total_rows": info["total_rows"],
                "tasks_assigned": shard_task_counts[sid],
                "bytes": info["bytes"],
                "sha256": info["sha256"],
                "retrieval_url": info["retrieval_url"],
                "landing_url": info["landing_url"],
            }
        )
    with (PROJECT_ASSETS_DIR / "documents.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(doc_rows[0].keys()))
        w.writeheader()
        w.writerows(doc_rows)

    # Build coverage.json strictly aligned with Track A `_only_one_bid_summary()` (#4 Section D)
    anchor_c = selected[0]
    anchor_task_id = tasks[0]["id"]
    top_authorities = sorted(
        auth_stats.values(),
        key=lambda x: (-x["tasks"], x["authority"].lower()),
    )[:100]
    top_repeat_winners = sorted(
        rw_clusters.values(),
        key=lambda x: (-x["single_bid_wins"], -x["win_count"], -x["total_value_inr_cr"], x["winner_name"].lower()),
    )[:50]
    jurisdictions_list = sorted(
        jur_stats.values(),
        key=lambda x: (-x["tasks"], x["jurisdiction"].lower()),
    )
    years_list = [
        {
            "year": str(y),
            "fiscal_year": f"FY{y}-{(y + 1) % 100:02d}",
            "tasks": year_stats[y]["tasks"],
            "single_bidder_tasks": year_stats[y]["single_bidder_tasks"],
            "repeat_winner_tasks": year_stats[y]["repeat_winner_tasks"],
            "both_tasks": year_stats[y]["both_tasks"],
        }
        for y in range(YEAR_MIN, YEAR_MAX + 1)
    ]

    coverage: dict[str, Any] = {
        "hook": HOOK_TEXT,
        "generated_on": AS_OF_DATE,
        "as_of_date": AS_OF_DATE,
        "total_tasks": len(tasks),
        "year_range": [str(YEAR_MIN), str(YEAR_MAX)],
        "portals_covered": len(portals_used),
        "authorities_covered": len(auth_stats),
        "distinct_repeat_winners": len(distinct_rw_keys),
        "cleared_control_tasks_embedded": cleared_controls,
        "by_cohort": {
            "single_bidder": {"tasks": cohort_counts["single_bidder"]},
            "repeat_winner": {"tasks": cohort_counts["repeat_winner"]},
            "both": {"tasks": cohort_counts["both"]},
        },
        "years": years_list,
        "jurisdictions": jurisdictions_list,
        "top_authorities": top_authorities,
        "top_repeat_winners": top_repeat_winners,
        "anchor_case": {
            "task_id": anchor_task_id,
            "case_key": anchor_task_id,
            "tender_id": anchor_c.tender_id,
            "match_value": anchor_c.match_value,
            "ocid": anchor_c.ocid,
            "authority": anchor_c.authority,
            "authority_key": anchor_c.authority_key,
            "jurisdiction": anchor_c.jurisdiction,
            "portal": anchor_c.portal,
            "fiscal_year": anchor_c.fiscal_year,
            "year": anchor_c.year,
            "tender_title": anchor_c.tender_title,
            "winner_name": anchor_c.winner_name,
            "normalized_winner_key": anchor_c.normalized_winner_key,
            "bids_received": anchor_c.bids_received,
            "technically_qualified_bids": anchor_c.qualified_bids,
            "effective_bids": anchor_c.effective_bids,
            "single_bid_mechanism": anchor_c.single_bid_mechanism,
            "winner_total_wins": anchor_c.winner_total_wins,
            "winner_single_bid_wins": anchor_c.winner_single_bid_wins,
            "award_value_inr": anchor_c.award_value_inr,
            "winner_cohort_total_inr": anchor_c.winner_cohort_total_inr,
            "signal_category": "single_bidder_repeat_winner",
            "record_url": anchor_c.record_url,
            "source_url": anchor_c.retrieval_url,
            "finding_id": None,
        },
        "sources": [
            {
                "shard_id": r["shard_id"],
                "source_format": r["source_format"],
                "portal": r["portal"],
                "jurisdiction": r["jurisdiction"],
                "year_min": r["year_min"],
                "year_max": r["year_max"],
                "tasks_assigned": r["tasks_assigned"],
                "bytes": r["bytes"],
                "sha256": r["sha256"],
                "retrieval_url": r["retrieval_url"],
                "landing_url": r["landing_url"],
            }
            for r in doc_rows
        ],
    }

    # Verify coverage.json itself has zero PII and zero forbidden terms
    cov_pii, cov_tags = contains_pii(coverage)
    if cov_pii:
        raise RuntimeError(f"coverage.json contains PII: {cov_tags}")

    (PROJECT_ASSETS_DIR / "coverage.json").write_text(
        json.dumps(coverage, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    write_methodology_md(coverage)

    print(f"\n[P7] SUCCESS! Wrote {PROJECT_JSON_PATH}")
    print(f"  Header bytes before '\"tasks\":[': {header_bytes:,} (< 10,240)")
    print(f"  Total JSON size: {total_bytes / 1048576:.2f} MiB (<= 20 MiB)")
    print(f"  Tasks: {len(tasks):,} | Cohorts: {dict(cohort_counts)} | Cleared controls: {cleared_controls:,}")
    print(f"  Portals: {len(portals_used)} | Authorities: {len(auth_stats):,} | Repeat winners: {len(distinct_rw_keys):,} | Shards: {len(doc_rows)}")
    print("  By year:", [(y["year"], y["tasks"], f"sb={y['single_bidder_tasks']}/rw={y['repeat_winner_tasks']}/bo={y['both_tasks']}") for y in years_list])
    print(f"  Anchor Case: {anchor_task_id} -> {anchor_c.tender_id} ({anchor_c.authority}, {anchor_c.winner_name}, {format_inr_short(anchor_c.award_value_inr)})")


if __name__ == "__main__":
    main()
