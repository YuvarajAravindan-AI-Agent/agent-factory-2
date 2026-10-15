"""Pure gate checks: JUnit parsing, anti-tamper, offline secret scan."""

from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

SKIP_PATTERNS = re.compile(
    r"pytest\.skip|pytest\.mark\.skip|pytest\.xfail|pytest\.mark\.xfail|pytest\.importorskip"
    r"|unittest\.skip|@Disabled|@Ignore|\bxit\(|\bxdescribe\("
)
SECRET_PATTERNS = {
    "aws-access-key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "google-api-key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "github-token": re.compile(r"\bgh[pousr]_[0-9A-Za-z]{36,}\b"),
    "generic-secret": re.compile(
        r"(?i)\b(?:api[_-]?key|secret|passwd|password|token)\b\s*[:=]\s*['\"][^'\"\s]{16,}['\"]"
    ),
}
TEST_GLOBS = (
    "tests/**/*",
    "test_*.py",
    "**/test_*.py",
    "**/*_test.py",
    "conftest.py",
    "**/conftest.py",
)


def parse_junit(xml: str) -> tuple[int, int, int]:
    """Return (total, failed, skipped); errors count as failures."""
    if not xml.strip():
        return 0, 0, 0
    root = ET.fromstring(xml)  # noqa: S314 - XML produced by our own pytest run
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    total = sum(int(s.get("tests", 0)) for s in suites)
    failed = sum(int(s.get("failures", 0)) + int(s.get("errors", 0)) for s in suites)
    skipped = sum(int(s.get("skipped", 0)) for s in suites)
    return total, failed, skipped


def stdout_summary_counts(stdout: str) -> dict[str, int]:
    """Parse pytest's final line, e.g. '3 passed, 1 failed in 0.1s'."""
    counts: dict[str, int] = {}
    for n, word in re.findall(
        r"(\d+) (passed|failed|skipped|error|errors|xfailed|xpassed)", stdout
    ):
        counts[word] = counts.get(word, 0) + int(n)
    return counts


def test_files(ws: Path) -> list[Path]:
    seen: dict[Path, None] = {}
    for g in TEST_GLOBS:
        for p in ws.glob(g):
            if p.is_file() and ".deps" not in p.parts and "__pycache__" not in p.parts:
                seen[p] = None
    return sorted(seen)


def build_manifest(ws: Path) -> dict[str, str]:
    return {
        str(p.relative_to(ws)): hashlib.sha256(p.read_bytes()).hexdigest() for p in test_files(ws)
    }


def save_manifest(meta: Path, manifest: dict[str, str]) -> None:
    (meta / "tests.manifest.json").write_text(json.dumps(manifest, sort_keys=True))


def load_manifest(meta: Path) -> dict[str, str] | None:
    f = meta / "tests.manifest.json"
    return json.loads(f.read_text()) if f.exists() else None


def tamper_findings(
    ws: Path,
    meta: Path,
    baseline_count: int,
    total: int,
    skipped: int,
    fixer_allowed: set[str],
) -> list[str]:
    out: list[str] = []
    if total < baseline_count:
        out.append(f"test count dropped: {total} < baseline {baseline_count}")
    if total == 0:
        out.append("no tests were collected")
    if skipped:
        out.append(f"{skipped} test(s) skipped")
    for p in test_files(ws):
        if p.suffix not in (".py", ".java", ".js", ".ts"):
            continue
        for i, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
            if SKIP_PATTERNS.search(line):
                out.append(f"skip/disable marker: {p.relative_to(ws)}:{i}")
    base = load_manifest(meta)
    if base is None:
        out.append("no test manifest recorded for this run (Tester step missing)")
    else:
        now = build_manifest(ws)
        for rel in sorted(set(base) | set(now)):
            if base.get(rel) != now.get(rel) and rel not in fixer_allowed:
                kind = "deleted" if rel not in now else "added" if rel not in base else "modified"
                out.append(f"test file {kind} after Tester step without Fixer record: {rel}")
    return out


def scan_secrets(ws: Path) -> list[str]:
    out: list[str] = []
    for p in sorted(ws.rglob("*")):
        if (
            not p.is_file()
            or ".deps" in p.parts
            or ".git" in p.parts
            or p.stat().st_size > 1_000_000
        ):
            continue
        try:
            text = p.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        for name, pat in SECRET_PATTERNS.items():
            if pat.search(text):
                out.append(f"secret:{name}: {p.relative_to(ws)}")
    return out
