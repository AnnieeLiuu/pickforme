# -*- coding: utf-8 -*-
"""State 2: Google Places Ladder Search and Pre-filtering

負責階梯式搜尋 (800m ➔ 1200m ➔ 1800m ➔ 2500m 擴圈保底)、老饕潛力智慧預篩以及檢索關鍵字約束解析。
"""

from __future__ import annotations

import logging
import math
import re
import time
from typing import TYPE_CHECKING, Any, Optional

from src.services.entity_resolver import calculate_distance_meters

if TYPE_CHECKING:
    from src.agent.pipeline import FoodRecommendationFSM

logger = logging.getLogger(__name__)


def resolve_search_constraints(
    cuisine: str,
    modifier: Optional[str] = None,
) -> tuple[str, list[int], Optional[str]]:
    """解析並分離料理名稱、風格修飾語與物理距離約束 (非 Hardcode，杜絕 Places 搜尋脫靶)

    Returns:
        tuple[str, list[int], Optional[str]]:
            - clean_keyword: 純淨之 Google Places 檢索關鍵字 (例: "壽喜燒")
            - radii_ladder: 物理階梯半徑清單 (例: 15分鐘步行 -> [1200, 1700, 2500])
            - style_tag: 僅供卡片或語意重排之風格標籤 (例: "老店", "平價")
    """
    clean_cuisine = (cuisine or "熱門美食").strip()

    # 1. 偵測並移除誤混入 cuisine 的空間詞
    clean_cuisine = re.sub(r"(?:走路|步行|走)?\s*\d{1,2}\s*(?:分鐘|min|mins)[^\s]*", "", clean_cuisine).strip()
    clean_cuisine = re.sub(r"(?:遠一點|近一點|大圈一點|附近)", "", clean_cuisine).strip()
    if not clean_cuisine:
        clean_cuisine = "熱門美食"

    radii_ladder = [800, 1200, 1800]
    style_tag: Optional[str] = None

    if not modifier:
        return clean_cuisine, radii_ladder, None

    mod = modifier.strip()

    # 2. 物理空間約束：提取具體分鐘數 (以日本 80m/min 步行標準換算)
    walk_match = re.search(r"(?:走路|步行|走)?\s*(\d{1,2})\s*(?:分鐘|min|mins)", mod)
    if walk_match:
        mins = int(walk_match.group(1))
        # 換算為公尺，例如 15 分鐘 -> 1200m
        base_r = max(800, min(2500, mins * 80))
        radii_ladder = [base_r, min(2500, base_r + 500), 2500]
    elif any(kw in mod for kw in ["遠一點", "大圈一點", "距離遠", "擴大", "遠些"]):
        radii_ladder = [1200, 1800, 2500]
    elif any(kw in mod for kw in ["近一點", "最近", "附近", "腳程短", "不想走"]):
        radii_ladder = [500, 800, 1200]

    # 3. 剝離空間詞，檢驗是否有實質風格/口味詞 (如 "老店", "平價", "清淡", "濃郁")
    rem_style = re.sub(r"(?:走路|步行|走)?\s*\d{1,2}\s*(?:分鐘|min|mins)[^\s]*", "", mod)
    rem_style = re.sub(
        r"(?:可以接受|距離|遠一點|近一點|大圈一點|擴大|以內|內都可以|都可以|附近|能接受)",
        "",
        rem_style,
    ).strip()

    clean_keyword = clean_cuisine
    if rem_style and len(rem_style) >= 2:
        style_tag = rem_style
        # 只有實質風格詞才安全疊加到 keyword
        clean_keyword = f"{clean_cuisine} {rem_style}"

    return clean_keyword, radii_ladder, style_tag


def prefilter_potential_foodie_places(
    places: list[dict[str, Any]],
    max_seeds: int = 6,
) -> list[dict[str, Any]]:
    """Google 端老饕潛力智慧預篩 (Smart Pre-filtering)

    依評分、評論數合理性與距離，在 0ms 內預篩出最具備 Tabelog 高分潛力的種子店家
    """
    if not places:
        return []

    def calc_pre_score(p: dict[str, Any]) -> float:
        g_rating = float(p.get("rating") or p.get("google_rating") or 3.5)
        rev_count = int(p.get("userRatingCount") or p.get("review_count") or 10)
        dist_m = float(p.get("distance_meters") or 500)

        # Google 評分權重
        rating_score = g_rating * 0.6
        # 評論數對數平滑 (上限 0.9，防大眾網紅店刷量)
        rev_score = min(math.log10(max(1, rev_count)) * 0.3, 0.9)
        # 距離懲罰：800m 內無扣分，超過 800m 每 500m 扣 0.1
        dist_penalty = max(0.0, (dist_m - 800) / 500 * 0.1) if dist_m > 800 else 0.0

        return rating_score + rev_score - dist_penalty

    sorted_places = sorted(places, key=calc_pre_score, reverse=True)
    return sorted_places[:max_seeds]


async def execute_state_2_places_ladder(
    fsm: FoodRecommendationFSM,
    lat: float,
    lng: float,
    keyword: str,
    origin_name: Optional[str] = None,
    exclude_place_ids: Optional[set[str]] = None,
    max_candidates: int = 6,
    custom_radii: Optional[list[int]] = None,
) -> list[dict[str, Any]]:
    """State 2: Google Places 智慧階梯檢索 (800m ➔ 1200m ➔ 1800m ➔ 2500m 擴圈保底) + 潛力預篩"""
    t0 = time.time()
    candidates: list[dict[str, Any]] = []
    origin_prefix = f"從「{origin_name}」" if origin_name else "從「您目前位置」"
    exclude_set = set(exclude_place_ids or set())

    radii = custom_radii or [800, 1200, 1800]
    for r in radii:
        raw_places = await fsm.places_client.search_nearby(lat=lat, lng=lng, radius=r, keyword=keyword)
        # 過濾重複與已看過店家
        existing_ids = {c["google_place_id"] for c in candidates}
        for p in raw_places:
            pid = p.get("google_place_id")
            if pid and pid not in existing_ids and pid not in exclude_set:
                p_lat = p.get("lat")
                p_lng = p.get("lng")
                if p_lat and p_lng:
                    dist_m = int(calculate_distance_meters(lat, lng, p_lat, p_lng))
                    walk_mins = max(1, math.ceil(dist_m / 80))
                    p["distance_meters"] = dist_m
                    p["walk_time_minutes"] = walk_mins
                    p["origin_name"] = origin_name or "您目前位置"
                    p["walking_desc"] = f"{origin_prefix}步行約 {walk_mins} 分鐘 ({dist_m}m)"
                candidates.append(p)
                existing_ids.add(pid)

        if len(candidates) >= 3:
            break

    # 自適應擴圈：若在 1800m 內候選店少於 3 家，自動向外擴圈至 2500m 保底
    if len(candidates) < 3:
        raw_places = await fsm.places_client.search_nearby(lat=lat, lng=lng, radius=2500, keyword=keyword)
        existing_ids = {c["google_place_id"] for c in candidates}
        for p in raw_places:
            pid = p.get("google_place_id")
            if pid and pid not in existing_ids and pid not in exclude_set:
                p_lat = p.get("lat")
                p_lng = p.get("lng")
                if p_lat and p_lng:
                    dist_m = int(calculate_distance_meters(lat, lng, p_lat, p_lng))
                    walk_mins = max(1, math.ceil(dist_m / 80))
                    p["distance_meters"] = dist_m
                    p["walk_time_minutes"] = walk_mins
                    p["origin_name"] = origin_name or "您目前位置"
                    p["walking_desc"] = f"{origin_prefix}步行約 {walk_mins} 分鐘 ({dist_m}m)"
                    p["radius_expanded"] = True
                candidates.append(p)
                existing_ids.add(pid)
            if len(candidates) >= 3:
                break

    # Google 端老饕潛力預篩：挑選最具 Tabelog 高分潛力的種子店家 (前 4 筆主選 + 2 筆備選)
    final_candidates = fsm._prefilter_potential_foodie_places(candidates, max_seeds=max_candidates)
    latency = int((time.time() - t0) * 1000)

    await fsm.trace_logger.log_step(
        action_type="state_2_places",
        tool_name="search_nearby_ladder",
        tool_input={
            "lat": lat,
            "lng": lng,
            "keyword": keyword,
            "excluded_count": len(exclude_set),
        },
        tool_output={"count": len(final_candidates)},
        latency_ms=latency,
    )
    return final_candidates
