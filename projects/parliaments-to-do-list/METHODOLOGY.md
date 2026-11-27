# Methodology `P6` — Parliament's To-Do List

## Simple Hook
> **A minister promised Parliament an answer. Did it ever get one?**

## Objective
Track **1,000 formal ministerial assurances** across **56 Union Ministries** in both Houses of the Indian Parliament directly from **Digital Sansad** (`https://sansad.in`). For every assurance, the deterministic `P6` rule engine extracts and cites three verified public-record elements:
1. **The Original Promise** — verbatim assurance text (`textOfAssurance` / `extent`), house, session, question reference, promise date, and (for Lok Sabha) the official Question & Answer PDF.
2. **The Recorded Status** — official Digital Sansad register status (`Pending`, `Fully Implemented`, `Partly Implemented`, or `Dropped`), days elapsed against the standard **90-day (3-month)** parliamentary assurance fulfillment timeline, Committee extensions (`extNo`, `extUpto`), and Committee remarks.
3. **What the Follow-Up Actually Answered** — extracted directly from pinned Rajya Sabha Implementation Report PDFs (`irUrl`), Lok Sabha Implementation Statement references (`impRecordNo`, e.g., `SS-1/28/05.08.2026`), Committee dropping remarks, or confirmed absence of any laid follow-up statement.

## Anchor Case (Task #1: `p6-ls-18-07-sq-544`)
- **Assurance Reference:** `SQ-18/07/544` (18th Lok Sabha, Session 7, answered `2026-04-02`)
- **Ministry:** Road Transport and Highways
- **Subject:** Widening of NH-11 Road from Jhunjhunu to Fatehpur
- **Recorded Status:** `pending` (`239` days elapsed as of `2026-11-27`, exceeding the 90-day norm)
- **Original Promise:** (a) to (c) The process for preparation of Detailed Project Report (DPR) for development of NH-11 from Jhunjhunu to Fatehpur to four- lanes has been initiated in January, 2026. Based on the outcome of the DPR, this stretch would be considered for upgradation to four- lanes subject to traffic requirements and synergy with PM Gati Shakti National Master Plan (NMP).
- **Follow-Up Status:** No Implementation Statement has been laid on the Table of the Lok Sabha as of 2026-11-27; Digital Sansad still lists SQ-18/07/544 as PENDING (239 days elapsed since 2026-04-02).

## Corpus Coverage (`1000` Tasks, SHA-256 Pinned)
- **Lok Sabha (18th LS, Sessions 1–7):** `546` assurances (with official Digital Sansad Question & Answer PDFs pinned as `secondary_url` where published)
- **Rajya Sabha (Sessions 260–271):** `454` assurances (with official Implementation Report PDFs `irUrl` pinned as `secondary_url` for `FIMP` assurances)
- **Recorded Status Breakdown:** `Pending`: `578` · `Fully Implemented`: `388` · `Partly Implemented`: `2` · `Dropped`: `32`
- **Deterministic Dry-Run (`as_of_date=2026-11-27`):** `952` leads · `48` cleared · `0` unevaluable

## Deterministic Rules (`P6`)
1. **Overdue Pending Assurance (`P6-OVERDUE-PENDING` / `overdue_pending_assurance`)**: Recorded status is `pending` and elapsed time exceeds the parliamentary **90-day (3-month)** fulfillment standard (`medium` at `91–180` days, `high` at `181–365` days, `critical` at `>365` days or `>= 2` Committee extensions).
2. **Partly Implemented Assurance (`P6-PARTLY-IMPLEMENTED` / `partly_implemented_assurance`)**: Recorded status is `partly_implemented` (partial statement laid while core commitment remains open).
3. **Dropped Without Fulfillment (`P6-DROPPED-ASSURANCE` / `dropped_without_fulfillment`)**: Recorded status is `dropped` by the Committee on Government Assurances rather than substantively fulfilled.
4. **Delayed or Non-Machine-Readable Follow-Up (`P6-FOLLOWUP-GAP` / `delayed_or_unreadable_followup`)**: Recorded status is `fully_implemented`, but fulfillment took `>180` days, the pinned Rajya Sabha Implementation Report PDF lacks a machine-readable text layer (image-only scan), or no implementation statement reference is recorded.

Assurances still within the 90-day window (`pending`, `<=90` days) or fulfilled within `<=180` days with verifiable follow-up documentation are published as **Cleared** with equal standing and all three citations preserved.

## Transparent Methodological Boundaries & Benign Explanations
- **Lok Sabha Implementation Statement PDFs:** Digital Sansad's Lok Sabha Government Assurances API publishes the Implementation Statement reference (`impRecordNo`, e.g., `SS-1/28/05.08.2026`) and laying date, while its `papersLaid` API lists the corresponding MPA statements with `url: null`. `P6` cites the exact statement reference and transparently records that no public PDF link is attached on Digital Sansad.
- **Scanned Rajya Sabha IR PDFs:** Many Rajya Sabha `irUrl` Implementation Report PDFs are uploaded as scanned image PDFs without OCR text layers. `P6` verifies the PDF bytes via SHA-256 and explicitly flags image-only scans rather than guessing follow-up text.
- **Plausible Lawful Explanations:** Inter-ministerial consultations, State Government data collection, DPR / environmental clearance timelines (such as the December 2026 DPR estimate noted on NH-11), formal extensions granted by the Committee on Government Assurances, and parliamentary session calendars for laying statements.
