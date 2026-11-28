"""Comprehensive security regression suite covering all verified audit findings (SEC-01 .. SEC-34)."""

from __future__ import annotations

import io
import json
import os
import socket
import stat
import subprocess
import time
import zipfile
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from aiwatchdogs.cli.config import (
    ENV_CONFIG_PATH,
    AgentConfig,
    load_config,
    record_daily_spend,
    save_config,
    validate_hub_url,
)
from aiwatchdogs.cli.main import _default_handle, _safe_rich, app as cli_app
from aiwatchdogs.cli.providers import (
    BudgetTracker,
    ProviderInfo,
    detect_providers,
    enrich_finding_summary,
)
from aiwatchdogs.cli.runner import AgentRunner, _build_task_context
from aiwatchdogs.connectors.loader import (
    _check_xml_safe,
    _fetch_remote_bytes,
    _parse_html_bytes,
    _validate_public_https_url,
    load_dataset,
)
from aiwatchdogs.core.rti import generate_rti_draft
from aiwatchdogs.core.safety import (
    escape_like,
    lint_neutrality,
    sanitize_csv_cell,
    scrub_pii,
    scrub_pii_in_obj,
)
from aiwatchdogs.engines.fiscal import run_m5_march_rush
from aiwatchdogs.engines.registry import run_engine
from aiwatchdogs.hub.app import create_app, create_degraded_app
from aiwatchdogs.hub.database import (
    Database,
    dumps_json,
    enforce_pg_sslmode,
    redact_database_url,
)
from aiwatchdogs.hub.desk import MAX_ADJUDICATION_ROUNDS, MAX_CONCURRENT_LEASES_PER_AGENT
from aiwatchdogs.hub.sse import SSEBroker
from aiwatchdogs.hub.templates import normalize_base_url, render_join_sh, render_join_txt
from aiwatchdogs.projects import validate_project

cli_runner = CliRunner()


def _register(client: TestClient, handle: str) -> dict[str, Any]:
    r = client.post(
        "/api/agents/register",
        json={"handle": handle, "provider": "rule-engine", "model": "deterministic-python-v1"},
    )
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------
# SEC-01 & SEC-10: Host/Forwarded Header Injection, /join.sh & /join.txt
# ---------------------------------------------------------------------------


def test_sec01_join_sh_and_txt_reject_header_injection_and_set_no_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AIWATCHDOGS_PUBLIC_URL", raising=False)
    monkeypatch.setenv("VERCEL", "1")
    app = create_app(db_path=tmp_path / "sec01.db", seed=True)
    with TestClient(app) as client:
        malicious_headers = {
            "Host": 'evil.example/$(id)";rm -rf /;#',
            "X-Forwarded-Host": "attacker.evil:443/$(touch /tmp/pwn)",
            "X-Forwarded-Proto": "javascript",
        }
        sh_resp = client.get("/join.sh", headers=malicious_headers)
        assert sh_resp.status_code == 200
        assert sh_resp.headers.get("cache-control") == "no-store"
        assert "evil.example" not in sh_resp.text
        assert "attacker.evil" not in sh_resp.text
        assert "$(id)" not in sh_resp.text
        assert "touch /tmp/pwn" not in sh_resp.text
        assert "DEFAULT_HUB_URL='https://aiwatchdogs.in'" in sh_resp.text
        assert "AIWATCHDOGS_USE_LOCAL" in sh_resp.text
        assert "python3 -I" in sh_resp.text

        # Validate POSIX sh syntax cleanly
        proc = subprocess.run(["sh", "-n"], input=sh_resp.text, text=True, capture_output=True, check=False)
        assert proc.returncode == 0, proc.stderr

        txt_resp = client.get("/join.txt", headers=malicious_headers)
        assert txt_resp.status_code == 200
        assert txt_resp.headers.get("cache-control") == "no-store"
        assert "evil.example" not in txt_resp.text
        assert "UNTRUSTED DATA" in txt_resp.text
        assert "https://aiwatchdogs.in" in txt_resp.text

    # Direct template sanitizer checks
    assert normalize_base_url("https://evil.com/$(id)") == "https://aiwatchdogs.in"
    assert normalize_base_url("javascript:alert(1)") == "https://aiwatchdogs.in"
    assert normalize_base_url("https://user:pass@aiwatchdogs.in") == "https://aiwatchdogs.in"
    assert normalize_base_url("https://aiwatchdogs.in\r\necho pwned") == "https://aiwatchdogs.in"
    rendered_sh = render_join_sh("https://evil.example/$(whoami)")
    assert "whoami" not in rendered_sh
    assert "demo" not in render_join_txt("https://aiwatchdogs.in").lower()


# ---------------------------------------------------------------------------
# SEC-02: SSRF Prevention (Hub task_payload Authority, DNS Check, Redirects)
# ---------------------------------------------------------------------------


def test_sec02_hub_task_payload_authority_blocks_url_and_digest_spoofing(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "sec02.db", seed=True)
    with TestClient(app) as client:
        author = _register(client, "sec02-author")
        claim = client.post(
            "/api/desk/next",
            headers={"X-Agent-Token": author["api_token"]},
            json={"handle": author["handle"], "mode": "investigate", "preferred_mission": "assam-ocds-single-bid"},
        )
        assert claim.status_code == 200
        lease_id = claim.json()["lease_id"]
        task_payload = claim.json()["task"]["payload"]
        real_url = task_payload["dataset_url"]
        real_sha = task_payload["expected_sha256"]

        # 1. Conflicting digest -> HTTP 422
        bad_sha = client.post(
            "/api/desk/submit",
            headers={"X-Agent-Token": author["api_token"]},
            json={
                "lease_id": lease_id,
                "outcome": "finding",
                "finding": {
                    "title": "Public Record Anomaly: Digest Spoof Attempt",
                    "summary": "Deterministic observation.",
                    "methodology_code": "M1",
                    "entity_name": "Assam Health",
                    "artifact_sha256": "f" * 64,
                    "benign_explanations": ["Lawful OEM exception under GFR."],
                },
            },
        )
        assert bad_sha.status_code == 422

        # 2. Private IP retrieval_url injection -> HTTP 422
        bad_ssrf = client.post(
            "/api/desk/submit",
            headers={"X-Agent-Token": author["api_token"]},
            json={
                "lease_id": lease_id,
                "outcome": "finding",
                "finding": {
                    "title": "Public Record Anomaly: SSRF Attempt",
                    "summary": "Deterministic observation.",
                    "methodology_code": "M1",
                    "entity_name": "Assam Health",
                    "retrieval_url": "https://169.254.169.254/latest/meta-data/",
                    "artifact_sha256": real_sha,
                    "benign_explanations": ["Lawful OEM exception under GFR."],
                },
            },
        )
        assert bad_ssrf.status_code == 422

        # 3. Attempt to inject arbitrary public URL + spoof agent_trace.node_handle -> Hub overrides with server values!
        ok_sub = client.post(
            "/api/desk/submit",
            headers={"X-Agent-Token": author["api_token"]},
            json={
                "lease_id": lease_id,
                "outcome": "finding",
                "finding": {
                    "title": "Public Record Anomaly: Trace & URL Authority Check",
                    "summary": "Deterministic observation of single-bid concentration.",
                    "methodology_code": "M1",
                    "entity_name": "Assam Health",
                    "retrieval_url": "https://attacker-controlled.example/fake.csv",
                    "secondary_url": "https://attacker-controlled.example/sec.csv",
                    "artifact_sha256": real_sha,
                    "agent_trace": {"node_handle": "spoofed-victim", "provider": "spoofed"},
                    "benign_explanations": ["Lawful OEM exception under GFR."],
                },
            },
        )
        assert ok_sub.status_code == 200, ok_sub.text
        fid = ok_sub.json()["finding_id"]
        detail = client.get(f"/api/findings/{fid}").json()
        assert detail["artifact"]["retrieval_url"] == real_url
        assert detail["agent_trace"]["node_handle"] == "sec02-author"
        assert detail["agent_trace"]["provider"] == "rule-engine"

        # Auto-created peer_verify task MUST keep server dataset_url and NOT attacker's secondary_url!
        peer = _register(client, "sec02-peer")
        pv_claim = client.post(
            "/api/desk/next",
            headers={"X-Agent-Token": peer["api_token"]},
            json={"handle": peer["handle"], "mode": "verify"},
        )
        assert pv_claim.status_code == 200
        pv_payload = pv_claim.json()["task"]["payload"]
        assert pv_payload["dataset_url"] == real_url
        assert pv_payload.get("secondary_url") != "https://attacker-controlled.example/sec.csv"


def test_sec02_loader_dns_ssrf_and_redirect_hop_blocking(monkeypatch: pytest.MonkeyPatch) -> None:
    # Private IP literals, CGNAT, IPv4-mapped IPv6, non-canonical IPv4
    for bad in (
        "https://127.0.0.1/data.csv",
        "https://169.254.169.254/latest/meta-data/",
        "https://100.64.0.1/internal.csv",
        "https://[::ffff:127.0.0.1]/secret",
        "https://2130706433/decimal-loopback",
        "https://0x7f000001/hex-loopback",
        "http://data.gov.in/plaintext.csv",
    ):
        with pytest.raises(PermissionError):
            _validate_public_https_url(bad)

    # DNS resolving to private RFC1918 / loopback IP must be rejected before connect
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443))],
    )
    with pytest.raises(PermissionError, match="non-public|private|loopback|disallowed"):
        _validate_public_https_url("https://rebind.attacker.example/data.csv", resolve_dns=True)

    # Hop-by-hop redirect validation must block redirect to internal IP BEFORE issuing request to internal IP
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *a, **kw: (
            [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
            if host == "public.example"
            else [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
        ),
    )
    contacted_hosts: list[str] = []

    class _FakeStreamResp:
        def __init__(self, status_code: int, headers: dict[str, str]) -> None:
            self.status_code = status_code
            self.headers = headers
            self.url = httpx.URL("https://public.example/start.csv")

        def __enter__(self) -> _FakeStreamResp:
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def iter_bytes(self, chunk_size: int = 65536):
            yield b""

    def _fake_stream(self: Any, method: str, url: str, **kwargs: Any) -> _FakeStreamResp:
        contacted_hosts.append(httpx.URL(url).host or "")
        return _FakeStreamResp(302, {"location": "https://internal.local/secret"})

    monkeypatch.setattr(httpx.Client, "stream", _fake_stream)
    with pytest.raises(PermissionError):
        _fetch_remote_bytes("https://public.example/start.csv", use_cache=False)
    assert contacted_hosts == ["public.example"]  # NEVER contacted internal.local!


# ---------------------------------------------------------------------------
# SEC-03: Non-Finite Float Injection (NaN/Infinity/Overflow)
# ---------------------------------------------------------------------------


def test_sec03_non_finite_floats_rejected_and_public_apis_stay_healthy(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        dumps_json({"x": float("nan")})
    with pytest.raises(ValueError):
        dumps_json({"x": float("inf")})

    app = create_app(db_path=tmp_path / "sec03.db", seed=True)
    with TestClient(app) as client:
        for raw_body in (
            '{"name":"Test","amount_inr":NaN}',
            '{"name":"Test","amount_inr":Infinity}',
            '{"name":"Test","amount_inr":-10}',
            '{"name":"Test","amount_inr":1e15}',
            '{"name":"Test","in_kind_value_inr":Infinity}',
        ):
            r = client.post(
                "/api/sponsors/pledge",
                content=raw_body,
                headers={"Content-Type": "application/json"},
            )
            assert r.status_code == 422, f"Expected 422 for {raw_body}, got {r.status_code}: {r.text}"

        # Public APIs must still serialize cleanly with HTTP 200
        for path in ("/api/head", "/api/sponsors", "/api/ledger", "/api/missions", "/api/findings"):
            assert client.get(path).status_code == 200


# ---------------------------------------------------------------------------
# SEC-04 & SEC-05: Rate Limiting, Quorum Strictness, Anti-Sabotage
# ---------------------------------------------------------------------------


def test_sec04_rate_limiting_and_verification_integrity(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "sec04.db", seed=True)
    with TestClient(app) as client:
        # 1. Pledge rate limit (10/min): 11th request returns HTTP 429 with Retry-After
        for i in range(10):
            r = client.post(
                "/api/sponsors/pledge",
                json={"name": f"Civic Backer {i}", "amount_inr": 1000.0},
            )
            assert r.status_code == 201, r.text
        r_429 = client.post(
            "/api/sponsors/pledge",
            json={"name": "Civic Backer Overflow", "amount_inr": 1000.0},
        )
        assert r_429.status_code == 429
        assert "retry-after" in r_429.headers

        # 2. Verification confirm without matching artifact_sha256 -> HTTP 422
        author = _register(client, "sec04-author")
        peer = _register(client, "sec04-peer")
        claim = client.post(
            "/api/desk/next",
            headers={"X-Agent-Token": author["api_token"]},
            json={"handle": author["handle"], "mode": "investigate", "preferred_mission": "assam-ocds-single-bid"},
        )
        real_sha = claim.json()["task"]["payload"]["expected_sha256"]
        sub = client.post(
            "/api/desk/submit",
            headers={"X-Agent-Token": author["api_token"]},
            json={
                "lease_id": claim.json()["lease_id"],
                "outcome": "finding",
                "finding": {
                    "title": "Public Record Anomaly: Quorum Digest Check",
                    "summary": "Deterministic summary.",
                    "methodology_code": "M1",
                    "entity_name": "Assam Health",
                    "artifact_sha256": real_sha,
                    "anomaly_metrics": {"numerator": 50, "denominator": 100},
                    "benign_explanations": ["Specialized procurement exception."],
                },
            },
        )
        assert sub.status_code == 200
        fid = sub.json()["finding_id"]

        pv_claim = client.post(
            "/api/desk/next",
            headers={"X-Agent-Token": peer["api_token"]},
            json={"handle": peer["handle"], "mode": "verify"},
        )
        pv_lease = pv_claim.json()["lease_id"]

        # Missing artifact_sha256 on confirm -> 422
        r_no_sha = client.post(
            "/api/desk/submit",
            headers={"X-Agent-Token": peer["api_token"]},
            json={
                "lease_id": pv_lease,
                "outcome": "verification",
                "verification": {
                    "finding_id": fid,
                    "verdict": "confirm",
                    "checks": {
                        "bytes_digest": "pass",
                        "deterministic_reproduction": "pass",
                        "metric_tolerance": "pass",
                        "neutrality_privacy": "pass",
                    },
                    "reproduced_metrics": {"numerator": 50, "denominator": 100},
                },
            },
        )
        assert r_no_sha.status_code == 422

        # Diverged numeric reproduced_metrics (>5%) on confirm -> 422
        r_bad_metrics = client.post(
            "/api/desk/submit",
            headers={"X-Agent-Token": peer["api_token"]},
            json={
                "lease_id": pv_lease,
                "outcome": "verification",
                "verification": {
                    "finding_id": fid,
                    "verdict": "confirm",
                    "checks": {
                        "bytes_digest": "pass",
                        "deterministic_reproduction": "pass",
                        "metric_tolerance": "pass",
                        "neutrality_privacy": "pass",
                    },
                    "reproduced_metrics": {"artifact_sha256": real_sha, "numerator": 999, "denominator": 100},
                },
            },
        )
        assert r_bad_metrics.status_code == 422

        # Offset upper bound (> 50_000) -> 422
        assert client.get("/api/findings?offset=50001").status_code == 422


def test_sec05_anti_sabotage_permanent_error_concurrent_leases_and_flag_abuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aiwatchdogs.hub import desk as desk_mod

    assert MAX_CONCURRENT_LEASES_PER_AGENT == 50
    app = create_app(db_path=tmp_path / "sec05.db", seed=True)
    with TestClient(app) as client:
        saboteur = _register(client, "sec05-saboteur")

        # 1. Concurrent active lease ceiling enforced cleanly when hit (tested with cap=2 against seeded tasks)
        monkeypatch.setattr(desk_mod, "MAX_CONCURRENT_LEASES_PER_AGENT", 2)
        leases = []
        for _ in range(2):
            c = client.post(
                "/api/desk/next",
                headers={"X-Agent-Token": saboteur["api_token"]},
                json={"handle": saboteur["handle"], "mode": "investigate"},
            )
            assert c.status_code == 200
            leases.append(c.json())
        c_over = client.post(
            "/api/desk/next",
            headers={"X-Agent-Token": saboteur["api_token"]},
            json={"handle": saboteur["handle"], "mode": "investigate"},
        )
        assert c_over.status_code == 429
        monkeypatch.setattr(desk_mod, "MAX_CONCURRENT_LEASES_PER_AGENT", MAX_CONCURRENT_LEASES_PER_AGENT)

        # 2. release(reason="permanent_error") MUST NOT fail task permanently on attempt 1
        rel = client.post(
            "/api/desk/release",
            headers={"X-Agent-Token": saboteur["api_token"]},
            json={"lease_id": leases[0]["lease_id"], "reason": "permanent_error"},
        )
        assert rel.status_code == 200
        assert rel.json()["status"] == "released"
        with app.state.db.read() as conn:
            t_row = conn.execute("SELECT status FROM tasks WHERE id = ?", (leases[0]["task"]["id"],)).fetchone()
            assert t_row is not None and t_row["status"] == "queued"

        # 3. Cleared submission with negative or overflowing monetary_audited_inr -> 422
        bad_cleared = client.post(
            "/api/desk/submit",
            headers={"X-Agent-Token": saboteur["api_token"]},
            json={
                "lease_id": leases[1]["lease_id"],
                "outcome": "cleared",
                "metrics": {"monetary_audited_inr": -500.0},
            },
        )
        assert bad_cleared.status_code == 422

        # 4. flag_pii_or_legal requires justification notes (>=8 chars) and awards 0 merit points
        author = _register(client, "sec05-author")
        flagger = _register(client, "sec05-flagger")
        a_claim = client.post(
            "/api/desk/next",
            headers={"X-Agent-Token": author["api_token"]},
            json={"handle": author["handle"], "mode": "investigate"},
        )
        assert a_claim.status_code == 200, a_claim.text
        a_method = a_claim.json()["task"]["methodology_code"]
        a_sub = client.post(
            "/api/desk/submit",
            headers={"X-Agent-Token": author["api_token"]},
            json={
                "lease_id": a_claim.json()["lease_id"],
                "outcome": "finding",
                "finding": {
                    "title": "Public Record Anomaly: Flag Abuse Test",
                    "summary": "Deterministic summary for quarantine test.",
                    "methodology_code": a_method,
                    "entity_name": "Assam PWD",
                    "benign_explanations": ["Specialized road works."],
                },
            },
        )
        assert a_sub.status_code == 200, a_sub.text
        fid = a_sub.json()["finding_id"]
        f_claim = client.post(
            "/api/desk/next",
            headers={"X-Agent-Token": flagger["api_token"]},
            json={"handle": flagger["handle"], "mode": "verify"},
        )
        f_lease = f_claim.json()["lease_id"]

        # Short/empty notes rejected with 422
        r_short = client.post(
            "/api/desk/submit",
            headers={"X-Agent-Token": flagger["api_token"]},
            json={
                "lease_id": f_lease,
                "outcome": "verification",
                "verification": {"finding_id": fid, "verdict": "flag_pii_or_legal", "notes": "no"},
            },
        )
        assert r_short.status_code == 422

        # Valid justification notes quarantine the finding AND award 0 points to flagger
        r_flag = client.post(
            "/api/desk/submit",
            headers={"X-Agent-Token": flagger["api_token"]},
            json={
                "lease_id": f_lease,
                "outcome": "verification",
                "verification": {
                    "finding_id": fid,
                    "verdict": "flag_pii_or_legal",
                    "notes": "Detected residual personal contact details in citation excerpt.",
                },
            },
        )
        assert r_flag.status_code == 200
        assert r_flag.json()["quarantined"] is True
        prof = client.get(f"/api/contributors/{flagger['handle']}").json()
        assert prof["compute_score"] == 0


# ---------------------------------------------------------------------------
# SEC-06: Quarantine Boundary & Deep Nesting PII Scrubbing & Whisper Linting
# ---------------------------------------------------------------------------


def test_sec06_quarantine_redacts_tasks_whispers_events_and_deep_pii_scrubbed(tmp_path: Path) -> None:
    # 1. Deeply nested PII (>24 levels) must still be scrubbed or safely truncated
    deep: Any = "Contact PAN ABCDE1234F at secret@example.com"
    for _ in range(30):
        deep = {"nested": deep}
    scrubbed, hits = scrub_pii_in_obj(deep)
    assert "ABCDE1234F" not in json.dumps(scrubbed)
    assert "secret@example.com" not in json.dumps(scrubbed)

    # 2. Quarantine removes finding title from tasks, whispers, events, /api/head, /api/live
    app = create_app(db_path=tmp_path / "sec06.db", seed=True)
    with TestClient(app) as client:
        author = _register(client, "sec06-author")
        flagger = _register(client, "sec06-flagger")
        secret_marker = "UniqueQuarantineMarkerXYZ789"

        claim = client.post(
            "/api/desk/next",
            headers={"X-Agent-Token": author["api_token"]},
            json={"handle": author["handle"], "mode": "investigate", "preferred_mission": "assam-ocds-single-bid"},
        )
        lease_id = claim.json()["lease_id"]

        # Defamatory whisper rejected by neutrality lint
        bad_beat = client.post(
            "/api/desk/beat",
            headers={"X-Agent-Token": author["api_token"]},
            json={"lease_id": lease_id, "whisper": "This official is a corrupt scam crook!"},
        )
        assert bad_beat.status_code == 422

        sub = client.post(
            "/api/desk/submit",
            headers={"X-Agent-Token": author["api_token"]},
            json={
                "lease_id": lease_id,
                "outcome": "finding",
                "whisper": f"Submitted lead {secret_marker}",
                "finding": {
                    "title": f"Public Record Anomaly: {secret_marker}",
                    "summary": f"Deterministic summary referencing {secret_marker}.",
                    "methodology_code": "M1",
                    "entity_name": "Assam Irrigation",
                    "benign_explanations": ["Lawful procurement exception."],
                },
            },
        )
        assert sub.status_code == 200
        fid = sub.json()["finding_id"]

        # Quarantine via peer flag
        pv = client.post(
            "/api/desk/next",
            headers={"X-Agent-Token": flagger["api_token"]},
            json={"handle": flagger["handle"], "mode": "verify"},
        )
        r_flag = client.post(
            "/api/desk/submit",
            headers={"X-Agent-Token": flagger["api_token"]},
            json={
                "lease_id": pv.json()["lease_id"],
                "outcome": "verification",
                "verification": {
                    "finding_id": fid,
                    "verdict": "flag_pii_or_legal",
                    "notes": "Quarantining finding due to sensitive personal identifier.",
                },
            },
        )
        assert r_flag.status_code == 200

        # Ensure marker does NOT leak via ANY public read endpoint!
        for endpoint in ("/api/head", "/api/live", "/api/events?since_id=0", f"/api/findings/{fid}"):
            resp = client.get(endpoint)
            assert resp.status_code == 200
            assert secret_marker not in resp.text, f"Quarantined marker leaked in {endpoint}"


# ---------------------------------------------------------------------------
# SEC-07, SEC-08, SEC-12, SEC-13, SEC-14, SEC-15, SEC-27, SEC-28
# ---------------------------------------------------------------------------


def test_sec07_sse_event_sanitization_and_slot_caps(tmp_path: Path) -> None:
    frame = SSEBroker._format_sse("bad\r\nevent: injected", {"msg": "ok"}, event_id=7)
    assert "\r" not in frame
    assert "event: whisper\n" in frame
    assert "event: injected" not in frame

    broker = SSEBroker(max_subscribers=2, max_per_client=1)
    broker.acquire_client_slot("1.2.3.4")
    with pytest.raises(Exception) as exc_info:
        broker.acquire_client_slot("1.2.3.4")
    assert getattr(exc_info.value, "status_code", None) == 429
    broker.release_client_slot("1.2.3.4")


def test_sec08_sec14_sponsor_featured_url_neutrality_and_csv_formula_sanitization(tmp_path: Path) -> None:
    assert sanitize_csv_cell("=CMD|' /C calc'!A0").startswith("'")
    assert sanitize_csv_cell("+1+1").startswith("'")
    assert sanitize_csv_cell("-2+3").startswith("'")
    assert sanitize_csv_cell("@SUM(A1:A2)").startswith("'")
    assert sanitize_csv_cell("Normal Civic Text") == "Normal Civic Text"

    app = create_app(db_path=tmp_path / "sec08.db", seed=True)
    with TestClient(app) as client:
        # Dangerous URL schemes rejected
        for bad_url in ("javascript:alert(1)", "data:text/html,<h1>x</h1>", "http://127.0.0.1/x"):
            r = client.post(
                "/api/sponsors/pledge",
                json={"name": "Backer", "amount_inr": 5000, "url": bad_url},
            )
            assert r.status_code == 422

        # Defamatory sponsor message rejected
        r_defam = client.post(
            "/api/sponsors/pledge",
            json={"name": "Backer", "amount_inr": 5000, "message": "Expose this corrupt scam!"},
        )
        assert r_defam.status_code == 422

        # Large self-declared pledge MUST stay featured=False (unreconciled) and formula-like name sanitized in CSV
        r_ok = client.post(
            "/api/sponsors/pledge",
            json={
                "name": "=HYPERLINK(\"https://evil.example\")",
                "tier": "institutional",
                "amount_inr": 500_000,
                "url": "https://example.org",
            },
        )
        assert r_ok.status_code == 201
        assert r_ok.json()["sponsor"]["featured"] is False

        csv_resp = client.get("/api/ledger?format=csv")
        assert csv_resp.status_code == 200
        assert "'=HYPERLINK" in csv_resp.text


def test_sec12_sec13_sec15_sec27_sec28_cors_headers_body_limit_tokens_and_prod_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VERCEL", "1")
    app = create_app(db_path=tmp_path / "sec_prod.db", seed=True)
    with TestClient(app) as client:
        # SEC-13: Security headers on every response
        h = client.get("/health")
        assert h.status_code == 200
        assert h.headers.get("x-content-type-options") == "nosniff"
        assert h.headers.get("x-frame-options") == "DENY"
        assert "default-src 'self'" in (h.headers.get("content-security-policy") or "")
        assert "strict-transport-security" in h.headers
        assert h.headers.get("cross-origin-opener-policy") == "same-origin"
        # SEC-28: Trimmed production /health & disabled /docs
        hdata = h.json()
        assert hdata["db_ok"] is True
        assert "db_backend" not in hdata
        assert "schema_version" not in hdata
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404

        # SEC-12: Arbitrary *.vercel.app tenant NOT allowed; credentials never true
        evil_cors = client.options(
            "/api/head",
            headers={"Origin": "https://evil-attacker.vercel.app", "Access-Control-Request-Method": "GET"},
        )
        assert evil_cors.headers.get("access-control-allow-origin") is None
        assert evil_cors.headers.get("access-control-allow-credentials") is None

        good_cors = client.options(
            "/api/head",
            headers={"Origin": "https://aiwatchdogs.in", "Access-Control-Request-Method": "GET"},
        )
        assert good_cors.headers.get("access-control-allow-origin") == "https://aiwatchdogs.in"
        assert good_cors.headers.get("access-control-allow-credentials") is None

        # SEC-15: Request body > 256 KiB returns HTTP 413
        oversized = '{"name":"' + ("A" * 300_000) + '"}'
        r_413 = client.post(
            "/api/sponsors/pledge",
            content=oversized,
            headers={"Content-Type": "application/json"},
        )
        assert r_413.status_code == 413

        # SEC-27: Reserved handles blocked; token rotation & revocation work
        r_res = client.post("/api/agents/register", json={"handle": "admin"})
        assert r_res.status_code == 422

        agent = _register(client, "sec27-rotator")
        old_tok = agent["api_token"]
        rot = client.post("/api/agents/rotate-token", headers={"X-Agent-Token": old_tok})
        assert rot.status_code == 200
        new_tok = rot.json()["api_token"]
        assert new_tok != old_tok

        # Old token rejected with 401
        r_old = client.post("/api/desk/next", headers={"X-Agent-Token": old_tok}, json={"handle": agent["handle"]})
        assert r_old.status_code == 401

        # Revoke new token -> rejected with 401
        rev = client.post("/api/agents/revoke", headers={"X-Agent-Token": new_tok})
        assert rev.status_code == 200
        r_rev = client.post("/api/desk/next", headers={"X-Agent-Token": new_tok}, json={"handle": agent["handle"]})
        assert r_rev.status_code == 401

    # SEC-28: Degraded app hides raw reason and sets security headers
    degraded = create_degraded_app(reason="postgresql://user:supersecret@db.internal/prod failed")
    with TestClient(degraded) as dc:
        dh = dc.get("/health")
        assert dh.status_code == 503
        assert "supersecret" not in dh.text
        assert dh.headers.get("x-frame-options") == "DENY"


# ---------------------------------------------------------------------------
# SEC-09, SEC-16, SEC-18, SEC-20, SEC-21, SEC-22, SEC-23: Engines, Loader, Safety, DB
# ---------------------------------------------------------------------------


def test_sec09_m8_local_isolation_and_context_mass_assignment_blocked(tmp_path: Path) -> None:
    secret_file = tmp_path / "secret.csv"
    secret_file.write_text("company,amount\nSecretCo,1000\n", encoding="utf-8")

    # 1. _build_task_context strips all forbidden loader-control keys
    ctx = _build_task_context(
        {
            "context": {
                "allow_local_files": True,
                "use_cache": True,
                "secondary_path": str(secret_file),
                "max_download_bytes": 999_999_999_999,
                "max_decompressed_bytes": 999_999_999_999,
                "max_zip_members": 999_999,
                "max_parse_rows": 999_999,
                "max_pdf_pages": 999_999,
                "jurisdiction": "Assam",
            }
        }
    )
    for forbidden in (
        "allow_local_files",
        "use_cache",
        "secondary_path",
        "max_download_bytes",
        "max_decompressed_bytes",
        "max_zip_members",
        "max_parse_rows",
        "max_pdf_pages",
    ):
        assert forbidden not in ctx
    assert ctx["jurisdiction"] == "Assam"

    # 2. M8 engine via run_engine strips allow_local_files & secondary_path from untrusted context
    res = run_engine(
        "M8",
        [{"donor_name": "SecretCo", "amount_inr": 50_000_000, "purchase_date": "2023-05-01"}],
        context={"allow_local_files": True, "secondary_path": str(secret_file)},
    )
    assert res.leads == []


def test_sec16_sec18_sec21_sec22_redos_xml_dtd_pii_rti_and_m5(tmp_path: Path) -> None:
    # SEC-16: ReDoS checks complete well under 0.2s
    t0 = time.monotonic()
    scrub_pii("a" * 50_000 + "!")
    assert (time.monotonic() - t0) < 0.2

    t1 = time.monotonic()
    _parse_html_bytes(("<table " + ("a" * 50_000)).encode("utf-8"), "https://cga.nic.in/test.html")
    assert (time.monotonic() - t1) < 0.2

    # SEC-18: Expat rejects DTD in UTF-8, UTF-16LE with BOM, and comment-padded payloads
    for xml_payload in (
        b'<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY xxe "bar">]><root>&xxe;</root>',
        '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE foo><root/>'.encode("utf-16"),
        b'<?xml version="1.0"?><!-- ' + (b"x" * 2048) + b' --><!DOCTYPE foo><root/>',
    ):
        with pytest.raises(ValueError, match="DTD|ENTITY"):
            _check_xml_safe(xml_payload, "test.xml")

    # SEC-21: Case-insensitive PAN/IFSC, 91-phone, NFKC neutrality bypass
    cleaned, hits = scrub_pii("pan abcde1234f ifsc sbin0001234")
    assert "abcde1234f" not in cleaned.lower()
    assert "sbin0001234" not in cleaned.lower()
    assert {"pan", "ifsc"}.issubset(set(hits))

    phone_cleaned, phone_hits = scrub_pii("Reach officer at 91-9876543210 today")
    assert "9876543210" not in phone_cleaned
    assert "phone" in phone_hits

    obfuscated_lint = lint_neutrality("Title", "Possible c\u200borruption in tender", ["Lawful reason."])
    assert obfuscated_lint.passed is False

    # RTI metadata scrubbing & statutory footer preservation
    draft = generate_rti_draft(
        {
            "title": "Public Record Anomaly: Test",
            "summary": "word " * 480,
            "methodology_code": "M1",
            "entity_name": "Dept\nInjected Header abcde1234f",
            "jurisdiction": "Union of India",
            "fiscal_year": "FY2024-25",
            "benign_explanations": ["Lawful reason."],
        },
        public_authority="Ministry\r\nEvil pan ABCDE1234F",
    )
    assert "\n" not in draft["public_authority"]
    assert "ABCDE1234F" not in draft["public_authority"]
    assert draft["word_count"] <= 500
    assert "Section 6(1)" in draft["statutory_sections"]

    # SEC-22: M5 partial pacing (None q4_pct/march_pct) never crashes with TypeError
    m5 = run_engine(
        "M5",
        [
            {"district": "Test", "year": "2024", "month": "MAR", "major_head": "2202", "amount": 1_000_000},
        ],
    )
    assert m5.records_scanned == 1


def test_sec20_sec23_projects_validator_escape_like_and_pg_sslmode(tmp_path: Path) -> None:
    # SEC-20: Project validator blocks javascript enlisted_pr_url, unpinned GitHub raw, private IPs
    bad_proj = {
        "slug": "bad-proj",
        "title": "Bad Project Testing Validation",
        "summary": "A test project verifying strict URL and pinning validation.",
        "pillar": "budget",
        "jurisdiction": "Union of India",
        "authority": "Ministry of Finance",
        "posted_by": {"name": "Tester", "url": "javascript:alert(1)"},
        "enlisted_pr_url": "javascript:alert(2)",
        "sources": [{"name": "Src", "publisher": "Pub", "url": "https://127.0.0.1/file.csv"}],
        "tasks": [
            {
                "id": "prj-bad-1",
                "methodology_code": "P1",
                "title": "Check unpinned raw URL",
                "payload": {
                    "dataset_url": "https://github.com/org/repo/raw/main/file.pdf",
                    "expected_sha256": "a" * 64,
                },
            }
        ],
    }
    errs = validate_project(bad_proj, filename_stem="bad-proj")
    assert len(errs) >= 3

    # SEC-23: escape_like, PG sslmode, and DSN redaction
    assert escape_like("100%_test\\x") == "100\\%\\_test\\\\x"
    assert "supersecret" not in redact_database_url("postgresql://alice:supersecret@db.neon.tech/neondb")
    with pytest.raises(RuntimeError, match="sslmode"):
        enforce_pg_sslmode("postgresql://u:p@ep-prod.aws.neon.tech/neondb?sslmode=disable")
    enforced = enforce_pg_sslmode("postgresql://u:p@ep-prod.aws.neon.tech/neondb")
    assert "sslmode=require" in enforced
    # Loopback Postgres remains permitted for local/CI tests
    assert enforce_pg_sslmode("postgresql://postgres:postgres@127.0.0.1:5432/test").startswith("postgresql://")


# ---------------------------------------------------------------------------
# SEC-10 & SEC-24: CLI, Config & Provider Hardening
# ---------------------------------------------------------------------------


def test_sec10_sec24_cli_providers_https_rich_escape_symlink_and_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 1. OPENAI_API_KEY MUST NEVER be sent to VLLM_BASE_URL, and remote OLLAMA/VLLM never probed by probe_local
    seen_requests: list[httpx.Request] = []

    def _capture(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        return httpx.Response(200, json={"data": [{"id": "local-m"}]})

    env = {
        "OLLAMA_HOST": "https://remote-ollama.attacker.example",
        "VLLM_BASE_URL": "http://127.0.0.1:8001/v1",
        "OPENAI_API_KEY": "sk-secret-openai-key",
    }
    with httpx.Client(transport=httpx.MockTransport(_capture)) as client:
        detect_providers(probe_local=True, env=env, http_client=client)
    # Local Ollama and vLLM are never probed at all
    assert seen_requests == []

    # 2. Gemini sends API key in x-goog-api-key header, NEVER query param; rejects path-traversal model
    gemini_reqs: list[httpx.Request] = []

    def _gemini_handler(request: httpx.Request) -> httpx.Response:
        gemini_reqs.append(request)
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": json.dumps(
                                        {
                                            "editorial_interpretation": "Neutral audit context.",
                                            "benign_explanations": ["Standard GFR exception."],
                                        }
                                    )
                                }
                            ]
                        }
                    }
                ]
            },
        )

    lead = {
        "title": "Public Record Anomaly: Gemini Header Check",
        "summary": "Deterministic summary.",
        "monetary_exposure_inr": 1_000_000.0,
        "benign_explanations": ["Lawful reason."],
    }
    gem_cfg = ProviderInfo(
        provider="gemini",
        model="gemini-2.5-flash",
        label="Gemini",
        api_key_env="GEMINI_API_KEY",
    )
    with httpx.Client(transport=httpx.MockTransport(_gemini_handler)) as client:
        out = enrich_finding_summary(
            lead,
            gem_cfg,
            http_client=client,
            env={"GEMINI_API_KEY": "gem-secret-123"},
            allow_paid_provider=True,
            max_usd=1.0,
        )
    assert out["used_llm"] is True
    assert len(gemini_reqs) == 1
    assert gemini_reqs[0].headers.get("x-goog-api-key") == "gem-secret-123"
    assert "key=" not in str(gemini_reqs[0].url)

    # Path traversal in Gemini model rejected before HTTP call
    bad_gem = gem_cfg.model_copy(update={"model": "../v1/evil"})
    gemini_reqs.clear()
    with httpx.Client(transport=httpx.MockTransport(_gemini_handler)) as client:
        out_bad = enrich_finding_summary(
            lead,
            bad_gem,
            http_client=client,
            env={"GEMINI_API_KEY": "gem-secret-123"},
            allow_paid_provider=True,
            max_usd=1.0,
        )
    assert out_bad["used_llm"] is False
    assert gemini_reqs == []

    # 3. Cleartext non-loopback Hub URL rejected; loopback & HTTPS allowed
    with pytest.raises(ValueError, match="http://"):
        validate_hub_url("http://aiwatchdogs.in")
    assert validate_hub_url("https://aiwatchdogs.in") == "https://aiwatchdogs.in"
    assert validate_hub_url("http://127.0.0.1:8000") == "http://127.0.0.1:8000"

    # CLI join refuses cleartext non-loopback Hub
    bad_cli = cli_runner.invoke(cli_app, ["join", "--hub", "http://evil.example", "--dry-run"])
    assert bad_cli.exit_code == 2

    # 4. _default_handle() is random & pseudonymous; _safe_rich escapes Rich markup & ANSI escapes
    h1 = _default_handle()
    assert h1.startswith("civic-") and len(h1) == 12
    escaped = _safe_rich("[bold red]pwn[/bold red]\x1b[31m\x1b]8;;https://evil\x07click\x1b]8;;\x07")
    assert "\\[bold red]" in escaped
    assert "\x1b" not in escaped

    # 5. _fetch_finding rejects path traversal
    cfg = AgentConfig(handle="runner-sec", hub_url="https://aiwatchdogs.in", api_token="awd_tok")
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))) as client:
        runner = AgentRunner(cfg, http_client=client)
        with pytest.raises(ValueError, match="Invalid finding_id"):
            runner._fetch_finding("../sponsors/pledge")

    # 6. Symlink config rejected & loose permissions tightened to 0600
    real_cfg = tmp_path / "real_config.json"
    save_config(cfg, real_cfg)
    real_cfg.chmod(0o644)
    loaded = load_config(real_cfg)
    assert loaded is not None
    assert stat.S_IMODE(real_cfg.stat().st_mode) == 0o600

    sym_cfg = tmp_path / "sym_config.json"
    sym_cfg.symlink_to(real_cfg)
    with pytest.raises(ValueError, match="symlink"):
        load_config(sym_cfg)

    # 7. BudgetTracker reloads concurrent spend from locked ledger on disk before dispatch
    ledger = tmp_path / "spend_ledger.json"
    tracker = BudgetTracker(session_max_usd=1.0, daily_max_usd=0.50, allow_paid_provider=True, ledger_path=ledger)
    # Simulate another process recording $0.49 spend
    record_daily_spend(ledger, amount_usd=0.49, provider="openai", model="gpt-4o-mini")
    allowed, reason = tracker.check_before_dispatch(gem_cfg, 0.05)
    assert allowed is False
    assert reason == "daily_budget_cap_exceeded"


# ---------------------------------------------------------------------------
# SEC-35 .. SEC-38: Anti-Impersonation, Registration Rate Limits, Sanitization,
# GitHub Proof-of-Control, and Anti-Sybil Same-IP Quorum Hardening
# ---------------------------------------------------------------------------


def test_sec35_display_name_and_profile_sanitization_blocks_xss_and_markdown_injection(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "sec35.db", seed=True)
    with TestClient(app) as client:
        for bad_payload in (
            {"handle": "probe-xss-1", "display_name": "<img src=x onerror=alert(1)>"},
            {"handle": "probe-md-1", "display_name": 'evil"\ncanonical_url: "https://evil.example'},
            {"handle": "probe-ctrl-1", "display_name": "norm\x00hidden"},
            {"handle": "probe-html-reg", "region": "<script>alert(1)</script>"},
            {"handle": "probe-html-agent", "coding_agent": "Codex\nInjected"},
        ):
            r = client.post("/api/agents/register", json=bad_payload)
            assert r.status_code == 422, f"Expected 422 for {bad_payload}, got {r.status_code}: {r.text}"

        # Defense-in-depth: even if a legacy dirty row already exists in DB, `.md` escapes HTML & strips newlines.
        with app.state.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO agents (
                    id, handle, display_name, provider, model, mode, region,
                    capabilities_json, api_token_hash, status, joined_at, last_heartbeat_at
                ) VALUES (
                    'agt-legacy-dirty', 'legacy-dirty',
                    '<img src=x onerror=alert(1)>\ncanonical_url: "https://evil.example',
                    'rule-engine', 'x', 'auto', 'IN', '[]', 'legacyhash', 'active',
                    '2026-11-28T00:00:00Z', '2026-11-28T00:00:00Z'
                )
                """
            )
        md = client.get("/a/legacy-dirty.md")
        assert md.status_code == 200
        assert "<img" not in md.text
        assert "&lt;img src=x onerror=alert(1)&gt;" in md.text
        assert "\ncanonical_url: \"https://evil.example" not in md.text
        assert "**GitHub verified:** No (self-declared handle)" in md.text


def test_sec36_register_rate_limit_blocks_probe_bursts_while_allowing_valid_reconnects(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "sec36.db", seed=True)
    with TestClient(app) as client:
        first_token = ""
        for i in range(5):
            r = client.post(
                "/api/agents/register",
                json={"handle": f"rl-ok-{i}", "provider": "rule-engine", "model": "deterministic-python-v1"},
            )
            assert r.status_code == 200, r.text
            if i == 0:
                first_token = r.json()["api_token"]

        # 6th new registration from the same IP in 10 min MUST be rate-limited (HTTP 429 + Retry-After).
        r_blocked = client.post(
            "/api/agents/register",
            json={"handle": "rl-overflow-6", "provider": "rule-engine", "model": "deterministic-python-v1"},
        )
        assert r_blocked.status_code == 429
        assert "retry-after" in r_blocked.headers

        # A forged X-Agent-Token on a new handle MUST NOT bypass the new-registration rate limit.
        r_forged = client.post(
            "/api/agents/register",
            headers={"X-Agent-Token": "awd_forged_token_attempt"},
            json={"handle": "rl-forged-7", "provider": "rule-engine", "model": "deterministic-python-v1"},
        )
        assert r_forged.status_code == 429

        # A legitimate existing agent reconnecting with its valid X-Agent-Token MUST still succeed!
        r_reconn = client.post(
            "/api/agents/register",
            headers={"X-Agent-Token": first_token},
            json={"handle": "rl-ok-0", "provider": "rule-engine", "model": "deterministic-python-v1"},
        )
        assert r_reconn.status_code == 200, r_reconn.text
        assert r_reconn.json()["status"] == "reconnected"


def test_sec37_github_gist_verification_flow_and_duplicate_github_uid_rejection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app(db_path=tmp_path / "sec37.db", seed=True)
    with TestClient(app) as client:
        alice = _register(client, "alice-gh")
        alt = _register(client, "alice-alt")
        fallback = _register(client, "civic-sandbox-1234")

        # Unverified by default across API responses
        assert alice["github_verified"] is False
        prof_before = client.get("/api/contributors/alice-gh").json()
        assert prof_before["agent"]["github_verified"] is False
        live_before = client.get("/api/swarm/live").json()
        alice_node = next(n for n in live_before["active_nodes"] if n["handle"] == "alice-gh")
        assert alice_node["github_verified"] is False

        # Fallback civic-* handle cannot be GitHub-verified
        r_fb = client.post(
            "/api/agents/verify-github/challenge",
            headers={"X-Agent-Token": fallback["api_token"]},
        )
        assert r_fb.status_code == 422

        # Issue challenge for @alice-gh
        ch = client.post(
            "/api/agents/verify-github/challenge",
            headers={"X-Agent-Token": alice["api_token"]},
        )
        assert ch.status_code == 200, ch.text
        challenge_token = ch.json()["challenge_token"]
        assert challenge_token.startswith("aiwatchdogs-verify:alice-gh:")

        fake_gists: dict[str, dict[str, Any]] = {
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaa1111": {
                "id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaa1111",
                "public": True,
                "owner": {"login": "impersonator", "id": 99999},
                "files": {"aiwatchdogs-verify.txt": {"content": challenge_token}},
            },
            "bbbbbbbbbbbbbbbbbbbbbbbbbbbb2222": {
                "id": "bbbbbbbbbbbbbbbbbbbbbbbbbbbb2222",
                "public": True,
                "owner": {"login": "alice-gh", "id": 424242},
                "files": {"aiwatchdogs-verify.txt": {"content": "wrong token"}},
            },
            "cccccccccccccccccccccccccccc3333": {
                "id": "cccccccccccccccccccccccccccc3333",
                "public": True,
                "owner": {"login": "alice-gh", "id": 424242},
                "files": {"aiwatchdogs-verify.txt": {"content": f"proof:\n{challenge_token}\n"}},
            },
        }

        def _mock_gh_get(cls: Any, path: str, *, http_client: Any = None) -> Any:
            _ = http_client
            if path.startswith("/gists/"):
                return fake_gists.get(path.split("/gists/", 1)[1])
            return []

        monkeypatch.setattr(app.state.desk.__class__, "_github_api_get_json", classmethod(_mock_gh_get))

        # 1. Wrong gist owner -> 403
        r_wrong_owner = client.post(
            "/api/agents/verify-github/confirm",
            headers={"X-Agent-Token": alice["api_token"]},
            json={"gist_url": "https://gist.github.com/impersonator/aaaaaaaaaaaaaaaaaaaaaaaaaaaa1111"},
        )
        assert r_wrong_owner.status_code == 403

        # 2. Missing challenge token -> 400
        r_wrong_tok = client.post(
            "/api/agents/verify-github/confirm",
            headers={"X-Agent-Token": alice["api_token"]},
            json={"gist_url": "https://gist.github.com/alice-gh/bbbbbbbbbbbbbbbbbbbbbbbbbbbb2222"},
        )
        assert r_wrong_tok.status_code == 400

        # 3. Valid public gist owned by @alice-gh -> verified!
        r_ok = client.post(
            "/api/agents/verify-github/confirm",
            headers={"X-Agent-Token": alice["api_token"]},
            json={"gist_url": "https://gist.github.com/alice-gh/cccccccccccccccccccccccccccc3333"},
        )
        assert r_ok.status_code == 200, r_ok.text
        assert r_ok.json()["github_verified"] is True
        assert r_ok.json()["github_user_id"] == "424242"

        # Profile JSON, live snapshot, and .md all reflect github_verified=True
        assert client.get("/api/contributors/alice-gh").json()["agent"]["github_verified"] is True
        live_after = client.get("/api/swarm/live").json()
        assert next(n for n in live_after["active_nodes"] if n["handle"] == "alice-gh")["github_verified"] is True
        assert "**GitHub verified:** Yes" in client.get("/a/alice-gh.md").text

        # 4. Same GitHub numeric user ID (e.g. after username rename) MUST NOT verify a 2nd handle -> 409!
        ch_alt = client.post(
            "/api/agents/verify-github/challenge",
            headers={"X-Agent-Token": alt["api_token"]},
        )
        assert ch_alt.status_code == 200
        alt_tok = ch_alt.json()["challenge_token"]
        fake_gists["dddddddddddddddddddddddddddd4444"] = {
            "id": "dddddddddddddddddddddddddddd4444",
            "public": True,
            "owner": {"login": "alice-alt", "id": 424242},
            "files": {"aiwatchdogs-verify.txt": {"content": alt_tok}},
        }
        r_dup_uid = client.post(
            "/api/agents/verify-github/confirm",
            headers={"X-Agent-Token": alt["api_token"]},
            json={"gist_url": "https://gist.github.com/alice-alt/dddddddddddddddddddddddddddd4444"},
        )
        assert r_dup_uid.status_code == 409


def test_sec38_sybil_same_ip_quorum_guard_blocks_unverified_sockpuppets_allows_verified_nat(
    tmp_path: Path,
) -> None:
    app = create_app(db_path=tmp_path / "sec38.db", seed=True)
    with TestClient(app) as client:
        ip_a = {"x-vercel-forwarded-for": "203.0.113.42"}
        ip_b = {"x-vercel-forwarded-for": "198.51.100.77"}

        author = client.post(
            "/api/agents/register",
            headers=ip_a,
            json={
                "handle": "sybil-author",
                "provider": "rule-engine",
                "model": "deterministic-python-v1",
                "coding_agent": "Claude Code",
                "coding_agent_model": "claude-opus-4-6",
            },
        ).json()
        sockpuppet = client.post(
            "/api/agents/register",
            headers=ip_a,
            json={
                "handle": "sybil-sock",
                "provider": "rule-engine",
                "model": "deterministic-python-v1",
                "coding_agent": "Codex",
                "coding_agent_model": "gpt-6",
            },
        ).json()
        independent = client.post(
            "/api/agents/register",
            headers=ip_b,
            json={
                "handle": "indep-peer",
                "provider": "rule-engine",
                "model": "deterministic-python-v1",
                "coding_agent": "Gemini",
                "coding_agent_model": "gemini-3-pro",
            },
        ).json()

        # Author submits a finding
        claim = client.post(
            "/api/desk/next",
            headers={"X-Agent-Token": author["api_token"]},
            json={"handle": author["handle"], "mode": "investigate", "preferred_mission": "assam-ocds-single-bid"},
        )
        assert claim.status_code == 200
        real_sha = claim.json()["task"]["payload"]["expected_sha256"]
        sub = client.post(
            "/api/desk/submit",
            headers={"X-Agent-Token": author["api_token"]},
            json={
                "lease_id": claim.json()["lease_id"],
                "outcome": "finding",
                "finding": {
                    "title": "Public Record Anomaly: Sybil Independence Test",
                    "summary": "Deterministic observation.",
                    "methodology_code": "M1",
                    "entity_name": "Assam Health",
                    "artifact_sha256": real_sha,
                    "anomaly_metrics": {"numerator": 50, "denominator": 100},
                    "benign_explanations": ["Lawful OEM exception under GFR."],
                },
            },
        )
        assert sub.status_code == 200
        fid = sub.json()["finding_id"]
        with app.state.db.read() as conn:
            pv_task_id = str(
                conn.execute(
                    "SELECT id FROM tasks WHERE target_finding_id = ? AND task_type = 'peer_verify' LIMIT 1",
                    (fid,),
                ).fetchone()["id"]
            )

        # 1. Unverified sockpuppet from SAME IP (even with distinct handle/harness/model!) is blocked!
        auto_sock = client.post(
            "/api/desk/next",
            headers={"X-Agent-Token": sockpuppet["api_token"]},
            json={"handle": sockpuppet["handle"], "mode": "verify"},
        )
        assert auto_sock.status_code == 204
        explicit_sock = client.post(
            "/api/desk/next",
            headers={"X-Agent-Token": sockpuppet["api_token"]},
            json={"handle": sockpuppet["handle"], "mode": "verify", "task_id": pv_task_id},
        )
        assert explicit_sock.status_code == 409
        assert "sybil" in explicit_sock.text.lower()

        # 2. Independent verifier from DIFFERENT IP CAN claim cleanly.
        indep_claim = client.post(
            "/api/desk/next",
            headers={"X-Agent-Token": independent["api_token"]},
            json={"handle": independent["handle"], "mode": "verify"},
        )
        assert indep_claim.status_code == 200
        assert indep_claim.json()["task"]["target_finding_id"] == fid

        # 3. Once both accounts on IP A prove distinct GitHub IDs (shared campus/office NAT), sockpuppet IS allowed!
        with app.state.db.transaction() as conn:
            conn.execute(
                "UPDATE agents SET github_verified = 1, github_user_id = '1001' WHERE handle = 'sybil-author'"
            )
            conn.execute(
                "UPDATE agents SET github_verified = 1, github_user_id = '1002' WHERE handle = 'sybil-sock'"
            )
        nat_claim = client.post(
            "/api/desk/next",
            headers={"X-Agent-Token": sockpuppet["api_token"]},
            json={"handle": sockpuppet["handle"], "mode": "verify"},
        )
        assert nat_claim.status_code == 200
        assert nat_claim.json()["task"]["target_finding_id"] == fid
