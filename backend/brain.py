import math
from dataclasses import dataclass
from typing import Callable, Iterable, List
from bisect import bisect_left, bisect_right

from tqdm.auto import tqdm

from fourier import (
    filter_top_percent,
    finding_max,
    fourier_transform,
    local_max_suppression,
    plot_fourier_points,
    filter_by_snr,
    get_median_value,
    filter_by_harmony,
    get_candidate_period_groups_ms,
)
from config import (
    GHOST_PEAK_SUPPRESSION_ENABLED,
    PHASE_GHOST_SUPPRESSION_ENABLED,
    PHASE_GHOST_SUPPRESSION_SIMILARITY_THRESHOLD,
)

MIN_EVENTS_FOR_ALERT = 4
UNKNOWN_TIMESTAMP_MS = -1
UNKNOWN_CONFIDENCE = 0
SUPPRESSION_RADIUS_MS = 500
TOP_PERCENT = 0.10
SNR_THRESHOLD = 3.0
HARMONY_THRESHOLD = 0.8
HARMONY_PEAK_COUNT = 2
WINDOW_OVERLAP_RATIO = 0.5

@dataclass(frozen=True)
class EventRecord:
    internal_event_id: int
    native_event_id: int
    timestamp_ms: int


@dataclass(frozen=True)
class AlertRecord:
    endpoint_id: str
    native_event_id: int
    event_ids: list[int]
    ts_begin: int = UNKNOWN_TIMESTAMP_MS
    ts_end: int = UNKNOWN_TIMESTAMP_MS
    period_ts: float = math.nan
    confidence: int = UNKNOWN_CONFIDENCE


@dataclass(frozen=True)
class AlertCore:
    ts_begin: int = UNKNOWN_TIMESTAMP_MS
    ts_end: int = UNKNOWN_TIMESTAMP_MS
    period_ts: float = math.nan
    confidence: int = UNKNOWN_CONFIDENCE
    phase: float = math.nan


FetchEventsFn = Callable[[str], Iterable[EventRecord]]
PublishAlertsFn = Callable[[str, list[AlertRecord]], int]
AlertBuilderFn = Callable[[list[int], "AlertBuildContext"], AlertCore | None]


@dataclass(frozen=True)
class AlertBuildContext:
    endpoint_id: str
    native_event_id: int
    period_candidates_ms: list[float] | None = None
    plot: bool = False
    show_progress: bool = False


ALERT_BUILDERS: dict[str, AlertBuilderFn] = {}


def register_alert_builder(name: str, builder: AlertBuilderFn) -> None:
    ALERT_BUILDERS[name] = builder


def get_alert_builder(name: str) -> AlertBuilderFn:
    try:
        return ALERT_BUILDERS[name]
    except KeyError as err:
        available = ", ".join(sorted(ALERT_BUILDERS)) or "none"
        raise ValueError(f"Unknown alert builder '{name}'. Available builders: {available}") from err


def _group_logs_by_native_event(events: Iterable[EventRecord]):
    grouped = {}
    for event in events:
        grouped.setdefault(event.native_event_id, []).append(event)
    return grouped


def _build_window_ranges(start_ms: int, end_ms: int, window_size_ms: int) -> list[tuple[int, int]]:
    if start_ms > end_ms:
        return []

    if start_ms == end_ms or (end_ms - start_ms) <= window_size_ms:
        return [(start_ms, end_ms)]

    step_ms = max(1, int(window_size_ms * (1 - WINDOW_OVERLAP_RATIO)))
    window_ranges: list[tuple[int, int]] = []

    current_start = start_ms
    while current_start + window_size_ms <= end_ms:
        window_ranges.append((current_start, current_start + window_size_ms))
        current_start += step_ms

    final_start = max(start_ms, end_ms - window_size_ms)
    if not window_ranges or window_ranges[-1][0] != final_start:
        window_ranges.append((final_start, end_ms))

    return window_ranges


def _build_group_alerts_from_sorted_timestamps_ms(
    sorted_timestamps_ms: list[int],
    context: AlertBuildContext,
    window_size_ms: int,
) -> List[AlertCore] | None:
    if len(sorted_timestamps_ms) < MIN_EVENTS_FOR_ALERT:
        return None

    alerts: list[AlertCore] = []
    window_ranges = _build_window_ranges(sorted_timestamps_ms[0], sorted_timestamps_ms[-1], window_size_ms)

    for window_start_ms, window_end_ms in window_ranges:
        left_index = bisect_left(sorted_timestamps_ms, window_start_ms)
        right_index = bisect_right(sorted_timestamps_ms, window_end_ms)
        window_timestamps_ms = sorted_timestamps_ms[left_index:right_index]

        if len(window_timestamps_ms) < MIN_EVENTS_FOR_ALERT:
            continue

        alert_cores = build_fourier_alert_from_sorted_timestamps_ms(window_timestamps_ms, context)
        if not alert_cores:
            continue

        alerts.extend(alert_cores)

    return alerts or None


def _build_windowed_fourier_alerts_from_sorted_timestamps_ms(
    sorted_timestamps_ms: list[int],
    context: AlertBuildContext,
) -> List[AlertCore] | None:
    if len(sorted_timestamps_ms) < MIN_EVENTS_FOR_ALERT:
        return None

    try:
        candidate_groups = get_candidate_period_groups_ms(sorted_timestamps_ms[-1] - sorted_timestamps_ms[0])
    except ValueError:
        return None

    alerts: list[AlertCore] = []
    for group in sorted(candidate_groups, key=lambda candidate_group: candidate_group.window_size_ms, reverse=True):
        grouped_alert_cores = _build_group_alerts_from_sorted_timestamps_ms(
            sorted_timestamps_ms,
            AlertBuildContext(
                endpoint_id=context.endpoint_id,
                native_event_id=context.native_event_id,
                period_candidates_ms=group.periods_ms,
                plot=context.plot,
                show_progress=context.show_progress,
            ),
            window_size_ms=group.window_size_ms,
        )
        if grouped_alert_cores:
            alerts.extend(grouped_alert_cores)

    if not alerts:
        return None

    if not GHOST_PEAK_SUPPRESSION_ENABLED:
        return alerts

    def is_harmonic(base_period_ms: float, candidate_period_ms: float, tolerance_ratio: float = 0.05) -> bool:
        if candidate_period_ms <= 0:
            return False

        ratio = base_period_ms / candidate_period_ms
        nearest_multiplier = round(ratio)
        if nearest_multiplier < 2:
            return False

        return abs(ratio - nearest_multiplier) <= tolerance_ratio

    filtered_alerts: list[AlertCore] = []
    suppression_sources: list[AlertCore] = []
    ordered_alerts = sorted(alerts, key=lambda alert_core: alert_core.period_ts, reverse=True)

    for alert_core in ordered_alerts:
        suppress_alert = False

        for source_alert_core in suppression_sources:
            if not is_harmonic(source_alert_core.period_ts, alert_core.period_ts):
                continue

            if (not PHASE_GHOST_SUPPRESSION_ENABLED) or (
                math.cos(alert_core.phase - source_alert_core.phase) >= PHASE_GHOST_SUPPRESSION_SIMILARITY_THRESHOLD
            ):
                suppress_alert = True
                break

        suppression_sources.append(alert_core)

        if not suppress_alert:
            filtered_alerts.append(alert_core)

    return filtered_alerts or None


def build_fourier_alert_from_sorted_timestamps_ms(
    sorted_timestamps_ms: list[int],
    context: AlertBuildContext,
) -> List[AlertCore] | None:
    if len(sorted_timestamps_ms) < MIN_EVENTS_FOR_ALERT:
        return None

    if PHASE_GHOST_SUPPRESSION_ENABLED:
        period_candidates_ms, magnitudes, phases = fourier_transform(
            sorted_timestamps_ms,
            period_candidates_ms=context.period_candidates_ms,
            show_progress=context.show_progress,
            include_phase=True,
        )
    else:
        period_candidates_ms, magnitudes = fourier_transform(
            sorted_timestamps_ms,
            period_candidates_ms=context.period_candidates_ms,
            show_progress=context.show_progress,
            include_phase=False,
        )
        phases = [math.nan for _ in period_candidates_ms]
    if not period_candidates_ms or not magnitudes:
        return None

    points = list(zip(period_candidates_ms, magnitudes))
    phase_by_period = {period_ms: phase for period_ms, phase in zip(period_candidates_ms, phases)}

    median = get_median_value(magnitudes)
    distances_from_medians = []
    for magnitude in magnitudes:
        distance_from_median = abs(magnitude - median)
        distances_from_medians.append(distance_from_median)
    mad = get_median_value(distances_from_medians)

    local_max_indices = finding_max(magnitudes)
    local_max_points = [points[index] for index in local_max_indices]
    # if not local_max_points:
    #     return None

    suppressed_local_max_points = local_max_suppression(
        radius=SUPPRESSION_RADIUS_MS,
        local_maxs=local_max_points,
    )
    # if not suppressed_local_max_points:
    #     return None

    top_percent_points = filter_top_percent(
        suppressed_local_max_points,
        top_percent=TOP_PERCENT,
    )
    # if not top_percent_points:
    #     return None

    top_percent_points = suppressed_local_max_points
    high_snr_points = filter_by_snr(
        top_percent_points,
        median=median,
        mad=mad,
        min_snr=SNR_THRESHOLD,
    )

    harmonic_points = filter_by_harmony(
        high_snr_points,
        points,
        threshold=HARMONY_THRESHOLD,
        required_peak_count=HARMONY_PEAK_COUNT,
        median=median,
    )

    # # Prune sub-harmonic duplicates: if a large-period point has matching
    # # smaller-period peaks at exact integer divisors (within tolerance),
    # # remove the smaller / derived points so we report the canonical period.
    # if harmonic_points:
    #     pts = sorted(harmonic_points, key=lambda p: p[0], reverse=True)

    #     changed = True
    #     # Check divisors up to 6th harmonic (adjustable). Restart loop when a
    #     # removal happens to ensure transitive relationships are cleaned.
    #     while changed:
    #         changed = False
    #         for i, (base_period, _base_mag) in enumerate(pts):
    #             for n in range(2, 7):
    #                 target = base_period / n
    #                 # relative tolerance: 2% or at least 1ms
    #                 tol = max(1.0, target * 0.02)
    #                 # search for a point near the target (exclude the base itself)
    #                 found_index = None
    #                 for j, (p_period, _p_mag) in enumerate(pts):
    #                     if j == i:
    #                         continue
    #                     if abs(p_period - target) <= tol:
    #                         found_index = j
    #                         break

    #                 if found_index is not None:
    #                     # remove the derived (smaller) period so the base remains
    #                     del pts[found_index]
    #                     changed = True
    #                     break
    #             if changed:
    #                 break

    #     harmonic_points = sorted(pts, key=lambda p: p[0])
    # else:
    #     harmonic_points = []

    # Generate a graph of the transform and mark important points when requested.
    plot_path = None
    if context.plot:
        try:
            plot_path = plot_fourier_points(
                period_candidates_ms,
                magnitudes,
                top_percent_points=top_percent_points,
                high_snr_points=high_snr_points,
                harmonic_points=harmonic_points,
                median=median,
                mad=mad,
                snr_threshold=SNR_THRESHOLD,
                endpoint_id=context.endpoint_id,
                native_event_id=context.native_event_id,
            )
        except Exception:
            plot_path = None

    alerts = []
    for point in harmonic_points:
        confidence = max(0, min(100, int(round(point[1] * 100))))
        alerts.append(
            AlertCore(
                ts_begin=sorted_timestamps_ms[0],
                ts_end=sorted_timestamps_ms[-1],
                period_ts=float(point[0]),
                confidence=confidence,
                phase=float(phase_by_period.get(point[0], math.nan)),
            ))
    return alerts

register_alert_builder("fourier", build_fourier_alert_from_sorted_timestamps_ms)


def build_alerts_from_sorted_timestamps_ms(
    sorted_timestamps_ms: list[int],
    endpoint_id: str,
    native_event_id: int,
    period_candidates_ms: list[float] | None = None,
    method: str = "fourier",
    plot: bool = False,
    show_progress: bool = False,
) -> List[AlertCore] | None:
    builder = get_alert_builder(method)
    context = AlertBuildContext(
        endpoint_id=endpoint_id,
        native_event_id=native_event_id,
        period_candidates_ms=period_candidates_ms,
        plot=plot,
        show_progress=show_progress,
    )
    if method == "fourier":
        return _build_windowed_fourier_alerts_from_sorted_timestamps_ms(sorted_timestamps_ms, context)

    return builder(sorted_timestamps_ms, context)


def build_alerts_for_endpoint(
    endpoint_id: str,
    events: Iterable[EventRecord],
    method: str = "fourier",
    plot: bool = False,
    show_progress: bool = False,
) -> list[AlertRecord]:
    grouped_by_native_event = _group_logs_by_native_event(events)
    alerts = []
    seen_alert_keys: set[tuple] = set()

    native_event_items = grouped_by_native_event.items()
    if show_progress:
        native_event_items = tqdm(
            native_event_items,
            desc=f"Processing endpoint {endpoint_id}",
            unit="event group",
        )

    for native_event_id, native_events in native_event_items:
        native_events = sorted(native_events, key=lambda item: item.timestamp_ms)
        sorted_timestamps_ms = [event.timestamp_ms for event in native_events]
        alert_cores = build_alerts_from_sorted_timestamps_ms(
            sorted_timestamps_ms,
            endpoint_id=endpoint_id,
            native_event_id=native_event_id,
            method=method,
            plot=plot,
            show_progress=show_progress,
        )
        if not alert_cores:
            continue

        for alert_core in alert_cores:
            alert_record = AlertRecord(
                endpoint_id=endpoint_id,
                native_event_id=native_event_id,
                event_ids=[event.internal_event_id for event in native_events],
                ts_begin=alert_core.ts_begin,
                ts_end=alert_core.ts_end,
                period_ts=alert_core.period_ts,
                confidence=alert_core.confidence,
            )
            alert_key = (
                alert_record.endpoint_id,
                alert_record.native_event_id,
                tuple(alert_record.event_ids),
                alert_record.ts_begin,
                alert_record.ts_end,
                alert_record.period_ts,
                alert_record.confidence,
            )
            if alert_key in seen_alert_keys:
                continue
            seen_alert_keys.add(alert_key)
            alerts.append(alert_record)

    return alerts


def run_brain_for_endpoint(
    endpoint_id: str,
    fetch_events: FetchEventsFn,
    publish_alerts: PublishAlertsFn,
    method: str = "fourier",
    plot: bool = False,
    show_progress: bool = False,
) -> int:
    events = list(fetch_events(endpoint_id))
    alerts = build_alerts_for_endpoint(
        endpoint_id,
        events,
        method=method,
        plot=plot,
        show_progress=show_progress,
    )
    return publish_alerts(endpoint_id, alerts)
