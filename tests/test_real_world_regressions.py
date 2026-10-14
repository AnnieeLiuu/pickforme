"""🍱 PickForMe Golden Regression Test Suite (真實戰役回歸測試集)

本檔案收錄系統在生產環境與真實使用者交互中踩過、排查並根除的 7 大經典戰役。
每個案例皆源於真實 Bug 現場，透過 Mock-backed 單元化固化，保證：
1. 0ms 離線執行、零外部 API 配額消耗。
2. 納入 GitLab CI / Pre-push 守門防線，徹底杜絕歷史問題二次復發 (Prevent Regressions)。
"""

import asyncio
import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from src.agent.pipeline import FoodRecommendationFSM, resolve_search_constraints
from src.agent.validator import validate_recommendation_card
from src.services.entity_resolver import (
    EntityResolver,
    EntityVerifyResponse,
    calculate_string_similarity,
)
from src.services.review_sanitizer import (
    is_pure_metadata_noise,
    sanitize_review_snippet,
    synthesize_store_recommendation_reason,
)


# ==============================================================================
# Case 1: 排版崩壞防禦 (Markdown Table vs Code Block)
# ==============================================================================
def test_case1_validator_rejects_markdown_table_and_falls_back():
    """Case 1: 驗證當 LLM 自作主張輸出生硬的 Markdown 表格時，驗證器能精準攔截，

    並促使 State 4 觸發確定性模板直出，確保 Telegram 使用者永遠收到統一排版。
    """
    # 模擬 LLM 違規產生的 Markdown 表格
    bad_table_card = """
你好！以下是橫濱車站的美食推薦：

| 店家名稱 | 評分 | 步行時間 | 推薦理由 |
|---|---|---|---|
| 鷄三和 | 3.2 | 2分鐘 | 親子丼很嫩 |
| 角平 | 3.5 | 5分鐘 | 沾麵好吃 |

希望您用餐愉快！
"""
    is_valid, reasons = validate_recommendation_card(bad_table_card)
    assert is_valid is False
    assert any("Markdown 表格" in r for r in reasons)

    # 模擬標準規範卡片 (反引號一鍵複製 + 步行距離 + 雙平台評分 + 支付方式)
    valid_card = """
### 🍱 精選推薦 1：鷄三和 橫濱 Joinus 店
`鷄三和 横浜ジョイナス店`

🚶 **步行距離**：從「橫濱車站」步行約 2 分鐘 (128m)
⭐️ **雙平台評分**：⭐️ Tabelog 3.2 (272 則評價) / Google 4.0
💳 **支付方式**：現金為主
💰 **預算區間**：約 ￥1,000 ~ ￥1,500
📍 **老饕評價與推薦理由 (食べログ 口コミ)**：
雞肉非常嫩滑，醬汁濃郁下飯。
🔗 **店家連結**：[Tabelog 頁面](https://tabelog.com/sample)
"""
    is_valid_ok, ok_reasons = validate_recommendation_card(valid_card)
    assert is_valid_ok is True
    assert len(ok_reasons) == 0


# ==============================================================================
# Case 2: 歷史上下文污染防禦 (Few-shot 模仿偏見)
# ==============================================================================
def test_case2_state1_history_sanitization_removes_few_shot_bias():
    """Case 2: 驗證多輪對話時，上一輪助理的長篇卡片 (>100字) 會被自動淨化為簡要摘要，

    杜絕微型 LLM 模仿助理輸出長文本，確保 State 1 的 JSON Mode 穩定運作。
    """
    fsm = FoodRecommendationFSM(
        places_client=MagicMock(),
        tabelog_scraper=MagicMock(),
        entity_resolver=MagicMock(),
        llm_client=MagicMock(),
    )

    long_assistant_card = (
        "你好！我是 PickForMe。\n"
        "### 🍱 精選推薦 1：回し寿司 活 橫濱Sky大樓店\n"
        "`回し寿司 活`\n"
        "🚶 **步行距離**：步行 4 分鐘 (320m)\n"
        "⭐️ **雙平台評分**：Tabelog 3.52 / Google 4.2\n"
        "💳 **支付方式**：可使用信用卡、PayPay\n"
        "這是一家極具人氣的迴轉壽司店，食材新鮮美味..." + ("詳細推薦內文..." * 30)
    )

    raw_history = [
        {"role": "user", "content": "我想吃橫濱壽司"},
        {"role": "assistant", "content": long_assistant_card},
    ]

    sanitized = fsm._sanitize_history_for_intent(raw_history)
    assert len(sanitized) == 2
    assert sanitized[0]["content"] == "我想吃橫濱壽司"
    # 長篇卡片應被濃縮為簡短摘要
    assert len(sanitized[1]["content"]) < 100
    assert "推薦" in sanitized[1]["content"] or "卡片" in sanitized[1]["content"]
    assert "🚶 **步行距離**" not in sanitized[1]["content"]


# ==============================================================================
# Case 3: 反向問候意圖保底 (Inverse Greeting Fallback)
# ==============================================================================
def test_case3_inverse_greeting_fallback_safeguards_unlisted_cuisines():
    """Case 3: 驗證當 LLM JSON 解析出現偶發異常時，只要非純打招呼短語，

    即便是一般白名單外的料理（如親子丼、天婦羅、鰻魚飯），一律安全保底為 search，絕不誤墮 chitchat。
    """
    from src.agent.llm_client import LLMClient

    client = LLMClient()
    # 模擬 LLM 產生非 JSON 的純文本內容
    client.call_text = AsyncMock(return_value=("我推薦您吃這家店，但沒有給出 JSON！", "groq", 100))

    # 1. 實質美食搜尋：即便未列入特定白名單，反向排除純問候後 100% 保底為 search
    res_don, _, _ = asyncio.run(client.call_structured_json([{"role": "user", "content": "橫濱車站附近的好吃親子丼"}]))
    assert res_don["intent"] == "search"

    res_tempura, _, _ = asyncio.run(client.call_structured_json([{"role": "user", "content": "新宿天婦羅老店"}]))
    assert res_tempura["intent"] == "search"

    res_unagi, _, _ = asyncio.run(client.call_structured_json([{"role": "user", "content": "淺草鰻魚飯推薦"}]))
    assert res_unagi["intent"] == "search"

    # 2. 純打招呼問候語：正確辨識為 chitchat
    res_greeting, _, _ = asyncio.run(client.call_structured_json([{"role": "user", "content": "你好！早安"}]))
    assert res_greeting["intent"] == "chitchat"


# ==============================================================================
# Case 4: 跨區連鎖店商圈搜尋錨定 (Mawashizushi KATSU 横浜)
# ==============================================================================
def test_case4_ladder_search_appends_target_area_for_chain_stores():
    """Case 4: 驗證跨地區連鎖店 (如 Mawashizushi KATSU) 在搜尋 Tabelog 時，

    系統能自動結合目標區域 (如 横浜)，避免搜尋結果全被東京分店霸榜。
    """
    fsm = FoodRecommendationFSM(
        places_client=MagicMock(),
        tabelog_scraper=MagicMock(),
        entity_resolver=MagicMock(),
        llm_client=MagicMock(),
    )

    searched_queries: list[str] = []

    async def mock_search(store_name: str):
        searched_queries.append(store_name)
        return []

    fsm.tabelog_scraper.search_tabelog_candidates = mock_search
    fsm.entity_resolver.resolve_entity = AsyncMock(return_value={"match_found": False})

    store_place = {
        "google_place_id": "place_katsu_123",
        "name": "Mawashizushi KATSU",
        "address": "神奈川県横浜市西区高島２丁目１９−１２ スカイビル 11階",
        "lat": 35.465,
        "lng": 139.622,
    }

    asyncio.run(fsm._enrich_single_place(store_place, area_hint="橫濱車站"))

    # 驗證首次搜尋查詢詞必須拼接商圈地名，鎖定在地分店
    assert len(searched_queries) >= 1
    assert "Mawashizushi KATSU" in searched_queries[0]
    assert "橫濱" in searched_queries[0] or "横浜" in searched_queries[0]


# ==============================================================================
# Case 5: Unicode 破折號跨語言智慧裁決 (Tori Sanwa – Landmark Plaza)
# ==============================================================================
def test_case5_unicode_dash_romanized_triggers_level2_llm():
    """Case 5: 驗證店名中含有 Unicode 破折號 (\u2013 EN DASH) 之西文店名，

    不會被脆弱的 .isascii() 誤殺，而是 100% 平滑流向 Level 2 Micro-LLM 智慧裁決。
    """
    resolver = EntityResolver(gemini_api_key=None)

    # 含有 \u2013 (EN DASH)
    google_store = {
        "name": "Tori Sanwa – Yokohama Landmark Plaza Branch",
        "address": "日本、〒220-0012 神奈川県横浜市西区みなとみらい２丁目２−１ ランドマークプラザ 1F",
        "phone": "045-263-8180",
        "lat": 35.4548,
        "lng": 139.6318,
    }

    tabelog_candidates = [
        {
            "name_ja": "鶏三和 横浜ランドマークタワー店",
            "address": "神奈川県横浜市西区みなとみらい2-2-1 ランドマークプラザ 1F",
            "phone": "",
            "lat": 35.4548,
            "lng": 139.6318,
            "tabelog_url": "https://tabelog.com/kanagawa/A1401/A140103/14037512/",
            "tabelog_rating": 3.2,
        }
    ]

    with patch.object(resolver, "_verify_with_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = EntityVerifyResponse(
            is_same_store=True,
            confidence=0.98,
            reason="Tori Sanwa 即為 鶏三和，分店名指向橫濱地標塔，地址與電話區號完全相符",
        )

        result = asyncio.run(resolver.resolve_entity(google_store, tabelog_candidates, allow_llm=True))

        assert result["match_found"] is True
        assert result["match_method"] == "llm_verify"
        assert result["confidence"] == 0.98
        assert result["candidate"]["name_ja"] == "鶏三和 横浜ランドマークタワー店"
        mock_llm.assert_called_once()


# ==============================================================================
# Case 6: 短店名停用詞保護 (Curry House / The Bakery)
# ==============================================================================
def test_case6_short_store_name_stop_words_protection():
    """Case 6: 驗證短店名 (如 Curry House、The Bakery) 不會因激進的通用停用詞清洗導致核心 Token 歸零。"""
    # 英文連鎖短店名
    sim_curry = calculate_string_similarity("Curry House", "Curry House CoCo")
    assert sim_curry >= 0.85

    sim_bakery = calculate_string_similarity("The Bakery", "The Bakery Tokyo")
    assert sim_bakery >= 0.85

    # 繁體漢字正規化維持 1:1，不任意擴展
    sim_noodle = calculate_string_similarity("麵屋武藏", "面屋武蔵")
    assert sim_noodle >= 0.60


# ==============================================================================
# Case 7: 全日本 47 都道府縣行政區劃泛用正則覆蓋
# ==============================================================================
@pytest.mark.parametrize(
    ("address", "expected_extracted_area"),
    [
        ("神奈川県横浜市西区みなとみらい2-2-1", "横浜"),  # 日文縣「県」
        ("東京都新宿區歌舞伎町1-1-1", "新宿"),  # 繁體區「區」
        ("兵庫県神戸市中央区北長狭通1-1", "神戸"),  # 兵庫神戶
        ("沖縄県那覇市久茂地1-1-1", "那覇"),  # 沖繩那霸
        ("宮城県仙台市青葉区中央1-1", "仙台"),  # 宮城仙台
        ("広島県広島市中区本通1-1", "広島"),  # 廣島 (驗證不誤傷地名中的「島」字)
        ("北海道札幌市中央区北5条西2丁目", "札幌"),  # 北海道
        ("京都府京都市中京区河原町通", "京都"),  # 京都府 (驗證不誤傷地名中的「都」字)
    ],
)
def test_case7_japan_47_prefectures_regex_coverage(address: str, expected_extracted_area: str):
    """Case 7: 驗證行政區劃泛用正則不硬編碼任何單一地名，能夠泛化提取全日本 47 都道府縣之核心行政區名稱。"""
    # 核心正則：相容「都道府縣県」與「市區町村島」
    m = re.search(r"(?:[^\d\s,，]{2,3}[都道府縣県])\s*([^\d\s,，]{2,6}?[市區町村島])", address)
    if not m:
        m = re.search(r"([^\d\s,，]{2,6}?[市區町村島])", address)

    assert m is not None
    # 僅剝離結尾單一行政層級詞彙，防止誤傷「広島」、「京都」等地名主幹
    extracted = re.sub(r"[市區町村都道府縣県]$", "", m.group(1))
    assert extracted == expected_extracted_area


# ==============================================================================
# Case 8: 電話號碼檢索備援 (Phone Grounding Fallback)
# ==============================================================================
def test_case8_phone_fallback_recovers_mangled_romanized_store():
    """Case 8: 驗證當 Google 店名為連綴畸形羅馬拼音 (Densetsunosutadonya) 且店名檢索為 0 筆時，

    系統能自動降級為以電話號碼 045-317-2181 檢索 Tabelog，並由 Level 1 電話精準吻合 100% 召回。
    """
    mock_repo = MagicMock()
    mock_repo.get_by_google_place_id = AsyncMock(return_value=None)
    mock_repo.save_restaurant_with_tabelog = AsyncMock()

    fsm = FoodRecommendationFSM(
        places_client=MagicMock(),
        tabelog_scraper=MagicMock(),
        entity_resolver=EntityResolver(gemini_api_key=None),
        restaurant_repo=mock_repo,
        llm_client=MagicMock(),
    )

    searched_inputs: list[str] = []

    async def mock_search_candidates(store_name: str, area_name: str = ""):
        searched_inputs.append(store_name)
        # 店名搜尋皆返回 0 筆
        return []

    async def mock_search_via_grounding(store_name: str, area_name: str = ""):
        searched_inputs.append(store_name)
        if "045-317-2181" in store_name:
            return [
                {
                    "name_ja": "伝説のすた丼屋 横浜西口店",
                    "tabelog_url": "https://tabelog.com/kanagawa/A1401/A140102/14040064/",
                    "phone": "045-317-2181",
                    "tabelog_rating": 3.06,
                    "review_count": 89,
                }
            ]
        return []

    fsm.tabelog_scraper.search_tabelog_candidates = mock_search_candidates
    fsm.tabelog_scraper.search_via_grounding = mock_search_via_grounding
    fsm.tabelog_scraper.scrape_restaurant_details = AsyncMock(return_value={"tabelog_rating": 3.06, "review_count": 89})

    store_place = {
        "google_place_id": "place_sutadon_999",
        "name": "Densetsunosutadonya Yokohamanishiguchiten",
        "phone": "045-317-2181",
        "address": "1-chōme-13-11 Okano, Nishi Ward, Yokohama, Kanagawa 220-0073, Japan",
        "lat": 35.463,
        "lng": 139.617,
    }

    enriched = asyncio.run(fsm._enrich_single_place(store_place, area_hint="橫濱車站"))

    # 驗證觸發了電話搜尋備援，並成功賦予 Tabelog 評分
    assert any("045-317-2181" in s for s in searched_inputs)
    assert enriched.get("tabelog_rating") == 3.06
    assert enriched.get("rating_source") == "Tabelog"


# ==============================================================================
# Case 9: 卡片排版驗證防誤殺 (Fuzzy Store Name in Code Blocks)
# ==============================================================================
def test_case9_validator_fuzzy_store_name_in_code_blocks():
    """Case 9: 驗證當 LLM 自發性將英文拼音還原為日文漢字、或包含繁日漢字微調時，驗證器不會產生誤殺。"""
    stores = [
        {"name": "Densetsunosutadonya Yokohamanishiguchiten", "name_ja": "Densetsunosutadonya Yokohamanishiguchiten"},
        {"name": "三﨑市場橫濱Joinus店", "name_ja": "三﨑市場橫濱Joinus店"},
    ]

    # LLM 產生的卡片：自動還原為日文漢字「伝説のすた丼屋 横浜西口店」與「三﨑市場 横浜ジョイナス店」
    compliant_card = """
### 🍱 精選推薦 1：伝説のすた丼屋 橫濱西口店
`伝説のすた丼屋 横浜西口店`

🚶 **步行距離**：從「橫濱車站」步行約 9 分鐘 (700m)
⭐️ **雙平台評分**：⭐️ Tabelog 3.06 (89 則評價) / Google 3.7
💳 **支付方式**：現金為主
💰 **預算區間**：約 ￥800 ~ ￥1,200
📍 **老饕評價與推薦理由**：
大份量蒜味豬肉丼飯，肉質鮮嫩，附生雞蛋與味噌湯。
🔗 **店家連結**：[Tabelog 頁面](https://tabelog.com/sample)

---

### 🍱 精選推薦 2：三﨑市場 橫濱Joinus店
`三﨑市場 横浜ジョイナス店`

🚶 **步行距離**：從「橫濱車站」步行約 2 分鐘 (92m)
⭐️ **雙平台評分**：⭐️ Tabelog 3.30 (211 則評價) / Google 3.6
💳 **支付方式**：現金為主
💰 **預算區間**：約 ￥1,000 ~ ￥1,500
📍 **老饕評價與推薦理由**：
鮪魚丼飯性價比高，出餐極為迅速。
🔗 **店家連結**：[Tabelog 頁面](https://tabelog.com/sample)
"""
    is_valid, reasons = validate_recommendation_card(compliant_card, stores=stores)
    assert is_valid is True, f"應通過驗證，卻失敗了：{reasons}"


# ==============================================================================
# Case 10: 模板直出長篇流水帳評論截斷 (Template Review Truncation)
# ==============================================================================
def test_case10_template_fallback_truncates_long_raw_reviews():
    """Case 10: 驗證即便觸發模板直出兜底，長達數百字之未總結原始 Google/Tabelog 評論也會被安全截斷至 130 字內。"""
    fsm = FoodRecommendationFSM(
        places_client=MagicMock(),
        tabelog_scraper=MagicMock(),
        entity_resolver=MagicMock(),
        llm_client=MagicMock(),
    )

    super_long_review = (
        "This is the legendary Sutadon restaurant, located about one block northwest of Okano intersection. "
        "I visited on a Meat Day evening. I ordered the original Sutadon with extra meat. It comes with a raw egg "
        "and miso soup. Meal tickets must be purchased in advance, and the restaurant has both counter and table seating. "
        "The ticket machine does not support the free extra meat on Meat Day, and on the day itself, the extra meat "
        "option is displayed as sold out. Given the good customer service and operation, I will not deduct points."
    )

    stores = [
        {
            "name": "Densetsunosutadonya",
            "name_ja": "伝説のすた丼屋",
            "rating_source": "Google",
            "google_rating": 3.7,
            "address": "橫濱市西區",
            "sample_reviews": [{"source": "google", "text": super_long_review}],
        }
    ]

    fallback_card = fsm._render_template_fallback_card(stores, cuisine="丼飯", is_format_fallback=True)

    # 驗證無恐嚇性連線異常標籤
    assert "AI 語意模型連線異常" not in fallback_card
    # 驗證評論已被截斷，長度受控
    assert "..." in fallback_card
    assert len(super_long_review) > 400
    assert super_long_review not in fallback_card


# ==============================================================================
# Case 11: 搜尋雜訊阻斷防禦與電話號碼保底檢索 (Search Noise Unblocking & Phone Grounding Rescue)
# ==============================================================================
@pytest.mark.asyncio
async def test_case11_search_noise_unblocking_and_phone_grounding_rescue():
    """Case 11: 驗證當帶商圈搜尋回傳不相干雜訊候選（如大和町、大和屋）導致比對失敗時，

    系統不會受非空候選誤導，而是由 match_found 驅動回退階梯，
    以日本電話號碼精準檢索保底成功召回真身 (Yamatoshi -> 七代目 山利)。
    """
    mock_places_client = MagicMock()
    mock_tabelog_scraper = MagicMock()
    mock_entity_resolver = MagicMock()
    mock_llm_client = MagicMock()

    # 模擬 Yamatoshi 店家
    yamatoshi_store = {
        "google_place_id": "test_yamatoshi_place_id",
        "name": "Yamatoshi",
        "address": "Japan, 〒220-0005 Kanagawa, Yokohama, Nishi Ward, Minamisaiwai, 1-chōme−1−１ JR横浜タワーNEWoMan 8F",
        "phone": "045-534-5234",
        "lat": 35.4660,
        "lng": 139.6225,
        "reviews": [{"text": "好吃的大蝦丼飯", "author": "Tester"}],
    }

    # 1. 帶地區搜尋回傳 5 筆字面雜訊候選（大和町、大和屋等）
    noisy_candidates = [
        {"name_ja": "や台ずし 大和町", "tabelog_url": "https://tabelog.com/sample1"},
        {"name_ja": "大和屋横浜店", "tabelog_url": "https://tabelog.com/sample2"},
    ]
    # 2. 純店名搜尋亦回傳無關候選
    fallback_candidates = [
        {"name_ja": "大和酒場", "tabelog_url": "https://tabelog.com/sample3"},
    ]
    # 3. 電話號碼檢索回傳之真身候選
    phone_candidates = [
        {
            "name_ja": "しらす食堂 じゃこ屋 七代目 山利",
            "tabelog_url": "https://tabelog.com/kanagawa/A1401/A140101/14079594/",
            "rating": 3.5,
            "review_count": 637,
        }
    ]

    async def mock_search_cands(store_name: str):
        if "橫濱" in store_name or "横浜" in store_name:
            return noisy_candidates
        return fallback_candidates

    mock_tabelog_scraper.search_tabelog_candidates = AsyncMock(side_effect=mock_search_cands)
    mock_tabelog_scraper.search_via_grounding = AsyncMock(return_value=phone_candidates)
    mock_tabelog_scraper.scrape_restaurant_details = AsyncMock(
        return_value={
            "tabelog_rating": 3.5,
            "review_count": 637,
            "credit_cards": ["Visa"],
            "supports_paypay": True,
        }
    )

    async def mock_resolve(google_store, tabelog_candidates, allow_llm=True):
        # 雜訊候選無法匹配
        if tabelog_candidates == noisy_candidates or tabelog_candidates == fallback_candidates:
            return {"match_found": False, "candidate": None}
        # 電話檢索候選比對成功 (由注入的 phone 觸發完全比對)
        if any(c.get("name_ja") == "しらす食堂 じゃこ屋 七代目 山利" for c in tabelog_candidates):
            return {
                "match_found": True,
                "candidate": tabelog_candidates[0],
                "match_method": "phone_exact",
                "confidence": 1.0,
            }
        return {"match_found": False, "candidate": None}

    mock_entity_resolver.resolve_entity = AsyncMock(side_effect=mock_resolve)

    fsm = FoodRecommendationFSM(
        places_client=mock_places_client,
        tabelog_scraper=mock_tabelog_scraper,
        entity_resolver=mock_entity_resolver,
        llm_client=mock_llm_client,
    )
    # 關閉 DB 查詢以利純離線測試
    fsm.restaurant_repo.get_by_google_place_id = AsyncMock(return_value=None)
    fsm.restaurant_repo.upsert_restaurant = AsyncMock()

    enriched = await fsm._enrich_single_place(yamatoshi_store, area_hint="橫濱車站")

    # 驗證成功透過電話保底階梯救回真身
    assert enriched["rating_source"] == "Tabelog"
    assert enriched["name_ja"] == "しらす食堂 じゃこ屋 七代目 山利"
    assert enriched["tabelog_url"] == "https://tabelog.com/kanagawa/A1401/A140101/14079594/"
    assert enriched["tabelog_rating"] == 3.5
    assert enriched["review_count"] == 637
    # 驗證確實觸發了電話搜尋
    mock_tabelog_scraper.search_via_grounding.assert_called_once_with(store_name="045-534-5234", area_name="")


# ==============================================================================
# Case 12: 自適應老饕預篩、Early Stopping 與高分優先重排驗證 (Smart Pre-filtering & Adaptive Ranking)
# ==============================================================================
@pytest.mark.asyncio
async def test_case12_smart_prefiltering_early_stopping_and_foodie_ranking():
    """Case 12: 驗證：

    1. Google 端老饕潛力預篩 (0ms) 能正確挑選高評分、近距離之高潛力種子店家。
    2. 自適應漸進查驗：當 Tier 1 已有 >= 2 家 Tabelog 高分名店時觸發 Early Stopping，不浪費查驗 Tier 2。
    3. 老饕綜合加權重排：Tabelog 高分與百名店享有絕對排序優先權 (🥇/🥈/🥉)，無 Tabelog 者安全降權。
    """
    fsm = FoodRecommendationFSM(
        places_client=MagicMock(),
        tabelog_scraper=MagicMock(),
        entity_resolver=MagicMock(),
        llm_client=MagicMock(),
    )

    # 1. 驗證 Google 潛力預篩
    mock_places = [
        {"name": "Low Rating", "google_rating": 3.2, "userRatingCount": 20, "distance_meters": 300},
        {"name": "Popular Near", "google_rating": 4.5, "userRatingCount": 600, "distance_meters": 200},
        {"name": "Good Medium", "google_rating": 4.2, "userRatingCount": 200, "distance_meters": 400},
        {"name": "Good Far", "google_rating": 4.3, "userRatingCount": 200, "distance_meters": 2000},
        {"name": "Hidden Gem", "google_rating": 4.1, "userRatingCount": 80, "distance_meters": 350},
        {"name": "Decent Place", "google_rating": 4.0, "userRatingCount": 150, "distance_meters": 500},
        {"name": "Backup Store", "google_rating": 3.9, "userRatingCount": 120, "distance_meters": 450},
    ]
    prefiltered = fsm._prefilter_potential_foodie_places(mock_places, max_seeds=6)
    assert len(prefiltered) == 6
    # 驗證最高潛力的 Popular Near 排在第一
    assert prefiltered[0]["name"] == "Popular Near"
    # 驗證低評分的 Low Rating 被排除在種子之外或排在最後
    assert "Low Rating" not in [p["name"] for p in prefiltered[:4]]

    # 2. 驗證老饕綜合加權重排 (Foodie Ranking Engine)
    stores_to_rank = [
        {"name": "Google High No Tabelog", "rating": 4.6, "tabelog_rating": None, "distance_meters": 100},
        {"name": "Tabelog Ordinary", "tabelog_rating": 3.20, "rating": 3.8, "distance_meters": 300},
        {"name": "Tabelog Hyakumeiten", "tabelog_rating": 3.68, "awards": ["百名店"], "distance_meters": 500},
        {"name": "Tabelog High", "tabelog_rating": 3.52, "distance_meters": 250},
    ]
    top_3 = fsm.rank_and_select_top_stores(stores_to_rank, top_k=3)
    assert len(top_3) == 3
    # 第一名應為百名店 3.68
    assert top_3[0]["name"] == "Tabelog Hyakumeiten"
    assert top_3[0]["rank_badge"] == "🥇"
    # 第二名應為 Tabelog 3.52
    assert top_3[1]["name"] == "Tabelog High"
    assert top_3[1]["rank_badge"] == "🥈"
    # 第三名應為 Tabelog 3.20
    assert top_3[2]["name"] == "Tabelog Ordinary"
    assert top_3[2]["rank_badge"] == "🥉"
    # 驗證無 Tabelog 店家被降權，未擠入 Top 3
    assert all(s["name"] != "Google High No Tabelog" for s in top_3)

    # 3. 驗證自適應 Early Stopping (Tier 1 滿足條件則不呼叫 Tier 2)
    tier1_stores = [
        {"name": "Shop 1", "google_place_id": "p1"},
        {"name": "Shop 2", "google_place_id": "p2"},
        {"name": "Shop 3", "google_place_id": "p3"},
        {"name": "Shop 4", "google_place_id": "p4"},
    ]
    tier2_stores = [
        {"name": "Shop 5", "google_place_id": "p5"},
        {"name": "Shop 6", "google_place_id": "p6"},
    ]
    all_6_seeds = tier1_stores + tier2_stores

    enriched_mock_results = {
        "p1": {"name_ja": "Shop 1", "tabelog_rating": 3.55, "rating_source": "Tabelog", "distance_meters": 200},
        "p2": {"name_ja": "Shop 2", "tabelog_rating": 3.48, "rating_source": "Tabelog", "distance_meters": 300},
        "p3": {"name_ja": "Shop 3", "tabelog_rating": 3.20, "rating_source": "Tabelog", "distance_meters": 400},
        "p4": {"name_ja": "Shop 4", "tabelog_rating": None, "rating_source": "Google", "distance_meters": 500},
        "p5": {"name_ja": "Shop 5", "tabelog_rating": 3.60, "rating_source": "Tabelog", "distance_meters": 600},
        "p6": {"name_ja": "Shop 6", "tabelog_rating": 3.50, "rating_source": "Tabelog", "distance_meters": 700},
    }

    called_pids: list[str] = []

    async def mock_enrich(place, area_hint=None):
        pid = place.get("google_place_id")
        called_pids.append(pid)
        return enriched_mock_results[pid]

    fsm._enrich_single_place = AsyncMock(side_effect=mock_enrich)

    # 執行 State 3：因為 Tier 1 已有 Shop 1 (3.55) 與 Shop 2 (3.48) 兩家高分店，應直接觸發 Early Stopping！
    final_top3 = await fsm.execute_state_3_parallel_tabelog(all_6_seeds, area_hint="橫濱")

    # 驗證只查驗了 Tier 1 的 4 家，Tier 2 的 p5 與 p6 完全未被呼叫！
    assert len(called_pids) == 4
    assert "p5" not in called_pids
    assert "p6" not in called_pids
    assert len(final_top3) == 3
    assert final_top3[0]["name_ja"] == "Shop 1"
    assert final_top3[0]["rank_badge"] == "🥇"


# ==============================================================================
# Case 13: 結構化搜尋約束解耦與食評雜訊濾網 (Distance Modifier & Review Sanitizer)
# 現場還原：使用者提問「可以接受距離遠一點的走路15分鐘內都可以」(接續壽喜燒)
# 歷史問題：
# 1. 關鍵字被粗暴拼接為「壽喜燒 走路15分鐘內」，導致 Places 搜尋嚴重語意漂移 (誤召回海底撈、牛舌、一蘭)。
# 2. 模板直出時未過濾雜訊，將「営業時間 24時間営業」「西武新宿駅徒歩1分」等生硬元資料誤當老饕口感推薦。
# 防禦機制：
# 1. resolve_search_constraints 將空間約束分流至半徑階梯，Places keyword 100% 保持純淨。
# 2. ReviewSanitizer 自動剔除營業時間與交通路線，以結構化權威指標合成有溫度推薦理由。
# ==============================================================================
@pytest.mark.asyncio
async def test_case13_distance_modifier_cleansing_and_review_noise_filtering() -> None:
    # 1. 驗證結構化搜尋約束 (Structured Search Constraints)
    # 測試情境 A: 真實現場「可以接受距離遠一點的走路15分鐘內都可以」
    kw1, radii1, style1 = resolve_search_constraints(
        cuisine="壽喜燒",
        modifier="可以接受距離遠一點的走路15分鐘內都可以",
    )
    assert kw1 == "壽喜燒", f"關鍵字必須維持純淨壽喜燒，但得到: {kw1}"
    assert radii1[0] == 1200, f"15分鐘應對應 1200m 半徑，但得到: {radii1[0]}"
    assert "走路" not in kw1
    assert "15分鐘" not in kw1

    # 測試情境 B: 包含實質風格詞「更在地老店」
    kw2, radii2, style2 = resolve_search_constraints(
        cuisine="壽喜燒",
        modifier="我想找更在地老店",
    )
    assert "老店" in kw2
    assert radii2 == [800, 1200, 1800]
    assert style2 == "我想找更在地老店"

    # 測試情境 C: 空間與風格混合「走路10分鐘以內、平價一點」
    kw3, radii3, style3 = resolve_search_constraints(
        cuisine="拉麵",
        modifier="走路10分鐘以內 平價一點",
    )
    assert "平價一點" in kw3
    assert "走路" not in kw3
    assert "10分鐘" not in kw3
    assert radii3[0] == 800  # 10 * 80 = 800m

    # 2. 驗證食評雜訊濾網 (Metadata Noise Rejection)
    noise_ichiran = (
        "一蘭 新宿歌舞伎町店 (新宿西口/ラーメン) 営業時間 24時間営業 ※営業時間は変更になる場合がございます …"
    )
    assert is_pure_metadata_noise(noise_ichiran) is True
    assert sanitize_review_snippet(noise_ichiran) is None

    noise_lemon = "從新宿站西口步行6分鐘 從大江戶線新宿西口站（D5出口）步行2分鐘 從西武新宿站步行5分鐘. 距离西武新宿200 米 · 11:30 - 15:00"
    assert is_pure_metadata_noise(noise_lemon) is True
    assert sanitize_review_snippet(noise_lemon) is None

    # 真實口感心得應順利保留
    real_review = "肉質非常軟嫩，湯頭濃郁順口，特製松露蛋拌飯超級好吃！"
    cleaned_real = sanitize_review_snippet(real_review)
    assert cleaned_real is not None
    assert "肉質非常軟嫩" in cleaned_real

    # 3. 驗證智慧合成推薦理由 (Synthesized Reason Fallback)
    store_pure_noise = {
        "name_ja": "一蘭 新宿歌舞伎町店",
        "tabelog_rating": 3.10,
        "sample_reviews": [{"text": noise_ichiran}],
    }
    reason, badge = synthesize_store_recommendation_reason(store_pure_noise, cuisine="拉麵")
    assert "24時間" not in reason
    assert "営業時間" not in reason
    assert "ラーメン" not in reason
    assert "道地 拉麵 風味" in reason or "美味" in reason or "精選" in reason

    store_hyakumeiten = {
        "name_ja": "高級壽喜燒名店",
        "tabelog_rating": 3.75,
        "awards": ["壽喜燒 百名店 2026"],
        "sample_reviews": [{"text": noise_lemon}],
    }
    reason_h, badge_h = synthesize_store_recommendation_reason(store_hyakumeiten, cuisine="壽喜燒")
    assert "百名店" in reason_h
    assert "步行" not in reason_h
    assert badge_h == "百名店指標"

    # 4. 驗證端到端模板直出卡片排版
    mock_llm = MagicMock()
    mock_places = MagicMock()
    mock_scraper = MagicMock()
    fsm = FoodRecommendationFSM(llm_client=mock_llm, places_client=mock_places, tabelog_scraper=mock_scraper)

    test_stores = [
        {
            "name_ja": "壽喜燒極",
            "tabelog_rating": 3.52,
            "review_count": 820,
            "sample_reviews": [{"text": noise_ichiran}],
            "walking_desc": "從「新宿車站」步行約 8 分鐘 (629m)",
            "payment_methods_desc": "現金為主",
            "address": "東京都新宿區",
            "tabelog_url": "https://tabelog.com/tokyo/dummy",
        }
    ]
    card = fsm._render_template_fallback_card(test_stores, cuisine="壽喜燒", is_format_fallback=True)

    # 驗證卡片中 100% 絕無生硬營業時間或雜訊
    assert "24時間" not in card
    assert "営業時間" not in card
    assert "老饕推薦理由" in card
    assert "3.52" in card
    assert "`壽喜燒極`" in card


# ==============================================================================
# Case 14: 跨縣市同名防禦一票否決與多候選羅馬拼音正牌召回 (Cross-Prefecture Veto & Multi-Candidate Recall)
# ==============================================================================
@pytest.mark.asyncio
async def test_case14_cross_prefecture_hard_veto_and_cross_lingual_recall():
    """Case 14: 驗證跨縣市同名店家 (橫濱 Araiya vs 澀谷新井屋) 100% 被都道府縣硬性護欄一票否決，

    且多候選仲裁池能正確跳過雜訊分店，100% 正向召回正牌橫濱老字號荒井屋本店。
    """
    from unittest.mock import AsyncMock

    from src.services.entity_resolver import (
        EntityResolver,
        EntityVerifyResponse,
        extract_prefecture_from_address,
        extract_prefecture_from_tabelog_url,
    )

    # 1. 驗證都道府縣解析器之準確性
    assert extract_prefecture_from_tabelog_url("https://tabelog.com/kanagawa/A1401/A140104/14003068/") == "kanagawa"
    assert extract_prefecture_from_tabelog_url("https://tabelog.com/tokyo/A1303/A130301/13237382/") == "tokyo"
    assert (
        extract_prefecture_from_address("4-chōme-19-1 Kaigandōri, Naka Ward, Yokohama, Kanagawa 231-0002") == "kanagawa"
    )
    assert extract_prefecture_from_address("東京都渋谷区道玄坂2-19-11") == "tokyo"

    resolver = EntityResolver()

    # 模擬 Google 橫濱牛鍋名店 (Araiya)
    google_araiya = {
        "name": "Araiya",
        "address": "4-chōme-19-1 Kaigandōri, Naka Ward, Yokohama, Kanagawa 231-0002",
        "phone": "+81 45-226-5003",
        "lat": 35.4497,
        "lng": 139.6385,
    }

    # 2. 負向防禦：若被 SEO 誤召回東京都澀谷區的新井屋
    shibuya_candidate = [
        {
            "name_ja": "焼肉ホルモン 新井屋 渋谷",
            "tabelog_url": "https://tabelog.com/tokyo/A1303/A130301/13237382/",
            "address": "東京都渋谷区道玄坂2-19-11",
            "lat": 35.6595,
            "lng": 139.6961,
        }
    ]

    veto_res = await resolver.resolve_entity(
        google_store=google_araiya,
        tabelog_candidates=shibuya_candidate,
        allow_llm=True,
    )
    # 必須觸發都道府縣硬性一票否決
    assert veto_res["match_found"] is False
    assert veto_res["match_method"] == "geo_vetoed"

    # 3. 正向召回：真實候選清單 (1: 本店, 2: 萬國橋店, 3: ANNEX)
    yokohama_candidates = [
        {
            "name_ja": "荒井屋 本店",
            "tabelog_url": "https://tabelog.com/kanagawa/A1401/A140104/14003068/",
            "address": "神奈川県横浜市中区伊勢佐木町",
            "lat": 35.4428,
            "lng": 139.6291,
            "tabelog_rating": 3.60,
        },
        {
            "name_ja": "荒井屋 万國橋店",
            "tabelog_url": "https://tabelog.com/kanagawa/A1401/A140104/14009643/",
            "address": "神奈川県横浜市中区海岸通",
            "lat": 35.4501,
            "lng": 139.6379,
            "tabelog_rating": 3.55,
        },
        {
            "name_ja": "荒井屋ANNEX",
            "tabelog_url": "https://tabelog.com/kanagawa/A1401/A140104/14041185/",
            "address": "神奈川県横浜市中区",
            "lat": 35.4429,
            "lng": 139.6295,
            "tabelog_rating": 3.40,
        },
    ]

    # Mock LLM 裁決：比對 本店 時判定為 True，比對 ANNEX 時判定為 False
    async def mock_verify(g_store, t_cand):
        if "本店" in t_cand.get("name_ja", ""):
            return EntityVerifyResponse(
                is_same_store=True,
                confidence=0.98,
                reason="店名 Araiya 完全為荒井屋之羅馬拼音，且位於橫濱市中區，為老字號本店",
            )
        return EntityVerifyResponse(
            is_same_store=False,
            confidence=0.20,
            reason="分店不同",
        )

    resolver._verify_with_llm = AsyncMock(side_effect=mock_verify)

    recall_res = await resolver.resolve_entity(
        google_store=google_araiya,
        tabelog_candidates=yokohama_candidates,
        allow_llm=True,
    )

    # 驗證多候選仲裁池順利跨過 ANNEX 雜訊，成功命中「荒井屋 本店」
    assert recall_res["match_found"] is True
    assert recall_res["match_method"] == "llm_verify"
    assert recall_res["candidate"]["name_ja"] == "荒井屋 本店"
    assert recall_res["candidate"]["tabelog_rating"] == 3.60


@pytest.mark.asyncio
async def test_case_15_yokohama_sukiyaki_zero_hallucination_and_grounding():
    """Case 15: 橫濱公園壽喜燒防幻覺與 Grounding 守門 (避免憑空捏造「一蘭」、「吉田」等假店家與 maps/search 偽造連結)

    真實案例回歸：
    使用者提問「橫濱公園附近的壽喜燒」。
    候選名單經去重後僅有 1 家經典名店「荒井屋 本店」。
    LLM 為了「湊滿 3 家」引發嚴重幻覺，自造不存在的「壽喜燒 一蘭」、「壽喜燒 吉田」，並偽造 maps/search 連結。

    守門防禦目標：
    1. Validator 檢測到 maps/search 偽造連結或未在候選池中的店名，強制一票否決！
    2. 自動觸發 Template Fallback 保底機制，安全輸出 100% 真實店家的推薦卡片。
    3. 卡片中誠實標註目前範圍僅有該名店，並引導使用者擴大商圈。
    """
    fsm = FoodRecommendationFSM(
        places_client=MagicMock(),
        tabelog_scraper=MagicMock(),
        entity_resolver=MagicMock(),
        llm_client=MagicMock(),
    )

    real_stores = [
        {
            "name": "Araiya",
            "name_ja": "荒井屋 本店",
            "tabelog_url": "https://tabelog.com/kanagawa/A1401/A140104/14003068/",
            "tabelog_rating": 3.60,
            "review_count": 664,
            "google_rating": 4.3,
            "walking_desc": "從「橫濱公園」步行約 14 分鐘 (1,069m)",
            "payment_methods_desc": "現金為主 (不支援信用卡/行動支付，請備足現鈔)",
            "price_dinner": "￥3,000~￥5,000",
            "address": "神奈川県横浜市中区旭町",
        }
    ]

    # 模擬 LLM 嚴重幻覺輸出（包含腦補的一蘭與 maps/search 連結）
    hallucinated_llm_output = """
#### 1. 荒井屋 本店 (Araiya Honten)
`荒井屋 本店`
🚶 **步行距離**：從「橫濱公園」步行約 14 分鐘 (1,069m)
⭐️ **雙平台評分**：⭐️ Tabelog 3.60 (664 則評價) / Google 4.3
💳 **支付方式**：現金為主 (不支援信用卡/行動支付，請備足現鈔)
💰 **預算區間**：晚餐約 ￥3,000~￥5,000
📍 **老饕推薦理由 (食べログ 口コミ)**：創業 120 年以上的歷史老舖。
🔗 **店家連結**：[Tabelog 頁面](https://tabelog.com/kanagawa/A1401/A140104/14003068/)

#### 2. 壽喜燒 一蘭 (Sukiyaki Ichiran) - *補充推薦*
`寿喜焼 一蘭`
🚶 **步行距離**：從「橫濱公園」步行約 8 分鐘 (600m)
⭐️ **雙平台評分**：⭐️ Tabelog 3.55 / Google 4.2
💳 **支付方式**：可使用信用卡 / PayPay
💰 **預算區間**：晚餐約 ￥2,500~￥4,000
📍 **老饕推薦理由 (食べログ 口コミ)**：位於關內核心區。
🔗 **店家連結**：[Google Maps 搜尋](https://www.google.com/maps/search/寿喜焼+一蘭+横浜)
"""
    fsm.llm_client.call_text.return_value = (hallucinated_llm_output, "llama-3.3-70b-versatile", 250)

    # 執行 State 4 合成
    result_card = await fsm.execute_state_4_synthesis(
        stores=real_stores,
        cuisine="壽喜燒",
        location_name="橫濱公園",
    )

    # 驗證守門攔截生效：結果卡片絕不含「一蘭」或「maps/search」
    assert "一蘭" not in result_card
    assert "google.com/maps/search" not in result_card
    # 驗證自動觸發了安全保底模板，呈現真實店家「荒井屋 本店」
    assert "`荒井屋 本店`" in result_card
    assert "3.60" in result_card
    # 驗證包含少於 3 家時的誠實引導
    assert "特派員提醒" in result_card
    assert "1 家" in result_card


@pytest.mark.asyncio
async def test_case_16_payment_options_dual_source_fusion_prevent_false_negative():
    """Case 16: 雙源支付方式融合與防假陰性誤判 (Google Places paymentOptions + Tabelog)

    真實案例回歸：
    使用者提問：「荒井屋明明就可以刷卡 為什麼訊息說不行呢？」
    根本原因：Tabelog 內頁遭 403 阻擋降級至 Web Grounding，導致 Tabelog 支付方式為 None，
    系統卻誤將 None 當作 [] (不支援信用卡)，進而鐵口直斷「現金為主 (不支援信用卡/行動支付)」。
    但 Google Places 原生已明確標註 acceptsCreditCards: True！

    守門防禦目標：
    1. 雙源融合：Tabelog 缺失時，Google Places 的 accepts_credit_cards 成功補位，精準標記「可使用 信用卡」。
    2. 防武斷否定：資料未知 (None) 時絕不武斷斷言「不支援信用卡」，僅溫和提示「建議備足現鈔」。
    3. 卡片產出：荒井屋推薦卡片中支付方式為「可使用 信用卡」，徹底消除烏龍誤殺。
    """
    from src.agent.pipeline import format_payment_methods

    # 1. 單元層面：驗證 format_payment_methods 雙源融合邏輯
    # (a) Tabelog 缺 (None)，但 Google 標記可刷卡 -> 判定為可使用信用卡
    pay_google_rescue = format_payment_methods(
        supports_paypay=None,
        credit_cards=None,
        accepts_credit_cards=True,
    )
    assert pay_google_rescue == "可使用 信用卡"
    assert "不支援信用卡" not in pay_google_rescue

    # (b) Tabelog 標記具體卡別 -> 優先呈現詳細卡別
    pay_tabelog_cards = format_payment_methods(
        credit_cards=["VISA", "Master", "JCB"],
        accepts_credit_cards=True,
    )
    assert pay_tabelog_cards == "可使用 信用卡 (VISA、Master、JCB)"

    # (c) 雙邊皆未知 (None) -> 溫和提示，絕不宣稱不支援信用卡
    pay_unknown = format_payment_methods(
        supports_paypay=None,
        credit_cards=None,
        accepts_credit_cards=None,
        accepts_cash_only=None,
    )
    assert pay_unknown == "現金為主 (建議備足日圓現鈔)"
    assert "不支援信用卡" not in pay_unknown

    # (d) 明確標記僅收現金 -> 始可輸出不支援信用卡
    pay_cash_only = format_payment_methods(accepts_cash_only=True)
    assert "不支援信用卡/行動支付" in pay_cash_only

    # 2. 端到端卡片生成驗證：以荒井屋本店為例
    fsm = FoodRecommendationFSM(
        places_client=MagicMock(),
        tabelog_scraper=MagicMock(),
        entity_resolver=MagicMock(),
        llm_client=MagicMock(),
    )
    araiya_store = [
        {
            "name": "Araiya",
            "name_ja": "荒井屋 本店",
            "tabelog_url": "https://tabelog.com/kanagawa/A1401/A140104/14003068/",
            "tabelog_rating": 3.60,
            "review_count": 664,
            "google_rating": 4.3,
            "walking_desc": "從「橫濱公園」步行約 14 分鐘 (1,069m)",
            "supports_paypay": None,
            "credit_cards": None,
            "accepts_credit_cards": True,  # Google Places 救援
            "accepts_cash_only": False,
            "price_dinner": "￥3,000~￥5,000",
            "address": "神奈川県横浜市中区旭町",
        }
    ]

    # 透過模板生成直出卡片
    card = fsm._render_template_fallback_card(araiya_store, "壽喜燒")
    assert "可使用 信用卡" in card
    assert "不支援信用卡/行動支付" not in card


def test_shiten_coffee_payment_accepts_cash_only_false_regression():
    """回歸測試：SHI-TEN coffee 支付方式判定修復

    根本原因：
    Google Places API (New) 回傳 paymentOptions: { "acceptsCashOnly": false }，
    但未帶 acceptsCreditCards 欄位 (None)，且 Tabelog 爬蟲因 403 阻擋未能提取具體卡別清單時，
    format_payment_methods() 原先僅檢查 accepts_credit_cards is True，
    忽略 accepts_cash_only is False 之非現金專門店信號，導致直接跌落兜底邏輯誤判為「現金為主 (建議備足日圓現鈔)」。

    修復邏輯：
    1. 當 accepts_cash_only is False 且未被 Tabelog 明確否定為 0 卡時，判定為「可使用 信用卡 / 電子支付」。
    2. 絕不在此情境下輸出「現金為主」。
    """
    from src.agent.pipeline import FoodRecommendationFSM, format_payment_methods

    # 1. 單元層面：驗證 accepts_cash_only=False 雙源融合
    pay_non_cash = format_payment_methods(
        supports_paypay=None,
        credit_cards=None,
        accepts_credit_cards=None,
        accepts_cash_only=False,  # Google Places 明確標註非僅收現金
    )
    assert pay_non_cash == "可使用 信用卡 / 電子支付"
    assert "現金為主" not in pay_non_cash

    # 2. 端到端卡片生成驗證：以 SHI-TEN coffee 為例
    fsm = FoodRecommendationFSM(
        places_client=MagicMock(),
        tabelog_scraper=MagicMock(),
        entity_resolver=MagicMock(),
        llm_client=MagicMock(),
    )
    shiten_store = [
        {
            "name": "SHI-TEN coffee",
            "name_ja": "SHI-TEN coffee",
            "tabelog_url": "https://tabelog.com/tokyo/A1311/A131101/13234338/",
            "tabelog_rating": 3.30,
            "review_count": 75,
            "google_rating": 4.6,
            "walking_desc": "從「末廣町站」步行約 3 分鐘 (219m)",
            "supports_paypay": None,
            "credit_cards": None,
            "accepts_credit_cards": None,
            "accepts_cash_only": False,  # Google Places: acceptsCashOnly = false
            "price_dinner": None,
            "address": "東京都千代田区外神田5-4-12",
        }
    ]

    card = fsm._render_template_fallback_card(shiten_store, "咖啡")
    assert "可使用 信用卡 / 電子支付" in card
    assert "現金為主" not in card


# ==============================================================================
# Case 18: Tabelog 食評優先覆蓋 Google 評論防短路 (Pharos Coffee Jimbocho Regression)
# ==============================================================================
@pytest.mark.asyncio
async def test_case18_pharos_coffee_triggers_tabelog_overrule_despite_google_reviews():
    """Case 18: 驗證當 Google Places 預帶 5 則評論時，
    系統絕不被 has_authentic 假陰性短路，而是只要配對到 Tabelog，
    100% 平行觸發 fetch_tabelog_reviews，並在抓到老饕口コミ後全面覆蓋 Google 評論。
    """
    fsm = FoodRecommendationFSM(
        places_client=MagicMock(),
        tabelog_scraper=MagicMock(),
        entity_resolver=MagicMock(),
        llm_client=MagicMock(),
    )

    # 模擬 Pharos Coffee：已從 Google Places 帶入 5 則英文評論
    google_reviews = [
        {"source": "google", "text": "A High-Resolution Lunch Break...", "author": "PaPi"},
        {"source": "google", "text": "Visited Pharos after a long day...", "author": "Neil"},
    ]
    pharos_store = {
        "google_place_id": "place_pharos_123",
        "name": "Pharos Coffee",
        "name_ja": "Pharos Coffee",
        "tabelog_url": "https://tabelog.com/tokyo/A1310/A131003/13281433/",
        "tabelog_rating": 3.4,
        "sample_reviews": list(google_reviews),
    }

    # 模擬 Tabelog 定向爬蟲回傳日本老饕真實短評
    tabelog_reviews = [
        {
            "source": "tabelog",
            "title": "浅煎りの名店",
            "text": "神保町の路地裏にある隠れ家。エチオピア浅煎りの華やかな酸味と香りが素晴らしい。",
            "original_text": "神保町の路地裏にある隠れ家。エチオピア浅煎りの華やかな酸味と香りが素晴らしい。",
        }
    ]
    fsm.tabelog_scraper.fetch_tabelog_reviews = AsyncMock(return_value=tabelog_reviews)

    fsm._index_store_reviews = AsyncMock()

    # 執行 State 3 老饕食評豐富化 (由 execute_state_3_parallel_tabelog 觸發)
    tier1_res = [dict(pharos_store)]
    fsm._enrich_single_place = AsyncMock(return_value=tier1_res[0])
    fsm.rank_and_select_top_stores = MagicMock(return_value=tier1_res)

    results = await fsm.execute_state_3_parallel_tabelog([pharos_store])

    # 驗證 Tabelog 評論成功覆蓋原本的 Google 評論
    assert len(results) == 1
    assert results[0]["foodie_review_source"] == "tabelog"
    assert "神保町の路地裏にある隠れ家" in results[0]["foodie_review_highlight"]
    assert results[0]["sample_reviews"][0]["source"] == "tabelog"
    fsm.tabelog_scraper.fetch_tabelog_reviews.assert_called_once()


# ==============================================================================
# Case 19: 精準來源標籤對齊與 No-Rating-As-Reason 護欄 (Hayakawatei Regression)
# ==============================================================================
def test_case19_accurate_review_attribution_and_no_rating_boilerplate():
    """Case 19: 驗證：
    1. 來源標籤嚴格由資料血統決定，未配對 Tabelog 之評論絕不標記為 (食べログ 口コミ)。
    2. Guardrail 精準攔截「Google 評分高達 4.9 分...」等拿評分數值充當推薦理由的客套廢話。
    """
    # 1. 模擬包含評分客套話的違規卡片 (早川亭真實錯誤現場還原)
    bad_boilerplate_card = """
### 🍱 精選推薦 1：coffeestand早川亭
`coffeestand早川亭`

🚶 **步行距離**：從「神保町車站」步行約 8 分鐘 (572m)
⭐️ **雙平台評分**：⭐️ Tabelog 3.2 (49 則評價) / Google 4.9
💳 **支付方式**：可使用 信用卡 / 感應支付
💰 **預算區間**：未提供
📍 **老饕推薦理由 (食べログ 口コミ)**：Google 評分高達 4.9 分，顯示在地遊客對其咖啡品質極度認可，雖 Tabelog 評價較少，但高 Google 評分通常代表穩定的日常高品質表現。
🔗 **店家連結**：https://tabelog.com/tokyo/A1319/A131901/13183537/
"""
    is_valid, reasons = validate_recommendation_card(bad_boilerplate_card)
    assert is_valid is False
    assert any("No-Rating-As-Reason 違規" in r for r in reasons)

    # 2. 模擬合規的真實風味口感卡片
    good_flavor_card = """
### 🍱 精選推薦 1：coffeestand早川亭
`coffeestand早川亭`

🚶 **步行距離**：從「神保町車站」步行約 8 分鐘 (572m)
⭐️ **雙平台評分**：⭐️ Tabelog 3.2 (49 則評價) / Google 4.9
💳 **支付方式**：可使用 信用卡 / 感應支付
💰 **預算區間**：約 ￥500 ~ ￥1,000
📍 **老饕推薦理由 (Google 在地評價)**：店主親手調製的手作焦糖拿鐵香氣極度濃郁，自家製巴斯克乳酪蛋糕口感綿密滑順，是飯田橋與神保町一帶不可錯過的職人外帶咖啡站。
🔗 **店家連結**：https://tabelog.com/tokyo/A1319/A131901/13183537/
"""
    is_valid_good, good_reasons = validate_recommendation_card(good_flavor_card)
    assert is_valid_good is True
    assert len(good_reasons) == 0
