import asyncio

from src.services.entity_resolver import (
    EntityResolver,
    calculate_distance_meters,
    calculate_string_similarity,
    normalize_japanese_phone,
    normalize_store_name,
)


def test_normalize_japanese_phone():
    assert normalize_japanese_phone("+81 3-3363-4690") == "0333634690"
    assert normalize_japanese_phone("03-3363-4690") == "0333634690"
    assert normalize_japanese_phone("+81 92 263 7585") == "0922637585"
    assert normalize_japanese_phone(None) == ""


def test_normalize_store_name():
    # 繁體 "藏" 會轉換成日文新字體 "蔵"
    assert normalize_store_name("創始 面屋武藏") == "創始面屋武蔵"
    assert normalize_store_name("一蘭（博多店）") == "一蘭博多店"
    assert normalize_store_name(" Menya  Musashi ") == "menyamusashi"


def test_calculate_distance_meters():
    # 新宿車站附近兩點距離
    dist = calculate_distance_meters(35.6938, 139.7034, 35.6940, 139.7036)
    assert 0 < dist < 50


def test_calculate_string_similarity():
    sim1 = calculate_string_similarity("麵屋武藏", "面屋武蔵")
    assert sim1 > 0.60

    sim2 = calculate_string_similarity("一蘭 博多店", "一蘭 博多車站前店")
    assert sim2 > 0.70


def test_entity_resolver_phone_exact_match():
    async def _test():
        resolver = EntityResolver(gemini_api_key=None)
        google_store = {
            "name": "創始面屋武藏",
            "phone": "+81 3-3363-4690",
            "lat": 35.6938,
            "lng": 139.7034,
        }
        tabelog_candidates = [
            {"name_ja": "面屋武蔵", "phone": "03-3363-4690", "lat": 35.6939, "lng": 139.7035},
            {"name_ja": "別間拉麵店", "phone": "03-9999-9999", "lat": 35.7000, "lng": 139.7100},
        ]

        result = await resolver.resolve_entity(google_store, tabelog_candidates)
        assert result["match_found"] is True
        assert result["match_method"] == "phone_exact"
        assert result["confidence"] == 1.0

    asyncio.run(_test())


def test_entity_resolver_no_match():
    async def _test():
        resolver = EntityResolver(gemini_api_key=None)
        google_store = {
            "name": "完全不相干的店名",
            "phone": "03-1111-2222",
            "lat": 35.6938,
            "lng": 139.7034,
        }
        tabelog_candidates = [
            {"name_ja": "壽司大", "phone": "03-9999-9999", "lat": 35.7000, "lng": 139.7100},
        ]

        result = await resolver.resolve_entity(google_store, tabelog_candidates)
        assert result["match_found"] is False
        assert result["match_method"] == "no_match"

    asyncio.run(_test())


def test_entity_resolver_llm_verify():
    from unittest.mock import AsyncMock

    from src.services.entity_resolver import EntityVerifyResponse

    async def _test():
        resolver = EntityResolver(gemini_api_key=None)
        # 相似度約 0.54 (落在 0.40 ~ 0.85 灰色區間)
        google_store = {
            "name": "一蘭 歌舞伎町店",
            "phone": "",
            "lat": 35.6948,
            "lng": 139.7025,
        }
        tabelog_candidates = [
            {"name_ja": "天然とんこつラーメン一蘭 新宿歌舞伎町店", "phone": "", "lat": 35.6949, "lng": 139.7026},
        ]

        # 模擬 Level 2 LLM 裁決返回 True
        resolver._verify_with_llm = AsyncMock(
            return_value=EntityVerifyResponse(
                is_same_store=True,
                confidence=0.95,
                reason="LLM 語意辨識兩者皆為新宿歌舞伎町之一蘭拉麵實體分店",
            )
        )

        result = await resolver.resolve_entity(google_store, tabelog_candidates, allow_llm=True)
        assert result["match_found"] is True
        assert result["match_method"] == "llm_verify"
        assert result["confidence"] == 0.95

    asyncio.run(_test())


def test_resolve_honey_toast_top1_match():
    async def _test():
        resolver = EntityResolver(gemini_api_key=None)
        google_store = {
            "name": "ハニトーカフェ横浜関内店",
            "phone": "0120-911-753",
            "lat": 35.4437,
            "lng": 139.6328,
        }
        tabelog_candidates = [
            {
                "tabelog_url": "https://tabelog.com/kanagawa/A1401/A140104/14099981/",
                "name_ja": "ハニトーカフェ 横浜関内店",
                "phone": "",
                "lat": 35.4437,
                "lng": 139.6328,
                "tabelog_rating": 3.1,
            }
        ]
        result = await resolver.resolve_entity(google_store, tabelog_candidates, allow_llm=False)
        assert result["match_found"] is True
        assert result["match_method"] in ("rule_high", "top1_core_match")
        assert result["candidate"]["tabelog_url"] == "https://tabelog.com/kanagawa/A1401/A140104/14099981/"

    asyncio.run(_test())


def test_resolve_jiichiro_pudding_match():
    async def _test():
        resolver = EntityResolver(gemini_api_key=None)
        google_store = {
            "name": "治一郎 ジョイナス店",
            "phone": "045-311-2005",
            "lat": 35.4658,
            "lng": 139.6223,
        }
        tabelog_candidates = [
            {
                "tabelog_url": "https://tabelog.com/kanagawa/A1401/A140101/14061803/",
                "name_ja": "治一郎",
                "phone": "",
                "lat": 35.4658,
                "lng": 139.6223,
                "tabelog_rating": 3.25,
            }
        ]
        result = await resolver.resolve_entity(google_store, tabelog_candidates, allow_llm=False)
        assert result["match_found"] is True
        assert result["match_method"] in ("rule_high", "top1_core_match")
        assert result["candidate"]["tabelog_rating"] == 3.25

    asyncio.run(_test())


def test_resolve_building_address_exact_match():
    async def _test():
        resolver = EntityResolver(gemini_api_key=None)
        google_store = {
            "name": "Mawashizushi KATSU",
            "address": "神奈川県横浜市西区高島２丁目１９−１２ スカイビル 11階",
            "lat": 35.465,
            "lng": 139.622,
        }
        tabelog_candidates = [
            {
                "name_ja": "回し寿司活 活美登利横浜スカイビル店",
                "address": "神奈川県横浜市西区高島2-19-12 スカイビル 11F",
                "lat": 35.4651,
                "lng": 139.6221,
                "tabelog_url": "https://tabelog.com/kanagawa/A1401/A140101/14041492/",
                "tabelog_rating": 3.52,
            }
        ]
        result = await resolver.resolve_entity(google_store, tabelog_candidates, allow_llm=False)
        assert result["match_found"] is True
        assert result["match_method"] == "building_address_exact"
        assert result["confidence"] >= 0.90
        assert result["candidate"]["name_ja"] == "回し寿司活 活美登利横浜スカイビル店"

    asyncio.run(_test())
