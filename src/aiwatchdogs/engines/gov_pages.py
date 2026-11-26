"""Read up to three citizen-facing pages and retain only bounded GIGW 3.0 & RTI observation data."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html.parser import HTMLParser
import ipaddress
import re
import socket
from typing import Any
from urllib.parse import unquote, urljoin, urlsplit

import httpx
from protego import Protego

from aiwatchdogs.core.safety import is_public_ip_address
from aiwatchdogs.engines.gigw_matrix import (
    CONTACT_FIELDS,
    LEGACY_CHECKS,
    W1_CHECKS,
    is_rti_sec24_exempt,
)

# Canonical 42 W1 automated checks (7 original keys first, then 28 GIGW 3.0 + 7 RTI/statutory).
CHECKS = W1_CHECKS
AGENT = "AIWatchdogs"
USER_AGENT = "AIWatchdogs/1.0 (aiwatchdogs.in; public website content review)"
DNS_POOL = ThreadPoolExecutor(max_workers=6, thread_name_prefix="gov-page-dns")
LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
DOMAIN = re.compile(rf"(?:{LABEL}\.)+gov\.in\Z", re.ASCII)
GOV_OR_NIC_DOMAIN = re.compile(rf"(?:{LABEL}\.)+(?:gov|nic)\.in\Z", re.ASCII)

# Navigation & content link patterns (English + major Indian official languages)
CONTACT = re.compile(
    r"contact|संपर्क|सम्पर्क|যোগাযোগ|தொடர்பு|సంప్రదించ|ಸಂಪರ್ಕ|સંપર્ક|ബന്ധപ്പെട|ਸੰਪਰਕ|ଯୋଗାଯୋଗ",
    re.I,
)
DIRECTORY = re.compile(
    r"who[’']?s[\s_-]+who|(?:telephone|phone|staff|officer|contact)[\s_-]+directory|^directory$",
    re.I,
)
ABOUT = re.compile(
    r"about|परिचय|हमारे बारे|எங்களைப் பற்றி|మా గురించి|ನಮ್ಮ ಬಗ್ಗೆ|আমাদের সম্পর্কে|અમારા વિશે",
    re.I,
)
HELP = re.compile(
    r"\bhelp\b|सहायता|मदद|உதவி|సహాయం|ಸಹಾಯ|সাহায্য|મદદ|സഹായം|ਮਦਦ|ସହାୟତା",
    re.I,
)
FEEDBACK = re.compile(
    r"\bfeedback\b|suggestions?|प्रतिक्रिया|सुझाव|கருத்து|అభిప్రాయం|ಪ್ರತಿಕ್ರಿಯೆ|মতামত|પ્રતિભાવ",
    re.I,
)
NATIONAL_PORTAL = re.compile(
    r"(?:^|\.)india\.gov\.in\b|national\s+portal\s+of\s+india|भारत\s+का\s+राष्ट्रीय\s+पोर्टल",
    re.I,
)
SOCIAL_HOSTS = frozenset(
    {
        "twitter.com",
        "x.com",
        "facebook.com",
        "fb.com",
        "youtube.com",
        "youtu.be",
        "instagram.com",
        "linkedin.com",
        "whatsapp.com",
        "t.me",
        "telegram.me",
        "kooapp.com",
    }
)
WIM = re.compile(
    r"web\s+information\s+manager|web[-_]?information[-_]?manager|वेब\s+सूचना\s+प्रबंधक",
    re.I,
)
WEBSITE_POLICIES = re.compile(
    r"website\s+polic(?:y|ies)|web\s+polic(?:y|ies)|hyperlinking\s+policy|content\s+(?:archival|review)\s+policy|वेबसाइट\s+नीति",
    re.I,
)
PRIVACY_POLICY = re.compile(
    r"privacy\s+policy|privacy[-_]?policy|गोपनीयता\s+नीति",
    re.I,
)
TERMS_COPYRIGHT = re.compile(
    r"terms\s*(?:&|and)\s*conditions|terms[-_]?(?:of[-_]?use|and[-_]?conditions)|copyright\s+policy|copyright[-_]?policy|\bdisclaimer\b|नियम\s*(?:एवं|और)\s*शर्तें|कॉपीराइट\s+नीति|अस्वीकरण",
    re.I,
)
HYPERLINK_POLICY = re.compile(r"hyperlinking\s+policy|external\s+(?:link|website)|opens?\s+in\s+a?\s*new\s+(?:window|tab)", re.I)
UNDER_CONSTRUCTION = re.compile(
    r"under\s+construction|coming\s+soon|page\s+is\s+under\s+development|site\s+is\s+under\s+construction|निर्माणाधीन",
    re.I,
)
SKIP_TO_MAIN = re.compile(
    r"skip\s+to\s+(?:main\s+)?content|skip\s+navigation|मुख्य\s+(?:सामग्री|विषय)\s+पर\s+जा",
    re.I,
)
SITEMAP = re.compile(r"\bsite\s*map\b|साइट\s*मैप", re.I)
SEARCH_LINK = re.compile(r"\bsearch\b|खोजें|खोज", re.I)
ACCESSIBILITY = re.compile(
    r"screen\s+reader(?:\s+access)?|accessibility(?:\s+(?:statement|options|help|guidelines))?|screen[-_]?reader|स्क्रीन\s+रीडर|सुगम्यता",
    re.I,
)
RTI_LINK_RE = re.compile(
    r"\brti\b|right\s+to\s+information|right[-_]?to[-_]?information|suo\s+motu|suo[-_]?motu|proactive\s+disclosure|proactive[-_]?disclosure|section\s*4\s*\(1\)\s*\(b\)|सूचना\s+का\s+अधिकार|आरटीआई",
    re.I,
)
PIO_FAA_RE = re.compile(
    r"\b(?:c\.?p\.?i\.?o\.?|s\.?p\.?i\.?o\.?|c\.?a\.?p\.?i\.?o\.?|s\.?a\.?p\.?i\.?o\.?|p\.?i\.?o\.?)\b"
    r"|(?:central|state|assistant)?\s*public\s+information\s+officer(?:s)?"
    r"|first\s+appellate\s+authority|\bfaa\b\s*(?:under\s+rti|details|contact|officer)"
    r"|(?:केंद्रीय|केन्द्रीय|राज्य)?\s*(?:लोक|जन)\s+सूचना\s+अधिकारी|प्रथम\s+अपीलीय\s+प्राधिकारी",
    re.I,
)
CITIZEN_CHARTER = re.compile(
    r"citizen[’']?s?\s+charter|client[’']?s?\s+charter|citizen[-_]?s?[-_]?charter|\bsevottam\b|goicharters\.nic\.in|नागरिक\s+चार्टर|सिटिजन\s+चार्टर|नागरिक\s+अधिकार\s+पत्र|सेवोत्तम",
    re.I,
)
GRIEVANCE = re.compile(
    r"pgportal\.gov\.in|\bcpgrams\b|public\s+grievances?|grievance\s+redress(?:al)?|public[-_]?grievance|lodge\s+(?:a\s+)?grievance|लोक\s+शिकायत|जन\s+शिकायत|शिकायत\s+निवारण|जनसुनवाई",
    re.I,
)
ACTS_TENDERS = re.compile(
    r"\bacts?\s*(?:&|and)\s*rules?\b|acts?[-_]?(?:and[-_]?)?rules?|\btenders?\b|\bprocurement\b"
    r"|\bannual\s+reports?\b|\bbudgets?\b|\bschemes?\b|\bcirculars?\b|\bnotifications?\b"
    r"|\bmanuals?\b|\bopen\s+data\b|data\.gov\.in|अधिनियम|निविदा|योजनाएं|परिपत्र|अधिसूचना|वार्षिक\s+रिपोर्ट",
    re.I,
)

# Content & document indicators
EMAIL = re.compile(
    r"[\w.+-]{1,64}\s*(?:@|\[at\]|\(at\))\s*([\w-]+(?:\.[\w-]+|\s*(?:\[dot\]|\(dot\))\s*[\w-]+)+)",
    re.I,
)
GOV_EMAIL_DOMAIN_RE = re.compile(
    r"(?:^|\.|\[dot\]|\(dot\))\s*(?:gov|nic)\s*(?:\.|\[dot\]|\(dot\))\s*in\Z",
    re.I,
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
EMBLEM_OR_LOGO = re.compile(
    r"emblem|ashoka|satyameva|national[-_\s]?logo|state[-_\s]?logo|gov[-_\s]?logo|govt[-_\s]?logo|ministry[-_\s]?logo|department[-_\s]?logo|\blogo\b|प्रतीक|अशोक\s+स्तंभ|सत्यमेव\s+जयते",
    re.I,
)
OWNERSHIP = re.compile(
    r"content\s+owned|website\s+(?:belongs|designed|developed|maintained)|developed\s+and\s+hosted\s+by"
    r"|national\s+informatics\s+centre|\bnic\b|government\s+of\s+india|govt\.?\s+of\s+"
    r"|government\s+of\s+[a-z]{3,25}|ministry\s+of\s+[a-z]{3,25}|department\s+of\s+[a-z]{3,25}"
    r"|all\s+rights\s+reserved|भारत\s+सरकार|राष्ट्रीय\s+सूचना\s+विज्ञान\s+केंद्र|राज्य\s+सरकार",
    re.I,
)
DOWNLOAD_EXT = re.compile(r"\.(?:pdf|docx?|xlsx?|pptx?|zip|rar|odt|ods|csv)\Z", re.I)
DOWNLOAD_META = re.compile(
    r"(?:\b\d+(?:\.\d+)?\s*(?:kb|mb|gb|bytes?)\b|\b(?:pdf|doc|docx|xls|xlsx|ppt|pptx|zip|csv|odt|ods)\b)",
    re.I,
)
LANGUAGE_SWITCH = re.compile(
    r"\b(?:hindi|english|marathi|tamil|telugu|kannada|bengali|gujarati|malayalam|punjabi|odia|assamese|urdu|language)\b"
    r"|हिन्दी|हिंदी|मराठी|தமிழ்|తెలుగు|ಕನ್ನಡ|বাংলা|ગુજરાતી|മലയാളം|ਪੰਜਾਬੀ|ଓଡ଼ିଆ|অসমীয়া|اردو",
    re.I,
)
INDIC_SCRIPT_RE = re.compile(r"[\u0900-\u0D7F]")
BCP47_RE = re.compile(r"^[a-z]{2,3}(?:-[a-z0-9]{2,8})*\Z", re.I | re.ASCII)
GENERIC_TITLES = frozenset({"home", "homepage", "index", "untitled", "document", "welcome", "default", "page"})
SERVER_VERSION_LEAK = re.compile(
    r"(?:apache|nginx|iis|microsoft-iis|tomcat|jetty|openresty|lighttpd|caddy|php|asp\.net|express|gunicorn|uvicorn|werkzeug|openssl|mod_ssl|python|java|node|ruby|perl|jboss|wildfly|weblogic|websphere)[/\s-]+\d+(?:\.\d+)*",
    re.I,
)
HSTS_MAX_AGE = re.compile(r"max-age\s*=\s*(\d+)", re.I)


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
    r"(?:staff|telephone|phone|officer)[-_]directory|who[-_]?s[-_]?who|about(?:[-_]?us)?|help|"
    r"rti(?:[-_]?(?:act|cell|info|details|portal|disclosures?))?|right[-_]?to[-_]?information|"
    r"suo[-_]?motu(?:[-_]?disclosures?)?|proactive[-_]?disclosures?|section[-_]?4|"
    r"pio|cpio|spio|faa|citizen[-_]?s?[-_]?charter|charter|sevottam|"
    r"grievances?|public[-_]?grievances?|cpgrams|web[-_]?information[-_]?manager|wim|"
    r"privacy(?:[-_]?policy)?|website[-_]?polic(?:y|ies)|policies|terms(?:[-_]?and[-_]?conditions)?|"
    r"copyright[-_]?policy|disclaimer|accessibility(?:[-_]?statement)?|screen[-_]?reader(?:[-_]?access)?|"
    r"site[-_]?map|feedback|acts?[-_]?(?:and[-_]?)?rules?|rules|manuals|circulars|notifications|"
    r"tenders|procurement|budget|schemes|annual[-_]?reports?|open[-_]?data|transparency[-_]?audit)"
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
    LANDMARK_TAGS = frozenset({"main", "nav", "header", "footer"})
    LANDMARK_ROLES = frozenset({"main", "navigation", "banner", "contentinfo"})
    HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
    IGNORED_INPUT_TYPES = frozenset({"hidden", "submit", "button", "reset", "image"})

    def __init__(self, html: str):
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self.link_attrs: list[dict[str, str]] = []
        self.text: list[str] = []
        self._hidden: list[str] = []
        self.ids: set[str] = set()
        self.duplicate_ids: bool = False
        self.base = ""
        self.has_password = False
        self.title: list[str] = []
        self._in_title = False
        self._href = ""
        self._label: list[str] = []
        self._current_a_attrs: dict[str, str] = {}

        # Rich GIGW 3.0 & WCAG 2.1 HTML baseline observations (zero raw PII retained)
        self.html_lang: str = ""
        self.has_utf8_meta: bool = False
        self.has_viewport_meta: bool = False
        self.has_stylesheet: bool = False
        self.has_meta_desc_or_keywords: bool = False
        self.has_emblem_or_logo_img: bool = False
        self.img_count: int = 0
        self.imgs_with_alt: int = 0
        self.has_landmark: bool = False
        self.has_heading: bool = False
        self.has_search_control: bool = False
        self.has_feedback_form: bool = False
        self.table_count: int = 0
        self.has_semantic_table_markup: bool = False
        self.label_for_ids: set[str] = set()
        self._label_depth: int = 0
        self.form_controls: list[dict[str, Any]] = []

        self.feed(html)
        self.close()
        self.body = " ".join(" ".join(self.text).split())
        self.title_text = " ".join(" ".join(self.title).split()).strip()
        if (
            re.search(r"^(?:404\b|page not found\b|access denied\b|service unavailable\b)", self.title_text, re.I)
            or self.has_password
            and re.search(r"\b(?:log[ -]?in|sign[ -]?in)\b", self.title_text, re.I)
        ):
            raise Unavailable("unsupported_page")
        if len(self.body) < 40 or re.search(
            r"verify (?:that )?you are human|checking your browser|enable javascript to (?:continue|run this app)|access denied|just a moment\.\.\.",
            self.body[:1200],
            re.I,
        ):
            raise Unavailable("unsupported_page")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {k.lower(): (v or "") for k, v in attrs}
        # Capture root <html> / <meta> / <link> / <style> metadata regardless of body visibility.
        if tag == "html" and not self.html_lang:
            self.html_lang = (attributes.get("lang") or attributes.get("xml:lang") or "").strip()
        elif tag == "meta":
            charset = attributes.get("charset", "").strip().lower()
            http_equiv = attributes.get("http-equiv", "").strip().lower()
            name = attributes.get("name", "").strip().lower()
            content = attributes.get("content", "").strip()
            if charset == "utf-8" or (http_equiv == "content-type" and "utf-8" in content.lower()):
                self.has_utf8_meta = True
            if name == "viewport" and content:
                self.has_viewport_meta = True
            if name in {"description", "keywords"} and len(content) >= 3:
                self.has_meta_desc_or_keywords = True
        elif tag == "link":
            rel = attributes.get("rel", "").lower()
            if "stylesheet" in rel.split():
                self.has_stylesheet = True
        elif tag == "style":
            self.has_stylesheet = True

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

        if identifier := attributes.get("id", "").strip():
            if identifier in self.ids:
                self.duplicate_ids = True
            self.ids.add(identifier)

        if tag == "base" and not self.base:
            self.base = attributes.get("href") or ""
        if tag == "title":
            self._in_title = True

        # Landmarks & headings
        role = attributes.get("role", "").strip().lower()
        if tag in self.LANDMARK_TAGS or role in self.LANDMARK_ROLES:
            self.has_landmark = True
        if tag in self.HEADING_TAGS:
            self.has_heading = True
        if role == "search":
            self.has_search_control = True

        # Forms & search / feedback indicators (strictly read-only)
        if tag == "form":
            form_blob = " ".join(
                (attributes.get("id", ""), attributes.get("name", ""), attributes.get("action", ""), attributes.get("aria-label", ""))
            )
            if SEARCH_LINK.search(form_blob):
                self.has_search_control = True
            if FEEDBACK.search(form_blob):
                self.has_feedback_form = True
        elif tag == "label":
            self._label_depth += 1
            if for_id := attributes.get("for", "").strip():
                self.label_for_ids.add(for_id)
        elif tag == "input":
            itype = (attributes.get("type") or "text").strip().lower()
            if itype == "password":
                self.has_password = True
            if itype == "search" or SEARCH_LINK.search(
                " ".join((attributes.get("name", ""), attributes.get("id", ""), attributes.get("placeholder", ""), attributes.get("aria-label", "")))
            ):
                self.has_search_control = True
            if itype not in self.IGNORED_INPUT_TYPES:
                self.form_controls.append(
                    {
                        "id": attributes.get("id", "").strip(),
                        "has_inline_label": bool(
                            attributes.get("aria-label", "").strip()
                            or attributes.get("aria-labelledby", "").strip()
                            or attributes.get("title", "").strip()
                            or self._label_depth > 0
                        ),
                    }
                )
        elif tag in {"select", "textarea"}:
            self.form_controls.append(
                {
                    "id": attributes.get("id", "").strip(),
                    "has_inline_label": bool(
                        attributes.get("aria-label", "").strip()
                        or attributes.get("aria-labelledby", "").strip()
                        or attributes.get("title", "").strip()
                        or self._label_depth > 0
                    ),
                }
            )

        # Images & emblem/logo markup
        if tag == "img":
            self.img_count += 1
            if "alt" in attributes:
                self.imgs_with_alt += 1
            img_blob = " ".join(
                (
                    attributes.get("alt", ""),
                    attributes.get("title", ""),
                    attributes.get("aria-label", ""),
                    attributes.get("src", ""),
                    attributes.get("class", ""),
                    attributes.get("id", ""),
                )
            )
            if EMBLEM_OR_LOGO.search(img_blob):
                self.has_emblem_or_logo_img = True
            if self._href:
                self._label.append(attributes.get("alt") or "")
        elif tag == "svg":
            svg_blob = " ".join(
                (attributes.get("aria-label", ""), attributes.get("title", ""), attributes.get("class", ""), attributes.get("id", ""))
            )
            if EMBLEM_OR_LOGO.search(svg_blob):
                self.has_emblem_or_logo_img = True

        # Tables
        if tag == "table":
            self.table_count += 1
        elif tag in {"th", "thead", "caption"} or (tag in {"td", "th"} and attributes.get("scope")):
            self.has_semantic_table_markup = True

        # Links
        if tag == "a":
            self._href = attributes.get("href") or ""
            self._label = [attributes.get("aria-label") or "", attributes.get("title") or ""]
            self._current_a_attrs = {
                "target": attributes.get("target", ""),
                "rel": attributes.get("rel", ""),
                "class": attributes.get("class", ""),
                "title": attributes.get("title", ""),
                "aria-label": attributes.get("aria-label", ""),
            }

    def handle_data(self, text: str) -> None:
        if not self._hidden:
            self.text.append(text)
            if self._in_title:
                self.title.append(text)
            if self._href:
                self._label.append(text)

    def handle_endtag(self, tag: str) -> None:
        if self._hidden:
            if self._hidden[-1] == tag:
                self._hidden.pop()
            return
        if tag == "title":
            self._in_title = False
        if tag == "label" and self._label_depth > 0:
            self._label_depth -= 1
        if tag == "a" and self._href:
            self.links.append((self._href, " ".join(self._label)))
            self.link_attrs.append(self._current_a_attrs)
            self._href = ""
            self._current_a_attrs = {}

    def matching_links(self, pattern: re.Pattern[str]):
        for href, label in self.links:
            try:
                parts = urlsplit(href)
            except ValueError:
                continue
            if parts.scheme not in {"", "http", "https"}:
                continue
            if href.startswith("#") and unquote(parts.fragment) not in self.ids:
                continue
            target_text = f"{parts.netloc} {unquote(parts.path).strip('/')}"
            if pattern.search(label.strip()) or pattern.search(target_text):
                yield href

    def _first_local_link(self, home: str, domain: str, patterns: tuple[re.Pattern[str], ...]) -> tuple[bool, str | None]:
        linked = False
        for pattern in patterns:
            for href in self.matching_links(pattern):
                linked = True
                try:
                    return True, _page_url(urljoin(home, self.base), href, domain)
                except (Unavailable, ValueError):
                    continue
        return linked, None

    def contact_link(self, home: str, domain: str) -> tuple[bool, str | None]:
        # Prefer a reachable candidate on the assigned site when the navigation also
        # points to a shared government directory or an external service desk.
        return self._first_local_link(home, domain, (CONTACT, DIRECTORY))

    def rti_page_link(self, home: str, domain: str) -> tuple[bool, str | None]:
        return self._first_local_link(home, domain, (RTI_LINK_RE,))

    def email_classification(self) -> tuple[bool, bool]:
        """Return ``(has_any_email, has_gov_or_nic_email)`` without retaining any address text."""
        has_any = False
        has_gov = False
        for match in EMAIL.finditer(self.body):
            has_any = True
            if GOV_EMAIL_DOMAIN_RE.search(match.group(1)):
                has_gov = True
                break
        if not has_gov:
            for href, _ in self.links:
                if href.lower().startswith("mailto:"):
                    m = EMAIL.match(href[7:])
                    if m:
                        has_any = True
                        if GOV_EMAIL_DOMAIN_RE.search(m.group(1)):
                            has_gov = True
                            break
        return has_any, has_gov

    def contact_fields(self) -> dict[str, bool]:
        has_email, _ = self.email_classification()
        return {
            "email": has_email,
            "postal_address": bool(ADDRESS.search(self.body)),
            "telephone": bool(
                any(len(re.findall(r"\d", match[1])) >= 7 for match in PHONE.finditer(self.body))
                or any(
                    re.fullmatch(r"tel:\+?[\d() -]{7,22}", h, re.I) and len(re.findall(r"\d", h)) >= 7
                    for h, _ in self.links
                )
            ),
        }

    def download_metadata_state(self) -> str:
        found_download = False
        for href, label in self.links:
            try:
                parts = urlsplit(href)
            except ValueError:
                continue
            if DOWNLOAD_EXT.search(parts.path):
                found_download = True
                if DOWNLOAD_META.search(label):
                    return "observed"
        return "not_observed" if found_download else "not_applicable"

    def external_link_notice_state(self, domain: str) -> str:
        bare = domain.removeprefix("www.")
        has_policy = bool(next(self.matching_links(HYPERLINK_POLICY), None))
        has_external = False
        external_indicated = False
        for (href, label), attrs in zip(self.links, self.link_attrs, strict=False):
            try:
                parts = urlsplit(href)
            except ValueError:
                continue
            if parts.scheme not in {"http", "https"} or not parts.hostname:
                continue
            host = parts.hostname.lower().removeprefix("www.")
            if host == bare:
                continue
            has_external = True
            if (
                attrs.get("target", "").lower() == "_blank"
                or "noopener" in attrs.get("rel", "").lower()
                or HYPERLINK_POLICY.search(f"{label} {attrs.get('class', '')}")
            ):
                external_indicated = True
                break
        if has_policy or external_indicated:
            return "observed"
        if not has_external:
            return "not_applicable"
        return "not_observed"

    def has_social_links(self) -> bool:
        for href, _ in self.links:
            try:
                host = (urlsplit(href).hostname or "").lower().removeprefix("www.")
            except ValueError:
                continue
            if host in SOCIAL_HOSTS or any(host.endswith("." + sh) for sh in SOCIAL_HOSTS):
                return True
        return False

    def has_skip_to_main(self) -> bool:
        for href, label in self.links:
            if not href.startswith("#") or len(href) < 2:
                continue
            frag = unquote(href[1:])
            if frag in self.ids and SKIP_TO_MAIN.search(label):
                return True
        return False

    def form_labels_state(self) -> str:
        if not self.form_controls:
            return "not_applicable"
        for ctrl in self.form_controls:
            if ctrl["has_inline_label"]:
                continue
            if ctrl["id"] and ctrl["id"] in self.label_for_ids:
                continue
            return "not_observed"
        return "observed"

    def has_bilingual_unicode(self, content_type: str) -> bool:
        utf8 = self.has_utf8_meta or ("utf-8" in content_type.lower())
        if not utf8:
            return False
        if INDIC_SCRIPT_RE.search(self.body):
            return True
        for href, label in self.links:
            if LANGUAGE_SWITCH.search(label) or re.search(r"/(?:hi|hindi|ta|te|mr|gu|ml|kn|bn|pa|or|as|ur)(?:/|\Z)", href, re.I):
                return True
        return False


def _header_security_checks(headers: dict[str, str], home_url: str) -> dict[str, str]:
    h = {str(k).lower(): str(v) for k, v in headers.items()}
    https_ok = home_url.startswith("https://")
    hsts_val = h.get("strict-transport-security", "")
    hsts_m = HSTS_MAX_AGE.search(hsts_val)
    hsts_ok = bool(hsts_m and int(hsts_m.group(1)) > 0)

    nosniff = h.get("x-content-type-options", "").strip().lower() == "nosniff"
    xfo = h.get("x-frame-options", "").strip().lower()
    csp = h.get("content-security-policy", "").lower()
    clickjack_ok = xfo in {"deny", "sameorigin"} or ("frame-ancestors" in csp)

    server_hdr = h.get("server", "")
    powered_hdr = h.get("x-powered-by", "")
    banner_leaked = bool(SERVER_VERSION_LEAK.search(server_hdr) or SERVER_VERSION_LEAK.search(powered_hdr))

    return {
        "https_valid_tls": "observed" if https_ok else "not_observed",
        "hsts_header": "observed" if hsts_ok else "not_observed",
        "security_headers": "observed" if (nosniff and clickjack_ok) else "not_observed",
        "server_banner_obscured": "not_observed" if banner_leaked else "observed",
    }


class Reader:
    def __init__(self, domain: str):
        self.domain = domain
        self.addresses: dict[str, str] = {}
        self.rules: dict[str, Protego] = {}
        self.last_request: dict[str, float] = {}
        self.page_headers: dict[str, dict[str, str]] = {}

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
                self.page_headers[url] = {str(k).lower(): str(v) for k, v in headers.items()}
                return url, Page(body)
            if status not in {301, 302, 303, 307, 308} or not headers.get("location"):
                raise Unavailable("unsupported_page" if status == 200 else "fetch_failed")
            url = _page_url(url, headers["location"], self.domain)
        raise Unavailable("fetch_failed")


async def _observe(result: dict, start_url: str) -> None:
    domain = result["domain"]
    rti_exempt = is_rti_sec24_exempt(domain)
    reader = Reader(domain)
    home_url, home = await reader.page(start_url)
    home_headers = reader.page_headers.get(home_url, {})

    contact_found, contact_url = home.contact_link(home_url, domain)
    rti_found, rti_url = home.rti_page_link(home_url, domain)

    home_contacts = home.contact_fields()
    home_any_email, home_gov_email = home.email_classification()
    home_wim = bool(WIM.search(home.body) or next(home.matching_links(WIM), None))
    home_pio_faa = bool(PIO_FAA_RE.search(home.body) or next(home.matching_links(PIO_FAA_RE), None))
    home_updated = bool(UPDATED.search(home.body))
    home_under_construction = bool(UNDER_CONSTRUCTION.search(home.body))
    home_privacy = bool(PRIVACY_POLICY.search(home.body) or next(home.matching_links(PRIVACY_POLICY), None))
    home_charter = bool(CITIZEN_CHARTER.search(home.body) or next(home.matching_links(CITIZEN_CHARTER), None))
    home_grievance = bool(GRIEVANCE.search(home.body) or next(home.matching_links(GRIEVANCE), None))
    home_acts = bool(next(home.matching_links(ACTS_TENDERS), None))

    # Build initial checks map from homepage + HTTP headers.
    sec_checks = _header_security_checks(home_headers, home_url)
    title_descriptive = (
        len(home.title_text) >= 4
        and home.title_text.lower() not in GENERIC_TITLES
        and home.has_meta_desc_or_keywords
    )
    img_state = (
        "not_applicable"
        if home.img_count == 0
        else ("observed" if home.imgs_with_alt == home.img_count else "not_observed")
    )
    table_count = home.table_count
    table_semantic = home.has_semantic_table_markup

    checks = result["checks"]
    checks.update(
        {
            # 7 original checks
            "contact_link": "observed" if contact_found else "not_observed",
            "email": "observed" if home_contacts["email"] else "not_observed",
            "postal_address": "observed" if home_contacts["postal_address"] else "not_observed",
            "telephone": "observed" if home_contacts["telephone"] else "not_observed",
            "about_link": "observed" if next(home.matching_links(ABOUT), None) else "not_observed",
            "updated_date": "observed" if home_updated else "not_observed",
            "help_link": "observed" if next(home.matching_links(HELP), None) else "not_observed",
            # 28 GIGW 3.0 automated checks
            "emblem_or_logo": "observed" if home.has_emblem_or_logo_img else "not_observed",
            "ownership_statement": "observed" if OWNERSHIP.search(home.body) else "not_observed",
            "download_metadata": home.download_metadata_state(),
            "feedback_link": "observed" if (home.has_feedback_form or next(home.matching_links(FEEDBACK), None)) else "not_observed",
            "national_portal_link": "observed" if next(home.matching_links(NATIONAL_PORTAL), None) else "not_observed",
            "responsive_viewport": "observed" if (home.has_viewport_meta and home.has_stylesheet) else "not_observed",
            "page_title_meta": "observed" if title_descriptive else "not_observed",
            "data_table_headers": (
                "not_applicable" if table_count == 0 else ("observed" if table_semantic else "not_observed")
            ),
            "gov_domain_canonical": (
                "observed" if GOV_OR_NIC_DOMAIN.fullmatch((urlsplit(home_url).hostname or "").lower()) else "not_observed"
            ),
            "social_media_links": "observed" if home.has_social_links() else "not_observed",
            "wim_disclosure": "observed" if home_wim else "not_observed",
            "website_policies_link": "observed" if next(home.matching_links(WEBSITE_POLICIES), None) else "not_observed",
            "privacy_policy_link": "observed" if home_privacy else "not_observed",
            "terms_copyright_link": "observed" if next(home.matching_links(TERMS_COPYRIGHT), None) else "not_observed",
            "external_link_notice": home.external_link_notice_state(domain),
            "no_under_construction": "not_observed" if home_under_construction else "observed",
            "bilingual_unicode": (
                "observed" if home.has_bilingual_unicode(home_headers.get("content-type", "")) else "not_observed"
            ),
            "img_alt_text": img_state,
            "semantic_landmarks": "observed" if (home.has_landmark and home.has_heading) else "not_observed",
            "skip_to_main": "observed" if home.has_skip_to_main() else "not_observed",
            "search_or_sitemap": (
                "observed"
                if (home.has_search_control or next(home.matching_links(SITEMAP), None) or next(home.matching_links(SEARCH_LINK), None))
                else "not_observed"
            ),
            "html_lang_attr": "observed" if BCP47_RE.fullmatch(home.html_lang) else "not_observed",
            "form_input_labels": home.form_labels_state(),
            "unique_dom_ids": "not_observed" if home.duplicate_ids else "observed",
            **sec_checks,
            # 7 RTI Act 2005 & GoI statutory checks
            "rti_link": "observed" if rti_found else ("not_applicable" if rti_exempt else "not_observed"),
            "pio_faa_disclosure": "observed" if home_pio_faa else ("not_applicable" if rti_exempt else "not_observed"),
            "citizen_charter_link": "observed" if home_charter else "not_observed",
            "grievance_link": "observed" if home_grievance else "not_observed",
            "acts_tenders_link": "observed" if home_acts else "not_observed",
            "gov_email_domain": (
                "observed" if home_gov_email else ("not_observed" if home_any_email else "not_applicable")
            ),
            "accessibility_link": "observed" if next(home.matching_links(ACCESSIBILITY), None) else "not_observed",
        }
    )

    result["pages"].append(public_evidence_url(home_url, domain))
    result["outcome"] = "observed"

    any_email = home_any_email
    gov_email = home_gov_email
    contact_failed = False
    rti_failed = False
    sampled_subpages: list[Page] = []

    def _merge_subpage(sub: Page) -> None:
        nonlocal any_email, gov_email, table_count, table_semantic
        sampled_subpages.append(sub)
        sub_contacts = sub.contact_fields()
        for key in ("email", "postal_address", "telephone"):
            if sub_contacts[key]:
                checks[key] = "observed"
        s_any, s_gov = sub.email_classification()
        any_email = any_email or s_any
        gov_email = gov_email or s_gov
        if WIM.search(sub.body) or next(sub.matching_links(WIM), None):
            checks["wim_disclosure"] = "observed"
        if PIO_FAA_RE.search(sub.body) or next(sub.matching_links(PIO_FAA_RE), None):
            checks["pio_faa_disclosure"] = "observed"
        if UPDATED.search(sub.body):
            checks["updated_date"] = "observed"
        if PRIVACY_POLICY.search(sub.body) or next(sub.matching_links(PRIVACY_POLICY), None):
            checks["privacy_policy_link"] = "observed"
        if CITIZEN_CHARTER.search(sub.body) or next(sub.matching_links(CITIZEN_CHARTER), None):
            checks["citizen_charter_link"] = "observed"
        if GRIEVANCE.search(sub.body) or next(sub.matching_links(GRIEVANCE), None):
            checks["grievance_link"] = "observed"
        if next(sub.matching_links(ACTS_TENDERS), None):
            checks["acts_tenders_link"] = "observed"
        if UNDER_CONSTRUCTION.search(sub.body):
            checks["no_under_construction"] = "not_observed"
        table_count += sub.table_count
        table_semantic = table_semantic or sub.has_semantic_table_markup
        if table_count > 0:
            checks["data_table_headers"] = "observed" if table_semantic else "not_observed"

    # Page 2: optional linked Contact / Directory page
    if contact_found:
        if contact_url is None:
            contact_failed = True
        elif contact_url != home_url:
            try:
                resolved_contact, contact_page = await reader.page(contact_url)
                ev_url = public_evidence_url(resolved_contact, domain)
                if ev_url not in result["pages"]:
                    result["pages"].append(ev_url)
                _merge_subpage(contact_page)
            except (Unavailable, ValueError, OSError, httpx.HTTPError, LookupError, AssertionError):
                contact_failed = True

    # Page 3: optional linked RTI / Proactive Disclosure page (if distinct from home & contact)
    if rti_found:
        if rti_url is None:
            rti_failed = True
        elif rti_url not in {home_url, contact_url}:
            try:
                resolved_rti, rti_page = await reader.page(rti_url)
                ev_url = public_evidence_url(resolved_rti, domain)
                if ev_url not in result["pages"]:
                    result["pages"].append(ev_url)
                _merge_subpage(rti_page)
            except (Unavailable, ValueError, OSError, httpx.HTTPError, LookupError, AssertionError):
                rti_failed = True

    # Reconcile contact & RTI check states honestly when a linked subpage was unavailable.
    if gov_email:
        checks["gov_email_domain"] = "observed"
    elif any_email:
        checks["gov_email_domain"] = "not_observed"
    else:
        checks["gov_email_domain"] = "not_applicable"

    if contact_failed:
        for key in CONTACT_FIELDS:
            if checks[key] != "observed":
                checks[key] = "unassessable"
        if checks["wim_disclosure"] != "observed":
            checks["wim_disclosure"] = "unassessable"
        if rti_failed and not rti_exempt and checks["pio_faa_disclosure"] != "observed":
            checks["pio_faa_disclosure"] = "unassessable"
        result["reason"] = "contact_unavailable"
    elif rti_failed:
        if not rti_exempt and checks["pio_faa_disclosure"] != "observed":
            checks["pio_faa_disclosure"] = "unassessable"
        result["reason"] = "rti_unavailable"


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
        asyncio.run(asyncio.wait_for(_observe(result, start_url), timeout=15))
    except (Unavailable, ValueError, OSError, httpx.HTTPError, LookupError, AssertionError) as exc:
        if result["outcome"] == "observed":
            result["reason"] = result["reason"] or "contact_unavailable"
        elif isinstance(exc, Unavailable):
            result["reason"] = str(exc)
        else:
            result["reason"] = "fetch_failed"
    return result


__all__ = [
    "AGENT",
    "CHECKS",
    "CONTACT_FIELDS",
    "DOMAIN",
    "LEGACY_CHECKS",
    "Page",
    "Reader",
    "USER_AGENT",
    "Unavailable",
    "observe_site",
    "public_evidence_url",
]
