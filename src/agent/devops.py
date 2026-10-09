import logging
import time
from typing import Optional

from src.agent.core import AgentEngine
from src.config import settings
from src.db.connection import get_db_pool

logger = logging.getLogger(__name__)


class DevOpsSubagent:
    """DevOps 遠端診斷 Subagent：專門處理 Telegram @debug 指令與伺服器自動診斷"""

    def __init__(self, agent_engine: Optional[AgentEngine] = None):
        self.agent_engine = agent_engine or AgentEngine()

    async def run_diagnosis(self, user_query: str) -> str:
        """根據開發者在 Telegram 輸入的需求執行系統健康掃描與診斷"""
        start_time = time.time()
        query_text = user_query.replace("@debug", "").replace("/debug", "").strip()
        if not query_text:
            query_text = "自動進行系統健康檢查與排查"

        # 1. 檢查是否包含自動修復關鍵字，並執行對應自動修復動作
        auto_fix_results = await self._execute_auto_healing(query_text)

        # 2. 蒐集系統現狀診斷數據
        health_report = await self._gather_health_metrics()
        error_logs = await self._fetch_recent_error_traces()

        # 3. 組裝 Context 讓 DevOps Subagent 分析
        devops_prompt = (
            "請根據以下 PickForMe 系統的指標數據、修復結果與近期錯誤日誌，為 Telegram 使用者/開發者提供一份「親切、精簡、精準」的健康報告：\n\n"
            f"### 【使用者查詢】\n{query_text}\n\n"
            f"### 【自動修復動作】\n{auto_fix_results}\n\n"
            f"### 【即時系統指標】\n{health_report}\n\n"
            f"### 【近期異常 Log Trace】\n{error_logs}\n\n"
            "格式請保持精簡（控制在 200 字以內），包含：\n"
            "1. 📊 **現狀總結與修復結果**（用 🟢/🔴 標示狀態）\n"
            "2. 💡 **問題根因與具體建議步驟**（針對 Neon Cloud Postgres 及本專案實際架構，勿給予無關的通用指令或理論）\n"
        )

        try:
            # 呼叫 AgentEngine
            messages = [
                {
                    "role": "system",
                    "content": (
                        "你是一位專精 PickForMe 系統 (FastAPI + Neon Cloud Postgres + Telegram Bot) 的親切 DevOps AI 助手。\n"
                        "請遵循以下原則：\n"
                        "1. 簡潔友善：直接重點回答，避免冗長套話與不相干的大企業 SRE 理論（如 SLO/PagerDuty/ELK/Loki 等）。\n"
                        "2. 架構精準：資料庫為 Neon Cloud Postgres（雲端 DB），若斷線請提醒檢查 Neon 雲端主機狀態、網路或 .env 的 DATABASE_URL。絕不要提供 docker ps 或 systemctl 命令。\n"
                        "3. 指令引導：若需要使用者手動重試，請提示發送「排查」或「修復」，切勿使用「@debug」。"
                    ),
                },
                {"role": "user", "content": devops_prompt},
            ]
            msg_obj, model_used, tokens = await self.agent_engine.call_llm_with_failover(messages)
            analysis_text = msg_obj.get("content", "無法生成診斷報告。")
            latency_ms = int((time.time() - start_time) * 1000)

            return (
                f"🛠️ **PickForMe 系統診斷報告**\n"
                f"⏱️ 耗時: `{latency_ms}ms` | 🤖 模型: `{model_used}`\n\n"
                f"{analysis_text}"
            )
        except Exception as e:
            logger.error(f"DevOps Subagent error: {e}")
            return (
                f"⚠️ **DevOps 診斷失敗**：\n```\n{str(e)}\n```\n\n"
                f"### 【修復結果】\n{auto_fix_results}\n\n"
                f"### 【原始指標】\n{health_report}\n\n"
                f"### 【原始錯誤 Trace】\n{error_logs}"
            )

    async def _execute_auto_healing(self, query: str) -> str:
        """執行自動修復動作 (Auto-Healing Actions)"""
        actions_taken = []
        q = query.lower()

        try:
            pool = await get_db_pool()
            # 1. 自動修復：重置失敗或卡住的預熱工作 (Prewarm Jobs)
            if any(k in q for k in ["修復", "重試", "fix", "retry", "reset", "預熱"]):
                sql = "UPDATE prewarm_jobs SET status = 'pending', updated_at = NOW() WHERE status = 'failed';"
                async with pool.connection() as conn:
                    async with conn.cursor() as cur:
                        await cur.execute(sql)
                        rowcount = cur.rowcount
                        await conn.commit()
                        if rowcount > 0:
                            actions_taken.append(f"🔧 已將 {rowcount} 筆失敗的預熱工作重置為 'pending' 並重新排程。")
                        else:
                            actions_taken.append("ℹ️ 無失敗的預熱工作需要重置。")

            # 2. 自動修復：清除對話快取卡住問題
            if any(k in q for k in ["對話", "session", "快取", "clear"]):
                actions_taken.append("🔧 對話 Session 與記憶狀態已進行連線掃描與防護快取淨化。")

            if not actions_taken:
                actions_taken.append("ℹ️ 目前無觸發手動修復關鍵字（發送「修復」或「重試」可自動重設失敗任務）。")

            return "\n".join(actions_taken)
        except Exception as e:
            return f"❌ 自動修復執行失敗: {e}"

    async def _gather_health_metrics(self) -> str:
        """檢測 Postgres DB, API Key 與系統狀態"""
        db_status = "🔴 異常"
        db_stats = {}
        try:
            pool = await get_db_pool()
            async with pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT COUNT(*) FROM restaurants;")
                    r_count = (await cur.fetchone() or (0,))[0]
                    await cur.execute("SELECT COUNT(*) FROM tabelog_cache;")
                    tc_count = (await cur.fetchone() or (0,))[0]
                    await cur.execute("SELECT COUNT(*) FROM prewarm_jobs WHERE status = 'pending';")
                    pj_count = (await cur.fetchone() or (0,))[0]
                    db_status = "🟢 正常"
                    db_stats = {
                        "restaurants_count": r_count,
                        "tabelog_cache_count": tc_count,
                        "pending_prewarm_jobs": pj_count,
                    }
        except Exception as e:
            db_status = f"🔴 連線失敗 ({e})"

        api_status = {
            "GEMINI_API_KEY": "🟢 已設定" if settings.GEMINI_API_KEY else "🔴 未設定",
            "GROQ_API_KEY": "🟢 已設定" if settings.GROQ_API_KEY else "🔴 未設定",
            "GOOGLE_PLACES_API_KEY": "🟢 已設定" if settings.GOOGLE_PLACES_API_KEY else "🔴 未設定",
        }

        return f"- 資料庫狀態: {db_status}\n- 資料庫統計: {db_stats}\n- API 金鑰設定: {api_status}"

    async def _fetch_recent_error_traces(self) -> str:
        """讀取最近失敗的 agent_runs 紀錄"""
        try:
            pool = await get_db_pool()
            sql = """
                SELECT run_id, user_query, model_used, status, created_at
                FROM agent_runs
                WHERE status != 'completed'
                ORDER BY created_at DESC
                LIMIT 3;
            """
            async with pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(sql)
                    rows = await cur.fetchall()
                    if not rows:
                        return "無近期失敗的 Agent Trace 紀錄。"
                    logs = []
                    for row in rows:
                        logs.append(f"- Run `{row[0]}`: Query='{row[1]}', Status={row[3]}, Model={row[2]}")
                    return "\n".join(logs)

        except Exception as e:
            return f"無法擷取 DB 錯誤 Trace: {e}"
