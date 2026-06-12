from typing import Any

ENDPOINT_HOSTNAME_KEYS = (
    "hostname",
    "Hostname",
    "Computer",
    "host",
    "endpoint_hostname",
    "agent_hostname",
    "WorkstationName",
    "Workstation",
)

ENDPOINT_IP_KEYS = (
    "ip",
    "IP",
    "HostIp",
    "host_ip",
    "hostip",
    "machine_ip",
    "MachineIp",
    "agent_ip",
    "endpoint_ip",
    "endpointIP",
    "ipv4",
    "Ipv4",
)


def normalize_payload(record: dict[str, Any]) -> dict[str, Any]:
    payload = record.get("result")
    if isinstance(payload, dict):
        return payload
    return record


def payload_value(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = payload.get(key)
        if value is not None and str(value).strip() != "":
            return value
    return None


def normalize_ip(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "-":
        return None
    if text.lower().startswith("::ffff:"):
        text = text[7:]
    if text in {"127.0.0.1", "::1", "0.0.0.0"}:
        return None
    return text


def endpoint_agent_hostname(source: dict[str, Any]) -> str | None:
    value = payload_value(source, *ENDPOINT_HOSTNAME_KEYS)
    return str(value).strip() if value is not None else None


def endpoint_agent_ip(source: dict[str, Any]) -> str | None:
    for key in ENDPOINT_IP_KEYS:
        normalized = normalize_ip(source.get(key))
        if normalized:
            return normalized
    return None


def extract_endpoint_agent_metadata(record: dict[str, Any]) -> tuple[str | None, str | None]:
    payload = normalize_payload(record)
    hostname = None
    ip = None

    for source in (record, payload):
        if hostname is None:
            hostname = endpoint_agent_hostname(source)
        if ip is None:
            ip = endpoint_agent_ip(source)

    return hostname, ip


def payload_hostname(payload: dict[str, Any]) -> str | None:
    return endpoint_agent_hostname(payload)


def resolve_endpoint_ip(raw_record: dict[str, Any] | None, _parsed_details: dict[str, Any] | None = None) -> str | None:
    if not raw_record:
        return None
    _hostname, ip = extract_endpoint_agent_metadata(raw_record)
    return ip


def event_data_map(payload: dict[str, Any]) -> dict[str, str]:
    event_data = payload.get("EventData")
    if isinstance(event_data, dict):
        return {str(key): str(value) for key, value in event_data.items() if value is not None}

    if isinstance(event_data, list):
        mapped: dict[str, str] = {}
        for item in event_data:
            if not isinstance(item, dict):
                continue
            name = item.get("Name") or item.get("name")
            value = item.get("Value") or item.get("value") or item.get("#text")
            if name and value is not None:
                mapped[str(name)] = str(value)
        return mapped

    mapped: dict[str, str] = {}
    for key, value in payload.items():
        if key.startswith("EventData_"):
            mapped[key.removeprefix("EventData_")] = str(value)
    return mapped
