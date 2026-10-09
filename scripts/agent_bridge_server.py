import asyncio
import logging
import sys
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agent.core import AgentEngine
from src.config import settings
from src.db.connection import get_db_pool

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="PickForMe IDE Agent Bridge Server")


class DiagnosisRequest(BaseModel):
    query: str


@app.get("/health")
@app.get("/agent/health")
async def health_check():
    return {"status": "ok", "agent": "Antigravity IDE Agent Bridge Active"}


@app.post("/agent/diagnose")
async def diagnose(req: DiagnosisRequest):
    start_time = time.time()
    logger.info(f"Received Telegram diagnosis request via IDE Bridge: '{req.query}'")

    # 1. 檢測 Neon DB
    db_status = "🟢 正常"
    db_info = ""
    try:
        pool = await get_db_pool()
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT COUNT(*) FROM restaurants;")
                r_count = (await cur.fetchone() or (0,))[0]
                await cur.execute("SELECT COUNT(*) FROM prewarm_jobs WHERE status = 'pending';")
                pj_count = (await cur.fetchone() or (0,))[0]
                db_info = f"已快取 {r_count} 間餐廳，待處理預熱任務 {pj_count} 筆"
    except Exception as e:
        db_status = "🔴 連線失敗"
        db_info = f"Neon DB 拒絕連線: {e}"

    # 2. 檢測環境變數
    keys_ok = bool(settings.GEMINI_API_KEY and settings.GROQ_API_KEY and settings.GOOGLE_PLACES_API_KEY)
    key_status = "🟢 全部正常" if keys_ok else "🔴 有金鑰未設定"

    # 3. 呼叫大腦生成極簡親切報告
    agent_engine = AgentEngine()
    prompt = (
        "你是 Antigravity IDE 駐點 Agent。Telegram 使用者向你請求系統排查。\n"
        f"需求：{req.query}\n"
        f"DB 現狀：{db_status} ({db_info})\n"
        f"API 金鑰：{key_status}\n\n"
        "請用超級親切、專業、精簡（100字以內）的卡片風格回覆 Telegram 使用者。"
        "切勿輸出任何 docker, kubectl, systemctl 等無關技術廢話。"
    )

    try:
        messages = [
            {"role": "system", "content": "你是與開發者對話的 IDE Agent，語言親切簡潔。"},
            {"role": "user", "content": prompt},
        ]
        msg_obj, model, _ = await agent_engine.call_llm_with_failover(messages)
        content = msg_obj.get("content", "系統運作正常。")
        latency = int((time.time() - start_time) * 1000)

        report = (
            "🤖 **[IDE Agent 遠端直連分析]**\n"
            f"⏱️ 耗時: `{latency}ms` | 🧠 運算模型: `{model}`\n\n"
            f"{content}"
        )
        return {"status": "ok", "report": report}
    except Exception as e:
        return {
            "status": "error",
            "report": f"🤖 **[IDE Agent 遠端分析報告]**\n\n"
            f"• 資料庫：{db_status} ({db_info})\n"
            f"• 金鑰：{key_status}\n"
            f"• 備註：{e}",
        }


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    logger.info("Starting IDE Agent Bridge Server on http://127.0.0.1:8000 ...")
    uvicorn.run(app, host="127.0.0.1", port=8000)
