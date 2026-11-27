"""Finding .md pages link each citation to the document its printed line was read from."""

from __future__ import annotations

from typing import Any

from aiwatchdogs.hub.agent_access import render_finding_detail_md

EST_URL = "https://www.indiabudget.gov.in/budget2024-25/doc/eb/sbe70.pdf"
ACT_URL = "https://www.indiabudget.gov.in/budget2025-26/doc/eb/sbe70.pdf"
EST_SHA = "a" * 64
ACT_SHA = "b" * 64


class _StubDesk:
    def __init__(self, detail: dict[str, Any]) -> None:
        self._detail = detail

    def get_finding_detail(self, finding_id: str) -> dict[str, Any]:
        return self._detail


def _citation_lines(detail: dict[str, Any]) -> list[str]:
    md = render_finding_detail_md(_StubDesk(detail), "https://aiwatchdogs.in", "fnd-test")  # type: ignore[arg-type]
    section = md.split("## Provenance & Citations", 1)[1]
    return [ln for ln in section.splitlines() if ln.startswith("- https://") or ln.startswith("- portal")]


def _two_document_detail() -> dict[str, Any]:
    # Shape of a real UB1 finding: the CLI stamps source_ref = primary PDF onto both citations.
    return {
        "id": "fnd-test",
        "title": "Test line: actual expenditure was below the budget estimate",
        "artifact_sha256": EST_SHA,
        "citations": [
            {
                "source_url": EST_URL,
                "source_ref": EST_URL,
                "sha256": EST_SHA,
                "page": 1,
                "role": "estimates",
                "printed_line": "3. Test Scheme 5.12 ... 5.12 17.00 ... 17.00 8.00 ... 8.00 16.00 ... 16.00",
            },
            {
                "source_url": ACT_URL,
                "source_ref": EST_URL,
                "sha256": ACT_SHA,
                "page": 1,
                "role": "actuals",
                "printed_line": "3. Test Scheme 0.10 ... 0.10 16.00 ... 16.00 3.07 ... 3.07 13.00 ... 13.00",
            },
        ],
    }


def test_actuals_citation_links_to_the_later_budget_document() -> None:
    lines = _citation_lines(_two_document_detail())
    assert lines[0].startswith(f"- {EST_URL} (page 1)")
    assert lines[1].startswith(f"- {ACT_URL} (page 1)")


def test_single_source_citation_keeps_dataset_link_over_portal_fallback() -> None:
    # Engines without per-row URLs fill source_url with a portal homepage; the dataset link is better.
    detail = {
        "id": "fnd-test",
        "title": "Single-source finding",
        "artifact_sha256": EST_SHA,
        "citations": [
            {"source_url": "https://cga.nic.in", "source_ref": EST_URL, "sha256": EST_SHA, "excerpt": "row 4"},
        ],
    }
    assert _citation_lines(detail) == [f"- {EST_URL} — row 4"]


def test_citation_without_fingerprint_falls_back_as_before() -> None:
    detail = {
        "id": "fnd-test",
        "title": "Legacy finding",
        "citations": [{"source_ref": EST_URL, "page": 2, "printed_line": "5. Delhi Police 1 2 3"}],
    }
    assert _citation_lines(detail) == [f"- {EST_URL} (page 2) — 5. Delhi Police 1 2 3"]
