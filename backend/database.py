import json
import sqlite3
from datetime import datetime, timedelta, timezone

from alert_filters import AlertQueryFilters, native_event_ids_for_filters
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

    _ensure_column(cursor=conn.cursor(), table_name="alerts", column_definition="nativeEventID INTEGER NOT NULL DEFAULT 0")
    _ensure_column(cursor=conn.cursor(), table_name="alerts", column_definition="phase REAL")
    _ensure_column(cursor=conn.cursor(), table_name="alerts", column_definition="logID INTEGER")
    _ensure_column(cursor=conn.cursor(), table_name="alertGroups", column_definition="logID INTEGER")
    _ensure_column(cursor=conn.cursor(), table_name="logs", column_definition="rawPayload TEXT NOT NULL DEFAULT '{}'")
    _ensure_column(cursor=conn.cursor(), table_name="logs", column_definition="parsedDetails TEXT NOT NULL DEFAULT '{}'")
    conn.commit()
    conn.close()


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

        for event_id in alert.event_ids:
            cursor.execute(
                """
                INSERT INTO eventAlertMap (
                    eventID,
                    alertID,
                    confidence
                ) VALUES (?, ?, ?)
                """,
                (event_id, alert_id, alert.confidence),
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
    return {
        "alertID": row[0],
        "endpointID": row[1],
        "nativeEventID": native_event_id,
        "eventName": resolve_event_name(log_id, native_event_id),
        "logID": log_id,
        "logSource": resolve_log_source_name(log_id),
        "tsBegin": row[3],
        "tsEnd": row[4],
        "periodTs": row[5],
        "confidence": row[6],
        "phase": row[7],
        "hostname": row[9] if len(row) > 9 else None,
        "ip": row[10] if len(row) > 10 else None,
        "windows": windows,
    }


def fetch_alerts(filters: AlertQueryFilters):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    where_clauses = []
    params: list = []

    if filters.endpoint_id:
        where_clauses.append("g.endpointID = ?")
        params.append(filters.endpoint_id)

    native_event_ids = native_event_ids_for_filters(filters)
    if native_event_ids is not None:
        if not native_event_ids:
            conn.close()
            return []
        placeholders = ", ".join("?" for _ in native_event_ids)
        where_clauses.append(f"g.nativeEventID IN ({placeholders})")
        params.extend(sorted(native_event_ids))

    if filters.min_confidence is not None:
        where_clauses.append("g.confidence >= ?")
        params.append(filters.min_confidence)

    if filters.window_start_ms is not None and filters.window_end_ms is not None:
        where_clauses.append("g.tsBegin <= ?")
        where_clauses.append("g.tsEnd >= ?")
        params.extend([filters.window_end_ms, filters.window_start_ms])

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
            alert = _serialize_alert_group_row(row[:11], [])
            grouped_alerts[alert_group_id] = alert

        child_alert_id = row[11]
        if child_alert_id is not None:
            alert["windows"].append(
                {
                    "alertID": child_alert_id,
                    "tsBegin": row[12],
                    "tsEnd": row[13],
                    "confidence": row[14],
                    "phase": row[15],
                }
            )

    alerts = list(grouped_alerts.values())
    if filters.sort_key == "eventName":
        reverse = filters.sort_direction == "desc"
        alerts.sort(key=lambda item: item.get("eventName", "").lower(), reverse=reverse)
    return alerts


def fetch_alert_detail(alert_group_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
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
            e.ip
        FROM alertGroups g
        LEFT JOIN endpoints e ON e.endpointID = g.endpointID
        WHERE g.alertGroupID = ?
        """,
        (alert_group_id,),
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

    cursor.execute(
        """
        SELECT
            l.internalEventID,
            l.timestamp,
            l.logID,
            l.nativeEventID,
            l.rawPayload,
            l.parsedDetails
        FROM logs l
        JOIN alertGroups g ON g.endpointID = l.endpointID
        JOIN eventAlertMap eam ON eam.eventID = l.internalEventID
        JOIN alertGroupMap agm ON agm.alertID = eam.alertID AND agm.alertGroupID = g.alertGroupID
        WHERE g.alertGroupID = ?
        ORDER BY l.timestamp ASC
        """,
        (alert_group_id,),
    )
    event_rows = cursor.fetchall()
    conn.close()

    log_id = group_row[8]
    native_event_id = group_row[2]
    representative_event = None
    stored_events = []

    for event_row in event_rows:
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
        }
        stored_events.append(event_item)
        if representative_event is None:
            representative_event = event_item

    windows = [
        {
            "alertID": row[0],
            "tsBegin": row[1],
            "tsEnd": row[2],
            "confidence": row[3],
            "phase": row[4],
        }
        for row in window_rows
    ]

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
    return alert


def fetch_entities():
    now = datetime.now(timezone.utc)
    window_end_ms = int(now.timestamp() * 1000)
    window_start_ms = int((now - timedelta(days=7)).timestamp() * 1000)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT
            base.endpointID,
            base.hostname,
            base.ip,
            COALESCE(counts.alertCount, 0) AS alertsLastWeek
        FROM (
            SELECT endpointID, hostname, ip FROM endpoints
            UNION
            SELECT DISTINCT g.endpointID, NULL, NULL
            FROM alertGroups g
            WHERE g.endpointID NOT IN (SELECT endpointID FROM endpoints)
        ) base
        LEFT JOIN (
            SELECT endpointID, COUNT(*) AS alertCount
            FROM alertGroups
            WHERE tsBegin <= ?
              AND tsEnd >= ?
            GROUP BY endpointID
        ) counts ON counts.endpointID = base.endpointID
        ORDER BY alertsLastWeek DESC, base.endpointID ASC
        """,
        (window_end_ms, window_start_ms),
    )
    rows = cursor.fetchall()
    conn.close()

    entities = []
    for endpoint_id, hostname, ip, alerts_last_week in rows:
        entities.append(
            {
                "endpointID": endpoint_id,
                "name": hostname,
                "ip": ip,
                "alertsLastWeek": int(alerts_last_week),
            }
        )

    return {
        "windowStart": window_start_ms,
        "windowEnd": window_end_ms,
        "entities": entities,
    }


def _count_overlapping_alert_groups(cursor, window_start_ms: int, window_end_ms: int) -> int:
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM alertGroups
        WHERE tsBegin <= ?
          AND tsEnd >= ?
        """,
        (window_end_ms, window_start_ms),
    )
    row = cursor.fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def fetch_dashboard_stats():
    now = datetime.now(timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    last_24h_start_ms = int((now - timedelta(hours=24)).timestamp() * 1000)
    last_week_start_ms = int((now - timedelta(days=7)).timestamp() * 1000)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    active_now = _count_overlapping_alert_groups(cursor, now_ms, now_ms)
    active_last_24h = _count_overlapping_alert_groups(cursor, last_24h_start_ms, now_ms)
    active_last_week = _count_overlapping_alert_groups(cursor, last_week_start_ms, now_ms)

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM alertGroups
        WHERE tsBegin <= ?
          AND tsEnd >= ?
          AND confidence >= 80
        """,
        (now_ms, last_week_start_ms),
    )
    high_confidence_last_week = int(cursor.fetchone()[0])

    timeline = []
    for day_offset in range(6, -1, -1):
        day_start = (now - timedelta(days=day_offset)).replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1) - timedelta(milliseconds=1)
        bucket_start_ms = int(day_start.timestamp() * 1000)
        bucket_end_ms = int(day_end.timestamp() * 1000)
        count = _count_overlapping_alert_groups(cursor, bucket_start_ms, bucket_end_ms)
        timeline.append(
            {
                "bucketStart": bucket_start_ms,
                "bucketEnd": bucket_end_ms,
                "label": day_start.strftime("%a %m/%d"),
                "count": count,
            }
        )

    cursor.execute(
        """
        SELECT nativeEventID, logID, COUNT(*) AS alertCount
        FROM alertGroups
        WHERE tsBegin <= ?
          AND tsEnd >= ?
        GROUP BY nativeEventID, logID
        ORDER BY alertCount DESC, nativeEventID ASC
        LIMIT 8
        """,
        (now_ms, last_week_start_ms),
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
        """
        SELECT
            g.endpointID,
            e.hostname,
            COUNT(*) AS alertCount
        FROM alertGroups g
        LEFT JOIN endpoints e ON e.endpointID = g.endpointID
        WHERE g.tsBegin <= ?
          AND g.tsEnd >= ?
        GROUP BY g.endpointID, e.hostname
        ORDER BY alertCount DESC, g.endpointID ASC
        LIMIT 5
        """,
        (now_ms, last_week_start_ms),
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
        """
        SELECT
            g.alertGroupID,
            g.endpointID,
            g.nativeEventID,
            g.logID,
            g.confidence,
            g.tsBegin,
            g.tsEnd,
            e.hostname
        FROM alertGroups g
        LEFT JOIN endpoints e ON e.endpointID = g.endpointID
        WHERE g.confidence >= 80
          AND g.tsBegin <= ?
          AND g.tsEnd >= ?
        ORDER BY g.confidence DESC, g.tsEnd DESC, g.alertGroupID DESC
        LIMIT 5
        """,
        (now_ms, last_week_start_ms),
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
        "summary": {
            "activeNow": active_now,
            "activeLast24h": active_last_24h,
            "activeLastWeek": active_last_week,
            "highConfidenceLastWeek": high_confidence_last_week,
        },
        "timeline": timeline,
        "topEvents": top_events,
        "topEndpoints": top_endpoints,
        "recentHighConfidenceAlerts": recent_high_confidence_alerts,
    }
