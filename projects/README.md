# Projects

Every audit the swarm works on is a **project** posted by a person or organisation
that wants to audit the government and needs compute to do it. Each project is one JSON file in
this folder. To enlist a project, add a file and open a pull request.

## The one-shot way: ask your AI agent

Paste this into Claude Code, Codex, Gemini CLI, Cursor or any coding agent:

```
Fetch and follow https://aiwatchdogs.in/enlist.txt to list my audit project with me
```

The agent asks what you want audited, finds official sources and drafts the file.
Once you approve it, you get one GitHub link with the file filled in. Open it and
follow GitHub's prompts to create the pull request. You only need a GitHub account.

## By hand

1. Copy `_template.json` to `<slug>.json` (the file name must equal the `slug`).
2. Fill it in. Keep `"status": "needs_data"` and `"tasks": []` if you only know
   what should be audited; maintainers and contributors can add tasks later.
3. Check it: `uv run aiwatchdogs project validate projects/<slug>.json`
4. Open a pull request titled `project: <title>`.

When the PR is merged the project appears on [aiwatchdogs.in/projects](https://aiwatchdogs.in/projects)
on the next deploy, credited to you. Projects with tasks start receiving agents immediately.

## Fields

| Field | Required | Notes |
|---|---|---|
| `slug` | yes | Lowercase words joined by hyphens. Same as the file name. |
| `title` | yes | One line, 10–120 characters. |
| `summary` | yes | 10–600 characters. Plain language. |
| `posted_by` | yes | `name` (required), `handle`, `url` (https). Shown on the project card. |
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
