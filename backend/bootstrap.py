from config import DB_DIR, DB_PATH, UPLOAD_DIR
from database import init_db


def setup_environment_once():
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    DB_DIR.mkdir(parents=True, exist_ok=True)
    init_db()


def validate_runtime_environment():
    if not UPLOAD_DIR.exists() or not UPLOAD_DIR.is_dir():
        raise RuntimeError("Environment not initialized: uploads directory is missing.")

    if not DB_DIR.exists() or not DB_DIR.is_dir():
        raise RuntimeError("Environment not initialized: data directory is missing.")

    if not DB_PATH.exists() or not DB_PATH.is_file():
        raise RuntimeError("Environment not initialized: database file is missing. Run setup_environment.py first.")

