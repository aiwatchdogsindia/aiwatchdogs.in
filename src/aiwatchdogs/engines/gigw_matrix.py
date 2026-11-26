"""Canonical Indian government website conformity matrix (GIGW 3.0 + RTI Act 2005 + GoI statutory norms).

Provides pure-Python W1 check metadata (usable inside the contributor CLI wheel without requiring
the repository's ``projects/`` directory) and a cached read-only loader for the complete 159-item
``projects/gov-in-public-information/conformity-matrix.json`` asset used by the hub API.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

MATRIX_VERSION = "gigw-3.0-rti-2005-v1"
TOTAL_DIRECTORY_HOSTS = 12_537

# Original 7 W1 checks preserved first in exact order for backward compatibility.
LEGACY_CHECKS: tuple[str, ...] = (
    "contact_link",
    "email",
    "postal_address",
    "telephone",
    "about_link",
    "updated_date",
    "help_link",
)

# Unified 42 deterministic, read-only, privacy-safe W1 automated checks:
# 7 original + 28 GIGW 3.0 Annexure II/Ch. 5 + 7 RTI Act 2005 / GoI statutory checks.
W1_CHECK_SPECS: tuple[tuple[str, str, str, str, str], ...] = (
    # --- 7 preserved legacy checks ---
    ("contact_link", "Contact link", "quality", "GIGW 3.0", "Homepage navigation link to Contact Us, Who's Who, or Directory (multilingual)."),
    ("email", "Email indicator", "quality", "GIGW 3.0", "Published email indicator on homepage or linked contact page (presence only, zero PII retained)."),
    ("postal_address", "Postal address", "quality", "GIGW 3.0", "Published postal/office address indicator with PIN code (presence only, zero PII retained)."),
    ("telephone", "Telephone indicator", "quality", "GIGW 3.0", "Published telephone/helpline indicator on homepage or contact page (presence only, zero PII retained)."),
    ("about_link", "About Us link", "quality", "GIGW 3.0", "Homepage navigation link to About Us / organisation overview (multilingual)."),
    ("updated_date", "Last updated date", "quality", "GIGW 3.0", "Homepage displays a Last Updated / Last Reviewed date indicator (multilingual)."),
    ("help_link", "Help link", "quality", "GIGW 3.0", "Homepage link to a Help section (multilingual)."),
    # --- 28 new GIGW 3.0 automated checks ---
    ("emblem_or_logo", "Govt emblem / logo", "quality", "GIGW 3.0", "Homepage displays State Emblem of India or official government logo markup (alt/src/title/aria-label)."),
    ("ownership_statement", "Ownership attribution", "quality", "GIGW 3.0", "Header/footer ownership text identifying the responsible government body or NIC hosting attribution."),
    ("download_metadata", "Download size/format", "quality", "GIGW 3.0", "Downloadable document links (.pdf/.doc/.xls/.zip) on homepage disclose format/size metadata."),
    ("feedback_link", "Feedback link / form", "quality", "GIGW 3.0", "Homepage links to a Feedback page or embeds a feedback form indicator (read-only, never submitted)."),
    ("national_portal_link", "National Portal link", "quality", "GIGW 3.0", "Homepage includes a prominent link to india.gov.in (National Portal of India)."),
    ("responsive_viewport", "Responsive CSS / viewport", "quality", "GIGW 3.0", "Homepage declares <meta name='viewport'> and loads CSS stylesheets."),
    ("page_title_meta", "Title & page metadata", "quality", "GIGW 3.0", "Homepage has a descriptive non-default <title> plus <meta name='description'> or keywords."),
    ("data_table_headers", "Data table markup", "quality", "GIGW 3.0", "HTML tables on sampled pages use semantic <th>/<thead>/<caption>/scope markup."),
    ("gov_domain_canonical", "Official .gov.in/.nic.in host", "quality", "GIGW 3.0", "Canonical final URL stays within official .gov.in / .nic.in namespace without redirecting off-government."),
    ("social_media_links", "Social media links", "quality", "GIGW 3.0", "Homepage links to official social media platforms (X/Twitter, Facebook, YouTube, Instagram, LinkedIn)."),
    ("wim_disclosure", "Web Information Manager", "lifecycle", "GIGW 3.0", "Public mention/disclosure of Web Information Manager (WIM) on homepage or linked contact/RTI page."),
    ("website_policies_link", "Website policies link", "lifecycle", "GIGW 3.0", "Homepage links to Website Policies (Copyright, Hyperlinking, Privacy, CAP/CRP/CMAP, Terms & Conditions)."),
    ("privacy_policy_link", "Privacy policy", "lifecycle", "GIGW 3.0", "Published Privacy Policy link or section on homepage / Website Policies page."),
    ("terms_copyright_link", "Terms & copyright policy", "lifecycle", "GIGW 3.0", "Published Terms & Conditions and/or Copyright Policy link or section."),
    ("external_link_notice", "External link indication", "lifecycle", "GIGW 3.0", "Hyperlinking policy or external-link indicator (target='_blank' / external warning) on non-government links."),
    ("no_under_construction", "No under-construction stubs", "lifecycle", "GIGW 3.0", "Sampled pages are free from 'under construction' / 'coming soon' placeholder text."),
    ("bilingual_unicode", "Bilingual & Unicode", "lifecycle", "GIGW 3.0", "UTF-8/Unicode encoding declared and Hindi/regional language switcher or bilingual text present."),
    ("img_alt_text", "Image alt attributes (1.1.1)", "accessibility", "GIGW 3.0", "All <img> elements on homepage carry an alt attribute."),
    ("semantic_landmarks", "Semantic landmarks (1.3.1)", "accessibility", "GIGW 3.0", "Homepage uses semantic HTML5 landmarks (<main>/<nav>/<header>/<footer>) and heading tags (<h1>-<h6>)."),
    ("skip_to_main", "Skip to main content (2.4.1)", "accessibility", "GIGW 3.0", "Homepage provides a 'Skip to main content' bypass link targeting a valid fragment ID."),
    ("search_or_sitemap", "Search / Sitemap (2.4.5)", "accessibility", "GIGW 3.0", "Homepage provides a Site Search control/link and/or a Sitemap link."),
    ("html_lang_attr", "HTML lang attribute (3.1.1)", "accessibility", "GIGW 3.0", "Root <html> tag specifies a valid BCP-47 lang attribute."),
    ("form_input_labels", "Form input labels (3.3.2)", "accessibility", "GIGW 3.0", "Visible form inputs have programmatic <label>/aria-label/title attributes."),
    ("unique_dom_ids", "Unique DOM IDs (4.1.1)", "accessibility", "GIGW 3.0", "Parsed homepage HTML contains no duplicate id attributes."),
    ("https_valid_tls", "Valid HTTPS / TLS", "security", "GIGW 3.0", "Site serves valid verified HTTPS/TLS on its assigned .gov.in hostname (passive TLS verification)."),
    ("hsts_header", "HSTS header", "security", "GIGW 3.0", "HTTPS response sends Strict-Transport-Security (HSTS) header (GIGW 5.3.2)."),
    ("security_headers", "HTTP security headers", "security", "GIGW 3.0", "Response sends X-Content-Type-Options: nosniff and clickjacking protection (X-Frame-Options or CSP frame-ancestors)."),
    ("server_banner_obscured", "Server version obscured", "security", "GIGW 3.0", "Server / X-Powered-By HTTP headers do not disclose exact server/runtime version numbers."),
    # --- 7 new RTI Act 2005 & GoI statutory/policy checks ---
    ("rti_link", "RTI / Proactive disclosure link", "rti_act", "RTI Act 2005", "Dedicated RTI / Right to Information / Suo Motu Disclosure link on homepage or navigation."),
    ("pio_faa_disclosure", "CPIO / SPIO / FAA disclosure", "rti_act", "RTI Act 2005", "Public Information Officer (CPIO/SPIO/CAPIO) or First Appellate Authority designation indicator (zero PII stored)."),
    ("citizen_charter_link", "Citizen's Charter link", "statutory_rights_governance", "GoI Statutory & Policy", "Published Citizen's Charter / Client's Charter / Sevottam navigation link."),
    ("grievance_link", "Public Grievance / CPGRAMS link", "statutory_rights_governance", "GoI Statutory & Policy", "Public Grievance Redressal / CPGRAMS (pgportal.gov.in) / PGO navigation link."),
    ("acts_tenders_link", "Acts, Rules, Tenders & Schemes", "rti_act", "RTI Act 2005", "Public governance disclosure links: Acts/Rules/Manuals, Tenders/Procurement, Budget/Annual Reports, Schemes, or Open Data."),
    ("gov_email_domain", "Official @gov.in / @nic.in email", "statutory_security_data", "GoI Statutory & Policy", "Observed public email uses official @gov.in / @nic.in government domain rather than commercial webmail (domain class only, zero email strings stored)."),
    ("accessibility_link", "Accessibility / Screen Reader link", "statutory_rights_governance", "GoI Statutory & Policy", "Screen Reader Access or Accessibility Statement link per RPwD Rule 15(1)(c)(i) and GIGW."),
)

W1_CHECKS: tuple[str, ...] = tuple(spec[0] for spec in W1_CHECK_SPECS)

CONTACT_FIELDS: tuple[str, ...] = ("email", "postal_address", "telephone", "gov_email_domain")
CONTACT_UNASSESSABLE_FIELDS: frozenset[str] = frozenset(
    {"email", "postal_address", "telephone", "gov_email_domain", "wim_disclosure", "pio_faa_disclosure"}
)
RTI_UNASSESSABLE_FIELDS: frozenset[str] = frozenset({"pio_faa_disclosure", "wim_disclosure"})
CONDITIONAL_NA_FIELDS: frozenset[str] = frozenset(
    {
        "download_metadata",
        "data_table_headers",
        "external_link_notice",
        "img_alt_text",
        "form_input_labels",
        "gov_email_domain",
        "rti_link",
        "pio_faa_disclosure",
    }
)

VALID_CHECK_STATES: frozenset[str] = frozenset({"observed", "not_observed", "unassessable", "not_applicable"})
W1_REASON_KEYS: tuple[str, ...] = (
    "robots_restricted",
    "fetch_failed",
    "unsupported_page",
    "contact_unavailable",
    "rti_unavailable",
)

# Verified RTI Act, 2005 Section 24(1) & Second Schedule intelligence/security organisations.
# On these domains, absence of proactive RTI disclosures is recorded as `not_applicable`
# rather than `not_observed` (positive proactive disclosures still count as `observed`).
RTI_SEC24_EXEMPT_BASE_DOMAINS: frozenset[str] = frozenset(
    {
        "bsf.gov.in",
        "crpf.gov.in",
        "cisf.gov.in",
        "itbp.gov.in",
        "itbpolice.nic.in",
        "ssb.gov.in",
        "nsg.gov.in",
        "assamrifles.gov.in",
        "drdo.gov.in",
        "cbi.gov.in",
        "nia.gov.in",
        "enforcementdirectorate.gov.in",
        "fiuindia.gov.in",
        "ntro.gov.in",
        "narcoticsindia.nic.in",
        "dri.nic.in",
        "ceib.nic.in",
        "spg.nic.in",
        "bro.gov.in",
    }
)


def is_rti_sec24_exempt(domain: str) -> bool:
    """Return True if *domain* matches a verified RTI Act Section 24 / Second Schedule host."""
    host = domain.strip().lower().removeprefix("www.")
    if not host:
        return False
    return any(host == base or host.endswith("." + base) for base in RTI_SEC24_EXEMPT_BASE_DOMAINS)


def _matrix_json_candidates() -> list[Path]:
    from aiwatchdogs.projects import default_projects_dir

    candidates = [
        default_projects_dir() / "gov-in-public-information" / "conformity-matrix.json",
        Path(__file__).resolve().parents[3] / "projects" / "gov-in-public-information" / "conformity-matrix.json",
    ]
    return candidates


@lru_cache(maxsize=2)
def _load_matrix_from_path(path_str: str) -> dict[str, Any]:
    return json.loads(Path(path_str).read_text(encoding="utf-8"))


def load_conformity_matrix() -> dict[str, Any]:
    """Load the static read-only 159-checkpoint conformity matrix JSON."""
    for candidate in _matrix_json_candidates():
        if candidate.is_file():
            return _load_matrix_from_path(str(candidate.resolve()))
    raise FileNotFoundError("Could not locate projects/gov-in-public-information/conformity-matrix.json")


def build_website_audit_visual_payload(
    *,
    batches_done: int,
    batches_total: int,
    hosts_observed: int,
    hosts_unassessable: int,
    cp_counts: dict[str, dict[str, int]],
    reasons: dict[str, int],
) -> dict[str, Any]:
    """Assemble the complete ``visual_summary.website_audit`` payload for ``gov-in-public-information``."""
    matrix_doc = load_conformity_matrix()
    w1_meta_by_key = {item["key"]: item for item in matrix_doc.get("w1_checks") or []}

    checkpoints_out: list[dict[str, Any]] = []
    observed_pct_by_key: dict[str, float | None] = {}

    for key, label, section, framework, description in W1_CHECK_SPECS:
        c = cp_counts.get(key) or {"observed": 0, "not_observed": 0, "unassessable": 0, "not_applicable": 0}
        obs = int(c.get("observed", 0))
        not_obs = int(c.get("not_observed", 0))
        unass = int(c.get("unassessable", 0))
        na = int(c.get("not_applicable", 0))
        denom = obs + not_obs + unass + na
        obs_pct = round((obs / denom) * 100.0, 2) if denom > 0 else 0.0
        not_obs_pct = round((not_obs / denom) * 100.0, 2) if denom > 0 else 0.0
        unass_pct = round((unass / denom) * 100.0, 2) if denom > 0 else 0.0
        na_pct = round((na / denom) * 100.0, 2) if denom > 0 else 0.0

        # Track applicable-evaluated rate for rollups when at least one host was assessed on this check.
        applicable_denom = obs + not_obs + unass
        observed_pct_by_key[key] = round((obs / applicable_denom) * 100.0, 2) if applicable_denom > 0 else None

        meta = w1_meta_by_key.get(key) or {}
        checkpoints_out.append(
            {
                "key": key,
                "label": meta.get("label") or label,
                "section": meta.get("section") or section,
                "framework": meta.get("framework") or framework,
                "checkpoint_ids": list(meta.get("checkpoint_ids") or []),
                "description": meta.get("description") or description,
                "observed": obs,
                "not_observed": not_obs,
                "unassessable": unass,
                "not_applicable": na,
                "observed_pct": obs_pct,
                "not_observed_pct": not_obs_pct,
                "unassessable_pct": unass_pct,
                "not_applicable_pct": na_pct,
            }
        )

    sections_out: list[dict[str, Any]] = []
    for sec in matrix_doc.get("sections") or []:
        sec_keys = list(sec.get("w1_check_keys") or [])
        key_pcts = [observed_pct_by_key[k] for k in sec_keys if observed_pct_by_key.get(k) is not None]
        avg_pct = round(sum(key_pcts) / len(key_pcts), 2) if key_pcts else None
        sections_out.append(
            {
                "id": sec["id"],
                "name": sec["name"],
                "short_name": sec["short_name"],
                "framework": sec["framework"],
                "total_checkpoints": int(sec["total_checkpoints"]),
                "automated_count": int(sec["automated_count"]),
                "agent_count": int(sec["agent_count"]),
                "internal_count": int(sec["internal_count"]),
                "w1_check_keys": sec_keys,
                "avg_observed_pct": avg_pct,
            }
        )

    compact_matrix: list[dict[str, Any]] = []
    for cp in matrix_doc.get("checkpoints") or []:
        w1_keys = list(cp.get("w1_check_keys") or [])
        linked_pcts = [observed_pct_by_key[k] for k in w1_keys if observed_pct_by_key.get(k) is not None]
        cp_obs_pct = round(sum(linked_pcts) / len(linked_pcts), 2) if linked_pcts else None
        compact_matrix.append(
            {
                "id": cp["id"],
                "section_id": cp["section_id"],
                "section_name": cp["section_name"],
                "framework": cp["framework"],
                "source_group": cp["source_group"],
                "serial_no": str(cp["serial_no"]),
                "title": cp["title"],
                "requirement_text": cp["requirement_text"],
                "conformance_level": cp["conformance_level"],
                "citation": cp["citation"],
                "wcag_reference": cp.get("wcag_reference"),
                "wcag_level": cp.get("wcag_level"),
                "risks_addressed": list(cp.get("risks_addressed") or []),
                "source_url": cp["source_url"],
                "verification_mode": cp["verification_mode"],
                "automation_coverage": cp["automation_coverage"],
                "w1_check_keys": w1_keys,
                "public_disclosure_indicator": cp.get("public_disclosure_indicator") or "",
                "caveats": cp.get("caveats") or "",
                "observed_pct": cp_obs_pct,
            }
        )

    return {
        "batches_done": batches_done,
        "batches_total": batches_total,
        "hosts_observed": hosts_observed,
        "hosts_unassessable": hosts_unassessable,
        "hosts_total": hosts_observed + hosts_unassessable,
        "total_directory_hosts": TOTAL_DIRECTORY_HOSTS,
        "matrix_version": matrix_doc.get("matrix_version") or MATRIX_VERSION,
        "matrix_endpoint": "/api/missions/gov-in-public-information/matrix",
        "matrix_summary": dict(matrix_doc.get("summary") or {}),
        "sections": sections_out,
        "checkpoints": checkpoints_out,
        "matrix": compact_matrix,
        "reasons": reasons,
    }
