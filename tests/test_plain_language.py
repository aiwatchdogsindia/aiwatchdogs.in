"""Plain-language headline and brief: dispatcher behaviour and where the fields surface."""

from __future__ import annotations

from typing import Any

import pytest

from aiwatchdogs.core import plain
from aiwatchdogs.core.plain import BRIEF_MAX, HEADLINE_MAX, plain_brief
from aiwatchdogs.core.plain._common import clip, fmt_date, fmt_inr, fmt_num, place
from aiwatchdogs.hub.agent_access import render_finding_detail_md, render_findings_md


class _StubDesk:
    def __init__(self, findings: list[dict[str, Any]]) -> None:
        self._findings = findings

    def list_findings(self, **_: Any) -> dict[str, Any]:
        return {"findings": self._findings, "total": len(self._findings)}

    def get_finding_detail(self, finding_id: str) -> dict[str, Any]:
        return self._findings[0]


def test_unknown_check_type_falls_back_to_trimmed_stored_text() -> None:
    out = plain_brief(
        {
            "methodology_code": "ZZ9",
            "title": "Public Record Anomaly Lead: Road repair line shows a gap",
            "summary": "The record prints 12 against 20. A second sentence follows.",
        }
    )
    assert out == {"headline": "Road repair line shows a gap", "brief": "The record prints 12 against 20."}


def test_empty_finding_still_returns_a_headline() -> None:
    out = plain_brief({})
    assert out["headline"] == "Public record observation"
    assert out["brief"] == ""


def test_writer_that_raises_falls_back_instead_of_failing_the_request(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_: dict[str, Any]) -> tuple[str, str]:
        raise ValueError("bad field")

    monkeypatch.setitem(plain._writers, "P9", boom)
    out = plain_brief({"methodology_code": "P9", "title": "Exam notice", "summary": "Notice text."})
    assert out == {"headline": "Exam notice", "brief": "Notice text."}


def test_output_is_bounded_and_brief_ends_with_a_full_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(plain._writers, "P9", lambda _: ("word " * 80, "A short brief without a stop"))
    out = plain_brief({"methodology_code": "P9"})
    assert len(out["headline"]) <= HEADLINE_MAX and out["headline"].endswith("…")
    assert out["brief"] == "A short brief without a stop."
    assert len(plain_brief({"methodology_code": "ZZ9", "title": "t", "summary": "x" * 900})["brief"]) <= BRIEF_MAX


@pytest.mark.parametrize("code", sorted(plain._WRITER_MODULES))
def test_every_registered_writer_loads_and_survives_an_empty_finding(code: str) -> None:
    assert plain._writer_for(code) is not None, f"no writer module for {code}"
    out = plain_brief({"methodology_code": code})
    assert isinstance(out["headline"], str) and out["headline"]
    assert "None" not in out["headline"] and "None" not in out["brief"]


def test_formatting_helpers() -> None:
    assert fmt_num(223142) == "2,23,142"
    assert fmt_num(28.50) == "28.5"
    assert fmt_inr(26079709) == "₹2.61 crore"
    assert fmt_inr(450000) == "₹4.5 lakh"
    assert fmt_inr(0) == ""
    assert fmt_date("2024-07-16") == "16 Jul 2024"
    assert fmt_date("not a date") == ""
    assert place({"jurisdiction": "State:Assam"}) == "Assam"
    assert place({"jurisdiction": "Union of India"}) == "Union government"
    assert clip("one two three four five", 16) == "one two three…"


def test_findings_markdown_lists_headline_then_brief() -> None:
    finding = {
        "id": "fnd-1",
        "title": "Public Examination Conduct Observation (Delayed): Advt No. 23 of 2023",
        "headline": "Treasury officer exam result came 120 days late",
        "brief": "An official notice moved the date from 16 Jul 2024 to 13 Nov 2024.",
        "status": "verified",
        "methodology_code": "P9",
        "entity_name": "Haryana Public Service Commission (HPSC)",
        "fiscal_year": "FY2024-25",
        "monetary_exposure_inr_cr": 0.0,
    }
    md = render_findings_md(_StubDesk([finding]), "https://aiwatchdogs.in")  # type: ignore[arg-type]
    assert "### [Treasury officer exam result came 120 days late](https://aiwatchdogs.in/findings/fnd-1.md)" in md
    assert "An official notice moved the date from 16 Jul 2024 to 13 Nov 2024." in md
    assert "Verified · Haryana Public Service Commission (HPSC) · FY2024-25 · `P9`" in md
    assert "Public Examination Conduct Observation" not in md
    assert "₹0.00" not in md

    detail = render_finding_detail_md(_StubDesk([finding]), "https://aiwatchdogs.in", "fnd-1")  # type: ignore[arg-type]
    assert "## In plain words" in detail
    assert "**Treasury officer exam result came 120 days late**" in detail
    # The stored title stays as the page heading, so the submitted record is still visible.
    assert "# Public Examination Conduct Observation (Delayed): Advt No. 23 of 2023" in detail


def test_findings_markdown_uses_stored_title_when_no_headline() -> None:
    md = render_findings_md(_StubDesk([{"id": "fnd-2", "title": "Stored title", "status": "peer_review"}]), "https://x")  # type: ignore[arg-type]
    assert "### [Stored title](https://x/findings/fnd-2.md)" in md
