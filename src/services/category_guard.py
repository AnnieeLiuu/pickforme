# -*- coding: utf-8 -*-
"""Shared Category Consistency Guardrail for Stage 2 & Stage 3."""

from __future__ import annotations

from typing import Any, Optional

_CAFE_TARGET_KEYWORDS = (
    "カフェ",
    "喫茶",
    "コーヒー",
    "珈琲",
    "咖啡",
    "cafe",
    "coffee",
    "tea",
    "抹茶",
    "茶房",
)

_ALLOWED_CAFE_GOOGLE_TYPES = frozenset(
    {
        "cafe",
        "coffee_shop",
        "tea_house",
        "bakery",
        "dessert_shop",
        "ice_cream_shop",
        "confectionery",
    }
)

_EXCLUDED_CAFE_GOOGLE_PRIMARY_TYPES = frozenset(
    {
        "japanese_restaurant",
        "restaurant",
        "ramen_restaurant",
        "sushi_restaurant",
        "steak_house",
        "yakiniku_restaurant",
        "izakaya",
        "bar",
        "hookah_bar",
        "night_club",
        "pub",
        "fast_food_restaurant",
        "meal_takeaway",
    }
)

_SAFE_CAFE_TABELOG_GENRES = (
    "カフェ",
    "喫茶",
    "コーヒー",
    "珈琲",
    "紅茶",
    "日本茶",
    "スイーツ",
    "ケーキ",
    "パン",
    "甘味",
    "パフェ",
    "クレープ",
    "ジェラート",
)

_EXCLUDED_CAFE_TABELOG_GENRES = (
    "食堂",
    "定食",
    "居酒屋",
    "ラーメン",
    "焼肉",
    "寿司",
    "鮨",
    "バー",
    "シーシャ",
    "焼鳥",
    "ステーキ",
    "とんかつ",
    "うどん",
    "そば",
    "鍋",
    "中華",
    "カレー",
)


def is_cafe_or_drink_target(
    cuisine: Optional[str] = None,
    search_keyword: Optional[str] = None,
    explore_category: Optional[str] = None,
) -> bool:
    """判定本次查詢是否屬於咖啡／喫茶／飲品類別。"""
    if explore_category == "drink":
        return True
    combined = f"{cuisine or ''} {search_keyword or ''}".lower()
    return any(k in combined for k in _CAFE_TARGET_KEYWORDS)


def check_category_mismatch(
    store: dict[str, Any],
    cuisine: Optional[str] = None,
    search_keyword: Optional[str] = None,
    explore_category: Optional[str] = None,
) -> tuple[bool, Optional[str]]:
    """檢查單一店家與目標品類是否明顯互斥（Fail-Open：缺欄位一律回傳 False）。"""
    if not is_cafe_or_drink_target(cuisine=cuisine, search_keyword=search_keyword, explore_category=explore_category):
        return False, None

    # 1. Google Places primaryType / types 檢查
    raw_types = store.get("types")
    types_set = {str(t).strip().lower() for t in raw_types if t} if isinstance(raw_types, list) else set()
    primary = str(store.get("primary_type") or "").strip().lower()

    if primary or types_set:
        if primary not in _ALLOWED_CAFE_GOOGLE_TYPES and not (types_set & _ALLOWED_CAFE_GOOGLE_TYPES):
            if primary in _EXCLUDED_CAFE_GOOGLE_PRIMARY_TYPES:
                return True, f"Google 類型（{primary}）與咖啡廳／飲品需求不符"

    # 2. Tabelog genre 檢查（保護複合咖啡品類如「ダイニングバー・カフェ」）
    genre = str(store.get("tabelog_genre") or "").strip()
    if genre:
        if not any(safe in genre for safe in _SAFE_CAFE_TABELOG_GENRES):
            if any(excl in genre for excl in _EXCLUDED_CAFE_TABELOG_GENRES):
                return True, f"Tabelog 分類（{genre}）與咖啡廳／飲品需求不符"

    return False, None
