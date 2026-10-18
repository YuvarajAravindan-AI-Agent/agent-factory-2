"""Bundle parsing and role-owned file kinds."""

from __future__ import annotations

import json

from langgraph.checkpoint.memory import InMemorySaver

from factory.adapters.fakes import FakeApproval, FakeEvidence, FakeGate, FakeModel, FakeSandbox
from factory.graph import build_graph
from factory.nodes.bundle import is_test_path, parse_bundle, safe_path, strip_fences
from factory.nodes.pipeline import Ports
from factory.ports.models import CommandResult


def bundle(**files: str) -> str:
    return json.dumps(
        {"files": [{"path": k.replace("__", "/"), "content": v} for k, v in files.items()]}
    )


def test_safe_path_rules() -> None:
    assert safe_path("src/app.py") == "src/app.py"
    for bad in ("/etc/passwd", "../x", "a/../b", ".git/config", ".af/x", "", "a\x00b"):
        assert safe_path(bad) is None


def test_fence_and_bundle_parsing() -> None:
    assert strip_fences("```python\nx = 1\n```") == "x = 1\n"
    assert strip_fences("x = 1\n") == "x = 1\n"
    assert parse_bundle("not json") is None
    assert parse_bundle(bundle(a__b="c")).files[0].path == "a/b"  # type: ignore[union-attr]
    assert is_test_path("tests/test_a.py") and is_test_path("x/conftest.py")
    assert not is_test_path("src/app.py")


def sandbox(codes: list[int]) -> FakeSandbox:
    q = list(codes)

    def h(_c: list[str]) -> CommandResult:
        c = q.pop(0) if len(q) > 1 else q[0]
        return CommandResult(exit_code=c, stdout="2 passed" if c == 0 else "1 failed")

    return FakeSandbox(h)


def test_fixer_cannot_edit_tests_and_bad_paths_are_dropped() -> None:
    sb = sandbox([1, 0])  # first test run fails, second passes
    scripts = {
        "coder": [bundle(**{"src__app.py": "v1"})],
        "tester": [bundle(**{"tests__test_app.py": "ORIGINAL"})],
        "fixer": [
            bundle(**{"src__app.py": "v2", "tests__test_app.py": "WEAKENED", "..__evil": "x"})
        ],
        "reviewer": ['{"critical":0,"high":0,"summary":"ok"}'],
    }
    ev = FakeEvidence()
    ports = Ports(FakeModel(scripts), sb, FakeGate(), FakeApproval(), ev)
    g = build_graph(ports, InMemorySaver())
    g.invoke(
        {"run_id": "r1", "spec": "s", "status": "running"}, {"configurable": {"thread_id": "t"}}
    )
    assert sb.files[("r1", "tests/test_app.py")] == "ORIGINAL"  # the Fixer's edit was discarded
    assert sb.files[("r1", "src/app.py")] == "v2"
    assert not any("evil" in p for (_r, p) in sb.files)
    assert any(r["action"] == "rejected_path" for r in ev.rows)
