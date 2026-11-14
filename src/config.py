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

    # Storage & Cold Archive Watermark
    GCS_BUCKET_NAME: str = "pickforme-cold-archive"
    COLD_ARCHIVE_MAX_HOT_RUNS: int = 500
    COLD_ARCHIVE_MAX_DB_SIZE_MB: float = 400.0

    # IDE Agent Bridge (Option B)
    IDE_AGENT_BRIDGE_URL: str = "http://127.0.0.1:8000/agent/diagnose"

    # LLM Provider Configuration ('groq' | 'ollama' | 'gemini')
    LLM_PRIMARY_PROVIDER: str = "groq"
    GROQ_MODELS: str = "qwen/qwen3.8-27b,openai/gpt-oss-120b,openai/gpt-oss-20b"
    GEMINI_MODEL: str = "gemini-3.8-flash"

    # Ollama Local LLM Configuration
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "qwen2.5:3b"
    OLLAMA_ENABLED: bool = True

    # Langfuse Online Observability
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_HOST: str = "https://cloud.langfuse.com"

    def is_langfuse_enabled(self) -> bool:
        """Langfuse is only enabled when keys are present and not in test environment."""
        return bool(self.LANGFUSE_PUBLIC_KEY and self.LANGFUSE_SECRET_KEY and self.ENVIRONMENT != "test")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


# 建立單例 settings 物件
settings = Settings()
