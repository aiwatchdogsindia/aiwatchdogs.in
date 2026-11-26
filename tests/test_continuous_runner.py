"""Regression tests for continuous unlimited queue-drain mode (--max-tasks 0), resilience & paid guardrails."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from aiwatchdogs.cli import config as cfg_mod
from aiwatchdogs.cli.config import (
    DEFAULT_FREE_MAX_TASKS,
    DEFAULT_PAID_MAX_TASKS,
    ENV_CONFIG_PATH,
    ENV_HANDLE,
    AgentConfig,
    get_handle_config_path,
    load_config,
    save_config,
)
from aiwatchdogs.cli.main import app as cli_app
from aiwatchdogs.cli.providers import ProviderInfo
from aiwatchdogs.cli.runner import MAX_CONSECUTIVE_SOURCE_FAILURES, AgentRunner
from aiwatchdogs.hub.app import create_app
from aiwatchdogs.hub.templates import render_join_sh, render_join_txt

cli_runner = CliRunner()

_PAGE = "Demand 9\n2055 Police 100.00 200.00 160.00 210.00\n"
_P1_CONTEXT = {
    "budget_lines": [
        {
            "key": "test-police-2055",
            "entity": "Test Police",
            "label": "Major Head 2055",
            "unit": "crore",
            "parts": [
                {"page": 1, "pattern": r"^2055 Police\s+\d", "values": {"actual": 1, "be": 2, "re": 3, "be_next": 4}}
            ],
            "years": {"actual": "FY2024-25", "be": "FY2025-26", "re": "FY2025-26", "be_next": "FY2026-27"},
        }
    ],
    "comparisons": [
        {"a": "re", "b": "be", "label": "revised estimate vs budget estimate", "threshold_pct": 10, "kind": "re_be"}
    ],
}


def _lease(lease_id: str, *, role: str = "auditor") -> dict[str, Any]:
    return {
        "lease_id": lease_id,
        "lease_expires_at": "2099-01-01T00:00:00Z",
        "role": {"name": role, "title": role.title(), "brief": "audit task"},
        "task": {
            "id": f"tsk-{lease_id}",
            "task_type": "anomaly_scan",
            "mission_slug": "police-finances",
            "methodology_code": "P1",
            "title": f"Test Police task {lease_id}",
            "instructions": "Run P1",
            "payload": {
                "records": [{"page": 1, "raw_text": _PAGE}],
                "jurisdiction": "State:Test",
                "fiscal_year": "FY2024-27",
                "context": _P1_CONTEXT,
            },
        },
    }


class _ScriptedHub:
    def __init__(
        self,
        leases: list[dict[str, Any]],
        *,
        beat_410_leases: set[str] | None = None,
        submit_410_leases: set[str] | None = None,
    ) -> None:
        self.leases = list(leases)
        self.beat_410 = set(beat_410_leases or ())
        self.submit_410 = set(submit_410_leases or ())
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        self.calls.append((path, body))
        if path == "/api/desk/next":
            return httpx.Response(200, json=self.leases.pop(0)) if self.leases else httpx.Response(204)
        if path == "/api/desk/beat":
            if body.get("lease_id") in self.beat_410:
                return httpx.Response(410, json={"detail": "Lease settled"})
            return httpx.Response(200, json={"status": "active"})
        if path == "/api/desk/submit":
            if body.get("lease_id") in self.submit_410:
                return httpx.Response(410, json={"detail": "Lease settled"})
            return httpx.Response(200, json={"status": "accepted", "outcome": body.get("outcome")})
        if path == "/api/desk/release":
            return httpx.Response(200, json={"status": "released"})
        return httpx.Response(404)

    def bodies(self, path: str) -> list[dict[str, Any]]:
        return [b for p, b in self.calls if p == path]


def _make_runner(
    hub: _ScriptedHub,
    *,
    cfg: AgentConfig | None = None,
    events: list[tuple[str, dict[str, Any]]] | None = None,
) -> tuple[AgentRunner, list[float]]:
    agent_cfg = cfg or AgentConfig(handle="drain-node", hub_url="http://mockhub", api_token="awd_tok", max_tasks=0)
    client = httpx.Client(transport=httpx.MockTransport(hub), base_url="http://mockhub")
    runner = AgentRunner(
        agent_cfg,
        http_client=client,
        on_event=(lambda ev, data: events.append((ev, data))) if events is not None else None,
    )
    sleeps: list[float] = []
    runner._sleep = sleeps.append
    return runner, sleeps


# ---------------------------------------------------------------------------
# 1. Unlimited free-mode queue draining (`max_tasks == 0`)
# ---------------------------------------------------------------------------


def test_run_loop_unlimited_drains_all_tasks_until_queue_empty() -> None:
    leases = [_lease(f"t{i}") for i in range(1, 7)]
    hub = _ScriptedHub(leases)
    events: list[tuple[str, dict[str, Any]]] = []
    runner, sleeps = _make_runner(hub, events=events)

    summary = runner.run_loop(max_tasks=0)
    assert summary.tasks_claimed == 6
    assert summary.tasks_completed == 6
    assert summary.findings_submitted == 6
    assert summary.errors == 0
    assert summary.stopped_reason == "queue_empty"
    assert sleeps == []
    assert hub.leases == []

    started = [d for ev, d in events if ev == "run_started"]
    assert len(started) == 1 and started[0]["max_tasks"] == 0
    progress = [d for ev, d in events if ev == "task_progress"]
    assert [p["completed"] for p in progress] == [1, 2, 3, 4, 5, 6]
    assert all(p["max_tasks"] == 0 for p in progress)


def test_run_loop_unlimited_continues_after_single_403_and_timeout_and_resets_failure_counter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single HTTP 403 (e.g. CloudFront Telangana PDF) or timeout releases cleanly and moves to the next task."""
    leases = [_lease(f"t{i}") for i in range(1, 8)]
    hub = _ScriptedHub(leases)
    runner, sleeps = _make_runner(hub)

    orig_load = runner._load_task_dataset
    call_idx = 0

    def flaky_load(payload: dict[str, Any], **kwargs: Any) -> Any:
        nonlocal call_idx
        call_idx += 1
        # Task 1: HTTP 403 PermissionError (just like loader.py raises for Telangana CloudFront 403)
        if call_idx == 1:
            raise PermissionError(
                "Remote source https://d123.cloudfront.net/telangana.pdf returned HTTP 403. "
                "AI Watchdogs does not bypass authentication walls or access controls (IT Act Sec 43)."
            )
        # Task 2 & 3: succeed (resets consecutive failure counter to 0!)
        # Task 4 & 5: two transient errors in a row (< 5 threshold -> still keeps going!)
        if call_idx == 4:
            raise httpx.ReadTimeout("slow .gov.in server")
        if call_idx == 5:
            raise PermissionError("Remote source https://state.gov.in/budget.pdf returned HTTP 403.")
        # Task 6 & 7: succeed!
        return orig_load(payload, **kwargs)

    monkeypatch.setattr(runner, "_load_task_dataset", flaky_load)
    summary = runner.run_loop(max_tasks=0)

    assert summary.tasks_claimed == 7
    assert summary.tasks_completed == 4
    assert summary.errors == 3
    assert summary.stopped_reason == "queue_empty"
    assert [b["reason"] for b in hub.bodies("/api/desk/release")] == [
        "source_http_forbidden",
        "network_error",
        "source_http_forbidden",
    ]
    # Brief backoff after each failure: failure #1 -> 1.0s, then reset, then #1 -> 1.0s, #2 -> 2.0s
    assert sleeps == [1.0, 1.0, 2.0]


def test_run_loop_unlimited_stops_cleanly_after_five_consecutive_source_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert MAX_CONSECUTIVE_SOURCE_FAILURES == 5
    leases = [_lease(f"t{i}") for i in range(1, 9)]
    hub = _ScriptedHub(leases)
    runner, sleeps = _make_runner(hub)

    def always_down(*_a: object, **_k: object) -> None:
        raise httpx.ConnectError("machine offline or egress firewalled")

    monkeypatch.setattr(runner, "_load_task_dataset", always_down)
    summary = runner.run_loop(max_tasks=0)

    assert summary.tasks_claimed == 5
    assert summary.tasks_completed == 0
    assert summary.errors == 5
    assert summary.stopped_reason == "sources_unreachable"
    assert len(hub.leases) == 3  # remaining 3 tasks left untouched for healthy nodes
    assert sleeps == [1.0, 2.0, 3.0, 4.0]


def test_run_loop_unlimited_continues_after_410_gone_on_beat_or_submit() -> None:
    leases = [_lease("ok1"), _lease("beat410"), _lease("submit410"), _lease("ok2")]
    hub = _ScriptedHub(leases, beat_410_leases={"beat410"}, submit_410_leases={"submit410"})
    runner, sleeps = _make_runner(hub)

    summary = runner.run_loop(max_tasks=0)
    assert summary.tasks_claimed == 4
    assert summary.tasks_completed == 2
    assert summary.aborted_410 == 2
    assert summary.errors == 0
    assert summary.stopped_reason == "queue_empty"
    assert sleeps == []


# ---------------------------------------------------------------------------
# 2. Paid provider cap preservation & defense-in-depth
# ---------------------------------------------------------------------------


def test_paid_provider_cannot_run_unbounded_in_runner_or_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 1. Defense-in-depth in AgentRunner.run_loop: opted-in paid provider clamps max_tasks=0 -> DEFAULT_PAID_MAX_TASKS
    leases = [_lease(f"p{i}") for i in range(1, 10)]
    hub = _ScriptedHub(leases)
    paid_cfg = AgentConfig(
        handle="paid-node",
        hub_url="http://mockhub",
        api_token="awd_tok",
        provider="openai",
        model="gpt-4o-mini",
        max_tasks=0,
        max_usd=1.0,
        allow_paid_provider=True,
    )
    runner, _ = _make_runner(hub, cfg=paid_cfg)
    # Stub LLM enrichment so zero network calls go to OpenAI while still exercising paid provider guard
    monkeypatch.setattr(
        "aiwatchdogs.cli.runner.enrich_finding_summary",
        lambda lead, *_a, **_k: {
            "title": lead.title,
            "summary": lead.summary,
            "benign_explanations": list(lead.benign_explanations),
            "editorial_interpretation": None,
            "enriched_by": "rule-engine",
            "used_llm": False,
        },
    )
    summary = runner.run_loop(max_tasks=0)
    assert summary.tasks_claimed == DEFAULT_PAID_MAX_TASKS
    assert summary.tasks_completed == DEFAULT_PAID_MAX_TASKS
    assert len(hub.leases) == 9 - DEFAULT_PAID_MAX_TASKS

    # 2. CLI `join` with paid provider defaults to DEFAULT_PAID_MAX_TASKS (5), NOT 0!
    cfg_file = tmp_path / "paid_cfg.json"
    monkeypatch.setenv(ENV_CONFIG_PATH, str(cfg_file))
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
    res_paid_dry = cli_runner.invoke(
        cli_app,
        ["join", "--dry-run", "--provider", "openai", "--allow-paid-provider", "--max-usd", "1.0"],
        env={"COLUMNS": "320"},
    )
    assert res_paid_dry.exit_code == 0, res_paid_dry.output
    norm_paid = " ".join(res_paid_dry.stdout.split())
    assert f"Max Tasks Cap: {DEFAULT_PAID_MAX_TASKS}" in norm_paid
    assert "Unlimited" not in norm_paid

    # 2b. Saved free config (max_tasks=0) switched to paid provider via `run` automatically enforces paid cap!
    save_config(
        AgentConfig(
            handle="upgrade-paid-node",
            hub_url="https://aiwatchdogs.in",
            api_token="awd_tok",
            provider="rule-engine",
            model="deterministic-python-v1",
            max_tasks=0,
        ),
        path=cfg_file,
    )
    res_run_paid_switch = cli_runner.invoke(
        cli_app,
        ["run", "--dry-run", "--provider", "openai", "--allow-paid-provider", "--max-usd", "1.0"],
        env={"COLUMNS": "320"},
    )
    assert res_run_paid_switch.exit_code == 0, res_run_paid_switch.output
    norm_switch = " ".join(res_run_paid_switch.stdout.split())
    assert f"Max Tasks Cap: {DEFAULT_PAID_MAX_TASKS}" in norm_switch
    assert "Unlimited" not in norm_switch

    # 2c. Saved legacy config (max_tasks=5, rule-engine) overridden with `run --max-tasks 0` switches to unlimited!
    save_config(
        AgentConfig(
            handle="legacy-cap-node",
            hub_url="https://aiwatchdogs.in",
            api_token="awd_tok",
            provider="rule-engine",
            model="deterministic-python-v1",
            max_tasks=5,
        ),
        path=cfg_file,
    )
    res_run_zero_override = cli_runner.invoke(
        cli_app,
        ["run", "--dry-run", "--max-tasks", "0"],
        env={"COLUMNS": "320"},
    )
    assert res_run_zero_override.exit_code == 0, res_run_zero_override.output
    assert "Unlimited (₹0 free mode — runs until queue empty or Ctrl+C)" in " ".join(
        res_run_zero_override.stdout.split()
    )

    # 3. CLI `join` REJECTS explicit `--max-tasks 0` with paid provider (exit code 2)!
    res_paid_zero = cli_runner.invoke(
        cli_app,
        [
            "join",
            "--dry-run",
            "--provider",
            "openai",
            "--allow-paid-provider",
            "--max-usd",
            "1.0",
            "--max-tasks",
            "0",
        ],
    )
    assert res_paid_zero.exit_code == 2
    assert "Refusing --max-tasks 0" in res_paid_zero.output

    # 4. CLI `run` REJECTS explicit `--max-tasks 0` with paid provider too!
    res_run_paid_zero = cli_runner.invoke(
        cli_app,
        ["run", "--dry-run", "--provider", "openai", "--allow-paid-provider", "--max-usd", "1.0", "--max-tasks", "0"],
    )
    assert res_run_paid_zero.exit_code == 2
    assert "Refusing --max-tasks 0" in res_run_paid_zero.output


# ---------------------------------------------------------------------------
# 3. CLI `join` / `run` defaults, guardrail table, `--task-id`, and Ctrl+C summary
# ---------------------------------------------------------------------------


def test_cli_join_free_defaults_unlimited_guardrail_display_task_id_and_interrupt_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_CONFIG_PATH, raising=False)
    monkeypatch.delenv(ENV_HANDLE, raising=False)
    legacy_cfg = tmp_path / "aiwatchdogs" / "config.json"
    monkeypatch.setattr(cfg_mod, "DEFAULT_CONFIG_PATH", legacy_cfg)
    monkeypatch.setattr("aiwatchdogs.cli.main.detect_github_handle", lambda: ("draindev", "gh auth status"))
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

    # 1. Free mode dry-run defaults to max_tasks=0 and prints the exact unlimited guardrail text!
    res_dry = cli_runner.invoke(
        cli_app,
        ["join", "--dry-run", "--provider", "rule-engine"],
        env={"COLUMNS": "240"},
    )
    assert res_dry.exit_code == 0, res_dry.output
    assert "Unlimited (₹0 free mode — runs until queue empty or Ctrl+C)" in res_dry.stdout

    # 2. Free mode `join --no-run` saves max_tasks=0 to disk
    hub_app = create_app(db_path=tmp_path / "cli_unlimited.db", seed=True)
    with TestClient(hub_app, base_url="https://testserver") as asgi_client:
        orig_init = AgentRunner.__init__

        def _patched_init(self: AgentRunner, config: AgentConfig, **kwargs: Any) -> None:
            kwargs.setdefault("http_client", asgi_client)
            orig_init(self, config, **kwargs)

        monkeypatch.setattr(AgentRunner, "__init__", _patched_init)

        res_reg = cli_runner.invoke(
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
        assert res_reg.exit_code == 0, res_reg.output
        saved = load_config(get_handle_config_path("draindev"))
        assert saved is not None and saved.max_tasks == DEFAULT_FREE_MAX_TASKS == 0

        # 3. `whoami` renders unlimited clearly
        res_who = cli_runner.invoke(cli_app, ["whoami", "--handle", "draindev"], env={"COLUMNS": "240"})
        assert res_who.exit_code == 0, res_who.output
        assert "unlimited (₹0 free mode)" in res_who.stdout

        # 4. `--task-id` with `--max-tasks 0` still caps effective_run_max to 1!
        res_task_dry = cli_runner.invoke(
            cli_app,
            ["run", "--handle", "draindev", "--max-tasks", "0", "--task-id", "some-task-1", "--dry-run"],
            env={"COLUMNS": "240"},
        )
        assert res_task_dry.exit_code == 0, res_task_dry.output
        assert "Max Tasks Cap: 1" in res_task_dry.stdout

    # 5. Ctrl+C / SIGTERM mid-loop hands back the in-flight task AND prints summary + report-usage for completed tasks!
    cfg_file = tmp_path / "interrupt_cfg.json"
    monkeypatch.setenv(ENV_CONFIG_PATH, str(cfg_file))
    save_config(
        AgentConfig(
            handle="sigterm-node",
            hub_url="https://mockhub",
            api_token="awd_tok",
            provider="rule-engine",
            model="deterministic-python-v1",
            max_tasks=0,
        ),
        path=cfg_file,
    )
    leases = [_lease("done1"), _lease("done2"), _lease("interrupted3")]
    scripted_hub = _ScriptedHub(leases)
    mock_client = httpx.Client(transport=httpx.MockTransport(scripted_hub), base_url="https://mockhub")
    try:
        def _init_interrupt(self: AgentRunner, config: AgentConfig, **kwargs: Any) -> None:
            kwargs["http_client"] = mock_client
            orig_init(self, config, **kwargs)
            orig_invoke = self._invoke_engine
            calls = {"n": 0}

            def _invoke_or_interrupt(*a: Any, **kw: Any) -> Any:
                calls["n"] += 1
                if calls["n"] == 3:
                    raise KeyboardInterrupt("signal 15")
                return orig_invoke(*a, **kw)

            self._invoke_engine = _invoke_or_interrupt  # type: ignore[method-assign]

        monkeypatch.setattr(AgentRunner, "__init__", _init_interrupt)
        res_int = cli_runner.invoke(cli_app, ["run"], env={"COLUMNS": "240"})
        assert res_int.exit_code == 0, res_int.output
        assert "Stopped. Any task in progress was handed back to the queue." in res_int.stdout
        assert "Agent Run Summary" in res_int.stdout
        assert "aiwatchdogs report-usage --handle sigterm-node --task-id tsk-done1 --task-id tsk-done2" in res_int.stdout
        assert [b["reason"] for b in scripted_hub.bodies("/api/desk/release")] == ["interrupted"]
        assert len(scripted_hub.bodies("/api/desk/submit")) == 2
    finally:
        mock_client.close()


# ---------------------------------------------------------------------------
# 4. Template checks (`/join.txt` and `/join.sh`)
# ---------------------------------------------------------------------------


def test_templates_use_max_tasks_zero_and_never_continuous() -> None:
    txt = render_join_txt("https://aiwatchdogs.in")
    assert "completes queued tasks one after another at ₹0" in txt
    assert "--max-tasks 3" not in txt
    assert "--continuous" not in txt
    assert "aiwatchdogs run --handle '<HANDLE>'" in txt
    assert "nohup" not in txt.lower()
    assert "demo" not in txt.lower()
    assert "sample" not in txt.lower()

    sh = render_join_sh("https://aiwatchdogs.in")
    assert "--max-tasks 0" in sh
    assert "--continuous" not in sh
