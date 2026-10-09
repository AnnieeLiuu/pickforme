import json
from unittest.mock import AsyncMock, patch

import pytest
from src.agent.core import AgentEngine
from src.agent.tools import TOOL_SCHEMAS, AgentToolExecutor
from src.agent.trace import AgentTraceLogger


def test_tool_schemas_format():
    """驗證 TOOL_SCHEMAS符合 OpenAI Function Tool 格式規範"""
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
async def test_agent_engine_run_loop_direct_answer():
    mock_trace = AsyncMock(spec=AgentTraceLogger)
    engine = AgentEngine(trace_logger=mock_trace)

    mock_response = (
        {
            "role": "assistant",
            "content": "推薦博多站附近的「一蘭拉麵本店」，Tabelog 評分為 3.65！",
        },
        "gemini-3.8-flash",
        150,
    )

    with patch.object(engine, "call_llm_with_failover", return_value=mock_response):
        result = await engine.run_agent(
            chat_id=12345,
            user_message="請問博多站有什麼好吃拉麵？",
            user_location=(33.5902, 130.4207),
        )

        assert "一蘭拉麵本店" in result
        mock_trace.start_run.assert_called_once()
        mock_trace.finish_run.assert_called_once_with(
            status="completed", model_used="gemini-3.8-flash", total_tokens=150
        )


@pytest.mark.asyncio
async def test_agent_engine_groq_primary():
    engine = AgentEngine()

    with (
        patch("src.config.settings.LLM_PRIMARY_PROVIDER", "groq"),
        patch("src.config.settings.GROQ_API_KEY", "fake_groq_key"),
        patch.object(
            engine,
            "_call_openai_compatible_api",
            AsyncMock(return_value=({"role": "assistant", "content": "Groq 極速推薦！"}, 95)),
        ),
    ):
        msg, model, tokens = await engine.call_llm_with_failover([{"role": "user", "content": "hi"}])
        assert model == "qwen/qwen3.8-27b"
        assert msg["content"] == "Groq 極速推薦！"
        assert tokens == 95


@pytest.mark.asyncio
async def test_agent_engine_failover_gemini_to_groq():
    engine = AgentEngine()

    with (
        patch("src.config.settings.LLM_PRIMARY_PROVIDER", "gemini"),
        patch.object(engine, "_is_ollama_online", AsyncMock(return_value=False)),
        patch("src.config.settings.GEMINI_API_KEY", "fake_gemini_key"),
        patch("src.config.settings.GROQ_API_KEY", "fake_groq_key"),
    ):
        with patch.object(
            engine,
            "_call_openai_compatible_api",
            side_effect=[
                Exception("Gemini Rate Limit 429"),  # 第一個 Gemini 失敗
                (
                    {"role": "assistant", "content": "Groq 回覆推薦！"},
                    120,
                ),  # 第二個 Groq 成功
            ],
        ):
            msg, model, tokens = await engine.call_llm_with_failover([{"role": "user", "content": "hi"}])
            assert model == "qwen/qwen3.8-27b"
            assert msg["content"] == "Groq 回覆推薦！"
            assert tokens == 120


@pytest.mark.asyncio
async def test_agent_engine_ollama_primary():
    engine = AgentEngine()

    with (
        patch("src.config.settings.LLM_PRIMARY_PROVIDER", "ollama"),
        patch.object(engine, "_is_ollama_online", AsyncMock(return_value=True)),
        patch.object(
            engine,
            "_call_openai_compatible_api",
            AsyncMock(return_value=({"role": "assistant", "content": "Ollama 本地推薦！"}, 80)),
        ),
    ):
        msg, model, tokens = await engine.call_llm_with_failover([{"role": "user", "content": "hi"}])
        assert "ollama" in model
        assert msg["content"] == "Ollama 本地推薦！"
        assert tokens == 80


@pytest.mark.asyncio
async def test_agent_engine_on_progress_stages():
    engine = AgentEngine()
    progress_updates: list[str] = []

    async def mock_progress(msg: str) -> None:
        progress_updates.append(msg)

    # 模擬 2 步對話：第 1 步叫 search_nearby，第 2 步輸出最終結果
    step1_response = (
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "search_nearby",
                        "arguments": json.dumps({"lat": 35.6, "lng": 139.7, "keyword": "拉麵"}),
                    },
                }
            ],
        },
        "gemini-3.8-flash",
        100,
    )
    step2_response = (
        {
            "role": "assistant",
            "content": "為您推薦精選拉麵店！",
        },
        "gemini-3.8-flash",
        50,
    )

    with patch.object(engine, "call_llm_with_failover", side_effect=[step1_response, step2_response]):
        with patch.object(
            engine.tool_executor,
            "execute_tool",
            AsyncMock(return_value=json.dumps({"count": 1, "places": []})),
        ):
            res = await engine.run_agent(
                chat_id=999,
                user_message="我想吃拉麵",
                on_progress=mock_progress,
            )

    assert "精選拉麵店" in res
    # 驗證進度訊息包含了各階段的具體語意
    assert any("正在理解您的美食需求" in p for p in progress_updates)
    assert any("正在開啟地圖雷達" in p and "拉麵" in p for p in progress_updates)
    assert any("已取得周邊候選店家" in p for p in progress_updates)
