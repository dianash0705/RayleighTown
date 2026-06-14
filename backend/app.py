import argparse
import logging
import os
from datetime import timedelta

from flask import Flask

from auth import load_or_create_secret_key
from bootstrap import validate_runtime_environment
from routes import register_routes


def configure_logging() -> None:
    if logging.getLogger().handlers:
        return
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


configure_logging()


def create_app():
    validate_runtime_environment()
    app = Flask(__name__)
    app.secret_key = load_or_create_secret_key()
    app.permanent_session_lifetime = timedelta(days=7)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    register_routes(app)
    return app


def _should_start_analysis_worker() -> bool:
    from config import INGESTION_CONFIG

    if not INGESTION_CONFIG.analysis_worker_enabled:
        return False
    if os.environ.get("DISABLE_ANALYSIS_WORKER") == "1":
        return False
    werkzeug_main = os.environ.get("WERKZEUG_RUN_MAIN")
    return werkzeug_main in (None, "true")


app = create_app()

if _should_start_analysis_worker():
    from analysis_queue import start_analysis_worker

    start_analysis_worker()


def parse_args():
    parser = argparse.ArgumentParser(description="Run the backend web app.")
    parser.add_argument("--host", default="0.0.0.0", help="Host interface to bind to.")
    parser.add_argument("--port", type=int, default=2222, help="Port to bind to.")
    parser.add_argument("--debug", action="store_true", help="Enable Flask debug mode.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    app.run(host=args.host, port=args.port, debug=args.debug)
