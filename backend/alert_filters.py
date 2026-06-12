from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from log_registry import LOG_TYPE_CONFIG, resolve_event_name, resolve_native_event_ids_for_name


@dataclass(frozen=True)
class AlertQueryFilters:
    endpoint_id: str | None = None
    native_event_id: int | None = None
    event_name: str | None = None
    min_confidence: int | None = None
    window_start_ms: int | None = None
    window_end_ms: int | None = None
    sort_key: str = "confidence"
    sort_direction: str = "desc"


ALLOWED_SORT_KEYS = {
    "alertID",
    "nativeEventID",
    "eventName",
    "endpointID",
    "tsBegin",
    "tsEnd",
    "periodTs",
    "confidence",
}


def _parse_int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _parse_iso_ms(value: str | None) -> int | None:
    if value is None or value.strip() == "":
        return None
    normalized = value.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def build_alert_filters(args) -> AlertQueryFilters:
    time_preset = (args.get("timePreset") or "all").strip().lower()
    now = datetime.now(timezone.utc)
    window_end_ms = _parse_iso_ms(args.get("timeTo"))
    window_start_ms = _parse_iso_ms(args.get("timeFrom"))

    if window_start_ms is None and window_end_ms is None:
        if time_preset == "last_week":
            window_start_ms = int((now - timedelta(days=7)).timestamp() * 1000)
            window_end_ms = int(now.timestamp() * 1000)
        elif time_preset == "all":
            window_start_ms = None
            window_end_ms = None
        else:
            window_start_ms = int((now - timedelta(hours=24)).timestamp() * 1000)
            window_end_ms = int(now.timestamp() * 1000)
    elif window_start_ms is None:
        window_start_ms = 0
    elif window_end_ms is None:
        window_end_ms = int(now.timestamp() * 1000)

    sort_key = (args.get("sort") or "confidence").strip()
    if sort_key not in ALLOWED_SORT_KEYS:
        sort_key = "confidence"

    sort_direction = (args.get("order") or "desc").strip().lower()
    if sort_direction not in {"asc", "desc"}:
        sort_direction = "desc"

    return AlertQueryFilters(
        endpoint_id=(args.get("endpointID") or "").strip() or None,
        native_event_id=_parse_int(args.get("nativeEventID")),
        event_name=(args.get("eventName") or "").strip() or None,
        min_confidence=_parse_int(args.get("minConfidence")),
        window_start_ms=window_start_ms,
        window_end_ms=window_end_ms,
        sort_key=sort_key,
        sort_direction=sort_direction,
    )


def native_event_ids_for_filters(filters: AlertQueryFilters) -> set[int] | None:
    if filters.native_event_id is not None:
        return {filters.native_event_id}
    if filters.event_name:
        return resolve_native_event_ids_for_name(filters.event_name)
    return None
