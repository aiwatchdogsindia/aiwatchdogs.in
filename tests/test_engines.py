"""Comprehensive unit and integration tests for core safety, RTI drafter, M1-M8 engines, and connectors."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pypdf import PdfWriter

from aiwatchdogs.connectors.loader import USER_AGENT, load_dataset
from aiwatchdogs.core.rti import generate_rti_draft
from aiwatchdogs.core.safety import (
    compute_sha256,
    lint_neutrality,
    scrub_pii,
    verhoeff_generate_check_digit,
    verhoeff_validate,
)
from aiwatchdogs.engines.registry import (
    METHODOLOGY_CATALOG,
    get_engine_script_hash,
    run_engine,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DIR = REPO_ROOT / "datasets" / "sample"


def _make_valid_aadhaar(prefix_11: str) -> str:
    assert len(prefix_11) == 11 and prefix_11[0] in "23456789"
    return prefix_11 + verhoeff_generate_check_digit(prefix_11)


def _create_minimal_pdf(path: Path, text_lines: list[str]) -> None:
    """Create a real valid PDF with extractable text using pypdf (no external dependencies)."""
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)

    # Embed standard Type1 Helvetica font and raw PDF content stream with text lines
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    font_dict = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_ref = writer._add_object(font_dict)
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
    )

    escaped_ops = ["BT", "/F1 11 Tf", "50 740 Td", "15 TL"]
    for idx, line in enumerate(text_lines):
        safe = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        if idx == 0:
            escaped_ops.append(f"({safe}) Tj")
        else:
            escaped_ops.append(f"T* ({safe}) Tj")
    escaped_ops.append("ET")

    stream = DecodedStreamObject()
    stream.set_data("\n".join(escaped_ops).encode("latin-1", errors="replace"))
    page[NameObject("/Contents")] = writer._add_object(stream)

    with path.open("wb") as fh:
        writer.write(fh)


# ---------------------------------------------------------------------------
# 1. Core Safety: Verhoeff Aadhaar, PII Scrubbing, Neutrality Lint, SHA-256
# ---------------------------------------------------------------------------


def test_verhoeff_checksum_validates_and_catches_errors() -> None:
    valid_aadhaar = _make_valid_aadhaar("36759834601")
    assert len(valid_aadhaar) == 12
    assert verhoeff_validate(valid_aadhaar) is True

    # Single-digit mutation must fail Verhoeff
    bad_last_digit = valid_aadhaar[:-1] + str((int(valid_aadhaar[-1]) + 1) % 10)
    assert verhoeff_validate(bad_last_digit) is False

    # Adjacent transposition must fail Verhoeff
    transposed = valid_aadhaar[:4] + valid_aadhaar[5] + valid_aadhaar[4] + valid_aadhaar[6:]
    if transposed != valid_aadhaar:
        assert verhoeff_validate(transposed) is False


def test_scrub_pii_redacts_all_indian_pii_and_preserves_corporate_ids() -> None:
    valid_aadhaar = _make_valid_aadhaar("48291037462")
    spaced_aadhaar = f"{valid_aadhaar[:4]} {valid_aadhaar[4:8]} {valid_aadhaar[8:]}"
    invalid_12_digit = valid_aadhaar[:-1] + str((int(valid_aadhaar[-1]) + 3) % 10)

    sample = (
        f"Beneficiary Aadhaar: {spaced_aadhaar}, non-aadhaar ref {invalid_12_digit}, "
        f"PAN: ABCDE1234F, Corporate GSTIN: 18AAACD1234F1Z5, CIN: U45200DL2024PTC990001, "
        f"IFSC: SBIN0001234 Bank Account No: 30981234567890, "
        f"Mobile: +91 9876543210 and 9811223344, Budget INR 7500000000, "
        f"Email: ramesh.demo@gmail.com, Official CPIO: cpio@morth.gov.in."
    )

    cleaned, redactions = scrub_pii(sample)

    assert "[REDACTED_AADHAAR]" in cleaned
    assert valid_aadhaar not in cleaned
    assert spaced_aadhaar not in cleaned
    # Invalid checksum 12-digit number must NOT be misidentified as Aadhaar
    assert invalid_12_digit in cleaned

    assert "[REDACTED_PAN]" in cleaned
    assert "ABCDE1234F" not in cleaned
    # Registered corporate identifiers must be preserved per DPDP / public registry rules
    assert "18AAACD1234F1Z5" in cleaned
    assert "U45200DL2024PTC990001" in cleaned

    assert "[REDACTED_IFSC]" in cleaned
    assert "SBIN0001234" not in cleaned
    assert "[REDACTED_BANK_ACCOUNT]" in cleaned
    assert "30981234567890" not in cleaned

    assert "[REDACTED_PHONE]" in cleaned
    assert "9876543210" not in cleaned
    assert "9811223344" not in cleaned
    # Round fiscal amount must remain untouched
    assert "7500000000" in cleaned

    assert "[REDACTED_EMAIL]" in cleaned
    assert "ramesh.demo@gmail.com" not in cleaned
    # Generic .gov.in CPIO contact is preserved
    assert "cpio@morth.gov.in" in cleaned

    assert set(redactions) >= {"aadhaar", "pan", "ifsc", "bank_account", "phone", "email"}


@pytest.mark.parametrize(
    "bad_term",
    [
        "scam",
        "corrupt",
        "corruption",
        "bribe",
        "fraud",
        "kickback",
        "guilty",
        "stolen",
        "rigged",
        "crook",
        "loot",
        "embezzlement",
    ],
)
def test_lint_neutrality_rejects_defamatory_terms(bad_term: str) -> None:
    res = lint_neutrality(
        title=f"Alleged {bad_term} in tender",
        summary="This public tender shows statistical concentration.",
        benign_explanations=["Proprietary OEM exemption under GFR Rule 166."],
    )
    assert res.passed is False
    assert len(res.violations) >= 1


def test_lint_neutrality_requires_benign_explanations_and_accepts_neutral_text() -> None:
    missing_benign = lint_neutrality(
        title="Public Record Anomaly: Single-Bidder Share 60%",
        summary="Six of ten competitive tenders recorded a single responsive bid.",
        benign_explanations=[],
    )
    assert missing_benign.passed is False

    clean = lint_neutrality(
        title="Public Record Anomaly: Single-Bidder Share 60%",
        summary="Six of ten competitive tenders recorded a single responsive bid.",
        benign_explanations=[
            "Specialized equipment may qualify for single-tender procurement under GFR 2017 Rule 166."
        ],
    )
    assert clean.passed is True
    assert clean.violations == []


def test_compute_sha256_deterministic() -> None:
    h1 = compute_sha256("aiwatchdogs.in")
    h2 = compute_sha256(b"aiwatchdogs.in")
    assert h1 == h2
    assert len(h1) == 64


# ---------------------------------------------------------------------------
# 2. RTI Act 2005 Sec 6(1) & Sec 2(f) Draft Generator
# ---------------------------------------------------------------------------


def test_generate_rti_draft_statutory_requirements_and_500_word_cap() -> None:
    finding = {
        "title": "Procurement Red-Flag Lead: Single-Bidder Concentration — Demo Division",
        "summary": "Public record anomaly " + ("detail " * 600),
        "methodology_code": "M1",
        "entity_name": "Assam Public Works (Roads) Division — Demo Unit",
        "jurisdiction": "State:Assam",
        "fiscal_year": "FY 2024-25",
        "monetary_exposure_inr": 132_000_000,
        "rule_triggered": "GFR 2017 Rule 166",
        "citations": [{"record_id": "DEMO-AS-PWD-2024-001"}],
        "suggested_authority": "Assam Public Works Department",
    }

    draft = generate_rti_draft(finding)
    assert draft["public_authority"] == "Assam Public Works Department"
    assert draft["rti_portal_url"] == "https://rtionline.gov.in"
    assert "Section 6(1)" in draft["statutory_sections"]
    assert "Section 2(f)" in draft["statutory_sections"]
    assert draft["word_count"] <= 500
    assert draft["word_count"] == len(draft["body_text"].split())
    assert "rtionline.gov.in" in draft["body_text"]
    assert "Rs. 10" in draft["body_text"]
    assert "Section 7(5)" in draft["body_text"]
    assert "30 days" in draft["body_text"]
    assert len(draft["requested_records"]) >= 3
    # Must never ask accusatory or unanswerable "why" questions
    assert "why did" not in draft["body_text"].lower()


# ---------------------------------------------------------------------------
# 3. All 8 Engines (M1-M8) End-to-End Against Clearly Badged Sample Datasets
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "filename", "expected_min_leads", "expected_min_cleared"),
    [
        ("M1", "m1_ocds_procurement.json", 1, 6),
        ("M2", "m2_gem_split_tenders.csv", 3, 5),
        ("M3", "m3_vendor_concentration.csv", 1, 6),
        ("M4", "m4_unit_price_boq.csv", 2, 10),
        ("M5", "m5_cga_march_rush.csv", 2, 3),
        ("M6", "m6_cag_audit_paras.json", 3, 2),
        ("M7", "m7_pmgsy_mplads_mgnrega.csv", 3, 3),
        ("M8", "m8_corporate_proximity.json", 3, 2),
    ],
)
def test_all_eight_engines_on_sample_datasets(
    code: str,
    filename: str,
    expected_min_leads: int,
    expected_min_cleared: int,
) -> None:
    path = SAMPLE_DIR / filename
    assert path.exists(), f"Missing sample dataset: {path}"

    ds = load_dataset(str(path))
    assert ds.is_demo_sample is True
    assert len(ds.sha256) == 64
    assert ds.byte_size > 0
    assert len(ds.records) > 0

    result = run_engine(code, ds.records)
    assert result.methodology_code == code
    assert result.records_scanned == len(ds.records)
    assert result.monetary_audited_inr > 0
    assert len(result.leads) >= expected_min_leads
    assert result.cleared_count >= expected_min_cleared
    assert result.script_hash == get_engine_script_hash(code)
    assert len(result.script_hash) == 64

    # Every emitted lead MUST pass BNS Sec 356 neutrality linting and produce a <=500w RTI draft
    for lead in result.leads:
        lint = lint_neutrality(lead.title, lead.summary, lead.benign_explanations)
        assert lint.passed, f"{code} lead failed neutrality lint: {lint.violations}"
        assert len(lead.benign_explanations) >= 1
        assert len(lead.citations) >= 1

        rti = generate_rti_draft(lead)
        assert rti["word_count"] <= 500
        assert rti["rti_portal_url"] == "https://rtionline.gov.in"


def test_engine_metric_precision_spot_checks() -> None:
    # M1: verify OCDS flattening computed single_bid_share=60.0% and repeat vendor count=4
    m1_ds = load_dataset(str(SAMPLE_DIR / "m1_ocds_procurement.json"))
    assert m1_ds.format == "ocds_json"
    m1_res = run_engine("M1", m1_ds.records)
    assert m1_res.leads[0].anomaly_metrics["single_bid_share_pct"] == 60.0
    assert m1_res.leads[0].anomaly_metrics["short_window_single_bid_count"] == 4
    assert m1_res.leads[0].anomaly_metrics["repeat_single_bid_vendors"][
        "Demo Brahmaputra Infra Works Pvt Ltd"
    ] == 4

    # M2: verify GFR thresholds detected (50k, 10L, 50L)
    m2_ds = load_dataset(str(SAMPLE_DIR / "m2_gem_split_tenders.csv"))
    m2_res = run_engine("M2", m2_ds.records)
    thresholds_hit = {lead.anomaly_metrics["threshold_inr"] for lead in m2_res.leads}
    assert thresholds_hit == {50_000.0, 1_000_000.0, 5_000_000.0}

    # M3: verify HHI > 0.35 and top vendor share > 40%
    m3_ds = load_dataset(str(SAMPLE_DIR / "m3_vendor_concentration.csv"))
    m3_res = run_engine("M3", m3_ds.records)
    assert m3_res.leads[0].anomaly_metrics["hhi"] > 0.35
    assert m3_res.leads[0].anomaly_metrics["top_vendor_share_pct"] > 40.0
    assert m3_res.leads[0].anomaly_metrics["narrow_spread_count"] >= 3

    # M4: verify both unit-price outlier (>1.75x median) and escalation (>20%) leads
    m4_ds = load_dataset(str(SAMPLE_DIR / "m4_unit_price_boq.csv"))
    m4_res = run_engine("M4", m4_ds.records)
    assert any(l.anomaly_metrics["median_ratio"] > 1.75 for l in m4_res.leads)
    assert any(l.anomaly_metrics["escalation_pct"] > 20.0 for l in m4_res.leads)

    # M5: verify MoF CapEx waiver ministry was cleared while non-waived March Rush & Variance flagged
    m5_ds = load_dataset(str(SAMPLE_DIR / "m5_cga_march_rush.csv"))
    m5_res = run_engine("M5", m5_ds.records)
    flagged_entities = {l.entity_name for l in m5_res.leads}
    assert "Ministry of Rail Freight Corridors — Demo" not in flagged_entities
    assert "Ministry of Rural Connectivity — Demo" in flagged_entities
    assert "Department of Urban Water Infrastructure — Demo" in flagged_entities

    # M7: verify all three schemes (PMGSY, MPLADS, MGNREGA) produced leads
    m7_ds = load_dataset(str(SAMPLE_DIR / "m7_pmgsy_mplads_mgnrega.csv"))
    m7_res = run_engine("M7", m7_ds.records)
    schemes_hit = {l.anomaly_metrics["scheme"] for l in m7_res.leads}
    assert schemes_hit == {"PMGSY", "MPLADS", "MGNREGA"}

    # M8: verify <180d vendor, shared DIN overlap, and disclosure proximity lead are strictly neutral
    m8_ds = load_dataset(str(SAMPLE_DIR / "m8_corporate_proximity.json"))
    m8_res = run_engine("M8", m8_ds.records)
    assert any((l.anomaly_metrics["company_age_days"] or 999) < 180 for l in m8_res.leads)
    assert any("09911223" in l.anomaly_metrics["shared_dins"] for l in m8_res.leads)
    assert any(l.anomaly_metrics["electoral_disclosure_inr"] > 0 for l in m8_res.leads)
    assert all(l.anomaly_metrics["disclosure_only"] is True for l in m8_res.leads)


def test_methodology_catalog_contains_m1_through_m8() -> None:
    assert sorted(METHODOLOGY_CATALOG.keys()) == [f"M{i}" for i in range(1, 9)]
    for code, meta in METHODOLOGY_CATALOG.items():
        sample_path = REPO_ROOT / meta["sample_dataset"]
        assert sample_path.exists(), f"{code} sample_dataset missing: {sample_path}"
    with pytest.raises(ValueError, match="Unknown methodology code"):
        run_engine("M99", [])


def test_m2_two_point_five_lakh_lpc_ceiling_and_m8_flat_shared_din() -> None:
    # M2: GFR Rule 155 INR 2.5 Lakh LPC ceiling (85%-99.9% = 212,500..249,750)
    lpc_records = [
        {
            "po_id": f"DEMO-LPC-{i}",
            "buyer_name": "Regional Agricultural Station — Demo",
            "item_category": "Lab Glassware",
            "award_date": f"2025-01-{10 + i * 7:02d}",
            "award_value_inr": val,
            "jurisdiction": "Union of India",
            "fiscal_year": "FY 2024-25",
        }
        for i, val in enumerate([232_000, 244_500, 229_000])
    ]
    m2_res = run_engine("M2", lpc_records)
    assert len(m2_res.leads) == 1
    assert m2_res.leads[0].anomaly_metrics["threshold_inr"] == 250_000.0

    # M8: flat rows sharing same tender_id and overlapping DIN
    flat_rows = [
        {
            "tender_id": "DEMO-FLAT-99",
            "buyer_name": "Municipal Corp — Demo",
            "vendor_name": "Demo Alpha Works Pvt Ltd",
            "cin": "U45200MH2018PTC111111",
            "incorporation_date": "2018-01-01",
            "award_date": "2025-01-15",
            "award_value_inr": 15_000_000,
            "director_dins": "08812345,08812346",
        },
        {
            "tender_id": "DEMO-FLAT-99",
            "buyer_name": "Municipal Corp — Demo",
            "vendor_name": "Demo Beta Works Pvt Ltd",
            "cin": "U45200MH2019PTC222222",
            "incorporation_date": "2019-01-01",
            "award_date": "2025-01-15",
            "award_value_inr": 0,
            "director_dins": "08812345,08899999",
        },
    ]
    m8_res = run_engine("M8", flat_rows)
    assert len(m8_res.leads) == 1
    assert "08812345" in m8_res.leads[0].anomaly_metrics["shared_dins"]


def test_aadhaar_prefix_zero_or_one_not_falsely_redacted() -> None:
    # UIDAI Aadhaar never starts with 0 or 1 even if Verhoeff checksum passes
    zero_prefixed = "01234567890" + verhoeff_generate_check_digit("01234567890")
    hyphen_valid = _make_valid_aadhaar("72345678901")
    hyphen_fmt = f"{hyphen_valid[:4]}-{hyphen_valid[4:8]}-{hyphen_valid[8:]}"
    text = f"Ref {zero_prefixed} and citizen {hyphen_fmt}"
    cleaned, tags = scrub_pii(text)
    assert zero_prefixed in cleaned
    assert hyphen_fmt not in cleaned
    assert "[REDACTED_AADHAAR]" in cleaned
    assert "aadhaar" in tags


# ---------------------------------------------------------------------------
# 4. Dataset Connector: PDF Extraction (M6), Live Tier-1 HTTP & CAPTCHA Guard
# ---------------------------------------------------------------------------


def test_pdf_loader_and_m6_text_para_extraction(tmp_path: Path) -> None:
    pdf_path = tmp_path / "cag_demo_report.pdf"
    _create_minimal_pdf(
        pdf_path,
        [
            "Comptroller and Auditor General of India - Compliance Audit (Sample Demo)",
            "Para 4.3 Unfruitful expenditure on regional cold storage project",
            "An expenditure of Rs. 42.75 crore remained unfruitful and ATN is unresolved",
            "despite repeat audit observations from the earlier audit cycle.",
            "Para 4.4 Minor procedural note on inventory register",
            "An amount of Rs. 1.20 crore was reconciled and settled by the department.",
        ],
    )

    ds = load_dataset(str(pdf_path))
    assert ds.format == "pdf"
    assert len(ds.records) == 1
    assert "Para 4.3" in ds.raw_text

    res = run_engine("M6", ds.records)
    assert len(res.leads) == 1
    assert res.cleared_count == 1
    assert res.leads[0].anomaly_metrics["para_id"] == "Para 4.3"
    assert res.leads[0].anomaly_metrics["quantified_impact_crore"] == pytest.approx(42.75)


def test_http_loader_ckan_api_user_agent_and_captcha_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_headers: dict[str, Any] = {}

    class _MockResponse:
        def __init__(self, status_code: int, content: bytes, content_type: str) -> None:
            self.status_code = status_code
            self.content = content
            self.headers = {"content-type": content_type}

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise httpx.HTTPStatusError(
                    f"HTTP {self.status_code}",
                    request=httpx.Request("GET", "https://openbudgetsindia.org/api"),
                    response=httpx.Response(self.status_code),
                )

    ckan_payload = {
        "success": True,
        "result": {
            "records": [
                {
                    "demand_no": "Demand-10",
                    "ministry_name": "Ministry of Demo Budget",
                    "be_crore": 1000,
                    "re_crore": 1000,
                    "actual_crore": 980,
                    "q4_spend_crore": 420,
                    "march_spend_crore": 210,
                }
            ]
        },
    }

    def _mock_get(self: Any, url: str) -> _MockResponse:
        captured_headers.update(dict(self.headers))
        if "captcha" in url:
            return _MockResponse(
                200,
                b"<html><body>Please verify you are human via reCAPTCHA</body></html>",
                "text/html; charset=utf-8",
            )
        if "forbidden" in url:
            return _MockResponse(403, b"Forbidden", "text/html")
        return _MockResponse(
            200,
            json.dumps(ckan_payload).encode("utf-8"),
            "application/json",
        )

    monkeypatch.setattr(httpx.Client, "get", _mock_get)

    ds = load_dataset("https://openbudgetsindia.org/api/3/action/datastore_search?resource_id=demo")
    assert ds.format == "ckan_json"
    assert ds.is_demo_sample is False
    assert len(ds.records) == 1
    assert captured_headers.get("user-agent") == USER_AGENT

    m5_res = run_engine("M5", ds.records)
    assert len(m5_res.leads) == 1

    with pytest.raises(PermissionError, match="CAPTCHA"):
        load_dataset("https://eprocure.gov.in/captcha-gate")

    with pytest.raises(PermissionError, match="403"):
        load_dataset("https://mca.gov.in/forbidden-endpoint")
