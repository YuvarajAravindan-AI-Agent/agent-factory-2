"""Optional LangSmith tracing. Off unless LANGSMITH_TRACING=true; a run never depends on it.

Traces hold prompts and generated code, so set FACTORY_TRACE_HIDE_IO=true for regulated work:
inputs and outputs are then replaced by their SHA-256 and length, and only metadata is sent.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping
from typing import Any, TypeVar

from factory.nodes.version import FACTORY_VERSION, prompts_version

F = TypeVar("F", bound=Callable[..., Any])


def enabled() -> bool:
    return os.environ.get("LANGSMITH_TRACING", "").lower() == "true"


def _digest(obj: Any) -> dict[str, Any]:
    raw = json.dumps(obj, default=str, sort_keys=True)
    return {"sha256": hashlib.sha256(raw.encode()).hexdigest(), "len": len(raw)}


def hide_io() -> bool:
    return os.environ.get("FACTORY_TRACE_HIDE_IO", "").lower() == "true"


def base_metadata(**extra: Any) -> dict[str, Any]:
    return {"factory_version": FACTORY_VERSION, "prompts_version": prompts_version(), **extra}


def traced(name: str, run_type: str = "chain", **fixed_meta: Any) -> Callable[[F], F]:
    """Wrap a function with @traceable when tracing is on; otherwise return it unchanged.
    The decision is made per call so tests and long-lived services can toggle it."""

    def deco(fn: F) -> F:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if not enabled():
                return fn(*args, **kwargs)
            from langsmith import traceable

            opts: dict[str, Any] = {
                "name": name,
                "run_type": run_type,
                "metadata": base_metadata(**fixed_meta),
                "tags": [f"factory:{FACTORY_VERSION}"],
            }
            if hide_io():
                opts["process_inputs"] = lambda inputs: _digest(inputs)
                opts["process_outputs"] = lambda outputs: _digest(outputs)
            return traceable(**opts)(fn)(*args, **kwargs)

        wrapper.__name__ = getattr(fn, "__name__", name)
        wrapper.__doc__ = fn.__doc__
        return wrapper  # type: ignore[return-value]

    return deco


def run_metadata(
    state: Mapping[str, Any], role: str = "", tier: str = "", model_id: str = ""
) -> dict[str, Any]:
    """Metadata attached to a trace: run ID, role, tier, model ID, spec hash, loop counters."""
    spec = state.get("spec", "")
    return base_metadata(
        run_id=state.get("run_id", ""),
        role=role,
        tier=tier,
        model_id=model_id,
        spec_sha256=hashlib.sha256(str(spec).encode()).hexdigest(),
        test_fix_rounds=state.get("test_fix_rounds", 0),
        review_fix_rounds=state.get("review_fix_rounds", 0),
    )
