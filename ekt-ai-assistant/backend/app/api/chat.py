"""Session-scoped prototype. The model has no cart mutation tool."""
import asyncio
import re
import secrets
import time
from decimal import Decimal
from typing import Literal
from dataclasses import dataclass, field

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field, StrictBool

from app.core.config import settings
from app.models.specification import SpecificationItem
from app.services.catalog_service import CatalogService, _to_catalog_product, _unwrap_product
from app.services.demo_catalog import DemoClient
from app.services.ekt_client import EKTAPIError, ekt_client
from app.services.specification_parser import SpecificationParserError, parse_specification_text
from app.api.attachments import _extract_upload
from app.services.attachment_service import AttachmentError
from app.services import accounts
from app.services.i18n import tr
from app.services.terms import purchase_terms
from app.services.recommendations import reason, related_ids
from app.services.dialogue import interpret

router = APIRouter(prefix="/api", tags=["assistant"])
catalog = CatalogService(DemoClient() if settings.demo_mode else ekt_client)

@dataclass
class Session:
    csrf: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    expires: float = field(default_factory=lambda: time.monotonic() + 3600)
    cart: dict = field(default_factory=dict)
    pending: dict | None = None
    last_query: str = ""
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    requests: list = field(default_factory=list)
    language: str = "ru"
    history: list = field(default_factory=list)
    user_id: str | None = None
    username: str | None = None

sessions: dict[str, Session] = {}

def session(request: Request):
    key = request.cookies.get("ekt_session", "")
    value = sessions.get(key)
    if not value or value.expires < time.monotonic():
        sessions.pop(key, None)
        raise HTTPException(401, "Сессия истекла. Обновите страницу.")
    return value

def mutation(s: Session = Depends(session), x_csrf_token: str = Header(default="")):
    if not secrets.compare_digest(s.csrf, x_csrf_token):
        raise HTTPException(403, "Недействительный токен сессии.")
    now = time.monotonic()
    s.requests = [t for t in s.requests if now - t < 60]
    if len(s.requests) >= 30:
        raise HTTPException(429, "Слишком много запросов. Попробуйте через минуту.")
    s.requests.append(now)
    return s

@router.get("/session")
def start(request: Request, response: Response):
    now = time.monotonic()
    for key in list(sessions):
        if sessions[key].expires < now:
            sessions.pop(key, None)
    key = request.cookies.get("ekt_session", "")
    if key not in sessions:
        if len(sessions) >= 1000:
            raise HTTPException(503, "Сервис занят. Попробуйте позже.")
        key = secrets.token_urlsafe(32)
        sessions[key] = Session()
    response.set_cookie("ekt_session", key, httponly=True, samesite="strict", secure=settings.cookie_secure, max_age=3600)
    current = sessions[key]
    return {"csrf": current.csrf, "demo": settings.demo_mode, "cart_url": "/cart", "manager_url": settings.manager_url, "language": current.language, "username": current.username}

class Message(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    language: Literal["ru", "kk"] = "ru"

class Proposal(BaseModel):
    product_id: int = Field(gt=0)
    quantity: float = Field(gt=0, le=100000, allow_inf_nan=False)

class Confirmation(BaseModel):
    proposal_id: str
    confirmed: StrictBool

async def fresh(product_id):
    product = _to_catalog_product(_unwrap_product(await catalog.client.get_product_detail(product_id)))
    if not product:
        raise HTTPException(404, "Товар не найден.")
    return product

async def alternatives(product, language="ru"):
    if not product.category or not product.characteristics or product.data_warnings:
        return []
    result = []
    inspected = 0
    for raw in await catalog._load_catalog():
        if inspected >= 12:
            break
        candidate = _to_catalog_product(raw)
        if not candidate or candidate.id == product.id or candidate.category != product.category:
            continue
        candidate = await fresh(int(candidate.id))
        inspected += 1
        if candidate.data_warnings or not candidate.quantity or candidate.quantity <= 0:
            continue
        explanation = reason(product, candidate, language)
        if explanation:
            result.append({"product": candidate.model_dump(), "reason": explanation})
        if len(result) >= 3 or inspected >= 12:
            break
    return result

async def search(query, language="ru", use_ai=True):
    # Deterministic article extraction also works without an AI key.
    articles = re.findall(r"(?i)\bDEMO-\d{3}\b|\b\d{6,}\b", query)
    if articles:
        items = [SpecificationItem(raw_text=query, search_query=a.upper(), article=a.upper()) for a in articles[:5]]
    elif settings.openai_api_key and not settings.demo_mode and use_ai:
        items = (await parse_specification_text(query)).items[:5]
    else:
        clean = re.sub(r"(?i)\b(есть|наличие|покажи|найди|нужен|нужны|товар|сертификат|характеристики|аналог|на|складе)\b", " ", query).strip()
        items = [SpecificationItem(raw_text=query, search_query=clean or query)]
    matches = await catalog.match_items(items)
    products, analogs = [], []
    for match in matches:
        selected = [match.match.product] if match.match.product else match.match.candidates
        for p in selected:
            p = await fresh(int(p.id))
            products.append(p.model_dump())
            if p.quantity == 0:
                analogs.extend(await alternatives(p, language))
    related = []
    for p in products[:3]:
        for relation in related_ids(p["id"], settings.demo_mode):
            item = await fresh(int(relation["product_id"]))
            if item.quantity and item.quantity > 0:
                related.append({"product": item.model_dump(), "reason": relation.get("reason_kk") if language == "kk" and relation.get("reason_kk") else relation["reason"]})
    return {"products": products, "alternatives": analogs, "related": related, "coverage": catalog.coverage}

@router.post("/chat")
async def chat(body: Message, s: Session = Depends(mutation)):
    s.language = body.language
    result = await respond(body, s)
    # Payment-like messages never enter history, including persistent history.
    if not re.search(r"(?:\d[ -]?){13,19}", body.message):
        s.history.extend([{"role": "user", "text": body.message}, {"role": "assistant", "text": result.get("answer", "")}])
        s.history = s.history[-100:]
        await asyncio.to_thread(accounts.save_history, s.user_id, s.history)
    return result

async def respond(body: Message, s: Session):
    text = body.message.strip()
    if re.search(r"(?:\d[ -]?){13,19}", text):
        return {"answer": tr("payment", s.language)}
    low = text.casefold()
    if low in ("да, добавь", "да добавь", "иә, қос", "иә қос"):
        if not s.pending:
            return {"answer": tr("choose", s.language)}
        return await confirm(Confirmation(proposal_id=s.pending["id"], confirmed=True), s)
    if any(w in low for w in ("оплат", "достав", "минималь", "условия", "төлем", "жеткізу", "ең аз")):
        return {**purchase_terms(s.language, settings.demo_mode, settings.purchase_terms), "manager_url": settings.manager_url}
    if "менеджер" in low:
        return {"answer": tr("manager", s.language), "manager_url": settings.manager_url, "handoff_url": "/api/handoff"}
    if low in ("привет", "здравствуйте", "сәлем"):
        return {"answer": tr("welcome", s.language)}
    if any(w in low for w in ("а сертификат", "а характеристики", "а наличие", "а аналог")) and s.last_query:
        text = s.last_query
    quantity = None
    if settings.ai_dialogue and settings.openai_api_key and not settings.demo_mode and not re.search(r"\b\d{6,}\b", text) and text != s.last_query:
        try:
            context = [m["text"] for m in s.history][-8:]
            if s.last_query:
                context.append("Последний поисковый запрос: " + s.last_query)
            plan = await interpret(text, context, s.language)
            if plan.intent == "clarify":
                return {"answer": plan.reply}
            if plan.intent == "terms":
                return purchase_terms(s.language, settings.demo_mode, settings.purchase_terms)
            if plan.intent == "manager":
                return {"answer": tr("manager", s.language), "manager_url": settings.manager_url, "handoff_url": "/api/handoff"}
            text, quantity = plan.query, plan.quantity
        except SpecificationParserError:
            # Keep catalog access available during AI outages.
            pass
    s.last_query = text
    result = await search(text, s.language, use_ai=False)
    return {"answer": tr("found" if result["products"] else "missing", s.language), "requested_quantity": quantity, **result}

@router.get("/cart")
def cart(s: Session = Depends(session)):
    return {"items": list(s.cart.values()), "cart_url": "/cart", "prototype": True}

@router.post("/cart/propose")
async def propose(body: Proposal, s: Session = Depends(mutation)):
    async with s.lock:
        p = await fresh(body.product_id)
        existing = s.cart.get(str(p.id), {}).get("quantity", 0)
        validate_quantity(p, body.quantity)
        if p.quantity is None or body.quantity + existing > p.quantity:
            raise HTTPException(409, tr("stock", s.language))
        s.pending = {"id": secrets.token_urlsafe(24), "product_id": body.product_id, "quantity": body.quantity, "price": p.price, "expires": time.monotonic() + 120}
        question = f"«{p.name}», {body.quantity:g} бірлік себетке қосылсын ба?" if s.language == "kk" else f"Добавить «{p.name}», {body.quantity:g} ед.?"
        return {"proposal_id": s.pending["id"], "answer": question, "requires_confirmation": True}

@router.post("/cart/confirm")
async def confirm(body: Confirmation, s: Session = Depends(mutation)):
    async with s.lock:
        pending = s.pending
        if not pending or pending["id"] != body.proposal_id or pending["expires"] < time.monotonic():
            raise HTTPException(409, tr("expired", s.language))
        s.pending = None
        if not body.confirmed:
            return {"answer": tr("cancel", s.language)}
        p = await fresh(pending["product_id"])
        quantity = s.cart.get(str(p.id), {}).get("quantity", 0) + pending["quantity"]
        validate_quantity(p, pending["quantity"])
        if p.quantity is None or quantity > p.quantity or p.price != pending.get("price"):
            raise HTTPException(409, tr("changed", s.language))
        s.cart[str(p.id)] = {"product": p.model_dump(), "quantity": quantity}
        return {"answer": tr("added", s.language), **cart(s)}

@router.delete("/session")
def forget(request: Request, response: Response, s: Session = Depends(mutation)):
    sessions.pop(request.cookies.get("ekt_session", ""), None)
    response.delete_cookie("ekt_session")
    return {"deleted": True}

@router.post("/chat/attachment")
async def attachment(file: UploadFile = File(...), s: Session = Depends(mutation)):
    data = await _extract_upload(file)
    parsed = await parse_specification_text(data["text"])
    if len(parsed.items) > 100:
        raise AttachmentError("Разделите спецификацию: максимум 100 позиций на один запрос.")
    result = []
    for item in parsed.items:
        result.append({"source": item.model_dump(), **await search(item.article or item.search_query, s.language, use_ai=False)})
    return {"answer": tr("parsed", s.language), "items": result, "unresolved": parsed.unresolved, "truncated": data["truncated"]}

def validate_quantity(product, quantity):
    if product.minimum_quantity and quantity < product.minimum_quantity:
        raise HTTPException(409, f"Минимальная партия / Ең аз партия: {product.minimum_quantity:g}.")
    if product.quantity_step and Decimal(str(quantity)) % Decimal(str(product.quantity_step)):
        raise HTTPException(409, f"Кратность / Еселік: {product.quantity_step:g}.")

class Language(BaseModel):
    language: Literal["ru", "kk"]

@router.post("/language")
def language(body: Language, s: Session = Depends(mutation)):
    s.language = body.language
    return {"language": s.language}

class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=40, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str = Field(min_length=10, max_length=128)

@router.post("/account/{operation}")
async def account(operation: Literal["register", "login"], body: Credentials, request: Request, response: Response, s: Session = Depends(mutation)):
    function = accounts.register if operation == "register" else accounts.login
    user_id = await asyncio.to_thread(function, body.username, body.password)
    if not user_id:
        raise HTTPException(400, "Не удалось войти или создать аккаунт. Проверьте данные. / Тіркелгіге кіру мүмкін болмады.")
    # Rotate identity; do not transfer another account's cart or history.
    new = Session(language=s.language, user_id=user_id, username=body.username)
    new.history = await asyncio.to_thread(accounts.read_history, user_id)
    key = secrets.token_urlsafe(32)
    sessions.pop(request.cookies.get("ekt_session", ""), None)
    sessions[key] = new
    response.set_cookie("ekt_session", key, httponly=True, samesite="strict", secure=settings.cookie_secure, max_age=3600)
    return {"csrf": new.csrf, "username": new.username, "history": new.history}

@router.get("/history")
def history(s: Session = Depends(session)):
    return {"messages": s.history, "saved": bool(s.user_id)}

@router.delete("/history")
async def clear_history(s: Session = Depends(mutation)):
    s.history.clear()
    s.last_query = ""
    await asyncio.to_thread(accounts.save_history, s.user_id, [])
    return {"deleted": True}

@router.delete("/account")
async def delete_account(request: Request, response: Response, s: Session = Depends(mutation)):
    if not s.user_id:
        raise HTTPException(401, "Войдите в аккаунт / Тіркелгіге кіріңіз")
    await asyncio.to_thread(accounts.delete_account, s.user_id)
    for key in list(sessions):
        if sessions[key].user_id == s.user_id:
            sessions.pop(key, None)
    response.delete_cookie("ekt_session")
    return {"deleted": True}

@router.get("/handoff")
def handoff(s: Session = Depends(session)):
    text = "EKT Assistant — запрос менеджеру / менеджерге сұрау\n"
    text += "\n".join(m["role"] + ": " + m["text"] for m in s.history[-20:])
    text += "\n\nКорзина / Себет:\n" + "\n".join(f"{i['product']['name']} — {i['quantity']}" for i in s.cart.values())
    return Response(text, media_type="text/plain; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="ekt-request.txt"'})
