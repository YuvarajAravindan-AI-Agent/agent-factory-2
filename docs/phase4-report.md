# Phase 4 report: evidence, guardrails, audit chain, approval (2026-10-15)

Verified on Contabo against a real Postgres 16 (`af-postgres`, 127.0.0.1:5433, own volume,
password in /opt/agent-factory-2/.env mode 600): 8 live tests, plus 43 unit tests locally.

## Acceptance (SPEC section 12, phase 4)
| Item | Result |
| --- | --- |
| Audit chain verifies | PASS: 40 concurrent writers produce one valid chain |
| Chain detects tampering | PASS: edited row and deleted row are located by sequence number |
| Append-only | PASS: UPDATE, DELETE, TRUNCATE raise (trigger). A privileged owner who disables the trigger is caught by the chain check, not prevented |
| Guardrails layer A | PASS: Luhn cards, IBAN mod-97, NHS mod-11, Aadhaar Verhoeff, SSN, PAN, secrets; invalid checksums are not flagged |
| Guardrails layer B | PASS on fakes: separate classifier call, fails closed on unparseable output, never sees an artifact layer A rejected |
| Evidence layout | PASS: manifest.json (spec hash, prompt hashes, model IDs, tokens, loop counters), guardrail in/out, package.zip + SHA256SUMS (deterministic, re-verifiable), approval.json |
| Approval by emailed link | PASS with a mail sink: signed link, GET shows confirmation page and changes nothing, POST decides once, expiry honoured, new package invalidates old link, unknown run and bad token both 404 |
| Full run | PASS: graph on Postgres evidence + approval, resumed from the HTTP click, chain verifies |

## NOT verified
- **A real email was never sent.** SmtpMailer is untested against a live server; no SMTP
  credentials were used. Needs AF_SMTP_* env and one real send.
- The control service is not deployed or public: no Caddy route yet. Only /approve/* should be public.
- No real Gemini traffic (models.yaml tiers are still TBD placeholders).
- Postgres role REVOKE for the application role not applied (tests use the owner role).
- Nightly chain re-verification is `factory verify-audit` (exit code 2 on break); not scheduled.
- `factory expire` sweeps lapsed approvals; not scheduled.

## Limits
- Approver identity is "holder of the emailed link" (recorded as the configured approver address).
- Layer A is regex + checksum; it will miss obfuscated data. The executor policy is the real control.
