# Sponsors, Compute Grants & Open Public Treasury Charter

> **Editorial Independence Firewall:**
> *"Sponsors fund compute and infrastructure. Open evidence, deterministic code, and independent peer verification decide every finding."*

A civic watchdog auditing public money must practice **radical budget transparency itself**. Every rupee, dollar, and donated API-credit grant received by AI Watchdogs India (`aiwatchdogs.in`) — and every server, hosting, OCR, and domain expense paid out — is published in real time in our **Open Public Treasury Ledger** (`/ledger`, downloadable via `GET /api/ledger?format=json` and `GET /api/ledger?format=csv`).

---

## 1. The Ironclad Editorial Firewall

All sponsors, backers, and compute-grant partners accept these non-negotiable terms prior to being listed on the Sponsors Wall:

1. **Zero Editorial Influence:** Sponsors cannot propose, suppress, edit, delay, prioritize, or pre-review any audit mission, finding, verification verdict, or RTI draft.
2. **Zero Political Party or Election-Campaign Funding:** AI Watchdogs India does not accept funding from political parties, electoral candidates, PACs, or politically controlled trusts.
3. **Mandatory Conflict Disclosure:** If an institutional sponsor (or its parent/subsidiary) appears in a public procurement or corporate-proximity dataset audited by the swarm, the swarm audits that record identically under the same deterministic rules — and the sponsorship relationship is disclosed automatically.
4. **No Pay-to-Play API Access:** All datasets, deterministic engines, findings, and APIs are 100% open source under the **MIT License** and freely accessible to every citizen, journalist, and researcher.
5. **Public Attribution & Open Receipts:** Anonymous large institutional grants are not accepted. Every inflow and outflow is logged publicly with date, category, amount (INR / USD equivalent / compute credits), and receipt or invoice reference.

---

## 2. How You Can Back the Swarm

### Path A — Donate Idle Compute (Free / ₹0 Hub Cost)
The single highest-impact contribution is running an audit node on your laptop, workstation, or homelab using:
* **Zero-cost Deterministic CPU Mode (`rule-engine`)** — runs all 8 Python audit engines and peer verifications with zero LLM API spend.
* **Local Open-Weight LLMs (Ollama / vLLM)** — e.g., `qwen2.5:14b` or `llama3.3` running privately on your own GPU.
* **BYO Cloud API Key** with strict self-imposed caps (`--max-tasks`, `--max-usd`).

### Path B — Citizen Backers Wall (From ₹500 / $5)
* Open to individual citizens supporting hosting, public archival snapshots, domain renewals (`aiwatchdogs.in`), and OCR compute for multi-hundred-page CAG PDFs.
* Repeat contributions by the same backer stack cumulatively on the public ranked wall (`/sponsors`).
* Backers choose their public display name or handle (or `Anonymous Citizen` for small individual micro-grants under the public disclosure threshold).

### Path C — Compute-Grant & Institutional Sponsors (₹85,000+ / $1,000+)
* Designed for civil-society foundations, open-source labs, cloud providers, and AI infrastructure providers contributing cash grants or verifiable GPU / inference / cloud hosting credits.
* Listed as muted monochrome partner wordmarks in the *"Backed by"* strip and `/sponsors` wall, accompanied by the Editorial Independence Firewall statement.

---

## 3. Open Public Treasury Ledger (`GET /api/ledger`)

Inspired by OpenCollective's open-finances standard, the Hub tracks all project finances in the `treasury_ledger` table and exposes live public exports:

* **Web View:** `https://aiwatchdogs.in/ledger`
* **JSON Export:** `GET /api/ledger?format=json`
* **CSV Export:** `GET /api/ledger?format=csv`

Every ledger entry records:
* `entry_date`: ISO-8601 date of transaction or credit grant
* `direction`: `inflow` | `outflow`
* `entry_type`: `grant` | `citizen_donation` | `compute_credit` | `infrastructure` | `domain` | `ocr_compute`
* `category`: Budget line category (e.g., *Compute Grant*, *Citizen Backer*, *Hosting & Storage*, *Domain & TLS*)
* `counterparty`: Public name of backer, grant provider, or infrastructure vendor
* `description`: Plain-English description (e.g., *Monthly VPS & SQLite backup hosting*, *PDF archival storage*, *Domain renewal for aiwatchdogs.in*)
* `amount_inr`: Amount in Indian Rupees (with original currency/credit note where applicable)
* `compute_credit_units`: Optional GPU-hour or API-credit unit disclosure for non-cash grants
* `receipt_ref`: Public invoice, grant agreement, or transaction reference ID
* `recorded_at` / `created_at`: UTC timestamp

*(Note: In a fresh local installation booted with seed data, sample founding grants and sample infrastructure line items are clearly labeled as seed/demo ledger entries so contributors can inspect the transparency workflow immediately.)*

---

## 4. Recording a Pledge Locally

To test or submit a pledge on your local instance (or on `aiwatchdogs.in`):

* Use the **"Sponsor / Donate Compute"** modal at `/sponsors`, or
* `POST /api/sponsors/pledge` with backer name, tier (`citizen` | `compute_grant` | `institutional`), pledge amount or compute-credit description, and acknowledgment of the Editorial Independence Firewall.
