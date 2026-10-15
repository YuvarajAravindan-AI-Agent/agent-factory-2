"""Docker-free sandbox and gate for REPLAYED, already-reviewed code only.

This runs generated code on the host with no isolation, so the CLI refuses to pair it with a
live model. It exists so the regression suite can run on a laptop or in CI in seconds; the
real isolation is proven by the executor tests and by running evals on the VPS."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from factory.gate import checks
from factory.ports.models import CommandResult, GateResult


class LocalSandbox:
    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="af-local-"))

    def ws(self, run_id: str) -> Path:
        d = self.root / run_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def run(
        self, run_id: str, step: str, image: str, command: list[str], timeout_s: int = 60
    ) -> CommandResult:
        argv = [sys.executable if command[0] == "python" else command[0], *command[1:]]
        try:
            cp = subprocess.run(  # noqa: S603
                argv, cwd=self.ws(run_id), capture_output=True, timeout=timeout_s, check=False
            )
            return CommandResult(
                exit_code=cp.returncode,
                stdout=cp.stdout.decode(errors="replace"),
                stderr=cp.stderr.decode(errors="replace"),
            )
        except subprocess.TimeoutExpired:
            return CommandResult(exit_code=124, timed_out=True)

    def read_file(self, run_id: str, path: str) -> str:
        return (self.ws(run_id) / path).read_text()

    def write_file(self, run_id: str, path: str, content: str) -> None:
        p = self.ws(run_id) / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)

    def list_dir(self, run_id: str, path: str = ".") -> list[str]:
        ws = self.ws(run_id)
        skip = {"__pycache__", ".pytest_cache", ".af"}
        return sorted(
            str(p.relative_to(ws))
            for p in (ws / path).rglob("*")
            if p.is_file() and not skip & set(p.relative_to(ws).parts)
        )


class LocalGate:
    """Stage 2 only (dependencies are already installed in the host environment)."""

    def __init__(self, sandbox: LocalSandbox) -> None:
        self.sb = sandbox

    def record_manifest(self, run_id: str) -> dict[str, str]:
        ws = self.sb.ws(run_id)
        meta = ws / ".af"
        meta.mkdir(exist_ok=True)
        m = checks.build_manifest(ws)
        checks.save_manifest(meta, m)
        return m

    def stage1_fetch(self, run_id: str) -> GateResult:
        return GateResult(stage=1, passed=True, log="local backend: dependencies preinstalled")

    def stage2_test(self, run_id: str, baseline_test_count: int) -> GateResult:
        ws = self.sb.ws(run_id)
        junit = ws / ".af" / "junit.xml"
        res = self.sb.run(
            run_id, "gate2", "", ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider",
                                  f"--junitxml={junit}"], timeout_s=300,
        )  # fmt: skip
        xml = junit.read_text() if junit.exists() else ""
        total, failed, skipped = checks.parse_junit(xml)
        findings = checks.tamper_findings(
            ws, ws / ".af", baseline_test_count, total, skipped, set()
        )
        findings += [f for f in checks.scan_secrets(ws) if ".af" not in f]
        return GateResult(
            stage=2,
            passed=res.exit_code == 0 and failed == 0 and not findings,
            tests_total=total,
            tests_failed=failed,
            tests_skipped=skipped,
            junit_xml=xml,
            log=res.stdout[-2000:],
            tamper_findings=findings,
        )
