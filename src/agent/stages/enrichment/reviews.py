# -*- coding: utf-8 -*-
"""Review vector indexing, semantic search, and foodie review enrichment."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:
    from src.agent.pipeline import FoodRecommendationFSM

logger = logging.getLogger(__name__)


async def index_store_reviews(
    fsm: FoodRecommendationFSM,
    restaurant_id: Any,
    reviews: list[dict[str, Any]],
) -> None:
    """非同步將 Google/Tabelog 評論向量化並寫入 review_embeddings (pgvector)"""
    if not reviews or not fsm.vector_repo or not fsm.embedding_service:
        return
    try:
        rid = UUID(str(restaurant_id))
        existing = await fsm.vector_repo.get_reviews_by_restaurant_id(rid)
        if existing:
            return

        valid_reviews = [r for r in reviews[:3] if (r.get("text") or r.get("original_text"))]
        texts = [str(r.get("text") or r.get("original_text", "")) for r in valid_reviews]
        if not texts:
            return

        # 直接使用既有中文欄位或原文進行向量索引，避免背景 LLM 翻譯與前景爭搶配額
        zh_texts = [
            str(r.get("zh") or r.get("text_zh") or r.get("text") or r.get("original_text", "")) for r in valid_reviews
        ]
        embeddings = await fsm.embedding_service.get_embeddings_batch(texts)

        records = []
        for r, ja_text, zh_text, emb in zip(valid_reviews, texts, zh_texts, embeddings):
            if any(emb):
                records.append(
                    {
                        "restaurant_id": rid,
                        "review_text_ja": str(r.get("original_text") or ja_text),
                        "review_text_zh": zh_text,
                        "embedding": emb,
                    }
                )
        if records:
            await fsm.vector_repo.batch_insert_reviews(records)
            logger.info(f"Indexed {len(records)} review embeddings for {rid}")
    except Exception as e:
        logger.warning(f"Could not index review embeddings for {restaurant_id}: {e}")


async def semantic_rerank_reviews(
    fsm: FoodRecommendationFSM,
    user_query: str,
    stores: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """依據使用者詢問之口感/氛圍，透過 pgvector Cosine 相似度檢索最契合的食評亮點"""
    if not fsm.embedding_service or not fsm.vector_repo or not stores:
        return stores

    try:
        query_vec = await fsm.embedding_service.get_embedding(user_query)
        if not any(query_vec):
            return stores

        valid_rids: list[UUID] = []
        store_map: dict[UUID, dict[str, Any]] = {}
        for s in stores:
            rid_str = s.get("restaurant_id")
            if rid_str:
                try:
                    rid = UUID(str(rid_str))
                    valid_rids.append(rid)
                    store_map[rid] = s
                except Exception:
                    pass

        if not valid_rids:
            return stores

        matches = await fsm.vector_repo.search_similar_reviews(
            query_embedding=query_vec,
            top_k=len(valid_rids),
            restaurant_ids=valid_rids,
        )

        for m in matches:
            rid_val = m.get("restaurant_id")
            if rid_val:
                rid_obj = UUID(str(rid_val))
                if rid_obj in store_map:
                    highlight = m.get("review_text_zh") or m.get("review_text_ja")
                    if highlight and not store_map[rid_obj].get("foodie_review_highlight"):
                        sim = m.get("similarity", 0.0)
                        store_map[rid_obj]["foodie_review_highlight"] = highlight
                        store_map[rid_obj]["review_similarity"] = sim

        return stores
    except Exception as e:
        logger.warning(f"Semantic reranking skipped: {e}")
        return stores


async def enrich_foodie_reviews(
    fsm: FoodRecommendationFSM,
    store: dict[str, Any],
) -> None:
    """為入選店家豐富定向老饕食評 (Tabelog 口コミ絕對優先合約)"""
    tabelog_url = store.get("tabelog_url")
    tabelog_fetcher = getattr(fsm.tabelog_scraper, "fetch_tabelog_reviews", None)

    if tabelog_url and callable(tabelog_fetcher):
        search_name = store.get("name_ja") or store.get("name") or ""
        tabelog_revs: list[dict[str, Any]] = []
        try:
            res = tabelog_fetcher(search_name)
            if asyncio.iscoroutine(res):
                raw_revs = await res
                if isinstance(raw_revs, list):
                    tabelog_revs = raw_revs
            elif isinstance(res, list):
                tabelog_revs = res
        except Exception as e:
            logger.warning(f"Error fetching tabelog reviews for '{search_name}': {e}")

        if tabelog_revs and len(tabelog_revs) > 0:
            store["sample_reviews"] = tabelog_revs
            first_text = tabelog_revs[0].get("text") if isinstance(tabelog_revs[0], dict) else None
            if isinstance(first_text, str):
                store["foodie_review_highlight"] = first_text
            store["foodie_review_source"] = "tabelog"
            rid = store.get("restaurant_id")
            if rid:
                index_coro = fsm._index_store_reviews(rid, tabelog_revs)
                if asyncio.iscoroutine(index_coro):
                    asyncio.create_task(index_coro)
            return

    existing_revs = store.get("sample_reviews") or []
    if existing_revs and isinstance(existing_revs, list) and isinstance(existing_revs[0], dict):
        src = existing_revs[0].get("source") or ("tabelog" if store.get("tabelog_url") else "google")
        store["foodie_review_source"] = src
        if not store.get("foodie_review_highlight"):
            first_text = existing_revs[0].get("text") or existing_revs[0].get("original_text")
            if isinstance(first_text, str):
                store["foodie_review_highlight"] = first_text
