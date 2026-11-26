"""A large, mixed swarm must not break the queue or the public record.

Covers what changes when a hundred tasks meet many agents at once: the role the hub assigns,
rate limits shared by nodes behind one address, runs killed by a harness timeout, nodes that
cannot reach the source hosts, tasks parked after repeated failures, and renamed projects.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from test_projects import SHA, _project

from aiwatchdogs.cli.config import AgentConfig
from aiwatchdogs.cli.providers import describe_outbound_destinations, rule_engine_provider
from aiwatchdogs.cli.runner import CLI_USER_AGENT, AgentRunner, _source_host, role_of
from aiwatchdogs.engines.registry import run_engine
from aiwatchdogs.hub.app import create_app
from aiwatchdogs.hub.desk import task_role
from aiwatchdogs.hub.templates import render_join_txt
from aiwatchdogs.projects import load_project_files, slug_aliases, validate_project

REPO_PROJECTS = Path(__file__).resolve().parents[1] / "projects"

# One printed page with a police head whose revised estimate is 20% under its budget estimate.
_PAGE = "Demand 9\n2055 Police 100.00 200.00 160.00 210.00\n"
_P1_CONTEXT = {
    "budget_lines": [
        {
            "key": "test-police-2055",
            "entity": "Test Police",
            "label": "Major Head 2055",
            "unit": "crore",
            "parts": [{"page": 1, "pattern": r"^2055 Police\s+\d", "values": {"actual": 1, "be": 2, "re": 3, "be_next": 4}}],
            "years": {"actual": "FY2024-25", "be": "FY2025-26", "re": "FY2025-26", "be_next": "FY2026-27"},
        }
    ],
    "comparisons": [{"a": "re", "b": "be", "label": "revised estimate vs budget estimate", "threshold_pct": 10, "kind": "re_be"}],
}


def _lease(lease_id: str, *, context: dict[str, Any] | None = None, role: str | None = "auditor") -> dict[str, Any]:
    lease: dict[str, Any] = {
        "lease_id": lease_id,
        "lease_expires_at": "2099-01-01T00:00:00Z",
        "task": {
            "id": f"tsk-{lease_id}",
            "task_type": "anomaly_scan",
            "mission_slug": "police-finances",
            "methodology_code": "P1",
            "title": "Test Police: revenue spending",
            "instructions": "Run P1",
            "payload": {
                "records": [{"page": 1, "raw_text": _PAGE}],
                "jurisdiction": "State:Test",
                "fiscal_year": "FY2024-27",
                "context": context or _P1_CONTEXT,
            },
        },
    }
    if role:
        lease["role"] = {"name": role, "title": role.title(), "brief": "do the work"}
    return lease


class _Hub:
    """A scripted hub: hands out leases, records calls, and can throttle chosen endpoints."""

    def __init__(self, leases: list[dict[str, Any]], *, throttle: dict[str, int] | None = None) -> None:
        self.leases = list(leases)
        self.throttle = dict(throttle or {})
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        self.calls.append((path, body))
        if self.throttle.get(path, 0) != 0:
            if self.throttle[path] > 0:
                self.throttle[path] -= 1
            return httpx.Response(429, headers={"Retry-After": "7"}, json={"detail": "Rate limit exceeded; please retry shortly."})
        if path == "/api/desk/next":
            return httpx.Response(200, json=self.leases.pop(0)) if self.leases else httpx.Response(204)
        if path == "/api/desk/beat":
            return httpx.Response(200, json={"status": "active"})
        if path == "/api/desk/submit":
            return httpx.Response(200, json={"status": "accepted", "outcome": body.get("outcome")})
        if path == "/api/desk/release":
            return httpx.Response(200, json={"status": "released"})
        return httpx.Response(404)

    def bodies(self, path: str) -> list[dict[str, Any]]:
        return [b for p, b in self.calls if p == path]


def _runner(hub: _Hub, tmp_path: Path | None = None) -> tuple[AgentRunner, list[float]]:
    cfg = AgentConfig(handle="swarm-node", hub_url="http://mockhub", api_token="awd_tok")
    client = httpx.Client(transport=httpx.MockTransport(hub), base_url="http://mockhub")
    runner = AgentRunner(cfg, http_client=client, config_path=(tmp_path / "swarm-node.json") if tmp_path else None)
    sleeps: list[float] = []
    runner._sleep = sleeps.append  # never really wait in tests
    return runner, sleeps


# ---------------------------------------------------------------------------
# The contributor CLI under swarm conditions
# ---------------------------------------------------------------------------


def test_rate_limit_shared_by_many_nodes_is_waited_out() -> None:
    hub = _Hub([_lease("a")], throttle={"/api/desk/next": 2, "/api/desk/submit": 1})
    runner, sleeps = _runner(hub)
    summary = runner.run_loop(max_tasks=1)
    assert (summary.tasks_completed, summary.findings_submitted, summary.errors) == (1, 1, 0)
    assert len(sleeps) == 3 and all(7.0 <= s <= 10.5 for s in sleeps)  # Retry-After plus a little spread
    assert not hub.bodies("/api/desk/release")


def test_throttled_heartbeats_never_fail_a_task() -> None:
    hub = _Hub([_lease("a")], throttle={"/api/desk/beat": -1})  # every heartbeat refused
    runner, sleeps = _runner(hub)
    summary = runner.run_loop(max_tasks=1)
    assert (summary.tasks_completed, summary.errors) == (1, 0)
    assert sleeps == []  # a heartbeat is skipped, not waited for
    assert len(hub.bodies("/api/desk/submit")) == 1


def test_a_refusal_without_retry_after_is_not_retried() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"detail": "Agent already holds 2 active task leases"})

    runner, sleeps = _runner(handler)  # type: ignore[arg-type]
    summary = runner.run_loop(max_tasks=3)
    assert (summary.tasks_claimed, summary.stopped_reason, sleeps) == (0, "hub_busy_or_lease_cap", [])


def test_nothing_read_is_handed_back_not_reported_as_cleared() -> None:
    unreadable = json.loads(json.dumps(_P1_CONTEXT))
    unreadable["budget_lines"][0]["parts"][0]["pattern"] = "^9999 No such head"
    hub = _Hub([_lease("a", context=unreadable)])
    runner, _ = _runner(hub)
    summary = runner.run_loop(max_tasks=1)
    assert (summary.cleared_submitted, summary.errors) == (0, 1)
    assert hub.bodies("/api/desk/submit") == []
    assert [b["reason"] for b in hub.bodies("/api/desk/release")] == ["unevaluable_source"]


def test_interrupted_run_hands_its_task_back(monkeypatch: pytest.MonkeyPatch) -> None:
    hub = _Hub([_lease("a")])
    runner, _ = _runner(hub)

    def killed(*_a: object, **_k: object) -> None:
        raise KeyboardInterrupt  # what Ctrl-C, SIGTERM or a harness timeout becomes

    monkeypatch.setattr(runner, "_invoke_engine", killed)
    with pytest.raises(KeyboardInterrupt):
        runner.run_loop(max_tasks=1)
    assert [b["reason"] for b in hub.bodies("/api/desk/release")] == ["interrupted"]
    assert hub.bodies("/api/desk/submit") == []


def test_next_run_returns_the_task_a_killed_run_was_holding(tmp_path: Path) -> None:
    note = tmp_path / "swarm-node.json.lease"
    note.write_text(json.dumps({"hub": "http://mockhub", "handle": "swarm-node", "lease_id": "lease-left-behind"}))
    hub = _Hub([])
    runner, _ = _runner(hub, tmp_path)
    runner.run_loop(max_tasks=1)
    assert [b["lease_id"] for b in hub.bodies("/api/desk/release")] == ["lease-left-behind"]
    assert not note.exists()

    # A note written for another handle or hub is never acted on.
    note.write_text(json.dumps({"hub": "http://elsewhere", "handle": "swarm-node", "lease_id": "not-mine"}))
    hub2 = _Hub([])
    runner2, _ = _runner(hub2, tmp_path)
    runner2.run_loop(max_tasks=1)
    assert hub2.bodies("/api/desk/release") == [] and not note.exists()


def test_lease_note_is_kept_only_while_a_task_is_held(tmp_path: Path) -> None:
    hub = _Hub([_lease("a")])
    runner, _ = _runner(hub, tmp_path)
    seen: list[bool] = []
    original = runner._invoke_engine

    def spy(*args: Any, **kwargs: Any) -> Any:
        seen.append((tmp_path / "swarm-node.json.lease").is_file())
        return original(*args, **kwargs)

    runner._invoke_engine = spy  # type: ignore[method-assign]
    runner.run_loop(max_tasks=1)
    assert seen == [True] and not (tmp_path / "swarm-node.json.lease").exists()

    # A run under a borrowed identity writes nothing beside the saved credentials.
    hub3 = _Hub([_lease("b")])
    runner3, _ = _runner(hub3, tmp_path)
    runner3.run_loop(max_tasks=1, save_on_register=False)
    assert not (tmp_path / "swarm-node.json.lease").exists()


def test_node_that_cannot_reach_sources_stops_after_two_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    hub = _Hub([_lease("a"), _lease("b"), _lease("c"), _lease("d")])
    runner, _ = _runner(hub)

    def blocked(*_a: object, **_k: object) -> None:
        raise httpx.ConnectError("government host unreachable from this sandbox")

    monkeypatch.setattr(runner, "_load_task_dataset", blocked)
    summary = runner.run_loop(max_tasks=4)
    assert (summary.tasks_claimed, summary.errors, summary.stopped_reason) == (2, 2, "sources_unreachable")
    assert [b["reason"] for b in hub.bodies("/api/desk/release")] == ["network_error", "network_error"]
    assert len(hub.leases) == 2  # the other two tasks were never taken from the queue


def test_the_network_notice_is_true_whatever_the_current_projects_are() -> None:
    plan = describe_outbound_destinations("https://aiwatchdogs.in", rule_engine_provider())
    rows = plan["dataset_hosts"]
    # The tasks decide which hosts a node contacts, so the notice names no fixed list.
    assert [r.get("destination") for r in rows] == ["The official host named in each task"]
    # Hosts of projects that no longer exist must not be shown as destinations.
    assert not any(h in json.dumps(rows) for h in ("cga.nic.in", "cloudfront.net", "raw.githubusercontent.com"))
    # A host the operator adds is still listed by name.
    extra = describe_outbound_destinations("https://aiwatchdogs.in", rule_engine_provider(), extra_dataset_hosts=["Example.org"])
    assert extra["dataset_hosts"][-1]["host"] == "example.org"


@pytest.mark.parametrize(
    ("ref", "host"),
    [
        ("https://www.indiabudget.gov.in/doc/eb/sbe51.pdf", "www.indiabudget.gov.in"),
        ("https://www.mahalegislature.org.in/performance-report/2026/भाग-4 पोलीस.pdf", "www.mahalegislature.org.in"),
        ("https://user:secret@finance.example.gov.in:8443/a.pdf", "finance.example.gov.in"),
        ("/tmp/local.csv", "public source"),
        ("http://[not-a-host/a.pdf", "public source"),
        ("https://", "public source"),
        ("", "public source"),
        (None, "public source"),
    ],
)
def test_the_host_is_named_before_each_download(ref: str | None, host: str) -> None:
    assert _source_host(ref) == host


def test_every_download_is_announced_with_its_host(monkeypatch: pytest.MonkeyPatch) -> None:
    import aiwatchdogs.cli.runner as runner_mod
    from aiwatchdogs.core.models import LoadedDataset

    lease = _lease("a")
    lease["task"]["payload"].pop("records")
    lease["task"]["payload"]["retrieval_url"] = "https://www.indiabudget.gov.in/doc/eb/sbe51.pdf"
    seen: list[tuple[str, dict[str, Any]]] = []
    order: list[str] = []

    def fake_load(ref: str, **_: Any) -> LoadedDataset:
        order.append("download")
        return LoadedDataset(records=[{"page": 1, "raw_text": _PAGE}], raw_text=_PAGE, sha256="0" * 64, byte_size=len(_PAGE), format="pdf", source_ref=ref)

    monkeypatch.setattr(runner_mod, "load_dataset", fake_load)
    hub = _Hub([lease])
    runner, _ = _runner(hub)
    runner.on_event = lambda event, data: (seen.append((event, data)), order.append(event))[0]
    assert runner.run_loop(max_tasks=1).tasks_completed == 1
    assert ("source_download", {"hosts": ["www.indiabudget.gov.in"]}) in seen
    assert order.index("source_download") < order.index("download")  # announced first, then fetched
    assert any(b.get("whisper", "").endswith("from www.indiabudget.gov.in") for b in hub.bodies("/api/desk/beat"))


def test_a_task_without_a_link_still_says_where_its_data_came_from() -> None:
    hub = _Hub([_lease("a")])
    runner, _ = _runner(hub)
    assert runner.run_loop(max_tasks=1).tasks_completed == 1
    fetch_lines = [b["whisper"] for b in hub.bodies("/api/desk/beat") if b.get("whisper", "").startswith("Fetching")]
    assert fetch_lines and all(line.endswith("from public source") for line in fetch_lines)


def test_role_comes_from_the_hub_and_is_counted() -> None:
    hub = _Hub([_lease("a", role="auditor"), _lease("b", role=None)])
    runner, _ = _runner(hub)
    summary = runner.run_loop(max_tasks=2)
    assert summary.roles == {"auditor": 2}  # an older hub sends no role; it is derived from the task
    assert role_of({"task": {"task_type": "peer_verify", "payload": {}}}) == "peer_verifier"
    assert role_of({"task": {"task_type": "peer_verify", "payload": {"review_type": "adjudication"}}}) == "adjudicator"
    assert role_of({"role": {"name": "peer_verifier"}, "task": {"task_type": "anomaly_scan"}}) == "peer_verifier"


# ---------------------------------------------------------------------------
# The hub
# ---------------------------------------------------------------------------


def _hub_with(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, projects: list[dict]) -> tuple[Path, Path]:
    pdir = tmp_path / "projects"
    pdir.mkdir(exist_ok=True)
    for old in pdir.glob("*.json"):
        old.unlink()
    for proj in projects:
        (pdir / f"{proj['slug']}.json").write_text(json.dumps(proj))
    monkeypatch.setenv("AIWATCHDOGS_PROJECTS_DIR", str(pdir))
    monkeypatch.delenv("AIWATCHDOGS_KEEP_LEGACY_MISSIONS", raising=False)
    monkeypatch.setenv("AIWATCHDOGS_DISABLE_RATE_LIMIT", "1")
    return pdir, tmp_path / "hub.db"


def _register(client: TestClient, handle: str) -> dict[str, str]:
    body = {"handle": handle, "provider": "rule-engine", "model": "deterministic-python-v1"}
    return {"X-Agent-Token": client.post("/api/agents/register", json=body).json()["api_token"]}


_FINDING = {
    "title": "Test Police: revised estimate vs budget estimate is 20.0% below (Major Head 2055)",
    "summary": "The official budget document prints 160.00 crore against 200.00 crore. Statistical divergence for public review.",
    "methodology_code": "M5",
    "entity_name": "Test Police",
    "jurisdiction": "State:Test",
    "fiscal_year": "FY2025-26",
    "monetary_exposure_inr": 4e8,
    "severity": "low",
    "anomaly_metrics": {"be": 200.0, "re": 160.0, "pct_difference": -20.0},
    "rule_triggered": "M5-RE-VS-BE>10%",
    "engine_version": "2.0.0",
    "ruleset_version": "M5-2026.11-v2",
    "artifact_sha256": SHA,
    "citations": [{"page": 1, "printed_line": "2055 Police 100.00 200.00 160.00 210.00"}],
    "benign_explanations": ["Mid-year revisions can reflect re-phased capital works."],
    "suggested_authority": "Home Department, Government of Test",
}


@pytest.mark.production_missions
def test_every_claim_names_the_role(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, db = _hub_with(tmp_path, monkeypatch, [_project()])
    with TestClient(create_app(db_path=db, seed=True)) as client:
        first = _register(client, "node-one")
        claim = client.post("/api/desk/next", headers=first, json={"mode": "auto"}).json()
        assert claim["role"]["name"] == "auditor" and "finding | cleared" in claim["role"]["submit_outcomes"]
        done = client.post("/api/desk/submit", headers=first, json={"lease_id": claim["lease_id"], "outcome": "finding", "finding": _FINDING})
        assert done.status_code == 200, done.text
        # The author is never offered its own lead; the next agent is made a peer verifier.
        assert client.post("/api/desk/next", headers=first, json={"mode": "auto"}).status_code == 204
        second = client.post("/api/desk/next", headers=_register(client, "node-two"), json={"mode": "auto"}).json()
        assert second["role"]["name"] == "peer_verifier" and second["task"]["task_type"] == "peer_verify"
        roles = {t["role"] for t in client.get("/api/missions/test-city-budget").json()["tasks"]}
        assert roles == {"auditor", "peer_verifier"}
    assert task_role("peer_verify", {"review_type": "adjudication"})["name"] == "adjudicator"


@pytest.mark.production_missions
def test_parked_task_returns_to_the_queue_after_cooling_down(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, db = _hub_with(tmp_path, monkeypatch, [_project()])
    monkeypatch.setenv("AIWATCHDOGS_RETRY_BASE_SECONDS", "0")
    with TestClient(create_app(db_path=db, seed=True)) as client:
        auth = _register(client, "flaky-node")
        for _ in range(3):  # three hand-backs park the task
            lease = client.post("/api/desk/next", headers=auth, json={"mode": "auto"}).json()
            released = client.post("/api/desk/release", headers=auth, json={"lease_id": lease["lease_id"], "reason": "network_error"})
        assert released.json()["task_status"] == "needs_adjudication"
        assert client.post("/api/desk/next", headers=auth, json={"mode": "auto"}).status_code == 204

        conn = sqlite3.connect(db)
        conn.execute("UPDATE tasks SET updated_at = '2020-01-01T00:00:00Z' WHERE status = 'needs_adjudication'")
        conn.commit()
        conn.close()
        again = client.post("/api/desk/next", headers=_register(client, "healthy-node"), json={"mode": "auto"})
        assert again.status_code == 200 and again.json()["task"]["id"] == "prj-test-city-budget-fy24"

    monkeypatch.setenv("AIWATCHDOGS_PARKED_REQUEUE_MINUTES", "0")  # an operator can switch the cool-down off
    conn = sqlite3.connect(db)
    conn.execute("UPDATE tasks SET status = 'needs_adjudication', lease_id = NULL, updated_at = '2020-01-01T00:00:00Z'")
    conn.commit()
    conn.close()
    with TestClient(create_app(db_path=db, seed=True)) as client:
        assert client.post("/api/desk/next", headers=_register(client, "third-node"), json={"mode": "auto"}).status_code == 204


@pytest.mark.production_missions
def test_project_page_lists_every_task_of_a_large_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base = _project()["tasks"][0]
    tasks = [{**base, "id": f"test-city-budget-{i:03d}", "dataset_key": f"key-{i:03d}", "title": f"Police heads, unit {i:03d}"} for i in range(120)]
    assert validate_project(_project(tasks=tasks), filename_stem="test-city-budget") == []
    _, db = _hub_with(tmp_path, monkeypatch, [_project(tasks=tasks)])
    with TestClient(create_app(db_path=db, seed=True)) as client:
        detail = client.get("/api/missions/test-city-budget").json()
        assert detail["total_tasks"] == 120 and len(detail["tasks"]) == 120
        claimed = {client.post("/api/desk/next", headers=_register(client, f"node-{i:02d}"), json={"mode": "auto"}).json()["task"]["id"] for i in range(12)}
        assert len(claimed) == 12  # twelve agents, twelve different tasks


@pytest.mark.production_missions
def test_out_of_date_official_cli_is_asked_to_refresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, db = _hub_with(tmp_path, monkeypatch, [_project()])
    with TestClient(create_app(db_path=db, seed=True)) as client:
        body = {"handle": "old-build", "provider": "rule-engine", "model": "x"}
        old = client.post("/api/agents/register", json=body, headers={"User-Agent": "AIWatchdogs-India-AgentCLI/0.2"})
        assert old.status_code == 426 and "refresh" in old.json()["detail"]
        assert client.post("/api/desk/next", json={}, headers={"User-Agent": "AIWatchdogs-India-AgentCLI/0.2"}).status_code == 426
        for agent in (CLI_USER_AGENT, "curl/8.5.0", "python-httpx/0.28"):  # current CLI and hand-written clients pass
            assert client.post("/api/agents/register", json={**body, "handle": f"ok-{abs(hash(agent)) % 10**6}"}, headers={"User-Agent": agent}).status_code == 200


@pytest.mark.production_missions
def test_renamed_project_keeps_its_record_and_its_old_links(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    old = _project(slug="old-name")
    old["tasks"][0]["id"] = "old-name-t1"
    pdir, db = _hub_with(tmp_path, monkeypatch, [old])
    with TestClient(create_app(db_path=db, seed=True)) as client:
        auth = _register(client, "early-node")
        lease = client.post("/api/desk/next", headers=auth, json={"mode": "auto"}).json()
        sub = client.post("/api/desk/submit", headers=auth, json={"lease_id": lease["lease_id"], "outcome": "finding", "finding": _FINDING})
        finding_id = sub.json()["finding_id"]

    new = _project(slug="new-name", formerly=["old-name"], title="Test police budget audit, all units")
    new["tasks"] = [{**old["tasks"][0]}, {**old["tasks"][0], "id": "new-name-t2", "dataset_key": "second"}]
    (pdir / "old-name.json").unlink()
    (pdir / "new-name.json").write_text(json.dumps(new))
    assert slug_aliases(pdir) == {"old-name": "new-name"}

    with TestClient(create_app(db_path=db, seed=True)) as client:
        missions = client.get("/api/missions").json()["missions"]
        assert [m["slug"] for m in missions] == ["new-name"]  # renamed, not archived-and-recreated
        via_old = client.get("/api/missions/old-name").json()
        assert via_old["slug"] == "new-name" and via_old["title"] == "Test police budget audit, all units"
        assert via_old["total_tasks"] == 2 and via_old["completed_tasks"] == 1
        assert via_old["peer_checks_total"] == 2 and via_old["peer_checks_done"] == 0
        assert [f["id"] for f in via_old["findings"]] == [finding_id]
        assert client.get(f"/api/findings/{finding_id}").json()["mission_slug"] == "new-name"
        assert client.get("/api/findings", params={"mission": "old-name"}).json()["findings"][0]["id"] == finding_id
        assert client.get("/feeds/old-name.atom").status_code == 200
        # The lead submitted under the old name is still waiting for its two peer checks.
        peer = client.post("/api/desk/next", headers=_register(client, "later-node"), json={"mode": "verify"}).json()
        assert peer["task"]["mission_slug"] == "new-name" and peer["task"]["target_finding_id"] == finding_id

    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM missions WHERE slug = 'old-name' OR id = 'project-old-name'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM tasks WHERE mission_slug = 'old-name'").fetchone()[0] == 0
    conn.close()

    # Starting again changes nothing (the rename is not repeated, nothing is duplicated).
    with TestClient(create_app(db_path=db, seed=True)) as client:
        assert client.get("/api/missions/new-name").json()["total_tasks"] == 2
        assert client.get("/api/missions/new-name").json()["peer_checks_total"] == 2


@pytest.mark.parametrize(
    ("formerly", "expected"),
    [
        (["test-city-budget"], "current slug"),
        (["Bad Slug"], "must be slugs"),
        (["a-b", "a-b"], "twice"),
        ("old-name", "must be a list"),
        (["single-bidder-procurement-scrutiny"], "reserved"),
    ],
)
def test_bad_former_slugs_are_rejected(formerly: object, expected: str) -> None:
    errors = validate_project(_project(formerly=formerly), filename_stem="test-city-budget")
    assert any(expected in e for e in errors), errors


def test_two_projects_cannot_claim_the_same_former_slug(tmp_path: Path) -> None:
    for slug in ("first-project", "second-project"):
        proj = _project(slug=slug, formerly=["shared-old-name"])
        proj["tasks"][0]["id"] = f"{slug}-t1"
        (tmp_path / f"{slug}.json").write_text(json.dumps(proj))
    errors = [e for pf in load_project_files(tmp_path) for e in pf.errors]
    assert any("also claimed" in e for e in errors), errors


# ---------------------------------------------------------------------------
# P1: rows printed one cell per line
# ---------------------------------------------------------------------------

_CELL_PER_LINE = "19,870\n47,46,878\n50,39,700\n50,64,800\nlabel in Hindi\nTotal - Police (Major Head)\n53,78,400\n47,46,878\nVoted\n"


def test_p1_reads_a_row_printed_one_cell_per_line() -> None:
    line = {
        "key": "ut-police-2055",
        "entity": "Test UT Police",
        "label": "Major Head 2055, total",
        "unit": "thousand",
        "parts": [
            {
                "page": 30,
                "pattern": r"^Total - Police \(Major Head\)",
                "lines_before": 4,
                "lines_after": 1,
                # the lone dash in the label counts as a nil token, so BE next is token 4
                "values": {"actual": 0, "be": 1, "re": 2, "be_next": 4},
            }
        ],
        "years": {"actual": "FY2024-25", "be": "FY2025-26", "re": "FY2025-26", "be_next": "FY2026-27"},
    }
    ctx = {
        "budget_lines": [line],
        "comparisons": [{"a": "be_next", "b": "re", "label": "next year's budget vs revised estimate", "threshold_pct": 5, "kind": "growth"}],
        "retrieval_url": "https://example.gov.in/ddg.pdf",
        "expected_sha256": SHA,
    }
    res = run_engine("P1", [{"page": 30, "raw_text": _CELL_PER_LINE}], ctx)
    assert res.unevaluable_count == 0 and len(res.leads) == 1
    lead = res.leads[0]
    assert (lead.anomaly_metrics["be_next"], lead.anomaly_metrics["re"]) == (5378400.0, 5064800.0)
    assert lead.citations[0]["printed_line"] == "47,46,878 50,39,700 50,64,800 label in Hindi Total - Police (Major Head) 53,78,400"

    # Without the window the label line carries no figures: unevaluable, never a guess.
    line["parts"][0].pop("lines_before")
    line["parts"][0].pop("lines_after")
    assert run_engine("P1", [{"page": 30, "raw_text": _CELL_PER_LINE}], ctx).unevaluable_count == 1


@pytest.mark.parametrize("bad", [-1, 13, "2", True, 1.5])
def test_bad_line_windows_are_rejected(bad: object) -> None:
    proj = _project(methodology_code="P1")
    proj["tasks"][0]["methodology_code"] = "P1"
    proj["tasks"][0]["payload"]["context"] = {"budget_lines": [{"key": "x", "parts": [{"page": 1, "pattern": "Total", "lines_after": bad}]}]}
    assert any("lines_after" in e for e in validate_project(proj, filename_stem="test-city-budget"))


# ---------------------------------------------------------------------------
# The police finances project file and the join instructions
# ---------------------------------------------------------------------------


def test_police_finances_project_is_complete_and_consistent() -> None:
    data = json.loads((REPO_PROJECTS / "police-finances.json").read_text(encoding="utf-8"))
    assert validate_project(data, filename_stem="police-finances") == []
    assert data["formerly"] == ["metro-police-finances"] and data["posted_by"]["name"] == "Copwatch India"
    tasks = data["tasks"]
    assert len(tasks) == 100
    assert len({t["id"] for t in tasks}) == 100
    assert {"metro-police-delhi-notes-2024-25", "metro-police-delhi-notes-2025-26", "metro-police-hyderabad-2026-27", "metro-police-mumbai-2026-27"} <= {t["id"] for t in tasks}
    for task in tasks:
        payload = task["payload"]
        assert task.get("methodology_code", data["methodology_code"]) == "P1"
        assert payload["retrieval_url"].startswith("https://") and len(payload["expected_sha256"]) == 64
        lines = payload["context"]["budget_lines"]
        assert lines and payload["context"]["comparisons"], task["id"]
        for line in lines:
            assert line["unit"] in {"crore", "lakh", "thousand"} and line["entity"] and line["years"]
            names: set[str] = set()
            for part in line["parts"]:
                assert isinstance(part["page"], int) and part["page"] >= 1
                assert "visual_order" not in part  # a research aid, never shipped to agents
                names |= set(part["values"])
            names |= set(line.get("references", {}))
            # every comparison the task promises can actually be made from what the line reads
            assert any({c["a"], c["b"]} <= names for c in payload["context"]["comparisons"]), task["id"]
    # One file never hides two different checksums, and every State named in scope has a task.
    by_url: dict[str, set[str]] = {}
    for task in tasks:
        by_url.setdefault(task["payload"]["retrieval_url"], set()).add(task["payload"]["expected_sha256"])
    assert all(len(digests) == 1 for digests in by_url.values())
    planned = [p["title"] for p in data["planned_tasks"]]
    assert sum("Find a usable official source" in t for t in planned) == 11


def test_join_instructions_explain_roles_time_and_failures() -> None:
    text = render_join_txt("https://aiwatchdogs.in")
    for needle in ("YOUR ROLE IS ASSIGNED BY THE HUB", "AUDITOR", "PEER VERIFIER", "ADJUDICATOR", "at least 10 minutes", "Retry-After", "426 Upgrade Required", "`role.name`"):
        assert needle in text, needle
    assert "--continuous" not in text  # an agent must never be told to wait on an empty queue forever
    # An agent whose shell is blocked must stop and ask, not hand-write the HTTP protocol.
    assert "That is not a reason to use Option B." in text
    assert "A blocked shell or an unanswered permission prompt is not that case." in text
    assert "uvx --from 'git+https://github.com/aiwatchdogsindia/aiwatchdogs.in.git' aiwatchdogs join" in text
