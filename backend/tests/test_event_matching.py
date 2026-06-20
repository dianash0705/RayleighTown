import math

import pytest

from event_matching import (
    circular_distance_ms,
    match_confidence_from_distance,
    match_events_to_alert,
    match_distance_ms,
    nearest_tick_distance_ms,
    periods_near_match,
    phase_offset_ms,
    refine_matching_period_ms,
    series_spacing_distance_ms,
)


@pytest.mark.unit
class TestEventMatchingHelpers:
    def test_circular_distance_is_shortest_arc_on_period(self):
        period_ms = 60_000.0
        phase_ms = 3_000.0

        assert circular_distance_ms(3_000, period_ms, phase_ms) == 0
        assert circular_distance_ms(63_000, period_ms, phase_ms) == 0
        assert circular_distance_ms(123_000, period_ms, phase_ms) == 0
        assert circular_distance_ms(57_000, period_ms, phase_ms) == 6_000
        assert circular_distance_ms(30_000, period_ms, phase_ms) == 27_000

    def test_match_confidence_is_hundred_at_zero_distance(self):
        assert match_confidence_from_distance(0, 500) == 100

    def test_match_confidence_falls_off_with_distance(self):
        near = match_confidence_from_distance(100, 500)
        far = match_confidence_from_distance(2_000, 500)
        assert near > far

    def test_periods_near_match_groups_31_and_33_second_aliases(self):
        assert periods_near_match(31_000.0, 33_000.0)
        assert periods_near_match(31_500.0, 31_000.0)
        assert not periods_near_match(31_000.0, 45_000.0)


@pytest.mark.unit
class TestMatchEventsToAlert:
    def test_matches_periodic_events_at_expected_ticks(self):
        period_ms = 60_000.0
        phase_ms = 3_000.0
        phase_rad = (phase_ms / period_ms) * (2 * math.pi)

        events = [
            (1, 3_000),
            (2, 63_000),
            (3, 123_000),
            (4, 30_000),
        ]

        result = match_events_to_alert(
            events,
            ts_begin_ms=0,
            ts_end_ms=180_000,
            period_ms=period_ms,
            phase_rad=phase_rad,
            min_confidence=25,
            sigma_ms=500,
        )

        matched = result.matched_events
        matched_ids = {item.internal_event_id for item in matched}
        assert matched_ids == {1, 2, 3}
        assert all(item.confidence >= 90 for item in matched if item.internal_event_id in {1, 2, 3})

    def test_excludes_events_outside_window(self):
        period_ms = 60_000.0
        phase_ms = 3_000.0
        phase_rad = (phase_ms / period_ms) * (2 * math.pi)

        result = match_events_to_alert(
            [(1, 3_000), (2, 963_000)],
            ts_begin_ms=0,
            ts_end_ms=120_000,
            period_ms=period_ms,
            phase_rad=phase_rad,
        )

        assert [item.internal_event_id for item in result.matched_events] == [1]

    def test_respects_configurable_threshold(self):
        period_ms = 60_000.0
        phase_ms = 3_000.0
        phase_rad = (phase_ms / period_ms) * (2 * math.pi)

        result = match_events_to_alert(
            [(1, 3_000), (2, 63_000), (3, 8_000)],
            ts_begin_ms=0,
            ts_end_ms=120_000,
            period_ms=period_ms,
            phase_rad=phase_rad,
            min_confidence=95,
            sigma_ms=500,
        )

        matched_ids = {item.internal_event_id for item in result.matched_events}
        assert matched_ids == {1, 2}

    def test_phase_offset_conversion(self):
        period_ms = 60_000.0
        phase_rad = math.pi / 10
        assert phase_offset_ms(phase_rad, period_ms) == pytest.approx(3_000.0)

    def test_nearest_tick_matches_circular_for_same_phase(self):
        period_ms = 60_000.0
        phase_ms = 3_000.0
        timestamp_ms = 123_000.0
        assert nearest_tick_distance_ms(timestamp_ms, period_ms, phase_ms) == pytest.approx(
            circular_distance_ms(timestamp_ms, period_ms, phase_ms),
        )

    def test_refine_period_nudges_toward_observed_spacing(self):
        timestamps = [0, 30_000, 60_000, 90_000, 120_000]
        refined = refine_matching_period_ms(timestamps, 29_000.0)
        assert refined == pytest.approx(30_000.0)

    def test_evenly_spaced_series_keeps_stable_match_scores(self):
        period_ms = 30_000.0
        phase_rad = 0.0
        events = [(index + 1, index * 30_000) for index in range(8)]

        result = match_events_to_alert(
            events,
            ts_begin_ms=0,
            ts_end_ms=240_000,
            period_ms=29_000.0,
            phase_rad=phase_rad,
            min_confidence=25,
            sigma_ms=500,
        )

        matched = result.matched_events
        assert len(matched) >= 6
        confidences = [item.confidence for item in matched]
        assert max(confidences) - min(confidences) <= 5

    def test_observed_period_overrides_coarse_fourier_hint(self):
        period_ms = 31_500.0
        events = [(index + 1, index * period_ms) for index in range(7)]

        result = match_events_to_alert(
            events,
            ts_begin_ms=0,
            ts_end_ms=int(7 * period_ms),
            period_ms=33_000.0,
            phase_rad=0.0,
            min_confidence=25,
            sigma_ms=500,
        )

        assert len(result.matched_events) == 7
        assert result.observed_period_ms == pytest.approx(31_500.0, rel=0.02)

    def test_series_spacing_limits_drift_from_slightly_wrong_grid_period(self):
        true_period_ms = 31_200.0
        grid_period_ms = 31_000.0
        anchor_ms = 0.0
        timestamps = [index * true_period_ms for index in range(12)]
        prior: list[float] = []

        grid_only = []
        combined = []
        for timestamp_ms in timestamps:
            grid_only.append(
                nearest_tick_distance_ms(timestamp_ms, grid_period_ms, anchor_ms),
            )
            combined.append(
                match_distance_ms(
                    timestamp_ms,
                    period_ms=grid_period_ms,
                    anchor_ms=anchor_ms,
                    prior_timestamps=prior,
                ),
            )
            prior.append(timestamp_ms)

        assert max(grid_only) > max(combined)
        assert max(combined) <= 500.0

    def test_chain_spacing_prefers_local_multiple_of_period(self):
        period_ms = 31_000.0
        prior = [0.0, 31_500.0]
        distance = series_spacing_distance_ms(63_000.0, prior, period_ms)
        assert distance == pytest.approx(0.0, abs=500.0)

    def test_matches_events_at_real_world_timestamps(self):
        period_ms = 31_000.0
        base_ms = 1_700_000_000_000
        events = [(index + 1, int(base_ms + index * period_ms)) for index in range(40)]

        result = match_events_to_alert(
            events,
            ts_begin_ms=events[0][1],
            ts_end_ms=events[-1][1],
            period_ms=period_ms,
            phase_rad=1.25,
            min_confidence=25,
            sigma_ms=500,
        )

        assert len(result.matched_events) == 40
        assert result.observed_period_ms == pytest.approx(31_000.0, rel=0.02)
