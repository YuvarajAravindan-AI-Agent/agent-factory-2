"""Version stamps recorded in manifests, traces and eval baselines."""

from __future__ import annotations

import hashlib
from importlib import resources

FACTORY_VERSION = "0.5.0"


def prompts_version() -> str:
    """Short hash of the role prompts: any prompt edit changes it."""
    src = resources.files("factory.nodes").joinpath("prompts.py").read_bytes()
    return hashlib.sha256(src).hexdigest()[:12]
