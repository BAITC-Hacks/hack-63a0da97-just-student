from pydantic import BaseModel, Field


class SpecificationItem(BaseModel):
    raw_text: str = Field(
        description="Исходное описание позиции из документа"
    )

    search_query: str = Field(
        description="Текст, который можно использовать для поиска товара"
    )

    article: str | None = Field(
        default=None,
        description="Артикул, если он явно присутствует в документе"
    )

    quantity: float | None = Field(
        default=None,
        description="Количество, если оно явно указано"
    )

    unit: str | None = Field(
        default=None,
        description="Единица измерения: шт, м, уп и т.д."
    )


class SpecificationParseResult(BaseModel):
    items: list[SpecificationItem]

    unresolved: list[str] = Field(
        default_factory=list,
        description="Строки документа, которые нельзя уверенно разобрать"
    )