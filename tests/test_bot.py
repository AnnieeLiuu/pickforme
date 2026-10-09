from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from src.bot.handlers import TelegramBotHandler, is_update_processed
from src.config import settings
from src.main import app


def test_is_update_processed_deduplication():
    update_id = 987654321
    assert is_update_processed(update_id) is False
    assert is_update_processed(update_id) is True  # 第二次返回 True 表示已處理過


@pytest.mark.asyncio
async def test_telegram_handler_whitelist_rejection():
    mock_bot = AsyncMock()
    handler = TelegramBotHandler(bot=mock_bot)

    with patch("src.config.settings.TELEGRAM_ALLOWED_USER_ID", 99999):
        unauthorized_update = {
            "update_id": 1001,
            "message": {
                "message_id": 1,
                "from": {"id": 12345},  # 不相符的 ID
                "chat": {"id": 12345},
                "text": "Hello",
            },
        }
        await handler.handle_update(unauthorized_update)
        mock_bot.send_message.assert_called_once()
        args = mock_bot.send_message.call_args[1]
        assert "未獲權限" in args["text"]


@pytest.mark.asyncio
async def test_telegram_handler_location_message():
    mock_bot = AsyncMock()
    mock_session_repo = AsyncMock()
    handler = TelegramBotHandler(bot=mock_bot, session_repo=mock_session_repo)

    location_update = {
        "update_id": 1002,
        "message": {
            "message_id": 2,
            "from": {"id": 100},
            "chat": {"id": 100},
            "location": {"latitude": 33.5902, "longitude": 130.4207},
        },
    }

    with patch("src.config.settings.TELEGRAM_ALLOWED_USER_ID", 0):
        await handler.handle_update(location_update)
        mock_session_repo.update_location.assert_called_once_with(
            chat_id=100, lat=33.5902, lng=130.4207
        )
        mock_bot.send_message.assert_called_once()
        args = mock_bot.send_message.call_args[1]
        assert "已成功接收並記錄您的定位資訊" in args["text"]


@pytest.mark.asyncio
async def test_telegram_webhook_fastapi_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/health")
        assert response.status_code == 200

        webhook_payload = {
            "update_id": 1003,
            "message": {
                "message_id": 3,
                "from": {"id": 100},
                "chat": {"id": 100},
                "text": "/start",
            },
        }

        headers = {}
        if settings.TELEGRAM_WEBHOOK_SECRET:
            headers["X-Telegram-Bot-Api-Secret-Token"] = settings.TELEGRAM_WEBHOOK_SECRET

        with patch("src.bot.webhook.bot_handler.handle_update", new_callable=AsyncMock) as mock_handle:
            resp = await ac.post("/api/telegram/webhook", json=webhook_payload, headers=headers)
            assert resp.status_code == 200
            assert resp.json() == {"status": "ok"}
            mock_handle.assert_called_once()
