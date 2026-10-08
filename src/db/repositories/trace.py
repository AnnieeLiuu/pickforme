import json
import logging
from typing import Any, Optional
from uuid import UUID, uuid4

from src.db.connection import get_db_pool

logger = logging.getLogger(__name__)


class TraceRepository:
    async def create_agent_run(self, chat_id: Optional[int], user_query: str) -> UUID:
        """建立新的 Agent Run 紀錄"""
        run_id = uuid4()
        pool = await get_db_pool()
        sql = """
            INSERT INTO agent_runs (run_id, chat_id, user_query, status)
            VALUES (%s, %s, %s, 'running');
        """
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (run_id, chat_id, user_query))
                await conn.commit()
        return run_id

    async def update_agent_run(
        self,
        run_id: UUID,
        status: str,
        model_used: Optional[str] = None,
        total_tokens: Optional[int] = None,
        latency_ms: Optional[int] = None,
    ) -> None:
        """更新 Agent Run 狀態與 Token / Latency"""
        pool = await get_db_pool()
        sql = """
            UPDATE agent_runs
            SET status = %s,
                model_used = COALESCE(%s, model_used),
                total_tokens = COALESCE(%s, total_tokens),
                latency_ms = COALESCE(%s, latency_ms)
            WHERE run_id = %s;
        """
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (status, model_used, total_tokens, latency_ms, run_id))
                await conn.commit()

    async def record_agent_step(
        self,
        run_id: UUID,
        step_number: int,
        action_type: str,
        tool_name: Optional[str] = None,
        tool_input: Optional[dict[str, Any]] = None,
        tool_output: Optional[dict[str, Any]] = None,
        latency_ms: Optional[int] = None,
        error_message: Optional[str] = None,
    ) -> UUID:
        """寫入 Agent 單一步驟 (step) 紀錄"""
        step_id = uuid4()
        pool = await get_db_pool()
        sql = """
            INSERT INTO agent_steps (
                step_id, run_id, step_number, action_type, tool_name, tool_input, tool_output, latency_ms, error_message
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);
        """
        tool_input_json = json.dumps(tool_input, ensure_ascii=False) if tool_input else None
        tool_output_json = json.dumps(tool_output, ensure_ascii=False) if tool_output else None

        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    sql,
                    (
                        step_id,
                        run_id,
                        step_number,
                        action_type,
                        tool_name,
                        tool_input_json,
                        tool_output_json,
                        latency_ms,
                        error_message,
                    ),
                )
                await conn.commit()
        return step_id
