from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    ENVIRONMENT: Literal["development", "production", "test"] = "development"

    # Database
    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/pickforme_test"

    # Telegram Bot
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_ALLOWED_USER_ID: int = 0
    TELEGRAM_WEBHOOK_SECRET: str = ""

    # External APIs
    GOOGLE_PLACES_API_KEY: str = ""
    GEMINI_API_KEY: str = ""
    GROQ_API_KEY: str = ""

    # Storage
    GCS_BUCKET_NAME: str = "pickforme-cold-archive"

    # IDE Agent Bridge (Option B)
    IDE_AGENT_BRIDGE_URL: str = "http://127.0.0.1:8000/agent/diagnose"

    # Ollama Local LLM Configuration
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "qwen2.5:7b"
    OLLAMA_ENABLED: bool = True

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


# 建立單例 settings 物件
settings = Settings()
