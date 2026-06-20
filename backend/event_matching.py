"""Match log events to periodic alerts using nearest-tick Gaussian scoring."""

from __future__ import annotations

import math
from dataclasses import dataclass

from config import EVENT_MATCHING_CONFIG


@dataclass(frozen=True)
class MatchedEvent:
    internal_event_id: int
    timestamp_ms: int
    confidence: int


def phase_offset_ms(phase_rad: float, period_ms: float) -> float:
    """Convert Fourier phase (radians) to offset within one period cycle."""
    return (phase_rad / (2 * math.pi)) * period_ms


def nearest_tick_distance_ms(
    timestamp_ms: float,
    period_ms: float,
    phase_offset_ms_value: float,
) -> float:
    """Distance from timestamp to the closest absolute-time grid tick."""
    if period_ms <= 0:
        return float("inf")

    cycle_index = round((timestamp_ms - phase_offset_ms_value) / period_ms)
    nearest_tick_ms = phase_offset_ms_value + cycle_index * period_ms
    return abs(timestamp_ms - nearest_tick_ms)


def circular_distance_ms(
    timestamp_ms: float,
    period_ms: float,
    phase_offset_ms_value: float,
) -> float:
    """Shortest distance on a circle between timestamp mod period and expected phase."""
    position_ms = timestamp_ms % period_ms
    target_ms = phase_offset_ms_value % period_ms
    direct_distance = abs(position_ms - target_ms)
    return min(direct_distance, period_ms - direct_distance)


def match_confidence_from_distance(distance_ms: float, sigma_ms: float) -> int:
    if sigma_ms <= 0:
        return 100 if distance_ms == 0 else 0

    score = math.exp(-(distance_ms**2) / (2 * sigma_ms**2))
    return max(0, min(100, int(round(score * 100))))


def _median_positive_gap_ms(timestamps: list[int]) -> float:
    unique_sorted = sorted({int(timestamp) for timestamp in timestamps})
    if len(unique_sorted) < 2:
        return 0.0

    gaps = [
        unique_sorted[index + 1] - unique_sorted[index]
        for index in range(len(unique_sorted) - 1)
        if unique_sorted[index + 1] > unique_sorted[index]
    ]
    if not gaps:
        return 0.0

    gaps.sort()
    middle = len(gaps) // 2
    if len(gaps) % 2 == 0:
        return (gaps[middle - 1] + gaps[middle]) / 2.0
    return float(gaps[middle])


def refine_matching_period_ms(
    timestamps: list[int],
    period_ms: float,
) -> float:
    """
    Nudge the Fourier period toward observed median spacing when they disagree slightly.

    Reduces drift in match scores when the true cadence is close to but not exactly
    the detected period (e.g. 30s events scored against a 29s grid).
    """
    config = EVENT_MATCHING_CONFIG
    if len(timestamps) < 2 or not math.isfinite(period_ms) or period_ms <= 0:
        return period_ms

    median_gap_ms = _median_positive_gap_ms(timestamps)
    if median_gap_ms <= 0:
        return period_ms

    multiplier = max(1, round(median_gap_ms / period_ms))
    candidate_period_ms = median_gap_ms / multiplier
    if abs(candidate_period_ms - period_ms) / period_ms <= config.period_refinement_max_ratio_delta:
        return candidate_period_ms

    if multiplier == 1 and abs(median_gap_ms - period_ms) / period_ms <= config.period_refinement_max_ratio_delta:
        return median_gap_ms

    return period_ms


def match_events_to_alert(
    events: list[tuple[int, int]],
    *,
    ts_begin_ms: int,
    ts_end_ms: int,
    period_ms: float,
    phase_rad: float,
    min_confidence: int | None = None,
    sigma_ms: float | None = None,
) -> tuple[MatchedEvent, ...]:
    """
    Score events inside [ts_begin_ms, ts_end_ms] against a periodic grid.

    Uses nearest absolute-time ticks (not cumulative drift from the first event).
    When enough events are present, the period is lightly refined from median spacing
    before scoring so evenly spaced series keep stable match percentages.
    """
    config = EVENT_MATCHING_CONFIG
    threshold = config.min_match_confidence if min_confidence is None else min_confidence
    jitter_sigma = config.jitter_sigma_ms if sigma_ms is None else sigma_ms

    if (
        not math.isfinite(period_ms)
        or period_ms <= 0
        or not math.isfinite(phase_rad)
        or ts_begin_ms > ts_end_ms
    ):
        return ()

    in_window = [
        (internal_event_id, timestamp_ms)
        for internal_event_id, timestamp_ms in events
        if ts_begin_ms <= timestamp_ms <= ts_end_ms
    ]
    if not in_window:
        return ()

    scoring_period_ms = refine_matching_period_ms(
        [timestamp_ms for _, timestamp_ms in in_window],
        period_ms,
    )
    expected_offset_ms = phase_offset_ms(phase_rad, scoring_period_ms)
    matched: list[MatchedEvent] = []

    for internal_event_id, timestamp_ms in in_window:
        distance_ms = nearest_tick_distance_ms(
            float(timestamp_ms),
            scoring_period_ms,
            expected_offset_ms,
        )
        confidence = match_confidence_from_distance(distance_ms, jitter_sigma)
        if confidence < threshold:
            continue

        matched.append(
            MatchedEvent(
                internal_event_id=internal_event_id,
                timestamp_ms=timestamp_ms,
                confidence=confidence,
            )
        )

    matched.sort(key=lambda item: (item.timestamp_ms, item.internal_event_id))
    return tuple(matched)
