import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from alert_filters import AlertQueryFilters, apply_filter_rules
from alert_whitelist import filter_alerts_against_whitelist, is_alert_whitelisted
from auth import (
    generate_endpoint_id,
    generate_endpoint_secret,
    hash_secret,
    verify_secret,
)
from brain import (
    AlertRecord,
    EventRecord,
    build_alerts_for_endpoint_incremental,
    run_brain_for_endpoint,
)
from config import DB_PATH, INGESTION_CONFIG, PHASE_GHOST_SUPPRESSION_ENABLED, PHASE_GHOST_SUPPRESSION_SIMILARITY_THRESHOLD
from event_matching import MatchedEvent, periods_near_match
from event_parsers.common import extract_endpoint_agent_metadata
from ingestion_models import InsertEventsResult, IncrementalAnalysisResult, NativeEventImpact
from log_registry import resolve_event_name, resolve_log_source_name

_DB_WRITE_LOCK = threading.Lock()


def connect_db() -> sqlite3.Connection:
    """Open SQLite with a busy timeout so concurrent readers/writers can retry."""
    return sqlite3.connect(DB_PATH, timeout=INGESTION_CONFIG.sqlite_busy_timeout_sec)


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
    conn = connect_db()
    conn.execute("PRAGMA journal_mode=WAL")
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
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS alertWhitelist (
            whitelistID INTEGER PRIMARY KEY AUTOINCREMENT,
            organizationID INTEGER NOT NULL,
            endpointID TEXT,
            logID INTEGER,
            nativeEventID INTEGER NOT NULL,
            seriesKey TEXT NOT NULL DEFAULT '',
            periodMs REAL,
            note TEXT NOT NULL DEFAULT '',
            seriesIdentityJson TEXT NOT NULL DEFAULT '{}',
            createdByAccountID INTEGER,
            createdAt INTEGER NOT NULL,
            updatedAt INTEGER NOT NULL
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_alert_whitelist_org
        ON alertWhitelist(organizationID, nativeEventID)
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
    _ensure_column(cursor=conn.cursor(), table_name="alerts", column_definition="seriesKey TEXT NOT NULL DEFAULT ''")
    conn.commit()
    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_logs_endpoint_log_event_ts
        ON logs(endpointID, logID, nativeEventID, timestamp)
        """
    )
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
    conn = connect_db()
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
    name = (name or "").strip()
    if not name:
        return None
    conn = connect_db()
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
    conn = connect_db()
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
    result = attempt_login(organization_name, account_name, password)
    return result.get("account")


def attempt_login(organization_name: str, account_name: str, password: str) -> dict:
    """Validate login credentials with specific failure reasons.

    Returns ``{"account": dict}`` on success, or
    ``{"error": str, "code": str, "status": int}`` on failure.
    """
    organization_name = (organization_name or "").strip()
    account_name = (account_name or "").strip()
    password = password or ""
    if not organization_name or not account_name or not password:
        return {
            "error": "Organization name, username, and password are all required.",
            "code": "MISSING_FIELDS",
            "status": 400,
        }

    organization = get_organization_by_name(organization_name)
    if organization is None:
        return {
            "error": "Organization not found. Check the spelling or create a new organization.",
            "code": "ORG_NOT_FOUND",
            "status": 404,
        }

    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT accountID, organizationID, name, isAdmin, isSuperAdmin,
               createdAt, createdByAccountID, passwordHash
        FROM accounts
        WHERE organizationID = ? AND name = ? COLLATE NOCASE
        """,
        (organization["organizationID"], account_name),
    )
    row = cursor.fetchone()
    conn.close()
    if row is None:
        return {
            "error": "Username not found in this organization.",
            "code": "USER_NOT_FOUND",
            "status": 401,
        }
    if not verify_secret(password, row[7]):
        return {
            "error": "Incorrect password.",
            "code": "BAD_PASSWORD",
            "status": 401,
        }
    return {"account": _serialize_account_row(row[:7])}


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

    conn = connect_db()
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
    conn = connect_db()
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

    conn = connect_db()
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
    conn = connect_db()
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
    conn = connect_db()
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
    display_name = (display_name or "").strip()
    if not display_name:
        raise ValueError("Endpoint display name is required.")
    secret = generate_endpoint_secret()

    conn = connect_db()
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
    conn = connect_db()
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
    conn = connect_db()
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
    conn = connect_db()
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
    conn = connect_db()
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
    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute("SELECT organizationID FROM endpoints WHERE endpointID = ?", (endpoint_id,))
    row = cursor.fetchone()
    conn.close()
    if row is None or row[0] is None:
        return None
    return int(row[0])


def verify_endpoint_secret(endpoint_id: str, secret: str) -> bool:
    """Return True when the endpoint exists, is registered, and the secret matches."""
    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute("SELECT secretHash FROM endpoints WHERE endpointID = ?", (endpoint_id,))
    row = cursor.fetchone()
    conn.close()
    if row is None:
        return False
    return verify_secret(secret, row[0])


def upsert_endpoint(endpoint_id: str, hostname: str | None, ip: str | None, last_seen_at: int) -> None:
    conn = connect_db()
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


def get_max_log_timestamp(endpoint_id: str, log_id: int) -> int | None:
    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT MAX(timestamp)
        FROM logs
        WHERE endpointID = ? AND logID = ?
        """,
        (endpoint_id, log_id),
    )
    row = cursor.fetchone()
    conn.close()
    if row is None or row[0] is None:
        return None
    return int(row[0])


def _log_event_exists(
    cursor,
    endpoint_id: str,
    log_id: int,
    native_event_id: int,
    timestamp_ms: int,
) -> bool:
    cursor.execute(
        """
        SELECT 1
        FROM logs
        WHERE endpointID = ? AND logID = ? AND nativeEventID = ? AND timestamp = ?
        LIMIT 1
        """,
        (endpoint_id, log_id, native_event_id, timestamp_ms),
    )
    return cursor.fetchone() is not None


def insert_events(endpoint_id: str, log_id: int, events) -> InsertEventsResult:
    with _DB_WRITE_LOCK:
        conn = connect_db()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT MAX(internalEventID) FROM logs WHERE endpointID = ?",
            (endpoint_id,),
        )
        max_id_row = cursor.fetchone()
        next_internal_event_id = 0 if max_id_row[0] is None else max_id_row[0] + 1

        cursor.execute(
            """
            SELECT MAX(timestamp)
            FROM logs
            WHERE endpointID = ? AND logID = ?
            """,
            (endpoint_id, log_id),
        )
        watermark_row = cursor.fetchone()
        watermark = None if watermark_row is None or watermark_row[0] is None else int(watermark_row[0])
        overlap_buffer_ms = INGESTION_CONFIG.overlap_buffer_ms
        cutoff_ms = watermark - overlap_buffer_ms if watermark is not None else None

        existing_keys: set[tuple[int, int]] = set()
        if cutoff_ms is not None:
            cursor.execute(
                """
                SELECT nativeEventID, timestamp
                FROM logs
                WHERE endpointID = ? AND logID = ? AND timestamp >= ?
                """,
                (endpoint_id, log_id, cutoff_ms),
            )
            existing_keys = {(int(row[0]), int(row[1])) for row in cursor.fetchall()}

        latest_timestamp = 0
        endpoint_hostname = None
        endpoint_ip = None
        skipped_count = 0
        impact_bounds: dict[tuple[int, str], dict[str, int]] = {}
        rows_to_insert: list[tuple] = []

        for event in events:
            timestamp_ms = event["timestamp_ms"]
            native_event_id = event["native_event_id"]
            if not isinstance(timestamp_ms, int):
                conn.close()
                raise TypeError("timestamp_ms must be int")

            if cutoff_ms is not None and timestamp_ms < cutoff_ms:
                skipped_count += 1
                continue

            dedupe_key = (native_event_id, timestamp_ms)
            if dedupe_key in existing_keys:
                skipped_count += 1
                continue
            existing_keys.add(dedupe_key)

            raw_payload = event.get("raw_payload") or {}
            parsed_details = event.get("parsed_details") or {}
            series_key = str(parsed_details.get("seriesKey") or "")
            agent_hostname, agent_ip = extract_endpoint_agent_metadata(raw_payload)
            hostname = event.get("hostname") or agent_hostname
            ip = event.get("ip") or agent_ip

            if hostname and not endpoint_hostname:
                endpoint_hostname = hostname
            if ip:
                endpoint_ip = ip
            latest_timestamp = max(latest_timestamp, timestamp_ms)

            rows_to_insert.append(
                (
                    endpoint_id,
                    next_internal_event_id,
                    timestamp_ms,
                    log_id,
                    native_event_id,
                    native_event_id,
                    json.dumps(raw_payload),
                    json.dumps(parsed_details),
                )
            )
            next_internal_event_id += 1

            bounds = impact_bounds.setdefault(
                (native_event_id, series_key),
                {"min": timestamp_ms, "max": timestamp_ms, "count": 0},
            )
            bounds["min"] = min(bounds["min"], timestamp_ms)
            bounds["max"] = max(bounds["max"], timestamp_ms)
            bounds["count"] += 1

        if rows_to_insert:
            cursor.executemany(
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
                rows_to_insert,
            )

        inserted_count = len(rows_to_insert)

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

    impacts = [
        NativeEventImpact(
            native_event_id=native_id,
            series_key=series_key,
            new_min_ms=bounds["min"],
            new_max_ms=bounds["max"],
            new_event_count=bounds["count"],
        )
        for (native_id, series_key), bounds in impact_bounds.items()
    ]
    return InsertEventsResult(
        inserted_count=inserted_count,
        skipped_count=skipped_count,
        impacts=impacts,
    )


def fetch_events_for_endpoint(endpoint_id: str) -> list[EventRecord]:
    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT internalEventID, nativeEventID, timestamp, parsedDetails
        FROM logs
        WHERE endpointID = ?
        ORDER BY timestamp
        """,
        (endpoint_id,),
    )
    rows = cursor.fetchall()
    conn.close()

    events: list[EventRecord] = []
    for internal_event_id, native_event_id, timestamp, parsed_details_text in rows:
        series_key = ""
        if parsed_details_text:
            try:
                parsed_details = json.loads(parsed_details_text)
                series_key = str(parsed_details.get("seriesKey") or "")
            except json.JSONDecodeError:
                series_key = ""
        events.append(
            EventRecord(
                internal_event_id=internal_event_id,
                native_event_id=native_event_id,
                timestamp_ms=timestamp,
                series_key=series_key,
            )
        )
    return events


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


def fetch_preserved_alert_records(
    endpoint_id: str,
    native_event_id: int,
    impact_min_ms: int,
    impact_max_ms: int,
    *,
    series_key: str = "",
) -> list[AlertRecord]:
    """Return existing window alerts that do not overlap the new-event impact range."""
    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT alertID, tsBegin, tsEnd, periodTs, confidence, phase, seriesKey
        FROM alerts
        WHERE endpointID = ?
          AND nativeEventID = ?
          AND seriesKey = ?
          AND NOT (tsBegin <= ? AND tsEnd >= ?)
        ORDER BY tsBegin ASC, tsEnd ASC, alertID ASC
        """,
        (endpoint_id, native_event_id, series_key, impact_max_ms, impact_min_ms),
    )
    rows = cursor.fetchall()

    preserved: list[AlertRecord] = []
    for alert_id, ts_begin, ts_end, period_ts, confidence, phase, alert_series_key in rows:
        cursor.execute(
            """
            SELECT eventID, confidence
            FROM eventAlertMap
            WHERE alertID = ?
            ORDER BY eventID ASC
            """,
            (alert_id,),
        )
        matched_rows = cursor.fetchall()
        matched_events = []
        for event_id, match_confidence in matched_rows:
            cursor.execute(
                """
                SELECT timestamp
                FROM logs
                WHERE endpointID = ? AND internalEventID = ?
                """,
                (endpoint_id, event_id),
            )
            ts_row = cursor.fetchone()
            if ts_row is None:
                continue
            matched_events.append(
                MatchedEvent(
                    internal_event_id=int(event_id),
                    timestamp_ms=int(ts_row[0]),
                    confidence=int(match_confidence),
                )
            )

        preserved.append(
            AlertRecord(
                endpoint_id=endpoint_id,
                native_event_id=native_event_id,
                matched_events=tuple(matched_events),
                series_key=str(alert_series_key or ""),
                ts_begin=int(ts_begin),
                ts_end=int(ts_end),
                period_ts=float(period_ts),
                confidence=int(confidence),
                phase=float(phase) if phase is not None else float("nan"),
            )
        )

    conn.close()
    return preserved


def _group_raw_alert_rows(raw_alert_rows: list[dict]) -> list[dict]:
    grouped_alerts: list[dict] = []

    def find_matching_group(raw_alert_row: dict):
        for group in grouped_alerts:
            if group["endpoint_id"] != raw_alert_row["endpoint_id"]:
                continue
            if group["native_event_id"] != raw_alert_row["native_event_id"]:
                continue
            if group.get("series_key", "") != raw_alert_row.get("series_key", ""):
                continue
            if not periods_near_match(group["period_ts"], raw_alert_row["period_ts"]):
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
                "series_key": raw_alert_row.get("series_key", ""),
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
            if raw_alert_row["confidence"] >= group["confidence"]:
                group["period_ts"] = raw_alert_row["period_ts"]
                if raw_alert_row["phase"] is not None:
                    group["phase"] = raw_alert_row["phase"]
            group["confidence"] = max(group["confidence"], raw_alert_row["confidence"])
            if group["log_id"] is None and raw_alert_row["log_id"] is not None:
                group["log_id"] = raw_alert_row["log_id"]
            if group["phase"] is None and raw_alert_row["phase"] is not None:
                group["phase"] = raw_alert_row["phase"]
            group["alert_ids"].append(raw_alert_row["alert_id"])

    return grouped_alerts


def _insert_alert_groups(cursor, grouped_alerts: list[dict]) -> None:
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


def _insert_alert_records(cursor, alerts) -> list[dict]:
    raw_alert_rows: list[dict] = []

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
                phase,
                seriesKey
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                alert.series_key,
            ),
        )
        alert_id = cursor.lastrowid
        raw_alert_rows.append(
            {
                "alert_id": alert_id,
                "endpoint_id": alert.endpoint_id,
                "native_event_id": alert.native_event_id,
                "series_key": alert.series_key,
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

    return raw_alert_rows


def _delete_alert_groups_for_endpoint(cursor, endpoint_id: str) -> None:
    cursor.execute(
        "DELETE FROM alertGroupMap WHERE alertGroupID IN (SELECT alertGroupID FROM alertGroups WHERE endpointID = ?)",
        (endpoint_id,),
    )
    cursor.execute("DELETE FROM alertGroups WHERE endpointID = ?", (endpoint_id,))


def replace_alerts_for_affected_series(
    endpoint_id: str,
    alerts,
    affected_series_keys: set[tuple[int, str]],
) -> int:
    if not affected_series_keys:
        return 0

    with _DB_WRITE_LOCK:
        conn = connect_db()
        cursor = conn.cursor()

        for native_event_id, series_key in sorted(affected_series_keys):
            cursor.execute(
                """
                DELETE FROM eventAlertMap
                WHERE alertID IN (
                    SELECT alertID FROM alerts
                    WHERE endpointID = ? AND nativeEventID = ? AND seriesKey = ?
                )
                """,
                (endpoint_id, native_event_id, series_key),
            )
            cursor.execute(
                """
                DELETE FROM alerts
                WHERE endpointID = ? AND nativeEventID = ? AND seriesKey = ?
                """,
                (endpoint_id, native_event_id, series_key),
            )

        _delete_alert_groups_for_endpoint(cursor, endpoint_id)

        _insert_alert_records(cursor, alerts)

        cursor.execute(
            """
            SELECT alertID, endpointID, nativeEventID, logID, tsBegin, tsEnd, periodTs, confidence, phase, seriesKey
            FROM alerts
            WHERE endpointID = ?
            ORDER BY nativeEventID ASC, seriesKey ASC, tsBegin ASC, tsEnd ASC, alertID ASC
            """,
            (endpoint_id,),
        )
        all_rows = cursor.fetchall()
        all_raw_rows = [
            {
                "alert_id": row[0],
                "endpoint_id": row[1],
                "native_event_id": row[2],
                "log_id": row[3],
                "ts_begin": row[4],
                "ts_end": row[5],
                "period_ts": row[6],
                "confidence": row[7],
                "phase": row[8],
                "series_key": row[9] or "",
            }
            for row in all_rows
        ]

        grouped_alerts = _group_raw_alert_rows(all_raw_rows)
        _insert_alert_groups(cursor, grouped_alerts)

        conn.commit()
        conn.close()
        return len(grouped_alerts)


def replace_alerts_for_native_event_types(
    endpoint_id: str,
    alerts,
    affected_native_event_ids: set[int],
) -> int:
    affected_series_keys = {
        (alert.native_event_id, alert.series_key)
        for alert in alerts
    }
    if not affected_series_keys and affected_native_event_ids:
        affected_series_keys = {(native_event_id, "") for native_event_id in affected_native_event_ids}
    return replace_alerts_for_affected_series(endpoint_id, alerts, affected_series_keys)


def incremental_recompute_alerts_for_endpoint(
    endpoint_id: str,
    impacts: list[NativeEventImpact],
    method: str = "fourier",
    plot: bool = False,
    show_progress: bool = False,
) -> IncrementalAnalysisResult:
    import time

    started = time.monotonic()
    if not impacts:
        return IncrementalAnalysisResult(
            endpoint_id=endpoint_id,
            method=method,
            event_types_analyzed=[],
            new_events_queued=0,
            total_events_loaded=0,
            affected_type_events_loaded=0,
            preserved_windows_kept=0,
            alert_windows_written=0,
            alert_groups_total=0,
            elapsed_sec=0.0,
        )

    affected_series_keys = {impact.impact_key for impact in impacts}
    affected_native_event_ids = {impact.native_event_id for impact in impacts}
    preserved_by_series: dict[tuple[int, str], list[AlertRecord]] = {}
    preserved_windows_kept = 0
    for impact in impacts:
        preserved = fetch_preserved_alert_records(
            endpoint_id,
            impact.native_event_id,
            impact.new_min_ms,
            impact.new_max_ms,
            series_key=impact.series_key,
        )
        preserved_by_series[impact.impact_key] = preserved
        preserved_windows_kept += len(preserved)

    events = fetch_events_for_endpoint(endpoint_id)
    affected_type_events_loaded = sum(
        1
        for event in events
        if (event.native_event_id, event.series_key) in affected_series_keys
    )
    new_events_queued = sum(impact.new_event_count for impact in impacts)

    new_alerts = build_alerts_for_endpoint_incremental(
        endpoint_id,
        events,
        impacts,
        preserved_by_series,
        method=method,
        plot=plot,
        show_progress=show_progress,
    )
    alert_groups_total = replace_alerts_for_affected_series(
        endpoint_id,
        new_alerts,
        affected_series_keys,
    )

    return IncrementalAnalysisResult(
        endpoint_id=endpoint_id,
        method=method,
        event_types_analyzed=sorted(affected_native_event_ids),
        new_events_queued=new_events_queued,
        total_events_loaded=len(events),
        affected_type_events_loaded=affected_type_events_loaded,
        preserved_windows_kept=preserved_windows_kept,
        alert_windows_written=len(new_alerts),
        alert_groups_total=alert_groups_total,
        elapsed_sec=time.monotonic() - started,
    )


def replace_alerts_for_endpoint(endpoint_id: str, alerts) -> int:
    with _DB_WRITE_LOCK:
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

        raw_alert_rows = _insert_alert_records(cursor, alerts)
        grouped_alerts = _group_raw_alert_rows(raw_alert_rows)
        _insert_alert_groups(cursor, grouped_alerts)

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


def _serialize_whitelist_row(row) -> dict:
    series_identity = {}
    raw_identity = row[8] if len(row) > 8 else "{}"
    try:
        parsed = json.loads(raw_identity or "{}")
        if isinstance(parsed, dict):
            series_identity = parsed
    except json.JSONDecodeError:
        series_identity = {}

    return {
        "whitelistID": int(row[0]),
        "organizationID": int(row[1]),
        "endpointID": row[2],
        "logID": int(row[3]) if row[3] is not None else None,
        "nativeEventID": int(row[4]),
        "seriesKey": row[5] or "",
        "periodMs": float(row[6]) if row[6] is not None else None,
        "note": row[7] or "",
        "seriesIdentity": series_identity,
        "createdByAccountID": int(row[9]) if row[9] is not None else None,
        "createdAt": int(row[10]),
        "updatedAt": int(row[11]),
        "endpointName": row[12] if len(row) > 12 else None,
        "createdByName": row[13] if len(row) > 13 else None,
        "eventName": resolve_event_name(
            int(row[3]) if row[3] is not None else None,
            int(row[4]),
        ),
        "logSource": resolve_log_source_name(int(row[3]) if row[3] is not None else None),
        "scope": "organization" if row[2] is None else "endpoint",
    }


def list_alert_whitelist(organization_id: int) -> list[dict]:
    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT
            w.whitelistID,
            w.organizationID,
            w.endpointID,
            w.logID,
            w.nativeEventID,
            w.seriesKey,
            w.periodMs,
            w.note,
            w.seriesIdentityJson,
            w.createdByAccountID,
            w.createdAt,
            w.updatedAt,
            COALESCE(e.displayName, e.hostname),
            a.name
        FROM alertWhitelist w
        LEFT JOIN endpoints e ON e.endpointID = w.endpointID
        LEFT JOIN accounts a ON a.accountID = w.createdByAccountID
        WHERE w.organizationID = ?
        ORDER BY w.createdAt DESC, w.whitelistID DESC
        """,
        (organization_id,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [_serialize_whitelist_row(row) for row in rows]


def get_alert_whitelist_entry(whitelist_id: int, organization_id: int) -> dict | None:
    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT
            w.whitelistID,
            w.organizationID,
            w.endpointID,
            w.logID,
            w.nativeEventID,
            w.seriesKey,
            w.periodMs,
            w.note,
            w.seriesIdentityJson,
            w.createdByAccountID,
            w.createdAt,
            w.updatedAt,
            COALESCE(e.displayName, e.hostname),
            a.name
        FROM alertWhitelist w
        LEFT JOIN endpoints e ON e.endpointID = w.endpointID
        LEFT JOIN accounts a ON a.accountID = w.createdByAccountID
        WHERE w.whitelistID = ? AND w.organizationID = ?
        """,
        (whitelist_id, organization_id),
    )
    row = cursor.fetchone()
    conn.close()
    if row is None:
        return None
    return _serialize_whitelist_row(row)


def _find_duplicate_whitelist_id(
    cursor,
    *,
    organization_id: int,
    endpoint_id: str | None,
    log_id: int | None,
    native_event_id: int,
    series_key: str,
    period_ms: float | None,
) -> int | None:
    cursor.execute(
        """
        SELECT whitelistID, endpointID, logID, periodMs
        FROM alertWhitelist
        WHERE organizationID = ?
          AND nativeEventID = ?
          AND seriesKey = ?
        """,
        (organization_id, native_event_id, series_key),
    )
    for whitelist_id, existing_endpoint, existing_log_id, existing_period in cursor.fetchall():
        if (existing_endpoint is None) != (endpoint_id is None):
            continue
        if endpoint_id is not None and str(existing_endpoint) != str(endpoint_id):
            continue
        if (existing_log_id is None) != (log_id is None):
            continue
        if log_id is not None and int(existing_log_id) != int(log_id):
            continue
        if (existing_period is None) != (period_ms is None):
            continue
        if period_ms is not None and not periods_near_match(float(existing_period), float(period_ms)):
            continue
        return int(whitelist_id)
    return None


def create_alert_whitelist_entry(
    *,
    organization_id: int,
    created_by_account_id: int,
    endpoint_id: str | None,
    log_id: int | None,
    native_event_id: int,
    series_key: str,
    period_ms: float | None,
    note: str,
    series_identity: dict | None = None,
) -> dict:
    from event_series import compute_series_key, extract_series_identity

    note_text = (note or "").strip()
    if not note_text:
        raise ValueError("A note is required when whitelisting a pattern.")

    resolved_identity = series_identity if isinstance(series_identity, dict) else {}
    if log_id is not None and resolved_identity:
        resolved_identity = extract_series_identity(int(log_id), int(native_event_id), resolved_identity)
    resolved_key = (series_key or "").strip()
    if not resolved_key and log_id is not None:
        resolved_key = compute_series_key(int(log_id), int(native_event_id), resolved_identity or series_identity)

    if endpoint_id is not None:
        conn = connect_db()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT endpointID FROM endpoints
            WHERE endpointID = ? AND organizationID = ?
            """,
            (endpoint_id, organization_id),
        )
        if cursor.fetchone() is None:
            conn.close()
            raise ValueError("Endpoint not found in this organization.")
        conn.close()

    now_ms = _now_ms()
    identity_json = json.dumps(resolved_identity or {}, separators=(",", ":"), sort_keys=True)

    with _DB_WRITE_LOCK:
        conn = connect_db()
        cursor = conn.cursor()
        duplicate_id = _find_duplicate_whitelist_id(
            cursor,
            organization_id=organization_id,
            endpoint_id=endpoint_id,
            log_id=log_id,
            native_event_id=native_event_id,
            series_key=resolved_key,
            period_ms=period_ms,
        )
        if duplicate_id is not None:
            conn.close()
            raise ValueError("An equivalent whitelist entry already exists.")

        cursor.execute(
            """
            INSERT INTO alertWhitelist (
                organizationID, endpointID, logID, nativeEventID, seriesKey,
                periodMs, note, seriesIdentityJson, createdByAccountID, createdAt, updatedAt
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                organization_id,
                endpoint_id,
                log_id,
                int(native_event_id),
                resolved_key,
                period_ms,
                note_text,
                identity_json,
                created_by_account_id,
                now_ms,
                now_ms,
            ),
        )
        whitelist_id = int(cursor.lastrowid)
        conn.commit()
        conn.close()

    entry = get_alert_whitelist_entry(whitelist_id, organization_id)
    if entry is None:
        raise RuntimeError("Failed to load whitelist entry after insert.")
    return entry


def update_alert_whitelist_entry(
    whitelist_id: int,
    organization_id: int,
    *,
    note: str | None = None,
    period_ms: float | None | object = ...,
    endpoint_id: str | None | object = ...,
) -> dict:
    existing = get_alert_whitelist_entry(whitelist_id, organization_id)
    if existing is None:
        raise ValueError("Whitelist entry not found.")

    next_note = existing["note"] if note is None else (note or "").strip()
    if not next_note:
        raise ValueError("A note is required when whitelisting a pattern.")

    next_period = existing["periodMs"] if period_ms is ... else period_ms
    next_endpoint = existing["endpointID"] if endpoint_id is ... else endpoint_id

    if next_endpoint is not None:
        conn = connect_db()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT endpointID FROM endpoints
            WHERE endpointID = ? AND organizationID = ?
            """,
            (next_endpoint, organization_id),
        )
        found = cursor.fetchone()
        conn.close()
        if found is None:
            raise ValueError("Endpoint not found in this organization.")

    with _DB_WRITE_LOCK:
        conn = connect_db()
        cursor = conn.cursor()
        duplicate_id = _find_duplicate_whitelist_id(
            cursor,
            organization_id=organization_id,
            endpoint_id=next_endpoint,
            log_id=existing["logID"],
            native_event_id=existing["nativeEventID"],
            series_key=existing["seriesKey"],
            period_ms=next_period,
        )
        if duplicate_id is not None and duplicate_id != whitelist_id:
            conn.close()
            raise ValueError("An equivalent whitelist entry already exists.")

        cursor.execute(
            """
            UPDATE alertWhitelist
            SET note = ?, periodMs = ?, endpointID = ?, updatedAt = ?
            WHERE whitelistID = ? AND organizationID = ?
            """,
            (next_note, next_period, next_endpoint, _now_ms(), whitelist_id, organization_id),
        )
        conn.commit()
        conn.close()

    updated = get_alert_whitelist_entry(whitelist_id, organization_id)
    if updated is None:
        raise RuntimeError("Failed to load whitelist entry after update.")
    return updated


def delete_alert_whitelist_entry(whitelist_id: int, organization_id: int) -> bool:
    with _DB_WRITE_LOCK:
        conn = connect_db()
        cursor = conn.cursor()
        cursor.execute(
            """
            DELETE FROM alertWhitelist
            WHERE whitelistID = ? AND organizationID = ?
            """,
            (whitelist_id, organization_id),
        )
        deleted = cursor.rowcount > 0
        conn.commit()
        conn.close()
    return deleted


def resolve_alert_group_whitelist_identity(
    alert_group_id: int,
    organization_id: int,
) -> dict | None:
    """Resolve stable whitelist fields from an alert group (via child alert seriesKey)."""
    conn = connect_db()
    cursor = conn.cursor()
    org_clause, org_params = _org_filter_sql("g.endpointID", organization_id)
    cursor.execute(
        f"""
        SELECT
            g.alertGroupID,
            g.endpointID,
            g.nativeEventID,
            g.logID,
            g.periodTs,
            COALESCE(e.displayName, e.hostname),
            (
                SELECT a.seriesKey
                FROM alertGroupMap gm
                JOIN alerts a ON a.alertID = gm.alertID
                WHERE gm.alertGroupID = g.alertGroupID
                ORDER BY a.alertID ASC
                LIMIT 1
            ) AS seriesKey
        FROM alertGroups g
        LEFT JOIN endpoints e ON e.endpointID = g.endpointID
        WHERE g.alertGroupID = ?{org_clause}
        """,
        (alert_group_id, *org_params),
    )
    row = cursor.fetchone()
    if row is None:
        conn.close()
        return None

    series_key = row[6] or ""
    series_identity: dict = {}
    cursor.execute(
        """
        SELECT l.parsedDetails
        FROM eventAlertMap eam
        JOIN alertGroupMap gm ON gm.alertID = eam.alertID
        JOIN logs l ON l.internalEventID = eam.eventID
        WHERE gm.alertGroupID = ?
        ORDER BY l.timestamp ASC, l.internalEventID ASC
        LIMIT 1
        """,
        (alert_group_id,),
    )
    details_row = cursor.fetchone()
    conn.close()
    if details_row and details_row[0]:
        try:
            parsed = json.loads(details_row[0])
            identity = parsed.get("seriesIdentity") or parsed.get("identity") or {}
            if isinstance(identity, dict):
                series_identity = {
                    str(key): str(value) for key, value in identity.items() if value is not None
                }
        except (json.JSONDecodeError, TypeError, ValueError):
            series_identity = {}

    return {
        "alertGroupID": int(row[0]),
        "endpointID": row[1],
        "endpointName": row[5] or row[1],
        "nativeEventID": int(row[2]),
        "logID": int(row[3]) if row[3] is not None else None,
        "periodTs": float(row[4]) if row[4] is not None else None,
        "seriesKey": series_key,
        "seriesIdentity": series_identity,
        "eventName": resolve_event_name(
            int(row[3]) if row[3] is not None else None,
            int(row[2]),
        ),
        "logSource": resolve_log_source_name(int(row[3]) if row[3] is not None else None),
    }


def create_alert_whitelist_from_alert_group(
    *,
    alert_group_id: int,
    organization_id: int,
    created_by_account_id: int,
    scope: str,
    match_period: bool,
    note: str,
) -> dict:
    identity = resolve_alert_group_whitelist_identity(alert_group_id, organization_id)
    if identity is None:
        raise ValueError("Alert group not found.")

    scope_normalized = (scope or "endpoint").strip().lower()
    if scope_normalized not in {"endpoint", "organization"}:
        raise ValueError("Scope must be 'endpoint' or 'organization'.")

    endpoint_id = None if scope_normalized == "organization" else identity["endpointID"]
    period_ms = identity["periodTs"] if match_period else None

    return create_alert_whitelist_entry(
        organization_id=organization_id,
        created_by_account_id=created_by_account_id,
        endpoint_id=endpoint_id,
        log_id=identity["logID"],
        native_event_id=identity["nativeEventID"],
        series_key=identity["seriesKey"],
        period_ms=period_ms,
        note=note,
        series_identity=identity["seriesIdentity"],
    )


def _series_key_subquery() -> str:
    return """
        (
            SELECT a.seriesKey
            FROM alertGroupMap gm
            JOIN alerts a ON a.alertID = gm.alertID
            WHERE gm.alertGroupID = g.alertGroupID
            ORDER BY a.alertID ASC
            LIMIT 1
        )
    """


def _list_alert_group_identities(
    cursor,
    *,
    where_sql: str,
    params: list | tuple,
) -> list[dict]:
    cursor.execute(
        f"""
        SELECT
            g.alertGroupID,
            g.endpointID,
            g.nativeEventID,
            g.logID,
            g.periodTs,
            {_series_key_subquery()} AS seriesKey
        FROM alertGroups g
        {where_sql}
        """,
        params,
    )
    identities = []
    for row in cursor.fetchall():
        identities.append(
            {
                "alertGroupID": int(row[0]),
                "endpointID": row[1],
                "nativeEventID": int(row[2]),
                "logID": int(row[3]) if row[3] is not None else None,
                "periodTs": float(row[4]) if row[4] is not None else None,
                "seriesKey": row[5] or "",
            }
        )
    return identities


def _visible_alert_group_ids(
    identities: list[dict],
    whitelist_entries: list[dict],
) -> set[int]:
    if not whitelist_entries:
        return {item["alertGroupID"] for item in identities}
    visible: set[int] = set()
    for item in identities:
        if is_alert_whitelisted(
            endpoint_id=str(item["endpointID"] or ""),
            log_id=item["logID"],
            native_event_id=item["nativeEventID"],
            series_key=item["seriesKey"],
            period_ms=item["periodTs"],
            entries=whitelist_entries,
        ):
            continue
        visible.add(item["alertGroupID"])
    return visible


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
    conn = connect_db()
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
            a.phase,
            {_series_key_subquery()} AS seriesKey
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
            alert["seriesKey"] = row[17] or ""
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
    if filters.organization_id is not None:
        whitelist_entries = list_alert_whitelist(filters.organization_id)
        alerts = filter_alerts_against_whitelist(alerts, whitelist_entries)
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
    conn = connect_db()
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

    identity = None
    if organization_id is not None:
        identity = resolve_alert_group_whitelist_identity(alert_group_id, organization_id)
    if identity is not None:
        alert["seriesKey"] = identity["seriesKey"]
        alert["seriesIdentity"] = identity["seriesIdentity"]
        whitelist_entries = list_alert_whitelist(organization_id)
        alert["isWhitelisted"] = is_alert_whitelisted(
            endpoint_id=str(identity["endpointID"] or ""),
            log_id=identity["logID"],
            native_event_id=identity["nativeEventID"],
            series_key=identity["seriesKey"],
            period_ms=identity["periodTs"],
            entries=whitelist_entries,
        )
    else:
        alert.setdefault("seriesKey", "")
        alert.setdefault("seriesIdentity", {})
        alert["isWhitelisted"] = False

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
                    "label": bucket_start.strftime("%d/%m"),
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

    conn = connect_db()
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

    cursor.execute(
        f"""
        SELECT
            base.endpointID,
            base.displayName,
            base.hostname,
            base.ip,
            COALESCE(base.lastSeenAt, log_stats.latestLogTs, alert_stats.latestAlertEnd) AS lastSeenAt
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
        ORDER BY base.endpointID ASC
        """,
        base_params,
    )
    rows = cursor.fetchall()

    org_clause, org_params = _org_filter_sql("g.endpointID", organization_id)
    identities = _list_alert_group_identities(
        cursor,
        where_sql=f"WHERE g.tsBegin <= ? AND g.tsEnd >= ?{org_clause}",
        params=(effective_end_ms, effective_start_ms, *org_params),
    )
    whitelist_entries = list_alert_whitelist(organization_id) if organization_id is not None else []
    visible_ids = _visible_alert_group_ids(identities, whitelist_entries)
    counts_by_endpoint: dict[str, int] = {}
    for item in identities:
        if item["alertGroupID"] not in visible_ids:
            continue
        endpoint_id = str(item["endpointID"] or "")
        counts_by_endpoint[endpoint_id] = counts_by_endpoint.get(endpoint_id, 0) + 1

    conn.close()

    entities = []
    for endpoint_id, display_name, hostname, ip, last_seen_at in rows:
        alert_count = int(counts_by_endpoint.get(str(endpoint_id), 0))
        entities.append(
            {
                "endpointID": endpoint_id,
                "name": display_name or hostname,
                "ip": ip,
                "lastSeenAt": int(last_seen_at) if last_seen_at is not None else None,
                "alertsLastWeek": alert_count,
                "alertCount": alert_count,
            }
        )

    entities.sort(key=lambda item: (-int(item["alertsLastWeek"]), str(item["endpointID"])))

    return {
        "windowStart": effective_start_ms,
        "windowEnd": effective_end_ms,
        "timePreset": time_preset,
        "entities": entities,
    }


def _count_alerts_with_last_event_since(
    cursor,
    since_ms: int,
    organization_id: int | None = None,
) -> int:
    """Alerts whose last observed event (tsEnd) is at or after since_ms."""
    org_clause, org_params = _org_filter_sql("g.endpointID", organization_id)
    identities = _list_alert_group_identities(
        cursor,
        where_sql=f"WHERE g.tsEnd >= ?{org_clause}",
        params=(since_ms, *org_params),
    )
    whitelist_entries = list_alert_whitelist(organization_id) if organization_id is not None else []
    return len(_visible_alert_group_ids(identities, whitelist_entries))


def _count_overlapping_alert_groups(
    cursor,
    window_start_ms: int,
    window_end_ms: int,
    organization_id: int | None = None,
) -> int:
    org_clause, org_params = _org_filter_sql("g.endpointID", organization_id)
    identities = _list_alert_group_identities(
        cursor,
        where_sql=f"WHERE g.tsBegin <= ? AND g.tsEnd >= ?{org_clause}",
        params=(window_end_ms, window_start_ms, *org_params),
    )
    whitelist_entries = list_alert_whitelist(organization_id) if organization_id is not None else []
    return len(_visible_alert_group_ids(identities, whitelist_entries))


def fetch_dashboard_stats(
    window_start_ms: int | None = None,
    window_end_ms: int | None = None,
    time_preset: str = "last_week",
    organization_id: int | None = None,
):
    now = datetime.now(timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    last_24h_start_ms = int((now - timedelta(hours=24)).timestamp() * 1000)

    conn = connect_db()
    cursor = conn.cursor()
    effective_start_ms, effective_end_ms = _effective_query_window(cursor, window_start_ms, window_end_ms, now_ms)

    recent_activity_hours = 6
    recent_activity_start_ms = int((now - timedelta(hours=recent_activity_hours)).timestamp() * 1000)
    recent_activity = _count_alerts_with_last_event_since(
        cursor,
        recent_activity_start_ms,
        organization_id,
    )
    active_last_24h = _count_overlapping_alert_groups(cursor, last_24h_start_ms, now_ms, organization_id)
    active_in_window = _count_overlapping_alert_groups(cursor, effective_start_ms, effective_end_ms, organization_id)

    org_g_clause, org_g_params = _org_filter_sql("g.endpointID", organization_id)
    whitelist_entries = list_alert_whitelist(organization_id) if organization_id is not None else []
    window_identities = _list_alert_group_identities(
        cursor,
        where_sql=f"WHERE g.tsBegin <= ? AND g.tsEnd >= ?{org_g_clause}",
        params=(effective_end_ms, effective_start_ms, *org_g_params),
    )
    visible_window_ids = _visible_alert_group_ids(window_identities, whitelist_entries)
    visible_window_identities = [
        item for item in window_identities if item["alertGroupID"] in visible_window_ids
    ]

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
            COALESCE(e.displayName, e.hostname) AS name,
            g.periodTs,
            {_series_key_subquery()} AS seriesKey
        FROM alertGroups g
        LEFT JOIN endpoints e ON e.endpointID = g.endpointID
        WHERE g.confidence >= 80
          AND g.tsBegin <= ?
          AND g.tsEnd >= ?{org_g_clause}
        ORDER BY g.confidence DESC, g.tsEnd DESC, g.alertGroupID DESC
        LIMIT 40
        """,
        (effective_end_ms, effective_start_ms, *org_g_params),
    )
    recent_high_confidence_alerts = []
    for row in cursor.fetchall():
        (
            alert_group_id,
            endpoint_id,
            native_event_id,
            log_id,
            confidence,
            ts_begin,
            ts_end,
            hostname,
            period_ts,
            series_key,
        ) = row
        if is_alert_whitelisted(
            endpoint_id=str(endpoint_id or ""),
            log_id=int(log_id) if log_id is not None else None,
            native_event_id=int(native_event_id),
            series_key=series_key or "",
            period_ms=float(period_ts) if period_ts is not None else None,
            entries=whitelist_entries,
        ):
            continue
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
        if len(recent_high_confidence_alerts) >= 5:
            break

    # Confidence for high-confidence summary from already-loaded visible identities.
    conf_by_id = {}
    if visible_window_identities:
        placeholders = ",".join("?" for _ in visible_window_identities)
        cursor.execute(
            f"""
            SELECT alertGroupID, confidence
            FROM alertGroups
            WHERE alertGroupID IN ({placeholders})
            """,
            [item["alertGroupID"] for item in visible_window_identities],
        )
        conf_by_id = {int(row[0]): int(row[1]) for row in cursor.fetchall()}

    high_confidence_in_window = sum(
        1 for item in visible_window_identities if conf_by_id.get(item["alertGroupID"], 0) >= 80
    )

    event_counts: dict[tuple[int | None, int], int] = {}
    endpoint_counts: dict[str, int] = {}
    for item in visible_window_identities:
        event_key = (item["logID"], item["nativeEventID"])
        event_counts[event_key] = event_counts.get(event_key, 0) + 1
        endpoint_id = str(item["endpointID"] or "")
        endpoint_counts[endpoint_id] = endpoint_counts.get(endpoint_id, 0) + 1

    timeline = []
    for bucket in _timeline_buckets(effective_start_ms, effective_end_ms):
        count = _count_overlapping_alert_groups(
            cursor, bucket["bucketStart"], bucket["bucketEnd"], organization_id
        )
        timeline.append({**bucket, "count": count})

    top_events = []
    for (log_id, native_event_id), alert_count in sorted(
        event_counts.items(),
        key=lambda pair: (-pair[1], pair[0][1]),
    )[:8]:
        top_events.append(
            {
                "nativeEventID": native_event_id,
                "eventName": resolve_event_name(log_id, native_event_id),
                "alertCount": int(alert_count),
            }
        )

    top_endpoints = []
    for endpoint_id, alert_count in sorted(
        endpoint_counts.items(),
        key=lambda pair: (-pair[1], pair[0]),
    )[:5]:
        cursor.execute(
            """
            SELECT COALESCE(displayName, hostname)
            FROM endpoints
            WHERE endpointID = ?
            """,
            (endpoint_id,),
        )
        name_row = cursor.fetchone()
        top_endpoints.append(
            {
                "endpointID": endpoint_id,
                "name": name_row[0] if name_row else endpoint_id,
                "alertCount": int(alert_count),
            }
        )

    conn.close()

    return {
        "generatedAt": now_ms,
        "windowStart": effective_start_ms,
        "windowEnd": effective_end_ms,
        "timePreset": time_preset,
        "summary": {
            "recentActivity": recent_activity,
            "recentActivityHours": recent_activity_hours,
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
