#!/usr/bin/env python3
"""Build Project P4, ``projects/one-road-two-bills.json``, from official CAG Union reports (past 2 years).

    uv run python scripts/build_one_road_two_bills_project.py --cache ~/.cache/aiwatchdogs/one-road-two-bills --download

Every Union Government audit report tabled in the past 2 years is published publicly on
``cag.gov.in`` (direct CloudFront PDFs, zero login or CAPTCHA). This script:

1. catalogs Union Government audit reports on ``cag.gov.in/en/audit-report?gt=50`` for 2025,
   2026, and late-2024 (tabling/forwarding date >= 2024-11-27), excluding State reports
   mistagged under Union on the portal;
2. downloads missing English PDFs into ``--cache`` (respecting 100 MiB and 1,000-page loader caps)
   and deduplicates identical SHA-256 digests;
3. parses every PDF with ``aiwatchdogs.connectors.loader.load_dataset`` (same reader as the CLI)
   and splits substantive pages into non-overlapping paragraph/section blocks;
4. packs all blocks into up to 10,000 non-overlapping tasks;
5. dry-runs every task through the ``OR1`` engine and validates the generated project file before
   writing ``one-road-two-bills.json``, ``documents.csv``, ``coverage.json``, and ``METHODOLOGY.md``.
"""

from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
import logging
import math
import multiprocessing
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from aiwatchdogs.connectors.loader import DEFAULT_MAX_DOWNLOAD_BYTES, load_dataset  # noqa: E402
from aiwatchdogs.engines import one_road_two_bills as or1  # noqa: E402
from aiwatchdogs.engines.registry import run_engine  # noqa: E402
from aiwatchdogs.projects import validate_project  # noqa: E402

logging.getLogger("pypdf").setLevel(logging.ERROR)

SLUG = or1.ONE_ROAD_TWO_BILLS_PROJECT_SLUG
METHODOLOGY = or1.ONE_ROAD_TWO_BILLS_METHODOLOGY
TARGET_TASKS = 10_000
MAX_WINDOWS_PER_TASK = 10
USER_AGENT = "AIWatchdogs-India-OpenAudit/0.1 (aiwatchdogs.in; lawful-public-audit)"
CUTOFF_DATE = date(2024, 11, 27)  # rolling 2-year window as of 2026-11-27
CAG_LIST_URL = "https://cag.gov.in/en/audit-report?gt=50&yrf={year}&page={page}"
CAG_DETAIL_URL = "https://cag.gov.in/en/audit-report/details/{cag_id}"

_STATE_MISTAG_RE = re.compile(
    r"Article\s+151\(2\)|State\s+Legislature|"
    r"Government\s+of\s+(?:Punjab|Haryana|Maharashtra|Gujarat|Bihar|Odisha|Assam|Kerala|"
    r"Karnataka|Tamil\s+Nadu|Rajasthan|Madhya\s+Pradesh|Uttar\s+Pradesh|West\s+Bengal|"
    r"Andhra\s+Pradesh|Telangana|Jharkhand|Chhattisgarh|Uttarakhand|Himachal\s+Pradesh|"
    r"Goa|Manipur|Meghalaya|Mizoram|Nagaland|Sikkim|Tripura|Arunachal\s+Pradesh)",
    re.IGNORECASE,
)
_REPORT_NO_RE = re.compile(r"(?:Audit\s+)?Report\s*(?:No\.?)?\s*(\d+\s+of\s+20\d{2})", re.IGNORECASE)
_PRIORITY_TERMS_RE = re.compile(
    r"(?i)\b(?:duplicate|double|twice|billed|billing|voucher|invoice|receipt|"
    r"payment|reimbursement|contractor|road|bridge|highway|project|work|agency|"
    r"expenditure|excess|irregular|crore|lakh)\b"
)


@dataclass
class ReportDoc:
    cag_id: str
    listing_year: int
    report_year: str
    report_no: str
    title: str
    department: str
    sector: str
    tabled_date: str
    sent_date: str
    landing_url: str
    pdf_url: str
    fallback_pdf_url: str
    cache_path: Path
    sha256: str = ""
    byte_size: int = 0
    pages_total: int = 0
    pages_used: int = 0
    error: str = ""
    status: str = "pending"
    pages: dict[int, str] = field(default_factory=dict)


@dataclass(frozen=True)
class BlockItem:
    report: ReportDoc
    page: int
    block: or1.PageBlock
    priority_score: int


def _http_get_text(url: str, timeout: int = 25) -> str:
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise
            time.sleep(1.0 * (attempt + 1))
        except OSError:
            time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"Failed to fetch {url}")


def _percent_encode_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url.strip())
    encoded_path = urllib.parse.quote(urllib.parse.unquote(parts.path), safe="/:@!$&'()*+,;=-._~")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, encoded_path, parts.query, parts.fragment))


def _parse_cag_date(raw: str) -> date | None:
    cleaned = " ".join((raw or "").replace(",", "").split())
    for fmt in ("%d %b %Y", "%d %B %Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


def _select_english_pdf(links: list[tuple[str, str]]) -> tuple[str, str]:
    """Return (primary_pdf_url, fallback_pdf_url) preferring full English PDFs."""
    dedup: list[tuple[str, str]] = []
    seen_urls: set[str] = set()
    for raw_url, raw_lbl in links:
        enc = _percent_encode_url(raw_url)
        if enc in seen_urls:
            continue
        seen_urls.add(enc)
        lbl = " ".join(re.sub(r"<[^>]+>", " ", raw_lbl).split())
        dedup.append((enc, lbl))

    def _is_chapter_or_toc(url: str, lbl: str) -> bool:
        low_u = url.rsplit("/", 1)[-1].lower()
        low_l = lbl.lower()
        return any(
            tok in low_u or tok in low_l
            for tok in ("table-of-contents", "table of contents", "chap-", "chap_", "chapter ", "preface", "glossary", "appendices", "overview")
        )

    def _is_hindi(url: str, lbl: str) -> bool:
        low = (url + " " + lbl).lower()
        return "hindi" in low or "हिन्दी" in low

    non_hindi = [(u, l) for u, l in dedup if not _is_hindi(u, l)]
    pool = non_hindi or dedup
    full_candidates = [(u, l) for u, l in pool if not _is_chapter_or_toc(u, l)]
    ordered = full_candidates or pool
    non_pdfa = [u for u, l in ordered if "pdf a format" not in l.lower()]
    pdfa = [u for u, l in ordered if "pdf a format" in l.lower()]
    final = non_pdfa + pdfa
    if not final:
        return "", ""
    return final[0], (final[1] if len(final) > 1 else "")


def discover_union_reports(cache_dir: Path) -> tuple[list[ReportDoc], list[dict[str, str]]]:
    """Discover Union Government CAG audit reports from the past 2 years."""
    discovered: dict[str, tuple[int, str]] = {}
    for yrf in (2026, 2025, 2024):
        for page in range(1, 25):
            url = CAG_LIST_URL.format(year=yrf, page=page)
            try:
                html = _http_get_text(url)
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    break
                raise
            items = re.findall(r"href=[\x27\"]/en/audit-report/details/(\d+)[\x27\"][^>]*>([^<]+)<", html)
            if not items:
                break
            for rid, title in items:
                if rid not in discovered:
                    discovered[rid] = (yrf, " ".join(title.split()))
            time.sleep(0.1)

    docs: list[ReportDoc] = []
    skipped: list[dict[str, str]] = []
    for rid, (yrf, title) in discovered.items():
        landing = CAG_DETAIL_URL.format(cag_id=rid)
        det = _http_get_text(landing)
        idx = det.find("Breadcrumb")
        if idx < 0:
            idx = int(len(det) * 0.5)
        body = det[idx : idx + 25_000]
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)).strip()

        m_tab = re.search(
            r"Date on which Report Tabled:\s*(?:[A-Za-z]{3}\s+)?(\d{1,2}\s+[A-Za-z]{3},?\s+20\d{2})",
            text,
        )
        m_sent = re.search(
            r"Date of sending the report to Government\s*(?:[A-Za-z]{3}\s+)?(\d{1,2}\s+[A-Za-z]{3},?\s+20\d{2})",
            text,
        )
        m_dept = re.search(r"Union Department\s+(.*?)\s+Download Report", text)
        m_sec = re.search(r"Download Report\s+Sector\s+(.*?)\s+(?:Download Audit Report|Overview)", text)
        tabled_str = m_tab.group(1) if m_tab else ""
        sent_str = m_sent.group(1) if m_sent else ""
        tabled_dt = _parse_cag_date(tabled_str)
        sent_dt = _parse_cag_date(sent_str)

        # Past-2-years window: all 2025/2026 reports, plus 2024 listings tabled/sent >= 2024-11-27.
        in_window = yrf in (2025, 2026) or (
            yrf == 2024
            and (
                (tabled_dt is not None and tabled_dt >= CUTOFF_DATE)
                or (sent_dt is not None and sent_dt >= CUTOFF_DATE)
            )
        )
        if not in_window:
            continue

        # Exclude State reports mistakenly tagged under Union on cag.gov.in.
        if _STATE_MISTAG_RE.search(text) or _STATE_MISTAG_RE.search(title):
            skipped.append({"cag_id": rid, "title": title, "reason": "state_report_mistagged_as_union"})
            continue

        links = re.findall(
            r"<a[^>]*href=[\x27\"](https://[^\x27\"]*download_audit_report[^\x27\"]+\.pdf)[\x27\"][^>]*>(.*?)</a>",
            body,
            re.S | re.I,
        )
        primary_url, fallback_url = _select_english_pdf(links)
        if not primary_url:
            skipped.append({"cag_id": rid, "title": title, "reason": "no_pdf_uploaded_on_cag_portal"})
            continue
        if _STATE_MISTAG_RE.search(primary_url) or any(
            st_name in primary_url.lower() for st_name in ("haryana", "punjab", "maharashtra", "bihar", "odisha", "assam", "kerala", "karnataka", "rajasthan", "gujarat")
        ):
            skipped.append({"cag_id": rid, "title": title, "reason": "state_report_mistagged_as_union"})
            continue
        if "-hindi" in primary_url.lower() or "_hindi" in primary_url.lower():
            skipped.append({"cag_id": rid, "title": title, "reason": "hindi_duplicate_listing"})
            continue

        m_rno = _REPORT_NO_RE.search(title)
        report_no = f"Report No. {m_rno.group(1)}" if m_rno else f"CAG Union Report {rid} ({yrf})"
        report_year = str(tabled_dt.year) if (yrf == 2024 and tabled_dt and tabled_dt.year >= 2025) else str(yrf)
        dept = (m_dept.group(1).strip() if m_dept else "") or "Union"
        sector = (m_sec.group(1).strip() if m_sec else "") or dept
        if sector in ("-", "--"):
            sector = dept

        docs.append(
            ReportDoc(
                cag_id=rid,
                listing_year=yrf,
                report_year=report_year,
                report_no=report_no,
                title=or1.neutral_text(title),
                department=dept,
                sector=sector,
                tabled_date=tabled_dt.isoformat() if tabled_dt else "",
                sent_date=sent_dt.isoformat() if sent_dt else "",
                landing_url=landing,
                pdf_url=primary_url,
                fallback_pdf_url=fallback_url,
                cache_path=cache_dir / f"cag-{rid}.pdf",
            )
        )
        time.sleep(0.08)

    docs.sort(key=lambda d: (d.report_year, int(d.cag_id)), reverse=True)
    return docs, skipped


def _download_pdf(url: str, dest: Path) -> tuple[bool, str]:
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=90) as resp:
                clen = resp.headers.get("Content-Length")
                if clen and int(clen) > DEFAULT_MAX_DOWNLOAD_BYTES:
                    return False, f"exceeds_{DEFAULT_MAX_DOWNLOAD_BYTES}_bytes"
                chunks: list[bytes] = []
                total = 0
                while True:
                    chunk = resp.read(256 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > DEFAULT_MAX_DOWNLOAD_BYTES:
                        return False, f"exceeds_{DEFAULT_MAX_DOWNLOAD_BYTES}_bytes"
                    chunks.append(chunk)
                data = b"".join(chunks)
            if not data.startswith(b"%PDF-"):
                return False, "not_a_pdf"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            return True, ""
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 404, 410):
                return False, f"http_{exc.code}"
        except OSError as exc:
            if attempt == 3:
                return False, f"download_error:{type(exc).__name__}"
        time.sleep(1.2 * (attempt + 1))
    return False, "download_failed"


def ensure_downloads(docs: list[ReportDoc], download: bool) -> None:
    for doc in docs:
        if doc.cache_path.exists():
            continue
        if not download:
            doc.error = "missing_from_cache"
            doc.status = "missing_from_cache"
            continue
        ok, err = _download_pdf(doc.pdf_url, doc.cache_path)
        if not ok and doc.fallback_pdf_url:
            ok2, err2 = _download_pdf(doc.fallback_pdf_url, doc.cache_path)
            if ok2:
                doc.pdf_url = doc.fallback_pdf_url
                ok, err = True, ""
            else:
                err = err2 or err
        if not ok:
            doc.error = err
            doc.status = err
        time.sleep(0.15)


def _load_one_pdf(path_str: str) -> dict[str, Any]:
    path = Path(path_str)
    if not path.exists():
        return {"sha256": "", "size": 0, "error": "missing_file", "pages": {}}
    try:
        ds = load_dataset(path_str, allow_local_files=True, use_cache=False)
    except Exception as exc:  # noqa: BLE001
        raw = path.read_bytes()
        return {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size": len(raw),
            "error": f"{type(exc).__name__}:{str(exc)[:120]}",
            "pages": {},
        }
    pages = {int(rec["page"]): str(rec.get("raw_text") or "") for rec in ds.records}
    return {"sha256": ds.sha256, "size": ds.byte_size, "error": "", "pages": pages}


def load_all_pdfs(docs: list[ReportDoc], workers: int) -> list[ReportDoc]:
    readable_candidates = [d for d in docs if not d.error]
    ctx = multiprocessing.get_context("fork")
    with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as pool:
        results = list(pool.map(_load_one_pdf, [str(d.cache_path) for d in readable_candidates], chunksize=2))

    seen_hashes: dict[str, str] = {}
    usable: list[ReportDoc] = []
    for doc, res in zip(readable_candidates, results, strict=True):
        doc.sha256 = res["sha256"]
        doc.byte_size = res["size"]
        if res["error"]:
            doc.error = res["error"]
            doc.status = f"unreadable:{res['error']}"
            continue
        if doc.sha256 in seen_hashes:
            doc.error = f"duplicate_of_{seen_hashes[doc.sha256]}"
            doc.status = doc.error
            continue
        seen_hashes[doc.sha256] = doc.cag_id
        doc.pages = res["pages"]
        doc.pages_total = len(doc.pages)
        doc.pages_used = sum(1 for txt in doc.pages.values() if len("".join(txt.split())) >= 40)
        if doc.pages_used == 0:
            doc.error = "no_extractable_text"
            doc.status = "no_extractable_text"
            continue
        doc.status = "used"
        usable.append(doc)
    return usable


def extract_all_blocks(docs: list[ReportDoc]) -> list[BlockItem]:
    items: list[BlockItem] = []
    for doc in docs:
        for page_num in sorted(doc.pages):
            txt = doc.pages[page_num]
            if len("".join(txt.split())) < 40:
                continue
            for block in or1.extract_page_blocks(txt):
                if or1._is_boilerplate_block(block.text):
                    continue
                hits = len(_PRIORITY_TERMS_RE.findall(block.text))
                score = hits * 1000 + min(len(block.text), 2000)
                items.append(BlockItem(report=doc, page=page_num, block=block, priority_score=score))
    return items


def pack_tasks(blocks: list[BlockItem], target_tasks: int) -> list[dict[str, Any]]:
    """Pack non-overlapping blocks into up to ``target_tasks`` tasks without losing any block."""
    if not blocks:
        return []
    by_report: dict[str, list[BlockItem]] = collections.defaultdict(list)
    for b in blocks:
        by_report[b.report.cag_id].append(b)

    # Start with 1 task per block; if len(blocks) > target_tasks, group contiguous lower-priority
    # blocks within the same report into multi-window tasks (up to MAX_WINDOWS_PER_TASK) until we
    # reach exactly target_tasks (or the minimum possible tasks).
    groups: list[list[BlockItem]] = []
    if len(blocks) <= target_tasks:
        groups = [[b] for b in blocks]
    else:
        # Decide how many blocks stay singleton (highest priority scores) vs grouped.
        # Reserve enough capacity for grouping the rest cleanly per report.
        ranked_indices = sorted(range(len(blocks)), key=lambda i: (-blocks[i].priority_score, i))
        # Binary search or greedy exact fit: pick top S blocks as singletons so remaining fit in (target_tasks - S).
        best_singletons: set[int] = set()
        lo, hi = 0, target_tasks - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            singleton_idx = set(ranked_indices[:mid])
            # Simulate grouping non-singletons per report in reading order
            rem_groups = 0
            for cid, r_blocks in by_report.items():
                run_len = 0
                for b in r_blocks:
                    # We track membership by object id
                    pass
            # Faster: map id(b) -> is_singleton
            is_sing = {id(blocks[i]) for i in singleton_idx}
            total_groups = mid
            for r_blocks in by_report.values():
                run = 0
                for b in r_blocks:
                    if id(b) in is_sing:
                        if run:
                            total_groups += 1
                            run = 0
                    else:
                        run += 1
                        if run == MAX_WINDOWS_PER_TASK:
                            total_groups += 1
                            run = 0
                if run:
                    total_groups += 1
            if total_groups <= target_tasks:
                best_singletons = singleton_idx
                lo = mid + 1
            else:
                hi = mid - 1

        is_sing = {id(blocks[i]) for i in best_singletons}
        for r_blocks in by_report.values():
            cur_run: list[BlockItem] = []
            for b in r_blocks:
                if id(b) in is_sing:
                    if cur_run:
                        groups.append(cur_run)
                        cur_run = []
                    groups.append([b])
                else:
                    cur_run.append(b)
                    if len(cur_run) == MAX_WINDOWS_PER_TASK:
                        groups.append(cur_run)
                        cur_run = []
            if cur_run:
                groups.append(cur_run)

        # If total_groups is still slightly below target_tasks, split largest multi-block groups down!
        if len(groups) < target_tasks:
            expanded: list[list[BlockItem]] = []
            deficit = target_tasks - len(groups)
            for grp in groups:
                while deficit > 0 and len(grp) > 1:
                    expanded.append([grp[0]])
                    grp = grp[1:]
                    deficit -= 1
                expanded.append(grp)
            groups = expanded

    tasks: list[dict[str, Any]] = []
    report_seq: collections.Counter[str] = collections.Counter()
    for grp in groups:
        rep = grp[0].report
        report_seq[rep.cag_id] += 1
        seq = report_seq[rep.cag_id]
        tid = f"or1-{rep.report_year}-r{rep.cag_id}-{seq:04d}"
        windows = [
            {
                "key": f"r{rep.cag_id}-p{b.page:03d}-b{b.block.index:02d}",
                "page": b.page,
                "block_index": b.block.index,
                "line_start": b.block.line_start,
                "line_end": b.block.line_end,
            }
            for b in grp
        ]
        pages_label = (
            f"p.{grp[0].page}"
            if grp[0].page == grp[-1].page
            else f"pp.{grp[0].page}–{grp[-1].page}"
        )
        raw_title = f"{rep.report_no} ({rep.report_year}, {pages_label}): duplicate billing check"
        title = or1.neutral_text(raw_title)[:150]
        est_chars = sum(len(b.block.text) for b in grp)
        est_tokens = max(400, min(50_000, 350 + est_chars // 3))
        instructions = or1.neutral_text(
            f"Fetch the pinned CAG Union Audit Report PDF ({rep.report_no}, {rep.report_year}), "
            f"verify SHA-256 {rep.sha256[:12]}…, and run OR1 over {len(windows)} non-overlapping "
            f"section window(s) on {pages_label} to surface documented duplicate-billing or "
            "dual-agency irregularity leads while excluding legitimate cost-sharing."
        )
        tasks.append(
            {
                "id": tid,
                "title": title,
                "instructions": instructions,
                "type": "run_check",
                "dataset_key": f"cag-union-{rep.report_year}-{rep.cag_id}",
                "financial_quantity_type": "cag_report_monetary_figures",
                "methodology_code": METHODOLOGY,
                "estimated_tokens": est_tokens,
                "estimate_basis": f"100% deterministic CPU text scan (~{est_chars:,} chars across {len(windows)} block(s)).",
                "payload": {
                    "retrieval_url": rep.pdf_url,
                    "expected_sha256": rep.sha256,
                    "official_publisher": "Comptroller and Auditor General of India (cag.gov.in)",
                    "source_landing_page": rep.landing_url,
                    "artifact_title": f"{rep.report_no} ({rep.report_year})",
                    "jurisdiction": "Union of India",
                    "context": {
                        "one_road_two_bills": {
                            "report": {
                                "cag_id": rep.cag_id,
                                "report_no": rep.report_no,
                                "year": rep.report_year,
                                "title": rep.title,
                                "sector": rep.sector,
                            },
                            "windows": windows,
                            "thresholds": {"min_amount_inr": 0},
                        }
                    },
                },
            }
        )
    return tasks


def dry_run_all(tasks: list[dict[str, Any]], docs_by_id: dict[str, ReportDoc]) -> dict[str, Any]:
    records_by_id: dict[str, list[dict[str, Any]]] = {
        cid: [{"page": p, "raw_text": txt} for p, txt in sorted(doc.pages.items())]
        for cid, doc in docs_by_id.items()
    }
    tasks_with_leads = 0
    tasks_cleared = 0
    total_leads = 0
    total_unevaluable = 0
    total_excluded_cofunding = 0
    by_signal: collections.Counter[str] = collections.Counter()
    sample_leads: list[dict[str, Any]] = []
    total_inr = 0.0

    for task in tasks:
        spec = task["payload"]["context"]["one_road_two_bills"]
        cid = spec["report"]["cag_id"]
        ctx = {
            "retrieval_url": task["payload"]["retrieval_url"],
            "expected_sha256": task["payload"]["expected_sha256"],
            "source_landing_page": task["payload"]["source_landing_page"],
            "jurisdiction": "Union of India",
            "one_road_two_bills": spec,
        }
        res = run_engine(METHODOLOGY, records_by_id[cid], context=ctx)
        total_unevaluable += res.unevaluable_count
        if res.unevaluable_count:
            raise SystemExit(f"Task {task['id']} had {res.unevaluable_count} unevaluable window(s).")
        if res.leads:
            tasks_with_leads += 1
            total_leads += len(res.leads)
            for lead in res.leads:
                sig = str(lead.anomaly_metrics.get("signal_category") or "")
                by_signal[sig] += 1
                total_inr += float(lead.monetary_exposure_inr or 0.0)
                if len(sample_leads) < 20:
                    sample_leads.append(
                        {
                            "task_id": task["id"],
                            "entity": lead.entity_name,
                            "signal": sig,
                            "amount_inr": lead.monetary_exposure_inr,
                            "title": lead.title,
                            "excerpt": lead.citations[0]["excerpt"] if lead.citations else "",
                        }
                    )
        else:
            tasks_cleared += 1
        # Count excluded cofunding directly from engine meta via raw call if needed
        _leads, _clr, _mon, _sum, meta = or1.run_or1_duplicate_billing(records_by_id[cid], ctx)
        total_excluded_cofunding += int(meta.get("excluded_legitimate_cofunding") or 0)

    return {
        "tasks_with_leads": tasks_with_leads,
        "tasks_cleared": tasks_cleared,
        "total_leads": total_leads,
        "total_unevaluable": total_unevaluable,
        "excluded_legitimate_cofunding": total_excluded_cofunding,
        "by_signal": dict(sorted(by_signal.items())),
        "total_flagged_inr_cr": round(total_inr / 1e7, 2),
        "sample_leads": sample_leads,
    }


def build_project_doc(tasks: list[dict[str, Any]], coverage: dict[str, Any]) -> dict[str, Any]:
    reports_used = coverage["reports_used"]
    pages_used = coverage["pages_used"]
    blocks_total = coverage["blocks"]
    years_str = ", ".join(coverage["report_years"])
    return {
        "slug": SLUG,
        "project_code": "P4",
        "priority": 4,
        "title": "One Road, Two Bills: Union Government projects billed twice across agencies (past 2 years)",
        "summary": (
            f"Audits {reports_used} official Union Government CAG Audit Reports from the past 2 years ({years_str}; "
            f"{pages_used:,} pages, {blocks_total:,} section blocks across {len(tasks):,} checks) to find documented "
            "irregularities where a finished project, work, or expenditure was billed twice or claimed across two "
            "agencies, departments, ministries, or schemes — while excluding legitimate cost-sharing and co-funding. "
            "Every finding cites exact report, page, paragraph, and SHA-256, and requires two independent peer verifiers."
        ),
        "posted_by": {"name": "AI Watchdogs India", "url": "https://aiwatchdogs.in/projects"},
        "pillar": "audit",
        "status": "active",
        "jurisdiction": "Union of India",
        "authority": (
            "Comptroller and Auditor General of India (cag.gov.in), Union Ministries & Departments, and Public Accounts Committee (PAC)"
        ),
        "scope": [
            f"All readable Union Government CAG Audit Reports from the past 2 years ({years_str}, including late-2024 reports tabled on or after {CUTOFF_DATE.isoformat()}).",
            "Explicit duplicate-billing, double-payment, double-drawal, two-voucher/two-receipt, and same-invoice irregularities on Union projects and schemes.",
            "Dual-agency, dual-ministry, or dual-scheme claims on already-executed or overlapping public works where audit records an irregularity or recoverable excess.",
            "Strict exclusion of legitimate co-funding and cost-sharing arrangements (60:40 / 50:50 Centre-State shares, matching grants, VGF, pooled funding) unless explicit double billing occurs.",
            "Every block is evaluated deterministically from checksummed official CAG PDFs and peer-verified independently by two agents.",
        ],
        "out_of_scope": [
            "Any individual contractor employee, citizen beneficiary, or personal financial details (DPDP Act 2023).",
            "Raw PFMS / PAO payment vouchers, contractor Measurement Books, and e-Bills not yet publicly released (listed below as planned work).",
            "CAPTCHA-gated portals (eprocure.gov.in / gem.gov.in) or login-protected portals (IT Act Sec 43).",
            "State-only CAG audit reports mistagged under Union on cag.gov.in, and Union reports older than the 2-year window.",
            "Legitimate co-funded or cost-shared projects operating within sanctioned Centre-State or joint-funding guidelines.",
        ],
        "statutory_anchors": [
            "Constitution-Art-151",
            "CAG-DPC-Act-1971",
            "GFR-2017-R21",
            "GFR-2017-R62",
        ],
        "sources": [
            {
                "name": "Union Government Audit Reports (2024–2026)",
                "publisher": "Comptroller and Auditor General of India",
                "url": "https://cag.gov.in/en/audit-report?gt=50",
            },
            {
                "name": "Every CAG Union report used, with its URL and SHA-256",
                "publisher": "AI Watchdogs India, from cag.gov.in",
                "url": f"https://aiwatchdogs.in/api/missions/{SLUG}/documents.csv",
            },
            {
                "name": "Audit Para Monitoring System (APMS) & Public Accounts Committee ATRs",
                "publisher": "Controller General of Accounts & Parliament of India",
                "url": "https://apms.nic.in",
            },
        ],
        "methodology_code": METHODOLOGY,
        "tasks": tasks,
        "planned_tasks": [
            {
                "title": "Cross-match raw PFMS/PAO voucher-level disbursements once proactively published under RTI Sec 4(1)(b)",
                "type": "cross_check",
                "estimated_tokens": 25000000,
                "estimate_basis": "Pending public release of machine-readable PAO/PFMS bill-level expenditure registers across Union ministries.",
            },
            {
                "title": "Cross-match CPWD, NHAI, and PMGSY completed-work geo-coordinates against MPLADS completed works",
                "type": "cross_check",
                "estimated_tokens": 12000000,
                "estimate_basis": "Requires standardized work-ID and GIS asset registries across central executing agencies.",
            },
            {
                "title": "Audit Union CAG reports that had no PDF uploaded on cag.gov.in at build time",
                "type": "extract_data",
                "estimated_tokens": 3000000,
                "estimate_basis": f"{coverage['reports_skipped_no_pdf']} Union listings had no PDF attached on cag.gov.in yet.",
            },
        ],
    }


def dump_project(project: dict[str, Any]) -> str:
    marker = "@@TASKS@@"
    head = json.dumps({**project, "tasks": marker}, ensure_ascii=False, indent=2)
    tasks_lines = ",\n".join("    " + json.dumps(task, ensure_ascii=False, separators=(",", ":")) for task in project["tasks"])
    return head.replace(json.dumps(marker), "[\n" + tasks_lines + "\n  ]") + "\n"


def render_methodology_md(coverage: dict[str, Any]) -> str:
    return (
        "# P4 · One Road, Two Bills: Union Government duplicate billing & dual-agency claims\n\n"
        "This project audits official **Union Government Audit Reports** published by the Comptroller and "
        "Auditor General (CAG) of India over the past two years (`2024–2026`, tabled on or after "
        f"`{CUTOFF_DATE.isoformat()}`).\n\n"
        "## Why CAG Union Audit Reports\n\n"
        "Raw PFMS / PAO payment vouchers and Measurement Books are not publicly downloadable without login, "
        "and CPPP/GeM portals only publish tender/award notices behind CAPTCHAs. The CAG's published Union "
        "Audit Reports are the authoritative, publicly accessible primary record documenting cases where "
        "executed works, bills, or claims were paid twice or claimed across two agencies, departments, "
        "ministries, or schemes.\n\n"
        "## Deterministic rule (`OR1`)\n\n"
        "Implemented in [`src/aiwatchdogs/engines/one_road_two_bills.py`](../../src/aiwatchdogs/engines/one_road_two_bills.py):\n\n"
        "1. **Non-overlapping page blocks**: Each substantive PDF page is partitioned along numbered audit "
        "section headings and paragraph boundaries into non-overlapping line ranges.\n"
        "2. **Boilerplate & methodology exclusion**: Table of Contents lines, figure captions, and generic "
        "audit-criteria/objective sentences (`to verify whether...`, `prevent duplicate...`) are excluded.\n"
        "3. **Legitimate co-funding exclusion**: Standard cost-sharing and joint-funding provisions "
        "(`cost sharing`, `60:40`, `50:50`, `centre-state share`, `matching grant`, `VGF`, `pooled funding`) "
        "are explicitly cleared unless an explicit double-billing or paid-twice irregularity appears.\n"
        "4. **Two-tier signal detection**:\n"
        "   - `duplicate_payment_or_billing`: explicit double payment, duplicate billing/voucher/invoice, "
        "paid twice, billed twice, reimbursed twice, two receipts/bills/vouchers.\n"
        "   - `dual_agency_or_scheme_overlap`: two agencies/departments/ministries/schemes or already-executed "
        "overlapping works co-occurring with an audit irregularity marker and spending context.\n"
        "5. **Zero fabricated figures**: Only rupee figures explicitly printed in the matched section are cited.\n\n"
        "## Coverage\n\n"
        f"- **Reports audited:** {coverage['reports_used']} of {coverage['reports_discovered']} Union listings\n"
        f"- **Pages read:** {coverage['pages_used']:,}\n"
        f"- **Non-overlapping blocks:** {coverage['blocks']:,}\n"
        f"- **Swarm tasks:** {coverage['tasks']:,}\n"
        "- **Document index:** [`documents.csv`](./documents.csv)\n"
        "- **Coverage JSON:** [`coverage.json`](./coverage.json)\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--cache",
        type=Path,
        default=Path.home() / ".cache" / "aiwatchdogs" / SLUG,
        help="Folder holding downloaded CAG PDFs",
    )
    parser.add_argument("--download", action="store_true", help="Download missing PDFs from cag.gov.in / CloudFront")
    parser.add_argument("--out", type=Path, default=REPO / "projects", help="Projects output folder")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--target-tasks", type=int, default=TARGET_TASKS)
    parser.add_argument("--generated-on", default=date.today().isoformat())
    args = parser.parse_args()

    print("Discovering past-2-years Union CAG reports...", flush=True)
    docs, skipped = discover_union_reports(args.cache)
    print(f"Discovered {len(docs)} Union reports with PDFs ({len(skipped)} skipped at listing stage).", flush=True)

    ensure_downloads(docs, args.download)
    usable = load_all_pdfs(docs, args.workers)
    print(f"Loaded {len(usable)} readable unique Union CAG PDFs.", flush=True)
    if not usable:
        raise SystemExit("No readable Union CAG PDFs were loaded.")

    blocks = extract_all_blocks(usable)
    print(f"Extracted {len(blocks):,} non-overlapping substantive page blocks.", flush=True)
    tasks = pack_tasks(blocks, args.target_tasks)
    print(f"Packed into {len(tasks):,} tasks.", flush=True)

    docs_by_id = {d.cag_id: d for d in usable}
    dry = dry_run_all(tasks, docs_by_id)
    print("Dry-run summary:", json.dumps({k: v for k, v in dry.items() if k != "sample_leads"}, indent=2), flush=True)

    years = sorted({d.report_year for d in usable})
    by_year: dict[str, dict[str, Any]] = {}
    for yr in years:
        y_docs = [d for d in usable if d.report_year == yr]
        y_blocks = [b for b in blocks if b.report.report_year == yr]
        y_tasks = [t for t in tasks if t["payload"]["context"]["one_road_two_bills"]["report"]["year"] == yr]
        by_year[yr] = {
            "reports": len(y_docs),
            "pages": sum(d.pages_used for d in y_docs),
            "blocks": len(y_blocks),
            "tasks": len(y_tasks),
        }

    coverage = {
        "generated_on": args.generated_on,
        "cutoff_date": CUTOFF_DATE.isoformat(),
        "report_years": years,
        "reports_discovered": len(docs) + len(skipped),
        "reports_used": len(usable),
        "reports_skipped_no_pdf": sum(1 for s in skipped if s["reason"] == "no_pdf_uploaded_on_cag_portal"),
        "reports_excluded_state_mistag": sum(1 for s in skipped if s["reason"] == "state_report_mistagged_as_union"),
        "reports_unreadable_or_dup": len(docs) - len(usable),
        "pages_used": sum(d.pages_used for d in usable),
        "blocks": len(blocks),
        "tasks": len(tasks),
        "by_year": by_year,
        "dry_run": {k: v for k, v in dry.items() if k != "sample_leads"},
    }

    project = build_project_doc(tasks, coverage)
    val_errors = validate_project(project, filename_stem=SLUG)
    if val_errors:
        for err in val_errors[:25]:
            print("VALIDATION ERROR:", err, file=sys.stderr)
        raise SystemExit(f"Project validation failed with {len(val_errors)} error(s).")

    out_dir = args.out / SLUG
    out_dir.mkdir(parents=True, exist_ok=True)
    proj_path = args.out / f"{SLUG}.json"
    proj_path.write_text(dump_project(project), encoding="utf-8")
    (out_dir / "coverage.json").write_text(json.dumps(coverage, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / "METHODOLOGY.md").write_text(render_methodology_md(coverage), encoding="utf-8")

    with (out_dir / "documents.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(
            [
                "cag_id",
                "report_year",
                "report_no",
                "department",
                "sector",
                "tabled_date",
                "title",
                "landing_url",
                "pdf_url",
                "sha256",
                "bytes",
                "pages_total",
                "pages_used",
                "status",
            ]
        )
        for d in docs:
            writer.writerow(
                [
                    d.cag_id,
                    d.report_year,
                    d.report_no,
                    d.department,
                    d.sector,
                    d.tabled_date,
                    d.title,
                    d.landing_url,
                    d.pdf_url,
                    d.sha256,
                    d.byte_size,
                    d.pages_total,
                    d.pages_used,
                    d.status,
                ]
            )

    print(f"Wrote {proj_path} ({proj_path.stat().st_size / (1024 * 1024):.2f} MiB)")
    print("Sample dry-run leads:")
    for item in dry["sample_leads"][:15]:
        print(f"  [{item['signal']}] {item['task_id']} | {item['entity']} | INR {item['amount_inr']:,.0f}")
        print(f"     {item['excerpt'][:160]}")


if __name__ == "__main__":
    main()
