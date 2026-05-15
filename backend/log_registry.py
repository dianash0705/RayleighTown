from log_processors import extract_windows_evtx_events

WINDOWS_SECURITY_EVENT_NAMES = {
    4688: "Process Creation",
    4698: "Scheduled Task Created/Updated",
    4702: "Scheduled Task Created/Updated",
    4624: "Successful Logon",
    4703: "Token Right Adjusted",
}

LOG_TYPE_CONFIG = {
    0: {
        "name": "windows_security",
        "extractor": extract_windows_evtx_events,
        "event_id_whitelist": set(WINDOWS_SECURITY_EVENT_NAMES),
        "event_id_names": WINDOWS_SECURITY_EVENT_NAMES,
    }
}
