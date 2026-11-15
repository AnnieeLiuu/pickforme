# -*- coding: utf-8 -*-
"""Unit tests for refactored enrichment submodules."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from src.agent.stages.enrichment import (
    _align_walking_and_station,
    _build_cached_tabelog_result,
    _build_seed_result,
    _extract_min_budget_jpy,
    _hydrate_station_walking_distances,
    _is_tabelog_cache_complete,
    apply_deterministic_constraints,
    calculate_foodie_score,
    check_cached_store,
    check_seed_store,
    enrich_foodie_reviews,
    enrich_single_place,
    execute_state_3_parallel_tabelog,
    format_payment_methods,
    index_store_reviews,
    rank_and_select_top_stores,
    search_and_resolve_tabelog,
    semantic_rerank_reviews,
)


def test_formatting_payment_methods():
    # 1. Tabelog credit cards
    assert "信用卡 (VISA、JCB)" in format_payment_methods(credit_cards=["VISA", "JCB"])
    # 2. Tabelog simple '可'
    assert "信用卡" in format_payment_methods(credit_cards=["可"])
    # 3. Google accepts credit cards
    assert "信用卡" in format_payment_methods(accepts_credit_cards=True)
    # 4. Cash only
    assert "不支援信用卡/行動支付" in format_payment_methods(accepts_cash_only=True)
    # 5. PayPay
    assert "PayPay" in format_payment_methods(supports_paypay=True)
    # 6. NFC
    assert "感應支付" in format_payment_methods(accepts_nfc=True)


def test_scoring_and_budget_extraction():
    # 1. Budget extraction
    assert _extract_min_budget_jpy("～￥999") == 0
    assert _extract_min_budget_jpy("~¥999") == 0
    assert _extract_min_budget_jpy("￥1,000～￥1,999") == 1000
    assert _extract_min_budget_jpy("￥15,000～") == 15000
    assert _extract_min_budget_jpy(None) is None
    assert _extract_min_budget_jpy("-") is None

    # 2. Foodie score calculations
    store_tabelog = {"tabelog_rating": 3.75, "rating": 4.5, "distance_meters": 300}
    assert calculate_foodie_score(store_tabelog) == pytest.approx(3.75)

    # Google fallback cap at 4.4 and scale 0.65 (cap 3.05)
    store_google = {"rating": 4.8, "distance_meters": 300}
    store_google_44 = {"rating": 4.4, "distance_meters": 300}
    assert calculate_foodie_score(store_google) == pytest.approx(calculate_foodie_score(store_google_44))
    assert calculate_foodie_score(store_google) <= 3.05

    # Distance penalty
    store_far = {"tabelog_rating": 3.6, "distance_meters": 2000}
    assert calculate_foodie_score(store_far) == pytest.approx(3.6 - 0.12)

    # Wide area exemption
    store_wide = {"tabelog_rating": 3.6, "distance_meters": 2000, "origin_name": "箱根"}
    assert calculate_foodie_score(store_wide) == pytest.approx(3.6)


def test_deterministic_constraints():
    stores = [
        {"name_ja": "銀座 特選牛排", "price_dinner": "￥10,000～￥19,999", "open_now": True},
        {"name_ja": "平價 拉麵店", "price_dinner": "～￥999", "open_now": True},
        {"name_ja": "深夜 串燒居酒屋", "price_dinner": "￥3,000～￥3,999", "open_now": False},
    ]

    # Test dietary constraint & budget constraint
    res = apply_deterministic_constraints(
        stores=stores,
        modifier="不吃牛 預算1000日圓內",
        budget_max_jpy=1000,
    )
    # Store 1 violated both beef & budget
    assert res[0].get("constraint_violation") is True
    # Store 2 within budget and no beef
    assert res[1].get("constraint_violation") is None
    # Store 3 over budget
    assert res[2].get("constraint_violation") is True


def test_ranking_and_deduplication():
    stores = [
        {"name_ja": "壽司大", "tabelog_rating": 3.8, "tabelog_url": "http://tabelog.com/1"},
        {"name_ja": "壽司 大", "tabelog_rating": 3.9, "tabelog_url": "http://tabelog.com/1"},  # duplicate url & name
        {"name_ja": "天婦羅 近藤", "tabelog_rating": 3.7, "tabelog_url": "http://tabelog.com/2"},
        {"name_ja": "拉麵 一蘭", "tabelog_rating": 3.5, "tabelog_url": "http://tabelog.com/3"},
        {"name_ja": "居酒屋 鳥貴族", "tabelog_rating": 3.2, "tabelog_url": "http://tabelog.com/4"},
    ]

    top = rank_and_select_top_stores(stores, top_k=3)
    assert len(top) == 3
    assert top[0]["rank_badge"] == "🥇"
    assert top[1]["rank_badge"] == "🥈"
    assert top[2]["rank_badge"] == "🥉"
    assert top[0]["name_ja"] == "壽司 大"
    assert top[1]["name_ja"] == "天婦羅 近藤"
    assert top[2]["name_ja"] == "拉麵 一蘭"


@pytest.mark.asyncio
async def test_orchestrator_semaphore_bounded_full_enrichment():
    """Verify State 3 enriches all up to 6 candidates concurrently with Semaphore(4) bound."""
    import asyncio

    active = 0
    max_active = 0

    async def fake_enrich(place, area_hint=None):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return {**place, "tabelog_rating": 3.55}

    fsm = MagicMock()
    fsm._enrich_single_place = AsyncMock(side_effect=fake_enrich)
    fsm.rank_and_select_top_stores = MagicMock(side_effect=lambda stores, top_k=3: stores[:top_k])
    fsm.places_client = MagicMock(geocode_location=AsyncMock(return_value=None))
    fsm.tabelog_scraper = MagicMock(fetch_tabelog_reviews=None)
    fsm.trace_logger = AsyncMock()

    places = [{"id": f"p{i}", "name": f"店{i}"} for i in range(6)]
    top = await execute_state_3_parallel_tabelog(fsm, places, area_hint="東京")
    assert len(top) == 3
    assert fsm._enrich_single_place.await_count == 6
    assert max_active <= 4


@pytest.mark.asyncio
async def test_cache_helpers_and_poison_detection():
    # Cache completeness check
    complete_cache = {
        "tabelog_rating": 3.6,
        "tabelog_url": "https://tabelog.com/tokyo/A1311/A131103/13000000/",
        "review_count": 50,
        "price_lunch": "～￥999",
    }
    assert _is_tabelog_cache_complete(complete_cache) is True

    incomplete_cache = {
        "tabelog_rating": 3.6,
        "tabelog_url": "https://tabelog.com/tokyo/A1311/A131103/13000000/",
        "review_count": None,
        "price_lunch": None,
    }
    assert _is_tabelog_cache_complete(incomplete_cache) is False

    # Test _build_cached_tabelog_result and _build_seed_result
    cached_built = _build_cached_tabelog_result(
        {"name": "咖啡店", "google_place_id": "p1"},
        complete_cache,
        area_hint="藏前",
    )
    assert cached_built["tabelog_rating"] == 3.6
    assert cached_built["rating_source"] == "Tabelog"

    seed_complete = {
        "name": "種子店",
        "tabelog_seed": True,
        "tabelog_rating": 3.65,
        "review_count": 100,
        "price_dinner": "￥2,000～￥2,999",
        "tabelog_url": "https://tabelog.com/tokyo/seed/",
    }
    seed_res, seed_fallback = check_seed_store(seed_complete, area_hint="澀谷")
    assert seed_res is not None
    assert seed_res["tabelog_rating"] == 3.65

    seed_built = _build_seed_result(seed_complete, area_hint="澀谷")
    assert seed_built["rating_source"] == "Tabelog"

    # Poison cache detection (prefecture mismatch: Kanagawa vs Tokyo)
    fsm = MagicMock()
    dirty_cache = {
        "id": uuid4(),
        "tabelog_rating": 3.7,
        "tabelog_url": "https://tabelog.com/tokyo/A1311/A131103/13000000/",
        "updated_at": datetime.now(timezone.utc),
        "review_count": 50,
        "price_lunch": "～￥999",
    }
    fsm.restaurant_repo = MagicMock(
        get_by_google_place_id=AsyncMock(return_value=dirty_cache),
        delete_by_id=AsyncMock(),
    )
    place_kanagawa = {
        "google_place_id": "p_dirty",
        "name": "橫濱拉麵",
        "address": "神奈川県横浜市西区南幸1-1-1",
    }
    cached_res, incomplete_fallback = await check_cached_store(fsm, place_kanagawa)
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        await asyncio.gather(*pending)
    # Poisoned cache is invalidated
    assert cached_res is None


@pytest.mark.asyncio
async def test_geocoding_station_hydration():
    fsm = MagicMock()
    fsm.places_client = MagicMock(geocode_location=AsyncMock(return_value=(35.704, 139.792)))

    top_stores = [
        {
            "name": "咖啡店",
            "lat": 35.705,
            "lng": 139.793,
            "nearest_station_desc": "鄰近「蔵前站」",
            "walking_desc": "鄰近「蔵前站」",
        }
    ]

    await _hydrate_station_walking_distances(fsm, top_stores)
    assert "從「蔵前站」步行約" in top_stores[0]["walking_desc"]
    assert "m)" in top_stores[0]["walking_desc"]

    # Align walking description directly
    aligned = _align_walking_and_station(
        {"name": "小店", "distance_meters": 150, "walk_time_minutes": 2},
        nearest_station="從「澀谷站」步行約 2 分鐘",
    )
    assert "澀谷站" in aligned["walking_desc"]


@pytest.mark.asyncio
async def test_reviews_indexing_and_enrichment():
    fsm = MagicMock()
    fsm.vector_repo = AsyncMock()
    fsm.vector_repo.get_reviews_by_restaurant_id.return_value = []
    fsm.vector_repo.batch_insert_reviews.return_value = None
    fsm.vector_repo.search_similar_reviews.return_value = []

    fsm.embedding_service = AsyncMock()
    fsm.embedding_service.get_embeddings_batch.return_value = [[0.1, 0.2]]
    fsm.embedding_service.get_embedding.return_value = [0.1, 0.2]

    reviews = [{"text": "好吃", "original_text": "おいしい"}]
    rid = uuid4()
    await index_store_reviews(fsm, rid, reviews)
    fsm.vector_repo.batch_insert_reviews.assert_called_once()

    # Semantic rerank reviews
    stores = [{"restaurant_id": str(rid), "name": "店"}]
    reranked = await semantic_rerank_reviews(fsm, "濃郁湯頭", stores)
    assert reranked == stores

    # Foodie reviews enrichment
    fsm._index_store_reviews = AsyncMock()
    fsm.tabelog_scraper = MagicMock(
        fetch_tabelog_reviews=AsyncMock(return_value=[{"text": "濃郁豚骨湯頭超讚"}]),
    )
    store = {
        "name_ja": "一雙",
        "tabelog_url": "https://tabelog.com/fukuoka/123",
        "restaurant_id": str(rid),
    }
    await enrich_foodie_reviews(fsm, store)
    pending_enrich = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending_enrich:
        await asyncio.gather(*pending_enrich)
    assert store["foodie_review_highlight"] == "濃郁豚骨湯頭超讚"
    assert store["foodie_review_source"] == "tabelog"


@pytest.mark.asyncio
async def test_enrich_single_place_google_fallback():
    fsm = MagicMock()
    fsm.restaurant_repo = MagicMock(
        get_by_google_place_id=AsyncMock(return_value=None),
        save_restaurant_with_tabelog=AsyncMock(return_value=uuid4()),
    )
    fsm.tabelog_scraper = MagicMock(search_tabelog_candidates=AsyncMock(return_value=[]))
    fsm.entity_resolver = MagicMock()

    place = {"google_place_id": "p_fallback", "name": "無知名小店", "rating": 4.1}
    res = await enrich_single_place(fsm, place, area_hint="新宿")
    assert res["rating_source"] == "Google"
    assert res["tabelog_rating"] is None


@pytest.mark.asyncio
async def test_orchestrator_execution_end_to_end():
    fsm = MagicMock()
    fsm._enrich_single_place = AsyncMock(
        side_effect=lambda p, area_hint=None: {
            **p,
            "tabelog_rating": 3.55 if "1" in p["id"] or "2" in p["id"] else 3.2,
            "tabelog_url": f"https://tabelog.com/{p['id']}",
        }
    )
    fsm.rank_and_select_top_stores = MagicMock(
        side_effect=lambda stores, top_k=3: sorted(stores, key=calculate_foodie_score, reverse=True)[:top_k]
    )
    fsm.tabelog_scraper = MagicMock(fetch_tabelog_reviews=None)
    fsm.places_client = None
    fsm.trace_logger = AsyncMock()

    places = [
        {"id": "p1", "name": "店1", "rating": 4.5},
        {"id": "p2", "name": "店2", "rating": 4.4},
        {"id": "p3", "name": "店3", "rating": 4.2},
        {"id": "p4", "name": "店4", "rating": 4.0},
        {"id": "p5", "name": "店5", "rating": 4.1},
    ]

    # Full enrichment without early stopping -> all 5 places enriched!
    top = await execute_state_3_parallel_tabelog(fsm, places, area_hint="東京")
    assert len(top) == 3
    assert fsm._enrich_single_place.await_count == 5
    fsm.trace_logger.log_step.assert_called_once()


@pytest.mark.asyncio
async def test_geocoding_station_hydration_robustness_adversarial():
    """對抗性驗證：geocoding 回傳各類異常型別（list、bool、tuple-3、None、字典、例外）皆不當機且安全略過"""
    fsm = MagicMock()

    # 1. 回傳 list 型別 [lat, lng]：應正確相容並水合步行時間
    fsm.places_client = MagicMock(geocode_location=AsyncMock(return_value=[35.704, 139.792]))
    store_list_coords = [
        {
            "name": "咖啡店",
            "lat": 35.705,
            "lng": 139.793,
            "nearest_station_desc": "鄰近「蔵前站」",
            "walking_desc": "鄰近「蔵前站」",
        }
    ]
    await _hydrate_station_walking_distances(fsm, store_list_coords)
    assert "從「蔵前站」步行約" in store_list_coords[0]["walking_desc"]

    # 2. 回傳布林值 (True, False)：不可被誤當作數字計算，安全略過
    fsm.places_client.geocode_location = AsyncMock(return_value=(True, False))
    store_bool = [
        {
            "name": "布林測試店",
            "lat": 35.705,
            "lng": 139.793,
            "nearest_station_desc": "鄰近「蔵前站」",
            "walking_desc": "鄰近「蔵前站」",
        }
    ]
    await _hydrate_station_walking_distances(fsm, store_bool)
    assert store_bool[0]["walking_desc"] == "鄰近「蔵前站」"

    # 3. 回傳 3 元組 (lat, lng, alt)：安全略過
    fsm.places_client.geocode_location = AsyncMock(return_value=(35.704, 139.792, 100))
    store_3tuple = [
        {
            "name": "3元組店",
            "lat": 35.705,
            "lng": 139.793,
            "nearest_station_desc": "鄰近「蔵前站」",
            "walking_desc": "鄰近「蔵前站」",
        }
    ]
    await _hydrate_station_walking_distances(fsm, store_3tuple)
    assert store_3tuple[0]["walking_desc"] == "鄰近「蔵前站」"

    # 4. 回傳包含 None 的 tuple (35.704, None)：安全略過
    fsm.places_client.geocode_location = AsyncMock(return_value=(35.704, None))
    store_none_tuple = [
        {
            "name": "部分空值店",
            "lat": 35.705,
            "lng": 139.793,
            "nearest_station_desc": "鄰近「蔵前站」",
            "walking_desc": "鄰近「蔵前站」",
        }
    ]
    await _hydrate_station_walking_distances(fsm, store_none_tuple)
    assert store_none_tuple[0]["walking_desc"] == "鄰近「蔵前站」"

    # 5. 回傳 dict 或拋出例外：安全略過
    fsm.places_client.geocode_location = AsyncMock(side_effect=RuntimeError("Geocoding quota exceeded"))
    store_err = [
        {
            "name": "例外店",
            "lat": 35.705,
            "lng": 139.793,
            "nearest_station_desc": "鄰近「蔵前站」",
            "walking_desc": "鄰近「蔵前站」",
        }
    ]
    await _hydrate_station_walking_distances(fsm, store_err)
    assert store_err[0]["walking_desc"] == "鄰近「蔵前站」"

    # 6. store 的座標為非數字或 None：安全略過
    fsm.places_client.geocode_location = AsyncMock(return_value=(35.704, 139.792))
    store_bad_coords = [
        {
            "name": "無座標店",
            "lat": "not_a_number",
            "lng": None,
            "nearest_station_desc": "鄰近「蔵前站」",
            "walking_desc": "鄰近「蔵前站」",
        }
    ]
    await _hydrate_station_walking_distances(fsm, store_bad_coords)
    assert store_bad_coords[0]["walking_desc"] == "鄰近「蔵前站」"


@pytest.mark.asyncio
async def test_orchestrator_candidate_count_edge_cases():
    """對抗性驗證：候選列表介於 0 至 3 家（未滿 Tier 1 容量 4 家）時，早停與編排完全正常"""
    fsm = MagicMock()
    fsm._enrich_single_place = AsyncMock(
        side_effect=lambda p, area_hint=None: {
            **p,
            "tabelog_rating": p.get("rating", 3.5),
            "tabelog_url": f"https://tabelog.com/{p.get('id', 'x')}",
        }
    )
    fsm.rank_and_select_top_stores = MagicMock(
        side_effect=lambda stores, top_k=3: sorted(stores, key=calculate_foodie_score, reverse=True)[:top_k]
    )
    fsm.tabelog_scraper = MagicMock(fetch_tabelog_reviews=None)
    fsm.places_client = None
    fsm.trace_logger = AsyncMock()

    # 1. 0 家店 (空列表)
    res_0 = await execute_state_3_parallel_tabelog(fsm, [])
    assert res_0 == []
    assert fsm._enrich_single_place.await_count == 0

    # 2. 1 家店
    fsm._enrich_single_place.reset_mock()
    res_1 = await execute_state_3_parallel_tabelog(fsm, [{"id": "p1", "rating": 3.8}])
    assert len(res_1) == 1
    assert fsm._enrich_single_place.await_count == 1

    # 3. 2 家店
    fsm._enrich_single_place.reset_mock()
    res_2 = await execute_state_3_parallel_tabelog(fsm, [{"id": "p1", "rating": 3.8}, {"id": "p2", "rating": 3.7}])
    assert len(res_2) == 2
    assert fsm._enrich_single_place.await_count == 2

    # 4. 3 家店 (剛好等於 top_k=3，但小於 Tier 1 容量 4)
    fsm._enrich_single_place.reset_mock()
    res_3 = await execute_state_3_parallel_tabelog(
        fsm,
        [{"id": "p1", "rating": 3.8}, {"id": "p2", "rating": 3.7}, {"id": "p3", "rating": 3.6}],
    )
    assert len(res_3) == 3
    assert fsm._enrich_single_place.await_count == 3

    # 5. 5 家店且低分 (<2 家高分) -> 必須觸發 Tier 2 補全全部 5 家
    fsm._enrich_single_place.reset_mock()
    low_places = [
        {"id": "p1", "rating": 3.2},
        {"id": "p2", "rating": 3.1},
        {"id": "p3", "rating": 3.3},
        {"id": "p4", "rating": 3.0},
        {"id": "p5", "rating": 3.4},
    ]
    res_5_low = await execute_state_3_parallel_tabelog(fsm, low_places)
    assert len(res_5_low) == 3
    assert fsm._enrich_single_place.await_count == 5

    # 6. 5 家店且高分 (>=2 家高分) 但有預算上限 (budget_max_jpy) -> 必須觸發 Tier 2 撈出平價店
    fsm._enrich_single_place.reset_mock()
    high_places = [
        {"id": "p1", "rating": 3.8},
        {"id": "p2", "rating": 3.7},
        {"id": "p3", "rating": 3.6},
        {"id": "p4", "rating": 3.5},
        {"id": "p5", "rating": 3.4},
    ]
    res_5_budget = await execute_state_3_parallel_tabelog(fsm, high_places, budget_max_jpy=1500)
    assert len(res_5_budget) == 3
    assert fsm._enrich_single_place.await_count == 5


@pytest.mark.asyncio
async def test_crawler_resolution_adversarial_malformed_returns():
    """對抗性驗證：爬蟲與 Resolver 回傳非 dict / 非 list (例如 AsyncMock、None、非預期型別) 時防禦不噴警告與當機"""
    fsm = MagicMock()
    # 1. search_tabelog_candidates 回傳非 list (如 AsyncMock 物件)
    fsm.tabelog_scraper = MagicMock(
        search_tabelog_candidates=AsyncMock(return_value=AsyncMock()),
        search_via_grounding=AsyncMock(return_value=None),
        scrape_restaurant_details=AsyncMock(return_value=None),
    )
    # 2. resolve_entity 回傳非 dict (如 AsyncMock 物件)
    fsm.entity_resolver = MagicMock(
        resolve_entity=AsyncMock(return_value=AsyncMock()),
    )
    fsm.restaurant_repo = MagicMock(
        save_restaurant_with_tabelog=AsyncMock(return_value=None),
    )

    place = {
        "google_place_id": "p_malformed",
        "name": "奇異小店",
        "address": "東京都新宿区歌舞伎町1-1",
        "phone": "+81-3-1234-5678",
    }
    # 呼叫爬蟲解析，確認安全回傳 None 且絕不拋出例外或 unawaited coroutine warning
    result = await search_and_resolve_tabelog(fsm, place, area_hint="新宿")
    assert result is None
