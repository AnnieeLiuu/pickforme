# -*- coding: utf-8 -*-
"""Cache validation, cache retrieval, and seed processing for Enrichment stage."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from src.agent.stages.enrichment.formatting import format_payment_methods
from src.agent.stages.enrichment.geocoding import _align_walking_and_station
from src.services.entity_resolver import (
    extract_prefecture_from_address,
    extract_prefecture_from_tabelog_url,
)

if TYPE_CHECKING:
    from src.agent.pipeline import FoodRecommendationFSM

logger = logging.getLogger(__name__)


def _is_tabelog_cache_complete(cached: dict[str, Any]) -> bool:
    """檢查 Tabelog 資料是否具備推薦卡片所需核心欄位 (評分、連結、評價數、至少一項預算)"""
    has_budget = bool(cached.get("price_lunch") or cached.get("price_dinner"))
    review_count = cached.get("review_count")
    if review_count is None:
        review_count = cached.get("tabelog_review_count")
    return bool(cached.get("tabelog_rating") and cached.get("tabelog_url") and review_count is not None and has_budget)


def _build_cached_tabelog_result(
    place: dict[str, Any],
    cached: dict[str, Any],
    area_hint: Optional[str] = None,
) -> dict[str, Any]:
    """將快取的 Tabelog 紀錄組裝為標準推薦店家字典"""
    rid = cached.get("id")
    store_name = place.get("name", "")
    pay_cards = cached.get("credit_cards")
    pay_desc = format_payment_methods(
        supports_paypay=cached.get("supports_paypay"),
        credit_cards=pay_cards,
        accepts_credit_cards=place.get("accepts_credit_cards"),
        accepts_cash_only=place.get("accepts_cash_only"),
        accepts_nfc=place.get("accepts_nfc"),
    )
    res_dict = {
        **place,
        "restaurant_id": str(rid) if rid else None,
        "tabelog_url": cached.get("tabelog_url"),
        "name_ja": cached.get("name_ja") or store_name,
        "tabelog_rating": float(cached["tabelog_rating"]) if cached.get("tabelog_rating") else None,
        "review_count": cached.get("review_count"),
        "price_lunch": cached.get("price_lunch"),
        "price_dinner": cached.get("price_dinner"),
        "awards": cached.get("awards") or [],
        "supports_paypay": cached.get("supports_paypay"),
        "credit_cards": pay_cards,
        "payment_methods_desc": pay_desc,
        "sample_reviews": place.get("reviews") or [],
        "nearest_station_desc": place.get("nearest_station_desc"),
        "rating_source": "Tabelog",
    }
    return _align_walking_and_station(res_dict, res_dict.get("nearest_station_desc"), area_hint)


def _build_seed_result(
    seed_place: dict[str, Any],
    area_hint: Optional[str] = None,
) -> dict[str, Any]:
    """將 State 2 Tabelog-First 種子組裝為標準推薦店家字典"""
    store_name = seed_place.get("name", "")
    pay_desc = format_payment_methods(
        supports_paypay=seed_place.get("supports_paypay"),
        credit_cards=seed_place.get("credit_cards"),
        accepts_credit_cards=seed_place.get("accepts_credit_cards"),
        accepts_cash_only=seed_place.get("accepts_cash_only"),
        accepts_nfc=seed_place.get("accepts_nfc"),
    )
    res_dict = {
        **seed_place,
        "name_ja": seed_place.get("name_ja") or store_name,
        "tabelog_rating": float(seed_place["tabelog_rating"]) if seed_place.get("tabelog_rating") else None,
        "review_count": seed_place.get("tabelog_review_count") or seed_place.get("review_count"),
        "awards": seed_place.get("awards") or seed_place.get("hyakumeiten") or [],
        "payment_methods_desc": pay_desc,
        "sample_reviews": seed_place.get("reviews") or [],
        "nearest_station_desc": seed_place.get("nearest_station_desc"),
        "rating_source": "Tabelog",
    }
    return _align_walking_and_station(res_dict, res_dict.get("nearest_station_desc"), area_hint)


async def check_cached_store(
    fsm: FoodRecommendationFSM,
    place: dict[str, Any],
    area_hint: Optional[str] = None,
) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
    """檢查 Neon DB 快取。若命中且有效回傳 (result, None)；若不完整回傳 (None, incomplete_fallback)；未命中回傳 (None, None)"""
    place_id = place.get("google_place_id", "")
    store_name = place.get("name", "")

    cached = None
    try:
        cached = await fsm.restaurant_repo.get_by_google_place_id(place_id)
    except Exception as e:
        logger.warning(f"DB cache check error for {place_id}: {e}")

    incomplete_cache_fallback: Optional[dict[str, Any]] = None
    if cached and cached.get("tabelog_rating"):
        is_fresh = True
        updated_at = cached.get("updated_at")
        if updated_at:
            now = datetime.now(timezone.utc)
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            if (now - updated_at).days >= 7:
                is_fresh = False

        # 快取行政區一致性防禦：避免歷史髒快取導致跨縣市漂移
        if is_fresh and cached.get("tabelog_url"):
            cached_pref = extract_prefecture_from_tabelog_url(cached.get("tabelog_url"))
            google_pref = extract_prefecture_from_address(place.get("address"))
            if cached_pref and google_pref and cached_pref != google_pref:
                logger.warning(
                    f"Poisoned cache detected for '{store_name}': Google in '{google_pref}' but cached Tabelog in '{cached_pref}'. Self-healing: invalidating and purging dirty record."
                )
                is_fresh = False
                if cached.get("id"):
                    purge_coro = fsm.restaurant_repo.delete_by_id(cached["id"])
                    if asyncio.iscoroutine(purge_coro):
                        asyncio.create_task(purge_coro)

        if is_fresh and not _is_tabelog_cache_complete(cached):
            logger.info(
                f"Incomplete Tabelog cache for '{store_name}' (missing url/review_count/budget); refreshing via live scrape."
            )
            incomplete_cache_fallback = cached
            is_fresh = False

        if is_fresh:
            rid = cached.get("id")
            if rid and place.get("reviews"):
                index_coro = fsm._index_store_reviews(rid, place["reviews"])
                if asyncio.iscoroutine(index_coro):
                    asyncio.create_task(index_coro)
            return _build_cached_tabelog_result(place, cached, area_hint), None

    return None, incomplete_cache_fallback


def check_seed_store(
    place: dict[str, Any],
    area_hint: Optional[str] = None,
) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
    """檢查 State 2 Tabelog-First 種子。若完整回傳 (result, None)；若不完整回傳 (None, seed_fallback)；非種子回傳 (None, None)"""
    if place.get("tabelog_seed") and (place.get("tabelog_rating") or place.get("awards") or place.get("hyakumeiten")):
        if _is_tabelog_cache_complete(place):
            return _build_seed_result(place, area_hint), None
        return None, place
    return None, None
