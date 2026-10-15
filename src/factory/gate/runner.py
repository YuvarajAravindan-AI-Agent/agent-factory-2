"""Two-stage gate driven through the executor core."""

from __future__ import annotations

import shlex

from factory.executor.core import ExecutorCore
from factory.ports.models import GateResult

from . import checks

STAGE1_TIMEOUT = 280
STAGE2_TIMEOUT = 280


class Gate:
    def __init__(self, core: ExecutorCore) -> None:
        self.core = core

    def record_manifest(self, run_id: str) -> dict[str, str]:
        """Called after the Tester step: freeze hashes of every test file."""
        d = self.core.run_dir(run_id)
        m = checks.build_manifest(d / "ws")
        checks.save_manifest(d / "meta", m)
        self.core.audit(run_id, "record_manifest", {"files": len(m)})
        return m

    def allow_test_change(self, run_id: str, rel: str) -> None:
        """Fixer record: this test file may legitimately differ from the manifest."""
        self.core.safe_path(run_id, rel)
        meta = self.core.run_dir(run_id) / "meta"
        with (meta / "fixer_allowed.txt").open("a") as f:
            f.write(rel + "\n")
        self.core.audit(run_id, "fixer_record", {"path": rel})

    def _fixer_allowed(self, run_id: str) -> set[str]:
        f = self.core.run_dir(run_id) / "meta" / "fixer_allowed.txt"
        return set(f.read_text().split()) if f.exists() else set()

    def stage1_fetch(self, run_id: str) -> GateResult:
        """Download wheels through the allow-list proxy; runs none of the project's code."""
        p, d = self.core.policy, self.core.run_dir(run_id)
        reqs = d / "ws" / "requirements.txt"
        if not reqs.exists():
            return GateResult(stage=1, passed=False, log="requirements.txt missing")
        # Fresh start: a previous run's wheels must not satisfy this run's requirements.
        for old in (d / "deps").glob("*"):
            old.unlink()
        cmd = (
            "pip download --only-binary=:all: --no-input --disable-pip-version-check "
            f"-r /ws/requirements.txt {' '.join(shlex.quote(t) for t in p.gate_tools)} -d /deps"
        )
        res = self.core.exec_container(
            run_id, "gate1", p.gate_image, ["sh", "-c", cmd],
            [(d / "ws", "/ws", "ro"), (d / "deps", "/deps", "rw")],
            p.fetch_network, STAGE1_TIMEOUT,
            env={"HTTPS_PROXY": p.proxy_url, "HTTP_PROXY": p.proxy_url},
        )  # fmt: skip
        n = len(list((d / "deps").glob("*.whl")))
        ok = res.exit_code == 0 and n > 0
        return GateResult(
            stage=1, passed=ok, log=f"wheels={n}\n{res.stdout[-3000:]}{res.stderr[-3000:]}"
        )

    def stage2_test(self, run_id: str, baseline_test_count: int) -> GateResult:
        """Run tests with no network, deps read-only, JUnit written to a separate mount."""
        p, d = self.core.policy, self.core.run_dir(run_id)
        for old in (d / "out").glob("*"):
            old.unlink()
        cmd = (
            "pip install --no-index --no-input --disable-pip-version-check "
            "--find-links /deps --target /tmp/site -r /work/requirements.txt "
            f"{' '.join(shlex.quote(t) for t in p.gate_tools)} >/tmp/pip.log 2>&1 "
            "|| { cat /tmp/pip.log; exit 97; }; "
            "PYTHONPATH=/tmp/site:/work PYTHONDONTWRITEBYTECODE=1 "
            "python -m pytest -p no:cacheprovider -q --junitxml=/out/junit.xml /work"
        )
        res = self.core.exec_container(
            run_id, "gate2", p.gate_image, ["sh", "-c", cmd],
            [(d / "ws", "/work", "rw"), (d / "deps", "/deps", "ro"), (d / "out", "/out", "rw")],
            "none", STAGE2_TIMEOUT,
        )  # fmt: skip
        junit = (d / "out" / "junit.xml").read_text() if (d / "out" / "junit.xml").exists() else ""
        findings: list[str] = []
        try:
            total, failed, skipped = checks.parse_junit(junit)
        except Exception as e:  # malformed XML is itself a failure
            total = failed = skipped = 0
            findings.append(f"unparseable junit xml: {e}")
        counts = checks.stdout_summary_counts(res.stdout)
        said = counts.get("passed", 0) + counts.get("failed", 0) + counts.get("skipped", 0)
        said += counts.get("error", 0) + counts.get("errors", 0)
        if junit and said != total:
            findings.append(f"junit total {total} disagrees with pytest output {said}")
        findings += checks.tamper_findings(
            d / "ws", d / "meta", baseline_test_count, total, skipped, self._fixer_allowed(run_id)
        )
        findings += checks.scan_secrets(d / "ws")
        passed = res.exit_code == 0 and failed == 0 and not findings and not res.timed_out
        return GateResult(
            stage=2, passed=passed, tests_total=total, tests_failed=failed,
            tests_skipped=skipped, junit_xml=junit,
            log=(res.stdout[-4000:] + res.stderr[-2000:]), tamper_findings=findings,
        )  # fmt: skip
