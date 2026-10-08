from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    ENVIRONMENT: Literal["development", "production", "test"] = "development"

    # Database
    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/pickforme_test"

    # Telegram Bot
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_ALLOWED_USER_ID: int = 0

    # External APIs
    GOOGLE_PLACES_API_KEY: str = ""
    GEMINI_API_KEY: str = ""
    GROQ_API_KEY: str = ""

    # Storage
    GCS_BUCKET_NAME: str = "pickforme-cold-archive"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


# 建立單例 settings 物件
settings = Settings()
