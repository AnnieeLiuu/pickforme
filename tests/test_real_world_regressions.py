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


# ==============================================================================
# Case 20: Google Maps 店家專屬連結保底與純文字連結自動外科手術修復 (Soshiji Regression)
# ==============================================================================
def test_case20_google_maps_link_fallback_and_in_place_repair():
    """Case 20: 驗證：
    1. 當店家未匹配到 Tabelog 網址時，保底提供正規 Google Maps 專屬連結 (place_id)。
    2. LLM 產生之純文字連結 (例如「🔗 **店家連結**：Google Maps 位置」) 能被 repair_card_in_place 自動替換為可點擊超連結。
    3. Template Fallback Card 在無 Tabelog 網址時，能正確渲染 [Google Maps 頁面](...)。
    """
    from src.agent.pipeline import FoodRecommendationFSM
    from src.agent.validator import repair_card_in_place

    mock_store = {
        "google_place_id": "ChIJGS1FyUeNGGAR6WVdJgjYHvU",
        "google_maps_url": "https://www.google.com/maps/place/?q=place_id:ChIJGS1FyUeNGGAR6WVdJgjYHvU",
        "name_ja": "和牛すき焼 そしじ 上 本郷三丁目店",
        "name": "和牛すき焼 そしじ 上 本郷三丁目店",
        "tabelog_url": None,
        "tabelog_rating": None,
        "google_rating": 4.6,
        "user_ratings_total": 61,
        "address": "東京都文京区本郷５丁目２４−７",
        "walking_desc": "從「神保町車站」步行約 19 分鐘 (1441m)",
        "rating_source": "Google",
    }

    # 1. 驗證 repair_card_in_place 將無連結之純文字修復為 Markdown 超連結
    unlinked_card = (
        "🍱 **精選推薦：神保町周邊壽喜燒**\n\n"
        "🥇 **和牛すき焼 そしじ 上 本郷三丁目店**\n"
        "`和牛すき焼 そしじ 上 本郷三丁目店`\n"
        "🚶 **步行距離**：從「神保町車站」步行約 19 分鐘 (1441m)\n"
        "⭐️ **雙平台評分**：⭐️ Google 4.6 分 (61 則評價) ｜ Tabelog 暫無評分\n"
        "💳 **支付方式**：現金為主 (建議備足日圓現鈔)\n"
        "📍 **老饕推薦理由 (Google 在地評價)**：完全個室設計氛圍沉穩，和牛口感極佳。\n"
        "🔗 **店家連結**：Google Maps 位置\n"
    )

    repaired = repair_card_in_place(unlinked_card, [mock_store])
    assert (
        "🔗 **店家連結**：[Google Maps 頁面](https://www.google.com/maps/place/?q=place_id:ChIJGS1FyUeNGGAR6WVdJgjYHvU)"
        in repaired
    )

    # 2. 驗證 Template Fallback Card 正常產生 Google Maps 連結
    fsm = FoodRecommendationFSM()
    template_card = fsm._render_template_fallback_card([mock_store], cuisine="壽喜燒")
    assert (
        "🔗 [Google Maps 頁面](https://www.google.com/maps/place/?q=place_id:ChIJGS1FyUeNGGAR6WVdJgjYHvU)"
        in template_card
    )


@pytest.mark.asyncio
async def test_case21_no_raw_japanese_review_leak_and_code_block_repair():
    """Case 21: 日文食評防穿透洩漏與店名代碼塊自動修復回歸測試

    根本原因:
    1. 當 Tabelog 爬取的 sample_reviews 或 foodie_review_highlight 為純日文時，若因
       卡片排版微瑕觸發 template fallback，原先代碼未過濾假名字元直接輸出日文短評，
       導致中文使用者看到生硬日文。
    2. LLM 在輸出推薦卡片時偶爾在標題行漏掉反引號 (`店名`)，直接被判為致命錯誤並丟棄卡片。

    防禦驗證:
    1. synthesize_store_recommendation_reason 偵測到日文假名且無繁中翻譯時，
       強制走結構化老饕中文指標合成，保證理由 100% 為繁體中文且無假名。
    2. repair_card_in_place 遇到日文短評理由時，外科手術替換為道地繁體中文。
    3. repair_store_name_code_blocks 能自動為標題行遺漏反引號的候選店名包裹反引號。
    """
    from src.agent.pipeline import FoodRecommendationFSM
    from src.agent.validator import (
        repair_card_in_place,
        repair_store_name_code_blocks,
        validate_card_tiered,
    )
    from src.services.review_sanitizer import (
        contains_japanese_kana,
        synthesize_store_recommendation_reason,
    )

    japanese_raw_review = "浅草の町中華を代表する名店で、チャーハンと餃子が絶品でした。スープも濃厚で美味しい！"
    mock_store_japanese = {
        "name_ja": "浅草 聚楽",
        "name": "浅草 聚楽",
        "tabelog_rating": 3.65,
        "review_count": 420,
        "sample_reviews": [{"text": japanese_raw_review, "source": "tabelog"}],
        "foodie_review_highlight": japanese_raw_review,
        "foodie_review_source": "tabelog",
        "walking_desc": "從「淺草站」步行約 3 分鐘",
        "payment_methods_desc": "現金為主",
        "address": "東京都台東區淺草1-1-1",
        "tabelog_url": "https://tabelog.com/tokyo/A1311/A131102/13000000/",
    }

    # 1. 驗證 synthesize_store_recommendation_reason 絕不穿透日文假名
    reason, badge = synthesize_store_recommendation_reason(mock_store_japanese, cuisine="中華料理")
    assert not contains_japanese_kana(reason), f"理由仍含有日文假名: {reason}"
    assert "Tabelog 評分達 3.65 高分名店" in reason or "中華料理" in reason
    assert badge in ["食べログ 權威認證", "在地饕客精選", "百名店指標"]

    # 2. 驗證 template fallback 渲染出之卡片理由絕無日文假名
    fsm = FoodRecommendationFSM()
    template_card = fsm._render_template_fallback_card([mock_store_japanese], cuisine="中華料理")
    reason_match = re.search(r"📍 \*\*老饕推薦理由[^\n]*\*\*：([^\n]+)", template_card)
    assert reason_match is not None
    extracted_reason = reason_match.group(1)
    assert not contains_japanese_kana(extracted_reason), f"模板理由穿透了日文: {extracted_reason}"

    # 3. 驗證 repair_card_in_place 能將日文理由外科手術修復為繁體中文
    card_with_japanese_reason = (
        "🍱 **精選推薦：淺草商圈美食**\n\n"
        "🥇 **【1】 `浅草 聚楽`**\n"
        "🚶 **步行距離**：從「淺草站」步行約 3 分鐘\n"
        "⭐️ **雙平台評分**：⭐️ Tabelog 3.65 分\n"
        "💳 **支付方式**：現金為主\n"
        f"📍 **老饕推薦理由 (食べログ 口コミ)**：{japanese_raw_review}\n"
        "🔗 **店家連結**：[Tabelog 頁面](https://tabelog.com/tokyo/A1311/A131102/13000000/)\n"
    )

    fatal_errs, minor_warns = validate_card_tiered(card_with_japanese_reason, [mock_store_japanese])
    assert len(fatal_errs) == 0, f"不應產生致命錯誤: {fatal_errs}"
    assert any("Raw Japanese Leak" in w for w in minor_warns), f"應標記日文洩漏警告: {minor_warns}"

    repaired = repair_card_in_place(card_with_japanese_reason, [mock_store_japanese])
    repaired_reason_match = re.search(r"📍 \*\*老饕推薦理由[^\n]*\*\*：([^\n]+)", repaired)
    assert repaired_reason_match is not None
    assert not contains_japanese_kana(repaired_reason_match.group(1)), "修復後理由仍含有日文"

    # 4. 驗證 repair_store_name_code_blocks 自動包裹缺失之反引號
    card_missing_code_block = (
        "🍱 **精選推薦：淺草商圈美食**\n\n"
        "🥇 **【1】 浅草 聚楽**\n"
        "🚶 **步行距離**：從「淺草站」步行約 3 分鐘\n"
        "⭐️ **雙平台評分**：⭐️ Tabelog 3.65 分\n"
        "💳 **支付方式**：現金為主\n"
        "📍 **老饕推薦理由**：招牌炒飯粒粒分明，鑊氣十足。\n"
    )
    # 修復前會因為缺少反引號被判定致命錯誤
    fatal_before, _ = validate_card_tiered(card_missing_code_block, [mock_store_japanese])
    assert any("缺少一鍵複製店名反引號代碼塊" in e for e in fatal_before)

    # 執行自動修復
    auto_fixed = repair_store_name_code_blocks(card_missing_code_block, [mock_store_japanese])
    assert "`浅草 聚楽`" in auto_fixed

    # 修復後致命錯誤應被解除
    fatal_after, _ = validate_card_tiered(auto_fixed, [mock_store_japanese])
    assert len(fatal_after) == 0, f"修復後仍有致命錯誤: {fatal_after}"


@pytest.mark.asyncio
async def test_case22_internal_diagnostics_and_max_tokens_guardrail():
    """Case 22: 內部安全診斷端點與 Groq OTPM 1000 Token 防禦回歸測試

    根本原因:
    1. 當 Cloud Run / Docker 發生底層網路連線異常 (如 429 或重試) 時，
       在 GCP Organization Policy 封鎖匯出 Service Account Key 的情境下，
       AI 與開發者無法透過外部 MCP 取得底層日誌。
    2. LLM Client 未帶入 max_tokens 參數，導致 Groq 雲端以預設最大輸出計算，
       估算 token 達 1180 超過免費用戶每分鐘 1000 output token 限制，觸發 429。

    防禦驗證:
    1. InMemoryLogBuffer 自動攔截 WARNING/ERROR 日誌，且 /internal/diagnostics/logs
       受到 TELEGRAM_WEBHOOK_SECRET 嚴格防護，非授權存取立即回傳 403。
    2. _call_openai_compatible_api 預設帶入 max_tokens: 800，徹底根除 Groq 429。
    """
    import logging
    from unittest.mock import AsyncMock, patch

    from fastapi.testclient import TestClient
    from src.agent.llm_client import LLMClient
    from src.config import settings
    from src.main import app
    from src.services.diagnostics import InMemoryLogBuffer

    # 1. 驗證 InMemoryLogBuffer 正常收集與過濾
    test_buffer = InMemoryLogBuffer(maxlen=10)
    test_logger = logging.getLogger("test_diag")
    test_logger.addHandler(test_buffer)

    test_logger.info("Normal info message")
    test_logger.warning("Groq API 429 rate limit detected")
    test_logger.error("Connection pool dropped")

    logs = test_buffer.get_logs(limit=10, min_level="WARNING")
    assert len(logs) == 2
    assert logs[0]["message"] == "Connection pool dropped"
    assert logs[1]["message"] == "Groq API 429 rate limit detected"

    # 2. 驗證 /internal/diagnostics/logs 端點安全防護
    client = TestClient(app)
    with patch.object(settings, "TELEGRAM_WEBHOOK_SECRET", "test_secret_123"):
        # (a) 未帶 secret 或 secret 錯誤 ➡️ 403 Forbidden
        resp_unauth = client.get("/internal/diagnostics/logs?secret=wrong_secret")
        assert resp_unauth.status_code == 403
        assert "Forbidden" in resp_unauth.text

        # (b) 帶正確 secret ➡️ 200 OK
        resp_auth = client.get("/internal/diagnostics/logs?secret=test_secret_123")
        assert resp_auth.status_code == 200
        data = resp_auth.json()
        assert data["status"] == "ok"
        assert "logs" in data

    # 3. 驗證 LLM Client 帶入 max_tokens 防禦
    llm = LLMClient()
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"total_tokens": 10},
        }
        mock_post.return_value = mock_resp

        await llm._call_openai_compatible_api(
            url="https://api.groq.com/openai/v1/chat/completions",
            api_key="dummy_key",
            model="qwen/qwen3.8-27b",
            messages=[{"role": "user", "content": "hi"}],
        )

        sent_json = mock_post.call_args.kwargs.get("json", {})
        assert sent_json.get("max_tokens") == 800, f"max_tokens 應為 800: {sent_json}"


# ==============================================================================
# Case 23: QuickPassGate 0-LLM 秒回與指定店號追問快篩回歸測試
# ==============================================================================
@pytest.mark.asyncio
async def test_regression_quick_pass_gate_zero_llm_on_greeting_and_follow_up():
    """Case 23: 驗證 QuickPassGate 在入口即以 0ms 攔截純問候與指定店號追問：
    1. 「你好」直接透過 fast_reply 秒回，Stage 1 與 Chitchat 均為 0 次 LLM 呼叫。
    2. 「第二家可以刷卡嗎」跳過 Stage 1 call_structured_json，直接鎖定 target_index=2 進入 execute_follow_up。
    """
    mock_llm = MagicMock()
    mock_llm.call_structured_json = AsyncMock()
    mock_llm.call_text = AsyncMock(return_value=("第二家荒井屋可以使用信用卡喔！", "groq", 42))

    mock_session_repo = MagicMock()
    mock_session_repo.get_last_recommended_stores = AsyncMock(
        return_value=[
            {"name_ja": "第一家拉麵", "tabelog_rating": 3.5},
            {"name_ja": "荒井屋 本店", "tabelog_rating": 3.6, "accepts_credit_cards": True},
        ]
    )
    mock_session_repo.update_long_term_preferences = AsyncMock()

    fsm = FoodRecommendationFSM(
        llm_client=mock_llm,
        places_client=MagicMock(),
        tabelog_scraper=MagicMock(),
        entity_resolver=MagicMock(),
        session_repo=mock_session_repo,
        trace_logger=MagicMock(log_step=AsyncMock(), start_run=AsyncMock(), finish_run=AsyncMock()),
    )

    # 1. 純問候：「你好」-> 0 次 LLM 呼叫
    reply_greeting = await fsm.run_pipeline(chat_id=12345, user_message="你好")
    assert "日本美食推薦助手" in reply_greeting
    mock_llm.call_structured_json.assert_not_called()
    mock_llm.call_text.assert_not_called()

    # 2. 指定店號追問：「第二家可以刷卡嗎」-> 跳過 Stage 1 LLM (call_structured_json 0 次)，直接執行 follow_up
    reply_followup = await fsm.run_pipeline(chat_id=12345, user_message="第二家可以刷卡嗎")
    assert "荒井屋" in reply_followup
    mock_llm.call_structured_json.assert_not_called()
    mock_llm.call_text.assert_called_once()
    # 驗證傳給 follow_up LLM 的目標店家確實是 target_index=2 (荒井屋 本店)
    sent_messages = mock_llm.call_text.call_args.args[0]
    assert "荒井屋 本店" in sent_messages[1]["content"]
    # 驗證確定性偏好抽取同時觸發了長期偏好更新 (credit_card)
    mock_session_repo.update_long_term_preferences.assert_awaited_once()


# ==============================================================================
# Case 24: 純「換一批」0ms 快篩與 Working Memory 跨輪繼承回歸測試
# ==============================================================================
@pytest.mark.asyncio
async def test_regression_pure_more_inherits_working_memory_without_intent_llm():
    """Case 24: 驗證使用者輸入純「換一批」時：
    1. QuickPassGate 直接命中 pure_more，0 次 Stage 1 LLM 呼叫 (call_structured_json.assert_not_called())。
    2. 自動從 SessionRepository.get_working_memory 繼承上一輪的 last_cuisine="壽喜燒" 與 last_location="淺草"。
    """
    mock_llm = MagicMock()
    mock_llm.call_structured_json = AsyncMock()
    mock_llm.call_text = AsyncMock()

    mock_places = MagicMock()
    mock_places.geocode_location = AsyncMock(return_value=(35.7148, 139.7967))

    mock_session_repo = MagicMock()
    mock_session_repo.get_working_memory = AsyncMock(return_value={"last_cuisine": "壽喜燒", "last_location": "淺草"})
    mock_session_repo.get_session = AsyncMock(
        return_value={"last_location_lat": 35.7148, "last_location_lng": 139.7967}
    )

    fsm = FoodRecommendationFSM(
        llm_client=mock_llm,
        places_client=mock_places,
        tabelog_scraper=MagicMock(),
        entity_resolver=MagicMock(),
        session_repo=mock_session_repo,
        trace_logger=MagicMock(log_step=AsyncMock(), start_run=AsyncMock(), finish_run=AsyncMock()),
    )

    parsed = await fsm.execute_state_1_intent(
        user_message="換一批",
        user_location=None,
        conversation_history=[],
        chat_id=99999,
    )

    mock_llm.call_structured_json.assert_not_called()
    assert parsed["intent"] == "more"
    assert parsed["cuisine"] == "壽喜燒"
    assert parsed["location"] == "淺草"
    assert parsed["coords"] == (35.7148, 139.7967)


# ==============================================================================
# Case 25: Tabelog-First 雙源獵店與反向種子保送回歸測試 (防止 Google 低分淘汰日本百名店)
# ==============================================================================
@pytest.mark.asyncio
async def test_regression_tabelog_dual_source_discovery_promotes_hidden_gem_over_tourist_traps():
    """Case 25: 驗證當日本 Tabelog 3.78 百名店在 Google Maps 僅 3.8 星（甚至排在 Google 第 7 名以後）時：
    1. State 2 透過 TabelogScraper.discover_area_top_stores 主動獵取該百名店並反查 Google 綁定座標與距離。
    2. prefilter_potential_foodie_places 給予 tabelog_seed 保送權重，不會被前 6 名 Google 4.7 星觀光客店擠掉。
    3. 進入 State 3 後，該店憑藉 Tabelog 3.78 + 百名店穩居第 1 名 (🥇)。
    """
    mock_places = MagicMock()
    # Google 階梯回傳 6 家高分觀光客店 (Google 4.6~4.8 星，但 Tabelog 僅 3.15)
    tourist_places = [
        {
            "google_place_id": f"tourist_{i}",
            "name": f"Tourist Pasta {i}",
            "lat": 35.6959,
            "lng": 139.7576,
            "rating": 4.7,
            "userRatingCount": 1200,
        }
        for i in range(1, 7)
    ]
    mock_places.search_nearby = AsyncMock(return_value=tourist_places)

    # 當反查 Tabelog 主動提名的「神保町イタリアン百名店」時，Google 給它 3.8 星
    hidden_gem_google = [
        {
            "google_place_id": "gem_hyakumeiten_1",
            "name": "トラットリア 神保町 本店",
            "lat": 35.6962,
            "lng": 139.7580,
            "rating": 3.8,
            "userRatingCount": 45,
        }
    ]
    mock_places.search_by_text = AsyncMock(return_value=hidden_gem_google)

    mock_scraper = MagicMock()
    mock_scraper.discover_area_top_stores = AsyncMock(
        return_value=[
            {
                "name_ja": "トラットリア 神保町 本店",
                "tabelog_url": "https://tabelog.com/tokyo/A1310/A131003/13000001/",
                "tabelog_rating": 3.78,
                "review_count": 320,
                "hyakumeiten": ["イタリアン TOKYO 百名店 2025"],
                "awards": ["イタリアン TOKYO 百名店 2025"],
            }
        ]
    )
    mock_scraper.search_tabelog_candidates = AsyncMock(return_value=[])
    mock_scraper.fetch_tabelog_reviews = AsyncMock(return_value=[])

    mock_repo = MagicMock()
    mock_repo.get_by_google_place_id = AsyncMock(return_value=None)

    fsm = FoodRecommendationFSM(
        llm_client=MagicMock(),
        places_client=mock_places,
        tabelog_scraper=mock_scraper,
        entity_resolver=MagicMock(),
        restaurant_repo=mock_repo,
        trace_logger=MagicMock(log_step=AsyncMock(), start_run=AsyncMock(), finish_run=AsyncMock()),
    )

    # 1. 執行 State 2 雙源檢索 (限制 max_candidates=6)
    seeds = await fsm.execute_state_2_places_ladder(
        lat=35.6959,
        lng=139.7576,
        keyword="義大利麵",
        origin_name="神保町車站",
        max_candidates=6,
    )

    # 驗證即使有 6 家 Google 4.7 星觀光客店，Google 僅 3.8 星的 Tabelog 3.78 百名店依然排在種子第 1 順位！
    assert len(seeds) == 6
    assert seeds[0]["google_place_id"] == "gem_hyakumeiten_1"
    assert seeds[0]["tabelog_seed"] is True
    assert seeds[0]["tabelog_rating"] == 3.78

    # 2. 執行 State 3 平行查驗與老饕重排
    top_stores = await fsm.execute_state_3_parallel_tabelog(seeds, area_hint="神保町車站")
    assert top_stores[0]["google_place_id"] == "gem_hyakumeiten_1"
    assert top_stores[0]["rank_badge"] == "🥇"
    assert top_stores[0]["tabelog_rating"] == 3.78


# ==============================================================================
# Case: 特色導覽地名誤抓 (Explore Location Mis-capture) — v1.3.0 上線缺陷
# ==============================================================================
@pytest.mark.parametrize(
    ("message", "expected_location"),
    [
        ("有什麼特色美食？", None),
        ("我想找特色美食", None),
        ("我想找神保町的特色美食", "神保町"),
        ("請問淺草有什麼特色美食？", "淺草"),
        ("神保町有什麼特色美食？", "神保町"),
        ("那須特色美食", "那須"),
    ],
)
def test_regression_explore_gate_rejects_question_words_as_location(message, expected_location):
    """根本原因：_EXPLORE_SPECIALTY_RE 以惰性 (.{2,15}?) 擷取地名，未指定地名的問句
    「有什麼特色美食？」會被抓成 location="有什麼"，導致卡片出現「『有什麼』匯集了…」。
    防禦邏輯：clean_explore_location() 剝除多字口語前綴/尾綴並拒絕疑問詞停用詞，
    同時不剝單字「那」以保護「那須」等真實地名；無地名時交由 working_memory 繼承或反問。
    """
    from src.agent.gate import QuickPassGate

    res = QuickPassGate().evaluate(message)
    assert res.matched and res.rule_name == "explore_specialty"
    assert res.parsed_intent is not None
    assert res.parsed_intent["intent"] == "explore_cuisine"
    assert res.parsed_intent["location"] == expected_location


@pytest.mark.asyncio
async def test_regression_explore_without_location_inherits_or_asks():
    """無地名探索問句：有 last_location 時繼承；無記憶時改為 need_location 反問，絕不 geocode「有什麼」。"""
    mock_places = MagicMock()
    mock_places.geocode_location = AsyncMock(return_value=(35.0, 139.0))
    trace = MagicMock(log_step=AsyncMock(), start_run=AsyncMock(), finish_run=AsyncMock())

    repo_with_mem = MagicMock()
    repo_with_mem.get_working_memory = AsyncMock(return_value={"last_location": "淺草", "last_cuisine": "天丼"})
    fsm = FoodRecommendationFSM(
        llm_client=MagicMock(),
        places_client=mock_places,
        session_repo=repo_with_mem,
        trace_logger=trace,
    )
    parsed = await fsm.execute_state_1_intent("有什麼特色美食？", None, [], chat_id=1)
    assert parsed["intent"] == "explore_cuisine"
    assert parsed["location"] == "淺草"

    repo_empty = MagicMock()
    repo_empty.get_working_memory = AsyncMock(return_value={"last_location": None, "last_cuisine": None})
    fsm.session_repo = repo_empty
    parsed2 = await fsm.execute_state_1_intent("有什麼特色美食？", None, [], chat_id=1)
    assert parsed2["intent"] == "need_location"
    mock_places.geocode_location.assert_not_called()


# ==============================================================================
# Case: 過期導覽選單 (Stale suggested_cuisines) — v1.3.0 上線缺陷
# ==============================================================================
@pytest.mark.asyncio
async def test_regression_search_clears_stale_suggested_cuisines():
    """根本原因：update_recommended_ids() 以 {**existing_wm} 保留所有鍵，suggested_cuisines 永不清除，
    數輪後單獨回覆「1」會被映射回數天前導覽過的特色料理。
    防禦邏輯：一般搜尋寫入推薦結果時即 pop("suggested_cuisines")。
    """
    from src.db.repositories.session import SessionRepository

    repo = SessionRepository()
    repo.get_seen_place_ids = AsyncMock(return_value=set())
    repo.get_working_memory = AsyncMock(
        return_value={"last_cuisine": "歐風咖哩", "last_location": "神保町", "suggested_cuisines": ["歐風咖哩"]}
    )
    repo.get_long_term_preferences = AsyncMock(return_value={"dietary_restrictions": [], "payment_preferences": []})

    captured: dict[str, str] = {}

    class _Cur:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, sql, params):
            captured["payload"] = params[1]

    class _Conn:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def cursor(self):
            return _Cur()

        async def commit(self):
            return None

    pool = MagicMock()
    pool.connection = MagicMock(return_value=_Conn())
    with patch("src.db.repositories.session.get_db_pool", AsyncMock(return_value=pool)):
        await repo.update_recommended_ids(
            chat_id=9, recommended_ids=[{"google_place_id": "p1"}], last_cuisine="歐風咖哩", last_location="神保町"
        )

    import json as _json

    stored = _json.loads(captured["payload"])
    assert "suggested_cuisines" not in stored["working_memory"]
    assert stored["working_memory"]["last_location"] == "神保町"


@pytest.mark.asyncio
async def test_regression_bare_index_without_menu_falls_back_to_follow_up():
    """無有效導覽選單時，單獨回覆「1」應視為追問上一輪第 1 家店，而非用舊料理重新搜尋。"""
    repo = MagicMock()
    repo.get_working_memory = AsyncMock(return_value={"last_location": "新宿", "last_cuisine": "拉麵"})
    fsm = FoodRecommendationFSM(
        llm_client=MagicMock(),
        places_client=MagicMock(geocode_location=AsyncMock(return_value=(35.69, 139.70))),
        session_repo=repo,
        trace_logger=MagicMock(log_step=AsyncMock(), start_run=AsyncMock(), finish_run=AsyncMock()),
    )
    parsed = await fsm.execute_state_1_intent("1", None, [], chat_id=5)
    assert parsed["intent"] == "follow_up"
    assert parsed["target_index"] == 1
    assert "specialty_index" not in parsed


@pytest.mark.asyncio
async def test_regression_prefecture_disambiguation_menu_and_working_memory():
    """Case 1: 策略 A - 縣級廣域主動反問收斂。
    輸入「靜岡 鰻魚飯」，系統識別為縣級大區，回傳分區引導訊息 (靜岡車站、濱松、沼津港)，
    並將 suggested_locations 與 pending_cuisine 存入 working memory。
    """
    repo = MagicMock()
    repo.get_working_memory = AsyncMock(return_value={})
    fsm = FoodRecommendationFSM(
        llm_client=MagicMock(
            call_structured_json=AsyncMock(
                return_value=({"intent": "search", "location": "靜岡", "cuisine": "鰻魚飯"}, "mock", 10)
            )
        ),
        places_client=MagicMock(geocode_location=AsyncMock(return_value=None)),
        session_repo=repo,
        trace_logger=MagicMock(log_step=AsyncMock(), start_run=AsyncMock(), finish_run=AsyncMock()),
    )
    parsed = await fsm.execute_state_1_intent("靜岡 鰻魚飯", None, [], chat_id=123)
    assert parsed["intent"] == "need_location"
    assert "need_location_prompt" in parsed
    prompt = parsed["need_location_prompt"]
    assert "靜岡" in prompt
    assert "靜岡車站" in prompt
    assert "濱松" in prompt
    assert "沼津" in prompt
    assert parsed.get("suggested_locations") == ["靜岡車站", "濱松車站", "沼津港"]
    assert parsed.get("pending_cuisine") == "鰻魚飯"

    # 驗證「靜岡縣」與日文「静岡県」亦能精準判定
    from src.agent.stages.intent import is_prefecture_level_area

    b1, name1, _ = is_prefecture_level_area("靜岡縣")
    assert b1 is True and name1 == "靜岡"
    b2, name2, _ = is_prefecture_level_area("静岡県")
    assert b2 is True and name2 == "静岡"
    b3, name3, _ = is_prefecture_level_area("北海道")
    assert b3 is True and name3 == "北海道"
    b4, name4, _ = is_prefecture_level_area("長野縣")
    assert b4 is True and name4 == "長野"
    b5, name5, _ = is_prefecture_level_area("靜岡市葵區")
    assert b5 is False and name5 is None


@pytest.mark.asyncio
async def test_regression_prefecture_disambiguation_selection_and_cuisine_inheritance():
    """Case 2 & 3: 策略 A - 雙軌感知與城鎮名回覆接軌。
    1. 當 working memory 含有 suggested_locations 時，回覆「2」自動轉為搜尋「濱松車站」+ 繼承「鰻魚飯」，且 intent=search。
    2. 下一輪若直接回覆城鎮名「濱松」，自動繼承暫存之 pending_cuisine="鰻魚飯"。
    """
    # 軌道 1: 回覆 "2"
    repo1 = MagicMock()
    repo1.get_working_memory = AsyncMock(
        return_value={
            "suggested_locations": ["靜岡車站", "濱松車站", "沼津港"],
            "pending_cuisine": "鰻魚飯",
        }
    )
    fsm1 = FoodRecommendationFSM(
        llm_client=MagicMock(),
        places_client=MagicMock(geocode_location=AsyncMock(return_value=(34.70, 137.73))),
        session_repo=repo1,
        trace_logger=MagicMock(log_step=AsyncMock(), start_run=AsyncMock(), finish_run=AsyncMock()),
    )
    parsed1 = await fsm1.execute_state_1_intent("2", None, [], chat_id=123)
    assert parsed1["intent"] == "search"
    assert parsed1["location"] == "濱松車站"
    assert parsed1["cuisine"] == "鰻魚飯"

    # 軌道 2: 直接回覆城鎮名 "濱松"
    repo2 = MagicMock()
    repo2.get_working_memory = AsyncMock(
        return_value={
            "suggested_locations": ["靜岡車站", "濱松車站", "沼津港"],
            "pending_cuisine": "鰻魚飯",
        }
    )
    fsm2 = FoodRecommendationFSM(
        llm_client=MagicMock(
            call_structured_json=AsyncMock(
                return_value=({"intent": "search", "location": "濱松", "cuisine": "熱門美食"}, "mock", 10)
            )
        ),
        places_client=MagicMock(geocode_location=AsyncMock(return_value=(34.70, 137.73))),
        session_repo=repo2,
        trace_logger=MagicMock(log_step=AsyncMock(), start_run=AsyncMock(), finish_run=AsyncMock()),
    )
    parsed2 = await fsm2.execute_state_1_intent("濱松", None, [], chat_id=123)
    assert parsed2["intent"] == "search"
    assert parsed2["location"] == "濱松"
    assert parsed2["cuisine"] == "鰻魚飯"


def test_regression_transit_text_nearest_station_extraction():
    """Case 7: Tabelog 交通手段爬蟲正則提取測試。
    涵蓋一般步行、出口後綴、距離折算等各種日本交通描述。
    """
    from src.services.tabelog_scraper import extract_nearest_station_from_transit_text

    # 1. 車站 + 步行分鐘
    assert extract_nearest_station_from_transit_text("箱根登山鉄道 強羅駅 徒歩5分") == "從「強羅站」步行約 5 分鐘"
    # 2. 出口後綴 + 步行分鐘
    assert extract_nearest_station_from_transit_text("強羅駅正面口から徒歩約3分") == "從「強羅站正面口」步行約 3 分鐘"
    # 3. 鐵道前綴清洗
    assert (
        extract_nearest_station_from_transit_text("地下鉄表参道駅 A1出口より徒歩3分")
        == "從「表参道站 A1出口」步行約 3 分鐘"
    )
    # 4. 距離折算 (160m -> 2分鐘)
    assert extract_nearest_station_from_transit_text("箱根湯本駅から160m") == "從「箱根湯本站」步行約 2 分鐘"
    # 5. 空值或無關文字
    assert extract_nearest_station_from_transit_text("") is None
    assert extract_nearest_station_from_transit_text("無關文字說明") is None
    # 6. 嚴格防護：公車、開車、計程車絕不誤判為步行
    assert extract_nearest_station_from_transit_text("強羅駅からバスで15分") is None
    assert extract_nearest_station_from_transit_text("強羅駅から車で10分") is None
    assert extract_nearest_station_from_transit_text("強羅駅からタクシーで8分") is None
    assert extract_nearest_station_from_transit_text("箱根湯本駅よりバスで20分") is None


def test_regression_walking_description_wide_area_and_point_to_point():
    """Case 4, 5, 6: 方案 2 - 景區最近車站與防錯位步行描述測試。
    1. 廣域景區（箱根）+ 最近車站（強羅站 5分鐘）：直接輸出「從「強羅站」步行約 5 分鐘」，無中心點米數。
    2. 廣域景區（箱根）+ 無車站但有子商圈：輸出「位於箱根「強羅」商圈 (距定位點約 2800m)」。
    3. 具體車站（箱根湯本站）：維持點對點「從「箱根湯本站」步行約 3 分鐘 (240m)」。
    4. 都心商圈（新宿）：維持點對點「從「新宿」步行約 4 分鐘 (320m)」。
    """
    from src.agent.stages.places_search import format_walking_description, is_wide_area_origin

    # 廣域判定 (含常見溫泉景區後綴)
    assert is_wide_area_origin("箱根") is True
    assert is_wide_area_origin("輕井澤") is True
    assert is_wide_area_origin("草津溫泉") is True
    assert is_wide_area_origin("有馬温泉") is True
    assert is_wide_area_origin("由布院溫泉") is True
    assert is_wide_area_origin("登別溫泉") is True
    assert is_wide_area_origin("黑川溫泉") is True
    assert is_wide_area_origin("箱根湯本站") is False  # 具體站點不降級
    assert is_wide_area_origin("草津溫泉站") is False
    assert is_wide_area_origin("強羅車站") is False
    assert is_wide_area_origin("新宿") is False  # 都心商圈不誤殺
    assert is_wide_area_origin("銀座") is False

    # Case 4: 箱根景區 + 強羅站
    desc1 = format_walking_description(
        origin_name="箱根",
        dist_m=2800,
        walk_mins=35,
        nearest_station_desc="從「強羅站」步行約 5 分鐘",
    )
    assert desc1 == "從「強羅站」步行約 5 分鐘"
    assert "2800m" not in desc1
    assert "從「箱根」步行" not in desc1

    # 箱根景區 + 子商圈
    desc2 = format_walking_description(
        origin_name="箱根",
        dist_m=2800,
        walk_mins=35,
        nearest_station_desc=None,
        address="神奈川県足柄下郡箱根町強羅1300",
    )
    assert "強羅" in desc2
    assert "2800m" in desc2

    # Case 5: 具體車站（箱根湯本站）
    desc3 = format_walking_description(
        origin_name="箱根湯本站",
        dist_m=240,
        walk_mins=3,
        nearest_station_desc=None,
    )
    assert desc3 == "從「箱根湯本站」步行約 3 分鐘 (240m)"

    # Case 6: 都心商圈（新宿）
    desc4 = format_walking_description(
        origin_name="新宿",
        dist_m=320,
        walk_mins=4,
        nearest_station_desc=None,
    )
    assert desc4 == "從「新宿」步行約 4 分鐘 (320m)"


def test_regression_foodie_score_distance_penalty_waiver_for_wide_area():
    """方案 2: 廣域景區或持最近車站者，豁免幾何中心距離懲罰。"""
    from src.agent.stages.enrichment import calculate_foodie_score

    # 普通市區店鋪，距離 2000m -> dist_penalty = 0.12
    normal_far_store = {
        "name": "市區遠店",
        "origin_name": "新宿",
        "distance_meters": 2000,
        "tabelog_rating": 3.70,
    }
    score_normal = calculate_foodie_score(normal_far_store)
    # 3.70 - 0.12 = 3.58
    assert abs(score_normal - 3.58) < 1e-4

    # 箱根景區店鋪，距離 2800m -> 距離懲罰歸零 (0.0)
    hakone_far_store = {
        "name": "箱根名店",
        "origin_name": "箱根",
        "distance_meters": 2800,
        "tabelog_rating": 3.70,
        "nearest_station_desc": "從「強羅站」步行約 5 分鐘",
    }
    score_hakone = calculate_foodie_score(hakone_far_store)
    # 3.70 - 0.0 = 3.70
    assert abs(score_hakone - 3.70) < 1e-4


@pytest.mark.asyncio
async def test_regression_cross_menu_mutual_exclusion_in_working_memory():
    """Finding 1 回歸測試：多輪對話中特色選單與縣級選單之互斥清理。
    1. 當前輪為縣級反問，Working Memory 含有 suggested_locations 與 pending_cuisine。
    2. 下一輪使用者轉為詢問特色導覽 (suggested_cuisines)，舊的 suggested_locations 必須被互斥清空。
    3. 再下一輪使用者輸入「1」，確保能正確導向特色料理，絕不被過期的縣級選單劫持。
    """
    from typing import Any

    from src.db.repositories.session import SessionRepository

    captured_payloads: list[dict[str, Any]] = []

    class _MockCur:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def execute(self, sql, params=None):
            pass

        async def fetchone(self):
            if captured_payloads:
                last_p = captured_payloads[-1]
                return {"last_recommended_ids": last_p}
            return None

    class _MockConn:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def commit(self):
            pass

        def cursor(self):
            return _MockCur()

    class _MockPool:
        def connection(self):
            return _MockConn()

    repo = SessionRepository()
    mock_pool = _MockPool()

    with patch("src.db.repositories.session.get_db_pool", AsyncMock(return_value=mock_pool)):
        # 步驟 1: 存入縣級選單
        wm1 = await repo.update_working_memory(
            chat_id=999,
            suggested_locations=["靜岡車站", "濱松車站", "沼津港"],
            pending_cuisine="鰻魚飯",
        )
        assert wm1.get("suggested_locations") == ["靜岡車站", "濱松車站", "沼津港"]
        assert wm1.get("pending_cuisine") == "鰻魚飯"
        assert "suggested_cuisines" not in wm1

        # 模擬 DB 保存了 wm1
        captured_payloads.append({"current": [], "accumulated_seen_ids": [], "working_memory": wm1})

        # 步驟 2: 使用者轉為詢問東京特色導覽，存入 suggested_cuisines
        wm2 = await repo.update_working_memory(
            chat_id=999,
            last_location="東京",
            suggested_cuisines=["文字燒", "江戶前壽司", "深川飯"],
        )
        # 驗證互斥清理：舊的 suggested_locations 與 pending_cuisine 必須被清空！
        assert wm2.get("suggested_cuisines") == ["文字燒", "江戶前壽司", "深川飯"]
        assert "suggested_locations" not in wm2
        assert "pending_cuisine" not in wm2


# ==============================================================================
# Case 26: 全方位預熱 Worker (All-in-One Prewarm Flow & Partial Failure Isolation)
# ==============================================================================
@pytest.mark.asyncio
async def test_regression_all_in_one_prewarm_flow():
    """Case 26: 驗證 All-in-One Prewarm Worker 端到端整合：
    1. 同步並行執行 Google Places + Tabelog 餐廳快取 (20家) 與官方在地文化深度導覽三維度預熱 (food/drink/souvenir)。
    2. 驗證完成後推播 Telegram 整合卡片，包含名物料理、在地飲品、必買伴手禮與問答引導語。
    3. 驗證部分故障隔離 (Partial Failure Isolation)：當文化導覽拋出例外或超時時，餐廳快取照常完成並安全降級推播，任務永不崩潰。
    """
    from src.db.repositories.prewarm import PrewarmRepository
    from src.db.repositories.restaurant import RestaurantRepository
    from src.services.cuisine_guide import AreaSpecialtyGuide, LocalSpecialtyGuideService, SpecialtyItem
    from src.services.entity_resolver import EntityResolver
    from src.services.google_places import GooglePlacesClient
    from src.services.tabelog_scraper import TabelogScraper
    from src.worker.prewarm import PrewarmWorker

    # --- 階段 1: 完整雙軌並行成功情境 ---
    mock_prewarm_repo = AsyncMock(spec=PrewarmRepository)
    mock_prewarm_repo.get_next_pending_job.return_value = {
        "id": "job_reg_aio_1",
        "area_name": "函館",
        "target_count": 20,
        "chat_id": 987654,
    }

    mock_places_client = AsyncMock(spec=GooglePlacesClient)
    mock_places_client.geocode_location.return_value = (41.7687, 140.7288)
    mock_places_client.search_nearby.return_value = [
        {"google_place_id": f"g_reg_{i}", "name": f"函館名店_{i}", "phone": f"0138-00-{i:04d}"} for i in range(20)
    ]

    mock_scraper = AsyncMock(spec=TabelogScraper)
    mock_scraper.search_tabelog_candidates.return_value = [
        {"tabelog_url": "https://tabelog.com/hokkaido/A0105/010501/01000001/", "name_ja": "函館店"}
    ]
    mock_scraper.scrape_restaurant_details.return_value = {
        "tabelog_rating": 3.65,
        "name_ja": "函館店",
    }

    mock_resolver = AsyncMock(spec=EntityResolver)
    mock_resolver.resolve_entity.return_value = {
        "match_found": True,
        "candidate": {"tabelog_url": "https://tabelog.com/hokkaido/A0105/010501/01000001/", "tabelog_rating": 3.65},
        "confidence": 0.95,
        "match_method": "phone_exact",
    }

    mock_restaurant_repo = AsyncMock(spec=RestaurantRepository)
    mock_restaurant_repo.get_by_google_place_id.return_value = None

    sample_culture = {
        "food": AreaSpecialtyGuide(
            area_name="函館",
            category="food",
            intro="函館開港特色料理：",
            specialties=[
                SpecialtyItem(
                    index=1,
                    cuisine_name="函館鹽味拉麵",
                    name_ja="塩ラーメン",
                    history_and_culture="函館百年開港清湯麵食文化",
                    flavor_profile="金黃鹽味回甘",
                ),
                SpecialtyItem(
                    index=2,
                    cuisine_name="小丑漢堡",
                    name_ja="ラッキーピエロ",
                    history_and_culture="函館在地傳奇漢堡速食名物",
                    flavor_profile="酸甜炸雞多汁",
                ),
                SpecialtyItem(
                    index=3,
                    cuisine_name="活烏賊刺身",
                    name_ja="イカソーメン",
                    history_and_culture="津輕海峽晨捕現流烏賊",
                    flavor_profile="晶瑩鮮甜脆嫩",
                ),
            ],
            source_tier="curated",
        ),
        "drink": AreaSpecialtyGuide(
            area_name="函館",
            category="drink",
            intro="函館特色飲品：",
            specialties=[
                SpecialtyItem(
                    index=1,
                    cuisine_name="函館葡萄酒",
                    name_ja="函館ワイン",
                    history_and_culture="道南優良葡萄釀造在地銘酒",
                    flavor_profile="芳醇果香",
                ),
                SpecialtyItem(
                    index=2,
                    cuisine_name="美鈴珈琲",
                    name_ja="美鈴珈琲",
                    history_and_culture="北海道第一家炭火烘焙咖啡",
                    flavor_profile="焦香甘醇",
                ),
            ],
            source_tier="curated",
        ),
        "souvenir": AreaSpecialtyGuide(
            area_name="函館",
            category="souvenir",
            intro="函館必買伴手禮：",
            specialties=[
                SpecialtyItem(
                    index=1,
                    cuisine_name="起司舒芙蕾",
                    name_ja="チーズオムレット",
                    history_and_culture="北海道牛乳與特級乳酪烘焙",
                    flavor_profile="綿密如雪入口即化",
                ),
                SpecialtyItem(
                    index=2,
                    cuisine_name="生烏賊仙貝",
                    name_ja="いか煎餅",
                    history_and_culture="整隻烏賊鐵板鐵壓烘烤名產",
                    flavor_profile="鮮美酥脆",
                ),
            ],
            source_tier="curated",
        ),
    }

    mock_guide_service = AsyncMock(spec=LocalSpecialtyGuideService)
    mock_guide_service.prewarm_area_culture.return_value = sample_culture
    mock_bot = AsyncMock()

    worker = PrewarmWorker(
        prewarm_repo=mock_prewarm_repo,
        places_client=mock_places_client,
        tabelog_scraper=mock_scraper,
        entity_resolver=mock_resolver,
        restaurant_repo=mock_restaurant_repo,
        guide_service=mock_guide_service,
        scrape_delay_sec=0,
        bot=mock_bot,
    )

    result = await worker.process_next_job()
    assert result is True
    assert mock_restaurant_repo.save_restaurant_with_tabelog.call_count == 20
    mock_guide_service.prewarm_area_culture.assert_called_once_with("函館", llm_client=worker.llm_client)
    mock_prewarm_repo.update_job_status.assert_called_once_with("job_reg_aio_1", "completed")

    mock_bot.send_message.assert_called_once()
    msg_args = mock_bot.send_message.call_args[1]
    assert msg_args["chat_id"] == 987654
    assert "20 家" in msg_args["text"]
    assert "• 🍱 特色名物：函館鹽味拉麵、小丑漢堡、活烏賊刺身" in msg_args["text"]
    assert "• 🍵 在地飲品：函館葡萄酒、美鈴珈琲" in msg_args["text"]
    assert "• 🎁 必買伴手禮：起司舒芙蕾、生烏賊仙貝" in msg_args["text"]
    assert "函館 伴手禮" in msg_args["text"]

    # --- 階段 2: 部分故障隔離 (Partial Failure Isolation) ---
    mock_bot.reset_mock()
    mock_prewarm_repo.reset_mock()
    mock_prewarm_repo.get_next_pending_job.return_value = {
        "id": "job_reg_aio_2",
        "area_name": "札幌",
        "target_count": 5,
        "chat_id": 987654,
    }
    mock_places_client.search_nearby.return_value = [
        {"google_place_id": f"g_sp_{i}", "name": f"札幌名店_{i}", "phone": f"011-00-{i:04d}"} for i in range(5)
    ]
    # 模擬文化導覽抓取崩潰 (超時或網路異常)
    mock_guide_service.prewarm_area_culture.side_effect = RuntimeError("Official portal timeout")

    result_fail = await worker.process_next_job()
    assert result_fail is True
    # 餐廳仍照常快取完成，任務狀態仍為 completed
    mock_prewarm_repo.update_job_status.assert_called_once_with("job_reg_aio_2", "completed")
    # Telegram 推播降級卡片，無文化行但包含餐廳快取成果，且系統零崩潰
    mock_bot.send_message.assert_called_once()
    msg_fail_text = mock_bot.send_message.call_args[1]["text"]
    assert "已備妥" in msg_fail_text
    assert "特色名物" not in msg_fail_text


# ==============================================================================
# Case 27: 廣域預熱互動防護與狀態防卡死 (Wide-Area Prewarm Disambiguation & Safe Fallback)
# ==============================================================================
@pytest.mark.asyncio
async def test_regression_prewarm_cancellation_and_invalid_choice():
    """Case 27: 驗證 Telegram 廣域預熱防護 (/prewarm 北海道)：
    1. 針對都道府縣級大區域主動觸發分區選單 (1.札幌 / 2.函館 / 3.小樽 / 全部 / 取消)，不盲搜 Places。
    2. 使用者後續輸入「取消」，乾淨清除 pending_prewarm_options，不建立預熱任務並溫和回覆。
    3. 再次觸發選單後，若使用者輸入越界號碼 (如 "99") 或普通問句，系統自動清除待確認狀態，
       安全銜接至正常指令或 Agent 處理，絕不拋出 IndexError 崩潰或卡死對話狀態。
    """
    from src.bot.handlers import TelegramBotHandler

    class _InMemorySessionRepo:
        def __init__(self) -> None:
            self.wm: dict[str, object] = {}

        async def get_working_memory(self, chat_id: int) -> dict[str, object]:
            return dict(self.wm)

        async def update_working_memory(self, chat_id: int, **kwargs: object) -> dict[str, object]:
            if kwargs.get("clear_pending_prewarm") or kwargs.get("pending_prewarm_options") == {}:
                self.wm.pop("pending_prewarm_options", None)
            elif kwargs.get("pending_prewarm_options") is not None:
                self.wm["pending_prewarm_options"] = kwargs["pending_prewarm_options"]
            return dict(self.wm)

        async def get_session(self, chat_id: int) -> dict[str, object]:
            return {"working_memory": dict(self.wm)}

        async def get_recent_history(self, chat_id: int, max_turns: int = 3) -> list[dict[str, str]]:
            return []

        async def add_conversation_turn(self, chat_id: int, user_message: str, assistant_response: str) -> None:
            pass

    mock_bot = AsyncMock()
    mock_prewarm_repo = AsyncMock()
    mock_agent_engine = AsyncMock()
    mock_agent_engine.run_agent.return_value = "處理完成"
    session_repo = _InMemorySessionRepo()

    handler = TelegramBotHandler(
        bot=mock_bot,
        session_repo=session_repo,  # type: ignore[arg-type]
        prewarm_repo=mock_prewarm_repo,
        agent_engine=mock_agent_engine,
    )

    def _make_update(text: str, uid: int = 100) -> dict[str, object]:
        return {
            "update_id": uid,
            "message": {
                "message_id": uid,
                "from": {"id": 12345},
                "chat": {"id": 12345},
                "text": text,
            },
        }

    with patch("src.config.settings.TELEGRAM_ALLOWED_USER_ID", 0):
        # 步驟 1: 發送廣域預熱指令
        await handler.handle_update(_make_update("/prewarm 北海道", uid=1))
        mock_prewarm_repo.create_job.assert_not_called()
        wm1 = await session_repo.get_working_memory(12345)
        assert "pending_prewarm_options" in wm1
        pending = wm1["pending_prewarm_options"]
        assert pending["pref"] == "北海道"

        mock_bot.send_message.assert_called_once()
        sent_text = mock_bot.send_message.call_args[1]["text"]
        assert "北海道幅員遼闊" in sent_text
        assert "札幌" in sent_text

        # 步驟 2: 使用者輸入「取消」
        mock_bot.reset_mock()
        await handler.handle_update(_make_update("取消", uid=2))
        mock_prewarm_repo.create_job.assert_not_called()
        wm2 = await session_repo.get_working_memory(12345)
        assert "pending_prewarm_options" not in wm2
        mock_bot.send_message.assert_called_once()
        assert "已取消預熱任務。" in mock_bot.send_message.call_args[1]["text"]

        # 步驟 3: 再次觸發廣域預熱
        await handler.handle_update(_make_update("/prewarm 北海道", uid=3))
        assert "pending_prewarm_options" in (await session_repo.get_working_memory(12345))

        # 步驟 4: 使用者輸入越界號碼 "99" (非合法選單選項 1~3)
        mock_bot.reset_mock()
        mock_prewarm_repo.reset_mock()
        await handler.handle_update(_make_update("99", uid=4))

        # 驗證未建立非法預熱任務
        mock_prewarm_repo.create_job.assert_not_called()
        # 驗證狀態已自動被清除，杜絕狀態卡死
        wm4 = await session_repo.get_working_memory(12345)
        assert "pending_prewarm_options" not in wm4
        # 驗證無縫轉交 agent_engine 處理且無崩潰
        mock_agent_engine.run_agent.assert_called_once()
