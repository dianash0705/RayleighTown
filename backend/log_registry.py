from log_processors import extract_windows_events
import json
from pathlib import Path


WINDOWS_SECURITY_EVENT_NAMES = {
    4688: "Process Creation",
    4698: "Scheduled Task Created/Updated",
    4702: "Scheduled Task Created/Updated",
    4624: "Successful Logon",
    4625: "Failed Logon",
    4720: "User Account Created",
    4726: "User Account Deleted",
    4703: "Token Right Adjusted",
    4946: "Windows Firewall Setting Changed",
    4947: "Windows Firewall Rule Changed",
}

# Sysmon events commonly present in the JSON sample
SYSMON_EVENT_NAMES = {
    1: "Process Create",
    3: "Network Connection",
    5: "Process Terminated",
    7: "Image Loaded",
    10: "Process Access",
    11: "File Create",
    13: "Registry SetValue",
    26: "File Delete",
}

# Load optional mapping of logID -> source name from a JSON file alongside this module.
_MAP_PATH = Path(__file__).with_name("log_source_map.json")
if _MAP_PATH.exists():
    try:
        with _MAP_PATH.open("r", encoding="utf-8") as fh:
            _raw = json.load(fh)
            LOG_SOURCE_MAP = {int(k): v for k, v in _raw.items()}
    except Exception:
        LOG_SOURCE_MAP = {}
else:
    LOG_SOURCE_MAP = {}

LOG_TYPE_CONFIG = {
    0: {
        "name": LOG_SOURCE_MAP.get(0, "windows_security"),
        "extractor": extract_windows_events,
        "event_id_whitelist": set(WINDOWS_SECURITY_EVENT_NAMES),
        "event_id_names": WINDOWS_SECURITY_EVENT_NAMES,
    }
}

# Additional registry entries for known sources
LOG_TYPE_CONFIG[1] = {
    "name": LOG_SOURCE_MAP.get(1, "Microsoft-Windows-Sysmon"),
    "extractor": extract_windows_events,
    "event_id_whitelist": set(SYSMON_EVENT_NAMES),
    "event_id_names": SYSMON_EVENT_NAMES,
}

# Default unknown source: allow all events (no whitelist)
LOG_TYPE_CONFIG[999] = {
    "name": LOG_SOURCE_MAP.get(999, "unknown source"),
    "extractor": extract_windows_events,
    "event_id_whitelist": None,
    "event_id_names": {**WINDOWS_SECURITY_EVENT_NAMES, **SYSMON_EVENT_NAMES},
}


def all_event_names() -> list[str]:
    names: set[str] = set()
    for config in LOG_TYPE_CONFIG.values():
        names.update(config.get("event_id_names", {}).values())
    return sorted(names)


def resolve_event_name(log_id: int | None, native_event_id: int) -> str:
    if log_id is not None:
        config = LOG_TYPE_CONFIG.get(log_id)
        if config:
            name = config.get("event_id_names", {}).get(native_event_id)
            if name:
                return name

    for config in LOG_TYPE_CONFIG.values():
        name = config.get("event_id_names", {}).get(native_event_id)
        if name:
            return name

    return f"Event {native_event_id}"


def resolve_native_event_ids_for_name(event_name: str) -> set[int]:
    normalized = event_name.strip().lower()
    matches: set[int] = set()
    for config in LOG_TYPE_CONFIG.values():
        for native_event_id, name in config.get("event_id_names", {}).items():
            if name.lower() == normalized:
                matches.add(native_event_id)
    return matches


def resolve_log_source_name(log_id: int | None) -> str | None:
    if log_id is None:
        return None
    config = LOG_TYPE_CONFIG.get(log_id)
    return config["name"] if config else None
