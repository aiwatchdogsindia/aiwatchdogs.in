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
    assert len(result["checks"]) == 46
    assert all(result["checks"][k] == "observed" for k in audit.LEGACY_CHECKS)
    assert result["checks"]["gov_email_domain"] == "observed"
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
    assert all(result["checks"][k] == "not_observed" for k in audit.LEGACY_CHECKS)
    assert result["checks"]["https_valid_tls"] == "observed"
    assert result["checks"]["gov_domain_canonical"] == "observed"
    assert result["checks"]["gov_email_domain"] == "not_applicable"


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


def test_prioritizes_local_contact_and_handles_malformed_unrelated_links(monkeypatch):
    home = """
    <a href="http://[">Malformed unrelated link</a>
    <a href="/districts">District directory</a>
    <a href="https://another.gov.in/contact">Contact external service</a>
    <a href="/contact-us">Contact us</a>
    <a href="/about-us">About us</a>
    """
    seen = serve(
        monkeypatch,
        {
            HOME + "robots.txt": (404, {}, ""),
            HOME: (200, HTML, home),
            HOME + "contact-us": (200, HTML, CONTACT_HTML),
        },
    )
    result = audit.observe_site({"domain": HOST})
    assert seen == [HOME + "robots.txt", HOME, HOME + "contact-us"]
    assert result["checks"]["email"] == result["checks"]["about_link"] == "observed"


def test_regional_labels_and_contact_fragment(monkeypatch):
    home = """
    <h1>Citizen services for people in the district</h1>
    <a href="#phone">தொடர்பு</a><div id="phone">Tel: (011) 23456789</div>
    <a href="/a">எங்களைப் பற்றி</a><a href="/h"><img alt="உதவி"></a>"""
    seen = serve(monkeypatch, {HOME + "robots.txt": (404, {}, ""), HOME: (200, HTML, home)})
    result = audit.observe_site({"domain": HOST})
    assert seen == [HOME + "robots.txt", HOME]
    assert all(
        result["checks"][key] == "observed"
        for key in ("contact_link", "about_link", "help_link", "telephone")
    )


def test_handles_missing_anchor_without_guessing(monkeypatch):
    home = (
        '<main>This site provides public services to all residents.</main><a href="#missing">Contact Us</a>'
    )
    serve(monkeypatch, {HOME + "robots.txt": (404, {}, ""), HOME: (200, HTML, home)})
    result = audit.observe_site({"domain": HOST})
    assert result["checks"]["contact_link"] == "not_observed"


def test_office_order_and_empty_telephone_are_not_contact_evidence():
    page = audit.Page('<main>Office order 123456 was issued last week.</main><a href="tel:-------">Phone</a>')
    assert page.contact_fields() == {"postal_address": False, "telephone": False, "email": False}


@pytest.mark.parametrize(
    "html",
    [
        "<title>404 Page Not Found</title><main>The requested resource could not be found.</main>",
        '<title>Staff Login</title><p>Authorized staff only. Please enter your credentials.</p><input type="password">',
    ],
)
def test_soft_errors_and_login_walls_are_unassessable(monkeypatch, html):
    serve(monkeypatch, {HOME + "robots.txt": (404, {}, ""), HOME: (200, HTML, html)})
    result = audit.observe_site({"domain": HOST})
    assert result["reason"] == "unsupported_page" and not result["pages"]
    assert set(result["checks"].values()) == {"unassessable"}


def test_public_evidence_uses_only_generic_paths(monkeypatch):
    page = '<base href="/directory/"><p>District services for residents.</p><a href="someone">Contact Us</a>'
    seen = serve(
        monkeypatch,
        {
            HOME + "robots.txt": (404, {}, ""),
            HOME: (200, HTML, page),
            HOME + "directory/someone": (200, HTML, CONTACT_HTML),
        },
    )
    result = audit.observe_site({"domain": HOST})
    assert seen[-1] == HOME + "directory/someone" and result["checks"]["email"] == "observed"
    assert result["pages"] == [HOME]
    assert "someone" not in json.dumps(result)
    assert audit.public_evidence_url(HOME + "en/contact-us.aspx", HOST) == HOME + "en/contact-us.aspx"


def test_hidden_contacts_do_not_count(monkeypatch):
    home = """<main>The district's public services are listed here.</main>
    <div hidden><span>office@example.gov.in</span><a href="/contact/">Contact</a></div>
    <p style="display: none">Telephone: 011 23456789</p><a href="/help">Help</a>"""
    seen = serve(monkeypatch, {HOME + "robots.txt": (404, {}, ""), HOME: (200, HTML, home)})
    result = audit.observe_site({"domain": HOST})
    assert seen == [HOME + "robots.txt", HOME]
    assert all(result["checks"][key] == "not_observed" for key in ("contact_link", "email", "telephone"))
    assert result["checks"]["help_link"] == "observed"


FULL_COMPLIANT_HEADERS = {
    "content-type": "text/html; charset=utf-8",
    "strict-transport-security": "max-age=31536000; includeSubDomains",
    "x-content-type-options": "nosniff",
    "x-frame-options": "SAMEORIGIN",
    "server": "NIC-Web",
}

FULL_COMPLIANT_HOME = """<!doctype html>
<html lang="en-IN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="Official portal of Department of Education, Government of India">
  <link rel="stylesheet" href="/site.css">
  <title>Department of Education, Government of India</title>
</head>
<body>
  <header>
    <a href="#main-content">Skip to main content</a>
    <img src="/emblem.png" alt="State Emblem of India">
    <a href="/hi/">हिन्दी</a>
    <a href="/screen-reader-access">Screen Reader Access</a>
  </header>
  <nav>
    <a href="/about-us">About Us</a>
    <a href="/contact-us">Contact Us</a>
    <a href="/rti">Right to Information</a>
    <a href="/citizens-charter">Citizen's Charter</a>
    <a href="/acts-and-rules">Acts & Rules</a>
    <a href="/help">Help</a>
    <a href="/feedback">Feedback</a>
    <a href="/sitemap">Sitemap</a>
    <a href="https://pgportal.gov.in/" target="_blank" rel="noopener">CPGRAMS Public Grievances</a>
    <a href="https://www.india.gov.in/" target="_blank" rel="noopener">National Portal of India</a>
    <a href="https://data.gov.in/" target="_blank" rel="noopener">Open Data</a>
    <a href="https://x.com/govindia" target="_blank" rel="noopener">Official X</a>
  </nav>
  <main id="main-content">
    <h1>Department of Education Public Portal</h1>
    <form role="search"><label for="q">Search</label><input id="q" type="search"></form>
    <a href="/report.pdf">Annual Report (PDF, 1.2 MB)</a>
    <table><caption>Summary</caption><thead><tr><th scope="col">Scheme</th></tr></thead><tbody><tr><td>Schools</td></tr></tbody></table>
  </main>
  <footer>
    <p>Content owned by Ministry of Education, Government of India. Last updated on 26 November 2026.</p>
    <img src="/stqc-cqw.png" alt="STQC Certified Quality Website (CQW) GIGW Compliant">
    <a href="/website-policies">Website Policies</a>
    <a href="/privacy-policy">Privacy Policy</a>
    <a href="/terms-and-conditions">Terms and Conditions</a>
  </footer>
</body>
</html>"""

FULL_COMPLIANT_RTI = """
<h1>Right to Information — Suo Motu Disclosure under Section 4(1)(b)</h1>
<p>Central Public Information Officer (CPIO) and First Appellate Authority details are published in Manual 16.</p>
<p>Web Information Manager: Director (IT), Department of Education.</p>
"""


def test_full_46_checks_compliant_site_and_three_page_crawl(monkeypatch):
    seen = serve(
        monkeypatch,
        {
            HOME + "robots.txt": (404, {}, ""),
            HOME: (200, FULL_COMPLIANT_HEADERS, FULL_COMPLIANT_HOME),
            HOME + "contact-us": (200, HTML, CONTACT_HTML),
            HOME + "rti": (200, HTML, FULL_COMPLIANT_RTI),
        },
    )
    result = audit.observe_site({"domain": HOST})
    assert seen == [HOME + "robots.txt", HOME, HOME + "contact-us", HOME + "rti"]
    assert result["outcome"] == "observed" and result["reason"] is None
    assert result["pages"] == [HOME, HOME + "contact-us", HOME + "rti"]
    assert len(result["checks"]) == 46
    not_obs = {k: v for k, v in result["checks"].items() if v != "observed"}
    assert not_obs == {}, f"Expected all 46 checks observed, got {not_obs}"
    dumped = json.dumps(result)
    assert "office[at]" not in dumped and "12345678" not in dumped and "NIC-Web" not in dumped


def test_negative_signals_banner_leak_commercial_email_and_a11y_gaps(monkeypatch):
    bad_headers = {
        "content-type": "text/html; charset=utf-8",
        "server": "Apache/2.4.58 (Ubuntu)",
        "x-powered-by": "PHP/8.2.10",
    }
    bad_html = """<html><body>
    <div id="dup">Department portal currently under construction — coming soon.</div>
    <div id="dup">Email queries to depthelp@gmail.com for assistance.</div>
    <img src="/banner.jpg">
    <form><input type="text" name="query"></form>
    <table><tr><td>No header cells</td></tr></table>
    <a href="/notice.pdf">Download Notice</a>
    <a href="https://external.example.org/doc">External resource</a>
    </body></html>"""
    serve(monkeypatch, {HOME + "robots.txt": (404, {}, ""), HOME: (200, bad_headers, bad_html)})
    result = audit.observe_site({"domain": HOST})
    c = result["checks"]
    assert c["server_banner_obscured"] == "not_observed"
    assert c["hsts_header"] == "not_observed"
    assert c["security_headers"] == "not_observed"
    assert c["no_under_construction"] == "not_observed"
    assert c["unique_dom_ids"] == "not_observed"
    assert c["img_alt_text"] == "not_observed"
    assert c["form_input_labels"] == "not_observed"
    assert c["data_table_headers"] == "not_observed"
    assert c["download_metadata"] == "not_observed"
    assert c["external_link_notice"] == "not_observed"
    assert c["html_lang_attr"] == "not_observed"
    assert c["email"] == "observed"
    assert c["gov_email_domain"] == "not_observed"
    assert "Apache/2.4.58" not in json.dumps(result) and "depthelp@gmail.com" not in json.dumps(result)


def test_rti_sec24_exempt_agency_and_blocked_rti_subpage(monkeypatch):
    exempt_host = "bsf.gov.in"
    exempt_home = f"https://{exempt_host}/"
    serve(
        monkeypatch,
        {
            exempt_home + "robots.txt": (404, {}, ""),
            exempt_home: (200, HTML, "<main>Border Security Force official public information portal.</main>"),
        },
    )
    exempt_res = audit.observe_site({"domain": exempt_host})
    assert exempt_res["checks"]["rti_link"] == "not_applicable"
    assert exempt_res["checks"]["pio_faa_disclosure"] == "not_applicable"

    # Non-exempt host whose RTI subpage is disallowed by robots.txt marks pio_faa_disclosure unassessable
    serve(
        monkeypatch,
        {
            HOME + "robots.txt": (200, {}, "User-agent: *\nDisallow: /rti"),
            HOME: (200, HTML, "<main>District portal with public citizen services.</main><a href='/rti'>RTI</a>"),
        },
    )
    blocked_rti = audit.observe_site({"domain": HOST})
    assert blocked_rti["outcome"] == "observed"
    assert blocked_rti["reason"] == "rti_unavailable"
    assert blocked_rti["checks"]["rti_link"] == "observed"
    assert blocked_rti["checks"]["pio_faa_disclosure"] == "unassessable"


def test_direct_checks_do_not_conflate_footer_viewport_tenders_or_non_hindi_scripts(monkeypatch):
    """P1 regressions: STQC badge != ownership footer; legacy plugins != viewport;
    Open Data != tenders; Hindi-English OLA != non-Hindi Indic scripts."""
    tamil_legacy_html = """<!doctype html>
    <html lang="en-IN">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <link rel="stylesheet" href="/app.css">
      <title>State Transport Department Portal</title>
    </head>
    <body>
      <main>
        <p>Official portal of Tamil Nadu Transport Department. தமிழ் version available.</p>
        <a href="/ta/">தமிழ்</a>
        <a href="/tenders">Tenders</a>
        <embed src="/banner.swf" type="application/x-shockwave-flash" width="300" height="100">
      </main>
      <footer>Content owned by Government of Tamil Nadu, hosted by NIC.</footer>
    </body></html>"""
    serve(monkeypatch, {HOME + "robots.txt": (404, {}, ""), HOME: (200, HTML, tamil_legacy_html)})
    res = audit.observe_site({"domain": HOST})
    c = res["checks"]
    assert c["ownership_statement"] == "observed"
    assert c["stqc_cqw_badge"] == "not_observed"
    assert c["responsive_viewport"] == "observed"
    assert c["no_legacy_plugins"] == "not_observed"
    assert c["acts_tenders_link"] == "observed"
    assert c["open_data_link"] == "not_observed"
    assert c["bilingual_unicode"] == "observed"
    assert c["hindi_english_bilingual"] == "not_observed"

    # Bare legacy plugin embeds/objects WITHOUT explicit type or classid MUST fail no_legacy_plugins
    for plugin_tag in (
        '<embed src="/banner.swf">',
        '<object data="/intro.swf?v=1"></object>',
        '<object codebase="/legacy/viewer.cab"></object>',
        '<applet code="NoticeTicker.class"></applet>',
    ):
        plugin_html = f"<main>Official department public portal with citizen services. {plugin_tag}</main>"
        serve(monkeypatch, {HOME + "robots.txt": (404, {}, ""), HOME: (200, HTML, plugin_html)})
        r_plug = audit.observe_site({"domain": HOST})
        assert r_plug["checks"]["no_legacy_plugins"] == "not_observed", plugin_tag

    # Verify STQC/CQW certification variants and API Setu Open Data link match cleanly
    for badge_phrase in ("STQC Certified", "STQC certification valid", "CQW Certificate", "CQW certified"):
        page_html = f"<main>Official department public portal with services. {badge_phrase}.</main><a href='https://apisetu.gov.in/'>API Setu</a>"
        serve(monkeypatch, {HOME + "robots.txt": (404, {}, ""), HOME: (200, HTML, page_html)})
        r_badge = audit.observe_site({"domain": HOST})
        assert r_badge["checks"]["stqc_cqw_badge"] == "observed", badge_phrase
        assert r_badge["checks"]["open_data_link"] == "observed"


def test_slow_rti_subpage_timeout_emits_valid_rti_unavailable_without_contact_link(monkeypatch):
    """P1 regression: a timeout fetching /rti on a homepage without a contact link MUST emit
    reason='rti_unavailable' with pio_faa_disclosure='unassessable' and pass server validation."""
    import asyncio
    from aiwatchdogs.hub.website_reports import _validate_evidence

    monkeypatch.setattr(audit, "SITE_TIMEOUT_SECONDS", 0.25)

    async def slow_page(reader, url):
        if url.endswith("/rti"):
            await asyncio.sleep(1.0)
        return url, audit.Page(
            "<main>Official department public portal with citizen services and records.</main>"
            "<a href='/rti'>Right to Information</a>"
        )

    monkeypatch.setattr(audit.Reader, "page", slow_page)
    res = audit.observe_site({"domain": HOST})
    assert res["outcome"] == "observed"
    assert res["reason"] == "rti_unavailable"
    assert res["pages"] == [HOME]
    assert res["checks"]["contact_link"] == "not_observed"
    assert res["checks"]["rti_link"] == "observed"
    assert res["checks"]["pio_faa_disclosure"] == "unassessable"
    # Server batch validator MUST accept this observation cleanly (zero 422 rejection)
    _validate_evidence(res, HOST)

    # Also verify when BOTH /contact (fast) and /rti (times out) exist on homepage
    async def fast_contact_slow_rti(reader, url):
        if url.endswith("/rti"):
            await asyncio.sleep(1.0)
        if url.endswith("/contact"):
            return url, audit.Page(CONTACT_HTML)
        return url, audit.Page(
            "<main>Official department public portal with citizen services and records.</main>"
            "<a href='/contact'>Contact Us</a><a href='/rti'>Right to Information</a>"
        )

    monkeypatch.setattr(audit.Reader, "page", fast_contact_slow_rti)
    res2 = audit.observe_site({"domain": HOST})
    assert res2["outcome"] == "observed"
    assert res2["reason"] == "rti_unavailable"
    assert res2["pages"] == [HOME, HOME + "contact"]
    assert res2["checks"]["email"] == "observed"
    assert res2["checks"]["gov_email_domain"] == "observed"
    assert res2["checks"]["pio_faa_disclosure"] == "unassessable"
    _validate_evidence(res2, HOST)
