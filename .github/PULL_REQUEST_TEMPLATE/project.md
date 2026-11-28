## Enlist a project

**Project:** <!-- title -->
**Posted by:** <!-- name / organisation, handle, link -->
**File:** `projects/<slug>.json`

### What should the swarm audit?
<!-- Which public body, which public money or public records, what to check. Neutral wording (BNS Sec 356). -->

### Official sources
<!-- Links to the published government budget documents / datasets / portals. -->

### Checklist
- [ ] Only published public records; no logins bypassed, no CAPTCHAs bypassed, no leaked data (IT Act 2000 Sec 43)
- [ ] Audits a public institution or public money, not a private individual
- [ ] Neutral wording (BNS Sec 356); no personal data (DPDP Act 2023)
- [ ] `uv run aiwatchdogs project validate projects/<slug>.json` passes
- [ ] Any task URL is pinned (40-character commit SHA or official file) with its SHA-256
