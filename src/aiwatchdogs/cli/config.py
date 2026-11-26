"""Persistent contributor agent configuration with atomic 0600 persistence & advisory locking.

Each handle keeps its own credential file (``~/.config/aiwatchdogs/agents/<handle>.json``) so
several agents on one machine never overwrite each other's token. The older single
``config.json`` is still read, and still used when it is the only identity on the machine.
"""

from __future__ import annotations

import json
import logging
import os
import re
import stat
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None  # type: ignore[assignment]

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger("aiwatchdogs.cli.config")

DEFAULT_HUB_URL = "https://aiwatchdogs.in"
DEFAULT_CONFIG_PATH = Path.home() / ".config" / "aiwatchdogs" / "config.json"
_INITIAL_DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_PATH
ENV_CONFIG_PATH = "AIWATCHDOGS_CONFIG_PATH"
ENV_API_TOKEN = "AIWATCHDOGS_API_TOKEN"
ENV_HANDLE = "AIWATCHDOGS_HANDLE"
AGENTS_DIRNAME = "agents"
LEGACY_NODE_FILENAME = "node.json"
_HANDLE_FILE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
DEFAULT_SPEND_LEDGER_FILENAME = "spend_ledger.json"
DEFAULT_FREE_MAX_TASKS = 0  # 0 = unlimited (drain queue task-after-task in ₹0 free mode)
DEFAULT_PAID_MAX_TASKS = 5  # conservative default cap when paid cloud LLM providers are selected

LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def normalize_hub_origin(url: str) -> str:
    """Normalize a Hub URL into a canonical origin (+ optional base path) for credential scoping."""
    raw = str(url or "").strip()
    if not raw:
        return ""
    candidate = raw if "://" in raw else f"https://{raw}"
    parsed = urlsplit(candidate)
    scheme = (parsed.scheme or "https").lower()
    hostname = (parsed.hostname or "").lower()
    if hostname == "www.aiwatchdogs.in":
        hostname = "aiwatchdogs.in"
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


def is_loopback_hub(url: str) -> bool:
    """Return True if the URL resolves to a loopback hostname."""
    try:
        parsed = urlsplit(url.strip())
    except ValueError:
        return False
    return (parsed.hostname or "").lower() in LOOPBACK_HOSTS


def validate_hub_url(url: str, *, allow_insecure_http: bool = False) -> str:
    """Validate Hub URL: require HTTPS for all non-loopback hosts unless explicitly overridden.

    Protects contributor bearer tokens (`X-Agent-Token`) from cleartext interception over HTTP.
    """
    raw = str(url or "").strip().rstrip("/")
    if not raw:
        raise ValueError("Hub URL cannot be empty.")
    if any(ord(ch) < 32 or ord(ch) == 127 or ch.isspace() for ch in raw):
        raise ValueError("Hub URL must not contain whitespace or control characters.")
    parsed = urlsplit(raw)
    scheme = (parsed.scheme or "").lower()
    if scheme not in {"http", "https"}:
        raise ValueError(f"Unsupported Hub URL scheme {scheme!r}; use https://.")
    if parsed.username or parsed.password:
        raise ValueError("Hub URL must not embed credentials.")
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError("Hub URL must specify a hostname.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"Invalid Hub URL port: {exc}") from exc
    if port is not None and not (1 <= port <= 65535):
        raise ValueError(f"Invalid Hub URL port: {port}")
    if scheme == "http" and not allow_insecure_http and host not in LOOPBACK_HOSTS:
        raise ValueError(
            f"Refusing insecure cleartext http:// Hub URL for non-loopback host {host!r}. "
            "Use https:// (or pass --allow-insecure-http only for trusted private networks)."
        )
    return raw


class AgentConfig(BaseModel):
    """Saved configuration for a local AI Watchdogs contributor node."""

    model_config = ConfigDict(extra="ignore")

    handle: str = ""
    hub_url: str = DEFAULT_HUB_URL
    api_token: str = ""
    provider: str = "rule-engine"
    model: str = "deterministic-python-v1"
    mode: str = "auto"
    max_tasks: int = Field(default=DEFAULT_FREE_MAX_TASKS, ge=0)
    max_usd: float = Field(default=0.0, ge=0.0, allow_inf_nan=False)
    daily_max_usd: float | None = Field(default=None, ge=0.0, allow_inf_nan=False)
    allow_paid_provider: bool = False
    coding_agent: str = ""
    coding_agent_model: str = ""

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
    env_tok = os.environ.get(ENV_API_TOKEN, "").strip()
    if env_tok:
        return env_tok
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


class AmbiguousConfigError(ValueError):
    """Several saved agent identities exist and no handle was given to choose between them."""

    def __init__(self, handles: list[str]) -> None:
        self.handles = handles
        super().__init__(
            "Several agents are saved on this machine ("
            + ", ".join(handles)
            + "). Pass --handle <HANDLE> (or set AIWATCHDOGS_HANDLE) to choose one."
        )


def _clean_handle(handle: str | None) -> str:
    return str(handle or "").strip().lstrip("@").lower()


def get_agents_dir() -> Path:
    """Directory holding one credential file per handle."""
    return DEFAULT_CONFIG_PATH.parent / AGENTS_DIRNAME


def get_handle_config_path(handle: str) -> Path:
    """Per-handle credential file. Rejects handles that are not safe file names."""
    cleaned = _clean_handle(handle)
    if not _HANDLE_FILE_RE.fullmatch(cleaned):
        raise ValueError(
            f"Invalid handle {handle!r}: use lowercase letters, digits, hyphens or underscores."
        )
    return get_agents_dir() / f"{cleaned}.json"


def list_saved_handles() -> list[str]:
    """Handles that have a per-handle credential file on this machine."""
    agents_dir = get_agents_dir()
    if not agents_dir.is_dir():
        return []
    return sorted(
        f.stem
        for f in agents_dir.glob("*.json")
        if f.is_file() and not f.is_symlink() and _HANDLE_FILE_RE.fullmatch(f.stem)
    )


def _peek_handle(path: Path) -> str:
    """Best-effort read of the handle stored in a config file ('' when unreadable)."""
    try:
        if path.is_symlink() or not path.is_file():
            return ""
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return _clean_handle(data.get("handle")) if isinstance(data, dict) else ""


def explicit_config_path(override: str | Path | None = None) -> Path | None:
    """The config path the operator pinned (argument or AIWATCHDOGS_CONFIG_PATH), if any."""
    if override is not None:
        return Path(override).expanduser()
    env_override = os.environ.get(ENV_CONFIG_PATH, "").strip()
    if env_override:
        return Path(env_override).expanduser()
    return None


def get_config_path(override: str | Path | None = None, *, handle: str | None = None) -> Path:
    """Resolve the config file path.

    Order: explicit override, AIWATCHDOGS_CONFIG_PATH, then the per-handle file for ``handle``
    (or AIWATCHDOGS_HANDLE). With no handle: the single saved identity, whether that is the
    legacy ``config.json`` or the only per-handle file. Raises ``AmbiguousConfigError`` when
    several identities are saved and none was chosen.
    """
    pinned = explicit_config_path(override)
    if pinned is not None:
        return pinned
    chosen = _clean_handle(handle) or _clean_handle(os.environ.get(ENV_HANDLE))
    if chosen:
        return get_handle_config_path(chosen)
    saved = list_saved_handles()
    if not saved:
        return DEFAULT_CONFIG_PATH
    legacy_handle = _peek_handle(DEFAULT_CONFIG_PATH)
    identities = sorted(set(saved) | ({legacy_handle} if legacy_handle else set()))
    if len(identities) == 1:
        return get_handle_config_path(saved[0])
    raise AmbiguousConfigError(identities)


def _load_legacy_node_config(path: Path) -> AgentConfig | None:
    """Read a legacy ``node.json`` saved by Option B / manual registration without mutating it."""
    try:
        if path.is_symlink() or not path.is_file():
            return None
        st = path.lstat()
        if stat.S_ISLNK(st.st_mode):
            return None
        if (stat.S_IMODE(st.st_mode) & 0o077) != 0:
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None

    reg = data.get("registration") if isinstance(data.get("registration"), dict) else data
    handle = _clean_handle(reg.get("handle") or data.get("handle"))
    api_token = str(reg.get("api_token") or data.get("api_token") or "").strip()
    hub_url = str(data.get("hub_url") or data.get("hub") or reg.get("hub_url") or reg.get("hub") or "").strip()
    if not hub_url:
        profile_url = str(reg.get("profile_url") or "").strip()
        if profile_url.startswith(("http://", "https://")):
            parsed = urlsplit(profile_url)
            if parsed.scheme and parsed.netloc:
                hub_url = f"{parsed.scheme}://{parsed.netloc}"
    if not handle or not api_token:
        return None
    return AgentConfig(
        handle=handle,
        hub_url=hub_url or DEFAULT_HUB_URL,
        api_token=api_token,
    )


def load_legacy_config_for_handle(handle: str) -> AgentConfig | None:
    """Return a legacy saved config (``config.json``, ``node.json``, or XDG fallback) matching ``handle``."""
    cleaned = _clean_handle(handle)
    if not cleaned:
        return None

    if _peek_handle(DEFAULT_CONFIG_PATH) == cleaned:
        try:
            return load_config(DEFAULT_CONFIG_PATH)
        except ValueError:
            pass

    node_cfg = _load_legacy_node_config(DEFAULT_CONFIG_PATH.parent / LEGACY_NODE_FILENAME)
    if node_cfg is not None and _clean_handle(node_cfg.handle) == cleaned:
        return node_cfg

    # If DEFAULT_CONFIG_PATH was not overridden by tests, also check custom XDG_CONFIG_HOME.
    if DEFAULT_CONFIG_PATH == _INITIAL_DEFAULT_CONFIG_PATH:
        xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
        if xdg:
            xdg_dir = Path(xdg).expanduser() / "aiwatchdogs"
            if xdg_dir != DEFAULT_CONFIG_PATH.parent:
                if _HANDLE_FILE_RE.fullmatch(cleaned):
                    xdg_agent = xdg_dir / AGENTS_DIRNAME / f"{cleaned}.json"
                    try:
                        loaded = load_config(xdg_agent)
                        if loaded is not None and _clean_handle(loaded.handle) == cleaned:
                            return loaded
                    except ValueError:
                        pass
                xdg_legacy = xdg_dir / "config.json"
                if _peek_handle(xdg_legacy) == cleaned:
                    try:
                        return load_config(xdg_legacy)
                    except ValueError:
                        pass
                xdg_node = _load_legacy_node_config(xdg_dir / LEGACY_NODE_FILENAME)
                if xdg_node is not None and _clean_handle(xdg_node.handle) == cleaned:
                    return xdg_node

    return None


def find_saved_config_for_prefix(base_handle: str, *, hub_url: str) -> AgentConfig | None:
    """Find an already-saved ``<base_handle>-<suffix>`` credential for ``hub_url`` to avoid handle churn."""
    cleaned = _clean_handle(base_handle)
    if not cleaned:
        return None
    prefix = f"{cleaned}-"
    matches: list[tuple[int, int, str, AgentConfig]] = []
    for saved_handle in list_saved_handles():
        if not saved_handle.startswith(prefix):
            continue
        suffix = saved_handle[len(prefix) :]
        if not suffix:
            continue
        try:
            cfg = load_config(get_handle_config_path(saved_handle))
        except ValueError:
            continue
        if cfg is None:
            continue
        if not resolve_reusable_token(cfg, hub_url=hub_url, handle=saved_handle):
            continue
        # Prefer numeric suffixes (-2, -3, ...) in ascending order over random hex suffixes.
        if suffix.isdigit():
            matches.append((0, int(suffix), saved_handle, cfg))
        else:
            matches.append((1, 0, saved_handle, cfg))
    if not matches:
        return None
    matches.sort(key=lambda item: (item[0], item[1], item[2]))
    return matches[0][3]


def get_spend_ledger_path(config_path: str | Path | None = None) -> Path:
    """Resolve the daily paid-provider spend ledger.

    It sits beside the config file, except that all per-handle files share the one ledger in
    the main config directory so the daily cap covers every agent on the machine.
    """
    cfg_path = get_config_path(config_path)
    parent = cfg_path.parent
    if parent == get_agents_dir():
        parent = parent.parent
    return parent / DEFAULT_SPEND_LEDGER_FILENAME


def config_exists(path: str | Path | None = None) -> bool:
    """Return True if a saved configuration file exists at the resolved path."""
    cfg_path = get_config_path(path)
    return cfg_path.is_file() and not cfg_path.is_symlink()


def load_config(path: str | Path | None = None) -> AgentConfig | None:
    """Load saved AgentConfig from disk, rejecting symlinks and tightening loose permissions."""
    cfg_path = get_config_path(path)
    if cfg_path.is_symlink():
        raise ValueError(f"Refusing to load config from symlink path: {cfg_path}")
    if not cfg_path.is_file():
        return None
    try:
        st = cfg_path.lstat()
        if stat.S_ISLNK(st.st_mode):
            raise ValueError(f"Refusing to load config from symlink path: {cfg_path}")
        if (stat.S_IMODE(st.st_mode) & 0o077) != 0:
            logger.warning(
                "Config file %s had loose permissions (%s); tightening to 0600.",
                cfg_path,
                oct(stat.S_IMODE(st.st_mode)),
            )
            try:
                os.chmod(cfg_path, 0o600)
            except OSError:
                pass
    except OSError:
        pass
    raw_text = cfg_path.read_text(encoding="utf-8")
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in config file {cfg_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object in config file {cfg_path}")
    cfg = AgentConfig.model_validate(data)
    env_tok = os.environ.get(ENV_API_TOKEN, "").strip()
    if env_tok and not cfg.api_token:
        cfg.api_token = env_tok
    return cfg


def _atomic_write_text_0600(target_path: Path, payload_text: str) -> Path:
    """Write UTF-8 text atomically via sibling temp file (0600), fsync, and os.replace."""
    if target_path.is_symlink():
        raise ValueError(f"Refusing to overwrite symlink config/ledger path: {target_path}")
    target_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
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


@contextmanager
def _ledger_lock(ledger_path: Path) -> Iterator[None]:
    """Acquire an exclusive advisory lock on a sibling `.lock` file to serialize concurrent CLI runs."""
    ledger_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = ledger_path.parent / f".{ledger_path.name}.lock"
    if fcntl is None:
        yield
        return
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            os.fchmod(fd, 0o600)
        except OSError:
            pass
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def load_daily_spend(
    ledger_path: str | Path,
    *,
    date_utc: str | None = None,
) -> float:
    """Load cumulative recorded paid-provider USD spend for a UTC date (default today)."""
    path = Path(ledger_path)
    if path.is_symlink() or not path.is_file():
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
                val = float(entry.get("spent_usd", 0.0))
                return max(0.0, val) if (val == val and val != float("inf")) else 0.0
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
    """Atomically add a paid-provider call's USD cost to the daily spend ledger under file lock."""
    path = Path(ledger_path)
    day_key = date_utc or _utc_today_iso()
    delta = max(0.0, float(amount_usd))

    with _ledger_lock(path):
        data: dict[str, Any] = {"daily": {}}
        if path.is_file() and not path.is_symlink():
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
