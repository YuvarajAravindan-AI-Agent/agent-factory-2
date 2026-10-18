"""Phase 0: every fake satisfies its Protocol and behaves as the graph will need."""

from factory.adapters.fakes import (
    FakeApproval,
    FakeEvidence,
    FakeGate,
    FakeModel,
    FakeSandbox,
)
from factory.ports import ApprovalPort, EvidencePort, GatePort, ModelPort, SandboxPort
from factory.ports.models import GateResult, ModelRequest


def test_fakes_satisfy_protocols() -> None:
    assert isinstance(FakeModel(), ModelPort)
    assert isinstance(FakeSandbox(), SandboxPort)
    assert isinstance(FakeGate(), GatePort)
    assert isinstance(FakeApproval(), ApprovalPort)
    assert isinstance(FakeEvidence(), EvidencePort)


def test_model_replays_script_then_default() -> None:
    m = FakeModel({"coder": ["a", "b"]}, default="z")
    req = ModelRequest(role="coder", tier="strong", prompt="p")
    assert [m.invoke(req).text for _ in range(3)] == ["a", "b", "z"]
    assert len(m.calls) == 3


def test_sandbox_files_scoped_per_run() -> None:
    s = FakeSandbox()
    s.write_file("r1", "src/a.py", "x")
    s.write_file("r2", "src/b.py", "y")
    assert s.list_dir("r1", "src") == ["src/a.py"]
    assert s.read_file("r2", "src/b.py") == "y"


def test_gate_scripted_failure_then_pass() -> None:
    g = FakeGate([GateResult(stage=2, passed=False, tests_failed=1)])
    assert not g.stage2_test("r", 3).passed
    assert g.stage2_test("r", 3).passed


def test_approval_pending_until_requested() -> None:
    a = FakeApproval("rejected")
    assert a.status("r").decision == "pending"
    a.request("r", "abc")
    assert a.status("r").decision == "rejected"


def test_audit_chain_verifies_and_detects_tamper() -> None:
    e = FakeEvidence()
    e.audit("r", "worker", "start", "p1")
    e.audit("r", "worker", "end", "p2")
    assert e.verify_chain()
    e.rows[0]["action"] = "tampered"
    assert not e.verify_chain()
