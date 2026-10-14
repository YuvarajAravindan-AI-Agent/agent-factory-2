import hashlib

from factory.adapters.fakes import (
    FakeApproval,
    FakeEvidence,
    FakeGate,
    FakeModel,
    FakeSandbox,
)
from factory.ports import ApprovalPort, EvidencePort, GatePort, ModelPort, SandboxPort
from factory.ports.models import (
    ApprovalDecision,
    CommandLimits,
    GateResult,
    ModelRequest,
    Tier,
)


def test_fakes_satisfy_ports() -> None:
    assert isinstance(FakeModel(), ModelPort)
    assert isinstance(FakeSandbox(), SandboxPort)
    assert isinstance(FakeGate(), GatePort)
    assert isinstance(FakeApproval(), ApprovalPort)
    assert isinstance(FakeEvidence(), EvidencePort)


def test_model_replays_script_then_default() -> None:
    m = FakeModel({"coder": ["a"]})
    req = ModelRequest(role="coder", tier=Tier.STRONG, prompt="write code")
    assert m.invoke(req).text == "a"
    assert m.invoke(req).text == "fake-coder-output"
    assert len(m.requests) == 2


def test_sandbox_files_are_per_run() -> None:
    s = FakeSandbox()
    s.write_file("r1", "src/a.py", "x")
    assert s.read_file("r1", "src/a.py") == "x"
    assert s.list_dir("r1", "src") == ["src/a.py"]
    assert s.list_dir("r2", "src") == []
    assert s.run("r1", "t", "img", ["true"], CommandLimits()).exit_code == 0


def test_gate_pass_and_tamper() -> None:
    g = FakeGate([GateResult(stage1_ok=True, stage2_ok=True, tamper_findings=["skip"])])
    assert not g.run_gate("r").passed
    assert g.run_gate("r").passed


def test_approval_states() -> None:
    a = FakeApproval(None)
    assert a.poll_decision("r") is None
    rec = FakeApproval(ApprovalDecision.REJECTED).poll_decision("r")
    assert rec is not None and rec.decision is ApprovalDecision.REJECTED


def test_evidence_manifest_hashes() -> None:
    e = FakeEvidence()
    digest = e.write("r", "spec.md", b"hello")
    assert digest == hashlib.sha256(b"hello").hexdigest()
    assert e.manifest("r") == {"spec.md": digest}
    assert e.read("r", "spec.md") == b"hello"
