"""The whole graph through the REAL executor daemon, gVisor sandbox and two-stage gate.
Only the model is scripted (no Gemini key); everything else is real, including Postgres
evidence and the audit chain. Run on the VPS as factory-exec with the executor service up."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from factory.adapters.executor_client import ExecutorClient
from factory.adapters.fakes import FakeApproval, FakeModel
from factory.evidence import Evidence, FsEvidence, PgAudit
from factory.graph import build_graph
from factory.nodes.pipeline import Ports

SOCK = os.environ.get("AF_EXECUTOR_SOCK", "")
DSN = os.environ.get("AF_PG_DSN", "")
pytestmark = pytest.mark.skipif(not (SOCK and DSN), reason="needs executor socket + Postgres")

CLEAN = '{"critical":0,"high":0,"summary":"ok"}'
CODE = "def add(a, b):\n    return a + b\n"
BAD_CODE = "def add(a, b):\n    return a - b\n"
TESTS = (
    "from src.app import add\n\n\n"
    "def test_add():\n    assert add(1, 2) == 3\n\n\n"
    "def test_add_negative():\n    assert add(-1, -2) == -3\n"
)


def run_graph(tmp_path: Path, run_id: str, scripts: dict[str, list[str]]):  # type: ignore[no-untyped-def]
    client = ExecutorClient(SOCK)
    evidence = Evidence(FsEvidence(tmp_path / "ev"), PgAudit(DSN))
    ports = Ports(FakeModel(scripts), client, client, FakeApproval(), evidence)
    g = build_graph(ports, InMemorySaver())
    cfg = {"configurable": {"thread_id": run_id}}
    g.invoke({"run_id": run_id, "spec": "build an add() function", "status": "running"}, cfg)
    return g, cfg, client, evidence


def test_real_pipeline_reaches_approval_then_accepts(tmp_path: Path) -> None:
    rid = f"pipe-ok-{os.getpid()}"
    g, cfg, client, evidence = run_graph(
        tmp_path, rid, {"coder": [CODE], "tester": [TESTS], "reviewer": [CLEAN]}
    )
    st = g.get_state(cfg)
    assert st.next == ("approval",), st.values
    assert st.values["baseline_tests"] == 2  # parsed from the real pytest run in gVisor
    # The gate really ran: JUnit from stage 2 is in the evidence and shows both tests.
    junit = evidence.read(rid, "gate/stage2/junit.xml")
    assert 'tests="2"' in junit and 'failures="0"' in junit
    assert "wheels=" in evidence.read(rid, "gate/stage1.log")
    # Package holds the real workspace, with no bytecode or cache files.
    files = client.list_dir(rid)
    assert "src/app.py" in files and "tests/test_app.py" in files
    assert not any("__pycache__" in f or ".pyc" in f for f in files)
    g.invoke(Command(resume="approved"), cfg)
    assert g.get_state(cfg).values["status"] == "Accepted"
    assert evidence.fs.verify_sums(rid) and evidence.verify_chain()
    m = json.loads(evidence.read(rid, "manifest.json"))
    assert m["baseline_tests"] == 2 and m["package_sha256"]


def test_real_pipeline_stops_after_three_failed_fix_rounds(tmp_path: Path) -> None:
    rid = f"pipe-bad-{os.getpid()}"
    g, cfg, _client, evidence = run_graph(
        tmp_path,
        rid,
        {"coder": [BAD_CODE], "tester": [TESTS], "fixer": [BAD_CODE] * 3, "reviewer": [CLEAN]},
    )
    st = g.get_state(cfg)
    assert st.values["status"] == "TestsFailed" and st.values["test_fix_rounds"] == 3
    assert st.next == ()
    assert evidence.verify_chain()


def test_real_pipeline_tester_weakening_is_caught_by_gate(tmp_path: Path) -> None:
    """A fixer that edits a recorded test file is caught by the gate's manifest check."""
    rid = f"pipe-tamper-{os.getpid()}"
    client = ExecutorClient(SOCK)
    client.write_file(rid, "src/app.py", CODE)
    client.write_file(rid, "tests/test_app.py", TESTS)
    client.record_manifest(rid)
    assert client.stage1_fetch(rid).passed
    client.write_file(rid, "tests/test_app.py", TESTS.replace("== 3", "== 3 or True"))
    r = client.stage2_test(rid, 2)
    assert not r.passed and any("modified" in f for f in r.tamper_findings)
