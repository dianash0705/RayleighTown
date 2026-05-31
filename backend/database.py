import sqlite3

from brain import EventRecord, run_brain_for_endpoint
from config import DB_PATH, PHASE_GHOST_SUPPRESSION_ENABLED, PHASE_GHOST_SUPPRESSION_SIMILARITY_THRESHOLD


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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS logs (
            endpointID TEXT NOT NULL,
            internalEventID INTEGER NOT NULL,
            timestamp INTEGER NOT NULL,
            logID INTEGER NOT NULL,
            nativeEventID INTEGER NOT NULL,
            internalEventType INTEGER NOT NULL,
            PRIMARY KEY (endpointID, internalEventID)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS alerts (
            alertID INTEGER PRIMARY KEY AUTOINCREMENT,
            endpointID TEXT NOT NULL,
            nativeEventID INTEGER NOT NULL,
            tsBegin INTEGER NOT NULL,
            tsEnd INTEGER NOT NULL,
            periodTs REAL,
            confidence INTEGER NOT NULL,
            phase REAL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS eventAlertMap (
            eventID INTEGER NOT NULL,
            alertID INTEGER NOT NULL,
            confidence INTEGER NOT NULL,
            PRIMARY KEY (eventID, alertID)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS alertGroups (
            alertGroupID INTEGER PRIMARY KEY AUTOINCREMENT,
            endpointID TEXT NOT NULL,
            nativeEventID INTEGER NOT NULL,
            tsBegin INTEGER NOT NULL,
            tsEnd INTEGER NOT NULL,
            periodTs REAL,
            confidence INTEGER NOT NULL,
            phase REAL
        )
        """
    )
    conn.execute(
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

    for timestamp_ms, native_event_id in events:
        if not isinstance(timestamp_ms, int):
            raise TypeError("timestamp_ms must be int")
        cursor.execute(
            """
            INSERT INTO logs (
                endpointID,
                internalEventID,
                timestamp,
                logID,
                nativeEventID,
                internalEventType
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                endpoint_id,
                next_internal_event_id,
                timestamp_ms,
                log_id,
                native_event_id,
                native_event_id,
            ),
        )
        next_internal_event_id += 1

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
        cursor.execute(
            """
            INSERT INTO alerts (
                endpointID,
                nativeEventID,
                tsBegin,
                tsEnd,
                periodTs,
                confidence,
                phase
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                alert.endpoint_id,
                alert.native_event_id,
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

    for raw_alert_row in sorted(raw_alert_rows, key=lambda row: (row["native_event_id"], row["period_ts"], row["ts_begin"], row["ts_end"], row["alert_id"])):
        group = find_matching_group(raw_alert_row)
        if group is None:
            group = {
                "endpoint_id": raw_alert_row["endpoint_id"],
                "native_event_id": raw_alert_row["native_event_id"],
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
            if group["phase"] is None and raw_alert_row["phase"] is not None:
                group["phase"] = raw_alert_row["phase"]
            group["alert_ids"].append(raw_alert_row["alert_id"])

    for group in grouped_alerts:
        cursor.execute(
            """
            INSERT INTO alertGroups (
                endpointID,
                nativeEventID,
                tsBegin,
                tsEnd,
                periodTs,
                confidence,
                phase
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                group["endpoint_id"],
                group["native_event_id"],
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


def fetch_alerts_for_endpoint(endpoint_id: str):
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
            a.alertID,
            a.tsBegin,
            a.tsEnd,
            a.confidence,
            a.phase
        FROM alertGroups g
        LEFT JOIN alertGroupMap gm ON gm.alertGroupID = g.alertGroupID
        LEFT JOIN alerts a ON a.alertID = gm.alertID
        WHERE g.endpointID = ?
        ORDER BY g.confidence DESC, g.tsBegin DESC, a.tsBegin ASC
        """,
        (endpoint_id,),
    )
    rows = cursor.fetchall()
    conn.close()

    grouped_alerts = {}
    for row in rows:
        alert_group_id = row[0]
        alert = grouped_alerts.get(alert_group_id)
        if alert is None:
            alert = {
                "alertID": alert_group_id,
                "endpointID": row[1],
                "nativeEventID": row[2],
                "tsBegin": row[3],
                "tsEnd": row[4],
                "periodTs": row[5],
                "confidence": row[6],
                "phase": row[7],
                "windows": [],
            }
            grouped_alerts[alert_group_id] = alert

        child_alert_id = row[8]
        if child_alert_id is not None:
            alert["windows"].append(
                {
                    "alertID": child_alert_id,
                    "tsBegin": row[9],
                    "tsEnd": row[10],
                    "confidence": row[11],
                    "phase": row[12],
                }
            )

    return list(grouped_alerts.values())
