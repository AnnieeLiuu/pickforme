import json
from pathlib import Path

from src.services.google_places import GooglePlacesClient


def test_format_place_item():
    client = GooglePlacesClient(api_key="mock_key")
    fixture_path = Path(__file__).parent / "fixtures" / "google_places.json"
    raw_data = json.loads(fixture_path.read_text(encoding="utf-8"))

    item_1 = client._format_place_item(raw_data[0])
    assert item_1["google_place_id"] == "ChIJN1t_tDeuEmsRUsoyG83frY4"
    assert item_1["name"] == "創始面屋武蔵"
    assert item_1["phone"] == "03-3363-4690"
    assert item_1["lat"] == 35.6938
    assert item_1["google_rating"] == 4.2
    assert item_1["open_now"] is True

    item_2 = client._format_place_item(raw_data[1])
    assert item_2["phone"] == "+81 92-263-7585"
    assert item_2["user_ratings_total"] == 3400
