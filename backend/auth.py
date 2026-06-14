"""Authentication helpers: password/secret hashing, session handling, and route guards.

Sessions are plain Flask signed cookies (no extra infrastructure). The signing
secret is generated once and persisted to disk so logins survive restarts.

Passwords and endpoint secrets are stored only as hashes (werkzeug PBKDF2), so a
database leak never exposes the original credentials.
"""

import secrets
from functools import wraps

from flask import jsonify, session
from werkzeug.security import check_password_hash, generate_password_hash

from config import SECRET_KEY_PATH

SESSION_ACCOUNT_KEY = "accountID"


def load_or_create_secret_key() -> bytes:
    """Return a stable Flask session secret, creating one on first run."""
    if SECRET_KEY_PATH.exists():
        data = SECRET_KEY_PATH.read_bytes()
        if data:
            return data

    SECRET_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    new_key = secrets.token_bytes(32)
    SECRET_KEY_PATH.write_bytes(new_key)
    return new_key


def hash_secret(plaintext: str) -> str:
    """Hash a password or endpoint secret for storage."""
    return generate_password_hash(plaintext)


def verify_secret(plaintext: str, stored_hash: str | None) -> bool:
    if not stored_hash:
        return False
    return check_password_hash(stored_hash, plaintext)


def generate_endpoint_secret() -> str:
    """Generate a high-entropy secret shown once to the admin at registration."""
    return secrets.token_urlsafe(24)


def generate_endpoint_id() -> str:
    """Generate a server-side endpoint identifier for a newly registered endpoint."""
    return "ep_" + secrets.token_hex(6)


def login_session(account_id: int) -> None:
    session.clear()
    session[SESSION_ACCOUNT_KEY] = int(account_id)
    session.permanent = True


def logout_session() -> None:
    session.clear()


def current_account():
    """Return the logged-in account dict, or None if not authenticated."""
    account_id = session.get(SESSION_ACCOUNT_KEY)
    if account_id is None:
        return None

    # Imported lazily to avoid import cycles (database imports brain, etc.).
    from database import get_account_by_id

    account = get_account_by_id(int(account_id))
    if account is None:
        session.clear()
    return account


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        account = current_account()
        if account is None:
            return jsonify({"error": "Authentication required."}), 401
        return view(account, *args, **kwargs)

    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        account = current_account()
        if account is None:
            return jsonify({"error": "Authentication required."}), 401
        if not account.get("isAdmin"):
            return jsonify({"error": "Administrator access required."}), 403
        return view(account, *args, **kwargs)

    return wrapped


def super_admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        account = current_account()
        if account is None:
            return jsonify({"error": "Authentication required."}), 401
        if not account.get("isSuperAdmin"):
            return jsonify({"error": "Super administrator access required."}), 403
        return view(account, *args, **kwargs)

    return wrapped
