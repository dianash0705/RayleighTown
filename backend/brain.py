import math
from dataclasses import dataclass
from typing import Callable, Iterable, List

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
)

MIN_EVENTS_FOR_ALERT = 4
UNKNOWN_TIMESTAMP_MS = -1
UNKNOWN_CONFIDENCE = 0
SUPPRESSION_RADIUS_MS = 500
TOP_PERCENT = 0.10
SNR_THRESHOLD = 3.0
HARMONY_THRESHOLD = 0.8
HARMONY_PEAK_COUNT = 2

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


FetchEventsFn = Callable[[str], Iterable[EventRecord]]
PublishAlertsFn = Callable[[str, list[AlertRecord]], int]
AlertBuilderFn = Callable[[list[int], "AlertBuildContext"], AlertCore | None]


@dataclass(frozen=True)
class AlertBuildContext:
    endpoint_id: str
    native_event_id: int
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


def build_fourier_alert_from_sorted_timestamps_ms(
    sorted_timestamps_ms: list[int],
    context: AlertBuildContext,
) -> List[AlertCore] | None:
    if len(sorted_timestamps_ms) < MIN_EVENTS_FOR_ALERT:
        return None

    period_candidates_ms, magnitudes = fourier_transform(
        sorted_timestamps_ms,
        show_progress=context.show_progress,
    )
    if not period_candidates_ms or not magnitudes:
        return None

    points = list(zip(period_candidates_ms, magnitudes))

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
            ))
    return alerts

register_alert_builder("fourier", build_fourier_alert_from_sorted_timestamps_ms)


def build_alerts_from_sorted_timestamps_ms(
    sorted_timestamps_ms: list[int],
    endpoint_id: str,
    native_event_id: int,
    method: str = "fourier",
    plot: bool = False,
    show_progress: bool = False,
) -> List[AlertCore] | None:
    builder = get_alert_builder(method)
    context = AlertBuildContext(
        endpoint_id=endpoint_id,
        native_event_id=native_event_id,
        plot=plot,
        show_progress=show_progress,
    )
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
            alerts.append(
            AlertRecord(
                endpoint_id=endpoint_id,
                native_event_id=native_event_id,
                event_ids=[event.internal_event_id for event in native_events],
                ts_begin=alert_core.ts_begin,
                ts_end=alert_core.ts_end,
                period_ts=alert_core.period_ts,
                confidence=alert_core.confidence,
            )
        )

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
