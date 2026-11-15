# -*- coding: utf-8 -*-
"""State 3: Parallel Tabelog Enrichment and Foodie Ranking Package

負責即時 Tabelog 爬取、實體對齊 (Entity Resolution)、評論向量化與檢索、
老饕綜合得分計算 (Foodie Score) 以及 Top 店家排序去重。
"""

from __future__ import annotations

from src.agent.stages.enrichment.cache import (
    _build_cached_tabelog_result,
    _build_seed_result,
    _is_tabelog_cache_complete,
    check_cached_store,
    check_seed_store,
)
from src.agent.stages.enrichment.crawler import search_and_resolve_tabelog
from src.agent.stages.enrichment.formatting import format_payment_methods
from src.agent.stages.enrichment.geocoding import (
    _BREADCRUMB_STATION_RE,
    _align_walking_and_station,
    _hydrate_station_walking_distances,
)
from src.agent.stages.enrichment.orchestrator import execute_state_3_parallel_tabelog
from src.agent.stages.enrichment.reviews import (
    enrich_foodie_reviews,
    index_store_reviews,
    semantic_rerank_reviews,
)
from src.agent.stages.enrichment.scoring import (
    _extract_min_budget_jpy,
    apply_deterministic_constraints,
    calculate_foodie_score,
    rank_and_select_top_stores,
)
from src.agent.stages.enrichment.single_place import enrich_single_place

__all__ = [
    # Formatting
    "format_payment_methods",
    # Scoring & Ranking
    "calculate_foodie_score",
    "_extract_min_budget_jpy",
    "apply_deterministic_constraints",
    "rank_and_select_top_stores",
    # Geocoding & Transit
    "_BREADCRUMB_STATION_RE",
    "_align_walking_and_station",
    "_hydrate_station_walking_distances",
    # Reviews & Embeddings
    "index_store_reviews",
    "semantic_rerank_reviews",
    "enrich_foodie_reviews",
    # Cache
    "_is_tabelog_cache_complete",
    "_build_cached_tabelog_result",
    "_build_seed_result",
    "check_cached_store",
    "check_seed_store",
    # Crawler
    "search_and_resolve_tabelog",
    # Single Place
    "enrich_single_place",
    # Orchestrator
    "execute_state_3_parallel_tabelog",
]
