"""Performance acceptance for count-only signals, fixed categories, and paired FPS windows."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from androidapm_server.config import Settings
from androidapm_server.db.base import Base
from androidapm_server.db.inbox import insert_batch
from androidapm_server.db.maintenance import maintain_once
from androidapm_server.db.models import InboxEvent
from androidapm_server.domain import ApmEvent, IngestMetadata, OccurrenceContext
from androidapm_server.identity import InstallationHmacKeyRing
from androidapm_server.normalization import normalize_event
from androidapm_server.performance import (
    build_performance,
    load_observations,
    summarize,
    summarize_category,
    summarize_weighted_fps,
)
from androidapm_server.performance_catalog import (
    DIMENSION_FIELDS,
    PERFORMANCE_BY_ID,
)
from androidapm_server.query_auth import QueryPrincipal

NOW_MS = int(datetime.now(UTC).timestamp() * 1000)
PRINCIPAL = QueryPrincipal("tenant", "viewer", "app", "local", "viewer", None)


def row(
    module: str,
    name: str,
    fields: dict[str, Any],
    *,
    release: str = "2",
    quality: str = "OCCURRENCE_BOUND",
) -> dict[str, Any]:
    """Create one persisted-shape observation through real normalization."""
    event = ApmEvent(
        event_id="test",
        timestamp=NOW_MS,
        module=module,
        name=name,
        kind="METRIC",
        severity="INFO",
        priority="NORMAL",
        process_name="app",
        thread_name="main",
        fields=fields,
    )
    return {
        "app_version": release,
        "release_identity_quality": quality,
        "occurrence_timestamp_ms": NOW_MS,
        "received_at": datetime.fromtimestamp(NOW_MS / 1000, UTC),
        "normalized_json": normalize_event(event).normalized_json,
    }


def test_fps_weights_actual_windows_and_excludes_missing_invalid_denominators() -> None:
    rows = [
        row("fps", "fps_stats", {"fps": 30, "windowDurationMs": 1_000}),
        row("fps", "fps_stats", {"fps": 60, "windowDurationMs": 3_000}),
        row("fps", "fps_stats", {"fps": 0, "windowDurationMs": 0}),
        row("fps", "fps_stats", {"fps": 90}),
        row("fps", "fps_stats", {"fps": "bad", "windowDurationMs": 5_000}),
    ]
    result = summarize_weighted_fps(rows)
    assert result.value == pytest.approx(52.5)
    assert result.total_window_ms == 4_000
    assert result.sample_count == 2 and result.event_count == 5
    assert result.missing_count == 1 and result.invalid_count == 2
    assert result.state == "DEGRADED" and result.coverage == pytest.approx(0.4)
    # A real zero FPS with a positive observed window remains a legitimate zero.
    assert (
        summarize_weighted_fps([row("fps", "fps_stats", {"fps": 0, "windowDurationMs": 1})]).state
        == "ZERO"
    )
    assert summarize_weighted_fps([]).value is None


def test_huge_finite_windows_do_not_overflow_weighted_result_json() -> None:
    rows = [
        row("fps", "fps_stats", {"fps": 60, "windowDurationMs": 1e308}),
        row("fps", "fps_stats", {"fps": 30, "windowDurationMs": 1e308}),
    ]
    result = summarize_weighted_fps(rows)
    assert result.value == pytest.approx(45)
    assert result.total_window_ms is None
    assert "null" in result.model_dump_json(by_alias=True)


def test_count_only_events_and_crash_app_exit_are_separate_safe_contracts() -> None:
    rows = [
        row("io", "io_closeable_leak", {"path": "/secret/file"}),
        row("webview", "js_console_error", {"errorMessage": "account credential"}),
        row("crash", "java_crash", {}),
        row("crash", "native_crash", {}),
        row("crash", "tombstone_crash", {}, release="1"),
        row("crash", "app_exit", {"reasonCode": 1}, quality="BATCH_DECLARED"),
    ]
    result = build_performance(
        rows,
        PRINCIPAL,
        "query",
        "2",
        "1",
        NOW_MS - 1_000,
        NOW_MS + 1_000,
        PERFORMANCE_BY_ID["fps.fps_stats.fps"],
        10_000,
    )
    events = {(item.module, item.name): item for item in result.event_statistics}
    assert events["io", "io_closeable_leak"].current_count == 1
    assert events["webview", "js_console_error"].current_count == 1
    assert events["crash", "native_crash"].current_count == 1
    assert events["crash", "tombstone_crash"].baseline_count == 1
    assert events["crash", "app_exit"].current_count == 0
    assert events["crash", "app_exit"].non_occurrence_count == 1
    assert events["battery", "gps_still_active"].total_count == 0
    encoded = result.model_dump_json(by_alias=True)
    assert "/secret/file" not in encoded and "account credential" not in encoded


def test_fixed_categories_preserve_false_missing_invalid_unknown_without_exposing_value() -> None:
    field = next(
        item for item in DIMENSION_FIELDS if item.id == "webview.slow_resource.resourceType"
    )
    rows = [
        row("webview", "slow_resource", {"resourceType": "script"}),
        row("webview", "slow_resource", {"resourceType": "https://secret/customer"}),
        row("webview", "slow_resource", {"resourceType": 7}),
        row("webview", "slow_resource", {}),
    ]
    result = summarize_category(rows, field)
    assert result.counts["script"] == 1 and result.counts["image"] == 0
    assert result.available_count == 1 and result.event_count == 4
    assert result.unknown_count == 1 and result.invalid_count == 1 and result.missing_count == 1
    assert "customer" not in result.model_dump_json()
    boolean_field = next(item for item in DIMENSION_FIELDS if item.id == "io.io_issue.isMainThread")
    result_bool = summarize_category(
        [row("io", "io_issue", {"isMainThread": False}), row("io", "io_issue", {})], boolean_field
    )
    assert result_bool.counts == {"false": 1, "true": 0}
    assert result_bool.missing_count == 1


async def test_actual_raw_prune_separates_expired_categories_and_retains_boolean_facts() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.fromtimestamp(NOW_MS / 1000, UTC)
    settings = Settings(_env_file=None, delivered_retention_days=1)
    occurrence = OccurrenceContext(
        service_version="2",
        version_code="1",
        app_build="build",
        variant="debug",
        installation_id="retention-statistics-installation",
    )
    events = [
        ApmEvent(
            event_id=f"category-retention-{index}",
            timestamp=NOW_MS,
            module=module,
            name=name,
            kind="METRIC",
            severity="INFO",
            priority="NORMAL",
            process_name="app",
            thread_name="main",
            fields=fields,
            occurrence=occurrence,
        )
        for index, (module, name, fields) in enumerate(
            [
                ("webview", "slow_resource", {"resourceType": "script"}),
                ("webview", "slow_resource", {"resourceType": "https://private/customer"}),
                ("webview", "slow_resource", {"resourceType": 7}),
                ("webview", "slow_resource", {}),
                ("io", "io_issue", {"isMainThread": False}),
            ]
        )
    ]
    try:
        async with factory() as session:
            await insert_batch(
                session,
                IngestMetadata(
                    request_id="statistics-retention",
                    tenant_id="tenant",
                    app_id="app",
                    environment="local",
                    schema_version="3",
                    sdk_version="test",
                    protocol="protobuf_v3",
                ),
                events,
                InstallationHmacKeyRing({"v1": b"synthetic-retention-test-key-bytes"}, "v1"),
            )
            await session.execute(
                update(InboxEvent).values(status="delivered", finalized_at=now - timedelta(days=2))
            )
            await session.commit()
        assert await maintain_once(factory, settings, now) == (5, 0)
        async with factory() as session:
            stored = (await session.scalars(select(InboxEvent))).all()
            assert all(item.raw_pruned_at is not None for item in stored)
            observations = await load_observations(
                session, PRINCIPAL, settings, NOW_MS - 1_000, NOW_MS + 1_000
            )
        result = build_performance(
            observations,
            PRINCIPAL,
            "retention-query",
            "2",
            "1",
            NOW_MS - 1_000,
            NOW_MS + 1_000,
            PERFORMANCE_BY_ID["fps.fps_stats.fps"],
            10_000,
        )
        dimensions = {item.id: item.current for item in result.dimensions}
        resource = dimensions["webview.slow_resource.resourceType"]
        assert resource.event_count == 4 and resource.available_count == 0
        assert resource.state == "UNAVAILABLE" and resource.counts["script"] == 0
        assert resource.expired_count == resource.unknown_count == 1
        assert resource.invalid_count == resource.missing_count == 1
        assert resource.model_dump(by_alias=True)["expiredCount"] == 1
        main_thread = dimensions["io.io_issue.isMainThread"]
        assert main_thread.state == "PRESENT" and main_thread.counts["false"] == 1
        assert main_thread.expired_count == main_thread.unknown_count == 0
        assert "private/customer" not in result.model_dump_json(by_alias=True)
    finally:
        await engine.dispose()


def test_gc_availability_and_cpu_ratio_follow_actual_source_semantics() -> None:
    gc_field = PERFORMANCE_BY_ID["gc_monitor.memory_churn.gcCountDelta"]
    observation = summarize(
        [row("gc_monitor", "memory_churn", {"gcCountDelta": 0, "gcCountersAvailable": False})],
        gc_field,
    )
    assert observation.state == "UNAVAILABLE" and observation.unavailable_count == 1
    cpu_field = PERFORMANCE_BY_ID["battery.cpu_high_usage.cpuPercent"]
    assert cpu_field.unit == "cores"
    assert summarize([row("battery", "cpu_high_usage", {"cpuPercent": 0.8})], cpu_field).mean == 0.8
    assert (
        summarize([row("battery", "cpu_high_usage", {"cpuPercent": -1})], cpu_field).invalid_count
        == 1
    )
    assert summarize([row("battery", "cpu_high_usage", {"cpuPercent": 2.4})], cpu_field).mean == 2.4
    heap_field = PERFORMANCE_BY_ID["gc_monitor.memory_churn.heapGrowth"]
    assert (
        summarize([row("gc_monitor", "memory_churn", {"heapGrowth": -0.2})], heap_field).mean
        == -0.2
    )


def test_actual_extended_contracts_are_aggregated_without_sensitive_dimensions() -> None:
    fixtures = (
        ("io", "io_fd_leak", "fdCount", 700),
        ("io", "io_throughput", "totalReadBytes", 4096),
        ("io", "io_zero_copy_opportunity", "avgBuffers", "4.5"),
        ("render", "deep_hierarchy", "maxDepth", 12),
        ("render", "frame_metrics", "droppedFrames", 2),
        ("webview", "white_screen", "durationMs", 3000),
        ("webview", "resource_waterfall", "criticalCount", 2),
        ("memory", "memory_leak", "retainedCount", 1),
        ("battery", "wakelock_still_held", "durationMs", 60000),
        ("battery", "gps_used_too_long", "durationMs", 31000),
        ("battery", "alarm_schedule_flood", "alarmCount", 12),
        ("thread_monitor", "blocked_thread", "blockedCount", 4),
        ("slow_method", "hot_methods_detected", "sampleCount", 100),
        ("fps", "fps_stats", "metricsDrawNs", 16000000),
    )
    for module, name, field, value in fixtures:
        summary = summarize(
            [row(module, name, {field: value, "url": "secret", "stack": "secret"})],
            PERFORMANCE_BY_ID[f"{module}.{name}.{field}"],
        )
        assert summary.mean == float(value)
    sensitive_fields = {
        "url",
        "sql",
        "path",
        "stackTrace",
        "scene",
        "wakeLockTag",
        "gpsTag",
        "threadName",
        "poolName",
        "bridgeName",
        "method",
    }
    assert not sensitive_fields.intersection(item.field for item in DIMENSION_FIELDS)


def test_counts_reject_fractional_samples_but_average_buffer_counts_preserve_fraction() -> None:
    count_field = PERFORMANCE_BY_ID["io.io_fd_leak.fdCount"]
    result = summarize([row("io", "io_fd_leak", {"fdCount": 1.5})], count_field)
    assert result.invalid_count == 1 and result.mean is None
    buffer_field = PERFORMANCE_BY_ID["io.io_zero_copy_opportunity.avgBuffers"]
    result = summarize([row("io", "io_zero_copy_opportunity", {"avgBuffers": "4.5"})], buffer_field)
    assert result.mean == 4.5


def test_empty_dispatcher_histograms_do_not_create_observed_zero_latency() -> None:
    field = PERFORMANCE_BY_ID["core.sdk_health.dispatcherStage.resolve.avgMicros"]
    missing = summarize(
        [
            row(
                "core",
                "sdk_health",
                {"dispatcherStage.resolve.avgMicros": 0, "dispatcherStage.resolve.count": 0},
            )
        ],
        field,
    )
    real_zero = summarize(
        [
            row(
                "core",
                "sdk_health",
                {"dispatcherStage.resolve.avgMicros": 0, "dispatcherStage.resolve.count": 2},
            )
        ],
        field,
    )
    assert missing.state == "UNAVAILABLE" and missing.mean is None
    assert real_zero.state == "ZERO" and real_zero.mean == 0


def test_fps_severity_uses_bounded_integer_categories_and_preserves_unknown() -> None:
    field = next(item for item in DIMENSION_FIELDS if item.id == "fps.fps_stats.dropSeverity")
    result = summarize_category(
        [
            row("fps", "fps_stats", {"dropSeverity": 0}),
            row("fps", "fps_stats", {"dropSeverity": 3}),
            row("fps", "fps_stats", {"dropSeverity": 4}),
            row("fps", "fps_stats", {"dropSeverity": 1.5}),
        ],
        field,
    )
    assert result.counts == {"0": 1, "1": 0, "2": 0, "3": 1}
    assert result.unknown_count == 1 and result.invalid_count == 1
