#!/usr/bin/env python3
"""Build Project P8 (`sovereign-lies`, methodology `P8`, 1,000 tasks) from verified Union & State/UT records.

Hook: "The government made a promise when it launched the scheme. Did it keep it?"

Assembles exactly 1,000 distinct, SHA-256-pinned, byte-stable Indian government scheme audit tasks
spanning the Union Government (500 tasks) and ALL 28 States & 8 Union Territories (500 tasks),
anchored by Task #1 (`p8-union-sgb-anchor-0001`) documenting the Sovereign Gold Bond (SGB) Scheme
launch statutory capital-gains tax exemption (Finance Bill 2016 Clause 28 / Sec 47(viic)) versus
the Union Budget 2026-27 / Finance Bill 2026 Clause 35 primary-subscriber-only walk-back
(amending Sec 70(1)(x) effective 1 April 2026), followed by all 67 SGB tranches and broad Union &
State/UT scheme walk-backs plus verified Cleared (grandfathered/honored) benchmarks.

Modes:
  1. Catalog build mode (when `--union-catalog` and `--state-catalog` exist):
     Normalizes, refines CAG/statutory records, deduplicates, selects exactly 500 Union + 500
     State/UT = 1,000 tasks, live-verifies all unique HTTPS URLs (double-fetch SHA-256
     byte-stability), dry-runs all 1,000 tasks through `load_dataset()` + `run_engine("P8", ...)`,
     enforces strict data-quality & token-hygiene gates, validates with `validate_project()`, and
     writes all project artifacts.
  2. Standalone reproducible rebuild/verify mode (default on fresh checkout or `--from-existing`):
     Loads the 1,000 pinned task specifications from `projects/sovereign-lies.json`, live-verifies
     all unique URLs & SHA-256 digests, re-runs `load_dataset()` + `run_engine("P8", ...)` across
     all 1,000 tasks, enforces all quality gates + `validate_project()`, and regenerates
     `coverage.json`, `documents.csv`, `projects/sovereign-lies/METHODOLOGY.md`, and
     `projects/sovereign-lies.json`.

Outputs:
  - projects/sovereign-lies.json (exactly 1,000 tasks, compact 1-line-per-task JSON)
  - projects/sovereign-lies/coverage.json
  - projects/sovereign-lies/documents.csv
  - projects/sovereign-lies/METHODOLOGY.md
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import logging
import math
import multiprocessing
import re
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlsplit, urlunsplit

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.append(str(REPO_ROOT / "src"))

from aiwatchdogs.core.safety import (  # noqa: E402
    contains_pii,
    sanitize_csv_cell,
    scan_forbidden_terms,
    scrub_pii,
    validate_safe_http_url,
)

logging.getLogger("pypdf").setLevel(logging.ERROR)

PROJECT_SLUG = "sovereign-lies"
PROJECT_CODE = "P8"
METHODOLOGY_CODE = "P8"
TARGET_TASKS = 1000
TARGET_UNION_TASKS = 500
TARGET_STATE_UT_TASKS = 500
AS_OF_DATE = "2026-11-27"
HOOK_TEXT = "The government made a promise when it launched the scheme. Did it keep it?"
PROJECT_TITLE = "Sovereign Lies: Did Indian governments keep their scheme promises?"
USER_AGENT = "AIWatchdogs-India-OpenAudit/0.1 (aiwatchdogs.in; lawful-public-audit)"

DEFAULT_CACHE_DIR = Path.home() / ".cache" / "aiwatchdogs" / PROJECT_SLUG
DEFAULT_UNION_CATALOG = Path(
    "/home/piyushbodaa/.gemini/antigravity-cli/brain/6c913b9a-f39f-4014-bdfc-59b770e67f83/scratch/union_sources.json"
)
DEFAULT_STATE_CATALOG = Path(
    "/home/piyushbodaa/.gemini/antigravity-cli/brain/6c913b9a-f39f-4014-bdfc-59b770e67f83/scratch/state_sources.json"
)
PROJECTS_DIR = REPO_ROOT / "projects"
PROJECT_JSON_PATH = PROJECTS_DIR / f"{PROJECT_SLUG}.json"
PROJECT_ASSETS_DIR = PROJECTS_DIR / PROJECT_SLUG

P8_TASK_ID_RE = re.compile(r"^p8-(union|state|ut)-[a-z0-9][a-z0-9-]{1,40}-\d{4}$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CTRL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]+")
_PUA_AND_GLYPHS_RE = re.compile(
    r"[\ue000-\uf8ff\ufffd➢•●▪▫►▻▸▹○■□◆◇❖✓✔✗✘¾\u0100-\u02af\u2020\u2021\u2030\u2039\u203a\u02c6\u02dc\u0160\u0161\u0192\u0201]+"
)
_MULTI_SPACE_RE = re.compile(r"\s+")
_DASH_VARIANTS_RE = re.compile(r"[\u2010-\u2015\u2212]")
_QUOTE_SINGLE_RE = re.compile(r"[\u2018\u2019\u201a\u201b]")
_QUOTE_DOUBLE_RE = re.compile(r"[\u201c\u201d\u201e\u201f]")
_HTML_ENTITY_IN_URL_RE = re.compile(r"&(?:#\d+|#x[0-9a-fA-F]+|amp|apos|quot|lt|gt|nbsp|ndash|mdash);")
_PINNED_GITHUB_RAW_PATH_RE = re.compile(r"^/[^/]+/[^/]+/[0-9a-f]{40}/.+$")

_ACB_RE = re.compile(r"\banti[\s-]+corruption\b", re.IGNORECASE)
_PAGE_MARKER_RE = re.compile(r"\[?\s*PAGE\s+\d+\s*\]?|\[PAGE\b", re.IGNORECASE)
_NEUTRAL_REPLACE_RE = re.compile(
    r"(?<![a-z])(?:scam(?:s|med|mer|mers)?|corrupt(?:ed|ing|ly|ion)?|brib(?:e|es|ed|ing|ery)|"
    r"fraud(?:s|ulent|ulently|ster|sters)?|kickback(?:s)?|guilty|stolen|rigged|"
    r"crook(?:s|ed)?|loot(?:s|ed|ing|er|ers)?|embezzl(?:e|es|ed|ing|ement))(?![a-z])",
    re.IGNORECASE,
)

# Intro-only CAG PDFs (Chapter 1 Introduction without audit findings) excluded from catalog selection
_INTRO_ONLY_CAG_REPORT_IDS: frozenset[str] = frozenset({"117135", "116304"})

_NUMERIC_ONLY_RE = re.compile(r"^[\d,.\s%₹Rs/-]+$", re.IGNORECASE)
_GENERIC_BAN_TOKENS: frozenset[str] = frozenset(
    {
        "state",
        "states",
        "india",
        "indian",
        "scheme",
        "schemes",
        "report",
        "reports",
        "government",
        "ministry",
        "department",
        "total",
        "crore",
        "crores",
        "lakh",
        "lakhs",
        "year",
        "years",
        "page",
        "table",
        "chart",
        "figure",
        "annexure",
        "section",
        "number",
        "details",
        "central",
        "national",
        "public",
        "data",
        "fund",
        "funds",
        "grant",
        "grants",
        "percent",
        "per cent",
        "rupees",
        "amount",
        "status",
        "period",
        "audit",
        "budget",
        "sir",
        "madam",
        "answer",
        "question",
        "statement",
        "laid",
        "house",
        "dated",
        "regarding",
        "lok sabha",
        "rajya sabha",
        "unstarred question",
        "starred question",
        "parliamentary answer",
        "executive summary",
    }
)
_BANNED_BARE_NUMBERS: frozenset[str] = frozenset(
    {
        "200",
        "300",
        "10,000",
        "15%",
        "20%",
        "6,000",
        "12.5%",
        "38%",
        "0.03",
        "1.5%",
        "2018",
        "2019",
        "2020",
        "2021",
        "2022",
        "2023",
        "2024",
        "2025",
        "2026",
    }
)
# Short acronyms that must ONLY appear when genuinely relevant to the task's scheme context
_CONTEXT_GATED_ACRONYMS: dict[str, tuple[str, ...]] = {
    "eps": ("eps", "pension", "epfo", "epf", "retirement"),
    "pli": ("pli", "production linked", "production-linked"),
    "rte": ("rte", "right to education", "school", "samagra shiksha", "education"),
    "epf": ("epf", "epfo", "provident fund", "pension"),
    "ops": ("ops", "old pension", "pension"),
    "nps": ("nps", "national pension", "pension"),
    "ups": ("ups", "unified pension", "pension"),
    "fame": ("fame", "electric", "ev", "e-drive", "mobility"),
    "asha": ("asha", "health", "nhm", "anganwadi"),
    "orop": ("orop", "one rank one pension", "defence", "ex-servicemen"),
    "sgb": ("sgb", "sovereign gold bond", "gold bond"),
    "ulip": ("ulip", "unit linked", "10(10d)"),
    "mld": ("mld", "market linked", "50aa"),
}

# CAG Audit extraction filters
_CAG_MACRO_RE = re.compile(
    r"\b(?:total expenditure|revenue expenditure|capital expenditure|capital outlay|"
    r"committed expenditure|fiscal deficit|revenue deficit|primary deficit|"
    r"revenue receipts|tax receipts|non-tax receipts|total receipts|total budget|"
    r"gross state domestic product|gsdp|overall budgetary|state'?s own tax|"
    r"loans and advances|consolidated fund of the state|budgetary profile|"
    r"fiscal parameters|fiscal indicators|expenditure of the state)\b",
    re.IGNORECASE,
)
_CAG_PRAISE_RE = re.compile(
    r"\b(?:best practices|commendable|significant reduction in misclassification|"
    r"satisfactory compliance|appreciable|noteworthy achievement|positive trend)\b",
    re.IGNORECASE,
)
_CAG_CHART_TABLE_RE = re.compile(
    r"(?:^|\b)(?:Chart|Table|Figure|Statement|Graph|Annexure|Appendix)\s+\d|"
    r"0%\s*10%\s*20%|Table of Contents|^\s*Contents\b|^\s*Preface\b|^\s*Abbreviations\b|"
    r"^\s*Overview\b|^\s*Executive Summary\b|^\s*This Report of the Comptroller|"
    r"\.{5,}",
    re.IGNORECASE,
)
_CAG_FINDING_RE = re.compile(
    r"\b(?:shortfall|unspent|unutilised|unutilized|non-utilis\w*|non-utiliz\w*|surrender(?:ed|ing)?|"
    r"lapsed|savings of|excessive savings|persistent savings|arrears|unpaid|not paid|"
    r"delayed|delay in|non-release|short[\s-]release|not released|pending|"
    r"not transferred|lying idle|idle funds|unadjusted|utilisation certificates?|"
    r"utilization certificates?|non-submission|irregular|diversion|diverted|"
    r"failed to achieve|not achieved|did not achieve|deficien\w*|inadequate|shortage of|"
    r"not implemented|non-implementation|incomplete|abandoned|stalled|"
    r"not provided|deprived|excluded|denied|backlog|not disbursed|undisbursed|"
    r"losses? of|incurred loss|accumulated loss|overpayment|excess payment|"
    r"non-recovery|unrealised|uncollected|outstanding|under[\s-]?assessment|short[\s-]?levy|"
    r"not adhered)\b",
    re.IGNORECASE,
)
_SHORTFALL_EXP_CTX_RE = re.compile(
    r"\b(?:shortfall|unspent|unutilised|unutilized|surrender(?:ed)?|lapsed|savings|"
    r"arrears|unpaid|not paid|pending|not transferred|lying idle|unadjusted|"
    r"utilisation certificates?|utilization certificates?|non-submission|irregular|"
    r"diverted|diversion|short[\s-]release|not released|non-release|undisbursed|"
    r"not disbursed|loss(?:es)?|overpayment|excess payment|non-recovery|unrealised|"
    r"outstanding|under[\s-]?assessment|short[\s-]?levy|uncollected)\b",
    re.IGNORECASE,
)
_NON_SHORTFALL_PREFIX_RE = re.compile(
    r"(?:out of|against|budget provision|total provision|provision of|total expenditure|"
    r"revenue expenditure|committed expenditure|revenue receipts|total budget|gsdp|"
    r"total grants(?:\s*/\s*appropriations)?|appropriations|grant(?:s)? of|allocation of|"
    r"receipts of|target of|turnover of|outlay of|sanctioned|received(?:\s+interest)?\s+of|"
    r"allotted\s+for)\s*\(?\s*$",
    re.IGNORECASE,
)

# Domain phrase cues for mining substantive verbatim walkback/status phrases from parliamentary/budget PDFs
_MECHANISM_CUE_WORDS: dict[str, tuple[str, ...]] = {
    "retroactive_tax_reversal": (
        "capital gains",
        "exemption",
        "indexation",
        "short-term",
        "with effect from",
        "section",
        "proviso",
        "amended",
        "withdrawn",
        "taxable",
    ),
    "midstream_benefit_cut": (
        "subsidy",
        "revised",
        "reduced",
        "reduction",
        "incentive",
        "ceiling",
        "cap",
        "revised estimates",
        "budget estimates",
        "outlay",
        "allocation",
        "expenditure",
        "cylinder",
        "tariff",
        "rate",
        "per kwh",
        "electric",
        "beneficiaries",
    ),
    "premature_discontinuation": (
        "discontinued",
        "closed",
        "suspended",
        "stopped",
        "terminated",
        "withdrawn",
        "not implemented",
        "no fresh",
        "concession",
        "scholarship",
        "tranche",
        "opted out",
        "expiry",
        "sunset",
        "phase-ii",
    ),
    "eligibility_narrowing": (
        "eligible",
        "ineligible",
        "excluded",
        "exclusion",
        "ceiling",
        "cap",
        "income",
        "taxpayer",
        "deleted",
        "aadhaar",
        "kyc",
        "verification",
        "beneficiaries",
        "declined",
        "proviso",
        "criteria",
    ),
    "lockin_or_exit_restriction": (
        "lock-in",
        "premature",
        "redemption",
        "withdrawal",
        "exit",
        "corpus",
        "transfer",
        "maturity",
        "pension",
        "statutory",
        "retention",
    ),
    "arrears_or_unfunded_promise": (
        "arrears",
        "pending",
        "unpaid",
        "delay",
        "delayed",
        "shortfall",
        "unspent",
        "backlog",
        "liabilities",
        "dues",
        "compensation",
        "unutilised",
        "vacancies",
        "claims",
        "release",
        "outstanding",
        "surrendered",
    ),
    "grandfathered_or_honored": (
        "cleared",
        "honoured",
        "honored",
        "grandfathered",
        "disbursed",
        "released",
        "achieved",
        "completed",
        "installed",
        "beneficiaries",
        "continued",
        "existing",
        "enhanced",
        "exempt",
        "paid",
    ),
}

# Verified statutory overrides for the 4 Union Budget Finance Bill / Memo records where
# original launch year preceded the Finance Bill amendment date (eliminating promise_date == walkback_date)
_STATUTORY_UNION_OVERRIDES: dict[str, dict[str, Any]] = {
    "life-insurance-sec10-10d-exemption": {
        "promise_date": "2003-04-01",
        "government_era_or_launch_year": "2003 (Finance Act 2003 / Sec 10(10D))",
        "promise_evidence_tokens": [
            "Clause (10D) of section 10",
            "income-tax exemption on the sum received under a life insurance policy",
            "bonus on such policy",
        ],
        "walkback_evidence_tokens": [
            "five lakh rupees",
            "1st day of April, 2023",
            "other than a unit linked insurance policy",
        ],
    },
    "section-50aa-mld-debt-mf": {
        "promise_date": "2014-07-10",
        "government_era_or_launch_year": "2014 (Finance (No. 2) Act 2014 LTCG Regime)",
        "promise_evidence_tokens": [
            "Market Linked Debentures",
            "long-term capital gain at the rate of 10% without indexation",
            "listed securities",
        ],
        "walkback_evidence_tokens": [
            "Insertion of new section 50AA",
            "short-term capital",
            "1st day of April, 2024",
        ],
    },
    "sec54-54f-reinvestment-cap": {
        "promise_date": "1982-04-01",
        "government_era_or_launch_year": "1982 (Finance Act 1982 / Sec 54 & 54F)",
        "promise_evidence_tokens": [
            "existing provisions of section 54 and section 54F",
            "allows deduction on the Capital gains",
            "long-term capital asset",
        ],
        "walkback_evidence_tokens": [
            "Amendment of section 54F",
            "exceeds ten crore rupees",
            "1st day of April, 2024",
        ],
    },
    "ulip-sec10-10d-exemption": {
        "promise_date": "2003-04-01",
        "government_era_or_launch_year": "2003 (Finance Act 2003 / Sec 10(10D) ULIP)",
        "promise_evidence_tokens": [
            "Clause (10D) of section 10",
            "exemption for the sum received under a life insurance policy",
            "unit linked insurance policy (ULIP)",
        ],
        "walkback_evidence_tokens": [
            "two lakh and fifty thousand rupees",
            "1st day of February, 2021",
            "fourth and fifth proviso",
        ],
    },
}

SCHEME_CATEGORY_LABELS: dict[str, str] = {
    "sovereign_bonds_small_savings": "Sovereign Bonds & Small Savings",
    "pensions_retirement": "Pensions & Retirement",
    "tax_exempt_instruments": "Tax-Exempt Instruments",
    "cash_transfers_guarantees": "Cash Transfers & Guarantees",
    "ev_housing_farm_subsidies": "EV, Housing & Farm Subsidies",
    "scholarships_education": "Scholarships & Education",
    "industrial_export_incentives": "Industrial & Export Incentives",
    "welfare_health_public_services": "Welfare, Health & Public Services",
}
VALID_SCHEME_CATEGORIES = frozenset(SCHEME_CATEGORY_LABELS.keys())

SIGNAL_CATEGORIES: tuple[str, ...] = (
    "retroactive_tax_reversal",
    "midstream_benefit_cut",
    "premature_discontinuation",
    "eligibility_narrowing",
    "lockin_or_exit_restriction",
    "arrears_or_unfunded_promise",
)
VALID_WALKBACK_MECHANISMS = frozenset(SIGNAL_CATEGORIES) | {"grandfathered_or_honored"}
VALID_RETROACTIVE_IMPACTS = frozenset(
    {
        "retroactive_existing_holders",
        "mid_stream_active_scheme",
        "prospective_discontinuation",
        "eligibility_narrowing",
        "unfulfilled_arrears",
        "grandfathered_or_honored",
    }
)
VALID_JURISDICTION_LEVELS = frozenset({"union", "state", "ut"})
VALID_SOURCE_FORMATS = frozenset({"pdf", "html", "json", "csv", "txt"})
VALID_SECONDARY_ROLES = frozenset({"", "walkback_document", "promise_document", "corroborating_record"})

OFFICIAL_PROJECT_SOURCES: list[dict[str, str]] = [
    {
        "name": "Union Budget Archives (Finance Bills, Explanatory Memoranda & Budget Speeches, 2015–2026)",
        "publisher": "Ministry of Finance, Government of India",
        "url": "https://www.indiabudget.gov.in/",
    },
    {
        "name": "Digital Sansad — Lok Sabha Questions & Official Answer PDFs (16th–18th Lok Sabha)",
        "publisher": "Parliament of India — Lok Sabha Secretariat",
        "url": "https://sansad.in/ls/questions/questions-and-answers",
    },
    {
        "name": "Digital Sansad — Rajya Sabha Questions, Answers & Debates Archive",
        "publisher": "Parliament of India — Rajya Sabha Secretariat",
        "url": "https://sansad.in/rs/questions/questions-and-answers",
    },
    {
        "name": "Comptroller and Auditor General (CAG) of India — Union & State Audit Reports",
        "publisher": "Comptroller and Auditor General of India",
        "url": "https://cag.gov.in/en/audit-report",
    },
    {
        "name": "National Stock Exchange of India — Sovereign Gold Bond Listing Circular Archives (CML)",
        "publisher": "National Stock Exchange of India (SGB Secondary Market Listing Circulars)",
        "url": "https://www.nseindia.com/resources/exchange-communication-circulars",
    },
    {
        "name": "Press Information Bureau — Static Official Document & Factsheet Archive",
        "publisher": "Press Information Bureau, Ministry of Information & Broadcasting",
        "url": "https://pib.gov.in/",
    },
    {
        "name": "PRS Legislative Research — State Budget Analyses (FY 2024-25 to FY 2026-27)",
        "publisher": "PRS Legislative Research (synthesizing State Budget & Finance Documents)",
        "url": "https://prsindia.org/budgets/states",
    },
    {
        "name": "Government of NCT of Delhi — Transport & Women and Child Development Official Notifications",
        "publisher": "Government of National Capital Territory of Delhi",
        "url": "https://transport.delhi.gov.in/",
    },
    {
        "name": "Government of Andhra Pradesh — Finance Department Budget & CBRO Guidelines",
        "publisher": "Finance Department, Government of Andhra Pradesh",
        "url": "https://apfinance.gov.in/",
    },
]


def norm_search(text: str) -> str:
    norm = unicodedata.normalize("NFKC", text or "")
    norm = _DASH_VARIANTS_RE.sub("-", norm)
    norm = _QUOTE_SINGLE_RE.sub("'", norm)
    norm = _QUOTE_DOUBLE_RE.sub('"', norm)
    norm = _CTRL_CHARS_RE.sub(" ", norm)
    return _MULTI_SPACE_RE.sub(" ", norm).strip().lower()


def _trim_word_boundary(text: str, max_len: int) -> str:
    """Truncate `text` to <= `max_len` strictly on a word boundary (never mid-word)."""
    if max_len <= 0 or len(text) <= max_len:
        return text
    limit = max(1, max_len - 1)
    cut = text[:limit]
    # If cutting split an alphanumeric word in half, roll back to the previous whitespace
    if limit < len(text) and text[limit].isalnum() and cut and cut[-1].isalnum():
        sp = cut.rfind(" ")
        if sp >= max(8, limit // 3):
            cut = cut[:sp]
    cut = cut.rstrip(" ,;:-/([—–")
    # Strip trailing dangling prepositions/conjunctions when truncating cleanly
    cut2 = re.sub(r"\s+(?:of|for|in|on|and|the|to|by|with|under|from|including|regarding|a|an)$", "", cut, flags=re.IGNORECASE).rstrip(" ,;:-/—–")
    if len(cut2) >= 12:
        cut = cut2
    # Close dangling unmatched parentheses if needed
    if cut.count("(") > cut.count(")"):
        lp = cut.rfind("(")
        if lp >= max(8, len(cut) // 2):
            cut = cut[:lp].rstrip(" ,;:-/—–")
    return cut + "…"


def neutralize_clean(text: Any, *, max_len: int = 0) -> str:
    if text is None:
        return ""
    s = _CTRL_CHARS_RE.sub(" ", str(text))
    s = _PUA_AND_GLYPHS_RE.sub(" ", s)
    s = _PAGE_MARKER_RE.sub(" ", s)
    s, _ = scrub_pii(s)
    s = _ACB_RE.sub("Vigilance", s)
    s = _NEUTRAL_REPLACE_RE.sub("compliance-review", s)
    s = _MULTI_SPACE_RE.sub(" ", s).strip()
    if max_len > 0 and len(s) > max_len:
        s = _trim_word_boundary(s, max_len)
    return s


def _records_raw_text(recs: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for rec in recs:
        if isinstance(rec, dict):
            if isinstance(rec.get("raw_text"), str) and rec["raw_text"].strip():
                parts.append(rec["raw_text"])
            elif isinstance(rec.get("text"), str) and rec["text"].strip():
                parts.append(rec["text"])
            else:
                parts.append(json.dumps(rec, ensure_ascii=False))
    return "\n".join(parts)


def _records_norm_haystack(recs: list[dict[str, Any]]) -> str:
    return norm_search(_records_raw_text(recs))


def _word_boundary_match(norm_tok: str, norm_hay: str) -> bool:
    if not norm_tok:
        return False
    return re.search(r"(?<![a-z0-9])" + re.escape(norm_tok) + r"(?![a-z0-9])", norm_hay) is not None


def _has_alpha_substance(tok: str, *, min_alpha: int = 4) -> bool:
    return sum(1 for ch in tok if ch.isalpha()) >= min_alpha


def _is_comma_broken_fragment(tok: str) -> bool:
    """Detect broken Indian rupee/count fragments caused by splitting inside comma-grouped numbers."""
    s = tok.strip()
    if re.search(r"(?:₹|Rs\.?)\s*\d{1,3}$", s, re.IGNORECASE):
        return True
    if re.search(r"\b\d{1,3}$", s) and re.search(r"(?:of|to|worth|total|amounting|planting|expenditure|out of)\s+(?:₹\s*)?\d{1,2}$", s, re.IGNORECASE):
        return True
    if re.match(r"^\d{3}(?:\.\d+)?\s+(?:crore|lakh|ha\b|e-challans|beneficiaries|cases)", s, re.IGNORECASE):
        return True
    return False


def _token_allowed(
    tok: str,
    *,
    context_lower: str,
    allow_numeric: bool = False,
) -> bool:
    cleaned = tok.strip()
    if len(cleaned) < 3 or len(cleaned) > 120:
        return False
    if scan_forbidden_terms(cleaned):
        return False
    if _PUA_AND_GLYPHS_RE.search(cleaned):
        return False
    if _is_comma_broken_fragment(cleaned):
        return False
    low = cleaned.lower()
    if low in _GENERIC_BAN_TOKENS:
        return False
    if cleaned in _BANNED_BARE_NUMBERS or low in _BANNED_BARE_NUMBERS:
        return False
    if _NUMERIC_ONLY_RE.match(cleaned):
        if not allow_numeric:
            return False
        digits = re.sub(r"\D", "", cleaned)
        # Only keep genuinely specific multi-digit figures (e.g. 12491, 206461, 22,085), never generic 2-4 digit numbers
        if len(digits) < 4:
            return False
    if low in _CONTEXT_GATED_ACRONYMS:
        cues = _CONTEXT_GATED_ACRONYMS[low]
        if not any(cue in context_lower for cue in cues):
            return False
    return True


def _extract_verbatim_from_raw(raw_text: str, norm_tok: str) -> str | None:
    """Find the exact verbatim surface string matching `norm_tok` (or minor plural/inflection) at word boundaries."""
    # Fast path: build a whitespace-collapsed version of raw_text with standard dashes/quotes for extraction
    flat = _MULTI_SPACE_RE.sub(" ", _PUA_AND_GLYPHS_RE.sub(" ", _CTRL_CHARS_RE.sub(" ", raw_text)))
    flat_n = _DASH_VARIANTS_RE.sub("-", flat)
    flat_n = _QUOTE_SINGLE_RE.sub("'", flat_n)
    flat_n = _QUOTE_DOUBLE_RE.sub('"', flat_n)

    pat_exact = re.compile(r"(?<![A-Za-z0-9])" + re.escape(norm_tok) + r"(?![A-Za-z0-9])", re.IGNORECASE)
    m = pat_exact.search(flat_n)
    if m:
        return m.group(0).strip()

    # For substantive phrases (>= 5 chars, >= 4 alpha), allow exact plural/suffix match verbatim from PDF
    if len(norm_tok) >= 5 and _has_alpha_substance(norm_tok):
        pat_ext = re.compile(r"(?<![A-Za-z0-9])" + re.escape(norm_tok) + r"[a-z]{1,3}(?![A-Za-z0-9])", re.IGNORECASE)
        m2 = pat_ext.search(flat_n)
        if m2:
            return m2.group(0).strip()
    return None


def _split_clauses_intact_numbers(text: str) -> list[str]:
    """Split a sentence into clauses without ever splitting inside comma-grouped numbers."""
    # Split on semicolons, em-dashes, or commas NOT surrounded by digits
    parts = re.split(r";|\s+—\s+|(?<!\d),\s+(?!\d)|\s+and\s+(?=[A-Z₹])", text)
    return [p.strip(" .,:;-()") for p in parts if p and p.strip(" .,:;-()")]


def _mine_verbatim_phrases_from_text(
    text: str,
    hay: str,
    raw_text: str,
    *,
    context_lower: str,
    exclude_norms: set[str],
    max_phrases: int = 4,
) -> list[str]:
    """Mine clean verbatim 2–6 word substantive phrases from `text` that match `hay` at word boundaries."""
    out: list[str] = []
    seen = set(exclude_norms)
    clean_t = neutralize_clean(text)
    # Try intact clauses first
    for clause in _split_clauses_intact_numbers(clean_t):
        words = re.findall(r"[A-Za-z0-9₹][A-Za-z0-9./%₹\-()']*", clause)
        if len(words) < 2:
            continue
        for win in (5, 4, 3, 2):
            for i in range(0, max(0, len(words) - win + 1)):
                cand = " ".join(words[i : i + win]).strip(" .,;:-()[]'\"")
                if not _has_alpha_substance(cand, min_alpha=5):
                    continue
                if not _token_allowed(cand, context_lower=context_lower, allow_numeric=False):
                    continue
                nc = norm_search(cand)
                if not nc or nc in seen:
                    continue
                # Avoid tiny stopword-heavy windows
                alpha_words = [w.lower() for w in re.findall(r"[A-Za-z]+", cand) if len(w) >= 4 and w.lower() not in _GENERIC_BAN_TOKENS]
                if not alpha_words:
                    continue
                if _word_boundary_match(nc, hay):
                    verb = _extract_verbatim_from_raw(raw_text, nc) or cand
                    verb = neutralize_clean(verb, max_len=115).strip(" [].,;:-'\"")
                    if verb and _token_allowed(verb, context_lower=context_lower, allow_numeric=False) and _word_boundary_match(norm_search(verb), hay):
                        seen.add(norm_search(verb))
                        out.append(verb)
                        if len(out) >= max_phrases:
                            return out
    return out


def _mine_pdf_domain_phrases(
    target_recs: list[dict[str, Any]],
    hay: str,
    raw_text: str,
    *,
    mechanism: str,
    side: str,
    context_lower: str,
    exclude_norms: set[str],
    needed: int = 3,
) -> list[str]:
    """Mine clean verbatim substantive phrases directly from the verified PDF bytes."""
    out: list[str] = []
    seen = set(exclude_norms)
    cues = _MECHANISM_CUE_WORDS.get(mechanism, ()) if side == "walkback" else (
        "yojana",
        "mission",
        "abhiyan",
        "pension",
        "scholarship",
        "subsidy",
        "insurance",
        "housing",
        "education",
        "health",
        "welfare",
        "allowance",
        "exemption",
        "concession",
        "programme",
        "benefit",
        "farmers",
        "beneficiaries",
        "workers",
        "scheme",
        "act",
        "policy",
    )

    def _try_phrase(cand: str) -> bool:
        c = neutralize_clean(cand, max_len=95).strip(" [].,;:-()\"'")
        if len(c) < 6 or len(c) > 95:
            return False
        if not _has_alpha_substance(c, min_alpha=5):
            return False
        if not _token_allowed(c, context_lower=context_lower, allow_numeric=False):
            return False
        # Skip pure boilerplate headers / question templates
        low = c.lower()
        if any(
            bad in low
            for bad in (
                "unstarred question",
                "starred question",
                "to be answered on",
                "government of india",
                "lok sabha",
                "rajya sabha",
                "table of contents",
                "comptroller and auditor general",
                "whether the government",
                "if so, the details",
                "minister of state",
                "will the minister",
                "annexure",
                "sl. no",
                "s. no",
            )
        ):
            return False
        nc = norm_search(c)
        if not nc or nc in seen:
            return False
        if _word_boundary_match(nc, hay):
            seen.add(nc)
            out.append(c)
            return True
        return False

    # Pass 1: Look for exact domain cue phrases in the document
    for cue in cues:
        if len(out) >= needed:
            break
        if not _word_boundary_match(cue, hay):
            continue
        # Extract a clean 2–5 word window around this cue verbatim from raw_text
        m = re.search(
            r"(?<![A-Za-z0-9])(?:[A-Za-z0-9-]+\s+){0,2}" + re.escape(cue) + r"(?:\s+[A-Za-z0-9-]+){0,2}(?![A-Za-z0-9])",
            _MULTI_SPACE_RE.sub(" ", _PUA_AND_GLYPHS_RE.sub(" ", raw_text)),
            re.IGNORECASE,
        )
        if m:
            if not _try_phrase(m.group(0)):
                _try_phrase(cue)
        else:
            _try_phrase(cue)

    # Pass 2: Scan substantive sentences across pages for clean multi-word clauses
    if len(out) < needed:
        for rec in target_recs[:15]:
            txt = neutralize_clean(rec.get("raw_text") or rec.get("text") or "")
            if len(txt) < 40:
                continue
            for sent in re.split(r"(?<=[.!?])\s+", txt):
                if len(sent) < 25:
                    continue
                for clause in _split_clauses_intact_numbers(sent):
                    words = clause.split()
                    if 2 <= len(words) <= 7:
                        _try_phrase(clause)
                    elif len(words) > 7:
                        _try_phrase(" ".join(words[:5]))
                    if len(out) >= needed:
                        return out
    # Pass 3: Single substantive words >= 6 chars from PDF as absolute last resort
    if len(out) < needed:
        for rec in target_recs[:15]:
            txt = neutralize_clean(rec.get("raw_text") or rec.get("text") or "")
            for w in re.findall(r"[A-Za-z][A-Za-z-]{5,30}", txt):
                _try_phrase(w)
                if len(out) >= needed:
                    return out
    return out


def _sanitize_and_verify_tokens(
    raw_tokens: list[Any],
    target_recs: list[dict[str, Any]],
    *,
    fallback_summary: str,
    scheme_name: str,
    tranche_or_phase: str,
    mechanism: str,
    side: str,
    context_lower: str,
    exclude_norms: set[str] | None = None,
) -> list[str]:
    """Verify every token at strict word boundaries inside `target_recs`, banning false acronyms,
    bare generic words, comma-broken rupee splits, and all-numeric token lists."""
    raw_text = _records_raw_text(target_recs)
    hay = norm_search(raw_text)
    verified: list[str] = []
    seen_norm: set[str] = set()
    excl = set(exclude_norms or set())

    def _try_add(cand: str, *, allow_numeric: bool = False) -> bool:
        cleaned = neutralize_clean(cand, max_len=120).strip(" [].,;:-'\"")
        if not _token_allowed(cleaned, context_lower=context_lower, allow_numeric=allow_numeric):
            return False
        nc = norm_search(cleaned)
        if not nc or nc in seen_norm:
            return False
        if _word_boundary_match(nc, hay):
            seen_norm.add(nc)
            verified.append(cleaned)
            return True
        # Allow minor plural/inflection lift ONLY for substantive >=5-char tokens, using exact verbatim PDF string
        if len(nc) >= 5 and _has_alpha_substance(cleaned):
            verb = _extract_verbatim_from_raw(raw_text, nc)
            if verb:
                verb_c = neutralize_clean(verb, max_len=120).strip(" [].,;:-'\"")
                nv = norm_search(verb_c)
                if (
                    verb_c
                    and nv not in seen_norm
                    and _token_allowed(verb_c, context_lower=context_lower, allow_numeric=False)
                    and _word_boundary_match(nv, hay)
                ):
                    seen_norm.add(nv)
                    verified.append(verb_c)
                    return True
        return False

    for raw_t in raw_tokens or []:
        t_str = str(raw_t or "")
        for piece in _PAGE_MARKER_RE.split(t_str):
            _try_add(piece, allow_numeric=True)
            if len(verified) >= 8:
                break
        if len(verified) >= 8:
            break

    # Count substantive alphabetical tokens (>= 4 alpha chars)
    def _alpha_count() -> int:
        return sum(1 for tk in verified if _has_alpha_substance(tk, min_alpha=4))

    # Mine substantive verbatim phrases from summary/scheme/tranche if <2 substantive alpha tokens
    if _alpha_count() < 2 or len(verified) < 2:
        seed_texts = (
            [fallback_summary, tranche_or_phase, scheme_name]
            if side == "walkback"
            else [scheme_name, tranche_or_phase, fallback_summary]
        )
        for stxt in seed_texts:
            if not stxt:
                continue
            mined = _mine_verbatim_phrases_from_text(
                stxt,
                hay,
                raw_text,
                context_lower=context_lower,
                exclude_norms=seen_norm | excl,
                max_phrases=3,
            )
            for m in mined:
                if _try_add(m, allow_numeric=False) and _alpha_count() >= 2 and len(verified) >= 2:
                    break
            if _alpha_count() >= 2 and len(verified) >= 2:
                break

    # Mine directly from verified PDF bytes if still short of 2 substantive alpha tokens
    if _alpha_count() < 2 or len(verified) < 2:
        mined_pdf = _mine_pdf_domain_phrases(
            target_recs,
            hay,
            raw_text,
            mechanism=mechanism,
            side=side,
            context_lower=context_lower,
            exclude_norms=seen_norm | excl,
            needed=4,
        )
        for m in mined_pdf:
            _try_add(m, allow_numeric=False)
            if _alpha_count() >= 2 and len(verified) >= 3:
                break

    # If still identical to `exclude_norms` (e.g. promise vs walkback set equality), mine 1 distinct phrase
    if excl and {norm_search(x) for x in verified} == excl:
        extra = _mine_pdf_domain_phrases(
            target_recs,
            hay,
            raw_text,
            mechanism=mechanism,
            side=side,
            context_lower=context_lower,
            exclude_norms=seen_norm | excl,
            needed=2,
        )
        for m in extra:
            if _try_add(m, allow_numeric=False):
                break

    # Keep substantive alpha tokens prioritized ahead of specific numeric tokens
    alpha_first = [t for t in verified if _has_alpha_substance(t, min_alpha=4)]
    rest_toks = [t for t in verified if not _has_alpha_substance(t, min_alpha=4)]
    ordered = (alpha_first + rest_toks)[:10]
    return ordered


def _extract_cag_report_id(raw: dict[str, Any]) -> str:
    for field in (raw.get("tranche_or_phase"), raw.get("walkback_summary"), raw.get("task_id"), raw.get("suggested_task_id"), raw.get("case_key")):
        s = str(field or "")
        m = re.search(r"CAG\s+Report\s+ID\s+(\d+)", s, re.IGNORECASE)
        if m:
            return m.group(1)
        m2 = re.search(r"-cag-(\d+)-", s, re.IGNORECASE)
        if m2:
            return m2.group(1)
    return hashlib.sha256(str(raw.get("retrieval_url") or "").encode()).hexdigest()[:6]


def _clean_cag_topic(raw: dict[str, Any], jur_short: str) -> str:
    """Produce a crisp, word-boundary-clean CAG report topic (never truncated mid-word)."""
    base = html.unescape(str(raw.get("scheme_name") or ""))
    base = re.sub(r"C&amp;?AG", "C&AG", base, flags=re.IGNORECASE)
    # Strip leading "<State> CAG Audit — "
    base = re.sub(r"^.+?\bCAG\s+Audit\s*[—:-]\s*", "", base, flags=re.IGNORECASE)
    base = neutralize_clean(base).strip(" “”\"'–—:-")
    # If upstream catalog pre-truncated mid-word with trailing ellipsis, strip the incomplete word cleanly
    if base.endswith(("…", "...")):
        base = re.sub(r"\s+\S*(?:…|\.\.\.)$", "", base).rstrip(" ,;:-/(")
    # Strip verbose CAG boilerplate prefixes
    boilerplate_patterns = [
        r"^Reports?\s+No\.?\s*\d+\s+of\s+(?:Year\s+)?\d{4}\s*[:–—-]*\s*",
        r"^Report\s+of\s+the\s+Comptroller\s+and\s+Auditor\s+General(?:\s+of\s+India)?\s+(?:on\s+)?",
        r"^Report\s+of\s+the\s+Report\s+of\s+the\s+Comptroller\s+and\s+Auditor\s+General(?:\s+of\s+India)?\s+(?:on\s+)?",
        r"^Government\s+of\s+[A-Za-z\s&]+?\s*[:–—-]+\s*",
        r"^Performance\s+Audit\s+Report\s+(?:of\s+the\s+Comptroller\s+and\s+Auditor\s+General\s+of\s+India\s+)?on\s+",
        r"^Performance\s+Audit\s+(?:Report\s+)?(?:on|of)\s+",
        r"^Compliance\s+Audit\s+Report\s+(?:No\.?\s*\d+\s+of\s+\d{4}\s+)?(?:on|for)\s+",
        r"^Audit\s+Report\s+(?:on|for)\s+",
        r"^State\s+Finances?\s+Audit\s+Report\s+",
        r"^of\s+Government\s+of\s+[A-Za-z\s&]+?\s+on\s+",
    ]
    for pat in boilerplate_patterns:
        cand = re.sub(pat, "", base, flags=re.IGNORECASE).strip(" “”\"'–—:-")
        if len(cand) >= 12:
            base = cand
    # Detect if original title was about State Finances so the topic stays explicit
    orig_low = (str(raw.get("scheme_name") or "") + " " + str(raw.get("retrieval_url") or "")).lower()
    if "state finance" in orig_low or "state-finance" in orig_low or "state_finance" in orig_low:
        yr_m = re.search(r"20\d{2}(?:-\d{2})?", base) or re.search(r"20\d{2}(?:-\d{2})?", orig_low)
        yr = yr_m.group(0) if yr_m else ""
        topic = f"State Finances Audit ({yr})" if yr else "State Finances Audit"
        return _trim_word_boundary(topic, 72)

    # Strip trailing redundant ", Government of <State>" or "(English)"
    base = re.sub(r",?\s*Government\s+of\s+(?:Union\s+Territory\s+of\s+)?[A-Za-z\s&.]*$", "", base, flags=re.IGNORECASE).strip(" ,;:-()")
    base = re.sub(r"\s*\(English\)\s*$", "", base, flags=re.IGNORECASE).strip(" ,;:-()")
    if jur_short and base.lower().startswith(jur_short.lower() + " "):
        rest_b = base[len(jur_short) :].strip(" —:-")
        if len(rest_b) >= 12:
            base = rest_b
    if len(base) < 10 or base.lower().startswith(("for the ", "the period ")):
        ref = neutralize_clean(raw.get("promise_document_ref") or "", max_len=90)
        yr_m = re.search(r"20\d{2}(?:-\d{2})?", str(raw.get("scheme_name") or "") + " " + ref + " " + str(raw.get("retrieval_url") or ""))
        yr = f" ({yr_m.group(0)})" if yr_m else ""
        base = f"Compliance & Sector Audit{yr}"
    # Fix any dangling truncated fragment at end (e.g., "(Report No." or "(Perf")
    if base.count("(") > base.count(")"):
        base = base[: base.rfind("(")].rstrip(" ,;:-")
    # Strip trailing dangling prepositions/conjunctions if trimmed
    base = re.sub(r"\s+(?:of|for|in|on|and|the|to|by|including)$", "", base, flags=re.IGNORECASE).strip(" ,;:-")
    return _trim_word_boundary(base, 72)


def _extract_cag_findings(recs: list[dict[str, Any]], needed: int) -> list[tuple[int, int, str]]:
    """Extract top `needed` distinct, substantive, non-macro audit finding paragraphs from a CAG PDF."""
    candidates: list[tuple[int, int, str]] = []
    seen_norm: set[str] = set()

    for p_idx, rec in enumerate(recs, start=1):
        raw = str(rec.get("raw_text") or rec.get("text") or "")
        if len(raw.strip()) < 80:
            continue
        head = raw[:500]
        if re.search(r"\b(?:Table of Contents|Abbreviations|Glossary of Terms|Preface)\b", head, re.IGNORECASE):
            continue
        if raw.count("....") >= 4 or raw.count(". . .") >= 4:
            continue

        raw_clean = _PUA_AND_GLYPHS_RE.sub(" ", raw)
        raw_clean = _PAGE_MARKER_RE.sub(" ", raw_clean)
        lines: list[str] = []
        for ln in raw_clean.splitlines():
            ls = ln.strip()
            if not ls:
                lines.append("")
                continue
            if re.fullmatch(r"(?:Page\s+)?\d{1,4}", ls, re.IGNORECASE) or re.fullmatch(r"[ivxlcdm]{1,6}", ls, re.IGNORECASE):
                continue
            if re.fullmatch(
                r"(?:Executive Summary|Overview|Chapter\s+[IVX0-9]+|Audit Report.*|State Finances? Audit Report.*|Performance Audit.*|Compliance Audit.*)",
                ls,
                re.IGNORECASE,
            ):
                continue
            lines.append(ls)

        text = "\n".join(lines)
        flat = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
        paras = [_MULTI_SPACE_RE.sub(" ", p).strip() for p in re.split(r"\n{2,}", flat)]
        for para in paras:
            sents = re.split(r"(?<=[.!?])\s+(?=[A-Z(₹])", para)
            for i in range(len(sents)):
                cand = sents[i].strip()
                if len(cand) < 90 and i + 1 < len(sents):
                    cand = (cand + " " + sents[i + 1].strip()).strip()
                cand = neutralize_clean(cand)
                if len(cand) < 75 or len(cand) > 460:
                    continue
                if _CAG_CHART_TABLE_RE.search(cand) or _CAG_PRAISE_RE.search(cand) or _CAG_MACRO_RE.search(cand):
                    continue
                toks = cand.split()
                if len(toks) < 11:
                    continue
                num_toks = sum(1 for tk in toks if re.fullmatch(r"[\d,.\-%₹()`]+", tk))
                if num_toks / len(toks) > 0.28:
                    continue
                # Strip leading "(Paragraph 4.3)" label if present
                cand_clean = re.sub(r"^\(\s*Paragraph[:\s]*[\d.]+\s*\)\s*", "", cand, flags=re.IGNORECASE).strip()
                if len(cand_clean) < 65:
                    continue
                matches = _CAG_FINDING_RE.findall(cand_clean)
                if not matches:
                    continue
                score = len({m.lower() for m in matches}) * 3
                if re.search(r"(?:₹|Rs\.?|rupees)\s*[\d,]+(?:\.\d+)?\s*(?:crore|lakh)", cand_clean, re.IGNORECASE):
                    score += 4
                if re.search(r"\b\d+(?:\.\d+)?\s*(?:per\s*cent|%)\b", cand_clean, re.IGNORECASE):
                    score += 2
                if re.search(
                    r"\b(?:beneficiar|scheme|grant|subsidy|pension|scholarship|housing|health|water|hospital|school|worker|farmer|cess|welfare|project|mission|yojana|abhiyan|fund|spse|stamp duty|tax|utilisation certificate|sna)\w*\b",
                    cand_clean,
                    re.IGNORECASE,
                ):
                    score += 3
                if re.search(
                    r"\b(?:shortfall|unspent|unutilised|unutilized|surrender|lapsed|arrears|non-release|short[\s-]release|not transferred|unadjusted|utilisation certificate|not paid|pending|not achieved|failed to achieve|lying idle|under[\s-]?assessment|short[\s-]?levy)\b",
                    cand_clean,
                    re.IGNORECASE,
                ):
                    score += 3
                if cand_clean[0].islower() or cand_clean.startswith(("(", "Source:", "Note:")):
                    score -= 3
                # Penalize garbled OCR sentences with repeated "per cent per cent"
                if re.search(r"\bper cent\s+per cent\b", cand_clean, re.IGNORECASE):
                    score -= 10
                key = re.sub(r"[^a-z0-9]+", "", cand_clean.lower())[:60]
                if key in seen_norm:
                    continue
                seen_norm.add(key)
                candidates.append((score, p_idx, cand_clean))

    candidates.sort(key=lambda x: (-x[0], x[1]))
    picked: list[tuple[int, int, str]] = []
    used_pages: set[int] = set()
    for item in candidates:
        sc, pg, _ = item
        if len(picked) < needed:
            if pg in used_pages and len(candidates) > needed * 3:
                continue
            picked.append(item)
            used_pages.add(pg)
    if len(picked) < needed:
        for item in candidates:
            if item not in picked:
                picked.append(item)
            if len(picked) >= needed:
                break
    return picked


def _extract_shortfall_exposure_inr(finding_text: str) -> tuple[float | None, str | None]:
    """Extract an INR exposure ONLY when directly linked to a scheme shortfall/unspent/arrears/loss figure.
    Strictly excludes macro State-budget aggregates, total expenditure, or total budget provision figures."""
    if _CAG_MACRO_RE.search(finding_text):
        return None, None
    amt_re = re.compile(
        r"(?:₹|Rs\.?|INR|rupees)\s*([\d,]+(?:\.\d+)?)\s*(crore|lakh)",
        re.IGNORECASE,
    )
    matches = list(amt_re.finditer(finding_text))
    if not matches:
        return None, None
    best: tuple[int, float, str] | None = None
    for m in matches:
        start, end = m.span()
        before = finding_text[max(0, start - 90) : start]
        after = finding_text[end : min(len(finding_text), end + 90)]
        # Reject amounts immediately preceded by budget-provision / total-expenditure / out-of framing
        if _NON_SHORTFALL_PREFIX_RE.search(before):
            continue
        # Require explicit shortfall/unspent/arrears/surrender/loss cue within 90 chars before or after
        has_before = bool(_SHORTFALL_EXP_CTX_RE.search(before))
        has_after = bool(_SHORTFALL_EXP_CTX_RE.search(after))
        if not (has_before or has_after):
            continue
        try:
            num = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        unit = m.group(2).lower()
        inr = round(num * (1e7 if unit == "crore" else 1e5), 2)
        if inr <= 0:
            continue
        # Sanity ceiling: skip suspicious macro-scale State figures (> Rs 15,000 Cr) unless explicitly UCs/arrears/unspent
        if inr > 15_000 * 1e7:
            if not re.search(r"\b(?:unspent|unutilised|utilisation certificates?|arrears of revenue|surrendered|lying idle)\b", before + " " + after, re.IGNORECASE):
                continue
        prio = (2 if has_after else 0) + (2 if has_before else 0)
        basis = f"CAG-documented shortfall/unspent/arrears figure: ₹{m.group(1)} {unit}"
        if best is None or prio > best[0]:
            best = (prio, inr, basis)
    if best is None:
        return None, None
    return best[1], best[2]


def _extract_cag_finding_tokens(
    finding_text: str,
    target_recs: list[dict[str, Any]],
    *,
    context_lower: str,
    exclude_norms: set[str],
) -> list[str]:
    """Extract 2–4 clean, comma-intact, word-boundary-verified verbatim phrases from the CAG finding."""
    raw_text = _records_raw_text(target_recs)
    hay = norm_search(raw_text)
    toks: list[str] = []
    seen = set(exclude_norms)

    # First try intact clauses (preserving Indian comma-grouped numbers completely!)
    for clause in _split_clauses_intact_numbers(finding_text):
        c = neutralize_clean(clause, max_len=110).strip(" [].,;:-()\"'")
        if len(c) < 10 or len(c) > 110:
            continue
        if not _has_alpha_substance(c, min_alpha=6):
            continue
        if not _token_allowed(c, context_lower=context_lower, allow_numeric=False):
            continue
        nc = norm_search(c)
        if nc in seen:
            continue
        if _word_boundary_match(nc, hay):
            seen.add(nc)
            toks.append(c)
            if len(toks) >= 3:
                return toks

    # Fallback: mine verbatim 3–6 word windows directly from `finding_text`
    mined = _mine_verbatim_phrases_from_text(
        finding_text,
        hay,
        raw_text,
        context_lower=context_lower,
        exclude_norms=seen,
        max_phrases=4,
    )
    for m in mined:
        nm = norm_search(m)
        if nm not in seen:
            seen.add(nm)
            toks.append(m)
        if len(toks) >= 3:
            break
    return toks


def _refine_all_records(
    selected_raw: list[dict[str, Any]],
    parsed_records: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Apply deterministic CAG audit re-extraction, statutory date/token fixes, and OROP hygiene."""
    refined = [dict(r) for r in selected_raw]

    # 1. Statutory Union overrides, SGB naming consistency & OROP hygiene
    for r in refined:
        slug = slugify(r.get("scheme_slug") or r.get("scheme_name") or "", max_len=78)
        if slug in {"sovereign-gold-bond", "sovereign-gold-bond-scheme"} or r.get("is_sgb_anchor"):
            sn = str(r.get("scheme_name") or "")
            if "Sovereign Gold Bond Scheme" in sn and "(SGB)" not in sn:
                r["scheme_name"] = sn.replace("Sovereign Gold Bond Scheme", "Sovereign Gold Bond (SGB) Scheme", 1)
        if slug in _STATUTORY_UNION_OVERRIDES:
            ov = _STATUTORY_UNION_OVERRIDES[slug]
            r["promise_date"] = ov["promise_date"]
            r["government_era_or_launch_year"] = ov["government_era_or_launch_year"]
            r["promise_evidence_tokens"] = list(ov["promise_evidence_tokens"])
            r["walkback_evidence_tokens"] = list(ov["walkback_evidence_tokens"])

        # OROP parliamentary Q&As originally bucketed under agnipath-defence-recruitment
        if slug == "agnipath-defence-recruitment" and "one rank" in str(r.get("tranche_or_phase") or "").lower() or (
            slug == "agnipath-defence-recruitment" and "orop" in str(r.get("tranche_or_phase") or "").lower()
        ):
            r["scheme_name"] = "One Rank One Pension (OROP) — Armed Forces Pension Revision"
            r["promise_date"] = "2015-11-07"
            r["government_era_or_launch_year"] = "2015 (MoD OROP Notification)"
            r["promise_summary"] = (
                "Launch commitment under One Rank One Pension (OROP, MoD letter dated 07.11.2015): "
                "uniform pension for armed forces personnel retiring in the same rank with the same length of service, "
                "with periodic re-fixation every 5 years (due July 2019) and timely arrear disbursement."
            )
            r["promised_metric"] = "5-year OROP pension equalisation (due Jul 2019) & timely arrears"

    # 2. Group all CAG tasks by PDF URL for clean, distinct Finding #1 / Finding #2 re-extraction
    cag_by_url: dict[str, list[int]] = defaultdict(list)
    for idx, r in enumerate(refined):
        u = clean_public_url(r["retrieval_url"])
        if "cag.gov.in" in (urlsplit(u).hostname or "").lower():
            cag_by_url[u].append(idx)

    for u, idxs in cag_by_url.items():
        recs = parsed_records.get(u, [])
        if not recs:
            continue
        findings = _extract_cag_findings(recs, needed=len(idxs))
        for pos, r_idx in enumerate(idxs):
            r = refined[r_idx]
            jlvl = str(r["jurisdiction_level"]).strip().lower()
            jur_short = "Union of India" if jlvl == "union" else neutralize_clean(r.get("state_or_ut"), max_len=80)
            rep_id = _extract_cag_report_id(r)
            f_num = pos + 1
            topic = _clean_cag_topic(r, jur_short)

            r["scheme_name"] = f"{jur_short} CAG Audit — {topic}"
            r["tranche_or_phase"] = f"CAG Report ID {rep_id} Finding #{f_num}: {topic}"
            r["_cag_title_override"] = f"{jur_short} · CAG Report {rep_id} (Finding #{f_num}): {topic}"

            if pos < len(findings):
                _sc, pg, finding_txt = findings[pos]
                r["walkback_summary"] = neutralize_clean(
                    f"CAG Audit Finding (Report ID {rep_id}, p.{pg}): {finding_txt}",
                    max_len=590,
                )
                exp_inr, exp_basis = _extract_shortfall_exposure_inr(finding_txt)
                r["estimated_exposure_inr"] = exp_inr
                r["exposure_basis"] = exp_basis
                if exp_inr is not None and exp_basis:
                    r["revised_metric"] = neutralize_clean(exp_basis.replace("CAG-documented shortfall/unspent/arrears figure: ", "CAG finding: "), max_len=150)
                else:
                    r["revised_metric"] = neutralize_clean(f"CAG Report {rep_id} p.{pg}: documented implementation/fund shortfall", max_len=150)

                # Set clean record_selector page pointer
                sel = dict(r.get("record_selector") or {})
                sel["walkback_page"] = pg
                sel["section_hint"] = neutralize_clean(f"CAG Report ID {rep_id} Finding #{f_num} (p.{pg})", max_len=118)
                r["record_selector"] = sel

                ctx_low = f"{r['scheme_name']} {r['tranche_or_phase']} {finding_txt}".lower()
                # Extract clean distinct promise tokens from topic/report mandate (never bare 'State')
                p_toks = _sanitize_and_verify_tokens(
                    r.get("promise_evidence_tokens") or [],
                    recs,
                    fallback_summary=f"{topic} {r.get('promise_summary') or ''}",
                    scheme_name=topic,
                    tranche_or_phase=topic,
                    mechanism=str(r.get("walkback_mechanism") or "arrears_or_unfunded_promise"),
                    side="promise",
                    context_lower=ctx_low,
                )
                p_norms = {norm_search(x) for x in p_toks}
                w_toks = _extract_cag_finding_tokens(
                    finding_txt,
                    recs,
                    context_lower=ctx_low,
                    exclude_norms=p_norms,
                )
                if len(w_toks) < 2:
                    w_toks = _sanitize_and_verify_tokens(
                        w_toks,
                        recs,
                        fallback_summary=finding_txt,
                        scheme_name=topic,
                        tranche_or_phase=r["tranche_or_phase"],
                        mechanism=str(r.get("walkback_mechanism") or "arrears_or_unfunded_promise"),
                        side="walkback",
                        context_lower=ctx_low,
                        exclude_norms=p_norms,
                    )
                r["promise_evidence_tokens"] = p_toks
                r["walkback_evidence_tokens"] = w_toks
                r["promise_summary"] = neutralize_clean(
                    f"Under approved State Budget allocations, scheme guidelines, and statutory compliance rules audited in "
                    f"{jur_short} CAG Report ID {rep_id} ({topic}), allocated public funds and notified entitlements were committed for timely delivery.",
                    max_len=580,
                )

    return refined


def slugify(text: str, *, max_len: int = 40) -> str:
    k = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    if not k:
        return "scheme"
    return k[:max_len].strip("-") or "scheme"


def clean_public_url(url: str) -> str:
    raw = html.unescape(str(url or "").strip())
    if "#" in raw:
        raw = raw.split("#", 1)[0]
    parts = urlsplit(raw)
    encoded_path = quote(unquote(parts.path.replace("\\", "/")), safe="/:@!$&'()*+,;=-._~%")
    rebuilt = urlunsplit((parts.scheme, parts.netloc, encoded_path, parts.query, ""))
    cleaned = validate_safe_http_url(rebuilt, require_https=True, allow_private=False)
    if _HTML_ENTITY_IN_URL_RE.search(cleaned):
        raise ValueError(f"URL contains HTML entity: {cleaned}")
    host = (urlsplit(cleaned).hostname or "").lower()
    path = urlsplit(cleaned).path or ""
    if host == "raw.githubusercontent.com" and not _PINNED_GITHUB_RAW_PATH_RE.match(path):
        raise ValueError(f"Unpinned GitHub raw URL rejected: {cleaned}")
    if host in {"github.com", "www.github.com"} and "/raw/" in path.lower():
        segs = [p for p in path.split("/") if p]
        if len(segs) < 5 or not re.fullmatch(r"[0-9a-f]{40}", segs[3]):
            raise ValueError(f"Unpinned GitHub /raw/ URL rejected: {cleaned}")
    return cleaned


def _parse_iso_date(val: Any) -> date | None:
    if not isinstance(val, str) or not _ISO_DATE_RE.fullmatch(val.strip()):
        return None
    try:
        return date.fromisoformat(val.strip())
    except ValueError:
        return None


def _blob_path(cache_dir: Path, url: str) -> Path:
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
    return cache_dir / "blobs" / f"{key}.bin"


def _fetch_one_url(
    url: str,
    expected_sha: str,
    cache_dir: Path,
    *,
    double_check: bool = True,
    force_live: bool = False,
) -> tuple[str, str, int, Path]:
    """Fetch (or verify cached) official document bytes and confirm exact SHA-256 + byte-stability."""
    clean_u = clean_public_url(url)
    fp = _blob_path(cache_dir, clean_u)
    fp.parent.mkdir(parents=True, exist_ok=True)

    if not force_live and fp.exists():
        raw = fp.read_bytes()
        obs = hashlib.sha256(raw).hexdigest()
        if not expected_sha or obs == expected_sha:
            return clean_u, obs, len(raw), fp

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/pdf, application/json, text/csv, text/html, */*",
    }
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            with httpx.Client(headers=headers, follow_redirects=True, timeout=35.0) as client:
                r1 = client.get(clean_u)
                if r1.status_code != 200:
                    raise RuntimeError(f"HTTP {r1.status_code} for {clean_u}")
                raw1 = r1.content
                if len(raw1) < 40:
                    raise RuntimeError(f"Body too small ({len(raw1)}B) for {clean_u}")
                sha1 = hashlib.sha256(raw1).hexdigest()
                if expected_sha and sha1 != expected_sha:
                    raise RuntimeError(
                        f"SHA-256 mismatch for {clean_u}: expected {expected_sha}, got {sha1}"
                    )
                if double_check:
                    r2 = client.get(clean_u)
                    if r2.status_code != 200:
                        raise RuntimeError(f"Second GET HTTP {r2.status_code} for {clean_u}")
                    sha2 = hashlib.sha256(r2.content).hexdigest()
                    if sha2 != sha1:
                        raise RuntimeError(
                            f"Byte-stability drift on {clean_u}: {sha1[:12]} != {sha2[:12]}"
                        )
                fp.write_bytes(raw1)
                return clean_u, sha1, len(raw1), fp
        except Exception as exc:
            last_err = exc
            time.sleep(0.7 * (attempt + 1))
    raise RuntimeError(f"Failed to verify {clean_u}: {last_err}")


def _parse_blob_worker(args: tuple[str, str, str, str]) -> tuple[str, str, str, int, int, list[dict[str, Any]]]:
    """Worker process: parse one cached artifact via canonical `load_dataset()` (with SHA-keyed parsed cache)."""
    url, sha, fp_str, parsed_cache_dir = args
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    pc_dir = Path(parsed_cache_dir)
    pc_dir.mkdir(parents=True, exist_ok=True)
    pc_key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
    pc_file = pc_dir / f"{pc_key}_{sha[:12]}.json"
    if pc_file.exists():
        cached = json.loads(pc_file.read_text(encoding="utf-8"))
        if cached.get("sha256") == sha and cached.get("url") == url:
            return (
                url,
                sha,
                str(cached["fmt"]),
                int(cached["byte_size"]),
                int(cached["pages"]),
                list(cached["records"]),
            )

    from aiwatchdogs.connectors.loader import load_dataset

    ds = load_dataset(
        fp_str,
        context={"_extract_html_text": True, "sovereign_lies": {"case_key": "build-parse"}},
        allow_local_files=True,
        expected_sha256=sha,
    )
    pages = len(ds.records) if ds.format == "pdf" else 1
    pc_file.write_text(
        json.dumps(
            {
                "url": url,
                "sha256": ds.sha256,
                "fmt": ds.format,
                "byte_size": ds.byte_size,
                "pages": pages,
                "records": ds.records,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return url, ds.sha256, ds.format, ds.byte_size, pages, ds.records


def _load_cases_from_existing_project(project_path: Path) -> list[dict[str, Any]]:
    """Reconstruct normalized case records from an existing committed `projects/sovereign-lies.json`."""
    data = json.loads(project_path.read_text(encoding="utf-8"))
    out: list[dict[str, Any]] = []
    for task in data.get("tasks", []):
        p = task["payload"]
        s = p["context"]["sovereign_lies"]
        rec = dict(s)
        rec["task_id"] = task["id"]
        rec["retrieval_url"] = p["retrieval_url"]
        rec["expected_sha256"] = p["expected_sha256"]
        rec["official_publisher"] = p["official_publisher"]
        rec["source_landing_page"] = p["source_landing_page"]
        if p.get("secondary_url"):
            rec["secondary_url"] = p["secondary_url"]
            rec["secondary_expected_sha256"] = p["secondary_expected_sha256"]
        out.append(rec)
    return out


def _select_500_union(union_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Select exactly 500 sharpest, distinct Union records preserving Task #1 SGB anchor + all 73 SGB + all schemes."""
    assert len(union_records) >= TARGET_UNION_TASKS, f"Need >= {TARGET_UNION_TASKS} union records, got {len(union_records)}"

    anchor = [r for r in union_records if r.get("is_sgb_anchor")]
    assert len(anchor) == 1, f"Expected exactly 1 SGB anchor in union catalog, found {len(anchor)}"

    a0 = dict(anchor[0])
    a0["scheme_slug"] = "sovereign-gold-bond"
    a0["is_sgb_anchor"] = True
    a0["is_anchor_case"] = True
    wtoks = list(a0.get("walkback_evidence_tokens") or [])
    for pref in ("from the date of original issue till maturity", "Sovereign Gold Bond Scheme, 2015"):
        if pref in wtoks:
            wtoks.remove(pref)
            wtoks.insert(0, pref)
    a0["walkback_evidence_tokens"] = wtoks

    rest = [dict(r) for r in union_records if not r.get("is_sgb_anchor")]
    for r in rest:
        r["is_sgb_anchor"] = False
        r["is_anchor_case"] = False
        if r.get("scheme_slug") in {"sovereign-gold-bond-scheme", "sovereign-gold-bond"}:
            r["scheme_slug"] = "sovereign-gold-bond"

    must_keep: list[dict[str, Any]] = [a0]
    flexible: list[dict[str, Any]] = []
    for r in rest:
        is_sgb = r.get("scheme_slug") == "sovereign-gold-bond"
        is_non_sansad = "sansad.in" not in str(r.get("retrieval_url") or "")
        is_cleared = r.get("walkback_mechanism") == "grandfathered_or_honored"
        if is_sgb or is_non_sansad or is_cleared:
            must_keep.append(r)
        else:
            flexible.append(r)

    needed_from_flex = TARGET_UNION_TASKS - len(must_keep)
    assert needed_from_flex >= 0, f"Too many must_keep union records: {len(must_keep)}"
    if len(flexible) <= needed_from_flex:
        selected = must_keep + flexible
    else:
        by_scheme: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in flexible:
            by_scheme[r["scheme_slug"]].append(r)
        to_drop = len(flexible) - needed_from_flex
        drop_keys: set[str] = set()
        while to_drop > 0:
            largest_slug = max(by_scheme, key=lambda sl: (len(by_scheme[sl]), sl))
            victim = by_scheme[largest_slug].pop()
            drop_keys.add(victim["case_key"])
            to_drop -= 1
        selected = must_keep + [r for r in flexible if r["case_key"] not in drop_keys]

    assert len(selected) == TARGET_UNION_TASKS
    return selected


def _select_500_state_ut(state_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Select exactly 500 sharpest State & UT records preserving ALL 36 States & UTs, ALL Flagships, substantive CAG & Cleared."""
    # Exclude intro-only Chapter-1 CAG PDFs lacking audit findings
    filtered = [
        r
        for r in state_records
        if not (
            "cag.gov.in" in str(r.get("retrieval_url") or "").lower()
            and _extract_cag_report_id(r) in _INTRO_ONLY_CAG_REPORT_IDS
        )
    ]
    assert len(filtered) >= TARGET_STATE_UT_TASKS, (
        f"Need >= {TARGET_STATE_UT_TASKS} state/ut records after excluding intro-only CAG PDFs, got {len(filtered)}"
    )

    must_keep: list[dict[str, Any]] = []
    flexible: list[dict[str, Any]] = []
    for r in filtered:
        rc = dict(r)
        rc["is_sgb_anchor"] = False
        rc["is_anchor_case"] = False
        cohort = str(rc.get("cohort") or "")
        if cohort in {"A_flagship", "B_cag_audit", "D_cleared_control"} or rc.get("walkback_mechanism") == "grandfathered_or_honored":
            must_keep.append(rc)
        else:
            flexible.append(rc)

    needed = TARGET_STATE_UT_TASKS - len(must_keep)
    assert needed >= 0, f"Too many must_keep state records: {len(must_keep)}"

    if len(flexible) <= needed:
        selected = must_keep + flexible
    else:
        jur_totals: Counter[str] = Counter(r["jurisdiction"] for r in must_keep)
        flex_by_jur: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in flexible:
            jur_totals[r["jurisdiction"]] += 1
            flex_by_jur[r["jurisdiction"]].append(r)

        to_drop = len(flexible) - needed
        drop_keys: set[str] = set()
        while to_drop > 0:
            candidates = [j for j, lst in flex_by_jur.items() if lst]
            target_jur = max(candidates, key=lambda j: (jur_totals[j], len(flex_by_jur[j]), j))
            victim = flex_by_jur[target_jur].pop()
            jur_totals[target_jur] -= 1
            drop_keys.add(victim["case_key"])
            to_drop -= 1

        selected = must_keep + [r for r in flexible if r["case_key"] not in drop_keys]

    assert len(selected) == TARGET_STATE_UT_TASKS
    jurs_covered = {r["jurisdiction"] for r in selected}
    assert len(jurs_covered) == 36, f"Expected all 36 States & UTs covered, got {len(jurs_covered)}"
    return selected


def _make_clean_task(
    raw: dict[str, Any],
    *,
    seq_num: int,
    url_info: dict[str, tuple[str, str, int, int]],
    parsed_records: dict[str, list[dict[str, Any]]],
    used_task_ids: set[str],
    used_case_keys: set[str],
    used_titles: set[str],
) -> dict[str, Any]:
    """Normalize one verified record into a strict, quality-checked P8 task JSON object."""
    jlvl = str(raw["jurisdiction_level"]).strip().lower()
    if jlvl not in VALID_JURISDICTION_LEVELS:
        raise ValueError(f"Invalid jurisdiction_level: {jlvl}")

    if jlvl == "union":
        state_or_ut = None
        jurisdiction = "Union of India"
    else:
        state_or_ut = neutralize_clean(raw.get("state_or_ut"), max_len=80)
        if not state_or_ut:
            raise ValueError(f"Missing state_or_ut for {jlvl}")
        jurisdiction = f"{'State' if jlvl == 'state' else 'UT'}:{state_or_ut}"

    scheme_name = neutralize_clean(raw["scheme_name"], max_len=200)
    scheme_slug = slugify(raw.get("scheme_slug") or scheme_name, max_len=78)
    if scheme_slug == "sovereign-gold-bond-scheme":
        scheme_slug = "sovereign-gold-bond"

    tranche_or_phase = neutralize_clean(raw["tranche_or_phase"], max_len=160)
    authority = neutralize_clean(raw["authority"], max_len=200)
    authority_key = slugify(raw.get("authority_key") or authority, max_len=78)
    scheme_category = str(raw["scheme_category"]).strip().lower()
    walkback_mechanism = str(raw["walkback_mechanism"]).strip().lower()
    retroactive_impact = str(raw["retroactive_impact"]).strip().lower()

    if walkback_mechanism == "grandfathered_or_honored":
        retroactive_impact = "grandfathered_or_honored"

    promise_date = str(raw["promise_date"]).strip()
    walkback_date = raw.get("walkback_date")
    if walkback_date in (None, ""):
        walkback_date = None
    else:
        walkback_date = str(walkback_date).strip()

    is_sgb_anchor = bool(raw.get("is_sgb_anchor"))
    is_anchor_case = bool(raw.get("is_anchor_case") or is_sgb_anchor)

    if is_sgb_anchor and seq_num == 1:
        tid = "p8-union-sgb-anchor-0001"
    else:
        base_hint = raw.get("task_id") or raw.get("suggested_task_id") or ""
        if (
            isinstance(base_hint, str)
            and P8_TASK_ID_RE.fullmatch(base_hint)
            and base_hint.startswith(f"p8-{jlvl}-")
            and base_hint not in used_task_ids
        ):
            tid = base_hint
        else:
            prefix_tag = slugify(state_or_ut or "", max_len=10) + "-" if state_or_ut else ""
            slug_part = slugify(f"{prefix_tag}{scheme_slug}", max_len=36)
            tid = f"p8-{jlvl}-{slug_part}-{seq_num:04d}"
            if tid in used_task_ids:
                tid = f"p8-{jlvl}-case-{seq_num:04d}"
    assert P8_TASK_ID_RE.fullmatch(tid) and len(tid) <= 60, f"Bad task ID: {tid}"
    assert tid not in used_task_ids, f"Duplicate task ID: {tid}"
    used_task_ids.add(tid)

    case_key = tid[3:]
    if case_key in used_case_keys:
        case_key = f"{jlvl}-case-{seq_num:04d}"
    used_case_keys.add(case_key)

    pri_url = clean_public_url(raw["retrieval_url"])
    pri_sha, pri_fmt, _pri_bytes, _pri_pages = url_info[pri_url]

    sec_url_raw = raw.get("secondary_url")
    sec_url = clean_public_url(sec_url_raw) if sec_url_raw else None
    if sec_url and sec_url in url_info:
        sec_sha, sec_fmt, _sec_bytes, _sec_pages = url_info[sec_url]
        sec_role = str(raw.get("secondary_role") or "walkback_document").strip().lower()
        if sec_role not in VALID_SECONDARY_ROLES or not sec_role:
            sec_role = "walkback_document"
    else:
        sec_url = None
        sec_sha = None
        sec_fmt = None
        sec_role = None

    est_exp = raw.get("estimated_exposure_inr")
    if est_exp is not None:
        try:
            est_exp = round(float(est_exp), 2)
            if not math.isfinite(est_exp) or est_exp < 0:
                est_exp = None
        except (TypeError, ValueError):
            est_exp = None

    pri_recs = parsed_records[pri_url]
    sec_recs = parsed_records[sec_url] if (sec_url and sec_url in parsed_records) else []
    if sec_recs and sec_role == "promise_document":
        p_target, w_target = sec_recs, pri_recs
    elif sec_recs and sec_role in {"", "walkback_document"}:
        p_target, w_target = pri_recs, sec_recs
    elif sec_recs and sec_role == "corroborating_record":
        p_target = pri_recs + sec_recs
        w_target = pri_recs + sec_recs
    else:
        p_target, w_target = pri_recs, pri_recs

    promise_summary = neutralize_clean(raw["promise_summary"], max_len=600)
    walkback_summary = neutralize_clean(raw["walkback_summary"], max_len=600)
    context_lower = f"{scheme_slug} {scheme_name} {tranche_or_phase} {promise_summary} {walkback_summary}".lower()

    promise_tokens = _sanitize_and_verify_tokens(
        raw["promise_evidence_tokens"],
        p_target,
        fallback_summary=promise_summary,
        scheme_name=scheme_name,
        tranche_or_phase=tranche_or_phase,
        mechanism=walkback_mechanism,
        side="promise",
        context_lower=context_lower,
    )
    walkback_tokens = _sanitize_and_verify_tokens(
        raw["walkback_evidence_tokens"],
        w_target,
        fallback_summary=walkback_summary,
        scheme_name=scheme_name,
        tranche_or_phase=tranche_or_phase,
        mechanism=walkback_mechanism,
        side="walkback",
        context_lower=context_lower,
        exclude_norms={norm_search(x) for x in promise_tokens},
    )

    # Refresh any stale parliamentary summary parenthetical "indicators (200, 300)" with verified tokens
    if re.search(r"indicators\s*\([^)]*\b(?:200|300|10,000|15%|EPS|PLI|RTE)\b[^)]*\)", walkback_summary):
        tok_preview = ", ".join(walkback_tokens[:3])
        walkback_summary = re.sub(
            r"indicators\s*\([^)]*\)",
            f"indicators ({tok_preview})",
            walkback_summary,
            count=1,
        )
        walkback_summary = neutralize_clean(walkback_summary, max_len=600)

    spec: dict[str, Any] = {
        "case_key": case_key,
        "scheme_name": scheme_name,
        "scheme_slug": scheme_slug,
        "tranche_or_phase": tranche_or_phase,
        "jurisdiction": jurisdiction,
        "jurisdiction_level": jlvl,
        "state_or_ut": state_or_ut,
        "authority": authority,
        "authority_key": authority_key,
        "scheme_category": scheme_category,
        "government_era_or_launch_year": neutralize_clean(raw.get("government_era_or_launch_year") or promise_date[:4], max_len=64),
        "promise_date": promise_date,
        "promise_summary": promise_summary,
        "promise_document_ref": neutralize_clean(raw["promise_document_ref"], max_len=200),
        "promise_evidence_tokens": promise_tokens,
        "walkback_date": walkback_date,
        "walkback_summary": walkback_summary,
        "walkback_document_ref": neutralize_clean(raw["walkback_document_ref"], max_len=200),
        "walkback_evidence_tokens": walkback_tokens,
        "walkback_mechanism": walkback_mechanism,
        "retroactive_impact": retroactive_impact,
        "promised_metric": neutralize_clean(raw["promised_metric"], max_len=160),
        "revised_metric": neutralize_clean(raw["revised_metric"], max_len=160),
        "metric_unit": neutralize_clean(raw["metric_unit"], max_len=64),
        "affected_group": neutralize_clean(raw["affected_group"], max_len=160),
        "estimated_exposure_inr": est_exp,
        "exposure_basis": neutralize_clean(raw.get("exposure_basis"), max_len=200) or None if est_exp is not None else None,
        "statutory_anchor": neutralize_clean(raw.get("statutory_anchor"), max_len=200) or None,
        "benign_explanation_context": neutralize_clean(raw.get("benign_explanation_context"), max_len=300) or None,
        "source_format": pri_fmt if pri_fmt in VALID_SOURCE_FORMATS else "pdf",
        "is_sgb_anchor": is_sgb_anchor,
        "is_anchor_case": is_anchor_case,
    }
    if sec_url and sec_sha:
        spec["secondary_source_format"] = sec_fmt if sec_fmt in VALID_SOURCE_FORMATS else "pdf"
        spec["secondary_role"] = sec_role
    else:
        spec["secondary_role"] = None

    if isinstance(raw.get("record_selector"), dict) and raw["record_selector"]:
        sel: dict[str, Any] = {}
        raw_sel = raw["record_selector"]
        if raw_sel.get("match_field") and raw_sel.get("match_value"):
            sel["match_field"] = str(raw_sel["match_field"])[:80]
            sel["match_value"] = str(raw_sel["match_value"])[:160]
        for pkey in ("promise_page", "walkback_page"):
            pv = raw_sel.get(pkey)
            if isinstance(pv, int) and not isinstance(pv, bool) and 1 <= pv <= 5000:
                sel[pkey] = pv
        if raw_sel.get("section_hint"):
            sel["section_hint"] = neutralize_clean(raw_sel["section_hint"], max_len=120)
        if raw_sel.get("record_url"):
            sel["record_url"] = clean_public_url(raw_sel["record_url"])
        if sel:
            spec["record_selector"] = sel

    official_publisher = neutralize_clean(raw.get("official_publisher") or authority, max_len=200)
    landing_page = clean_public_url(raw.get("source_landing_page") or pri_url)

    task_ctx: dict[str, Any] = {
        "as_of_date": AS_OF_DATE,
        "official_publisher": official_publisher,
        "source_landing_page": landing_page,
        "retrieval_url": pri_url,
        "expected_sha256": pri_sha,
        "sovereign_lies": spec,
    }
    payload: dict[str, Any] = {
        "retrieval_url": pri_url,
        "expected_sha256": pri_sha,
        "official_publisher": official_publisher,
        "source_landing_page": landing_page,
        "as_of_date": AS_OF_DATE,
        "jurisdiction": jurisdiction,
        "context": task_ctx,
    }
    if sec_url and sec_sha:
        task_ctx["secondary_url"] = sec_url
        task_ctx["secondary_expected_sha256"] = sec_sha
        payload["secondary_url"] = sec_url
        payload["secondary_expected_sha256"] = sec_sha

    jur_short = "Union of India" if jlvl == "union" else (state_or_ut or jurisdiction)
    if raw.get("_cag_title_override"):
        title = neutralize_clean(raw["_cag_title_override"], max_len=155)
    else:
        title = neutralize_clean(f"{jur_short} · {scheme_name}: {tranche_or_phase}", max_len=155)
    if title in used_titles:
        # Guarantee 1,000/1,000 unique visible titles while keeping `<jur_short> · ` intact
        tag = f" (#{seq_num})"
        title = _trim_word_boundary(title, 155 - len(tag)) + tag
    used_titles.add(title)

    instructions = neutralize_clean(
        f"Verify SHA-256 {pri_sha[:12]}… and run P8 on {scheme_name} ({tranche_or_phase}, {jurisdiction}): "
        f"confirm launch terms ({promise_date}) vs subsequent record ({walkback_date or 'honored/grandfathered'}).",
        max_len=400,
    )

    return {
        "id": tid,
        "title": title,
        "instructions": instructions,
        "type": "run_check",
        "methodology_code": METHODOLOGY_CODE,
        "dataset_key": f"p8-{jlvl}-{slugify(scheme_slug, max_len=42)}",
        "financial_quantity_type": "scheme_promises_and_revisions",
        "estimated_tokens": 6000 if sec_url else 4000,
        "estimate_basis": (
            "Deterministic P8 two-document SHA-256 and verbatim evidence-token check (zero LLM cost in rule-engine mode)."
            if sec_url
            else "Deterministic P8 single-document SHA-256 and verbatim evidence-token check (zero LLM cost in rule-engine mode)."
        ),
        "payload": payload,
    }


def _common_scheme_display_name(slug: str, group: list[dict[str, Any]]) -> str:
    """Compute a clean display `scheme_name` for `coverage.json`'s `top_schemes` that matches
    100% (N/N) of the scheme's task titles in frontend substring filtering (`p8SchemeFilterQuery`)."""
    if slug == "sovereign-gold-bond":
        return "Sovereign Gold Bond (SGB) Scheme"

    jurs = {t["payload"]["context"]["sovereign_lies"]["jurisdiction"] for t in group}
    if len(jurs) == 1:
        names = [t["payload"]["context"]["sovereign_lies"]["scheme_name"] for t in group]
        if len(set(names)) == 1:
            return names[0]
        # Compute longest common word prefix across tasks sharing this slug
        split_lists = [n.split() for n in names]
        prefix_words: list[str] = []
        for idx in range(min(len(w) for w in split_lists)):
            cand = split_lists[0][idx]
            if all(w[idx].lower() == cand.lower() for w in split_lists):
                prefix_words.append(cand)
            else:
                break
        if len(prefix_words) >= 2:
            return " ".join(prefix_words).rstrip(" —:-(")
        return min(names, key=len)

    # Multi-jurisdiction scheme: strip leading "<State/UT> — " or "<State/UT> " from each task's scheme_name
    stripped: list[str] = []
    for t in group:
        s = t["payload"]["context"]["sovereign_lies"]
        nm = s["scheme_name"]
        st = s.get("state_or_ut") or ""
        if st:
            nm2 = re.sub(r"^" + re.escape(st) + r"\s*(?:—|-|:)?\s*", "", nm, flags=re.IGNORECASE).strip()
            if nm2:
                nm = nm2
        stripped.append(nm)

    if len(set(stripped)) == 1:
        return stripped[0]

    # Longest common word prefix across all stripped names
    split_lists = [n.split() for n in stripped]
    prefix_words = []
    for idx in range(min(len(w) for w in split_lists)):
        cand = split_lists[0][idx]
        if all(w[idx].lower() == cand.lower() for w in split_lists):
            prefix_words.append(cand)
        else:
            break
    if len(prefix_words) >= 2:
        return " ".join(prefix_words).rstrip(" —:-(")
    return min(stripped, key=len)


def _run_quality_gates(
    tasks: list[dict[str, Any]],
    coverage: dict[str, Any],
    parsed_records: dict[str, list[dict[str, Any]]],
) -> None:
    """Enforce zero-defect automated quality gates across all 1,000 tasks & coverage.json."""
    errors: list[str] = []

    if len(tasks) != TARGET_TASKS:
        errors.append(f"Expected {TARGET_TASKS} tasks, got {len(tasks)}")
    if len({t["id"] for t in tasks}) != TARGET_TASKS:
        errors.append("Duplicate task IDs detected")
    if len({t["title"] for t in tasks}) != TARGET_TASKS:
        dups = [k for k, v in Counter(t["title"] for t in tasks).items() if v > 1]
        errors.append(f"Duplicate visible task titles ({len(dups)}): {dups[:5]}")
    if len({t["payload"]["context"]["sovereign_lies"]["case_key"] for t in tasks}) != TARGET_TASKS:
        errors.append("Duplicate case_key values detected")

    # Cache normalized haystacks per URL for fast word-boundary checks
    hay_cache: dict[str, str] = {u: _records_norm_haystack(recs) for u, recs in parsed_records.items()}

    pua_hits = 0
    page_marker_hits = 0
    same_date_hits = 0
    same_tok_hits = 0
    all_num_hits = 0
    non_wb_hits: list[str] = []
    comma_broken_hits: list[str] = []
    bad_cag_hits: list[str] = []
    bare_bad_toks: list[str] = []

    for t in tasks:
        raw_unesc = json.dumps(t, ensure_ascii=False)
        if _PUA_AND_GLYPHS_RE.search(raw_unesc) or _CTRL_CHARS_RE.search(raw_unesc):
            pua_hits += 1
        if _PAGE_MARKER_RE.search(raw_unesc):
            page_marker_hits += 1

        p = t["payload"]
        s = p["context"]["sovereign_lies"]
        tid = t["id"]

        # 1. Dates
        if s["walkback_mechanism"] != "grandfathered_or_honored" and s.get("walkback_date"):
            if s["promise_date"] >= s["walkback_date"]:
                same_date_hits += 1

        # 2. Distinct promise vs walkback tokens
        p_set = {norm_search(x) for x in s["promise_evidence_tokens"]}
        w_set = {norm_search(x) for x in s["walkback_evidence_tokens"]}
        if p_set == w_set:
            same_tok_hits += 1

        # 3. Target doc word-boundary & token hygiene
        pri_u = p["retrieval_url"]
        sec_u = p.get("secondary_url")
        role = s.get("secondary_role") or ""
        if sec_u and role == "promise_document":
            p_hay, w_hay = hay_cache[sec_u], hay_cache[pri_u]
        elif sec_u and role in {"", "walkback_document"}:
            p_hay, w_hay = hay_cache[pri_u], hay_cache[sec_u]
        elif sec_u and role == "corroborating_record":
            p_hay = w_hay = hay_cache[pri_u] + "\n" + hay_cache[sec_u]
        else:
            p_hay = w_hay = hay_cache[pri_u]

        for side, hay in (("promise_evidence_tokens", p_hay), ("walkback_evidence_tokens", w_hay)):
            toks = s[side]
            if len(toks) < 2:
                errors.append(f"{tid} {side} has <2 tokens: {toks}")
            if all(_NUMERIC_ONLY_RE.match(x.strip()) for x in toks):
                all_num_hits += 1
            for tok in toks:
                if tok.strip().lower() in _GENERIC_BAN_TOKENS or tok.strip() in _BANNED_BARE_NUMBERS:
                    bare_bad_toks.append(f"{tid}:{side}:{tok!r}")
                if not _word_boundary_match(norm_search(tok), hay):
                    non_wb_hits.append(f"{tid}:{side}:{tok!r}")
                if _is_comma_broken_fragment(tok):
                    comma_broken_hits.append(f"{tid}:{side}:{tok!r}")
            for i in range(len(toks) - 1):
                a, b = toks[i].strip(), toks[i + 1].strip()
                if re.search(r"(?:^|[\s₹])\d{1,2}$", a) and not re.search(r"\.\d+$", a) and re.match(r"^\d{3}(?:\.\d+)?(?:\s|$)", b):
                    comma_broken_hits.append(f"{tid}:{side}:{a!r}+{b!r}")

        # 4. CAG quality checks
        if "cag.gov.in" in pri_u:
            ws = s["walkback_summary"]
            if _CAG_PRAISE_RE.search(ws) or _CAG_CHART_TABLE_RE.search(ws) or _CAG_MACRO_RE.search(ws):
                bad_cag_hits.append(f"{tid}:{ws[:90]!r}")

    if pua_hits:
        errors.append(f"PUA/bullet/garbled glyphs found in {pua_hits} tasks")
    if page_marker_hits:
        errors.append(f"[PAGE N] artifacts found in {page_marker_hits} tasks")
    if same_date_hits:
        errors.append(f"{same_date_hits} walk-back tasks have promise_date >= walkback_date")
    if same_tok_hits:
        errors.append(f"{same_tok_hits} tasks have set-identical promise vs walkback tokens")
    if all_num_hits:
        errors.append(f"{all_num_hits} all-numeric token lists detected")
    if bare_bad_toks:
        errors.append(f"{len(bare_bad_toks)} banned bare/generic tokens: {bare_bad_toks[:8]}")
    if non_wb_hits:
        errors.append(f"{len(non_wb_hits)} non-word-boundary tokens: {non_wb_hits[:8]}")
    if comma_broken_hits:
        errors.append(f"{len(comma_broken_hits)} comma-broken number tokens: {comma_broken_hits[:8]}")
    if bad_cag_hits:
        errors.append(f"{len(bad_cag_hits)} non-walkback/praise/chart/macro CAG summaries: {bad_cag_hits[:5]}")

    # 5. Frontend jurisdiction prefix & top_schemes filter integrity checks (#39)
    for jrow in coverage["jurisdictions"]:
        jur_short = "Union of India" if jrow["jurisdiction_level"] == "union" else str(jrow["state_or_ut"])
        prefix = f"{jur_short} ·"
        matched = sum(1 for t in tasks if prefix.lower() in t["title"].lower())
        if matched != jrow["tasks"]:
            errors.append(f"Jurisdiction filter mismatch for {prefix!r}: matched {matched} != {jrow['tasks']}")

    by_slug_tasks: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in tasks:
        by_slug_tasks[t["payload"]["context"]["sovereign_lies"]["scheme_slug"]].append(t)

    for srow in coverage["top_schemes"]:
        sslug = srow["scheme_slug"]
        grp = by_slug_tasks[sslug]
        jurs = {t["payload"]["context"]["sovereign_lies"]["jurisdiction"] for t in grp}
        if len(jurs) > 1 and not srow["jurisdiction"].startswith("Multi-State"):
            errors.append(f"top_schemes[{sslug}] spans {len(jurs)} jurs but jurisdiction={srow['jurisdiction']!r}")
        has_walkback = any(t["payload"]["context"]["sovereign_lies"]["walkback_mechanism"] != "grandfathered_or_honored" for t in grp)
        if has_walkback and srow["walkback_mechanism"] == "grandfathered_or_honored":
            errors.append(f"top_schemes[{sslug}] has walk-back tasks but labeled grandfathered_or_honored")
        q = srow["scheme_name"].lower()
        q_matches = sum(1 for t in grp if q in t["title"].lower() or q in t["payload"]["context"]["sovereign_lies"]["scheme_name"].lower())
        if q_matches < len(grp):
            errors.append(f"top_schemes[{sslug}] display name {srow['scheme_name']!r} only matches {q_matches}/{len(grp)} tasks")

    if errors:
        raise RuntimeError("P8 QUALITY GATE FAILED:\n  - " + "\n  - ".join(errors))


def _write_methodology_md(coverage: dict[str, Any], hosts_summary: list[tuple[str, int]]) -> None:
    anchor = coverage["sgb_anchor_case"] or {}
    jlvl = coverage["by_jurisdiction_level"]
    mech = coverage["by_mechanism"]
    dry = coverage["dry_run_summary"]
    host_lines = "\n".join(f"  - `{host}`: `{cnt}` unique SHA-256-pinned documents" for host, cnt in hosts_summary)
    content = f"""# Methodology `P8` — Sovereign Lies

## Simple Hook
> **{HOOK_TEXT}**

## Objective
Audit **1,000 SHA-256-pinned Indian government scheme commitments** spanning the **Union Government** (`{jlvl['union']['tasks']}` tasks) and **all {coverage['states_and_uts_covered']} States & Union Territories** (`{jlvl['state']['tasks']}` State + `{jlvl['ut']['tasks']}` UT tasks across all 28 States and 8 UTs) covering **{coverage['unique_schemes_covered']} unique schemes** and **{coverage['documents_count']} unique live-verified official documents**. Every task compares an explicit launch promise (Union/State Budget, Finance Bill, RBI/NSE SGB notification, Parliamentary answer, or CAG-documented Government Order) against a subsequent statutory amendment, benefit cut, premature discontinuation, eligibility narrowing, exit restriction, or documented arrears record — failing closed as `unevaluable` whenever downloaded bytes fail SHA-256 verification or lack required verbatim documentary tokens.

## Flagship Anchor Case (Task #1: `{anchor.get('task_id', 'p8-union-sgb-anchor-0001')}`)
- **Scheme:** {anchor.get('scheme_name', 'Sovereign Gold Bond (SGB) Scheme')}
- **Scope:** {anchor.get('tranche_or_phase', '')}
- **Jurisdiction:** {anchor.get('jurisdiction', 'Union of India')}
- **Authority:** {anchor.get('authority', '')}
- **Original Launch Promise (`{anchor.get('promise_date', '')}`):** {anchor.get('promise_summary', '')}
- **Subsequent Walk-Back (`{anchor.get('walkback_date', '')}`):** {anchor.get('walkback_summary', '')}
- **Promised vs. Revised Metric:** `{anchor.get('promised_metric', '')}` → `{anchor.get('revised_metric', '')}`
- **Primary Launch Record:** {anchor.get('source_url', '')}
- **Walk-Back Record:** {anchor.get('secondary_url', '')}

## Corpus Coverage (`{coverage['total_tasks']}` Tasks · All 28 States + 8 UTs + Union · Live Byte-Stability Verified)
- **Jurisdiction Tier Split:** Union of India: `{jlvl['union']['tasks']}` · States (28/28): `{jlvl['state']['tasks']}` · Union Territories (8/8): `{jlvl['ut']['tasks']}`
- **Sovereign Gold Bond Cohort:** `{coverage['sgb_tasks_count']}` tasks (Flagship Anchor #1 + ALL 67/67 SGB Tranches 2015-16 Series I through 2023-24 Series IV + CAG Gold Reserve Fund & statutory indexation/discontinuation checks)
- **Walk-Back Mechanism Breakdown:**
  - `retroactive_tax_reversal`: `{mech['retroactive_tax_reversal']['tasks']}`
  - `midstream_benefit_cut`: `{mech['midstream_benefit_cut']['tasks']}`
  - `premature_discontinuation`: `{mech['premature_discontinuation']['tasks']}`
  - `eligibility_narrowing`: `{mech['eligibility_narrowing']['tasks']}`
  - `lockin_or_exit_restriction`: `{mech['lockin_or_exit_restriction']['tasks']}`
  - `arrears_or_unfunded_promise`: `{mech['arrears_or_unfunded_promise']['tasks']}`
  - `grandfathered_or_honored` (Cleared benchmark): `{mech['grandfathered_or_honored']['tasks']}`
- **Deterministic Dry-Run (`as_of_date={coverage['as_of_date']}`):** `{dry['leads']}` leads · `{dry['cleared']}` cleared · `{dry['unevaluable']}` unevaluable (across all `{dry['tasks_tested']}` tasks)
- **Verified Byte-Stable Source Domains ({coverage['documents_count']} unique pinned URLs):**
{host_lines}

## Deterministic Rules (`P8`)
1. **Retrospective Tax Reversal (`P8-RETROACTIVE-TAX-REVERSAL`):** Tax exemption or fiscal treatment promised at launch is later narrowed, capped, or withdrawn.
2. **Mid-Stream Benefit Cut (`P8-MIDSTREAM-BENEFIT-CUT`):** Interest rate, subsidy share, payout, or entitlement reduced during an active scheme tenure.
3. **Premature Discontinuation (`P8-PREMATURE-DISCONTINUATION`):** Notified scheme, tranche series, fellowship, or concession suspended or wound up ahead of schedule.
4. **Eligibility Narrowing (`P8-ELIGIBILITY-NARROWING`):** Post-launch taxpayer bars, income ceilings, or beneficiary pruning narrowing original launch eligibility.
5. **Lock-In or Exit Restriction (`P8-LOCKIN-OR-EXIT-RESTRICTION`):** Lock-in lengthened or premature exit/redemption windows tightened after launch.
6. **Arrears or Unfunded Promise (`P8-ARREARS-OR-UNFUNDED-PROMISE`):** Notified benefit, pension, waiver, or subsidy left unpaid, unimplemented, or in documented arrears.
7. **Grandfathered or Honored (`Cleared`):** Verified launch terms honored or existing holders grandfathered intact (`{coverage['cleared_control_tasks_embedded']}` tasks), published with equal journalistic standing.

## Legal, Privacy & Verification Safeguards
- **Fail-Closed Verification:** Every primary and secondary URL is double-fetch byte-stability verified and SHA-256 pinned (`DigestMismatchError` on any byte change). `P8` confirms `>= 40` chars of readable text and 100% verbatim word-boundary token presence in the verified document bytes before emitting any lead or cleared verdict.
- **BNS Sec 356 Neutrality & DPDP Act 2023:** Zero forbidden accusatory terms and zero personal PII across `projects/sovereign-lies.json`, `coverage.json`, `documents.csv`, and `METHODOLOGY.md`. Every lead emits mandatory lawful benign explanations (parliamentary statutory amendment power, FRBM fiscal consolidation, bullion/macroeconomic cycles, arbitrage prevention, and e-KYC deduplication).
"""
    PROJECT_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    (PROJECT_ASSETS_DIR / "METHODOLOGY.md").write_text(content, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build & verify Project P8 (Sovereign Lies, 1,000 tasks).")
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE_DIR, help="Local blob cache dir (kept out of /tmp).")
    parser.add_argument("--union-catalog", type=Path, default=DEFAULT_UNION_CATALOG, help="Union researcher catalog JSON.")
    parser.add_argument("--state-catalog", type=Path, default=DEFAULT_STATE_CATALOG, help="State/UT researcher catalog JSON.")
    parser.add_argument("--from-existing", action="store_true", help="Rebuild/verify from committed projects/sovereign-lies.json.")
    parser.add_argument("--force-live", action="store_true", help="Re-download all URLs ignoring local cache.")
    parser.add_argument("--skip-spot-check", action="store_true", help="Skip live uncached cross-domain byte-stability spot-check.")
    parser.add_argument("--fetch-workers", type=int, default=12, help="Concurrent HTTP download threads.")
    parser.add_argument("--parse-workers", type=int, default=max(2, min(8, multiprocessing.cpu_count())), help="Parallel PDF parse workers.")
    args = parser.parse_args()

    from aiwatchdogs.engines.registry import run_engine
    from aiwatchdogs.projects import validate_project

    t_start = time.time()
    use_existing = args.from_existing or not (args.union_catalog.exists() and args.state_catalog.exists())
    if use_existing:
        if not PROJECT_JSON_PATH.exists():
            raise SystemExit(f"Neither researcher catalogs nor {PROJECT_JSON_PATH} exist.")
        print(f"Standalone rebuild/verification mode: reading {PROJECT_JSON_PATH}...")
        selected_raw = _load_cases_from_existing_project(PROJECT_JSON_PATH)
    else:
        print(f"Catalog build mode: loading {args.union_catalog} & {args.state_catalog}...")
        u_recs = json.loads(args.union_catalog.read_text(encoding="utf-8"))["records"]
        s_recs = json.loads(args.state_catalog.read_text(encoding="utf-8"))["records"]
        selected_union = _select_500_union(u_recs)
        selected_state = _select_500_state_ut(s_recs)
        selected_raw = selected_union + selected_state
        print(f"Selected {len(selected_union)} Union + {len(selected_state)} State/UT = {len(selected_raw)} tasks.")

    assert len(selected_raw) == TARGET_TASKS, f"Expected {TARGET_TASKS} tasks, got {len(selected_raw)}"

    # 1. Collect all unique pinned URLs -> expected SHA-256
    url_expected: dict[str, str] = {}
    for rec in selected_raw:
        for uk, sk in (("retrieval_url", "expected_sha256"), ("secondary_url", "secondary_expected_sha256")):
            u = rec.get(uk)
            if u:
                cu = clean_public_url(u)
                sha = str(rec.get(sk) or "").strip().lower()
                if cu in url_expected and sha and url_expected[cu] and url_expected[cu] != sha:
                    raise RuntimeError(f"Conflicting SHA-256 for {cu}: {url_expected[cu]} vs {sha}")
                if sha or cu not in url_expected:
                    url_expected[cu] = sha

    print(f"Verifying {len(url_expected)} unique official HTTPS URLs (cache={args.cache})...", flush=True)
    blob_map: dict[str, tuple[str, int, Path]] = {}
    with ThreadPoolExecutor(max_workers=args.fetch_workers) as ex:
        futs = {
            ex.submit(_fetch_one_url, u, sha, args.cache, double_check=True, force_live=args.force_live): u
            for u, sha in url_expected.items()
        }
        done = 0
        for fut in as_completed(futs):
            done += 1
            cu, obs_sha, sz, fp = fut.result()
            blob_map[cu] = (obs_sha, sz, fp)
            if done % 150 == 0 or done == len(url_expected):
                print(f"  fetched/verified {done}/{len(url_expected)} URLs ({time.time() - t_start:.1f}s)", flush=True)

    # 2. Live uncached cross-domain spot-check (1 URL per unique host, double-fetch)
    if not args.skip_spot_check:
        by_host_pick: dict[str, str] = {}
        for cu in sorted(blob_map):
            h = (urlsplit(cu).hostname or "").lower()
            if h not in by_host_pick:
                by_host_pick[h] = cu
        print(f"Running live uncached byte-stability spot-check across all {len(by_host_pick)} source domains...", flush=True)
        for h, cu in sorted(by_host_pick.items()):
            exp_sha = blob_map[cu][0]
            _fetch_one_url(cu, exp_sha, args.cache, double_check=True, force_live=True)
            print(f"  LIVE OK: {h} ({exp_sha[:12]}…)", flush=True)

    # 3. Parse all unique blobs in parallel via canonical `load_dataset()`
    print(f"Parsing {len(blob_map)} unique verified artifacts in parallel ({args.parse_workers} workers)...", flush=True)
    parsed_cache_dir = str(args.cache / "parsed_pages")
    parse_jobs = [(u, sha, str(fp), parsed_cache_dir) for u, (sha, _sz, fp) in blob_map.items()]
    parsed_records: dict[str, list[dict[str, Any]]] = {}
    url_info: dict[str, tuple[str, str, int, int]] = {}  # url -> (sha, fmt, bytes, pages)

    with ProcessPoolExecutor(max_workers=args.parse_workers) as pex:
        done = 0
        for url, sha, fmt, bsz, pages, recs in pex.map(_parse_blob_worker, parse_jobs, chunksize=8):
            done += 1
            parsed_records[url] = recs
            url_info[url] = (sha, fmt, bsz, pages)
            if done % 150 == 0 or done == len(parse_jobs):
                print(f"  parsed {done}/{len(parse_jobs)} artifacts ({time.time() - t_start:.1f}s)", flush=True)

    # 4. Deterministic CAG audit re-extraction, statutory date/token fixes & OROP cleanup
    print("Refining CAG audit findings, statutory launch dates, and OROP metadata...", flush=True)
    selected_refined = _refine_all_records(selected_raw, parsed_records)

    # 5. Build strict P8 tasks and dry-run EVERY SINGLE TASK through `run_engine("P8", ...)`
    print(f"Assembling & dry-running all {TARGET_TASKS} tasks through deterministic P8 engine...", flush=True)
    tasks: list[dict[str, Any]] = []
    used_task_ids: set[str] = set()
    used_case_keys: set[str] = set()
    used_titles: set[str] = set()
    dry_leads = 0
    dry_cleared = 0
    dry_unevaluable = 0
    by_signal: Counter[str] = Counter()
    by_severity: Counter[str] = Counter()

    for idx, raw in enumerate(selected_refined, start=1):
        task = _make_clean_task(
            raw,
            seq_num=idx,
            url_info=url_info,
            parsed_records=parsed_records,
            used_task_ids=used_task_ids,
            used_case_keys=used_case_keys,
            used_titles=used_titles,
        )
        p = task["payload"]
        pri_url = p["retrieval_url"]
        sec_url = p.get("secondary_url")
        pri_recs = parsed_records[pri_url]
        if sec_url and sec_url != pri_url:
            sec_sha, sec_fmt, _, _ = url_info[sec_url]
            combined = [{**rec, "document": "primary"} for rec in pri_recs] + [
                {**rec, "document": "secondary", "secondary_sha256": sec_sha, "secondary_format": sec_fmt}
                for rec in parsed_records[sec_url]
            ]
        else:
            combined = pri_recs

        res = run_engine(METHODOLOGY_CODE, combined, p["context"], as_of_date=AS_OF_DATE)
        if res.unevaluable_count > 0:
            dry_unevaluable += res.unevaluable_count
            raise RuntimeError(f"FAIL-CLOSED: Task #{idx} {task['id']} evaluated as unevaluable: {res.summary}")
        if res.leads:
            dry_leads += len(res.leads)
            lead = res.leads[0]
            sig = str((lead.anomaly_metrics or {}).get("signal_category") or lead.rule_triggered or "flagged")
            by_signal[sig] += 1
            by_severity[str(lead.severity)] += 1
        elif res.cleared_count > 0:
            dry_cleared += res.cleared_count
        else:
            raise RuntimeError(f"Task #{idx} {task['id']} produced neither lead nor cleared verdict: {res.summary}")

        tasks.append(task)
        if idx % 250 == 0 or idx == TARGET_TASKS:
            print(
                f"  dry-run {idx}/{TARGET_TASKS}: leads={dry_leads}, cleared={dry_cleared}, unevaluable={dry_unevaluable}",
                flush=True,
            )

    assert len(tasks) == TARGET_TASKS
    assert dry_unevaluable == 0
    assert tasks[0]["id"] == "p8-union-sgb-anchor-0001"
    assert tasks[0]["payload"]["context"]["sovereign_lies"]["is_sgb_anchor"] is True

    # 6. Aggregate coverage.json & documents.csv (with multi-jurisdiction & dominant-mechanism aggregation)
    by_jlevel: dict[str, dict[str, int]] = {
        lvl: {"tasks": 0, "cleared_controls": 0} for lvl in ("union", "state", "ut")
    }
    by_mechanism: dict[str, dict[str, int]] = {
        m: {"tasks": 0} for m in (*SIGNAL_CATEGORIES, "grandfathered_or_honored")
    }
    by_impact: dict[str, int] = {imp: 0 for imp in sorted(VALID_RETROACTIVE_IMPACTS)}
    by_category: dict[str, dict[str, Any]] = {
        cat: {"category": cat, "label": lbl, "tasks": 0}
        for cat, lbl in SCHEME_CATEGORY_LABELS.items()
    }
    jurisdictions_map: dict[str, dict[str, Any]] = {}
    scheme_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    doc_usage: Counter[str] = Counter()
    doc_meta: dict[str, dict[str, Any]] = {}
    sgb_tasks_count = 0
    cleared_controls = 0
    sgb_anchor_case: dict[str, Any] | None = None

    for task in tasks:
        p = task["payload"]
        s = p["context"]["sovereign_lies"]
        jlvl = s["jurisdiction_level"]
        jur = s["jurisdiction"]
        mech = s["walkback_mechanism"]
        imp = s["retroactive_impact"]
        cat = s["scheme_category"]
        sslug = s["scheme_slug"]

        by_jlevel[jlvl]["tasks"] += 1
        if mech == "grandfathered_or_honored":
            by_jlevel[jlvl]["cleared_controls"] += 1
            cleared_controls += 1
        by_mechanism[mech]["tasks"] += 1
        by_impact[imp] += 1
        by_category[cat]["tasks"] += 1

        if sslug == "sovereign-gold-bond" or s["is_sgb_anchor"]:
            sgb_tasks_count += 1

        jentry = jurisdictions_map.setdefault(
            jur,
            {
                "jurisdiction": jur,
                "jurisdiction_level": jlvl,
                "state_or_ut": s["state_or_ut"],
                "tasks": 0,
                "cleared_controls": 0,
            },
        )
        jentry["tasks"] += 1
        if mech == "grandfathered_or_honored":
            jentry["cleared_controls"] += 1

        scheme_groups[sslug].append(task)

        if s["is_sgb_anchor"] and sgb_anchor_case is None:
            pdt = _parse_iso_date(s["promise_date"])
            wdt = _parse_iso_date(s["walkback_date"])
            days_between = (wdt - pdt).days if (pdt and wdt and wdt >= pdt) else None
            sgb_anchor_case = {
                "task_id": task["id"],
                "finding_id": None,
                "scheme_name": s["scheme_name"],
                "tranche_or_phase": s["tranche_or_phase"],
                "jurisdiction": jur,
                "authority": s["authority"],
                "promise_date": s["promise_date"],
                "promise_summary": s["promise_summary"],
                "promise_document_ref": s["promise_document_ref"],
                "walkback_date": s["walkback_date"],
                "walkback_summary": s["walkback_summary"],
                "walkback_document_ref": s["walkback_document_ref"],
                "walkback_mechanism": mech,
                "retroactive_impact": imp,
                "promised_metric": s["promised_metric"],
                "revised_metric": s["revised_metric"],
                "affected_group": s["affected_group"],
                "days_from_promise_to_walkback": days_between,
                "source_url": p["retrieval_url"],
                "secondary_url": p.get("secondary_url"),
                "portal_url": p["source_landing_page"],
            }

        for ukey in ("retrieval_url", "secondary_url"):
            u = p.get(ukey)
            if not u:
                continue
            doc_usage[u] += 1
            if u not in doc_meta:
                sha, fmt, bsz, pages = url_info[u]
                doc_meta[u] = {
                    "document_id": f"p8-doc-{hashlib.sha256(u.encode()).hexdigest()[:12]}",
                    "jurisdiction": jur,
                    "format": fmt,
                    "bytes": bsz,
                    "pages": pages,
                    "sha256": sha,
                    "official_publisher": p["official_publisher"],
                    "retrieval_url": u,
                    "landing_url": p["source_landing_page"],
                }

    schemes_map: dict[str, dict[str, Any]] = {}
    for sslug, grp in scheme_groups.items():
        specs = [t["payload"]["context"]["sovereign_lies"] for t in grp]
        unique_jurs = sorted({s["jurisdiction"] for s in specs})
        lvls = {s["jurisdiction_level"] for s in specs}
        if len(unique_jurs) > 1:
            jur_disp = f"Multi-State ({len(unique_jurs)} States & UTs)"
            jlvl_disp = "state" if "state" in lvls else ("ut" if "ut" in lvls else "union")
        else:
            jur_disp = unique_jurs[0]
            jlvl_disp = specs[0]["jurisdiction_level"]

        # Dominant non-cleared mechanism & impact; grandfathered_or_honored ONLY if 100% cleared
        non_cleared = [s for s in specs if s["walkback_mechanism"] != "grandfathered_or_honored"]
        if non_cleared:
            dom_mech = Counter(s["walkback_mechanism"] for s in non_cleared).most_common(1)[0][0]
            dom_imp = Counter(
                s["retroactive_impact"] for s in non_cleared if s["walkback_mechanism"] == dom_mech
            ).most_common(1)[0][0]
        else:
            dom_mech = "grandfathered_or_honored"
            dom_imp = "grandfathered_or_honored"

        dom_cat = Counter(s["scheme_category"] for s in specs).most_common(1)[0][0]
        schemes_map[sslug] = {
            "scheme_name": _common_scheme_display_name(sslug, grp),
            "scheme_slug": sslug,
            "jurisdiction": jur_disp,
            "jurisdiction_level": jlvl_disp,
            "scheme_category": dom_cat,
            "walkback_mechanism": dom_mech,
            "retroactive_impact": dom_imp,
            "tasks": len(grp),
            "is_sgb_anchor": any(bool(s["is_sgb_anchor"]) for s in specs),
        }

    jurisdictions_sorted = sorted(
        jurisdictions_map.values(),
        key=lambda j: (0 if j["jurisdiction_level"] == "union" else 1, -j["tasks"], j["jurisdiction"].lower()),
    )
    top_schemes_sorted = sorted(
        schemes_map.values(),
        key=lambda x: (0 if x["is_sgb_anchor"] else 1, -x["tasks"], x["scheme_name"].lower()),
    )[:40]
    by_category_sorted = sorted(by_category.values(), key=lambda c: (-c["tasks"], c["category"]))
    states_uts_covered = sum(1 for j in jurisdictions_sorted if j["jurisdiction_level"] in {"state", "ut"})

    dry_run_summary = {
        "tasks_tested": len(tasks),
        "leads": dry_leads,
        "cleared": dry_cleared,
        "unevaluable": dry_unevaluable,
        "by_signal": dict(sorted(by_signal.items())),
        "by_severity": dict(sorted(by_severity.items())),
    }

    coverage: dict[str, Any] = {
        "project_slug": PROJECT_SLUG,
        "project_code": PROJECT_CODE,
        "methodology_code": METHODOLOGY_CODE,
        "hook": HOOK_TEXT,
        "generated_on": datetime.now(timezone.utc).date().isoformat(),
        "as_of_date": AS_OF_DATE,
        "total_tasks": len(tasks),
        "unique_schemes_covered": len(schemes_map),
        "states_and_uts_covered": states_uts_covered,
        "sgb_tasks_count": sgb_tasks_count,
        "cleared_control_tasks_embedded": cleared_controls,
        "documents_count": len(doc_meta),
        "by_jurisdiction_level": by_jlevel,
        "by_mechanism": by_mechanism,
        "by_retroactive_impact": by_impact,
        "by_category": by_category_sorted,
        "jurisdictions": jurisdictions_sorted,
        "top_schemes": top_schemes_sorted,
        "sgb_anchor_case": sgb_anchor_case,
        "dry_run_summary": dry_run_summary,
    }

    project_obj: dict[str, Any] = {
        "slug": PROJECT_SLUG,
        "project_code": PROJECT_CODE,
        "priority": 8,
        "title": PROJECT_TITLE,
        "summary": (
            f"{HOOK_TEXT} Audits 1,000 SHA-256-pinned Union and State/UT government scheme commitments "
            f"across all {states_uts_covered} States & UTs and {len(schemes_map)} schemes — comparing original "
            "statutory/notification launch promises against subsequent tax reversals, benefit cuts, eligibility "
            "narrowing, exit restrictions, arrears, or discontinuations, anchored by the Sovereign Gold Bond (SGB) Scheme."
        ),
        "posted_by": {
            "name": "AI Watchdogs India",
            "handle": "@aiwatchdogsIN",
            "url": "https://aiwatchdogs.in",
        },
        "pillar": "scheme",
        "status": "active",
        "methodology_code": METHODOLOGY_CODE,
        "jurisdiction": f"India (Union of India & All {states_uts_covered} States/UTs)",
        "authority": "Union Ministries, Reserve Bank of India, CAG of India, and State/UT Finance & Welfare Departments",
        "statutory_anchors": [
            "Income-tax Act 1961 Sec 47(viic) & Income-tax Act 2025 Sec 70(1)(x) (Finance Bill 2026 Clause 35 — SGB)",
            "Constitution of India Arts 112-114, 148-151 & 202-203 (Budgetary, Statutory & CAG Accountability)",
            "Government Securities Act 2006 & Sovereign Gold Bond Scheme Notifications",
            "Finance Acts (2016-2026), Union & State Budgets, CAG Audit Reports & Parliamentary Records",
            "RTI Act 2005 Sec 4(1)(b) & Sec 6(1) (Proactive Disclosure & Public Record Access)",
        ],
        "scope": [
            f"1,000 real, SHA-256-pinned public scheme commitment checks across the Union of India ({by_jlevel['union']['tasks']} tasks) and all {states_uts_covered} States & UTs ({by_jlevel['state']['tasks']} State + {by_jlevel['ut']['tasks']} UT tasks)",
            "Anchor case Task #1 (p8-union-sgb-anchor-0001): Sovereign Gold Bond (SGB) Scheme — Finance Bill 2016 Sec 47(viic) redemption tax exemption vs Finance Bill 2026 Clause 35 primary-subscriber-only walk-back, plus all 67/67 SGB tranches",
            "Covers retrospective tax reversals, mid-stream benefit cuts, premature discontinuations, eligibility narrowing, lock-in/exit restrictions, and documented unfunded arrears",
            f"Includes {cleared_controls} verified grandfathered/honored benchmark tasks published as Cleared with equal standing",
        ],
        "out_of_scope": [
            "Informal political speeches or manifesto pledges lacking official Budget, Gazette, RBI, NSE, Parliamentary, or CAG documentary records",
            "Dynamic non-byte-stable web pages whose cryptographic SHA-256 drifts across live fetches",
            "Personal beneficiary identifiers, Aadhaar, PAN, phone numbers, or personal emails (scrubbed per DPDP Act 2023)",
        ],
        "sources": OFFICIAL_PROJECT_SOURCES,
        "tasks": tasks,
    }

    # 7. Run hard automated quality gates + schema + BNS Sec 356 + DPDP PII checks before writing anything
    print("Running automated quality gates, schema validation, BNS neutrality & DPDP PII checks...", flush=True)
    _run_quality_gates(tasks, coverage, parsed_records)

    val_errs = validate_project(project_obj, filename_stem=PROJECT_SLUG)
    if val_errs:
        raise RuntimeError(f"Generated project failed validate_project ({len(val_errs)} errors): {val_errs[:10]}")

    for label, blob in (
        ("coverage.json", json.dumps(coverage, ensure_ascii=False)),
        ("project.json", json.dumps(project_obj, ensure_ascii=False)),
    ):
        bad = scan_forbidden_terms(blob)
        if bad:
            raise RuntimeError(f"BNS Sec 356 neutrality violation in {label}: {bad}")
    has_pii_cov, pii_cov = contains_pii(coverage)
    if has_pii_cov:
        raise RuntimeError(f"DPDP PII detected in coverage.json: {pii_cov}")

    # 8. Write all 4 project artifacts
    PROJECT_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    header_obj = {k: v for k, v in project_obj.items() if k != "tasks"}
    header_json = json.dumps(header_obj, indent=2, ensure_ascii=False)
    lines = [header_json[:-2] + ',\n  "tasks": [']
    for i, t in enumerate(tasks):
        suffix = "," if i + 1 < len(tasks) else ""
        lines.append("    " + json.dumps(t, separators=(",", ":"), ensure_ascii=False) + suffix)
    lines.append("  ]\n}\n")
    PROJECT_JSON_PATH.write_text("\n".join(lines), encoding="utf-8")

    (PROJECT_ASSETS_DIR / "coverage.json").write_text(
        json.dumps(coverage, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    doc_rows = []
    for u, meta in sorted(doc_meta.items(), key=lambda kv: (-doc_usage[kv[0]], kv[0])):
        doc_rows.append(
            {
                "document_id": meta["document_id"],
                "tasks_assigned": doc_usage[u],
                "jurisdiction": meta["jurisdiction"],
                "format": meta["format"],
                "bytes": meta["bytes"],
                "pages": meta["pages"],
                "sha256": meta["sha256"],
                "official_publisher": meta["official_publisher"],
                "retrieval_url": u,
                "landing_url": meta["landing_url"],
            }
        )
    csv_path = PROJECT_ASSETS_DIR / "documents.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(doc_rows[0].keys()))
        writer.writeheader()
        for row in doc_rows:
            writer.writerow({k: sanitize_csv_cell(v) for k, v in row.items()})

    host_counts = Counter((urlsplit(u).hostname or "").lower() for u in doc_meta)
    hosts_summary = sorted(host_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    _write_methodology_md(coverage, hosts_summary)

    # Final safety scan on written CSV & METHODOLOGY.md
    for fpath in (csv_path, PROJECT_ASSETS_DIR / "METHODOLOGY.md"):
        txt = fpath.read_text(encoding="utf-8")
        bad = scan_forbidden_terms(txt)
        if bad:
            raise RuntimeError(f"BNS Sec 356 violation in {fpath}: {bad}")
        hp, pt = contains_pii(txt)
        if hp:
            raise RuntimeError(f"DPDP PII violation in {fpath}: {pt}")

    print(
        f"\nSUCCESS in {time.time() - t_start:.1f}s!\n"
        f"  Project: {PROJECT_JSON_PATH} ({PROJECT_JSON_PATH.stat().st_size / 1e6:.2f} MB)\n"
        f"  Tasks: {len(tasks)} (Union={by_jlevel['union']['tasks']}, State={by_jlevel['state']['tasks']}, UT={by_jlevel['ut']['tasks']})\n"
        f"  States & UTs covered: {states_uts_covered} (28 States + 8 UTs)\n"
        f"  Unique schemes: {len(schemes_map)} | SGB cohort: {sgb_tasks_count} | Unique pinned URLs: {len(doc_meta)}\n"
        f"  Dry-run: leads={dry_leads}, cleared={dry_cleared}, unevaluable={dry_unevaluable}\n"
        f"  Mechanisms: {json.dumps({k: v['tasks'] for k, v in by_mechanism.items()})}\n"
        f"  By severity: {json.dumps(dict(by_severity))}"
    )


if __name__ == "__main__":
    main()
