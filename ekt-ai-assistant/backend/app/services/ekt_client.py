from typing import Any

import httpx

from app.core.config import settings


class EKTAPIError(Exception):
    pass


class EKTClient:
    def __init__(self):
        self.base_url = settings.ekt_base_url.rstrip("/")

        self.auth = httpx.BasicAuth(
            username=settings.ekt_api_user,
            password=settings.ekt_api_password,
        )

        self.timeout = httpx.Timeout(
            connect=10.0,
            read=30.0,
            write=10.0,
            pool=10.0,
        )

    async def get_products(self, page: int = 1) -> dict[str, Any]:
        """
        Получить страницу каталога ekt.kz.
        """

        if page < 1:
            raise ValueError("page должен быть >= 1")

        url = f"{self.base_url}/api/products"

        try:
            async with httpx.AsyncClient(
                auth=self.auth,
                timeout=self.timeout,
                follow_redirects=True,
            ) as client:
                response = await client.get(
                    url,
                    params={
                        "page": page,
                    },
                )

        except httpx.RequestError as exc:
            raise EKTAPIError(
                f"Не удалось подключиться к EKT API: {exc}"
            ) from exc

        if response.status_code == 401:
            raise EKTAPIError(
                "EKT API вернул 401 Unauthorized. "
                "Проверь EKT_API_USER и EKT_API_PASSWORD."
            )

        if response.status_code != 200:
            raise EKTAPIError(
                f"EKT API вернул HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )

        try:
            return response.json()

        except ValueError as exc:
            raise EKTAPIError(
                "EKT API вернул некорректный JSON"
            ) from exc

    async def get_product_detail(
        self,
        product_id: int,
    ) -> dict[str, Any]:
        """
        Получить детальную информацию о товаре.
        """

        if product_id <= 0:
            raise ValueError("product_id должен быть > 0")

        url = f"{self.base_url}/api/products/detail"

        try:
            async with httpx.AsyncClient(
                auth=self.auth,
                timeout=self.timeout,
                follow_redirects=True,
            ) as client:
                response = await client.get(
                    url,
                    params={
                        "id": product_id,
                    },
                )

        except httpx.RequestError as exc:
            raise EKTAPIError(
                f"Не удалось подключиться к EKT API: {exc}"
            ) from exc

        if response.status_code == 401:
            raise EKTAPIError(
                "EKT API вернул 401 Unauthorized. "
                "Проверь EKT_API_USER и EKT_API_PASSWORD."
            )

        if response.status_code == 404:
            raise EKTAPIError(
                f"Товар с id={product_id} не найден"
            )

        if response.status_code != 200:
            raise EKTAPIError(
                f"EKT API вернул HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )

        try:
            return response.json()

        except ValueError as exc:
            raise EKTAPIError(
                "EKT API вернул некорректный JSON"
            ) from exc


ekt_client = EKTClient()