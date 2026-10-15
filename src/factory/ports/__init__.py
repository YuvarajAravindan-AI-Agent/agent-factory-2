"""Protocol ports: every external dependency sits behind one of these."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import ApprovalDecision, CommandResult, GateResult, ModelRequest, ModelResponse


@runtime_checkable
class ModelPort(Protocol):
    def invoke(self, request: ModelRequest) -> ModelResponse: ...


@runtime_checkable
class SandboxPort(Protocol):
    def run(
        self, run_id: str, step: str, image: str, command: list[str], timeout_s: int = 60
    ) -> CommandResult: ...
    def read_file(self, run_id: str, path: str) -> str: ...
    def write_file(self, run_id: str, path: str, content: str) -> None: ...
    def list_dir(self, run_id: str, path: str = ".") -> list[str]: ...


@runtime_checkable
class GatePort(Protocol):
    def stage1_fetch(self, run_id: str) -> GateResult: ...
    def stage2_test(self, run_id: str, baseline_test_count: int) -> GateResult: ...


@runtime_checkable
class ApprovalPort(Protocol):
    def request(self, run_id: str, package_sha256: str) -> None: ...
    def status(self, run_id: str) -> ApprovalDecision: ...


@runtime_checkable
class EvidencePort(Protocol):
    def write(self, run_id: str, rel_path: str, content: str) -> str:
        """Store a file under the run directory; return its SHA-256."""
        ...

    def read(self, run_id: str, rel_path: str) -> str: ...
    def audit(self, run_id: str, actor: str, action: str, payload: str) -> str:
        """Append a hash-chained audit row; return the row hash."""
        ...

    def verify_chain(self) -> bool: ...
    def package(self, run_id: str, files: dict[str, str]) -> str:
        """Write package.zip and SHA256SUMS; return the package SHA-256."""
        ...


__all__ = [
    "ApprovalPort",
    "EvidencePort",
    "GatePort",
    "ModelPort",
    "SandboxPort",
]
