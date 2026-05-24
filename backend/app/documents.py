import csv
import io


def extract_text(file_storage):
    filename = file_storage.filename or "upload"
    lower = filename.lower()
    raw = file_storage.read()

    if lower.endswith(".pdf"):
        try:
            from PyPDF2 import PdfReader
        except ImportError as exc:
            raise ValueError("PDF support requires PyPDF2. Run pip install -r requirements.txt") from exc
        reader = PdfReader(io.BytesIO(raw))
        return "\n".join(page.extract_text() or "" for page in reader.pages).strip()

    if lower.endswith(".docx"):
        try:
            from docx import Document as DocxDocument
        except ImportError as exc:
            raise ValueError("DOCX support requires python-docx. Run pip install -r requirements.txt") from exc
        doc = DocxDocument(io.BytesIO(raw))
        return "\n".join(p.text for p in doc.paragraphs).strip()

    if lower.endswith(".csv"):
        text = raw.decode("utf-8-sig", errors="ignore")
        rows = csv.reader(io.StringIO(text))
        return "\n".join(", ".join(cell.strip() for cell in row) for row in rows).strip()

    raise ValueError("Only PDF, DOCX, and CSV uploads are supported")
