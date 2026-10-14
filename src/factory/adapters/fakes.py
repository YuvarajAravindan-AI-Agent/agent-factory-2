"""In-memory fakes for every port, so the whole graph runs in unit tests."""

from __future__ import annotations

import hashlib
from collections.abc import Callable

from factory.ports.models import (
    ApprovalDecision,
    CommandResult,
    GateResult,
    ModelRequest,
    ModelResponse,
)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class FakeModel:
    """Replays scripted responses per role; records every request."""

    def __init__(self, scripts: dict[str, list[str]] | None = None, default: str = "ok") -> None:
        self.scripts = {k: list(v) for k, v in (scripts or {}).items()}
        self.default = default
        self.calls: list[ModelRequest] = []

    def invoke(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        queue = self.scripts.get(request.role)
        text = queue.pop(0) if queue else self.default
        return ModelResponse(
            text=text,
            model_id=f"fake-{request.tier}",
            prompt_hash=sha256(request.prompt),
            input_tokens=len(request.prompt.split()),
            output_tokens=len(text.split()),
        )


class FakeSandbox:
    def __init__(self, handler: Callable[[list[str]], CommandResult] | None = None) -> None:
        self.files: dict[tuple[str, str], str] = {}
        self.commands: list[tuple[str, str, list[str]]] = []
        self.handler = handler or (lambda _cmd: CommandResult(exit_code=0))

    def run(
        self, run_id: str, step: str, image: str, command: list[str], timeout_s: int = 60
    ) -> CommandResult:
        self.commands.append((run_id, step, command))
        return self.handler(command)

    def read_file(self, run_id: str, path: str) -> str:
        return self.files[(run_id, path)]

    def write_file(self, run_id: str, path: str, content: str) -> None:
        self.files[(run_id, path)] = content

    def list_dir(self, run_id: str, path: str = ".") -> list[str]:
        prefix = "" if path in (".", "") else path.rstrip("/") + "/"
        return sorted(p for (r, p) in self.files if r == run_id and p.startswith(prefix))


class FakeGate:
    def __init__(self, stage2_results: list[GateResult] | None = None) -> None:
        self.stage2_results = list(stage2_results or [])

    def stage1_fetch(self, run_id: str) -> GateResult:
        return GateResult(stage=1, passed=True, log="fake deps fetched")

    def stage2_test(self, run_id: str, baseline_test_count: int) -> GateResult:
        if self.stage2_results:
            return self.stage2_results.pop(0)
        return GateResult(stage=2, passed=True, tests_total=baseline_test_count)


class FakeApproval:
    def __init__(self, decision: str = "approved") -> None:
        self.decision = decision
        self.requested: dict[str, str] = {}

    def request(self, run_id: str, package_sha256: str) -> None:
        self.requested[run_id] = package_sha256

    def status(self, run_id: str) -> ApprovalDecision:
        if run_id not in self.requested:
            return ApprovalDecision(run_id=run_id, decision="pending")
        return ApprovalDecision(
            run_id=run_id, decision=self.decision, approver="fake@example.invalid"
        )


class FakeEvidence:
    """Dict-backed store with a real SHA-256 hash chain for the audit rows."""

    GENESIS = "0" * 64

    def __init__(self) -> None:
        self.files: dict[tuple[str, str], str] = {}
        self.rows: list[dict[str, str]] = []

    def write(self, run_id: str, rel_path: str, content: str) -> str:
        self.files[(run_id, rel_path)] = content
        return sha256(content)

    def read(self, run_id: str, rel_path: str) -> str:
        return self.files[(run_id, rel_path)]

    @staticmethod
    def _row_hash(prev: str, run_id: str, actor: str, action: str, payload_hash: str) -> str:
        return sha256("|".join([prev, run_id, actor, action, payload_hash]))

    def audit(self, run_id: str, actor: str, action: str, payload: str) -> str:
        prev = self.rows[-1]["row_hash"] if self.rows else self.GENESIS
        payload_hash = sha256(payload)
        row_hash = self._row_hash(prev, run_id, actor, action, payload_hash)
        self.rows.append(
            {
                "run_id": run_id,
                "actor": actor,
                "action": action,
                "payload_hash": payload_hash,
                "prev_hash": prev,
                "row_hash": row_hash,
            }
        )
        return row_hash

    def verify_chain(self) -> bool:
        prev = self.GENESIS
        for r in self.rows:
            expected = self._row_hash(prev, r["run_id"], r["actor"], r["action"], r["payload_hash"])
            if r["prev_hash"] != prev or r["row_hash"] != expected:
                return False
            prev = r["row_hash"]
        return True
