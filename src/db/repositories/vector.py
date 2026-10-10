import logging
from typing import Any, Optional
from uuid import UUID

from src.db.connection import get_db_pool

logger = logging.getLogger(__name__)


class VectorRepository:
    """老饕評論向量資料庫倉儲 (pgvector + halfvec 768 維)"""

    @staticmethod
    def _format_vector(vector: list[float]) -> str:
        """將 float list 轉為 pgvector 可辨識之字串格式 '[v1,v2,...]'"""
        return "[" + ",".join(f"{x:.6f}" for x in vector) + "]"

    async def insert_review_embedding(
        self,
        restaurant_id: UUID,
        review_text_ja: str,
        embedding: list[float],
        review_text_zh: Optional[str] = None,
    ) -> UUID:
        """儲存單筆評論與其 halfvec(768) 語意向量"""
        pool = await get_db_pool()
        vec_str = self._format_vector(embedding)

        query = """
            INSERT INTO review_embeddings (restaurant_id, review_text_ja, review_text_zh, embedding)
            VALUES (%s, %s, %s, %s::halfvec)
            RETURNING id;
        """
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    query,
                    (restaurant_id, review_text_ja, review_text_zh, vec_str),
                )
                row = await cur.fetchone()
                if not row:
                    raise RuntimeError("Failed to insert review embedding")
                inserted_id = row["id"] if isinstance(row, dict) else row[0]
                return UUID(str(inserted_id))

    async def batch_insert_reviews(
        self,
        records: list[dict[str, Any]],
    ) -> list[UUID]:
        """批次儲存多筆評論向量
        records 格式: [{'restaurant_id': UUID, 'review_text_ja': str, 'review_text_zh': str, 'embedding': list[float]}]
        """
        if not records:
            return []

        pool = await get_db_pool()
        inserted_ids: list[UUID] = []

        query = """
            INSERT INTO review_embeddings (restaurant_id, review_text_ja, review_text_zh, embedding)
            VALUES (%s, %s, %s, %s::halfvec)
            RETURNING id;
        """
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                for rec in records:
                    vec_str = self._format_vector(rec["embedding"])
                    await cur.execute(
                        query,
                        (
                            rec["restaurant_id"],
                            rec["review_text_ja"],
                            rec.get("review_text_zh"),
                            vec_str,
                        ),
                    )
                    row = await cur.fetchone()
                    if row:
                        inserted_id = row["id"] if isinstance(row, dict) else row[0]
                        inserted_ids.append(UUID(str(inserted_id)))
        return inserted_ids

    async def search_similar_reviews(
        self,
        query_embedding: list[float],
        top_k: int = 5,
        restaurant_ids: Optional[list[UUID]] = None,
    ) -> list[dict[str, Any]]:
        """利用 Cosine Distance 檢索最相符之老饕評論
        若提供 restaurant_ids，則限定在該批候選餐廳中做重排 (Semantic Rerank)
        """
        pool = await get_db_pool()
        vec_str = self._format_vector(query_embedding)

        if restaurant_ids:
            query = """
                SELECT
                    r.id,
                    r.restaurant_id,
                    r.review_text_ja,
                    r.review_text_zh,
                    rest.name_ja AS restaurant_name_ja,
                    rest.tabelog_url,
                    1 - (r.embedding <=> %s::halfvec) AS similarity
                FROM review_embeddings r
                JOIN restaurants rest ON r.restaurant_id = rest.id
                WHERE r.restaurant_id = ANY(%s)
                ORDER BY r.embedding <=> %s::halfvec
                LIMIT %s;
            """
            params: tuple[Any, ...] = (vec_str, [str(rid) for rid in restaurant_ids], vec_str, top_k)
        else:
            query = """
                SELECT
                    r.id,
                    r.restaurant_id,
                    r.review_text_ja,
                    r.review_text_zh,
                    rest.name_ja AS restaurant_name_ja,
                    rest.tabelog_url,
                    1 - (r.embedding <=> %s::halfvec) AS similarity
                FROM review_embeddings r
                JOIN restaurants rest ON r.restaurant_id = rest.id
                ORDER BY r.embedding <=> %s::halfvec
                LIMIT %s;
            """
            params = (vec_str, vec_str, top_k)

        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, params)
                rows = await cur.fetchall()
                results = []
                for row in rows:
                    if isinstance(row, dict):
                        results.append(dict(row))
                    else:
                        results.append(
                            {
                                "id": row[0],
                                "restaurant_id": row[1],
                                "review_text_ja": row[2],
                                "review_text_zh": row[3],
                                "restaurant_name_ja": row[4],
                                "tabelog_url": row[5],
                                "similarity": float(row[6]),
                            }
                        )
                return results

    async def get_reviews_by_restaurant_id(self, restaurant_id: UUID) -> list[dict[str, Any]]:
        """查詢特定餐廳的所有收錄評論"""
        pool = await get_db_pool()
        query = """
            SELECT id, restaurant_id, review_text_ja, review_text_zh, created_at
            FROM review_embeddings
            WHERE restaurant_id = %s
            ORDER BY created_at DESC;
        """
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, (restaurant_id,))
                rows = await cur.fetchall()
                return [
                    dict(r)
                    if isinstance(r, dict)
                    else dict(zip(["id", "restaurant_id", "review_text_ja", "review_text_zh", "created_at"], r))
                    for r in rows
                ]
