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
CONTACT = re.compile(r"contact|संपर्क|सम्पर्क|যোগাযোগ|தொடர்பு|సంప్రదించ|ಸಂಪರ್ಕ|સંપર્ક|ബന്ധപ്പെട|ਸੰਪਰਕ|ଯୋଗାଯୋଗ", re.I)
DIRECTORY = re.compile(
    r"who[’']?s[\s_-]+who|(?:telephone|phone|staff|officer|contact)[\s_-]+directory|^directory$", re.I
)
ABOUT = re.compile(r"about|परिचय|हमारे बारे|எங்களைப் பற்றி|మా గురించి|ನಮ್ಮ ಬಗ್ಗೆ|আমাদের সম্পর্কে|અમારા વિશે", re.I)
HELP = re.compile(r"\bhelp\b|सहायता|मदद|உதவி|సహాయం|ಸಹಾಯ|সাহায্য|મદદ|സഹായം|ਮਦਦ|ସହାୟତା", re.I)

EMAIL = re.compile(
    r"[\w.+-]{1,64}\s*(?:@|\[at\]|\(at\))\s*[\w-]+(?:\s*(?:\.|\[dot\]|\(dot\))\s*[\w-]+)+", re.I
)
PHONE = re.compile(r"(?:phone|tel(?:ephone)?|दूरभाष)\s*(?:no\.?)?\s*[:.-]?\s*(\+?[\d(][\d() -]{5,20}\d)", re.I)
ADDRESS = re.compile(
    r"(?:\b(?:postal|office|mailing|correspondence)\s+address\b|(?<!email )(?<!e-mail )\baddress\s*:|पता|पत्ता|முகவரி|చిరునామా|ವಿಳಾಸ|ঠিকানা|સરનામું)"
    r".{0,200}?\b[1-9]\d{2}\s?\d{3}\b",
    re.I,
)
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


_PUBLIC_NAVIGATION = re.compile(
    r"(?:en(?:-in|-us)?|eng|english|hi|hindi|ta|te|mr|gu|ml|kn|bn|pa|or|as|ur|"
    r"home(?:page)?|index|default|welcome|main|portal|page|pages|content|site|web|"
    r"contact(?:[-_]?(?:us|info|details|information))?|contacts|directory|"
    r"(?:staff|telephone|phone|officer)[-_]directory|who[-_]?s[-_]?who|about[-_]?us|help)"
    r"(?:\.(?:html?|aspx?|php|jsp))?",
    re.I | re.ASCII,
)


def public_evidence_url(url: str, domain: str) -> str:
    """Publish generic navigation paths. Keep the site origin for arbitrary CMS paths."""
    checked = _page_url(f"https://{domain}/", url, domain)
    parts = urlsplit(checked)
    if all(_PUBLIC_NAVIGATION.fullmatch(segment) for segment in parts.path.split("/") if segment):
        return checked
    return parts._replace(path="/").geturl()


class Page(HTMLParser):
    VOID_TAGS = frozenset("area base br col embed hr img input link meta param source track wbr".split())

    def __init__(self, html: str):
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self.text: list[str] = []
        self._hidden: list[str] = []
        self.ids: set[str] = set()
        self.base = ""
        self.has_password = False
        self.title: list[str] = []
        self._in_title = False
        self._href = ""
        self._label: list[str] = []
        self.feed(html)
        self.close()
        self.body = " ".join(" ".join(self.text).split())
        title = " ".join(self.title).strip()
        if (
            re.search(r"^(?:404\b|page not found\b|access denied\b|service unavailable\b)", title, re.I)
            or self.has_password
            and re.search(r"\b(?:log[ -]?in|sign[ -]?in)\b", title, re.I)
        ):
            raise Unavailable("unsupported_page")
        if len(self.body) < 40 or re.search(
            r"verify (?:that )?you are human|checking your browser|enable javascript to (?:continue|run this app)|access denied|just a moment\.\.\.",
            self.body[:1200],
            re.I,
        ):
            raise Unavailable("unsupported_page")

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if (
            self._hidden
            or tag in {"script", "style", "template", "noscript"}
            or "hidden" in attributes
            or re.search(
                r"(?:display\s*:\s*none|visibility\s*:\s*hidden)\b", attributes.get("style") or "", re.I
            )
        ):
            if tag not in self.VOID_TAGS:
                self._hidden.append(tag)
            return
        if identifier := attributes.get("id"):
            self.ids.add(identifier)
        if tag == "base" and not self.base:
            self.base = attributes.get("href") or ""
        if tag == "title":
            self._in_title = True
        if tag == "input" and (attributes.get("type") or "").lower() == "password":
            self.has_password = True
        if tag == "img" and self._href:
            self._label.append(attributes.get("alt") or "")
        if tag == "a":
            self._href = attributes.get("href") or ""
            self._label = [attributes.get("aria-label") or "", attributes.get("title") or ""]

    def handle_data(self, text):
        if not self._hidden:
            self.text.append(text)
            if self._in_title:
                self.title.append(text)
            if self._href:
                self._label.append(text)

    def handle_endtag(self, tag):
        if self._hidden:
            if self._hidden[-1] == tag:
                self._hidden.pop()
            return
        if tag == "title":
            self._in_title = False
        if tag == "a" and self._href:
            self.links.append((self._href, " ".join(self._label)))
            self._href = ""

    def matching_links(self, pattern: re.Pattern):
        for href, label in self.links:
            try:
                parts = urlsplit(href)
            except ValueError:
                continue
            if parts.scheme not in {"", "http", "https"}:
                continue
            if href.startswith("#") and unquote(parts.fragment) not in self.ids:
                continue
            if pattern.search(label.strip()) or pattern.search(unquote(parts.path).strip("/")):
                yield href

    def contact_link(self, home: str, domain: str) -> tuple[bool, str | None]:
        # Prefer a reachable candidate on the assigned site when the navigation also
        # points to a shared government directory or an external service desk.
        linked = False
        for pattern in (CONTACT, DIRECTORY):
            for href in self.matching_links(pattern):
                linked = True
                try:
                    return True, _page_url(urljoin(home, self.base), href, domain)
                except (Unavailable, ValueError):
                    continue
        return linked, None

    def contact_fields(self) -> dict[str, bool]:
        return {
            "email": bool(
                EMAIL.search(self.body)
                or any(h.lower().startswith("mailto:") and EMAIL.match(h[7:]) for h, _ in self.links)
            ),
            "postal_address": bool(ADDRESS.search(self.body)),
            "telephone": bool(
                any(len(re.findall(r"\d", match[1])) >= 7 for match in PHONE.finditer(self.body))
                or any(
                    re.fullmatch(r"tel:\+?[\d() -]{7,22}", h, re.I) and len(re.findall(r"\d", h)) >= 7
                    for h, _ in self.links
                )
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
    contact, contact_url = home.contact_link(home_url, domain)
    signals = {
        "contact_link": bool(contact),
        "about_link": bool(next(home.matching_links(ABOUT), None)),
        "help_link": bool(next(home.matching_links(HELP), None)),
        "updated_date": bool(UPDATED.search(home.body)),
        **home.contact_fields(),
    }
    checks = result["checks"]
    checks.update({key: "observed" if found else "not_observed" for key, found in signals.items()})
    result["pages"].append(public_evidence_url(home_url, domain))
    result["outcome"] = "observed"
    if contact:
        for key in CONTACT_FIELDS:
            if not signals[key]:
                checks[key] = "unassessable"
        if contact_url is None:
            raise Unavailable("contact_unavailable")
        page = home
        if contact_url != home_url:
            contact_url, page = await reader.page(contact_url)
            evidence_url = public_evidence_url(contact_url, domain)
            if evidence_url not in result["pages"]:
                result["pages"].append(evidence_url)
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
