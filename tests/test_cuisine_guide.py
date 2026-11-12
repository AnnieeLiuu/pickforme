from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from src.agent.pipeline import FoodRecommendationFSM
from src.services.cuisine_guide import AreaSpecialtyGuide, LocalSpecialtyGuideService


@pytest.mark.asyncio
async def test_tier_1_curated_local_specialty_hit():
    """驗證 Tier 1 內建考據知識庫：查詢「神保町」或「淺草」時 0ms 命中且不消耗 LLM"""
    service = LocalSpecialtyGuideService()
    mock_llm = MagicMock()
    mock_llm.call_structured_json = AsyncMock()

    guide = await service.get_area_guide("神保町車站", llm_client=mock_llm)
    assert isinstance(guide, AreaSpecialtyGuide)
    assert guide.source_tier == "curated"
    assert len(guide.specialties) == 3
    assert any("咖哩" in item.cuisine_name for item in guide.specialties)
    mock_llm.call_structured_json.assert_not_called()

    md = service.render_guide_markdown(guide)
    assert "神保町" in md
    assert "歐風咖哩" in md
    assert "直接推薦" in md


@pytest.mark.asyncio
async def test_tier_2_grounding_synthesis_for_unknown_station():
    """驗證 Tier 2 即時日本網頁 Grounding：查詢未內建之車站（如「門前仲町」）時，
    先擷取 Yahoo Japan 摘要再交由 LLM 結構化提煉，並快取結果。
    """
    service = LocalSpecialtyGuideService()
    mock_llm = MagicMock()
    mock_llm.call_structured_json = AsyncMock(
        return_value=(
            {
                "area_name": "門前仲町",
                "intro": "門前仲町是深川文化的中心，以江戶漁師料理「深川飯」與富岡八幡宮老街下町居酒屋聞名。",
                "specialties": [
                    {
                        "index": 1,
                        "cuisine_name": "深川飯（蛤蜊炊飯）",
                        "name_ja": "深川めし",
                        "history_and_culture": "源自江戶時代深川地區漁夫以新鮮文蛤與青蔥熬煮味噌湯澆飯的傳統漁師料理。",
                        "flavor_profile": "蛤蜊鮮甜濃郁、帶有傳統味噌與青蔥香氣。",
                    },
                    {
                        "index": 2,
                        "cuisine_name": "下町大眾居酒屋與烤雞串",
                        "name_ja": "大衆酒場・焼き鳥",
                        "history_and_culture": "富岡八幡宮與深川不動堂參道周邊自戰後聚集了眾多歷史悠久的下町酒場。",
                        "flavor_profile": "炭香濃郁、搭配生啤或燒酎極具老東京人情味。",
                    },
                    {
                        "index": 3,
                        "cuisine_name": "江戶前天婦羅與烤鰻魚",
                        "name_ja": "天ぷら・うなぎ",
                        "history_and_culture": "鄰近隅田川與東京灣河口，江戶時代即為河鮮與海味匯聚之地。",
                        "flavor_profile": "麻油香酥麵衣鎖住時令海鮮甘甜。",
                    },
                ],
                "source_tier": "grounding",
            },
            "groq",
            220,
        )
    )

    with patch.object(
        service,
        "_fetch_yahoo_grounding_snippets",
        new_callable=AsyncMock,
        return_value=["門前仲町の名物といえば深川めし。江戸時代の漁師料理として発祥。"],
    ):
        guide = await service.get_area_guide("門前仲町", llm_client=mock_llm)
        assert guide.source_tier == "grounding"
        assert guide.specialties[0].cuisine_name == "深川飯（蛤蜊炊飯）"
        mock_llm.call_structured_json.assert_awaited_once()


@pytest.mark.asyncio
async def test_fsm_explore_cuisine_and_next_turn_selection_flow():
    """驗證端到端兩階段互動：
    Turn 1: 使用者輸入「神保町有什麼特色美食？」 -> 回傳神保町 3 大特色料理與歷史故事，並寫入 working_memory.suggested_cuisines。
    Turn 2: 使用者回覆「1」 -> 0ms 從 working_memory 映射出 location="神保町" 與 cuisine="歐風咖哩"，直接進入餐廳檢索。
    Turn 3: 使用者回覆「直接推薦」 -> 0ms 從 working_memory 映射出 location="神保町" 與 cuisine="熱門人氣美食"。
    """
    mock_llm = MagicMock()
    mock_llm.call_structured_json = AsyncMock()
    mock_llm.call_text = AsyncMock(return_value=("### 🥇 精選推薦 1：`ボンディ 神保町本店`", "groq", 150))

    mock_places = MagicMock()
    mock_places.geocode_location = AsyncMock(return_value=(35.6959, 139.7576))
    mock_places.search_nearby = AsyncMock(
        return_value=[
            {
                "google_place_id": "bondy_1",
                "name": "ボンディ 神保町本店",
                "lat": 35.6955,
                "lng": 139.7572,
                "rating": 4.4,
                "userRatingCount": 2500,
            }
        ]
    )
    mock_places.search_by_text = AsyncMock(return_value=[])

    stored_wm: dict[str, object] = {}

    async def _mock_update_wm(chat_id, last_location=None, last_cuisine=None, suggested_cuisines=None):
        if last_location is not None:
            stored_wm["last_location"] = last_location
        if last_cuisine is not None:
            stored_wm["last_cuisine"] = last_cuisine
        if suggested_cuisines is not None:
            stored_wm["suggested_cuisines"] = suggested_cuisines
        return dict(stored_wm)

    async def _mock_get_wm(chat_id):
        return dict(stored_wm)

    mock_session_repo = MagicMock()
    mock_session_repo.update_working_memory = AsyncMock(side_effect=_mock_update_wm)
    mock_session_repo.get_working_memory = AsyncMock(side_effect=_mock_get_wm)
    mock_session_repo.get_long_term_preferences = AsyncMock(return_value={})
    mock_session_repo.update_recommended_ids = AsyncMock()
    mock_session_repo.get_session = AsyncMock(
        return_value={"last_location_lat": 35.6959, "last_location_lng": 139.7576}
    )

    mock_scraper = MagicMock()
    mock_scraper.discover_area_top_stores = AsyncMock(return_value=[])
    mock_scraper.search_tabelog_candidates = AsyncMock(return_value=[])
    mock_scraper.fetch_tabelog_reviews = AsyncMock(return_value=[])

    mock_repo = MagicMock()
    mock_repo.get_by_google_place_id = AsyncMock(return_value=None)

    fsm = FoodRecommendationFSM(
        llm_client=mock_llm,
        places_client=mock_places,
        tabelog_scraper=mock_scraper,
        entity_resolver=MagicMock(),
        restaurant_repo=mock_repo,
        session_repo=mock_session_repo,
        trace_logger=MagicMock(log_step=AsyncMock(), start_run=AsyncMock(), finish_run=AsyncMock()),
    )

    # Turn 1: 詢問「神保町有什麼特色美食？」
    reply_1 = await fsm.run_pipeline(chat_id=777, user_message="神保町有什麼特色美食？")
    assert "歐風咖哩" in reply_1
    assert "直接推薦" in reply_1
    assert stored_wm.get("last_location") == "神保町"
    assert isinstance(stored_wm.get("suggested_cuisines"), list)
    assert "歐風咖哩" in stored_wm["suggested_cuisines"][0]  # type: ignore[index]

    # Turn 2: 回覆「1」-> 驗證自動映射為「歐風咖哩」並呼叫 search_nearby
    parsed_2 = await fsm.execute_state_1_intent(
        user_message="1",
        user_location=None,
        conversation_history=[],
        chat_id=777,
    )
    assert parsed_2["intent"] == "search"
    assert parsed_2["location"] == "神保町"
    assert "歐風咖哩" in parsed_2["cuisine"]

    # Turn 3: 回覆「直接推薦」-> 驗證自動映射為「熱門人氣美食」
    parsed_3 = await fsm.execute_state_1_intent(
        user_message="直接推薦",
        user_location=None,
        conversation_history=[],
        chat_id=777,
    )
    assert parsed_3["intent"] == "search"
    assert parsed_3["location"] == "神保町"
    assert parsed_3["cuisine"] == "熱門人氣美食"
