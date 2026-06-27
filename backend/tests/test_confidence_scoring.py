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


@pytest.mark.unit
class TestEvidenceSufficiency:
    def test_sparse_two_event_window_is_heavily_penalized(self):
        from confidence_scoring import (
            apply_evidence_penalty,
            compute_evidence_sufficiency_penalty,
            should_publish_alert,
        )

        penalty = compute_evidence_sufficiency_penalty(
            period_ms=29_000.0,
            ts_begin_ms=0,
            ts_end_ms=9 * 60_000,
            matched_count=2,
            timestamps=[0.0, 9 * 60_000.0],
        )
        final_confidence = apply_evidence_penalty(72, penalty)

        assert penalty >= 30.0
        assert not should_publish_alert(
            final_confidence,
            events_in_window=2,
            matched_count=2,
            spacing_score=0.1,
        )

    def test_two_grid_matches_with_strong_spacing_do_not_publish(self):
        from confidence_scoring import cap_confidence_by_match_evidence, should_publish_alert

        period_ms = 38_333.0
        ts_begin_ms = 0
        ts_end_ms = 600_000
        matched = [299_987.0, 338_320.0]
        capped = cap_confidence_by_match_evidence(
            72,
            matched_count=2,
            period_ms=period_ms,
            ts_begin_ms=ts_begin_ms,
            ts_end_ms=ts_end_ms,
            matched_timestamps=matched,
        )

        assert capped == 42
        assert not should_publish_alert(
            capped,
            events_in_window=6,
            matched_count=2,
            spacing_score=0.95,
            period_ms=period_ms,
            ts_begin_ms=ts_begin_ms,
            ts_end_ms=ts_end_ms,
            matched_timestamps=matched,
        )

    def test_dense_periodic_window_passes_publish_gate(self):
        from confidence_scoring import compute_evidence_sufficiency_penalty, should_publish_alert

        timestamps = [float(index * 30_000) for index in range(8)]
        penalty = compute_evidence_sufficiency_penalty(
            period_ms=30_000.0,
            ts_begin_ms=0,
            ts_end_ms=240_000,
            matched_count=8,
            timestamps=timestamps,
        )
        assert penalty < 10.0
        assert should_publish_alert(
            70 - int(penalty),
            events_in_window=8,
            matched_count=8,
            spacing_score=0.95,
        )

    def test_jittery_long_period_with_grid_matches_skips_spacing_penalty(self):
        from confidence_scoring import (
            apply_evidence_penalty,
            compute_evidence_sufficiency_penalty,
            should_publish_alert,
        )

        period_ms = 48_500.0
        timestamps = [
            float(index * period_ms + ((index % 5) - 2) * period_ms * 0.2)
            for index in range(168)
        ]
        penalty = compute_evidence_sufficiency_penalty(
            period_ms=period_ms,
            ts_begin_ms=int(timestamps[0]),
            ts_end_ms=int(timestamps[-1]),
            matched_count=12,
            timestamps=timestamps,
            match_spacing_score=0.35,
        )
        final_confidence = apply_evidence_penalty(21, penalty)

        assert penalty < 5.0
        assert final_confidence == 21
        assert should_publish_alert(
            final_confidence,
            events_in_window=len(timestamps),
            matched_count=12,
            spacing_score=0.0,
        )

    def test_sparse_window_still_penalized_without_grid_matches(self):
        from confidence_scoring import compute_evidence_sufficiency_penalty

        penalty = compute_evidence_sufficiency_penalty(
            period_ms=48_500.0,
            ts_begin_ms=0,
            ts_end_ms=600_000,
            matched_count=2,
            timestamps=[0.0, 600_000.0],
            match_spacing_score=0.0,
        )

        assert penalty >= 18.0


@pytest.mark.unit
class TestGridCorroboration:
    def test_jittery_fundamental_with_strong_grid_match_is_corroborated(self):
        from confidence_scoring import apply_grid_corroboration, should_publish_alert

        period_ms = 48_500.0
        timestamps = [
            float(index * period_ms + ((index % 5) - 2) * period_ms * 0.2)
            for index in range(168)
        ]
        corroborated = apply_grid_corroboration(
            3,
            window_confidence=3,
            matched_count=17,
            match_spacing_score=0.86,
            period_ms=period_ms,
            timestamps=timestamps,
        )

        assert 35 <= corroborated <= 55
        assert should_publish_alert(
            corroborated,
            events_in_window=len(timestamps),
            matched_count=17,
            spacing_score=0.0,
        )

    def test_long_period_alias_is_not_corroborated_without_spacing_support(self):
        from confidence_scoring import apply_grid_corroboration

        period_ms = 1_380_000.0
        short_period_ms = 48_500.0
        timestamps = [
            float(index * short_period_ms + ((index % 5) - 2) * short_period_ms * 0.2)
            for index in range(200)
        ]
        corroborated = apply_grid_corroboration(
            13,
            window_confidence=13,
            matched_count=55,
            match_spacing_score=0.86,
            period_ms=period_ms,
            timestamps=timestamps,
            matched_timestamps=timestamps,
        )

        assert corroborated == 13

    def test_corroboration_ignores_non_periodic_noise_in_window(self):
        from confidence_scoring import apply_grid_corroboration, should_publish_alert

        period_ms = 48_500.0
        matched_timestamps = [
            float(index * period_ms + ((index % 5) - 2) * period_ms * 0.2)
            for index in range(20)
        ]
        noisy_window = sorted(set(matched_timestamps + [float(i * 5_000) for i in range(220)]))
        corroborated = apply_grid_corroboration(
            9,
            window_confidence=9,
            matched_count=17,
            match_spacing_score=0.86,
            period_ms=period_ms,
            timestamps=noisy_window,
            matched_timestamps=matched_timestamps,
        )

        assert 35 <= corroborated <= 55
        assert should_publish_alert(
            corroborated,
            events_in_window=len(noisy_window),
            matched_count=17,
            spacing_score=0.0,
        )

        without_matched_spacing = apply_grid_corroboration(
            9,
            window_confidence=9,
            matched_count=17,
            match_spacing_score=0.86,
            period_ms=period_ms,
            timestamps=noisy_window,
        )
        assert without_matched_spacing == 9

    def test_strong_fourier_window_is_not_overridden(self):
        from confidence_scoring import apply_grid_corroboration

        timestamps = [float(index * 30_000) for index in range(12)]
        corroborated = apply_grid_corroboration(
            95,
            window_confidence=95,
            matched_count=12,
            match_spacing_score=0.92,
            period_ms=30_000.0,
            timestamps=timestamps,
        )

        assert corroborated == 95


@pytest.mark.unit
class TestInWindowConsistencyBonus:
    def _six_hour_span(self) -> tuple[int, int]:
        span_ms = 6 * 60 * 60_000
        return 0, span_ms

    def test_dense_five_minute_series_scores_higher_than_sparse_hourly(self):
        from confidence_scoring import (
            apply_in_window_consistency_bonus,
            compute_in_window_consistency_score,
        )

        ts_begin_ms, ts_end_ms = self._six_hour_span()
        period_5m = 300_000.0
        matched_5m = [float(index * period_5m) for index in range(72)]
        period_1h = 3_600_000.0
        matched_1h = [float(index * period_1h) for index in range(6)]

        score_5m = compute_in_window_consistency_score(
            matched_count=len(matched_5m),
            period_ms=period_5m,
            ts_begin_ms=ts_begin_ms,
            ts_end_ms=ts_end_ms,
            match_spacing_score=0.95,
            matched_timestamps=matched_5m,
        )
        score_1h = compute_in_window_consistency_score(
            matched_count=len(matched_1h),
            period_ms=period_1h,
            ts_begin_ms=ts_begin_ms,
            ts_end_ms=ts_end_ms,
            match_spacing_score=0.92,
            matched_timestamps=matched_1h,
        )

        assert score_5m > score_1h
        boosted_5m = apply_in_window_consistency_bonus(
            62,
            matched_count=len(matched_5m),
            period_ms=period_5m,
            ts_begin_ms=ts_begin_ms,
            ts_end_ms=ts_end_ms,
            match_spacing_score=0.95,
            matched_timestamps=matched_5m,
        )
        boosted_1h = apply_in_window_consistency_bonus(
            62,
            matched_count=len(matched_1h),
            period_ms=period_1h,
            ts_begin_ms=ts_begin_ms,
            ts_end_ms=ts_end_ms,
            match_spacing_score=0.92,
            matched_timestamps=matched_1h,
        )

        assert boosted_5m > boosted_1h
        assert boosted_5m >= 88
        assert boosted_1h <= 85

    def test_sparse_matches_get_no_bonus(self):
        from confidence_scoring import apply_in_window_consistency_bonus

        period_ms = 38_333.0
        matched = [299_987.0, 338_320.0]
        assert apply_in_window_consistency_bonus(
            42,
            matched_count=2,
            period_ms=period_ms,
            ts_begin_ms=0,
            ts_end_ms=600_000,
            match_spacing_score=0.95,
            matched_timestamps=matched,
        ) == 42

    def test_high_confidence_is_not_capped_down(self):
        from confidence_scoring import apply_in_window_consistency_bonus

        ts_begin_ms, ts_end_ms = self._six_hour_span()
        period_ms = 31_139.0
        matched = [float(index * period_ms) for index in range(200)]
        assert apply_in_window_consistency_bonus(
            100,
            matched_count=len(matched),
            period_ms=period_ms,
            ts_begin_ms=ts_begin_ms,
            ts_end_ms=ts_end_ms,
            match_spacing_score=0.95,
            matched_timestamps=matched,
        ) == 100


@pytest.mark.unit
class TestPublishQualityGates:
    def test_long_period_alert_with_three_matches_is_rejected(self):
        from confidence_scoring import cap_confidence_by_match_evidence, should_publish_alert

        period_ms = 3_420_000.0  # 57 minutes
        matched = [0.0, 3_420_000.0, 6_840_000.0]
        capped = cap_confidence_by_match_evidence(
            86,
            matched_count=3,
            period_ms=period_ms,
            ts_begin_ms=0,
            ts_end_ms=int(7_000_000),
            matched_timestamps=matched,
        )

        assert capped <= 42
        assert not should_publish_alert(
            capped,
            events_in_window=20,
            matched_count=3,
            spacing_score=0.9,
            period_ms=period_ms,
            ts_begin_ms=0,
            ts_end_ms=int(7_000_000),
            matched_timestamps=matched,
        )

    def test_short_period_with_solid_matches_still_publishes(self):
        from confidence_scoring import should_publish_alert

        period_ms = 31_000.0
        matched = [float(index * period_ms) for index in range(8)]
        assert should_publish_alert(
            86,
            events_in_window=8,
            matched_count=8,
            spacing_score=0.95,
            period_ms=period_ms,
            ts_begin_ms=0,
            ts_end_ms=int(8 * period_ms),
            matched_timestamps=matched,
        )

    def test_sub_min_publish_period_is_rejected(self):
        from confidence_scoring import should_publish_alert

        assert not should_publish_alert(
            95,
            events_in_window=20,
            matched_count=10,
            spacing_score=0.95,
            period_ms=1_000.0,
            ts_begin_ms=0,
            ts_end_ms=60_000,
            matched_timestamps=[float(index * 1_000) for index in range(10)],
        )
