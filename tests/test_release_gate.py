"""The release gate: replay the recorded golden specs and compare with the committed baseline.
A regression here fails the build, which is what blocks a factory release."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from factory.evals.cli import main as evals_main

BASELINE = Path("evals/baseline.scripted.json")
REC = Path("evals/golden/recordings")


def run(tmp: Path, rec: Path, name: str) -> Path:
    out = tmp / name
    evals_main(["run", "--model", "replay", "--backend", "local", "--recordings", str(rec),
                "--out", str(out)])  # fmt: skip
    return out


def test_golden_specs_match_the_recorded_baseline(tmp_path: Path) -> None:
    cur = run(tmp_path, REC, "cur.json")
    evals_main(["compare", str(BASELINE), str(cur)])  # exits 1 (fails the test) on regression


def test_an_injected_bug_blocks_the_release(tmp_path: Path) -> None:
    rec = tmp_path / "rec"
    shutil.copytree(REC, rec)
    p = rec / "loan-prequal.json"
    d = json.loads(p.read_text())
    patched = 0
    for c in d["calls"]:
        if c["role"] == "coder":
            b = json.loads(c["text"])
            for f in b["files"]:
                if "Fraction(36, 100)" in f["content"]:
                    f["content"] = f["content"].replace("Fraction(36, 100)", "Fraction(30, 100)")
                    patched += 1
            c["text"] = json.dumps(b)
    assert patched == 1  # the bug really was injected
    p.write_text(json.dumps(d))
    cur = run(tmp_path, rec, "bad.json")
    with pytest.raises(SystemExit) as e:
        evals_main(["compare", str(BASELINE), str(cur)])
    assert e.value.code == 1
