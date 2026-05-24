import json
import os
import secrets
import urllib.parse
import urllib.request

from flask import Blueprint, Response, current_app, jsonify, redirect, request, stream_with_context
from werkzeug.security import check_password_hash, generate_password_hash

from .assistant import openai_assistant_response, save_message, thread_messages
from .auth import require_admin, require_user
from .db import create_invite, get_db, row_to_dict, utcnow
from .documents import extract_text
from .openai_files import upload_to_vector_store
from .retrieval import backfill_document_chunks, index_document_chunks
from .tools import execute_confirmed_action

api = Blueprint("api", __name__)


def json_error(message, status=400):
    return jsonify({"error": message}), status


@api.get("/health")
def health():
    return jsonify({"ok": True})


@api.post("/auth/login")
def login():
    data = request.get_json(force=True)
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")
    user = get_db().execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if not user or not user["password_hash"] or not check_password_hash(user["password_hash"], password):
        return json_error("Invalid email or password", 401)
    return jsonify({"auth_token": user["auth_token"], "user": row_to_dict(user)})


@api.post("/auth/invites")
@require_user
@require_admin
def create_invite_route(user):
    data = request.get_json(force=True)
    email = data.get("email", "").strip().lower()
    role = data.get("role", "member")
    if not email:
        return json_error("Email is required")
    if role not in ("admin", "member"):
        return json_error("Role must be admin or member")
    token = create_invite(user["company_id"], email, role, user["id"])
    return jsonify({"token": token, "email": email, "role": role})


@api.post("/auth/accept-invite")
def accept_invite():
    data = request.get_json(force=True)
    token = data.get("token", "")
    name = data.get("name", "").strip()
    password = data.get("password", "")
    if not token or not name or len(password) < 8:
        return json_error("Token, name, and an 8 character password are required")

    db = get_db()
    invite = db.execute(
        "SELECT * FROM invites WHERE token = ? AND accepted_at IS NULL", (token,)
    ).fetchone()
    if not invite:
        return json_error("Invite not found", 404)

    auth_token = secrets.token_urlsafe(32)
    db.execute(
        """
        INSERT INTO users (company_id, email, name, role, password_hash, auth_token, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (invite["company_id"], invite["email"], name, invite["role"], generate_password_hash(password), auth_token, utcnow()),
    )
    db.execute("UPDATE invites SET accepted_at = ? WHERE id = ?", (utcnow(), invite["id"]))
    db.commit()
    return jsonify({"auth_token": auth_token, "email": invite["email"], "role": invite["role"]})


@api.get("/me")
@require_user
def me(user):
    company = get_db().execute("SELECT * FROM companies WHERE id = ?", (user["company_id"],)).fetchone()
    return jsonify({"user": user, "company": row_to_dict(company)})


@api.get("/chat/threads")
@require_user
def list_threads(user):
    rows = get_db().execute(
        """
        SELECT * FROM chat_threads
        WHERE company_id = ? AND user_id = ?
        ORDER BY created_at DESC
        """,
        (user["company_id"], user["id"]),
    ).fetchall()
    return jsonify({"threads": [row_to_dict(row) for row in rows]})


@api.post("/chat/threads")
@require_user
def create_thread(user):
    data = request.get_json(silent=True) or {}
    title = data.get("title") or "New chat"
    db = get_db()
    db.execute(
        "INSERT INTO chat_threads (company_id, user_id, title, created_at) VALUES (?, ?, ?, ?)",
        (user["company_id"], user["id"], title, utcnow()),
    )
    db.commit()
    thread_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    thread = db.execute("SELECT * FROM chat_threads WHERE id = ?", (thread_id,)).fetchone()
    return jsonify({"thread": row_to_dict(thread)}), 201


@api.get("/chat/threads/<int:thread_id>/messages")
@require_user
def get_thread_messages(user, thread_id):
    thread = get_db().execute(
        "SELECT id FROM chat_threads WHERE id = ? AND company_id = ? AND user_id = ?",
        (thread_id, user["company_id"], user["id"]),
    ).fetchone()
    if not thread:
        return json_error("Thread not found", 404)
    return jsonify({"messages": thread_messages(thread_id, user["company_id"])})


@api.post("/chat/threads/<int:thread_id>/messages")
@require_user
def post_message(user, thread_id):
    data = request.get_json(force=True)
    content = data.get("content", "").strip()
    if not content:
        return json_error("Message content is required")

    db = get_db()
    thread = db.execute(
        "SELECT * FROM chat_threads WHERE id = ? AND company_id = ? AND user_id = ?",
        (thread_id, user["company_id"], user["id"]),
    ).fetchone()
    if not thread:
        return json_error("Thread not found", 404)

    save_message(thread_id, user["company_id"], user["id"], "user", content)
    messages = thread_messages(thread_id, user["company_id"])
    assistant = openai_assistant_response(user["company_id"], user["id"], messages)
    save_message(
        thread_id,
        user["company_id"],
        user["id"],
        "assistant",
        assistant["content"],
        {"pending_actions": assistant.get("pending_actions", [])},
    )

    def event_stream():
        yield f"data: {json.dumps({'type': 'token', 'content': assistant['content']})}\n\n"
        yield f"data: {json.dumps({'type': 'done', 'pending_actions': assistant.get('pending_actions', [])})}\n\n"

    return Response(stream_with_context(event_stream()), mimetype="text/event-stream")


@api.post("/actions/<int:action_id>/confirm")
@require_user
def confirm_action(user, action_id):
    data = request.get_json(silent=True) or {}
    decision = data.get("decision", "confirm")
    if decision not in ("confirm", "reject"):
        return json_error("Decision must be confirm or reject")

    db = get_db()
    action = db.execute(
        "SELECT * FROM pending_actions WHERE id = ? AND company_id = ? AND user_id = ?",
        (action_id, user["company_id"], user["id"]),
    ).fetchone()
    if not action:
        return json_error("Action not found", 404)
    if action["status"] != "pending":
        return json_error("Action already decided", 409)

    if decision == "reject":
        db.execute(
            "UPDATE pending_actions SET status = 'rejected', decided_at = ? WHERE id = ?",
            (utcnow(), action_id),
        )
        db.commit()
        return jsonify({"action": {"id": action_id, "status": "rejected"}})

    result = execute_confirmed_action(action, user["id"])
    db.execute(
        """
        UPDATE pending_actions
        SET status = 'confirmed', result = ?, decided_at = ?
        WHERE id = ?
        """,
        (json.dumps(result), utcnow(), action_id),
    )
    db.execute(
        "INSERT INTO audit_logs (company_id, user_id, event, payload, created_at) VALUES (?, ?, ?, ?, ?)",
        (user["company_id"], user["id"], f"action_confirmed:{action['action_type']}", json.dumps(result), utcnow()),
    )
    db.commit()
    return jsonify({"action": {"id": action_id, "status": "confirmed", "result": result}})


@api.post("/documents/upload")
@require_user
def upload_document(user):
    uploaded = request.files.get("file")
    if not uploaded:
        return json_error("File is required")
    try:
        text = extract_text(uploaded)
    except ValueError as exc:
        return json_error(str(exc))
    openai_file_id = upload_to_vector_store(user["company_id"], uploaded)

    db = get_db()
    db.execute(
        """
        INSERT INTO documents (company_id, uploaded_by, filename, content_type, extracted_text, openai_file_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (user["company_id"], user["id"], uploaded.filename, uploaded.content_type or "", text, openai_file_id, utcnow()),
    )
    db.commit()
    document_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    index_document_chunks(user["company_id"], document_id, text)
    return jsonify({"document": {"id": document_id, "filename": uploaded.filename, "characters": len(text)}}), 201


@api.get("/projects")
@require_user
def list_projects_route(user):
    rows = get_db().execute(
        "SELECT * FROM projects WHERE company_id = ? ORDER BY created_at DESC", (user["company_id"],)
    ).fetchall()
    return jsonify({"projects": [row_to_dict(row) for row in rows]})


@api.post("/projects")
@require_user
def create_project(user):
    data = request.get_json(force=True)
    name = data.get("name", "").strip()
    if not name:
        return json_error("Project name is required")
    db = get_db()
    db.execute(
        "INSERT INTO projects (company_id, name, description, source, created_at) VALUES (?, ?, ?, ?, ?)",
        (user["company_id"], name, data.get("description", ""), "manual", utcnow()),
    )
    db.commit()
    project_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    project = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    return jsonify({"project": row_to_dict(project)}), 201


@api.get("/tasks")
@require_user
def list_tasks(user):
    rows = get_db().execute(
        """
        SELECT tasks.*, projects.name AS project_name
        FROM tasks
        LEFT JOIN projects ON projects.id = tasks.project_id
        WHERE tasks.company_id = ?
        ORDER BY tasks.created_at DESC
        """,
        (user["company_id"],),
    ).fetchall()
    return jsonify({"tasks": [row_to_dict(row) for row in rows]})


@api.post("/tasks")
@require_user
def create_task_direct(user):
    data = request.get_json(force=True)
    title = data.get("title", "").strip()
    if not title:
        return json_error("Task title is required")
    from .tools import create_task

    task = create_task(user["company_id"], data)
    return jsonify({"task": task}), 201


@api.post("/integrations/google-calendar/connect")
@require_user
def connect_google_calendar(user):
    client_id = current_app.config.get("GOOGLE_CLIENT_ID")
    redirect_uri = current_app.config.get("GOOGLE_REDIRECT_URI")
    if client_id and redirect_uri:
        state = secrets.token_urlsafe(24)
        db = get_db()
        db.execute(
            "INSERT INTO oauth_states (company_id, user_id, provider, state, created_at) VALUES (?, ?, ?, ?, ?)",
            (user["company_id"], user["id"], "google-calendar", state, utcnow()),
        )
        db.commit()
        params = urllib.parse.urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": "https://www.googleapis.com/auth/calendar.events",
                "access_type": "offline",
                "prompt": "consent",
                "state": state,
            }
        )
        return jsonify({"auth_url": f"https://accounts.google.com/o/oauth2/v2/auth?{params}"})
    return connect_integration(user, "google-calendar")


@api.get("/integrations/google-calendar/callback")
def google_calendar_callback():
    code = request.args.get("code")
    state = request.args.get("state")
    if not code or not state:
        return json_error("Missing OAuth code or state")

    db = get_db()
    saved_state = db.execute(
        "SELECT * FROM oauth_states WHERE state = ? AND provider = ?", (state, "google-calendar")
    ).fetchone()
    if not saved_state:
        return json_error("OAuth state not found", 404)

    payload = urllib.parse.urlencode(
        {
            "code": code,
            "client_id": current_app.config.get("GOOGLE_CLIENT_ID"),
            "client_secret": current_app.config.get("GOOGLE_CLIENT_SECRET"),
            "redirect_uri": current_app.config.get("GOOGLE_REDIRECT_URI"),
            "grant_type": "authorization_code",
        }
    ).encode("utf-8")
    request_obj = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request_obj, timeout=15) as response:
            token_data = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        current_app.logger.warning("Google OAuth callback failed: %s", exc)
        return json_error("Google OAuth token exchange failed", 502)

    now = utcnow()
    db.execute(
        """
        INSERT INTO integrations (company_id, user_id, provider, status, config, token_ref, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(company_id, provider)
        DO UPDATE SET user_id = excluded.user_id, status = excluded.status, config = excluded.config, updated_at = excluded.updated_at
        """,
        (
            saved_state["company_id"],
            saved_state["user_id"],
            "google-calendar",
            "connected",
            json.dumps(token_data),
            "google-oauth",
            now,
            now,
        ),
    )
    db.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
    db.commit()
    frontend_url = os.environ.get("FRONTEND_URL", "http://127.0.0.1:5173").rstrip("/")
    return redirect(f"{frontend_url}?integration=google-calendar")


@api.post("/integrations/trello/connect")
@require_user
def connect_trello(user):
    return connect_integration(user, "trello")


def connect_integration(user, provider):
    data = request.get_json(silent=True) or {}
    db = get_db()
    db.execute(
        """
        INSERT INTO integrations (company_id, user_id, provider, status, config, token_ref, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(company_id, provider)
        DO UPDATE SET status = excluded.status, config = excluded.config, updated_at = excluded.updated_at
        """,
        (
            user["company_id"],
            user["id"],
            provider,
            "connected",
            json.dumps(data.get("config", {})),
            data.get("token_ref", f"demo-{provider}-token-ref"),
            utcnow(),
            utcnow(),
        ),
    )
    db.commit()
    return jsonify({"integration": {"provider": provider, "status": "connected"}})


@api.get("/admin/summary")
@require_user
@require_admin
def admin_summary(user):
    db = get_db()
    counts = {}
    for table in ("users", "documents", "projects", "tasks", "pending_actions", "integrations"):
        counts[table] = db.execute(f"SELECT COUNT(*) FROM {table} WHERE company_id = ?", (user["company_id"],)).fetchone()[0]
    company = db.execute("SELECT * FROM companies WHERE id = ?", (user["company_id"],)).fetchone()
    return jsonify({"company": row_to_dict(company), "counts": counts})


@api.get("/admin/users")
@require_user
@require_admin
def admin_users(user):
    rows = get_db().execute(
        "SELECT id, email, name, role, created_at FROM users WHERE company_id = ? ORDER BY created_at DESC",
        (user["company_id"],),
    ).fetchall()
    return jsonify({"users": [row_to_dict(row) for row in rows]})


@api.patch("/admin/users/<int:user_id>")
@require_user
@require_admin
def admin_update_user(user, user_id):
    data = request.get_json(force=True)
    role = data.get("role")
    if role not in ("owner", "admin", "member"):
        return json_error("Role must be owner, admin, or member")
    if user_id == user["id"] and role != user["role"]:
        return json_error("You cannot change your own role", 409)
    db = get_db()
    target = db.execute("SELECT id FROM users WHERE company_id = ? AND id = ?", (user["company_id"], user_id)).fetchone()
    if not target:
        return json_error("User not found", 404)
    db.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
    db.commit()
    return jsonify({"user": {"id": user_id, "role": role}})


@api.get("/admin/invites")
@require_user
@require_admin
def admin_invites(user):
    rows = get_db().execute(
        """
        SELECT id, email, role, token, accepted_at, expires_at, created_at
        FROM invites
        WHERE company_id = ?
        ORDER BY created_at DESC
        """,
        (user["company_id"],),
    ).fetchall()
    return jsonify({"invites": [row_to_dict(row) for row in rows]})


@api.get("/admin/integrations")
@require_user
@require_admin
def admin_integrations(user):
    rows = get_db().execute(
        "SELECT id, provider, status, updated_at, created_at FROM integrations WHERE company_id = ? ORDER BY provider",
        (user["company_id"],),
    ).fetchall()
    return jsonify({"integrations": [row_to_dict(row) for row in rows]})


@api.post("/admin/reindex")
@require_user
@require_admin
def admin_reindex(user):
    backfill_document_chunks(user["company_id"])
    count = get_db().execute("SELECT COUNT(*) FROM document_chunks WHERE company_id = ?", (user["company_id"],)).fetchone()[0]
    return jsonify({"chunks": count})


@api.post("/imports/projects/csv")
@require_user
def import_projects_csv_route(user):
    uploaded = request.files.get("file")
    if not uploaded:
        return json_error("CSV file is required")
    content = uploaded.read().decode("utf-8-sig", errors="ignore")
    from .tools import create_pending_action

    action = create_pending_action(
        user["company_id"],
        user["id"],
        "import_project_csv",
        {"filename": uploaded.filename, "csv": content},
    )
    return jsonify({"pending_action": action}), 202
