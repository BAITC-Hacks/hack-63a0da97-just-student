"""Synthetic fixtures, never presented as partner inventory."""
from copy import deepcopy

PRODUCTS = [
    {"id": 4, "article": "DEMO-004", "name": "Кожух клемм для DEMO-002 · демонстрационный",
     "category": "Аксессуары", "price": 900, "quantity": 20,
     "minimum_quantity": 1, "quantity_step": 1, "unit": "шт", "properties": {"Совместимость": "DEMO-002"}},
    {"id": 1, "article": "DEMO-001", "name": "Автомат 3P 160А 18кА · демонстрационный",
     "category": "Автоматические выключатели", "price": 24000, "quantity": 0,
     "properties": {"Полюса": "3", "Номинальный ток": "160 А", "Отключающая способность": "18 кА"},
     "certificates": ["/static/demo-certificate.txt"], "stores": []},
    {"id": 2, "article": "DEMO-002", "name": "Автомат 3P 160А 18кА · вариант B",
     "category": "Автоматические выключатели", "price": 26500, "quantity": 12,
     "properties": {"Полюса": "3", "Номинальный ток": "160 А", "Отключающая способность": "18 кА"},
     "certificates": ["/static/demo-certificate.txt"],
     "stores": [{"name": "Демо-склад Алматы", "quantity": 8}, {"name": "Демо-склад Астана", "quantity": 4}]},
    {"id": 3, "article": "DEMO-003", "name": "Кабель ВВГнг 3×2,5 · демонстрационный",
     "category": "Кабель", "price": 650, "quantity": 100,
     "properties": {"Жилы": "3", "Сечение": "2,5 мм²", "Единица": "м"},
     "stores": [{"name": "Демо-склад Алматы", "quantity": 100}], "certificates": []},
]

class DemoClient:
    async def get_products(self, page=1):
        return {"products": deepcopy(PRODUCTS) if page == 1 else [], "last_page": 1}

    async def get_product_detail(self, product_id):
        return deepcopy(next((p for p in PRODUCTS if p["id"] == product_id), {}))
