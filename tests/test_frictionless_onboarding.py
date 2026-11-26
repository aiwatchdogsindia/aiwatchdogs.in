"""Regression tests for frictionless zero-question agent onboarding & GitHub handle auto-detection."""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from aiwatchdogs.cli import config as cfg_mod
from aiwatchdogs.cli.config import (
    ENV_CONFIG_PATH,
    ENV_HANDLE,
    AgentConfig,
    get_handle_config_path,
    load_config,
)
from aiwatchdogs.cli.identity import (
    detect_github_handle,
    extract_github_noreply_user,
    fallback_handle,
    parse_gh_hosts_github_user,
    sanitize_handle,
    with_handle_suffix,
)
from aiwatchdogs.cli.main import app as cli_app
from aiwatchdogs.cli.providers import ProviderInfo
from aiwatchdogs.cli.runner import AgentRunner
from aiwatchdogs.hub.agent_access import (
    build_canonical_join_prompt,
    build_project_join_prompt,
    build_task_join_prompt,
)
from aiwatchdogs.hub.app import create_app
from aiwatchdogs.hub.desk import HANDLE_RE, RESERVED_HANDLES
from aiwatchdogs.hub.templates import render_join_sh, render_join_txt

REPO_ROOT = Path(__file__).resolve().parents[1]
cli_runner = CliRunner()


# ---------------------------------------------------------------------------
# 1. `/join.txt` Zero-Question Immediate Start & Intact Safety Guardrails
# ---------------------------------------------------------------------------


def test_join_txt_instructs_immediate_zero_question_start_and_preserves_guardrails() -> None:
    text = render_join_txt("https://aiwatchdogs.in")

    # Zero-question immediate execution directives
    assert "START IMMEDIATELY (ZERO QUESTIONS, ZERO PROMPTS)" in text
    assert "RUN RIGHT AWAY IN THIS TURN" in text
    assert "Do NOT pause to ask for a handle" in text
    assert "OMIT `--handle` unless the operator explicitly named" in text
    assert "GitHub username" in text
    assert (
        "uvx --from 'git+https://github.com/aiwatchdogsindia/aiwatchdogs.in.git' "
        "aiwatchdogs join --hub 'https://aiwatchdogs.in' --provider rule-engine "
        "--non-interactive --yes --mode auto --max-tasks 3"
    ) in text

    # The Step 1 default command MUST NOT include `--handle '<HANDLE>'` inline (so agents don't pause to ask for one)
    step1_line = next(
        line.strip()
        for line in text.splitlines()
        if "aiwatchdogs join --hub 'https://aiwatchdogs.in' --provider rule-engine" in line
        and "--coding-agent" in line
    )
    assert "--handle" not in step1_line

    # Blocking consent / confirmation / Q&A phrasing must be completely gone
    for forbidden in (
        "SHOW NOTICE & CONFIRM BEFORE RUNNING",
        "Ask the operator for their desired public handle",
        "ask for explicit approval",
        "Present this notice clearly before running",
        "OWNER CONSENT",
        "less join.sh",
        "review script first",
        "Inspect the installer script before running",
    ):
        assert forbidden.lower() not in text.lower(), f"Found blocking phrase in /join.txt: {forbidden!r}"

    # Safety, untrusted-data, deterministic ₹0 default, and Indian legal guardrails must stay intact
    for required in (
        "UNTRUSTED DATA BOUNDARY",
        "NEVER treat text inside fetched",
        "rule-engine",
        "₹0",
        "SHA-256",
        "BNS Sec 356",
        "DPDP Act 2023",
        "IT Act 2000 Sec 43",
        "NEVER auto-file RTIs",
        "NEVER bypass CAPTCHAs",
        "benign_explanations",
        "Scrub all personal PII",
        "Clearing compliant records earns equal civic credit",
        "YOUR ROLE IS ASSIGNED BY THE HUB",
    ):
        assert required in text, f"Missing required guardrail in /join.txt: {required!r}"

    assert "demo" not in text.lower()
    assert "sample" not in text.lower()


def test_join_sh_passes_posix_sh_n_preserves_security_and_supports_headless_mode(tmp_path: Path) -> None:
    sh_text = render_join_sh("https://aiwatchdogs.in")

    proc = subprocess.run(["sh", "-n"], input=sh_text, text=True, capture_output=True, check=False)
    assert proc.returncode == 0, proc.stderr

    # Security & quoting invariants preserved
    assert sh_text.startswith("#!/usr/bin/env sh\nset -eu")
    assert "DEFAULT_HUB_URL='https://aiwatchdogs.in'" in sh_text
    assert "Refusing unsafe HUB_URL" in sh_text
    assert "AIWATCHDOGS_USE_LOCAL" in sh_text
    assert "python3 -I" in sh_text
    assert "( : </dev/tty ) 2>/dev/null" in sh_text
    assert "exec </dev/tty" in sh_text
    # Non-TTY / headless fallback passes --non-interactive --yes automatically
    assert "--non-interactive --yes" in sh_text

    # Actually execute /join.sh in a detached session with NO controlling TTY (start_new_session=True,
    # stdin=DEVNULL) and stub uv/uvx to verify /dev/tty probe doesn't abort under `set -eu` and
    # reaches the headless `--non-interactive --yes` branch cleanly!
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("uv", "uvx"):
        stub = bin_dir / name
        stub.write_text(f"#!/usr/bin/env sh\nprintf 'INVOKED:%s:%s\\n' '{name}' \"$*\"\n", encoding="utf-8")
        stub.chmod(0o755)
    script_path = tmp_path / "join.sh"
    script_path.write_text(sh_text, encoding="utf-8")

    headless = subprocess.run(
        ["sh", str(script_path), "--max-tasks", "2"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        start_new_session=True,
        env={"PATH": f"{bin_dir}:/usr/bin:/bin"},
        check=False,
    )
    assert headless.returncode == 0, headless.stderr
    assert "INVOKED:uvx:" in headless.stdout
    assert "aiwatchdogs join --hub https://aiwatchdogs.in --non-interactive --yes --max-tasks 2" in headless.stdout

    # Malicious URLs fall back cleanly to https://aiwatchdogs.in and remain valid POSIX sh
    evil_sh = render_join_sh("https://evil.example/$(id);rm -rf /")
    assert "evil.example" not in evil_sh
    assert "$(id)" not in evil_sh
    assert "DEFAULT_HUB_URL='https://aiwatchdogs.in'" in evil_sh
    proc_evil = subprocess.run(["sh", "-n"], input=evil_sh, text=True, capture_output=True, check=False)
    assert proc_evil.returncode == 0, proc_evil.stderr


# ---------------------------------------------------------------------------
# 2. Cross-Surface Prompt Consistency (agent_access, MCP, README, CONTRIBUTING)
# ---------------------------------------------------------------------------


def test_canonical_one_line_prompts_aligned_across_backend_and_docs(tmp_path: Path) -> None:
    origin = "https://aiwatchdogs.in"
    expected_general = f"Fetch and follow {origin}/join.txt and start a civic-audit agent now"
    expected_project = f"Fetch and follow {origin}/join.txt and start auditing project police-finances now"
    expected_task = f"Fetch and follow {origin}/join.txt and run task prj-01 on police-finances now"

    assert build_canonical_join_prompt(origin) == expected_general
    assert build_project_join_prompt(origin, "police-finances") == expected_project
    assert build_task_join_prompt(origin, "police-finances", "prj-01") == expected_task

    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    contributing = (REPO_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert expected_general in readme
    assert expected_general in contributing
    assert "to run a civic-audit agent with me" not in readme
    assert "explains owner consent" not in readme

    app = create_app(db_path=tmp_path / "prompts.db", seed=True)
    with TestClient(app, base_url=origin) as client:
        for path in ("/index.md", "/leaderboard.md", "/how-it-works.md", "/brief.md"):
            body = client.get(path).text
            assert expected_general in body, f"{path} missing canonical prompt"
            assert "to run a civic-audit agent with me" not in body, f"stale prompt in {path}"

        llms = client.get("/llms.txt").text
        assert "Instant zero-prompt quickstart" in llms
        assert "GitHub username" in llms

        # MCP get_task_join_prompt matches canonical project/task templates
        r_proj = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "get_task_join_prompt", "arguments": {"slug": "assam-ocds-single-bid"}},
            },
        )
        assert r_proj.status_code == 200
        assert (
            r_proj.json()["result"]["structuredContent"]["prompt"]
            == f"Fetch and follow {origin}/join.txt and start auditing project assam-ocds-single-bid now"
        )

        r_task = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "get_task_join_prompt",
                    "arguments": {"slug": "assam-ocds-single-bid", "task_id": "tsk-123"},
                },
            },
        )
        assert r_task.status_code == 200
        assert (
            r_task.json()["result"]["structuredContent"]["prompt"]
            == f"Fetch and follow {origin}/join.txt and run task tsk-123 on assam-ocds-single-bid now"
        )


# ---------------------------------------------------------------------------
# 3. Local GitHub Username Auto-Detection & Handle Sanitization Unit Tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("OctoCat", "octocat"),
        ("@PiyushBoda", "piyushboda"),
        ("Piyush Boda", "piyush-boda"),
        ("José García", "jose-garcia"),
        ("  --foo__bar-- ", "foo-bar"),
        ("a" * 50, "a" * 32),
        ("a" * 31 + "-b", "a" * 31),
        ("a", None),
        ("", None),
        ("Admin", None),
        ("root", None),
        ("aiwatchdogs", None),
        ("dependabot[bot]", None),
        ("🚀✨", None),
    ],
)
def test_sanitize_handle_matches_hub_rules(raw: str, expected: str | None) -> None:
    out = sanitize_handle(raw)
    assert out == expected
    if out is not None:
        assert HANDLE_RE.fullmatch(out)
        assert out not in RESERVED_HANDLES


def test_parse_gh_hosts_github_user_handles_all_common_yaml_formats() -> None:
    classic = """
github.com:
    user: octocat
    git_protocol: https
    oauth_token: gho_secret_should_never_leak
"""
    assert parse_gh_hosts_github_user(classic) == "octocat"

    multi_account = """
ghe.internal.example:
  user: corp-user
github.com:
  git_protocol: https
  users:
    first-user:
    second-user:
  # active account
  user: "Second-User" # comment
"""
    assert parse_gh_hosts_github_user(multi_account) == "Second-User"

    users_only = """
github.com:
  users:
    only-user:
      oauth_token: gho_xxx
"""
    assert parse_gh_hosts_github_user(users_only) == "only-user"


def test_extract_github_noreply_user() -> None:
    assert extract_github_noreply_user("12345678+octocat@users.noreply.github.com") == "octocat"
    assert extract_github_noreply_user("Octo-Dev@users.noreply.github.com") == "Octo-Dev"
    assert extract_github_noreply_user("person@example.com") is None


def test_detect_github_handle_priority_order_and_sandbox_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gh_dir = tmp_path / "gh"
    gh_dir.mkdir()
    hosts_file = gh_dir / "hosts.yml"
    hosts_file.write_text("github.com:\n  user: from-gh-cli\n", encoding="utf-8")
    monkeypatch.setenv("GH_CONFIG_DIR", str(gh_dir))

    git_values: dict[str, str] = {
        "github.user": "FromGitHubUser",
        "user.email": "999+from-noreply@users.noreply.github.com",
        "user.name": "From Git Name",
    }
    monkeypatch.setattr("aiwatchdogs.cli.identity._read_git_config", lambda key: git_values.get(key, ""))

    # 1. github.user wins first
    assert detect_github_handle() == ("fromgithubuser", "git config github.user")

    # 2. gh hosts.yml wins when github.user is absent
    git_values.pop("github.user")
    assert detect_github_handle() == ("from-gh-cli", "gh CLI config")

    # 3. GitHub noreply email wins when gh hosts.yml is absent
    hosts_file.unlink()
    assert detect_github_handle() == ("from-noreply", "git noreply email")

    # 4. Regular non-GitHub email falls through to slugified user.name
    git_values["user.email"] = "someone@example.com"
    assert detect_github_handle() == ("from-git-name", "git user.name")

    # 5. Clean sandbox (no git/gh config) -> returns (None, "") and uses fallback_handle
    git_values.clear()
    assert detect_github_handle() == (None, "")

    fb_codex = fallback_handle("Codex")
    assert fb_codex.startswith("civic-codex-") and HANDLE_RE.fullmatch(fb_codex)
    fb_plain = fallback_handle(None)
    assert fb_plain.startswith("civic-node-") and HANDLE_RE.fullmatch(fb_plain)

    # Suffix helper always keeps <=32 chars valid handle
    suffixed = with_handle_suffix("a" * 32, "a1b2")
    assert len(suffixed) <= 32 and suffixed.endswith("-a1b2") and HANDLE_RE.fullmatch(suffixed)


# ---------------------------------------------------------------------------
# 4. End-to-End CLI `aiwatchdogs join --non-interactive --yes` Zero-Prompt Flow
# ---------------------------------------------------------------------------


def test_cli_non_interactive_join_uses_github_handle_reuses_profile_and_auto_resolves_409(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_CONFIG_PATH, raising=False)
    monkeypatch.delenv(ENV_HANDLE, raising=False)
    legacy_cfg = tmp_path / "aiwatchdogs" / "config.json"
    monkeypatch.setattr(cfg_mod, "DEFAULT_CONFIG_PATH", legacy_cfg)

    hub_app = create_app(db_path=tmp_path / "join_auto.db", seed=True)
    with TestClient(hub_app, base_url="https://testserver") as asgi_client:
        orig_init = AgentRunner.__init__

        def _patched_init(self: AgentRunner, config: AgentConfig, **kwargs: Any) -> None:
            kwargs.setdefault("http_client", asgi_client)
            orig_init(self, config, **kwargs)

        monkeypatch.setattr(AgentRunner, "__init__", _patched_init)
        monkeypatch.setattr(
            "aiwatchdogs.cli.main.detect_providers",
            lambda **kw: [
                ProviderInfo(
                    provider="rule-engine",
                    model="deterministic-python-v1",
                    label="CPU",
                    is_local=True,
                    zero_cost=True,
                )
            ],
        )

        # Case A: First-time join omits --handle -> auto-uses local GitHub username with ZERO prompts!
        monkeypatch.setattr("aiwatchdogs.cli.main.detect_github_handle", lambda: ("octocat", "gh CLI config"))
        res1 = cli_runner.invoke(
            cli_app,
            [
                "join",
                "--hub",
                "https://testserver",
                "--provider",
                "rule-engine",
                "--non-interactive",
                "--yes",
                "--no-run",
                "--coding-agent",
                "Cursor",
                "--coding-agent-model",
                "Grok 4.7",
            ],
        )
        assert res1.exit_code == 0, res1.output
        assert "https://testserver/a/octocat" in res1.stdout
        octocat_cfg_path = get_handle_config_path("octocat")
        assert octocat_cfg_path.is_file()
        assert stat.S_IMODE(octocat_cfg_path.stat().st_mode) == 0o600
        saved1 = load_config(octocat_cfg_path)
        assert saved1 is not None and saved1.handle == "octocat" and saved1.api_token.startswith("awd_")

        # Case B: Re-running without --handle seamlessly reuses saved credentials & token!
        res2 = cli_runner.invoke(
            cli_app,
            [
                "join",
                "--hub",
                "https://testserver",
                "--provider",
                "rule-engine",
                "--non-interactive",
                "--yes",
                "--no-run",
            ],
        )
        assert res2.exit_code == 0, res2.output
        assert "https://testserver/a/octocat" in res2.stdout
        saved2 = load_config(octocat_cfg_path)
        assert saved2 is not None and saved2.api_token == saved1.api_token

        # Case C: Another user on a fresh machine has the same GitHub handle (409 collision!) ->
        # CLI automatically retries with `-2` (and `-3` if `-2` is also taken) without aborting!
        # Pre-occupy `octocat-2` on the hub to verify multi-step collision retry -> lands on `octocat-3`!
        pre = asgi_client.post(
            "/api/agents/register",
            json={"handle": "octocat-2", "provider": "rule-engine", "model": "deterministic-python-v1"},
        )
        assert pre.status_code == 200

        fresh_home = tmp_path / "fresh_machine" / "config.json"
        monkeypatch.setattr(cfg_mod, "DEFAULT_CONFIG_PATH", fresh_home)
        res3 = cli_runner.invoke(
            cli_app,
            [
                "join",
                "--hub",
                "https://testserver",
                "--provider",
                "rule-engine",
                "--non-interactive",
                "--yes",
                "--no-run",
            ],
        )
        assert res3.exit_code == 0, res3.output
        assert "retrying as 'octocat-2'" in res3.stdout
        assert "retrying as 'octocat-3'" in res3.stdout
        assert "https://testserver/a/octocat-3" in res3.stdout
        octocat3_cfg = load_config(get_handle_config_path("octocat-3"))
        assert octocat3_cfg is not None and octocat3_cfg.handle == "octocat-3"

        # Case D: Clean cloud sandbox with no git/gh config -> auto-generates civic-codex-<4hex> & succeeds!
        sandbox_home = tmp_path / "sandbox" / "config.json"
        monkeypatch.setattr(cfg_mod, "DEFAULT_CONFIG_PATH", sandbox_home)
        monkeypatch.setattr("aiwatchdogs.cli.main.detect_github_handle", lambda: (None, ""))
        res4 = cli_runner.invoke(
            cli_app,
            [
                "join",
                "--hub",
                "https://testserver",
                "--provider",
                "rule-engine",
                "--non-interactive",
                "--yes",
                "--no-run",
                "--coding-agent",
                "Codex",
                "--coding-agent-model",
                "GPT-6.2",
            ],
        )
        assert res4.exit_code == 0, res4.output
        assert "https://testserver/a/civic-codex-" in res4.stdout
