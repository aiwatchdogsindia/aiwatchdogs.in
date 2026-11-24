"""Coding-agent attribution: which AI coding agent produced each lead and verification."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aiwatchdogs.core.coding_agents import detect_coding_agent, normalize_coding_agent
from aiwatchdogs.hub.app import create_app


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("claude-code", "Claude Code"),
        ("Antigravity", "Antigravity"),
        ("codex", "Codex"),
        ("gemini cli", "Gemini CLI"),
        ("Some New Agent", "Some New Agent"),
        ("", ""),
    ],
)
def test_normalize_coding_agent(raw: str, expected: str) -> None:
    assert normalize_coding_agent(raw) == expected


def test_detect_claude_code_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDECODE", "1")
    assert detect_coding_agent() == "Claude Code"
    monkeypatch.delenv("CLAUDECODE")
    monkeypatch.delenv("GEMINI_CLI", raising=False)
    assert detect_coding_agent() == ""


def _register(client: TestClient, **extra: str) -> dict:
    body = {"handle": extra.pop("handle"), "provider": "rule-engine", "model": "deterministic-cpu-v1", "mode": "auto"}
    body.update(extra)
    resp = client.post("/api/agents/register", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_registration_records_coding_agent_and_raw_http_provider_backfill(tmp_path: Path) -> None:
    with TestClient(create_app(db_path=tmp_path / "hub.db", seed=True)) as client:
        _register(client, handle="cli-node", coding_agent="claude-code", coding_agent_model="Claude Opus")
        _register(client, handle="http-node", provider="antigravity", model="Gemini 4 Neon")
        agents = {a["handle"]: a for a in client.get("/api/contributors?window=all").json()["contributors"]}
        assert agents["cli-node"]["coding_agent"] == "Claude Code"
        assert agents["cli-node"]["coding_agent_model"] == "Claude Opus"
        assert agents["http-node"]["coding_agent"] == "Antigravity"
        assert agents["http-node"]["coding_agent_model"] == "Gemini 4 Neon"


def test_findings_snapshot_the_submitting_coding_agent(tmp_path: Path) -> None:
    import sqlite3

    db = tmp_path / "hub.db"
    with TestClient(create_app(db_path=db, seed=True)) as client:
        reg = _register(client, handle="snap-node", coding_agent="Codex", coding_agent_model="gpt-5")
        claim = client.post(
            "/api/desk/next",
            headers={"X-Agent-Token": reg["api_token"]},
            json={"handle": "snap-node", "mode": "investigate", "preferred_mission": "assam-ocds-single-bid"},
        )
        assert claim.status_code == 200, claim.text
        finding = {
            "title": "Public Record Anomaly: Single-Bidder Concentration in Public Tenders",
            "summary": "Deterministic scan observed 81 of 228 positive-bid tenders (35.53%) with 1 bid.",
            "methodology_code": "M1",
            "entity_name": "Jorhat Medical College, Assam",
            "jurisdiction": "State:Assam",
            "fiscal_year": "FY 2017-18",
            "monetary_exposure_inr": 604_300_000.0,
            "severity": "high",
            "anomaly_metrics": {"numerator": 81, "denominator": 228},
            "rule_triggered": "CVC-Single-Tender-Guidelines",
            "engine_version": "2.0.0",
            "ruleset_version": "M1-2026.11-v2",
            "source_locator": "rows=1,2,3",
            "artifact_sha256": "01a3ab8b7dcd3023e10f2e14cf7685d16825932feb88a363d69bc1177af4a77e",
            "citations": [{"record_id": "OCID-JMC-1"}],
            "benign_explanations": ["Specialised equipment may lawfully attract a single qualified OEM."],
            "suggested_authority": "Health and Family Welfare Department, Government of Assam",
        }
        sub = client.post(
            "/api/desk/submit",
            headers={"X-Agent-Token": reg["api_token"]},
            json={"lease_id": claim.json()["lease_id"], "outcome": "finding", "finding": finding},
        )
        assert sub.status_code == 200, sub.text
        fid = sub.json()["finding_id"]
        detail = client.get(f"/api/findings/{fid}").json()
        f = detail.get("finding", detail)
        assert f["submitted_coding_agent"] == "Codex"
        assert f["submitted_coding_agent_model"] == "gpt-5"
