"""OTLP/HTTP Protobuf client with explicit retry classification."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceResponse
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
    ExportMetricsServiceResponse,
)
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceResponse

from androidapm_server.constants import RETRY_AFTER_MAX_SECONDS

OTLP_CONTENT_TYPE = "application/x-protobuf"
RETRYABLE_STATUS_CODES = frozenset({408, 429, 502, 503, 504})
MAX_OTLP_RESPONSE_BYTES = 65_536
RESPONSE_TYPES = {
    "logs": (ExportLogsServiceResponse, "rejected_log_records"),
    "metrics": (ExportMetricsServiceResponse, "rejected_data_points"),
    "traces": (ExportTraceServiceResponse, "rejected_spans"),
}


@dataclass(frozen=True, slots=True)
class ExportResult:
    """Classified OTLP outcome consumed by the durable worker."""

    success: bool
    retryable: bool
    error_code: str | None = None
    error_message: str | None = None
    retry_after_seconds: int | None = None


class OtlpLogsClient:
    """Send serialized OTLP LogService requests to SigNoz or a compatible collector."""

    signal = "logs"

    def __init__(
        self,
        endpoint: str,
        headers_json: str,
        timeout_seconds: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """Validate transport configuration and optionally accept a test client."""
        parsed_headers = json.loads(headers_json)
        if not isinstance(parsed_headers, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in parsed_headers.items()
        ):
            raise ValueError("OTLP headers JSON must be an object of string values")
        self._endpoint = endpoint
        self._headers = {"Content-Type": OTLP_CONTENT_TYPE, **parsed_headers}
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None

    async def export(self, payload: bytes) -> ExportResult:
        """Export one request and classify ambiguous, retryable, and permanent failures."""
        try:
            async with self._client.stream(
                "POST",
                self._endpoint,
                headers=self._headers,
                content=payload,
                follow_redirects=False,
            ) as response:
                status = response.status_code
                retry_after = response.headers.get("Retry-After")
                body = bytearray()
                if 200 <= status < 300:
                    async for chunk in response.aiter_bytes():
                        if len(body) + len(chunk) > MAX_OTLP_RESPONSE_BYTES:
                            return ExportResult(
                                False,
                                True,
                                "invalid_otlp_response",
                                "OTLP response exceeds the size limit",
                            )
                        body.extend(chunk)
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError):
            # Transport exception messages can embed the configured URL or credentials.
            return ExportResult(False, True, "transport_error", "OTLP transport failed")

        if 200 <= status < 300:
            if body:
                response_type, rejected_field = RESPONSE_TYPES[self.signal]
                parsed: Any = response_type()
                try:
                    parsed.ParseFromString(bytes(body))
                except Exception:
                    return ExportResult(
                        False, True, "invalid_otlp_response", "OTLP response is not valid Protobuf"
                    )
                partial = parsed.partial_success
                if getattr(partial, rejected_field):
                    # OTLP requires no retry of partial_success requests. Receivers cannot
                    # identify rejected items, so retain this signal's safe terminal failure.
                    return ExportResult(
                        False,
                        False,
                        "otlp_partial_rejection",
                        "OTLP endpoint partially rejected this signal",
                    )
            return ExportResult(True, False)

        retryable = status in RETRYABLE_STATUS_CODES or status >= 500
        return ExportResult(
            False,
            retryable,
            f"http_{status}",
            "OTLP endpoint rejected the export",
            _parse_retry_after(retry_after) if retryable else None,
        )

    async def close(self) -> None:
        """Close the internally owned HTTP connection pool."""
        if self._owns_client:
            await self._client.aclose()


class OtlpMetricsClient(OtlpLogsClient):
    """Use MetricService response and partial-rejection semantics for metric exports."""

    signal = "metrics"


class OtlpTracesClient(OtlpLogsClient):
    """Use TraceService response and partial-rejection semantics for real span exports."""

    signal = "traces"


def _parse_retry_after(value: str | None) -> int | None:
    """Parse bounded delta-seconds or HTTP-date Retry-After values."""
    if not value:
        return None
    try:
        seconds = int(value)
    except ValueError:
        try:
            target = parsedate_to_datetime(value)
            now = datetime.now(UTC)
            if target.tzinfo is None:
                target = target.replace(tzinfo=UTC)
            seconds = max(0, int((target - now).total_seconds()))
        except (TypeError, ValueError, OverflowError):
            return None
    return max(0, min(seconds, RETRY_AFTER_MAX_SECONDS))
