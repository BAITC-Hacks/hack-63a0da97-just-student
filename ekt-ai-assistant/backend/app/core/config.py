from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    app_name: str = "EKT AI Assistant"
    app_version: str = "0.1.0"

    ekt_base_url: str = "https://ekt.kz"
    ekt_api_user: str = ""
    ekt_api_password: str = ""

    openai_api_key: str = ""
    nvidia_api_key: str = ""

    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


settings = Settings()