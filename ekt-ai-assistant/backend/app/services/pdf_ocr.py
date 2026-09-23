"""Retain every text page and OCR image-only pages; reject instead of silently dropping."""
from io import BytesIO
from pypdf import PdfReader
from starlette.concurrency import run_in_threadpool
from app.services.attachment_service import AttachmentError, MAX_FILE_SIZE
from app.services.vision import extract_image

def inspect_pdf(data):
    if not data or len(data) > MAX_FILE_SIZE:
        raise AttachmentError("PDF должен быть непустым и не больше 10 MB.")
    try:
        reader = PdfReader(BytesIO(data))
        if len(reader.pages) > 50:
            raise AttachmentError("Разделите PDF: максимум 50 страниц в одном файле.")
        return [p.extract_text() or "" for p in reader.pages]
    except AttachmentError:
        raise
    except Exception as exc:
        raise AttachmentError("Не удалось открыть PDF.") from exc

def render(data, page):
    import pymupdf
    with pymupdf.open(stream=data, filetype="pdf") as document:
        p = document[page]
        scale = min(2, 1800 / max(p.rect.width, p.rect.height))
        return p.get_pixmap(matrix=pymupdf.Matrix(scale, scale)).tobytes("png")

async def extract_pdf_with_ocr(filename, data):
    pages = await run_in_threadpool(inspect_pdf, data)
    missing = [i for i,t in enumerate(pages) if not t.strip()]
    if len(missing) > 5:
        raise AttachmentError("Разделите скан PDF на файлы по 5 страниц для распознавания.")
    for index in missing:
        picture = await run_in_threadpool(render, data, index)
        result = await extract_image(f"page-{index+1}.png", picture)
        pages[index] = result["text"]
    text = "\n\n".join(f"--- Страница {i+1} ---\n{t}" for i,t in enumerate(pages))
    if not text.strip():
        raise AttachmentError("PDF не содержит читаемых страниц.")
    if len(text) > 50000:
        raise AttachmentError("Слишком много текста. Разделите файл на части до 50 000 символов.")
    return {"filename": filename, "extension": ".pdf", "text": text, "characters": len(text), "truncated": False}
