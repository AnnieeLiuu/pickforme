import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from typing import AsyncGenerator

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi import FastAPI

from src.bot.webhook import router as telegram_router
from src.config import settings
from src.db.connection import close_db_pool, get_db_pool

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """FastAPI 應用程式生命週期管理 (初始化與關閉 DB Pool)"""
    logger.info("Initializing PickForMe application lifespan...")
    try:
        # 測試並預熱 DB 連線池 (若有配置)
        if settings.DATABASE_URL:
            await get_db_pool()

        # 註冊 Telegram 選單與指令提示
        if settings.TELEGRAM_BOT_TOKEN:
            from src.bot.handlers import TelegramBotHandler

            handler = TelegramBotHandler()
            await handler.setup_commands()
    except Exception as e:
        logger.warning(f"Failed to complete startup tasks: {e}")

    yield

    logger.info("Shutting down PickForMe application lifespan...")
    await close_db_pool()


app = FastAPI(
    title="PickForMe - 日本美食 AI Agent API",
    description="結合 Google Places API、Tabelog Scraper、Entity Resolver 與自研 Agent Engine 之 AI Agent 後端",
    version="0.1.0",
    lifespan=lifespan,
)

# 註冊 Telegram Webhook 路由
app.include_router(telegram_router)


@app.get("/")
async def root() -> dict[str, str]:
    return {"message": "PickForMe AI Agent API is running."}


@app.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok", "environment": settings.ENVIRONMENT}
