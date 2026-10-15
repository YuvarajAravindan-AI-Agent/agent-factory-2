"""Wiring from environment to ports, and the run/resume entry points used by the CLI and
the control service. Secrets are read from the environment and never printed."""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path
from typing import Any

from langgraph.types import Command

from factory.adapters.checkpoint import postgres_saver
from factory.adapters.executor_client import ExecutorClient
from factory.adapters.gemini import GeminiModel, default_chat_factory
from factory.config import load_model_config
from factory.control.approval import PgApproval, SmtpMailer
from factory.evidence import Evidence, FsEvidence, PgAudit
from factory.graph import build_graph
from factory.nodes.pipeline import Ports
from factory.ports import ModelPort
from factory.ports.models import ModelRequest, ModelResponse


def need(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise SystemExit(f"missing environment variable {name}")
    return v


def build_approval() -> PgApproval:
    mailer = SmtpMailer(
        need("AF_SMTP_HOST"),
        int(os.environ.get("AF_SMTP_PORT", "587")),
        need("AF_SMTP_USER"),
        need("AF_SMTP_PASSWORD"),
        os.environ.get("AF_SMTP_FROM", need("AF_SMTP_USER")),
    )
    return PgApproval(
        need("AF_PG_DSN"),
        need("AF_APPROVAL_SECRET").encode(),
        need("AF_PUBLIC_URL"),
        need("AF_APPROVER_EMAIL"),
        mailer,
        ttl=timedelta(minutes=int(os.environ.get("AF_APPROVAL_TTL_MIN", "1440"))),
    )


class NoModel:
    def invoke(self, request: ModelRequest) -> ModelResponse:
        raise RuntimeError("no model configured in this process (AF_GEMINI_KEY unset)")


def build_model() -> ModelPort:
    cfg = load_model_config(os.environ.get("AF_MODELS", "models.yaml"))
    key = os.environ.get("AF_GEMINI_KEY")
    if not key:  # the control service only resumes paused runs and never calls a model
        return NoModel()
    return GeminiModel(cfg, default_chat_factory(key, os.environ.get("AF_GEMINI_BASE_URL")))


def build_ports(approval: PgApproval) -> Ports:
    dsn = need("AF_PG_DSN")
    client = ExecutorClient(os.environ.get("AF_EXECUTOR_SOCK", "/run/factory/executor.sock"))
    evidence = Evidence(
        FsEvidence(Path(os.environ.get("AF_EVIDENCE_DIR", "/var/lib/factory/evidence"))),
        PgAudit(dsn),
    )
    return Ports(build_model(), client, client, approval, evidence)


def run_spec(ports: Ports, run_id: str, spec: str, token_budget: int = 400_000) -> str:
    """Run until the graph pauses at approval (or stops). Returns the status."""
    with postgres_saver(need("AF_PG_DSN"), setup=False) as saver:
        g = build_graph(ports, saver)
        cfg: Any = {"configurable": {"thread_id": run_id}}
        g.invoke(
            {"run_id": run_id, "spec": spec, "status": "running", "token_budget": token_budget}, cfg
        )
        return _status(g, cfg)


def resume_run(ports: Ports, run_id: str, decision: str) -> str:
    with postgres_saver(need("AF_PG_DSN"), setup=False) as saver:
        g = build_graph(ports, saver)
        cfg: Any = {"configurable": {"thread_id": run_id}}
        if not g.get_state(cfg).next:  # already finished or never paused: nothing to resume
            return _status(g, cfg)
        g.invoke(Command(resume=decision), cfg)
        return _status(g, cfg)


def run_status(ports: Ports, run_id: str) -> str:
    with postgres_saver(need("AF_PG_DSN"), setup=False) as saver:
        g = build_graph(ports, saver)
        return _status(g, {"configurable": {"thread_id": run_id}})


def _status(g: Any, cfg: Any) -> str:
    st = g.get_state(cfg)
    s = str(st.values.get("status", "unknown"))
    return f"{s} (waiting at {','.join(st.next)})" if st.next else s
