from log_processors import extract_windows_events
import json
from pathlib import Path


WINDOWS_SECURITY_EVENT_NAMES = {
    4688: "Process Creation",
    4698: "Scheduled Task Created/Updated",
    4702: "Scheduled Task Created/Updated",
    4624: "Successful Logon",
    4703: "Token Right Adjusted",
}

# Sysmon events commonly present in the JSON sample
SYSMON_EVENT_NAMES = {
    1: "Process Create",
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
    "event_id_names": {},
}
