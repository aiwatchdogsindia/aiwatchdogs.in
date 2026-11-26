"""Validate a contributor's public observations against the deployed batch manifest."""

from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from fastapi import HTTPException

from aiwatchdogs.engines.gigw_matrix import (
    CONDITIONAL_NA_FIELDS,
    CONTACT_FIELDS,
    CONTACT_UNASSESSABLE_FIELDS,
    RTI_UNASSESSABLE_FIELDS,
    VALID_CHECK_STATES,
    is_rti_sec24_exempt,
)
from aiwatchdogs.engines.gov_pages import CHECKS, Unavailable, public_evidence_url
from aiwatchdogs.projects import default_projects_dir

OBSERVATION_FIELDS = {"domain", "observed_at", "outcome", "reason", "checks", "pages"}
STAMP = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z", re.ASCII)
CLOCK_SKEW = timedelta(minutes=5)
CORE_CONTACT_FIELDS = ("email", "postal_address", "telephone")


def _valid_evidence_url(page: object, domain: str) -> bool:
    if not isinstance(page, str) or len(page) > 256 or not page.isascii() or not page.isprintable():
        return False
    try:
        return public_evidence_url(page, domain) == page
    except (Unavailable, ValueError):
        return False


def _validate_evidence(row: dict, domain: str) -> None:
    checks, pages, reason = row["checks"], row["pages"], row["reason"]
    if (
        not isinstance(checks, dict)
        or set(checks) != set(CHECKS)
        or any(v not in VALID_CHECK_STATES for v in checks.values())
        or not isinstance(pages, list)
        or len(pages) > 3
    ):
        raise HTTPException(422, "Invalid website check or page list")

    if row["outcome"] == "unassessable":
        if (
            pages
            or any(v != "unassessable" for v in checks.values())
            or reason not in ("robots_restricted", "fetch_failed", "unsupported_page")
        ):
            raise HTTPException(422, "Invalid unassessable site observation")
        return

    if row["outcome"] != "observed" or not pages or reason not in (None, "contact_unavailable", "rti_unavailable"):
        raise HTTPException(422, "Observed sites require page evidence")

    if len(set(pages)) != len(pages):
        raise HTTPException(422, "Sampled evidence pages must be distinct")

    if len(pages) == 2 and checks["contact_link"] != "observed" and checks["rti_link"] != "observed":
        raise HTTPException(422, "Two distinct sampled pages require an observed contact or RTI link")

    if len(pages) == 3 and (checks["contact_link"] != "observed" or checks["rti_link"] != "observed"):
        raise HTTPException(422, "Three distinct sampled pages require observed contact and RTI links")

    if reason == "contact_unavailable":
        if checks["contact_link"] != "observed" or any(checks[k] == "not_observed" for k in CORE_CONTACT_FIELDS):
            raise HTTPException(422, "Inconsistent contact-page evidence")
        if checks["rti_link"] != "observed" and len(pages) != 1:
            raise HTTPException(422, "Inconsistent contact-page evidence")

    if reason == "rti_unavailable":
        if checks["rti_link"] != "observed" or checks["pio_faa_disclosure"] == "not_observed":
            raise HTTPException(422, "Inconsistent RTI-page evidence")

    rti_exempt = is_rti_sec24_exempt(domain)
    for key, state in checks.items():
        if state == "unassessable":
            if reason == "contact_unavailable" and key in CONTACT_UNASSESSABLE_FIELDS:
                continue
            if reason == "rti_unavailable" and key in RTI_UNASSESSABLE_FIELDS:
                continue
            raise HTTPException(422, "An unassessable check requires a corresponding reason")
        if state == "not_applicable":
            if key not in CONDITIONAL_NA_FIELDS:
                raise HTTPException(422, f"Check '{key}' cannot be marked not_applicable")
            if key in ("rti_link", "pio_faa_disclosure") and not rti_exempt:
                raise HTTPException(422, "RTI checks may only be not_applicable for Section 24 exempt hosts")
            if key == "gov_email_domain" and checks["email"] == "observed":
                raise HTTPException(422, "gov_email_domain cannot be not_applicable when an email was observed")


def validate_batch(payload: dict, rows: object, *, lease_started_at: str, received_at: str) -> list[dict]:
    filename = urlsplit(payload["retrieval_url"]).path.rsplit("/", 1)[-1]
    if not re.fullmatch(r"batch-[0-9]{3,4}\.csv", filename):
        raise HTTPException(500, "Invalid deployed batch filename")
    try:
        data = (default_projects_dir() / "gov-in-public-information" / filename).read_bytes()
    except OSError:
        raise HTTPException(503, "The assigned batch is unavailable in this deployment") from None
    if hashlib.sha256(data).hexdigest() != payload["expected_sha256"]:
        raise HTTPException(503, "The assigned batch is unavailable in this deployment")
    domains = {record["domain"] for record in csv.DictReader(io.StringIO(data.decode()))}
    if not isinstance(rows, list) or len(rows) != len(domains):
        raise HTTPException(422, "Submit one observation for each assigned host")
    earliest = datetime.fromisoformat(lease_started_at) - CLOCK_SKEW
    latest = datetime.fromisoformat(received_at) + CLOCK_SKEW
    for row in rows:
        if not isinstance(row, dict) or set(row) != OBSERVATION_FIELDS:
            raise HTTPException(422, "Invalid website observation fields")
        domain = row["domain"]
        if not isinstance(domain, str) or domain not in domains:
            raise HTTPException(422, "Unexpected or repeated host")
        domains.remove(domain)
        _validate_evidence(row, domain)
        try:
            stamp = row["observed_at"]
            if not isinstance(stamp, str) or not STAMP.fullmatch(stamp):
                raise ValueError
            if not earliest <= datetime.fromisoformat(stamp) <= latest:
                raise ValueError
        except ValueError:
            raise HTTPException(422, "Observation timestamp must fall within the task lease") from None
        for page in row["pages"]:
            if not _valid_evidence_url(page, domain):
                raise HTTPException(422, "Invalid evidence URL")
    return rows


__all__ = ["CONTACT_FIELDS", "OBSERVATION_FIELDS", "validate_batch"]
