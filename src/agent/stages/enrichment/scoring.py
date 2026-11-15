# -*- coding: utf-8 -*-
"""Foodie scoring, deterministic constraints, and store ranking for Enrichment stage."""

from __future__ import annotations

import re
from typing import Any, Optional

from src.agent.schemas import extract_preferences_from_text
from src.agent.stages.places_search import is_wide_area_origin


def calculate_foodie_score(store: dict[str, Any]) -> float:
    """計算單一店家老饕綜合重排得分 (Foodie Score)"""
    t_rating = store.get("tabelog_rating")
    g_rating = float(store.get("rating") or store.get("google_rating") or 3.5)
    dist_m = float(store.get("distance_meters") or 300)
    awards = store.get("awards") or []
    hyakumeiten = store.get("hyakumeiten") or []
    rev_count = int(store.get("review_count") or 0)

    # 1. 基礎評分 (Tabelog 優先，若無則降權折算上限 3.05)
    if t_rating and float(t_rating) > 0:
        base_score = float(t_rating)
    else:
        # 沿用日本在地老店保護曲線：壓平超過 4.4 星之 Google 虛高分，避免無 Tabelog 店家靠單一 4.8 星輾壓候選
        effective_g = min(g_rating, 4.4) if g_rating >= 3.8 else g_rating
        base_score = min(effective_g * 0.65, 3.05)  # Google 評分降權折算，確保 Tabelog 高分店絕對優先

    # 2. 老饕榮譽加分
    award_bonus = 0.0
    if awards or hyakumeiten:
        award_bonus += 0.15
    if rev_count >= 500:
        award_bonus += 0.05

    # 3. 距離懲罰 (若為廣域景區或已具備最近車站描述，豁免中心點距離扣分，杜絕偏遠降權)
    dist_penalty = 0.0
    if not (is_wide_area_origin(store.get("origin_name")) or store.get("nearest_station_desc")):
        if dist_m > 1500:
            dist_penalty = 0.12
        elif dist_m > 800:
            dist_penalty = 0.05

    # 4. 硬性條件違反懲罰 (Deterministic Constraint Sensor)
    constraint_penalty = 0.45 if store.get("constraint_violation") is True else 0.0

    return base_score + award_bonus - dist_penalty - constraint_penalty


def _extract_min_budget_jpy(price_str: Optional[str]) -> Optional[int]:
    """從 Tabelog 預算區間字串解析最低消費金額 (日圓)

    - 「～￥999」等僅有上限者，下界視為 0（不會誤殺低預算查詢）
    - 「￥1,000～￥1,999」或「￥10,000～」等具明確下界者，回傳下界整數
    - 無效或缺失字串（如 None / "-"）回傳 None
    """
    if not isinstance(price_str, str):
        return None
    s = price_str.strip()
    if not s or s == "-":
        return None
    if s[0] in ("～", "~"):
        return 0
    m = re.search(r"[￥¥]?\s*([0-9][0-9,]*)", s)
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except ValueError:
        return None


def apply_deterministic_constraints(
    stores: list[dict[str, Any]],
    modifier: Optional[str] = None,
    preferences: Optional[dict[str, Any]] = None,
    time_constraint: Optional[str] = None,
    budget_max_jpy: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Deterministic Constraint Sensor: 根據當輪 modifier、長期偏好、預算與時間對明確違反條件的店家標記降權"""
    if not stores:
        return []

    payment_reqs: set[str] = set()
    dietary_reqs: set[str] = set()

    if isinstance(preferences, dict):
        pay_list = preferences.get("payment_preferences")
        if isinstance(pay_list, list):
            payment_reqs.update(str(p) for p in pay_list if p)
        diet_list = preferences.get("dietary_restrictions")
        if isinstance(diet_list, list):
            dietary_reqs.update(str(d) for d in diet_list if d)

    if modifier:
        extracted = extract_preferences_from_text(modifier)
        payment_reqs.update(extracted.get("payment_preferences", []))
        dietary_reqs.update(extracted.get("dietary_restrictions", []))
        # 若 modifier 包含宵夜/早餐關鍵字且未傳入 time_constraint，自動啟動時間感知
        if not time_constraint:
            if any(k in modifier for k in ("宵夜", "深夜", "半夜")):
                time_constraint = "late_night"
            elif any(k in modifier for k in ("早餐", "早午餐", "早點")):
                time_constraint = "breakfast"
            elif any(k in modifier for k in ("現在有開", "現在開著", "營業中")):
                time_constraint = "now"

    if not payment_reqs and not dietary_reqs and not time_constraint and budget_max_jpy is None:
        return stores

    for store in stores:
        violated = False
        store_name = str(store.get("name_ja") or store.get("name") or "")

        # 1. 支付條件檢驗
        if "credit_card" in payment_reqs:
            is_cash_only = store.get("accepts_cash_only") is True
            cards = store.get("credit_cards")
            no_cards = isinstance(cards, list) and len(cards) == 0 and store.get("accepts_credit_cards") is not True
            if is_cash_only or no_cards:
                violated = True

        if "paypay" in payment_reqs:
            if store.get("supports_paypay") is False or store.get("accepts_cash_only") is True:
                violated = True

        # 2. 飲食禁忌檢驗
        if "不吃牛" in dietary_reqs and "牛" in store_name:
            violated = True
        if "不吃豬" in dietary_reqs and any(k in store_name for k in ("豚", "豬", "猪")):
            violated = True
        if "不吃生食" in dietary_reqs and any(k in store_name for k in ("寿司", "鮨", "刺身", "壽司")):
            violated = True
        if "不吃辣" in dietary_reqs and any(k in store_name for k in ("激辛", "麻辣", "担々", "擔擔", "辛")):
            violated = True

        # 3. 營業時間與即時營業狀態檢驗 (Deterministic Hours Sensor)
        # 嚴格守門：只有在明確標註已打烊/今日公休 (open_now is False) 時才懲罰；若無資料 (open_now is None) 則保護不扣分
        if time_constraint in ("now", "late_night", "breakfast"):
            if store.get("open_now") is False:
                violated = True

        # 4. 預算上限軟性檢驗 (Deterministic Budget Sensor)
        # 僅在明確取得下界金額且高於 budget_max_jpy 時標記降權，絕不刪除候選店家
        if budget_max_jpy is not None and budget_max_jpy > 0:
            lunch_min = _extract_min_budget_jpy(store.get("price_lunch"))
            dinner_min = _extract_min_budget_jpy(store.get("price_dinner"))
            if time_constraint == "late_night":
                effective_min = dinner_min if dinner_min is not None else lunch_min
            else:
                candidates_min = [p for p in (lunch_min, dinner_min) if p is not None]
                effective_min = min(candidates_min) if candidates_min else None
            if effective_min is not None and effective_min > budget_max_jpy:
                violated = True

        if violated:
            store["constraint_violation"] = True

    return stores


def rank_and_select_top_stores(
    stores: list[dict[str, Any]],
    top_k: int = 3,
) -> list[dict[str, Any]]:
    """依老饕綜合得分降序重排，並進行嚴格去重 (Deduplication)，選取 Top K 高分店家，注入排名徽章"""
    if not stores:
        return []

    ranked = sorted(stores, key=calculate_foodie_score, reverse=True)

    deduped: list[dict[str, Any]] = []
    seen_tabelog_urls: set[str] = set()
    seen_names: set[str] = set()

    for s in ranked:
        t_url = s.get("tabelog_url")
        raw_name = s.get("name_ja") or s.get("name") or ""
        clean_name = re.sub(r"[\s\-_・，,（）()]+", "", raw_name).lower()

        if t_url and t_url in seen_tabelog_urls:
            continue
        if clean_name and clean_name in seen_names:
            continue

        if t_url:
            seen_tabelog_urls.add(t_url)
        if clean_name:
            seen_names.add(clean_name)

        deduped.append(s)
        if len(deduped) >= top_k:
            break

    top_stores = deduped
    rank_badges = ["🥇", "🥈", "🥉", "🏅", "🎖️"]
    for idx, s in enumerate(top_stores):
        s["rank_index"] = idx + 1
        s["rank_badge"] = rank_badges[idx] if idx < len(rank_badges) else f"#{idx + 1}"

    return top_stores
