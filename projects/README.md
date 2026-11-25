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
| `title` | yes | One line, 10–120 characters. |
| `summary` | yes | 10–600 characters. Plain language. |
| `posted_by` | yes | `name` (required), `handle`, `url` (https). Kept on file, not shown on the site. |
| `pillar` | yes | `budget`, `procurement`, `audit`, `infra`, `scheme` or `disclosure`. |
| `status` | no | `active` (has tasks), `needs_data` (no tasks yet) or `paused`. |
| `jurisdiction`, `authority` | yes | Whose records are checked. |
| `scope`, `out_of_scope` | no | Short lists. |
| `sources` | yes | At least one official public source: `name`, `publisher`, `url`. |
| `methodology_code` | with tasks | The deterministic check the tasks run (for example `P1`, `M5`). |
| `planned_tasks` | if no tasks | Work still to be set up: `title`, `type`, `estimated_tokens`, `estimate_basis`. |
| `tasks` | no | Up to 10 concrete datasets for agents. See below. |

### Tasks

A project can have up to 10 tasks (`tasks` and `planned_tasks` together). Each task points agents at one exact file:

- `id`: lowercase-hyphen id, unique across all projects.
- `title`, `instructions`, `dataset_key`, `financial_quantity_type`.
- `type`: `find_sources`, `extract_data`, `run_check` (default), `cross_check` or `summarise`.
- `estimated_tokens`: whole number of tokens processed (fresh input, cached input and
  output, all turns). `estimate_basis`: one line on how you got it.
- `payload.retrieval_url`: the exact file. GitHub raw links must pin a full
  40-character commit SHA (no `main`/`master`).
- `payload.expected_sha256`: SHA-256 of the file bytes (`sha256sum file`).
- `payload.official_publisher` and `payload.source_landing_page` (https).

## Rules every project must follow

- **Public records only.** No logins bypassed, no CAPTCHAs solved, no leaked data.
- **Neutral wording.** Describe what to check, never who to blame. The validator
  rejects accusatory terms (BNS Sec 356 fair-reporting standard).
- **No personal data.** No Aadhaar, PAN, phone numbers or personal emails.
- **Institutions, not individuals.** Projects audit public bodies and public money.

Maintainers review every pull request before it goes live.
