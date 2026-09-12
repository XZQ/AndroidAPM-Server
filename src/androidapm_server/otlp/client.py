"""OTLP/HTTP Protobuf client with explicit retry classification."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceResponse

from androidapm_server.constants import RETRY_AFTER_MAX_SECONDS

OTLP_CONTENT_TYPE = "application/x-protobuf"
RETRYABLE_STATUS_CODES = frozenset({408, 429, 502, 503, 504})


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
            isinstance(key, str) and isinstance(value, str)
            for key, value in parsed_headers.items()
        ):
            raise ValueError("OTLP headers JSON must be an object of string values")
        self._endpoint = endpoint
        self._headers = {"Content-Type": OTLP_CONTENT_TYPE, **parsed_headers}
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None

    async def export(self, payload: bytes) -> ExportResult:
        """Export one request and classify ambiguous, retryable, and permanent failures."""
        try:
            response = await self._client.post(
                self._endpoint,
                headers=self._headers,
                content=payload,
            )
        except (httpx.TimeoutException, httpx.NetworkError) as error:
            return ExportResult(False, True, "transport_error", str(error)[:512])

        if 200 <= response.status_code < 300:
            if response.content:
                parsed = ExportLogsServiceResponse()
                try:
                    parsed.ParseFromString(response.content)
                except Exception as error:
                    return ExportResult(False, True, "invalid_otlp_response", str(error)[:512])
                partial = parsed.partial_success
                if partial.rejected_log_records:
                    return ExportResult(
                        False,
                        False,
                        "otlp_partial_rejection",
                        partial.error_message[:512],
                    )
            return ExportResult(True, False)

        retryable = response.status_code in RETRYABLE_STATUS_CODES or response.status_code >= 500
        return ExportResult(
            False,
            retryable,
            f"http_{response.status_code}",
            "OTLP endpoint rejected the export",
            _parse_retry_after(response.headers.get("Retry-After")) if retryable else None,
        )

    async def close(self) -> None:
        """Close the internally owned HTTP connection pool."""
        if self._owns_client:
            await self._client.aclose()


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
