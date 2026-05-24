import csv
import io
import json
import urllib.error
import urllib.request

from flask import current_app

from .db import get_db, row_to_dict, utcnow
from .retrieval import search_chunks


WRITE_ACTIONS = {
    "create_task",
    "create_calendar_event",
    "import_project_csv",
    "sync_trello",
}


def create_pending_action(company_id, user_id, action_type, payload):
    db = get_db()
    db.execute(
        """
        INSERT INTO pending_actions (company_id, user_id, action_type, payload, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (company_id, user_id, action_type, json.dumps(payload), utcnow()),
    )
    db.commit()
    action_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    return {"id": action_id, "type": action_type, "payload": payload, "status": "pending"}


def search_company_knowledge(company_id, query, limit=5):
    chunk_results = search_chunks(company_id, query, limit)
    if chunk_results:
        return chunk_results

    db = get_db()
    terms = [term for term in query.lower().split() if len(term) > 2]
    if not terms:
        terms = [query.lower()]
    where = " OR ".join("lower(extracted_text) LIKE ?" for _ in terms)
    rows = db.execute(
        f"""
        SELECT id, filename, substr(extracted_text, 1, 500) AS snippet
        FROM documents
        WHERE company_id = ? AND ({where})
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (company_id, *[f"%{term}%" for term in terms], limit),
    ).fetchall()
    return [row_to_dict(row) for row in rows]


def list_projects(company_id):
    db = get_db()
    rows = db.execute(
        "SELECT * FROM projects WHERE company_id = ? ORDER BY created_at DESC", (company_id,)
    ).fetchall()
    return [row_to_dict(row) for row in rows]


def get_project(company_id, project_id):
    db = get_db()
    project = db.execute(
        "SELECT * FROM projects WHERE company_id = ? AND id = ?", (company_id, project_id)
    ).fetchone()
    if not project:
        return None
    tasks = db.execute(
        "SELECT * FROM tasks WHERE company_id = ? AND project_id = ? ORDER BY created_at DESC",
        (company_id, project_id),
    ).fetchall()
    data = row_to_dict(project)
    data["tasks"] = [row_to_dict(row) for row in tasks]
    return data


def create_task(company_id, payload):
    db = get_db()
    project_id = payload.get("project_id")
    if project_id:
        exists = db.execute(
            "SELECT id FROM projects WHERE company_id = ? AND id = ?", (company_id, project_id)
        ).fetchone()
        if not exists:
            raise ValueError("Project not found")
    db.execute(
        """
        INSERT INTO tasks
            (company_id, project_id, title, description, status, assignee_email, due_date, source, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            company_id,
            project_id,
            payload["title"],
            payload.get("description", ""),
            payload.get("status", "todo"),
            payload.get("assignee_email"),
            payload.get("due_date"),
            "assistant",
            utcnow(),
        ),
    )
    db.commit()
    task_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    return row_to_dict(db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone())


def propose_calendar_booking(payload):
    return {
        "title": payload.get("title", "Meeting"),
        "start": payload.get("start"),
        "end": payload.get("end"),
        "attendees": payload.get("attendees", []),
        "description": payload.get("description", ""),
    }


def create_calendar_event(company_id, user_id, payload):
    db = get_db()
    integration = db.execute(
        "SELECT * FROM integrations WHERE company_id = ? AND provider = ? AND status = ?",
        (company_id, "google-calendar", "connected"),
    ).fetchone()
    if integration:
        config = json.loads(integration["config"])
        access_token = config.get("access_token")
        if access_token:
            event_body = {
                "summary": payload.get("title", "Meeting"),
                "description": payload.get("description", ""),
                "start": {"dateTime": payload.get("start")},
                "end": {"dateTime": payload.get("end")},
                "attendees": [{"email": email} for email in payload.get("attendees", [])],
            }
            request = urllib.request.Request(
                "https://www.googleapis.com/calendar/v3/calendars/primary/events",
                data=json.dumps(event_body).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=15) as response:
                    created = json.loads(response.read().decode("utf-8"))
                db.execute(
                    "INSERT INTO audit_logs (company_id, user_id, event, payload, created_at) VALUES (?, ?, ?, ?, ?)",
                    (company_id, user_id, "google_calendar_event_created", json.dumps(created), utcnow()),
                )
                db.commit()
                return created
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
                current_app.logger.warning("Google Calendar event creation failed: %s", exc)

    event = {
        "provider": "google-calendar",
        "external_event_id": f"demo-event-{company_id}-{user_id}-{utcnow()}",
        **propose_calendar_booking(payload),
    }
    db.execute(
        "INSERT INTO audit_logs (company_id, user_id, event, payload, created_at) VALUES (?, ?, ?, ?, ?)",
        (company_id, user_id, "calendar_event_created", json.dumps(event), utcnow()),
    )
    db.commit()
    return event


def import_project_csv(company_id, payload):
    db = get_db()
    content = payload.get("csv", "")
    reader = csv.DictReader(io.StringIO(content))
    created = {"projects": 0, "tasks": 0}
    project_cache = {}

    for row in reader:
        project_name = (row.get("project") or row.get("project_name") or "Imported Project").strip()
        if project_name not in project_cache:
            existing = db.execute(
                "SELECT id FROM projects WHERE company_id = ? AND name = ?", (company_id, project_name)
            ).fetchone()
            if existing:
                project_cache[project_name] = existing["id"]
            else:
                db.execute(
                    "INSERT INTO projects (company_id, name, description, source, created_at) VALUES (?, ?, ?, ?, ?)",
                    (company_id, project_name, row.get("project_description", ""), "csv", utcnow()),
                )
                project_cache[project_name] = db.execute("SELECT last_insert_rowid()").fetchone()[0]
                created["projects"] += 1
        title = (row.get("task") or row.get("title") or "").strip()
        if title:
            db.execute(
                """
                INSERT INTO tasks (company_id, project_id, title, description, status, assignee_email, due_date, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    company_id,
                    project_cache[project_name],
                    title,
                    row.get("description", ""),
                    row.get("status", "todo"),
                    row.get("assignee_email"),
                    row.get("due_date"),
                    "csv",
                    utcnow(),
                ),
            )
            created["tasks"] += 1
    db.commit()
    return created


def sync_trello(company_id, payload):
    db = get_db()
    board_name = payload.get("board_name", "Trello Board")
    cards = payload.get("cards", [])
    db.execute(
        "INSERT INTO projects (company_id, name, description, source, external_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (company_id, board_name, "Synced from Trello", "trello", payload.get("board_id"), utcnow()),
    )
    project_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    for card in cards:
        db.execute(
            """
            INSERT INTO tasks (company_id, project_id, title, description, status, source, external_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                company_id,
                project_id,
                card.get("name", "Untitled Trello card"),
                card.get("description", ""),
                card.get("status", "todo"),
                "trello",
                card.get("id"),
                utcnow(),
            ),
        )
    db.commit()
    return {"project_id": project_id, "tasks": len(cards)}


def execute_confirmed_action(action, user_id):
    payload = json.loads(action["payload"])
    action_type = action["action_type"]
    company_id = action["company_id"]

    if action_type == "create_task":
        return create_task(company_id, payload)
    if action_type == "create_calendar_event":
        return create_calendar_event(company_id, user_id, payload)
    if action_type == "import_project_csv":
        return import_project_csv(company_id, payload)
    if action_type == "sync_trello":
        return sync_trello(company_id, payload)
    raise ValueError(f"Unsupported action: {action_type}")
