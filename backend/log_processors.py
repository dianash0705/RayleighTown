from datetime import datetime, timezone
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

EVENT_XML_NAMESPACE = {"e": "http://schemas.microsoft.com/win/2004/08/events/event"}


def _system_time_to_epoch_ms(system_time_text: str) -> int:
    normalized = system_time_text.strip().replace("Z", "+00:00")

    # fromisoformat supports up to 6 fractional second digits.
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


def extract_windows_evtx_events(log_path: Path, event_id_whitelist):
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

        if native_event_id not in event_id_whitelist:
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

        events.append((timestamp_ms, native_event_id))

    return events
