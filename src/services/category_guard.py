# -*- coding: utf-8 -*-
"""Generic Category Consistency Guardrail for Stage 2 & Stage 3."""

from __future__ import annotations

from typing import Any, Optional

# 明確非餐飲生活服務/住宿/零售/交通設施類型（即使 target_place_types 為空亦一律排除）
_NON_FOOD_PRIMARY_TYPES = frozenset(
    {
        "service",
        "travel_agency",
        "lodging",
        "hotel",
        "real_estate_agency",
        "hair_care",
        "beauty_salon",
        "spa",
        "gym",
        "hospital",
        "doctor",
        "dentist",
        "pharmacy",
        "school",
        "university",
        "laundry",
        "car_repair",
        "gas_station",
        "parking",
        "transit_station",
        "train_station",
        "subway_station",
        "bus_station",
        "corporate_office",
        "local_government_office",
        "clothing_store",
        "electronics_store",
        "book_store",
        "shoe_store",
        "jewelry_store",
        "furniture_store",
        "home_goods_store",
        "pet_store",
        "photo_shop",
    }
)

# 明確具餐飲/食品供應能力之 Google Places 基底類型（嚴禁納入泛用 "store" 或 "_shop" 以免誤放行零售店）
_FOOD_CAPABLE_TYPES = frozenset(
    {
        "restaurant",
        "food",
        "cafe",
        "coffee_shop",
        "tea_house",
        "bakery",
        "dessert_shop",
        "ice_cream_shop",
        "confectionery",
        "bar",
        "pub",
        "hookah_bar",
        "night_club",
        "izakaya",
        "japanese_izakaya_restaurant",
        "meal_takeaway",
        "meal_delivery",
        "ramen_restaurant",
        "sushi_restaurant",
        "yakiniku_restaurant",
        "steak_house",
        "seafood_restaurant",
        "japanese_restaurant",
        "chinese_restaurant",
        "korean_restaurant",
        "italian_restaurant",
        "french_restaurant",
        "fast_food_restaurant",
        "hamburger_restaurant",
        "pizza_restaurant",
        "food_court",
        "food_store",
        "grocery_store",
        "asian_grocery_store",
        "supermarket",
        "grocery_or_supermarket",
        "convenience_store",
        "liquor_store",
        "market",
        "bistro",
        "diner",
        "deli",
    }
)

_FOOD_TYPE_SUFFIXES = (
    "_restaurant",
    "_cafe",
    "_bar",
    "_bakery",
    "_food",
    "_izakaya",
    "_coffee_shop",
    "_tea_house",
    "_dessert_shop",
    "_ice_cream_shop",
    "_cake_shop",
    "_noodle_shop",
    "_sandwich_shop",
    "_juice_shop",
    "_donut_shop",
    "_bagel_shop",
    "_chocolate_shop",
    "_candy_store",
    "_food_store",
    "_grocery_store",
    "_bistro",
    "_diner",
    "_deli",
    "_pub",
    "_steak_house",
)

# 明確獨立之專賣或夜生活業態：若店家 primaryType 屬於此集合卻不在目標類型中，即使次要 types 混入其他標籤仍判定不符
_DISTINCT_PRIMARY_TYPES = frozenset(
    {
        "cafe",
        "coffee_shop",
        "tea_house",
        "bakery",
        "dessert_shop",
        "ice_cream_shop",
        "confectionery",
        "bar",
        "pub",
        "hookah_bar",
        "night_club",
        "izakaya",
        "japanese_izakaya_restaurant",
        "ramen_restaurant",
        "sushi_restaurant",
        "steak_house",
        "yakiniku_restaurant",
        "hamburger_restaurant",
        "pizza_restaurant",
        "fast_food_restaurant",
    }
)

# 非正餐專賣類型：當目標全屬此類而店家 primaryType 為一般正餐 (restaurant / japanese_restaurant) 且 types 無交集時判定不符
_NON_DINING_TARGET_TYPES = frozenset(
    {
        "cafe",
        "coffee_shop",
        "tea_house",
        "bakery",
        "dessert_shop",
        "ice_cream_shop",
        "confectionery",
        "bar",
        "pub",
        "hookah_bar",
        "night_club",
    }
)

# 粗粒度互斥大類（僅用於 Stage 3 Tabelog genre 兜底比對，不寫死細部同義字字典）
_MACRO_BUCKETS: dict[str, tuple[frozenset[str], tuple[str, ...]]] = {
    "cafe_dessert": (
        frozenset({"cafe", "coffee_shop", "tea_house", "bakery", "dessert_shop", "ice_cream_shop", "confectionery"}),
        ("カフェ", "喫茶", "コーヒー", "珈琲", "紅茶", "日本茶", "スイーツ", "ケーキ", "パン", "甘味"),
    ),
    "ramen_noodle": (
        frozenset({"ramen_restaurant"}),
        ("ラーメン", "つけ麺", "まぜそば", "油そば", "うどん", "そば"),
    ),
    "sushi_seafood": (
        frozenset({"sushi_restaurant"}),
        ("寿司", "鮨", "海鮮"),
    ),
    "yakiniku_steak": (
        frozenset({"yakiniku_restaurant", "steak_house"}),
        ("焼肉", "ホルモン", "ステーキ"),
    ),
    "bar_izakaya": (
        frozenset({"bar", "pub", "hookah_bar", "night_club", "izakaya", "japanese_izakaya_restaurant"}),
        ("居酒屋", "バー", "シーシャ", "焼鳥", "ダイニングバー"),
    ),
    "dining_shokudo": (
        frozenset({"japanese_restaurant", "restaurant", "fast_food_restaurant"}),
        ("食堂", "定食"),
    ),
}


def _resolve_target_buckets(targets: set[str]) -> set[str]:
    return {name for name, (g_types, _) in _MACRO_BUCKETS.items() if targets & g_types}


def _is_food_capable(primary: str, types_set: set[str]) -> bool:
    """檢查店家之 Google Places 類型是否具備餐飲/食品供應屬性。"""
    all_types = types_set | ({primary} if primary else set())
    if all_types & _FOOD_CAPABLE_TYPES:
        return True
    return any(t.endswith(_FOOD_TYPE_SUFFIXES) for t in all_types)


def check_category_mismatch(
    store: dict[str, Any],
    target_place_types: Optional[list[str]] = None,
) -> tuple[bool, Optional[str]]:
    """通用檢查單一店家與目標 Google Places 類型是否明顯互斥（Fail-Open：缺欄位或無目標類型一律回傳 False）。"""
    raw_types = store.get("types")
    types_set = {str(t).strip().lower() for t in raw_types if t} if isinstance(raw_types, list) else set()
    primary = str(store.get("primary_type") or "").strip().lower()

    # 0. 非餐飲業態基底守門（Fail-Open：僅當 primary_type 或 types 有值時才檢查）
    if primary or types_set:
        strict_food_hit = bool(types_set & {"restaurant", "food", "cafe", "bakery", "bar", "meal_takeaway"})
        if (primary in _NON_FOOD_PRIMARY_TYPES and not strict_food_hit) or not _is_food_capable(primary, types_set):
            label = primary or ", ".join(sorted(types_set)[:2])
            return True, f"非餐飲業態（{label}）"

    targets = {str(t).strip().lower() for t in (target_place_types or []) if str(t).strip()}
    if not targets:
        return False, None

    # 1. Google Places primaryType / types 通用檢查
    if primary or types_set:
        if primary in targets:
            return False, None
        if primary in _DISTINCT_PRIMARY_TYPES:
            return True, f"Google 主類型（{primary}）與目標需求不符"
        # primary 為泛用餐廳或缺失時，檢查次要 types 是否有交集
        if not (types_set & targets):
            if targets <= _NON_DINING_TARGET_TYPES or (types_set & _DISTINCT_PRIMARY_TYPES):
                label = primary or ", ".join(sorted(types_set)[:2])
                return True, f"Google 類型（{label}）與目標需求不符"

    # 2. Tabelog genre 粗粒度互斥檢查（僅在目標對應明確大類時觸發，且保護複合業態）
    genre = str(store.get("tabelog_genre") or "").strip()
    if genre:
        target_buckets = _resolve_target_buckets(targets)
        if target_buckets:
            # 只要包含任一目標大類關鍵字即放行
            for b_name in target_buckets:
                if any(tok in genre for tok in _MACRO_BUCKETS[b_name][1]):
                    return False, None
            # 否則若命中其他互斥大類則判定不符
            for b_name, (_, tokens) in _MACRO_BUCKETS.items():
                if b_name not in target_buckets and any(tok in genre for tok in tokens):
                    return True, f"Tabelog 分類（{genre}）與目標需求不符"

    return False, None
