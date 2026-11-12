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

    def _parse_raw_recommended_json(self, raw: Any) -> Any:
        """安全解析 last_recommended_ids 欄位 (相容 None, list, JSON str, dict)"""
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return None
        return raw

    async def get_last_recommended_stores(self, chat_id: int) -> list[dict[str, Any]]:
        """取得當前最後一輪推薦的店家清單 (供 follow_up 目標 1, 2, 3 追問使用)"""
        session = await self.get_session(chat_id)
        if not session or not isinstance(session, dict):
            return []
        raw = self._parse_raw_recommended_json(session.get("last_recommended_ids"))
        if not raw:
            return []
        if isinstance(raw, list):
            return raw
        if isinstance(raw, dict):
            curr = raw.get("current")
            if isinstance(curr, list):
                return curr
        return []

    async def get_seen_place_ids(self, chat_id: int) -> set[str]:
        """取得該使用者對話累積看過的所有 google_place_id"""
        session = await self.get_session(chat_id)
        if not session or not isinstance(session, dict):
            return set()
        raw = self._parse_raw_recommended_json(session.get("last_recommended_ids"))
        if not raw:
            return set()

        seen: set[str] = set()
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict) and item.get("google_place_id"):
                    seen.add(str(item["google_place_id"]))
        elif isinstance(raw, dict):
            acc = raw.get("accumulated_seen_ids")
            if isinstance(acc, list):
                seen.update(str(x) for x in acc)
            curr = raw.get("current")
            if isinstance(curr, list):
                for item in curr:
                    if isinstance(item, dict) and item.get("google_place_id"):
                        seen.add(str(item["google_place_id"]))
        return seen

    async def get_working_memory(self, chat_id: int) -> dict[str, Any]:
        """取得 Working Memory (上一輪 last_cuisine、last_location 與選填 suggested_cuisines)，向下相容舊版格式"""
        default_wm: dict[str, Any] = {"last_cuisine": None, "last_location": None}
        try:
            session = await self.get_session(chat_id)
        except Exception:
            return default_wm
        if not session or not isinstance(session, dict):
            return default_wm
        raw = self._parse_raw_recommended_json(session.get("last_recommended_ids"))
        if isinstance(raw, dict):
            wm = raw.get("working_memory")
            if isinstance(wm, dict):
                return {
                    **wm,
                    "last_cuisine": wm.get("last_cuisine"),
                    "last_location": wm.get("last_location"),
                }
        return default_wm

    async def update_working_memory(
        self,
        chat_id: int,
        last_location: Optional[str] = None,
        last_cuisine: Optional[str] = None,
        suggested_cuisines: Optional[list[str]] = None,
        last_explore_category: Optional[str] = None,
    ) -> dict[str, Any]:
        """單獨更新 Working Memory (如 explore_cuisine 導覽後保存 last_location 與 suggested_cuisines)"""
        current_stores: list[dict[str, Any]] = []
        accumulated_ids: list[str] = []
        existing_wm = await self.get_working_memory(chat_id)
        existing_prefs = await self.get_long_term_preferences(chat_id)

        try:
            session = await self.get_session(chat_id)
            if isinstance(session, dict):
                raw = self._parse_raw_recommended_json(session.get("last_recommended_ids"))
                if isinstance(raw, list):
                    current_stores = raw
                    accumulated_ids = [
                        str(r["google_place_id"]) for r in raw if isinstance(r, dict) and r.get("google_place_id")
                    ]
                elif isinstance(raw, dict):
                    if isinstance(raw.get("current"), list):
                        current_stores = raw["current"]
                    if isinstance(raw.get("accumulated_seen_ids"), list):
                        accumulated_ids = [str(x) for x in raw["accumulated_seen_ids"]]
        except Exception as e:
            logger.warning(f"Failed to read existing session in update_working_memory: {e}")

        updated_wm: dict[str, Any] = {
            **existing_wm,
            "last_location": last_location if last_location is not None else existing_wm.get("last_location"),
            "last_cuisine": last_cuisine if last_cuisine is not None else existing_wm.get("last_cuisine"),
        }
        if suggested_cuisines is not None:
            updated_wm["suggested_cuisines"] = suggested_cuisines
        if last_explore_category is not None:
            updated_wm["last_explore_category"] = last_explore_category

        data_to_store = {
            "current": current_stores,
            "accumulated_seen_ids": accumulated_ids,
            "working_memory": updated_wm,
            "long_term_preferences": existing_prefs,
        }

        pool = await get_db_pool()
        sql = """
            INSERT INTO chat_sessions (chat_id, last_recommended_ids, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (chat_id) DO UPDATE SET
                last_recommended_ids = EXCLUDED.last_recommended_ids,
                updated_at = NOW();
        """
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (chat_id, json.dumps(data_to_store, ensure_ascii=False)))
                await conn.commit()

        return updated_wm

    async def get_long_term_preferences(self, chat_id: int) -> dict[str, list[str]]:
        """取得 Long-Term Preferences (飲食禁忌與支付偏好)，向下相容舊版格式"""
        default_prefs: dict[str, list[str]] = {
            "dietary_restrictions": [],
            "payment_preferences": [],
        }
        try:
            session = await self.get_session(chat_id)
        except Exception:
            return default_prefs
        if not session or not isinstance(session, dict):
            return default_prefs
        raw = self._parse_raw_recommended_json(session.get("last_recommended_ids"))
        if isinstance(raw, dict):
            prefs = raw.get("long_term_preferences")
            if isinstance(prefs, dict):
                dietary = prefs.get("dietary_restrictions")
                payment = prefs.get("payment_preferences")
                return {
                    "dietary_restrictions": [str(x) for x in dietary] if isinstance(dietary, list) else [],
                    "payment_preferences": [str(x) for x in payment] if isinstance(payment, list) else [],
                }
        return default_prefs

    async def update_long_term_preferences(
        self,
        chat_id: int,
        dietary_restrictions: Optional[list[str]] = None,
        payment_preferences: Optional[list[str]] = None,
    ) -> dict[str, list[str]]:
        """合併去重並持久化使用者的長期飲食禁忌與支付偏好"""
        existing_prefs = await self.get_long_term_preferences(chat_id)
        dietary_list = list(existing_prefs.get("dietary_restrictions", []))
        payment_list = list(existing_prefs.get("payment_preferences", []))

        if dietary_restrictions:
            for item in dietary_restrictions:
                if item and item not in dietary_list:
                    dietary_list.append(str(item))
        if payment_preferences:
            for item in payment_preferences:
                if item and item not in payment_list:
                    payment_list.append(str(item))

        updated_prefs: dict[str, list[str]] = {
            "dietary_restrictions": dietary_list,
            "payment_preferences": payment_list,
        }

        current_stores: list[dict[str, Any]] = []
        accumulated_ids: list[str] = []
        wm: dict[str, Any] = {"last_cuisine": None, "last_location": None}

        try:
            session = await self.get_session(chat_id)
            if isinstance(session, dict):
                raw = self._parse_raw_recommended_json(session.get("last_recommended_ids"))
                if isinstance(raw, list):
                    current_stores = raw
                    accumulated_ids = [
                        str(r["google_place_id"]) for r in raw if isinstance(r, dict) and r.get("google_place_id")
                    ]
                elif isinstance(raw, dict):
                    if isinstance(raw.get("current"), list):
                        current_stores = raw["current"]
                    if isinstance(raw.get("accumulated_seen_ids"), list):
                        accumulated_ids = [str(x) for x in raw["accumulated_seen_ids"]]
                    if isinstance(raw.get("working_memory"), dict):
                        wm = {
                            **raw["working_memory"],
                            "last_cuisine": raw["working_memory"].get("last_cuisine"),
                            "last_location": raw["working_memory"].get("last_location"),
                        }
        except Exception as e:
            logger.warning(f"Failed to read existing session in update_long_term_preferences: {e}")

        data_to_store = {
            "current": current_stores,
            "accumulated_seen_ids": accumulated_ids,
            "working_memory": wm,
            "long_term_preferences": updated_prefs,
        }

        pool = await get_db_pool()
        sql = """
            INSERT INTO chat_sessions (chat_id, last_recommended_ids, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (chat_id) DO UPDATE SET
                last_recommended_ids = EXCLUDED.last_recommended_ids,
                updated_at = NOW();
        """
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (chat_id, json.dumps(data_to_store, ensure_ascii=False)))
                await conn.commit()

        return updated_prefs

    async def update_recommended_ids(
        self,
        chat_id: int,
        recommended_ids: list[dict[str, Any]],
        accumulate: bool = True,
        last_cuisine: Optional[str] = None,
        last_location: Optional[str] = None,
    ) -> None:
        """更新推薦店家清單，預設累積更新歷史看過的店家 ID，同時保留長期偏好與更新 Working Memory"""
        pool = await get_db_pool()
        accumulated_seen_ids: list[str] = []

        if accumulate:
            existing_seen = await self.get_seen_place_ids(chat_id)
            if not isinstance(existing_seen, set):
                existing_seen = set(existing_seen) if isinstance(existing_seen, (list, tuple)) else set()
            for r in recommended_ids:
                pid = r.get("google_place_id")
                if pid:
                    existing_seen.add(str(pid))
            accumulated_seen_ids = list(existing_seen)
        else:
            accumulated_seen_ids = [str(r["google_place_id"]) for r in recommended_ids if r.get("google_place_id")]

        existing_wm: dict[str, Any] = {"last_cuisine": None, "last_location": None}
        try:
            wm_res = await self.get_working_memory(chat_id)
            if isinstance(wm_res, dict):
                existing_wm = wm_res
        except Exception:
            pass

        existing_prefs: dict[str, list[str]] = {
            "dietary_restrictions": [],
            "payment_preferences": [],
        }
        try:
            prefs_res = await self.get_long_term_preferences(chat_id)
            if isinstance(prefs_res, dict):
                existing_prefs = {
                    "dietary_restrictions": prefs_res.get("dietary_restrictions", [])
                    if isinstance(prefs_res.get("dietary_restrictions"), list)
                    else [],
                    "payment_preferences": prefs_res.get("payment_preferences", [])
                    if isinstance(prefs_res.get("payment_preferences"), list)
                    else [],
                }
        except Exception:
            pass

        updated_wm = {
            **existing_wm,
            "last_cuisine": last_cuisine if last_cuisine is not None else existing_wm.get("last_cuisine"),
            "last_location": last_location if last_location is not None else existing_wm.get("last_location"),
        }
        # 一般搜尋完成即代表「特色導覽選單」已消費完畢：清除 suggested_cuisines，
        # 避免數輪後單獨回覆「1」被映射回過期的導覽品項 (stale menu)
        updated_wm.pop("suggested_cuisines", None)

        data_to_store = {
            "current": recommended_ids,
            "accumulated_seen_ids": accumulated_seen_ids,
            "working_memory": updated_wm,
            "long_term_preferences": existing_prefs,
        }

        sql = """
            INSERT INTO chat_sessions (chat_id, last_recommended_ids, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (chat_id) DO UPDATE SET
                last_recommended_ids = EXCLUDED.last_recommended_ids,
                updated_at = NOW();
        """
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (chat_id, json.dumps(data_to_store, ensure_ascii=False)))
                await conn.commit()

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
