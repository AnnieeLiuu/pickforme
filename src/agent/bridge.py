import logging
import sys
from pathlib import Path
from typing import Optional

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.config import settings

logger = logging.getLogger(__name__)


class IDEAgentBridge:
    """IDE Agent 橋接器 (Option B): 將 Telegram 診斷需求轉發給本地開發環境的 Agent 大腦"""

    def __init__(self, bridge_url: Optional[str] = None):
        url: str = bridge_url or settings.IDE_AGENT_BRIDGE_URL or "http://127.0.0.1:8000/agent/diagnose"
        self.bridge_url: str = url

    async def is_bridge_available(self) -> bool:
        """檢查本地 IDE Agent Bridge 服務是否在線上"""
        try:
            health_url = self.bridge_url.rsplit("/", 1)[0] + "/health"
            async with httpx.AsyncClient(timeout=1.5) as client:
                resp = await client.get(health_url)
                return resp.status_code == 200
        except Exception:
            return False

    async def forward_diagnosis_request(self, user_query: str) -> Optional[str]:
        """將 Telegram 輸入的排查需求轉發給本地 IDE Agent 大腦"""
        if not await self.is_bridge_available():
            logger.info("IDE Agent Bridge 不在線上，降級切換至內部 DevOpsSubagent。")
            return None

        try:
            async with httpx.AsyncClient(timeout=25.0) as client:
                resp = await client.post(
                    self.bridge_url,
                    json={"query": user_query},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    report = data.get("report")
                    if isinstance(report, str) and report:
                        logger.info("成功取得 IDE Agent Bridge 的專業診斷報告。")
                        return report
        except Exception as e:
            logger.warning(f"呼叫 IDE Agent Bridge 失敗: {e}，切換至內部模組。")

        return None

    async def forward_entity_verify_request(
        self, google_store: dict, tabelog_candidate: dict
    ) -> Optional[dict]:
        """將 Prewarm 比對需求轉發給本地 IDE Agent 大腦"""
        if not await self.is_bridge_available():
            return None

        try:
            url = self.bridge_url.rsplit("/", 1)[0] + "/verify-entity"
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    url,
                    json={
                        "google_store": google_store,
                        "tabelog_candidate": tabelog_candidate,
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("status") == "ok" and isinstance(data.get("result"), dict):
                        logger.info("成功由 IDE Agent Bridge 完成實體比對。")
                        return dict(data["result"])
        except Exception as e:
            logger.warning(f"IDE Agent 實體比對轉發失敗: {e}")

        return None

    async def forward_agent_request(
        self,
        user_message: str,
        chat_id: Optional[int] = None,
        user_location: Optional[tuple[float, float]] = None,
    ) -> Optional[str]:
        """將 Telegram 一般對話與搜尋需求轉發給本地 IDE Agent 大腦"""
        if not await self.is_bridge_available():
            return None

        try:
            url = self.bridge_url.rsplit("/", 1)[0] + "/run-agent"
            async with httpx.AsyncClient(timeout=25.0) as client:
                resp = await client.post(
                    url,
                    json={
                        "user_message": user_message,
                        "chat_id": chat_id,
                        "user_location": user_location,
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("status") == "ok" and data.get("response"):
                        logger.info("成功由 IDE Agent Bridge 完成對話處理。")
                        return str(data["response"])
        except Exception as e:
            logger.warning(f"IDE Agent 對話轉發失敗: {e}")

        return None
