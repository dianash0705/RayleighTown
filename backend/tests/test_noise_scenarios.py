"""Noise-heavy scenarios — extend this module as more cases are added."""

import pytest

from fourier import evaluate_period, filter_by_harmony, fourier_transform, get_median_value
from tests.helpers import NoiseProfile, make_mixed_timestamps_ms


@pytest.mark.noise
class TestNoiseScenarios:
    def test_uniform_noise_still_detects_fundamental_with_dynamic_harmonics(self):
        period_ms = 9_000
        timestamps = make_mixed_timestamps_ms(
            period_ms,
            primary_count=24,
            noise=NoiseProfile(uniform_event_count=30, seed=11),
        )

        coarse_periods = [8_000.0, 9_000.0, 10_000.0]
        _xs, magnitudes = fourier_transform(timestamps, period_candidates_ms=coarse_periods)
        all_points = list(zip(coarse_periods, magnitudes))
        median = get_median_value(magnitudes)
        candidate = next(point for point in all_points if point[0] == period_ms)

        fundamental_magnitude, _phase = evaluate_period(timestamps, float(period_ms))
        assert fundamental_magnitude > median

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

    def test_jittered_periodic_signal_still_has_measurable_fundamental(self):
        period_ms = 12_000
        timestamps = make_mixed_timestamps_ms(
            period_ms,
            primary_count=20,
            noise=NoiseProfile(jitter_ms=500, seed=19),
        )

        magnitude, _phase = evaluate_period(timestamps, float(period_ms))

        assert magnitude > 0.4
