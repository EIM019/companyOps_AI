# CompanyOps AI

A multi-company internal AI assistant MVP for company knowledge search, project/task operations, and confirmed workflow actions.

## Stack

- Frontend: React + Vite
- Backend: Flask
- Database: SQLite
- AI: OpenAI Responses API, with a deterministic local fallback for development

## Quick Start

### Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python run.py
```

The API runs at `http://127.0.0.1:5000`.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

The app runs at `http://127.0.0.1:5173`.

## Demo Access

On first backend boot, a demo company and owner are created:

- Company: `Acme Operations`
- Owner email: `owner@acme.test`
- Owner token: `demo-owner-token`

The frontend stores this token automatically for the demo experience.

## Environment

Set `OPENAI_API_KEY` in `backend/.env` to enable live OpenAI Responses API calls and embedding-backed document retrieval. Without it, chat uses a local deterministic assistant and keyword retrieval so the demo still works.

```env
OPENAI_API_KEY=your_openai_key
OPENAI_MODEL=gpt-5.4
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
```

For real Google Calendar OAuth, create OAuth credentials in Google Cloud and set:

```env
GOOGLE_CLIENT_ID=your_google_client_id
GOOGLE_CLIENT_SECRET=your_google_client_secret
GOOGLE_REDIRECT_URI=http://127.0.0.1:5000/api/integrations/google-calendar/callback
```

Add that redirect URI to the Google OAuth client. If these values are blank, the app uses a demo integration connection for presentation purposes.

## Demo Login

- Email: `owner@acme.test`
- Password: `demo12345`

Run `python seed_demo.py` from `backend` any time you want to restore demo projects, tasks, documents, and the knowledge index without deleting existing chats.
