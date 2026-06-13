"""Unit tests for multi-factor confidence scoring."""

import math

import pytest

from confidence_scoring import (
    WindowConfidenceBreakdown,
    build_window_snapshot,
    compute_group_confidence,
    compute_snr_bonus,
    compute_window_confidence,
)
from fourier import evaluate_period, fourier_transform, get_median_value
from tests.helpers import make_periodic_timestamps_ms


def _window_breakdown(confidence: int, base_peak: float = 56.0) -> WindowConfidenceBreakdown:
    return WindowConfidenceBreakdown(
        peak_magnitude=0.8,
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


@pytest.mark.unit
class TestSnrBonus:
    def test_snr_bonus_is_zero_at_filter_threshold(self):
        _, bonus = compute_snr_bonus(0.5, median=0.2, mad=0.1, snr_threshold=3.0)
        assert bonus == pytest.approx(0.0, abs=0.01)

    def test_snr_bonus_increases_above_threshold(self):
        _, low_bonus = compute_snr_bonus(0.5, median=0.2, mad=0.1, snr_threshold=3.0)
        _, high_bonus = compute_snr_bonus(0.9, median=0.2, mad=0.1, snr_threshold=3.0)
        assert high_bonus > low_bonus


@pytest.mark.unit
class TestWindowConfidence:
    def test_clean_periodic_signal_scores_higher_base_than_flat_noise(self):
        period_ms = 9_000
        clean = make_periodic_timestamps_ms(period_ms, count=16)
        noisy = [float(index * 17_000) for index in range(16)]

        def score_for(timestamps: list[float]) -> int:
            periods, magnitudes, phases = fourier_transform(timestamps, include_phase=True)
            points = list(zip(periods, magnitudes))
            phase_by_period = dict(zip(periods, phases))
            median = get_median_value(magnitudes)
            distances = [abs(value - median) for value in magnitudes]
            mad = get_median_value(distances)
            candidate = next(point for point in points if point[0] == period_ms)
            breakdown = compute_window_confidence(
                period_ms,
                candidate[1],
                median,
                mad,
                points,
                timestamps,
                phase_by_period,
            )
            return breakdown.window_confidence

        assert score_for(clean) > score_for(noisy)

    def test_harmonic_rich_signal_gets_higher_confidence_than_single_peak_shape(self):
        period_ms = 9_000
        timestamps = make_periodic_timestamps_ms(period_ms, count=20)
        periods, magnitudes, phases = fourier_transform(timestamps, include_phase=True)
        points = list(zip(periods, magnitudes))
        phase_by_period = dict(zip(periods, phases))
        median = get_median_value(magnitudes)
        mad = get_median_value([abs(value - median) for value in magnitudes])
        candidate = next(point for point in points if point[0] == period_ms)

        breakdown = compute_window_confidence(
            period_ms,
            candidate[1],
            median,
            mad,
            points,
            timestamps,
            phase_by_period,
        )

        assert breakdown.harmonic_bonus > 0
        assert breakdown.phase_bonus > 0
        assert breakdown.window_confidence >= 60


@pytest.mark.unit
class TestGroupConfidence:
    def test_more_windows_raise_group_confidence(self):
        single = compute_group_confidence(
            [
                build_window_snapshot(
                    ts_begin=0,
                    ts_end=10_000,
                    period_ts=60_000.0,
                    phase=0.0,
                    breakdown=_window_breakdown(45),
                )
            ]
        )
        many = compute_group_confidence(
            [
                build_window_snapshot(
                    ts_begin=index * 450_000,
                    ts_end=index * 450_000 + 900_000,
                    period_ts=60_000.0,
                    phase=0.1 * index,
                    breakdown=_window_breakdown(40 + index),
                )
                for index in range(6)
            ]
        )

        assert many.final_confidence > single.final_confidence
        assert many.count_bonus > 0

    def test_consecutive_windows_add_streak_bonus_after_third_window(self):
        two_in_a_row = compute_group_confidence(
            [
                build_window_snapshot(
                    ts_begin=index * 450_000,
                    ts_end=index * 450_000 + 900_000,
                    period_ts=60_000.0,
                    phase=0.0,
                    breakdown=_window_breakdown(50),
                )
                for index in range(2)
            ]
        )
        four_in_a_row = compute_group_confidence(
            [
                build_window_snapshot(
                    ts_begin=index * 450_000,
                    ts_end=index * 450_000 + 900_000,
                    period_ts=60_000.0,
                    phase=0.0,
                    breakdown=_window_breakdown(50),
                )
                for index in range(4)
            ]
        )

        assert two_in_a_row.streak_bonus == 0.0
        assert four_in_a_row.longest_streak >= 3
        assert four_in_a_row.streak_bonus > two_in_a_row.streak_bonus
        assert four_in_a_row.final_confidence > two_in_a_row.final_confidence

    def test_aligned_phases_add_consistency_bonus(self):
        aligned = compute_group_confidence(
            [
                build_window_snapshot(
                    ts_begin=index * 450_000,
                    ts_end=index * 450_000 + 900_000,
                    period_ts=60_000.0,
                    phase=0.2,
                    breakdown=_window_breakdown(48),
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
                    breakdown=_window_breakdown(48),
                )
                for index, phase in enumerate([0.0, math.pi / 2, math.pi, 3 * math.pi / 2])
            ]
        )

        assert aligned.phase_consistency_bonus > misaligned.phase_consistency_bonus


@pytest.mark.unit
class TestSingleWindowPenalty:
    def test_weak_single_window_is_penalized_and_capped(self):
        lone = compute_group_confidence(
            [
                build_window_snapshot(
                    ts_begin=0,
                    ts_end=900_000,
                    period_ts=60_000.0,
                    phase=0.0,
                    breakdown=_window_breakdown(74, base_peak=50.0),
                )
            ]
        )

        assert lone.window_count == 1
        assert lone.final_confidence <= 62

    def test_strong_single_window_still_gets_penalty_but_can_remain_useful(self):
        lone = compute_group_confidence(
            [
                build_window_snapshot(
                    ts_begin=0,
                    ts_end=900_000,
                    period_ts=60_000.0,
                    phase=0.0,
                    breakdown=_window_breakdown(92, base_peak=88.0),
                )
            ]
        )

        assert lone.final_confidence < 92
        assert lone.final_confidence >= 75


@pytest.mark.unit
class TestEvaluatePeriodPhaseSupport:
    def test_periodic_signal_has_measurable_phase_at_fundamental_and_half_period(self):
        period_ms = 12_000
        timestamps = make_periodic_timestamps_ms(period_ms, count=18)

        _fundamental_magnitude, fundamental_phase = evaluate_period(timestamps, float(period_ms))
        _half_magnitude, half_phase = evaluate_period(timestamps, float(period_ms / 2))

        assert math.cos(fundamental_phase - half_phase) > 0.5
