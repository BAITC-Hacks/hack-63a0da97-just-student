from fastapi import APIRouter, File, HTTPException, UploadFile

from app.models.catalog import FileMetadata, MatchProductsResponse
from app.services.attachment_service import (
    AttachmentError,
    MAX_FILE_SIZE,
    extract_attachment,
)
from app.services.catalog_service import catalog_service
from app.services.ekt_client import EKTAPIError
from app.services.specification_parser import (
    SpecificationParserError,
    parse_specification_text,
)


router = APIRouter(
    prefix="/api/attachments",
    tags=["attachments"],
)


async def _extract_upload(file: UploadFile) -> dict:
    data = await file.read(MAX_FILE_SIZE + 1)
    return extract_attachment(
        filename=file.filename or "unknown",
        data=data,
    )


def _file_metadata(attachment: dict) -> FileMetadata:
    return FileMetadata(
        filename=attachment["filename"],
        extension=attachment["extension"],
        characters=attachment["characters"],
        truncated=attachment["truncated"],
    )


@router.post("/extract")
async def extract_file(
    file: UploadFile = File(...),
):
    try:
        result = await _extract_upload(file)

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
        attachment = await _extract_upload(file)

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


@router.post(
    "/match-products",
    response_model=MatchProductsResponse,
)
async def match_products(
    file: UploadFile = File(...),
) -> MatchProductsResponse:
    """
    Извлекает спецификацию, разбирает её через OpenAI и
    сопоставляет позиции с реальными карточками каталога EKT.
    """
    try:
        attachment = await _extract_upload(file)
        parsed = await parse_specification_text(attachment["text"])
        matched_items = await catalog_service.match_items(parsed.items)

        return MatchProductsResponse(
            file=_file_metadata(attachment),
            items=matched_items,
        )

    except AttachmentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SpecificationParserError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except EKTAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Внутренняя ошибка сопоставления товаров.",
        ) from exc
