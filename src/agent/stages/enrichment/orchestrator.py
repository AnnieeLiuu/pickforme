# -*- coding: utf-8 -*-
"""State 3 Orchestrator: Adaptive Early Stopping, parallel execution, and foodie ranking."""

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


def count_high_score_stores(stores: list[dict[str, Any]]) -> int:
    """計算符合 Tabelog 高分名店標準 (tabelog_rating >= 3.45 或百名店/獲獎) 的店家數量"""
    return sum(
        1
        for s in stores
        if (s.get("tabelog_rating") and float(s["tabelog_rating"]) >= 3.45)
        or bool(s.get("awards"))
        or bool(s.get("hyakumeiten"))
    )


def should_trigger_tier2(
    high_score_count: int,
    budget_max_jpy: Optional[int],
    has_tier2_places: bool,
) -> bool:
    """評估是否滿足自適應早停條件。

    規則：
    - 若已有 >= 2 家高分名店且無預算限制，觸發早停 (回傳 False)。
    - 若高分店不足 (< 2) 或使用者指定預算上限 (需查完全部候選以挑選平價店)，且存在 Tier 2 店家，啟動 Tier 2 (回傳 True)。
    """
    return bool(has_tier2_places and (high_score_count < 2 or budget_max_jpy is not None))


async def execute_state_3_parallel_tabelog(
    fsm: FoodRecommendationFSM,
    places: list[dict[str, Any]],
    area_hint: Optional[str] = None,
    modifier: Optional[str] = None,
    preferences: Optional[dict[str, Any]] = None,
    time_constraint: Optional[str] = None,
    budget_max_jpy: Optional[int] = None,
) -> list[dict[str, Any]]:
    """State 3: 自適應漸進查驗 (Adaptive Early Stopping) + Tabelog 老饕綜合重排"""
    t0 = time.time()
    tier1_places = places[:4]
    tier2_places = places[4:6]

    # 1. Tier 1 平行查驗 (前 4 家高潛力種子)
    tier1_tasks = [fsm._enrich_single_place(p, area_hint=area_hint) for p in tier1_places]
    tier1_results = await asyncio.gather(*tier1_tasks)

    # 2. 品質門檻檢驗：若已有 >= 2 家達到 Tabelog 高分名店標準 (tabelog_rating >= 3.45 或百名店)
    high_score_count = count_high_score_stores(list(tier1_results))
    final_enriched = list(tier1_results)

    # 3. 自適應決策：若品質不足、或使用者有明確預算條件 (需查完 Tier 2 才能撈出平價店)，啟動 Tier 2 查驗
    if should_trigger_tier2(high_score_count, budget_max_jpy, bool(tier2_places)):
        logger.info(
            f"Triggering Tier 2 enrichment for {len(tier2_places)} stores "
            f"(high_score_count={high_score_count}, budget_max_jpy={budget_max_jpy})."
        )
        tier2_tasks = [fsm._enrich_single_place(p, area_hint=area_hint) for p in tier2_places]
        tier2_results = await asyncio.gather(*tier2_tasks)
        final_enriched.extend(tier2_results)
    else:
        logger.info(f"Tier 1 high-score count={high_score_count}. Early stopping satisfied, skipping Tier 2.")

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
