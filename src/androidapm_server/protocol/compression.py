"""Bounded request decompression helpers."""

from __future__ import annotations

import zlib

from androidapm_server.errors import ApiError

GZIP_WINDOW_BITS = 16 + zlib.MAX_WBITS
DECOMPRESSION_CHUNK_BYTES = 64 * 1024


def decompress_gzip_bounded(payload: bytes, max_output_bytes: int) -> bytes:
    """Decompress one gzip member while enforcing a hard output limit."""
    decompressor = zlib.decompressobj(GZIP_WINDOW_BITS)
    output = bytearray()
    cursor = 0
    try:
        while cursor < len(payload):
            chunk = payload[cursor : cursor + DECOMPRESSION_CHUNK_BYTES]
            cursor += len(chunk)
            remaining = max_output_bytes - len(output)
            if remaining < 0:
                raise _payload_too_large()
            output.extend(decompressor.decompress(chunk, remaining + 1))
            if len(output) > max_output_bytes or decompressor.unconsumed_tail:
                raise _payload_too_large()
        remaining = max_output_bytes - len(output)
        output.extend(decompressor.flush(remaining + 1))
    except zlib.error as error:
        raise ApiError(400, "invalid_gzip", "The gzip body is invalid") from error

    if len(output) > max_output_bytes:
        raise _payload_too_large()
    if not decompressor.eof:
        raise ApiError(400, "invalid_gzip", "The gzip body is truncated")
    # Multiple members and trailing bytes are rejected to keep accounting deterministic.
    if decompressor.unused_data:
        raise ApiError(400, "invalid_gzip", "Trailing gzip data is not allowed")
    return bytes(output)


def _payload_too_large() -> ApiError:
    """Build the stable decompressed-size error."""
    return ApiError(413, "payload_too_large", "The decompressed request body is too large")
