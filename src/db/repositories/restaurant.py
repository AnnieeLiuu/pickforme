import logging
from typing import Any, Optional
from uuid import UUID, uuid4

from src.db.connection import get_db_pool

logger = logging.getLogger(__name__)


class RestaurantRepository:
    async def get_by_google_place_id(self, google_place_id: str) -> Optional[dict[str, Any]]:
        """透過 google_place_id 查詢餐廳快取與 Tabelog 評分"""
        pool = await get_db_pool()
        query = """
            SELECT
                r.id, r.google_place_id, r.tabelog_url, r.name_ja, r.name_en, r.phone, r.lat, r.lng, r.address,
                c.tabelog_rating, c.review_count, c.price_lunch, c.price_dinner, c.match_confidence, c.match_method, c.updated_at
            FROM restaurants r
            LEFT JOIN tabelog_cache c ON r.id = c.restaurant_id
            WHERE r.google_place_id = %s;
        """
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, (google_place_id,))
                row = await cur.fetchone()
                return dict(row) if row else None

    async def save_restaurant_with_tabelog(
        self,
        google_store: dict[str, Any],
        tabelog_data: Optional[dict[str, Any]] = None,
        match_confidence: float = 0.0,
        match_method: str = "no_match",
    ) -> UUID:
        """儲存/更新餐廳基本資料與 Tabelog 快取 (Neon Postgres)"""
        pool = await get_db_pool()
        google_place_id = google_store["google_place_id"]

        # 先檢查是否已存在
        existing = await self.get_by_google_place_id(google_place_id)
        restaurant_id = existing["id"] if existing else uuid4()

        tabelog_url = tabelog_data.get("tabelog_url") if tabelog_data else None
        name_ja = (tabelog_data.get("name_ja") if tabelog_data and tabelog_data.get("name_ja") else google_store.get("name", ""))
        phone = (tabelog_data.get("phone") if tabelog_data and tabelog_data.get("phone") else google_store.get("phone"))

        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                # 1. Upsert 到 restaurants 表
                upsert_restaurant_sql = """
                    INSERT INTO restaurants (id, google_place_id, tabelog_url, name_ja, name_en, phone, lat, lng, address)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (google_place_id) DO UPDATE SET
                        tabelog_url = EXCLUDED.tabelog_url,
                        name_ja = EXCLUDED.name_ja,
                        phone = EXCLUDED.phone,
                        lat = EXCLUDED.lat,
                        lng = EXCLUDED.lng,
                        address = EXCLUDED.address
                    RETURNING id;
                """
                await cur.execute(
                    upsert_restaurant_sql,
                    (
                        restaurant_id,
                        google_place_id,
                        tabelog_url,
                        name_ja,
                        google_store.get("name"),
                        phone,
                        google_store.get("lat", 0.0),
                        google_store.get("lng", 0.0),
                        google_store.get("address"),
                    ),
                )
                res_row = await cur.fetchone()
                if res_row:
                    restaurant_id = res_row["id"]

                # 2. Upsert 到 tabelog_cache 表 (若有抓到 Tabelog 資料)
                if tabelog_data:
                    upsert_cache_sql = """
                        INSERT INTO tabelog_cache (
                            restaurant_id, tabelog_rating, review_count, price_lunch, price_dinner, match_confidence, match_method, updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                        ON CONFLICT (restaurant_id) DO UPDATE SET
                            tabelog_rating = EXCLUDED.tabelog_rating,
                            review_count = EXCLUDED.review_count,
                            price_lunch = EXCLUDED.price_lunch,
                            price_dinner = EXCLUDED.price_dinner,
                            match_confidence = EXCLUDED.match_confidence,
                            match_method = EXCLUDED.match_method,
                            updated_at = NOW();
                    """
                    await cur.execute(
                        upsert_cache_sql,
                        (
                            restaurant_id,
                            tabelog_data.get("tabelog_rating"),
                            tabelog_data.get("review_count"),
                            tabelog_data.get("price_lunch"),
                            tabelog_data.get("price_dinner"),
                            match_confidence,
                            match_method,
                        ),
                    )

                await conn.commit()
                return restaurant_id
