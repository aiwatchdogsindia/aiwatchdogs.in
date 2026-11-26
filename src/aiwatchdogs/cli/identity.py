"""Public local username detection (GitHub username first) and handle sanitization.

Used by ``aiwatchdogs join`` when ``--handle`` is omitted so contributors are
credited under their public GitHub username. Checks ONLY non-secret local
identity signals (explicit username env vars, ``git config``, and read-only
``gh auth status``). Never reads credential files, auth config files, or token
environment variables, and never inspects repository remote URLs (which may
belong to someone else's clone).
"""

from __future__ import annotations

import os
import re
import secrets
import subprocess
import unicodedata
from pathlib import Path

_HANDLE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,31}$")
_GITHUB_NOREPLY_RE = re.compile(
    r"^(?:[0-9]+\+)?([^@\s]+)@users\.noreply\.github\.com$",
    re.IGNORECASE,
)
_FALLBACK_HANDLE_RE = re.compile(r"^civic-(?:[a-z0-9-]+-)?[0-9a-f]{4,6}$")
_GH_AUTH_LOGGED_IN_RE = re.compile(
    r"logged\s+in\s+to\s+github\.com\s+(?:account|as)\s+([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))\b",
    re.IGNORECASE,
)
_MAX_HANDLE_LEN = 32
_MAX_CONFIG_BYTES = 131_072

# Mirrors aiwatchdogs.hub.desk.RESERVED_HANDLES so auto-detected handles never collide with reserved names.
RESERVED_HANDLES: frozenset[str] = frozenset(
    {
        "admin",
        "administrator",
        "root",
        "system",
        "hub",
        "moderator",
        "editorial-review",
        "aiwatchdogs",
        "official",
        "support",
        "security",
        "staff",
        "owner",
        "operator",
        "desk",
        "api",
    }
)

# Mirrors aiwatchdogs.hub.desk.GENERIC_AGENT_HANDLES: coding-agent / AI product names
# must go in `--coding-agent`, never as a volunteer contributor's public `--handle`.
GENERIC_AGENT_HANDLES: frozenset[str] = frozenset(
    {
        "cursor",
        "codex",
        "claude",
        "claude-code",
        "claudecode",
        "antigravity",
        "agy",
        "gemini",
        "gemini-cli",
        "copilot",
        "github-copilot",
        "windsurf",
        "opencode",
        "cline",
        "roo-code",
        "hermes",
        "hermes-agent",
        "chatgpt",
        "openai",
        "anthropic",
        "aider",
        "devin",
        "kiro",
        "amp",
        "grok",
    }
)

# Generic placeholder git user.name values commonly pre-baked into ephemeral sandboxes/containers.
_PLACEHOLDER_GIT_NAMES: frozenset[str] = frozenset(
    {
        "unknown",
        "user",
        "username",
        "developer",
        "dev",
        "sandbox",
        "container",
        "codex-sandbox",
        "github-actions",
        "runner",
        "ubuntu",
        "debian",
        "ec2-user",
        "codespace",
        "vscode",
        "agent",
        "bot",
        "ci",
    }
)

# Defense-in-depth: never accept values shaped like GitHub token prefixes as a public handle.
_TOKEN_PREFIXES: tuple[str, ...] = ("ghp_", "gho_", "ghu_", "ghs_", "ghr_", "github_pat_")

# Strictly non-secret public-username environment variables.
_GITHUB_ENV_VARS: tuple[str, ...] = (
    "AIWATCHDOGS_GITHUB_USER",
    "GITHUB_USER",
    "GITHUB_USERNAME",
    "GH_USER",
    "GITHUB_ACTOR",
)


def is_generic_agent_handle(raw: str | None) -> bool:
    """Return True if ``raw`` is a generic AI coding-agent or model product name."""
    if not raw or not isinstance(raw, str):
        return False
    cleaned = raw.strip().lstrip("@").lower()
    if cleaned in GENERIC_AGENT_HANDLES:
        return True
    slug = sanitize_handle(cleaned)
    return bool(slug and slug in GENERIC_AGENT_HANDLES)


def is_fallback_handle(raw: str | None) -> bool:
    """Return True if ``raw`` looks like an auto-generated ``civic-*`` sandbox fallback handle."""
    if not raw or not isinstance(raw, str):
        return False
    cleaned = raw.strip().lstrip("@").lower()
    return bool(_FALLBACK_HANDLE_RE.fullmatch(cleaned))


def sanitize_handle(raw: str | None) -> str | None:
    """Normalize an arbitrary username or name into a valid Hub handle (2-32 chars, lowercase + hyphens)."""
    if not raw or not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text or "[bot]" in text.lower():
        return None
    if text.startswith("@"):
        text = text.lstrip("@").strip()
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9-]+", "-", folded)
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    if len(slug) > _MAX_HANDLE_LEN:
        slug = slug[:_MAX_HANDLE_LEN].rstrip("-")
    if len(slug) < 2 or not _HANDLE_RE.fullmatch(slug) or slug in RESERVED_HANDLES:
        return None
    return slug


def _sanitize_detected_handle(raw: str | None, *, reject_placeholders: bool = False) -> str | None:
    """Sanitize a detected candidate while rejecting coding-agent names and token-like values."""
    if not raw or not isinstance(raw, str):
        return None
    stripped = raw.strip().lstrip("@")
    if stripped.lower().startswith(_TOKEN_PREFIXES):
        return None
    cleaned = sanitize_handle(stripped)
    if not cleaned or cleaned in GENERIC_AGENT_HANDLES:
        return None
    if reject_placeholders and cleaned in _PLACEHOLDER_GIT_NAMES:
        return None
    return cleaned


def extract_github_noreply_user(email: str | None) -> str | None:
    """Extract the GitHub username from a ``users.noreply.github.com`` email address."""
    if not email or not isinstance(email, str):
        return None
    match = _GITHUB_NOREPLY_RE.fullmatch(email.strip())
    if not match:
        return None
    return match.group(1)


def parse_gh_auth_status_user(output: str) -> str | None:
    """Extract the active GitHub login from ``gh auth status`` output (classic or multi-account)."""
    if not output:
        return None
    accounts: list[tuple[str, bool]] = []
    current_user: str | None = None
    current_active = False

    for line in output.splitlines():
        match = _GH_AUTH_LOGGED_IN_RE.search(line)
        if match:
            if current_user is not None:
                accounts.append((current_user, current_active))
            current_user = match.group(1).strip()
            current_active = False
            continue
        if current_user is not None and "active account:" in line.lower():
            current_active = "true" in line.lower()

    if current_user is not None:
        accounts.append((current_user, current_active))

    for user, is_active in accounts:
        if is_active:
            return user
    return accounts[0][0] if accounts else None


def _read_gh_cli_auth_user() -> str | None:
    """Run read-only ``gh auth status --hostname github.com`` and parse only the public account name.

    Never reads credential/config files directly, never inspects token env vars,
    and never requests or prints tokens.
    """
    env = {**os.environ, "GH_PROMPT_DISABLED": "1", "NO_COLOR": "1"}
    try:
        proc = subprocess.run(
            ["gh", "auth", "status", "--hostname", "github.com"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=2.5,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    combined = "\n".join(part for part in (proc.stdout, proc.stderr) if part)
    return parse_gh_auth_status_user(combined)


def parse_git_config_value(content: str, key: str) -> str:
    """Extract a ``section.option`` value from a public gitconfig file without spawning git."""
    if "." not in key:
        return ""
    target_section, target_option = key.split(".", 1)
    target_section = target_section.strip().lower()
    target_option = target_option.strip().lower()
    if not target_section or not target_option:
        return ""

    current_section = ""
    found = ""
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and "]" in line:
            header = line[1 : line.index("]")].strip()
            # Only match plain `[section]` headers (skip subsection blocks like `[includeIf "..."]`).
            if " " not in header and "\t" not in header and '"' not in header:
                current_section = header.lower()
            else:
                current_section = ""
            continue
        if current_section != target_section or "=" not in line:
            continue
        opt, val = line.split("=", 1)
        if opt.strip().lower() != target_option:
            continue
        val = val.strip()
        if val.startswith('"') and len(val) >= 2:
            end = val.find('"', 1)
            val = val[1:end] if end != -1 else val.strip('"')
        else:
            for comment_ch in ("#", ";"):
                idx = val.find(comment_ch)
                if idx != -1:
                    val = val[:idx]
            val = val.strip()
        found = val.strip()
    return found


def _git_config_candidate_paths() -> list[Path]:
    paths: list[Path] = []
    explicit_global = os.environ.get("GIT_CONFIG_GLOBAL", "").strip()
    if explicit_global:
        paths.append(Path(explicit_global).expanduser())
        return paths

    def _add(p: Path) -> None:
        if p not in paths:
            paths.append(p)

    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if xdg:
        _add(Path(xdg).expanduser() / "git" / "config")
    _add(Path.home() / ".config" / "git" / "config")
    _add(Path.home() / ".gitconfig")
    return paths


def _read_git_config_files(key: str) -> str:
    val = ""
    for path in _git_config_candidate_paths():
        try:
            if not path.is_file():
                continue
            with path.open("r", encoding="utf-8", errors="ignore") as fh:
                content = fh.read(_MAX_CONFIG_BYTES)
        except OSError:
            continue
        parsed = parse_git_config_value(content, key)
        if parsed:
            val = parsed
    return val


def _run_git_config_cmd(args: list[str]) -> str:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        proc = subprocess.run(
            ["git", "config", *args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=1.5,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if proc.returncode != 0:
        return ""
    return (proc.stdout or "").strip()


def _read_git_config(key: str) -> str:
    """Read a public git config key via ``git config --get``, ``--global --get``, or public ``.gitconfig``."""
    val = _run_git_config_cmd(["--get", key])
    if val:
        return val
    val = _run_git_config_cmd(["--global", "--get", key])
    if val:
        return val
    return _read_git_config_files(key)


def detect_github_handle() -> tuple[str | None, str]:
    """Detect the contributor's public GitHub username using ONLY non-secret local signals.

    Checks in strict priority order:
      1. Explicit non-secret username env vars: ``AIWATCHDOGS_GITHUB_USER``,
         ``GITHUB_USER``, ``GITHUB_USERNAME``, ``GH_USER``, ``GITHUB_ACTOR``
      2. ``git config`` ``github.user`` (local, ``--global``, or public ``~/.gitconfig``)
      3. ``gh auth status --hostname github.com`` (read-only status output; never reads tokens)
      4. ``git config user.email`` matching ``users.noreply.github.com``
      5. ``git config user.name`` (public author display name, slugified, excluding agent/sandbox placeholders)

    Never reads credential files, token files, or token env vars, and never inspects repo remotes.
    Returns ``(sanitized_handle, source_label)`` or ``(None, "")``.
    """
    for var_name in _GITHUB_ENV_VARS:
        raw_env = os.environ.get(var_name, "").strip()
        if raw_env:
            cleaned = _sanitize_detected_handle(raw_env)
            if cleaned:
                return cleaned, f"env {var_name}"

    gh_user = _read_git_config("github.user")
    if gh_user:
        cleaned = _sanitize_detected_handle(gh_user)
        if cleaned:
            return cleaned, "git config github.user"

    gh_auth_user = _read_gh_cli_auth_user()
    if gh_auth_user:
        cleaned = _sanitize_detected_handle(gh_auth_user)
        if cleaned:
            return cleaned, "gh auth status"

    git_email = _read_git_config("user.email")
    if git_email:
        noreply_user = extract_github_noreply_user(git_email)
        if noreply_user:
            cleaned = _sanitize_detected_handle(noreply_user)
            if cleaned:
                return cleaned, "git noreply email"

    git_name = _read_git_config("user.name")
    if git_name:
        cleaned = _sanitize_detected_handle(git_name, reject_placeholders=True)
        if cleaned:
            return cleaned, "git user.name"

    return None, ""


def fallback_handle(coding_agent: str | None = None) -> str:
    """Generate a clean fallback handle for sandboxes with no local git/gh identity."""
    suffix = secrets.token_hex(2)
    agent_slug = sanitize_handle(coding_agent) if coding_agent else None
    if agent_slug:
        # `civic-` (6) + trimmed (<=21) + `-` (1) + 4 hex (4) <= 32
        trimmed = agent_slug[:21].rstrip("-")
        if trimmed:
            return f"civic-{trimmed}-{suffix}"
    return f"civic-node-{suffix}"


def with_handle_suffix(base: str, suffix: str) -> str:
    """Append ``-<suffix>`` to ``base`` while guaranteeing a valid 2-32 char Hub handle."""
    clean_suffix = re.sub(r"[^a-z0-9]+", "", str(suffix or "").lower()) or secrets.token_hex(2)
    clean_base = sanitize_handle(base) or "civic"
    max_base_len = max(1, _MAX_HANDLE_LEN - 1 - len(clean_suffix))
    trimmed = clean_base[:max_base_len].rstrip("-_")
    if not trimmed or not trimmed[0].isalnum():
        trimmed = "civic"[:max_base_len]
    candidate = f"{trimmed}-{clean_suffix}"
    if not _HANDLE_RE.fullmatch(candidate) or candidate in RESERVED_HANDLES:
        return f"civic-{secrets.token_hex(3)}"
    return candidate
