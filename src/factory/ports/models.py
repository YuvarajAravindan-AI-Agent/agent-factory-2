"""Plain data types shared by the ports."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class Tier(StrEnum):
    STRONG = "strong"
    FAST = "fast"


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0


class ModelRequest(BaseModel):
    role: str
    tier: Tier
    prompt: str
    schema_name: str | None = None
    temperature: float = Field(default=0.0, ge=0.0, le=0.2)


class ModelResponse(BaseModel):
    model_id: str
    text: str
    parsed: dict[str, object] | None = None
    usage: Usage = Field(default_factory=Usage)


class CommandLimits(BaseModel):
    timeout_s: int = 60
    memory_mb: int = 512
    cpus: float = 1.0
    pids: int = 128


class CommandResult(BaseModel):
    exit_code: int
    stdout: str = ""
    stderr: str = ""


class GateResult(BaseModel):
    stage1_ok: bool
    stage2_ok: bool
    tests_total: int = 0
    tests_failed: int = 0
    tests_skipped: int = 0
    junit_xml: str = ""
    tamper_findings: list[str] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return (
            self.stage1_ok
            and self.stage2_ok
            and self.tests_failed == 0
            and not self.tamper_findings
        )


class ApprovalDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ApprovalRecord(BaseModel):
    run_id: str
    decision: ApprovalDecision
    approver: str
    decided_at: str
