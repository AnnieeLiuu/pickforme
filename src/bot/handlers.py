import logging
from typing import Any, Optional, Set

from src.agent.core import AgentEngine
from src.agent.devops import DevOpsSubagent
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
        devops_subagent: Optional[DevOpsSubagent] = None,
    ):
        self.agent_engine = agent_engine or AgentEngine()
        self.session_repo = session_repo or SessionRepository()
        self.bot = bot or (Bot(token=settings.TELEGRAM_BOT_TOKEN) if settings.TELEGRAM_BOT_TOKEN else None)
        self.devops_subagent = devops_subagent or DevOpsSubagent(agent_engine=self.agent_engine)

    async def setup_commands(self) -> None:
        """向 Telegram 伺服器註冊選單與指令提示 (Slash Commands Menu)"""
        if not self.bot:
            return
        try:
            from telegram import BotCommand

            commands = [
                BotCommand("start", "🍱 歡迎選單與美食指南說明"),
                BotCommand("status", "📊 系統健康狀態與對話 Trace"),
                BotCommand("prewarm", "♨️ 觸發地區熱門美食快取預熱"),
            ]
            await self.bot.set_my_commands(commands)
            logger.info("Successfully registered Telegram Bot slash commands menu.")
        except Exception as e:
            logger.warning(f"Could not register Telegram bot commands menu: {e}")

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
                await self._send_telegram_message(chat_id, "⛔ 抱歉，您未獲權限使用此 PickForMe Bot。")
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

        if text.startswith("/status"):
            gemini_status = "🟢 已設定" if settings.GEMINI_API_KEY else "🔴 未設定"
            groq_status = "🟢 已設定" if settings.GROQ_API_KEY else "🔴 未設定"
            google_status = "🟢 已設定" if settings.GOOGLE_PLACES_API_KEY else "🔴 未設定"

            status_text = (
                "📊 **PickForMe 系統健康狀態總覽**\n\n"
                "🗄️ **資料庫**：🟢 正常 (Neon Cloud Postgres)\n"
                f"🧠 **Gemini API**：{gemini_status}\n"
                f"⚡ **Groq Fallback**：{groq_status}\n"
                f"🗺️ **Google Places API**：{google_status}\n\n"
                "💡 輸入 `/logs` 可查看最近一次 Agent 執行與錯誤 Trace。"
            )
            await self._send_telegram_message(chat_id, status_text)
            return

        if text.startswith("/logs"):
            recent_run = await self.session_repo.get_latest_agent_run(chat_id)
            if not recent_run:
                await self._send_telegram_message(chat_id, "ℹ️ 目前尚無 Agent 執行日誌紀錄。")
                return

            status_emoji = "🟢 成功" if recent_run.get("status") == "completed" else "🔴 失敗"
            log_text = (
                "📝 **最新 Agent 執行日誌 (Trace)**\n\n"
                f"📌 **問句**：`{recent_run.get('user_query', '')}`\n"
                f"⏱️ **狀態**：{status_emoji}\n"
                f"🤖 **模型**：`{recent_run.get('model_used', 'unknown')}`\n"
                f"🪙 **Tokens**：{recent_run.get('total_tokens', 0)}\n"
                f"⏳ **耗時**：{recent_run.get('latency_ms', 0)} ms"
            )
            await self._send_telegram_message(chat_id, log_text)
            return

        if text.startswith("/prewarm"):
            prewarm_msg = (
                "♨️ **PickForMe 精準地標與商圈美食快取預熱**\n\n"
                "📍 **預熱概念**：日本旅遊以「熱門車站/觀光地標」為核心，預熱可將熱門地標 800m 內的 Tabelog 頂級評分餐廳提前寫入 Neon DB 快取，達成秒答體驗！\n\n"
                "🎯 **推薦預熱地標範例**：\n"
                "• 關東：`橫濱中華街`、`新宿歌舞伎町`、`澀谷`\n"
                "• 關西：`心齋橋`、`難波`、`京都河原町`\n"
                "• 九州：`博多站`、`天神`、`由布院`\n\n"
                "💡 **發動指令**：在對話中直接輸入 `預熱 橫濱中華街` 或 `預熱 博多站`，Agent 即會啟動該地標的精準預熱！"
            )
            await self._send_telegram_message(chat_id, prewarm_msg)
            return

        lowered_text = text.lower()
        if any(k in lowered_text for k in ["/debug", "@debug", "debug", "排查", "除錯", "診斷"]):
            status_msg_id = await self._send_telegram_message(
                chat_id, "🛠️ **PickForMe DevOps Subagent 正在為您進行排查與診斷...**"
            )
            report_text = await self.devops_subagent.run_diagnosis(text)
            if status_msg_id:
                await self._edit_telegram_message(chat_id, status_msg_id, report_text)
            else:
                await self._send_telegram_message(chat_id, report_text)
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
        status_msg_id = await self._send_telegram_message(chat_id, "🧭 收到！PickForMe 正在啟動美食 Agent 為您規劃...")

        # 定義進度更新 Callback (動態編輯 Telegram 訊息)
        async def on_progress(status_text: str) -> None:
            if status_msg_id:
                await self._edit_telegram_message(chat_id, status_msg_id, status_text)

        # 執行 Agent Loop (支援本機 Ollama 優先、雲端模型自動容錯與即時進度回傳)
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
        """發送 Telegram 訊息，支援 Markdown 並含降級機制"""
        if not self.bot:
            logger.warning(f"[Mock Bot Send] chat_id={chat_id}: {text}")
            return None
        try:
            msg = await self.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
            return int(msg.message_id)
        except TelegramError as e:
            logger.warning(f"Markdown send failed ({e}), falling back to plain text")
            try:
                msg = await self.bot.send_message(chat_id=chat_id, text=text)
                return int(msg.message_id)
            except TelegramError as err:
                logger.error(f"Error sending Telegram message: {err}")
                return None

    async def _edit_telegram_message(self, chat_id: int, message_id: int, text: str) -> None:
        """編輯已發送的 Telegram 訊息以達成串流效果，支援 Markdown 並含降級機制"""
        if not self.bot:
            logger.warning(f"[Mock Bot Edit] msg_id={message_id}: {text}")
            return
        try:
            await self.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, parse_mode="Markdown")
        except TelegramError as e:
            if "not modified" in str(e).lower():
                return
            logger.warning(f"Markdown edit failed ({e}), falling back to plain text")
            try:
                await self.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text)
            except TelegramError as err:
                if "not modified" not in str(err).lower():
                    logger.warning(f"Error editing Telegram message {message_id}: {err}")
