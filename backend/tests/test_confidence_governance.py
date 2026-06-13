"""Tests for confidence governance: shape caps, phase penalties, streak gates."""

import math

import pytest

from brain import build_alerts_from_sorted_timestamps_ms
from confidence_scoring import (
    WindowConfidenceBreakdown,
    build_window_snapshot,
    compute_group_confidence,
    compute_window_confidence,
)
from fourier import fourier_transform, get_median_value
from tests.helpers import NoiseProfile, make_mixed_timestamps_ms, make_periodic_timestamps_ms


def _window_breakdown(confidence: int, base_peak: float = 50.0) -> WindowConfidenceBreakdown:
    return WindowConfidenceBreakdown(
        peak_magnitude=base_peak / 70.0,
        median=0.05,
        mad=0.02,
        snr=5.0,
        base_peak=base_peak,
        snr_bonus=5.0,
        harmonic_bonus=3.0,
        phase_bonus=2.0,
        window_score=float(confidence),
        window_confidence=confidence,
    )


@pytest.fixture(autouse=True)
def _disable_confidence_file_logging(override_confidence_config):
    override_confidence_config(confidence_logging_enabled=False)


@pytest.mark.unit
class TestShapeBonusGovernance:
    def test_weak_base_caps_harmonic_and_phase_bonuses(self):
        period_ms = 9_000
        timestamps = make_periodic_timestamps_ms(period_ms, count=6)
        periods, magnitudes, phases = fourier_transform(timestamps, include_phase=True)
        points = list(zip(periods, magnitudes))
        phase_by_period = dict(zip(periods, phases))
        median = get_median_value(magnitudes)
        mad = get_median_value([abs(value - median) for value in magnitudes])

        breakdown = compute_window_confidence(
            period_ms,
            0.12,
            median,
            mad,
            points,
            timestamps,
            phase_by_period,
        )

        if breakdown.base_peak < 48.0:
            assert breakdown.shape_bonus_capped or (
                breakdown.harmonic_bonus + breakdown.phase_bonus
                <= breakdown.base_peak * 0.5 + 0.01
            )


@pytest.mark.unit
class TestGroupGovernance:
    def test_streak_bonus_requires_at_least_three_windows(self):
        two_window_group = compute_group_confidence(
            [
                build_window_snapshot(
                    ts_begin=0,
                    ts_end=900_000,
                    period_ts=60_000.0,
                    phase=0.0,
                    breakdown=_window_breakdown(70),
                ),
                build_window_snapshot(
                    ts_begin=450_000,
                    ts_end=1_350_000,
                    period_ts=60_000.0,
                    phase=0.0,
                    breakdown=_window_breakdown(72),
                ),
            ]
        )
        three_window_group = compute_group_confidence(
            [
                build_window_snapshot(
                    ts_begin=index * 450_000,
                    ts_end=index * 450_000 + 900_000,
                    period_ts=60_000.0,
                    phase=0.0,
                    breakdown=_window_breakdown(70),
                )
                for index in range(3)
            ]
        )

        assert two_window_group.streak_bonus == 0.0
        assert three_window_group.streak_bonus > 0.0

    def test_low_phase_similarity_applies_penalty_and_reduces_count_bonus(self):
        aligned = compute_group_confidence(
            [
                build_window_snapshot(
                    ts_begin=index * 450_000,
                    ts_end=index * 450_000 + 900_000,
                    period_ts=60_000.0,
                    phase=0.1,
                    breakdown=_window_breakdown(80, base_peak=60.0),
                )
                for index in range(4)
            ]
        )
        misaligned = compute_group_confidence(
            [
                build_window_snapshot(
                    ts_begin=index * 450_000,
                    ts_end=index * 450_000 + 900_000,
                    period_ts=60_000.0,
                    phase=phase,
                    breakdown=_window_breakdown(80, base_peak=60.0),
                )
                for index, phase in enumerate([0.0, math.pi / 2, math.pi, 3 * math.pi / 2])
            ]
        )

        assert misaligned.phase_penalty > aligned.phase_penalty
        assert misaligned.count_bonus <= aligned.count_bonus
        assert misaligned.final_confidence < aligned.final_confidence


@pytest.mark.noise
class TestNoiseGovernanceIntegration:
    def test_pure_spread_noise_stays_below_high_confidence(self):
        timestamps = make_mixed_timestamps_ms(
            60_000,
            primary_count=0,
            noise=NoiseProfile(
                uniform_event_count=120,
                uniform_range_ms=(0, 6 * 60 * 60_000),
                seed=41,
            ),
        )
        alerts = build_alerts_from_sorted_timestamps_ms(
            [int(timestamp) for timestamp in timestamps],
            endpoint_id="noise-governance",
            native_event_id=99,
        )

        if not alerts:
            return

        group_confidences = [
            alert.confidence
            for alert in alerts
            if alert.confidence_breakdown and alert.confidence_breakdown.group
        ]
        if group_confidences:
            assert max(group_confidences) <= 72

        assert max(alert.confidence for alert in alerts) <= 75

    def test_strong_periodic_signal_can_still_reach_high_confidence(self):
        timestamps = make_periodic_timestamps_ms(60_000, count=180)
        alerts = build_alerts_from_sorted_timestamps_ms(
            [int(timestamp) for timestamp in timestamps],
            endpoint_id="clean-governance",
            native_event_id=1,
        )

        assert alerts is not None
        assert max(alert.confidence for alert in alerts) >= 90
