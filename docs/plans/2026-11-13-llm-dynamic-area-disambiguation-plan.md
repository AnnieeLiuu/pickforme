# LLM Dynamic Area Disambiguation & Anti-Hardcoded Map Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eradicate the hardcoded `PREFECTURE_DISAMBIGUATION_MAP` dictionary in accordance with the `AGENTS.md` charter, upgrading both general food search (Stage 1) and `/prewarm` to use Micro-LLM world knowledge to dynamically detect broad/ambiguous locations (e.g., `靜岡溫泉街`, `北海道`, `長野滑雪`, `九州`) and provide structured sub-area choices with 0 extra network latency.

**Architecture:**
1. **Pydantic Schema & System Prompt Co-Evolution (`src/agent/schemas.py`, `src/agent/stages/intent.py`)**:
   - Define `SubAreaOption(name: str, desc: str = "", search_loc: Optional[str] = None)` with safe field defaults.
   - Extend `IntentOutput` with `is_location_ambiguous: bool = False`, `ambiguity_reason: Optional[str] = None`, and `suggested_sub_areas: list[SubAreaOption] = []`.
   - Update `INTENT_PARSER_SYSTEM_PROMPT` to teach the LLM to inspect `location`: if it is a prefecture, an entire island/region, or a broad concept without a single 800m center (e.g. `靜岡溫泉街`, `北海道`, `長野滑雪`, `九州`), mark `is_location_ambiguous=true` and return 2-3 specific sub-areas (with name, desc, and search_loc).
2. **Eradication of Hardcoded Mapping & Stage 1 Intent FSM Upgrade (`src/agent/stages/intent.py`)**:
   - Safely remove `PREFECTURE_DISAMBIGUATION_MAP`.
   - Update `execute_state_1_intent` to check `parsed.is_location_ambiguous` and `parsed.suggested_sub_areas`: if ambiguous with $\ge 2$ sub-areas, generate interactive disambiguation card and persist `pending_disambiguation` into `working_memory["suggested_locations"]`.
   - Keep a lightweight backwards-compatible `is_prefecture_level_area()` helper or bridge for existing callers.
3. **Decoupled AgentEngine Facade & Telegram Handler Alignment (`src/agent/core.py`, `src/bot/handlers.py`)**:
   - Add facade method `AgentEngine.disambiguate_area(area: str) -> tuple[bool, Optional[str], list[dict[str, str]]]` powered by Micro-LLM with a 1.5s timeout.
   - In `TelegramBotHandler.handle_update()`, when `/prewarm <area>` is invoked, call `self.agent_engine.disambiguate_area(area)` wrapped in `try...except` (fail-open).
   - If ambiguous with $\ge 2$ sub-areas, prompt the user with interactive sub-areas + `全部` + `取消`.
   - If unambiguous or LLM fails/times out, proceed directly with prewarm and background dispatch.
4. **Co-Evolution & Quality Gate (`docs/ARCHITECTURE.md`, `tests/test_docs_mermaid.py`, `tests/test_real_world_regressions.py`)**:
   - Update `docs/ARCHITECTURE.md` Section 3.D and `tests/test_docs_mermaid.py` keyword contracts.
   - Update golden regression test in `tests/test_real_world_regressions.py:1690-1725` for dynamic disambiguation.

**Tech Stack:** Python 3.11, Pydantic v2, Groq / Gemini Flash LLMClient, FastAPI, pytest.

**Spec / Requirements Source:** User Design Interview via `/grill-me` (2026-11-13) & Subagent Codebase Audit.

## Global Constraints

- **Anti-Hardcoded Mapping Charter**: Zero static dictionaries for prefecture-to-subarea mappings. All world knowledge must be resolved dynamically by the LLM.
- **0ms Offline Mock Tests**: All unit and regression tests must execute offline with Mock/AsyncMock, with zero network calls and zero live DB dependencies.
- **Zero Extra Latency in Search**: Ambiguity judgment occurs in the same inference pass as intent extraction (0 extra HTTP round-trips).
- **Fail-Open Policy**: If LLM times out or returns 0 options, the system must safely fall open to standard Places search without raising unhandled exceptions.
- **AGENTS.md Line Guardrail**: `AGENTS.md` length must strictly remain $\le 106$ physical lines (currently 88).
- **Pre-push Quality Gate**: Must pass `python -m ruff format src/ tests/ scripts/ && python -m ruff check src/ tests/ scripts/ && python -m mypy src/ && pytest` before any push.

## Review Focus

1. **Broad Concept Detection**: Verify that queries like `靜岡溫泉街`, `北海道`, `長野滑雪場`, and `九州` are dynamically flagged as ambiguous with 2-3 well-formed sub-areas without any hardcoded dictionary.
2. **Specific Landmark Exemption**: Verify that specific stations and local spots (e.g., `箱根湯本`, `熱海車站`, `新宿歌舞伎町`, `淺草雷門`) are marked `is_location_ambiguous=false` and pass through immediately.
3. **Dual-Path Telegram & Natural Language Flow**: Verify interactive options, selection by number (`1`, `2`, `3`), batch `全部`, cancellation (`取消`), and non-trapped fallthrough work seamlessly in both `/prewarm` and regular search.
4. **Fail-Open Behavior**: Verify that when LLM returns no sub-areas, raises an error, or times out, `/prewarm` safely falls open to standard Places search without raising exceptions.
5. **No Regressions in Existing Golden Test Suite**: Verify all 49 golden regression tests in `tests/test_real_world_regressions.py` and 206 total tests pass cleanly.

---

## Tasks

### Task 1: Extend `IntentOutput` Schema & Prompt for Dynamic Ambiguity Detection

**Files:**
- Modify: `src/agent/schemas.py:30-100`
- Modify: `src/agent/stages/intent.py:90-140`
- Test: `tests/test_dynamic_disambiguation_schema.py`

- [x] **Step 1: Write failing test for schema & prompt parsing**
  Create `tests/test_dynamic_disambiguation_schema.py` testing:
  - `SubAreaOption` model with fields `name: str`, `desc: str = ""`, `search_loc: Optional[str] = None`.
  - `IntentOutput` serialization & validation with `is_location_ambiguous: bool = False`, `ambiguity_reason: Optional[str] = None`, `suggested_sub_areas: list[SubAreaOption] = []`.
  - Mock LLM response parsing for `靜岡溫泉街` -> `is_location_ambiguous=True`, `suggested_sub_areas=[SubAreaOption(name="熱海溫泉街", desc="海景、溫泉布丁", search_loc="熱海溫泉"), SubAreaOption(name="修善寺溫泉街", desc="百年古湯、竹林", search_loc="修善寺")]`.
  - Mock LLM response parsing for `箱根湯本` -> `is_location_ambiguous=False`, `suggested_sub_areas=[]`.

- [x] **Step 2: Run test to confirm failure**
  Run `pytest tests/test_dynamic_disambiguation_schema.py` and confirm failure.

- [x] **Step 3: Update `IntentOutput` and `INTENT_PARSER_SYSTEM_PROMPT`**
  - In `src/agent/schemas.py`, define `SubAreaOption` and add ambiguity fields to `IntentOutput`.
  - In `src/agent/stages/intent.py`, update `INTENT_PARSER_SYSTEM_PROMPT` instructions to evaluate location breadth and populate `is_location_ambiguous` and `suggested_sub_areas`.

- [x] **Step 4: Verify test passes**
  Run `pytest tests/test_dynamic_disambiguation_schema.py` and verify 100% pass.

- [x] **Step 5: Commit changes**
  Commit with message: `feat(agent): add dynamic location ambiguity detection to IntentOutput schema`

---

### Task 2: Eradicate `PREFECTURE_DISAMBIGUATION_MAP` & Upgrade Stage 1 Intent FSM

**Files:**
- Modify: `src/agent/stages/intent.py:20-85, 300-360`
- Create: `tests/test_wide_area_disambiguation.py`

- [x] **Step 1: Write failing test for LLM-driven Stage 1 disambiguation**
  Create `tests/test_wide_area_disambiguation.py`:
  - Assert `PREFECTURE_DISAMBIGUATION_MAP` is no longer in `src/agent/stages/intent.py`.
  - Test `execute_state_1_intent`:
    - When `parsed.is_location_ambiguous=True` and `len(parsed.suggested_sub_areas) >= 2`:
      - Generates disambiguation guide card.
      - Sets `pipeline.early_response`.
      - Saves options in `working_memory["suggested_locations"]`.
    - When `parsed.is_location_ambiguous=False`:
      - Proceeds normally to Stage 2.
    - When fail-open (ambiguous but 0 options):
      - Proceeds normally to Stage 2 with original location.

- [x] **Step 2: Run test to confirm failure**
  Run `pytest tests/test_wide_area_disambiguation.py` and confirm failure.

- [x] **Step 3: Refactor `src/agent/stages/intent.py`**
  - Eradicate `PREFECTURE_DISAMBIGUATION_MAP` dictionary.
  - In `execute_state_1_intent`, inspect `parsed.is_location_ambiguous` and `parsed.suggested_sub_areas` to trigger interactive clarification.
  - Retain backwards-compatible `is_prefecture_level_area` helper function that checks `parsed.is_location_ambiguous` to avoid breaking external callers.

- [x] **Step 4: Verify test passes**
  Run `pytest tests/test_wide_area_disambiguation.py` and verify 100% pass.

- [x] **Step 5: Commit changes**
  Commit with message: `refactor(intent): eradicate hardcoded prefecture map in favor of dynamic llm disambiguation`

---

### Task 3: Decouple AgentEngine Facade & Align Telegram `/prewarm` Handler

**Files:**
- Modify: `src/agent/core.py:40-90`
- Modify: `src/bot/handlers.py:220-270`
- Modify: `tests/test_prewarm_wide_area.py`

- [x] **Step 1: Write failing test for AgentEngine facade & Telegram dynamic prewarm**
  In `tests/test_prewarm_wide_area.py`:
  - Test `agent_engine.disambiguate_area("靜岡溫泉街")` returns `(True, "靜岡溫泉街", [{"name": "熱海溫泉街", ...}, ...])`.
  - Test `/prewarm 靜岡溫泉街` in Telegram handler: triggers interactive menu with sub-areas.
  - Test follow-up selection `1` creates prewarm job for `熱海溫泉街` and dispatches background worker.
  - Test fail-open: if `agent_engine.disambiguate_area` raises an exception or times out, falls open to creating prewarm job for `靜岡溫泉街` directly without throwing error.

- [x] **Step 2: Run test to confirm failure**
  Run `pytest tests/test_prewarm_wide_area.py` and confirm failure.

- [x] **Step 3: Implement facade and update Telegram handler**
  - In `src/agent/core.py`, add `async def disambiguate_area(self, area: str) -> tuple[bool, Optional[str], list[dict[str, str]]]:` invoking `llm_client.call_structured_json` with a lightweight disambiguation prompt and 2s timeout.
  - In `src/bot/handlers.py` (`handle_update`):
    - Under `/prewarm`, wrap `await self.agent_engine.disambiguate_area(area)` in a `try...except` block.
    - If `is_ambiguous` and options $\ge 2$: save `pending_prewarm_options` and reply with interactive prompt.
    - Else: directly create job and call `_dispatch_background_prewarm(count=1)`.

- [x] **Step 4: Verify test passes**
  Run `pytest tests/test_prewarm_wide_area.py` and verify 100% pass.

- [x] **Step 5: Commit changes**
  Commit with message: `feat(bot): integrate dynamic llm ambiguity resolution into prewarm handler via agent engine`

---

### Task 4: Documentation, Regression Mandate & CI Quality Gate

**Files:**
- Modify: `docs/ARCHITECTURE.md`
- Modify: `CHANGELOG.md`
- Modify: `tests/test_docs_mermaid.py`
- Modify: `tests/test_real_world_regressions.py:1690-1730`

- [x] **Step 1: Update golden regression test & Mermaid test**
  - In `tests/test_real_world_regressions.py:1690-1730`, update `test_regression_prefecture_disambiguation_case_1_shizuoka_guidance` to supply dynamic `is_location_ambiguous=True` in structured JSON mock.
  - Add new regression test `test_regression_llm_dynamic_area_disambiguation_shizuoka_onsen`.
  - In `tests/test_docs_mermaid.py`, ensure diagram keyword assertions reflect dynamic ambiguity check (`is_location_ambiguous`).

- [x] **Step 2: Documentation co-evolution**
  - Update `docs/ARCHITECTURE.md` Section 3.D, removing static `PREFECTURE_DISAMBIGUATION_MAP` and documenting the LLM-driven dynamic disambiguation resolver.
  - Add unreleased entry in `CHANGELOG.md`.
  - Confirm `AGENTS.md` strictly $\le 106$ lines.

- [x] **Step 3: Run full pre-push test and quality gate**
  Run `python -m ruff format src/ tests/ scripts/ && python -m ruff check src/ tests/ scripts/ && python -m mypy src/ && pytest`.

- [x] **Step 4: Commit and push**
  Commit and push to remote (`main -> main`).
