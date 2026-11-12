# -*- coding: utf-8 -*-
"""State 2: Google Places Ladder Search and Pre-filtering

負責階梯式搜尋 (800m ➔ 1200m ➔ 1800m ➔ 2500m 擴圈保底)、老饕潛力智慧預篩以及檢索關鍵字約束解析。
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import math
import re
import time
from typing import TYPE_CHECKING, Any, Optional

from src.services.entity_resolver import calculate_distance_meters

if TYPE_CHECKING:
    from src.agent.pipeline import FoodRecommendationFSM

logger = logging.getLogger(__name__)

WIDE_AREA_SCENIC_REGIONS: set[str] = {
    "箱根",
    "伊豆",
    "輕井澤",
    "軽井沢",
    "日光",
    "草津",
    "有馬",
    "白濱",
    "白浜",
    "河口湖",
    "富士五湖",
    "由布院",
    "湯布院",
    "登別",
    "定山溪",
    "黑川",
    "黒川",
    "白馬",
    "富良野",
    "美瑛",
}


def is_wide_area_origin(origin_name: Optional[str]) -> bool:
    """判斷起點是否為多商圈散佈之山區/廣域度假勝地 (如「箱根」、「輕井澤」)

    規則：
    1. 若結尾包含明確站點後綴（站/駅/Station/口），如「箱根湯本站」、「強羅車站」，視為具體站點 (False)。
    2. 都心稠密商圈（新宿、澀谷、池袋、銀座）維持點對點導航 (False)。
    3. 命中廣域景區清單則回傳 True，觸發方案 2 店家最近車站提取機制。
    """
    if not origin_name:
        return False
    clean = origin_name.strip()
    if any(clean.endswith(s) for s in ("站", "駅", "Station", "口")):
        return False
    stripped = re.sub(r"[町村山]$", "", clean).strip()
    return clean in WIDE_AREA_SCENIC_REGIONS or stripped in WIDE_AREA_SCENIC_REGIONS


def format_walking_description(
    origin_name: Optional[str],
    dist_m: int,
    walk_mins: int,
    nearest_station_desc: Optional[str] = None,
    address: Optional[str] = None,
) -> str:
    """產生精準步行描述。在廣域景區且有 nearest_station_desc 時，絕不拼接廣域中心點米數 (避免 Usain Bolt 錯位)"""
    if not is_wide_area_origin(origin_name):
        origin_prefix = f"從「{origin_name}」" if origin_name else "從「您目前位置」"
        return f"{origin_prefix}步行約 {walk_mins} 分鐘 ({dist_m}m)"

    # 廣域景區模式：
    # 1. 優先採用店家所屬之最近車站步行指引 (如「從「強羅站」步行約 5 分鐘」)
    if nearest_station_desc:
        return nearest_station_desc

    # 2. 次之若地址可提煉出在地子商圈 (如 箱根町強羅 ➡️ 強羅商圈)
    sub_area = None
    if address:
        sub_m = re.search(r"[町市區区]([^\d\s\-\,]{2,8}?)(?=\d|丁目|番地|\s|$)", address)
        if sub_m:
            sub_area = sub_m.group(1).strip()
    if sub_area:
        return f"位於{origin_name}「{sub_area}」商圈 (距定位點約 {dist_m}m)"

    # 3. 保底
    return f"位於「{origin_name}」區域 (距定位點約 {dist_m}m)"


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
    """雙源老饕潛力智慧預篩 (Tabelog-First Dual-Source Pre-filtering)

    1. 絕對保送權重 (+10.0)：凡由 Tabelog 主動獵店提名 (tabelog_seed=True) 或已知 Tabelog >= 3.45 / 百名店之店家，
       無條件優先保送進入 Top 種子名單，徹底消除「日本 Tabelog 名店在 Google 僅 3.8 星被觀光客店淘汰」之盲點。
    2. 日本在地老店保護曲線：將 Google 3.7 ~ 4.4 星視為日本常態優質區間（壓平超過 4.4 星之觀光客虛高分增益），
       結合評論數對數平滑與步行距離選出剩餘種子。
    """
    if not places:
        return []

    def calc_pre_score(p: dict[str, Any]) -> float:
        t_rating = float(p.get("tabelog_rating") or 0.0)
        has_award = bool(p.get("hyakumeiten") or p.get("awards"))
        is_tabelog_seed = bool(p.get("tabelog_seed"))

        # 1. Tabelog 老饕種子保送通道
        tabelog_boost = 0.0
        if is_tabelog_seed or t_rating >= 3.45 or has_award:
            tabelog_boost = 10.0 + (t_rating if t_rating > 0 else 3.5) + (0.5 if has_award else 0.0)

        g_rating = float(p.get("rating") or p.get("google_rating") or 3.5)
        rev_count = int(p.get("userRatingCount") or p.get("review_count") or 10)
        dist_m = float(p.get("distance_meters") or 500)

        # 2. 日本在地老店保護曲線：3.8 ~ 4.4 星皆給予穩健高分，壓抑 > 4.4 星觀光客店的超額輾壓
        effective_g = min(g_rating, 4.4) if g_rating >= 3.8 else g_rating
        rating_score = effective_g * 0.55

        # 評論數對數平滑 (上限 0.9，防大眾網紅店刷量)
        rev_score = min(math.log10(max(1, rev_count)) * 0.3, 0.9)
        # 距離懲罰：800m 內無扣分，超過 800m 每 500m 扣 0.1 (若為廣域景區或有最近車站則豁免)
        dist_penalty = 0.0
        if not (is_wide_area_origin(p.get("origin_name")) or p.get("nearest_station_desc")):
            if dist_m > 800:
                dist_penalty = max(0.0, (dist_m - 800) / 500 * 0.1)

        return tabelog_boost + rating_score + rev_score - dist_penalty

    sorted_places = sorted(places, key=calc_pre_score, reverse=True)
    return sorted_places[:max_seeds]


async def _hydrate_tabelog_seeds_with_places(
    fsm: FoodRecommendationFSM,
    lat: float,
    lng: float,
    origin_name: Optional[str],
    keyword: str,
    max_radius: int,
    exclude_set: set[str],
) -> list[dict[str, Any]]:
    """源頭 A：主動從 Tabelog 獵取該地區高分/百名店名單，並反查 Google Places 錨定座標與步行距離"""
    if not origin_name:
        return []

    scraper = getattr(fsm, "tabelog_scraper", None)
    discover_fn = getattr(scraper, "discover_area_top_stores", None)
    search_text_fn = getattr(fsm.places_client, "search_by_text", None)
    if not callable(discover_fn) or not callable(search_text_fn):
        return []

    try:
        res = discover_fn(area_name=origin_name, cuisine=keyword, max_seeds=3)
        tabelog_seeds = await res if inspect.isawaitable(res) else res
        if not isinstance(tabelog_seeds, list) or not tabelog_seeds:
            return []
    except Exception as e:
        logger.debug(f"Tabelog area seed discovery skipped: {e}")
        return []

    async def _resolve_one_seed(seed: dict[str, Any]) -> Optional[dict[str, Any]]:
        name_ja = (seed.get("name_ja") or "").strip()
        if not name_ja:
            return None
        try:
            call_res = search_text_fn(
                query=f"{name_ja} {origin_name}",
                lat=lat,
                lng=lng,
                radius=max_radius,
                max_results=1,
            )
            found = await call_res if inspect.isawaitable(call_res) else call_res
            if not isinstance(found, list) or not found:
                return None
            place = dict(found[0])
            pid = place.get("google_place_id")
            if not pid or pid in exclude_set:
                return None

            if seed.get("nearest_station_desc"):
                place["nearest_station_desc"] = seed["nearest_station_desc"]

            p_lat = place.get("lat")
            p_lng = place.get("lng")
            if p_lat and p_lng:
                dist_m = int(calculate_distance_meters(lat, lng, p_lat, p_lng))
                if dist_m > max_radius:
                    return None
                walk_mins = max(1, math.ceil(dist_m / 80))
                place["distance_meters"] = dist_m
                place["walk_time_minutes"] = walk_mins
                place["origin_name"] = origin_name
                place["walking_desc"] = format_walking_description(
                    origin_name=origin_name,
                    dist_m=dist_m,
                    walk_mins=walk_mins,
                    nearest_station_desc=place.get("nearest_station_desc"),
                    address=place.get("address"),
                )

            place["tabelog_seed"] = True
            if seed.get("tabelog_url"):
                place["tabelog_url"] = seed["tabelog_url"]
            if seed.get("tabelog_rating") is not None:
                place["tabelog_rating"] = seed["tabelog_rating"]
            if seed.get("review_count") is not None:
                place["tabelog_review_count"] = seed["review_count"]
            if seed.get("hyakumeiten"):
                place["hyakumeiten"] = seed["hyakumeiten"]
                place["awards"] = seed["hyakumeiten"]
            return place
        except Exception as e:
            logger.debug(f"Failed to hydrate Tabelog seed '{name_ja}': {e}")
            return None

    hydrated = await asyncio.gather(*[_resolve_one_seed(s) for s in tabelog_seeds if isinstance(s, dict)])
    return [p for p in hydrated if p is not None]


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
    exclude_set = set(exclude_place_ids or set())

    radii = custom_radii or [800, 1200, 1800]
    max_seed_radius = max(radii) if radii else 1800

    # 源頭 A：平行啟動 Tabelog 主動獵店任務 (若有明確地名)
    tabelog_seed_task = asyncio.create_task(
        _hydrate_tabelog_seeds_with_places(
            fsm=fsm,
            lat=lat,
            lng=lng,
            origin_name=origin_name,
            keyword=keyword,
            max_radius=max_seed_radius,
            exclude_set=exclude_set,
        )
    )

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
                    p["walking_desc"] = format_walking_description(
                        origin_name=origin_name,
                        dist_m=dist_m,
                        walk_mins=walk_mins,
                        nearest_station_desc=p.get("nearest_station_desc"),
                        address=p.get("address"),
                    )
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
                    p["walking_desc"] = format_walking_description(
                        origin_name=origin_name,
                        dist_m=dist_m,
                        walk_mins=walk_mins,
                        nearest_station_desc=p.get("nearest_station_desc"),
                        address=p.get("address"),
                    )
                    p["radius_expanded"] = True
                candidates.append(p)
                existing_ids.add(pid)
            if len(candidates) >= 3:
                break

    # 雙源匯流：合併 Tabelog 主動獵店種子與 Google 階梯候選池
    tabelog_hydrated_seeds = await tabelog_seed_task
    if tabelog_hydrated_seeds:
        by_id: dict[str, dict[str, Any]] = {c["google_place_id"]: c for c in candidates if c.get("google_place_id")}
        for t_seed in tabelog_hydrated_seeds:
            pid = t_seed.get("google_place_id")
            if not pid:
                continue
            if pid in by_id:
                # 升級既有 Google 候選店為 Tabelog 保送種子
                by_id[pid]["tabelog_seed"] = True
                for k in (
                    "tabelog_url",
                    "tabelog_rating",
                    "tabelog_review_count",
                    "hyakumeiten",
                    "awards",
                    "nearest_station_desc",
                ):
                    if t_seed.get(k) is not None and not by_id[pid].get(k):
                        by_id[pid][k] = t_seed[k]
                if t_seed.get("nearest_station_desc") and is_wide_area_origin(origin_name):
                    by_id[pid]["walking_desc"] = format_walking_description(
                        origin_name=origin_name,
                        dist_m=by_id[pid].get("distance_meters", 0),
                        walk_mins=by_id[pid].get("walk_time_minutes", 1),
                        nearest_station_desc=t_seed["nearest_station_desc"],
                        address=by_id[pid].get("address"),
                    )
            else:
                candidates.insert(0, t_seed)
                by_id[pid] = t_seed

    # 雙源老饕潛力預篩：Tabelog 種子 100% 優先保送 + 挑選種子店家 (前 4 筆主選 + 2 筆備選)
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
