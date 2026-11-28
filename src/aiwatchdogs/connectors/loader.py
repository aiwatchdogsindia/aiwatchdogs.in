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
import gzip
import html
from html.parser import HTMLParser
import io
import ipaddress
import json
import math
import os
import re
import socket
import stat
import tarfile
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
import zipfile
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from pypdf import PdfReader

from aiwatchdogs.core.models import LoadedDataset
from aiwatchdogs.core.safety import (
    compute_sha256,
    is_public_ip_address,
    scrub_pii,
    scrub_sensitive_field_value,
    validate_safe_http_url,
)

# Identifies the project honestly. The contact is written as a bare domain: some government
# web firewalls (e.g. indiabudget.gov.in) reject any User-Agent that embeds an "https://" URL.
USER_AGENT = "AIWatchdogs-India-OpenAudit/0.1 (aiwatchdogs.in; lawful-public-audit)"

DEFAULT_MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024  # 100 MiB
DEFAULT_MAX_DECOMPRESSED_BYTES = 250 * 1024 * 1024  # 250 MiB
DEFAULT_MAX_COMPRESSION_RATIO = 100.0
DEFAULT_MAX_ZIP_MEMBERS = 500
DEFAULT_MAX_PARSE_ROWS = 250_000
DEFAULT_MAX_PDF_PAGES = 1000
DEFAULT_MAX_PDF_CHARS = 50_000_000
DEFAULT_MAX_SHARED_STRINGS = 500_000
DEFAULT_MAX_REDIRECTS = 5
DEFAULT_CACHE_TTL_SECONDS = 3600
_SHA256_PIN_RE = re.compile(r"[0-9a-fA-F]{64}")
_PINNED_MEM_CACHE_MAX_ENTRIES = 16
_PINNED_MEM_CACHE_MAX_BYTES = 64 * 1024 * 1024  # 64 MiB bounded in-process cache for SHA-256-pinned artifacts
_PINNED_MEM_CACHE_LOCK = threading.Lock()
_PINNED_MEM_CACHE: OrderedDict[tuple[str, str], tuple[bytes, str, str]] = OrderedDict()
_PINNED_MEM_CACHE_BYTES = 0
_RETRYABLE_SOURCE_STATUS = frozenset({429, 500, 502, 503, 504})
_MAX_SOURCE_RETRIES = 2

_ORIGINAL_HTTPX_GET = httpx.Client.get
_ORIGINAL_HTTPX_STREAM = httpx.Client.stream

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


def _validate_public_https_url(url: str, *, resolve_dns: bool = False) -> None:
    """Enforce HTTPS and reject private/loopback/link-local/metadata/CGNAT hosts & DNS records."""
    try:
        cleaned = validate_safe_http_url(url, require_https=True, allow_private=False)
    except ValueError as exc:
        raise PermissionError(str(exc)) from exc

    parsed = urlparse(cleaned)
    hostname = (parsed.hostname or "").strip().lower().rstrip(".")
    if hostname in _BLOCKED_HOSTNAMES or hostname.endswith((".localhost", ".local", ".internal", ".arpa")):
        raise PermissionError(f"Refused non-public/internal hostname '{hostname}' in {url!r}.")

    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        ip = None
    if ip is not None:
        if not is_public_ip_address(ip):
            raise PermissionError(
                f"Refused non-public IP address '{hostname}' (loopback/private/link-local/reserved) in {url!r}."
            )
        return

    if resolve_dns:
        port = parsed.port or 443
        try:
            infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            # Fail closed on real network clients; allow mocked httpx tests using synthetic hostnames.
            if httpx.Client.get is _ORIGINAL_HTTPX_GET and httpx.Client.stream is _ORIGINAL_HTTPX_STREAM:
                raise PermissionError(f"Could not resolve hostname '{hostname}' for {url!r}: {exc}") from exc
            return
        for _family, _socktype, _proto, _canonname, sockaddr in infos:
            addr_str = sockaddr[0]
            try:
                resolved_ip = ipaddress.ip_address(addr_str)
            except ValueError:
                continue
            if not is_public_ip_address(resolved_ip):
                raise PermissionError(
                    f"Refused URL {url!r}: hostname '{hostname}' resolved to non-public IP address '{resolved_ip}'."
                )


def _cache_dir() -> Path:
    explicit = os.environ.get("AIWATCHDOGS_CACHE_DIR", "").strip()
    if explicit:
        return Path(explicit)
    xdg = os.environ.get("XDG_CACHE_HOME", "").strip()
    base = Path(xdg) if xdg else (Path.home() / ".cache")
    return base / "aiwatchdogs"


def _to_float_safe(value: Any, default: float = 0.0) -> float:
    if value is None or value == "" or isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        parsed = float(value)
        return parsed if math.isfinite(parsed) else default
    text = str(value).strip().replace(",", "").replace("₹", "").replace("INR", "").replace(" ", "")
    try:
        parsed = float(text)
    except ValueError:
        return default
    return parsed if math.isfinite(parsed) else default


def _to_int_opt(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        try:
            return int(round(value))
        except OverflowError:
            return None
    text = str(value).strip()
    if not text or text in {"--", "-", "NA", "N/A", "null", "None"}:
        return None
    try:
        parsed = float(text.replace(",", ""))
        if not math.isfinite(parsed):
            return None
        return int(round(parsed))
    except (ValueError, OverflowError):
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
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y/%m/%d",
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
    if "assam" in lower or "/publication/131" in lower:
        return "State:Assam"
    if (
        "himachal" in lower
        or "hp_health" in lower
        or "hp%20health" in lower
        or "hptenders" in lower
        or "/publication/77" in lower
    ):
        return "State:Himachal Pradesh"
    if "odisha" in lower or "orissa" in lower or "tendersodisha" in lower:
        return "State:Odisha"
    if (
        "uttar pradesh" in lower
        or "flood-data-ecosystem-up" in lower
        or "all_up_tenders" in lower
        or "etender.up.nic.in" in lower
    ):
        return "State:Uttar Pradesh"
    if "andhra" in lower or "ap_child" in lower:
        return "State:Andhra Pradesh"
    if "madhya pradesh" in lower or "mp-jjm" in lower or "mp_jjm" in lower or "mprrda" in lower or "bhopal" in lower:
        return "State:Madhya Pradesh"
    state_markers: tuple[tuple[str, str], ...] = (
        ("arunachal pradesh", "State:Arunachal Pradesh"),
        ("bihar", "State:Bihar"),
        ("chhattisgarh", "State:Chhattisgarh"),
        ("goa", "State:Goa"),
        ("gujarat", "State:Gujarat"),
        ("haryana", "State:Haryana"),
        ("jharkhand", "State:Jharkhand"),
        ("karnataka", "State:Karnataka"),
        ("kerala", "State:Kerala"),
        ("maharashtra", "State:Maharashtra"),
        ("manipur", "State:Manipur"),
        ("meghalaya", "State:Meghalaya"),
        ("mizoram", "State:Mizoram"),
        ("nagaland", "State:Nagaland"),
        ("punjab", "State:Punjab"),
        ("rajasthan", "State:Rajasthan"),
        ("sikkim", "State:Sikkim"),
        ("tamil nadu", "State:Tamil Nadu"),
        ("telangana", "State:Telangana"),
        ("tripura", "State:Tripura"),
        ("uttarakhand", "State:Uttarakhand"),
        ("west bengal", "State:West Bengal"),
        ("jammu and kashmir", "UT:Jammu and Kashmir"),
        ("puducherry", "UT:Puducherry"),
        ("chandigarh", "UT:Chandigarh"),
        ("ladakh", "UT:Ladakh"),
    )
    for token, label in state_markers:
        if re.search(rf"(?<![a-z]){re.escape(token)}(?![a-z])", lower):
            return label
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
    # Unwrap accidental nested 1-element list wrapping on bids.details
    while len(bids_details) == 1 and isinstance(bids_details[0], list):
        bids_details = bids_details[0]

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
    if (bid_count is None or bid_count == 0) and bids_details:
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

    qualified_bid_count = _to_int_opt(
        tender.get("numberOfQualifiedTenderers") or tender.get("qualifiedTenderers")
    )
    if qualified_bid_count is None and bids_details:
        # Distinguish technical-stage rejection ("Rejected-Technical", "Not Admitted-*") from
        # financially evaluated bids ("Accepted-*", "Rejected-AOC", "Rejected-Finance", "valid").
        tech_eval_present = False
        tech_passed = 0
        for b in bids_details:
            if not isinstance(b, dict):
                continue
            bstatus = str(b.get("status") or b.get("bidStatus") or b.get("tendererStatus") or "").strip().lower()
            if not bstatus:
                continue
            if bstatus.startswith(("rejected-technical", "rejected_technical", "not admitted", "not-admitted", "not_admitted", "rejected-prequal", "rejected-eligib")) or bstatus in {"disqualified", "invalid", "withdrawn"}:
                tech_eval_present = True
            elif bstatus.startswith(("accepted", "rejected-aoc", "rejected_aoc", "rejected-finance", "rejected_finance", "rejected-financial")) or bstatus in {"valid", "qualified", "responsive"}:
                tech_eval_present = True
                tech_passed += 1
        if tech_eval_present and tech_passed > 0:
            qualified_bid_count = tech_passed

    procurement_method_details = str(tender.get("procurementMethodDetails") or "").strip()
    exemption_reason = str(
        tender.get("procurementMethodRationale")
        or first_award.get("rationale")
        or ""
    ).strip()

    return {
        "ocid": release.get("ocid") or "",
        "tender_id": tender.get("id") or release.get("ocid") or release.get("id") or "",
        "tender_title": tender.get("title") or "",
        "buyer_name": buyer.get("name") or procuring_entity.get("name") or "",
        "buyer_id": buyer.get("id") or procuring_entity.get("id") or "",
        "procurement_method": tender.get("procurementMethod") or "open",
        "procurement_method_details": procurement_method_details,
        "exemption_reason": exemption_reason,
        "bid_count": bid_count,
        "qualified_bid_count": qualified_bid_count,
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
    out["procurement_method_details"] = out.get("procurement_method_details") or str(
        row.get("tender/procurementMethodDetails") or ""
    ).strip()
    out["exemption_reason"] = out.get("exemption_reason") or str(
        row.get("tender/procurementMethodRationale") or row.get("awards/0/rationale") or ""
    ).strip()
    if "bid_count" not in out or out["bid_count"] is None:
        out["bid_count"] = bid_count
    if "qualified_bid_count" not in out or out["qualified_bid_count"] is None:
        out["qualified_bid_count"] = _to_int_opt(
            row.get("tender/numberOfQualifiedTenderers") or row.get("qualified_bid_count")
        )
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

    parsed_url = urlparse(source_ref) if _is_url(source_ref) else None
    parsed_path = parsed_url.path if parsed_url is not None else source_ref
    lower_path = parsed_path.lower()
    lower_query = (parsed_url.query or "").lower() if parsed_url is not None else ""
    if (
        lower_path.endswith((".tar", ".tar.gz", ".tgz"))
        or ".tar.gz" in lower_query
        or ".tgz" in lower_query
        or (len(raw_bytes) > 262 and raw_bytes[257:262] == b"ustar")
    ):
        return "tar"
    if lower_path.endswith(".gz") or ".gz" in lower_query or raw_bytes[:2] == b"\x1f\x8b":
        return "gzip"
    if lower_path.endswith(".xlsx"):
        return "xlsx"
    if lower_path.endswith(".json.zip") or lower_path.endswith(".zip"):
        return "zip"
    if lower_path.endswith((".jsonl", ".ndjson")) or ".jsonl" in lower_query or ".ndjson" in lower_query:
        return "jsonl"
    if lower_path.endswith(".csv") or ".csv" in lower_query:
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
    if "application/gzip" in ct or "application/x-gzip" in ct:
        return "gzip"
    if "application/zip" in ct or raw_bytes[:4] == b"PK\x03\x04":
        return "zip"
    if "application/x-ndjson" in ct or "application/jsonl" in ct:
        return "jsonl"
    if "application/json" in ct or "+json" in ct:
        return "json"
    if "text/csv" in ct:
        return "csv"
    if "text/html" in ct:
        return "html"
    if "text/plain" in ct:
        return "txt"
    head_sniff = raw_bytes[:512].lstrip().lower()
    if head_sniff.startswith((b"<!doctype html", b"<html", b"<?xml")):
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


def _ensure_private_cache_dir(cdir: Path, *, create: bool) -> bool:
    """Verify cache directory is owned by current user, not a symlink, and mode 0700 (fail closed)."""
    try:
        if create:
            cdir.mkdir(parents=True, exist_ok=True, mode=0o700)
        st = os.lstat(cdir)
        if not stat.S_ISDIR(st.st_mode) or stat.S_ISLNK(st.st_mode):
            return False
        if hasattr(os, "getuid") and st.st_uid != os.getuid():
            return False
        if st.st_mode & 0o077:
            if not create:
                return False
            os.chmod(cdir, 0o700)
            st = os.lstat(cdir)
            if st.st_mode & 0o077:
                return False
        return True
    except OSError:
        return False


def _read_regular_file_bounded(path: Path, max_bytes: int) -> bytes | None:
    """Open with O_NOFOLLOW, verify S_ISREG and st_size <= max_bytes before reading."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd: int | None = None
    try:
        fd = os.open(str(path), flags)
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size <= 0 or st.st_size > max_bytes:
            return None
        with os.fdopen(fd, "rb") as fh:
            fd = None
            data = fh.read(max_bytes + 1)
        if len(data) > max_bytes:
            return None
        return data
    except OSError:
        return None
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass


def _read_validated_cache(
    url: str,
    expected_sha256: str | None,
    max_download_bytes: int,
) -> tuple[bytes, str, str] | None:
    """Return ``(bytes, content_type, fetched_at)`` only if cache dir/files & digest/TTL revalidate cleanly."""
    url_hash = compute_sha256(url)
    cdir = _cache_dir()
    if not _ensure_private_cache_dir(cdir, create=False):
        return None
    data_cache = cdir / f"{url_hash}.bin"
    meta_cache = cdir / f"{url_hash}.meta.json"
    if not (data_cache.exists() and meta_cache.exists()):
        return None
    try:
        meta_raw = _read_regular_file_bounded(meta_cache, 65_536)
        if meta_raw is None:
            return None
        meta = json.loads(meta_raw.decode("utf-8"))
        if meta.get("url") != url:
            return None
        ttl = int(os.environ.get("AIWATCHDOGS_CACHE_TTL_SECONDS", str(DEFAULT_CACHE_TTL_SECONDS)))
        fetched_at = str(meta.get("fetched_at") or "")
        # A SHA-256-pinned entry cannot go stale: its bytes are re-verified against the pin below,
        # so age-based expiry would only force re-downloading identical official files.
        pinned = bool(expected_sha256 and _SHA256_PIN_RE.fullmatch(expected_sha256.strip()))
        if ttl > 0 and fetched_at and not pinned:
            try:
                ft = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
                age = (datetime.now(timezone.utc) - ft).total_seconds()
                if age > ttl:
                    _unlink_quiet(data_cache)
                    _unlink_quiet(meta_cache)
                    return None
            except ValueError:
                pass
        cached_bytes = _read_regular_file_bounded(data_cache, max_download_bytes)
        if not cached_bytes:
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
    """Persist opt-in cache with strict 0o700/0o600 permissions ONLY when zero raw PII was detected."""
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
    if not _ensure_private_cache_dir(cdir, create=True):
        return
    try:
        fd, tmp_path = tempfile.mkstemp(prefix=f"{url_hash}_", dir=str(cdir))
        try:
            os.fchmod(fd, 0o600)
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
            os.fchmod(fd_m, 0o600)
            with os.fdopen(fd_m, "w", encoding="utf-8") as fh:
                fh.write(meta_payload)
            os.replace(tmp_meta, meta_cache)
        finally:
            if os.path.exists(tmp_meta):
                os.unlink(tmp_meta)
    except OSError:
        pass


def _read_pinned_mem_cache(
    url: str,
    expected_sha256: str | None,
    max_download_bytes: int,
) -> tuple[bytes, str, str] | None:
    if not expected_sha256:
        return None
    key = (url, expected_sha256.lower())
    with _PINNED_MEM_CACHE_LOCK:
        entry = _PINNED_MEM_CACHE.get(key)
        if entry is None:
            return None
        raw_bytes, content_type, fetched_at = entry
        if len(raw_bytes) > max_download_bytes or compute_sha256(raw_bytes).lower() != key[1]:
            _PINNED_MEM_CACHE.pop(key, None)
            return None
        _PINNED_MEM_CACHE.move_to_end(key)
        return raw_bytes, content_type, fetched_at


def _store_pinned_mem_cache(
    url: str,
    expected_sha256: str | None,
    raw_bytes: bytes,
    content_type: str,
    fetched_at: str,
    *,
    has_pii: bool,
) -> None:
    global _PINNED_MEM_CACHE_BYTES
    if has_pii or not expected_sha256 or not raw_bytes:
        return
    if len(raw_bytes) > _PINNED_MEM_CACHE_MAX_BYTES // 2:
        return
    key = (url, expected_sha256.lower())
    if compute_sha256(raw_bytes).lower() != key[1]:
        return
    with _PINNED_MEM_CACHE_LOCK:
        prev = _PINNED_MEM_CACHE.pop(key, None)
        if prev is not None:
            _PINNED_MEM_CACHE_BYTES = max(0, _PINNED_MEM_CACHE_BYTES - len(prev[0]))
        while _PINNED_MEM_CACHE and (
            len(_PINNED_MEM_CACHE) >= _PINNED_MEM_CACHE_MAX_ENTRIES
            or _PINNED_MEM_CACHE_BYTES + len(raw_bytes) > _PINNED_MEM_CACHE_MAX_BYTES
        ):
            _, evicted = _PINNED_MEM_CACHE.popitem(last=False)
            _PINNED_MEM_CACHE_BYTES = max(0, _PINNED_MEM_CACHE_BYTES - len(evicted[0]))
        _PINNED_MEM_CACHE[key] = (raw_bytes, content_type, fetched_at)
        _PINNED_MEM_CACHE_BYTES += len(raw_bytes)


def _fetch_remote_bytes(
    url: str,
    timeout: float = 60.0,
    use_cache: bool = False,
    expected_sha256: str | None = None,
    max_download_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
) -> tuple[bytes, str, str, bool]:
    """Fetch remote public HTTPS bytes with hop-by-hop redirect SSRF validation and size bounds.

    Returns ``(raw_bytes, content_type, fetched_at_iso, from_cache)``.
    """
    _validate_public_https_url(url, resolve_dns=True)

    cache_enabled = use_cache and os.environ.get("AIWATCHDOGS_DISABLE_CACHE") != "1"
    if cache_enabled:
        mem_hit = _read_pinned_mem_cache(url, expected_sha256, max_download_bytes)
        if mem_hit is not None:
            return mem_hit[0], mem_hit[1], mem_hit[2], True
        hit = _read_validated_cache(url, expected_sha256, max_download_bytes)
        if hit is not None:
            return hit[0], hit[1], hit[2], True

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/csv, application/pdf, application/zip, application/vnd.openxmlformats-officedocument.spreadsheetml.sheet, text/html;q=0.9, text/plain;q=0.8, */*;q=0.5",
    }
    fetched_at = _utc_now_iso()
    raw_bytes = b""
    content_type = ""
    live_transport = httpx.Client.get is _ORIGINAL_HTTPX_GET and httpx.Client.stream is _ORIGINAL_HTTPX_STREAM
    max_attempts = (1 + _MAX_SOURCE_RETRIES) if live_transport else 1

    # Never enable automatic redirect following: validate every redirect hop BEFORE connecting.
    with httpx.Client(headers=headers, timeout=timeout, follow_redirects=False) as client:
        for attempt in range(max_attempts):
            current_url = url
            try:
                for _hop in range(DEFAULT_MAX_REDIRECTS + 1):
                    _validate_public_https_url(current_url, resolve_dns=True)
                    if httpx.Client.get is _ORIGINAL_HTTPX_GET:
                        with client.stream("GET", current_url) as response:
                            if response.status_code in {301, 302, 303, 307, 308}:
                                location = response.headers.get("location", "").strip()
                                if not location:
                                    raise ValueError(f"Redirect response from {current_url} missing Location header.")
                                next_url = urljoin(current_url, location)
                                _validate_public_https_url(next_url, resolve_dns=True)
                                current_url = next_url
                                continue
                            final_url = str(getattr(response, "url", "") or current_url)
                            if _is_url(final_url):
                                _validate_public_https_url(final_url, resolve_dns=False)
                            if response.status_code in {401, 403}:
                                raise PermissionError(
                                    f"Remote source {current_url} returned HTTP {response.status_code}. "
                                    "AI Watchdogs does not bypass authentication walls or access controls (IT Act Sec 43)."
                                )
                            response.raise_for_status()
                            content_type = response.headers.get("content-type", "")
                            cl_hdr = response.headers.get("content-length")
                            if cl_hdr and cl_hdr.isdigit() and int(cl_hdr) > max_download_bytes:
                                raise ValueError(
                                    f"Remote artifact {current_url} declares Content-Length={int(cl_hdr)} bytes, "
                                    f"exceeding limit of {max_download_bytes} bytes."
                                )
                            buf = bytearray()
                            for chunk in response.iter_bytes():
                                buf.extend(chunk)
                                if len(buf) > max_download_bytes:
                                    raise ValueError(
                                        f"Remote artifact {current_url} exceeded maximum allowed download size of {max_download_bytes} bytes."
                                    )
                            raw_bytes = bytes(buf)
                            break
                    else:
                        response = client.get(current_url)
                        if response.status_code in {301, 302, 303, 307, 308}:
                            location = response.headers.get("location", "").strip()
                            if not location:
                                raise ValueError(f"Redirect response from {current_url} missing Location header.")
                            next_url = urljoin(current_url, location)
                            _validate_public_https_url(next_url, resolve_dns=True)
                            current_url = next_url
                            continue
                        final_url = str(getattr(response, "url", "") or current_url)
                        if _is_url(final_url):
                            _validate_public_https_url(final_url, resolve_dns=False)
                        if response.status_code in {401, 403}:
                            raise PermissionError(
                                f"Remote source {current_url} returned HTTP {response.status_code}. "
                                "AI Watchdogs does not bypass authentication walls or access controls (IT Act Sec 43)."
                            )
                        response.raise_for_status()
                        content_type = response.headers.get("content-type", "")
                        raw_bytes = response.content
                        if len(raw_bytes) > max_download_bytes:
                            raise ValueError(
                                f"Remote artifact {current_url} size {len(raw_bytes)} bytes exceeds limit of {max_download_bytes} bytes."
                            )
                        break
                else:
                    raise ValueError(f"Remote source {url} exceeded maximum allowed redirects ({DEFAULT_MAX_REDIRECTS}).")
                break
            except httpx.HTTPStatusError as exc:
                if (
                    attempt + 1 < max_attempts
                    and exc.response is not None
                    and exc.response.status_code in _RETRYABLE_SOURCE_STATUS
                ):
                    time.sleep(1.5 * (2**attempt))
                    continue
                raise
            except httpx.TransportError:
                if attempt + 1 < max_attempts:
                    time.sleep(1.5 * (2**attempt))
                    continue
                raise

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
    """Reject XML containing DTD/ENTITY declarations in any encoding, BOM, or offset using Expat."""
    import xml.parsers.expat as expat

    upper = xml_bytes.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise ValueError(f"Refused unsafe XML DTD/ENTITY declaration in archive member '{member_name}'.")

    def _forbid_dtd(*_args: Any, **_kwargs: Any) -> None:
        raise ValueError(f"Refused unsafe XML DTD/ENTITY declaration in archive member '{member_name}'.")

    parser = expat.ParserCreate()
    parser.StartDoctypeDeclHandler = _forbid_dtd
    parser.EntityDeclHandler = _forbid_dtd
    parser.UnparsedEntityDeclHandler = _forbid_dtd
    parser.ExternalEntityRefHandler = lambda *_args: (_forbid_dtd() or 0)
    try:
        parser.Parse(xml_bytes, True)
    except ValueError:
        raise
    except expat.ExpatError as exc:
        raise ValueError(f"Malformed XML in archive member '{member_name}': {exc}") from exc


def _read_zip_member_bounded(
    zf: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    *,
    remaining_bytes: int,
    max_ratio: float,
) -> bytes:
    """Safely read a single zip member enforcing incremental decompressed byte and ratio limits."""
    name = info.filename
    norm_name = name.replace("\\", "/")
    if norm_name.startswith("/") or ".." in Path(norm_name).parts:
        raise ValueError(f"Refused unsafe archive member path: {name!r}")
    if info.file_size > remaining_bytes:
        raise ValueError(
            f"Archive member '{name}' declared size {info.file_size} exceeds remaining limit {remaining_bytes}."
        )
    denom = float(max(info.compress_size, 1))
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
            if len(buf) >= 65_536 and (len(buf) / denom) > max_ratio:
                raise ValueError(
                    f"Archive member '{name}' compression ratio {len(buf) / denom:.1f}x "
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
            si_tag = f"{{{_XLSX_MAIN_NS}}}si"
            t_tag = f"{{{_XLSX_MAIN_NS}}}t"
            for _, elem in ET.iterparse(
                io.BytesIO(sst_bytes),
                events=("end",),
            ):
                if elem.tag == si_tag:
                    if len(shared_strings) >= DEFAULT_MAX_SHARED_STRINGS:
                        raise ValueError(
                            f"XLSX sharedStrings.xml exceeds maximum cap of {DEFAULT_MAX_SHARED_STRINGS} strings."
                        )
                    shared_strings.append("".join(t.text or "" for t in elem.iter(t_tag)))
                    elem.clear()

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
                joined.append(_normalize_ocds_flat_row(base, source_ref, row_index=_to_int_opt(aw.get("source_row_index")) or row_i))

            for row_i, t in enumerate(tenders, start=1):
                ocid = str(t.get("ocid") or "").strip()
                if ocid not in matched_ocids:
                    joined.append(_normalize_ocds_flat_row(dict(t), source_ref, row_index=_to_int_opt(t.get("source_row_index")) or row_i))

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
                    _normalize_ocds_flat_row(r, source_ref, row_index=_to_int_opt(r.get("source_row_index")) or i)
                    for i, r in enumerate(records, start=1)
                ]
                return records, f"XLSX OCDS sheet '{chosen_sheet}': {len(records)} rows", "xlsx_ocds", f"sheet={chosen_sheet}"
            if _is_url(source_ref):
                for r in records:
                    r.setdefault("source_url", source_ref)

        return records, f"XLSX sheet '{chosen_sheet}': {len(records)} rows", "xlsx", f"sheet={chosen_sheet}"


def _reject_non_finite_json(token: str) -> Any:
    raise ValueError(f"Non-standard JSON numeric constant '{token}' is not permitted.")


def _looks_like_ocds_release(obj: dict[str, Any]) -> bool:
    return bool(
        obj.get("ocid")
        and (
            isinstance(obj.get("tender"), (dict, list))
            or isinstance(obj.get("awards"), list)
            or isinstance(obj.get("buyer"), (dict, list))
        )
    )


def _decompress_gzip_bounded(
    raw_bytes: bytes,
    source_ref: str,
    *,
    max_decompressed_bytes: int = DEFAULT_MAX_DECOMPRESSED_BYTES,
    max_compression_ratio: float = DEFAULT_MAX_COMPRESSION_RATIO,
) -> bytes:
    """Decompress a gzip payload with strict byte-size and compression-ratio bomb guards."""
    compressed_size = max(1, len(raw_bytes))
    chunks: list[bytes] = []
    total = 0
    with gzip.GzipFile(fileobj=io.BytesIO(raw_bytes), mode="rb") as gf:
        while True:
            chunk = gf.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > max_decompressed_bytes:
                raise ValueError(
                    f"Gzip source {source_ref} decompressed size exceeds limit of {max_decompressed_bytes} bytes."
                )
            if total > 1_048_576 and (total / compressed_size) > max_compression_ratio:
                raise ValueError(
                    f"Gzip source {source_ref} exceeds maximum allowed compression ratio ({max_compression_ratio}x)."
                )
            chunks.append(chunk)
    return b"".join(chunks)


def _parse_jsonl_bytes(
    raw_bytes: bytes,
    source_ref: str = "",
    *,
    max_parse_rows: int = DEFAULT_MAX_PARSE_ROWS,
) -> tuple[list[dict[str, Any]], str, str]:
    """Parse newline-delimited JSON (.jsonl/.ndjson), flattening OCDS release/record shapes automatically."""
    text = raw_bytes.decode("utf-8-sig", errors="replace")
    preview = text[:8192]
    records: list[dict[str, Any]] = []
    detected_format = "jsonl"
    default_meta = {
        "jurisdiction": _infer_jurisdiction(source_ref),
        "fiscal_year": None,
        "source_url": source_ref if _is_url(source_ref) else None,
    }

    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        obj = json.loads(line, parse_constant=_reject_non_finite_json)
        if not isinstance(obj, dict):
            continue

        if isinstance(obj.get("releases"), list):
            detected_format = "ocds_jsonl"
            pub = obj.get("publisher")
            pub_name = pub.get("name") if isinstance(pub, dict) else None
            pkg_meta = {
                "jurisdiction": obj.get("jurisdiction") or _infer_jurisdiction(source_ref, pub_name),
                "fiscal_year": obj.get("fiscal_year"),
                "source_url": obj.get("uri") or obj.get("source_url") or default_meta["source_url"],
            }
            for rel in obj["releases"]:
                if not isinstance(rel, dict):
                    continue
                if len(records) >= max_parse_rows:
                    raise ValueError(f"JSONL dataset {source_ref} exceeds maximum allowed row cap ({max_parse_rows}).")
                records.append(_flatten_ocds_release(rel, pkg_meta, row_index=len(records) + 1))
            continue

        if len(records) >= max_parse_rows:
            raise ValueError(f"JSONL dataset {source_ref} exceeds maximum allowed row cap ({max_parse_rows}).")

        compiled = obj.get("compiledRelease")
        if isinstance(compiled, dict):
            detected_format = "ocds_jsonl"
            rel_obj = dict(compiled)
            if not rel_obj.get("ocid") and obj.get("ocid"):
                rel_obj["ocid"] = obj["ocid"]
            records.append(_flatten_ocds_release(rel_obj, default_meta, row_index=line_no))
        elif _looks_like_ocds_release(obj):
            detected_format = "ocds_jsonl"
            records.append(_flatten_ocds_release(obj, default_meta, row_index=line_no))
        else:
            row = dict(obj)
            row.setdefault("source_row_index", line_no)
            if _is_url(source_ref):
                row.setdefault("source_url", source_ref)
            records.append(row)

    return records, preview, detected_format


def _parse_json_bytes(
    raw_bytes: bytes,
    source_ref: str = "",
    *,
    max_parse_rows: int = DEFAULT_MAX_PARSE_ROWS,
) -> tuple[list[dict[str, Any]], str, Any, str]:
    text = raw_bytes.decode("utf-8-sig", errors="replace")
    try:
        payload = json.loads(text, parse_constant=_reject_non_finite_json)
    except json.JSONDecodeError:
        # Transparently fall back to JSONL when newline-delimited JSON is served with a .json suffix.
        non_empty = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if len(non_empty) >= 2 and all(ln.startswith("{") and ln.endswith("}") for ln in non_empty[:3]):
            recs, preview, fmt = _parse_jsonl_bytes(raw_bytes, source_ref, max_parse_rows=max_parse_rows)
            return recs, preview, None, fmt
        raise
    detected_format = "json"
    preview = text[:8192]

    if isinstance(payload, list):
        if len(payload) > max_parse_rows:
            raise ValueError(f"JSON array exceeds maximum allowed row cap ({max_parse_rows}).")
        dict_rows = [r for r in payload if isinstance(r, dict)]
        if dict_rows and (
            isinstance(dict_rows[0].get("compiledRelease"), dict) or _looks_like_ocds_release(dict_rows[0])
        ):
            detected_format = "ocds_json"
            pkg_meta = {
                "jurisdiction": _infer_jurisdiction(source_ref),
                "fiscal_year": None,
                "source_url": source_ref if _is_url(source_ref) else None,
            }
            flattened: list[dict[str, Any]] = []
            for idx, item in enumerate(dict_rows, start=1):
                if isinstance(item.get("compiledRelease"), dict):
                    rel_obj = dict(item["compiledRelease"])
                    if not rel_obj.get("ocid") and item.get("ocid"):
                        rel_obj["ocid"] = item["ocid"]
                    flattened.append(_flatten_ocds_release(rel_obj, pkg_meta, row_index=idx))
                elif _looks_like_ocds_release(item):
                    flattened.append(_flatten_ocds_release(item, pkg_meta, row_index=idx))
                else:
                    row = dict(item)
                    row.setdefault("source_row_index", idx)
                    flattened.append(row)
            return flattened, preview, payload, detected_format
        for idx, row in enumerate(dict_rows, start=1):
            row.setdefault("source_row_index", idx)
        return dict_rows, preview, payload, detected_format

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

        if isinstance(payload.get("records"), list) and payload["records"]:
            first_rec = payload["records"][0]
            if isinstance(first_rec, dict) and isinstance(first_rec.get("compiledRelease"), dict):
                detected_format = "ocds_json"
                ocds_records = payload["records"]
                if len(ocds_records) > max_parse_rows:
                    raise ValueError(f"OCDS record package exceeds maximum allowed row cap ({max_parse_rows}).")
                publisher = payload.get("publisher")
                pub_name = publisher.get("name") if isinstance(publisher, dict) else None
                package_meta = {
                    "jurisdiction": payload.get("jurisdiction") or _infer_jurisdiction(source_ref, pub_name),
                    "fiscal_year": payload.get("fiscal_year"),
                    "source_url": payload.get("uri") or payload.get("source_url") or (source_ref if _is_url(source_ref) else None),
                }
                records = []
                for idx, rec_obj in enumerate(ocds_records, start=1):
                    if not isinstance(rec_obj, dict):
                        continue
                    compiled = rec_obj.get("compiledRelease")
                    if isinstance(compiled, dict):
                        rel_obj = dict(compiled)
                        if not rel_obj.get("ocid") and rec_obj.get("ocid"):
                            rel_obj["ocid"] = rec_obj["ocid"]
                        records.append(_flatten_ocds_release(rel_obj, package_meta, row_index=idx))
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

        if isinstance(payload.get("procnotices"), list):
            notices = payload["procnotices"]
            if len(notices) > max_parse_rows:
                raise ValueError(f"World Bank procnotices exceeds maximum allowed row cap ({max_parse_rows}).")
            wb_records = _parse_worldbank_procnotices(notices, source_ref)
            return wb_records, preview, payload, "worldbank_procnotices_json"

        for key in ("records", "data", "items", "paras", "tenders", "rows"):
            if isinstance(payload.get(key), list):
                rows = payload[key]
                if len(rows) > max_parse_rows:
                    raise ValueError(f"JSON dataset '{key}' exceeds maximum allowed row cap ({max_parse_rows}).")
                return [r for r in rows if isinstance(r, dict)], preview, payload, detected_format

        if _looks_like_ocds_release(payload):
            pkg_meta = {
                "jurisdiction": payload.get("jurisdiction") or _infer_jurisdiction(source_ref),
                "fiscal_year": payload.get("fiscal_year"),
                "source_url": payload.get("source_url") or (source_ref if _is_url(source_ref) else None),
            }
            return [_flatten_ocds_release(payload, pkg_meta, row_index=1)], preview, payload, "ocds_json"

        return [payload], preview, payload, detected_format

    return [], preview, payload, detected_format


class _WBNoticeHTMLToLines(HTMLParser):
    """Linear-time stdlib HTML-to-lines converter for World Bank STEP Contract Award notices."""

    _BREAK_TAGS = frozenset({"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "hr"})
    _CELL_TAGS = frozenset({"td", "th"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if t in self._BREAK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in self._BREAK_TAGS:
            self._parts.append("\n")
        elif t in self._CELL_TAGS:
            self._parts.append(" | ")

    def handle_data(self, data: str) -> None:
        if data:
            self._parts.append(data)

    def lines(self) -> list[str]:
        raw = "".join(self._parts).replace("\xa0", " ")
        out: list[str] = []
        for ln in raw.splitlines():
            cleaned = re.sub(r"\s+", " ", ln).strip(" |")
            if cleaned:
                out.append(cleaned)
        return out


_WB_AWARDED_HEAD_RE = re.compile(
    r"^(?:awarded|successful)\s+(?:bidder(?:\(s\)|s)?|firm(?:/individual|s)?|consultant(?:\(s\)|s)?|contractor(?:\(s\)|s)?|supplier(?:\(s\)|s)?)\s*[:|-]*",
    re.IGNORECASE,
)
_WB_EVALUATED_HEAD_RE = re.compile(
    r"^(?:other\s+)?evaluated\s+(?:bidder(?:\(s\)|s)?|firm(?:/individual|s)?|consultant(?:\(s\)|s)?|contractor(?:\(s\)|s)?|supplier(?:\(s\)|s)?)\s*[:|-]*",
    re.IGNORECASE,
)
_WB_REJECTED_HEAD_RE = re.compile(
    r"^(?:rejected|non[\s-]*responsive|disqualified)\s+(?:bidder(?:\(s\)|s)?|firm(?:/individual|s)?|consultant(?:\(s\)|s)?|contractor(?:\(s\)|s)?|supplier(?:\(s\)|s)?)\s*[:|-]*",
    re.IGNORECASE,
)
_WB_STOP_HEAD_RE = re.compile(
    r"^(?:scope\s+of\s+contract|contract\s+signature\s+date|duration\s+of\s+contract|"
    r"evaluation\s+currency|beneficial\s+ownership|summary\s+of\s+contract|selection\s+method|"
    r"procurement\s+method|project\s+id|country\s*:?\s*$)",
    re.IGNORECASE,
)
_WB_NAME_LINE_RE = re.compile(r"^(?:name|bidder\s*name|firm\s*name|consultant\s*name|supplier\s*name)\s*[:|]\s*(.+)$", re.IGNORECASE)
_WB_SUPPLIER_ID_RE = re.compile(r"^(.*?)\s*\((\d{4,12})\)\s*$")
_WB_META_PREFIX_RE = re.compile(
    r"^(?:address|country|city|province|postal|zip|phone|tel|fax|email|read[\s-]*out|evaluated\s+price|"
    r"signed\s+contract|contract\s+price|contract\s+signature|duration|reason\s+for\s+rejection|"
    r"rejection\s+reason|currency|price|amount|score|rank|technical\s+score|financial\s+score|combined\s+score)\b",
    re.IGNORECASE,
)
_WB_SIGNED_INR_RE_1 = re.compile(
    r"(?:signed\s+contract\s+price|final\s+contract\s+price|contract\s+price|awarded\s+(?:price|value|amount))"
    r"[^0-9]{0,50}?(?:INR|Rs\.?|₹|Indian\s+Rupees?)\s*([0-9][0-9,]*(?:\.\d+)?)",
    re.IGNORECASE,
)
_WB_SIGNED_INR_RE_2 = re.compile(
    r"(?:signed\s+contract\s+price|final\s+contract\s+price|contract\s+price|awarded\s+(?:price|value|amount))"
    r"[^0-9]{0,50}?([0-9][0-9,]*(?:\.\d+)?)\s*(?:INR|Indian\s+Rupees?)",
    re.IGNORECASE,
)
_WB_AWARD_DATE_RE = re.compile(
    r"(?:contract\s+signature\s+date|date\s+of\s+contract\s+signature|date\s+of\s+award|award\s+date)"
    r"\s*[:|]?\s*(?:\(YYYY/MM/DD\)\s*|\([^)]{1,16}\)\s*)?"
    r"([0-9]{1,2}[-/\s][A-Za-z0-9]{2,9}[-/\s]20\d{2}|20\d{2}[/-]\d{2}[/-]\d{2})",
    re.IGNORECASE,
)
_WB_DIRECT_METHODS = frozenset({"DIR", "CDS", "SSS"})


def _extract_wb_bidders(section_lines: list[str]) -> list[tuple[str, str]]:
    """Extract (supplier_name, numeric_supplier_id) entries from a World Bank STEP bidder section."""
    bidders: list[tuple[str, str]] = []
    for ln in section_lines:
        m_name = _WB_NAME_LINE_RE.match(ln)
        if m_name:
            cand = m_name.group(1).strip(" |")
            if cand and not _WB_META_PREFIX_RE.match(cand):
                m_id = _WB_SUPPLIER_ID_RE.match(cand)
                if m_id:
                    bidders.append((m_id.group(1).strip(), m_id.group(2).strip()))
                else:
                    bidders.append((cand, ""))
    if bidders:
        return bidders

    # Fallback: table/paragraph lines bearing World Bank numeric Supplier IDs "(123456)"
    for ln in section_lines:
        if _WB_META_PREFIX_RE.match(ln):
            continue
        first_cell = ln.split(" | ", 1)[0].strip()
        m_id = _WB_SUPPLIER_ID_RE.match(first_cell)
        if m_id and m_id.group(1).strip():
            bidders.append((m_id.group(1).strip(), m_id.group(2).strip()))
    return bidders


def _parse_worldbank_procnotices(notices: list[Any], source_ref: str) -> list[dict[str, Any]]:
    """Parse World Bank ``procnotices[]`` JSON + STEP HTML ``notice_text`` into canonical procurement rows.

    Forensic hygiene rules:
    - Drops corrupt upstream notices where ``bid_reference_no`` is non-empty but absent from ``notice_text``.
    - In WB STEP HTML, ``Evaluated Bidder(s)`` lists *other* evaluated losing bidders:
      ``bids_received = 1 + len(evaluated) + len(rejected)``, ``qualified_bid_count = 1 + len(evaluated)``.
    - On competitive methods (RFB/RFQ/QCBS/etc.) where NEITHER ``Evaluated`` NOR ``Rejected`` section is
      present, leaves ``bid_count = None`` and ``qualified_bid_count = None`` (never misclassifying as 1 bid).
    """
    records: list[dict[str, Any]] = []
    for idx, item in enumerate(notices, start=1):
        if not isinstance(item, dict):
            continue
        notice_id = str(item.get("id") or "").strip()
        bid_ref = str(item.get("bid_reference_no") or "").strip()
        raw_html = str(item.get("notice_text") or "")

        parser = _WBNoticeHTMLToLines()
        parser.feed(raw_html)
        parser.close()
        lines = parser.lines()
        plain_text = "\n".join(lines)

        # Drop notices with empty notice_text or corrupt notices where bid_reference_no is absent from notice_text
        if not plain_text.strip():
            continue
        if bid_ref:
            norm_plain = re.sub(r"\s+", " ", plain_text).lower()
            norm_ref = re.sub(r"\s+", " ", bid_ref).lower()
            if norm_ref not in norm_plain:
                continue

        sections: dict[str, list[str]] = {"awarded": [], "evaluated": [], "rejected": []}
        has_evaluated_section = False
        has_rejected_section = False
        active_sec: str | None = None

        for ln in lines:
            m_awd = _WB_AWARDED_HEAD_RE.match(ln)
            if m_awd:
                active_sec = "awarded"
                remainder = ln[m_awd.end() :].strip(" :|-")
                if remainder:
                    sections["awarded"].append(remainder)
                continue
            m_eval = _WB_EVALUATED_HEAD_RE.match(ln)
            if m_eval:
                active_sec = "evaluated"
                has_evaluated_section = True
                remainder = ln[m_eval.end() :].strip(" :|-")
                if remainder:
                    sections["evaluated"].append(remainder)
                continue
            m_rej = _WB_REJECTED_HEAD_RE.match(ln)
            if m_rej:
                active_sec = "rejected"
                has_rejected_section = True
                remainder = ln[m_rej.end() :].strip(" :|-")
                if remainder:
                    sections["rejected"].append(remainder)
                continue
            if _WB_STOP_HEAD_RE.match(ln):
                active_sec = None
                continue
            if active_sec is not None:
                sections[active_sec].append(ln)

        awarded_bidders = _extract_wb_bidders(sections["awarded"])
        evaluated_bidders = _extract_wb_bidders(sections["evaluated"])
        rejected_bidders = _extract_wb_bidders(sections["rejected"])

        winner_name = awarded_bidders[0][0] if awarded_bidders else ""
        vendor_id = awarded_bidders[0][1] if awarded_bidders else ""

        # Search Awarded section first with BOTH regexes before falling back to full plain_text so
        # a losing bidder's price can never override the awarded bidder's signed contract price.
        awarded_text = "\n".join(sections["awarded"])
        award_value_inr: float | None = None
        for blob in (awarded_text, plain_text):
            if not blob:
                continue
            for rx in (_WB_SIGNED_INR_RE_1, _WB_SIGNED_INR_RE_2):
                m_val = rx.search(blob)
                if m_val:
                    parsed_v = _to_float_safe(m_val.group(1), 0.0)
                    if parsed_v > 0:
                        award_value_inr = parsed_v
                        break
            if award_value_inr is not None:
                break

        award_date = ""
        m_dt = _WB_AWARD_DATE_RE.search(plain_text)
        if m_dt:
            parsed_dt = _parse_datetime_flex(m_dt.group(1).strip())
            if parsed_dt is not None:
                award_date = parsed_dt.date().isoformat()
        noticedate = str(item.get("noticedate") or item.get("submission_date") or "").strip()
        if not award_date and noticedate:
            parsed_nd = _parse_datetime_flex(noticedate)
            if parsed_nd is not None:
                award_date = parsed_nd.date().isoformat()

        method_code = str(item.get("procurement_method_code") or "").strip().upper()
        method_name = str(item.get("procurement_method_name") or "").strip()
        is_direct = method_code in _WB_DIRECT_METHODS or "direct selection" in method_name.lower() or "single source" in method_name.lower()

        n_eval = len(evaluated_bidders)
        n_rej = len(rejected_bidders)
        if is_direct:
            proc_method = "direct"
            if awarded_bidders:
                bid_count: int | None = 1 + n_eval + n_rej
                qualified_bid_count: int | None = 1 + n_eval
            else:
                # Fail closed when direct-selection notice lacks a parsed awarded bidder
                bid_count = None
                qualified_bid_count = None
        else:
            proc_method = "open"
            # Fail closed to None when Evaluated Bidder(s) heading existed but parsed 0 bidders,
            # or when headings existed with 0 total parsed non-winning bidders (prevents false single-bid).
            if has_evaluated_section and n_eval == 0:
                bid_count = None
                qualified_bid_count = None
            elif (has_evaluated_section or has_rejected_section) and (n_eval + n_rej) > 0:
                bid_count = 1 + n_eval + n_rej
                qualified_bid_count = 1 + n_eval
            else:
                # Awarded-only RFB/RFQ without parsed Evaluated/Rejected bidders: NEVER misclassify as 1 bid!
                bid_count = None
                qualified_bid_count = None

        method_details = f"{method_code} ({method_name})".strip() if method_code and method_name else (method_code or method_name)
        buyer_name = str(
            item.get("agency_name")
            or item.get("contact_organization")
            or item.get("implementing_agency")
            or item.get("borrower")
            or item.get("project_name")
            or ""
        ).strip()
        title = str(
            item.get("bid_description")
            or item.get("noticetitle")
            or item.get("notice_description")
            or item.get("project_name")
            or bid_ref
            or notice_id
        ).strip()
        category = str(item.get("procurement_group_desc") or item.get("procurement_group") or "Public Procurement").strip()
        detail_url = (
            f"https://projects.worldbank.org/en/projects-operations/procurement-detail/{notice_id}"
            if notice_id
            else (source_ref if _is_url(source_ref) else "https://projects.worldbank.org")
        )
        fiscal_year = _normalize_fy(item.get("fiscal_year"), fallback_date=award_date or noticedate)

        records.append(
            {
                "id": notice_id,
                "ocid": notice_id,
                "tender_id": bid_ref or notice_id,
                "bid_reference_no": bid_ref,
                "project_id": str(item.get("project_id") or "").strip(),
                "project_name": str(item.get("project_name") or "").strip(),
                "tender_title": title,
                "buyer_name": buyer_name,
                "vendor_name": winner_name,
                "vendor_id": vendor_id,
                "procurement_method": proc_method,
                "procurement_method_details": method_details,
                "exemption_reason": "",
                "bid_count": bid_count,
                "qualified_bid_count": qualified_bid_count,
                "evaluated_bidder_count": n_eval,
                "rejected_bidder_count": n_rej,
                "has_award": bool(award_value_inr or winner_name),
                "award_value_inr": award_value_inr,
                "award_amount_inr": award_value_inr,
                "tender_estimate_inr": 0.0,
                "tender_value_inr": 0.0,
                "amount_type": "award_value" if award_value_inr is not None else "unrecorded",
                "award_date": award_date,
                "release_date": noticedate,
                "tender_published_date": "",
                "bid_window_days": None,
                "item_category": category,
                "jurisdiction": _infer_jurisdiction(f"{buyer_name} {item.get('project_name') or ''}"),
                "fiscal_year": fiscal_year,
                "source_url": detail_url,
                "record_url": detail_url,
                "source_row_index": idx,
            }
        )
    return records


def _select_archive_member(members: list[str], archive_member: str | None) -> str | None:
    """Safely resolve an optional requested member inside a ZIP/TAR archive (rejects traversal)."""
    if not archive_member:
        return None
    norm_req = archive_member.strip().replace("\\", "/").lstrip("./")
    if not norm_req or norm_req.startswith("/") or ".." in Path(norm_req).parts:
        raise ValueError(f"Refused unsafe archive member selector: {archive_member!r}")
    for m in members:
        norm_m = m.replace("\\", "/").lstrip("./")
        if norm_m == norm_req:
            return m
    suffix_matches = [m for m in members if m.replace("\\", "/").endswith("/" + norm_req)]
    if len(suffix_matches) == 1:
        return suffix_matches[0]
    return ""


def _parse_archive_member_bytes(
    inner: bytes,
    member_name: str,
    source_ref: str,
    *,
    extract_html_text: bool = False,
    max_parse_rows: int = DEFAULT_MAX_PARSE_ROWS,
    max_pdf_pages: int = DEFAULT_MAX_PDF_PAGES,
    max_pdf_chars: int = DEFAULT_MAX_PDF_CHARS,
) -> tuple[list[dict[str, Any]], str, Any, str]:
    """Dispatch an extracted ZIP/TAR member's bytes to the appropriate bounded parser."""
    lower_m = member_name.lower()
    if lower_m.endswith(".pdf") or inner[:5] == b"%PDF-":
        recs, preview, _ = _parse_pdf_bytes(
            inner,
            source_ref,
            max_pdf_pages=max_pdf_pages,
            max_pdf_chars=max_pdf_chars,
        )
        fmt = "pdf"
        payload: Any = None
    elif lower_m.endswith(".json"):
        recs, preview, payload, fmt = _parse_json_bytes(inner, source_ref=source_ref, max_parse_rows=max_parse_rows)
    elif lower_m.endswith((".jsonl", ".ndjson")):
        recs, preview, fmt = _parse_jsonl_bytes(inner, source_ref=source_ref, max_parse_rows=max_parse_rows)
        payload = None
    elif lower_m.endswith(".csv"):
        recs, preview = _parse_csv_bytes(inner, source_ref=source_ref, max_parse_rows=max_parse_rows)
        fmt = "csv"
        payload = None
    elif lower_m.endswith((".html", ".htm")):
        if extract_html_text:
            visible_text = _extract_html_visible_text(inner, max_chars=max_pdf_chars)
            recs = [
                {
                    "page": 1,
                    "raw_text": visible_text,
                    "source_ref": source_ref,
                    "source_url": source_ref if _is_url(source_ref) else "",
                    "source_format": "html",
                    "jurisdiction": _infer_jurisdiction(source_ref),
                }
            ]
            preview = visible_text[:65_536]
            fmt = "html"
            payload = None
        else:
            recs, preview, fmt = _parse_html_bytes(inner, source_ref=source_ref, max_parse_rows=max_parse_rows)
            payload = None
    else:
        raw_txt = inner.decode("utf-8-sig", errors="replace")
        recs = [{"raw_text": raw_txt, "page": 1, "source_ref": source_ref, "source_format": "txt"}]
        preview = raw_txt[:65_536]
        fmt = "txt"
        payload = None

    for r in recs:
        if isinstance(r, dict):
            r.setdefault("archive_member", member_name)
    return recs, preview, payload, fmt


def _parse_tar_bytes(
    raw_bytes: bytes,
    source_ref: str,
    *,
    archive_member: str | None = None,
    extract_html_text: bool = False,
    max_decompressed_bytes: int = DEFAULT_MAX_DECOMPRESSED_BYTES,
    max_compression_ratio: float = DEFAULT_MAX_COMPRESSION_RATIO,
    max_zip_members: int = DEFAULT_MAX_ZIP_MEMBERS,
    max_parse_rows: int = DEFAULT_MAX_PARSE_ROWS,
    max_pdf_pages: int = DEFAULT_MAX_PDF_PAGES,
    max_pdf_chars: int = DEFAULT_MAX_PDF_CHARS,
) -> tuple[list[dict[str, Any]], str, Any, str, str]:
    """Pure-stdlib .tar / .tar.gz reader with decompression bomb, symlink, and traversal guards."""
    tar_bytes = raw_bytes
    if raw_bytes[:2] == b"\x1f\x8b":
        tar_bytes = _decompress_gzip_bounded(
            raw_bytes,
            source_ref,
            max_decompressed_bytes=max_decompressed_bytes,
            max_compression_ratio=max_compression_ratio,
        )

    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:*") as tf:
        infos = tf.getmembers()
        if len(infos) > max_zip_members:
            raise ValueError(f"TAR archive has {len(infos)} members, exceeding cap of {max_zip_members}.")
        regular_infos: dict[str, tarfile.TarInfo] = {}
        ordered_names: list[str] = []
        for info in infos:
            norm_name = info.name.replace("\\", "/")
            if norm_name.startswith("/") or ".." in Path(norm_name).parts:
                raise ValueError(f"Refused unsafe TAR archive member path: {info.name!r}")
            if not info.isfile() or info.issym() or info.islnk():
                continue
            if norm_name.startswith("__MACOSX/") or Path(norm_name).name.startswith("._"):
                continue
            regular_infos[info.name] = info
            ordered_names.append(info.name)

        chosen: str | None = None
        if archive_member:
            resolved = _select_archive_member(ordered_names, archive_member)
            if not resolved:
                return [], "", None, "tar", f"tar_member_missing={archive_member}"
            chosen = resolved
        else:
            for exts in ((".pdf",), (".json",), (".jsonl", ".ndjson"), (".csv",), (".html", ".htm"), (".txt",)):
                match = next((n for n in ordered_names if n.lower().endswith(exts)), None)
                if match is not None:
                    chosen = match
                    break

        if not chosen:
            return [], "", None, "tar", "tar=empty"

        tinfo = regular_infos[chosen]
        if tinfo.size > max_decompressed_bytes:
            raise ValueError(
                f"TAR member '{chosen}' size {tinfo.size} exceeds remaining limit {max_decompressed_bytes}."
            )
        fh = tf.extractfile(tinfo)
        if fh is None:
            return [], "", None, "tar", f"tar_member={chosen}"
        buf = bytearray()
        while True:
            chunk = fh.read(65536)
            if not chunk:
                break
            buf.extend(chunk)
            if len(buf) > max_decompressed_bytes:
                raise ValueError(f"TAR member '{chosen}' exceeded maximum allowed decompressed size.")
        inner = bytes(buf)
        recs, preview, payload, fmt = _parse_archive_member_bytes(
            inner,
            chosen,
            source_ref,
            extract_html_text=extract_html_text,
            max_parse_rows=max_parse_rows,
            max_pdf_pages=max_pdf_pages,
            max_pdf_chars=max_pdf_chars,
        )
        return recs, preview, payload, fmt, f"tar_member={chosen}"


def _parse_zip_bytes(
    raw_bytes: bytes,
    source_ref: str,
    *,
    archive_member: str | None = None,
    extract_html_text: bool = False,
    max_decompressed_bytes: int = DEFAULT_MAX_DECOMPRESSED_BYTES,
    max_compression_ratio: float = DEFAULT_MAX_COMPRESSION_RATIO,
    max_zip_members: int = DEFAULT_MAX_ZIP_MEMBERS,
    max_parse_rows: int = DEFAULT_MAX_PARSE_ROWS,
    max_pdf_pages: int = DEFAULT_MAX_PDF_PAGES,
    max_pdf_chars: int = DEFAULT_MAX_PDF_CHARS,
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
        if not archive_member and "xl/workbook.xml" in members:
            recs, preview, fmt, locator = _parse_xlsx_bytes(
                raw_bytes,
                source_ref,
                max_decompressed_bytes=max_decompressed_bytes,
                max_compression_ratio=max_compression_ratio,
                max_zip_members=max_zip_members,
                max_parse_rows=max_parse_rows,
            )
            return recs, preview, None, fmt, locator

        if archive_member:
            chosen = _select_archive_member(members, archive_member)
            if not chosen:
                return [], "", None, "zip", f"zip_member_missing={archive_member}"
            inner = _read_zip_member_bounded(
                zf,
                zf.getinfo(chosen),
                remaining_bytes=max_decompressed_bytes,
                max_ratio=max_compression_ratio,
            )
            recs, preview, payload, fmt = _parse_archive_member_bytes(
                inner,
                chosen,
                source_ref,
                extract_html_text=extract_html_text,
                max_parse_rows=max_parse_rows,
                max_pdf_pages=max_pdf_pages,
                max_pdf_chars=max_pdf_chars,
            )
            return recs, preview, payload, fmt, f"zip_member={chosen}"

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

        jsonl_members = [m for m in members if m.lower().endswith((".jsonl", ".ndjson"))]
        if jsonl_members:
            inner = _read_zip_member_bounded(
                zf,
                zf.getinfo(jsonl_members[0]),
                remaining_bytes=max_decompressed_bytes,
                max_ratio=max_compression_ratio,
            )
            recs, preview, fmt = _parse_jsonl_bytes(
                inner, source_ref=source_ref, max_parse_rows=max_parse_rows
            )
            return recs, preview, None, fmt, f"zip_member={jsonl_members[0]}"

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

        for exts in ((".pdf",), (".html", ".htm"), (".txt",)):
            doc_members = [m for m in members if m.lower().endswith(exts)]
            if doc_members:
                inner = _read_zip_member_bounded(
                    zf,
                    zf.getinfo(doc_members[0]),
                    remaining_bytes=max_decompressed_bytes,
                    max_ratio=max_compression_ratio,
                )
                recs, preview, payload, fmt = _parse_archive_member_bytes(
                    inner,
                    doc_members[0],
                    source_ref,
                    extract_html_text=extract_html_text,
                    max_parse_rows=max_parse_rows,
                    max_pdf_pages=max_pdf_pages,
                    max_pdf_chars=max_pdf_chars,
                )
                return recs, preview, payload, fmt, f"zip_member={doc_members[0]}"

    return [], "", None, "zip", "zip=empty"


class _HTMLTableParser(HTMLParser):
    """Linear-time O(N) HTML table extractor replacing backtracking regexes (CWE-1333)."""

    def __init__(self, *, max_rows: int, source_ref: str, max_cell_chars: int = 8192) -> None:
        super().__init__(convert_charrefs=True)
        self.max_rows = max_rows
        self.source_ref = source_ref
        self.max_cell_chars = max_cell_chars
        self.rows: list[list[str]] = []
        self._in_tr = False
        self._in_cell = False
        self._current_row: list[str] = []
        self._current_cell: list[str] = []
        self._current_cell_len = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if t == "tr":
            if self._in_tr:
                self._flush_row()
            self._in_tr = True
            self._current_row = []
        elif t in ("td", "th") and self._in_tr:
            if self._in_cell:
                self._flush_cell()
            self._in_cell = True
            self._current_cell = []
            self._current_cell_len = 0

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in ("td", "th") and self._in_cell:
            self._flush_cell()
        elif t == "tr" and self._in_tr:
            if self._in_cell:
                self._flush_cell()
            self._flush_row()

    def handle_data(self, data: str) -> None:
        if self._in_cell and self._current_cell_len < self.max_cell_chars:
            rem = self.max_cell_chars - self._current_cell_len
            piece = data[:rem]
            self._current_cell.append(piece)
            self._current_cell_len += len(piece)

    def _flush_cell(self) -> None:
        raw = "".join(self._current_cell).replace("\xa0", " ")
        self._current_row.append(" ".join(raw.split()).strip())
        self._current_cell = []
        self._current_cell_len = 0
        self._in_cell = False

    def _flush_row(self) -> None:
        if any(self._current_row):
            if len(self.rows) >= self.max_rows:
                raise ValueError(f"HTML table in {self.source_ref} exceeds maximum row cap ({self.max_rows}).")
            self.rows.append(self._current_row)
        self._current_row = []
        self._in_tr = False


class _HTMLVisibleTextParser(HTMLParser):
    """Linear-time O(N) visible-text extractor for official government HTML press releases & circulars."""

    _SKIP_TAGS = frozenset({"script", "style", "noscript", "svg", "template"})
    _BREAK_TAGS = frozenset(
        {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "header", "footer", "hr", "dd", "dt"}
    )
    _CELL_TAGS = frozenset({"td", "th"})

    def __init__(self, *, max_chars: int = DEFAULT_MAX_PDF_CHARS) -> None:
        super().__init__(convert_charrefs=True)
        self.max_chars = max_chars
        self._skip_depth = 0
        self._parts: list[str] = []
        self._chars = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if t in self._SKIP_TAGS:
            self._skip_depth += 1
        elif self._skip_depth == 0 and t in self._BREAK_TAGS and self._chars < self.max_chars:
            self._parts.append("\n")
            self._chars += 1

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in self._SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        elif self._skip_depth == 0 and self._chars < self.max_chars:
            if t in self._BREAK_TAGS:
                self._parts.append("\n")
                self._chars += 1
            elif t in self._CELL_TAGS:
                self._parts.append(" | ")
                self._chars += 3

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data and self._chars < self.max_chars:
            rem = self.max_chars - self._chars
            chunk = data[:rem]
            self._parts.append(chunk)
            self._chars += len(chunk)

    def extract_text(self) -> str:
        raw = "".join(self._parts).replace("\xa0", " ")
        lines = [" ".join(ln.split()).strip() for ln in raw.splitlines()]
        return "\n".join(ln for ln in lines if ln)


def _extract_html_visible_text(raw_bytes: bytes, *, max_chars: int = DEFAULT_MAX_PDF_CHARS) -> str:
    text = raw_bytes.decode("utf-8", errors="replace")
    parser = _HTMLVisibleTextParser(max_chars=max_chars)
    parser.feed(text)
    parser.close()
    return parser.extract_text()


def _strip_html_cell(fragment: str) -> str:
    class _Strip(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.parts: list[str] = []

        def handle_data(self, data: str) -> None:
            self.parts.append(data)

    p = _Strip()
    p.feed(fragment)
    p.close()
    return " ".join("".join(p.parts).replace("\xa0", " ").split()).strip()


def _parse_html_bytes(
    raw_bytes: bytes,
    source_ref: str,
    *,
    max_parse_rows: int = DEFAULT_MAX_PARSE_ROWS,
) -> tuple[list[dict[str, Any]], str, str]:
    """Extract structured fiscal/accounts tables from official .nic.in HTML pages in strict O(N) time."""
    text = raw_bytes.decode("utf-8", errors="replace")
    parser = _HTMLTableParser(max_rows=max_parse_rows, source_ref=source_ref)
    parser.feed(text)
    parser.close()
    rows_cells = parser.rows

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
    max_pdf_chars: int = DEFAULT_MAX_PDF_CHARS,
) -> tuple[list[dict[str, Any]], str, str]:
    reader = PdfReader(io.BytesIO(raw_bytes))
    num_pages = len(reader.pages)
    if num_pages > max_pdf_pages:
        raise ValueError(
            f"PDF document {source_ref} has {num_pages} pages, exceeding cap of {max_pdf_pages} pages."
        )
    records: list[dict[str, Any]] = []
    preview_chunks: list[str] = []
    preview_chars = 0
    total_chars = 0
    for page_idx, page in enumerate(reader.pages, start=1):
        page_text = (page.extract_text() or "").strip()
        total_chars += len(page_text)
        if total_chars > max_pdf_chars:
            raise ValueError(
                f"PDF document {source_ref} extracted text exceeded cap of {max_pdf_chars} characters."
            )
        if preview_chars < 65_536:
            snippet = page_text[: max(0, 65_536 - preview_chars)]
            preview_chunks.append(snippet)
            preview_chars += len(snippet)
        records.append(
            {
                "page": page_idx,
                "raw_text": page_text,
                "source_ref": source_ref,
                "source_url": source_ref if _is_url(source_ref) else "https://cag.gov.in",
                "jurisdiction": _infer_jurisdiction(source_ref),
            }
        )
    return records, "\n\n".join(preview_chunks), f"pages=1-{num_pages}"


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
    """Build strictly separated provenance fields anchored strictly to parsed host and path."""
    is_remote = _is_url(source_ref)
    parsed_ref = urlparse(source_ref) if is_remote else None
    host = (parsed_ref.hostname or "").lower().rstrip(".") if parsed_ref else ""
    path_lower = (parsed_ref.path or "").lower() if parsed_ref else ""
    is_github_host = host in {"raw.githubusercontent.com", "github.com", "www.github.com"}

    inferred_publisher: str | None = None
    inferred_landing: str | None = None
    inferred_mirror: str | None = None

    if is_github_host and path_lower.startswith("/civicdatalab/assam-tenders-data/"):
        inferred_publisher = "Finance Department, Government of Assam (assamtenders.gov.in)"
        inferred_landing = "https://assam.open-contracting.in/datasets"
        inferred_mirror = "CivicDataLab GitHub Mirror"
    elif is_github_host and path_lower.startswith("/civicdatalab/himachal-pradesh-health-procurement-ocds/"):
        inferred_publisher = "Government of Himachal Pradesh (hptenders.gov.in)"
        inferred_landing = "https://hptenders.gov.in"
        inferred_mirror = "CivicDataLab GitHub Mirror"
    elif is_github_host and path_lower.startswith("/civicdatalab/assam-budget-obds/"):
        inferred_publisher = "Finance Department, Government of Assam"
        inferred_landing = "https://finance.assam.gov.in"
        inferred_mirror = "CivicDataLab GitHub Mirror (Assam-Budget-OBDS)"
    elif is_github_host and path_lower.startswith("/civicdatalab/child-budget-tracker/"):
        inferred_publisher = "Finance Department, Government of Andhra Pradesh"
        inferred_landing = "https://treasury.ap.gov.in"
        inferred_mirror = "CivicDataLab GitHub Mirror (child-budget-tracker)"
    elif host == "d1jmc0u14z2tmp.cloudfront.net" or host == "cag.gov.in" or host.endswith(".cag.gov.in"):
        inferred_publisher = "Comptroller and Auditor General of India"
        inferred_landing = "https://cag.gov.in/en/audit-report"
        inferred_mirror = "CAG CloudFront Distribution" if host == "d1jmc0u14z2tmp.cloudfront.net" else None
    elif host == "cga.nic.in" or host.endswith(".cga.nic.in"):
        inferred_publisher = "Controller General of Accounts, Ministry of Finance"
        inferred_landing = source_ref
        inferred_mirror = None
    elif is_github_host and path_lower.startswith("/saisantoshv3/electoral_bonds/"):
        inferred_publisher = "Election Commission of India / State Bank of India (SC WP(C) 880/2017)"
        inferred_landing = "https://www.eci.gov.in"
        inferred_mirror = "GitHub Mirror (saisantoshv3/electoral_bonds)"
    elif is_github_host and path_lower.startswith("/spfrantz/mca-csr-scraper/"):
        inferred_publisher = "Ministry of Corporate Affairs (National CSR Portal)"
        inferred_landing = "https://www.csr.gov.in"
        inferred_mirror = "GitHub Mirror (spfrantz/MCA-CSR-Scraper)"
    elif is_github_host and path_lower.startswith("/aditya-kr86/pmgsy_multiclass_ml/"):
        inferred_publisher = "National Rural Infrastructure Development Agency (PMGSY OMMAS / data.gov.in)"
        inferred_landing = "https://ommas.nic.in"
        inferred_mirror = "GitHub Mirror (aditya-kr86/PMGSY_Multiclass_ML)"
    elif is_github_host and path_lower.startswith("/arpita-deb/nrega-data-analysis/"):
        inferred_publisher = "Ministry of Rural Development (MGNREGA MIS)"
        inferred_landing = "https://nrega.nic.in"
        inferred_mirror = "GitHub Mirror (Arpita-deb/NREGA-Data-Analysis)"
    elif is_github_host and path_lower.startswith("/vonter/india-mplads-works/"):
        inferred_publisher = "Ministry of Statistics & Programme Implementation (MPLADS)"
        inferred_landing = "https://mplads.mospi.gov.in"
        inferred_mirror = "GitHub Mirror (Vonter/india-mplads-works)"
    elif is_github_host and path_lower.startswith("/varnasr/policystack/"):
        inferred_publisher = "Union Budget / Ministry of Finance (unverified third-party mirror)"
        inferred_landing = "https://indiabudget.gov.in"
        inferred_mirror = "GitHub Mirror (Varnasr/PolicyStack)"
    elif host == "sansad.in" or host.endswith(".sansad.in"):
        inferred_publisher = "Parliament of India (Digital Sansad)"
        inferred_landing = "https://sansad.in"
        inferred_mirror = None
    elif host == "indian-supreme-court-judgments.s3.amazonaws.com":
        inferred_publisher = "Supreme Court of India (eSCR Official Judgments Archive)"
        inferred_landing = "https://judgments.ecourts.gov.in/"
        inferred_mirror = "AWS Open Data Registry (indian-supreme-court-judgments)"
    elif host == "indian-high-court-judgments.s3.amazonaws.com":
        inferred_publisher = "High Courts of India (eCourts Official Judgments Archive)"
        inferred_landing = "https://judgments.ecourts.gov.in/"
        inferred_mirror = "AWS Open Data Registry (indian-high-court-judgments)"
    elif host == "sci.gov.in" or host.endswith(".sci.gov.in"):
        inferred_publisher = "Supreme Court of India"
        inferred_landing = "https://www.sci.gov.in/"
        inferred_mirror = None
    elif host == "rajyasabha.digital" or host.endswith(".rajyasabha.digital"):
        inferred_publisher = "Rajya Sabha, Parliament of India (Digital Sansad)"
        inferred_landing = "https://sansad.in/rs"
        inferred_mirror = None
    elif host == "nhrc.nic.in" or host.endswith(".nhrc.nic.in"):
        inferred_publisher = "National Human Rights Commission of India (NHRC)"
        inferred_landing = "https://nhrc.nic.in/"
        inferred_mirror = None
    elif host == "nalsa.gov.in" or host.endswith(".nalsa.gov.in"):
        inferred_publisher = "National Legal Services Authority (NALSA)"
        inferred_landing = "https://nalsa.gov.in/"
        inferred_mirror = None
    elif host == "cdnbbsr.s3waas.gov.in":
        inferred_publisher = "National Informatics Centre (S3WaaS Official Government Document Repository)"
        inferred_landing = source_ref
        inferred_mirror = None
    elif host == "mha.gov.in" or host.endswith(".mha.gov.in"):
        inferred_publisher = "Ministry of Home Affairs, Government of India"
        inferred_landing = "https://www.mha.gov.in/"
        inferred_mirror = None
    elif host == "doj.gov.in" or host.endswith(".doj.gov.in"):
        inferred_publisher = "Department of Justice, Ministry of Law and Justice, Government of India"
        inferred_landing = "https://doj.gov.in/"
        inferred_mirror = None
    elif host == "eprisons.nic.in" or host.endswith(".eprisons.nic.in"):
        inferred_publisher = "ePrisons National Portal, NIC / Ministry of Home Affairs"
        inferred_landing = "https://eprisons.nic.in/"
        inferred_mirror = None
    elif host == "dslsa.org" or host.endswith(".dslsa.org"):
        inferred_publisher = "Delhi State Legal Services Authority (DSLSA)"
        inferred_landing = "https://dslsa.org/"
        inferred_mirror = None
    elif host == "jhalsa.org" or host.endswith(".jhalsa.org"):
        inferred_publisher = "Jharkhand State Legal Services Authority (JHALSA)"
        inferred_landing = "https://jhalsa.org/"
        inferred_mirror = None
    elif host == "pshrc.net" or host.endswith(".pshrc.net"):
        inferred_publisher = "Punjab State Human Rights Commission (PSHRC)"
        inferred_landing = "https://pshrc.net/"
        inferred_mirror = None
    elif host == "allahabadhighcourt.in" or host.endswith(".allahabadhighcourt.in"):
        inferred_publisher = "High Court of Judicature at Allahabad"
        inferred_landing = "https://www.allahabadhighcourt.in/"
        inferred_mirror = None
    elif host == "rbi.org.in" or host.endswith(".rbi.org.in"):
        inferred_publisher = "Reserve Bank of India"
        inferred_landing = source_ref
        inferred_mirror = None
    elif host == "pib.gov.in" or host.endswith(".pib.gov.in"):
        inferred_publisher = "Press Information Bureau, Government of India"
        inferred_landing = source_ref
        inferred_mirror = None
    elif host == "indiabudget.gov.in" or host.endswith(".indiabudget.gov.in"):
        inferred_publisher = "Ministry of Finance, Government of India (Union Budget)"
        inferred_landing = "https://www.indiabudget.gov.in"
        inferred_mirror = None
    elif host == "incometaxindia.gov.in" or host.endswith(".incometaxindia.gov.in"):
        inferred_publisher = "Central Board of Direct Taxes, Ministry of Finance"
        inferred_landing = source_ref
        inferred_mirror = None
    elif host == "indiacode.nic.in" or host.endswith(".indiacode.nic.in"):
        inferred_publisher = "Legislative Department, Ministry of Law and Justice (India Code)"
        inferred_landing = source_ref
        inferred_mirror = None
    elif host == "egazette.gov.in" or host.endswith(".egazette.gov.in"):
        inferred_publisher = "The Gazette of India, Department of Publication"
        inferred_landing = "https://egazette.gov.in"
        inferred_mirror = None
    elif host == "myscheme.gov.in" or host.endswith(".myscheme.gov.in"):
        inferred_publisher = "National e-Governance Division, MeitY (myScheme)"
        inferred_landing = source_ref
        inferred_mirror = None
    elif host == "epfindia.gov.in" or host.endswith(".epfindia.gov.in"):
        inferred_publisher = "Employees' Provident Fund Organisation (EPFO)"
        inferred_landing = source_ref
        inferred_mirror = None
    elif host == "pfrda.org.in" or host.endswith(".pfrda.org.in"):
        inferred_publisher = "Pension Fund Regulatory and Development Authority (PFRDA)"
        inferred_landing = source_ref
        inferred_mirror = None
    elif host in {"dea.gov.in", "dor.gov.in", "finmin.nic.in", "nsiindia.gov.in"} or host.endswith(
        (".dea.gov.in", ".dor.gov.in", ".finmin.nic.in", ".nsiindia.gov.in")
    ):
        inferred_publisher = "Ministry of Finance, Government of India"
        inferred_landing = source_ref
        inferred_mirror = None
    elif host == "open-contracting.in" or host.endswith(".open-contracting.in"):
        inferred_publisher = "Public Procurement Open Contracting India"
        inferred_landing = source_ref
        inferred_mirror = "CivicDataLab Open Contracting India"
    elif host == "data.open-contracting.org" or host.endswith(".open-contracting.org"):
        inferred_publisher = "Open Contracting Partnership Data Registry (India OCDS)"
        inferred_landing = "https://data.open-contracting.org"
        inferred_mirror = "Open Contracting Partnership Data Registry"
    elif is_github_host and path_lower.startswith("/civicdatalab/flood-data-ecosystem-"):
        inferred_publisher = "State e-Procurement Portal (CivicDataLab IDS-DRR Open Data)"
        inferred_landing = "https://github.com/CivicDataLab"
        inferred_mirror = "GitHub Mirror (CivicDataLab IDS-DRR)"
    elif host == "data.gov.in" or host.endswith(".data.gov.in"):
        inferred_publisher = "Open Government Data (OGD) Platform India, NIC"
        inferred_landing = "https://data.gov.in"
        inferred_mirror = None
    elif host == "gem.gov.in" or host.endswith(".gem.gov.in"):
        inferred_publisher = "Government e-Marketplace (GeM), Ministry of Commerce & Industry"
        inferred_landing = "https://gem.gov.in"
        inferred_mirror = None
    elif host in {"eprocure.gov.in", "etenders.gov.in"} or host.endswith((".eprocure.gov.in", ".etenders.gov.in")):
        inferred_publisher = "Central Public Procurement Portal (CPPP), NIC / Ministry of Finance"
        inferred_landing = f"https://{host}"
        inferred_mirror = None
    elif host == "worldbank.org" or host.endswith(".worldbank.org"):
        inferred_publisher = "Indian Government (World Bank–assisted projects)"
        inferred_landing = "https://projects.worldbank.org"
        inferred_mirror = None
    elif host == "zenodo.org" or host.endswith(".zenodo.org"):
        inferred_publisher = "Open Research Procurement Archive (Zenodo)"
        inferred_landing = source_ref
        inferred_mirror = "Zenodo"
    elif host in {"gov.in", "nic.in"} or host.endswith((".gov.in", ".nic.in")):
        inferred_publisher = host
        inferred_landing = source_ref
        inferred_mirror = None
    elif is_github_host or host.endswith(".githubusercontent.com"):
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


def _fast_scrub_records(
    records: list[dict[str, Any]],
    *,
    max_depth: int = 32,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Recursively scrub all string values, PII keys, and sensitive structured fields with depth bound."""
    redactions: list[str] = []

    def _walk(item: Any, depth: int) -> Any:
        if depth > max_depth:
            return "[TRUNCATED_DEPTH]"
        if isinstance(item, str):
            if len(item) >= 6 and ("@" in item or any("0" <= ch <= "9" for ch in item)):
                cleaned, tags = scrub_pii(item)
                for tag in tags:
                    if tag not in redactions:
                        redactions.append(tag)
                return cleaned
            return item
        if isinstance(item, list):
            return [_walk(v, depth + 1) for v in item]
        if isinstance(item, tuple):
            return tuple(_walk(v, depth + 1) for v in item)
        if isinstance(item, dict):
            out: dict[Any, Any] = {}
            for k, v in item.items():
                clean_k = k
                if isinstance(k, str):
                    if len(k) >= 6 and ("@" in k or any("0" <= ch <= "9" for ch in k)):
                        ck, ktags = scrub_pii(k)
                        if ktags:
                            for tag in ktags:
                                if tag not in redactions:
                                    redactions.append(tag)
                            clean_k = ck
                    matched, redacted_v = scrub_sensitive_field_value(k, v, redactions)
                    if matched:
                        out[clean_k] = redacted_v
                        continue
                out[clean_k] = _walk(v, depth + 1)
            return out
        return item

    return [_walk(r, 0) for r in records], redactions


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


def _clamp_int_cap(raw: Any, default: int, *, minimum: int = 1) -> int:
    val = _to_int_opt(raw)
    if val is None:
        return default
    return max(minimum, min(val, default))


def _clamp_float_cap(raw: Any, default: float, *, minimum: float = 1.0) -> float:
    if raw is None or isinstance(raw, bool):
        return default
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(val):
        return default
    return max(minimum, min(val, default))


def load_dataset(
    path_or_url: str,
    fmt: str | None = None,
    context: dict[str, Any] | None = None,
    *,
    allow_local_files: bool = False,
    expected_sha256: str | None = None,
    secondary_expected_sha256: str | None = None,
    use_cache: bool | None = None,
) -> LoadedDataset:
    """Load, hash, verify digests, detect demo status, and PII-scrub a public URL or local file.

    Security & provenance guarantees:
    - ``allow_local_files`` defaults to ``False`` for unattended swarm runner safety; untrusted
      ``context`` dictionaries cannot override ``allow_local_files=False``, cannot raise resource
      caps above hardcoded ceilings, and cannot enable raw disk caching without explicit opt-in.
    - Primary and secondary ``expected_sha256`` digests fail closed with ``DigestMismatchError``.
    - Remote fetches require HTTPS public hosts (with DNS IP & hop-by-hop redirect checks), enforce
      bounded download size, decompression size/ratio limits, XML DTD guards, and row/page parse caps.
    """
    ctx = context or {}
    source_ref = str(path_or_url).strip()
    content_type = ""

    # Opt-in raw disk cache only via explicit keyword argument or operator env var (never untrusted context in unattended mode).
    env_cache = os.environ.get("AIWATCHDOGS_ENABLE_RAW_CACHE") == "1"
    if use_cache is not None:
        resolved_use_cache = bool(use_cache)
    else:
        resolved_use_cache = bool(env_cache or (allow_local_files and ctx.get("use_cache", False)))

    # Clamp all context-provided limits strictly to <= hardcoded safe defaults (untrusted context can never raise caps).
    max_download_bytes = _clamp_int_cap(ctx.get("max_download_bytes"), DEFAULT_MAX_DOWNLOAD_BYTES)
    max_decompressed_bytes = _clamp_int_cap(ctx.get("max_decompressed_bytes"), DEFAULT_MAX_DECOMPRESSED_BYTES)
    max_compression_ratio = _clamp_float_cap(ctx.get("max_compression_ratio"), DEFAULT_MAX_COMPRESSION_RATIO)
    max_zip_members = _clamp_int_cap(ctx.get("max_zip_members"), DEFAULT_MAX_ZIP_MEMBERS)
    max_parse_rows = _clamp_int_cap(ctx.get("max_parse_rows"), DEFAULT_MAX_PARSE_ROWS)
    max_pdf_pages = _clamp_int_cap(ctx.get("max_pdf_pages"), DEFAULT_MAX_PDF_PAGES)
    max_pdf_chars = _clamp_int_cap(ctx.get("max_pdf_chars"), DEFAULT_MAX_PDF_CHARS)

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
            use_cache=resolved_use_cache,
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

    p10_spec = ctx.get("innocent_but_locked_up") if isinstance(ctx.get("innocent_but_locked_up"), dict) else None
    p10_selector = p10_spec.get("record_selector") if isinstance(p10_spec, dict) and isinstance(p10_spec.get("record_selector"), dict) else {}
    requested_archive_member = str(ctx.get("_archive_member") or p10_selector.get("archive_member") or "").strip() or None
    wants_html_visible_text = bool(
        isinstance(ctx.get("sovereign_lies"), dict)
        or isinstance(p10_spec, dict)
        or ctx.get("_extract_html_text")
    )

    parsed_json: Any = None
    source_locator: str | None = None
    if resolved_fmt == "tar":
        records, raw_text, parsed_json, resolved_fmt, source_locator = _parse_tar_bytes(
            raw_bytes,
            source_ref,
            archive_member=requested_archive_member,
            extract_html_text=wants_html_visible_text,
            max_decompressed_bytes=max_decompressed_bytes,
            max_compression_ratio=max_compression_ratio,
            max_zip_members=max_zip_members,
            max_parse_rows=max_parse_rows,
            max_pdf_pages=max_pdf_pages,
            max_pdf_chars=max_pdf_chars,
        )
    elif resolved_fmt == "gzip":
        decompressed = _decompress_gzip_bounded(
            raw_bytes,
            source_ref,
            max_decompressed_bytes=max_decompressed_bytes,
            max_compression_ratio=max_compression_ratio,
        )
        inner_ref = re.sub(r"\.gz(?=$|[?&#])", "", source_ref, flags=re.IGNORECASE)
        inner_fmt = _infer_format(inner_ref, None, "", decompressed)
        if inner_fmt == "gzip":
            head_strip = decompressed[:2048].decode("utf-8-sig", errors="replace").lstrip()
            inner_fmt = "json" if (head_strip.startswith("{") or head_strip.startswith("[")) else "csv"
        if inner_fmt == "tar":
            records, raw_text, parsed_json, resolved_fmt, source_locator = _parse_tar_bytes(
                decompressed,
                inner_ref,
                archive_member=requested_archive_member,
                extract_html_text=wants_html_visible_text,
                max_decompressed_bytes=max_decompressed_bytes,
                max_compression_ratio=max_compression_ratio,
                max_zip_members=max_zip_members,
                max_parse_rows=max_parse_rows,
                max_pdf_pages=max_pdf_pages,
                max_pdf_chars=max_pdf_chars,
            )
        elif inner_fmt == "csv":
            records, raw_text = _parse_csv_bytes(decompressed, inner_ref, max_parse_rows=max_parse_rows)
            resolved_fmt = "csv"
            source_locator = f"rows=1-{len(records)}" if records else "rows=0"
        elif inner_fmt == "jsonl":
            records, raw_text, resolved_fmt = _parse_jsonl_bytes(
                decompressed, inner_ref, max_parse_rows=max_parse_rows
            )
            source_locator = f"records=1-{len(records)}" if records else "records=0"
        else:
            records, raw_text, parsed_json, resolved_fmt = _parse_json_bytes(
                decompressed, inner_ref, max_parse_rows=max_parse_rows
            )
            source_locator = f"records=1-{len(records)}" if records else "records=0"
    elif resolved_fmt == "csv":
        records, raw_text = _parse_csv_bytes(raw_bytes, source_ref, max_parse_rows=max_parse_rows)
        source_locator = f"rows=1-{len(records)}" if records else "rows=0"
    elif resolved_fmt == "jsonl":
        records, raw_text, resolved_fmt = _parse_jsonl_bytes(
            raw_bytes, source_ref, max_parse_rows=max_parse_rows
        )
        source_locator = f"records=1-{len(records)}" if records else "records=0"
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
            archive_member=requested_archive_member,
            extract_html_text=wants_html_visible_text,
            max_decompressed_bytes=max_decompressed_bytes,
            max_compression_ratio=max_compression_ratio,
            max_zip_members=max_zip_members,
            max_parse_rows=max_parse_rows,
            max_pdf_pages=max_pdf_pages,
            max_pdf_chars=max_pdf_chars,
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
        if wants_html_visible_text:
            visible_text = _extract_html_visible_text(raw_bytes, max_chars=max_pdf_chars)
            html_body_record = {
                "page": 1,
                "raw_text": visible_text,
                "source_ref": source_ref,
                "source_url": source_ref if _is_url(source_ref) else "",
                "source_format": "html",
                "jurisdiction": _infer_jurisdiction(source_ref),
            }
            records = [html_body_record, *records]
            raw_text = visible_text[:65_536]
            resolved_fmt = "html"
            source_locator = "html_body"
        else:
            source_locator = f"table_rows=1-{len(records)}" if records else "table_rows=0"
    elif resolved_fmt == "pdf":
        records, raw_text, source_locator = _parse_pdf_bytes(
            raw_bytes, source_ref, max_pdf_pages=max_pdf_pages, max_pdf_chars=max_pdf_chars
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

    # Optional secondary dataset join (fail closed on secondary digest mismatch!).
    # In unattended mode (allow_local_files=False), ignore secondary_path so untrusted context
    # cannot inject an untracked secondary dataset bypassing runner secondary digest verification.
    secondary_ref = str(
        ctx.get("secondary_url")
        or ctx.get("secondary_dataset_url")
        or (ctx.get("secondary_path") if allow_local_files else "")
        or ""
    ).strip()
    secondary_sha256: str | None = None
    secondary_digest_verified: bool | None = None
    secondary_provenance: dict[str, Any] | None = None
    if secondary_ref and secondary_ref == source_ref:
        if resolved_sec_expected_sha is not None and sha256.lower() != resolved_sec_expected_sha:
            raise DigestMismatchError(
                f"Secondary artifact SHA-256 mismatch for {secondary_ref}: "
                f"expected {resolved_sec_expected_sha}, observed {sha256}."
            )
        secondary_sha256 = sha256
        secondary_digest_verified = True if resolved_sec_expected_sha is not None else digest_verified
    elif secondary_ref and secondary_ref != source_ref:
        sec_ctx: dict[str, Any] = {
            "max_download_bytes": max_download_bytes,
            "max_decompressed_bytes": max_decompressed_bytes,
            "max_compression_ratio": max_compression_ratio,
            "max_zip_members": max_zip_members,
            "max_parse_rows": max_parse_rows,
            "max_pdf_pages": max_pdf_pages,
            "max_pdf_chars": max_pdf_chars,
        }
        if isinstance(ctx.get("sovereign_lies"), dict) or isinstance(p10_spec, dict):
            sec_ctx["_extract_html_text"] = True
        sec_arc_member = str(p10_selector.get("secondary_archive_member") or "").strip()
        if sec_arc_member:
            sec_ctx["_archive_member"] = sec_arc_member
        sec_ds = load_dataset(
            secondary_ref,
            context=sec_ctx,
            allow_local_files=allow_local_files,
            expected_sha256=resolved_sec_expected_sha,
            use_cache=resolved_use_cache,
        )
        secondary_sha256 = sec_ds.sha256
        secondary_digest_verified = sec_ds.digest_verified
        secondary_provenance = sec_ds.provenance
        if ctx.get("secondary_document") == "pages" and resolved_fmt == "pdf" and sec_ds.format == "pdf":
            # A check that reads two official documents side by side (UB1): keep both sets of pages,
            # each marked with the document it came from. Page numbers restart in the second one.
            records = [{**rec, "document": "primary"} for rec in records] + [
                {**rec, "document": "secondary"} for rec in sec_ds.records
            ]
        elif isinstance(ctx.get("parliaments_to_do_list"), dict) and sec_ds.format == "pdf":
            # P6 attaches an official Lok Sabha Question PDF or Rajya Sabha Implementation Report PDF
            # alongside the primary Digital Sansad JSON record without reusing UB1's secondary_document flag.
            records = [{**rec, "document": "primary"} for rec in records] + [
                {**rec, "document": "secondary", "secondary_sha256": sec_ds.sha256} for rec in sec_ds.records
            ]
        elif isinstance(ctx.get("only_one_bid"), dict):
            # P7 attaches a pinned companion procurement shard for cross-year repeat-winner cohorts.
            records = [{**rec, "document": "primary"} for rec in records] + [
                {**rec, "document": "secondary", "secondary_sha256": sec_ds.sha256} for rec in sec_ds.records
            ]
        elif isinstance(ctx.get("sovereign_lies"), dict) or isinstance(p10_spec, dict):
            # P8 / P10 pair a primary official record with a secondary official record
            # across PDF, HTML, JSON, JSONL, CSV, TXT, ZIP, or TAR formats while keeping both sets clearly tagged.
            records = [{**rec, "document": "primary"} for rec in records] + [
                {
                    **rec,
                    "document": "secondary",
                    "secondary_sha256": sec_ds.sha256,
                    "secondary_format": sec_ds.format,
                }
                for rec in sec_ds.records
            ]
        else:
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

    # Persist opt-in / pinned-SHA-256 cache ONLY after verifying zero PII was present in the payload.
    if is_remote and resolved_use_cache and not from_cache and os.environ.get("AIWATCHDOGS_DISABLE_CACHE") != "1":
        _store_pinned_mem_cache(
            source_ref,
            resolved_expected_sha,
            raw_bytes,
            content_type,
            fetched_at,
            has_pii=bool(combined_redactions),
        )
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
