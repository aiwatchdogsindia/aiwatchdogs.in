"""Read two citizen-facing pages and retain only bounded GIGW observation data."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html.parser import HTMLParser
import ipaddress
import re
import socket
from urllib.parse import unquote, urljoin, urlsplit

import httpx
from protego import Protego

from aiwatchdogs.core.safety import is_public_ip_address


CHECKS = ("contact_link", "email", "postal_address", "telephone", "about_link", "updated_date", "help_link")
CONTACT_FIELDS = ("email", "postal_address", "telephone")
AGENT = "AIWatchdogs"
USER_AGENT = "AIWatchdogs/1.0 (aiwatchdogs.in; public website content review)"
DNS_POOL = ThreadPoolExecutor(max_workers=6, thread_name_prefix="gov-page-dns")
LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
DOMAIN = re.compile(rf"(?:{LABEL}\.)+gov\.in\Z", re.ASCII)
CONTACT = re.compile(r"contact|directory|who.s.who|संपर्क|सम्पर्क", re.I)
EMAIL = re.compile(
    r"[\w.+-]{1,64}\s*(?:@|\[at\]|\(at\))\s*[\w-]+(?:\s*(?:\.|\[dot\]|\(dot\))\s*[\w-]+)+", re.I
)
PHONE = re.compile(r"(?:phone|tel(?:ephone)?|दूरभाष)\s*(?:no\.?)?\s*[:.-]?\s*\+?\d[\d() -]{6,19}\d", re.I)
ADDRESS = re.compile(r"(?:address|office|पता|कार्यालय)\b.{0,180}?\b[1-9]\d{2}\s?\d{3}\b", re.I)
UPDATED = re.compile(
    r"(?:last\s+(?:updated|reviewed)|(?:अंतिम|अन्तिम)\s+(?:अद्यतन|अपडेट))\s*(?:on|दिनांक)?\s*[:\-]?\s*(?:\d{1,4}[- ./,][\w]+[- ./,]+\d{1,4}|[a-z]{3,9}\s+\d{1,2},?\s+\d{4})",
    re.I,
)


class Unavailable(Exception):
    """Reason codes are published, so they must be independent of response content."""


def _page_url(base: str, target: str, domain: str) -> str:
    url = urlsplit(urljoin(base, target))
    bare = domain.removeprefix("www.")
    path = unquote(url.path)
    if (
        url.scheme != "https"
        or url.netloc not in {bare, "www." + bare}
        or not DOMAIN.fullmatch(url.netloc)
        or url.query
        or len(url.geturl()) > 256
        or (url.path and not url.path.isascii())
        or any(c in path for c in ("@", "\\", "?", "#"))
        or re.search(r"[\s\x00-\x1f\x7f]|\d{7,}", path)
        or any(segment in {".", ".."} for segment in path.split("/"))
    ):
        raise Unavailable("unsupported_page")
    return url._replace(fragment="", path=url.path or "/").geturl()


class Page(HTMLParser):
    def __init__(self, html: str):
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self.text: list[str] = []
        self._hidden = 0
        self._href = ""
        self._label: list[str] = []
        self.feed(html)
        self.close()
        self.body = " ".join(" ".join(self.text).split())
        if len(self.body) < 40 or re.search(
            r"verify (?:that )?you are human|checking your browser|enable javascript to (?:continue|run this app)|access denied|just a moment\.\.\.",
            self.body[:1200],
            re.I,
        ):
            raise Unavailable("unsupported_page")

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "template", "noscript"}:
            self._hidden += 1
        if tag == "a" and not self._hidden:
            attributes = dict(attrs)
            self._href = attributes.get("href") or ""
            self._label = [attributes.get("aria-label") or "", attributes.get("title") or ""]

    def handle_data(self, text):
        if not self._hidden:
            self.text.append(text)
            if self._href:
                self._label.append(text)

    def handle_endtag(self, tag):
        if tag in {"script", "style", "template", "noscript"} and self._hidden:
            self._hidden -= 1
        if tag == "a" and self._href:
            self.links.append((self._href, " ".join(self._label)))
            self._href = ""

    def link(self, pattern: re.Pattern) -> str | None:
        for href, label in self.links:
            if not href.startswith("#") and urlsplit(href).scheme in {"", "http", "https"}:
                if pattern.search(label) or pattern.search(unquote(urlsplit(href).path)):
                    return href
        return None

    def contact_fields(self) -> dict[str, bool]:
        return {
            "email": bool(
                EMAIL.search(self.body)
                or any(h.lower().startswith("mailto:") and EMAIL.match(h[7:]) for h, _ in self.links)
            ),
            "postal_address": bool(ADDRESS.search(self.body)),
            "telephone": bool(
                PHONE.search(self.body)
                or any(re.match(r"^tel:\+?[\d() -]{7,20}$", h, re.I) for h, _ in self.links)
            ),
        }


class Reader:
    def __init__(self, domain: str):
        self.domain = domain
        self.addresses: dict[str, str] = {}
        self.rules: dict[str, Protego] = {}
        self.last_request: dict[str, float] = {}

    async def request(self, url: str, limit: int):
        host = urlsplit(url).hostname
        if host not in self.addresses:
            future = DNS_POOL.submit(socket.getaddrinfo, host, 443, type=socket.SOCK_STREAM)
            infos = await asyncio.wait_for(asyncio.wrap_future(future), timeout=3)
            addresses = [ipaddress.ip_address(info[4][0]) for info in infos]
            if not addresses or not all(is_public_ip_address(ip) for ip in addresses):
                raise Unavailable("fetch_failed")
            # Prefer a public IPv4 answer where the volunteer has no IPv6 route.
            self.addresses[host] = str(min(addresses, key=lambda ip: ip.version))
        pinned = httpx.URL(url).copy_with(host=self.addresses[host])
        self.last_request[host] = asyncio.get_running_loop().time()
        # Only the checked numeric address is passed to the transport. TLS still validates the named site.
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=3) as client:
            async with client.stream(
                "GET",
                pinned,
                headers={
                    "Host": host,
                    "User-Agent": USER_AGENT,
                    "Accept-Encoding": "identity",
                },
                extensions={"sni_hostname": host},
            ) as response:
                if response.status_code != 200:
                    return response.status_code, response.headers, ""
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise Unavailable("unsupported_page")
                raw = bytearray()
                async for chunk in response.aiter_raw():
                    if len(raw) + len(chunk) > limit:
                        raise Unavailable("unsupported_page")
                    raw.extend(chunk)
                return 200, response.headers, raw.decode(response.encoding or "utf-8", errors="replace")

    async def permission(self, url: str):
        host = urlsplit(url).hostname
        if host not in self.rules:
            robots_url = f"https://{host}/robots.txt"
            status, headers, body = await self.request(robots_url, 512_000)
            if status in {301, 302, 303, 307, 308} and headers.get("location"):
                # A common site-wide www redirect can also apply to robots.txt.
                location = _page_url(robots_url, headers["location"], self.domain)
                if urlsplit(location).path != "/robots.txt":
                    raise Unavailable("robots_restricted")
                status, headers, body = await self.request(location, 512_000)
            if status not in {200, 404, 410}:
                raise Unavailable("robots_restricted")
            if status == 200 and (
                "html" in headers.get("content-type", "").lower() or body.lstrip().startswith("<")
            ):
                raise Unavailable("robots_restricted")
            self.rules[host] = Protego.parse(body)
        rules = self.rules[host]
        if not rules.can_fetch(url, AGENT):
            raise Unavailable("robots_restricted")
        window = rules.visit_time(AGENT)
        if window:
            now = datetime.now(timezone.utc).time()
            if window.start_time <= window.end_time:
                permitted = window.start_time <= now <= window.end_time
            else:
                permitted = now >= window.start_time or now <= window.end_time
            if not permitted:
                raise Unavailable("robots_restricted")
        delay = max(0.15, rules.crawl_delay(AGENT) or 0)
        rate = rules.request_rate(AGENT)
        if rate and rate.requests:
            delay = max(delay, rate.seconds / rate.requests)
        if delay > 3:
            raise Unavailable("robots_restricted")
        await asyncio.sleep(max(0, self.last_request[host] + delay - asyncio.get_running_loop().time()))

    async def page(self, url: str):
        for _ in range(3):
            await self.permission(url)
            status, headers, body = await self.request(url, 512_000)
            if status == 200 and headers.get("content-type", "").split(";")[0].strip().lower() in {
                "text/html",
                "application/xhtml+xml",
            }:
                return url, Page(body)
            if status not in {301, 302, 303, 307, 308} or not headers.get("location"):
                raise Unavailable("unsupported_page" if status == 200 else "fetch_failed")
            url = _page_url(url, headers["location"], self.domain)
        raise Unavailable("fetch_failed")


async def _observe(result: dict, start_url: str):
    domain = result["domain"]
    reader = Reader(domain)
    home_url, home = await reader.page(start_url)
    contact = home.link(CONTACT)
    signals = {
        "contact_link": bool(contact),
        "about_link": bool(home.link(re.compile(r"about|परिचय|हमारे बारे", re.I))),
        "help_link": bool(home.link(re.compile(r"\bhelp\b|सहायता|मदद", re.I))),
        "updated_date": bool(UPDATED.search(home.body)),
        **home.contact_fields(),
    }
    checks = result["checks"]
    checks.update({key: "observed" if found else "not_observed" for key, found in signals.items()})
    result["pages"].append(home_url)
    result["outcome"] = "observed"
    if contact:
        for key in CONTACT_FIELDS:
            if not signals[key]:
                checks[key] = "unassessable"
        contact_url = _page_url(home_url, contact, domain)
        page = home
        if contact_url != home_url:
            contact_url, page = await reader.page(contact_url)
            if contact_url != home_url:
                result["pages"].append(contact_url)
        for key, found in page.contact_fields().items():
            checks[key] = "observed" if found or signals[key] else "not_observed"


def observe_site(record: dict) -> dict:
    """Observe one inventory domain. This synchronous function runs in the worker's thread pool."""
    domain = str(record.get("domain", "")).strip().lower()
    valid = len(domain) <= 253 and bool(DOMAIN.fullmatch(domain))
    result = {
        "domain": domain if valid else "",
        "outcome": "unassessable",
        "observed_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "pages": [],
        "checks": dict.fromkeys(CHECKS, "unassessable"),
        "reason": None,
    }
    if not valid:
        result["reason"] = "invalid_domain"
        return result
    try:
        base = f"https://{domain}/"
        assigned_url = record.get("url", base)
        if not isinstance(assigned_url, str) or not assigned_url.startswith("https://"):
            raise Unavailable("unsupported_page")
        start_url = _page_url(base, assigned_url, domain)
        asyncio.run(asyncio.wait_for(_observe(result, start_url), timeout=12))
    except (Unavailable, ValueError, OSError, httpx.HTTPError, LookupError, AssertionError) as exc:
        if result["outcome"] == "observed":
            result["reason"] = "contact_unavailable"
        elif isinstance(exc, Unavailable):
            result["reason"] = str(exc)
        else:
            result["reason"] = "fetch_failed"
    return result
