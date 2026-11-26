"""pequalnp-style Desk Coordination Loop and Autonomous Watchdog Agent Runner."""

from __future__ import annotations

import inspect
import json
import logging
import math
import os
import random
import re
import secrets
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
from pydantic import BaseModel, Field

from aiwatchdogs.cli.config import (
    DEFAULT_PAID_MAX_TASKS,
    AgentConfig,
    get_spend_ledger_path,
    normalize_hub_origin,
    save_config,
    validate_hub_url,
)
from aiwatchdogs.cli.providers import (
    BudgetTracker,
    ProviderInfo,
    enrich_finding_summary,
    is_paid_provider,
    render_deterministic_summary,
    resolve_provider_config,
)
from aiwatchdogs.connectors.loader import LoadedDataset, load_dataset
from aiwatchdogs.core.safety import compute_sha256, lint_neutrality, scrub_pii, scrub_pii_in_obj
from aiwatchdogs.engines.registry import METHODOLOGY_CATALOG, run_engine

SEVERITY_RANK: dict[str, int] = {
    "critical": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
}

MATH_TOLERANCE = 0.01  # 1% relative tolerance for independent math reproduction
PROJECT_ROOT = Path(__file__).resolve().parents[3]

CHECK_PASS = "pass"
CHECK_FAIL = "fail"
CHECK_UNRECORDED = "unrecorded"

MAX_HUB_RESPONSE_BYTES = 5 * 1024 * 1024  # 5 MiB cap on Hub JSON responses
# Sent with every Hub request so the Hub can tell an out-of-date official CLI to refresh.
CLI_USER_AGENT = "AIWatchdogs-India-AgentCLI/0.7"
# How patiently the runner waits out the Hub's per-IP rate limit (many nodes can share one address).
MAX_RATE_LIMIT_WAITS = 12
MAX_RATE_LIMIT_WAIT_SECONDS = 75.0
MAX_CLAIM_429_BACKOFFS = 4
MAX_TRANSPORT_RETRIES = 3
RETRYABLE_HUB_STATUS = frozenset({502, 503, 504})
# After this many consecutive source-fetch failures in a row the node stops claiming:
# the trouble is likely its own network/firewall, and further claims would only churn the queue.
# A single bad URL (e.g. an upstream HTTP 403 or transient timeout) releases its lease cleanly
# and moves straight on to the next task; the counter resets as soon as any task succeeds.
MAX_CONSECUTIVE_SOURCE_FAILURES = 5
SOURCE_FAILURE_BACKOFF_SECONDS = 1.0
SOURCE_FAILURE_REASONS = frozenset({"network_error", "policy_blocked", "source_http_forbidden"})
ROLE_LABELS = {
    "auditor": "AUDITOR",
    "peer_verifier": "PEER VERIFIER",
    "adjudicator": "ADJUDICATOR",
}

# pypdf logs harmless font-table warnings for some government PDFs; they only confuse a reader.
logging.getLogger("pypdf").setLevel(logging.ERROR)
MAX_INLINE_RECORDS = 5_000
_SAFE_FINDING_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_SAFE_HANDLE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,39}$")
_SHA256_HEX_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _pid_is_alive(pid: int) -> bool:
    """Return True if a local OS process with ``pid`` appears to be running."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    if pid == os.getpid():
        return True
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False

# Strict allowlist of task context keys forwarded to connectors/engines.
# Security-sensitive keys (allow_local_files, use_cache, secondary_path, secondary_records,
# max_download_bytes, max_decompressed_bytes, max_compression_ratio, max_zip_members,
# max_parse_rows, max_pdf_pages, max_pdf_chars) are NEVER forwarded from remote task payloads.
_ALLOWED_TASK_CONTEXT_KEYS: frozenset[str] = frozenset(
    {
        "artifact_title",
        "as_of_date",
        "budget_lines",
        "canonical_url",
        "comparisons",
        "dataset_url",
        "electoral_award_cutoff_inr",
        "escalation_cutoff_pct",
        "expected_sha256",
        "filter_buyer",
        "fiscal_year",
        "fiscal_year_end",
        "fiscal_year_start",
        "flag_re_equals_be",
        "hhi_threshold",
        "jurisdiction",
        "lower_ratio",
        "mad_cutoff",
        "major_contract_inr",
        "march_ceiling_pct",
        "max_leads",
        "max_rows",
        "median_ratio_cutoff",
        "methodology_code",
        "min_cluster_count",
        "min_disclosure_inr",
        "min_escalation_inr",
        "min_group_tenders",
        "min_impact_inr",
        "min_tender_estimate_inr",
        "min_tenders",
        "mirror_catalog_url",
        "mirror_name",
        "new_entity_days",
        "official_publisher",
        "pac_note_threshold_inr",
        "q4_ceiling_pct",
        "retrieval_url",
        "secondary_dataset_url",
        "secondary_document",
        "secondary_expected_sha256",
        "secondary_sha256",
        "secondary_url",
        "share_threshold",
        "short_window_days",
        "single_bid_share_threshold",
        "source_landing_page",
        "source_url",
        "suggested_authority",
        "union_budget",
        "upper_ratio",
        "variance_ceiling_pct",
        "window_days",
    }
)

# Semantic / structural metric keys whose values must agree exactly when present on a reproduced lead.
CRITICAL_SEMANTIC_METRIC_KEYS: frozenset[str] = frozenset(
    {
        "amount_type",
        "join_basis",
        "date_basis",
        "splitting_claim_supported",
        "thresholds_triggered",
        "hhi_triggered",
        "top_supplier_share_triggered",
        "narrow_spread_triggered",
        "exact_identifier_verified",
        "statutory_applicability_verified",
        "governing_instrument",
        "threshold_scope",
        "aggregate_level",
        "comparison_basis",
        "is_final_cost_overrun",
    }
)

# Record-level identifier keys that must match exactly when present on both submitted and candidate leads.
RECORD_IDENTIFIER_KEYS: tuple[str, ...] = (
    "line_key",
    "source_locator",
    "ocid",
    "tender_id",
    "para_number",
    "matched_csr_cin",
    "cin",
    "work_id",
    "package_id",
)


class DigestMismatchFailure(ValueError):
    """Raised when a primary or secondary artifact SHA-256 digest fails verification."""


class UnevaluableSourceFailure(ValueError):
    """Raised when the check could not read any of the figures it was asked to compare."""


def role_of(lease: dict[str, Any]) -> str:
    """The role the Hub assigned with this lease (older Hubs send none; derive it from the task)."""
    role = lease.get("role") if isinstance(lease, dict) else None
    if isinstance(role, dict) and str(role.get("name") or "") in ROLE_LABELS:
        return str(role["name"])
    task = lease.get("task") if isinstance(lease, dict) and isinstance(lease.get("task"), dict) else {}
    if str(task.get("task_type") or "").strip().lower() == "peer_verify":
        payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
        return "adjudicator" if payload.get("review_type") == "adjudication" else "peer_verifier"
    return "auditor"


def _retry_after_seconds(resp: httpx.Response) -> float | None:
    raw = resp.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return max(1.0, min(float(raw.strip()), MAX_RATE_LIMIT_WAIT_SECONDS))
    except ValueError:
        return None


def _is_lease_cap_429(resp: httpx.Response) -> bool:
    """Return True when a 429 is the Hub's explicit concurrent-lease cap (not a transient rate limit)."""
    if resp.status_code != 429 or resp.headers.get("Retry-After") is not None:
        return False
    detail = ""
    try:
        body = resp.json()
        if isinstance(body, dict):
            detail = str(body.get("detail") or "")
    except ValueError:
        detail = resp.text[:300]
    low = detail.lower()
    return "active task lease" in low or "already holds" in low


class RunSummary(BaseModel):
    """Aggregate telemetry returned when an AgentRunner loop finishes."""

    handle: str
    hub_url: str
    profile_url: str
    tasks_claimed: int = 0
    tasks_completed: int = 0
    findings_submitted: int = 0
    cleared_submitted: int = 0
    verifications_submitted: int = 0
    aborted_410: int = 0
    errors: int = 0
    roles: dict[str, int] = Field(default_factory=dict)
    stopped_reason: str = ""
    session_spent_usd: float = 0.0
    budget: dict[str, Any] = Field(default_factory=dict)
    results: list[dict[str, Any]] = Field(default_factory=list)


def _is_number(val: Any) -> bool:
    return isinstance(val, (int, float)) and not isinstance(val, bool) and math.isfinite(float(val))


def _within_tolerance(expected: float, actual: float, rel_tol: float = MATH_TOLERANCE) -> bool:
    diff = abs(float(expected) - float(actual))
    scale = max(abs(float(expected)), abs(float(actual)), 1.0)
    return diff <= max(1e-6, rel_tol * scale)


def _norm_exact(value: Any) -> str:
    """Normalize an identifier/label for exact matching (trim, collapse whitespace, lowercase)."""
    if value is None:
        return ""
    return " ".join(str(value).strip().lower().split())


def _is_http_url(ref: str) -> bool:
    low = ref.strip().lower()
    return low.startswith(("http://", "https://"))


def _resolve_dataset_ref(ref: str, *, allow_local_files: bool = False) -> str:
    """Resolve dataset URL or (only when explicitly allowed) local path."""
    cleaned = ref.strip()
    if _is_http_url(cleaned):
        return cleaned
    if not allow_local_files:
        return cleaned
    direct = Path(cleaned)
    if direct.exists():
        return str(direct)
    from_root = PROJECT_ROOT / cleaned
    if from_root.exists():
        return str(from_root)
    return cleaned


def _source_host(target_ref: str | None) -> str:
    """The host a task's file is downloaded from, for the line shown before the download."""
    try:
        host = urlsplit(str(target_ref or "")).hostname if "://" in str(target_ref or "") else None
    except ValueError:
        host = None
    return host or "public source"


def _extract_target_ref(
    payload: dict[str, Any],
    *,
    allow_local_files: bool = False,
    allow_finding_fallback: bool = True,
) -> str | None:
    """Extract official dataset URL or (when allowed) local file path from a Desk task payload or finding."""
    for key in (
        "retrieval_url",
        "dataset_url",
        "dataset_path",
        "canonical_url",
        "file_path",
        "artifact_path",
        "source_ref",
        "url",
        "source_url",
        "path",
    ):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return _resolve_dataset_ref(val, allow_local_files=allow_local_files)

    for nested_key in ("provenance", "artifact"):
        nested = payload.get(nested_key)
        if isinstance(nested, dict):
            for key in (
                "retrieval_url",
                "dataset_url",
                "canonical_url",
                "source_ref",
                "file_path",
                "dataset_path",
                "url",
                "path",
            ):
                val = nested.get(key)
                if isinstance(val, str) and val.strip():
                    return _resolve_dataset_ref(val, allow_local_files=allow_local_files)

    if not allow_finding_fallback:
        return None

    finding = payload.get("finding")
    if isinstance(finding, dict):
        for key in (
            "retrieval_url",
            "dataset_url",
            "canonical_url",
            "dataset_path",
            "source_ref",
            "artifact_path",
            "source_url",
        ):
            val = finding.get(key)
            if isinstance(val, str) and val.strip():
                return _resolve_dataset_ref(val, allow_local_files=allow_local_files)
        prov = finding.get("provenance")
        if isinstance(prov, dict):
            for key in ("retrieval_url", "dataset_url", "canonical_url", "source_ref"):
                val = prov.get(key)
                if isinstance(val, str) and val.strip():
                    return _resolve_dataset_ref(val, allow_local_files=allow_local_files)
        trace = finding.get("agent_trace")
        if isinstance(trace, dict):
            for key in ("retrieval_url", "dataset_url", "canonical_url", "dataset_path", "source_ref"):
                val = trace.get(key)
                if isinstance(val, str) and val.strip():
                    return _resolve_dataset_ref(val, allow_local_files=allow_local_files)
        citations = finding.get("citations")
        if isinstance(citations, list):
            for cit in citations:
                if isinstance(cit, dict):
                    for key in (
                        "retrieval_url",
                        "dataset_url",
                        "canonical_url",
                        "source_ref",
                        "dataset_path",
                        "file_path",
                        "source_url",
                        "url",
                    ):
                        val = cit.get(key)
                        if isinstance(val, str) and val.strip():
                            return _resolve_dataset_ref(val, allow_local_files=allow_local_files)
    return None


def _extract_secondary_url(
    payload: dict[str, Any],
    *,
    allow_local_files: bool = False,
    allow_finding_fallback: bool = True,
) -> str | None:
    """Extract optional secondary dataset URL (e.g. MCA CSR CSV for M8 Electoral Bonds cross-check)."""
    ctx = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    prov = payload.get("provenance") if isinstance(payload.get("provenance"), dict) else {}
    candidates: list[Any] = [
        payload.get("secondary_url"),
        payload.get("secondary_dataset_url"),
        ctx.get("secondary_url"),
        ctx.get("secondary_dataset_url"),
        prov.get("secondary_url"),
    ]
    if allow_finding_fallback:
        finding = payload.get("finding")
        if isinstance(finding, dict):
            candidates.append(finding.get("secondary_url"))
            f_prov = finding.get("provenance")
            if isinstance(f_prov, dict):
                candidates.append(f_prov.get("secondary_url"))
            trace = finding.get("agent_trace")
            if isinstance(trace, dict):
                candidates.append(trace.get("secondary_url"))
            citations = finding.get("citations")
            if isinstance(citations, list):
                for cit in citations:
                    if isinstance(cit, dict):
                        candidates.append(cit.get("secondary_url"))
    for val in candidates:
        if isinstance(val, str) and val.strip():
            return _resolve_dataset_ref(val, allow_local_files=allow_local_files)
    return None


def _extract_expected_sha256(payload: dict[str, Any], finding: dict[str, Any] | None = None) -> str:
    """Extract expected primary artifact SHA-256 from task payload or finding manifest."""
    ctx = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    f_obj = finding if isinstance(finding, dict) else (
        payload.get("finding") if isinstance(payload.get("finding"), dict) else {}
    )
    f_prov = f_obj.get("provenance") if isinstance(f_obj.get("provenance"), dict) else {}
    f_trace = f_obj.get("agent_trace") if isinstance(f_obj.get("agent_trace"), dict) else {}

    candidates = [
        payload.get("expected_sha256"),
        ctx.get("expected_sha256"),
        payload.get("artifact_sha256"),
        f_obj.get("expected_sha256"),
        f_obj.get("artifact_sha256"),
        f_prov.get("sha256"),
        f_prov.get("observed_sha256"),
        f_trace.get("artifact_sha256"),
    ]
    for cit in f_obj.get("citations") or []:
        if isinstance(cit, dict) and cit.get("sha256"):
            candidates.append(cit["sha256"])

    for val in candidates:
        if isinstance(val, str) and val.strip():
            return val.strip().lower()
    return ""


def _extract_expected_secondary_sha256(payload: dict[str, Any], finding: dict[str, Any] | None = None) -> str:
    """Extract expected secondary artifact SHA-256 from task payload or finding manifest."""
    ctx = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    f_obj = finding if isinstance(finding, dict) else (
        payload.get("finding") if isinstance(payload.get("finding"), dict) else {}
    )
    f_prov = f_obj.get("provenance") if isinstance(f_obj.get("provenance"), dict) else {}
    f_trace = f_obj.get("agent_trace") if isinstance(f_obj.get("agent_trace"), dict) else {}

    candidates = [
        payload.get("secondary_expected_sha256"),
        payload.get("secondary_sha256"),
        ctx.get("secondary_expected_sha256"),
        ctx.get("secondary_sha256"),
        f_obj.get("secondary_expected_sha256"),
        f_obj.get("secondary_sha256"),
        f_prov.get("secondary_sha256"),
        f_trace.get("secondary_sha256"),
    ]
    for cit in f_obj.get("citations") or []:
        if isinstance(cit, dict) and cit.get("secondary_sha256"):
            candidates.append(cit["secondary_sha256"])

    for val in candidates:
        if isinstance(val, str) and val.strip():
            return val.strip().lower()
    return ""


def _extract_observed_secondary_sha256(dataset: LoadedDataset) -> str:
    """Extract observed secondary artifact SHA-256 from LoadedDataset provenance or joined records."""
    sec_sha = getattr(dataset, "secondary_sha256", None)
    if isinstance(sec_sha, str) and sec_sha.strip():
        return sec_sha.strip().lower()
    sec_prov = getattr(dataset, "secondary_provenance", None)
    if isinstance(sec_prov, dict):
        for key in ("observed_sha256", "sha256"):
            val = sec_prov.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip().lower()
    prov = getattr(dataset, "provenance", None)
    if isinstance(prov, dict):
        val = prov.get("secondary_sha256")
        if isinstance(val, str) and val.strip():
            return val.strip().lower()
    for rec in dataset.records[:25]:
        if isinstance(rec, dict):
            for key in ("secondary_sha256", "csr_source_sha256"):
                val = rec.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip().lower()
    return ""


def _extract_as_of_date(
    payload: dict[str, Any],
    finding: dict[str, Any] | None = None,
    *,
    default_today_utc: bool = False,
) -> str | None:
    """Extract deterministic ISO as_of_date from finding/task manifest."""
    ctx = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    f_obj = finding if isinstance(finding, dict) else (
        payload.get("finding") if isinstance(payload.get("finding"), dict) else {}
    )
    f_trace = f_obj.get("agent_trace") if isinstance(f_obj.get("agent_trace"), dict) else {}
    f_metrics = f_obj.get("anomaly_metrics") if isinstance(f_obj.get("anomaly_metrics"), dict) else {}

    for val in (
        f_obj.get("as_of_date"),
        f_trace.get("as_of_date"),
        f_metrics.get("as_of_date"),
        payload.get("as_of_date"),
        ctx.get("as_of_date"),
    ):
        if isinstance(val, str) and val.strip():
            return val.strip()[:10]

    if default_today_utc:
        return datetime.now(timezone.utc).date().isoformat()
    return None


def _build_task_context(
    payload: dict[str, Any],
    *,
    allow_local_files: bool = False,
    as_of_date: str | None = None,
    allow_finding_fallback: bool = True,
) -> dict[str, Any]:
    """Assemble strictly allowlisted engine/loader context hints from a Desk task payload."""
    ctx: dict[str, Any] = {}
    nested = payload.get("context")
    if isinstance(nested, dict):
        for k, v in nested.items():
            if k in _ALLOWED_TASK_CONTEXT_KEYS:
                ctx[k] = v

    finding = (
        payload.get("finding")
        if allow_finding_fallback and isinstance(payload.get("finding"), dict)
        else {}
    )
    for key in (
        "retrieval_url",
        "dataset_url",
        "source_url",
        "canonical_url",
        "official_publisher",
        "source_landing_page",
        "mirror_name",
        "mirror_catalog_url",
        "jurisdiction",
        "fiscal_year",
        "fiscal_year_start",
        "fiscal_year_end",
        "artifact_title",
        "methodology_code",
        "max_rows",
        "filter_buyer",
        "expected_sha256",
        "secondary_expected_sha256",
        "secondary_sha256",
        "as_of_date",
    ):
        val = payload.get(key)
        if val is None and isinstance(finding, dict):
            val = finding.get(key)
        if val is not None and key not in ctx:
            ctx[key] = val

    # Clamp numeric row/lead caps to safe upper bounds
    if "max_rows" in ctx:
        try:
            ctx["max_rows"] = max(1, min(int(ctx["max_rows"]), 50_000))
        except (TypeError, ValueError):
            ctx.pop("max_rows", None)
    if "max_leads" in ctx:
        try:
            ctx["max_leads"] = max(1, min(int(ctx["max_leads"]), 50))
        except (TypeError, ValueError):
            ctx.pop("max_leads", None)

    secondary = _extract_secondary_url(
        payload,
        allow_local_files=allow_local_files,
        allow_finding_fallback=allow_finding_fallback,
    )
    if secondary:
        ctx["secondary_url"] = secondary

    expected_sec = _extract_expected_secondary_sha256(payload, finding if finding else None)
    if expected_sec:
        ctx["secondary_expected_sha256"] = expected_sec
        ctx.setdefault("secondary_sha256", expected_sec)

    if as_of_date:
        ctx["as_of_date"] = as_of_date

    # Defense-in-depth: never allow untrusted context keys to escalate local/cache/limit settings
    for forbidden in ("allow_local_files", "use_cache", "secondary_path", "secondary_records"):
        ctx.pop(forbidden, None)

    return ctx


def _select_top_lead(leads: list[Any]) -> Any:
    return max(
        leads,
        key=lambda lead: (
            SEVERITY_RANK.get(str(getattr(lead, "severity", "low")).lower(), 0),
            float(getattr(lead, "monetary_exposure_inr", 0.0) or 0.0),
        ),
    )


def _extract_source_locator(lead: Any, dataset: LoadedDataset | None = None) -> str | None:
    loc = getattr(lead, "source_locator", None)
    if isinstance(loc, str) and loc.strip():
        return loc.strip()
    citations = getattr(lead, "citations", None) or []
    for cit in citations:
        if isinstance(cit, dict):
            for key in ("source_locator", "locator", "page", "row_range", "record_id", "ocid", "tender_id"):
                val = cit.get(key)
                if val is not None and str(val).strip():
                    if key == "page":
                        return f"page:{val}"
                    return str(val).strip()
    if dataset is not None:
        ds_loc = getattr(dataset, "source_locator", None)
        if isinstance(ds_loc, str) and ds_loc.strip():
            return ds_loc.strip()
    return None


def _scrub_citations(
    citations: list[dict[str, Any]],
    dataset: LoadedDataset,
    *,
    canonical_url: str | None = None,
    source_url: str | None = None,
    secondary_url: str | None = None,
    secondary_sha256: str | None = None,
) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    ref_url = canonical_url or dataset.source_ref
    for item in citations:
        if not isinstance(item, dict):
            continue
        copy_item, _ = scrub_pii_in_obj(dict(item))
        copy_item.setdefault("sha256", dataset.sha256)
        copy_item.setdefault("source_ref", ref_url)
        if canonical_url:
            copy_item.setdefault("canonical_url", canonical_url)
            if _is_http_url(canonical_url):
                copy_item.setdefault("dataset_url", canonical_url)
        if source_url:
            copy_item.setdefault("source_url", source_url)
        if secondary_url:
            copy_item.setdefault("secondary_url", secondary_url)
        if secondary_sha256:
            copy_item.setdefault("secondary_sha256", secondary_sha256)
        cleaned.append(copy_item)
    if not cleaned:
        entry: dict[str, Any] = {
            "source_ref": ref_url,
            "source_url": source_url or ref_url,
            "sha256": dataset.sha256,
            "excerpt": f"Deterministic audit over {len(dataset.records):,} public records.",
        }
        if canonical_url:
            entry["canonical_url"] = canonical_url
            if _is_http_url(canonical_url):
                entry["dataset_url"] = canonical_url
        if secondary_url:
            entry["secondary_url"] = secondary_url
        if secondary_sha256:
            entry["secondary_sha256"] = secondary_sha256
        cleaned.append(entry)
    return cleaned


def _extract_fiscal_window(obj: Any) -> tuple[str, str, str]:
    """Extract normalized (fiscal_year, fiscal_year_start, fiscal_year_end) from finding dict or lead model."""
    if isinstance(obj, dict):
        metrics = obj.get("anomaly_metrics") if isinstance(obj.get("anomaly_metrics"), dict) else {}
        fy = _norm_exact(obj.get("fiscal_year"))
        fy_start = _norm_exact(obj.get("fiscal_year_start") or metrics.get("fiscal_year_start"))
        fy_end = _norm_exact(obj.get("fiscal_year_end") or metrics.get("fiscal_year_end"))
    else:
        metrics = getattr(obj, "anomaly_metrics", None) or {}
        if not isinstance(metrics, dict):
            metrics = {}
        fy = _norm_exact(getattr(obj, "fiscal_year", ""))
        fy_start = _norm_exact(getattr(obj, "fiscal_year_start", None) or metrics.get("fiscal_year_start"))
        fy_end = _norm_exact(getattr(obj, "fiscal_year_end", None) or metrics.get("fiscal_year_end"))
    return fy, fy_start, fy_end


def _extract_record_identifiers(obj: Any) -> dict[str, str]:
    """Extract non-empty record-level identifiers from a finding dict or candidate lead."""
    ids: dict[str, str] = {}
    if isinstance(obj, dict):
        metrics = obj.get("anomaly_metrics") if isinstance(obj.get("anomaly_metrics"), dict) else {}
        for key in RECORD_IDENTIFIER_KEYS:
            val = obj.get(key) if obj.get(key) is not None else metrics.get(key)
            norm = _norm_exact(val)
            if norm:
                ids[key] = norm
    else:
        metrics = getattr(obj, "anomaly_metrics", None) or {}
        if not isinstance(metrics, dict):
            metrics = {}
        for key in RECORD_IDENTIFIER_KEYS:
            val = getattr(obj, key, None)
            if val is None:
                val = metrics.get(key)
            norm = _norm_exact(val)
            if norm:
                ids[key] = norm
    return ids


def _compare_complete_metric_set(
    submitted: dict[str, Any],
    candidate: Any,
    *,
    rel_tol: float = MATH_TOLERANCE,
) -> tuple[str, dict[str, Any]]:
    """Strictly verify the complete required metric set (NO partial-overlap passing!)."""
    sub_metrics = submitted.get("anomaly_metrics")
    cand_metrics = getattr(candidate, "anomaly_metrics", None) or {}
    if not isinstance(cand_metrics, dict):
        cand_metrics = {}

    details: dict[str, Any] = {
        "matched_numeric_keys": [],
        "diverged_numeric_keys": [],
        "missing_in_submitted": [],
        "missing_in_reproduced": [],
        "type_or_null_mismatches": [],
        "semantic_mismatches": [],
        "monetary_exposure_matched": None,
    }

    if not isinstance(sub_metrics, dict) or len(sub_metrics) == 0:
        return CHECK_UNRECORDED, details

    # 1. Monetary exposure must be present and within tolerance
    sub_mon = submitted.get("monetary_exposure_inr")
    cand_mon = getattr(candidate, "monetary_exposure_inr", None)
    if _is_number(cand_mon):
        if not _is_number(sub_mon):
            details["monetary_exposure_matched"] = False
            details["missing_in_submitted"].append("monetary_exposure_inr")
        elif _within_tolerance(float(sub_mon), float(cand_mon), rel_tol=rel_tol):
            details["monetary_exposure_matched"] = True
        else:
            details["monetary_exposure_matched"] = False
            details["diverged_numeric_keys"].append(
                {
                    "key": "monetary_exposure_inr",
                    "submitted": float(sub_mon),
                    "reproduced": float(cand_mon),
                }
            )

    # 2. Complete numeric metric union: every numeric metric in submitted AND reproduced must exist on both sides and match
    all_keys = sorted(set(sub_metrics.keys()) | set(cand_metrics.keys()))
    for key in all_keys:
        in_sub = key in sub_metrics
        in_cand = key in cand_metrics
        sval = sub_metrics.get(key)
        cval = cand_metrics.get(key)

        s_is_num = _is_number(sval)
        c_is_num = _is_number(cval)

        if c_is_num and not in_sub:
            details["missing_in_submitted"].append(key)
            continue
        if s_is_num and not in_cand:
            details["missing_in_reproduced"].append(key)
            continue
        if (s_is_num and not c_is_num) or (c_is_num and not s_is_num):
            details["type_or_null_mismatches"].append(
                {"key": key, "submitted": sval, "reproduced": cval}
            )
            continue
        if s_is_num and c_is_num:
            if _within_tolerance(float(sval), float(cval), rel_tol=rel_tol):
                details["matched_numeric_keys"].append(key)
            else:
                details["diverged_numeric_keys"].append(
                    {"key": key, "submitted": float(sval), "reproduced": float(cval)}
                )
            continue

        # 3. Critical boolean/semantic/nullability fields must match exactly
        if key in CRITICAL_SEMANTIC_METRIC_KEYS:
            if in_cand and not in_sub:
                details["missing_in_submitted"].append(key)
            elif in_sub and in_cand:
                if isinstance(sval, bool) or isinstance(cval, bool):
                    if bool(sval) is not bool(cval) or type(sval) is not type(cval):
                        details["semantic_mismatches"].append(
                            {"key": key, "submitted": sval, "reproduced": cval}
                        )
                elif isinstance(sval, list) or isinstance(cval, list):
                    if not (isinstance(sval, list) and isinstance(cval, list)):
                        details["semantic_mismatches"].append(
                            {"key": key, "submitted": sval, "reproduced": cval}
                        )
                    elif sorted(_norm_exact(x) for x in sval) != sorted(_norm_exact(x) for x in cval):
                        details["semantic_mismatches"].append(
                            {"key": key, "submitted": sval, "reproduced": cval}
                        )
                elif _norm_exact(sval) != _norm_exact(cval):
                    details["semantic_mismatches"].append(
                        {"key": key, "submitted": sval, "reproduced": cval}
                    )

    has_failure = bool(
        details["diverged_numeric_keys"]
        or details["missing_in_submitted"]
        or details["missing_in_reproduced"]
        or details["type_or_null_mismatches"]
        or details["semantic_mismatches"]
        or details["monetary_exposure_matched"] is False
    )
    if has_failure:
        return CHECK_FAIL, details

    # Require at least one verified numeric metric or verified monetary exposure
    if not details["matched_numeric_keys"] and details["monetary_exposure_matched"] is not True:
        return CHECK_UNRECORDED, details

    return CHECK_PASS, details


def _match_reproduced_lead_strict(
    submitted: dict[str, Any],
    reproduced_leads: list[Any],
    *,
    submitted_methodology: str,
    repro_engine_version: str | None,
    repro_ruleset_version: str | None,
    repro_script_hash: str | None,
) -> dict[str, Any]:
    """Strictly match a submitted finding against reproduced leads (zero fuzzy substring matching).

    Requires exact match on:
    - methodology_code & rule_triggered
    - entity_name (and record identifiers when present)
    - fiscal period / window (fiscal_year, fiscal_year_start, fiscal_year_end)
    - engine & ruleset version (and script_hash when recorded)
    - complete required metric set within 1% tolerance
    """
    sub_entity = _norm_exact(submitted.get("entity_name"))
    sub_rule = _norm_exact(submitted.get("rule_triggered"))
    sub_method = submitted_methodology.strip().upper()
    sub_fy, sub_fy_start, sub_fy_end = _extract_fiscal_window(submitted)
    sub_ids = _extract_record_identifiers(submitted)

    trace = submitted.get("agent_trace") if isinstance(submitted.get("agent_trace"), dict) else {}
    sub_engine_ver = _norm_exact(submitted.get("engine_version") or trace.get("engine_version"))
    sub_ruleset_ver = _norm_exact(submitted.get("ruleset_version") or trace.get("ruleset_version"))
    sub_script_hash = _norm_exact(submitted.get("verifier_script_hash") or trace.get("verifier_script_hash") or trace.get("script_hash"))

    reasons: list[str] = []

    if not reproduced_leads:
        return {
            "matched_lead": None,
            "deterministic_reproduction": CHECK_FAIL,
            "metric_tolerance": CHECK_FAIL,
            "reasons": ["Independent deterministic engine re-run produced 0 leads."],
            "reproduced_summary": {},
            "metric_comparison": {},
        }

    if not sub_entity or not sub_rule:
        return {
            "matched_lead": None,
            "deterministic_reproduction": CHECK_UNRECORDED,
            "metric_tolerance": CHECK_UNRECORDED,
            "reasons": ["Submitted finding omits required entity_name or rule_triggered binding."],
            "reproduced_summary": {},
            "metric_comparison": {},
        }

    # Exact identity filter: methodology + entity + rule
    identity_candidates: list[Any] = []
    for cand in reproduced_leads:
        cand_method = str(getattr(cand, "methodology_code", "")).strip().upper()
        cand_entity = _norm_exact(getattr(cand, "entity_name", ""))
        cand_rule = _norm_exact(getattr(cand, "rule_triggered", ""))
        if cand_method == sub_method and cand_entity == sub_entity and cand_rule == sub_rule:
            identity_candidates.append(cand)

    if not identity_candidates:
        return {
            "matched_lead": None,
            "deterministic_reproduction": CHECK_FAIL,
            "metric_tolerance": CHECK_FAIL,
            "reasons": [
                f"No reproduced lead matched exact entity={submitted.get('entity_name')!r}, "
                f"methodology={sub_method!r}, rule={submitted.get('rule_triggered')!r}."
            ],
            "reproduced_summary": {},
            "metric_comparison": {},
        }

    # Exact fiscal period / window filter
    period_candidates: list[Any] = []
    period_unrecorded = False
    for cand in identity_candidates:
        cand_fy, cand_fy_start, cand_fy_end = _extract_fiscal_window(cand)
        if not sub_fy and not sub_fy_start and not sub_fy_end:
            period_unrecorded = True
            period_candidates.append(cand)
            continue
        fy_ok = (not sub_fy) or (cand_fy == sub_fy)
        start_ok = (not sub_fy_start) or (cand_fy_start == sub_fy_start)
        end_ok = (not sub_fy_end) or (cand_fy_end == sub_fy_end)
        if fy_ok and start_ok and end_ok:
            period_candidates.append(cand)

    if not period_candidates:
        return {
            "matched_lead": None,
            "deterministic_reproduction": CHECK_FAIL,
            "metric_tolerance": CHECK_FAIL,
            "reasons": [
                f"Fiscal period mismatch for entity={submitted.get('entity_name')!r} "
                f"(submitted fiscal_year={submitted.get('fiscal_year')!r}, "
                f"window={sub_fy_start or '?'}..{sub_fy_end or '?'})."
            ],
            "reproduced_summary": {},
            "metric_comparison": {},
        }

    # Exact record-level identifier filter when present on both sides
    final_candidates: list[Any] = []
    for cand in period_candidates:
        cand_ids = _extract_record_identifiers(cand)
        mismatch = False
        for k, s_val in sub_ids.items():
            if k in cand_ids and cand_ids[k] != s_val:
                mismatch = True
                break
        if not mismatch:
            final_candidates.append(cand)

    if not final_candidates:
        return {
            "matched_lead": None,
            "deterministic_reproduction": CHECK_FAIL,
            "metric_tolerance": CHECK_FAIL,
            "reasons": [
                f"Record identifier/source_locator mismatch for entity={submitted.get('entity_name')!r}."
            ],
            "reproduced_summary": {},
            "metric_comparison": {},
        }

    # Select candidate if multiple share identical entity+rule+period (prefer exact metric pass)
    best = final_candidates[0]
    best_metric_state, best_metric_details = _compare_complete_metric_set(submitted, best)
    if len(final_candidates) > 1 and best_metric_state != CHECK_PASS:
        for alt in final_candidates[1:]:
            alt_state, alt_details = _compare_complete_metric_set(submitted, alt)
            if alt_state == CHECK_PASS:
                best = alt
                best_metric_state, best_metric_details = alt_state, alt_details
                break

    # Verify exact engine & ruleset version binding
    cand_ruleset = _norm_exact(getattr(best, "ruleset_version", None) or repro_ruleset_version)
    actual_engine_ver = _norm_exact(repro_engine_version)
    actual_script_hash = _norm_exact(repro_script_hash)

    version_state = CHECK_PASS
    if sub_engine_ver and actual_engine_ver and sub_engine_ver != actual_engine_ver:
        version_state = CHECK_FAIL
        reasons.append(
            f"Engine version mismatch (submitted {sub_engine_ver!r}, reproduced {actual_engine_ver!r})."
        )
    if sub_ruleset_ver and cand_ruleset and sub_ruleset_ver != cand_ruleset:
        version_state = CHECK_FAIL
        reasons.append(
            f"Ruleset version mismatch (submitted {sub_ruleset_ver!r}, reproduced {cand_ruleset!r})."
        )
    if sub_script_hash and actual_script_hash and sub_script_hash != actual_script_hash:
        version_state = CHECK_FAIL
        reasons.append(
            f"Engine script fingerprint mismatch (submitted {sub_script_hash[:12]}, reproduced {actual_script_hash[:12]})."
        )

    # Require explicit version binding on submitted finding to attest full deterministic reproduction
    if version_state == CHECK_PASS:
        has_version_binding = bool(
            (sub_engine_ver and (sub_ruleset_ver or not cand_ruleset))
            or sub_script_hash
        )
        if not has_version_binding:
            version_state = CHECK_UNRECORDED
            reasons.append("Submitted finding omits engine_version/ruleset_version binding.")

    if version_state == CHECK_FAIL:
        repro_state = CHECK_FAIL
    elif period_unrecorded or version_state == CHECK_UNRECORDED:
        repro_state = CHECK_UNRECORDED
        if period_unrecorded:
            reasons.append("Submitted finding omits fiscal_year/period binding.")
    else:
        repro_state = CHECK_PASS

    if best_metric_state == CHECK_FAIL:
        if best_metric_details.get("diverged_numeric_keys"):
            reasons.append("Reproduced numeric metrics diverged beyond 1% tolerance.")
        if best_metric_details.get("missing_in_submitted"):
            reasons.append(
                f"Submitted finding omitted required metrics: {', '.join(best_metric_details['missing_in_submitted'][:6])}."
            )
        if best_metric_details.get("missing_in_reproduced"):
            reasons.append(
                f"Submitted metrics absent from reproduction: {', '.join(best_metric_details['missing_in_reproduced'][:6])}."
            )
        if best_metric_details.get("type_or_null_mismatches") or best_metric_details.get("semantic_mismatches"):
            reasons.append("Metric nullability or semantic field labels diverged from reproduction.")
    elif best_metric_state == CHECK_UNRECORDED:
        reasons.append("Submitted finding omitted required structured anomaly_metrics.")

    cand_fy, cand_fy_start, cand_fy_end = _extract_fiscal_window(best)
    reproduced_summary = {
        "entity_name": getattr(best, "entity_name", ""),
        "rule_triggered": getattr(best, "rule_triggered", ""),
        "fiscal_year": getattr(best, "fiscal_year", ""),
        "fiscal_year_start": getattr(best, "fiscal_year_start", None) or (cand_fy_start or None),
        "fiscal_year_end": getattr(best, "fiscal_year_end", None) or (cand_fy_end or None),
        "source_locator": _extract_source_locator(best),
        "ruleset_version": getattr(best, "ruleset_version", None) or repro_ruleset_version,
        "monetary_exposure_inr": float(getattr(best, "monetary_exposure_inr", 0.0) or 0.0),
        "anomaly_metrics": getattr(best, "anomaly_metrics", {}) or {},
        "metric_comparison": best_metric_details,
    }

    return {
        "matched_lead": best,
        "deterministic_reproduction": repro_state,
        "metric_tolerance": best_metric_state,
        "reasons": reasons,
        "reproduced_summary": reproduced_summary,
        "metric_comparison": best_metric_details,
    }


def _match_reproduced_lead(
    submitted: dict[str, Any],
    reproduced_leads: list[Any],
) -> tuple[Any | None, bool, dict[str, Any]]:
    """Backwards-compatible strict wrapper returning (matched_lead, metrics_passed, reproduced_summary)."""
    method = str(submitted.get("methodology_code", "")).strip().upper()
    trace = submitted.get("agent_trace") if isinstance(submitted.get("agent_trace"), dict) else {}
    res = _match_reproduced_lead_strict(
        submitted,
        reproduced_leads,
        submitted_methodology=method,
        repro_engine_version=str(submitted.get("engine_version") or trace.get("engine_version") or "") or None,
        repro_ruleset_version=str(submitted.get("ruleset_version") or trace.get("ruleset_version") or "") or None,
        repro_script_hash=str(trace.get("verifier_script_hash") or "") or None,
    )
    ok = (
        res["matched_lead"] is not None
        and res["deterministic_reproduction"] != CHECK_FAIL
        and res["metric_tolerance"] == CHECK_PASS
    )
    return res["matched_lead"], ok, res["reproduced_summary"]


def _is_digest_mismatch_exception(exc: Exception) -> bool:
    if isinstance(exc, DigestMismatchFailure):
        return True
    name = type(exc).__name__.lower()
    if "digestmismatch" in name or "checksum" in name:
        return True
    msg = str(exc).lower()
    return "sha-256" in msg and "mismatch" in msg


class AgentRunner:
    """Outbound HTTPS Desk runner implementing claim -> heartbeat -> audit/verify -> submit."""

    def __init__(
        self,
        config: AgentConfig,
        *,
        provider_cfg: ProviderInfo | None = None,
        http_client: httpx.Client | None = None,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
        poll_interval: float = 2.0,
        request_timeout: float = 30.0,
        config_path: str | Path | None = None,
        allow_local_files: bool = False,
        allow_insecure_http: bool = False,
        budget_tracker: BudgetTracker | None = None,
    ) -> None:
        self.config = config
        self.allow_local_files = bool(allow_local_files)
        if http_client is None:
            validate_hub_url(config.normalized_hub_url, allow_insecure_http=allow_insecure_http)
        self.provider_cfg = provider_cfg or resolve_provider_config(
            config.provider,
            config.model,
            probe_local=False,
            allow_paid_provider=config.allow_paid_provider,
        )
        self._external_client = http_client is not None
        self._client = http_client or httpx.Client(
            base_url=config.normalized_hub_url,
            timeout=httpx.Timeout(request_timeout),
            headers={"User-Agent": CLI_USER_AGENT},
            follow_redirects=False,
        )
        self.on_event = on_event
        self.poll_interval = poll_interval
        self.request_timeout = request_timeout
        self.config_path = config_path

        ledger_path = get_spend_ledger_path(config_path) if config_path is not None else None
        self.budget_tracker = budget_tracker or BudgetTracker(
            session_max_usd=config.max_usd,
            daily_max_usd=config.effective_daily_max_usd,
            allow_paid_provider=config.allow_paid_provider,
            ledger_path=ledger_path,
        )

        self._task_started: float | None = None
        self._task_input_tokens = 0
        self._task_output_tokens = 0
        self._sleep: Callable[[float], None] = time.sleep
        self._lease_note_enabled = True
        self.last_summary: RunSummary | None = None

    # ------------------------------------------------------------------
    # Lease note: lets the next run hand back a lease an interrupted run left behind
    # ------------------------------------------------------------------

    def _lease_note_path(self) -> Path | None:
        if self.config_path is None or not self._lease_note_enabled:
            return None
        return Path(f"{self.config_path}.lease")

    def _note_lease(self, lease_id: str) -> None:
        path = self._lease_note_path()
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(
                    {
                        "hub": self.config.normalized_hub_origin,
                        "handle": self.config.handle,
                        "lease_id": lease_id,
                        "pid": os.getpid(),
                    },
                    fh,
                )
        except OSError:
            pass

    def _clear_lease_note(self, *, lease_id: str | None = None) -> None:
        path = self._lease_note_path()
        if path is None:
            return
        try:
            if path.is_file():
                try:
                    note = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    note = None
                if isinstance(note, dict):
                    owner_pid = note.get("pid")
                    if (
                        isinstance(owner_pid, int)
                        and not isinstance(owner_pid, bool)
                        and owner_pid != os.getpid()
                        and _pid_is_alive(owner_pid)
                    ):
                        # Another live process using the same handle owns this lease note; never delete it.
                        return
                    if lease_id and str(note.get("lease_id") or "").strip() not in ("", lease_id):
                        return
                path.unlink()
        except OSError:
            pass

    def recover_interrupted_lease(self) -> bool:
        """Release the lease a previous run of this handle was holding when it was killed, if any."""
        path = self._lease_note_path()
        if path is None or not path.is_file() or not self.config.api_token:
            return False
        try:
            note = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            note = None
        if isinstance(note, dict):
            owner_pid = note.get("pid")
            if (
                isinstance(owner_pid, int)
                and not isinstance(owner_pid, bool)
                and owner_pid != os.getpid()
                and _pid_is_alive(owner_pid)
            ):
                # Do not revoke a lease held by a sibling agent still running with the same handle.
                self._emit("lease_note_active_sibling", {"pid": owner_pid, "lease_id": note.get("lease_id")})
                return False
        self._clear_lease_note()
        if not isinstance(note, dict):
            return False
        lease_id = str(note.get("lease_id") or "").strip()
        same_identity = (
            str(note.get("hub") or "") == self.config.normalized_hub_origin
            and str(note.get("handle") or "") == self.config.handle
        )
        if not lease_id or len(lease_id) > 128 or not same_identity:
            return False
        released = self.release(lease_id, reason="interrupted", notes="Earlier run was interrupted; returning the task.")
        if released is not None:
            self._emit("lease_recovered", {"lease_id": lease_id})
        return released is not None

    # ------------------------------------------------------------------
    # Hub transport: wait out rate limits, retry short outages
    # ------------------------------------------------------------------

    def _hub_send(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        what: str = "request",
        rate_limit_waits: int = MAX_RATE_LIMIT_WAITS,
        transport_retries: int = MAX_TRANSPORT_RETRIES,
        retry_read_errors: bool = True,
    ) -> httpx.Response:
        """Send one Hub request, waiting out HTTP 429 (Retry-After) and retrying brief outages.

        Many volunteer nodes can sit behind one public address and share its rate limit, so a 429
        with ``Retry-After`` is normal traffic control, not a failure. A 429 without that header is
        a real refusal (for example the two-lease cap) and is returned at once.
        """
        waited = 0
        retried = 0
        while True:
            try:
                resp = self._client.request(
                    method,
                    self._url(path),
                    headers=headers,
                    json=json_body,
                    **self._request_kwargs(),
                )
            except httpx.TransportError as exc:
                connect_phase = isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout))
                if retried >= transport_retries or not (retry_read_errors or connect_phase):
                    raise
                retried += 1
                delay = min(20.0, 2.0 * (2 ** (retried - 1))) + random.uniform(0.0, 1.0)
                self._emit("hub_retry", {"what": what, "reason": type(exc).__name__, "delay": round(delay, 1)})
                self._sleep(delay)
                continue
            if resp.status_code == 429:
                if _is_lease_cap_429(resp):
                    return resp
                if waited >= rate_limit_waits:
                    return resp
                pause = _retry_after_seconds(resp)
                if pause is None:
                    pause = min(MAX_RATE_LIMIT_WAIT_SECONDS, 2.0 * (2 ** waited))
                waited += 1
                delay = pause + random.uniform(0.2, 3.0)  # spread nodes that share one address
                self._emit("hub_retry", {"what": what, "reason": "rate_limited", "delay": round(delay, 1)})
                self._sleep(delay)
                continue
            if resp.status_code in RETRYABLE_HUB_STATUS and retried < transport_retries and retry_read_errors:
                retried += 1
                delay = min(20.0, 2.0 * (2 ** (retried - 1))) + random.uniform(0.0, 1.0)
                self._emit("hub_retry", {"what": what, "reason": f"HTTP {resp.status_code}", "delay": round(delay, 1)})
                self._sleep(delay)
                continue
            return resp

    def close(self) -> None:
        if not self._external_client:
            self._client.close()

    def __enter__(self) -> AgentRunner:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _emit(self, event: str, data: dict[str, Any]) -> None:
        if self.on_event is not None:
            self.on_event(event, data)

    def _url(self, path: str) -> str:
        if self._external_client and str(self._client.base_url):
            return path
        return f"{self.config.normalized_hub_url}{path}"

    def _auth_headers(self, *, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.config.api_token:
            headers["X-Agent-Token"] = self.config.api_token
        if extra:
            headers.update(extra)
        return headers

    def _request_kwargs(self) -> dict[str, Any]:
        return {} if self._external_client else {"timeout": self.request_timeout}

    def _parse_hub_json(self, resp: httpx.Response) -> Any:
        if len(resp.content) > MAX_HUB_RESPONSE_BYTES:
            raise ValueError(f"Hub response exceeded maximum size limit ({MAX_HUB_RESPONSE_BYTES} bytes).")
        return resp.json()

    def register(
        self,
        *,
        display_name: str | None = None,
        region: str = "IN",
        save: bool = True,
    ) -> dict[str, Any]:
        """Register/reconnect handle with Hub and atomically persist config ONLY on HTTP 2xx."""
        normalized_handle = self.config.handle.strip().lstrip("@").lower()
        self.config.handle = normalized_handle
        payload = {
            "handle": normalized_handle,
            "display_name": display_name or normalized_handle,
            "provider": self.config.provider,
            "model": self.config.model,
            "mode": self.config.mode,
            "region": region,
            "capabilities": ["anomaly_scan", "peer_verify", "deterministic_audit"],
            "coding_agent": getattr(self.config, "coding_agent", "") or "",
            "coding_agent_model": getattr(self.config, "coding_agent_model", "") or "",
        }
        if self.config.api_token:
            headers = self._auth_headers()
        else:
            # Attach a high-entropy per-call registration_key so a dropped first reply can be safely
            # retried and recovered with a fresh token without stranding the handle.
            reg_key = f"reg-{secrets.token_urlsafe(24)}"
            payload["registration_key"] = reg_key
            headers = {"Accept": "application/json", "Idempotency-Key": reg_key}
        resp = self._hub_send(
            "POST",
            "/api/agents/register",
            headers=headers,
            json_body=payload,
            what="register",
            retry_read_errors=True,
        )
        resp.raise_for_status()
        data = self._parse_hub_json(resp)
        if not isinstance(data, dict):
            raise ValueError("Hub registration response must be a JSON object.")

        token = str(data.get("api_token", "")).strip()
        if token:
            self.config.api_token = token
        returned_handle = str(data.get("handle", "")).strip().lower()
        if returned_handle:
            if not _SAFE_HANDLE_RE.fullmatch(returned_handle):
                raise ValueError(f"Hub returned an invalid agent handle: {returned_handle!r}")
            self.config.handle = returned_handle

        raw_profile = str(data.get("profile_url", "")).strip()
        expected_origin = self.config.normalized_hub_origin
        if raw_profile.startswith(("http://", "https://")):
            parsed_prof = urlsplit(raw_profile)
            prof_origin = normalize_hub_origin(f"{parsed_prof.scheme}://{parsed_prof.netloc}")
            if prof_origin and expected_origin and prof_origin == expected_origin:
                profile_url = raw_profile
            else:
                profile_url = self.config.profile_url
        elif raw_profile.startswith("/") and not raw_profile.startswith("//"):
            profile_url = f"{self.config.normalized_hub_url}{raw_profile}"
        else:
            profile_url = self.config.profile_url

        if save:
            save_config(self.config, path=self.config_path)

        result = {**data, "profile_url": profile_url}
        self._emit("registered", result)
        return result

    def claim_next(
        self,
        preferred_mission: str | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Claim the next available audit or verification task via POST /api/desk/next."""
        claim_request_id = f"claim-{secrets.token_hex(12)}"
        body: dict[str, Any] = {
            "handle": self.config.handle,
            "mode": self.config.mode,
            "request_id": claim_request_id,
        }
        body["website_observations"] = True
        if preferred_mission:
            body["preferred_mission"] = preferred_mission
        cleaned_task_id = (task_id or "").strip()
        if cleaned_task_id:
            body["task_id"] = cleaned_task_id

        resp = self._hub_send(
            "POST",
            "/api/desk/next",
            headers=self._auth_headers(extra={"Idempotency-Key": claim_request_id}),
            json_body=body,
            what="claim",
        )
        if resp.status_code == 204:
            self._emit("queue_empty", {"handle": self.config.handle})
            return None
        resp.raise_for_status()
        if not resp.content.strip():
            raise ValueError("Hub returned an empty 2xx response to task claim.")
        lease = self._parse_hub_json(resp)
        if isinstance(lease, dict) and lease.get("lease_id"):
            self._note_lease(str(lease["lease_id"]))
        self._emit("task_claimed", lease)
        return lease

    def heartbeat(
        self,
        lease_id: str,
        *,
        whisper: str | None = None,
        progress_pct: int | None = None,
    ) -> bool:
        """Send live heartbeat + optional whisper. Returns False on 410 Gone."""
        body: dict[str, Any] = {"lease_id": lease_id}
        if whisper is not None:
            body["whisper"] = whisper
        if progress_pct is not None:
            body["progress_pct"] = progress_pct

        # A heartbeat only reports progress; the lease itself runs 45 minutes. If the Hub is
        # throttling or briefly unreachable, skip the beat and keep working rather than fail the task.
        try:
            resp = self._hub_send(
                "POST",
                "/api/desk/beat",
                headers=self._auth_headers(),
                json_body=body,
                what="heartbeat",
                rate_limit_waits=0,
                transport_retries=1,
            )
        except httpx.TransportError:
            self._emit("heartbeat_skipped", {"lease_id": lease_id, "reason": "hub unreachable"})
            return True
        if resp.status_code == 410:
            self._clear_lease_note(lease_id=lease_id)
            self._emit("lease_gone", {"lease_id": lease_id})
            return False
        if resp.status_code == 429 or resp.status_code >= 500:
            self._emit("heartbeat_skipped", {"lease_id": lease_id, "reason": f"HTTP {resp.status_code}"})
            return True
        resp.raise_for_status()
        self._emit(
            "heartbeat",
            {"lease_id": lease_id, "progress_pct": progress_pct, "whisper": whisper},
        )
        return True

    def release(
        self,
        lease_id: str,
        *,
        reason: str,
        notes: str | None = None,
    ) -> dict[str, Any] | None:
        """Release a claimed lease cleanly so tasks never stall on error."""
        body: dict[str, Any] = {"lease_id": lease_id, "reason": reason}
        if notes:
            body["notes"] = notes[:500]
        try:
            resp = self._hub_send(
                "POST",
                "/api/desk/release",
                headers=self._auth_headers(),
                json_body=body,
                what="release",
                rate_limit_waits=2,
            )
            if resp.status_code in (404, 410):
                self._clear_lease_note()
                return None
            resp.raise_for_status()
            data = resp.json() if resp.content.strip() else {"status": "released"}
            self._clear_lease_note()
            self._emit("task_released", {"lease_id": lease_id, "reason": reason})
            return data
        except httpx.HTTPError:
            return None

    def _engine_usage(self) -> dict[str, Any] | None:
        """Tokens and time this CLI measured for the current task (0 tokens for code-only checks)."""
        if self._task_started is None:
            return None
        return {
            "source": "engine",
            "input_tokens": self._task_input_tokens,
            "output_tokens": self._task_output_tokens,
            "duration_seconds": max(0, int(time.monotonic() - self._task_started)),
        }

    def submit(
        self,
        lease_id: str,
        *,
        outcome: str,
        whisper: str | None = None,
        finding: dict[str, Any] | None = None,
        findings: list[dict[str, Any]] | None = None,
        observations: list[dict[str, Any]] | None = None,
        verification: dict[str, Any] | None = None,
        metrics: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Submit task outcome (finding | cleared | verification) to POST /api/desk/submit."""
        idem_key = idempotency_key or f"settle-{lease_id}-{outcome}"
        body: dict[str, Any] = {
            "lease_id": lease_id,
            "outcome": outcome,
            "idempotency_key": idem_key,
        }
        if whisper is not None:
            body["whisper"] = whisper
        if observations is not None:
            body["observations"] = observations
        if finding is not None:
            body["finding"] = finding
        if findings is not None:
            body["findings"] = findings
        if verification is not None:
            body["verification"] = verification
        if metrics is not None:
            body["metrics"] = metrics
        usage = self._engine_usage()
        if usage is not None:
            body["usage"] = usage

        # Safe to repeat: the Hub replays the stored answer for the same idempotency key.
        resp = self._hub_send(
            "POST",
            "/api/desk/submit",
            headers=self._auth_headers(extra={"Idempotency-Key": idem_key}),
            json_body=body,
            what="submit",
        )
        resp.raise_for_status()
        data = resp.json() if resp.content.strip() else {"status": "submitted"}
        self._clear_lease_note()
        self._emit("task_submitted", {"lease_id": lease_id, "outcome": outcome, "response": data})
        return data

    def _fetch_finding(self, finding_id: str) -> dict[str, Any] | None:
        cleaned_id = str(finding_id or "").strip()
        if not _SAFE_FINDING_ID_RE.fullmatch(cleaned_id):
            raise ValueError(f"Invalid finding_id format: {cleaned_id!r}")
        resp = self._hub_send(
            "GET",
            f"/api/findings/{quote(cleaned_id, safe='')}",
            headers=self._auth_headers(),
            what="fetch_finding",
        )
        if resp.status_code != 200:
            return None
        data = self._parse_hub_json(resp)
        if isinstance(data, dict) and isinstance(data.get("finding"), dict):
            return data["finding"]
        return data if isinstance(data, dict) else None

    def _invoke_engine(
        self,
        methodology_code: str,
        records: list[dict[str, Any]],
        *,
        context: dict[str, Any] | None,
        as_of_date: str | None,
    ) -> Any:
        params = inspect.signature(run_engine).parameters
        kwargs: dict[str, Any] = {"context": context or None}
        if "as_of_date" in params and as_of_date is not None:
            kwargs["as_of_date"] = as_of_date
        return run_engine(methodology_code, records, **kwargs)

    def _load_task_dataset(
        self,
        payload: dict[str, Any],
        *,
        expected_sha256: str | None = None,
        secondary_expected_sha256: str | None = None,
        as_of_date: str | None = None,
        allow_finding_fallback: bool = True,
    ) -> LoadedDataset:
        target_ref = _extract_target_ref(
            payload,
            allow_local_files=self.allow_local_files,
            allow_finding_fallback=allow_finding_fallback,
        )
        secondary_url = _extract_secondary_url(
            payload,
            allow_local_files=self.allow_local_files,
            allow_finding_fallback=allow_finding_fallback,
        )

        if target_ref:
            # Defense-in-depth: block local file paths in unattended runner mode even if loader is stubbed.
            if not self.allow_local_files and not _is_http_url(target_ref):
                raise PermissionError(
                    f"Local file path {target_ref!r} is disabled in unattended swarm runner mode "
                    "(allow_local_files=False); use interactive 'aiwatchdogs audit <local-file>'."
                )
            if secondary_url and not self.allow_local_files and not _is_http_url(secondary_url):
                raise PermissionError(
                    f"Local secondary file path {secondary_url!r} is disabled in unattended swarm runner mode."
                )

            fmt = payload.get("format") or payload.get("file_format")
            load_ctx = _build_task_context(
                payload,
                allow_local_files=self.allow_local_files,
                as_of_date=as_of_date,
                allow_finding_fallback=allow_finding_fallback,
            )
            if expected_sha256:
                load_ctx["expected_sha256"] = expected_sha256
            if secondary_expected_sha256:
                load_ctx["secondary_expected_sha256"] = secondary_expected_sha256
                load_ctx["secondary_sha256"] = secondary_expected_sha256

            params = inspect.signature(load_dataset).parameters
            kwargs: dict[str, Any] = {}
            if "fmt" in params:
                kwargs["fmt"] = str(fmt) if fmt else None
            if "context" in params:
                kwargs["context"] = load_ctx or None
            if "allow_local_files" in params:
                kwargs["allow_local_files"] = self.allow_local_files
            if "use_cache" in params:
                # Enable SHA-256-verified caching ONLY when the task pins an exact 64-hex digest.
                # Untrusted task payloads cannot force caching of unpinned URLs; loader re-verifies SHA-256
                # and refuses to cache payloads containing PII.
                kwargs["use_cache"] = bool(
                    expected_sha256 and _SHA256_HEX_RE.fullmatch(expected_sha256.strip())
                )
            if "expected_sha256" in params and expected_sha256:
                kwargs["expected_sha256"] = expected_sha256
            if "secondary_expected_sha256" in params and secondary_expected_sha256:
                kwargs["secondary_expected_sha256"] = secondary_expected_sha256

            # Say where the file comes from before fetching it, for audits and peer checks alike.
            hosts = [_source_host(ref) for ref in (target_ref, secondary_url) if ref and _is_http_url(ref)]
            if hosts:
                self._emit("source_download", {"hosts": list(dict.fromkeys(hosts))})
            return load_dataset(target_ref, **kwargs)

        inline_records = payload.get("records")
        if isinstance(inline_records, list):
            if len(inline_records) > MAX_INLINE_RECORDS:
                raise ValueError(f"Inline records count exceeds maximum allowed cap ({MAX_INLINE_RECORDS}).")
            scrubbed_records, redactions = scrub_pii_in_obj(inline_records)
            raw_text = str(scrubbed_records)
            return LoadedDataset(
                records=scrubbed_records,
                raw_text=raw_text[:8192],
                sha256=compute_sha256(raw_text),
                byte_size=len(raw_text.encode("utf-8")),
                format="json",
                is_demo_sample=False,
                source_ref=str(payload.get("source_ref", "inline-task-payload")),
                pii_redactions=redactions,
            )
        raise ValueError("Task payload missing dataset_url, dataset_path, source_url, or inline records")

    def _verify_digests_strict(
        self,
        dataset: LoadedDataset,
        *,
        expected_primary_sha: str,
        secondary_url: str | None,
        expected_secondary_sha: str,
    ) -> tuple[str, bool | None, bool | None, str, str | None]:
        """Verify BOTH primary and secondary artifact SHA-256 digests strictly (fail-closed).

        Returns ``(check_state, primary_matched, secondary_matched, observed_secondary_sha, failure_detail)``.
        """
        observed_primary = (getattr(dataset, "observed_sha256", None) or dataset.sha256 or "").strip().lower()
        observed_secondary = _extract_observed_secondary_sha256(dataset)

        primary_matched: bool | None = None
        if expected_primary_sha:
            primary_matched = (observed_primary == expected_primary_sha.lower())
            if getattr(dataset, "digest_verified", None) is False:
                primary_matched = False
            if not primary_matched:
                return (
                    CHECK_FAIL,
                    False,
                    None,
                    observed_secondary,
                    f"Primary artifact SHA-256 mismatch (expected {expected_primary_sha[:12]}, got {observed_primary[:12]})",
                )

        secondary_matched: bool | None = None
        if secondary_url:
            if expected_secondary_sha:
                secondary_matched = bool(observed_secondary and observed_secondary == expected_secondary_sha.lower())
                if getattr(dataset, "secondary_digest_verified", None) is False:
                    secondary_matched = False
                if not secondary_matched:
                    return (
                        CHECK_FAIL,
                        primary_matched,
                        False,
                        observed_secondary,
                        (
                            f"Secondary artifact SHA-256 mismatch "
                            f"(expected {expected_secondary_sha[:12]}, got {observed_secondary[:12] or 'missing'})"
                        ),
                    )
            else:
                # Secondary dataset was loaded without an expected digest to verify against.
                return (
                    CHECK_UNRECORDED,
                    primary_matched,
                    None,
                    observed_secondary,
                    "Secondary artifact expected SHA-256 was unrecorded.",
                )

        if not expected_primary_sha:
            return (
                CHECK_UNRECORDED,
                None,
                secondary_matched,
                observed_secondary,
                "Primary artifact expected SHA-256 was unrecorded.",
            )

        return (CHECK_PASS, True, secondary_matched, observed_secondary, None)

    def _handle_anomaly_scan(self, lease_id: str, task: dict[str, Any]) -> dict[str, Any]:
        task_id = task.get("id")
        mission_slug = str(task.get("mission_slug", ""))
        methodology_code = str(task.get("methodology_code", "")).upper().strip()
        payload = task.get("payload") or {}
        artifact_label = str(
            payload.get("artifact_title") or task.get("title") or mission_slug or methodology_code
        ).strip()
        target_ref = _extract_target_ref(payload, allow_local_files=self.allow_local_files)
        secondary_url = _extract_secondary_url(payload, allow_local_files=self.allow_local_files)
        expected_sha = _extract_expected_sha256(payload)
        expected_secondary_sha = _extract_expected_secondary_sha256(payload)
        as_of_date = _extract_as_of_date(payload, default_today_utc=True)
        engine_context = _build_task_context(
            payload,
            allow_local_files=self.allow_local_files,
            as_of_date=as_of_date,
        )

        if not self.heartbeat(
            lease_id,
            whisper=f"Fetching {artifact_label} ({methodology_code}) from {_source_host(target_ref)}",
            progress_pct=15,
        ):
            return {"status": "aborted_410", "task_id": task_id, "outcome": "aborted_410"}

        dataset = self._load_task_dataset(
            payload,
            expected_sha256=expected_sha or None,
            secondary_expected_sha256=expected_secondary_sha or None,
            as_of_date=as_of_date,
        )

        # Fail closed on BOTH primary AND secondary SHA-256 digest mismatches!
        digest_state, primary_matched, secondary_matched, observed_secondary_sha, digest_err = (
            self._verify_digests_strict(
                dataset,
                expected_primary_sha=expected_sha,
                secondary_url=secondary_url,
                expected_secondary_sha=expected_secondary_sha,
            )
        )
        if digest_state == CHECK_FAIL:
            raise DigestMismatchFailure(digest_err or "Artifact SHA-256 digest verification failed.")

        if methodology_code == "W1":
            from aiwatchdogs.engines.gov_pages import observe_site

            if primary_matched is not True or not 1 <= len(dataset.records) <= 126:
                raise ValueError("Website audit requires a checksummed batch of 1–126 websites.")
            rows = []
            with ThreadPoolExecutor(max_workers=4) as pool:
                for index in range(0, len(dataset.records), 13):
                    if not self.heartbeat(
                        lease_id, whisper=f"Reviewed {index} of {len(dataset.records)} assigned websites",
                        progress_pct=20 + 75 * index // len(dataset.records),
                    ):
                        return {"status": "aborted_410", "task_id": task_id, "outcome": "aborted_410"}
                    rows.extend(pool.map(observe_site, dataset.records[index:index + 13]))
            response = self.submit(lease_id, outcome="observations", observations=rows)
            return {"status": "completed", "task_id": task_id, "outcome": "observations", "response": response}

        sha_note = (
            f"sha256:{dataset.sha256[:8]} matched"
            if primary_matched is True
            else f"sha256:{dataset.sha256[:8]}"
        )
        if secondary_matched is True and observed_secondary_sha:
            sha_note += f", secondary:{observed_secondary_sha[:8]} matched"

        action_verb = "Downloaded" if _is_http_url(target_ref or "") else "Loaded"
        if not self.heartbeat(
            lease_id,
            whisper=(
                f"{action_verb} {artifact_label} ({len(dataset.records):,} records, {sha_note}); "
                f"running {methodology_code} deterministic checks"
            ),
            progress_pct=50,
        ):
            return {"status": "aborted_410", "task_id": task_id, "outcome": "aborted_410"}

        engine_res = self._invoke_engine(
            methodology_code,
            dataset.records,
            context=engine_context or None,
            as_of_date=as_of_date,
        )

        engine_version = str(getattr(engine_res, "engine_version", None) or "2.0.0")
        ruleset_version = str(getattr(engine_res, "ruleset_version", None) or f"{methodology_code}-v2")
        effective_as_of = str(getattr(engine_res, "as_of_date", None) or as_of_date or "")
        unevaluable_count = int(getattr(engine_res, "unevaluable_count", 0) or 0)

        retrieval_url = str(
            getattr(dataset, "retrieval_url", None)
            or payload.get("retrieval_url")
            or payload.get("dataset_url")
            or payload.get("canonical_url")
            or target_ref
            or dataset.source_ref
        ).strip()
        canonical_url = str(
            payload.get("dataset_url")
            or payload.get("canonical_url")
            or retrieval_url
            or dataset.source_ref
        ).strip()
        source_url = str(
            payload.get("source_landing_page")
            or payload.get("source_url")
            or getattr(dataset, "source_landing_page", None)
            or canonical_url
        ).strip()

        metrics: dict[str, Any] = {
            "records_scanned": engine_res.records_scanned,
            "monetary_audited_inr": engine_res.monetary_audited_inr,
            "cleared_count": engine_res.cleared_count,
            "unevaluable_count": unevaluable_count,
            "leads_found": len(engine_res.leads),
            "script_hash": engine_res.script_hash,
            "engine_version": engine_version,
            "ruleset_version": ruleset_version,
            "as_of_date": effective_as_of or None,
            "artifact_sha256": dataset.sha256,
            "byte_size": dataset.byte_size,
            "expected_sha256_matched": primary_matched,
            "secondary_sha256": observed_secondary_sha or None,
            "secondary_sha256_matched": secondary_matched,
            "source_ref": dataset.source_ref,
            "canonical_url": canonical_url,
            "retrieval_url": retrieval_url,
            "is_demo_sample": False,
            "pii_redactions_count": len(dataset.pii_redactions),
        }

        if not engine_res.leads and unevaluable_count > 0 and int(engine_res.cleared_count or 0) == 0:
            # Nothing was read, so nothing was checked. Reporting that as "cleared" would put a false
            # clean result on the public record; hand the task back instead.
            raise UnevaluableSourceFailure(
                f"{methodology_code} could not read any of the {unevaluable_count} figure set(s) it was asked "
                "to compare in this file (the printed lines were not found as specified)."
            )

        if not engine_res.leads:
            clean_summary, _ = scrub_pii(engine_res.summary)
            if not self.heartbeat(
                lease_id,
                whisper=(
                    f"Cleared {engine_res.records_scanned:,} records in {artifact_label} "
                    f"under {methodology_code}: 0 statutory thresholds breached"
                ),
                progress_pct=90,
            ):
                return {"status": "aborted_410", "task_id": task_id, "outcome": "aborted_410"}
            resp = self.submit(
                lease_id,
                outcome="cleared",
                whisper=clean_summary
                or f"Cleared {engine_res.records_scanned:,} records in {artifact_label} with zero statutory anomalies.",
                metrics=metrics,
            )
            return {
                "status": "completed",
                "task_id": task_id,
                "outcome": "cleared",
                "metrics": metrics,
                "response": resp,
            }

        sorted_leads = sorted(
            engine_res.leads,
            key=lambda lead: (
                SEVERITY_RANK.get(str(getattr(lead, "severity", "low")).lower(), 0),
                float(getattr(lead, "monetary_exposure_inr", 0.0) or 0.0),
            ),
            reverse=True,
        )
        leads_to_submit = sorted_leads[:25]

        def _build_finding_payload(lead: Any) -> dict[str, Any]:
            enriched = enrich_finding_summary(
                lead,
                self.provider_cfg,
                allow_paid_provider=self.config.allow_paid_provider,
                max_usd=self.config.max_usd,
                budget_tracker=self.budget_tracker,
            )
            self._task_input_tokens += int(enriched.get("input_tokens") or 0)
            self._task_output_tokens += int(enriched.get("output_tokens") or 0)

            # Deterministic verified numbers/summary NEVER overwritten by LLM prose!
            clean_title, _ = scrub_pii(enriched["title"])
            clean_summary = enriched["summary"]
            clean_entity, _ = scrub_pii(str(lead.entity_name))
            clean_benign = [scrub_pii(b)[0] for b in enriched["benign_explanations"] if str(b).strip()]
            editorial_interpretation = enriched.get("editorial_interpretation")

            lint_res = lint_neutrality(clean_title, clean_summary, clean_benign)
            if not lint_res.passed:
                raise ValueError(f"Neutrality lint rejected lead before submission: {lint_res.violations}")

            lead_ruleset_version = str(getattr(lead, "ruleset_version", None) or ruleset_version)
            lead_source_locator = _extract_source_locator(lead, dataset)
            lead_metrics_clean, _ = scrub_pii_in_obj(dict(lead.anomaly_metrics or {}))
            lead_fy_start = getattr(lead, "fiscal_year_start", None) or lead_metrics_clean.get("fiscal_year_start")
            lead_fy_end = getattr(lead, "fiscal_year_end", None) or lead_metrics_clean.get("fiscal_year_end")

            item_payload: dict[str, Any] = {
                "title": clean_title,
                "summary": clean_summary,
                "editorial_interpretation": editorial_interpretation,
                "methodology_code": lead.methodology_code,
                "entity_name": clean_entity,
                "jurisdiction": lead.jurisdiction,
                "fiscal_year": lead.fiscal_year,
                "fiscal_year_start": lead_fy_start,
                "fiscal_year_end": lead_fy_end,
                "monetary_exposure_inr": float(lead.monetary_exposure_inr),
                "severity": lead.severity,
                "anomaly_metrics": lead_metrics_clean,
                "rule_triggered": lead.rule_triggered,
                "engine_version": engine_version,
                "ruleset_version": lead_ruleset_version,
                "as_of_date": effective_as_of or None,
                "source_locator": lead_source_locator,
                "citations": _scrub_citations(
                    list(lead.citations),
                    dataset,
                    canonical_url=canonical_url,
                    source_url=source_url,
                    secondary_url=secondary_url,
                    secondary_sha256=observed_secondary_sha or None,
                ),
                "benign_explanations": clean_benign,
                "suggested_authority": lead.suggested_authority,
                "artifact_id": payload.get("artifact_id"),
                "artifact_title": artifact_label,
                "artifact_sha256": dataset.sha256,
                "byte_size": dataset.byte_size,
                "fetched_at": getattr(dataset, "fetched_at", None),
                "official_publisher": getattr(dataset, "official_publisher", None) or payload.get("official_publisher"),
                "source_landing_page": getattr(dataset, "source_landing_page", None) or payload.get("source_landing_page") or source_url,
                "mirror_name": getattr(dataset, "mirror_name", None) or payload.get("mirror_name"),
                "mirror_catalog_url": payload.get("mirror_catalog_url"),
                "retrieval_url": retrieval_url,
                "archive_url": payload.get("archive_url"),
                "source_id": payload.get("source_id"),
                "dataset_path": canonical_url,
                "canonical_url": canonical_url,
                "source_ref": canonical_url,
                "source_url": source_url,
                "is_demo_sample": False,
                "agent_trace": {
                    "handle": self.config.handle,
                    "provider": self.config.provider,
                    "model": self.config.model,
                    "enriched_by": enriched["enriched_by"],
                    "used_llm": enriched["used_llm"],
                    "editorial_interpretation": editorial_interpretation,
                    "budget_blocked": enriched.get("budget_blocked", False),
                    "enrichment_fallback_reason": enriched.get("fallback_reason"),
                    "enrichment_cost_usd": enriched.get("actual_cost_usd", 0.0),
                    "verifier_script_hash": engine_res.script_hash,
                    "engine_version": engine_version,
                    "ruleset_version": lead_ruleset_version,
                    "as_of_date": effective_as_of or None,
                    "source_locator": lead_source_locator,
                    "artifact_sha256": dataset.sha256,
                    "expected_sha256_matched": primary_matched,
                    "secondary_sha256": observed_secondary_sha or None,
                    "secondary_sha256_matched": secondary_matched,
                    "canonical_url": canonical_url,
                    "retrieval_url": retrieval_url,
                    "records_scanned": engine_res.records_scanned,
                    "monetary_audited_inr": engine_res.monetary_audited_inr,
                    "unevaluable_count": unevaluable_count,
                    **({"dataset_url": canonical_url} if _is_http_url(canonical_url) else {}),
                    **({"secondary_url": secondary_url} if secondary_url else {}),
                },
            }
            if _is_http_url(canonical_url):
                item_payload["dataset_url"] = canonical_url
            if secondary_url:
                item_payload["secondary_url"] = secondary_url
            if observed_secondary_sha:
                item_payload["secondary_sha256"] = observed_secondary_sha
            return item_payload

        finding_payloads = [_build_finding_payload(lead) for lead in leads_to_submit]
        finding_payload = finding_payloads[0]
        clean_entity = str(finding_payload["entity_name"])
        clean_title = str(finding_payload["title"])

        if not self.heartbeat(
            lease_id,
            whisper=(
                f"Submitting {methodology_code} lead for {clean_entity} "
                f"({len(finding_payloads)} of {len(engine_res.leads)} lead(s) from {engine_res.records_scanned:,} records; peer review pending)"
            ),
            progress_pct=90,
        ):
            return {"status": "aborted_410", "task_id": task_id, "outcome": "aborted_410"}

        whisper_msg = (
            f"{methodology_code} submitted {len(finding_payloads)} leads (top: {clean_title})"
            if len(finding_payloads) > 1
            else f"{methodology_code} lead submitted: {clean_title}"
        )
        resp = self.submit(
            lease_id,
            outcome="finding",
            whisper=whisper_msg,
            finding=finding_payload,
            findings=finding_payloads,
            metrics=metrics,
        )
        return {
            "status": "completed",
            "task_id": task_id,
            "outcome": "finding",
            "finding": finding_payload,
            "findings": finding_payloads,
            "metrics": metrics,
            "response": resp,
        }

    # Backwards-compatible internal alias
    _execute_anomaly_scan = _handle_anomaly_scan

    def _handle_peer_verify(self, lease_id: str, task: dict[str, Any]) -> dict[str, Any]:
        task_id = task.get("id")
        payload = task.get("payload") or {}
        finding_id = str(
            task.get("target_finding_id")
            or payload.get("finding_id")
            or (payload.get("finding") or {}).get("id")
            or ""
        ).strip()

        if not self.heartbeat(
            lease_id,
            whisper=f"Starting independent peer verification for finding {finding_id or task_id}",
            progress_pct=20,
        ):
            return {"status": "aborted_410", "task_id": task_id, "outcome": "aborted_410"}

        finding: dict[str, Any] | None = None
        if isinstance(payload.get("finding"), dict):
            finding = payload["finding"]
        elif isinstance(payload, dict) and payload.get("title") and payload.get("summary"):
            finding = payload
        elif finding_id:
            finding = self._fetch_finding(finding_id)
        if not isinstance(finding, dict):
            raise ValueError(f"peer_verify task missing finding payload for finding_id={finding_id!r}")

        if not finding_id and finding.get("id"):
            finding_id = str(finding["id"])

        methodology_code = str(
            finding.get("methodology_code") or task.get("methodology_code") or ""
        ).upper().strip()

        # Check 4: Neutrality & recursive PII safety across ALL finding fields (including nested metrics/citations/editorial)
        title = str(finding.get("title", ""))
        summary = str(finding.get("summary", ""))
        entity_name = str(finding.get("entity_name", ""))
        benign = finding.get("benign_explanations") or []
        if not isinstance(benign, list):
            benign = []

        _, pii_hits = scrub_pii_in_obj(
            {
                "title": title,
                "summary": summary,
                "entity_name": entity_name,
                "benign_explanations": benign,
                "anomaly_metrics": finding.get("anomaly_metrics"),
                "citations": finding.get("citations"),
                "editorial_interpretation": finding.get("editorial_interpretation"),
            }
        )
        lint_res = lint_neutrality(title, summary, [str(b) for b in benign])
        editorial_obj = finding.get("editorial_interpretation")
        editorial_lint_ok = True
        editorial_violations: list[str] = []
        if isinstance(editorial_obj, dict) and editorial_obj.get("text"):
            ed_lint = lint_neutrality(title, str(editorial_obj["text"]), [str(b) for b in benign])
            editorial_lint_ok = ed_lint.passed
            editorial_violations = ed_lint.violations
        elif isinstance(editorial_obj, str) and editorial_obj.strip():
            ed_lint = lint_neutrality(title, editorial_obj, [str(b) for b in benign])
            editorial_lint_ok = ed_lint.passed
            editorial_violations = ed_lint.violations

        neutrality_privacy_state = (
            CHECK_PASS
            if (lint_res.passed and editorial_lint_ok and len(benign) > 0 and len(pii_hits) == 0)
            else CHECK_FAIL
        )

        # Merge authoritative finding metadata if target_ref or manifest bindings are missing from task payload
        merged_payload: dict[str, Any] = {**payload, "finding": finding}
        trace_obj = finding.get("agent_trace") if isinstance(finding.get("agent_trace"), dict) else {}
        needs_manifest_enrichment = (
            _extract_target_ref(merged_payload, allow_local_files=self.allow_local_files) is None
            or not (finding.get("engine_version") or trace_obj.get("engine_version"))
            or not (finding.get("ruleset_version") or trace_obj.get("ruleset_version"))
            or not finding.get("fiscal_year")
        )
        if finding_id and needs_manifest_enrichment:
            fetched = self._fetch_finding(finding_id)
            if isinstance(fetched, dict) and not fetched.get("quarantined"):
                merged_finding = dict(finding)
                for key, val in fetched.items():
                    if merged_finding.get(key) in (None, "", []):
                        merged_finding[key] = val
                finding = merged_finding
                merged_payload = {**payload, "finding": finding}

        # Prefer server-pinned task payload URLs over any untrusted finding-embedded URLs!
        server_has_target = (
            _extract_target_ref(
                payload, allow_local_files=self.allow_local_files, allow_finding_fallback=False
            )
            is not None
        )
        allow_finding_url_fallback = not server_has_target
        secondary_url = _extract_secondary_url(
            merged_payload,
            allow_local_files=self.allow_local_files,
            allow_finding_fallback=allow_finding_url_fallback,
        )
        expected_sha = _extract_expected_sha256(merged_payload, finding)
        expected_secondary_sha = _extract_expected_secondary_sha256(merged_payload, finding)
        as_of_date = _extract_as_of_date(merged_payload, finding, default_today_utc=False)
        engine_context = _build_task_context(
            merged_payload,
            allow_local_files=self.allow_local_files,
            as_of_date=as_of_date,
            allow_finding_fallback=allow_finding_url_fallback,
        )

        notes_parts: list[str] = []
        if neutrality_privacy_state == CHECK_FAIL:
            if pii_hits:
                notes_parts.append(f"PII detected ({', '.join(sorted(set(pii_hits)))})")
            if not lint_res.passed:
                notes_parts.append(f"Neutrality lint failed ({'; '.join(lint_res.violations)})")
            if not editorial_lint_ok:
                notes_parts.append(f"Editorial neutrality lint failed ({'; '.join(editorial_violations)})")
            if not benign:
                notes_parts.append("Missing mandatory benign_explanations")

        # Check 1: Independent dataset reload & fail-closed primary + secondary SHA-256 verification
        dataset: LoadedDataset | None = None
        bytes_digest_state = CHECK_UNRECORDED
        observed_secondary_sha = ""
        try:
            dataset = self._load_task_dataset(
                merged_payload,
                expected_sha256=expected_sha or None,
                secondary_expected_sha256=expected_secondary_sha or None,
                as_of_date=as_of_date,
                allow_finding_fallback=allow_finding_url_fallback,
            )
            bytes_digest_state, _prim_ok, _sec_ok, observed_secondary_sha, digest_note = (
                self._verify_digests_strict(
                    dataset,
                    expected_primary_sha=expected_sha,
                    secondary_url=secondary_url,
                    expected_secondary_sha=expected_secondary_sha,
                )
            )
            if digest_note:
                notes_parts.append(digest_note)
        except Exception as exc:
            if _is_digest_mismatch_exception(exc):
                bytes_digest_state = CHECK_FAIL
                notes_parts.append(str(exc))
            else:
                raise

        records_count = len(dataset.records) if dataset is not None else 0
        observed_primary_sha = dataset.sha256 if dataset is not None else None

        if not self.heartbeat(
            lease_id,
            whisper=(
                f"Checked dataset provenance ({records_count:,} records, bytes_digest={bytes_digest_state}); "
                f"verifying {methodology_code} for {entity_name or finding_id}"
            ),
            progress_pct=60,
        ):
            return {"status": "aborted_410", "task_id": task_id, "outcome": "aborted_410"}

        # Check 2 & 3: Strict deterministic reproduction & complete required metric-set tolerance
        reproduction_state = CHECK_UNRECORDED
        metric_tolerance_state = CHECK_UNRECORDED
        reproduced_metrics: dict[str, Any] = {}
        reproduced_leads_count: int | None = None
        bound_engine_version: str | None = None
        bound_ruleset_version: str | None = None
        bound_source_locator: str | None = (
            str(finding.get("source_locator")).strip() if finding.get("source_locator") else None
        )
        bound_as_of_date: str | None = as_of_date

        # Fail-closed: NEVER run deterministic reproduction on digest-mismatched bytes!
        if bytes_digest_state == CHECK_FAIL:
            reproduction_state = CHECK_UNRECORDED
            metric_tolerance_state = CHECK_UNRECORDED
            reproduced_metrics = {
                "artifact_sha256": observed_primary_sha,
                "expected_sha256": expected_sha or None,
                "secondary_sha256": observed_secondary_sha or None,
                "expected_secondary_sha256": expected_secondary_sha or None,
                "records_scanned": records_count,
                "aborted_due_to_digest_mismatch": True,
            }
        elif methodology_code not in METHODOLOGY_CATALOG:
            reproduction_state = CHECK_FAIL
            metric_tolerance_state = CHECK_FAIL
            notes_parts.append(f"Unknown methodology_code {methodology_code!r}.")
        elif dataset is not None:
            repro = self._invoke_engine(
                methodology_code,
                dataset.records,
                context=engine_context or None,
                as_of_date=as_of_date,
            )
            reproduced_leads_count = len(repro.leads)
            bound_engine_version = str(getattr(repro, "engine_version", None) or "2.0.0")
            bound_ruleset_version = str(getattr(repro, "ruleset_version", None) or f"{methodology_code}-v2")
            bound_as_of_date = str(getattr(repro, "as_of_date", None) or as_of_date or "") or None

            match_res = _match_reproduced_lead_strict(
                finding,
                repro.leads,
                submitted_methodology=methodology_code,
                repro_engine_version=bound_engine_version,
                repro_ruleset_version=bound_ruleset_version,
                repro_script_hash=repro.script_hash,
            )
            reproduction_state = match_res["deterministic_reproduction"]
            metric_tolerance_state = match_res["metric_tolerance"]
            notes_parts.extend(match_res["reasons"])

            matched_lead = match_res["matched_lead"]
            if matched_lead is not None:
                matched_locator = _extract_source_locator(matched_lead, dataset)
                if matched_locator:
                    bound_source_locator = matched_locator
                if getattr(matched_lead, "ruleset_version", None):
                    bound_ruleset_version = str(matched_lead.ruleset_version)

            reproduced_metrics = {
                "script_hash": repro.script_hash,
                "engine_version": bound_engine_version,
                "ruleset_version": bound_ruleset_version,
                "as_of_date": bound_as_of_date,
                "source_locator": bound_source_locator,
                "artifact_sha256": dataset.sha256,
                "secondary_sha256": observed_secondary_sha or None,
                "records_scanned": repro.records_scanned,
                "unevaluable_count": int(getattr(repro, "unevaluable_count", 0) or 0),
                "reproduced_leads_count": reproduced_leads_count,
                **match_res["reproduced_summary"],
            }

        checks_map = {
            "bytes_digest": bytes_digest_state,
            "deterministic_reproduction": reproduction_state,
            "metric_tolerance": metric_tolerance_state,
            "neutrality_privacy": neutrality_privacy_state,
        }

        # Derive explicit, never-omitted verdict from the 4 explicit check states
        all_four_passed = all(state == CHECK_PASS for state in checks_map.values())
        any_hard_fail = any(
            checks_map[k] == CHECK_FAIL
            for k in ("bytes_digest", "deterministic_reproduction", "metric_tolerance")
        )

        if neutrality_privacy_state == CHECK_FAIL:
            verdict = "flag_pii_or_legal"
            confidence = 0.98
        elif all_four_passed:
            verdict = "confirm"
            confidence = 0.95
            notes_parts.append(
                f"Independently reproduced {methodology_code} exact entity, fiscal period, "
                f"engine/ruleset version ({bound_engine_version}/{bound_ruleset_version}), "
                f"and complete required metric set within 1% tolerance on verified sha256:{(observed_primary_sha or '')[:12]}."
            )
        elif bytes_digest_state == CHECK_PASS and reproduced_leads_count == 0:
            verdict = "reject_false_positive"
            confidence = 0.90
        elif any_hard_fail:
            verdict = "dispute"
            confidence = 0.85 if (bytes_digest_state == CHECK_FAIL or metric_tolerance_state == CHECK_FAIL) else 0.80
        else:
            verdict = "needs_review"
            confidence = 0.50

        verification_payload: dict[str, Any] = {
            "finding_id": finding_id,
            "claim_id": finding_id,
            "review_type": "computational_reproduction",
            "verdict": verdict,
            "checks": checks_map,
            "source_locator": bound_source_locator,
            "engine_version": bound_engine_version,
            "ruleset_version": bound_ruleset_version,
            "as_of_date": bound_as_of_date,
            "secondary_sha256": observed_secondary_sha or None,
            "reproduced_metrics": reproduced_metrics,
            "confidence": confidence,
            "notes": " | ".join(dict.fromkeys(p for p in notes_parts if p)),
        }

        if not self.heartbeat(
            lease_id,
            whisper=f"Peer verification complete for {finding_id}: verdict={verdict}",
            progress_pct=90,
        ):
            return {"status": "aborted_410", "task_id": task_id, "outcome": "aborted_410"}

        resp = self.submit(
            lease_id,
            outcome="verification",
            whisper=f"Peer-verified {finding_id} ({methodology_code}): {verdict.upper()}",
            verification=verification_payload,
            metrics={
                "records_scanned": records_count,
                "artifact_sha256": observed_primary_sha,
                "secondary_sha256": observed_secondary_sha or None,
            },
        )
        return {
            "status": "completed",
            "task_id": task_id,
            "outcome": "verification",
            "verification": verification_payload,
            "response": resp,
        }

    # Backwards-compatible internal alias
    _execute_peer_verify = _handle_peer_verify

    def execute_task(self, lease: dict[str, Any]) -> dict[str, Any]:
        """Execute a single claimed lease, releasing cleanly if any error occurs."""
        lease_id = str(lease["lease_id"])
        task = lease.get("task") or {}
        task_type = str(task.get("task_type", "anomaly_scan")).strip().lower()
        self._task_started = time.monotonic()
        self._task_input_tokens = 0
        self._task_output_tokens = 0

        try:
            if task_type == "peer_verify":
                return self._handle_peer_verify(lease_id, task)
            if task_type in ("anomaly_scan", "ingest_parse"):
                return self._handle_anomaly_scan(lease_id, task)
            self.release(
                lease_id,
                reason="unsupported_task_type",
                notes=f"Unsupported task_type: {task_type}",
            )
            return {
                "status": "released",
                "task_id": task.get("id"),
                "outcome": "released",
                "reason": "unsupported_task_type",
            }
        except (KeyboardInterrupt, SystemExit):
            # Ctrl-C, a terminal hang-up or a harness timeout: give the task back before leaving so it
            # is not locked for the rest of its 45-minute lease.
            self.release(lease_id, reason="interrupted", notes="Run was interrupted before the task finished.")
            raise
        except Exception as exc:
            if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None and exc.response.status_code == 410:
                self._clear_lease_note(lease_id=lease_id)
                self._emit("lease_gone", {"lease_id": lease_id})
                return {"status": "aborted_410", "task_id": task.get("id"), "outcome": "aborted_410"}
            if _is_digest_mismatch_exception(exc):
                release_reason = "digest_mismatch"
                safe_release_note = "Artifact SHA-256 digest mismatch."
            elif isinstance(exc, UnevaluableSourceFailure):
                release_reason = "unevaluable_source"
                safe_release_note = "The check could not read the figures it was asked to compare."
            elif isinstance(exc, PermissionError):
                exc_msg = str(exc)
                if "returned HTTP 403" in exc_msg or "returned HTTP 401" in exc_msg:
                    release_reason = "source_http_forbidden"
                    safe_release_note = "Remote source returned HTTP 401/403 (access control respected under IT Act Sec 43)."
                else:
                    release_reason = "policy_blocked"
                    safe_release_note = "Dataset source disallowed by local runner security policy."
            elif isinstance(exc, httpx.HTTPError):
                release_reason = "network_error"
                safe_release_note = f"Upstream fetch error ({type(exc).__name__})."
            else:
                release_reason = "execution_error"
                safe_release_note = f"Local audit execution failed ({type(exc).__name__})."
            self.release(
                lease_id,
                reason=release_reason,
                notes=safe_release_note,
            )
            self._emit(
                "task_error",
                {
                    "lease_id": lease_id,
                    "task_id": task.get("id"),
                    "reason": release_reason,
                    "error": str(exc),
                },
            )
            return {
                "status": "error",
                "task_id": task.get("id"),
                "outcome": "released",
                "reason": release_reason,
                "error": str(exc),
            }

    def run_loop(
        self,
        *,
        max_tasks: int | None = None,
        continuous: bool = False,
        preferred_mission: str | None = None,
        task_id: str | None = None,
        auto_register: bool = True,
        save_on_register: bool = True,
    ) -> RunSummary:
        """Run the claim -> audit/verify -> submit loop.

        ``max_tasks == 0`` drains task after task continuously in ₹0 free mode until the queue is
        empty (HTTP 204), the user stops with Ctrl+C/SIGTERM, or consecutive source failures hit
        ``MAX_CONSECUTIVE_SOURCE_FAILURES``. Positive ``max_tasks`` is strictly enforced in BOTH
        normal and ``--continuous`` modes, and paid LLM providers are always capped.
        """
        # A run with a borrowed identity must not leave files beside someone else's credentials.
        self._lease_note_enabled = bool(save_on_register)
        if auto_register and not self.config.api_token:
            self.register(save=save_on_register)
        self.recover_interrupted_lease()

        target_task_id = (task_id or "").strip() or None
        raw_max = max_tasks if max_tasks is not None else self.config.max_tasks
        effective_max = max(0, int(raw_max))
        if target_task_id:
            effective_max = 1
        elif effective_max == 0 and is_paid_provider(self.provider_cfg) and self.config.allow_paid_provider:
            # Defense-in-depth: never allow an unbounded loop when paid cloud LLM calls are opted in.
            effective_max = DEFAULT_PAID_MAX_TASKS

        summary = RunSummary(
            handle=self.config.handle,
            hub_url=self.config.normalized_hub_url,
            profile_url=self.config.profile_url,
        )
        self.last_summary = summary
        source_failures = 0
        consecutive_claim_429 = 0

        self._emit(
            "run_started",
            {
                "handle": self.config.handle,
                "profile_url": self.config.profile_url,
                "max_tasks": effective_max,
                "mode": self.config.mode,
                "continuous": bool(continuous and not target_task_id),
                "task_id": target_task_id,
            },
        )

        # Strictly enforce effective_max (> 0) in BOTH normal and continuous modes.
        # When effective_max == 0 (₹0 free unlimited mode), loop task after task until the queue
        # empties (or continuous polls), Ctrl+C/SIGTERM interrupts, or consecutive source failures trip.
        try:
            while effective_max == 0 or summary.tasks_claimed < effective_max:
                try:
                    lease = self.claim_next(preferred_mission=preferred_mission, task_id=target_task_id)
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 429:
                        is_lease_cap = _is_lease_cap_429(exc.response)
                        if is_lease_cap and not continuous:
                            # This handle already holds its concurrent lease cap; stop cleanly without spinning.
                            summary.stopped_reason = "hub_busy_or_lease_cap"
                            self._emit("claim_refused", {"detail": exc.response.text[:300]})
                            break
                        if consecutive_claim_429 < MAX_CLAIM_429_BACKOFFS:
                            consecutive_claim_429 += 1
                            base_wait = _retry_after_seconds(exc.response)
                            if base_wait is None:
                                base_wait = min(
                                    MAX_RATE_LIMIT_WAIT_SECONDS,
                                    max(self.poll_interval, 5.0 * (2 ** (consecutive_claim_429 - 1))),
                                )
                            delay = base_wait + random.uniform(0.2, 2.0)
                            self._emit(
                                "hub_retry",
                                {
                                    "what": "claim",
                                    "reason": "lease_cap_busy" if is_lease_cap else "rate_limited",
                                    "delay": round(delay, 1),
                                },
                            )
                            self._sleep(delay)
                            continue
                        summary.stopped_reason = "hub_busy_or_lease_cap"
                        self._emit("claim_refused", {"detail": exc.response.text[:300]})
                        break
                    if target_task_id and exc.response.status_code in (404, 409):
                        detail = ""
                        try:
                            err_body = exc.response.json()
                            if isinstance(err_body, dict) and err_body.get("detail"):
                                detail = str(err_body["detail"])
                        except ValueError:
                            detail = exc.response.text[:300]
                        summary.stopped_reason = (
                            "task_not_found" if exc.response.status_code == 404 else "task_unavailable"
                        )
                        self._emit(
                            "task_claim_rejected",
                            {
                                "task_id": target_task_id,
                                "status_code": exc.response.status_code,
                                "detail": detail or f"HTTP {exc.response.status_code}",
                            },
                        )
                        break
                    raise
                consecutive_claim_429 = 0
                if lease is None:
                    if continuous and not target_task_id:
                        self._sleep(self.poll_interval)
                        continue
                    summary.stopped_reason = "queue_empty"
                    break

                summary.tasks_claimed += 1
                role = role_of(lease)
                result = self.execute_task(lease)
                result.setdefault("role", role)
                summary.results.append(result)

                outcome = result.get("outcome")
                status = result.get("status")
                if status == "completed":
                    summary.roles[role] = summary.roles.get(role, 0) + 1
                if status == "error" and result.get("reason") in SOURCE_FAILURE_REASONS:
                    source_failures += 1
                else:
                    source_failures = 0
                if status == "aborted_410":
                    summary.aborted_410 += 1
                elif status == "error":
                    summary.errors += 1
                elif status == "completed":
                    summary.tasks_completed += 1
                    if outcome == "finding":
                        resp_obj = result.get("response") if isinstance(result.get("response"), dict) else {}
                        created = resp_obj.get("findings_created")
                        if isinstance(created, int) and created > 0:
                            summary.findings_submitted += created
                        elif isinstance(result.get("findings"), list) and result["findings"]:
                            summary.findings_submitted += len(result["findings"])
                        else:
                            summary.findings_submitted += 1
                    elif outcome == "cleared":
                        summary.cleared_submitted += 1
                    elif outcome == "verification":
                        summary.verifications_submitted += 1
                    self._emit(
                        "task_progress",
                        {
                            "completed": summary.tasks_completed,
                            "claimed": summary.tasks_claimed,
                            "max_tasks": effective_max,
                            "task_id": result.get("task_id"),
                            "role": role,
                            "outcome": outcome,
                            "errors": summary.errors,
                            "profile_url": summary.profile_url,
                        },
                    )

                if source_failures >= MAX_CONSECUTIVE_SOURCE_FAILURES:
                    summary.stopped_reason = "sources_unreachable"
                    self._emit("sources_unreachable", {"failures": source_failures})
                    break
                if source_failures > 0 and (effective_max == 0 or summary.tasks_claimed < effective_max):
                    delay = min(8.0, SOURCE_FAILURE_BACKOFF_SECONDS * source_failures)
                    self._sleep(delay)
        except KeyboardInterrupt:
            summary.stopped_reason = "interrupted"
            budget_snap = self.budget_tracker.snapshot()
            summary.session_spent_usd = float(budget_snap.get("session_spent_usd", 0.0))
            summary.budget = budget_snap
            self.last_summary = summary
            raise

        budget_snap = self.budget_tracker.snapshot()
        summary.session_spent_usd = float(budget_snap.get("session_spent_usd", 0.0))
        summary.budget = budget_snap
        self.last_summary = summary
        self._emit("run_finished", summary.model_dump(mode="json"))
        return summary
