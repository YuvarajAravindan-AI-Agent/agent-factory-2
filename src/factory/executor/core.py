"""Executor core: policy checks, workspace scoping, container launch, audit."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from factory.ports.models import CommandResult

from .policy import RUN_ID_RE, Policy

Audit = Callable[[str, str, dict[str, object]], None]


class PolicyError(Exception):
    pass


class JsonlAudit:
    """Append-only hash-chained audit log (the executor's own record)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def __call__(self, run_id: str, action: str, payload: dict[str, object]) -> None:
        prev = "0" * 64
        if self.path.exists():
            lines = self.path.read_text().splitlines()
            if lines:
                prev = json.loads(lines[-1])["hash"]
        row = {"ts": time.time(), "run_id": run_id, "action": action, "payload": payload}
        body = json.dumps(row, sort_keys=True)
        row["hash"] = hashlib.sha256((prev + body).encode()).hexdigest()
        row["prev"] = prev
        with self.path.open("a") as f:
            f.write(json.dumps(row, sort_keys=True) + "\n")


class ExecutorCore:
    def __init__(self, policy: Policy, audit: Audit | None = None) -> None:
        if os.getuid() == 0 and not os.environ.get("AF_ALLOW_ROOT"):
            raise PolicyError(
                "executor must run as an unprivileged user (sandboxes inherit its uid)"
            )
        self.policy = policy
        self.audit = audit or JsonlAudit(policy.runs_root / "audit.jsonl")

    # --- workspace scoping -------------------------------------------------
    def run_dir(self, run_id: str) -> Path:
        if not re.match(RUN_ID_RE, run_id):
            raise PolicyError(f"bad run id: {run_id!r}")
        d = self.policy.runs_root / run_id
        for sub in ("ws", "deps", "out", "meta"):
            (d / sub).mkdir(parents=True, exist_ok=True)
        return d

    def safe_path(self, run_id: str, rel: str) -> Path:
        ws = (self.run_dir(run_id) / "ws").resolve()
        if os.path.isabs(rel) or "\x00" in rel:
            raise PolicyError(f"path not allowed: {rel!r}")
        target = (ws / rel).resolve()
        if target != ws and ws not in target.parents:
            raise PolicyError(f"path escapes workspace: {rel!r}")
        return target

    def read_file(self, run_id: str, path: str) -> str:
        p = self.safe_path(run_id, path)
        if not p.is_file():
            raise PolicyError(f"not a file: {path}")
        if p.stat().st_size > self.policy.max_file_bytes:
            raise PolicyError("file too large")
        self.audit(run_id, "read_file", {"path": path})
        return p.read_text(errors="replace")

    def write_file(self, run_id: str, path: str, content: str) -> None:
        if len(content.encode()) > self.policy.max_file_bytes:
            raise PolicyError("file too large")
        p = self.safe_path(run_id, path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        self.audit(
            run_id,
            "write_file",
            {"path": path, "sha256": hashlib.sha256(content.encode()).hexdigest()},
        )

    def list_dir(self, run_id: str, path: str = ".") -> list[str]:
        base = self.safe_path(run_id, path)
        ws = (self.run_dir(run_id) / "ws").resolve()
        out = [
            str(p.relative_to(ws))
            for p in sorted(base.rglob("*"))
            if p.is_file() and not p.is_symlink()
        ]
        self.audit(run_id, "list_dir", {"path": path})
        return out

    # --- containers --------------------------------------------------------
    def docker_args(
        self,
        name: str,
        image: str,
        command: list[str],
        mounts: list[tuple[Path, str, str]],
        network: str,
        env: dict[str, str] | None = None,
        tmpfs_mb: int = 512,
    ) -> list[str]:
        p = self.policy
        args = [
            "docker", "run", "--rm", "--name", name,
            "--runtime", p.runtime,
            "--network", network,
            "--read-only",
            "--tmpfs", f"/tmp:rw,exec,nosuid,size={tmpfs_mb}m",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--cpus", str(p.cpu),
            "--memory", f"{p.memory_mb}m",
            "--memory-swap", f"{p.memory_mb}m",
            "--pids-limit", str(p.pids),
            "--user", f"{os.getuid()}:{os.getgid()}",
            "--workdir", "/work",
            "-e", "HOME=/tmp",
        ]  # fmt: skip
        for k, v in (env or {}).items():
            args += ["-e", f"{k}={v}"]
        for src, dst, mode in mounts:
            args += ["-v", f"{src}:{dst}:{mode}"]
        return [*args, image, *command]

    def exec_container(
        self,
        run_id: str,
        step: str,
        image: str,
        command: list[str],
        mounts: list[tuple[Path, str, str]],
        network: str,
        timeout_s: int,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        if image not in self.policy.allowed_images:
            self.audit(run_id, "denied_image", {"image": image})
            raise PolicyError(f"image not allowed: {image}")
        timeout_s = max(1, min(timeout_s, self.policy.command_timeout_s))
        name = f"af-{run_id[:24]}-{re.sub(r'[^A-Za-z0-9]', '', step)[:12]}-{secrets.token_hex(3)}"
        argv = self.docker_args(name, image, command, mounts, network, env)
        self.audit(
            run_id, "run", {"step": step, "image": image, "command": command, "net": network}
        )
        try:
            cp = subprocess.run(  # noqa: S603
                argv, capture_output=True, timeout=timeout_s, check=False
            )
            res = CommandResult(
                exit_code=cp.returncode,
                stdout=self._cap(cp.stdout),
                stderr=self._cap(cp.stderr),
            )
        except subprocess.TimeoutExpired as e:
            subprocess.run(["docker", "kill", name], capture_output=True, check=False)  # noqa: S607
            res = CommandResult(
                exit_code=124,
                stdout=self._cap(e.stdout or b""),
                stderr=self._cap(e.stderr or b""),
                timed_out=True,
            )
        self.audit(
            run_id, "run_done", {"step": step, "exit": res.exit_code, "timeout": res.timed_out}
        )
        return res

    def _cap(self, data: bytes) -> str:
        return data[: self.policy.max_output_bytes].decode(errors="replace")

    def run(
        self, run_id: str, step: str, image: str, command: list[str], timeout_s: int = 60
    ) -> CommandResult:
        """Agent command: workspace only, never any network."""
        d = self.run_dir(run_id)
        return self.exec_container(
            run_id, step, image, command, [(d / "ws", "/work", "rw")], "none", timeout_s
        )
