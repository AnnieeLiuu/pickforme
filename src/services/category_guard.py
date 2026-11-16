# -*- coding: utf-8 -*-
"""Generic Category Consistency Guardrail for Stage 2 & Stage 3."""

from __future__ import annotations

from typing import Any, Optional

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


def check_category_mismatch(
    store: dict[str, Any],
    target_place_types: Optional[list[str]] = None,
) -> tuple[bool, Optional[str]]:
    """通用檢查單一店家與目標 Google Places 類型是否明顯互斥（Fail-Open：缺欄位或無目標類型一律回傳 False）。"""
    targets = {str(t).strip().lower() for t in (target_place_types or []) if str(t).strip()}
    if not targets:
        return False, None

    # 1. Google Places primaryType / types 通用檢查
    raw_types = store.get("types")
    types_set = {str(t).strip().lower() for t in raw_types if t} if isinstance(raw_types, list) else set()
    primary = str(store.get("primary_type") or "").strip().lower()

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
