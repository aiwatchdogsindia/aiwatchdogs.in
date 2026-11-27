#!/usr/bin/env python3
"""Build Project P6 (`parliaments-to-do-list`, methodology `P6`, 1,000 tasks) from Digital Sansad.

Downloads and verifies 1,000 formal ministerial assurances across all active Union Ministries
from Parliament of India's Digital Sansad open APIs (`sansad.in/api_ls/govtAssurance/...` for
the 18th Lok Sabha and `sansad.in/api_rs/govtAssurance/...` for Rajya Sabha Sessions 260-271),
plus pinned official Question/Answer PDFs (Lok Sabha) and Implementation Report PDFs (Rajya Sabha),
anchored by the pending Jhunjhunu-to-Fatehpur assurance (`SQ-18/07/544`) as Task #1.

Every primary JSON and secondary PDF is SHA-256 pinned, verified readable, dry-run through the
deterministic `P6` rule engine (`run_engine("P6", ...)`), validated with `validate_project()`,
and written to:
  - projects/parliaments-to-do-list.json
  - projects/parliaments-to-do-list/coverage.json
  - projects/parliaments-to-do-list/documents.csv
  - projects/parliaments-to-do-list/METHODOLOGY.md
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pypdf import PdfReader

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.append(str(REPO_ROOT / "src"))

from aiwatchdogs.connectors.loader import USER_AGENT, load_dataset  # noqa: E402
from aiwatchdogs.core.safety import (  # noqa: E402
    contains_pii,
    sanitize_csv_cell,
    scan_forbidden_terms,
    validate_safe_http_url,
)

PROJECT_SLUG = "parliaments-to-do-list"
PROJECT_CODE = "P6"
METHODOLOGY_CODE = "P6"
TARGET_TASKS = 1000
AS_OF_DATE = "2026-11-27"
MAX_SECONDARY_PDF_BYTES = 15 * 1024 * 1024  # 15 MiB ceiling per secondary PDF for contributor nodes

DEFAULT_CACHE_DIR = Path.home() / ".cache" / "aiwatchdogs" / PROJECT_SLUG
PROJECTS_DIR = REPO_ROOT / "projects"
PROJECT_JSON_PATH = PROJECTS_DIR / f"{PROJECT_SLUG}.json"
PROJECT_ASSETS_DIR = PROJECTS_DIR / PROJECT_SLUG

LS_INDEX_URL = (
    "https://sansad.in/api_ls/govtAssurance/assurances"
    "?loksabha=18&session=&subject=&member=&minCode=&qType=&status=&catg="
    "&dateFrom=&dateTo=&page=1&size=2000&locale=en"
)
RS_INDEX_URL = (
    "https://sansad.in/api_rs/govtAssurance/assurances"
    "?subject=&member=&ministry=&qType=&status=&catg="
    "&sessionFrom=260&sessionTo=271&dateFrom=&dateTo=&page=1&size=2000&locale=en"
)
LS_PORTAL_URL = "https://sansad.in/ls/business/government-assurances"
RS_PORTAL_URL = "https://sansad.in/rs/house-business/government-assurances"
OAMS_PORTAL_URL = "https://oams.nic.in/"

LS_PUBLISHER = "Parliament of India — Digital Sansad (Lok Sabha Secretariat)"
RS_PUBLISHER = "Parliament of India — Digital Sansad (Rajya Sabha Secretariat)"

ANCHOR_ASSURANCE_REF = "SQ-18/07/544"
ANCHOR_TASK_ID = "p6-ls-18-07-sq-544"

HOOK_TEXT = "A minister promised Parliament an answer. Did it ever get one?"

# Canonical Union Ministry display names (normalizes LS/RS uppercase, '&' vs 'AND', and aliases).
CANONICAL_MINISTRIES: dict[str, str] = {
    "AGRICULTURE AND FARMERS WELFARE": "Agriculture and Farmers Welfare",
    "ATOMIC ENERGY": "Atomic Energy",
    "AYURVEDA, YOGA AND NATUROPATHY, UNANI, SIDDHA": "AYUSH",
    "AYUSH": "AYUSH",
    "CHEMICALS AND FERTILIZERS": "Chemicals and Fertilizers",
    "CIVIL AVIATION": "Civil Aviation",
    "COAL": "Coal",
    "COMMERCE AND INDUSTRY": "Commerce and Industry",
    "COMMUNICATIONS": "Communications",
    "CONSUMER AFFAIRS, FOOD AND PUBLIC DISTRIBUTION": "Consumer Affairs, Food and Public Distribution",
    "COOPERATION": "Cooperation",
    "CORPORATE AFFAIRS": "Corporate Affairs",
    "CULTURE": "Culture",
    "DEFENCE": "Defence",
    "DEVELOPMENT OF NORTH EASTERN REGION": "Development of North Eastern Region",
    "EARTH SCIENCE": "Earth Sciences",
    "EARTH SCIENCES": "Earth Sciences",
    "EDUCATION": "Education",
    "ELECTRONICS AND INFORMATION TECHNOLOGY": "Electronics and Information Technology",
    "ENVIRONMENT, FORESTS AND CLIMATE CHANGE": "Environment, Forest and Climate Change",
    "ENVIRONMENT, FOREST AND CLIMATE CHANGE": "Environment, Forest and Climate Change",
    "EXTERNAL AFFAIRS": "External Affairs",
    "FINANCE": "Finance",
    "FISHERIES, ANIMAL HUSBANDRY AND DAIRYING": "Fisheries, Animal Husbandry and Dairying",
    "FOOD PROCESSING INDUSTRIES": "Food Processing Industries",
    "HEALTH AND FAMILY WELFARE": "Health and Family Welfare",
    "HEAVY INDUSTRIES": "Heavy Industries",
    "HOME AFFAIRS": "Home Affairs",
    "HOUSING AND URBAN AFFAIRS": "Housing and Urban Affairs",
    "INFORMATION AND BROADCASTING": "Information and Broadcasting",
    "JAL SHAKTI": "Jal Shakti",
    "LABOUR AND EMPLOYMENT": "Labour and Employment",
    "LAW AND JUSTICE": "Law and Justice",
    "MICRO SMALL AND MEDIUM ENTERPRISES": "Micro, Small and Medium Enterprises",
    "MICRO, SMALL AND MEDIUM ENTERPRISES": "Micro, Small and Medium Enterprises",
    "MINES": "Mines",
    "MINORITY AFFAIRS": "Minority Affairs",
    "NEW AND RENEWABLE ENERGY": "New and Renewable Energy",
    "NITI AAYOG": "NITI Aayog",
    "PANCHAYATI RAJ": "Panchayati Raj",
    "PARLIAMENTARY AFFAIRS": "Parliamentary Affairs",
    "PERSONNEL, PUBLIC GRIEVANCES AND PENSIONS": "Personnel, Public Grievances and Pensions",
    "PETROLEUM AND NATURAL GAS": "Petroleum and Natural Gas",
    "PLANNING": "Planning",
    "PORTS, SHIPPING AND WATERWAYS": "Ports, Shipping and Waterways",
    "POWER": "Power",
    "RAILWAYS": "Railways",
    "ROAD TRANSPORT AND HIGHWAYS": "Road Transport and Highways",
    "RURAL DEVELOPMENT": "Rural Development",
    "SCIENCE AND TECHNOLOGY": "Science and Technology",
    "SKILL DEVELOPMENT AND ENTREPRENEURSHIP": "Skill Development and Entrepreneurship",
    "SOCIAL JUSTICE AND EMPOWERMENT": "Social Justice and Empowerment",
    "SPACE": "Space",
    "STATISTICS AND PROGRAMME IMPLEMENTATION": "Statistics and Programme Implementation",
    "STEEL": "Steel",
    "TEXTILES": "Textiles",
    "TOURISM": "Tourism",
    "TRIBAL AFFAIRS": "Tribal Affairs",
    "WOMEN AND CHILD DEVELOPMENT": "Women and Child Development",
    "YOUTH AFFAIRS AND SPORTS": "Youth Affairs and Sports",
}

_STATUS_LABELS: dict[str, str] = {
    "pending": "Pending",
    "fully_implemented": "Fully Implemented",
    "partly_implemented": "Partly Implemented",
    "dropped": "Dropped",
}


def canonical_ministry(raw: str) -> tuple[str, str]:
    """Return ``(display_name, ministry_key)`` for any raw Digital Sansad ministry label."""
    norm = " ".join(str(raw or "").upper().replace("&", "AND").split()).strip()
    norm = re.sub(r"\s+,", ",", norm)
    display = CANONICAL_MINISTRIES.get(norm)
    if not display:
        # Clean Title Case fallback preserving short connectors.
        words = norm.title().split()
        lower_words = {"And", "Of", "For", "In", "The"}
        display = " ".join(w.lower() if (i > 0 and w in lower_words) else w for i, w in enumerate(words))
    key = re.sub(r"[^a-z0-9]+", "-", display.lower()).strip("-")
    return display, key


def normalize_status(raw: str) -> str:
    s = " ".join(str(raw or "").strip().upper().split())
    if s in {"PENDING", "NOT DROPPED"}:
        return "pending"
    if s in {"FULLY IMPLEMENTED", "FIMP", "IMPLEMENTED", "FULFILLED"}:
        return "fully_implemented"
    if s in {"PARTLY IMPLEMENTED", "PARTIALLY IMPLEMENTED", "PIMP"}:
        return "partly_implemented"
    if s in {"DROPPED", "DRP"}:
        return "dropped"
    return "pending"


def parse_promise_date_iso(raw: str | None) -> str | None:
    text = str(raw or "").strip()
    if not text:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def safe_url(url: str) -> str:
    """Percent-encode path spaces/backslashes cleanly and validate public HTTPS."""
    parts = urllib.parse.urlsplit(url.strip())
    clean_path = urllib.parse.quote(parts.path.replace("\\", "/"), safe="/:@!$&'()*+,;=-._~%")
    rebuilt = urllib.parse.urlunsplit((parts.scheme, parts.netloc, clean_path, parts.query, ""))
    return validate_safe_http_url(rebuilt, require_https=True, allow_private=False)


def http_get_bytes(url: str, *, retries: int = 3, timeout: int = 30) -> bytes:
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"HTTP {resp.status} for {url}")
                return resp.read()
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
            last_err = exc
            if isinstance(exc, urllib.error.HTTPError) and exc.code in {400, 404}:
                raise
            time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"Failed GET {url}: {last_err}")


def clean_subject_for_title(subject: str, max_len: int = 72) -> str:
    text = " ".join(str(subject or "").replace("\r", " ").replace("\n", " ").split()).strip()
    # Fix odd capitalization like "Nh-11" -> "NH-11" without mutating standard words.
    text = re.sub(r"\bNh-(\d+)\b", r"NH-\1", text)
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip(" ,;:-") + "…"


def _is_clean_text_blob(obj: Any) -> bool:
    """Ensure no BNS Sec 356 accusatory terms or personal PII appear in project metadata."""
    if isinstance(obj, str):
        if scan_forbidden_terms(obj):
            return False
        has_pii, _ = contains_pii(obj)
        return not has_pii
    has_pii, _ = contains_pii(obj)
    if has_pii:
        return False
    if isinstance(obj, dict):
        return all(_is_clean_text_blob(v) for v in obj.values() if isinstance(v, (str, dict, list)))
    if isinstance(obj, list):
        return all(_is_clean_text_blob(v) for v in obj if isinstance(v, (str, dict, list)))
    return True


def fetch_master_indexes(cache_dir: Path, *, refresh: bool = False) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    ls_path = cache_dir / "index_ls18.json"
    rs_path = cache_dir / "index_rs260_271.json"

    if refresh or not ls_path.exists():
        print("Fetching 18th Lok Sabha master assurance index from Digital Sansad...")
        ls_raw = http_get_bytes(LS_INDEX_URL, timeout=45)
        ls_path.write_bytes(ls_raw)
    if refresh or not rs_path.exists():
        print("Fetching Rajya Sabha (Sessions 260-271) master assurance index from Digital Sansad...")
        rs_raw = http_get_bytes(RS_INDEX_URL, timeout=45)
        rs_path.write_bytes(rs_raw)

    ls_data = json.loads(ls_path.read_text(encoding="utf-8"))
    rs_data = json.loads(rs_path.read_text(encoding="utf-8"))
    return list(ls_data.get("records") or []), list(rs_data.get("records") or [])


def _pick_ls_primary_row(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """For multi-row LS questions (written answer + oral supplementaries), select the canonical main row."""
    # Prefer English ASCII-heavy main reply (e.g. '(a) to (c) ...' for SQ-18/07/544) over Hindi supplementary.
    def score(r: dict[str, Any]) -> tuple[int, int, int]:
        txt = str(r.get("textOfAssurance") or "").strip()
        ascii_chars = sum(1 for c in txt if ord(c) < 128)
        ratio = int(100 * ascii_chars / max(len(txt), 1))
        starts_part = 1 if re.match(r"^\(?[a-z]\)", txt.lower()) else 0
        has_imp = 1 if str(r.get("impRecordNo") or "").strip() else 0
        return (starts_part, ratio, has_imp)

    return sorted(rows, key=score, reverse=True)[0]


def build_candidate_pool(
    ls_rows: list[dict[str, Any]],
    rs_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build a deduplicated, clean, ministry-balanced ordered candidate list (>1,000 items)."""
    candidates: list[dict[str, Any]] = []

    # 1. Group LS18 records by unique (loksabha, session, qtype, qno)
    ls_grouped: dict[tuple[int, int, str, int], list[dict[str, Any]]] = defaultdict(list)
    for r in ls_rows:
        assu_no = str(r.get("assuNo") or "").strip()
        ls_no = r.get("loksabha")
        sess = r.get("session")
        qtype = str(r.get("qtype") or "").strip().upper()
        qno_raw = str(r.get("qno") or "").strip()
        text = str(r.get("textOfAssurance") or "").strip()
        raw_min = str(r.get("ministry") or "").strip()
        subj = " ".join(str(r.get("subject") or "").split()).strip()
        pdate = parse_promise_date_iso(r.get("date"))
        raw_status = str(r.get("status") or "").strip()
        if not (assu_no and ls_no and sess and qtype in {"SQ", "USQ"} and qno_raw.isdigit() and text and raw_min and subj and pdate and raw_status):
            continue
        ls_grouped[(int(ls_no), int(sess), qtype, int(qno_raw))].append(r)

    for (ls_no, sess, qtype, qno), grp in ls_grouped.items():
        grp_statuses = {normalize_status(str(x.get("status") or "")) for x in grp if str(x.get("status") or "").strip()}
        if len(grp_statuses) != 1:
            continue  # Skip rare mixed-status multi-row questions so every task has one unambiguous recorded status.
        main = _pick_ls_primary_row(grp)
        assu_no = str(main.get("assuNo") or "").strip()
        raw_min = str(main.get("ministry") or "").strip()
        ministry, ministry_key = canonical_ministry(raw_min)
        subj = " ".join(str(main.get("subject") or "").split()).strip()
        member = " ".join(str(main.get("member") or "").split()).strip() or "Member of Parliament"
        pdate = parse_promise_date_iso(main.get("date"))
        assert pdate is not None
        raw_status = str(main.get("status") or "").strip()
        rec_status = normalize_status(raw_status)
        tid = f"p6-ls-{ls_no}-{sess:02d}-{qtype.lower()}-{qno}"
        if len(tid) > 60:
            continue
        meta_check = {"title": subj, "member": member, "ministry": ministry, "assu_no": assu_no}
        if not _is_clean_text_blob(meta_check):
            continue

        retrieval_url = (
            f"https://sansad.in/api_ls/govtAssurance/assurances"
            f"?loksabha={ls_no}&session={sess}&qNo={qno}&qType={qtype}&page=1&size=20&locale=en"
        )
        candidates.append(
            {
                "task_id": tid,
                "assurance_key": tid[3:],  # ls-18-07-sq-544
                "house": "Lok Sabha",
                "house_code": "LS",
                "loksabha": ls_no,
                "session": str(sess),
                "assurance_ref": assu_no,
                "oassu_no": None,
                "question_type": qtype,
                "question_no": str(qno),
                "promise_date": pdate,
                "ministry": ministry,
                "ministry_key": ministry_key,
                "raw_ministry": raw_min,
                "member": member,
                "subject": subj,
                "recorded_status": rec_status,
                "raw_status": raw_status,
                "imp_record_no": str(main.get("impRecordNo") or "").strip(),
                "remarks": str(main.get("remarks") or "").strip() or None,
                "ir_url": None,
                "retrieval_url": retrieval_url,
                "portal_url": LS_PORTAL_URL,
                "official_publisher": LS_PUBLISHER,
                "is_anchor_case": (assu_no == ANCHOR_ASSURANCE_REF),
                "row_count_in_group": len(grp),
            }
        )

    # 2. Group RS s260-271 records by unique (session, questionType, qno) — one task per unique RS question,
    #    skipping duplicate-entry questions that carry conflicting statuses (e.g. both FIMP & Pending) in Digital Sansad.
    rs_grouped: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
    for r in rs_rows:
        oassu = str(r.get("oassuNo") or "").strip()
        sess = str(r.get("session") or "").strip()
        qno_raw = str(r.get("qno") or "").strip()
        qtype = str(r.get("questionType") or "").strip()
        extent = str(r.get("extent") or "").strip()
        raw_min = str(r.get("ministry") or "").strip()
        subj = " ".join(str(r.get("subject") or "").split()).strip()
        pdate = parse_promise_date_iso(r.get("date"))
        raw_status = str(r.get("status") or "").strip()
        if not (oassu and sess.isdigit() and qno_raw.isdigit() and qtype in {"Starred", "Unstarred"} and extent and raw_min and subj and pdate and raw_status):
            continue
        rs_grouped[(sess, qtype, int(qno_raw))].append(r)

    rs_seen_tid: set[str] = set()
    for (sess, qtype, qno_int), grp in rs_grouped.items():
        grp_statuses = {normalize_status(str(x.get("status") or "")) for x in grp if str(x.get("status") or "").strip()}
        if len(grp_statuses) != 1:
            continue
        # Prefer the row with an irUrl / longest extent when multiple rows share the same question and status
        r = sorted(
            grp,
            key=lambda x: (1 if str(x.get("irUrl") or "").strip() else 0, len(str(x.get("extent") or "").strip())),
            reverse=True,
        )[0]
        oassu = str(r.get("oassuNo") or "").strip()
        qno_raw = str(qno_int)
        raw_min = str(r.get("ministry") or "").strip()
        subj = " ".join(str(r.get("subject") or "").split()).strip()
        member = " ".join(str(r.get("member") or "").split()).strip() or "Member of Parliament"
        pdate = parse_promise_date_iso(r.get("date"))
        assert pdate is not None
        raw_status = str(r.get("status") or "").strip()
        oassu_slug = re.sub(r"[^a-z0-9]+", "-", oassu.lower()).strip("-")
        tid = f"p6-rs-{oassu_slug}"
        if len(tid) > 60 or tid in rs_seen_tid:
            continue
        ministry, ministry_key = canonical_ministry(raw_min)
        meta_check = {"title": subj, "member": member, "ministry": ministry, "oassu": oassu}
        if not _is_clean_text_blob(meta_check):
            continue

        rs_seen_tid.add(tid)
        rec_status = normalize_status(raw_status)
        ir_url_raw = str(r.get("irUrl") or "").strip()
        ir_url: str | None = None
        if ir_url_raw.startswith("https://sansad.in/"):
            try:
                ir_url = safe_url(ir_url_raw)
            except ValueError:
                ir_url = None

        retrieval_url = (
            f"https://sansad.in/api_rs/govtAssurance/assurances"
            f"?qNo={qno_raw}&qType={urllib.parse.quote(qtype)}&sessionFrom={sess}&sessionTo={sess}"
            f"&page=1&size=20&locale=en"
        )
        candidates.append(
            {
                "task_id": tid,
                "assurance_key": tid[3:],  # rs-270-139
                "house": "Rajya Sabha",
                "house_code": "RS",
                "loksabha": None,
                "session": sess,
                "assurance_ref": f"RS-{oassu}",
                "oassu_no": oassu,
                "question_type": qtype,
                "question_no": qno_raw,
                "promise_date": pdate,
                "ministry": ministry,
                "ministry_key": ministry_key,
                "raw_ministry": raw_min,
                "member": member,
                "subject": subj,
                "recorded_status": rec_status,
                "raw_status": raw_status,
                "imp_record_no": "",
                "remarks": None,
                "ir_url": ir_url,
                "ext_no": r.get("extNo"),
                "ext_upto": r.get("extUpto"),
                "retrieval_url": retrieval_url,
                "portal_url": RS_PORTAL_URL,
                "official_publisher": RS_PUBLISHER,
                "is_anchor_case": False,
                "row_count_in_group": len(grp),
            }
        )

    # 3. Order candidates:
    #    - Anchor case SQ-18/07/544 FIRST
    #    - Both real PARTLY IMPLEMENTED cases right after
    #    - Stratified round-robin across ALL ministries, interleaving (LS Pending, RS Pending,
    #      RS FIMP w/ IR PDF, LS Fully Implemented, Dropped) so every ministry & status is covered!
    anchor = [c for c in candidates if c["is_anchor_case"]]
    if len(anchor) != 1:
        raise RuntimeError(f"Expected exactly 1 Jhunjhunu-Fatehpur anchor ({ANCHOR_ASSURANCE_REF}), got {len(anchor)}")

    used_ids: set[str] = {anchor[0]["task_id"]}
    ordered: list[dict[str, Any]] = [anchor[0]]

    # Include real partly_implemented cases early so all 4 status categories are guaranteed.
    for c in sorted(candidates, key=lambda x: x["task_id"]):
        if c["task_id"] in used_ids:
            continue
        if c["recorded_status"] == "partly_implemented":
            used_ids.add(c["task_id"])
            ordered.append(c)

    # Build per-ministry interleaved queues
    by_ministry: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for c in candidates:
        if c["task_id"] not in used_ids:
            by_ministry[c["ministry_key"]].append(c)

    ministry_queues: dict[str, list[dict[str, Any]]] = {}
    for mkey, items in sorted(by_ministry.items()):
        # Split into 5 strata per ministry for rich, fair interleaving
        b_ls_pend = sorted(
            [x for x in items if x["house_code"] == "LS" and x["recorded_status"] == "pending"],
            key=lambda x: (x["promise_date"], x["task_id"]),
        )  # oldest pending first (most overdue!) + recent interleaved below
        # Interleave newest & oldest pending so both recent 2026 and long-pending 2024-25 appear
        b_ls_pend_mix: list[dict[str, Any]] = []
        lo, hi = 0, len(b_ls_pend) - 1
        while lo <= hi:
            b_ls_pend_mix.append(b_ls_pend[lo])
            if lo != hi:
                b_ls_pend_mix.append(b_ls_pend[hi])
            lo += 1
            hi -= 1

        b_rs_pend = sorted(
            [x for x in items if x["house_code"] == "RS" and x["recorded_status"] == "pending"],
            key=lambda x: (-(int(x.get("ext_no") or 0)), x["promise_date"], x["task_id"]),
        )
        b_rs_fimp = sorted(
            [x for x in items if x["house_code"] == "RS" and x["recorded_status"] == "fully_implemented"],
            key=lambda x: (0 if x.get("ir_url") else 1, x["promise_date"], x["task_id"]),
        )
        b_ls_fimp = sorted(
            [x for x in items if x["house_code"] == "LS" and x["recorded_status"] == "fully_implemented"],
            key=lambda x: (x["promise_date"], x["task_id"]),
            reverse=True,
        )
        b_drop = sorted(
            [x for x in items if x["recorded_status"] == "dropped"],
            key=lambda x: (x["promise_date"], x["task_id"]),
        )

        # Interleave buckets: LS-Pending, RS-Pending, LS-Fulfilled, RS-FIMP(irUrl), Dropped
        buckets = [b_ls_pend_mix, b_rs_pend, b_ls_fimp, b_rs_fimp, b_drop]
        q: list[dict[str, Any]] = []
        idx = 0
        while any(buckets):
            b = buckets[idx % len(buckets)]
            if b:
                q.append(b.pop(0))
            buckets = [x for x in buckets if x]
            idx += 1
        ministry_queues[mkey] = q

    # Round-robin across all ministries until exhausted
    mkeys = sorted(ministry_queues.keys())
    while True:
        progressed = False
        for mkey in mkeys:
            q = ministry_queues[mkey]
            if q:
                item = q.pop(0)
                if item["task_id"] not in used_ids:
                    used_ids.add(item["task_id"])
                    ordered.append(item)
                progressed = True
        if not progressed:
            break

    return ordered


def _resolve_ls_pdf_url(cand: dict[str, Any], cache_dir: Path) -> str | None:
    meta_dir = cache_dir / "ls_qpdf_meta"
    meta_dir.mkdir(parents=True, exist_ok=True)
    mfile = meta_dir / f"{cand['task_id']}.json"
    if mfile.exists():
        data = json.loads(mfile.read_text(encoding="utf-8"))
        raw = str(data.get("path") or "").strip()
    else:
        info_url = (
            f"https://sansad.in/api_ls/question/pdf-path-info"
            f"?qType={cand['question_type']}&quesNo={cand['question_no']}"
            f"&lsno={cand['loksabha']}&session={cand['session']}&locale=en"
        )
        try:
            raw_bytes = http_get_bytes(info_url, retries=2, timeout=20)
            data = json.loads(raw_bytes.decode("utf-8"))
            mfile.write_bytes(raw_bytes)
            raw = str(data.get("path") or "").strip()
        except Exception:
            mfile.write_text('{"path": ""}', encoding="utf-8")
            return None
    if not raw or not raw.lower().startswith("https://sansad.in/") or ".pdf" not in raw.lower():
        return None
    try:
        return safe_url(raw)
    except ValueError:
        return None


def _fetch_and_verify_candidate(cand: dict[str, Any], cache_dir: Path) -> dict[str, Any] | None:
    """Fetch & verify primary single-question JSON + optional secondary PDF for one candidate."""
    json_dir = cache_dir / "primary_json"
    pdf_dir = cache_dir / "secondary_pdf"
    json_dir.mkdir(parents=True, exist_ok=True)
    pdf_dir.mkdir(parents=True, exist_ok=True)

    tid = cand["task_id"]
    p_file = json_dir / f"{tid}.json"
    try:
        if p_file.exists():
            raw_json = p_file.read_bytes()
        else:
            raw_json = http_get_bytes(cand["retrieval_url"], retries=3, timeout=25)
            p_file.write_bytes(raw_json)
        parsed = json.loads(raw_json.decode("utf-8"))
        recs = parsed.get("records") if isinstance(parsed, dict) else None
        if not isinstance(recs, list) or not recs:
            return None

        # Verify target assurance is present inside the single-question JSON response with unambiguous status
        if cand["house_code"] == "LS":
            matched = [r for r in recs if str(r.get("assuNo") or "").strip() == cand["assurance_ref"]]
        else:
            matched = [r for r in recs if str(r.get("oassuNo") or "").strip() == cand["oassu_no"]]
        if not matched:
            return None
        all_statuses = {normalize_status(str(r.get("status") or "")) for r in recs if str(r.get("status") or "").strip()}
        if len(all_statuses) != 1 or next(iter(all_statuses)) != cand["recorded_status"]:
            return None
        primary_sha = hashlib.sha256(raw_json).hexdigest()
    except Exception:
        return None

    # Resolve & verify optional secondary PDF
    sec_url: str | None = None
    sec_sha: str | None = None
    sec_role: str | None = None
    sec_bytes = 0
    sec_pages = 0
    sec_chars = 0

    candidate_pdf_url: str | None = None
    candidate_role: str | None = None
    if cand["house_code"] == "LS":
        candidate_pdf_url = _resolve_ls_pdf_url(cand, cache_dir)
        candidate_role = "question_pdf" if candidate_pdf_url else None
    elif cand["house_code"] == "RS" and cand.get("ir_url"):
        candidate_pdf_url = cand["ir_url"]
        candidate_role = "implementation_report_pdf"

    if candidate_pdf_url and candidate_role:
        pdf_file = pdf_dir / f"{tid}.pdf"
        skip_marker = pdf_dir / f"{tid}.skip"
        if not skip_marker.exists():
            try:
                if pdf_file.exists():
                    raw_pdf = pdf_file.read_bytes()
                else:
                    raw_pdf = http_get_bytes(candidate_pdf_url, retries=2, timeout=30)
                    if len(raw_pdf) > MAX_SECONDARY_PDF_BYTES or not raw_pdf.startswith(b"%PDF"):
                        skip_marker.write_text(f"oversized_or_not_pdf:{len(raw_pdf)}", encoding="utf-8")
                        raw_pdf = b""
                    else:
                        pdf_file.write_bytes(raw_pdf)
                if raw_pdf and raw_pdf.startswith(b"%PDF") and len(raw_pdf) <= MAX_SECONDARY_PDF_BYTES:
                    reader = PdfReader(io.BytesIO(raw_pdf))
                    n_pages = len(reader.pages)
                    if n_pages >= 1:
                        # Extract quick char count for telemetry
                        txt_len = sum(len((p.extract_text() or "").strip()) for p in reader.pages[: min(n_pages, 10)])
                        sec_url = candidate_pdf_url
                        sec_sha = hashlib.sha256(raw_pdf).hexdigest()
                        sec_role = candidate_role
                        sec_bytes = len(raw_pdf)
                        sec_pages = n_pages
                        sec_chars = txt_len
                    else:
                        skip_marker.write_text("zero_pages", encoding="utf-8")
            except Exception as exc:
                # For RS FIMP with broken/corrupt PDF, skip attaching bad secondary rather than breaking task
                if cand["is_anchor_case"]:
                    raise RuntimeError(f"Anchor secondary PDF failed: {exc}") from exc
                skip_marker.write_text(f"err:{type(exc).__name__}", encoding="utf-8")

    # Anchor case MUST have its verified LS Question PDF attached
    if cand["is_anchor_case"] and not (sec_url and sec_sha):
        raise RuntimeError("Anchor case SQ-18/07/544 missing verified Question PDF!")

    out = dict(cand)
    out.update(
        {
            "primary_sha256": primary_sha,
            "primary_bytes": len(raw_json),
            "primary_cache_path": str(p_file),
            "secondary_url": sec_url,
            "secondary_expected_sha256": sec_sha,
            "secondary_role": sec_role,
            "secondary_bytes": sec_bytes,
            "secondary_pages": sec_pages,
            "secondary_chars": sec_chars,
            "secondary_cache_path": str(pdf_dir / f"{tid}.pdf") if sec_url else None,
        }
    )
    return out


def collect_verified_records(
    candidates: list[dict[str, Any]],
    cache_dir: Path,
    *,
    target: int = TARGET_TASKS,
    workers: int = 10,
) -> list[dict[str, Any]]:
    """Fetch & verify exactly ``target`` candidates (preserving anchor #1 + ministry ordering)."""
    # Process in chunks slightly larger than remaining needed so we preserve round-robin order.
    verified_by_idx: dict[int, dict[str, Any]] = {}
    cursor = 0
    total_cands = len(candidates)
    while len(verified_by_idx) < target and cursor < total_cands:
        needed = target - len(verified_by_idx)
        batch_size = min(max(needed + 40, 60), total_cands - cursor)
        batch = list(range(cursor, cursor + batch_size))
        cursor += batch_size
        print(
            f"Verifying batch [{batch[0]}..{batch[-1]}] (have {len(verified_by_idx)}/{target} verified)...",
            flush=True,
        )
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_fetch_and_verify_candidate, candidates[i], cache_dir): i for i in batch}
            done = 0
            for fut in as_completed(futs):
                i = futs[fut]
                done += 1
                res = fut.result()
                if res is not None:
                    verified_by_idx[i] = res
                if done % 100 == 0 or done == len(batch):
                    print(
                        f"  batch progress: {done}/{len(batch)} (total verified={len(verified_by_idx)})",
                        flush=True,
                    )

    ordered_verified = [verified_by_idx[i] for i in sorted(verified_by_idx)]
    if len(ordered_verified) < target:
        raise RuntimeError(f"Only verified {len(ordered_verified)} candidates; need {target}.")
    selected = ordered_verified[:target]
    assert selected[0]["is_anchor_case"], "Task #1 must be the Jhunjhunu-Fatehpur anchor case!"
    return selected


def verify_live_byte_stability(selected: list[dict[str, Any]], *, sample_count: int = 8) -> None:
    """Spot-check live Digital Sansad URLs without cache to prove byte-stable SHA-256 pinning."""
    # Always check anchor #1 + a spread of LS/RS pending & fulfilled records
    picks = [selected[0]]
    step = max(1, len(selected) // max(sample_count - 1, 1))
    for i in range(step, len(selected), step):
        if len(picks) >= sample_count:
            break
        picks.append(selected[i])

    for rec in picks:
        live_primary = http_get_bytes(rec["retrieval_url"], retries=2, timeout=25)
        obs_sha = hashlib.sha256(live_primary).hexdigest()
        if obs_sha != rec["primary_sha256"]:
            raise RuntimeError(
                f"Live SHA-256 drift on primary {rec['task_id']}: cached={rec['primary_sha256']} live={obs_sha}"
            )
        if rec.get("secondary_url") and rec.get("secondary_expected_sha256"):
            live_sec = http_get_bytes(rec["secondary_url"], retries=2, timeout=30)
            obs_sec = hashlib.sha256(live_sec).hexdigest()
            if obs_sec != rec["secondary_expected_sha256"]:
                raise RuntimeError(
                    f"Live SHA-256 drift on secondary {rec['task_id']}: cached={rec['secondary_expected_sha256']} live={obs_sec}"
                )
    print(f"Live SHA-256 byte-stability verified across {len(picks)} primary + secondary Digital Sansad URLs.")


def _write_methodology_md(coverage: dict[str, Any]) -> None:
    by_st = coverage["by_recorded_status"]
    by_h = coverage["by_house"]
    dry = coverage["dry_run_summary"]
    anchor = coverage["anchor_case"]
    content = f"""# Methodology `P6` — Parliament's To-Do List

## Simple Hook
> **{HOOK_TEXT}**

## Objective
Track **1,000 formal ministerial assurances** across **{coverage['ministries_covered']} Union Ministries** in both Houses of the Indian Parliament directly from **Digital Sansad** (`https://sansad.in`). For every assurance, the deterministic `P6` rule engine extracts and cites three verified public-record elements:
1. **The Original Promise** — verbatim assurance text (`textOfAssurance` / `extent`), house, session, question reference, promise date, and (for Lok Sabha) the official Question & Answer PDF.
2. **The Recorded Status** — official Digital Sansad register status (`Pending`, `Fully Implemented`, `Partly Implemented`, or `Dropped`), days elapsed against the standard **90-day (3-month)** parliamentary assurance fulfillment timeline, Committee extensions (`extNo`, `extUpto`), and Committee remarks.
3. **What the Follow-Up Actually Answered** — extracted directly from pinned Rajya Sabha Implementation Report PDFs (`irUrl`), Lok Sabha Implementation Statement references (`impRecordNo`, e.g., `SS-1/28/05.08.2026`), Committee dropping remarks, or confirmed absence of any laid follow-up statement.

## Anchor Case (Task #1: `{anchor['task_id']}`)
- **Assurance Reference:** `{anchor['assurance_ref']}` (18th Lok Sabha, Session 7, answered `{anchor['promise_date']}`)
- **Ministry:** {anchor['ministry']}
- **Subject:** {anchor['subject']}
- **Recorded Status:** `{anchor['recorded_status']}` (`{anchor['days_elapsed']}` days elapsed as of `{coverage['as_of_date']}`, exceeding the 90-day norm)
- **Original Promise:** {anchor['original_promise']}
- **Follow-Up Status:** {anchor['followup_answer']}

## Corpus Coverage (`{coverage['total_tasks']}` Tasks, SHA-256 Pinned)
- **Lok Sabha (18th LS, Sessions 1–7):** `{by_h['Lok Sabha']['tasks']}` assurances (with official Digital Sansad Question & Answer PDFs pinned as `secondary_url` where published)
- **Rajya Sabha (Sessions 260–271):** `{by_h['Rajya Sabha']['tasks']}` assurances (with official Implementation Report PDFs `irUrl` pinned as `secondary_url` for `FIMP` assurances)
- **Recorded Status Breakdown:** `Pending`: `{by_st['pending']}` · `Fully Implemented`: `{by_st['fully_implemented']}` · `Partly Implemented`: `{by_st['partly_implemented']}` · `Dropped`: `{by_st['dropped']}`
- **Deterministic Dry-Run (`as_of_date={coverage['as_of_date']}`):** `{dry['leads']}` leads · `{dry['cleared']}` cleared · `{dry['unevaluable']}` unevaluable

## Deterministic Rules (`P6`)
1. **Overdue Pending Assurance (`P6-OVERDUE-PENDING` / `overdue_pending_assurance`)**: Recorded status is `pending` and elapsed time exceeds the parliamentary **90-day (3-month)** fulfillment standard (`medium` at `91–180` days, `high` at `181–365` days, `critical` at `>365` days or `>= 2` Committee extensions).
2. **Partly Implemented Assurance (`P6-PARTLY-IMPLEMENTED` / `partly_implemented_assurance`)**: Recorded status is `partly_implemented` (partial statement laid while core commitment remains open).
3. **Dropped Without Fulfillment (`P6-DROPPED-ASSURANCE` / `dropped_without_fulfillment`)**: Recorded status is `dropped` by the Committee on Government Assurances rather than substantively fulfilled.
4. **Delayed or Non-Machine-Readable Follow-Up (`P6-FOLLOWUP-GAP` / `delayed_or_unreadable_followup`)**: Recorded status is `fully_implemented`, but fulfillment took `>180` days, the pinned Rajya Sabha Implementation Report PDF lacks a machine-readable text layer (image-only scan), or no implementation statement reference is recorded.

Assurances still within the 90-day window (`pending`, `<=90` days) or fulfilled within `<=180` days with verifiable follow-up documentation are published as **Cleared** with equal standing and all three citations preserved.

## Transparent Methodological Boundaries & Benign Explanations
- **Lok Sabha Implementation Statement PDFs:** Digital Sansad's Lok Sabha Government Assurances API publishes the Implementation Statement reference (`impRecordNo`, e.g., `SS-1/28/05.08.2026`) and laying date, while its `papersLaid` API lists the corresponding MPA statements with `url: null`. `P6` cites the exact statement reference and transparently records that no public PDF link is attached on Digital Sansad.
- **Scanned Rajya Sabha IR PDFs:** Many Rajya Sabha `irUrl` Implementation Report PDFs are uploaded as scanned image PDFs without OCR text layers. `P6` verifies the PDF bytes via SHA-256 and explicitly flags image-only scans rather than guessing follow-up text.
- **Plausible Lawful Explanations:** Inter-ministerial consultations, State Government data collection, DPR / environmental clearance timelines (such as the December 2026 DPR estimate noted on NH-11), formal extensions granted by the Committee on Government Assurances, and parliamentary session calendars for laying statements.
"""
    PROJECT_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    (PROJECT_ASSETS_DIR / "METHODOLOGY.md").write_text(content, encoding="utf-8")


def build_and_write_project(selected: list[dict[str, Any]], cache_dir: Path) -> None:
    from aiwatchdogs.engines.registry import run_engine
    from aiwatchdogs.projects import validate_project

    tasks: list[dict[str, Any]] = []
    documents_rows: list[dict[str, Any]] = []
    by_house_counts: dict[str, dict[str, int]] = {
        "Lok Sabha": {"tasks": 0, "pending": 0, "fully_implemented": 0, "partly_implemented": 0, "dropped": 0, "with_secondary_pdf": 0, "leads_in_dry_run": 0},
        "Rajya Sabha": {"tasks": 0, "pending": 0, "fully_implemented": 0, "partly_implemented": 0, "dropped": 0, "with_secondary_pdf": 0, "leads_in_dry_run": 0},
    }
    by_status_counts: Counter[str] = Counter()
    by_signal_counts: Counter[str] = Counter()
    ministry_stats: dict[str, dict[str, Any]] = {}

    dry_run_leads = 0
    dry_run_cleared = 0
    dry_run_unevaluable = 0
    anchor_case_summary: dict[str, Any] | None = None

    print(f"Dry-running all {len(selected)} tasks through deterministic P6 engine...", flush=True)
    for idx, rec in enumerate(selected, start=1):
        tid = rec["task_id"]
        house = rec["house"]
        house_code = rec["house_code"]
        ministry = rec["ministry"]
        ministry_key = rec["ministry_key"]
        status_norm = rec["recorded_status"]
        status_label = _STATUS_LABELS.get(status_norm, status_norm.title())
        subj_short = clean_subject_for_title(rec["subject"])

        p6_spec: dict[str, Any] = {
            "assurance_key": rec["assurance_key"],
            "house": house,
            "house_code": house_code,
            "loksabha": rec["loksabha"],
            "session": rec["session"],
            "assurance_ref": rec["assurance_ref"],
            "question_type": rec["question_type"],
            "question_no": rec["question_no"],
            "promise_date": rec["promise_date"],
            "ministry": ministry,
            "ministry_key": ministry_key,
            "raw_ministry": rec["raw_ministry"],
            "member": rec["member"],
            "subject": rec["subject"],
            "recorded_status": status_norm,
            "raw_status": rec["raw_status"],
            "secondary_role": rec["secondary_role"],
            "portal_url": rec["portal_url"],
            "is_anchor_case": bool(rec["is_anchor_case"]),
        }
        if rec.get("oassu_no"):
            p6_spec["oassu_no"] = rec["oassu_no"]

        task_context: dict[str, Any] = {
            "official_publisher": rec["official_publisher"],
            "source_landing_page": rec["portal_url"],
            "retrieval_url": rec["retrieval_url"],
            "expected_sha256": rec["primary_sha256"],
            "parliaments_to_do_list": p6_spec,
        }
        if rec.get("secondary_url") and rec.get("secondary_expected_sha256"):
            task_context["secondary_url"] = rec["secondary_url"]
            task_context["secondary_expected_sha256"] = rec["secondary_expected_sha256"]

        # Load via canonical loader.py using local cached files (verifying identical SHA-256!)
        loader_ctx = dict(task_context)
        if rec.get("secondary_cache_path"):
            loader_ctx["secondary_path"] = rec["secondary_cache_path"]
            loader_ctx.pop("secondary_url", None)
        loaded = load_dataset(
            rec["primary_cache_path"],
            context=loader_ctx,
            allow_local_files=True,
            expected_sha256=rec["primary_sha256"],
            secondary_expected_sha256=rec.get("secondary_expected_sha256"),
        )
        result = run_engine(METHODOLOGY_CODE, loaded.records, task_context, as_of_date=AS_OF_DATE)
        if result.unevaluable_count > 0:
            dry_run_unevaluable += result.unevaluable_count
            raise RuntimeError(f"Task {tid} evaluated as unevaluable: {result.summary}")

        dry_run_cleared += result.cleared_count
        lead_signal: str | None = None
        lead_metrics: dict[str, Any] = {}
        if result.leads:
            dry_run_leads += len(result.leads)
            lead = result.leads[0]
            lead_metrics = dict(lead.anomaly_metrics or {})
            if lead_metrics.get("recorded_status") != status_norm:
                raise RuntimeError(
                    f"Status mismatch on {tid}: spec={status_norm} engine={lead_metrics.get('recorded_status')}"
                )
            lead_signal = str(lead_metrics.get("signal_category") or lead.rule_triggered or "flagged")
            by_signal_counts[lead_signal] += 1
            by_house_counts[house]["leads_in_dry_run"] += 1

        # Update house / status / ministry stats
        by_house_counts[house]["tasks"] += 1
        by_house_counts[house][status_norm] += 1
        if rec.get("secondary_url"):
            by_house_counts[house]["with_secondary_pdf"] += 1
        by_status_counts[status_norm] += 1

        ms = ministry_stats.setdefault(
            ministry_key,
            {
                "ministry": ministry,
                "ministry_key": ministry_key,
                "tasks": 0,
                "lok_sabha_tasks": 0,
                "rajya_sabha_tasks": 0,
                "pending_count": 0,
                "fulfilled_count": 0,
                "partly_implemented_count": 0,
                "dropped_count": 0,
                "leads_in_dry_run": 0,
            },
        )
        ms["tasks"] += 1
        if house_code == "LS":
            ms["lok_sabha_tasks"] += 1
        else:
            ms["rajya_sabha_tasks"] += 1
        if status_norm == "pending":
            ms["pending_count"] += 1
        elif status_norm == "fully_implemented":
            ms["fulfilled_count"] += 1
        elif status_norm == "partly_implemented":
            ms["partly_implemented_count"] += 1
        elif status_norm == "dropped":
            ms["dropped_count"] += 1
        if result.leads:
            ms["leads_in_dry_run"] += 1

        if rec["is_anchor_case"]:
            anchor_case_summary = {
                "task_id": tid,
                "assurance_ref": rec["assurance_ref"],
                "house": house,
                "session": rec["session"],
                "promise_date": rec["promise_date"],
                "ministry": ministry,
                "member": rec["member"],
                "minister": lead_metrics.get("minister") or "Shri Nitin Jairam Gadkari",
                "subject": clean_subject_for_title(rec["subject"], max_len=140),
                "recorded_status": status_norm,
                "raw_status": rec["raw_status"],
                "days_elapsed": lead_metrics.get("days_elapsed"),
                "original_promise": lead_metrics.get("original_promise") or "",
                "followup_answer": lead_metrics.get("followup_answer") or "",
                "source_url": rec["retrieval_url"],
                "secondary_url": rec["secondary_url"],
                "portal_url": rec["portal_url"],
            }

        sec_note = ""
        if rec["secondary_role"] == "question_pdf":
            sec_note = " and its pinned Lok Sabha Question/Answer PDF"
        elif rec["secondary_role"] == "implementation_report_pdf":
            sec_note = " and its pinned Rajya Sabha Implementation Report PDF"

        title = f"{ministry} · {house_code} {rec['assurance_ref']} ({status_label}): {subj_short}"
        instructions = (
            f"Download the checksummed Digital Sansad assurance record for {house} "
            f"{rec['assurance_ref']} ({ministry}, answered {rec['promise_date']}){sec_note}, "
            f"and verify three cited facts: (1) the verbatim original promise, "
            f"(2) the recorded status and elapsed days against the 90-day parliamentary norm, and "
            f"(3) what the follow-up implementation statement or Committee record actually answered."
        )

        payload: dict[str, Any] = {
            "retrieval_url": rec["retrieval_url"],
            "expected_sha256": rec["primary_sha256"],
            "official_publisher": rec["official_publisher"],
            "source_landing_page": rec["portal_url"],
            "as_of_date": AS_OF_DATE,
            "context": task_context,
        }
        if rec.get("secondary_url") and rec.get("secondary_expected_sha256"):
            payload["secondary_url"] = rec["secondary_url"]
            payload["secondary_expected_sha256"] = rec["secondary_expected_sha256"]

        tasks.append(
            {
                "id": tid,
                "title": title,
                "instructions": instructions,
                "type": "run_check",
                "methodology_code": METHODOLOGY_CODE,
                "dataset_key": f"sansad-{rec['assurance_key']}",
                "financial_quantity_type": "parliamentary_assurances",
                "estimated_tokens": 9000 if rec.get("secondary_url") else 5000,
                "estimate_basis": (
                    "Deterministic JSON + official PDF extraction on Digital Sansad assurance record (zero LLM cost in rule-engine mode)."
                    if rec.get("secondary_url")
                    else "Deterministic JSON extraction on Digital Sansad assurance record (zero LLM cost in rule-engine mode)."
                ),
                "payload": payload,
            }
        )

        documents_rows.append(
            {
                "task_id": tid,
                "house": house,
                "session": rec["session"],
                "assurance_ref": rec["assurance_ref"],
                "promise_date": rec["promise_date"],
                "ministry": ministry,
                "recorded_status": status_norm,
                "signal_category": lead_signal or "cleared",
                "primary_bytes": rec["primary_bytes"],
                "primary_sha256": rec["primary_sha256"],
                "primary_url": rec["retrieval_url"],
                "secondary_role": rec["secondary_role"] or "",
                "secondary_pages": rec["secondary_pages"] or 0,
                "secondary_sha256": rec["secondary_expected_sha256"] or "",
                "secondary_url": rec["secondary_url"] or "",
            }
        )

        if idx % 200 == 0 or idx == len(selected):
            print(f"  dry-run progress: {idx}/{len(selected)} (leads={dry_run_leads}, cleared={dry_run_cleared})", flush=True)

    assert len(tasks) == TARGET_TASKS, f"Expected {TARGET_TASKS} tasks, built {len(tasks)}"
    assert len({t["payload"]["retrieval_url"] for t in tasks}) == TARGET_TASKS, "Duplicate retrieval_url detected!"
    assert anchor_case_summary is not None, "Anchor case summary missing!"

    ministries_sorted = sorted(
        ministry_stats.values(),
        key=lambda x: (-x["pending_count"], -x["tasks"], x["ministry"]),
    )

    coverage: dict[str, Any] = {
        "project_slug": PROJECT_SLUG,
        "project_code": PROJECT_CODE,
        "methodology_code": METHODOLOGY_CODE,
        "hook": HOOK_TEXT,
        "generated_on": datetime.now(timezone.utc).date().isoformat(),
        "as_of_date": AS_OF_DATE,
        "total_tasks": len(tasks),
        "ministries_covered": len(ministries_sorted),
        "documents_count": len(documents_rows),
        "by_house": by_house_counts,
        "by_recorded_status": {
            "pending": by_status_counts["pending"],
            "fully_implemented": by_status_counts["fully_implemented"],
            "partly_implemented": by_status_counts["partly_implemented"],
            "dropped": by_status_counts["dropped"],
        },
        "dry_run_summary": {
            "tasks_tested": len(tasks),
            "leads": dry_run_leads,
            "cleared": dry_run_cleared,
            "unevaluable": dry_run_unevaluable,
            "by_signal": dict(sorted(by_signal_counts.items())),
        },
        "anchor_case": anchor_case_summary,
        "ministries": ministries_sorted,
    }

    project_obj: dict[str, Any] = {
        "slug": PROJECT_SLUG,
        "project_code": PROJECT_CODE,
        "priority": 6,
        "title": "Parliament's To-Do List: Tracking ministerial assurances across ministries",
        "summary": (
            f"{HOOK_TEXT} Tracks 1,000 formal ministerial assurances across all "
            f"{len(ministries_sorted)} Union Ministries in the 18th Lok Sabha and Rajya Sabha "
            "(Sessions 260–271) directly from Digital Sansad, citing for each the original promise, "
            "the recorded status, and what the follow-up actually answered — anchored by the pending "
            "NH-11 Jhunjhunu-to-Fatehpur assurance (SQ-18/07/544)."
        ),
        "posted_by": {
            "name": "AI Watchdogs India",
            "handle": "@aiwatchdogs",
            "url": "https://aiwatchdogs.in",
        },
        "pillar": "parliament",
        "status": "active",
        "methodology_code": METHODOLOGY_CODE,
        "jurisdiction": "Union of India — Parliament of India (18th Lok Sabha & Rajya Sabha Sessions 260–271)",
        "authority": "Lok Sabha Secretariat, Rajya Sabha Secretariat, Ministry of Parliamentary Affairs, and Union Ministries",
        "statutory_anchors": [
            "Constitution-Art-75-3",
            "Lok-Sabha-Rules-323-324",
            "Rajya-Sabha-Rules-211A-212",
            "MPA-Manual-Parliamentary-Procedures-90d",
        ],
        "scope": [
            f"1,000 formal ministerial assurances across all {len(ministries_sorted)} active Union Ministries in the 18th Lok Sabha ({by_house_counts['Lok Sabha']['tasks']} tasks) and Rajya Sabha Sessions 260–271 ({by_house_counts['Rajya Sabha']['tasks']} tasks)",
            "Anchor case Task #1: SQ-18/07/544 (02 Apr 2026, Ministry of Road Transport and Highways) — Widening of NH-11 Road from Jhunjhunu to Fatehpur, listed as Pending on Digital Sansad",
            "Three-part deterministic citation per assurance: (1) verbatim original promise & Question PDF, (2) recorded status & elapsed days vs 90-day parliamentary norm, and (3) what the follow-up implementation report or Committee record actually answered",
            "Covers Pending, Fully Implemented, Partly Implemented, and Dropped assurances with equal publication of Cleared records",
        ],
        "out_of_scope": [
            "Informal floor remarks or press statements not formally culled as Government Assurances by the Lok Sabha or Rajya Sabha Secretariats",
            "State Legislative Assembly assurances (scoped strictly to Parliament of India Union Ministerial assurances)",
            "Authenticated Ministry-internal OAMS workflow notes (public Digital Sansad records and public PDFs only)",
        ],
        "sources": [
            {
                "name": "Digital Sansad — Lok Sabha Government Assurances Portal & API",
                "publisher": LS_PUBLISHER,
                "url": LS_PORTAL_URL,
            },
            {
                "name": "Digital Sansad — Rajya Sabha Government Assurances Portal & API",
                "publisher": RS_PUBLISHER,
                "url": RS_PORTAL_URL,
            },
            {
                "name": "Online Assurance Monitoring System (OAMS) — Ministry of Parliamentary Affairs",
                "publisher": "Ministry of Parliamentary Affairs, Government of India",
                "url": OAMS_PORTAL_URL,
            },
        ],
        "planned_tasks": [
            {
                "title": "17th Lok Sabha historical pending assurances backlog (248 older assurances carried forward across ministries)",
                "type": "run_check",
                "estimated_tokens": 12000,
                "estimate_basis": "Follow-on cycle once 18th Lok Sabha and recent Rajya Sabha cohort is audited",
            },
            {
                "title": "Structured text verification of scanned Rajya Sabha Implementation Report PDFs and Lok Sabha laid statements",
                "type": "extract_data",
                "estimated_tokens": 16000,
                "estimate_basis": "Requires OCR pass or machine-readable text publication by Parliament Secretariats",
            },
        ],
        "tasks": tasks,
    }

    errs = validate_project(project_obj, filename_stem=PROJECT_SLUG)
    if errs:
        raise RuntimeError(f"Generated project JSON failed validation ({len(errs)} errors): {errs[:10]}")

    PROJECT_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    PROJECT_JSON_PATH.write_text(json.dumps(project_obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (PROJECT_ASSETS_DIR / "coverage.json").write_text(
        json.dumps(coverage, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    with (PROJECT_ASSETS_DIR / "documents.csv").open("w", encoding="utf-8", newline="") as fh:
        fieldnames = list(documents_rows[0].keys())
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in documents_rows:
            writer.writerow({k: sanitize_csv_cell(v) for k, v in row.items()})
    _write_methodology_md(coverage)

    print(f"\nBuilt {PROJECT_JSON_PATH} with {len(tasks)} tasks across {len(ministries_sorted)} ministries.")
    print(f"By house: {json.dumps(by_house_counts)}")
    print(f"By status: {json.dumps(coverage['by_recorded_status'])}")
    print(f"Dry-run: {json.dumps(coverage['dry_run_summary'])}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Project P6 (Parliament's To-Do List) from Digital Sansad.")
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE_DIR, help="Cache directory for Digital Sansad files.")
    parser.add_argument("--refresh-index", action="store_true", help="Re-download master LS18 & RS260-271 indexes.")
    parser.add_argument("--fetch-only", action="store_true", help="Fetch, verify & cache the 1,000 records without running P6 engine.")
    parser.add_argument("--skip-live-check", action="store_true", help="Skip live repeat-fetch byte-stability check.")
    parser.add_argument("--workers", type=int, default=10, help="Concurrent HTTP fetch workers.")
    args = parser.parse_args()

    cache_dir: Path = args.cache
    manifest_path = cache_dir / "selected_1000_manifest.json"

    if not args.refresh_index and manifest_path.exists() and not args.fetch_only:
        selected = json.loads(manifest_path.read_text(encoding="utf-8"))
        print(f"Loaded {len(selected)} verified records from cache manifest {manifest_path}.")
    else:
        ls_rows, rs_rows = fetch_master_indexes(cache_dir, refresh=args.refresh_index)
        print(f"Master indexes loaded: LS18={len(ls_rows)}, RS260-271={len(rs_rows)}")
        candidates = build_candidate_pool(ls_rows, rs_rows)
        distinct_mins = len({c["ministry_key"] for c in candidates})
        print(f"Built clean candidate pool: {len(candidates)} assurances across {distinct_mins} ministries.")
        selected = collect_verified_records(candidates, cache_dir, target=TARGET_TASKS, workers=args.workers)
        manifest_path.write_text(json.dumps(selected, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Saved {len(selected)} verified records to {manifest_path}.")

    if not args.skip_live_check:
        verify_live_byte_stability(selected, sample_count=8)

    # Print summary telemetry
    houses = Counter(r["house"] for r in selected)
    statuses = Counter(r["recorded_status"] for r in selected)
    sec_roles = Counter(r["secondary_role"] or "none" for r in selected)
    mins = len({r["ministry_key"] for r in selected})
    print(f"Selection summary: tasks={len(selected)}, ministries={mins}, houses={dict(houses)}, statuses={dict(statuses)}, secondary={dict(sec_roles)}")

    if args.fetch_only:
        return

    build_and_write_project(selected, cache_dir)


if __name__ == "__main__":
    main()
