from unittest.mock import AsyncMock

import pytest
from src.agent.llm_client import LLMClient
from src.agent.pipeline import FoodRecommendationFSM
from src.agent.trace import AgentTraceLogger
from src.db.repositories.restaurant import RestaurantRepository
from src.db.repositories.session import SessionRepository
from src.services.entity_resolver import EntityResolver
from src.services.google_places import GooglePlacesClient
from src.services.tabelog_scraper import TabelogScraper


@pytest.fixture
def mock_fsm():
    mock_llm = AsyncMock(spec=LLMClient)
    mock_places = AsyncMock(spec=GooglePlacesClient)
    mock_scraper = AsyncMock(spec=TabelogScraper)
    mock_resolver = AsyncMock(spec=EntityResolver)
    mock_resto_repo = AsyncMock(spec=RestaurantRepository)
    mock_session_repo = AsyncMock(spec=SessionRepository)
    mock_trace = AsyncMock(spec=AgentTraceLogger)

    fsm = FoodRecommendationFSM(
        llm_client=mock_llm,
        places_client=mock_places,
        tabelog_scraper=mock_scraper,
        entity_resolver=mock_resolver,
        restaurant_repo=mock_resto_repo,
        session_repo=mock_session_repo,
        trace_logger=mock_trace,
    )
    return fsm


@pytest.mark.asyncio
async def test_fsm_state_1_intent_search_normal(mock_fsm):
    mock_fsm.llm_client.call_structured_json.return_value = (
        {"intent": "search", "location": "新宿御苑", "cuisine": "居酒屋 餃子"},
        "qwen/qwen3.8-27b",
        30,
    )
    mock_fsm.places_client.geocode_location.return_value = (35.685, 139.710)

    res = await mock_fsm.execute_state_1_intent("新宿御苑 居酒屋（餃子）", None, None)
    assert res["intent"] == "search"
    assert res["location"] == "新宿御苑"
    assert res["cuisine"] == "居酒屋 餃子"
    assert res["coords"] == (35.685, 139.710)


@pytest.mark.asyncio
async def test_fsm_state_1_intent_reversed_order(mock_fsm):
    mock_fsm.llm_client.call_structured_json.return_value = (
        {"intent": "search", "location": "新宿御苑", "cuisine": "拉麵"},
        "qwen/qwen3.8-27b",
        25,
    )
    mock_fsm.places_client.geocode_location.return_value = (35.685, 139.710)

    res = await mock_fsm.execute_state_1_intent("拉麵 新宿御苑", None, None)
    assert res["intent"] == "search"
    assert res["location"] == "新宿御苑"
    assert res["cuisine"] == "拉麵"


@pytest.mark.asyncio
async def test_fsm_state_1_intent_need_location(mock_fsm):
    mock_fsm.llm_client.call_structured_json.return_value = (
        {"intent": "search", "location": None, "cuisine": "拉麵"},
        "qwen/qwen3.8-27b",
        20,
    )

    res = await mock_fsm.execute_state_1_intent("我想吃拉麵", None, None)
    assert res["intent"] == "need_location"
    assert res["coords"] is None


@pytest.mark.asyncio
async def test_fsm_state_1_intent_follow_up(mock_fsm):
    mock_fsm.llm_client.call_structured_json.return_value = (
        {"intent": "follow_up", "target_index": 2, "question": "營業時間"},
        "qwen/qwen3.8-27b",
        20,
    )

    res = await mock_fsm.execute_state_1_intent("那第二家有開嗎？", None, None)
    assert res["intent"] == "follow_up"
    assert res["target_index"] == 2


@pytest.mark.asyncio
async def test_fsm_state_2_places_ladder_expansion(mock_fsm):
    # 800m 回傳 1 家，1200m 再回傳 2 家，階梯擴大湊齊 3 家
    mock_fsm.places_client.search_nearby.side_effect = [
        [{"google_place_id": "p1", "name": "餐廳1"}],
        [{"google_place_id": "p2", "name": "餐廳2"}, {"google_place_id": "p3", "name": "餐廳3"}],
    ]

    candidates = await mock_fsm.execute_state_2_places_ladder(35.685, 139.710, "拉麵")
    assert len(candidates) == 3
    assert [c["google_place_id"] for c in candidates] == ["p1", "p2", "p3"]
    assert mock_fsm.places_client.search_nearby.call_count == 2


@pytest.mark.asyncio
async def test_fsm_state_3_parallel_tabelog_cached(mock_fsm):
    # p1 有快取
    mock_fsm.restaurant_repo.get_by_google_place_id.return_value = {
        "google_place_id": "p1",
        "name_ja": "壽司店 A",
        "tabelog_rating": 3.75,
        "review_count": 210,
        "tabelog_url": "https://tabelog.com/1",
        "price_dinner": "￥5,000",
    }

    places = [{"google_place_id": "p1", "name": "壽司店 A"}]
    enriched = await mock_fsm.execute_state_3_parallel_tabelog(places)

    assert len(enriched) == 1
    assert enriched[0]["rating_source"] == "Tabelog"
    assert enriched[0]["tabelog_rating"] == 3.75
    mock_fsm.tabelog_scraper.search_tabelog_candidates.assert_not_called()


@pytest.mark.asyncio
async def test_fsm_state_3_tabelog_403_transparent_fallback(mock_fsm):
    # 快取未命中，爬蟲遭遇 403 異常
    mock_fsm.restaurant_repo.get_by_google_place_id.return_value = None
    mock_fsm.tabelog_scraper.search_tabelog_candidates.side_effect = Exception("HTTP 403 Forbidden")

    places = [{"google_place_id": "p2", "name": "神戶牛 B", "rating": 4.6, "user_ratings_total": 85}]
    enriched = await mock_fsm.execute_state_3_parallel_tabelog(places)

    assert len(enriched) == 1
    assert enriched[0]["rating_source"] == "Google"
    assert enriched[0]["tabelog_rating"] is None
    assert enriched[0]["rating"] == 4.6


@pytest.mark.asyncio
async def test_fsm_state_4_synthesis_success(mock_fsm):
    mock_fsm.llm_client.call_text.return_value = (
        "為您推薦 `一蘭 本店`，Tabelog 3.65 分！",
        "qwen/qwen3.8-27b",
        150,
    )

    stores = [{"name_ja": "一蘭 本店", "tabelog_rating": 3.65, "rating_source": "Tabelog"}]
    card = await mock_fsm.execute_state_4_synthesis(stores, "拉麵", "新宿")

    assert "一蘭 本店" in card
    mock_fsm.llm_client.call_text.assert_called_once()


@pytest.mark.asyncio
async def test_fsm_state_4_template_fallback_on_llm_failure(mock_fsm):
    # LLM 拋出例外，觸發模板保底
    mock_fsm.llm_client.call_text.side_effect = Exception("Groq Timeout")

    stores = [
        {
            "name_ja": "餃子處 新宿",
            "rating_source": "Google",
            "rating": 4.4,
            "address": "東京都新宿區",
        }
    ]
    card = await mock_fsm.execute_state_4_synthesis(stores, "餃子", "新宿")

    assert "⚠️ **AI 語意模型連線異常，已為您自動載入直出精準美食卡片：**" in card
    assert "`餃子處 新宿`" in card
    assert "以 Google 評價為依據" in card


@pytest.mark.asyncio
async def test_fsm_run_pipeline_end_to_end(mock_fsm):
    mock_fsm.llm_client.call_structured_json.return_value = (
        {"intent": "search", "location": "博多", "cuisine": "拉麵"},
        "qwen/qwen3.8-27b",
        25,
    )
    mock_fsm.places_client.geocode_location.return_value = (33.59, 130.42)
    mock_fsm.places_client.search_nearby.return_value = [{"google_place_id": "p1", "name": "博多一雙", "rating": 4.5}]
    mock_fsm.restaurant_repo.get_by_google_place_id.return_value = {
        "google_place_id": "p1",
        "name_ja": "博多一雙",
        "tabelog_rating": 3.72,
        "review_count": 500,
        "rating_source": "Tabelog",
    }
    mock_fsm.llm_client.call_text.return_value = (
        "為您推薦博多名店 `博多一雙`！",
        "qwen/qwen3.8-27b",
        100,
    )

    progress_stages = []

    async def mock_progress(msg: str):
        progress_stages.append(msg)

    result = await mock_fsm.run_pipeline(
        chat_id=123,
        user_message="我想在博多吃拉麵",
        on_progress=mock_progress,
    )

    assert "博多一雙" in result
    # 驗證方案 1A 二段式進度
    assert any("正在為您鎖定" in p for p in progress_stages)
    assert any("已精選 3 家" in p or "正在為您生成專屬推薦卡片" in p for p in progress_stages)
    mock_fsm.session_repo.update_recommended_ids.assert_called_once()


@pytest.mark.asyncio
async def test_fsm_execute_follow_up(mock_fsm):
    mock_fsm.session_repo.get_session.return_value = {
        "last_recommended_ids": [
            {"name_ja": "店家一", "tabelog_rating": 3.5},
            {"name_ja": "店家二", "tabelog_rating": 3.8, "address": "新宿三丁目"},
        ]
    }
    mock_fsm.llm_client.call_text.return_value = (
        "店家二位於新宿三丁目，評分為 3.8 分！",
        "qwen/qwen3.8-27b",
        60,
    )

    parsed = {"intent": "follow_up", "target_index": 2, "question": "地址在哪"}
    ans = await mock_fsm.execute_follow_up(chat_id=999, parsed_intent=parsed, user_message="那第二家在哪？")

    assert "店家二" in ans or "新宿三丁目" in ans


@pytest.mark.asyncio
async def test_fsm_walking_distance_and_prompt_loader(mock_fsm):
    from src.agent.pipeline import get_recommendation_card_prompt

    # 1. 測試提示詞載入器
    prompt = get_recommendation_card_prompt()
    assert "步行距離" in prompt
    assert "特派員提醒" in prompt

    # 2. 測試階梯搜尋中的步行距離計算 (基準點 35.685, 139.710)
    mock_fsm.places_client.search_nearby.return_value = [
        {"google_place_id": "p1", "name": "近距離拉麵", "lat": 35.686, "lng": 139.711}
    ]
    candidates = await mock_fsm.execute_state_2_places_ladder(35.685, 139.710, "拉麵")
    assert len(candidates) == 1
    assert "distance_meters" in candidates[0]
    assert "walk_time_minutes" in candidates[0]
    assert "步行約" in candidates[0]["walking_desc"]

    # 3. 測試保底卡片排版有加入步行距離
    fallback_card = mock_fsm._render_template_fallback_card(candidates, "拉麵")
    assert "🚶 **步行距離**" in fallback_card


def test_guardrail_sanitizes_rating_drift(mock_fsm):
    """測試 Scoped Anchor Guardrail 能夠精準修正 LLM 的評分筆誤，且絕不誤傷評論內的數字"""
    raw_card = """為您推薦新宿優質名店：

1. 🍱 `寿々喜家 本店`
   🚶 **步行距離**：步行約 3 分鐘 (240m)
   ⭐️ **雙平台評分**：⭐️ Tabelog 3.70 分 (2,467 則評價) / Google 4.1 分
   📍 老饕點評：排隊約 3.5 小時，湯頭經 3.5 天熬煮，叉燒重達 350g！
   🔗 [Tabelog](https://tabelog.com/1)

2. 🍱 `餃子の福包 新宿店`
   🚶 **步行距離**：步行約 5 分鐘 (400m)
   ⭐️ **雙平台評分**：⭐️ Tabelog 3.42 分 (1,531 則評價) / Google 4.3 分
   📍 老饕點評：皮薄多汁，性價比極高。
   🔗 [Tabelog](https://tabelog.com/2)

💡 特派員提醒：排隊請趁早！"""

    stores = [
        {"name_ja": "寿々喜家 本店", "tabelog_rating": 3.78, "google_rating": 4.4},
        {"name_ja": "餃子の福包 新宿店", "tabelog_rating": 3.50, "google_rating": 4.2},
    ]

    sanitized = mock_fsm._sanitize_card_ratings(raw_card, stores)

    # 1. 驗證店家 1 的分數被精準校正
    assert "Tabelog 3.78 分" in sanitized
    assert "Google 4.4 分" in sanitized

    # 2. 驗證店家 1 評論內的非評分數值 100% 未被誤傷
    assert "排隊約 3.5 小時" in sanitized
    assert "3.5 天熬煮" in sanitized
    assert "350g" in sanitized

    # 3. 驗證店家 2 的分數亦被精準校正
    assert "Tabelog 3.50 分" in sanitized
    assert "Google 4.2 分" in sanitized


def test_guardrail_contract_with_agent_system_prompt():
    """合約測試 (Contract Test)：確保 agent_system.md 的規範與 Guardrail 正則錨點 100% 同步。
    若未來有人改動 Prompt 格式而導致關鍵字不一致，CI 會立刻紅燈阻擋！"""
    from src.agent.pipeline import AGENT_SYSTEM_PROMPT_PATH

    assert AGENT_SYSTEM_PROMPT_PATH.exists(), "agent_system.md 檔案不存在！"
    prompt_text = AGENT_SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")

    # 檢驗 Prompt 必須包含護欄仰賴的關鍵錨點關鍵字
    assert "Tabelog" in prompt_text, "Prompt 缺少 'Tabelog' 評分錨點！"
    assert "Google" in prompt_text, "Prompt 缺少 'Google' 評分錨點！"
    assert "步行距離" in prompt_text, "Prompt 缺少 '步行距離' 錨點！"
