"""Phase 2: the Gemini adapter, exercised with scripted chat models. No network, no key."""

from __future__ import annotations

import ast
import random
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from factory.adapters.fakes import FakeApproval, FakeEvidence, FakeGate, FakeSandbox
from factory.adapters.gemini import GeminiModel, ModelCallError, is_transient
from factory.config import ModelConfig, load_model_config
from factory.graph import build_graph
from factory.nodes.pipeline import Ports
from factory.ports import ModelPort
from factory.ports.models import CommandResult, ModelRequest
from factory.state import ReviewFindings

CFG = ModelConfig(
    tiers={"strong": "model-strong", "fast": "model-fast"},
    roles={"coder": "strong", "reviewer": "fast"},
)


class HttpError(Exception):
    def __init__(self, status: int, headers: dict[str, str] | None = None) -> None:
        super().__init__("")  # empty message on purpose
        self.status_code = status
        self.response = type("R", (), {"headers": headers or {}})()


class Scripted(BaseChatModel):
    """Plays back items: str -> reply, Exception -> raised. Structured output is scripted too."""

    items: list[Any]
    seen_models: list[str] = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def _generate(self, messages: list[BaseMessage], stop: Any = None, **kw: Any) -> ChatResult:
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        msg = AIMessage(
            content=item, usage_metadata={"input_tokens": 7, "output_tokens": 3, "total_tokens": 10}
        )
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def with_structured_output(self, schema: Any, **kw: Any) -> Any:
        from langchain_core.runnables import RunnableLambda

        def run(prompt: Any) -> dict[str, Any]:
            raw = self.invoke(prompt)
            try:
                return {
                    "raw": raw,
                    "parsed": schema.model_validate_json(raw.content),
                    "parsing_error": None,
                }
            except Exception as e:
                return {"raw": raw, "parsed": None, "parsing_error": e}

        return RunnableLambda(run)


def adapter(items: list[Any], sleeps: list[float] | None = None, **kw: Any) -> GeminiModel:
    chat = Scripted(items=items)
    log = sleeps if sleeps is not None else []
    return GeminiModel(CFG, lambda _m, _t: chat, sleep=log.append, rng=random.Random(0), **kw)


REQ = ModelRequest(role="coder", tier="strong", prompt="p")


def test_adapter_is_a_model_port_and_reports_usage() -> None:
    m = adapter(["hello"])
    assert isinstance(m, ModelPort)
    r = m.invoke(REQ)
    assert (r.text, r.model_id, r.input_tokens, r.output_tokens) == ("hello", "model-strong", 7, 3)
    assert len(r.prompt_hash) == 64


def test_transient_errors_retry_with_backoff_then_succeed() -> None:
    sleeps: list[float] = []
    m = adapter([HttpError(503), HttpError(429), "ok"], sleeps)
    assert m.invoke(REQ).text == "ok"
    assert len(sleeps) == 2


def test_retry_after_header_is_honoured() -> None:
    sleeps: list[float] = []
    m = adapter([HttpError(429, {"Retry-After": "7"}), "ok"], sleeps)
    m.invoke(REQ)
    assert sleeps == [7.0]


def test_attempts_are_capped_and_chain_reported() -> None:
    m = adapter([HttpError(500)] * 4, max_attempts=3)
    with pytest.raises(ModelCallError) as ei:
        m.invoke(REQ)
    assert "3 attempt" in str(ei.value) and "HttpError(500)" in str(ei.value)


def test_non_transient_error_is_not_retried() -> None:
    sleeps: list[float] = []
    m = adapter([HttpError(400), "never"], sleeps)
    with pytest.raises(ModelCallError):
        m.invoke(REQ)
    assert sleeps == []


def test_classification_uses_type_and_cause_chain_not_message() -> None:
    try:
        try:
            raise ConnectionResetError()  # empty message, like the wrapped network error
        except ConnectionResetError as inner:
            raise RuntimeError("") from inner
    except RuntimeError as outer:
        assert is_transient(outer)
    # A transient-sounding message with a non-transient type is not retried.
    assert not is_transient(ValueError("429 timeout connection reset"))


def test_structured_output_parses_and_retries_on_schema_miss() -> None:
    good = '{"critical":0,"high":1,"summary":"x"}'
    m = adapter(["not json", good])
    r = m.invoke(
        ModelRequest(role="reviewer", tier="fast", prompt="p", schema_name="ReviewFindings")
    )
    assert ReviewFindings.model_validate_json(r.text).high == 1


def test_config_rules() -> None:
    cfg = load_model_config("models.yaml")
    assert cfg.model_id(cfg.roles["coder"]) and cfg.roles["reviewer"] != cfg.roles["coder"]
    with pytest.raises(ValueError):
        ModelConfig(tiers={"a": "x"}, roles={"coder": "a", "reviewer": "a"})
    with pytest.raises(ValueError):
        ModelConfig(
            tiers={"a": "x", "b": "y"}, roles={"coder": "a", "reviewer": "b"}, temperature=0.9
        )


def test_graph_runs_through_the_gemini_adapter_on_prior_auth_spec() -> None:
    """Plan and code come from a scripted chat model, not Gemini: this proves the wiring,
    not model quality."""
    import json

    src = "from fastapi import FastAPI\napp = FastAPI()\n"
    code = json.dumps({"files": [{"path": "src/app.py", "content": src}]})
    tests_bundle = json.dumps(
        {"files": [{"path": "tests/test_app.py", "content": "def test_x():\n    assert True\n"}]}
    )
    review = '{"critical":0,"high":0,"summary":"ok"}'
    allow = '{"verdict":"ALLOW"}'
    items = [allow, "PLAN: three endpoints", code, tests_bundle, review, "# README", allow]
    chat = Scripted(items=items)
    model = GeminiModel(CFG, lambda _m, _t: chat, sleep=lambda _s: None)
    sandbox = FakeSandbox(lambda _c: CommandResult(exit_code=0, stdout="3 passed"))
    ports = Ports(model, sandbox, FakeGate(), FakeApproval(), FakeEvidence())
    g = build_graph(ports, InMemorySaver())
    cfg = {"configurable": {"thread_id": "t"}}
    spec = open("specs/prior-auth.md").read()
    g.invoke({"run_id": "r", "spec": spec, "status": "running"}, cfg)
    assert g.get_state(cfg).next == ("approval",)
    g.invoke(Command(resume="approved"), cfg)
    st = g.get_state(cfg).values
    assert st["status"] == "Accepted" and st["tokens_used"] > 0
    ast.parse(sandbox.read_file("r", "src/app.py"))  # the "code" at least parses
    assert ports.evidence.read("r", "plan/plan.md").startswith("PLAN")


def test_text_of_handles_gemini_block_content() -> None:
    from factory.adapters.gemini import text_of

    blocks = [
        {"type": "text", "text": '{"verdict":"ALLOW"}', "extras": {"signature": "abc"}},
        {"type": "thinking", "thinking": "hidden"},
    ]
    assert text_of(blocks) == '{"verdict":"ALLOW"}'
    assert text_of("plain") == "plain" and text_of(["a", "b"]) == "ab"
