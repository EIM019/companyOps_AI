from functools import wraps

from flask import jsonify, request

from .db import get_db, row_to_dict


def current_user():
    token = request.headers.get("Authorization", "").replace("Bearer ", "").strip()
    if not token:
        return None
    row = get_db().execute("SELECT * FROM users WHERE auth_token = ?", (token,)).fetchone()
    return row_to_dict(row)


def require_user(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user:
            return jsonify({"error": "Unauthorized"}), 401
        return fn(user, *args, **kwargs)

    return wrapper


def require_admin(fn):
    @wraps(fn)
    def wrapper(user, *args, **kwargs):
        if user["role"] not in ("owner", "admin"):
            return jsonify({"error": "Admin access required"}), 403
        return fn(user, *args, **kwargs)

    return wrapper

