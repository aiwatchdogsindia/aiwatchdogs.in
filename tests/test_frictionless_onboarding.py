"""Regression tests for trustworthy agent onboarding & non-secret GitHub handle auto-detection."""

from __future__ import annotations

import inspect
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from aiwatchdogs.cli import config as cfg_mod
from aiwatchdogs.cli import identity as identity_mod
from aiwatchdogs.cli.config import (
    ENV_CONFIG_PATH,
    ENV_HANDLE,
    AgentConfig,
    get_handle_config_path,
    load_config,
)
from aiwatchdogs.cli.identity import (
    GENERIC_AGENT_HANDLES,
    detect_github_handle,
    extract_github_noreply_user,
    fallback_handle,
    is_fallback_handle,
    is_generic_agent_handle,
    parse_gh_auth_status_user,
    parse_git_config_value,
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
from aiwatchdogs.hub.desk import GENERIC_AGENT_HANDLES as HUB_GENERIC_AGENT_HANDLES, HANDLE_RE, RESERVED_HANDLES
from aiwatchdogs.hub.templates import render_join_sh, render_join_txt

REPO_ROOT = Path(__file__).resolve().parents[1]
cli_runner = CliRunner()


# ---------------------------------------------------------------------------
# 1. `/join.txt` Trustworthy Quickstart, Zero Prompt-Injection & Intact Guardrails
# ---------------------------------------------------------------------------


def test_join_txt_is_trustworthy_transparent_and_preserves_guardrails() -> None:
    text = render_join_txt("https://aiwatchdogs.in")

    # Calm, transparent quickstart respecting normal agent consent
    assert "0. QUICKSTART — OPEN-SOURCE CIVIC AUDIT CLI" in text
    assert "https://github.com/aiwatchdogsindia/aiwatchdogs.in" in text
    assert "~/.config/aiwatchdogs/agents/<handle>.json" in text
    assert "Follow your normal safety and user-confirmation practices" in text
    assert "never reading tokens or credential files" in text
    assert "gh auth status" in text
    assert "Do not pass a" in text
    assert "GitHub username" in text
    assert (
        "uvx --refresh --from 'git+https://github.com/aiwatchdogsindia/aiwatchdogs.in.git' "
        "aiwatchdogs join --hub 'https://aiwatchdogs.in' --provider rule-engine "
        "--non-interactive --yes --mode auto "
        "--coding-agent '<YOUR PRODUCT NAME>' --coding-agent-model '<YOUR MODEL>'"
    ) in text
    assert "--max-tasks 3" not in text
    assert "--continuous" not in text
    assert "Runs in the foreground and completes queued tasks one after another at ₹0" in text
    assert "separate terminal or tmux session" in text

    # The Step 1 default command MUST NOT include `--handle '<HANDLE>'` inline
    step1_line = next(
        line.strip()
        for line in text.splitlines()
        if "aiwatchdogs join --hub 'https://aiwatchdogs.in' --provider rule-engine" in line
        and "--coding-agent" in line
    )
    assert "--handle" not in step1_line

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


def test_join_txt_join_sh_and_identity_have_zero_prompt_injection_or_credential_scraping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    txt = render_join_txt("https://aiwatchdogs.in")
    sh = render_join_sh("https://aiwatchdogs.in")

    # Anti-consent, prompt-injection, credential-scraping, and hidden background daemon strings MUST NEVER appear
    for forbidden in (
        "Do NOT pause",
        "already opted in",
        "zero questions",
        "zero prompts",
        "zero-prompt",
        "START IMMEDIATELY",
        "RUN RIGHT AWAY",
        "skip consent",
        "do NOT stop to show a consent screen",
        "do NOT wait for another confirmation",
        "do NOT ask for a handle",
        "never as a pre-execution gate",
        "never pause to ask",
        "~/.git-credentials",
        "git-credentials",
        "hosts.yml",
        "~/.config/gh",
        "gh api",
        "nohup",
        "agent.log",
        "pkill",
        "leave it continuously working",
        "KEEP IT RUNNING TASK AFTER TASK",
    ):
        assert forbidden.lower() not in txt.lower(), f"Found red-flag phrase in /join.txt: {forbidden!r}"

    for forbidden_sh in ("zero questions", "zero-prompt", "skip consent", "already opted in", "nohup", "hosts.yml", "git-credentials"):
        assert forbidden_sh.lower() not in sh.lower(), f"Found red-flag phrase in /join.sh: {forbidden_sh!r}"

    # identity.py must NEVER define credential-file readers or reference secret files/token env vars
    for removed_attr in (
        "parse_git_credentials_github_user",
        "_read_git_credentials_user",
        "_git_credentials_candidate_paths",
        "parse_gh_hosts_github_user",
        "_read_gh_hosts_user",
        "_gh_hosts_candidate_paths",
    ):
        assert not hasattr(identity_mod, removed_attr), f"Removed credential reader still exists: {removed_attr}"

    identity_src = inspect.getsource(identity_mod)
    for forbidden_src in (".git-credentials", "hosts.yml", "GH_TOKEN", "GITHUB_TOKEN", "gh api"):
        assert forbidden_src not in identity_src, f"Found forbidden credential reference in identity.py: {forbidden_src!r}"

    # Runtime check: even if ~/.git-credentials, ~/.config/gh/hosts.yml, GH_TOKEN, and GITHUB_TOKEN exist,
    # detect_github_handle() MUST NOT read them!
    for var in (
        "AIWATCHDOGS_GITHUB_USER",
        "GITHUB_USER",
        "GITHUB_USERNAME",
        "GH_USER",
        "GITHUB_ACTOR",
    ):
        monkeypatch.delenv(var, raising=False)
    fake_home = tmp_path / "home"
    (fake_home / ".config" / "gh").mkdir(parents=True)
    (fake_home / ".config" / "gh" / "hosts.yml").write_text("github.com:\n  user: from-hosts-yml\n  oauth_token: gho_secret\n", encoding="utf-8")
    (fake_home / ".git-credentials").write_text("https://from-creds:ghp_secret@github.com\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("GH_CONFIG_DIR", str(fake_home / ".config" / "gh"))
    monkeypatch.setenv("GH_TOKEN", "ghp_should_never_be_read")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_should_never_be_read")
    monkeypatch.setattr("aiwatchdogs.cli.identity._read_git_config", lambda key: "")
    monkeypatch.setattr("aiwatchdogs.cli.identity._read_gh_cli_auth_user", lambda: None)
    assert detect_github_handle() == (None, "")

    # Verify read-only `gh auth status --hostname github.com` output format cleanly detects piyushbodaa
    real_gh_status_output = (
        "github.com\n"
        "  ✓ Logged in to github.com account piyushbodaa (keyring)\n"
        "  - Active account: true\n"
        "  - Git operations protocol: https\n"
        "  - Token: gho_************************************\n"
    )
    assert parse_gh_auth_status_user(real_gh_status_output) == "piyushbodaa"


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
    assert "--refresh --from" in headless.stdout
    assert "aiwatchdogs join --hub https://aiwatchdogs.in --non-interactive --yes --max-tasks 2" in headless.stdout
    assert "--max-tasks 0" not in headless.stdout

    # Headless default (no --max-tasks flag) passes --max-tasks 0 to drain tasks continuously!
    headless_default = subprocess.run(
        ["sh", str(script_path)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        start_new_session=True,
        env={"PATH": f"{bin_dir}:/usr/bin:/bin"},
        check=False,
    )
    assert headless_default.returncode == 0, headless_default.stderr
    assert "aiwatchdogs join --hub https://aiwatchdogs.in --non-interactive --yes --max-tasks 0" in headless_default.stdout

    # Headless paid-provider invocation does NOT force --max-tasks 0 (preserves CLI's paid cap!)
    headless_paid = subprocess.run(
        ["sh", str(script_path), "--provider", "openai", "--allow-paid-provider", "--max-usd", "1.0"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        start_new_session=True,
        env={"PATH": f"{bin_dir}:/usr/bin:/bin"},
        check=False,
    )
    assert headless_paid.returncode == 0, headless_paid.stderr
    assert "--max-tasks 0" not in headless_paid.stdout
    assert "--provider openai --allow-paid-provider --max-usd 1.0" in headless_paid.stdout

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
        assert "Quickstart and CLI / HTTP Desk protocol" in llms
        assert "auto-detects your public GitHub username" in llms
        assert "zero-prompt" not in llms.lower()
        assert "zero questions" not in llms.lower()
        assert "skip consent" not in llms.lower()

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


def test_extract_github_noreply_user() -> None:
    assert extract_github_noreply_user("12345678+octocat@users.noreply.github.com") == "octocat"
    assert extract_github_noreply_user("Octo-Dev@users.noreply.github.com") == "Octo-Dev"
    assert extract_github_noreply_user("person@example.com") is None


def test_detect_github_handle_priority_order_and_sandbox_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for var in (
        "AIWATCHDOGS_GITHUB_USER",
        "GITHUB_USER",
        "GITHUB_USERNAME",
        "GH_USER",
        "GITHUB_ACTOR",
    ):
        monkeypatch.delenv(var, raising=False)

    git_values: dict[str, str] = {
        "github.user": "FromGitHubUser",
        "user.email": "999+from-noreply@users.noreply.github.com",
        "user.name": "From Git Name",
    }
    gh_auth_value: list[str | None] = ["from-gh-status"]
    monkeypatch.setattr("aiwatchdogs.cli.identity._read_git_config", lambda key: git_values.get(key, ""))
    monkeypatch.setattr("aiwatchdogs.cli.identity._read_gh_cli_auth_user", lambda: gh_auth_value[0])

    # 1. Explicit non-secret env var wins over everything
    monkeypatch.setenv("GITHUB_USER", "FromEnvUser")
    assert detect_github_handle() == ("fromenvuser", "env GITHUB_USER")
    monkeypatch.delenv("GITHUB_USER")

    # 2. git config github.user wins next
    assert detect_github_handle() == ("fromgithubuser", "git config github.user")

    # 3. gh auth status wins when github.user is absent
    git_values.pop("github.user")
    assert detect_github_handle() == ("from-gh-status", "gh auth status")

    # 4. GitHub noreply email wins when gh auth status is absent
    gh_auth_value[0] = None
    assert detect_github_handle() == ("from-noreply", "git noreply email")

    # 5. Regular non-GitHub email falls through to slugified user.name
    git_values["user.email"] = "someone@example.com"
    assert detect_github_handle() == ("from-git-name", "git user.name")

    # 6. Clean sandbox (no git/gh identity) -> returns (None, "") and uses fallback_handle
    git_values.clear()
    assert detect_github_handle() == (None, "")

    fb_codex = fallback_handle("Codex")
    assert fb_codex.startswith("civic-codex-") and HANDLE_RE.fullmatch(fb_codex)
    fb_plain = fallback_handle(None)
    assert fb_plain.startswith("civic-node-") and HANDLE_RE.fullmatch(fb_plain)

    # Suffix helper always keeps <=32 chars valid handle
    suffixed = with_handle_suffix("a" * 32, "a1b2")
    assert len(suffixed) <= 32 and suffixed.endswith("-a1b2") and HANDLE_RE.fullmatch(suffixed)


def test_detect_github_handle_env_gh_auth_direct_gitconfig_and_agent_filtering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert GENERIC_AGENT_HANDLES == HUB_GENERIC_AGENT_HANDLES
    for var in (
        "AIWATCHDOGS_GITHUB_USER",
        "GITHUB_USER",
        "GITHUB_USERNAME",
        "GH_USER",
        "GITHUB_ACTOR",
    ):
        monkeypatch.delenv(var, raising=False)

    # 1. Explicit env var wins over everything, while rejecting generic coding-agent names
    monkeypatch.setattr("aiwatchdogs.cli.identity._read_git_config", lambda key: "")
    monkeypatch.setattr("aiwatchdogs.cli.identity._read_gh_cli_auth_user", lambda: None)

    monkeypatch.setenv("GITHUB_ACTOR", "cursor")
    assert detect_github_handle() == (None, "")
    monkeypatch.setenv("GITHUB_ACTOR", "RealActor99")
    assert detect_github_handle() == ("realactor99", "env GITHUB_ACTOR")
    monkeypatch.setenv("AIWATCHDOGS_GITHUB_USER", "@PiyushBodaa")
    assert detect_github_handle() == ("piyushbodaa", "env AIWATCHDOGS_GITHUB_USER")
    monkeypatch.delenv("AIWATCHDOGS_GITHUB_USER")
    monkeypatch.delenv("GITHUB_ACTOR")

    # 2. Modern gh auth status parser (classic + multi-account active selection)
    modern_auth = """
github.com
  ✓ Logged in to github.com account old-account (keyring)
  - Active account: false
  ✓ Logged in to github.com account PiyushBodaa (keyring)
  - Active account: true
"""
    assert parse_gh_auth_status_user(modern_auth) == "PiyushBodaa"
    assert parse_gh_auth_status_user("✓ Logged in to github.com as classic-dev (oauth)") == "classic-dev"

    monkeypatch.setattr("aiwatchdogs.cli.identity._read_gh_cli_auth_user", lambda: "PiyushBodaa")
    assert detect_github_handle() == ("piyushbodaa", "gh auth status")
    monkeypatch.setattr("aiwatchdogs.cli.identity._read_gh_cli_auth_user", lambda: None)

    # 3. Direct gitconfig parsing works when git subprocess is unavailable/blocked
    gitconfig_text = """
[user]
    name = Piyush Bodaa
    email = piyushbodaa@gmail.com
[github]
    user = "PiyushBodaa"
"""
    assert parse_git_config_value(gitconfig_text, "github.user") == "PiyushBodaa"
    assert parse_git_config_value(gitconfig_text, "user.name") == "Piyush Bodaa"

    # 4. Generic agent names and sandbox placeholders in git user.name are rejected
    for bad_name in ("Cursor", "Codex", "Claude Code", "Antigravity", "sandbox", "unknown", "github-actions"):
        monkeypatch.setattr(
            "aiwatchdogs.cli.identity._read_git_config",
            lambda key, v=bad_name: v if key == "user.name" else "",
        )
        assert detect_github_handle() == (None, ""), f"Should ignore placeholder/agent name {bad_name!r}"

    # 5. Helper classifiers
    assert is_generic_agent_handle("Cursor") and is_generic_agent_handle("@claude-code")
    assert not is_generic_agent_handle("piyushbodaa")
    assert is_fallback_handle("civic-codex-b519") and is_fallback_handle("civic-node-a1b2")
    assert not is_fallback_handle("piyushbodaa")


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
        monkeypatch.setattr("aiwatchdogs.cli.main.detect_github_handle", lambda: ("octocat", "gh auth status"))
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

        # Case C: Second machine detects the same GitHub handle (`octocat`) with NO local token ->
        # CLI MUST NOT auto-mint `octocat-2` or `octocat-3`; exits cleanly with actionable reuse steps!
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
        assert res3.exit_code == 1, res3.output
        assert "already registered" in res3.output.lower()
        assert "octocat-2" not in [c["handle"] for c in asgi_client.get("/api/contributors").json()["contributors"]]
        assert "AIWATCHDOGS_API_TOKEN" in res3.output
        assert "agents/octocat.json" in res3.output
        assert not get_handle_config_path("octocat-2").exists()
        assert not get_handle_config_path("octocat").exists()

        # Passing AIWATCHDOGS_API_TOKEN on the second machine reconnects directly as @octocat (1 profile!)
        monkeypatch.setenv("AIWATCHDOGS_API_TOKEN", saved1.api_token)
        res3_tok = cli_runner.invoke(
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
        monkeypatch.delenv("AIWATCHDOGS_API_TOKEN", raising=False)
        assert res3_tok.exit_code == 0, res3_tok.output
        assert "https://testserver/a/octocat" in res3_tok.stdout
        assert [c["handle"] for c in asgi_client.get("/api/contributors").json()["contributors"]] == ["octocat"]

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
        assert "Could not detect a local GitHub username" in res4.stdout


def test_hub_and_cli_block_generic_agent_handles_and_prevent_identity_fragmentation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    monkeypatch.delenv(ENV_CONFIG_PATH, raising=False)
    monkeypatch.delenv(ENV_HANDLE, raising=False)
    legacy_cfg = tmp_path / "aiwatchdogs" / "config.json"
    monkeypatch.setattr(cfg_mod, "DEFAULT_CONFIG_PATH", legacy_cfg)

    hub_app = create_app(db_path=tmp_path / "identity_guard.db", seed=True)
    with TestClient(hub_app, base_url="https://aiwatchdogs.in") as asgi_client:
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

        # 1. Hub rejects generic coding-agent product names as contributor handles (HTTP 422)
        for bad_handle in ("cursor", "codex", "claude", "claude-code", "antigravity", "gemini", "windsurf"):
            resp = asgi_client.post(
                "/api/agents/register",
                json={"handle": bad_handle, "provider": "rule-engine", "model": "deterministic-python-v1"},
            )
            assert resp.status_code == 422, f"Expected 422 for {bad_handle}, got {resp.status_code}"
            assert "coding_agent" in resp.json()["detail"]

        # 2. Pre-register @piyushbodaa on the Hub and save its credentials ONLY in legacy node.json
        #    (with www.aiwatchdogs.in origin!) plus a stray agents/piyushbodaa-2.json on disk.
        reg0 = asgi_client.post(
            "/api/agents/register",
            json={"handle": "piyushbodaa", "provider": "antigravity", "model": "Gemini"},
        )
        assert reg0.status_code == 200
        orig_token = reg0.json()["api_token"]

        node_json = legacy_cfg.parent / "node.json"
        node_json.parent.mkdir(parents=True, exist_ok=True)
        node_json.write_text(
            json.dumps(
                {
                    "hub": "https://www.aiwatchdogs.in",
                    "registration": {
                        "agent_id": reg0.json()["agent_id"],
                        "handle": "piyushbodaa",
                        "api_token": orig_token,
                        "profile_url": "https://aiwatchdogs.in/a/piyushbodaa",
                    },
                }
            ),
            encoding="utf-8",
        )
        # Simulate the user previously ending up with a stale piyushbodaa-2.json
        cfg_mod.save_config(
            AgentConfig(
                handle="piyushbodaa-2",
                hub_url="https://aiwatchdogs.in",
                api_token="awd_stale_dash2_tok",
            ),
            path=get_handle_config_path("piyushbodaa-2"),
        )

        # Running join without --handle MUST recover @piyushbodaa from node.json instead of sticking to piyushbodaa-2!
        monkeypatch.setattr("aiwatchdogs.cli.main.detect_github_handle", lambda: ("piyushbodaa", "gh auth status"))
        res_recover = cli_runner.invoke(
            cli_app,
            [
                "join",
                "--hub",
                "https://aiwatchdogs.in",
                "--provider",
                "rule-engine",
                "--non-interactive",
                "--yes",
                "--no-run",
                "--coding-agent",
                "Claude Code",
            ],
        )
        assert res_recover.exit_code == 0, res_recover.output
        assert "https://aiwatchdogs.in/a/piyushbodaa" in res_recover.stdout
        assert "piyushbodaa-2" not in res_recover.stdout
        saved_main = load_config(get_handle_config_path("piyushbodaa"))
        assert saved_main is not None and saved_main.api_token == orig_token

        # 3. Mistakenly passing `--handle cursor` when GitHub username is detectable overrides to @piyushbodaa!
        res_cursor_fix = cli_runner.invoke(
            cli_app,
            [
                "join",
                "--hub",
                "https://aiwatchdogs.in",
                "--handle",
                "cursor",
                "--provider",
                "rule-engine",
                "--non-interactive",
                "--yes",
                "--no-run",
            ],
        )
        assert res_cursor_fix.exit_code == 0, res_cursor_fix.output
        assert "Ignoring coding-agent product name 'cursor'" in res_cursor_fix.stdout
        assert "https://aiwatchdogs.in/a/piyushbodaa" in res_cursor_fix.stdout

        # 4. Passing `--handle @piyushbodaa` (leading @) works seamlessly
        res_at = cli_runner.invoke(
            cli_app,
            [
                "join",
                "--hub",
                "https://aiwatchdogs.in",
                "--handle",
                "@piyushbodaa",
                "--provider",
                "rule-engine",
                "--non-interactive",
                "--yes",
                "--no-run",
            ],
        )
        assert res_at.exit_code == 0, res_at.output
        assert "https://aiwatchdogs.in/a/piyushbodaa" in res_at.stdout

        # 5. Passing `--handle cursor` in an isolated sandbox (no GitHub handle detected) is refused (exit 2)
        monkeypatch.setattr("aiwatchdogs.cli.main.detect_github_handle", lambda: (None, ""))
        res_cursor_refuse = cli_runner.invoke(
            cli_app,
            [
                "join",
                "--hub",
                "https://aiwatchdogs.in",
                "--handle",
                "cursor",
                "--provider",
                "rule-engine",
                "--non-interactive",
                "--yes",
                "--no-run",
            ],
        )
        assert res_cursor_refuse.exit_code == 2
        assert "coding-agent product name" in res_cursor_refuse.output

        # 6. Second machine (no token for @piyushbodaa) NEVER auto-mints piyushbodaa-2!
        #    Exits cleanly with actionable instructions to copy agents/piyushbodaa.json or set AIWATCHDOGS_API_TOKEN.
        second_machine_cfg = tmp_path / "machine2" / "config.json"
        monkeypatch.setattr(cfg_mod, "DEFAULT_CONFIG_PATH", second_machine_cfg)
        monkeypatch.setattr("aiwatchdogs.cli.main.detect_github_handle", lambda: ("piyushbodaa", "gh auth status"))

        r_m2_first = cli_runner.invoke(
            cli_app,
            [
                "join",
                "--hub",
                "https://aiwatchdogs.in",
                "--provider",
                "rule-engine",
                "--non-interactive",
                "--yes",
                "--no-run",
            ],
        )
        assert r_m2_first.exit_code == 1, r_m2_first.output
        assert "one github username = one public profile" in r_m2_first.output.lower()
        assert "AIWATCHDOGS_API_TOKEN" in r_m2_first.output
        assert "agents/piyushbodaa.json" in r_m2_first.output
        assert not get_handle_config_path("piyushbodaa-2").exists()
        assert [c["handle"] for c in asgi_client.get("/api/contributors").json()["contributors"]] == ["piyushbodaa"]

        # With AIWATCHDOGS_API_TOKEN provided, the second machine joins cleanly AS @piyushbodaa!
        monkeypatch.setenv("AIWATCHDOGS_API_TOKEN", orig_token)
        r_m2_second = cli_runner.invoke(
            cli_app,
            [
                "join",
                "--hub",
                "https://aiwatchdogs.in",
                "--provider",
                "rule-engine",
                "--non-interactive",
                "--yes",
                "--no-run",
            ],
        )
        monkeypatch.delenv("AIWATCHDOGS_API_TOKEN", raising=False)
        assert r_m2_second.exit_code == 0, r_m2_second.output
        assert "https://aiwatchdogs.in/a/piyushbodaa" in r_m2_second.stdout
        assert "piyushbodaa-2" not in r_m2_second.stdout
        m2_saved_after = load_config(get_handle_config_path("piyushbodaa"))
        assert m2_saved_after is not None and m2_saved_after.api_token == orig_token
        assert [c["handle"] for c in asgi_client.get("/api/contributors").json()["contributors"]] == ["piyushbodaa"]

        # 7. Upgrading from an earlier civic-codex-xxxx fallback once GitHub handle is configured!
        upgrade_cfg = tmp_path / "upgrade_machine" / "config.json"
        monkeypatch.setattr(cfg_mod, "DEFAULT_CONFIG_PATH", upgrade_cfg)
        cfg_mod.save_config(
            AgentConfig(
                handle="civic-codex-beef",
                hub_url="https://aiwatchdogs.in",
                api_token="awd_old_fallback_tok",
            ),
            path=get_handle_config_path("civic-codex-beef"),
        )
        monkeypatch.setattr("aiwatchdogs.cli.main.detect_github_handle", lambda: ("newdev", "gh auth status"))
        r_upgrade = cli_runner.invoke(
            cli_app,
            [
                "join",
                "--hub",
                "https://aiwatchdogs.in",
                "--provider",
                "rule-engine",
                "--non-interactive",
                "--yes",
                "--no-run",
            ],
        )
        assert r_upgrade.exit_code == 0, r_upgrade.output
        assert "https://aiwatchdogs.in/a/newdev" in r_upgrade.stdout
        assert "civic-codex-beef" not in r_upgrade.stdout


# ---------------------------------------------------------------------------
# 6. Cross-Agent Resilience: Top-of-file Quickstart, Fallbacks, Detection, Sandbox Config
# ---------------------------------------------------------------------------


def test_join_txt_places_command_near_top_with_non_uvx_fallbacks_and_timeout_guidance() -> None:
    text = render_join_txt("https://aiwatchdogs.in")
    lines = text.splitlines()

    # Exact CLI command MUST appear in the first 25 lines so output truncation in any AI agent never hides it!
    cmd_line_idx = next(
        idx
        for idx, line in enumerate(lines, start=1)
        if "uvx --refresh --from 'git+https://github.com/aiwatchdogsindia/aiwatchdogs.in.git' aiwatchdogs join" in line
    )
    assert cmd_line_idx <= 25, f"Step 1 command slipped down to line {cmd_line_idx}"
    # Keep total /join.txt compact enough for WebFetch / CLI tool buffers
    assert len(lines) <= 220, f"/join.txt grew too long ({len(lines)} lines)"

    # Non-uvx, non-git, and sandbox fallbacks must be right next to Step 1
    top_60 = "\n".join(lines[:60])
    assert "$HOME/.local/bin" in top_60
    assert "pipx run" in top_60
    assert "python3 -m venv" in top_60
    assert "main.tar.gz" in top_60
    assert "UV_CACHE_DIR" in top_60
    assert "run_in_background" in top_60
    assert "--max-tasks 5" in top_60


def test_join_sh_discovers_user_local_bin_and_falls_back_to_pipx(tmp_path: Path) -> None:
    sh_text = render_join_sh("https://aiwatchdogs.in")
    script_path = tmp_path / "join.sh"
    script_path.write_text(sh_text, encoding="utf-8")

    # 1. uv/uvx installed only in $HOME/.local/bin (common in non-login AI agent shells!)
    fake_home = tmp_path / "home"
    local_bin = fake_home / ".local" / "bin"
    local_bin.mkdir(parents=True)
    for name in ("uv", "uvx"):
        stub = local_bin / name
        stub.write_text(f"#!/usr/bin/env sh\nprintf 'LOCAL_BIN:%s:%s\\n' '{name}' \"$*\"\n", encoding="utf-8")
        stub.chmod(0o755)
    # Minimal PATH without $HOME/.local/bin
    res_local = subprocess.run(
        ["sh", str(script_path), "--coding-agent", "Codex"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        start_new_session=True,
        env={"PATH": "/usr/bin:/bin", "HOME": str(fake_home)},
        check=False,
    )
    assert res_local.returncode == 0, res_local.stderr
    assert "LOCAL_BIN:uvx:" in res_local.stdout
    assert "--coding-agent Codex" in res_local.stdout

    # 2. Neither uv nor uvx installed, only pipx present -> falls back to pipx cleanly!
    #    Use an isolated PATH without system /usr/bin (where real uv may live) so only stubbed pipx is visible.
    pipx_dir = tmp_path / "pipx_bin"
    pipx_dir.mkdir()
    pipx_stub = pipx_dir / "pipx"
    pipx_stub.write_text("#!/bin/sh\nprintf 'PIPX_FALLBACK:%s\\n' \"$*\"\n", encoding="utf-8")
    pipx_stub.chmod(0o755)
    res_pipx = subprocess.run(
        ["/bin/sh", str(script_path), "--max-tasks", "5"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        start_new_session=True,
        env={"PATH": str(pipx_dir)},
        check=False,
    )
    assert res_pipx.returncode == 0, res_pipx.stderr
    assert "PIPX_FALLBACK:run --spec" in res_pipx.stdout
    assert "main.tar.gz" in res_pipx.stdout  # no git in isolated PATH -> tarball fallback!
    assert "aiwatchdogs join --hub https://aiwatchdogs.in --non-interactive --yes --max-tasks 5" in res_pipx.stdout


def test_coding_agent_detection_placeholder_rejection_and_no_stale_model_carryover(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aiwatchdogs.core.coding_agents import (
        detect_coding_agent,
        detect_coding_agent_model,
        normalize_coding_agent,
        normalize_coding_agent_model,
    )

    # Literal template placeholders & rule-engine names are ignored cleanly
    for bogus in ("<YOUR PRODUCT NAME>", "YOUR PRODUCT NAME", "rule-engine", "deterministic-python-v1", "unknown"):
        assert normalize_coding_agent(bogus) == ""
    for bogus_m in ("<YOUR MODEL>", "YOUR MODEL", "rule-engine", "deterministic-python-v1", "n/a"):
        assert normalize_coding_agent_model(bogus_m) == ""

    # Clear all detection env vars before testing each harness
    all_vars = (
        "AIWATCHDOGS_CODING_AGENT",
        "AIWATCHDOGS_CODING_AGENT_MODEL",
        "CLAUDECODE",
        "CLAUDE_CODE_ENTRYPOINT",
        "CODEX_THREAD_ID",
        "CODEX_SANDBOX",
        "CODEX_CI",
        "CODEX_MANAGED_BY_NPM",
        "CODEX_SHELL",
        "CODEX_MODEL",
        "ANTIGRAVITY_AGENT",
        "ANTIGRAVITY_CLI_ALIAS",
        "AGY_SESSION_ID",
        "GEMINI_CLI",
        "GEMINI_MODEL",
        "CURSOR_AGENT",
        "CURSOR_TRACE_ID",
        "CURSOR_CLI",
        "CURSOR_MODEL",
        "HERMES_AGENT",
        "HERMES_SESSION_ID",
        "OPENCODE",
        "OPENCODE_CLIENT",
        "OPENCODE_MODEL",
        "WINDSURF_SESSION",
        "CODEIUM_EDITOR",
        "CLINE_ACTIVE",
        "CLINE_TASK_ID",
        "COPILOT_CLI",
        "GITHUB_COPILOT_CLI",
        "AMP_THREAD_ID",
        "AMP_CLI",
        "KIRO_AGENT",
        "AIDER_MODEL",
        "ANTHROPIC_MODEL",
        "CLAUDE_MODEL",
        "OPENAI_MODEL",
    )
    for var in all_vars:
        monkeypatch.delenv(var, raising=False)

    assert detect_coding_agent() == ""
    assert detect_coding_agent_model() == ""

    for env_var, env_val, expected in (
        ("CLAUDECODE", "1", "Claude Code"),
        ("CODEX_THREAD_ID", "thr_123", "Codex"),
        ("ANTIGRAVITY_AGENT", "1", "Antigravity"),
        ("GEMINI_CLI", "1", "Gemini CLI"),
        ("CURSOR_AGENT", "1", "Cursor"),
        ("HERMES_AGENT", "1", "Hermes Agent"),
        ("OPENCODE", "1", "OpenCode"),
        ("WINDSURF_SESSION", "ws_1", "Windsurf"),
        ("CLINE_ACTIVE", "1", "Cline"),
        ("COPILOT_CLI", "1", "GitHub Copilot"),
        ("AIDER_MODEL", "claude-sonnet", "Aider"),
    ):
        monkeypatch.setenv(env_var, env_val)
        assert detect_coding_agent() == expected
        monkeypatch.delenv(env_var, raising=False)

    monkeypatch.setenv("CODEX_MODEL", "gpt-6")
    assert detect_coding_agent_model() == "gpt-6"
    monkeypatch.delenv("CODEX_MODEL", raising=False)

    # Switching harnesses on `join` must NOT carry over stale coding_agent_model from previous harness!
    monkeypatch.delenv(ENV_CONFIG_PATH, raising=False)
    monkeypatch.delenv(ENV_HANDLE, raising=False)
    monkeypatch.setattr(cfg_mod, "DEFAULT_CONFIG_PATH", tmp_path / "switch" / "config.json")
    monkeypatch.setattr("aiwatchdogs.cli.main.detect_github_handle", lambda: ("switchdev", "gh auth status"))
    monkeypatch.setattr(
        "aiwatchdogs.cli.main.detect_providers",
        lambda **kw: [
            ProviderInfo(provider="rule-engine", model="deterministic-python-v1", label="CPU", is_local=True, zero_cost=True)
        ],
    )
    hub_app = create_app(db_path=tmp_path / "switch.db", seed=True)
    with TestClient(hub_app, base_url="https://aiwatchdogs.in") as asgi_client:
        orig_init = AgentRunner.__init__

        def _patched_init(self: AgentRunner, config: AgentConfig, **kwargs: Any) -> None:
            kwargs.setdefault("http_client", asgi_client)
            orig_init(self, config, **kwargs)

        monkeypatch.setattr(AgentRunner, "__init__", _patched_init)

        r1 = cli_runner.invoke(
            cli_app,
            [
                "join",
                "--hub",
                "https://aiwatchdogs.in",
                "--provider",
                "rule-engine",
                "--non-interactive",
                "--yes",
                "--no-run",
                "--coding-agent",
                "Codex",
                "--coding-agent-model",
                "GPT-6",
            ],
        )
        assert r1.exit_code == 0, r1.output
        saved1 = load_config(get_handle_config_path("switchdev"))
        assert saved1 is not None and saved1.coding_agent == "Codex" and saved1.coding_agent_model == "GPT-6"

        # Now run in Claude Code without passing --coding-agent-model: must NOT stay paired with "GPT-6"!
        r2 = cli_runner.invoke(
            cli_app,
            [
                "join",
                "--hub",
                "https://aiwatchdogs.in",
                "--provider",
                "rule-engine",
                "--non-interactive",
                "--yes",
                "--no-run",
                "--coding-agent",
                "Claude Code",
            ],
        )
        assert r2.exit_code == 0, r2.output
        saved2 = load_config(get_handle_config_path("switchdev"))
        assert saved2 is not None and saved2.coding_agent == "Claude Code" and saved2.coding_agent_model == ""


def test_sandbox_readonly_config_falls_back_to_temp_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import errno

    monkeypatch.delenv(ENV_CONFIG_PATH, raising=False)
    fallback_dir = tmp_path / "tmp_fallback" / "agents"
    monkeypatch.setattr(cfg_mod, "_fallback_temp_agents_dir", lambda: fallback_dir)

    orig_save = cfg_mod.save_config

    def _ro_save(config: Any, path: Any = None) -> Path:
        raise PermissionError(errno.EACCES, "Read-only ~/.config in sandbox")

    monkeypatch.setattr(cfg_mod, "save_config", _ro_save)
    cfg = AgentConfig(handle="sandboxdev", hub_url="https://aiwatchdogs.in", api_token="awd_sandbox_tok")
    saved_path, used_fallback = cfg_mod.save_config_with_fallback(cfg, path=tmp_path / "ro" / "sandboxdev.json")
    assert used_fallback is True
    assert saved_path == fallback_dir / "sandboxdev.json"
    assert saved_path.is_file()
    assert (saved_path.lstat().st_mode & 0o777) == 0o600
    monkeypatch.setattr(cfg_mod, "save_config", orig_save)

    recovered = cfg_mod._load_temp_fallback_for_handle("sandboxdev")
    assert recovered is not None and recovered.api_token == "awd_sandbox_tok"


def test_frontend_onboarding_copy_has_zero_prompt_injection_phrasing() -> None:
    for rel in (
        "web/src/components/EyebrowBar.tsx",
        "web/src/components/JoinModal.tsx",
        "web/src/pages/BriefPage.tsx",
        "web/src/pages/HowItWorksPage.tsx",
    ):
        src = (REPO_ROOT / rel).read_text(encoding="utf-8").lower()
        assert "zero questions" not in src, f"Stale 'zero questions' phrase found in {rel}"
        assert "zero setup questions" not in src, f"Stale 'zero setup questions' phrase found in {rel}"
