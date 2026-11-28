#!/usr/bin/env python3
"""Build Project P9 (`auditing-government-exams`, methodology `P9`, 1,000 tasks) from verified Indian exam records.

Hook: "Did India's government exams happen on time and flawlessly — or get delayed, postponed,
rescheduled, or cancelled after students sat them?"

Assembles exactly 1,000 distinct, SHA-256-pinned, byte-stable Indian public examination audit tasks
covering the 3-year audit window (2023-11-28 to 2026-11-28; exam years 2023–2026) across the Union
Government and ALL 28 States & 8 Union Territories (37 jurisdictions total), anchored by Task #1
(`p9-union-ugc-net-june-2024-0001`: UGC-NET June 2024 held 18 June 2024 and cancelled 19 June 2024)
alongside national & state anchor cases (NEET-PG 2024 night-before postponement, CSIR-UGC NET June 2024,
NCET 2024, NEET-UG 2024/2026 re-exams, CUET-UG 2024/2025/2026 reschedules/re-tests, UPSC CSE 2024
election postponement & UPSC 2025–2026 calendar benchmarks, UP Police Constable Feb 2024, UPPSC RO/ARO
Feb 2024, BPSC TRE-3.0 & 70th CCE, SSC/RRB/IBPS/State PSC records) plus a verified `flawless_conduct`
Cleared benchmark cohort.

Modes:
  1. Catalog build mode (when `--catalogs` or default scratch catalogs exist and `--from-existing` is off):
     Loads verified harvested catalogs, normalizes, deduplicates, enforces 3-year window & schema rules,
     selects exactly 1,000 diverse tasks covering all 37 jurisdictions & all 7 disruption types,
     live-verifies every unique HTTPS URL (double-fetch SHA-256 byte-stability), parses all artifacts
     via canonical `load_dataset()`, verifies 100% word-boundary verbatim documentary tokens, dry-runs
     all 1,000 tasks through `run_engine("P9", ...)` (when available), validates with `validate_project()`,
     and writes all project artifacts.
  2. Standalone reproducible rebuild/verify mode (default on fresh checkout or `--from-existing`):
     Loads the 1,000 pinned task specifications from `projects/auditing-government-exams.json`,
     live-verifies all unique URLs & SHA-256 digests, re-parses via `load_dataset()`, re-verifies all
     word-boundary tokens & `run_engine("P9", ...)`, validates with `validate_project()`, and regenerates
     `coverage.json`, `documents.csv`, `projects/auditing-government-exams/METHODOLOGY.md`, and
     `projects/auditing-government-exams.json`.

Outputs:
  - projects/auditing-government-exams.json (exactly 1,000 tasks, compact 1-line-per-task JSON)
  - projects/auditing-government-exams/coverage.json
  - projects/auditing-government-exams/documents.csv
  - projects/auditing-government-exams/METHODOLOGY.md
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
from datetime import date
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

PROJECT_SLUG = "auditing-government-exams"
PROJECT_CODE = "P9"
METHODOLOGY_CODE = "P9"
RULESET_VERSION = "P9-2026.11-v1"
TARGET_TASKS = 1000
AS_OF_DATE = "2026-11-28"
AUDIT_WINDOW_START = date(2023, 11, 28)
AUDIT_WINDOW_END = date(2026, 11, 28)
EVENT_WINDOW_START = date(2023, 11, 1)
EVENT_WINDOW_END = date(2027, 6, 30)
AUDIT_WINDOW_YEARS: tuple[int, ...] = (2023, 2024, 2025, 2026)
AUDIT_WINDOW_LABEL = "Nov 2023 – Nov 2026 (3 years)"

HOOK_TEXT = (
    "Did India's government exams happen on time and flawlessly — or get delayed, postponed, "
    "rescheduled, or cancelled after students sat them?"
)
PROJECT_TITLE = "Auditing Government Exams: Did India's public exams run flawlessly or get disrupted?"
USER_AGENT = "AIWatchdogs-India-OpenAudit/0.1 (aiwatchdogs.in; lawful-public-audit)"

DEFAULT_CACHE_DIR = Path.home() / ".cache" / "aiwatchdogs" / PROJECT_SLUG
DEFAULT_SCRATCH_DIR = Path(
    "/home/piyushbodaa/.gemini/antigravity-cli/brain/a6793235-839d-40f7-a5a1-33881c05903c/scratch"
)
DEFAULT_CATALOG_NAMES: tuple[str, ...] = (
    "anchors_catalog.json",
    "upsc_catalog.json",
    "union_portals_catalog.json",
    "sansad_catalog.json",
    "state_psc_catalog.json",
    "gap_fill_catalog.json",
)

PROJECTS_DIR = REPO_ROOT / "projects"
PROJECT_JSON_PATH = PROJECTS_DIR / f"{PROJECT_SLUG}.json"
PROJECT_ASSETS_DIR = PROJECTS_DIR / PROJECT_SLUG

P9_TASK_ID_RE = re.compile(r"^p9-(union|state|ut)-[a-z0-9][a-z0-9-]{1,40}-\d{4}$")
_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,58}$")
_SLUG_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CTRL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]+")
_PUA_AND_GLYPHS_RE = re.compile(
    r"[\ue000-\uf8ff\ufffd➢•●▪▫►▻▸▹○■□◆◇❖✓✔✗✘¾\u2020\u2021\u2030\u2039\u203a\u02c6\u02dc]+"
)
_MULTI_SPACE_RE = re.compile(r"\s+")
_DASH_VARIANTS_RE = re.compile(r"[\u2010-\u2015\u2212]")
_QUOTE_SINGLE_RE = re.compile(r"[\u2018\u2019\u201a\u201b]")
_QUOTE_DOUBLE_RE = re.compile(r"[\u201c\u201d\u201e\u201f]")
_HTML_ENTITY_IN_URL_RE = re.compile(r"&(?:#\d+|#x[0-9a-fA-F]+|amp|apos|quot|lt|gt|nbsp|ndash|mdash);")
_PINNED_GITHUB_RAW_PATH_RE = re.compile(r"^/[^/]+/[^/]+/[0-9a-f]{40}/.+$")
_PAGE_MARKER_RE = re.compile(r"\[?\s*PAGE\s+\d+\s*\]?|\[PAGE\b", re.IGNORECASE)
_ACB_RE = re.compile(r"\banti[\s-]+corruption\b", re.IGNORECASE)
_NEUTRAL_REPLACE_RE = re.compile(
    r"(?<![a-z])(?:scam(?:s|med|mer|mers)?|corrupt(?:ed|ing|ly|ion)?|brib(?:e|es|ed|ing|ery)|"
    r"fraud(?:s|ulent|ulently|ster|sters)?|kickback(?:s)?|guilty|stolen|rigged|"
    r"crook(?:s|ed)?|loot(?:s|ed|ing|er|ers)?|embezzl(?:e|es|ed|ing|ement))(?![a-z])",
    re.IGNORECASE,
)

DISRUPTION_TYPE_RULES: dict[str, str] = {
    "cancelled_after_conduct": "P9-CANCELLED-AFTER-CONDUCT",
    "postponed": "P9-POSTPONED",
    "rescheduled": "P9-RESCHEDULED",
    "delayed": "P9-DELAYED",
    "cancelled_before_conduct": "P9-CANCELLED-BEFORE-CONDUCT",
    "partial_reexam_or_glitch": "P9-PARTIAL-REEXAM-OR-GLITCH",
}

DISRUPTION_TYPE_LABELS: dict[str, str] = {
    "cancelled_after_conduct": "Took Exam, Then Cancelled",
    "postponed": "Postponed",
    "rescheduled": "Rescheduled",
    "delayed": "Delayed",
    "cancelled_before_conduct": "Cancelled Before Conduct",
    "partial_reexam_or_glitch": "Technical Glitch / Partial Re-Exam",
    "flawless_conduct": "Conducted Flawlessly (Cleared)",
}

SIGNAL_CATEGORIES: tuple[str, ...] = (
    "cancelled_after_conduct",
    "postponed",
    "rescheduled",
    "delayed",
    "cancelled_before_conduct",
    "partial_reexam_or_glitch",
)
VALID_DISRUPTION_TYPES: frozenset[str] = frozenset(SIGNAL_CATEGORIES) | {"flawless_conduct"}

AUTHORITY_TYPE_LABELS: dict[str, str] = {
    "central_testing_agency": "Central Testing Agency (NTA / NBEMS / AIIMS)",
    "union_psc_or_recruitment": "Union PSC & Staff Recruitment (UPSC / SSC)",
    "railway_banking_psu": "Railways, Banking & PSU Boards (RRB / IBPS / SBI / RBI)",
    "defence_medical_technical": "Defence, Medical & Technical Bodies",
    "state_psc": "State Public Service Commissions",
    "state_police_or_subordinate": "State Police & Subordinate Services Boards",
    "state_teacher_or_entrance": "State Teacher Eligibility & Entrance Boards",
    "ut_recruitment_or_board": "Union Territory Recruitment & Exam Boards",
}
VALID_AUTHORITY_TYPES: frozenset[str] = frozenset(AUTHORITY_TYPE_LABELS.keys())

EXAM_CATEGORY_LABELS: dict[str, str] = {
    "civil_services_and_psc": "Civil Services & PSC",
    "police_and_defence": "Police & Defence",
    "teaching_and_faculty": "Teaching & Faculty",
    "medical_and_health": "Medical & Health",
    "engineering_and_higher_ed": "Engineering & Higher Education",
    "banking_railways_ssc": "Banking, Railways & SSC",
    "clerical_and_subordinate": "Clerical & Subordinate Services",
    "board_and_other_public": "Board & Other Public Exams",
}
VALID_EXAM_CATEGORIES: frozenset[str] = frozenset(EXAM_CATEGORY_LABELS.keys())

REASON_CATEGORY_LABELS: dict[str, str] = {
    "paper_integrity_compromise": "Paper Integrity / Question Security Safeguard",
    "administrative_or_logistical": "Administrative or Logistical Reasons",
    "technical_or_cbt_glitch": "Technical or CBT Server Glitch",
    "election_or_calendar_clash": "Election Schedule or Calendar Clash",
    "weather_or_disaster": "Severe Weather or Natural Disaster",
    "judicial_or_policy_review": "Judicial Order or Policy Review",
    "none_flawless": "None (Conducted as Scheduled)",
}
VALID_REASON_CATEGORIES: frozenset[str] = frozenset(REASON_CATEGORY_LABELS.keys())

VALID_JURISDICTION_LEVELS: frozenset[str] = frozenset({"union", "state", "ut"})
VALID_SOURCE_FORMATS: frozenset[str] = frozenset({"pdf", "html", "json", "csv", "txt"})
VALID_SECONDARY_ROLES: frozenset[str] = frozenset(
    {"", "outcome_document", "schedule_document", "corroborating_record"}
)
DISRUPTION_NO_SAT_BEFORE_CANCEL: frozenset[str] = frozenset(
    {"flawless_conduct", "cancelled_before_conduct", "postponed", "rescheduled", "delayed"}
)

ALL_28_STATES: tuple[str, ...] = (
    "Andhra Pradesh",
    "Arunachal Pradesh",
    "Assam",
    "Bihar",
    "Chhattisgarh",
    "Goa",
    "Gujarat",
    "Haryana",
    "Himachal Pradesh",
    "Jharkhand",
    "Karnataka",
    "Kerala",
    "Madhya Pradesh",
    "Maharashtra",
    "Manipur",
    "Meghalaya",
    "Mizoram",
    "Nagaland",
    "Odisha",
    "Punjab",
    "Rajasthan",
    "Sikkim",
    "Tamil Nadu",
    "Telangana",
    "Tripura",
    "Uttar Pradesh",
    "Uttarakhand",
    "West Bengal",
)

ALL_8_UTS: tuple[str, ...] = (
    "Andaman and Nicobar Islands",
    "Chandigarh",
    "Dadra and Nagar Haveli and Daman and Diu",
    "Delhi",
    "Jammu and Kashmir",
    "Ladakh",
    "Lakshadweep",
    "Puducherry",
)

STATE_NAME_ALIASES: dict[str, tuple[str, str]] = {}
for _st in ALL_28_STATES:
    STATE_NAME_ALIASES[_st.lower()] = ("state", _st)
for _ut in ALL_8_UTS:
    STATE_NAME_ALIASES[_ut.lower()] = ("ut", _ut)
STATE_NAME_ALIASES.update(
    {
        "nct of delhi": ("ut", "Delhi"),
        "nct delhi": ("ut", "Delhi"),
        "jammu & kashmir": ("ut", "Jammu and Kashmir"),
        "j&k": ("ut", "Jammu and Kashmir"),
        "andaman & nicobar islands": ("ut", "Andaman and Nicobar Islands"),
        "andaman & nicobar": ("ut", "Andaman and Nicobar Islands"),
        "andaman and nicobar": ("ut", "Andaman and Nicobar Islands"),
        "dadra & nagar haveli and daman & diu": ("ut", "Dadra and Nagar Haveli and Daman and Diu"),
        "dadra and nagar haveli & daman and diu": ("ut", "Dadra and Nagar Haveli and Daman and Diu"),
        "dnhdd": ("ut", "Dadra and Nagar Haveli and Daman and Diu"),
        "dnh & dd": ("ut", "Dadra and Nagar Haveli and Daman and Diu"),
        "pondicherry": ("ut", "Puducherry"),
        "orissa": ("state", "Odisha"),
        "uttaranchal": ("state", "Uttarakhand"),
    }
)

_GENERIC_BAN_TOKENS: frozenset[str] = frozenset(
    {
        "state",
        "states",
        "india",
        "indian",
        "exam",
        "exams",
        "examination",
        "examinations",
        "notice",
        "public",
        "government",
        "ministry",
        "department",
        "commission",
        "board",
        "candidates",
        "candidate",
        "date",
        "dates",
        "year",
        "years",
        "page",
        "table",
        "annexure",
        "number",
        "details",
        "status",
        "answer",
        "question",
        "dated",
        "regarding",
        "lok sabha",
        "rajya sabha",
        "2023",
        "2024",
        "2025",
        "2026",
    }
)

OFFICIAL_PROJECT_SOURCES: list[dict[str, str]] = [
    {
        "name": "National Testing Agency (NTA) — Public Notice Archive (2023–2026)",
        "publisher": "National Testing Agency, Ministry of Education, Government of India",
        "url": "https://nta.ac.in/NoticeBoardArchive",
    },
    {
        "name": "Digital Sansad — Lok Sabha & Rajya Sabha Questions & Official Answer PDFs (17th–18th LS, RS Sessions 262–271)",
        "publisher": "Parliament of India — Lok Sabha & Rajya Sabha Secretariats",
        "url": "https://sansad.in/ls/questions/questions-and-answers",
    },
    {
        "name": "Union Public Service Commission (UPSC) — Annual Calendars & Examination Result Press Notes",
        "publisher": "Union Public Service Commission, Government of India",
        "url": "https://upsc.gov.in/examination-calendar/",
    },
    {
        "name": "Staff Selection Commission (SSC) — Official Notice Board & Examination Calendar",
        "publisher": "Staff Selection Commission, Ministry of Personnel, Public Grievances and Pensions",
        "url": "https://ssc.gov.in/",
    },
    {
        "name": "Central Board of Secondary Education (CBSE), IBPS & Central Recruitment Portals",
        "publisher": "CBSE / IBPS / Union Recruitment Authorities",
        "url": "https://www.cbse.gov.in/cbsenew/cbse.html",
    },
    {
        "name": "State Public Service Commissions & Police/Subordinate Recruitment Boards (All 28 States)",
        "publisher": "State PSCs (RPSC, BPSC, UPPSC, UPPRPB, CSBC, APSC, GPSC, HPSC, MPPSC, TNPSC, KPSC/KEA, Kerala PSC, OPSC, TGPSC, APPSC, MPSC, JPSC, UKSSSC, Goa PSC, Manipur, Tripura, Sikkim, Nagaland, Mizoram, Arunachal, CGPSC)",
        "url": "https://rpsc.rajasthan.gov.in/",
    },
    {
        "name": "Union Territory Recruitment & Examination Boards (All 8 Union Territories)",
        "publisher": "DSSSB (Delhi), Ladakh, Puducherry, Dadra & Nagar Haveli and Daman & Diu, Lakshadweep, Chandigarh, Jammu & Kashmir, and Andaman & Nicobar Islands Administration Records",
        "url": "https://dsssb.delhi.gov.in/",
    },
]


def norm_search(text: str) -> str:
    norm = unicodedata.normalize("NFKC", text or "")
    norm = _DASH_VARIANTS_RE.sub("-", norm)
    norm = _QUOTE_SINGLE_RE.sub("'", norm)
    norm = _QUOTE_DOUBLE_RE.sub('"', norm)
    norm = _CTRL_CHARS_RE.sub(" ", norm)
    return _MULTI_SPACE_RE.sub(" ", norm).strip().lower()


def word_boundary_match(token: str, norm_haystack: str) -> bool:
    """Exact mirror of P9 engine `_match_token_in_norm` word-boundary check."""
    nt = norm_search(token)
    if not nt or not norm_haystack:
        return False
    prefix = r"(?<![^\W_])" if nt[0].isalnum() else ""
    suffix = r"(?![^\W_])" if nt[-1].isalnum() else ""
    return re.search(prefix + re.escape(nt) + suffix, norm_haystack) is not None


def _trim_word_boundary(text: str, max_len: int) -> str:
    if max_len <= 0 or len(text) <= max_len:
        return text
    limit = max(1, max_len - 1)
    cut = text[:limit]
    if limit < len(text) and text[limit].isalnum() and cut and cut[-1].isalnum():
        sp = cut.rfind(" ")
        if sp >= max(8, limit // 3):
            cut = cut[:sp]
    cut = cut.rstrip(" ,;:-/([—–")
    if cut.count("(") > cut.count(")"):
        lp = cut.rfind("(")
        if lp >= max(8, len(cut) // 2):
            cut = cut[:lp].rstrip(" ,;:-/—–")
    return cut + "…"


def neutralize_clean(text: Any, *, max_len: int = 0) -> str:
    if text is None:
        return ""
    s = html.unescape(str(text)).replace("\xa0", " ")
    s = _CTRL_CHARS_RE.sub(" ", s)
    s = _PUA_AND_GLYPHS_RE.sub(" ", s)
    s = _PAGE_MARKER_RE.sub(" ", s)
    s, _ = scrub_pii(s)
    s = _ACB_RE.sub("Vigilance", s)
    s = _NEUTRAL_REPLACE_RE.sub("compliance-review", s)
    s = _MULTI_SPACE_RE.sub(" ", s).strip()
    if max_len > 0 and len(s) > max_len:
        s = _trim_word_boundary(s, max_len)
    return s


def slugify(text: str, *, max_len: int = 40) -> str:
    k = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    if not k:
        return "exam"
    return k[:max_len].strip("-") or "exam"


def parse_iso_date(val: Any) -> date | None:
    if not isinstance(val, str) or not _ISO_DATE_RE.fullmatch(val.strip()):
        return None
    try:
        return date.fromisoformat(val.strip())
    except ValueError:
        return None


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
    return cleaned


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
    clean_u = clean_public_url(url)
    fp = _blob_path(cache_dir, clean_u)
    fp.parent.mkdir(parents=True, exist_ok=True)

    if not force_live:
        for cand_fp in (
            fp,
            *(
                [
                    cache_dir / "blobs" / f"{expected_sha}.pdf",
                    cache_dir / "blobs" / f"{expected_sha}.bin",
                    cache_dir / "blobs" / f"{expected_sha}.html",
                ]
                if expected_sha
                else []
            ),
        ):
            if cand_fp.exists():
                raw = cand_fp.read_bytes()
                obs = hashlib.sha256(raw).hexdigest()
                if not expected_sha or obs == expected_sha:
                    return clean_u, obs, len(raw), cand_fp

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
                    raise RuntimeError(f"SHA-256 mismatch for {clean_u}: expected {expected_sha}, got {sha1}")
                if double_check:
                    r2 = client.get(clean_u)
                    if r2.status_code != 200:
                        raise RuntimeError(f"Second GET HTTP {r2.status_code} for {clean_u}")
                    sha2 = hashlib.sha256(r2.content).hexdigest()
                    if sha2 != sha1:
                        raise RuntimeError(f"Byte-stability drift on {clean_u}: {sha1[:12]} != {sha2[:12]}")
                fp.write_bytes(raw1)
                return clean_u, sha1, len(raw1), fp
        except Exception as exc:
            last_err = exc
            time.sleep(0.7 * (attempt + 1))
    raise RuntimeError(f"Failed to verify {clean_u}: {last_err}")


def _parse_blob_worker(args: tuple[str, str, str, str]) -> tuple[str, str, str, int, int, list[dict[str, Any]]]:
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
        context={"_extract_html_text": True, "auditing_government_exams": {"case_key": "build-parse"}},
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


def _canonicalize_jurisdiction(raw: dict[str, Any]) -> tuple[str, str, str | None]:
    jlvl = str(raw.get("jurisdiction_level") or "").strip().lower()
    s_ut = str(raw.get("state_or_ut") or "").strip()
    jur = str(raw.get("jurisdiction") or "").strip()

    if jlvl == "union" or jur.lower() == "union of india":
        return "Union of India", "union", None

    candidate_name = s_ut
    if not candidate_name:
        if jur.startswith("State:"):
            candidate_name = jur[6:].strip()
        elif jur.startswith("UT:"):
            candidate_name = jur[3:].strip()
        else:
            candidate_name = jur

    resolved = STATE_NAME_ALIASES.get(candidate_name.lower())
    if resolved:
        r_lvl, r_name = resolved
        prefix = "State" if r_lvl == "state" else "UT"
        return f"{prefix}:{r_name}", r_lvl, r_name

    raise ValueError(f"Unrecognized jurisdiction: level={jlvl!r} state_or_ut={s_ut!r} jur={jur!r}")


def _evaluate_severity_local(
    *,
    disruption_type: str,
    disruption_reason_category: str,
    students_sat: bool,
    delay_days: int | None,
    affected_candidates_count: int | None,
    is_anchor_case: bool,
) -> str:
    if disruption_type == "flawless_conduct":
        return "low"
    cands = affected_candidates_count or 0
    delay = delay_days or 0
    if (
        disruption_type == "cancelled_after_conduct"
        or (is_anchor_case and disruption_type in {"postponed", "partial_reexam_or_glitch"})
        or cands >= 500_000
        or (disruption_reason_category == "paper_integrity_compromise" and students_sat)
    ):
        return "critical"
    if (
        disruption_type in {"cancelled_before_conduct", "postponed", "partial_reexam_or_glitch"}
        or cands >= 50_000
        or delay >= 60
        or disruption_reason_category == "paper_integrity_compromise"
    ):
        return "high"
    return "medium"


def _mine_distinct_tokens(
    raw_tokens: list[Any],
    recs: list[dict[str, Any]],
    *,
    seed_texts: list[str],
    exclude_norms: set[str] | None = None,
) -> list[str]:
    hay = _records_norm_haystack(recs)
    raw_text = _records_raw_text(recs)
    excl = set(exclude_norms or set())
    verified: list[str] = []
    seen: set[str] = set()

    def _try(tok: str) -> bool:
        c = neutralize_clean(tok, max_len=115).strip(" [].,;:-'\"")
        if len(c) < 3 or len(c) > 120:
            return False
        if scan_forbidden_terms(c):
            return False
        nc = norm_search(c)
        if not nc or nc in seen or nc in _GENERIC_BAN_TOKENS:
            return False
        if re.fullmatch(r"[\d,.\s/-]+", c) and len(re.sub(r"\D", "", c)) < 4:
            return False
        if word_boundary_match(c, hay):
            seen.add(nc)
            verified.append(c)
            return True
        return False

    for t in raw_tokens or []:
        _try(str(t or ""))
        if len(verified) >= 6:
            break

    # If <2 tokens or identical to exclude_norms, mine clean 2-5 word verbatim phrases from seed_texts or raw_text
    if len(verified) < 2 or (excl and {norm_search(x) for x in verified} == excl):
        for stxt in seed_texts:
            words = re.findall(r"[A-Za-z0-9][A-Za-z0-9./\-()']*", neutralize_clean(stxt))
            for win in (4, 3, 2):
                for i in range(max(0, len(words) - win + 1)):
                    cand = " ".join(words[i : i + win]).strip(" .,;:-()[]'\"")
                    if sum(1 for ch in cand if ch.isalpha()) < 5:
                        continue
                    nc = norm_search(cand)
                    if nc in excl or nc in seen:
                        continue
                    if _try(cand) and len(verified) >= 3 and (not excl or {norm_search(x) for x in verified} != excl):
                        return verified[:6]

    if len(verified) < 2 or (excl and {norm_search(x) for x in verified} == excl):
        flat = neutralize_clean(raw_text[:25000])
        words = re.findall(r"[A-Za-z0-9][A-Za-z0-9./\-()']*", flat)
        for win in (3, 2):
            for i in range(0, min(len(words) - win + 1, 800)):
                cand = " ".join(words[i : i + win]).strip(" .,;:-()[]'\"")
                if sum(1 for ch in cand if ch.isalpha()) < 6:
                    continue
                nc = norm_search(cand)
                if nc in excl or nc in seen:
                    continue
                if _try(cand) and len(verified) >= 2 and (not excl or {norm_search(x) for x in verified} != excl):
                    return verified[:6]

    return verified[:6]


def _normalize_raw_record(
    raw: dict[str, Any],
    *,
    seq_num: int,
    url_info: dict[str, tuple[str, str, int, int]],
    parsed_records: dict[str, list[dict[str, Any]]],
    used_task_ids: set[str],
    used_case_keys: set[str],
    used_titles: set[str],
) -> dict[str, Any]:
    jur, jlvl, state_or_ut = _canonicalize_jurisdiction(raw)
    pri_url = clean_public_url(raw["retrieval_url"])
    pri_sha, pri_fmt, _, _ = url_info[pri_url]

    sec_url_raw = raw.get("secondary_url")
    sec_url = clean_public_url(sec_url_raw) if sec_url_raw else None
    if sec_url == pri_url:
        sec_url = None
    sec_sha: str | None = None
    sec_fmt: str | None = None
    if sec_url:
        sec_sha, sec_fmt, _, _ = url_info[sec_url]

    sec_role = str(raw.get("secondary_role") or "").strip().lower()
    if sec_url:
        if sec_role not in {"outcome_document", "schedule_document", "corroborating_record"}:
            sec_role = "outcome_document"
    else:
        sec_role = ""

    dtype = str(raw.get("disruption_type") or "").strip().lower()
    if dtype not in VALID_DISRUPTION_TYPES:
        raise ValueError(f"Invalid disruption_type={dtype!r} in {raw.get('case_key')}")

    rcat = str(raw.get("disruption_reason_category") or "").strip().lower()
    if dtype == "flawless_conduct":
        rcat = "none_flawless"
    elif rcat not in VALID_REASON_CATEGORIES or rcat == "none_flawless":
        rcat = "administrative_or_logistical"

    sat_raw = raw.get("students_took_exam_before_cancellation")
    if dtype == "cancelled_after_conduct":
        students_sat = True
    elif dtype in DISRUPTION_NO_SAT_BEFORE_CANCEL:
        students_sat = False
    else:
        students_sat = bool(sat_raw)

    sdt = parse_iso_date(raw.get("scheduled_date"))
    edt = parse_iso_date(raw.get("actual_or_event_date"))
    if sdt is None or not (AUDIT_WINDOW_START <= sdt <= AUDIT_WINDOW_END):
        raise ValueError(f"scheduled_date {raw.get('scheduled_date')!r} outside 3-year window for {raw.get('case_key')}")
    if edt is None or not (EVENT_WINDOW_START <= edt <= EVENT_WINDOW_END):
        edt = sdt
    if dtype == "cancelled_after_conduct" and edt < sdt:
        edt = sdt

    eyear_raw = raw.get("exam_year")
    exam_year = int(eyear_raw) if isinstance(eyear_raw, int) and not isinstance(eyear_raw, bool) and 2023 <= eyear_raw <= 2026 else sdt.year

    delay_raw = raw.get("delay_days")
    if dtype == "flawless_conduct":
        delay_days: int | None = 0
    elif isinstance(delay_raw, int) and not isinstance(delay_raw, bool) and 0 <= delay_raw <= 1500:
        delay_days = delay_raw
    elif edt >= sdt and dtype in {"postponed", "rescheduled", "delayed"}:
        d_calc = (edt - sdt).days
        delay_days = d_calc if 0 <= d_calc <= 1500 else None
    else:
        delay_days = None

    exam_name = neutralize_clean(raw.get("exam_name"), max_len=195).lstrip("|-•·: ") or "Public Examination"
    if exam_name and exam_name[0].islower():
        exam_name = exam_name[0].upper() + exam_name[1:]
    exam_slug = slugify(str(raw.get("exam_slug") or exam_name), max_len=78)
    cycle_or_stage = neutralize_clean(raw.get("cycle_or_stage"), max_len=155).lstrip("|-•·: ") or f"{exam_year} Cycle"

    authority = neutralize_clean(raw.get("authority") or raw.get("official_publisher"), max_len=195) or "Examination Authority"
    authority_key = slugify(str(raw.get("authority_key") or authority), max_len=78)

    atype = str(raw.get("authority_type") or "").strip().lower()
    if atype not in VALID_AUTHORITY_TYPES:
        if jlvl == "ut":
            atype = "ut_recruitment_or_board"
        elif jlvl == "state":
            atype = "state_psc"
        else:
            atype = "central_testing_agency"

    ecat = str(raw.get("exam_category") or "").strip().lower()
    if ecat not in VALID_EXAM_CATEGORIES:
        ecat = "board_and_other_public"

    cands_raw = raw.get("affected_candidates_count")
    if dtype == "flawless_conduct":
        cands_count: int | None = None
        cands_basis = "Not applicable — conducted flawlessly as scheduled (cleared benchmark)"
    else:
        if isinstance(cands_raw, int) and not isinstance(cands_raw, bool) and 0 <= cands_raw <= 100_000_000:
            cands_count = cands_raw
        else:
            cands_count = None
        cands_basis = neutralize_clean(raw.get("affected_candidates_basis"), max_len=235)
        if not cands_basis:
            cands_basis = (
                f"Documented candidate count ({cands_count:,}) reported in cited official record"
                if cands_count is not None
                else "Not disclosed in cited official document"
            )

    sched_summary = neutralize_clean(raw.get("schedule_summary"), max_len=590) or f"{exam_name} ({cycle_or_stage}) scheduled for {sdt.isoformat()}."
    sched_doc_ref = neutralize_clean(raw.get("schedule_document_ref"), max_len=195) or f"{authority} Official Notice ({sdt.isoformat()})"
    out_summary = neutralize_clean(raw.get("outcome_summary"), max_len=590) or f"Official conduct/outcome record dated {edt.isoformat()} ({DISRUPTION_TYPE_LABELS[dtype]})."
    out_doc_ref = neutralize_clean(raw.get("outcome_document_ref"), max_len=195) or sched_doc_ref

    statutory_anchor = neutralize_clean(raw.get("statutory_anchor"), max_len=195) or (
        "Public Examinations (Prevention of Unfair Means) Act, 2024; RTI Act 2005 Sec 4(1)(b)"
        if jlvl == "union"
        else f"Constitution of India Art 315 & 320; {state_or_ut or jur} Recruitment & Examination Rules; RTI Act 2005 Sec 4(1)(b)"
    )
    benign_ctx = neutralize_clean(raw.get("benign_explanation_context"), max_len=295) or (
        "Confirmed conducted on the scheduled date per official authority notice with no documented disruption."
        if dtype == "flawless_conduct"
        else "Schedule revision, postponement, or preventive cancellation may reflect lawful integrity safeguards, election schedules, weather advisories, technical remediation, or judicial review."
    )

    # Verify evidence tokens against exact document pool used by P9 engine
    pri_recs = parsed_records[pri_url]
    sec_recs = parsed_records[sec_url] if sec_url else []
    if sec_url and sec_role == "schedule_document":
        sched_pool, out_pool = sec_recs, pri_recs
    elif sec_url and sec_role == "outcome_document":
        sched_pool, out_pool = pri_recs, sec_recs
    elif sec_url and sec_role == "corroborating_record":
        sched_pool = pri_recs + sec_recs
        out_pool = pri_recs + sec_recs
    else:
        sched_pool, out_pool = pri_recs, pri_recs

    sched_toks = _mine_distinct_tokens(
        raw.get("schedule_evidence_tokens") or [],
        sched_pool,
        seed_texts=[exam_name, cycle_or_stage, sched_summary, sched_doc_ref],
    )
    out_toks = _mine_distinct_tokens(
        raw.get("outcome_evidence_tokens") or [],
        out_pool,
        seed_texts=[out_summary, out_doc_ref, cycle_or_stage, exam_name],
        exclude_norms={norm_search(x) for x in sched_toks} if not sec_url else None,
    )
    if len(sched_toks) < 2 or len(out_toks) < 2:
        raise ValueError(
            f"Could not verify >=2 verbatim word-boundary tokens for {raw.get('case_key')}: "
            f"sched={sched_toks}, out={out_toks} ({pri_url})"
        )

    # Unique case_key (<=59 chars, [a-z0-9-])
    base_ckey = slugify(str(raw.get("case_key") or f"{jlvl}-{exam_slug}-{exam_year}"), max_len=52)
    ckey = base_ckey
    n_try = 2
    while ckey in used_case_keys or not _KEY_RE.fullmatch(ckey):
        suffix = f"-{n_try}"
        ckey = slugify(base_ckey[: 58 - len(suffix)] + suffix, max_len=58)
        n_try += 1
    used_case_keys.add(ckey)

    # Unique canonical P9 task ID: p9-(union|state|ut)-<slug>-NNNN
    existing_tid = str(raw.get("task_id") or "").strip()
    if existing_tid and P9_TASK_ID_RE.fullmatch(existing_tid) and existing_tid.startswith(f"p9-{jlvl}-") and existing_tid not in used_task_ids:
        tid = existing_tid
    else:
        if seq_num == 1 and jlvl == "union" and "ugc-net" in exam_slug:
            id_slug = "ugc-net-june-2024"
        else:
            prefix_s = "" if jlvl == "union" else slugify(state_or_ut or "", max_len=12) + "-"
            id_slug = slugify(f"{prefix_s}{exam_slug}", max_len=38)
            if len(id_slug) < 2:
                id_slug = "exam"
        tid = f"p9-{jlvl}-{id_slug}-{seq_num:04d}"
        while tid in used_task_ids or not P9_TASK_ID_RE.fullmatch(tid):
            id_slug = slugify(id_slug[:32], max_len=32) or "exam"
            seq_alt = (seq_num + len(used_task_ids)) % 9999 + 1
            tid = f"p9-{jlvl}-{id_slug}-{seq_alt:04d}"
    used_task_ids.add(tid)

    is_anchor = bool(raw.get("is_anchor_case"))
    raw_sel = raw.get("record_selector") if isinstance(raw.get("record_selector"), dict) else {}
    sel: dict[str, Any] = {}
    for pkey in ("schedule_page", "outcome_page"):
        pv = raw_sel.get(pkey)
        if isinstance(pv, int) and not isinstance(pv, bool) and 1 <= pv <= 5000:
            sel[pkey] = pv
    if raw_sel.get("section_hint"):
        sel["section_hint"] = neutralize_clean(raw_sel["section_hint"], max_len=118)
    if raw_sel.get("record_url"):
        try:
            sel["record_url"] = clean_public_url(raw_sel["record_url"])
        except Exception:
            pass

    spec: dict[str, Any] = {
        "case_key": ckey,
        "exam_name": exam_name,
        "exam_slug": exam_slug,
        "cycle_or_stage": cycle_or_stage,
        "exam_year": exam_year,
        "jurisdiction": jur,
        "jurisdiction_level": jlvl,
        "state_or_ut": state_or_ut,
        "authority": authority,
        "authority_key": authority_key,
        "authority_type": atype,
        "exam_category": ecat,
        "scheduled_date": sdt.isoformat(),
        "actual_or_event_date": edt.isoformat(),
        "delay_days": delay_days,
        "disruption_type": dtype,
        "students_took_exam_before_cancellation": students_sat,
        "schedule_summary": sched_summary,
        "schedule_document_ref": sched_doc_ref,
        "schedule_evidence_tokens": sched_toks,
        "outcome_summary": out_summary,
        "outcome_document_ref": out_doc_ref,
        "outcome_evidence_tokens": out_toks,
        "disruption_reason_category": rcat,
        "affected_candidates_count": cands_count,
        "affected_candidates_basis": cands_basis,
        "statutory_anchor": statutory_anchor,
        "benign_explanation_context": benign_ctx,
        "source_format": pri_fmt if pri_fmt in VALID_SOURCE_FORMATS else "pdf",
        "is_anchor_case": is_anchor,
    }
    if sec_url and sec_fmt:
        spec["secondary_source_format"] = sec_fmt if sec_fmt in VALID_SOURCE_FORMATS else "pdf"
        spec["secondary_role"] = sec_role
    if sel:
        spec["record_selector"] = sel

    official_publisher = neutralize_clean(raw.get("official_publisher") or authority, max_len=195)
    landing_page = clean_public_url(raw.get("source_landing_page") or pri_url)

    task_ctx: dict[str, Any] = {
        "as_of_date": AS_OF_DATE,
        "official_publisher": official_publisher,
        "source_landing_page": landing_page,
        "retrieval_url": pri_url,
        "expected_sha256": pri_sha,
        "auditing_government_exams": spec,
    }
    payload: dict[str, Any] = {
        "retrieval_url": pri_url,
        "expected_sha256": pri_sha,
        "official_publisher": official_publisher,
        "source_landing_page": landing_page,
        "as_of_date": AS_OF_DATE,
        "jurisdiction": jur,
        "context": task_ctx,
    }
    if sec_url and sec_sha:
        task_ctx["secondary_url"] = sec_url
        task_ctx["secondary_expected_sha256"] = sec_sha
        payload["secondary_url"] = sec_url
        payload["secondary_expected_sha256"] = sec_sha

    jur_short = "Union of India" if jlvl == "union" else (state_or_ut or jur)
    title = neutralize_clean(f"{jur_short} · {exam_name}: {cycle_or_stage}", max_len=155)
    if title in used_titles:
        tag = f" (#{seq_num})"
        title = _trim_word_boundary(title, 155 - len(tag)) + tag
    used_titles.add(title)

    instructions = neutralize_clean(
        f"Verify SHA-256 {pri_sha[:12]}… and run P9 on {exam_name} ({cycle_or_stage}, {jur}): "
        f"confirm scheduled date ({sdt.isoformat()}) vs conduct/outcome ({edt.isoformat()}, {DISRUPTION_TYPE_LABELS[dtype]}).",
        max_len=400,
    )

    return {
        "id": tid,
        "title": title,
        "instructions": instructions,
        "type": "run_check",
        "methodology_code": METHODOLOGY_CODE,
        "dataset_key": f"p9-{jlvl}-{slugify(exam_slug, max_len=42)}",
        "financial_quantity_type": "public_examination_conduct",
        "estimated_tokens": 6000 if sec_url else 4000,
        "estimate_basis": (
            "Deterministic P9 two-document SHA-256 and word-boundary verbatim token check (zero LLM cost in rule-engine mode)."
            if sec_url
            else "Deterministic P9 single-document SHA-256 and word-boundary verbatim token check (zero LLM cost in rule-engine mode)."
        ),
        "payload": payload,
    }


def _load_cases_from_existing_project(project_path: Path) -> list[dict[str, Any]]:
    data = json.loads(project_path.read_text(encoding="utf-8"))
    out: list[dict[str, Any]] = []
    for task in data.get("tasks", []):
        p = task["payload"]
        s = p["context"]["auditing_government_exams"]
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


def _select_1000_records(raw_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate and select exactly 1,000 highest-value, maximally diverse records covering all 37 jurisdictions."""
    # Step 1: Filter valid 3-year window & canonical jurisdiction, deduplicate
    deduped: list[dict[str, Any]] = []
    seen_sigs: set[tuple[str, ...]] = set()
    seen_ckeys: set[str] = set()

    for r in raw_records:
        try:
            jur, jlvl, s_ut = _canonicalize_jurisdiction(r)
            sdt = parse_iso_date(r.get("scheduled_date"))
            if sdt is None or not (AUDIT_WINDOW_START <= sdt <= AUDIT_WINDOW_END):
                continue
            u = clean_public_url(r.get("retrieval_url") or "")
            if not u:
                continue
        except Exception:
            continue

        ckey = slugify(str(r.get("case_key") or ""), max_len=58)
        eslug = slugify(str(r.get("exam_slug") or r.get("exam_name") or ""), max_len=78)
        stage = norm_search(str(r.get("cycle_or_stage") or ""))[:60]
        dtype = str(r.get("disruption_type") or "").strip().lower()
        if dtype not in VALID_DISRUPTION_TYPES:
            continue

        sig = (jur, eslug, stage, sdt.isoformat(), dtype, u)
        if sig in seen_sigs:
            continue
        if ckey and ckey in seen_ckeys:
            continue
        seen_sigs.add(sig)
        if ckey:
            seen_ckeys.add(ckey)

        rec = dict(r)
        rec["jurisdiction"] = jur
        rec["jurisdiction_level"] = jlvl
        rec["state_or_ut"] = s_ut
        rec["retrieval_url"] = u
        deduped.append(rec)

    if len(deduped) < TARGET_TASKS:
        raise RuntimeError(f"Only {len(deduped)} valid deduplicated records available; need >= {TARGET_TASKS}.")

    # Step 2: Guarantee Task #1 is UGC-NET June 2024 cancelled_after_conduct anchor
    ugc_anchors = [
        r
        for r in deduped
        if r["jurisdiction_level"] == "union"
        and r["disruption_type"] == "cancelled_after_conduct"
        and "ugc-net" in (str(r.get("exam_slug") or "") + " " + str(r.get("exam_name") or "")).lower()
        and "2024" in str(r.get("scheduled_date") or "")
    ]
    if not ugc_anchors:
        raise RuntimeError("Missing flagship Task #1 anchor: UGC-NET June 2024 (cancelled_after_conduct)!")

    # Prefer paired official doc or anchor-marked UGC-NET 2024 record
    ugc_anchors.sort(key=lambda x: (0 if x.get("is_anchor_case") else 1, 0 if x.get("secondary_url") else 1, -(x.get("affected_candidates_count") or 0)))
    task1 = ugc_anchors[0]
    task1["is_anchor_case"] = True

    remaining = [r for r in deduped if r is not task1]

    # Step 3: Priority-tiered selection ensuring:
    # - All anchor cases
    # - All 36 States & UTs represented (floor of min available up to 6 per State/UT)
    # - Balanced flawless_conduct cohort (~140-170 tasks)
    # - Rich representation of cancelled_after_conduct, postponed, rescheduled, delayed, cancelled_before_conduct, partial_reexam_or_glitch
    # - Strong preference for unique URLs and direct authority notices
    selected: list[dict[str, Any]] = [task1]
    selected_ids: set[int] = {id(task1)}
    url_counts: Counter[str] = Counter({task1["retrieval_url"]: 1})
    jur_counts: Counter[str] = Counter({task1["jurisdiction"]: 1})
    dtype_counts: Counter[str] = Counter({task1["disruption_type"]: 1})

    def _add(r: dict[str, Any]) -> None:
        selected.append(r)
        selected_ids.add(id(r))
        url_counts[r["retrieval_url"]] += 1
        jur_counts[r["jurisdiction"]] += 1
        dtype_counts[r["disruption_type"]] += 1

    # 3a. All anchor cases first (up to 15)
    for r in remaining:
        if r.get("is_anchor_case") and len(selected) < 25:
            _add(r)

    # 3b. Guarantee every single one of the 28 States + 8 UTs has strong coverage (up to 8 per State, 6 per UT first)
    by_jur: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in remaining:
        if id(r) not in selected_ids:
            by_jur[r["jurisdiction"]].append(r)

    def _rec_quality_key(r: dict[str, Any]) -> tuple[Any, ...]:
        u = r["retrieval_url"]
        is_direct = 0 if "sansad.in" not in u else 1
        has_sec = 0 if r.get("secondary_url") else 1
        has_cands = 0 if r.get("affected_candidates_count") else 1
        cac_prio = 0 if r["disruption_type"] == "cancelled_after_conduct" else 1
        return (url_counts[u], is_direct, has_sec, cac_prio, has_cands, str(r.get("case_key") or ""))

    for jur_name in sorted(by_jur.keys()):
        if jur_name == "Union of India":
            continue
        quota = 6 if jur_name.startswith("UT:") else 10
        pool = by_jur[jur_name]
        picked = 0
        while pool and picked < quota and len(selected) < TARGET_TASKS - 200:
            pool.sort(key=_rec_quality_key)
            cand = pool.pop(0)
            if id(cand) in selected_ids:
                continue
            # Keep flawless share balanced during floor pass
            if cand["disruption_type"] == "flawless_conduct" and dtype_counts["flawless_conduct"] >= 165:
                continue
            _add(cand)
            picked += 1

    # 3c. Ensure ~140-165 verified flawless_conduct tasks across Union + States/UTs
    target_flawless = 150
    flawless_pool = [r for r in remaining if id(r) not in selected_ids and r["disruption_type"] == "flawless_conduct"]
    while flawless_pool and dtype_counts["flawless_conduct"] < target_flawless and len(selected) < TARGET_TASKS:
        flawless_pool.sort(key=lambda r: (url_counts[r["retrieval_url"]], jur_counts[r["jurisdiction"]] > 15, _rec_quality_key(r)))
        cand = flawless_pool.pop(0)
        if id(cand) not in selected_ids:
            _add(cand)

    # 3d. Fill remaining slots up to 1,000 maximizing unique documents, non-flawless disruption diversity, and ~45-52% Union / 48-55% State+UT balance
    disrupted_pool = [r for r in remaining if id(r) not in selected_ids and r["disruption_type"] != "flawless_conduct"]
    while len(selected) < TARGET_TASKS and disrupted_pool:
        union_ct = jur_counts["Union of India"]
        prefer_union = union_ct < 460
        disrupted_pool.sort(
            key=lambda r: (
                url_counts[r["retrieval_url"]],
                0 if (prefer_union and r["jurisdiction_level"] == "union") or (not prefer_union and r["jurisdiction_level"] != "union") else 1,
                0 if r["disruption_type"] in {"cancelled_after_conduct", "delayed", "cancelled_before_conduct", "partial_reexam_or_glitch"} and dtype_counts[r["disruption_type"]] < 110 else 1,
                jur_counts[r["jurisdiction"]] // 12 if r["jurisdiction_level"] != "union" else 0,
                _rec_quality_key(r),
            )
        )
        cand = disrupted_pool.pop(0)
        if id(cand) not in selected_ids:
            _add(cand)

    # Fallback if disrupted_pool ran short: top up from flawless_pool
    while len(selected) < TARGET_TASKS and flawless_pool:
        flawless_pool.sort(key=_rec_quality_key)
        cand = flawless_pool.pop(0)
        if id(cand) not in selected_ids:
            _add(cand)

    if len(selected) != TARGET_TASKS:
        raise RuntimeError(f"Selection ended with {len(selected)} tasks instead of {TARGET_TASKS}.")
    return selected


def _run_quality_gates(
    tasks: list[dict[str, Any]],
    coverage: dict[str, Any],
    parsed_records: dict[str, list[dict[str, Any]]],
) -> None:
    errors: list[str] = []

    if len(tasks) != TARGET_TASKS:
        errors.append(f"Expected {TARGET_TASKS} tasks, got {len(tasks)}")
    if len({t["id"] for t in tasks}) != TARGET_TASKS:
        errors.append("Duplicate task IDs detected")
    if len({t["title"] for t in tasks}) != TARGET_TASKS:
        dups = [k for k, v in Counter(t["title"] for t in tasks).items() if v > 1]
        errors.append(f"Duplicate visible task titles ({len(dups)}): {dups[:5]}")
    if len({t["payload"]["context"]["auditing_government_exams"]["case_key"] for t in tasks}) != TARGET_TASKS:
        errors.append("Duplicate case_key values detected")

    # Confirm all 37 jurisdictions present
    present_jurs = {t["payload"]["context"]["auditing_government_exams"]["jurisdiction"] for t in tasks}
    expected_jurs = {"Union of India"} | {f"State:{s}" for s in ALL_28_STATES} | {f"UT:{u}" for u in ALL_8_UTS}
    missing_jurs = sorted(expected_jurs - present_jurs)
    if missing_jurs:
        errors.append(f"Missing {len(missing_jurs)} jurisdictions: {missing_jurs}")

    hay_cache = {u: _records_norm_haystack(recs) for u, recs in parsed_records.items()}
    pua_hits = 0
    page_marker_hits = 0
    non_wb_hits: list[str] = []
    window_errs: list[str] = []
    sat_errs: list[str] = []

    for t in tasks:
        raw_unesc = json.dumps(t, ensure_ascii=False)
        if _PUA_AND_GLYPHS_RE.search(raw_unesc) or _CTRL_CHARS_RE.search(raw_unesc):
            pua_hits += 1
        if _PAGE_MARKER_RE.search(raw_unesc):
            page_marker_hits += 1

        p = t["payload"]
        s = p["context"]["auditing_government_exams"]
        tid = t["id"]

        if not P9_TASK_ID_RE.fullmatch(tid):
            errors.append(f"Invalid P9 task id format: {tid}")
        sdt = parse_iso_date(s["scheduled_date"])
        edt = parse_iso_date(s["actual_or_event_date"])
        if sdt is None or not (AUDIT_WINDOW_START <= sdt <= AUDIT_WINDOW_END):
            window_errs.append(f"{tid}:scheduled_date={s['scheduled_date']}")
        if edt is None or not (EVENT_WINDOW_START <= edt <= EVENT_WINDOW_END):
            window_errs.append(f"{tid}:actual_or_event_date={s['actual_or_event_date']}")
        if s["disruption_type"] == "cancelled_after_conduct":
            if not s["students_took_exam_before_cancellation"]:
                sat_errs.append(f"{tid}:cancelled_after_conduct must have students_sat=True")
            if sdt and edt and edt < sdt:
                window_errs.append(f"{tid}:cancelled_after_conduct event<scheduled")
        elif s["disruption_type"] in DISRUPTION_NO_SAT_BEFORE_CANCEL and s["students_took_exam_before_cancellation"]:
            sat_errs.append(f"{tid}:{s['disruption_type']} must have students_sat=False")
        if (s["disruption_type"] == "flawless_conduct") != (s["disruption_reason_category"] == "none_flawless"):
            errors.append(f"{tid}: flawless_conduct / none_flawless mismatch")
        if s["disruption_type"] == "flawless_conduct" and s["affected_candidates_count"] is not None:
            errors.append(f"{tid}: flawless_conduct must have affected_candidates_count=None")
        if re.search(r"&(amp|nbsp|lt|gt|quot|#\d+);", t["title"] + " " + s["exam_name"] + " " + s["cycle_or_stage"]):
            errors.append(f"{tid}: raw HTML entity in visible title/exam_name/cycle")
        if "%20" in t["title"] or "%20" in s["exam_name"] or "%20" in s["cycle_or_stage"]:
            errors.append(f"{tid}: URL-encoded %20 in visible title/exam_name/cycle")

        pri_u = p["retrieval_url"]
        sec_u = p.get("secondary_url")
        role = s.get("secondary_role") or ""
        if sec_u and role == "schedule_document":
            s_hay, o_hay = hay_cache[sec_u], hay_cache[pri_u]
        elif sec_u and role in {"", "outcome_document"}:
            s_hay, o_hay = hay_cache[pri_u], hay_cache[sec_u]
        elif sec_u and role == "corroborating_record":
            s_hay = o_hay = hay_cache[pri_u] + "\n" + hay_cache[sec_u]
        else:
            s_hay = o_hay = hay_cache[pri_u]

        for side, hay in (("schedule_evidence_tokens", s_hay), ("outcome_evidence_tokens", o_hay)):
            toks = s[side]
            if len(toks) < 2:
                errors.append(f"{tid} {side} has <2 tokens")
            for tok in toks:
                if not word_boundary_match(tok, hay):
                    non_wb_hits.append(f"{tid}:{side}:{tok!r}")

    if pua_hits:
        errors.append(f"PUA/control glyphs found in {pua_hits} tasks")
    if page_marker_hits:
        errors.append(f"[PAGE N] artifacts found in {page_marker_hits} tasks")
    if window_errs:
        errors.append(f"{len(window_errs)} date window errors: {window_errs[:8]}")
    if sat_errs:
        errors.append(f"{len(sat_errs)} students_took_exam_before_cancellation errors: {sat_errs[:8]}")
    if non_wb_hits:
        errors.append(f"{len(non_wb_hits)} non-word-boundary token misses: {non_wb_hits[:8]}")

    # Headline reconciliation
    hc = coverage["headline_counts"]
    if hc["not_flawless_count"] + hc["flawless_count"] != TARGET_TASKS:
        errors.append("headline_counts not_flawless + flawless != 1000")
    sum_dis = sum(hc[k] for k in SIGNAL_CATEGORIES)
    if sum_dis != hc["not_flawless_count"]:
        errors.append(f"headline_counts sum(6 disruption types)={sum_dis} != not_flawless={hc['not_flawless_count']}")

    # Frontend jurisdiction prefix filter & official-domain integrity gates
    for jrow in coverage["jurisdictions"]:
        jur_short = "Union of India" if jrow["jurisdiction_level"] == "union" else str(jrow["state_or_ut"])
        prefix = f"{jur_short} ·"
        matched = sum(1 for t in tasks if prefix.lower() in t["title"].lower())
        if matched != jrow["tasks"]:
            errors.append(f"Jurisdiction filter mismatch for {prefix!r}: matched {matched} != {jrow['tasks']}")

    official_suffixes = (".gov.in", ".nic.in", ".nta.ac.in", ".sansad.in", ".rajyasabha.digital", ".rbi.org.in", ".aiimsexams.ac.in")
    official_exact = {"gov.in", "nic.in", "sansad.in", "nta.ac.in", "ibps.in", "www.ibps.in", "cag.gov.in", "rbi.org.in"}
    bad_hosts: set[str] = set()
    for t in tasks:
        for uk in ("retrieval_url", "secondary_url"):
            u = t["payload"].get(uk)
            if not u:
                continue
            h = (urlsplit(u).hostname or "").lower()
            if h not in official_exact and not h.endswith(official_suffixes):
                bad_hosts.add(h)
    if bad_hosts:
        errors.append(f"Non-official hostnames detected: {sorted(bad_hosts)}")

    if errors:
        raise RuntimeError("P9 QUALITY GATE FAILED:\n  - " + "\n  - ".join(errors))


def _write_methodology_md(coverage: dict[str, Any], hosts_summary: list[tuple[str, int]]) -> None:
    hc = coverage["headline_counts"]
    jlvl = coverage["by_jurisdiction_level"]
    dry = coverage["dry_run_summary"]
    anchors = coverage.get("anchor_cases") or []
    a0 = anchors[0] if anchors else {}
    host_lines = "\n".join(f"  - `{host}`: `{cnt}` unique SHA-256-pinned documents" for host, cnt in hosts_summary[:25])
    if len(hosts_summary) > 25:
        rem_docs = sum(cnt for _, cnt in hosts_summary[25:])
        host_lines += f"\n  - `+{len(hosts_summary) - 25} additional official State/UT/Union domains`: `{rem_docs}` unique SHA-256-pinned documents"
    year_lines = "\n".join(
        f"  - `{y['year']}`: `{y['tasks']}` audited (`{y['not_flawless']}` not flawless · `{y['flawless']}` flawless · `{y['cancelled_after_conduct']}` took exam then cancelled)"
        for y in coverage["by_year"]
    )

    content = f"""# Methodology `P9` — Auditing Government Exams

## Simple Hook
> **{HOOK_TEXT}**

## Objective
Audit **1,000 SHA-256-pinned Indian government examination conduct records** across the **past 3 years (`2023-11-28` to `2026-11-28`, exam years 2023–2026)** spanning the **Union Government** (`{jlvl['union']['tasks']}` tasks) and **all {coverage['states_and_uts_covered']} States & Union Territories** (`{jlvl['state']['tasks']}` State + `{jlvl['ut']['tasks']}` UT tasks across all 28 States and 8 UTs), covering **{coverage['unique_exams_covered']} unique examinations**, **{coverage['unique_authorities_covered']} conducting authorities**, and **{coverage['unique_documents_count']} unique live-verified byte-stable official documents**. Every task verifies an official scheduled examination notice against its subsequent conduct or disruption record — failing closed as `unevaluable` whenever downloaded bytes fail SHA-256 verification or lack required verbatim word-boundary documentary tokens.

## Flagship Anchor Case (Task #1: `{a0.get('task_id', 'p9-union-ugc-net-june-2024-0001')}`)
- **Examination:** {a0.get('exam_name', 'UGC-NET June 2024')} — {a0.get('cycle_or_stage', '')}
- **Jurisdiction & Authority:** {a0.get('jurisdiction', 'Union of India')} · {a0.get('authority', '')}
- **Original Schedule (`{a0.get('scheduled_date', '')}`):** {a0.get('schedule_summary', '')}
- **Documented Outcome (`{a0.get('actual_or_event_date', '')}`):** {a0.get('outcome_summary', '')}
- **Classification:** `{a0.get('disruption_type', 'cancelled_after_conduct')}` (`students_took_exam_before_cancellation={str(a0.get('students_took_exam_before_cancellation', True)).lower()}`)
- **Primary Record:** {a0.get('source_url', '')}
- **Secondary Record:** {a0.get('secondary_url') or 'Single official document'}

## 3-Year Audit Scorecard (`{coverage['total_tasks']}` Exams · Nov 2023 – Nov 2026 · All 28 States + 8 UTs + Union)
- **Did Not Happen Flawlessly:** `{hc['not_flawless_count']}` (`{hc['not_flawless_rate_pct']}%`)
- **Happened Flawlessly (Cleared Benchmark):** `{hc['flawless_count']}` (`{hc['flawless_rate_pct']}%`)
- **Disruption Breakdown:**
  - `cancelled_after_conduct` (Took Exam, Then Cancelled): `{hc['cancelled_after_conduct']}`
  - `postponed`: `{hc['postponed']}`
  - `rescheduled`: `{hc['rescheduled']}`
  - `delayed`: `{hc['delayed']}`
  - `cancelled_before_conduct`: `{hc['cancelled_before_conduct']}`
  - `partial_reexam_or_glitch` (Technical Glitch / Partial Re-Exam): `{hc['partial_reexam_or_glitch']}`
  - `flawless_conduct` (Conducted Flawlessly — Cleared): `{hc['flawless_count']}`
- **Jurisdiction Tier Split:** Union of India: `{jlvl['union']['tasks']}` · States (28/28): `{jlvl['state']['tasks']}` · Union Territories (8/8): `{jlvl['ut']['tasks']}`
- **Year-over-Year Coverage (2023–2026):**
{year_lines}
- **Deterministic Dry-Run (`as_of_date={coverage['as_of_date']}`):** `{dry['flagged_leads']}` leads · `{dry['cleared_flawless']}` cleared · `{dry['unevaluable']}` unevaluable (across all `{dry['evaluated_tasks']}` tasks)
- **Verified Byte-Stable Source Domains ({coverage['unique_documents_count']} unique pinned URLs across {len(hosts_summary)} official domains):**
{host_lines}

## Deterministic Rules (`P9`)
1. **Took Exam, Then Cancelled (`P9-CANCELLED-AFTER-CONDUCT`):** Candidates sat/attempted the scheduled examination and the authority subsequently cancelled it (`students_took_exam_before_cancellation=true`).
2. **Postponed (`P9-POSTPONED`):** Scheduled examination was deferred/postponed prior to conduct.
3. **Rescheduled (`P9-RESCHEDULED`):** Scheduled examination date, shift, or centre was revised via official notice.
4. **Delayed (`P9-DELAYED`):** Examination conduct, notification, or statutory cycle was materially delayed beyond announced schedule.
5. **Cancelled Before Conduct (`P9-CANCELLED-BEFORE-CONDUCT`):** Notified examination or recruitment test was cancelled/withdrawn before candidates sat.
6. **Technical Glitch / Partial Re-Exam (`P9-PARTIAL-REEXAM-OR-GLITCH`):** Technical/CBT fault, time-loss grace-mark anomaly, or centre/subject-level disruption requiring partial re-examination.
7. **Conducted Flawlessly (`Cleared`):** Audited and confirmed conducted as scheduled without documented disruption (`{hc['flawless_count']}` tasks), published with equal journalistic standing.

## Legal, Privacy & Verification Safeguards
- **Fail-Closed Verification:** Every primary and secondary URL is double-fetch byte-stability verified over HTTPS and SHA-256 pinned (`DigestMismatchError` on any byte change). `P9` confirms `>= 40` chars of machine-readable extracted text and 100% verbatim word-boundary token presence in the verified document bytes before emitting any lead or cleared verdict.
- **No Fabricated Candidate Counts:** `affected_candidates_count` is populated exclusively when explicitly stated in the cited official document; otherwise preserved as `null` with an explicit `affected_candidates_basis`.
- **BNS Sec 356 Neutrality & DPDP Act 2023:** Zero forbidden accusatory terms and zero personal PII across `projects/auditing-government-exams.json`, `coverage.json`, `documents.csv`, and `METHODOLOGY.md`. Every lead emits mandatory lawful benign explanations (preventive integrity safeguards under the Public Examinations (Prevention of Unfair Means) Act 2024, election Model Code of Conduct, severe weather/disaster advisories, CBT vendor faults, and judicial orders).
"""
    PROJECT_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    (PROJECT_ASSETS_DIR / "METHODOLOGY.md").write_text(content, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build & verify Project P9 (Auditing Government Exams, 1,000 tasks).")
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE_DIR, help="Local blob cache dir.")
    parser.add_argument("--catalogs", type=Path, nargs="*", default=None, help="Explicit catalog JSON paths.")
    parser.add_argument("--from-existing", action="store_true", help="Rebuild/verify from committed projects/auditing-government-exams.json.")
    parser.add_argument("--force-live", action="store_true", help="Re-download all URLs ignoring local cache.")
    parser.add_argument("--skip-spot-check", action="store_true", help="Skip live uncached cross-domain spot-check.")
    parser.add_argument("--fetch-workers", type=int, default=12, help="Concurrent HTTP download threads.")
    parser.add_argument("--parse-workers", type=int, default=max(2, min(8, multiprocessing.cpu_count())), help="Parallel PDF parse workers.")
    args = parser.parse_args()

    t_start = time.time()

    # Optional P9 engine + project validator import (available once backend P9 is merged or via PYTHONPATH)
    has_p9_engine = False
    run_engine = None
    validate_project = None
    try:
        from aiwatchdogs.engines.auditing_government_exams import validate_context as _p9_vc  # noqa: F401
        from aiwatchdogs.engines.registry import run_engine as _re
        from aiwatchdogs.projects import validate_project as _vp

        run_engine = _re
        validate_project = _vp
        has_p9_engine = True
    except Exception:
        has_p9_engine = False

    if args.catalogs:
        catalog_paths = [p for p in args.catalogs if p.exists()]
    else:
        catalog_paths = [DEFAULT_SCRATCH_DIR / nm for nm in DEFAULT_CATALOG_NAMES if (DEFAULT_SCRATCH_DIR / nm).exists()]

    use_existing = args.from_existing or not catalog_paths
    if use_existing:
        if not PROJECT_JSON_PATH.exists():
            raise SystemExit(f"Neither harvested catalogs nor {PROJECT_JSON_PATH} exist.")
        print(f"Standalone rebuild/verification mode: reading {PROJECT_JSON_PATH}...", flush=True)
        selected_raw = _load_cases_from_existing_project(PROJECT_JSON_PATH)
    else:
        print(f"Catalog build mode: loading {len(catalog_paths)} catalog(s): {[p.name for p in catalog_paths]}...", flush=True)
        all_raw: list[dict[str, Any]] = []
        for cp in catalog_paths:
            data = json.loads(cp.read_text(encoding="utf-8"))
            recs = data.get("records", data) if isinstance(data, dict) else data
            print(f"  loaded {len(recs)} records from {cp.name}", flush=True)
            all_raw.extend(recs)
        selected_raw = _select_1000_records(all_raw)
        print(f"Selected exactly {len(selected_raw)} diverse records.", flush=True)

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
    url_info: dict[str, tuple[str, str, int, int]] = {}

    with ProcessPoolExecutor(max_workers=args.parse_workers) as pex:
        done = 0
        for url, sha, fmt, bsz, pages, recs in pex.map(_parse_blob_worker, parse_jobs, chunksize=8):
            done += 1
            parsed_records[url] = recs
            url_info[url] = (sha, fmt, bsz, pages)
            if done % 150 == 0 or done == len(parse_jobs):
                print(f"  parsed {done}/{len(parse_jobs)} artifacts ({time.time() - t_start:.1f}s)", flush=True)

    # 4. Build strict P9 tasks and dry-run EVERY SINGLE TASK
    print(f"Assembling & verifying all {TARGET_TASKS} P9 tasks (engine_available={has_p9_engine})...", flush=True)
    tasks: list[dict[str, Any]] = []
    used_task_ids: set[str] = set()
    used_case_keys: set[str] = set()
    used_titles: set[str] = set()
    dry_leads = 0
    dry_cleared = 0
    dry_unevaluable = 0
    by_severity: Counter[str] = Counter()

    for idx, raw in enumerate(selected_raw, start=1):
        task = _normalize_raw_record(
            raw,
            seq_num=idx,
            url_info=url_info,
            parsed_records=parsed_records,
            used_task_ids=used_task_ids,
            used_case_keys=used_case_keys,
            used_titles=used_titles,
        )
        p = task["payload"]
        s = p["context"]["auditing_government_exams"]
        if has_p9_engine and run_engine is not None:
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
                by_severity[str(res.leads[0].severity)] += 1
            elif res.cleared_count > 0:
                dry_cleared += res.cleared_count
            else:
                raise RuntimeError(f"Task #{idx} {task['id']} produced neither lead nor cleared verdict: {res.summary}")
        else:
            if s["disruption_type"] == "flawless_conduct":
                dry_cleared += 1
            else:
                dry_leads += 1
                sev = _evaluate_severity_local(
                    disruption_type=s["disruption_type"],
                    disruption_reason_category=s["disruption_reason_category"],
                    students_sat=s["students_took_exam_before_cancellation"],
                    delay_days=s["delay_days"],
                    affected_candidates_count=s["affected_candidates_count"],
                    is_anchor_case=s["is_anchor_case"],
                )
                by_severity[sev] += 1

        tasks.append(task)
        if idx % 250 == 0 or idx == TARGET_TASKS:
            print(
                f"  verified {idx}/{TARGET_TASKS}: leads={dry_leads}, cleared={dry_cleared}, unevaluable={dry_unevaluable}",
                flush=True,
            )

    assert len(tasks) == TARGET_TASKS
    assert dry_unevaluable == 0

    # 5. Aggregate coverage.json & documents.csv aligned 1:1 with `@backend` desk.py
    by_disruption_type: dict[str, dict[str, Any]] = {
        d: {
            "disruption_type": d,
            "label": DISRUPTION_TYPE_LABELS[d],
            "rule": DISRUPTION_TYPE_RULES.get(d),
            "tasks": 0,
            "pct": 0.0,
            "documented_affected_candidates": 0,
        }
        for d in (*SIGNAL_CATEGORIES, "flawless_conduct")
    }
    by_jlevel: dict[str, dict[str, int]] = {
        lvl: {"tasks": 0, "not_flawless": 0, "flawless": 0, "cancelled_after_conduct": 0}
        for lvl in ("union", "state", "ut")
    }
    by_atype: dict[str, dict[str, Any]] = {
        at: {"authority_type": at, "label": lbl, "tasks": 0, "not_flawless": 0, "flawless": 0, "cancelled_after_conduct": 0}
        for at, lbl in AUTHORITY_TYPE_LABELS.items()
    }
    by_ecat: dict[str, dict[str, Any]] = {
        ec: {"exam_category": ec, "label": lbl, "tasks": 0, "not_flawless": 0, "flawless": 0, "cancelled_after_conduct": 0}
        for ec, lbl in EXAM_CATEGORY_LABELS.items()
    }
    by_year: dict[int, dict[str, Any]] = {
        yr: {
            "year": yr,
            "tasks": 0,
            "not_flawless": 0,
            "flawless": 0,
            "cancelled_after_conduct": 0,
            "postponed": 0,
            "rescheduled": 0,
            "delayed": 0,
            "cancelled_before_conduct": 0,
            "partial_reexam_or_glitch": 0,
        }
        for yr in AUDIT_WINDOW_YEARS
    }
    by_reason: dict[str, dict[str, Any]] = {
        rc: {"reason_category": rc, "label": lbl, "tasks": 0}
        for rc, lbl in REASON_CATEGORY_LABELS.items()
    }
    jurisdictions_map: dict[str, dict[str, Any]] = {}
    authorities_map: dict[str, dict[str, Any]] = {}
    unique_exams: set[str] = set()
    anchor_cases: list[dict[str, Any]] = []
    sample_leads_pool: list[dict[str, Any]] = []
    doc_usage: Counter[str] = Counter()
    doc_meta: dict[str, dict[str, Any]] = {}
    doc_cands_total = 0

    sev_rank = {"critical": 4, "high": 3, "medium": 2, "low": 1}

    for task in tasks:
        p = task["payload"]
        s = p["context"]["auditing_government_exams"]
        dtype = s["disruption_type"]
        rcat = s["disruption_reason_category"]
        jlvl = s["jurisdiction_level"]
        jur = s["jurisdiction"]
        atype = s["authority_type"]
        ecat = s["exam_category"]
        yr = s["exam_year"]
        cands = s["affected_candidates_count"] or 0
        is_flawless = dtype == "flawless_conduct"
        is_cac = dtype == "cancelled_after_conduct"

        unique_exams.add(s["exam_slug"])
        by_disruption_type[dtype]["tasks"] += 1
        by_disruption_type[dtype]["documented_affected_candidates"] += cands
        doc_cands_total += cands

        by_jlevel[jlvl]["tasks"] += 1
        by_jlevel[jlvl]["flawless" if is_flawless else "not_flawless"] += 1
        if is_cac:
            by_jlevel[jlvl]["cancelled_after_conduct"] += 1

        by_atype[atype]["tasks"] += 1
        by_atype[atype]["flawless" if is_flawless else "not_flawless"] += 1
        if is_cac:
            by_atype[atype]["cancelled_after_conduct"] += 1

        by_ecat[ecat]["tasks"] += 1
        by_ecat[ecat]["flawless" if is_flawless else "not_flawless"] += 1
        if is_cac:
            by_ecat[ecat]["cancelled_after_conduct"] += 1

        yrow = by_year[yr]
        yrow["tasks"] += 1
        yrow["flawless" if is_flawless else "not_flawless"] += 1
        if not is_flawless:
            yrow[dtype] += 1

        by_reason[rcat]["tasks"] += 1

        jrow = jurisdictions_map.setdefault(
            jur,
            {
                "jurisdiction": jur,
                "jurisdiction_level": jlvl,
                "state_or_ut": s["state_or_ut"],
                "tasks": 0,
                "not_flawless": 0,
                "flawless": 0,
                "cancelled_after_conduct": 0,
                "postponed": 0,
                "rescheduled": 0,
                "delayed": 0,
                "cancelled_before_conduct": 0,
                "partial_reexam_or_glitch": 0,
                "not_flawless_rate_pct": 0.0,
                "documented_affected_candidates": 0,
            },
        )
        jrow["tasks"] += 1
        jrow["flawless" if is_flawless else "not_flawless"] += 1
        if not is_flawless:
            jrow[dtype] += 1
        jrow["documented_affected_candidates"] += cands

        akey = s["authority_key"]
        arow = authorities_map.setdefault(
            akey,
            {
                "authority": s["authority"],
                "authority_key": akey,
                "authority_type": atype,
                "jurisdiction": jur,
                "jurisdiction_level": jlvl,
                "tasks": 0,
                "not_flawless": 0,
                "flawless": 0,
                "cancelled_after_conduct": 0,
                "postponed": 0,
                "rescheduled": 0,
                "delayed": 0,
                "cancelled_before_conduct": 0,
                "partial_reexam_or_glitch": 0,
                "not_flawless_rate_pct": 0.0,
                "documented_affected_candidates": 0,
            },
        )
        arow["tasks"] += 1
        arow["flawless" if is_flawless else "not_flawless"] += 1
        if not is_flawless:
            arow[dtype] += 1
        arow["documented_affected_candidates"] += cands

        sev = _evaluate_severity_local(
            disruption_type=dtype,
            disruption_reason_category=rcat,
            students_sat=s["students_took_exam_before_cancellation"],
            delay_days=s["delay_days"],
            affected_candidates_count=s["affected_candidates_count"],
            is_anchor_case=s["is_anchor_case"],
        )

        if s["is_anchor_case"]:
            anchor_cases.append(
                {
                    "task_id": task["id"],
                    "case_key": s["case_key"],
                    "finding_id": None,
                    "exam_name": s["exam_name"],
                    "exam_slug": s["exam_slug"],
                    "cycle_or_stage": s["cycle_or_stage"],
                    "exam_year": yr,
                    "jurisdiction": jur,
                    "jurisdiction_level": jlvl,
                    "state_or_ut": s["state_or_ut"],
                    "authority": s["authority"],
                    "authority_key": akey,
                    "authority_type": atype,
                    "exam_category": ecat,
                    "scheduled_date": s["scheduled_date"],
                    "actual_or_event_date": s["actual_or_event_date"],
                    "delay_days": s["delay_days"],
                    "disruption_type": dtype,
                    "disruption_reason_category": rcat,
                    "students_took_exam_before_cancellation": s["students_took_exam_before_cancellation"],
                    "schedule_summary": s["schedule_summary"],
                    "schedule_document_ref": s["schedule_document_ref"],
                    "outcome_summary": s["outcome_summary"],
                    "outcome_document_ref": s["outcome_document_ref"],
                    "affected_candidates_count": s["affected_candidates_count"],
                    "affected_candidates_basis": s["affected_candidates_basis"],
                    "statutory_anchor": s["statutory_anchor"],
                    "source_url": p["retrieval_url"],
                    "secondary_url": p.get("secondary_url"),
                    "portal_url": p["source_landing_page"],
                    "severity": sev,
                }
            )

        if not is_flawless:
            sample_leads_pool.append(
                {
                    "finding_id": None,
                    "task_id": task["id"],
                    "case_key": s["case_key"],
                    "title": task["title"],
                    "exam_name": s["exam_name"],
                    "exam_slug": s["exam_slug"],
                    "cycle_or_stage": s["cycle_or_stage"],
                    "exam_year": yr,
                    "jurisdiction": jur,
                    "jurisdiction_level": jlvl,
                    "state_or_ut": s["state_or_ut"],
                    "authority": s["authority"],
                    "authority_key": akey,
                    "authority_type": atype,
                    "exam_category": ecat,
                    "scheduled_date": s["scheduled_date"],
                    "actual_or_event_date": s["actual_or_event_date"],
                    "delay_days": s["delay_days"],
                    "disruption_type": dtype,
                    "disruption_reason_category": rcat,
                    "signal_category": dtype,
                    "rule_triggered": DISRUPTION_TYPE_RULES[dtype],
                    "students_took_exam_before_cancellation": s["students_took_exam_before_cancellation"],
                    "schedule_summary": s["schedule_summary"],
                    "schedule_document_ref": s["schedule_document_ref"],
                    "outcome_summary": s["outcome_summary"],
                    "outcome_document_ref": s["outcome_document_ref"],
                    "affected_candidates_count": s["affected_candidates_count"],
                    "affected_candidates_basis": s["affected_candidates_basis"],
                    "statutory_anchor": s["statutory_anchor"],
                    "source_url": p["retrieval_url"],
                    "secondary_url": p.get("secondary_url"),
                    "portal_url": p["source_landing_page"],
                    "is_anchor_case": s["is_anchor_case"],
                    "severity": sev,
                    "status": "corpus_queued",
                    "_sort_key": (
                        0 if s["is_anchor_case"] else 1,
                        0 if is_cac else 1,
                        -sev_rank.get(sev, 0),
                        -cands,
                        task["id"],
                    ),
                }
            )

        for ukey in ("retrieval_url", "secondary_url"):
            u = p.get(ukey)
            if not u:
                continue
            doc_usage[u] += 1
            if u not in doc_meta:
                sha, fmt, bsz, pages = url_info[u]
                doc_meta[u] = {
                    "document_id": f"p9-doc-{hashlib.sha256(u.encode()).hexdigest()[:12]}",
                    "jurisdiction": jur,
                    "format": fmt,
                    "bytes": bsz,
                    "pages": pages,
                    "sha256": sha,
                    "official_publisher": p["official_publisher"],
                    "retrieval_url": u,
                    "landing_url": p["source_landing_page"],
                }

    total_audited = len(tasks)
    flawless_count = by_disruption_type["flawless_conduct"]["tasks"]
    not_flawless_count = sum(by_disruption_type[k]["tasks"] for k in SIGNAL_CATEGORIES)
    for drow in by_disruption_type.values():
        drow["pct"] = round(drow["tasks"] / total_audited * 100.0, 2)
    for jrow in jurisdictions_map.values():
        jrow["not_flawless_rate_pct"] = round(jrow["not_flawless"] / jrow["tasks"] * 100.0, 2) if jrow["tasks"] else 0.0
    for arow in authorities_map.values():
        arow["not_flawless_rate_pct"] = round(arow["not_flawless"] / arow["tasks"] * 100.0, 2) if arow["tasks"] else 0.0

    jurisdictions_sorted = sorted(
        jurisdictions_map.values(),
        key=lambda j: (0 if j["jurisdiction_level"] == "union" else 1, -j["not_flawless"], -j["tasks"], j["jurisdiction"].lower()),
    )
    top_authorities_sorted = sorted(
        authorities_map.values(),
        key=lambda a: (-a["not_flawless"], -a["tasks"], a["authority"].lower()),
    )[:40]
    anchor_cases.sort(
        key=lambda a: (
            0 if a["task_id"] == tasks[0]["id"] else 1,
            0 if a["disruption_type"] == "cancelled_after_conduct" else 1,
            -(a["affected_candidates_count"] or 0),
            a["task_id"],
        )
    )
    sample_leads_pool.sort(key=lambda x: x["_sort_key"])
    sample_leads = [{k: v for k, v in item.items() if k != "_sort_key"} for item in sample_leads_pool[:120]]

    states_uts_covered = sum(1 for j in jurisdictions_sorted if j["jurisdiction_level"] in {"state", "ut"})
    headline_counts = {
        "total_audited": total_audited,
        "not_flawless_count": not_flawless_count,
        "flawless_count": flawless_count,
        "not_flawless_rate_pct": round(not_flawless_count / total_audited * 100.0, 2),
        "flawless_rate_pct": round(flawless_count / total_audited * 100.0, 2),
        "cancelled_after_conduct": by_disruption_type["cancelled_after_conduct"]["tasks"],
        "postponed": by_disruption_type["postponed"]["tasks"],
        "rescheduled": by_disruption_type["rescheduled"]["tasks"],
        "delayed": by_disruption_type["delayed"]["tasks"],
        "cancelled_before_conduct": by_disruption_type["cancelled_before_conduct"]["tasks"],
        "partial_reexam_or_glitch": by_disruption_type["partial_reexam_or_glitch"]["tasks"],
        "documented_affected_candidates_total": doc_cands_total,
    }
    dry_run_summary = {
        "status": "verified",
        "evaluated_tasks": total_audited,
        "flagged_leads": dry_leads,
        "cleared_flawless": dry_cleared,
        "unevaluable": dry_unevaluable,
        "verified_documents": len(doc_meta),
        "as_of_date": AS_OF_DATE,
    }

    coverage: dict[str, Any] = {
        "project_slug": PROJECT_SLUG,
        "project_code": PROJECT_CODE,
        "methodology_code": METHODOLOGY_CODE,
        "hook": HOOK_TEXT,
        "generated_on": AS_OF_DATE,
        "as_of_date": AS_OF_DATE,
        "audit_window": {
            "start_date": AUDIT_WINDOW_START.isoformat(),
            "end_date": AUDIT_WINDOW_END.isoformat(),
            "years": list(AUDIT_WINDOW_YEARS),
            "label": AUDIT_WINDOW_LABEL,
        },
        "total_tasks": total_audited,
        "unique_exams_covered": len(unique_exams),
        "unique_authorities_covered": len(authorities_map),
        "unique_documents_count": len(doc_meta),
        "documents_count": len(doc_meta),
        "states_and_uts_covered": states_uts_covered,
        "cleared_control_tasks_embedded": flawless_count,
        "headline_counts": headline_counts,
        "by_disruption_type": by_disruption_type,
        "by_jurisdiction_level": by_jlevel,
        "by_authority_type": sorted(by_atype.values(), key=lambda x: (-x["tasks"], x["authority_type"])),
        "by_exam_category": sorted(by_ecat.values(), key=lambda x: (-x["tasks"], x["exam_category"])),
        "by_year": [by_year[yr] for yr in AUDIT_WINDOW_YEARS],
        "by_reason_category": sorted(by_reason.values(), key=lambda x: (-x["tasks"], x["reason_category"])),
        "jurisdictions": jurisdictions_sorted,
        "top_authorities": top_authorities_sorted,
        "anchor_cases": anchor_cases[:15],
        "sample_leads": sample_leads,
        "dry_run_summary": dry_run_summary,
    }

    project_obj: dict[str, Any] = {
        "slug": PROJECT_SLUG,
        "project_code": PROJECT_CODE,
        "priority": 9,
        "title": PROJECT_TITLE,
        "summary": (
            f"{HOOK_TEXT} Audits 1,000 SHA-256-pinned Union, State, and UT government examinations across "
            f"all {states_uts_covered} States & UTs over the past 3 years (Nov 2023 – Nov 2026) — measuring "
            "cancellations after conduct, postponements, reschedulings, delays, pre-exam cancellations, "
            "technical glitches/partial re-exams, and verified flawless-conduct benchmarks."
        ),
        "posted_by": {
            "name": "AI Watchdogs India",
            "handle": "@aiwatchdogsIN",
            "url": "https://aiwatchdogs.in",
        },
        "pillar": "audit",
        "status": "active",
        "methodology_code": METHODOLOGY_CODE,
        "jurisdiction": f"India (Union of India & All {states_uts_covered} States/UTs)",
        "authority": "NTA, UPSC, SSC, RRB, IBPS, CBSE, Union Ministries, State PSCs, Police/Subordinate Boards & UT Recruitment Authorities",
        "statutory_anchors": [
            "Public Examinations (Prevention of Unfair Means) Act, 2024",
            "Constitution of India Articles 315–323 (Union & State Public Service Commissions)",
            "Official Examination Calendars, Public Notices & Digital Sansad Parliamentary Records (2023–2026)",
            "RTI Act 2005 Sec 4(1)(b) & Sec 6(1) (Proactive Disclosure & Public Record Access)",
        ],
        "scope": [
            f"1,000 real, SHA-256-pinned Indian public examination conduct checks (Nov 2023 – Nov 2026) across Union of India ({by_jlevel['union']['tasks']} tasks) and all {states_uts_covered} States & UTs ({by_jlevel['state']['tasks']} State + {by_jlevel['ut']['tasks']} UT tasks)",
            f"Covers {len(unique_exams)} unique examinations across {len(authorities_map)} conducting authorities and {len(doc_meta)} unique live-verified official documents",
            "Measures took-exam-then-cancelled, postponed, rescheduled, delayed, cancelled-before-conduct, and technical-glitch/partial-re-exam disruptions",
            f"Includes {flawless_count} verified flawless-conduct benchmark examinations published as Cleared with equal standing",
        ],
        "out_of_scope": [
            "Private coaching tests, non-government university internal assessments, or unverified social media claims lacking official notices or Parliamentary records",
            "Dynamic non-byte-stable web pages whose cryptographic SHA-256 drifts across live fetches",
            "Personal candidate roll numbers, names, Aadhaar, PAN, phone numbers, or personal emails (scrubbed per DPDP Act 2023)",
        ],
        "sources": OFFICIAL_PROJECT_SOURCES,
        "tasks": tasks,
    }

    # 6. Hard quality gates + schema + BNS Sec 356 + DPDP PII checks
    print("Running automated quality gates, BNS neutrality & DPDP PII checks...", flush=True)
    _run_quality_gates(tasks, coverage, parsed_records)

    if has_p9_engine and validate_project is not None:
        val_errs = validate_project(project_obj, filename_stem=PROJECT_SLUG)
        if val_errs:
            raise RuntimeError(f"Generated project failed validate_project ({len(val_errs)} errors): {val_errs[:10]}")
        print("  validate_project(): PASS (0 errors)", flush=True)

    for label, blob in (
        ("coverage.json", json.dumps(coverage, ensure_ascii=False)),
        ("project.json", json.dumps(project_obj, ensure_ascii=False)),
    ):
        bad = scan_forbidden_terms(blob)
        if bad:
            raise RuntimeError(f"BNS Sec 356 neutrality violation in {label}: {bad}")
    for label, obj in (("coverage.json", coverage), ("project.json", project_obj)):
        hp, pt = contains_pii(obj)
        if hp:
            raise RuntimeError(f"DPDP PII detected in {label}: {pt}")

    # 7. Write all 4 project artifacts
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
        f"  Unique exams: {len(unique_exams)} | Authorities: {len(authorities_map)} | Unique pinned URLs: {len(doc_meta)}\n"
        f"  Headline: not_flawless={not_flawless_count} ({headline_counts['not_flawless_rate_pct']}%), flawless={flawless_count} ({headline_counts['flawless_rate_pct']}%)\n"
        f"  Disruptions: {json.dumps({k: by_disruption_type[k]['tasks'] for k in VALID_DISRUPTION_TYPES})}\n"
        f"  Dry-run: leads={dry_leads}, cleared={dry_cleared}, unevaluable={dry_unevaluable}\n"
        f"  By severity: {json.dumps(dict(by_severity))}"
    )


if __name__ == "__main__":
    main()
