"""Structured code output: a bundle of files with safe relative paths."""

from __future__ import annotations

import json
import re

from pydantic import BaseModel, ValidationError

FENCE = re.compile(r"^```[a-zA-Z0-9_+-]*\n(.*?)\n```\s*$", re.S)
BAD_PARTS = {"..", ".git", ".af", ".deps", "__pycache__"}


class CodeFile(BaseModel):
    path: str
    content: str


class CodeBundle(BaseModel):
    files: list[CodeFile]
    notes: str = ""


def strip_fences(text: str) -> str:
    m = FENCE.match(text.strip())
    return (m.group(1) + "\n") if m else text


def parse_bundle(text: str) -> CodeBundle | None:
    try:
        return CodeBundle.model_validate(json.loads(text))
    except (json.JSONDecodeError, ValidationError):
        return None


def safe_path(path: str) -> str | None:
    p = path.strip().replace("\\", "/")
    if not p or p.startswith("/") or "\x00" in p:
        return None
    parts = p.split("/")
    if BAD_PARTS & set(parts) or len(p) > 200:
        return None
    return p


def is_test_path(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return path.startswith("tests/") or name.startswith("test_") or name == "conftest.py"
