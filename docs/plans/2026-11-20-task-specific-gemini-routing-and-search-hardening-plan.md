# Task-Specific Gemini Routing & Stage 2/3 Search Hardening Plan

- **Date**: 2026-11-20
- **Status**: Completed (Reviewed & Verified)
- **Trigger Case**: `run_id: cf31b483-a0b1-4458-855b-f121e0501d31` (`"日本橋人形町 烏龍麵"` missed百名店 `谷や (Taniya)` and included non-food photo studio `モリヤスタジオ` / tab title `メニュー写真`).

---

## 1. Root Cause Analysis (6-Dimensional Audit)

1. **Stage 1 Cross-Lingual Knowledge Deficit (`qwen/qwen3.8-27b`)**:
   - `qwen/qwen3.8-27b` is trained primarily on Mainland Simplified Chinese corpora (where `うどん` corresponds to 「烏冬麵」) and lacks the Taiwan Traditional Chinese lexical mapping 「烏龍麵 ➔ うどん」, outputting `"烏龍ラーメン"` or `"烏龍麺"` and `target_place_types=["ramen_restaurant"]`.
   - Google Gemini Flash (`gemini-3.8-flash`) natively aligns Taiwan Traditional Chinese and Japanese culinary concepts, directly outputting `search_keyword_ja="うどん"`.
   - When `gemini-3.8-flash` experiences transient HTTP 503 (`UNAVAILABLE: high demand`), `_call_openai_compatible_api` previously only retried on HTTP 429 (not 503), immediately failing over to Groq.
2. **Stage 2 Single-Keyword Fragility on Failover (`src/agent/stages/places_search.py`)**:
   - `resolve_search_constraints` discarded the user's original `cuisine` (`"烏龍麵"`) whenever `search_keyword_ja` was non-empty.
   - If Gemini is temporarily unavailable (503) and fails over to Groq (`qwen/qwen3.8-27b`), a skewed `search_keyword_ja` (`"烏龍ラーメン"`) yields `< 3` valid stores at 800m and immediately expands radius to 1200m/1800m/2500m (pulling in Tokyo Station stores 1.8km away) instead of falling back to the user's original `cuisine` (`"烏龍麵"`, which Google Places Multilingual Knowledge Graph natively resolves to `谷や` and `五代目 花山うどん` within 800m).
3. **Stage 2 Non-Food Business Leakage (`src/services/category_guard.py`)**:
   - `check_category_mismatch` only checked if `primary_type` belonged to `_DISTINCT_PRIMARY_TYPES`. A non-food business (`モリヤスタジオ`, `primary_type="service"`, `types=["point_of_interest", "service", "establishment"]`) bypassed `check_category_mismatch` and entered Stage 3.
4. **Stage 2 Unconditional `+10.0` Seed Boost & `/rstLst/` Ranking Directory Collision (`src/agent/stages/places_search.py` & `src/services/tabelog_scraper.py`)**:
   - `discover_area_top_stores` queried `site:tabelog.com {cuisine} {area} 予算`, which on Yahoo Japan often matches `/rstLst/` ranking directory pages (discarded by `_clean_tabelog_url`), leaving 0–1 single stores. Using `総合得点` targets single-store pages directly.
   - `_hydrate_tabelog_seeds_with_places` stamped all hydrated seeds with `tabelog_seed=True` without checking `check_category_mismatch` on the hydrated Google Place, and `prefilter_potential_foodie_places` granted `+10.0` boost even to `<3.45` or mismatched seeds.
5. **Stage 3 Tabelog Sub-Page Prefix Parsing (`src/services/tabelog_scraper.py`)**:
   - `_fetch_and_parse_grounding` split `raw_title` on `:` (`re.split(r"[（\(\-\|\:\[]", raw_title)[0]`). When Yahoo returned a sub-page title like `メニュー写真: 店名 - 食べログ` or `料理メニュー : 店名`, `[0]` extracted `"メニュー写真"` as `name_ja`, polluting downstream `fetch_tabelog_reviews`.

---

## 2. Architectural Design

### 2.1 Task-Specific Provider Routing (`preferred_provider`) & HTTP 503 Retry
- **`src/agent/llm_client.py`**:
  - Maintain `GEMINI_PRIMARY_MODEL = "gemini-3.8-flash"` and `settings.LLM_PRIMARY_PROVIDER = "groq"`.
  - In `_call_openai_compatible_api`, handle transient HTTP `503` (Service Unavailable / high demand spike) alongside `429` with a single fast backoff retry (`1.5s` for 503, `retry-after` / `3.0s` for 429).
  - Add optional keyword argument `preferred_provider: Optional[str] = None` to `LLMClient.call_text` and `LLMClient.call_structured_json`.
    - `effective_provider = (preferred_provider or getattr(settings, "LLM_PRIMARY_PROVIDER", "groq")).lower()`
    - When `effective_provider == "gemini"`, `call_text` routes `Gemini ➡️ Groq ➡️ Ollama`.
    - When ` preferred_provider` is omitted (`None`), `call_text` uses `settings.LLM_PRIMARY_PROVIDER` (`"groq"` by default), preserving 100% of existing behavior and tests.
- **Target Scenarios Routed to `preferred_provider="gemini"`**:
  1. **Scenario 1 — Stage 1 Intent & Keyword Extraction** (`src/agent/stages/intent.py: execute_state_1_intent`):
     Call `fsm.llm_client.call_structured_json(messages, schema=IntentOutput, preferred_provider="gemini")` (with signature inspection / `TypeError` fallback so custom test monkeypatches with 2-arg `fake_call_structured_json(messages, schema=...)` continue to work seamlessly).
  2. **Scenario 2 — Local Specialty Cultural Guide Cache-Miss Synthesis** (`src/services/cuisine_guide.py: _scrape_official_authority_guide` & `_fetch_and_synthesize_via_grounding`):
     Call `client_to_use.call_structured_json(..., preferred_provider="gemini")` (with `TypeError` fallback for custom test mocks) so Taiwan Traditional Chinese terminology is synthesized and cached in Neon DB on cache misses.
  3. **All Other Scenarios (Stage 3 Entity Resolver, Stage 4 Card Synthesis, Follow-up, Trip Scoping, Chitchat)**:
     Remain on the default provider (`Groq`) to keep 80%+ of token usage on low-cost Groq.

### 2.2 Stage 2 Non-F&B Domain Invariant & Raw-Cuisine 800m Failover (`src/services/category_guard.py` & `src/agent/stages/places_search.py`)
- **`src/services/category_guard.py`**:
  - Define `_NON_FOOD_PRIMARY_TYPES = frozenset({"service", "travel_agency", "lodging", "hotel", "real_estate_agency", "hair_care", "beauty_salon", "spa", "gym", "hospital", "doctor", "dentist", "pharmacy", "school", "university", "laundry", "car_repair", "gas_station", "parking", "transit_station", "train_station", "subway_station", "bus_station", "Corporate_office", "local_government_office"})` and `_FOOD_CAPABLE_TYPES = frozenset({"restaurant", "food", "cafe", "coffee_shop", "tea_house", "bakery", "dessert_shop", "ice_cream_shop", "confectionery", "bar", "pub", "hookah_bar", "night_club", "izakaya", "japanese_izakaya_restaurant", "meal_takeaway", "meal_delivery", "ramen_restaurant", "sushi_restaurant", "yakiniku_restaurant", "steak_house", "seafood_restaurant", "japanese_restaurant", "chinese_restaurant", "korean_restaurant", "italian_restaurant", "french_restaurant", "fast_food_restaurant", "hamburger_restaurant", "pizza_restaurant", "food_court", "store", "grocery_or_supermarket", "supermarket", "convenience_store", "liquor_store"})`.
  - In `check_category_mismatch` (even before checking `if not targets: return False, None`!):
    - If `primary` or `types_set` is present:
      - Let `has_food_type = bool(types_set & _FOOD_CAPABLE_TYPES) or primary in _FOOD_CAPABLE_TYPES or any(t.endswith(("_restaurant", "_cafe", "_bar", "_bakery", "_shop", "_food", "_izakaya")) for t in (types_set | ({primary} if primary else set())))`.
      - If `primary in _NON_FOOD_PRIMARY_TYPES` or not `has_food_type`:
        return `True, f"非餐飲業態（{primary or ', '.join(sorted(types_set)[:2])}）"`.
- **`src/agent/stages/places_search.py` & `src/agent/pipeline.py`**:
  - In `execute_state_2_places_ladder`, add optional `raw_cuisine: Optional[str] = None` (passed from `pipeline.py` as `raw_cuisine=run_ctx.cuisine`).
  - When `check_category_mismatch` returns `is_mismatch=True`, if the reason starts with `"非餐飲業態"`, completely discard the place (do NOT append to `mismatch_fallback`).
  - **800m Raw-Cuisine Rescue before Radius Expansion**:
    If after the first radius (`radii[0]`, typically 800m), `len(candidates) < 3` and `raw_cuisine` is a specific dish (not empty, not in `("熱門美食", "熱門人氣美食")`, and `raw_cuisine != keyword`), query `fsm.places_client.search_nearby(lat=lat, lng=lng, radius=radii[0], keyword=raw_cuisine)` once at the same core radius (`radii[0]`). This ensures that even if Gemini 503s and Groq mistranslates `"烏龍麵"` to `"烏龍ラーメン"`, Google Places' native multilingual engine immediately recovers `谷や` and `五代目 花山うどん` within 800m instead of expanding to Tokyo Station (1800m).
  - In `_hydrate_tabelog_seeds_with_places`:
    Verify `is_mismatch, _ = check_category_mismatch(place, target_place_types=target_place_types)` on the hydrated Google Place; if `is_mismatch` is True, discard the seed (or do not mark `tabelog_seed = True`).
  - In `prefilter_potential_foodie_places`:
    Only grant the `+10.0` `tabelog_boost` when `(is_tabelog_seed and (t_rating >= 3.45 or t_rating == 0.0)) or t_rating >= 3.45 or has_award` (so a low-rated 3.1–3.4 store from a noisy grounding query doesn't get a `+10.0` free pass over a 4.1–4.3 Google Place).

### 2.3 Stage 2 & Stage 3 Tabelog Grounding Title Normalization & Single-Store Anchor (`src/services/tabelog_scraper.py`)
- In `_fetch_and_parse_grounding`:
  - Before `name_ja = re.split(r"[（\(\-\|\:\[]", raw_title)[0].strip()`, strip Tabelog sub-page prefixes such as `メニュー写真 :`, `料理メニュー :`, `ドリンクメニュー :`, `ランチメニュー :`, `コース一覧 :`, `コース :`, `座席情報 :`, `座席 :`, `口コミ一覧 :`, `地図 :` (`_TABELOG_SUBPAGE_PREFIX_RE = re.compile(r"^(?:メニュー写真|料理メニュー|ドリンクメニュー|ランチ(?:メニュー)?|コース(?:一覧)?|座席(?:情報)?|写真(?:一覧)?|口コミ(?:一覧)?|クチコミ(?:一覧)?|周辺の地図|地図|アクセス|予約(?:サイト)?)\s*[:：]\s*", re.IGNORECASE)`).
- In `discover_area_top_stores`:
  - Query `site:tabelog.com {clean_cuisine} {clean_area} 総合得点` first via `_fetch_and_parse_grounding` (and if empty, fall back to `search_via_grounding`), avoiding `/rstLst/` ranking directory starvation.

---

## 3. Verification & Regression Testing
- Run full test suite (`pytest`), `ruff`, and `mypy`.
- Add regression tests in `tests/test_real_world_regressions.py` covering:
  1. Stage 1 & Cuisine Guide `preferred_provider="gemini"` routing + HTTP 503 single-retry & Groq failover.
  2. Non-F&B establishment (`モリヤスタジオ`, `primary_type="service"`) hard exclusion from candidates and `mismatch_fallback`.
  3. Tabelog sub-page prefix stripping (`メニュー写真: 店名 - 食べログ` ➔ `店名`).
  4. Stage 2 raw-cuisine 800m rescue when translated keyword yields `< 3` candidates (`"日本橋人形町 烏龍麵"` recovers `谷や`).
