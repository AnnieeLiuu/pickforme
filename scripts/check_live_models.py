# -*- coding: utf-8 -*-
"""Live Cloud LLM Model Availability Smoke Check.

用法:
    python scripts/check_live_models.py

驗證 `src/agent/llm_client.py` 中設定的 Groq 與 Gemini 模型是否仍存在於遠端官方服務清單，
並可即時回傳 200 OK，避免模型默默下線導致系統退化至本機 Ollama。
"""

import asyncio
import sys
from pathlib import Path

import httpx

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.agent.llm_client import get_active_gemini_model, get_active_groq_models  # noqa: E402
from src.config import settings  # noqa: E402


async def check_groq_models() -> bool:
    """檢查 Groq /v1/models 清單並實測主力模型推論。"""
    if not settings.GROQ_API_KEY:
        print("⚠️  GROQ_API_KEY 未設定，略過 Groq 檢查")
        return True

    target_models = get_active_groq_models()
    headers = {"Authorization": f"Bearer {settings.GROQ_API_KEY}"}
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get("https://api.groq.com/openai/v1/models", headers=headers)
        if resp.status_code != 200:
            print(f"❌ Groq /v1/models 查詢失敗: HTTP {resp.status_code} {resp.text[:200]}")
            return False

        active_ids = {m["id"] for m in resp.json().get("data", []) if "id" in m}
        missing = [m for m in target_models if m not in active_ids]
        if missing:
            print(f"❌ Groq 模型已下線或不存在: {missing}")
            print(f"   目前線上可用模型: {sorted(active_ids)}")
            return False

        print(f"✅ Groq /v1/models 全數存活: {list(target_models)}")

        # 對主力模型進行 1-token 實測
        primary = target_models[0]
        ping = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers=headers,
            json={"model": primary, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5},
        )
        if ping.status_code == 200:
            print(f"✅ Groq 主力 ({primary}) 實測推論成功 (HTTP 200)")
            return True
        print(f"❌ Groq 主力 ({primary}) 實測推論失敗: HTTP {ping.status_code} {ping.text[:200]}")
        return False


async def check_gemini_model() -> bool:
    """實測 Gemini 主力模型。"""
    if not settings.GEMINI_API_KEY:
        print("⚠️  GEMINI_API_KEY 未設定，略過 Gemini 檢查")
        return True

    gemini_model = get_active_gemini_model()
    headers = {"Authorization": f"Bearer {settings.GEMINI_API_KEY}", "Content-Type": "application/json"}
    url = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.post(
                url,
                headers=headers,
                json={"model": gemini_model, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5},
            )
            if resp.status_code == 200:
                print(f"✅ Gemini 主力 ({gemini_model}) 實測推論成功 (HTTP 200)")
                return True
            print(f"❌ Gemini ({gemini_model}) 回傳異常: HTTP {resp.status_code} {resp.text[:200]}")
            return False
        except httpx.TimeoutException:
            print(f"⚠️  Gemini ({gemini_model}) 連線逾時 (Timeout)，請檢查本地網路或代理設定")
            return False


async def main() -> int:
    groq_ok = await check_groq_models()
    gemini_ok = await check_gemini_model()
    return 0 if (groq_ok and gemini_ok) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
