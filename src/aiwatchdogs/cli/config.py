"""Persistent contributor agent configuration with strict 0600 permissions."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_HUB_URL = "https://aiwatchdogs.in"
DEFAULT_CONFIG_PATH = Path.home() / ".config" / "aiwatchdogs" / "config.json"
ENV_CONFIG_PATH = "AIWATCHDOGS_CONFIG_PATH"


class AgentConfig(BaseModel):
    """Saved configuration for a local AI Watchdogs contributor node."""

    model_config = ConfigDict(extra="allow")

    handle: str = ""
    hub_url: str = DEFAULT_HUB_URL
    api_token: str = ""
    provider: str = "rule-engine"
    model: str = "deterministic-python-v1"
    mode: str = "auto"
    max_tasks: int = Field(default=5, ge=1)
    max_usd: float = Field(default=1.0, ge=0.0)

    @property
    def normalized_hub_url(self) -> str:
        return self.hub_url.rstrip("/")

    @property
    def profile_url(self) -> str:
        base = self.normalized_hub_url
        return f"{base}/a/{self.handle}" if self.handle else base


def get_config_path(override: str | Path | None = None) -> Path:
    """Resolve the config file path, honoring explicit override or AIWATCHDOGS_CONFIG_PATH."""
    if override is not None:
        return Path(override).expanduser()
    env_override = os.environ.get(ENV_CONFIG_PATH, "").strip()
    if env_override:
        return Path(env_override).expanduser()
    return DEFAULT_CONFIG_PATH


def config_exists(path: str | Path | None = None) -> bool:
    """Return True if a saved configuration file exists at the resolved path."""
    return get_config_path(path).is_file()


def load_config(path: str | Path | None = None) -> AgentConfig | None:
    """Load saved AgentConfig from disk, or return None if not yet created."""
    cfg_path = get_config_path(path)
    if not cfg_path.is_file():
        return None
    raw_text = cfg_path.read_text(encoding="utf-8")
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in config file {cfg_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object in config file {cfg_path}")
    return AgentConfig.model_validate(data)


def save_config(
    config: AgentConfig | dict[str, Any],
    path: str | Path | None = None,
) -> Path:
    """Persist AgentConfig as JSON with strict 0600 file permissions."""
    cfg_model = config if isinstance(config, AgentConfig) else AgentConfig.model_validate(config)
    cfg_path = get_config_path(path)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(cfg_path.parent, 0o700)
    except OSError:
        # Parent might be a shared temp directory owned by root/system; ignore safely.
        pass

    payload = json.dumps(cfg_model.model_dump(mode="json"), indent=2, sort_keys=False) + "\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(cfg_path, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    os.chmod(cfg_path, 0o600)
    return cfg_path


def mask_token(token: str) -> str:
    """Return a masked representation of an API token suitable for terminal display."""
    cleaned = token.strip()
    if not cleaned:
        return "(not registered)"
    if len(cleaned) <= 8:
        return "*" * len(cleaned)
    return f"{cleaned[:6]}...{cleaned[-4:]}"
