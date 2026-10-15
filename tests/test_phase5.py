"""Phase 5: tracing is optional and carries the right metadata; the regression policy blocks."""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from factory.adapters.fakes import FakeModel
from factory.adapters.recording import RecordingModel, ReplayModel
from factory.evals.compare import Policy, blocking, compare
from factory.nodes.version import FACTORY_VERSION, prompts_version
from factory.ports.models import ModelRequest
from factory.tracing import enabled, run_metadata, traced


def case(name: str, **kw: Any) -> dict[str, Any]:
    base = {
        "name": name, "vertical": "x", "reached_approval": True, "stop_state": "",
        "tests_total": 30, "test_fix_rounds": 0, "review_fix_rounds": 0, "blockers": 0,
        "tokens": 1000, "model_calls": 7, "wall_s": 10.0, "error": "",
    }  # fmt: skip
    return {**base, **kw}


def suite(*cases: dict[str, Any]) -> dict[str, Any]:
    n = len(cases)
    return {
        "prompts_version": "abc",
        "cases": list(cases),
        "summary": {
            "cases": n,
            "reached_approval_rate": sum(c["reached_approval"] for c in cases) / n,
            "fix_rounds": sum(c["test_fix_rounds"] + c["review_fix_rounds"] for c in cases),
            "blockers": sum(c["blockers"] for c in cases),
            "tokens": sum(c["tokens"] for c in cases),
            "wall_s": sum(c["wall_s"] for c in cases),
        },
    }


BASE = suite(case("a"), case("b"))
POL = Policy()


def test_identical_run_passes() -> None:
    assert blocking(compare(BASE, copy.deepcopy(BASE), POL)) == []


@pytest.mark.parametrize(
    ("change", "needle"),
    [
        ({"reached_approval": False, "stop_state": "GateFailed"}, "no longer reaches approval"),
        ({"test_fix_rounds": 1}, "fix rounds rose"),
        ({"blockers": 1}, "blocking review findings rose"),
        ({"tokens": 2000}, "tokens rose"),
        ({"tests_total": 10}, "tests 10"),
        ({"error": "boom"}, "run crashed"),
    ],
)
def test_each_regression_blocks(change: dict[str, Any], needle: str) -> None:
    cur = suite(case("a", **change), case("b"))
    msgs = blocking(compare(BASE, cur, POL))
    assert any(needle in m for m in msgs), msgs


def test_pass_rate_drop_and_missing_case_block() -> None:
    cur = suite(case("a", reached_approval=False, stop_state="TestsFailed"), case("b"))
    assert any("gate pass rate fell" in m for m in blocking(compare(BASE, cur, POL)))
    assert any("missing" in m for m in blocking(compare(BASE, suite(case("a")), POL)))


def test_improvement_and_prompt_change_do_not_block() -> None:
    better = suite(case("a", tokens=900, tests_total=40), case("b"))
    better["prompts_version"] = "new"
    msgs = compare(BASE, better, POL)
    assert blocking(msgs) == [] and any(m.startswith("note:") for m in msgs)


def test_wall_clock_gated_only_when_enabled() -> None:
    slow = suite(case("a", wall_s=500.0), case("b"))
    assert blocking(compare(BASE, slow, POL)) == []
    assert any("wall-clock" in m for m in blocking(compare(BASE, slow, Policy(wall_growth=1.0))))


def test_tracing_off_is_a_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    assert not enabled()
    assert traced("x")(lambda a, b: a + b)(1, 2) == 3


def test_run_metadata_has_required_fields() -> None:
    md = run_metadata({"run_id": "r1", "spec": "s", "test_fix_rounds": 2}, "coder", "strong", "m")
    for k in ("run_id", "role", "tier", "model_id", "spec_sha256", "test_fix_rounds",
              "review_fix_rounds", "factory_version", "prompts_version"):  # fmt: skip
        assert k in md
    assert md["factory_version"] == FACTORY_VERSION and md["prompts_version"] == prompts_version()


@pytest.fixture
def runs() -> Iterator[list[dict[str, Any]]]:
    """Captures what would be sent to LangSmith, without any network."""
    from langsmith import Client
    from langsmith.run_helpers import tracing_context

    captured: list[dict[str, Any]] = []

    class Capture(Client):
        def create_run(self, *a: Any, **kw: Any) -> None:  # type: ignore[override]
            captured.append(kw)

        def update_run(self, *a: Any, **kw: Any) -> None:  # type: ignore[override]
            return None

    with tracing_context(enabled=True, client=Capture(api_key="test", auto_batch_tracing=False)):
        yield captured


def test_tracing_on_sends_metadata_and_tags(
    monkeypatch: pytest.MonkeyPatch, runs: list[dict[str, Any]]
) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.delenv("FACTORY_TRACE_HIDE_IO", raising=False)
    out = traced("model:coder", "llm", role="coder", run_id="r1")(lambda p: p.upper())(
        "secret code"
    )
    assert out == "SECRET CODE"
    r = next(x for x in runs if x.get("name") == "model:coder")
    md = r["extra"]["metadata"]
    assert (
        md["role"] == "coder" and md["run_id"] == "r1" and md["factory_version"] == FACTORY_VERSION
    )
    assert f"factory:{FACTORY_VERSION}" in r["tags"]
    assert "secret code" in json.dumps(r["inputs"])


def test_hide_io_replaces_inputs_and_outputs_with_digests(
    monkeypatch: pytest.MonkeyPatch, runs: list[dict[str, Any]]
) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("FACTORY_TRACE_HIDE_IO", "true")
    traced("model:coder", "llm")(lambda p: p.upper())("secret code")
    sent = json.dumps([r.get("inputs") for r in runs])
    assert "secret code" not in sent and "sha256" in sent


def test_record_then_replay_roundtrip_and_drift(tmp_path: Path) -> None:
    rec = RecordingModel(FakeModel({"coder": ["A", "B"]}))
    rec.invoke(ModelRequest(role="coder", tier="strong", prompt="p1"))
    rec.invoke(ModelRequest(role="coder", tier="strong", prompt="p2"))
    f = tmp_path / "r.json"
    rec.dump(f)
    rp = ReplayModel(f)
    assert rp.invoke(ModelRequest(role="coder", tier="strong", prompt="p1")).text == "A"
    assert rp.drift == 0
    assert rp.invoke(ModelRequest(role="coder", tier="strong", prompt="CHANGED")).text == "B"
    assert rp.drift == 1  # recording is stale for that prompt
    with pytest.raises(RuntimeError, match="exhausted"):
        rp.invoke(ModelRequest(role="coder", tier="strong", prompt="p3"))


def test_langsmith_evaluators_and_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from factory.evals import langsmith_sync as ls

    ok = {"reached_approval": True, "tests_total": 30, "blockers": 0}
    ref = {"min_tests": 20}
    assert [e(ok, ref)["score"] for e in ls.EVALUATORS] == [1.0, 1.0, 1.0]
    bad = {"reached_approval": False, "tests_total": 5, "blockers": 2}
    assert [e(bad, ref)["score"] for e in ls.EVALUATORS] == [0.0, 0.0, 0.0]
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="LANGSMITH_API_KEY"):
        ls.main("x", lambda _i: {})
