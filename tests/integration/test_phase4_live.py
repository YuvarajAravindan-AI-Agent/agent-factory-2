"""Phase 4 gate on a real Postgres: audit chain verifies and detects tampering; approval by
emailed link works end to end. Run: AF_PG_DSN=postgresql://... pytest tests/integration"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from factory.adapters.fakes import FakeGate, FakeModel, FakeSandbox
from factory.control import PgApproval, create_app
from factory.evidence import Evidence, FsEvidence, PgAudit, setup_schema
from factory.graph import build_graph
from factory.nodes.pipeline import Ports
from factory.ports.models import CommandResult

DSN = os.environ.get("AF_PG_DSN", "")
pytestmark = pytest.mark.skipif(not DSN, reason="needs AF_PG_DSN")
CLEAN = '{"critical":0,"high":0,"summary":"ok"}'


class Sink:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    def send(self, to: str, subject: str, body: str) -> None:
        self.sent.append((to, subject, body))


@pytest.fixture(autouse=True)
def fresh_schema() -> None:
    setup_schema(DSN)
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("ALTER TABLE audit_log DISABLE TRIGGER USER")
        c.execute("TRUNCATE audit_log RESTART IDENTITY")
        c.execute("ALTER TABLE audit_log ENABLE TRIGGER USER")
        c.execute("TRUNCATE approvals")


def test_chain_verifies_and_is_append_only() -> None:
    a = PgAudit(DSN)
    for i in range(5):
        a.audit("r1", "factory", "step", f"payload {i}")
    assert a.verify_chain() and len(a.rows()) == 5
    for stmt in ("UPDATE audit_log SET actor='x'", "DELETE FROM audit_log", "TRUNCATE audit_log"):
        with pytest.raises(psycopg.Error, match="append-only"), psycopg.connect(DSN) as c:
            c.execute(stmt)  # type: ignore[call-overload]
    assert a.verify_chain() and len(a.rows()) == 5


def test_chain_detects_edit_delete_and_reorder() -> None:
    a = PgAudit(DSN)
    for i in range(6):
        a.audit("r1", "factory", "step", f"payload {i}")

    def as_owner(sql: str) -> None:  # a privileged attacker who disables the trigger
        with psycopg.connect(DSN, autocommit=True) as c:
            c.execute("ALTER TABLE audit_log DISABLE TRIGGER USER")
            c.execute(sql)  # type: ignore[call-overload]
            c.execute("ALTER TABLE audit_log ENABLE TRIGGER USER")

    as_owner("UPDATE audit_log SET payload_hash = repeat('a',64) WHERE seq = 3")
    assert a.first_break() == 3
    as_owner(
        "UPDATE audit_log SET payload_hash = (SELECT payload_hash FROM audit_log WHERE seq=2)"
        " WHERE seq=3"
    )  # undo is impossible without the original: still broken
    assert not a.verify_chain()


def test_chain_detects_deleted_row() -> None:
    a = PgAudit(DSN)
    for i in range(4):
        a.audit("r1", "f", "s", str(i))
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("ALTER TABLE audit_log DISABLE TRIGGER USER")
        c.execute("DELETE FROM audit_log WHERE seq = 2")
        c.execute("ALTER TABLE audit_log ENABLE TRIGGER USER")
    assert a.first_break() == 3


def test_concurrent_writers_keep_one_chain() -> None:
    from concurrent.futures import ThreadPoolExecutor

    a = PgAudit(DSN)
    with ThreadPoolExecutor(8) as ex:
        list(ex.map(lambda i: a.audit(f"r{i % 3}", "f", "s", str(i)), range(40)))
    assert len(a.rows()) == 40 and a.verify_chain()


def make_approval(clock=None, ttl=timedelta(hours=24)):  # type: ignore[no-untyped-def]
    sink = Sink()
    ap = PgApproval(DSN, b"k" * 32, "https://factory.example.test", "approver@example.test", sink,
                    ttl=ttl, clock=clock)  # fmt: skip
    return ap, sink


def link_parts(body: str) -> tuple[str, str]:
    m = re.search(r"/approve/([A-Za-z0-9_-]+)\?t=([0-9a-f]{64})", body)
    assert m
    return m.group(1), m.group(2)


def test_approval_by_email_link(tmp_path: Path) -> None:
    ap, sink = make_approval()
    resumed: list[tuple[str, str]] = []
    client = TestClient(create_app(ap, lambda r, d: resumed.append((r, d))))
    ap.request("run1", "ab" * 32)
    assert len(sink.sent) == 1 and sink.sent[0][0] == "approver@example.test"
    run, t = link_parts(sink.sent[0][2])
    # GET shows the page and changes nothing (link scanners cannot approve).
    r = client.get(f"/approve/{run}", params={"t": t})
    assert r.status_code == 200 and "Approve" in r.text and ("ab" * 32) in r.text
    assert ap.status("run1").decision == "pending" and resumed == []
    # Bad token and unknown run look identical.
    assert client.get(f"/approve/{run}", params={"t": "0" * 64}).status_code == 404
    assert client.get("/approve/nope", params={"t": t}).status_code == 404
    assert (
        client.post(f"/approve/{run}", data={"t": "0" * 64, "decision": "approved"}).status_code
        == 404
    )
    # POST approves once.
    r = client.post(f"/approve/{run}", data={"t": t, "decision": "approved"})
    assert r.status_code == 200 and "approved" in r.text
    st = ap.status("run1")
    assert st.decision == "approved" and st.approver == "approver@example.test" and st.decided_at
    # A second click (or a reject) cannot change it or resume twice.
    client.post(f"/approve/{run}", data={"t": t, "decision": "rejected"})
    assert ap.status("run1").decision == "approved" and resumed == [("run1", "approved")]


def test_approval_expires() -> None:
    now = [datetime(2026, 10, 15, tzinfo=UTC)]
    ap, sink = make_approval(clock=lambda: now[0], ttl=timedelta(hours=1))
    resumed: list[tuple[str, str]] = []
    client = TestClient(create_app(ap, lambda r, d: resumed.append((r, d))))
    ap.request("run2", "cd" * 32)
    run, t = link_parts(sink.sent[0][2])
    now[0] += timedelta(hours=2)
    assert ap.status("run2").decision == "expired"
    client.post(f"/approve/{run}", data={"t": t, "decision": "approved"})
    assert ap.status("run2").decision == "expired" and resumed == [("run2", "expired")]


def test_token_binds_to_package() -> None:
    ap, sink = make_approval()
    ap.request("run3", "ee" * 32)
    _, t = link_parts(sink.sent[0][2])
    ap.request("run3", "ff" * 32)  # a new package invalidates the old link
    assert not ap.verify_token("run3", t)


def test_full_run_with_postgres_evidence_and_email_approval(tmp_path: Path) -> None:
    ap, sink = make_approval()
    evidence = Evidence(FsEvidence(tmp_path / "evidence"), PgAudit(DSN))
    sandbox = FakeSandbox(lambda _c: CommandResult(exit_code=0, stdout="3 passed"))
    ports = Ports(FakeModel({"reviewer": [CLEAN]}), sandbox, FakeGate(), ap, evidence)
    g = build_graph(ports, InMemorySaver())
    cfg = {"configurable": {"thread_id": "full1"}}

    def resume(run_id: str, decision: str) -> None:
        g.invoke(Command(resume=decision), cfg)

    client = TestClient(create_app(ap, resume))
    g.invoke({"run_id": "full1", "spec": "build a prior-auth API", "status": "running"}, cfg)
    assert g.get_state(cfg).next == ("approval",) and len(sink.sent) == 1
    run, t = link_parts(sink.sent[0][2])
    assert client.post(f"/approve/{run}", data={"t": t, "decision": "approved"}).status_code == 200
    assert g.get_state(cfg).values["status"] == "Accepted"
    # Evidence: manifest, package, approval record, and a verifying audit chain.
    ev = evidence.fs
    assert ev.verify_sums("full1")
    for rel in ("manifest.json", "guardrail/in.json", "guardrail/out.json", "approval.json"):
        assert ev.read("full1", rel)
    actions = [r["action"] for r in evidence._audit.rows("full1")]
    assert "package" in actions and "decision" in actions and "screen_spec" in actions
    assert evidence.verify_chain()
