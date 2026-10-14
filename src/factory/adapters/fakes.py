"""In-memory fakes for every port. No network, no real credentials."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import UTC, datetime

from factory.ports.models import (
    ApprovalDecision,
    ApprovalRecord,
    CommandLimits,
    CommandResult,
    GateResult,
    ModelRequest,
    ModelResponse,
    Usage,
)


class FakeModel:
    """Replays scripted responses per role; records every request."""

    def __init__(self, scripts: dict[str, list[str]] | None = None) -> None:
        self._scripts = {k: list(v) for k, v in (scripts or {}).items()}
        self.requests: list[ModelRequest] = []

    def invoke(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        queue = self._scripts.get(request.role)
        text = queue.pop(0) if queue else f"fake-{request.role}-output"
        return ModelResponse(
            model_id=f"fake-{request.tier.value}",
            text=text,
            usage=Usage(input_tokens=len(request.prompt.split()), output_tokens=len(text.split())),
        )


class FakeSandbox:
    def __init__(self, results: list[CommandResult] | None = None) -> None:
        self._results = list(results or [])
        self.calls: list[tuple[str, str, list[str]]] = []
        self._files: dict[str, dict[str, str]] = defaultdict(dict)

    def run(
        self, run_id: str, step: str, image: str, command: list[str], limits: CommandLimits
    ) -> CommandResult:
        self.calls.append((run_id, step, command))
        return self._results.pop(0) if self._results else CommandResult(exit_code=0)

    def write_file(self, run_id: str, path: str, content: str) -> None:
        self._files[run_id][path] = content

    def read_file(self, run_id: str, path: str) -> str:
        return self._files[run_id][path]

    def list_dir(self, run_id: str, path: str) -> list[str]:
        prefix = path.rstrip("/") + "/" if path not in ("", ".") else ""
        return sorted(p for p in self._files[run_id] if p.startswith(prefix))


class FakeGate:
    def __init__(self, results: list[GateResult] | None = None) -> None:
        self._results = list(results or [])
        self.calls: list[str] = []

    def run_gate(self, run_id: str) -> GateResult:
        self.calls.append(run_id)
        if self._results:
            return self._results.pop(0)
        return GateResult(stage1_ok=True, stage2_ok=True, tests_total=1)


class FakeApproval:
    def __init__(self, decision: ApprovalDecision | None = ApprovalDecision.APPROVED) -> None:
        self._decision = decision
        self.requested: list[tuple[str, str]] = []

    def request_approval(self, run_id: str, package_sha256: str) -> None:
        self.requested.append((run_id, package_sha256))

    def poll_decision(self, run_id: str) -> ApprovalRecord | None:
        if self._decision is None:
            return None
        return ApprovalRecord(
            run_id=run_id,
            decision=self._decision,
            approver="fake-approver",
            decided_at=datetime.now(UTC).isoformat(),
        )


class FakeEvidence:
    def __init__(self) -> None:
        self._files: dict[str, dict[str, bytes]] = defaultdict(dict)

    def write(self, run_id: str, relpath: str, content: bytes) -> str:
        self._files[run_id][relpath] = content
        return hashlib.sha256(content).hexdigest()

    def read(self, run_id: str, relpath: str) -> bytes:
        return self._files[run_id][relpath]

    def manifest(self, run_id: str) -> dict[str, str]:
        return {p: hashlib.sha256(c).hexdigest() for p, c in sorted(self._files[run_id].items())}
