from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    app_name: str = "EKT AI Assistant"
    app_version: str = "0.1.0"
    demo_mode: bool = True
    cookie_secure: bool = False
    purchase_terms: str = ""
    manager_url: str = "https://ekt.kz/about/contacts/"
    ai_dialogue: bool = True
    openai_timeout_seconds: float = 25
    catalog_warmup: bool = True
    data_dir: Path = BACKEND_DIR / "var"
    # Only explicitly curated relations; never guess electrical compatibility.
    related_products_file: Path = BACKEND_DIR / "data" / "related_products.json"

    ekt_base_url: str = "https://ekt.kz"
    ekt_api_user: str = ""
    ekt_api_password: str = ""

    openai_api_key: str = ""
    openai_model: str = "gpt-5.6-luna"
    nvidia_api_key: str = ""

    catalog_search_max_pages: int = Field(default=100, ge=1, le=1000)
    catalog_detail_candidates: int = Field(default=5, ge=1, le=20)
    catalog_cache_ttl_seconds: int = Field(default=300, ge=0, le=86400)

    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


settings = Settings()
