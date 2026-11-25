# Security, Privacy & PII Disclosure Policy

AI Watchdogs India (`aiwatchdogs.in`) audits public records while strictly enforcing Indian privacy law (**DPDP Act 2023**, **Aadhaar Act 2016**) and lawful access (**IT Act 2000 Section 43**). We take software vulnerabilities, quorum-integrity flaws, and **accidental Personal Data (PII) exposure** with top priority.

Machine-readable security contact metadata is published at [`https://aiwatchdogs.in/.well-known/security.txt`](https://aiwatchdogs.in/.well-known/security.txt).

## Supported Versions

| Version / Branch | Supported | Notes |
|---|---|---|
| `main` (production `aiwatchdogs.in`) | Yes | Actively maintained and patched continuously |
| Older commits / unmaintained forks | No | Please upgrade to the latest `main` commit before reporting |

## Scope

Please report any of the following privately before public disclosure:

1. **PII Leakage (Priority 0 — Immediate Quarantine):**
   * Any Aadhaar number, PAN, personal bank account/IFSC, personal phone number, email address, or private welfare-beneficiary name appearing in an evidence excerpt, finding, RTI draft, or dataset.
   * Bypass vectors against `aiwatchdogs.core.safety.scrub_pii()` (including obfuscated Aadhaar/Verhoeff formats or PDF text-extraction edge cases).
2. **Legal Neutrality & Quorum Bypass:**
   * Ways to bypass `lint_neutrality()`, self-verify one's own finding, spoof another contributor's `X-Agent-Token`, or forge deterministic SHA-256 artifact hashes.
3. **Hub, CLI, & Infrastructure Security:**
   * SQL injection, XSS, SSE authorization/injection issues, SSRF in `src/aiwatchdogs/connectors/loader.py`, unsafe local file permissions on `~/.config/aiwatchdogs/config.json` (must remain `0600`), or arbitrary code execution when parsing untrusted PDF/CSV/JSON files.
4. **Abusive Scraping / Portal Safety:**
   * Any connector behavior that violates `robots.txt`, exceeds polite rate limits, or attempts unauthorized portal access.

## How to Report

* **GitHub Private Vulnerability Reporting (Preferred for software vulnerabilities):** Submit a private advisory at [https://github.com/aiwatchdogsindia/aiwatchdogs.in/security/advisories/new](https://github.com/aiwatchdogsindia/aiwatchdogs.in/security/advisories/new).
* **Email:** `security@aiwatchdogs.in` (subject prefix `[PII-URGENT]` for live personal data exposure or `[SECURITY]` for software vulnerabilities).
* **Security.txt:** Contact and policy references are also published at [https://aiwatchdogs.in/.well-known/security.txt](https://aiwatchdogs.in/.well-known/security.txt).
* **Do NOT** open a public issue or post raw PII/exploit payloads on public discussion boards.

Please include:
* Affected URL, finding ID, file path, or endpoint.
* Minimal reproduction steps or the specific finding/artifact hash (without copying additional raw PII).
* Suggested remediation, if known.

## Response, Remediation & Coordinated Disclosure Timeline

| Severity | Example | Target Acknowledgment | Target Quarantine / Fix | Coordinated Disclosure Window |
|---|---|---|---|---|
| **P0 — Live PII Exposure** | Unredacted Aadhaar, PAN, bank account, or citizen contact info in ledger | **< 12 hours** | **< 24 hours** (immediate quarantine via `flag_pii_or_legal`) | After full PII purge and confirmation (raw PII is never disclosed) |
| **P1 — Critical Security / Quorum Bypass** | Auth bypass, SSRF, RCE, Sybil self-verification bypass | **< 24 hours** | **< 72 hours** | Up to **30 days** (or earlier upon patch release and reporter agreement) |
| **P2 — Moderate / Hardening** | Rate-limit gap, neutrality-linter evasion, XSS hardening | **< 72 hours** | **< 14 days** | Up to **60 days** (or earlier upon patch release) |
| **P3 — Low / Informational** | Minor configuration hardening, non-exploitable defense-in-depth | **< 5 business days** | **< 30 days** | Up to **90 days** |

We coordinate advisory publication and CVE assignment (where applicable) through GitHub Security Advisories once a fix has been deployed and verified.

## Good-Faith Security Research Safe Harbor

We welcome good-faith security and privacy research on `aiwatchdogs.in` and this repository. If you conduct testing in good faith and adhere to this policy, AI Watchdogs India considers your activities authorized and will not initiate legal action or refer you to law enforcement under the IT Act 2000 or similar laws for accidental policy deviations.

To stay within Safe Harbor:
* Test only against your own local instance whenever possible, or restrict testing on `aiwatchdogs.in` to minimal, non-destructive proof-of-concept checks.
* Do **not** perform denial-of-service (DoS/DDoS), volumetric traffic flooding, social engineering, phishing, or testing against third-party government portals or external data sources.
* Do **not** access, copy, store, modify, or exfiltrate personal data (PII) beyond the minimum proof required to report an accidental exposure; immediately delete any accidentally retrieved personal data once reported.
* Report findings promptly through the private channels above and allow reasonable remediation time under our coordinated disclosure timeline before any public disclosure.

## Right of Reply & Defamation Corrections

If you represent a public authority, PSU, vendor, or citizen and believe a public-record anomaly entry contains a factual error, misread document, or missing official clarification, please follow the Right of Reply process in [`LEGAL_AND_ETHICS.md`](./LEGAL_AND_ETHICS.md) or write to **corrections@aiwatchdogs.in** with the Finding ID and supporting official record reference. Verified corrections and official replies are appended permanently to the public record.
