"""Tests for conservative 2x superharmonic period canonicalization."""

import math

import pytest

from brain import build_alerts_from_sorted_timestamps_ms
from fourier import evaluate_period, resolve_superharmonic_canonical_period, resolve_subharmonic_alias_period, period_supported_by_event_spacing, compute_spacing_alias_penalty
from tests.helpers import NoiseProfile, make_mixed_timestamps_ms, make_periodic_timestamps_ms


@pytest.mark.unit
class TestResolveSuperharmonicCanonicalPeriod:
    def test_true_60s_prefers_half_over_weak_2x_alias(self):
        timestamps = make_periodic_timestamps_ms(60_000, count=24)
        mag_60, _phase_60 = evaluate_period(timestamps, 60_000.0)
        mag_120, _phase_120 = evaluate_period(timestamps, 120_000.0)

        assert mag_60 > mag_120 * 1.25
        assert mag_120 < mag_60 * 0.55

        result = resolve_superharmonic_canonical_period(
            120_000.0,
            mag_120,
            timestamps,
        )

        assert result.canonicalized
        assert result.period_ms == 60_000.0
        assert result.original_period_ms == 120_000.0

    def test_true_120s_sparse_events_keep_long_period(self):
        timestamps = make_periodic_timestamps_ms(120_000, count=16)
        mag_60, _phase_60 = evaluate_period(timestamps, 60_000.0)
        mag_120, phase_120 = evaluate_period(timestamps, 120_000.0)

        assert mag_120 > 0.4
        assert mag_60 > mag_120 * 0.55

        result = resolve_superharmonic_canonical_period(
            120_000.0,
            mag_120,
            timestamps,
            phase=phase_120,
        )

        assert not result.canonicalized
        assert result.period_ms == 120_000.0

    def test_does_not_step_down_to_quarter_period(self):
        timestamps = make_periodic_timestamps_ms(60_000, count=20)
        mag_60, phase_60 = evaluate_period(timestamps, 60_000.0)

        result = resolve_superharmonic_canonical_period(
            60_000.0,
            mag_60,
            timestamps,
            phase=phase_60,
        )

        assert not result.canonicalized
        assert result.period_ms == 60_000.0


@pytest.mark.unit
class TestResolveSubharmonicAliasPeriod:
    def test_5_minute_events_prefer_5_minute_when_enabled(self, override_harmonic_config):
        override_harmonic_config(subharmonic_alias_resolution_enabled=True)
        import fourier

        timestamps = make_periodic_timestamps_ms(300_000, count=12)
        mag_1min, _phase_1min = evaluate_period(timestamps, 60_000.0)
        mag_5min, _phase_5min = evaluate_period(timestamps, 300_000.0)

        assert mag_5min > 0
        assert mag_1min >= mag_5min * 0.85

        result = fourier.resolve_subharmonic_alias_period(
            60_000.0,
            mag_1min,
            timestamps,
        )

        assert result.canonicalized
        assert result.period_ms == 300_000.0

    def test_subharmonic_resolution_disabled_by_default(self):
        timestamps = make_periodic_timestamps_ms(300_000, count=12)
        mag_1min, _phase_1min = evaluate_period(timestamps, 60_000.0)

        result = resolve_subharmonic_alias_period(
            60_000.0,
            mag_1min,
            timestamps,
        )

        assert not result.canonicalized
        assert result.period_ms == 60_000.0

    def test_true_1_minute_signal_is_not_stepped_up_without_support(self, override_harmonic_config):
        override_harmonic_config(subharmonic_alias_resolution_enabled=True)
        import fourier

        timestamps = make_periodic_timestamps_ms(60_000, count=24)
        mag_1min, phase_1min = evaluate_period(timestamps, 60_000.0)

        result = fourier.resolve_subharmonic_alias_period(
            60_000.0,
            mag_1min,
            timestamps,
            phase=phase_1min,
        )

        assert not result.canonicalized
        assert result.period_ms == 60_000.0


@pytest.mark.unit
class TestPeriodSupportedByEventSpacing:
    def test_burst_same_timestamp_is_rejected(self):
        timestamps = [1_000.0] * 5

        assert not period_supported_by_event_spacing(60_000.0, timestamps)

    def test_short_period_allowed_when_spacing_is_long(self):
        timestamps = make_periodic_timestamps_ms(300_000, count=8)

        assert period_supported_by_event_spacing(300_000.0, timestamps)
        assert period_supported_by_event_spacing(60_000.0, timestamps)

    def test_spacing_penalty_hits_short_period_on_long_spacing(self):
        timestamps = make_periodic_timestamps_ms(300_000, count=8)

        assert compute_spacing_alias_penalty(300_000.0, timestamps) == 0.0
        assert compute_spacing_alias_penalty(60_000.0, timestamps) > 10.0


@pytest.mark.noise
class TestSpacingAliasPenaltyIntegration:
    def test_5_minute_timeline_keeps_1_minute_ghost_below_5_minute(self):
        timestamps = make_periodic_timestamps_ms(300_000, count=16)
        alerts = build_alerts_from_sorted_timestamps_ms(
            [int(timestamp) for timestamp in timestamps],
            endpoint_id="spacing-penalty-5m",
            native_event_id=11,
        )

        if not alerts:
            pytest.skip("No alerts produced for clean 5m scenario")

        five_minute = [
            alert for alert in alerts
            if 295_000 <= alert.period_ts <= 305_000
        ]
        one_minute = [
            alert for alert in alerts
            if 58_000 <= alert.period_ts <= 62_000
        ]

        assert five_minute
        if one_minute:
            assert max(alert.confidence for alert in one_minute) < max(
                alert.confidence for alert in five_minute
            )


@pytest.mark.noise
class TestSubharmonicIntegration:
    def test_5_minute_timeline_still_finds_five_minute_period(self):
        timestamps = make_periodic_timestamps_ms(300_000, count=16)
        alerts = build_alerts_from_sorted_timestamps_ms(
            [int(timestamp) for timestamp in timestamps],
            endpoint_id="subharmonic-5m",
            native_event_id=11,
        )

        if not alerts:
            pytest.skip("No alerts produced for clean 5m scenario")

        five_minute_alerts = [
            alert for alert in alerts
            if 295_000 <= alert.period_ts <= 305_000
        ]

        assert five_minute_alerts


@pytest.mark.noise
class TestCanonicalizationIntegration:
    def test_noisy_60s_timeline_prefers_60s_over_120s_when_present(self):
        timestamps = make_mixed_timestamps_ms(
            60_000,
            primary_count=120,
            noise=NoiseProfile(
                uniform_event_count=40,
                uniform_range_ms=(0, 6 * 60 * 60_000),
                jitter_ratio=0.10,
                seed=42,
            ),
        )
        alerts = build_alerts_from_sorted_timestamps_ms(
            [int(timestamp) for timestamp in timestamps],
            endpoint_id="canonical-60s",
            native_event_id=7,
        )

        if not alerts:
            pytest.skip("No alerts produced for noisy 60s scenario")

        target_periods = [alert.period_ts for alert in alerts]
        has_60s = any(abs(period - 60_000) <= 3_000 for period in target_periods)
        has_only_weak_120s = all(
            not (118_000 <= period <= 122_000 and alert.confidence >= 40)
            for period, alert in zip(target_periods, alerts)
        )

        assert has_60s or has_only_weak_120s

    def test_clean_60s_still_reports_near_60s(self):
        timestamps = make_periodic_timestamps_ms(60_000, count=120)
        alerts = build_alerts_from_sorted_timestamps_ms(
            [int(timestamp) for timestamp in timestamps],
            endpoint_id="canonical-clean",
            native_event_id=8,
        )

        assert alerts is not None
        assert any(abs(alert.period_ts - 60_000) <= 3_000 for alert in alerts)
