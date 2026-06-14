import json
import sqlite3
from datetime import datetime, timedelta, timezone

from alert_filters import AlertQueryFilters, apply_filter_rules
from auth import (
    generate_endpoint_id,
    generate_endpoint_secret,
    hash_secret,
    verify_secret,
)
from brain import EventRecord, run_brain_for_endpoint
from config import DB_PATH, PHASE_GHOST_SUPPRESSION_ENABLED, PHASE_GHOST_SUPPRESSION_SIMILARITY_THRESHOLD
from event_parsers.common import extract_endpoint_agent_metadata
from log_registry import resolve_event_name, resolve_log_source_name


def _table_columns(cursor, table_name: str) -> set[str]:
    cursor.execute(f"PRAGMA table_info({table_name})")
    return {row[1] for row in cursor.fetchall()}


def _ensure_column(cursor, table_name: str, column_definition: str) -> None:
    column_name = column_definition.split()[0]
    if column_name not in _table_columns(cursor, table_name):
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_definition}")


def _phase_similarity(left_phase: float, right_phase: float) -> float:
    import math

    return math.cos(left_phase - right_phase)


def _compute_overview_time_span(
    *,
    log_min_ms: int | None,
    log_max_ms: int | None,
    activity_min_ms: int,
    activity_max_ms: int,
) -> dict[str, int | str]:
    """Build a wide overview axis: prefer full log span, with sensible minimum padding."""
    one_day_ms = 24 * 60 * 60 * 1000
    two_weeks_ms = 14 * one_day_ms

    if (
        log_min_ms is not None
        and log_max_ms is not None
        and log_max_ms > log_min_ms
    ):
        ts_begin = int(log_min_ms)
        ts_end = int(log_max_ms)
        source = "log"
    else:
        ts_begin = int(activity_min_ms)
        ts_end = int(activity_max_ms)
        source = "activity"

    if ts_end <= ts_begin:
        center = int((activity_min_ms + activity_max_ms) / 2)
        ts_begin = center - one_day_ms // 2
        ts_end = center + one_day_ms // 2
        source = "padded_day"

    span_ms = ts_end - ts_begin
    center = int((activity_min_ms + activity_max_ms) / 2)

    if span_ms < one_day_ms:
        half = one_day_ms // 2
        ts_begin = center - half
        ts_end = center + half
        source = "padded_day"
    elif source != "log" and span_ms < two_weeks_ms:
        half = two_weeks_ms // 2
        ts_begin = center - half
        ts_end = center + half
        source = "padded_two_weeks"

    margin_ms = max(int((ts_end - ts_begin) * 0.02), 60_000)
    return {
        "tsBegin": ts_begin - margin_ms,
        "tsEnd": ts_end + margin_ms,
        "source": source,
    }


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS endpoints (
            endpointID TEXT PRIMARY KEY,
            hostname TEXT,
            ip TEXT,
            lastSeenAt INTEGER
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS logs (
            endpointID TEXT NOT NULL,
            internalEventID INTEGER NOT NULL,
            timestamp INTEGER NOT NULL,
            logID INTEGER NOT NULL,
            nativeEventID INTEGER NOT NULL,
            internalEventType INTEGER NOT NULL,
            rawPayload TEXT NOT NULL,
            parsedDetails TEXT NOT NULL,
            PRIMARY KEY (endpointID, internalEventID)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS alerts (
            alertID INTEGER PRIMARY KEY AUTOINCREMENT,
            endpointID TEXT NOT NULL,
            nativeEventID INTEGER NOT NULL,
            logID INTEGER,
            tsBegin INTEGER NOT NULL,
            tsEnd INTEGER NOT NULL,
            periodTs REAL,
            confidence INTEGER NOT NULL,
            phase REAL
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS eventAlertMap (
            eventID INTEGER NOT NULL,
            alertID INTEGER NOT NULL,
            confidence INTEGER NOT NULL,
            PRIMARY KEY (eventID, alertID)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS alertGroups (
            alertGroupID INTEGER PRIMARY KEY AUTOINCREMENT,
            endpointID TEXT NOT NULL,
            nativeEventID INTEGER NOT NULL,
            logID INTEGER,
            tsBegin INTEGER NOT NULL,
            tsEnd INTEGER NOT NULL,
            periodTs REAL,
            confidence INTEGER NOT NULL,
            phase REAL
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS alertGroupMap (
            alertGroupID INTEGER NOT NULL,
            alertID INTEGER NOT NULL,
            PRIMARY KEY (alertGroupID, alertID)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS organizations (
            organizationID INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            createdAt INTEGER NOT NULL
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS accounts (
            accountID INTEGER PRIMARY KEY AUTOINCREMENT,
            organizationID INTEGER NOT NULL,
            name TEXT NOT NULL,
            passwordHash TEXT NOT NULL,
            isAdmin INTEGER NOT NULL DEFAULT 0,
            isSuperAdmin INTEGER NOT NULL DEFAULT 0,
            createdAt INTEGER NOT NULL,
            createdByAccountID INTEGER,
            UNIQUE (organizationID, name)
        )
        """
    )

    _ensure_column(cursor=conn.cursor(), table_name="endpoints", column_definition="organizationID INTEGER")
    _ensure_column(cursor=conn.cursor(), table_name="endpoints", column_definition="displayName TEXT")
    _ensure_column(cursor=conn.cursor(), table_name="endpoints", column_definition="secret TEXT")
    _ensure_column(cursor=conn.cursor(), table_name="endpoints", column_definition="secretHash TEXT")
    _ensure_column(cursor=conn.cursor(), table_name="endpoints", column_definition="registeredAt INTEGER")
    _ensure_column(cursor=conn.cursor(), table_name="alerts", column_definition="nativeEventID INTEGER NOT NULL DEFAULT 0")
    _ensure_column(cursor=conn.cursor(), table_name="alerts", column_definition="phase REAL")
    _ensure_column(cursor=conn.cursor(), table_name="alerts", column_definition="logID INTEGER")
    _ensure_column(cursor=conn.cursor(), table_name="alertGroups", column_definition="logID INTEGER")
    _ensure_column(cursor=conn.cursor(), table_name="logs", column_definition="rawPayload TEXT NOT NULL DEFAULT '{}'")
    _ensure_column(cursor=conn.cursor(), table_name="logs", column_definition="parsedDetails TEXT NOT NULL DEFAULT '{}'")
    conn.commit()
    conn.close()


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _serialize_account_row(row) -> dict:
    return {
        "accountID": int(row[0]),
        "organizationID": int(row[1]),
        "name": row[2],
        "isAdmin": bool(row[3]),
        "isSuperAdmin": bool(row[4]),
        "createdAt": int(row[5]) if row[5] is not None else None,
        "createdByAccountID": int(row[6]) if row[6] is not None else None,
    }


def get_organization_by_id(organization_id: int) -> dict | None:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT organizationID, name, createdAt FROM organizations WHERE organizationID = ?",
        (organization_id,),
    )
    row = cursor.fetchone()
    conn.close()
    if row is None:
        return None
    return {"organizationID": int(row[0]), "name": row[1], "createdAt": int(row[2])}


def get_organization_by_name(name: str) -> dict | None:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT organizationID, name, createdAt FROM organizations WHERE name = ? COLLATE NOCASE",
        (name,),
    )
    row = cursor.fetchone()
    conn.close()
    if row is None:
        return None
    return {"organizationID": int(row[0]), "name": row[1], "createdAt": int(row[2])}


def get_account_by_id(account_id: int) -> dict | None:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT accountID, organizationID, name, isAdmin, isSuperAdmin, createdAt, createdByAccountID
        FROM accounts WHERE accountID = ?
        """,
        (account_id,),
    )
    row = cursor.fetchone()
    conn.close()
    if row is None:
        return None
    return _serialize_account_row(row)


def authenticate_account(organization_name: str, account_name: str, password: str) -> dict | None:
    """Return the account dict when the org/username/password triple is valid."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT a.accountID, a.organizationID, a.name, a.isAdmin, a.isSuperAdmin,
               a.createdAt, a.createdByAccountID, a.passwordHash
        FROM accounts a
        JOIN organizations o ON o.organizationID = a.organizationID
        WHERE o.name = ? COLLATE NOCASE AND a.name = ? COLLATE NOCASE
        """,
        (organization_name, account_name),
    )
    row = cursor.fetchone()
    conn.close()
    if row is None:
        return None
    if not verify_secret(password, row[7]):
        return None
    return _serialize_account_row(row[:7])


def create_organization_with_admin(organization_name: str, admin_name: str, admin_password: str) -> dict:
    """Create an organization and its default (super) admin account in one shot.

    Raises ValueError if the organization name is already taken.
    """
    organization_name = organization_name.strip()
    admin_name = admin_name.strip()
    if not organization_name:
        raise ValueError("Organization name is required.")
    if not admin_name:
        raise ValueError("Admin username is required.")
    if not admin_password:
        raise ValueError("Admin password is required.")

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    now_ms = _now_ms()
    # Case-insensitive uniqueness check (the column's UNIQUE constraint is case-sensitive).
    cursor.execute(
        "SELECT 1 FROM organizations WHERE name = ? COLLATE NOCASE",
        (organization_name,),
    )
    if cursor.fetchone() is not None:
        conn.close()
        raise ValueError("An organization with that name already exists.")
    try:
        cursor.execute(
            "INSERT INTO organizations (name, createdAt) VALUES (?, ?)",
            (organization_name, now_ms),
        )
        organization_id = cursor.lastrowid
        cursor.execute(
            """
            INSERT INTO accounts (organizationID, name, passwordHash, isAdmin, isSuperAdmin, createdAt, createdByAccountID)
            VALUES (?, ?, ?, 1, 1, ?, NULL)
            """,
            (organization_id, admin_name, hash_secret(admin_password), now_ms),
        )
        account_id = cursor.lastrowid
        conn.commit()
    except sqlite3.IntegrityError as error:
        conn.close()
        raise ValueError("An organization with that name already exists.") from error
    conn.close()
    return get_account_by_id(account_id)


def list_accounts_for_organization(organization_id: int) -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT accountID, organizationID, name, isAdmin, isSuperAdmin, createdAt, createdByAccountID
        FROM accounts
        WHERE organizationID = ?
        ORDER BY isSuperAdmin DESC, isAdmin DESC, name COLLATE NOCASE ASC
        """,
        (organization_id,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [_serialize_account_row(row) for row in rows]


def create_account(
    organization_id: int,
    name: str,
    password: str,
    is_admin: bool,
    created_by_account_id: int,
) -> dict:
    name = name.strip()
    if not name:
        raise ValueError("Username is required.")
    if not password:
        raise ValueError("Password is required.")

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO accounts (organizationID, name, passwordHash, isAdmin, isSuperAdmin, createdAt, createdByAccountID)
            VALUES (?, ?, ?, ?, 0, ?, ?)
            """,
            (
                organization_id,
                name,
                hash_secret(password),
                1 if is_admin else 0,
                _now_ms(),
                created_by_account_id,
            ),
        )
        account_id = cursor.lastrowid
        conn.commit()
    except sqlite3.IntegrityError as error:
        conn.close()
        raise ValueError("A user with that name already exists in this organization.") from error
    conn.close()
    return get_account_by_id(account_id)


def delete_account(organization_id: int, account_id: int) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM accounts WHERE accountID = ? AND organizationID = ?",
        (account_id, organization_id),
    )
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


def set_account_admin(organization_id: int, account_id: int, is_admin: bool) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE accounts SET isAdmin = ?
        WHERE accountID = ? AND organizationID = ? AND isSuperAdmin = 0
        """,
        (1 if is_admin else 0, account_id, organization_id),
    )
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return updated


def register_endpoint(organization_id: int, display_name: str | None) -> dict:
    """Register a new endpoint for an organization and return its id + secret.

    The secret is stored (so admins can re-display it for a lost agent) alongside a
    hash that is used for the actual authentication checks.
    """
    display_name = (display_name or "").strip() or None
    secret = generate_endpoint_secret()

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    now_ms = _now_ms()
    # Retry a few times in the (astronomically unlikely) event of an id collision.
    for _ in range(5):
        endpoint_id = generate_endpoint_id()
        cursor.execute("SELECT 1 FROM endpoints WHERE endpointID = ?", (endpoint_id,))
        if cursor.fetchone() is None:
            break
    else:
        conn.close()
        raise RuntimeError("Could not allocate a unique endpoint id.")

    cursor.execute(
        """
        INSERT INTO endpoints (endpointID, hostname, ip, lastSeenAt, organizationID, displayName, secret, secretHash, registeredAt)
        VALUES (?, NULL, NULL, NULL, ?, ?, ?, ?, ?)
        """,
        (endpoint_id, organization_id, display_name, secret, hash_secret(secret), now_ms),
    )
    conn.commit()
    conn.close()
    return {
        "endpointID": endpoint_id,
        "displayName": display_name,
        "secret": secret,
        "registeredAt": now_ms,
    }


def get_endpoint_secret(organization_id: int, endpoint_id: str) -> dict | None:
    """Return the stored secret for an endpoint (admin reveal), or None if not found.

    The returned ``secret`` may itself be None for endpoints registered before
    secrets were stored; those must be reset to obtain a viewable secret.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT secret FROM endpoints WHERE endpointID = ? AND organizationID = ?",
        (endpoint_id, organization_id),
    )
    row = cursor.fetchone()
    conn.close()
    if row is None:
        return None
    return {"endpointID": endpoint_id, "secret": row[0]}


def reset_endpoint_secret(organization_id: int, endpoint_id: str) -> dict | None:
    """Generate a fresh secret for an endpoint, invalidating the previous one.

    Returns the new secret, or None if the endpoint does not belong to the org.
    """
    secret = generate_endpoint_secret()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE endpoints SET secret = ?, secretHash = ? WHERE endpointID = ? AND organizationID = ?",
        (secret, hash_secret(secret), endpoint_id, organization_id),
    )
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    if not updated:
        return None
    return {"endpointID": endpoint_id, "secret": secret}


def list_registered_endpoints(organization_id: int) -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT endpointID, displayName, hostname, ip, lastSeenAt, registeredAt, secret
        FROM endpoints
        WHERE organizationID = ?
        ORDER BY registeredAt DESC, endpointID ASC
        """,
        (organization_id,),
    )
    rows = cursor.fetchall()
    conn.close()
    endpoints = []
    for row in rows:
        endpoints.append(
            {
                "endpointID": row[0],
                "displayName": row[1],
                "hostname": row[2],
                "ip": row[3],
                "lastSeenAt": int(row[4]) if row[4] is not None else None,
                "registeredAt": int(row[5]) if row[5] is not None else None,
                # Whether a viewable secret is stored (so the UI can offer "Show").
                # The plaintext itself is only returned via the dedicated reveal route.
                "hasSecret": row[6] is not None,
            }
        )
    return endpoints


def delete_endpoint(organization_id: int, endpoint_id: str) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM endpoints WHERE endpointID = ? AND organizationID = ?",
        (endpoint_id, organization_id),
    )
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


def get_endpoint_organization(endpoint_id: str) -> int | None:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT organizationID FROM endpoints WHERE endpointID = ?", (endpoint_id,))
    row = cursor.fetchone()
    conn.close()
    if row is None or row[0] is None:
        return None
    return int(row[0])


def verify_endpoint_secret(endpoint_id: str, secret: str) -> bool:
    """Return True when the endpoint exists, is registered, and the secret matches."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT secretHash FROM endpoints WHERE endpointID = ?", (endpoint_id,))
    row = cursor.fetchone()
    conn.close()
    if row is None:
        return False
    return verify_secret(secret, row[0])


def upsert_endpoint(endpoint_id: str, hostname: str | None, ip: str | None, last_seen_at: int) -> None:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO endpoints (endpointID, hostname, ip, lastSeenAt)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(endpointID) DO UPDATE SET
            hostname = COALESCE(excluded.hostname, endpoints.hostname),
            ip = COALESCE(excluded.ip, endpoints.ip),
            lastSeenAt = MAX(endpoints.lastSeenAt, excluded.lastSeenAt)
        """,
        (endpoint_id, hostname, ip, last_seen_at),
    )
    conn.commit()
    conn.close()


def insert_events(endpoint_id: str, log_id: int, events):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        "SELECT MAX(internalEventID) FROM logs WHERE endpointID = ?",
        (endpoint_id,),
    )
    max_id_row = cursor.fetchone()
    next_internal_event_id = 0 if max_id_row[0] is None else max_id_row[0] + 1

    latest_timestamp = 0
    endpoint_hostname = None
    endpoint_ip = None

    for event in events:
        timestamp_ms = event["timestamp_ms"]
        native_event_id = event["native_event_id"]
        if not isinstance(timestamp_ms, int):
            raise TypeError("timestamp_ms must be int")

        raw_payload = event.get("raw_payload") or {}
        parsed_details = event.get("parsed_details") or {}
        agent_hostname, agent_ip = extract_endpoint_agent_metadata(raw_payload)
        hostname = event.get("hostname") or agent_hostname
        ip = event.get("ip") or agent_ip

        if hostname and not endpoint_hostname:
            endpoint_hostname = hostname
        if ip:
            endpoint_ip = ip
        latest_timestamp = max(latest_timestamp, timestamp_ms)

        cursor.execute(
            """
            INSERT INTO logs (
                endpointID,
                internalEventID,
                timestamp,
                logID,
                nativeEventID,
                internalEventType,
                rawPayload,
                parsedDetails
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                endpoint_id,
                next_internal_event_id,
                timestamp_ms,
                log_id,
                native_event_id,
                native_event_id,
                json.dumps(raw_payload),
                json.dumps(parsed_details),
            ),
        )
        next_internal_event_id += 1

    if latest_timestamp:
        cursor.execute(
            """
            INSERT INTO endpoints (endpointID, hostname, ip, lastSeenAt)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(endpointID) DO UPDATE SET
                hostname = COALESCE(excluded.hostname, endpoints.hostname),
                ip = COALESCE(excluded.ip, endpoints.ip),
                lastSeenAt = MAX(endpoints.lastSeenAt, excluded.lastSeenAt)
            """,
            (endpoint_id, endpoint_hostname, endpoint_ip, latest_timestamp),
        )

    conn.commit()
    conn.close()
    return len(events)


def fetch_events_for_endpoint(endpoint_id: str) -> list[EventRecord]:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT internalEventID, nativeEventID, timestamp
        FROM logs
        WHERE endpointID = ?
        ORDER BY timestamp
        """,
        (endpoint_id,),
    )
    rows = cursor.fetchall()
    conn.close()

    return [
        EventRecord(
            internal_event_id=internal_event_id,
            native_event_id=native_event_id,
            timestamp_ms=timestamp,
        )
        for internal_event_id, native_event_id, timestamp in rows
    ]


def _alert_log_id(cursor, endpoint_id: str, native_event_id: int, event_ids: list[int]) -> int | None:
    if event_ids:
        cursor.execute(
            """
            SELECT logID
            FROM logs
            WHERE endpointID = ? AND internalEventID = ?
            """,
            (endpoint_id, event_ids[0]),
        )
        row = cursor.fetchone()
        if row and row[0] is not None:
            return int(row[0])

    cursor.execute(
        """
        SELECT logID
        FROM logs
        WHERE endpointID = ? AND nativeEventID = ?
        ORDER BY timestamp DESC
        LIMIT 1
        """,
        (endpoint_id, native_event_id),
    )
    row = cursor.fetchone()
    return int(row[0]) if row and row[0] is not None else None


def replace_alerts_for_endpoint(endpoint_id: str, alerts) -> int:
    conn = sqlite3.connect(DB_PATH)
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

    raw_alert_rows = []

    for alert in alerts:
        log_id = _alert_log_id(cursor, alert.endpoint_id, alert.native_event_id, alert.event_ids)
        cursor.execute(
            """
            INSERT INTO alerts (
                endpointID,
                nativeEventID,
                logID,
                tsBegin,
                tsEnd,
                periodTs,
                confidence,
                phase
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                alert.endpoint_id,
                alert.native_event_id,
                log_id,
                alert.ts_begin,
                alert.ts_end,
                alert.period_ts,
                alert.confidence,
                alert.phase,
            ),
        )
        alert_id = cursor.lastrowid
        raw_alert_rows.append(
            {
                "alert_id": alert_id,
                "endpoint_id": alert.endpoint_id,
                "native_event_id": alert.native_event_id,
                "log_id": log_id,
                "ts_begin": alert.ts_begin,
                "ts_end": alert.ts_end,
                "period_ts": alert.period_ts,
                "confidence": alert.confidence,
                "phase": alert.phase,
            }
        )

        for matched_event in alert.matched_events:
            cursor.execute(
                """
                INSERT INTO eventAlertMap (
                    eventID,
                    alertID,
                    confidence
                ) VALUES (?, ?, ?)
                """,
                (matched_event.internal_event_id, alert_id, matched_event.confidence),
            )

    grouped_alerts: list[dict] = []

    def find_matching_group(raw_alert_row: dict):
        for group in grouped_alerts:
            if group["endpoint_id"] != raw_alert_row["endpoint_id"]:
                continue
            if group["native_event_id"] != raw_alert_row["native_event_id"]:
                continue
            if group["period_ts"] != raw_alert_row["period_ts"]:
                continue

            if PHASE_GHOST_SUPPRESSION_ENABLED:
                if group["phase"] is None or raw_alert_row["phase"] is None:
                    continue
                if _phase_similarity(group["phase"], raw_alert_row["phase"]) < PHASE_GHOST_SUPPRESSION_SIMILARITY_THRESHOLD:
                    continue

            return group

        return None

    for raw_alert_row in sorted(
        raw_alert_rows,
        key=lambda row: (row["native_event_id"], row["period_ts"], row["ts_begin"], row["ts_end"], row["alert_id"]),
    ):
        group = find_matching_group(raw_alert_row)
        if group is None:
            group = {
                "endpoint_id": raw_alert_row["endpoint_id"],
                "native_event_id": raw_alert_row["native_event_id"],
                "log_id": raw_alert_row["log_id"],
                "ts_begin": raw_alert_row["ts_begin"],
                "ts_end": raw_alert_row["ts_end"],
                "period_ts": raw_alert_row["period_ts"],
                "confidence": raw_alert_row["confidence"],
                "phase": raw_alert_row["phase"],
                "alert_ids": [raw_alert_row["alert_id"]],
            }
            grouped_alerts.append(group)
        else:
            group["ts_begin"] = min(group["ts_begin"], raw_alert_row["ts_begin"])
            group["ts_end"] = max(group["ts_end"], raw_alert_row["ts_end"])
            group["confidence"] = max(group["confidence"], raw_alert_row["confidence"])
            if group["log_id"] is None and raw_alert_row["log_id"] is not None:
                group["log_id"] = raw_alert_row["log_id"]
            if group["phase"] is None and raw_alert_row["phase"] is not None:
                group["phase"] = raw_alert_row["phase"]
            group["alert_ids"].append(raw_alert_row["alert_id"])

    for group in grouped_alerts:
        cursor.execute(
            """
            INSERT INTO alertGroups (
                endpointID,
                nativeEventID,
                logID,
                tsBegin,
                tsEnd,
                periodTs,
                confidence,
                phase
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                group["endpoint_id"],
                group["native_event_id"],
                group["log_id"],
                group["ts_begin"],
                group["ts_end"],
                group["period_ts"],
                group["confidence"],
                group["phase"],
            ),
        )
        alert_group_id = cursor.lastrowid

        for alert_id in group["alert_ids"]:
            cursor.execute(
                """
                INSERT INTO alertGroupMap (
                    alertGroupID,
                    alertID
                ) VALUES (?, ?)
                """,
                (alert_group_id, alert_id),
            )

    conn.commit()
    conn.close()
    return len(grouped_alerts)


def recompute_alerts_for_endpoint(
    endpoint_id: str,
    method: str = "fourier",
    plot: bool = False,
    show_progress: bool = False,
) -> int:
    return run_brain_for_endpoint(
        endpoint_id=endpoint_id,
        fetch_events=fetch_events_for_endpoint,
        publish_alerts=replace_alerts_for_endpoint,
        method=method,
        plot=plot,
        show_progress=show_progress,
    )


def _serialize_alert_group_row(row, windows):
    log_id = row[8] if len(row) > 8 else None
    native_event_id = row[2]
    endpoint_id = row[1]
    hostname = row[9] if len(row) > 9 else None
    display_name = row[11] if len(row) > 11 else None
    return {
        "alertID": row[0],
        "endpointID": endpoint_id,
        "nativeEventID": native_event_id,
        "eventName": resolve_event_name(log_id, native_event_id),
        "logID": log_id,
        "logSource": resolve_log_source_name(log_id),
        "tsBegin": row[3],
        "tsEnd": row[4],
        "periodTs": row[5],
        "confidence": row[6],
        "phase": row[7],
        "hostname": hostname,
        "ip": row[10] if len(row) > 10 else None,
        "name": display_name or hostname or endpoint_id,
        "windows": windows,
    }


def fetch_alerts(filters: AlertQueryFilters):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    where_clauses, params = apply_filter_rules(filters)

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    sort_column_map = {
        "alertID": "g.alertGroupID",
        "nativeEventID": "g.nativeEventID",
        "endpointID": "g.endpointID",
        "tsBegin": "g.tsBegin",
        "tsEnd": "g.tsEnd",
        "periodTs": "g.periodTs",
        "confidence": "g.confidence",
    }
    sort_column = sort_column_map.get(filters.sort_key, "g.confidence")
    if filters.sort_key == "eventName":
        sort_column = "g.nativeEventID"
    sort_direction = "ASC" if filters.sort_direction == "asc" else "DESC"

    cursor.execute(
        f"""
        SELECT
            g.alertGroupID,
            g.endpointID,
            g.nativeEventID,
            g.tsBegin,
            g.tsEnd,
            g.periodTs,
            g.confidence,
            g.phase,
            g.logID,
            e.hostname,
            e.ip,
            e.displayName,
            a.alertID,
            a.tsBegin,
            a.tsEnd,
            a.confidence,
            a.phase
        FROM alertGroups g
        LEFT JOIN endpoints e ON e.endpointID = g.endpointID
        LEFT JOIN alertGroupMap gm ON gm.alertGroupID = g.alertGroupID
        LEFT JOIN alerts a ON a.alertID = gm.alertID
        {where_sql}
        ORDER BY {sort_column} {sort_direction}, g.tsEnd DESC, a.tsBegin ASC
        """,
        params,
    )
    rows = cursor.fetchall()
    conn.close()

    grouped_alerts = {}
    for row in rows:
        alert_group_id = row[0]
        alert = grouped_alerts.get(alert_group_id)
        if alert is None:
            alert = _serialize_alert_group_row(row[:12], [])
            grouped_alerts[alert_group_id] = alert

        child_alert_id = row[12]
        if child_alert_id is not None:
            alert["windows"].append(
                {
                    "alertID": child_alert_id,
                    "tsBegin": row[13],
                    "tsEnd": row[14],
                    "confidence": row[15],
                    "phase": row[16],
                }
            )

    alerts = list(grouped_alerts.values())
    if filters.sort_key == "eventName":
        reverse = filters.sort_direction == "desc"
        alerts.sort(key=lambda item: item.get("eventName", "").lower(), reverse=reverse)
    return alerts


def _org_filter_sql(column: str, organization_id: int | None) -> tuple[str, list]:
    """Build an optional clause restricting `column` to an organization's endpoints."""
    if organization_id is None:
        return "", []
    return (
        f" AND {column} IN (SELECT endpointID FROM endpoints WHERE organizationID = ?)",
        [organization_id],
    )


def fetch_alert_detail(alert_group_id: int, organization_id: int | None = None):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    org_clause, org_params = _org_filter_sql("g.endpointID", organization_id)
    cursor.execute(
        f"""
        SELECT
            g.alertGroupID,
            g.endpointID,
            g.nativeEventID,
            g.tsBegin,
            g.tsEnd,
            g.periodTs,
            g.confidence,
            g.phase,
            g.logID,
            e.hostname,
            e.ip,
            e.displayName
        FROM alertGroups g
        LEFT JOIN endpoints e ON e.endpointID = g.endpointID
        WHERE g.alertGroupID = ?{org_clause}
        """,
        (alert_group_id, *org_params),
    )
    group_row = cursor.fetchone()
    if group_row is None:
        conn.close()
        return None

    cursor.execute(
        """
        SELECT
            a.alertID,
            a.tsBegin,
            a.tsEnd,
            a.confidence,
            a.phase
        FROM alerts a
        JOIN alertGroupMap gm ON gm.alertID = a.alertID
        WHERE gm.alertGroupID = ?
        ORDER BY a.tsBegin ASC
        """,
        (alert_group_id,),
    )
    window_rows = cursor.fetchall()

    windows = []
    stored_events = []
    representative_event = None

    for row in window_rows:
        alert_id = row[0]
        cursor.execute(
            """
            SELECT
                l.internalEventID,
                l.timestamp,
                l.logID,
                l.nativeEventID,
                l.rawPayload,
                l.parsedDetails,
                eam.confidence
            FROM eventAlertMap eam
            JOIN logs l ON l.internalEventID = eam.eventID
            WHERE eam.alertID = ?
            ORDER BY l.timestamp ASC, l.internalEventID ASC
            """,
            (alert_id,),
        )
        matched_events = []
        for event_row in cursor.fetchall():
            parsed_details = json.loads(event_row[5] or "{}")
            raw_payload = json.loads(event_row[4] or "{}")
            event_item = {
                "internalEventID": event_row[0],
                "timestamp": event_row[1],
                "logID": event_row[2],
                "nativeEventID": event_row[3],
                "parsedDetails": parsed_details,
                "identity": parsed_details.get("identity", {}),
                "rawPayload": raw_payload,
                "matchConfidence": int(event_row[6]),
            }
            matched_events.append(event_item)
            stored_events.append(event_item)
            if representative_event is None:
                representative_event = event_item

        windows.append(
            {
                "alertID": alert_id,
                "tsBegin": row[1],
                "tsEnd": row[2],
                "confidence": row[3],
                "phase": row[4],
                "matchedEvents": matched_events,
                "matchedEventCount": len(matched_events),
            }
        )

    endpoint_id = group_row[1]
    native_event_id = group_row[2]
    activity_min_ms = min(
        int(group_row[3]),
        *(int(row[1]) for row in window_rows),
    )
    activity_max_ms = max(
        int(group_row[4]),
        *(int(row[2]) for row in window_rows),
    )

    cursor.execute(
        """
        SELECT MIN(timestamp), MAX(timestamp)
        FROM logs
        WHERE endpointID = ?
          AND nativeEventID = ?
        """,
        (endpoint_id, native_event_id),
    )
    log_bounds = cursor.fetchone()
    log_min_ms = int(log_bounds[0]) if log_bounds and log_bounds[0] is not None else None
    log_max_ms = int(log_bounds[1]) if log_bounds and log_bounds[1] is not None else None

    overview_context = _compute_overview_time_span(
        log_min_ms=log_min_ms,
        log_max_ms=log_max_ms,
        activity_min_ms=activity_min_ms,
        activity_max_ms=activity_max_ms,
    )

    conn.close()

    log_id = group_row[8]

    deduped_events: dict[int, dict] = {}
    for event_item in stored_events:
        event_id = event_item["internalEventID"]
        existing = deduped_events.get(event_id)
        if existing is None or event_item["matchConfidence"] > existing["matchConfidence"]:
            deduped_events[event_id] = event_item
    stored_events = sorted(deduped_events.values(), key=lambda item: (item["timestamp"], item["internalEventID"]))

    alert = _serialize_alert_group_row(group_row, windows)
    if not alert.get("hostname") or not alert.get("ip"):
        for event_item in stored_events:
            hostname, ip = extract_endpoint_agent_metadata(event_item.get("rawPayload") or {})
            if not alert.get("hostname") and hostname:
                alert["hostname"] = hostname
            if not alert.get("ip") and ip:
                alert["ip"] = ip
            if alert.get("hostname") and alert.get("ip"):
                break

    alert["representativeEvent"] = representative_event
    alert["contributingEvents"] = stored_events
    alert["contributingEventCount"] = len(stored_events)
    alert["eventDetails"] = representative_event["parsedDetails"] if representative_event else None
    alert["overviewContext"] = overview_context
    return alert


def _effective_query_window(
    cursor,
    window_start_ms: int | None,
    window_end_ms: int | None,
    now_ms: int,
) -> tuple[int, int]:
    if window_start_ms is not None and window_end_ms is not None:
        return int(window_start_ms), int(window_end_ms)

    cursor.execute("SELECT MIN(tsBegin), MAX(tsEnd) FROM alertGroups")
    row = cursor.fetchone()
    if row and row[0] is not None and row[1] is not None and int(row[1]) > int(row[0]):
        return int(row[0]), int(row[1])

    return int((datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc) - timedelta(days=7)).timestamp() * 1000), now_ms


def _timeline_buckets(window_start_ms: int, window_end_ms: int) -> list[dict]:
    span_ms = window_end_ms - window_start_ms
    if span_ms <= 0:
        return []

    one_day_ms = 24 * 60 * 60 * 1000
    start_dt = datetime.fromtimestamp(window_start_ms / 1000, tz=timezone.utc)
    end_dt = datetime.fromtimestamp(window_end_ms / 1000, tz=timezone.utc)

    if span_ms <= 14 * one_day_ms:
        bucket_start = start_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        buckets: list[dict] = []
        while bucket_start.timestamp() * 1000 <= window_end_ms:
            bucket_end_dt = bucket_start + timedelta(days=1) - timedelta(milliseconds=1)
            buckets.append(
                {
                    "bucketStart": int(bucket_start.timestamp() * 1000),
                    "bucketEnd": int(bucket_end_dt.timestamp() * 1000),
                    "label": bucket_start.strftime("%a %m/%d"),
                }
            )
            bucket_start += timedelta(days=1)
        return buckets[-14:] if len(buckets) > 14 else buckets

    bucket_start = start_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    buckets = []
    while bucket_start.timestamp() * 1000 <= window_end_ms:
        bucket_end_dt = bucket_start + timedelta(days=7) - timedelta(milliseconds=1)
        buckets.append(
            {
                "bucketStart": int(bucket_start.timestamp() * 1000),
                "bucketEnd": int(bucket_end_dt.timestamp() * 1000),
                "label": bucket_start.strftime("%b %d"),
            }
        )
        bucket_start += timedelta(days=7)
    return buckets[-12:] if len(buckets) > 12 else buckets


def fetch_entities(
    window_start_ms: int | None = None,
    window_end_ms: int | None = None,
    time_preset: str = "last_week",
    organization_id: int | None = None,
):
    now = datetime.now(timezone.utc)
    now_ms = int(now.timestamp() * 1000)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    effective_start_ms, effective_end_ms = _effective_query_window(cursor, window_start_ms, window_end_ms, now_ms)

    if organization_id is not None:
        base_sql = "SELECT endpointID, displayName, hostname, ip, lastSeenAt FROM endpoints WHERE organizationID = ?"
        base_params: tuple = (organization_id,)
    else:
        base_sql = """
            SELECT endpointID, displayName, hostname, ip, lastSeenAt FROM endpoints
            UNION
            SELECT DISTINCT g.endpointID, NULL, NULL, NULL, NULL
            FROM alertGroups g
            WHERE g.endpointID NOT IN (SELECT endpointID FROM endpoints)
        """
        base_params = ()

    counts_clause, counts_params = _org_filter_sql("endpointID", organization_id)
    cursor.execute(
        f"""
        SELECT
            base.endpointID,
            base.displayName,
            base.hostname,
            base.ip,
            COALESCE(base.lastSeenAt, log_stats.latestLogTs, alert_stats.latestAlertEnd) AS lastSeenAt,
            COALESCE(counts.alertCount, 0) AS alertsLastWeek
        FROM (
            {base_sql}
        ) base
        LEFT JOIN (
            SELECT endpointID, MAX(timestamp) AS latestLogTs
            FROM logs
            GROUP BY endpointID
        ) log_stats ON log_stats.endpointID = base.endpointID
        LEFT JOIN (
            SELECT endpointID, MAX(tsEnd) AS latestAlertEnd
            FROM alertGroups
            GROUP BY endpointID
        ) alert_stats ON alert_stats.endpointID = base.endpointID
        LEFT JOIN (
            SELECT endpointID, COUNT(*) AS alertCount
            FROM alertGroups
            WHERE tsBegin <= ?
              AND tsEnd >= ?{counts_clause}
            GROUP BY endpointID
        ) counts ON counts.endpointID = base.endpointID
        ORDER BY alertsLastWeek DESC, base.endpointID ASC
        """,
        (*base_params, effective_end_ms, effective_start_ms, *counts_params),
    )
    rows = cursor.fetchall()
    conn.close()

    entities = []
    for endpoint_id, display_name, hostname, ip, last_seen_at, alerts_last_week in rows:
        entities.append(
            {
                "endpointID": endpoint_id,
                "name": display_name or hostname,
                "ip": ip,
                "lastSeenAt": int(last_seen_at) if last_seen_at is not None else None,
                "alertsLastWeek": int(alerts_last_week),
                "alertCount": int(alerts_last_week),
            }
        )

    return {
        "windowStart": effective_start_ms,
        "windowEnd": effective_end_ms,
        "timePreset": time_preset,
        "entities": entities,
    }


def _count_overlapping_alert_groups(
    cursor,
    window_start_ms: int,
    window_end_ms: int,
    organization_id: int | None = None,
) -> int:
    org_clause, org_params = _org_filter_sql("endpointID", organization_id)
    cursor.execute(
        f"""
        SELECT COUNT(*)
        FROM alertGroups
        WHERE tsBegin <= ?
          AND tsEnd >= ?{org_clause}
        """,
        (window_end_ms, window_start_ms, *org_params),
    )
    row = cursor.fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def fetch_dashboard_stats(
    window_start_ms: int | None = None,
    window_end_ms: int | None = None,
    time_preset: str = "last_week",
    organization_id: int | None = None,
):
    now = datetime.now(timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    last_24h_start_ms = int((now - timedelta(hours=24)).timestamp() * 1000)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    effective_start_ms, effective_end_ms = _effective_query_window(cursor, window_start_ms, window_end_ms, now_ms)

    active_now = _count_overlapping_alert_groups(cursor, now_ms, now_ms, organization_id)
    active_last_24h = _count_overlapping_alert_groups(cursor, last_24h_start_ms, now_ms, organization_id)
    active_in_window = _count_overlapping_alert_groups(cursor, effective_start_ms, effective_end_ms, organization_id)

    org_g_clause, org_g_params = _org_filter_sql("g.endpointID", organization_id)
    org_clause, org_params = _org_filter_sql("endpointID", organization_id)

    cursor.execute(
        f"""
        SELECT COUNT(*)
        FROM alertGroups
        WHERE tsBegin <= ?
          AND tsEnd >= ?
          AND confidence >= 80{org_clause}
        """,
        (effective_end_ms, effective_start_ms, *org_params),
    )
    high_confidence_in_window = int(cursor.fetchone()[0])

    timeline = []
    for bucket in _timeline_buckets(effective_start_ms, effective_end_ms):
        count = _count_overlapping_alert_groups(cursor, bucket["bucketStart"], bucket["bucketEnd"], organization_id)
        timeline.append({**bucket, "count": count})

    cursor.execute(
        f"""
        SELECT nativeEventID, logID, COUNT(*) AS alertCount
        FROM alertGroups
        WHERE tsBegin <= ?
          AND tsEnd >= ?{org_clause}
        GROUP BY nativeEventID, logID
        ORDER BY alertCount DESC, nativeEventID ASC
        LIMIT 8
        """,
        (effective_end_ms, effective_start_ms, *org_params),
    )
    top_events = []
    for native_event_id, log_id, alert_count in cursor.fetchall():
        top_events.append(
            {
                "nativeEventID": native_event_id,
                "eventName": resolve_event_name(log_id, native_event_id),
                "alertCount": int(alert_count),
            }
        )

    cursor.execute(
        f"""
        SELECT
            g.endpointID,
            COALESCE(e.displayName, e.hostname) AS name,
            COUNT(*) AS alertCount
        FROM alertGroups g
        LEFT JOIN endpoints e ON e.endpointID = g.endpointID
        WHERE g.tsBegin <= ?
          AND g.tsEnd >= ?{org_g_clause}
        GROUP BY g.endpointID, name
        ORDER BY alertCount DESC, g.endpointID ASC
        LIMIT 5
        """,
        (effective_end_ms, effective_start_ms, *org_g_params),
    )
    top_endpoints = []
    for endpoint_id, hostname, alert_count in cursor.fetchall():
        top_endpoints.append(
            {
                "endpointID": endpoint_id,
                "name": hostname,
                "alertCount": int(alert_count),
            }
        )

    cursor.execute(
        f"""
        SELECT
            g.alertGroupID,
            g.endpointID,
            g.nativeEventID,
            g.logID,
            g.confidence,
            g.tsBegin,
            g.tsEnd,
            COALESCE(e.displayName, e.hostname) AS name
        FROM alertGroups g
        LEFT JOIN endpoints e ON e.endpointID = g.endpointID
        WHERE g.confidence >= 80
          AND g.tsBegin <= ?
          AND g.tsEnd >= ?{org_g_clause}
        ORDER BY g.confidence DESC, g.tsEnd DESC, g.alertGroupID DESC
        LIMIT 5
        """,
        (effective_end_ms, effective_start_ms, *org_g_params),
    )
    recent_high_confidence_alerts = []
    for row in cursor.fetchall():
        alert_group_id, endpoint_id, native_event_id, log_id, confidence, ts_begin, ts_end, hostname = row
        recent_high_confidence_alerts.append(
            {
                "alertID": alert_group_id,
                "endpointID": endpoint_id,
                "name": hostname,
                "nativeEventID": native_event_id,
                "eventName": resolve_event_name(log_id, native_event_id),
                "confidence": int(confidence),
                "tsBegin": ts_begin,
                "tsEnd": ts_end,
            }
        )

    conn.close()

    return {
        "generatedAt": now_ms,
        "windowStart": effective_start_ms,
        "windowEnd": effective_end_ms,
        "timePreset": time_preset,
        "summary": {
            "activeNow": active_now,
            "activeLast24h": active_last_24h,
            "activeLastWeek": active_in_window,
            "activeInWindow": active_in_window,
            "highConfidenceLastWeek": high_confidence_in_window,
            "highConfidenceInWindow": high_confidence_in_window,
        },
        "timeline": timeline,
        "topEvents": top_events,
        "topEndpoints": top_endpoints,
        "recentHighConfidenceAlerts": recent_high_confidence_alerts,
    }
