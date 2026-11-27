# Methodology `OT1` — One Teacher, Five Grades (`P5`)

## Objective
Map where **single-teacher schools** and **under-staffed Government Primary (Classes I–V) schools** are concentrated across India's States and Union Territories using official Ministry of Education **UDISE+** annual reports (`2023-24`, `2024-25`, and `2025-26`).

## Official Primary Sources (Zero Auth, Zero CAPTCHA, SHA-256 Pinned)
All three source booklets are published directly by the Department of School Education & Literacy (DoSEL), Ministry of Education at `https://dashboard.udiseplus.gov.in/`:
- **UDISE+ 2025-26 Booklet (Existing Structure)** — all 28 States + 8 UTs (`36` tasks)
- **UDISE+ 2024-25 Booklet (Existing Structure)** — all 28 States + 8 UTs (`36` tasks)
- **UDISE+ 2023-24 Booklet (Existing Structure)** — all 28 States (`28` tasks)

Total: **`100` deterministic tasks** across three consecutive academic cycles (`100,843` single-teacher schools and `29,12,147` enrolled pupils nationwide in 2025-26).

## Four Tables Cross-Checked per Task
1. **Table 2.2** (*State wise highlights: Schools, Enrolments and Teachers*) — Total Schools, Enrolments, Teachers, overall PTR, **Schools with Single Teachers**, and **Enrolments in Single Teacher Schools**.
2. **Table 3.3** (*Number of schools by management: Government*) — Government Total Schools and Government Primary (Classes I–V) schools.
3. **Table 4.3** (*Number of teachers by management: Government*) — Government Total Teachers and Government Primary (Classes I–V) teachers.
4. **Table 5.2** (*Enrolment of students by school management: Government*) — Government Total and Primary-level student enrolments.

## Deterministic Rules (`OT1`)
1. **High Single-Teacher Concentration (`single_teacher_share_ge_10pct`)**: `single_teacher_schools / total_schools >= 10.0%` AND `single_teacher_schools >= 100`.
2. **Severe Single-Teacher Overcrowding (`students_per_single_teacher_gt_30`)**: `single_teacher_enrolments / single_teacher_schools > 30.0` (exceeding the RTE Act 2009 Schedule `30:1` Primary PTR norm) AND `single_teacher_schools >= 500`.
3. **Under-Staffed Government Primary I–V Schools (`gov_primary_avg_teachers_lt_2`)**: `gov_primary_1_5_teachers / gov_primary_1_5_schools < 2.0` (below the RTE Act 2009 statutory 2-teacher minimum for Primary schools) AND `gov_primary_1_5_schools >= 100`.

States and UTs below all thresholds are published as **Cleared** with equal standing and full extracted figures.

## Transparent Methodological Boundaries
- In UDISE+ national booklets, **Table 2.2** reports single-teacher school counts across **all school managements combined**, whereas **Tables 3.3, 4.3, and 5.2** isolate **Government schools** specifically (Government Primary I–V schools account for 612,066 of all 722,498 Primary I–V schools nationally in 2025-26). Every `OT1` output explicitly publishes both sets of figures without conflating them.
- District-level and school-level (`11-digit UDISE code`) concentrations are listed in `planned_tasks` because UDISE+ public booklets publish State/UT matrices while bulk school/district CSV extracts require authenticated access on `udiseplus.gov.in`.
