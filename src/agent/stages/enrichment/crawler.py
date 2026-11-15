# -*- coding: utf-8 -*-
"""Tabelog crawler search, multi-tier fallback resolution, and details scraping."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING, Any, Optional

from src.agent.stages.enrichment.formatting import format_payment_methods
from src.agent.stages.enrichment.geocoding import _align_walking_and_station

if TYPE_CHECKING:
    from src.agent.pipeline import FoodRecommendationFSM

logger = logging.getLogger(__name__)


async def search_and_resolve_tabelog(
    fsm: FoodRecommendationFSM,
    place: dict[str, Any],
    area_hint: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """即時爬取 Tabelog 候選店家並執行實體對齊與細節爬取。若對齊成功回傳推薦字典，否則回傳 None"""
    store_name = place.get("name", "")

    try:
        # 1. 自動組合商圈/地名關鍵字，精準鎖定在地分店 (如 Mawashizushi KATSU 横浜)
        search_query = store_name
        target_area = (area_hint or "").strip()
        if not target_area and place.get("address"):
            m = re.search(
                r"(?:[^\d\s,，]{2,3}[都道府縣県])\s*([^\d\s,，]{2,6}?[市區町村島])",
                place["address"],
            )
            if not m:
                m = re.search(r"([^\d\s,，]{2,6}?[市區町村島])", place["address"])
            if m:
                target_area = re.sub(r"[市區町村都道府縣県]$", "", m.group(1))

        if target_area and target_area.lower() not in store_name.lower():
            search_query = f"{store_name} {target_area}".strip()

        raw_candidates = await fsm.tabelog_scraper.search_tabelog_candidates(store_name=search_query)
        candidates = raw_candidates if isinstance(raw_candidates, list) else []
        match_res = None
        if candidates:
            raw_match = await fsm.entity_resolver.resolve_entity(
                google_store=place,
                tabelog_candidates=candidates,
                allow_llm=True,
            )
            if isinstance(raw_match, dict):
                match_res = raw_match

        # 2. 階梯備援 1：若帶細部商圈搜尋未匹配，回退至大行政區/城市搜尋 (如 橫濱/新宿)
        match_found = bool(match_res and match_res.get("match_found"))
        if not match_found and search_query != store_name:
            city_anchor = ""
            if place.get("address"):
                city_m = re.search(r"([^\d\s,，]{2,6}?(?:市|特別区|都|府|県))", place["address"])
                if city_m:
                    city_anchor = re.sub(r"[都道府縣県市]$", "", city_m.group(1)).strip()

            broad_query = (
                f"{store_name} {city_anchor}".strip()
                if (city_anchor and city_anchor.lower() != target_area.lower())
                else store_name
            )
            raw_fb_cands = await fsm.tabelog_scraper.search_tabelog_candidates(store_name=broad_query)
            fallback_cands = raw_fb_cands if isinstance(raw_fb_cands, list) else []
            if fallback_cands:
                fb_match = await fsm.entity_resolver.resolve_entity(
                    google_store=place,
                    tabelog_candidates=fallback_cands,
                    allow_llm=True,
                )
                if isinstance(fb_match, dict) and fb_match.get("match_found"):
                    match_res = fb_match
                    match_found = True

        # 3. 階梯備援 2：若電話號碼有效，啟動電話精準檢索保底
        phone_raw = (place.get("phone") or "").strip()
        if not match_found and phone_raw and len(re.sub(r"\D", "", phone_raw)) >= 9:
            phone_clean = re.sub(r"[^\d-]", "", phone_raw)
            logger.info(f"Store matching unaligned for '{store_name}'. Fallback to phone search: '{phone_clean}'")
            raw_phone_cands = await fsm.tabelog_scraper.search_via_grounding(store_name=phone_clean, area_name="")
            phone_candidates = raw_phone_cands if isinstance(raw_phone_cands, list) else []
            if phone_candidates:
                for pc in phone_candidates:
                    if isinstance(pc, dict) and not pc.get("phone"):
                        pc["phone"] = phone_clean
                phone_match = await fsm.entity_resolver.resolve_entity(
                    google_store=place,
                    tabelog_candidates=phone_candidates,
                    allow_llm=True,
                )
                if isinstance(phone_match, dict) and phone_match.get("match_found"):
                    match_res = phone_match

        if match_res and match_res.get("match_found") and isinstance(match_res.get("candidate"), dict):
            cand = match_res["candidate"]
            tabelog_url = cand.get("tabelog_url", "")
            raw_details = await fsm.tabelog_scraper.scrape_restaurant_details(tabelog_url, fallback_candidate=cand)
            details = raw_details if isinstance(raw_details, dict) else {}
            rating = (
                details.get("tabelog_rating")
                or cand.get("tabelog_rating")
                or details.get("rating")
                or cand.get("rating")
            )
            rev_count = details.get("review_count") or cand.get("review_count")
            cards_list = details.get("credit_cards") or cand.get("credit_cards")
            paypay_val = details.get("supports_paypay")
            pay_desc = format_payment_methods(
                supports_paypay=paypay_val,
                credit_cards=cards_list,
                accepts_credit_cards=place.get("accepts_credit_cards"),
                accepts_cash_only=place.get("accepts_cash_only"),
                accepts_nfc=place.get("accepts_nfc"),
            )

            # 存入快取
            tabelog_save_data = {
                **cand,
                **details,
                "tabelog_rating": rating,
                "review_count": rev_count,
                "tabelog_url": tabelog_url,
                "name_ja": cand.get("name_ja") or store_name,
                "supports_paypay": paypay_val,
                "credit_cards": cards_list,
            }
            chosen_reviews = details.get("sample_reviews") or cand.get("sample_reviews") or place.get("reviews") or []

            saved_id = None
            try:
                saved_id = await fsm.restaurant_repo.save_restaurant_with_tabelog(
                    google_store=place,
                    tabelog_data=tabelog_save_data,
                    match_confidence=match_res.get("confidence", 0.9),
                    match_method=match_res.get("match_method", "rule_high"),
                )
                if saved_id and chosen_reviews:
                    index_coro = fsm._index_store_reviews(saved_id, chosen_reviews)
                    if asyncio.iscoroutine(index_coro):
                        asyncio.create_task(index_coro)
            except Exception as e:
                logger.warning(f"Failed to upsert tabelog cache: {e}")

            nearest_st = (
                details.get("nearest_station_desc")
                or cand.get("nearest_station_desc")
                or place.get("nearest_station_desc")
            )
            res_dict = {
                **place,
                "restaurant_id": str(saved_id) if saved_id else None,
                "tabelog_url": tabelog_url,
                "name_ja": cand.get("name_ja") or store_name,
                "tabelog_rating": rating,
                "review_count": rev_count,
                "price_lunch": details.get("price_lunch"),
                "price_dinner": details.get("price_dinner"),
                "awards": details.get("awards") or cand.get("awards") or [],
                "supports_paypay": paypay_val,
                "credit_cards": cards_list,
                "payment_methods_desc": pay_desc,
                "sample_reviews": chosen_reviews,
                "nearest_station_desc": nearest_st,
                "rating_source": "Tabelog",
            }
            return _align_walking_and_station(res_dict, nearest_st, area_hint)

    except Exception as e:
        logger.warning(f"Tabelog scraping / resolution failed for {store_name}: {e}")

    return None
