"""Reproduce the 100 assignments from the Data Libertarian directory snapshot.

Download gov/data/snapshot.json at commit 2801dc7d932115c86aee39e51f4702799e1b0adc
from https://github.com/piyushbodaa/datalibertarian, then run
    python projects/gov-in-public-information/verify.py path/to/snapshot.json
Add --write to regenerate the CSV files.
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
BATCHES = 100
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
        yield f"batch-{number:03d}.csv", buf.getvalue().encode("utf-8")
        start = end


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--write", action="store_true", help="recreate the CSV files")
    args = parser.parse_args()
    expected = dict(batches(inventory(args.snapshot.read_bytes())))
    if args.write:
        for name, content in expected.items():
            (DIRECTORY / name).write_bytes(content)
        return
    paths = {p.name for p in DIRECTORY.glob("batch-*.csv")}
    if paths != set(expected):
        raise ValueError("Manifest filenames differ from the 100 expected batches")
    project = json.loads(DIRECTORY.with_suffix(".json").read_text())
    if len(project["tasks"]) != BATCHES:
        raise ValueError("Expected one task per manifest")
    for task, (name, content) in zip(project["tasks"], expected.items(), strict=True):
        if (DIRECTORY / name).read_bytes() != content:
            raise ValueError(f"{name} differs from the source assignment")
        payload = task["payload"]
        if payload["expected_sha256"] != hashlib.sha256(content).hexdigest():
            raise ValueError(f"Project checksum differs for {name}")
        if not re.fullmatch(
            r"https://raw\.githubusercontent\.com/aiwatchdogsindia/aiwatchdogs\.in/"
            rf"[a-f0-9]{{40}}/projects/gov-in-public-information/{re.escape(name)}",
            payload["retrieval_url"],
        ):
            raise ValueError(f"Project URL differs for {name}")
    print("Verified 100 batches covering 12,537 unique web hostnames. 26 infrastructure entries excluded.")


if __name__ == "__main__":
    main()
