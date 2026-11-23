"""Shared Pydantic data models for AI Watchdogs India."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


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
    monetary_exposure_inr: float = 0.0
    severity: Literal["low", "medium", "high", "critical"]
    anomaly_metrics: dict[str, Any] = Field(default_factory=dict)
    rule_triggered: str
    citations: list[dict[str, Any]] = Field(default_factory=list)
    benign_explanations: list[str] = Field(default_factory=list)
    suggested_authority: str


class EngineRunResult(BaseModel):
    """Structured result from running one deterministic methodology engine."""

    methodology_code: str
    records_scanned: int
    monetary_audited_inr: float
    leads: list[AuditLeadCandidate] = Field(default_factory=list)
    cleared_count: int = 0
    script_hash: str
    summary: str


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
