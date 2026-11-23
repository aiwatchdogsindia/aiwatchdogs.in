"""Lawful dataset loader for local CSV/JSON/OCDS/PDF files and Tier-1 Indian open-data URLs."""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from pypdf import PdfReader

from aiwatchdogs.core.models import LoadedDataset
from aiwatchdogs.core.safety import compute_sha256, scrub_pii, scrub_pii_in_obj

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


def _is_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _parse_iso_days(start_str: str | None, end_str: str | None) -> int | None:
    if not start_str or not end_str:
        return None
    try:
        s = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
        e = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
        return max(0, (e - s).days)
    except ValueError:
        return None


def _flatten_ocds_release(release: dict[str, Any], package_meta: dict[str, Any]) -> dict[str, Any]:
    """Flatten an Open Contracting Data Standard (OCDS) release into a canonical procurement row."""
    tender = release.get("tender") or {}
    buyer = release.get("buyer") or {}
    awards = release.get("awards") or []
    first_award = awards[0] if isinstance(awards, list) and awards else {}
    suppliers = first_award.get("suppliers") or []
    first_supplier = suppliers[0] if isinstance(suppliers, list) and suppliers else {}
    bids_details = (release.get("bids") or {}).get("details") or []

    period = tender.get("tenderPeriod") or {}
    start_date = period.get("startDate")
    end_date = period.get("endDate")
    bid_window_days = _parse_iso_days(start_date, end_date)

    bid_count = tender.get("numberOfTenderers")
    if bid_count is None and isinstance(bids_details, list) and bids_details:
        bid_count = len(bids_details)

    award_value = (first_award.get("value") or {}).get("amount")
    if award_value is None:
        award_value = (tender.get("value") or {}).get("amount", 0)

    items = tender.get("items") or []
    item_category = ""
    if isinstance(items, list) and items:
        first_item = items[0] or {}
        item_category = (
            (first_item.get("classification") or {}).get("description")
            or first_item.get("description")
            or ""
        )

    return {
        "ocid": release.get("ocid") or "",
        "tender_id": tender.get("id") or release.get("ocid") or release.get("id") or "",
        "tender_title": tender.get("title") or "",
        "buyer_name": buyer.get("name") or "",
        "buyer_id": buyer.get("id") or "",
        "procurement_method": tender.get("procurementMethod") or "open",
        "bid_count": int(bid_count) if bid_count is not None else 0,
        "tender_start_date": start_date or "",
        "tender_end_date": end_date or "",
        "bid_window_days": bid_window_days,
        "award_id": first_award.get("id") or "",
        "award_date": first_award.get("date") or release.get("date") or "",
        "award_value_inr": float(award_value or 0),
        "vendor_name": first_supplier.get("name") or "",
        "vendor_id": first_supplier.get("id") or "",
        "item_category": item_category or "Public Procurement",
        "jurisdiction": release.get("jurisdiction") or package_meta.get("jurisdiction") or "Union of India",
        "fiscal_year": release.get("fiscal_year") or package_meta.get("fiscal_year") or "FY 2024-25",
        "source_url": release.get("source_url") or package_meta.get("source_url") or "https://eprocure.gov.in",
    }


def _infer_format(source_ref: str, fmt: str | None, content_type: str = "") -> str:
    if fmt:
        return fmt.lower().strip().lstrip(".")
    ct = content_type.lower()
    if "application/pdf" in ct:
        return "pdf"
    if "application/json" in ct or "+json" in ct:
        return "json"
    if "text/csv" in ct:
        return "csv"
    parsed_path = urlparse(source_ref).path if _is_url(source_ref) else source_ref
    suffix = Path(parsed_path).suffix.lower().lstrip(".")
    if suffix in {"csv", "json", "pdf", "txt"}:
        return suffix
    return "json"


def _detect_demo_sample(source_ref: str, raw_text: str, parsed_json: Any = None) -> bool:
    lower_ref = source_ref.lower()
    if "datasets/sample" in lower_ref or "sample/" in lower_ref or "demo_sample" in lower_ref:
        return True
    if isinstance(parsed_json, dict):
        if parsed_json.get("is_demo_sample") is True:
            return True
        badge = str(parsed_json.get("dataset_badge") or parsed_json.get("notice") or "").upper()
        if "SAMPLE" in badge or "DEMO" in badge:
            return True
    head_text = raw_text[:2000].upper()
    if "[SAMPLE DEMO DATASET]" in head_text or "SAMPLE_DEMO" in head_text or "DEMO_SAMPLE" in head_text:
        return True
    return False


def _fetch_remote_bytes(url: str, timeout: float = 20.0) -> tuple[bytes, str]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/csv, application/pdf, text/plain;q=0.9, */*;q=0.5",
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
        if "text/html" in content_type.lower():
            snippet = raw_bytes[:8192].decode("utf-8", errors="ignore").lower()
            for marker in _CAPTCHA_MARKERS:
                if marker in snippet:
                    raise PermissionError(
                        f"Remote source {url} is gated by a CAPTCHA/anti-bot check ('{marker}'). "
                        "Per IT Act Sec 43 policy, please download the official file manually and audit locally."
                    )
        return raw_bytes, content_type


def _parse_csv_bytes(raw_bytes: bytes) -> tuple[list[dict[str, Any]], str]:
    text = raw_bytes.decode("utf-8-sig", errors="replace")
    # Strip optional comment/banner metadata lines starting with '#' (e.g. [SAMPLE DEMO DATASET] header).
    data_lines = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    reader = csv.DictReader(io.StringIO("\n".join(data_lines)))
    records: list[dict[str, Any]] = []
    for row in reader:
        cleaned_row = {
            str(k).strip(): (v.strip() if isinstance(v, str) else v)
            for k, v in row.items()
            if k is not None
        }
        if any(str(v or "").strip() for v in cleaned_row.values()):
            records.append(cleaned_row)
    return records, text


def _parse_json_bytes(raw_bytes: bytes) -> tuple[list[dict[str, Any]], str, Any, str]:
    text = raw_bytes.decode("utf-8-sig", errors="replace")
    payload = json.loads(text)
    detected_format = "json"

    if isinstance(payload, list):
        records = [r for r in payload if isinstance(r, dict)]
        return records, text, payload, detected_format

    if isinstance(payload, dict):
        # 1. OCDS release package (e.g. CivicDataLab Assam / HP public procurement)
        if isinstance(payload.get("releases"), list):
            detected_format = "ocds_json"
            package_meta = {
                "jurisdiction": payload.get("jurisdiction") or (payload.get("publisher") or {}).get("name"),
                "fiscal_year": payload.get("fiscal_year"),
                "source_url": payload.get("uri") or payload.get("source_url"),
            }
            records = [
                _flatten_ocds_release(rel, package_meta)
                for rel in payload["releases"]
                if isinstance(rel, dict)
            ]
            return records, text, payload, detected_format

        # 2. OpenBudgetsIndia CKAN Action API response
        if payload.get("success") is True and isinstance(payload.get("result"), dict):
            result_obj = payload["result"]
            if isinstance(result_obj.get("records"), list):
                return [r for r in result_obj["records"] if isinstance(r, dict)], text, payload, "ckan_json"
            if isinstance(result_obj.get("results"), list):
                return [r for r in result_obj["results"] if isinstance(r, dict)], text, payload, "ckan_json"

        # 3. Generic record container (data.gov.in OGD API, sample JSON packages, etc.)
        for key in ("records", "data", "items", "paras", "tenders", "rows"):
            if isinstance(payload.get(key), list):
                return [r for r in payload[key] if isinstance(r, dict)], text, payload, detected_format

        return [payload], text, payload, detected_format

    return [], text, payload, detected_format


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
            }
        )
    return records, "\n\n".join(all_pages)


def load_dataset(path_or_url: str, fmt: str | None = None) -> LoadedDataset:
    """Load, hash, detect demo status, and PII-scrub a local file or Tier-1 public URL.

    Supports CSV, JSON (including OCDS release packages and OpenBudgetsIndia CKAN API
    envelopes), PDF (via ``pypdf``), and plain text.
    """
    source_ref = str(path_or_url).strip()
    content_type = ""

    if _is_url(source_ref):
        raw_bytes, content_type = _fetch_remote_bytes(source_ref)
    else:
        path = Path(source_ref)
        if not path.exists():
            raise FileNotFoundError(f"Dataset path does not exist: {source_ref}")
        raw_bytes = path.read_bytes()

    sha256 = compute_sha256(raw_bytes)
    byte_size = len(raw_bytes)
    resolved_fmt = _infer_format(source_ref, fmt, content_type)

    parsed_json: Any = None
    if resolved_fmt == "csv":
        records, raw_text = _parse_csv_bytes(raw_bytes)
    elif resolved_fmt == "json":
        records, raw_text, parsed_json, resolved_fmt = _parse_json_bytes(raw_bytes)
    elif resolved_fmt == "pdf":
        records, raw_text = _parse_pdf_bytes(raw_bytes, source_ref)
    else:
        raw_text = raw_bytes.decode("utf-8-sig", errors="replace")
        # Fall back cleanly to JSON or CSV if content clearly matches
        stripped = raw_text.lstrip()
        if stripped.startswith("{") or stripped.startswith("["):
            records, raw_text, parsed_json, resolved_fmt = _parse_json_bytes(raw_bytes)
        else:
            records = [{"raw_text": raw_text, "page": 1, "source_ref": source_ref}]

    is_demo = _detect_demo_sample(source_ref, raw_text, parsed_json)

    # Mandatory pre-storage PII scrubbing per DPDP Act 2023 & Aadhaar Act 2016
    scrubbed_records, rec_redactions = scrub_pii_in_obj(records)
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
