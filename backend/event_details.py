from typing import Any, TypedDict


class EventDetailField(TypedDict, total=False):
    key: str
    label: str
    value: str
    emphasis: bool


class EventDetails(TypedDict):
    title: str
    fields: list[EventDetailField]
    identity: dict[str, str]


LOGON_TYPE_NAMES = {
    0: "System",
    1: "Interactive",
    2: "Network",
    3: "Batch",
    4: "Service",
    5: "Proxy",
    6: "Unlock",
    7: "Network Cleartext",
    8: "New Credentials",
    9: "Remote Interactive",
    10: "Cached Interactive",
    11: "Cached Remote Interactive",
    12: "Cached Unlock",
}

NTSTATUS_NAMES = {
    0x0: "Success",
    0xC0000064: "User name does not exist",
    0xC000006A: "Wrong password",
    0xC000006D: "Bad username or password",
    0xC000006E: "Account restriction",
    0xC000006F: "Invalid logon hours",
    0xC0000070: "Invalid workstation",
    0xC0000071: "Password expired",
    0xC0000072: "Account disabled",
    0xC0000133: "Clock skew too great",
    0xC000015B: "Logon type not granted",
    0xC000018D: "Trust failure",
    0xC0000193: "Account expired",
    0xC0000224: "Password must change",
    0xC0000234: "Account locked out",
}

PROCESS_ACCESS_FLAGS = (
    (0x00000001, "Terminate"),
    (0x00000002, "Create thread"),
    (0x00000004, "Set session ID"),
    (0x00000008, "Virtual memory operation"),
    (0x00000010, "Virtual memory read"),
    (0x00000020, "Virtual memory write"),
    (0x00000040, "Duplicate handle"),
    (0x00000080, "Create process"),
    (0x00000100, "Set quota"),
    (0x00000200, "Set information"),
    (0x00000400, "Query information"),
    (0x00000800, "Suspend/resume"),
    (0x00001000, "Query limited information"),
    (0x00010000, "Delete"),
    (0x00020000, "Read control"),
    (0x00040000, "Write DAC"),
    (0x00080000, "Write owner"),
    (0x00100000, "Synchronize"),
    (0x01000000, "Access system security"),
    (0x10000000, "Generic all"),
    (0x20000000, "Generic execute"),
    (0x40000000, "Generic write"),
    (0x80000000, "Generic read"),
)


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _parse_int(text: str) -> int | None:
    try:
        return int(text, 0)
    except (TypeError, ValueError):
        return None


def _format_labeled_code(text: str, names: dict[int, str]) -> str:
    number = _parse_int(text)
    if number is None:
        return text

    label = names.get(number)
    if label is None:
        return text

    if text.lower() == label.lower():
        return label
    return f"{label} ({text})"


def _format_boolean(text: str) -> str:
    lowered = text.lower()
    if lowered in {"true", "1", "yes", "signed", "verified"}:
        return "Yes"
    if lowered in {"false", "0", "no", "unsigned", "unverified"}:
        return "No"
    return text


def _format_protocol(text: str) -> str:
    lowered = text.lower()
    if lowered in {"tcp", "udp", "icmp", "gre", "sctp", "esp", "ah"}:
        return lowered.upper()
    return text


def _format_direction(text: str) -> str:
    lowered = text.lower()
    if lowered in {"in", "inbound"}:
        return "Inbound"
    if lowered in {"out", "outbound"}:
        return "Outbound"
    return text.title()


def _format_action(text: str) -> str:
    lowered = text.lower()
    if lowered in {"allow", "allowed"}:
        return "Allow"
    if lowered in {"block", "blocked", "deny", "denied"}:
        return "Block"
    return text.title()


def _format_granted_access(text: str) -> str:
    number = _parse_int(text)
    if number is None:
        return text

    names = [name for mask, name in PROCESS_ACCESS_FLAGS if number & mask == mask]
    if not names:
        return text
    return f"{', '.join(names)} ({text})"


def humanize_field_value(key: str, value: Any, event_type: str | None = None) -> str:
    text = _text(value)
    if not text:
        return text

    normalized_key = key.strip().lower()
    if normalized_key == "logontype":
        return _format_labeled_code(text, LOGON_TYPE_NAMES)
    if normalized_key in {"status", "substatus"}:
        return _format_labeled_code(text, NTSTATUS_NAMES)
    if normalized_key == "signed":
        return _format_boolean(text)
    if normalized_key == "protocol":
        return _format_protocol(text)
    if normalized_key == "direction" and event_type == "Windows Firewall Rule Changed":
        return _format_direction(text)
    if normalized_key == "action" and event_type == "Windows Firewall Rule Changed":
        return _format_action(text)
    if normalized_key == "grantedaccess":
        return _format_granted_access(text)
    return text


def make_event_details(
    title: str,
    fields: list[EventDetailField],
    identity: dict[str, str] | None = None,
) -> EventDetails:
    return {
        "title": title,
        "fields": fields,
        "identity": identity or {},
    }


def field(
    key: str,
    label: str,
    value: Any,
    emphasis: bool = False,
    event_type: str | None = None,
) -> EventDetailField | None:
    if value is None:
        return None
    text = humanize_field_value(key, value, event_type=event_type)
    if not text or text == "-":
        return None
    return {
        "key": key,
        "label": label,
        "value": text,
        "emphasis": emphasis,
    }


def compact_fields(*items: EventDetailField | None) -> list[EventDetailField]:
    return [item for item in items if item is not None]
