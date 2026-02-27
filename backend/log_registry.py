from log_processors import extract_windows_evtx_events

LOG_TYPE_CONFIG = {
    0: {
        "name": "windows_security",
        "extractor": extract_windows_evtx_events,
        "event_id_whitelist": {4624, 4625, 4634},
    }
}
