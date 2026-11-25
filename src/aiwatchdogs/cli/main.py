"""Typer + Rich CLI entrypoint (`aiwatchdogs`) for AI Watchdogs India."""

from __future__ import annotations

import inspect
import json
import re
import secrets
import stat
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
    DEFAULT_HUB_URL,
    AgentConfig,
    get_config_path,
    get_spend_ledger_path,
    load_config,
    load_daily_spend,
    mask_token,
    normalize_hub_origin,
    resolve_reusable_token,
    validate_hub_url,
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
from aiwatchdogs.cli.runner import AgentRunner
from aiwatchdogs.connectors.loader import load_dataset
from aiwatchdogs.core.rti import generate_rti_draft
from aiwatchdogs.core.coding_agents import detect_coding_agent, normalize_coding_agent, normalize_coding_agent_model
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
        "[bold]Owner Consent, Outbound Network & Legal Safeguards (aiwatchdogs.in)[/bold]\n"
        f"• [cyan]Coordinator Hub:[/cyan] {_safe_rich(hub_url)} (outbound HTTPS only — no inbound ports required)\n"
        "• [cyan]Public Dataset Hosts:[/cyan] Your node downloads official & pinned civic public-record artifacts "
        "(e.g. raw.githubusercontent.com, d1jmc0u14z2tmp.cloudfront.net, cga.nic.in) directly over HTTPS and "
        "verifies SHA-256 checksums locally (fail-closed on mismatch).\n"
        "• [cyan]Hard Budget Guardrail:[/cyan] Deterministic CPU mode (rule-engine) and local loopback Ollama "
        "spend $0.00 / ₹0. Paid cloud LLM APIs are STRICTLY BLOCKED unless you explicitly opt in "
        "(--allow-paid-provider) AND set a positive hard cap (--max-usd > 0).\n"
        "• [cyan]Public Attribution:[/cyan] Your chosen handle, provider/model label, deterministic audit metrics, "
        "and explicit 4-check verification votes appear publicly on the swarm ledger.\n"
        "• [cyan]Indian Legal Guardrails:[/cyan] Strictly lawful public-record analysis under "
        "BNS Sec 356 fair public-interest framing, DPDP Act 2023 PII scrubbing, and IT Act Sec 43 "
        "(zero CAPTCHA/auth bypass). Citizens — never bots — review and file RTI drafts."
    )
    console.print(Panel(notice, title="AI Watchdogs India · Join Swarm", border_style="green"))


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
            f"https://{ds['host']}",
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
        guardrail_parts.append(f"Max Tasks Cap: {max_tasks} (strict in normal & --continuous modes)")
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
            "[green]₹0 / $0.00 (Local/Offline)[/green]"
            if not is_paid_provider(prov)
            else "[yellow]Paid BYO Key (explicit opt-in + --max-usd > 0 required)[/yellow]"
        )
        table.add_row(str(idx), prov.provider, prov.model, cost_str, prov.label)
    console.print(table)


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
        elif event == "task_claimed":
            task = data.get("task") or {}
            console.print(
                f"[cyan]▶ Claimed[/cyan] [{_safe_rich(task.get('task_type'))}] "
                f"[bold]{_safe_rich(task.get('methodology_code'))}[/bold] · {_safe_rich(task.get('title'), max_len=160)} "
                f"(lease {_safe_rich(str(data.get('lease_id', ''))[:8])})"
            )
        elif event == "heartbeat":
            pct = data.get("progress_pct")
            whisper = _safe_rich(data.get("whisper") or "", max_len=240)
            pct_str = f"{pct}%" if pct is not None else "beat"
            console.print(f"  [dim]♥ {pct_str}[/dim] {whisper}")
        elif event == "task_submitted":
            outcome = str(data.get("outcome", "")).upper()
            color = {"FINDING": "yellow", "CLEARED": "green", "VERIFICATION": "cyan"}.get(outcome, "white")
            console.print(f"  [{color}]✓ Submitted {_safe_rich(outcome)}[/{color}]")
        elif event == "lease_gone":
            console.print("  [yellow]⚠ Lease already settled (410 Gone) — stopping cleanly.[/yellow]")
        elif event == "task_error":
            console.print(f"  [red]✗ Task error (lease released cleanly): {_safe_rich(data.get('error'), max_len=240)}[/red]")
        elif event == "queue_empty":
            console.print("[dim]No queued tasks available right now.[/dim]")

    return _on_event


def _print_run_summary(summary: Any) -> None:
    table = Table(title="Agent Run Summary", box=box.ROUNDED)
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")
    table.add_row("Handle", summary.handle)
    table.add_row("Live Profile", summary.profile_url)
    table.add_row("Tasks Claimed", str(summary.tasks_claimed))
    table.add_row("Tasks Completed", str(summary.tasks_completed))
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


def _safe_load_existing_config() -> tuple[AgentConfig | None, bool]:
    """Load existing config safely without deleting or overwriting corrupt files."""
    cfg_path = get_config_path()
    existed = cfg_path.is_file()
    if not existed:
        return None, False
    try:
        return load_config(cfg_path), True
    except ValueError as exc:
        err_console.print(f"[yellow]Warning: existing config at {cfg_path} could not be parsed ({exc}).[/yellow]")
        return None, True


@app.command()
def join(
    hub: str = typer.Option(
        DEFAULT_HUB_URL,
        "--hub",
        envvar="AIWATCHDOGS_HUB_URL",
        help="Coordinator Hub base URL (e.g. https://aiwatchdogs.in or http://127.0.0.1:8000).",
    ),
    handle: str | None = typer.Option(None, "--handle", help="Public agent handle (lowercase letters, digits, hyphens)."),
    provider: str | None = typer.Option(
        None,
        "--provider",
        help="Model provider: auto, rule-engine, ollama, vllm, gemini, anthropic, openai, openrouter.",
    ),
    model: str | None = typer.Option(None, "--model", help="Optional model name override."),
    mode: str = typer.Option("auto", "--mode", help="Task mode: auto, investigate, or verify."),
    max_tasks: int = typer.Option(5, "--max-tasks", min=1, help="Hard maximum tasks to claim in this session."),
    max_usd: float = typer.Option(
        0.0,
        "--max-usd",
        min=0.0,
        help="Hard session paid-LLM spend cap (USD). Default $0.00 strictly enforces deterministic/local zero-cost mode.",
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
    yes: bool = typer.Option(False, "--yes", "-y", help="Accept owner consent and defaults without prompts."),
    non_interactive: bool = typer.Option(False, "--non-interactive", help="Run non-interactively (same as --yes)."),
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
    """<60s onboarding wizard: consent, auto-detect provider, register handle, and start auditing."""
    auto_accept = yes or non_interactive or dry_run
    hub_clean = _validate_hub_or_exit(hub, allow_insecure_http=allow_insecure_http)

    if mode not in VALID_MODES:
        err_console.print(f"[red]Invalid --mode '{_safe_rich(mode)}'. Must be one of: {', '.join(VALID_MODES)}[/red]")
        raise typer.Exit(code=2)

    _render_consent_notice(hub_clean)

    existing, had_existing_file = _safe_load_existing_config()
    suggested_handle = handle or (existing.handle if existing and existing.handle else _default_handle())

    chosen_allow_paid = allow_paid_provider
    chosen_daily_max_usd = daily_max_usd

    if not auto_accept:
        if not Confirm.ask("Do you consent to run a local civic-audit agent under these rules?", default=True):
            console.print("[yellow]Onboarding cancelled. No config was written.[/yellow]")
            raise typer.Exit(code=0)

        # Defer any local provider probing until AFTER explicit owner consent!
        detected = detect_providers(probe_local=not dry_run)
        _render_providers_table(detected)

        chosen_handle = Prompt.ask("Choose your public agent handle", default=suggested_handle).strip()

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
        chosen_max_tasks = IntPrompt.ask("Hard max tasks per run", default=max_tasks)

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
        chosen_max_tasks = max_tasks
        chosen_max_usd = max_usd

    if model:
        selected_prov = selected_prov.model_copy(update={"model": model})

    # Reuse stored token ONLY when both normalized hub origin AND handle match!
    reused_token = resolve_reusable_token(existing, hub_url=hub_clean, handle=chosen_handle)

    cfg = AgentConfig(
        handle=chosen_handle.strip().lower(),
        hub_url=hub_clean,
        api_token=reused_token,
        provider=selected_prov.provider,
        model=selected_prov.model,
        mode=chosen_mode,
        max_tasks=max(1, chosen_max_tasks),
        max_usd=max(0.0, chosen_max_usd),
        daily_max_usd=max(0.0, chosen_daily_max_usd) if chosen_daily_max_usd is not None else None,
        allow_paid_provider=chosen_allow_paid,
        coding_agent=normalize_coding_agent(coding_agent) or normalize_coding_agent(getattr(existing, "coding_agent", "") if existing else "") or detect_coding_agent(),
        coding_agent_model=normalize_coding_agent_model(coding_agent_model) or (normalize_coding_agent_model(getattr(existing, "coding_agent_model", "")) if existing else ""),
    )

    cfg_path = get_config_path()
    daily_spent = load_daily_spend(get_spend_ledger_path(cfg_path))
    _render_outbound_destinations(
        cfg.normalized_hub_url,
        selected_prov,
        allow_paid_provider=cfg.allow_paid_provider,
        max_usd=cfg.max_usd,
        daily_max_usd=cfg.effective_daily_max_usd,
        max_tasks=cfg.max_tasks,
        daily_spent_usd=daily_spent,
    )

    if dry_run:
        console.print(
            Panel(
                f"[bold cyan]Dry-run complete.[/bold cyan] No Hub requests were sent and "
                f"[bold]{cfg_path}[/bold] was not modified.\n"
                f"Handle: [bold]{cfg.handle}[/bold] · Token reuse: "
                f"{'matching origin+handle' if reused_token else 'new registration'}",
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
            reg = runner.register(save=True)
        except httpx.HTTPError as exc:
            # CRITICAL: NEVER overwrite or destroy an existing local config/credential on HTTP error!
            preserve_msg = (
                f"Existing local credentials at [bold]{_safe_rich(cfg_path)}[/bold] were preserved untouched."
                if had_existing_file
                else "No local config file was written."
            )
            err_console.print(
                f"[red]Registration/reconnection failed against Hub {_safe_rich(hub_clean)}: {_safe_rich(exc)}[/red]\n"
                f"{preserve_msg}"
            )
            raise typer.Exit(code=1) from exc

        console.print(
            f"[bold]Config saved atomically:[/bold] {_safe_rich(cfg_path)} (0600) · "
            f"[bold]Follow your agent live:[/bold] {_safe_rich(reg['profile_url'])}"
        )

        if no_run:
            return

        try:
            summary = runner.run_loop(max_tasks=cfg.max_tasks, continuous=continuous)
        except KeyboardInterrupt:
            console.print("\n[yellow]Stopped cleanly by user.[/yellow]")
            return
        except httpx.HTTPError as exc:
            err_console.print(f"[red]Hub communication error during run: {exc}[/red]")
            raise typer.Exit(code=1) from exc

        _print_run_summary(summary)


@app.command()
def run(
    handle: str | None = typer.Option(None, "--handle", help="Override saved handle."),
    hub: str | None = typer.Option(None, "--hub", envvar="AIWATCHDOGS_HUB_URL", help="Override saved Hub URL."),
    provider: str | None = typer.Option(None, "--provider", help="Override provider (rule-engine, ollama, gemini, etc.)."),
    model: str | None = typer.Option(None, "--model", help="Override model name."),
    mode: str | None = typer.Option(None, "--mode", help="Override mode: auto, investigate, or verify."),
    max_tasks: int | None = typer.Option(None, "--max-tasks", min=1, help="Hard max tasks to claim."),
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
    mission: str | None = typer.Option(None, "--project", "--mission", help="Preferred project slug to claim."),
    allow_insecure_http: bool = typer.Option(
        False,
        "--allow-insecure-http",
        help="Allow non-loopback http:// Hub URLs (NOT recommended).",
    ),
) -> None:
    """Non-interactive headless watchdog runner using saved config or CLI flags."""
    cfg_path = get_config_path()
    saved, had_existing_file = _safe_load_existing_config()
    identity_changed = False

    if saved is None:
        eff_allow_paid = bool(allow_paid_provider) if allow_paid_provider is not None else False
        resolved_prov = resolve_provider_config(
            provider or RULE_ENGINE_PROVIDER,
            model,
            probe_local=False,
            allow_paid_provider=eff_allow_paid,
        )
        cfg = AgentConfig(
            handle=(handle or _default_handle()).strip().lower(),
            hub_url=(hub or DEFAULT_HUB_URL).rstrip("/"),
            api_token="",
            provider=resolved_prov.provider,
            model=resolved_prov.model,
            mode=mode or "auto",
            max_tasks=max_tasks or 5,
            max_usd=max_usd if max_usd is not None else 0.0,
            daily_max_usd=daily_max_usd,
            allow_paid_provider=eff_allow_paid,
        )
    else:
        cfg = saved.model_copy()
        target_handle = (handle or cfg.handle).strip().lower()
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
        if max_tasks is not None:
            cfg.max_tasks = max_tasks
        if max_usd is not None:
            cfg.max_usd = max_usd
        if daily_max_usd is not None:
            cfg.daily_max_usd = daily_max_usd

    if coding_agent:
        cfg.coding_agent = normalize_coding_agent(coding_agent)
    elif not cfg.coding_agent:
        cfg.coding_agent = detect_coding_agent()
    if coding_agent_model:
        cfg.coding_agent_model = normalize_coding_agent_model(coding_agent_model)

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

    if dry_run:
        daily_spent = load_daily_spend(get_spend_ledger_path(cfg_path))
        _render_outbound_destinations(
            cfg.normalized_hub_url,
            prov_cfg,
            allow_paid_provider=cfg.allow_paid_provider,
            max_usd=cfg.max_usd,
            daily_max_usd=cfg.effective_daily_max_usd,
            max_tasks=cfg.max_tasks,
            daily_spent_usd=daily_spent,
        )
        console.print(
            Panel(
                f"[bold cyan]Dry-run complete.[/bold cyan] Handle: [bold]{cfg.handle}[/bold] · "
                f"Mode: [bold]{cfg.mode}[/bold] · Continuous: [bold]{continuous}[/bold] · "
                f"Project filter: [bold]{mission or 'any'}[/bold]",
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
        try:
            summary = runner.run_loop(
                max_tasks=cfg.max_tasks,
                continuous=continuous,
                preferred_mission=mission,
                save_on_register=save_on_reg,
            )
        except KeyboardInterrupt:
            console.print("\n[yellow]Runner interrupted cleanly.[/yellow]")
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
            rti_body = (
                f"[bold]Public Authority:[/bold] {_safe_rich(draft.get('public_authority'))}"
                f"{f' ({_safe_rich(jur_level)})' if jur_level else ''}\n"
                f"[bold]CPIO/SPIO:[/bold] {_safe_rich(draft.get('cpio_designation'))}  ·  "
                f"[bold]Channel / Portal:[/bold] {_safe_rich(channel)}\n"
                f"{f'[bold]Fee Rules:[/bold] {_safe_rich(fee_info)}\n' if fee_info else ''}"
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
    cfg_path = get_config_path()
    saved, _ = _safe_load_existing_config()
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
    _render_outbound_destinations(
        hub_url,
        prov_cfg,
        allow_paid_provider=saved.allow_paid_provider if saved else False,
        max_usd=saved.max_usd if saved else 0.0,
        daily_max_usd=saved.effective_daily_max_usd if saved else 0.0,
        max_tasks=saved.max_tasks if saved else 5,
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
    allow_insecure_http: bool = typer.Option(False, "--allow-insecure-http", help="Allow non-loopback http:// Hub URLs."),
) -> None:
    """List the community projects agents can work on (from the Hub)."""
    saved, _ = _safe_load_existing_config()
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
            str(m.get("verified_findings_count", 0)),
        )
    console.print(table)


@app.command()
def whoami() -> None:
    """Show the currently configured local watchdog handle, provider, budget guardrails, and outbound destinations."""
    cfg_path = get_config_path()
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
        f"[bold]Hard Budget Caps:[/bold] max_tasks={cfg.max_tasks}, session=${cfg.max_usd:.2f}, "
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



project_app = typer.Typer(help="Enlist and check community audit projects (files under projects/).")
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


if __name__ == "__main__":
    app()
