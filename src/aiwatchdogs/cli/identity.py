"""Zero-prompt local handle detection (GitHub username first) and handle sanitization.

Used by ``aiwatchdogs join`` when ``--handle`` is omitted so coding agents and
terminal users can start auditing immediately with zero handle Q&A. Strictly local
(zero network calls, zero interactive prompts) and never inspects repository remote
URLs (which may belong to someone else's cloned repository).
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
_MAX_HANDLE_LEN = 32
_MAX_GH_HOSTS_BYTES = 65_536

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


def sanitize_handle(raw: str | None) -> str | None:
    """Normalize an arbitrary username or name into a valid Hub handle (2-32 chars, lowercase + hyphens)."""
    if not raw or not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text or "[bot]" in text.lower():
        return None
    if text.startswith("@"):
        text = text[1:].strip()
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9-]+", "-", folded)
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    if len(slug) > _MAX_HANDLE_LEN:
        slug = slug[:_MAX_HANDLE_LEN].rstrip("-")
    if len(slug) < 2 or not _HANDLE_RE.fullmatch(slug) or slug in RESERVED_HANDLES:
        return None
    return slug


def extract_github_noreply_user(email: str | None) -> str | None:
    """Extract the GitHub username from a ``users.noreply.github.com`` email address."""
    if not email or not isinstance(email, str):
        return None
    match = _GITHUB_NOREPLY_RE.fullmatch(email.strip())
    if not match:
        return None
    return match.group(1)


def _strip_yaml_scalar(raw: str) -> str:
    val = raw.strip()
    if not val:
        return ""
    if val[0] in ("'", '"') and len(val) >= 2:
        quote_ch = val[0]
        end = val.find(quote_ch, 1)
        if end != -1:
            return val[1:end].strip()
        return val.strip(quote_ch).strip()
    if " #" in val:
        val = val.split(" #", 1)[0].strip()
    elif val.startswith("#"):
        return ""
    return val.strip()


def parse_gh_hosts_github_user(content: str) -> str | None:
    """Extract the active ``github.com`` username from a ``gh`` CLI ``hosts.yml`` without PyYAML."""
    in_github = False
    host_indent = 0
    child_indent: int | None = None
    in_users = False
    users_indent = 0
    fallback_from_users: str | None = None

    for raw_line in content.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        stripped = raw_line.strip()

        if not in_github:
            key = stripped.split(":", 1)[0].strip().strip("'\"") if ":" in stripped else ""
            if indent == 0 and key.lower() == "github.com":
                in_github = True
                host_indent = indent
            continue

        if indent <= host_indent:
            break

        if child_indent is None:
            child_indent = indent

        if in_users:
            if indent <= users_indent:
                in_users = False
            else:
                if fallback_from_users is None and ":" in stripped:
                    cand = stripped.split(":", 1)[0].strip().strip("'\"")
                    if cand:
                        fallback_from_users = cand
                continue

        if indent == child_indent and ":" in stripped:
            key, rest = stripped.split(":", 1)
            key_clean = key.strip().strip("'\"").lower()
            if key_clean == "user":
                user_val = _strip_yaml_scalar(rest)
                if user_val:
                    return user_val
            elif key_clean == "users":
                in_users = True
                users_indent = indent

    return fallback_from_users


def _gh_hosts_candidate_paths() -> list[Path]:
    paths: list[Path] = []
    gh_dir = os.environ.get("GH_CONFIG_DIR", "").strip()
    if gh_dir:
        paths.append(Path(gh_dir).expanduser() / "hosts.yml")
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
        if xdg:
            paths.append(Path(xdg).expanduser() / "gh" / "hosts.yml")
        default_path = Path.home() / ".config" / "gh" / "hosts.yml"
        if default_path not in paths:
            paths.append(default_path)
    return paths


def _read_gh_hosts_user() -> str | None:
    for path in _gh_hosts_candidate_paths():
        try:
            if not path.is_file():
                continue
            with path.open("r", encoding="utf-8", errors="ignore") as fh:
                content = fh.read(_MAX_GH_HOSTS_BYTES)
        except OSError:
            continue
        user = parse_gh_hosts_github_user(content)
        if user:
            return user
    return None


def _read_git_config(key: str) -> str:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        proc = subprocess.run(
            ["git", "config", "--get", key],
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


def detect_github_handle() -> tuple[str | None, str]:
    """Detect the operator's GitHub username locally with zero network calls or prompts.

    Checks in strict priority order (never touching repository remote URLs):
      1. ``git config --get github.user``
      2. ``~/.config/gh/hosts.yml`` (``github.com`` ``user:``)
      3. ``git config --get user.email`` matching ``users.noreply.github.com``
      4. ``git config --get user.name`` (slugified)

    Returns ``(sanitized_handle, source_label)`` or ``(None, "")``.
    """
    gh_user = _read_git_config("github.user")
    if gh_user:
        cleaned = sanitize_handle(gh_user)
        if cleaned:
            return cleaned, "git config github.user"

    gh_cli_user = _read_gh_hosts_user()
    if gh_cli_user:
        cleaned = sanitize_handle(gh_cli_user)
        if cleaned:
            return cleaned, "gh CLI config"

    git_email = _read_git_config("user.email")
    if git_email:
        noreply_user = extract_github_noreply_user(git_email)
        if noreply_user:
            cleaned = sanitize_handle(noreply_user)
            if cleaned:
                return cleaned, "git noreply email"

    git_name = _read_git_config("user.name")
    if git_name:
        cleaned = sanitize_handle(git_name)
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
