# -*- coding: utf-8 -*-
"""Plan 4 Follow-up Output Validation, Tool Loop & Trip-Scoping 0ms 離線測試"""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from src.agent.pipeline import FoodRecommendationFSM
from src.agent.validator import sanitize_single_store_ratings, validate_follow_up_response


def test_validate_follow_up_response_and_single_store_rating_sanitization():
    store = {"name": "麵匠 竹虎", "name_ja": "麺匠 竹虎", "tabelog_rating": 3.55, "google_rating": 4.2}

    # 1. 單店純文字回覆（無標題前綴）也能矯正評分，且容許短料理專有名詞如 お通し
    text_with_drift = "這家店 Tabelog 評分 3.99 分，Google 4.8 分，有提供 お通し 小菜。"
    sanitized, errors = validate_follow_up_response(text_with_drift, [store])
    assert errors == []
    assert "3.55" in sanitized
    assert "4.2" in sanitized
    assert "3.99" not in sanitized

    # 2. 偽造搜尋連結與連續 8 字以上日文假名應被攔截
    bad_url = "請看這裡 [搜尋](https://www.google.com/maps/search/ramen)"
    _, url_errs = validate_follow_up_response(bad_url, [store])
    assert any("搜尋連結" in e for e in url_errs)

    bad_kana = "這家店的評論說：チャーハンと餃子が本当に絶品でした！"
    _, kana_errs = validate_follow_up_response(bad_kana, [store])
    assert any("日文" in e for e in kana_errs)

    # 直接呼叫 sanitize_single_store_ratings
    assert "3.55" in sanitize_single_store_ratings("Tabelog 約 3.90 分", store)


@pytest.mark.asyncio
async def test_follow_up_tool_loop_detail_lookup_and_nearby_pairing():
    rid = uuid4()
    store = {
        "restaurant_id": str(rid),
        "name": "風雲兒",
        "name_ja": "風雲児",
        "lat": 35.687,
        "lng": 139.696,
        "tabelog_rating": 3.72,
    }

    # 1. detail_lookup 命中 vector_repo 中的繁中評論
    fsm = FoodRecommendationFSM(
        places_client=MagicMock(),
        tabelog_scraper=MagicMock(),
        entity_resolver=MagicMock(),
        llm_client=MagicMock(),
        session_repo=MagicMock(),
        vector_repo=MagicMock(),
    )
    fsm.session_repo.get_last_recommended_stores = AsyncMock(return_value=[store])
    fsm.vector_repo.get_reviews_by_restaurant_id = AsyncMock(
        return_value=[{"review_text_zh": "店內走道寬敞，店員會協助安置嬰兒推車。"}]
    )
    fsm.llm_client.call_structured_json = AsyncMock(
        return_value=({"action": "detail_lookup", "tool_query": "嬰兒推車"}, "test", 10)
    )
    fsm.llm_client.call_text = AsyncMock(return_value=("根據在地評論，店內走道寬敞且對嬰兒推車友善。", "test", 30))

    ans_detail = await fsm.execute_follow_up(
        chat_id=123,
        parsed_intent={"intent": "follow_up", "target_index": 1},
        user_message="第 1 家帶嬰兒推車方便嗎？",
    )
    assert "嬰兒推車友善" in ans_detail
    fsm.vector_repo.get_reviews_by_restaurant_id.assert_awaited_once()

    # 2. nearby_pairing 呼叫 places_client.search_nearby(radius=400)
    fsm.places_client.search_nearby = AsyncMock(
        return_value=[{"name": "Blue Bottle Shinjuku", "rating": 4.4, "address": "新宿4丁目"}]
    )
    fsm.llm_client.call_structured_json = AsyncMock(
        return_value=({"action": "nearby_pairing", "tool_query": "咖啡廳"}, "test", 10)
    )
    fsm.llm_client.call_text = AsyncMock(return_value=("附近步行可達 Blue Bottle Shinjuku 喝咖啡續攤。", "test", 30))

    ans_nearby = await fsm.execute_follow_up(
        chat_id=123,
        parsed_intent={"intent": "follow_up", "target_index": 1},
        user_message="吃完附近有什麼咖啡廳可以續攤？",
    )
    assert "Blue Bottle Shinjuku" in ans_nearby
    fsm.places_client.search_nearby.assert_awaited_once()

    # 3. call_structured_json 失敗退回 intent 兜底字典時，安全降級直接回答
    fsm.llm_client.call_structured_json = AsyncMock(return_value=({"intent": "search", "location": None}, "test", 0))
    fsm.llm_client.call_text = AsyncMock(return_value=("該店 Tabelog 評分為 3.72 分。", "test", 20))
    ans_safe = await fsm.execute_follow_up(
        chat_id=123,
        parsed_intent={"intent": "follow_up", "target_index": 1},
        user_message="第 1 家幾分？",
    )
    assert "3.72" in ans_safe


@pytest.mark.asyncio
async def test_trip_scoping_vs_pure_disambiguation_and_turn_cap():
    fsm = FoodRecommendationFSM(
        places_client=MagicMock(),
        tabelog_scraper=MagicMock(),
        entity_resolver=MagicMock(),
        llm_client=MagicMock(),
        session_repo=MagicMock(),
    )
    fsm.session_repo.get_session = AsyncMock(return_value=None)
    fsm.session_repo.get_long_term_preferences = AsyncMock(return_value={})
    fsm.session_repo.get_working_memory = AsyncMock(return_value={})
    fsm.session_repo.update_working_memory = AsyncMock(return_value={})

    # 1. 複合模糊需求 -> 進入 trip-scoping (而非固定純地名反問)
    fsm.llm_client.call_structured_json = AsyncMock(
        return_value=(
            {
                "intent": "search",
                "location": "東京",
                "cuisine": "清淡午餐",
                "is_location_ambiguous": True,
                "suggested_sub_areas": [
                    {"name": "新宿", "desc": "交通樞紐", "search_loc": "新宿"},
                    {"name": "淺草", "desc": "下町風情", "search_loc": "淺草"},
                ],
            },
            "test",
            20,
        )
    )
    fsm.llm_client.call_text = AsyncMock(
        return_value=("請問您與長輩主要會在東京哪個車站一帶活動，預算大概抓多少呢？", "test", 25)
    )

    parsed_scoping = await fsm.execute_state_1_intent(
        "我下週帶長輩去東京，不想排隊、想吃清淡一點的午餐",
        user_location=None,
        conversation_history=[],
        chat_id=999,
    )
    assert parsed_scoping["intent"] == "need_location"
    assert "車站" in parsed_scoping["need_location_prompt"]
    fsm.session_repo.update_working_memory.assert_awaited_once()

    # 2. 達 2 輪上限後強制退回標準消歧或搜尋，防止無限追問
    fsm.session_repo.get_working_memory = AsyncMock(return_value={"trip_scoping": {"scoping_turns": 2}})
    parsed_capped = await fsm.execute_state_1_intent(
        "我下週帶長輩去東京，不想排隊、想吃清淡一點的午餐",
        user_location=None,
        conversation_history=[],
        chat_id=999,
    )
    assert "具體區域" in parsed_capped["need_location_prompt"]
