from fastapi import FastAPI, HTTPException, Depends
from contextlib import asynccontextmanager, suppress
import asyncio
import time
from collections import defaultdict, deque
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from pathlib import Path
from app.api.chat import router as chat_router
from app.api.chat import mutation, catalog, sessions, persist
from app.services import session_store
from app.services.attachment_service import AttachmentError
from app.services.specification_parser import SpecificationParserError

from app.api.attachments import router as attachments_router
from app.core.config import settings
from app.services.ekt_client import (
    EKTAPIError,
    ekt_client,
)


@asynccontextmanager
async def lifespan(app):
    async def maintenance():
        while True:
            for key in list(sessions):
                if sessions[key].expires < time.time():
                    sessions.pop(key, None)
            await asyncio.to_thread(session_store.cleanup)
            if settings.catalog_warmup and not settings.demo_mode:
                with suppress(EKTAPIError, OSError):
                    await catalog._load_live_catalog(advance=True)
            await asyncio.sleep(60)
    task = asyncio.create_task(maintenance())
    yield
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="AI-ассистент для интернет-магазина ekt.kz",
    lifespan=lifespan,
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(attachments_router, dependencies=[Depends(mutation)])
app.include_router(chat_router)
STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")
ip_requests = defaultdict(deque)

@app.middleware("http")
async def security_headers(request, call_next):
    if request.url.path.startswith("/api/"):
        try:
            length = int(request.headers.get("content-length", "0") or 0)
        except ValueError:
            return JSONResponse(status_code=400, content={"detail": "Некорректный размер запроса."})
        if length > 11 * 1024 * 1024:
            return JSONResponse(status_code=413, content={"detail": "Максимальный размер запроса 11 MB."})
        now = time.monotonic()
        key = request.client.host if request.client else "unknown"
        for ip in list(ip_requests):
            if not ip_requests[ip] or now - ip_requests[ip][-1] > 60:
                del ip_requests[ip]
        window = ip_requests[key]
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= 120:
            return JSONResponse(status_code=429, content={"detail": "Слишком много запросов с этого адреса. Повторите через минуту."}, headers={"Retry-After": "60"})
        window.append(now)
    response = await call_next(request)
    current = getattr(request.state, "assistant_session", None)
    if current is not None:
        async with current.lock:
            await asyncio.to_thread(persist, current)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' https: data:; connect-src 'self'; frame-ancestors 'self'; base-uri 'self'; form-action 'self'"
    if request.url.path in ("/docs", "/redoc"):
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; img-src 'self' https: data:; connect-src 'self'; frame-ancestors 'self'"
    return response

@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    # Never echo passwords, uploaded content, or sensitive request input.
    return JSONResponse(status_code=422, content={"detail": "Проверьте формат и допустимые значения полей. / Өрістердің пішімі мен мәндерін тексеріңіз."})

@app.exception_handler(EKTAPIError)
@app.exception_handler(SpecificationParserError)
async def integration_error(request, exc):
    return JSONResponse(status_code=502, content={"detail": str(exc)})

@app.exception_handler(AttachmentError)
async def attachment_error(request, exc):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.get("/")
async def root():
    return FileResponse(STATIC / "index.html")

@app.get("/cart")
async def cart_page():
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
async def health():
    return {
        "status": "healthy",
        "mode": "demo" if settings.demo_mode else "live",
        "catalog": catalog.coverage,
        "cart_integration": "local_prototype",

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
        ) from exc
