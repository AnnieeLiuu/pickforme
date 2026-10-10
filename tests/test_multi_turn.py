from unittest.mock import AsyncMock, MagicMock

import pytest
from src.agent.pipeline import FoodRecommendationFSM
from src.db.repositories.session import SessionRepository


@pytest.mark.asyncio
async def test_session_repository_seen_places_accumulation():
    """測試 SessionRepository 在多次推薦中累加 seen_place_ids 與相容舊格式。"""
    repo = SessionRepository()

    # 模擬 1: 資料庫中原無記錄 (None)
    repo.get_session = AsyncMock(return_value=None)
    seen_ids = await repo.get_seen_place_ids(12345)
    assert seen_ids == set()

    # 模擬 2: 資料庫中有舊格式 list [{"google_place_id": "p1", "name": "店1"}]
    repo.get_session = AsyncMock(return_value={"last_recommended_ids": [{"google_place_id": "p1", "name": "店1"}]})
    seen_ids = await repo.get_seen_place_ids(12345)
    assert seen_ids == {"p1"}
    stores = await repo.get_last_recommended_stores(12345)
    assert len(stores) == 1
    assert stores[0]["google_place_id"] == "p1"

    # 模擬 3: 資料庫中有新格式 dict
    repo.get_session = AsyncMock(
        return_value={
            "last_recommended_ids": {
                "current": [{"google_place_id": "p2", "name": "店2"}],
                "accumulated_seen_ids": ["p1", "p2"],
            }
        }
    )
    seen_ids = await repo.get_seen_place_ids(12345)
    assert seen_ids == {"p1", "p2"}
    stores = await repo.get_last_recommended_stores(12345)
    assert len(stores) == 1
    assert stores[0]["google_place_id"] == "p2"


@pytest.mark.asyncio
async def test_state_1_more_intent_inherits_session_location():
    """測試當使用者表示『還有更多推薦嗎』時，State 1 自動從 session 繼承座標。"""
    mock_llm_client = MagicMock()
    mock_session_repo = MagicMock()
    mock_trace_logger = MagicMock()
    mock_trace_logger.log_step = AsyncMock()

    fsm = FoodRecommendationFSM(
        llm_client=mock_llm_client,
        session_repo=mock_session_repo,
        trace_logger=mock_trace_logger,
    )

    mock_llm_result = {
        "intent": "more",
        "location": None,
        "cuisine": None,
        "modifier": "在地老店",
    }
    mock_llm_client.call_structured_json = AsyncMock(return_value=(mock_llm_result, "groq-llama-3.3-70b", 120))

    mock_session_repo.get_session = AsyncMock(
        return_value={
            "last_location_lat": 35.4437,
            "last_location_lng": 139.6380,
        }
    )

    intent_data = await fsm.execute_state_1_intent(
        user_message="還有更在地的老店嗎？",
        user_location=None,
        conversation_history=None,
        chat_id=99999,
    )

    assert intent_data["intent"] == "more"
    assert intent_data["modifier"] == "在地老店"
    assert intent_data["coords"] == (35.4437, 139.6380)


@pytest.mark.asyncio
async def test_state_2_places_ladder_excludes_seen_and_expands_radius():
    """測試 State 2 階梯搜尋能排除 exclude_place_ids，若候選不足則擴圈至 2500m。"""
    mock_places_client = MagicMock()
    mock_trace_logger = MagicMock()
    mock_trace_logger.log_step = AsyncMock()

    fsm = FoodRecommendationFSM(
        places_client=mock_places_client,
        trace_logger=mock_trace_logger,
    )

    # 模擬 Google Places 搜尋回傳
    async def mock_search_nearby(lat, lng, radius, keyword):
        if radius == 800:
            return [
                {"google_place_id": "p1", "name": "已看過壽喜燒A", "lat": 35.44, "lng": 139.63},
                {"google_place_id": "p2", "name": "新壽喜燒B", "lat": 35.44, "lng": 139.63},
            ]
        elif radius in (1200, 1800):
            return []
        elif radius == 2500:
            return [
                {"google_place_id": "p3", "name": "遠方老店C", "lat": 35.45, "lng": 139.64},
                {"google_place_id": "p4", "name": "遠方老店D", "lat": 35.45, "lng": 139.64},
            ]
        return []

    mock_places_client.search_nearby = AsyncMock(side_effect=mock_search_nearby)

    candidates = await fsm.execute_state_2_places_ladder(
        lat=35.4437,
        lng=139.6380,
        keyword="橫濱 壽喜燒 在地老店",
        origin_name="橫濱",
        exclude_place_ids={"p1"},
    )

    assert len(candidates) >= 3
    cand_ids = [c["google_place_id"] for c in candidates]
    assert "p1" not in cand_ids
    assert "p2" in cand_ids
    assert "p3" in cand_ids
    assert "p4" in cand_ids
