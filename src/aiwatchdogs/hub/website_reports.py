"""Validate a contributor's public observations against the deployed batch manifest."""

import csv
import hashlib
import io
import re
from datetime import datetime
from urllib.parse import unquote, urlsplit

from fastapi import HTTPException

from aiwatchdogs.engines.gov_pages import CHECKS
from aiwatchdogs.projects import default_projects_dir


def validate_batch(payload, rows):
    filename = urlsplit(payload["retrieval_url"]).path.rsplit("/", 1)[-1]
    if not re.fullmatch(r"batch-[0-9]{3}\.csv", filename):
        raise HTTPException(500, "Invalid deployed batch filename")
    data = (default_projects_dir() / "gov-in-public-information" / filename).read_bytes()
    if hashlib.sha256(data).hexdigest() != payload["expected_sha256"]:
        raise HTTPException(503, "The assigned batch is unavailable in this deployment")
    domains = {record["domain"] for record in csv.DictReader(io.StringIO(data.decode()))}
    if not isinstance(rows, list) or len(rows) != len(domains):
        raise HTTPException(422, "Submit one observation for each assigned host")
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"domain", "observed_at", "outcome", "reason", "checks", "pages"}:
            raise HTTPException(422, "Invalid website observation fields")
        domain = row["domain"]
        if not isinstance(domain, str) or domain not in domains:
            raise HTTPException(422, "Unexpected or repeated host")
        domains.remove(domain)
        checks, pages = row["checks"], row["pages"]
        if (not isinstance(checks, dict) or set(checks) != set(CHECKS)
                or any(v not in ("observed", "not_observed", "unassessable") for v in checks.values())
                or not isinstance(pages, list) or len(pages) > 2):
            raise HTTPException(422, "Invalid website check or page list")
        if row["outcome"] == "observed":
            if not pages or row["reason"] not in (None, "contact_unavailable"):
                raise HTTPException(422, "Observed sites require page evidence")
        elif (row["outcome"] != "unassessable" or pages or any(v != "unassessable" for v in checks.values())
              or row["reason"] not in ("invalid_domain", "robots_restricted", "fetch_failed", "unsupported_page")):
            raise HTTPException(422, "Invalid unassessable site observation")
        try:
            stamp = row["observed_at"]
            if not isinstance(stamp, str) or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z", stamp):
                raise ValueError
            datetime.fromisoformat(stamp)
            bare = domain.removeprefix("www.")
            for page in pages:
                if not isinstance(page, str) or len(page) > 256:
                    raise ValueError
                parsed = urlsplit(page)
                if (parsed.scheme != "https" or parsed.netloc not in (bare, "www." + bare)
                        or parsed.query or parsed.fragment or re.search(r"[\s\x00-\x1f\x7f@?#\\]|\d{7,}", unquote(page.split("://", 1)[-1].partition("/")[2]))
                        or not page.isascii() or not page.isprintable()):
                    raise ValueError
        except (ValueError, TypeError):
            raise HTTPException(422, "Invalid observation timestamp or page URL") from None
    return rows
