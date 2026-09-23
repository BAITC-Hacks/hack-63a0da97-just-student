# EKT AI Assistant

Backend для обработки PDF/DOCX/XLSX-спецификаций и сопоставления позиций
с реальными товарами каталога EKT.

## Запуск

Требуется Python 3.11.

```powershell
cd ekt-ai-assistant/backend
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```

Swagger UI: <http://127.0.0.1:8000/docs>

## Настройка

Скопируйте значения в `ekt-ai-assistant/backend/.env`. Файл уже добавлен в
`.gitignore`; не коммитьте и не публикуйте его.

```dotenv
EKT_BASE_URL=https://ekt.kz
EKT_API_USER=
EKT_API_PASSWORD=
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5.6-luna
NVIDIA_API_KEY=
```

`NVIDIA_API_KEY` зарезервирован для следующего этапа semantic search и пока
не используется.

## Endpoint'ы

- `GET /` — состояние приложения.
- `GET /api/health` — только флаги настройки интеграций, без секретов.
- `GET /api/ekt/products?page=1` — страница каталога EKT.
- `GET /api/ekt/products/{product_id}` — детальная карточка EKT.
- `POST /api/attachments/extract` — извлечение текста из PDF/DOCX/XLSX.
- `POST /api/attachments/parse-specification` — извлечение и структурный
  разбор через OpenAI.
- `POST /api/attachments/match-products` — полный flow: файл → текст →
  OpenAI → поиск по каталогу → detail → остаток и предупреждения.

В Swagger откройте нужный `POST`, нажмите **Try it out**, выберите файл и
нажмите **Execute**.

Пример фрагмента ответа `match-products`:

```json
{
  "success": true,
  "file": {
    "filename": "test_spec.xlsx",
    "extension": ".xlsx",
    "characters": 150,
    "truncated": false
  },
  "items": [
    {
      "source": {
        "raw_text": "027228 | Legrand DRX250 160A 18kA | 30",
        "search_query": "027228 Legrand DRX250 160A 18kA",
        "article": "027228",
        "quantity": 30,
        "unit": "шт"
      },
      "match": {
        "status": "matched",
        "product": {
          "id": 515291,
          "name": "027228 АВ DRX250 MT 3ф 160А 18ka Legrand (1)",
          "article": "200300285_",
          "supplier_article": "027228",
          "price": 64920,
          "quantity": 23,
          "image": null,
          "url": null,
          "stores": [],
          "certificates": [],
          "data_warnings": []
        },
        "candidates": []
      },
      "requested_quantity": 30,
      "available_quantity": 23,
      "enough_stock": false
    }
  ]
}
```

Значения выше иллюстрируют форму ответа. Backend не подставляет выдуманные
цены, остатки, ссылки или характеристики: фактические значения берутся из
ответа EKT. При нескольких точных/похожих карточках возвращается
`ambiguous`, а при отсутствии совпадения — `not_found`.

## Тесты

```powershell
cd ekt-ai-assistant/backend
python -m unittest discover -v
```
