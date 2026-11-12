"""CLI entry point for a detached sprint process (no Redis required in local dev)."""

from __future__ import annotations

import sys

from api.jobs import run_sprint_sync


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    if len(argv) != 1:
        print("usage: python -m api.worker_cli <sprint_id>", file=sys.stderr)
        return 2
    result = run_sprint_sync(argv[0])
    print(f"sprint {argv[0]} finished with status {result.get('status')}")
    return 0 if result.get("status") in {"completed", "killed", "budget_exhausted"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
