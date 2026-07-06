import sqlite3

import pytest

import database
from alert_filters import AlertQueryFilters
from alert_whitelist import alert_matches_whitelist_entry, filter_alerts_against_whitelist


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "whitelist_test.db"
    monkeypatch.setattr(database, "DB_PATH", db_path)
    database.init_db()
    return db_path


def _seed_org_endpoint():
    admin = database.create_organization_with_admin("Acme", "root", "pw123")
    endpoint = database.register_endpoint(admin["organizationID"], "Laptop")
    return admin, endpoint["endpointID"]


def _insert_alert_group(
    *,
    endpoint_id: str,
    native_event_id: int,
    log_id: int,
    series_key: str,
    period_ms: float,
    confidence: int = 90,
):
    conn = sqlite3.connect(database.DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO alerts (
            endpointID, nativeEventID, logID, tsBegin, tsEnd, periodTs, confidence, phase, seriesKey
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (endpoint_id, native_event_id, log_id, 1_000, 10_000, period_ms, confidence, 0.0, series_key),
    )
    alert_id = int(cursor.lastrowid)
    cursor.execute(
        """
        INSERT INTO alertGroups (
            endpointID, nativeEventID, logID, tsBegin, tsEnd, periodTs, confidence, phase
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (endpoint_id, native_event_id, log_id, 1_000, 10_000, period_ms, confidence, 0.0),
    )
    group_id = int(cursor.lastrowid)
    cursor.execute(
        "INSERT INTO alertGroupMap (alertGroupID, alertID) VALUES (?, ?)",
        (group_id, alert_id),
    )
    conn.commit()
    conn.close()
    return group_id


def test_match_org_wide_and_period_band():
    entries = [
        {
            "endpointID": None,
            "logID": 0,
            "nativeEventID": 4625,
            "seriesKey": "FailureReason=%%2313|LogonType=2|TargetUserName=RayleighFakeUser",
            "periodMs": None,
        }
    ]
    assert alert_matches_whitelist_entry(
        endpoint_id="ep-1",
        log_id=0,
        native_event_id=4625,
        series_key="FailureReason=%%2313|LogonType=2|TargetUserName=RayleighFakeUser",
        period_ms=300_000.0,
        entry=entries[0],
    )
    assert not alert_matches_whitelist_entry(
        endpoint_id="ep-1",
        log_id=0,
        native_event_id=4625,
        series_key="FailureReason=%%2313|LogonType=2|TargetUserName=Other",
        period_ms=300_000.0,
        entry=entries[0],
    )

    period_entry = {
        "endpointID": "ep-1",
        "logID": 0,
        "nativeEventID": 4625,
        "seriesKey": "k=v",
        "periodMs": 300_000.0,
    }
    assert alert_matches_whitelist_entry(
        endpoint_id="ep-1",
        log_id=0,
        native_event_id=4625,
        series_key="k=v",
        period_ms=310_000.0,
        entry=period_entry,
    )
    assert not alert_matches_whitelist_entry(
        endpoint_id="ep-1",
        log_id=0,
        native_event_id=4625,
        series_key="k=v",
        period_ms=600_000.0,
        entry=period_entry,
    )


def test_empty_series_key_matches_any_series_for_event_type():
    entry = {
        "endpointID": None,
        "logID": 1,
        "nativeEventID": 13,
        "seriesKey": "",
        "periodMs": 60_000.0,
    }
    assert alert_matches_whitelist_entry(
        endpoint_id="ep-1",
        log_id=1,
        native_event_id=13,
        series_key="targetObject=HKCU\\Software\\Demo\\Setting",
        period_ms=60_000.0,
        entry=entry,
    )
    assert not alert_matches_whitelist_entry(
        endpoint_id="ep-1",
        log_id=1,
        native_event_id=13,
        series_key="targetObject=HKCU\\Software\\Demo\\Setting",
        period_ms=120_000.0,
        entry=entry,
    )
    any_period = {**entry, "periodMs": None}
    assert alert_matches_whitelist_entry(
        endpoint_id="ep-1",
        log_id=1,
        native_event_id=13,
        series_key="targetObject=anything",
        period_ms=999_000.0,
        entry=any_period,
    )


def test_create_from_alert_hides_in_fetch_alerts(temp_db):
    admin, endpoint_id = _seed_org_endpoint()
    series_key = "FailureReason=%%2313|LogonType=2|TargetUserName=RayleighFakeUser"
    group_id = _insert_alert_group(
        endpoint_id=endpoint_id,
        native_event_id=4625,
        log_id=0,
        series_key=series_key,
        period_ms=300_000.0,
    )

    before = database.fetch_alerts(
        AlertQueryFilters(organization_id=admin["organizationID"])
    )
    assert len(before) == 1

    entry = database.create_alert_whitelist_from_alert_group(
        alert_group_id=group_id,
        organization_id=admin["organizationID"],
        created_by_account_id=admin["accountID"],
        scope="endpoint",
        match_period=False,
        note="Known failed-logon demo script",
    )
    assert entry["scope"] == "endpoint"
    assert entry["periodMs"] is None
    assert entry["seriesKey"] == series_key

    after = database.fetch_alerts(
        AlertQueryFilters(organization_id=admin["organizationID"])
    )
    assert after == []

    detail = database.fetch_alert_detail(group_id, organization_id=admin["organizationID"])
    assert detail is not None
    assert detail["isWhitelisted"] is True

    deleted = database.delete_alert_whitelist_entry(entry["whitelistID"], admin["organizationID"])
    assert deleted
    restored = database.fetch_alerts(
        AlertQueryFilters(organization_id=admin["organizationID"])
    )
    assert len(restored) == 1


def test_org_wide_hides_same_series_on_all_endpoints(temp_db):
    admin, endpoint_a = _seed_org_endpoint()
    endpoint_b = database.register_endpoint(admin["organizationID"], "Server")["endpointID"]
    series_key = "image=C:\\Vendor\\update.exe|protocol=tcp"
    _insert_alert_group(
        endpoint_id=endpoint_a,
        native_event_id=3,
        log_id=1,
        series_key=series_key,
        period_ms=60_000.0,
    )
    group_b = _insert_alert_group(
        endpoint_id=endpoint_b,
        native_event_id=3,
        log_id=1,
        series_key=series_key,
        period_ms=60_000.0,
    )

    database.create_alert_whitelist_from_alert_group(
        alert_group_id=group_b,
        organization_id=admin["organizationID"],
        created_by_account_id=admin["accountID"],
        scope="organization",
        match_period=True,
        note="Company updater",
    )

    visible = database.fetch_alerts(
        AlertQueryFilters(organization_id=admin["organizationID"])
    )
    assert visible == []


def test_filter_alerts_helper_keeps_non_matching():
    alerts = [
        {
            "endpointID": "ep-1",
            "logID": 0,
            "nativeEventID": 4625,
            "seriesKey": "a=1",
            "periodTs": 300_000.0,
        },
        {
            "endpointID": "ep-1",
            "logID": 0,
            "nativeEventID": 4624,
            "seriesKey": "b=2",
            "periodTs": 300_000.0,
        },
    ]
    entries = [
        {
            "endpointID": "ep-1",
            "logID": 0,
            "nativeEventID": 4625,
            "seriesKey": "a=1",
            "periodMs": None,
        }
    ]
    filtered = filter_alerts_against_whitelist(alerts, entries)
    assert len(filtered) == 1
    assert filtered[0]["nativeEventID"] == 4624


def test_predictive_create_from_series_identity(temp_db):
    admin, _endpoint_id = _seed_org_endpoint()
    entry = database.create_alert_whitelist_entry(
        organization_id=admin["organizationID"],
        created_by_account_id=admin["accountID"],
        endpoint_id=None,
        log_id=1,
        native_event_id=3,
        series_key="",
        period_ms=None,
        note="Acme Updater company rollout",
        series_identity={
            "protocol": "tcp",
            "image": r"C:\Program Files\Acme\update.exe",
            "destinationPort": "443",
        },
    )
    assert entry["scope"] == "organization"
    assert "image=" in entry["seriesKey"]
    assert "destinationPort=443" in entry["seriesKey"]
    assert entry["periodMs"] is None
