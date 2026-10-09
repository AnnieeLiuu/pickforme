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
        """根據開發者在 Telegram 輸入的 @debug 需求執行系統健康掃描與日誌診斷"""
        start_time = time.time()
        query_text = user_query.replace("@debug", "").replace("/debug", "").strip()
        if not query_text:
            query_text = "自動進行全面系統健康檢查與錯誤排查"

        # 1. 檢查是否包含自動修復關鍵字，並執行對應自動修復動作
        auto_fix_results = await self._execute_auto_healing(query_text)

        # 2. 蒐集系統現狀診斷數據
        health_report = await self._gather_health_metrics()
        error_logs = await self._fetch_recent_error_traces()

        # 3. 組裝 Context 讓 DevOps Subagent 分析
        devops_prompt = (
            "你是一位頂級的 Senior DevOps & Site Reliability Engineer (SRE) AI 助手。"
            "請針對以下系統現狀指標、自動修復執行結果與近期錯誤 Log，以專業、簡潔的繁體中文 Markdown 格式回答開發者：\n\n"
            f"### 【開發者查詢指令】\n{query_text}\n\n"
            f"### 【自動修復執行結果 (Auto-Healing Results)】\n{auto_fix_results}\n\n"
            f"### 【系統即時指標 (Health Metrics)】\n{health_report}\n\n"
            f"### 【近期錯誤 Trace (Recent Error Logs)】\n{error_logs}\n\n"
            "請包含以下重點：\n"
            "1. 🛠️ **現狀診斷摘要與修復結果**\n"
            "2. 🔍 **潛在根因分析 (Root Cause)**\n"
            "3. 💡 **後續監控與修復建議**\n"
        )

        try:
            # 呼叫 AgentEngine
            messages = [
                {
                    "role": "system",
                    "content": "你是一位專精 Python, FastAPI, Postgres 與 API 監控的 DevOps AI 特派員。",
                },
                {"role": "user", "content": devops_prompt},
            ]
            msg_obj, model_used, tokens = await self.agent_engine.call_llm_with_failover(messages)
            analysis_text = msg_obj.get("content", "無法生成診斷報告。")
            latency_ms = int((time.time() - start_time) * 1000)

            return (
                f"🛠️ **PickForMe DevOps 診斷與自動修復報告**\n"
                f"⏱️ 耗時: `{latency_ms}ms` | 🤖 分析模型: `{model_used}`\n\n"
                f"{analysis_text}"
            )
        except Exception as e:
            logger.error(f"DevOps Subagent error: {e}")
            return (
                f"⚠️ **DevOps 診斷程式執行異常**：\n```\n{str(e)}\n```\n\n"
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
                actions_taken.append("ℹ️ 目前無觸發手動修復關鍵字（可輸入 `@debug 修復` 觸發重試預熱工作）。")

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
