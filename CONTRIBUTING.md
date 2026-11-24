# Contributing to AI Watchdogs India (`aiwatchdogs.in`)

Thank you for helping build open, non-partisan, evidence-driven public accountability infrastructure for India! Whether you donate 5 minutes of local CPU time to verify CAG audit paras, build a new state procurement connector, refine a deterministic audit rule, or polish the editorial dashboard, you are welcome here.

Before contributing, please read:
* [`CODE_OF_CONDUCT.md`](./CODE_OF_CONDUCT.md)
* [`LEGAL_AND_ETHICS.md`](./LEGAL_AND_ETHICS.md)
* [`METHODOLOGY.md`](./METHODOLOGY.md)

---

## 1. Local Development Setup

### Prerequisites
* **Python 3.11+** (tested on **Python 3.14**) and **[`uv`](https://docs.astral.sh/uv/)** — *do not use `pip`.*
* **Node.js 22+** (tested on **Node 26**) and **`npm`** — *do not use `pnpm`, `yarn`, or `bun`.*
* **Optional:** Docker + Docker Compose for single-container deployment verification; PostgreSQL (for testing production migrations/concurrency); Ollama (`localhost:11434`) or an opt-in cloud API key if testing optional editorial interpretation.

### Install & Run Locally

```bash
# 1. Install Python dependencies (via uv) and web dependencies (via npm)
make install

# 2. Run the full pytest test suite (offline fixtures, zero live network required)
make test

# 3. Start the Coordinator Hub (port 8000) and Vite dev dashboard (port 5173)
make dev
# Or run in two separate terminals:
make dev-hub    # FastAPI Hub + SQLite WAL + SSE at http://127.0.0.1:8000
make dev-web    # Vite React SPA at http://127.0.0.1:5173 (proxies /api to :8000)

# 4. Run a 3-node local ₹0 CPU contributor swarm against your local Hub
make run-swarm
```

---

## 2. Repository Map

```text
.
├── pyproject.toml                   # Python package & uv dependency spec
├── uv.lock                          # Hashed Python lockfile (verified via `uv sync --frozen`)
├── api/index.py                     # Vercel Fluid Compute ASGI entrypoint (fail-closed without Postgres)
├── src/aiwatchdogs/
│   ├── core/
│   │   ├── models.py                # Shared Pydantic schemas & provenance models
│   │   ├── safety.py                # Verhoeff Aadhaar/PAN recursive PII scrubber, neutrality linter, SHA-256
│   │   └── rti.py                   # Jurisdiction-aware RTI Act 2005 Sec 6(1) <=500-word draft generator
│   ├── engines/
│   │   ├── registry.py              # METHODOLOGY_CATALOG (M1–M8), fingerprints & run_engine()
│   │   ├── procurement.py           # M1–M4 (Single-bid, Threshold bands, HHI, Award vs Estimate)
│   │   ├── fiscal.py                # M5–M6 (March Rush / OBDS budgets, CAG Audit Paras)
│   │   ├── schemes.py               # M7 (PMGSY, MPLADS, MGNREGA)
│   │   └── proximity.py             # M8 (Corporate–Electoral public disclosure cross-reference)
│   ├── connectors/
│   │   └── loader.py                # Fail-closed digest-verifying CSV/JSON/OCDS/XLSX/PDF loader
│   ├── hub/                         # FastAPI Coordinator Hub, versioned SQLite/Postgres migrations, Desk API, durable SSE
│   └── cli/                         # Typer + Rich contributor agent CLI (`aiwatchdogs`)
├── tests/                           # Offline pytest suites with inline fixtures (engines, hub, cli)
├── web/                             # React + TypeScript + Vite editorial dashboard
├── Makefile                         # Dev, test, build, live swarm (`run-swarm`), and Docker targets
├── Dockerfile                       # Pinned multi-stage Node + Python/uv image
├── docker-compose.yml               # Hub + persistent volume + DB-aware healthcheck + optional Caddy TLS
└── Caddyfile                        # Automatic HTTPS reverse proxy for aiwatchdogs.in
```

---

## 3. High-Impact Ways to Contribute

### A. Donate Compute as a Swarm Node
You do not even need to open a PR to help audit records. Point your coding agent at `/join.txt` or run inside a checkout:

```bash
uv run aiwatchdogs join --hub http://127.0.0.1:8000
```

Or without cloning first (via `uvx` from GitHub):

```bash
uvx --from git+https://github.com/piyushbodaa/aiwatchdogs.in.git aiwatchdogs join --hub https://aiwatchdogs.in
```

Zero-cost `rule-engine` mode runs deterministically on CPU with zero paid-provider API calls and earns leaderboard credit for independent peer verifications and cleared scans.

### B. Add a New Indian Public Data Connector (`src/aiwatchdogs/connectors/`)
Examples needed: state OCDS mirrors, state-level CAG report indexers, Municipal OpenBudgetsIndia datasets, or new official `.gov.in` OGD resource parsers.

**Connector Checklist:**
1. Confirm the source fits **Tier 1** (open API / bulk files) or **Tier 2** (polite public MIS without login). **Never** write CAPTCHA solvers or login scrapers (**Tier 3** portals must use local human-exported CSV/PDF files via `aiwatchdogs audit-file`).
2. Pin all external mirror URLs to immutable commit SHAs and record `official_publisher`, `source_landing_page`, `mirror_name`, and `retrieval_url` separately.
3. Verify `sha256` on raw bytes fail-closed before parsing, enforce bounded download/decompression limits, and pass extracted text through `aiwatchdogs.core.safety.scrub_pii_in_obj()`.
4. Add unit tests using inline/temporary offline fixtures in `tests/` (never make CI depend on live `.gov.in` uptime).

### C. Add or Refine a Deterministic Audit Methodology (`src/aiwatchdogs/engines/`)
Examples needed: State-specific Procurement Act thresholds (Assam PPR 2020, Karnataka KTPP Act, Tamil Nadu Transparency in Tenders Act, Rajasthan RTPP Act), NHAI toll/BOT variance checks, or Smart Cities Mission utilization checks.

**Methodology Checklist:**
1. Anchor every threshold in a specific published Indian statute, state procurement rule, GFR rule, CVC circular, or CAG guideline — and record jurisdiction & effective date explicitly.
2. Keep all anomaly math **100% deterministic** in pure Python with explicit `as_of_date` so any independent node reproduces identical metrics.
3. Preserve missing/unknown source fields (`None`/`unrecorded`) instead of defaulting them to zero or false, and classify unevaluable groups separately from cleared records.
4. Ensure every generated `AuditLeadCandidate` includes:
   * Exact pinpoint citations (`row_id` / `page` / `para` / `excerpt`).
   * At least one realistic, lawful `benign_explanations` entry.
   * Clean passage through `lint_neutrality()` (zero accusatory terms).
5. Add offline regression fixtures in `tests/` covering both triggering anomalies and clean/edge-case baseline records.

### D. Improve the Web Dashboard (`web/`)
* Preserve the warm archival editorial broadsheet aesthetic (`#F6F4EF`, **Geist Serif** and **Geist Mono**) and strict **Epistemic Visual Grammar** (*only peer-verified findings appear as verified; unverified, corrected, retracted, and quarantined states remain clearly distinct*).
* Keep dialogs focus-trapped and keyboard-accessible (WCAG AA contrast >= 4.5:1) across desktop (`1440px`) and mobile (`390px`).
* Verify `cd web && npx tsc --noEmit && npm run build` passes with zero TypeScript or bundler errors.

---

## 4. Verification Before Submitting Changes

Every contribution should pass all local checks cleanly:

```bash
# 1. Verify frozen lockfile sync & run Python unit/integration tests
uv sync --frozen
uv run pytest -v

# 2. Frontend TypeScript check & production bundle build
cd web && npm ci && npx tsc --noEmit && npm run build

# 3. Optional: verify Docker container build & DB healthcheck
make docker-up
```

When adding inline test fixtures in `tests/`:
* **Never** accuse any real living individual of wrongdoing. Use clearly scoped public-record citations or synthetic test identifiers while keeping Indian statutory rules, ₹ thresholds, and portal routing accurate.

---

## 5. License

By contributing to AI Watchdogs India, you agree that your contributions will be licensed under the [MIT License](./LICENSE).
