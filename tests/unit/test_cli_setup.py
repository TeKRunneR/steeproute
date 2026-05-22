# pyright: reportPrivateUsage=false
# Reason: `_emit_osm_age_warning` is intentionally module-private (the `_` prefix
# signals "internal to cli/setup.py" per Architecture §Python code conventions).
# Unit-testing it from outside the module is the correct way to pin its boundary
# semantics — the `_` says "safe to rename if no external usage", and tests
# constraining the contract are explicit external usage.
"""Unit tests for `cli/setup.py` pure helpers (Story 2.9: `_emit_osm_age_warning`)."""

from __future__ import annotations

import datetime
import logging

import pytest

from steeproute.cache import Manifest
from steeproute.cli.setup import _emit_osm_age_warning
from steeproute.models import Area


def _manifest_with(osm_extract_date: str) -> Manifest:
    """Build a synthetic `Manifest` with the given `osm_extract_date`.

    All other fields are arbitrary but valid: `_emit_osm_age_warning` only
    reads `osm_extract_date`, so the rest are stable placeholder content.
    """
    return Manifest(
        area=Area(center=(45.0716, 6.1079), radius_km=2.0),
        untagged_policy="include",
        dem_version="test-dem-1",
        pipeline_content_hash="a" * 64,
        osm_extract_date=osm_extract_date,
        cache_key_hash="0123456789abcdef",
        steeproute_version="0.1.0",
        steeproute_commit="abc1234",
        created_at=osm_extract_date,
    )


# Fixed "now" so age math is deterministic across the test file.
_NOW = datetime.datetime(2026, 5, 22, 12, 0, 0, tzinfo=datetime.UTC)


def _iso_days_before(days: float) -> str:
    """ISO-8601 Z-suffix timestamp `days` ago from `_NOW`."""
    dt = _NOW - datetime.timedelta(days=days)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_emit_osm_age_warning_warns_when_age_exceeds_threshold(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """AC #6: an `osm_extract_date` dated >90 days ago triggers a single `logging.warning`."""
    manifest = _manifest_with(_iso_days_before(120))
    with caplog.at_level(logging.WARNING, logger="steeproute.cli.setup"):
        _emit_osm_age_warning(manifest=manifest, threshold_days=90, now=_NOW)

    warnings = [rec for rec in caplog.records if rec.levelno == logging.WARNING]
    assert len(warnings) == 1
    msg = warnings[0].getMessage()
    assert "120" in msg
    assert "90" in msg
    assert "--force-refresh" in msg


def test_emit_osm_age_warning_silent_below_threshold(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """AC #6: a fresh `osm_extract_date` (0 days old) emits no warning."""
    manifest = _manifest_with(_iso_days_before(0))
    with caplog.at_level(logging.WARNING, logger="steeproute.cli.setup"):
        _emit_osm_age_warning(manifest=manifest, threshold_days=90, now=_NOW)
    assert caplog.records == []


@pytest.mark.parametrize(
    ("age_days", "should_warn"),
    [
        # Threshold semantics are **strict** per the helper docstring: only
        # `age > threshold_days` warns. 90.0 days exactly = no warn; just over
        # = warn. Pinning these two cases freezes the boundary against drift.
        (89.0, False),
        (90.0, False),
        (90.5, True),
        (91.0, True),
    ],
)
def test_emit_osm_age_warning_boundary_semantics(
    caplog: pytest.LogCaptureFixture,
    age_days: float,
    should_warn: bool,
) -> None:
    """AC #6 boundary: `age == threshold` does NOT warn; `age > threshold` does."""
    manifest = _manifest_with(_iso_days_before(age_days))
    with caplog.at_level(logging.WARNING, logger="steeproute.cli.setup"):
        _emit_osm_age_warning(manifest=manifest, threshold_days=90, now=_NOW)
    if should_warn:
        assert len(caplog.records) == 1
    else:
        assert caplog.records == []


def test_emit_osm_age_warning_custom_threshold(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A non-default `threshold_days` (e.g. user passed `--osm-age-warn-days 30`) is honoured."""
    manifest = _manifest_with(_iso_days_before(60))
    with caplog.at_level(logging.WARNING, logger="steeproute.cli.setup"):
        _emit_osm_age_warning(manifest=manifest, threshold_days=30, now=_NOW)
    assert len(caplog.records) == 1


def test_emit_osm_age_warning_swallows_malformed_extract_date(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A manifest with a malformed `osm_extract_date` does not crash the cache-hit path.

    `Manifest.from_dict` (Story 2.7) raises `CacheCorruptedError` on schema
    violations before this helper ever sees the manifest, so reaching this branch
    requires the user hand-editing the file mid-run. The age warning is auxiliary
    diagnostic information — losing it on a malformed date is acceptable; crashing
    the cache-hit happy path because of it is not.
    """
    manifest = _manifest_with("not-an-iso-timestamp")
    with caplog.at_level(logging.WARNING, logger="steeproute.cli.setup"):
        _emit_osm_age_warning(manifest=manifest, threshold_days=90, now=_NOW)
    assert caplog.records == []
