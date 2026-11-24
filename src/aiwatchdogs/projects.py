"""Community-posted audit projects.

Anyone who wants compute to scrutinise public spending can enlist a project by
adding one JSON file under ``projects/`` in the repository and opening a pull
request (see ``projects/README.md`` and ``/enlist.txt``). On every deploy the hub
syncs those files into the ``missions`` table (projects are stored as missions
internally so the existing agent protocol keeps working).

This module owns the file format, validation (used by CI and by
``aiwatchdogs project validate``) and the database sync.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiwatchdogs.core.safety import _FORBIDDEN_TERM_PATTERNS, contains_pii

PROJECT_ID_PREFIX = "project-"
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
TASK_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
PINNED_GITHUB_RAW_RE = re.compile(
    r"^https://raw\.githubusercontent\.com/[^/]+/[^/]+/[0-9a-f]{40}/.+"
)
# Mutable-branch raw URLs are rejected: tasks must point at bytes that cannot change.
MUTABLE_GITHUB_RAW_RE = re.compile(
    r"^https://raw\.githubusercontent\.com/[^/]+/[^/]+/(?:main|master|HEAD|gh-pages|dev|develop)/"
)

VALID_PILLARS = {"procurement", "budget", "audit", "infra", "scheme", "disclosure"}
VALID_STATUSES = {"active", "needs_data", "paused"}
VALID_QUANTITY_TYPES = {
    "tender_estimates",
    "awarded_contract_values",
    "budget_and_treasury_expenditure",
    "cag_report_monetary_figures",
    "scheme_outlay_and_expenditure",
    "electoral_bond_disclosures",
}
REQUIRED_TOP_LEVEL = ("slug", "title", "summary", "posted_by", "pillar", "jurisdiction", "authority")
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

    return set(_ENGINE_DISPATCH)


def _scan_terms(text: str) -> list[str]:
    return [label for label, pattern in _FORBIDDEN_TERM_PATTERNS if pattern.search(text or "")]


def _is_https(url: Any) -> bool:
    return isinstance(url, str) and url.startswith("https://") and len(url) > len("https://") + 3


def validate_project(data: Any, *, filename_stem: str | None = None) -> list[str]:
    """Return a list of human-readable problems (empty list means the project is valid)."""
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["The file must contain one JSON object."]

    for key in REQUIRED_TOP_LEVEL:
        if key not in data or data[key] in (None, "", {}):
            errors.append(f"Missing required field '{key}'.")

    slug = data.get("slug")
    if isinstance(slug, str):
        if not SLUG_RE.match(slug) or len(slug) > 64:
            errors.append("'slug' must be lowercase words joined by hyphens (max 64 characters).")
        if filename_stem is not None and filename_stem != slug:
            errors.append(f"File name must match the slug: rename it to '{slug}.json'.")

    for key, limit in (("title", 120), ("summary", 600)):
        val = data.get(key)
        if val is not None and (not isinstance(val, str) or len(val.strip()) < 10 or len(val) > limit):
            errors.append(f"'{key}' must be text between 10 and {limit} characters.")

    posted_by = data.get("posted_by")
    if posted_by is not None:
        if not isinstance(posted_by, dict) or not str(posted_by.get("name", "")).strip():
            errors.append("'posted_by.name' is required: say who is posting this project.")
        else:
            url = posted_by.get("url")
            if url not in (None, "") and not _is_https(url):
                errors.append("'posted_by.url' must be an https:// link.")
            handle = posted_by.get("handle")
            if handle not in (None, "") and (not isinstance(handle, str) or len(handle) > 40):
                errors.append("'posted_by.handle' must be a short handle such as '@copwatchIN'.")

    pillar = data.get("pillar")
    if pillar is not None and pillar not in VALID_PILLARS:
        errors.append(f"'pillar' must be one of: {', '.join(sorted(VALID_PILLARS))}.")

    status = data.get("status", "active")
    if status not in VALID_STATUSES:
        errors.append(f"'status' must be one of: {', '.join(sorted(VALID_STATUSES))}.")

    for key in ("scope", "out_of_scope", "statutory_anchors"):
        val = data.get(key, [])
        if not isinstance(val, list) or not all(isinstance(x, str) and x.strip() for x in val):
            errors.append(f"'{key}' must be a list of short text items.")

    sources = data.get("sources", [])
    if not isinstance(sources, list) or not sources:
        errors.append("'sources' must list at least one official public source (name, publisher, url).")
    else:
        for i, src in enumerate(sources):
            if not isinstance(src, dict) or not str(src.get("name", "")).strip() or not _is_https(src.get("url")):
                errors.append(f"sources[{i}] needs a 'name' and an https:// 'url'.")

    methodology = data.get("methodology_code")
    known = _known_methodologies()
    tasks = data.get("tasks", [])
    if not isinstance(tasks, list):
        errors.append("'tasks' must be a list (it can be empty while data is still being found).")
        tasks = []
    if tasks and methodology not in known:
        errors.append(f"'methodology_code' must be one of {sorted(known)} when tasks are included.")
    if not tasks and status == "active":
        errors.append("A project with no tasks yet must use \"status\": \"needs_data\".")

    seen_task_ids: set[str] = set()
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
            if not str(task.get(key, "")).strip():
                errors.append(f"{where}.{key} is required.")
        if task.get("financial_quantity_type") not in (None, "") and task.get(
            "financial_quantity_type"
        ) not in VALID_QUANTITY_TYPES:
            errors.append(f"{where}.financial_quantity_type must be one of {sorted(VALID_QUANTITY_TYPES)}.")
        code = task.get("methodology_code", methodology)
        if code not in known:
            errors.append(f"{where}.methodology_code must be one of {sorted(known)}.")
        payload = task.get("payload")
        if not isinstance(payload, dict):
            errors.append(f"{where}.payload is required.")
            continue
        url = payload.get("retrieval_url")
        if not _is_https(url):
            errors.append(f"{where}.payload.retrieval_url must be an https:// link to the exact file.")
        elif MUTABLE_GITHUB_RAW_RE.match(url) or (
            "raw.githubusercontent.com" in url and not PINNED_GITHUB_RAW_RE.match(url)
        ):
            errors.append(
                f"{where}.payload.retrieval_url must pin a full 40-character commit SHA, not a branch name."
            )
        sha = payload.get("expected_sha256")
        if not isinstance(sha, str) or not SHA256_RE.match(sha):
            errors.append(f"{where}.payload.expected_sha256 must be the 64-character lowercase SHA-256 of the file.")
        if not str(payload.get("official_publisher", "")).strip():
            errors.append(f"{where}.payload.official_publisher is required.")
        if not _is_https(payload.get("source_landing_page")):
            errors.append(f"{where}.payload.source_landing_page must be an https:// link.")

    # Neutral framing (BNS Sec 356) and no personal data, across all human-written text.
    text_blobs = [
        ("title", data.get("title", "")),
        ("summary", data.get("summary", "")),
        ("scope", " ".join(x for x in data.get("scope", []) if isinstance(x, str))),
    ]
    for i, task in enumerate(tasks):
        if isinstance(task, dict):
            text_blobs.append((f"tasks[{i}].title", task.get("title", "")))
            text_blobs.append((f"tasks[{i}].instructions", task.get("instructions", "")))
    for where, text in text_blobs:
        if not isinstance(text, str):
            continue
        for term in _scan_terms(text):
            errors.append(
                f"{where} uses the accusatory term '{term}'. Describe what to check, not who to blame."
            )
    has_pii, tags = contains_pii({k: v for k, v in data.items() if k != "posted_by"})
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
    task_ids: dict[str, Path] = {}
    for pf in files:
        if pf.slug in slugs:
            pf.errors.append(f"Slug '{pf.slug}' is already used by {slugs[pf.slug].name}.")
        else:
            slugs[pf.slug] = pf.path
        for task in pf.data.get("tasks", []) if isinstance(pf.data.get("tasks"), list) else []:
            tid = task.get("id") if isinstance(task, dict) else None
            if isinstance(tid, str):
                if tid in task_ids and task_ids[tid] != pf.path:
                    pf.errors.append(f"Task id '{tid}' is already used by {task_ids[tid].name}.")
                task_ids.setdefault(tid, pf.path)
    return files


def project_mission_id(slug: str) -> str:
    return f"{PROJECT_ID_PREFIX}{slug}"


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


def sync_projects(db: Any, directory: Path | None = None) -> dict[str, Any]:
    """Upsert valid project files into ``missions``/``tasks``. Invalid files are skipped, never half-loaded.

    Runs on every hub start; it is a no-op when the project files are unchanged.
    Existing task execution history is preserved: only queued tasks are refreshed.
    """
    from aiwatchdogs.hub.database import PG_SEED_ADVISORY_LOCK_KEY, dumps_json

    files = load_project_files(directory)
    valid = [pf for pf in files if not pf.errors]
    skipped = [{"file": pf.path.name, "errors": pf.errors} for pf in files if pf.errors]
    fingerprint = _fingerprint(valid)

    with db.read() as conn:
        row = conn.execute("SELECT version FROM seed_state WHERE key = ?", (SYNC_STATE_KEY,)).fetchone()
        if row is not None and int(row["version"]) == fingerprint:
            return {"synced": 0, "skipped": skipped, "changed": False}

    now = _utc_now_iso()
    with db.transaction() as conn:
        if getattr(db, "is_postgres", False):
            conn.execute(f"SELECT pg_advisory_xact_lock({PG_SEED_ADVISORY_LOCK_KEY})")
        for priority, pf in enumerate(valid, start=1):
            d = pf.data
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
                    project_scope_json, enlisted_pr_url, is_community_project
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', '', 'live_public', ?, ?, ?, 0, 0, 0, 0, 0, 0.0,
                          'inr', ?, ?, ?, ?, ?, ?, ?, ?, 1)
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
                ),
            )
            for task in tasks:
                tid = project_task_id(task["id"])
                code = task.get("methodology_code", methodology)
                payload = dict(task["payload"])
                payload.setdefault("methodology_code", code)
                payload.setdefault("dataset_key", task["dataset_key"])
                payload.setdefault("financial_quantity_type", task["financial_quantity_type"])
                payload.setdefault("dataset_url", payload["retrieval_url"])
                payload.setdefault("source_url", payload.get("source_landing_page", ""))
                payload.setdefault("is_demo_sample", False)
                payload["project_slug"] = d["slug"]
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
                        ) VALUES (?, ?, ?, ?, 'anomaly_scan', ?, ?, ?, NULL, '[]', 'queued', ?, ?, ?, ?)
                        """,
                        (
                            tid,
                            mid,
                            d["slug"],
                            code,
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
                        UPDATE tasks SET title = ?, instructions = ?, payload_json = ?, methodology_code = ?,
                            dataset_key = ?, financial_quantity_type = ?, updated_at = ?
                        WHERE id = ? AND status = 'queued'
                        """,
                        (
                            task["title"],
                            task["instructions"],
                            payload_str,
                            code,
                            task["dataset_key"],
                            task["financial_quantity_type"],
                            now,
                            tid,
                        ),
                    )
            cnt = conn.execute("SELECT COUNT(*) AS c FROM tasks WHERE mission_id = ?", (mid,)).fetchone()
            conn.execute("UPDATE missions SET total_tasks = ? WHERE id = ?", (int(cnt["c"]), mid))

        # A project whose file was removed from the repository is archived (its record stays).
        active_ids = [project_mission_id(pf.data["slug"]) for pf in valid]
        if active_ids:
            placeholders = ",".join("?" for _ in active_ids)
            conn.execute(
                f"UPDATE missions SET status = 'archived' "
                f"WHERE is_community_project = 1 AND id NOT IN ({placeholders})",
                active_ids,
            )
        else:
            conn.execute("UPDATE missions SET status = 'archived' WHERE is_community_project = 1")

        conn.execute(
            """
            INSERT INTO seed_state (key, version, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET version = excluded.version, updated_at = excluded.updated_at
            """,
            (SYNC_STATE_KEY, fingerprint, now),
        )
    return {"synced": len(valid), "skipped": skipped, "changed": True}
