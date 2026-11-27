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
    DEFAULT_HUB_URL,
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
    resolve_provider_config,
)
from aiwatchdogs.cli.runner import AgentRunner
from aiwatchdogs.core.models import LoadedDataset
from aiwatchdogs.core.safety import compute_sha256, lint_neutrality
from aiwatchdogs.hub.app import create_app

cli_runner = CliRunner()


# ---------------------------------------------------------------------------
# Inline Realistic Fixtures (Zero datasets/sample/ dependency)
# ---------------------------------------------------------------------------


def _write_m1_ocds_fixture(path: Path) -> Path:
    """Create a realistic OCDS JSON fixture that triggers M1 single-bidder concentration."""
    releases = []
    for idx in range(1, 7):
        single = idx <= 4
        releases.append(
            {
                "ocid": f"ocds-assam-2024-{idx:03d}",
                "id": f"rel-{idx:03d}",
                "date": f"2024-05-{10 + idx:02d}T10:00:00Z",
                "jurisdiction": "Assam",
                "fiscal_year": "FY2024-25",
                "source_url": "https://assamtenders.gov.in/",
                "buyer": {"id": "BYR-AS-HF", "name": "Assam Health & Family Welfare Directorate"},
                "tender": {
                    "id": f"TND-AS-{idx:03d}",
                    "title": f"District Medical Equipment Supply Lot {idx}",
                    "procurementMethod": "open",
                    "numberOfTenderers": 1 if single else 4,
                    "tenderPeriod": {
                        "startDate": "2024-05-01T10:00:00Z",
                        "endDate": "2024-05-08T10:00:00Z" if single else "2024-05-25T10:00:00Z",
                    },
                    "value": {"amount": 4500000 + idx * 100000, "currency": "INR"},
                    "items": [{"classification": {"description": "Hospital Equipment"}}],
                },
                "awards": [
                    {
                        "id": f"AWD-AS-{idx:03d}",
                        "date": f"2024-05-{15 + idx:02d}T10:00:00Z",
                        "value": {"amount": 4500000 + idx * 100000, "currency": "INR"},
                        "suppliers": [
                            {
                                "id": "SUP-01" if single else f"SUP-{idx:02d}",
                                "name": "Brahmaputra MedTech Pvt Ltd" if single else f"Vendor {idx} Enterprises",
                            }
                        ],
                    }
                ],
            }
        )
    package = {
        "uri": "https://assam.open-contracting.in/datasets",
        "jurisdiction": "Assam",
        "fiscal_year": "FY2024-25",
        "releases": releases,
    }
    path.write_text(json.dumps(package, indent=2), encoding="utf-8")
    return path


def _write_m2_split_csv_fixture(path: Path, *, multi_buyer: bool = False) -> Path:
    """Create a realistic post-Jul 2024 Central GFR 2017 split-tendering CSV fixture for M2."""
    rows = [
        "tender_id,buyer_name,vendor_name,item_category,award_date,award_value_inr,jurisdiction,fiscal_year,source_url",
        "PO-101,Central Medical Procurement Cell,NorthEast Diagnostics,Lab Kits,2024-08-02,4850000,Union of India,FY2024-25,https://eprocure.gov.in/",
        "PO-102,Central Medical Procurement Cell,NorthEast Diagnostics,Lab Kits,2024-08-14,4910000,Union of India,FY2024-25,https://eprocure.gov.in/",
        "PO-103,Central Medical Procurement Cell,NorthEast Diagnostics,Lab Kits,2024-08-29,4940000,Union of India,FY2024-25,https://eprocure.gov.in/",
        "PO-104,Central Medical Procurement Cell,NorthEast Diagnostics,Lab Kits,2024-09-09,4880000,Union of India,FY2024-25,https://eprocure.gov.in/",
    ]
    if multi_buyer:
        rows.extend(
            [
                "PO-201,Central PWD Works Division,Gauhati Infra Co,Road Signages,2024-07-15,960000,Union of India,FY2024-25,https://eprocure.gov.in/",
                "PO-202,Central PWD Works Division,Gauhati Infra Co,Road Signages,2024-07-24,980000,Union of India,FY2024-25,https://eprocure.gov.in/",
                "PO-203,Central PWD Works Division,Gauhati Infra Co,Road Signages,2024-08-05,975000,Union of India,FY2024-25,https://eprocure.gov.in/",
            ]
        )
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def _write_m5_march_rush_csv_fixture(path: Path) -> Path:
    """Create a realistic AP Monthly Treasury CSV fixture triggering GFR Rule 62(3) March Rush."""
    rows = [
        "District,Year,Month,Major Head,Major Head Description,Amount,source_url",
        "Krishna,2016,APR,2202,General Education,80000000,https://treasury.ap.gov.in",
        "Krishna,2016,MAY,2202,General Education,85000000,https://treasury.ap.gov.in",
        "Krishna,2016,JUN,2202,General Education,90000000,https://treasury.ap.gov.in",
        "Krishna,2016,JUL,2202,General Education,95000000,https://treasury.ap.gov.in",
        "Krishna,2016,AUG,2202,General Education,90000000,https://treasury.ap.gov.in",
        "Krishna,2016,SEP,2202,General Education,100000000,https://treasury.ap.gov.in",
        "Krishna,2016,OCT,2202,General Education,95000000,https://treasury.ap.gov.in",
        "Krishna,2016,NOV,2202,General Education,90000000,https://treasury.ap.gov.in",
        "Krishna,2016,DEC,2202,General Education,105000000,https://treasury.ap.gov.in",
        "Krishna,2017,JAN,2202,General Education,150000000,https://treasury.ap.gov.in",
        "Krishna,2017,FEB,2202,General Education,180000000,https://treasury.ap.gov.in",
        "Krishna,2017,MAR,2202,General Education,440000000,https://treasury.ap.gov.in",
    ]
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 1. Config Persistence & 0600 Permissions
# ---------------------------------------------------------------------------


def test_config_persistence_and_0600_permissions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert DEFAULT_HUB_URL == "https://aiwatchdogs.in"
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
            handle="civic-auditor",
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
    assert "civic-auditor" in res_cfg.stdout
    assert "https://aiwatchdogs.in/a/civic-auditor" in res_cfg.stdout
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
    assert "Deterministic" in providers[0].label
    assert best.provider == RULE_ENGINE_PROVIDER


def test_detect_providers_cloud_keys_only_and_ignores_local_ollama_vllm() -> None:
    seen_requests: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        return httpx.Response(500)

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
    assert seen_requests == []
    names = [p.provider for p in providers]
    assert names == ["gemini", "anthropic", "openai", "openrouter", "rule-engine"]
    # Legacy/removed local provider strings degrade safely to rule-engine
    assert resolve_provider_config("ollama", env=env).provider == RULE_ENGINE_PROVIDER
    assert resolve_provider_config("vllm", env=env).provider == RULE_ENGINE_PROVIDER


def test_enrich_finding_summary_llm_success_and_neutrality_fallback() -> None:
    audit_lead = {
        "title": "Public Record Anomaly: Split-tendering cluster below INR 10L GeM threshold",
        "summary": "Deterministic scan flagged 4 POs totaling INR 38.4L priced at 95%-98% of the INR 10L ceiling.",
        "methodology_code": "M2",
        "entity_name": "District Public Works Division",
        "jurisdiction": "Assam",
        "fiscal_year": "FY2024-25",
        "monetary_exposure_inr": 3840000.0,
        "rule_triggered": "GFR-2017-R149-Split-Tendering",
        "anomaly_metrics": {"po_count": 4, "threshold_inr": 1000000},
        "benign_explanations": [
            "Urgent staggered field requirements across separate sub-units may result in sequential POs."
        ],
    }

    # 1. Deterministic rule-engine mode (zero HTTP calls)
    det = enrich_finding_summary(audit_lead, None)
    assert det["used_llm"] is False
    assert det["neutrality_passed"] is True
    assert det["enriched_by"] == "rule-engine"

    # 2. Opted-in cloud LLM returning clean, neutral enriched JSON
    def _openai_ok(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
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
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(payload)}}]})

    openai_ok_cfg = ProviderInfo(
        provider="openai",
        model="gpt-4o-mini",
        label="OpenAI",
        api_key_env="OPENAI_API_KEY",
        base_url="https://api.openai.com/v1",
    )
    with httpx.Client(transport=httpx.MockTransport(_openai_ok)) as client:
        enriched = enrich_finding_summary(
            audit_lead,
            openai_ok_cfg,
            http_client=client,
            env={"OPENAI_API_KEY": "sk-test"},
            allow_paid_provider=True,
            max_usd=0.50,
        )
    assert enriched["used_llm"] is True
    assert enriched["enriched_by"] == "openai/gpt-4o-mini"
    assert enriched["neutrality_passed"] is True
    # Deterministic verified summary is NEVER overwritten; LLM text stays in separate editorial_interpretation
    assert enriched["summary"] == audit_lead["summary"]
    assert enriched["editorial_interpretation"]["authoritative"] is False
    assert "INR 38.40 lakh" in enriched["editorial_interpretation"]["text"]
    # Verified deterministic benign explanation preserved + 2 distinct LLM explanations merged
    assert len(enriched["benign_explanations"]) == 3

    # 3. Cloud LLM violating BNS Sec 356 neutrality (with explicit paid opt-in + positive cap) -> deterministic fallback
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
            audit_lead,
            openai_cfg,
            http_client=client,
            env={"OPENAI_API_KEY": "sk-test"},
            allow_paid_provider=True,
            max_usd=0.50,
        )
    assert safe_fallback["used_llm"] is False
    assert safe_fallback["fallback_reason"] is not None
    assert "neutrality_violation" in safe_fallback["fallback_reason"]
    assert safe_fallback["summary"] == audit_lead["summary"]
    assert lint_neutrality(
        safe_fallback["title"],
        safe_fallback["summary"],
        safe_fallback["benign_explanations"],
    ).passed

    # 4. Unavailable LLM (with explicit paid opt-in + positive cap) -> clean deterministic fallback
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
            audit_lead,
            gemini_cfg,
            http_client=client,
            env={"GEMINI_API_KEY": "test-key"},
            allow_paid_provider=True,
            max_usd=0.50,
        )
    assert down_fallback["used_llm"] is False
    assert down_fallback["fallback_reason"] == "llm_unavailable"


# ---------------------------------------------------------------------------
# 3. Standalone Offline `aiwatchdogs audit-file` CLI Tests (M1, M2, M5 + --rti)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("builder", "filename", "methodology", "expected_portal"),
    [
        (_write_m1_ocds_fixture, "assam_ocds_fixture.json", "M1", "https://rtionline.assam.gov.in"),
        (_write_m2_split_csv_fixture, "central_gfr_split_fixture.csv", "M2", "https://rtionline.gov.in"),
        (_write_m5_march_rush_csv_fixture, "ap_treasury_march_rush.csv", "M5", "https://rtionline.ap.gov.in"),
    ],
)
def test_audit_file_inline_datasets_json_and_rti(
    tmp_path: Path,
    builder: Any,
    filename: str,
    methodology: str,
    expected_portal: str,
) -> None:
    dataset_path = builder(tmp_path / filename)

    res = cli_runner.invoke(
        cli_app,
        ["audit-file", str(dataset_path), "--methodology", methodology, "--json", "--rti"],
    )
    assert res.exit_code == 0, res.output
    data = json.loads(res.stdout)

    assert data["methodology_code"] == methodology
    assert data["is_demo_sample"] is False
    assert data["records_scanned"] > 0
    assert len(data["sha256"]) == 64
    assert len(data["leads"]) >= 1
    assert all(lead["legal_lint_passed"] is True for lead in data["leads"])
    assert all(len(lead["benign_explanations"]) >= 1 for lead in data["leads"])

    assert len(data["rti_drafts"]) == len(data["leads"])
    for draft in data["rti_drafts"]:
        assert 20 <= draft["word_count"] <= 500
        assert draft["rti_portal_url"] == expected_portal
        assert draft["human_filing_required"] is True
        assert "Section 6(1)" in draft["statutory_sections"]


def test_audit_file_rich_output_and_cleared_file(tmp_path: Path) -> None:
    m2_csv = _write_m2_split_csv_fixture(tmp_path / "m2_split.csv")

    # Rich formatted report with RTI panel — zero demo/sample badge!
    res = cli_runner.invoke(
        cli_app,
        ["audit-file", str(m2_csv), "--methodology", "M2", "--rti"],
    )
    assert res.exit_code == 0, res.output
    assert "SAMPLE DEMO" not in res.stdout
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
    assert clean_data["is_demo_sample"] is False

    # Invalid methodology code
    res_bad = cli_runner.invoke(
        cli_app,
        ["audit-file", str(clean_csv), "--methodology", "M99"],
    )
    assert res_bad.exit_code == 2


# ---------------------------------------------------------------------------
# 4. Remote dataset_url + secondary_url + expected_sha256 + Multi-Lead Peer Verify
# ---------------------------------------------------------------------------


def test_runner_remote_dataset_url_secondary_url_and_expected_sha256(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    m2_csv = _write_m2_split_csv_fixture(tmp_path / "remote_m2.csv")
    raw_bytes = m2_csv.read_bytes()
    expected_sha = compute_sha256(raw_bytes)
    expected_sec_sha = compute_sha256(b"secondary-csr-bytes")
    loader_calls: list[dict[str, Any]] = []

    from aiwatchdogs.connectors.loader import load_dataset as real_load_dataset

    def _stub_load_dataset(
        path_or_url: str,
        fmt: str | None = None,
        context: dict[str, Any] | None = None,
        allow_local_files: bool = False,
        **kwargs: Any,
    ) -> LoadedDataset:
        loader_calls.append(
            {
                "path_or_url": path_or_url,
                "fmt": fmt,
                "context": context,
                "allow_local_files": allow_local_files,
                "kwargs": kwargs,
            }
        )
        loaded = real_load_dataset(str(m2_csv), fmt=fmt, allow_local_files=True)
        return loaded.model_copy(
            update={
                "source_ref": path_or_url,
                "retrieval_url": path_or_url,
                "secondary_sha256": expected_sec_sha,
                "secondary_digest_verified": True,
                "is_demo_sample": False,
            }
        )

    monkeypatch.setattr("aiwatchdogs.cli.runner.load_dataset", _stub_load_dataset)

    beats: list[dict[str, Any]] = []
    submitted: list[dict[str, Any]] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/desk/beat":
            beats.append(json.loads(request.content))
            return httpx.Response(200, json={"status": "active"})
        if request.url.path == "/api/desk/submit":
            submitted.append(json.loads(request.content))
            return httpx.Response(200, json={"status": "accepted", "finding_id": "fnd-remote-1"})
        return httpx.Response(404)

    cfg = AgentConfig(handle="remote-url-node", hub_url="http://mockhub", api_token="awd_tok")
    with httpx.Client(transport=httpx.MockTransport(_handler), base_url="http://mockhub") as client:
        runner = AgentRunner(cfg, http_client=client)
        result = runner.execute_task(
            {
                "lease_id": "lease-remote-1",
                "task": {
                    "id": "tsk-remote-1",
                    "task_type": "anomaly_scan",
                    "mission_slug": "assam-gfr-split-tendering",
                    "methodology_code": "M2",
                    "title": "Central GFR Rule 161 Split-Tendering Scan",
                    "payload": {
                        "dataset_url": "https://raw.githubusercontent.com/CivicDataLab/assam-tenders-data/1e12e516748130c15398b2501fb092bfa7413941/v2.csv",
                        "secondary_url": "https://raw.githubusercontent.com/spfrantz/MCA-CSR-Scraper/dc4b0044265d5628adef739bf7cea5b091acb856/examples/csr_top_100.csv",
                        "source_url": "https://eprocure.gov.in/",
                        "expected_sha256": expected_sha,
                        "secondary_expected_sha256": expected_sec_sha,
                        "artifact_title": "Assam OCDS FY16-22",
                        "jurisdiction": "Union of India",
                        "fiscal_year": "FY2024-25",
                        "is_demo_sample": False,
                    },
                },
            }
        )

    assert result["status"] == "completed"
    assert result["outcome"] == "finding"
    assert len(loader_calls) == 1
    assert loader_calls[0]["path_or_url"].startswith("https://raw.githubusercontent.com/")
    assert loader_calls[0]["context"]["secondary_url"].endswith("csr_top_100.csv")
    assert loader_calls[0]["allow_local_files"] is False

    # Verify factual real-time whisper contains dataset title, count, and matched hash
    assert any(
        "Downloaded Assam OCDS FY16-22" in (b.get("whisper") or "")
        and f"sha256:{expected_sha[:8]} matched" in (b.get("whisper") or "")
        for b in beats
    )

    body = submitted[0]
    finding = body["finding"]
    assert finding["is_demo_sample"] is False
    assert finding["artifact_sha256"] == expected_sha
    assert finding["secondary_sha256"] == expected_sec_sha
    assert finding["canonical_url"].startswith("https://raw.githubusercontent.com/")
    assert finding["source_url"] == "https://eprocure.gov.in/"
    assert finding["secondary_url"].endswith("csr_top_100.csv")
    assert body["metrics"]["expected_sha256_matched"] is True
    assert body["metrics"]["secondary_sha256_matched"] is True
    assert body["metrics"]["records_scanned"] == 4
    assert body["metrics"]["monetary_audited_inr"] > 0


def test_runner_peer_verify_multi_lead_matching(tmp_path: Path) -> None:
    """Ensure _handle_peer_verify matches the exact target finding among multiple engine leads."""
    multi_csv = _write_m2_split_csv_fixture(tmp_path / "multi_buyer_m2.csv", multi_buyer=True)
    sha = compute_sha256(multi_csv.read_bytes())

    from aiwatchdogs.connectors.loader import load_dataset as real_load_dataset
    from aiwatchdogs.engines.registry import run_engine

    loaded = real_load_dataset(str(multi_csv), allow_local_files=True)
    eng = run_engine("M2", loaded.records)
    assert len(eng.leads) >= 2

    # Explicitly target the second (smaller exposure) lead to verify strict entity+period+ruleset+metric matching!
    target = min(eng.leads, key=lambda ld: float(ld.monetary_exposure_inr))

    submitted: list[dict[str, Any]] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/desk/beat":
            return httpx.Response(200, json={"status": "active"})
        if request.url.path == "/api/desk/submit":
            submitted.append(json.loads(request.content))
            return httpx.Response(200, json={"status": "accepted"})
        return httpx.Response(404)

    cfg = AgentConfig(handle="multi-lead-verifier", hub_url="http://mockhub", api_token="awd_tok")
    with httpx.Client(transport=httpx.MockTransport(_handler), base_url="http://mockhub") as client:
        runner = AgentRunner(cfg, http_client=client, allow_local_files=True)
        res = runner.execute_task(
            {
                "lease_id": "lease-pv-multi",
                "task": {
                    "id": "tsk-pv-multi",
                    "task_type": "peer_verify",
                    "methodology_code": "M2",
                    "payload": {
                        "finding_id": "fnd-target-beta",
                        "canonical_url": str(multi_csv),
                        "artifact_sha256": sha,
                        "methodology_code": "M2",
                        "title": target.title,
                        "summary": target.summary,
                        "entity_name": target.entity_name,
                        "fiscal_year": target.fiscal_year,
                        "fiscal_year_start": target.fiscal_year_start,
                        "fiscal_year_end": target.fiscal_year_end,
                        "source_locator": target.source_locator,
                        "engine_version": eng.engine_version,
                        "ruleset_version": eng.ruleset_version,
                        "monetary_exposure_inr": float(target.monetary_exposure_inr),
                        "anomaly_metrics": target.anomaly_metrics,
                        "rule_triggered": target.rule_triggered,
                        "benign_explanations": target.benign_explanations,
                    },
                },
            }
        )

    assert res["verification"]["verdict"] == "confirm"
    assert res["verification"]["checks"] == {
        "bytes_digest": "pass",
        "deterministic_reproduction": "pass",
        "metric_tolerance": "pass",
        "neutrality_privacy": "pass",
    }
    assert res["verification"]["reproduced_metrics"]["entity_name"] == target.entity_name
    assert len(submitted) == 1


# ---------------------------------------------------------------------------
# 5. Full End-to-End AgentRunner Flow Against Live FastAPI Hub (ASGI)
# ---------------------------------------------------------------------------


def _point_mission_task_to_local_fixture(
    hub_app: Any,
    mission_slug: str,
    fixture_path: Path,
) -> None:
    """Re-point a seeded mission's queued task to a local temp fixture for offline E2E tests."""
    sha = compute_sha256(fixture_path.read_bytes())
    db = hub_app.state.db
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT id, payload_json FROM tasks WHERE mission_slug = ? AND status = 'queued' ORDER BY id ASC LIMIT 1",
            (mission_slug,),
        ).fetchone()
        assert row is not None, f"Expected queued task for mission {mission_slug}"
        payload = json.loads(row["payload_json"])
        payload["retrieval_url"] = str(fixture_path)
        payload["dataset_url"] = str(fixture_path)
        payload["dataset_path"] = str(fixture_path)
        payload["expected_sha256"] = sha
        conn.execute(
            "UPDATE tasks SET payload_json = ? WHERE id = ?",
            (json.dumps(payload), row["id"]),
        )


def test_runner_full_claim_audit_submit_and_2of2_peer_verification_quorum(tmp_path: Path) -> None:
    hub_app = create_app(db_path=tmp_path / "live_hub.db", seed=True)
    m2_fixture = _write_m2_split_csv_fixture(tmp_path / "assam_gfr_m2.csv")
    _point_mission_task_to_local_fixture(hub_app, "assam-gfr-split-tendering", m2_fixture)

    with TestClient(hub_app, base_url="http://testserver") as asgi_client:
        # Confirm Hub starts 100% honest: 0 fake agents, 0 fake findings, 8 live missions, 16 queued tasks!
        head0 = asgi_client.get("/api/head").json()
        assert head0["stats"]["agents_total"] == 0
        assert head0["stats"]["findings_verified"] == 0
        assert head0["stats"]["tasks_queued"] == 16
        assert all(m["data_mode"] == "live_public" for m in head0["missions"])

        # Node 1: Investigator claims M2 scan task, audits inline CSV, submits neutral finding
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
            allow_local_files=True,
        )
        summary_inv = runner_inv.run_loop(
            max_tasks=1,
            preferred_mission="assam-gfr-split-tendering",
        )

        assert stat.S_IMODE(inv_cfg_path.stat().st_mode) == 0o600
        assert summary_inv.tasks_completed == 1
        assert summary_inv.findings_submitted == 1
        assert summary_inv.errors == 0

        submit_resp = summary_inv.results[0]["response"]
        finding_id = submit_resp["finding_id"]
        assert submit_resp["finding_status"] == "peer_review"
        assert submit_resp["peer_verify_tasks_created"] == 3

        # Verify finding is stored in peer_review with is_demo_sample=False before independent checks
        f_before = asgi_client.get(f"/api/findings/{finding_id}").json()
        assert f_before["status"] == "peer_review"
        assert f_before["confirm_count"] == 0
        assert f_before["required_quorum"] == 3
        assert f_before["is_demo_sample"] is False

        # Node 2, Node 3 & Node 4: Three independent peer verifiers re-load CSV, check SHA-256,
        # reproduce deterministic M2 math within 1% tolerance, check neutrality & zero PII!
        for idx, handle in enumerate(("cli-verifier-two", "cli-verifier-three", "cli-verifier-four"), start=1):
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
                allow_local_files=True,
            )
            summary_ver = runner_ver.run_loop(
                max_tasks=1,
                preferred_mission="assam-gfr-split-tendering",
            )
            assert summary_ver.tasks_completed == 1
            assert summary_ver.verifications_submitted == 1
            verif_payload = summary_ver.results[0]["verification"]
            assert verif_payload["finding_id"] == finding_id
            assert verif_payload["verdict"] == "confirm"
            assert verif_payload["checks"] == {
                "bytes_digest": "pass",
                "deterministic_reproduction": "pass",
                "metric_tolerance": "pass",
                "neutrality_privacy": "pass",
            }

        # Confirm Hub promoted the finding to VERIFIED and auto-generated <=500-word RTI draft!
        f_after = asgi_client.get(f"/api/findings/{finding_id}").json()
        assert f_after["status"] == "verified"
        assert f_after["confirm_count"] == 3
        assert f_after["verification_score"] == 1.0
        assert len(f_after["verifications"]) == 3
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
        data = head.json()
        assert data["stats"]["tasks_queued"] == 16
        assert data["stats"]["agents_total"] == 0
        assert len(data["missions"]) == 8


# ---------------------------------------------------------------------------
# 6. Edge Cases: Cleared Outcome, 410 Gone, Exception Release, Peer Verdicts
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
                "mission_slug": "assam-gfr-split-tendering",
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
        summary_a = AgentRunner(cfg, http_client=client, allow_local_files=True).run_loop(max_tasks=2)
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
                "mission_slug": "assam-gfr-split-tendering",
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
        summary_b = AgentRunner(cfg, http_client=client, allow_local_files=True).run_loop(max_tasks=1)
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
                "mission_slug": "assam-gfr-split-tendering",
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
        summary_c = AgentRunner(cfg, http_client=client, allow_local_files=True).run_loop(max_tasks=1)
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

    m2_fixture = _write_m2_split_csv_fixture(tmp_path / "real_m2_fixture.csv")
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
        runner = AgentRunner(cfg, http_client=client, allow_local_files=True)

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
        assert res_pii["verification"]["checks"]["neutrality_privacy"] == "fail"

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
        assert res_fp["verification"]["checks"]["deterministic_reproduction"] == "fail"

        # 3. Real anomaly dataset with wrong SHA-256 -> fail-closed dispute (bytes_digest="fail")
        res_sha = runner.execute_task(
            {
                "lease_id": "l-sha",
                "task": {
                    "id": "t-sha",
                    "task_type": "peer_verify",
                    "methodology_code": "M2",
                    "payload": {
                        "finding_id": "f-bad-sha",
                        "dataset_path": str(m2_fixture),
                        "artifact_sha256": "0" * 64,
                        "methodology_code": "M2",
                        "title": "Public Record Anomaly: Threshold cluster",
                        "summary": "Summary of threshold cluster.",
                        "entity_name": "Central Medical Procurement Cell",
                        "monetary_exposure_inr": 100.0,
                        "rule_triggered": "GFR-2017-R161",
                        "benign_explanations": ["Lawful urgent indents."],
                    },
                },
            }
        )
        assert res_sha["verification"]["verdict"] == "dispute"
        assert res_sha["verification"]["checks"]["bytes_digest"] == "fail"
        assert res_sha["verification"]["checks"]["deterministic_reproduction"] == "unrecorded"

    assert len(submitted) == 3


# ---------------------------------------------------------------------------
# 7. Typer CLI `join --yes`, `run`, `status`, and `missions` Against Live Hub
# ---------------------------------------------------------------------------


def test_cli_join_status_missions_against_live_hub(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg_file = tmp_path / "cli_join_config.json"
    monkeypatch.setenv(ENV_CONFIG_PATH, str(cfg_file))

    hub_app = create_app(db_path=tmp_path / "cli_hub.db", seed=True)
    m1_fixture = _write_m1_ocds_fixture(tmp_path / "assam_m1.json")
    m2_fixture = _write_m2_split_csv_fixture(tmp_path / "assam_m2.csv")
    _point_mission_task_to_local_fixture(hub_app, "assam-ocds-single-bid", m1_fixture)
    _point_mission_task_to_local_fixture(hub_app, "assam-gfr-split-tendering", m2_fixture)

    with TestClient(hub_app, base_url="https://testserver") as asgi_client:
        # Route CLI httpx calls & AgentRunner to the in-process ASGI TestClient
        orig_init = AgentRunner.__init__

        def _patched_init(self: AgentRunner, config: AgentConfig, **kwargs: Any) -> None:
            kwargs.setdefault("http_client", asgi_client)
            # Local temp fixture override for offline E2E CLI test (unattended local-file block tested separately)
            kwargs["allow_local_files"] = True
            orig_init(self, config, **kwargs)

        def _patched_get_json(url: str, **_kwargs: Any) -> Any:
            path = url.replace("https://testserver", "").replace("http://testserver", "")
            resp = asgi_client.get(path)
            resp.raise_for_status()
            return resp.json()

        monkeypatch.setattr(AgentRunner, "__init__", _patched_init)
        monkeypatch.setattr("aiwatchdogs.cli.main._bounded_http_get_json", _patched_get_json)

        # 1. `aiwatchdogs join --yes`
        res_join = cli_runner.invoke(
            cli_app,
            [
                "join",
                "--hub",
                "https://testserver",
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
        assert "https://testserver/a/cli-join-node" in res_join.stdout
        assert "SAMPLE" not in res_join.stdout.upper()
        assert cfg_file.is_file()
        assert stat.S_IMODE(cfg_file.stat().st_mode) == 0o600

        saved = load_config(cfg_file)
        assert saved is not None
        assert saved.handle == "cli-join-node"
        assert saved.api_token.startswith("awd_")

        # 2. `aiwatchdogs run` using saved config (headless)
        res_run = cli_runner.invoke(
            cli_app,
            ["run", "--mode", "investigate", "--mission", "assam-gfr-split-tendering", "--max-tasks", "1"],
        )
        assert res_run.exit_code == 0, res_run.output
        assert "Agent Run Summary" in res_run.stdout
        assert "cli-join-node" in res_run.stdout

        # 3. `aiwatchdogs status --hub https://testserver`
        res_status = cli_runner.invoke(cli_app, ["status", "--hub", "https://testserver"])
        assert res_status.exit_code == 0, res_status.output
        assert "Public Spend Audited" in res_status.stdout
        assert "Peer-Verified Leads" in res_status.stdout

        # 4. `aiwatchdogs missions --hub https://testserver` (wide COLUMNS so Rich keeps full slugs)
        res_missions = cli_runner.invoke(
            cli_app,
            ["missions", "--hub", "https://testserver"],
            env={"COLUMNS": "140"},
        )
        assert res_missions.exit_code == 0, res_missions.output
        assert "assam-ocds-single-bid" in res_missions.stdout
        assert "assam-gfr-split-tendering" in res_missions.stdout
        assert "live_public" in res_missions.stdout
        assert "demo_sample" not in res_missions.stdout
