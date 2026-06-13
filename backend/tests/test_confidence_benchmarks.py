"""Benchmark-style confidence scenarios for jitter and noise profiles."""

import pytest

from brain import build_alerts_from_sorted_timestamps_ms
from tests.helpers import NoiseProfile, make_mixed_timestamps_ms, make_periodic_timestamps_ms


def _as_int_ms(timestamps: list[float]) -> list[int]:
    return [int(timestamp) for timestamp in timestamps]


def _max_confidence(alerts) -> int:
    if not alerts:
        return 0
    return max(alert.confidence for alert in alerts)


def _period_matches_signal(alert_period: float, period_ms: int) -> bool:
    """Match fundamental period or common harmonic aliases (2x / 0.5x)."""
    tolerance = period_ms * 0.05
    for candidate in (period_ms, period_ms * 2, period_ms / 2):
        if abs(alert_period - candidate) <= tolerance:
            return True
    return False


def _matching_period_confidence(alerts, period_ms: int, *, fallback_to_max: bool = True) -> int:
    if not alerts:
        return 0
    matches = [
        alert.confidence
        for alert in alerts
        if _period_matches_signal(alert.period_ts, period_ms)
    ]
    if matches:
        return max(matches)
    if fallback_to_max:
        return _max_confidence(alerts)
    return 0


def _has_period_match(alerts, period_ms: int) -> bool:
    if not alerts:
        return False
    return any(_period_matches_signal(alert.period_ts, period_ms) for alert in alerts)


@pytest.fixture(autouse=True)
def _disable_confidence_file_logging(override_confidence_config):
    override_confidence_config(confidence_logging_enabled=False)


@pytest.mark.noise
class TestConfidenceNoiseBenchmarks:
    def test_clean_periodic_baseline_is_highest(self):
        period_ms = 60_000
        duration_ms = period_ms * 180
        clean = make_periodic_timestamps_ms(
            period_ms,
            count=180,
            start_ms=0,
        )
        low_jitter = make_mixed_timestamps_ms(
            period_ms,
            primary_count=180,
            noise=NoiseProfile(jitter_ratio=0.05, seed=21),
        )
        high_jitter = make_mixed_timestamps_ms(
            period_ms,
            primary_count=180,
            noise=NoiseProfile(jitter_ratio=0.20, seed=22),
        )

        clean_alerts = build_alerts_from_sorted_timestamps_ms(
            _as_int_ms(clean),
            endpoint_id="bench-clean",
            native_event_id=1,
        )
        low_jitter_alerts = build_alerts_from_sorted_timestamps_ms(
            _as_int_ms(low_jitter),
            endpoint_id="bench-low-jitter",
            native_event_id=1,
        )
        high_jitter_alerts = build_alerts_from_sorted_timestamps_ms(
            _as_int_ms(high_jitter),
            endpoint_id="bench-high-jitter",
            native_event_id=1,
        )

        clean_conf = _matching_period_confidence(clean_alerts, period_ms)
        low_conf = _matching_period_confidence(low_jitter_alerts, period_ms)
        high_conf = _matching_period_confidence(high_jitter_alerts, period_ms)

        assert clean_alerts is not None and clean_conf > 0
        assert low_jitter_alerts is not None and low_conf > 0
        assert clean_conf >= low_conf
        assert low_conf >= high_conf

    def test_high_dropout_reduces_confidence_vs_low_dropout(self):
        period_ms = 60_000
        low_dropout = make_mixed_timestamps_ms(
            period_ms,
            primary_count=160,
            noise=NoiseProfile(drop_probability=0.05, jitter_ratio=0.10, seed=31),
        )
        high_dropout = make_mixed_timestamps_ms(
            period_ms,
            primary_count=160,
            noise=NoiseProfile(drop_probability=0.35, jitter_ratio=0.10, seed=32),
        )

        low_alerts = build_alerts_from_sorted_timestamps_ms(
            _as_int_ms(low_dropout),
            endpoint_id="bench-low-drop",
            native_event_id=2,
        )
        high_alerts = build_alerts_from_sorted_timestamps_ms(
            _as_int_ms(high_dropout),
            endpoint_id="bench-high-drop",
            native_event_id=2,
        )

        low_conf = _matching_period_confidence(low_alerts, period_ms)
        high_conf = _matching_period_confidence(high_alerts, period_ms)

        if low_alerts and high_alerts:
            assert low_conf >= high_conf

    def test_spread_uniform_noise_scores_lower_than_periodic_signal_at_target_period(self):
        period_ms = 9_000
        spread_noise = make_mixed_timestamps_ms(
            period_ms,
            primary_count=0,
            noise=NoiseProfile(
                uniform_event_count=80,
                uniform_range_ms=(0, 4 * 60 * 60_000),
                seed=41,
            ),
        )
        periodic_with_noise = make_mixed_timestamps_ms(
            period_ms,
            primary_count=80,
            noise=NoiseProfile(
                uniform_event_count=25,
                uniform_range_ms=(0, 4 * 60 * 60_000),
                jitter_ratio=0.08,
                seed=42,
            ),
        )

        noise_alerts = build_alerts_from_sorted_timestamps_ms(
            _as_int_ms(spread_noise),
            endpoint_id="bench-noise-only",
            native_event_id=3,
        )
        periodic_alerts = build_alerts_from_sorted_timestamps_ms(
            _as_int_ms(periodic_with_noise),
            endpoint_id="bench-periodic-noise",
            native_event_id=3,
        )

        noise_period_conf = _matching_period_confidence(
            noise_alerts,
            period_ms,
            fallback_to_max=False,
        )
        periodic_conf = _matching_period_confidence(periodic_alerts, period_ms)

        assert periodic_alerts is not None
        assert _has_period_match(periodic_alerts, period_ms)
        assert periodic_conf > 40
        if _has_period_match(noise_alerts, period_ms):
            assert periodic_conf > noise_period_conf

    def test_bursty_noise_is_harder_than_spread_uniform_noise(self):
        period_ms = 45_000
        spread = make_mixed_timestamps_ms(
            period_ms,
            primary_count=140,
            noise=NoiseProfile(
                uniform_event_count=50,
                uniform_range_ms=(0, 8 * 60 * 60_000),
                jitter_ratio=0.08,
                seed=51,
            ),
        )
        bursty = make_mixed_timestamps_ms(
            period_ms,
            primary_count=140,
            noise=NoiseProfile(
                burst_count=12,
                events_per_burst=8,
                burst_span_ms=3_000,
                uniform_range_ms=(0, 8 * 60 * 60_000),
                jitter_ratio=0.08,
                seed=52,
            ),
        )

        spread_alerts = build_alerts_from_sorted_timestamps_ms(
            _as_int_ms(spread),
            endpoint_id="bench-spread",
            native_event_id=4,
        )
        bursty_alerts = build_alerts_from_sorted_timestamps_ms(
            _as_int_ms(bursty),
            endpoint_id="bench-bursty",
            native_event_id=4,
        )

        spread_conf = _matching_period_confidence(spread_alerts, period_ms)
        bursty_conf = _matching_period_confidence(bursty_alerts, period_ms)

        if spread_alerts and bursty_alerts:
            assert spread_conf >= bursty_conf

    def test_long_jittered_timeline_can_gain_group_confidence_from_multiple_windows(self):
        period_ms = 60_000
        jittered = make_mixed_timestamps_ms(
            period_ms,
            primary_count=220,
            noise=NoiseProfile(jitter_ratio=0.20, seed=61),
        )

        alerts = build_alerts_from_sorted_timestamps_ms(
            _as_int_ms(jittered),
            endpoint_id="bench-long-jitter",
            native_event_id=5,
        )

        assert alerts is not None
        target = next(
            (alert for alert in alerts if _period_matches_signal(alert.period_ts, period_ms)),
            None,
        )
        assert target is not None
        assert target.confidence_breakdown is not None
        if target.confidence_breakdown.group is not None:
            assert target.confidence_breakdown.group.window_count >= 2
            assert target.confidence_breakdown.group.count_bonus > 0
