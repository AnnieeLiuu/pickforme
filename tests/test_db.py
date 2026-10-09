from uuid import uuid4

from src.db.models import Restaurant, RestaurantCreate, TabelogCache


def test_restaurant_pydantic_model():
    req = RestaurantCreate(
        google_place_id="ChIJN1t_tDeuEmsRUsoyG83frY4",
        name_ja="麵屋武藏",
        name_en="Menya Musashi",
        phone="03-3363-4690",
        lat=35.6938,
        lng=139.7034,
        address="東京都新宿區西新宿7-2-6",
    )

    assert req.google_place_id == "ChIJN1t_tDeuEmsRUsoyG83frY4"
    assert req.name_ja == "麵屋武藏"

    restaurant = Restaurant(**req.model_dump())
    assert restaurant.id is not None
    assert restaurant.lat == 35.6938


def test_tabelog_cache_model():
    res_id = uuid4()
    cache = TabelogCache(
        restaurant_id=res_id,
        tabelog_rating=3.78,
        review_count=450,
        price_lunch="￥1,000～￥1,999",
        price_dinner="￥1,000～￥1,999",
        match_confidence=0.95,
        match_method="phone_exact",
    )

    assert cache.restaurant_id == res_id
    assert cache.tabelog_rating == 3.78
    assert cache.match_method == "phone_exact"
