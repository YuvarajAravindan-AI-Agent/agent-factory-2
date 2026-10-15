"""Regression policy: compare a current eval run with the recorded baseline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import yaml


@dataclass
class Policy:
    max_extra_fix_rounds: int = 0  # total over all cases
    max_extra_blockers: int = 0
    token_growth: float = 0.25  # +25% total tokens allowed
    tests_floor: float = 0.8  # tests_total >= 80% of baseline per case
    wall_growth: float | None = None  # None = wall-clock is not gated (scripted runs)
    wall_slack_s: float = 30.0
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str) -> Policy:
        raw = yaml.safe_load(open(path)) or {}  # noqa: SIM115
        return cls(**{k: v for k, v in raw.items() if k in cls.__dataclass_fields__})


def compare(baseline: dict[str, Any], current: dict[str, Any], pol: Policy) -> list[str]:
    """Return regression messages; an empty list means the release may proceed."""
    out: list[str] = []
    b = {c["name"]: c for c in baseline["cases"]}
    c = {x["name"]: x for x in current["cases"]}
    for name in b:
        if name not in c:
            out.append(f"{name}: case missing from current run")
    for name, cur in c.items():
        base = b.get(name)
        if base is None:
            continue  # new case: nothing to regress against
        if base["reached_approval"] and not cur["reached_approval"]:
            out.append(f"{name}: no longer reaches approval (now {cur['stop_state'] or 'error'})")
        if cur["error"]:
            out.append(f"{name}: run crashed: {cur['error']}")
        if base["tests_total"] and cur["tests_total"] < base["tests_total"] * pol.tests_floor:
            floor = f"{pol.tests_floor:.0%} of {base['tests_total']}"
            out.append(f"{name}: tests {cur['tests_total']} < {floor}")
    bs, cs = baseline["summary"], current["summary"]
    if cs["reached_approval_rate"] < bs["reached_approval_rate"]:
        was, now = bs["reached_approval_rate"], cs["reached_approval_rate"]
        out.append(f"gate pass rate fell {was:.0%} -> {now:.0%}")
    if cs["fix_rounds"] > bs["fix_rounds"] + pol.max_extra_fix_rounds:
        out.append(f"fix rounds rose {bs['fix_rounds']} -> {cs['fix_rounds']}")
    if cs["blockers"] > bs["blockers"] + pol.max_extra_blockers:
        out.append(f"blocking review findings rose {bs['blockers']} -> {cs['blockers']}")
    if bs["tokens"] and cs["tokens"] > bs["tokens"] * (1 + pol.token_growth):
        out.append(f"tokens rose {bs['tokens']} -> {cs['tokens']} (> +{pol.token_growth:.0%})")
    if (
        pol.wall_growth is not None
        and cs["wall_s"] > bs["wall_s"] * (1 + pol.wall_growth) + pol.wall_slack_s
    ):
        out.append(f"wall-clock rose {bs['wall_s']}s -> {cs['wall_s']}s")
    if baseline.get("prompts_version") != current.get("prompts_version"):
        out.append(
            f"note: prompts_version changed {baseline.get('prompts_version')} -> "
            f"{current.get('prompts_version')} (re-record the baseline if intended)"
        )
    return [m for m in out if not m.startswith("note:")] + [m for m in out if m.startswith("note:")]


def blocking(messages: list[str]) -> list[str]:
    return [m for m in messages if not m.startswith("note:")]
