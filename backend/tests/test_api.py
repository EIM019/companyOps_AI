import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from app import create_app
from app.db import get_db, utcnow


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tempdir.name, "test.sqlite3")
        self.app = create_app(
            {
                "TESTING": True,
                "DATABASE": self.db_path,
                "OPENAI_API_KEY": "",
            }
        )
        self.client = self.app.test_client()
        self.headers = {"Authorization": "Bearer demo-owner-token"}

    def tearDown(self):
        self.tempdir.cleanup()

    def test_admin_can_invite_and_user_accepts(self):
        response = self.client.post(
            "/api/auth/invites",
            json={"email": "member@example.com", "role": "member"},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        token = response.get_json()["token"]

        accepted = self.client.post(
            "/api/auth/accept-invite",
            json={"token": token, "name": "Member User", "password": "member123"},
        )
        self.assertEqual(accepted.status_code, 200)
        self.assertIn("auth_token", accepted.get_json())

    def test_non_invited_user_cannot_join(self):
        response = self.client.post(
            "/api/auth/accept-invite",
            json={"token": "missing", "name": "No Invite", "password": "member123"},
        )
        self.assertEqual(response.status_code, 404)

    def test_owner_can_login_and_view_admin_summary(self):
        login = self.client.post(
            "/api/auth/login",
            json={"email": "owner@acme.test", "password": "demo12345"},
        )
        self.assertEqual(login.status_code, 200)
        token = login.get_json()["auth_token"]
        summary = self.client.get("/api/admin/summary", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(summary.status_code, 200)
        self.assertIn("users", summary.get_json()["counts"])

    def test_tenant_isolation_for_projects(self):
        with self.app.app_context():
            db = get_db()
            db.execute("INSERT INTO companies (name, created_at) VALUES (?, ?)", ("Other Co", utcnow()))
            company_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.execute(
                "INSERT INTO users (company_id, email, name, role, auth_token, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (company_id, "other@example.com", "Other", "owner", "other-token", utcnow()),
            )
            db.execute(
                "INSERT INTO projects (company_id, name, description, source, created_at) VALUES (?, ?, ?, ?, ?)",
                (company_id, "Other Secret Project", "", "manual", utcnow()),
            )
            db.commit()

        response = self.client.get("/api/projects", headers=self.headers)
        names = [project["name"] for project in response.get_json()["projects"]]
        self.assertNotIn("Other Secret Project", names)

    def test_chat_persists_and_returns_pending_task_action(self):
        thread = self.client.post(
            "/api/chat/threads",
            json={"title": "Test chat"},
            headers=self.headers,
        ).get_json()["thread"]

        response = self.client.post(
            f"/api/chat/threads/{thread['id']}/messages",
            json={"content": "create task: Call the supplier"},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        text = response.get_data(as_text=True)
        self.assertIn("pending_actions", text)

        messages = self.client.get(
            f"/api/chat/threads/{thread['id']}/messages",
            headers=self.headers,
        ).get_json()["messages"]
        self.assertEqual(len(messages), 2)

    def test_confirmed_task_action_mutates_and_rejected_action_does_not(self):
        thread = self.client.post(
            "/api/chat/threads",
            json={"title": "Actions"},
            headers=self.headers,
        ).get_json()["thread"]
        response = self.client.post(
            f"/api/chat/threads/{thread['id']}/messages",
            json={"content": "create task: Prepare budget"},
            headers=self.headers,
        )
        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.get_data(as_text=True).splitlines()
            if line.startswith("data: ")
        ]
        action_id = events[-1]["pending_actions"][0]["id"]

        confirm = self.client.post(
            f"/api/actions/{action_id}/confirm",
            json={"decision": "confirm"},
            headers=self.headers,
        )
        self.assertEqual(confirm.status_code, 200)
        tasks = self.client.get("/api/tasks", headers=self.headers).get_json()["tasks"]
        self.assertTrue(any(task["title"] == "Prepare budget" for task in tasks))

        response = self.client.post(
            f"/api/chat/threads/{thread['id']}/messages",
            json={"content": "create task: Do not create this"},
            headers=self.headers,
        )
        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.get_data(as_text=True).splitlines()
            if line.startswith("data: ")
        ]
        rejected_id = events[-1]["pending_actions"][0]["id"]
        reject = self.client.post(
            f"/api/actions/{rejected_id}/confirm",
            json={"decision": "reject"},
            headers=self.headers,
        )
        self.assertEqual(reject.status_code, 200)
        tasks = self.client.get("/api/tasks", headers=self.headers).get_json()["tasks"]
        self.assertFalse(any(task["title"] == "Do not create this" for task in tasks))

    def test_demo_mode_calendar_booking_language_creates_pending_action(self):
        thread = self.client.post(
            "/api/chat/threads",
            json={"title": "Calendar"},
            headers=self.headers,
        ).get_json()["thread"]

        response = self.client.post(
            f"/api/chat/threads/{thread['id']}/messages",
            json={"content": "make calender booking for Tuesday 1500hrs, with the Dev team"},
            headers=self.headers,
        )
        text = response.get_data(as_text=True)
        self.assertIn("create_calendar_event", text)
        self.assertIn("drafted a Google Calendar event", text)

    def test_csv_upload_indexes_for_company_search(self):
        response = self.client.post(
            "/api/documents/upload",
            data={"file": (io.BytesIO(b"policy,details\ntravel,Use approved hotels"), "policy.csv")},
            content_type="multipart/form-data",
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 201)

        thread = self.client.post(
            "/api/chat/threads",
            json={"title": "Knowledge"},
            headers=self.headers,
        ).get_json()["thread"]
        chat = self.client.post(
            f"/api/chat/threads/{thread['id']}/messages",
            json={"content": "search travel policy"},
            headers=self.headers,
        )
        self.assertIn("policy.csv", chat.get_data(as_text=True))

    def test_openai_failure_uses_polished_demo_mode_message(self):
        app = create_app(
            {
                "TESTING": True,
                "DATABASE": os.path.join(self.tempdir.name, "openai-failure.sqlite3"),
                "OPENAI_API_KEY": "test-key",
            }
        )
        client = app.test_client()
        headers = {"Authorization": "Bearer demo-owner-token"}
        thread = client.post(
            "/api/chat/threads",
            json={"title": "OpenAI failure"},
            headers=headers,
        ).get_json()["thread"]

        with patch("openai.OpenAI") as mocked_openai:
            mocked_openai.side_effect = Exception("insufficient_quota")
            response = client.post(
                f"/api/chat/threads/{thread['id']}/messages",
                json={"content": "hello"},
                headers=headers,
            )

        text = response.get_data(as_text=True)
        self.assertIn("Demo AI mode is active", text)
        self.assertNotIn("insufficient_quota", text)


if __name__ == "__main__":
    unittest.main()
