import math

import pytest

from brain import AlertCore, suppress_harmonic_ghost_alerts
from fourier import (
    _harmonic_support_at_period,
    evaluate_period,
    filter_by_harmony,
    fourier_transform,
    get_median_value,
)
from tests.helpers import make_periodic_timestamps_ms


@pytest.mark.unit
class TestEvaluatePeriod:
    def test_evaluates_arbitrary_period_not_on_candidate_grid(self):
        period_ms = 9_000
        timestamps = make_periodic_timestamps_ms(period_ms, count=12)

        fundamental_magnitude, _phase = evaluate_period(timestamps, float(period_ms))
        harmonic_magnitude, _phase = evaluate_period(timestamps, float(period_ms // 2))

        assert fundamental_magnitude > 0.8
        assert harmonic_magnitude > 0.5


@pytest.mark.unit
class TestDynamicHarmonicSupport:
    def test_dynamic_eval_finds_harmonic_support_missing_from_grid(self):
        period_ms = 9_000
        harmonic_period_ms = period_ms / 2
        timestamps = make_periodic_timestamps_ms(period_ms, count=12)

        all_points = [(8_000.0, 0.02), (9_000.0, 1.0), (10_000.0, 0.02)]
        median = 0.02

        static_support = _harmonic_support_at_period(
            all_points,
            harmonic_period_ms,
            median,
            None,
            use_dynamic_eval=False,
            harmonic_tolerance_ratio=0.05,
        )
        dynamic_support = _harmonic_support_at_period(
            all_points,
            harmonic_period_ms,
            median,
            timestamps,
            use_dynamic_eval=True,
            harmonic_tolerance_ratio=0.05,
        )

        assert static_support < 0.1
        assert dynamic_support > 0.5

        candidate_points = [(float(period_ms), 1.0)]
        dynamic_result = filter_by_harmony(
            candidate_points,
            all_points,
            threshold=0.8,
            required_peak_count=1,
            median=median,
            timestamps=timestamps,
            use_dynamic_eval=True,
        )
        static_result = filter_by_harmony(
            candidate_points,
            all_points,
            threshold=0.8,
            required_peak_count=1,
            median=median,
            timestamps=None,
            use_dynamic_eval=False,
        )

        assert dynamic_result == candidate_points
        assert static_result == []

    def test_ninety_to_forty_five_minute_style_harmonic_check(self):
        period_ms = 90 * 60_000
        timestamps = make_periodic_timestamps_ms(period_ms, count=10)

        coarse_periods = [80 * 60_000.0, 90 * 60_000.0, 100 * 60_000.0]
        _xs, magnitudes = fourier_transform(timestamps, period_candidates_ms=coarse_periods)
        all_points = list(zip(coarse_periods, magnitudes))
        median = get_median_value(magnitudes)
        candidate = next(point for point in all_points if point[0] == period_ms)

        result = filter_by_harmony(
            [candidate],
            all_points,
            threshold=0.8,
            required_peak_count=1,
            median=median,
            timestamps=timestamps,
            use_dynamic_eval=True,
        )

        assert result == [candidate]


@pytest.mark.unit
class TestGhostSuppression:
    def test_long_to_short_processing_suppresses_matching_harmonic(self):
        long_alert = AlertCore(period_ts=30_000.0, phase=0.0, confidence=90)
        short_alert = AlertCore(period_ts=15_000.0, phase=0.0, confidence=80)

        result = suppress_harmonic_ghost_alerts([short_alert, long_alert])

        assert len(result) == 1
        assert result[0].period_ts == 30_000.0

    def test_phase_mismatch_preserves_shorter_peak_when_phase_check_enabled(self, override_harmonic_config):
        override_harmonic_config(
            ghost_suppression_enabled=True,
            phase_ghost_suppression_enabled=True,
            phase_similarity_threshold=0.9,
        )

        long_alert = AlertCore(period_ts=30_000.0, phase=0.0, confidence=90)
        short_alert = AlertCore(period_ts=15_000.0, phase=math.pi, confidence=80)

        result = suppress_harmonic_ghost_alerts([long_alert, short_alert])

        assert len(result) == 2
        assert {alert.period_ts for alert in result} == {30_000.0, 15_000.0}

    def test_suppression_order_is_deterministic_long_to_short(self):
        alerts = [
            AlertCore(period_ts=10_000.0, phase=0.0, confidence=70),
            AlertCore(period_ts=30_000.0, phase=0.0, confidence=90),
            AlertCore(period_ts=15_000.0, phase=0.0, confidence=80),
        ]

        result = suppress_harmonic_ghost_alerts(alerts)

        assert [alert.period_ts for alert in result] == [30_000.0]
