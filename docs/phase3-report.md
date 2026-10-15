# Phase 3 report: executor + two-stage gate (Contabo, 2026-10-15)

Host: `contabo-tally` (Ubuntu 24.04, Docker 29.1.3, no /dev/kvm). gVisor `runsc` release-20261005.0
installed to /usr/local/bin and registered via /etc/docker/daemon.json with `systemctl reload docker`
(no daemon restart; the 12 existing containers kept running).

Deployed: `/opt/agent-factory-2`, systemd `factory-executor` (user `factory-exec`, socket
`/run/factory/executor.sock`, runs in /var/lib/factory/runs), `af-fetch` internal network and
`af-proxy` allow-list proxy (pypi.org, files.pythonhosted.org only) at 172.30.77.2.

## Acceptance (SPEC section 12, phase 3)
| Item | Result |
| --- | --- |
| Canary blocked (network, DNS, host files, docker.sock, root-fs write, setuid) | PASS (test_canary_blocked) |
| Agent commands: no network, workspace only, image allow-list, path escape denied, timeout kill | PASS |
| Stage 1 proxy denies non-allow-listed host, no direct route, real wheel fetched | PASS |
| Tamper: deleted/modified test, count drop, skip marker, Fixer record, failing test, secret in code | PASS |
| Daemon over Unix socket, SandboxPort and GatePort satisfied | PASS |

## Findings and limits
- gVisor does not see Docker's embedded DNS (127.0.0.11): the proxy is addressed by fixed IP.
- The executor refuses to run as root (sandbox uid = executor uid).
- JUnit XML is written by pytest inside the stage-2 sandbox, so hostile test code could forge it.
  The gate cross-checks it against pytest's own stdout counts; a stronger fix is a gate-owned
  pytest plugin or running collection in a separate container (open item for phase 4).
- Stage 1 runs `pip download --only-binary=:all:` only; sdists fail by design. Java/Maven gate not built.
- Optional extras done: offline secret scan. Not done: static-analysis rule pack, dependency vuln DB.
- Spec section 11 advises a separate VPS for untrusted code. This ran on the shared Contabo host by
  decision; gVisor + no-network + non-root is the compensating control.
- `docker` group membership of `factory-exec` is root-equivalent on the host.
