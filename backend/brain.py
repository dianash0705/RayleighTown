import math
from dataclasses import dataclass
from typing import Callable, Iterable

from fourier import (
    filter_top_percent,
    finding_max,
    fourier_transform,
    local_max_suppression,
)

MIN_EVENTS_FOR_ALERT = 4
UNKNOWN_TIMESTAMP_MS = -1
UNKNOWN_CONFIDENCE = 0
SUPPRESSION_RADIUS_MS = 15_000
TOP_PERCENT = 0.10


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


def _group_logs_by_native_event(events: Iterable[EventRecord]):
    grouped = {}
    for event in events:
        grouped.setdefault(event.native_event_id, []).append(event)
    return grouped


def build_alert_from_sorted_timestamps_ms(sorted_timestamps_ms: list[int]) -> AlertCore | None:
    if len(sorted_timestamps_ms) < MIN_EVENTS_FOR_ALERT:
        return None

    period_candidates_ms, magnitudes = fourier_transform(
        sorted_timestamps_ms,
        show_progress=True,
    )
    if not period_candidates_ms or not magnitudes:
        return None

    points = list(zip(period_candidates_ms, magnitudes))
    local_max_indices = finding_max(magnitudes)
    local_max_points = [points[index] for index in local_max_indices]
    if not local_max_points:
        return None

    suppressed_local_max_points = local_max_suppression(
        radius=SUPPRESSION_RADIUS_MS,
        local_maxs=local_max_points,
    )
    if not suppressed_local_max_points:
        return None

    top_percent_points = filter_top_percent(
        suppressed_local_max_points,
        top_percent=TOP_PERCENT,
    )
    if not top_percent_points:
        return None

    dominant_period_ms, dominant_magnitude = max(top_percent_points, key=lambda point: point[1])
    confidence = max(0, min(100, int(round(dominant_magnitude * 100))))

    return AlertCore(
        ts_begin=sorted_timestamps_ms[0],
        ts_end=sorted_timestamps_ms[-1],
        period_ts=float(dominant_period_ms),
        confidence=confidence,
    )


def build_alerts_for_endpoint(endpoint_id: str, events: Iterable[EventRecord]) -> list[AlertRecord]:
    grouped_by_native_event = _group_logs_by_native_event(events)
    alerts = []

    for native_event_id, native_events in grouped_by_native_event.items():
        native_events = sorted(native_events, key=lambda item: item.timestamp_ms)
        sorted_timestamps_ms = [event.timestamp_ms for event in native_events]
        alert_core = build_alert_from_sorted_timestamps_ms(sorted_timestamps_ms)
        if alert_core is None:
            continue

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


def run_brain_for_endpoint(endpoint_id: str, fetch_events: FetchEventsFn, publish_alerts: PublishAlertsFn) -> int:
    events = list(fetch_events(endpoint_id))
    alerts = build_alerts_for_endpoint(endpoint_id, events)
    return publish_alerts(endpoint_id, alerts)
