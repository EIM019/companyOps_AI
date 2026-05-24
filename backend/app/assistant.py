import json

from flask import current_app

from .db import get_db, row_to_dict, utcnow
from .tools import (
    create_pending_action,
    get_project,
    list_projects,
    propose_calendar_booking,
    search_company_knowledge,
)


TOOL_DEFINITIONS = [
    {
        "type": "function",
        "name": "search_company_knowledge",
        "description": "Search uploaded company PDF, DOCX, and CSV knowledge.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "list_projects",
        "description": "List projects in the current company workspace.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "type": "function",
        "name": "get_project",
        "description": "Get one project and its tasks.",
        "parameters": {
            "type": "object",
            "properties": {"project_id": {"type": "integer"}},
            "required": ["project_id"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "create_task",
        "description": "Prepare a task creation action that must be confirmed by the user.",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": ["integer", "null"]},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "status": {"type": "string"},
                "assignee_email": {"type": ["string", "null"]},
                "due_date": {"type": ["string", "null"]},
            },
            "required": ["title"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "propose_calendar_booking",
        "description": "Draft meeting details for review.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "start": {"type": "string"},
                "end": {"type": "string"},
                "attendees": {"type": "array", "items": {"type": "string"}},
                "description": {"type": "string"},
            },
            "required": ["title", "start", "end"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "create_calendar_event",
        "description": "Prepare a Google Calendar event creation action that must be confirmed.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "start": {"type": "string"},
                "end": {"type": "string"},
                "attendees": {"type": "array", "items": {"type": "string"}},
                "description": {"type": "string"},
            },
            "required": ["title", "start", "end"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "import_project_csv",
        "description": "Prepare a CSV project import action that must be confirmed.",
        "parameters": {
            "type": "object",
            "properties": {"csv": {"type": "string"}, "filename": {"type": "string"}},
            "required": ["csv"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "sync_trello",
        "description": "Prepare a Trello sync action that must be confirmed.",
        "parameters": {
            "type": "object",
            "properties": {
                "board_id": {"type": ["string", "null"]},
                "board_name": {"type": "string"},
                "cards": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": ["string", "null"]},
                            "name": {"type": "string"},
                            "description": {"type": "string"},
                            "status": {"type": "string"},
                        },
                        "required": ["name"],
                        "additionalProperties": True,
                    },
                },
            },
            "required": ["board_name"],
            "additionalProperties": False,
        },
    },
]


def run_tool_call(company_id, user_id, name, arguments):
    if name == "search_company_knowledge":
        return search_company_knowledge(company_id, arguments.get("query", ""))
    if name == "list_projects":
        return list_projects(company_id)
    if name == "get_project":
        return get_project(company_id, arguments.get("project_id"))
    if name == "propose_calendar_booking":
        return propose_calendar_booking(arguments)
    if name in {"create_task", "create_calendar_event", "import_project_csv", "sync_trello"}:
        return create_pending_action(company_id, user_id, name, arguments)
    return {"error": f"Unknown tool: {name}"}


def local_assistant_response(company_id, user_id, message):
    lowered = message.lower()
    pending = None

    if any(phrase in lowered for phrase in ("create task", "add task", "new task")):
        title = message.split(":", 1)[-1].strip() if ":" in message else "New assistant task"
        pending = create_pending_action(
            company_id,
            user_id,
            "create_task",
            {"title": title, "description": "Created from assistant chat", "status": "todo"},
        )
        return {
            "content": "I prepared a task for confirmation. Review the action card before I add it to the workspace.",
            "pending_actions": [pending],
        }

    if any(phrase in lowered for phrase in ("schedule", "book meeting", "book a meeting", "booking", "calendar", "calender", "meeting")):
        pending = create_pending_action(
            company_id,
            user_id,
            "create_calendar_event",
            {
                "title": "Team meeting",
                "start": "2026-05-25T09:00:00+02:00",
                "end": "2026-05-25T09:30:00+02:00",
                "attendees": [],
                "description": message,
            },
        )
        return {
            "content": "I drafted a Google Calendar event. Confirm it when the details look right.",
            "pending_actions": [pending],
        }

    if "project" in lowered:
        projects = list_projects(company_id)
        names = ", ".join(project["name"] for project in projects) or "no projects yet"
        return {"content": f"Current projects: {names}.", "pending_actions": []}

    if any(word in lowered for word in ("policy", "document", "knowledge", "search")):
        query = message.replace("search", "").strip() or message
        results = search_company_knowledge(company_id, query)
        if not results:
            return {"content": "I could not find matching company knowledge yet. Upload PDF, DOCX, or CSV files to teach me more.", "pending_actions": []}
        snippets = "\n".join(f"- {r['filename']}: {r['snippet'][:180]}" for r in results)
        return {"content": f"I found these matches:\n{snippets}", "pending_actions": []}

    return {
        "content": "I can search company knowledge, list projects, prepare tasks, and draft calendar bookings. Write actions will always ask for confirmation first.",
        "pending_actions": [],
    }


def demo_mode_response(company_id, user_id, message):
    fallback = local_assistant_response(company_id, user_id, message)
    fallback["content"] = (
        "Demo AI mode is active. "
        f"{fallback['content']}"
    )
    return fallback


def openai_assistant_response(company_id, user_id, messages):
    api_key = current_app.config.get("OPENAI_API_KEY")
    if not api_key:
        return local_assistant_response(company_id, user_id, messages[-1]["content"])

    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        response = client.responses.create(
            model=current_app.config.get("OPENAI_MODEL", "gpt-5.4"),
            tools=TOOL_DEFINITIONS,
            input=[
                {
                    "role": "system",
                    "content": (
                        "You are an internal company operations assistant. "
                        "Explain when a write action requires confirmation. "
                        "Do not claim an action was completed unless the backend confirms it."
                    ),
                },
                *messages,
            ],
        )
        pending_actions = []
        tool_summaries = []
        followup_input = []
        for item in getattr(response, "output", []) or []:
            if getattr(item, "type", None) != "function_call":
                continue
            args = json.loads(getattr(item, "arguments", "{}") or "{}")
            result = run_tool_call(company_id, user_id, getattr(item, "name", ""), args)
            if isinstance(result, dict) and result.get("status") == "pending":
                pending_actions.append(result)
            tool_summaries.append({"tool": getattr(item, "name", ""), "result": result})
            followup_input.append(
                {
                    "type": "function_call_output",
                    "call_id": getattr(item, "call_id", ""),
                    "output": json.dumps(result),
                }
            )

        content = response.output_text
        if followup_input:
            try:
                followup = client.responses.create(
                    model=current_app.config.get("OPENAI_MODEL", "gpt-5.4"),
                    previous_response_id=response.id,
                    input=followup_input,
                )
                content = followup.output_text or content
            except Exception:
                pass
        if pending_actions:
            content = f"{content}\n\nI prepared {len(pending_actions)} action for confirmation.".strip()
        elif tool_summaries and not content:
            content = json.dumps(tool_summaries, indent=2)
        return {"content": content, "pending_actions": pending_actions}
    except Exception as exc:
        current_app.logger.warning("OpenAI response failed; using demo mode fallback: %s", exc)
        return demo_mode_response(company_id, user_id, messages[-1]["content"])


def save_message(thread_id, company_id, user_id, role, content, metadata=None):
    db = get_db()
    db.execute(
        """
        INSERT INTO chat_messages (thread_id, company_id, user_id, role, content, metadata, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (thread_id, company_id, user_id, role, content, json.dumps(metadata or {}), utcnow()),
    )
    db.commit()


def thread_messages(thread_id, company_id):
    rows = get_db().execute(
        """
        SELECT role, content
        FROM chat_messages
        WHERE thread_id = ? AND company_id = ?
        ORDER BY created_at ASC, id ASC
        """,
        (thread_id, company_id),
    ).fetchall()
    return [row_to_dict(row) for row in rows]
