"""Gate demo: run the graph on fake model/sandbox/gate but REAL Postgres evidence, REAL SES
email and a REAL Postgres checkpointer, then pause at approval. The control service (a
separate process) resumes the run when you click the emailed link.

    python scripts/demo_approval.py run <run_id>      # sends the email, pauses
    python scripts/demo_approval.py status <run_id>   # shows graph + approval status
"""

from __future__ import annotations

import sys

from factory import runtime as rt
from factory.adapters.fakes import FakeGate, FakeModel, FakeSandbox
from factory.nodes.pipeline import Ports
from factory.ports.models import CommandResult

CLEAN = '{"critical":0,"high":0,"summary":"demo"}'


def demo_ports(real: rt.Ports, approval: rt.PgApproval) -> rt.Ports:
    sandbox = FakeSandbox(lambda _c: CommandResult(exit_code=0, stdout="3 passed"))
    return Ports(FakeModel({"reviewer": [CLEAN]}), sandbox, FakeGate(), approval, real.evidence)


def main() -> None:
    cmd, run_id = sys.argv[1], sys.argv[2]
    approval = rt.build_approval()
    ports = demo_ports(rt.build_ports(approval), approval)
    if cmd == "run":
        print(rt.run_spec(ports, run_id, "Demo spec: a tiny prior-authorisation intake API."))
    print(rt.run_status(ports, run_id), "| approval:", approval.status(run_id).decision)


if __name__ == "__main__":
    main()
