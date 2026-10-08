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
