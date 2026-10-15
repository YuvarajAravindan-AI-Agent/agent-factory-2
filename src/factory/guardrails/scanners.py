"""Layer A: deterministic scanners (checksummed where a checksum exists). Block on any hit."""

from __future__ import annotations

import re
from dataclasses import dataclass

from factory.gate.checks import SECRET_PATTERNS


@dataclass(frozen=True)
class Finding:
    kind: str
    where: str = ""

    def __str__(self) -> str:
        return f"{self.kind}{':' + self.where if self.where else ''}"


def luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
    return total % 10 == 0


def iban_ok(raw: str) -> bool:
    s = raw.replace(" ", "").upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", s):
        return False
    rearranged = s[4:] + s[:4]
    num = "".join(str(int(c, 36)) for c in rearranged)
    return int(num) % 97 == 1


_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6], [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4], [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]  # fmt: skip
_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2], [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]  # fmt: skip


def verhoeff_ok(digits: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(digits)):
        c = _D[c][_P[i % 8][int(ch)]]
    return c == 0


def nhs_ok(digits: str) -> bool:
    total = sum(int(d) * w for d, w in zip(digits[:9], range(10, 1, -1), strict=True))
    check = 11 - total % 11
    check = 0 if check == 11 else check
    return check != 10 and check == int(digits[9])


CARD = re.compile(r"(?<![\d-])(?:\d[ -]?){13,19}(?![\d-])")
IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b")
SSN = re.compile(r"(?<!\d)(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}(?!\d)")
AADHAAR = re.compile(r"(?<!\d)[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}(?!\d)")
PAN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
NHS = re.compile(r"(?<!\d)\d{3}[ -]?\d{3}[ -]?\d{4}(?!\d)")


def scan_text(text: str, where: str = "") -> list[Finding]:
    out: list[Finding] = []

    def add(kind: str) -> None:
        out.append(Finding(kind, where))

    for m in CARD.finditer(text):
        d = re.sub(r"\D", "", m.group())
        if 13 <= len(d) <= 19 and luhn_ok(d):
            add("card-number")
    for m in IBAN.finditer(text):
        if iban_ok(m.group()):
            add("iban")
    if SSN.search(text):
        add("us-ssn")
    for m in AADHAAR.finditer(text):
        d = re.sub(r"\D", "", m.group())
        if verhoeff_ok(d):
            add("aadhaar")
    if PAN.search(text):
        add("india-pan")
    for m in NHS.finditer(text):
        d = re.sub(r"\D", "", m.group())
        if nhs_ok(d):
            add("nhs-number")
    for name, pat in SECRET_PATTERNS.items():
        if pat.search(text):
            add(f"secret-{name}")
    # Deduplicate while keeping order.
    seen: dict[tuple[str, str], Finding] = {(f.kind, f.where): f for f in out}
    return list(seen.values())


def scan_files(files: dict[str, str]) -> list[Finding]:
    found: list[Finding] = []
    for path, text in sorted(files.items()):
        found += scan_text(text, path)
    return found
