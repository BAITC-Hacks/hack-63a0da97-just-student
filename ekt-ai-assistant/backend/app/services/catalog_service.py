from __future__ import annotations

import asyncio
import re
import time
import unicodedata
import json
from pathlib import Path
from urllib.parse import urlparse, unquote, urljoin
from collections.abc import Iterable, Mapping
from typing import Any

from app.core.config import settings
from app.models.catalog import (
    CatalogProduct,
    DataWarning,
    MatchedSpecificationItem,
    ProductMatch,
    StoreAvailability,
)
from app.models.specification import SpecificationItem
from app.services.ekt_client import EKTClient, EKTAPIError, ekt_client


SUPPLIER_ARTICLE_KEYS = {
    "artikulpostavshchika",
    "артикулпоставщика",
    "supplierarticle",
}
NOMINAL_CURRENT_KEYS = {
    "nominalnyytok",
    "номинальныйток",
    "ratedcurrent",
}


def _normalize(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = text.replace("ё", "е")
    return " ".join(re.findall(r"[\w]+", text, flags=re.UNICODE))


def _compact(value: Any) -> str:
    return "".join(character for character in _normalize(value) if character.isalnum())


def _scalar(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        text = str(value).strip()
        return text or None
    if isinstance(value, Mapping):
        for key in ("value", "text", "label", "name", "url"):
            if key in value:
                result = _scalar(value[key])
                if result:
                    return result
    return None


def _case_insensitive_get(data: Mapping[str, Any], *keys: str) -> Any:
    lowered = {str(key).casefold(): value for key, value in data.items()}
    for key in keys:
        if key.casefold() in lowered:
            return lowered[key.casefold()]
    return None


def _property_pairs(product: Mapping[str, Any]) -> list[tuple[str, str]]:
    raw = _case_insensitive_get(
        product,
        "properties",
        "property_values",
        "characteristics",
        "attributes",
    )
    result: list[tuple[str, str]] = []

    if isinstance(raw, Mapping):
        for key, value in raw.items():
            scalar = _scalar(value)
            if scalar:
                result.append((str(key), scalar))
    elif isinstance(raw, list):
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            key = _case_insensitive_get(
                item, "code", "key", "slug", "name", "title"
            )
            value = _case_insensitive_get(
                item, "value", "text", "label", "property_value"
            )
            key_text = _scalar(key)
            value_text = _scalar(value)
            if key_text and value_text:
                result.append((key_text, value_text))

    return result


def _property_value(
    product: Mapping[str, Any],
    accepted_keys: set[str],
) -> str | None:
    for key, value in _property_pairs(product):
        if _compact(key) in accepted_keys:
            return value
    return None


def _supplier_article(product: Mapping[str, Any]) -> str | None:
    return _property_value(product, SUPPLIER_ARTICLE_KEYS)


def _identifiers(product: Mapping[str, Any]) -> set[str]:
    values = [
        _case_insensitive_get(product, "id"),
        _case_insensitive_get(product, "article", "sku", "code"),
        _supplier_article(product),
    ]
    return {_compact(value) for value in values if _compact(value)}


def _product_text(product: Mapping[str, Any]) -> str:
    values: list[str] = []
    for key in (
        "id",
        "name",
        "title",
        "article",
        "sku",
        "code",
        "brand",
        "model",
        "description",
    ):
        scalar = _scalar(_case_insensitive_get(product, key))
        if scalar:
            values.append(scalar)
    values.extend(value for _, value in _property_pairs(product))
    return " ".join(values)


def _is_exact(item: SpecificationItem, product: Mapping[str, Any]) -> bool:
    identifiers = _identifiers(product)
    article = _compact(item.article)
    if article:
        return article in identifiers

    query = _compact(item.search_query)
    return bool(query and query in identifiers)


def _score(item: SpecificationItem, product: Mapping[str, Any]) -> float:
    if _is_exact(item, product):
        return 2.0
    # API list does not include supplier_article. Names often start with it;
    # boost retrieval only, then verify the identifier against detail properties.
    if item.article and _compact(item.article) in {_compact(t) for t in str(product.get("name", "")).split()}:
        return 1.8

    query = _normalize(item.search_query)
    product_text = _normalize(_product_text(product))
    if not query or not product_text:
        return 0.0

    query_tokens = set(query.split())
    product_tokens = set(product_text.split())
    if not query_tokens:
        return 0.0

    coverage = len(query_tokens & product_tokens) / len(query_tokens)
    phrase_bonus = 0.15 if query in product_text else 0.0
    return min(1.0, coverage * 0.85 + phrase_bonus)


def _extract_products(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, Mapping)]
    if not isinstance(payload, Mapping):
        return []

    for key in ("products", "items", "results"):
        value = _case_insensitive_get(payload, key)
        if isinstance(value, list):
            return [dict(item) for item in value if isinstance(item, Mapping)]

    data = _case_insensitive_get(payload, "data")
    if isinstance(data, list):
        return [dict(item) for item in data if isinstance(item, Mapping)]
    if isinstance(data, Mapping):
        return _extract_products(data)
    return []


def _pagination_last_page(payload: Any) -> int | None:
    if not isinstance(payload, Mapping):
        return None

    containers: list[Mapping[str, Any]] = [payload]
    for key in ("meta", "pagination", "data"):
        value = _case_insensitive_get(payload, key)
        if isinstance(value, Mapping):
            containers.append(value)

    for container in containers:
        value = _case_insensitive_get(
            container,
            "last_page",
            "lastPage",
            "total_pages",
            "page_count",
        )
        try:
            if value is not None:
                return max(1, int(value))
        except (TypeError, ValueError):
            continue
    return None


def _unwrap_product(payload: Any) -> dict[str, Any]:
    if isinstance(payload, list):
        first = next((item for item in payload if isinstance(item, Mapping)), {})
        return dict(first)
    if not isinstance(payload, Mapping):
        return {}

    for key in ("product", "item", "result", "data"):
        value = _case_insensitive_get(payload, key)
        if isinstance(value, Mapping):
            return _unwrap_product(value)
    return dict(payload)


def _number(value: Any) -> float | None:
    scalar = _scalar(value)
    if scalar is None:
        return None
    match = re.search(r"-?\d+(?:[.,]\d+)?", scalar.replace(" ", ""))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", "."))
    except ValueError:
        return None


def _first_number(product: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _number(_case_insensitive_get(product, key))
        if value is not None:
            return value
    return None


def _stores(product: Mapping[str, Any]) -> list[StoreAvailability]:
    raw = _case_insensitive_get(product, "stores", "warehouses", "stocks")
    if not isinstance(raw, list):
        return []

    result: list[StoreAvailability] = []
    for store in raw:
        if not isinstance(store, Mapping):
            continue
        name = _scalar(
            _case_insensitive_get(store, "name", "title", "city", "store")
        )
        quantity = _first_number(
            store, "quantity", "stock", "available", "available_quantity"
        )
        if name and quantity is not None and quantity > 0:
            result.append(StoreAvailability(name=name, quantity=quantity))
    return result


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            scalar = _scalar(item)
            if scalar:
                result.append(scalar)
        return result
    scalar = _scalar(value)
    return [scalar] if scalar else []


def _amp_values(text: str) -> set[str]:
    values: set[str] = set()
    for raw in re.findall(r"(?i)(\d+(?:[.,]\d+)?)\s*(?:a|а)\b", text):
        number = float(raw.replace(",", "."))
        values.add(f"{number:g} A")
    return values


def _data_warnings(product: Mapping[str, Any]) -> list[DataWarning]:
    name = _scalar(_case_insensitive_get(product, "name", "title")) or ""
    property_current = _property_value(product, NOMINAL_CURRENT_KEYS) or ""
    values = _amp_values(name) | _amp_values(property_current)
    if len(values) < 2:
        return []
    return [
        DataWarning(
            field="nominal_current",
            values=sorted(values),
            message="В данных каталога найдено противоречие.",
        )
    ]


def _url_value(value: Any) -> str | None:
    if isinstance(value, list):
        for item in value:
            result = _url_value(item)
            if result:
                return result
        return None
    if isinstance(value, Mapping):
        return _url_value(_case_insensitive_get(value, "url", "href", "link", "src"))
    result = _scalar(value)
    if not result:
        return None
    if result.startswith("/static/"):
        return result
    if result.startswith("/") and not result.startswith("//"):
        return urljoin(settings.ekt_base_url, result)
    return result if urlparse(result).scheme in ("https", "http") else None


def _to_catalog_product(product: Mapping[str, Any]) -> CatalogProduct | None:
    product_id = _case_insensitive_get(product, "id")
    name = _scalar(_case_insensitive_get(product, "name", "title"))
    if product_id is None or not name:
        return None

    stores = _stores(product)
    quantity = _first_number(
        product, "quantity", "stock", "available_quantity", "available"
    )
    if quantity is None and stores:
        quantity = sum(store.quantity for store in stores)

    price = _first_number(
        product, "price", "sale_price", "current_price", "cost"
    )
    article = _scalar(
        _case_insensitive_get(product, "article", "sku", "code")
    )
    image = _url_value(
        _case_insensitive_get(product, "image", "images", "picture", "photo")
    )
    url = _url_value(_case_insensitive_get(product, "url", "link"))
    raw_certificates = _case_insensitive_get(product, "certificates", "certificate")
    if not isinstance(raw_certificates, list):
        raw_certificates = [raw_certificates]
    certificates = [cert_url for item in raw_certificates if (cert_url := _url_value(item))]
    category = _scalar(_case_insensitive_get(product, "category"))
    category_source = "catalog" if category else None
    if not category and url:
        parts = [p for p in urlparse(url).path.split("/") if p]
        if len(parts) >= 3 and parts[0] == "catalog":
            category = "/".join(unquote(p) for p in parts[1:-1])
            category_source = "product_url"
    properties = dict(_property_pairs(product))
    minimum = _number(_case_insensitive_get(product, "minimum_quantity")) or _number(_property_value(product, {"kratnostmin", "minimumquantity", "минимальнаяпартия"}))
    step = _number(_case_insensitive_get(product, "quantity_step", "measure_ratio"))

    return CatalogProduct(
        id=product_id,
        name=name,
        category=category,
        category_source=category_source,
        minimum_quantity=minimum if minimum and minimum > 0 else None,
        quantity_step=step if step and step > 0 else None,
        unit=_scalar(_case_insensitive_get(product, "unit", "measure")) or properties.get("Единица"),
        characteristics=properties,
        article=article,
        supplier_article=_supplier_article(product),
        price=price,
        quantity=quantity,
        image=image,
        url=url,
        stores=stores,
        certificates=certificates,
        data_warnings=_data_warnings(product),
    )


class CatalogService:
    def __init__(self, client: EKTClient):
        self.client = client
        self._catalog: list[dict[str, Any]] = []
        self._catalog_expires_at = 0.0
        self._detail_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._lock = asyncio.Lock()
        self.coverage = {"products": 0, "complete": False, "source": "memory"}

    async def _load_catalog(self) -> list[dict[str, Any]]:
        if isinstance(self.client, EKTClient):
            return await self._load_live_catalog()
        now = time.monotonic()
        if self._catalog and now < self._catalog_expires_at:
            return self._catalog

        async with self._lock:
            now = time.monotonic()
            if self._catalog and now < self._catalog_expires_at:
                return self._catalog

            products: list[dict[str, Any]] = []
            seen_pages: set[tuple[str, ...]] = set()
            first_page_size: int | None = None

            for page in range(1, settings.catalog_search_max_pages + 1):
                payload = await self.client.get_products(page)
                page_products = _extract_products(payload)
                if not page_products:
                    break

                if first_page_size is None:
                    first_page_size = len(page_products)

                fingerprint = tuple(
                    str(_case_insensitive_get(product, "id", "article"))
                    for product in page_products[:10]
                )
                if fingerprint in seen_pages:
                    break
                seen_pages.add(fingerprint)
                products.extend(page_products)

                last_page = _pagination_last_page(payload)
                if last_page is not None and page >= last_page:
                    break
                if last_page is None and len(page_products) < (first_page_size or 1):
                    break

            self._catalog = products
            self._catalog_expires_at = (
                time.monotonic() + settings.catalog_cache_ttl_seconds
            )
            return products

    async def _load_live_catalog(self, advance=False) -> list[dict[str, Any]]:
        """Resume bounded batches in the background; keep the last usable snapshot."""
        if self._catalog and not advance and (time.monotonic() < self._catalog_expires_at or self._lock.locked()):
            return self._catalog
        async with self._lock:
            path = settings.data_dir / "catalog.json"
            if not self._catalog and path.exists():
                try:
                    snapshot = json.loads(path.read_text(encoding="utf-8"))
                    if snapshot.get("base_url") == self.client.base_url:
                        self._catalog = snapshot["products"]
                        self.coverage = snapshot["coverage"]
                        self._catalog_expires_at = time.monotonic() + max(0, settings.catalog_cache_ttl_seconds - (time.time() - snapshot["saved_at"]))
                except (ValueError, KeyError, OSError):
                    pass
            if self._catalog and time.monotonic() < self._catalog_expires_at and (not advance or self.coverage.get("complete")):
                return self._catalog
            # Old snapshots without a cursor are rebuilt once, then resumed.
            resume = not self.coverage.get("complete") and self.coverage.get("next_page") and time.time() - self.coverage.get("scan_started_at", 0) < 86400
            products = list(self._catalog) if resume else []
            next_page = self.coverage["next_page"] if resume else 1
            scan_started = self.coverage["scan_started_at"] if resume else time.time()
            seen = {str(p.get("id")) for p in products}
            complete, failure = False, None
            limit = min(next_page + settings.catalog_search_max_pages, settings.catalog_total_max_pages + 1)
            try:
                for start in range(next_page, limit, 4):
                    pages = list(range(start, min(start + 4, limit)))
                    payloads = await asyncio.gather(*(self.client.get_products(page) for page in pages))
                    for page, payload in zip(pages, payloads):
                        batch = _extract_products(payload)
                        if not isinstance(payload, (list, Mapping)) or (isinstance(payload, Mapping) and not any(k in payload for k in ("products", "items", "results", "data"))):
                            raise EKTAPIError("Неожиданный формат каталога.")
                        if not batch:
                            complete = True
                            break
                        fresh_products = [p for p in batch if str(p.get("id")) not in seen]
                        if not fresh_products:
                            raise EKTAPIError("API повторяет страницы; полнота каталога не подтверждена.")
                        for product in fresh_products:
                            products.append(product)
                            seen.add(str(product.get("id")))
                        next_page = page + 1
                        last_page = _pagination_last_page(payload)
                        if last_page is not None and page >= last_page:
                            complete = True
                            break
                    if complete:
                        break
            except EKTAPIError:
                failure = "refresh_failed"
                if not products:
                    if self._catalog:
                        self.coverage["stale"] = True
                        return self._catalog
                    raise
            self._catalog = products
            self._catalog_expires_at = time.monotonic() + settings.catalog_cache_ttl_seconds
            self.coverage = {"products": len(products), "complete": complete, "source": "EKT API", "checked_at": time.time(), "next_page": next_page, "scan_started_at": scan_started, "stale": bool(failure), "limit_reached": not complete and next_page > settings.catalog_total_max_pages}
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"base_url": self.client.base_url, "saved_at": time.time(), "products": products, "coverage": self.coverage}, ensure_ascii=False), encoding="utf-8")
            temporary.replace(path)
            return products

    async def _detail(self, summary: Mapping[str, Any]) -> dict[str, Any]:
        raw_id = _case_insensitive_get(summary, "id")
        try:
            product_id = int(raw_id)
        except (TypeError, ValueError):
            return dict(summary)

        cache_key = str(product_id)
        cached = self._detail_cache.get(cache_key)
        now = time.monotonic()
        if cached and now < cached[0]:
            return cached[1]

        detail = _unwrap_product(
            await self.client.get_product_detail(product_id)
        )
        merged = dict(summary)
        merged.update(detail)
        self._detail_cache[cache_key] = (
            now + settings.catalog_cache_ttl_seconds,
            merged,
        )
        return merged

    async def match_item(
        self,
        item: SpecificationItem,
    ) -> MatchedSpecificationItem:
        catalog = await self._load_catalog()
        ranked = sorted(
            ((_score(item, product), product) for product in catalog),
            key=lambda value: value[0],
            reverse=True,
        )
        ranked = [value for value in ranked if value[0] >= 0.25]

        if not ranked:
            return MatchedSpecificationItem(
                source=item,
                match=ProductMatch(status="not_found"),
                requested_quantity=item.quantity,
            )

        detail_limit = max(1, settings.catalog_detail_candidates)
        detailed: list[tuple[float, dict[str, Any]]] = []
        for _, summary in ranked[:detail_limit]:
            detail = await self._detail(summary)
            detailed.append((_score(item, detail), detail))
        detailed.sort(key=lambda value: value[0], reverse=True)

        exact = [product for _, product in detailed if _is_exact(item, product)]
        unique_exact = {
            str(_case_insensitive_get(product, "id")): product
            for product in exact
        }

        selected: dict[str, Any] | None = None
        ambiguous: list[dict[str, Any]] = []
        if len(unique_exact) == 1:
            selected = next(iter(unique_exact.values()))
        elif len(unique_exact) > 1:
            ambiguous = list(unique_exact.values())
        elif item.article:
            ambiguous = [product for score, product in detailed if score >= 0.4]
        else:
            top_score = detailed[0][0]
            second_score = detailed[1][0] if len(detailed) > 1 else 0.0
            if top_score >= 0.72 and top_score - second_score >= 0.12:
                selected = detailed[0][1]
            elif top_score >= 0.4:
                ambiguous = [
                    product for score, product in detailed if score >= 0.4
                ]

        if selected is not None:
            mapped = _to_catalog_product(selected)
            if mapped is None:
                return MatchedSpecificationItem(
                    source=item,
                    match=ProductMatch(status="not_found"),
                    requested_quantity=item.quantity,
                )

            enough_stock = None
            if item.quantity is not None and mapped.quantity is not None:
                enough_stock = mapped.quantity >= item.quantity
            return MatchedSpecificationItem(
                source=item,
                match=ProductMatch(status="matched", product=mapped),
                requested_quantity=item.quantity,
                available_quantity=mapped.quantity,
                enough_stock=enough_stock,
            )

        candidates = [
            mapped
            for product in ambiguous
            if (mapped := _to_catalog_product(product)) is not None
        ]
        if candidates:
            return MatchedSpecificationItem(
                source=item,
                match=ProductMatch(
                    status="ambiguous",
                    candidates=candidates,
                ),
                requested_quantity=item.quantity,
            )

        return MatchedSpecificationItem(
            source=item,
            match=ProductMatch(status="not_found"),
            requested_quantity=item.quantity,
        )

    async def match_items(
        self,
        items: Iterable[SpecificationItem],
    ) -> list[MatchedSpecificationItem]:
        return [await self.match_item(item) for item in items]


catalog_service = CatalogService(ekt_client)
