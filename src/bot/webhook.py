import logging
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request
from src.bot.handlers import TelegramBotHandler
from src.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/telegram", tags=["Telegram Bot Webhook"])

# 單例 Handler 物件
bot_handler = TelegramBotHandler()


@router.post("/webhook")
async def telegram_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_telegram_bot_api_secret_token: Optional[str] = Header(None),
) -> dict[str, str]:
    """FastAPI Webhook 端點：接收來自 Telegram Server 的 Update 訊息"""
    try:
        update_data: dict[str, Any] = await request.json()
    except Exception as e:
        logger.error(f"Invalid JSON payload in Telegram webhook: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON format")

    # 驗證 Webhook Secret Token (若有設定)
    if settings.TELEGRAM_WEBHOOK_SECRET:
        if x_telegram_bot_api_secret_token != settings.TELEGRAM_WEBHOOK_SECRET:
            logger.warning("Invalid Telegram secret token header")
            raise HTTPException(status_code=403, detail="Invalid webhook secret token")

    logger.info(f"Received Telegram webhook update (update_id={update_data.get('update_id')})")

    # 在背景任務執行 handler，確保 200 OK 立即回傳給 Telegram 防止 Webhook 重試
    background_tasks.add_task(bot_handler.handle_update, update_data)

    return {"status": "ok"}
