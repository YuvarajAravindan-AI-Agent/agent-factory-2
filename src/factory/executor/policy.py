"""Executor policy, loaded from policy.yaml."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

RUN_ID_RE = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"


@dataclass(frozen=True)
class Policy:
    allowed_images: tuple[str, ...] = ()  # see policy.yaml
    cpu: float = 2
    memory_mb: int = 2048
    pids: int = 256
    command_timeout_s: int = 300
    runtime: str = "runsc"
    runs_root: Path = Path("/var/lib/factory/runs")
    fetch_network: str = "af-fetch"
    proxy_url: str = "http://172.30.77.2:3128"
    gate_image: str = "python:3.12-slim"
    gate_tools: tuple[str, ...] = ("pytest",)
    max_output_bytes: int = 65536
    max_file_bytes: int = 2_000_000
    extra: dict[str, object] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> Policy:
        raw = yaml.safe_load(Path(path).read_text()) or {}
        lim = raw.get("limits", {})
        ex = raw.get("executor", {})
        return cls(
            allowed_images=tuple(raw.get("allowed_images", [])),
            cpu=float(lim.get("cpu", 2)),
            memory_mb=int(lim.get("memory_mb", 2048)),
            pids=int(lim.get("pids", 256)),
            command_timeout_s=int(lim.get("command_timeout_s", 300)),
            runtime=str(ex.get("runtime", "runsc")),
            runs_root=Path(ex.get("runs_root", "/var/lib/factory/runs")),
            fetch_network=str(ex.get("fetch_network", "af-fetch")),
            proxy_url=str(ex.get("proxy_url", "http://172.30.77.2:3128")),
            gate_image=str(ex.get("gate_image", "python:3.12-slim")),
            gate_tools=tuple(ex.get("gate_tools", ["pytest"])),
        )
