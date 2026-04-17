import sqlite3

from brain import EventRecord, run_brain_for_endpoint
from config import DB_PATH


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
            tsBegin INTEGER NOT NULL,
            tsEnd INTEGER NOT NULL,
            periodTs REAL,
            confidence INTEGER NOT NULL
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
        "DELETE FROM eventAlertMap WHERE alertID IN (SELECT alertID FROM alerts WHERE endpointID = ?)",
        (endpoint_id,),
    )
    cursor.execute("DELETE FROM alerts WHERE endpointID = ?", (endpoint_id,))

    for alert in alerts:
        cursor.execute(
            """
            INSERT INTO alerts (
                endpointID,
                tsBegin,
                tsEnd,
                periodTs,
                confidence
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                alert.endpoint_id,
                alert.ts_begin,
                alert.ts_end,
                alert.period_ts,
                alert.confidence,
            ),
        )
        alert_id = cursor.lastrowid

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

    conn.commit()
    conn.close()
    return len(alerts)


def recompute_alerts_for_endpoint(endpoint_id: str) -> int:
    return run_brain_for_endpoint(
        endpoint_id=endpoint_id,
        fetch_events=fetch_events_for_endpoint,
        publish_alerts=replace_alerts_for_endpoint,
    )
