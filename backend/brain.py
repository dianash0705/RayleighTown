import math
from dataclasses import dataclass
from typing import Callable, Iterable

MIN_EVENTS_FOR_ALERT = 4
UNKNOWN_TEXT = "NaN"
UNKNOWN_CONFIDENCE = 0


@dataclass(frozen=True)
class EventRecord:
    internal_event_id: int
    native_event_id: int
    timestamp: str


@dataclass(frozen=True)
class AlertRecord:
    endpoint_id: str
    native_event_id: int
    event_ids: list[int]
    ts_begin: str = UNKNOWN_TEXT
    ts_end: str = UNKNOWN_TEXT
    period_ts: float = math.nan
    confidence: int = UNKNOWN_CONFIDENCE


@dataclass(frozen=True)
class AlertCore:
    ts_begin: str = UNKNOWN_TEXT
    ts_end: str = UNKNOWN_TEXT
    period_ts: float = math.nan
    confidence: int = UNKNOWN_CONFIDENCE


FetchEventsFn = Callable[[str], Iterable[EventRecord]]
PublishAlertsFn = Callable[[str, list[AlertRecord]], int]


def _group_logs_by_native_event(events: Iterable[EventRecord]):
    grouped = {}
    for event in events:
        grouped.setdefault(event.native_event_id, []).append(event)
    return grouped


def build_alert_from_sorted_timestamps(sorted_timestamps: list[str]) -> AlertCore | None:
    if len(sorted_timestamps) < MIN_EVENTS_FOR_ALERT:
        return None

    return AlertCore()


def build_alerts_for_endpoint(endpoint_id: str, events: Iterable[EventRecord]) -> list[AlertRecord]:
    grouped_by_native_event = _group_logs_by_native_event(events)
    alerts = []

    for native_event_id, native_events in grouped_by_native_event.items():
        native_events = sorted(native_events, key=lambda item: item.timestamp)
        sorted_timestamps = [event.timestamp for event in native_events]
        alert_core = build_alert_from_sorted_timestamps(sorted_timestamps)
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
