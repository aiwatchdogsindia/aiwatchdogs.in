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


def normalize_coding_agent(value: object) -> str:
    """Map common spellings to a canonical product name; keep other names, cleaned and short."""
    raw = str(value or "").strip()[:256]
    if not raw:
        return ""
    key = re.sub(r"\s+", " ", raw.lower())
    if key in _CANONICAL:
        return _CANONICAL[key]
    cleaned, _ = scrub_pii(_SAFE.sub("", raw))
    return cleaned.strip()[:40]


def normalize_coding_agent_model(value: object) -> str:
    raw = str(value or "").strip()[:256]
    if not raw:
        return ""
    cleaned, _ = scrub_pii(_SAFE.sub("", raw))
    return cleaned.strip()[:60]


def detect_coding_agent() -> str:
    """Best-effort detection from environment variables coding agents set for their shells."""
    if os.environ.get("CLAUDECODE") == "1":
        return "Claude Code"
    if os.environ.get("GEMINI_CLI") == "1":
        return "Gemini CLI"
    return ""
