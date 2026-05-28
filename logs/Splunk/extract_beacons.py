import json

# Make sure this matches the exact name of the file you extracted
INPUT_FILE = "botsv1.XmlWinEventLog_Microsoft-Windows-Sysmon-Operational.json"
OUTPUT_FILE = "rayleightown_beacons.json"
print(f"Scanning {INPUT_FILE} for network connections...")


def get_event_code(event_data):
    """Return the event code from a Splunk-style JSON line.

    Supports records where the event payload is either at the top level or
    nested under a `result` key.
    """
    if not isinstance(event_data, dict):
        return None

    payload = event_data.get("result")
    if isinstance(payload, dict):
        if "EventCode" in payload:
            return str(payload["EventCode"]).strip()
        if "eventcode" in payload:
            return str(payload["eventcode"]).strip()

    if "EventCode" in event_data:
        return str(event_data["EventCode"]).strip()
    if "eventcode" in event_data:
        return str(event_data["eventcode"]).strip()

    return None

with open(INPUT_FILE, 'r', encoding='utf-8') as infile, \
     open(OUTPUT_FILE, 'w', encoding='utf-8') as outfile:
    
    match_count = 0
    
    for line in infile:
        try:
            # Validate it is proper JSON and inspect the parsed payload rather
            # than relying on exact whitespace in the input line.
            event_data = json.loads(line)
        except json.JSONDecodeError:
            continue

        event_code = get_event_code(event_data)
        if event_code == "3":
            # Write it back as a clean JSON line
            outfile.write(json.dumps(event_data) + '\n')
            match_count += 1

print(f"Extraction complete! Found {match_count} network connection events.")
print(f"Your clean data is ready in: {OUTPUT_FILE}")