"""Optional: push the golden specs to LangSmith and run `evaluate` over them.

Needs LANGSMITH_API_KEY. Tracing/hosting a regulated client's specs there is a data-region and
confidentiality decision (see SPEC section 10); golden specs are synthetic. The release gate does
NOT depend on this: it uses the local baseline comparison."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from factory.evals.runner import Case, load_dataset
from factory.nodes.version import FACTORY_VERSION

DATASET = "agent-factory-golden"


def score_reached(outputs: dict[str, Any], reference_outputs: dict[str, Any]) -> dict[str, Any]:
    return {"key": "reached_approval", "score": 1.0 if outputs.get("reached_approval") else 0.0}


def score_min_tests(outputs: dict[str, Any], reference_outputs: dict[str, Any]) -> dict[str, Any]:
    need = int(reference_outputs.get("min_tests", 0))
    return {"key": "enough_tests", "score": 1.0 if outputs.get("tests_total", 0) >= need else 0.0}


def score_no_blockers(outputs: dict[str, Any], reference_outputs: dict[str, Any]) -> dict[str, Any]:
    return {"key": "no_blocking_findings", "score": 1.0 if outputs.get("blockers", 1) == 0 else 0.0}


EVALUATORS = [score_reached, score_min_tests, score_no_blockers]


def sync_dataset(cases: list[Case]) -> None:
    from langsmith import Client

    client = Client()
    if client.has_dataset(dataset_name=DATASET):
        return
    ds = client.create_dataset(DATASET, description="Agent Factory golden specs (synthetic)")
    client.create_examples(
        dataset_id=ds.id,
        inputs=[{"name": c.name, "spec": Path(c.spec).read_text()} for c in cases],
        outputs=[c.expect for c in cases],
    )


def evaluate_suite(target: Any, prefix: str) -> Any:
    from langsmith import evaluate

    return evaluate(
        target,
        data=DATASET,
        evaluators=EVALUATORS,
        experiment_prefix=prefix,
        metadata={"factory_version": FACTORY_VERSION},
    )


def main(prefix: str, target: Any) -> None:
    if not os.environ.get("LANGSMITH_API_KEY"):
        raise SystemExit("LANGSMITH_API_KEY is not set; the local baseline gate still works")
    sync_dataset(load_dataset())
    evaluate_suite(target, prefix)
