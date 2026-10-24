"""PickForMe Agent FSM Stages Package

4-Stage Deterministic Food Recommendation Pipeline Components:
- State 1: `intent` (Micro-LLM Intent extraction, follow-up handler, context sanitization)
- State 2: `places_search` (Ladder Places search 800m->1800m->3000m, foodie prefiltering, prewarm caching)
- State 3: `enrichment` (Parallel Tabelog scraper, entity resolution, review vector indexing, foodie scoring & ranking)
- State 4: `synthesis` (Card generation prompt, Markdown template fallback, rating sanitization, payment formatting)
"""
