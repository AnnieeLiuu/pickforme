"""PickForMe Agent FSM Stages Package

4-Stage Deterministic Food Recommendation Pipeline Components:
- State 1: `intent` (Micro-LLM Intent extraction, follow-up handler, context sanitization)
- State 2: `places_search` (Ladder Places search 800m->1800m->3000m, foodie prefiltering, prewarm caching)
- State 3: `enrichment` (Parallel Tabelog scraper, entity resolution, review vector indexing, foodie scoring & ranking)
- State 4: `synthesis` (Card generation prompt, Markdown template fallback, rating sanitization, payment formatting)
"""

from src.agent.stages.enrichment import (
    calculate_foodie_score,
    enrich_single_place,
    execute_state_3_parallel_tabelog,
    format_payment_methods,
    index_store_reviews,
    rank_and_select_top_stores,
    semantic_rerank_reviews,
)
from src.agent.stages.intent import (
    INTENT_PARSER_SYSTEM_PROMPT,
    execute_follow_up,
    execute_state_1_intent,
    sanitize_history_for_intent,
)
from src.agent.stages.places_search import (
    execute_state_2_places_ladder,
    prefilter_potential_foodie_places,
    resolve_search_constraints,
)
from src.agent.stages.synthesis import (
    AGENT_SYSTEM_PROMPT_PATH,
    RECOMMENDATION_CARD_SYSTEM_PROMPT,
    execute_state_4_synthesis,
    get_recommendation_card_prompt,
    render_template_fallback_card,
    sanitize_card_ratings,
)

__all__ = [
    # State 1
    "INTENT_PARSER_SYSTEM_PROMPT",
    "sanitize_history_for_intent",
    "execute_state_1_intent",
    "execute_follow_up",
    # State 2
    "resolve_search_constraints",
    "prefilter_potential_foodie_places",
    "execute_state_2_places_ladder",
    # State 3
    "format_payment_methods",
    "calculate_foodie_score",
    "rank_and_select_top_stores",
    "enrich_single_place",
    "index_store_reviews",
    "semantic_rerank_reviews",
    "execute_state_3_parallel_tabelog",
    # State 4
    "AGENT_SYSTEM_PROMPT_PATH",
    "RECOMMENDATION_CARD_SYSTEM_PROMPT",
    "get_recommendation_card_prompt",
    "render_template_fallback_card",
    "sanitize_card_ratings",
    "execute_state_4_synthesis",
]
