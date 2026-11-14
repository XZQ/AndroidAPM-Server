"""The local preview fixture must demonstrate the intended fresh release scenario.

These regressions pin the seed contract: fresh (non-LATE) occurrences, idempotent
reseeding, evidence-bearing incidents only, a separated V2 legacy release, and
drop breakdowns consistent with the drop counters.
"""

from __future__ import annotations

import base64
import importlib.util
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from androidapm_server.config import Settings, get_settings
from androidapm_server.db.base import Base
from androidapm_server.db.models import InboxEvent
from androidapm_server.db.session import get_session
from androidapm_server.main import create_app

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "seed_local_preview.py"
_spec = importlib.util.spec_from_file_location("seed_local_preview", _SCRIPT)
assert _spec is not None and _spec.loader is not None
seed = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(seed)

HMAC = json.dumps({"v1": base64.b64encode(b"local-preview-test-key-32-bytes!!").decode()})


@pytest_asyncio.fixture
async def preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[AsyncClient, async_sessionmaker[AsyncSession], str]]:
    local_root = tmp_path / ".local"
    local_root.mkdir()
    database = local_root / "preview.db"
    settings = Settings(
        _env_file=None,
        environment="local",
        database_url=f"sqlite+aiosqlite:///{database.as_posix()}",
        installation_hmac_keys_json=HMAC,
        query_min_installations=3,
    )
    engine = create_async_engine(settings.database_url.get_secret_value())
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(seed, "LOCAL_ROOT", local_root)
    monkeypatch.setattr(seed, "get_settings", lambda: settings)
    monkeypatch.setattr(seed, "get_session_factory", lambda: factory)

    query_key, _ingest_key, inserted, compatibility = await seed.seed_local_preview()
    assert inserted > 0
    assert compatibility == 1

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_settings] = lambda: settings
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, factory, query_key
    await engine.dispose()


def auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def window() -> dict[str, int]:
    now = int(datetime.now(UTC).timestamp() * 1_000)
    return {"fromMs": now - 3_600_000, "toMs": now + 60_000}


@pytest.mark.asyncio
async def test_reseed_replaces_prior_generations_without_duplication(
    preview: tuple[AsyncClient, async_sessionmaker[AsyncSession], str],
) -> None:
    _client, factory, _key = preview
    async with factory() as session:
        before = await session.scalar(select(func.count()).select_from(InboxEvent))
    _query, _ingest, inserted, compatibility = await seed.seed_local_preview()
    assert inserted > 0
    assert compatibility == 1
    async with factory() as session:
        after = await session.scalar(select(func.count()).select_from(InboxEvent))
    assert after == before


@pytest.mark.asyncio
async def test_preview_release_comparison_is_fresh_and_session_rates_render(
    preview: tuple[AsyncClient, async_sessionmaker[AsyncSession], str],
) -> None:
    client, _factory, key = preview
    response = await client.get(
        "/v1/query/release-health",
        params={"newRelease": "2.0.0", "baselineRelease": "1.0.0", **window()},
        headers=auth(key),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    for slice_name in ("newRelease", "baselineRelease"):
        assert body[slice_name]["state"] != "LATE", body[slice_name]
    # The V2 compatibility row must not inflate the new release's declared sample.
    assert body["newRelease"]["declaredSampleCount"] == body["newRelease"]["eligibleSampleCount"]
    # The clean baseline renders a real session rate under the preview installation gate.
    baseline_sessions = body["baselineRelease"]["metrics"]["crashFreeSessions"]
    assert baseline_sessions["state"] == "PRESENT", baseline_sessions
    assert baseline_sessions["value"] == pytest.approx(2 / 3), baseline_sessions
    # The new release intentionally reports SDK drops, so its session rate stays gated.
    new_sessions = body["newRelease"]["metrics"]["crashFreeSessions"]
    assert new_sessions["reason"] == "SDK_REPORTED_DROPS", new_sessions
    assert new_sessions["value"] is None, new_sessions


@pytest.mark.asyncio
async def test_preview_incidents_all_carry_evidence_and_fingerprints(
    preview: tuple[AsyncClient, async_sessionmaker[AsyncSession], str],
) -> None:
    client, factory, key = preview
    response = await client.get(
        "/v1/query/fingerprints",
        params={"releaseVersion": "2.0.0", "limit": 50, **window()},
        headers=auth(key),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["fingerprintCoverage"] == 1.0, body
    families = {item["eventFamily"] for item in body["items"]}
    assert {"JAVA_CRASH", "ANR", "NATIVE_CRASH", "NATIVE_TOMBSTONE"} <= families
    incident_pairs = {
        ("crash", "java_crash"),
        ("anr", "anr_detected"),
        ("crash", "native_crash"),
        ("crash", "tombstone_crash"),
    }
    async with factory() as session:
        rows = (
            await session.scalars(
                select(InboxEvent).where(InboxEvent.incident_fingerprint.is_(None))
            )
        ).all()
    # Non-incident events legitimately have no fingerprint; every incident must have one.
    assert [
        row.event_id
        for row in rows
        if (row.payload_json["module"], row.payload_json["name"]) in incident_pairs
    ] == []


@pytest.mark.asyncio
async def test_preview_sdk_health_drop_breakdown_matches_counters(
    preview: tuple[AsyncClient, async_sessionmaker[AsyncSession], str],
) -> None:
    _client, factory, _key = preview
    async with factory() as session:
        rows = (
            await session.scalars(
                select(InboxEvent).where(
                    InboxEvent.payload_json["module"].as_string() == "core",
                    InboxEvent.payload_json["name"].as_string() == "sdk_health",
                )
            )
        ).all()
    assert rows
    for row in rows:
        fields = row.payload_json.get("fields", {})
        reason_keys = [key for key in fields if key.startswith("dropReason")]
        reasons = sum(fields[key] for key in reason_keys)
        drop_count = fields.get("dropCount", 0)
        if drop_count == 0:
            # A zero drop counter must not contradict a nonzero per-reason breakdown.
            assert reasons == 0, row.event_id
        elif reason_keys:
            assert reasons == drop_count, row.event_id
        # dropCount > 0 without any reason keys is missing evidence, not a contradiction.


@pytest.mark.asyncio
async def test_preview_v2_legacy_release_is_separated(
    preview: tuple[AsyncClient, async_sessionmaker[AsyncSession], str],
) -> None:
    client, _factory, key = preview
    response = await client.get(
        "/v1/query/release-health",
        params={"newRelease": seed.LEGACY_RELEASE, "baselineRelease": "1.0.0", **window()},
        headers=auth(key),
    )
    assert response.status_code == 200, response.text
    legacy = response.json()["newRelease"]
    # Batch-declared compatibility evidence stays visible but never occurrence-eligible.
    assert legacy["declaredSampleCount"] == 1
    assert legacy["eligibleSampleCount"] == 0
