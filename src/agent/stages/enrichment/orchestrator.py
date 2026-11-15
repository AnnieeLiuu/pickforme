# -*- coding: utf-8 -*-
"""State 3 Orchestrator: Semaphore-bounded parallel enrichment and foodie ranking."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any, Optional

from src.agent.stages.enrichment.geocoding import _hydrate_station_walking_distances
from src.agent.stages.enrichment.reviews import enrich_foodie_reviews
from src.agent.stages.enrichment.scoring import apply_deterministic_constraints

if TYPE_CHECKING:
    from src.agent.pipeline import FoodRecommendationFSM

logger = logging.getLogger(__name__)


async def execute_state_3_parallel_tabelog(
    fsm: FoodRecommendationFSM,
    places: list[dict[str, Any]],
    area_hint: Optional[str] = None,
    modifier: Optional[str] = None,
    preferences: Optional[dict[str, Any]] = None,
    time_constraint: Optional[str] = None,
    budget_max_jpy: Optional[int] = None,
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
    if modifier or preferences or time_constraint or budget_max_jpy is not None:
        final_enriched = apply_deterministic_constraints(
            final_enriched,
            modifier=modifier,
            preferences=preferences,
            time_constraint=time_constraint,
            budget_max_jpy=budget_max_jpy,
        )

    # 5. 老饕綜合加權重排，精選 Top 3
    top_stores = fsm.rank_and_select_top_stores(final_enriched, top_k=3)

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
