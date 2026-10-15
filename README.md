# Agent Factory 2

A spec-to-code factory: you give it a specification, a graph of role agents (Architect, Coder,
Tester, Fixer, Reviewer, Docs) builds a codebase, an independent sandboxed gate proves it, and a
human approves by emailed link. Gemini only, LangGraph for the state machine, no AWS.

Educational project. See `docs/SPEC.md` for the design and `docs/phase3-report.md`,
`docs/phase4-report.md`, `docs/phase5-report.md` for what is built, verified and still open.

## Quick start (laptop, no Docker, no keys)
```bash
uv sync
make check                 # ruff + mypy + pytest (includes the golden-spec replay)
make release               # same, plus the explicit baseline comparison
```

## Pieces
| Path | What |
| --- | --- |
| `src/factory/graph.py`, `nodes/` | The LangGraph pipeline and role prompts |
| `src/factory/executor/`, `gate/` | Unprivileged executor daemon, gVisor sandbox, two-stage gate |
| `src/factory/guardrails/` | Deterministic scanners plus a separate classifier call |
| `src/factory/evidence/`, `control/` | Hash-chained audit table, evidence files, approval service |
| `src/factory/evals/`, `evals/` | Golden specs, recordings, baselines, regression gate |
| `executor/` | Host setup: proxy, sandbox image, systemd units |

## Running for real (VPS with Docker + gVisor)
See `docs/phase3-report.md` (executor), `docs/phase4-report.md` (Postgres, approval, email) and
`executor/setup.sh`. Real model runs need `AF_GEMINI_KEY` and `AF_GEMINI_BASE_URL` in a
worker-only environment file.
