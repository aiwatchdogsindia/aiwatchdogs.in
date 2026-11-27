"""Kerala Finance Department budget-document connector (Tier 1).

The Kerala Finance Department publishes its budget papers on
``https://www.finance.kerala.gov.in/bdgtDcs.jsp``.  Each row on that public
index carries a JavaScript ``dwldDoc('...')`` handler; the actual PDF is served
by ``/includeWeb/fileViewer.jsp?dId=...`` with
``Content-Disposition: attachment``.

This connector:

* parses the public index into :class:`KeralaBudgetDocument` records;
* resolves the official download URL for a document id; and
* loads the PDF through :func:`aiwatchdogs.connectors.loader.load_dataset`, so
  the normal fail-closed SHA-256, bounded-download and PII-scrubbing guarantees
  apply.

It is a Tier-1 connector: no login, CAPTCHA, cookie or credential is used.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Final
from urllib.parse import quote, urljoin, urlparse

import httpx

from aiwatchdogs.connectors.loader import USER_AGENT, load_dataset
from aiwatchdogs.core.models import LoadedDataset
from aiwatchdogs.core.safety import validate_safe_http_url

KERALA_FINANCE_HOSTS: Final[frozenset[str]] = frozenset(
    {"www.finance.kerala.gov.in", "finance.kerala.gov.in"}
)
KERALA_BUDGET_LANDING_URL: Final[str] = "https://www.finance.kerala.gov.in/bdgtDcs.jsp"
KERALA_BUDGET_VIEWER_PATH: Final[str] = "includeWeb/fileViewer.jsp"
KERALA_BUDGET_PUBLISHER: Final[str] = "Finance Department, Government of Kerala"
KERALA_BUDGET_JURISDICTION: Final[str] = "State:Kerala"
MAX_INDEX_BYTES: Final[int] = 5 * 1024 * 1024

KERALA_BUDGET_SOURCE: Final[dict[str, Any]] = {
    "id": "src-kerala-finance",
    "slug": "kerala-finance-budget",
    "name": "Kerala Finance Department Budget Documents",
    "pillar": "budget",
    "authority": "Finance Department, Government of Kerala",
    "base_url": "https://www.finance.kerala.gov.in",
    "official_publisher": KERALA_BUDGET_PUBLISHER,
    "source_landing_page": KERALA_BUDGET_LANDING_URL,
    "mirror_name": "",
    "retrieval_url": KERALA_BUDGET_LANDING_URL,
    "ingestion_tier": 1,
    "connector_type": "http_pdf",
    "data_contents": (
        "Kerala Annual Financial Statements, Revised Annual Financial Statements "
        "and related budget documents"
    ),
    "format_access": "Official Finance Department PDF downloads (public, no login)",
    "robots_policy": "strict-polite-1rps",
}

_DOC_ID_RE = re.compile(r"^[A-Za-z0-9]{6,64}$")
_DWLD_DOC_RE = re.compile(r"dwldDoc\(\s*['\"]([A-Za-z0-9]{6,64})['\"]\s*\)")
_DID_QUERY_RE = re.compile(r"[?&]dId=([A-Za-z0-9]{6,64})")
_DATE_ISO_RE = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
_DATE_DMY_RE = re.compile(r"\b(\d{2})-(\d{2})-(20\d{2})\b")
_FISCAL_YEAR_RE = re.compile(r"\b(20\d{2})\s*[-–]\s*(\d{2}|20\d{2})\b")


@dataclass(frozen=True, slots=True)
class KeralaBudgetDocument:
    """One document listed on the Kerala Finance Department budget page."""

    doc_id: str
    title: str
    published_on: str | None
    fiscal_year: str | None
    download_url: str
    landing_page: str = KERALA_BUDGET_LANDING_URL

    def as_record(self) -> dict[str, Any]:
        """Return a provenance-friendly record for the generic dataset pipeline."""
        return {
            "record_type": "kerala_budget_document",
            "doc_id": self.doc_id,
            "title": self.title,
            "published_on": self.published_on,
            "fiscal_year": self.fiscal_year,
            "download_url": self.download_url,
            "landing_page": self.landing_page,
            "official_publisher": KERALA_BUDGET_PUBLISHER,
            "jurisdiction": KERALA_BUDGET_JURISDICTION,
            "source_url": self.download_url,
        }


def _clean_text(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").split())


def _parse_date(value: str) -> str | None:
    """Normalise the printed ``DD-MM-YYYY`` / hidden ``YYYY-MM-DD`` date to ISO."""
    iso = _DATE_ISO_RE.search(value)
    if iso:
        return f"{iso.group(1)}-{iso.group(2)}-{iso.group(3)}"
    dmy = _DATE_DMY_RE.search(value)
    if dmy:
        return f"{dmy.group(3)}-{dmy.group(2)}-{dmy.group(1)}"
    return None


def _parse_fiscal_year(value: str) -> str | None:
    match = _FISCAL_YEAR_RE.search(value)
    if not match:
        return None
    end = match.group(2)
    return f"{match.group(1)}-{end[-2:]}"


def _validate_kerala_finance_url(url: str) -> str:
    cleaned = validate_safe_http_url(url, require_https=True, allow_private=False)
    host = (urlparse(cleaned).hostname or "").lower().rstrip(".")
    if host not in KERALA_FINANCE_HOSTS:
        raise ValueError(
            f"Refusing non-Kerala Finance Department host {host!r} in {url!r}."
        )
    return cleaned


def _validate_doc_id(doc_id: str) -> str:
    cleaned = str(doc_id).strip()
    if not _DOC_ID_RE.fullmatch(cleaned):
        raise ValueError(
            "Kerala budget document id must be 6-64 ASCII letters/digits "
            f"(got {doc_id!r})."
        )
    return cleaned


def resolve_kerala_budget_download_url(
    doc_id: str,
    *,
    landing_page: str = KERALA_BUDGET_LANDING_URL,
) -> str:
    """Build the official ``fileViewer.jsp`` URL for a Kerala budget document id."""
    base = _validate_kerala_finance_url(landing_page)
    clean_id = _validate_doc_id(doc_id)
    return urljoin(base, f"{KERALA_BUDGET_VIEWER_PATH}?dId={quote(clean_id, safe='')}")


class _BudgetIndexParser(HTMLParser):
    """Extract ``dwldDoc`` rows from the Kerala budget index without regex backtracking."""

    def __init__(self, *, landing_page: str) -> None:
        super().__init__(convert_charrefs=True)
        self.landing_page = landing_page
        self.documents: list[KeralaBudgetDocument] = []
        self._seen: set[str] = set()
        self._in_row = False
        self._in_cell = False
        self._cells: list[str] = []
        self._cell_parts: list[str] = []
        self._row_doc_id: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.lower()
        if name == "tr":
            self._in_row = True
            self._cells = []
            self._row_doc_id = None
        elif name in {"td", "th"} and self._in_row:
            self._in_cell = True
            self._cell_parts = []
            self._capture_doc_id(attrs)
        elif name == "a" and self._in_row:
            self._capture_doc_id(attrs)

    def handle_endtag(self, tag: str) -> None:
        name = tag.lower()
        if name in {"td", "th"} and self._in_cell:
            self._cells.append(_clean_text(" ".join(self._cell_parts)))
            self._in_cell = False
        elif name == "tr" and self._in_row:
            self._flush_row()
            self._in_row = False

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cell_parts.append(data)

    def _capture_doc_id(self, attrs: list[tuple[str, str | None]]) -> None:
        for key, value in attrs:
            if not value or key.lower() not in {"onclick", "href"}:
                continue
            match = _DWLD_DOC_RE.search(value) or _DID_QUERY_RE.search(value)
            if match:
                self._row_doc_id = match.group(1)
                return

    def _flush_row(self) -> None:
        doc_id = self._row_doc_id
        if not doc_id or doc_id in self._seen:
            return
        title = self._cells[0] if self._cells else ""
        if not title:
            return
        published_on = _parse_date(self._cells[1]) if len(self._cells) > 1 else None
        fiscal_year = (
            _parse_fiscal_year(self._cells[2]) if len(self._cells) > 2 else None
        )
        if fiscal_year is None:
            fiscal_year = _parse_fiscal_year(title)
        self._seen.add(doc_id)
        self.documents.append(
            KeralaBudgetDocument(
                doc_id=doc_id,
                title=title,
                published_on=published_on,
                fiscal_year=fiscal_year,
                download_url=resolve_kerala_budget_download_url(
                    doc_id, landing_page=self.landing_page
                ),
                landing_page=self.landing_page,
            )
        )


def parse_kerala_budget_documents(
    html: str,
    *,
    landing_page: str = KERALA_BUDGET_LANDING_URL,
) -> list[KeralaBudgetDocument]:
    """Parse the Kerala Finance Department budget index into document records."""
    base = _validate_kerala_finance_url(landing_page)
    parser = _BudgetIndexParser(landing_page=base)
    parser.feed(html)
    parser.close()
    return parser.documents


def _read_index_html(
    client: httpx.Client,
    url: str,
    *,
    max_bytes: int,
) -> tuple[str, str]:
    with client.stream("GET", url, headers={"User-Agent": USER_AGENT}) as response:
        response.raise_for_status()
        final_url = _validate_kerala_finance_url(str(response.url))
        content_type = response.headers.get("content-type", "").lower()
        if (
            content_type
            and "html" not in content_type
            and "text/plain" not in content_type
        ):
            raise ValueError(
                f"Kerala budget index returned non-HTML content type {content_type!r}."
            )
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_bytes():
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(
                    f"Kerala budget index exceeds maximum allowed size of {max_bytes} bytes."
                )
            chunks.append(chunk)
        encoding = response.encoding or "utf-8"
        return b"".join(chunks).decode(encoding, errors="replace"), final_url


def fetch_kerala_budget_documents(
    *,
    client: httpx.Client | None = None,
    timeout: float = 30.0,
    max_bytes: int = MAX_INDEX_BYTES,
) -> list[KeralaBudgetDocument]:
    """Fetch and parse the live Kerala Finance Department budget index.

    ``client`` is injectable for offline tests.  Redirects are followed only to
    the same official Kerala Finance hosts.
    """
    landing = _validate_kerala_finance_url(KERALA_BUDGET_LANDING_URL)
    owns_client = client is None
    if client is None:
        client = httpx.Client(
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
            timeout=timeout,
        )
    try:
        html, final_url = _read_index_html(client, landing, max_bytes=max_bytes)
    finally:
        if owns_client:
            client.close()
    return parse_kerala_budget_documents(html, landing_page=final_url)


def load_kerala_budget_document(
    doc_id: str,
    *,
    expected_sha256: str | None = None,
    landing_page: str = KERALA_BUDGET_LANDING_URL,
) -> LoadedDataset:
    """Load one official Kerala budget PDF through the fail-closed dataset loader.

    Passing ``expected_sha256`` makes a digest mismatch raise
    :class:`aiwatchdogs.connectors.loader.DigestMismatchError` before parsing.
    """
    base = _validate_kerala_finance_url(landing_page)
    url = resolve_kerala_budget_download_url(doc_id, landing_page=base)
    return load_dataset(
        url,
        fmt="pdf",
        context={
            "official_publisher": KERALA_BUDGET_PUBLISHER,
            "source_landing_page": base,
            "retrieval_url": url,
            "jurisdiction": KERALA_BUDGET_JURISDICTION,
        },
        expected_sha256=expected_sha256,
    )


__all__ = [
    "KERALA_BUDGET_JURISDICTION",
    "KERALA_BUDGET_LANDING_URL",
    "KERALA_BUDGET_PUBLISHER",
    "KERALA_BUDGET_SOURCE",
    "KERALA_FINANCE_HOSTS",
    "MAX_INDEX_BYTES",
    "KeralaBudgetDocument",
    "fetch_kerala_budget_documents",
    "load_kerala_budget_document",
    "parse_kerala_budget_documents",
    "resolve_kerala_budget_download_url",
]
