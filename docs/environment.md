# Environment probe (phase 0)

Probed 2026-10-14 in a Claude Code cloud session (Firecracker microVM, Linux 6.18, root, 4 vCPU, 16 GB RAM, ~31 GB free disk, Python 3.11 default, 3.12.3 at /usr/bin/python3.12, uv 0.11).

| Question | Result |
| --- | --- |
| Docker or podman | Docker CLI 29.8.2 and `dockerd`, `containerd`, `runc` are installed. No daemon runs at session start; `dockerd` started manually as root and worked (overlayfs, cgroup v1). podman: absent. |
| Container with `--network none` | **Works** (daemon started by hand). Verified with a locally built image (`docker import` of `/usr/bin/true` plus its libraries); `docker run --network none` exited 0. No image was pulled from any registry. |
| `runsc` (gVisor) | **Absent.** `--runtime runsc` fails with "unknown or invalid runtime name". Only `runc` is registered. Not installed because that needs an external download. |
| `/dev/kvm` (Kata) | Absent. |
| Outbound PyPI | **Works** through the session proxy (`pypi.org` 200). Installs via `uv` succeeded. |
| Outbound Maven Central | Works (`repo.maven.apache.org` 200). |
| Outbound generic HTTPS host | **Blocked.** `https://example.com` failed at the proxy (no response); egress is allow-list only. |
| Gemini / LangSmith hosts | Not exercised. Two unauthenticated reachability requests were sent to the Gemini API host (got 404, so reachable) and the LangSmith API host (failed). No key was sent. |
| How secrets are provided | As environment variables of the session (names seen include GitHub tokens and, unexpectedly, `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`, length 20 for the id). They were not used or printed. No Gemini, Google or LangSmith variables are set. Treat the session as unsuitable for real keys. |
| Maximum session length | **Unknown / not verifiable from inside the session.** Container was up under a minute at probe time. |
| Proxy / TLS | HTTPS goes through a local proxy with its own CA at `/root/.ccr/ca-bundle.crt`; do not disable verification. |

## Consequences for the plan

- Phases 1 and 2 (graph on fakes, then Gemini adapter) can run here. Postgres checkpointer tests need a Postgres server; Docker would have to pull `postgres:16`, which is not possible without a registry. Use the in-memory saver here and Postgres on the VPS (or install Postgres via apt if the proxy allows it).
- Phase 3 (executor, gVisor, two-stage gate) must be proven on the VPS: no `runsc`, no KVM, and no registry here.
- Real Gemini calls and LangSmith tracing stay off in this environment.
