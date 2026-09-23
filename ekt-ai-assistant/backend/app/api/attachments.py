from fastapi import APIRouter, File, HTTPException, UploadFile

from app.services.attachment_service import (
    AttachmentError,
    extract_attachment,
)

from app.services.specification_parser import (
    SpecificationParserError,
    parse_specification_text,
)


router = APIRouter(
    prefix="/api/attachments",
    tags=["attachments"],
)


@router.post("/extract")
async def extract_file(
    file: UploadFile = File(...),
):
    try:
        data = await file.read()

        result = extract_attachment(
            filename=file.filename or "unknown",
            data=data,
        )

        return {
            "success": True,
            "data": result,
        }

    except AttachmentError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )

    except Exception:
        raise HTTPException(
            status_code=500,
            detail="Ошибка обработки файла.",
        )

@router.post("/parse-specification")
async def parse_specification(
    file: UploadFile = File(...),
):
    """
    1. Получаем PDF/DOCX/XLSX.
    2. Извлекаем текст.
    3. Передаём текст OpenAI.
    4. Возвращаем структурированные позиции.
    """

    try:
        data = await file.read()

        attachment = extract_attachment(
            filename=file.filename or "unknown",
            data=data,
        )

        parsed = await parse_specification_text(
            attachment["text"]
        )

        return {
            "success": True,

            "file": {
                "filename": attachment["filename"],
                "extension": attachment["extension"],
                "characters": attachment["characters"],
                "truncated": attachment["truncated"],
            },

            "specification": parsed.model_dump(),
        }

    except AttachmentError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )

    except SpecificationParserError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        )

    except Exception:
        raise HTTPException(
            status_code=500,
            detail="Внутренняя ошибка обработки спецификации.",
        )