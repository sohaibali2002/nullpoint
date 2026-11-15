"""Prometheus metrics for durable research jobs.

The HTTP metrics live beside the router; these are process-wide and are imported by both the API
and the worker.  Labels are deliberately low-cardinality: no sprint ids or user ids.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

SPRINT_JOBS = Counter(
    "nullpoint_sprint_jobs_total",
    "Sprint job attempts by terminal status",
    ["status"],
)
SPRINT_ATTEMPTS = Counter(
    "nullpoint_sprint_attempts_total",
    "Worker attempt starts",
)
SPRINT_DURATION = Histogram(
    "nullpoint_sprint_duration_seconds",
    "Wall-clock duration of a sprint attempt",
    buckets=(1, 5, 15, 30, 60, 300, 900, 1800, 3600),
)
SPRINT_REQUEUES = Counter(
    "nullpoint_sprint_requeues_total",
    "Sprints requeued after a stale heartbeat",
)
STALE_SPRINTS = Gauge(
    "nullpoint_stale_sprints",
    "Sprints currently considered stale by the reaper",
)
