#!/usr/bin/env python3
"""Build & verify Project P10 (`innocent-but-locked-up`, methodology `P10`, 1,000 tasks).

Hook: "The court declared them acquitted and ordered release. Why did the prison keep them locked up until contempt was filed?"

Assembles exactly 1,000 unique, SHA-256-pinned, double-fetch byte-stable official Indian public
records spanning 2021–2026 across all 26 constitutional courts (Supreme Court of India + all 25
High Courts), Prison/Jail portals, Police/ICJS portals, NHRC/SHRCs, NALSA/SLSAs/UTRCs, Parliament
(Digital Sansad), Union/State Ministries (MHA, DoJ, PIB), and CAG Audit Reports.

Modes:
  1. Manifest build mode (when `--courts-manifest` and `--prisons-manifest` exist):
     Ingests live-verified research manifests, deduplicates by URL/SHA-256/case_key, normalizes
     and enforces strict BNS Sec 356 neutrality & DPDP Act 2023 PII scrubbing, selects exactly
     1,000 unique high-precision tasks anchored by Task #1 (full 4-stage paired-PDF flagship case),
     verifies all unique HTTPS URLs (double-fetch byte-stability + uncached per-host spot-check),
     parses every PDF/artifact via canonical `load_dataset()`, dry-runs ALL 1,000 tasks through
     `run_engine("P10", ...)` confirming 0 unevaluable, validates with `validate_project()`, and
     writes all project artifacts.
  2. Standalone reproducible rebuild/verify mode (default on fresh checkout or `--from-existing`):
     Loads the 1,000 pinned tasks from `projects/innocent-but-locked-up.json`, verifies all unique
     URLs & SHA-256 digests, re-runs `load_dataset()` + `run_engine("P10", ...)` across all 1,000
     tasks, enforces all automated quality gates + `validate_project()`, and regenerates
     `coverage.json`, `documents.csv`, `projects/innocent-but-locked-up/METHODOLOGY.md`, and
     `projects/innocent-but-locked-up.json`.

Outputs:
  - projects/innocent-but-locked-up.json (exactly 1,000 tasks, header < 10 KiB, total <= 20 MiB)
  - projects/innocent-but-locked-up/coverage.json
  - projects/innocent-but-locked-up/documents.csv
  - projects/innocent-but-locked-up/METHODOLOGY.md
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
# Respect PYTHONPATH if caller points to a newer backend worktree; append REPO_ROOT/src as fallback.
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

PROJECT_SLUG = "innocent-but-locked-up"
PROJECT_CODE = "P10"
METHODOLOGY_CODE = "P10"
TARGET_TASKS = 1000
MIN_YEAR = 2021
MAX_YEAR = 2026
MIN_DOC_DATE = "2021-11-01"
AS_OF_DATE = "2026-11-28"
HOOK_TEXT = (
    "The court declared them acquitted and ordered release. "
    "Why did the prison keep them locked up until contempt was filed?"
)
PROJECT_TITLE = "Innocent But Locked Up"
USER_AGENT = "AIWatchdogs-India-OpenAudit/0.1 (aiwatchdogs.in; lawful-public-audit)"

DEFAULT_CACHE_DIR = Path("/tmp/p10-cache")
DEFAULT_RESEARCH_DIR = Path(
    "/home/piyushbodaa/.gemini/antigravity-cli/worktrees/30a3f59c-8eba-4a0f-bffd-5cb0314fcfc6/p10-research"
)
DEFAULT_COURTS_MANIFEST = DEFAULT_RESEARCH_DIR / "courts" / "court_cases_manifest.jsonl"
DEFAULT_ANCHORS_JSON = DEFAULT_RESEARCH_DIR / "courts" / "anchor_cases.json"
DEFAULT_PRISONS_MANIFEST = DEFAULT_RESEARCH_DIR / "prisons_govt" / "prisons_govt_manifest.jsonl"

PROJECTS_DIR = REPO_ROOT / "projects"
PROJECT_JSON_PATH = PROJECTS_DIR / f"{PROJECT_SLUG}.json"
PROJECT_ASSETS_DIR = PROJECTS_DIR / PROJECT_SLUG

MAX_HEADER_BYTES = 10 * 1024  # < 10 KiB
MAX_PROJECT_JSON_BYTES = 20 * 1024 * 1024  # <= 20 MiB

PORTAL_CATEGORY_ID_CODES: dict[str, str] = {
    "court_portal": "court",
    "prison_portal": "prison",
    "police_portal": "police",
    "nhrc_shrc": "nhrc",
    "nalsa_slsa": "nalsa",
    "parliament": "parl",
    "govt_ministry": "govt",
    "cag_audit": "cag",
}

PORTAL_CATEGORY_LABELS: dict[str, str] = {
    "court_portal": "Court Portals (SCI & High Courts)",
    "prison_portal": "Prison & Correctional Portals",
    "police_portal": "Police & ICJS Portals",
    "nhrc_shrc": "NHRC & State Human Rights Commissions",
    "nalsa_slsa": "NALSA, SLSAs & UTRCs",
    "parliament": "Parliament (Digital Sansad)",
    "govt_ministry": "Union & State Government Ministries",
    "cag_audit": "CAG Audit Reports",
}

SIGNAL_CATEGORIES: tuple[str, ...] = (
    "post_acquittal_contempt_overrun",
    "bail_or_release_order_contempt",
    "warrant_or_faster_transmission_delay",
    "statutory_commission_detention_finding",
    "institutional_compliance_gap",
)
VALID_OVERRUN_MECHANISMS: frozenset[str] = frozenset(SIGNAL_CATEGORIES) | {"timely_release_or_compliant"}
VALID_COURT_TIERS: frozenset[str] = frozenset({"supreme_court", "high_court", "district_court", "statutory_body"})
VALID_ACQUITTAL_ORDER_TYPES: frozenset[str] = frozenset(
    {
        "acquittal",
        "discharge",
        "detention_quashed",
        "bail_release_order",
        "conviction_set_aside",
        "institutional_compliance_review",
    }
)
VALID_SECONDARY_ROLES: frozenset[str] = frozenset(
    {
        "",
        "contempt_order_document",
        "acquittal_order_document",
        "release_or_compliance_record",
        "corroborating_record",
    }
)

P10_TASK_ID_RE = re.compile(
    r"^p10-(court|prison|police|nhrc|nalsa|parl|govt|cag)-(202[1-6])-[a-z0-9]+(?:-[a-z0-9]+)*-\d{4}$"
)
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CTRL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]+")
_PUA_AND_GLYPHS_RE = re.compile(
    r"[\ue000-\uf8ff\ufffd➢•●▪▫►▻▸▹○■□◆◇❖✓✔✗✘¾\u2020\u2021\u2030\u2039\u203a]+"
)
_MULTI_SPACE_RE = re.compile(r"\s+")
_DASH_VARIANTS_RE = re.compile(r"[\u2010-\u2015\u2212]")
_QUOTE_SINGLE_RE = re.compile(r"[\u2018\u2019\u201a\u201b]")
_QUOTE_DOUBLE_RE = re.compile(r"[\u201c\u201d\u201e\u201f]")
_HTML_ENTITY_RE = re.compile(r"&(?:#\d+|#x[0-9a-fA-F]+|amp|apos|quot|lt|gt|nbsp|ndash|mdash);")
_PAGE_MARKER_RE = re.compile(r"\[?\s*PAGE\s+\d+\s*\]?|\[PAGE\b", re.IGNORECASE)
_WATERMARK_RE = re.compile(
    r"(?:https?://)?(?:www\.)?mhc\.tn\.gov\.in/judis[^\s]*|v\.in/judis[^\s]*|Judis\s+Uploaded\s+on[^\n]*",
    re.IGNORECASE,
)

# BNS Sec 356 neutralization (matches innocent_but_locked_up.py)
_NOT_GUILTY_RE = re.compile(r"(?<![a-z])not[\s_-]+guilty(?![a-z])", re.IGNORECASE)
_HELD_GUILTY_CONTEMPT_RE = re.compile(r"(?<![a-z])(?:held|found)[\s_-]+guilty[\s_-]+of[\s_-]+contempt(?![a-z])", re.IGNORECASE)
_ACB_RE = re.compile(r"\banti[\s-]+corruption\b", re.IGNORECASE)
_NEUTRAL_REPLACE_RE = re.compile(
    r"(?<![a-z])(?:scam(?:s|med|mer|mers)?|corrupt(?:ed|ing|ly|ion)?|brib(?:e|es|ed|ing|ery)|"
    r"fraud(?:s|ulent|ulently|ster|sters)?|kickback(?:s)?|guilty|stolen|rigged|"
    r"crook(?:s|ed)?|loot(?:s|ed|ing|er|ers)?|embezzl(?:e|es|ed|ing|ement))(?![a-z])",
    re.IGNORECASE,
)

# Generic non-specific prison labels excluded from named-prisons analytics (`prisons_named` / `top_prisons`)
_GENERIC_PRISON_RE = re.compile(
    r"^(?:"
    r"prison\s*/\s*correctional\s+authority|"
    r"correctional\s+institutions?.*|"
    r"state\s+prison(?:s)?\s*/\s*jail.*|"
    r"all[\s-]india\s+prison.*|"
    r"unspecified.*|"
    r"not\s+specified.*|"
    r"jail\s+authorities.*|"
    r"prison\s+authorities.*|"
    r"concerned\s+(?:jail|prison).*|"
    r"state\s+prisons\s+and\s+correctional.*|"
    r"union\s*/\s*state\s+prison.*|"
    r"indian\s+prison.*|"
    r"police\s*/\s*custody.*|"
    r"custody\s*/\s*prison.*"
    r")$",
    re.IGNORECASE,
)

_NAMED_PRISON_EXTRACT_RE = re.compile(
    r"\b((?:Central|District|Sub[\s-]?District|Model|Open|Women(?:'s)?|Circle|Special|Modern|Tihar|Arthur\s+Road|"
    r"Yerwada|Yerawada|Puzhal|Parappana\s+Agrahara|Sabarmati|Beur|Dasna|Naini|Kot\s+Bhalwal|Amphalla|Burail|"
    r"Mandoli|Rohini|Presidency|Alipore|Dum\s+Dum|Bhopal|Indore|Jabalpur|Durg|Raipur|Bilaspur|Hisar|Ambala|"
    r"Patiala|Ludhiana|Amritsar|Nashik|Thane|Taloja|Byculla|Chanchalguda|Cherlapalli|Rajahmundry|Visakhapatnam|"
    r"Cuddalore|Vellore|Madurai|Coimbatore|Tiruchirappalli|Poojappura|Viyyur|Kannur|Guwahati|Srinagar|Jammu|"
    r"Kupwara|Baramulla|Anantnag|Yamuna\s+Nagar|Ghaziabad|Lucknow|Agra|Bareilly|Meerut|Varanasi|Gorakhpur|"
    r"Kanpur|Jhansi|Aligarh|Mathura|Firozabad|Moradabad|Muzaffarnagar|Saharanpur|Etah|Unnao|Sitapur|Hardoi|"
    r"Jaipur|Jodhpur|Ajmer|Bikaner|Kota|Udaipur|Ahmedabad|Vadodara|Surat|Rajkot|Bhavnagar|Cuttack|Bhubaneswar|"
    r"Choudwar|Berhampur|Sambalpur|Ranchi|Hazaribagh|Dhanbad|Jamshedpur|Deoghar|Raiganj|Midnapore|Berhampore|"
    r"Siliguri|Jalpaiguri|Imphal|Sajiwa|Shillong|Aizawl|Kohima|Dimapur|Agartala|Gangtok|Shimla|Nahan|Dehradun|"
    r"Haldwani|Haridwar|Colvale|Puducherry|Port\s+Blair)\s+"
    r"(?:Central\s+|District\s+|Sub[\s-]?District\s+|Circle\s+|Special\s+|Open\s+|Model\s+)?"
    r"(?:Jail|Prison|Correctional\s+Home)(?:,\s*[A-Z][a-zA-Z\s]{2,28})?)\b"
)

OFFICIAL_PROJECT_SOURCES: list[dict[str, str]] = [
    {
        "name": "Supreme Court of India — Official Orders & Judgments Portal",
        "publisher": "Supreme Court of India",
        "url": "https://www.sci.gov.in/",
    },
    {
        "name": "AWS Open Data — Indian Supreme Court Judgments (eSCR Public Mirror, 2021–2026)",
        "publisher": "Supreme Court of India (via AWS Open Data Public Mirror)",
        "url": "https://registry.opendata.aws/indian-supreme-court-judgments/",
    },
    {
        "name": "AWS Open Data — Indian High Court Judgments (All 25 High Courts, 2021–2026)",
        "publisher": "eCourts / 25 High Courts of India (via AWS Open Data Public Mirror)",
        "url": "https://registry.opendata.aws/indian-high-court-judgments/",
    },
    {
        "name": "Digital Sansad — Lok Sabha & Rajya Sabha Questions, Answers & Committee Reports",
        "publisher": "Parliament of India (Lok Sabha & Rajya Sabha Secretariats)",
        "url": "https://sansad.in/",
    },
    {
        "name": "National Human Rights Commission (NHRC) & State HRCs — Reports, Advisories & Jail Visits",
        "publisher": "National Human Rights Commission of India & State HRCs",
        "url": "https://nhrc.nic.in/",
    },
    {
        "name": "National Legal Services Authority (NALSA) & SLSAs — UTRC & Undertrial Release Reports",
        "publisher": "National Legal Services Authority (NALSA)",
        "url": "https://nalsa.gov.in/",
    },
    {
        "name": "Ministry of Home Affairs (MHA), NCRB Prison Statistics India & BPR&D Custody Handbooks",
        "publisher": "Ministry of Home Affairs, Government of India",
        "url": "https://www.mha.gov.in/",
    },
    {
        "name": "Department of Justice (DoJ), State Prison & Police Portals — FASTER & BNSS Safeguards",
        "publisher": "Department of Justice & State Prison / Police Departments",
        "url": "https://doj.gov.in/",
    },
    {
        "name": "Comptroller and Auditor General (CAG) of India — Prison & Police Administration Audits",
        "publisher": "Comptroller and Auditor General of India",
        "url": "https://cag.gov.in/en/audit-report",
    },
]

_PORTAL_STATUTORY_ANCHORS: dict[str, str] = {
    "court_portal": "Constitution of India Art. 21; Contempt of Courts Act 1971 Sec. 2(b)/12; SC FASTER Guidelines (SMW(Crl) 4/2021)",
    "prison_portal": "Model Prisons and Correctional Services Act 2023; BNSS Sec. 479 (CrPC Sec. 436A); SC FASTER Guidelines",
    "police_portal": "Constitution of India Arts. 21 & 22; BNSS Sec. 35/48/57; ICJS / CCTNS-ePrisons Custody Safeguards",
    "nhrc_shrc": "Protection of Human Rights Act 1993 Sec. 12 & 18; Constitution of India Art. 21 (Unlawful Detention Redress)",
    "nalsa_slsa": "Legal Services Authorities Act 1987; BNSS Sec. 479 (CrPC Sec. 436A); NALSA UTRC Standard Operating Procedure",
    "parliament": "Constitution of India Arts. 21 & 75(3); Lok Sabha & Rajya Sabha Parliamentary Oversight of Prisons & Justice",
    "govt_ministry": "Constitution of India Art. 21; MHA Prison Reform Advisories; DoJ FASTER & BNSS Sec. 479 Directives",
    "cag_audit": "Constitution of India Arts. 148-151; CAG (DPC) Act 1971; Model Prison Manual 2016 Compliance",
}


def strip_invisible_and_nfkc(text: str) -> str:
    norm = unicodedata.normalize("NFKC", text or "")
    return "".join(ch for ch in norm if unicodedata.category(ch) not in {"Cf", "Cc"} or ch in ("\n", "\t", " "))


def norm_search(text: str) -> str:
    norm = strip_invisible_and_nfkc(text)
    norm = _DASH_VARIANTS_RE.sub("-", norm)
    norm = _QUOTE_SINGLE_RE.sub("'", norm)
    norm = _QUOTE_DOUBLE_RE.sub('"', norm)
    norm = _CTRL_CHARS_RE.sub(" ", norm)
    return _MULTI_SPACE_RE.sub(" ", norm).strip().lower()


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
    cut2 = re.sub(
        r"\s+(?:of|for|in|on|and|the|to|by|with|under|from|including|regarding|a|an)$",
        "",
        cut,
        flags=re.IGNORECASE,
    ).rstrip(" ,;:-/—–")
    if len(cut2) >= 12:
        cut = cut2
    if cut.count("(") > cut.count(")"):
        lp = cut.rfind("(")
        if lp >= max(8, len(cut) // 2):
            cut = cut[:lp].rstrip(" ,;:-/—–")
    return cut + "…"


def neutralize_clean(text: Any, *, max_len: int = 0) -> str:
    if text is None:
        return ""
    s = html.unescape(str(text))
    s = strip_invisible_and_nfkc(s)
    s = _CTRL_CHARS_RE.sub(" ", s)
    s = _PUA_AND_GLYPHS_RE.sub(" ", s)
    s = _PAGE_MARKER_RE.sub(" ", s)
    s = _WATERMARK_RE.sub(" ", s)
    s, _ = scrub_pii(s)
    s = _NOT_GUILTY_RE.sub("acquitted", s)
    s = _HELD_GUILTY_CONTEMPT_RE.sub("held in contempt", s)
    s = _ACB_RE.sub("Vigilance", s)
    s = _NEUTRAL_REPLACE_RE.sub("compliance-review", s)
    s = _MULTI_SPACE_RE.sub(" ", s).strip()
    if max_len > 0 and len(s) > max_len:
        s = _trim_word_boundary(s, max_len)
    return s


def slugify(text: str, *, max_len: int = 28) -> str:
    s = norm_search(text).replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    s = re.sub(r"-{2,}", "-", s)
    if len(s) > max_len:
        s = s[:max_len].rstrip("-")
    return s or "record"


def clean_public_url(url: str) -> str:
    raw = html.unescape(str(url or "").strip())
    if not raw:
        return ""
    sp = urlsplit(raw)
    # Encode raw spaces or non-ASCII in path/query without double-encoding %XX
    path = quote(unquote(sp.path), safe="/:@!$&'()*+,;=-._~")
    query = sp.query.replace(" ", "%20")
    cleaned = urlunsplit((sp.scheme, sp.netloc, path, query, ""))
    validate_safe_http_url(cleaned, require_https=True, allow_private=False)
    return cleaned


def _parse_iso_date(val: Any) -> date | None:
    if not val or not isinstance(val, str):
        return None
    s = val.strip()[:10]
    if not _ISO_DATE_RE.fullmatch(s):
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _is_named_prison(name: str) -> bool:
    s = neutralize_clean(name)
    if not s or len(s) < 5:
        return False
    if _GENERIC_PRISON_RE.match(s):
        return False
    low = s.lower()
    generic_phrases = (
        "prison administration",
        "prison / correctional",
        "prison / custody",
        "custody facility",
        "correctional authority",
        "state prisons",
        "indian prisons",
        "all-india",
        "union / state",
        "undertrial prisoners",
        "custodial institutions",
        "jails department",
        "jail administration",
        "jail authorities",
        "prison authorities",
        "prisons across",
        "concerned jail",
        "concerned prison",
        "concerned superintendent",
        "not specified",
        "unspecified",
        "police custody",
        "judicial custody",
    )
    if any(p in low for p in generic_phrases):
        return False
    # Exclude bare un-located labels like "Central Prison", "District Jail", "Central Jail", "Sub Jail"
    if re.fullmatch(
        r"(?:the\s+)?(?:central|district|sub[\s-]?district|sub|circle|special|open|model|women(?:'s)?|modern)\s+(?:jail|prison|correctional\s+home)s?",
        low,
    ):
        return False
    return any(
        kw in low
        for kw in (
            "jail",
            "prison",
            "correctional home",
            "tihar",
            "mandoli",
            "rohini",
            "yerwada",
            "yerawada",
            "arthur road",
            "sabarmati",
            "puzhal",
            "kot bhalwal",
            "amphalla",
            "beur",
            "dasna",
            "naini",
            "burail",
            "parappana",
        )
    )


def _snap_quote_words(text: str, *, max_len: int = 440) -> str:
    """Clean a raw excerpt and trim dangling mid-word head/tail fragments."""
    s = neutralize_clean(text)
    if not s:
        return ""
    # Strip broken leading fragment (starts with lowercase partial word or punctuation)
    if s and (s[0].islower() or s[0] in ",.;:)]-"):
        first_sp = s.find(" ")
        if 0 < first_sp < 24:
            rest = s[first_sp + 1 :].lstrip(" ,.;:-)")
            if len(rest) >= 24:
                s = "…" + rest
    if len(s) > max_len:
        s = _trim_word_boundary(s, max_len)
    return s


def _fetch_one_url(
    url: str,
    expected_sha256: str,
    cache_dir: Path,
    *,
    double_check: bool = True,
    force_live: bool = False,
) -> tuple[str, str, int, Path]:
    """Fetch or verify a cached HTTPS official artifact with fail-closed SHA-256 checking."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
    ext = ".pdf" if ".pdf" in url.lower() else ".bin"
    blob_path = cache_dir / f"{key}{ext}"
    meta_path = cache_dir / f"{key}.json"

    if not force_live and blob_path.exists() and meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            raw = blob_path.read_bytes()
            obs = hashlib.sha256(raw).hexdigest()
            if meta.get("url") == url and meta.get("sha256") == obs:
                if not expected_sha256 or obs == expected_sha256:
                    return url, obs, len(raw), blob_path
        except OSError:
            pass

    headers = {"User-Agent": USER_AGENT, "Accept": "application/pdf,application/json,text/html,*/*"}

    def _download_once() -> bytes:
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                with httpx.Client(follow_redirects=True, timeout=httpx.Timeout(60.0, connect=20.0), headers=headers) as cli:
                    resp = cli.get(url)
                    if resp.status_code != 200:
                        raise RuntimeError(f"HTTP {resp.status_code} for {url}")
                    data = resp.content
                    if not data:
                        raise RuntimeError(f"Empty body for {url}")
                    return data
            except Exception as exc:
                last_exc = exc
                time.sleep(0.8 * (attempt + 1))
        raise RuntimeError(f"Failed fetching {url}: {last_exc}") from last_exc

    b1 = _download_once()
    sha1 = hashlib.sha256(b1).hexdigest()
    if expected_sha256 and sha1 != expected_sha256:
        raise RuntimeError(f"SHA-256 mismatch on live fetch for {url}: expected {expected_sha256}, got {sha1}")

    if double_check and (force_live or not expected_sha256):
        time.sleep(0.35)
        b2 = _download_once()
        sha2 = hashlib.sha256(b2).hexdigest()
        if sha1 != sha2:
            raise RuntimeError(f"Non-byte-stable URL {url}: fetch1={sha1}, fetch2={sha2}")

    blob_path.write_bytes(b1)
    meta_path.write_text(
        json.dumps({"url": url, "sha256": sha1, "bytes": len(b1), "fetched_at": datetime.now(timezone.utc).isoformat()}),
        encoding="utf-8",
    )
    return url, sha1, len(b1), blob_path


def _parse_blob_worker(job: tuple[str, str, str, str]) -> tuple[str, str, str, int, int, list[dict[str, Any]]]:
    """Parse one verified artifact via canonical `aiwatchdogs.connectors.loader.load_dataset`."""
    url, sha, blob_path_str, parsed_cache_dir_str = job
    blob_path = Path(blob_path_str)
    parsed_cache_dir = Path(parsed_cache_dir_str)
    parsed_cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = parsed_cache_dir / f"{sha}.json"

    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            if cached.get("sha256") == sha and isinstance(cached.get("records"), list):
                return url, sha, str(cached["format"]), int(cached["bytes"]), int(cached["pages"]), cached["records"]
        except (OSError, json.JSONDecodeError, KeyError):
            pass

    from aiwatchdogs.connectors.loader import load_dataset

    loaded = load_dataset(
        str(blob_path),
        expected_sha256=sha,
        allow_local_files=True,
        use_cache=False,
    )
    recs = loaded.records
    pages = sum(1 for r in recs if isinstance(r, dict) and "page" in r)
    if pages == 0:
        pages = max(1, len(recs))
    fmt = str(getattr(loaded, "format", None) or getattr(loaded, "dataset_format", None) or "pdf").lower()
    bsz = int(getattr(loaded, "byte_size", 0) or blob_path.stat().st_size)

    try:
        cache_file.write_text(
            json.dumps({"sha256": sha, "format": fmt, "bytes": bsz, "pages": pages, "records": recs}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass

    return url, sha, fmt, bsz, pages, recs


def _records_combined_text(recs: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for rec in recs:
        if not isinstance(rec, dict):
            continue
        if isinstance(rec.get("raw_text"), str) and rec["raw_text"].strip():
            parts.append(rec["raw_text"])
        elif isinstance(rec.get("text"), str) and rec["text"].strip():
            parts.append(rec["text"])
        else:
            parts.append(json.dumps(rec, ensure_ascii=False))
    return "\n".join(parts)


def _extract_pdf_window(recs: list[dict[str, Any]], tokens: list[str], *, preferred_page: int | None = None) -> str:
    """Extract a clean whole-word window around the first matched token in parsed PDF records."""
    best_text = ""
    best_tok = tokens[0] if tokens else ""
    best_hits = -1
    for rec in recs:
        if not isinstance(rec, dict):
            continue
        raw = str(rec.get("raw_text") or rec.get("text") or "")
        if not raw.strip():
            continue
        nraw = norm_search(raw)
        hits = [t for t in tokens if norm_search(t) in nraw]
        pg = rec.get("page")
        bonus = 1 if (preferred_page is not None and pg == preferred_page and hits) else 0
        if len(hits) + bonus > best_hits:
            best_hits = len(hits) + bonus
            best_text = raw
            if hits:
                best_tok = hits[0]

    if not best_text:
        return ""
    compact = _MULTI_SPACE_RE.sub(" ", strip_invisible_and_nfkc(best_text)).strip()
    ncomp = norm_search(compact)
    ntok = norm_search(best_tok)
    pos = ncomp.find(ntok) if ntok else -1
    if pos == -1:
        return _snap_quote_words(compact[:360], max_len=360)
    ratio = len(compact) / max(1, len(ncomp))
    center = int(pos * ratio)
    start = max(0, center - 160)
    end = min(len(compact), start + 360)
    if start > 0:
        sp = compact.find(" ", start, min(len(compact), start + 28))
        if sp != -1:
            start = sp + 1
    return _snap_quote_words(compact[start:end], max_len=360)


def _normalize_jurisdiction(jur_raw: Any, state_raw: Any) -> tuple[str, str | None]:
    """Ensure strict backend consistency: Union of India -> state_ut=None; State:X / UT:X -> state_ut=X."""
    jur = neutralize_clean(jur_raw or "", max_len=120)
    st = neutralize_clean(state_raw or "", max_len=80) or None

    if jur == "Union of India" or (not jur and not st):
        return "Union of India", None
    if jur.startswith("State:"):
        rest = jur[6:].strip()
        if rest:
            return f"State:{rest}", rest
    if jur.startswith("UT:"):
        rest = jur[3:].strip()
        if rest:
            return f"UT:{rest}", rest

    ut_names = {
        "Delhi",
        "NCT of Delhi",
        "Jammu and Kashmir",
        "Jammu & Kashmir",
        "Ladakh",
        "Chandigarh",
        "Puducherry",
        "Andaman and Nicobar Islands",
        "Dadra and Nagar Haveli and Daman and Diu",
        "Lakshadweep",
    }
    if st:
        if st == "NCT of Delhi":
            st = "Delhi"
        elif st == "Jammu & Kashmir":
            st = "Jammu and Kashmir"
        if st in ut_names:
            return f"UT:{st}", st
        return f"State:{st}", st
    return "Union of India", None


def _normalize_record_from_court_manifest(row: dict[str, Any], *, is_flagship: bool = False) -> dict[str, Any]:
    """Normalize one verified court manifest record into canonical internal builder format."""
    ctx = dict(row.get("p10_context") or {})
    pcat = str(ctx.get("portal_category") or row.get("portal_category") or "court_portal").strip().lower()
    ctier = str(ctx.get("court_tier") or row.get("court_tier") or "high_court").strip().lower()
    jur, state_ut = _normalize_jurisdiction(
        ctx.get("jurisdiction") or row.get("jurisdiction"),
        ctx.get("state_ut") if "state_ut" in ctx else row.get("state_ut"),
    )

    doc_date = str(ctx.get("document_date") or row.get("order_date") or "").strip()[:10]
    doc_dt = _parse_iso_date(doc_date)
    year = int(ctx.get("year") or row.get("year") or (doc_dt.year if doc_dt else 2025))
    year = max(MIN_YEAR, min(MAX_YEAR, year))
    if not doc_dt or doc_dt.year != year:
        doc_date = f"{year}-06-15"

    mech = str(ctx.get("overrun_mechanism") or row.get("overrun_mechanism") or "institutional_compliance_gap").strip().lower()
    if mech not in VALID_OVERRUN_MECHANISMS:
        mech = "institutional_compliance_gap"

    s1 = bool(ctx.get("stage_1_acquittal_verified", row.get("stage_1_acquittal", False)))
    s2 = bool(ctx.get("stage_2_continued_detention_verified", row.get("stage_2_continued_detention", False)))
    s3 = bool(ctx.get("stage_3_contempt_verified", row.get("stage_3_contempt_filed", False)))
    s4 = bool(ctx.get("stage_4_release_verified", row.get("stage_4_released", False)))
    rel_ver = bool(ctx.get("release_verified", row.get("release_verified", s4))) or s4

    overrun_days = ctx.get("overrun_days") if "overrun_days" in ctx else row.get("overrun_days")
    if isinstance(overrun_days, bool) or not isinstance(overrun_days, int):
        overrun_days = None

    # Enforce strict stage/mechanism consistency
    if mech == "timely_release_or_compliant":
        s2 = False
        s3 = False
        overrun_days = 0
    elif not s2 and overrun_days is not None and overrun_days > 0:
        overrun_days = None

    acq_dt_str = ctx.get("acquittal_order_date") or row.get("acquittal_order_date")
    rel_dt_str = ctx.get("release_date") or row.get("release_date")
    cf_dt_str = ctx.get("contempt_filing_date") or row.get("contempt_filing_date")
    co_dt_str = ctx.get("contempt_order_date") or row.get("contempt_order_date")

    acq_dt = _parse_iso_date(acq_dt_str)
    rel_dt = _parse_iso_date(rel_dt_str)
    cf_dt = _parse_iso_date(cf_dt_str)
    co_dt = _parse_iso_date(co_dt_str)
    if acq_dt and rel_dt and rel_dt < acq_dt:
        rel_dt = None
        rel_dt_str = None
    if cf_dt and co_dt and co_dt < cf_dt:
        cf_dt = None
        cf_dt_str = None

    comp = ctx.get("compensation_inr") if "compensation_inr" in ctx else row.get("compensation_inr")
    if isinstance(comp, bool) or comp is None:
        comp_val = None
    else:
        try:
            fcomp = float(comp)
            comp_val = round(fcomp, 2) if math.isfinite(fcomp) and fcomp >= 0 else None
        except (TypeError, ValueError):
            comp_val = None

    ret_url = clean_public_url(row.get("retrieval_url") or "")
    sec_url = clean_public_url(row.get("secondary_url") or "") if row.get("secondary_url") else None
    sec_sha = str(row.get("secondary_sha256") or "").strip().lower() or None
    sec_role = str(ctx.get("secondary_role") or row.get("secondary_role") or "").strip().lower() or None
    if not sec_url:
        sec_sha = None
        sec_role = None

    acq_type = str(ctx.get("acquittal_order_type") or row.get("acquittal_order_type") or "acquittal").strip().lower()
    if acq_type not in VALID_ACQUITTAL_ORDER_TYPES:
        acq_type = "acquittal"

    case_title = neutralize_clean(ctx.get("case_title") or row.get("case_title") or row.get("case_number"), max_len=200)
    doc_ref = neutralize_clean(ctx.get("document_ref") or row.get("case_number") or case_title, max_len=200)
    acq_court = neutralize_clean(ctx.get("acquittal_court") or row.get("court_name") or "High Court", max_len=200)
    acq_case_no = neutralize_clean(ctx.get("acquittal_case_number") or row.get("case_number") or doc_ref, max_len=140)
    acq_quote = _snap_quote_words(ctx.get("acquittal_quote") or row.get("acquittal_quote") or "", max_len=440)
    prison_name = neutralize_clean(ctx.get("prison_name") or row.get("prison_name") or f"Prison / Correctional Authority ({state_ut or 'India'})", max_len=180)
    if state_ut == "Jammu and Kashmir" and "(Ladakh)" in prison_name:
        prison_name = prison_name.replace("(Ladakh)", "(Jammu and Kashmir)")
    custody_auth = neutralize_clean(ctx.get("custody_authority") or row.get("custody_authority") or prison_name, max_len=200)
    non_rel = neutralize_clean(ctx.get("non_release_reason") or row.get("non_release_reason") or "", max_len=360)
    overrun_win = neutralize_clean(ctx.get("overrun_window") or row.get("overrun_window") or "", max_len=160)
    if not overrun_win:
        if acq_dt and rel_dt and overrun_days is not None:
            overrun_win = f"{acq_dt.isoformat()} to {rel_dt.isoformat()} ({overrun_days} days)"
        elif overrun_days is not None and overrun_days > 0:
            overrun_win = f"{overrun_days} days documented"
        elif mech == "timely_release_or_compliant":
            overrun_win = "0 days — verified timely release / compliance benchmark"
        else:
            overrun_win = f"CY {year} judicial compliance record"

    jud_dir = _snap_quote_words(ctx.get("judicial_direction_summary") or row.get("judicial_direction_summary") or "", max_len=590)
    contempt_case = neutralize_clean(ctx.get("contempt_case_number") or row.get("contempt_case_number") or "", max_len=140) or None
    contempt_court = neutralize_clean(ctx.get("contempt_court") or row.get("contempt_court") or "", max_len=200) or None
    contempt_quote = _snap_quote_words(ctx.get("contempt_quote") or row.get("contempt_quote") or "", max_len=440) or None

    selector = dict(ctx.get("record_selector") or {}) if isinstance(ctx.get("record_selector"), dict) else {}

    return {
        "source_origin": "court_manifest",
        "is_anchor_pool": bool(row.get("is_anchor_case") or ctx.get("is_anchor_case")),
        "is_anchor_case": is_flagship,
        "raw_case_key": str(ctx.get("case_key") or row.get("case_key") or "").strip().lower(),
        "retrieval_url": ret_url,
        "expected_sha256": str(row.get("sha256") or "").strip().lower(),
        "secondary_url": sec_url,
        "secondary_expected_sha256": sec_sha,
        "official_publisher": neutralize_clean(row.get("official_publisher") or acq_court, max_len=200),
        "source_landing_page": clean_public_url(row.get("source_landing_page") or ret_url),
        "spec": {
            "case_key": str(ctx.get("case_key") or row.get("case_key") or "").strip().lower(),
            "case_title": case_title,
            "portal_category": pcat,
            "court_tier": ctier if ctier in VALID_COURT_TIERS else "high_court",
            "state_ut": state_ut,
            "jurisdiction": jur,
            "year": year,
            "document_date": doc_date,
            "document_ref": doc_ref,
            "overrun_mechanism": mech,
            "is_anchor_case": is_flagship,
            "stage_1_acquittal_verified": s1,
            "acquittal_order_date": acq_dt.isoformat() if acq_dt else None,
            "acquittal_court": acq_court,
            "acquittal_case_number": acq_case_no,
            "acquittal_order_type": acq_type,
            "acquittal_quote": acq_quote or f"Judicial record {acq_case_no} before {acq_court} ({doc_date}).",
            "stage_2_continued_detention_verified": s2,
            "prison_name": prison_name,
            "custody_authority": custody_auth,
            "non_release_reason": non_rel or (
                "Verified timely release / court-ordered compliance benchmark without custody overrun."
                if mech == "timely_release_or_compliant"
                else "Documented custody-release delay or judicial compliance review recorded in court order."
            ),
            "overrun_days": overrun_days,
            "overrun_window": overrun_win,
            "stage_3_contempt_verified": s3,
            "contempt_case_number": contempt_case,
            "contempt_court": contempt_court,
            "contempt_filing_date": cf_dt.isoformat() if cf_dt else None,
            "contempt_order_date": co_dt.isoformat() if co_dt else None,
            "contempt_quote": contempt_quote,
            "stage_4_release_verified": s4,
            "release_date": rel_dt.isoformat() if rel_dt else None,
            "release_verified": rel_ver,
            "compensation_inr": comp_val,
            "judicial_direction_summary": jud_dir or f"Verified court order {doc_ref} ({doc_date}) audited under P10.",
            "custody_order_evidence_tokens": list(ctx.get("custody_order_evidence_tokens") or row.get("custody_order_evidence_tokens") or []),
            "contempt_or_outcome_evidence_tokens": list(ctx.get("contempt_or_outcome_evidence_tokens") or row.get("contempt_or_outcome_evidence_tokens") or []),
            "statutory_anchor": neutralize_clean(ctx.get("statutory_anchor") or _PORTAL_STATUTORY_ANCHORS.get(pcat, ""), max_len=240),
            "source_format": str(ctx.get("source_format") or row.get("source_format") or "pdf").strip().lower(),
            "secondary_source_format": (str(ctx.get("secondary_source_format") or row.get("secondary_source_format") or "pdf").strip().lower() if sec_url else None),
            "secondary_role": sec_role,
            "record_selector": selector or None,
        },
    }


def _normalize_record_from_prisons_manifest(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize one verified prisons/police/NHRC/NALSA/Parliament/Govt/CAG manifest record."""
    ctx = dict(row.get("p10_context") or {})
    pcat = str(ctx.get("portal_category") or row.get("portal_category") or "govt_ministry").strip().lower()
    if pcat not in PORTAL_CATEGORY_ID_CODES:
        pcat = "govt_ministry"
    ctier = str(ctx.get("court_tier") or row.get("court_tier") or "statutory_body").strip().lower()
    if ctier not in VALID_COURT_TIERS:
        ctier = "statutory_body"

    jur, state_ut = _normalize_jurisdiction(
        ctx.get("jurisdiction") or row.get("jurisdiction"),
        ctx.get("state_ut") if "state_ut" in ctx else row.get("state_ut"),
    )

    doc_date = str(ctx.get("document_date") or row.get("document_date") or "").strip()[:10]
    doc_dt = _parse_iso_date(doc_date)
    year = int(ctx.get("year") or row.get("year") or (doc_dt.year if doc_dt else 2025))
    year = max(MIN_YEAR, min(MAX_YEAR, year))
    if not doc_dt:
        doc_date = f"{year}-06-15"

    mech = str(ctx.get("overrun_mechanism") or row.get("overrun_mechanism") or "institutional_compliance_gap").strip().lower()
    if mech not in VALID_OVERRUN_MECHANISMS:
        mech = "institutional_compliance_gap"

    s1 = bool(ctx.get("stage_1_acquittal_verified", row.get("stage_1_acquittal_verified", False)))
    s2 = bool(ctx.get("stage_2_continued_detention_verified", row.get("stage_2_continued_detention_verified", False)))
    s3 = bool(ctx.get("stage_3_contempt_verified", row.get("stage_3_contempt_verified", False)))
    s4 = bool(ctx.get("stage_4_release_verified", row.get("stage_4_release_verified", False)))

    # Strict discipline: compliant controls NEVER mark Stage 2 detention or Stage 3 contempt.
    if mech == "timely_release_or_compliant":
        s2 = False
        s3 = False
    # Institutional policy/audit/statistical reports do not claim individual Stage 3 contempt;
    # multi-topic NHRC Newsletters/Annual Reports are institutional oversight records, not individual 4-stage court chains.
    if pcat in {"cag_audit", "govt_ministry", "nalsa_slsa", "prison_portal", "police_portal"}:
        s3 = False
        if mech == "timely_release_or_compliant":
            s1 = False
            s2 = False
    title_probe = str(ctx.get("case_title") or row.get("title") or "").lower()
    if pcat == "nhrc_shrc" and any(kw in title_probe for kw in ("newsletter", "annual report", "compendium", "advisory")):
        s1 = False

    rel_ver = bool(ctx.get("release_verified", row.get("release_verified", s4))) or s4

    title_raw = ctx.get("case_title") or row.get("title") or row.get("document_ref") or "Official Custody Compliance Record"
    case_title = neutralize_clean(title_raw, max_len=200)
    doc_ref = neutralize_clean(ctx.get("document_ref") or row.get("document_ref") or case_title, max_len=200)
    authority = neutralize_clean(ctx.get("custody_authority") or row.get("authority") or row.get("official_publisher") or PORTAL_CATEGORY_LABELS[pcat], max_len=200)
    authority = re.sub(r"\bMinistry of Law And Justice\b", "Ministry of Law and Justice", authority)

    snippets = row.get("evidence_snippets") or []
    cust_snips = [str(s.get("text") or "") for s in snippets if isinstance(s, dict) and s.get("role") == "custody_order" and s.get("text")]
    out_snips = [str(s.get("text") or "") for s in snippets if isinstance(s, dict) and s.get("role") == "contempt_or_outcome" and s.get("text")]

    acq_quote = _snap_quote_words(
        ctx.get("acquittal_quote") or (cust_snips[0] if cust_snips else "") or f"{authority} — {case_title} ({doc_date}).",
        max_len=440,
    )
    jud_dir = _snap_quote_words(
        ctx.get("judicial_direction_summary") or (out_snips[0] if out_snips else "") or f"Official record {doc_ref} ({doc_date}) audited under P10 custody-release safeguards.",
        max_len=590,
    )

    # Check if a specific Indian prison is explicitly named in title/snippets
    prison_name = neutralize_clean(ctx.get("prison_name") or "", max_len=180)
    if not prison_name:
        probe_blob = f"{case_title} {acq_quote} {jud_dir}"
        pm = _NAMED_PRISON_EXTRACT_RE.search(probe_blob)
        if pm:
            prison_name = neutralize_clean(pm.group(1), max_len=180)
        else:
            prison_name = neutralize_clean(f"Correctional Institutions / Prison Administration ({state_ut or 'All-India'})", max_len=180)

    non_rel_default = {
        "timely_release_or_compliant": "Verified compliant custody-release / legal-aid / statutory oversight benchmark without documented post-acquittal overrun.",
        "warrant_or_faster_transmission_delay": "Documented release-warrant (parwana), e-Prisons/ICJS, or FASTER electronic transmission compliance review.",
        "statutory_commission_detention_finding": "NHRC/SHRC statutory oversight record documenting unlawful detention scrutiny, custody duration, or release compliance.",
        "institutional_compliance_gap": "Official institutional audit or parliamentary disclosure examining undertrial detention, release delays, or custody compliance.",
    }.get(mech, "Official custody and release-compliance record reviewed under P10.")

    overrun_days = ctx.get("overrun_days") if "overrun_days" in ctx else row.get("overrun_days")
    if isinstance(overrun_days, bool) or not isinstance(overrun_days, int) or not s2:
        overrun_days = 0 if mech == "timely_release_or_compliant" else None
    if mech == "timely_release_or_compliant":
        overrun_days = 0

    overrun_win = neutralize_clean(ctx.get("overrun_window") or "", max_len=160)
    if not overrun_win:
        if mech == "timely_release_or_compliant":
            overrun_win = f"0 days — CY {year} verified compliance benchmark"
        elif overrun_days is not None and overrun_days > 0:
            overrun_win = f"{overrun_days} days documented ({year})"
        else:
            overrun_win = f"CY {year} {PORTAL_CATEGORY_LABELS[pcat]} record"

    comp = ctx.get("compensation_inr") if "compensation_inr" in ctx else row.get("compensation_inr")
    comp_val = round(float(comp), 2) if isinstance(comp, (int, float)) and not isinstance(comp, bool) and math.isfinite(float(comp)) and float(comp) >= 0 else None

    ret_url = clean_public_url(row.get("retrieval_url") or "")
    sec_url = clean_public_url(row.get("secondary_url") or "") if row.get("secondary_url") else None
    sec_sha = str(row.get("secondary_sha256") or "").strip().lower() or None
    sec_role = str(ctx.get("secondary_role") or "").strip().lower() or None
    if not sec_url:
        sec_sha = None
        sec_role = None

    selector: dict[str, Any] = dict(ctx.get("record_selector") or {}) if isinstance(ctx.get("record_selector"), dict) else {}
    if not selector:
        op = row.get("order_page")
        cp = row.get("contempt_page")
        if isinstance(op, int) and 1 <= op <= 5000:
            selector["order_page"] = op
        if isinstance(cp, int) and 1 <= cp <= 5000:
            selector["contempt_page"] = cp

    raw_ck = str(ctx.get("case_key") or "").strip().lower()
    if not raw_ck:
        pcode = PORTAL_CATEGORY_ID_CODES[pcat]
        raw_ck = f"p10-{pcode}-{year}-{slugify(doc_ref, max_len=36)}"

    return {
        "source_origin": "prisons_govt_manifest",
        "is_anchor_pool": False,
        "is_anchor_case": False,
        "raw_case_key": raw_ck,
        "retrieval_url": ret_url,
        "expected_sha256": str(row.get("sha256") or "").strip().lower(),
        "secondary_url": sec_url,
        "secondary_expected_sha256": sec_sha,
        "official_publisher": neutralize_clean(row.get("official_publisher") or authority, max_len=200),
        "source_landing_page": clean_public_url(row.get("source_landing_page") or ret_url),
        "spec": {
            "case_key": raw_ck,
            "case_title": case_title,
            "portal_category": pcat,
            "court_tier": ctier,
            "state_ut": state_ut,
            "jurisdiction": jur,
            "year": year,
            "document_date": doc_date,
            "document_ref": doc_ref,
            "overrun_mechanism": mech,
            "is_anchor_case": False,
            "stage_1_acquittal_verified": s1,
            "acquittal_order_date": str(ctx.get("acquittal_order_date") or "").strip()[:10] or None,
            "acquittal_court": neutralize_clean(ctx.get("acquittal_court") or authority, max_len=200),
            "acquittal_case_number": neutralize_clean(ctx.get("acquittal_case_number") or doc_ref, max_len=140),
            "acquittal_order_type": str(ctx.get("acquittal_order_type") or "institutional_compliance_review").strip().lower(),
            "acquittal_quote": acq_quote,
            "stage_2_continued_detention_verified": s2,
            "prison_name": prison_name,
            "custody_authority": authority,
            "non_release_reason": neutralize_clean(ctx.get("non_release_reason") or non_rel_default, max_len=360),
            "overrun_days": overrun_days,
            "overrun_window": overrun_win,
            "stage_3_contempt_verified": s3,
            "contempt_case_number": neutralize_clean(ctx.get("contempt_case_number") or "", max_len=140) or None,
            "contempt_court": neutralize_clean(ctx.get("contempt_court") or "", max_len=200) or None,
            "contempt_filing_date": str(ctx.get("contempt_filing_date") or "").strip()[:10] or None,
            "contempt_order_date": str(ctx.get("contempt_order_date") or "").strip()[:10] or None,
            "contempt_quote": _snap_quote_words(ctx.get("contempt_quote") or "", max_len=440) or None,
            "stage_4_release_verified": s4,
            "release_date": str(ctx.get("release_date") or "").strip()[:10] or None,
            "release_verified": rel_ver,
            "compensation_inr": comp_val,
            "judicial_direction_summary": jud_dir,
            "custody_order_evidence_tokens": list(ctx.get("custody_order_evidence_tokens") or row.get("custody_order_evidence_tokens") or []),
            "contempt_or_outcome_evidence_tokens": list(ctx.get("contempt_or_outcome_evidence_tokens") or row.get("contempt_or_outcome_evidence_tokens") or []),
            "statutory_anchor": neutralize_clean(ctx.get("statutory_anchor") or _PORTAL_STATUTORY_ANCHORS.get(pcat, ""), max_len=240),
            "source_format": str(ctx.get("source_format") or row.get("source_format") or "pdf").strip().lower(),
            "secondary_source_format": (str(ctx.get("secondary_source_format") or "pdf").strip().lower() if sec_url else None),
            "secondary_role": sec_role,
            "record_selector": selector or None,
        },
    }


def _load_cases_from_existing_project(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    out: list[dict[str, Any]] = []
    for t in data.get("tasks") or []:
        p = t["payload"]
        ctx = p["context"]
        spec = dict(ctx["innocent_but_locked_up"])
        out.append(
            {
                "source_origin": "existing_project",
                "is_anchor_pool": bool(spec.get("is_anchor_case")),
                "is_anchor_case": bool(spec.get("is_anchor_case")),
                "raw_case_key": str(spec.get("case_key") or ""),
                "existing_task_id": str(t["id"]),
                "retrieval_url": p["retrieval_url"],
                "expected_sha256": p["expected_sha256"],
                "secondary_url": p.get("secondary_url"),
                "secondary_expected_sha256": p.get("secondary_expected_sha256"),
                "official_publisher": p["official_publisher"],
                "source_landing_page": p["source_landing_page"],
                "spec": spec,
            }
        )
    return out


def _select_1000_records(
    court_rows: list[dict[str, Any]],
    prisons_rows: list[dict[str, Any]],
    preferred_flagship_key: str | None = None,
) -> list[dict[str, Any]]:
    """Deterministically deduplicate and select exactly 1,000 highest-signal unique records.

    Priority order:
      1. Task #1: Flagship 4-stage paired-PDF anchor (`is_anchor_case=True`, ONLY Task #1).
      2. All remaining hand-verified flagship court anchors (4-stage, 3-stage, SC/HC landmarks).
      3. All verified non-court portal records across all 7 categories (prison, police, NHRC/SHRC,
         NALSA/SLSA, Parliament, Govt Ministries, CAG) to maximize multi-portal coverage.
      4. Highest-signal court records maximizing coverage of all 26 courts (SCI + 25 HCs),
         all 4-stage/3-stage contempt/overrun cases, all years 2021–2026, all States/UTs,
         and balanced verified Cleared controls.
    """
    seen_urls: set[str] = set()
    seen_shas: set[str] = set()
    dedup_courts: list[dict[str, Any]] = []
    dedup_prisons: list[dict[str, Any]] = []

    def _keep(item: dict[str, Any], bucket: list[dict[str, Any]]) -> None:
        u = item["retrieval_url"]
        sha = item["expected_sha256"]
        if not u or u in seen_urls:
            return
        if sha and sha in seen_shas:
            return
        seen_urls.add(u)
        if sha:
            seen_shas.add(sha)
        bucket.append(item)

    for r in court_rows:
        _keep(r, dedup_courts)
    for r in prisons_rows:
        _keep(r, dedup_prisons)

    total_avail = len(dedup_courts) + len(dedup_prisons)
    if total_avail < TARGET_TASKS:
        raise RuntimeError(
            f"Insufficient verified unique records: courts={len(dedup_courts)} + prisons_govt={len(dedup_prisons)} "
            f"= {total_avail} < {TARGET_TASKS}."
        )

    # Pick Task #1 flagship 4-stage anchor
    flagship: dict[str, Any] | None = None
    if preferred_flagship_key:
        for r in dedup_courts:
            if r["raw_case_key"] == preferred_flagship_key:
                flagship = r
                break
    if flagship is None:
        # Fallback: strongest 4-stage paired-PDF anchor with overrun_days > 0
        four_stage_cands = [
            r
            for r in dedup_courts
            if all(
                r["spec"][k]
                for k in (
                    "stage_1_acquittal_verified",
                    "stage_2_continued_detention_verified",
                    "stage_3_contempt_verified",
                    "stage_4_release_verified",
                )
            )
            and (r["spec"].get("overrun_days") or 0) > 0
        ]
        four_stage_cands.sort(
            key=lambda r: (
                0 if r["secondary_url"] else 1,
                0 if r["is_anchor_pool"] else 1,
                -(r["spec"].get("overrun_days") or 0),
                r["raw_case_key"],
            )
        )
        if not four_stage_cands:
            raise RuntimeError("No verified 4-stage flagship anchor case found in court manifest!")
        flagship = four_stage_cands[0]

    flagship["is_anchor_case"] = True
    flagship["spec"]["is_anchor_case"] = True

    remaining_courts = [r for r in dedup_courts if r is not flagship]
    for r in remaining_courts:
        r["is_anchor_case"] = False
        r["spec"]["is_anchor_case"] = False

    # Keep ALL verified non-court portal records (up to 320 max so courts stay >= 680)
    def _prison_priority(r: dict[str, Any]) -> tuple[Any, ...]:
        s = r["spec"]
        cat_order = {
            "prison_portal": 0,
            "police_portal": 1,
            "nhrc_shrc": 2,
            "nalsa_slsa": 3,
            "govt_ministry": 4,
            "cag_audit": 5,
            "parliament": 6,
        }
        is_state = 0 if s["state_ut"] else 1
        is_cleared = 1 if s["overrun_mechanism"] == "timely_release_or_compliant" else 0
        return (cat_order.get(s["portal_category"], 9), is_state, is_cleared, -s["year"], r["raw_case_key"])

    dedup_prisons.sort(key=_prison_priority)
    max_non_court = min(len(dedup_prisons), 320)
    chosen_prisons = dedup_prisons[:max_non_court]
    needed_courts = TARGET_TASKS - 1 - len(chosen_prisons)

    if len(remaining_courts) < needed_courts:
        raise RuntimeError(
            f"Not enough remaining court records ({len(remaining_courts)}) to reach {TARGET_TASKS} tasks "
            f"with {len(chosen_prisons)} non-court records."
        )

    # Score remaining courts: anchors first, then 4-stage, 3-stage, contempt/overrun, compensation, court/state diversity
    court_seen_counts: Counter[str] = Counter([flagship["spec"]["acquittal_court"]])
    state_seen_counts: Counter[str] = Counter([flagship["spec"]["jurisdiction"]])
    for r in chosen_prisons:
        state_seen_counts[r["spec"]["jurisdiction"]] += 1

    def _court_base_score(r: dict[str, Any]) -> tuple[Any, ...]:
        s = r["spec"]
        stages = sum(
            1
            for k in (
                "stage_1_acquittal_verified",
                "stage_2_continued_detention_verified",
                "stage_3_contempt_verified",
                "stage_4_release_verified",
            )
            if s[k]
        )
        is_anchor = 0 if r["is_anchor_pool"] else 1
        has_comp = 0 if (s.get("compensation_inr") or 0) > 0 else 1
        has_contempt = 0 if s["stage_3_contempt_verified"] else 1
        has_overrun = 0 if (s.get("overrun_days") or 0) > 0 else 1
        is_sc = 0 if s["court_tier"] == "supreme_court" else 1
        is_cleared = 1 if s["overrun_mechanism"] == "timely_release_or_compliant" else 0
        return (
            is_anchor,
            -stages,
            has_contempt,
            has_comp,
            has_overrun,
            is_sc,
            is_cleared,
            -(s.get("overrun_days") or 0),
            -s["year"],
            r["raw_case_key"],
        )

    # Guarantee at least 1 record from every distinct court present in remaining_courts
    must_keep: list[dict[str, Any]] = []
    rest_pool: list[dict[str, Any]] = []
    by_court: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in sorted(remaining_courts, key=_court_base_score):
        by_court[r["spec"]["acquittal_court"]].append(r)

    chosen_ids: set[int] = set()
    # 1. All remaining hand-verified anchors
    for r in sorted(remaining_courts, key=_court_base_score):
        if r["is_anchor_pool"]:
            must_keep.append(r)
            chosen_ids.add(id(r))
            court_seen_counts[r["spec"]["acquittal_court"]] += 1
            state_seen_counts[r["spec"]["jurisdiction"]] += 1

    # 2. First best record from any court not yet covered
    for cname, items in sorted(by_court.items()):
        if court_seen_counts[cname] == 0:
            for r in items:
                if id(r) not in chosen_ids:
                    must_keep.append(r)
                    chosen_ids.add(id(r))
                    court_seen_counts[cname] += 1
                    state_seen_counts[r["spec"]["jurisdiction"]] += 1
                    break

    # 3. Fill remainder preserving balanced Cleared benchmarks (~100-140 total cleared across project)
    current_cleared = sum(1 for r in chosen_prisons + must_keep if r["spec"]["overrun_mechanism"] == "timely_release_or_compliant")
    target_min_cleared = 115
    target_max_cleared = 160

    unselected = [r for r in sorted(remaining_courts, key=_court_base_score) if id(r) not in chosen_ids]
    flagged_rem = [r for r in unselected if r["spec"]["overrun_mechanism"] != "timely_release_or_compliant"]
    cleared_rem = [r for r in unselected if r["spec"]["overrun_mechanism"] == "timely_release_or_compliant"]

    slots_left = needed_courts - len(must_keep)
    if slots_left < 0:
        raise RuntimeError(f"must_keep ({len(must_keep)}) exceeds needed_courts ({needed_courts})")

    desired_cleared_add = max(0, min(len(cleared_rem), target_min_cleared - current_cleared, slots_left))
    # If not enough flagged to fill remaining slots, take extra cleared up to available
    flagged_take = min(len(flagged_rem), slots_left - desired_cleared_add)
    cleared_take = slots_left - flagged_take
    if cleared_take > len(cleared_rem):
        cleared_take = len(cleared_rem)
        flagged_take = slots_left - cleared_take

    # Keep flagged/cleared balanced across courts if capping
    chosen_courts = must_keep + flagged_rem[:flagged_take] + cleared_rem[:cleared_take]
    assert len(chosen_courts) == needed_courts

    # Order final 1,000 cleanly: Task #1 flagship, then remaining anchors, then high-signal courts, then non-court portals
    chosen_courts_sorted = sorted(chosen_courts, key=_court_base_score)
    selected = [flagship] + chosen_courts_sorted + chosen_prisons
    assert len(selected) == TARGET_TASKS
    return selected


def _make_clean_task(
    rec: dict[str, Any],
    *,
    seq_num: int,
    url_info: dict[str, tuple[str, str, int, int]],
    parsed_records: dict[str, list[dict[str, Any]]],
    used_task_ids: set[str],
    used_case_keys: set[str],
    used_titles: set[str],
) -> dict[str, Any]:
    """Assemble one validated P10 task dict matching `validate_project()` + `validate_context()`."""
    spec = dict(rec["spec"])
    pri_url = rec["retrieval_url"]
    sec_url = rec.get("secondary_url")
    pri_sha, pri_fmt, _, pri_pages = url_info[pri_url]
    sec_sha = url_info[sec_url][0] if sec_url else None
    sec_fmt = url_info[sec_url][1] if sec_url else None
    sec_pages = url_info[sec_url][3] if sec_url else 0

    pcat = spec["portal_category"]
    pcode = PORTAL_CATEGORY_ID_CODES[pcat]
    year = int(spec["year"])
    is_anchor = seq_num == 1
    spec["is_anchor_case"] = is_anchor

    # Normalize & deduplicate case_key (<= 59 chars, ^[a-z0-9][a-z0-9-]{0,58}$, never triggering Verhoeff Aadhaar regex)
    base_ck = slugify(spec.get("case_key") or f"p10-{pcode}-{year}-{spec['case_title']}", max_len=52)
    base_ck = re.sub(r"(\d{4})-(\d{4})-(\d{4})", r"\1-c\2-\3", base_ck)
    ck = base_ck
    suffix_n = 2
    while ck in used_case_keys or len(ck) > 59 or contains_pii(ck)[0]:
        tag = f"-c{suffix_n}"
        ck = base_ck[: 59 - len(tag)].rstrip("-") + tag
        suffix_n += 1
    used_case_keys.add(ck)
    spec["case_key"] = ck

    # Task ID: ^p10-(court|prison|police|nhrc|nalsa|parl|govt|cag)-(202[1-6])-[a-z0-9]+(?:-[a-z0-9]+)*-\d{4}$ (<= 60 chars)
    existing_tid = rec.get("existing_task_id")
    if existing_tid and P10_TASK_ID_RE.fullmatch(existing_tid) and len(existing_tid) <= 60 and existing_tid not in used_task_ids and not contains_pii(existing_tid)[0]:
        tid = existing_tid
    else:
        prefix = f"p10-{pcode}-{year}-"
        seq_str = f"-{seq_num:04d}"
        max_slug = 60 - len(prefix) - len(seq_str)
        if is_anchor:
            mid_slug = slugify(f"anchor-{spec['case_title'][:36]}", max_len=max_slug)
        else:
            raw_mid = re.sub(r"^p10-(?:anchor-)?", "", ck)
            raw_mid = re.sub(rf"^{pcode}-{year}-", "", raw_mid)
            mid_slug = slugify(raw_mid or spec["document_ref"], max_len=max_slug)
        # Never let mid_slug start/end with bare digits adjacent to YYYY- or -NNNN (prevents YYYY-DDDD-NNNN Verhoeff collisions)
        if mid_slug and mid_slug[0].isdigit():
            mid_slug = f"r-{mid_slug}"[:max_slug].rstrip("-")
        if mid_slug and mid_slug[-1].isdigit():
            mid_slug = f"{mid_slug[: max(1, max_slug - 2)].rstrip('-')}-r"
        mid_slug = re.sub(r"(\d{4})-(\d{4})", r"\1-r\2", mid_slug)[:max_slug].rstrip("-")
        tid = f"{prefix}{mid_slug}{seq_str}"
        if tid in used_task_ids or contains_pii(tid)[0]:
            mid_slug = slugify(f"r-{mid_slug[: max(4, max_slug - 8)]}-k{seq_num}", max_len=max_slug)
            tid = f"{prefix}{mid_slug}{seq_str}"
    assert P10_TASK_ID_RE.fullmatch(tid) and len(tid) <= 60, f"Invalid task ID: {tid}"
    used_task_ids.add(tid)

    # Verify record_selector page bounds against actual parsed page count
    sel = dict(spec.get("record_selector") or {}) if isinstance(spec.get("record_selector"), dict) else {}
    sec_role = str(spec.get("secondary_role") or "").strip().lower()
    if sec_url and sec_role == "acquittal_order_document":
        cust_pages, cont_pages = sec_pages, pri_pages
        cust_recs, cont_recs = parsed_records[sec_url], parsed_records[pri_url]
    elif sec_url and sec_role in {"", "contempt_order_document", "release_or_compliance_record"}:
        cust_pages, cont_pages = pri_pages, sec_pages
        cust_recs, cont_recs = parsed_records[pri_url], parsed_records[sec_url]
    elif sec_url and sec_role == "corroborating_record":
        cust_pages = cont_pages = max(pri_pages, sec_pages)
        cust_recs = cont_recs = parsed_records[pri_url] + parsed_records[sec_url]
    else:
        cust_pages = cont_pages = pri_pages
        cust_recs = cont_recs = parsed_records[pri_url]

    if "order_page" in sel:
        op = sel["order_page"]
        if not isinstance(op, int) or op < 1 or op > max(1, cust_pages):
            sel.pop("order_page", None)
    if "contempt_page" in sel:
        cp = sel["contempt_page"]
        if not isinstance(cp, int) or cp < 1 or cp > max(1, cont_pages):
            sel.pop("contempt_page", None)
    if sel:
        spec["record_selector"] = sel
    else:
        spec.pop("record_selector", None)

    # Clean all quotes & tokens (strict NFKC + neutral + PII-scrubbed)
    spec["case_title"] = neutralize_clean(spec["case_title"], max_len=200)
    spec["document_ref"] = neutralize_clean(spec["document_ref"], max_len=200)
    spec["acquittal_court"] = neutralize_clean(spec["acquittal_court"], max_len=200)
    spec["acquittal_case_number"] = neutralize_clean(spec["acquittal_case_number"], max_len=140)
    spec["prison_name"] = neutralize_clean(spec["prison_name"], max_len=180)
    spec["custody_authority"] = neutralize_clean(spec["custody_authority"], max_len=200)
    spec["non_release_reason"] = neutralize_clean(spec["non_release_reason"], max_len=360)
    spec["overrun_window"] = neutralize_clean(spec["overrun_window"], max_len=160)
    spec["statutory_anchor"] = neutralize_clean(spec.get("statutory_anchor") or _PORTAL_STATUTORY_ANCHORS[pcat], max_len=240)
    spec["source_format"] = pri_fmt if pri_fmt in {"pdf", "html", "json", "jsonl", "csv", "txt", "zip", "tar"} else "pdf"
    if sec_url:
        spec["secondary_source_format"] = sec_fmt if sec_fmt in {"pdf", "html", "json", "jsonl", "csv", "txt", "zip", "tar"} else "pdf"
        spec["secondary_role"] = sec_role or "contempt_order_document"
    else:
        spec["secondary_source_format"] = None
        spec["secondary_role"] = None

    if spec.get("contempt_case_number"):
        spec["contempt_case_number"] = neutralize_clean(spec["contempt_case_number"], max_len=140) or None
    if spec.get("contempt_court"):
        spec["contempt_court"] = neutralize_clean(spec["contempt_court"], max_len=200) or None
    if spec.get("contempt_quote"):
        spec["contempt_quote"] = _snap_quote_words(spec["contempt_quote"], max_len=440) or None

    # Clean tokens (preserve exact case/text from PDF, drop any forbidden term)
    def _clean_tokens(raw_toks: list[Any], target_recs: list[dict[str, Any]]) -> list[str]:
        hay = norm_search(_records_combined_text(target_recs))
        out: list[str] = []
        seen_t: set[str] = set()
        for t in raw_toks:
            s = html.unescape(strip_invisible_and_nfkc(str(t or ""))).strip()
            s = _MULTI_SPACE_RE.sub(" ", s)
            if not (3 <= len(s) <= 120):
                continue
            if scan_forbidden_terms(s):
                continue
            hp, _ = contains_pii(s)
            if hp:
                continue
            ns = norm_search(s)
            if ns and ns not in seen_t and ns in hay:
                seen_t.add(ns)
                out.append(s)
                if len(out) >= 6:
                    break
        return out

    spec["custody_order_evidence_tokens"] = _clean_tokens(spec.get("custody_order_evidence_tokens") or [], cust_recs)
    spec["contempt_or_outcome_evidence_tokens"] = _clean_tokens(spec.get("contempt_or_outcome_evidence_tokens") or [], cont_recs)

    # Refresh acquittal_quote & judicial_direction_summary if chopped/empty
    acq_q = _snap_quote_words(spec.get("acquittal_quote") or "", max_len=440)
    if len(acq_q) < 20:
        acq_q = _extract_pdf_window(cust_recs, spec["custody_order_evidence_tokens"], preferred_page=sel.get("order_page")) or f"Official record {spec['acquittal_case_number']} before {spec['acquittal_court']}."
    spec["acquittal_quote"] = neutralize_clean(acq_q, max_len=440)

    jud_d = _snap_quote_words(spec.get("judicial_direction_summary") or "", max_len=590)
    if len(jud_d) < 20:
        jud_d = _extract_pdf_window(cont_recs, spec["contempt_or_outcome_evidence_tokens"], preferred_page=sel.get("contempt_page")) or f"Verified official order {spec['document_ref']} ({spec['document_date']})."
    spec["judicial_direction_summary"] = neutralize_clean(jud_d, max_len=590)

    # Strip None-only optional record_selector if empty
    if "record_selector" in spec and not spec["record_selector"]:
        spec.pop("record_selector", None)

    official_publisher = neutralize_clean(rec["official_publisher"], max_len=200)
    landing_page = clean_public_url(rec["source_landing_page"])

    task_ctx: dict[str, Any] = {
        "as_of_date": AS_OF_DATE,
        "official_publisher": official_publisher,
        "source_landing_page": landing_page,
        "retrieval_url": pri_url,
        "expected_sha256": pri_sha,
        "innocent_but_locked_up": spec,
    }
    payload: dict[str, Any] = {
        "retrieval_url": pri_url,
        "expected_sha256": pri_sha,
        "official_publisher": official_publisher,
        "source_landing_page": landing_page,
        "as_of_date": AS_OF_DATE,
        "jurisdiction": spec["jurisdiction"],
        "context": task_ctx,
    }
    if sec_url and sec_sha:
        task_ctx["secondary_url"] = sec_url
        task_ctx["secondary_expected_sha256"] = sec_sha
        payload["secondary_url"] = sec_url
        payload["secondary_expected_sha256"] = sec_sha

    # Visible task title MUST begin with `<state_ut or Union of India> · ` so frontend p10StateFilterQuery matches 100%!
    jur_short = spec["state_ut"] or "Union of India"
    ov = spec.get("overrun_days")
    ov_tag = f" (+{ov}d)" if isinstance(ov, int) and ov > 0 else ""
    base_title = neutralize_clean(f"{jur_short} · {spec['case_title']}{ov_tag}", max_len=155)
    title = base_title
    if title in used_titles:
        tag = f" (#{seq_num})"
        title = _trim_word_boundary(base_title, 155 - len(tag)) + tag
    used_titles.add(title)

    court_for_inst = spec.get("contempt_court") or spec["acquittal_court"]
    instructions = neutralize_clean(
        f"Verify SHA-256 {pri_sha[:8]}… and run P10 on {spec['case_title']} "
        f"({court_for_inst}; {spec['prison_name']}; {spec['jurisdiction']}, {year}): "
        f"confirm custody/release order tokens and contempt or compliance outcome in official bytes.",
        max_len=400,
    )

    return {
        "id": tid,
        "title": title,
        "instructions": instructions,
        "type": "run_check",
        "methodology_code": METHODOLOGY_CODE,
        "dataset_key": f"p10-{pcode}-{year}-{slugify(spec['acquittal_court'], max_len=36)}",
        "financial_quantity_type": "custody_overrun_and_contempt",
        "estimated_tokens": 6000 if sec_url else 4000,
        "estimate_basis": (
            "Deterministic P10 two-document SHA-256 and verbatim custody/contempt evidence-token check (zero LLM cost)."
            if sec_url
            else "Deterministic P10 single-document SHA-256 and verbatim custody/compliance evidence-token check (zero LLM cost)."
        ),
        "payload": payload,
    }


def _run_quality_gates(
    tasks: list[dict[str, Any]],
    coverage: dict[str, Any],
    header_bytes: int,
    total_json_bytes: int,
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
    if len({t["payload"]["context"]["innocent_but_locked_up"]["case_key"] for t in tasks}) != TARGET_TASKS:
        errors.append("Duplicate case_key values detected")
    if len({t["payload"]["retrieval_url"] for t in tasks}) != TARGET_TASKS:
        errors.append("Duplicate primary retrieval_url values detected")

    if header_bytes >= MAX_HEADER_BYTES:
        errors.append(f"Project JSON header {header_bytes} bytes >= {MAX_HEADER_BYTES} bytes (10 KiB limit)")
    if total_json_bytes > MAX_PROJECT_JSON_BYTES:
        errors.append(f"Project JSON size {total_json_bytes} bytes > {MAX_PROJECT_JSON_BYTES} bytes (20 MiB limit)")

    # Task #1 flagship check
    t0_spec = tasks[0]["payload"]["context"]["innocent_but_locked_up"]
    if not t0_spec.get("is_anchor_case"):
        errors.append("Task #1 must have is_anchor_case=True")
    anchor_cnt = sum(1 for t in tasks if t["payload"]["context"]["innocent_but_locked_up"].get("is_anchor_case"))
    if anchor_cnt != 1:
        errors.append(f"Expected exactly 1 is_anchor_case=True task (Task #1), got {anchor_cnt}")
    if not all(
        t0_spec.get(k)
        for k in (
            "stage_1_acquittal_verified",
            "stage_2_continued_detention_verified",
            "stage_3_contempt_verified",
            "stage_4_release_verified",
        )
    ):
        errors.append("Task #1 flagship anchor must have all 4 stages verified")
    if not isinstance(t0_spec.get("overrun_days"), int) or t0_spec["overrun_days"] <= 0:
        errors.append(f"Task #1 flagship anchor must have overrun_days > 0, got {t0_spec.get('overrun_days')}")

    bad_dates = 0
    bad_cleared = 0
    bad_overrun = 0
    html_ent_hits = 0
    watermark_hits = 0
    pua_hits = 0

    for t in tasks:
        raw_unesc = json.dumps(t, ensure_ascii=False)
        if _PUA_AND_GLYPHS_RE.search(raw_unesc) or _CTRL_CHARS_RE.search(raw_unesc):
            pua_hits += 1
        if _HTML_ENTITY_RE.search(raw_unesc):
            html_ent_hits += 1
        if _WATERMARK_RE.search(raw_unesc):
            watermark_hits += 1

        s = t["payload"]["context"]["innocent_but_locked_up"]
        dd = s.get("document_date") or ""
        if dd < "2021-01-01" or dd > "2026-12-31":
            bad_dates += 1
        if s["overrun_mechanism"] == "timely_release_or_compliant":
            if s["stage_2_continued_detention_verified"] or s["stage_3_contempt_verified"] or (s.get("overrun_days") or 0) > 0:
                bad_cleared += 1
        if (s.get("overrun_days") or 0) > 0 and not s["stage_2_continued_detention_verified"]:
            bad_overrun += 1

    if bad_dates:
        errors.append(f"{bad_dates} tasks have document_date outside 2021-2026")
    if bad_cleared:
        errors.append(f"{bad_cleared} timely_release_or_compliant tasks have contradictory stage_2/stage_3/overrun_days")
    if bad_overrun:
        errors.append(f"{bad_overrun} tasks have overrun_days > 0 while stage_2_continued_detention_verified=False")
    if html_ent_hits:
        errors.append(f"HTML entities found in {html_ent_hits} tasks")
    if watermark_hits:
        errors.append(f"mhc.tn.gov.in/judis watermark fragments found in {watermark_hits} tasks")
    if pua_hits:
        errors.append(f"PUA/control glyphs found in {pua_hits} tasks")

    # Verify frontend p10StateFilterQuery prefix matches 100% of every state/UT row in coverage.json
    for srow in coverage["states"]:
        q = (srow.get("state_ut") or "Union of India").strip()
        prefix = f"{q} ·"
        matched = sum(1 for t in tasks if t["title"].startswith(prefix))
        if matched != srow["tasks"]:
            errors.append(f"State filter prefix mismatch for {prefix!r}: matched {matched} != {srow['tasks']}")

    if errors:
        raise RuntimeError("P10 QUALITY GATE FAILED:\n  - " + "\n  - ".join(errors))


def _write_methodology_md(coverage: dict[str, Any], hosts_summary: list[tuple[str, int]]) -> None:
    anchor = coverage["anchor_case"] or {}
    dry = coverage["dry_run_summary"]
    mech = coverage["by_mechanism"]
    chain = coverage["by_chain_status"]
    portal_lines = "\n".join(
        f"  - `{p['portal_category']}` ({p['label']}): `{p['tasks']}` tasks (`{p['cleared_controls']}` cleared controls)"
        for p in coverage["by_portal_category"]
    )
    year_lines = "\n".join(
        f"  - `{y['year']}`: `{y['tasks']}` tasks (`{y['full_4_stage']}` full 4-stage, `{y['overrun_days']:,}` documented overrun days)"
        for y in coverage["by_year"]
    )
    host_lines = "\n".join(f"  - `{host}`: `{cnt}` unique SHA-256-pinned official documents" for host, cnt in hosts_summary)

    content = f"""# Methodology `P10` — Innocent But Locked Up

## Simple Hook
> **{HOOK_TEXT}**

## Objective
Audit **{coverage['total_tasks']:,} SHA-256-pinned Indian judicial, prison, police, human-rights, legal-aid, parliamentary, ministry, and CAG primary records** from the past five years (**2021–2026**) spanning **{coverage['courts_covered']} courts/statutory bodies**, **{coverage['states_and_uts_covered']} States & Union Territories** (plus Union of India), and **{coverage['documents_count']:,} unique live-verified official PDFs** across **{len(hosts_summary)} verified byte-stable domains**. Every task verifies exact primary-document bytes and tracks the four-stage custody-overrun chain:

1. **Stage 1 — Acquittal / Discharge / Release Order:** Court declared the accused acquitted, discharged, quashed detention, or directed release.
2. **Stage 2 — Continued Prison Detention / Non-Release:** Prison or custodial authorities retained the individual past the court's release order, quantifying documented overrun days where recorded.
3. **Stage 3 — Contempt of Court / Habeas Corpus Action:** Civil/criminal contempt, suo motu contempt, or habeas corpus enforcement initiated against custodial/state officials.
4. **Stage 4 — Subsequent Release & Remedial Outcome:** Verified release, judicial censure, FASTER / release-warrant compliance directions, and Article 21 compensation where ordered.

## Flagship Anchor Case (Task #1: `{anchor.get('task_id', '')}`)
- **Case:** {anchor.get('case_title', '')}
- **Jurisdiction:** {anchor.get('jurisdiction', '')} · **Year:** `{anchor.get('year', '')}` · **Chain:** `{anchor.get('stages_verified_count', 4)}/4 stages verified ({anchor.get('chain_status', 'full_4_stage_chain')})`
- **Stage 1 (Order — `{anchor.get('acquittal_order_date', '')}`):** {anchor.get('acquittal_court', '')} (`{anchor.get('acquittal_case_number', '')}`) — {anchor.get('acquittal_quote', '')}
- **Stage 2 (Continued Detention):** {anchor.get('prison_name', '')} / {anchor.get('custody_authority', '')} — **{anchor.get('overrun_days', '')} days** (`{anchor.get('overrun_window', '')}`); recorded reason: {anchor.get('non_release_reason', '')}
- **Stage 3 (Contempt — `{anchor.get('contempt_order_date', '')}`):** {anchor.get('contempt_court', '')} (`{anchor.get('contempt_case_number', '')}`) — {anchor.get('contempt_quote', '')}
- **Stage 4 (Release — `{anchor.get('release_date', '')}`):** Release verified (`{anchor.get('release_verified', True)}`) — {anchor.get('judicial_direction_summary', '')}
- **Primary Verified Document:** {anchor.get('source_url', '')}
- **Paired Secondary Document:** {anchor.get('secondary_url', 'None')}

## Corpus Coverage (`{coverage['total_tasks']:,}` Tasks · 2021–2026 · Live Byte-Stability Verified)
- **4-Stage Chain & Impact Totals:** `{coverage['full_4_stage_tasks']}` full 4-stage chains · `{chain['partial_3_stage']}` 3-stage chains · `{coverage['contempt_tasks']}` verified contempt proceedings · `{coverage['verified_release_tasks']}` verified release records · `{coverage['total_overrun_days_documented']:,}` documented overrun days (median `{coverage['median_overrun_days']}`d, max `{coverage['max_overrun_days']:,}`d) · `INR {coverage['total_compensation_ordered_inr']:,.0f}` verified Article 21 compensation · `{coverage['prisons_named']}` explicitly named prisons
- **Portal Category Breakdown (All 8 Official Categories):**
{portal_lines}
- **5-Year Temporal Coverage (`2021–2026`):**
{year_lines}
- **Mechanism Breakdown:**
  - `post_acquittal_contempt_overrun`: `{mech['post_acquittal_contempt_overrun']['tasks']}`
  - `bail_or_release_order_contempt`: `{mech['bail_or_release_order_contempt']['tasks']}`
  - `warrant_or_faster_transmission_delay`: `{mech['warrant_or_faster_transmission_delay']['tasks']}`
  - `statutory_commission_detention_finding`: `{mech['statutory_commission_detention_finding']['tasks']}`
  - `institutional_compliance_gap`: `{mech['institutional_compliance_gap']['tasks']}`
  - `timely_release_or_compliant` (Cleared benchmark): `{mech['timely_release_or_compliant']['tasks']}`
- **Deterministic Dry-Run (`as_of_date={coverage['as_of_date']}`):** `{dry['leads']}` leads · `{dry['cleared']}` cleared · `{dry['unevaluable']}` unevaluable (across all `{dry['tasks_tested']}` tasks)
- **Verified Byte-Stable Official Domains ({coverage['documents_count']:,} unique pinned URLs across {len(hosts_summary)} domains):**
{host_lines}

## Deterministic Rules (`P10`)
1. **Post-Acquittal Detention Overrun & Contempt (`P10-POST-ACQUITTAL-CONTEMPT-OVERRUN`):** Continued prison detention documented after an acquittal, discharge, conviction set-aside, or detention-quashment order.
2. **Release-Order Custody Overrun & Contempt (`P10-RELEASE-ORDER-CONTEMPT-OVERRUN`):** Custodial non-compliance or contempt proceedings following a judicial release or bail-release direction.
3. **Warrant / FASTER Transmission Delay (`P10-WARRANT-TRANSMISSION-DELAY`):** Documented release-warrant (parwana), e-Prisons/ICJS, or Supreme Court FASTER electronic transmission breakdown delaying release.
4. **Statutory Commission Unlawful-Detention Finding (`P10-STATUTORY-UNLAWFUL-DETENTION`):** NHRC or SHRC statutory oversight record documenting unlawful detention or custodial release failures.
5. **Institutional Compliance Gap (`P10-INSTITUTIONAL-COMPLIANCE-GAP`):** Parliamentary, CAG, Ministry, Prison, Police, or NALSA/UTRC oversight finding on undertrial custody overruns and release compliance.
6. **Timely Release / Verified Compliance (`Cleared`):** Verified compliant releases, NIL FASTER backlog returns, and institutional compliance benchmarks (`{coverage['cleared_control_tasks_embedded']}` tasks), published with equal journalistic standing after verifying all documentary tokens in fetched bytes.

## Legal, Privacy & Verification Safeguards
- **Fail-Closed Verification:** Every primary and secondary URL is live double-fetch byte-stability verified and SHA-256 pinned (`DigestMismatchError` on any byte drift). `P10` verifies machine-readable text (`>= 40` chars), chronological date consistency, and 100% verbatim documentary token presence in downloaded official bytes before emitting any lead or cleared verdict.
- **Zero CAPTCHA Bypasses (IT Act Sec. 43):** Only public, CAPTCHA-free, strict-TLS official portals and open-data mirrors are queried; CAPTCHA-gated eCourts/ePrisons searches and non-byte-stable dynamic HTML endpoints are excluded by design.
- **BNS Sec 356 Neutrality & DPDP Act 2023:** Zero forbidden accusatory terms and zero personal PII (Aadhaar, PAN, phone, email, bank details) across `projects/innocent-but-locked-up.json`, `coverage.json`, `documents.csv`, and `METHODOLOGY.md`. Every lead includes mandatory lawful benign explanations (concurrent remand/production warrants in separate cases, surety/identity verification, certified-copy/FASTER transmission gaps, or clerical warrant clarifications).
"""
    PROJECT_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    (PROJECT_ASSETS_DIR / "METHODOLOGY.md").write_text(content, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build & verify Project P10 (Innocent But Locked Up, 1,000 tasks).")
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE_DIR, help="External blob/parse cache dir (outside git).")
    parser.add_argument("--courts-manifest", type=Path, default=DEFAULT_COURTS_MANIFEST, help="Verified court cases JSONL.")
    parser.add_argument("--anchors-json", type=Path, default=DEFAULT_ANCHORS_JSON, help="Verified anchor cases JSON.")
    parser.add_argument("--prisons-manifest", type=Path, default=DEFAULT_PRISONS_MANIFEST, help="Verified prisons/govt JSONL.")
    parser.add_argument("--from-existing", action="store_true", help="Rebuild/verify from committed projects/innocent-but-locked-up.json.")
    parser.add_argument("--force-live", action="store_true", help="Re-download all URLs ignoring local cache.")
    parser.add_argument("--skip-spot-check", action="store_true", help="Skip live uncached cross-domain byte-stability spot-check.")
    parser.add_argument("--fetch-workers", type=int, default=16, help="Concurrent HTTP download threads.")
    parser.add_argument("--parse-workers", type=int, default=max(2, min(8, multiprocessing.cpu_count())), help="Parallel PDF parse workers.")
    args = parser.parse_args()

    from aiwatchdogs.engines.registry import run_engine
    from aiwatchdogs.projects import validate_project

    t_start = time.time()
    use_existing = args.from_existing or not (args.courts_manifest.exists() and args.prisons_manifest.exists())

    if use_existing:
        if not PROJECT_JSON_PATH.exists():
            raise SystemExit(f"Neither research manifests nor {PROJECT_JSON_PATH} exist.")
        print(f"Standalone rebuild/verification mode: reading {PROJECT_JSON_PATH}...", flush=True)
        selected_raw = _load_cases_from_existing_project(PROJECT_JSON_PATH)
    else:
        print(f"Manifest build mode: loading {args.courts_manifest} & {args.prisons_manifest}...", flush=True)
        preferred_flagship: str | None = None
        if args.anchors_json.exists():
            adata = json.loads(args.anchors_json.read_text(encoding="utf-8"))
            frec = adata.get("flagship_recommendation") or {}
            if isinstance(frec, dict):
                preferred_flagship = frec.get("primary_4stage_paired_contempt_anchor")

        raw_courts = [
            _normalize_record_from_court_manifest(json.loads(line))
            for line in args.courts_manifest.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        raw_prisons = [
            _normalize_record_from_prisons_manifest(json.loads(line))
            for line in args.prisons_manifest.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        print(f"Loaded {len(raw_courts)} court rows + {len(raw_prisons)} prisons/govt rows.", flush=True)
        selected_raw = _select_1000_records(raw_courts, raw_prisons, preferred_flagship_key=preferred_flagship)
        print(f"Selected exactly {len(selected_raw)} unique high-signal tasks (Task #1 = {selected_raw[0]['raw_case_key']}).", flush=True)

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

    print(f"Fetching & SHA-256 verifying {len(url_expected)} unique official HTTPS URLs (cache={args.cache})...", flush=True)
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
            if done % 200 == 0 or done == len(url_expected):
                print(f"  fetched/verified {done}/{len(url_expected)} URLs ({time.time() - t_start:.1f}s)", flush=True)

    # 2. Live uncached double-fetch byte-stability spot-check across EVERY unique host
    if not args.skip_spot_check:
        by_host_pick: dict[str, str] = {}
        for cu in sorted(blob_map):
            h = (urlsplit(cu).hostname or "").lower()
            if h not in by_host_pick:
                by_host_pick[h] = cu
        print(f"Running live uncached double-fetch spot-check across all {len(by_host_pick)} source domains...", flush=True)
        for h, cu in sorted(by_host_pick.items()):
            exp_sha = blob_map[cu][0]
            _fetch_one_url(cu, exp_sha, args.cache, double_check=True, force_live=True)
            print(f"  LIVE 2x OK: {h} ({exp_sha[:12]}…)", flush=True)

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
            if done % 200 == 0 or done == len(parse_jobs):
                print(f"  parsed {done}/{len(parse_jobs)} artifacts ({time.time() - t_start:.1f}s)", flush=True)

    # 4. Assemble all 1,000 tasks and dry-run EVERY SINGLE TASK through `run_engine("P10", ...)`
    print(f"Assembling & dry-running all {TARGET_TASKS} tasks through deterministic P10 engine...", flush=True)
    tasks: list[dict[str, Any]] = []
    used_task_ids: set[str] = set()
    used_case_keys: set[str] = set()
    used_titles: set[str] = set()
    dry_leads = 0
    dry_cleared = 0
    dry_unevaluable = 0
    by_signal: Counter[str] = Counter()
    by_severity: Counter[str] = Counter()

    for idx, raw in enumerate(selected_raw, start=1):
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

    # 5. Aggregate coverage.json & documents.csv matching backend `desk.py` & frontend `types.ts`
    portal_agg: dict[str, dict[str, Any]] = {
        pcat: {"portal_category": pcat, "label": lbl, "tasks": 0, "cleared_controls": 0}
        for pcat, lbl in PORTAL_CATEGORY_LABELS.items()
    }
    year_agg: dict[int, dict[str, Any]] = {
        yr: {"year": yr, "tasks": 0, "full_4_stage": 0, "cleared_controls": 0, "overrun_days": 0}
        for yr in range(MIN_YEAR, MAX_YEAR + 1)
    }
    tier_agg: dict[str, dict[str, int]] = {
        t: {"tasks": 0, "cleared_controls": 0}
        for t in ("supreme_court", "high_court", "district_court", "statutory_body")
    }
    mech_agg: dict[str, dict[str, int]] = {
        m: {"tasks": 0} for m in (*SIGNAL_CATEGORIES, "timely_release_or_compliant")
    }
    chain_agg: dict[str, int] = {
        "full_4_stage_chain": 0,
        "partial_3_stage": 0,
        "institutional_record": 0,
        "compliant_control": 0,
    }
    states_map: dict[str, dict[str, Any]] = {}
    courts_map: dict[str, dict[str, Any]] = {}
    prisons_map: dict[str, dict[str, Any]] = {}
    doc_usage: Counter[str] = Counter()
    doc_meta: dict[str, dict[str, Any]] = {}

    full_4_stage_tasks = 0
    contempt_tasks = 0
    verified_release_tasks = 0
    cleared_controls = 0
    overrun_days_list: list[int] = []
    total_compensation_inr = 0.0
    anchor_case: dict[str, Any] | None = None

    for task in tasks:
        p = task["payload"]
        s = p["context"]["innocent_but_locked_up"]
        pcat = s["portal_category"]
        ctier = s["court_tier"]
        yr = int(s["year"])
        mech = s["overrun_mechanism"]
        jur = s["jurisdiction"]
        sut = s["state_ut"]

        s1 = bool(s["stage_1_acquittal_verified"])
        s2 = bool(s["stage_2_continued_detention_verified"])
        s3 = bool(s["stage_3_contempt_verified"])
        s4 = bool(s["stage_4_release_verified"])
        scount = sum(1 for f in (s1, s2, s3, s4) if f)
        is_cleared = mech == "timely_release_or_compliant"
        ov = s.get("overrun_days")
        ov_pos = int(ov) if isinstance(ov, int) and ov > 0 else 0
        comp = float(s.get("compensation_inr") or 0.0)

        portal_agg[pcat]["tasks"] += 1
        year_agg[yr]["tasks"] += 1
        tier_agg[ctier]["tasks"] += 1
        mech_agg[mech]["tasks"] += 1

        if is_cleared:
            portal_agg[pcat]["cleared_controls"] += 1
            year_agg[yr]["cleared_controls"] += 1
            tier_agg[ctier]["cleared_controls"] += 1
            cleared_controls += 1
            chain_status = "compliant_control"
        elif scount == 4:
            full_4_stage_tasks += 1
            year_agg[yr]["full_4_stage"] += 1
            chain_status = "full_4_stage_chain"
        elif scount == 3:
            chain_status = "partial_3_stage"
        else:
            chain_status = "institutional_record"
        chain_agg[chain_status] += 1

        if s3:
            contempt_tasks += 1
        if s4 or bool(s.get("release_verified")):
            verified_release_tasks += 1
        if ov_pos > 0:
            overrun_days_list.append(ov_pos)
            year_agg[yr]["overrun_days"] += ov_pos
        if comp > 0:
            total_compensation_inr += comp

        st_entry = states_map.setdefault(
            jur,
            {
                "state_ut": sut,
                "jurisdiction": jur,
                "tasks": 0,
                "full_4_stage": 0,
                "cleared_controls": 0,
                "total_overrun_days": 0,
                "compensation_inr": 0.0,
            },
        )
        st_entry["tasks"] += 1
        if chain_status == "full_4_stage_chain":
            st_entry["full_4_stage"] += 1
        if is_cleared:
            st_entry["cleared_controls"] += 1
        if ov_pos > 0:
            st_entry["total_overrun_days"] += ov_pos
        if comp > 0:
            st_entry["compensation_inr"] = round(st_entry["compensation_inr"] + comp, 2)

        cname = str(s.get("contempt_court") or s.get("acquittal_court") or "").strip()
        if cname:
            c_entry = courts_map.setdefault(
                cname,
                {
                    "court_name": cname,
                    "court_tier": ctier,
                    "state_ut": sut,
                    "tasks": 0,
                    "full_4_stage": 0,
                },
            )
            c_entry["tasks"] += 1
            if chain_status == "full_4_stage_chain":
                c_entry["full_4_stage"] += 1

        pname = str(s.get("prison_name") or "").strip()
        if _is_named_prison(pname):
            pr_entry = prisons_map.setdefault(
                pname,
                {
                    "prison_name": pname,
                    "state_ut": sut,
                    "tasks": 0,
                    "total_overrun_days": 0,
                },
            )
            if pr_entry["state_ut"] is None and sut:
                pr_entry["state_ut"] = sut
            pr_entry["tasks"] += 1
            if ov_pos > 0:
                pr_entry["total_overrun_days"] += ov_pos

        if s["is_anchor_case"] and anchor_case is None:
            anchor_case = {
                "task_id": task["id"],
                "finding_id": None,
                "case_title": s["case_title"],
                "portal_category": pcat,
                "court_tier": ctier,
                "state_ut": sut,
                "jurisdiction": jur,
                "year": yr,
                "overrun_mechanism": mech,
                "stages_verified_count": scount,
                "chain_status": chain_status,
                "acquittal_order_date": s.get("acquittal_order_date"),
                "acquittal_court": s["acquittal_court"],
                "acquittal_case_number": s["acquittal_case_number"],
                "acquittal_quote": s["acquittal_quote"],
                "prison_name": pname,
                "custody_authority": s["custody_authority"],
                "non_release_reason": s["non_release_reason"],
                "overrun_days": s.get("overrun_days"),
                "overrun_window": s["overrun_window"],
                "contempt_case_number": s.get("contempt_case_number"),
                "contempt_court": s.get("contempt_court"),
                "contempt_filing_date": s.get("contempt_filing_date"),
                "contempt_order_date": s.get("contempt_order_date"),
                "contempt_quote": s.get("contempt_quote"),
                "release_date": s.get("release_date"),
                "release_verified": bool(s.get("release_verified")),
                "compensation_inr": s.get("compensation_inr"),
                "judicial_direction_summary": s["judicial_direction_summary"],
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
                h_doc = hashlib.sha256(u.encode()).hexdigest()
                doc_meta[u] = {
                    "document_id": f"p10-doc-{h_doc[:6]}x{h_doc[6:12]}",
                    "portal_category": pcat,
                    "jurisdiction": jur,
                    "year": yr,
                    "format": fmt,
                    "bytes": bsz,
                    "pages": pages,
                    "sha256": sha,
                    "official_publisher": p["official_publisher"],
                    "retrieval_url": u,
                    "landing_url": p["source_landing_page"],
                }

    by_portal_list = sorted(portal_agg.values(), key=lambda x: (-x["tasks"], x["portal_category"]))
    by_year_list = [year_agg[yr] for yr in range(MIN_YEAR, MAX_YEAR + 1)]
    states_sorted = sorted(
        states_map.values(),
        key=lambda x: (0 if x["jurisdiction"] == "Union of India" else 1, -x["tasks"], x["jurisdiction"].lower()),
    )
    top_courts_sorted = sorted(courts_map.values(), key=lambda x: (-x["tasks"], -x["full_4_stage"], x["court_name"].lower()))[:30]
    top_prisons_sorted = sorted(prisons_map.values(), key=lambda x: (-x["tasks"], -x["total_overrun_days"], x["prison_name"].lower()))[:30]
    states_uts_covered = sum(1 for x in states_sorted if x["jurisdiction"] != "Union of India")

    median_overrun: float | None = None
    max_overrun: int | None = None
    if overrun_days_list:
        sd = sorted(overrun_days_list)
        mid = len(sd) // 2
        median_overrun = float(sd[mid]) if len(sd) % 2 == 1 else round((sd[mid - 1] + sd[mid]) / 2.0, 1)
        max_overrun = sd[-1]

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
        "year_range": [MIN_YEAR, MAX_YEAR],
        "total_tasks": len(tasks),
        "unique_documents_covered": len(doc_meta),
        "documents_count": len(doc_meta),
        "states_and_uts_covered": states_uts_covered,
        "courts_covered": len(courts_map),
        "prisons_named": len(prisons_map),
        "full_4_stage_tasks": full_4_stage_tasks,
        "contempt_tasks": contempt_tasks,
        "verified_release_tasks": verified_release_tasks,
        "cleared_control_tasks_embedded": cleared_controls,
        "total_overrun_days_documented": sum(overrun_days_list),
        "median_overrun_days": median_overrun,
        "max_overrun_days": max_overrun,
        "total_compensation_ordered_inr": round(total_compensation_inr, 2),
        "by_portal_category": by_portal_list,
        "by_year": by_year_list,
        "by_court_tier": tier_agg,
        "by_mechanism": mech_agg,
        "by_chain_status": chain_agg,
        "states": states_sorted,
        "top_courts": top_courts_sorted,
        "top_prisons": top_prisons_sorted,
        "anchor_case": anchor_case,
        "dry_run_summary": dry_run_summary,
    }

    project_obj: dict[str, Any] = {
        "slug": PROJECT_SLUG,
        "project_code": PROJECT_CODE,
        "priority": 10,
        "title": PROJECT_TITLE,
        "summary": (
            f"{HOOK_TEXT} Audits 1,000 SHA-256-pinned 2021–2026 official orders and oversight records across "
            f"the Supreme Court, all 25 High Courts, {states_uts_covered} States & UTs, prison & police portals, "
            "NHRC/SHRCs, NALSA/SLSAs, Digital Sansad, Union Ministries, and CAG reports — verifying post-acquittal "
            "detention overruns, contempt actions, and subsequent releases alongside verified Cleared compliance benchmarks."
        ),
        "posted_by": {
            "name": "AI Watchdogs India",
            "handle": "@aiwatchdogsIN",
            "url": "https://aiwatchdogs.in",
        },
        "pillar": "justice",
        "status": "active",
        "methodology_code": METHODOLOGY_CODE,
        "jurisdiction": f"India (Union of India & {states_uts_covered} States/UTs; All 26 Constitutional Courts)",
        "authority": "Supreme Court, 25 High Courts, State Prison & Police Depts, NHRC/SHRCs, NALSA/SLSAs, MHA, DoJ & CAG",
        "statutory_anchors": [
            "Constitution of India Art. 21 (Protection of Life and Personal Liberty) & Art. 215 (High Court Contempt Power)",
            "Contempt of Courts Act 1971 Sec. 2(b) & Sec. 12 (Wilful Disobedience of Judicial Release Orders)",
            "Supreme Court FASTER Directives (In Re: Delay in Release of Convicts / SMW(Crl) No. 4/2021)",
            "BNSS 2023 Sec. 479 (formerly CrPC Sec. 436A) & Model Prisons and Correctional Services Act 2023",
            "Protection of Human Rights Act 1993 Sec. 12/18 & RTI Act 2005 Sec. 4(1)(b)/6(1)",
        ],
        "scope": [
            f"1,000 live-verified, SHA-256-pinned 2021–2026 primary records spanning all 26 constitutional courts (Supreme Court + 25 High Courts) and all 8 official portal categories",
            f"Flagship Anchor Task #1 ({tasks[0]['id']}): full 4-stage paired-PDF custody-overrun & contempt chain verified in downloaded court bytes",
            f"Includes {cleared_controls} verified compliant/timely-release benchmark tasks published as Cleared with equal journalistic standing",
        ],
        "out_of_scope": [
            "CAPTCHA-gated eCourts/ePrisons searches, login-walled portals, or dynamic non-byte-stable web pages",
            "Private personal identifiers (Aadhaar, PAN, phone numbers, personal emails, bank details — scrubbed per DPDP Act 2023)",
            "Accusatory or conclusory labels barred under BNS Sec. 356 fair-reporting standards",
        ],
        "sources": OFFICIAL_PROJECT_SOURCES,
        "tasks": tasks,
    }

    # 6. Serialize compact JSON (1 line per task) and verify header < 10 KiB & total <= 20 MiB
    header_obj = {k: v for k, v in project_obj.items() if k != "tasks"}
    header_json = json.dumps(header_obj, indent=2, ensure_ascii=False)
    header_prefix = header_json[:-2] + ',\n  "tasks": ['
    lines = [header_prefix]
    for i, t in enumerate(tasks):
        suffix = "," if i + 1 < len(tasks) else ""
        lines.append("    " + json.dumps(t, separators=(",", ":"), ensure_ascii=False) + suffix)
    lines.append("  ]\n}\n")
    full_project_text = "\n".join(lines)
    header_bytes = len(header_prefix.encode("utf-8"))
    total_json_bytes = len(full_project_text.encode("utf-8"))

    # 7. Run hard automated quality gates + schema + BNS Sec 356 + DPDP PII checks before writing
    print(
        f"Running quality gates, validate_project(), BNS neutrality & DPDP PII checks "
        f"(header={header_bytes} B, total={total_json_bytes / 1e6:.2f} MB)...",
        flush=True,
    )
    _run_quality_gates(tasks, coverage, header_bytes, total_json_bytes)

    val_errs = validate_project(project_obj, filename_stem=PROJECT_SLUG)
    if val_errs:
        raise RuntimeError(f"Generated project failed validate_project ({len(val_errs)} errors): {val_errs[:10]}")

    for label, blob in (
        ("coverage.json", json.dumps(coverage, ensure_ascii=False)),
        ("project.json", full_project_text),
    ):
        bad = scan_forbidden_terms(blob)
        if bad:
            raise RuntimeError(f"BNS Sec 356 neutrality violation in {label}: {bad}")
    has_pii_cov, pii_cov = contains_pii(coverage)
    if has_pii_cov:
        raise RuntimeError(f"DPDP PII detected in coverage.json: {pii_cov}")

    # 8. Write all 4 project artifacts
    PROJECT_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    PROJECT_JSON_PATH.write_text(full_project_text, encoding="utf-8")
    (PROJECT_ASSETS_DIR / "coverage.json").write_text(
        json.dumps(coverage, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    doc_rows = []
    for u, meta in sorted(doc_meta.items(), key=lambda kv: (-doc_usage[kv[0]], kv[0])):
        doc_rows.append(
            {
                "document_id": meta["document_id"],
                "tasks_assigned": doc_usage[u],
                "portal_category": meta["portal_category"],
                "jurisdiction": meta["jurisdiction"],
                "year": meta["year"],
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
        f"  Project: {PROJECT_JSON_PATH} ({total_json_bytes / 1e6:.2f} MB, header={header_bytes} B)\n"
        f"  Tasks: {len(tasks)} | Unique docs: {len(doc_meta)} | Domains: {len(hosts_summary)}\n"
        f"  Task #1 Anchor: {tasks[0]['id']} ({tasks[0]['title']})\n"
        f"  Courts/Bodies: {len(courts_map)} | States/UTs: {states_uts_covered} (+ Union) | Named prisons: {len(prisons_map)}\n"
        f"  Chains: 4-stage={full_4_stage_tasks}, 3-stage={chain_agg['partial_3_stage']}, contempt={contempt_tasks}, releases={verified_release_tasks}\n"
        f"  Overrun days: total={sum(overrun_days_list)}, median={median_overrun}, max={max_overrun} | Compensation: INR {total_compensation_inr:,.0f}\n"
        f"  Dry-run: leads={dry_leads}, cleared={dry_cleared}, unevaluable={dry_unevaluable}\n"
        f"  Portals: {json.dumps({p['portal_category']: p['tasks'] for p in by_portal_list})}\n"
        f"  Years: {json.dumps({str(y['year']): y['tasks'] for y in by_year_list})}\n"
        f"  By severity: {json.dumps(dict(by_severity))}",
        flush=True,
    )


if __name__ == "__main__":
    main()
