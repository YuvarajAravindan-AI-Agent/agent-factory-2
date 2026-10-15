from pathlib import Path

import pytest

from factory.executor.core import ExecutorCore, PolicyError
from factory.executor.policy import Policy
from factory.gate import checks

JUNIT = '<testsuites><testsuite tests="3" failures="1" errors="0" skipped="1"/></testsuites>'


def core(tmp_path: Path) -> ExecutorCore:
    return ExecutorCore(Policy(runs_root=tmp_path, allowed_images=("python:3.12-slim",)))


def test_parse_junit() -> None:
    assert checks.parse_junit(JUNIT) == (3, 1, 1)
    assert checks.parse_junit("") == (0, 0, 0)


def test_stdout_summary() -> None:
    assert checks.stdout_summary_counts("2 passed, 1 failed in 0.3s") == {"passed": 2, "failed": 1}


def test_path_escape_rejected(tmp_path: Path) -> None:
    c = core(tmp_path)
    for bad in ("../x", "/etc/passwd", "a/../../x"):
        with pytest.raises(PolicyError):
            c.read_file("r1", bad)
    with pytest.raises(PolicyError):
        c.read_file("../r1", "x")


def test_symlink_escape_rejected(tmp_path: Path) -> None:
    c = core(tmp_path)
    c.write_file("r1", "ok.txt", "hi")
    (c.run_dir("r1") / "ws" / "link").symlink_to("/etc/passwd")
    with pytest.raises(PolicyError):
        c.read_file("r1", "link")
    assert c.list_dir("r1") == ["ok.txt"]


def test_image_allow_list(tmp_path: Path) -> None:
    with pytest.raises(PolicyError):
        core(tmp_path).run("r1", "s", "evil:latest", ["true"])


def test_audit_chain(tmp_path: Path) -> None:
    c = core(tmp_path)
    c.write_file("r1", "a", "1")
    c.write_file("r1", "b", "2")
    import json

    rows = [json.loads(x) for x in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert rows[1]["prev"] == rows[0]["hash"]


def test_tamper_findings(tmp_path: Path) -> None:
    ws, meta = tmp_path / "ws", tmp_path / "meta"
    (ws / "tests").mkdir(parents=True)
    meta.mkdir()
    t = ws / "tests" / "test_a.py"
    t.write_text("def test_a():\n    assert 1\n")
    checks.save_manifest(meta, checks.build_manifest(ws))
    assert checks.tamper_findings(ws, meta, 1, 1, 0, set()) == []
    t.write_text("import pytest\n@pytest.mark.skip\ndef test_a():\n    assert 1\n")
    f = checks.tamper_findings(ws, meta, 1, 1, 0, set())
    assert any("skip/disable" in x for x in f) and any("modified" in x for x in f)
    assert not any(
        "modified" in x for x in checks.tamper_findings(ws, meta, 1, 1, 0, {"tests/test_a.py"})
    )
    t.unlink()
    assert any("deleted" in x for x in checks.tamper_findings(ws, meta, 1, 0, 0, set()))
    assert any("count dropped" in x for x in checks.tamper_findings(ws, meta, 2, 1, 0, set()))


def test_secret_scan(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text('key = "AKIA' + "A" * 16 + '"\n')
    (tmp_path / "b.py").write_text("x = 1\n")
    assert checks.scan_secrets(tmp_path) == ["secret:aws-access-key: a.py"]
