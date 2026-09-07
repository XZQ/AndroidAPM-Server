"""Low-cardinality Prometheus metrics for Gateway and worker health."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

INGEST_REQUESTS = Counter(
    "androidapm_ingest_requests_total",
    "Ingest HTTP requests by bounded result and protocol.",
    ("result", "protocol"),
)
INGEST_EVENTS = Counter(
    "androidapm_ingest_events_total",
    "Events observed at the durable inbox boundary.",
    ("result",),
)
INGEST_ACK_SECONDS = Histogram(
    "androidapm_ingest_ack_seconds",
    "Time from route entry to durable whole-batch acknowledgement.",
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)
INBOX_PENDING = Gauge(
    "androidapm_inbox_pending",
    "Current pending/expired-processing events visible to a worker.",
)
INBOX_OLDEST_SECONDS = Gauge(
    "androidapm_inbox_oldest_seconds",
    "Age of the oldest exportable inbox event.",
)
INBOX_SNAPSHOT_AVAILABLE = Gauge(
    "androidapm_inbox_snapshot_available", "Whether the latest DB snapshot succeeded."
)
INBOX_SNAPSHOT_TIMESTAMP = Gauge(
    "androidapm_inbox_snapshot_timestamp_seconds", "Last successful database observation time."
)
INBOX_ROWS = Gauge(
    "androidapm_inbox_rows", "Database inbox rows by bounded durable status.", ("status",)
)
PROCESS_ROLE = Gauge("androidapm_process_role", "Role served by this scrape target.", ("role",))
WORKER_CYCLE_TIMESTAMP = Gauge(
    "androidapm_worker_cycle_timestamp_seconds", "Last completed worker cycle.", ("role",)
)
INBOX_PENDING.set(float("nan"))
INBOX_OLDEST_SECONDS.set(float("nan"))
EXPORT_EVENTS = Counter(
    "androidapm_export_events_total",
    "Inbox export outcomes.",
    ("result",),
)
EXPORT_REQUEST_SECONDS = Histogram(
    "androidapm_export_request_seconds",
    "OTLP export request duration.",
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
SYMBOLIZATION_JOBS = Counter(
    "androidapm_symbolization_jobs_total",
    "Durable crash symbolization outcomes.",
    ("result", "job_type"),
)
SYMBOLIZATION_SECONDS = Histogram(
    "androidapm_symbolization_seconds",
    "External retrace or llvm-symbolizer execution duration.",
    ("job_type",),
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)
