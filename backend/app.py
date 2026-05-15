import argparse

from flask import Flask

from bootstrap import validate_runtime_environment
from routes import register_routes


def create_app():
    validate_runtime_environment()
    app = Flask(__name__)
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
