from __future__ import annotations

import gzip

import pytest

from androidapm_server.errors import ApiError
from androidapm_server.protocol.compression import decompress_gzip_bounded


def test_decompresses_single_bounded_gzip_member() -> None:
    payload = b"hello" * 100
    assert decompress_gzip_bounded(gzip.compress(payload), len(payload)) == payload


def test_rejects_compression_bomb_at_output_limit() -> None:
    with pytest.raises(ApiError) as caught:
        decompress_gzip_bounded(gzip.compress(b"a" * 10_000), 100)
    assert caught.value.status_code == 413


@pytest.mark.parametrize("payload", [b"not-gzip", gzip.compress(b"one") + gzip.compress(b"two")])
def test_rejects_invalid_or_multiple_gzip_members(payload: bytes) -> None:
    with pytest.raises(ApiError) as caught:
        decompress_gzip_bounded(payload, 10_000)
    assert caught.value.code == "invalid_gzip"
