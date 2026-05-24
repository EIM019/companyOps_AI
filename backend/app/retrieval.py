import json
import math
import re

from flask import current_app

from .db import get_db, row_to_dict, utcnow


def chunk_text(text, max_chars=900):
    clean = re.sub(r"\s+", " ", text).strip()
    if not clean:
        return []
    chunks = []
    start = 0
    while start < len(clean):
        end = min(start + max_chars, len(clean))
        chunks.append(clean[start:end])
        start = end
    return chunks


def embed_texts(texts):
    api_key = current_app.config.get("OPENAI_API_KEY")
    if not api_key or not texts:
        return [None for _ in texts]
    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        response = client.embeddings.create(
            model=current_app.config.get("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"),
            input=texts,
        )
        return [item.embedding for item in response.data]
    except Exception:
        return [None for _ in texts]


def index_document_chunks(company_id, document_id, text):
    db = get_db()
    chunks = chunk_text(text)
    embeddings = embed_texts(chunks)
    for index, content in enumerate(chunks):
        db.execute(
            """
            INSERT OR REPLACE INTO document_chunks
                (document_id, company_id, chunk_index, content, embedding, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                document_id,
                company_id,
                index,
                content,
                json.dumps(embeddings[index]) if embeddings[index] else None,
                utcnow(),
            ),
        )
    db.commit()


def cosine_similarity(left, right):
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if not left_norm or not right_norm:
        return 0
    return dot / (left_norm * right_norm)


def keyword_score(query, content):
    terms = [term for term in re.findall(r"[a-z0-9]+", query.lower()) if len(term) > 2]
    if not terms:
        return 0
    lower = content.lower()
    return sum(lower.count(term) for term in terms)


def search_chunks(company_id, query, limit=5):
    db = get_db()
    rows = db.execute(
        """
        SELECT document_chunks.*, documents.filename
        FROM document_chunks
        JOIN documents ON documents.id = document_chunks.document_id
        WHERE document_chunks.company_id = ?
        """,
        (company_id,),
    ).fetchall()
    query_embedding = embed_texts([query])[0]
    scored = []
    for row in rows:
        data = row_to_dict(row)
        score = keyword_score(query, data["content"])
        if query_embedding and data["embedding"]:
            score += cosine_similarity(query_embedding, json.loads(data["embedding"])) * 10
        if score > 0:
            scored.append((score, data))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        {
            "document_id": item["document_id"],
            "filename": item["filename"],
            "snippet": item["content"],
            "score": round(score, 3),
        }
        for score, item in scored[:limit]
    ]


def backfill_document_chunks(company_id=None):
    db = get_db()
    if company_id:
        rows = db.execute("SELECT * FROM documents WHERE company_id = ?", (company_id,)).fetchall()
    else:
        rows = db.execute("SELECT * FROM documents").fetchall()
    for row in rows:
        existing = db.execute("SELECT id FROM document_chunks WHERE document_id = ? LIMIT 1", (row["id"],)).fetchone()
        if not existing:
            index_document_chunks(row["company_id"], row["id"], row["extracted_text"])
