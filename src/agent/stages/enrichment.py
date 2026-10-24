# -*- coding: utf-8 -*-
"""State 3: Parallel Tabelog Enrichment and Foodie Ranking

負責即時 Tabelog 爬取、實體對齊 (Entity Resolution)、評論向量化與檢索、
老饕綜合得分計算 (Foodie Score) 以及 Top 店家排序去重。
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import TYPE_CHECKING, Any, Optional
from uuid import UUID

from src.services.entity_resolver import (
    extract_prefecture_from_address,
    extract_prefecture_from_tabelog_url,
)

if TYPE_CHECKING:
    from src.agent.pipeline import FoodRecommendationFSM

logger = logging.getLogger(__name__)


def format_payment_methods(
    supports_paypay: Optional[bool] = None,
    credit_cards: Optional[list[str]] = None,
    accepts_credit_cards: Optional[bool] = None,
    accepts_cash_only: Optional[bool] = None,
    accepts_nfc: Optional[bool] = None,
) -> str:
    """將 Google Places (New) 與 Tabelog 支付方式雙源融合轉換為旅客友善的說明字串 (消除假陰性武斷斷言)"""
    parts: list[str] = []

    # 1. 信用卡與無現金支付判定：
    #    (a) 若 Tabelog 有明確卡別優先展示卡別
    if credit_cards and len(credit_cards) > 0:
        if credit_cards == ["可"]:
            parts.append("信用卡")
        else:
            cards_str = "、".join(credit_cards[:3])
            parts.append(f"信用卡 ({cards_str})")
    #    (b) 若 Google 標記可刷卡亦採信
    elif accepts_credit_cards is True:
        parts.append("信用卡")
    #    (c) 若 Google Places 明確標註非僅收現金 (acceptsCashOnly: False)，且未被 Tabelog 明確否定為 0 卡
    elif accepts_cash_only is False and (credit_cards is None or len(credit_cards) > 0):
        parts.append("信用卡 / 電子支付")

    # 2. 感應支付 (NFC / Apple Pay 等) 判定
    if accepts_nfc is True and "信用卡 / 電子支付" not in parts:
        parts.append("感應支付")

    # 3. PayPay 行動支付判定
    if supports_paypay is True:
        parts.append("PayPay")

    if parts:
        return "可使用 " + " / ".join(parts)

    # 4. 現金判定：必須有明確信號指出「僅收現金」
    if accepts_cash_only is True or (credit_cards is not None and len(credit_cards) == 0 and supports_paypay is False):
        return "現金為主 (不支援信用卡/行動支付，請備足現鈔)"

    # 5. 未知/未明確標記：溫和提示
    if supports_paypay is False:
        return "現金為主 (不支援 PayPay，建議備足日圓現鈔)"

    return "現金為主 (建議備足日圓現鈔)"


def calculate_foodie_score(store: dict[str, Any]) -> float:
    """計算單一店家老饕綜合重排得分 (Foodie Score)"""
    t_rating = store.get("tabelog_rating")
    g_rating = float(store.get("rating") or store.get("google_rating") or 3.5)
    dist_m = float(store.get("distance_meters") or 300)
    awards = store.get("awards") or []
    hyakumeiten = store.get("hyakumeiten") or []
    rev_count = int(store.get("review_count") or 0)

    # 1. 基礎評分 (Tabelog 優先，若無則降權折算上限 3.05)
    if t_rating and float(t_rating) > 0:
        base_score = float(t_rating)
    else:
        base_score = min(g_rating * 0.65, 3.05)  # Google 評分降權折算，確保 Tabelog 高分店絕對優先

    # 2. 老饕榮譽加分
    award_bonus = 0.0
    if awards or hyakumeiten:
        award_bonus += 0.15
    if rev_count >= 500:
        award_bonus += 0.05

    # 3. 距離懲罰
    dist_penalty = 0.0
    if dist_m > 1500:
        dist_penalty = 0.12
    elif dist_m > 800:
        dist_penalty = 0.05

    return base_score + award_bonus - dist_penalty


def rank_and_select_top_stores(
    stores: list[dict[str, Any]],
    top_k: int = 3,
) -> list[dict[str, Any]]:
    """依老饕綜合得分降序重排，並進行嚴格去重 (Deduplication)，選取 Top K 高分店家，注入排名徽章"""
    if not stores:
        return []

    ranked = sorted(stores, key=calculate_foodie_score, reverse=True)

    deduped: list[dict[str, Any]] = []
    seen_tabelog_urls: set[str] = set()
    seen_names: set[str] = set()

    for s in ranked:
        t_url = s.get("tabelog_url")
        raw_name = s.get("name_ja") or s.get("name") or ""
        clean_name = re.sub(r"[\s\-_・，,（）()]+", "", raw_name).lower()

        if t_url and t_url in seen_tabelog_urls:
            continue
        if clean_name and clean_name in seen_names:
            continue

        if t_url:
            seen_tabelog_urls.add(t_url)
        if clean_name:
            seen_names.add(clean_name)

        deduped.append(s)
        if len(deduped) >= top_k:
            break

    top_stores = deduped
    rank_badges = ["🥇", "🥈", "🥉", "🏅", "🎖️"]
    for idx, s in enumerate(top_stores):
        s["rank_index"] = idx + 1
        s["rank_badge"] = rank_badges[idx] if idx < len(rank_badges) else f"#{idx + 1}"

    return top_stores


async def enrich_single_place(
    fsm: FoodRecommendationFSM,
    place: dict[str, Any],
    area_hint: Optional[str] = None,
) -> dict[str, Any]:
    """對單一店家補全 Tabelog 評分 (優先 Neon DB 快取，未命中則爬蟲+Resolver)"""
    place_id = place.get("google_place_id", "")
    store_name = place.get("name", "")

    # 1. Neon DB 快取檢查 (7 天 TTL)
    cached = None
    try:
        cached = await fsm.restaurant_repo.get_by_google_place_id(place_id)
    except Exception as e:
        logger.warning(f"DB cache check error for {place_id}: {e}")

    if cached and cached.get("tabelog_rating"):
        is_fresh = True
        updated_at = cached.get("updated_at")
        if updated_at:
            from datetime import datetime, timezone

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
                    asyncio.create_task(fsm.restaurant_repo.delete_by_id(cached["id"]))

        if is_fresh:
            rid = cached.get("id")
            if rid and place.get("reviews"):
                asyncio.create_task(index_store_reviews(fsm, rid, place["reviews"]))
            pay_cards = cached.get("credit_cards")
            pay_desc = format_payment_methods(
                supports_paypay=cached.get("supports_paypay"),
                credit_cards=pay_cards,
                accepts_credit_cards=place.get("accepts_credit_cards"),
                accepts_cash_only=place.get("accepts_cash_only"),
                accepts_nfc=place.get("accepts_nfc"),
            )
            return {
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
                "rating_source": "Tabelog",
            }

    # 2. 未命中快取：執行即時爬蟲與比對
    try:
        # 自動組合商圈/地名關鍵字，精準鎖定在地分店 (如 Mawashizushi KATSU 横浜)
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

        candidates = await fsm.tabelog_scraper.search_tabelog_candidates(store_name=search_query)
        match_res = (
            await fsm.entity_resolver.resolve_entity(
                google_store=place,
                tabelog_candidates=candidates,
                allow_llm=True,
            )
            if candidates
            else None
        )

        # 階梯備援 1：若帶細部商圈搜尋未匹配，回退至大行政區/城市搜尋 (如 橫濱/新宿)
        if (not match_res or not match_res.get("match_found")) and search_query != store_name:
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
            fallback_cands = await fsm.tabelog_scraper.search_tabelog_candidates(store_name=broad_query)
            if fallback_cands:
                fb_match = await fsm.entity_resolver.resolve_entity(
                    google_store=place,
                    tabelog_candidates=fallback_cands,
                    allow_llm=True,
                )
                if fb_match and fb_match.get("match_found"):
                    match_res = fb_match

        # 階梯備援 2：若電話號碼有效，啟動電話精準檢索保底
        phone_raw = (place.get("phone") or "").strip()
        if (not match_res or not match_res.get("match_found")) and phone_raw and len(re.sub(r"\D", "", phone_raw)) >= 9:
            phone_clean = re.sub(r"[^\d-]", "", phone_raw)
            logger.info(f"Store matching unaligned for '{store_name}'. Fallback to phone search: '{phone_clean}'")
            phone_candidates = await fsm.tabelog_scraper.search_via_grounding(store_name=phone_clean, area_name="")
            if phone_candidates:
                for pc in phone_candidates:
                    if not pc.get("phone"):
                        pc["phone"] = phone_clean
                phone_match = await fsm.entity_resolver.resolve_entity(
                    google_store=place,
                    tabelog_candidates=phone_candidates,
                    allow_llm=True,
                )
                if phone_match and phone_match.get("match_found"):
                    match_res = phone_match

        if match_res and match_res.get("match_found") and match_res.get("candidate"):
            cand = match_res["candidate"]
            tabelog_url = cand.get("tabelog_url", "")
            details = await fsm.tabelog_scraper.scrape_restaurant_details(tabelog_url, fallback_candidate=cand)
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
                    asyncio.create_task(index_store_reviews(fsm, saved_id, chosen_reviews))
            except Exception as e:
                logger.warning(f"Failed to upsert tabelog cache: {e}")

            return {
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
                "rating_source": "Tabelog",
            }
    except Exception as e:
        logger.warning(f"Tabelog scraping / resolution failed for {store_name}: {e}")

    # 3. 爬蟲失敗或無資料保底：以 Google 評分呈現
    fallback_id = None
    try:
        fallback_id = await fsm.restaurant_repo.save_restaurant_with_tabelog(google_store=place)
        if fallback_id and place.get("reviews"):
            asyncio.create_task(index_store_reviews(fsm, fallback_id, place["reviews"]))
    except Exception:
        pass

    fallback_reviews = place.get("reviews") or []
    place_id = place.get("google_place_id") or place.get("id")
    fallback_maps_url = place.get("google_maps_url") or (
        f"https://www.google.com/maps/place/?q=place_id:{place_id}" if place_id else None
    )
    return {
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
        "rating_source": "Google",
    }


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

        sample_reviews = reviews[:3]
        texts = [
            r.get("text") or r.get("original_text", "")
            for r in sample_reviews
            if (r.get("text") or r.get("original_text"))
        ]
        if not texts:
            return

        embeddings = await fsm.embedding_service.get_embeddings_batch(texts)
        records = []
        for r, emb in zip(sample_reviews, embeddings):
            if any(emb):
                records.append(
                    {
                        "restaurant_id": rid,
                        "review_text_ja": r.get("original_text") or r.get("text", ""),
                        "review_text_zh": r.get("text") or r.get("original_text", ""),
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


async def execute_state_3_parallel_tabelog(
    fsm: FoodRecommendationFSM,
    places: list[dict[str, Any]],
    area_hint: Optional[str] = None,
) -> list[dict[str, Any]]:
    """State 3: 自適應漸進查驗 (Adaptive Early Stopping) + Tabelog 老饕綜合重排"""
    t0 = time.time()
    tier1_places = places[:4]
    tier2_places = places[4:6]

    # 1. Tier 1 平行查驗 (前 4 家高潛力種子)
    tier1_tasks = [enrich_single_place(fsm, p, area_hint=area_hint) for p in tier1_places]
    tier1_results = await asyncio.gather(*tier1_tasks)

    # 2. 品質門檻檢驗：若已有 >= 2 家達到 Tabelog 高分名店標準 (tabelog_rating >= 3.45 或百名店)
    high_score_count = sum(
        1
        for s in tier1_results
        if (s.get("tabelog_rating") and float(s["tabelog_rating"]) >= 3.45)
        or bool(s.get("awards"))
        or bool(s.get("hyakumeiten"))
    )

    final_enriched = list(tier1_results)

    # 3. 自適應決策：若品質不足且有 Tier 2 備選店家，啟動動態查驗；否則觸發 Early Stopping
    if high_score_count < 2 and tier2_places:
        logger.info(
            f"Tier 1 high-score stores count is {high_score_count} (<2). Triggering Tier 2 adaptive rescue for {len(tier2_places)} stores."
        )
        tier2_tasks = [enrich_single_place(fsm, p, area_hint=area_hint) for p in tier2_places]
        tier2_results = await asyncio.gather(*tier2_tasks)
        final_enriched.extend(tier2_results)
    else:
        logger.info(f"Tier 1 high-score count={high_score_count}. Early stopping satisfied, skipping Tier 2.")

    # 4. 老饕綜合加權重排，精選 Top 3
    top_stores = rank_and_select_top_stores(final_enriched, top_k=3)

    # 5. 為最終入選的 Top 店家平行豐富定向老饕食評 (Tabelog 口コミ絕對優先合約)
    async def _enrich_foodie_reviews(store: dict[str, Any]) -> None:
        tabelog_url = store.get("tabelog_url")
        tabelog_fetcher = getattr(fsm.tabelog_scraper, "fetch_tabelog_reviews", None)

        if tabelog_url and callable(tabelog_fetcher):
            search_name = store.get("name_ja") or store.get("name") or ""
            tabelog_revs: list[dict[str, Any]] = []
            try:
                res = tabelog_fetcher(search_name, max_reviews=5)
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
                    asyncio.create_task(index_store_reviews(fsm, rid, tabelog_revs))
                return

        existing_revs = store.get("sample_reviews") or []
        if existing_revs and isinstance(existing_revs, list) and isinstance(existing_revs[0], dict):
            src = existing_revs[0].get("source") or ("tabelog" if store.get("tabelog_url") else "google")
            store["foodie_review_source"] = src
            if not store.get("foodie_review_highlight"):
                first_text = existing_revs[0].get("text") or existing_revs[0].get("original_text")
                if isinstance(first_text, str):
                    store["foodie_review_highlight"] = first_text

    if top_stores:
        await asyncio.gather(*[_enrich_foodie_reviews(s) for s in top_stores])

    latency = int((time.time() - t0) * 1000)

    stores_input_summary = [
        {
            "name": p.get("name"),
            "phone": p.get("phone"),
            "google_rating": p.get("rating") or p.get("google_rating"),
        }
        for p in places
    ]

    stores_output_summary = [
        {
            "name_ja": s.get("name_ja") or s.get("name"),
            "tabelog_rating": s.get("tabelog_rating"),
            "review_count": s.get("review_count"),
            "price_lunch": s.get("price_lunch"),
            "price_dinner": s.get("price_dinner"),
            "supports_paypay": s.get("supports_paypay"),
            "credit_cards": s.get("credit_cards") or [],
            "awards": s.get("awards") or [],
            "tabelog_url": s.get("tabelog_url"),
            "sample_reviews": s.get("sample_reviews") or [],
            "rating_source": s.get("rating_source", "Tabelog"),
            "rank_badge": s.get("rank_badge"),
        }
        for s in top_stores
    ]

    await fsm.trace_logger.log_step(
        action_type="state_3_tabelog",
        tool_name="parallel_tabelog_enrichment",
        tool_input={"stores_count": len(places), "stores_searched": stores_input_summary},
        tool_output={"selected_count": len(top_stores), "aligned_stores": stores_output_summary},
        latency_ms=latency,
    )
    return top_stores
