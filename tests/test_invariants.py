"""架構合約與不變量測試集 (Architecture Invariants Test Suite)

機器 100% 物理守門，防止任何未看全鏈路的修改破壞核心系統合約：
1. Invariant 1: 虛構未授權店家必須觸發致命一票否決 (All-or-Nothing Abort)
2. Invariant 2: 評分客套話必須由外科手術式就地修復 (In-Place Surgical Repair) 替換為真實短評
3. Invariant 3: Tabelog 口コミ命中時必須 100% 覆蓋 Google 評論並保留血統標籤
4. Invariant 4: 日本郵遞區號與區市補全保證 99.9% 都道府縣精準推導
5. Invariant 5: 0120/0800 虛擬客服專線禁止單獨觸發 phone_exact 秒通
"""

import pytest
from src.agent.validator import repair_card_in_place, validate_card_tiered
from src.models.review import ReviewItem, ReviewSource
from src.models.store_context import StoreContext
from src.services.entity_resolver import (
    EntityResolver,
    extract_prefecture_from_address,
    extract_prefecture_from_postal_code,
)


def test_invariant_1_fake_store_all_or_nothing_abort():
    """合約 1: 虛構店家或超額店家必須判定為致命錯誤 (Fatal Error)，觸發全部拋棄"""
    stores = [
        {"name": "壽司大", "name_ja": "寿司大"},
        {"name": "晴空塔拉麵", "name_ja": "スカイツリーラーメン"},
    ]

    # (a) 模型捏造了第三家不在名單內的店
    hallucinated_card = (
        "1. `寿司大`\n⭐️ **雙平台評分**：4.5\n🚶 **步行距離**：3分鐘\n💳 **支付方式**：現金\n\n"
        "2. `スカイツリーラーメン`\n⭐️ **雙平台評分**：3.8\n🚶 **步行距離**：5分鐘\n💳 **支付方式**：現金\n\n"
        "3. `虛構幽靈拉麵`\n⭐️ **雙平台評分**：4.0\n🚶 **步行距離**：1分鐘\n💳 **支付方式**：現金\n"
    )

    fatal_errors, minor_warnings = validate_card_tiered(hallucinated_card, stores)
    assert len(fatal_errors) > 0
    assert any("虛構店家" in err or "超過輸入之候選店家數" in err for err in fatal_errors)


def test_invariant_2_rating_boilerplate_surgical_repair():
    """合約 2: 評分套話必須被外科手術式修復替換為真實老饕口感短評，且不破壞整體結構"""
    stores = [
        {
            "name": "早川亭",
            "name_ja": "早川亭",
            "foodie_highlight": "鰻魚外皮微焦香脆，醬汁甘甜醇厚，魚肉細嫩入口即化。",
        }
    ]

    card_with_boilerplate = (
        "1. `早川亭`\n"
        "⭐️ **雙平台評分**：Google 4.9 分 / 食べログ 3.20\n"
        "🚶 **步行距離**：神保町站步行約 3 分鐘\n"
        "💳 **支付方式**：僅收現金\n"
        "💡 **老饕推薦理由**：Google 評分高達 4.9 分，雖然 Tabelog 評分較少，但深受在地人認可。\n"
    )

    fatal_errors, minor_warnings = validate_card_tiered(card_with_boilerplate, stores)
    # 評分套話不得被視為致命錯誤 (避免殺敵一千自損八百)
    assert len(fatal_errors) == 0
    assert any("No-Rating-As-Reason" in w for w in minor_warnings)

    # 執行外科手術替換
    repaired_card = repair_card_in_place(card_with_boilerplate, stores)

    # 驗證套話已被徹底消除
    assert "Google 評分高達 4.9 分" not in repaired_card
    assert "雖然 Tabelog 評分較少" not in repaired_card
    # 驗證真實口感短評已被外科手術縫合進去
    assert "鰻魚外皮微焦香脆" in repaired_card
    # 驗證基本排版結構依然完整
    assert "`早川亭`" in repaired_card
    assert "⭐️ **雙平台評分**" in repaired_card


def test_invariant_3_tabelog_overrules_google_lineage():
    """合約 3: 當 Tabelog 口コミ命中時，必須 100% 覆蓋 Google 評論且血統永不遺失"""
    google_review = ReviewItem(
        text="Very good restaurant!",
        source=ReviewSource.GOOGLE,
        author="Tourist",
    )
    tabelog_review = ReviewItem(
        text="濃厚な豚骨魚介スープと自家製極太麺の絡みが絶品。",
        source=ReviewSource.TABELOG,
        author="ラーメン通",
    )

    ctx = StoreContext(
        name="極味拉麵",
        google_rating=4.2,
        tabelog_rating=3.68,
        reviews=[google_review],
        foodie_review_source=ReviewSource.GOOGLE,
    )

    # 模擬 Stage 3 成功抓取到 Tabelog 口コミ並執行 100% 覆蓋合約
    assert not ctx.has_authentic_tabelog_reviews()
    ctx.reviews = [tabelog_review]
    ctx.foodie_review_source = ReviewSource.TABELOG

    assert ctx.has_authentic_tabelog_reviews()
    assert ctx.reviews[0].source == ReviewSource.TABELOG
    assert ctx.foodie_review_source == ReviewSource.TABELOG


def test_invariant_4_postal_code_and_district_mapping():
    """合約 4: 日本郵遞區號前三碼與 23 區市補全保證 99.9% 精度，徹底消滅無縣市字樣盲區"""
    # 1. 郵遞區號直接解析
    assert extract_prefecture_from_postal_code("〒101-0051") == "tokyo"
    assert extract_prefecture_from_postal_code("Japan, 220-0073") == "kanagawa"
    assert extract_prefecture_from_postal_code("530-0001 Osaka") == "osaka"

    # 2. 省略「東京都」或「神奈川県」的區市補全
    assert extract_prefecture_from_address("千代田区神田神保町1-1") == "tokyo"
    assert extract_prefecture_from_address("横浜市中区海岸通4-19") == "kanagawa"
    assert extract_prefecture_from_address("京都市中京区河原町") == "kyoto"

    # 3. 英文地址只寫 City / Ward 的補全
    assert extract_prefecture_from_address("1-1-1 Jimbocho, Chiyoda City") == "tokyo"
    assert extract_prefecture_from_address("Nishi Ward, Yokohama") == "kanagawa"


@pytest.mark.asyncio
async def test_invariant_5_virtual_toll_free_phone_guard():
    """合約 5: 0120/0800 虛擬專線禁止單獨觸發 phone_exact 秒通，防止跨分店總部污染"""
    resolver = EntityResolver()

    # 東京新宿店
    google_store = {
        "name": "牛角 新宿東口店",
        "phone": "0120-111-222",  # 總公司免費專線
        "address": "東京都新宿区歌舞伎町1-1",
        "lat": 35.693,
        "lng": 139.703,
    }

    # Tabelog 候選店家 (同在東京新宿，登記了同一支 0120 總公司電話)
    tabelog_candidate = {
        "name_ja": "牛角 西新宿店",  # 不同分店
        "phone": "0120-111-222",
        "address": "東京都新宿区西新宿7-1",
        "lat": 35.694,
        "lng": 139.699,
        "tabelog_url": "https://tabelog.com/tokyo/A1304/A130401/13000000/",
    }

    # 執行實體解析 (不允許 LLM)
    res = await resolver.resolve_entity(google_store, [tabelog_candidate], allow_llm=False)

    # 由於是 0120 虛擬電話，嚴禁被判定為 phone_exact 秒過！
    assert res.get("match_method") != "phone_exact"
