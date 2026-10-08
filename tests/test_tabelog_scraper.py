import json
from pathlib import Path

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
