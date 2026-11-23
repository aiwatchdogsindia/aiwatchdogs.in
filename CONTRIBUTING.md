# Contributing to AI Watchdogs India (`aiwatchdogs.in`)

Thank you for helping build open, non-partisan, evidence-driven public accountability infrastructure for India! Whether you donate 5 minutes of local CPU time to verify CAG audit paras, build a new state procurement connector, refine a GFR 2017 deterministic rule, or polish the editorial dashboard, you are welcome here.

Before contributing, please read:
* [`CODE_OF_CONDUCT.md`](./CODE_OF_CONDUCT.md)
* [`LEGAL_AND_ETHICS.md`](./LEGAL_AND_ETHICS.md)
* [`METHODOLOGY.md`](./METHODOLOGY.md)

---

## 1. Local Development Setup

### Prerequisites
* **Python 3.11+** (tested on **Python 3.14**) and **[`uv`](https://docs.astral.sh/uv/)** — *do not use `pip`.*
* **Node.js 22+** (tested on **Node 26**) and **`npm`** — *do not use `pnpm`, `yarn`, or `bun`.*
* **Optional:** Docker + Docker Compose for single-container deployment verification; Ollama (`localhost:11434`) or a cloud API key if testing optional LLM narrative enrichment.

### Install & Run Locally

```bash
# 1. Install Python dependencies (via uv) and web dependencies (via npm)
make install

# 2. Run the full pytest test suite
make test

# 3. Start the Coordinator Hub (port 8000) and Vite dev dashboard (port 5173)
make dev
# Or run in two separate terminals:
make dev-hub    # FastAPI Hub + SQLite WAL + SSE at http://127.0.0.1:8000
make dev-web    # Vite React SPA at http://127.0.0.1:5173 (proxies /api to :8000)

# 4. Run a 2-node local contributor swarm demo against your local Hub
make demo-swarm
```

---

## 2. Repository Map

```text
.
├── pyproject.toml                   # Python package & uv dependency spec
├── src/aiwatchdogs/
│   ├── core/
│   │   ├── models.py                # Shared Pydantic schemas
│   │   ├── safety.py                # Verhoeff Aadhaar/PAN PII scrubber, neutrality linter, SHA-256
│   │   └── rti.py                   # RTI Act 2005 Sec 6(1) <=500-word draft generator
│   ├── engines/
│   │   ├── registry.py              # METHODOLOGY_CATALOG (M1–M8) & run_engine()
│   │   ├── procurement.py           # M1–M4 (Single-bid, Split-tendering, HHI, Unit-price)
│   │   ├── fiscal.py                # M5–M6 (March Rush, CAG Audit Paras)
│   │   ├── schemes.py               # M7 (PMGSY, MPLADS, MGNREGA)
│   │   └── proximity.py             # M8 (Corporate–Electoral–Procurement disclosure leads)
│   ├── connectors/
│   │   └── loader.py                # Local CSV/JSON/OCDS/PDF + polite Tier-1 HTTP loader
│   ├── hub/                         # FastAPI Coordinator Hub, SQLite WAL, Desk API, SSE, /join.txt
│   └── cli/                         # Typer + Rich contributor agent CLI (`aiwatchdogs`)
├── datasets/sample/                 # 8 clearly badged [SAMPLE DEMO DATASET] fixtures (M1–M8)
├── tests/                           # pytest suites (engines, hub, cli)
├── web/                             # React + TypeScript + Vite editorial dashboard
├── Makefile                         # Common dev, test, build, swarm demo, and Docker targets
├── Dockerfile                       # Multi-stage Node 26 + Python 3.14/uv image
├── docker-compose.yml               # Hub + persistent SQLite volume + optional Caddy TLS profile
└── Caddyfile                        # Automatic HTTPS reverse proxy for aiwatchdogs.in
```

---

## 3. High-Impact Ways to Contribute

### A. Donate Compute as a Swarm Node
You do not even need to open a PR to help audit records. Point your coding agent at `/join.txt` or run:

```bash
uv run aiwatchdogs join --hub http://127.0.0.1:8000
```

Zero-cost `rule-engine` mode works completely offline on any CPU and earns equal leaderboard credit for independent peer verifications and cleared records.

### B. Add a New Indian Public Data Connector (`src/aiwatchdogs/connectors/`)
Examples needed: state OCDS mirrors, state-level CAG report indexers, Municipal OpenBudgetsIndia datasets, or new official `.gov.in` OGD resource parsers.

**Connector Checklist:**
1. Confirm the source fits **Tier 1** (open API / bulk files) or **Tier 2** (polite public MIS without login). **Never** write CAPTCHA solvers or login scrapers (**Tier 3** portals must use local human-exported CSV/PDF files via `aiwatchdogs audit-file`).
2. Send the identifying User-Agent (`AIWatchdogs-India-OpenAudit/0.1`) and respect `robots.txt`.
3. Compute `sha256` on raw bytes and pass all extracted text through `aiwatchdogs.core.safety.scrub_pii()`.
4. Add unit tests using offline fixtures (never make CI depend on flaky live `.gov.in` uptime).

### C. Add or Refine a Deterministic Audit Methodology (`src/aiwatchdogs/engines/`)
Examples needed: State-specific Procurement Act thresholds (Karnataka KTPP Act, Tamil Nadu Transparency in Tenders Act, Rajasthan RTPP Act), NHAI toll/BOT variance checks, or Smart Cities Mission utilization checks.

**Methodology Checklist:**
1. Anchor every threshold in a specific published Indian statute, GFR rule, CVC circular, or CAG guideline.
2. Keep all anomaly math **100% deterministic** in pure Python so any node reproduces identical metrics.
3. Ensure every generated `AuditLeadCandidate` includes:
   * Exact pinpoint citations (`row_id` / `page` / `para` / `excerpt`).
   * At least one realistic, lawful `benign_explanations` entry.
   * Clean passage through `lint_neutrality()` (zero accusatory terms).
4. Ship a clearly marked `[SAMPLE DEMO DATASET]` fixture containing **both** triggering anomalies and clean baseline records so tests prove low false-positive rates.

### D. Improve the Web Dashboard (`web/`)
* Preserve the warm archival editorial broadsheet aesthetic (`#F6F4EF`, Newsreader/Inter/JetBrains Mono) and strict **Epistemic Visual Grammar** (*only peer-verified findings look like solid upright green results; unverified leads stay dashed and italic*).
* Keep bundle clean and accessible across desktop (`1440px`) and mobile (`390px`).
* Verify `cd web && npm run build` passes with zero TypeScript or bundler errors.

---

## 4. Verification Before Submitting Changes

Every contribution should pass all local checks cleanly:

```bash
# 1. Python unit & integration tests (engines, PII scrubber, neutrality linter, hub quorum, CLI)
uv run pytest -v

# 2. Frontend TypeScript check & production bundle build
cd web && npm run build
```

When adding sample datasets or fixtures:
* **Never** accuse any real living individual of wrongdoing. Use synthetic tender IDs and clearly labeled placeholder entity names (`Demo Infra Works Pvt Ltd`, etc.) while keeping Indian statutory rules, ₹ thresholds, and portal URLs 100% authentic.
* Always include `is_demo_sample: true` metadata so the UI renders the `[SAMPLE DEMO DATA]` badge.

---

## 5. License

By contributing to AI Watchdogs India, you agree that your contributions will be licensed under the [MIT License](./LICENSE).
