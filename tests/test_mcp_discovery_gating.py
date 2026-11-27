"""Tests that MCP discovery is advertised only when Streamable HTTP MCP is served."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aiwatchdogs.hub.agent_access import is_mcp_enabled
from aiwatchdogs.hub.app import create_app


@pytest.mark.parametrize(
    ("vercel", "enable_mcp", "expected"),
    [
        (None, None, True),
        ("1", None, False),
        ("1", "true", True),
    ],
)
def test_is_mcp_enabled_env_rule(
    monkeypatch: pytest.MonkeyPatch,
    vercel: str | None,
    enable_mcp: str | None,
    expected: bool,
) -> None:
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("AIWATCHDOGS_ENABLE_MCP", raising=False)
    if vercel is not None:
        monkeypatch.setenv("VERCEL", vercel)
    if enable_mcp is not None:
        monkeypatch.setenv("AIWATCHDOGS_ENABLE_MCP", enable_mcp)
    assert is_mcp_enabled() is expected


def test_mcp_discovery_is_hidden_when_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("AIWATCHDOGS_ENABLE_MCP", raising=False)
    monkeypatch.delenv("VERCEL_ENV", raising=False)

    app = create_app(db_path=tmp_path / "mcp_disabled.db", seed=True)
    with TestClient(app) as client:
        llms = client.get("/llms.txt")
        assert llms.status_code == 200
        assert "/mcp" not in llms.text
        assert 'rel="mcp"' not in llms.headers.get("link", "")

        assert client.get("/.well-known/mcp.json").status_code == 404

        catalog = client.get("/.well-known/api-catalog").json()
        assert not any(entry["anchor"].endswith("/mcp") for entry in catalog["linkset"])

        mcp = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        assert mcp.status_code == 410
        assert client.get("/mcp").status_code == 410


def test_mcp_discovery_is_visible_when_enabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("AIWATCHDOGS_ENABLE_MCP", raising=False)
    monkeypatch.delenv("VERCEL_ENV", raising=False)

    app = create_app(db_path=tmp_path / "mcp_enabled.db", seed=True)
    with TestClient(app) as client:
        llms = client.get("/llms.txt")
        assert llms.status_code == 200
        assert "/mcp" in llms.text
        assert 'rel="mcp"' in llms.headers.get("link", "")

        assert client.get("/.well-known/mcp.json").status_code == 200
