"""Comprehensive unit and integration tests for core safety, RTI drafter, M1-M8 engines, and real-format connectors."""

from __future__ import annotations

import json
import zipfile
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


def _make_valid_aadhaar(prefix_11: str) -> str:
    assert len(prefix_11) == 11 and prefix_11[0] in "23456789"
    return prefix_11 + verhoeff_generate_check_digit(prefix_11)


def _create_minimal_pdf(path: Path, pages_lines: list[list[str]]) -> None:
    """Create a real valid multi-page PDF with extractable text using pypdf."""
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    for text_lines in pages_lines:
        page = writer.add_blank_page(width=612, height=792)
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


def _col_letter(idx: int) -> str:
    res = ""
    n = idx + 1
    while n > 0:
        n, rem = divmod(n - 1, 26)
        res = chr(65 + rem) + res
    return res


def _create_minimal_xlsx(
    path: Path,
    sheets: dict[str, list[list[Any]]],
    *,
    use_inline_strings: bool = False,
) -> None:
    """Create a minimal valid OOXML .xlsx archive using pure Python stdlib."""
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    pkg_rel_ns = "http://schemas.openxmlformats.org/package/2006/relationships"

    shared_strings: list[str] = []
    sst_index: dict[str, int] = {}

    def _intern(val: str) -> int:
        if val not in sst_index:
            sst_index[val] = len(shared_strings)
            shared_strings.append(val)
        return sst_index[val]

    def _xml_escape(s: str) -> str:
        return (
            s.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    sheet_entries = list(sheets.items())
    wb_sheets_xml = []
    rels_xml = []
    sheet_xml_blobs: dict[str, str] = {}

    for s_idx, (sname, rows) in enumerate(sheet_entries, start=1):
        rid = f"rId{s_idx}"
        wb_sheets_xml.append(
            f'<sheet name="{_xml_escape(sname)}" sheetId="{s_idx}" r:id="{rid}"/>'
        )
        rels_xml.append(
            f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{s_idx}.xml"/>'
        )
        row_xml_parts = []
        for r_idx, row in enumerate(rows, start=1):
            cells_xml = []
            for c_idx, val in enumerate(row):
                ref = f"{_col_letter(c_idx)}{r_idx}"
                if val is None or val == "":
                    continue
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    cells_xml.append(f'<c r="{ref}"><v>{val}</v></c>')
                elif use_inline_strings:
                    cells_xml.append(
                        f'<c r="{ref}" t="inlineStr"><is><t>{_xml_escape(str(val))}</t></is></c>'
                    )
                else:
                    si = _intern(str(val))
                    cells_xml.append(f'<c r="{ref}" t="s"><v>{si}</v></c>')
            row_xml_parts.append(f'<row r="{r_idx}">{"".join(cells_xml)}</row>')
        sheet_xml_blobs[f"xl/worksheets/sheet{s_idx}.xml"] = (
            f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<worksheet xmlns="{ns}"><sheetData>{"".join(row_xml_parts)}</sheetData></worksheet>'
        )

    wb_xml = (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<workbook xmlns="{ns}" xmlns:r="{rel_ns}"><sheets>{"".join(wb_sheets_xml)}</sheets></workbook>'
    )
    wb_rels_xml = (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<Relationships xmlns="{pkg_rel_ns}">{"".join(rels_xml)}</Relationships>'
    )

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("xl/workbook.xml", wb_xml)
        zf.writestr("xl/_rels/workbook.xml.rels", wb_rels_xml)
        for spath, blob in sheet_xml_blobs.items():
            zf.writestr(spath, blob)
        if not use_inline_strings:
            si_xml = "".join(
                f"<si><t>{_xml_escape(s)}</t></si>" for s in shared_strings
            )
            zf.writestr(
                "xl/sharedStrings.xml",
                f'<?xml version="1.0" encoding="UTF-8"?><sst xmlns="{ns}">{si_xml}</sst>',
            )


# ---------------------------------------------------------------------------
# 1. Core Safety: Verhoeff Aadhaar, PII Scrubbing, Neutrality Lint, SHA-256
# ---------------------------------------------------------------------------


def test_verhoeff_checksum_validates_and_catches_errors() -> None:
    valid_aadhaar = _make_valid_aadhaar("36759834601")
    assert len(valid_aadhaar) == 12
    assert verhoeff_validate(valid_aadhaar) is True

    bad_last_digit = valid_aadhaar[:-1] + str((int(valid_aadhaar[-1]) + 1) % 10)
    assert verhoeff_validate(bad_last_digit) is False

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
        f"Email: ramesh.citizen@gmail.com, Official CPIO: cpio@morth.gov.in."
    )

    cleaned, redactions = scrub_pii(sample)

    assert "[REDACTED_AADHAAR]" in cleaned
    assert valid_aadhaar not in cleaned
    assert spaced_aadhaar not in cleaned
    assert invalid_12_digit in cleaned

    assert "[REDACTED_PAN]" in cleaned
    assert "ABCDE1234F" not in cleaned
    assert "18AAACD1234F1Z5" in cleaned
    assert "U45200DL2024PTC990001" in cleaned

    assert "[REDACTED_IFSC]" in cleaned
    assert "SBIN0001234" not in cleaned
    assert "[REDACTED_BANK_ACCOUNT]" in cleaned
    assert "30981234567890" not in cleaned

    assert "[REDACTED_PHONE]" in cleaned
    assert "9876543210" not in cleaned
    assert "9811223344" not in cleaned
    assert "7500000000" in cleaned

    assert "[REDACTED_EMAIL]" in cleaned
    assert "ramesh.citizen@gmail.com" not in cleaned
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


def test_aadhaar_prefix_zero_or_one_not_falsely_redacted() -> None:
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
# 2. RTI Act 2005 Sec 6(1) & Sec 2(f) Draft Generator & Registry Catalog
# ---------------------------------------------------------------------------


def test_generate_rti_draft_statutory_requirements_and_500_word_cap() -> None:
    # 1. State public authority -> MUST route to State RTI portal, never central rtionline.gov.in
    finding_state = {
        "title": "Procurement Red-Flag Lead: Single-Bidder Concentration — PWD Division",
        "summary": "Public record anomaly " + ("detail " * 600),
        "methodology_code": "M1",
        "entity_name": "Assam Public Works (Roads) Division",
        "jurisdiction": "State:Assam",
        "fiscal_year": "FY 2023-24",
        "monetary_exposure_inr": 132_000_000,
        "rule_triggered": "GFR 2017 Rule 166",
        "citations": [{"record_id": "ocds-kjhdrl-2023_PWD_001"}],
        "suggested_authority": "Assam Public Works Department",
    }

    draft = generate_rti_draft(finding_state)
    assert draft["public_authority"] == "Assam Public Works Department"
    assert draft["jurisdiction_level"] == "state"
    assert draft["state_name"] == "Assam"
    assert draft["rti_portal_url"] == "https://rtionline.assam.gov.in"
    assert "State Public Information Officer (SPIO)" in draft["cpio_designation"]
    assert draft["central_portal_warning"] is not None
    assert draft["human_filing_required"] is True
    assert "Section 6(1)" in draft["statutory_sections"]
    assert "Section 2(f)" in draft["statutory_sections"]
    assert draft["word_count"] <= 500
    assert draft["word_count"] == len(draft["body_text"].split())
    assert "rtionline.assam.gov.in" in draft["body_text"]
    assert "Rs. 10" in draft["body_text"]
    assert "Section 7(5)" in draft["body_text"]
    assert "30 days" in draft["body_text"]
    assert len(draft["requested_records"]) >= 3
    assert "why did" not in draft["body_text"].lower()

    # 2. Central public authority -> routes to central https://rtionline.gov.in
    finding_central = {
        "title": "Fiscal Pacing Lead: CGA Monthly Accounts Divergence",
        "summary": "Mid-year utilization variance observed in Union accounts.",
        "methodology_code": "M5",
        "entity_name": "Ministry of Road Transport and Highways",
        "jurisdiction": "Union of India",
        "fiscal_year": "FY 2024-25",
        "monetary_exposure_inr": 500_000_000,
        "rule_triggered": "GFR 2017 Rule 62(3)",
        "citations": [{"record_id": "CGA-2024-12"}],
        "suggested_authority": "Ministry of Road Transport and Highways",
    }
    central_draft = generate_rti_draft(finding_central)
    assert central_draft["jurisdiction_level"] == "central"
    assert central_draft["rti_portal_url"] == "https://rtionline.gov.in"
    assert central_draft["central_portal_warning"] is None
    assert central_draft["human_filing_required"] is True
    assert "rtionline.gov.in" in central_draft["body_text"]
    assert central_draft["word_count"] <= 500


def test_methodology_catalog_contains_m1_through_m8_and_no_sample_dir() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    assert not (repo_root / "datasets" / "sample").exists()
    assert sorted(METHODOLOGY_CATALOG.keys()) == sorted([f"M{i}" for i in range(1, 9)] + ["P1", "UB1", "OR1", "P5", "P6", "P7", "P8", "P9"])
    for code, meta in METHODOLOGY_CATALOG.items():
        assert "sample_dataset" not in meta
        assert meta["reference_dataset_url"].startswith("https://")
        assert len(get_engine_script_hash(code)) == 64
    with pytest.raises(ValueError, match="Unknown methodology code"):
        run_engine("M99", [])


# ---------------------------------------------------------------------------
# 3. Procurement Engines (M1-M4) with CivicDataLab OCDS (.json.zip, CSV, .xlsx)
# ---------------------------------------------------------------------------


def test_m1_and_m2_flat_ocds_csv_with_civicdatalab_dates_and_tender_value_fallback(tmp_path: Path) -> None:
    csv_path = tmp_path / "assam_ocds_flat.csv"
    csv_content = "\n".join(
        [
            "ocid,tender/id,tender/title,tender/procurementMethod,tenderclassification/description,tender/value/amount,tender/datePublished,tender/tenderPeriod/durationInDays,tender/numberOfTenderers,buyer/name,fiscal_year",
            "ocds-as-1,2022_HF_1,Hospital Equipment A,Open,Medical Equipment,2400000,05-Apr-2021 10:00 AM,10,1,Health and Family Welfare Assam,2021",
            "ocds-as-2,2022_HF_2,Hospital Equipment B,Open,Medical Equipment,2350000,19-Apr-2021 11:00 AM,11,1,Health and Family Welfare Assam,2021",
            "ocds-as-3,2022_HF_3,Hospital Equipment C,Open,Medical Equipment,2450000,05-May-2021 04:00 PM,9,1,Health and Family Welfare Assam,2021",
            "ocds-as-4,2022_HF_4,Hospital Equipment D,Open,Medical Equipment,2300000,20-May-2021 02:00 PM,30,3,Health and Family Welfare Assam,2021",
            "ocds-as-5,2022_HF_5,Hospital Equipment E,Open,Medical Equipment,1500000,22-May-2021 02:00 PM,30,4,Health and Family Welfare Assam,2021",
            # Cleared buyer (competitive multi-bidder)
            "ocds-as-6,2022_EDU_1,School Desks 1,Open,Furniture,1200000,10-Jun-2021 10:00 AM,30,5,Elementary Education Assam,2021",
            "ocds-as-7,2022_EDU_2,School Desks 2,Open,Furniture,1100000,15-Jun-2021 10:00 AM,30,4,Elementary Education Assam,2021",
            "ocds-as-8,2022_EDU_3,School Desks 3,Open,Furniture,1300000,20-Jun-2021 10:00 AM,30,3,Elementary Education Assam,2021",
            "ocds-as-9,2022_EDU_4,School Desks 4,Open,Furniture,1250000,25-Jun-2021 10:00 AM,30,6,Elementary Education Assam,2021",
        ]
    )
    csv_path.write_text(csv_content, encoding="utf-8")

    ds = load_dataset(str(csv_path), allow_local_files=True)
    assert ds.is_demo_sample is False
    assert ds.format == "csv"
    assert len(ds.records) == 9
    assert ds.records[0]["buyer_name"] == "Health and Family Welfare Assam"
    assert ds.records[0]["tender_value_inr"] == 2_400_000.0
    assert ds.records[0]["award_value_inr"] is None
    assert ds.records[0]["award_date"] == ""
    assert ds.records[0]["bid_count"] == 1

    m1 = run_engine("M1", ds.records)
    assert len(m1.leads) == 1
    assert m1.cleared_count == 4
    assert m1.leads[0].anomaly_metrics["single_bid_share_pct"] == 60.0
    assert m1.leads[0].anomaly_metrics["short_window_single_bid_count"] == 3
    assert m1.leads[0].jurisdiction == "State:Assam"

    # For Assam tender-estimate-only rows, M2 applies Assam APPR 2020 (₹25L) and emits
    # a non-splitting tender-estimate price-band observation rather than a splitting claim.
    m2 = run_engine("M2", ds.records)
    assert len(m2.leads) == 1
    assert m2.leads[0].anomaly_metrics["threshold_inr"] == 2_500_000.0
    assert m2.leads[0].anomaly_metrics["threshold_scope"] == "state_assam_appr_2020_reference"
    assert m2.leads[0].anomaly_metrics["cluster_po_count"] == 4
    assert m2.leads[0].anomaly_metrics["amount_type"] == "tender_estimate"
    assert m2.leads[0].anomaly_metrics["splitting_claim_supported"] is False
    assert m2.leads[0].anomaly_metrics["has_award_evidence"] is False
    assert m2.cleared_count == 5

    # Verify filter_buyer + max_rows context hints
    filtered_ds = load_dataset(
        str(csv_path),
        context={"filter_buyer": "Education", "max_rows": 2},
        allow_local_files=True,
    )
    assert len(filtered_ds.records) == 2
    assert all("Education" in r["buyer_name"] for r in filtered_ds.records)


def test_m3_and_m4_civicdatalab_json_zip_unwrapping_and_award_escalation(tmp_path: Path) -> None:
    """Test pure-stdlib .json.zip reader, __MACOSX skip, 1-element OCDS lists, M3 HHI & M4 award-vs-estimate."""
    releases = []
    # 5 awarded releases for BTC-PWD where 1 vendor wins 4 of 5 + one 40% estimate escalation
    for i in range(1, 6):
        vendor = "ALPHA INFRA ASSAM" if i <= 4 else "BETA BUILDERS"
        est = 10_000_000.0
        aw = 14_500_000.0 if i == 1 else 10_200_000.0
        releases.append(
            {
                "ocid": f"ocds-assam-{i}",
                "id": str(i),
                "date": "2023-12-18",
                "buyer": [{"name": "Bodoland Territorial Council-PWD", "id": "btcpwd"}],
                "tender": [
                    {
                        "id": f"2022_BTC_{i}",
                        "title": f"Bridge Work {i}",
                        "datePublished": f"2022-{10 + i:02d}-02 10:00",  # YYYY-DD-MM CivicDataLab quirk
                        "value": [{"amount": est, "currency": "INR"}],
                        "tenderPeriod": [{"durationInDays": 21}],
                        "items": [{"id": "01", "classification": [{"description": "Civil Works"}]}],
                    }
                ],
                "awards": [
                    {
                        "id": f"aw-{i}",
                        "value": [{"amount": aw, "currency": "INR"}],
                        "suppliers": [{"id": vendor.lower(), "name": vendor}],
                        "contractPeriod": [{"startDate": "2022-08-03", "durationInDays": 90}],
                    }
                ],
                "bids": [{"id": 1, "details": [{"id": "b1"}, {"id": "b2"}]}],
            }
        )
    # Add an unawarded release — must NOT create a fake "Unspecified Vendor" monopoly in M3
    releases.append(
        {
            "ocid": "ocds-assam-unawarded",
            "buyer": [{"name": "Bodoland Territorial Council-PWD"}],
            "tender": [{"id": "2022_BTC_99", "value": [{"amount": 90_000_000.0}]}],
        }
    )

    zip_path = tmp_path / "assam_ocds_mapped_data.json.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("__MACOSX/._package.json", b"AppleDouble garbage")
        zf.writestr(
            "package.json",
            json.dumps({"publisher": {"name": "CivicDataLab Assam"}, "releases": releases}),
        )

    ds = load_dataset(str(zip_path), allow_local_files=True)
    assert ds.is_demo_sample is False
    assert ds.format == "ocds_json"
    assert len(ds.records) == 6
    assert ds.records[0]["buyer_name"] == "Bodoland Territorial Council-PWD"
    assert ds.records[0]["vendor_name"] == "ALPHA INFRA ASSAM"
    assert ds.records[0]["tender_value_inr"] == 10_000_000.0
    assert ds.records[0]["award_amount_inr"] == 14_500_000.0
    assert ds.records[0]["bid_count"] == 2

    m3 = run_engine("M3", ds.records)
    assert len(m3.leads) == 1
    assert m3.cleared_count == 1  # unawarded release cleanly cleared
    assert m3.leads[0].anomaly_metrics["hhi"] > 0.65
    assert m3.leads[0].anomaly_metrics["top_vendor_name"] == "ALPHA INFRA ASSAM"

    m4 = run_engine("M4", ds.records)
    assert len(m4.leads) == 1
    assert m4.leads[0].anomaly_metrics["escalation_kind"] == "award_vs_estimate_escalation"
    assert m4.leads[0].anomaly_metrics["escalation_pct"] == pytest.approx(45.0)
    assert m4.cleared_count == 5


def test_xlsx_ocds_tenders_awards_auto_join_and_m4_boq_unit_prices(tmp_path: Path) -> None:
    xlsx_path = tmp_path / "hp_health_ocds.xlsx"
    _create_minimal_xlsx(
        xlsx_path,
        {
            "tenders": [
                [
                    "ocid",
                    "tender/id",
                    "buyer/name",
                    "tender/value/amount",
                    "tender/numberOfTenderers",
                    "tender/tenderPeriod/startDate",
                    "tender/tenderPeriod/endDate",
                    "tender/classification/description",
                ],
                ["ocds-hp-1", "HP-1", "HP I&PH Hamirpur", 5_000_000, 1, "2020-05-01T10:00:00Z", "2020-05-10T10:00:00Z", "Water Works"],
                ["ocds-hp-2", "HP-2", "HP I&PH Hamirpur", 5_000_000, 1, "2020-05-02T10:00:00Z", "2020-05-11T10:00:00Z", "Water Works"],
                ["ocds-hp-3", "HP-3", "HP I&PH Hamirpur", 5_000_000, 2, "2020-05-03T10:00:00Z", "2020-05-25T10:00:00Z", "Water Works"],
                ["ocds-hp-4", "HP-4", "HP I&PH Hamirpur", 5_000_000, 2, "2020-05-04T10:00:00Z", "2020-05-26T10:00:00Z", "Water Works"],
            ],
            "awards": [
                ["ocid", "awards/0/id", "awards/0/suppliers/0/name", "awards/0/value/amount"],
                ["ocds-hp-1", "A1", "HIMALAYA PIPES LTD", 7_500_000],
                ["ocds-hp-2", "A2", "HIMALAYA PIPES LTD", 5_100_000],
                ["ocds-hp-3", "A3", "HIMALAYA PIPES LTD", 4_900_000],
                ["ocds-hp-4", "A4", "SHIMLA CONTRACTORS", 4_800_000],
            ],
        },
        use_inline_strings=False,
    )

    ds = load_dataset(str(xlsx_path), allow_local_files=True)
    assert ds.is_demo_sample is False
    assert ds.format == "xlsx_ocds"
    assert len(ds.records) == 4
    assert ds.records[0]["bid_window_days"] == 9
    assert ds.records[0]["vendor_name"] == "HIMALAYA PIPES LTD"
    assert ds.records[0]["jurisdiction"] == "State:Himachal Pradesh"

    m1 = run_engine("M1", ds.records)
    assert len(m1.leads) == 1
    assert m1.leads[0].anomaly_metrics["single_bid_share_pct"] == 50.0

    m3 = run_engine("M3", ds.records)
    assert len(m3.leads) == 1
    assert m3.leads[0].anomaly_metrics["hhi"] > 0.6

    m4 = run_engine("M4", ds.records)
    assert len(m4.leads) == 1
    assert m4.leads[0].anomaly_metrics["escalation_pct"] == pytest.approx(50.0)

    # Also verify classic BOQ unit-price MAD / median ratio detection in M4
    boq_records = [
        {"tender_id": f"BOQ-{i}", "buyer_name": "State Medical Corp", "item_category": "ECG Monitor", "unit": "piece", "quantity": 100, "unit_price_inr": p, "awarded_value_inr": p * 100}
        for i, p in enumerate([50_000, 51_000, 49_500, 50_500, 125_000], start=1)
    ]
    boq_res = run_engine("M4", boq_records)
    assert len(boq_res.leads) == 1
    assert boq_res.leads[0].anomaly_metrics["median_ratio"] > 2.0


def test_m2_two_point_five_lakh_lpc_ceiling() -> None:
    # Pre-10 July 2024 Central GFR 2017 Rule 155 LPC ceiling was ₹2.5 Lakh
    pre_jul2024_records = [
        {
            "po_id": f"LPC-PRE-{i}",
            "buyer_name": "Regional Agricultural Station",
            "item_category": "Lab Glassware",
            "award_date": f"2024-01-{10 + i * 7:02d}",
            "award_value_inr": val,
            "jurisdiction": "Union of India",
            "fiscal_year": "FY 2023-24",
        }
        for i, val in enumerate([232_000, 244_500, 229_000])
    ]
    pre_res = run_engine("M2", pre_jul2024_records)
    assert len(pre_res.leads) == 1
    assert pre_res.leads[0].anomaly_metrics["threshold_inr"] == 250_000.0
    assert pre_res.leads[0].anomaly_metrics["threshold_scope"] == "central_union_gfr_pre_2024_07_10"
    assert pre_res.leads[0].anomaly_metrics["statutory_applicability_verified"] is True

    # Post-10 July 2024 (MoF DoE O.M. F.1/3/2024-PPD) revised LPC ceiling to ₹5 Lakh:
    # ₹2.3L orders no longer hug the ₹5L ceiling, while ₹4.6L-₹4.9L orders do.
    post_jul2024_low = [
        {**r, "po_id": f"LPC-LOW-{i}", "award_date": f"2025-01-{10 + i * 7:02d}", "fiscal_year": "FY 2024-25"}
        for i, r in enumerate(pre_jul2024_records)
    ]
    assert run_engine("M2", post_jul2024_low).leads == []

    post_jul2024_records = [
        {
            "po_id": f"LPC-POST-{i}",
            "buyer_name": "Regional Agricultural Station",
            "item_category": "Lab Glassware",
            "award_date": f"2025-01-{10 + i * 7:02d}",
            "award_value_inr": val,
            "jurisdiction": "Union of India",
            "fiscal_year": "FY 2024-25",
        }
        for i, val in enumerate([465_000, 488_000, 452_000])
    ]
    post_res = run_engine("M2", post_jul2024_records)
    assert len(post_res.leads) == 1
    assert post_res.leads[0].anomaly_metrics["threshold_inr"] == 500_000.0
    assert post_res.leads[0].anomaly_metrics["threshold_scope"] == "central_union_gfr_post_2024_07_10"
    assert post_res.leads[0].anomaly_metrics["statutory_applicability_verified"] is True


# ---------------------------------------------------------------------------
# 4. Fiscal Engines (M5: AP Treasury, Assam OBDS inlineStr XLSX, PolicyStack, CGA HTML; M6: CAG PDFs)
# ---------------------------------------------------------------------------


def test_m5_monthly_treasury_obds_policystack_and_cga_html(tmp_path: Path) -> None:
    # (a) Monthly AP Treasury rollup (Indian FY Apr..Mar; Q4=Jan..Mar)
    ap_rows = [
        {"District": "KRISHNA", "Year": "2016", "Month": m, "Major Head": "2202", "Major Head Description": "General Education", "Amount": amt}
        for m, amt in [
            ("APRIL", 5_000_000),
            ("MAY", 5_000_000),
            ("JUNE", 5_000_000),
            ("JULY", 5_000_000),
            ("AUG", 5_000_000),
            ("OCT", 5_000_000),
            ("DEC", 5_000_000),
        ]
    ] + [
        {"District": "KRISHNA", "Year": "2017", "Month": "JAN", "Major Head": "2202", "Major Head Description": "General Education", "Amount": 10_000_000},
        {"District": "KRISHNA", "Year": "2017", "Month": "FEB", "Major Head": "2202", "Major Head Description": "General Education", "Amount": 15_000_000},
        {"District": "KRISHNA", "Year": "2017", "Month": "MAR", "Major Head": "2202", "Major Head Description": "General Education", "Amount": 40_000_000},
    ]
    ap_res = run_engine("M5", ap_rows)
    assert len(ap_res.leads) == 1
    assert ap_res.leads[0].fiscal_year == "FY 2016-17"
    assert ap_res.leads[0].anomaly_metrics["q4_share_pct"] == pytest.approx(65.0)
    assert ap_res.leads[0].anomaly_metrics["march_share_pct"] == pytest.approx(40.0)

    # (b) Assam OBDS inlineStr XLSX reader:
    # Unreconciled rows missing explicit unit/agency/major head MUST be quarantined as unevaluable!
    obds_unverified_path = tmp_path / "assam_expenditure_unverified_22_23.xlsx"
    _create_minimal_xlsx(
        obds_unverified_path,
        {
            "Sheet1": [
                [
                    "state_ut_name_eng",
                    "demand_grant_no_en",
                    "demand_grant_desc_en",
                    "budget_financial_year",
                    "budget_amount_unit",
                    "budget_estimates_previous_financial_year",
                    "revised_estimates_previous_financial_year",
                    "actuals_last_audited_year",
                ],
                ["Assam", "65", "Tourism", "2022-23", "", 10000, 28680, 9500],
                ["Assam", "65", "Tourism", "2022-23", "", 5000, 14340, 4800],
                ["Assam", "1", "State Legislature", "2022-23", "", 10000, 10500, 9800],
            ]
        },
        use_inline_strings=True,
    )
    obds_unv_ds = load_dataset(str(obds_unverified_path), allow_local_files=True)
    assert obds_unv_ds.format == "xlsx"
    assert obds_unv_ds.is_demo_sample is False
    obds_unv_res = run_engine("M5", obds_unv_ds.records)
    assert obds_unv_res.leads == []
    assert obds_unv_res.cleared_count == 0
    assert obds_unv_res.unevaluable_count == 3

    # Properly reconciled Assam OBDS rows with matched agency, major head, and explicit unit
    obds_path = tmp_path / "assam_expenditure_reconciled_22_23.xlsx"
    _create_minimal_xlsx(
        obds_path,
        {
            "Sheet1": [
                [
                    "state_ut_name_eng",
                    "demand_grant_no_en",
                    "demand_grant_desc_en",
                    "dept_name_en",
                    "major_head_code",
                    "major_head_desc_en",
                    "budget_financial_year",
                    "budget_amount_unit",
                    "budget_estimates_previous_financial_year",
                    "revised_estimates_previous_financial_year",
                    "actuals_last_audited_year",
                ],
                ["Assam", "65", "Tourism", "Tourism Department", "3452", "Tourism", "2022-23", "Lakhs", 10000, 16000, 9500],
                ["Assam", "65", "Tourism", "Tourism Department", "3452", "Tourism", "2022-23", "Lakhs", 5000, 8000, 4800],
                ["Assam", "1", "State Legislature", "State Legislature Secretariat", "2011", "State Legislature", "2022-23", "Lakhs", 10000, 10500, 9800],
            ]
        },
        use_inline_strings=True,
    )
    obds_ds = load_dataset(str(obds_path), allow_local_files=True)
    obds_res = run_engine("M5", obds_ds.records)
    assert len(obds_res.leads) == 1
    assert obds_res.cleared_count == 1
    assert obds_res.unevaluable_count == 0
    assert "Tourism" in obds_res.leads[0].entity_name
    assert obds_res.leads[0].fiscal_year == "FY 2021-22"
    assert obds_res.leads[0].anomaly_metrics["re_vs_be_shift_pct"] == pytest.approx(60.0)
    # T-2 actuals_last_audited_year is NEVER compared against T-1 BE/RE
    assert obds_res.leads[0].anomaly_metrics["actual_inr"] is None
    assert obds_res.leads[0].anomaly_metrics["be_variance_pct"] is None
    assert obds_res.leads[0].anomaly_metrics["prior_audited_fy"] == "FY 2020-21"

    # (c) PolicyStack Union Flagship Schemes
    ps_rows = [
        {"scheme_id": "mgnrega", "fiscal_year": "2020-21", "budget_allocated_cr": "61500", "budget_revised_cr": "111500", "budget_spent_cr": "98000"},
        {"scheme_id": "pm_kisan", "fiscal_year": "2023-24", "budget_allocated_cr": "60000", "budget_revised_cr": "60000", "budget_spent_cr": "59500"},
    ]
    ps_res = run_engine("M5", ps_rows)
    assert len(ps_res.leads) == 1
    assert ps_res.cleared_count == 1
    assert "MGNREGA" in ps_res.leads[0].entity_name

    # (d) Live CGA Monthly Accounts HTML table
    cga_html = tmp_path / "cga_12_2024_2025.html"
    cga_html.write_text(
        """<html><head><title>Monthly Report : CGA</title></head><body>
        <table>
          <tr><td></td><td></td><td></td><td>Budget Estimates 2024-2025*</td><td>Actuals@ upto December 2024</td><td>% of Actuals to Budget Estimates</td></tr>
          <tr><td>8</td><td>Revenue Expenditure</td><td></td><td>3709401</td><td>2546757</td><td>68.7%</td><td>(68.0%)</td></tr>
          <tr><td>10</td><td>Capital Expenditure</td><td></td><td>1111111</td><td>685337</td><td>61.7 %</td><td>(67.3%)</td></tr>
          <tr><td>1</td><td>External Financing</td><td></td><td>15952.29</td><td>19195.72</td><td>120%</td><td>(130%)</td></tr>
        </table></body></html>""",
        encoding="utf-8",
    )
    cga_ds = load_dataset(str(cga_html), allow_local_files=True)
    assert cga_ds.format == "html_table"
    assert len(cga_ds.records) == 3
    cga_res = run_engine("M5", cga_ds.records)
    flagged_heads = {l.entity_name for l in cga_res.leads}
    assert "Capital Expenditure" in flagged_heads
    assert "External Financing" in flagged_heads
    assert "Revenue Expenditure" not in flagged_heads


def test_pdf_loader_and_m6_both_legacy_para_and_real_cag_numbered_sections(tmp_path: Path) -> None:
    # 1. Legacy "Para X.Y" format
    legacy_pdf = tmp_path / "cag_legacy.pdf"
    _create_minimal_pdf(
        legacy_pdf,
        [
            [
                "Comptroller and Auditor General of India - Report No. 12 of 2025",
                "Para 4.3 Unfruitful expenditure on regional cold storage project",
                "An expenditure of Rs. 42.75 crore remained unfruitful and ATN is unresolved",
                "despite repeat audit observations from the earlier audit cycle.",
                "Para 4.4 Minor procedural note on inventory register",
                "An amount of Rs. 1.20 crore was reconciled and settled by the department.",
            ]
        ],
    )
    ds_leg = load_dataset(str(legacy_pdf), allow_local_files=True)
    assert ds_leg.format == "pdf"
    assert ds_leg.is_demo_sample is False
    res_leg = run_engine("M6", ds_leg.records)
    assert len(res_leg.leads) == 1
    assert res_leg.cleared_count == 1
    assert res_leg.leads[0].anomaly_metrics["para_id"] == "Para 4.3"
    assert res_leg.leads[0].anomaly_metrics["quantified_impact_crore"] == pytest.approx(42.75)

    # 2. Real CAG numbered section headings with TOC skip + neutral handling of third-party audit words
    real_cag_pdf = tmp_path / "cag_39_2025_commercial.pdf"
    _create_minimal_pdf(
        real_cag_pdf,
        [
            [
                "Report No. 39 of 2025 (Union Government - Commercial)",
                "Table of Contents",
                "2.1 Infructuous expenditure on Polyol project ........ 14",
                "2.2 Avoidable expenditure on coal transport ........ 28",
                "1.1 Introduction",
                "This report covers commercial public sector enterprises for 2023-24.",
            ],
            [
                "2.1 Infructuous expenditure of Rs. 300.15 crore due to discontinuation of Polyol project",
                "Audit observed that Kochi Refinery spent Rs. 300.15 crore on a project later halted.",
                "Despite internal anti-fraud committee review, the ATN reply was awaited.",
                "2.2 Avoidable expenditure in coal transportation logistics",
                "Central Coalfields Limited incurred avoidable expenditure of Rs. 21.22 crore.",
                "3.50 Cu.m/MWh water consumption norm was benchmarked at Rs. 5.00 crore.",
                "2.3 Unquantified administrative observation",
                "Routine procedural documentation update noted without monetary figure.",
            ],
        ],
    )
    ds_real = load_dataset(str(real_cag_pdf), allow_local_files=True)
    res_real = run_engine("M6", ds_real.records)
    para_ids = [l.anomaly_metrics["para_id"] for l in res_real.leads]
    assert "Para 2.1" in para_ids
    assert "Para 2.2" in para_ids
    assert "Para 3.50" not in para_ids
    assert "Para 2.3" not in para_ids
    top = res_real.leads[0]
    assert top.anomaly_metrics["para_id"] == "Para 2.1"
    assert top.anomaly_metrics["quantified_impact_crore"] == pytest.approx(300.15)
    assert top.citations[0]["page"] == 2
    assert lint_neutrality(top.title, top.summary, top.benign_explanations).passed


# ---------------------------------------------------------------------------
# 5. Welfare Schemes Engine (M7: PMGSY, semicolon MGNREGA & MPLADS)
# ---------------------------------------------------------------------------


def test_m7_real_pmgsy_semicolon_mgnrega_and_mplads_schemas(tmp_path: Path) -> None:
    # PMGSY raw OMMAS columns (Cr x 1e7)
    pmgsy_csv = tmp_path / "pmgsy.csv"
    pmgsy_csv.write_text(
        "\n".join(
            [
                "STATE_NAME,DISTRICT_NAME,PMGSY_SCHEME,LENGTH_OF_ROAD_WORK_SANCTIONED,COST_OF_WORKS_SANCTIONED,LENGTH_OF_ROAD_WORK_COMPLETED,EXPENDITURE_OCCURED,",
                "Bihar,Araria,PMGSY-III,120.0,100.0,55.0,94.0,",
                "Kerala,Idukki,PMGSY-I,80.0,50.0,78.0,48.0,",
            ]
        ),
        encoding="utf-8",
    )
    pmgsy_ds = load_dataset(str(pmgsy_csv), allow_local_files=True)
    pmgsy_res = run_engine("M7", pmgsy_ds.records)
    assert len(pmgsy_res.leads) == 1
    assert pmgsy_res.cleared_count == 1
    assert pmgsy_res.leads[0].anomaly_metrics["scheme"] == "PMGSY"
    assert pmgsy_res.leads[0].anomaly_metrics["sanctioned_cost_inr"] == pytest.approx(1_000_000_000.0)

    # MGNREGA semicolon-delimited CSV (Lakhs x 1e5)
    nrega_csv = tmp_path / "nrega.csv"
    nrega_csv.write_text(
        "\n".join(
            [
                "state_name;district_name;Total Exp(Rs. in Lakhs.);Wages(Rs. In Lakhs);Material and skilled Wages(Rs. In Lakhs)",
                "RAJASTHAN;BARMER;1000.0;450.0;550.0",
                "TAMIL NADU;MADURAI;800.0;600.0;200.0",
            ]
        ),
        encoding="utf-8",
    )
    nrega_ds = load_dataset(str(nrega_csv), allow_local_files=True)
    nrega_res = run_engine("M7", nrega_ds.records)
    assert len(nrega_res.leads) == 1
    assert nrega_res.cleared_count == 1
    assert nrega_res.leads[0].anomaly_metrics["material_share_pct"] == pytest.approx(55.0)
    assert nrega_res.leads[0].anomaly_metrics["wage_spend_inr"] == pytest.approx(45_000_000.0)

    # MPLADS semicolon-delimited CSV without release/geotag columns
    mplads_csv = tmp_path / "mplads.csv"
    mplads_csv.write_text(
        "\n".join(
            [
                "MP NAME;WORK;STATE;CONSTITUENCY;IDA;RECOMMENDED DATE;ALLOCATION AMOUNT;STATUS",
                "MP Alpha;WS/MP1/2023-24/101 - Community Hall;Meghalaya;TURA;DC WEST GARO HILLS_IDA;2023-06-01;2500000;Ongoing",
                "MP Beta;WS/MP2/2023-24/102 - Street Lights;Bihar;PATNA;DM PATNA_IDA;2023-06-01;500000;Unsanctioned",
                "MP Gamma;WS/MP3/2023-24/103 - School Room;Kerala;KOCHI;DC ERNAKULAM_IDA;2023-06-01;1000000;Completed",
            ]
        ),
        encoding="utf-8",
    )
    mplads_ds = load_dataset(str(mplads_csv), allow_local_files=True)
    mplads_res = run_engine("M7", mplads_ds.records, context={"as_of_date": "2025-12-01"})
    assert len(mplads_res.leads) == 1
    assert mplads_res.cleared_count == 2
    assert mplads_res.leads[0].anomaly_metrics["scheme"] == "MPLADS"
    assert mplads_res.leads[0].monetary_exposure_inr == pytest.approx(2_500_000.0)


# ---------------------------------------------------------------------------
# 6. Corporate-Electoral Disclosure Engine (M8: Classic + SC Electoral Bonds x MCA CSR)
# ---------------------------------------------------------------------------


def test_m8_electoral_bonds_x_mca_csr_join_and_classic_flat_shared_din(tmp_path: Path) -> None:
    eb_csv = tmp_path / "eb_final.csv"
    eb_csv.write_text(
        "\n".join(
            [
                "date_of_encashment,political_party_name,prefix,bond_number,amount,date_of_purchase,purchaser_name",
                "12/Apr/2023,NATIONAL PARTY A,OC,1001,\"1,00,00,000\",05/Apr/2023,VEDANTA LIMITED",
                "15/Oct/2023,NATIONAL PARTY B,OC,1002,\"2,00,00,000\",10/Oct/2023,VEDANTA LTD",
                "18/Nov/2023,NATIONAL PARTY A,OC,1003,\"50,00,000\",11/Nov/2023,UNMATCHED SMALL FIRM PVT LTD",
            ]
        ),
        encoding="utf-8",
    )
    csr_csv = tmp_path / "mca_csr_top100.csv"
    csr_csv.write_text(
        "\n".join(
            [
                "company_name,cin,csr_total,csr_total_rank",
                "VEDANTA LTD.,L13209MH1965PLC291394,14.30,22",
                "OTHER CORP LTD.,L99999MH1990PLC000001,25.00,10",
            ]
        ),
        encoding="utf-8",
    )

    ds = load_dataset(str(eb_csv), context={"secondary_path": str(csr_csv)}, allow_local_files=True)
    assert ds.is_demo_sample is False
    m8_eb = run_engine("M8", ds.records)
    assert len(m8_eb.leads) == 1
    assert m8_eb.cleared_count == 1
    lead = m8_eb.leads[0]
    assert lead.anomaly_metrics["cin"] is None
    assert lead.anomaly_metrics["bond_source_cin"] is None
    assert lead.anomaly_metrics["matched_csr_cin"] == "L13209MH1965PLC291394"
    assert lead.anomaly_metrics["join_basis"] == "normalized_name"
    assert lead.anomaly_metrics["exact_identifier_verified"] is False
    assert lead.anomaly_metrics["disclosed_bond_value_inr"] == pytest.approx(30_000_000.0)
    assert lead.anomaly_metrics["electoral_disclosure_inr"] == pytest.approx(30_000_000.0)
    assert lead.anomaly_metrics["amount_type"] == "disclosed_bond_value"
    assert lead.anomaly_metrics["csr_total_crore"] == pytest.approx(14.3)
    assert lead.anomaly_metrics["disclosure_only"] is True
    assert lint_neutrality(lead.title, lead.summary, lead.benign_explanations).passed
    assert generate_rti_draft(lead)["word_count"] <= 500

    # Classic M8: flat rows sharing same tender_id + overlapping DIN
    flat_rows = [
        {
            "tender_id": "PROC-FLAT-99",
            "buyer_name": "Municipal Corp",
            "vendor_name": "Alpha Works Pvt Ltd",
            "cin": "U45200MH2018PTC111111",
            "incorporation_date": "2024-11-01",
            "award_date": "2025-01-15",
            "award_value_inr": 15_000_000,
            "director_dins": "08812345,08812346",
        },
        {
            "tender_id": "PROC-FLAT-99",
            "buyer_name": "Municipal Corp",
            "vendor_name": "Beta Works Pvt Ltd",
            "cin": "U45200MH2019PTC222222",
            "incorporation_date": "2019-01-01",
            "award_date": "2025-01-15",
            "award_value_inr": 0,
            "director_dins": "08812345,08899999",
        },
    ]
    m8_flat = run_engine("M8", flat_rows)
    assert len(m8_flat.leads) == 1
    assert "08812345" in m8_flat.leads[0].anomaly_metrics["shared_dins"]
    assert m8_flat.leads[0].anomaly_metrics["company_age_days"] < 180


# ---------------------------------------------------------------------------
# 7. HTTP Loader: User-Agent, Disk Cache, CKAN Envelope, and CAPTCHA/403 Guard
# ---------------------------------------------------------------------------


def test_http_loader_cache_user_agent_and_captcha_refusal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("AIWATCHDOGS_CACHE_DIR", str(tmp_path / "http_cache"))
    captured_headers: dict[str, Any] = {}
    call_count = {"n": 0}

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
                    "ministry_name": "Ministry of Public Infrastructure",
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
        call_count["n"] += 1
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

    url = "https://openbudgetsindia.org/api/3/action/datastore_search?resource_id=fiscal-1"
    # Default mode: opt-in raw disk cache is OFF (zero raw bytes persisted to disk)
    ds_nocache = load_dataset(url)
    assert ds_nocache.format == "ckan_json"
    assert ds_nocache.is_demo_sample is False
    assert len(ds_nocache.records) == 1
    assert captured_headers.get("user-agent") == USER_AGENT
    assert call_count["n"] == 1
    assert not (tmp_path / "http_cache").exists()

    # Opt-in cache write + second load hits disk cache with SHA-256 revalidation
    ds1 = load_dataset(url, use_cache=True)
    assert call_count["n"] == 2
    ds2 = load_dataset(url, use_cache=True)
    assert ds2.sha256 == ds1.sha256
    assert call_count["n"] == 2

    m5_res = run_engine("M5", ds1.records)
    assert len(m5_res.leads) == 1

    with pytest.raises(PermissionError, match="CAPTCHA"):
        load_dataset("https://eprocure.gov.in/captcha-gate")

    with pytest.raises(PermissionError, match="403"):
        load_dataset("https://mca.gov.in/forbidden-endpoint")


def test_p8_registered_in_methodology_catalog_and_fingerprint() -> None:
    assert "P8" in METHODOLOGY_CATALOG
    assert METHODOLOGY_CATALOG["P8"]["module"] == "aiwatchdogs.engines.sovereign_lies"
    assert METHODOLOGY_CATALOG["P8"]["pillar"] == "scheme"
    h1 = get_engine_script_hash("P8", as_of_date="2026-11-28")
    h2 = get_engine_script_hash("P8", as_of_date="2026-11-28")
    assert len(h1) == 64 and h1 == h2


def test_p9_registered_in_methodology_catalog_and_fingerprint() -> None:
    assert "P9" in METHODOLOGY_CATALOG
    assert METHODOLOGY_CATALOG["P9"]["module"] == "aiwatchdogs.engines.auditing_government_exams"
    assert METHODOLOGY_CATALOG["P9"]["pillar"] == "audit"
    h1 = get_engine_script_hash("P9", as_of_date="2026-11-28")
    h2 = get_engine_script_hash("P9", as_of_date="2026-11-28")
    assert len(h1) == 64 and h1 == h2

