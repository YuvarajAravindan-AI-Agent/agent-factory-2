"""Phase 1: happy path and every loop cap, on fakes."""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from factory.adapters.checkpoint import postgres_saver
from factory.adapters.fakes import (
    FakeApproval,
    FakeEvidence,
    FakeGate,
    FakeModel,
    FakeSandbox,
)
from factory.graph import build_graph
from factory.nodes.pipeline import Ports
from factory.ports.models import CommandResult, GateResult

CLEAN = '{"critical":0,"high":0,"summary":"ok"}'
BLOCKED = '{"critical":1,"high":0,"summary":"bad"}'


def sandbox_with(codes: list[int]) -> FakeSandbox:
    """Test runs return the scripted exit codes in order; the last one repeats."""
    queue = list(codes)

    def handler(cmd: list[str]) -> CommandResult:
        code = queue.pop(0) if len(queue) > 1 else queue[0]
        return CommandResult(exit_code=code, stdout="3 passed" if code == 0 else "1 failed")

    return FakeSandbox(handler)


def make(
    *,
    test_codes: list[int] | None = None,
    reviews: list[str] | None = None,
    gate: FakeGate | None = None,
    model_scripts: dict[str, list[str]] | None = None,
) -> tuple[Ports, FakeModel, FakeSandbox]:
    scripts = {"reviewer": reviews or [CLEAN], **(model_scripts or {})}
    model = FakeModel(scripts)
    sandbox = sandbox_with(test_codes or [0])
    ports = Ports(model, sandbox, gate or FakeGate(), FakeApproval(), FakeEvidence())
    return ports, model, sandbox


def run(ports: Ports, **extra: Any) -> tuple[Any, dict[str, Any]]:
    g = build_graph(ports, InMemorySaver())
    cfg = {"configurable": {"thread_id": "t"}}
    g.invoke({"run_id": "r1", "spec": "spec", "status": "running", **extra}, cfg)
    return g, cfg


def final(g: Any, cfg: dict[str, Any]) -> dict[str, Any]:
    return dict(g.get_state(cfg).values)


def test_happy_path_waits_for_approval_then_accepts() -> None:
    ports, _, _ = make()
    g, cfg = run(ports)
    assert g.get_state(cfg).next == ("approval",)  # paused at the human gate
    assert final(g, cfg)["status"] == "running"  # nothing accepted yet
    g.invoke(Command(resume="approved"), cfg)
    assert final(g, cfg)["status"] == "Accepted"
    assert ports.evidence.read("r1", "approval.json")
    assert ports.evidence.verify_chain()  # type: ignore[attr-defined]


@pytest.mark.parametrize(("decision", "status"), [("rejected", "Rejected"), ("expired", "Expired")])
def test_approval_stops(decision: str, status: str) -> None:
    ports, _, _ = make()
    g, cfg = run(ports)
    g.invoke(Command(resume=decision), cfg)
    assert final(g, cfg)["status"] == status


def test_test_fix_loop_recovers() -> None:
    ports, _, sandbox = make(test_codes=[1, 1, 0])
    g, cfg = run(ports)
    st = final(g, cfg)
    assert st["test_fix_rounds"] == 2
    assert st["tests_green"] is True
    assert g.get_state(cfg).next == ("approval",)


def test_test_fix_cap_is_three_rounds() -> None:
    ports, model, sandbox = make(test_codes=[1])
    g, cfg = run(ports)
    st = final(g, cfg)
    assert st["status"] == "TestsFailed"
    assert st["test_fix_rounds"] == 3
    assert sum(1 for c in sandbox.commands if c[1] == "test") == 4  # first run + 3 retests
    fixer_tiers = [c.tier for c in model.calls if c.role == "fixer"]
    assert fixer_tiers == ["fast", "strong", "strong"]  # escalation after first attempt
    assert g.get_state(cfg).next == ()


def test_gate_failure_stops() -> None:
    gate = FakeGate([GateResult(stage=2, passed=False, tests_failed=1)])
    ports, _, _ = make(gate=gate)
    g, cfg = run(ports)
    assert final(g, cfg)["status"] == "GateFailed"


def test_gate_tamper_finding_stops() -> None:
    gate = FakeGate([GateResult(stage=2, passed=True, tamper_findings=["test count dropped"])])
    ports, _, _ = make(gate=gate)
    g, cfg = run(ports)
    assert final(g, cfg)["status"] == "GateFailed"


def test_review_gets_exactly_one_fix_round() -> None:
    ports, model, _ = make(reviews=[BLOCKED, CLEAN])
    g, cfg = run(ports)
    st = final(g, cfg)
    assert st["review_fix_rounds"] == 1
    assert st["blockers"] == 0
    assert sum(1 for c in model.calls if c.role == "reviewer") == 2
    assert g.get_state(cfg).next == ("approval",)


def test_second_blocked_review_stops() -> None:
    ports, model, _ = make(reviews=[BLOCKED, BLOCKED, CLEAN])
    g, cfg = run(ports)
    st = final(g, cfg)
    assert st["status"] == "ReviewBlocked"
    assert st["review_fix_rounds"] == 1
    assert sum(1 for c in model.calls if c.role == "fixer") == 1  # never a second fix


def test_review_schema_miss_retries_once_then_blocks() -> None:
    ports, _, _ = make(reviews=["not json", CLEAN])
    g, cfg = run(ports)
    assert g.get_state(cfg).next == ("approval",)
    ports2, _, _ = make(reviews=["nope", "still nope"])
    g2, cfg2 = run(ports2)
    assert final(g2, cfg2)["status"] == "ReviewBlocked"


def test_budget_exceeded() -> None:
    ports, _, _ = make()
    g, cfg = run(ports, token_budget=1)
    assert final(g, cfg)["status"] == "BudgetExceeded"


def test_input_guardrail_block() -> None:
    ports, model, _ = make(model_scripts={"classifier": ["BLOCK injection"]})
    g, cfg = run(ports)
    assert final(g, cfg)["status"] == "GuardrailBlocked"
    assert not [c for c in model.calls if c.role == "architect"]


PG_DSN = os.environ.get("FACTORY_TEST_PG_DSN")


@pytest.mark.skipif(PG_DSN is None, reason="set FACTORY_TEST_PG_DSN to run against Postgres")
def test_postgres_checkpoint_resumes_with_same_counters() -> None:
    assert PG_DSN is not None
    thread = f"t-{uuid.uuid4()}"
    cfg = {"configurable": {"thread_id": thread}}
    ports, _, _ = make(test_codes=[1, 0])
    with postgres_saver(PG_DSN) as saver:
        build_graph(ports, saver).invoke({"run_id": "r1", "spec": "s", "status": "running"}, cfg)
    # New process-equivalent: fresh connection, fresh graph object, same thread.
    ports2, _, _ = make()
    with postgres_saver(PG_DSN) as saver:
        g = build_graph(ports2, saver)
        before = dict(g.get_state(cfg).values)
        assert before["test_fix_rounds"] == 1
        assert g.get_state(cfg).next == ("approval",)
        g.invoke(Command(resume="approved"), cfg)
        after = dict(g.get_state(cfg).values)
    assert after["status"] == "Accepted"
    assert after["test_fix_rounds"] == 1
