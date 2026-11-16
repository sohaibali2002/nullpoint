"""Hardware-aware execution helpers.

The research stack runs on everything from a single-core CI container to a multi-core
workstation.  Hard-coding a worker count is wrong in both directions: one worker leaves
large machines idle, while ``cpu_count`` workers on a small container cause oversubscription
and can make NumPy/pandas workloads slower.

This module reads the CPU budget from the process environment or cgroup limits, caps the
number of workers, and provides a small ordered parallel map.  Ordered results matter:
trial ordering feeds the audit ledger and the parameter-stability report.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TypeVar

T = TypeVar("T")
R = TypeVar("R")

MAX_AUTO_WORKERS = 8


def _cgroup_cpu_limit() -> int | None:
    """Return the CPU quota visible to this container, if one is set."""
    try:
        cpu_max = Path("/sys/fs/cgroup/cpu.max").read_text(encoding="utf-8").split()
        if len(cpu_max) == 2 and cpu_max[0] != "max":
            quota, period = float(cpu_max[0]), float(cpu_max[1])
            if quota > 0 and period > 0:
                return max(1, int(quota // period))
    except (OSError, ValueError):
        pass
    try:
        quota = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").read_text(encoding="utf-8"))
        period = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read_text(encoding="utf-8"))
        if quota > 0 and period > 0:
            return max(1, quota // period)
    except (OSError, ValueError):
        pass
    return None


def worker_count(requested: int | None = None) -> int:
    """Resolve a safe worker count from an explicit value, env override, cgroup or CPU count.

    ``NULLPOINT_MAX_WORKERS`` is the deployment escape hatch.  A value of ``1`` forces
    deterministic sequential execution, which is useful for debugging and for constrained
    environments where even a small thread pool is undesirable.
    """
    # The environment override is intentionally checked before the settings value so an
    # operator can throttle a busy host without editing .env or redeploying.
    raw = os.environ.get("NULLPOINT_MAX_WORKERS", "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    if requested is not None and requested > 0:
        return max(1, int(requested))
    cpus = os.cpu_count() or 1
    cgroup = _cgroup_cpu_limit()
    if cgroup is not None:
        cpus = min(cpus, cgroup)
    return max(1, min(int(cpus), MAX_AUTO_WORKERS))


def parallel_map(
    func: Callable[[T], R],
    items: Iterable[T],
    *,
    max_workers: int | None = None,
) -> list[R]:
    """Map in input order using a bounded thread pool.

    Threads are deliberately used instead of processes: the hot path is pandas/NumPy
    (which releases the GIL for large operations), the sandbox adapter already spawns a
    subprocess for untrusted code, and processes would require pickling every panel and
    every strategy spec for each trial.  On a one-core machine this falls back to a plain
    loop without creating a pool.
    """
    materialised = list(items)
    workers = min(worker_count(max_workers), max(1, len(materialised)))
    if workers <= 1 or len(materialised) <= 1:
        return [func(item) for item in materialised]
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="nullpoint") as pool:
        return list(pool.map(func, materialised))
