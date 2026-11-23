"""Typer + Rich CLI entrypoint (`aiwatchdogs`) for AI Watchdogs India."""

from __future__ import annotations

import getpass
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
from rich.panel import Panel
from rich.prompt import Confirm, FloatPrompt, IntPrompt, Prompt
from rich.table import Table

from aiwatchdogs.cli.config import (
    DEFAULT_HUB_URL,
    AgentConfig,
    get_config_path,
    load_config,
    mask_token,
    save_config,
)
from aiwatchdogs.cli.providers import (
    RULE_ENGINE_PROVIDER,
    ProviderInfo,
    detect_providers,
    resolve_provider_config,
)
from aiwatchdogs.cli.runner import AgentRunner
from aiwatchdogs.connectors.loader import load_dataset
from aiwatchdogs.core.rti import generate_rti_draft
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


def _default_handle() -> str:
    raw_user = getpass.getuser().lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", raw_user).strip("-")[:14] or "citizen"
    suffix = secrets.token_hex(2)
    return f"{cleaned}-{suffix}"


def _format_inr(amount: float) -> str:
    val = float(amount or 0.0)
    if val >= 1e7:
        return f"₹{val / 1e7:,.2f} Cr"
    if val >= 1e5:
        return f"₹{val / 1e5:,.2f} L"
    return f"₹{val:,.0f}"


def _render_consent_notice(hub_url: str) -> None:
    notice = (
        "[bold]Owner Consent & Legal Safeguards (aiwatchdogs.in)[/bold]\n"
        f"• [cyan]Hub:[/cyan] {hub_url} (outbound HTTPS only — no open ports required)\n"
        "• [cyan]Compute:[/cyan] Runs locally on your machine using your chosen provider "
        "(offline deterministic CPU mode uses ₹0 API cost; optional local/cloud LLMs obey your caps).\n"
        "• [cyan]Public Attribution:[/cyan] Your chosen handle, provider/model label, audit metrics, "
        "and verification votes appear publicly on the swarm ledger.\n"
        "• [cyan]Indian Legal Guardrails:[/cyan] Strictly lawful public-record analysis under "
        "BNS Sec 356 fair public-interest framing, DPDP Act 2023 PII scrubbing, and IT Act Sec 43 "
        "(zero CAPTCHA/auth bypass). Citizens — never bots — review and file RTI drafts."
    )
    console.print(Panel(notice, title="AI Watchdogs India · Join Swarm", border_style="green"))


def _render_providers_table(providers: list[ProviderInfo]) -> None:
    table = Table(title="Detected Audit Providers (<2s scan)", box=box.SIMPLE_HEAVY)
    table.add_column("#", style="bold cyan", width=3)
    table.add_column("Provider", style="bold")
    table.add_column("Model")
    table.add_column("Cost")
    table.add_column("Details")
    for idx, prov in enumerate(providers, start=1):
        cost_str = "[green]₹0 (Local/Offline)[/green]" if prov.zero_cost else "[yellow]BYO API Key[/yellow]"
        table.add_row(str(idx), prov.provider, prov.model, cost_str, prov.label)
    console.print(table)


def _make_event_printer() -> Any:
    def _on_event(event: str, data: dict[str, Any]) -> None:
        if event == "registered":
            console.print(
                Panel(
                    f"[bold green]Registered![/bold green] Handle: [bold]{data.get('handle')}[/bold]\n"
                    f"Follow your agent live: [bold underline cyan]{data.get('profile_url')}[/bold underline cyan]",
                    border_style="green",
                )
            )
        elif event == "task_claimed":
            task = data.get("task") or {}
            console.print(
                f"[cyan]▶ Claimed[/cyan] [{task.get('task_type')}] "
                f"[bold]{task.get('methodology_code')}[/bold] · {task.get('title')} "
                f"(lease {str(data.get('lease_id', ''))[:8]})"
            )
        elif event == "heartbeat":
            pct = data.get("progress_pct")
            whisper = data.get("whisper") or ""
            pct_str = f"{pct}%" if pct is not None else "beat"
            console.print(f"  [dim]♥ {pct_str}[/dim] {whisper}")
        elif event == "task_submitted":
            outcome = str(data.get("outcome", "")).upper()
            color = {"FINDING": "yellow", "CLEARED": "green", "VERIFICATION": "cyan"}.get(outcome, "white")
            console.print(f"  [{color}]✓ Submitted {outcome}[/{color}]")
        elif event == "lease_gone":
            console.print("  [yellow]⚠ Lease already settled (410 Gone) — stopping cleanly.[/yellow]")
        elif event == "task_error":
            console.print(f"  [red]✗ Task error (lease released cleanly): {data.get('error')}[/red]")
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
    if summary.aborted_410:
        table.add_row("Stopped on 410 Gone", str(summary.aborted_410))
    if summary.errors:
        table.add_row("Errors (Released)", f"[red]{summary.errors}[/red]")
    console.print(table)


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
    max_tasks: int = typer.Option(5, "--max-tasks", min=1, help="Maximum tasks to process in this session."),
    max_usd: float = typer.Option(1.0, "--max-usd", min=0.0, help="Advisory LLM spend cap (USD); ₹0 in rule-engine/local mode."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Accept owner consent and defaults without prompts."),
    non_interactive: bool = typer.Option(False, "--non-interactive", help="Run non-interactively (same as --yes)."),
    no_run: bool = typer.Option(False, "--no-run", help="Register & save config without immediately running tasks."),
    continuous: bool = typer.Option(False, "--continuous", help="Keep polling for tasks continuously."),
) -> None:
    """<60s onboarding wizard: consent, auto-detect provider, register handle, and start auditing."""
    auto_accept = yes or non_interactive
    hub_clean = hub.rstrip("/")

    if mode not in VALID_MODES:
        err_console.print(f"[red]Invalid --mode '{mode}'. Must be one of: {', '.join(VALID_MODES)}[/red]")
        raise typer.Exit(code=2)

    _render_consent_notice(hub_clean)

    detected = detect_providers(probe_local=True)
    _render_providers_table(detected)

    existing = load_config()
    suggested_handle = handle or (existing.handle if existing and existing.handle else _default_handle())

    if not auto_accept:
        if not Confirm.ask("Do you consent to run a local civic-audit agent under these rules?", default=True):
            console.print("[yellow]Onboarding cancelled. No config was written.[/yellow]")
            raise typer.Exit(code=0)

        chosen_handle = Prompt.ask("Choose your public agent handle", default=suggested_handle).strip()

        if provider is None:
            choice_idx = IntPrompt.ask(
                "Select provider number from table above",
                default=1,
            )
            clamped = max(1, min(choice_idx, len(detected)))
            selected_prov = detected[clamped - 1]
        else:
            selected_prov = resolve_provider_config(provider, model, probe_local=False)

        chosen_mode = Prompt.ask("Task mode", choices=list(VALID_MODES), default=mode)
        chosen_max_tasks = IntPrompt.ask("Max tasks per run", default=max_tasks)
        chosen_max_usd = FloatPrompt.ask("Max advisory LLM spend (USD)", default=max_usd)
    else:
        chosen_handle = suggested_handle
        if provider is None or provider.lower() == "auto":
            selected_prov = detected[0] if detected else resolve_provider_config(RULE_ENGINE_PROVIDER)
        else:
            selected_prov = resolve_provider_config(provider, model, probe_local=False)
        chosen_mode = mode
        chosen_max_tasks = max_tasks
        chosen_max_usd = max_usd

    if model:
        selected_prov = selected_prov.model_copy(update={"model": model})

    cfg = AgentConfig(
        handle=chosen_handle,
        hub_url=hub_clean,
        api_token="",
        provider=selected_prov.provider,
        model=selected_prov.model,
        mode=chosen_mode,
        max_tasks=max(1, chosen_max_tasks),
        max_usd=max(0.0, chosen_max_usd),
    )

    with AgentRunner(cfg, provider_cfg=selected_prov, on_event=_make_event_printer()) as runner:
        try:
            reg = runner.register(save=True)
        except httpx.HTTPError as exc:
            # Save config cleanly even if hub is currently offline so user doesn't lose setup.
            saved_path = save_config(cfg)
            err_console.print(
                f"[red]Could not reach Hub at {hub_clean}: {exc}[/red]\n"
                f"Saved local config to [bold]{saved_path}[/bold] (mode 0600). "
                "Start your local Hub or retry with `aiwatchdogs run`."
            )
            raise typer.Exit(code=1) from exc

        console.print(
            f"[bold]Config saved:[/bold] {get_config_path()} (0600) · "
            f"[bold]Follow your agent live:[/bold] {reg['profile_url']}"
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
    max_tasks: int | None = typer.Option(None, "--max-tasks", min=1, help="Max tasks to execute."),
    max_usd: float | None = typer.Option(None, "--max-usd", min=0.0, help="Advisory USD cap."),
    continuous: bool = typer.Option(False, "--continuous", help="Run continuously until interrupted."),
    mission: str | None = typer.Option(None, "--mission", help="Preferred mission slug to claim."),
) -> None:
    """Non-interactive headless watchdog runner using saved config or CLI flags."""
    saved = load_config()
    if saved is None:
        resolved_prov = resolve_provider_config(provider or RULE_ENGINE_PROVIDER, model, probe_local=False)
        cfg = AgentConfig(
            handle=handle or _default_handle(),
            hub_url=(hub or DEFAULT_HUB_URL).rstrip("/"),
            api_token="",
            provider=resolved_prov.provider,
            model=resolved_prov.model,
            mode=mode or "auto",
            max_tasks=max_tasks or 5,
            max_usd=max_usd if max_usd is not None else 1.0,
        )
    else:
        cfg = saved.model_copy()
        identity_changed = False
        if handle and handle != cfg.handle:
            cfg.handle = handle
            identity_changed = True
        if hub and hub.rstrip("/") != cfg.normalized_hub_url:
            cfg.hub_url = hub.rstrip("/")
            identity_changed = True
        if identity_changed:
            cfg.api_token = ""
        if provider:
            resolved_prov = resolve_provider_config(provider, model, probe_local=False)
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

    if cfg.mode not in VALID_MODES:
        err_console.print(f"[red]Invalid --mode '{cfg.mode}'. Must be one of: {', '.join(VALID_MODES)}[/red]")
        raise typer.Exit(code=2)

    prov_cfg = resolve_provider_config(cfg.provider, cfg.model, probe_local=False)

    with AgentRunner(cfg, provider_cfg=prov_cfg, on_event=_make_event_printer()) as runner:
        try:
            summary = runner.run_loop(
                max_tasks=cfg.max_tasks,
                continuous=continuous,
                preferred_mission=mission,
            )
        except KeyboardInterrupt:
            console.print("\n[yellow]Runner interrupted cleanly.[/yellow]")
            return
        except httpx.HTTPError as exc:
            err_console.print(f"[red]Failed to communicate with Hub at {cfg.normalized_hub_url}: {exc}[/red]")
            raise typer.Exit(code=1) from exc

    _print_run_summary(summary)


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
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON report to stdout."),
    rti: bool = typer.Option(False, "--rti", help="Generate lawful RTI Act 2005 Sec 6(1) draft(s) for detected leads."),
    authority: str | None = typer.Option(None, "--authority", help="Optional Public Authority override for RTI draft."),
) -> None:
    """Standalone civic-audit analyzer for official Government of India files & URLs (zero API key required)."""
    code = methodology.strip().upper()
    if code not in METHODOLOGY_CATALOG:
        valid = ", ".join(sorted(METHODOLOGY_CATALOG.keys()))
        err_console.print(f"[red]Unknown methodology '{methodology}'. Valid codes: {valid}[/red]")
        raise typer.Exit(code=2)

    load_ctx: dict[str, Any] | None = {"secondary_url": secondary_url} if secondary_url else None
    try:
        params = inspect.signature(load_dataset).parameters
        if "context" in params and load_ctx is not None:
            dataset = load_dataset(path_or_url, context=load_ctx)
        else:
            dataset = load_dataset(path_or_url)
    except Exception as exc:
        err_console.print(f"[red]Failed to load dataset '{path_or_url}': {exc}[/red]")
        raise typer.Exit(code=1) from exc

    result = run_engine(code, dataset.records, context=load_ctx)

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
        "byte_size": dataset.byte_size,
        "is_demo_sample": False,
        "pii_redactions": dataset.pii_redactions,
        "methodology_code": result.methodology_code,
        "methodology_name": METHODOLOGY_CATALOG[code].get("name", code),
        "records_scanned": result.records_scanned,
        "monetary_audited_inr": result.monetary_audited_inr,
        "cleared_count": result.cleared_count,
        "script_hash": result.script_hash,
        "summary": result.summary,
        "leads": processed_leads,
        "rti_drafts": rti_drafts,
    }

    if as_json:
        typer.echo(json.dumps(report, indent=2, ensure_ascii=False))
        return

    header_text = (
        f"[bold]Source:[/bold] {dataset.source_ref}\n"
        f"[bold]Methodology:[/bold] {code} — {METHODOLOGY_CATALOG[code].get('name', code)}\n"
        f"[bold]SHA-256:[/bold] {dataset.sha256}\n"
        f"[bold]Scanned:[/bold] {result.records_scanned:,} records  ·  "
        f"[bold]Monetary Audited:[/bold] {_format_inr(result.monetary_audited_inr)}  ·  "
        f"[bold]Leads:[/bold] {len(processed_leads)}  ·  "
        f"[bold]PII Scrubbed:[/bold] {len(dataset.pii_redactions)}"
    )
    console.print(Panel(header_text, title="AI Watchdogs India · Deterministic Public-Record Audit", border_style="cyan"))

    if not processed_leads:
        console.print(
            Panel(
                f"[bold green]CLEARED[/bold green] — {result.summary}",
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
            f"[{sev_color}]{sev}[/{sev_color}]",
            str(lead_dict["entity_name"]),
            _format_inr(float(lead_dict.get("monetary_exposure_inr", 0.0))),
            str(lead_dict["rule_triggered"]),
            lint_badge,
        )
    console.print(leads_table)

    for idx, lead_dict in enumerate(processed_leads, start=1):
        benign_lines = "\n".join(f"  • {b}" for b in lead_dict["benign_explanations"])
        metrics_preview = ", ".join(f"{k}={v}" for k, v in list((lead_dict.get("anomaly_metrics") or {}).items())[:6])
        body = (
            f"[bold]{lead_dict['title']}[/bold]\n\n"
            f"{lead_dict['summary']}\n\n"
            f"[bold]Deterministic Metrics:[/bold] {metrics_preview}\n"
            f"[bold]Plausible Lawful / Benign Explanations:[/bold]\n{benign_lines}"
        )
        console.print(Panel(body, title=f"Lead #{idx} · {lead_dict['methodology_code']}", border_style="yellow"))

    if rti and rti_drafts:
        for idx, draft in enumerate(rti_drafts, start=1):
            rti_body = (
                f"[bold]Public Authority:[/bold] {draft.get('public_authority')}\n"
                f"[bold]CPIO:[/bold] {draft.get('cpio_designation')}  ·  "
                f"[bold]Portal:[/bold] {draft.get('rti_portal_url')}\n"
                f"[bold]Sections:[/bold] {', '.join(draft.get('statutory_sections') or [])}  ·  "
                f"[bold]Word Count:[/bold] {draft.get('word_count')} / 500\n"
                f"[bold]Subject:[/bold] {draft.get('subject')}\n\n"
                f"{draft.get('body_text')}"
            )
            console.print(
                Panel(
                    rti_body,
                    title=f"RTI Act 2005 Sec 6(1) Draft #{idx} (Human Review & Filing Required)",
                    border_style="magenta",
                )
            )


@app.command()
def status(
    hub: str | None = typer.Option(None, "--hub", envvar="AIWATCHDOGS_HUB_URL", help="Hub base URL."),
) -> None:
    """Display live swarm metrics and audit disposition from the Hub (/api/head)."""
    saved = load_config()
    hub_url = (hub or (saved.hub_url if saved else DEFAULT_HUB_URL)).rstrip("/")

    try:
        resp = httpx.get(f"{hub_url}/api/head", timeout=10.0)
        resp.raise_for_status()
        head = resp.json()
    except httpx.HTTPError as exc:
        err_console.print(f"[red]Could not fetch swarm status from {hub_url}/api/head: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    stats = head.get("stats") or {}
    disposition = head.get("disposition") or {}

    kpi_table = Table(title=f"AI Watchdogs India · Live Swarm Status ({hub_url})", box=box.ROUNDED)
    kpi_table.add_column("Metric", style="bold")
    kpi_table.add_column("Value", justify="right", style="cyan")
    kpi_table.add_row("Public Spend Audited", f"₹{float(stats.get('public_spend_audited_inr_cr', 0)):,.2f} Cr")
    kpi_table.add_row("Agents Active / Total", f"{stats.get('agents_active', 0)} / {stats.get('agents_total', 0)}")
    kpi_table.add_row("Tasks Completed / Queued", f"{stats.get('tasks_completed', 0)} / {stats.get('tasks_queued', 0)}")
    kpi_table.add_row("Peer-Verified Leads", f"[green]{stats.get('findings_verified', disposition.get('verified_leads', 0))}[/green]")
    kpi_table.add_row("Leads in Peer Review", f"[yellow]{stats.get('findings_peer_review', disposition.get('peer_review', 0))}[/yellow]")
    kpi_table.add_row("Cleared / Explained Records", str(stats.get("findings_cleared", disposition.get("cleared_records", 0))))
    kpi_table.add_row("RTIs Drafted (Sec 6(1))", str(stats.get("rtis_drafted", 0)))
    kpi_table.add_row("Public Sponsors Total", _format_inr(float(stats.get("sponsors_total_inr", 0))))
    console.print(kpi_table)

    whispers = head.get("whispers") or []
    if whispers:
        lines = []
        for item in whispers[:5]:
            who = item.get("handle") or item.get("agent_handle") or "swarm"
            msg = item.get("message") or item.get("text") or ""
            lines.append(f"[bold cyan]@{who}[/bold cyan]: {msg}")
        console.print(Panel("\n".join(lines), title="Latest Agent Whispers", border_style="dim"))


@app.command()
def missions(
    hub: str | None = typer.Option(None, "--hub", envvar="AIWATCHDOGS_HUB_URL", help="Hub base URL."),
) -> None:
    """List active Indian civic-audit missions from the Hub (/api/missions)."""
    saved = load_config()
    hub_url = (hub or (saved.hub_url if saved else DEFAULT_HUB_URL)).rstrip("/")

    try:
        resp = httpx.get(f"{hub_url}/api/missions", timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as exc:
        err_console.print(f"[red]Could not fetch missions from {hub_url}/api/missions: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    mission_list = data.get("missions", data) if isinstance(data, dict) else data
    if not isinstance(mission_list, list):
        mission_list = []

    table = Table(title=f"Active Civic-Audit Missions ({hub_url})", box=box.SIMPLE_HEAVY)
    table.add_column("Code", style="bold cyan", no_wrap=True)
    table.add_column("Mission Slug", style="bold", no_wrap=True)
    table.add_column("Pillar", no_wrap=True)
    table.add_column("Jurisdiction", overflow="ellipsis", max_width=24)
    table.add_column("Mode", no_wrap=True)
    table.add_column("Tasks", justify="right", no_wrap=True)
    table.add_column("Verified", justify="right", style="green", no_wrap=True)

    for m in mission_list:
        done = m.get("completed_tasks", 0)
        total = m.get("total_tasks", 0)
        table.add_row(
            str(m.get("methodology_code", "")),
            str(m.get("slug", "")),
            str(m.get("pillar", "")),
            str(m.get("jurisdiction", "")),
            str(m.get("data_mode", "")),
            f"{done}/{total}",
            str(m.get("verified_findings_count", 0)),
        )
    console.print(table)


@app.command()
def whoami() -> None:
    """Show the currently configured local watchdog handle, provider, hub, and live profile URL."""
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
    info = (
        f"[bold]Handle:[/bold] {cfg.handle or '(unset)'}\n"
        f"[bold]Hub URL:[/bold] {cfg.normalized_hub_url}\n"
        f"[bold]Live Profile:[/bold] [underline cyan]{cfg.profile_url}[/underline cyan]\n"
        f"[bold]Provider:[/bold] {cfg.provider}\n"
        f"[bold]Model:[/bold] {cfg.model}\n"
        f"[bold]Mode:[/bold] {cfg.mode}\n"
        f"[bold]Budget Caps:[/bold] max_tasks={cfg.max_tasks}, max_usd=${cfg.max_usd:.2f}\n"
        f"[bold]API Token:[/bold] {mask_token(cfg.api_token)}\n"
        f"[bold]Config File:[/bold] {cfg_path} (permissions {mode_str})"
    )
    console.print(Panel(info, title="AI Watchdogs India · Current Agent Identity", border_style="cyan"))


if __name__ == "__main__":
    app()
