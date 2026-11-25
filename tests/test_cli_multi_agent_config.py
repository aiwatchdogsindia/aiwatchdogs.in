"""Several agents on one machine keep separate credentials (one config file per handle)."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from aiwatchdogs.cli import config as cfg_mod
from aiwatchdogs.cli.config import (
    ENV_CONFIG_PATH,
    ENV_HANDLE,
    AgentConfig,
    AmbiguousConfigError,
    get_config_path,
    get_handle_config_path,
    get_spend_ledger_path,
    list_saved_handles,
    load_config,
    load_legacy_config_for_handle,
    save_config,
)
from aiwatchdogs.cli.main import app as cli_app

cli_runner = CliRunner()


@pytest.fixture()
def cfg_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv(ENV_CONFIG_PATH, raising=False)
    monkeypatch.delenv(ENV_HANDLE, raising=False)
    legacy = tmp_path / "aiwatchdogs" / "config.json"
    monkeypatch.setattr(cfg_mod, "DEFAULT_CONFIG_PATH", legacy)
    return legacy


def _save(handle: str, token: str, path: Path | None = None) -> Path:
    cfg = AgentConfig(handle=handle, hub_url="https://aiwatchdogs.in", api_token=token)
    return save_config(cfg, path=path or get_handle_config_path(handle))


def test_each_handle_gets_its_own_file_and_tokens_survive(cfg_home: Path) -> None:
    for i in range(1, 5):
        _save(f"agent-0{i}", f"tok-{i}")
    assert list_saved_handles() == ["agent-01", "agent-02", "agent-03", "agent-04"]
    for i in range(1, 5):
        loaded = load_config(get_config_path(handle=f"agent-0{i}"))
        assert loaded is not None and loaded.api_token == f"tok-{i}"
    assert not cfg_home.exists()


def test_no_handle_uses_the_only_identity_and_refuses_to_guess(cfg_home: Path) -> None:
    assert get_config_path() == cfg_home  # nothing saved yet
    _save("agent-01", "tok-1")
    assert get_config_path() == get_handle_config_path("agent-01")
    _save("agent-02", "tok-2")
    with pytest.raises(AmbiguousConfigError) as err:
        get_config_path()
    assert err.value.handles == ["agent-01", "agent-02"]


def test_env_handle_and_explicit_path_take_precedence(
    cfg_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _save("agent-01", "tok-1")
    _save("agent-02", "tok-2")
    monkeypatch.setenv(ENV_HANDLE, "agent-02")
    assert get_config_path() == get_handle_config_path("agent-02")
    pinned = tmp_path / "pinned.json"
    monkeypatch.setenv(ENV_CONFIG_PATH, str(pinned))
    assert get_config_path(handle="agent-01") == pinned


def test_legacy_config_still_works_and_is_reused_only_for_its_own_handle(cfg_home: Path) -> None:
    _save("old-timer", "legacy-tok", path=cfg_home)
    assert get_config_path() == cfg_home
    reused = load_legacy_config_for_handle("old-timer")
    assert reused is not None and reused.api_token == "legacy-tok"
    assert load_legacy_config_for_handle("someone-else") is None
    # A second, different identity makes the no-handle default ambiguous instead of silently wrong.
    _save("agent-02", "tok-2")
    with pytest.raises(AmbiguousConfigError):
        get_config_path()
    # The same identity saved per-handle is not ambiguous.
    get_handle_config_path("agent-02").unlink()
    _save("old-timer", "legacy-tok")
    assert get_config_path() == get_handle_config_path("old-timer")


@pytest.mark.parametrize("bad", ["../evil", "a/b", "", ".", "x" * 80, "has space"])
def test_unsafe_handles_never_become_paths(cfg_home: Path, bad: str) -> None:
    with pytest.raises(ValueError):
        get_handle_config_path(bad)


def test_all_handles_share_one_spend_ledger(cfg_home: Path) -> None:
    a = get_spend_ledger_path(get_handle_config_path("agent-01"))
    b = get_spend_ledger_path(get_handle_config_path("agent-02"))
    assert a == b == cfg_home.parent / "spend_ledger.json"


def test_cli_whoami_asks_for_handle_when_several_are_saved(cfg_home: Path) -> None:
    _save("agent-01", "tok-1111111111")
    _save("agent-02", "tok-2222222222")
    res = cli_runner.invoke(cli_app, ["whoami"])
    assert res.exit_code == 2
    assert "--handle" in res.output and "agent-01" in res.output
    res = cli_runner.invoke(cli_app, ["whoami", "--handle", "agent-02"])
    assert res.exit_code == 0
    assert "agent-02" in res.output and "agent-01" not in res.output
