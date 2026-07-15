from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from cryptography.exceptions import InvalidSignature

from androidapm_server.remote_config import (
    generate_signing_keypair,
    rollout_includes,
    sign_config,
    verify_envelope,
)


def test_signs_and_verifies_canonical_remote_config() -> None:
    private_key, public_key = generate_signing_keypair()
    issued = datetime(2026, 7, 16, tzinfo=UTC)
    signed = sign_config(
        private_key,
        1,
        issued,
        issued + timedelta(hours=1),
        2_500,
        {"modules": {"network": {"enabled": True}}, "samplingRate": 0.1},
        "key-1",
    )
    envelope = signed.envelope()
    verify_envelope(public_key, envelope)

    envelope["payload"]["samplingRate"] = 1.0
    with pytest.raises(InvalidSignature):
        verify_envelope(public_key, envelope)


def test_rollout_bucket_is_stable_and_bounded() -> None:
    salt = "01" * 32
    first = rollout_includes(salt, "installation-a", 5_000)
    assert rollout_includes(salt, "installation-a", 5_000) is first
    assert not rollout_includes(salt, "installation-a", 0)
    assert rollout_includes(salt, "installation-a", 10_000)


@pytest.mark.parametrize(
    ("revision", "hours", "rollout"),
    [(0, 1, 10_000), (1, 0, 10_000), (1, 1, -1), (1, 1, 10_001)],
)
def test_rejects_invalid_signed_envelope_bounds(revision: int, hours: int, rollout: int) -> None:
    private_key, _public_key = generate_signing_keypair()
    issued = datetime(2026, 7, 16, tzinfo=UTC)
    with pytest.raises(ValueError):
        sign_config(
            private_key,
            revision,
            issued,
            issued + timedelta(hours=hours),
            rollout,
            {},
            "key",
        )
