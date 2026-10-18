"""The LangGraph state graph (SPEC section 6). Loop caps are visible in the routers."""

from __future__ import annotations

from typing import Any, cast

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from factory.nodes.pipeline import Ports, build_nodes
from factory.state import (
    MAX_REVIEW_FIX_ROUNDS,
    MAX_TEST_FIX_ROUNDS,
    STOP_STATES,
    FactoryState,
)


def _stopped(state: FactoryState) -> bool:
    return state.get("status") in STOP_STATES


def _after(next_node: str) -> Any:
    def route(state: FactoryState) -> str:
        return END if _stopped(state) else next_node

    return route


def route_after_tester(state: FactoryState) -> str:
    if _stopped(state):
        return END
    if state.get("tests_green"):
        return "gate"
    if state.get("test_fix_rounds", 0) < MAX_TEST_FIX_ROUNDS:
        return "fixer_tests"
    return "stop_tests_failed"


def route_after_review(state: FactoryState) -> str:
    if _stopped(state):
        return END
    if state.get("blockers", 0) == 0:
        return "docs"
    if state.get("review_fix_rounds", 0) < MAX_REVIEW_FIX_ROUNDS:
        return "fixer_review"
    return "stop_review_blocked"


def build_graph(
    ports: Ports, checkpointer: BaseCheckpointSaver[Any] | None = None
) -> CompiledStateGraph[Any, Any, Any, Any]:
    g: StateGraph[Any, Any, Any, Any] = StateGraph(FactoryState)
    for name, fn in build_nodes(ports).items():
        g.add_node(name, cast(Any, fn))
    g.add_node("stop_tests_failed", lambda _s: {"status": "TestsFailed"})
    g.add_node("stop_review_blocked", lambda _s: {"status": "ReviewBlocked"})

    g.add_edge(START, "screen_in")
    g.add_conditional_edges("screen_in", _after("architect"))
    g.add_conditional_edges("architect", _after("coder"))
    g.add_conditional_edges("coder", _after("tester"))
    g.add_conditional_edges("tester", route_after_tester)
    g.add_conditional_edges("fixer_tests", _after("tester"))
    g.add_conditional_edges("gate", _after("reviewer"))
    g.add_conditional_edges("reviewer", route_after_review)
    # After the one review fix: retest, then gate, then re-review.
    g.add_conditional_edges("fixer_review", _after("tester"))
    g.add_conditional_edges("docs", _after("screen_out"))
    g.add_conditional_edges("screen_out", _after("package"))
    g.add_edge("package", "approval")
    g.add_edge("approval", END)
    g.add_edge("stop_tests_failed", END)
    g.add_edge("stop_review_blocked", END)
    return g.compile(checkpointer=checkpointer)
