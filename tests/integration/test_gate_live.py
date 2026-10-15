"""Live executor + gate tests. Need Docker, gVisor and the af-fetch network (executor/setup.sh).

Run on the VPS:  AF_INTEGRATION=1 AF_RUNTIME=runsc uv run pytest tests/integration -v
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

import pytest

from factory.executor.core import ExecutorCore, PolicyError
from factory.executor.policy import Policy
from factory.gate.runner import Gate

pytestmark = pytest.mark.skipif(not os.environ.get("AF_INTEGRATION"), reason="needs Docker/gVisor")

IMAGE = "python:3.12-slim"
GOOD_TESTS = """\
from calc import add


def test_add():
    assert add(1, 2) == 3


def test_add_neg():
    assert add(-1, -2) == -3
"""

# Passes only if every attack fails; a green gate therefore proves containment.
CANARY_TESTS = """\
import os
import socket
from pathlib import Path


def test_no_network():
    for host in ("1.1.1.1", "8.8.8.8"):
        s = socket.socket()
        s.settimeout(3)
        try:
            s.connect((host, 443))
        except OSError:
            continue
        raise AssertionError("outbound connection succeeded")


def test_no_dns():
    try:
        socket.getaddrinfo("example.com", 443)
    except OSError:
        return
    raise AssertionError("dns resolved")


def test_host_canary_invisible():
    p = Path(os.environ.get("CANARY_PATH", "/nonexistent"))
    assert not Path(os.environ["CANARY_FILE"]).exists() if "CANARY_FILE" in os.environ else True
    assert not any("CANARY" in k for k in os.environ)
    assert not Path("/var/lib/factory").exists()
    assert not Path("/var/run/docker.sock").exists()
    assert not p.exists()


def test_root_fs_read_only():
    for target in ("/etc/af-owned", "/usr/af-owned", "/af-owned"):
        try:
            Path(target).write_text("x")
        except OSError:
            continue
        raise AssertionError(f"wrote {target}")


def test_no_privilege():
    assert os.getuid() != 0 or not Path("/proc/sys/kernel/hostname").is_symlink()
    try:
        os.setuid(0)
        raise AssertionError("setuid(0) worked from non-root")
    except PermissionError:
        pass
"""


@pytest.fixture
def core(tmp_path: Path) -> ExecutorCore:
    pol = Policy(
        runs_root=tmp_path / "runs",
        allowed_images=(IMAGE,),
        runtime=os.environ.get("AF_RUNTIME", "runsc"),
    )
    return ExecutorCore(pol)


def project(core: ExecutorCore, run: str, tests: str = GOOD_TESTS) -> None:
    core.write_file(run, "requirements.txt", "")
    core.write_file(run, "calc.py", "def add(a, b):\n    return a + b\n")
    core.write_file(run, "tests/test_calc.py", tests)


def full_gate(core: ExecutorCore, run: str, baseline: int):  # type: ignore[no-untyped-def]
    g = Gate(core)
    g.record_manifest(run)
    s1 = g.stage1_fetch(run)
    assert s1.passed, s1.log
    return g, g.stage2_test(run, baseline)


def test_happy_path(core: ExecutorCore) -> None:
    project(core, "happy")
    _, r = full_gate(core, "happy", 2)
    assert r.passed, (r.tamper_findings, r.log)
    assert r.tests_total == 2


def test_canary_blocked(
    core: ExecutorCore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = secrets.token_hex(8)
    canary = tmp_path / "host-canary.txt"
    canary.write_text(token)
    monkeypatch.setenv("CANARY_TOKEN", token)
    monkeypatch.setenv("CANARY_FILE", str(canary))
    project(core, "canary", CANARY_TESTS)
    _, r = full_gate(core, "canary", 5)
    assert r.passed, (r.tamper_findings, r.log)
    assert token not in r.log and token not in r.junit_xml


def test_agent_commands_have_no_network_or_host(core: ExecutorCore) -> None:
    core.write_file("agent", "x.txt", "hello")
    out = core.run(
        "agent", "probe", IMAGE, ["sh", "-c", "cat /work/x.txt; ls /var/run/docker.sock; env"]
    )
    assert "hello" in out.stdout and "docker.sock" in out.stderr
    assert "CANARY" not in out.stdout
    net = core.run(
        "agent", "net", IMAGE,
        ["python", "-c", "import socket;socket.create_connection(('1.1.1.1',443),3)"],
    )  # fmt: skip
    assert net.exit_code != 0


def test_image_and_escape_denied(core: ExecutorCore) -> None:
    with pytest.raises(PolicyError):
        core.run("agent", "s", "alpine", ["true"])
    with pytest.raises(PolicyError):
        core.read_file("agent", "../../etc/passwd")


def test_timeout_kills(core: ExecutorCore) -> None:
    r = core.run("agent", "sleep", IMAGE, ["sleep", "30"], timeout_s=3)
    assert r.timed_out


def test_stage1_proxy_allowlist(core: ExecutorCore) -> None:
    probe = (
        "import urllib.request as u;"
        "h=u.ProxyHandler({'https':'http://af-proxy:3128'});"
        "o=u.build_opener(h);"
        "import sys\ntry:\n o.open('https://example.com',timeout=8);print('REACHED')\n"
        "except Exception as e:\n print('DENIED',e)"
    )
    d = core.run_dir("proxy")
    r = core.exec_container(
        "proxy", "probe", IMAGE, ["python", "-c", probe], [(d / "ws", "/work", "rw")],
        core.policy.fetch_network, 20,
    )  # fmt: skip
    assert "REACHED" not in r.stdout and "DENIED" in r.stdout
    direct = core.exec_container(
        "proxy", "direct", IMAGE,
        ["python", "-c", "import socket;socket.create_connection(('1.1.1.1',443),4)"],
        [(d / "ws", "/work", "rw")], core.policy.fetch_network, 20,
    )  # fmt: skip
    assert direct.exit_code != 0


def test_stage1_fetches_real_wheel(core: ExecutorCore) -> None:
    project(core, "wheel")
    core.write_file("wheel", "requirements.txt", "six==1.16.0\n")
    g = Gate(core)
    s1 = g.stage1_fetch("wheel")
    assert s1.passed, s1.log
    assert list((core.run_dir("wheel") / "deps").glob("six-*.whl"))


def test_tamper_delete_test(core: ExecutorCore) -> None:
    project(core, "t1")
    g = Gate(core)
    g.record_manifest("t1")
    assert g.stage1_fetch("t1").passed
    core.write_file(
        "t1",
        "tests/test_calc.py",
        "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n",
    )
    r = g.stage2_test("t1", 2)
    assert not r.passed
    assert any("count dropped" in f for f in r.tamper_findings)
    assert any("modified" in f for f in r.tamper_findings)


def test_tamper_skip_and_fixer_record(core: ExecutorCore) -> None:
    project(core, "t2")
    g = Gate(core)
    g.record_manifest("t2")
    assert g.stage1_fetch("t2").passed
    skipped = "import pytest\nfrom calc import add\n\n\n@pytest.mark.skip\ndef test_add():\n    assert add(1, 2) == 4\n\n\ndef test_add_neg():\n    assert add(-1, -2) == -3\n"  # noqa: E501
    core.write_file("t2", "tests/test_calc.py", skipped)
    r = g.stage2_test("t2", 2)
    assert not r.passed and r.tests_skipped == 1
    assert any("skip/disable" in f for f in r.tamper_findings)
    # A Fixer record excuses the modification, but the skip marker still fails the gate.
    g.allow_test_change("t2", "tests/test_calc.py")
    r2 = g.stage2_test("t2", 2)
    assert not r2.passed and not any("modified" in f for f in r2.tamper_findings)


def test_failing_test_fails_gate(core: ExecutorCore) -> None:
    project(core, "t3", GOOD_TESTS.replace("== 3", "== 4"))
    _, r = full_gate(core, "t3", 2)
    assert not r.passed and r.tests_failed == 1


def test_secret_in_code_fails_gate(core: ExecutorCore) -> None:
    project(core, "t4")
    core.write_file("t4", "cfg.py", 'KEY = "AKIA' + "B" * 16 + '"\n')
    _, r = full_gate(core, "t4", 2)
    assert not r.passed and any(f.startswith("secret:") for f in r.tamper_findings)
