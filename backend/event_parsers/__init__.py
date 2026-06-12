from typing import Any, Callable

from event_details import make_event_details
from event_parsers.sysmon import SYSMON_EVENT_PARSERS
from event_parsers.windows_security import WINDOWS_SECURITY_EVENT_PARSERS

EventParser = Callable[[dict[str, Any]], dict]

LOG_EVENT_PARSERS: dict[int, dict[int, EventParser]] = {
    0: WINDOWS_SECURITY_EVENT_PARSERS,
    1: SYSMON_EVENT_PARSERS,
    999: {**WINDOWS_SECURITY_EVENT_PARSERS, **SYSMON_EVENT_PARSERS},
}


def parse_event_details(log_id: int, native_event_id: int, record: dict[str, Any]) -> dict:
    parser_map = LOG_EVENT_PARSERS.get(log_id) or LOG_EVENT_PARSERS[999]
    parser = parser_map.get(native_event_id)
    if parser is None:
        return make_event_details(f"Event {native_event_id}", [], {})
    return parser(record)
