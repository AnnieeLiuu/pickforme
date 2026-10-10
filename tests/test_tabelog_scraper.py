import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from src.services.tabelog_scraper import TabelogScraper


def test_parse_search_results():
    scraper = TabelogScraper()
    fixture_path = Path(__file__).parent / "fixtures" / "tabelog_pages.json"
    data = json.loads(fixture_path.read_text(encoding="utf-8"))

    results = scraper._parse_search_results(data["search_html"])
    assert len(results) == 1
    assert results[0]["name_ja"] == "創始面屋武蔵"
    assert results[0]["tabelog_rating"] == 3.75
    assert results[0]["phone"] == "03-3363-4690"


def test_parse_store_details():
    scraper = TabelogScraper()
    fixture_path = Path(__file__).parent / "fixtures" / "tabelog_pages.json"
    data = json.loads(fixture_path.read_text(encoding="utf-8"))

    url = "https://tabelog.com/tokyo/A1304/A130401/13001013/"
    details = scraper._parse_store_details(data["details_html"], url)
    assert details["name_ja"] == "創始面屋武蔵"
    assert details["tabelog_rating"] == 3.75
    assert details["review_count"] == 850
    assert details["phone"] == "03-3363-4690"


def test_clean_tabelog_url():
    scraper = TabelogScraper()
    raw_tw = "https://tabelog.com/tw/tokyo/A1304/A130402/13050544/"
    assert scraper._clean_tabelog_url(raw_tw) == "https://tabelog.com/tokyo/A1304/A130402/13050544/"

    raw_sub = "https://tabelog.com/tokyo/A1319/A131902/13160998/dtlrvwlst/B356102671/?type=0"
    assert scraper._clean_tabelog_url(raw_sub) == "https://tabelog.com/tokyo/A1319/A131902/13160998/"


@pytest.mark.asyncio
async def test_scrape_restaurant_details_fallback_when_blocked():
    scraper = TabelogScraper()
    candidate = {
        "name_ja": "餃子の福包 新宿店",
        "tabelog_rating": 3.49,
        "review_count": 1500,
        "hyakumeiten": ["餃子 百名店 2021"],
    }
    with patch.object(scraper, "_fetch_url_with_retry", new=AsyncMock(return_value=None)):
        res = await scraper.scrape_restaurant_details(
            "https://tabelog.com/tokyo/A1304/A130402/13050544/",
            fallback_candidate=candidate,
        )
        assert res["tabelog_rating"] == 3.49
        assert res["review_count"] == 1500
        assert "餃子 百名店 2021" in res["hyakumeiten"]


@pytest.mark.asyncio
async def test_search_candidates_grounding_fallback_when_blocked():
    scraper = TabelogScraper()
    mock_candidates = [
        {
            "tabelog_url": "https://tabelog.com/tokyo/A1304/A130402/13050544/",
            "name_ja": "餃子の福包 新宿店",
            "tabelog_rating": 3.49,
            "review_count": 1500,
            "hyakumeiten": [],
            "awards": [],
            "phone": "",
            "address": "",
        }
    ]
    with patch.object(scraper, "_fetch_url_with_retry", new=AsyncMock(return_value=None)):
        with patch.object(scraper, "search_via_grounding", new=AsyncMock(return_value=mock_candidates)):
            results = await scraper.search_tabelog_candidates("餃子の福包 新宿店", "新宿")
            assert len(results) == 1
            assert results[0]["tabelog_rating"] == 3.49
            assert results[0]["name_ja"] == "餃子の福包 新宿店"
