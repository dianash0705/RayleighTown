"""
Per-event-type fields that define separate periodic series within one native event ID.

Only include fields that are stable for a single cadence and guaranteed to differ
across unrelated series. Avoid volatile endpoints (IPs, filenames with timestamps)
unless they are the semantic identity of the activity.
"""

from __future__ import annotations

from typing import Any

from config import EVENT_MATCHING_CONFIG

# log_id -> native_event_id -> identity field keys (from parsed identity dict)
SERIES_DISCRIMINATOR_FIELDS: dict[int, dict[int, tuple[str, ...]]] = {
    0: {
        # Windows Security
        4624: ("LogonType", "AuthenticationPackageName", "TargetUserName"),
        4625: ("LogonType", "FailureReason", "TargetUserName"),
        4688: ("NewProcessName",),
        4698: ("TaskName",),
        4702: ("TaskName",),
        4703: ("SubjectUserName", "PrivilegeList"),
        4720: ("TargetUserName", "SamAccountName"),
        4726: ("TargetUserName", "SamAccountName"),
        4946: ("SettingType", "SettingValue"),
        4947: ("RuleName", "Direction", "Action"),
    },
    1: {
        # Sysmon
        1: ("image", "parentImage"),
        # Sysmon — network uses peer-aware keys (see _network_series_identity)
        3: ("protocol", "image"),
        5: ("image",),
        7: ("image", "imageLoaded"),
        10: ("sourceImage", "targetImage", "grantedAccess"),
        11: ("image",),
        13: ("targetObject",),
        26: ("image",),
    },
}

# Unknown-source log type inherits both registries.
SERIES_DISCRIMINATOR_FIELDS[999] = {
    **SERIES_DISCRIMINATOR_FIELDS[0],
    **SERIES_DISCRIMINATOR_FIELDS[1],
}


def _parse_port(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "-":
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _network_series_identity(identity: dict[str, Any]) -> dict[str, str]:
    """
    Split Sysmon network connections by role, not every unique IP.

    Client-side (ephemeral source port): group by process + protocol + destination
    port. Include destinationHostname when present so DNS rotation stays one series.
    Server/listening side (well-known source port): group by remote client.
    """
    series_identity: dict[str, str] = {}
    for key in ("protocol", "image"):
        value = identity.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text and text != "-":
            series_identity[key] = text

    source_port = _parse_port(identity.get("sourcePort"))
    threshold = EVENT_MATCHING_CONFIG.network_server_source_port_threshold
    if source_port is not None and source_port < threshold:
        for key in ("sourceIp", "sourcePort"):
            value = identity.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text and text != "-":
                series_identity[key] = text
    else:
        for key in ("destinationPort",):
            value = identity.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text and text != "-":
                series_identity[key] = text
        hostname = identity.get("destinationHostname")
        if hostname is not None:
            hostname_text = str(hostname).strip().lower()
            if hostname_text and hostname_text != "-":
                series_identity["destinationHostname"] = hostname_text
    return series_identity


def should_skip_periodic_analysis(
    log_id: int,
    native_event_id: int,
    identity: dict[str, Any] | None = None,
    *,
    series_key: str = "",
) -> bool:
    """Skip series that are unlikely to represent a single client-side cadence."""
    if log_id not in (1, 999) or native_event_id != 3:
        return False

    source_port = None
    if identity:
        source_port = _parse_port(identity.get("sourcePort"))
    elif series_key:
        for part in series_key.split("|"):
            if part.startswith("sourcePort="):
                source_port = _parse_port(part.split("=", 1)[1])
                break

    if source_port is None:
        return False
    return source_port < EVENT_MATCHING_CONFIG.network_server_source_port_threshold


def series_field_keys_for_event(log_id: int, native_event_id: int) -> tuple[str, ...]:
    """Identity fields an operator should fill when pre-whitelisting a pattern."""
    parser_map = SERIES_DISCRIMINATOR_FIELDS.get(log_id) or SERIES_DISCRIMINATOR_FIELDS[999]
    field_keys = parser_map.get(native_event_id)
    if not field_keys:
        return ()
    if log_id in (1, 999) and native_event_id == 3:
        # Network series uses peer-aware keys beyond the static tuple.
        return (
            "protocol",
            "image",
            "destinationPort",
            "destinationHostname",
            "sourceIp",
            "sourcePort",
        )
    return field_keys


def list_series_field_catalog() -> list[dict[str, Any]]:
    """Event types with series discriminators, for predictive whitelist UI."""
    from log_registry import resolve_event_name, resolve_log_source_name

    catalog: list[dict[str, Any]] = []
    for log_id in sorted(SERIES_DISCRIMINATOR_FIELDS.keys()):
        if log_id == 999:
            continue
        for native_event_id in sorted(SERIES_DISCRIMINATOR_FIELDS[log_id].keys()):
            fields = series_field_keys_for_event(log_id, native_event_id)
            catalog.append(
                {
                    "logID": log_id,
                    "nativeEventID": native_event_id,
                    "eventName": resolve_event_name(log_id, native_event_id),
                    "logSource": resolve_log_source_name(log_id),
                    "fields": list(fields),
                }
            )
    return catalog


def extract_series_identity(
    log_id: int,
    native_event_id: int,
    identity: dict[str, Any] | None,
) -> dict[str, str]:
    if not identity:
        return {}

    parser_map = SERIES_DISCRIMINATOR_FIELDS.get(log_id) or SERIES_DISCRIMINATOR_FIELDS[999]
    field_keys = parser_map.get(native_event_id)
    if not field_keys:
        return {}

    if log_id in (1, 999) and native_event_id == 3:
        return _network_series_identity(identity or {})

    series_identity: dict[str, str] = {}
    for key in field_keys:
        value = identity.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text and text != "-":
            series_identity[key] = text
    return series_identity


def compute_series_key(
    log_id: int,
    native_event_id: int,
    identity: dict[str, Any] | None,
) -> str:
    series_identity = extract_series_identity(log_id, native_event_id, identity)
    if not series_identity:
        return ""

    parts = [f"{key}={series_identity[key]}" for key in sorted(series_identity.keys())]
    return "|".join(parts)


def enrich_parsed_details(
    log_id: int,
    native_event_id: int,
    parsed_details: dict[str, Any],
) -> dict[str, Any]:
    identity = parsed_details.get("identity") or {}
    series_identity = extract_series_identity(log_id, native_event_id, identity)
    parsed_details["seriesIdentity"] = series_identity
    parsed_details["seriesKey"] = compute_series_key(log_id, native_event_id, identity)
    return parsed_details
