# Local Specialty & Cultural Guide Implementation Plan (Completed)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a hybrid two-tier Local Specialty & Cultural Guide (`LocalSpecialtyGuideService`) that introduces 3 local specialty dishes with authentic historical/cultural stories when a user explores a Japanese location without specifying a concrete dish, and seamlessly chains their next turn (`1`/`2`/`3` or `"直接推薦"`) into the 4-Stage FSM restaurant search.

**Architecture:**
1. **Tier 1 Curated Knowledge Base (`config/local_specialties.json`) + Tier 2 Yahoo Japan Grounding (`src/services/cuisine_guide.py`)** provide zero-hallucination local food history and 3 representative dishes for any Japanese station/area.
2. **`SessionRepository.update_working_memory()` (`src/db/repositories/session.py`)** persists `last_location` and `suggested_cuisines: list[str]` in `working_memory`.
3. **Stage 1 (`src/agent/gate.py`, `src/agent/schemas.py`, `src/agent/stages/intent.py`) & Pipeline (`src/agent/pipeline.py`)** detect `explore_cuisine` requests (e.g. `"淺草有什麼好吃的？"`, `"神保町特色美食"`) and resolve next-turn selections (`"1"`, `"選2"`, `"直接推薦"`) back into standard `"search"` intents.

**Tech Stack:** Python 3.11, Pydantic v2, httpx, BeautifulSoup4, pytest

**Spec:** `docs/plans/2026-11-12-local-specialty-cultural-guide-design.md`

## Global Constraints

- Never allow the LLM to invent historical dates or origin legends without grounding snippets.
- Preserve 100% backward compatibility with all 158 existing tests and the 20-case 4-Tier offline evaluation benchmark (`scripts/run_eval.py`).
- All new tests must run offline in 0ms using `AsyncMock` / `MagicMock`.

## Review Focus

- User asks `"神保町有什麼好吃的"` or `"淺草特色美食"` -> triggers `explore_cuisine` and returns the 3 local specialties with cultural stories plus the `"1 / 2 / 3"` and `"直接推薦"` prompt.
- User replies `"1"` or `"選第2個"` on the next turn -> resolves `cuisine` from `working_memory["suggested_cuisines"]` and `location` from `working_memory["last_location"]`, executing Stage 2~4 restaurant search.
- User replies `"直接推薦"` or `"隨便"` on the next turn -> resolves `cuisine="熱門人氣美食"` and `location` from `working_memory["last_location"]`, executing Stage 2~4 restaurant search.
- User asks for an uncurated station (e.g. `"門前仲町特色美食"`) -> Tier 2 fetches Yahoo Japan grounding snippets and synthesizes `AreaSpecialtyGuide` via `LLMClient.call_structured_json`, falling back safely if network/LLM fails.
- User already specifies a concrete dish on Turn 1 (e.g. `"神保町車站 義大利麵"`) -> goes straight to `intent="search"` without interrupting with the cultural guide.

---

### Task 1: Curated Knowledge Base & `LocalSpecialtyGuideService` (Tier 1 + Tier 2 Grounding)

**Files:**
- Create: `config/local_specialties.json`
- Create: `src/services/cuisine_guide.py`
- Test: `tests/test_cuisine_guide.py`

**Interfaces:**
- Produces:
  - `SpecialtyItem(BaseModel)`: `index: int`, `cuisine_name: str`, `name_ja: str`, `history_and_culture: str`, `flavor_profile: str`
  - `AreaSpecialtyGuide(BaseModel)`: `area_name: str`, `intro: str`, `specialties: list[SpecialtyItem]`, `source_tier: Literal["curated", "grounding", "fallback"] = "curated"`
  - `LocalSpecialtyGuideService`:
    - `async def get_area_guide(self, area_name: str, llm_client: Optional[LLMClient] = None) -> AreaSpecialtyGuide`
    - `def render_guide_markdown(self, guide: AreaSpecialtyGuide) -> str`

- [ ] **Step 1: Write the failing test in `tests/test_cuisine_guide.py`**
- [ ] **Step 2: Run test to verify it fails (`pytest tests/test_cuisine_guide.py -v`)**
- [ ] **Step 3: Create `config/local_specialties.json` (covering 神保町, 淺草, 月島, 築地, 銀座, 上野, 新宿, 澀谷, 池袋, 橫濱中華街, 關內/橫濱, 京都祇園, 宇治, 大阪道頓堀/難波, 新世界, 博多/天神, 札幌) and implement `src/services/cuisine_guide.py`**
- [ ] **Step 4: Run test to verify it passes (`pytest tests/test_cuisine_guide.py -v`)**

---

### Task 2: Working Memory Extension & FSM Integration (`explore_cuisine` + Next-Turn Selection)

**Files:**
- Modify: `src/db/repositories/session.py`
- Modify: `src/agent/schemas.py`
- Modify: `src/agent/gate.py`
- Modify: `src/agent/stages/intent.py`
- Modify: `src/agent/pipeline.py`
- Test: `tests/test_cuisine_guide.py`

**Interfaces:**
- Consumes: `LocalSpecialtyGuideService` from `src/services/cuisine_guide.py`
- Produces:
  - `SessionRepository.update_working_memory(self, chat_id: int, last_location: Optional[str] = None, last_cuisine: Optional[str] = None, suggested_cuisines: Optional[list[str]] = None) -> dict[str, Any]`
  - `IntentOutput.intent`: `Literal["search", "more", "follow_up", "chitchat", "need_location", "explore_cuisine"]`
  - `QuickPassGate`: matches `"1" / "2" / "3" / "選1" / "第1種"` (`select_specialty_index`) and `"直接推薦" / "隨便"` (`direct_recommend`).

- [ ] **Step 1: Add integration tests in `tests/test_cuisine_guide.py` for Turn 1 `explore_cuisine` and Turn 2 `1` / `直接推薦` resolution**
- [ ] **Step 2: Run tests to verify they fail**
- [ ] **Step 3: Implement `SessionRepository.update_working_memory`, update `IntentOutput`, `QuickPassGate`, `execute_state_1_intent`, and `FoodRecommendationFSM.run_pipeline`**
- [ ] **Step 4: Run full test suite (`pytest`) and regenerate dashboard (`python scripts/run_eval.py`)**
- [ ] **Step 5: Update `CHANGELOG.md`, `README.md`, `docs/ARCHITECTURE.md`, and commit**
