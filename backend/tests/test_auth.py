"""Tests for the registration/login data layer: organizations, accounts, endpoints,
and organization-scoped reads."""

import sqlite3

import pytest

import database
from alert_filters import AlertQueryFilters


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "auth_test.db"
    monkeypatch.setattr(database, "DB_PATH", db_path)
    database.init_db()
    return db_path


def _insert_alert_group(db_path, endpoint_id, *, native_event_id=4624, ts_begin=1_000, ts_end=2_000, confidence=90):
    """Insert a minimal alert group with one child window/alert and return its group id."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO alertGroups (endpointID, nativeEventID, logID, tsBegin, tsEnd, periodTs, confidence, phase)
        VALUES (?, ?, 0, ?, ?, 60000, ?, 0.0)
        """,
        (endpoint_id, native_event_id, ts_begin, ts_end, confidence),
    )
    group_id = cursor.lastrowid
    cursor.execute(
        """
        INSERT INTO alerts (endpointID, nativeEventID, logID, tsBegin, tsEnd, periodTs, confidence, phase)
        VALUES (?, ?, 0, ?, ?, 60000, ?, 0.0)
        """,
        (endpoint_id, native_event_id, ts_begin, ts_end, confidence),
    )
    alert_id = cursor.lastrowid
    cursor.execute(
        "INSERT INTO alertGroupMap (alertGroupID, alertID) VALUES (?, ?)",
        (group_id, alert_id),
    )
    conn.commit()
    conn.close()
    return group_id


# --- Organizations & accounts ---


def test_create_organization_creates_super_admin(temp_db):
    admin = database.create_organization_with_admin("Acme", "root", "pw123")
    assert admin["name"] == "root"
    assert admin["isAdmin"] is True
    assert admin["isSuperAdmin"] is True
    assert admin["organizationID"] > 0


def test_duplicate_organization_name_rejected(temp_db):
    database.create_organization_with_admin("Acme", "root", "pw123")
    with pytest.raises(ValueError):
        database.create_organization_with_admin("acme", "other", "pw123")


def test_authenticate_account(temp_db):
    database.create_organization_with_admin("Acme", "root", "pw123")
    assert database.authenticate_account("Acme", "root", "pw123") is not None
    assert database.authenticate_account("Acme", "root", "wrong") is None
    assert database.authenticate_account("Other", "root", "pw123") is None


def test_create_and_delete_account(temp_db):
    admin = database.create_organization_with_admin("Acme", "root", "pw123")
    org_id = admin["organizationID"]
    user = database.create_account(org_id, "alice", "pw", is_admin=False, created_by_account_id=admin["accountID"])
    assert user["isAdmin"] is False

    with pytest.raises(ValueError):
        database.create_account(org_id, "alice", "pw2", is_admin=False, created_by_account_id=admin["accountID"])

    assert database.delete_account(org_id, user["accountID"]) is True
    assert database.get_account_by_id(user["accountID"]) is None


def test_set_account_admin_cannot_touch_super_admin(temp_db):
    admin = database.create_organization_with_admin("Acme", "root", "pw123")
    org_id = admin["organizationID"]
    user = database.create_account(org_id, "alice", "pw", is_admin=False, created_by_account_id=admin["accountID"])

    assert database.set_account_admin(org_id, user["accountID"], True) is True
    assert database.get_account_by_id(user["accountID"])["isAdmin"] is True

    # The super admin row is shielded from demotion.
    assert database.set_account_admin(org_id, admin["accountID"], False) is False
    assert database.get_account_by_id(admin["accountID"])["isAdmin"] is True


# --- Endpoint registration ---


def test_register_endpoint_returns_one_time_secret(temp_db):
    admin = database.create_organization_with_admin("Acme", "root", "pw123")
    org_id = admin["organizationID"]
    registration = database.register_endpoint(org_id, "Finance laptop")

    assert registration["endpointID"].startswith("ep_")
    assert registration["secret"]
    assert database.verify_endpoint_secret(registration["endpointID"], registration["secret"]) is True
    assert database.verify_endpoint_secret(registration["endpointID"], "nope") is False
    assert database.verify_endpoint_secret("ep_missing", registration["secret"]) is False

    listed = database.list_registered_endpoints(org_id)
    assert len(listed) == 1
    assert listed[0]["displayName"] == "Finance laptop"
    # The plaintext secret is never returned in the listing, only a flag.
    assert "secret" not in listed[0]
    assert listed[0]["hasSecret"] is True


def test_show_endpoint_secret_returns_stored_value(temp_db):
    admin = database.create_organization_with_admin("Acme", "root", "pw123")
    org_id = admin["organizationID"]
    registration = database.register_endpoint(org_id, "Finance laptop")

    shown = database.get_endpoint_secret(org_id, registration["endpointID"])
    assert shown is not None
    assert shown["secret"] == registration["secret"]

    # Scoped to the org: another org cannot read the secret.
    other = database.create_organization_with_admin("Beta", "root", "pw123")
    assert database.get_endpoint_secret(other["organizationID"], registration["endpointID"]) is None


def test_reset_endpoint_secret_rotates_and_invalidates_old(temp_db):
    admin = database.create_organization_with_admin("Acme", "root", "pw123")
    org_id = admin["organizationID"]
    registration = database.register_endpoint(org_id, None)
    endpoint_id = registration["endpointID"]
    old_secret = registration["secret"]

    rotated = database.reset_endpoint_secret(org_id, endpoint_id)
    assert rotated is not None
    assert rotated["secret"] != old_secret

    # Old secret no longer authenticates; the new one does.
    assert database.verify_endpoint_secret(endpoint_id, old_secret) is False
    assert database.verify_endpoint_secret(endpoint_id, rotated["secret"]) is True
    # The new secret is also retrievable for re-display.
    assert database.get_endpoint_secret(org_id, endpoint_id)["secret"] == rotated["secret"]

    # Reset is scoped to the owning org.
    other = database.create_organization_with_admin("Beta", "root", "pw123")
    assert database.reset_endpoint_secret(other["organizationID"], endpoint_id) is None


def test_delete_endpoint_scoped_to_org(temp_db):
    admin_a = database.create_organization_with_admin("Acme", "root", "pw123")
    admin_b = database.create_organization_with_admin("Beta", "root", "pw123")
    endpoint = database.register_endpoint(admin_a["organizationID"], None)

    # Org B cannot delete org A's endpoint.
    assert database.delete_endpoint(admin_b["organizationID"], endpoint["endpointID"]) is False
    assert database.delete_endpoint(admin_a["organizationID"], endpoint["endpointID"]) is True


# --- Organization-scoped reads ---


def test_reads_are_scoped_to_organization(temp_db):
    admin_a = database.create_organization_with_admin("Acme", "root", "pw123")
    admin_b = database.create_organization_with_admin("Beta", "root", "pw123")
    org_a = admin_a["organizationID"]
    org_b = admin_b["organizationID"]

    endpoint_a = database.register_endpoint(org_a, "A-host")["endpointID"]
    endpoint_b = database.register_endpoint(org_b, "B-host")["endpointID"]

    _insert_alert_group(temp_db, endpoint_a, ts_begin=1_000, ts_end=2_000)
    _insert_alert_group(temp_db, endpoint_b, ts_begin=1_000, ts_end=2_000)

    # Entities
    entities_a = database.fetch_entities(0, 10_000, "all", organization_id=org_a)["entities"]
    assert {e["endpointID"] for e in entities_a} == {endpoint_a}

    # Alerts
    alerts_a = database.fetch_alerts(
        AlertQueryFilters(window_start_ms=0, window_end_ms=10_000, organization_id=org_a)
    )
    assert {a["endpointID"] for a in alerts_a} == {endpoint_a}

    # Dashboard
    dash_a = database.fetch_dashboard_stats(0, 10_000, "all", organization_id=org_a)
    assert dash_a["summary"]["activeInWindow"] == 1
    assert {e["endpointID"] for e in dash_a["topEndpoints"]} == {endpoint_a}


def test_alert_detail_blocked_cross_org(temp_db):
    admin_a = database.create_organization_with_admin("Acme", "root", "pw123")
    admin_b = database.create_organization_with_admin("Beta", "root", "pw123")
    endpoint_a = database.register_endpoint(admin_a["organizationID"], None)["endpointID"]
    group_id = _insert_alert_group(temp_db, endpoint_a)

    assert database.fetch_alert_detail(group_id, organization_id=admin_a["organizationID"]) is not None
    assert database.fetch_alert_detail(group_id, organization_id=admin_b["organizationID"]) is None
