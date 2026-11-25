"""Shared Pydantic data models for AI Watchdogs India."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class LegalLintResult(BaseModel):
    """Outcome of the automated BNS Sec 356 neutrality and fairness linter."""

    passed: bool
    violations: list[str] = Field(default_factory=list)


class AuditLeadCandidate(BaseModel):
    """A neutral, evidence-backed civic-audit lead produced by a deterministic engine."""

    title: str
    summary: str
    methodology_code: str
    entity_name: str
    jurisdiction: str
    fiscal_year: str
    monetary_exposure_inr: float = Field(default=0.0, ge=0.0, allow_inf_nan=False)
    severity: Literal["low", "medium", "high", "critical"]
    anomaly_metrics: dict[str, Any] = Field(default_factory=dict)
    rule_triggered: str
    citations: list[dict[str, Any]] = Field(default_factory=list)
    benign_explanations: list[str] = Field(default_factory=list)
    suggested_authority: str
    fiscal_year_start: str | None = None
    fiscal_year_end: str | None = None
    source_locator: str | None = None
    amount_type: str | None = None
    ruleset_version: str | None = None


class EngineRunResult(BaseModel):
    """Structured result from running one deterministic methodology engine."""

    methodology_code: str
    records_scanned: int = Field(ge=0)
    monetary_audited_inr: float = Field(ge=0.0, allow_inf_nan=False)
    leads: list[AuditLeadCandidate] = Field(default_factory=list)
    cleared_count: int = Field(default=0, ge=0)
    unevaluable_count: int = Field(default=0, ge=0)
    script_hash: str
    summary: str
    engine_version: str = "2.0.0"
    ruleset_version: str = ""
    as_of_date: str | None = None
    fingerprint_manifest: dict[str, Any] = Field(default_factory=dict)
    amount_type: str | None = None


class LoadedDataset(BaseModel):
    """Normalized public dataset ingested from a local file or Tier-1 public URL."""

    records: list[dict[str, Any]] = Field(default_factory=list)
    raw_text: str = ""
    sha256: str
    byte_size: int
    format: str
    is_demo_sample: bool = False
    source_ref: str
    pii_redactions: list[str] = Field(default_factory=list)
    observed_sha256: str = ""
    expected_sha256: str | None = None
    digest_verified: bool | None = None
    secondary_sha256: str | None = None
    secondary_expected_sha256: str | None = None
    secondary_digest_verified: bool | None = None
    secondary_provenance: dict[str, Any] | None = None
    official_publisher: str | None = None
    source_landing_page: str | None = None
    mirror_name: str | None = None
    retrieval_url: str | None = None
    fetched_at: str | None = None
    source_locator: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _populate_observed_provenance(self) -> LoadedDataset:
        if not self.observed_sha256:
            self.observed_sha256 = self.sha256
        if not self.provenance:
            self.provenance = {
                "official_publisher": self.official_publisher,
                "source_landing_page": self.source_landing_page,
                "mirror_name": self.mirror_name,
                "retrieval_url": self.retrieval_url or self.source_ref,
                "observed_sha256": self.observed_sha256,
                "expected_sha256": self.expected_sha256,
                "digest_verified": self.digest_verified,
                "byte_size": self.byte_size,
                "fetched_at": self.fetched_at,
                "source_locator": self.source_locator,
            }
        return self
