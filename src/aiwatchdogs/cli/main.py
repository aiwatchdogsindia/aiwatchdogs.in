"""Typer + Rich CLI entrypoint (`aiwatchdogs`) for AI Watchdogs India."""

from __future__ import annotations

import inspect
import json
import os
import re
import secrets
import signal
import stat
import sys
from pathlib import Path
from typing import Any

import httpx
import typer
from rich import box
from rich.console import Console
from rich.markup import escape as rich_escape
from rich.panel import Panel
from rich.prompt import Confirm, FloatPrompt, IntPrompt, Prompt
from rich.table import Table

from aiwatchdogs.cli.config import (
    DEFAULT_FREE_MAX_TASKS,
    DEFAULT_HUB_URL,
    DEFAULT_PAID_MAX_TASKS,
    ENV_HANDLE,
    AgentConfig,
    AmbiguousConfigError,
    explicit_config_path,
    find_saved_config_for_handle,
    get_config_path,
    get_spend_ledger_path,
    load_config,
    load_daily_spend,
    load_legacy_config_for_handle,
    mask_token,
    normalize_hub_origin,
    resolve_reusable_token,
    validate_hub_url,
)
from aiwatchdogs.cli.identity import (
    detect_github_handle,
    fallback_handle,
    is_fallback_handle,
    is_generic_agent_handle,
)
from aiwatchdogs.cli.providers import (
    RULE_ENGINE_PROVIDER,
    ProviderInfo,
    describe_outbound_destinations,
    detect_best_provider,
    detect_providers,
    is_paid_provider,
    resolve_provider_config,
)
from aiwatchdogs.cli.runner import ROLE_LABELS, AgentRunner, role_of
from aiwatchdogs.connectors.loader import load_dataset
from aiwatchdogs.core.rti import generate_rti_draft
from aiwatchdogs.core.coding_agents import (
    detect_coding_agent,
    detect_coding_agent_model,
    normalize_coding_agent,
    normalize_coding_agent_model,
)
from aiwatchdogs.core.safety import lint_neutrality, scrub_pii
from aiwatchdogs.engines.registry import METHODOLOGY_CATALOG, run_engine

app = typer.Typer(
    name="aiwatchdogs",
    help="AI Watchdogs India — Open-source distributed AI civic-audit CLI & agent runner.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

console = Console()
err_console = Console(stderr=True)

VALID_MODES = ("auto", "investigate", "verify")
MAX_CLI_HTTP_BYTES = 5 * 1024 * 1024  # 5 MiB cap on CLI Hub GET responses
_ANSI_ESC_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_CTRL_CHARS_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _safe_rich(value: Any, *, max_len: int | None = None) -> str:
    """Strip terminal escape/control sequences and escape Rich markup on untrusted strings."""
    text = str("" if value is None else value)
    text = _ANSI_ESC_RE.sub("", text)
    text = _CTRL_CHARS_RE.sub("", text)
    if max_len is not None and len(text) > max_len:
        text = text[:max_len] + "…"
    return rich_escape(text)


def _bounded_http_get_json(url: str, *, timeout: float = 10.0, max_bytes: int = MAX_CLI_HTTP_BYTES) -> Any:
    """Perform a bounded HTTP GET with redirects disabled and strict response-size cap."""
    with httpx.Client(timeout=httpx.Timeout(timeout), follow_redirects=False) as client:
        with client.stream("GET", url, headers={"Accept": "application/json"}) as resp:
            resp.raise_for_status()
            buf = bytearray()
            for chunk in resp.iter_bytes(chunk_size=65536):
                buf.extend(chunk)
                if len(buf) > max_bytes:
                    raise ValueError(f"Hub response exceeded maximum allowed size ({max_bytes} bytes).")
            return json.loads(buf.decode("utf-8"))


def _validate_hub_or_exit(url: str, *, allow_insecure_http: bool = False) -> str:
    try:
        return validate_hub_url(url, allow_insecure_http=allow_insecure_http)
    except ValueError as exc:
        err_console.print(f"[red]Invalid Hub URL: {_safe_rich(exc)}[/red]")
        raise typer.Exit(code=2) from exc


def _default_handle() -> str:
    """Generate a pseudonymous random default handle (never leaking local OS username)."""
    return f"civic-{secrets.token_hex(3)}"


def _format_inr(amount: float) -> str:
    val = float(amount or 0.0)
    if val >= 1e7:
        return f"₹{val / 1e7:,.2f} Cr"
    if val >= 1e5:
        return f"₹{val / 1e5:,.2f} L"
    return f"₹{val:,.0f}"


def _render_consent_notice(hub_url: str) -> None:
    notice = (
        "[bold]Running Rules, Outbound Network & Legal Safeguards (aiwatchdogs.in)[/bold]\n"
        f"• [cyan]Coordinator Hub:[/cyan] {_safe_rich(hub_url)} (outbound HTTPS only — no inbound ports required)\n"
        "• [cyan]Public Dataset Hosts:[/cyan] Your node downloads official public-record files from the host "
        "named in each task (for the police project: Union Budget, Home Ministry and State finance department "
        "sites) directly over HTTPS, shows that host before each download, and verifies SHA-256 checksums "
        "locally (fail-closed on mismatch).\n"
        "• [cyan]Hard Budget Guardrail:[/cyan] Deterministic Python mode (rule-engine) "
        "spends $0.00 / ₹0. Paid cloud LLM APIs are STRICTLY BLOCKED unless you explicitly opt in "
        "(--allow-paid-provider) AND set a positive hard cap (--max-usd > 0).\n"
        "• [cyan]Public Attribution:[/cyan] Your public handle (auto-set from local GitHub config unless specified), "
        "provider/model label, deterministic audit metrics, and explicit 4-check verification votes appear publicly "
        "on the swarm ledger.\n"
        "• [cyan]Indian Legal Guardrails:[/cyan] Strictly lawful public-record analysis under "
        "BNS Sec 356 fair public-interest framing, DPDP Act 2023 PII scrubbing, and IT Act Sec 43 "
        "(zero CAPTCHA/auth bypass). Citizens — never bots — review and file RTI drafts."
    )
    console.print(Panel(notice, title="AI Watchdogs India · Join Swarm", border_style="green"))


def _format_max_tasks(max_tasks: int, *, for_guardrail_table: bool = False) -> str:
    if max_tasks <= 0:
        return (
            "Unlimited (₹0 free mode — runs until queue empty or Ctrl+C)"
            if for_guardrail_table
            else "unlimited (₹0 free mode)"
        )
    if for_guardrail_table:
        return f"{max_tasks} (strict in normal & --continuous modes)"
    return str(max_tasks)


def _resolve_max_tasks_or_exit(
    requested_max_tasks: int | None,
    *,
    provider_cfg: ProviderInfo,
    allow_paid_provider: bool,
    saved_max_tasks: int | None = None,
) -> int:
    """Resolve session max_tasks: 0 (unlimited) by default in ₹0 free mode, strict positive cap for paid LLMs."""
    paid_prov = is_paid_provider(provider_cfg)
    if requested_max_tasks is not None:
        if requested_max_tasks == 0 and paid_prov:
            err_console.print(
                "[red]Refusing --max-tasks 0 (unlimited) with a paid cloud LLM provider.[/red]\n"
                "Unlimited task-after-task runs are restricted to ₹0 free mode (--provider rule-engine). "
                f"For paid providers, pass an explicit positive cap (e.g. --max-tasks {DEFAULT_PAID_MAX_TASKS}) "
                "alongside --max-usd."
            )
            raise typer.Exit(code=2)
        return max(0, int(requested_max_tasks))

    if saved_max_tasks is not None:
        if saved_max_tasks <= 0 and paid_prov:
            return DEFAULT_PAID_MAX_TASKS
        return max(0, int(saved_max_tasks))

    return DEFAULT_PAID_MAX_TASKS if paid_prov else DEFAULT_FREE_MAX_TASKS


def _render_outbound_destinations(
    hub_url: str,
    provider_cfg: ProviderInfo,
    *,
    allow_paid_provider: bool,
    max_usd: float,
    daily_max_usd: float | None = None,
    max_tasks: int | None = None,
    daily_spent_usd: float | None = None,
    title: str = "Outbound Network Destinations & Hard Budget Guardrails",
) -> dict[str, Any]:
    plan = describe_outbound_destinations(
        hub_url,
        provider_cfg,
        allow_paid_provider=allow_paid_provider,
        max_usd=max_usd,
        daily_max_usd=daily_max_usd,
    )
    table = Table(title=title, box=box.ROUNDED)
    table.add_column("Category", style="bold cyan", no_wrap=True)
    table.add_column("Destination", style="bold")
    table.add_column("Status / Purpose")

    table.add_row(
        "Hub API",
        str(plan["hub"]["destination"]),
        str(plan["hub"]["purpose"]),
    )
    for ds in plan["dataset_hosts"]:
        table.add_row(
            "Public Dataset",
            str(ds.get("destination") or f"https://{ds['host']}"),
            str(ds["purpose"]),
        )

    llm = plan["llm_provider"]
    llm_dest = str(llm.get("destination") or "(none — offline deterministic CPU)")
    llm_status = str(llm.get("status", ""))
    if llm_status == "blocked_by_guardrail":
        llm_desc = f"[yellow]{llm['purpose']}[/yellow]"
    elif llm_status == "opted_in_paid_provider":
        llm_desc = f"[magenta]{llm['purpose']}[/magenta]"
    else:
        llm_desc = f"[green]{llm['purpose']}[/green]"
    table.add_row("LLM Provider", llm_dest, llm_desc)

    eff_daily = daily_max_usd if daily_max_usd is not None else max_usd
    guardrail_parts = [
        f"Mode: {plan['mode_summary']}",
        f"Paid Opt-In: {'YES' if allow_paid_provider else 'NO (blocked)'}",
        f"Hard Caps: session=${max_usd:.2f}, daily=${eff_daily:.2f}",
    ]
    if daily_spent_usd is not None:
        guardrail_parts.append(f"Today's Recorded Spend: ${daily_spent_usd:.4f}")
    if max_tasks is not None:
        guardrail_parts.append(f"Max Tasks Cap: {_format_max_tasks(max_tasks, for_guardrail_table=True)}")
    table.add_row("Guardrails", "Enforced pre-dispatch", " · ".join(guardrail_parts))

    console.print(table)
    return plan


def _render_providers_table(providers: list[ProviderInfo]) -> None:
    table = Table(title="Detected Audit Providers (<2s scan)", box=box.SIMPLE_HEAVY)
    table.add_column("#", style="bold cyan", width=3)
    table.add_column("Provider", style="bold")
    table.add_column("Model")
    table.add_column("Cost / Policy")
    table.add_column("Details")
    for idx, prov in enumerate(providers, start=1):
        cost_str = (
            "[green]₹0 / $0.00 (Deterministic)[/green]"
            if not is_paid_provider(prov)
            else "[yellow]Paid BYO Key (explicit opt-in + --max-usd > 0 required)[/yellow]"
        )
        table.add_row(str(idx), prov.provider, prov.model, cost_str, prov.label)
    console.print(table)


def _enable_line_buffered_output() -> None:
    """Flush stdout/stderr per line in piped non-TTY agent shells so live progress appears immediately."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(line_buffering=True)
            except (OSError, ValueError):
                pass


def _make_event_printer() -> Any:
    def _on_event(event: str, data: dict[str, Any]) -> None:
        if event == "registered":
            console.print(
                Panel(
                    f"[bold green]Registered![/bold green] Handle: [bold]{_safe_rich(data.get('handle'))}[/bold]\n"
                    f"Follow your agent live: [bold underline cyan]{_safe_rich(data.get('profile_url'))}[/bold underline cyan]",
                    border_style="green",
                )
            )
        elif event == "config_fallback":
            console.print(
                f"[yellow]Note:[/yellow] ~/.config is read-only in this environment; saved hub token (0600) at "
                f"[bold]{_safe_rich(data.get('path'))}[/bold].",
                soft_wrap=True,
            )
        elif event == "run_started":
            max_t = int(data.get("max_tasks") or 0)
            profile = _safe_rich(data.get("profile_url") or "")
            if max_t <= 0:
                console.print(
                    "[bold cyan]Starting continuous task loop[/bold cyan] "
                    "(unlimited ₹0 tasks until queue empty or Ctrl+C) · "
                    f"Live profile: [underline cyan]{profile}[/underline cyan]",
                    soft_wrap=True,
                )
            else:
                console.print(
                    f"[bold cyan]Starting task loop[/bold cyan] (max {max_t} task(s)) · "
                    f"Live profile: [underline cyan]{profile}[/underline cyan]",
                    soft_wrap=True,
                )
        elif event == "task_claimed":
            task = data.get("task") or {}
            role = ROLE_LABELS.get(role_of(data), "AUDITOR")
            console.print(
                f"[cyan]▶ Role assigned by the hub:[/cyan] [bold]{role}[/bold] · "
                f"[bold]{_safe_rich(task.get('methodology_code'))}[/bold] · {_safe_rich(task.get('title'), max_len=160)} "
                f"(task {_safe_rich(task.get('id'), max_len=80)}, lease {_safe_rich(str(data.get('lease_id', ''))[:8])})"
            )
        elif event == "heartbeat":
            pct = data.get("progress_pct")
            whisper = _safe_rich(data.get("whisper") or "", max_len=240)
            pct_str = f"{pct}%" if pct is not None else "beat"
            console.print(f"  [dim]♥ {pct_str}[/dim] {whisper}")
        elif event == "task_submitted":
            outcome = str(data.get("outcome", "")).upper()
            color = {
                "FINDING": "yellow",
                "CLEARED": "green",
                "VERIFICATION": "cyan",
                "OBSERVATIONS": "green",
            }.get(outcome, "white")
            console.print(f"  [{color}]✓ Submitted {_safe_rich(outcome)}[/{color}]")
        elif event == "task_progress":
            done_n = int(data.get("completed") or 0)
            max_t = int(data.get("max_tasks") or 0)
            role = ROLE_LABELS.get(str(data.get("role") or ""), "AUDITOR")
            outcome = str(data.get("outcome") or "").upper()
            progress_tag = f"#{done_n}/{max_t}" if max_t > 0 else f"#{done_n} (continuing to next task…)"
            console.print(
                f"  [bold green]● Completed task {progress_tag}[/bold green] · "
                f"[bold]{role}[/bold] · {_safe_rich(outcome)}"
            )
        elif event == "lease_gone":
            console.print("  [yellow]⚠ Lease already settled (410 Gone) — moving to next task.[/yellow]")
        elif event == "task_error":
            console.print(
                f"  [red]✗ Task error (lease released cleanly; moving to next task): "
                f"{_safe_rich(data.get('error'), max_len=240)}[/red]"
            )
        elif event == "queue_empty":
            console.print("[dim]No queued tasks available right now.[/dim]")
        elif event == "source_download":
            hosts = ", ".join(_safe_rich(h, max_len=120) for h in data.get("hosts") or [])
            console.print(f"  [dim]↓ Downloading the pinned file from[/dim] {hosts}")
        elif event == "hub_retry":
            why = "the hub is rate-limiting this network address" if data.get("reason") == "rate_limited" else f"hub {data.get('reason')}"
            console.print(f"  [dim]… {_safe_rich(data.get('what'))}: {_safe_rich(why)}; retrying in {_safe_rich(data.get('delay'))}s[/dim]")
        elif event == "lease_recovered":
            console.print("[dim]Returned a task that an earlier interrupted run of this handle was still holding.[/dim]")
        elif event == "claim_refused":
            console.print(
                "[yellow]The hub would not hand out another task right now (transient rate limit or "
                "this handle reached its concurrent active-task ceiling). Nothing is lost; run again shortly.[/yellow]"
            )
        elif event == "task_claim_rejected":
            status_code = data.get("status_code")
            label = "Task not found" if status_code == 404 else "Task unavailable"
            tid = _safe_rich(data.get("task_id") or "", max_len=128)
            detail = _safe_rich(data.get("detail") or "The hub could not claim that task.", max_len=240)
            console.print(f"[yellow]{label} ({tid}): {detail}[/yellow]")
        elif event == "sources_unreachable":
            console.print(
                "[yellow]Stopping: this machine could not download the official source files "
                f"({_safe_rich(data.get('failures'))} in a row). The tasks were handed back for other nodes. "
                "Check that outbound HTTPS to the government hosts is allowed here, then run again.[/yellow]"
            )
        elif event == "consecutive_errors":
            console.print(
                "[yellow]Stopping after "
                f"{_safe_rich(data.get('errors'))} consecutive task errors. "
                "All leases were released cleanly; check the errors above or re-run with `uvx --refresh`.[/yellow]"
            )

    return _on_event


def _raise_interrupt(signum: int, _frame: Any) -> None:
    raise KeyboardInterrupt(f"signal {signum}")


def _release_on_termination() -> None:
    """Treat a harness timeout or closed terminal like Ctrl-C, so the runner hands its task back.

    Coding agents often stop a long command with SIGTERM; without this the claimed task would stay
    locked until its lease lapsed.
    """
    for name in ("SIGTERM", "SIGHUP"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _raise_interrupt)
        except (ValueError, OSError):  # not the main thread, or unsupported on this platform
            pass


def _print_run_summary(summary: Any) -> None:
    table = Table(title="Agent Run Summary", box=box.ROUNDED)
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")
    table.add_row("Handle", summary.handle)
    table.add_row("Live Profile", summary.profile_url)
    table.add_row("Tasks Claimed", str(summary.tasks_claimed))
    table.add_row("Tasks Completed", str(summary.tasks_completed))
    roles = getattr(summary, "roles", None) or {}
    if roles:
        table.add_row(
            "Roles Played",
            ", ".join(f"{ROLE_LABELS.get(name, name).title()} ×{count}" for name, count in sorted(roles.items())),
        )
    table.add_row("Findings Submitted", str(summary.findings_submitted))
    table.add_row("Peer Verifications", str(summary.verifications_submitted))
    table.add_row("Cleared Records", str(summary.cleared_submitted))
    table.add_row("Paid LLM Spend (Session)", f"${float(getattr(summary, 'session_spent_usd', 0.0)):.4f}")
    budget = getattr(summary, "budget", None) or {}
    if budget.get("calls_blocked"):
        table.add_row("Paid LLM Calls Blocked", f"[yellow]{budget['calls_blocked']}[/yellow]")
    if summary.aborted_410:
        table.add_row("Stopped on 410 Gone", str(summary.aborted_410))
    if summary.errors:
        table.add_row("Errors (Released)", f"[red]{summary.errors}[/red]")
    console.print(table)
    done = [str(x.get("task_id")) for x in summary.results if x.get("status") == "completed" and x.get("task_id")]
    if done:
        console.print(
            "Coding agents: report this session's tokens, time and iterations (the check itself is measured for you):\n"
            f"  aiwatchdogs report-usage --handle {_safe_rich(summary.handle)} "
            f"{' '.join('--task-id ' + _safe_rich(t) for t in done)} "
            "--input-tokens N --cached-input-tokens N --output-tokens N --iterations N --minutes N",
            soft_wrap=True,
        )


def _resolve_config_path(handle: str | None = None) -> Path:
    """Resolve the config path for ``handle``, exiting with a clear message when it is ambiguous."""
    try:
        return get_config_path(handle=handle)
    except AmbiguousConfigError as exc:
        err_console.print(f"[red]{_safe_rich(exc)}[/red]")
        raise typer.Exit(code=2) from exc
    except ValueError as exc:
        err_console.print(f"[red]{_safe_rich(exc)}[/red]")
        raise typer.Exit(code=2) from exc


def _safe_load_existing_config(
    handle: str | None = None,
    *,
    hub_url: str | None = None,
) -> tuple[AgentConfig | None, bool]:
    """Load existing config safely without deleting or overwriting corrupt files.

    When ``handle`` is provided and no explicit config path is pinned, checks
    ``agents/<handle>.json`` as well as legacy ``config.json`` and ``node.json``
    (and XDG fallback) so saved credentials for that exact handle are always reused.
    """
    cfg_path = _resolve_config_path(handle)
    existed = cfg_path.is_file()
    if handle and explicit_config_path() is None and hub_url:
        found = find_saved_config_for_handle(handle, hub_url=hub_url)
        if found is not None:
            return found, existed
    if not existed:
        if handle and explicit_config_path() is None:
            return load_legacy_config_for_handle(handle), False
        return None, False
    try:
        return load_config(cfg_path), True
    except ValueError as exc:
        err_console.print(f"[yellow]Warning: existing config at {cfg_path} could not be parsed ({exc}).[/yellow]")
        return None, True


_HANDLE_OPTION_HELP = "Which saved agent to use, when several are saved on this machine."


def _register_single_profile(
    runner: AgentRunner,
    cfg: AgentConfig,
    *,
    hub_clean: str,
) -> tuple[dict[str, Any], Path]:
    """Register or reconnect enforcing one GitHub username = one public profile.

    NEVER auto-mints `-2` / `-3` / `-4` duplicate handles on HTTP 409. If the Hub returns
    HTTP 409 (handle already registered), exhaustively re-checks all local credential stores
    for that exact handle (`agents/<handle>.json`, `config.json`, `node.json`, XDG fallback,
    and `AIWATCHDOGS_API_TOKEN`) and retries once as `@<handle>` when a matching token exists.
    """
    try:
        reg = runner.register(save=True)
        saved_path = Path(runner.config_path) if runner.config_path is not None else _resolve_config_path(cfg.handle)
        return reg, saved_path
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 409:
            raise
        recovered_cfg: AgentConfig | None = None
        if explicit_config_path() is None:
            recovered_cfg = find_saved_config_for_handle(cfg.handle, hub_url=hub_clean)
        else:
            recovered_cfg, _ = _safe_load_existing_config(cfg.handle, hub_url=hub_clean)
        recovered_token = resolve_reusable_token(recovered_cfg, hub_url=hub_clean, handle=cfg.handle)
        if recovered_token and recovered_token != cfg.api_token:
            cfg.api_token = recovered_token
            if explicit_config_path() is None:
                runner.config_path = _resolve_config_path(cfg.handle)
            console.print(
                f"[dim]Found saved local credentials for '@{_safe_rich(cfg.handle)}'; "
                "reconnecting to your existing GitHub profile...[/dim]"
            )
            reg = runner.register(save=True)
            saved_path = Path(runner.config_path) if runner.config_path is not None else _resolve_config_path(cfg.handle)
            return reg, saved_path
        raise


@app.command()
def join(
    project: str | None = typer.Option(
        None,
        "--project",
        help="Optional project slug (e.g. gov-in-public-information, police-finances).",
    ),
    task_id: str | None = typer.Option(
        None,
        "--task-id",
        "--task",
        help="Claim and run one specific task ID (caps this run to 1 task).",
    ),
    hub: str = typer.Option(
        DEFAULT_HUB_URL,
        "--hub",
        envvar="AIWATCHDOGS_HUB_URL",
        help="Coordinator Hub base URL (e.g. https://aiwatchdogs.in or http://127.0.0.1:8000).",
    ),
    handle: str | None = typer.Option(
        None,
        "--handle",
        help="Optional public agent handle (auto-detects your local GitHub username when omitted).",
    ),
    provider: str | None = typer.Option(
        None,
        "--provider",
        help="Model provider: auto, rule-engine, gemini, anthropic, openai, openrouter.",
    ),
    model: str | None = typer.Option(None, "--model", help="Optional model name override."),
    mode: str = typer.Option("auto", "--mode", help="Task mode: auto, investigate, or verify."),
    max_tasks: int | None = typer.Option(
        None,
        "--max-tasks",
        min=0,
        help="Max tasks to claim in this session (0 = unlimited in ₹0 free mode; default 0 for free providers, 5 for paid providers).",
    ),
    max_usd: float = typer.Option(
        0.0,
        "--max-usd",
        min=0.0,
        help="Hard session paid-LLM spend cap (USD). Default $0.00 strictly enforces ₹0 deterministic mode.",
    ),
    daily_max_usd: float | None = typer.Option(
        None,
        "--daily-max-usd",
        min=0.0,
        help="Optional hard cumulative daily paid-LLM spend cap (USD); defaults to --max-usd.",
    ),
    allow_paid_provider: bool = typer.Option(
        False,
        "--allow-paid-provider/--no-allow-paid-provider",
        help="Explicitly opt in to paid cloud LLM API calls for non-authoritative editorial commentary.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Preview resolved mode, budget guardrails, and outbound destinations without registering or writing config.",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Run immediately with zero interactive prompts."),
    non_interactive: bool = typer.Option(False, "--non-interactive", help="Run non-interactively with zero prompts (same as --yes)."),
    no_run: bool = typer.Option(False, "--no-run", help="Register & save config without immediately running tasks."),
    coding_agent: str | None = typer.Option(
        None,
        "--coding-agent",
        help="The AI coding agent running this contributor (e.g. 'Claude Code', 'Codex', 'Antigravity', 'Cursor'). Shown publicly.",
    ),
    coding_agent_model: str | None = typer.Option(
        None, "--coding-agent-model", help="The coding agent's model (e.g. 'Gemini 4 Neon', 'Claude Opus'). Shown publicly."
    ),
    continuous: bool = typer.Option(
        False,
        "--continuous",
        help="Wait & poll on empty queue until --max-tasks is reached (never exceeds --max-tasks).",
    ),
    allow_insecure_http: bool = typer.Option(
        False,
        "--allow-insecure-http",
        help="Allow non-loopback http:// Hub URLs (NOT recommended; exposes bearer token in cleartext).",
    ),
) -> None:
    """<60s onboarding: auto-detect GitHub handle & provider, register, and start auditing."""
    _enable_line_buffered_output()
    auto_accept = yes or non_interactive or dry_run
    hub_clean = _validate_hub_or_exit(hub, allow_insecure_http=allow_insecure_http)

    if mode not in VALID_MODES:
        err_console.print(f"[red]Invalid --mode '{_safe_rich(mode)}'. Must be one of: {', '.join(VALID_MODES)}[/red]")
        raise typer.Exit(code=2)

    _render_consent_notice(hub_clean)

    explicit_handle = (handle or "").strip().lstrip("@").lower() or None
    handle_was_explicit = explicit_handle is not None
    inferred_coding_agent = ""
    effective_coding_agent = normalize_coding_agent(coding_agent) or detect_coding_agent()

    if explicit_handle is not None and is_generic_agent_handle(explicit_handle):
        detected_gh, gh_source = detect_github_handle()
        if detected_gh:
            inferred_coding_agent = normalize_coding_agent(explicit_handle)
            if not effective_coding_agent and inferred_coding_agent:
                effective_coding_agent = inferred_coding_agent
            console.print(
                f"[yellow]Ignoring coding-agent product name '{_safe_rich(explicit_handle)}' as --handle; "
                f"using your GitHub username '{_safe_rich(detected_gh)}' ({_safe_rich(gh_source)}) instead.[/yellow]"
            )
            explicit_handle = None
            handle_was_explicit = False
        else:
            err_console.print(
                f"[red]Refusing coding-agent product name '{_safe_rich(explicit_handle)}' as --handle.[/red]\n"
                "Pass the human operator's GitHub username via --handle <github-username> "
                "(or omit --handle to auto-detect it), and put the tool name in --coding-agent."
            )
            raise typer.Exit(code=2)

    existing: AgentConfig | None = None
    had_existing_file = False
    handle_source = ""
    suggested_handle = ""

    if explicit_handle is not None:
        existing, had_existing_file = _safe_load_existing_config(explicit_handle, hub_url=hub_clean)
        suggested_handle = explicit_handle
        handle_source = "explicit --handle"
    else:
        # 1. If the operator pinned AIWATCHDOGS_CONFIG_PATH and it holds a valid token for this hub, reuse it.
        saved_candidate: AgentConfig | None = None
        if explicit_config_path() is not None:
            saved_candidate, had_existing_file = _safe_load_existing_config(None, hub_url=hub_clean)
            if (
                saved_candidate is not None
                and saved_candidate.handle
                and not is_generic_agent_handle(saved_candidate.handle)
                and resolve_reusable_token(saved_candidate, hub_url=hub_clean, handle=saved_candidate.handle)
            ):
                existing = saved_candidate
                suggested_handle = saved_candidate.handle.strip().lstrip("@").lower()
                handle_source = "saved local profile"
        else:
            try:
                _only_path = get_config_path()
                if _only_path.is_file():
                    saved_candidate = load_config(_only_path)
            except ValueError:
                saved_candidate = None

        if not handle_source:
            # 2. Local GitHub username detection FIRST so users never stay stuck on a fallback/suffixed
            #    handle when their real GitHub username (and any saved token in agents/, config.json,
            #    or legacy node.json) is available on this machine.
            detected_gh, gh_source = detect_github_handle()
            if detected_gh:
                gh_existing: AgentConfig | None = None
                gh_had_file = False
                if explicit_config_path() is None:
                    gh_existing, gh_had_file = _safe_load_existing_config(detected_gh, hub_url=hub_clean)
                elif saved_candidate is not None and saved_candidate.handle.strip().lstrip("@").lower() == detected_gh:
                    gh_existing = saved_candidate
                    gh_had_file = had_existing_file

                saved_norm = saved_candidate.handle.strip().lstrip("@").lower() if saved_candidate and saved_candidate.handle else ""
                if gh_existing is not None and resolve_reusable_token(
                    gh_existing, hub_url=hub_clean, handle=detected_gh
                ):
                    existing = gh_existing
                    had_existing_file = gh_had_file
                    suggested_handle = detected_gh
                    handle_source = f"{gh_source} (saved credentials)"
                elif (
                    saved_candidate is not None
                    and saved_norm
                    and not is_fallback_handle(saved_norm)
                    and not is_generic_agent_handle(saved_norm)
                    and not saved_norm.startswith(f"{detected_gh}-")
                    and resolve_reusable_token(saved_candidate, hub_url=hub_clean, handle=saved_norm)
                ):
                    existing = saved_candidate
                    suggested_handle = saved_norm
                    handle_source = "saved local profile"
                else:
                    existing = gh_existing
                    had_existing_file = gh_had_file
                    suggested_handle = detected_gh
                    handle_source = gh_source
            elif (
                saved_candidate is not None
                and saved_candidate.handle
                and not is_generic_agent_handle(saved_candidate.handle)
                and resolve_reusable_token(saved_candidate, hub_url=hub_clean, handle=saved_candidate.handle)
            ):
                existing = saved_candidate
                suggested_handle = saved_candidate.handle.strip().lstrip("@").lower()
                handle_source = "saved local profile"
            else:
                # 3. Clean sandbox fallback (e.g. cloud container with no local git/gh config).
                suggested_handle = fallback_handle(effective_coding_agent)
                handle_source = "auto-generated fallback"

    chosen_allow_paid = allow_paid_provider
    chosen_daily_max_usd = daily_max_usd

    if not auto_accept:
        if not Confirm.ask("Do you consent to run a civic-audit agent connected to this Hub under these rules?", default=True):
            console.print("[yellow]Onboarding cancelled. No config was written.[/yellow]")
            raise typer.Exit(code=0)

        # Defer any local provider probing until AFTER explicit owner consent!
        detected = detect_providers(probe_local=not dry_run)
        _render_providers_table(detected)

        chosen_handle = Prompt.ask("Choose your public agent handle", default=suggested_handle).strip()
        if chosen_handle.lower() != suggested_handle.lower():
            handle_was_explicit = True
            handle_source = "interactive input"

        if provider is None:
            default_idx = next(
                (i for i, p in enumerate(detected, start=1) if not is_paid_provider(p)),
                len(detected),
            )
            choice_idx = IntPrompt.ask(
                "Select provider number from table above",
                default=default_idx,
            )
            clamped = max(1, min(choice_idx, len(detected)))
            selected_prov = detected[clamped - 1]
        else:
            selected_prov = resolve_provider_config(
                provider,
                model,
                probe_local=False,
                allow_paid_provider=chosen_allow_paid,
            )

        chosen_mode = Prompt.ask("Task mode", choices=list(VALID_MODES), default=mode)
        default_max_tasks = (
            max_tasks
            if max_tasks is not None
            else (DEFAULT_PAID_MAX_TASKS if is_paid_provider(selected_prov) else DEFAULT_FREE_MAX_TASKS)
        )
        prompted_max_tasks = IntPrompt.ask(
            "Max tasks per run (0 = unlimited in ₹0 free mode)",
            default=default_max_tasks,
        )

        if is_paid_provider(selected_prov):
            if not chosen_allow_paid:
                chosen_allow_paid = Confirm.ask(
                    f"Provider '{selected_prov.provider}' is a PAID cloud API. "
                    "Explicitly opt in to paid LLM editorial commentary?",
                    default=False,
                )
            if chosen_allow_paid:
                chosen_max_usd = FloatPrompt.ask(
                    "Hard session paid-LLM spend cap (USD; $0.00 blocks paid calls)",
                    default=max_usd if max_usd > 0 else 0.50,
                )
                if chosen_daily_max_usd is None:
                    chosen_daily_max_usd = FloatPrompt.ask(
                        "Hard daily cumulative paid-LLM spend cap (USD)",
                        default=chosen_max_usd,
                    )
            else:
                chosen_max_usd = 0.0
                chosen_daily_max_usd = 0.0
                console.print(
                    "[yellow]Paid-provider opt-in declined — paid LLM calls are strictly BLOCKED ($0.00 deterministic CPU fallback).[/yellow]"
                )
        else:
            chosen_max_usd = 0.0
            chosen_daily_max_usd = 0.0

        chosen_max_tasks = _resolve_max_tasks_or_exit(
            max(0, prompted_max_tasks),
            provider_cfg=selected_prov,
            allow_paid_provider=chosen_allow_paid,
        )
    else:
        detected = detect_providers(probe_local=not dry_run)
        _render_providers_table(detected)
        chosen_handle = suggested_handle
        if provider is None or provider.lower() == "auto":
            selected_prov = detect_best_provider(
                probe_local=not dry_run,
                allow_paid_provider=chosen_allow_paid,
            )
        else:
            selected_prov = resolve_provider_config(
                provider,
                model,
                probe_local=False,
                allow_paid_provider=chosen_allow_paid,
            )
        chosen_mode = mode
        chosen_max_tasks = _resolve_max_tasks_or_exit(
            max_tasks,
            provider_cfg=selected_prov,
            allow_paid_provider=chosen_allow_paid,
        )
        chosen_max_usd = max_usd

    if model:
        selected_prov = selected_prov.model_copy(update={"model": model})

    # Each handle has its own credential file, so joining never overwrites another agent's token.
    chosen_handle = chosen_handle.strip().lstrip("@").lower()
    existing, had_existing_file = _safe_load_existing_config(chosen_handle, hub_url=hub_clean)

    # Reuse stored token ONLY when both normalized hub origin AND handle match!
    reused_token = resolve_reusable_token(existing, hub_url=hub_clean, handle=chosen_handle)

    if handle_source:
        console.print(f"[dim]Public handle:[/dim] [bold]{_safe_rich(chosen_handle)}[/bold] [dim]({_safe_rich(handle_source)})[/dim]")
        if handle_source == "auto-generated fallback":
            console.print(
                "[yellow]Note:[/yellow] Could not detect a local GitHub username in this environment. "
                "To get credited under your real GitHub username, pass [bold]--handle <your-github-username>[/bold] "
                "(or configure [bold]gh auth login[/bold] / [bold]git config --global github.user <username>[/bold])."
            )

    existing_agent = normalize_coding_agent(getattr(existing, "coding_agent", "") if existing else "")
    resolved_coding_agent = (
        normalize_coding_agent(coding_agent)
        or inferred_coding_agent
        or detect_coding_agent()
        or existing_agent
    )
    same_coding_agent = (
        not resolved_coding_agent or not existing_agent or resolved_coding_agent == existing_agent
    )
    resolved_coding_agent_model = (
        normalize_coding_agent_model(coding_agent_model)
        or detect_coding_agent_model()
        or (
            normalize_coding_agent_model(getattr(existing, "coding_agent_model", ""))
            if (existing and same_coding_agent)
            else ""
        )
    )
    cfg = AgentConfig(
        handle=chosen_handle,
        hub_url=hub_clean,
        api_token=reused_token,
        provider=selected_prov.provider,
        model=selected_prov.model,
        mode=chosen_mode,
        max_tasks=max(0, chosen_max_tasks),
        max_usd=max(0.0, chosen_max_usd),
        daily_max_usd=max(0.0, chosen_daily_max_usd) if chosen_daily_max_usd is not None else None,
        allow_paid_provider=chosen_allow_paid,
        coding_agent=resolved_coding_agent,
        coding_agent_model=resolved_coding_agent_model,
    )

    cfg_path = _resolve_config_path(cfg.handle)
    daily_spent = load_daily_spend(get_spend_ledger_path(cfg_path))
    target_task_id = (task_id or "").strip() or None
    effective_run_max = 1 if target_task_id else cfg.max_tasks
    _render_outbound_destinations(
        cfg.normalized_hub_url,
        selected_prov,
        allow_paid_provider=cfg.allow_paid_provider,
        max_usd=cfg.max_usd,
        daily_max_usd=cfg.effective_daily_max_usd,
        max_tasks=effective_run_max,
        daily_spent_usd=daily_spent,
    )

    if dry_run:
        task_note = f" · Task ID: [bold]{_safe_rich(target_task_id)}[/bold]" if target_task_id else ""
        console.print(
            Panel(
                f"[bold cyan]Dry-run complete.[/bold cyan] No Hub requests were sent and "
                f"[bold]{cfg_path}[/bold] was not modified.\n"
                f"Handle: [bold]{cfg.handle}[/bold] · Token reuse: "
                f"{'matching origin+handle' if reused_token else 'new registration'}{task_note}",
                border_style="cyan",
            )
        )
        return

    with AgentRunner(
        cfg,
        provider_cfg=selected_prov,
        on_event=_make_event_printer(),
        config_path=cfg_path,
        allow_local_files=False,
        allow_insecure_http=allow_insecure_http,
    ) as runner:
        try:
            reg, cfg_path = _register_single_profile(
                runner,
                cfg,
                hub_clean=hub_clean,
            )
        except httpx.HTTPError as exc:
            # CRITICAL: NEVER overwrite or destroy an existing local config/credential on HTTP error!
            preserve_msg = (
                f"Existing local credentials at [bold]{_safe_rich(cfg_path)}[/bold] were preserved untouched."
                if had_existing_file
                else "No local config file was written."
            )
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 409:
                safe_h = _safe_rich(cfg.handle)
                err_console.print(
                    f"[red]Handle '@{safe_h}' is already registered on {_safe_rich(hub_clean)}.[/red]\n"
                    "AI Watchdogs enforces one GitHub username = one public profile = one scoreboard entry "
                    f"(will NOT create a duplicate '@{safe_h}-2' profile).\n"
                    f"To run this machine under your existing '@{safe_h}' profile, either:\n"
                    f"  1. Copy ~/.config/aiwatchdogs/agents/{safe_h}.json from the original machine where you registered, OR\n"
                    "  2. Export AIWATCHDOGS_API_TOKEN=<your-saved-token> and re-run `aiwatchdogs join`.\n"
                    f"{preserve_msg}"
                )
                raise typer.Exit(code=1) from exc
            err_console.print(
                f"[red]Registration/reconnection failed against Hub {_safe_rich(hub_clean)}: {_safe_rich(exc)}[/red]\n"
                f"{preserve_msg}"
            )
            raise typer.Exit(code=1) from exc

        console.print(
            f"[bold]Config saved atomically:[/bold] {_safe_rich(cfg_path)} (0600) · "
            f"[bold]Follow your agent live:[/bold] {_safe_rich(reg['profile_url'])}",
            soft_wrap=True,
        )

        if no_run:
            return

        _release_on_termination()
        try:
            run_kwargs: dict[str, Any] = {
                "max_tasks": effective_run_max,
                "continuous": False if target_task_id else continuous,
            }
            if project:
                run_kwargs["preferred_mission"] = project
            if target_task_id:
                run_kwargs["task_id"] = target_task_id
            summary = runner.run_loop(**run_kwargs)
        except KeyboardInterrupt:
            console.print("\n[yellow]Stopped. Any task in progress was handed back to the queue.[/yellow]")
            if runner.last_summary is not None:
                _print_run_summary(runner.last_summary)
            return
        except httpx.HTTPError as exc:
            err_console.print(f"[red]Hub communication error during run: {exc}[/red]")
            raise typer.Exit(code=1) from exc

        _print_run_summary(summary)


@app.command()
def run(
    handle: str | None = typer.Option(None, "--handle", envvar="AIWATCHDOGS_HANDLE", help="Which saved agent to run (each handle has its own credential file)."),
    hub: str | None = typer.Option(None, "--hub", envvar="AIWATCHDOGS_HUB_URL", help="Override saved Hub URL."),
    provider: str | None = typer.Option(None, "--provider", help="Override provider (rule-engine, gemini, anthropic, openai, openrouter)."),
    model: str | None = typer.Option(None, "--model", help="Override model name."),
    mode: str | None = typer.Option(None, "--mode", help="Override mode: auto, investigate, or verify."),
    max_tasks: int | None = typer.Option(
        None,
        "--max-tasks",
        min=0,
        help="Max tasks to claim (0 = unlimited in ₹0 free mode; paid providers default to 5 and require >= 1).",
    ),
    max_usd: float | None = typer.Option(None, "--max-usd", min=0.0, help="Hard session paid-LLM USD cap."),
    daily_max_usd: float | None = typer.Option(None, "--daily-max-usd", min=0.0, help="Hard daily paid-LLM USD cap."),
    allow_paid_provider: bool | None = typer.Option(
        None,
        "--allow-paid-provider/--no-allow-paid-provider",
        help="Explicit opt-in for paid cloud LLM editorial commentary.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Preview resolved mode, hard budget guardrails, and outbound destinations without running tasks.",
    ),
    continuous: bool = typer.Option(
        False,
        "--continuous",
        help="Wait & poll on empty queue until max_tasks is reached (strictly respects --max-tasks).",
    ),
    coding_agent: str | None = typer.Option(
        None,
        "--coding-agent",
        help="The AI coding agent running this contributor (e.g. 'Claude Code', 'Codex', 'Antigravity', 'Cursor'). Shown publicly.",
    ),
    coding_agent_model: str | None = typer.Option(
        None, "--coding-agent-model", help="The coding agent's model (e.g. 'Gemini 4 Neon', 'Claude Opus'). Shown publicly."
    ),
    mission: str | None = typer.Option(None, "--project", "--mission", help="Optional project slug (e.g. gov-in-public-information, police-finances)."),
    task_id: str | None = typer.Option(
        None,
        "--task-id",
        "--task",
        help="Claim and run one specific task ID (caps this run to 1 task).",
    ),
    allow_insecure_http: bool = typer.Option(
        False,
        "--allow-insecure-http",
        help="Allow non-loopback http:// Hub URLs (NOT recommended).",
    ),
) -> None:
    """Non-interactive headless watchdog runner using saved config or CLI flags."""
    _enable_line_buffered_output()
    clean_handle = (handle or "").strip().lstrip("@").lower() or None
    target_hub_hint = (hub or DEFAULT_HUB_URL).rstrip("/")
    saved: AgentConfig | None = None
    had_existing_file = False
    if clean_handle is None and explicit_config_path() is None and not os.environ.get(ENV_HANDLE, "").strip():
        try:
            cfg_path = get_config_path()
            saved, had_existing_file = _safe_load_existing_config(None, hub_url=target_hub_hint)
        except AmbiguousConfigError:
            detected_gh, _ = detect_github_handle()
            gh_cfg = find_saved_config_for_handle(detected_gh, hub_url=target_hub_hint) if detected_gh else None
            if detected_gh and gh_cfg is not None:
                clean_handle = detected_gh
                cfg_path = _resolve_config_path(detected_gh)
                saved = gh_cfg
                had_existing_file = cfg_path.is_file()
            else:
                cfg_path = _resolve_config_path(None)
        else:
            # If only a stale `<gh>-2` file exists in agents/, still prioritize `<gh>` when
            # saved credentials exist for `<gh>` in config.json / node.json / agents/<gh>.json.
            if saved is not None and saved.handle:
                detected_gh, _ = detect_github_handle()
                saved_norm = saved.handle.strip().lstrip("@").lower()
                if detected_gh and saved_norm.startswith(f"{detected_gh}-"):
                    gh_cfg = find_saved_config_for_handle(detected_gh, hub_url=hub or saved.hub_url)
                    if gh_cfg is not None and resolve_reusable_token(
                        gh_cfg, hub_url=hub or saved.hub_url, handle=detected_gh
                    ):
                        clean_handle = detected_gh
                        cfg_path = _resolve_config_path(detected_gh)
                        saved = gh_cfg
                        had_existing_file = cfg_path.is_file()
    else:
        cfg_path = _resolve_config_path(clean_handle)
        saved, had_existing_file = _safe_load_existing_config(clean_handle, hub_url=target_hub_hint)
    identity_changed = False

    if saved is None:
        eff_allow_paid = bool(allow_paid_provider) if allow_paid_provider is not None else False
        resolved_prov = resolve_provider_config(
            provider or RULE_ENGINE_PROVIDER,
            model,
            probe_local=False,
            allow_paid_provider=eff_allow_paid,
        )
        run_coding_agent = normalize_coding_agent(coding_agent) or detect_coding_agent()
        resolved_handle = clean_handle
        if not resolved_handle:
            detected_gh, _ = detect_github_handle()
            resolved_handle = detected_gh or fallback_handle(run_coding_agent)
        resolved_max_tasks = _resolve_max_tasks_or_exit(
            max_tasks,
            provider_cfg=resolved_prov,
            allow_paid_provider=eff_allow_paid,
        )
        cfg = AgentConfig(
            handle=resolved_handle,
            hub_url=(hub or DEFAULT_HUB_URL).rstrip("/"),
            api_token="",
            provider=resolved_prov.provider,
            model=resolved_prov.model,
            mode=mode or "auto",
            max_tasks=resolved_max_tasks,
            max_usd=max_usd if max_usd is not None else 0.0,
            daily_max_usd=daily_max_usd,
            allow_paid_provider=eff_allow_paid,
        )
        cfg_path = _resolve_config_path(cfg.handle)
    else:
        cfg = saved.model_copy()
        target_handle = (clean_handle or cfg.handle).strip().lstrip("@").lower()
        target_hub = (hub or cfg.hub_url).rstrip("/")
        if (
            target_handle != cfg.handle.strip().lower()
            or normalize_hub_origin(target_hub) != normalize_hub_origin(cfg.hub_url)
        ):
            identity_changed = True
        cfg.handle = target_handle
        cfg.hub_url = target_hub
        # Reuse stored token ONLY when both normalized hub origin AND handle match!
        cfg.api_token = resolve_reusable_token(saved, hub_url=target_hub, handle=target_handle)

        if allow_paid_provider is not None:
            cfg.allow_paid_provider = allow_paid_provider
        if provider:
            resolved_prov = resolve_provider_config(
                provider,
                model,
                probe_local=False,
                allow_paid_provider=cfg.allow_paid_provider,
            )
            cfg.provider = resolved_prov.provider
            cfg.model = resolved_prov.model
        elif model:
            cfg.model = model
        if mode:
            cfg.mode = mode
        if max_usd is not None:
            cfg.max_usd = max_usd
        if daily_max_usd is not None:
            cfg.daily_max_usd = daily_max_usd

    prev_coding_agent = normalize_coding_agent(cfg.coding_agent)
    explicit_agent = normalize_coding_agent(coding_agent)
    if explicit_agent:
        cfg.coding_agent = explicit_agent
    elif not prev_coding_agent:
        cfg.coding_agent = detect_coding_agent()
    explicit_model = normalize_coding_agent_model(coding_agent_model)
    if explicit_model:
        cfg.coding_agent_model = explicit_model
    elif explicit_agent and prev_coding_agent and explicit_agent != prev_coding_agent:
        cfg.coding_agent_model = detect_coding_agent_model()
    elif not normalize_coding_agent_model(cfg.coding_agent_model):
        cfg.coding_agent_model = detect_coding_agent_model()

    cfg.hub_url = _validate_hub_or_exit(cfg.hub_url, allow_insecure_http=allow_insecure_http)

    if cfg.mode not in VALID_MODES:
        err_console.print(f"[red]Invalid --mode '{_safe_rich(cfg.mode)}'. Must be one of: {', '.join(VALID_MODES)}[/red]")
        raise typer.Exit(code=2)

    prov_cfg = resolve_provider_config(
        cfg.provider,
        cfg.model,
        probe_local=False,
        allow_paid_provider=cfg.allow_paid_provider,
    )
    if saved is not None:
        cfg.max_tasks = _resolve_max_tasks_or_exit(
            max_tasks,
            provider_cfg=prov_cfg,
            allow_paid_provider=cfg.allow_paid_provider,
            saved_max_tasks=cfg.max_tasks,
        )

    target_task_id = (task_id or "").strip() or None
    effective_run_max = 1 if target_task_id else cfg.max_tasks

    if dry_run:
        daily_spent = load_daily_spend(get_spend_ledger_path(cfg_path))
        _render_outbound_destinations(
            cfg.normalized_hub_url,
            prov_cfg,
            allow_paid_provider=cfg.allow_paid_provider,
            max_usd=cfg.max_usd,
            daily_max_usd=cfg.effective_daily_max_usd,
            max_tasks=effective_run_max,
            daily_spent_usd=daily_spent,
        )
        task_note = f" · Task ID: [bold]{_safe_rich(target_task_id)}[/bold]" if target_task_id else ""
        console.print(
            Panel(
                f"[bold cyan]Dry-run complete.[/bold cyan] Handle: [bold]{cfg.handle}[/bold] · "
                f"Mode: [bold]{cfg.mode}[/bold] · Continuous: [bold]{False if target_task_id else continuous}[/bold] · "
                f"Project filter: [bold]{mission or 'any'}[/bold]{task_note}",
                border_style="cyan",
            )
        )
        return

    # Never overwrite an existing saved credential file when running with a temporary different handle/hub
    save_on_reg = not (had_existing_file and identity_changed)

    with AgentRunner(
        cfg,
        provider_cfg=prov_cfg,
        on_event=_make_event_printer(),
        config_path=cfg_path,
        allow_local_files=False,
        allow_insecure_http=allow_insecure_http,
    ) as runner:
        _release_on_termination()
        try:
            summary = runner.run_loop(
                max_tasks=effective_run_max,
                continuous=False if target_task_id else continuous,
                preferred_mission=mission,
                task_id=target_task_id,
                save_on_register=save_on_reg,
            )
        except KeyboardInterrupt:
            console.print("\n[yellow]Stopped. Any task in progress was handed back to the queue.[/yellow]")
            if runner.last_summary is not None:
                _print_run_summary(runner.last_summary)
            return
        except httpx.HTTPError as exc:
            err_console.print(f"[red]Failed to communicate with Hub at {_safe_rich(cfg.normalized_hub_url)}: {_safe_rich(exc)}[/red]")
            raise typer.Exit(code=1) from exc

    _print_run_summary(summary)


def _run_interactive_audit(
    path_or_url: str,
    methodology: str,
    secondary_url: str | None,
    as_of_date: str | None,
    expected_sha256: str | None,
    secondary_sha256: str | None,
    as_json: bool,
    rti: bool,
    authority: str | None,
) -> None:
    """Shared interactive local/URL deterministic civic-audit implementation."""
    code = methodology.strip().upper()
    if code not in METHODOLOGY_CATALOG:
        valid = ", ".join(sorted(METHODOLOGY_CATALOG.keys()))
        err_console.print(f"[red]Unknown methodology '{methodology}'. Valid codes: {valid}[/red]")
        raise typer.Exit(code=2)

    load_ctx: dict[str, Any] = {}
    if secondary_url:
        load_ctx["secondary_url"] = secondary_url
    if as_of_date:
        load_ctx["as_of_date"] = as_of_date
    if expected_sha256:
        load_ctx["expected_sha256"] = expected_sha256.strip().lower()
    if secondary_sha256:
        load_ctx["secondary_expected_sha256"] = secondary_sha256.strip().lower()
        load_ctx["secondary_sha256"] = secondary_sha256.strip().lower()

    try:
        params = inspect.signature(load_dataset).parameters
        kwargs: dict[str, Any] = {}
        if "context" in params and load_ctx:
            kwargs["context"] = load_ctx
        # Local files are explicitly permitted ONLY in interactive CLI audit mode.
        if "allow_local_files" in params:
            kwargs["allow_local_files"] = True
        if "expected_sha256" in params and expected_sha256:
            kwargs["expected_sha256"] = expected_sha256.strip().lower()
        if "secondary_expected_sha256" in params and secondary_sha256:
            kwargs["secondary_expected_sha256"] = secondary_sha256.strip().lower()
        dataset = load_dataset(path_or_url, **kwargs)
    except Exception as exc:
        err_console.print(f"[red]Failed to load dataset '{path_or_url}': {exc}[/red]")
        raise typer.Exit(code=1) from exc

    eng_params = inspect.signature(run_engine).parameters
    eng_kwargs: dict[str, Any] = {"context": load_ctx or None}
    if "as_of_date" in eng_params and as_of_date:
        eng_kwargs["as_of_date"] = as_of_date
    result = run_engine(code, dataset.records, **eng_kwargs)

    processed_leads: list[dict[str, Any]] = []
    rti_drafts: list[dict[str, Any]] = []

    for lead in result.leads:
        lead_dict = lead.model_dump(mode="json")
        lead_dict["title"], _ = scrub_pii(str(lead_dict.get("title", "")))
        lead_dict["summary"], _ = scrub_pii(str(lead_dict.get("summary", "")))
        lead_dict["benign_explanations"] = [
            scrub_pii(str(b))[0] for b in (lead_dict.get("benign_explanations") or [])
        ]
        lint_res = lint_neutrality(
            lead_dict["title"],
            lead_dict["summary"],
            lead_dict["benign_explanations"],
        )
        lead_dict["legal_lint_passed"] = lint_res.passed
        lead_dict["legal_lint_violations"] = lint_res.violations
        processed_leads.append(lead_dict)

        if rti:
            draft = generate_rti_draft(lead_dict, public_authority=authority)
            rti_drafts.append(draft)

    report = {
        "source_ref": dataset.source_ref,
        "format": dataset.format,
        "sha256": dataset.sha256,
        "secondary_sha256": getattr(dataset, "secondary_sha256", None),
        "byte_size": dataset.byte_size,
        "is_demo_sample": False,
        "pii_redactions": dataset.pii_redactions,
        "methodology_code": result.methodology_code,
        "methodology_name": METHODOLOGY_CATALOG[code].get("name", code),
        "engine_version": getattr(result, "engine_version", None),
        "ruleset_version": getattr(result, "ruleset_version", None),
        "as_of_date": getattr(result, "as_of_date", as_of_date),
        "records_scanned": result.records_scanned,
        "monetary_audited_inr": result.monetary_audited_inr,
        "cleared_count": result.cleared_count,
        "unevaluable_count": int(getattr(result, "unevaluable_count", 0) or 0),
        "script_hash": result.script_hash,
        "summary": result.summary,
        "leads": processed_leads,
        "rti_drafts": rti_drafts,
    }

    if as_json:
        typer.echo(json.dumps(report, indent=2, ensure_ascii=False))
        return

    header_text = (
        f"[bold]Source:[/bold] {_safe_rich(dataset.source_ref)}\n"
        f"[bold]Methodology:[/bold] {_safe_rich(code)} — {_safe_rich(METHODOLOGY_CATALOG[code].get('name', code))}\n"
        f"[bold]SHA-256:[/bold] {_safe_rich(dataset.sha256)}\n"
        f"[bold]Scanned:[/bold] {result.records_scanned:,} records  ·  "
        f"[bold]Monetary Audited:[/bold] {_format_inr(result.monetary_audited_inr)}  ·  "
        f"[bold]Leads:[/bold] {len(processed_leads)}  ·  "
        f"[bold]Unevaluable:[/bold] {report['unevaluable_count']}  ·  "
        f"[bold]PII Scrubbed:[/bold] {len(dataset.pii_redactions)}"
    )
    console.print(Panel(header_text, title="AI Watchdogs India · Deterministic Public-Record Audit", border_style="cyan"))

    if not processed_leads:
        console.print(
            Panel(
                f"[bold green]CLEARED[/bold green] — {_safe_rich(result.summary)}",
                border_style="green",
            )
        )
        return

    leads_table = Table(title="Detected Public-Record Anomaly Leads", box=box.SIMPLE_HEAVY)
    leads_table.add_column("#", width=3, style="cyan")
    leads_table.add_column("Severity", width=10)
    leads_table.add_column("Entity / Authority")
    leads_table.add_column("Exposure", justify="right")
    leads_table.add_column("Rule Triggered")
    leads_table.add_column("Neutrality")

    for idx, lead_dict in enumerate(processed_leads, start=1):
        sev = str(lead_dict["severity"]).upper()
        sev_color = {"CRITICAL": "red", "HIGH": "yellow", "MEDIUM": "cyan", "LOW": "green"}.get(sev, "white")
        lint_badge = "[green]PASS[/green]" if lead_dict["legal_lint_passed"] else "[red]FAIL[/red]"
        leads_table.add_row(
            str(idx),
            f"[{sev_color}]{_safe_rich(sev)}[/{sev_color}]",
            _safe_rich(lead_dict["entity_name"]),
            _format_inr(float(lead_dict.get("monetary_exposure_inr", 0.0))),
            _safe_rich(lead_dict["rule_triggered"]),
            lint_badge,
        )
    console.print(leads_table)

    for idx, lead_dict in enumerate(processed_leads, start=1):
        benign_lines = "\n".join(f"  • {_safe_rich(b)}" for b in lead_dict["benign_explanations"])
        metrics_preview = ", ".join(
            f"{_safe_rich(k)}={_safe_rich(v)}" for k, v in list((lead_dict.get("anomaly_metrics") or {}).items())[:6]
        )
        body = (
            f"[bold]{_safe_rich(lead_dict['title'])}[/bold]\n\n"
            f"{_safe_rich(lead_dict['summary'])}\n\n"
            f"[bold]Deterministic Metrics:[/bold] {metrics_preview}\n"
            f"[bold]Plausible Lawful / Benign Explanations:[/bold]\n{benign_lines}"
        )
        console.print(Panel(body, title=f"Lead #{idx} · {_safe_rich(lead_dict['methodology_code'])}", border_style="yellow"))

    if rti and rti_drafts:
        for idx, draft in enumerate(rti_drafts, start=1):
            channel = draft.get("filing_channel") or draft.get("rti_portal_url") or "Verify jurisdiction RTI channel"
            jur_level = draft.get("jurisdiction_level") or ""
            fee_info = draft.get("fee_rules") or draft.get("fee_note") or ""
            # Built outside the f-string: a backslash inside an f-string expression needs Python 3.12+.
            fee_line = f"[bold]Fee Rules:[/bold] {_safe_rich(fee_info)}\n" if fee_info else ""
            rti_body = (
                f"[bold]Public Authority:[/bold] {_safe_rich(draft.get('public_authority'))}"
                f"{f' ({_safe_rich(jur_level)})' if jur_level else ''}\n"
                f"[bold]CPIO/SPIO:[/bold] {_safe_rich(draft.get('cpio_designation'))}  ·  "
                f"[bold]Channel / Portal:[/bold] {_safe_rich(channel)}\n"
                f"{fee_line}"
                f"[bold]Sections:[/bold] {_safe_rich(', '.join(draft.get('statutory_sections') or []))}  ·  "
                f"[bold]Word Count:[/bold] {_safe_rich(draft.get('word_count'))} / 500\n"
                f"[bold]Subject:[/bold] {_safe_rich(draft.get('subject'))}\n\n"
                f"{_safe_rich(draft.get('body_text'))}"
            )
            console.print(
                Panel(
                    rti_body,
                    title=f"RTI Act 2005 Sec 6(1) Draft #{idx} (Human Review & Filing Required)",
                    border_style="magenta",
                )
            )


@app.command("audit-file")
def audit_file(
    path_or_url: str = typer.Argument(
        ...,
        help="Local CSV/JSON/OCDS/XLSX/ZIP/PDF/HTML path or official public dataset URL to audit.",
    ),
    methodology: str = typer.Option(..., "--methodology", "-m", help="Audit methodology code (M1 through M8)."),
    secondary_url: str | None = typer.Option(
        None,
        "--secondary-url",
        help="Optional secondary official CSV/JSON URL for cross-registry joins (e.g. M8 Electoral Bonds x MCA CSR).",
    ),
    as_of_date: str | None = typer.Option(
        None,
        "--as-of-date",
        help="Optional deterministic ISO evaluation date (YYYY-MM-DD) for age-based checks.",
    ),
    expected_sha256: str | None = typer.Option(
        None,
        "--expected-sha256",
        help="Optional expected primary SHA-256 digest (fail-closed on mismatch).",
    ),
    secondary_sha256: str | None = typer.Option(
        None,
        "--secondary-sha256",
        help="Optional expected secondary SHA-256 digest (fail-closed on mismatch).",
    ),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON report to stdout."),
    rti: bool = typer.Option(False, "--rti", help="Generate lawful RTI Act 2005 Sec 6(1) draft(s) for detected leads."),
    authority: str | None = typer.Option(None, "--authority", help="Optional Public Authority override for RTI draft."),
) -> None:
    """Interactive civic-audit analyzer for local files & official Government of India URLs (zero API key required)."""
    _run_interactive_audit(
        path_or_url,
        methodology,
        secondary_url,
        as_of_date,
        expected_sha256,
        secondary_sha256,
        as_json,
        rti,
        authority,
    )


@app.command("audit")
def audit(
    path_or_url: str = typer.Argument(
        ...,
        help="Local CSV/JSON/OCDS/XLSX/ZIP/PDF/HTML path or official public dataset URL to audit.",
    ),
    methodology: str = typer.Option(..., "--methodology", "-m", help="Audit methodology code (M1 through M8)."),
    secondary_url: str | None = typer.Option(
        None,
        "--secondary-url",
        help="Optional secondary official CSV/JSON URL for cross-registry joins (e.g. M8 Electoral Bonds x MCA CSR).",
    ),
    as_of_date: str | None = typer.Option(
        None,
        "--as-of-date",
        help="Optional deterministic ISO evaluation date (YYYY-MM-DD) for age-based checks.",
    ),
    expected_sha256: str | None = typer.Option(
        None,
        "--expected-sha256",
        help="Optional expected primary SHA-256 digest (fail-closed on mismatch).",
    ),
    secondary_sha256: str | None = typer.Option(
        None,
        "--secondary-sha256",
        help="Optional expected secondary SHA-256 digest (fail-closed on mismatch).",
    ),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON report to stdout."),
    rti: bool = typer.Option(False, "--rti", help="Generate lawful RTI Act 2005 Sec 6(1) draft(s) for detected leads."),
    authority: str | None = typer.Option(None, "--authority", help="Optional Public Authority override for RTI draft."),
) -> None:
    """Interactive civic-audit analyzer (alias of `audit-file`; explicitly allows local files)."""
    _run_interactive_audit(
        path_or_url,
        methodology,
        secondary_url,
        as_of_date,
        expected_sha256,
        secondary_sha256,
        as_json,
        rti,
        authority,
    )


@app.command()
def status(
    hub: str | None = typer.Option(None, "--hub", envvar="AIWATCHDOGS_HUB_URL", help="Hub base URL."),
    handle: str | None = typer.Option(None, "--handle", envvar="AIWATCHDOGS_HANDLE", help=_HANDLE_OPTION_HELP),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Display local mode, budget guardrails, and outbound destinations without contacting the Hub.",
    ),
    allow_insecure_http: bool = typer.Option(
        False,
        "--allow-insecure-http",
        help="Allow non-loopback http:// Hub URLs (NOT recommended).",
    ),
) -> None:
    """Display live swarm metrics from the Hub (/api/head) and local outbound network / budget posture."""
    cfg_path = _resolve_config_path(handle)
    saved, _ = _safe_load_existing_config(handle)
    hub_url = _validate_hub_or_exit(
        hub or (saved.hub_url if saved else DEFAULT_HUB_URL),
        allow_insecure_http=allow_insecure_http,
    )

    prov_cfg = resolve_provider_config(
        saved.provider if saved else RULE_ENGINE_PROVIDER,
        saved.model if saved else None,
        probe_local=False,
        allow_paid_provider=saved.allow_paid_provider if saved else False,
    )
    daily_spent = load_daily_spend(get_spend_ledger_path(cfg_path))
    default_status_max = DEFAULT_PAID_MAX_TASKS if is_paid_provider(prov_cfg) else DEFAULT_FREE_MAX_TASKS
    _render_outbound_destinations(
        hub_url,
        prov_cfg,
        allow_paid_provider=saved.allow_paid_provider if saved else False,
        max_usd=saved.max_usd if saved else 0.0,
        daily_max_usd=saved.effective_daily_max_usd if saved else 0.0,
        max_tasks=saved.max_tasks if saved else default_status_max,
        daily_spent_usd=daily_spent,
        title="Local Contributor Outbound Destinations & Budget Posture",
    )

    if dry_run:
        return

    try:
        head = _bounded_http_get_json(f"{hub_url}/api/head", timeout=10.0)
    except (httpx.HTTPError, ValueError) as exc:
        err_console.print(f"[red]Could not fetch swarm status from {_safe_rich(hub_url)}/api/head: {_safe_rich(exc)}[/red]")
        raise typer.Exit(code=1) from exc

    stats = head.get("stats") or {}
    disposition = head.get("disposition") or {}

    kpi_table = Table(title=f"AI Watchdogs India · Live Swarm Status ({_safe_rich(hub_url)})", box=box.ROUNDED)
    kpi_table.add_column("Metric", style="bold")
    kpi_table.add_column("Value", justify="right", style="cyan")

    if "scan_workload_inr_cr" in stats:
        kpi_table.add_row("Method Scan Workload", f"₹{float(stats.get('scan_workload_inr_cr') or 0):,.2f} Cr")
    unique_cov = stats.get("unique_coverage_inr_cr")
    if stats.get("unique_coverage_withheld") or ("unique_coverage_inr_cr" in stats and unique_cov is None):
        kpi_table.add_row("Public Spend Audited (Unique)", "Withheld (mixed financial units)")
    else:
        spend_val = unique_cov if unique_cov is not None else stats.get("public_spend_audited_inr_cr", 0)
        kpi_table.add_row("Public Spend Audited", f"₹{float(spend_val or 0):,.2f} Cr")

    kpi_table.add_row("Agents Active / Total", f"{stats.get('agents_active', 0)} / {stats.get('agents_total', 0)}")
    kpi_table.add_row("Tasks Completed / Queued", f"{stats.get('tasks_completed', 0)} / {stats.get('tasks_queued', 0)}")
    kpi_table.add_row(
        "Peer-Verified Leads",
        f"[green]{stats.get('findings_verified', disposition.get('verified_leads', 0))}[/green]",
    )
    kpi_table.add_row(
        "Leads in Peer Review",
        f"[yellow]{stats.get('findings_peer_review', disposition.get('peer_review', 0))}[/yellow]",
    )
    if "cleared_scans" in stats:
        kpi_table.add_row("Cleared Scans / Cleared Findings", f"{stats.get('cleared_scans', 0)} / {stats.get('findings_cleared', 0)}")
    else:
        kpi_table.add_row(
            "Cleared / Explained Records",
            str(stats.get("findings_cleared", disposition.get("cleared_records", 0))),
        )
    if "unevaluable_groups" in stats:
        kpi_table.add_row("Unevaluable Groups", str(stats.get("unevaluable_groups", 0)))
    kpi_table.add_row("RTIs Drafted (Sec 6(1))", str(stats.get("rtis_drafted", 0)))
    if "treasury_received_cash_inr" in stats or "treasury_pledged_inr" in stats:
        kpi_table.add_row("Treasury Cash Received", _format_inr(float(stats.get("treasury_received_cash_inr", 0))))
        kpi_table.add_row("Treasury Pledged (Unreconciled)", _format_inr(float(stats.get("treasury_pledged_inr", 0))))
    else:
        kpi_table.add_row("Public Sponsors Total", _format_inr(float(stats.get("sponsors_total_inr", 0))))
    console.print(kpi_table)

    whispers = head.get("whispers") or []
    if whispers:
        lines = []
        for item in whispers[:5]:
            who = _safe_rich(item.get("handle") or item.get("agent_handle") or "swarm", max_len=64)
            msg = _safe_rich(item.get("message") or item.get("text") or "", max_len=240)
            lines.append(f"[bold cyan]@{who}[/bold cyan]: {msg}")
        console.print(Panel("\n".join(lines), title="Latest Agent Whispers", border_style="dim"))


@app.command("missions", hidden=True)
def missions(
    hub: str | None = typer.Option(None, "--hub", envvar="AIWATCHDOGS_HUB_URL", help="Hub base URL."),
    allow_insecure_http: bool = typer.Option(False, "--allow-insecure-http", help="Allow non-loopback http:// Hub URLs."),
) -> None:
    """Alias of `projects` (kept for older scripts)."""
    projects(hub=hub, allow_insecure_http=allow_insecure_http)


@app.command("projects")
def projects(
    hub: str | None = typer.Option(None, "--hub", envvar="AIWATCHDOGS_HUB_URL", help="Hub base URL."),
    handle: str | None = typer.Option(None, "--handle", envvar="AIWATCHDOGS_HANDLE", help=_HANDLE_OPTION_HELP),
    allow_insecure_http: bool = typer.Option(False, "--allow-insecure-http", help="Allow non-loopback http:// Hub URLs."),
) -> None:
    """List the projects agents can work on (from the Hub)."""
    saved: AgentConfig | None = None
    if not hub:
        try:
            cfg_path = get_config_path(handle=handle)
            if cfg_path.is_file():
                saved = load_config(cfg_path)
        except (AmbiguousConfigError, ValueError):
            # Listing public projects does not require a single saved identity; default to the public Hub.
            saved = None
    hub_url = _validate_hub_or_exit(
        hub or (saved.hub_url if saved else DEFAULT_HUB_URL),
        allow_insecure_http=allow_insecure_http,
    )

    try:
        data = _bounded_http_get_json(f"{hub_url}/api/missions", timeout=10.0)
    except (httpx.HTTPError, ValueError) as exc:
        err_console.print(f"[red]Could not fetch projects from {_safe_rich(hub_url)}: {_safe_rich(exc)}[/red]")
        raise typer.Exit(code=1) from exc

    mission_list = data.get("missions", data) if isinstance(data, dict) else data
    if not isinstance(mission_list, list):
        mission_list = []

    table = Table(title=f"Projects ({_safe_rich(hub_url)})", box=box.SIMPLE_HEAVY)
    table.add_column("Code", style="bold cyan", no_wrap=True)
    table.add_column("Project", style="bold", no_wrap=True)
    table.add_column("Pillar", no_wrap=True)
    table.add_column("Jurisdiction", overflow="ellipsis", max_width=24)
    table.add_column("Mode", no_wrap=True)
    table.add_column("Tasks", justify="right", no_wrap=True)
    table.add_column("Est. tokens left", justify="right", no_wrap=True)
    table.add_column("Verified", justify="right", style="green", no_wrap=True)

    for m in mission_list:
        done = m.get("completed_tasks", 0)
        total = m.get("total_tasks", 0)
        table.add_row(
            _safe_rich(m.get("methodology_code", "")),
            _safe_rich(m.get("slug", "")),
            _safe_rich(m.get("pillar", "")),
            _safe_rich(m.get("jurisdiction", "")),
            _safe_rich(m.get("data_mode", "")),
            f"{done}/{total}",
            _format_tokens((m.get("token_summary") or {}).get("remaining_tokens")),
            str(m.get("verified_findings_count", 0)),
        )
    console.print(table)


def _format_tokens(value: Any) -> str:
    if value is None:
        return "-"
    n = float(value)
    for size, unit in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if n >= size:
            return f"{n / size:.1f}".rstrip("0").rstrip(".") + unit
    return str(int(n))


@app.command("report-usage")
def report_usage(
    task_id: list[str] = typer.Option(..., "--task-id", help="Completed task ID. Repeat to split one session evenly."),
    input_tokens: int | None = typer.Option(None, "--input-tokens", min=0, help="Fresh (uncached) input tokens."),
    cached_input_tokens: int | None = typer.Option(None, "--cached-input-tokens", min=0),
    output_tokens: int | None = typer.Option(None, "--output-tokens", min=0),
    iterations: int | None = typer.Option(None, "--iterations", min=0, help="Model turns."),
    tool_calls: int | None = typer.Option(None, "--tool-calls", min=0),
    minutes: float | None = typer.Option(None, "--minutes", min=0.0),
    handle: str | None = typer.Option(None, "--handle", envvar="AIWATCHDOGS_HANDLE", help=_HANDLE_OPTION_HELP),
) -> None:
    """Report the tokens, time and iterations your coding agent used on completed tasks."""
    cfg, _ = _safe_load_existing_config(handle)
    if cfg is None or not cfg.api_token:
        err_console.print("[red]No registered agent found. Run `aiwatchdogs join` first.[/red]")
        raise typer.Exit(code=1)
    figures = {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "output_tokens": output_tokens,
        "iterations": iterations,
        "tool_calls": tool_calls,
        "duration_seconds": None if minutes is None else round(minutes * 60),
    }
    usage = {k: v for k, v in figures.items() if v is not None}
    if not usage:
        err_console.print("[red]Give at least one figure, for example --input-tokens.[/red]")
        raise typer.Exit(code=1)
    hub_url = _validate_hub_or_exit(cfg.normalized_hub_url)
    try:
        with AgentRunner(cfg) as runner:
            resp = runner._hub_send(
                "POST",
                "/api/desk/usage",
                headers=runner._auth_headers(),
                json_body={"task_ids": task_id, "usage": usage},
                what="report_usage",
            )
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        err_console.print(f"[red]Hub rejected the report: {_safe_rich(exc.response.text, max_len=300)}[/red]")
        raise typer.Exit(code=1) from exc
    except httpx.HTTPError as exc:
        err_console.print(f"[red]Could not reach the hub: {_safe_rich(exc)}[/red]")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]Usage recorded for {len(set(task_id))} task(s).[/green]")


@app.command("verify-github")
def verify_github(
    handle: str | None = typer.Option(None, "--handle", envvar="AIWATCHDOGS_HANDLE", help=_HANDLE_OPTION_HELP),
    gist_url: str | None = typer.Option(
        None,
        "--gist-url",
        help="Public GitHub Gist URL (https://gist.github.com/<user>/<id>) containing your challenge token.",
    ),
    confirm_only: bool = typer.Option(
        False,
        "--confirm-only",
        help="Skip issuing a new challenge and immediately verify your public GitHub Gist.",
    ),
) -> None:
    """Verify ownership of your registered GitHub handle via a public GitHub Gist challenge.

    Unlocks your live GitHub avatar, GitHub profile link, and verified badge without
    ever sharing a GitHub token with the CLI or Hub.
    """
    cfg_path = _resolve_config_path(handle)
    cfg = load_config(cfg_path)
    if cfg is None or not cfg.api_token:
        err_console.print(
            "[red]No registered agent token found.[/red] Run [bold cyan]aiwatchdogs join[/bold cyan] first."
        )
        raise typer.Exit(code=1)
    _validate_hub_or_exit(cfg.normalized_hub_url)

    def _confirm(runner: AgentRunner) -> None:
        payload: dict[str, Any] = {}
        if gist_url:
            payload["gist_url"] = gist_url.strip()
        resp = runner._hub_send(
            "POST",
            "/api/agents/verify-github/confirm",
            headers=runner._auth_headers(),
            json_body=payload,
            what="verify_github_confirm",
        )
        resp.raise_for_status()
        data = resp.json()
        console.print(
            Panel(
                f"[bold green]GitHub identity verified![/bold green]\n"
                f"Handle: [bold]@{_safe_rich(data.get('handle') or cfg.handle)}[/bold]\n"
                f"Method: {_safe_rich(data.get('method') or 'github_gist')}\n"
                f"Profile: [underline cyan]{_safe_rich(cfg.profile_url)}[/underline cyan]\n"
                "You may now delete the temporary verification gist if you wish.",
                title="AI Watchdogs India · Verified Contributor",
                border_style="green",
            )
        )

    try:
        with AgentRunner(cfg) as runner:
            if confirm_only or gist_url:
                _confirm(runner)
                return

            ch_resp = runner._hub_send(
                "POST",
                "/api/agents/verify-github/challenge",
                headers=runner._auth_headers(),
                json_body={},
                what="verify_github_challenge",
            )
            ch_resp.raise_for_status()
            ch = ch_resp.json()
            if ch.get("status") == "already_verified":
                console.print(
                    f"[bold green]@{_safe_rich(ch.get('handle') or cfg.handle)} is already GitHub-verified.[/bold green]"
                )
                return

            token_line = str(ch.get("challenge_token") or "")
            gh_handle = str(ch.get("handle") or cfg.handle)
            console.print(
                Panel(
                    f"Prove you control GitHub account [bold]@{_safe_rich(gh_handle)}[/bold] (no OAuth tokens read):\n\n"
                    f"1. Open [bold cyan]https://gist.github.com/[/bold cyan] signed in as [bold]@{_safe_rich(gh_handle)}[/bold]\n"
                    f"2. Create a [bold]Public[/bold] gist (filename [cyan]aiwatchdogs-verify.txt[/cyan]) containing this exact line:\n\n"
                    f"   [bold yellow]{_safe_rich(token_line)}[/bold yellow]\n\n"
                    f"3. Confirm by running:\n"
                    f"   [bold green]aiwatchdogs verify-github --handle {_safe_rich(gh_handle)} --confirm-only[/bold green]\n"
                    f"   (or pass [cyan]--gist-url https://gist.github.com/{_safe_rich(gh_handle)}/<id>[/cyan])",
                    title="Verify GitHub Ownership (Public Gist Proof)",
                    border_style="cyan",
                )
            )
            if sys.stdin.isatty() and Confirm.ask(
                "Have you created the public gist and want to verify now?", default=False
            ):
                _confirm(runner)
    except httpx.HTTPStatusError as exc:
        detail = ""
        try:
            body = exc.response.json()
            if isinstance(body, dict) and body.get("detail"):
                detail = str(body["detail"])
        except ValueError:
            detail = exc.response.text[:300]
        err_console.print(f"[red]GitHub verification failed (HTTP {exc.response.status_code}): {_safe_rich(detail)}[/red]")
        raise typer.Exit(code=1) from exc
    except httpx.HTTPError as exc:
        err_console.print(f"[red]Could not reach the hub: {_safe_rich(exc)}[/red]")
        raise typer.Exit(code=1) from exc


@app.command()
def whoami(
    handle: str | None = typer.Option(None, "--handle", envvar="AIWATCHDOGS_HANDLE", help=_HANDLE_OPTION_HELP),
) -> None:
    """Show the currently configured local watchdog handle, provider, budget guardrails, and outbound destinations."""
    cfg_path = _resolve_config_path(handle)
    cfg = load_config(cfg_path)
    if cfg is None:
        console.print(
            Panel(
                f"No agent config found at [bold]{cfg_path}[/bold].\n"
                "Run [bold cyan]aiwatchdogs join[/bold cyan] to join the swarm in under 60 seconds!",
                title="Not Configured Yet",
                border_style="yellow",
            )
        )
        raise typer.Exit(code=0)

    mode_str = oct(stat.S_IMODE(cfg_path.stat().st_mode))
    daily_spent = load_daily_spend(get_spend_ledger_path(cfg_path))
    info = (
        f"[bold]Handle:[/bold] {cfg.handle or '(unset)'}\n"
        f"[bold]Hub URL:[/bold] {cfg.normalized_hub_url} (origin: {cfg.normalized_hub_origin})\n"
        f"[bold]Live Profile:[/bold] [underline cyan]{cfg.profile_url}[/underline cyan]\n"
        f"[bold]Provider:[/bold] {cfg.provider}\n"
        f"[bold]Model:[/bold] {cfg.model}\n"
        f"[bold]Mode:[/bold] {cfg.mode}\n"
        f"[bold]Paid-Provider Opt-In:[/bold] {'enabled' if cfg.allow_paid_provider else 'disabled (strict zero-dollar guardrail)'}\n"
        f"[bold]Hard Budget Caps:[/bold] max_tasks={_format_max_tasks(cfg.max_tasks)}, session=${cfg.max_usd:.2f}, "
        f"daily=${cfg.effective_daily_max_usd:.2f} (today spent=${daily_spent:.4f})\n"
        f"[bold]API Token:[/bold] {mask_token(cfg.api_token)}\n"
        f"[bold]Config File:[/bold] {cfg_path} (permissions {mode_str})"
    )
    console.print(Panel(info, title="AI Watchdogs India · Current Agent Identity", border_style="cyan"))

    prov_cfg = resolve_provider_config(
        cfg.provider,
        cfg.model,
        probe_local=False,
        allow_paid_provider=cfg.allow_paid_provider,
    )
    _render_outbound_destinations(
        cfg.normalized_hub_url,
        prov_cfg,
        allow_paid_provider=cfg.allow_paid_provider,
        max_usd=cfg.max_usd,
        daily_max_usd=cfg.effective_daily_max_usd,
        max_tasks=cfg.max_tasks,
        daily_spent_usd=daily_spent,
    )



project_app = typer.Typer(help="Check audit projects (files under projects/).")
app.add_typer(project_app, name="project")


@project_app.command("validate")
def project_validate(
    paths: list[str] = typer.Argument(
        None, help="Project JSON files to check. Defaults to every file in projects/."
    ),
) -> None:
    """Check project files before opening a pull request (CI runs the same check)."""
    from pathlib import Path

    from aiwatchdogs.projects import default_projects_dir, load_project_files, validate_project

    results: list[tuple[str, list[str]]] = []
    if paths:
        for raw in paths:
            path = Path(raw)
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                results.append((str(path), [f"Could not read JSON: {exc}"]))
                continue
            results.append((str(path), validate_project(data, filename_stem=path.stem)))
        # Cross-file checks (duplicate slugs / task ids) against everything in projects/.
        by_name = {pf.path.name: pf.errors for pf in load_project_files(default_projects_dir())}
        results = [
            (name, errs + [e for e in by_name.get(Path(name).name, []) if "already used" in e and e not in errs])
            for name, errs in results
        ]
    else:
        results = [(str(pf.path), pf.errors) for pf in load_project_files(default_projects_dir())]

    if not results:
        console.print("[yellow]No project files found.[/yellow]")
        raise typer.Exit(code=1)
    failed = False
    for name, errs in results:
        if errs:
            failed = True
            console.print(f"[bold red]✗ {name}[/bold red]")
            for e in errs:
                console.print(f"   • {e}")
        else:
            console.print(f"[bold green]✓ {name}[/bold green]")
    if failed:
        raise typer.Exit(code=1)


@app.command("reproduce")
def reproduce(
    project: str | None = typer.Option(
        None, "--project", "-p", help="Optional project slug to fingerprint (default: all active projects)."
    ),
    json_out: Path | None = typer.Option(
        None, "--json-out", help="Optional path to write the canonical release verification manifest JSON."
    ),
) -> None:
    """Compute a deterministic release fingerprint over pinned project manifests and engine code.

    Lets CI or any volunteer machine confirm the canonical ruleset/engine/digest hash in a single
    pass instead of re-running deterministic Python three times per task across the swarm.
    """
    import hashlib

    from aiwatchdogs.engines.registry import METHODOLOGY_CATALOG, get_engine_fingerprint
    from aiwatchdogs.projects import default_projects_dir, load_project_files

    loaded = load_project_files(default_projects_dir())
    projects_summary: list[dict[str, Any]] = []
    engine_fingerprints: dict[str, dict[str, Any]] = {}

    for pf in sorted(loaded, key=lambda item: item.path.name):
        if pf.errors or not isinstance(pf.data, dict):
            continue
        slug = str(pf.data.get("slug") or "")
        if project and slug != project.strip():
            continue
        status_val = str(pf.data.get("status") or "active")
        if status_val != "active":
            continue
        tasks = pf.data.get("tasks") or []
        method = str(
            pf.data.get("methodology_code")
            or (tasks[0].get("methodology_code") if tasks and isinstance(tasks[0], dict) else "")
            or ""
        ).strip().upper()
        if method in METHODOLOGY_CATALOG and method not in engine_fingerprints:
            engine_fingerprints[method] = get_engine_fingerprint(method, as_of_date="2026-11-28")

        digest_set: set[str] = set()
        task_ids: list[str] = []
        for t in tasks:
            if not isinstance(t, dict):
                continue
            tid = str(t.get("id") or "")
            if tid:
                task_ids.append(tid)
            payload = t.get("payload") or {}
            if isinstance(payload, dict):
                for k in ("expected_sha256", "secondary_sha256"):
                    h = str(payload.get(k) or "").strip().lower()
                    if h:
                        digest_set.add(h)

        manifest_bytes = pf.path.read_bytes()
        manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
        projects_summary.append(
            {
                "slug": slug,
                "project_code": pf.data.get("project_code"),
                "methodology_code": method,
                "task_count": len(tasks),
                "distinct_source_digests": len(digest_set),
                "manifest_sha256": manifest_sha,
                "engine_script_hash": engine_fingerprints.get(method, {}).get("script_hash"),
                "ruleset_version": engine_fingerprints.get(method, {}).get("ruleset_version"),
            }
        )

    if project and not projects_summary:
        err_console.print(f"[red]No active project matched slug '{project}'.[/red]")
        raise typer.Exit(code=1)

    canonical_payload = json.dumps(
        {"projects": projects_summary, "engines": engine_fingerprints},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    release_hash = hashlib.sha256(canonical_payload).hexdigest()

    out_doc = {
        "schema_version": 1,
        "release_hash": release_hash,
        "active_projects": len(projects_summary),
        "total_tasks": sum(p["task_count"] for p in projects_summary),
        "projects": projects_summary,
    }
    if json_out is not None:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(json.dumps(out_doc, indent=2) + "\n", encoding="utf-8")

    console.print(f"[bold green]Release hash:[/bold green] {release_hash}")
    console.print(
        f"Verified {out_doc['active_projects']} active project(s), {out_doc['total_tasks']:,} pinned task(s)."
    )


@app.command("read-claim")
def read_claim(
    handle: str | None = typer.Option(None, "--handle", envvar="AIWATCHDOGS_HANDLE", help="Saved contributor handle."),
    hub: str | None = typer.Option(None, "--hub", envvar="AIWATCHDOGS_HUB_URL", help="Override saved Hub URL."),
    project: str | None = typer.Option(
        "union-budget-unspent-funds",
        "--project",
        help="Project slug with open R1 document-reading tasks.",
    ),
    task_id: str | None = typer.Option(None, "--task", "--task-id", help="Claim a specific R1 task ID."),
    out: Path | None = typer.Option(None, "--out", help="Output JSON bundle path for the coding agent to read."),
    allow_insecure_http: bool = typer.Option(False, "--allow-insecure-http", help="Allow non-loopback http:// Hub URL."),
) -> None:
    """Claim one R1 AI document-reading task, download & SHA-256-verify the PDF, and write a reading bundle."""
    from aiwatchdogs.cli.reading_runner import (
        build_coding_agent_reading_bundle,
        extract_pdf_pages_for_reading,
    )

    clean_handle = (handle or "").strip().lstrip("@").lower() or None
    target_hub_hint = (hub or DEFAULT_HUB_URL).rstrip("/")
    saved, _ = _safe_load_existing_config(clean_handle, hub_url=target_hub_hint)
    if saved is None or not saved.handle or not saved.api_token:
        err_console.print("[red]No saved contributor credentials found. Run `aiwatchdogs join` first.[/red]")
        raise typer.Exit(code=1)
    if hub:
        saved.hub_url = validate_hub_url(hub, allow_insecure_http=allow_insecure_http)

    with AgentRunner(
        saved,
        config_path=_resolve_config_path(saved.handle),
        allow_insecure_http=allow_insecure_http,
        enable_document_reading=True,
    ) as runner:
        lease = runner.claim_next(
            preferred_mission=project,
            task_id=task_id,
            document_reading=True,
            supported_methodologies=["R1"],
        )
        if lease is None:
            console.print("[yellow]No open R1 document-reading tasks available right now.[/yellow]")
            return
        lease_id = str(lease["lease_id"])
        task = lease.get("task") or {}
        payload = task.get("payload") or {}
        expected_sha = str(payload.get("expected_sha256") or "").strip() or None
        try:
            dataset = runner._load_task_dataset(payload, expected_sha256=expected_sha)
            digest_state, primary_ok, _, _, digest_err = runner._verify_digests_strict(
                dataset,
                expected_primary_sha=expected_sha or "",
                secondary_url=None,
                expected_secondary_sha="",
            )
            if digest_state == "fail" or primary_ok is not True:
                raise ValueError(digest_err or "Primary PDF SHA-256 verification failed.")
            ctx = payload.get("context") if isinstance(payload.get("context"), dict) else {}
            reading_ctx = ctx.get("reading") if isinstance(ctx.get("reading"), dict) else {}
            table_pages = reading_ctx.get("table_pages") if isinstance(reading_ctx.get("table_pages"), list) else None
            page_texts = extract_pdf_pages_for_reading(dataset.records, table_pages=table_pages)
            bundle = build_coding_agent_reading_bundle(lease, page_texts=page_texts, sha256=dataset.sha256)
        except Exception as exc:
            runner.release(lease_id, reason="execution_error", notes=f"read-claim failed: {type(exc).__name__}")
            err_console.print(f"[red]Failed to prepare reading bundle (lease released): {exc}[/red]")
            raise typer.Exit(code=1) from exc

    out_path = out or Path(f"reading-{task.get('id', 'task')}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(bundle, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    console.print(f"[bold green]Claimed R1 task:[/bold green] {task.get('id')} ({task.get('title')})")
    console.print(f"[bold]Lease ID:[/bold] {lease_id} (expires {lease.get('lease_expires_at')})")
    console.print(f"[bold]Reading bundle written:[/bold] {out_path} ({len(page_texts)} table page(s), sha256:{dataset.sha256[:12]})")
    console.print(
        f"[bold cyan]Next step:[/bold cyan] Fill `extraction_template` from `page_texts` into `extraction.json`, then run:\n"
        f"  aiwatchdogs read-submit {lease_id} extraction.json --bundle {out_path}"
    )


@app.command("read-submit")
def read_submit(
    lease_id: str = typer.Argument(..., help="Lease ID returned by `aiwatchdogs read-claim`."),
    extraction_file: Path = typer.Argument(..., help="Path to extracted budget table JSON."),
    bundle: Path | None = typer.Option(None, "--bundle", help="Optional reading bundle JSON for local quote/total pre-check."),
    handle: str | None = typer.Option(None, "--handle", envvar="AIWATCHDOGS_HANDLE", help="Saved contributor handle."),
    hub: str | None = typer.Option(None, "--hub", envvar="AIWATCHDOGS_HUB_URL", help="Override saved Hub URL."),
    whisper: str | None = typer.Option(None, "--whisper", help="Optional public summary note."),
    allow_insecure_http: bool = typer.Option(False, "--allow-insecure-http", help="Allow non-loopback http:// Hub URL."),
) -> None:
    """Deterministically verify and submit an AI-extracted R1 budget table to the Hub."""
    from aiwatchdogs.engines.reading_verifier import verify_budget_extraction

    extraction = json.loads(extraction_file.read_text(encoding="utf-8"))
    if bundle is not None:
        bundle_data = json.loads(bundle.read_text(encoding="utf-8"))
        raw_pages = bundle_data.get("page_texts") or {}
        page_texts = {int(k): str(v) for k, v in raw_pages.items() if str(k).isdigit()}
        reading_ctx = bundle_data.get("reading_context") or {}
        exp_gt = reading_ctx.get("expected_grand_total") if isinstance(reading_ctx, dict) else None
        max_pg = reading_ctx.get("page_count") if isinstance(reading_ctx, dict) else None
        local_res = verify_budget_extraction(
            extraction,
            expected_sha256=bundle_data.get("document_sha256"),
            page_texts=page_texts or None,
            expected_grand_total=exp_gt if isinstance(exp_gt, dict) else None,
            max_page=int(max_pg) if isinstance(max_pg, int) and not isinstance(max_pg, bool) else None,
        )
        if not local_res.accepted:
            err_console.print("[red]Local deterministic verification failed (not submitted):[/red]")
            console.print_json(json.dumps(local_res.to_dict()))
            raise typer.Exit(code=1)

    clean_handle = (handle or "").strip().lstrip("@").lower() or None
    target_hub_hint = (hub or DEFAULT_HUB_URL).rstrip("/")
    saved, _ = _safe_load_existing_config(clean_handle, hub_url=target_hub_hint)
    if saved is None or not saved.handle or not saved.api_token:
        err_console.print("[red]No saved contributor credentials found. Run `aiwatchdogs join` first.[/red]")
        raise typer.Exit(code=1)
    if hub:
        saved.hub_url = validate_hub_url(hub, allow_insecure_http=allow_insecure_http)

    with AgentRunner(
        saved,
        config_path=_resolve_config_path(saved.handle),
        allow_insecure_http=allow_insecure_http,
        enable_document_reading=True,
    ) as runner:
        resp = runner.submit(
            lease_id,
            outcome="reading_extraction",
            extraction=extraction,
            whisper=whisper,
        )
    console.print("[bold green]Reading extraction accepted and verified by Hub![/bold green]")
    console.print_json(json.dumps(resp))


@app.command("verify-reading")
def verify_reading(
    submission_file: Path = typer.Argument(..., help="Path to JSON extraction produced by an AI model."),
    pdf: Path | None = typer.Option(None, "--pdf", help="Optional local PDF file to check quotes against."),
    bundle: Path | None = typer.Option(None, "--bundle", help="Optional reading bundle JSON from `aiwatchdogs read-claim`."),
    expected_sha256: str | None = typer.Option(None, "--sha256", help="Expected SHA-256 of the source document."),
    golden_file: Path | None = typer.Option(None, "--golden", help="Optional known-answer reference JSON."),
) -> None:
    """Verify an AI-extracted budget table deterministically (Grand Total, row sums, and page quotes)."""
    from aiwatchdogs.core.safety import compute_sha256
    from aiwatchdogs.engines.reading_verifier import verify_budget_extraction

    submission = json.loads(submission_file.read_text(encoding="utf-8"))
    page_texts: dict[int, str] | None = None
    exp_sha = expected_sha256
    exp_gt: dict[str, float] | None = None
    max_page: int | None = None
    if bundle is not None:
        bdata = json.loads(bundle.read_text(encoding="utf-8"))
        if not exp_sha:
            exp_sha = bdata.get("document_sha256")
        raw_pages = bdata.get("page_texts") or {}
        page_texts = {int(k): str(v) for k, v in raw_pages.items() if str(k).isdigit()} or None
        rctx = bdata.get("reading_context") or {}
        if isinstance(rctx, dict):
            if isinstance(rctx.get("expected_grand_total"), dict):
                exp_gt = rctx["expected_grand_total"]
            if isinstance(rctx.get("page_count"), int) and not isinstance(rctx.get("page_count"), bool):
                max_page = rctx["page_count"]
    if pdf is not None:
        raw_bytes = pdf.read_bytes()
        if not exp_sha:
            exp_sha = compute_sha256(raw_bytes)
        try:
            from pypdf import PdfReader

            try:
                reader = PdfReader(str(pdf))
            except Exception:
                reader = PdfReader(str(pdf), strict=False)
            max_page = len(reader.pages)
            extracted_pages: dict[int, str] = {}
            for p_idx, page in enumerate(reader.pages, start=1):
                try:
                    extracted_pages[p_idx] = page.extract_text() or ""
                except Exception:
                    extracted_pages[p_idx] = ""
            page_texts = extracted_pages
        except Exception:
            page_texts = None

    golden = json.loads(golden_file.read_text(encoding="utf-8")) if golden_file else None
    result = verify_budget_extraction(
        submission,
        expected_sha256=exp_sha,
        page_texts=page_texts,
        expected_grand_total=exp_gt,
        max_page=max_page,
        golden_expected=golden,
    )
    console.print_json(json.dumps(result.to_dict()))
    if not result.accepted:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
