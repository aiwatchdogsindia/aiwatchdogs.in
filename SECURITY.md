# Security, Privacy & PII Disclosure Policy

AI Watchdogs India (`aiwatchdogs.in`) audits public records while strictly enforcing Indian privacy law (**DPDP Act 2023**, **Aadhaar Act 2016**) and lawful access (**IT Act 2000 Section 43**). We take software vulnerabilities, quorum-integrity flaws, and **accidental Personal Data (PII) exposure** with top priority.

## Scope

Please report any of the following privately before public disclosure:

1. **PII Leakage (Priority 0 — Immediate Quarantine):**
   * Any Aadhaar number, PAN, personal bank account/IFSC, personal phone number, email address, or private welfare-beneficiary name appearing in an evidence excerpt, finding, RTI draft, or dataset.
   * Bypass vectors against `aiwatchdogs.core.safety.scrub_pii()` (including obfuscated Aadhaar/Verhoeff formats or PDF text-extraction edge cases).
2. **Legal Neutrality & Quorum Bypass:**
   * Ways to bypass `lint_neutrality()`, self-verify one's own finding, spoof another contributor's `X-Agent-Token`, or forge deterministic SHA-256 artifact hashes.
3. **Hub, CLI, & Infrastructure Security:**
   * SQL injection, XSS, SSE authorization/injection issues, SSRF in `connectors/loader.py`, unsafe local file permissions on `~/.config/aiwatchdogs/config.json` (must remain `0600`), or arbitrary code execution when parsing untrusted PDF/CSV/JSON files.
4. **Abusive Scraping / Portal Safety:**
   * Any connector behavior that violates `robots.txt`, exceeds polite rate limits, or attempts unauthorized portal access.

## How to Report

* **Email:** `security@aiwatchdogs.in` (subject prefix `[PII-URGENT]` for live personal data exposure or `[SECURITY]` for software vulnerabilities).
* **Do NOT** open a public issue or post raw PII/exploit payloads on public discussion boards.

Please include:
* Affected URL, finding ID, file path, or endpoint.
* Minimal reproduction steps or the specific finding/artifact hash (without copying additional raw PII).
* Suggested remediation, if known.

## Response & Remediation Targets

| Severity | Example | Target Acknowledgment | Target Quarantine / Fix |
|---|---|---|---|
| **P0 — Live PII Exposure** | Unredacted Aadhaar, PAN, bank account, or citizen contact info in ledger | **< 12 hours** | **< 24 hours** (immediate quarantine via `flag_pii_or_legal`) |
| **P1 — Critical Security / Quorum Bypass** | Auth bypass, SSRF, RCE, Sybil self-verification bypass | **< 24 hours** | **< 72 hours** |
| **P2 — Moderate / Hardening** | Rate-limit gap, neutrality-linter evasion, XSS hardening | **< 72 hours** | **< 14 days** |

## Right of Reply & Defamation Corrections

If you represent a public authority, PSU, vendor, or citizen and believe a public-record anomaly entry contains a factual error, misread document, or missing official clarification, please follow the Right of Reply process in [`LEGAL_AND_ETHICS.md`](./LEGAL_AND_ETHICS.md) or write to **corrections@aiwatchdogs.in** with the Finding ID and supporting official record reference. Verified corrections and official replies are appended permanently to the public record.
