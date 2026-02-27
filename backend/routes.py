from datetime import datetime
from uuid import uuid4

from flask import jsonify, request
from werkzeug.utils import secure_filename

from config import UPLOAD_DIR
from database import insert_events
from log_registry import LOG_TYPE_CONFIG


def register_routes(app):
    @app.post("/api/logs/upload")
    def upload_log():
        endpoint_id = request.form.get("endpointID")
        if not endpoint_id:
            return jsonify({"error": "Missing form field 'endpointID'."}), 400

        log_id_raw = request.form.get("logID", "0")
        try:
            log_id = int(log_id_raw)
        except ValueError:
            return jsonify({"error": "Invalid form field 'logID'. Must be an integer."}), 400

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

        try:
            extractor = log_config["extractor"]
            event_id_whitelist = log_config["event_id_whitelist"]
            whitelisted_events = extractor(destination, event_id_whitelist)
        except RuntimeError as err:
            return jsonify({"error": str(err)}), 400

        inserted_count = insert_events(endpoint_id, log_id, whitelisted_events)

        return jsonify(
            {
                "message": "Log processed successfully.",
                "endpointID": endpoint_id,
                "inserted": inserted_count,
                "logID": log_id,
                "logType": log_config["name"],
                "filename": saved_name,
            }
        ), 201
