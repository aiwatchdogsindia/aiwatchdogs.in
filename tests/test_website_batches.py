"""Exercise the volunteer workflow against a real manifest, without external traffic."""

import csv
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from aiwatchdogs.cli.config import AgentConfig
from aiwatchdogs.cli.runner import AgentRunner
from aiwatchdogs.connectors import loader
from aiwatchdogs.engines import gov_pages
from aiwatchdogs.hub.app import create_app
from aiwatchdogs.hub.website_reports import validate_batch


SLUG = "gov-in-public-information"
PROJECTS = Path(__file__).resolve().parents[1] / "projects"


@pytest.mark.production_missions
def test_website_batch_from_public_queue_to_public_report(tmp_path, monkeypatch):
    monkeypatch.setenv("AIWATCHDOGS_PROJECTS_DIR", str(PROJECTS))
    monkeypatch.delenv("AIWATCHDOGS_KEEP_LEGACY_MISSIONS", raising=False)

    with TestClient(create_app(db_path=tmp_path / "hub.db", seed=True)) as client:
        public_url = f"/api/missions/{SLUG}"
        dashboard = client.get(public_url).json()
        assert len({task["id"] for task in dashboard["tasks"]}) == 1000
        assert all(task["status"] == "queued" for task in dashboard["tasks"])

        # Matrix endpoint returns all 159 checkpoints, 8 sections, 46 W1 checks
        mat_resp = client.get(f"{public_url}/matrix")
        assert mat_resp.status_code == 200
        mat = mat_resp.json()
        assert mat["matrix_version"] == "gigw-3.0-rti-2005-v1"
        assert mat["summary"]["total_checkpoints"] == 159
        assert mat["summary"]["w1_automated_checks_count"] == 46
        assert mat["summary"]["automated_http_html"] == 48
        assert mat["summary"]["agent_inspection"] == 96
        assert mat["summary"]["internal_governance"] == 15
        assert len(mat["checkpoints"]) == 159
        assert len(mat["sections"]) == 8
        assert client.get("/api/missions/police-finances/matrix").status_code == 404

        # Batch CSV endpoint serves exact checksummed manifests & rejects traversal / out-of-range names
        import hashlib
        b1_resp = client.get(f"{public_url}/batches/batch-0001.csv")
        assert b1_resp.status_code == 200
        assert "text/csv" in b1_resp.headers["content-type"]
        assert "immutable" in b1_resp.headers.get("cache-control", "")
        b1_task = next(t for t in dashboard["tasks"] if t["id"] == "prj-gov-in-public-information-0001")
        assert hashlib.sha256(b1_resp.content).hexdigest() == b1_task["expected_sha256"]
        assert client.get(f"{public_url}/batches/batch-1000.csv").status_code == 200
        for bad_name in ("batch-0000.csv", "batch-1001.csv", "batch-001.csv", "verify.py", "..%2Fgov-in-public-information.json"):
            assert client.get(f"{public_url}/batches/{bad_name}").status_code == 404

        union_budget = client.get("/api/missions/union-budget-unspent-funds").json()
        assert union_budget["total_tasks"] == 10000
        assert len({task["id"] for task in union_budget["tasks"]}) == 200
        assert union_budget["tasks_listed"] == 200
        assert union_budget["tasks_truncated"] is True
        assert union_budget["visual_summary"]["status_counts"]["total"] == 10000
        assert client.get("/api/missions/independent-india-inflation").status_code == 404
        police = client.get("/api/missions/police-finances").json()
        assert len({task["id"] for task in police["tasks"]}) == 100

        runner = AgentRunner(
            AgentConfig(handle="website-volunteer", hub_url="http://testserver", provider="rule-engine",
                        model="deterministic-python-v1", mode="investigate"),
            http_client=client,
            config_path=tmp_path / "agent.json",
        )
        reg = runner.register(save=False)
        auth = {"X-Agent-Token": reg["api_token"]}
        legacy_request = {"handle": runner.config.handle, "mode": "investigate", "preferred_mission": SLUG}
        assert client.post("/api/desk/next", headers=auth, json=legacy_request).status_code == 204

        lease = runner.claim_next(preferred_mission=SLUG)
        assert lease and lease["task"]["methodology_code"] == "W1"
        task = lease["task"]
        assert lease["role"]["submit_outcomes"] == "observations"
        report_url = f"{public_url}/observations/{task['id']}"
        incomplete = client.post(
            "/api/desk/submit", headers=auth,
            json={"lease_id": lease["lease_id"], "outcome": "observations", "observations": []},
        )
        assert incomplete.status_code == 422, incomplete.text
        assert client.get(report_url).status_code == 404

        retrieval_url = task["payload"]["retrieval_url"]
        batch = PROJECTS / SLUG / Path(urlsplit(retrieval_url).path).name
        with batch.open(newline="") as file:
            assignments = list(csv.DictReader(file))
        assert len(assignments) in (12, 13)
        unreachable = assignments[0]["domain"]

        def fetch_manifest(url, **kwargs):
            assert url == retrieval_url
            return batch.read_bytes(), "text/csv", "2026-11-25T00:00:00Z", False

        async def get_page(reader, url):
            if reader.domain == unreachable:
                raise gov_pages.Unavailable("fetch_failed")
            if urlsplit(url).path == "/contact":
                html = "<p>Public enquiries go to the information desk at help [at] example [dot] gov [dot] in.</p>"
            elif urlsplit(url).path == "/rti":
                html = "<p>Right to Information Act Section 4(1)(b) disclosures. CPIO and First Appellate Authority listed.</p>"
            else:
                html = "<p>Public services and information from the department.</p><a href='/contact'>Contact us</a><a href='/rti'>RTI</a>"
            return url, gov_pages.Page(html)

        monkeypatch.setattr(loader, "_fetch_remote_bytes", fetch_manifest)
        monkeypatch.setattr(gov_pages.Reader, "page", get_page)
        result = runner.execute_task(lease)
        assert result["status"] == "completed", result
        assert result["response"]["hosts"] == len(assignments)

        response = client.get(report_url)
        assert response.status_code == 200, response.text
        report = response.json()
        assert report["review_status"] == "volunteer_observations"
        assert report["matrix_version"] == "gigw-3.0-rti-2005-v1"
        assert report["manifest_sha256"] == task["payload"]["expected_sha256"]
        by_domain = {row["domain"]: row for row in report["observations"]}
        assert set(by_domain) == {row["domain"] for row in assignments}
        assert by_domain[unreachable]["outcome"] == "unassessable"
        assert by_domain[unreachable]["pages"] == []
        assert set(by_domain[unreachable]["checks"].values()) == {"unassessable"}
        for assignment in assignments[1:]:
            row = by_domain[assignment["domain"]]
            assert row["outcome"] == "observed"
            assert row["pages"][0] == assignment["url"]
            assert len(row["pages"]) == 3
            assert row["checks"]["contact_link"] == row["checks"]["email"] == "observed"
            assert row["checks"]["gov_email_domain"] == "observed"
            assert row["checks"]["rti_link"] == row["checks"]["pio_faa_disclosure"] == "observed"
            assert row["checks"]["postal_address"] == "not_observed"
        dashboard = client.get(public_url).json()
        assert len(dashboard["tasks"]) == 1000
        assert sum(task["status"] == "completed" for task in dashboard["tasks"]) == 1


@pytest.mark.parametrize("fault", [
    "future", "stale", "personal_path", "encoded_private_path", "arbitrary_contact_gap",
    "missing_contact_page", "impossible_unknown", "two_pages_without_link", "wrong_host",
    "invalid_not_applicable", "rti_na_non_exempt", "inconsistent_rti_unavailable",
])
def test_public_report_rejects_inconsistent_or_private_evidence(monkeypatch, fault):
    monkeypatch.setenv("AIWATCHDOGS_PROJECTS_DIR", str(PROJECTS))
    payload = json.loads((PROJECTS / f"{SLUG}.json").read_text())["tasks"][0]["payload"]
    with (PROJECTS / SLUG / "batch-0001.csv").open(newline="") as file:
        rows = [{
            "domain": entry["domain"], "observed_at": "2026-11-25T08:30:00Z",
            "outcome": "unassessable", "reason": "robots_restricted", "pages": [],
            "checks": dict.fromkeys(gov_pages.CHECKS, "unassessable"),
        } for entry in csv.DictReader(file)]
    row = rows[0]
    root = f"https://{row['domain']}/"
    row.update(outcome="observed", reason=None, pages=[root], checks=dict.fromkeys(gov_pages.CHECKS, "not_observed"))
    if fault == "future":
        row["observed_at"] = "2099-01-01T00:00:00Z"
    elif fault == "stale":
        row["observed_at"] = "2000-01-01T00:00:00Z"
    elif fault == "personal_path":
        row["pages"] = [root + "contacts/staff-member-name"]
    elif fault == "encoded_private_path":
        row["pages"] = [root + "directory/%252540personal"]
    elif fault == "arbitrary_contact_gap":
        row["reason"] = "contact_unavailable"
        row["checks"]["contact_link"] = "observed"
    elif fault == "missing_contact_page":
        row["reason"] = "contact_unavailable"
    elif fault == "impossible_unknown":
        row["checks"]["about_link"] = "unassessable"
    elif fault == "two_pages_without_link":
        row["pages"].append(root + "contact")
    elif fault == "wrong_host":
        row["pages"] = ["https://other.gov.in/contact"]
    elif fault == "invalid_not_applicable":
        row["checks"]["about_link"] = "not_applicable"
    elif fault == "rti_na_non_exempt":
        row["checks"]["rti_link"] = "not_applicable"
    elif fault == "inconsistent_rti_unavailable":
        row["reason"] = "rti_unavailable"
        row["checks"]["rti_link"] = "observed"
    with pytest.raises(HTTPException) as exc:
        validate_batch(payload, rows, lease_started_at="2026-11-25T08:20:00Z", received_at="2026-11-25T08:35:00Z")
    assert exc.value.status_code == 422
