"""Validate a contributor's public observations against the deployed batch manifest."""

import csv
import hashlib
import io
import re
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from fastapi import HTTPException

from aiwatchdogs.engines.gov_pages import CHECKS, CONTACT_FIELDS, Unavailable, public_evidence_url
from aiwatchdogs.projects import default_projects_dir

OBSERVATION_FIELDS = {"domain", "observed_at", "outcome", "reason", "checks", "pages"}
STAMP = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z", re.ASCII)
CLOCK_SKEW = timedelta(minutes=5)


def _valid_evidence_url(page, domain: str) -> bool:
    if not isinstance(page, str) or len(page) > 256 or not page.isascii() or not page.isprintable():
        return False
    try:
        return public_evidence_url(page, domain) == page
    except (Unavailable, ValueError):
        return False


def _validate_evidence(row: dict) -> None:
    checks, pages, reason = row["checks"], row["pages"], row["reason"]
    if (
        not isinstance(checks, dict) or set(checks) != set(CHECKS)
        or any(v not in ("observed", "not_observed", "unassessable") for v in checks.values())
        or not isinstance(pages, list) or len(pages) > 2
    ):
        raise HTTPException(422, "Invalid website check or page list")
    if row["outcome"] == "unassessable":
        if (
            pages or any(v != "unassessable" for v in checks.values())
            or reason not in ("robots_restricted", "fetch_failed", "unsupported_page")
        ):
            raise HTTPException(422, "Invalid unassessable site observation")
        return
    if row["outcome"] != "observed" or not pages or reason not in (None, "contact_unavailable"):
        raise HTTPException(422, "Observed sites require page evidence")
    if len(pages) == 2 and (pages[0] == pages[1] or checks["contact_link"] != "observed"):
        raise HTTPException(422, "Two distinct sampled pages require an observed contact link")
    if reason == "contact_unavailable":
        if (
            len(pages) != 1 or checks["contact_link"] != "observed"
            or any(checks[k] == "not_observed" for k in CONTACT_FIELDS)
        ):
            raise HTTPException(422, "Inconsistent contact-page evidence")
    for key, state in checks.items():
        if state == "unassessable" and (reason != "contact_unavailable" or key not in CONTACT_FIELDS):
            raise HTTPException(422, "An unassessable check requires a corresponding reason")


def validate_batch(payload, rows, *, lease_started_at: str, received_at: str):
    filename = urlsplit(payload["retrieval_url"]).path.rsplit("/", 1)[-1]
    if not re.fullmatch(r"batch-[0-9]{3}\.csv", filename):
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
        _validate_evidence(row)
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
