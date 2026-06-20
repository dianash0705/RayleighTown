import sqlite3

import pytest

import database
from analysis_queue import AnalysisQueue
from ingestion_models import NativeEventImpact


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "incremental_test.db"
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


def test_insert_events_skips_duplicates_and_old_rows(temp_db):
    endpoint_id = "ep-ingest-1"
    log_id = 1

    first = database.insert_events(endpoint_id, log_id, [_event(1_000_000), _event(1_010_000)])
    assert first.inserted_count == 2
    assert first.skipped_count == 0
    assert len(first.impacts) == 1
    assert first.impacts[0].new_min_ms == 1_000_000
    assert first.impacts[0].new_max_ms == 1_010_000

    replay = database.insert_events(
        endpoint_id,
        log_id,
        [_event(1_000_000), _event(1_010_000), _event(1_020_000)],
    )
    assert replay.inserted_count == 1
    assert replay.skipped_count == 2
    assert replay.impacts[0].new_max_ms == 1_020_000

    conn = sqlite3.connect(temp_db)
    count = conn.execute("SELECT COUNT(*) FROM logs WHERE endpointID = ?", (endpoint_id,)).fetchone()[0]
    conn.close()
    assert count == 3


def test_insert_events_overlap_buffer_allows_recent_resend(temp_db, monkeypatch):
    from dataclasses import replace

    import config
    import database as db

    updated = replace(config.INGESTION_CONFIG, overlap_buffer_ms=60_000)
    monkeypatch.setattr(config, "INGESTION_CONFIG", updated)
    monkeypatch.setattr(db, "INGESTION_CONFIG", updated)
    endpoint_id = "ep-ingest-2"
    log_id = 1

    database.insert_events(endpoint_id, log_id, [_event(1_000_000)])

    too_old = database.insert_events(endpoint_id, log_id, [_event(800_000)])
    assert too_old.inserted_count == 0
    assert too_old.skipped_count == 1

    new_in_buffer = database.insert_events(endpoint_id, log_id, [_event(1_030_000)])
    assert new_in_buffer.inserted_count == 1


def test_analysis_queue_merges_jobs_for_same_endpoint():
    queue = AnalysisQueue(poll_interval_sec=0.01)
    queue.enqueue("ep-a", [NativeEventImpact(4624, 1_000, 2_000)])
    queue.enqueue(
        "ep-a",
        [
            NativeEventImpact(4624, 3_000, 4_000),
            NativeEventImpact(4688, 5_000, 6_000),
        ],
    )

    jobs = queue._drain()
    assert len(jobs) == 1
    job = jobs[0]
    assert set(job.impacts) == {(4624, ""), (4688, "")}
    assert job.impacts[(4624, "")].new_min_ms == 1_000
    assert job.impacts[(4624, "")].new_max_ms == 4_000


def test_incremental_recompute_only_touches_affected_event_type(temp_db):
    endpoint_id = "ep-brain-1"
    log_id = 1

    periodic = [
        _event(1_000_000 + index * 10_000, native_event_id=4624)
        for index in range(8)
    ]
    other = [_event(1_000_000, native_event_id=4688)]

    database.insert_events(endpoint_id, log_id, periodic + other)
    database.recompute_alerts_for_endpoint(endpoint_id)

    conn = sqlite3.connect(temp_db)
    before_4688 = conn.execute(
        "SELECT COUNT(*) FROM alerts WHERE endpointID = ? AND nativeEventID = 4688",
        (endpoint_id,),
    ).fetchone()[0]
    conn.close()

    database.insert_events(endpoint_id, log_id, [_event(1_080_000, native_event_id=4624)])
    database.incremental_recompute_alerts_for_endpoint(
        endpoint_id,
        [NativeEventImpact(4624, 1_080_000, 1_080_000)],
    )

    conn = sqlite3.connect(temp_db)
    after_4688 = conn.execute(
        "SELECT COUNT(*) FROM alerts WHERE endpointID = ? AND nativeEventID = 4688",
        (endpoint_id,),
    ).fetchone()[0]
    conn.close()

    assert before_4688 == after_4688
