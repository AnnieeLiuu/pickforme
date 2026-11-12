# -*- coding: utf-8 -*-
"""SpecialtyGuideRepository: 在地特色美食・飲品・伴手禮知識庫 Repository (Neon Postgres)

職責：
1. 提供 area_specialty_guides 讀取與寫回介面 (Read-Through + Write-Back)。
2. 支援 fail-open 彈性防護：若 DB 連線超時或表格未建立，自動記錄 warning 並回傳 None，絕不阻斷前端對話。
3. 支援別名 (aliases) JSONB 陣列檢索與防重覆寫回。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

from src.db.connection import get_db_pool

logger = logging.getLogger(__name__)


class SpecialtyGuideRepository:
    """管理 area_specialty_guides 資料庫讀寫"""

    _table_ensured: bool = False

    async def ensure_table(self) -> None:
        """懶加載確保資料表與索引存在 (每個 process 僅執行一次 DDL 檢查)"""
        if self._table_ensured:
            return
        sql = """
            CREATE TABLE IF NOT EXISTS area_specialty_guides (
                area_key VARCHAR(100) NOT NULL,
                category VARCHAR(20) NOT NULL DEFAULT 'food',
                area_name VARCHAR(100) NOT NULL,
                aliases JSONB DEFAULT '[]'::jsonb,
                intro TEXT NOT NULL,
                specialties JSONB NOT NULL,
                source_tier VARCHAR(20) DEFAULT 'grounding',
                grounding_snippets JSONB DEFAULT '[]'::jsonb,
                verified BOOLEAN DEFAULT FALSE,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                PRIMARY KEY (area_key, category)
            );
            CREATE INDEX IF NOT EXISTS idx_specialty_guides_category ON area_specialty_guides(category);
            CREATE INDEX IF NOT EXISTS idx_specialty_guides_aliases ON area_specialty_guides USING gin (aliases);
        """
        try:
            pool = await get_db_pool()
            async with pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(sql)
                    await conn.commit()
            SpecialtyGuideRepository._table_ensured = True
        except Exception as e:
            logger.warning(f"SpecialtyGuideRepository.ensure_table failed (fail-open): {e}")

    async def get_guide(self, area_query: str, category: str = "food") -> Optional[dict[str, Any]]:
        """依據區域搜尋詞與類別 (food/drink/souvenir) 查詢導覽紀錄 (優先 manual > grounding)"""
        norm_key = (area_query or "").strip().lower()
        if not norm_key:
            return None

        cat = (category or "food").strip().lower()

        # 優先比對 area_key，次之比對 aliases JSONB 陣列包含此關鍵字
        sql = """
            SELECT area_key, category, area_name, aliases, intro, specialties, source_tier, grounding_snippets, verified, updated_at
            FROM area_specialty_guides
            WHERE category = %s
              AND (
                  LOWER(area_key) = %s
                  OR aliases @> jsonb_build_array(%s::text)
              )
            ORDER BY CASE WHEN source_tier = 'manual' THEN 1 ELSE 2 END, updated_at DESC
            LIMIT 1;
        """

        try:
            return await asyncio.wait_for(self._exec_get(sql, (cat, norm_key, norm_key)), timeout=1.5)
        except Exception as e:
            logger.warning(f"SpecialtyGuideRepository.get_guide fail-open for '{area_query}' ({cat}): {e}")
            return None

    async def _exec_get(self, sql: str, params: tuple[Any, ...]) -> Optional[dict[str, Any]]:
        await self.ensure_table()
        pool = await get_db_pool()
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, params)
                row = await cur.fetchone()
                if not row:
                    return None
                data = dict(row)
                # 確保 specialties 與 aliases 解析為 Python 物件
                if isinstance(data.get("specialties"), str):
                    try:
                        data["specialties"] = json.loads(data["specialties"])
                    except Exception:
                        pass
                if isinstance(data.get("aliases"), str):
                    try:
                        data["aliases"] = json.loads(data["aliases"])
                    except Exception:
                        pass
                return data

    async def upsert_guide(
        self,
        area_key: str,
        category: str,
        area_name: str,
        intro: str,
        specialties: list[dict[str, Any]],
        aliases: Optional[list[str]] = None,
        source_tier: str = "grounding",
        grounding_snippets: Optional[list[str]] = None,
        verified: bool = False,
    ) -> bool:
        """寫回或更新導覽記錄至資料庫 (Write-Back)"""
        norm_key = (area_key or "").strip().lower()
        cat = (category or "food").strip().lower()
        if not norm_key or not specialties:
            return False

        clean_aliases = list(dict.fromkeys([a.strip().lower() for a in (aliases or []) if a and a.strip()]))
        if norm_key not in clean_aliases:
            clean_aliases.append(norm_key)

        sql = """
            INSERT INTO area_specialty_guides (
                area_key, category, area_name, aliases, intro, specialties, source_tier, grounding_snippets, verified, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (area_key, category) DO UPDATE SET
                area_name = EXCLUDED.area_name,
                aliases = EXCLUDED.aliases,
                intro = EXCLUDED.intro,
                specialties = EXCLUDED.specialties,
                source_tier = EXCLUDED.source_tier,
                grounding_snippets = EXCLUDED.grounding_snippets,
                verified = EXCLUDED.verified,
                updated_at = NOW();
        """

        try:
            params = (
                norm_key,
                cat,
                area_name,
                json.dumps(clean_aliases, ensure_ascii=False),
                intro,
                json.dumps(specialties, ensure_ascii=False),
                source_tier,
                json.dumps(grounding_snippets or [], ensure_ascii=False),
                verified,
            )
            await asyncio.wait_for(self._exec_upsert(sql, params), timeout=1.5)
            return True
        except Exception as e:
            logger.warning(f"SpecialtyGuideRepository.upsert_guide fail-open for '{area_key}' ({cat}): {e}")
            return False

    async def _exec_upsert(self, sql: str, params: tuple[Any, ...]) -> None:
        await self.ensure_table()
        pool = await get_db_pool()
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, params)
                await conn.commit()
