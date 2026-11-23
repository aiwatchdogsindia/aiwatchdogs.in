"""Unit & live-Hub integration tests for AI Watchdogs CLI & AgentRunner."""

from __future__ import annotations

import json
import stat
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from aiwatchdogs.cli.config import (
    ENV_CONFIG_PATH,
    AgentConfig,
    get_config_path,
    load_config,
    mask_token,
    save_config,
)
from aiwatchdogs.cli.main import app as cli_app
from aiwatchdogs.cli.providers import (
    RULE_ENGINE_PROVIDER,
    ProviderInfo,
    detect_best_provider,
    detect_providers,
    enrich_finding_summary,
)
from aiwatchdogs.cli.runner import AgentRunner
from aiwatchdogs.core.safety import compute_sha256, lint_neutrality
from aiwatchdogs.hub.app import create_app

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = REPO_ROOT / "datasets" / "sample"

cli_runner = CliRunner()


# ---------------------------------------------------------------------------
# 1. Config Persistence & 0600 Permissions
# ---------------------------------------------------------------------------


def test_config_persistence_and_0600_permissions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_file = tmp_path / "secure" / "config.json"
    monkeypatch.setenv(ENV_CONFIG_PATH, str(cfg_file))

    assert get_config_path() == cfg_file
    assert load_config() is None

    cfg = AgentConfig(
        handle="civic-node-01",
        hub_url="http://127.0.0.1:8000/",
        api_token="awd_secret_token_1234567890",
        provider="rule-engine",
        model="deterministic-python-v1",
        mode="auto",
        max_tasks=7,
        max_usd=0.0,
    )
    saved_path = save_config(cfg)
    assert saved_path == cfg_file
    assert cfg_file.is_file()
    assert stat.S_IMODE(cfg_file.stat().st_mode) == 0o600

    # Even if permissions are loosened externally, re-saving restores strict 0600.
    cfg_file.chmod(0o644)
    save_config(cfg)
    assert stat.S_IMODE(cfg_file.stat().st_mode) == 0o600

    loaded = load_config()
    assert loaded is not None
    assert loaded.handle == "civic-node-01"
    assert loaded.api_token == "awd_secret_token_1234567890"
    assert loaded.provider == "rule-engine"
    assert loaded.max_tasks == 7
    assert loaded.profile_url == "http://127.0.0.1:8000/a/civic-node-01"
    assert mask_token(loaded.api_token).startswith("awd_se")
    assert "secret_token" not in mask_token(loaded.api_token)


def test_whoami_cli_unconfigured_and_configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_file = tmp_path / "whoami_config.json"
    monkeypatch.setenv(ENV_CONFIG_PATH, str(cfg_file))

    res_empty = cli_runner.invoke(cli_app, ["whoami"])
    assert res_empty.exit_code == 0
    assert "aiwatchdogs join" in res_empty.stdout

    save_config(
        AgentConfig(
            handle="satya-tester",
            hub_url="https://aiwatchdogs.in",
            api_token="awd_supersecret_9876543210",
            provider="rule-engine",
            model="deterministic-python-v1",
            mode="verify",
            max_tasks=10,
            max_usd=0.0,
        )
    )
    res_cfg = cli_runner.invoke(cli_app, ["whoami"])
    assert res_cfg.exit_code == 0
    assert "satya-tester" in res_cfg.stdout
    assert "https://aiwatchdogs.in/a/satya-tester" in res_cfg.stdout
    assert "0o600" in res_cfg.stdout
    assert "supersecret" not in res_cfg.stdout


# ---------------------------------------------------------------------------
# 2. Provider Auto-Detection (<2s) & Neutrality-Guarded Enrichment
# ---------------------------------------------------------------------------


def test_detect_providers_offline_fallback_rule_engine() -> None:
    def _fail_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused", request=request)

    t0 = time.monotonic()
    with httpx.Client(transport=httpx.MockTransport(_fail_handler)) as client:
        providers = detect_providers(probe_local=True, env={}, http_client=client)
        best = detect_best_provider(probe_local=True, env={}, http_client=client)
    elapsed = time.monotonic() - t0

    assert elapsed < 2.0
    assert len(providers) == 1
    assert providers[0].provider == RULE_ENGINE_PROVIDER
    assert providers[0].zero_cost is True
    assert "₹0" in providers[0].label
    assert best.provider == RULE_ENGINE_PROVIDER


def test_detect_providers_ollama_vllm_and_cloud_keys() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={"models": [{"name": "llama3.1:8b"}, {"name": "qwen2.5:14b"}]},
            )
        if request.url.path.endswith("/models"):
            return httpx.Response(
                200,
                json={"data": [{"id": "Qwen/Qwen2.5-32B-Instruct"}]},
            )
        return httpx.Response(404)

    env = {
        "OLLAMA_HOST": "http://127.0.0.1:11434",
        "VLLM_BASE_URL": "http://127.0.0.1:8001/v1",
        "GEMINI_API_KEY": "test-gemini-key",
        "ANTHROPIC_API_KEY": "test-anthropic-key",
        "OPENAI_API_KEY": "test-openai-key",
        "OPENROUTER_API_KEY": "test-openrouter-key",
    }

    t0 = time.monotonic()
    with httpx.Client(transport=httpx.MockTransport(_handler)) as client:
        providers = detect_providers(probe_local=True, env=env, http_client=client)
    elapsed = time.monotonic() - t0

    assert elapsed < 2.0
    names = [p.provider for p in providers]
    assert names == ["ollama", "vllm", "gemini", "anthropic", "openai", "openrouter", "rule-engine"]
    assert providers[0].model == "qwen2.5:14b"
    assert providers[1].model == "Qwen/Qwen2.5-32B-Instruct"


def test_enrich_finding_summary_llm_success_and_neutrality_fallback() -> None:
    sample_lead = {
        "title": "Public Record Anomaly: Split-tendering cluster below INR 10L GeM threshold",
        "summary": "Deterministic scan flagged 4 POs totaling INR 38.4L priced at 95%-98% of the INR 10L ceiling.",
        "methodology_code": "M2",
        "entity_name": "Sample Public Buyer Division",
        "jurisdiction": "Union of India",
        "fiscal_year": "FY2024-25",
        "monetary_exposure_inr": 3840000.0,
        "rule_triggered": "GFR-2017-R149-Split-Tendering",
        "anomaly_metrics": {"po_count": 4, "threshold_inr": 1000000},
        "benign_explanations": [
            "Urgent staggered field requirements across separate sub-units may result in sequential POs."
        ],
    }

    # 1. Deterministic rule-engine mode (zero HTTP calls)
    det = enrich_finding_summary(sample_lead, None)
    assert det["used_llm"] is False
    assert det["neutrality_passed"] is True
    assert det["enriched_by"] == "rule-engine"

    # 2. Ollama returning clean, neutral enriched JSON
    def _ollama_ok(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat"
        payload = {
            "summary": (
                "Four sequential GeM purchase orders totaling INR 38.40 lakh fell within 95%-98% "
                "of the INR 10 lakh bidding ceiling under GFR 2017 Rule 149."
            ),
            "benign_explanations": [
                "Independent sub-offices may have raised separate urgent indents under delegated powers.",
                "Emergency replenishment during peak operational demand can require rapid repeat orders.",
            ],
        }
        return httpx.Response(200, json={"message": {"content": json.dumps(payload)}})

    ollama_cfg = ProviderInfo(
        provider="ollama",
        model="qwen2.5:14b",
        label="Local Ollama",
        is_local=True,
        zero_cost=True,
        base_url="http://127.0.0.1:11434",
    )
    with httpx.Client(transport=httpx.MockTransport(_ollama_ok)) as client:
        enriched = enrich_finding_summary(sample_lead, ollama_cfg, http_client=client)
    assert enriched["used_llm"] is True
    assert enriched["enriched_by"] == "ollama/qwen2.5:14b"
    assert enriched["neutrality_passed"] is True
    assert len(enriched["benign_explanations"]) == 2

    # 3. Cloud LLM violating BNS Sec 356 neutrality -> MUST fall back cleanly to deterministic phrasing!
    def _openai_defamatory(request: httpx.Request) -> httpx.Response:
        bad = {
            "summary": "This department ran an obvious corruption scam and bribe scheme to loot funds.",
            "benign_explanations": ["None, they are clearly guilty."],
        }
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(bad)}}]})

    openai_cfg = ProviderInfo(
        provider="openai",
        model="gpt-4o-mini",
        label="OpenAI",
        api_key_env="OPENAI_API_KEY",
        base_url="https://api.openai.com/v1",
    )
    with httpx.Client(transport=httpx.MockTransport(_openai_defamatory)) as client:
        safe_fallback = enrich_finding_summary(
            sample_lead,
            openai_cfg,
            http_client=client,
            env={"OPENAI_API_KEY": "sk-test"},
        )
    assert safe_fallback["used_llm"] is False
    assert safe_fallback["fallback_reason"] is not None
    assert "neutrality_violation" in safe_fallback["fallback_reason"]
    assert safe_fallback["summary"] == sample_lead["summary"]
    assert lint_neutrality(
        safe_fallback["title"],
        safe_fallback["summary"],
        safe_fallback["benign_explanations"],
    ).passed

    # 4. Unavailable LLM -> clean deterministic fallback
    def _gemini_down(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="Service Unavailable")

    gemini_cfg = ProviderInfo(
        provider="gemini",
        model="gemini-2.5-flash",
        label="Gemini",
        api_key_env="GEMINI_API_KEY",
        base_url="https://generativelanguage.googleapis.com/v1beta",
    )
    with httpx.Client(transport=httpx.MockTransport(_gemini_down)) as client:
        down_fallback = enrich_finding_summary(
            sample_lead,
            gemini_cfg,
            http_client=client,
            env={"GEMINI_API_KEY": "test-key"},
        )
    assert down_fallback["used_llm"] is False
    assert down_fallback["fallback_reason"] == "llm_unavailable"


# ---------------------------------------------------------------------------
# 3. Standalone Offline `aiwatchdogs audit-file` CLI Tests (M1, M2, M5 + --rti)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("filename", "methodology"),
    [
        ("m1_ocds_procurement.json", "M1"),
        ("m2_gem_split_tenders.csv", "M2"),
        ("m5_cga_march_rush.csv", "M5"),
    ],
)
def test_audit_file_sample_datasets_json_and_rti(filename: str, methodology: str) -> None:
    sample_path = SAMPLE_DIR / filename
    assert sample_path.is_file(), f"Expected sample dataset {sample_path}"

    res = cli_runner.invoke(
        cli_app,
        ["audit-file", str(sample_path), "--methodology", methodology, "--json", "--rti"],
    )
    assert res.exit_code == 0, res.output
    data = json.loads(res.stdout)

    assert data["methodology_code"] == methodology
    assert data["is_demo_sample"] is True
    assert data["records_scanned"] > 0
    assert len(data["sha256"]) == 64
    assert len(data["leads"]) >= 1
    assert all(lead["legal_lint_passed"] is True for lead in data["leads"])
    assert all(len(lead["benign_explanations"]) >= 1 for lead in data["leads"])

    assert len(data["rti_drafts"]) == len(data["leads"])
    for draft in data["rti_drafts"]:
        assert 20 <= draft["word_count"] <= 500
        assert "rtionline.gov.in" in draft["rti_portal_url"]
        assert "Section 6(1)" in draft["statutory_sections"]


def test_audit_file_rich_output_and_cleared_file(tmp_path: Path) -> None:
    # Rich formatted report with RTI panel
    res = cli_runner.invoke(
        cli_app,
        ["audit-file", str(SAMPLE_DIR / "m2_gem_split_tenders.csv"), "--methodology", "M2", "--rti"],
    )
    assert res.exit_code == 0, res.output
    assert "SAMPLE DEMO DATASET" in res.stdout
    assert "RTI Act 2005" in res.stdout

    # Clean file with 0 threshold-hugging POs -> CLEARED
    clean_csv = tmp_path / "clean_tenders.csv"
    clean_csv.write_text(
        "tender_id,buyer_name,vendor_name,item_category,award_date,award_value_inr,jurisdiction,fiscal_year\n"
        "PO-1,Clean Dept,Vendor Alpha,Stationery,2024-06-01,12000,Union of India,FY2024-25\n"
        "PO-2,Clean Dept,Vendor Beta,Stationery,2024-09-15,15000,Union of India,FY2024-25\n",
        encoding="utf-8",
    )
    res_clean = cli_runner.invoke(
        cli_app,
        ["audit-file", str(clean_csv), "--methodology", "M2", "--json"],
    )
    assert res_clean.exit_code == 0, res_clean.output
    clean_data = json.loads(res_clean.stdout)
    assert clean_data["records_scanned"] == 2
    assert clean_data["leads"] == []

    # Invalid methodology code
    res_bad = cli_runner.invoke(
        cli_app,
        ["audit-file", str(clean_csv), "--methodology", "M99"],
    )
    assert res_bad.exit_code == 2


# ---------------------------------------------------------------------------
# 4. Full End-to-End AgentRunner Flow Against Live FastAPI Hub (ASGI)
# ---------------------------------------------------------------------------


def test_runner_full_claim_audit_submit_and_2of2_peer_verification_quorum(tmp_path: Path) -> None:
    hub_app = create_app(db_path=tmp_path / "live_hub.db", seed=True)

    with TestClient(hub_app, base_url="http://testserver") as asgi_client:
        # Node 1: Investigator claims M2 scan task, audits real sample CSV, submits neutral finding
        cfg_investigator = AgentConfig(
            handle="cli-auditor-one",
            hub_url="http://testserver",
            provider="rule-engine",
            model="deterministic-python-v1",
            mode="investigate",
            max_tasks=1,
            max_usd=0.0,
        )
        inv_cfg_path = tmp_path / "node1.json"
        runner_inv = AgentRunner(
            cfg_investigator,
            http_client=asgi_client,
            config_path=inv_cfg_path,
        )
        summary_inv = runner_inv.run_loop(
            max_tasks=1,
            preferred_mission="gem-split-tendering-threshold",
        )

        assert stat.S_IMODE(inv_cfg_path.stat().st_mode) == 0o600
        assert summary_inv.tasks_completed == 1
        assert summary_inv.findings_submitted == 1
        assert summary_inv.errors == 0

        submit_resp = summary_inv.results[0]["response"]
        finding_id = submit_resp["finding_id"]
        assert submit_resp["finding_status"] == "peer_review"
        assert submit_resp["peer_verify_tasks_created"] == 2

        # Verify finding is stored in peer_review before independent checks
        f_before = asgi_client.get(f"/api/findings/{finding_id}").json()
        assert f_before["status"] == "peer_review"
        assert f_before["confirm_count"] == 0

        # Node 2 & Node 3: Two independent peer verifiers re-load CSV, check SHA-256,
        # reproduce deterministic M2 math within 1% tolerance, check neutrality & zero PII!
        for idx, handle in enumerate(("cli-verifier-two", "cli-verifier-three"), start=1):
            cfg_ver = AgentConfig(
                handle=handle,
                hub_url="http://testserver",
                provider="rule-engine",
                model="deterministic-python-v1",
                mode="verify",
                max_tasks=1,
                max_usd=0.0,
            )
            runner_ver = AgentRunner(
                cfg_ver,
                http_client=asgi_client,
                config_path=tmp_path / f"verifier_{idx}.json",
            )
            summary_ver = runner_ver.run_loop(
                max_tasks=1,
                preferred_mission="gem-split-tendering-threshold",
            )
            assert summary_ver.tasks_completed == 1
            assert summary_ver.verifications_submitted == 1
            verif_payload = summary_ver.results[0]["verification"]
            assert verif_payload["finding_id"] == finding_id
            assert verif_payload["verdict"] == "confirm"
            assert verif_payload["checks"] == {
                "provenance_ok": True,
                "math_reproduced": True,
                "rule_valid": True,
                "legal_pii_ok": True,
            }

        # Confirm Hub promoted the finding to VERIFIED and auto-generated <=500-word RTI draft!
        f_after = asgi_client.get(f"/api/findings/{finding_id}").json()
        assert f_after["status"] == "verified"
        assert f_after["confirm_count"] == 2
        assert f_after["verification_score"] == 1.0
        assert len(f_after["verifications"]) == 2
        assert f_after["rti_draft"] is not None
        assert 20 <= f_after["rti_draft"]["word_count"] <= 500


@pytest.mark.asyncio
async def test_httpx_asgi_transport_live_hub_health_and_head(tmp_path: Path) -> None:
    """Smoke-check direct httpx.ASGITransport compatibility with the live FastAPI Hub."""
    hub_app = create_app(db_path=tmp_path / "asgi_smoke.db", seed=True)
    transport = httpx.ASGITransport(app=hub_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        health = await client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        head = await client.get("/api/head")
        assert head.status_code == 200
        assert head.json()["stats"]["tasks_queued"] >= 8


# ---------------------------------------------------------------------------
# 5. Edge Cases: Cleared Outcome, 410 Gone, Exception Release, Peer Verdicts
# ---------------------------------------------------------------------------


def test_runner_cleared_410_gone_and_exception_release(tmp_path: Path) -> None:
    clean_csv = tmp_path / "no_anomalies.csv"
    clean_csv.write_text(
        "tender_id,buyer_name,vendor_name,item_category,award_date,award_value_inr,jurisdiction,fiscal_year\n"
        "PO-10,Clean Office,Vendor X,Books,2024-07-01,10000,Union of India,FY2024-25\n",
        encoding="utf-8",
    )

    # Case A: Cleared outcome (0 leads)
    submitted_bodies: list[dict[str, Any]] = []
    tasks_a = [
        {
            "lease_id": "lease-clear-1",
            "lease_expires_at": "2099-01-01T00:00:00Z",
            "task": {
                "id": "tsk-clear-1",
                "task_type": "anomaly_scan",
                "mission_slug": "gem-split-tendering-threshold",
                "methodology_code": "M2",
                "title": "Clean dataset scan",
                "instructions": "Run M2",
                "payload": {"dataset_path": str(clean_csv)},
            },
        }
    ]

    def _handler_a(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/desk/next":
            return httpx.Response(200, json=tasks_a.pop(0)) if tasks_a else httpx.Response(204)
        if request.url.path == "/api/desk/beat":
            return httpx.Response(200, json={"status": "active", "lease_expires_at": "2099-01-01T00:00:00Z"})
        if request.url.path == "/api/desk/submit":
            submitted_bodies.append(json.loads(request.content))
            return httpx.Response(200, json={"status": "accepted", "outcome": "cleared"})
        return httpx.Response(404)

    cfg = AgentConfig(handle="edge-node", hub_url="http://mockhub", api_token="awd_tok")
    with httpx.Client(transport=httpx.MockTransport(_handler_a), base_url="http://mockhub") as client:
        summary_a = AgentRunner(cfg, http_client=client).run_loop(max_tasks=2)
    assert summary_a.cleared_submitted == 1
    assert submitted_bodies[0]["outcome"] == "cleared"

    # Case B: 410 Gone on beat stops cleanly without submitting
    submit_called_b = False
    tasks_b = [
        {
            "lease_id": "lease-410",
            "lease_expires_at": "2099-01-01T00:00:00Z",
            "task": {
                "id": "tsk-410",
                "task_type": "anomaly_scan",
                "mission_slug": "gem-split-tendering-threshold",
                "methodology_code": "M2",
                "title": "Already settled task",
                "instructions": "Run M2",
                "payload": {"dataset_path": str(clean_csv)},
            },
        }
    ]

    def _handler_b(request: httpx.Request) -> httpx.Response:
        nonlocal submit_called_b
        if request.url.path == "/api/desk/next":
            return httpx.Response(200, json=tasks_b.pop(0)) if tasks_b else httpx.Response(204)
        if request.url.path == "/api/desk/beat":
            return httpx.Response(410, json={"detail": "Task already settled"})
        if request.url.path == "/api/desk/submit":
            submit_called_b = True
            return httpx.Response(410)
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(_handler_b), base_url="http://mockhub") as client:
        summary_b = AgentRunner(cfg, http_client=client).run_loop(max_tasks=1)
    assert summary_b.aborted_410 == 1
    assert submit_called_b is False

    # Case C: Exception during task execution releases lease cleanly
    released_bodies: list[dict[str, Any]] = []
    tasks_c = [
        {
            "lease_id": "lease-err",
            "lease_expires_at": "2099-01-01T00:00:00Z",
            "task": {
                "id": "tsk-err",
                "task_type": "anomaly_scan",
                "mission_slug": "gem-split-tendering-threshold",
                "methodology_code": "M2",
                "title": "Missing file task",
                "instructions": "Run M2",
                "payload": {"dataset_path": str(tmp_path / "does_not_exist.csv")},
            },
        }
    ]

    def _handler_c(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/desk/next":
            return httpx.Response(200, json=tasks_c.pop(0)) if tasks_c else httpx.Response(204)
        if request.url.path == "/api/desk/beat":
            return httpx.Response(200, json={"status": "active"})
        if request.url.path == "/api/desk/release":
            released_bodies.append(json.loads(request.content))
            return httpx.Response(200, json={"status": "released"})
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(_handler_c), base_url="http://mockhub") as client:
        summary_c = AgentRunner(cfg, http_client=client).run_loop(max_tasks=1)
    assert summary_c.errors == 1
    assert len(released_bodies) == 1
    assert released_bodies[0]["lease_id"] == "lease-err"
    assert released_bodies[0]["reason"] == "execution_error"


def test_runner_peer_verify_negative_verdicts(tmp_path: Path) -> None:
    clean_csv = tmp_path / "clean_m2.csv"
    clean_csv.write_text(
        "tender_id,buyer_name,vendor_name,item_category,award_date,award_value_inr,jurisdiction,fiscal_year\n"
        "PO-1,Dept A,Vendor A,Chairs,2024-05-01,10000,Union of India,FY2024-25\n",
        encoding="utf-8",
    )
    clean_sha = compute_sha256(clean_csv.read_bytes())

    m2_sample = SAMPLE_DIR / "m2_gem_split_tenders.csv"
    submitted: list[dict[str, Any]] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/desk/beat":
            return httpx.Response(200, json={"status": "active"})
        if request.url.path == "/api/desk/submit":
            submitted.append(json.loads(request.content))
            return httpx.Response(200, json={"status": "accepted"})
        return httpx.Response(404)

    cfg = AgentConfig(handle="strict-verifier", hub_url="http://mockhub", api_token="awd_tok")
    with httpx.Client(transport=httpx.MockTransport(_handler), base_url="http://mockhub") as client:
        runner = AgentRunner(cfg, http_client=client)

        # 1. PII / accusatory wording -> flag_pii_or_legal
        res_pii = runner.execute_task(
            {
                "lease_id": "l-pii",
                "task": {
                    "id": "t-pii",
                    "task_type": "peer_verify",
                    "methodology_code": "M2",
                    "payload": {
                        "finding_id": "f-bad-legal",
                        "dataset_path": str(clean_csv),
                        "artifact_sha256": clean_sha,
                        "methodology_code": "M2",
                        "title": "Obvious bribe scam involving PAN ABCDE1234F",
                        "summary": "Accusatory text with personal email officer@gmail.com.",
                        "entity_name": "Dept A",
                        "rule_triggered": "M2",
                        "benign_explanations": [],
                    },
                },
            }
        )
        assert res_pii["verification"]["verdict"] == "flag_pii_or_legal"
        assert res_pii["verification"]["checks"]["legal_pii_ok"] is False

        # 2. Fabricated anomaly on clean dataset -> reject_false_positive
        res_fp = runner.execute_task(
            {
                "lease_id": "l-fp",
                "task": {
                    "id": "t-fp",
                    "task_type": "peer_verify",
                    "methodology_code": "M2",
                    "payload": {
                        "finding_id": "f-false-pos",
                        "dataset_path": str(clean_csv),
                        "artifact_sha256": clean_sha,
                        "methodology_code": "M2",
                        "title": "Public Record Anomaly: Claimed split-tendering cluster",
                        "summary": "Claimed 5 threshold-hugging POs on clean dataset.",
                        "entity_name": "Dept A",
                        "monetary_exposure_inr": 4800000.0,
                        "anomaly_metrics": {"po_count": 5},
                        "rule_triggered": "GFR-2017-R149",
                        "benign_explanations": ["Staggered lawful departmental indents."],
                    },
                },
            }
        )
        assert res_fp["verification"]["verdict"] == "reject_false_positive"
        assert res_fp["verification"]["checks"]["math_reproduced"] is False

        # 3. Real anomaly dataset with wrong SHA-256 -> dispute
        res_sha = runner.execute_task(
            {
                "lease_id": "l-sha",
                "task": {
                    "id": "t-sha",
                    "task_type": "peer_verify",
                    "methodology_code": "M2",
                    "payload": {
                        "finding_id": "f-bad-sha",
                        "dataset_path": str(m2_sample),
                        "artifact_sha256": "0" * 64,
                        "methodology_code": "M2",
                        "title": "Public Record Anomaly: Threshold cluster",
                        "summary": "Summary of threshold cluster.",
                        "entity_name": "Sample Buyer",
                        "monetary_exposure_inr": 100.0,
                        "rule_triggered": "GFR-2017-R149",
                        "benign_explanations": ["Lawful urgent indents."],
                    },
                },
            }
        )
        assert res_sha["verification"]["verdict"] == "dispute"
        assert res_sha["verification"]["checks"]["provenance_ok"] is False

    assert len(submitted) == 3


# ---------------------------------------------------------------------------
# 6. Typer CLI `join --yes`, `status`, and `missions` Against Live FastAPI Hub
# ---------------------------------------------------------------------------


def test_cli_join_status_missions_against_live_hub(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg_file = tmp_path / "cli_join_config.json"
    monkeypatch.setenv(ENV_CONFIG_PATH, str(cfg_file))

    hub_app = create_app(db_path=tmp_path / "cli_hub.db", seed=True)

    with TestClient(hub_app, base_url="http://testserver") as asgi_client:
        # Route CLI httpx calls & AgentRunner to the in-process ASGI TestClient
        orig_init = AgentRunner.__init__

        def _patched_init(self: AgentRunner, config: AgentConfig, **kwargs: Any) -> None:
            kwargs.setdefault("http_client", asgi_client)
            orig_init(self, config, **kwargs)

        def _patched_get(url: str, **kwargs: Any) -> httpx.Response:
            path = url.replace("http://testserver", "")
            return asgi_client.get(path)

        monkeypatch.setattr(AgentRunner, "__init__", _patched_init)
        monkeypatch.setattr(httpx, "get", _patched_get)

        # 1. `aiwatchdogs join --yes`
        res_join = cli_runner.invoke(
            cli_app,
            [
                "join",
                "--hub",
                "http://testserver",
                "--handle",
                "cli-join-node",
                "--provider",
                "rule-engine",
                "--mode",
                "investigate",
                "--max-tasks",
                "1",
                "--yes",
            ],
        )
        assert res_join.exit_code == 0, res_join.output
        assert "Follow your agent live" in res_join.stdout
        assert "http://testserver/a/cli-join-node" in res_join.stdout
        assert cfg_file.is_file()
        assert stat.S_IMODE(cfg_file.stat().st_mode) == 0o600

        saved = load_config(cfg_file)
        assert saved is not None
        assert saved.handle == "cli-join-node"
        assert saved.api_token.startswith("awd_")

        # 2. `aiwatchdogs run` using saved config (headless)
        res_run = cli_runner.invoke(
            cli_app,
            ["run", "--mode", "investigate", "--max-tasks", "1"],
        )
        assert res_run.exit_code == 0, res_run.output
        assert "Agent Run Summary" in res_run.stdout
        assert "cli-join-node" in res_run.stdout

        # 3. `aiwatchdogs status --hub http://testserver`
        res_status = cli_runner.invoke(cli_app, ["status", "--hub", "http://testserver"])
        assert res_status.exit_code == 0, res_status.output
        assert "Public Spend Audited" in res_status.stdout
        assert "Peer-Verified Leads" in res_status.stdout

        # 4. `aiwatchdogs missions --hub http://testserver` (wide COLUMNS so Rich keeps full slugs)
        res_missions = cli_runner.invoke(
            cli_app,
            ["missions", "--hub", "http://testserver"],
            env={"COLUMNS": "140"},
        )
        assert res_missions.exit_code == 0, res_missions.output
        assert "ocds-single-bid-assam-hp" in res_missions.stdout
        assert "gem-split-tendering-threshold" in res_missions.stdout
