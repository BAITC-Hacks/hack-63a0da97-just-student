from typing import Literal

from pydantic import BaseModel, Field

from app.models.specification import SpecificationItem


class FileMetadata(BaseModel):
    filename: str
    extension: str
    characters: int
    truncated: bool


class StoreAvailability(BaseModel):
    name: str
    quantity: float


class DataWarning(BaseModel):
    field: str
    values: list[str]
    message: str


class CatalogProduct(BaseModel):
    id: int | str
    name: str
    category: str | None = None
    category_source: str | None = None
    minimum_quantity: float | None = None
    quantity_step: float | None = None
    unit: str | None = None
    characteristics: dict[str, str] = Field(default_factory=dict)
    article: str | None = None
    supplier_article: str | None = None
    price: float | None = None
    quantity: float | None = None
    image: str | None = None
    url: str | None = None
    stores: list[StoreAvailability] = Field(default_factory=list)
    certificates: list[str] = Field(default_factory=list)
    data_warnings: list[DataWarning] = Field(default_factory=list)


class ProductMatch(BaseModel):
    status: Literal["matched", "ambiguous", "not_found"]
    product: CatalogProduct | None = None
    candidates: list[CatalogProduct] = Field(default_factory=list)


class MatchedSpecificationItem(BaseModel):
    source: SpecificationItem
    match: ProductMatch
    requested_quantity: float | None = None
    available_quantity: float | None = None
    enough_stock: bool | None = None


class MatchProductsResponse(BaseModel):
    success: bool = True
    file: FileMetadata
    items: list[MatchedSpecificationItem]
    unresolved: list[str] = Field(default_factory=list)
