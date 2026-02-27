import sqlite3

from config import DB_PATH


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS logs (
            endpointID TEXT NOT NULL,
            internalEventID INTEGER NOT NULL,
            timestamp TEXT NOT NULL,
            logID INTEGER NOT NULL,
            nativeEventID INTEGER NOT NULL,
            internalEventType INTEGER NOT NULL,
            PRIMARY KEY (endpointID, internalEventID)
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

    for timestamp, native_event_id in events:
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
                timestamp,
                log_id,
                native_event_id,
                native_event_id,
            ),
        )
        next_internal_event_id += 1

    conn.commit()
    conn.close()
    return len(events)
