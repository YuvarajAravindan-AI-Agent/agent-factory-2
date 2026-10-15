# Security

This is an educational project and is not yet audited. Do not run it on untrusted specs in
production, and do not point it at real personal or financial data (use synthetic data only).

Report a vulnerability privately to the owner (not in a public issue). Known open gaps are listed
in the phase reports under `docs/` (forgeable JUnit, no Java gate, no SAST or dependency scan).

Never commit secrets: `.env*` files are ignored; the executor and control service read them from
the host. If a secret is ever committed, rotate it first, then remove it from history.
