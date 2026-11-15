# -*- coding: utf-8 -*-
"""Single place enrichment orchestrator: Neon DB cache, seed fast-path, crawler, and fallbacks."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Optional

from src.agent.stages.enrichment.cache import (
    _build_cached_tabelog_result,
    _build_seed_result,
    check_cached_store,
    check_seed_store,
)
from src.agent.stages.enrichment.crawler import search_and_resolve_tabelog
from src.agent.stages.enrichment.geocoding import _align_walking_and_station

if TYPE_CHECKING:
    from src.agent.pipeline import FoodRecommendationFSM

logger = logging.getLogger(__name__)


async def enrich_single_place(
    fsm: FoodRecommendationFSM,
    place: dict[str, Any],
    area_hint: Optional[str] = None,
) -> dict[str, Any]:
    """對單一店家補全 Tabelog 評分 (優先 Neon DB 快取，未命中則爬蟲+Resolver，最終 Google 降級保底)"""
    store_name = place.get("name", "")

    # 1. Neon DB 快取檢查 (7 天 TTL + 行政區一致性防護)
    cached_result, incomplete_cache_fallback = await check_cached_store(fsm, place, area_hint)
    if cached_result is not None:
        return cached_result

    # 1.5 雙源直通車：若 State 2 Tabelog-First 種子已具備完整核心欄位直接組裝
    seed_result, incomplete_seed_fallback = check_seed_store(place, area_hint)
    if seed_result is not None:
        return seed_result

    # 2. 未命中快取：執行即時爬蟲與階梯式比對
    scraped_result = await search_and_resolve_tabelog(fsm, place, area_hint)
    if scraped_result is not None:
        return scraped_result

    # 2.5 爬蟲無結果時之不完整快取/種子保底
    if incomplete_cache_fallback is not None:
        logger.info(f"Live refresh yielded no match for '{store_name}'; falling back to cached Tabelog record.")
        return _build_cached_tabelog_result(place, incomplete_cache_fallback, area_hint)

    if incomplete_seed_fallback is not None:
        logger.info(f"Live refresh yielded no match for '{store_name}'; falling back to Tabelog seed record.")
        return _build_seed_result(incomplete_seed_fallback, area_hint)

    # 3. 爬蟲失敗或無資料保底：以 Google 評分呈現
    fallback_id = None
    try:
        fallback_id = await fsm.restaurant_repo.save_restaurant_with_tabelog(google_store=place)
        if fallback_id and place.get("reviews"):
            index_coro = fsm._index_store_reviews(fallback_id, place["reviews"])
            if asyncio.iscoroutine(index_coro):
                asyncio.create_task(index_coro)
    except Exception:
        pass

    fallback_reviews = place.get("reviews") or []
    place_id = place.get("google_place_id") or place.get("id")
    fallback_maps_url = place.get("google_maps_url") or (
        f"https://www.google.com/maps/place/?q=place_id:{place_id}" if place_id else None
    )
    fallback_res = {
        **place,
        "restaurant_id": str(fallback_id) if fallback_id else None,
        "tabelog_url": None,
        "google_maps_url": fallback_maps_url,
        "name_ja": store_name,
        "tabelog_rating": None,
        "review_count": place.get("user_ratings_total"),
        "price_lunch": None,
        "price_dinner": None,
        "awards": [],
        "supports_paypay": None,
        "credit_cards": [],
        "payment_methods_desc": "現金為主 (建議備足日圓現鈔)",
        "sample_reviews": fallback_reviews,
        "nearest_station_desc": place.get("nearest_station_desc"),
        "rating_source": "Google",
    }
    return _align_walking_and_station(fallback_res, fallback_res.get("nearest_station_desc"), area_hint)
