from app import create_app
from app.db import get_db, seed_demo_data
from app.retrieval import backfill_document_chunks


app = create_app()

with app.app_context():
    db = get_db()
    seed_demo_data(db)
    db.commit()
    backfill_document_chunks()
    print("Demo data is ready.")
