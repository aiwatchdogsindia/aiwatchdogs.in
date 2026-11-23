"""Lawful dataset loader for local CSV/JSON/OCDS/XLSX/ZIP/HTML/PDF files and Tier-1 Indian open-data URLs."""

from __future__ import annotations

import csv
import html
import io
import json
import os
import re
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from pypdf import PdfReader

from aiwatchdogs.core.models import LoadedDataset
from aiwatchdogs.core.safety import compute_sha256, scrub_pii

USER_AGENT = "AIWatchdogs-India-OpenAudit/0.1 (+https://aiwatchdogs.in; lawful-public-audit)"

_CAPTCHA_MARKERS: tuple[str, ...] = (
    "recaptcha",
    "hcaptcha",
    "cf-turnstile",
    "verify you are human",
    "enter captcha",
    "captcha image",
    "access denied",
)

_XLSX_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_XLSX_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

_MONTH_NUM: dict[str, int] = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}

_CORPORATE_SUFFIX_STOPWORDS = frozenset(
    {"LIMITED", "LTD", "PRIVATE", "PVT", "PUBLIC", "INDIA", "ENTERPRISES", "COMPANY", "CO"}
)


def _is_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _cache_dir() -> Path:
    return Path(os.environ.get("AIWATCHDOGS_CACHE_DIR", "/tmp/aiwatchdogs_cache"))


def _to_float_safe(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("₹", "").replace("INR", "").replace(" ", "")
    try:
        return float(text)
    except ValueError:
        return default


def _to_int_opt(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in {"--", "-", "NA", "N/A", "null", "None"}:
        return None
    try:
        return int(round(float(text.replace(",", ""))))
    except ValueError:
        return None


def _parse_datetime_flex(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    raw = str(value).strip()
    if not raw or raw in {"--", "-", "NA", "N/A"}:
        return None

    # ISO-8601 fast path
    iso_candidate = raw.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso_candidate)
        return dt.replace(tzinfo=None)
    except ValueError:
        pass

    # Common Indian portal & CivicDataLab formats
    date_formats = (
        "%d-%b-%Y %I:%M %p",
        "%d-%b-%Y %H:%M",
        "%d-%b-%Y",
        "%d/%b/%Y %I:%M %p",
        "%d/%b/%Y",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%Y-%d-%m %H:%M:%S",
        "%Y-%d-%m %H:%M",
        "%Y-%d-%m",
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y %H:%M",
        "%d-%m-%Y",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y",
    )
    for fmt in date_formats:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _parse_iso_days(start_str: Any, end_str: Any) -> int | None:
    s = _parse_datetime_flex(start_str)
    e = _parse_datetime_flex(end_str)
    if s is None or e is None:
        return None
    return max(0, (e - s).days)


def _unwrap_dict(value: Any) -> dict[str, Any]:
    """Unwrap CivicDataLab 1-element list quirks into a dict safely."""
    cur = value
    while isinstance(cur, list):
        if not cur:
            return {}
        cur = cur[0]
    return cur if isinstance(cur, dict) else {}


def _infer_jurisdiction(source_ref: str, explicit: Any = None) -> str:
    if explicit and str(explicit).strip():
        return str(explicit).strip()
    lower = source_ref.lower()
    if "assam" in lower:
        return "State:Assam"
    if "himachal" in lower or "hp_health" in lower or "hp%20health" in lower or "hptenders" in lower:
        return "State:Himachal Pradesh"
    if "andhra" in lower or "ap_child" in lower:
        return "State:Andhra Pradesh"
    if "mp-jjm" in lower or "mp_jjm" in lower or "bhopal" in lower:
        return "State:Madhya Pradesh"
    return "Union of India"


def _infer_fy_from_date(dt_val: Any, fallback: str = "FY 2024-25") -> str:
    dt = _parse_datetime_flex(dt_val)
    if dt is None:
        return fallback
    fy_start = dt.year if dt.month >= 4 else dt.year - 1
    return f"FY {fy_start}-{str(fy_start + 1)[-2:]}"


def _normalize_fy(val: Any, fallback_date: Any = None) -> str:
    text = str(val or "").strip()
    if text:
        if re.fullmatch(r"20\d{2}", text):
            y = int(text)
            return f"FY {y}-{str(y + 1)[-2:]}"
        if re.fullmatch(r"20\d{2}-\d{2,4}", text):
            return f"FY {text}"
        return text
    return _infer_fy_from_date(fallback_date)


def _flatten_ocds_release(release: dict[str, Any], package_meta: dict[str, Any]) -> dict[str, Any]:
    """Flatten an Open Contracting Data Standard (OCDS) release into a canonical procurement row.

    Handles both standard OCDS dicts and CivicDataLab 1-element list quirks
    (e.g., ``buyer``, ``tender``, ``tender.value``, ``tender.tenderPeriod``,
    ``awards[0].value``, ``bids``, ``items[0].classification``).
    """
    tender = _unwrap_dict(release.get("tender"))
    buyer = _unwrap_dict(release.get("buyer"))
    procuring_entity = _unwrap_dict(tender.get("procuringEntity"))

    raw_awards = release.get("awards") or []
    first_award = _unwrap_dict(raw_awards)
    raw_suppliers = first_award.get("suppliers") or []
    first_supplier = _unwrap_dict(raw_suppliers)
    award_val_obj = _unwrap_dict(first_award.get("value"))
    contract_period = _unwrap_dict(first_award.get("contractPeriod"))

    bids_obj = _unwrap_dict(release.get("bids"))
    bids_details = bids_obj.get("details") or []
    if not isinstance(bids_details, list):
        bids_details = []

    period = _unwrap_dict(tender.get("tenderPeriod"))
    start_date = period.get("startDate") or tender.get("datePublished")
    end_date = period.get("endDate")
    bid_window_days = _parse_iso_days(start_date, end_date)
    if bid_window_days is None and period.get("durationInDays") not in (None, ""):
        bid_window_days = _to_int_opt(period.get("durationInDays"))

    bid_count = _to_int_opt(tender.get("numberOfTenderers"))
    if bid_count is None and bids_details:
        bid_count = len(bids_details)

    tender_val_obj = _unwrap_dict(tender.get("value"))
    tender_amount_raw = tender_val_obj.get("amount")
    tender_value_inr = _to_float_safe(tender_amount_raw, 0.0)

    award_amount_raw = award_val_obj.get("amount")
    has_award = bool(first_award and award_amount_raw not in (None, ""))
    award_amount_inr = _to_float_safe(award_amount_raw, 0.0) if has_award else 0.0
    effective_award_value = award_amount_inr if has_award else tender_value_inr

    items = tender.get("items") or []
    item_category = ""
    if isinstance(items, list) and items:
        first_item = _unwrap_dict(items[0])
        item_cls = _unwrap_dict(first_item.get("classification"))
        item_category = (
            item_cls.get("description")
            or first_item.get("description")
            or ""
        )
    if not item_category:
        tender_cls = _unwrap_dict(tender.get("classification"))
        item_category = (
            tender_cls.get("description")
            or tender.get("mainProcurementCategory")
            or "Public Procurement"
        )

    source_url = (
        release.get("source_url")
        or package_meta.get("source_url")
        or "https://eprocure.gov.in"
    )
    jurisdiction = _infer_jurisdiction(
        str(source_url),
        release.get("jurisdiction") or package_meta.get("jurisdiction"),
    )
    award_date = (
        first_award.get("date")
        or contract_period.get("startDate")
        or tender.get("datePublished")
        or release.get("date")
        or ""
    )
    fiscal_year = _normalize_fy(
        release.get("fiscal_year") or package_meta.get("fiscal_year"),
        fallback_date=award_date or start_date,
    )

    return {
        "ocid": release.get("ocid") or "",
        "tender_id": tender.get("id") or release.get("ocid") or release.get("id") or "",
        "tender_title": tender.get("title") or "",
        "buyer_name": buyer.get("name") or procuring_entity.get("name") or "",
        "buyer_id": buyer.get("id") or procuring_entity.get("id") or "",
        "procurement_method": tender.get("procurementMethod") or "open",
        "bid_count": bid_count,
        "tender_start_date": start_date or "",
        "tender_end_date": end_date or "",
        "bid_window_days": bid_window_days,
        "award_id": first_award.get("id") or "",
        "award_date": award_date,
        "has_award": has_award,
        "tender_value_inr": tender_value_inr,
        "award_value_inr": effective_award_value,
        "award_amount_inr": award_amount_inr,
        "vendor_name": first_supplier.get("name") or "",
        "vendor_id": first_supplier.get("id") or "",
        "item_category": str(item_category).strip() or "Public Procurement",
        "jurisdiction": jurisdiction,
        "fiscal_year": fiscal_year,
        "source_url": str(source_url),
    }


def _is_ocds_flat_row(headers: set[str]) -> bool:
    ocds_markers = {
        "ocid",
        "buyer/name",
        "tender/id",
        "tender/value/amount",
        "tender/numberOfTenderers",
        "awards/0/value/amount",
    }
    return len(headers & ocds_markers) >= 2


def _normalize_ocds_flat_row(row: dict[str, Any], source_ref: str) -> dict[str, Any]:
    """Normalize CivicDataLab flat OCDS CSV/XLSX slash-headers into canonical engine keys."""
    out = dict(row)

    ocid = str(row.get("ocid") or "").strip()
    tender_id = str(row.get("tender/id") or ocid or row.get("id") or "").strip()
    buyer_name = str(
        row.get("buyer/name")
        or row.get("tender/procuringEntity/name")
        or row.get("parties/0/contactPoint/name")
        or ""
    ).strip()
    buyer_id = str(row.get("buyer/id") or row.get("tender/procuringEntity/id") or "").strip()

    tender_val_raw = row.get("tender/value/amount")
    tender_value_inr = _to_float_safe(tender_val_raw, 0.0)

    award_val_raw = row.get("awards/0/value/amount")
    has_award = award_val_raw not in (None, "") and _to_float_safe(award_val_raw, 0.0) > 0
    award_amount_inr = _to_float_safe(award_val_raw, 0.0) if has_award else 0.0

    start_date = str(
        row.get("tender/tenderPeriod/startDate") or row.get("tender/datePublished") or ""
    ).strip()
    end_date = str(row.get("tender/tenderPeriod/endDate") or "").strip()
    # Prefer actual tenderPeriod start/end diff over durationInDays (which in GePNIC is often bid-validity 90/180d)
    bid_window_days = _parse_iso_days(start_date, end_date)
    if bid_window_days is None and row.get("tender/tenderPeriod/durationInDays") not in (None, ""):
        bid_window_days = _to_int_opt(row.get("tender/tenderPeriod/durationInDays"))

    bid_count = _to_int_opt(row.get("tender/numberOfTenderers"))

    cls_desc = str(
        row.get("tenderclassification/description")
        or row.get("tender/classification/description")
        or ""
    ).strip()
    # CivicDataLab HP Health maps SDG narrative paragraphs (>80 chars) into tender/classification/description;
    # prefer concise tender/items/id or mainProcurementCategory when classification description is a long narrative.
    if not cls_desc or len(cls_desc) > 80:
        item_category = str(
            row.get("tender/items/id")
            or row.get("tender/mainProcurementCategory")
            or cls_desc[:80]
            or "Public Procurement"
        ).strip()
    else:
        item_category = cls_desc

    vendor_name = str(row.get("awards/0/suppliers/0/name") or "").strip()
    vendor_id = str(row.get("awards/0/suppliers/0/id") or "").strip()
    award_id = str(row.get("awards/0/id") or "").strip()
    award_date = str(
        row.get("award/date")
        or row.get("awards/0/date")
        or row.get("tender/datePublished")
        or row.get("date")
        or ""
    ).strip()
    source_url = source_ref if _is_url(source_ref) else str(row.get("source_url") or "https://eprocure.gov.in")

    out.setdefault("ocid", ocid)
    out["tender_id"] = out.get("tender_id") or tender_id
    out["tender_title"] = out.get("tender_title") or str(row.get("tender/title") or "").strip()
    out["buyer_name"] = out.get("buyer_name") or buyer_name
    out["buyer_id"] = out.get("buyer_id") or buyer_id
    out["procurement_method"] = out.get("procurement_method") or str(row.get("tender/procurementMethod") or "open").strip()
    if "bid_count" not in out or out["bid_count"] is None:
        out["bid_count"] = bid_count
    out["tender_start_date"] = out.get("tender_start_date") or start_date
    out["tender_end_date"] = out.get("tender_end_date") or end_date
    if out.get("bid_window_days") is None:
        out["bid_window_days"] = bid_window_days
    out["award_id"] = out.get("award_id") or award_id
    out["award_date"] = out.get("award_date") or award_date
    out["has_award"] = has_award
    out["tender_value_inr"] = tender_value_inr
    out["award_amount_inr"] = award_amount_inr
    # Keep award_value_inr populated cleanly (actual award if available, else tender estimate fallback)
    if has_award:
        out["award_value_inr"] = award_amount_inr
    elif tender_value_inr > 0 and not out.get("award_value_inr"):
        out["award_value_inr"] = tender_value_inr
    out["vendor_name"] = out.get("vendor_name") or vendor_name
    out["vendor_id"] = out.get("vendor_id") or vendor_id
    out["item_category"] = out.get("item_category") or item_category
    out["jurisdiction"] = out.get("jurisdiction") or _infer_jurisdiction(source_ref)
    out["fiscal_year"] = _normalize_fy(row.get("fiscal_year"), fallback_date=award_date or start_date)
    out["source_url"] = out.get("source_url") or source_url
    return out


def _infer_format(source_ref: str, fmt: str | None, content_type: str = "", raw_bytes: bytes = b"") -> str:
    if fmt:
        return fmt.lower().strip().lstrip(".")
    ct = content_type.lower()
    if "application/pdf" in ct or raw_bytes[:5] == b"%PDF-":
        return "pdf"

    parsed_path = urlparse(source_ref).path if _is_url(source_ref) else source_ref
    lower_path = parsed_path.lower()
    if lower_path.endswith(".xlsx"):
        return "xlsx"
    if lower_path.endswith(".json.zip") or lower_path.endswith(".zip"):
        return "zip"
    if lower_path.endswith(".csv"):
        return "csv"
    if lower_path.endswith(".json"):
        return "json"
    if lower_path.endswith(".pdf"):
        return "pdf"
    if lower_path.endswith((".html", ".htm", ".aspx")):
        return "html"
    if lower_path.endswith(".txt"):
        return "txt"

    if "spreadsheetml" in ct:
        return "xlsx"
    if "application/zip" in ct or raw_bytes[:4] == b"PK\x03\x04":
        return "zip"
    if "application/json" in ct or "+json" in ct:
        return "json"
    if "text/csv" in ct:
        return "csv"
    if "text/html" in ct:
        return "html"
    return "json"


def _detect_demo_sample(source_ref: str, raw_text: str, parsed_json: Any = None) -> bool:
    """Return True ONLY when explicitly marked as a synthetic sample/demo artifact.

    Real files and public URLs always default to False.
    """
    if isinstance(parsed_json, dict):
        if parsed_json.get("is_demo_sample") is True:
            return True
        badge = str(parsed_json.get("dataset_badge") or parsed_json.get("notice") or "").upper()
        if "[SAMPLE DEMO" in badge or "DEMO_SAMPLE" in badge:
            return True
    head_text = raw_text[:2000].upper()
    if "[SAMPLE DEMO DATASET]" in head_text or "SAMPLE_DEMO" in head_text or "DEMO_SAMPLE" in head_text:
        return True
    return False


def _fetch_remote_bytes(
    url: str,
    timeout: float = 60.0,
    use_cache: bool = True,
) -> tuple[bytes, str]:
    cache_enabled = use_cache and os.environ.get("AIWATCHDOGS_DISABLE_CACHE") != "1"
    url_hash = compute_sha256(url)
    cdir = _cache_dir()
    data_cache = cdir / f"{url_hash}.bin"
    meta_cache = cdir / f"{url_hash}.meta.json"

    if cache_enabled and data_cache.exists() and meta_cache.exists():
        try:
            meta = json.loads(meta_cache.read_text(encoding="utf-8"))
            cached_bytes = data_cache.read_bytes()
            if meta.get("url") == url and len(cached_bytes) > 0:
                return cached_bytes, str(meta.get("content_type") or "")
        except (OSError, ValueError):
            pass

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/csv, application/pdf, application/zip, application/vnd.openxmlformats-officedocument.spreadsheetml.sheet, text/html;q=0.9, text/plain;q=0.8, */*;q=0.5",
    }
    with httpx.Client(headers=headers, timeout=timeout, follow_redirects=True) as client:
        response = client.get(url)
        if response.status_code in {401, 403}:
            raise PermissionError(
                f"Remote source {url} returned HTTP {response.status_code}. "
                "AI Watchdogs does not bypass authentication walls or access controls (IT Act Sec 43)."
            )
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        raw_bytes = response.content

        # Refuse to scrape or bypass CAPTCHA/anti-bot HTML pages.
        if "text/html" in content_type.lower() or raw_bytes[:512].lstrip().lower().startswith((b"<!doctype html", b"<html")):
            snippet = raw_bytes[:8192].decode("utf-8", errors="ignore").lower()
            for marker in _CAPTCHA_MARKERS:
                if marker in snippet:
                    raise PermissionError(
                        f"Remote source {url} is gated by a CAPTCHA/anti-bot check ('{marker}'). "
                        "Per IT Act Sec 43 policy, please download the official file manually and audit locally."
                    )

    if cache_enabled and len(raw_bytes) > 0:
        try:
            cdir.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(prefix=f"{url_hash}_", dir=str(cdir))
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(raw_bytes)
                os.replace(tmp_path, data_cache)
            finally:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
            meta_payload = json.dumps(
                {
                    "url": url,
                    "content_type": content_type,
                    "sha256": compute_sha256(raw_bytes),
                    "byte_size": len(raw_bytes),
                }
            )
            fd_m, tmp_meta = tempfile.mkstemp(prefix=f"{url_hash}_meta_", dir=str(cdir))
            try:
                with os.fdopen(fd_m, "w", encoding="utf-8") as fh:
                    fh.write(meta_payload)
                os.replace(tmp_meta, meta_cache)
            finally:
                if os.path.exists(tmp_meta):
                    os.unlink(tmp_meta)
        except OSError:
            pass

    return raw_bytes, content_type


def _sniff_csv_delimiter(header_line: str) -> str:
    """Detect ';' vs ',' delimiter from header line (supports real NREGA & MPLADS CSVs)."""
    semi = header_line.count(";")
    comma = header_line.count(",")
    if semi >= 2 and semi > comma:
        return ";"
    return ","


def _parse_csv_bytes(raw_bytes: bytes, source_ref: str) -> tuple[list[dict[str, Any]], str]:
    text = raw_bytes.decode("utf-8-sig", errors="replace")
    data_lines = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    first_non_empty = next((ln for ln in data_lines if ln.strip()), "")
    delimiter = _sniff_csv_delimiter(first_non_empty)

    reader = csv.DictReader(io.StringIO("\n".join(data_lines)), delimiter=delimiter)
    fieldnames = {str(h).strip() for h in (reader.fieldnames or []) if h is not None and str(h).strip()}
    is_ocds = _is_ocds_flat_row(fieldnames)

    records: list[dict[str, Any]] = []
    for row in reader:
        cleaned_row = {
            str(k).strip(): (v.strip() if isinstance(v, str) else v)
            for k, v in row.items()
            if k is not None and str(k).strip() != ""
        }
        if not any(str(v or "").strip() for v in cleaned_row.values()):
            continue
        if is_ocds:
            cleaned_row = _normalize_ocds_flat_row(cleaned_row, source_ref)
        elif _is_url(source_ref):
            cleaned_row.setdefault("source_url", source_ref)
        records.append(cleaned_row)
    return records, text[:8192]


def _read_xlsx_sheet(
    zf: zipfile.ZipFile,
    sheet_path: str,
    shared_strings: list[str],
) -> list[dict[str, Any]]:
    """Stream-parse a single OOXML worksheet XML handling sharedStrings AND inline <is> cells."""
    row_tag = f"{{{_XLSX_MAIN_NS}}}row"
    c_tag = f"{{{_XLSX_MAIN_NS}}}c"
    v_tag = f"{{{_XLSX_MAIN_NS}}}v"
    is_tag = f"{{{_XLSX_MAIN_NS}}}is"
    t_tag = f"{{{_XLSX_MAIN_NS}}}t"

    headers_by_col: dict[str, str] = {}
    records: list[dict[str, Any]] = []

    with zf.open(sheet_path) as sf:
        for _, elem in ET.iterparse(sf, events=("end",)):
            if elem.tag != row_tag:
                continue
            row_vals: dict[str, str] = {}
            for cell in elem.findall(c_tag):
                ref = cell.attrib.get("r", "")
                col = "".join(ch for ch in ref if ch.isalpha())
                if not col:
                    continue
                ctype = cell.attrib.get("t", "")
                v_el = cell.find(v_tag)
                is_el = cell.find(is_tag)
                val = ""
                if ctype == "s" and v_el is not None and v_el.text is not None:
                    try:
                        val = shared_strings[int(v_el.text)]
                    except (ValueError, IndexError):
                        val = v_el.text
                elif ctype == "inlineStr" and is_el is not None:
                    val = "".join(t.text or "" for t in is_el.iter(t_tag))
                elif v_el is not None and v_el.text is not None:
                    val = v_el.text
                elif is_el is not None:
                    val = "".join(t.text or "" for t in is_el.iter(t_tag))
                row_vals[col] = val.strip()

            if not headers_by_col:
                if any(row_vals.values()):
                    headers_by_col = {col: hdr for col, hdr in row_vals.items() if hdr}
            else:
                rec = {
                    hdr: row_vals.get(col, "")
                    for col, hdr in headers_by_col.items()
                }
                if any(rec.values()):
                    records.append(rec)
            elem.clear()
    return records


def _parse_xlsx_bytes(raw_bytes: bytes, source_ref: str) -> tuple[list[dict[str, Any]], str, str]:
    """Pure-stdlib .xlsx reader supporting sharedStrings, inline <is>, and OCDS tenders+awards auto-join."""
    with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
        names = set(zf.namelist())
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in names:
            sst_root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            si_tag = f"{{{_XLSX_MAIN_NS}}}si"
            t_tag = f"{{{_XLSX_MAIN_NS}}}t"
            for si in sst_root.findall(si_tag):
                shared_strings.append("".join(t.text or "" for t in si.iter(t_tag)))

        wb_root = ET.fromstring(zf.read("xl/workbook.xml"))
        rels_root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        rid_to_target: dict[str, str] = {}
        for rel in rels_root:
            rid = rel.attrib.get("Id")
            target = rel.attrib.get("Target", "")
            if rid and target:
                norm_target = target.lstrip("/")
                if not norm_target.startswith("xl/"):
                    norm_target = "xl/" + norm_target
                rid_to_target[rid] = norm_target

        sheets: list[tuple[str, str]] = []
        sheet_tag = f"{{{_XLSX_MAIN_NS}}}sheet"
        rid_attr = f"{{{_XLSX_REL_NS}}}id"
        for sh in wb_root.findall(f".//{sheet_tag}"):
            sname = sh.attrib.get("name", "")
            rid = sh.attrib.get(rid_attr, "")
            target = rid_to_target.get(rid, "")
            if target and target in names:
                sheets.append((sname, target))

        sheet_map = {name.strip().lower(): path for name, path in sheets}

        # Auto-join tenders + awards sheets on ocid (e.g. HP Health OCDS workbook)
        if "tenders" in sheet_map and "awards" in sheet_map:
            tenders = _read_xlsx_sheet(zf, sheet_map["tenders"], shared_strings)
            awards = _read_xlsx_sheet(zf, sheet_map["awards"], shared_strings)

            tenders_by_ocid: dict[str, dict[str, Any]] = {}
            for t in tenders:
                ocid = str(t.get("ocid") or "").strip()
                if ocid:
                    tenders_by_ocid[ocid] = t

            matched_ocids: set[str] = set()
            joined: list[dict[str, Any]] = []
            # Award columns to overlay without clobbering buyer/tender parties columns
            for aw in awards:
                ocid = str(aw.get("ocid") or "").strip()
                base = dict(tenders_by_ocid.get(ocid, {}))
                if ocid and ocid in tenders_by_ocid:
                    matched_ocids.add(ocid)
                for k, v in aw.items():
                    if not v:
                        continue
                    if k.startswith("awards/") or k in {"award/date", "ocid"} or k not in base or not base.get(k):
                        if k.startswith("parties/") and base.get(k):
                            continue
                        base[k] = v
                joined.append(_normalize_ocds_flat_row(base, source_ref))

            for t in tenders:
                ocid = str(t.get("ocid") or "").strip()
                if ocid not in matched_ocids:
                    joined.append(_normalize_ocds_flat_row(dict(t), source_ref))

            preview = f"XLSX OCDS workbook: {len(tenders)} tenders, {len(awards)} awards, {len(joined)} joined rows"
            return joined, preview, "xlsx_ocds"

        # Single-sheet or primary-sheet workbook (e.g., Assam OBDS Expenditure)
        records: list[dict[str, Any]] = []
        chosen_sheet = ""
        for sname, spath in sheets:
            rows = _read_xlsx_sheet(zf, spath, shared_strings)
            if rows:
                records = rows
                chosen_sheet = sname
                break

        if records:
            headers = set(records[0].keys())
            if _is_ocds_flat_row(headers):
                records = [_normalize_ocds_flat_row(r, source_ref) for r in records]
                return records, f"XLSX OCDS sheet '{chosen_sheet}': {len(records)} rows", "xlsx_ocds"
            if _is_url(source_ref):
                for r in records:
                    r.setdefault("source_url", source_ref)

        return records, f"XLSX sheet '{chosen_sheet}': {len(records)} rows", "xlsx"


def _parse_json_bytes(raw_bytes: bytes, source_ref: str = "") -> tuple[list[dict[str, Any]], str, Any, str]:
    text = raw_bytes.decode("utf-8-sig", errors="replace")
    payload = json.loads(text)
    detected_format = "json"
    preview = text[:8192]

    if isinstance(payload, list):
        records = [r for r in payload if isinstance(r, dict)]
        return records, preview, payload, detected_format

    if isinstance(payload, dict):
        # 1. OCDS release package (e.g. CivicDataLab Assam / HP public procurement)
        if isinstance(payload.get("releases"), list):
            detected_format = "ocds_json"
            publisher = payload.get("publisher")
            pub_name = publisher.get("name") if isinstance(publisher, dict) else None
            package_meta = {
                "jurisdiction": payload.get("jurisdiction") or _infer_jurisdiction(source_ref, pub_name),
                "fiscal_year": payload.get("fiscal_year"),
                "source_url": payload.get("uri") or payload.get("source_url") or (source_ref if _is_url(source_ref) else None),
            }
            records = [
                _flatten_ocds_release(rel, package_meta)
                for rel in payload["releases"]
                if isinstance(rel, dict)
            ]
            return records, preview, payload, detected_format

        # 2. OpenBudgetsIndia CKAN Action API response
        if payload.get("success") is True and isinstance(payload.get("result"), dict):
            result_obj = payload["result"]
            if isinstance(result_obj.get("records"), list):
                return [r for r in result_obj["records"] if isinstance(r, dict)], preview, payload, "ckan_json"
            if isinstance(result_obj.get("results"), list):
                return [r for r in result_obj["results"] if isinstance(r, dict)], preview, payload, "ckan_json"

        # 3. Generic record container (data.gov.in OGD API, JSON packages, etc.)
        for key in ("records", "data", "items", "paras", "tenders", "rows"):
            if isinstance(payload.get(key), list):
                return [r for r in payload[key] if isinstance(r, dict)], preview, payload, detected_format

        return [payload], preview, payload, detected_format

    return [], preview, payload, detected_format


def _parse_zip_bytes(raw_bytes: bytes, source_ref: str) -> tuple[list[dict[str, Any]], str, Any, str]:
    """Pure-stdlib .json.zip / .zip reader (also auto-detects .xlsx zipped OOXML workbooks)."""
    with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
        members = [
            name
            for name in zf.namelist()
            if not name.endswith("/")
            and not name.startswith("__MACOSX/")
            and not Path(name).name.startswith("._")
        ]
        if "xl/workbook.xml" in members:
            recs, preview, fmt = _parse_xlsx_bytes(raw_bytes, source_ref)
            return recs, preview, None, fmt

        json_members = [m for m in members if m.lower().endswith(".json")]
        if json_members:
            inner = zf.read(json_members[0])
            return _parse_json_bytes(inner, source_ref=source_ref)

        csv_members = [m for m in members if m.lower().endswith(".csv")]
        if csv_members:
            inner = zf.read(csv_members[0])
            recs, preview = _parse_csv_bytes(inner, source_ref=source_ref)
            return recs, preview, None, "csv"

    return [], "", None, "zip"


def _strip_html_cell(fragment: str) -> str:
    no_tags = re.sub(r"<[^>]+>", " ", fragment)
    unescaped = html.unescape(no_tags).replace("\xa0", " ")
    return " ".join(unescaped.split()).strip()


def _parse_html_bytes(raw_bytes: bytes, source_ref: str) -> tuple[list[dict[str, Any]], str, str]:
    """Extract structured fiscal/accounts tables from official .nic.in HTML pages (e.g. CGA Monthly Accounts)."""
    text = raw_bytes.decode("utf-8", errors="replace")
    tr_blocks = re.findall(r"<tr\b[^>]*>(.*?)</tr>", text, re.IGNORECASE | re.DOTALL)

    rows_cells: list[list[str]] = []
    for tr in tr_blocks:
        cells = [
            _strip_html_cell(td)
            for td in re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", tr, re.IGNORECASE | re.DOTALL)
        ]
        if any(cells):
            rows_cells.append(cells)

    # Detect CGA Monthly Accounts at a Glance using tag-stripped table header text
    header_probe = " ".join(" ".join(r) for r in rows_cells[:25])
    fy_match = re.search(
        r"Budget\s+Estimates\s+(20\d{2})\s*[-–]\s*(20\d{2}|\d{2})",
        header_probe,
        re.IGNORECASE,
    )
    fy_label = f"FY {fy_match.group(1)}-{fy_match.group(2)[-2:]}" if fy_match else "FY 2024-25"

    upto_match = re.search(
        r"Actuals[@\s]*upto\s+([A-Za-z]+)[,\s]+(20\d{2})",
        header_probe,
        re.IGNORECASE,
    )
    months_elapsed_fy = 12
    report_month_label = "March"
    if upto_match:
        m_key = upto_match.group(1).strip().upper()[:3]
        cal_month = _MONTH_NUM.get(m_key, 3)
        report_month_label = f"{upto_match.group(1).title()} {upto_match.group(2)}"
        # Indian FY: April=1 .. December=9, January=10, February=11, March=12
        months_elapsed_fy = (cal_month - 3) if cal_month >= 4 else (cal_month + 9)

    is_cga = "cga" in source_ref.lower() or (
        bool(fy_match) and any("Revenue Expenditure" in " ".join(r) for r in rows_cells)
    )
    source_url = source_ref if _is_url(source_ref) else "https://cga.nic.in"

    if is_cga:
        cga_records: list[dict[str, Any]] = []
        for idx, cells in enumerate(rows_cells):
            if len(cells) < 4:
                continue
            # Identify description cell (first substantial alphabetic cell, skipping "(Details)" & numeric Sl.No)
            desc = ""
            desc_idx = -1
            for ci, cell in enumerate(cells[:3]):
                if re.search(r"[A-Za-z]{3,}", cell) and cell.lower() not in {"(details)", "rs.", "current", "coppy**"}:
                    desc = cell
                    desc_idx = ci
                    break
            if not desc or desc.lower().startswith(("budget estimates", "actuals")):
                continue

            # Extract numeric tokens after description: BE, Actual, optional COPPY amount, % Actual/BE, (COPPY %)
            trailing = cells[desc_idx + 1 :]
            num_vals: list[float] = []
            pct_vals: list[float] = []
            for cell in trailing:
                c = cell.strip()
                if not c or c.lower() == "(details)":
                    continue
                if "%" in c:
                    cleaned_pct = c.replace("(", "").replace(")", "").replace("%", "").replace(" ", "")
                    try:
                        pct_vals.append(float(cleaned_pct))
                    except ValueError:
                        pass
                else:
                    cleaned_num = c.replace(",", "").replace(" ", "")
                    if re.fullmatch(r"-?\d+(?:\.\d+)?", cleaned_num):
                        num_vals.append(float(cleaned_num))

            if len(num_vals) < 2:
                continue
            be_cr = num_vals[0]
            actual_cr = num_vals[1]
            actual_pct = pct_vals[0] if pct_vals else ((actual_cr / be_cr * 100.0) if be_cr else 0.0)
            coppy_pct = pct_vals[1] if len(pct_vals) >= 2 else None

            cga_records.append(
                {
                    "record_type": "cga_monthly_account",
                    "demand_no": f"CGA-{cells[0] if cells[0] and cells[0] != '.' else idx + 1}",
                    "ministry_name": desc,
                    "entity_name": desc,
                    "be_crore": be_cr,
                    "re_crore": be_cr,
                    "actual_crore": actual_cr,
                    "actual_to_be_pct": round(actual_pct, 2),
                    "coppy_pct": round(coppy_pct, 2) if coppy_pct is not None else None,
                    "months_elapsed_fy": months_elapsed_fy,
                    "report_period": report_month_label,
                    "fiscal_year": fy_label,
                    "jurisdiction": "Union of India",
                    "source_url": source_url,
                }
            )
        if cga_records:
            return cga_records, text[:8192], "html_table"

    # Generic HTML table parser fallback (first row with >=3 cells as headers)
    generic_records: list[dict[str, Any]] = []
    headers: list[str] = []
    for cells in rows_cells:
        if not headers and len(cells) >= 3 and sum(1 for c in cells if re.search(r"[A-Za-z]", c)) >= 2:
            headers = [c or f"col_{i}" for i, c in enumerate(cells)]
            continue
        if headers and len(cells) == len(headers):
            row_dict = {headers[i]: cells[i] for i in range(len(headers))}
            row_dict.setdefault("source_url", source_url)
            generic_records.append(row_dict)

    return generic_records, text[:8192], "html_table"


def _parse_pdf_bytes(raw_bytes: bytes, source_ref: str) -> tuple[list[dict[str, Any]], str]:
    reader = PdfReader(io.BytesIO(raw_bytes))
    records: list[dict[str, Any]] = []
    all_pages: list[str] = []
    for page_idx, page in enumerate(reader.pages, start=1):
        page_text = (page.extract_text() or "").strip()
        all_pages.append(page_text)
        records.append(
            {
                "page": page_idx,
                "raw_text": page_text,
                "source_ref": source_ref,
                "source_url": source_ref if _is_url(source_ref) else "https://cag.gov.in",
                "jurisdiction": _infer_jurisdiction(source_ref),
            }
        )
    return records, "\n\n".join(all_pages)


def normalize_company_name(name: str) -> tuple[str, str]:
    """Return (strict_normalized_name, core_normalized_name) for corporate registry matching."""
    upper = (name or "").upper()
    upper = re.sub(r"^\s*M/S\.?\s+", "", upper)
    upper = upper.replace("&", " AND ")
    upper = upper.replace("'S", "S").replace("’S", "S")
    upper = re.sub(r"[^A-Z0-9\s]", " ", upper)
    replacements = {
        "LTD": "LIMITED",
        "PVT": "PRIVATE",
        "CORPN": "CORPORATION",
        "CORP": "CORPORATION",
        "IND": "INDUSTRIES",
        "INDS": "INDUSTRIES",
        "PHARM": "PHARMACEUTICALS",
        "PHARMA": "PHARMACEUTICALS",
        "LABS": "LABORATORIES",
        "MFG": "MANUFACTURING",
        "CO": "COMPANY",
    }
    tokens = [replacements.get(tok, tok) for tok in upper.split()]
    strict = " ".join(tokens)
    core_tokens = [tok for tok in tokens if tok not in _CORPORATE_SUFFIX_STOPWORDS]
    core = " ".join(core_tokens) if core_tokens else strict
    return strict, core


def _build_csr_lookup(secondary_records: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Aggregate MCA CSR records (either top-100 summary or project-level rows) by company name."""
    by_cin: dict[str, dict[str, Any]] = {}
    for row in secondary_records:
        cname = str(row.get("company_name") or row.get("Company Name") or "").strip()
        cin = str(row.get("cin") or row.get("CIN") or "").strip().upper()
        if not cname and not cin:
            continue
        key = cin or cname.upper()
        entry = by_cin.get(key)
        if entry is None:
            entry = {
                "cin": cin,
                "company_name": cname,
                "csr_total_inr": 0.0,
                "csr_rank": _to_int_opt(row.get("csr_total_rank")),
                "csr_projects_count": 0,
            }
            by_cin[key] = entry

        if row.get("csr_total") not in (None, ""):
            # MCA Top-100 summary CSV reports csr_total in INR Crore
            entry["csr_total_inr"] = max(entry["csr_total_inr"], _to_float_safe(row.get("csr_total")) * 10_000_000.0)
        elif row.get("spent") not in (None, "") or row.get("outlay") not in (None, ""):
            # Project-level MCA CSR results CSV reports spent/outlay directly in INR
            amt = _to_float_safe(row.get("spent"), 0.0)
            if amt <= 0:
                amt = _to_float_safe(row.get("outlay"), 0.0)
            entry["csr_total_inr"] += max(0.0, amt)
            entry["csr_projects_count"] += 1

    strict_map: dict[str, dict[str, Any]] = {}
    core_map: dict[str, dict[str, Any]] = {}
    for entry in by_cin.values():
        strict, core = normalize_company_name(entry["company_name"])
        if strict:
            strict_map[strict] = entry
        if core and len(core) >= 6:
            core_map.setdefault(core, entry)
    return strict_map, core_map


def _join_secondary_records(
    primary_records: list[dict[str, Any]],
    secondary_records: list[dict[str, Any]],
    secondary_ref: str,
    secondary_sha256: str,
) -> list[dict[str, Any]]:
    """Enrich primary records (e.g. Electoral Bonds) with secondary registry rows (e.g. MCA CSR Top-100)."""
    if not secondary_records:
        return primary_records

    sample_sec = secondary_records[0]
    has_csr_fields = any(
        k in sample_sec for k in ("cin", "CIN", "csr_total", "csr_total_rank", "company_name", "Company Name")
    )
    if not has_csr_fields:
        return primary_records

    strict_map, core_map = _build_csr_lookup(secondary_records)
    enriched: list[dict[str, Any]] = []
    for rec in primary_records:
        purchaser = str(
            rec.get("purchaser_name")
            or rec.get("Name of the Purchaser")
            or rec.get("vendor_name")
            or rec.get("company_name")
            or ""
        ).strip()
        if purchaser:
            strict, core = normalize_company_name(purchaser)
            match = strict_map.get(strict) or (core_map.get(core) if len(core) >= 6 else None)
            if match is not None:
                updated = dict(rec)
                updated["cin"] = match["cin"]
                updated["csr_company_name"] = match["company_name"]
                updated["csr_total_inr"] = round(match["csr_total_inr"], 2)
                updated["csr_total_crore"] = round(match["csr_total_inr"] / 10_000_000.0, 4)
                if match.get("csr_rank") is not None:
                    updated["csr_rank"] = match["csr_rank"]
                updated["secondary_source_url"] = secondary_ref
                updated["secondary_sha256"] = secondary_sha256
                enriched.append(updated)
                continue
        enriched.append(rec)
    return enriched


def _fast_scrub_records(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Scrub all string values in records via scrub_pii while skipping strings that cannot contain PII.

    Every DPDP/Aadhaar PII pattern in ``scrub_pii`` requires either a digit (0-9) or '@',
    and at least 6 characters. Skipping pure alphabetic short strings yields identical redactions
    while keeping 40,000+ row real datasets blazing fast.
    """
    redactions: list[str] = []

    def _walk(item: Any) -> Any:
        if isinstance(item, str):
            if len(item) >= 6 and ("@" in item or any("0" <= ch <= "9" for ch in item)):
                cleaned, tags = scrub_pii(item)
                for tag in tags:
                    if tag not in redactions:
                        redactions.append(tag)
                return cleaned
            return item
        if isinstance(item, list):
            return [_walk(v) for v in item]
        if isinstance(item, dict):
            return {k: _walk(v) for k, v in item.items()}
        return item

    return [_walk(r) for r in records], redactions


def _apply_context_filters(
    records: list[dict[str, Any]],
    context: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not context:
        return records

    filtered = records
    buyer_filter = str(context.get("filter_buyer") or "").strip().lower()
    if buyer_filter:
        filtered = [
            rec
            for rec in filtered
            if buyer_filter
            in str(
                rec.get("buyer_name")
                or rec.get("buyer/name")
                or rec.get("entity_name")
                or rec.get("ministry_name")
                or rec.get("department")
                or ""
            ).lower()
        ]

    max_rows_raw = context.get("max_rows")
    if max_rows_raw is not None:
        max_rows = _to_int_opt(max_rows_raw)
        if max_rows is not None and max_rows > 0:
            filtered = filtered[:max_rows]

    return filtered


def load_dataset(
    path_or_url: str,
    fmt: str | None = None,
    context: dict[str, Any] | None = None,
) -> LoadedDataset:
    """Load, hash, detect demo status, and PII-scrub a local file or Tier-1 public URL.

    Supports CSV (with `;` / `,` delimiter sniffing & OCDS flat-header normalization),
    JSON (including CivicDataLab OCDS release packages and OpenBudgetsIndia CKAN envelopes),
    `.json.zip` / `.zip` archives, `.xlsx` workbooks (sharedStrings + inline `<is>` + OCDS
    `tenders`/`awards` auto-join), HTML tables (e.g. live `cga.nic.in` accounts), PDF (via
    ``pypdf``), and plain text.

    Optional ``context`` keys:
    - ``use_cache`` (bool, default True): cache HTTP downloads in ``/tmp/aiwatchdogs_cache/<sha>``
    - ``secondary_url`` / ``secondary_path``: secondary dataset to join (e.g. MCA CSR Top-100 for M8)
    - ``filter_buyer``: case-insensitive buyer/ministry substring filter
    - ``max_rows``: optional cap on returned records
    """
    ctx = context or {}
    source_ref = str(path_or_url).strip()
    content_type = ""
    use_cache = bool(ctx.get("use_cache", True))

    if _is_url(source_ref):
        raw_bytes, content_type = _fetch_remote_bytes(source_ref, use_cache=use_cache)
    else:
        path = Path(source_ref)
        if not path.exists():
            raise FileNotFoundError(f"Dataset path does not exist: {source_ref}")
        raw_bytes = path.read_bytes()

    sha256 = compute_sha256(raw_bytes)
    byte_size = len(raw_bytes)
    resolved_fmt = _infer_format(source_ref, fmt, content_type, raw_bytes)

    parsed_json: Any = None
    if resolved_fmt == "csv":
        records, raw_text = _parse_csv_bytes(raw_bytes, source_ref)
    elif resolved_fmt == "xlsx":
        records, raw_text, resolved_fmt = _parse_xlsx_bytes(raw_bytes, source_ref)
    elif resolved_fmt == "zip":
        records, raw_text, parsed_json, resolved_fmt = _parse_zip_bytes(raw_bytes, source_ref)
    elif resolved_fmt == "json":
        records, raw_text, parsed_json, resolved_fmt = _parse_json_bytes(raw_bytes, source_ref)
    elif resolved_fmt == "html":
        records, raw_text, resolved_fmt = _parse_html_bytes(raw_bytes, source_ref)
    elif resolved_fmt == "pdf":
        records, raw_text = _parse_pdf_bytes(raw_bytes, source_ref)
    else:
        raw_text = raw_bytes.decode("utf-8-sig", errors="replace")
        stripped = raw_text.lstrip()
        if stripped.startswith("{") or stripped.startswith("["):
            records, raw_text, parsed_json, resolved_fmt = _parse_json_bytes(raw_bytes, source_ref)
        else:
            records = [{"raw_text": raw_text, "page": 1, "source_ref": source_ref}]

    # Optional secondary dataset join (e.g., M8 Electoral Bonds x MCA Top-100 CSR)
    secondary_ref = str(
        ctx.get("secondary_url") or ctx.get("secondary_dataset_url") or ctx.get("secondary_path") or ""
    ).strip()
    if secondary_ref and secondary_ref != source_ref:
        sec_ds = load_dataset(secondary_ref, context={"use_cache": use_cache})
        records = _join_secondary_records(records, sec_ds.records, secondary_ref, sec_ds.sha256)

    records = _apply_context_filters(records, ctx)
    is_demo = _detect_demo_sample(source_ref, raw_text, parsed_json)

    # Mandatory pre-storage PII scrubbing per DPDP Act 2023 & Aadhaar Act 2016
    scrubbed_records, rec_redactions = _fast_scrub_records(records)
    scrubbed_raw_text, text_redactions = scrub_pii(raw_text)

    combined_redactions: list[str] = []
    for tag in rec_redactions + text_redactions:
        if tag not in combined_redactions:
            combined_redactions.append(tag)

    return LoadedDataset(
        records=scrubbed_records,
        raw_text=scrubbed_raw_text,
        sha256=sha256,
        byte_size=byte_size,
        format=resolved_fmt,
        is_demo_sample=is_demo,
        source_ref=source_ref,
        pii_redactions=combined_redactions,
    )
