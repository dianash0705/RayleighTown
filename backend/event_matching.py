"""Match log events to periodic alerts using grid + series-spacing scoring."""

from __future__ import annotations

import math
from dataclasses import dataclass

from config import EVENT_MATCHING_CONFIG


@dataclass(frozen=True)
class MatchedEvent:
    internal_event_id: int
    timestamp_ms: int
    confidence: int


@dataclass(frozen=True)
class AlertMatchResult:
    matched_events: tuple[MatchedEvent, ...]
    observed_period_ms: float
    observed_phase_rad: float


def periods_near_match(
    period_a: float,
    period_b: float,
    tolerance_ratio: float | None = None,
) -> bool:
    """True when two period estimates refer to the same cadence within tolerance."""
    if tolerance_ratio is None:
        tolerance_ratio = EVENT_MATCHING_CONFIG.period_merge_tolerance_ratio
    if math.isnan(period_a) or math.isnan(period_b):
        return False
    scale = max(1.0, abs(period_a), abs(period_b))
    return abs(period_a - period_b) <= scale * tolerance_ratio


def phase_offset_ms(phase_rad: float, period_ms: float) -> float:
    """Convert Fourier phase (radians) to offset within one period cycle."""
    return (phase_rad / (2 * math.pi)) * period_ms


def phase_rad_from_anchor_ms(anchor_ms: float, period_ms: float) -> float:
    if period_ms <= 0 or not math.isfinite(anchor_ms):
        return math.nan
    return (2 * math.pi * (anchor_ms % period_ms)) / period_ms


def nearest_tick_distance_ms(
    timestamp_ms: float,
    period_ms: float,
    anchor_ms: float,
) -> float:
    """Distance from timestamp to the closest tick at anchor + n * period."""
    if period_ms <= 0:
        return float("inf")

    cycle_index = round((timestamp_ms - anchor_ms) / period_ms)
    nearest_tick_ms = anchor_ms + cycle_index * period_ms
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


def series_spacing_distance_ms(
    timestamp_ms: float,
    prior_timestamps: list[float],
    period_ms: float,
) -> float:
    """
    Distance from timestamp to the nearest n * period spacing after a prior event.

    Scores locally against earlier events in the series so small period error does not
    accumulate across the grid (e.g. 31.0 vs 31.2 s over many cycles).
    """
    if not prior_timestamps or period_ms <= 0:
        return float("inf")

    best = float("inf")
    for prior_ms in prior_timestamps:
        gap_ms = timestamp_ms - prior_ms
        if gap_ms <= 0:
            continue
        multiplier = max(1, round(gap_ms / period_ms))
        expected_gap_ms = multiplier * period_ms
        best = min(best, abs(gap_ms - expected_gap_ms))
    return best


def match_distance_ms(
    timestamp_ms: float,
    *,
    period_ms: float,
    anchor_ms: float,
    prior_timestamps: list[float],
) -> float:
    grid_distance_ms = nearest_tick_distance_ms(timestamp_ms, period_ms, anchor_ms)
    spacing_distance_ms = series_spacing_distance_ms(timestamp_ms, prior_timestamps, period_ms)
    if not prior_timestamps:
        return grid_distance_ms
    return min(grid_distance_ms, spacing_distance_ms)


def match_confidence_from_distance(distance_ms: float, sigma_ms: float) -> int:
    if sigma_ms <= 0:
        return 100 if distance_ms == 0 else 0

    score = math.exp(-(distance_ms**2) / (2 * sigma_ms**2))
    return max(0, min(100, int(round(score * 100))))


def compute_match_spacing_score(matched_events: tuple[MatchedEvent, ...]) -> float:
    """Mean per-event grid match quality in [0, 1], for jitter-tolerant corroboration."""
    if not matched_events:
        return 0.0
    return sum(event.confidence for event in matched_events) / (len(matched_events) * 100.0)


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


def estimate_series_period_and_anchor_ms(
    timestamps: list[int],
    hint_period_ms: float,
) -> tuple[float, float]:
    """
    Derive cadence period and grid anchor from an observed event series.

    Used after matching so the stored alert period reflects measured spacing, not only
    the coarse Fourier candidate.
    """
    sorted_ts = sorted({int(timestamp) for timestamp in timestamps})
    if not sorted_ts:
        return hint_period_ms, 0.0
    if len(sorted_ts) == 1:
        return refine_matching_period_ms(sorted_ts, hint_period_ms), float(sorted_ts[0])

    period_ms = refine_matching_period_ms(sorted_ts, hint_period_ms)
    offsets_ms = []
    for timestamp_ms in sorted_ts:
        cycle_index = round((timestamp_ms - sorted_ts[0]) / period_ms)
        offsets_ms.append(timestamp_ms - cycle_index * period_ms)

    offsets_ms.sort()
    middle = len(offsets_ms) // 2
    if len(offsets_ms) % 2 == 0:
        anchor_ms = (offsets_ms[middle - 1] + offsets_ms[middle]) / 2.0
    else:
        anchor_ms = float(offsets_ms[middle])
    return period_ms, anchor_ms


def _score_events_in_window(
    in_window: list[tuple[int, int]],
    *,
    period_ms: float,
    anchor_ms: float,
    threshold: int,
    jitter_sigma: float,
) -> tuple[MatchedEvent, ...]:
    matched: list[MatchedEvent] = []
    prior_timestamps: list[float] = []

    for internal_event_id, timestamp_ms in sorted(in_window, key=lambda item: (item[1], item[0])):
        distance_ms = match_distance_ms(
            float(timestamp_ms),
            period_ms=period_ms,
            anchor_ms=anchor_ms,
            prior_timestamps=prior_timestamps,
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
        prior_timestamps.append(float(timestamp_ms))

    return tuple(matched)


def match_events_to_alert(
    events: list[tuple[int, int]],
    *,
    ts_begin_ms: int,
    ts_end_ms: int,
    period_ms: float,
    phase_rad: float,
    min_confidence: int | None = None,
    sigma_ms: float | None = None,
) -> AlertMatchResult:
    """
    Score events inside [ts_begin_ms, ts_end_ms] against a periodic series.

    Uses a Fourier hint for the first pass, then re-fits period and anchor from matched
    events when enough are found. Final scoring combines absolute grid distance with
    local spacing-from-prior distance to limit cumulative drift.
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
        return AlertMatchResult((), period_ms, phase_rad)

    in_window = [
        (internal_event_id, timestamp_ms)
        for internal_event_id, timestamp_ms in events
        if ts_begin_ms <= timestamp_ms <= ts_end_ms
    ]
    if not in_window:
        return AlertMatchResult((), period_ms, phase_rad)

    in_window_timestamps = [timestamp_ms for _, timestamp_ms in in_window]
    hint_period_ms = refine_matching_period_ms(in_window_timestamps, period_ms)
    if len(in_window_timestamps) >= 2:
        hint_period_ms, hint_anchor_ms = estimate_series_period_and_anchor_ms(
            in_window_timestamps,
            hint_period_ms,
        )
    else:
        hint_anchor_ms = phase_offset_ms(phase_rad, hint_period_ms)
        if in_window_timestamps:
            first_ts = float(in_window_timestamps[0])
            cycle_index = round((first_ts - hint_anchor_ms) / hint_period_ms)
            hint_anchor_ms = first_ts - cycle_index * hint_period_ms

    first_pass = _score_events_in_window(
        in_window,
        period_ms=hint_period_ms,
        anchor_ms=hint_anchor_ms,
        threshold=threshold,
        jitter_sigma=jitter_sigma,
    )

    observed_period_ms = hint_period_ms
    observed_anchor_ms = hint_anchor_ms
    if len(first_pass) >= config.min_observed_matches_for_period_override:
        observed_period_ms, observed_anchor_ms = estimate_series_period_and_anchor_ms(
            [item.timestamp_ms for item in first_pass],
            hint_period_ms,
        )

    if len(first_pass) >= config.min_observed_matches_for_period_override:
        matched = _score_events_in_window(
            in_window,
            period_ms=observed_period_ms,
            anchor_ms=observed_anchor_ms,
            threshold=threshold,
            jitter_sigma=jitter_sigma,
        )
    else:
        matched = first_pass

    observed_phase_rad = phase_rad_from_anchor_ms(observed_anchor_ms, observed_period_ms)
    if not math.isfinite(observed_phase_rad):
        observed_phase_rad = phase_rad

    return AlertMatchResult(
        matched_events=matched,
        observed_period_ms=observed_period_ms,
        observed_phase_rad=observed_phase_rad,
    )
