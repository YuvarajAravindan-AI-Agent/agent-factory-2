"""Graph nodes. Each node closes over the ports; none imports a vendor client."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from typing import Any

from langgraph.types import interrupt
from pydantic import ValidationError

from factory.guardrails import classify, scan_files, scan_text
from factory.nodes import prompts
from factory.nodes.bundle import is_test_path, parse_bundle, safe_path, strip_fences
from factory.nodes.version import FACTORY_VERSION, prompts_version
from factory.ports import ApprovalPort, EvidencePort, GatePort, ModelPort, SandboxPort
from factory.ports.models import ModelRequest, ModelResponse
from factory.state import (
    MAX_REVIEW_PARSE_ATTEMPTS,
    FactoryState,
    ReviewFindings,
)
from factory.tracing import run_metadata, traced

Update = dict[str, Any]
Node = Callable[[FactoryState], Update]

SANDBOX_IMAGE = "factory-sandbox"
SANDBOX_TEST_CMD = ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]


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
    request = ModelRequest(role=role, tier=tier, prompt=prompt, schema_name=schema_name)
    resp = traced(f"model:{role}", "llm", **run_metadata(state, role, tier))(p.model.invoke)(
        request
    )
    used = state.get("tokens_used", 0) + resp.input_tokens + resp.output_tokens
    update: Update = {
        "tokens_used": used,
        "calls": [
            {
                "role": role,
                "tier": tier,
                "model_id": resp.model_id,
                "prompt_hash": resp.prompt_hash,
                "tokens": resp.input_tokens + resp.output_tokens,
            }
        ],
    }
    budget = state.get("token_budget", 0)
    if budget and used > budget:
        update["status"] = "BudgetExceeded"
    p.evidence.audit(state["run_id"], role, "model_call", resp.prompt_hash)
    return resp, update


def _classifier_update(state: FactoryState, resp: ModelResponse) -> Update:
    return {
        "tokens_used": state.get("tokens_used", 0) + resp.input_tokens + resp.output_tokens,
        "calls": [
            {
                "role": "classifier",
                "tier": "fast",
                "model_id": resp.model_id,
                "prompt_hash": resp.prompt_hash,
                "tokens": resp.input_tokens + resp.output_tokens,
            }
        ],
    }


def build_nodes(p: Ports) -> dict[str, Node]:
    def screen(state: FactoryState, artifact: str, kind: str, evidence_path: str) -> Update:
        """Layer A (deterministic) then layer B (separate model call). Block on either."""
        run = state["run_id"]
        hits = scan_text(artifact) if kind == "spec" else []
        upd: Update = {}
        verdict_doc: dict[str, Any] = {"layer_a": [str(h) for h in hits]}
        if hits:
            upd["status"] = "GuardrailBlocked"
            upd["guardrail_findings"] = [str(h) for h in hits]
        else:  # the classifier never sees an artifact that layer A already rejected
            v, resp = classify(p.model, artifact, kind)
            verdict_doc["layer_b"] = {
                "verdict": v.verdict,
                "reason": v.reason,
                "prompt_hash": resp.prompt_hash,
            }
            upd.update(_classifier_update(state, resp))
            if v.blocked:
                upd["status"] = "GuardrailBlocked"
        p.evidence.write(run, evidence_path, json.dumps(verdict_doc, sort_keys=True))
        p.evidence.audit(
            run, "guardrail", f"screen_{kind}", json.dumps(verdict_doc, sort_keys=True)
        )
        return upd

    def screen_in(state: FactoryState) -> Update:
        return screen(state, state["spec"], "spec", "guardrail/in.json")

    def workspace_text(run_id: str, only_tests: bool | None = None) -> str:
        parts = []
        for f in sorted(p.sandbox.list_dir(run_id)):
            if only_tests is not None and is_test_path(f) != only_tests:
                continue
            parts.append(f"### FILE: {f}\n{p.sandbox.read_file(run_id, f)}")
        return "\n\n".join(parts) or "(no files yet)"

    def write_files(
        state: FactoryState, text: str, fallback_path: str, *, allow_tests: bool, allow_src: bool
    ) -> None:
        """Write a CodeBundle (or, for plain text, one fallback file). Paths are validated and
        the role may only touch the file kinds it owns: the Fixer can never edit tests."""
        run = state["run_id"]
        bundle = parse_bundle(text)
        files = (
            [(f.path, f.content) for f in bundle.files]
            if bundle
            else [(fallback_path, strip_fences(text))]
        )
        for path, content in files:
            rel = safe_path(path)
            ok = rel is not None and (allow_tests if is_test_path(rel) else allow_src)
            if not ok:
                p.evidence.audit(run, "executor", "rejected_path", str(path)[:200])
                continue
            assert rel is not None
            p.sandbox.write_file(run, rel, content)

    def architect(state: FactoryState) -> Update:
        resp, upd = _call(p, state, "architect", "strong", prompts.architect(state["spec"]))
        p.evidence.write(state["run_id"], "plan/plan.md", resp.text)
        upd["plan"] = resp.text
        return upd

    def coder(state: FactoryState) -> Update:
        resp, upd = _call(
            p, state, "coder", "strong", prompts.coder(state["spec"], state["plan"]), "CodeBundle"
        )
        write_files(state, resp.text, "src/app.py", allow_tests=False, allow_src=True)
        return upd

    def tester(state: FactoryState) -> Update:
        run = state["run_id"]
        upd: Update = {}
        if not state.get("tests_written"):  # authored exactly once; later rounds only re-run
            prompt = prompts.tester(state["spec"], workspace_text(run, False))
            resp, upd = _call(p, state, "tester", "fast", prompt, "CodeBundle")
            write_files(state, resp.text, "tests/test_app.py", allow_tests=True, allow_src=False)
            upd["tests_written"] = True
        result = p.sandbox.run(run, "test", SANDBOX_IMAGE, SANDBOX_TEST_CMD)
        # Green comes from the real run's exit code, never from the model's words.
        upd["tests_green"] = result.exit_code == 0 and not result.timed_out
        upd["last_test_log"] = (result.stdout + result.stderr)[-3000:]
        m = re.search(r"(\d+) passed", result.stdout)
        if "baseline_tests" not in state and m:
            upd["baseline_tests"] = int(m.group(1))
            p.gate.record_manifest(run)  # freeze the Tester's files for the gate
        p.evidence.audit(run, "tester", "test_run", str(result.exit_code))
        return upd

    def fixer_tests(state: FactoryState) -> Update:
        run = state["run_id"]
        rounds = state.get("test_fix_rounds", 0) + 1
        tier = "fast" if rounds == 1 else "strong"  # escalate on later attempts
        prompt = prompts.fixer_tests(
            state["spec"], workspace_text(run), state.get("last_test_log", ""), rounds
        )
        resp, upd = _call(p, state, "fixer", tier, prompt, "CodeBundle")
        write_files(state, resp.text, "src/app.py", allow_tests=False, allow_src=True)
        upd["test_fix_rounds"] = rounds
        return upd

    def gate(state: FactoryState) -> Update:
        meta = run_metadata(state)
        s1 = traced("gate.stage1", "tool", **meta)(p.gate.stage1_fetch)(state["run_id"])
        s2 = traced("gate.stage2", "tool", **meta)(p.gate.stage2_test)(
            state["run_id"], state.get("baseline_tests", 0)
        )
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
                prompts.reviewer(state["spec"], workspace_text(state["run_id"])),
                "ReviewFindings",
            )
            used = step["tokens_used"]
            upd["tokens_used"] = used
            upd["calls"] = [*upd.get("calls", []), *step["calls"]]
            if "status" in step:
                upd["status"] = step["status"]
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
        run = state["run_id"]
        prompt = prompts.fixer_review(
            state["spec"], workspace_text(run), p.evidence.read(run, "review/review.json")
        )
        resp, upd = _call(p, state, "fixer", "strong", prompt, "CodeBundle")
        write_files(state, resp.text, "src/app.py", allow_tests=False, allow_src=True)
        upd["review_fix_rounds"] = state.get("review_fix_rounds", 0) + 1
        return upd

    def docs(state: FactoryState) -> Update:
        resp, upd = _call(
            p, state, "docs", "fast", prompts.docs(state["spec"], workspace_text(state["run_id"]))
        )
        p.sandbox.write_file(state["run_id"], "README.md", strip_fences(resp.text))
        return upd

    def read_workspace(run_id: str) -> dict[str, str]:
        return {f: p.sandbox.read_file(run_id, f) for f in sorted(p.sandbox.list_dir(run_id))}

    def screen_out(state: FactoryState) -> Update:
        run = state["run_id"]
        files = read_workspace(run)
        hits = scan_files(files)
        upd: Update = {}
        doc: dict[str, Any] = {"layer_a": [str(h) for h in hits]}
        if hits:
            upd["status"] = "GuardrailBlocked"
            upd["guardrail_findings"] = [str(h) for h in hits]
        else:
            bundle = "\n".join(f"### {n}\n{t}" for n, t in files.items())
            v, resp = classify(p.model, bundle, "generated-code")
            doc["layer_b"] = {
                "verdict": v.verdict,
                "reason": v.reason,
                "prompt_hash": resp.prompt_hash,
            }
            upd.update(_classifier_update(state, resp))
            if v.blocked:
                upd["status"] = "GuardrailBlocked"
        p.evidence.write(run, "guardrail/out.json", json.dumps(doc, sort_keys=True))
        p.evidence.audit(run, "guardrail", "screen_out", json.dumps(doc, sort_keys=True))
        return upd

    def package(state: FactoryState) -> Update:
        run = state["run_id"]
        files = read_workspace(run)
        digest = p.evidence.package(run, files)
        manifest = {
            "run_id": run,
            "factory_version": FACTORY_VERSION,
            "prompts_version": prompts_version(),
            "spec_sha256": hashlib.sha256(state["spec"].encode()).hexdigest(),
            "package_sha256": digest,
            "model_calls": state.get("calls", []),
            "tokens_used": state.get("tokens_used", 0),
            "test_fix_rounds": state.get("test_fix_rounds", 0),
            "review_fix_rounds": state.get("review_fix_rounds", 0),
            "baseline_tests": state.get("baseline_tests", 0),
        }
        p.evidence.write(run, "manifest.json", json.dumps(manifest, sort_keys=True, indent=1))
        p.evidence.audit(run, "factory", "package", digest)
        p.approval.request(run, digest)
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
