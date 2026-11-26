"""Reproduce the 1,000 assignments from the Data Libertarian directory snapshot.

Download gov/data/snapshot.json at commit 2801dc7d932115c86aee39e51f4702799e1b0adc
from https://github.com/piyushbodaa/datalibertarian, then run
    python projects/gov-in-public-information/verify.py path/to/snapshot.json
Add --write to regenerate the 1,000 CSV files.
"""

import argparse
import csv
import hashlib
import io
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

DIRECTORY = Path(__file__).resolve().parent
SNAPSHOT_SHA256 = "e995ecdb2f87c8f1941fd259603afb9ccbd56f32e7ce06f733052b9eff6cb19c"
BATCHES = 1000
HOST = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+gov\.in")
# The directory sometimes cites an application action, session or document.
# Keep known home page forms and language landing pages. Otherwise start at /.
LANDING_PATH = re.compile(
    r"/(?:(?:en|hi|eng|english|en-in|en-us|ta)/?)?"
    r"(?:(?:home|homepage|index|default|welcome|main)(?:\.(?:html?|aspx?|php|jsp))?/?|portal/?)?",
    re.IGNORECASE,
)


def inventory(source):
    if hashlib.sha256(source).hexdigest() != SNAPSHOT_SHA256:
        raise ValueError("Snapshot SHA-256 differs from the November 13, 2026 inventory")
    rows = json.loads(source)["records"]
    assignments = []
    hosts = set()
    infrastructure = 0
    for row in rows:
        domain = row["domain"]
        if not HOST.fullmatch(domain) or domain in hosts:
            raise ValueError(f"Invalid or repeated inventory hostname: {domain}")
        hosts.add(domain)
        if row["role"] == "infrastructure":
            infrastructure += 1
            continue
        if row["role"] != "web":
            raise ValueError(f"Unrecognized directory role on {domain}")
        url = urlsplit(row["url"])
        if url.scheme not in {"http", "https"} or url.netloc not in {domain, f"www.{domain}"}:
            raise ValueError(f"Unexpected URL for {domain}")
        path = url.path if not url.query and LANDING_PATH.fullmatch(url.path) else "/"
        assignments.append((domain, f"https://{url.hostname}{path}"))
    if (len(assignments), infrastructure) != (12537, 26):
        raise ValueError("Coverage differs from the source inventory")
    return sorted(assignments)


def batches(assignments):
    size, extra = divmod(len(assignments), BATCHES)
    start = 0
    for number in range(1, BATCHES + 1):
        end = start + size + (number <= extra)
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        writer.writerow(("domain", "url"))
        writer.writerows(assignments[start:end])
        yield f"batch-{number:04d}.csv", buf.getvalue().encode("utf-8")
        start = end


NON_UNION_TIERS = frozenset({"state", "local", "judiciary", "legislature"})
NON_UNION_KINDS = frozenset({"local", "district", "court", "legislature"})
STATE_UT_MUNICIPAL_ROOTS = frozenset(
    {
        "andamannicobar.gov.in",
        "ap.gov.in",
        "arunachal.gov.in",
        "arunachalpradesh.gov.in",
        "assam.gov.in",
        "bihar.gov.in",
        "cg.gov.in",
        "cgstate.gov.in",
        "chandigarh.gov.in",
        "chhattisgarh.gov.in",
        "daman.gov.in",
        "dd.gov.in",
        "delhi.gov.in",
        "dnh.gov.in",
        "dnhdd.gov.in",
        "goa.gov.in",
        "gujarat.gov.in",
        "haryana.gov.in",
        "haryanapolice.gov.in",
        "hp.gov.in",
        "himachal.gov.in",
        "jharkhand.gov.in",
        "jk.gov.in",
        "jammukashmir.gov.in",
        "karnataka.gov.in",
        "kerala.gov.in",
        "lsgkerala.gov.in",
        "ladakh.gov.in",
        "lakshadweep.gov.in",
        "maharashtra.gov.in",
        "mahaonline.gov.in",
        "manipur.gov.in",
        "meghalaya.gov.in",
        "mizoram.gov.in",
        "mn.gov.in",
        "mp.gov.in",
        "mponline.gov.in",
        "nagaland.gov.in",
        "odisha.gov.in",
        "orissa.gov.in",
        "puducherry.gov.in",
        "punjab.gov.in",
        "py.gov.in",
        "rajasthan.gov.in",
        "sikkim.gov.in",
        "tamilnadu.gov.in",
        "telangana.gov.in",
        "tn.gov.in",
        "tripura.gov.in",
        "uk.gov.in",
        "up.gov.in",
        "upsdc.gov.in",
        "uttarakhand.gov.in",
        "uttarpradesh.gov.in",
        "wb.gov.in",
        "westbengal.gov.in",
        "dcourts.gov.in",
        "ecourts.gov.in",
        "mcgm.gov.in",
        "bbmp.gov.in",
        "ghmc.gov.in",
    }
)


def _is_non_union_host(row, web_by_domain):
    domain = row["domain"].strip().lower()
    root_domain = (row.get("root_domain") or "").strip().lower()
    if (
        row.get("tier") in NON_UNION_TIERS
        or row.get("kind") in NON_UNION_KINDS
        or row.get("state")
    ):
        return True
    if domain in STATE_UT_MUNICIPAL_ROOTS or root_domain in STATE_UT_MUNICIPAL_ROOTS:
        return True
    if any(domain.endswith("." + root) for root in STATE_UT_MUNICIPAL_ROOTS):
        return True
    root_rec = web_by_domain.get(root_domain)
    if root_rec and (
        root_rec.get("tier") in NON_UNION_TIERS
        or root_rec.get("kind") in NON_UNION_KINDS
        or root_rec.get("state")
    ):
        return True
    return False


def host_scopes(source):
    """Extract deterministic host applicability scopes from the pinned Data Libertarian snapshot."""
    if hashlib.sha256(source).hexdigest() != SNAPSHOT_SHA256:
        raise ValueError("Snapshot SHA-256 differs from the November 13, 2026 inventory")
    web_rows = [row for row in json.loads(source)["records"] if row.get("role") == "web"]
    web_by_domain = {row["domain"].strip().lower(): row for row in web_rows}
    union_hosts = []
    ministry_dept_hosts = []
    for row in web_rows:
        domain = row["domain"].strip().lower()
        tier = row.get("tier")
        state = row.get("state")
        ministry = row.get("ministry")
        kind = row.get("kind")
        if (
            tier in {"union", "apex", "missions"} or (tier == "uncharted" and ministry and not state)
        ) and not _is_non_union_host(row, web_by_domain):
            union_hosts.append(domain)
        if kind in {"ministry", "department"}:
            ministry_dept_hosts.append(domain)
    return {
        "snapshot_sha256": SNAPSHOT_SHA256,
        "total_web_hosts": 12537,
        "union_hosts": sorted(set(union_hosts)),
        "ministry_dept_hosts": sorted(set(ministry_dept_hosts)),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--write", action="store_true", help="recreate the 1,000 CSV files and host-scopes.json")
    args = parser.parse_args()
    raw_snapshot = args.snapshot.read_bytes()
    expected = dict(batches(inventory(raw_snapshot)))
    expected_scopes = host_scopes(raw_snapshot)
    expected_scopes_bytes = (json.dumps(expected_scopes, indent=2) + "\n").encode("utf-8")
    if args.write:
        for old in DIRECTORY.glob("batch-*.csv"):
            old.unlink()
        for name, content in expected.items():
            (DIRECTORY / name).write_bytes(content)
        (DIRECTORY / "host-scopes.json").write_bytes(expected_scopes_bytes)
        return
    paths = {p.name for p in DIRECTORY.glob("batch-*.csv")}
    if paths != set(expected):
        raise ValueError("Manifest filenames differ from the 1,000 expected batches")
    scopes_path = DIRECTORY / "host-scopes.json"
    if not scopes_path.is_file() or scopes_path.read_bytes() != expected_scopes_bytes:
        raise ValueError("host-scopes.json differs from the pinned Data Libertarian snapshot")
    project = json.loads(DIRECTORY.with_suffix(".json").read_text())
    if len(project["tasks"]) != BATCHES:
        raise ValueError("Expected one task per manifest")
    for task, (name, content) in zip(project["tasks"], expected.items(), strict=True):
        if (DIRECTORY / name).read_bytes() != content:
            raise ValueError(f"{name} differs from the source assignment")
        payload = task["payload"]
        if payload["expected_sha256"] != hashlib.sha256(content).hexdigest():
            raise ValueError(f"Project checksum differs for {name}")
        expected_url = f"https://aiwatchdogs.in/api/missions/gov-in-public-information/batches/{name}"
        if payload["retrieval_url"] != expected_url:
            raise ValueError(f"Project URL differs for {name}: got {payload['retrieval_url']!r}")
    print(
        f"Verified 1,000 batches covering 12,537 unique web hostnames "
        f"({len(expected_scopes['union_hosts'])} Union/Central, "
        f"{len(expected_scopes['ministry_dept_hosts'])} Ministries/Departments). 26 infrastructure entries excluded."
    )


if __name__ == "__main__":
    main()
