from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.services.ekt_client import (
    EKTAPIError,
    ekt_client,
)


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="AI-ассистент для интернет-магазина ekt.kz",
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "status": "running",
    }


@app.get("/api/health")
async def health():
    return {
        "status": "healthy",

        "ekt": {
            "configured": bool(
                settings.ekt_api_user
                and settings.ekt_api_password
            ),
            "base_url": settings.ekt_base_url,
        },

        "openai": {
            "configured": bool(settings.openai_api_key),
        },

        "nvidia": {
            "configured": bool(settings.nvidia_api_key),
        },
    }


@app.get("/api/ekt/products")
async def products(page: int = 1):
    try:
        return await ekt_client.get_products(page)

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )

    except EKTAPIError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        )


@app.get("/api/ekt/products/{product_id}")
async def product_detail(product_id: int):
    try:
        return await ekt_client.get_product_detail(product_id)

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )

    except EKTAPIError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        )

    from fastapi import FastAPI

from app.api.attachments import router as attachments_router
from app.core.config import settings


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
)

app.include_router(attachments_router)


@app.get("/")
async def root():
    return {
        "status": "ok",
        "message": "EKT AI Assistant работает",
    }