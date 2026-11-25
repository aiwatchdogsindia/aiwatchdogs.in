"""Government website observations use bounded, public, passive reads."""

import ipaddress
import json
import socket

import httpx
import pytest

from aiwatchdogs.engines import gov_pages as audit


HOST = "example.gov.in"
HOME = f"https://{HOST}/"
HTML = {"content-type": "text/html; charset=utf-8"}
HOME_HTML = """
<title>Department of Education</title>
<main>Citizen information and services for public schools in the district.</main>
<a href="/contact/">Contact us</a><a href="/about/">About us</a>
<a href="/help/">Help</a><footer>Last updated on 17 November 2026</footer>
"""
CONTACT_HTML = """
<h1>Contact the Department of Education</h1>
Postal address: Department Headquarters, New Delhi 110 001
office[at]example[dot]gov[dot]in. Telephone: +91 11 12345678
"""


def serve(monkeypatch, pages):
    seen = []

    async def request(self, url, limit):
        seen.append(url)
        self.last_request[httpx.URL(url).host] = 0
        assert url in pages, url
        return pages[url]

    monkeypatch.setattr(audit.Reader, "request", request)
    return seen


def test_evidence_retains_presence_without_contact_values(monkeypatch):
    seen = serve(
        monkeypatch,
        {
            HOME + "robots.txt": (404, {}, ""),
            HOME: (200, HTML, HOME_HTML),
            HOME + "contact/": (200, HTML, CONTACT_HTML),
        },
    )
    result = audit.observe_site({"domain": HOST})
    assert list(seen) == [HOME + "robots.txt", HOME, HOME + "contact/"]
    assert set(result["checks"].values()) == {"observed"}
    assert result["pages"] == [HOME, HOME + "contact/"]
    assert result["reason"] is None and result["observed_at"].endswith("Z")
    assert "110 001" not in json.dumps(result)
    assert "12345678" not in json.dumps(result)
    assert "office[at]" not in json.dumps(result)


@pytest.mark.parametrize(
    "url",
    [
        "https://other.gov.in/contact",
        "http://example.gov.in/a",
        "/contact?name=someone",
        "/contact/person%40example.gov.in",
        "https://internal/",
    ],
)
def test_contact_limit_preserves_homepage(monkeypatch, url):
    seen = serve(
        monkeypatch,
        {HOME + "robots.txt": (404, {}, ""), HOME: (200, HTML, HOME_HTML.replace("/contact/", url))},
    )
    result = audit.observe_site({"domain": HOST})
    assert seen == [HOME + "robots.txt", HOME]
    assert result["outcome"] == "observed" and result["reason"] == "contact_unavailable"
    assert result["checks"]["contact_link"] == "observed"
    assert all(result["checks"][key] == "unassessable" for key in audit.CONTACT_FIELDS)


@pytest.mark.parametrize(
    "rules",
    [
        "User-agent: *\nDisallow: /",
        "User-agent: AIWatchdogs\nDisallow: /*",
        "User-agent: AIWatchdogs\nCrawl-delay: 30",
    ],
)
def test_respects_robots_before_reading_home(monkeypatch, rules):
    seen = serve(monkeypatch, {HOME + "robots.txt": (200, {"content-type": "text/plain"}, rules)})
    result = audit.observe_site({"domain": HOST})
    assert seen == [HOME + "robots.txt"]
    assert result["reason"] == "robots_restricted" and result["pages"] == []


def test_restricted_contact_is_unknown(monkeypatch):
    seen = serve(
        monkeypatch,
        {HOME + "robots.txt": (200, {}, "User-agent: *\nDisallow: /contact"), HOME: (200, HTML, HOME_HTML)},
    )
    result = audit.observe_site({"domain": HOST})
    assert result["reason"] == "contact_unavailable" and result["checks"]["email"] == "unassessable"
    assert seen == [HOME + "robots.txt", HOME]


def test_challenge_page_is_not_a_deficiency(monkeypatch):
    serve(
        monkeypatch,
        {
            HOME + "robots.txt": (404, {}, ""),
            HOME: (
                200,
                HTML,
                "<title>Just a moment...</title><p>Please wait while we verify you are human.</p>",
            ),
        },
    )
    result = audit.observe_site({"domain": HOST})
    assert result["outcome"] == "unassessable" and set(result["checks"].values()) == {"unassessable"}


def test_dns_rejects_mixed_public_and_private_answers(monkeypatch):
    def resolve(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443)) for ip in ["1.1.1.1", "127.0.0.1"]]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    result = audit.observe_site({"domain": HOST})
    assert result["reason"] == "fetch_failed" and result["pages"] == []


@pytest.mark.parametrize(
    "domain", ["user@x.gov.in", "example.gov.in.evil", "x.gov.in:443", "127.0.0.1", "x.gov.in/?x", "gov.in"]
)
def test_only_inventory_hostnames(domain):
    assert audit.observe_site({"domain": domain})["reason"] == "invalid_domain"


def test_pin_address_keep_tls_and_host_identity(monkeypatch):
    calls = []

    def resolve(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443))]

    def handle(request):
        calls.append(request)
        assert ipaddress.ip_address(request.url.host).is_global
        assert request.headers["Host"] == HOST and request.extensions["sni_hostname"] == HOST
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(
            200,
            headers=HTML,
            stream=httpx.ByteStream(
                b"<p>This page provides public services for citizens of the district.</p>"
            ),
        )

    client = httpx.AsyncClient

    def make_client(**options):
        assert options["trust_env"] is False and options["follow_redirects"] is False
        return client(transport=httpx.MockTransport(handle), **options)

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(httpx, "AsyncClient", make_client)
    result = audit.observe_site({"domain": HOST})
    assert result["reason"] is None and len(calls) == 2
    assert set(result["checks"].values()) == {"not_observed"}


def test_uses_manifest_landing_page_and_www_alias(monkeypatch):
    base = "https://www." + HOST + "/"
    seen = serve(
        monkeypatch,
        {
            base + "robots.txt": (404, {}, ""),
            base + "en/home": (200, HTML, HOME_HTML),
            base + "contact/": (200, HTML, CONTACT_HTML),
        },
    )
    result = audit.observe_site({"domain": HOST, "url": base + "en/home"})
    assert result["pages"] == [base + "en/home", base + "contact/"]
    assert seen == [base + "robots.txt", *result["pages"]]


def test_site_cannot_supply_another_host_as_start_url(monkeypatch):
    seen = serve(monkeypatch, {})
    result = audit.observe_site({"domain": HOST, "url": "https://unrelated.gov.in/index"})
    assert result["reason"] == "unsupported_page" and result["pages"] == []
    assert not seen
