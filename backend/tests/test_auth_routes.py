"""Tests for the registration/login HTTP routes and admin endpoints."""

import pytest
from flask import Flask

import database
from routes import register_routes


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "routes_test.db"
    monkeypatch.setattr(database, "DB_PATH", db_path)
    database.init_db()

    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.testing = True
    register_routes(app)
    return app.test_client()


def _register(client, org="Acme", user="root", password="pw123"):
    return client.post(
        "/api/auth/register",
        json={"organizationName": org, "username": user, "password": password},
    )


# --- Registration / login / session ---


def test_register_logs_in_and_me_works(client):
    response = _register(client)
    assert response.status_code == 201
    account = response.get_json()["account"]
    assert account["isSuperAdmin"] is True

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.get_json()["account"]["name"] == "root"


def test_duplicate_org_registration_conflicts(client):
    _register(client)
    client.post("/api/auth/logout")
    response = _register(client)
    assert response.status_code == 409
    body = response.get_json()
    assert body["code"] == "ORG_EXISTS"
    assert "already exists" in body["error"].lower()


def test_login_wrong_password_rejected(client):
    _register(client)
    client.post("/api/auth/logout")
    bad = client.post(
        "/api/auth/login",
        json={"organizationName": "Acme", "username": "root", "password": "nope"},
    )
    assert bad.status_code == 401
    body = bad.get_json()
    assert body["code"] == "BAD_PASSWORD"
    assert "password" in body["error"].lower()


def test_login_unknown_organization(client):
    response = client.post(
        "/api/auth/login",
        json={"organizationName": "MissingOrg", "username": "root", "password": "pw123"},
    )
    assert response.status_code == 404
    body = response.get_json()
    assert body["code"] == "ORG_NOT_FOUND"
    assert "organization" in body["error"].lower()


def test_login_unknown_user(client):
    _register(client)
    client.post("/api/auth/logout")
    response = client.post(
        "/api/auth/login",
        json={"organizationName": "Acme", "username": "nobody", "password": "pw123"},
    )
    assert response.status_code == 401
    body = response.get_json()
    assert body["code"] == "USER_NOT_FOUND"
    assert "username" in body["error"].lower()


def test_data_apis_require_login(client):
    assert client.get("/api/dashboard").status_code == 401
    assert client.get("/api/alerts").status_code == 401
    assert client.get("/api/entities").status_code == 401


def test_logout_clears_session(client):
    _register(client)
    assert client.get("/api/auth/me").status_code == 200
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401


# --- Admin: users ---


def test_admin_can_add_and_list_users(client):
    _register(client)
    created = client.post("/api/admin/users", json={"username": "alice", "password": "pw"})
    assert created.status_code == 201

    listing = client.get("/api/admin/users").get_json()["users"]
    assert {u["name"] for u in listing} == {"root", "alice"}


def test_only_super_admin_creates_admins(client):
    _register(client)  # super admin
    # super admin makes a plain admin
    client.post("/api/admin/users", json={"username": "mod", "password": "pw", "isAdmin": True})
    client.post("/api/auth/logout")

    # log in as the plain admin and try to mint another admin
    client.post(
        "/api/auth/login",
        json={"organizationName": "Acme", "username": "mod", "password": "pw"},
    )
    forbidden = client.post("/api/admin/users", json={"username": "x", "password": "pw", "isAdmin": True})
    assert forbidden.status_code == 403
    # but creating a plain member is fine
    assert client.post("/api/admin/users", json={"username": "x", "password": "pw"}).status_code == 201


def test_non_admin_cannot_reach_admin_api(client):
    _register(client)
    client.post("/api/admin/users", json={"username": "bob", "password": "pw"})
    client.post("/api/auth/logout")
    client.post(
        "/api/auth/login",
        json={"organizationName": "Acme", "username": "bob", "password": "pw"},
    )
    assert client.get("/api/admin/users").status_code == 403


def test_cannot_delete_self_or_super_admin(client):
    me = _register(client).get_json()["account"]
    # cannot delete self
    assert client.delete(f"/api/admin/users/{me['accountID']}").status_code == 400


# --- Admin: endpoints + upload auth ---


def test_register_endpoint_and_upload_auth(client):
    _register(client)
    created = client.post("/api/admin/endpoints", json={"name": "Laptop"})
    assert created.status_code == 201
    endpoint = created.get_json()["endpoint"]
    assert endpoint["secret"]

    missing_name = client.post("/api/admin/endpoints", json={"name": "   "})
    assert missing_name.status_code == 400

    # Upload without a secret is rejected up front.
    no_secret = client.post("/api/logs/upload", data={"endpointID": endpoint["endpointID"]})
    assert no_secret.status_code == 401

    # Wrong secret is rejected too.
    wrong = client.post(
        "/api/logs/upload",
        data={"endpointID": endpoint["endpointID"], "endpointSecret": "bad"},
    )
    assert wrong.status_code == 401
