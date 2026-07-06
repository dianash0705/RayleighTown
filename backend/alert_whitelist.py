"""
Match alerts against user-defined whitelist (expected-pattern) entries.

Whitelist hides alerts in the UI/API. Detections remain in the DB so strong
patterns can still suppress harmonic lookalikes during analysis.
"""

from __future__ import annotations

from typing import Any

from event_matching import periods_near_match


def alert_matches_whitelist_entry(
    *,
    endpoint_id: str,
    log_id: int | None,
    native_event_id: int,
    series_key: str,
    period_ms: float | None,
    entry: dict[str, Any],
) -> bool:
    """True when an alert identity is covered by one whitelist row."""
    entry_endpoint = entry.get("endpointID")
    if entry_endpoint is not None and str(entry_endpoint) != str(endpoint_id):
        return False

    if int(entry["nativeEventID"]) != int(native_event_id):
        return False

    entry_log_id = entry.get("logID")
    if entry_log_id is None:
        if log_id is not None:
            return False
    elif log_id is None or int(entry_log_id) != int(log_id):
        return False

    # Empty seriesKey on a whitelist entry means "any series" for this event type
    # (predictive whole-event mute). A non-empty key must match exactly.
    entry_series_key = str(entry.get("seriesKey") or "")
    alert_series_key = str(series_key or "")
    if entry_series_key and entry_series_key != alert_series_key:
        return False

    entry_period = entry.get("periodMs")
    if entry_period is None:
        return True
    if period_ms is None:
        return False
    try:
        period_value = float(period_ms)
        entry_period_value = float(entry_period)
    except (TypeError, ValueError):
        return False
    return periods_near_match(period_value, entry_period_value)


def is_alert_whitelisted(
    *,
    endpoint_id: str,
    log_id: int | None,
    native_event_id: int,
    series_key: str,
    period_ms: float | None,
    entries: list[dict[str, Any]],
) -> bool:
    return any(
        alert_matches_whitelist_entry(
            endpoint_id=endpoint_id,
            log_id=log_id,
            native_event_id=native_event_id,
            series_key=series_key,
            period_ms=period_ms,
            entry=entry,
        )
        for entry in entries
    )


def filter_alerts_against_whitelist(
    alerts: list[dict[str, Any]],
    entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not entries:
        return alerts
    visible: list[dict[str, Any]] = []
    for alert in alerts:
        if is_alert_whitelisted(
            endpoint_id=str(alert.get("endpointID") or ""),
            log_id=alert.get("logID"),
            native_event_id=int(alert.get("nativeEventID") or 0),
            series_key=str(alert.get("seriesKey") or ""),
            period_ms=alert.get("periodTs"),
            entries=entries,
        ):
            continue
        visible.append(alert)
    return visible
