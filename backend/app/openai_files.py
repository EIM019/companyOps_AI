import os
import tempfile

from flask import current_app

from .db import get_db


def ensure_company_vector_store(company_id):
    api_key = current_app.config.get("OPENAI_API_KEY")
    if not api_key:
        return None

    db = get_db()
    company = db.execute("SELECT * FROM companies WHERE id = ?", (company_id,)).fetchone()
    if company["vector_store_id"]:
        return company["vector_store_id"]

    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        store = client.vector_stores.create(name=f"company-{company_id}-knowledge")
        db.execute("UPDATE companies SET vector_store_id = ? WHERE id = ?", (store.id, company_id))
        db.commit()
        return store.id
    except Exception:
        return None


def upload_to_vector_store(company_id, file_storage):
    vector_store_id = ensure_company_vector_store(company_id)
    if not vector_store_id:
        return None

    suffix = os.path.splitext(file_storage.filename or "upload.txt")[1]
    try:
        file_storage.stream.seek(0)
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp:
            temp.write(file_storage.read())
            temp_path = temp.name

        from openai import OpenAI

        client = OpenAI(api_key=current_app.config["OPENAI_API_KEY"])
        with open(temp_path, "rb") as handle:
            uploaded_file = client.files.create(file=handle, purpose="assistants")
        client.vector_stores.files.create(vector_store_id=vector_store_id, file_id=uploaded_file.id)
        return uploaded_file.id
    except Exception:
        return None
    finally:
        try:
            os.unlink(temp_path)
        except Exception:
            pass

