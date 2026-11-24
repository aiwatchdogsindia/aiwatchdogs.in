## Summary

<!-- Briefly describe what this PR changes and why. Link any related issue or discussion (e.g. Closes #123). -->

## Type of Change

- [ ] Bug fix
- [ ] New Indian public data connector (`src/aiwatchdogs/connectors/`)
- [ ] Deterministic audit methodology addition or refinement (`src/aiwatchdogs/engines/`)
- [ ] Coordinator Hub / CLI / API improvement (`src/aiwatchdogs/hub/`, `src/aiwatchdogs/cli/`)
- [ ] Web dashboard / UI improvement (`web/`)
- [ ] Documentation / governance / CI

## Civic-Audit, Legal & Privacy Checklist

<!-- Check every item that applies; mark N/A inline only if genuinely unaffected. -->

- [ ] **Deterministic math:** All numeric anomaly calculations run deterministically in pure Python with explicit `as_of_date`, preserving missing/unknown fields as `None`/`unevaluable` rather than coercing to `0` or `cleared`.
- [ ] **Neutrality (BNS Sec 356):** Finding titles, summaries, and fixtures pass `lint_neutrality()` with strictly factual public-record wording and at least one realistic `benign_explanations` entry. No accusations against living individuals.
- [ ] **Privacy & PII (DPDP Act 2023 / Aadhaar Act 2016):** Extracted text and records pass through `scrub_pii_in_obj()` (Verhoeff Aadhaar, PAN, bank/IFSC, personal phone/email); no raw citizen PII is committed in code, tests, or PR descriptions.
- [ ] **Lawful access (IT Act 2000 Sec 43):** Only Tier 1 / Tier 2 open sources or local user-exported files (`audit-file`) are used — zero CAPTCHA solving, login bypass, or abusive scraping.
- [ ] **Provenance & pinned snapshots:** Any external mirror URLs are pinned to immutable 40-char commit SHAs, record `official_publisher` / `source_landing_page` / `mirror_name` / `retrieval_url` separately, and verify SHA-256 fail-closed.
- [ ] **Human-in-the-loop RTIs:** No automated RTI/CPGRAMS/CVC submission; any RTI draft changes preserve the `<= 500`-word Rule 3 limit and Central vs. State portal routing.
- [ ] **Epistemic visual grammar (if `web/` changed):** Peer-verified vs. `peer_review` (dashed italic), `cleared`, `unevaluable`, `disputed`/`retracted`, and `rti_filed` states remain visually distinct, keyboard-accessible, and WCAG AA compliant.

## Verification

- [ ] `uv sync --locked && uv run pytest -v` passes with offline fixtures (no live `.gov.in` dependency in CI)
- [ ] `cd web && npm ci && npx tsc --noEmit && npm run build` passes (if `web/` touched)
- [ ] Verified `/join.sh` renders valid POSIX `sh -n` syntax (if onboarding templates touched)
