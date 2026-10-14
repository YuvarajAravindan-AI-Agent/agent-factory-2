"""Plain data types shared by the ports."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ModelRequest(BaseModel):
    role: str
    tier: str  # "strong" | "fast"
    prompt: str
    temperature: float = 0.0
    schema_name: str | None = None  # name of the Pydantic model expected back


class ModelResponse(BaseModel):
    text: str
    model_id: str
    prompt_hash: str
    input_tokens: int = 0
    output_tokens: int = 0
    parsed: dict[str, object] | None = None


class CommandResult(BaseModel):
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False


class GateResult(BaseModel):
    stage: int
    passed: bool
    tests_total: int = 0
    tests_failed: int = 0
    tests_skipped: int = 0
    junit_xml: str = ""
    log: str = ""
    tamper_findings: list[str] = Field(default_factory=list)


class ApprovalDecision(BaseModel):
    run_id: str
    decision: str  # "approved" | "rejected" | "expired" | "pending"
    approver: str | None = None
    decided_at: str | None = None
