"""Desk coordination logic, strict peer quorum, quarantine, treasury, coverage, and read queries."""

from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException, status

from aiwatchdogs.core.coding_agents import normalize_coding_agent, normalize_coding_agent_model
from aiwatchdogs.core.rti import generate_rti_draft
from aiwatchdogs.core.safety import (
    compute_sha256,
    compute_token_digest,
    escape_like,
    lint_neutrality,
    parse_bounded_int,
    parse_finite_float,
    sanitize_csv_cell,
    scan_forbidden_terms,
    scrub_pii,
    scrub_pii_in_obj,
    validate_safe_http_url,
    verify_token_digest,
)
from aiwatchdogs.core.usage import PEER_ESTIMATE_BASIS, UsageReport, public_usage, summarise_tokens, tokens_used
from aiwatchdogs.engines.gigw_matrix import (
    MATRIX_VERSION as W1_MATRIX_VERSION,
    VALID_CHECK_STATES as W1_VALID_CHECK_STATES,
    W1_CHECKS,
    W1_REASON_KEYS,
    build_website_audit_visual_payload,
    load_conformity_matrix,
)
from aiwatchdogs.hub.database import DB_INTEGRITY_ERRORS, Database, dumps_json, loads_json, utc_now_iso
from aiwatchdogs.hub.seed import FINANCIAL_QUANTITY_LABELS, INDEPENDENCE_PLEDGE
from aiwatchdogs.hub.sse import SSEBroker
from aiwatchdogs.projects import CANONICAL_PROJECT_CODES, UNION_BUDGET_METHODOLOGY, UNION_BUDGET_PROJECT_SLUG

HANDLE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,31}$")
RESERVED_HANDLES: frozenset[str] = frozenset(
    {
        "admin",
        "administrator",
        "root",
        "system",
        "hub",
        "moderator",
        "editorial-review",
        "aiwatchdogs",
        "official",
        "support",
        "security",
        "staff",
        "owner",
        "operator",
        "desk",
        "api",
    }
)
LEASE_MINUTES = 45
MAX_LEASE_TOTAL_MINUTES = 180
MAX_CONCURRENT_LEASES_PER_AGENT = 2
MAX_ADJUDICATION_ROUNDS = 3
MAX_WHISPERS_PER_LEASE = 25
MAX_MONETARY_INR = 1e14
MAX_PLEDGE_INR = 1e12
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BASE_SECONDS = 30
DEFAULT_PARKED_REQUEUE_MINUTES = 120
# Most tasks one project page lists (audits and the peer checks they spawn).
MAX_PROJECT_TASKS_LISTED = 2000
# A project with more tasks than that lists only its first open ones; the rest are paged.
LARGE_PROJECT_TASKS_LISTED = 200
MAX_TASK_PAGE = 100
# Projects an agent may ask for by name even when the hub hands work out by priority.
OPEN_CHOICE_PROJECTS = frozenset({"police-finances", "gov-in-public-information", UNION_BUDGET_PROJECT_SLUG})

# What the desk asks an agent to be for one task. Sent with every claim so that any agent,
# whatever tool runs it, knows its job without reading the task type.
TASK_ROLES: dict[str, dict[str, str]] = {
    "auditor": {
        "name": "auditor",
        "title": "Auditor",
        "brief": (
            "You are the first agent on this check. Download the pinned official file, verify its "
            "SHA-256, run the deterministic check, and submit either a neutral lead or a cleared "
            "result. Two other agents will repeat your work independently."
        ),
        "submit_outcomes": "finding | cleared",
    },
    "peer_verifier": {
        "name": "peer_verifier",
        "title": "Peer verifier",
        "brief": (
            "Another agent submitted this lead. Independently download the same pinned file, verify "
            "its SHA-256, re-run the check and submit an explicit verdict with all four checks. "
            "You can never verify your own lead."
        ),
        "submit_outcomes": "verification",
    },
    "adjudicator": {
        "name": "adjudicator",
        "title": "Adjudicator",
        "brief": (
            "Two agents disagreed on this lead. Independently download the same pinned file, verify "
            "its SHA-256, re-run the check and submit the tie-breaking verdict with all four checks."
        ),
        "submit_outcomes": "verification",
    },
}


def task_role(task_type: Any, payload: Any = None) -> dict[str, str]:
    """The role a claimed task puts its agent in: auditor, peer verifier or adjudicator."""
    kind = str(task_type or "").strip().lower()
    if kind == "anomaly_scan" and isinstance(payload, dict) and payload.get("methodology_code") == "W1":
        return {
            "name": "auditor",
            "title": "Website auditor",
            "brief": "Verify the batch checksum, review the assigned public pages and submit one dated observation for every host.",
            "submit_outcomes": "observations",
        }
    if kind == "peer_verify":
        review = str(payload.get("review_type") or "") if isinstance(payload, dict) else ""
        return dict(TASK_ROLES["adjudicator" if review == "adjudication" else "peer_verifier"])
    return dict(TASK_ROLES["auditor"])

_TASK_LIST_COLUMNS = (
    "t.id, t.task_type, t.methodology_code, t.title, t.status, t.claimed_by_handle, "
    "t.progress_pct, t.retry_count, t.available_at, t.target_finding_id, t.payload_json, "
    "t.created_at, t.completed_at, t.work_type, t.estimated_tokens, t.estimate_basis, "
    "t.usage_json, t.lease_started_at, t.attempt_count"
)
# A peer check of a lead that is held for privacy or legal review is not listed.
_TASK_NOT_QUARANTINED = (
    "(t.target_finding_id IS NULL OR NOT EXISTS ("
    "SELECT 1 FROM findings qf WHERE qf.id = t.target_finding_id "
    "AND (COALESCE(qf.quarantined, 0) = 1 OR qf.status = 'quarantined')))"
)


def _public_task(t: Any, t_payload: dict[str, Any]) -> dict[str, Any]:
    """One task as a project page lists it."""
    return {
        "id": t["id"],
        "task_type": t["task_type"],
        "methodology_code": t["methodology_code"],
        "title": t["title"],
        "status": t["status"],
        "role": task_role(t["task_type"], t_payload)["name"],
        "claimed_by_handle": t["claimed_by_handle"],
        "progress_pct": t["progress_pct"],
        "retry_count": int(_row_get(t, "retry_count", 0) or 0),
        "available_at": _row_get(t, "available_at", None),
        "target_finding_id": t["target_finding_id"],
        "dataset_url": t_payload.get("dataset_url"),
        "retrieval_url": t_payload.get("retrieval_url") or t_payload.get("dataset_url"),
        "official_publisher": t_payload.get("official_publisher"),
        "source_landing_page": t_payload.get("source_landing_page") or t_payload.get("source_url"),
        "mirror_name": t_payload.get("mirror_name"),
        "source_url": t_payload.get("source_url"),
        "expected_sha256": t_payload.get("expected_sha256"),
        "created_at": t["created_at"],
        "completed_at": t["completed_at"],
        "work_type": t["work_type"] or ("peer_verify" if t["task_type"] == "peer_verify" else None),
        "estimated_tokens": t["estimated_tokens"],
        "estimate_basis": t["estimate_basis"] or None,
        "usage": public_usage(
            loads_json(t["usage_json"] or "{}", {}),
            started_at=_row_get(t, "lease_started_at", None),
            completed_at=t["completed_at"] if t["status"] == "completed" else None,
            attempts=int(_row_get(t, "attempt_count", 0) or 0),
        ),
    }


_COVERAGE_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_COVERAGE_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _project_coverage(slug: str) -> dict[str, Any]:
    """The coverage summary a project publishes beside its file (empty when it has none)."""
    from aiwatchdogs.projects import default_projects_dir

    if not _COVERAGE_SLUG_RE.match(slug or ""):
        return {}
    path = default_projects_dir() / slug / "coverage.json"
    try:
        stamp = path.stat().st_mtime
    except OSError:
        return {}
    cached = _COVERAGE_CACHE.get(str(path))
    if cached is not None and cached[0] == stamp:
        return cached[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    _COVERAGE_CACHE[str(path)] = (stamp, data)
    return data


VALID_VERDICTS: frozenset[str] = frozenset(
    {"confirm", "dispute", "needs_review", "reject_false_positive", "flag_pii_or_legal"}
)
VALID_REVIEW_TYPES: frozenset[str] = frozenset(
    {"computational_reproduction", "editorial_review", "adjudication"}
)
CANONICAL_CHECKS: tuple[str, ...] = (
    "bytes_digest",
    "deterministic_reproduction",
    "metric_tolerance",
    "neutrality_privacy",
)
_LEGACY_CHECK_TO_CANONICAL: dict[str, str] = {
    "provenance_ok": "bytes_digest",
    "math_reproduced": "deterministic_reproduction",
    "rule_valid": "metric_tolerance",
    "legal_pii_ok": "neutrality_privacy",
}
VALID_CHECK_STATES: frozenset[str] = frozenset({"pass", "fail", "unrecorded"})

# Published transparent scoring weights used to derive both all-time and hourly leaderboards
# strictly from timestamped rows in ``score_events`` (zero hybrid double-counting).
SCORE_WEIGHTS: dict[str, int] = {
    "finding_submitted": 10,
    "finding_verified_author_bonus": 20,
    "peer_verification": 15,
    "cleared_scan": 10,
}

QUARANTINE_NOTICE = (
    "This record has been quarantined pending human privacy and legal review. "
    "Raw narrative, metrics, citations, and traces are withheld."
)

_SHARED_DATASET_NOTES: dict[str, str] = {
    "assam-ocds-single-bid": "Shares the 39,502-row Assam OCDS FY 2016–22 tender-estimate CSV with M2; deduplicated in coverage totals.",
    "assam-gfr-split-tendering": "Shares the 39,502-row Assam OCDS FY 2016–22 tender-estimate CSV with M1; deduplicated in coverage totals.",
    "ocds-vendor-concentration": "Shares the 43,669-release Assam OCDS JSON package with M4; deduplicated in coverage totals.",
    "ocds-cost-escalation-watch": "Shares the 43,669-release Assam OCDS JSON package with M3; deduplicated in coverage totals.",
}

_W1_REASON_KEYS: tuple[str, ...] = W1_REASON_KEYS
_P1_DELHI_UT_RE = re.compile(
    r"\b(?:delhi|jammu and kashmir|j&k|puducherry|andaman|nicobar|chandigarh|"
    r"dadra|nagar haveli|daman|diu|ladakh|lakshadweep)\b",
    re.IGNORECASE,
)
_P1_UNION_CENTRAL_RE = re.compile(
    r"(?:police-union-|\bunion demand 51\b|\bcentral reserve police\b|\bborder security force\b|"
    r"\bcentral industrial security\b|\bindo-tibetan border\b|\bsashastra seema bal\b|"
    r"\bassam rifles\b|\bnational security guard\b|\bspecial protection group\b|"
    r"\bintelligence bureau\b|\bcentral police organisations\b|\bcapf\b|\(union\))",
    re.IGNORECASE,
)
_P1_GROUP_ORDER: tuple[str, ...] = (
    "Union & Central Forces",
    "Delhi & Union Territories",
    "State Police",
)


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _future_iso(minutes: int = LEASE_MINUTES) -> str:
    dt = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _future_iso_seconds(seconds: int) -> str:
    dt = datetime.now(timezone.utc) + timedelta(seconds=max(0, int(seconds)))
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_finite_float(value: Any, default: float = 0.0) -> float:
    """Safely coerce a stored DB float to a finite Python float for JSON responses."""
    if value is None or isinstance(value, bool):
        return default
    try:
        num = float(value)
    except (TypeError, ValueError):
        return default
    return num if math.isfinite(num) else default


def _inr_to_cr(amount: float | int | None) -> float:
    return round(_safe_finite_float(amount, 0.0) / 10_000_000.0, 2)


def _scrub_recursive(value: Any) -> tuple[Any, list[str]]:
    """Recursively scrub PII from strings, nested containers, and dict keys with depth cap."""
    return scrub_pii_in_obj(value, max_depth=16)


def _parse_float_422(
    value: Any,
    *,
    field: str,
    min_value: float | None = 0.0,
    max_value: float | None = MAX_MONETARY_INR,
    default: float | None = 0.0,
) -> float:
    try:
        return parse_finite_float(
            value,
            field=field,
            min_value=min_value,
            max_value=max_value,
            default=default,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _parse_int_422(
    value: Any,
    *,
    field: str,
    min_value: int = 0,
    max_value: int = 10_000_000,
    default: int | None = 0,
) -> int:
    try:
        return parse_bounded_int(
            value,
            field=field,
            min_value=min_value,
            max_value=max_value,
            default=default,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _validate_optional_url_422(
    value: Any,
    *,
    field: str,
    require_https: bool = False,
) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    if not cleaned:
        return None
    try:
        return validate_safe_http_url(
            cleaned,
            require_https=require_https,
            allow_private=False,
            max_length=2048,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{field}: {exc}") from exc


def _validate_json_blob(
    obj: Any,
    *,
    field_name: str,
    max_depth: int = 10,
    max_nodes: int = 400,
    max_bytes: int = 65_536,
) -> None:
    """Enforce nesting depth, node count, finite-float, and byte limits on untrusted JSON blobs."""
    if obj is None:
        return
    nodes = 0

    def _walk(item: Any, depth: int) -> None:
        nonlocal nodes
        if depth > max_depth:
            raise HTTPException(
                status_code=422,
                detail=f"{field_name} exceeds maximum nesting depth ({max_depth}).",
            )
        nodes += 1
        if nodes > max_nodes:
            raise HTTPException(
                status_code=422,
                detail=f"{field_name} exceeds maximum element count ({max_nodes}).",
            )
        if isinstance(item, float) and not math.isfinite(item):
            raise HTTPException(
                status_code=422,
                detail=f"{field_name} contains non-finite float values (NaN/Infinity not allowed).",
            )
        if isinstance(item, dict):
            for k, v in item.items():
                if not isinstance(k, str) or len(k) > 128:
                    raise HTTPException(
                        status_code=422,
                        detail=f"{field_name} contains invalid or oversized key.",
                    )
                _walk(v, depth + 1)
        elif isinstance(item, list):
            for elem in item:
                _walk(elem, depth + 1)
        elif isinstance(item, str) and len(item) > 8192:
            raise HTTPException(
                status_code=422,
                detail=f"{field_name} contains a string exceeding 8192 characters.",
            )

    _walk(obj, 0)
    try:
        serialized = json.dumps(obj, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"{field_name} is not valid JSON: {exc}",
        ) from exc
    if len(serialized) > max_bytes:
        raise HTTPException(
            status_code=422,
            detail=f"{field_name} exceeds maximum serialized size ({max_bytes} bytes).",
        )


def _validate_whisper_text(raw: str | None) -> str:
    """Scrub, length-bound, and neutrality-lint public agent whisper text."""
    cleaned = str(raw or "").strip()
    if not cleaned:
        return ""
    if len(cleaned) > 500:
        raise HTTPException(status_code=422, detail="whisper must be at most 500 characters.")
    scrubbed, _ = scrub_pii(cleaned)
    bad_terms = scan_forbidden_terms(scrubbed)
    if bad_terms:
        raise HTTPException(
            status_code=422,
            detail=(
                "Whisper rejected by automated neutrality guardrail (BNS Sec 356): "
                f"disallowed term(s): {', '.join(bad_terms)}."
            ),
        )
    return scrubbed


def _check_reproduced_metrics_consistency(
    finding_metrics: dict[str, Any],
    reproduced_metrics: dict[str, Any],
) -> tuple[bool, list[str]]:
    """Verify overlapping numeric metrics between finding and verifier agree within tolerance."""
    mismatches: list[str] = []
    if not isinstance(finding_metrics, dict) or not isinstance(reproduced_metrics, dict):
        return True, mismatches
    for key, f_raw in finding_metrics.items():
        if key not in reproduced_metrics:
            continue
        r_raw = reproduced_metrics[key]
        if isinstance(f_raw, bool) or isinstance(r_raw, bool):
            continue
        if isinstance(f_raw, (int, float)) and isinstance(r_raw, (int, float)):
            f_val = float(f_raw)
            r_val = float(r_raw)
            if not (math.isfinite(f_val) and math.isfinite(r_val)):
                mismatches.append(f"{key}: non-finite metric")
                continue
            denom = max(abs(f_val), 1.0)
            if abs(f_val - r_val) > max(0.05, 0.02 * denom):
                mismatches.append(f"{key} (finding={f_val}, reproduced={r_val})")
    return len(mismatches) == 0, mismatches


def _normalize_check_state(raw_val: Any, *, field_name: str, strict: bool) -> str:
    if raw_val is None:
        return "unrecorded"
    if isinstance(raw_val, bool):
        return "pass" if raw_val else "fail"
    if isinstance(raw_val, str):
        norm = raw_val.strip().lower()
        if norm in VALID_CHECK_STATES:
            return norm
        if norm in {"true", "ok", "passed"}:
            return "pass"
        if norm in {"false", "failed"}:
            return "fail"
        if norm in {"", "unknown", "none", "null", "n/a"}:
            return "unrecorded"
    if strict:
        raise HTTPException(
            status_code=422,
            detail=f"checks.{field_name} must be 'pass', 'fail', or 'unrecorded'",
        )
    return "unrecorded"


def _normalize_checks(raw_checks: Any, *, strict: bool = False) -> dict[str, str]:
    """Normalize verification checks to the 4 canonical tri-state keys.

    Missing checks ALWAYS normalize to ``"unrecorded"`` — never ``"pass"``.
    """
    result: dict[str, str] = {key: "unrecorded" for key in CANONICAL_CHECKS}
    if not isinstance(raw_checks, dict):
        if strict and raw_checks is not None:
            raise HTTPException(status_code=422, detail="verification.checks must be an object")
        return result

    # Map legacy keys first so canonical keys take precedence if both supplied
    for legacy_key, canon_key in _LEGACY_CHECK_TO_CANONICAL.items():
        if legacy_key in raw_checks:
            result[canon_key] = _normalize_check_state(
                raw_checks[legacy_key], field_name=legacy_key, strict=strict
            )

    for canon_key in CANONICAL_CHECKS:
        if canon_key in raw_checks:
            result[canon_key] = _normalize_check_state(
                raw_checks[canon_key], field_name=canon_key, strict=strict
            )

    return result


def _all_checks_passed(checks: dict[str, str]) -> bool:
    return all(checks.get(k) == "pass" for k in CANONICAL_CHECKS)


def _is_quarantined_row(row: Any) -> bool:
    try:
        q = bool(row["quarantined"])
    except (KeyError, IndexError):
        q = False
    return q or str(row["status"] or "") == "quarantined"


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    try:
        val = row[key]
        return default if val is None else val
    except (KeyError, IndexError):
        return default


def _domain_of(url: str | None) -> str:
    if not url:
        return ""
    try:
        host = (urlparse(url).hostname or "").lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def _agents_may_choose_project() -> bool:
    """Compute goes where the team's priorities send it unless the hub opts out."""
    return os.environ.get("AIWATCHDOGS_ALLOW_PROJECT_CHOICE", "").strip().lower() in {"1", "true", "yes"}


class DeskService:
    """Implements Coordinator Hub read-plane queries and Desk write-plane coordination."""

    def resolve_slug(self, slug: str) -> str:
        """Follow a renamed project's earlier slug to its current one."""
        return self.slug_aliases.get(slug, slug)

    def resolve_project_code(self, slug: str, fallback: str = "") -> str:
        """Return sequential public project code (P1, P2, P3, ...) for a mission slug."""
        canon = self.resolve_slug(str(slug or ""))
        return self.project_codes.get(canon) or CANONICAL_PROJECT_CODES.get(canon) or fallback

    def __init__(self, db: Database, broker: SSEBroker) -> None:
        # Earlier slug -> current slug for renamed projects (filled by the app from projects/).
        self.slug_aliases: dict[str, str] = {}
        self.project_codes: dict[str, str] = dict(CANONICAL_PROJECT_CODES)
        self.db = db
        self.broker = broker
        self._rate_limit_prune_lock = threading.Lock()
        self._last_rate_limit_prune: float = 0.0
        self._w1_cache_lock = threading.Lock()
        self._w1_visual_cache: dict[str, tuple[tuple[Any, ...], dict[str, Any]]] = {}

    # ------------------------------------------------------------------
    # Row serializers
    # ------------------------------------------------------------------

    @staticmethod
    def _serialize_source(row: Any) -> dict[str, Any]:
        return {
            "id": row["id"],
            "slug": row["slug"],
            "name": row["name"],
            "pillar": row["pillar"],
            "authority": row["authority"],
            "base_url": row["base_url"],
            "official_publisher": _row_get(row, "official_publisher", "") or row["authority"],
            "source_landing_page": _row_get(row, "source_landing_page", "") or row["base_url"],
            "mirror_name": _row_get(row, "mirror_name", ""),
            "retrieval_url": _row_get(row, "retrieval_url", "") or row["base_url"],
            "ingestion_tier": row["ingestion_tier"],
            "connector_type": row["connector_type"],
            "data_contents": row["data_contents"],
            "format_access": row["format_access"],
            "config": loads_json(row["config_json"], {}),
            "robots_policy": row["robots_policy"],
            "last_synced_at": row["last_synced_at"],
        }

    @staticmethod
    def _serialize_correction(row: Any) -> dict[str, Any]:
        return {
            "id": row["id"],
            "finding_id": row["finding_id"],
            "correction_type": row["correction_type"],
            "previous_status": row["previous_status"],
            "new_status": row["new_status"],
            "summary": row["summary"],
            "details": row["details"],
            "source_evidence_url": row["source_evidence_url"],
            "reviewed_by": row["reviewed_by"],
            "created_at": row["created_at"],
        }

    @staticmethod
    def _serialize_mission(
        row: Any,
        queued_tasks: int = 0,
        claimed_tasks: int = 0,
        peer_review_count: int = 0,
        rungs: list[dict[str, Any]] | None = None,
        unique_coverage_inr: float | None = None,
        cleared_findings_count: int | None = None,
        token_rows: list[tuple[str, int | None, Any]] | None = None,
        token_summary: dict[str, Any] | None = None,
        *,
        project_code: str | None = None,
        primary_total_tasks: int | None = None,
        primary_completed_tasks: int | None = None,
        peer_checks_total: int = 0,
        peer_checks_done: int = 0,
        peer_checks_open: int = 0,
    ) -> dict[str, Any]:
        planned = DeskService._planned_tasks(row)
        total = int(primary_total_tasks if primary_total_tasks is not None else (row["total_tasks"] or 0))
        completed = int(primary_completed_tasks if primary_completed_tasks is not None else (row["completed_tasks"] or 0))
        progress_pct = round((completed / total) * 100.0, 1) if total > 0 else 0.0
        workload_inr = float(row["monetary_audited_inr"] or 0.0)
        uniq_inr = float(unique_coverage_inr if unique_coverage_inr is not None else workload_inr)
        qty_type = str(_row_get(row, "financial_quantity_type", "") or "")
        qty_label = FINANCIAL_QUANTITY_LABELS.get(qty_type, qty_type or "Reported project figure")
        cleared_scans = int(row["cleared_count"] or 0)
        cleared_findings = int(
            cleared_findings_count
            if cleared_findings_count is not None
            else _row_get(row, "cleared_findings_count", 0)
        )
        unevaluable = int(_row_get(row, "unevaluable_count", 0))
        resolved_code = (
            project_code
            or CANONICAL_PROJECT_CODES.get(str(row["slug"] or ""))
            or str(row["methodology_code"] or "")
        )
        return {
            "id": row["id"],
            "slug": row["slug"],
            "project_code": resolved_code,
            "title": row["title"],
            "description": row["description"],
            "pillar": row["pillar"],
            "methodology_code": row["methodology_code"],
            "jurisdiction": row["jurisdiction"],
            "ministry_or_authority": row["ministry_or_authority"],
            "statutory_anchors": loads_json(row["statutory_anchors_json"], []),
            "source_ids": loads_json(row["source_ids_json"], []),
            "sample_dataset_path": row["sample_dataset_path"],
            "data_mode": row["data_mode"],
            "is_demo_sample": row["data_mode"] == "demo_sample",
            "status": row["status"],
            "priority": row["priority"],
            "total_tasks": total,
            "completed_tasks": completed,
            "claimed_tasks": int(claimed_tasks),
            "queued_tasks": queued_tasks,
            "peer_checks_total": int(peer_checks_total),
            "peer_checks_done": int(peer_checks_done),
            "peer_checks_open": int(peer_checks_open),
            "progress_pct": progress_pct,
            "verified_findings_count": int(row["verified_findings_count"] or 0),
            "peer_review_count": peer_review_count,
            "cleared_count": cleared_scans,
            "cleared_scans_count": cleared_scans,
            "cleared_findings_count": cleared_findings,
            "unevaluable_count": unevaluable,
            "monetary_audited_inr": workload_inr,
            "monetary_audited_inr_cr": _inr_to_cr(workload_inr),
            "scan_workload_inr": workload_inr,
            "scan_workload_inr_cr": _inr_to_cr(workload_inr),
            "unique_coverage_inr": uniq_inr,
            "unique_coverage_inr_cr": _inr_to_cr(uniq_inr),
            "monetary_unit": str(_row_get(row, "monetary_unit", "inr") or "inr"),
            "financial_quantity_type": qty_type,
            "financial_quantity_label": qty_label,
            "coverage_note": _SHARED_DATASET_NOTES.get(
                row["slug"],
                f"Unit: {qty_label}. Deduplicated by unique dataset key.",
            ),
            "rungs": list(rungs) if rungs is not None else [],
            "created_at": row["created_at"],
            "is_community_project": bool(int(_row_get(row, "is_community_project", 0) or 0)),
            "posted_by": {
                "name": str(_row_get(row, "posted_by_name", "") or ""),
                "handle": str(_row_get(row, "posted_by_handle", "") or ""),
                "url": str(_row_get(row, "posted_by_url", "") or ""),
            },
            "project_sources": loads_json(_row_get(row, "project_sources_json", "[]") or "[]", []),
            "project_scope": loads_json(_row_get(row, "project_scope_json", "{}") or "{}", {}),
            "enlisted_pr_url": str(_row_get(row, "enlisted_pr_url", "") or ""),
            "planned_tasks": planned,
            "token_summary": (
                token_summary
                if token_summary is not None
                else summarise_tokens(token_rows or [], planned)
            ),
        }

    @staticmethod
    def _serialize_agent(
        row: Any,
        *,
        window_score: int | None = None,
        all_time_score: int | None = None,
        hourly_event_score: int | None = None,
    ) -> dict[str, Any]:
        last_hb = _parse_iso(row["last_heartbeat_at"])
        now = datetime.now(timezone.utc)
        age_sec = int((now - last_hb).total_seconds()) if last_hb else 999999
        is_online = age_sec <= 3600
        comp_score = int(all_time_score if all_time_score is not None else (row["compute_score"] or 0))
        hr_score = int(hourly_event_score if hourly_event_score is not None else 0)
        eff_score = int(window_score if window_score is not None else comp_score)
        return {
            "id": row["id"],
            "agent_id": row["id"],
            "handle": row["handle"],
            "display_name": row["display_name"],
            "provider": row["provider"],
            "model": row["model"],
            "coding_agent": str(_row_get(row, "coding_agent", "") or ""),
            "coding_agent_model": str(_row_get(row, "coding_agent_model", "") or ""),
            "mode": row["mode"],
            "region": row["region"],
            "capabilities": loads_json(row["capabilities_json"], []),
            "status": "active" if is_online else "idle",
            "is_active": is_online,
            "heartbeat_age_seconds": max(0, age_sec),
            "tasks_completed": int(row["tasks_completed"] or 0),
            "findings_submitted": int(row["findings_submitted"] or 0),
            "findings_verified": int(row["findings_verified"] or 0),
            "verifications_done": int(row["verifications_done"] or 0),
            "cleared_submitted": int(row["cleared_submitted"] or 0),
            "compute_score": comp_score,
            "score": eff_score,
            "window_score": eff_score,
            "hourly_score": hr_score,
            "current_task_id": row["current_task_id"],
            "joined_at": row["joined_at"],
            "last_heartbeat_at": row["last_heartbeat_at"],
            "profile_url": f"/a/{row['handle']}",
        }

    @staticmethod
    def _serialize_quarantined_finding(
        row: Any,
        *,
        corrections: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        corr_list = list(corrections) if corrections is not None else []
        latest = corr_list[-1] if corr_list else None
        return {
            "id": row["id"],
            "mission_id": row["mission_id"],
            "mission_slug": row["mission_slug"],
            "pillar": row["pillar"],
            "task_id": None,
            "artifact_id": None,
            "source_id": None,
            "title": "Record quarantined pending privacy/legal review",
            "summary": QUARANTINE_NOTICE,
            "editorial_interpretation": "",
            "methodology_code": row["methodology_code"],
            "entity_name": "[Withheld pending privacy/legal review]",
            "jurisdiction": row["jurisdiction"],
            "fiscal_year": row["fiscal_year"],
            "fiscal_year_start": None,
            "fiscal_year_end": None,
            "monetary_exposure_inr": None,
            "monetary_exposure_inr_cr": None,
            "severity": "withheld",
            "anomaly_metrics": {},
            "rule_triggered": "",
            "engine_version": "",
            "ruleset_version": "",
            "as_of_date": None,
            "source_locator": {},
            "source_url": "",
            "artifact_sha256": None,
            "secondary_sha256": None,
            "dataset_path": "",
            "provenance": {},
            "is_demo_sample": bool(row["is_demo_sample"]),
            "citations": [],
            "benign_explanations": [],
            "suggested_authority": "",
            "submitted_by": None,
            "agent_trace": {},
            "legal_lint_passed": False,
            "pii_scrubbed": False,
            "status": "quarantined",
            "editorial_status": "quarantined",
            "quarantined": True,
            "quarantine_notice": QUARANTINE_NOTICE,
            "verification_score": 0.0,
            "confirm_count": 0,
            "dispute_count": int(row["dispute_count"] or 0),
            "required_quorum": int(row["required_quorum"] or 2),
            "has_rti_draft": False,
            "correction_count": len(corr_list),
            "has_corrections": len(corr_list) > 0,
            "latest_correction": latest,
            "created_at": row["created_at"],
            "verified_at": None,
        }

    @classmethod
    def _serialize_finding(
        cls,
        row: Any,
        *,
        has_rti_draft: bool = False,
        corrections: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if _is_quarantined_row(row):
            return cls._serialize_quarantined_finding(row, corrections=corrections)

        exposure = _safe_finite_float(row["monetary_exposure_inr"], 0.0)
        raw_sha = str(row["artifact_sha256"] or "").strip()
        artifact_sha256: str | None = raw_sha if raw_sha else None
        raw_sec_sha = str(_row_get(row, "secondary_sha256", "") or "").strip()
        secondary_sha256: str | None = raw_sec_sha if raw_sec_sha else None

        # Defensive recursive PII scrubbing on nested objects
        anomaly_metrics, _ = _scrub_recursive(loads_json(row["anomaly_metrics_json"], {}))
        agent_trace, _ = _scrub_recursive(loads_json(row["agent_trace_json"], {}))
        citations, _ = _scrub_recursive(loads_json(row["citations_json"], []))
        benign, _ = _scrub_recursive(loads_json(row["benign_explanations_json"], []))
        source_locator, _ = _scrub_recursive(loads_json(_row_get(row, "source_locator_json", "{}"), {}))
        provenance = loads_json(_row_get(row, "provenance_json", "{}"), {})
        if not isinstance(provenance, dict):
            provenance = {}
        editorial_interp, _ = scrub_pii(str(_row_get(row, "editorial_interpretation", "") or ""))

        corr_list = list(corrections) if corrections is not None else []
        latest_corr = corr_list[-1] if corr_list else None
        editorial_status = str(_row_get(row, "editorial_status", "none") or "none")
        raw_ds_path = str(row["dataset_path"] or "").strip()
        safe_ds_path = raw_ds_path if raw_ds_path.startswith(("http://", "https://")) else ""

        return {
            "id": row["id"],
            "mission_id": row["mission_id"],
            "mission_slug": row["mission_slug"],
            "pillar": row["pillar"],
            "task_id": row["task_id"],
            "artifact_id": row["artifact_id"],
            "source_id": _row_get(row, "source_id", None),
            "title": row["title"],
            "summary": row["summary"],
            "editorial_interpretation": editorial_interp,
            "methodology_code": row["methodology_code"],
            "entity_name": row["entity_name"],
            "jurisdiction": row["jurisdiction"],
            "fiscal_year": row["fiscal_year"],
            "fiscal_year_start": _row_get(row, "fiscal_year_start", None),
            "fiscal_year_end": _row_get(row, "fiscal_year_end", None),
            "monetary_exposure_inr": exposure,
            "monetary_exposure_inr_cr": _inr_to_cr(exposure),
            "severity": row["severity"],
            "anomaly_metrics": anomaly_metrics if isinstance(anomaly_metrics, dict) else {},
            "rule_triggered": row["rule_triggered"],
            "engine_version": str(_row_get(row, "engine_version", "") or ""),
            "ruleset_version": str(_row_get(row, "ruleset_version", "") or ""),
            "as_of_date": _row_get(row, "as_of_date", None),
            "source_locator": source_locator,
            "source_url": row["source_url"],
            "artifact_sha256": artifact_sha256,
            "secondary_sha256": secondary_sha256,
            "dataset_path": safe_ds_path,
            "provenance": provenance,
            "is_demo_sample": bool(row["is_demo_sample"]),
            "citations": citations if isinstance(citations, list) else [],
            "benign_explanations": benign if isinstance(benign, list) else [],
            "suggested_authority": row["suggested_authority"],
            "submitted_by": row["submitted_by"],
            "submitted_coding_agent": str(_row_get(row, "submitted_coding_agent", "") or ""),
            "submitted_coding_agent_model": str(_row_get(row, "submitted_coding_agent_model", "") or ""),
            "agent_trace": agent_trace if isinstance(agent_trace, dict) else {},
            "legal_lint_passed": bool(row["legal_lint_passed"]),
            "pii_scrubbed": bool(row["pii_scrubbed"]),
            "status": row["status"],
            "editorial_status": editorial_status,
            "quarantined": False,
            "quarantine_notice": None,
            "verification_score": _safe_finite_float(row["verification_score"], 0.0),
            "confirm_count": int(row["confirm_count"] or 0),
            "dispute_count": int(row["dispute_count"] or 0),
            "required_quorum": int(row["required_quorum"] or 2),
            "has_rti_draft": has_rti_draft,
            "correction_count": len(corr_list),
            "has_corrections": len(corr_list) > 0,
            "latest_correction": latest_corr,
            "created_at": row["created_at"],
            "verified_at": row["verified_at"],
        }

    @staticmethod
    def _serialize_verification(row: Any) -> dict[str, Any]:
        raw_checks = loads_json(row["checks_json"], {})
        checks = _normalize_checks(raw_checks, strict=False)
        repro, _ = _scrub_recursive(loads_json(row["reproduced_metrics_json"], {}))
        manifest, _ = _scrub_recursive(loads_json(_row_get(row, "manifest_json", "{}"), {}))
        manifest_dict = manifest if isinstance(manifest, dict) else {}
        raw_conf = row["confidence"]
        confidence: float | None = (
            _safe_finite_float(raw_conf, 0.0) if raw_conf is not None else None
        )
        return {
            "id": row["id"],
            "finding_id": row["finding_id"],
            "verifier_node_id": row["verifier_node_id"],
            "contributor_id": row["contributor_id"],
            "verifier_handle": row["verifier_handle"],
            "coding_agent": str(_row_get(row, "coding_agent", "") or ""),
            "coding_agent_model": str(_row_get(row, "coding_agent_model", "") or ""),
            "provider": row["provider"],
            "model_id": row["model_id"],
            "review_type": str(_row_get(row, "review_type", "computational_reproduction") or "computational_reproduction"),
            "verdict": row["verdict"],
            "checks": checks,
            "all_checks_passed": _all_checks_passed(checks),
            "reproduced_metrics": repro if isinstance(repro, dict) else {},
            "manifest": manifest_dict,
            "source_locator": manifest_dict.get("source_locator"),
            "engine_version": manifest_dict.get("engine_version"),
            "ruleset_version": manifest_dict.get("ruleset_version"),
            "as_of_date": manifest_dict.get("as_of_date"),
            "confidence": confidence,
            "notes": row["notes"],
            "created_at": row["created_at"],
        }

    @staticmethod
    def _serialize_rti(row: Any, *, is_preview: bool = False, persisted: bool = True) -> dict[str, Any]:
        fee_note = str(_row_get(row, "fee_note", "") or "")
        return {
            "id": row["id"],
            "finding_id": row["finding_id"],
            "version": int(_row_get(row, "version", 1) or 1),
            "public_authority": row["public_authority"],
            "cpio_designation": row["cpio_designation"],
            "rti_portal_url": row["rti_portal_url"] or "",
            "jurisdiction_level": str(_row_get(row, "jurisdiction_level", "central") or "central"),
            "state_name": _row_get(row, "state_name", None),
            "filing_channel": str(_row_get(row, "filing_channel", "central_online_portal") or "central_online_portal"),
            "fee_note": fee_note,
            "fee_rules": fee_note,
            "central_portal_warning": str(_row_get(row, "central_portal_warning", "") or ""),
            "human_filing_required": True,
            "statutory_sections": loads_json(row["statutory_sections_json"], []),
            "subject": row["subject"],
            "body_text": row["body_text"],
            "word_count": int(row["word_count"]),
            "requested_records": loads_json(row["requested_records_json"], []),
            "status": row["status"],
            "editorial_notice": str(_row_get(row, "editorial_notice", "") or ""),
            "is_preview": is_preview,
            "persisted": persisted,
            "filed_date": row["filed_date"],
            "due_date_30d": row["due_date_30d"],
            "created_at": row["created_at"],
            "updated_at": _row_get(row, "updated_at", None),
        }

    @staticmethod
    def _serialize_whisper(row: Any) -> dict[str, Any]:
        return {
            "id": row["id"],
            "agent_handle": row["agent_handle"],
            "provider": row["provider"],
            "model": row["model"],
            "mission_slug": row["mission_slug"],
            "finding_id": row["finding_id"],
            "event_type": row["event_type"],
            "message": row["message"],
            "created_at": row["created_at"],
        }

    @staticmethod
    def _serialize_sponsor(row: Any) -> dict[str, Any]:
        pledge_status = str(_row_get(row, "pledge_status", "pledged") or "pledged")
        return {
            "id": row["id"],
            "name": row["name"],
            "handle_or_org": row["handle_or_org"],
            "tier": row["tier"],
            "amount_inr": _safe_finite_float(row["amount_inr"], 0.0),
            "in_kind_value_inr": _safe_finite_float(_row_get(row, "in_kind_value_inr", 0.0), 0.0),
            "compute_credits_desc": row["compute_credits_desc"],
            "pledge_status": pledge_status,
            "settlement_status": pledge_status,
            "reconciled_receipt_ref": _row_get(row, "reconciled_receipt_ref", None),
            "url": row["url"],
            "message": row["message"],
            "featured": bool(row["featured"]),
            "is_demo_entry": bool(row["is_demo_entry"]),
            "created_at": row["created_at"],
        }

    @staticmethod
    def _serialize_ledger(row: Any) -> dict[str, Any]:
        amt = _safe_finite_float(row["amount_inr"], 0.0)
        direction = row["direction"]
        settlement_status = str(_row_get(row, "settlement_status", "pledged") or "pledged")
        reconciled_ref = _row_get(row, "reconciled_receipt_ref", None)
        is_cash_received = direction == "inflow" and settlement_status == "received" and bool(reconciled_ref)
        if direction == "outflow":
            signed = -amt
        elif is_cash_received:
            signed = amt
        else:
            signed = 0.0
        return {
            "id": row["id"],
            "entry_date": row["entry_date"],
            "entry_type": row["entry_type"],
            "direction": direction,
            "settlement_status": settlement_status,
            "category": row["category"],
            "counterparty": row["counterparty"],
            "description": row["description"],
            "amount_inr": amt,
            "signed_amount_inr": signed,
            "compute_credit_units": row["compute_credit_units"],
            "receipt_ref": row["receipt_ref"],
            "reconciled_receipt_ref": reconciled_ref,
            "reconciled_at": _row_get(row, "reconciled_at", None),
            "sponsor_id": _row_get(row, "sponsor_id", None),
            "is_demo_entry": bool(row["is_demo_entry"]),
            "created_at": row["created_at"],
        }

    # ------------------------------------------------------------------
    # Internal write helpers
    # ------------------------------------------------------------------

    def check_rate_limits(
        self,
        checks: list[tuple[str, int]],
        *,
        window_seconds: int = 60,
    ) -> None:
        """Atomically enforce one or more distributed DB-backed fixed-window rate limits."""
        if os.environ.get("AIWATCHDOGS_DISABLE_RATE_LIMIT", "").strip().lower() in {"1", "true", "yes"}:
            return
        if not checks:
            return
        now_sec = int(time.time())
        win = max(1, int(window_seconds))
        win_start = (now_sec // win) * win
        retry_after = max(1, (win_start + win) - now_sec)
        now_iso = utc_now_iso()

        should_prune = False
        now_mono = time.monotonic()
        if now_mono - self._last_rate_limit_prune >= 300.0:
            with self._rate_limit_prune_lock:
                if now_mono - self._last_rate_limit_prune >= 300.0:
                    self._last_rate_limit_prune = now_mono
                    should_prune = True

        exceeded = False
        with self.db.transaction() as conn:
            for raw_key, limit in checks:
                key = raw_key[:160]
                conn.execute(
                    """
                    INSERT INTO rate_limit_buckets (bucket_key, window_start, count, updated_at)
                    VALUES (?, ?, 1, ?)
                    ON CONFLICT(bucket_key) DO UPDATE SET
                        count = CASE
                            WHEN rate_limit_buckets.window_start = excluded.window_start
                            THEN rate_limit_buckets.count + 1
                            ELSE 1
                        END,
                        window_start = excluded.window_start,
                        updated_at = excluded.updated_at
                    """,
                    (key, win_start, now_iso),
                )
                row = conn.execute(
                    "SELECT count FROM rate_limit_buckets WHERE bucket_key = ?",
                    (key,),
                ).fetchone()
                current = int(row["count"]) if row is not None else 1
                if current > limit:
                    exceeded = True
            if should_prune:
                conn.execute(
                    "DELETE FROM rate_limit_buckets WHERE window_start < ?",
                    (now_sec - 3600,),
                )

        if exceeded:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded; please retry shortly.",
                headers={"Retry-After": str(retry_after)},
            )

    def check_rate_limit(
        self,
        bucket_key: str,
        *,
        limit: int,
        window_seconds: int = 60,
    ) -> None:
        """Distributed DB-backed fixed-window rate limiter safe across multi-instance Fluid Compute."""
        self.check_rate_limits([(bucket_key, limit)], window_seconds=window_seconds)

    def _redact_quarantined_finding_artifacts(self, conn: Any, finding_id: str, now: str) -> None:
        """Atomically redact tasks, whispers, and events linked to a quarantined finding (DPDP/BNS)."""
        conn.execute(
            """
            UPDATE tasks
            SET title = 'Withheld pending privacy/legal review',
                payload_json = '{}',
                output_json = NULL,
                updated_at = ?
            WHERE target_finding_id = ?
            """,
            (now, finding_id),
        )
        conn.execute("DELETE FROM whispers WHERE finding_id = ?", (finding_id,))
        tombstone = dumps_json({"finding_id": finding_id, "redacted": True, "reason": "quarantined"})
        conn.execute(
            "UPDATE events SET payload_json = ? WHERE finding_id = ?",
            (tombstone, finding_id),
        )

    def _emit_durable_event(
        self,
        conn: Any,
        envelopes: list[dict[str, Any]],
        event_type: str,
        payload: dict[str, Any],
        *,
        now: str | None = None,
        finding_id: str | None = None,
    ) -> dict[str, Any]:
        env = self.broker.persist_event(
            conn,
            event_type,
            payload,
            created_at=now,
            finding_id=finding_id,
        )
        envelopes.append(env)
        return env

    def _broadcast_envelopes(self, envelopes: list[dict[str, Any]]) -> None:
        for env in envelopes:
            self.broker.publish_envelope(env)

    def _record_score_event(
        self,
        conn: Any,
        *,
        agent_id: str | None,
        agent_handle: str,
        event_type: str,
        ref_id: str | None,
        now: str,
    ) -> int:
        points = int(SCORE_WEIGHTS[event_type])
        se_id = f"se-{secrets.token_hex(6)}"
        cur = conn.execute(
            """
            INSERT INTO score_events (id, agent_id, agent_handle, event_type, points, ref_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(agent_handle, event_type, ref_id) WHERE ref_id IS NOT NULL DO NOTHING
            """,
            (se_id, agent_id, agent_handle, event_type, points, ref_id, now),
        )
        if cur.rowcount == 1:
            conn.execute(
                "UPDATE agents SET compute_score = compute_score + ? WHERE handle = ?",
                (points, agent_handle),
            )
            return points
        return 0

    def _fetch_single_agent_scores(self, conn: Any, agent_handle: str) -> tuple[int, int]:
        cutoff = (
            (datetime.now(timezone.utc) - timedelta(hours=1))
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        all_row = conn.execute(
            "SELECT COALESCE(SUM(points), 0) AS pts FROM score_events WHERE agent_handle = ?",
            (agent_handle,),
        ).fetchone()
        hour_row = conn.execute(
            "SELECT COALESCE(SUM(points), 0) AS pts FROM score_events WHERE agent_handle = ? AND created_at >= ?",
            (agent_handle, cutoff),
        ).fetchone()
        return (
            int(all_row["pts"] or 0) if all_row else 0,
            int(hour_row["pts"] or 0) if hour_row else 0,
        )

    def _fetch_score_maps(self, conn: Any) -> tuple[dict[str, int], dict[str, int]]:
        cutoff = (
            (datetime.now(timezone.utc) - timedelta(hours=1))
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        all_time: dict[str, int] = {
            str(r["agent_handle"]): int(r["pts"] or 0)
            for r in conn.execute(
                "SELECT agent_handle, COALESCE(SUM(points), 0) AS pts FROM score_events GROUP BY agent_handle"
            ).fetchall()
        }
        hourly: dict[str, int] = {
            str(r["agent_handle"]): int(r["pts"] or 0)
            for r in conn.execute(
                "SELECT agent_handle, COALESCE(SUM(points), 0) AS pts FROM score_events "
                "WHERE created_at >= ? GROUP BY agent_handle",
                (cutoff,),
            ).fetchall()
        }
        return all_time, hourly

    def _fetch_corrections_map(
        self, conn: Any, finding_ids: list[str] | None = None
    ) -> dict[str, list[dict[str, Any]]]:
        if finding_ids is not None:
            if not finding_ids:
                return {}
            placeholders = ",".join("?" for _ in finding_ids)
            rows = conn.execute(
                f"SELECT * FROM finding_corrections WHERE finding_id IN ({placeholders}) "
                "ORDER BY created_at ASC, id ASC",
                finding_ids,
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM finding_corrections ORDER BY created_at ASC, id ASC"
            ).fetchall()
        out: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            out.setdefault(str(r["finding_id"]), []).append(self._serialize_correction(r))
        return out

    def _insert_whisper(
        self,
        conn: Any,
        *,
        agent_handle: str,
        provider: str,
        model: str,
        event_type: str,
        message: str,
        mission_slug: str | None = None,
        finding_id: str | None = None,
    ) -> dict[str, Any]:
        cleaned_msg, _ = scrub_pii(message)
        wid = f"wsp-{secrets.token_hex(6)}"
        created = utc_now_iso()
        conn.execute(
            """
            INSERT INTO whispers (
                id, agent_handle, provider, model, mission_slug,
                finding_id, event_type, message, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (wid, agent_handle, provider, model, mission_slug, finding_id, event_type, cleaned_msg, created),
        )
        return {
            "id": wid,
            "agent_handle": agent_handle,
            "provider": provider,
            "model": model,
            "mission_slug": mission_slug,
            "finding_id": finding_id,
            "event_type": event_type,
            "message": cleaned_msg,
            "created_at": created,
        }

    @staticmethod
    def _is_orphaned_w1_task(task_row: Any) -> bool:
        """Return True if a W1 task references a batch CSV manifest that is no longer deployed."""
        if str(_row_get(task_row, "methodology_code", "") or "").strip() != "W1":
            return False
        payload = loads_json(_row_get(task_row, "payload_json", "{}"), {})
        if not isinstance(payload, dict):
            return False
        retrieval_url = str(payload.get("retrieval_url") or "").strip()
        if not retrieval_url:
            return False
        from urllib.parse import urlsplit

        filename = urlsplit(retrieval_url).path.rsplit("/", 1)[-1]
        if not re.fullmatch(r"batch-[0-9]{3,4}\.csv", filename):
            return False
        from aiwatchdogs.projects import default_projects_dir

        return not (default_projects_dir() / "gov-in-public-information" / filename).is_file()

    @staticmethod
    def _retire_orphaned_task(conn: Any, task_id: str, mission_id: str, now: str) -> None:
        has_finding = conn.execute("SELECT 1 FROM findings WHERE task_id = ? LIMIT 1", (task_id,)).fetchone()
        if has_finding is not None:
            conn.execute(
                """
                UPDATE tasks
                SET status = 'retired', claimed_by_agent_id = NULL, claimed_by_handle = NULL,
                    lease_id = NULL, claim_request_id = NULL, lease_started_at = NULL, lease_expires_at = NULL,
                    available_at = NULL, updated_at = ?
                WHERE id = ? AND status != 'completed'
                """,
                (now, task_id),
            )
        else:
            conn.execute("DELETE FROM tasks WHERE id = ? AND status != 'completed'", (task_id,))
        cnt = conn.execute(
            "SELECT COUNT(*) AS c FROM tasks WHERE mission_id = ? "
            "AND task_type != 'peer_verify' AND status != 'retired'",
            (mission_id,),
        ).fetchone()
        if cnt is not None:
            conn.execute("UPDATE missions SET total_tasks = ? WHERE id = ?", (int(cnt["c"]), mission_id))

    def _reclaim_expired_leases(self, conn: Any) -> int:
        now = utc_now_iso()
        lock_clause = " FOR UPDATE SKIP LOCKED" if self.db.is_postgres else ""
        expired = conn.execute(
            f"SELECT id, mission_id, task_type, methodology_code, payload_json, lease_id, "
            f"claimed_by_agent_id, retry_count, max_retries FROM tasks "
            f"WHERE status = 'claimed' AND lease_expires_at IS NOT NULL AND lease_expires_at < ? "
            f"ORDER BY lease_expires_at ASC LIMIT 50{lock_clause}",
            (now,),
        ).fetchall()
        base_delay = int(os.environ.get("AIWATCHDOGS_RETRY_BASE_SECONDS", str(DEFAULT_RETRY_BASE_SECONDS)))
        for row in expired:
            if self._is_orphaned_w1_task(row):
                if row["claimed_by_agent_id"]:
                    conn.execute(
                        "UPDATE agents SET current_task_id = NULL WHERE id = ? AND current_task_id = ?",
                        (row["claimed_by_agent_id"], row["id"]),
                    )
                self._retire_orphaned_task(conn, str(row["id"]), str(row["mission_id"]), now)
                continue
            next_retry = int(_row_get(row, "retry_count", 0) or 0) + 1
            max_retries = int(_row_get(row, "max_retries", DEFAULT_MAX_RETRIES) or DEFAULT_MAX_RETRIES)
            if next_retry >= max_retries:
                next_status = "needs_adjudication"
                avail_at: str | None = None
            else:
                next_status = "queued"
                delay = min(900, max(0, base_delay) * (2 ** max(0, next_retry - 1)))
                avail_at = _future_iso_seconds(delay) if delay > 0 else now
            conn.execute(
                """
                UPDATE tasks
                SET status = ?, claimed_by_agent_id = NULL, claimed_by_handle = NULL,
                    lease_id = NULL, claim_request_id = NULL, lease_started_at = NULL, lease_expires_at = NULL, progress_pct = 0,
                    retry_count = ?, available_at = ?, updated_at = ?
                WHERE id = ? AND status = 'claimed'
                """,
                (next_status, next_retry, avail_at, now, row["id"]),
            )
            if row["claimed_by_agent_id"]:
                conn.execute(
                    "UPDATE agents SET current_task_id = NULL WHERE id = ? AND current_task_id = ?",
                    (row["claimed_by_agent_id"], row["id"]),
                )
        self._requeue_parked_tasks(conn, now, is_postgres=self.db.is_postgres)
        return len(expired)

    @staticmethod
    def _requeue_parked_tasks(conn: Any, now: str, *, is_postgres: bool = False) -> None:
        """Put tasks parked after repeated failed attempts back on the queue once they have cooled down.

        Three releases or lapsed leases park a task. On a volunteer swarm those are usually a node's
        own trouble (a blocked network, a killed session), not the task's, so parking is temporary.
        Peer checks keep their exclusion list; nobody can come back to verify their own lead.
        """
        try:
            minutes = int(os.environ.get("AIWATCHDOGS_PARKED_REQUEUE_MINUTES", str(DEFAULT_PARKED_REQUEUE_MINUTES)))
        except ValueError:
            minutes = DEFAULT_PARKED_REQUEUE_MINUTES
        if minutes <= 0:
            return
        now_dt = _parse_iso(now)
        if now_dt is None:
            return
        cutoff = (now_dt - timedelta(minutes=minutes)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        lock_clause = " FOR UPDATE SKIP LOCKED" if is_postgres else ""
        parked = conn.execute(
            f"""
            SELECT id FROM tasks
            WHERE status = 'needs_adjudication' AND updated_at < ?
              AND task_type IN ('anomaly_scan', 'ingest_parse', 'peer_verify')
              AND (target_finding_id IS NULL OR target_finding_id IN (
                  SELECT id FROM findings WHERE status = 'peer_review' AND COALESCE(quarantined, 0) = 0
              ))
            LIMIT 50{lock_clause}
            """,
            (cutoff,),
        ).fetchall()
        if not parked:
            return
        ids = [str(r["id"]) for r in parked]
        ph = ",".join("?" for _ in ids)
        conn.execute(
            f"UPDATE tasks SET status = 'queued', retry_count = 0, available_at = NULL, updated_at = ? "
            f"WHERE id IN ({ph}) AND status = 'needs_adjudication'",
            [now, *ids],
        )

    def _authenticate_agent(
        self,
        conn: Any,
        token: str | None,
        required: bool = True,
    ) -> Any | None:
        if not token or not token.strip():
            if required:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Missing X-Agent-Token header",
                )
            return None
        cleaned = token.strip()
        primary_hash = compute_token_digest(cleaned)
        agent = conn.execute("SELECT * FROM agents WHERE api_token_hash = ?", (primary_hash,)).fetchone()
        if agent is None and primary_hash.startswith("hmac:"):
            # Backward-compatible fallback for pre-pepper legacy SHA-256 token digests
            legacy_hash = compute_sha256(cleaned)
            agent = conn.execute("SELECT * FROM agents WHERE api_token_hash = ?", (legacy_hash,)).fetchone()
        if agent is None or not verify_token_digest(cleaned, str(agent["api_token_hash"] or "")):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid X-Agent-Token",
            )
        if _row_get(agent, "revoked_at", None) or str(_row_get(agent, "status", "")) == "revoked":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Agent token has been revoked",
            )
        return agent

    def rotate_agent_token(self, token: str | None) -> dict[str, Any]:
        """Rotate an authenticated agent's bearer token atomically."""
        now = utc_now_iso()
        with self.db.transaction() as conn:
            agent = self._authenticate_agent(conn, token, required=True)
            assert agent is not None
            new_token = f"awd_{secrets.token_urlsafe(24)}"
            new_hash = compute_token_digest(new_token)
            conn.execute(
                "UPDATE agents SET api_token_hash = ?, token_rotated_at = ?, registration_key_hash = NULL, last_heartbeat_at = ? WHERE id = ?",
                (new_hash, now, now, agent["id"]),
            )
            return {
                "agent_id": agent["id"],
                "handle": agent["handle"],
                "api_token": new_token,
                "rotated_at": now,
                "status": "rotated",
            }

    def revoke_agent_token(self, token: str | None) -> dict[str, Any]:
        """Revoke an authenticated agent's bearer token immediately."""
        now = utc_now_iso()
        with self.db.transaction() as conn:
            agent = self._authenticate_agent(conn, token, required=True)
            assert agent is not None
            conn.execute(
                "UPDATE agents SET revoked_at = ?, status = 'revoked', current_task_id = NULL, registration_key_hash = NULL WHERE id = ?",
                (now, agent["id"]),
            )
            return {
                "agent_id": agent["id"],
                "handle": agent["handle"],
                "revoked_at": now,
                "status": "revoked",
            }

    def _resolve_source_id(
        self,
        conn: Any,
        *,
        requested_source_id: str | None,
        source_landing_page: str | None,
        source_url: str | None,
        dataset_url: str | None,
    ) -> str | None:
        """Resolve an accurate public_sources.id without blindly picking mission.source_ids[0]."""
        if requested_source_id:
            row = conn.execute(
                "SELECT id FROM public_sources WHERE id = ?", (str(requested_source_id).strip(),)
            ).fetchone()
            if row is not None:
                return str(row["id"])

        candidate_domains = {
            d
            for d in (_domain_of(source_landing_page), _domain_of(source_url), _domain_of(dataset_url))
            if d
        }
        if not candidate_domains:
            return None

        src_rows = conn.execute(
            "SELECT id, base_url, source_landing_page, retrieval_url FROM public_sources"
        ).fetchall()
        for r in src_rows:
            src_domains = {
                d
                for d in (
                    _domain_of(r["base_url"]),
                    _domain_of(_row_get(r, "source_landing_page", "")),
                    _domain_of(_row_get(r, "retrieval_url", "")),
                )
                if d
            }
            if candidate_domains & src_domains:
                return str(r["id"])
        return None

    def _compute_coverage_summary(
        self,
        conn: Any,
        mission_rows: list[Any],
    ) -> tuple[dict[str, float], dict[str, Any]]:
        """Compute method scan workload separately from deduplicated coverage by financial unit.

        Never double-counts shared files across missions (e.g. M1 + M2 Assam OCDS CSV) and
        withholds cross-unit aggregate totals when incomparable financial quantities exist.
        """
        mission_by_id = {str(r["id"]): r for r in mission_rows}

        # Map each mission -> {dataset_key: max_inr} from completed/settled anomaly_scan tasks
        mission_datasets: dict[str, dict[str, float]] = {str(r["id"]): {} for r in mission_rows}
        # Map each quantity_type -> {dataset_key: max_inr}, workload_inr, missions
        qty_datasets: dict[str, dict[str, float]] = {}
        qty_workload: dict[str, float] = {}
        qty_missions: dict[str, set[str]] = {}

        mission_ids = [str(r["id"]) for r in mission_rows]
        if mission_ids:
            ph = ",".join("?" for _ in mission_ids)
            task_rows = conn.execute(
                f"""
                SELECT id, mission_id, mission_slug, task_type, dataset_key,
                       financial_quantity_type, monetary_audited_inr,
                       CASE WHEN COALESCE(dataset_key, '') = '' THEN payload_json ELSE '' END AS payload_json
                FROM tasks
                WHERE mission_id IN ({ph})
                  AND status IN ('completed', 'settled')
                  AND task_type IN ('anomaly_scan', 'ingest_parse')
                  AND COALESCE(monetary_audited_inr, 0) > 0
                """,
                mission_ids,
            ).fetchall()
        else:
            task_rows = []

        missions_with_task_amounts: set[str] = set()
        for t in task_rows:
            mid = str(t["mission_id"])
            m_row = mission_by_id.get(mid)
            if m_row is None:
                continue
            amt = float(t["monetary_audited_inr"] or 0.0)
            if amt <= 0:
                continue
            missions_with_task_amounts.add(mid)
            raw_dkey = str(_row_get(t, "dataset_key", "") or "").strip()
            if raw_dkey:
                dkey = raw_dkey
            else:
                payload = loads_json(t["payload_json"], {})
                dkey = str(
                    (payload.get("dataset_key") if isinstance(payload, dict) else "")
                    or (payload.get("dataset_url") if isinstance(payload, dict) else "")
                    or t["id"]
                ).strip()
            qtype = str(
                _row_get(t, "financial_quantity_type", "")
                or _row_get(m_row, "financial_quantity_type", "")
                or "unspecified"
            ).strip()

            prev_m = mission_datasets[mid].get(dkey, 0.0)
            if amt > prev_m:
                mission_datasets[mid][dkey] = amt

            qmap = qty_datasets.setdefault(qtype, {})
            if amt > qmap.get(dkey, 0.0):
                qmap[dkey] = amt
            qty_workload[qtype] = qty_workload.get(qtype, 0.0) + amt
            qty_missions.setdefault(qtype, set()).add(str(m_row["slug"]))

        # Account for upgraded legacy missions that have monetary_audited_inr > 0 without task-level rows
        for m_row in mission_rows:
            mid = str(m_row["id"])
            m_amt = float(m_row["monetary_audited_inr"] or 0.0)
            if m_amt <= 0 or mid in missions_with_task_amounts:
                continue
            qtype = str(_row_get(m_row, "financial_quantity_type", "") or "unspecified").strip()
            # Deduplicate known overlapping Assam missions even on legacy upgraded rows
            if m_row["slug"] in ("assam-ocds-single-bid", "assam-gfr-split-tendering"):
                dkey = "assam-ocds-v2-fy2016-22"
            elif m_row["slug"] in ("ocds-vendor-concentration", "ocds-cost-escalation-watch"):
                dkey = "assam-ocds-full-fy2016-23"
            else:
                dkey = f"mission:{m_row['slug']}"
            mission_datasets[mid][dkey] = max(mission_datasets[mid].get(dkey, 0.0), m_amt)
            qmap = qty_datasets.setdefault(qtype, {})
            qmap[dkey] = max(qmap.get(dkey, 0.0), m_amt)
            qty_workload[qtype] = qty_workload.get(qtype, 0.0) + m_amt
            qty_missions.setdefault(qtype, set()).add(str(m_row["slug"]))

        mission_unique_inr: dict[str, float] = {
            mid: round(sum(dmap.values()), 2) for mid, dmap in mission_datasets.items()
        }
        total_scan_workload_inr = sum(float(r["monetary_audited_inr"] or 0.0) for r in mission_rows)

        breakdown: list[dict[str, Any]] = []
        for qtype in sorted(qty_datasets.keys()):
            dmap = qty_datasets[qtype]
            dedup_inr = round(sum(dmap.values()), 2)
            if dedup_inr <= 0:
                continue
            breakdown.append(
                {
                    "quantity_type": qtype,
                    "label": FINANCIAL_QUANTITY_LABELS.get(qtype, qtype),
                    "unique_datasets": len(dmap),
                    "deduplicated_inr": dedup_inr,
                    "deduplicated_inr_cr": _inr_to_cr(dedup_inr),
                    "scan_workload_inr_cr": _inr_to_cr(qty_workload.get(qtype, dedup_inr)),
                    "missions": sorted(qty_missions.get(qtype, set())),
                }
            )

        if len(breakdown) == 0:
            unique_coverage_inr_cr: float | None = 0.0
            withheld = False
            reason: str | None = None
        elif len(breakdown) == 1:
            unique_coverage_inr_cr = float(breakdown[0]["deduplicated_inr_cr"])
            withheld = False
            reason = None
        else:
            unique_coverage_inr_cr = None
            withheld = True
            reason = (
                "Aggregate unique public spend is withheld because completed scans span incomparable "
                "financial quantities (e.g. tender estimates, awarded contracts, budget/treasury "
                "expenditure, CAG report figures, scheme outlays, and electoral bond disclosures). "
                "Inspect coverage_by_quantity_type for deduplicated per-unit totals."
            )

        summary = {
            "scan_workload_inr_cr": _inr_to_cr(total_scan_workload_inr),
            "unique_coverage_inr_cr": unique_coverage_inr_cr,
            "unique_coverage_withheld": withheld,
            "unique_coverage_reason": reason,
            "coverage_by_quantity_type": breakdown,
        }
        return mission_unique_inr, summary

    def _compute_treasury_summary(self, entries: list[dict[str, Any]]) -> dict[str, Any]:
        pledged_inr = round(
            sum(
                e["amount_inr"]
                for e in entries
                if e["direction"] == "inflow"
                and e["settlement_status"] == "pledged"
                and e["entry_type"] != "compute_grant"
            ),
            2,
        )
        received_cash_inr = round(
            sum(
                e["amount_inr"]
                for e in entries
                if e["direction"] == "inflow"
                and e["settlement_status"] == "received"
                and bool(e.get("reconciled_receipt_ref"))
            ),
            2,
        )
        in_kind_compute_entries = [
            e
            for e in entries
            if e["entry_type"] == "compute_grant"
            or e["settlement_status"] in ("in_kind_committed", "in_kind_fulfilled")
        ]
        in_kind_compute_inr = round(sum(e["amount_inr"] for e in in_kind_compute_entries), 2)
        total_outflow_inr = round(
            sum(e["amount_inr"] for e in entries if e["direction"] == "outflow"),
            2,
        )
        net_cash_balance_inr = round(received_cash_inr - total_outflow_inr, 2)
        return {
            "pledged_inr": pledged_inr,
            "total_pledged_inr": pledged_inr,
            "received_cash_inr": received_cash_inr,
            "in_kind_compute_inr": in_kind_compute_inr,
            "in_kind_compute_entries": len(in_kind_compute_entries),
            "total_inflow_inr": received_cash_inr,
            "total_outflow_inr": total_outflow_inr,
            "net_cash_balance_inr": net_cash_balance_inr,
            "net_balance_inr": net_cash_balance_inr,
            "balance_inr": net_cash_balance_inr,
            "compute_grants_inr": in_kind_compute_inr,
            "entry_count": len(entries),
        }

    def _fetch_treasury_summary_sql(self, conn: Any) -> dict[str, Any]:
        """Compute treasury totals directly via SQL aggregates (avoids unbounded table scans)."""
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS entry_count,
                COALESCE(SUM(CASE
                    WHEN direction = 'inflow' AND settlement_status = 'pledged' AND entry_type != 'compute_grant'
                    THEN amount_inr ELSE 0.0 END), 0.0) AS pledged_inr,
                COALESCE(SUM(CASE
                    WHEN direction = 'inflow' AND settlement_status = 'received'
                         AND reconciled_receipt_ref IS NOT NULL AND reconciled_receipt_ref != ''
                    THEN amount_inr ELSE 0.0 END), 0.0) AS received_cash_inr,
                COALESCE(SUM(CASE
                    WHEN entry_type = 'compute_grant' OR settlement_status IN ('in_kind_committed', 'in_kind_fulfilled')
                    THEN amount_inr ELSE 0.0 END), 0.0) AS in_kind_compute_inr,
                COALESCE(SUM(CASE
                    WHEN entry_type = 'compute_grant' OR settlement_status IN ('in_kind_committed', 'in_kind_fulfilled')
                    THEN 1 ELSE 0 END), 0) AS in_kind_compute_entries,
                COALESCE(SUM(CASE WHEN direction = 'outflow' THEN amount_inr ELSE 0.0 END), 0.0) AS total_outflow_inr
            FROM treasury_ledger
            """
        ).fetchone()
        pledged_inr = round(_safe_finite_float(row["pledged_inr"], 0.0), 2) if row else 0.0
        received_cash_inr = round(_safe_finite_float(row["received_cash_inr"], 0.0), 2) if row else 0.0
        in_kind_compute_inr = round(_safe_finite_float(row["in_kind_compute_inr"], 0.0), 2) if row else 0.0
        in_kind_entries = int(row["in_kind_compute_entries"] or 0) if row else 0
        total_outflow_inr = round(_safe_finite_float(row["total_outflow_inr"], 0.0), 2) if row else 0.0
        net_cash_balance_inr = round(received_cash_inr - total_outflow_inr, 2)
        entry_count = int(row["entry_count"] or 0) if row else 0
        return {
            "pledged_inr": pledged_inr,
            "total_pledged_inr": pledged_inr,
            "received_cash_inr": received_cash_inr,
            "in_kind_compute_inr": in_kind_compute_inr,
            "in_kind_compute_entries": in_kind_entries,
            "total_inflow_inr": received_cash_inr,
            "total_outflow_inr": total_outflow_inr,
            "net_cash_balance_inr": net_cash_balance_inr,
            "net_balance_inr": net_cash_balance_inr,
            "balance_inr": net_cash_balance_inr,
            "compute_grants_inr": in_kind_compute_inr,
            "entry_count": entry_count,
        }

    # ------------------------------------------------------------------
    # Public Read Plane
    # ------------------------------------------------------------------

    def get_head(self) -> dict[str, Any]:
        generated_at = utc_now_iso()
        with self.db.read() as conn:
            missions, coverage_summary = self._fetch_missions_with_coverage(conn)
            rtis_drafted = int(
                conn.execute(
                    "SELECT COUNT(*) AS c FROM rti_drafts WHERE status NOT IN ('quarantined')"
                ).fetchone()["c"]
            )

            min_agent_row = conn.execute("SELECT MIN(joined_at) AS min_ts FROM agents").fetchone()
            min_mission_row = conn.execute("SELECT MIN(created_at) AS min_ts FROM missions").fetchone()
            started_candidates: list[tuple[datetime, str]] = []
            for ts_val in (
                min_agent_row["min_ts"] if min_agent_row else None,
                min_mission_row["min_ts"] if min_mission_row else None,
            ):
                parsed = _parse_iso(ts_val)
                if parsed is not None and ts_val:
                    started_candidates.append((parsed, ts_val))
            started_at = min(started_candidates, key=lambda item: item[0])[1] if started_candidates else generated_at

            all_time_scores, hourly_scores = self._fetch_score_maps(conn)
            total_agents_count = int(conn.execute("SELECT COUNT(*) AS c FROM agents").fetchone()["c"])
            all_agents = conn.execute(
                "SELECT * FROM agents ORDER BY compute_score DESC, joined_at DESC LIMIT 200"
            ).fetchall()
            serialized_agents = [
                self._serialize_agent(
                    r,
                    all_time_score=all_time_scores.get(r["handle"], int(r["compute_score"] or 0)),
                    hourly_event_score=hourly_scores.get(r["handle"], 0),
                )
                for r in all_agents
            ]
            serialized_agents.sort(key=lambda a: (a["compute_score"], a["tasks_completed"]), reverse=True)
            active_agents = [a for a in serialized_agents if a["is_active"]]

            just_joined_rows = conn.execute(
                "SELECT handle, display_name, provider, model, joined_at FROM agents ORDER BY joined_at DESC LIMIT 6"
            ).fetchall()
            just_joined = [
                {
                    "handle": r["handle"],
                    "display_name": r["display_name"],
                    "provider": r["provider"],
                    "model": r["model"],
                    "joined_at": r["joined_at"],
                }
                for r in just_joined_rows
            ]

            just_in_row = conn.execute(
                """
                SELECT id, title, methodology_code, entity_name, monetary_exposure_inr,
                       verified_at, created_at, is_demo_sample
                FROM findings
                WHERE status IN ('verified', 'rti_filed') AND COALESCE(quarantined, 0) = 0
                ORDER BY COALESCE(verified_at, created_at) DESC, id ASC
                LIMIT 1
                """
            ).fetchone()
            just_in: dict[str, Any] | None = (
                {
                    "id": just_in_row["id"],
                    "title": just_in_row["title"],
                    "methodology_code": just_in_row["methodology_code"],
                    "entity_name": just_in_row["entity_name"],
                    "monetary_exposure_inr_cr": _inr_to_cr(just_in_row["monetary_exposure_inr"]),
                    "verified_at": just_in_row["verified_at"] or just_in_row["created_at"],
                    "is_demo_sample": bool(just_in_row["is_demo_sample"]),
                }
                if just_in_row is not None
                else None
            )

            recent_finding_rows = conn.execute(
                """
                SELECT * FROM findings
                WHERE COALESCE(quarantined, 0) = 0 AND status != 'quarantined'
                ORDER BY
                    CASE status
                        WHEN 'verified' THEN 0
                        WHEN 'peer_review' THEN 1
                        WHEN 'cleared' THEN 2
                        WHEN 'under_review' THEN 3
                        ELSE 4
                    END,
                    COALESCE(verified_at, created_at) DESC
                LIMIT 12
                """
            ).fetchall()
            recent_fids = [str(r["id"]) for r in recent_finding_rows]
            rti_finding_ids: set[str] = set()
            if recent_fids:
                ph = ",".join("?" for _ in recent_fids)
                rti_finding_ids = {
                    str(r["finding_id"])
                    for r in conn.execute(
                        f"SELECT finding_id FROM rti_drafts WHERE status != 'quarantined' AND finding_id IN ({ph})",
                        recent_fids,
                    ).fetchall()
                }
            corr_map = self._fetch_corrections_map(conn, recent_fids)
            recent_findings = [
                self._serialize_finding(
                    r,
                    has_rti_draft=(r["id"] in rti_finding_ids),
                    corrections=corr_map.get(str(r["id"]), []),
                )
                for r in recent_finding_rows
            ]

            recent_verif_rows = conn.execute(
                """
                SELECT pv.*, f.title AS finding_title, f.methodology_code, f.status AS finding_status
                FROM peer_verifications pv
                JOIN findings f ON f.id = pv.finding_id
                WHERE COALESCE(f.quarantined, 0) = 0 AND f.status != 'quarantined'
                ORDER BY pv.created_at DESC, pv.id ASC
                LIMIT 6
                """
            ).fetchall()
            recent_verifications = [
                {
                    **self._serialize_verification(r),
                    "model": r["model_id"],
                    "finding_title": r["finding_title"],
                    "methodology_code": r["methodology_code"],
                    "finding_status": r["finding_status"],
                }
                for r in recent_verif_rows
            ]

            whisper_rows = conn.execute(
                """
                SELECT w.* FROM whispers w
                WHERE w.finding_id IS NULL
                   OR NOT EXISTS (
                       SELECT 1 FROM findings f
                       WHERE f.id = w.finding_id
                         AND (COALESCE(f.quarantined, 0) = 1 OR f.status = 'quarantined')
                   )
                ORDER BY w.created_at DESC
                LIMIT 25
                """
            ).fetchall()
            whispers = [self._serialize_whisper(r) for r in whisper_rows]

            sponsor_rows = conn.execute(
                "SELECT * FROM sponsors WHERE featured = 1 ORDER BY amount_inr DESC LIMIT 6"
            ).fetchall()
            sponsors_featured = [self._serialize_sponsor(r) for r in sponsor_rows]

            recent_sponsor_rows = conn.execute(
                "SELECT * FROM sponsors ORDER BY created_at DESC, id ASC LIMIT 12"
            ).fetchall()
            sponsors_recent = [
                {
                    **self._serialize_sponsor(r),
                    "compute_grant": r["compute_credits_desc"],
                    "note": r["message"],
                    "website_url": r["url"],
                    "is_demo": bool(r["is_demo_entry"]),
                }
                for r in recent_sponsor_rows
            ]

            treasury_summary = self._fetch_treasury_summary_sql(conn)

            record_events = self._build_record_events(conn, limit=20)

            tasks_completed = int(
                conn.execute(
                    "SELECT COUNT(*) AS c FROM tasks WHERE status IN ('completed', 'settled')"
                ).fetchone()["c"]
            )
            # Only work an agent can actually claim: queued scans on live projects + pending peer checks.
            tasks_queued = int(
                conn.execute(
                    "SELECT COUNT(*) AS c FROM tasks WHERE status = 'queued' AND ("
                    "task_type = 'peer_verify' OR mission_id IN "
                    "(SELECT id FROM missions WHERE status NOT IN ('archived', 'paused', 'needs_data')))"
                ).fetchone()["c"]
            )
            status_counts = {
                str(r["status"]): int(r["c"])
                for r in conn.execute("SELECT status, COUNT(*) AS c FROM findings GROUP BY status").fetchall()
            }
            quarantined_cnt = int(
                conn.execute(
                    "SELECT COUNT(*) AS c FROM findings WHERE COALESCE(quarantined, 0) = 1 OR status = 'quarantined'"
                ).fetchone()["c"]
            )
            verified_cnt = status_counts.get("verified", 0) + status_counts.get("rti_filed", 0)
            peer_review_cnt = status_counts.get("peer_review", 0) + status_counts.get("raw_lead", 0)
            cleared_findings_cnt = status_counts.get("cleared", 0) + status_counts.get("resolved_explained", 0)
            disputed_cnt = status_counts.get("disputed", 0)
            under_review_cnt = status_counts.get("under_review", 0)
            retracted_cnt = status_counts.get("retracted", 0) + status_counts.get("retracted_false_positive", 0)
            cleared_scans_cnt = sum(int(m["cleared_scans_count"]) for m in missions)
            unevaluable_groups_cnt = sum(int(m["unevaluable_count"]) for m in missions)
            latest_event_row = conn.execute("SELECT COALESCE(MAX(id), 0) AS max_id FROM events").fetchone()
            latest_event_id = int(latest_event_row["max_id"]) if latest_event_row else 0

        n_projects = len(missions)
        projects_label = "project" if n_projects == 1 else "projects"
        project_names = ", ".join(str(m["title"]) for m in missions[:3])
        if verified_cnt == 0 and peer_review_cnt == 0:
            orchestrator_brief = (
                f"{tasks_queued} official datasets are queued across {n_projects} "
                f"{projects_label}" + (f" ({project_names})" if project_names else "") + ". "
                f"{len(active_agents)} contributor agents are active. Every lead needs SHA-256 provenance, "
                "neutral wording and two independent agents agreeing."
            )
        else:
            orchestrator_brief = (
                f"Swarm focus: {verified_cnt} verified leads, {peer_review_cnt} awaiting peer verification, "
                f"{cleared_findings_cnt} cleared findings, and {tasks_queued} queued tasks across "
                f"{n_projects} {projects_label} ({len(active_agents)} active contributor agents)."
            )

        return {
            "generated_at": generated_at,
            "started_at": started_at,
            "orchestrator_brief": orchestrator_brief,
            "independence_pledge": INDEPENDENCE_PLEDGE,
            "score_weights": dict(SCORE_WEIGHTS),
            "latest_event_id": latest_event_id,
            "stats": {
                "public_spend_audited_inr_cr": coverage_summary["unique_coverage_inr_cr"],
                "scan_workload_inr_cr": coverage_summary["scan_workload_inr_cr"],
                "unique_coverage_inr_cr": coverage_summary["unique_coverage_inr_cr"],
                "unique_coverage_withheld": coverage_summary["unique_coverage_withheld"],
                "unique_coverage_reason": coverage_summary["unique_coverage_reason"],
                "coverage_by_quantity_type": coverage_summary["coverage_by_quantity_type"],
                "tasks_completed": tasks_completed,
                "tasks_queued": tasks_queued,
                "agents_active": len(active_agents),
                "agents_total": total_agents_count,
                "findings_verified": verified_cnt,
                "findings_peer_review": peer_review_cnt,
                "findings_cleared": cleared_findings_cnt,
                "cleared_scans": cleared_scans_cnt,
                "unevaluable_groups": unevaluable_groups_cnt,
                "findings_disputed": disputed_cnt,
                "findings_under_review": under_review_cnt,
                "findings_retracted": retracted_cnt,
                "findings_quarantined": quarantined_cnt,
                "rtis_drafted": rtis_drafted,
                "treasury_pledged_inr": treasury_summary["pledged_inr"],
                "treasury_received_cash_inr": treasury_summary["received_cash_inr"],
                "treasury_in_kind_compute_inr": treasury_summary["in_kind_compute_inr"],
                "sponsors_total_inr": treasury_summary["received_cash_inr"],
            },
            "disposition": {
                "verified_leads": verified_cnt,
                "peer_review": peer_review_cnt,
                "cleared_records": cleared_findings_cnt,
                "cleared_scans": cleared_scans_cnt,
                "unevaluable_groups": unevaluable_groups_cnt,
                "disputed": disputed_cnt,
                "under_review": under_review_cnt,
                "retracted": retracted_cnt,
                "quarantined": quarantined_cnt,
            },
            "just_in": just_in,
            "just_joined": just_joined,
            "active_agents": active_agents,
            "missions": missions,
            "recent_findings": recent_findings,
            "recent_verifications": recent_verifications,
            "whispers": whispers,
            "record_events": record_events,
            "top_contributors": serialized_agents[:10],
            "sponsors_featured": sponsors_featured,
            "sponsors_recent": sponsors_recent,
        }

    @staticmethod
    def _build_record_events(conn: Any, limit: int = 20) -> list[dict[str, Any]]:
        events: list[tuple[datetime, int, str, dict[str, Any]]] = []
        epoch_min = datetime.min.replace(tzinfo=timezone.utc)

        def push(ts: str | None, tie_rank: int, payload: dict[str, Any]) -> None:
            parsed = _parse_iso(ts) or epoch_min
            events.append((parsed, tie_rank, payload["id"], payload))

        finding_rows = conn.execute(
            """
            SELECT id, title, status, methodology_code, submitted_by,
                   monetary_exposure_inr, is_demo_sample, created_at, verified_at
            FROM findings
            WHERE COALESCE(quarantined, 0) = 0 AND status != 'quarantined'
            ORDER BY COALESCE(verified_at, created_at) DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        for r in finding_rows:
            st = str(r["status"] or "")
            if st in ("verified", "rti_filed"):
                kind, tag, tie_rank = "verified", "Verified", 3
                ts = r["verified_at"] or r["created_at"]
            elif st in ("cleared", "resolved_explained"):
                kind, tag, tie_rank = "cleared", "Cleared", 3
                ts = r["verified_at"] or r["created_at"]
            elif st in ("retracted", "retracted_false_positive"):
                kind, tag, tie_rank = "retracted", "Retracted", 2
                ts = r["verified_at"] or r["created_at"]
            elif st == "under_review":
                kind, tag, tie_rank = "under_review", "Editorial review", 2
                ts = r["verified_at"] or r["created_at"]
            elif st == "disputed":
                kind, tag, tie_rank = "disputed", "Disputed", 2
                ts = r["verified_at"] or r["created_at"]
            else:
                kind, tag, tie_rank = "peer_review", "In review", 1
                ts = r["created_at"]
            push(
                ts,
                tie_rank,
                {
                    "id": f"finding:{r['id']}",
                    "ts": ts,
                    "kind": kind,
                    "tag": tag,
                    "title": r["title"],
                    "actor": r["submitted_by"],
                    "methodology_code": r["methodology_code"],
                    "finding_id": r["id"],
                    "monetary_exposure_inr_cr": _inr_to_cr(r["monetary_exposure_inr"]),
                    "is_demo_sample": bool(r["is_demo_sample"]),
                },
            )

        verif_rows = conn.execute(
            """
            SELECT pv.id, pv.finding_id, pv.verifier_handle, pv.verdict, pv.created_at,
                   f.title AS finding_title, f.methodology_code,
                   f.monetary_exposure_inr, f.is_demo_sample
            FROM peer_verifications pv
            JOIN findings f ON f.id = pv.finding_id
            WHERE COALESCE(f.quarantined, 0) = 0 AND f.status != 'quarantined'
            ORDER BY pv.created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        for r in verif_rows:
            verdict = str(r["verdict"] or "")
            if verdict == "confirm":
                kind, tag, tie_rank = "peer_review", "Peer check", 1
                title = f"Peer check confirmed: {r['finding_title']}"
            else:
                kind, tag, tie_rank = "disputed", "Peer dispute", 2
                title = f"Peer review flagged ({verdict}): {r['finding_title']}"
            push(
                r["created_at"],
                tie_rank,
                {
                    "id": f"verif:{r['id']}",
                    "ts": r["created_at"],
                    "kind": kind,
                    "tag": tag,
                    "title": title,
                    "actor": r["verifier_handle"],
                    "methodology_code": r["methodology_code"],
                    "finding_id": r["finding_id"],
                    "monetary_exposure_inr_cr": _inr_to_cr(r["monetary_exposure_inr"]),
                    "is_demo_sample": bool(r["is_demo_sample"]),
                },
            )

        rti_rows = conn.execute(
            """
            SELECT rd.id, rd.finding_id, rd.public_authority, rd.subject, rd.created_at,
                   f.title AS finding_title, f.methodology_code, f.submitted_by,
                   f.monetary_exposure_inr, f.is_demo_sample
            FROM rti_drafts rd
            JOIN findings f ON f.id = rd.finding_id
            WHERE COALESCE(f.quarantined, 0) = 0 AND f.status != 'quarantined'
              AND rd.status != 'quarantined'
            ORDER BY rd.created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        for r in rti_rows:
            push(
                r["created_at"],
                4,
                {
                    "id": f"rti:{r['id']}",
                    "ts": r["created_at"],
                    "kind": "rti",
                    "tag": "RTI draft",
                    "title": f"RTI Sec 6(1) draft for {r['public_authority']}: {r['finding_title']}",
                    "actor": r["submitted_by"],
                    "methodology_code": r["methodology_code"],
                    "finding_id": r["finding_id"],
                    "monetary_exposure_inr_cr": _inr_to_cr(r["monetary_exposure_inr"]),
                    "is_demo_sample": bool(r["is_demo_sample"]),
                },
            )

        events.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
        return [item[3] for item in events[:limit]]

    @staticmethod
    def _build_mission_rungs(
        conn: Any,
        mission_id: str | None = None,
        max_rungs: int = 24,
        mission_ids: list[str] | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        stage_order = {
            ("verified", "finding"): 0,
            ("verified", "task"): 1,
            ("cleared", "finding"): 2,
            ("cleared", "task"): 2,
            ("peer_review", "finding"): 3,
            ("hard", "task"): 4,
            ("disputed", "finding"): 5,
            ("disputed", "task"): 5,
            ("queued", "task"): 6,
        }
        grouped: dict[str, list[tuple[int, str, str, dict[str, Any]]]] = {}

        quarantine_task_guard = (
            "(t.target_finding_id IS NULL OR NOT EXISTS ("
            "SELECT 1 FROM findings qf WHERE qf.id = t.target_finding_id "
            "AND (COALESCE(qf.quarantined, 0) = 1 OR qf.status = 'quarantined')))"
        )
        if mission_id is not None:
            target_mids = [mission_id]
            per_mission_limit = 300
        elif mission_ids is not None:
            target_mids = list(mission_ids)
            per_mission_limit = max(48, max_rungs * 2)
        else:
            target_mids = [
                str(r["id"])
                for r in conn.execute("SELECT id FROM missions WHERE status != 'archived'").fetchall()
            ]
            per_mission_limit = max(48, max_rungs * 2)

        f_rows: list[Any] = []
        t_rows: list[Any] = []
        for mid in target_mids:
            f_rows.extend(
                conn.execute(
                    "SELECT id, mission_id, title, status, quarantined, created_at FROM findings "
                    "WHERE mission_id = ? AND COALESCE(quarantined, 0) = 0 AND status != 'quarantined' "
                    "ORDER BY created_at DESC LIMIT ?",
                    (mid, per_mission_limit),
                ).fetchall()
            )
            t_rows.extend(
                conn.execute(
                    f"SELECT t.id, t.mission_id, t.title, t.task_type, t.status, t.target_finding_id, t.created_at "
                    f"FROM tasks t WHERE t.mission_id = ? AND t.status != 'retired' AND {quarantine_task_guard} "
                    f"ORDER BY CASE t.status WHEN 'completed' THEN 0 WHEN 'settled' THEN 0 WHEN 'claimed' THEN 1 "
                    f"WHEN 'failed' THEN 2 WHEN 'needs_adjudication' THEN 2 ELSE 3 END, "
                    f"t.created_at DESC LIMIT ?",
                    (mid, per_mission_limit),
                ).fetchall()
            )

        for r in f_rows:
            if _is_quarantined_row(r):
                continue
            st = str(r["status"] or "")
            if st in ("verified", "rti_filed"):
                rung_state = "verified"
            elif st in ("cleared", "resolved_explained"):
                rung_state = "cleared"
            elif st in ("disputed", "retracted", "retracted_false_positive", "under_review"):
                rung_state = "disputed"
            else:
                rung_state = "peer_review"
            rung = {
                "id": r["id"],
                "state": rung_state,
                "label": str(r["title"] or "")[:90],
                "kind": "finding",
                "finding_id": r["id"],
            }
            rank = stage_order.get((rung_state, "finding"), 3)
            grouped.setdefault(r["mission_id"], []).append((rank, str(r["created_at"] or ""), r["id"], rung))

        for r in t_rows:
            st = str(r["status"] or "")
            ttype = str(r["task_type"] or "")
            if st in ("completed", "settled"):
                rung_state = "verified"
            elif st == "claimed" or (st == "queued" and ttype == "peer_verify"):
                rung_state = "hard"
            elif st in ("failed", "needs_adjudication"):
                rung_state = "disputed"
            else:
                rung_state = "queued"
            rung = {
                "id": r["id"],
                "state": rung_state,
                "label": str(r["title"] or "")[:90],
                "kind": "task",
            }
            if r["target_finding_id"]:
                rung["finding_id"] = r["target_finding_id"]
            rank = stage_order.get((rung_state, "task"), 6)
            grouped.setdefault(r["mission_id"], []).append((rank, str(r["created_at"] or ""), r["id"], rung))

        result: dict[str, list[dict[str, Any]]] = {}
        for mid, items in grouped.items():
            items.sort(key=lambda item: (item[0], item[1], item[2]))
            if len(items) > max_rungs:
                frontier = [it for it in items if it[3]["state"] != "verified"]
                verified_items = [it for it in items if it[3]["state"] == "verified"]
                keep_frontier = frontier[: min(len(frontier), max_rungs // 2)]
                keep_verified = verified_items[-(max_rungs - len(keep_frontier)) :] if (max_rungs - len(keep_frontier)) > 0 else []
                combined = keep_verified + keep_frontier
                combined.sort(key=lambda item: (item[0], item[1], item[2]))
                items = combined[:max_rungs]
            result[mid] = [it[3] for it in items]
        return result

    def _fetch_missions_with_coverage(
        self, conn: Any
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        primary_total_map: dict[str, int] = {}
        primary_done_map: dict[str, int] = {}
        queued_map: dict[str, int] = {}
        claimed_map: dict[str, int] = {}
        pv_total_map: dict[str, int] = {}
        pv_done_map: dict[str, int] = {}
        pv_open_map: dict[str, int] = {}
        for r in conn.execute(
            "SELECT mission_id, CASE WHEN task_type = 'peer_verify' THEN 1 ELSE 0 END AS is_pv, "
            "status, COUNT(*) AS c FROM tasks WHERE status != 'retired' GROUP BY mission_id, is_pv, status"
        ).fetchall():
            mid = r["mission_id"]
            cnt = int(r["c"])
            st = str(r["status"] or "")
            if int(r["is_pv"]) == 1:
                pv_total_map[mid] = pv_total_map.get(mid, 0) + cnt
                if st == "completed":
                    pv_done_map[mid] = pv_done_map.get(mid, 0) + cnt
                elif st in ("queued", "claimed"):
                    pv_open_map[mid] = pv_open_map.get(mid, 0) + cnt
            else:
                primary_total_map[mid] = primary_total_map.get(mid, 0) + cnt
                if st == "completed":
                    primary_done_map[mid] = primary_done_map.get(mid, 0) + cnt
                elif st == "queued":
                    queued_map[mid] = queued_map.get(mid, 0) + cnt
                elif st == "claimed":
                    claimed_map[mid] = claimed_map.get(mid, 0) + cnt
        pr_map = {
            r["mission_id"]: int(r["c"])
            for r in conn.execute(
                "SELECT mission_id, COUNT(*) AS c FROM findings "
                "WHERE status IN ('peer_review', 'raw_lead') AND COALESCE(quarantined, 0) = 0 "
                "GROUP BY mission_id"
            ).fetchall()
        }
        cleared_findings_map = {
            r["mission_id"]: int(r["c"])
            for r in conn.execute(
                "SELECT mission_id, COUNT(*) AS c FROM findings "
                "WHERE status IN ('cleared', 'resolved_explained') AND COALESCE(quarantined, 0) = 0 "
                "GROUP BY mission_id"
            ).fetchall()
        }
        # Archived missions (the original M1–M8 and removed projects) keep their public record
        # but are not listed, mapped or offered new scans.
        rows = conn.execute(
            "SELECT * FROM missions WHERE status != 'archived' ORDER BY priority ASC, id ASC"
        ).fetchall()
        rungs_map = self._build_mission_rungs(conn, mission_ids=[str(r["id"]) for r in rows])
        mission_unique_inr, coverage_summary = self._compute_coverage_summary(conn, rows)
        token_summaries = self._token_summaries(conn, rows)
        missions = [
            self._serialize_mission(
                r,
                queued_tasks=queued_map.get(r["id"], 0),
                claimed_tasks=claimed_map.get(r["id"], 0),
                peer_review_count=pr_map.get(r["id"], 0),
                rungs=rungs_map.get(r["id"], []),
                unique_coverage_inr=mission_unique_inr.get(str(r["id"]), 0.0),
                cleared_findings_count=cleared_findings_map.get(r["id"], 0),
                token_summary=token_summaries.get(str(r["id"])),
                project_code=self.resolve_project_code(r["slug"], str(r["methodology_code"] or "")),
                primary_total_tasks=primary_total_map.get(r["id"], int(r["total_tasks"] or 0)),
                primary_completed_tasks=primary_done_map.get(r["id"], int(r["completed_tasks"] or 0)),
                peer_checks_total=pv_total_map.get(r["id"], 0),
                peer_checks_done=pv_done_map.get(r["id"], 0),
                peer_checks_open=pv_open_map.get(r["id"], 0),
            )
            for r in rows
        ]
        return missions, coverage_summary

    def _fetch_missions(self, conn: Any) -> list[dict[str, Any]]:
        missions, _ = self._fetch_missions_with_coverage(conn)
        return missions

    def list_missions(self) -> dict[str, Any]:
        with self.db.read() as conn:
            missions, coverage_summary = self._fetch_missions_with_coverage(conn)
        return {
            "missions": missions,
            "total": len(missions),
            "coverage_summary": coverage_summary,
        }

    def _compute_mission_contributors(
        self, conn: Any, mission_id: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        cutoff = (
            (datetime.now(timezone.utc) - timedelta(hours=1))
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        all_handles: set[str] = set()
        hour_handles: set[str] = set()

        tasks_all: dict[str, int] = {}
        tasks_hour: dict[str, int] = {}
        cleared_task_ids_all: dict[str, set[str]] = {}
        cleared_task_ids_hour: dict[str, set[str]] = {}

        for tr in conn.execute(
            "SELECT id, claimed_by_handle, task_type, completed_at, updated_at, "
            "CASE WHEN task_type != 'peer_verify' AND methodology_code != 'W1' "
            "AND output_json LIKE '%\"cleared\"%' THEN output_json ELSE '' END AS output_json "
            "FROM tasks WHERE mission_id = ? AND status = 'completed' AND claimed_by_handle IS NOT NULL",
            (mission_id,),
        ).fetchall():
            h = str(tr["claimed_by_handle"] or "").strip()
            if not h:
                continue
            all_handles.add(h)
            tasks_all[h] = tasks_all.get(h, 0) + 1
            ts = str(tr["completed_at"] or tr["updated_at"] or "")
            in_hour = bool(ts and ts >= cutoff)
            if in_hour:
                hour_handles.add(h)
                tasks_hour[h] = tasks_hour.get(h, 0) + 1
            if tr["task_type"] != "peer_verify" and tr["output_json"]:
                out = loads_json(tr["output_json"], {})
                if isinstance(out, dict) and out.get("outcome") == "cleared":
                    tid = str(tr["id"])
                    cleared_task_ids_all.setdefault(h, set()).add(tid)
                    if in_hour:
                        cleared_task_ids_hour.setdefault(h, set()).add(tid)

        findings_all: dict[str, int] = {}
        findings_hour: dict[str, int] = {}
        verified_all: dict[str, int] = {}
        verified_hour: dict[str, int] = {}
        for fr in conn.execute(
            "SELECT submitted_by, status, created_at, verified_at FROM findings "
            "WHERE mission_id = ? AND COALESCE(quarantined, 0) = 0 AND status != 'quarantined'",
            (mission_id,),
        ).fetchall():
            h = str(fr["submitted_by"] or "").strip()
            if not h:
                continue
            all_handles.add(h)
            findings_all[h] = findings_all.get(h, 0) + 1
            if fr["created_at"] and str(fr["created_at"]) >= cutoff:
                hour_handles.add(h)
                findings_hour[h] = findings_hour.get(h, 0) + 1
            if fr["status"] == "verified":
                verified_all[h] = verified_all.get(h, 0) + 1
                v_ts = str(fr["verified_at"] or fr["created_at"] or "")
                if v_ts and v_ts >= cutoff:
                    hour_handles.add(h)
                    verified_hour[h] = verified_hour.get(h, 0) + 1

        verifs_all: dict[str, int] = {}
        verifs_hour: dict[str, int] = {}
        for vr in conn.execute(
            "SELECT pv.verifier_handle, pv.created_at "
            "FROM peer_verifications pv "
            "JOIN findings f ON f.id = pv.finding_id "
            "WHERE f.mission_id = ? AND COALESCE(f.quarantined, 0) = 0 AND f.status != 'quarantined'",
            (mission_id,),
        ).fetchall():
            h = str(vr["verifier_handle"] or "").strip()
            if not h:
                continue
            all_handles.add(h)
            verifs_all[h] = verifs_all.get(h, 0) + 1
            if vr["created_at"] and str(vr["created_at"]) >= cutoff:
                hour_handles.add(h)
                verifs_hour[h] = verifs_hour.get(h, 0) + 1

        se_all: dict[str, int] = {}
        se_hour: dict[str, int] = {}
        for sr in conn.execute(
            """
            SELECT se.agent_handle, se.event_type, se.points, se.ref_id, se.created_at
            FROM score_events se
            WHERE (
                se.event_type IN ('finding_submitted', 'finding_verified_author_bonus')
                AND EXISTS (
                    SELECT 1 FROM findings f
                    WHERE f.id = se.ref_id AND f.mission_id = ?
                      AND COALESCE(f.quarantined, 0) = 0 AND f.status != 'quarantined'
                )
            ) OR (
                se.event_type = 'peer_verification'
                AND EXISTS (
                    SELECT 1 FROM peer_verifications pv
                    JOIN findings f ON f.id = pv.finding_id
                    WHERE pv.id = se.ref_id AND f.mission_id = ?
                      AND COALESCE(f.quarantined, 0) = 0 AND f.status != 'quarantined'
                )
            ) OR (
                se.event_type = 'cleared_scan'
                AND EXISTS (
                    SELECT 1 FROM tasks t
                    WHERE t.id = se.ref_id AND t.mission_id = ?
                )
            )
            """,
            (mission_id, mission_id, mission_id),
        ).fetchall():
            h = str(sr["agent_handle"] or "").strip()
            if not h:
                continue
            pts = int(sr["points"] or 0)
            all_handles.add(h)
            se_all[h] = se_all.get(h, 0) + pts
            in_hour = bool(sr["created_at"] and str(sr["created_at"]) >= cutoff)
            if in_hour:
                hour_handles.add(h)
                se_hour[h] = se_hour.get(h, 0) + pts
            if sr["event_type"] == "cleared_scan" and sr["ref_id"]:
                ref_id = str(sr["ref_id"])
                cleared_task_ids_all.setdefault(h, set()).add(ref_id)
                if in_hour:
                    cleared_task_ids_hour.setdefault(h, set()).add(ref_id)

        if not all_handles:
            return [], []

        agent_rows: dict[str, Any] = {}
        sorted_handles = sorted(all_handles)
        for i in range(0, len(sorted_handles), 200):
            chunk = sorted_handles[i : i + 200]
            ph = ",".join("?" for _ in chunk)
            for ar in conn.execute(f"SELECT * FROM agents WHERE handle IN ({ph})", chunk).fetchall():
                agent_rows[str(ar["handle"])] = ar

        def _build_entry(handle: str, *, hourly: bool) -> dict[str, Any]:
            t_done = tasks_hour.get(handle, 0) if hourly else tasks_all.get(handle, 0)
            f_sub = findings_hour.get(handle, 0) if hourly else findings_all.get(handle, 0)
            f_ver = verified_hour.get(handle, 0) if hourly else verified_all.get(handle, 0)
            v_done = verifs_hour.get(handle, 0) if hourly else verifs_all.get(handle, 0)
            c_sub = (
                len(cleared_task_ids_hour.get(handle, ()))
                if hourly
                else len(cleared_task_ids_all.get(handle, ()))
            )
            all_pts = se_all.get(handle, 0)
            all_score = all_pts if all_pts > 0 else tasks_all.get(handle, 0)
            hr_pts = se_hour.get(handle, 0)
            hr_score = hr_pts if hr_pts > 0 else tasks_hour.get(handle, 0)
            eff_score = hr_score if hourly else all_score

            ar = agent_rows.get(handle)
            if ar is not None:
                base = self._serialize_agent(
                    ar,
                    window_score=eff_score,
                    all_time_score=all_score,
                    hourly_event_score=hr_score,
                )
            else:
                base = {
                    "id": handle,
                    "agent_id": handle,
                    "handle": handle,
                    "display_name": handle,
                    "provider": "rule-engine",
                    "model": "deterministic-cpu-v1",
                    "coding_agent": "",
                    "coding_agent_model": "",
                    "mode": "auto",
                    "region": "IN",
                    "capabilities": [],
                    "status": "idle",
                    "is_active": False,
                    "heartbeat_age_seconds": 999999,
                    "current_task_id": None,
                    "joined_at": None,
                    "last_heartbeat_at": None,
                    "profile_url": f"/a/{handle}",
                }
            base["tasks_completed"] = int(t_done)
            base["findings_submitted"] = int(f_sub)
            base["findings_verified"] = int(f_ver)
            base["verifications_done"] = int(v_done)
            base["cleared_submitted"] = int(c_sub)
            base["compute_score"] = int(all_score)
            base["score"] = int(eff_score)
            base["window_score"] = int(eff_score)
            base["hourly_score"] = int(hr_score)
            return base

        contributors_all = [_build_entry(h, hourly=False) for h in all_handles]
        contributors_all.sort(key=lambda c: (-c["score"], -c["tasks_completed"], c["handle"]))

        contributors_hour = [_build_entry(h, hourly=True) for h in hour_handles]
        contributors_hour.sort(key=lambda c: (-c["score"], -c["tasks_completed"], c["handle"]))

        return contributors_all[:100], contributors_hour[:100]

    @staticmethod
    def _classify_p1_group(task_id: str, title: str, jurisdiction: str) -> str:
        combined = f"{title} {jurisdiction}"
        if _P1_DELHI_UT_RE.search(combined):
            return "Delhi & Union Territories"
        if _P1_UNION_CENTRAL_RE.search(f"{task_id} {combined}"):
            return "Union & Central Forces"
        return "State Police"

    def _build_visual_summary(
        self,
        conn: Any,
        *,
        mission_row: Any,
        mission: dict[str, Any],
        parsed_tasks: list[tuple[Any, dict[str, Any]]],
        findings: list[dict[str, Any]],
        cleared_findings_count: int,
        counts: dict[str, int],
    ) -> dict[str, Any]:
        methodology_code = str(mission_row["methodology_code"] or "").strip()
        # Counted over every task of the project in SQL: the listed tasks can be a first page only.
        completed_cnt = counts["audits_done"]
        claimed_cnt = counts["claimed"]
        queued_cnt = counts["queued"]
        total_cnt = audits_total = counts["audits_total"]
        audits_done = completed_cnt
        checks_done = counts["checks_done"]
        checks_total = counts["checks_total"]
        waiting_cnt = 0
        planned_cnt = len(mission.get("planned_tasks") or [])
        pct_complete = round((completed_cnt / total_cnt) * 100.0, 2) if total_cnt > 0 else 0.0

        summary: dict[str, Any] = {
            "project_code": str(mission.get("project_code") or self.resolve_project_code(mission_row["slug"], methodology_code)),
            "methodology_code": methodology_code,
            "status_counts": {
                "completed": completed_cnt,
                "claimed": claimed_cnt,
                "queued": queued_cnt,
                "waiting": waiting_cnt,
                "planned": planned_cnt,
                "total": total_cnt,
                "pct_complete": pct_complete,
                "audits_done": audits_done,
                "audits_total": audits_total,
                "checks_done": checks_done,
                "checks_total": checks_total,
            },
        }

        mid = str(mission_row["id"])
        if methodology_code == "W1":
            latest_row = conn.execute(
                "SELECT COALESCE(MAX(COALESCE(completed_at, updated_at)), '') AS ts "
                "FROM tasks WHERE mission_id = ? AND methodology_code = 'W1' AND status = 'completed'",
                (mid,),
            ).fetchone()
            cache_key = (
                mid,
                completed_cnt,
                total_cnt,
                str(latest_row["ts"] if latest_row else ""),
            )
            with self._w1_cache_lock:
                cached_entry = self._w1_visual_cache.get(mid)
                if cached_entry is not None and cached_entry[0] == cache_key:
                    summary["website_audit"] = dict(cached_entry[1])
                    return summary

            hosts_observed = 0
            hosts_unassessable = 0
            cp_counts: dict[str, dict[str, int]] = {
                key: {"observed": 0, "not_observed": 0, "unassessable": 0, "not_applicable": 0}
                for key in W1_CHECKS
            }
            reasons: dict[str, int] = {k: 0 for k in _W1_REASON_KEYS}
            # Deduplicate by normalized domain (latest completed observation wins) so applicability-scoped
            # checkpoint rollups never double-count a host across legacy and upgraded batches.
            by_domain: dict[str, dict[str, Any]] = {}
            unnamed_obs: list[dict[str, Any]] = []

            obs_rows = conn.execute(
                "SELECT output_json FROM tasks WHERE mission_id = ? AND methodology_code = 'W1' "
                "AND status = 'completed' ORDER BY COALESCE(completed_at, updated_at) ASC, id ASC",
                (mid,),
            ).fetchall()
            for orow in obs_rows:
                out = loads_json(orow["output_json"], {})
                obs_list = out.get("observations") if isinstance(out, dict) else None
                if not isinstance(obs_list, list):
                    continue
                for item in obs_list:
                    if not isinstance(item, dict):
                        continue
                    dom = str(item.get("domain") or "").strip().lower()
                    if dom:
                        by_domain[dom] = item
                    else:
                        unnamed_obs.append(item)

            host_observations = list(by_domain.values()) + unnamed_obs
            for item in host_observations:
                outcome = str(item.get("outcome") or "").strip().lower()
                if outcome == "observed":
                    hosts_observed += 1
                elif outcome == "unassessable":
                    hosts_unassessable += 1
                reason = item.get("reason")
                if isinstance(reason, str) and reason.strip():
                    rkey = reason.strip()
                    reasons[rkey] = reasons.get(rkey, 0) + 1
                checks = item.get("checks")
                if isinstance(checks, dict):
                    # Supports legacy 7-key, 42-key, and current 46-key observations gracefully.
                    for key in W1_CHECKS:
                        val = str(checks.get(key) or "").strip().lower()
                        if val in W1_VALID_CHECK_STATES:
                            cp_counts[key][val] += 1

            w1_payload = build_website_audit_visual_payload(
                batches_done=completed_cnt,
                batches_total=total_cnt,
                hosts_observed=hosts_observed,
                hosts_unassessable=hosts_unassessable,
                cp_counts=cp_counts,
                reasons=reasons,
                host_observations=host_observations,
            )
            with self._w1_cache_lock:
                self._w1_visual_cache[mid] = (cache_key, w1_payload)
            summary["website_audit"] = dict(w1_payload)
            return summary

        if methodology_code == UNION_BUDGET_METHODOLOGY:
            summary["union_budget"] = self._union_budget_summary(conn, mission_row=mission_row)
            return summary

        # Budget / procurement / audit projects (P1, M1-M8, etc.)
        verified_cnt = sum(1 for f in findings if f.get("status") == "verified")
        peer_review_cnt = sum(1 for f in findings if f.get("status") in ("peer_review", "raw_lead"))
        disputed_cnt = sum(1 for f in findings if f.get("status") in ("disputed", "needs_adjudication"))
        cleared_scans_cnt = int(mission_row["cleared_count"] or 0)
        cleared_cnt = cleared_scans_cnt + int(cleared_findings_count)

        total_exposure_inr = sum(
            _safe_finite_float(f.get("monetary_exposure_inr"), 0.0)
            for f in findings
            if f.get("status") not in ("retracted", "retracted_false_positive")
        )

        divergences: list[dict[str, Any]] = []
        for f in findings:
            am = f.get("anomaly_metrics") if isinstance(f.get("anomaly_metrics"), dict) else {}
            pct_diff: float | None = None
            for pkey in ("pct_difference", "variance_pct", "escalation_pct", "pct_variance"):
                raw_pct = am.get(pkey)
                if raw_pct is not None and not isinstance(raw_pct, bool):
                    try:
                        val = float(raw_pct)
                        if math.isfinite(val):
                            pct_diff = round(val, 2)
                            break
                    except (TypeError, ValueError):
                        pass
            divergences.append(
                {
                    "finding_id": str(f.get("id") or ""),
                    "entity_name": str(f.get("entity_name") or ""),
                    "title": str(f.get("title") or ""),
                    "pct_difference": pct_diff,
                    "monetary_exposure_inr_cr": _safe_finite_float(f.get("monetary_exposure_inr_cr"), 0.0),
                    "status": str(f.get("status") or ""),
                    "severity": str(f.get("severity") or ""),
                    "fiscal_year": str(f.get("fiscal_year") or ""),
                    "rule_triggered": str(f.get("rule_triggered") or ""),
                }
            )
        divergences.sort(
            key=lambda d: (
                1 if d["pct_difference"] is not None else 0,
                abs(d["pct_difference"]) if d["pct_difference"] is not None else 0.0,
                d["monetary_exposure_inr_cr"],
            ),
            reverse=True,
        )

        group_counts: dict[str, list[int]] = {}
        mission_juris = str(mission_row["jurisdiction"] or "").strip() or "All jurisdictions"
        for t, t_payload in parsed_tasks:
            if t["task_type"] == "peer_verify":
                continue
            ctx = t_payload.get("context") if isinstance(t_payload.get("context"), dict) else {}
            t_juris = str(t_payload.get("jurisdiction") or ctx.get("jurisdiction") or "").strip()
            if methodology_code == "P1":
                glabel = self._classify_p1_group(str(t["id"]), str(t["title"] or ""), t_juris)
            else:
                glabel = t_juris or mission_juris
            gb = group_counts.setdefault(glabel, [0, 0])
            gb[1] += 1
            if t["status"] == "completed":
                gb[0] += 1

        if methodology_code == "P1":
            ordered_groups = [g for g in _P1_GROUP_ORDER if g in group_counts] + sorted(
                g for g in group_counts if g not in _P1_GROUP_ORDER
            )
        else:
            ordered_groups = sorted(group_counts.keys(), key=lambda k: (-group_counts[k][1], k))

        groups = [
            {
                "label": g,
                "done": group_counts[g][0],
                "total": group_counts[g][1],
                "pct": round((group_counts[g][0] / group_counts[g][1]) * 100.0, 2)
                if group_counts[g][1] > 0
                else 0.0,
            }
            for g in ordered_groups
        ]

        summary["budget_audit"] = {
            "verified_count": verified_cnt,
            "peer_review_count": peer_review_cnt,
            "cleared_count": cleared_cnt,
            "disputed_count": disputed_cnt,
            "total_exposure_inr_cr": _inr_to_cr(total_exposure_inr),
            "top_divergences": divergences[:12],
            "groups": groups,
        }
        return summary

    def get_mission_detail(self, slug: str) -> dict[str, Any]:
        slug = self.resolve_slug(slug)
        with self.db.read() as conn:
            row = conn.execute("SELECT * FROM missions WHERE slug = ? OR id = ?", (slug, slug)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail=f"Mission '{slug}' not found")
            mid = row["id"]
            primary_total = 0
            primary_done = 0
            queued_cnt = 0
            claimed_cnt = 0
            pv_total = 0
            pv_done = 0
            pv_open = 0
            for r in conn.execute(
                "SELECT CASE WHEN task_type = 'peer_verify' THEN 1 ELSE 0 END AS is_pv, "
                "status, COUNT(*) AS c FROM tasks WHERE mission_id = ? AND status != 'retired' GROUP BY is_pv, status",
                (mid,),
            ).fetchall():
                cnt = int(r["c"])
                st = str(r["status"] or "")
                if int(r["is_pv"]) == 1:
                    pv_total += cnt
                    if st == "completed":
                        pv_done += cnt
                    elif st in ("queued", "claimed"):
                        pv_open += cnt
                else:
                    primary_total += cnt
                    if st == "completed":
                        primary_done += cnt
                    elif st == "queued":
                        queued_cnt += cnt
                    elif st == "claimed":
                        claimed_cnt += cnt
            pr_cnt = int(
                conn.execute(
                    "SELECT COUNT(*) AS c FROM findings WHERE mission_id = ? "
                    "AND status IN ('peer_review', 'raw_lead') AND COALESCE(quarantined, 0) = 0",
                    (mid,),
                ).fetchone()["c"]
            )
            cf_cnt = int(
                conn.execute(
                    "SELECT COUNT(*) AS c FROM findings WHERE mission_id = ? "
                    "AND status IN ('cleared', 'resolved_explained') AND COALESCE(quarantined, 0) = 0",
                    (mid,),
                ).fetchone()["c"]
            )
            rungs = self._build_mission_rungs(conn, mission_id=mid).get(mid, [])
            mission_unique_inr, _ = self._compute_coverage_summary(conn, [row])
            mission = self._serialize_mission(
                row,
                queued_tasks=queued_cnt,
                claimed_tasks=claimed_cnt,
                peer_review_count=pr_cnt,
                rungs=rungs,
                unique_coverage_inr=mission_unique_inr.get(str(mid), 0.0),
                cleared_findings_count=cf_cnt,
                token_summary=self._token_summaries(conn, [row]).get(str(mid)),
                project_code=self.resolve_project_code(row["slug"], str(row["methodology_code"] or "")),
                primary_total_tasks=primary_total if primary_total > 0 else int(row["total_tasks"] or 0),
                primary_completed_tasks=primary_done if primary_total > 0 else int(row["completed_tasks"] or 0),
                peer_checks_total=pv_total,
                peer_checks_done=pv_done,
                peer_checks_open=pv_open,
            )

            source_ids = mission["source_ids"]
            sources: list[dict[str, Any]] = []
            if source_ids:
                placeholders = ",".join("?" for _ in source_ids)
                s_rows = conn.execute(
                    f"SELECT * FROM public_sources WHERE id IN ({placeholders})", source_ids
                ).fetchall()
                sources = [self._serialize_source(r) for r in s_rows]

            f_rows = conn.execute(
                "SELECT * FROM findings WHERE mission_id = ? "
                "AND COALESCE(quarantined, 0) = 0 AND status != 'quarantined' "
                "ORDER BY COALESCE(verified_at, created_at) DESC LIMIT 200",
                (mid,),
            ).fetchall()
            f_ids = [str(r["id"]) for r in f_rows]
            rti_ids: set[str] = set()
            if f_ids:
                ph = ",".join("?" for _ in f_ids)
                rti_ids = {
                    str(r["finding_id"])
                    for r in conn.execute(
                        f"SELECT finding_id FROM rti_drafts WHERE status != 'quarantined' AND finding_id IN ({ph})",
                        f_ids,
                    ).fetchall()
                }
            corr_map = self._fetch_corrections_map(conn, f_ids)
            findings = [
                self._serialize_finding(
                    r,
                    has_rti_draft=(r["id"] in rti_ids),
                    corrections=corr_map.get(str(r["id"]), []),
                )
                for r in f_rows
            ]

            all_tasks = primary_total + pv_total
            truncated = all_tasks > MAX_PROJECT_TASKS_LISTED
            t_rows = conn.execute(
                f"""
                SELECT {_TASK_LIST_COLUMNS}
                FROM tasks t WHERE t.mission_id = ?
                  AND t.status != 'retired'
                  AND {_TASK_NOT_QUARANTINED}
                ORDER BY CASE t.status WHEN 'queued' THEN 0 WHEN 'claimed' THEN 1 ELSE 2 END, t.updated_at DESC, t.id ASC
                LIMIT ?
                """,
                (mid, LARGE_PROJECT_TASKS_LISTED if truncated else MAX_PROJECT_TASKS_LISTED),
            ).fetchall()
            parsed_tasks: list[tuple[Any, dict[str, Any]]] = [
                (t, loads_json(t["payload_json"], {})) for t in t_rows
            ]
            tasks = [_public_task(t, t_payload) for t, t_payload in parsed_tasks]

            contributors_all, contributors_hour = self._compute_mission_contributors(conn, str(mid))
            visual_summary = self._build_visual_summary(
                conn,
                mission_row=row,
                mission=mission,
                parsed_tasks=parsed_tasks,
                findings=findings,
                cleared_findings_count=cf_cnt,
                counts={
                    "audits_total": primary_total,
                    "audits_done": primary_done,
                    "queued": queued_cnt,
                    "claimed": claimed_cnt,
                    "checks_total": pv_total,
                    "checks_done": pv_done,
                },
            )

        return {
            **mission,
            "sources": sources,
            "findings": findings,
            "tasks": tasks,
            "tasks_listed": len(tasks),
            "tasks_truncated": truncated,
            "contributors_all": contributors_all,
            "contributors_hour": contributors_hour,
            "visual_summary": visual_summary,
        }

    def get_website_matrix(self, slug: str) -> dict[str, Any]:
        slug = self.resolve_slug(slug)
        if slug != "gov-in-public-information":
            raise HTTPException(status_code=404, detail="Conformity matrix not found")
        return load_conformity_matrix()

    def website_observations(self, slug: str, task_id: str) -> dict[str, Any]:
        slug = self.resolve_slug(slug)
        with self.db.read() as conn:
            row = conn.execute(
                "SELECT output_json, payload_json, completed_at FROM tasks WHERE mission_slug = ? AND id = ? "
                "AND methodology_code = 'W1' AND status = 'completed'", (slug, task_id),
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="No completed observation report for this batch")
        manifest = loads_json(row["payload_json"], {})
        return {"task_id": task_id, "manifest": manifest["retrieval_url"], "manifest_sha256": manifest["expected_sha256"],
                "completed_at": row["completed_at"], "review_status": "volunteer_observations",
                "matrix_version": W1_MATRIX_VERSION,
                "matrix_endpoint": "/api/missions/gov-in-public-information/matrix",
                "guidelines": "https://guidelines.india.gov.in/annexure-ii-matrix-to-check-conformity/",
                **loads_json(row["output_json"], {})}

    def list_mission_tasks(
        self, slug: str, *, status_filter: str = "all", q: str = "", kind: str = "audit", offset: int = 0, limit: int = 25,
    ) -> dict[str, Any]:
        """One page of a project's tasks. A project with thousands of tasks is searched here, not listed whole."""
        slug = self.resolve_slug(slug)
        wanted = {"all": None, "open": "queued", "claimed": "claimed", "completed": "completed"}
        if status_filter not in wanted:
            raise HTTPException(status_code=422, detail="status must be one of: all, open, claimed, completed")
        if kind not in ("audit", "check", "all"):
            raise HTTPException(status_code=422, detail="kind must be one of: audit, check, all")
        limit = max(1, min(int(limit), MAX_TASK_PAGE))
        offset = max(0, int(offset))
        clauses = ["t.mission_id = ?", "t.status != 'retired'", _TASK_NOT_QUARANTINED]
        params: list[Any] = []
        if kind == "audit":
            clauses.append("t.task_type != 'peer_verify'")
        elif kind == "check":
            clauses.append("t.task_type = 'peer_verify'")
        needle = " ".join(str(q or "").split())[:120].lower()
        if needle:
            clauses.append("(LOWER(t.title) LIKE ? ESCAPE '\\' OR LOWER(t.id) LIKE ? ESCAPE '\\')")
            params.extend([f"%{escape_like(needle)}%"] * 2)
        where = " AND ".join(clauses)
        with self.db.read() as conn:
            mission = conn.execute("SELECT id FROM missions WHERE slug = ? OR id = ?", (slug, slug)).fetchone()
            if mission is None:
                raise HTTPException(status_code=404, detail=f"Mission '{slug}' not found")
            mid = mission["id"]
            counts = {"all": 0, "open": 0, "claimed": 0, "completed": 0}
            for r in conn.execute(
                f"SELECT t.status AS status, COUNT(*) AS c FROM tasks t WHERE {where} GROUP BY t.status", (mid, *params)
            ).fetchall():
                n = int(r["c"])
                counts["all"] += n
                st = str(r["status"] or "")
                if st == "queued":
                    counts["open"] += n
                elif st in ("claimed", "completed"):
                    counts[st] += n
            page_clauses, page_params = list(clauses), list(params)
            if wanted[status_filter] is not None:
                page_clauses.append("t.status = ?")
                page_params.append(wanted[status_filter])
            rows = conn.execute(
                f"""
                SELECT {_TASK_LIST_COLUMNS}
                FROM tasks t WHERE {' AND '.join(page_clauses)}
                ORDER BY CASE t.status WHEN 'queued' THEN 0 WHEN 'claimed' THEN 1 ELSE 2 END, t.id ASC
                LIMIT ? OFFSET ?
                """,
                (mid, *page_params, limit, offset),
            ).fetchall()
        return {
            "slug": slug,
            "total": counts[status_filter],
            "offset": offset,
            "limit": limit,
            "counts": counts,
            "tasks": [_public_task(t, loads_json(t["payload_json"], {})) for t in rows],
        }

    def _union_budget_summary(self, conn: Any, *, mission_row: Any) -> dict[str, Any]:
        """Year-by-year progress and the largest shortfalls reported so far, for the Union Budget project."""
        from aiwatchdogs.engines.union_budget import _YEAR_RE, shift_year

        mid = str(mission_row["id"])
        slug = str(mission_row["slug"])
        coverage = _project_coverage(slug)
        by_year = coverage.get("by_year") if isinstance(coverage.get("by_year"), dict) else {}
        financial_years = [y for y in coverage.get("financial_years") or [] if isinstance(y, str)]
        latest_budget = shift_year(max(financial_years), 1) if financial_years else ""
        years: dict[str, dict[str, Any]] = {}

        def _actuals_reason_for(year: str) -> str | None:
            if not latest_budget or not _YEAR_RE.match(year):
                return "not_published"
            return "unmatched" if shift_year(year, 2) <= latest_budget else "not_published"

        def year_row(year: str) -> dict[str, Any]:
            planned = by_year.get(year) if isinstance(by_year.get(year), dict) else {}
            lines = int(planned.get("lines") or 0)
            with_actuals = int(planned.get("with_actuals") or 0)
            compares = "actual" if with_actuals > 0 or not lines else "revised"
            return years.setdefault(
                year,
                {
                    "year": year,
                    "tasks": 0,
                    "done": 0,
                    "claimed": 0,
                    "open": 0,
                    "pct": 0.0,
                    "lines": lines,
                    "with_actuals": with_actuals,
                    "budget_estimate_cr": _safe_finite_float(planned.get("budget_estimate_crore"), 0.0),
                    "leads": 0,
                    "below_estimate_cr": 0.0,
                    "compares": compares,
                    "actuals_reason": _actuals_reason_for(year) if compares == "revised" else None,
                },
            )

        for year in coverage.get("financial_years") or []:
            if isinstance(year, str):
                year_row(year)
        # A task id carries its financial year: 'prj-ub-2019-20-d042-l12-03'.
        for r in conn.execute(
            "SELECT SUBSTR(id, 8, 7) AS yr, status, COUNT(*) AS c FROM tasks WHERE mission_id = ? "
            "AND task_type != 'peer_verify' AND status != 'retired' AND id LIKE 'prj-ub-%' GROUP BY yr, status",
            (mid,),
        ).fetchall():
            entry = year_row(str(r["yr"]))
            n = int(r["c"])
            entry["tasks"] += n
            st = str(r["status"] or "")
            if st == "completed":
                entry["done"] += n
            elif st == "claimed":
                entry["claimed"] += n
            elif st == "queued":
                entry["open"] += n
        live = (
            "mission_id = ? AND COALESCE(quarantined, 0) = 0 "
            "AND status NOT IN ('quarantined', 'retracted', 'retracted_false_positive')"
        )
        leads = verified = peer_review = 0
        below_total = 0.0
        for r in conn.execute(
            f"SELECT fiscal_year, status, COUNT(*) AS c, COALESCE(SUM(monetary_exposure_inr), 0) AS inr "
            f"FROM findings WHERE {live} GROUP BY fiscal_year, status",
            (mid,),
        ).fetchall():
            n = int(r["c"])
            crore = _safe_finite_float(r["inr"], 0.0) / 1e7
            leads += n
            below_total += crore
            st = str(r["status"] or "")
            if st == "verified":
                verified += n
            elif st in ("peer_review", "raw_lead"):
                peer_review += n
            year = str(r["fiscal_year"] or "").removeprefix("FY")
            if year in years:
                years[year]["leads"] += n
                years[year]["below_estimate_cr"] = round(years[year]["below_estimate_cr"] + crore, 2)
        for entry in years.values():
            entry["pct"] = round(entry["done"] / entry["tasks"] * 100.0, 2) if entry["tasks"] else 0.0

        top_leads: list[dict[str, Any]] = []
        for r in conn.execute(
            f"SELECT id, title, entity_name, fiscal_year, status, monetary_exposure_inr, anomaly_metrics_json "
            f"FROM findings WHERE {live} ORDER BY monetary_exposure_inr DESC, id ASC LIMIT 10",
            (mid,),
        ).fetchall():
            metrics = loads_json(r["anomaly_metrics_json"], {})
            metrics = metrics if isinstance(metrics, dict) else {}

            def figure(key: str) -> float | None:
                value = metrics.get(key)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                    return None
                return float(value)

            compare = str(metrics.get("compare") or "")
            lead_year = str(r["fiscal_year"] or "").removeprefix("FY")
            top_leads.append(
                {
                    "finding_id": str(r["id"]),
                    "title": str(r["title"] or ""),
                    "entity_name": str(r["entity_name"] or ""),
                    "fiscal_year": str(r["fiscal_year"] or ""),
                    "status": str(r["status"] or ""),
                    "budget_estimate_cr": figure("budget_estimate"),
                    "revised_estimate_cr": figure("revised_estimate"),
                    "actual_cr": figure("actual"),
                    "below_estimate_cr": round(_safe_finite_float(r["monetary_exposure_inr"], 0.0) / 1e7, 2),
                    "below_estimate_pct": figure("unspent_pct" if compare != "revised_vs_budget_estimate" else "revised_cut_pct"),
                    "compare": compare,
                    "actuals_reason": _actuals_reason_for(lead_year) if compare == "revised_vs_budget_estimate" else None,
                }
            )
        return {
            "lines": int(coverage.get("lines") or 0),
            "lines_with_actuals": int(coverage.get("lines_with_actuals") or 0),
            "lines_alone": int(coverage.get("lines_alone") or 0),
            "alone_from_crore": _safe_finite_float(coverage.get("alone_from_crore"), 0.0),
            "set_size": int(coverage.get("set_size") or 0),
            "statements": int(coverage.get("statements_used") or 0),
            "financial_years": [y for y in coverage.get("financial_years") or [] if isinstance(y, str)],
            "generated_on": str(coverage.get("generated_on") or ""),
            "leads": leads,
            "verified": verified,
            "peer_review": peer_review,
            "cleared_tasks": int(mission_row["cleared_count"] or 0),
            "below_estimate_cr": round(below_total, 2),
            "years": [years[y] for y in sorted(years)],
            "top_leads": top_leads,
            "documents_url": f"/api/missions/{slug}/documents.csv",
            "coverage_url": f"/api/missions/{slug}/coverage.json",
        }

    def list_findings(
        self,
        *,
        status_filter: str | None = None,
        pillar: str | None = None,
        methodology: str | None = None,
        mission: str | None = None,
        search: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        eff_limit = max(1, min(int(limit), 500))
        eff_offset = max(0, min(int(offset), 50_000))
        clauses: list[str] = []
        params: list[Any] = []

        if status_filter and status_filter != "all":
            if status_filter == "quarantined":
                clauses.append("(COALESCE(quarantined, 0) = 1 OR status = 'quarantined')")
            elif status_filter == "corrected":
                clauses.append(
                    "COALESCE(quarantined, 0) = 0 AND status != 'quarantined' AND ("
                    "status = 'corrected' OR editorial_status IN ('corrected', 'retracted') "
                    "OR EXISTS (SELECT 1 FROM finding_corrections fc WHERE fc.finding_id = findings.id))"
                )
            elif status_filter == "retracted":
                clauses.append(
                    "status IN ('retracted', 'retracted_false_positive') AND COALESCE(quarantined, 0) = 0"
                )
            else:
                clauses.append("status = ? AND COALESCE(quarantined, 0) = 0")
                params.append(status_filter)
        else:
            clauses.append("COALESCE(quarantined, 0) = 0 AND status != 'quarantined'")

        if pillar and pillar != "all":
            clauses.append("pillar = ?")
            params.append(pillar)
        if methodology and methodology != "all":
            clauses.append("methodology_code = ?")
            params.append(methodology.upper())
        if mission:
            clauses.append("mission_slug = ?")
            params.append(self.resolve_slug(mission))
        if search and search.strip():
            escaped = escape_like(search.strip()[:120])
            like = f"%{escaped}%"
            clauses.append(
                "(COALESCE(quarantined, 0) = 0 AND ("
                "LOWER(title) LIKE LOWER(?) ESCAPE '\\' OR LOWER(summary) LIKE LOWER(?) ESCAPE '\\' "
                "OR LOWER(entity_name) LIKE LOWER(?) ESCAPE '\\' OR LOWER(jurisdiction) LIKE LOWER(?) ESCAPE '\\' "
                "OR LOWER(rule_triggered) LIKE LOWER(?) ESCAPE '\\'))"
            )
            params.extend([like, like, like, like, like])

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.db.read() as conn:
            total = int(conn.execute(f"SELECT COUNT(*) AS c FROM findings {where_sql}", params).fetchone()["c"])
            rows = conn.execute(
                f"""
                SELECT * FROM findings {where_sql}
                ORDER BY
                    CASE status
                        WHEN 'verified' THEN 0
                        WHEN 'peer_review' THEN 1
                        WHEN 'cleared' THEN 2
                        WHEN 'under_review' THEN 3
                        ELSE 4
                    END,
                    COALESCE(verified_at, created_at) DESC,
                    id ASC
                LIMIT ? OFFSET ?
                """,
                [*params, eff_limit, eff_offset],
            ).fetchall()
            row_fids = [str(r["id"]) for r in rows]
            rti_ids: set[str] = set()
            if row_fids:
                ph = ",".join("?" for _ in row_fids)
                rti_ids = {
                    str(r["finding_id"])
                    for r in conn.execute(
                        f"SELECT finding_id FROM rti_drafts WHERE status != 'quarantined' AND finding_id IN ({ph})",
                        row_fids,
                    ).fetchall()
                }
            corr_map = self._fetch_corrections_map(conn, row_fids)
            findings = [
                self._serialize_finding(
                    r,
                    has_rti_draft=(r["id"] in rti_ids),
                    corrections=corr_map.get(str(r["id"]), []),
                )
                for r in rows
            ]

        return {
            "findings": findings,
            "total": total,
            "limit": eff_limit,
            "offset": eff_offset,
            "has_more": (eff_offset + len(findings)) < total,
        }

    def list_graveyard(
        self,
        *,
        cause: str | None = None,
        project: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Every lead that did not survive (false positive, retracted, disputed, or withdrawn), newest death first."""
        valid_causes = {
            "false_positive": "False positive",
            "editorial_retraction": "Retracted",
            "peer_dispute": "Peer dispute",
            "withdrawn_review": "Withdrawn for review",
        }
        clean_cause = str(cause or "").strip().lower()
        if clean_cause and clean_cause not in valid_causes:
            raise HTTPException(
                status_code=422,
                detail=f"cause must be one of: {', '.join(sorted(valid_causes))}",
            )
        clean_project = str(project or "").strip()
        resolved_project = self.resolve_slug(clean_project) if clean_project else ""
        eff_limit = max(1, min(int(limit), 100))
        eff_offset = max(0, min(int(offset), 50_000))

        cause_statuses: dict[str, tuple[str, ...]] = {
            "false_positive": ("retracted_false_positive",),
            "editorial_retraction": ("retracted",),
            "peer_dispute": ("disputed",),
            "withdrawn_review": ("under_review", "under_editorial_review", "corrected"),
        }
        graveyard_statuses = tuple(s for group in cause_statuses.values() for s in group)
        placeholders = ",".join("?" for _ in graveyard_statuses)
        page_statuses = cause_statuses[clean_cause] if clean_cause else graveyard_statuses
        page_placeholders = ",".join("?" for _ in page_statuses)

        def _parse_iso(val: Any) -> float | None:
            raw = str(val or "").strip()
            if not raw:
                return None
            try:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.timestamp()
            except ValueError:
                return None

        def _classify_cause(st: str) -> str:
            if st == "retracted_false_positive":
                return "false_positive"
            if st == "retracted":
                return "editorial_retraction"
            if st == "disputed":
                return "peer_dispute"
            return "withdrawn_review"

        with self.db.read() as conn:
            mission_rows = conn.execute(
                "SELECT slug, title, is_community_project, status, priority, created_at "
                "FROM missions ORDER BY priority ASC, created_at ASC"
            ).fetchall()
            mission_titles = {str(m["slug"]): str(m["title"]) for m in mission_rows}
            project_list: list[dict[str, str]] = [
                {"slug": str(m["slug"]), "title": str(m["title"])}
                for m in mission_rows
                if int(m["is_community_project"] or 0) == 1 and str(m["status"] or "") != "archived"
            ]
            listed_slugs = {p["slug"] for p in project_list}

            for srow in conn.execute(
                f"""
                SELECT DISTINCT mission_slug
                FROM findings
                WHERE COALESCE(quarantined, 0) = 0
                  AND status != 'quarantined'
                  AND status IN ({placeholders})
                """,
                graveyard_statuses,
            ).fetchall():
                mslug = str(srow["mission_slug"] or "")
                if mslug and mslug not in listed_slugs:
                    project_list.append({"slug": mslug, "title": mission_titles.get(mslug, mslug)})
                    listed_slugs.add(mslug)

            count_params: list[Any] = list(graveyard_statuses)
            project_clause = ""
            if resolved_project:
                project_clause = " AND mission_slug = ?"
                count_params.append(resolved_project)

            counts_by_cause: dict[str, int] = {"all": 0, **{k: 0 for k in valid_causes}}
            for crow in conn.execute(
                f"""
                SELECT LOWER(status) AS st, COUNT(*) AS c
                FROM findings
                WHERE COALESCE(quarantined, 0) = 0
                  AND status != 'quarantined'
                  AND status IN ({placeholders}){project_clause}
                GROUP BY LOWER(status)
                """,
                count_params,
            ).fetchall():
                c_val = int(crow["c"] or 0)
                c_key = _classify_cause(str(crow["st"] or ""))
                counts_by_cause[c_key] = counts_by_cause.get(c_key, 0) + c_val
                counts_by_cause["all"] += c_val

            total = counts_by_cause[clean_cause] if clean_cause else counts_by_cause["all"]

            page_params: list[Any] = list(page_statuses)
            f_project_clause = ""
            if resolved_project:
                f_project_clause = " AND f.mission_slug = ?"
                page_params.append(resolved_project)
            page_params.extend([eff_limit, eff_offset])

            rows = conn.execute(
                f"""
                SELECT * FROM (
                    SELECT f.id, f.mission_slug, f.title, f.summary, f.methodology_code, f.entity_name,
                           f.jurisdiction, f.fiscal_year, f.monetary_exposure_inr, f.status,
                           f.editorial_status, f.confirm_count, f.dispute_count, f.created_at,
                           (SELECT MAX(c.created_at) FROM finding_corrections c
                            WHERE c.finding_id = f.id AND COALESCE(c.created_at, '') != '') AS corr_at,
                           (SELECT MAX(v.created_at) FROM peer_verifications v
                            WHERE v.finding_id = f.id
                              AND LOWER(COALESCE(v.verdict, '')) != 'confirm'
                              AND COALESCE(v.created_at, '') != '') AS disp_at
                    FROM findings f
                    WHERE COALESCE(f.quarantined, 0) = 0
                      AND f.status != 'quarantined'
                      AND f.status IN ({page_placeholders}){f_project_clause}
                ) AS sub
                ORDER BY
                    CASE
                        WHEN sub.corr_at IS NOT NULL AND sub.disp_at IS NOT NULL THEN
                            CASE WHEN sub.corr_at >= sub.disp_at THEN sub.corr_at ELSE sub.disp_at END
                        ELSE COALESCE(sub.corr_at, sub.disp_at, sub.created_at)
                    END DESC,
                    sub.id DESC
                LIMIT ? OFFSET ?
                """,
                page_params,
            ).fetchall()

            fids = [str(r["id"]) for r in rows]
            corr_map = self._fetch_corrections_map(conn, fids)
            verifs_map: dict[str, list[Any]] = {}
            rti_map: dict[str, str] = {}
            if fids:
                ph = ",".join("?" for _ in fids)
                for vr in conn.execute(
                    f"""
                    SELECT finding_id, verifier_handle, verdict, checks_json, notes, created_at
                    FROM peer_verifications
                    WHERE finding_id IN ({ph})
                    ORDER BY created_at DESC, id DESC
                    """,
                    fids,
                ).fetchall():
                    verifs_map.setdefault(str(vr["finding_id"]), []).append(vr)
                for rr in conn.execute(
                    f"SELECT finding_id, status, editorial_notice FROM rti_drafts "
                    f"WHERE status != 'quarantined' AND finding_id IN ({ph})",
                    fids,
                ).fetchall():
                    rti_map[str(rr["finding_id"])] = str(rr["editorial_notice"] or rr["status"] or "")

        page_items: list[dict[str, Any]] = []
        for r in rows:
            fid = str(r["id"])
            st = str(r["status"] or "").lower()
            entry_cause = _classify_cause(st)
            mslug = str(r["mission_slug"] or "")

            corrs = corr_map.get(fid, [])
            verifs = verifs_map.get(fid, [])
            non_confirm_verifs = [v for v in verifs if str(v["verdict"] or "").lower() != "confirm"]

            proposed_at = str(r["created_at"] or "")
            ended_candidates = [str(c.get("created_at") or "") for c in corrs if c.get("created_at")]
            ended_candidates.extend(str(v["created_at"] or "") for v in non_confirm_verifs if v["created_at"])
            ended_at = max(ended_candidates) if ended_candidates else proposed_at

            start_ts = _parse_iso(proposed_at)
            end_ts = _parse_iso(ended_at)
            lived_seconds = max(0, int(end_ts - start_ts)) if (start_ts is not None and end_ts is not None) else 0

            latest_corr = corrs[-1] if corrs else None
            source_evidence_url: str | None = None
            if latest_corr is not None:
                raw_ev = latest_corr.get("source_evidence_url")
                if isinstance(raw_ev, str) and raw_ev.strip():
                    try:
                        source_evidence_url = validate_safe_http_url(raw_ev.strip())
                    except ValueError:
                        source_evidence_url = None
                corr_summary = str(latest_corr.get("summary") or "").strip()
                corr_details = str(latest_corr.get("details") or "").strip()
                reviewer = str(latest_corr.get("reviewed_by") or "editorial-review").strip()
                cause_detail = corr_summary or corr_details or "Retracted after independent editorial review."
                if reviewer and reviewer not in cause_detail:
                    cause_detail = f"{cause_detail} (reviewed by {reviewer})"
            elif non_confirm_verifs:
                notes_list = [str(v["notes"] or "").strip() for v in non_confirm_verifs if str(v["notes"] or "").strip()]
                verdicts_str = ", ".join(
                    sorted({str(v["verdict"] or "").replace("_", " ") for v in non_confirm_verifs})
                )
                if notes_list:
                    cause_detail = notes_list[0]
                else:
                    cause_detail = (
                        f"Ended in independent peer verification ({len(non_confirm_verifs)} non-confirm review(s): "
                        f"{verdicts_str})."
                    )
            elif entry_cause == "false_positive":
                cause_detail = "Independent peer verifiers re-ran the deterministic check and found zero breach."
            elif entry_cause == "peer_dispute":
                cause_detail = "Disputed during independent peer verification and excluded from verified results."
            elif entry_cause == "editorial_retraction":
                cause_detail = "Retracted from the public record after review."
            else:
                cause_detail = "Withdrawn from verified results for editorial reconciliation."

            confirm_n = int(r["confirm_count"] or 0)
            dispute_n = int(r["dispute_count"] or 0)
            rti_notice = rti_map.get(fid)
            rti_on_hold = fid in rti_map and ("hold" in (rti_notice or "").lower() or st in ("retracted", "under_review", "disputed"))
            if rti_on_hold:
                survived = "RTI draft placed on editorial hold. Recorded so nobody files or repeats it."
            elif verifs or confirm_n or dispute_n:
                total_reviews = max(len(verifs), confirm_n + dispute_n)
                survived = (
                    f"Peer checks: {total_reviews} independent review(s) "
                    f"({confirm_n} confirmed, {dispute_n} disputed). Recorded so nobody repeats it."
                )
            else:
                survived = "0 verified findings rest on it. Recorded so nobody repeats it."

            page_items.append(
                {
                    "id": fid,
                    "title": str(r["title"] or ""),
                    "status": st,
                    "cause": entry_cause,
                    "cause_label": valid_causes[entry_cause],
                    "methodology_code": str(r["methodology_code"] or ""),
                    "entity_name": str(r["entity_name"] or ""),
                    "jurisdiction": str(r["jurisdiction"] or ""),
                    "fiscal_year": str(r["fiscal_year"] or ""),
                    "monetary_exposure_inr_cr": round(_safe_finite_float(r["monetary_exposure_inr"], 0.0) / 1e7, 2),
                    "mission_slug": mslug,
                    "project_title": mission_titles.get(mslug, mslug or "Civic Audit"),
                    "proposed_at": proposed_at,
                    "ended_at": ended_at,
                    "lived_seconds": lived_seconds,
                    "claimed": str(r["summary"] or ""),
                    "cause_detail": cause_detail,
                    "source_evidence_url": source_evidence_url,
                    "survived": survived,
                    "confirm_count": confirm_n,
                    "dispute_count": dispute_n,
                    "corrections_count": len(corrs),
                    "rti_on_hold": rti_on_hold,
                }
            )

        return {
            "total": total,
            "offset": eff_offset,
            "limit": eff_limit,
            "has_more": (eff_offset + len(page_items)) < total,
            "counts_by_cause": counts_by_cause,
            "projects": project_list,
            "entries": page_items,
        }

    def get_finding_detail(self, finding_id: str) -> dict[str, Any]:
        with self.db.read() as conn:
            row = conn.execute("SELECT * FROM findings WHERE id = ?", (finding_id,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail=f"Finding '{finding_id}' not found")
            corr_rows = conn.execute(
                "SELECT * FROM finding_corrections WHERE finding_id = ? ORDER BY created_at ASC, id ASC",
                (finding_id,),
            ).fetchall()
            corrections = [self._serialize_correction(c) for c in corr_rows]
            mis_row = conn.execute("SELECT * FROM missions WHERE id = ?", (row["mission_id"],)).fetchone()
            mission_ref = (
                {"id": mis_row["id"], "slug": mis_row["slug"], "title": mis_row["title"]}
                if mis_row is not None
                else None
            )

            # Strict quarantine boundary: NEVER leak raw metrics, trace, text, citations, or artifact
            if _is_quarantined_row(row):
                redacted = self._serialize_quarantined_finding(row, corrections=corrections)
                return {
                    **redacted,
                    "corrections": corrections,
                    "verifications": [],
                    "reproduction_summary": {
                        "computational_confirm_count": 0,
                        "editorial_review_count": 0,
                        "required_quorum": int(row["required_quorum"] or 2),
                    },
                    "rti_draft": None,
                    "artifact": None,
                    "mission": mission_ref,
                }

            verif_rows = conn.execute(
                "SELECT * FROM peer_verifications WHERE finding_id = ? ORDER BY created_at ASC",
                (finding_id,),
            ).fetchall()
            rti_row = conn.execute(
                "SELECT * FROM rti_drafts WHERE finding_id = ? AND status != 'quarantined'", (finding_id,)
            ).fetchone()
            art_row = (
                conn.execute("SELECT * FROM evidence_artifacts WHERE id = ?", (row["artifact_id"],)).fetchone()
                if row["artifact_id"]
                else None
            )

            finding = self._serialize_finding(
                row, has_rti_draft=(rti_row is not None), corrections=corrections
            )
            verifications = [self._serialize_verification(v) for v in verif_rows]
            rti_draft = self._serialize_rti(rti_row) if rti_row is not None else None
            artifact = None
            if art_row is not None:
                art_sha_raw = str(art_row["sha256"] or "").strip()
                parsed_meta = loads_json(art_row["parsed_metadata_json"], {})
                if not isinstance(parsed_meta, dict):
                    parsed_meta = {}
                artifact = {
                    "id": art_row["id"],
                    "source_id": art_row["source_id"],
                    "title": art_row["title"],
                    "official_publisher": _row_get(art_row, "official_publisher", "")
                    or parsed_meta.get("official_publisher", ""),
                    "source_landing_page": _row_get(art_row, "source_landing_page", "")
                    or parsed_meta.get("source_landing_page")
                    or parsed_meta.get("source_url", ""),
                    "mirror_name": _row_get(art_row, "mirror_name", "")
                    or parsed_meta.get("mirror_name", ""),
                    "mirror_catalog_url": parsed_meta.get("mirror_catalog_url", ""),
                    "retrieval_url": _row_get(art_row, "retrieval_url", "") or art_row["canonical_url"],
                    "canonical_url": art_row["canonical_url"],
                    "archive_url": art_row["archive_url"],
                    "secondary_url": _row_get(art_row, "secondary_url", None)
                    or parsed_meta.get("secondary_url"),
                    "secondary_sha256": _row_get(art_row, "secondary_sha256", None)
                    or parsed_meta.get("secondary_sha256"),
                    "local_path": (
                        art_row["local_path"]
                        if str(art_row["local_path"] or "").startswith(("http://", "https://"))
                        else None
                    ),
                    "file_format": art_row["file_format"],
                    "sha256": art_sha_raw if art_sha_raw else None,
                    "byte_size": int(art_row["byte_size"] or 0),
                    "is_demo_sample": bool(art_row["is_demo_sample"]),
                    "pii_scrubbed": bool(art_row["pii_scrubbed"]),
                    "fetched_at": art_row["fetched_at"],
                }

            comp_confirms = sum(
                1
                for v in verifications
                if v["review_type"] in ("computational_reproduction", "adjudication")
                and v["verdict"] == "confirm"
                and v["all_checks_passed"]
                and bool(v["reproduced_metrics"])
            )
            editorial_reviews = sum(
                1 for v in verifications if v["review_type"] == "editorial_review"
            ) + len(corrections)

        return {
            **finding,
            "corrections": corrections,
            "verifications": verifications,
            "reproduction_summary": {
                "computational_confirm_count": comp_confirms,
                "editorial_review_count": editorial_reviews,
                "required_quorum": int(row["required_quorum"] or 2),
            },
            "rti_draft": rti_draft,
            "artifact": artifact,
            "mission": mission_ref,
        }

    def record_editorial_correction(
        self,
        finding_id: str,
        *,
        correction_type: str,
        new_status: str,
        editorial_status: str,
        summary: str,
        details: str = "",
        source_evidence_url: str | None = None,
        reviewed_by: str = "editorial-review",
        rti_editorial_notice: str | None = None,
    ) -> dict[str, Any]:
        """Record a transparent editorial correction notice and update finding/RTI disposition."""
        envelopes: list[dict[str, Any]] = []
        now = utc_now_iso()
        cor_id = f"cor-{secrets.token_hex(6)}"

        with self.db.transaction() as conn:
            lock_clause = " FOR UPDATE" if self.db.is_postgres else ""
            row = conn.execute(
                f"SELECT * FROM findings WHERE id = ?{lock_clause}", (finding_id,)
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail=f"Finding '{finding_id}' not found")
            prev_status = str(row["status"] or "")
            clean_summary, _ = scrub_pii(summary.strip())
            clean_details, _ = scrub_pii(details.strip())
            safe_evidence_url = _validate_optional_url_422(
                source_evidence_url, field="source_evidence_url", require_https=False
            )

            conn.execute(
                """
                INSERT INTO finding_corrections (
                    id, finding_id, correction_type, previous_status, new_status,
                    summary, details, source_evidence_url, reviewed_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cor_id,
                    finding_id,
                    correction_type,
                    prev_status,
                    new_status,
                    clean_summary,
                    clean_details,
                    safe_evidence_url,
                    reviewed_by,
                    now,
                ),
            )
            is_quarantine = 1 if new_status == "quarantined" else int(_row_get(row, "quarantined", 0))
            conn.execute(
                """
                UPDATE findings
                SET status = ?, editorial_status = ?, quarantined = ?
                WHERE id = ?
                """,
                (new_status, editorial_status, is_quarantine, finding_id),
            )
            if is_quarantine:
                self._redact_quarantined_finding_artifacts(conn, finding_id, now)

            # Lock mission row on Postgres before recomputing verified count to avoid concurrent overwrite races
            if self.db.is_postgres:
                conn.execute("SELECT id FROM missions WHERE id = ? FOR UPDATE", (row["mission_id"],))
            cnt_row = conn.execute(
                """
                SELECT COUNT(*) AS c FROM findings
                WHERE mission_id = ? AND status IN ('verified', 'rti_filed') AND COALESCE(quarantined, 0) = 0
                """,
                (row["mission_id"],),
            ).fetchone()
            conn.execute(
                "UPDATE missions SET verified_findings_count = ? WHERE id = ?",
                (int(cnt_row["c"]) if cnt_row else 0, row["mission_id"]),
            )

            # Update shared RTI draft & append version snapshot if present
            rti_row = conn.execute("SELECT * FROM rti_drafts WHERE finding_id = ?", (finding_id,)).fetchone()
            if rti_row is not None:
                rti_status = "quarantined" if is_quarantine else "editorial_hold"
                notice = rti_editorial_notice or f"EDITORIAL NOTICE: {clean_summary}"
                next_ver = int(_row_get(rti_row, "version", 1) or 1) + 1
                conn.execute(
                    """
                    UPDATE rti_drafts
                    SET status = ?, editorial_notice = ?, version = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (rti_status, notice, next_ver, now, rti_row["id"]),
                )
                conn.execute(
                    """
                    INSERT INTO rti_draft_versions (
                        id, rti_draft_id, finding_id, version, public_authority, cpio_designation,
                        rti_portal_url, jurisdiction_level, filing_channel, subject, body_text,
                        word_count, requested_records_json, status, editorial_notice, change_note, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"rtiv-{secrets.token_hex(6)}",
                        rti_row["id"],
                        finding_id,
                        next_ver,
                        rti_row["public_authority"],
                        rti_row["cpio_designation"],
                        rti_row["rti_portal_url"] or "",
                        _row_get(rti_row, "jurisdiction_level", "central"),
                        _row_get(rti_row, "filing_channel", "central_online_portal"),
                        rti_row["subject"],
                        rti_row["body_text"],
                        int(rti_row["word_count"] or 0),
                        rti_row["requested_records_json"] or "[]",
                        rti_status,
                        notice,
                        clean_summary,
                        now,
                    ),
                )

            self._emit_durable_event(
                conn,
                envelopes,
                "finding_corrected",
                {
                    "finding_id": finding_id,
                    "correction_id": cor_id,
                    "correction_type": correction_type,
                    "previous_status": prev_status,
                    "new_status": new_status,
                    "editorial_status": editorial_status,
                },
                now=now,
            )
            saved = conn.execute("SELECT * FROM finding_corrections WHERE id = ?", (cor_id,)).fetchone()
            result = self._serialize_correction(saved)

        self._broadcast_envelopes(envelopes)
        return result

    @staticmethod
    def _build_rti_preview_response(
        finding_id: str,
        draft: dict[str, Any],
        *,
        status_label: str,
        editorial_notice: str = "",
    ) -> dict[str, Any]:
        fee_note = str(draft.get("fee_rules") or draft.get("fee_note") or "")
        return {
            "id": f"preview-rti-{finding_id}",
            "finding_id": finding_id,
            "version": 0,
            "public_authority": draft["public_authority"],
            "cpio_designation": draft["cpio_designation"],
            "rti_portal_url": draft.get("rti_portal_url") or "",
            "jurisdiction_level": draft.get("jurisdiction_level", "central"),
            "state_name": draft.get("state_name"),
            "filing_channel": draft.get("filing_channel", "central_online_portal"),
            "fee_note": fee_note,
            "fee_rules": fee_note,
            "central_portal_warning": str(draft.get("central_portal_warning") or ""),
            "human_filing_required": True,
            "statutory_sections": list(draft.get("statutory_sections") or []),
            "subject": draft["subject"],
            "body_text": draft["body_text"],
            "word_count": int(draft["word_count"]),
            "requested_records": list(draft.get("requested_records") or []),
            "status": status_label,
            "editorial_notice": editorial_notice,
            "is_preview": True,
            "persisted": False,
            "filed_date": None,
            "due_date_30d": None,
            "created_at": utc_now_iso(),
            "updated_at": None,
        }

    def create_or_get_rti_draft(
        self,
        finding_id: str,
        public_authority: str | None = None,
        *,
        preview: bool | None = None,
    ) -> dict[str, Any]:
        """Return shared versioned RTI draft for verified findings, or personal non-mutating preview.

        - Quarantined findings return HTTP 403.
        - Unverified, under-review, or retracted findings NEVER mutate or create a shared
          ``rti_drafts`` row; they return a clearly badged personal preview (or existing
          editorial-hold draft when no custom override is requested).
        - Supplying a custom ``public_authority`` (or ``preview=True``) returns a personal
          non-mutating preview instead of silently overwriting the shared published draft.
        """
        custom_authority = (public_authority or "").strip()[:200] or None
        envelopes: list[dict[str, Any]] = []

        # Fast read-only path: previews and existing drafts NEVER open a DB write transaction!
        with self.db.read() as conn:
            row = conn.execute("SELECT * FROM findings WHERE id = ?", (finding_id,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail=f"Finding '{finding_id}' not found")
            if _is_quarantined_row(row):
                raise HTTPException(
                    status_code=403,
                    detail="RTI draft unavailable: finding is quarantined pending privacy/legal review.",
                )

            existing = conn.execute("SELECT * FROM rti_drafts WHERE finding_id = ?", (finding_id,)).fetchone()
            finding_status = str(row["status"] or "")
            is_verified = finding_status in ("verified", "rti_filed")

            if not is_verified:
                if existing is not None and not custom_authority and preview is not True:
                    return self._serialize_rti(existing, is_preview=False, persisted=True)
                finding_dict = self._serialize_finding(row)
                draft = generate_rti_draft(finding_dict, public_authority=custom_authority)
                notice = (
                    f"Personal preview only (finding status: {finding_status}). "
                    "Shared RTI drafts are published only for independently 2-of-2 verified leads."
                )
                if existing is not None and _row_get(existing, "editorial_notice", ""):
                    notice = str(existing["editorial_notice"])
                return self._build_rti_preview_response(
                    finding_id,
                    draft,
                    status_label="preview_unverified" if finding_status == "peer_review" else "preview_editorial_hold",
                    editorial_notice=notice,
                )

            if custom_authority or preview is True:
                finding_dict = self._serialize_finding(row)
                draft = generate_rti_draft(finding_dict, public_authority=custom_authority)
                return self._build_rti_preview_response(
                    finding_id,
                    draft,
                    status_label="personal_preview",
                    editorial_notice="",
                )

            if existing is not None:
                return self._serialize_rti(existing, is_preview=False, persisted=True)

        # Write transaction ONLY when creating a new persisted v1 shared draft for a verified finding.
        with self.db.transaction() as conn:
            lock_clause = " FOR UPDATE" if self.db.is_postgres else ""
            row = conn.execute(
                f"SELECT * FROM findings WHERE id = ?{lock_clause}", (finding_id,)
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail=f"Finding '{finding_id}' not found")
            if _is_quarantined_row(row):
                raise HTTPException(
                    status_code=403,
                    detail="RTI draft unavailable: finding is quarantined pending privacy/legal review.",
                )
            existing = conn.execute("SELECT * FROM rti_drafts WHERE finding_id = ?", (finding_id,)).fetchone()
            if existing is not None:
                return self._serialize_rti(existing, is_preview=False, persisted=True)
            if str(row["status"] or "") not in ("verified", "rti_filed"):
                finding_dict = self._serialize_finding(row)
                draft = generate_rti_draft(finding_dict)
                return self._build_rti_preview_response(
                    finding_id,
                    draft,
                    status_label="preview_editorial_hold",
                    editorial_notice="Personal preview only (finding status changed concurrently).",
                )

            finding_dict = self._serialize_finding(row)
            draft = generate_rti_draft(finding_dict)
            now = utc_now_iso()
            draft_id = f"rti-{finding_id}"
            fee_note = str(draft.get("fee_rules") or draft.get("fee_note") or "")
            jlevel = str(draft.get("jurisdiction_level") or "central")
            sname = draft.get("state_name")
            fchan = str(draft.get("filing_channel") or "central_online_portal")
            cwarn = str(draft.get("central_portal_warning") or "")

            try:
                conn.execute(
                    """
                    INSERT INTO rti_drafts (
                        id, finding_id, version, public_authority, cpio_designation,
                        rti_portal_url, jurisdiction_level, state_name, filing_channel,
                        fee_note, central_portal_warning, statutory_sections_json,
                        subject, body_text, word_count, requested_records_json,
                        status, editorial_notice, created_at, updated_at
                    ) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', '', ?, ?)
                    """,
                    (
                        draft_id,
                        finding_id,
                        draft["public_authority"],
                        draft["cpio_designation"],
                        draft.get("rti_portal_url") or "",
                        jlevel,
                        sname,
                        fchan,
                        fee_note,
                        cwarn,
                        dumps_json(draft["statutory_sections"]),
                        draft["subject"],
                        draft["body_text"],
                        draft["word_count"],
                        dumps_json(draft["requested_records"]),
                        now,
                        now,
                    ),
                )
            except DB_INTEGRITY_ERRORS:
                raced = conn.execute("SELECT * FROM rti_drafts WHERE finding_id = ?", (finding_id,)).fetchone()
                if raced is not None:
                    return self._serialize_rti(raced, is_preview=False, persisted=True)
                raise
            conn.execute(
                """
                INSERT INTO rti_draft_versions (
                    id, rti_draft_id, finding_id, version, public_authority, cpio_designation,
                    rti_portal_url, jurisdiction_level, filing_channel, subject, body_text,
                    word_count, requested_records_json, status, editorial_notice, change_note, created_at
                ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', '', 'Initial verified RTI draft', ?)
                """,
                (
                    f"rtiv-{secrets.token_hex(6)}",
                    draft_id,
                    finding_id,
                    draft["public_authority"],
                    draft["cpio_designation"],
                    draft.get("rti_portal_url") or "",
                    jlevel,
                    fchan,
                    draft["subject"],
                    draft["body_text"],
                    draft["word_count"],
                    dumps_json(draft["requested_records"]),
                    now,
                ),
            )
            saved = conn.execute("SELECT * FROM rti_drafts WHERE id = ?", (draft_id,)).fetchone()
            result = self._serialize_rti(saved, is_preview=False, persisted=True)
            self._emit_durable_event(
                conn,
                envelopes,
                "rti_drafted",
                {
                    "finding_id": finding_id,
                    "rti_draft_id": result["id"],
                    "public_authority": result["public_authority"],
                    "word_count": result["word_count"],
                    "version": result["version"],
                },
                now=now,
            )

        self._broadcast_envelopes(envelopes)
        return result

    def get_graph(self) -> dict[str, Any]:
        """Build structured Observatory graph: Authorities -> Missions -> Findings -> Verified Sources."""
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        seen_nodes: set[str] = set()
        seen_edges: set[tuple[str, str, str]] = set()

        def add_node(node: dict[str, Any]) -> None:
            if node["id"] not in seen_nodes:
                seen_nodes.add(node["id"])
                nodes.append(node)

        def add_edge(source: str, target: str, relation: str) -> None:
            key = (source, target, relation)
            if key not in seen_edges:
                seen_edges.add(key)
                edges.append({"source": source, "target": target, "relation": relation})

        with self.db.read() as conn:
            missions = self._fetch_missions(conn)
            sources_by_id = {
                r["id"]: self._serialize_source(r)
                for r in conn.execute("SELECT * FROM public_sources").fetchall()
            }
            finding_rows = conn.execute(
                "SELECT * FROM findings WHERE COALESCE(quarantined, 0) = 0 AND status != 'quarantined' "
                "ORDER BY COALESCE(verified_at, created_at) DESC LIMIT 40"
            ).fetchall()
            art_ids = sorted({str(r["artifact_id"]) for r in finding_rows if r["artifact_id"]})
            artifact_sources: dict[str, str | None] = {}
            if art_ids:
                ph = ",".join("?" for _ in art_ids)
                artifact_sources = {
                    str(r["id"]): r["source_id"]
                    for r in conn.execute(
                        f"SELECT id, source_id FROM evidence_artifacts WHERE id IN ({ph})", art_ids
                    ).fetchall()
                }

        authority_map: dict[str, str] = {}

        for m in missions:
            auth_label = m["ministry_or_authority"]
            auth_id = authority_map.get(auth_label)
            if not auth_id:
                auth_id = f"auth-{len(authority_map) + 1:02d}"
                authority_map[auth_label] = auth_id
                add_node(
                    {
                        "id": auth_id,
                        "type": "ministry",
                        "label": auth_label,
                        "status": "active",
                        "pillar": m["pillar"],
                        "value_inr_cr": m["unique_coverage_inr_cr"],
                        "meta": {"jurisdiction": m["jurisdiction"]},
                    }
                )

            m_node_id = f"mission:{m['slug']}"
            add_node(
                {
                    "id": m_node_id,
                    "type": "mission",
                    "label": m["title"],
                    "status": m["status"],
                    "pillar": m["pillar"],
                    "value_inr_cr": m["unique_coverage_inr_cr"],
                    "meta": {
                        "slug": m["slug"],
                        "pillar": m["pillar"],
                        "methodology_code": m["methodology_code"],
                        "progress_pct": m["progress_pct"],
                        "verified_findings_count": m["verified_findings_count"],
                        "is_demo_sample": m["is_demo_sample"],
                        "financial_quantity_type": m["financial_quantity_type"],
                    },
                }
            )
            add_edge(auth_id, m_node_id, "oversees")

            for sid in m["source_ids"]:
                src = sources_by_id.get(sid)
                if src:
                    s_node_id = f"source:{src['slug']}"
                    add_node(
                        {
                            "id": s_node_id,
                            "type": "source",
                            "label": src["name"],
                            "status": f"tier_{src['ingestion_tier']}",
                            "pillar": src["pillar"],
                            "value_inr_cr": 0.0,
                            "meta": {
                                "base_url": src["base_url"],
                                "official_publisher": src["official_publisher"],
                                "source_landing_page": src["source_landing_page"],
                                "mirror_name": src["mirror_name"],
                                "ingestion_tier": src["ingestion_tier"],
                                "connector_type": src["connector_type"],
                            },
                        }
                    )
                    add_edge(m_node_id, s_node_id, "ingests_from")

        active_mission_ids = {m["id"] for m in missions}
        for f_row in finding_rows:
            if _row_get(f_row, "mission_id") not in active_mission_ids:
                continue  # findings of archived missions stay on the record, off the live map
            f = self._serialize_finding(f_row)
            f_node_id = f"finding:{f['id']}"
            submitted_by = str(f["submitted_by"] or "").strip()
            is_community = bool(submitted_by)
            add_node(
                {
                    "id": f_node_id,
                    "type": "finding",
                    "label": f["title"],
                    "status": f["status"],
                    "pillar": f["pillar"],
                    "value_inr_cr": f["monetary_exposure_inr_cr"],
                    "meta": {
                        "finding_id": f["id"],
                        "mission_slug": f["mission_slug"],
                        "submitted_by": submitted_by,
                        "is_community": is_community,
                        "monetary_exposure_inr_cr": f["monetary_exposure_inr_cr"],
                        "methodology_code": f["methodology_code"],
                        "entity_name": f["entity_name"],
                        "severity": f["severity"],
                        "confirm_count": f["confirm_count"],
                        "editorial_status": f["editorial_status"],
                        "is_demo_sample": f["is_demo_sample"],
                    },
                }
            )
            add_edge(f"mission:{f['mission_slug']}", f_node_id, "surfaced")

            # Link finding ONLY to its verified evidence source(s) — never blindly mission.source_ids[0]
            linked_source_ids: list[str] = []
            direct_sid = f.get("source_id")
            if direct_sid and direct_sid in sources_by_id:
                linked_source_ids.append(str(direct_sid))
            art_id = f.get("artifact_id")
            if art_id:
                art_sid = artifact_sources.get(str(art_id))
                if art_sid and art_sid in sources_by_id and art_sid not in linked_source_ids:
                    linked_source_ids.append(str(art_sid))
            prov = f.get("provenance") if isinstance(f.get("provenance"), dict) else {}
            sec_sid = prov.get("secondary_source_id")
            if sec_sid and sec_sid in sources_by_id and sec_sid not in linked_source_ids:
                linked_source_ids.append(str(sec_sid))

            for sid in linked_source_ids:
                src = sources_by_id[sid]
                s_node_id = f"source:{src['slug']}"
                add_node(
                    {
                        "id": s_node_id,
                        "type": "source",
                        "label": src["name"],
                        "status": f"tier_{src['ingestion_tier']}",
                        "pillar": src["pillar"],
                        "value_inr_cr": 0.0,
                        "meta": {
                            "base_url": src["base_url"],
                            "official_publisher": src["official_publisher"],
                            "source_landing_page": src["source_landing_page"],
                            "mirror_name": src["mirror_name"],
                            "ingestion_tier": src["ingestion_tier"],
                            "connector_type": src["connector_type"],
                        },
                    }
                )
                add_edge(f_node_id, s_node_id, "evidenced_by")

        return {"nodes": nodes, "edges": edges}

    def get_live(self) -> dict[str, Any]:
        with self.db.read() as conn:
            all_time_scores, hourly_scores = self._fetch_score_maps(conn)
            total_agents = int(conn.execute("SELECT COUNT(*) AS c FROM agents").fetchone()["c"])
            agent_rows = conn.execute(
                "SELECT * FROM agents ORDER BY last_heartbeat_at DESC, compute_score DESC LIMIT 200"
            ).fetchall()
            agents = [
                self._serialize_agent(
                    r,
                    all_time_score=all_time_scores.get(r["handle"], int(r["compute_score"] or 0)),
                    hourly_event_score=hourly_scores.get(r["handle"], 0),
                )
                for r in agent_rows
            ]
            active_agents = [a for a in agents if a["is_active"]]

            whisper_rows = conn.execute(
                """
                SELECT w.* FROM whispers w
                WHERE w.finding_id IS NULL
                   OR NOT EXISTS (
                       SELECT 1 FROM findings f
                       WHERE f.id = w.finding_id
                         AND (COALESCE(f.quarantined, 0) = 1 OR f.status = 'quarantined')
                   )
                ORDER BY w.created_at DESC
                LIMIT 40
                """
            ).fetchall()
            whispers = [self._serialize_whisper(r) for r in whisper_rows]

            verif_rows = conn.execute(
                """
                SELECT pv.*, f.title AS finding_title, f.methodology_code, f.status AS finding_status
                FROM peer_verifications pv
                JOIN findings f ON f.id = pv.finding_id
                WHERE COALESCE(f.quarantined, 0) = 0 AND f.status != 'quarantined'
                ORDER BY pv.created_at DESC
                LIMIT 20
                """
            ).fetchall()
            recent_verifications = [
                {
                    **self._serialize_verification(r),
                    "finding_title": r["finding_title"],
                    "methodology_code": r["methodology_code"],
                    "finding_status": r["finding_status"],
                }
                for r in verif_rows
            ]

            breakdown_rows = conn.execute(
                """
                SELECT provider, model, COUNT(*) AS agent_count,
                       COALESCE(SUM(tasks_completed), 0) AS tasks_completed,
                       COALESCE(SUM(compute_score), 0) AS compute_score
                FROM agents
                GROUP BY provider, model
                ORDER BY tasks_completed DESC, agent_count DESC
                """
            ).fetchall()
            total_tasks = sum(int(r["tasks_completed"] or 0) for r in breakdown_rows) or 1
            model_breakdown = [
                {
                    "provider": r["provider"],
                    "model": r["model"],
                    "agent_count": int(r["agent_count"]),
                    "tasks_completed": int(r["tasks_completed"]),
                    "compute_score": int(r["compute_score"]),
                    "share_pct": round((int(r["tasks_completed"]) / total_tasks) * 100.0, 1),
                }
                for r in breakdown_rows
            ]
            latest_ev = conn.execute("SELECT COALESCE(MAX(id), 0) AS max_id FROM events").fetchone()
            latest_event_id = int(latest_ev["max_id"]) if latest_ev else 0

        return {
            "agents": agents,
            "active_agents": active_agents,
            "whispers": whispers,
            "recent_verifications": recent_verifications,
            "model_breakdown": model_breakdown,
            "active_count": len(active_agents),
            "total_count": total_agents,
            "sse_subscribers": self.broker.subscriber_count,
            "latest_event_id": latest_event_id,
        }

    def list_events(
        self,
        *,
        since_id: int = 0,
        limit: int = 100,
        event_type: str | None = None,
    ) -> dict[str, Any]:
        eff_since = max(0, int(since_id))
        eff_limit = max(1, min(int(limit), 500))
        fetched = self.broker.fetch_events_since(
            eff_since, limit=eff_limit + 1, event_type=event_type
        )
        has_more = len(fetched) > eff_limit
        events = fetched[:eff_limit]
        last_event_id = int(events[-1]["id"]) if events else self.broker.latest_event_id()
        return {
            "events": events,
            "last_event_id": last_event_id,
            "since_id": eff_since,
            "has_more": has_more,
        }

    def list_contributors(self, window: str = "all") -> dict[str, Any]:
        window_norm = "hour" if window == "hour" else "all"
        with self.db.read() as conn:
            total_contributors = int(conn.execute("SELECT COUNT(*) AS c FROM agents").fetchone()["c"])
            rows = conn.execute(
                "SELECT * FROM agents ORDER BY compute_score DESC, tasks_completed DESC LIMIT 200"
            ).fetchall()
            all_time_scores, hourly_scores = self._fetch_score_maps(conn)
            contributors: list[dict[str, Any]] = []
            for r in rows:
                handle = str(r["handle"])
                all_score = all_time_scores.get(handle, int(r["compute_score"] or 0))
                hour_score = hourly_scores.get(handle, 0)
                score = hour_score if window_norm == "hour" else all_score
                contributors.append(
                    self._serialize_agent(
                        r,
                        window_score=score,
                        all_time_score=all_score,
                        hourly_event_score=hour_score,
                    )
                )

        contributors.sort(key=lambda c: (c["window_score"], c["tasks_completed"]), reverse=True)
        return {
            "window": window_norm,
            "contributors": contributors,
            "total": total_contributors,
            "score_weights": dict(SCORE_WEIGHTS),
        }

    def get_contributor_profile(self, handle: str) -> dict[str, Any]:
        norm_handle = handle.strip().lower()
        with self.db.read() as conn:
            row = conn.execute("SELECT * FROM agents WHERE handle = ?", (norm_handle,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail=f"Contributor '{handle}' not found")
            all_score, hour_score = self._fetch_single_agent_scores(conn, norm_handle)
            agent = self._serialize_agent(
                row,
                all_time_score=all_score if all_score > 0 else int(row["compute_score"] or 0),
                hourly_event_score=hour_score,
            )

            f_rows = conn.execute(
                "SELECT * FROM findings WHERE submitted_by = ? "
                "AND COALESCE(quarantined, 0) = 0 AND status != 'quarantined' "
                "ORDER BY created_at DESC LIMIT 25",
                (norm_handle,),
            ).fetchall()
            f_ids = [str(r["id"]) for r in f_rows]
            rti_ids: set[str] = set()
            if f_ids:
                ph = ",".join("?" for _ in f_ids)
                rti_ids = {
                    str(r["finding_id"])
                    for r in conn.execute(
                        f"SELECT finding_id FROM rti_drafts WHERE status != 'quarantined' AND finding_id IN ({ph})",
                        f_ids,
                    ).fetchall()
                }
            corr_map = self._fetch_corrections_map(conn, f_ids)
            submitted_findings = [
                self._serialize_finding(
                    r,
                    has_rti_draft=(r["id"] in rti_ids),
                    corrections=corr_map.get(str(r["id"]), []),
                )
                for r in f_rows
            ]

            v_rows = conn.execute(
                """
                SELECT pv.*, f.title AS finding_title, f.methodology_code, f.status AS finding_status
                FROM peer_verifications pv
                JOIN findings f ON f.id = pv.finding_id
                WHERE pv.verifier_handle = ?
                  AND COALESCE(f.quarantined, 0) = 0 AND f.status != 'quarantined'
                ORDER BY pv.created_at DESC LIMIT 25
                """,
                (norm_handle,),
            ).fetchall()
            verifications = [
                {
                    **self._serialize_verification(r),
                    "finding_title": r["finding_title"],
                    "methodology_code": r["methodology_code"],
                    "finding_status": r["finding_status"],
                }
                for r in v_rows
            ]

            w_rows = conn.execute(
                """
                SELECT w.* FROM whispers w
                WHERE w.agent_handle = ?
                  AND (w.finding_id IS NULL OR NOT EXISTS (
                      SELECT 1 FROM findings f
                      WHERE f.id = w.finding_id
                        AND (COALESCE(f.quarantined, 0) = 1 OR f.status = 'quarantined')
                  ))
                ORDER BY w.created_at DESC LIMIT 20
                """,
                (norm_handle,),
            ).fetchall()
            recent_whispers = [self._serialize_whisper(r) for r in w_rows]

        return {
            **agent,
            "agent": agent,
            "contributor": agent,
            "score_weights": dict(SCORE_WEIGHTS),
            "submitted_findings": submitted_findings,
            "verifications": verifications,
            "recent_whispers": recent_whispers,
        }

    def list_sponsors(self) -> dict[str, Any]:
        with self.db.read() as conn:
            agg = conn.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN pledge_status = 'pledged' THEN amount_inr ELSE 0.0 END), 0.0) AS total_pledged,
                    COALESCE(SUM(CASE
                        WHEN pledge_status = 'received' AND reconciled_receipt_ref IS NOT NULL AND reconciled_receipt_ref != ''
                        THEN amount_inr ELSE 0.0 END), 0.0) AS received_cash,
                    COALESCE(SUM(CASE
                        WHEN COALESCE(in_kind_value_inr, 0.0) > 0 THEN in_kind_value_inr
                        WHEN pledge_status LIKE 'in_kind%' THEN amount_inr
                        ELSE 0.0 END), 0.0) AS in_kind_compute
                FROM sponsors
                """
            ).fetchone()
            rows = conn.execute(
                "SELECT * FROM sponsors ORDER BY featured DESC, amount_inr DESC, created_at DESC LIMIT 200"
            ).fetchall()
            sponsors = [self._serialize_sponsor(r) for r in rows]

        featured = [s for s in sponsors if s["featured"]]
        institutional = [s for s in sponsors if s["tier"] in ("institutional_compute", "founding_grant", "patron")]
        citizen_backers = [s for s in sponsors if s["tier"] == "citizen_backer"]
        total_pledged = round(_safe_finite_float(agg["total_pledged"], 0.0), 2) if agg else 0.0
        received_cash = round(_safe_finite_float(agg["received_cash"], 0.0), 2) if agg else 0.0
        in_kind_compute = round(_safe_finite_float(agg["in_kind_compute"], 0.0), 2) if agg else 0.0

        return {
            "independence_pledge": INDEPENDENCE_PLEDGE,
            "total_pledged_inr": total_pledged,
            "received_cash_inr": received_cash,
            "in_kind_compute_inr": in_kind_compute,
            "total_inr": received_cash,
            "sponsors": sponsors,
            "featured": featured,
            "institutional": institutional,
            "citizen_backers": citizen_backers,
        }

    def create_sponsor_pledge(self, payload: dict[str, Any]) -> dict[str, Any]:
        name, _ = scrub_pii(str(payload.get("name") or "").strip()[:120])
        if not name:
            raise HTTPException(status_code=422, detail="Sponsor name is required")
        amount_inr = _parse_float_422(
            payload.get("amount_inr"),
            field="amount_inr",
            min_value=0.0,
            max_value=MAX_PLEDGE_INR,
            default=0.0,
        )
        in_kind_val = _parse_float_422(
            payload.get("in_kind_value_inr"),
            field="in_kind_value_inr",
            min_value=0.0,
            max_value=MAX_PLEDGE_INR,
            default=0.0,
        )
        credits_desc, _ = scrub_pii(str(payload.get("compute_credits_desc") or "").strip()[:200])
        if amount_inr == 0 and not credits_desc and in_kind_val == 0:
            raise HTTPException(status_code=422, detail="Provide either amount_inr > 0 or compute_credits_desc")

        tier = str(payload.get("tier") or ("institutional_compute" if amount_inr >= 50000 else "citizen_backer")).strip()
        if tier not in {"institutional_compute", "founding_grant", "patron", "citizen_backer"}:
            tier = "citizen_backer"
        handle_or_org, _ = scrub_pii(
            str(payload.get("handle_or_org") or payload.get("handle") or "").strip()[:80]
        )
        message, _ = scrub_pii(
            str(payload.get("message") or payload.get("note") or "").strip()[:500]
        )
        # Neutrality lint across all user-supplied public sponsor display fields (BNS Sec 356)
        for field_label, field_val in (
            ("name", name),
            ("handle_or_org", handle_or_org),
            ("compute_credits_desc", credits_desc),
            ("message", message),
        ):
            bad_terms = scan_forbidden_terms(field_val)
            if bad_terms:
                raise HTTPException(
                    status_code=422,
                    detail=f"Sponsor {field_label} contains prohibited term(s): {', '.join(bad_terms)}.",
                )

        raw_url = payload.get("url")
        url = _validate_optional_url_422(raw_url, field="url", require_https=False)
        # Self-declared unverified pledges NEVER automatically claim homepage featured slots.
        featured = 0

        sid = f"spn-{secrets.token_hex(6)}"
        lid = f"led-{secrets.token_hex(6)}"
        now = utc_now_iso()
        today = datetime.now(timezone.utc).date().isoformat()
        # Self-declared pledge reference (NEVER a reconciled cash receipt!)
        pledge_ref = f"PLEDGE-{secrets.token_hex(4).upper()}"
        is_in_kind = bool(credits_desc) and amount_inr == 0
        entry_type = "compute_grant" if is_in_kind else "pledge_inr"
        settlement_status = "in_kind_committed" if is_in_kind else "pledged"
        pledge_status = "in_kind_committed" if is_in_kind else "pledged"
        envelopes: list[dict[str, Any]] = []

        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO sponsors (
                    id, name, handle_or_org, tier, amount_inr, in_kind_value_inr,
                    compute_credits_desc, pledge_status, reconciled_receipt_ref,
                    url, message, featured, is_demo_entry, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, 0, ?)
                """,
                (
                    sid,
                    name,
                    handle_or_org or None,
                    tier,
                    amount_inr,
                    in_kind_val,
                    credits_desc,
                    pledge_status,
                    url,
                    message,
                    featured,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO treasury_ledger (
                    id, entry_date, entry_type, direction, settlement_status, category,
                    counterparty, description, amount_inr, compute_credit_units, receipt_ref,
                    reconciled_receipt_ref, reconciled_at, sponsor_id, is_demo_entry, created_at
                ) VALUES (?, ?, ?, 'inflow', ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, 0, ?)
                """,
                (
                    lid,
                    today,
                    entry_type,
                    settlement_status,
                    "In-Kind Compute Pledge" if is_in_kind else "Sponsor Pledge (Unreconciled)",
                    name,
                    message or f"Self-declared open-source pledge ({tier}; awaiting receipt reconciliation)",
                    in_kind_val if is_in_kind else amount_inr,
                    credits_desc,
                    pledge_ref,
                    sid,
                    now,
                ),
            )
            sp_row = conn.execute("SELECT * FROM sponsors WHERE id = ?", (sid,)).fetchone()
            led_row = conn.execute("SELECT * FROM treasury_ledger WHERE id = ?", (lid,)).fetchone()
            sponsor = self._serialize_sponsor(sp_row)
            ledger_entry = self._serialize_ledger(led_row)
            self._emit_durable_event(
                conn,
                envelopes,
                "sponsor_added",
                {
                    "sponsor_id": sponsor["id"],
                    "name": sponsor["name"],
                    "tier": sponsor["tier"],
                    "amount_inr": sponsor["amount_inr"],
                    "pledge_status": sponsor["pledge_status"],
                    "compute_credits_desc": sponsor["compute_credits_desc"],
                },
                now=now,
            )

        self._broadcast_envelopes(envelopes)
        return {"sponsor": sponsor, "ledger_entry": ledger_entry}

    def get_ledger(self, *, limit: int = 1000) -> dict[str, Any]:
        eff_limit = max(1, min(int(limit), 2000))
        with self.db.read() as conn:
            summary = self._fetch_treasury_summary_sql(conn)
            rows = conn.execute(
                "SELECT * FROM treasury_ledger ORDER BY entry_date DESC, created_at DESC LIMIT ?",
                (eff_limit,),
            ).fetchall()
            entries = [self._serialize_ledger(r) for r in rows]

        return {
            "independence_pledge": INDEPENDENCE_PLEDGE,
            "summary": summary,
            "entries": entries,
        }

    def export_ledger_csv(self) -> str:
        ledger = self.get_ledger(limit=2000)
        buf = io.StringIO()
        fieldnames = [
            "id",
            "entry_date",
            "entry_type",
            "direction",
            "settlement_status",
            "category",
            "counterparty",
            "description",
            "amount_inr",
            "signed_amount_inr",
            "compute_credit_units",
            "receipt_ref",
            "reconciled_receipt_ref",
            "reconciled_at",
            "is_demo_entry",
        ]
        writer = csv.DictWriter(buf, fieldnames=fieldnames)
        writer.writeheader()
        for entry in ledger["entries"]:
            writer.writerow({k: sanitize_csv_cell(entry.get(k, "")) for k in fieldnames})
        return buf.getvalue()

    def list_sources(self) -> dict[str, Any]:
        with self.db.read() as conn:
            rows = conn.execute(
                "SELECT * FROM public_sources ORDER BY ingestion_tier ASC, pillar ASC, name ASC"
            ).fetchall()
            sources = [self._serialize_source(r) for r in rows]
        return {
            "sources": sources,
            "total": len(sources),
            "tiers": {
                "1": "Direct Open API / Bulk Structured Files — zero CAPTCHA, automated polite fetch.",
                "2": "Public Dashboards / HTML MIS Reports — no login, strict robots.txt & rate caps.",
                "3": "CAPTCHA/Portal-Gated — contributor-uploaded official exports or OCDS mirrors ONLY (zero CAPTCHA bypass).",
            },
        }

    # ------------------------------------------------------------------
    # Desk Coordination Plane
    # ------------------------------------------------------------------

    def register_agent(
        self,
        payload: dict[str, Any],
        base_url: str,
        existing_token: str | None = None,
        registration_key: str | None = None,
    ) -> dict[str, Any]:
        raw_handle = str(payload.get("handle") or "").strip().lower()
        if not HANDLE_RE.match(raw_handle):
            raise HTTPException(
                status_code=422,
                detail="Handle must be 2-32 lowercase characters matching ^[a-z0-9][a-z0-9_-]{1,31}$",
            )
        if raw_handle in RESERVED_HANDLES:
            raise HTTPException(
                status_code=422,
                detail=f"Handle '{raw_handle}' is reserved and cannot be registered.",
            )
        display_name, _ = scrub_pii(str(payload.get("display_name") or raw_handle).strip()[:120])
        provider, _ = scrub_pii(str(payload.get("provider") or "rule-engine").strip().lower()[:64])
        model, _ = scrub_pii(str(payload.get("model") or "deterministic-cpu-v1").strip()[:120])
        mode = str(payload.get("mode") or "auto").strip().lower()
        if mode not in {"auto", "investigate", "verify"}:
            mode = "auto"
        region, _ = scrub_pii(str(payload.get("region") or "IN").strip()[:64])
        raw_caps = payload.get("capabilities") or ["deterministic_audit", "peer_verify"]
        if not isinstance(raw_caps, list):
            raw_caps = ["deterministic_audit", "peer_verify"]
        capabilities = [
            scrub_pii(str(c).strip()[:64])[0]
            for c in raw_caps[:20]
            if isinstance(c, str) and c.strip()
        ] or ["deterministic_audit", "peer_verify"]
        coding_agent = normalize_coding_agent(payload.get("coding_agent"))
        coding_agent_model = normalize_coding_agent_model(payload.get("coding_agent_model"))
        if not coding_agent:
            # Raw-HTTP registrations often name the coding agent as the provider.
            from aiwatchdogs.core.coding_agents import _CANONICAL

            coding_agent = _CANONICAL.get(provider, "")
            if coding_agent and not coding_agent_model:
                coding_agent_model = normalize_coding_agent_model(model)
        clean_reg_key = (registration_key or str(payload.get("registration_key") or "")).strip()[:128]
        # Require >=16 characters before storing a recovery digest so weak/guessable keys cannot recover handles.
        reg_key_hash: str | None = compute_token_digest(clean_reg_key) if len(clean_reg_key) >= 16 else None
        now = utc_now_iso()
        profile_url = f"{base_url.rstrip('/')}/a/{raw_handle}"
        envelopes: list[dict[str, Any]] = []

        def _handle_existing_agent(conn: Any, existing: Any) -> dict[str, Any]:
            if existing_token and verify_token_digest(
                existing_token.strip(), str(existing["api_token_hash"] or "")
            ):
                if _row_get(existing, "revoked_at", None) or str(_row_get(existing, "status", "")) == "revoked":
                    raise HTTPException(
                        status_code=403,
                        detail="Agent token has been revoked and cannot reconnect.",
                    )
                conn.execute(
                    """
                    UPDATE agents
                    SET display_name = ?, provider = ?, model = ?, mode = ?, region = ?,
                        capabilities_json = ?, status = 'active', last_heartbeat_at = ?,
                        registration_key_hash = NULL,
                        coding_agent = CASE WHEN ? != '' THEN ? ELSE coding_agent END,
                        coding_agent_model = CASE WHEN ? != '' THEN ? ELSE coding_agent_model END
                    WHERE id = ?
                    """,
                    (
                        display_name,
                        provider,
                        model,
                        mode,
                        region,
                        dumps_json(capabilities),
                        now,
                        coding_agent,
                        coding_agent,
                        coding_agent_model,
                        coding_agent_model,
                        existing["id"],
                    ),
                )
                return {
                    "agent_id": existing["id"],
                    "handle": raw_handle,
                    "api_token": existing_token.strip(),
                    "profile_url": profile_url,
                    "status": "reconnected",
                }

            # Safe retry recovery for a dropped first registration reply: requires a matching high-entropy
            # registration_key_hash, zero prior activity/claims, unrevoked & unrotated account, within 15 min.
            if not existing_token and len(clean_reg_key) >= 16:
                stored_reg_hash = str(_row_get(existing, "registration_key_hash", "") or "")
                not_revoked = (
                    not _row_get(existing, "revoked_at", None)
                    and str(_row_get(existing, "status", "")) != "revoked"
                )
                unused_account = (
                    int(_row_get(existing, "tasks_completed", 0) or 0) == 0
                    and int(_row_get(existing, "findings_submitted", 0) or 0) == 0
                    and int(_row_get(existing, "verifications_done", 0) or 0) == 0
                    and int(_row_get(existing, "cleared_submitted", 0) or 0) == 0
                    and _row_get(existing, "current_task_id", None) is None
                    and _row_get(existing, "token_rotated_at", None) is None
                )
                joined_dt = _parse_iso(str(_row_get(existing, "joined_at", "") or ""))
                now_dt = _parse_iso(now)
                within_window = (
                    joined_dt is not None
                    and now_dt is not None
                    and -60.0 <= (now_dt - joined_dt).total_seconds() <= 900.0
                )
                if (
                    not_revoked
                    and unused_account
                    and within_window
                    and stored_reg_hash
                    and verify_token_digest(clean_reg_key, stored_reg_hash)
                ):
                    has_any_claim = conn.execute(
                        "SELECT 1 FROM tasks WHERE claimed_by_agent_id = ? LIMIT 1",
                        (existing["id"],),
                    ).fetchone()
                    if has_any_claim is None:
                        recovered_token = f"awd_{secrets.token_urlsafe(24)}"
                        recovered_hash = compute_token_digest(recovered_token)
                        conn.execute(
                            """
                            UPDATE agents
                            SET display_name = ?, provider = ?, model = ?, mode = ?, region = ?,
                                capabilities_json = ?, api_token_hash = ?, status = 'active',
                                last_heartbeat_at = ?,
                                coding_agent = CASE WHEN ? != '' THEN ? ELSE coding_agent END,
                                coding_agent_model = CASE WHEN ? != '' THEN ? ELSE coding_agent_model END
                            WHERE id = ?
                            """,
                            (
                                display_name,
                                provider,
                                model,
                                mode,
                                region,
                                dumps_json(capabilities),
                                recovered_hash,
                                now,
                                coding_agent,
                                coding_agent,
                                coding_agent_model,
                                coding_agent_model,
                                existing["id"],
                            ),
                        )
                        return {
                            "agent_id": existing["id"],
                            "handle": raw_handle,
                            "api_token": recovered_token,
                            "profile_url": profile_url,
                            "status": "active",
                        }

            raise HTTPException(
                status_code=409,
                detail=f"Handle '{raw_handle}' is already registered; pass X-Agent-Token to reconnect or pick a different handle.",
            )

        with self.db.transaction() as conn:
            lock_clause = " FOR UPDATE" if self.db.is_postgres else ""
            existing = conn.execute(
                f"SELECT * FROM agents WHERE handle = ?{lock_clause}",
                (raw_handle,),
            ).fetchone()
            if existing is not None:
                return _handle_existing_agent(conn, existing)

            agent_id = f"agt-{secrets.token_hex(6)}"
            api_token = f"awd_{secrets.token_urlsafe(24)}"
            token_hash = compute_token_digest(api_token)
            cur = conn.execute(
                """
                INSERT INTO agents (
                    id, handle, display_name, provider, model, mode, region,
                    capabilities_json, api_token_hash, registration_key_hash,
                    status, joined_at, last_heartbeat_at,
                    coding_agent, coding_agent_model
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)
                ON CONFLICT(handle) DO NOTHING
                """,
                (
                    agent_id,
                    raw_handle,
                    display_name,
                    provider,
                    model,
                    mode,
                    region,
                    dumps_json(capabilities),
                    token_hash,
                    reg_key_hash,
                    now,
                    now,
                    coding_agent,
                    coding_agent_model,
                ),
            )
            if cur.rowcount == 0:
                existing = conn.execute(
                    f"SELECT * FROM agents WHERE handle = ?{lock_clause}",
                    (raw_handle,),
                ).fetchone()
                if existing is not None:
                    return _handle_existing_agent(conn, existing)
                raise HTTPException(
                    status_code=409,
                    detail=f"Handle '{raw_handle}' is already registered; pick a different handle.",
                )
            whisper = self._insert_whisper(
                conn,
                agent_handle=raw_handle,
                provider=provider,
                model=model,
                event_type="agent_joined",
                message=f"Joined the swarm ({provider}/{model}, mode={mode}). Ready to audit public records.",
            )
            self._emit_durable_event(
                conn,
                envelopes,
                "agent_joined",
                {
                    "agent_id": agent_id,
                    "handle": raw_handle,
                    "provider": provider,
                    "model": model,
                    "mode": mode,
                    "profile_url": profile_url,
                },
                now=now,
            )
            self._emit_durable_event(conn, envelopes, "whisper", whisper, now=now)

        self._broadcast_envelopes(envelopes)
        return {
            "agent_id": agent_id,
            "handle": raw_handle,
            "api_token": api_token,
            "profile_url": profile_url,
            "status": "active",
        }

    @staticmethod
    def _build_claim_response(cand: Any, *, lease_id: str, lease_expires: str) -> dict[str, Any]:
        task_payload = loads_json(cand["payload_json"], {})
        return {
            "lease_id": lease_id,
            "lease_expires_at": lease_expires,
            "role": task_role(cand["task_type"], task_payload),
            "task": {
                "id": cand["id"],
                "task_type": cand["task_type"],
                "mission_slug": cand["mission_slug"],
                "methodology_code": cand["methodology_code"],
                "title": cand["title"],
                "instructions": cand["instructions"],
                "target_finding_id": cand["target_finding_id"],
                "payload": task_payload,
                "work_type": cand["work_type"] or None,
                "estimated_tokens": cand["estimated_tokens"],
                "estimate_basis": cand["estimate_basis"] or None,
            },
        }

    def claim_next_task(
        self,
        *,
        token: str | None,
        handle: str | None = None,
        mode: str | None = None,
        preferred_mission: str | None = None,
        max_estimated_tokens: int | None = None,
        website_observations: bool = False,
        task_id: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any] | None:
        envelopes: list[dict[str, Any]] = []
        requested_task_id = (task_id or "").strip()
        clean_request_id = (request_id or "").strip()[:128] or None
        with self.db.transaction() as conn:
            agent = self._authenticate_agent(conn, token, required=True)
            assert agent is not None
            if handle and handle.strip().lower() != agent["handle"]:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Handle does not match X-Agent-Token owner",
                )
            agent_handle = str(agent["handle"])
            eff_mode = (mode or agent["mode"] or "auto").strip().lower()

            # Serialize concurrent claim attempts by the same agent on Postgres (prevents double peer_verify claims)
            if self.db.is_postgres:
                conn.execute("SELECT id FROM agents WHERE id = ? FOR UPDATE", (agent["id"],))

            self._reclaim_expired_leases(conn)
            now = utc_now_iso()

            # Durable idempotent claim replay: if the caller retries with the same request_id after
            # a dropped HTTP reply, return the exact existing active lease instead of acquiring a 2nd one.
            if clean_request_id:
                replay_task = conn.execute(
                    """
                    SELECT * FROM tasks
                    WHERE claimed_by_agent_id = ? AND claim_request_id = ? AND status = 'claimed'
                    LIMIT 1
                    """,
                    (agent["id"], clean_request_id),
                ).fetchone()
                if replay_task is not None:
                    if requested_task_id and str(replay_task["id"]) not in (
                        requested_task_id,
                        f"prj-{requested_task_id}",
                    ):
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail=(
                                f"Claim request_id '{clean_request_id}' is already bound to active lease "
                                f"'{replay_task['lease_id']}' for task '{replay_task['id']}'."
                            ),
                        )
                    conn.execute(
                        "UPDATE agents SET last_heartbeat_at = ?, status = 'active', registration_key_hash = NULL WHERE id = ?",
                        (now, agent["id"]),
                    )
                    return self._build_claim_response(
                        replay_task,
                        lease_id=str(replay_task["lease_id"]),
                        lease_expires=str(replay_task["lease_expires_at"]),
                    )

            # Enforce strict cap on concurrent active leases per agent (blocks single-agent queue hoarding)
            active_lease_row = conn.execute(
                "SELECT COUNT(*) AS c FROM tasks WHERE status = 'claimed' AND claimed_by_agent_id = ?",
                (agent["id"],),
            ).fetchone()
            if active_lease_row and int(active_lease_row["c"] or 0) >= MAX_CONCURRENT_LEASES_PER_AGENT:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=(
                        f"Agent already holds {MAX_CONCURRENT_LEASES_PER_AGENT} active task leases; "
                        "submit or release an existing lease before claiming another."
                    ),
                )

            cand: Any = None
            if requested_task_id:
                row_lock = " FOR UPDATE" if self.db.is_postgres else ""
                cand = conn.execute(
                    f"SELECT * FROM tasks WHERE id = ?{row_lock}",
                    (requested_task_id,),
                ).fetchone()
                if cand is None and not requested_task_id.startswith(("prj-", "tsk-")):
                    cand = conn.execute(
                        f"SELECT * FROM tasks WHERE id = ?{row_lock}",
                        (f"prj-{requested_task_id}",),
                    ).fetchone()
                if cand is None:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Task '{requested_task_id}' not found",
                    )

                resolved_tid = str(cand["id"])
                cand_status = str(cand["status"] or "")
                if cand_status == "claimed":
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"Task '{resolved_tid}' is already claimed by an active lease.",
                    )
                if cand_status == "completed":
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"Task '{resolved_tid}' is already completed.",
                    )
                if cand_status != "queued":
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"Task '{resolved_tid}' is not queued (status: {cand_status}).",
                    )

                if cand["task_type"] != "peer_verify":
                    m_row = conn.execute(
                        "SELECT status FROM missions WHERE id = ?",
                        (cand["mission_id"],),
                    ).fetchone()
                    m_status = str(m_row["status"] or "") if m_row is not None else ""
                    if m_row is None or m_status in ("archived", "paused", "needs_data"):
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail=(
                                f"Task '{resolved_tid}' belongs to a project that is not accepting "
                                f"new scans (status: {m_status or 'unknown'})."
                            ),
                        )

                avail_at = cand["available_at"]
                if avail_at:
                    avail_dt = _parse_iso(str(avail_at))
                    now_dt = _parse_iso(now)
                    not_ready = (avail_dt > now_dt) if (avail_dt and now_dt) else (str(avail_at) > now)
                    if not_ready:
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail=f"Task '{resolved_tid}' is not claimable until {avail_at}.",
                        )

                if cand["methodology_code"] == "W1" and not website_observations:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=(
                            f"Task '{resolved_tid}' is a W1 website review batch and requires "
                            "website_observations=true."
                        ),
                    )

                if max_estimated_tokens is not None and cand["estimated_tokens"] is not None:
                    if int(cand["estimated_tokens"]) > int(max_estimated_tokens):
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail=(
                                f"Task '{resolved_tid}' exceeds max_estimated_tokens "
                                f"({cand['estimated_tokens']} > {int(max_estimated_tokens)})."
                            ),
                        )

                target_fid = cand["target_finding_id"]
                if target_fid:
                    f_row = conn.execute(
                        "SELECT submitted_by, status, quarantined FROM findings WHERE id = ?",
                        (target_fid,),
                    ).fetchone()
                    if f_row is not None:
                        if bool(f_row["quarantined"]) or str(f_row["status"] or "") == "quarantined":
                            raise HTTPException(
                                status_code=status.HTTP_409_CONFLICT,
                                detail=f"Task '{resolved_tid}' targets a quarantined finding and cannot be claimed.",
                            )
                        if str(f_row["submitted_by"] or "") == agent_handle:
                            raise HTTPException(
                                status_code=status.HTTP_409_CONFLICT,
                                detail=f"Agent '{agent_handle}' cannot peer-verify its own finding '{target_fid}'.",
                            )
                    dup_pv = conn.execute(
                        "SELECT 1 FROM peer_verifications WHERE finding_id = ? "
                        "AND (contributor_id = ? OR verifier_handle = ?) LIMIT 1",
                        (target_fid, agent_handle, agent_handle),
                    ).fetchone()
                    if dup_pv is not None:
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail=f"Agent '{agent_handle}' has already verified finding '{target_fid}'.",
                        )
                    dup_lease = conn.execute(
                        "SELECT 1 FROM tasks WHERE status = 'claimed' AND claimed_by_handle = ? "
                        "AND target_finding_id = ? LIMIT 1",
                        (agent_handle, target_fid),
                    ).fetchone()
                    if dup_lease is not None:
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail=f"Agent '{agent_handle}' already holds an active verification lease for finding '{target_fid}'.",
                        )

                excluded = set(loads_json(cand["excluded_handles_json"], []))
                if agent_handle in excluded:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"Agent '{agent_handle}' is excluded from claiming task '{resolved_tid}'.",
                    )

                if cand["task_type"] == "research":
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"Task '{resolved_tid}' belongs to a project that has ended.",
                    )
            else:
                escaped_handle = escape_like(agent_handle)
                raw_pref = (preferred_mission or "").strip()[:120]
                preferred_slug = self.resolve_slug(raw_pref) if raw_pref else ""
                filter_preferred = bool(
                    preferred_slug and (_agents_may_choose_project() or preferred_slug in OPEN_CHOICE_PROJECTS)
                )

                # Resolve mission priority buckets first (tiny table, ~3-10 rows) so each candidate
                # lookup walks a narrow partial index on `tasks` directly in exact claim order — zero
                # cross-table sort, zero wide `payload_json` disk sort, and 1-row `FOR UPDATE SKIP LOCKED`.
                if filter_preferred:
                    mission_rows = conn.execute(
                        """
                        SELECT id, slug, status, priority
                        FROM missions
                        WHERE slug = ?
                        ORDER BY CASE WHEN status = 'archived' THEN 1 ELSE 0 END ASC, priority ASC, id ASC
                        """,
                        (preferred_slug,),
                    ).fetchall()
                else:
                    mission_rows = conn.execute(
                        """
                        SELECT id, slug, status, priority
                        FROM missions
                        ORDER BY CASE WHEN status = 'archived' THEN 1 ELSE 0 END ASC, priority ASC, id ASC
                        """
                    ).fetchall()

                mission_buckets: list[list[Any]] = []
                last_bucket_key: tuple[int, int] | None = None
                for m_row in mission_rows:
                    bkey = (1 if str(m_row["status"] or "") == "archived" else 0, int(m_row["priority"] or 1))
                    if bkey != last_bucket_key:
                        mission_buckets.append([m_row])
                        last_bucket_key = bkey
                    else:
                        mission_buckets[-1].append(m_row)

                if eff_mode == "verify":
                    phases: tuple[str, ...] = ("peer_verify",)
                elif eff_mode == "investigate":
                    phases = ("investigate",)
                else:
                    phases = ("peer_verify", "scan")

                base_clauses = [
                    "t.status = 'queued'",
                    "(t.available_at IS NULL OR t.available_at <= ?)",
                    "t.excluded_handles_json NOT LIKE ? ESCAPE '\\'",
                    # Exclude self-authored findings, quarantined findings, already-verified findings,
                    # and findings this agent already holds an active lease on (pure SQL NOT EXISTS — no bind limit!).
                    """(t.target_finding_id IS NULL OR (
                        NOT EXISTS (
                            SELECT 1 FROM findings f
                            WHERE f.id = t.target_finding_id
                              AND (f.submitted_by = ? OR COALESCE(f.quarantined, 0) = 1 OR f.status = 'quarantined')
                        )
                        AND NOT EXISTS (
                            SELECT 1 FROM peer_verifications pv
                            WHERE pv.finding_id = t.target_finding_id
                              AND (pv.contributor_id = ? OR pv.verifier_handle = ?)
                        )
                        AND NOT EXISTS (
                            SELECT 1 FROM tasks t2
                            WHERE t2.status = 'claimed'
                              AND t2.claimed_by_handle = ?
                              AND t2.target_finding_id = t.target_finding_id
                        )
                    ))""",
                ]
                base_params: list[Any] = [
                    now,
                    f'%"{escaped_handle}"%',
                    agent_handle,
                    agent_handle,
                    agent_handle,
                    agent_handle,
                ]
                if not website_observations:
                    base_clauses.append("t.methodology_code != 'W1'")
                if max_estimated_tokens is not None:
                    base_clauses.append("(t.estimated_tokens IS NULL OR t.estimated_tokens <= ?)")
                    base_params.append(int(max_estimated_tokens))

                lock_clause = " FOR UPDATE SKIP LOCKED" if self.db.is_postgres else ""
                for bucket in mission_buckets:
                    for phase in phases:
                        if phase == "peer_verify":
                            # Peer verification of existing leads may still finish on archived/paused/needs_data projects.
                            eligible_mids = [str(m["id"]) for m in bucket]
                            phase_clauses = ["t.task_type = 'peer_verify'"]
                        else:
                            # New scans only for live projects; research tasks are never handed out.
                            eligible_mids = [
                                str(m["id"])
                                for m in bucket
                                if str(m["status"] or "") not in ("archived", "paused", "needs_data")
                            ]
                            # Repeat the exact partial-index predicate `t.task_type NOT IN ('peer_verify', 'research')`
                            # so PostgreSQL always matches `idx_tasks_queued_scan_order` even in `investigate` mode.
                            if phase == "investigate":
                                phase_clauses = [
                                    "t.task_type NOT IN ('peer_verify', 'research')",
                                    "t.task_type IN ('anomaly_scan', 'ingest_parse')",
                                ]
                            else:
                                phase_clauses = ["t.task_type NOT IN ('peer_verify', 'research')"]

                        if not eligible_mids:
                            continue

                        if len(eligible_mids) == 1:
                            mission_clause = "t.mission_id = ?"
                            mission_params: list[Any] = [eligible_mids[0]]
                        else:
                            ph = ",".join("?" for _ in eligible_mids)
                            mission_clause = f"t.mission_id IN ({ph})"
                            mission_params = eligible_mids

                        all_clauses = [mission_clause, *phase_clauses, *base_clauses]
                        all_params = [*mission_params, *base_params]
                        cand_id_row = conn.execute(
                            f"""
                            SELECT t.id FROM tasks t
                            WHERE {' AND '.join(all_clauses)}
                            ORDER BY
                                COALESCE(t.available_at, t.created_at) ASC,
                                t.created_at ASC,
                                t.id ASC
                            LIMIT 1{lock_clause}
                            """,
                            all_params,
                        ).fetchone()
                        if cand_id_row is not None:
                            # Fetch the single locked row (including payload_json) by primary key.
                            cand = conn.execute(
                                "SELECT * FROM tasks WHERE id = ?",
                                (cand_id_row["id"],),
                            ).fetchone()
                            if cand is not None:
                                break
                    if cand is not None:
                        break

                if cand is None:
                    conn.execute(
                        "UPDATE agents SET last_heartbeat_at = ?, status = 'active', registration_key_hash = NULL WHERE id = ?",
                        (now, agent["id"]),
                    )
                    return None

                # Defense-in-depth exclusion verification
                excluded = set(loads_json(cand["excluded_handles_json"], []))
                if agent_handle in excluded:
                    conn.execute(
                        "UPDATE agents SET last_heartbeat_at = ?, status = 'active', registration_key_hash = NULL WHERE id = ?",
                        (now, agent["id"]),
                    )
                    return None

            lease_id = f"lease-{secrets.token_hex(8)}"
            lease_expires = _future_iso(LEASE_MINUTES)
            try:
                cur = conn.execute(
                    """
                    UPDATE tasks
                    SET status = 'claimed', claimed_by_agent_id = ?, claimed_by_handle = ?,
                        lease_id = ?, claim_request_id = ?, lease_started_at = ?, lease_expires_at = ?, progress_pct = 5,
                        attempt_count = attempt_count + 1, updated_at = ?
                    WHERE id = ? AND status = 'queued'
                    """,
                    (agent["id"], agent_handle, lease_id, clean_request_id, now, lease_expires, now, cand["id"]),
                )
            except DB_INTEGRITY_ERRORS as exc:
                if requested_task_id:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"Task '{cand['id']}' could not be claimed due to a concurrent lease conflict.",
                    ) from exc
                return None
            if cur.rowcount != 1:
                if requested_task_id:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"Task '{cand['id']}' was claimed concurrently and is no longer queued.",
                    )
                return None

            conn.execute(
                "UPDATE agents SET current_task_id = ?, last_heartbeat_at = ?, status = 'active', registration_key_hash = NULL WHERE id = ?",
                (cand["id"], now, agent["id"]),
            )
            whisper = self._insert_whisper(
                conn,
                agent_handle=agent_handle,
                provider=agent["provider"],
                model=agent["model"],
                mission_slug=cand["mission_slug"],
                finding_id=cand["target_finding_id"],
                event_type="task_claimed",
                message=f"Claimed {cand['task_type']} task ({cand['methodology_code']}): {cand['title'][:110]}",
            )
            response = self._build_claim_response(cand, lease_id=lease_id, lease_expires=lease_expires)
            self._emit_durable_event(
                conn,
                envelopes,
                "task_claimed",
                {
                    "agent_handle": agent_handle,
                    "task_id": response["task"]["id"],
                    "task_type": response["task"]["task_type"],
                    "mission_slug": response["task"]["mission_slug"],
                    "methodology_code": response["task"]["methodology_code"],
                },
                now=now,
            )
            self._emit_durable_event(conn, envelopes, "whisper", whisper, now=now)

        self._broadcast_envelopes(envelopes)
        return response

    def _lock_and_verify_lease(
        self,
        conn: Any,
        *,
        lease_id: str,
        token: str | None,
        now: str,
        allow_settled_idempotency_key: str | None = None,
    ) -> tuple[Any, Any, dict[str, Any] | None]:
        """Enforce authenticated contributor + active unexpired lease ownership atomically."""
        authed = self._authenticate_agent(conn, token, required=True)
        assert authed is not None

        lock_clause = " FOR UPDATE" if self.db.is_postgres else ""
        task = conn.execute(
            f"SELECT * FROM tasks WHERE lease_id = ?{lock_clause}", (lease_id,)
        ).fetchone()
        if task is None:
            raise HTTPException(status_code=404, detail="Lease not found")

        if authed["id"] != task["claimed_by_agent_id"]:
            raise HTTPException(status_code=403, detail="Lease belongs to another agent")

        if task["status"] in ("completed", "settled"):
            if (
                allow_settled_idempotency_key
                and _row_get(task, "settlement_idempotency_key", None) == allow_settled_idempotency_key
                and _row_get(task, "settlement_response_json", None)
            ):
                cached = loads_json(task["settlement_response_json"], None)
                if isinstance(cached, dict):
                    return authed, task, {**cached, "idempotent_replay": True}
            raise HTTPException(status_code=410, detail="Task already settled; stop and claim next")

        if task["status"] != "claimed":
            raise HTTPException(status_code=410, detail="Lease is no longer active")

        exp = task["lease_expires_at"]
        if exp and str(exp) < now:
            raise HTTPException(status_code=410, detail="Lease has expired; stop and claim next")

        return authed, task, None

    def heartbeat(
        self,
        *,
        lease_id: str,
        token: str | None = None,
        whisper_text: str | None = None,
        progress_pct: int | None = None,
    ) -> dict[str, Any]:
        envelopes: list[dict[str, Any]] = []
        now = utc_now_iso()
        clean_whisper = _validate_whisper_text(whisper_text) if whisper_text and whisper_text.strip() else ""
        with self.db.transaction() as conn:
            authed, task, _ = self._lock_and_verify_lease(conn, lease_id=lease_id, token=token, now=now)

            # Enforce hard cumulative lease lifetime cap (prevents infinite lease holding)
            started_iso = _row_get(task, "lease_started_at", None) or _row_get(task, "updated_at", None)
            started_dt = _parse_iso(started_iso)
            now_dt = _parse_iso(now)
            if started_dt is not None and now_dt is not None:
                hard_cap_dt = started_dt + timedelta(minutes=MAX_LEASE_TOTAL_MINUTES)
                if now_dt >= hard_cap_dt:
                    raise HTTPException(
                        status_code=410,
                        detail=f"Lease exceeded maximum total lifetime ({MAX_LEASE_TOTAL_MINUTES} minutes); release or resubmit.",
                    )
                proposed_dt = now_dt + timedelta(minutes=LEASE_MINUTES)
                capped_dt = min(proposed_dt, hard_cap_dt)
                new_exp = capped_dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")
            else:
                new_exp = _future_iso(LEASE_MINUTES)

            pct = max(0, min(100, int(progress_pct))) if progress_pct is not None else int(task["progress_pct"] or 0)
            cur = conn.execute(
                """
                UPDATE tasks
                SET lease_expires_at = ?, progress_pct = ?, updated_at = ?
                WHERE id = ? AND lease_id = ? AND claimed_by_agent_id = ? AND status = 'claimed'
                """,
                (new_exp, pct, now, task["id"], lease_id, authed["id"]),
            )
            if cur.rowcount != 1:
                raise HTTPException(status_code=410, detail="Lease is no longer active")

            conn.execute(
                "UPDATE agents SET last_heartbeat_at = ?, status = 'active' WHERE id = ?",
                (now, authed["id"]),
            )
            if clean_whisper:
                # Cap whisper bursts during a single lease to prevent DB/SSE flooding
                w_since = started_iso or now
                w_cnt_row = conn.execute(
                    "SELECT COUNT(*) AS c FROM whispers WHERE agent_handle = ? AND created_at >= ?",
                    (authed["handle"], w_since),
                ).fetchone()
                if not w_cnt_row or int(w_cnt_row["c"] or 0) < MAX_WHISPERS_PER_LEASE:
                    whisper_obj = self._insert_whisper(
                        conn,
                        agent_handle=authed["handle"],
                        provider=authed["provider"],
                        model=authed["model"],
                        mission_slug=task["mission_slug"],
                        finding_id=task["target_finding_id"],
                        event_type="whisper",
                        message=clean_whisper,
                    )
                    self._emit_durable_event(
                        conn,
                        envelopes,
                        "whisper",
                        whisper_obj,
                        now=now,
                        finding_id=task["target_finding_id"],
                    )

        self._broadcast_envelopes(envelopes)
        return {"status": "active", "lease_expires_at": new_exp, "progress_pct": pct}

    def release_task(
        self,
        *,
        lease_id: str,
        reason: str,
        notes: str | None = None,
        token: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        base_delay = int(os.environ.get("AIWATCHDOGS_RETRY_BASE_SECONDS", str(DEFAULT_RETRY_BASE_SECONDS)))
        clean_reason = str(reason or "released").strip()[:64] or "released"
        envelopes: list[dict[str, Any]] = []

        with self.db.transaction() as conn:
            authed, task, _ = self._lock_and_verify_lease(conn, lease_id=lease_id, token=token, now=now)
            if self._is_orphaned_w1_task(task):
                conn.execute(
                    "UPDATE agents SET current_task_id = NULL, last_heartbeat_at = ? WHERE id = ?",
                    (now, authed["id"]),
                )
                self._retire_orphaned_task(conn, str(task["id"]), str(task["mission_id"]), now)
                return {
                    "status": "retired",
                    "task_status": "retired",
                    "task_id": str(task["id"]),
                    "reason": clean_reason,
                    "retry_count": int(_row_get(task, "retry_count", 0) or 0),
                    "max_retries": int(_row_get(task, "max_retries", DEFAULT_MAX_RETRIES) or DEFAULT_MAX_RETRIES),
                    "available_at": None,
                }
            clean_notes, _ = scrub_pii(str(notes or "")[:2000])
            new_retry = int(_row_get(task, "retry_count", 0) or 0) + 1
            max_retries = int(_row_get(task, "max_retries", DEFAULT_MAX_RETRIES) or DEFAULT_MAX_RETRIES)

            # Never allow a single unprivileged client (e.g. reason='permanent_error') to bypass max_retries
            # and destroy queued backlog; all client releases follow bounded backoff & retry_count escalation.
            if new_retry >= max_retries:
                next_status = "needs_adjudication"
                available_at: str | None = None
            else:
                next_status = "queued"
                delay_sec = min(900, max(0, base_delay) * (2 ** max(0, new_retry - 1)))
                available_at = _future_iso_seconds(delay_sec) if delay_sec > 0 else now

            cur = conn.execute(
                """
                UPDATE tasks
                SET status = ?, claimed_by_agent_id = NULL, claimed_by_handle = NULL,
                    lease_id = NULL, claim_request_id = NULL, lease_started_at = NULL, lease_expires_at = NULL, progress_pct = 0,
                    retry_count = ?, available_at = ?, output_json = ?, updated_at = ?
                WHERE id = ? AND lease_id = ? AND claimed_by_agent_id = ? AND status = 'claimed'
                """,
                (
                    next_status,
                    new_retry,
                    available_at,
                    dumps_json({"released_reason": clean_reason, "notes": clean_notes, "retry_count": new_retry}),
                    now,
                    task["id"],
                    lease_id,
                    authed["id"],
                ),
            )
            if cur.rowcount != 1:
                raise HTTPException(status_code=410, detail="Lease is no longer active")

            conn.execute(
                "UPDATE agents SET current_task_id = NULL, last_heartbeat_at = ? WHERE id = ?",
                (now, authed["id"]),
            )
            task_id = str(task["id"])
            if next_status in ("needs_adjudication", "failed"):
                self._emit_durable_event(
                    conn,
                    envelopes,
                    "task_escalated",
                    {
                        "task_id": task_id,
                        "status": next_status,
                        "reason": clean_reason,
                        "retry_count": new_retry,
                        "max_retries": max_retries,
                    },
                    now=now,
                )

        self._broadcast_envelopes(envelopes)
        return {
            "status": "released" if next_status == "queued" else next_status,
            "task_status": next_status,
            "task_id": task_id,
            "reason": clean_reason,
            "retry_count": new_retry,
            "max_retries": max_retries,
            "available_at": available_at,
        }

    def submit_task(
        self,
        payload: dict[str, Any],
        token: str | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        lease_id = str(payload.get("lease_id") or "").strip()
        outcome = str(payload.get("outcome") or "").strip().lower()
        if outcome not in {"finding", "cleared", "verification", "observations"}:
            raise HTTPException(
                status_code=422,
                detail="Unknown task outcome",
            )
        eff_idem = (idempotency_key or str(payload.get("idempotency_key") or "")).strip() or None
        envelopes: list[dict[str, Any]] = []
        now = utc_now_iso()

        with self.db.transaction() as conn:
            agent, task, replay = self._lock_and_verify_lease(
                conn,
                lease_id=lease_id,
                token=token,
                now=now,
                allow_settled_idempotency_key=eff_idem,
            )
            if replay is not None:
                return replay

            if (task["methodology_code"] == "W1") != (outcome == "observations"):
                raise HTTPException(status_code=422, detail="Website assignments require a batch observation report.")

            # Server-authoritative task type enforcement
            assigned_type = str(task["task_type"] or "").strip().lower()
            if assigned_type == "research":
                raise HTTPException(status_code=409, detail="This task belongs to a project that has ended.")
            if assigned_type == "peer_verify" and outcome != "verification":
                raise HTTPException(
                    status_code=422,
                    detail="Assigned lease is a peer_verify task; outcome must be 'verification'.",
                )
            if assigned_type in ("anomaly_scan", "ingest_parse") and outcome == "verification":
                raise HTTPException(
                    status_code=422,
                    detail=f"Assigned lease is a {assigned_type} task; outcome must be 'finding' or 'cleared'.",
                )

            mission = conn.execute("SELECT * FROM missions WHERE id = ?", (task["mission_id"],)).fetchone()
            if mission is None:
                raise HTTPException(status_code=404, detail="Mission not found")

            whisper_text = _validate_whisper_text(payload.get("whisper"))
            raw_metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
            _validate_json_blob(raw_metrics, field_name="metrics", max_depth=8, max_nodes=200, max_bytes=32_768)
            metrics, _ = _scrub_recursive(raw_metrics)
            if not isinstance(metrics, dict):
                metrics = {}

            if outcome == "observations":
                from aiwatchdogs.hub.website_reports import validate_batch

                rows = validate_batch(
                    loads_json(task["payload_json"], {}), payload.get("observations"),
                    lease_started_at=task["lease_started_at"], received_at=now,
                )
                result = {"status": "accepted", "outcome": outcome, "task_id": task["id"], "hosts": len(rows)}
                self._settle_task_atomically(
                    conn, task_id=task["id"], lease_id=lease_id, agent_id=agent["id"],
                    output_obj={"observations": rows}, idempotency_key=eff_idem,
                    response_obj=result, monetary_audited_inr=0, now=now,
                )
                conn.execute("UPDATE missions SET completed_tasks = completed_tasks + 1 WHERE id = ?", (mission["id"],))
                conn.execute(
                    "UPDATE agents SET tasks_completed = tasks_completed + 1, current_task_id = NULL, "
                    "last_heartbeat_at = ? WHERE id = ?", (now, agent["id"]),
                )
                w1_msg = whisper_text or f"Observed {len(rows)} .gov.in public sites ({task['title'][:90]})."
                w1_whisper = self._insert_whisper(
                    conn,
                    agent_handle=str(agent["handle"]),
                    provider=agent["provider"],
                    model=agent["model"],
                    mission_slug=mission["slug"],
                    event_type="website_batch_observed",
                    message=w1_msg,
                )
                self._emit_durable_event(
                    conn,
                    envelopes,
                    "website_batch_observed",
                    {
                        **result,
                        "agent_handle": str(agent["handle"]),
                        "mission_slug": mission["slug"],
                    },
                    now=now,
                )
                self._emit_durable_event(conn, envelopes, "whisper", w1_whisper, now=now)
            elif outcome == "finding":
                result = self._handle_submit_finding(
                    conn=conn,
                    task=task,
                    lease_id=lease_id,
                    agent=agent,
                    mission=mission,
                    finding_in=payload.get("finding"),
                    findings_in=payload.get("findings"),
                    whisper_text=whisper_text,
                    metrics=metrics,
                    idempotency_key=eff_idem,
                    now=now,
                    envelopes=envelopes,
                )
            elif outcome == "verification":
                result = self._handle_submit_verification(
                    conn=conn,
                    task=task,
                    lease_id=lease_id,
                    agent=agent,
                    mission=mission,
                    verif_in=payload.get("verification"),
                    whisper_text=whisper_text,
                    idempotency_key=eff_idem,
                    now=now,
                    envelopes=envelopes,
                )
            else:
                result = self._handle_submit_cleared(
                    conn=conn,
                    task=task,
                    lease_id=lease_id,
                    agent=agent,
                    mission=mission,
                    whisper_text=whisper_text,
                    metrics=metrics,
                    idempotency_key=eff_idem,
                    now=now,
                    envelopes=envelopes,
                )

            # Peer checks repeat the original check on the same dataset, so they carry its estimate.
            if task["estimated_tokens"] is not None and task["dataset_key"]:
                conn.execute(
                    "UPDATE tasks SET estimated_tokens = ?, estimate_basis = ? "
                    "WHERE mission_id = ? AND dataset_key = ? AND task_type = 'peer_verify' "
                    "AND estimated_tokens IS NULL",
                    (task["estimated_tokens"], PEER_ESTIMATE_BASIS, task["mission_id"], task["dataset_key"]),
                )

            raw_usage = payload.get("usage")
            if isinstance(raw_usage, dict):
                report = UsageReport.model_validate(raw_usage)
                self._store_usage(conn, task, report.source, report.counts())

        self._broadcast_envelopes(envelopes)
        return result

    @staticmethod
    def _store_usage(conn: Any, task: Any, layer: str, counts: dict[str, int]) -> None:
        usage = loads_json(_row_get(task, "usage_json", "") or "{}", {})
        if not isinstance(usage, dict):
            usage = {}
        usage[layer] = counts
        conn.execute("UPDATE tasks SET usage_json = ? WHERE id = ?", (dumps_json(usage), task["id"]))

    def report_usage(
        self, *, task_ids: list[str], usage: UsageReport, token: str | None
    ) -> dict[str, Any]:
        """Record a coding agent's session usage against tasks it has already completed."""
        ids = list(dict.fromkeys(t.strip() for t in task_ids if t.strip()))
        if not ids:
            raise HTTPException(status_code=422, detail="task_ids must name at least one task")
        with self.db.transaction() as conn:
            agent = self._authenticate_agent(conn, token, required=True)
            assert agent is not None
            lock_clause = " FOR UPDATE" if self.db.is_postgres else ""
            rows = []
            for tid in sorted(ids):
                row = conn.execute(f"SELECT * FROM tasks WHERE id = ?{lock_clause}", (tid,)).fetchone()
                if row is None:
                    raise HTTPException(status_code=404, detail=f"Task '{tid}' not found")
                if row["claimed_by_agent_id"] != agent["id"]:
                    raise HTTPException(status_code=403, detail=f"Task '{tid}' was completed by another agent")
                if row["status"] != "completed":
                    raise HTTPException(status_code=409, detail=f"Task '{tid}' is not completed yet")
                rows.append(row)
            for row, counts in zip(rows, usage.split(len(rows)), strict=True):
                self._store_usage(conn, row, "agent", counts)
        return {"status": "recorded", "task_ids": [r["id"] for r in rows]}

    @staticmethod
    def _planned_tasks(row: Any) -> list[dict[str, Any]]:
        planned = loads_json(_row_get(row, "planned_tasks_json", "[]") or "[]", [])
        return [p for p in planned if isinstance(p, dict)] if isinstance(planned, list) else []

    def _token_summaries(
        self, conn: Any, mission_rows: list[Any]
    ) -> dict[str, dict[str, Any]]:
        """Aggregate project token estimates and reported usage in SQL without scanning all task rows into Python."""
        mission_ids = [str(r["id"]) for r in mission_rows]
        if not mission_ids:
            return {}
        ph = ",".join("?" for _ in mission_ids)
        agg_by_mid: dict[str, dict[str, int]] = {}
        for r in conn.execute(
            f"""
            SELECT mission_id,
                   SUM(CASE WHEN estimated_tokens IS NOT NULL THEN 1 ELSE 0 END) AS estimated_cnt,
                   COALESCE(SUM(CASE WHEN estimated_tokens IS NOT NULL THEN estimated_tokens ELSE 0 END), 0) AS total_est,
                   COALESCE(SUM(CASE WHEN estimated_tokens IS NOT NULL AND status != 'completed' THEN estimated_tokens ELSE 0 END), 0) AS remaining_est,
                   SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed_cnt
            FROM tasks
            WHERE mission_id IN ({ph}) AND status != 'retired'
            GROUP BY mission_id
            """,
            mission_ids,
        ).fetchall():
            agg_by_mid[str(r["mission_id"])] = {
                "estimated": int(r["estimated_cnt"] or 0),
                "total": int(r["total_est"] or 0),
                "remaining": int(r["remaining_est"] or 0),
                "done": int(r["completed_cnt"] or 0),
            }

        used_by_mid: dict[str, int] = {}
        reported_by_mid: dict[str, int] = {}
        for r in conn.execute(
            f"""
            SELECT mission_id, usage_json
            FROM tasks
            WHERE mission_id IN ({ph})
              AND status = 'completed'
              AND usage_json IS NOT NULL
              AND usage_json != ''
              AND usage_json != '{{}}'
            """,
            mission_ids,
        ).fetchall():
            spent = tokens_used(loads_json(r["usage_json"], {}))
            if spent is not None:
                mid = str(r["mission_id"])
                reported_by_mid[mid] = reported_by_mid.get(mid, 0) + 1
                used_by_mid[mid] = used_by_mid.get(mid, 0) + int(spent)

        out: dict[str, dict[str, Any]] = {}
        for row in mission_rows:
            mid = str(row["id"])
            agg = agg_by_mid.get(mid, {})
            estimated = int(agg.get("estimated", 0))
            total = int(agg.get("total", 0))
            remaining = int(agg.get("remaining", 0))
            done = int(agg.get("done", 0))
            for item in self._planned_tasks(row):
                estimated += 1
                est_val = int(item.get("estimated_tokens") or 0)
                total += est_val
                remaining += est_val
            reported = reported_by_mid.get(mid, 0)
            used = used_by_mid.get(mid, 0)
            out[mid] = {
                "estimated_tokens": total if estimated else None,
                "remaining_tokens": remaining if estimated else None,
                "tokens_used": used if reported else None,
                "reported_tasks": reported,
                "completed_tasks": done,
            }
        return out

    def _token_rows(self, conn: Any, mission_id: str | None = None) -> dict[str, list[tuple[str, int | None, Any]]]:
        where, params = (
            ("WHERE mission_id = ? AND status != 'retired'", (mission_id,))
            if mission_id
            else ("WHERE status != 'retired'", ())
        )
        out: dict[str, list[tuple[str, int | None, Any]]] = {}
        for r in conn.execute(
            f"SELECT mission_id, status, estimated_tokens, usage_json FROM tasks {where}", params
        ).fetchall():
            out.setdefault(str(r["mission_id"]), []).append(
                (str(r["status"]), r["estimated_tokens"], loads_json(r["usage_json"] or "{}", {}))
            )
        return out

    def _settle_task_atomically(
        self,
        conn: Any,
        *,
        task_id: str,
        lease_id: str,
        agent_id: str,
        output_obj: dict[str, Any],
        idempotency_key: str | None,
        response_obj: dict[str, Any],
        monetary_audited_inr: float,
        now: str,
    ) -> None:
        cur = conn.execute(
            """
            UPDATE tasks
            SET status = 'completed', progress_pct = 100, output_json = ?,
                settlement_idempotency_key = ?, settlement_response_json = ?,
                monetary_audited_inr = ?, updated_at = ?, completed_at = ?
            WHERE id = ? AND lease_id = ? AND claimed_by_agent_id = ? AND status = 'claimed'
            """,
            (
                dumps_json(output_obj),
                idempotency_key,
                dumps_json(response_obj),
                float(monetary_audited_inr),
                now,
                now,
                task_id,
                lease_id,
                agent_id,
            ),
        )
        if cur.rowcount != 1:
            raise HTTPException(
                status_code=410,
                detail="Task was already settled or lease ownership changed concurrently.",
            )

    def _prepare_finding_submission(
        self,
        *,
        task: Any,
        task_payload: dict[str, Any],
        agent: Any,
        mission: Any,
        finding_in: Any,
        metrics: dict[str, Any],
        now: str,
    ) -> dict[str, Any]:
        """Validate, PII-scrub, lint, and normalize one finding submission payload before DB mutation."""
        if not isinstance(finding_in, dict):
            raise HTTPException(status_code=422, detail="Each finding must be a JSON object when outcome='finding'")
        _validate_json_blob(finding_in, field_name="finding", max_depth=10, max_nodes=400, max_bytes=65_536)

        raw_title = str(finding_in.get("title") or "").strip()[:300]
        raw_summary = str(finding_in.get("summary") or "").strip()[:4000]
        raw_benign = finding_in.get("benign_explanations")
        benign_list = [str(x).strip()[:500] for x in raw_benign[:15]] if isinstance(raw_benign, list) else []

        # 1. Recursive PII scrubbing across ALL text & nested metrics/trace/citations/locators
        title, red_title = scrub_pii(raw_title)
        summary, red_sum = scrub_pii(raw_summary)
        entity_name, red_ent = scrub_pii(str(finding_in.get("entity_name") or mission["ministry_or_authority"]).strip()[:200])
        suggested_authority, red_auth = scrub_pii(
            str(finding_in.get("suggested_authority") or mission["ministry_or_authority"]).strip()[:200]
        )
        raw_editorial = finding_in.get("editorial_interpretation")
        if isinstance(raw_editorial, dict):
            raw_editorial_text = str(raw_editorial.get("text") or "").strip()[:2000]
        else:
            raw_editorial_text = str(raw_editorial or "").strip()[:2000]
        editorial_interp, red_interp = scrub_pii(raw_editorial_text)
        benign_clean, red_ben = _scrub_recursive(benign_list)
        citations_clean, red_cit = _scrub_recursive(finding_in.get("citations") or [])
        raw_anomaly = finding_in.get("anomaly_metrics") if isinstance(finding_in.get("anomaly_metrics"), dict) else {}
        anomaly_metrics, red_met = _scrub_recursive(raw_anomaly)
        raw_trace = finding_in.get("agent_trace") if isinstance(finding_in.get("agent_trace"), dict) else {}
        trace_clean, red_trc = _scrub_recursive(raw_trace)
        raw_locator = finding_in.get("source_locator")
        locator_clean, red_loc = _scrub_recursive(
            raw_locator if isinstance(raw_locator, (dict, str)) else {}
        )
        all_redactions = sorted(
            set(red_title + red_sum + red_ent + red_auth + red_interp + red_ben + red_cit + red_met + red_trc + red_loc)
        )

        # 2. Neutrality & journalistic fairness linting (BNS Sec 356)
        benign_for_lint = benign_clean if isinstance(benign_clean, list) else []
        lint_result = lint_neutrality(title, summary, benign_for_lint)
        if not lint_result.passed:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "Finding rejected by automated legal neutrality & evidence guardrails (BNS Sec 356).",
                    "violations": lint_result.violations,
                },
            )
        if editorial_interp:
            ed_lint = lint_neutrality(title, editorial_interp, benign_for_lint)
            if not ed_lint.passed:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "message": "Editorial interpretation rejected by automated legal neutrality guardrails (BNS Sec 356).",
                        "violations": ed_lint.violations,
                    },
                )

        finding_id = f"fnd-{secrets.token_hex(6)}"
        # Hub task methodology_code is authoritative over untrusted finding_in
        server_method = str(task["methodology_code"] or "").upper().strip()
        client_method = str(finding_in.get("methodology_code") or "").upper().strip()
        if server_method and client_method and client_method != server_method:
            raise HTTPException(
                status_code=422,
                detail=f"methodology_code mismatch: task requires '{server_method}', got '{client_method}'.",
            )
        method_code = server_method or client_method
        jurisdiction, _ = scrub_pii(str(finding_in.get("jurisdiction") or mission["jurisdiction"]).strip()[:160])
        fiscal_year, _ = scrub_pii(str(finding_in.get("fiscal_year") or task_payload.get("fiscal_year") or "FY2024-25").strip()[:100])
        fiscal_year_start = str(finding_in.get("fiscal_year_start") or "").strip()[:32] or None
        fiscal_year_end = str(finding_in.get("fiscal_year_end") or "").strip()[:32] or None
        exposure_inr = _parse_float_422(
            finding_in.get("monetary_exposure_inr"),
            field="finding.monetary_exposure_inr",
            min_value=0.0,
            max_value=MAX_MONETARY_INR,
            default=0.0,
        )
        severity = str(finding_in.get("severity") or "medium").lower().strip()
        if severity not in {"low", "medium", "high", "critical"}:
            severity = "medium"
        rule_triggered, _ = scrub_pii(str(finding_in.get("rule_triggered") or f"{method_code}-Deterministic-Rule").strip()[:400])
        engine_version = str(
            finding_in.get("engine_version")
            or (trace_clean.get("engine_version") if isinstance(trace_clean, dict) else "")
            or ""
        ).strip()[:120]
        ruleset_version = str(
            finding_in.get("ruleset_version")
            or (trace_clean.get("ruleset_version") if isinstance(trace_clean, dict) else "")
            or ""
        ).strip()[:120]
        as_of_date = str(
            finding_in.get("as_of_date")
            or task_payload.get("as_of_date")
            or (trace_clean.get("as_of_date") if isinstance(trace_clean, dict) else "")
            or ""
        ).strip()[:32] or None

        # Hub task_payload is STRICTLY AUTHORITATIVE over untrusted finding_in for dataset URLs,
        # digests, publisher/mirror provenance, and demo status (blocks verifier SSRF & provenance spoofing).
        server_retrieval = str(
            task_payload.get("retrieval_url") or task_payload.get("dataset_url") or ""
        ).strip()
        server_landing = str(
            task_payload.get("source_landing_page") or task_payload.get("source_url") or ""
        ).strip()
        server_secondary_url = str(task_payload.get("secondary_url") or "").strip()

        raw_client_retrieval = str(finding_in.get("retrieval_url") or finding_in.get("dataset_url") or "").strip()
        if raw_client_retrieval and raw_client_retrieval != server_retrieval:
            client_retrieval = _validate_optional_url_422(
                raw_client_retrieval,
                field="finding.retrieval_url",
                require_https=True,
            )
        else:
            client_retrieval = None

        raw_client_landing = str(finding_in.get("source_landing_page") or "").strip()
        if raw_client_landing and raw_client_landing != server_landing:
            client_landing = _validate_optional_url_422(
                raw_client_landing,
                field="finding.source_landing_page",
                require_https=False,
            )
        else:
            client_landing = None

        raw_client_source_url = str(finding_in.get("source_url") or finding_in.get("canonical_url") or "").strip()
        if raw_client_source_url and raw_client_source_url not in {server_landing, server_retrieval}:
            client_source_url = _validate_optional_url_422(
                raw_client_source_url,
                field="finding.source_url",
                require_https=False,
            )
        else:
            client_source_url = None

        client_mirror_catalog = _validate_optional_url_422(
            finding_in.get("mirror_catalog_url"),
            field="finding.mirror_catalog_url",
            require_https=False,
        )
        client_archive_url = _validate_optional_url_422(
            finding_in.get("archive_url"),
            field="finding.archive_url",
            require_https=False,
        )
        raw_client_secondary = str(finding_in.get("secondary_url") or "").strip()
        if raw_client_secondary and raw_client_secondary != server_secondary_url:
            client_secondary_url = _validate_optional_url_422(
                raw_client_secondary,
                field="finding.secondary_url",
                require_https=True,
            )
        else:
            client_secondary_url = raw_client_secondary or None

        retrieval_url = server_retrieval or (client_retrieval or "")
        source_landing_page = server_landing or (client_landing or client_source_url or "")

        official_publisher, _ = scrub_pii(
            str(
                task_payload.get("official_publisher")
                or finding_in.get("official_publisher")
                or mission["ministry_or_authority"]
            ).strip()[:200]
        )
        mirror_name, _ = scrub_pii(
            str(task_payload.get("mirror_name") or finding_in.get("mirror_name") or "").strip()[:160]
        )
        mirror_catalog_url = str(
            task_payload.get("mirror_catalog_url") or client_mirror_catalog or ""
        ).strip()
        archive_url = str(task_payload.get("archive_url") or "").strip() or client_archive_url
        source_url = source_landing_page or str(
            task_payload.get("source_url") or client_source_url or retrieval_url or ""
        ).strip()
        dataset_path = str(
            retrieval_url
            or task_payload.get("dataset_path")
            or mission["sample_dataset_path"]
            or ""
        ).strip()

        # Primary SHA-256: Hub pinned expected_sha256 is authoritative; reject conflicting client digests!
        server_sha = str(
            task_payload.get("expected_sha256") or task_payload.get("artifact_sha256") or ""
        ).strip().lower()
        client_sha = str(finding_in.get("artifact_sha256") or "").strip().lower()
        if server_sha and client_sha and client_sha != server_sha:
            raise HTTPException(
                status_code=422,
                detail=f"finding.artifact_sha256 conflicts with task expected_sha256 ({server_sha[:12]}).",
            )
        raw_sha = server_sha or client_sha
        artifact_sha256: str | None = raw_sha if raw_sha else None

        # Secondary URL & SHA-256: strictly server-authoritative (submitter cannot inject new secondary_url for verifiers)
        if client_secondary_url and server_secondary_url and client_secondary_url != server_secondary_url:
            raise HTTPException(
                status_code=422,
                detail="finding.secondary_url conflicts with server task secondary_url.",
            )
        secondary_url: str | None = server_secondary_url or None
        server_sec_sha = str(
            task_payload.get("secondary_expected_sha256") or task_payload.get("secondary_sha256") or ""
        ).strip().lower()
        client_sec_sha = str(finding_in.get("secondary_sha256") or "").strip().lower()
        if server_sec_sha and client_sec_sha and client_sec_sha != server_sec_sha:
            raise HTTPException(
                status_code=422,
                detail="finding.secondary_sha256 conflicts with server task secondary_sha256.",
            )
        raw_sec_sha = server_sec_sha or (client_sec_sha if secondary_url else "")
        secondary_sha256: str | None = raw_sec_sha if raw_sec_sha else None

        byte_size = _parse_int_422(
            finding_in.get("byte_size") if finding_in.get("byte_size") is not None else metrics.get("byte_size"),
            field="finding.byte_size",
            min_value=0,
            max_value=10_000_000_000,
            default=0,
        )
        fetched_at = str(finding_in.get("fetched_at") or metrics.get("fetched_at") or now).strip()[:64]
        # Submitters can never spoof is_demo_sample=False on a demo task
        is_demo = 1 if (task_payload.get("is_demo_sample", False) or finding_in.get("is_demo_sample", False)) else 0

        # Server-attested identity fields MUST overwrite any client-supplied trace keys
        agent_trace: dict[str, Any] = {
            **(trace_clean if isinstance(trace_clean, dict) else {}),
            "node_handle": agent["handle"],
            "provider": agent["provider"],
            "model_id": agent["model"],
        }

        return {
            "finding_id": finding_id,
            "pv_task_ids": [f"tsk-pv-{finding_id}-1", f"tsk-pv-{finding_id}-2"],
            "title": title,
            "summary": summary,
            "entity_name": entity_name,
            "suggested_authority": suggested_authority,
            "editorial_interp": editorial_interp,
            "benign_clean": benign_clean,
            "citations_clean": citations_clean,
            "anomaly_metrics": anomaly_metrics,
            "locator_clean": locator_clean,
            "all_redactions": all_redactions,
            "method_code": method_code,
            "jurisdiction": jurisdiction,
            "fiscal_year": fiscal_year,
            "fiscal_year_start": fiscal_year_start,
            "fiscal_year_end": fiscal_year_end,
            "exposure_inr": exposure_inr,
            "severity": severity,
            "rule_triggered": rule_triggered,
            "engine_version": engine_version,
            "ruleset_version": ruleset_version,
            "as_of_date": as_of_date,
            "retrieval_url": retrieval_url,
            "source_landing_page": source_landing_page,
            "official_publisher": official_publisher,
            "mirror_name": mirror_name,
            "mirror_catalog_url": mirror_catalog_url,
            "archive_url": archive_url,
            "source_url": source_url,
            "dataset_path": dataset_path,
            "artifact_sha256": artifact_sha256,
            "secondary_url": secondary_url,
            "secondary_sha256": secondary_sha256,
            "byte_size": byte_size,
            "fetched_at": fetched_at,
            "is_demo": is_demo,
            "requested_source_id": finding_in.get("source_id") or task_payload.get("source_id"),
            "requested_artifact_id": finding_in.get("artifact_id") or task_payload.get("artifact_id"),
            "artifact_title_hint": finding_in.get("artifact_title"),
            "agent_trace": agent_trace,
        }

    def _handle_submit_finding(
        self,
        *,
        conn: Any,
        task: Any,
        lease_id: str,
        agent: Any,
        mission: Any,
        finding_in: Any,
        findings_in: Any = None,
        whisper_text: str,
        metrics: dict[str, Any],
        idempotency_key: str | None,
        now: str,
        envelopes: list[dict[str, Any]],
    ) -> dict[str, Any]:
        task_payload = loads_json(task["payload_json"], {})
        if not isinstance(task_payload, dict):
            task_payload = {}

        task_ctx = task_payload.get("context") if isinstance(task_payload.get("context"), dict) else {}
        ub_spec = task_ctx.get("union_budget") if isinstance(task_ctx, dict) else None
        ub_lines = ub_spec.get("lines") if isinstance(ub_spec, dict) and isinstance(ub_spec.get("lines"), list) else []
        is_grouped_ub1 = str(task["methodology_code"] or "").upper() == "UB1" and len(ub_lines) > 1

        if findings_in is not None:
            if not isinstance(findings_in, list) or not 1 <= len(findings_in) <= 25:
                raise HTTPException(
                    status_code=422,
                    detail="'findings' must be a list of 1 to 25 finding objects when provided",
                )
            raw_items = findings_in
        elif isinstance(finding_in, dict):
            if is_grouped_ub1:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "Grouped multi-line UB1 tasks require submitting findings via the 'findings' list "
                        "from a batch-capable client (upgrade aiwatchdogs CLI to >= 0.6)."
                    ),
                )
            raw_items = [finding_in]
        else:
            raise HTTPException(status_code=422, detail="'finding' object is required when outcome='finding'")

        # Reconcile declared metrics.leads_found against submitted finding items so legacy/buggy clients
        # that silently drop qualifying shortfalls from multi-lead scans cannot settle the task.
        if "leads_found" in metrics and metrics["leads_found"] is not None:
            declared_leads = _parse_int_422(
                metrics.get("leads_found"),
                field="metrics.leads_found",
                min_value=0,
                max_value=10_000_000,
                default=0,
            )
            if declared_leads > 0:
                expected_submitted = min(declared_leads, 25)
                if len(raw_items) != expected_submitted:
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            f"Submitted finding count ({len(raw_items)}) does not match declared "
                            f"metrics.leads_found ({declared_leads}; expected {expected_submitted}). "
                            "Submit all qualifying findings via 'findings' or upgrade aiwatchdogs CLI to >= 0.6."
                        ),
                    )

        prepared = [
            self._prepare_finding_submission(
                task=task,
                task_payload=task_payload,
                agent=agent,
                mission=mission,
                finding_in=item,
                metrics=metrics,
                now=now,
            )
            for item in raw_items
        ]

        if is_grouped_ub1:
            if len(prepared) > len(ub_lines):
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Submitted {len(prepared)} findings for a grouped UB1 task covering only "
                        f"{len(ub_lines)} budget line(s)."
                    ),
                )
            valid_line_keys = {
                str(line.get("key") or "").strip()
                for line in ub_lines
                if isinstance(line, dict) and str(line.get("key") or "").strip()
            }
            if valid_line_keys:
                seen_line_keys: set[str] = set()
                for p_item in prepared:
                    am = p_item.get("anomaly_metrics") if isinstance(p_item.get("anomaly_metrics"), dict) else {}
                    lk = str(am.get("line_key") or "").strip()
                    if not lk or lk not in valid_line_keys:
                        raise HTTPException(
                            status_code=422,
                            detail=(
                                f"Grouped UB1 finding is missing a valid anomaly_metrics.line_key "
                                f"(got {lk!r}; expected one of {sorted(valid_line_keys)})."
                            ),
                        )
                    if lk in seen_line_keys:
                        raise HTTPException(
                            status_code=422,
                            detail=f"Duplicate UB1 finding for line_key {lk!r} in the same submission.",
                        )
                    seen_line_keys.add(lk)

        # Atomic settlement guard FIRST before creating findings/tasks/counters
        if metrics.get("monetary_audited_inr") not in (None, ""):
            audited_delta = _parse_float_422(
                metrics.get("monetary_audited_inr"),
                field="metrics.monetary_audited_inr",
                min_value=0.0,
                max_value=MAX_MONETARY_INR,
                default=0.0,
            )
        else:
            audited_delta = min(MAX_MONETARY_INR, sum(float(p["exposure_inr"]) for p in prepared))

        finding_ids = [str(p["finding_id"]) for p in prepared]
        all_pv_task_ids = [str(pid) for p in prepared for pid in p["pv_task_ids"]]
        all_redactions = sorted({r for p in prepared for r in p["all_redactions"]})
        response_obj = {
            "status": "accepted",
            "outcome": "finding",
            "finding_id": finding_ids[0],
            "finding_ids": finding_ids,
            "findings_created": len(finding_ids),
            "finding_status": "peer_review",
            "peer_verify_tasks_created": len(all_pv_task_ids),
            "peer_verify_task_ids": all_pv_task_ids,
            "pii_redactions": all_redactions,
        }
        self._settle_task_atomically(
            conn,
            task_id=str(task["id"]),
            lease_id=lease_id,
            agent_id=str(agent["id"]),
            output_obj={
                "outcome": "finding",
                "finding_id": finding_ids[0],
                "finding_ids": finding_ids,
            },
            idempotency_key=idempotency_key,
            response_obj=response_obj,
            monetary_audited_inr=audited_delta,
            now=now,
        )

        server_ctx = task_payload.get("context")
        coding_agent_name = str(_row_get(agent, "coding_agent", "") or "")
        coding_agent_model = str(_row_get(agent, "coding_agent_model", "") or "")

        for idx, item in enumerate(prepared):
            finding_id = str(item["finding_id"])
            method_code = str(item["method_code"])
            retrieval_url = str(item["retrieval_url"])
            source_landing_page = str(item["source_landing_page"])
            source_url = str(item["source_url"])
            dataset_path = str(item["dataset_path"])
            artifact_sha256 = item["artifact_sha256"]
            secondary_url = item["secondary_url"]
            secondary_sha256 = item["secondary_sha256"]
            official_publisher = str(item["official_publisher"])
            mirror_name = str(item["mirror_name"])
            mirror_catalog_url = str(item["mirror_catalog_url"])
            archive_url = item["archive_url"]
            byte_size = int(item["byte_size"])
            fetched_at = str(item["fetched_at"])
            is_demo = int(item["is_demo"])
            jurisdiction = str(item["jurisdiction"])
            title = str(item["title"])

            # Resolve exact source_id without blindly choosing mission.source_ids[0]
            resolved_source_id = self._resolve_source_id(
                conn,
                requested_source_id=item["requested_source_id"],
                source_landing_page=source_landing_page,
                source_url=source_url,
                dataset_url=retrieval_url,
            )

            # Preserve full artifact record
            requested_artifact_id = item["requested_artifact_id"]
            artifact_id: str | None = None
            if requested_artifact_id:
                existing_art = conn.execute(
                    "SELECT id FROM evidence_artifacts WHERE id = ?",
                    (str(requested_artifact_id),),
                ).fetchone()
                if existing_art is not None:
                    artifact_id = existing_art["id"]

            if artifact_id is None and (retrieval_url or source_url or artifact_sha256):
                artifact_id = f"art-{finding_id}"
                canonical_ref = retrieval_url or source_url or dataset_path
                lower_ref = canonical_ref.lower()
                if lower_ref.endswith(".pdf"):
                    fmt = "pdf"
                elif lower_ref.endswith(".xlsx") or lower_ref.endswith(".xls"):
                    fmt = "xlsx"
                elif lower_ref.endswith(".zip"):
                    fmt = "json.zip"
                elif lower_ref.endswith(".json"):
                    fmt = "json"
                elif lower_ref.endswith(".aspx") or lower_ref.endswith(".html"):
                    fmt = "html"
                else:
                    fmt = "csv"
                art_title, _ = scrub_pii(
                    str(
                        task_payload.get("artifact_title")
                        or item["artifact_title_hint"]
                        or f"{method_code} Official Public Dataset ({jurisdiction})"
                    ).strip()[:240]
                )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO evidence_artifacts (
                        id, source_id, mission_id, title, canonical_url, archive_url,
                        official_publisher, source_landing_page, mirror_name, retrieval_url,
                        secondary_url, secondary_sha256, local_path, file_format, sha256,
                        byte_size, is_demo_sample, pii_scrubbed, fetched_at, parsed_metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        artifact_id,
                        resolved_source_id,
                        mission["id"],
                        art_title,
                        canonical_ref,
                        archive_url,
                        official_publisher,
                        source_landing_page,
                        mirror_name,
                        retrieval_url or canonical_ref,
                        secondary_url,
                        secondary_sha256,
                        dataset_path or None,
                        fmt,
                        artifact_sha256,
                        byte_size,
                        is_demo,
                        fetched_at,
                        dumps_json(
                            {
                                "official_publisher": official_publisher,
                                "source_landing_page": source_landing_page,
                                "mirror_name": mirror_name,
                                "mirror_catalog_url": mirror_catalog_url,
                                "retrieval_url": retrieval_url or canonical_ref,
                                "source_url": source_url,
                                "dataset_url": retrieval_url,
                                "secondary_url": secondary_url,
                                "secondary_sha256": secondary_sha256,
                                "secondary_source_id": task_payload.get("secondary_source_id"),
                                "methodology_code": method_code,
                            }
                        ),
                    ),
                )

            provenance_obj = {
                "official_publisher": official_publisher,
                "source_landing_page": source_landing_page,
                "mirror_name": mirror_name,
                "mirror_catalog_url": mirror_catalog_url,
                "retrieval_url": retrieval_url or source_url,
                "archive_url": archive_url,
                "sha256": artifact_sha256,
                "byte_size": byte_size,
                "fetched_at": fetched_at,
                "secondary_url": secondary_url,
                "secondary_sha256": secondary_sha256,
                "secondary_source_id": task_payload.get("secondary_source_id"),
            }

            conn.execute(
                """
                INSERT INTO findings (
                    id, mission_id, mission_slug, pillar, task_id, artifact_id, source_id,
                    title, summary, editorial_interpretation, methodology_code, entity_name,
                    jurisdiction, fiscal_year, fiscal_year_start, fiscal_year_end,
                    monetary_exposure_inr, severity, anomaly_metrics_json, rule_triggered,
                    engine_version, ruleset_version, as_of_date, source_locator_json,
                    provenance_json, source_url, artifact_sha256, secondary_sha256,
                    dataset_path, is_demo_sample, citations_json, benign_explanations_json,
                    suggested_authority, submitted_by, submitted_by_agent_id,
                    agent_trace_json, legal_lint_passed, pii_scrubbed, status,
                    editorial_status, quarantined, quarantine_reason,
                    verification_score, confirm_count, dispute_count, required_quorum,
                    created_at, verified_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, 1, 1, 'peer_review',
                    'none', 0, '',
                    0.0, 0, 0, 2,
                    ?, NULL
                )
                """,
                (
                    finding_id,
                    mission["id"],
                    mission["slug"],
                    mission["pillar"],
                    task["id"],
                    artifact_id,
                    resolved_source_id,
                    title,
                    item["summary"],
                    item["editorial_interp"],
                    method_code,
                    item["entity_name"],
                    jurisdiction,
                    item["fiscal_year"],
                    item["fiscal_year_start"],
                    item["fiscal_year_end"],
                    item["exposure_inr"],
                    item["severity"],
                    dumps_json(item["anomaly_metrics"]),
                    item["rule_triggered"],
                    item["engine_version"],
                    item["ruleset_version"],
                    item["as_of_date"],
                    dumps_json(item["locator_clean"]),
                    dumps_json(provenance_obj),
                    source_url,
                    artifact_sha256,
                    secondary_sha256,
                    dataset_path,
                    is_demo,
                    dumps_json(item["citations_clean"]),
                    dumps_json(item["benign_clean"]),
                    item["suggested_authority"],
                    agent["handle"],
                    agent["id"],
                    dumps_json(item["agent_trace"]),
                    now,
                ),
            )

            # Record which coding agent produced this lead (public attribution).
            conn.execute(
                "UPDATE findings SET submitted_coding_agent = ?, submitted_coding_agent_model = ? WHERE id = ?",
                (coding_agent_name, coding_agent_model, finding_id),
            )

            # Auto-create 2 independent peer_verify tasks with full bound manifest
            pv_payload: dict[str, Any] = {
                "finding_id": finding_id,
                "submitted_by": agent["handle"],
                "review_type": "computational_reproduction",
                "methodology_code": method_code,
                "dataset_url": retrieval_url,
                "retrieval_url": retrieval_url,
                "secondary_url": secondary_url,
                "secondary_sha256": secondary_sha256,
                "dataset_path": dataset_path,
                "source_url": source_url,
                "source_landing_page": source_landing_page,
                "official_publisher": official_publisher,
                "mirror_name": mirror_name,
                "canonical_url": retrieval_url or source_url,
                "artifact_id": artifact_id,
                "artifact_sha256": artifact_sha256,
                "expected_sha256": task_payload.get("expected_sha256") or artifact_sha256,
                "engine_version": item["engine_version"],
                "ruleset_version": item["ruleset_version"],
                "as_of_date": item["as_of_date"],
                "source_locator": item["locator_clean"],
                "title": title,
                "summary": item["summary"],
                "entity_name": item["entity_name"],
                "jurisdiction": jurisdiction,
                "fiscal_year": item["fiscal_year"],
                "fiscal_year_start": item["fiscal_year_start"],
                "fiscal_year_end": item["fiscal_year_end"],
                "monetary_exposure_inr": item["exposure_inr"],
                "anomaly_metrics": item["anomaly_metrics"],
                "rule_triggered": item["rule_triggered"],
                "citations": item["citations_clean"],
                "benign_explanations": item["benign_clean"],
                "is_demo_sample": bool(is_demo),
            }
            # Verifiers must re-run the engine with the same server-side configuration the original
            # task carried (e.g. P1 page/line specs from a reviewed project file). This comes from the
            # hub's own task record, never from the submitting agent.
            if isinstance(server_ctx, dict) and server_ctx:
                pv_payload["context"] = server_ctx
            for slot, pv_id in enumerate(item["pv_task_ids"], start=1):
                conn.execute(
                    """
                    INSERT INTO tasks (
                        id, mission_id, mission_slug, methodology_code, task_type,
                        title, instructions, payload_json, target_finding_id,
                        excluded_handles_json, status, dataset_key, financial_quantity_type,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'peer_verify', ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)
                    """,
                    (
                        pv_id,
                        mission["id"],
                        mission["slug"],
                        method_code,
                        f"Peer Verify ({slot}/2): {title[:96]}",
                        (
                            "Independently load the pinned source dataset, verify primary & secondary SHA-256 "
                            "digests, re-run deterministic calculations with the bound engine/ruleset/as-of date, "
                            "validate neutrality & zero-PII, and submit explicit pass/fail/unrecorded checks."
                        ),
                        dumps_json(pv_payload),
                        finding_id,
                        dumps_json([agent["handle"]]),
                        _row_get(task, "dataset_key", ""),
                        _row_get(task, "financial_quantity_type", ""),
                        now,
                        now,
                    ),
                )

            self._record_score_event(
                conn,
                agent_id=str(agent["id"]),
                agent_handle=str(agent["handle"]),
                event_type="finding_submitted",
                ref_id=finding_id,
                now=now,
            )

            default_whisper = f"Submitted neutral {method_code} lead '{title[:85]}' for 2-of-2 peer verification."
            whisper = self._insert_whisper(
                conn,
                agent_handle=agent["handle"],
                provider=agent["provider"],
                model=agent["model"],
                mission_slug=mission["slug"],
                finding_id=finding_id,
                event_type="finding_submitted",
                message=(whisper_text if idx == 0 and whisper_text else default_whisper),
            )
            self._emit_durable_event(
                conn,
                envelopes,
                "finding_submitted",
                {
                    "finding_id": finding_id,
                    "mission_slug": mission["slug"],
                    "methodology_code": method_code,
                    "title": title,
                    "submitted_by": agent["handle"],
                    "status": "peer_review",
                },
                now=now,
            )
            self._emit_durable_event(conn, envelopes, "whisper", whisper, now=now)

        uneval_delta = _parse_int_422(
            metrics.get("unevaluable_count"),
            field="metrics.unevaluable_count",
            min_value=0,
            max_value=10_000_000,
            default=0,
        )
        conn.execute(
            """
            UPDATE missions
            SET completed_tasks = completed_tasks + 1,
                unevaluable_count = unevaluable_count + ?,
                monetary_audited_inr = monetary_audited_inr + ?
            WHERE id = ?
            """,
            (uneval_delta, audited_delta, mission["id"]),
        )
        conn.execute(
            """
            UPDATE agents
            SET tasks_completed = tasks_completed + 1,
                findings_submitted = findings_submitted + ?,
                current_task_id = NULL,
                last_heartbeat_at = ?
            WHERE id = ?
            """,
            (len(prepared), now, agent["id"]),
        )

        return response_obj

    def _handle_submit_verification(
        self,
        *,
        conn: Any,
        task: Any,
        lease_id: str,
        agent: Any,
        mission: Any,
        verif_in: Any,
        whisper_text: str,
        idempotency_key: str | None,
        now: str,
        envelopes: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not isinstance(verif_in, dict):
            raise HTTPException(
                status_code=422, detail="'verification' object is required when outcome='verification'"
            )
        _validate_json_blob(verif_in, field_name="verification", max_depth=10, max_nodes=300, max_bytes=49_152)

        task_payload = loads_json(task["payload_json"], {})
        if not isinstance(task_payload, dict):
            task_payload = {}

        # Server-authoritative finding target: lease assignment decides target finding
        assigned_finding_id = str(
            task["target_finding_id"] or task_payload.get("finding_id") or ""
        ).strip()
        submitted_finding_id = str(verif_in.get("finding_id") or "").strip()
        if assigned_finding_id and submitted_finding_id and submitted_finding_id != assigned_finding_id:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Verification target mismatch: lease is assigned to finding '{assigned_finding_id}' "
                    f"and cannot verify '{submitted_finding_id}'."
                ),
            )
        finding_id = assigned_finding_id or submitted_finding_id
        if not finding_id:
            raise HTTPException(status_code=422, detail="verification.finding_id is required")

        # Serialize vote aggregation on Postgres (FOR UPDATE) & SQLite (BEGIN IMMEDIATE)
        lock_clause = " FOR UPDATE" if self.db.is_postgres else ""
        finding_row = conn.execute(
            f"SELECT * FROM findings WHERE id = ?{lock_clause}", (finding_id,)
        ).fetchone()
        if finding_row is None:
            raise HTTPException(status_code=404, detail=f"Finding '{finding_id}' not found")

        # Strict independence: no self-verification and no duplicate contributor votes
        if agent["handle"] == finding_row["submitted_by"]:
            raise HTTPException(
                status_code=409,
                detail="Self-verification prohibited: verifier must be independent of finding submitter.",
            )
        dup = conn.execute(
            "SELECT id FROM peer_verifications WHERE finding_id = ? AND contributor_id = ?",
            (finding_id, agent["handle"]),
        ).fetchone()
        if dup is not None:
            raise HTTPException(
                status_code=409,
                detail="Duplicate verification prohibited: contributor has already verified this finding.",
            )

        # Strict explicit verdict (NEVER default omitted verdict to 'confirm'!)
        raw_verdict = verif_in.get("verdict")
        if raw_verdict is None or not str(raw_verdict).strip():
            raise HTTPException(
                status_code=422,
                detail=f"verification.verdict is required and must be one of {sorted(VALID_VERDICTS)}",
            )
        verdict = str(raw_verdict).strip().lower()
        if verdict not in VALID_VERDICTS:
            raise HTTPException(
                status_code=422,
                detail=f"verdict must be one of {sorted(VALID_VERDICTS)}",
            )

        # Strict explicit 4-check normalization (missing checks -> 'unrecorded', NEVER 'pass'!)
        checks = _normalize_checks(verif_in.get("checks"), strict=True)

        raw_repro = verif_in.get("reproduced_metrics")
        if raw_repro is not None and not isinstance(raw_repro, dict):
            raise HTTPException(status_code=422, detail="verification.reproduced_metrics must be an object")
        reproduced, _ = _scrub_recursive(raw_repro if isinstance(raw_repro, dict) else {})
        if not isinstance(reproduced, dict):
            reproduced = {}

        # Optional nullable confidence (NEVER default omitted confidence to 0.9; reject NaN/Inf/bool)
        raw_conf = verif_in.get("confidence")
        confidence: float | None = None
        if raw_conf is not None:
            confidence = _parse_float_422(
                raw_conf,
                field="verification.confidence",
                min_value=0.0,
                max_value=1.0,
                default=None,
            )

        review_type = str(
            verif_in.get("review_type") or task_payload.get("review_type") or "computational_reproduction"
        ).strip().lower()
        if review_type not in VALID_REVIEW_TYPES:
            review_type = "computational_reproduction"

        manifest_raw: dict[str, Any] = {
            "claim_id": str(verif_in.get("claim_id") or finding_id),
            "review_type": review_type,
            "source_locator": verif_in.get("source_locator"),
            "engine_version": verif_in.get("engine_version"),
            "ruleset_version": verif_in.get("ruleset_version"),
            "as_of_date": verif_in.get("as_of_date"),
            "artifact_sha256": verif_in.get("artifact_sha256") or reproduced.get("artifact_sha256"),
            "secondary_sha256": verif_in.get("secondary_sha256") or reproduced.get("secondary_sha256"),
        }
        manifest_clean, _ = _scrub_recursive({k: v for k, v in manifest_raw.items() if v is not None})
        notes, _ = scrub_pii(str(verif_in.get("notes") or "").strip()[:2000])

        # Require written justification when flagging a finding for privacy/legal quarantine
        if verdict == "flag_pii_or_legal" and len(notes) < 8:
            raise HTTPException(
                status_code=422,
                detail="verdict='flag_pii_or_legal' requires descriptive notes (>= 8 characters) explaining the privacy/legal issue.",
            )

        # Strict confirmation requirements: all 4 checks pass + non-empty consistent metrics + matching bound digests
        if verdict == "confirm":
            missing_or_failed = [k for k in CANONICAL_CHECKS if checks.get(k) != "pass"]
            if missing_or_failed:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "verdict='confirm' requires explicit 'pass' on all four checks "
                        f"({', '.join(CANONICAL_CHECKS)}); not passed: {', '.join(missing_or_failed)}"
                    ),
                )
            if not reproduced:
                raise HTTPException(
                    status_code=422,
                    detail="verdict='confirm' requires non-empty reproduced_metrics binding the calculation.",
                )
            # Require matching digests whenever bound on the finding (prevents rubber-stamping without artifact proof)
            for digest_field in ("artifact_sha256", "secondary_sha256"):
                f_digest = str(_row_get(finding_row, digest_field, "") or "").strip().lower()
                v_digest = (
                    str(manifest_clean.get(digest_field) or "").strip().lower()
                    if isinstance(manifest_clean, dict)
                    else ""
                )
                if f_digest:
                    if not v_digest:
                        raise HTTPException(
                            status_code=422,
                            detail=f"verdict='confirm' requires matching {digest_field} when bound on the finding.",
                        )
                    if v_digest != f_digest:
                        raise HTTPException(
                            status_code=422,
                            detail=f"verdict='confirm' rejected: {digest_field} mismatch (finding={f_digest!r}, verifier={v_digest!r}).",
                        )
            # Reject explicit engine/ruleset version conflicts
            for mfield in ("engine_version", "ruleset_version"):
                f_val = str(_row_get(finding_row, mfield, "") or "").strip()
                v_val = str(manifest_clean.get(mfield) or "").strip() if isinstance(manifest_clean, dict) else ""
                if f_val and v_val and f_val.lower() != v_val.lower():
                    raise HTTPException(
                        status_code=422,
                        detail=f"verdict='confirm' rejected: {mfield} mismatch (finding={f_val!r}, verifier={v_val!r}).",
                    )
            # Validate numeric consistency of reproduced_metrics against finding.anomaly_metrics
            finding_metrics = loads_json(finding_row["anomaly_metrics_json"], {})
            if isinstance(finding_metrics, dict) and finding_metrics:
                metrics_ok, mismatches = _check_reproduced_metrics_consistency(finding_metrics, reproduced)
                if not metrics_ok:
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            "verdict='confirm' rejected: reproduced_metrics diverges from finding anomaly_metrics: "
                            + "; ".join(mismatches[:5])
                        ),
                    )

        pv_id = f"pv-{secrets.token_hex(6)}"
        try:
            conn.execute(
                """
                INSERT INTO peer_verifications (
                    id, finding_id, verifier_node_id, contributor_id, verifier_handle,
                    provider, model_id, review_type, verdict, checks_json,
                    reproduced_metrics_json, manifest_json, confidence, notes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pv_id,
                    finding_id,
                    agent["id"],
                    agent["handle"],
                    agent["handle"],
                    agent["provider"],
                    agent["model"],
                    review_type,
                    verdict,
                    dumps_json(checks),
                    dumps_json(reproduced),
                    dumps_json(manifest_clean),
                    confidence,
                    notes,
                    now,
                ),
            )
        except DB_INTEGRITY_ERRORS as exc:
            raise HTTPException(
                status_code=409,
                detail="Duplicate verification prohibited by uniqueness constraint.",
            ) from exc
        conn.execute(
            "UPDATE peer_verifications SET coding_agent = ?, coding_agent_model = ? WHERE id = ?",
            (
                str(_row_get(agent, "coding_agent", "") or ""),
                str(_row_get(agent, "coding_agent_model", "") or ""),
                pv_id,
            ),
        )

        # Recompute quorum across ALL independent verifications for this finding under finding lock
        all_votes = conn.execute(
            """
            SELECT contributor_id, verdict, checks_json, reproduced_metrics_json, confidence
            FROM peer_verifications
            WHERE finding_id = ?
            """,
            (finding_id,),
        ).fetchall()

        qualified_confirms = 0
        dispute_count = 0
        has_pii_or_legal_flag = False
        voter_handles: set[str] = {str(finding_row["submitted_by"])}

        for v in all_votes:
            voter_handles.add(str(v["contributor_id"]))
            v_verdict = str(v["verdict"] or "")
            if v_verdict == "flag_pii_or_legal":
                has_pii_or_legal_flag = True
                dispute_count += 1
            elif v_verdict == "confirm":
                v_checks = _normalize_checks(loads_json(v["checks_json"], {}), strict=False)
                v_metrics = loads_json(v["reproduced_metrics_json"], {})
                if _all_checks_passed(v_checks) and isinstance(v_metrics, dict) and len(v_metrics) > 0:
                    qualified_confirms += 1
            else:
                dispute_count += 1

        total_votes = len(all_votes)
        consensus = round(qualified_confirms / total_votes, 3) if total_votes > 0 else 0.0

        prev_status = str(finding_row["status"] or "")
        new_status = prev_status
        new_editorial = str(_row_get(finding_row, "editorial_status", "none") or "none")
        new_quarantined = int(_row_get(finding_row, "quarantined", 0) or 0)
        new_quarantine_reason = str(_row_get(finding_row, "quarantine_reason", "") or "")
        promoted = False
        adjudication_queued = False
        adjudication_task_id: str | None = None
        rti_draft_id: str | None = None

        if has_pii_or_legal_flag:
            new_status = "quarantined"
            new_editorial = "quarantined"
            new_quarantined = 1
            new_quarantine_reason = "Flagged for privacy/legal review pending human adjudication."
        elif (
            new_quarantined == 0
            and prev_status not in ("retracted", "under_review")
            and qualified_confirms >= 2
            and consensus >= 0.75
            and (dispute_count == 0 or qualified_confirms >= 3)
        ):
            new_status = "verified"
            promoted = prev_status != "verified"
        elif dispute_count >= 2 and consensus < 0.5:
            all_fp = all(v["verdict"] == "reject_false_positive" for v in all_votes)
            new_status = "retracted_false_positive" if all_fp else "disputed"

        new_verified_at = now if (new_status == "verified" and not finding_row["verified_at"]) else finding_row["verified_at"]

        # Check for split vote (1 confirm + 1 non-confirm) with bounded adjudication tie-breaker rounds
        if new_status == "peer_review" and qualified_confirms >= 1 and dispute_count >= 1:
            prior_adj_row = conn.execute(
                "SELECT COUNT(*) AS c FROM tasks WHERE target_finding_id = ? AND id LIKE 'tsk-adj-%'",
                (finding_id,),
            ).fetchone()
            prior_adj_rounds = int(prior_adj_row["c"] or 0) if prior_adj_row else 0
            open_other = conn.execute(
                """
                SELECT COUNT(*) AS c FROM tasks
                WHERE target_finding_id = ? AND task_type = 'peer_verify'
                  AND status IN ('queued', 'claimed') AND id != ?
                """,
                (finding_id, task["id"]),
            ).fetchone()
            if int(open_other["c"] if open_other else 0) == 0:
                if prior_adj_rounds >= MAX_ADJUDICATION_ROUNDS:
                    # Escalate deadlocked finding to human editorial review rather than spawning infinite tasks
                    new_status = "under_review"
                    new_editorial = "under_review"
                else:
                    adjudication_task_id = f"tsk-adj-{finding_id}-{total_votes}"
                    adj_payload = {
                        **task_payload,
                        "finding_id": finding_id,
                        "review_type": "adjudication",
                        "adjudication_reason": "split_vote",
                        "prior_confirm_count": qualified_confirms,
                        "prior_dispute_count": dispute_count,
                    }
                    conn.execute(
                        """
                        INSERT INTO tasks (
                            id, mission_id, mission_slug, methodology_code, task_type,
                            title, instructions, payload_json, target_finding_id,
                            excluded_handles_json, status, dataset_key, financial_quantity_type,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, 'peer_verify', ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)
                        ON CONFLICT(id) DO NOTHING
                        """,
                        (
                            adjudication_task_id,
                            finding_row["mission_id"],
                            finding_row["mission_slug"],
                            finding_row["methodology_code"],
                            f"Adjudication Tie-Breaker: {str(finding_row['title'])[:82]}",
                            (
                                "Split peer-verification vote detected (confirm vs dispute). Independently "
                                "reload the pinned source artifact, verify SHA-256, reproduce exact metrics, "
                                "and record an explicit tie-breaker adjudication verdict."
                            ),
                            dumps_json(adj_payload),
                            finding_id,
                            dumps_json(sorted(voter_handles)),
                            _row_get(task, "dataset_key", ""),
                            _row_get(task, "financial_quantity_type", ""),
                            now,
                            now,
                        ),
                    )
                    adjudication_queued = True
                    self._emit_durable_event(
                        conn,
                        envelopes,
                        "adjudication_queued",
                        {
                            "finding_id": finding_id,
                            "adjudication_task_id": adjudication_task_id,
                            "confirm_count": qualified_confirms,
                            "dispute_count": dispute_count,
                        },
                        now=now,
                        finding_id=finding_id,
                    )

        # Build response and settle current verification lease atomically BEFORE crediting counters
        if promoted:
            existing_rti = conn.execute(
                "SELECT id FROM rti_drafts WHERE finding_id = ?", (finding_id,)
            ).fetchone()
            rti_draft_id = str(existing_rti["id"]) if existing_rti is not None else f"rti-{finding_id}"

        response_obj = {
            "status": "accepted",
            "outcome": "verification",
            "verification_id": pv_id,
            "finding_id": finding_id,
            "finding_status": new_status,
            "confirm_count": qualified_confirms,
            "dispute_count": dispute_count,
            "verification_score": consensus,
            "promoted_to_verified": promoted,
            "quarantined": bool(new_quarantined),
            "adjudication_queued": adjudication_queued,
            "adjudication_task_id": adjudication_task_id,
            "rti_draft_id": rti_draft_id,
        }
        self._settle_task_atomically(
            conn,
            task_id=str(task["id"]),
            lease_id=lease_id,
            agent_id=str(agent["id"]),
            output_obj={"outcome": "verification", "finding_id": finding_id, "verdict": verdict},
            idempotency_key=idempotency_key,
            response_obj=response_obj,
            monetary_audited_inr=0.0,
            now=now,
        )

        conn.execute(
            """
            UPDATE findings
            SET confirm_count = ?, dispute_count = ?, verification_score = ?,
                status = ?, editorial_status = ?, quarantined = ?, quarantine_reason = ?,
                verified_at = ?
            WHERE id = ?
            """,
            (
                qualified_confirms,
                dispute_count,
                consensus,
                new_status,
                new_editorial,
                new_quarantined,
                new_quarantine_reason,
                new_verified_at,
                finding_id,
            ),
        )

        conn.execute(
            """
            UPDATE agents
            SET tasks_completed = tasks_completed + 1,
                verifications_done = verifications_done + 1,
                current_task_id = NULL,
                last_heartbeat_at = ?
            WHERE id = ?
            """,
            (now, agent["id"]),
        )
        # Do not award automatic merit points on unreviewed quarantine flags (blocks censorship score farming)
        if verdict != "flag_pii_or_legal":
            self._record_score_event(
                conn,
                agent_id=str(agent["id"]),
                agent_handle=str(agent["handle"]),
                event_type="peer_verification",
                ref_id=pv_id,
                now=now,
            )

        # Exclude this verifier from any remaining queued peer_verify tasks on the same finding
        rem_tasks = conn.execute(
            "SELECT id, excluded_handles_json FROM tasks WHERE target_finding_id = ? AND status = 'queued'",
            (finding_id,),
        ).fetchall()
        for rt in rem_tasks:
            excl = loads_json(rt["excluded_handles_json"], [])
            if agent["handle"] not in excl:
                excl.append(agent["handle"])
                conn.execute(
                    "UPDATE tasks SET excluded_handles_json = ?, updated_at = ? WHERE id = ?",
                    (dumps_json(excl), now, rt["id"]),
                )

        def _settle_open_peer_checks() -> None:
            conn.execute(
                """
                UPDATE agents
                SET current_task_id = NULL
                WHERE current_task_id IN (
                    SELECT id FROM tasks
                    WHERE target_finding_id = ? AND task_type = 'peer_verify' AND status = 'claimed'
                )
                """,
                (finding_id,),
            )
            conn.execute(
                """
                UPDATE tasks
                SET status = 'settled', updated_at = ?, completed_at = ?
                WHERE target_finding_id = ? AND task_type = 'peer_verify' AND status IN ('queued', 'claimed')
                """,
                (now, now, finding_id),
            )

        if has_pii_or_legal_flag:
            _settle_open_peer_checks()
            conn.execute(
                """
                INSERT OR IGNORE INTO finding_corrections (
                    id, finding_id, correction_type, previous_status, new_status,
                    summary, details, source_evidence_url, reviewed_by, created_at
                ) VALUES (?, ?, 'quarantine', ?, 'quarantined', ?, ?, NULL, ?, ?)
                """,
                (
                    f"cor-quar-{pv_id}",
                    finding_id,
                    prev_status,
                    "Quarantined automatically after independent peer privacy/legal flag.",
                    "Withheld across public list and detail views pending human privacy/legal adjudication.",
                    agent["handle"],
                    now,
                ),
            )
            conn.execute(
                """
                UPDATE rti_drafts
                SET status = 'quarantined',
                    editorial_notice = 'Quarantined pending privacy/legal review.',
                    updated_at = ?
                WHERE finding_id = ?
                """,
                (now, finding_id),
            )
            # Atomically redact dependent tasks, whispers, and prior events so quarantined data never leaks
            self._redact_quarantined_finding_artifacts(conn, finding_id, now)
            if self.db.is_postgres:
                conn.execute("SELECT id FROM missions WHERE id = ? FOR UPDATE", (finding_row["mission_id"],))
            cnt_row = conn.execute(
                """
                SELECT COUNT(*) AS c FROM findings
                WHERE mission_id = ? AND status IN ('verified', 'rti_filed') AND COALESCE(quarantined, 0) = 0
                """,
                (finding_row["mission_id"],),
            ).fetchone()
            conn.execute(
                "UPDATE missions SET verified_findings_count = ? WHERE id = ?",
                (int(cnt_row["c"]) if cnt_row else 0, finding_row["mission_id"]),
            )
            self._emit_durable_event(
                conn,
                envelopes,
                "finding_quarantined",
                {"finding_id": finding_id, "verification_id": pv_id},
                now=now,
                finding_id=finding_id,
            )

        elif new_status in ("disputed", "retracted_false_positive"):
            _settle_open_peer_checks()

        elif promoted:
            conn.execute(
                "UPDATE missions SET verified_findings_count = verified_findings_count + 1 WHERE id = ?",
                (finding_row["mission_id"],),
            )
            conn.execute(
                "UPDATE agents SET findings_verified = findings_verified + 1 WHERE handle = ?",
                (finding_row["submitted_by"],),
            )
            self._record_score_event(
                conn,
                agent_id=finding_row["submitted_by_agent_id"],
                agent_handle=str(finding_row["submitted_by"]),
                event_type="finding_verified_author_bonus",
                ref_id=finding_id,
                now=now,
            )
            _settle_open_peer_checks()
            existing_rti = conn.execute(
                "SELECT id FROM rti_drafts WHERE finding_id = ?", (finding_id,)
            ).fetchone()
            if existing_rti is None:
                finding_dict = self._serialize_finding(finding_row)
                rti = generate_rti_draft(finding_dict)
                rti_draft_id = f"rti-{finding_id}"
                fee_note = str(rti.get("fee_rules") or rti.get("fee_note") or "")
                jlevel = str(rti.get("jurisdiction_level") or "central")
                sname = rti.get("state_name")
                fchan = str(rti.get("filing_channel") or "central_online_portal")
                cwarn = str(rti.get("central_portal_warning") or "")
                conn.execute(
                    """
                    INSERT INTO rti_drafts (
                        id, finding_id, version, public_authority, cpio_designation,
                        rti_portal_url, jurisdiction_level, state_name, filing_channel,
                        fee_note, central_portal_warning, statutory_sections_json,
                        subject, body_text, word_count, requested_records_json,
                        status, editorial_notice, created_at, updated_at
                    ) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', '', ?, ?)
                    """,
                    (
                        rti_draft_id,
                        finding_id,
                        rti["public_authority"],
                        rti["cpio_designation"],
                        rti.get("rti_portal_url") or "",
                        jlevel,
                        sname,
                        fchan,
                        fee_note,
                        cwarn,
                        dumps_json(rti["statutory_sections"]),
                        rti["subject"],
                        rti["body_text"],
                        rti["word_count"],
                        dumps_json(rti["requested_records"]),
                        now,
                        now,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO rti_draft_versions (
                        id, rti_draft_id, finding_id, version, public_authority, cpio_designation,
                        rti_portal_url, jurisdiction_level, filing_channel, subject, body_text,
                        word_count, requested_records_json, status, editorial_notice, change_note, created_at
                    ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', '', 'Auto-generated upon 2-of-2 quorum', ?)
                    """,
                    (
                        f"rtiv-{secrets.token_hex(6)}",
                        rti_draft_id,
                        finding_id,
                        rti["public_authority"],
                        rti["cpio_designation"],
                        rti.get("rti_portal_url") or "",
                        jlevel,
                        fchan,
                        rti["subject"],
                        rti["body_text"],
                        rti["word_count"],
                        dumps_json(rti["requested_records"]),
                        now,
                    ),
                )
                self._emit_durable_event(
                    conn,
                    envelopes,
                    "rti_drafted",
                    {
                        "finding_id": finding_id,
                        "rti_draft_id": rti_draft_id,
                        "public_authority": rti["public_authority"],
                        "word_count": rti["word_count"],
                        "version": 1,
                    },
                    now=now,
                )

        safe_whisper = (
            "Recorded privacy/legal quarantine flag; record withheld pending human review."
            if has_pii_or_legal_flag
            else (
                whisper_text
                or (
                    f"Quorum reached ({qualified_confirms}/{total_votes}) on {finding_id} — promoted to VERIFIED & RTI draft ready."
                    if promoted
                    else f"Recorded independent peer verdict '{verdict}' on {finding_id} ({qualified_confirms} confirm / {dispute_count} dispute)."
                )
            )
        )
        whisper = self._insert_whisper(
            conn,
            agent_handle=agent["handle"],
            provider=agent["provider"],
            model=agent["model"],
            mission_slug=finding_row["mission_slug"],
            finding_id=None if has_pii_or_legal_flag else finding_id,
            event_type="finding_verified" if promoted else "verification_recorded",
            message=safe_whisper,
        )
        self._emit_durable_event(
            conn,
            envelopes,
            "verification_recorded",
            {
                "verification_id": pv_id,
                "finding_id": finding_id,
                "verifier_handle": agent["handle"],
                "verdict": verdict,
                "confirm_count": qualified_confirms,
                "dispute_count": dispute_count,
                "consensus": consensus,
                "quarantined": bool(new_quarantined),
            },
            now=now,
        )
        if promoted:
            self._emit_durable_event(
                conn,
                envelopes,
                "finding_verified",
                {
                    "finding_id": finding_id,
                    "mission_slug": finding_row["mission_slug"],
                    "title": finding_row["title"],
                    "confirm_count": qualified_confirms,
                    "verification_score": consensus,
                    "rti_draft_id": rti_draft_id,
                },
                now=now,
            )
        self._emit_durable_event(conn, envelopes, "whisper", whisper, now=now)
        return response_obj

    def _handle_submit_cleared(
        self,
        *,
        conn: Any,
        task: Any,
        lease_id: str,
        agent: Any,
        mission: Any,
        whisper_text: str,
        metrics: dict[str, Any],
        idempotency_key: str | None,
        now: str,
        envelopes: list[dict[str, Any]],
    ) -> dict[str, Any]:
        audited_delta = _parse_float_422(
            metrics.get("monetary_audited_inr"),
            field="metrics.monetary_audited_inr",
            min_value=0.0,
            max_value=MAX_MONETARY_INR,
            default=0.0,
        )
        cleared_records = _parse_int_422(
            metrics.get("cleared_count"),
            field="metrics.cleared_count",
            min_value=0,
            max_value=10_000_000,
            default=0,
        )
        unevaluable_records = _parse_int_422(
            metrics.get("unevaluable_count"),
            field="metrics.unevaluable_count",
            min_value=0,
            max_value=10_000_000,
            default=0,
        )
        initial_mission_cleared_scans = int(mission["cleared_count"] or 0) + 1

        response_obj = {
            "status": "accepted",
            "outcome": "cleared",
            "task_id": task["id"],
            "cleared_records": cleared_records,
            "unevaluable_records": unevaluable_records,
            "mission_cleared_count": initial_mission_cleared_scans,
            "mission_cleared_scans_count": initial_mission_cleared_scans,
        }
        self._settle_task_atomically(
            conn,
            task_id=str(task["id"]),
            lease_id=lease_id,
            agent_id=str(agent["id"]),
            output_obj={"outcome": "cleared", "metrics": metrics},
            idempotency_key=idempotency_key,
            response_obj=response_obj,
            monetary_audited_inr=audited_delta,
            now=now,
        )

        # Read the post-update counter atomically via RETURNING so concurrent clear receipts
        # reflect the exact monotonic mission total rather than a stale pre-update snapshot.
        updated_mission = conn.execute(
            """
            UPDATE missions
            SET completed_tasks = completed_tasks + 1,
                cleared_count = cleared_count + 1,
                unevaluable_count = unevaluable_count + ?,
                monetary_audited_inr = monetary_audited_inr + ?
            WHERE id = ?
            RETURNING cleared_count
            """,
            (unevaluable_records, audited_delta, mission["id"]),
        ).fetchone()
        if updated_mission is not None:
            actual_cleared = int(updated_mission["cleared_count"] or initial_mission_cleared_scans)
            if actual_cleared != initial_mission_cleared_scans:
                response_obj["mission_cleared_count"] = actual_cleared
                response_obj["mission_cleared_scans_count"] = actual_cleared
                conn.execute(
                    "UPDATE tasks SET settlement_response_json = ? WHERE id = ?",
                    (dumps_json(response_obj), str(task["id"])),
                )
        conn.execute(
            """
            UPDATE agents
            SET tasks_completed = tasks_completed + 1,
                cleared_submitted = cleared_submitted + 1,
                current_task_id = NULL,
                last_heartbeat_at = ?
            WHERE id = ?
            """,
            (now, agent["id"]),
        )
        self._record_score_event(
            conn,
            agent_id=str(agent["id"]),
            agent_handle=str(agent["handle"]),
            event_type="cleared_scan",
            ref_id=str(task["id"]),
            now=now,
        )

        whisper = self._insert_whisper(
            conn,
            agent_handle=agent["handle"],
            provider=agent["provider"],
            model=agent["model"],
            mission_slug=mission["slug"],
            event_type="cleared",
            message=whisper_text
            or (
                f"Cleared scan on {mission['slug']}: {cleared_records} evaluable record(s) within thresholds"
                + (f", {unevaluable_records} unevaluable group(s)." if unevaluable_records else ".")
            ),
        )
        self._emit_durable_event(
            conn,
            envelopes,
            "scan_cleared",
            {
                "task_id": task["id"],
                "mission_slug": mission["slug"],
                "agent_handle": agent["handle"],
                "cleared_records": cleared_records,
                "unevaluable_records": unevaluable_records,
            },
            now=now,
        )
        self._emit_durable_event(conn, envelopes, "whisper", whisper, now=now)
        return response_obj
