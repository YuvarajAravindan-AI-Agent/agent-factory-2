"""Command line entry point: factory run | status | approve (implemented from phase 1)."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] not in {"run", "status", "approve"}:
        print("usage: factory run|status|approve ...")
        return 2
    print(f"'{args[0]}' is not implemented yet (phase 0 skeleton)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
