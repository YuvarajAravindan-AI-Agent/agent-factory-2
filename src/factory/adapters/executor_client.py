"""SandboxPort and GatePort implemented over the executor's Unix socket."""

from __future__ import annotations

import http.client
import json
import socket
from typing import Any

from factory.ports.models import CommandResult, GateResult
from factory.tracing import traced


class ExecutorError(Exception):
    pass


class _UnixConn(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float) -> None:
        super().__init__("executor", timeout=timeout)
        self._path = path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self._path)


class ExecutorClient:
    def __init__(self, sock_path: str, timeout_s: float = 700) -> None:
        self.sock_path = sock_path
        self.timeout_s = timeout_s

    def _call(self, route: str, **args: Any) -> dict[str, Any]:
        return traced(f"executor{route}", "tool")(self._raw_call)(route, **args)

    def _raw_call(self, route: str, **args: Any) -> dict[str, Any]:
        conn = _UnixConn(self.sock_path, self.timeout_s)
        try:
            conn.request("POST", route, json.dumps(args), {"Content-Type": "application/json"})
            resp = conn.getresponse()
            body: dict[str, Any] = json.loads(resp.read() or b"{}")
        finally:
            conn.close()
        if resp.status != 200:
            raise ExecutorError(f"{route}: {resp.status} {body.get('error')}")
        return body

    # SandboxPort
    def run(
        self, run_id: str, step: str, image: str, command: list[str], timeout_s: int = 60
    ) -> CommandResult:
        return CommandResult(
            **self._call(
                "/run", run_id=run_id, step=step, image=image, command=command, timeout_s=timeout_s
            )
        )

    def read_file(self, run_id: str, path: str) -> str:
        return str(self._call("/read_file", run_id=run_id, path=path)["content"])

    def write_file(self, run_id: str, path: str, content: str) -> None:
        self._call("/write_file", run_id=run_id, path=path, content=content)

    def list_dir(self, run_id: str, path: str = ".") -> list[str]:
        return list(self._call("/list_dir", run_id=run_id, path=path)["paths"])

    # GatePort
    def stage1_fetch(self, run_id: str) -> GateResult:
        return GateResult(**self._call("/gate/stage1", run_id=run_id))

    def stage2_test(self, run_id: str, baseline_test_count: int) -> GateResult:
        return GateResult(
            **self._call("/gate/stage2", run_id=run_id, baseline_test_count=baseline_test_count)
        )

    # Gate extras (called by the pipeline, not exposed to agents)
    def record_manifest(self, run_id: str) -> dict[str, str]:
        return dict(self._call("/gate/record_manifest", run_id=run_id)["manifest"])

    def allow_test_change(self, run_id: str, path: str) -> None:
        self._call("/gate/allow_test_change", run_id=run_id, path=path)
