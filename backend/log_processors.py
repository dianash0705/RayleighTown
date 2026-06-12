from datetime import datetime, timezone
import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from event_parsers import parse_event_details
from event_parsers.common import extract_endpoint_agent_metadata, normalize_payload

EVENT_XML_NAMESPACE = {"e": "http://schemas.microsoft.com/win/2004/08/events/event"}


def _system_time_to_epoch_ms(system_time_text: str) -> int:
    normalized = system_time_text.strip().replace("Z", "+00:00")

    if "." in normalized:
        main_part, _, remainder = normalized.partition(".")
        plus_idx = remainder.find("+")
        minus_idx = remainder.find("-")
        tz_candidates = [index for index in (plus_idx, minus_idx) if index != -1]
        if tz_candidates:
            tz_idx = min(tz_candidates)
            normalized = f"{main_part}.{remainder[:tz_idx][:6]}{remainder[tz_idx:]}"

    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return int(dt.timestamp() * 1000)


def _json_time_to_epoch_ms(time_text: str) -> int:
    return _system_time_to_epoch_ms(time_text)


def _build_ingested_event(
    log_id: int,
    native_event_id: int,
    timestamp_ms: int,
    record: dict[str, Any],
) -> dict[str, Any]:
    parsed_details = parse_event_details(log_id, native_event_id, record)
    agent_hostname, agent_ip = extract_endpoint_agent_metadata(record)
    return {
        "timestamp_ms": timestamp_ms,
        "native_event_id": native_event_id,
        "raw_payload": record,
        "parsed_details": parsed_details,
        "hostname": agent_hostname,
        "ip": agent_ip,
    }


def _extract_json_payload(record):
    return normalize_payload(record)


def _evtx_event_data(root: ET.Element) -> dict[str, str]:
    event_data: dict[str, str] = {}
    for data_node in root.findall("./e:EventData/e:Data", namespaces=EVENT_XML_NAMESPACE):
        name = data_node.attrib.get("Name")
        value = data_node.text
        if name and value is not None:
            event_data[name] = value.strip()
    return event_data


def _evtx_to_payload(root: ET.Element) -> dict[str, Any]:
    system = root.find("./e:System", namespaces=EVENT_XML_NAMESPACE)
    event_id_text = system.findtext("e:EventID", namespaces=EVENT_XML_NAMESPACE) if system is not None else None
    computer = system.findtext("e:Computer", namespaces=EVENT_XML_NAMESPACE) if system is not None else None
    provider = None
    if system is not None:
        provider_node = system.find("e:Provider", namespaces=EVENT_XML_NAMESPACE)
        if provider_node is not None:
            provider = provider_node.attrib.get("Name")

    payload: dict[str, Any] = {
        "EventID": int(event_id_text) if event_id_text else None,
        "Computer": computer,
        "ProviderName": provider,
        "EventData": _evtx_event_data(root),
    }
    payload.update(payload["EventData"])
    return payload


def _extract_json_events(log_path: Path, event_id_whitelist, log_id: int):
    events = []
    with log_path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue

            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            payload = _extract_json_payload(record)

            native_event_id_value = payload.get("EventID")
            if native_event_id_value is None:
                native_event_id_value = payload.get("EventCode")
            try:
                native_event_id = int(native_event_id_value)
            except (TypeError, ValueError):
                continue

            if event_id_whitelist is not None and native_event_id not in event_id_whitelist:
                continue

            time_text = (
                payload.get("TimeCreated")
                or payload.get("@timestamp")
                or payload.get("UtcTime")
                or record.get("TimeCreated")
                or record.get("@timestamp")
                or record.get("UtcTime")
            )
            if not time_text:
                continue

            try:
                timestamp_ms = _json_time_to_epoch_ms(str(time_text))
            except ValueError:
                continue

            events.append(
                _build_ingested_event(
                    log_id=log_id,
                    native_event_id=native_event_id,
                    timestamp_ms=timestamp_ms,
                    record=record,
                )
            )

    return events


def extract_windows_evtx_events(log_path: Path, event_id_whitelist, log_id: int = 0):
    command = [
        "wevtutil",
        "qe",
        str(log_path),
        "/lf:true",
        "/f:xml",
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Failed to read event log.")

    fragments = result.stdout.split("</Event>")
    events = []
    for fragment in fragments:
        content = fragment.strip()
        if not content:
            continue

        xml_text = f"{content}</Event>"
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            continue

        native_event_id_text = root.findtext("./e:System/e:EventID", namespaces=EVENT_XML_NAMESPACE)
        if native_event_id_text is None:
            continue

        try:
            native_event_id = int(native_event_id_text)
        except ValueError:
            continue

        if event_id_whitelist is not None and native_event_id not in event_id_whitelist:
            continue

        time_node = root.find("./e:System/e:TimeCreated", namespaces=EVENT_XML_NAMESPACE)
        if time_node is None:
            continue

        system_time_text = time_node.attrib.get("SystemTime")
        if not system_time_text:
            continue

        try:
            timestamp_ms = _system_time_to_epoch_ms(system_time_text)
        except ValueError:
            continue

        payload = _evtx_to_payload(root)
        events.append(
            _build_ingested_event(
                log_id=log_id,
                native_event_id=native_event_id,
                timestamp_ms=timestamp_ms,
                record=payload,
            )
        )

    return events


def extract_windows_json_events(log_path: Path, event_id_whitelist, log_id: int = 0):
    return _extract_json_events(log_path, event_id_whitelist, log_id)


def extract_windows_events(log_path: Path, event_id_whitelist, log_id: int = 0):
    suffix = log_path.suffix.lower()
    if suffix == ".json":
        return extract_windows_json_events(log_path, event_id_whitelist, log_id)

    return extract_windows_evtx_events(log_path, event_id_whitelist, log_id)
