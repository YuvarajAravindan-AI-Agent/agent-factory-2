"""factory run <spec> | status <runId> | approve <runId> [--reject] | verify-audit."""

from __future__ import annotations

import argparse
import secrets
import sys
from datetime import UTC, datetime
from pathlib import Path

from factory.evidence import PgAudit


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="factory")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("spec")
    r.add_argument("--run-id")
    s = sub.add_parser("status")
    s.add_argument("run_id")
    a = sub.add_parser("approve")
    a.add_argument("run_id")
    a.add_argument("--reject", action="store_true")
    sub.add_parser("verify-audit")
    sub.add_parser("expire", help="resume runs whose approval lapsed")
    args = ap.parse_args(argv)

    from factory import runtime as rt  # imported late: needs the environment

    approval = rt.build_approval()
    if args.cmd == "verify-audit":
        ok = PgAudit(rt.need("AF_PG_DSN")).verify_chain()
        print("audit chain OK" if ok else "audit chain BROKEN")
        sys.exit(0 if ok else 2)
    ports = rt.build_ports(approval)
    if args.cmd == "expire":
        for rid in approval.lapsed_pending():
            print(rid, rt.resume_run(ports, rid, "expired"))
        return
    if args.cmd == "run":
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        run_id = args.run_id or f"{Path(args.spec).stem}-{stamp}-{secrets.token_hex(2)}"
        print(run_id)
        print(rt.run_spec(ports, run_id, Path(args.spec).read_text()))
    elif args.cmd == "status":
        print(rt.run_status(ports, args.run_id), approval.status(args.run_id).decision)
    elif args.cmd == "approve":
        decision = "rejected" if args.reject else "approved"
        st = approval.decide(args.run_id, decision, "cli:" + rt.need("AF_APPROVER_EMAIL"))
        if st.decision != decision:
            raise SystemExit(f"not recorded: request is {st.decision}")
        print(rt.resume_run(ports, args.run_id, decision))


if __name__ == "__main__":
    main()
