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
