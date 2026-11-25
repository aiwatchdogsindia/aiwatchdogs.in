"""Token estimates and reported compute usage for audit tasks.

A token count always means every token processed: fresh input + cached input + output.
Usage has two independent layers: ``engine`` (measured by the official CLI while it ran the
deterministic check) and ``agent`` (self-reported by the coding agent that ran the session).
Anything not reported stays ``None``; it is never shown as zero.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_TOKENS = 1_000_000_000_000
WORK_TYPES = ("find_sources", "extract_data", "run_check", "cross_check", "summarise")
DEFAULT_WORK_TYPE = "run_check"
PEER_ESTIMATE_BASIS = "Same as the check it repeats"
_TOKEN_FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens")
_COUNT_FIELDS = (*_TOKEN_FIELDS, "iterations", "tool_calls", "duration_seconds")


class UsageReport(BaseModel):
    """What one agent run consumed. All fields optional; report only what you can read."""

    model_config = ConfigDict(extra="forbid")

    input_tokens: int | None = Field(default=None, ge=0, le=MAX_TOKENS)
    cached_input_tokens: int | None = Field(default=None, ge=0, le=MAX_TOKENS)
    output_tokens: int | None = Field(default=None, ge=0, le=MAX_TOKENS)
    iterations: int | None = Field(default=None, ge=0, le=1_000_000)
    tool_calls: int | None = Field(default=None, ge=0, le=1_000_000)
    duration_seconds: int | None = Field(default=None, ge=0, le=31_536_000)
    source: Literal["engine", "agent"] = "agent"

    @model_validator(mode="after")
    def _not_empty(self) -> UsageReport:
        if all(getattr(self, f) is None for f in _COUNT_FIELDS):
            raise ValueError("Report at least one usage figure.")
        return self

    def counts(self) -> dict[str, int]:
        return {f: int(getattr(self, f)) for f in _COUNT_FIELDS if getattr(self, f) is not None}

    def split(self, parts: int) -> list[dict[str, int]]:
        """Share one session's totals across several tasks without losing the remainder."""
        out: list[dict[str, int]] = [{} for _ in range(parts)]
        for name, value in self.counts().items():
            base, extra = divmod(value, parts)
            for i in range(parts):
                out[i][name] = base + (1 if i < extra else 0)
        if parts > 1:
            for item in out:
                item["split_across"] = parts
        return out


def layer_tokens(layer: Any) -> int | None:
    if not isinstance(layer, dict):
        return None
    vals = [int(layer[f]) for f in _TOKEN_FIELDS if layer.get(f) is not None]
    return sum(vals) if vals else None


def tokens_used(usage: Any) -> int | None:
    """Reported tokens for a task, or None when no coding agent or paid model usage is known."""
    if not isinstance(usage, dict):
        return None
    agent, engine = layer_tokens(usage.get("agent")), layer_tokens(usage.get("engine"))
    if agent is None:
        return engine or None  # a code-only check (0 tokens) says nothing about the agent's session
    return agent + (engine or 0)


def _seconds_between(start: Any, end: Any) -> int | None:
    try:
        a = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    try:
        return max(0, int((b - a).total_seconds()))
    except TypeError:
        return None


def public_usage(usage: Any, *, started_at: Any, completed_at: Any, attempts: int) -> dict[str, Any] | None:
    """Public usage block for a completed task."""
    if not completed_at:
        return None
    data = usage if isinstance(usage, dict) else {}
    agent = data.get("agent") if isinstance(data.get("agent"), dict) else {}
    return {
        "tokens_used": tokens_used(data),
        "agent_tokens": layer_tokens(agent),
        "check_tokens": layer_tokens(data.get("engine")),
        "input_tokens": agent.get("input_tokens"),
        "cached_input_tokens": agent.get("cached_input_tokens"),
        "output_tokens": agent.get("output_tokens"),
        "iterations": agent.get("iterations"),
        "tool_calls": agent.get("tool_calls"),
        "agent_seconds": agent.get("duration_seconds"),
        "split_across": agent.get("split_across"),
        "hub_seconds": _seconds_between(started_at, completed_at) if started_at else None,
        "attempts": attempts,
    }


def summarise_tokens(
    tasks: Iterable[tuple[str, int | None, Any]], planned: Iterable[dict[str, Any]]
) -> dict[str, Any]:
    """Project totals from ``(status, estimated_tokens, usage)`` rows plus planned tasks."""
    total = remaining = used = 0
    estimated = done = reported = 0
    for status, estimate, usage in tasks:
        completed = status == "completed"
        if estimate is not None:
            estimated += 1
            total += int(estimate)
            if not completed:
                remaining += int(estimate)
        if completed:
            done += 1
            spent = tokens_used(usage)
            if spent is not None:
                reported += 1
                used += spent
    for item in planned:
        estimated += 1
        total += int(item.get("estimated_tokens") or 0)
        remaining += int(item.get("estimated_tokens") or 0)
    return {
        "estimated_tokens": total if estimated else None,
        "remaining_tokens": remaining if estimated else None,
        "tokens_used": used if reported else None,
        "reported_tasks": reported,
        "completed_tasks": done,
    }
