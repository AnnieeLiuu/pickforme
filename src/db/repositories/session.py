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
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (chat_id, lat, lng))
                await conn.commit()

    async def save_session_recommendations(
        self, chat_id: int, recommended_ids: list[str], conversation_summary: Optional[str] = None
    ) -> None:
        """更新 Session 上次推薦的店家列表與對話摘要"""
        pool = await get_db_pool()
        sql = """
            INSERT INTO chat_sessions (chat_id, last_recommended_ids, conversation_history, updated_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (chat_id) DO UPDATE SET
                last_recommended_ids = EXCLUDED.last_recommended_ids,
                conversation_history = EXCLUDED.conversation_history,
                updated_at = NOW();
        """
        ids_json = json.dumps(recommended_ids)
        history_json = json.dumps({"summary": conversation_summary}) if conversation_summary else None

        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (chat_id, ids_json, history_json))
                await conn.commit()
