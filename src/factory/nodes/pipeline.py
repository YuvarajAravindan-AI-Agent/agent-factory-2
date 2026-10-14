"""Graph nodes. Each node closes over the ports; none imports a vendor client."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from langgraph.types import interrupt
from pydantic import ValidationError

from factory.ports import ApprovalPort, EvidencePort, GatePort, ModelPort, SandboxPort
from factory.ports.models import ModelRequest, ModelResponse
from factory.state import (
    MAX_REVIEW_PARSE_ATTEMPTS,
    FactoryState,
    ReviewFindings,
)

Update = dict[str, Any]
Node = Callable[[FactoryState], Update]

SANDBOX_IMAGE = "factory-sandbox"


class Ports:
    def __init__(
        self,
        model: ModelPort,
        sandbox: SandboxPort,
        gate: GatePort,
        approval: ApprovalPort,
        evidence: EvidencePort,
    ) -> None:
        self.model = model
        self.sandbox = sandbox
        self.gate = gate
        self.approval = approval
        self.evidence = evidence


def _call(
    p: Ports,
    state: FactoryState,
    role: str,
    tier: str,
    prompt: str,
    schema_name: str | None = None,
) -> tuple[ModelResponse, Update]:
    resp = p.model.invoke(
        ModelRequest(role=role, tier=tier, prompt=prompt, schema_name=schema_name)
    )
    used = state.get("tokens_used", 0) + resp.input_tokens + resp.output_tokens
    update: Update = {"tokens_used": used}
    budget = state.get("token_budget", 0)
    if budget and used > budget:
        update["status"] = "BudgetExceeded"
    p.evidence.audit(state["run_id"], role, "model_call", resp.prompt_hash)
    return resp, update


def build_nodes(p: Ports) -> dict[str, Node]:
    def screen_in(state: FactoryState) -> Update:
        resp, upd = _call(p, state, "classifier", "fast", f"screen spec:\n{state['spec']}")
        p.evidence.write(state["run_id"], "guardrail/in.json", json.dumps({"v": resp.text}))
        if resp.text.strip().upper().startswith("BLOCK"):
            upd["status"] = "GuardrailBlocked"
        return upd

    def architect(state: FactoryState) -> Update:
        resp, upd = _call(p, state, "architect", "strong", f"plan for spec:\n{state['spec']}")
        p.evidence.write(state["run_id"], "plan/plan.md", resp.text)
        upd["plan"] = resp.text
        return upd

    def coder(state: FactoryState) -> Update:
        resp, upd = _call(p, state, "coder", "strong", f"code the plan:\n{state['plan']}")
        p.sandbox.write_file(state["run_id"], "src/app.py", resp.text)
        return upd

    def tester(state: FactoryState) -> Update:
        resp, upd = _call(p, state, "tester", "fast", "add tests for the workspace")
        p.sandbox.write_file(state["run_id"], "tests/test_app.py", resp.text)
        result = p.sandbox.run(state["run_id"], "test", SANDBOX_IMAGE, ["pytest", "-q"])
        # Green comes from the real run's exit code, never from the model's words.
        upd["tests_green"] = result.exit_code == 0 and not result.timed_out
        m = re.search(r"(\d+) passed", result.stdout)
        if "baseline_tests" not in state and m:
            upd["baseline_tests"] = int(m.group(1))
        p.evidence.audit(state["run_id"], "tester", "test_run", str(result.exit_code))
        return upd

    def fixer_tests(state: FactoryState) -> Update:
        rounds = state.get("test_fix_rounds", 0) + 1
        tier = "fast" if rounds == 1 else "strong"  # escalate on later attempts
        resp, upd = _call(p, state, "fixer", tier, f"fix failing tests, round {rounds}")
        p.sandbox.write_file(state["run_id"], "src/app.py", resp.text)
        upd["test_fix_rounds"] = rounds
        return upd

    def gate(state: FactoryState) -> Update:
        s1 = p.gate.stage1_fetch(state["run_id"])
        s2 = p.gate.stage2_test(state["run_id"], state.get("baseline_tests", 0))
        p.evidence.write(state["run_id"], "gate/stage1.log", s1.log)
        p.evidence.write(state["run_id"], "gate/stage2/junit.xml", s2.junit_xml)
        ok = s1.passed and s2.passed and not s2.tamper_findings
        p.evidence.audit(state["run_id"], "gate", "gate_result", str(ok))
        return {} if ok else {"status": "GateFailed"}

    def reviewer(state: FactoryState) -> Update:
        used = state.get("tokens_used", 0)
        upd: Update = {}
        findings: ReviewFindings | None = None
        for _ in range(MAX_REVIEW_PARSE_ATTEMPTS):  # reject and retry on a schema miss
            resp, step = _call(
                p,
                {**state, "tokens_used": used},
                "reviewer",
                "fast",
                "review against the spec",
                "ReviewFindings",
            )
            used = step["tokens_used"]
            upd.update(step)
            try:
                findings = ReviewFindings.model_validate_json(resp.text)
                p.evidence.write(state["run_id"], "review/review.json", resp.text)
                break
            except ValidationError:
                continue
        if findings is None:
            upd["status"] = "ReviewBlocked"
            return upd
        upd["blockers"] = findings.blockers
        return upd

    def fixer_review(state: FactoryState) -> Update:
        resp, upd = _call(
            p,
            state,
            "fixer",
            "strong",
            "fix review findings; full review file:\n"
            + p.evidence.read(state["run_id"], "review/review.json"),
        )
        p.sandbox.write_file(state["run_id"], "src/app.py", resp.text)
        upd["review_fix_rounds"] = state.get("review_fix_rounds", 0) + 1
        return upd

    def docs(state: FactoryState) -> Update:
        resp, upd = _call(p, state, "docs", "fast", "write README and runbook")
        p.sandbox.write_file(state["run_id"], "README.md", resp.text)
        return upd

    def screen_out(state: FactoryState) -> Update:
        resp, upd = _call(p, state, "classifier", "fast", "screen generated docs and code")
        p.evidence.write(state["run_id"], "guardrail/out.json", json.dumps({"v": resp.text}))
        if resp.text.strip().upper().startswith("BLOCK"):
            upd["status"] = "GuardrailBlocked"
        return upd

    def package(state: FactoryState) -> Update:
        files = p.sandbox.list_dir(state["run_id"])
        digest = p.evidence.write(state["run_id"], "SHA256SUMS", "\n".join(sorted(files)))
        p.approval.request(state["run_id"], digest)
        return {"package_sha256": digest}

    def approval(state: FactoryState) -> Update:
        # Pauses the graph; resume with Command(resume="approved" | "rejected" | "expired").
        decision = interrupt({"run_id": state["run_id"], "package": state["package_sha256"]})
        p.evidence.write(state["run_id"], "approval.json", json.dumps({"decision": decision}))
        p.evidence.audit(state["run_id"], "approver", "decision", str(decision))
        status = {"approved": "Accepted", "expired": "Expired"}.get(str(decision), "Rejected")
        return {"status": status}

    return {
        "screen_in": screen_in,
        "architect": architect,
        "coder": coder,
        "tester": tester,
        "fixer_tests": fixer_tests,
        "gate": gate,
        "reviewer": reviewer,
        "fixer_review": fixer_review,
        "docs": docs,
        "screen_out": screen_out,
        "package": package,
        "approval": approval,
    }
