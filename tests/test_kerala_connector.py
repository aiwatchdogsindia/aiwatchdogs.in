"""Offline tests for the Kerala Finance Department budget connector."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from aiwatchdogs.connectors import kerala

_INDEX_HTML = """
<html><body>
<table>
  <tr><th>Document</th><th>Published</th><th>Year</th><th>View</th></tr>
  <tr style="cursor: pointer;">
    <td onclick="dwldDoc('d6we5vb8ye5vf4u');"><b>Revised Annual Financial Statement</b></td>
    <td onclick="dwldDoc('d6we5vb8ye5vf4u');"><span style="display: none;">2026-06-19</span>19-06-2026</td>
    <td onclick="dwldDoc('d6we5vb8ye5vf4u');">2026-27</td>
    <td onclick="selDoc('d6we5vb8ye5vj0q');"></td>
  </tr>
  <tr>
    <td onclick="dwldDoc('d6we5vb8ye5vi1r');"><b>Revised Annual Plan Vol I</b></td>
    <td onclick="dwldDoc('d6we5vb8ye5vi1r');">19-06-2026</td>
    <td onclick="dwldDoc('d6we5vb8ye5vi1r');">Statements of alterations to the Fifth Year's Programme 2026-2027</td>
    <td></td>
  </tr>
  <tr>
    <td onclick="selDoc('d6we5vb8ye5vj0q');">View only, not a download</td>
    <td>19-06-2026</td>
    <td>2026-27</td>
    <td></td>
  </tr>
</table>
</body></html>
"""


def test_parse_kerala_budget_index_extracts_download_rows() -> None:
    documents = kerala.parse_kerala_budget_documents(_INDEX_HTML)

    assert len(documents) == 2
    first, second = documents
    assert first.doc_id == "d6we5vb8ye5vf4u"
    assert first.title == "Revised Annual Financial Statement"
    assert first.published_on == "2026-06-19"
    assert first.fiscal_year == "2026-27"
    assert first.download_url == (
        "https://www.finance.kerala.gov.in/includeWeb/fileViewer.jsp"
        "?dId=d6we5vb8ye5vf4u"
    )
    assert second.doc_id == "d6we5vb8ye5vi1r"
    assert second.title == "Revised Annual Plan Vol I"
    assert second.published_on == "2026-06-19"
    assert second.fiscal_year == "2026-27"


def test_parse_kerala_budget_index_deduplicates_repeated_doc_ids() -> None:
    html = _INDEX_HTML.replace(
        "</table>",
        """
        <tr>
          <td onclick="dwldDoc('d6we5vb8ye5vf4u');">Revised Annual Financial Statement</td>
          <td>19-06-2026</td>
          <td>2026-27</td>
        </tr>
        </table>""",
    )
    documents = kerala.parse_kerala_budget_documents(html)
    assert [doc.doc_id for doc in documents] == [
        "d6we5vb8ye5vf4u",
        "d6we5vb8ye5vi1r",
    ]


def test_document_as_record_includes_provenance() -> None:
    document = kerala.parse_kerala_budget_documents(_INDEX_HTML)[0]
    record = document.as_record()
    assert record["record_type"] == "kerala_budget_document"
    assert record["official_publisher"] == "Finance Department, Government of Kerala"
    assert record["jurisdiction"] == "State:Kerala"
    assert record["source_url"] == document.download_url


def test_resolve_download_url_uses_official_viewer() -> None:
    assert kerala.resolve_kerala_budget_download_url("d6we5vb8ye5vf4u") == (
        "https://www.finance.kerala.gov.in/includeWeb/fileViewer.jsp"
        "?dId=d6we5vb8ye5vf4u"
    )


@pytest.mark.parametrize("doc_id", ["", "abc", "../etc/passwd", "abc def", "a" * 65])
def test_invalid_doc_ids_are_rejected(doc_id: str) -> None:
    with pytest.raises(ValueError):
        kerala.resolve_kerala_budget_download_url(doc_id)


def test_non_kerala_landing_page_is_rejected() -> None:
    with pytest.raises(ValueError, match="non-Kerala"):
        kerala.parse_kerala_budget_documents(
            "<html></html>", landing_page="https://example.com/budget"
        )


def test_fetch_budget_index_uses_injected_client() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "www.finance.kerala.gov.in"
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text=_INDEX_HTML,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    try:
        documents = kerala.fetch_kerala_budget_documents(client=client)
    finally:
        client.close()

    assert [doc.doc_id for doc in documents] == [
        "d6we5vb8ye5vf4u",
        "d6we5vb8ye5vi1r",
    ]


def test_fetch_budget_index_rejects_non_html_content_type() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/pdf"},
            content=b"%PDF-1.7",
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    try:
        with pytest.raises(ValueError, match="non-HTML"):
            kerala.fetch_kerala_budget_documents(client=client)
    finally:
        client.close()


def test_fetch_budget_index_rejects_oversized_page() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text=_INDEX_HTML,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    try:
        with pytest.raises(ValueError, match="maximum allowed size"):
            kerala.fetch_kerala_budget_documents(client=client, max_bytes=10)
    finally:
        client.close()


def test_fetch_budget_index_rejects_redirect_off_kerala_host() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.finance.kerala.gov.in":
            return httpx.Response(
                302, headers={"location": "https://example.com/not-kerala.html"}
            )
        return httpx.Response(
            200, headers={"content-type": "text/html"}, text="<html></html>"
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    try:
        with pytest.raises(ValueError, match="non-Kerala"):
            kerala.fetch_kerala_budget_documents(client=client)
    finally:
        client.close()


def test_load_budget_document_forces_pdf_and_passes_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, Any] = {}

    def fake_load_dataset(
        url: str,
        fmt: str | None = None,
        context: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> object:
        calls["url"] = url
        calls["fmt"] = fmt
        calls["context"] = context
        calls["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(kerala, "load_dataset", fake_load_dataset)

    result = kerala.load_kerala_budget_document(
        "d6we5vb8ye5vf4u", expected_sha256="a" * 64
    )

    assert result is not None
    assert calls["url"] == (
        "https://www.finance.kerala.gov.in/includeWeb/fileViewer.jsp"
        "?dId=d6we5vb8ye5vf4u"
    )
    assert calls["fmt"] == "pdf"
    assert calls["kwargs"]["expected_sha256"] == "a" * 64
    assert calls["context"]["official_publisher"] == (
        "Finance Department, Government of Kerala"
    )
    assert calls["context"]["source_landing_page"] == (
        "https://www.finance.kerala.gov.in/bdgtDcs.jsp"
    )


def test_kerala_budget_source_metadata_is_tier_one() -> None:
    source = kerala.KERALA_BUDGET_SOURCE
    assert source["ingestion_tier"] == 1
    assert source["connector_type"] == "http_pdf"
    assert source["official_publisher"] == "Finance Department, Government of Kerala"
    assert (
        source["source_landing_page"] == "https://www.finance.kerala.gov.in/bdgtDcs.jsp"
    )
