import math

import pytest

from event_matching import (
    circular_distance_ms,
    match_confidence_from_distance,
    match_events_to_alert,
    nearest_tick_distance_ms,
    phase_offset_ms,
    refine_matching_period_ms,
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

        matched = match_events_to_alert(
            events,
            ts_begin_ms=0,
            ts_end_ms=180_000,
            period_ms=period_ms,
            phase_rad=phase_rad,
            min_confidence=25,
            sigma_ms=500,
        )

        matched_ids = {item.internal_event_id for item in matched}
        assert matched_ids == {1, 2, 3}
        assert all(item.confidence >= 90 for item in matched if item.internal_event_id in {1, 2, 3})

    def test_excludes_events_outside_window(self):
        period_ms = 60_000.0
        phase_ms = 3_000.0
        phase_rad = (phase_ms / period_ms) * (2 * math.pi)

        matched = match_events_to_alert(
            [(1, 3_000), (2, 963_000)],
            ts_begin_ms=0,
            ts_end_ms=120_000,
            period_ms=period_ms,
            phase_rad=phase_rad,
        )

        assert [item.internal_event_id for item in matched] == [1]

    def test_respects_configurable_threshold(self):
        period_ms = 60_000.0
        phase_ms = 3_000.0
        phase_rad = (phase_ms / period_ms) * (2 * math.pi)

        matched = match_events_to_alert(
            [(1, 8_000)],
            ts_begin_ms=0,
            ts_end_ms=120_000,
            period_ms=period_ms,
            phase_rad=phase_rad,
            min_confidence=95,
            sigma_ms=500,
        )

        assert matched == ()

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

        matched = match_events_to_alert(
            events,
            ts_begin_ms=0,
            ts_end_ms=240_000,
            period_ms=29_000.0,
            phase_rad=phase_rad,
            min_confidence=25,
            sigma_ms=500,
        )

        assert len(matched) >= 6
        confidences = [item.confidence for item in matched]
        assert max(confidences) - min(confidences) <= 5
