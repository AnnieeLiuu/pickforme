import json
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from src.agent.llm_client import LLMClient
from src.agent.schemas import (
    GeocodeArgs,
    IntentOutput,
    SearchNearbyArgs,
    TabelogInfoArgs,
    extract_preferences_from_text,
)
from src.agent.tools import AgentToolExecutor


def test_intent_output_schema_validation():
    valid = IntentOutput.model_validate(
        {
            "intent": "follow_up",
            "target_index": 2,
            "question": "有開嗎",
        }
    )
    assert valid.intent == "follow_up"
    assert valid.target_index == 2

    with pytest.raises(ValidationError):
        IntentOutput.model_validate({"intent": "invalid_intent_type"})

    with pytest.raises(ValidationError):
        IntentOutput.model_validate({"intent": "follow_up", "target_index": 9})


def test_search_nearby_args_validates_japan_bounds():
    valid = SearchNearbyArgs.model_validate({"lat": 35.68, "lng": 139.76, "radius": 800, "keyword": "拉麵"})
    assert valid.radius == 800

    with pytest.raises(ValidationError):
        # 緯度超出日本範圍
        SearchNearbyArgs.model_validate({"lat": 10.0, "lng": 139.76})


def test_geocode_and_tabelog_args_validation():
    valid_geo = GeocodeArgs.model_validate({"query": "新宿駅"})
    assert valid_geo.query == "新宿駅"
    with pytest.raises(ValidationError):
        GeocodeArgs.model_validate({"query": "   "})

    valid_tb = TabelogInfoArgs.model_validate({"google_place_id": "ChIJ123"})
    assert valid_tb.google_place_id == "ChIJ123"
    assert valid_tb.lat == 0.0
    assert valid_tb.lng == 0.0
    with pytest.raises(ValidationError):
        TabelogInfoArgs.model_validate({"google_place_id": ""})


def test_extract_preferences_from_text():
    prefs = extract_preferences_from_text("我想找新宿的燒肉，但我不吃牛，而且要可以刷卡或用PayPay")
    assert "不吃牛" in prefs["dietary_restrictions"]
    assert "credit_card" in prefs["payment_preferences"]
    assert "paypay" in prefs["payment_preferences"]


@pytest.mark.asyncio
async def test_llm_client_self_correction_loop_heals_invalid_schema():
    client = LLMClient()
    # 第一次回傳非法 target_index=99，第二次自癒重試回傳合法 JSON
    client.call_text = AsyncMock(
        side_effect=[
            ('{"intent": "follow_up", "target_index": 99}', "groq", 50),
            ('{"intent": "follow_up", "target_index": 2, "question": "第二家有開嗎"}', "groq", 60),
        ]
    )
    messages = [{"role": "user", "content": "第二家有開嗎"}]
    data, model, tokens = await client.call_structured_json(messages, schema=IntentOutput, max_retries=1)
    assert data["intent"] == "follow_up"
    assert data["target_index"] == 2
    assert tokens == 110
    assert client.call_text.call_count == 2


@pytest.mark.asyncio
async def test_llm_client_self_correction_fallback_when_all_attempts_fail():
    client = LLMClient()
    # 兩次都回傳非法 schema，最終不應拋出未處理的 ValidationError，而是退回保底 search
    client.call_text = AsyncMock(
        side_effect=[
            ('{"intent": "invalid_1"}', "groq", 30),
            ('{"intent": "invalid_2"}', "groq", 40),
        ]
    )
    messages = [{"role": "user", "content": "新宿有什麼好吃的拉麵"}]
    data, model, tokens = await client.call_structured_json(messages, schema=IntentOutput, max_retries=1)
    assert data["intent"] == "search"
    assert tokens == 70
    assert client.call_text.call_count == 2


@pytest.mark.asyncio
async def test_tool_executor_rejects_invalid_args_with_structured_error():
    executor = AgentToolExecutor(places_client=AsyncMock())
    res_str = await executor.execute_tool("search_nearby", {"lat": 10.0, "lng": 139.76})
    res = json.loads(res_str)
    assert "error" in res
