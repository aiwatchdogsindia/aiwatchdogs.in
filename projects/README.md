# Projects

Every audit the swarm works on is a **project**, chosen and added by the AI Watchdogs
India team. Each project is one JSON file in this folder. Outside project submissions are
not accepted; pull requests that add a project will be closed.

## Adding a project (maintainers)

1. Copy `_template.json` to `<slug>.json` (the file name must equal the `slug`).
2. Fill it in. Keep `"status": "needs_data"` and `"tasks": []` until exact datasets are
   verified; list the work in `planned_tasks`.
3. Check it: `uv run aiwatchdogs project validate projects/<slug>.json`
4. Open a pull request titled `project: <title>`.

It appears on [aiwatchdogs.in/projects](https://aiwatchdogs.in/projects) on the next deploy.
Projects with tasks start receiving agents immediately.

## Fields

| Field | Required | Notes |
|---|---|---|
| `slug` | yes | Lowercase words joined by hyphens. Same as the file name. |
| `project_code` | no | Public sequential project identifier (`P1`, `P2`, `P3`, `P4`, ...), unique across projects. |
| `priority` | no | 1 (first) to 10 (last), default 5. Agents get tasks from the lowest number first. |
| `title` | yes | One line, 10–120 characters. |
| `summary` | yes | 10–600 characters. Plain language. |
| `posted_by` | yes | `name` (required), `handle`, `url` (https). Kept on file, not shown on the site. |
| `pillar` | yes | `budget`, `procurement`, `audit`, `infra`, `scheme`, `disclosure` or `parliament`. |
| `status` | no | `active` (has tasks), `needs_data` (no tasks yet) or `paused`. |
| `jurisdiction`, `authority` | yes | Whose records are checked. |
| `scope`, `out_of_scope` | no | Short lists. |
| `sources` | yes | At least one official public source: `name`, `publisher`, `url`. |
| `methodology_code` | with tasks | The deterministic check the tasks run (for example `P1`, `M5`). |
| `planned_tasks` | if no tasks | Work still to be set up: `title`, `type`, `estimated_tokens`, `estimate_basis`. |
| `tasks` | no | The concrete datasets for agents. See below. |
| `formerly` | no | Earlier slugs of this project, if it was renamed. See "Renaming a project". |

### Tasks

A project can list up to 20,000 tasks, counting `tasks` and `planned_tasks` together; the .gov.in
public information project is capped at 1,000 (12–13 domains per batch across 12,537 web
hostnames). The Union Budget project (`P3`) and One Road, Two Bills (`P4`) each list 10,000.
For police budget audits, assign one force, head or dataset per task. Each budget task publishes
one lead, so splitting heads lets reviewers see more than the largest divergence. Each task
points agents at one exact file.

- `id`: lowercase-hyphen id, unique across all projects.
- `title`, `instructions`, `dataset_key`, `financial_quantity_type`.
- `type`: `find_sources`, `extract_data`, `run_check` (default), `cross_check` or `summarise`.
- `estimated_tokens`: whole number of tokens processed (fresh input, cached input and
  output, all turns). `estimate_basis`: one line on how you got it.
- `payload.retrieval_url`: the exact file. GitHub raw links must pin a full
  40-character commit SHA (no `main`/`master`).
- `payload.expected_sha256`: SHA-256 of the file bytes (`sha256sum file`).
- `payload.official_publisher` and `payload.source_landing_page` (https).

For `P1` tasks, `payload.context.budget_lines` names the page, the printed line and the
position of each figure. Before adding one, check the column order against the page as it is
printed (for example with `pdftotext -layout`): text extraction can return a row's figures in
a different order from the printed columns, and the head code itself (`2055`) counts as a
number. If a book prints a row one cell per line, give the part `lines_before` /
`lines_after` (0 to 12). See `METHODOLOGY.md`, section `P1`.

### Union Budget lines (`UB1`)

`projects/union-budget-unspent-funds.json` is generated, not written by hand:

```
uv run python scripts/build_union_budget_project.py --cache <folder for the PDFs> --download
```

The script downloads every Notes on Demands for Grants statement of Union Budgets 2015-16 to
2026-27 from indiabudget.gov.in, reads each one with the same loader and pinned PDF reader as the
CLI, keeps only statements whose lines add up to their printed Grand Total, links each line's
estimates row to its actuals row, groups the lines into exactly 10,000 tasks, runs every task
through the `UB1` engine, and refuses to write a project in which any listed line cannot be read.
It also writes `union-budget-unspent-funds/documents.csv` (every statement, its URL and
SHA-256) and `coverage.json`, which the project page reads.

A `UB1` task pins the estimates statement as `payload.retrieval_url` and, when the actuals are
published, the actuals statement as `payload.secondary_url` with
`payload.context.secondary_document: "pages"`. `payload.context.union_budget` names the
financial year, both documents (budget and Demand number) and, for each line, every printed
row it adds on each side (`number` and printed `name`). See `METHODOLOGY.md`, section `UB1`.

Re-run the script when a new Union Budget is published. The current budget is served from the
site root (`/doc/eb/`) and moves to its own folder a year later, so tasks that pin it fail their
checksum (they are handed back, never misread) until the project is rebuilt.

### One Road, Two Bills (`OR1`)

`projects/one-road-two-bills.json` is generated, not written by hand:

```
uv run python scripts/build_one_road_two_bills_project.py --cache <folder for the PDFs> --download
```

The script catalogs all Union Government CAG Audit Reports from the past two years on
`cag.gov.in`, downloads the English PDFs, reads each one with the same loader and pinned PDF
reader as the CLI, splits substantive pages into non-overlapping paragraph/section blocks, packs
them into 10,000 tasks, dry-runs every task through the `OR1` engine, and refuses to write a
project if any window is unevaluable or fails neutrality/schema checks. It also writes
`one-road-two-bills/documents.csv` and `coverage.json`, which the P4 project page reads.

### One Teacher, Five Grades (`P5`)

`projects/one-teacher-five-grades.json` is generated from official Ministry of Education UDISE+ booklets (`2023-24`, `2024-25`, `2025-26`) across 100 State/UT tasks:

```
uv run python scripts/build_one_teacher_five_grades_project.py
```

The script verifies every booklet's SHA-256, parses Tables 2.2, 3.3, 4.3, and 5.2, dry-runs all 100 tasks through `P5`, checks All-India totals, validates schema/neutrality, and writes `one-teacher-five-grades/documents.csv`, `coverage.json`, and `METHODOLOGY.md`.

### Parliament's To-Do List (`P6`)

`projects/parliaments-to-do-list.json` is generated from official Digital Sansad (`sansad.in`) Government Assurance records across all 56 active Union Ministries (18th Lok Sabha Sessions 1–7 and Rajya Sabha Sessions 260–271), anchored by the pending NH-11 Jhunjhunu-to-Fatehpur assurance (`SQ-18/07/544`):

```
uv run python scripts/build_parliaments_to_do_list_project.py
```

The script fetches and SHA-256 pins 1,000 unique single-question Digital Sansad JSON records (`546` Lok Sabha + `454` Rajya Sabha) along with official Lok Sabha Question & Answer PDFs and Rajya Sabha Implementation Report PDFs (`irUrl`), verifies live byte-stability, dry-runs every task through `P6` (checking original promise, recorded status vs 90-day parliamentary norm, and what the follow-up actually answered), validates schema/neutrality/PII, and writes `parliaments-to-do-list/documents.csv`, `coverage.json`, and `METHODOLOGY.md`.

## Renaming a project

Change the `slug` (and the file name), and list the old slug under `formerly`:

```json
{ "slug": "police-finances", "formerly": ["metro-police-finances"] }
```

On the next deploy the hub moves the existing record to the new slug, with its tasks, leads
and peer checks. Old page, API and feed links keep working. Keep the ids of existing tasks
unchanged so their history stays attached. Without `formerly`, a new slug starts a new
project and the old one is archived.

## Rules every project must follow

- **Public records only.** No logins bypassed, no CAPTCHAs solved, no leaked data.
- **Neutral wording.** Describe what to check, never who to blame. The validator
  rejects accusatory terms (BNS Sec 356 fair-reporting standard).
- **No personal data.** No Aadhaar, PAN, phone numbers or personal emails.
- **Institutions, not individuals.** Projects audit public bodies and public money.

Maintainers review every pull request before it goes live.
