"""Run golden specs through the graph and collect comparable metrics."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from factory.adapters.fakes import FakeApproval, FakeEvidence
from factory.graph import build_graph
from factory.nodes.pipeline import Ports
from factory.nodes.version import FACTORY_VERSION, prompts_version
from factory.ports import GatePort, ModelPort, SandboxPort


@dataclass(frozen=True)
class Case:
    name: str
    vertical: str
    spec: str
    expect: dict[str, Any]


def load_dataset(path: Path | str = "evals/golden/dataset.jsonl") -> list[Case]:
    cases = []
    for line in Path(path).read_text().splitlines():
        if line.strip():
            d = json.loads(line)
            cases.append(Case(d["name"], d["vertical"], d["spec"], d["expect"]))
    return cases


def run_case(
    case: Case,
    model: ModelPort,
    sandbox: SandboxPort,
    gate: GatePort,
    run_id: str,
    *,
    token_budget: int = 400_000,
) -> dict[str, Any]:
    """Run one spec to the approval pause (or a stop state) and return its metrics."""
    evidence = FakeEvidence()
    ports = Ports(model, sandbox, gate, FakeApproval(), evidence)
    g = build_graph(ports, InMemorySaver())
    cfg: Any = {"configurable": {"thread_id": run_id}}
    spec = Path(case.spec).read_text()
    t0 = time.monotonic()
    error = ""
    try:
        g.invoke(
            {"run_id": run_id, "spec": spec, "status": "running", "token_budget": token_budget}, cfg
        )
    except Exception as e:  # a crashed run is a result, not a crash of the eval suite
        error = f"{type(e).__name__}: {str(e)[:200]}"
    wall = time.monotonic() - t0
    st = g.get_state(cfg)
    v = dict(st.values)
    reached = tuple(st.next) == ("approval",)
    if reached:  # exercise the approval node so the full path is covered
        g.invoke(Command(resume="approved"), cfg)
    calls = v.get("calls", [])
    return {
        "name": case.name,
        "vertical": case.vertical,
        "reached_approval": reached,
        "stop_state": "" if reached else str(v.get("status", "unknown")),
        "tests_total": int(v.get("baseline_tests", 0)),
        "test_fix_rounds": int(v.get("test_fix_rounds", 0)),
        "review_fix_rounds": int(v.get("review_fix_rounds", 0)),
        "blockers": int(v.get("blockers", 0)),
        "tokens": int(v.get("tokens_used", 0)),
        "model_calls": len(calls),
        "wall_s": round(wall, 2),
        "error": error,
    }


def run_suite(
    cases: list[Case],
    make: Callable[[Case], tuple[ModelPort, SandboxPort, GatePort]],
    tag: str,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    results = []
    for c in cases:
        model, sandbox, gate = make(c)
        results.append(run_case(c, model, sandbox, gate, f"eval-{tag}-{c.name}"))
    n = len(results)
    return {
        "factory_version": FACTORY_VERSION,
        "prompts_version": prompts_version(),
        "meta": meta or {},
        "cases": results,
        "summary": {
            "cases": n,
            "reached_approval_rate": sum(r["reached_approval"] for r in results) / max(n, 1),
            "fix_rounds": sum(r["test_fix_rounds"] + r["review_fix_rounds"] for r in results),
            "blockers": sum(r["blockers"] for r in results),
            "tokens": sum(r["tokens"] for r in results),
            "wall_s": round(sum(r["wall_s"] for r in results), 2),
        },
    }
