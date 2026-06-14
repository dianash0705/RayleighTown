from datetime import datetime
import json
import logging
from pathlib import Path
from uuid import uuid4

from flask import jsonify, redirect, request, send_from_directory
from werkzeug.utils import secure_filename

from alert_filters import build_alert_filters, resolve_time_window
from auth import (
    admin_required,
    login_required,
    login_session,
    logout_session,
    super_admin_required,
)
from config import REQUIRE_ENDPOINT_AUTH, UPLOAD_DIR
from database import (
    authenticate_account,
    create_account,
    create_organization_with_admin,
    delete_account,
    delete_endpoint,
    fetch_alert_detail,
    fetch_alerts,
    fetch_dashboard_stats,
    get_endpoint_secret,
    fetch_entities,
    get_account_by_id,
    get_organization_by_id,
    insert_events,
    list_accounts_for_organization,
    list_registered_endpoints,
    register_endpoint,
    reset_endpoint_secret,
    set_account_admin,
    upsert_endpoint,
    verify_endpoint_secret,
)
from log_registry import LOG_TYPE_CONFIG, LOG_SOURCE_MAP, all_event_names


def _source_name_from_json_file(log_path):
    try:
        with log_path.open("r", encoding="utf-8") as handle:
            first_line = handle.readline().strip()
    except OSError:
        return None

    if not first_line:
        return None

    try:
        record = json.loads(first_line)
    except json.JSONDecodeError:
        return None

    def _normalize_source(value):
        value = str(value)
        lowered = value.lower()
        if "sysmon" in lowered:
            return "Microsoft-Windows-Sysmon"
        if "security" in lowered:
            return "windows_security"
        return value

    def _lookup_source(payload):
        if not isinstance(payload, dict):
            return None

        for field in ("SourceName", "source", "Source"):
            value = payload.get(field)
            if value:
                return _normalize_source(value)

        for field in ("EventChannel", "Channel", "EventLogName"):
            value = payload.get(field)
            if not value:
                continue
            return _normalize_source(value)

        return None

    source_name = _lookup_source(record.get("result"))
    if source_name:
        return source_name

    return _lookup_source(record)


def _log_id_from_source_name(source_name):
    if not source_name:
        return None

    source_name_lower = source_name.lower()
    for key, configured_source_name in LOG_SOURCE_MAP.items():
        configured_lower = configured_source_name.lower()
        if (
            configured_lower == source_name_lower
            or configured_lower in source_name_lower
            or source_name_lower in configured_lower
        ):
            return int(key)

    if "sysmon" in source_name_lower:
        return 1
    if "security" in source_name_lower:
        return 0

    return 999


STATIC_DIR = Path(__file__).parent / "static"
logger = logging.getLogger(__name__)


def _account_public(account: dict) -> dict:
    """Shape an account for client responses (never includes the password hash)."""
    organization = get_organization_by_id(account["organizationID"])
    return {
        "accountID": account["accountID"],
        "organizationID": account["organizationID"],
        "organizationName": organization["name"] if organization else "",
        "name": account["name"],
        "isAdmin": account["isAdmin"],
        "isSuperAdmin": account["isSuperAdmin"],
    }


def register_routes(app):
    @app.get("/")
    def dashboard_page():
        return send_from_directory(STATIC_DIR, "dashboard.html")

    @app.get("/alerts")
    def alerts_page():
        return send_from_directory(STATIC_DIR, "index.html")

    @app.get("/dashboard.js")
    def dashboard_js():
        return send_from_directory(STATIC_DIR, "dashboard.js")

    @app.get("/style.css")
    def style_css():
        return send_from_directory(STATIC_DIR, "style.css")

    @app.get("/script.js")
    def script_js():
        return send_from_directory(STATIC_DIR, "script.js")

    @app.get("/relative_time.js")
    def relative_time_js():
        return send_from_directory(STATIC_DIR, "relative_time.js")

    @app.get("/ui.js")
    def ui_js():
        return send_from_directory(STATIC_DIR, "ui.js")

    @app.get("/time_range.js")
    def time_range_js():
        return send_from_directory(STATIC_DIR, "time_range.js")

    @app.get("/entities")
    def entities_page():
        return send_from_directory(STATIC_DIR, "entities.html")

    @app.get("/entities.js")
    def entities_js():
        return send_from_directory(STATIC_DIR, "entities.js")

    @app.get("/login")
    def login_page():
        return send_from_directory(STATIC_DIR, "login.html")

    @app.get("/login.js")
    def login_js():
        return send_from_directory(STATIC_DIR, "login.js")

    @app.get("/admin")
    def admin_page():
        return send_from_directory(STATIC_DIR, "admin.html")

    @app.get("/admin.js")
    def admin_js():
        return send_from_directory(STATIC_DIR, "admin.js")

    @app.get("/auth.js")
    def auth_js():
        return send_from_directory(STATIC_DIR, "auth.js")

    @app.get("/alerts.html")
    def legacy_alerts_page():
        return redirect("/alerts", code=301)

    # --- Authentication API ---

    @app.post("/api/auth/register")
    def register_organization():
        payload = request.get_json(silent=True) or {}
        organization_name = (payload.get("organizationName") or "").strip()
        admin_name = (payload.get("username") or "").strip()
        admin_password = payload.get("password") or ""

        if not organization_name or not admin_name or not admin_password:
            return jsonify({"error": "Organization name, username, and password are all required."}), 400

        try:
            account = create_organization_with_admin(organization_name, admin_name, admin_password)
        except ValueError as error:
            return jsonify({"error": str(error)}), 409

        login_session(account["accountID"])
        return jsonify({"account": _account_public(account)}), 201

    @app.post("/api/auth/login")
    def login():
        payload = request.get_json(silent=True) or {}
        organization_name = (payload.get("organizationName") or "").strip()
        account_name = (payload.get("username") or "").strip()
        password = payload.get("password") or ""

        if not organization_name or not account_name or not password:
            return jsonify({"error": "Organization name, username, and password are all required."}), 400

        account = authenticate_account(organization_name, account_name, password)
        if account is None:
            return jsonify({"error": "Invalid organization, username, or password."}), 401

        login_session(account["accountID"])
        return jsonify({"account": _account_public(account)}), 200

    @app.post("/api/auth/logout")
    def logout():
        logout_session()
        return jsonify({"message": "Logged out."}), 200

    @app.get("/api/auth/me")
    @login_required
    def whoami(account):
        return jsonify({"account": _account_public(account)}), 200

    # --- Admin: user management ---

    @app.get("/api/admin/users")
    @admin_required
    def list_users(account):
        users = list_accounts_for_organization(account["organizationID"])
        return jsonify({"users": [_account_public(user) for user in users]}), 200

    @app.post("/api/admin/users")
    @admin_required
    def add_user(account):
        payload = request.get_json(silent=True) or {}
        name = (payload.get("username") or "").strip()
        password = payload.get("password") or ""
        make_admin = bool(payload.get("isAdmin"))

        if not name or not password:
            return jsonify({"error": "Username and password are required."}), 400

        # Only the super admin may mint new admins directly.
        if make_admin and not account.get("isSuperAdmin"):
            return jsonify({"error": "Only the super administrator can create admin accounts."}), 403

        try:
            created = create_account(
                organization_id=account["organizationID"],
                name=name,
                password=password,
                is_admin=make_admin,
                created_by_account_id=account["accountID"],
            )
        except ValueError as error:
            return jsonify({"error": str(error)}), 409

        return jsonify({"account": _account_public(created)}), 201

    @app.delete("/api/admin/users/<int:user_id>")
    @admin_required
    def remove_user(account, user_id: int):
        if user_id == account["accountID"]:
            return jsonify({"error": "You cannot delete your own account."}), 400

        target = get_account_by_id(user_id)
        if target is None or target["organizationID"] != account["organizationID"]:
            return jsonify({"error": "User not found."}), 404
        if target["isSuperAdmin"]:
            return jsonify({"error": "The super administrator account cannot be deleted."}), 400

        delete_account(account["organizationID"], user_id)
        return jsonify({"message": "User deleted."}), 200

    @app.post("/api/admin/users/<int:user_id>/admin")
    @super_admin_required
    def change_user_admin(account, user_id: int):
        payload = request.get_json(silent=True) or {}
        make_admin = bool(payload.get("isAdmin"))

        target = get_account_by_id(user_id)
        if target is None or target["organizationID"] != account["organizationID"]:
            return jsonify({"error": "User not found."}), 404
        if target["isSuperAdmin"]:
            return jsonify({"error": "The super administrator is always an admin."}), 400

        set_account_admin(account["organizationID"], user_id, make_admin)
        updated = get_account_by_id(user_id)
        return jsonify({"account": _account_public(updated)}), 200

    # --- Admin: endpoint management ---

    @app.get("/api/admin/endpoints")
    @admin_required
    def list_endpoints(account):
        endpoints = list_registered_endpoints(account["organizationID"])
        return jsonify({"endpoints": endpoints}), 200

    @app.post("/api/admin/endpoints")
    @admin_required
    def add_endpoint(account):
        payload = request.get_json(silent=True) or {}
        display_name = (payload.get("name") or "").strip() or None
        registration = register_endpoint(account["organizationID"], display_name)
        # The secret is returned only here; it is never retrievable again.
        return jsonify({"endpoint": registration}), 201

    @app.delete("/api/admin/endpoints/<endpoint_id>")
    @admin_required
    def remove_endpoint(account, endpoint_id: str):
        deleted = delete_endpoint(account["organizationID"], endpoint_id)
        if not deleted:
            return jsonify({"error": "Endpoint not found."}), 404
        return jsonify({"message": "Endpoint deleted."}), 200

    @app.get("/api/admin/endpoints/<endpoint_id>/secret")
    @admin_required
    def show_endpoint_secret(account, endpoint_id: str):
        result = get_endpoint_secret(account["organizationID"], endpoint_id)
        if result is None:
            return jsonify({"error": "Endpoint not found."}), 404
        if not result.get("secret"):
            return (
                jsonify({"error": "This endpoint has no stored secret. Reset it to generate a new one."}),
                409,
            )
        return jsonify({"endpoint": result}), 200

    @app.post("/api/admin/endpoints/<endpoint_id>/secret/reset")
    @admin_required
    def reset_endpoint_secret_route(account, endpoint_id: str):
        result = reset_endpoint_secret(account["organizationID"], endpoint_id)
        if result is None:
            return jsonify({"error": "Endpoint not found."}), 404
        return jsonify({"endpoint": result}), 200

    @app.get("/api/dashboard")
    @login_required
    def get_dashboard(account):
        window_start_ms, window_end_ms, time_preset = resolve_time_window(request.args)
        return jsonify(
            fetch_dashboard_stats(
                window_start_ms,
                window_end_ms,
                time_preset,
                organization_id=account["organizationID"],
            )
        ), 200

    @app.get("/api/meta")
    def get_meta():
        return jsonify(
            {
                "eventNames": all_event_names(),
                "timePresets": [
                    {"id": "last_24h", "label": "Last 24 hours"},
                    {"id": "last_week", "label": "Last week"},
                    {"id": "all", "label": "All time"},
                    {"id": "custom", "label": "Custom range"},
                ],
            }
        ), 200

    @app.get("/api/entities")
    @login_required
    def get_entities(account):
        window_start_ms, window_end_ms, time_preset = resolve_time_window(request.args)
        payload = fetch_entities(
            window_start_ms,
            window_end_ms,
            time_preset,
            organization_id=account["organizationID"],
        )
        return jsonify(
            {
                "count": len(payload["entities"]),
                "windowStart": payload["windowStart"],
                "windowEnd": payload["windowEnd"],
                "timePreset": payload["timePreset"],
                "entities": payload["entities"],
            }
        ), 200

    @app.get("/api/alerts")
    @login_required
    def get_alerts(account):
        try:
            filters = build_alert_filters(request.args, organization_id=account["organizationID"])
        except ValueError as error:
            return jsonify({"error": str(error)}), 400
        alerts = fetch_alerts(filters)
        return jsonify(
            {
                "count": len(alerts),
                "filters": {
                    "rules": [
                        {
                            "field": rule.field,
                            "operator": rule.operator,
                            "value": rule.value,
                            **({"values": list(rule.values)} if rule.values else {}),
                        }
                        for rule in filters.rules
                    ],
                    "minConfidence": filters.min_confidence,
                    "timePreset": request.args.get("timePreset"),
                    "timeFrom": filters.window_start_ms,
                    "timeTo": filters.window_end_ms,
                    "sort": filters.sort_key,
                    "order": filters.sort_direction,
                },
                "alerts": alerts,
            }
        ), 200

    @app.get("/api/alerts/<int:alert_group_id>")
    @login_required
    def get_alert_detail(account, alert_group_id: int):
        alert = fetch_alert_detail(alert_group_id, organization_id=account["organizationID"])
        if alert is None:
            return jsonify({"error": f"Alert group {alert_group_id} not found."}), 404
        return jsonify(alert), 200

    @app.post("/api/logs/upload")
    def upload_log():
        endpoint_id = request.form.get("endpointID")
        if not endpoint_id:
            return jsonify({"error": "Missing form field 'endpointID'."}), 400

        # Endpoints authenticate with the secret issued at registration time.
        if REQUIRE_ENDPOINT_AUTH:
            endpoint_secret = request.form.get("endpointSecret") or ""
            if not endpoint_secret:
                return jsonify({"error": "Missing form field 'endpointSecret'."}), 401
            if not verify_endpoint_secret(endpoint_id, endpoint_secret):
                return jsonify({"error": "Invalid endpoint id or secret."}), 401

        hostname = (request.form.get("hostname") or "").strip() or None
        ip = (request.form.get("ip") or "").strip() or None

        log_id_raw = request.form.get("logID", "0")
        try:
            log_id = int(log_id_raw)
        except ValueError:
            return jsonify({"error": "Invalid form field 'logID'. Must be an integer."}), 400

        source_name = request.form.get("sourceName")
        if source_name:
            mapped = _log_id_from_source_name(source_name)
            if mapped is not None:
                log_id = int(mapped)

        log_config = LOG_TYPE_CONFIG.get(log_id)
        if log_config is None:
            return jsonify({"error": f"Unsupported logID: {log_id}."}), 400

        if "log_file" not in request.files:
            return jsonify({"error": "Missing file field 'log_file'."}), 400

        log_file = request.files["log_file"]
        if not log_file.filename:
            return jsonify({"error": "No selected file."}), 400

        safe_name = secure_filename(log_file.filename)
        stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        saved_name = f"{stamp}_{uuid4().hex}_{safe_name}"
        destination = UPLOAD_DIR / saved_name
        log_file.save(destination)

        if log_id == 0 and destination.suffix.lower() == ".json":
            inferred_source_name = _source_name_from_json_file(destination)
            if inferred_source_name:
                source_name = inferred_source_name
                mapped = _log_id_from_source_name(inferred_source_name)
                if mapped is not None:
                    log_id = int(mapped)
            if log_id == 0:
                log_id = 999

        log_config = LOG_TYPE_CONFIG.get(log_id)
        if log_config is None:
            return jsonify({"error": f"Unsupported logID: {log_id}."}), 400

        try:
            extractor = log_config["extractor"]
            event_id_whitelist = log_config["event_id_whitelist"]
            whitelisted_events = extractor(destination, event_id_whitelist, log_id)
        except RuntimeError as err:
            return jsonify({"error": str(err)}), 400

        inserted_result = insert_events(endpoint_id, log_id, whitelisted_events)
        upsert_endpoint(endpoint_id, hostname, ip, int(datetime.utcnow().timestamp() * 1000))

        queue_action = None
        if inserted_result.has_new_events:
            from analysis_queue import get_analysis_queue

            queue_action = get_analysis_queue().enqueue(endpoint_id, inserted_result.impacts)

        logger.info(
            "Log upload endpoint=%s logID=%s logType=%s inserted=%s skipped=%s "
            "extracted=%s eventTypes=%s analysisQueued=%s queueAction=%s filename=%s",
            endpoint_id,
            log_id,
            log_config["name"],
            inserted_result.inserted_count,
            inserted_result.skipped_count,
            len(whitelisted_events),
            sorted({impact.native_event_id for impact in inserted_result.impacts}),
            inserted_result.has_new_events,
            queue_action,
            saved_name,
        )

        return jsonify(
            {
                "message": "Log processed successfully.",
                "endpointID": endpoint_id,
                "inserted": inserted_result.inserted_count,
                "skipped": inserted_result.skipped_count,
                "analysisQueued": inserted_result.has_new_events,
                "logID": log_id,
                "logType": log_config["name"],
                "sourceName": source_name if source_name else None,
                "filename": saved_name,
            }
        ), 201
