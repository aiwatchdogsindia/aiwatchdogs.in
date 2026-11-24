"""Persistent contributor agent configuration with atomic 0600 persistence."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_HUB_URL = "https://aiwatchdogs.in"
DEFAULT_CONFIG_PATH = Path.home() / ".config" / "aiwatchdogs" / "config.json"
ENV_CONFIG_PATH = "AIWATCHDOGS_CONFIG_PATH"
DEFAULT_SPEND_LEDGER_FILENAME = "spend_ledger.json"


def normalize_hub_origin(url: str) -> str:
    """Normalize a Hub URL into a canonical origin (+ optional base path) for credential scoping."""
    raw = str(url or "").strip()
    if not raw:
        return ""
    candidate = raw if "://" in raw else f"https://{raw}"
    parsed = urlsplit(candidate)
    scheme = (parsed.scheme or "https").lower()
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return raw.rstrip("/").lower()
    try:
        port = parsed.port
    except ValueError:
        return raw.rstrip("/").lower()
    is_default_port = (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
    host_fmt = f"[{hostname}]" if (":" in hostname and not hostname.startswith("[")) else hostname
    netloc = host_fmt if (port is None or is_default_port) else f"{host_fmt}:{port}"
    path = parsed.path.rstrip("/")
    return f"{scheme}://{netloc}{path}"


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
    max_usd: float = Field(default=0.0, ge=0.0)
    daily_max_usd: float | None = Field(default=None, ge=0.0)
    allow_paid_provider: bool = False

    @property
    def normalized_hub_url(self) -> str:
        return self.hub_url.rstrip("/")

    @property
    def normalized_hub_origin(self) -> str:
        return normalize_hub_origin(self.hub_url)

    @property
    def effective_daily_max_usd(self) -> float:
        if self.daily_max_usd is not None:
            return float(self.daily_max_usd)
        return float(self.max_usd)

    @property
    def profile_url(self) -> str:
        base = self.normalized_hub_url
        return f"{base}/a/{self.handle}" if self.handle else base


def resolve_reusable_token(
    existing: AgentConfig | None,
    *,
    hub_url: str,
    handle: str,
) -> str:
    """Return a stored API token ONLY when both normalized hub origin AND handle match."""
    if existing is None:
        return ""
    token = existing.api_token.strip()
    if not token:
        return ""
    existing_origin = normalize_hub_origin(existing.hub_url)
    target_origin = normalize_hub_origin(hub_url)
    existing_handle = existing.handle.strip().lower()
    target_handle = handle.strip().lower()
    if existing_origin and existing_origin == target_origin and existing_handle and existing_handle == target_handle:
        return token
    return ""


def get_config_path(override: str | Path | None = None) -> Path:
    """Resolve the config file path, honoring explicit override or AIWATCHDOGS_CONFIG_PATH."""
    if override is not None:
        return Path(override).expanduser()
    env_override = os.environ.get(ENV_CONFIG_PATH, "").strip()
    if env_override:
        return Path(env_override).expanduser()
    return DEFAULT_CONFIG_PATH


def get_spend_ledger_path(config_path: str | Path | None = None) -> Path:
    """Resolve the daily paid-provider spend ledger file sibling to config.json."""
    return get_config_path(config_path).parent / DEFAULT_SPEND_LEDGER_FILENAME


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


def _atomic_write_text_0600(target_path: Path, payload_text: str) -> Path:
    """Write UTF-8 text atomically via sibling temp file (0600), fsync, and os.replace."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(target_path.parent, 0o700)
    except OSError:
        # Parent might be a shared temp directory owned by root/system; ignore safely.
        pass

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target_path.name}.",
        suffix=".tmp",
        dir=str(target_path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        try:
            os.fchmod(fd, 0o600)
        except (AttributeError, OSError):
            os.chmod(tmp_path, 0o600)

        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1  # Ownership transferred to file object
            handle.write(payload_text)
            handle.flush()
            os.fsync(handle.fileno())

        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, target_path)
        os.chmod(target_path, 0o600)

        # Best-effort parent directory fsync for crash durability.
        try:
            dir_fd = os.open(target_path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
    except Exception:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    return target_path


def save_config(
    config: AgentConfig | dict[str, Any],
    path: str | Path | None = None,
) -> Path:
    """Persist AgentConfig atomically as JSON with strict 0600 file permissions."""
    cfg_model = config if isinstance(config, AgentConfig) else AgentConfig.model_validate(config)
    cfg_path = get_config_path(path)
    payload = json.dumps(cfg_model.model_dump(mode="json"), indent=2, sort_keys=False) + "\n"
    return _atomic_write_text_0600(cfg_path, payload)


def _utc_today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def load_daily_spend(
    ledger_path: str | Path,
    *,
    date_utc: str | None = None,
) -> float:
    """Load cumulative recorded paid-provider USD spend for a UTC date (default today)."""
    path = Path(ledger_path)
    if not path.is_file():
        return 0.0
    day_key = date_utc or _utc_today_iso()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0.0
    if not isinstance(raw, dict):
        return 0.0
    daily = raw.get("daily")
    if isinstance(daily, dict):
        entry = daily.get(day_key)
        if isinstance(entry, dict):
            try:
                return max(0.0, float(entry.get("spent_usd", 0.0)))
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def record_daily_spend(
    ledger_path: str | Path,
    *,
    amount_usd: float,
    provider: str,
    model: str,
    date_utc: str | None = None,
) -> float:
    """Atomically add a paid-provider call's USD cost to the daily spend ledger."""
    path = Path(ledger_path)
    day_key = date_utc or _utc_today_iso()
    delta = max(0.0, float(amount_usd))

    data: dict[str, Any] = {"daily": {}}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and isinstance(loaded.get("daily"), dict):
                data = loaded
        except (OSError, json.JSONDecodeError):
            data = {"daily": {}}

    daily: dict[str, Any] = data["daily"]
    entry = daily.get(day_key)
    if not isinstance(entry, dict):
        entry = {"spent_usd": 0.0, "calls": 0, "by_provider": {}}

    prev_spent = max(0.0, float(entry.get("spent_usd", 0.0) or 0.0))
    new_spent = round(prev_spent + delta, 6)
    entry["spent_usd"] = new_spent
    entry["calls"] = int(entry.get("calls", 0) or 0) + 1
    by_provider = entry.get("by_provider")
    if not isinstance(by_provider, dict):
        by_provider = {}
    prov_key = f"{provider}/{model}"
    by_provider[prov_key] = round(max(0.0, float(by_provider.get(prov_key, 0.0) or 0.0)) + delta, 6)
    entry["by_provider"] = by_provider
    entry["updated_at"] = datetime.now(timezone.utc).isoformat()
    daily[day_key] = entry

    # Keep only the most recent 30 UTC days to prevent unbounded growth.
    if len(daily) > 30:
        for stale_day in sorted(daily.keys())[:-30]:
            daily.pop(stale_day, None)

    payload = json.dumps(data, indent=2, sort_keys=True) + "\n"
    _atomic_write_text_0600(path, payload)
    return new_spent


def mask_token(token: str) -> str:
    """Return a masked representation of an API token suitable for terminal display."""
    cleaned = token.strip()
    if not cleaned:
        return "(not registered)"
    if len(cleaned) <= 8:
        return "*" * len(cleaned)
    return f"{cleaned[:6]}...{cleaned[-4:]}"
