import json
import re
from app.core.config import settings
from app.services.catalog_service import _compact, _normalize

# Only explicit technical fields participate. Marketing flags and internal IDs do not.
ALIASES = {
    "полюса": "poles", "количествополюсов": "poles", "kolichestvopolyusov": "poles",
    "номинальныйток": "current", "nominalnyytok": "current",
    "отключающаяспособность": "breaking", "nominalnayaotklyuchayushchayasposobnost": "breaking",
    "номинальноенапряжение": "voltage", "nominalnoenapryazhenie": "voltage",
    "типустановки": "mount", "tipustanovki": "mount",
    "сечение": "section", "жилы": "cores",
}

def technical(product):
    return {ALIASES[_compact(k)]: (k, v) for k,v in product.characteristics.items() if _compact(k) in ALIASES}

def value(text):
    # Keep decimal separators: 1.6 A must never compare equal to 16 A.
    normalized = text.casefold().replace(",", ".").replace("а", "a").replace("в", "v")
    return re.sub(r"\s+", "", normalized)

def reason(source, candidate, language="ru"):
    if source.data_warnings or candidate.data_warnings or not source.category or source.category != candidate.category:
        return None
    required, actual = technical(source), technical(candidate)
    if len(required) < 2 or any(key not in actual or value(v[1]) != value(actual[key][1]) for key,v in required.items()):
        return None
    prefix = "Категория және параметрлер сәйкес: " if language == "kk" else "Совпадают категория и параметры: "
    suffix = ". Үйлесімділікті маман растауы керек." if language == "kk" else ". Совместимость должен подтвердить специалист."
    return prefix + "; ".join(f"{k}: {v}" for k,v in required.values()) + suffix

def related_ids(product_id, demo=False):
    if demo:
        return [{"product_id": 4, "reason": "Демонстрационный аксессуар для серии DEMO-002: совместимость задана в демонстрационном каталоге.", "reason_kk": "DEMO-002 сериясына арналған демо керек-жарақ: үйлесімділік демо каталогта көрсетілген."}] if str(product_id) == "2" else []
    if not settings.related_products_file.exists():
        return []
    data = json.loads(settings.related_products_file.read_text(encoding="utf-8"))
    return data.get(str(product_id), [])[:3]
