"""Typed graph state. Loop counters live here so a restart resumes with the same counts."""

from __future__ import annotations

from typing import TypedDict

from pydantic import BaseModel

MAX_TEST_FIX_ROUNDS = 3
MAX_REVIEW_FIX_ROUNDS = 1
MAX_REVIEW_PARSE_ATTEMPTS = 2

# Terminal stop states (SPEC section 6) plus GuardrailBlocked for a failed screen.
STOP_STATES = frozenset(
    {
        "TestsFailed",
        "GateFailed",
        "ReviewBlocked",
        "BudgetExceeded",
        "Rejected",
        "Expired",
        "GuardrailBlocked",
    }
)


class ReviewFindings(BaseModel):
    """Machine-read reviewer result (review.json)."""

    critical: int
    high: int
    summary: str = ""

    @property
    def blockers(self) -> int:
        return self.critical + self.high


class FactoryState(TypedDict, total=False):
    run_id: str
    spec: str
    plan: str
    status: str  # "running" | "Accepted" | one of STOP_STATES
    tests_green: bool
    baseline_tests: int
    test_fix_rounds: int
    review_fix_rounds: int
    blockers: int
    review_file: str
    tokens_used: int
    token_budget: int
    package_sha256: str
