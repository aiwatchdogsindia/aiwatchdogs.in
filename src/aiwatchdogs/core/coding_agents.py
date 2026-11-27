"""Which AI coding agent (Claude Code, Codex, Antigravity, ...) ran a contributor node.

Self-reported at registration (``coding_agent`` / ``coding_agent_model``) and
snapshotted onto every finding and peer verification, so the public record shows
which coding agent produced each piece of work.
"""

from __future__ import annotations

import os
import re

from aiwatchdogs.core.safety import scrub_pii

_CANONICAL: dict[str, str] = {
    "claude code": "Claude Code",
    "claude-code": "Claude Code",
    "claudecode": "Claude Code",
    "codex": "Codex",
    "codex cli": "Codex",
    "openai codex": "Codex",
    "gemini cli": "Gemini CLI",
    "gemini-cli": "Gemini CLI",
    "antigravity": "Antigravity",
    "antigravity cli": "Antigravity",
    "google antigravity": "Antigravity",
    "cursor": "Cursor",
    "cursor agent": "Cursor",
    "github copilot": "GitHub Copilot",
    "copilot": "GitHub Copilot",
    "windsurf": "Windsurf",
    "aider": "Aider",
    "cline": "Cline",
    "opencode": "OpenCode",
    "roo code": "Roo Code",
    "amp": "Amp",
    "kiro": "Kiro",
    "hermes agent": "Hermes Agent",
    "hermes-agent": "Hermes Agent",
    "hermes": "Hermes Agent",
    "agy": "Antigravity",
    "chatgpt": "ChatGPT",
}
_SAFE = re.compile(r"[^\w .+()/-]")
_PLACEHOLDER_AGENTS: frozenset[str] = frozenset(
    {
        "your product name",
        "product name",
        "your agent",
        "your coding agent",
        "coding agent",
        "agent",
        "agent name",
        "rule-engine",
        "rule engine",
        "deterministic-python-v1",
        "deterministic-cpu-v1",
        "unknown",
        "none",
        "null",
        "n/a",
        "na",
        "auto",
    }
)
_PLACEHOLDER_MODELS: frozenset[str] = frozenset(
    {
        "your model",
        "model",
        "model name",
        "your model name",
        "coding agent model",
        "rule-engine",
        "rule engine",
        "deterministic-python-v1",
        "deterministic-cpu-v1",
        "unknown",
        "none",
        "null",
        "n/a",
        "na",
        "auto",
    }
)


def _strip_placeholder_brackets(raw: str) -> str:
    return raw.strip().strip("<>[]{}").strip()


def normalize_coding_agent(value: object) -> str:
    """Map common spellings to a canonical product name; keep other names, cleaned and short."""
    raw = str(value or "").strip()[:256]
    if not raw:
        return ""
    unwrapped = _strip_placeholder_brackets(raw)
    key = re.sub(r"\s+", " ", unwrapped.lower())
    if not key or key in _PLACEHOLDER_AGENTS or (raw.startswith("<") and raw.endswith(">")):
        return ""
    if key in _CANONICAL:
        return _CANONICAL[key]
    cleaned, _ = scrub_pii(_SAFE.sub("", raw))
    out = cleaned.strip()[:40]
    if re.sub(r"\s+", " ", out.lower()) in _PLACEHOLDER_AGENTS:
        return ""
    return out


def normalize_coding_agent_model(value: object) -> str:
    raw = str(value or "").strip()[:256]
    if not raw:
        return ""
    unwrapped = _strip_placeholder_brackets(raw)
    key = re.sub(r"\s+", " ", unwrapped.lower())
    if not key or key in _PLACEHOLDER_MODELS or (raw.startswith("<") and raw.endswith(">")):
        return ""
    cleaned, _ = scrub_pii(_SAFE.sub("", raw))
    out = cleaned.strip()[:60]
    if re.sub(r"\s+", " ", out.lower()) in _PLACEHOLDER_MODELS:
        return ""
    return out


def _env_truthy(name: str) -> bool:
    val = os.environ.get(name, "").strip().lower()
    return bool(val and val not in {"0", "false", "no", "off"})


def detect_coding_agent() -> str:
    """Best-effort detection from non-secret environment variables coding agents set for their shells."""
    explicit = normalize_coding_agent(os.environ.get("AIWATCHDOGS_CODING_AGENT", ""))
    if explicit:
        return explicit
    if _env_truthy("CLAUDECODE") or os.environ.get("CLAUDE_CODE_ENTRYPOINT", "").strip():
        return "Claude Code"
    if (
        os.environ.get("CODEX_THREAD_ID", "").strip()
        or os.environ.get("CODEX_SANDBOX", "").strip()
        or _env_truthy("CODEX_CI")
        or _env_truthy("CODEX_MANAGED_BY_NPM")
        or os.environ.get("CODEX_SHELL", "").strip()
    ):
        return "Codex"
    if (
        _env_truthy("ANTIGRAVITY_AGENT")
        or os.environ.get("ANTIGRAVITY_CLI_ALIAS", "").strip()
        or os.environ.get("AGY_SESSION_ID", "").strip()
    ):
        return "Antigravity"
    if _env_truthy("GEMINI_CLI"):
        return "Gemini CLI"
    if (
        _env_truthy("CURSOR_AGENT")
        or os.environ.get("CURSOR_TRACE_ID", "").strip()
        or _env_truthy("CURSOR_CLI")
    ):
        return "Cursor"
    if _env_truthy("HERMES_AGENT") or os.environ.get("HERMES_SESSION_ID", "").strip():
        return "Hermes Agent"
    if _env_truthy("OPENCODE") or os.environ.get("OPENCODE_CLIENT", "").strip():
        return "OpenCode"
    if _env_truthy("WINDSURF_SESSION") or os.environ.get("CODEIUM_EDITOR", "").strip():
        return "Windsurf"
    if _env_truthy("CLINE_ACTIVE") or os.environ.get("CLINE_TASK_ID", "").strip():
        return "Cline"
    if _env_truthy("COPILOT_CLI") or _env_truthy("GITHUB_COPILOT_CLI"):
        return "GitHub Copilot"
    if os.environ.get("AMP_THREAD_ID", "").strip() or _env_truthy("AMP_CLI"):
        return "Amp"
    if _env_truthy("KIRO_AGENT"):
        return "Kiro"
    if os.environ.get("AIDER_MODEL", "").strip():
        return "Aider"
    return ""


def detect_coding_agent_model() -> str:
    """Best-effort detection of the coding agent's model from non-secret environment variables."""
    for var_name in (
        "AIWATCHDOGS_CODING_AGENT_MODEL",
        "CODEX_MODEL",
        "ANTHROPIC_MODEL",
        "CLAUDE_MODEL",
        "GEMINI_MODEL",
        "OPENAI_MODEL",
        "OPENCODE_MODEL",
        "CURSOR_MODEL",
        "AIDER_MODEL",
    ):
        candidate = normalize_coding_agent_model(os.environ.get(var_name, ""))
        if candidate:
            return candidate
    return ""
