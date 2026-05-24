import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from werkzeug.security import generate_password_hash

from flask import current_app, g


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(_error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def row_to_dict(row):
    return dict(row) if row else None


SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    vector_store_id TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    email TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('owner', 'admin', 'member')),
    password_hash TEXT,
    auth_token TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS invites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    email TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin', 'member')),
    token TEXT NOT NULL UNIQUE,
    accepted_at TEXT,
    expires_at TEXT NOT NULL,
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_threads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id INTEGER NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'tool')),
    content TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    uploaded_by INTEGER NOT NULL REFERENCES users(id),
    filename TEXT NOT NULL,
    content_type TEXT NOT NULL,
    extracted_text TEXT NOT NULL,
    openai_file_id TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS document_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    embedding TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(document_id, chunk_index)
);

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'manual',
    external_id TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'todo',
    assignee_email TEXT,
    due_date TEXT,
    source TEXT NOT NULL DEFAULT 'manual',
    external_id TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS integrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    provider TEXT NOT NULL,
    status TEXT NOT NULL,
    config TEXT NOT NULL DEFAULT '{}',
    token_ref TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(company_id, provider)
);

CREATE TABLE IF NOT EXISTS oauth_states (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    state TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pending_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    action_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending', 'confirmed', 'rejected')) DEFAULT 'pending',
    result TEXT,
    created_at TEXT NOT NULL,
    decided_at TEXT
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    event TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
"""


def init_db():
    db = get_db()
    db.executescript(SCHEMA)
    migrate_db(db)
    seed_demo_data(db)
    db.commit()


def table_columns(db, table):
    return [row["name"] for row in db.execute(f"PRAGMA table_info({table})").fetchall()]


def migrate_db(db):
    user_columns = table_columns(db, "users")
    if "password_hash" not in user_columns:
        db.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")


def seed_demo_data(db):
    now = utcnow()
    company = db.execute("SELECT id FROM companies WHERE name = ?", ("Acme Operations",)).fetchone()
    if company:
        company_id = company["id"]
    else:
        db.execute("INSERT INTO companies (name, created_at) VALUES (?, ?)", ("Acme Operations", now))
        company_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

    user = db.execute("SELECT id, password_hash FROM users WHERE auth_token = ?", ("demo-owner-token",)).fetchone()
    if user:
        owner_id = user["id"]
        if not user["password_hash"]:
            db.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (generate_password_hash("demo12345"), owner_id),
            )
    else:
        db.execute(
            """
            INSERT INTO users (company_id, email, name, role, password_hash, auth_token, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                company_id,
                "owner@acme.test",
                "Demo Owner",
                "owner",
                generate_password_hash("demo12345"),
                "demo-owner-token",
                now,
            ),
        )
        owner_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

    demo_documents = [
        (
            "company-handbook.csv",
            "text/csv",
            """topic,policy,details
leave,Annual leave,Employees receive 18 paid leave days per year. Leave requests should be submitted at least 7 days before the start date.
remote work,Remote work,Employees may work remotely up to 3 days per week with manager approval. Core collaboration hours are 09:00 to 15:00.
travel,Travel policy,Domestic travel requires department manager approval. International travel requires finance director approval and receipts within 5 business days.
expenses,Expense claims,Approved expenses must be submitted by Friday 16:00 with receipts attached. Reimbursements are processed every Wednesday.
security,Security policy,All staff must use multi-factor authentication and report lost devices to IT within 1 hour.""",
        ),
        (
            "customer-support-playbook.csv",
            "text/csv",
            """scenario,response_sla,owner,notes
critical outage,30 minutes,Operations Manager,Open incident bridge and notify leadership.
billing complaint,4 business hours,Finance Team,Check invoice history before responding.
feature request,2 business days,Product Team,Tag request with product area and expected business impact.
vip escalation,1 business hour,Customer Success Lead,Create follow-up task and schedule review call.""",
        ),
        (
            "q2-operations-brief.csv",
            "text/csv",
            """metric,value,comment
onboarding completion,82%,Target is 90% by end of quarter.
open support tickets,37,12 tickets are older than 5 days.
warehouse stock accuracy,96%,Inventory audit is due next Friday.
monthly recurring revenue,125000,Sales expects a 7% increase after renewals.
vendor risk,medium,Two contracts need renewal before June 15.""",
        ),
    ]
    for filename, content_type, extracted_text in demo_documents:
        exists = db.execute(
            "SELECT id FROM documents WHERE company_id = ? AND filename = ?", (company_id, filename)
        ).fetchone()
        if not exists:
            db.execute(
                """
                INSERT INTO documents (company_id, uploaded_by, filename, content_type, extracted_text, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (company_id, owner_id, filename, content_type, extracted_text, now),
            )

    project_specs = [
        (
            "Operations Launch",
            "Prepare the internal assistant rollout across operations, finance, and customer support.",
            [
                ("Upload company handbook", "Add policies for knowledge search.", "todo", "ops.lead@acme.test", "2026-05-28"),
                ("Train department champions", "Run enablement sessions for first-line managers.", "in_progress", "people@acme.test", "2026-06-03"),
                ("Publish assistant usage guide", "Document examples for tasks, calendar booking, and knowledge search.", "todo", "ops.lead@acme.test", "2026-06-07"),
            ],
        ),
        (
            "Finance Automation",
            "Reduce manual expense and invoice follow-up work.",
            [
                ("Review expense claim backlog", "Check all claims older than 10 days.", "todo", "finance@acme.test", "2026-05-29"),
                ("Prepare vendor renewal list", "Identify contracts expiring before June 15.", "in_progress", "procurement@acme.test", "2026-05-31"),
                ("Create invoice escalation workflow", "Define routing for unpaid invoices above 30 days.", "todo", "finance@acme.test", "2026-06-05"),
            ],
        ),
        (
            "Customer Support Upgrade",
            "Improve response time and VIP escalation handling.",
            [
                ("Audit tickets older than 5 days", "Prioritize the 12 stale tickets from the operations brief.", "todo", "support@acme.test", "2026-05-27"),
                ("Draft VIP escalation checklist", "Use the support playbook to define owner and SLA.", "todo", "success@acme.test", "2026-05-30"),
                ("Schedule support performance review", "Review critical outage and billing complaint SLAs.", "todo", "support@acme.test", "2026-06-04"),
            ],
        ),
    ]

    for project_name, description, tasks in project_specs:
        project = db.execute(
            "SELECT id FROM projects WHERE company_id = ? AND name = ?", (company_id, project_name)
        ).fetchone()
        if project:
            project_id = project["id"]
        else:
            db.execute(
                "INSERT INTO projects (company_id, name, description, source, created_at) VALUES (?, ?, ?, ?, ?)",
                (company_id, project_name, description, "manual", now),
            )
            project_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

        for title, task_description, status, assignee_email, due_date in tasks:
            exists = db.execute(
                "SELECT id FROM tasks WHERE company_id = ? AND project_id = ? AND title = ?",
                (company_id, project_id, title),
            ).fetchone()
            if not exists:
                db.execute(
                    """
                    INSERT INTO tasks
                        (company_id, project_id, title, description, status, assignee_email, due_date, source, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        company_id,
                        project_id,
                        title,
                        task_description,
                        status,
                        assignee_email,
                        due_date,
                        "manual",
                        now,
                    ),
                )


def create_invite(company_id, email, role, created_by):
    db = get_db()
    token = secrets.token_urlsafe(24)
    expires_at = (datetime.now(timezone.utc) + timedelta(days=14)).isoformat(timespec="seconds")
    db.execute(
        """
        INSERT INTO invites (company_id, email, role, token, expires_at, created_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (company_id, email.lower(), role, token, expires_at, created_by, utcnow()),
    )
    db.commit()
    return token
