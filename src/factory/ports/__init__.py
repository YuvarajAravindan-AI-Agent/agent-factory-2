"""Protocol ports: every external dependency sits behind one of these."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from factory.ports.models import (
    ApprovalRecord,
    CommandLimits,
    CommandResult,
    GateResult,
    ModelRequest,
    ModelResponse,
)


@runtime_checkable
class ModelPort(Protocol):
    def invoke(self, request: ModelRequest) -> ModelResponse: ...


@runtime_checkable
class SandboxPort(Protocol):
    def run(
        self, run_id: str, step: str, image: str, command: list[str], limits: CommandLimits
    ) -> CommandResult: ...

    def write_file(self, run_id: str, path: str, content: str) -> None: ...

    def read_file(self, run_id: str, path: str) -> str: ...

    def list_dir(self, run_id: str, path: str) -> list[str]: ...


@runtime_checkable
class GatePort(Protocol):
    def run_gate(self, run_id: str) -> GateResult: ...


@runtime_checkable
class ApprovalPort(Protocol):
    def request_approval(self, run_id: str, package_sha256: str) -> None: ...

    def poll_decision(self, run_id: str) -> ApprovalRecord | None: ...


@runtime_checkable
class EvidencePort(Protocol):
    def write(self, run_id: str, relpath: str, content: bytes) -> str:
        """Store a file under the run; return its SHA-256 hex digest."""
        ...

    def read(self, run_id: str, relpath: str) -> bytes: ...

    def manifest(self, run_id: str) -> dict[str, str]:
        """Map of relpath to SHA-256 for every stored file."""
        ...


__all__ = ["ApprovalPort", "EvidencePort", "GatePort", "ModelPort", "SandboxPort"]
