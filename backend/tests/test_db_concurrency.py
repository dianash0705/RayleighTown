import sqlite3
import threading

import pytest

import database


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "concurrency.db"
    monkeypatch.setattr(database, "DB_PATH", db_path)
    database.init_db()
    return db_path


def _event(timestamp_ms: int, native_event_id: int = 4624) -> dict:
    return {
        "timestamp_ms": timestamp_ms,
        "native_event_id": native_event_id,
        "raw_payload": {},
        "parsed_details": {},
    }


@pytest.mark.unit
def test_concurrent_read_during_bulk_insert(temp_db):
    endpoint_id = "ep-concurrency"
    errors: list[Exception] = []

    def reader():
        try:
            for _ in range(20):
                conn = database.connect_db()
                conn.execute("SELECT COUNT(*) FROM logs").fetchone()
                conn.close()
        except sqlite3.OperationalError as err:
            errors.append(err)

    reader_thread = threading.Thread(target=reader)
    reader_thread.start()

    events = [_event(1_000_000 + index * 1_000) for index in range(500)]
    database.insert_events(endpoint_id, 1, events)

    reader_thread.join(timeout=10)
    assert not reader_thread.is_alive()
    assert errors == []
