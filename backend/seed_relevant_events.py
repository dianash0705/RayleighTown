from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import sys
from dataclasses import dataclass
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parent
REPO_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from event_parsers import parse_event_details


@dataclass(frozen=True)
class SeedSource:
    label: str
    path: Path
    log_id: int


@dataclass(frozen=True)
class Organization:
    organization_id: int
    name: str


@dataclass(frozen=True)
class SeedResult:
    source: SeedSource
    inserted_count: int
    skipped_count: int
    event_summary: tuple[tuple[str, int], ...]


DEFAULT_SOURCES = (
    SeedSource(
        label="sysmon sample",
        path=REPO_ROOT / "logs" / "aptsimulator_cobaltstrike" / "aptsimulator_cobaltstrike_2021-06-11T21081492.json",
        log_id=1,
    ),
    SeedSource(
        label="security sample",
        path=REPO_ROOT / "logs" / "JPCERT" / "Security.evtx",
        log_id=0,
    ),
)

SHOWCASE_EVENT_TYPES = (
    (0, 4688, "Process Creation", {
        "NewProcessName": r"C:\\Windows\\System32\\cmd.exe",
        "CommandLine": r'"C:\\Windows\\System32\\cmd.exe" /c whoami',
        "ParentProcessName": r"C:\\Windows\\explorer.exe",
        "SubjectUserName": "alice",
        "SubjectDomainName": "CORP",
    }),
    (0, 4624, "Successful Logon", {
        "IpAddress": "10.0.0.25",
        "WorkstationName": "HOST-A",
        "TargetUserName": "alice",
        "TargetDomainName": "CORP",
        "LogonType": "0x7",
        "ProcessName": r"C:\\Windows\\System32\\winlogon.exe",
        "AuthenticationPackageName": "Negotiate",
    }),
    (0, 4625, "Failed Logon", {
        "IpAddress": "10.0.0.99",
        "WorkstationName": "HOST-B",
        "TargetUserName": "bob",
        "TargetDomainName": "CORP",
        "FailureReason": "Unknown user name or bad password.",
        "Status": "0xC000006A",
        "LogonType": "0x3",
    }),
    (0, 4720, "User Account Created", {
        "TargetUserName": "charlie",
        "SamAccountName": "charlie",
        "TargetDomainName": "CORP",
        "SubjectUserName": "admin",
    }),
    (0, 4726, "User Account Deleted", {
        "TargetUserName": "dave",
        "SamAccountName": "dave",
        "TargetDomainName": "CORP",
        "SubjectUserName": "admin",
    }),
    (0, 4698, "Scheduled Task Created/Updated", {
        "TaskName": r"\\Microsoft\\Windows\\TestTask",
        "SubjectUserName": "admin",
        "Operation": "Created",
    }),
    (0, 4702, "Scheduled Task Created/Updated", {
        "TaskName": r"\\Microsoft\\Windows\\TestTask",
        "SubjectUserName": "admin",
        "Operation": "Updated",
    }),
    (0, 4703, "Token Right Adjusted", {
        "SubjectUserName": "admin",
        "TargetUserName": "alice",
        "PrivilegeList": "SeDebugPrivilege",
    }),
    (0, 4946, "Windows Firewall Setting Changed", {
        "SettingType": "FirewallState",
        "SettingValue": "On",
        "SubjectUserName": "admin",
    }),
    (0, 4947, "Windows Firewall Rule Changed", {
        "RuleName": "Allow Demo App",
        "Action": "allow",
        "Direction": "inbound",
        "Application": r"C:\\Program Files\\Demo\\demo.exe",
    }),
    (1, 1, "Process Create", {
        "Image": r"C:\\Windows\\System32\\powershell.exe",
        "CommandLine": "powershell.exe -NoProfile -Command Get-Date",
        "ParentImage": r"C:\\Windows\\System32\\cmd.exe",
        "User": "CORP\\alice",
        "Hashes": "SHA256=DEMO",
    }),
    (1, 3, "Network Connection", {
        "SourceIp": "10.0.0.50",
        "SourcePort": "53000",
        "DestinationIp": "203.0.113.10",
        "DestinationPort": "443",
        "DestinationHostname": "c2.example.com",
        "Protocol": "tcp",
        "Image": r"C:\\Windows\\System32\\svchost.exe",
        "User": "NT AUTHORITY\\SYSTEM",
    }),
    (1, 5, "Process Terminated", {
        "Image": r"C:\\Windows\\System32\\calc.exe",
        "User": "CORP\\alice",
    }),
    (1, 7, "Image Loaded", {
        "ImageLoaded": r"C:\\Windows\\System32\\ntdll.dll",
        "Image": r"C:\\Windows\\System32\\svchost.exe",
        "Signed": "true",
    }),
    (1, 10, "Process Access", {
        "SourceImage": r"C:\\Windows\\System32\\cmd.exe",
        "TargetImage": r"C:\\Windows\\System32\\lsass.exe",
        "GrantedAccess": "0x1410",
        "CallTrace": "trace",
    }),
    (1, 11, "File Create", {
        "TargetFilename": r"C:\\Temp\\demo.txt",
        "Image": r"C:\\Windows\\System32\\cmd.exe",
        "User": "CORP\\alice",
    }),
    (1, 13, "Registry SetValue", {
        "TargetObject": r"HKCU\\Software\\Demo\\Setting",
        "Details": "DWORD (0x00000001)",
        "Image": r"C:\\Windows\\System32\\powershell.exe",
    }),
    (1, 26, "File Delete", {
        "TargetFilename": r"C:\\Temp\\old-demo.txt",
        "Image": r"C:\\Windows\\System32\\cmd.exe",
        "User": "CORP\\alice",
    }),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Populate the test database with realistic sample events and rebuild alerts.",
    )
    parser.add_argument(
        "--organization",
        default="Seed Test Organization",
        help="Organization name to attach the seeded endpoint to.",
    )
    parser.add_argument("--endpoint-id", default="seed-test-endpoint", help="Endpoint ID to seed.")
    parser.add_argument(
        "--display-name",
        default="Seed Test Endpoint",
        help="Display name stored for the seeded endpoint.",
    )
    parser.add_argument(
        "--keep-existing",
        action="store_true",
        help="Keep existing rows for the endpoint instead of clearing them first.",
    )
    parser.add_argument(
        "--no-alert-rebuild",
        action="store_true",
        help="Skip alert recomputation after loading the seed events.",
    )
    parser.add_argument(
        "--sysmon-log",
        type=Path,
        default=DEFAULT_SOURCES[0].path,
        help="Path to a Sysmon JSON log to seed.",
    )
    parser.add_argument(
        "--security-log",
        type=Path,
        default=DEFAULT_SOURCES[1].path,
        help="Path to a Windows Security EVTX log to seed.",
    )
    return parser.parse_args()


def _get_or_create_organization(name: str) -> Organization:
    from database import connect_db

    organization_name = name.strip()
    if not organization_name:
        raise ValueError("Organization name is required.")

    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT organizationID, name FROM organizations WHERE name = ? COLLATE NOCASE",
        (organization_name,),
    )
    row = cursor.fetchone()
    if row is None:
        cursor.execute(
            "INSERT INTO organizations (name, createdAt) VALUES (?, strftime('%s','now') * 1000)",
            (organization_name,),
        )
        organization_id = int(cursor.lastrowid)
        conn.commit()
        conn.close()
        return Organization(organization_id=organization_id, name=organization_name)

    conn.close()
    return Organization(organization_id=int(row[0]), name=str(row[1]))


def _clear_endpoint(endpoint_id: str) -> None:
    from database import connect_db

    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM alertGroupMap WHERE alertGroupID IN (SELECT alertGroupID FROM alertGroups WHERE endpointID = ?)",
        (endpoint_id,),
    )
    cursor.execute("DELETE FROM alertGroups WHERE endpointID = ?", (endpoint_id,))
    cursor.execute(
        "DELETE FROM eventAlertMap WHERE alertID IN (SELECT alertID FROM alerts WHERE endpointID = ?)",
        (endpoint_id,),
    )
    cursor.execute("DELETE FROM alerts WHERE endpointID = ?", (endpoint_id,))
    cursor.execute("DELETE FROM logs WHERE endpointID = ?", (endpoint_id,))
    conn.commit()
    conn.close()


def _seed_source(endpoint_id: str, source: SeedSource) -> tuple[int, int]:
    from database import insert_events
    from log_registry import LOG_TYPE_CONFIG

    if not source.path.exists():
        raise FileNotFoundError(f"Seed source not found: {source.path}")

    log_config = LOG_TYPE_CONFIG.get(source.log_id)
    if log_config is None:
        raise ValueError(f"Unsupported log ID for {source.label}: {source.log_id}")

    extractor = log_config["extractor"]
    whitelist = log_config.get("event_id_whitelist")
    events = extractor(source.path, whitelist, log_id=source.log_id)
    result = insert_events(endpoint_id, source.log_id, events)

    event_counts: dict[str, int] = {}
    for event in events:
        event_name = log_config.get("event_id_names", {}).get(int(event["native_event_id"]))
        if event_name is None:
            event_name = f"Event {event['native_event_id']}"
        event_counts[event_name] = event_counts.get(event_name, 0) + 1

    return SeedResult(
        source=source,
        inserted_count=result.inserted_count,
        skipped_count=result.skipped_count,
        event_summary=tuple(sorted(event_counts.items(), key=lambda item: (-item[1], item[0]))),
    )


def _build_showcase_events(endpoint_id: str) -> SeedResult:
    from database import insert_events

    base_ms = int(datetime.now(timezone.utc).timestamp() * 1000) + 86_400_000
    timestamp_step_ms = 60_000
    per_event_count = 8

    events_by_log_id: dict[int, list[dict[str, object]]] = defaultdict(list)
    event_counts: Counter[str] = Counter()
    timestamp_ms = base_ms

    for log_id, native_event_id, event_name, payload in SHOWCASE_EVENT_TYPES:
        event_counts[event_name] += per_event_count
        for _index in range(per_event_count):
            parsed_details = parse_event_details(log_id, native_event_id, payload)
            events_by_log_id[log_id].append(
                {
                    "timestamp_ms": timestamp_ms,
                    "native_event_id": native_event_id,
                    "raw_payload": {**payload, "EventID": native_event_id},
                    "parsed_details": parsed_details,
                }
            )
            timestamp_ms += timestamp_step_ms
        timestamp_ms += timestamp_step_ms

    inserted_count = 0
    skipped_count = 0
    for log_id, events in sorted(events_by_log_id.items()):
        result = insert_events(endpoint_id, log_id, events)
        inserted_count += result.inserted_count
        skipped_count += result.skipped_count

    source = SeedSource("parser showcase", Path("<synthetic>"), 999)
    return SeedResult(
        source=source,
        inserted_count=inserted_count,
        skipped_count=skipped_count,
        event_summary=tuple(sorted(event_counts.items(), key=lambda item: (-item[1], item[0]))),
    )


def main() -> None:
    args = parse_args()

    from database import connect_db, init_db, recompute_alerts_for_endpoint

    init_db()

    organization = _get_or_create_organization(args.organization)

    if not args.keep_existing:
        _clear_endpoint(args.endpoint_id)

    total_inserted = 0
    total_skipped = 0
    loaded_sources = 0

    showcase_result = _build_showcase_events(args.endpoint_id)
    total_inserted += showcase_result.inserted_count
    total_skipped += showcase_result.skipped_count
    loaded_sources += 1
    showcase_summary = ", ".join(f"{name}={count}" for name, count in showcase_result.event_summary)
    print(
        f"Loaded parser showcase: inserted={showcase_result.inserted_count} skipped={showcase_result.skipped_count} "
        f"types={showcase_summary}",
    )

    for source in (
        SeedSource("sysmon sample", args.sysmon_log, 1),
        SeedSource("security sample", args.security_log, 0),
    ):
        result = _seed_source(args.endpoint_id, source)
        total_inserted += result.inserted_count
        total_skipped += result.skipped_count
        loaded_sources += 1
        summary_text = ", ".join(f"{name}={count}" for name, count in result.event_summary[:6])
        print(
            f"Loaded {source.label}: inserted={result.inserted_count} skipped={result.skipped_count} "
            f"file={source.path} types={summary_text}",
        )

    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO endpoints (endpointID, hostname, ip, lastSeenAt, organizationID, displayName)
        VALUES (?, NULL, NULL, NULL, ?, ?)
        ON CONFLICT(endpointID) DO UPDATE SET
            organizationID = excluded.organizationID,
            displayName = COALESCE(excluded.displayName, endpoints.displayName)
        """,
        (args.endpoint_id, organization.organization_id, args.display_name),
    )
    conn.commit()
    conn.close()

    alerts_created = 0
    if not args.no_alert_rebuild:
        alerts_created = recompute_alerts_for_endpoint(args.endpoint_id, show_progress=True)

    print(
        "Seed complete: "
        f"organization={organization.name!r} endpointID={args.endpoint_id} displayName={args.display_name!r} "
        f"sources={loaded_sources} inserted={total_inserted} skipped={total_skipped} alerts={alerts_created}",
    )


if __name__ == "__main__":
    main()