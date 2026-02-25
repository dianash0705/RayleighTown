from datetime import datetime
from pathlib import Path
from uuid import uuid4

from flask import Flask, jsonify, request
from werkzeug.utils import secure_filename

app = Flask(__name__)
UPLOAD_DIR = Path(__file__).parent / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


@app.post("/api/logs/upload")
def upload_log():
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

    return jsonify({
        "message": "Log uploaded successfully.",
        "filename": saved_name,
    }), 201


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
