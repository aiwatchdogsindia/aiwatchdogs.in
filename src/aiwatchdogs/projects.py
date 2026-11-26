"""Curated audit projects.

Maintainers choose every project and add it as one JSON file under ``projects/``
(see ``projects/README.md``); outside submissions are closed. On every deploy the hub
syncs those files into the ``missions`` table (projects are stored as missions
internally so the existing agent protocol keeps working).

This module owns the file format, validation (used by CI and by
``aiwatchdogs project validate``) and the database sync.

A project may be renamed: list its earlier slugs under ``formerly`` and the sync moves the
existing record (tasks, findings, history) to the new slug instead of starting a new one.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from aiwatchdogs.core.safety import contains_pii, scan_forbidden_terms, validate_safe_http_url
from aiwatchdogs.core.usage import DEFAULT_WORK_TYPE, MAX_TOKENS, PEER_ESTIMATE_BASIS, WORK_TYPES

PROJECT_ID_PREFIX = "project-"
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
PROJECT_CODE_RE = re.compile(r"^P[1-9][0-9]*$")
TASK_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
CANONICAL_PROJECT_CODES: dict[str, str] = {
    "police-finances": "P1",
    "gov-in-public-information": "P2",
    "independent-india-inflation": "P3",
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
PINNED_GITHUB_RAW_PATH_RE = re.compile(r"^/[^/]+/[^/]+/[0-9a-f]{40}/.+$")
ENLISTED_PR_URL_RE = re.compile(r"^https://github\.com/aiwatchdogsindia/aiwatchdogs\.in/pull/[1-9][0-9]*$")

# Reserved legacy seed mission slugs that community projects cannot collide with.
RESERVED_MISSION_SLUGS = frozenset(
    {
        "single-bidder-procurement-scrutiny",
        "split-order-threshold-audit",
        "vendor-concentration-hhi-watch",
        "price-escalation-estimate-gap",
        "fiscal-pacing-march-rush-audit",
        "cag-pending-atn-accountability",
        "scheme-delivery-utilization-gap",
        "electoral-bond-csr-proximity",
    }
)

# Loader-control keys that community project payload.context MUST NOT set.
FORBIDDEN_CONTEXT_KEYS = frozenset(
    {
        "allow_local_files",
        "use_cache",
        "secondary_path",
        "max_download_bytes",
        "max_decompressed_bytes",
        "max_compression_ratio",
        "max_zip_members",
        "max_parse_rows",
        "max_pdf_pages",
    }
)
# Heuristic guard against catastrophic-backtracking regex patterns in P1 context.
_UNSAFE_REGEX_RE = re.compile(r"(\([^)]*[+*][^)]*\)[+*?{]|\\[1-9])")

VALID_PILLARS = {"procurement", "budget", "audit", "infra", "scheme", "disclosure"}
VALID_STATUSES = {"active", "needs_data", "paused"}
VALID_QUANTITY_TYPES = {
    "tender_estimates",
    "awarded_contract_values",
    "budget_and_treasury_expenditure",
    "cag_report_monetary_figures",
    "public_website_information",
    "consumer_prices",
    "scheme_outlay_and_expenditure",
    "electoral_bond_disclosures",
}
REQUIRED_TOP_LEVEL = ("slug", "title", "summary", "posted_by", "pillar", "jurisdiction", "authority")
MAX_FORMER_SLUGS = 10
_COLUMN_NAME_RE = re.compile(r"[a-z_][a-z0-9_]*")
MAX_PART_WINDOW_LINES = 12
INFLATION_PROJECT_SLUG = "independent-india-inflation"
INFLATION_METHODOLOGY = "CPI1"
MAX_LARGE_PROJECT_TASKS = 100
MAX_WEBSITE_PROJECT_TASKS = 1000
DEFAULT_PRIORITY = 5
SYNC_STATE_KEY = "community_projects"


@dataclass
class ProjectFile:
    path: Path
    data: dict[str, Any]
    errors: list[str] = field(default_factory=list)

    @property
    def slug(self) -> str:
        return str(self.data.get("slug", ""))


def default_projects_dir() -> Path:
    """Locate ``projects/`` at the repository root (Vercel, Docker and local checkouts)."""
    explicit = os.environ.get("AIWATCHDOGS_PROJECTS_DIR")
    if explicit:
        return Path(explicit)
    env_root = os.environ.get("AIWATCHDOGS_REPO_ROOT")
    candidates = []
    if env_root:
        candidates.append(Path(env_root) / "projects")
    candidates.append(Path(__file__).resolve().parents[2] / "projects")
    candidates.append(Path.cwd() / "projects")
    for c in candidates:
        if c.is_dir():
            return c
    return candidates[0]


def _known_methodologies() -> set[str]:
    from aiwatchdogs.engines.registry import _ENGINE_DISPATCH

    return set(_ENGINE_DISPATCH) | {INFLATION_METHODOLOGY, "W1"}


def _scan_terms(text: str) -> list[str]:
    return scan_forbidden_terms(text or "")


def _is_https(url: Any) -> bool:
    try:
        validate_safe_http_url(url, require_https=True, allow_private=False)
        return True
    except ValueError:
        return False


def _validate_github_immutability(url: str) -> str | None:
    """Return an error string if a GitHub raw URL is not pinned to a 40-char commit SHA."""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path or ""
    if ".." in path.split("/"):
        return "must not contain '..' path segments."
    if host == "raw.github.com":
        return "must use raw.githubusercontent.com pinned to a 40-character commit SHA."
    if host == "raw.githubusercontent.com":
        if not PINNED_GITHUB_RAW_PATH_RE.match(path):
            return "must pin a full 40-character commit SHA (lowercase hex), not a branch name."
    if host in {"github.com", "www.github.com"} and "/raw/" in path.lower():
        parts = [p for p in path.split("/") if p]
        if len(parts) < 5 or not re.fullmatch(r"[0-9a-f]{40}", parts[3]):
            return "must pin a full 40-character commit SHA (lowercase hex), not a mutable GitHub branch."
    return None


def _validate_p1_context(ctx: dict[str, Any], where: str, errors: list[str]) -> None:
    budget_lines = ctx.get("budget_lines")
    if budget_lines is None:
        return
    if not isinstance(budget_lines, list) or len(budget_lines) > 50:
        errors.append(f"{where}.payload.context.budget_lines must be a list of at most 50 entries.")
        return
    for bi, spec in enumerate(budget_lines):
        bwhere = f"{where}.payload.context.budget_lines[{bi}]"
        if not isinstance(spec, dict):
            errors.append(f"{bwhere} must be an object.")
            continue
        parts = spec.get("parts") or []
        if not isinstance(parts, list) or len(parts) > 30:
            errors.append(f"{bwhere}.parts must be a list of at most 30 items.")
        else:
            for pi, part in enumerate(parts):
                pwhere = f"{bwhere}.parts[{pi}]"
                if not isinstance(part, dict):
                    errors.append(f"{pwhere} must be an object.")
                    continue
                pattern = part.get("pattern")
                if not isinstance(pattern, str) or not pattern.strip() or len(pattern) > 200:
                    errors.append(f"{pwhere}.pattern must be a regex string <= 200 characters.")
                elif _UNSAFE_REGEX_RE.search(pattern):
                    errors.append(f"{pwhere}.pattern uses nested quantifiers or backreferences (ReDoS risk).")
                else:
                    try:
                        re.compile(pattern)
                    except re.error as exc:
                        errors.append(f"{pwhere}.pattern is not a valid regex: {exc}")
                idx = part.get("index", 0)
                if isinstance(idx, bool) or not isinstance(idx, int) or idx < 0 or idx > 50:
                    errors.append(f"{pwhere}.index must be a non-negative integer <= 50.")
                for wkey in ("lines_before", "lines_after"):
                    win = part.get(wkey, 0)
                    if isinstance(win, bool) or not isinstance(win, int) or not 0 <= win <= MAX_PART_WINDOW_LINES:
                        errors.append(f"{pwhere}.{wkey} must be a whole number from 0 to {MAX_PART_WINDOW_LINES}.")
        refs = spec.get("references") or {}
        if refs and not isinstance(refs, dict):
            errors.append(f"{bwhere}.references must be an object.")
        elif isinstance(refs, dict):
            for rname, ref in refs.items():
                if not isinstance(ref, dict):
                    errors.append(f"{bwhere}.references.{rname} must be an object.")
                    continue
                val = ref.get("value")
                if val is not None and (
                    isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(float(val))
                ):
                    errors.append(f"{bwhere}.references.{rname}.value must be a finite number.")
                ref_url = ref.get("source_url")
                if ref_url not in (None, "") and not _is_https(ref_url):
                    errors.append(f"{bwhere}.references.{rname}.source_url must be a public https:// link.")


def _validate_estimate(item: dict[str, Any], where: str, errors: list[str]) -> None:
    est = item.get("estimated_tokens")
    if isinstance(est, bool) or not isinstance(est, int) or not 1 <= est <= MAX_TOKENS:
        errors.append(f"{where}.estimated_tokens must be a whole number of tokens, at least 1.")
    basis = item.get("estimate_basis")
    if not isinstance(basis, str) or not basis.strip() or len(basis) > 200:
        errors.append(f"{where}.estimate_basis must say how the estimate was made (200 characters at most).")


def validate_project(data: Any, *, filename_stem: str | None = None) -> list[str]:
    """Return a list of human-readable problems (empty list means the project is valid)."""
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["The file must contain one JSON object."]

    for key in REQUIRED_TOP_LEVEL:
        if key not in data or data[key] in (None, "", {}):
            errors.append(f"Missing required field '{key}'.")

    slug = data.get("slug")
    if slug is not None and not isinstance(slug, str):
        errors.append("'slug' must be a string (lowercase words joined by hyphens).")
    elif isinstance(slug, str):
        if not SLUG_RE.match(slug) or len(slug) > 64:
            errors.append("'slug' must be lowercase words joined by hyphens (max 64 characters).")
        if slug in RESERVED_MISSION_SLUGS:
            errors.append(f"'slug' '{slug}' is reserved for a built-in mission; choose a distinct slug.")
        if filename_stem is not None and filename_stem != slug:
            errors.append(f"File name must match the slug: rename it to '{slug}.json'.")

    project_code = data.get("project_code")
    if project_code not in (None, ""):
        if not isinstance(project_code, str) or not PROJECT_CODE_RE.fullmatch(project_code.strip()):
            errors.append("'project_code' must be a sequential project code like P1, P2, P3.")

    formerly = data.get("formerly", [])
    if not isinstance(formerly, list) or len(formerly) > MAX_FORMER_SLUGS:
        errors.append(f"'formerly' must be a list of at most {MAX_FORMER_SLUGS} earlier slugs.")
    else:
        for old in formerly:
            if not isinstance(old, str) or not SLUG_RE.match(old) or len(old) > 64:
                errors.append("'formerly' entries must be slugs (lowercase words joined by hyphens).")
            elif old == slug:
                errors.append("'formerly' must not repeat the project's current slug.")
            elif old in RESERVED_MISSION_SLUGS:
                errors.append(f"'formerly' entry '{old}' is reserved for a built-in mission.")
        if len({x for x in formerly if isinstance(x, str)}) != len(formerly):
            errors.append("'formerly' lists the same slug twice.")

    for key, limit in (("title", 120), ("summary", 600)):
        val = data.get(key)
        if val is not None and (not isinstance(val, str) or len(val.strip()) < 10 or len(val) > limit):
            errors.append(f"'{key}' must be text between 10 and {limit} characters.")

    for key, limit in (("jurisdiction", 120), ("authority", 200)):
        val = data.get(key)
        if val is not None and (not isinstance(val, str) or not val.strip() or len(val) > limit):
            errors.append(f"'{key}' must be text up to {limit} characters.")

    enlisted_pr_url = data.get("enlisted_pr_url")
    if enlisted_pr_url not in (None, ""):
        if not isinstance(enlisted_pr_url, str) or not ENLISTED_PR_URL_RE.match(enlisted_pr_url.strip()):
            errors.append(
                "'enlisted_pr_url' must be an official PR URL (https://github.com/aiwatchdogsindia/aiwatchdogs.in/pull/<number>)."
            )

    posted_by = data.get("posted_by")
    if posted_by is not None:
        if not isinstance(posted_by, dict) or not isinstance(posted_by.get("name"), str) or not posted_by["name"].strip():
            errors.append("'posted_by.name' is required.")
        else:
            if len(posted_by["name"]) > 120:
                errors.append("'posted_by.name' must be at most 120 characters.")
            url = posted_by.get("url")
            if url not in (None, "") and not _is_https(url):
                errors.append("'posted_by.url' must be a public https:// link.")
            handle = posted_by.get("handle")
            if handle not in (None, "") and (not isinstance(handle, str) or len(handle) > 40):
                errors.append("'posted_by.handle' must be a short handle such as '@copwatchIN'.")

    pillar = data.get("pillar")
    if pillar is not None and pillar not in VALID_PILLARS:
        errors.append(f"'pillar' must be one of: {', '.join(sorted(VALID_PILLARS))}.")

    priority = data.get("priority", DEFAULT_PRIORITY)
    if isinstance(priority, bool) or not isinstance(priority, int) or not 1 <= priority <= 10:
        errors.append("'priority' must be a whole number from 1 (first) to 10 (last).")

    status = data.get("status", "active")
    if status not in VALID_STATUSES:
        errors.append(f"'status' must be one of: {', '.join(sorted(VALID_STATUSES))}.")

    for key in ("scope", "out_of_scope", "statutory_anchors"):
        val = data.get(key, [])
        if not isinstance(val, list) or len(val) > 30 or not all(isinstance(x, str) and x.strip() and len(x) <= 300 for x in val):
            errors.append(f"'{key}' must be a list of short text items.")

    sources = data.get("sources", [])
    if not isinstance(sources, list) or not sources or len(sources) > 30:
        errors.append("'sources' must list at least one official public source (name, publisher, url).")
    else:
        for i, src in enumerate(sources):
            if not isinstance(src, dict) or not str(src.get("name", "")).strip() or not _is_https(src.get("url")):
                errors.append(f"sources[{i}] needs a 'name' and a public https:// 'url'.")

    methodology = data.get("methodology_code")
    known = _known_methodologies()
    tasks = data.get("tasks", [])
    if not isinstance(tasks, list):
        errors.append("'tasks' must be a list (it can be empty while data is still being found).")
        tasks = []
    planned = data.get("planned_tasks", [])
    if not isinstance(planned, list):
        errors.append("'planned_tasks' must be a list.")
        planned = []
    is_inflation = slug == INFLATION_PROJECT_SLUG and methodology == INFLATION_METHODOLOGY
    if (slug == INFLATION_PROJECT_SLUG or methodology == INFLATION_METHODOLOGY) and not is_inflation:
        errors.append(f"Methodology {INFLATION_METHODOLOGY} is reserved for '{INFLATION_PROJECT_SLUG}'.")
    is_website = slug == "gov-in-public-information" and methodology == "W1"
    if (slug == "gov-in-public-information" or methodology == "W1") and not is_website:
        errors.append("Methodology W1 is reserved for gov-in-public-information.")
    if is_inflation and len(tasks) + len(planned) > MAX_LARGE_PROJECT_TASKS:
        errors.append(
            f"A project can list at most {MAX_LARGE_PROJECT_TASKS} tasks ('tasks' and 'planned_tasks' together)."
        )
    if is_website and len(tasks) + len(planned) > MAX_WEBSITE_PROJECT_TASKS:
        errors.append(
            f"A website audit project can list at most {MAX_WEBSITE_PROJECT_TASKS} tasks ('tasks' and 'planned_tasks' together)."
        )
    if not tasks and not planned:
        errors.append("'planned_tasks' must list at least one task with a type and estimated_tokens.")
    for i, item in enumerate(planned):
        where = f"planned_tasks[{i}]"
        if not isinstance(item, dict):
            errors.append(f"{where} must be an object.")
            continue
        title = item.get("title")
        if not isinstance(title, str) or not 10 <= len(title.strip()) or len(title) > 160:
            errors.append(f"{where}.title must be text between 10 and 160 characters.")
        if item.get("type") not in WORK_TYPES:
            errors.append(f"{where}.type must be one of: {', '.join(WORK_TYPES)}.")
        _validate_estimate(item, where, errors)
    if tasks and methodology not in known:
        errors.append(f"'methodology_code' must be one of {sorted(known)} when tasks are included.")
    if not tasks and status == "active":
        errors.append("A project with no tasks yet must use \"status\": \"needs_data\".")

    seen_task_ids: set[str] = set()
    dependencies: dict[str, list[str]] = {}
    for i, task in enumerate(tasks):
        where = f"tasks[{i}]"
        if not isinstance(task, dict):
            errors.append(f"{where} must be an object.")
            continue
        tid = task.get("id")
        if not isinstance(tid, str) or not TASK_ID_RE.match(tid) or len(tid) > 60:
            errors.append(f"{where}.id must be lowercase words joined by hyphens.")
        elif tid in seen_task_ids:
            errors.append(f"{where}.id '{tid}' is used twice.")
        else:
            seen_task_ids.add(tid)
        for key in ("title", "instructions", "dataset_key", "financial_quantity_type"):
            if not isinstance(task.get(key), str) or not str(task.get(key, "")).strip():
                errors.append(f"{where}.{key} is required.")
        if task.get("financial_quantity_type") not in (None, "") and task.get(
            "financial_quantity_type"
        ) not in VALID_QUANTITY_TYPES:
            errors.append(f"{where}.financial_quantity_type must be one of {sorted(VALID_QUANTITY_TYPES)}.")
        if task.get("type", DEFAULT_WORK_TYPE) not in WORK_TYPES:
            errors.append(f"{where}.type must be one of: {', '.join(WORK_TYPES)}.")
        _validate_estimate(task, where, errors)
        code = task.get("methodology_code", methodology)
        if code not in known:
            errors.append(f"{where}.methodology_code must be one of {sorted(known)}.")
        if (code == "W1") != is_website or (is_website and task.get("financial_quantity_type") != "public_website_information"):
            errors.append(f"{where} must use the website audit project, methodology and quantity type together.")
        if is_inflation and (code != INFLATION_METHODOLOGY or task.get("financial_quantity_type") != "consumer_prices"):
            errors.append(f"{where} must use {INFLATION_METHODOLOGY} and the consumer_prices quantity type.")
        elif not is_inflation and code == INFLATION_METHODOLOGY:
            errors.append(f"{where}.methodology_code {INFLATION_METHODOLOGY} is reserved for '{INFLATION_PROJECT_SLUG}'.")
        if "depends_on" in task:
            deps = task["depends_on"]
            if not is_inflation:
                errors.append(f"{where}.depends_on is available to {INFLATION_METHODOLOGY} tasks.")
            elif (
                not isinstance(deps, list)
                or len(deps) > MAX_LARGE_PROJECT_TASKS
                or any(not isinstance(dep, str) or not TASK_ID_RE.fullmatch(dep) or len(dep) > 60 for dep in deps)
            ):
                errors.append(f"{where}.depends_on must be a list of task IDs in this project.")
            elif isinstance(tid, str):
                dependencies[tid] = deps
        payload = task.get("payload")
        if not isinstance(payload, dict):
            errors.append(f"{where}.payload is required.")
            continue
        if is_inflation and "depends_on" in payload:
            errors.append(f"{where}.depends_on belongs on the task, outside of 'payload'.")
        url = payload.get("retrieval_url")
        if not _is_https(url):
            errors.append(f"{where}.payload.retrieval_url must be a public https:// link to the exact file.")
        else:
            gh_err = _validate_github_immutability(url)
            if gh_err:
                errors.append(f"{where}.payload.retrieval_url {gh_err}")
        for extra_url_key in ("dataset_url", "source_url"):
            extra_val = payload.get(extra_url_key)
            if extra_val not in (None, ""):
                if not _is_https(extra_val):
                    errors.append(f"{where}.payload.{extra_url_key} must be a public https:// link.")
                else:
                    gh_err = _validate_github_immutability(extra_val)
                    if gh_err:
                        errors.append(f"{where}.payload.{extra_url_key} {gh_err}")
        sec_url = payload.get("secondary_url")
        if sec_url not in (None, ""):
            if not _is_https(sec_url):
                errors.append(f"{where}.payload.secondary_url must be a public https:// link.")
            else:
                gh_err = _validate_github_immutability(sec_url)
                if gh_err:
                    errors.append(f"{where}.payload.secondary_url {gh_err}")
            sec_sha = payload.get("secondary_expected_sha256")
            if not isinstance(sec_sha, str) or not SHA256_RE.match(sec_sha):
                errors.append(
                    f"{where}.payload.secondary_expected_sha256 must be a 64-character lowercase SHA-256 when secondary_url is set."
                )
        sha = payload.get("expected_sha256")
        if not isinstance(sha, str) or not SHA256_RE.match(sha):
            errors.append(f"{where}.payload.expected_sha256 must be the 64-character lowercase SHA-256 of the file.")
        if not str(payload.get("official_publisher", "")).strip():
            errors.append(f"{where}.payload.official_publisher is required.")
        if not _is_https(payload.get("source_landing_page")):
            errors.append(f"{where}.payload.source_landing_page must be a public https:// link.")
        ctx = payload.get("context")
        if ctx is not None:
            if not isinstance(ctx, dict):
                errors.append(f"{where}.payload.context must be an object.")
            else:
                forbidden_present = sorted(FORBIDDEN_CONTEXT_KEYS.intersection(ctx.keys()))
                if forbidden_present:
                    errors.append(
                        f"{where}.payload.context must not override loader controls: {', '.join(forbidden_present)}."
                    )
                if code == "P1":
                    _validate_p1_context(ctx, where, errors)

    for tid, deps in dependencies.items():
        if len(deps) != len(set(deps)) or any(dep not in seen_task_ids for dep in deps):
            errors.append(f"Task '{tid}' must list distinct prerequisites from this project's tasks.")
    try:
        TopologicalSorter(dependencies).prepare()
    except CycleError:
        errors.append("Research task prerequisites must be acyclic.")

    # Neutral framing (BNS Sec 356) and no personal data, across all human-written text.
    text_blobs = [
        ("title", data.get("title", "")),
        ("summary", data.get("summary", "")),
        ("authority", data.get("authority", "")),
        ("scope", " ".join(x for x in data.get("scope", []) if isinstance(x, str))),
        ("out_of_scope", " ".join(x for x in data.get("out_of_scope", []) if isinstance(x, str))),
    ]
    if isinstance(posted_by, dict):
        text_blobs.append(("posted_by.name", posted_by.get("name", "")))
    for i, task in enumerate(tasks):
        if isinstance(task, dict):
            text_blobs.append((f"tasks[{i}].title", task.get("title", "")))
            text_blobs.append((f"tasks[{i}].instructions", task.get("instructions", "")))
    for i, item in enumerate(planned):
        if isinstance(item, dict):
            text_blobs.append((f"planned_tasks[{i}].title", item.get("title", "")))
            text_blobs.append((f"planned_tasks[{i}].estimate_basis", item.get("estimate_basis", "")))
    for where, text in text_blobs:
        if not isinstance(text, str):
            continue
        for term in _scan_terms(text):
            errors.append(
                f"{where} uses the accusatory term '{term}'. Describe what to check, not who to blame."
            )
    # Scan all fields including posted_by (only allow benign github/org handles, never phone/email/PAN/Aadhaar).
    pii_payload = dict(data)
    if isinstance(posted_by, dict):
        pii_payload["posted_by"] = {k: v for k, v in posted_by.items() if k != "url"}
    has_pii, tags = contains_pii(pii_payload)
    if has_pii:
        errors.append(f"Remove personal data from the project ({', '.join(sorted(set(tags)))}).")

    return errors


def load_project_files(directory: Path | None = None) -> list[ProjectFile]:
    """Load every ``*.json`` project (files starting with ``_`` are templates and are skipped)."""
    root = directory or default_projects_dir()
    files: list[ProjectFile] = []
    if not root.is_dir():
        return files
    for path in sorted(root.glob("*.json")):
        if path.name.startswith("_"):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            files.append(ProjectFile(path=path, data={}, errors=[f"Invalid JSON: {exc}"]))
            continue
        files.append(ProjectFile(path=path, data=data, errors=validate_project(data, filename_stem=path.stem)))
    slugs: dict[str, Path] = {}
    project_codes: dict[str, Path] = {}
    task_ids: dict[str, Path] = {}
    for pf in files:
        if pf.slug in slugs:
            pf.errors.append(f"Slug '{pf.slug}' is already used by {slugs[pf.slug].name}.")
        else:
            slugs[pf.slug] = pf.path
        pcode = pf.data.get("project_code") if isinstance(pf.data, dict) else None
        if isinstance(pcode, str) and pcode.strip():
            code_clean = pcode.strip()
            if code_clean in project_codes and project_codes[code_clean] != pf.path:
                pf.errors.append(f"project_code '{code_clean}' is already used by {project_codes[code_clean].name}.")
            else:
                project_codes[code_clean] = pf.path
    former: dict[str, Path] = {}
    for pf in files:
        for old in former_slugs(pf.data):
            if old in slugs and slugs[old] != pf.path:
                pf.errors.append(f"'formerly' entry '{old}' is the current slug of {slugs[old].name}.")
            elif old in former and former[old] != pf.path:
                pf.errors.append(f"'formerly' entry '{old}' is also claimed by {former[old].name}.")
            else:
                former[old] = pf.path
    for pf in files:
        for task in pf.data.get("tasks", []) if isinstance(pf.data.get("tasks"), list) else []:
            tid = task.get("id") if isinstance(task, dict) else None
            if isinstance(tid, str):
                if tid in task_ids and task_ids[tid] != pf.path:
                    pf.errors.append(f"Task id '{tid}' is already used by {task_ids[tid].name}.")
                task_ids.setdefault(tid, pf.path)
    return files


def project_mission_id(slug: str) -> str:
    return f"{PROJECT_ID_PREFIX}{slug}"


def former_slugs(data: Any) -> list[str]:
    """Earlier slugs of a project, as listed under ``formerly`` (invalid entries are ignored)."""
    raw = data.get("formerly") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []
    return [x for x in raw if isinstance(x, str) and SLUG_RE.match(x) and len(x) <= 64]


def slug_aliases(directory: Path | None = None) -> dict[str, str]:
    """Map every earlier slug to the project's current slug, so old links keep working."""
    out: dict[str, str] = {}
    for pf in load_project_files(directory):
        if pf.errors or not pf.slug:
            continue
        for old in former_slugs(pf.data):
            out.setdefault(old, pf.slug)
    return out


def project_codes_by_slug(directory: Path | None = None) -> dict[str, str]:
    """Map every project slug to its sequential public project code (P1, P2, P3, ...)."""
    out = dict(CANONICAL_PROJECT_CODES)
    for pf in load_project_files(directory):
        if pf.errors or not pf.slug:
            continue
        code = pf.data.get("project_code")
        if isinstance(code, str) and PROJECT_CODE_RE.fullmatch(code.strip()):
            out[pf.slug] = code.strip()
    return out


def _rename_project(conn: Any, old_slug: str, new_slug: str) -> bool:
    """Move a project's record from ``old_slug`` to ``new_slug``, keeping its tasks and findings.

    ``missions.id`` is referenced by tasks, findings and evidence artifacts, so the row is copied
    under the new key, the children are repointed, and the old row is removed, all inside the
    caller's transaction.

    The column list comes from the row itself. A catalogue lookup would depend on which schema
    the tables sit in (on PostgreSQL they need not be in ``current_schema()``), and this runs
    once, on the live database, at deploy.
    """
    old_id, new_id = project_mission_id(old_slug), project_mission_id(new_slug)
    old_row = conn.execute("SELECT * FROM missions WHERE id = ?", (old_id,)).fetchone()
    if old_row is None:
        return False
    if conn.execute("SELECT id FROM missions WHERE id = ? OR slug = ?", (new_id, new_slug)).fetchone() is not None:
        return False  # the new project already exists; never merge two records
    copied = [str(c) for c in old_row.keys() if c not in ("id", "slug")]
    if not copied or not all(_COLUMN_NAME_RE.fullmatch(c) for c in copied):
        # Fail the whole sync (its transaction rolls back) rather than leave a project half moved.
        raise RuntimeError(f"Cannot rename project '{old_slug}': unexpected columns in its record.")
    col_sql = ", ".join(copied)
    conn.execute(
        f"INSERT INTO missions (id, slug, {col_sql}) SELECT ?, ?, {col_sql} FROM missions WHERE id = ?",
        (new_id, new_slug, old_id),
    )
    for table in ("tasks", "findings"):
        conn.execute(
            f"UPDATE {table} SET mission_id = ?, mission_slug = ? WHERE mission_id = ?",
            (new_id, new_slug, old_id),
        )
    conn.execute("UPDATE evidence_artifacts SET mission_id = ? WHERE mission_id = ?", (new_id, old_id))
    conn.execute("UPDATE whispers SET mission_slug = ? WHERE mission_slug = ?", (new_slug, old_slug))
    conn.execute("DELETE FROM missions WHERE id = ?", (old_id,))
    return True


def project_task_id(task_id: str) -> str:
    return f"prj-{task_id}"


def _fingerprint(files: list[ProjectFile]) -> int:
    digest = hashlib.sha256()
    for pf in files:
        digest.update(pf.path.name.encode())
        digest.update(json.dumps(pf.data, sort_keys=True).encode())
    # seed_state.version is a 32-bit INTEGER; keep the fingerprint inside that range.
    return int(digest.hexdigest()[:7], 16)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sync_projects(
    db: Any,
    directory: Path | None = None,
    *,
    allow_archive_all: bool = False,
) -> dict[str, Any]:
    """Upsert valid project files into ``missions``/``tasks``. Invalid files are skipped, never half-loaded.

    Runs on every hub start; it is a no-op when the project files are unchanged.
    Existing task execution history is preserved: only queued tasks are refreshed.
    Fails closed (does not archive live projects) if the projects directory is missing or yields
    zero valid files when community projects already exist unless ``allow_archive_all=True``.
    """
    from aiwatchdogs.hub.database import PG_SEED_ADVISORY_LOCK_KEY, dumps_json

    root = directory or default_projects_dir()
    if not root.is_dir():
        return {"synced": 0, "skipped": [], "changed": False, "warning": f"Projects directory not found: {root}"}

    files = load_project_files(root)
    valid = [pf for pf in files if not pf.errors]
    skipped = [{"file": pf.path.name, "errors": pf.errors} for pf in files if pf.errors]
    fingerprint = _fingerprint(valid)

    with db.read() as conn:
        row = conn.execute("SELECT version FROM seed_state WHERE key = ?", (SYNC_STATE_KEY,)).fetchone()
        if row is not None and int(row["version"]) == fingerprint:
            return {"synced": 0, "skipped": skipped, "changed": False}
        if not valid and not allow_archive_all:
            existing_active = conn.execute(
                "SELECT COUNT(*) AS c FROM missions WHERE is_community_project = 1 AND status != 'archived'"
            ).fetchone()
            if existing_active is not None and int(existing_active["c"]) > 0:
                return {
                    "synced": 0,
                    "skipped": skipped,
                    "changed": False,
                    "warning": "Refusing to archive active community projects when 0 valid project files were loaded.",
                }

    now = _utc_now_iso()
    with db.transaction() as conn:
        if getattr(db, "is_postgres", False):
            conn.execute(f"SELECT pg_advisory_xact_lock({PG_SEED_ADVISORY_LOCK_KEY})")
        # Re-check fingerprint after acquiring advisory lock to prevent redundant cold-start writes.
        locked_row = conn.execute("SELECT version FROM seed_state WHERE key = ?", (SYNC_STATE_KEY,)).fetchone()
        if locked_row is not None and int(locked_row["version"]) == fingerprint:
            return {"synced": 0, "skipped": skipped, "changed": False}
        for pf in valid:
            d = pf.data
            priority = d.get("priority", DEFAULT_PRIORITY)
            for old_slug in former_slugs(d):
                _rename_project(conn, old_slug, d["slug"])
            mid = project_mission_id(d["slug"])
            posted = d["posted_by"]
            tasks = d.get("tasks", [])
            methodology = str(d.get("methodology_code") or (tasks[0].get("methodology_code") if tasks else "") or "")
            qty = str(tasks[0].get("financial_quantity_type", "")) if tasks else ""
            status = d.get("status", "active")
            conn.execute(
                """
                INSERT INTO missions (
                    id, slug, title, description, pillar, methodology_code,
                    jurisdiction, ministry_or_authority, statutory_anchors_json,
                    source_ids_json, sample_dataset_path, data_mode, status,
                    priority, total_tasks, completed_tasks, verified_findings_count,
                    cleared_count, cleared_findings_count, unevaluable_count,
                    monetary_audited_inr, monetary_unit, financial_quantity_type, created_at,
                    posted_by_name, posted_by_handle, posted_by_url, project_sources_json,
                    project_scope_json, enlisted_pr_url, planned_tasks_json, is_community_project
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', '', 'live_public', ?, ?, ?, 0, 0, 0, 0, 0, 0.0,
                          'inr', ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    description = excluded.description,
                    pillar = excluded.pillar,
                    methodology_code = excluded.methodology_code,
                    jurisdiction = excluded.jurisdiction,
                    ministry_or_authority = excluded.ministry_or_authority,
                    statutory_anchors_json = excluded.statutory_anchors_json,
                    status = excluded.status,
                    priority = excluded.priority,
                    financial_quantity_type = excluded.financial_quantity_type,
                    posted_by_name = excluded.posted_by_name,
                    posted_by_handle = excluded.posted_by_handle,
                    posted_by_url = excluded.posted_by_url,
                    project_sources_json = excluded.project_sources_json,
                    project_scope_json = excluded.project_scope_json,
                    enlisted_pr_url = excluded.enlisted_pr_url,
                    planned_tasks_json = excluded.planned_tasks_json,
                    is_community_project = 1
                """,
                (
                    mid,
                    d["slug"],
                    d["title"].strip(),
                    d["summary"].strip(),
                    d["pillar"],
                    methodology,
                    d["jurisdiction"],
                    d["authority"],
                    dumps_json(d.get("statutory_anchors", [])),
                    status,
                    priority,
                    len(tasks),
                    qty,
                    now,
                    str(posted.get("name", "")).strip(),
                    str(posted.get("handle", "") or "").strip(),
                    str(posted.get("url", "") or "").strip(),
                    dumps_json(d.get("sources", [])),
                    dumps_json({"scope": d.get("scope", []), "out_of_scope": d.get("out_of_scope", [])}),
                    str(d.get("enlisted_pr_url", "") or ""),
                    dumps_json(
                        [
                            {k: p[k] for k in ("title", "type", "estimated_tokens", "estimate_basis")}
                            for p in d.get("planned_tasks", [])
                        ]
                    ),
                ),
            )
            for task in tasks:
                tid = project_task_id(task["id"])
                code = task.get("methodology_code", methodology)
                task_type = "research" if code == INFLATION_METHODOLOGY else "anomaly_scan"
                payload = dict(task["payload"])
                payload.setdefault("methodology_code", code)
                payload.setdefault("dataset_key", task["dataset_key"])
                payload.setdefault("financial_quantity_type", task["financial_quantity_type"])
                payload.setdefault("dataset_url", payload["retrieval_url"])
                payload.setdefault("source_url", payload.get("source_landing_page", ""))
                payload.setdefault("is_demo_sample", False)
                payload["project_slug"] = d["slug"]
                if task_type == "research":
                    payload["depends_on"] = [project_task_id(dep) for dep in task.get("depends_on", [])]
                payload_str = dumps_json(payload)
                existing = conn.execute("SELECT status FROM tasks WHERE id = ?", (tid,)).fetchone()
                if existing is None:
                    conn.execute(
                        """
                        INSERT INTO tasks (
                            id, mission_id, mission_slug, methodology_code, task_type,
                            title, instructions, payload_json, target_finding_id,
                            excluded_handles_json, status, dataset_key, financial_quantity_type,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, '[]', 'queued', ?, ?, ?, ?)
                        """,
                        (
                            tid,
                            mid,
                            d["slug"],
                            code,
                            task_type,
                            task["title"],
                            task["instructions"],
                            payload_str,
                            task["dataset_key"],
                            task["financial_quantity_type"],
                            now,
                            now,
                        ),
                    )
                elif existing["status"] == "queued":
                    conn.execute(
                        """
                        UPDATE tasks SET title = ?, instructions = ?, payload_json = ?, methodology_code = ?, task_type = ?,
                            dataset_key = ?, financial_quantity_type = ?, updated_at = ?
                        WHERE id = ? AND status = 'queued'
                        """,
                        (
                            task["title"],
                            task["instructions"],
                            payload_str,
                            code,
                            task_type,
                            task["dataset_key"],
                            task["financial_quantity_type"],
                            now,
                            tid,
                        ),
                    )
                # Estimates are descriptive, so refresh them whatever state the task is in.
                conn.execute(
                    "UPDATE tasks SET work_type = ?, estimated_tokens = ?, estimate_basis = ? WHERE id = ?",
                    (
                        task.get("type", DEFAULT_WORK_TYPE),
                        task["estimated_tokens"],
                        task["estimate_basis"],
                        tid,
                    ),
                )
                conn.execute(
                    "UPDATE tasks SET estimated_tokens = ?, estimate_basis = ? "
                    "WHERE mission_id = ? AND dataset_key = ? AND task_type = 'peer_verify'",
                    (task["estimated_tokens"], PEER_ESTIMATE_BASIS, mid, task["dataset_key"]),
                )
            if getattr(db, "is_postgres", False):
                conn.execute("SELECT id FROM missions WHERE id = ? FOR UPDATE", (mid,))
            # Retire obsolete unstarted primary tasks removed or superseded in the project file,
            # while strictly preserving any claimed/completed tasks and peer_verify history.
            current_task_ids = [project_task_id(t["id"]) for t in tasks]
            if current_task_ids:
                existing_queued = conn.execute(
                    "SELECT id FROM tasks WHERE mission_id = ? AND status = 'queued' "
                    "AND task_type != 'peer_verify' AND claimed_by_agent_id IS NULL AND completed_at IS NULL",
                    (mid,),
                ).fetchall()
                keep_ids = set(current_task_ids)
                obsolete_ids = [str(r["id"]) for r in existing_queued if str(r["id"]) not in keep_ids]
                for idx in range(0, len(obsolete_ids), 200):
                    chunk = obsolete_ids[idx : idx + 200]
                    ph = ",".join("?" for _ in chunk)
                    conn.execute(
                        f"DELETE FROM tasks WHERE mission_id = ? AND status = 'queued' "
                        f"AND task_type != 'peer_verify' AND claimed_by_agent_id IS NULL AND completed_at IS NULL "
                        f"AND id IN ({ph})",
                        (mid, *chunk),
                    )
            cnt = conn.execute(
                "SELECT COUNT(*) AS c FROM tasks WHERE mission_id = ? AND task_type != 'peer_verify'",
                (mid,),
            ).fetchone()
            done_cnt = conn.execute(
                "SELECT COUNT(*) AS c FROM tasks WHERE mission_id = ? AND task_type != 'peer_verify' AND status = 'completed'",
                (mid,),
            ).fetchone()
            conn.execute(
                "UPDATE missions SET total_tasks = ?, completed_tasks = ? WHERE id = ?",
                (int(cnt["c"]), int(done_cnt["c"]), mid),
            )

        # A project whose file was removed from the repository is archived (its record stays).
        active_ids = [project_mission_id(pf.data["slug"]) for pf in valid]
        if active_ids:
            placeholders = ",".join("?" for _ in active_ids)
            conn.execute(
                f"UPDATE missions SET status = 'archived' "
                f"WHERE is_community_project = 1 AND id NOT IN ({placeholders})",
                active_ids,
            )
        elif allow_archive_all:
            conn.execute("UPDATE missions SET status = 'archived' WHERE is_community_project = 1")

        conn.execute(
            """
            INSERT INTO seed_state (key, version, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET version = excluded.version, updated_at = excluded.updated_at
            """,
            (SYNC_STATE_KEY, fingerprint, now),
        )
    return {"synced": len(valid), "skipped": skipped, "changed": True}
