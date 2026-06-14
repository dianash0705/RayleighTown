import argparse
from datetime import timedelta

from flask import Flask

from auth import load_or_create_secret_key
from bootstrap import validate_runtime_environment
from routes import register_routes


def create_app():
    validate_runtime_environment()
    app = Flask(__name__)
    app.secret_key = load_or_create_secret_key()
    app.permanent_session_lifetime = timedelta(days=7)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    register_routes(app)
    return app

app = create_app()


def parse_args():
    parser = argparse.ArgumentParser(description="Run the backend web app.")
    parser.add_argument("--host", default="0.0.0.0", help="Host interface to bind to.")
    parser.add_argument("--port", type=int, default=2222, help="Port to bind to.")
    parser.add_argument("--debug", action="store_true", help="Enable Flask debug mode.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    app.run(host=args.host, port=args.port, debug=args.debug)
