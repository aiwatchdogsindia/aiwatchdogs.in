"""Lawful dataset loader for local CSV/JSON/OCDS/XLSX/ZIP/HTML/PDF files and Tier-1 Indian open-data URLs.

Defensive defaults for unattended execution:
- ``allow_local_files`` defaults to ``False`` (explicit opt-in required for local filesystem paths).
- Remote URLs must use HTTPS on public hosts (blocking localhost, private/loopback/link-local/metadata IPs).
- Expected primary and secondary SHA-256 digests fail closed via ``DigestMismatchError``.
- Raw disk caching is opt-in, uses ``0o700``/``0o600`` permissions, revalidates SHA-256 on hit,
  and refuses to persist raw bytes when PII is detected.
- Bounded download size, ZIP/XLSX decompression size & ratio limits, XML DTD rejection, and parse caps.
"""

from __future__ import annotations

import csv
import html
import io
import ipaddress
import json
import os
import re
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from pypdf import PdfReader

from aiwatchdogs.core.models import LoadedDataset
from aiwatchdogs.core.safety import compute_sha256, scrub_pii

# Identifies the project honestly. The contact is written as a bare domain: some government
# web firewalls (e.g. indiabudget.gov.in) reject any User-Agent that embeds an "https://" URL.
USER_AGENT = "AIWatchdogs-India-OpenAudit/0.1 (aiwatchdogs.in; lawful-public-audit)"

DEFAULT_MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024  # 100 MiB
DEFAULT_MAX_DECOMPRESSED_BYTES = 250 * 1024 * 1024  # 250 MiB
DEFAULT_MAX_COMPRESSION_RATIO = 100.0
DEFAULT_MAX_ZIP_MEMBERS = 500
DEFAULT_MAX_PARSE_ROWS = 250_000
DEFAULT_MAX_PDF_PAGES = 1000
DEFAULT_CACHE_TTL_SECONDS = 3600

_ORIGINAL_HTTPX_GET = httpx.Client.get

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

_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "metadata.google.internal",
        "metadata",
        "instance-data",
    }
)


class DigestMismatchError(ValueError):
    """Raised when observed artifact bytes do not match an expected SHA-256 digest."""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _is_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _validate_public_https_url(url: str) -> None:
    """Enforce HTTPS and reject private/loopback/link-local/metadata hosts for unattended loading."""
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https":
        raise PermissionError(
            f"Unattended remote loading requires HTTPS URLs; refused scheme '{parsed.scheme}' in {url!r}."
        )
    if parsed.username or parsed.password:
        raise PermissionError(f"Credentials embedded in URL are disallowed: {url!r}.")
    hostname = (parsed.hostname or "").strip().lower().rstrip(".")
    if not hostname:
        raise PermissionError(f"URL missing valid public hostname: {url!r}.")
    if hostname in _BLOCKED_HOSTNAMES or hostname.endswith((".localhost", ".local", ".internal")):
        raise PermissionError(f"Refused non-public/internal hostname '{hostname}' in {url!r}.")
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        ip = None
    if ip is not None and not ip.is_global:
        raise PermissionError(
            f"Refused non-public IP address '{hostname}' (loopback/private/link-local/reserved) in {url!r}."
        )


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
    if isinstance(value, bool):
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

    iso_candidate = raw.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso_candidate)
        return dt.replace(tzinfo=None)
    except ValueError:
        pass

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


def _infer_fy_from_date(dt_val: Any, fallback: str = "Unspecified") -> str:
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
    return _infer_fy_from_date(fallback_date, fallback="Unspecified")


def _extract_bid_opening_date_from_row(row: dict[str, Any]) -> str:
    for key in (
        "bid_opening_date",
        "tender/bidOpening/date",
        "tender/bidopening/date",
        "bidOpening/date",
        "tender/milestones/0/dueDate",
        "tender/tenderPeriod/bidOpeningDate",
    ):
        val = row.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    for k, v in row.items():
        if isinstance(k, str) and "bidopen" in k.lower().replace("_", "") and v not in (None, ""):
            return str(v).strip()
    return ""


def _flatten_ocds_release(
    release: dict[str, Any],
    package_meta: dict[str, Any],
    row_index: int = 1,
) -> dict[str, Any]:
    """Flatten an Open Contracting Data Standard (OCDS) release into a canonical procurement row.

    Strictly distinguishes:
    - ``release_date`` (OCDS package/release date)
    - ``tender_published_date`` / ``tender_start_date``
    - ``bid_opening_date``
    - ``award_date`` (ONLY genuine award/contract dates — never release.date!)
    - ``tender_estimate_inr`` (alias ``tender_value_inr``) vs ``award_value_inr`` (ONLY real awards!)
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
    tender_published_date = str(tender.get("datePublished") or "").strip()
    start_date = str(period.get("startDate") or tender_published_date or "").strip()
    end_date = str(period.get("endDate") or "").strip()
    bid_opening_obj = _unwrap_dict(tender.get("bidOpening"))
    bid_opening_date = str(bid_opening_obj.get("date") or tender.get("bidOpeningDate") or "").strip()
    release_date = str(release.get("date") or "").strip()

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
    has_award = bool(first_award and award_amount_raw not in (None, "") and _to_float_safe(award_amount_raw, 0.0) > 0)
    award_amount_inr: float | None = _to_float_safe(award_amount_raw, 0.0) if has_award else None

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
    # Strictly preserve real award/contract dates ONLY; never map release.date or datePublished into award_date.
    award_date = str(
        first_award.get("date")
        or contract_period.get("startDate")
        or ""
    ).strip()

    # Infer FY from real procurement event dates, never from OCDS export release_date.
    fiscal_year = _normalize_fy(
        release.get("fiscal_year") or package_meta.get("fiscal_year"),
        fallback_date=award_date or tender_published_date or start_date or bid_opening_date,
    )

    amount_type = (
        "award_value"
        if has_award
        else ("tender_estimate" if tender_value_inr > 0 else "unrecorded")
    )

    return {
        "ocid": release.get("ocid") or "",
        "tender_id": tender.get("id") or release.get("ocid") or release.get("id") or "",
        "tender_title": tender.get("title") or "",
        "buyer_name": buyer.get("name") or procuring_entity.get("name") or "",
        "buyer_id": buyer.get("id") or procuring_entity.get("id") or "",
        "procurement_method": tender.get("procurementMethod") or "open",
        "bid_count": bid_count,
        "release_date": release_date,
        "tender_published_date": tender_published_date,
        "tender_start_date": start_date,
        "tender_end_date": end_date,
        "bid_opening_date": bid_opening_date,
        "bid_window_days": bid_window_days,
        "award_id": first_award.get("id") or "",
        "award_date": award_date,
        "has_award": has_award,
        "tender_estimate_inr": tender_value_inr,
        "tender_value_inr": tender_value_inr,
        "award_value_inr": award_amount_inr,
        "award_amount_inr": award_amount_inr,
        "amount_type": amount_type,
        "vendor_name": first_supplier.get("name") or "",
        "vendor_id": first_supplier.get("id") or "",
        "item_category": str(item_category).strip() or "Public Procurement",
        "jurisdiction": jurisdiction,
        "fiscal_year": fiscal_year,
        "source_url": str(source_url),
        "source_row_index": row_index,
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


def _normalize_ocds_flat_row(
    row: dict[str, Any],
    source_ref: str,
    row_index: int = 1,
) -> dict[str, Any]:
    """Normalize CivicDataLab flat OCDS CSV/XLSX slash-headers into canonical engine keys.

    Strictly keeps ``release_date``, ``tender_published_date``, ``bid_opening_date``,
    ``award_date``, ``tender_estimate_inr``, and ``award_value_inr`` distinct.
    """
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
    award_amount_inr: float | None = _to_float_safe(award_val_raw, 0.0) if has_award else None

    release_date = str(row.get("date") or row.get("release/date") or "").strip()
    tender_published_date = str(row.get("tender/datePublished") or "").strip()
    start_date = str(
        row.get("tender/tenderPeriod/startDate") or tender_published_date or ""
    ).strip()
    end_date = str(row.get("tender/tenderPeriod/endDate") or "").strip()
    bid_opening_date = _extract_bid_opening_date_from_row(row)

    bid_window_days = _parse_iso_days(start_date, end_date)
    if bid_window_days is None and row.get("tender/tenderPeriod/durationInDays") not in (None, ""):
        bid_window_days = _to_int_opt(row.get("tender/tenderPeriod/durationInDays"))

    bid_count = _to_int_opt(row.get("tender/numberOfTenderers"))

    cls_desc = str(
        row.get("tenderclassification/description")
        or row.get("tender/classification/description")
        or ""
    ).strip()
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
    # Strictly genuine award/contract dates ONLY; NEVER fall back to release.date or tender/datePublished.
    award_date = str(
        row.get("award/date")
        or row.get("awards/0/date")
        or row.get("awards/0/contractPeriod/startDate")
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
    out["release_date"] = out.get("release_date") or release_date
    out["tender_published_date"] = out.get("tender_published_date") or tender_published_date
    out["tender_start_date"] = out.get("tender_start_date") or start_date
    out["tender_end_date"] = out.get("tender_end_date") or end_date
    out["bid_opening_date"] = out.get("bid_opening_date") or bid_opening_date
    if out.get("bid_window_days") is None:
        out["bid_window_days"] = bid_window_days
    out["award_id"] = out.get("award_id") or award_id
    out["award_date"] = out.get("award_date") or award_date
    out["has_award"] = has_award
    out["tender_estimate_inr"] = tender_value_inr
    out["tender_value_inr"] = tender_value_inr
    # Never treat tender estimates as award values.
    out["award_amount_inr"] = award_amount_inr
    out["award_value_inr"] = award_amount_inr if has_award else None
    out["amount_type"] = (
        "award_value"
        if has_award
        else ("tender_estimate" if tender_value_inr > 0 else "unrecorded")
    )
    out["vendor_name"] = out.get("vendor_name") or vendor_name
    out["vendor_id"] = out.get("vendor_id") or vendor_id
    out["item_category"] = out.get("item_category") or item_category
    out["jurisdiction"] = out.get("jurisdiction") or _infer_jurisdiction(source_ref)
    out["fiscal_year"] = _normalize_fy(
        row.get("fiscal_year"),
        fallback_date=award_date or tender_published_date or start_date or bid_opening_date,
    )
    out["source_url"] = out.get("source_url") or source_url
    out["source_row_index"] = row_index
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
    """Return True ONLY when explicitly marked as a synthetic sample/demo artifact."""
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


def _unlink_quiet(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


def _read_validated_cache(
    url: str,
    expected_sha256: str | None,
    max_download_bytes: int,
) -> tuple[bytes, str, str] | None:
    """Return ``(bytes, content_type, fetched_at)`` only if cache digest & TTL revalidate cleanly."""
    url_hash = compute_sha256(url)
    cdir = _cache_dir()
    data_cache = cdir / f"{url_hash}.bin"
    meta_cache = cdir / f"{url_hash}.meta.json"
    if not (data_cache.exists() and meta_cache.exists()):
        return None
    try:
        meta = json.loads(meta_cache.read_text(encoding="utf-8"))
        if meta.get("url") != url:
            return None
        ttl = int(os.environ.get("AIWATCHDOGS_CACHE_TTL_SECONDS", str(DEFAULT_CACHE_TTL_SECONDS)))
        fetched_at = str(meta.get("fetched_at") or "")
        if ttl > 0 and fetched_at:
            try:
                ft = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
                age = (datetime.now(timezone.utc) - ft).total_seconds()
                if age > ttl:
                    _unlink_quiet(data_cache)
                    _unlink_quiet(meta_cache)
                    return None
            except ValueError:
                pass
        cached_bytes = data_cache.read_bytes()
        if not cached_bytes or len(cached_bytes) > max_download_bytes:
            _unlink_quiet(data_cache)
            _unlink_quiet(meta_cache)
            return None
        observed_sha = compute_sha256(cached_bytes)
        recorded_sha = str(meta.get("sha256") or "").lower()
        if observed_sha != recorded_sha:
            _unlink_quiet(data_cache)
            _unlink_quiet(meta_cache)
            return None
        if expected_sha256 and observed_sha != expected_sha256.lower():
            # Stale or mismatched cache entry: invalidate and force fresh network fetch.
            _unlink_quiet(data_cache)
            _unlink_quiet(meta_cache)
            return None
        return cached_bytes, str(meta.get("content_type") or ""), fetched_at or _utc_now_iso()
    except (OSError, ValueError):
        return None


def _persist_raw_cache_if_clean(
    url: str,
    raw_bytes: bytes,
    content_type: str,
    sha256: str,
    fetched_at: str,
    *,
    has_pii: bool,
) -> None:
    """Persist opt-in cache with 0o700/0o600 permissions ONLY when zero raw PII was detected."""
    url_hash = compute_sha256(url)
    cdir = _cache_dir()
    data_cache = cdir / f"{url_hash}.bin"
    meta_cache = cdir / f"{url_hash}.meta.json"
    if has_pii:
        # Never leave unscrubbed raw PII on disk; also purge any pre-existing entry.
        _unlink_quiet(data_cache)
        _unlink_quiet(meta_cache)
        return
    if not raw_bytes:
        return
    try:
        cdir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(cdir, 0o700)
        except OSError:
            pass
        fd, tmp_path = tempfile.mkstemp(prefix=f"{url_hash}_", dir=str(cdir))
        try:
            os.chmod(tmp_path, 0o600)
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
                "sha256": sha256,
                "byte_size": len(raw_bytes),
                "fetched_at": fetched_at,
            }
        )
        fd_m, tmp_meta = tempfile.mkstemp(prefix=f"{url_hash}_meta_", dir=str(cdir))
        try:
            os.chmod(tmp_meta, 0o600)
            with os.fdopen(fd_m, "w", encoding="utf-8") as fh:
                fh.write(meta_payload)
            os.replace(tmp_meta, meta_cache)
        finally:
            if os.path.exists(tmp_meta):
                os.unlink(tmp_meta)
    except OSError:
        pass


def _fetch_remote_bytes(
    url: str,
    timeout: float = 60.0,
    use_cache: bool = False,
    expected_sha256: str | None = None,
    max_download_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
) -> tuple[bytes, str, str, bool]:
    """Fetch remote public HTTPS bytes with strict size bounds and cache digest revalidation.

    Returns ``(raw_bytes, content_type, fetched_at_iso, from_cache)``.
    """
    _validate_public_https_url(url)

    cache_enabled = use_cache and os.environ.get("AIWATCHDOGS_DISABLE_CACHE") != "1"
    if cache_enabled:
        hit = _read_validated_cache(url, expected_sha256, max_download_bytes)
        if hit is not None:
            return hit[0], hit[1], hit[2], True

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/csv, application/pdf, application/zip, application/vnd.openxmlformats-officedocument.spreadsheetml.sheet, text/html;q=0.9, text/plain;q=0.8, */*;q=0.5",
    }
    fetched_at = _utc_now_iso()
    with httpx.Client(headers=headers, timeout=timeout, follow_redirects=True) as client:
        if httpx.Client.get is _ORIGINAL_HTTPX_GET:
            with client.stream("GET", url) as response:
                final_url = str(getattr(response, "url", "") or url)
                if _is_url(final_url):
                    _validate_public_https_url(final_url)
                if response.status_code in {401, 403}:
                    raise PermissionError(
                        f"Remote source {url} returned HTTP {response.status_code}. "
                        "AI Watchdogs does not bypass authentication walls or access controls (IT Act Sec 43)."
                    )
                response.raise_for_status()
                content_type = response.headers.get("content-type", "")
                cl_hdr = response.headers.get("content-length")
                if cl_hdr and cl_hdr.isdigit() and int(cl_hdr) > max_download_bytes:
                    raise ValueError(
                        f"Remote artifact {url} declares Content-Length={int(cl_hdr)} bytes, "
                        f"exceeding limit of {max_download_bytes} bytes."
                    )
                buf = bytearray()
                for chunk in response.iter_bytes():
                    buf.extend(chunk)
                    if len(buf) > max_download_bytes:
                        raise ValueError(
                            f"Remote artifact {url} exceeded maximum allowed download size of {max_download_bytes} bytes."
                        )
                raw_bytes = bytes(buf)
        else:
            response = client.get(url)
            final_url = str(getattr(response, "url", "") or url)
            if _is_url(final_url):
                _validate_public_https_url(final_url)
            if response.status_code in {401, 403}:
                raise PermissionError(
                    f"Remote source {url} returned HTTP {response.status_code}. "
                    "AI Watchdogs does not bypass authentication walls or access controls (IT Act Sec 43)."
                )
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            raw_bytes = response.content
            if len(raw_bytes) > max_download_bytes:
                raise ValueError(
                    f"Remote artifact {url} size {len(raw_bytes)} bytes exceeds limit of {max_download_bytes} bytes."
                )

        # Refuse to scrape or bypass CAPTCHA/anti-bot HTML pages.
        if "text/html" in content_type.lower() or raw_bytes[:512].lstrip().lower().startswith((b"<!doctype html", b"<html")):
            snippet = raw_bytes[:8192].decode("utf-8", errors="ignore").lower()
            for marker in _CAPTCHA_MARKERS:
                if marker in snippet:
                    raise PermissionError(
                        f"Remote source {url} is gated by a CAPTCHA/anti-bot check ('{marker}'). "
                        "Per IT Act Sec 43 policy, please download the official file manually and audit locally."
                    )

    return raw_bytes, content_type, fetched_at, False


def _sniff_csv_delimiter(header_line: str) -> str:
    """Detect ';' vs ',' delimiter from header line (supports real NREGA & MPLADS CSVs)."""
    semi = header_line.count(";")
    comma = header_line.count(",")
    if semi >= 2 and semi > comma:
        return ";"
    return ","


def _parse_csv_bytes(
    raw_bytes: bytes,
    source_ref: str,
    *,
    max_parse_rows: int = DEFAULT_MAX_PARSE_ROWS,
) -> tuple[list[dict[str, Any]], str]:
    text = raw_bytes.decode("utf-8-sig", errors="replace")
    data_lines = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    first_non_empty = next((ln for ln in data_lines if ln.strip()), "")
    delimiter = _sniff_csv_delimiter(first_non_empty)

    reader = csv.DictReader(io.StringIO("\n".join(data_lines)), delimiter=delimiter)
    fieldnames = {str(h).strip() for h in (reader.fieldnames or []) if h is not None and str(h).strip()}
    is_ocds = _is_ocds_flat_row(fieldnames)

    records: list[dict[str, Any]] = []
    for row_idx, row in enumerate(reader, start=1):
        if len(records) >= max_parse_rows:
            raise ValueError(
                f"CSV dataset {source_ref} exceeds maximum allowed row cap ({max_parse_rows} rows)."
            )
        cleaned_row = {
            str(k).strip(): (v.strip() if isinstance(v, str) else v)
            for k, v in row.items()
            if k is not None and str(k).strip() != ""
        }
        if not any(str(v or "").strip() for v in cleaned_row.values()):
            continue
        if is_ocds:
            cleaned_row = _normalize_ocds_flat_row(cleaned_row, source_ref, row_index=row_idx)
        else:
            cleaned_row.setdefault("source_row_index", row_idx)
            if _is_url(source_ref):
                cleaned_row.setdefault("source_url", source_ref)
        records.append(cleaned_row)
    return records, text[:8192]


def _check_xml_safe(xml_bytes: bytes, member_name: str) -> None:
    head = xml_bytes[:4096].upper()
    if b"<!DOCTYPE" in head or b"<!ENTITY" in head:
        raise ValueError(f"Refused unsafe XML DTD/ENTITY declaration in archive member '{member_name}'.")


def _read_zip_member_bounded(
    zf: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    *,
    remaining_bytes: int,
    max_ratio: float,
) -> bytes:
    """Safely read a single zip member enforcing decompressed byte and ratio limits."""
    name = info.filename
    if name.startswith(("/", "\\")) or ".." in Path(name).parts:
        raise ValueError(f"Refused unsafe archive member path: {name!r}")
    if info.file_size > remaining_bytes:
        raise ValueError(
            f"Archive member '{name}' declared size {info.file_size} exceeds remaining limit {remaining_bytes}."
        )
    buf = bytearray()
    with zf.open(info, "r") as stream:
        while True:
            chunk = stream.read(65536)
            if not chunk:
                break
            buf.extend(chunk)
            if len(buf) > remaining_bytes:
                raise ValueError(
                    f"Archive member '{name}' decompressed size exceeds maximum allowed limit."
                )
    actual_size = len(buf)
    if actual_size >= 65_536 and (actual_size / float(max(info.compress_size, 1))) > max_ratio:
        raise ValueError(
            f"Archive member '{name}' compression ratio {actual_size / float(max(info.compress_size, 1)):.1f}x "
            f"exceeds maximum allowed ratio {max_ratio:.1f}x."
        )
    return bytes(buf)


def _read_xlsx_sheet(
    zf: zipfile.ZipFile,
    sheet_path: str,
    shared_strings: list[str],
    *,
    remaining_bytes: int,
    max_ratio: float,
    max_parse_rows: int,
) -> tuple[list[dict[str, Any]], int]:
    """Parse a single OOXML worksheet XML with bounded decompression, DTD guard, and row cap."""
    info = zf.getinfo(sheet_path)
    xml_bytes = _read_zip_member_bounded(
        zf, info, remaining_bytes=remaining_bytes, max_ratio=max_ratio
    )
    _check_xml_safe(xml_bytes, sheet_path)

    row_tag = f"{{{_XLSX_MAIN_NS}}}row"
    c_tag = f"{{{_XLSX_MAIN_NS}}}c"
    v_tag = f"{{{_XLSX_MAIN_NS}}}v"
    is_tag = f"{{{_XLSX_MAIN_NS}}}is"
    t_tag = f"{{{_XLSX_MAIN_NS}}}t"

    headers_by_col: dict[str, str] = {}
    records: list[dict[str, Any]] = []

    for _, elem in ET.iterparse(io.BytesIO(xml_bytes), events=("end",)):
        if elem.tag != row_tag:
            continue
        excel_row_num = _to_int_opt(elem.attrib.get("r"))
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
            if len(records) >= max_parse_rows:
                raise ValueError(
                    f"XLSX sheet '{sheet_path}' exceeds maximum allowed row cap ({max_parse_rows} rows)."
                )
            rec: dict[str, Any] = {
                hdr: row_vals.get(col, "")
                for col, hdr in headers_by_col.items()
            }
            if any(rec.values()):
                rec["source_row_index"] = excel_row_num or (len(records) + 2)
                records.append(rec)
        elem.clear()
    return records, len(xml_bytes)


def _parse_xlsx_bytes(
    raw_bytes: bytes,
    source_ref: str,
    *,
    max_decompressed_bytes: int = DEFAULT_MAX_DECOMPRESSED_BYTES,
    max_compression_ratio: float = DEFAULT_MAX_COMPRESSION_RATIO,
    max_zip_members: int = DEFAULT_MAX_ZIP_MEMBERS,
    max_parse_rows: int = DEFAULT_MAX_PARSE_ROWS,
) -> tuple[list[dict[str, Any]], str, str, str]:
    """Pure-stdlib .xlsx reader with bounded decompression, DTD safety, and OCDS tenders+awards auto-join."""
    with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
        infos = zf.infolist()
        if len(infos) > max_zip_members:
            raise ValueError(f"XLSX archive has {len(infos)} members, exceeding cap of {max_zip_members}.")
        names = set(zf.namelist())
        remaining = max_decompressed_bytes

        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in names:
            sst_bytes = _read_zip_member_bounded(
                zf,
                zf.getinfo("xl/sharedStrings.xml"),
                remaining_bytes=remaining,
                max_ratio=max_compression_ratio,
            )
            remaining -= len(sst_bytes)
            _check_xml_safe(sst_bytes, "xl/sharedStrings.xml")
            sst_root = ET.fromstring(sst_bytes)
            si_tag = f"{{{_XLSX_MAIN_NS}}}si"
            t_tag = f"{{{_XLSX_MAIN_NS}}}t"
            for si in sst_root.findall(si_tag):
                shared_strings.append("".join(t.text or "" for t in si.iter(t_tag)))

        wb_bytes = _read_zip_member_bounded(
            zf,
            zf.getinfo("xl/workbook.xml"),
            remaining_bytes=remaining,
            max_ratio=max_compression_ratio,
        )
        remaining -= len(wb_bytes)
        _check_xml_safe(wb_bytes, "xl/workbook.xml")
        wb_root = ET.fromstring(wb_bytes)

        rels_bytes = _read_zip_member_bounded(
            zf,
            zf.getinfo("xl/_rels/workbook.xml.rels"),
            remaining_bytes=remaining,
            max_ratio=max_compression_ratio,
        )
        remaining -= len(rels_bytes)
        _check_xml_safe(rels_bytes, "xl/_rels/workbook.xml.rels")
        rels_root = ET.fromstring(rels_bytes)

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
            tenders, used_t = _read_xlsx_sheet(
                zf,
                sheet_map["tenders"],
                shared_strings,
                remaining_bytes=remaining,
                max_ratio=max_compression_ratio,
                max_parse_rows=max_parse_rows,
            )
            remaining -= used_t
            awards, _ = _read_xlsx_sheet(
                zf,
                sheet_map["awards"],
                shared_strings,
                remaining_bytes=remaining,
                max_ratio=max_compression_ratio,
                max_parse_rows=max_parse_rows,
            )

            tenders_by_ocid: dict[str, dict[str, Any]] = {}
            for t in tenders:
                ocid = str(t.get("ocid") or "").strip()
                if ocid:
                    tenders_by_ocid[ocid] = t

            matched_ocids: set[str] = set()
            joined: list[dict[str, Any]] = []
            for row_i, aw in enumerate(awards, start=1):
                ocid = str(aw.get("ocid") or "").strip()
                base = dict(tenders_by_ocid.get(ocid, {}))
                if ocid and ocid in tenders_by_ocid:
                    matched_ocids.add(ocid)
                for k, v in aw.items():
                    if not v or k == "source_row_index":
                        continue
                    if k.startswith("awards/") or k in {"award/date", "ocid"} or k not in base or not base.get(k):
                        if k.startswith("parties/") and base.get(k):
                            continue
                        base[k] = v
                joined.append(_normalize_ocds_flat_row(base, source_ref, row_index=int(aw.get("source_row_index") or row_i)))

            for row_i, t in enumerate(tenders, start=1):
                ocid = str(t.get("ocid") or "").strip()
                if ocid not in matched_ocids:
                    joined.append(_normalize_ocds_flat_row(dict(t), source_ref, row_index=int(t.get("source_row_index") or row_i)))

            preview = f"XLSX OCDS workbook: {len(tenders)} tenders, {len(awards)} awards, {len(joined)} joined rows"
            return joined, preview, "xlsx_ocds", "sheets=tenders+awards"

        # Single-sheet or primary-sheet workbook (e.g., Assam OBDS Expenditure)
        records: list[dict[str, Any]] = []
        chosen_sheet = ""
        for sname, spath in sheets:
            rows, used_s = _read_xlsx_sheet(
                zf,
                spath,
                shared_strings,
                remaining_bytes=remaining,
                max_ratio=max_compression_ratio,
                max_parse_rows=max_parse_rows,
            )
            remaining -= used_s
            if rows:
                records = rows
                chosen_sheet = sname
                break

        if records:
            headers = set(records[0].keys())
            if _is_ocds_flat_row(headers):
                records = [
                    _normalize_ocds_flat_row(r, source_ref, row_index=int(r.get("source_row_index") or i))
                    for i, r in enumerate(records, start=1)
                ]
                return records, f"XLSX OCDS sheet '{chosen_sheet}': {len(records)} rows", "xlsx_ocds", f"sheet={chosen_sheet}"
            if _is_url(source_ref):
                for r in records:
                    r.setdefault("source_url", source_ref)

        return records, f"XLSX sheet '{chosen_sheet}': {len(records)} rows", "xlsx", f"sheet={chosen_sheet}"


def _parse_json_bytes(
    raw_bytes: bytes,
    source_ref: str = "",
    *,
    max_parse_rows: int = DEFAULT_MAX_PARSE_ROWS,
) -> tuple[list[dict[str, Any]], str, Any, str]:
    text = raw_bytes.decode("utf-8-sig", errors="replace")
    payload = json.loads(text)
    detected_format = "json"
    preview = text[:8192]

    if isinstance(payload, list):
        if len(payload) > max_parse_rows:
            raise ValueError(f"JSON array exceeds maximum allowed row cap ({max_parse_rows}).")
        records = [r for r in payload if isinstance(r, dict)]
        return records, preview, payload, detected_format

    if isinstance(payload, dict):
        if isinstance(payload.get("releases"), list):
            detected_format = "ocds_json"
            releases = payload["releases"]
            if len(releases) > max_parse_rows:
                raise ValueError(f"OCDS release package exceeds maximum allowed row cap ({max_parse_rows}).")
            publisher = payload.get("publisher")
            pub_name = publisher.get("name") if isinstance(publisher, dict) else None
            package_meta = {
                "jurisdiction": payload.get("jurisdiction") or _infer_jurisdiction(source_ref, pub_name),
                "fiscal_year": payload.get("fiscal_year"),
                "source_url": payload.get("uri") or payload.get("source_url") or (source_ref if _is_url(source_ref) else None),
            }
            records = [
                _flatten_ocds_release(rel, package_meta, row_index=idx)
                for idx, rel in enumerate(releases, start=1)
                if isinstance(rel, dict)
            ]
            return records, preview, payload, detected_format

        if payload.get("success") is True and isinstance(payload.get("result"), dict):
            result_obj = payload["result"]
            if isinstance(result_obj.get("records"), list):
                rows = result_obj["records"]
                if len(rows) > max_parse_rows:
                    raise ValueError(f"CKAN dataset exceeds maximum allowed row cap ({max_parse_rows}).")
                return [r for r in rows if isinstance(r, dict)], preview, payload, "ckan_json"
            if isinstance(result_obj.get("results"), list):
                rows = result_obj["results"]
                if len(rows) > max_parse_rows:
                    raise ValueError(f"CKAN dataset exceeds maximum allowed row cap ({max_parse_rows}).")
                return [r for r in rows if isinstance(r, dict)], preview, payload, "ckan_json"

        for key in ("records", "data", "items", "paras", "tenders", "rows"):
            if isinstance(payload.get(key), list):
                rows = payload[key]
                if len(rows) > max_parse_rows:
                    raise ValueError(f"JSON dataset '{key}' exceeds maximum allowed row cap ({max_parse_rows}).")
                return [r for r in rows if isinstance(r, dict)], preview, payload, detected_format

        return [payload], preview, payload, detected_format

    return [], preview, payload, detected_format


def _parse_zip_bytes(
    raw_bytes: bytes,
    source_ref: str,
    *,
    max_decompressed_bytes: int = DEFAULT_MAX_DECOMPRESSED_BYTES,
    max_compression_ratio: float = DEFAULT_MAX_COMPRESSION_RATIO,
    max_zip_members: int = DEFAULT_MAX_ZIP_MEMBERS,
    max_parse_rows: int = DEFAULT_MAX_PARSE_ROWS,
) -> tuple[list[dict[str, Any]], str, Any, str, str]:
    """Pure-stdlib .json.zip / .zip reader with decompression bomb and traversal guards."""
    with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
        infos = zf.infolist()
        if len(infos) > max_zip_members:
            raise ValueError(f"ZIP archive has {len(infos)} members, exceeding cap of {max_zip_members}.")
        members = [
            name
            for name in zf.namelist()
            if not name.endswith("/")
            and not name.startswith("__MACOSX/")
            and not Path(name).name.startswith("._")
        ]
        if "xl/workbook.xml" in members:
            recs, preview, fmt, locator = _parse_xlsx_bytes(
                raw_bytes,
                source_ref,
                max_decompressed_bytes=max_decompressed_bytes,
                max_compression_ratio=max_compression_ratio,
                max_zip_members=max_zip_members,
                max_parse_rows=max_parse_rows,
            )
            return recs, preview, None, fmt, locator

        json_members = [m for m in members if m.lower().endswith(".json")]
        if json_members:
            inner = _read_zip_member_bounded(
                zf,
                zf.getinfo(json_members[0]),
                remaining_bytes=max_decompressed_bytes,
                max_ratio=max_compression_ratio,
            )
            recs, preview, payload, fmt = _parse_json_bytes(
                inner, source_ref=source_ref, max_parse_rows=max_parse_rows
            )
            return recs, preview, payload, fmt, f"zip_member={json_members[0]}"

        csv_members = [m for m in members if m.lower().endswith(".csv")]
        if csv_members:
            inner = _read_zip_member_bounded(
                zf,
                zf.getinfo(csv_members[0]),
                remaining_bytes=max_decompressed_bytes,
                max_ratio=max_compression_ratio,
            )
            recs, preview = _parse_csv_bytes(inner, source_ref=source_ref, max_parse_rows=max_parse_rows)
            return recs, preview, None, "csv", f"zip_member={csv_members[0]}"

    return [], "", None, "zip", "zip=empty"


def _strip_html_cell(fragment: str) -> str:
    no_tags = re.sub(r"<[^>]+>", " ", fragment)
    unescaped = html.unescape(no_tags).replace("\xa0", " ")
    return " ".join(unescaped.split()).strip()


def _parse_html_bytes(
    raw_bytes: bytes,
    source_ref: str,
    *,
    max_parse_rows: int = DEFAULT_MAX_PARSE_ROWS,
) -> tuple[list[dict[str, Any]], str, str]:
    """Extract structured fiscal/accounts tables from official .nic.in HTML pages (e.g. CGA Monthly Accounts)."""
    text = raw_bytes.decode("utf-8", errors="replace")
    tr_blocks = re.findall(r"<tr\b[^>]*>(.*?)</tr>", text, re.IGNORECASE | re.DOTALL)

    rows_cells: list[list[str]] = []
    for tr in tr_blocks:
        if len(rows_cells) >= max_parse_rows:
            raise ValueError(f"HTML table in {source_ref} exceeds maximum row cap ({max_parse_rows}).")
        cells = [
            _strip_html_cell(td)
            for td in re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", tr, re.IGNORECASE | re.DOTALL)
        ]
        if any(cells):
            rows_cells.append(cells)

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
            desc = ""
            desc_idx = -1
            for ci, cell in enumerate(cells[:3]):
                if re.search(r"[A-Za-z]{3,}", cell) and cell.lower() not in {"(details)", "rs.", "current", "coppy**"}:
                    desc = cell
                    desc_idx = ci
                    break
            if not desc or desc.lower().startswith(("budget estimates", "actuals")):
                continue

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
                    "source_row_index": idx + 1,
                }
            )
        if cga_records:
            return cga_records, text[:8192], "html_table"

    generic_records: list[dict[str, Any]] = []
    headers: list[str] = []
    for idx, cells in enumerate(rows_cells, start=1):
        if not headers and len(cells) >= 3 and sum(1 for c in cells if re.search(r"[A-Za-z]", c)) >= 2:
            headers = [c or f"col_{i}" for i, c in enumerate(cells)]
            continue
        if headers and len(cells) == len(headers):
            row_dict = {headers[i]: cells[i] for i in range(len(headers))}
            row_dict.setdefault("source_url", source_url)
            row_dict.setdefault("source_row_index", idx)
            generic_records.append(row_dict)

    return generic_records, text[:8192], "html_table"


def _parse_pdf_bytes(
    raw_bytes: bytes,
    source_ref: str,
    *,
    max_pdf_pages: int = DEFAULT_MAX_PDF_PAGES,
) -> tuple[list[dict[str, Any]], str, str]:
    reader = PdfReader(io.BytesIO(raw_bytes))
    num_pages = len(reader.pages)
    if num_pages > max_pdf_pages:
        raise ValueError(
            f"PDF document {source_ref} has {num_pages} pages, exceeding cap of {max_pdf_pages} pages."
        )
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
    return records, "\n\n".join(all_pages), f"pages=1-{num_pages}"


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


def _build_csr_lookup(
    secondary_records: list[dict[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    """Aggregate MCA CSR records by CIN and index all candidates per normalized name to preserve ambiguity."""
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
            entry["csr_total_inr"] = max(
                entry["csr_total_inr"], _to_float_safe(row.get("csr_total")) * 10_000_000.0
            )
        elif row.get("spent") not in (None, "") or row.get("outlay") not in (None, ""):
            amt = _to_float_safe(row.get("spent"), 0.0)
            if amt <= 0:
                amt = _to_float_safe(row.get("outlay"), 0.0)
            entry["csr_total_inr"] += max(0.0, amt)
            entry["csr_projects_count"] += 1

    strict_map: dict[str, list[dict[str, Any]]] = {}
    core_map: dict[str, list[dict[str, Any]]] = {}
    cin_map: dict[str, dict[str, Any]] = {}
    for entry in by_cin.values():
        if entry["cin"]:
            cin_map[entry["cin"]] = entry
        strict, core = normalize_company_name(entry["company_name"])
        if strict:
            strict_map.setdefault(strict, []).append(entry)
        if core and len(core) >= 6:
            core_map.setdefault(core, []).append(entry)
    return strict_map, core_map, cin_map


def _join_secondary_records(
    primary_records: list[dict[str, Any]],
    secondary_records: list[dict[str, Any]],
    secondary_ref: str,
    secondary_sha256: str,
) -> list[dict[str, Any]]:
    """Link primary records (e.g. Electoral Bonds) with secondary CSR rows without fabricating exact CINs.

    - Requires a corroborating CIN in the primary record for ``join_basis="exact_cin"``.
    - Otherwise joins by normalized company name (``join_basis="normalized_name"``), NEVER copies
      the CSR CIN into the primary row's ``cin`` field as an exact match, and records explicit
      ambiguity metadata when multiple CSR CINs share the same normalized name.
    """
    if not secondary_records:
        return primary_records

    sample_sec = secondary_records[0]
    has_csr_fields = any(
        k in sample_sec for k in ("cin", "CIN", "csr_total", "csr_total_rank", "company_name", "Company Name")
    )
    if not has_csr_fields:
        return primary_records

    strict_map, core_map, cin_map = _build_csr_lookup(secondary_records)
    enriched: list[dict[str, Any]] = []
    for rec in primary_records:
        primary_cin = str(rec.get("cin") or rec.get("CIN") or "").strip().upper()
        purchaser = str(
            rec.get("purchaser_name")
            or rec.get("Name of the Purchaser")
            or rec.get("vendor_name")
            or rec.get("company_name")
            or ""
        ).strip()

        # 1. Exact corroborating identifier match (only when primary row already carries a CIN)
        if primary_cin and primary_cin in cin_map:
            match = cin_map[primary_cin]
            updated = dict(rec)
            updated["bond_source_cin"] = primary_cin
            updated["matched_csr_cin"] = match["cin"]
            updated["csr_company_name"] = match["company_name"]
            updated["csr_total_inr"] = round(match["csr_total_inr"], 2)
            updated["csr_total_crore"] = round(match["csr_total_inr"] / 10_000_000.0, 4)
            if match.get("csr_rank") is not None:
                updated["csr_rank"] = match["csr_rank"]
            updated["join_basis"] = "exact_cin"
            updated["join_match_tier"] = "exact_cin"
            updated["exact_identifier_verified"] = True
            updated["join_ambiguous"] = False
            updated["join_candidate_count"] = 1
            updated["join_candidate_cins"] = [match["cin"]]
            updated["join_candidate_names"] = [match["company_name"]]
            updated["secondary_source_url"] = secondary_ref
            updated["secondary_sha256"] = secondary_sha256
            enriched.append(updated)
            continue

        # 2. Normalized-name cross-disclosure match (never copy CSR CIN into primary `cin`!)
        if purchaser:
            strict, core = normalize_company_name(purchaser)
            candidates: list[dict[str, Any]] = []
            match_tier = ""
            matched_key = ""
            if strict and strict in strict_map:
                candidates = strict_map[strict]
                match_tier = "strict_normalized_name"
                matched_key = strict
            elif core and len(core) >= 6 and core in core_map:
                candidates = core_map[core]
                match_tier = "core_normalized_name"
                matched_key = core

            if candidates:
                updated = dict(rec)
                ambiguous = len(candidates) > 1
                updated["bond_source_cin"] = primary_cin or None
                updated["join_basis"] = "normalized_name"
                updated["join_match_tier"] = match_tier
                updated["matched_normalized_key"] = matched_key
                updated["exact_identifier_verified"] = False
                updated["join_ambiguous"] = ambiguous
                updated["join_candidate_count"] = len(candidates)
                updated["join_candidate_cins"] = sorted({c["cin"] for c in candidates if c.get("cin")})
                updated["join_candidate_names"] = sorted({c["company_name"] for c in candidates if c.get("company_name")})
                if not ambiguous:
                    match = candidates[0]
                    updated["matched_csr_cin"] = match["cin"]
                    updated["csr_company_name"] = match["company_name"]
                    updated["csr_total_inr"] = round(match["csr_total_inr"], 2)
                    updated["csr_total_crore"] = round(match["csr_total_inr"] / 10_000_000.0, 4)
                    if match.get("csr_rank") is not None:
                        updated["csr_rank"] = match["csr_rank"]
                else:
                    updated["matched_csr_cin"] = None
                    updated["csr_company_name"] = None
                    updated["csr_total_inr"] = None
                    updated["csr_total_crore"] = None
                updated["secondary_source_url"] = secondary_ref
                updated["secondary_sha256"] = secondary_sha256
                enriched.append(updated)
                continue

        enriched.append(rec)
    return enriched


def _resolve_provenance(
    source_ref: str,
    ctx: dict[str, Any],
    *,
    sha256: str,
    expected_sha256: str | None,
    digest_verified: bool | None,
    byte_size: int,
    fetched_at: str,
    source_locator: str | None,
) -> dict[str, Any]:
    """Build strictly separated provenance fields distinguishing official publisher vs mirror."""
    lower = source_ref.lower()
    is_remote = _is_url(source_ref)
    host = (urlparse(source_ref).hostname or "").lower() if is_remote else ""

    inferred_publisher: str | None = None
    inferred_landing: str | None = None
    inferred_mirror: str | None = None

    if "civicdatalab/assam-tenders-data" in lower:
        inferred_publisher = "Finance Department, Government of Assam (assamtenders.gov.in)"
        inferred_landing = "https://assam.open-contracting.in/datasets"
        inferred_mirror = "CivicDataLab GitHub Mirror"
    elif "civicdatalab/himachal-pradesh-health-procurement-ocds" in lower:
        inferred_publisher = "Government of Himachal Pradesh (hptenders.gov.in)"
        inferred_landing = "https://hptenders.gov.in"
        inferred_mirror = "CivicDataLab GitHub Mirror"
    elif "civicdatalab/assam-budget-obds" in lower:
        inferred_publisher = "Finance Department, Government of Assam"
        inferred_landing = "https://finance.assam.gov.in"
        inferred_mirror = "CivicDataLab GitHub Mirror (Assam-Budget-OBDS)"
    elif "civicdatalab/child-budget-tracker" in lower:
        inferred_publisher = "Finance Department, Government of Andhra Pradesh"
        inferred_landing = "https://treasury.ap.gov.in"
        inferred_mirror = "CivicDataLab GitHub Mirror (child-budget-tracker)"
    elif "d1jmc0u14z2tmp.cloudfront.net" in lower or host.endswith("cag.gov.in"):
        inferred_publisher = "Comptroller and Auditor General of India"
        inferred_landing = "https://cag.gov.in/en/audit-report"
        inferred_mirror = "CAG CloudFront Distribution" if "cloudfront.net" in lower else None
    elif host.endswith("cga.nic.in"):
        inferred_publisher = "Controller General of Accounts, Ministry of Finance"
        inferred_landing = source_ref
        inferred_mirror = None
    elif "saisantoshv3/electoral_bonds" in lower:
        inferred_publisher = "Election Commission of India / State Bank of India (SC WP(C) 880/2017)"
        inferred_landing = "https://www.eci.gov.in"
        inferred_mirror = "GitHub Mirror (saisantoshv3/electoral_bonds)"
    elif "spfrantz/mca-csr-scraper" in lower:
        inferred_publisher = "Ministry of Corporate Affairs (National CSR Portal)"
        inferred_landing = "https://www.csr.gov.in"
        inferred_mirror = "GitHub Mirror (spfrantz/MCA-CSR-Scraper)"
    elif "aditya-kr86/pmgsy_multiclass_ml" in lower:
        inferred_publisher = "National Rural Infrastructure Development Agency (PMGSY OMMAS / data.gov.in)"
        inferred_landing = "https://ommas.nic.in"
        inferred_mirror = "GitHub Mirror (aditya-kr86/PMGSY_Multiclass_ML)"
    elif "arpita-deb/nrega-data-analysis" in lower:
        inferred_publisher = "Ministry of Rural Development (MGNREGA MIS)"
        inferred_landing = "https://nrega.nic.in"
        inferred_mirror = "GitHub Mirror (Arpita-deb/NREGA-Data-Analysis)"
    elif "vonter/india-mplads-works" in lower:
        inferred_publisher = "Ministry of Statistics & Programme Implementation (MPLADS)"
        inferred_landing = "https://mplads.mospi.gov.in"
        inferred_mirror = "GitHub Mirror (Vonter/india-mplads-works)"
    elif "varnasr/policystack" in lower:
        inferred_publisher = "Union Budget / Ministry of Finance (unverified third-party mirror)"
        inferred_landing = "https://indiabudget.gov.in"
        inferred_mirror = "GitHub Mirror (Varnasr/PolicyStack)"
    elif host.endswith((".gov.in", ".nic.in")):
        inferred_publisher = host
        inferred_landing = source_ref
        inferred_mirror = None
    elif "github.com" in host or "githubusercontent.com" in host:
        inferred_mirror = "GitHub Mirror"
        inferred_landing = str(ctx.get("source_landing_page") or ctx.get("source_url") or "") or None
    elif not is_remote:
        inferred_mirror = "local_file"

    official_publisher = str(ctx.get("official_publisher") or inferred_publisher or "").strip() or None
    source_landing_page = (
        str(ctx.get("source_landing_page") or ctx.get("source_url") or inferred_landing or "").strip() or None
    )
    mirror_name = (
        str(ctx.get("mirror_name")).strip()
        if ctx.get("mirror_name") not in (None, "")
        else inferred_mirror
    )
    retrieval_url = (
        str(ctx.get("retrieval_url") or source_ref).strip()
        if is_remote
        else None
    )

    return {
        "official_publisher": official_publisher,
        "source_landing_page": source_landing_page,
        "mirror_name": mirror_name,
        "retrieval_url": retrieval_url,
        "observed_sha256": sha256,
        "expected_sha256": expected_sha256,
        "digest_verified": digest_verified,
        "byte_size": byte_size,
        "fetched_at": fetched_at,
        "source_locator": source_locator,
    }


def _fast_scrub_records(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Recursively scrub all string values and PII keys in records via scrub_pii."""
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
        if isinstance(item, tuple):
            return tuple(_walk(v) for v in item)
        if isinstance(item, dict):
            out: dict[Any, Any] = {}
            for k, v in item.items():
                clean_k = k
                if isinstance(k, str) and len(k) >= 6 and ("@" in k or any("0" <= ch <= "9" for ch in k)):
                    ck, ktags = scrub_pii(k)
                    if ktags:
                        for tag in ktags:
                            if tag not in redactions:
                                redactions.append(tag)
                        clean_k = ck
                out[clean_k] = _walk(v)
            return out
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
    *,
    allow_local_files: bool = False,
    expected_sha256: str | None = None,
    secondary_expected_sha256: str | None = None,
) -> LoadedDataset:
    """Load, hash, verify digests, detect demo status, and PII-scrub a public URL or local file.

    Security & provenance guarantees:
    - ``allow_local_files`` defaults to ``False`` for unattended swarm runner safety; untrusted
      ``context`` dictionaries cannot override ``allow_local_files=False``.
    - Primary and secondary ``expected_sha256`` digests fail closed with ``DigestMismatchError``.
    - Remote fetches require HTTPS public hosts, enforce bounded download size, decompression
      size/ratio limits, XML DTD guards, and row/page parse caps.
    - Raw disk caching is opt-in (``use_cache=True`` or ``AIWATCHDOGS_ENABLE_RAW_CACHE=1``),
      revalidates SHA-256 on hit, writes ``0o600`` files in ``0o700`` dir, and refuses to persist
      raw bytes if any PII is detected.
    """
    ctx = context or {}
    source_ref = str(path_or_url).strip()
    content_type = ""

    # Opt-in raw disk cache only (never persist raw bytes by default unless explicitly requested)
    use_cache = bool(
        ctx.get("use_cache", False)
        or os.environ.get("AIWATCHDOGS_ENABLE_RAW_CACHE") == "1"
    )
    max_download_bytes = int(ctx.get("max_download_bytes", DEFAULT_MAX_DOWNLOAD_BYTES))
    max_decompressed_bytes = int(ctx.get("max_decompressed_bytes", DEFAULT_MAX_DECOMPRESSED_BYTES))
    max_compression_ratio = float(ctx.get("max_compression_ratio", DEFAULT_MAX_COMPRESSION_RATIO))
    max_zip_members = int(ctx.get("max_zip_members", DEFAULT_MAX_ZIP_MEMBERS))
    max_parse_rows = int(ctx.get("max_parse_rows", DEFAULT_MAX_PARSE_ROWS))
    max_pdf_pages = int(ctx.get("max_pdf_pages", DEFAULT_MAX_PDF_PAGES))

    resolved_expected_sha = (
        str(expected_sha256 or ctx.get("expected_sha256") or "").strip().lower() or None
    )
    resolved_sec_expected_sha = (
        str(
            secondary_expected_sha256
            or ctx.get("secondary_expected_sha256")
            or ctx.get("secondary_sha256")
            or ""
        ).strip().lower()
        or None
    )

    is_remote = _is_url(source_ref)
    from_cache = False
    if is_remote:
        raw_bytes, content_type, fetched_at, from_cache = _fetch_remote_bytes(
            source_ref,
            use_cache=use_cache,
            expected_sha256=resolved_expected_sha,
            max_download_bytes=max_download_bytes,
        )
    else:
        if not allow_local_files:
            raise PermissionError(
                f"Local file loading is disabled in unattended mode (allow_local_files=False): {source_ref!r}. "
                "Use interactive local auditing with allow_local_files=True for local files."
            )
        path = Path(source_ref)
        if not path.exists():
            raise FileNotFoundError(f"Dataset path does not exist: {source_ref}")
        if path.stat().st_size > max_download_bytes:
            raise ValueError(
                f"Local file {source_ref} size {path.stat().st_size} bytes exceeds limit of {max_download_bytes} bytes."
            )
        raw_bytes = path.read_bytes()
        fetched_at = _utc_now_iso()

    sha256 = compute_sha256(raw_bytes)
    byte_size = len(raw_bytes)

    # Strict fail-closed primary digest verification
    digest_verified: bool | None = None
    if resolved_expected_sha is not None:
        if sha256.lower() != resolved_expected_sha:
            raise DigestMismatchError(
                f"Primary artifact SHA-256 mismatch for {source_ref}: "
                f"expected {resolved_expected_sha}, observed {sha256}."
            )
        digest_verified = True

    resolved_fmt = _infer_format(source_ref, fmt, content_type, raw_bytes)

    parsed_json: Any = None
    source_locator: str | None = None
    if resolved_fmt == "csv":
        records, raw_text = _parse_csv_bytes(raw_bytes, source_ref, max_parse_rows=max_parse_rows)
        source_locator = f"rows=1-{len(records)}" if records else "rows=0"
    elif resolved_fmt == "xlsx":
        records, raw_text, resolved_fmt, source_locator = _parse_xlsx_bytes(
            raw_bytes,
            source_ref,
            max_decompressed_bytes=max_decompressed_bytes,
            max_compression_ratio=max_compression_ratio,
            max_zip_members=max_zip_members,
            max_parse_rows=max_parse_rows,
        )
    elif resolved_fmt == "zip":
        records, raw_text, parsed_json, resolved_fmt, source_locator = _parse_zip_bytes(
            raw_bytes,
            source_ref,
            max_decompressed_bytes=max_decompressed_bytes,
            max_compression_ratio=max_compression_ratio,
            max_zip_members=max_zip_members,
            max_parse_rows=max_parse_rows,
        )
    elif resolved_fmt == "json":
        records, raw_text, parsed_json, resolved_fmt = _parse_json_bytes(
            raw_bytes, source_ref, max_parse_rows=max_parse_rows
        )
        source_locator = f"records=1-{len(records)}" if records else "records=0"
    elif resolved_fmt == "html":
        records, raw_text, resolved_fmt = _parse_html_bytes(
            raw_bytes, source_ref, max_parse_rows=max_parse_rows
        )
        source_locator = f"table_rows=1-{len(records)}" if records else "table_rows=0"
    elif resolved_fmt == "pdf":
        records, raw_text, source_locator = _parse_pdf_bytes(
            raw_bytes, source_ref, max_pdf_pages=max_pdf_pages
        )
    else:
        raw_text = raw_bytes.decode("utf-8-sig", errors="replace")
        stripped = raw_text.lstrip()
        if stripped.startswith("{") or stripped.startswith("["):
            records, raw_text, parsed_json, resolved_fmt = _parse_json_bytes(
                raw_bytes, source_ref, max_parse_rows=max_parse_rows
            )
            source_locator = f"records=1-{len(records)}" if records else "records=0"
        else:
            records = [{"raw_text": raw_text, "page": 1, "source_ref": source_ref}]
            source_locator = "page=1"

    # Optional secondary dataset join (fail closed on secondary digest mismatch!)
    secondary_ref = str(
        ctx.get("secondary_url") or ctx.get("secondary_dataset_url") or ctx.get("secondary_path") or ""
    ).strip()
    secondary_sha256: str | None = None
    secondary_digest_verified: bool | None = None
    secondary_provenance: dict[str, Any] | None = None
    if secondary_ref and secondary_ref != source_ref:
        sec_ctx: dict[str, Any] = {
            "use_cache": use_cache,
            "max_download_bytes": max_download_bytes,
            "max_decompressed_bytes": max_decompressed_bytes,
            "max_compression_ratio": max_compression_ratio,
            "max_zip_members": max_zip_members,
            "max_parse_rows": max_parse_rows,
            "max_pdf_pages": max_pdf_pages,
        }
        sec_ds = load_dataset(
            secondary_ref,
            context=sec_ctx,
            allow_local_files=allow_local_files,
            expected_sha256=resolved_sec_expected_sha,
        )
        secondary_sha256 = sec_ds.sha256
        secondary_digest_verified = sec_ds.digest_verified
        secondary_provenance = sec_ds.provenance
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

    # Persist opt-in raw cache ONLY after verifying zero PII was present in the payload.
    if is_remote and use_cache and not from_cache and os.environ.get("AIWATCHDOGS_DISABLE_CACHE") != "1":
        _persist_raw_cache_if_clean(
            source_ref,
            raw_bytes,
            content_type,
            sha256,
            fetched_at,
            has_pii=bool(combined_redactions),
        )

    prov = _resolve_provenance(
        source_ref,
        ctx,
        sha256=sha256,
        expected_sha256=resolved_expected_sha,
        digest_verified=digest_verified,
        byte_size=byte_size,
        fetched_at=fetched_at,
        source_locator=source_locator,
    )

    return LoadedDataset(
        records=scrubbed_records,
        raw_text=scrubbed_raw_text,
        sha256=sha256,
        observed_sha256=sha256,
        expected_sha256=resolved_expected_sha,
        digest_verified=digest_verified,
        secondary_sha256=secondary_sha256,
        secondary_expected_sha256=resolved_sec_expected_sha,
        secondary_digest_verified=secondary_digest_verified,
        secondary_provenance=secondary_provenance,
        byte_size=byte_size,
        format=resolved_fmt,
        is_demo_sample=is_demo,
        source_ref=source_ref,
        pii_redactions=combined_redactions,
        official_publisher=prov["official_publisher"],
        source_landing_page=prov["source_landing_page"],
        mirror_name=prov["mirror_name"],
        retrieval_url=prov["retrieval_url"],
        fetched_at=fetched_at,
        source_locator=source_locator,
        provenance=prov,
    )
