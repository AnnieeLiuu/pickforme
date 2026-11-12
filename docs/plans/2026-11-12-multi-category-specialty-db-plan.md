# Multi-Category Local Specialty Guide (Food / Drink / Souvenir) + Neon DB Auto-Growing Persistence Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade `LocalSpecialtyGuideService` to support three cultural exploration dimensions (`food` 特色美食 / `drink` 在地飲品・茶酒・喫茶 / `souvenir` 伴手禮・百年名菓), complete with historical/cultural origin stories and item introductions, backed by a Neon Postgres table (`area_specialty_guides`) that automatically persists newly discovered Yahoo-grounded stations (`Write-Back Cache`) alongside `config/local_specialties.json` seed data.

**Architecture:**
1. **Database Schema & Repository (`src/db/schema.sql`, `src/db/repositories/specialty_guide.py`)**:
   - Add `area_specialty_guides` table in `src/db/schema.sql` with composite primary key `(area_key, category)` (`category` in `'food'`, `'drink'`, `'souvenir'`), `area_name`, `aliases` (`JSONB`), `intro`, `specialties` (`JSONB`), `source_tier` (`'curated'` | `'grounding'`), and `updated_at`.
   - Create `SpecialtyGuideRepository` with `ensure_table()`, `get_guide(area_query, category)`, `upsert_guide(guide, aliases)`, and `seed_from_json(seed_data)` so newly discovered stations from Tier 2 Yahoo Japan Grounding are automatically persisted to Neon DB and survive Cloud Run container restarts.
2. **Three-Category Cultural Engine (`src/services/cuisine_guide.py`, `config/local_specialties.json`)**:
   - Expand `AreaSpecialtyGuide` with `category: Literal["food", "drink", "souvenir"] = "food"`.
   - Expand `config/local_specialties.json` structure to support per-area categories (`"food"`, `"drink"`, `"souvenir"`), including **金澤 (Kanazawa)** (featuring `加賀棒茶`, `能登・加賀地酒`, `金箔抹茶` under `drink`, and `長生殿落雁`, `金箔長崎蛋糕`, `加賀八幡起上最中` under `souvenir`), **宇治 (Uji)**, plus `drink` and `souvenir` entries for existing curated hubs (神保町, 淺草, 京都祇園, 大阪難波, 博多, etc.).
   - Lookup order in `LocalSpecialtyGuideService.get_area_guide(area_name, category="food", llm_client=...)`:
     1. In-memory runtime cache `self._runtime_cache[(clean_area, category)]`
     2. Tier 1 Curated JSON (`config/local_specialties.json`) -> 0ms hit
     3. Neon DB (`SpecialtyGuideRepository.get_guide(clean_area, category)`) -> hits previously grounded or dynamically added DB records
     4. Tier 2 Yahoo Japan Grounding + `LLMClient.call_structured_json(schema=AreaSpecialtyGuide)` with category-specific Japanese search queries (`food`: 名物 グルメ 発祥 歴史; `drink`: 名物 飲み物 お茶 地酒 喫茶 歴史; `souvenir`: お土産 名物 和菓子 銘菓 老舗 歴史) -> **automatically persists (`upsert_guide`) to Neon DB**!
     5. Category-aware safe fallback (`_build_safe_fallback_guide(clean_area, category)`).
3. **Intent & Multi-Turn Category Switching (`src/agent/gate.py`, `src/agent/schemas.py`, `src/agent/stages/intent.py`, `src/agent/pipeline.py`, `src/services/google_places.py`)**:
   - Add `explore_category: Optional[Literal["food", "drink", "souvenir"]] = None` to `IntentOutput` in `src/agent/schemas.py`.
   - In `QuickPassGate` (`src/agent/gate.py`):
     - Detect drink exploration queries (e.g., `"金澤有什麼喝的？"`, `"金澤 喝的"`, `"宇治有什麼好喝的"`, `"淺草特色飲品"`, `"那喝的呢？"`) -> `intent="explore_cuisine", explore_category="drink"`.
     - Detect souvenir exploration queries (e.g., `"金澤有什麼伴手禮？"`, `"金澤 伴手禮"`, `"京都必買お土産"`, `"淺草名產推薦"`, `"有什麼伴手禮？"`) -> `intent="explore_cuisine", explore_category="souvenir"`.
     - Detect food exploration queries (e.g., `"神保町有什麼特色美食？"`) -> `intent="explore_cuisine", explore_category="food"`.
   - Support cross-turn category switching without repeating location: if user first asks `"金澤有什麼特色美食？"` and next asks `"那喝的呢？"` or `"有什麼伴手禮？"`, `execute_state_1_intent` inherits `location="金澤"` from `working_memory["last_location"]`.
   - When user replies `1` / `2` / `3` after a `drink` or `souvenir` guide, `working_memory["suggested_cuisines"]` maps to the selected drink/souvenir keyword (e.g., `"加賀棒茶"` or `"長生殿 落雁 和菓子"`), and `GooglePlacesClient` (`category_type_map`) includes tea houses (`tea_house`, `cafe`) and souvenir/confectionery shops (`confectionery`, `gift_shop`, `bakery`, `dessert_shop`).

**Tech Stack:** Python 3.11, Neon Postgres (`psycopg` async pool), Pydantic v2, httpx, BeautifulSoup4, pytest

---

## 🔍 Review Findings Incorporated (2026-11-12, supersedes conflicting details below)

> Pre-flight bugs #1 (explore location mis-capture, e.g. `location="有什麼"`) and #2 (stale `suggested_cuisines` never cleared) were fixed separately as v1.3.1 with regressions in `tests/test_real_world_regressions.py`. Items below are binding for this plan.

| # | Decision | Applies to |
| :---: | :--- | :--- |
| 3 | **Search keyword ≠ display name.** Add `search_keyword_ja: str` to `SpecialtyItem` (e.g. `"加賀棒茶 ほうじ茶"`, `"長生殿 森八"`). `working_memory.suggested_cuisines` stores `search_keyword_ja`; `cuisine_name` (Chinese, may contain `（）`) is display-only. Fallback when absent: `name_ja` with `・` → space. | Task 2, 3 |
| 4 | **DB fail-open, no hidden I/O in tests.** `LocalSpecialtyGuideService(guide_repo=None)` by default → DB tier skipped. Production wiring (`src/agent/core.py` / handlers) injects `SpecialtyGuideRepository()`. Every repo call wrapped in `asyncio.wait_for(..., timeout=1.5)` + `except Exception` → skip tier. Repo lazily runs `CREATE TABLE IF NOT EXISTS` once per process (class-level flag) and catches `UndefinedTable`; `docs/ARCHITECTURE.md` notes `python -m src.db.init_db`. | Task 1, 2 |
| 5 | **Single lookup order:** Tier 1 JSON (authoritative) → in-memory cache `(area_key, category)` → Neon DB (`manual` > `grounding`) → Tier 2 Yahoo Grounding (+ write-back) → category-aware fallback (never cached, never persisted). | Task 2 |
| 6 | **Drop `seed_from_curated_dict`.** JSON stays the sole source for curated data; DB holds only `grounding` (auto) and `manual` (human-added) rows. `source_tier` ∈ `curated` (JSON only) / `manual` / `grounding`. | Task 1 |
| 7 | **Write-back quality gate.** Persist only if ≥2 grounding snippets AND Pydantic validation passes AND exactly 3 specialties. Store `grounding_snippets JSONB` for audit and `verified BOOLEAN DEFAULT FALSE`. | Task 1, 2 |
| 8 | **Alias SQL & key normalization.** Use `aliases @> jsonb_build_array(%s)`. `normalize_area_key()` strips `車站/駅/站/周邊/附近` and folds 金沢→金澤, 浅草→淺草 etc. via a small kanji fold map; write-back stores both the user query key and LLM `area_name` as aliases. | Task 1, 2 |
| 9 | **Category-aware 「直接推薦」.** `working_memory.last_explore_category` saved with the guide; `direct_recommend` maps to `food`→`熱門人氣美食`, `drink`→`人気 カフェ 茶房 日本茶`, `souvenir`→`人気 お土産 和菓子 老舗`. | Task 3 |
| 10 | **JSON schema per category.** `{"金澤": {"area_name", "aliases", "categories": {"food": {"intro","specialties"}, "drink": {...}, "souvenir": {...}}}}`; legacy top-level `intro/specialties` treated as `food`. | Task 2 |
| 11 | **Synthesis wording.** Pass `explore_category` (via `working_memory` → `parsed`) to State 4 so card titles/prompt say 茶寮・喫茶 / 老舖・伴手禮店 instead of 餐廳 when relevant. | Task 3 |

**Gate rule ordering (Task 3):** location-less follow-ups (`那喝的呢？`, `有什麼伴手禮？`) are matched *before* location-bearing explore regexes; all captured locations go through `clean_explore_location()`. Collision checks must keep `新宿 居酒屋`, `京都 抹茶甜點`, `淺草 買伴手禮的店` as `search`.

**Content accuracy:** curated `drink`/`souvenir` entries only state widely documented facts; omit uncertain years. User reviews Kanazawa & Uji entries before merge.

**Docs / pre-push:** update `src/db/schema.sql`, `docs/ARCHITECTURE.md` (schema table + lookup flow), `CHANGELOG.md`, `README.md`, regenerate `public/index.html`.

---

## Global Constraints

- **Zero Hallucination on History & Products**: Never allow LLM to invent dates, historical figures, or product attributes not grounded in Tier 1 curated JSON, Neon DB, or Tier 2 Yahoo Japan snippets.
- **Graceful Offline / Mock Compatibility**: If Neon DB is unreachable or unconfigured in unit tests, `SpecialtyGuideRepository` calls must fail-open gracefully without breaking 0ms offline tests.
- **Zero Regression**: All 161 existing unit tests and the 20-case 4-Tier offline evaluation benchmark (`scripts/run_eval.py`) must pass 100%.

---

### Task 1: Neon DB Schema & `SpecialtyGuideRepository`

**Files:**
- Modify: `src/db/schema.sql`
- Create: `src/db/repositories/specialty_guide.py`
- Modify: `tests/test_cuisine_guide.py`

- [ ] **Step 1: Add `area_specialty_guides` DDL to `src/db/schema.sql`**
  ```sql
  -- 8. 在地特色美食・飲品・伴手禮與飲食文化知識庫 (支援 Tier 1 種子與 Tier 2 自動持久化生長)
  CREATE TABLE IF NOT EXISTS area_specialty_guides (
      area_key VARCHAR(100) NOT NULL,
      category VARCHAR(20) NOT NULL DEFAULT 'food',
      area_name VARCHAR(100) NOT NULL,
      aliases JSONB DEFAULT '[]'::jsonb,
      intro TEXT NOT NULL,
      specialties JSONB NOT NULL,
      source_tier VARCHAR(20) DEFAULT 'grounding',
      updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
      PRIMARY KEY (area_key, category)
  );
  CREATE INDEX IF NOT EXISTS idx_specialty_guides_category ON area_specialty_guides(category);
  ```

- [ ] **Step 2: Implement `SpecialtyGuideRepository` in `src/db/repositories/specialty_guide.py`**
  - Methods:
    - `async def get_guide(self, area_key: str, category: str = "food") -> Optional[dict[str, Any]]`: Queries by exact `area_key` + `category`, or checks `aliases @> to_jsonb(area_key)`.
    - `async def upsert_guide(self, area_key: str, category: str, area_name: str, intro: str, specialties: list[dict[str, Any]], aliases: Optional[list[str]] = None, source_tier: str = "grounding") -> None`: Performs `INSERT INTO area_specialty_guides ... ON CONFLICT (area_key, category) DO UPDATE`.
    - `async def seed_from_curated_dict(self, curated_db: dict[str, dict[str, Any]]) -> int`: Batch upserts curated JSON entries into `area_specialty_guides` (preserving `source_tier='curated'`).

---

### Task 2: Expand `config/local_specialties.json` & `LocalSpecialtyGuideService` for `food` / `drink` / `souvenir` + DB Write-Back

**Files:**
- Modify: `config/local_specialties.json`
- Modify: `src/services/cuisine_guide.py`
- Modify: `tests/test_cuisine_guide.py`

- [ ] **Step 1: Upgrade `config/local_specialties.json` with `food`, `drink`, and `souvenir` categories**
  - Add **金澤 (Kanazawa)** (`aliases`: `["金澤", "金沢", "kanazawa", "金澤車站", "金沢駅", "近江町市場", "兼六園", "東茶屋街"]`):
    - `food`: 金澤關東煮（金沢おでん・蟹面）、能登海鮮丼與喉黑魚（のどぐろ・海鮮丼）、金澤黑咖哩（ゴーゴーカレー・金沢カレー）
    - `drink`: 加賀棒茶（加賀棒茶・ほうじ茶）、加賀與能登地酒（加賀の地酒・日本酒）、金箔抹茶與町家茶屋（金箔抹茶・茶屋）
    - `souvenir`: 長生殿與傳統和菓子（長生殿・落雁・和菓子）、烏雞庵金箔長崎蛋糕（金箔かすてら）、加賀八幡起上最中（加賀八幡起上もなか）
  - Add `drink` and `souvenir` sections (backward-compatible with top-level `specialties` as default `food`) for 神保町, 淺草, 京都祇園, 大阪難波, 博多, 新宿, 橫濱, 築地, 月島.

- [ ] **Step 2: Update `AreaSpecialtyGuide` and `LocalSpecialtyGuideService` in `src/services/cuisine_guide.py`**
  - Add `category: Literal["food", "drink", "souvenir"] = Field(default="food", description="探索類別：food(特色美食) | drink(特色飲品・茶酒) | souvenir(伴手禮・名菓)")` to `AreaSpecialtyGuide`.
  - Inject `guide_repo: Optional[SpecialtyGuideRepository] = None` into `LocalSpecialtyGuideService.__init__`.
  - In `get_area_guide(self, area_name: str, category: Literal["food", "drink", "souvenir"] = "food", llm_client: Optional[LLMClient] = None)`:
    1. Check Tier 1 curated JSON (`_match_tier_1_curated(clean_area, category)`).
    2. Check runtime cache `_runtime_cache[(clean_area, category)]`.
    3. Check Neon DB via `await self.guide_repo.get_guide(clean_area, category)`.
    4. Run Tier 2 Yahoo Japan Grounding (`_fetch_yahoo_grounding_snippets(clean_area, category)`) + `llm_client.call_structured_json(schema=AreaSpecialtyGuide)` -> on success, cache in memory AND call `await self.guide_repo.upsert_guide(...)` so the new station is permanently saved in Neon DB!
    5. Fallback to `_build_safe_fallback_guide(clean_area, category)`.
  - Update `render_guide_markdown(self, guide: AreaSpecialtyGuide)` so headers, item labels (`🎁 **伴手禮特色與口感**` vs `😋 **茶湯風味與品飲亮點**` vs `😋 **風味與必吃亮點**`), and bottom interactive tips match `guide.category` and remind users they can also ask `「那喝的呢？」` or `「有什麼伴手禮？」`.

---

### Task 3: Wire `explore_category` into `QuickPassGate`, `IntentOutput`, `intent.py`, `pipeline.py`, and `google_places.py`

**Files:**
- Modify: `src/agent/schemas.py`
- Modify: `src/agent/gate.py`
- Modify: `src/agent/stages/intent.py`
- Modify: `src/agent/pipeline.py`
- Modify: `src/services/google_places.py`
- Modify: `tests/test_cuisine_guide.py`

- [ ] **Step 1: Add `explore_category` to `IntentOutput` (`src/agent/schemas.py`)**
  - Add `explore_category: Optional[Literal["food", "drink", "souvenir"]] = Field(default=None, description='當 intent 為 explore_cuisine 時的探索子類別："food"(特色美食)、"drink"(特色飲品/茶酒)、"souvenir"(伴手禮/名菓)')`.

- [ ] **Step 2: Add Drink & Souvenir Fast-Path Rules in `src/agent/gate.py`**
  - Add `_EXPLORE_DRINK_RE` matching patterns like `^(.{2,15}?)(?:車站|駅|站|附近|周邊)?\s*(?:有什麼|有哪些|有啥|推薦)?(?:好喝的?|特色飲品|特色飲料|名茶|地酒|喝的|飲品推薦)[？?!！\s]*$` and follow-up `^(?:那|請問)?(?:有什麼|有哪些)?(?:喝的|好喝的|特色飲品|飲料)呢?[？?!！\s]*$`.
  - Add `_EXPLORE_SOUVENIR_RE` matching patterns like `^(.{2,15}?)(?:車站|駅|站|附近|周邊)?\s*(?:有什麼|有哪些|有啥|推薦|必買)?(?:伴手禮|名產|土產|お土産|みやげ|和菓子|銘菓)[？?!！\s]*$` and follow-up `^(?:那|請問)?(?:有什麼|有哪些|推薦)?(?:伴手禮|名產|土產|お土産)呢?[？?!！\s]*$`.

- [ ] **Step 3: Resolve `explore_category` and Location Inheritance in `src/agent/stages/intent.py` & `src/agent/pipeline.py`**
  - In `execute_state_1_intent`, ensure `parsed["explore_category"]` defaults to `"food"` when `intent == "explore_cuisine"`. If `location` is omitted (e.g., user asks `"那喝的呢？"` or `"有什麼伴手禮？"` after asking about Kanazawa), inherit `location` from `working_memory["last_location"]`.
  - In `FoodRecommendationFSM.run_pipeline()`, pass `category=explore_cat` to `self.cuisine_guide_service.get_area_guide(area_name=explore_loc, category=explore_cat, llm_client=self.llm_client)`.
  - Expand `category_type_map` in `src/services/google_places.py` with `"茶": ["tea_house", "cafe"]`, `"棒茶": ["tea_house", "cafe"]`, `"抹茶": ["tea_house", "cafe"]`, `"伴手禮": ["confectionery", "gift_shop", "dessert_shop"]`, `"和菓子": ["confectionery", "dessert_shop"]`.

- [ ] **Step 4: Run Full Verification Suite**
  - Run `pytest tests/test_cuisine_guide.py -v` (including new tests for `"金澤 喝的"` -> `加賀棒茶`, `"金澤 伴手禮"` -> cultural + souvenir intro, cross-turn `"那伴手禮呢？"`, and Tier 2 Yahoo Grounding auto-persisting to `SpecialtyGuideRepository`).
  - Run `python -m ruff format src/ tests/`, `python -m ruff check src/ tests/`, `python -m mypy src/`, `pytest`, and `python scripts/run_eval.py`.

