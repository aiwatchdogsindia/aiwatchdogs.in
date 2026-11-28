# Methodology `P9` — Auditing Government Exams

## Simple Hook
> **Did India's government exams happen on time and flawlessly — or get delayed, postponed, rescheduled, or cancelled after students sat them?**

## Objective
Audit **1,000 SHA-256-pinned Indian government examination conduct records** across the **past 3 years (`2023-11-28` to `2026-11-28`, exam years 2023–2026)** spanning the **Union Government** (`347` tasks) and **all 36 States & Union Territories** (`586` State + `67` UT tasks across all 28 States and 8 UTs), covering **716 unique examinations**, **163 conducting authorities**, and **858 unique live-verified byte-stable official documents**. Every task verifies an official scheduled examination notice against its subsequent conduct or disruption record — failing closed as `unevaluable` whenever downloaded bytes fail SHA-256 verification or lack required verbatim word-boundary documentary tokens.

## Flagship Anchor Case (Task #1: `p9-union-ugc-net-june-2024-0001`)
- **Examination:** UGC-NET (University Grants Commission National Eligibility Test) — June 2024 Cycle (OMR Pen & Paper -> Cancelled & Re-conducted in CBT)
- **Jurisdiction & Authority:** Union of India · National Testing Agency (NTA)
- **Original Schedule (`2024-06-18`):** NTA conducted UGC-NET June 2024 in OMR pen-and-paper mode across 1,205 centres in 317 cities on 18 June 2024 for 11,21,225 registered (9,08,580 appeared) candidates.
- **Documented Outcome (`2024-08-21`):** Following post-conduct cancellation of the 18 June 2024 offline exam, NTA notified fresh Computer-Based Test (CBT) dates between 21 August 2024 and 04 September 2024.
- **Classification:** `cancelled_after_conduct` (`students_took_exam_before_cancellation=true`)
- **Primary Record:** https://nta.ac.in/Download/Notice/Notice_20240618221029.pdf
- **Secondary Record:** https://nta.ac.in/Download/Notice/Notice_20240628232223.pdf

## 3-Year Audit Scorecard (`1000` Exams · Nov 2023 – Nov 2026 · All 28 States + 8 UTs + Union)
- **Did Not Happen Flawlessly:** `738` (`73.8%`)
- **Happened Flawlessly (Cleared Benchmark):** `262` (`26.2%`)
- **Disruption Breakdown:**
  - `cancelled_after_conduct` (Took Exam, Then Cancelled): `10`
  - `postponed`: `32`
  - `rescheduled`: `120`
  - `delayed`: `478`
  - `cancelled_before_conduct`: `35`
  - `partial_reexam_or_glitch` (Technical Glitch / Partial Re-Exam): `63`
  - `flawless_conduct` (Conducted Flawlessly — Cleared): `262`
- **Jurisdiction Tier Split:** Union of India: `347` · States (28/28): `586` · Union Territories (8/8): `67`
- **Year-over-Year Coverage (2023–2026):**
  - `2023`: `23` audited (`14` not flawless · `9` flawless · `0` took exam then cancelled)
  - `2024`: `385` audited (`285` not flawless · `100` flawless · `1` took exam then cancelled)
  - `2025`: `334` audited (`251` not flawless · `83` flawless · `5` took exam then cancelled)
  - `2026`: `258` audited (`188` not flawless · `70` flawless · `4` took exam then cancelled)
- **Deterministic Dry-Run (`as_of_date=2026-11-28`):** `738` leads · `262` cleared · `0` unevaluable (across all `1000` tasks)
- **Verified Byte-Stable Source Domains (858 unique pinned URLs across 28 official domains):**
  - `ssc.gov.in`: `211` unique SHA-256-pinned documents
  - `nta.ac.in`: `121` unique SHA-256-pinned documents
  - `sansad.in`: `80` unique SHA-256-pinned documents
  - `hpsc.gov.in`: `70` unique SHA-256-pinned documents
  - `www.tnpsc.gov.in`: `64` unique SHA-256-pinned documents
  - `hssc.gov.in`: `46` unique SHA-256-pinned documents
  - `nssb.nagaland.gov.in`: `34` unique SHA-256-pinned documents
  - `www.ibps.in`: `33` unique SHA-256-pinned documents
  - `apssb.nic.in`: `27` unique SHA-256-pinned documents
  - `gpsc.goa.gov.in`: `22` unique SHA-256-pinned documents
  - `gpsc.gujarat.gov.in`: `19` unique SHA-256-pinned documents
  - `mpsc.mizoram.gov.in`: `15` unique SHA-256-pinned documents
  - `rpsc.rajasthan.gov.in`: `15` unique SHA-256-pinned documents
  - `www.upsc.gov.in`: `15` unique SHA-256-pinned documents
  - `apsc.nic.in`: `13` unique SHA-256-pinned documents
  - `appsc.gov.in`: `12` unique SHA-256-pinned documents
  - `ugcnet.nta.ac.in`: `11` unique SHA-256-pinned documents
  - `cdnbbsr.s3waas.gov.in`: `9` unique SHA-256-pinned documents
  - `www.cbse.gov.in`: `8` unique SHA-256-pinned documents
  - `bceceboard.bihar.gov.in`: `7` unique SHA-256-pinned documents
  - `websitenew.tgpsc.gov.in`: `6` unique SHA-256-pinned documents
  - `dsssb.delhi.gov.in`: `4` unique SHA-256-pinned documents
  - `spsc.sikkim.gov.in`: `4` unique SHA-256-pinned documents
  - `mppsc.mp.gov.in`: `3` unique SHA-256-pinned documents
  - `www.keralapsc.gov.in`: `3` unique SHA-256-pinned documents
  - `+3 additional official State/UT/Union domains`: `6` unique SHA-256-pinned documents

## Deterministic Rules (`P9`)
1. **Took Exam, Then Cancelled (`P9-CANCELLED-AFTER-CONDUCT`):** Candidates sat/attempted the scheduled examination and the authority subsequently cancelled it (`students_took_exam_before_cancellation=true`).
2. **Postponed (`P9-POSTPONED`):** Scheduled examination was deferred/postponed prior to conduct.
3. **Rescheduled (`P9-RESCHEDULED`):** Scheduled examination date, shift, or centre was revised via official notice.
4. **Delayed (`P9-DELAYED`):** Examination conduct, notification, or statutory cycle was materially delayed beyond announced schedule.
5. **Cancelled Before Conduct (`P9-CANCELLED-BEFORE-CONDUCT`):** Notified examination or recruitment test was cancelled/withdrawn before candidates sat.
6. **Technical Glitch / Partial Re-Exam (`P9-PARTIAL-REEXAM-OR-GLITCH`):** Technical/CBT fault, time-loss grace-mark anomaly, or centre/subject-level disruption requiring partial re-examination.
7. **Conducted Flawlessly (`Cleared`):** Audited and confirmed conducted as scheduled without documented disruption (`262` tasks), published with equal journalistic standing.

## Legal, Privacy & Verification Safeguards
- **Fail-Closed Verification:** Every primary and secondary URL is double-fetch byte-stability verified over HTTPS and SHA-256 pinned (`DigestMismatchError` on any byte change). `P9` confirms `>= 40` chars of machine-readable extracted text and 100% verbatim word-boundary token presence in the verified document bytes before emitting any lead or cleared verdict.
- **No Fabricated Candidate Counts:** `affected_candidates_count` is populated exclusively when explicitly stated in the cited official document; otherwise preserved as `null` with an explicit `affected_candidates_basis`.
- **BNS Sec 356 Neutrality & DPDP Act 2023:** Zero forbidden accusatory terms and zero personal PII across `projects/auditing-government-exams.json`, `coverage.json`, `documents.csv`, and `METHODOLOGY.md`. Every lead emits mandatory lawful benign explanations (preventive integrity safeguards under the Public Examinations (Prevention of Unfair Means) Act 2024, election Model Code of Conduct, severe weather/disaster advisories, CBT vendor faults, and judicial orders).
