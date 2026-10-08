import logging
from typing import Any, Optional, Set

from src.agent.core import AgentEngine
from src.config import settings
from src.db.repositories.session import SessionRepository
from telegram import Bot
from telegram.error import TelegramError

logger = logging.getLogger(__name__)

# 去重快取：防護 Telegram Webhook 斷線重發機制
SEEN_UPDATE_IDS: Set[int] = set()
MAX_SEEN_SIZE = 1000


def is_update_processed(update_id: int) -> bool:
    """檢查 update_id 是否已處理過"""
    if update_id in SEEN_UPDATE_IDS:
        return True
    SEEN_UPDATE_IDS.add(update_id)
    if len(SEEN_UPDATE_IDS) > MAX_SEEN_SIZE:
        SEEN_UPDATE_IDS.pop()
    return False


class TelegramBotHandler:
    def __init__(
        self,
        agent_engine: Optional[AgentEngine] = None,
        session_repo: Optional[SessionRepository] = None,
        bot: Optional[Bot] = None,
    ):
        self.agent_engine = agent_engine or AgentEngine()
        self.session_repo = session_repo or SessionRepository()
        self.bot = bot or (Bot(token=settings.TELEGRAM_BOT_TOKEN) if settings.TELEGRAM_BOT_TOKEN else None)

    async def handle_update(self, update_data: dict[str, Any]) -> None:
        """處理 Telegram Webhook Update 主入口"""
        update_id = update_data.get("update_id")
        if update_id and is_update_processed(update_id):
            logger.info(f"Skipping duplicate Telegram update_id: {update_id}")
            return

        message = update_data.get("message") or update_data.get("edited_message")
        if not message:
            return

        from_user = message.get("from", {})
        user_id = from_user.get("id")
        chat_id = message.get("chat", {}).get("id")

        if not chat_id:
            return

        # 1. 白名單檢查
        if settings.TELEGRAM_ALLOWED_USER_ID and settings.TELEGRAM_ALLOWED_USER_ID != 0:
            if user_id != settings.TELEGRAM_ALLOWED_USER_ID:
                logger.warning(f"Unauthorized access attempt from Telegram user_id: {user_id}")
                await self._send_telegram_message(
                    chat_id, "⛔ 抱歉，您未獲權限使用此 PickForMe Bot。"
                )
                return

        # 2. 處理位置訊息 (Location Message)
        location = message.get("location")
        if location:
            lat = float(location.get("latitude", 0.0))
            lng = float(location.get("longitude", 0.0))
            await self.session_repo.update_location(chat_id=chat_id, lat=lat, lng=lng)
            await self._send_telegram_message(
                chat_id,
                f"📍 已成功接收並記錄您的定位資訊！\n(經緯度: {lat:.4f}, {lng:.4f})\n\n"
                "請告訴我您今天想吃些什麼？例如：「附近的拉麵」、「高評價燒肉」或「熱門居酒屋」。",
            )
            return

        # 3. 處理文字問句 (Text Message)
        text = message.get("text", "").strip()
        if not text:
            return

        if text.startswith("/start"):
            await self._send_telegram_message(
                chat_id,
                "🍱 歡迎使用 **PickForMe 日本美食特派員**！\n\n"
                "請先點擊下方 📎 傳送您的「位置」，或直接輸入地名與想吃的美食（例如「博多站附近的鰻魚飯」），我將為您比對最真實的 Tabelog 評分與 Google 地圖資訊！",
            )
            return

        # 查詢對話歷史與位置記憶
        user_location: Optional[tuple[float, float]] = None
        session = await self.session_repo.get_session(chat_id)
        if session and session.get("last_location_lat") and session.get("last_location_lng"):
            user_location = (
                float(session["last_location_lat"]),
                float(session["last_location_lng"]),
            )

        history = await self.session_repo.get_recent_history(chat_id, max_turns=3)

        # 發送初始思考訊息，取得 message_id
        status_msg_id = await self._send_telegram_message(
            chat_id, "🔍 PickForMe 正在啟動 Agent 為您搜尋..."
        )

        # 定義進度更新 Callback (動態編輯 Telegram 訊息)
        async def on_progress(status_text: str) -> None:
            if status_msg_id:
                await self._edit_telegram_message(chat_id, status_msg_id, status_text)

        # 執行 Agent Loop (附帶歷史紀錄)
        final_response = await self.agent_engine.run_agent(
            chat_id=chat_id,
            user_message=text,
            user_location=user_location,
            conversation_history=history,
            on_progress=on_progress,
        )

        # 紀錄本輪對話至 Session
        try:
            await self.session_repo.add_conversation_turn(
                chat_id=chat_id, user_message=text, assistant_response=final_response
            )
        except Exception as e:
            logger.warning(f"Could not save conversation turn to DB: {e}")

        # 最終呈現 Agent 結果
        if status_msg_id:
            await self._edit_telegram_message(chat_id, status_msg_id, final_response)
        else:
            await self._send_telegram_message(chat_id, final_response)

    async def _send_telegram_message(self, chat_id: int, text: str) -> Optional[int]:
        """發送 Telegram 訊息，回傳 message_id"""
        if not self.bot:
            logger.warning(f"[Mock Bot Send] chat_id={chat_id}: {text}")
            return None
        try:
            msg = await self.bot.send_message(chat_id=chat_id, text=text)
            return int(msg.message_id)
        except TelegramError as e:
            logger.error(f"Error sending Telegram message: {e}")
            return None

    async def _edit_telegram_message(self, chat_id: int, message_id: int, text: str) -> None:
        """編輯已發送的 Telegram 訊息以達成串流效果"""
        if not self.bot:
            logger.warning(f"[Mock Bot Edit] msg_id={message_id}: {text}")
            return
        try:
            await self.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text)
        except TelegramError as e:
            # 忽略內容未改變等無害錯誤
            if "Message is not modified" not in str(e):
                logger.warning(f"Error editing Telegram message {message_id}: {e}")
