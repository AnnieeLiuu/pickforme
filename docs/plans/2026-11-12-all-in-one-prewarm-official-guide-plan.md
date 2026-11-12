# All-in-One Prewarm & Official Authority Culture Guide Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the PickForMe prewarm architecture so that triggering `/prewarm <area>` in Telegram executes an **All-in-One Prewarm**: handling prefecture-level wide area disambiguation, performing Places + Tabelog 20-restaurant caching, and executing deep official-domain (`.lg.jp` / `.or.jp`) scraping for three-dimensional culture guides (`food`, `drink`, `souvenir`) persisted to Neon DB (`area_specialty_guides`), accompanied by a comprehensive notification card and offline seed persistence.

**Architecture:**
1. **Interactive Wide-Area Guardrail (`src/bot/handlers.py`, `src/agent/stages/intent.py`, `src/db/repositories/session.py`)**: Detect broad administrative areas (e.g. `北海道`, `靜岡`, `長野`) in `/prewarm` via `is_prefecture_level_area()`; prompt with 3 core sub-areas or allow "全部" batch fan-out; support cancellation and graceful context switching.
2. **Official Domain Deep Scraping & Tiered Fallback Engine (`src/services/cuisine_guide.py`)**: Implement `_fetch_official_authority_guide()` using query targeting `site:lg.jp OR site:or.jp` with deep text extraction (handling Shift_JIS/EUC-JP encodings and skipping PDFs), gracefully falling back to Yahoo Grounding on network failure, with tiered database tagging (`source_tier = 'curated'` vs `'grounding'`).
3. **All-in-One Prewarm Worker & Multi-Category Pipeline (`src/worker/prewarm.py`)**: Extend `PrewarmWorker` with injected `LLMClient` to concurrently prewarm 20 Tabelog restaurants AND the 3 culture categories (`food`, `drink`, `souvenir`) with partial failure isolation, saving all items to Neon DB and emitting a rich, compact summary card under 4096 chars with safe Markdown.
4. **Seed Fixture DB Synchronization & Tier Ranking (`scripts/seed_specialties_to_db.py`, `src/db/repositories/specialty_guide.py`)**: An idempotent script to sync both dual-format entries (15 total records: 9 food + 2x3 full categories) from `config/local_specialties.json` into Neon DB with `source_tier = 'curated'`, and update SQL ranking order (`manual > curated > grounding`).

**Tech Stack:** Python 3.11, Neon Postgres (async psycopg pool), httpx, BeautifulSoup4, python-telegram-bot, Pydantic v2, pytest.

**Spec / Requirements Source:** User Design Interview via `/grill-me` (2026-11-12) & Independent Research Subagent Review.

## Global Constraints

- **0ms Offline Mock Tests**: All unit and regression tests must execute offline with Mock/AsyncMock, with zero network calls and zero live DB dependencies.
- **Zero Hallucination Guarantee**: Strict Pydantic extraction from fetched official text; no LLM invention of historical dates or non-existent dishes.
- **Partial Failure Isolation**: Failure in cultural guide generation must NEVER abort or discard the 20 cached restaurants; graceful degradation is mandatory.
- **AGENTS.md Line Guardrail**: `AGENTS.md` length must strictly remain $\le 106$ physical lines.
- **Pre-push Quality Gate**: Must pass `python -m ruff format src/ tests/ && python -m ruff check src/ tests/ && python -m mypy src/ && pytest` before any push.

## Review Focus

1. **Dual-Format Fixture Parsing**: Verify that both `categories` (Kanazawa, Uji) and legacy top-level `specialties` (remaining 9 areas) in `config/local_specialties.json` are seeded cleanly (15 records total) without key errors.
2. **Wide-Area Disambiguation & Cancellation**: When user inputs `/prewarm 北海道`, verify the bot responds with an interactive choice of sub-areas (`札幌`, `函館`, `小樽`, `全部`) and handles sub-area selection, `全部`, `取消`/`/cancel`, and context switching without crashing or trapping user state.
3. **Official Domain Failure & Encoding Resilience**: When `.lg.jp` / `.or.jp` sites are unreachable, return non-HTML content, or use `Shift_JIS` encoding, verify seamless decoding and fallback to Yahoo Japan snippet grounding.
4. **Prewarm Notification Card Completeness & Character Ceiling**: Verify Telegram notification payload contains both restaurant count and the 3x3 cultural highlights, formatted compactly under 4096 characters with safe Markdown escaping.
5. **Session Continuity & 0ms Hit**: Verify that asking for culture items after prewarming immediately hits the database in $\approx 20\text{ms}$ without re-scraping.

---

## Tasks

### Task 1: Seed Fixture Idempotent Sync Script & SQL Tier Ranking (`scripts/seed_specialties_to_db.py`, `src/db/repositories/specialty_guide.py`)

**Files:**
- Create: `scripts/seed_specialties_to_db.py`
- Modify: `src/db/repositories/specialty_guide.py:70-85`
- Test: `tests/test_seed_specialties.py`

- [x] **Step 1: Write failing test for specialty seed script & tier ranking**
  Create `tests/test_seed_specialties.py` testing:
  - `seed_specialties_to_db()` correctly reads `config/local_specialties.json`.
  - Handles dual formats: seeds 3 categories for areas with `categories` (金澤, 宇治) and `category="food"` for remaining 9 areas, inserting exactly 15 records with `source_tier='curated'`.
  - `SpecialtyGuideRepository.get_guide()` prioritizes `manual > curated > grounding`.

- [x] **Step 2: Run test to confirm failure**
  Run `pytest tests/test_seed_specialties.py` and confirm failure.

- [x] **Step 3: Update `SpecialtyGuideRepository.get_guide()` SQL ranking**
  In `src/db/repositories/specialty_guide.py`:
  Update `ORDER BY CASE WHEN source_tier = 'manual' THEN 1 WHEN source_tier = 'curated' THEN 2 ELSE 3 END, updated_at DESC`.

- [x] **Step 4: Implement `scripts/seed_specialties_to_db.py`**
  Implement the script with CLI arguments (`--file`, `--dry-run`), async Neon DB connection, dual format parser, and idempotent upsert via `SpecialtyGuideRepository.upsert_guide()`.

- [x] **Step 5: Verify test passes and run dry-run**
  Run `pytest tests/test_seed_specialties.py` and verify 100% pass.
  Run `python scripts/seed_specialties_to_db.py --dry-run` to verify 15 records parsed.

- [x] **Step 6: Commit changes**
  Commit with message: `feat(db): add idempotent seed script for specialty guides and sql tier ranking`

---

### Task 2: Official Domain Deep Scraping in `LocalSpecialtyGuideService`

**Files:**
- Modify: `src/services/cuisine_guide.py:180-350`
- Test: `tests/test_official_guide_scraper.py`

- [x] **Step 1: Write failing test for official domain scraper**
  Create `tests/test_official_guide_scraper.py` with mock httpx responses:
  - Test case 1: Successful official domain scrape (`site:lg.jp OR site:or.jp`) -> extracts official text (handling Shift_JIS) -> synthesizes guide -> `source_tier = 'curated'`.
  - Test case 2: Official site timeout/404 or PDF content -> falls back to Yahoo Grounding -> `source_tier = 'grounding'`.
  - Test case 3: Both fail -> falls back to safe fallback template.

- [x] **Step 2: Run test to confirm failure**
  Run `pytest tests/test_official_guide_scraper.py` and confirm failure.

- [x] **Step 3: Implement `_fetch_official_authority_guide()` and `prewarm_area_culture()` in `cuisine_guide.py`**
  - Update `LocalSpecialtyGuideService.__init__` to accept `llm_client: Optional[LLMClient] = None`.
  - Add search query targeting `site:lg.jp OR site:or.jp {clean_area} 観光協会 特産品 郷土料理 名物 お土産` (Japanese Shinjitai).
  - Parse candidate URLs, skip non-HTML/PDF links, fetch page content with `httpx` (4s timeout), decode with `resp.encoding = resp.apparent_encoding` or errors="replace", extract clean text with `BeautifulSoup`.
  - On success, pass text to `llm_client.call_structured_json(AreaSpecialtyGuide)` and upsert to Neon DB with `source_tier = 'curated'`.
  - On failure or text < 150 chars, fallback to `_fetch_and_synthesize_via_grounding()` (`source_tier = 'grounding'`).
  - Add `prewarm_area_culture(area_name, llm_client=None)` which executes prewarm for all 3 categories (`food`, `drink`, `souvenir`) concurrently via `asyncio.gather()`.

- [x] **Step 4: Verify test passes**
  Run `pytest tests/test_official_guide_scraper.py` and verify all tests pass.

- [x] **Step 5: Commit changes**
  Commit with message: `feat(services): implement official domain deep scraping with tiered fallback`

---

### Task 3: Interactive Wide-Area Guardrail & State Management in Telegram Handlers

**Files:**
- Modify: `src/db/repositories/session.py:118-140`
- Modify: `src/bot/handlers.py:150-200`
- Test: `tests/test_prewarm_wide_area.py`

- [x] **Step 1: Write failing test for wide-area prewarm command**
  Create `tests/test_prewarm_wide_area.py` simulating Telegram `/prewarm`:
  - Input: `/prewarm 北海道` -> Triggers disambiguation prompt listing 1. 札幌 2. 函館 3. 小樽 and prompt for "全部".
  - Input: `1` following prompt -> Triggers prewarm for `札幌` and clears state.
  - Input: `全部` following prompt -> Triggers prewarm for all 3 sub-areas and clears state.
  - Input: `取消` or `/cancel` -> Cancels prewarm prompt and replies "已取消預熱任務。".
  - Input: Normal search or command -> Clears prewarm prompt and proceeds normally.
  - Input: `/prewarm 箱根` -> Directly creates prewarm job without disambiguation.

- [x] **Step 2: Run test to confirm failure**
  Run `pytest tests/test_prewarm_wide_area.py` and confirm failure.

- [x] **Step 3: Update `SessionRepository` and `handlers.py`**
  - In `src/db/repositories/session.py`, update `update_working_memory` signature to accept `pending_prewarm_options: Optional[dict[str, Any]] = None`.
  - In `src/bot/handlers.py`:
    - Check `is_prefecture_level_area(area)` in `/prewarm`. If broad, save `pending_prewarm_options` in `working_memory` and send prompt.
    - In message handler, if `pending_prewarm_options` exists:
      - Match `取消` / `/cancel` -> clear state and reply cancellation.
      - Match `1`, `2`, `3` or `全部` -> dispatch job(s) and clear state.
      - If text is a new command or search query -> clear state and fall through to standard pipeline.

- [x] **Step 4: Verify test passes**
  Run `pytest tests/test_prewarm_wide_area.py` and verify 100% pass.

- [x] **Step 5: Commit changes**
  Commit with message: `feat(bot): add wide area interactive disambiguation and session state to prewarm command`

---

### Task 4: All-in-One Prewarm Worker Execution & Compact Notification Card

**Files:**
- Modify: `src/worker/prewarm.py:15-150`
- Test: `tests/test_prewarm_worker_all_in_one.py`

- [x] **Step 1: Write failing test for All-in-One Prewarm worker**
  Create `tests/test_prewarm_worker_all_in_one.py` testing that `PrewarmWorker.process_next_job`:
  - Prewarms places + Tabelog restaurants.
  - Concurrently prewarms culture (`food`, `drink`, `souvenir`) with partial failure isolation (culture failure does NOT fail restaurant caching).
  - Emits safe Markdown card under 4096 characters with 20 restaurants count + 3x3 cultural highlights.
  - Sends message via Telegram bot.

- [x] **Step 2: Run test to confirm failure**
  Run `pytest tests/test_prewarm_worker_all_in_one.py` and confirm failure.

- [x] **Step 3: Implement All-in-One workflow in `PrewarmWorker`**
  - In `PrewarmWorker.__init__`, inject `llm_client: Optional[LLMClient] = None` and `guide_service: Optional[LocalSpecialtyGuideService] = None`.
  - In `process_next_job()`, execute `guide_service.prewarm_area_culture(area_name)` wrapped in a `try...except` block with logging and fallback.
  - Format compact notification with bullet points, ensuring length $\le 2000$ characters and safe escaping.

- [x] **Step 4: Verify test passes**
  Run `pytest tests/test_prewarm_worker_all_in_one.py` and verify 100% pass.

- [x] **Step 5: Commit changes**
  Commit with message: `feat(worker): upgrade prewarm worker to all-in-one cultural and restaurant caching`

---

### Task 5: Documentation, Regression Mandate & Full Quality Gate

**Files:**
- Modify: `docs/ARCHITECTURE.md`
- Modify: `CHANGELOG.md`
- Modify: `tests/test_real_world_regressions.py`

- [x] **Step 1: Add regression tests in `tests/test_real_world_regressions.py`**
  Add dedicated regression tests:
  - `test_regression_all_in_one_prewarm_flow()`
  - `test_regression_prewarm_cancellation_and_invalid_choice()`

- [x] **Step 2: Update documentation**
  - Document All-in-One Prewarm flow, wide-area guardrails, and Official Authority Scraper in `docs/ARCHITECTURE.md`.
  - Add unreleased entry in `CHANGELOG.md`.
  - Verify `AGENTS.md` length $\le 106$ physical lines.

- [x] **Step 3: Run full pre-push verification suite**
  Run `python -m ruff format src/ tests/ && python -m ruff check src/ tests/ && python -m mypy src/ && pytest`.

- [x] **Step 4: Commit and push**
  Commit and push to remote (`main -> main`).
