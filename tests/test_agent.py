import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from src.agent.core import AgentEngine
from src.agent.llm_client import LLMClient
from src.agent.tools import TOOL_SCHEMAS, AgentToolExecutor
from src.agent.trace import AgentTraceLogger


def test_tool_schemas_format():
    """驗證 TOOL_SCHEMAS 符合 OpenAI Function Tool 格式規範"""
    assert isinstance(TOOL_SCHEMAS, list)
    assert len(TOOL_SCHEMAS) == 3

    tool_names = {t["function"]["name"] for t in TOOL_SCHEMAS}
    assert "geocode_location" in tool_names
    assert "search_nearby" in tool_names
    assert "get_tabelog_info" in tool_names

    for tool in TOOL_SCHEMAS:
        assert tool["type"] == "function"
        func = tool["function"]
        assert "name" in func
        assert "description" in func
        assert "parameters" in func


@pytest.mark.asyncio
async def test_tool_executor_geocode_location():
    mock_places_client = AsyncMock()
    mock_places_client.geocode_location.return_value = (33.5902, 130.4207)

    executor = AgentToolExecutor(places_client=mock_places_client)
    res_str = await executor.execute_tool("geocode_location", {"query": "博多駅"})

    data = json.loads(res_str)
    assert data["lat"] == 33.5902
    assert data["lng"] == 130.4207
    mock_places_client.geocode_location.assert_called_once_with("博多駅")


@pytest.mark.asyncio
async def test_tool_executor_search_nearby():
    mock_places_client = AsyncMock()
    mock_places_client.search_nearby.return_value = [
        {
            "google_place_id": "p1",
            "name": "一蘭 博多店",
            "lat": 33.5902,
            "lng": 130.4207,
            "google_rating": 4.5,
        }
    ]

    executor = AgentToolExecutor(places_client=mock_places_client)
    res_str = await executor.execute_tool(
        "search_nearby", {"lat": 33.5902, "lng": 130.4207, "radius": 500, "keyword": "拉麵"}
    )

    data = json.loads(res_str)
    assert data["count"] == 1
    assert data["places"][0]["google_place_id"] == "p1"


@pytest.mark.asyncio
async def test_tool_executor_get_tabelog_info_cached():
    mock_repo = AsyncMock()
    mock_repo.get_by_google_place_id.return_value = {
        "google_place_id": "cached_id",
        "tabelog_url": "https://tabelog.com/fukuoka/A4001/A400101/40000001/",
        "name_ja": "一蘭 博多本店",
        "tabelog_rating": 3.65,
        "review_count": 150,
        "price_lunch": "￥1,000～￥1,999",
        "price_dinner": "￥1,000～￥1,999",
        "match_confidence": 1.0,
    }

    executor = AgentToolExecutor(restaurant_repo=mock_repo)
    res_str = await executor.execute_tool("get_tabelog_info", {"google_place_id": "cached_id"})

    data = json.loads(res_str)
    assert data["cached"] is True
    assert data["tabelog_rating"] == 3.65
    assert data["name_ja"] == "一蘭 博多本店"


@pytest.mark.asyncio
async def test_llm_client_groq_primary():
    client = LLMClient()

    with (
        patch("src.config.settings.LLM_PRIMARY_PROVIDER", "groq"),
        patch("src.config.settings.GROQ_API_KEY", "fake_groq_key"),
        patch.object(
            client,
            "_call_openai_compatible_api",
            AsyncMock(return_value=({"role": "assistant", "content": "Groq 極速推薦！"}, 95)),
        ),
    ):
        content, model, tokens = await client.call_text([{"role": "user", "content": "hi"}])
        assert model == "qwen/qwen3.8-27b"
        assert content == "Groq 極速推薦！"
        assert tokens == 95


@pytest.mark.asyncio
async def test_llm_client_failover_gemini_to_groq():
    client = LLMClient()

    with (
        patch("src.config.settings.LLM_PRIMARY_PROVIDER", "gemini"),
        patch.object(client, "_is_ollama_online", AsyncMock(return_value=False)),
        patch("src.config.settings.GEMINI_API_KEY", "fake_gemini_key"),
        patch("src.config.settings.GROQ_API_KEY", "fake_groq_key"),
    ):
        with patch.object(
            client,
            "_call_openai_compatible_api",
            side_effect=[
                Exception("Gemini Rate Limit 429"),  # 第一個 Gemini 失敗
                (
                    {"role": "assistant", "content": "Groq 回覆推薦！"},
                    120,
                ),  # 第二個 Groq 成功
            ],
        ):
            content, model, tokens = await client.call_text([{"role": "user", "content": "hi"}])
            assert model == "qwen/qwen3.8-27b"
            assert content == "Groq 回覆推薦！"
            assert tokens == 120


@pytest.mark.asyncio
async def test_llm_client_ollama_primary():
    client = LLMClient()

    with (
        patch("src.config.settings.LLM_PRIMARY_PROVIDER", "ollama"),
        patch.object(client, "_is_ollama_online", AsyncMock(return_value=True)),
        patch.object(
            client,
            "_call_openai_compatible_api",
            AsyncMock(return_value=({"role": "assistant", "content": "Ollama 本地推薦！"}, 80)),
        ),
    ):
        content, model, tokens = await client.call_text([{"role": "user", "content": "hi"}])
        assert "ollama" in model
        assert content == "Ollama 本地推薦！"
        assert tokens == 80


@pytest.mark.asyncio
async def test_call_openai_compatible_api_success():
    client = LLMClient()
    fake_payload = {
        "choices": [{"message": {"role": "assistant", "content": "測試成功回覆"}}],
        "usage": {"total_tokens": 42},
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = fake_payload

    with patch("httpx.AsyncClient.post", AsyncMock(return_value=mock_resp)):
        msg, tokens = await client._call_openai_compatible_api(
            url="http://fake-api/v1",
            api_key="key",
            model="fake-model",
            messages=[{"role": "user", "content": "hello"}],
        )
        assert msg["content"] == "測試成功回覆"
        assert tokens == 42


@pytest.mark.asyncio
async def test_call_openai_compatible_api_429_dynamic_retry():
    client = LLMClient()
    fake_429_resp = MagicMock()
    fake_429_resp.status_code = 429
    fake_429_resp.headers = {"retry-after": "0.1"}

    fake_200_resp = MagicMock()
    fake_200_resp.status_code = 200
    fake_200_resp.json.return_value = {
        "choices": [{"message": {"role": "assistant", "content": "重試後成功"}}],
        "usage": {"total_tokens": 15},
    }

    with patch("asyncio.sleep", AsyncMock()) as mock_sleep:
        with patch("httpx.AsyncClient.post", AsyncMock(side_effect=[fake_429_resp, fake_200_resp])):
            msg, tokens = await client._call_openai_compatible_api(
                url="http://fake-api/v1",
                api_key="key",
                model="fake-model",
                messages=[{"role": "user", "content": "hello"}],
            )
            assert msg["content"] == "重試後成功"
            assert tokens == 15
            mock_sleep.assert_called_once_with(3.0)  # max(0.1, 3.0) = 3.0


@pytest.mark.asyncio
async def test_call_openai_compatible_api_empty_response_rejection():
    client = LLMClient()
    empty_resp = MagicMock()
    empty_resp.status_code = 200
    empty_resp.json.return_value = {
        "choices": [{"message": {"role": "assistant", "content": "", "tool_calls": None}}],
        "usage": {"total_tokens": 5},
    }

    with patch("httpx.AsyncClient.post", AsyncMock(return_value=empty_resp)):
        with pytest.raises(ValueError, match="empty"):
            await client._call_openai_compatible_api(
                url="http://fake-api/v1",
                api_key="key",
                model="fake-model",
                messages=[{"role": "user", "content": "hello"}],
            )


@pytest.mark.asyncio
async def test_agent_engine_facade_delegation():
    """驗證 AgentEngine Facade 正確將請求轉發給內裝的 FSM Pipeline"""
    mock_trace = AsyncMock(spec=AgentTraceLogger)
    engine = AgentEngine(trace_logger=mock_trace)

    with patch.object(engine.fsm, "run_pipeline", AsyncMock(return_value="FSM 回傳推薦卡片")) as mock_run:
        res = await engine.run_agent(
            chat_id=123,
            user_message="我想吃鰻魚飯",
            user_location=(35.68, 139.71),
        )
        assert res == "FSM 回傳推薦卡片"
        mock_run.assert_called_once_with(
            chat_id=123,
            user_message="我想吃鰻魚飯",
            user_location=(35.68, 139.71),
            conversation_history=None,
            on_progress=None,
        )
