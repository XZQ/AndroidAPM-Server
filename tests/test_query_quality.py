"""Regress missing health and confidence gates with explicit synthetic occurrence facts."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from androidapm_server.db.query import QueryFact
from androidapm_server.query import (
    build_data_quality,
    build_issue_detail,
    build_release_health,
    build_top_fingerprints,
)
from androidapm_server.query_auth import QueryPrincipal

PRINCIPAL = QueryPrincipal("tenant", "key", "app", "test", "viewer", None)
NOW = datetime.now(UTC)
TIME = int(NOW.timestamp() * 1000)


def fact(**changes: object) -> QueryFact:
    """Create a complete SDK sample from an occurrence-bound installation."""
    base = QueryFact(
        id=1,
        event_id="sample",
        app_version="new",
        release_identity_quality="OCCURRENCE_BOUND",
        installation_identity_quality="OCCURRENCE_BOUND",
        installation_hmac="installation-a",
        installation_hmac_key_version="v1",
        occurrence_timestamp_ms=TIME,
        received_at=NOW,
        incident_fingerprint=None,
        schema_version="3",
        protocol="protobuf",
        inbox_status="pending",
        module="core",
        name="sdk_health",
        scene=None,
        sdk_emit_count=20,
        sdk_drop_count=0,
        sdk_drop_rate=0.0,
    )
    return replace(base, **changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"sdk_drop_rate": None},
        {"sdk_drop_count": None},
        {"sdk_emit_count": None},
        {"sdk_emit_count": 0},
        {"sdk_drop_rate": float("nan")},
        {"sdk_drop_rate": -1},
    ],
)
def test_missing_invalid_or_empty_health_cannot_be_zero(changes: dict[str, object]) -> None:
    facts = [fact(**changes)]
    result = build_release_health("r", PRINCIPAL, facts, "new", "old", TIME - 1, TIME + 1, 1000, 1)
    quality = build_data_quality("r", PRINCIPAL, facts, "new", TIME - 1, TIME + 1, 1000)
    assert result.new_release.metrics.sdk_drop_rate.state == "UNAVAILABLE"
    assert result.new_release.metrics.sdk_drop_rate.value is None
    assert result.new_release.metrics.affected_installation_ratio.value is None
    assert quality.sdk_health.state == "UNAVAILABLE"
    assert quality.sdk_health.value is None


@pytest.mark.parametrize(
    "case,reason",
    [
        ("missing", "SDK_HEALTH_NOT_PROVIDED"),
        ("drops", "SDK_REPORTED_DROPS"),
        ("late", "LATE_DATA_PRESENT"),
        ("coverage", "SDK_HEALTH_INSTALLATION_COVERAGE_INCOMPLETE"),
        ("sample", "INSUFFICIENT_INSTALLATION_SAMPLE"),
    ],
)
def test_ratio_requires_all_quality_gates(case: str, reason: str) -> None:
    facts = [fact()]
    if case == "missing":
        facts = [fact(module="crash", name="java_crash")]
    elif case == "drops":
        facts = [fact(sdk_drop_count=1, sdk_drop_rate=0.05)]
    elif case == "late":
        facts = [fact(received_at=NOW + timedelta(hours=1))]
    elif case == "coverage":
        facts.append(fact(id=2, module="crash", name="java_crash", installation_hmac="b"))
    result = build_release_health(
        "r", PRINCIPAL, facts, "new", "old", TIME - 1, TIME + 1, 1000, 2 if case == "sample" else 1
    )
    ratio = result.new_release.metrics.affected_installation_ratio
    assert ratio.value is None
    assert ratio.reason == reason
    assert result.comparison.affected_installation_ratio_delta is None
    assert result.new_release.metrics.java_crash_events.value is not None


def test_complete_healthy_sample_can_report_real_zero() -> None:
    result = build_release_health(
        "r", PRINCIPAL, [fact()], "new", "old", TIME - 1, TIME + 1, 1000, 1
    )
    assert result.new_release.metrics.sdk_drop_rate.state == "ZERO"
    assert result.new_release.metrics.affected_installation_ratio.state == "ZERO"
    assert result.new_release.metrics.affected_installation_ratio.value == 0


@pytest.mark.parametrize("declared_only", [False, True])
def test_missing_release_trends_never_fabricate_zero(declared_only: bool) -> None:
    facts = (
        [fact(release_identity_quality="BATCH_DECLARED", schema_version="2")]
        if declared_only
        else []
    )
    result = build_release_health("r", PRINCIPAL, facts, "new", "old", TIME - 1, TIME + 1, 1000)
    assert result.new_release.metrics.java_crash_events.value is None
    assert result.trend[0].new_java_crash_events is None
    assert result.trend[0].new_anr_events is None
    assert result.trend[0].new_state == ("UNAVAILABLE" if declared_only else "NO_DATA")
    assert result.trend[0].baseline_java_crash_events is None
    assert result.trend[0].baseline_state == "NO_DATA"


def test_release_trend_preserves_observed_zero_missing_bucket_and_incident_count() -> None:
    facts = [
        fact(occurrence_timestamp_ms=TIME - 120_000),
        fact(module="crash", name="java_crash", weight=3),
    ]
    result = build_release_health(
        "r", PRINCIPAL, facts, "new", "old", TIME - 120_000, TIME + 60_000, 1000
    )
    assert [point.new_java_crash_events for point in result.trend] == [0, None, 3]
    assert [point.new_state for point in result.trend] == ["ZERO", "NO_DATA", "PRESENT"]
    assert all(point.baseline_java_crash_events is None for point in result.trend)
    issue = build_issue_detail("r", PRINCIPAL, [], "missing", TIME - 120_000, TIME + 60_000)
    assert all(
        point.event_count is None and point.affected_installation_count is None
        for point in issue.trend
    )


@pytest.mark.parametrize("different_release", [False, True])
def test_rotated_hmac_does_not_double_count_or_compare_distinct_epochs(
    different_release: bool,
) -> None:
    facts = [
        fact(),
        fact(
            id=2,
            installation_hmac="rotated-same-installation",
            installation_hmac_key_version="v2",
            app_version="old" if different_release else "new",
        ),
    ]
    result = build_release_health("r", PRINCIPAL, facts, "new", "old", TIME - 1, TIME + 1, 1000, 1)
    assert result.new_release.metrics.active_installations.value is None
    assert (
        result.new_release.metrics.active_installations.reason
        == "INSTALLATION_HMAC_CONTINUITY_BREAK"
    )
    assert result.comparison.affected_installation_delta is None
    assert result.comparison.affected_installation_ratio_delta is None
    assert result.new_release.metrics.java_crash_events.value == 0


def test_rotation_is_explicit_in_issue_distributions_trend_and_quality() -> None:
    facts = [
        fact(module="crash", name="java_crash", incident_fingerprint="fp"),
        fact(
            id=2,
            module="crash",
            name="java_crash",
            incident_fingerprint="fp",
            installation_hmac="rotated",
            installation_hmac_key_version="v2",
        ),
    ]
    issue = build_issue_detail("r", PRINCIPAL, facts, "fp", TIME - 1, TIME + 1)
    assert issue.event_count == 2
    assert issue.affected_installation_count is None
    assert issue.reason == "INSTALLATION_HMAC_CONTINUITY_BREAK"
    assert all(point.affected_installation_count is None for point in issue.trend)
    assert all(item.affected_installation_count is None for item in issue.releases.items)
    fingerprints = build_top_fingerprints("r", PRINCIPAL, facts, "new", TIME - 1, TIME + 1, 10)
    assert fingerprints.items[0].affected_installation_count is None
    assert fingerprints.installation_reason == issue.reason
    quality = build_data_quality("r", PRINCIPAL, facts, "new", TIME - 1, TIME + 1, 1000)
    assert quality.installation_identity.reason == issue.reason
