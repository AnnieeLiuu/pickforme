import asyncio
import logging
from typing import Any, Optional, Set

from src.agent.core import AgentEngine
from src.agent.devops import DevOpsSubagent
from src.config import settings
from src.db.repositories.prewarm import PrewarmRepository
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
        prewarm_repo: Optional[PrewarmRepository] = None,
        prewarm_worker: Optional[Any] = None,
    ):
        self.agent_engine = agent_engine or AgentEngine()
        self.session_repo = session_repo or SessionRepository()
        self.bot = bot or (Bot(token=settings.TELEGRAM_BOT_TOKEN) if settings.TELEGRAM_BOT_TOKEN else None)
        self.devops_subagent = devops_subagent or DevOpsSubagent(agent_engine=self.agent_engine)
        self.prewarm_repo = prewarm_repo or PrewarmRepository()
        self.prewarm_worker = prewarm_worker

    def _dispatch_background_prewarm(self, count: int = 1) -> None:
        """非同步在背景呼叫 PrewarmWorker 處理任務，無需外部常駐 Worker 進程"""
        try:
            if self.prewarm_worker is None:
                from src.worker.prewarm import PrewarmWorker

                self.prewarm_worker = PrewarmWorker(
                    prewarm_repo=self.prewarm_repo,
                    bot=self.bot,
                )
            for _ in range(count):
                asyncio.create_task(self.prewarm_worker.process_next_job())
        except Exception as e:
            logger.warning(f"Failed to dispatch background prewarm worker: {e}")

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
                BotCommand("archive", "📦 歷史 Trace 轉檔 Parquet 冷儲存歸檔"),
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

        # 檢查是否有等待確認的縣級預熱選單 (Wide-Area Disambiguation Guardrail)
        try:
            wm = await self.session_repo.get_working_memory(chat_id)
            pending_prewarm = wm.get("pending_prewarm_options") if isinstance(wm, dict) else None
        except Exception as e:
            logger.warning(f"Failed to get working memory in handle_update: {e}")
            pending_prewarm = None

        if isinstance(pending_prewarm, dict) and pending_prewarm.get("options"):
            stripped_text = text.strip()
            lowered_text = stripped_text.lower()
            if lowered_text in ["取消", "/cancel", "cancel"]:
                await self.session_repo.update_working_memory(chat_id=chat_id, clear_pending_prewarm=True)
                await self._send_telegram_message(chat_id, "已取消預熱任務。")
                return

            options: list[dict[str, str]] = pending_prewarm.get("options", [])  # type: ignore[assignment]
            if stripped_text in ["全部", "全選"]:
                created_jobs: list[str] = []
                for opt in options:
                    target_loc = opt.get("search_loc") or opt.get("name")
                    if target_loc:
                        try:
                            job_id = await self.prewarm_repo.create_job(
                                area_name=target_loc, target_count=20, chat_id=chat_id
                            )
                            created_jobs.append(f"• **{opt.get('name', target_loc)}** (`{target_loc}`): `{job_id}`")
                        except Exception as e:
                            logger.error(f"Failed to create batch prewarm job for '{target_loc}': {e}")
                await self.session_repo.update_working_memory(chat_id=chat_id, clear_pending_prewarm=True)
                if created_jobs:
                    self._dispatch_background_prewarm(count=len(created_jobs))
                job_list_str = "\n".join(created_jobs)
                pref = pending_prewarm.get("pref", "")
                reply_msg = (
                    f"♨️ **已成功將「{pref}」全數 {len(created_jobs)} 個商圈排入 Neon 預熱佇列！**\n\n"
                    f"{job_list_str}\n\n"
                    f"🎯 **目標數量**：每商圈 20 家高評價餐廳\n"
                    f"🔄 **狀態**：`pending`（等待背景 Worker 快取）\n"
                    f"🔔 **推播提醒**：預熱完成時，PickForMe 將自動在此發訊通知您！"
                )
                await self._send_telegram_message(chat_id, reply_msg)
                return

            if stripped_text.isdigit():
                idx = int(stripped_text) - 1
                if 0 <= idx < len(options):
                    opt = options[idx]
                    target_loc = opt.get("search_loc") or opt.get("name")
                    if target_loc:
                        try:
                            job_id = await self.prewarm_repo.create_job(
                                area_name=target_loc, target_count=20, chat_id=chat_id
                            )
                            self._dispatch_background_prewarm(count=1)
                            reply_msg = (
                                f"♨️ **已成功將「{opt.get('name', target_loc)}」排入 Neon 預熱佇列！**\n\n"
                                f"🆔 **任務 ID**：`{job_id}`\n"
                                f"🎯 **目標數量**：20 家高評價餐廳\n"
                                f"🔄 **狀態**：`pending`（等待背景 Worker 快取）\n"
                                f"🔔 **推播提醒**：預熱完成時，PickForMe 將自動在此發訊通知您！\n\n"
                                f"💡 背景 Worker 將自動為您鎖定該地標，預先抓取 Google Places 與 Tabelog 評分並存入資料庫！"
                            )
                            await self._send_telegram_message(chat_id, reply_msg)
                        except Exception as e:
                            logger.error(f"Failed to create prewarm job for '{target_loc}': {e}")
                            await self._send_telegram_message(chat_id, f"❌ 預熱任務建立失敗：{e}")
                    await self.session_repo.update_working_memory(chat_id=chat_id, clear_pending_prewarm=True)
                    return

            # 非選單指令或非預期文字：自動清除待確認狀態，無縫轉交後續指令或 Agent 處理 (避免狀態卡死)
            await self.session_repo.update_working_memory(chat_id=chat_id, clear_pending_prewarm=True)

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
            primary = settings.LLM_PRIMARY_PROVIDER.upper()

            status_text = (
                "📊 **PickForMe 系統健康狀態總覽**\n\n"
                "🗄️ **資料庫**：🟢 正常 (Neon Cloud Postgres)\n"
                f"🧠 **主力推論引擎**：`{primary}`\n"
                f"⚡ **Groq API**：{groq_status}\n"
                f"🌟 **Gemini API**：{gemini_status}\n"
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

        if text.startswith("/prewarm") or text.startswith("預熱"):
            area = ""
            if text.startswith("/prewarm"):
                area = text[len("/prewarm") :].strip()
            elif text.startswith("預熱"):
                area = text[len("預熱") :].strip()

            if area:
                from src.agent.stages.intent import is_prefecture_level_area

                is_pref, pref_name, pref_options = is_prefecture_level_area(area)
                if is_pref and pref_name and pref_options:
                    await self.session_repo.update_working_memory(
                        chat_id=chat_id,
                        pending_prewarm_options={"pref": pref_name, "options": pref_options},
                    )
                    lines = [
                        f"♨️ **{pref_name}幅員遼闊，建議先鎖定特定精準商圈進行預熱快取！**\n",
                        "請選擇您欲預熱的目標商圈：",
                    ]
                    for idx, opt in enumerate(pref_options, 1):
                        lines.append(f"{idx}️⃣ **{opt['name']}**（{opt['desc']}）")
                    lines.append(
                        "\n💡 請直接回覆「1」、「2」、「3」鎖定商圈，或回覆「全部」一次預熱所有商圈；若想放棄請回覆「取消」！"
                    )
                    await self._send_telegram_message(chat_id, "\n".join(lines))
                    return

                try:
                    job_id = await self.prewarm_repo.create_job(area_name=area, target_count=20, chat_id=chat_id)
                    self._dispatch_background_prewarm(count=1)
                    reply_msg = (
                        f"♨️ **已成功將「{area}」排入 Neon 預熱佇列！**\n\n"
                        f"🆔 **任務 ID**：`{job_id}`\n"
                        f"🎯 **目標數量**：20 家高評價餐廳\n"
                        f"🔄 **狀態**：`pending`（等待背景 Worker 快取）\n"
                        f"🔔 **推播提醒**：預熱完成時，PickForMe 將自動在此發訊通知您！\n\n"
                        f"💡 背景 Worker 將自動為您鎖定該地標，預先抓取 Google Places 與 Tabelog 評分並存入資料庫！"
                    )
                    await self._send_telegram_message(chat_id, reply_msg)
                except Exception as e:
                    logger.error(f"Failed to create prewarm job for '{area}': {e}")
                    await self._send_telegram_message(chat_id, f"❌ 預熱任務建立失敗：{e}")
                return

            prewarm_msg = (
                "♨️ **PickForMe 精準地標與商圈美食快取預熱**\n\n"
                "📍 **預熱概念**：日本旅遊以「熱門車站/觀光地標」為核心，預熱可將熱門地標 800m 內的 Tabelog 頂級評分餐廳提前寫入 Neon DB 快取，達成秒答體驗！\n\n"
                "🎯 **推薦預熱地標範例**：\n"
                "• 關東：`橫濱中華街`、`新宿歌舞伎町`、`新宿御苑`\n"
                "• 關西：`心齋橋`、`難波`、`京都河原町`\n"
                "• 九州：`博多站`、`天神`、`由布院`\n\n"
                "💡 **發動指令**：在對話中直接輸入 `預熱 新宿御苑` 或 `/prewarm 新宿御苑`，Agent 即會啟動該地標的精準預熱！"
            )
            await self._send_telegram_message(chat_id, prewarm_msg)
            return

        if text.startswith("/archive") or text.startswith("歸檔"):
            from pathlib import Path

            from src.services.cold_storage import ColdStorageArchiver

            status_msg_id = await self._send_telegram_message(
                chat_id, "📦 **正在檢測雙重水位線（筆數 / DB 容量）並將超出配額之歷史 Trace 歸檔至 Parquet 冷層...**"
            )
            archiver = ColdStorageArchiver()
            res = await archiver.auto_archive_by_watermark(delete_after_archive=True)
            if res.get("status") == "skipped":
                reply = (
                    f"ℹ️ **目前水位安全，無需歸檔！**\n\n"
                    f"• **熱層總筆數**：`{res.get('total_runs', 0)}` / `{res.get('max_hot_runs', 500)}` 筆\n"
                    f"• **Neon DB 容量**：`{res.get('db_size_mb', 0.0)}` / `{res.get('max_db_size_mb', 400.0)}` MB\n\n"
                    f"💡 系統將於每次對話後自動背景巡檢，超出門檻時自動把多出的最舊紀錄轉為 Parquet。"
                )
            else:
                files_str = "\n".join([f"• `{Path(p).name}`" for p in res.get("parquet_files", [])])
                dest = res.get("destination")
                hot_deleted = res.get("hot_deleted", False)
                if hot_deleted:
                    cleanup_str = "🧹 **熱層清理**：已自 Neon DB 釋出舊紀錄，守護 1GB 免費額度！"
                elif res.get("delete_skipped_reason") == "gcs_upload_failed_on_stateless_cloud_run":
                    cleanup_str = (
                        "⚠️ **安全防刪鎖已觸發**：因 GCS 尚未就緒且目前位於 Cloud Run 無狀態容器，"
                        "為防止容器縮容遺失檔案，**已自動攔截 Neon DB 刪除動作**（舊資料仍安全保留於 Neon DB）！"
                    )
                else:
                    cleanup_str = "ℹ️ **熱層保留**：未刪除 Neon DB 舊紀錄。"

                reply = (
                    f"🎉 **冷儲存歸檔作業完成！**\n\n"
                    f"📊 **歸檔總量**：`{res.get('runs_archived', 0)}` 筆 Runs / `{res.get('steps_archived', 0)}` 筆 Steps\n"
                    f"🗄️ **儲存目標**：`{dest}` (Apache Parquet Snappy)\n"
                    f"📁 **產生檔案**：\n{files_str}\n\n"
                    f"{cleanup_str}"
                )
            if status_msg_id:
                await self._edit_telegram_message(chat_id, status_msg_id, reply)
            else:
                await self._send_telegram_message(chat_id, reply)
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

        # 執行 4 階段 FSM Pipeline (支援階梯搜尋、平行對齊、多模型容錯與即時進度推播)
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

        # 背景非阻塞巡檢雙重水位線（筆數 > 500 或 DB 容量 > 400MB 時自動將多出的舊資料歸檔）
        asyncio.create_task(self._background_check_and_archive())

    async def _background_check_and_archive(self) -> None:
        """背景巡檢冷熱儲存雙重水位線，不阻塞使用者對話回應"""
        try:
            from src.services.cold_storage import ColdStorageArchiver

            archiver = ColdStorageArchiver()
            res = await archiver.auto_archive_by_watermark(delete_after_archive=True)
            if res.get("status") == "success":
                logger.info(
                    f"Auto-archive watermark triggered ({res.get('trigger_reason')}): "
                    f"archived {res.get('runs_archived')} oldest runs to {res.get('destination')}."
                )
        except Exception as e:
            logger.debug(f"Background watermark check skipped or failed: {e}")

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
        if not text or not text.strip():
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
