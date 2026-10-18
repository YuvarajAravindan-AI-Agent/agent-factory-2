"""Run the control service: python -m factory.control.serve  (binds 127.0.0.1; Caddy fronts it)."""

from __future__ import annotations

import logging
import os

import uvicorn

from factory import runtime as rt
from factory.control.app import create_app

log = logging.getLogger("factory.control")


def main() -> None:
    approval = rt.build_approval()
    ports = rt.build_ports(approval)

    def resume(run_id: str, decision: str) -> None:
        try:
            log.info(
                "resume %s -> %s: %s", run_id, decision, rt.resume_run(ports, run_id, decision)
            )
        except Exception:  # decision is already recorded; the CLI can resume it
            log.exception("resume failed for %s", run_id)

    uvicorn.run(
        create_app(approval, resume),
        host="127.0.0.1",
        port=int(os.environ.get("AF_CONTROL_PORT", "8088")),
        log_level="info",
        access_log=False,  # the approval token travels in the query string
    )


if __name__ == "__main__":
    main()
