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
        mock_session_repo.update_location.assert_called_once_with(chat_id=100, lat=33.5902, lng=130.4207)
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


@pytest.mark.asyncio
async def test_telegram_handler_prewarm_command():
    mock_bot = AsyncMock()
    mock_prewarm = AsyncMock()
    from uuid import uuid4

    mock_prewarm.create_job.return_value = uuid4()
    mock_session_repo = AsyncMock()
    mock_session_repo.get_working_memory.return_value = {}
    handler = TelegramBotHandler(bot=mock_bot, prewarm_repo=mock_prewarm, session_repo=mock_session_repo)

    prewarm_update = {
        "update_id": 1004,
        "message": {
            "message_id": 4,
            "from": {"id": 100},
            "chat": {"id": 100},
            "text": "預熱 新宿御苑",
        },
    }

    with patch("src.config.settings.TELEGRAM_ALLOWED_USER_ID", 0):
        await handler.handle_update(prewarm_update)
        mock_prewarm.create_job.assert_called_once_with(area_name="新宿御苑", target_count=20, chat_id=100)
        mock_bot.send_message.assert_called_once()
        args = mock_bot.send_message.call_args[1]
        assert "新宿御苑" in args["text"]
        assert "排入 Neon 預熱佇列" in args["text"]


@pytest.mark.asyncio
async def test_telegram_handler_location_guard_japan_and_non_japan():
    mock_bot = AsyncMock()
    mock_session_repo = AsyncMock()
    handler = TelegramBotHandler(bot=mock_bot, session_repo=mock_session_repo)

    # 1. 境外定位 (台北: 25.033, 121.565) -> 阻斷不入庫
    taipei_update = {
        "update_id": 1005,
        "message": {
            "message_id": 5,
            "from": {"id": 100},
            "chat": {"id": 100},
            "location": {"latitude": 25.033, "longitude": 121.565},
        },
    }
    with patch("src.config.settings.TELEGRAM_ALLOWED_USER_ID", 0):
        await handler.handle_update(taipei_update)
        mock_session_repo.update_location.assert_not_called()
        mock_bot.send_message.assert_called_once()
        assert "不在日本國境內" in mock_bot.send_message.call_args[1]["text"]

    mock_bot.reset_mock()
    mock_session_repo.reset_mock()

    # 2. 日本境內定位 (東京: 35.6812, 139.7671) -> 正常更新入庫
    tokyo_update = {
        "update_id": 1006,
        "message": {
            "message_id": 6,
            "from": {"id": 100},
            "chat": {"id": 100},
            "location": {"latitude": 35.6812, "longitude": 139.7671},
        },
    }
    with patch("src.config.settings.TELEGRAM_ALLOWED_USER_ID", 0):
        await handler.handle_update(tokyo_update)
        mock_session_repo.update_location.assert_called_once_with(chat_id=100, lat=35.6812, lng=139.7671)
        mock_bot.send_message.assert_called_once()
        assert "已成功接收並記錄您的定位資訊" in mock_bot.send_message.call_args[1]["text"]
