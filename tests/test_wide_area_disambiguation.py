# -*- coding: utf-8 -*-
"""Unit tests for Stage 1 dynamic area disambiguation and eradication of static prefecture map.

Verifies:
1. Eradication of PREFECTURE_DISAMBIGUATION_MAP from src.agent.stages.intent.
2. Dynamic LLM-driven disambiguation when is_location_ambiguous=True with >= 2 sub-areas.
3. Normal pass-through when is_location_ambiguous=False.
4. Fail-open behavior when is_location_ambiguous=True but suggested_sub_areas < 2.
5. Backward-compatibility of is_prefecture_level_area without crashing.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
import src.agent.stages.intent as intent_stage
from src.agent.pipeline import FoodRecommendationFSM
from src.agent.schemas import SubAreaOption


def test_eradication_of_prefecture_disambiguation_map():
    """Test 1: Verify PREFECTURE_DISAMBIGUATION_MAP is eradicated from module."""
    assert not hasattr(intent_stage, "PREFECTURE_DISAMBIGUATION_MAP"), (
        "PREFECTURE_DISAMBIGUATION_MAP must be completely eradicated according to AGENTS.md charter"
    )


@pytest.mark.asyncio
async def test_dynamic_disambiguation_when_ambiguous():
    """Test 2: When location is ambiguous with >= 2 sub-areas, triggers early disambiguation response."""
    sub_areas = [
        SubAreaOption(name="熱海溫泉街", desc="海景、溫泉布丁", search_loc="熱海溫泉"),
        SubAreaOption(name="修善寺溫泉街", desc="百年古湯、竹林", search_loc="修善寺"),
    ]
    mock_parsed = {
        "intent": "search",
        "location": "靜岡溫泉街",
        "cuisine": "溫泉饅頭",
        "is_location_ambiguous": True,
        "ambiguity_reason": "靜岡境內涵蓋伊豆、熱海、修善寺等多個獨立溫泉區",
        "suggested_sub_areas": sub_areas,
    }

    session_repo = MagicMock()
    session_repo.update_working_memory = AsyncMock()
    places_client = MagicMock()
    places_client.geocode_location = AsyncMock(return_value=(35.0, 139.0))

    fsm = FoodRecommendationFSM(
        llm_client=MagicMock(call_structured_json=AsyncMock(return_value=(mock_parsed, "mock-model", 50))),
        places_client=places_client,
        session_repo=session_repo,
        trace_logger=MagicMock(log_step=AsyncMock(), start_run=AsyncMock(), finish_run=AsyncMock()),
    )

    parsed = await fsm.execute_state_1_intent(
        user_message="我想吃靜岡溫泉街的溫泉饅頭",
        user_location=None,
        conversation_history=[],
        chat_id=12345,
    )

    # Check parsed contract
    assert parsed["intent"] == "need_location"
    assert "need_location_prompt" in parsed
    prompt = parsed["need_location_prompt"]
    assert "靜岡溫泉街" in prompt
    assert "熱海溫泉街" in prompt
    assert "海景、溫泉布丁" in prompt
    assert "修善寺溫泉街" in prompt
    assert "百年古湯、竹林" in prompt

    # Verify pipeline.early_response is set
    assert hasattr(fsm, "early_response")
    assert fsm.early_response == prompt

    # Verify working memory is updated with suggested_locations and pending_cuisine
    session_repo.update_working_memory.assert_awaited_once_with(
        chat_id=12345,
        suggested_locations=["熱海溫泉", "修善寺"],
        pending_cuisine="溫泉饅頭",
    )

    # Verify run_pipeline early completion without calling geocoding
    fsm.llm_client.call_structured_json = AsyncMock(return_value=(mock_parsed, "mock-model", 50))
    res = await fsm.run_pipeline(chat_id=12345, user_message="我想吃靜岡溫泉街的溫泉饅頭")
    assert res == prompt
    places_client.geocode_location.assert_not_called()


@pytest.mark.asyncio
async def test_normal_flow_when_not_ambiguous():
    """Test 3: When location is not ambiguous, pipeline proceeds normally without early stopping."""
    mock_parsed = {
        "intent": "search",
        "location": "箱根湯本",
        "cuisine": "蕎麥麵",
        "is_location_ambiguous": False,
        "suggested_sub_areas": [],
    }

    session_repo = MagicMock()
    session_repo.update_working_memory = AsyncMock()
    places_client = MagicMock()
    places_client.geocode_location = AsyncMock(return_value=(35.23, 139.10))

    fsm = FoodRecommendationFSM(
        llm_client=MagicMock(call_structured_json=AsyncMock(return_value=(mock_parsed, "mock-model", 50))),
        places_client=places_client,
        session_repo=session_repo,
        trace_logger=MagicMock(log_step=AsyncMock(), start_run=AsyncMock(), finish_run=AsyncMock()),
    )

    parsed = await fsm.execute_state_1_intent(
        user_message="箱根湯本 蕎麥麵",
        user_location=None,
        conversation_history=[],
        chat_id=12345,
    )

    assert parsed["intent"] == "search"
    assert parsed["coords"] == (35.23, 139.10)
    assert getattr(fsm, "early_response", None) is None
    places_client.geocode_location.assert_awaited_once_with("箱根湯本")


@pytest.mark.asyncio
async def test_fail_open_when_ambiguous_without_sub_areas():
    """Test 4: Fail-open when is_location_ambiguous=True but suggested_sub_areas is empty."""
    mock_parsed = {
        "intent": "search",
        "location": "靜岡溫泉街",
        "cuisine": "鰻魚飯",
        "is_location_ambiguous": True,
        "suggested_sub_areas": [],  # LLM failed to produce sub-areas
    }

    places_client = MagicMock()
    places_client.geocode_location = AsyncMock(return_value=(34.97, 138.38))

    fsm = FoodRecommendationFSM(
        llm_client=MagicMock(call_structured_json=AsyncMock(return_value=(mock_parsed, "mock-model", 50))),
        places_client=places_client,
        session_repo=MagicMock(),
        trace_logger=MagicMock(log_step=AsyncMock(), start_run=AsyncMock(), finish_run=AsyncMock()),
    )

    parsed = await fsm.execute_state_1_intent(
        user_message="靜岡溫泉街 鰻魚飯",
        user_location=None,
        conversation_history=[],
        chat_id=12345,
    )

    # Should fail open and proceed with original location
    assert parsed["intent"] == "search"
    assert parsed["coords"] == (34.97, 138.38)
    places_client.geocode_location.assert_awaited_once_with("靜岡溫泉街")


def test_is_prefecture_level_area_backwards_compatibility():
    """Test 5: Backward compatibility helper is_prefecture_level_area exists and doesn't crash."""
    assert hasattr(intent_stage, "is_prefecture_level_area")
    fn = getattr(intent_stage, "is_prefecture_level_area")

    # None or empty
    assert fn(None) == (False, None, None)
    assert fn("") == (False, None, None)

    # Station / City suffix
    assert fn("靜岡車站") == (False, None, None)
    assert fn("新宿區") == (False, None, None)

    # Broad prefecture names
    b1, name1, _ = fn("靜岡縣")
    assert b1 is True and name1 == "靜岡"
    b2, name2, _ = fn("北海道")
    assert b2 is True and name2 == "北海道"
