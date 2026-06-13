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

    ``events`` is a list of (internal_event_id, timestamp_ms) pairs.
    Expected tick positions use absolute-time mod period (Fourier reference frame A).
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

    expected_offset_ms = phase_offset_ms(phase_rad, period_ms)
    matched: list[MatchedEvent] = []

    for internal_event_id, timestamp_ms in events:
        if timestamp_ms < ts_begin_ms or timestamp_ms > ts_end_ms:
            continue

        distance_ms = circular_distance_ms(float(timestamp_ms), period_ms, expected_offset_ms)
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
