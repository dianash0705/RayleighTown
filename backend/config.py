from pathlib import Path

BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploads"
DB_DIR = BASE_DIR / "data"
DB_PATH = DB_DIR / "logs.db"
