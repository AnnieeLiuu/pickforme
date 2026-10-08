import logging
from typing import Any, Optional
from uuid import UUID, uuid4

from src.db.connection import get_db_pool

logger = logging.getLogger(__name__)


class PrewarmRepository:
    async def create_job(self, area_name: str, target_count: int = 20) -> UUID:
        """新增一筆預熱區域任務"""
        job_id = uuid4()
        pool = await get_db_pool()
        sql = """
            INSERT INTO prewarm_jobs (id, area_name, target_count, status, created_at, updated_at)
            VALUES (%s, %s, %s, 'pending', NOW(), NOW())
            RETURNING id;
        """
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (job_id, area_name, target_count))
                await conn.commit()
        return job_id

    async def get_next_pending_job(self) -> Optional[dict[str, Any]]:
        """以 FOR UPDATE SKIP LOCKED 取出下一筆 pending 任務並切換為 processing"""
        pool = await get_db_pool()
        find_and_lock_sql = """
            SELECT id, area_name, target_count, status, created_at, updated_at
            FROM prewarm_jobs
            WHERE status = 'pending'
            ORDER BY created_at ASC
            FOR UPDATE SKIP LOCKED
            LIMIT 1;
        """
        update_sql = """
            UPDATE prewarm_jobs
            SET status = 'processing', updated_at = NOW()
            WHERE id = %s;
        """
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(find_and_lock_sql)
                row = await cur.fetchone()
                if not row:
                    return None

                job_dict = dict(row)
                job_id = job_dict["id"]
                await cur.execute(update_sql, (job_id,))
                await conn.commit()

                job_dict["status"] = "processing"
                return job_dict

    async def update_job_status(self, job_id: UUID, status: str) -> None:
        """更新預熱任務狀態 ('completed', 'failed', 'pending')"""
        pool = await get_db_pool()
        sql = """
            UPDATE prewarm_jobs
            SET status = %s, updated_at = NOW()
            WHERE id = %s;
        """
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (status, job_id))
                await conn.commit()
