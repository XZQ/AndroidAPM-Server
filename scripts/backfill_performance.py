"""Upgrade retained historical performance projections in bounded committed batches."""

import asyncio
import logging

from pydantic import ValidationError
from sqlalchemy import select

from androidapm_server.constants import NORMALIZATION_VERSION
from androidapm_server.db.models import InboxEvent
from androidapm_server.db.session import get_session_factory
from androidapm_server.domain import ApmEvent, NativeFrameIdentity
from androidapm_server.normalization import normalize_event

BATCH_SIZE = 200
MAX_RETAINED_NATIVE_FRAMES = 256
logger = logging.getLogger(__name__)


def _retained_native_frames(value: object, row_id: int) -> tuple[NativeFrameIdentity, ...]:
    """Keep bounded valid historical frames without reconstructing occurrence identity."""
    if not isinstance(value, list):
        logger.info("backfill_native_frames_invalid", extra={"row_id": row_id, "invalid_count": 1})
        return ()
    frames: list[NativeFrameIdentity] = []
    invalid_count = max(0, len(value) - MAX_RETAINED_NATIVE_FRAMES)
    for item in value[:MAX_RETAINED_NATIVE_FRAMES]:
        try:
            frames.append(NativeFrameIdentity.model_validate(item, strict=True))
        except ValidationError:
            # Old corruption must not fail the remaining row/batch or create a fake frame.
            invalid_count += 1
    if invalid_count:
        logger.info(
            "backfill_native_frames_invalid",
            extra={"row_id": row_id, "invalid_count": invalid_count},
        )
    return tuple(frames)


async def backfill() -> int:
    """Reproduce normalization from available raw evidence; expired raw stays unavailable."""
    factory = get_session_factory()
    total, cursor = 0, 0
    while True:
        async with factory() as session:
            rows = (
                await session.scalars(
                    select(InboxEvent)
                    .where(
                        InboxEvent.normalization_version < NORMALIZATION_VERSION,
                        InboxEvent.raw_pruned_at.is_(None),
                        InboxEvent.id > cursor,
                    )
                    .order_by(InboxEvent.id)
                    .limit(BATCH_SIZE)
                )
            ).all()
            if not rows:
                return total
            for row in rows:
                cursor = row.id
                # Durable V3 payloads intentionally omit installation plaintext. This job
                # derives projections and missing Native fingerprints from retained frames;
                # release/native/HMAC identity stays immutable without reconstructing occurrence.
                event = ApmEvent.model_validate({**row.payload_json, "occurrence": None})
                projection = normalize_event(
                    event,
                    native_frames=_retained_native_frames(row.native_identity_json, row.id),
                )
                # Identity, payload hash, occurrence clocks, symbol fingerprints stay immutable.
                row.normalized_json = projection.normalized_json
                row.normalization_version = NORMALIZATION_VERSION
                if row.incident_fingerprint is None and projection.incident_fingerprint is not None:
                    row.incident_fingerprint = projection.incident_fingerprint
                    row.raw_incident_fingerprint = projection.incident_fingerprint
                total += 1
            await session.commit()


if __name__ == "__main__":
    print(f"Updated retained projections: {asyncio.run(backfill())}")
