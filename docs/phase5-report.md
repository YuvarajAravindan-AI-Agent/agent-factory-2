# Phase 5 report: tracing, golden evals, regression gate (2026-10-15)

## Acceptance (SPEC section 12, phase 5)
| Item | Result |
| --- | --- |
| Three golden specs | PASS: prior-auth (healthcare), loan-prequal (finance), usage-quota (telecom), all synthetic, all pass the Layer A scan |
| Baseline recorded | PASS: `evals/baseline.real.json` (real Gemini, executor/gVisor gate) and `evals/baseline.scripted.json` (replayed recordings) |
| Regression blocks release | PASS: an injected bug in the recorded loan-prequal coder output makes `factory evals compare` exit 1 (gate pass rate 100% -> 67%, run crash, no approval); also a unit test. `make release` / `scripts/release_check.sh` fails the build on it |
| Replay is faithful | PASS: replay on the real Contabo executor reproduces the real-model baseline exactly (44/29/31 tests, 93,698 tokens, 1 fix round) |

## Real-model baseline (gemini-4-argon strong, gemini-3.8-flash fast, via vertex-gateway)
| Spec | Reached approval | Tests | Fix rounds (test/review) | Tokens |
| --- | --- | --- | --- | --- |
| prior-auth | yes | 44 | 0/0 | 27,411 |
| loan-prequal | yes | 29 | 0/0 | 25,952 |
| usage-quota | yes | 31 | 1/0 | 40,335 |
Total 93,698 tokens, 575 s wall clock, 0 blocking review findings.

## What was built
- `factory.evals`: dataset loader, runner (metrics: reached approval, tests, fix rounds, blockers,
  tokens, model calls, wall time), regression policy (`evals/policy*.yaml`), CLI
  (`factory evals run|compare`), Docker-free local backend for REPLAYED code only (refused with a live model).
- `RecordingModel` / `ReplayModel`: record real responses once, replay deterministically; stale
  recordings are counted as prompt drift (reported, not fatal).
- `factory.tracing`: LangSmith `@traceable` wrappers on model calls, gate stages and executor calls.
  Off unless `LANGSMITH_TRACING=true`. Metadata: run ID, role, tier, model ID, spec hash, loop
  counters, factory version, prompts version. `FACTORY_TRACE_HIDE_IO=true` sends only SHA-256 and length.
- `prompts_version` (hash of prompts.py) and `factory_version` recorded in manifest.json and in baselines.
- Optional `factory.evals.langsmith_sync` (dataset + `evaluate` + three evaluators).

## Fixes found while moving to a real model (these were invisible on fakes)
- Gemini 3.x returns content blocks with thought signatures; the adapter stringified the list.
- Phase 1 prompts were placeholders: reviewer never saw the code, tester never saw the source.
- The graph never recorded the gate's test manifest, so tamper checks could not work.
- If the first test run failed, the Tester re-authored the tests next round (now written exactly once).
- Fixer can no longer write test files at all (paths validated per role); bad paths are audited.

## NOT verified
- **LangSmith itself:** no API key was used. Trace metadata and hide-IO are verified against a
  captured client only; dataset sync / `evaluate` are untested against the hosted service.
  Check data region, masking and self-hosting terms before using it with a client's material.
- Only three specs, one run each: real-model variance is unmeasured (policy.real.yaml has wide tolerances).
- Prompt drift is 1 in replay: the Fixer prompt embeds pytest output (timings, paths).
- Replayed code runs unisolated on the laptop in the local backend; only for reviewed recordings.
- Still open from earlier phases: Java/Maven gate, static-analysis rule pack, dependency vulnerability
  scan, forgeable JUnit.
