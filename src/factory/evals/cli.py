"""factory evals run|compare|release-check."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from factory.adapters.recording import RecordingModel, ReplayModel
from factory.evals.compare import Policy, blocking, compare
from factory.evals.runner import Case, load_dataset, run_suite


def _make_factory(args: argparse.Namespace) -> Any:
    stamp = time.strftime("%Y%m%d%H%M%S")
    rec_dir = Path(args.recordings)
    sandbox_local = None
    if args.backend == "local":
        if args.model == "real":
            raise SystemExit("refusing: the local backend has no isolation; use --backend executor")
        from factory.evals.local import LocalGate, LocalSandbox

        sandbox_local = LocalSandbox()
    replays: list[ReplayModel] = []
    recorders: dict[str, RecordingModel] = {}

    def make(case: Case) -> tuple[Any, Any, Any]:
        if args.model == "replay":
            model: Any = ReplayModel(rec_dir / f"{case.name}.json")
            replays.append(model)
        else:
            from factory import runtime as rt

            model = rt.build_model()
            if args.record:
                model = recorders[case.name] = RecordingModel(model)
        if sandbox_local is not None:
            return model, sandbox_local, LocalGate(sandbox_local)
        from factory.adapters.executor_client import ExecutorClient

        client = ExecutorClient(os.environ.get("AF_EXECUTOR_SOCK", "/run/factory/executor.sock"))
        return model, client, client

    return make, stamp, replays, recorders


def main(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(prog="factory evals")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--model", choices=["real", "replay"], default="replay")
    r.add_argument("--backend", choices=["executor", "local"], default="local")
    r.add_argument("--dataset", default="evals/golden/dataset.jsonl")
    r.add_argument("--recordings", default="evals/golden/recordings")
    r.add_argument("--record", action="store_true", help="save real responses to --recordings")
    r.add_argument("--out", required=True)
    c = sub.add_parser("compare")
    c.add_argument("baseline")
    c.add_argument("current")
    c.add_argument("--policy", default="evals/policy.yaml")
    args = ap.parse_args(argv)

    if args.cmd == "run":
        make, stamp, replays, recorders = _make_factory(args)
        meta = {"model": args.model, "backend": args.backend, "stamp": stamp}
        result = run_suite(load_dataset(args.dataset), make, stamp, meta)
        result["meta"]["prompt_drift"] = sum(m.drift for m in replays)
        for name, rec in recorders.items():
            rec.dump(Path(args.recordings) / f"{name}.json")
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(result, indent=1, sort_keys=True))
        s = result["summary"]
        drift = result["meta"]["prompt_drift"]
        print(
            f"{s['cases']} cases | reached approval {s['reached_approval_rate']:.0%} | "
            f"fix rounds {s['fix_rounds']} | blockers {s['blockers']} | tokens {s['tokens']} | "
            f"{s['wall_s']}s | prompt drift {drift}"
        )
        for k in result["cases"]:
            fix = f"{k['test_fix_rounds']}/{k['review_fix_rounds']}"
            print(
                f"  {k['name']:14} reached={k['reached_approval']} tests={k['tests_total']} "
                f"fix={fix} tokens={k['tokens']} {k['error']}"
            )
        return
    base = json.loads(Path(args.baseline).read_text())
    cur = json.loads(Path(args.current).read_text())
    msgs = compare(base, cur, Policy.load(args.policy))
    for m in msgs:
        print(("BLOCK " if m in blocking(msgs) else "") + m)
    if blocking(msgs):
        print(f"RELEASE BLOCKED: {len(blocking(msgs))} regression(s)")
        sys.exit(1)
    print("no regressions: release may proceed")
