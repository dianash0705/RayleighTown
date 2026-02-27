import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

EVENT_XML_NAMESPACE = {"e": "http://schemas.microsoft.com/win/2004/08/events/event"}


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

        timestamp = time_node.attrib.get("SystemTime")
        if not timestamp:
            continue

        events.append((timestamp, native_event_id))

    return events
