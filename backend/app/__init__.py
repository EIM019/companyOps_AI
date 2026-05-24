import os

from dotenv import load_dotenv
from flask import Flask
from flask_cors import CORS

from .db import close_db, init_db
from .routes import api


def frontend_origins():
    origins = {
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    }
    configured_origins = os.environ.get("FRONTEND_ORIGINS") or os.environ.get("FRONTEND_ORIGIN", "")
    origins.update(origin.strip().rstrip("/") for origin in configured_origins.split(",") if origin.strip())

    frontend_host = os.environ.get("FRONTEND_HOST", "").strip().rstrip("/")
    if frontend_host:
        origins.add(frontend_host if frontend_host.startswith("http") else f"https://{frontend_host}")

    return sorted(origins)


def create_app(test_config=None):
    load_dotenv()
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        DATABASE=os.environ.get("DATABASE_PATH", os.path.join(app.instance_path, "companyops.sqlite3")),
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-secret"),
        OPENAI_API_KEY=os.environ.get("OPENAI_API_KEY", ""),
        OPENAI_MODEL=os.environ.get("OPENAI_MODEL", "gpt-5.4"),
        OPENAI_EMBEDDING_MODEL=os.environ.get("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"),
        GOOGLE_CLIENT_ID=os.environ.get("GOOGLE_CLIENT_ID", ""),
        GOOGLE_CLIENT_SECRET=os.environ.get("GOOGLE_CLIENT_SECRET", ""),
        GOOGLE_REDIRECT_URI=os.environ.get(
            "GOOGLE_REDIRECT_URI", "http://127.0.0.1:5000/api/integrations/google-calendar/callback"
        ),
        MAX_CONTENT_LENGTH=25 * 1024 * 1024,
    )

    if test_config:
        app.config.update(test_config)

    os.makedirs(app.instance_path, exist_ok=True)
    CORS(app, origins=frontend_origins())

    app.teardown_appcontext(close_db)
    app.register_blueprint(api, url_prefix="/api")

    with app.app_context():
        init_db()

    return app
