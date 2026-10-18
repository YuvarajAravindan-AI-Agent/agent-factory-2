"""Unix-socket JSON daemon in front of ExecutorCore and Gate."""

from __future__ import annotations

import json
import os
import socket
import socketserver
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from factory.gate.runner import Gate

from .core import ExecutorCore, PolicyError
from .policy import Policy


class _UnixHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    address_family = socket.AF_UNIX
    daemon_threads = True

    def server_bind(self) -> None:
        self.socket.bind(self.server_address)
        self.server_name = "executor"
        self.server_port = 0


def _do(fn: Any, *args: Any) -> dict[str, object]:
    fn(*args)
    return {}


def make_handler(core: ExecutorCore, gate: Gate) -> type[BaseHTTPRequestHandler]:
    routes: dict[str, Any] = {
        "/run": lambda a: core.run(
            a["run_id"], a["step"], a["image"], a["command"], int(a.get("timeout_s", 60))
        ).model_dump(),
        "/read_file": lambda a: {"content": core.read_file(a["run_id"], a["path"])},
        "/write_file": lambda a: _do(core.write_file, a["run_id"], a["path"], a["content"]),
        "/list_dir": lambda a: {"paths": core.list_dir(a["run_id"], a.get("path", "."))},
        "/gate/stage1": lambda a: gate.stage1_fetch(a["run_id"]).model_dump(),
        "/gate/stage2": lambda a: gate.stage2_test(
            a["run_id"], int(a["baseline_test_count"])
        ).model_dump(),
        "/gate/record_manifest": lambda a: {"manifest": gate.record_manifest(a["run_id"])},
        "/gate/allow_test_change": lambda a: _do(gate.allow_test_change, a["run_id"], a["path"]),
    }

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            try:
                n = int(self.headers.get("Content-Length", 0))
                args = json.loads(self.rfile.read(n) or b"{}")
                fn = routes.get(self.path)
                if fn is None:
                    return self._send(404, {"error": "unknown route"})
                self._send(200, fn(args))
            except PolicyError as e:
                self._send(403, {"error": str(e)})
            except Exception as e:  # noqa: BLE001
                self._send(500, {"error": f"{type(e).__name__}: {e}"})

        def _send(self, code: int, body: dict[str, object]) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def address_string(self) -> str:
            return "unix"

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

    return Handler


def serve(policy_path: str, sock_path: str) -> None:
    policy = Policy.load(policy_path)
    core = ExecutorCore(policy)
    if os.path.exists(sock_path):
        os.unlink(sock_path)
    srv = _UnixHTTPServer(
        sock_path,  # type: ignore[arg-type]
        make_handler(core, Gate(core)),
    )
    os.chmod(sock_path, 0o660)
    srv.serve_forever()


if __name__ == "__main__":
    serve(sys.argv[1], sys.argv[2])
