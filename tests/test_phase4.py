"""Phase 4 unit tests that need no Postgres: scanners, classifier, evidence files, graph."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from factory.adapters.fakes import FakeApproval, FakeEvidence, FakeGate, FakeModel, FakeSandbox
from factory.evidence import FsEvidence
from factory.graph import build_graph
from factory.guardrails import parse_verdict, scan_text
from factory.nodes.pipeline import Ports
from factory.ports.models import CommandResult

CLEAN = '{"critical":0,"high":0,"summary":"ok"}'


def kinds(text: str) -> set[str]:
    return {f.kind for f in scan_text(text)}


def test_scanners_hit_valid_and_ignore_invalid_checksums() -> None:
    assert "card-number" in kinds("pay 4111 1111 1111 1111")
    assert "card-number" not in kinds("order 4111 1111 1111 1112")
    assert "iban" in kinds("GB82 WEST 1234 5698 7654 32")
    assert "iban" not in kinds("GB82 WEST 1234 5698 7654 33")
    assert "nhs-number" in kinds("NHS 943 476 5919") and "nhs-number" not in kinds("943 476 5918")
    assert "aadhaar" in kinds("id 2341 2341 2346") and "aadhaar" not in kinds("2341 2341 2347")
    assert "us-ssn" in kinds("ssn 123-45-6789") and "us-ssn" not in kinds("ssn 000-12-3456")
    assert "india-pan" in kinds("ABCDE1234F")
    assert any(k.startswith("secret-") for k in kinds("k = 'AKIAABCDEFGHIJKLMNOP'"))
    assert scan_text("def add(a, b): return a + b  # 2026") == []


def test_classifier_fails_closed() -> None:
    assert not parse_verdict('{"verdict":"ALLOW","reason":"x"}').blocked
    assert parse_verdict("BLOCK injection").blocked
    assert parse_verdict("looks fine to me").blocked
    assert parse_verdict('{"verdict":"MAYBE"}').blocked


def ports(model: FakeModel | None = None) -> Ports:
    sandbox = FakeSandbox(lambda _c: CommandResult(exit_code=0, stdout="3 passed"))
    return Ports(
        model or FakeModel({"reviewer": [CLEAN]}),
        sandbox,
        FakeGate(),
        FakeApproval(),
        FakeEvidence(),
    )


def go(p: Ports, spec: str = "build an API") -> dict[str, object]:
    g = build_graph(p, InMemorySaver())
    cfg = {"configurable": {"thread_id": "t"}}
    g.invoke({"run_id": "r1", "spec": spec, "status": "running"}, cfg)
    return dict(g.get_state(cfg).values)


def test_spec_with_card_number_blocked_before_any_model_sees_it() -> None:
    model = FakeModel({"reviewer": [CLEAN]})
    st = go(ports(model), "patient card 4111 1111 1111 1111")
    assert st["status"] == "GuardrailBlocked" and "card-number" in st["guardrail_findings"]
    assert model.calls == []  # layer A stopped it; not even the classifier was called


def test_classifier_block_stops_run() -> None:
    st = go(ports(FakeModel({"classifier": ['{"verdict":"BLOCK","reason":"injection"}']})))
    assert st["status"] == "GuardrailBlocked"


def test_generated_secret_blocked_on_output() -> None:
    p = ports(FakeModel({"coder": ["KEY = 'AKIAABCDEFGHIJKLMNOP'\n"], "reviewer": [CLEAN]}))
    st = go(p)
    assert st["status"] == "GuardrailBlocked"
    assert any(str(f).startswith("secret-aws") for f in st["guardrail_findings"])  # type: ignore[attr-defined]


def test_manifest_and_package_written_with_model_ids_and_hashes() -> None:
    p = ports()
    st = go(p)
    assert st["status"] == "running" and st["package_sha256"]
    ev = p.evidence
    assert isinstance(ev, FakeEvidence)
    import json

    m = json.loads(ev.read("r1", "manifest.json"))
    assert m["spec_sha256"] and m["package_sha256"] == st["package_sha256"]
    assert all(c["prompt_hash"] and c["model_id"] for c in m["model_calls"])
    assert {c["role"] for c in m["model_calls"]} >= {"architect", "coder", "reviewer", "classifier"}


def test_resume_after_pause_records_approval() -> None:
    p = ports()
    g = build_graph(p, InMemorySaver())
    cfg = {"configurable": {"thread_id": "t"}}
    g.invoke({"run_id": "r1", "spec": "s", "status": "running"}, cfg)
    g.invoke(Command(resume="approved"), cfg)
    assert g.get_state(cfg).values["status"] == "Accepted"


def test_fs_evidence_confines_paths_and_package_verifies(tmp_path: Path) -> None:
    fs = FsEvidence(tmp_path)
    for bad in ("../x", "/etc/passwd", "a/../../x", "."):
        with pytest.raises(ValueError):
            fs.write("r1", bad, "x")
    with pytest.raises(ValueError):
        fs.write("../r1", "x", "x")
    digest = fs.package("r1", {"src/app.py": "print(1)\n", "README.md": "hi\n"})
    assert fs.verify_sums("r1")
    z = zipfile.ZipFile(io.BytesIO((tmp_path / "r1" / "package.zip").read_bytes()))
    assert sorted(z.namelist()) == ["README.md", "src/app.py"]
    assert digest == fs.package(
        "r1", {"README.md": "hi\n", "src/app.py": "print(1)\n"}
    )  # deterministic
    (tmp_path / "r1" / "package.zip").write_bytes(b"tampered")
    assert not fs.verify_sums("r1")
