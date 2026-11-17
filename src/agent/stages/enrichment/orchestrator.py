# -*- coding: utf-8 -*-
"""State 3 Orchestrator: Semaphore-bounded parallel enrichment and foodie ranking."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import TYPE_CHECKING, Any, Optional

from src.agent.stages.enrichment.geocoding import _hydrate_station_walking_distances
from src.agent.stages.enrichment.reviews import enrich_foodie_reviews
from src.agent.stages.enrichment.scoring import apply_deterministic_constraints

if TYPE_CHECKING:
    from src.agent.pipeline import FoodRecommendationFSM

logger = logging.getLogger(__name__)


def _extract_station_label(raw_desc: Any) -> Optional[str]:
    """從 nearest_station_desc 提取乾淨站名，避免巢狀引號。"""
    if not isinstance(raw_desc, str) or not raw_desc.strip():
        return None
    m = re.search(r"「([^」]+)」", raw_desc)
    if m:
        return m.group(1).strip() or None
    cleaned = re.sub(r"^(?:從|鄰近)", "", raw_desc.strip())
    cleaned = re.sub(r"步行約.*$", "", cleaned).strip("「」『』\"' ")
    return cleaned or None


def _extract_distant_station_gems(
    final_enriched: list[dict[str, Any]],
    top_stores: list[dict[str, Any]],
    max_gems: int = 2,
) -> list[dict[str, Any]]:
    """從未入選 Top 3 的已查驗店家中，萃取具備明確車站的高評價延伸老店。"""
    top_ids = {id(s) for s in top_stores}
    top_urls = {s.get("tabelog_url") for s in top_stores if s.get("tabelog_url")}
    top_names = {
        re.sub(r"[\s\-_・，,（）()]+", "", str(s.get("name_ja") or s.get("name") or "")).lower()
        for s in top_stores
        if (s.get("name_ja") or s.get("name"))
    }

    candidates: list[dict[str, Any]] = []
    for s in final_enriched:
        if id(s) in top_ids:
            continue
        if s.get("category_mismatch") or s.get("constraint_violation"):
            continue
        t_url = s.get("tabelog_url")
        raw_name = str(s.get("name_ja") or s.get("name") or "").strip()
        clean_name = re.sub(r"[\s\-_・，,（）()]+", "", raw_name).lower()
        if (t_url and t_url in top_urls) or (clean_name and clean_name in top_names) or not raw_name:
            continue

        station = _extract_station_label(s.get("nearest_station_desc"))
        if not station:
            continue

        t_rating = float(s.get("tabelog_rating") or 0.0)
        has_award = bool(s.get("awards") or s.get("hyakumeiten"))
        dist_m = int(s.get("distance_meters") or 0)
        if t_rating >= 3.45 or has_award or (dist_m > 1000 and t_rating >= 3.30):
            candidates.append(
                {
                    "name": raw_name,
                    "station": station,
                    "tabelog_rating": t_rating if t_rating > 0 else None,
                    "has_award": has_award,
                }
            )

    candidates.sort(
        key=lambda g: (1 if g.get("has_award") else 0, float(g.get("tabelog_rating") or 0.0)),
        reverse=True,
    )
    gems: list[dict[str, Any]] = []
    seen_stations: set[str] = set()
    for g in candidates:
        if g["station"] in seen_stations:
            continue
        seen_stations.add(g["station"])
        gems.append(g)
        if len(gems) >= max_gems:
            break
    return gems


async def execute_state_3_parallel_tabelog(
    fsm: FoodRecommendationFSM,
    places: list[dict[str, Any]],
    area_hint: Optional[str] = None,
    modifier: Optional[str] = None,
    preferences: Optional[dict[str, Any]] = None,
    time_constraint: Optional[str] = None,
    budget_max_jpy: Optional[int] = None,
    cuisine: Optional[str] = None,
    search_keyword: Optional[str] = None,
    explore_category: Optional[str] = None,
    target_place_types: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """State 3: 全候選店 Semaphore 限流並發查驗 + Tabelog 老饕綜合重排"""
    t0 = time.time()
    candidate_places = places[:6]
    enrich_sem = asyncio.Semaphore(4)

    async def _bounded_enrich(place: dict[str, Any]) -> dict[str, Any]:
        async with enrich_sem:
            return await fsm._enrich_single_place(place, area_hint=area_hint)

    # 1. 全候選平行查驗（Semaphore(4) 限流防 Tabelog 403，同時確保無早停遺珠）
    final_enriched = list(await asyncio.gather(*[_bounded_enrich(p) for p in candidate_places]))

    # 4. 預算與硬性條件預先加權評估（於挑選 Top 3 前執行，避免符合預算者被高價店擠出名單）
    if modifier or preferences or time_constraint or budget_max_jpy is not None or target_place_types or cuisine:
        final_enriched = apply_deterministic_constraints(
            final_enriched,
            modifier=modifier,
            preferences=preferences,
            time_constraint=time_constraint,
            budget_max_jpy=budget_max_jpy,
            cuisine=cuisine,
            search_keyword=search_keyword,
            explore_category=explore_category,
            target_place_types=target_place_types,
        )

    # 5. 老饕綜合加權重排，精選 Top 3（同步萃取未入選之延伸站點高分老店）
    top_stores = fsm.rank_and_select_top_stores(final_enriched, top_k=3)
    fsm._last_distant_station_gems = _extract_distant_station_gems(final_enriched, top_stores)

    # 6. 為最終入選的 Top 店家平行豐富定向老饕食評與車站步行距離
    if top_stores:
        await asyncio.gather(
            _hydrate_station_walking_distances(fsm, top_stores),
            *[enrich_foodie_reviews(fsm, s) for s in top_stores],
        )

    latency = int((time.time() - t0) * 1000)

    stores_input_summary = [
        {
            "name": p.get("name"),
            "phone": p.get("phone"),
            "google_rating": p.get("rating") or p.get("google_rating"),
            "primary_type": p.get("primary_type"),
            "types": p.get("types") or [],
        }
        for p in places
    ]

    stores_output_summary = [
        {
            "name_ja": s.get("name_ja") or s.get("name"),
            "tabelog_rating": s.get("tabelog_rating"),
            "review_count": s.get("review_count"),
            "price_lunch": s.get("price_lunch"),
            "price_dinner": s.get("price_dinner"),
            "supports_paypay": s.get("supports_paypay"),
            "credit_cards": s.get("credit_cards") or [],
            "awards": s.get("awards") or [],
            "tabelog_url": s.get("tabelog_url"),
            "sample_reviews": s.get("sample_reviews") or [],
            "rating_source": s.get("rating_source", "Tabelog"),
            "rank_badge": s.get("rank_badge"),
            "primary_type": s.get("primary_type"),
            "types": s.get("types") or [],
            "tabelog_genre": s.get("tabelog_genre"),
            "category_mismatch": bool(s.get("category_mismatch")),
        }
        for s in top_stores
    ]

    await fsm.trace_logger.log_step(
        action_type="state_3_tabelog",
        tool_name="parallel_tabelog_enrichment",
        tool_input={"stores_count": len(places), "stores_searched": stores_input_summary},
        tool_output={"selected_count": len(top_stores), "aligned_stores": stores_output_summary},
        latency_ms=latency,
    )
    return top_stores
