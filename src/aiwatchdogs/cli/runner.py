"""pequalnp-style Desk Coordination Loop and Autonomous Watchdog Agent Runner."""

from __future__ import annotations

import inspect
import math
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field

from aiwatchdogs.cli.config import AgentConfig, save_config
from aiwatchdogs.cli.providers import (
    ProviderInfo,
    enrich_finding_summary,
    resolve_provider_config,
)
from aiwatchdogs.connectors.loader import LoadedDataset, load_dataset
from aiwatchdogs.core.safety import compute_sha256, lint_neutrality, scrub_pii
from aiwatchdogs.engines.registry import METHODOLOGY_CATALOG, run_engine

SEVERITY_RANK: dict[str, int] = {
    "critical": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
}

MATH_TOLERANCE = 0.01  # 1% relative tolerance for independent math reproduction
PROJECT_ROOT = Path(__file__).resolve().parents[3]


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
    results: list[dict[str, Any]] = Field(default_factory=list)


def _is_number(val: Any) -> bool:
    return isinstance(val, (int, float)) and not isinstance(val, bool) and math.isfinite(float(val))


def _within_tolerance(expected: float, actual: float, rel_tol: float = MATH_TOLERANCE) -> bool:
    diff = abs(float(expected) - float(actual))
    scale = max(abs(float(expected)), abs(float(actual)), 1.0)
    return diff <= max(1e-6, rel_tol * scale)


def _resolve_dataset_ref(ref: str) -> str:
    """Resolve local relative dataset paths cleanly against CWD or repository root."""
    cleaned = ref.strip()
    if cleaned.startswith(("http://", "https://")):
        return cleaned
    direct = Path(cleaned)
    if direct.exists():
        return str(direct)
    from_root = PROJECT_ROOT / cleaned
    if from_root.exists():
        return str(from_root)
    return cleaned


def _extract_target_ref(payload: dict[str, Any]) -> str | None:
    """Extract official dataset URL or local file path from a Desk task payload or finding."""
    for key in (
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
            return _resolve_dataset_ref(val)

    artifact = payload.get("artifact")
    if isinstance(artifact, dict):
        for key in (
            "dataset_url",
            "canonical_url",
            "source_ref",
            "file_path",
            "dataset_path",
            "url",
            "path",
        ):
            val = artifact.get(key)
            if isinstance(val, str) and val.strip():
                return _resolve_dataset_ref(val)

    finding = payload.get("finding")
    if isinstance(finding, dict):
        for key in (
            "dataset_url",
            "canonical_url",
            "dataset_path",
            "source_ref",
            "artifact_path",
            "source_url",
        ):
            val = finding.get(key)
            if isinstance(val, str) and val.strip():
                return _resolve_dataset_ref(val)
        trace = finding.get("agent_trace")
        if isinstance(trace, dict):
            for key in ("dataset_url", "canonical_url", "dataset_path", "source_ref"):
                val = trace.get(key)
                if isinstance(val, str) and val.strip():
                    return _resolve_dataset_ref(val)
        citations = finding.get("citations")
        if isinstance(citations, list):
            for cit in citations:
                if isinstance(cit, dict):
                    for key in (
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
                            return _resolve_dataset_ref(val)
    return None


def _extract_secondary_url(payload: dict[str, Any]) -> str | None:
    """Extract optional secondary dataset URL (e.g. MCA CSR CSV for M8 Electoral Bonds cross-check)."""
    candidates: list[Any] = [
        payload.get("secondary_url"),
        (payload.get("context") or {}).get("secondary_url") if isinstance(payload.get("context"), dict) else None,
    ]
    finding = payload.get("finding")
    if isinstance(finding, dict):
        candidates.append(finding.get("secondary_url"))
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
            return _resolve_dataset_ref(val)
    return None


def _build_task_context(payload: dict[str, Any]) -> dict[str, Any]:
    """Assemble engine/loader context hints from a Desk task payload or embedded finding."""
    ctx: dict[str, Any] = {}
    nested = payload.get("context")
    if isinstance(nested, dict):
        ctx.update(nested)

    finding = payload.get("finding") if isinstance(payload.get("finding"), dict) else {}
    for key in (
        "dataset_url",
        "source_url",
        "canonical_url",
        "jurisdiction",
        "fiscal_year",
        "artifact_title",
        "methodology_code",
        "max_rows",
        "filter_buyer",
        "expected_sha256",
    ):
        val = payload.get(key)
        if val is None and isinstance(finding, dict):
            val = finding.get(key)
        if val is not None and key not in ctx:
            ctx[key] = val

    secondary = _extract_secondary_url(payload)
    if secondary:
        ctx["secondary_url"] = secondary
    return ctx


def _select_top_lead(leads: list[Any]) -> Any:
    return max(
        leads,
        key=lambda lead: (
            SEVERITY_RANK.get(str(getattr(lead, "severity", "low")).lower(), 0),
            float(getattr(lead, "monetary_exposure_inr", 0.0) or 0.0),
        ),
    )


def _scrub_citations(
    citations: list[dict[str, Any]],
    dataset: LoadedDataset,
    *,
    canonical_url: str | None = None,
    source_url: str | None = None,
    secondary_url: str | None = None,
) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    ref_url = canonical_url or dataset.source_ref
    for item in citations:
        if not isinstance(item, dict):
            continue
        copy_item = dict(item)
        for text_key in ("excerpt", "raw_excerpt", "note", "notes", "title"):
            if text_key in copy_item and isinstance(copy_item[text_key], str):
                copy_item[text_key], _ = scrub_pii(copy_item[text_key])
        copy_item.setdefault("sha256", dataset.sha256)
        copy_item.setdefault("source_ref", ref_url)
        if canonical_url:
            copy_item.setdefault("canonical_url", canonical_url)
            if canonical_url.startswith(("http://", "https://")):
                copy_item.setdefault("dataset_url", canonical_url)
        if source_url:
            copy_item.setdefault("source_url", source_url)
        if secondary_url:
            copy_item.setdefault("secondary_url", secondary_url)
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
            if canonical_url.startswith(("http://", "https://")):
                entry["dataset_url"] = canonical_url
        if secondary_url:
            entry["secondary_url"] = secondary_url
        cleaned.append(entry)
    return cleaned


def _match_reproduced_lead(
    submitted: dict[str, Any],
    reproduced_leads: list[Any],
) -> tuple[Any | None, bool, dict[str, Any]]:
    """Find best reproduced lead matching submitted finding and check 1% math tolerance."""
    if not reproduced_leads:
        return None, False, {}

    sub_entity = str(submitted.get("entity_name", "")).strip().lower()
    sub_rule = str(submitted.get("rule_triggered", "")).strip().lower()
    sub_monetary = submitted.get("monetary_exposure_inr")
    sub_metrics = submitted.get("anomaly_metrics") or {}
    if not isinstance(sub_metrics, dict):
        sub_metrics = {}

    def _score_candidate(cand: Any) -> tuple[int, int, int, float]:
        cand_entity = str(getattr(cand, "entity_name", "")).strip().lower()
        cand_rule = str(getattr(cand, "rule_triggered", "")).strip().lower()
        if sub_entity and cand_entity == sub_entity:
            entity_match = 2
        elif sub_entity and (sub_entity in cand_entity or cand_entity in sub_entity):
            entity_match = 1
        else:
            entity_match = 0

        if sub_rule and cand_rule == sub_rule:
            rule_match = 2
        elif sub_rule and (sub_rule in cand_rule or cand_rule in sub_rule):
            rule_match = 1
        else:
            rule_match = 0

        cand_metrics = getattr(cand, "anomaly_metrics", {}) or {}
        metric_hits = 0
        if sub_metrics and isinstance(cand_metrics, dict):
            for key, sval in sub_metrics.items():
                cval = cand_metrics.get(key)
                if _is_number(sval) and _is_number(cval) and _within_tolerance(float(sval), float(cval)):
                    metric_hits += 1

        cand_mon = float(getattr(cand, "monetary_exposure_inr", 0.0) or 0.0)
        mon_diff = abs(cand_mon - float(sub_monetary or 0.0)) if _is_number(sub_monetary) else 0.0
        return (entity_match, rule_match, metric_hits, -mon_diff)

    best = max(reproduced_leads, key=_score_candidate)
    best_metrics = getattr(best, "anomaly_metrics", {}) or {}
    best_monetary = float(getattr(best, "monetary_exposure_inr", 0.0) or 0.0)

    math_ok = True
    if _is_number(sub_monetary):
        if not _within_tolerance(float(sub_monetary), best_monetary):
            math_ok = False

    if sub_metrics and isinstance(best_metrics, dict):
        shared_numeric_keys = [
            key
            for key in sub_metrics
            if key in best_metrics and _is_number(sub_metrics[key]) and _is_number(best_metrics[key])
        ]
        for key in shared_numeric_keys:
            if not _within_tolerance(float(sub_metrics[key]), float(best_metrics[key])):
                math_ok = False
                break

    reproduced_summary = {
        "entity_name": getattr(best, "entity_name", ""),
        "rule_triggered": getattr(best, "rule_triggered", ""),
        "monetary_exposure_inr": best_monetary,
        "anomaly_metrics": best_metrics,
    }
    return best, math_ok, reproduced_summary


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
    ) -> None:
        self.config = config
        self.provider_cfg = provider_cfg or resolve_provider_config(
            config.provider,
            config.model,
            probe_local=False,
        )
        self._external_client = http_client is not None
        self._client = http_client or httpx.Client(
            base_url=config.normalized_hub_url,
            timeout=httpx.Timeout(request_timeout),
            headers={"User-Agent": "AIWatchdogs-India-AgentCLI/0.1"},
        )
        self.on_event = on_event
        self.poll_interval = poll_interval
        self.request_timeout = request_timeout
        self.config_path = config_path

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

    def _auth_headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.config.api_token:
            headers["X-Agent-Token"] = self.config.api_token
        return headers

    def _request_kwargs(self) -> dict[str, Any]:
        return {} if self._external_client else {"timeout": self.request_timeout}

    def register(
        self,
        *,
        display_name: str | None = None,
        region: str = "IN",
        save: bool = True,
    ) -> dict[str, Any]:
        """Register handle with Hub, persist api_token, and return live profile info."""
        normalized_handle = self.config.handle.strip().lower()
        self.config.handle = normalized_handle
        payload = {
            "handle": normalized_handle,
            "display_name": display_name or normalized_handle,
            "provider": self.config.provider,
            "model": self.config.model,
            "mode": self.config.mode,
            "region": region,
            "capabilities": ["anomaly_scan", "peer_verify", "deterministic_audit"],
        }
        headers = self._auth_headers() if self.config.api_token else {"Accept": "application/json"}
        resp = self._client.post(
            self._url("/api/agents/register"),
            headers=headers,
            json=payload,
            **self._request_kwargs(),
        )
        resp.raise_for_status()
        data = resp.json()

        token = str(data.get("api_token", "")).strip()
        if token:
            self.config.api_token = token
        returned_handle = str(data.get("handle", "")).strip()
        if returned_handle:
            self.config.handle = returned_handle

        raw_profile = str(data.get("profile_url", "")).strip()
        if raw_profile.startswith(("http://", "https://")):
            profile_url = raw_profile
        elif raw_profile.startswith("/"):
            profile_url = f"{self.config.normalized_hub_url}{raw_profile}"
        else:
            profile_url = self.config.profile_url

        if save:
            save_config(self.config, path=self.config_path)

        result = {**data, "profile_url": profile_url}
        self._emit("registered", result)
        return result

    def claim_next(self, preferred_mission: str | None = None) -> dict[str, Any] | None:
        """Claim the next available audit or verification task via POST /api/desk/next."""
        body: dict[str, Any] = {
            "handle": self.config.handle,
            "mode": self.config.mode,
        }
        if preferred_mission:
            body["preferred_mission"] = preferred_mission

        resp = self._client.post(
            self._url("/api/desk/next"),
            headers=self._auth_headers(),
            json=body,
            **self._request_kwargs(),
        )
        if resp.status_code == 204 or not resp.content.strip():
            self._emit("queue_empty", {"handle": self.config.handle})
            return None
        resp.raise_for_status()
        lease = resp.json()
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

        resp = self._client.post(
            self._url("/api/desk/beat"),
            headers=self._auth_headers(),
            json=body,
            **self._request_kwargs(),
        )
        if resp.status_code == 410:
            self._emit("lease_gone", {"lease_id": lease_id})
            return False
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
            resp = self._client.post(
                self._url("/api/desk/release"),
                headers=self._auth_headers(),
                json=body,
                **self._request_kwargs(),
            )
            if resp.status_code == 410:
                return None
            resp.raise_for_status()
            data = resp.json() if resp.content.strip() else {"status": "released"}
            self._emit("task_released", {"lease_id": lease_id, "reason": reason})
            return data
        except httpx.HTTPError:
            return None

    def submit(
        self,
        lease_id: str,
        *,
        outcome: str,
        whisper: str | None = None,
        finding: dict[str, Any] | None = None,
        verification: dict[str, Any] | None = None,
        metrics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Submit task outcome (finding | cleared | verification) to POST /api/desk/submit."""
        body: dict[str, Any] = {
            "lease_id": lease_id,
            "outcome": outcome,
        }
        if whisper is not None:
            body["whisper"] = whisper
        if finding is not None:
            body["finding"] = finding
        if verification is not None:
            body["verification"] = verification
        if metrics is not None:
            body["metrics"] = metrics

        resp = self._client.post(
            self._url("/api/desk/submit"),
            headers=self._auth_headers(),
            json=body,
            **self._request_kwargs(),
        )
        resp.raise_for_status()
        data = resp.json() if resp.content.strip() else {"status": "submitted"}
        self._emit("task_submitted", {"lease_id": lease_id, "outcome": outcome, "response": data})
        return data

    def _fetch_finding(self, finding_id: str) -> dict[str, Any] | None:
        resp = self._client.get(
            self._url(f"/api/findings/{finding_id}"),
            headers=self._auth_headers(),
            **self._request_kwargs(),
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        if isinstance(data, dict) and isinstance(data.get("finding"), dict):
            return data["finding"]
        return data if isinstance(data, dict) else None

    def _load_task_dataset(self, payload: dict[str, Any]) -> LoadedDataset:
        target_ref = _extract_target_ref(payload)
        if target_ref:
            fmt = payload.get("format") or payload.get("file_format")
            load_ctx = _build_task_context(payload)
            params = inspect.signature(load_dataset).parameters
            if "context" in params:
                return load_dataset(
                    target_ref,
                    fmt=str(fmt) if fmt else None,
                    context=load_ctx or None,
                )
            return load_dataset(target_ref, fmt=str(fmt) if fmt else None)

        inline_records = payload.get("records")
        if isinstance(inline_records, list):
            raw_text = str(inline_records)
            return LoadedDataset(
                records=inline_records,
                raw_text=raw_text,
                sha256=compute_sha256(raw_text),
                byte_size=len(raw_text.encode("utf-8")),
                format="json",
                is_demo_sample=False,
                source_ref=str(payload.get("source_ref", "inline-task-payload")),
                pii_redactions=[],
            )
        raise ValueError("Task payload missing dataset_url, dataset_path, source_url, or inline records")

    def _handle_anomaly_scan(self, lease_id: str, task: dict[str, Any]) -> dict[str, Any]:
        task_id = task.get("id")
        mission_slug = str(task.get("mission_slug", ""))
        methodology_code = str(task.get("methodology_code", "")).upper().strip()
        payload = task.get("payload") or {}
        artifact_label = str(
            payload.get("artifact_title") or task.get("title") or mission_slug or methodology_code
        ).strip()
        target_ref = _extract_target_ref(payload)
        secondary_url = _extract_secondary_url(payload)
        engine_context = _build_task_context(payload)

        if not self.heartbeat(
            lease_id,
            whisper=f"Fetching {artifact_label} ({methodology_code}) from official source",
            progress_pct=15,
        ):
            return {"status": "aborted_410", "task_id": task_id, "outcome": "aborted_410"}

        dataset = self._load_task_dataset(payload)

        expected_sha = str(payload.get("expected_sha256") or "").strip().lower()
        sha_matched: bool | None = None
        if expected_sha:
            sha_matched = dataset.sha256.lower() == expected_sha
            sha_note = (
                f"sha256:{dataset.sha256[:8]} matched"
                if sha_matched
                else f"sha256:{dataset.sha256[:8]} (updated from {expected_sha[:8]})"
            )
        else:
            sha_note = f"sha256:{dataset.sha256[:8]}"

        action_verb = "Downloaded" if (target_ref or "").startswith(("http://", "https://")) else "Loaded"
        if not self.heartbeat(
            lease_id,
            whisper=(
                f"{action_verb} {artifact_label} ({len(dataset.records):,} records, {sha_note}); "
                f"running {methodology_code} deterministic checks"
            ),
            progress_pct=50,
        ):
            return {"status": "aborted_410", "task_id": task_id, "outcome": "aborted_410"}

        engine_res = run_engine(
            methodology_code,
            dataset.records,
            context=engine_context or None,
        )

        canonical_url = str(
            payload.get("dataset_url")
            or payload.get("canonical_url")
            or target_ref
            or dataset.source_ref
        ).strip()
        source_url = str(payload.get("source_url") or canonical_url).strip()

        metrics = {
            "records_scanned": engine_res.records_scanned,
            "monetary_audited_inr": engine_res.monetary_audited_inr,
            "cleared_count": engine_res.cleared_count,
            "leads_found": len(engine_res.leads),
            "script_hash": engine_res.script_hash,
            "artifact_sha256": dataset.sha256,
            "expected_sha256_matched": sha_matched,
            "source_ref": dataset.source_ref,
            "canonical_url": canonical_url,
            "is_demo_sample": False,
            "pii_redactions_count": len(dataset.pii_redactions),
        }

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

        top_lead = _select_top_lead(engine_res.leads)
        enriched = enrich_finding_summary(top_lead, self.provider_cfg)

        clean_title, _ = scrub_pii(enriched["title"])
        clean_summary, _ = scrub_pii(enriched["summary"])
        clean_entity, _ = scrub_pii(str(top_lead.entity_name))
        clean_benign = [scrub_pii(b)[0] for b in enriched["benign_explanations"] if str(b).strip()]

        lint_res = lint_neutrality(clean_title, clean_summary, clean_benign)
        if not lint_res.passed:
            raise ValueError(f"Neutrality lint rejected lead before submission: {lint_res.violations}")

        finding_payload: dict[str, Any] = {
            "title": clean_title,
            "summary": clean_summary,
            "methodology_code": top_lead.methodology_code,
            "entity_name": clean_entity,
            "jurisdiction": top_lead.jurisdiction,
            "fiscal_year": top_lead.fiscal_year,
            "monetary_exposure_inr": float(top_lead.monetary_exposure_inr),
            "severity": top_lead.severity,
            "anomaly_metrics": top_lead.anomaly_metrics,
            "rule_triggered": top_lead.rule_triggered,
            "citations": _scrub_citations(
                list(top_lead.citations),
                dataset,
                canonical_url=canonical_url,
                source_url=source_url,
                secondary_url=secondary_url,
            ),
            "benign_explanations": clean_benign,
            "suggested_authority": top_lead.suggested_authority,
            "artifact_id": payload.get("artifact_id"),
            "artifact_title": artifact_label,
            "artifact_sha256": dataset.sha256,
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
                "verifier_script_hash": engine_res.script_hash,
                "artifact_sha256": dataset.sha256,
                "expected_sha256_matched": sha_matched,
                "canonical_url": canonical_url,
                "records_scanned": engine_res.records_scanned,
                "monetary_audited_inr": engine_res.monetary_audited_inr,
                **({"dataset_url": canonical_url} if canonical_url.startswith(("http://", "https://")) else {}),
                **({"secondary_url": secondary_url} if secondary_url else {}),
            },
        }
        if canonical_url.startswith(("http://", "https://")):
            finding_payload["dataset_url"] = canonical_url
        if secondary_url:
            finding_payload["secondary_url"] = secondary_url

        if not self.heartbeat(
            lease_id,
            whisper=(
                f"Submitting {methodology_code} lead for {clean_entity} "
                f"({len(engine_res.leads)} lead(s) from {engine_res.records_scanned:,} records; peer review pending)"
            ),
            progress_pct=90,
        ):
            return {"status": "aborted_410", "task_id": task_id, "outcome": "aborted_410"}

        resp = self.submit(
            lease_id,
            outcome="finding",
            whisper=f"{methodology_code} lead submitted: {clean_title}",
            finding=finding_payload,
            metrics=metrics,
        )
        return {
            "status": "completed",
            "task_id": task_id,
            "outcome": "finding",
            "finding": finding_payload,
            "metrics": metrics,
            "response": resp,
        }

    # Backwards-compatible internal alias
    _execute_anomaly_scan = _handle_anomaly_scan

    def _handle_peer_verify(self, lease_id: str, task: dict[str, Any]) -> dict[str, Any]:
        task_id = task.get("id")
        payload = task.get("payload") or {}
        finding_id = str(
            payload.get("finding_id")
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
            # Hub DeskService embeds finding fields flat inside peer_verify task payload.
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

        # Check 1: Legal neutrality & zero PII safety
        title = str(finding.get("title", ""))
        summary = str(finding.get("summary", ""))
        entity_name = str(finding.get("entity_name", ""))
        benign = finding.get("benign_explanations") or []
        if not isinstance(benign, list):
            benign = []

        pii_hits: list[str] = []
        for text in [title, summary, entity_name, *[str(b) for b in benign]]:
            _, hits = scrub_pii(text)
            pii_hits.extend(hits)

        lint_res = lint_neutrality(title, summary, [str(b) for b in benign])
        legal_pii_ok = bool(lint_res.passed and len(benign) > 0 and len(pii_hits) == 0)

        # Check 2: Independent dataset reload (dataset_url / canonical_url / secondary_url) & SHA-256 verification
        merged_payload: dict[str, Any] = {**payload, "finding": finding}
        if _extract_target_ref(merged_payload) is None and finding_id:
            fetched = self._fetch_finding(finding_id)
            if isinstance(fetched, dict):
                finding = {**fetched, **finding}
                merged_payload = {**payload, "finding": finding}

        dataset = self._load_task_dataset(merged_payload)
        engine_context = _build_task_context(merged_payload)

        expected_sha = str(
            payload.get("expected_sha256")
            or payload.get("artifact_sha256")
            or finding.get("artifact_sha256")
            or (finding.get("agent_trace") or {}).get("artifact_sha256")
            or ""
        ).strip().lower()
        if not expected_sha:
            for cit in finding.get("citations") or []:
                if isinstance(cit, dict) and cit.get("sha256"):
                    expected_sha = str(cit["sha256"]).strip().lower()
                    break

        provenance_ok = (dataset.sha256.lower() == expected_sha) if expected_sha else bool(dataset.records)

        if not self.heartbeat(
            lease_id,
            whisper=(
                f"Re-loaded {len(dataset.records):,} public records (sha256:{dataset.sha256[:8]}); "
                f"independently reproducing {methodology_code} checks for {entity_name or finding_id}"
            ),
            progress_pct=60,
        ):
            return {"status": "aborted_410", "task_id": task_id, "outcome": "aborted_410"}

        # Check 3 & 4: Deterministic math reproduction (1% tolerance) & rule validity
        rule_valid = methodology_code in METHODOLOGY_CATALOG and bool(finding.get("rule_triggered"))
        reproduced_metrics: dict[str, Any] = {}
        math_reproduced = False
        reproduced_leads_count = 0

        if methodology_code in METHODOLOGY_CATALOG:
            repro = run_engine(
                methodology_code,
                dataset.records,
                context=engine_context or None,
            )
            reproduced_leads_count = len(repro.leads)
            matched_lead, math_ok, repro_summary = _match_reproduced_lead(finding, repro.leads)
            math_reproduced = matched_lead is not None and math_ok
            reproduced_metrics = {
                "script_hash": repro.script_hash,
                "artifact_sha256": dataset.sha256,
                "records_scanned": repro.records_scanned,
                "reproduced_leads_count": reproduced_leads_count,
                **repro_summary,
            }
            if matched_lead is not None:
                rule_valid = rule_valid and (matched_lead.methodology_code == methodology_code)

        # Derive structured verdict
        notes_parts: list[str] = []
        if not legal_pii_ok:
            verdict = "flag_pii_or_legal"
            confidence = 0.98
            if pii_hits:
                notes_parts.append(f"PII detected ({', '.join(sorted(set(pii_hits)))})")
            if not lint_res.passed:
                notes_parts.append(f"Neutrality lint failed ({'; '.join(lint_res.violations)})")
            if not benign:
                notes_parts.append("Missing mandatory benign_explanations")
        elif provenance_ok and math_reproduced and rule_valid:
            verdict = "confirm"
            confidence = 0.95
            notes_parts.append(
                f"Independently reproduced {methodology_code} math within 1% tolerance, "
                f"verified sha256:{dataset.sha256[:12]}, neutrality & zero-PII passed."
            )
        elif not math_reproduced and reproduced_leads_count == 0:
            verdict = "reject_false_positive"
            confidence = 0.90
            notes_parts.append(
                f"Independent deterministic re-run of {methodology_code} produced 0 leads on source records."
            )
        else:
            verdict = "dispute"
            confidence = 0.78
            if not provenance_ok:
                notes_parts.append(
                    f"SHA-256 mismatch (expected {expected_sha[:12]}, got {dataset.sha256[:12]})"
                )
            if not math_reproduced:
                notes_parts.append("Reproduced numeric metrics diverged beyond 1% tolerance")
            if not rule_valid:
                notes_parts.append("Methodology/rule validation did not match")

        verification_payload = {
            "finding_id": finding_id,
            "verdict": verdict,
            "checks": {
                "provenance_ok": provenance_ok,
                "math_reproduced": math_reproduced,
                "rule_valid": rule_valid,
                "legal_pii_ok": legal_pii_ok,
            },
            "reproduced_metrics": reproduced_metrics,
            "confidence": confidence,
            "notes": " | ".join(notes_parts),
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
                "records_scanned": len(dataset.records),
                "artifact_sha256": dataset.sha256,
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
        except Exception as exc:
            self.release(
                lease_id,
                reason="execution_error",
                notes=f"{type(exc).__name__}: {exc}",
            )
            self._emit(
                "task_error",
                {"lease_id": lease_id, "task_id": task.get("id"), "error": str(exc)},
            )
            return {
                "status": "error",
                "task_id": task.get("id"),
                "outcome": "released",
                "error": str(exc),
            }

    def run_loop(
        self,
        *,
        max_tasks: int | None = None,
        continuous: bool = False,
        preferred_mission: str | None = None,
        auto_register: bool = True,
        save_on_register: bool = True,
    ) -> RunSummary:
        """Run the claim -> audit/verify -> submit loop up to max_tasks (or continuously)."""
        if auto_register and not self.config.api_token:
            self.register(save=save_on_register)

        effective_max = max_tasks if max_tasks is not None else self.config.max_tasks
        summary = RunSummary(
            handle=self.config.handle,
            hub_url=self.config.normalized_hub_url,
            profile_url=self.config.profile_url,
        )

        while continuous or summary.tasks_claimed < effective_max:
            lease = self.claim_next(preferred_mission=preferred_mission)
            if lease is None:
                if continuous:
                    time.sleep(self.poll_interval)
                    continue
                break

            summary.tasks_claimed += 1
            result = self.execute_task(lease)
            summary.results.append(result)

            outcome = result.get("outcome")
            status = result.get("status")
            if status == "aborted_410":
                summary.aborted_410 += 1
            elif status == "error":
                summary.errors += 1
            elif status == "completed":
                summary.tasks_completed += 1
                if outcome == "finding":
                    summary.findings_submitted += 1
                elif outcome == "cleared":
                    summary.cleared_submitted += 1
                elif outcome == "verification":
                    summary.verifications_submitted += 1

        self._emit("run_finished", summary.model_dump(mode="json"))
        return summary
