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
        return_value=[
            "門前仲町の名物といえば深川めし。江戸時代の漁師料理として発祥。",
            "深川不動堂参道周辺には下町情緒あふれる居酒屋や老舗和菓子店が並ぶ。",
        ],
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

    async def _mock_update_wm(
        chat_id,
        last_location=None,
        last_cuisine=None,
        suggested_cuisines=None,
        last_explore_category=None,
    ):
        if last_location is not None:
            stored_wm["last_location"] = last_location
        if last_cuisine is not None:
            stored_wm["last_cuisine"] = last_cuisine
        if suggested_cuisines is not None:
            stored_wm["suggested_cuisines"] = suggested_cuisines
        if last_explore_category is not None:
            stored_wm["last_explore_category"] = last_explore_category
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
    # suggested_cuisines 存入日文搜尋關鍵字 (如 欧風カレー)，避免中文括號污染搜尋
    assert "カレー" in str(stored_wm["suggested_cuisines"][0])  # type: ignore[index]

    # Turn 2: 回覆「1」-> 驗證自動映射為「欧風カレー」並呼叫 search_nearby
    parsed_2 = await fsm.execute_state_1_intent(
        user_message="1",
        user_location=None,
        conversation_history=[],
        chat_id=777,
    )
    assert parsed_2["intent"] == "search"
    assert parsed_2["location"] == "神保町"
    assert "カレー" in parsed_2["cuisine"]

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


@pytest.mark.asyncio
async def test_multi_category_kanazawa_drink_and_souvenir_curated():
    """驗證金澤在地飲品 (加賀棒茶) 與伴手禮 (長生殿落雁) 命中 Tier 1 考據庫，且 search_keyword 潔淨"""
    service = LocalSpecialtyGuideService()
    g_drink = await service.get_area_guide("金澤", category="drink")
    assert g_drink.category == "drink"
    assert g_drink.source_tier == "curated"
    assert "加賀棒茶" in g_drink.specialties[0].cuisine_name
    assert g_drink.specialties[0].get_search_keyword() == "加賀棒茶 ほうじ茶 丸八製茶場"

    md_drink = service.render_guide_markdown(g_drink)
    assert "飲品與喫茶酒場" in md_drink
    assert "加賀棒茶" in md_drink

    g_souvenir = await service.get_area_guide("金澤車站", category="souvenir")
    assert g_souvenir.category == "souvenir"
    assert "長生殿" in g_souvenir.specialties[0].cuisine_name
    assert "森八" in g_souvenir.specialties[0].get_search_keyword()

    md_souvenir = service.render_guide_markdown(g_souvenir)
    assert "伴手禮與百年老舖名菓" in md_souvenir
    assert "長生殿" in md_souvenir


@pytest.mark.asyncio
async def test_specialty_guide_db_write_back_and_read_through():
    """驗證 Tier 2 Yahoo Grounding 查證成功後自動寫回 Neon DB，下次查詢直接 Read-Through 0ms 命中"""
    mock_repo = MagicMock()
    mock_repo.get_guide = AsyncMock(return_value=None)
    mock_repo.upsert_guide = AsyncMock(return_value=True)

    service = LocalSpecialtyGuideService(guide_repo=mock_repo)
    mock_llm = MagicMock()
    mock_llm.call_structured_json = AsyncMock(
        return_value=(
            {
                "area_name": "尾道",
                "intro": "尾道依山傍海，擁有迷人的坡道與港町喫茶文化。",
                "specialties": [
                    {
                        "index": 1,
                        "cuisine_name": "尾道老舖自家烘焙珈琲",
                        "name_ja": "尾道珈琲",
                        "search_keyword_ja": "尾道 自家焙煎 珈琲 カフェ",
                        "history_and_culture": "昭和時期作為瀨戶內海文人航運樞紐，港邊聚集了許多自家烘焙手沖咖啡館。",
                        "flavor_profile": "苦甜回甘、香氣高雅。",
                    },
                    {
                        "index": 2,
                        "cuisine_name": "瀨戶內柑橘冷飲",
                        "name_ja": "瀬戸内みかんジュース",
                        "search_keyword_ja": "瀬戸内 みかん ジュース 尾道",
                        "history_and_culture": "瀨戶內溫暖陽光孕育出的高品質蜜柑，新鮮冷壓成天然果汁。",
                        "flavor_profile": "酸甜多汁消暑清香。",
                    },
                    {
                        "index": 3,
                        "cuisine_name": "尾道地酒（純米吟釀）",
                        "name_ja": "尾道地酒",
                        "search_keyword_ja": "尾道 地酒 日本酒",
                        "history_and_culture": "使用廣島優質酒米釀造，是瀨戶內海味的最佳搭檔。",
                        "flavor_profile": "淡雅辛口、餘韻清爽。",
                    },
                ],
                "source_tier": "grounding",
            },
            "groq",
            210,
        )
    )

    with patch.object(
        service,
        "_fetch_yahoo_grounding_snippets",
        new_callable=AsyncMock,
        return_value=[
            "尾道の名物飲み物といえば尾道珈琲。昭和の文豪が愛した喫茶店が多い。",
            "瀬戸内みかんの果汁を使ったフレッシュジュースも港町の名物として人気。",
        ],
    ):
        guide = await service.get_area_guide("尾道", category="drink", llm_client=mock_llm)
        assert guide.category == "drink"
        assert guide.source_tier == "grounding"
        mock_repo.upsert_guide.assert_awaited_once()

    # 第二次查詢：模擬 DB 已存在紀錄，驗證直接 Read-Through 秒回
    mock_repo.get_guide = AsyncMock(
        return_value={
            "area_name": "尾道",
            "category": "drink",
            "intro": "尾道依山傍海，擁有迷人的坡道與港町喫茶文化。",
            "specialties": [
                {
                    "index": 1,
                    "cuisine_name": "尾道老舖自家烘焙珈琲",
                    "name_ja": "尾道珈琲",
                    "search_keyword_ja": "尾道 自家焙煎 珈琲 カフェ",
                    "history_and_culture": "昭和時期作為瀨戶內海文人航運樞紐，港邊聚集了許多自家烘焙手沖咖啡館。",
                    "flavor_profile": "苦甜回甘、香氣高雅。",
                }
            ],
            "source_tier": "grounding",
        }
    )
    # 清空記憶體快取以強制測試 DB
    LocalSpecialtyGuideService._runtime_cache.clear()
    guide_from_db = await service.get_area_guide("尾道", category="drink")
    assert guide_from_db.area_name == "尾道"
    assert guide_from_db.source_tier == "grounding"


@pytest.mark.asyncio
async def test_cross_turn_switch_category_and_direct_recommend():
    """驗證跨輪類別切換（「金澤 喝的」->「有什麼伴手禮？」）與對應類別之「直接推薦」"""
    mock_llm = MagicMock()
    mock_llm.call_structured_json = AsyncMock()
    mock_llm.call_text = AsyncMock(return_value=("推薦卡片", "groq", 100))

    stored_wm: dict[str, object] = {}

    async def _mock_update_wm(
        chat_id, last_location=None, last_cuisine=None, suggested_cuisines=None, last_explore_category=None
    ):
        if last_location is not None:
            stored_wm["last_location"] = last_location
        if last_cuisine is not None:
            stored_wm["last_cuisine"] = last_cuisine
        if suggested_cuisines is not None:
            stored_wm["suggested_cuisines"] = suggested_cuisines
        if last_explore_category is not None:
            stored_wm["last_explore_category"] = last_explore_category
        return dict(stored_wm)

    mock_session_repo = MagicMock()
    mock_session_repo.update_working_memory = AsyncMock(side_effect=_mock_update_wm)
    mock_session_repo.get_working_memory = AsyncMock(side_effect=lambda cid: dict(stored_wm))
    mock_session_repo.get_long_term_preferences = AsyncMock(return_value={})
    mock_session_repo.update_recommended_ids = AsyncMock()
    mock_session_repo.get_session = AsyncMock(return_value=None)

    fsm = FoodRecommendationFSM(
        llm_client=mock_llm,
        places_client=MagicMock(
            geocode_location=AsyncMock(return_value=(36.56, 136.65)),
            search_nearby=AsyncMock(return_value=[]),
        ),
        session_repo=mock_session_repo,
        trace_logger=MagicMock(log_step=AsyncMock(), start_run=AsyncMock(), finish_run=AsyncMock()),
    )

    # 模擬 Stage 1 LLM 結構化解析「金澤有什麼喝的」-> explore_cuisine, category="drink"
    mock_llm.call_structured_json.return_value = (
        {
            "intent": "explore_cuisine",
            "explore_category": "drink",
            "location": "金澤",
            "cuisine": None,
            "modifier": None,
        },
        "groq",
        150,
    )

    reply_drink = await fsm.run_pipeline(chat_id=888, user_message="金澤有什麼喝的")
    assert "加賀棒茶" in reply_drink
    assert stored_wm.get("last_location") == "金澤"
    assert stored_wm.get("last_explore_category") == "drink"
    # suggested_cuisines 應使用純淨 search_keyword_ja
    assert "丸八製茶場" in stored_wm["suggested_cuisines"][0]  # type: ignore[index]

    # 跨輪追問 1: 使用者輸入「有什麼伴手禮？」(0ms Gate 比對 switch_souvenir 並繼承金澤)
    parsed_souvenir = await fsm.execute_state_1_intent(
        user_message="有什麼伴手禮？",
        user_location=None,
        conversation_history=[],
        chat_id=888,
    )
    assert parsed_souvenir["intent"] == "explore_cuisine"
    assert parsed_souvenir["explore_category"] == "souvenir"
    assert parsed_souvenir["location"] == "金澤"

    # 執行 run_pipeline 取得伴手禮卡片
    reply_souvenir = await fsm.run_pipeline(chat_id=888, user_message="有什麼伴手禮？")
    assert "長生殿" in reply_souvenir
    assert stored_wm.get("last_explore_category") == "souvenir"

    # 跨輪追問 2: 使用者回覆「直接推薦」-> 針對伴手禮維度自動映射為「人気 お土産 和菓子 老舗」
    parsed_direct = await fsm.execute_state_1_intent(
        user_message="直接推薦",
        user_location=None,
        conversation_history=[],
        chat_id=888,
    )
    assert parsed_direct["intent"] == "search"
    assert parsed_direct["location"] == "金澤"
    assert "お土産" in parsed_direct["cuisine"]
