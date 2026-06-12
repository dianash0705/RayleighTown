from datetime import datetime
import json
from pathlib import Path
from uuid import uuid4

from flask import jsonify, redirect, request, send_from_directory
from werkzeug.utils import secure_filename

from alert_filters import build_alert_filters
from config import UPLOAD_DIR
from database import fetch_alert_detail, fetch_alerts, fetch_entities, insert_events, upsert_endpoint
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


def register_routes(app):
    @app.get("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    @app.get("/style.css")
    def style_css():
        return send_from_directory(STATIC_DIR, "style.css")

    @app.get("/script.js")
    def script_js():
        return send_from_directory(STATIC_DIR, "script.js")

    @app.get("/entities")
    def entities_page():
        return send_from_directory(STATIC_DIR, "entities.html")

    @app.get("/entities.js")
    def entities_js():
        return send_from_directory(STATIC_DIR, "entities.js")

    @app.get("/alerts.html")
    def legacy_alerts_page():
        return redirect("/", code=301)

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
    def get_entities():
        payload = fetch_entities()
        return jsonify(
            {
                "count": len(payload["entities"]),
                "windowStart": payload["windowStart"],
                "windowEnd": payload["windowEnd"],
                "entities": payload["entities"],
            }
        ), 200

    @app.get("/api/alerts")
    def get_alerts():
        filters = build_alert_filters(request.args)
        alerts = fetch_alerts(filters)
        return jsonify(
            {
                "count": len(alerts),
                "filters": {
                    "endpointID": filters.endpoint_id,
                    "nativeEventID": filters.native_event_id,
                    "eventName": filters.event_name,
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
    def get_alert_detail(alert_group_id: int):
        alert = fetch_alert_detail(alert_group_id)
        if alert is None:
            return jsonify({"error": f"Alert group {alert_group_id} not found."}), 404
        return jsonify(alert), 200

    @app.post("/api/logs/upload")
    def upload_log():
        endpoint_id = request.form.get("endpointID")
        if not endpoint_id:
            return jsonify({"error": "Missing form field 'endpointID'."}), 400

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

        inserted_count = insert_events(endpoint_id, log_id, whitelisted_events)
        upsert_endpoint(endpoint_id, hostname, ip, int(datetime.utcnow().timestamp() * 1000))

        return jsonify(
            {
                "message": "Log processed successfully.",
                "endpointID": endpoint_id,
                "inserted": inserted_count,
                "logID": log_id,
                "logType": log_config["name"],
                "sourceName": source_name if source_name else None,
                "filename": saved_name,
            }
        ), 201
