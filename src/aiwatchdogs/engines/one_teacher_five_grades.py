"""OT1: One Teacher, Five Grades — single-teacher & primary-staffing concentration audit on UDISE+ reports.

Reads pinned official Ministry of Education (Department of School Education & Literacy)
UDISE+ annual report PDFs page by page, extracts a State/UT's exact verified figures across:
- Table 2.2: Total schools, enrolments, teachers, PTR, and Schools with Single Teachers & Enrolments
- Table 3.3: Government schools by category (incl. Primary I-V schools)
- Table 4.3: Government teachers by category (incl. Primary I-V teachers)
- Table 5.2: Government school enrolment by level (incl. Primary classes)

Flags States/UTs where single-teacher schools are concentrated (>= 10% of schools with >= 100
single-teacher schools), where single-teacher enrolments exceed the RTE Act 2009 Schedule 30:1
norm (> 30 students per single-teacher school with >= 500 single-teacher schools), or where
Government Primary (Classes I-V) schools average fewer than 2.0 teachers per school (below the
RTE Act 2009 two-teacher statutory minimum). Cleared States/UTs are emitted as clean records with
full exact metrics so both concentrations and well-staffed states appear transparently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from aiwatchdogs.core.models import AuditLeadCandidate
from aiwatchdogs.core.safety import validate_safe_http_url

ONE_TEACHER_FIVE_GRADES_PROJECT_SLUG = "one-teacher-five-grades"
ONE_TEACHER_FIVE_GRADES_METHODOLOGY = "OT1"

# RTE Act 2009 Schedule & UDISE+ staffing thresholds
HIGH_CONCENTRATION_SHARE_PCT = 10.0
MIN_SINGLE_TEACHER_SCHOOLS_FOR_SHARE = 100
SEVERE_OVERCROWDING_STUDENTS_PER_TEACHER = 30.0
MIN_SINGLE_TEACHER_SCHOOLS_FOR_PTR = 500
GOV_PRIMARY_MIN_AVG_TEACHERS = 2.0
MIN_GOV_PRIMARY_SCHOOLS = 100

SIGNAL_CATEGORIES: tuple[str, ...] = (
    "severe_concentration_and_overcrowding",
    "high_single_teacher_concentration",
    "severe_single_teacher_overcrowding",
    "gov_primary_understaffed",
)

_YEAR_RE = re.compile(r"^20\d{2}-\d{2}$")
_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,48}$")
_NON_ALNUM_SPACE_RE = re.compile(r"[^a-z0-9\s]+")
_MULTI_SPACE_RE = re.compile(r"\s+")
_HEADER_OR_FOOTER_RE = re.compile(
    r"^(?:\d{1,3}|Table\s+\d+\.\d+.*|India/State.*|Total\s+number.*|Number\s+of\s+.*|"
    r"Enrolment\s+of\s+.*|Source:.*|\(1\).*)$",
    re.IGNORECASE,
)

# Canonical state/UT name normalization so historical splits/reordering (e.g., DNH & DD) match reliably.
_CANON_ALIASES: dict[str, str] = {
    "daman and diu and dadra and nagar haveli": "dadra-nagar-haveli-daman-diu",
    "dadra and nagar haveli and daman and diu": "dadra-nagar-haveli-daman-diu",
    "daman and diu": "dadra-nagar-haveli-daman-diu",
    "dadra and nagar haveli": "dadra-nagar-haveli-daman-diu",
    "andaman and nicobar islands": "andaman-nicobar-islands",
    "jammu and kashmir": "jammu-kashmir",
    "nct of delhi": "delhi",
}


def canonical_state_key(name: str) -> str:
    """Normalize a State/UT name to a canonical hyphenated key independent of '&' vs 'and'."""
    cleaned = _MULTI_SPACE_RE.sub(" ", str(name or "").replace("&", " and ")).strip().lower()
    cleaned = _NON_ALNUM_SPACE_RE.sub(" ", cleaned)
    cleaned = _MULTI_SPACE_RE.sub(" ", cleaned).strip()
    if cleaned in _CANON_ALIASES:
        return _CANON_ALIASES[cleaned]
    return cleaned.replace(" ", "-")


@dataclass(frozen=True)
class UdiseStateMetrics:
    academic_year: str
    state_name: str
    state_key: str
    table_2_2_page: int
    table_3_3_page: int
    table_4_3_page: int
    table_5_2_page: int
    total_schools: int
    total_enrolments: int
    total_teachers: int
    overall_ptr: int
    avg_teachers_per_school: int
    avg_enrolments_per_school: int
    zero_enrolment_schools: int
    teachers_in_zero_enrolment_schools: int
    single_teacher_schools: int
    single_teacher_enrolments: int
    gov_total_schools: int
    gov_primary_1_5_schools: int
    gov_upper_primary_1_8_schools: int
    gov_total_teachers: int
    gov_primary_1_5_teachers: int
    gov_upper_primary_1_8_teachers: int
    gov_total_enrolments: int
    gov_primary_enrolments: int

    @property
    def single_teacher_share_pct(self) -> float:
        if self.total_schools <= 0:
            return 0.0
        return round(100.0 * self.single_teacher_schools / self.total_schools, 2)

    @property
    def students_per_single_teacher(self) -> float:
        if self.single_teacher_schools <= 0:
            return 0.0
        return round(self.single_teacher_enrolments / self.single_teacher_schools, 2)

    @property
    def gov_school_share_pct(self) -> float:
        if self.total_schools <= 0:
            return 0.0
        return round(100.0 * self.gov_total_schools / self.total_schools, 2)

    @property
    def gov_primary_teachers_per_school(self) -> float | None:
        if self.gov_primary_1_5_schools <= 0:
            return None
        return round(self.gov_primary_1_5_teachers / self.gov_primary_1_5_schools, 2)

    @property
    def gov_primary_ptr(self) -> float | None:
        if self.gov_primary_1_5_teachers <= 0:
            return None
        return round(self.gov_primary_enrolments / self.gov_primary_1_5_teachers, 2)

    @property
    def single_teacher_vs_gov_schools_pct(self) -> float:
        if self.gov_total_schools <= 0:
            return 0.0
        return round(100.0 * self.single_teacher_schools / self.gov_total_schools, 2)


def _merge_wrapped_table_lines(text: str, min_cols: int) -> list[str]:
    """Collapse multi-line wrapped State/UT names in extracted UDISE+ PDF table pages."""
    raw_lines = [_MULTI_SPACE_RE.sub(" ", ln).strip() for ln in str(text or "").splitlines()]
    lines = [ln for ln in raw_lines if ln]
    merged: list[str] = []
    trailing_nums_re = re.compile(rf"(?:\s+\d+){{{min_cols},}}$")
    i = 0
    while i < len(lines):
        cur = lines[i]
        is_header = bool(_HEADER_OR_FOOTER_RE.match(cur))
        has_nums = bool(re.search(r"\d", cur))
        if (
            not is_header
            and not has_nums
            and i + 1 < len(lines)
            and not _HEADER_OR_FOOTER_RE.match(lines[i + 1])
            and trailing_nums_re.search(lines[i + 1])
        ):
            merged.append(f"{cur} {lines[i + 1]}")
            i += 2
            continue
        merged.append(cur)
        i += 1
    return merged


def extract_udise_table_rows(page_text: str, *, expected_cols: int) -> dict[str, tuple[str, list[int]]]:
    """Extract ``{canonical_state_key: (printed_state_name, ints)}`` from a UDISE+ table page."""
    pattern = re.compile(rf"^(India|[A-Z][A-Za-z&.,'()/\-\s]+?)\s+((?:\d+\s+){{{expected_cols - 1}}}\d+)$")
    out: dict[str, tuple[str, list[int]]] = {}
    for line in _merge_wrapped_table_lines(page_text, expected_cols):
        if _HEADER_OR_FOOTER_RE.match(line):
            continue
        m = pattern.match(line)
        if not m:
            continue
        name = _MULTI_SPACE_RE.sub(" ", m.group(1)).strip()
        vals = [int(tok) for tok in m.group(2).split()]
        if len(vals) == expected_cols:
            out[canonical_state_key(name)] = (name, vals)
    return out


def _find_table_page(
    pages: dict[int, str],
    *,
    preferred_page: int,
    table_prefix: str,
    required_phrase: str | None = None,
) -> tuple[int | None, str]:
    """Locate a specific UDISE+ table page, preferring ``preferred_page`` and verifying header."""
    pref_text = pages.get(preferred_page, "")
    pref_norm = _MULTI_SPACE_RE.sub(" ", pref_text).lower()
    if table_prefix.lower() in pref_norm and (
        required_phrase is None or required_phrase.lower() in pref_norm
    ):
        return preferred_page, pref_text
    for pg in sorted(pages):
        txt = pages[pg]
        norm = _MULTI_SPACE_RE.sub(" ", txt).lower()
        if table_prefix.lower() in norm and (
            required_phrase is None or required_phrase.lower() in norm
        ):
            return pg, txt
    return None, ""


def _pages_map(records: list[dict[str, Any]]) -> dict[int, str]:
    out: dict[int, str] = {}
    for rec in records:
        if not isinstance(rec, dict):
            continue
        raw_pg = rec.get("page")
        if isinstance(raw_pg, bool):
            continue
        try:
            pg = int(raw_pg)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        raw_txt = rec.get("raw_text")
        if raw_txt is None:
            raw_txt = rec.get("text")
        if pg > 0 and isinstance(raw_txt, str):
            out[pg] = raw_txt
    return out


def extract_state_metrics_from_pages(
    pages: dict[int, str],
    *,
    academic_year: str,
    state_name: str,
    table_pages: dict[str, int] | None = None,
    state_key: str | None = None,
) -> UdiseStateMetrics | None:
    """Extract and cross-check all 4 UDISE+ tables for one State/UT."""
    tpages = table_pages or {}
    key = state_key or canonical_state_key(state_name)

    p22, t22 = _find_table_page(
        pages,
        preferred_page=int(tpages.get("table_2_2") or 32),
        table_prefix="Table 2.2",
        required_phrase="Single Teacher",
    )
    p33, t33 = _find_table_page(
        pages,
        preferred_page=int(tpages.get("table_3_3") or 42),
        table_prefix="Table 3.3",
        required_phrase="Government",
    )
    p43, t43 = _find_table_page(
        pages,
        preferred_page=int(tpages.get("table_4_3") or 54),
        table_prefix="Table 4.3",
        required_phrase="Government",
    )
    p52, t52 = _find_table_page(
        pages,
        preferred_page=int(tpages.get("table_5_2") or 86),
        table_prefix="Table 5.2",
        required_phrase="Government",
    )
    if p22 is None or p33 is None or p43 is None or p52 is None:
        return None

    rows22 = extract_udise_table_rows(t22, expected_cols=10)
    rows33 = extract_udise_table_rows(t33, expected_cols=11)
    rows43 = extract_udise_table_rows(t43, expected_cols=11)
    rows52 = extract_udise_table_rows(t52, expected_cols=7)

    r22 = rows22.get(key) or rows22.get(canonical_state_key(state_name))
    r33 = rows33.get(key) or rows33.get(canonical_state_key(state_name))
    r43 = rows43.get(key) or rows43.get(canonical_state_key(state_name))
    r52 = rows52.get(key) or rows52.get(canonical_state_key(state_name))
    if r22 is None or r33 is None or r43 is None or r52 is None:
        return None

    printed_name, v22 = r22
    _, v33 = r33
    _, v43 = r43
    _, v52 = r52

    return UdiseStateMetrics(
        academic_year=academic_year,
        state_name=printed_name if key == "india" else state_name,
        state_key=key,
        table_2_2_page=p22,
        table_3_3_page=p33,
        table_4_3_page=p43,
        table_5_2_page=p52,
        total_schools=v22[0],
        total_enrolments=v22[1],
        total_teachers=v22[2],
        overall_ptr=v22[3],
        avg_teachers_per_school=v22[4],
        avg_enrolments_per_school=v22[5],
        zero_enrolment_schools=v22[6],
        teachers_in_zero_enrolment_schools=v22[7],
        single_teacher_schools=v22[8],
        single_teacher_enrolments=v22[9],
        gov_total_schools=v33[0],
        gov_primary_1_5_schools=v33[1],
        gov_upper_primary_1_8_schools=v33[2],
        gov_total_teachers=v43[0],
        gov_primary_1_5_teachers=v43[1],
        gov_upper_primary_1_8_teachers=v43[2],
        gov_total_enrolments=v52[0],
        gov_primary_enrolments=v52[2],
    )


def evaluate_state_metrics(m: UdiseStateMetrics) -> tuple[bool, list[str], str, str, str]:
    """Evaluate OT1 concentration and staffing thresholds for one State/UT."""
    high_share = (
        m.single_teacher_schools >= MIN_SINGLE_TEACHER_SCHOOLS_FOR_SHARE
        and m.single_teacher_share_pct >= HIGH_CONCENTRATION_SHARE_PCT
    )
    severe_overcrowding = (
        m.single_teacher_schools >= MIN_SINGLE_TEACHER_SCHOOLS_FOR_PTR
        and m.students_per_single_teacher > SEVERE_OVERCROWDING_STUDENTS_PER_TEACHER
    )
    gov_avg = m.gov_primary_teachers_per_school
    gov_primary_understaffed = (
        m.gov_primary_1_5_schools >= MIN_GOV_PRIMARY_SCHOOLS
        and gov_avg is not None
        and gov_avg < GOV_PRIMARY_MIN_AVG_TEACHERS
    )

    triggered: list[str] = []
    if high_share:
        triggered.append("single_teacher_share_ge_10pct")
    if severe_overcrowding:
        triggered.append("students_per_single_teacher_gt_30")
    if gov_primary_understaffed:
        triggered.append("gov_primary_avg_teachers_lt_2")

    if not triggered:
        return False, [], "", "", "low"

    if high_share and (severe_overcrowding or gov_primary_understaffed):
        signal_category = "severe_concentration_and_overcrowding"
        rule_triggered = "OT1-CONCENTRATION-OVERCROWDING"
    elif high_share:
        signal_category = "high_single_teacher_concentration"
        rule_triggered = "OT1-HIGH-CONCENTRATION"
    elif severe_overcrowding:
        signal_category = "severe_single_teacher_overcrowding"
        rule_triggered = "OT1-SEVERE-OVERCROWDING"
    else:
        signal_category = "gov_primary_understaffed"
        rule_triggered = "OT1-GOV-PRIMARY-UNDERSTAFFED"

    if (
        m.single_teacher_share_pct >= 15.0
        or (m.single_teacher_schools >= MIN_SINGLE_TEACHER_SCHOOLS_FOR_PTR and m.students_per_single_teacher >= 45.0)
        or (gov_primary_understaffed and gov_avg is not None and gov_avg < 1.85)
    ):
        severity = "critical"
    elif high_share or (severe_overcrowding and m.students_per_single_teacher >= 35.0):
        severity = "high"
    else:
        severity = "medium"

    return True, triggered, signal_category, rule_triggered, severity


def run_ot1_single_teacher_schools(
    records: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
) -> tuple[list[AuditLeadCandidate], int, float, str, dict[str, Any]]:
    """Run deterministic OT1 single-teacher & primary-staffing concentration check on UDISE+ pages."""
    ctx = context or {}
    spec = ctx.get("one_teacher_five_grades") if isinstance(ctx.get("one_teacher_five_grades"), dict) else {}
    academic_year = str(spec.get("academic_year") or "").strip()
    state_name = str(spec.get("state_name") or "").strip()
    state_key = str(spec.get("state_key") or canonical_state_key(state_name)).strip()
    is_ut = bool(spec.get("is_ut"))
    doc_title = str(spec.get("source_document_title") or f"UDISE+ {academic_year} Booklet (Existing Structure)").strip()
    table_pages = spec.get("table_pages") if isinstance(spec.get("table_pages"), dict) else {}
    retrieval_url = str(ctx.get("retrieval_url") or ctx.get("dataset_url") or "").strip()
    landing_url = str(ctx.get("source_landing_page") or retrieval_url or "").strip()
    expected_sha = str(ctx.get("expected_sha256") or "").strip()
    official_publisher = str(
        ctx.get("official_publisher")
        or "Department of School Education & Literacy, Ministry of Education, Government of India"
    ).strip()

    meta: dict[str, Any] = {
        "as_of_date": ctx.get("as_of_date"),
        "amount_type": "school_staffing_and_enrolment",
        "unevaluable_count": 0,
    }

    pages = _pages_map(records)
    if not pages or not academic_year or not state_name:
        meta["unevaluable_count"] = 1
        return [], 0, 0.0, "OT1 read 0 UDISE+ rows: missing PDF pages, academic year, or state specification.", meta

    metrics = extract_state_metrics_from_pages(
        pages,
        academic_year=academic_year,
        state_name=state_name,
        table_pages=table_pages,
        state_key=state_key,
    )
    if metrics is None:
        meta["unevaluable_count"] = 1
        return (
            [],
            0,
            0.0,
            f"OT1 could not parse Tables 2.2, 3.3, 4.3, and 5.2 for {state_name} in UDISE+ {academic_year}.",
            meta,
        )

    flagged, triggered, signal_category, rule_triggered, severity = evaluate_state_metrics(metrics)
    source_locator = (
        f"UDISE+ {academic_year} Table 2.2 (p.{metrics.table_2_2_page}), "
        f"Table 3.3 (p.{metrics.table_3_3_page}), "
        f"Table 4.3 (p.{metrics.table_4_3_page}), "
        f"Table 5.2 (p.{metrics.table_5_2_page})"
    )
    gov_avg_str = (
        f"{metrics.gov_primary_teachers_per_school:.2f}"
        if metrics.gov_primary_teachers_per_school is not None
        else "n/a"
    )

    if not flagged:
        return (
            [],
            1,
            0.0,
            f"OT1 cleared {state_name} ({academic_year}): {metrics.single_teacher_schools:,} single-teacher schools "
            f"({metrics.single_teacher_share_pct:.2f}% of {metrics.total_schools:,} schools; "
            f"{metrics.single_teacher_enrolments:,} enrolled, {metrics.students_per_single_teacher:.1f} students/school; "
            f"Government Primary I-V avg {gov_avg_str} teachers/school across {metrics.gov_primary_1_5_schools:,} schools). "
            f"Source: {source_locator}.",
            meta,
        )

    entity_label = f"{state_name} — School Education Dept (UDISE+ {academic_year})"
    if signal_category == "severe_concentration_and_overcrowding":
        title = (
            f"{state_name} ({academic_year}): {metrics.single_teacher_schools:,} single-teacher schools "
            f"({metrics.single_teacher_share_pct:.1f}% of schools, {metrics.single_teacher_enrolments:,} students)"
        )
    elif signal_category == "high_single_teacher_concentration":
        title = (
            f"{state_name} ({academic_year}): {metrics.single_teacher_schools:,} single-teacher schools "
            f"({metrics.single_teacher_share_pct:.1f}% concentration)"
        )
    elif signal_category == "severe_single_teacher_overcrowding":
        title = (
            f"{state_name} ({academic_year}): {metrics.students_per_single_teacher:.1f} students/single-teacher school "
            f"across {metrics.single_teacher_schools:,} schools ({metrics.single_teacher_enrolments:,} pupils)"
        )
    else:
        title = (
            f"{state_name} ({academic_year}): Government primary schools average {gov_avg_str} teachers/school "
            f"across {metrics.gov_primary_1_5_schools:,} schools"
        )

    gov_ptr_str = f"{metrics.gov_primary_ptr:.1f}:1" if metrics.gov_primary_ptr is not None else "n/a"
    summary = (
        f"Official UDISE+ {academic_year} records for {state_name} show {metrics.single_teacher_schools:,} "
        f"single-teacher schools out of {metrics.total_schools:,} total schools "
        f"({metrics.single_teacher_share_pct:.2f}%, {metrics.single_teacher_enrolments:,} enrolled students; "
        f"{metrics.students_per_single_teacher:.1f} students per single-teacher school). "
        f"Government schools make up {metrics.gov_school_share_pct:.1f}% of all schools "
        f"({metrics.gov_total_schools:,} of {metrics.total_schools:,}), with {metrics.gov_primary_1_5_schools:,} "
        f"Government Primary (Classes I-V) schools staffed by {metrics.gov_primary_1_5_teachers:,} teachers "
        f"({gov_avg_str} teachers per school; Primary PTR {gov_ptr_str} across {metrics.gov_primary_enrolments:,} "
        f"Government Primary pupils). Triggered rule(s): {', '.join(triggered)}. "
        f"Note: Table 2.2 single-teacher totals cover all school managements combined; Tables 3.3, 4.3, and 5.2 "
        f"isolate Government-managed schools."
    )

    citations: list[dict[str, Any]] = []
    if retrieval_url:
        citations.append(
            {
                "document": doc_title,
                "url": retrieval_url,
                "landing_url": landing_url,
                "official_publisher": official_publisher,
                "page": metrics.table_2_2_page,
                "table_pages": {
                    "table_2_2": metrics.table_2_2_page,
                    "table_3_3": metrics.table_3_3_page,
                    "table_4_3": metrics.table_4_3_page,
                    "table_5_2": metrics.table_5_2_page,
                },
                "source_locator": source_locator,
                "sha256": expected_sha or None,
                "excerpt": (
                    f"{state_name} | Schools={metrics.total_schools:,}, Enrolments={metrics.total_enrolments:,}, "
                    f"Single-Teacher Schools={metrics.single_teacher_schools:,} ({metrics.single_teacher_share_pct:.2f}%), "
                    f"Single-Teacher Enrolments={metrics.single_teacher_enrolments:,} | "
                    f"Gov Schools={metrics.gov_total_schools:,}, Gov Primary(1-5) Schools={metrics.gov_primary_1_5_schools:,}, "
                    f"Gov Primary(1-5) Teachers={metrics.gov_primary_1_5_teachers:,} ({gov_avg_str}/school)"
                ),
            }
        )

    lead = AuditLeadCandidate(
        title=title[:180],
        summary=summary[:1500],
        methodology_code=ONE_TEACHER_FIVE_GRADES_METHODOLOGY,
        entity_name=entity_label[:160],
        jurisdiction=state_name,
        fiscal_year=academic_year,
        monetary_exposure_inr=0.0,
        severity=severity,
        anomaly_metrics={
            "academic_year": academic_year,
            "state_name": state_name,
            "state_key": state_key,
            "is_ut": is_ut,
            "signal_category": signal_category,
            "triggered_rules": triggered,
            "total_schools": metrics.total_schools,
            "total_enrolments": metrics.total_enrolments,
            "total_teachers": metrics.total_teachers,
            "overall_ptr": metrics.overall_ptr,
            "zero_enrolment_schools": metrics.zero_enrolment_schools,
            "single_teacher_schools": metrics.single_teacher_schools,
            "single_teacher_enrolments": metrics.single_teacher_enrolments,
            "single_teacher_share_pct": metrics.single_teacher_share_pct,
            "students_per_single_teacher": metrics.students_per_single_teacher,
            "gov_total_schools": metrics.gov_total_schools,
            "gov_school_share_pct": metrics.gov_school_share_pct,
            "gov_primary_1_5_schools": metrics.gov_primary_1_5_schools,
            "gov_primary_1_5_teachers": metrics.gov_primary_1_5_teachers,
            "gov_primary_teachers_per_school": metrics.gov_primary_teachers_per_school,
            "gov_primary_enrolments": metrics.gov_primary_enrolments,
            "gov_primary_ptr": metrics.gov_primary_ptr,
            "single_teacher_vs_gov_schools_pct": metrics.single_teacher_vs_gov_schools_pct,
            "table_2_2_page": metrics.table_2_2_page,
            "table_3_3_page": metrics.table_3_3_page,
            "table_4_3_page": metrics.table_4_3_page,
            "table_5_2_page": metrics.table_5_2_page,
            "source_locator": source_locator,
            "threshold_share_pct": HIGH_CONCENTRATION_SHARE_PCT,
            "threshold_students_per_single_teacher": SEVERE_OVERCROWDING_STUDENTS_PER_TEACHER,
            "threshold_gov_primary_avg_teachers": GOV_PRIMARY_MIN_AVG_TEACHERS,
            "all_management_scope_note": (
                "Table 2.2 Single-Teacher School counts include all school managements; Tables 3.3, 4.3, and 5.2 "
                "report Government schools, teachers, and enrolments specifically."
            ),
        },
        rule_triggered=rule_triggered,
        source_locator=source_locator,
        citations=citations,
        benign_explanations=[
            "Small, sparsely populated rural, tribal, or hilly habitations where RTE Act neighbourhood norms require keeping a standalone primary school open with low enrolment.",
            "Recent state teacher recruitment or school rationalisation drives completed after the UDISE+ reference cutoff date.",
            "A portion of Table 2.2 single-teacher schools may include non-government managements in states with lower government school share.",
        ],
        suggested_authority=f"Department of School Education, {state_name}; Department of School Education & Literacy (MoE)",
        amount_type="school_staffing_and_enrolment",
        evidence_text=summary[:1000],
    )

    return [lead], 0, 0.0, f"OT1 flagged {state_name} ({academic_year}): {signal_category} ({metrics.single_teacher_schools:,} single-teacher schools).", meta


def validate_context(ctx: Any, where: str) -> list[str]:
    """Validate ``payload.context.one_teacher_five_grades`` for an OT1 task."""
    errors: list[str] = []
    if not isinstance(ctx, dict):
        return [f"{where}.payload.context.one_teacher_five_grades is required for OT1 tasks."]
    spec = ctx.get("one_teacher_five_grades")
    if not isinstance(spec, dict):
        return [f"{where}.payload.context.one_teacher_five_grades must be an object."]

    yr = str(spec.get("academic_year") or "").strip()
    if not _YEAR_RE.fullmatch(yr):
        errors.append(f"{where}.payload.context.one_teacher_five_grades.academic_year must be YYYY-YY (e.g. '2025-26').")
    sname = spec.get("state_name")
    if not isinstance(sname, str) or not sname.strip() or len(sname) > 120:
        errors.append(f"{where}.payload.context.one_teacher_five_grades.state_name is required (<= 120 chars).")
    skey = spec.get("state_key")
    if not isinstance(skey, str) or not _KEY_RE.fullmatch(skey):
        errors.append(f"{where}.payload.context.one_teacher_five_grades.state_key must match [a-z0-9-] (<= 49 chars).")
    tpages = spec.get("table_pages")
    if not isinstance(tpages, dict):
        errors.append(f"{where}.payload.context.one_teacher_five_grades.table_pages must be an object.")
    else:
        for tkey in ("table_2_2", "table_3_3", "table_4_3", "table_5_2"):
            pv = tpages.get(tkey)
            if isinstance(pv, bool) or not isinstance(pv, int) or pv < 1 or pv > 500:
                errors.append(f"{where}.payload.context.one_teacher_five_grades.table_pages.{tkey} must be an integer 1..500.")
    src_title = spec.get("source_document_title")
    if not isinstance(src_title, str) or not src_title.strip() or len(src_title) > 240:
        errors.append(f"{where}.payload.context.one_teacher_five_grades.source_document_title is required (<= 240 chars).")
    ref_url = ctx.get("retrieval_url")
    if ref_url is not None:
        try:
            validate_safe_http_url(str(ref_url), require_https=True, allow_private=False)
        except ValueError:
            errors.append(f"{where}.payload.context.retrieval_url must be a public https:// URL.")
    return errors
