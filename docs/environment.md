# Environment probe (phase 0)

Probed 2026-10-14 in a Claude Code cloud session (Linux 6.18, x86_64, 4 vCPU, 16 GB RAM, root, no `/dev/kvm`).

| Question | Result |
| --- | --- |
| docker or podman | Docker CLI 29.8.2 and `dockerd`/`containerd` are installed. **No daemon is running by default** (`/var/run/docker.sock` absent); `dockerd` started manually as root and worked. podman: not installed. |
| Container with `--network none` | **Works** (runc), once `dockerd` is started by hand. No image could be pulled (no registry probed), so the test used a locally imported minimal image running `/bin/true`: exit 0. |
| `runsc` (gVisor) | **Not available**: binary absent, only `runc` runtime registered. Not downloaded (would touch an external service). Phase 3 needs the VPS. |
| Outbound PyPI | **Yes**: `pypi.org` 200 and package installs via `uv` work. (`files.pythonhosted.org/` root returns 404, which is normal.) |
| Maven Central | Yes (200). |
| Generic HTTPS host | **No**: `https://example.com/` returns 403 from the egress proxy (CONNECT refused). Egress is allow-listed by org policy. Gateway URL not probed. |
| Secrets | Provided as environment variables (GitHub token, platform proxy/session tokens, and AWS-named variables exist in the environment; lengths only: GH_TOKEN 14 chars, AWS_ACCESS_KEY_ID 20 chars). They were not used or logged. No Gemini or LangSmith key is present; none was supplied. |
| Maximum session length | **Could not determine** from inside the session; no limit is exposed. Treat sessions as ephemeral and push work often. |
| Python | System 3.12.3 present; project pins `>=3.12,<3.13`; `uv` 0.11.32 available. |

Consequences: phases 1 and 2 are feasible here (fakes, in-memory saver; Postgres container is possible only if an image can be obtained, otherwise test Postgres on the VPS). Phase 3 (gVisor, two-stage gate) must run on the VPS.

## Pinned versions (checked on PyPI 2026-10-14)

| Package | Pinned | Notes |
| --- | --- | --- |
| langgraph | 1.2.15 | `StateGraph`, `interrupt`, `Command` import fine |
| langgraph-checkpoint-postgres | 3.1.3 | `PostgresSaver` import fine |
| langchain-core | 1.6.10 | |
| langchain-google-genai | 4.4.2 | `ChatGoogleGenerativeAI` takes `google_api_key`, `model`, `base_url`; `with_structured_output` defaults to `method="json_schema"` |
| langsmith | 0.14.8 | `traceable`, `evaluate` import fine |

API differences from SPEC.md: none found at import level; package names match. Behaviour of Gemini calls and Postgres checkpointing is unverified until phases 1 and 2. Tracing stays off (no `LANGSMITH_*` variables set).
