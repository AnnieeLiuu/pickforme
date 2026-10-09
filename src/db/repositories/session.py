import json
import logging
from typing import Any, Optional

from src.db.connection import get_db_pool

logger = logging.getLogger(__name__)


class SessionRepository:
    async def get_session(self, chat_id: int) -> Optional[dict[str, Any]]:
        """取得 Telegram Chat Session 資料"""
        pool = await get_db_pool()
        sql = """
            SELECT chat_id, last_location_lat, last_location_lng, last_recommended_ids, conversation_history, updated_at
            FROM chat_sessions
            WHERE chat_id = %s;
        """
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (chat_id,))
                row = await cur.fetchone()
                return dict(row) if row else None

    async def update_location(self, chat_id: int, lat: float, lng: float) -> None:
        """更新使用者最後分享的位置經緯度"""
        pool = await get_db_pool()
        sql = """
            INSERT INTO chat_sessions (chat_id, last_location_lat, last_location_lng, updated_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (chat_id) DO UPDATE SET
                last_location_lat = EXCLUDED.last_location_lat,
                last_location_lng = EXCLUDED.last_location_lng,
                updated_at = NOW();
        """
        for attempt in range(2):
            try:
                async with pool.connection() as conn:
                    async with conn.cursor() as cur:
                        await cur.execute(sql, (chat_id, lat, lng))
                        await conn.commit()
                return
            except Exception as e:
                logger.warning(f"update_location attempt {attempt + 1} failed: {e}")
                if attempt == 1:
                    raise

    async def add_conversation_turn(
        self, chat_id: int, user_message: str, assistant_response: str, max_turns: int = 3
    ) -> None:
        """追加一輪對話 (User + Assistant)，並最多保留最近 max_turns 輪 (2 * max_turns 則訊息)"""
        session = await self.get_session(chat_id)
        raw_history = session.get("conversation_history") if session else None

        history_list: list[dict[str, str]] = []
        if isinstance(raw_history, list):
            history_list = raw_history
        elif isinstance(raw_history, str):
            try:
                parsed = json.loads(raw_history)
                if isinstance(parsed, list):
                    history_list = parsed
            except json.JSONDecodeError:
                pass

        # 追加新對話輪
        history_list.append({"role": "user", "content": user_message})
        history_list.append({"role": "assistant", "content": assistant_response})

        # 限制只留最近 max_turns 輪 (2 * max_turns 個訊息)
        max_messages = max_turns * 2
        if len(history_list) > max_messages:
            history_list = history_list[-max_messages:]

        pool = await get_db_pool()
        sql = """
            INSERT INTO chat_sessions (chat_id, conversation_history, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (chat_id) DO UPDATE SET
                conversation_history = EXCLUDED.conversation_history,
                updated_at = NOW();
        """
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (chat_id, json.dumps(history_list, ensure_ascii=False)))
                await conn.commit()

    async def get_recent_history(self, chat_id: int, max_turns: int = 3) -> list[dict[str, str]]:
        """取得最近 max_turns 輪的對話歷史紀錄"""
        session = await self.get_session(chat_id)
        if not session:
            return []

        raw_history = session.get("conversation_history")
        if not raw_history:
            return []

        if isinstance(raw_history, str):
            try:
                raw_history = json.loads(raw_history)
            except json.JSONDecodeError:
                return []

        if isinstance(raw_history, list):
            max_messages = max_turns * 2
            return raw_history[-max_messages:]
        return []

    async def get_latest_agent_run(self, chat_id: int) -> Optional[dict[str, Any]]:
        """取得最近一筆 Agent 執行 Trace 紀錄"""
        pool = await get_db_pool()
        sql = """
            SELECT run_id, chat_id, user_query, model_used, total_tokens, latency_ms, status, created_at
            FROM agent_runs
            WHERE chat_id = %s
            ORDER BY created_at DESC
            LIMIT 1;
        """
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (chat_id,))
                row = await cur.fetchone()
                return dict(row) if row else None
