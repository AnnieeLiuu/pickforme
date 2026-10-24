# God Files Decoupling & Modularization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decouple the two major God Files (`src/eval/reporter.py` 2,380 lines and `src/agent/pipeline.py` 1,422 lines) into focused, single-responsibility modules while maintaining 100% backward compatibility for all 130 tests and public APIs via the Facade pattern.

**Architecture:** Phase 1 extracts 2,200+ lines of raw HTML/CSS/JS template from `src/eval/reporter.py` into `src/eval/templates/river_dashboard.html`. Phase 2 decomposes the monolithic 4-stage FSM in `src/agent/pipeline.py` into dedicated modules in `src/agent/stages/` (`intent.py`, `places_search.py`, `enrichment.py`, `synthesis.py`), leaving `pipeline.py` as a lightweight Facade Orchestrator.

**Tech Stack:** Python 3.12, FastAPI, Pytest, Ruff, Mypy, Jinja2/string replacement, AsyncIO.

**Spec:** `docs/plans/god_files_decoupling_design.md`

## Global Constraints

- Preserve 100% backward compatibility for existing imports (`FoodRecommendationFSM`, `resolve_search_constraints`, `format_payment_methods`, `get_recommendation_card_prompt`, `AGENT_SYSTEM_PROMPT_PATH`, `generate_html_dashboard`, `generate_markdown_report`, `BASE_DASHBOARD_TEMPLATE`).
- Zero circular imports between `src/agent/pipeline.py` and `src/agent/stages/`.
- Strict 0ms offline mock test policy: no live network calls or LLM API spend in test suite.
- Maintain 100% CI pass rate: Ruff format, Ruff check, Mypy type check, and Pytest (all 130 tests).

## Review Focus

1. **Missing template file fallback**: If `river_dashboard.html` is missing or package is bundled into an egg/wheel, `reporter.py` must fail with an informative `FileNotFoundError`.
2. **Facade monkeypatching compatibility**: Tests that patch methods on `FoodRecommendationFSM` (e.g. `mock.patch.object(fsm, 'execute_state_1_intent')`) must continue to intercept calls during `run_pipeline`.
3. **Symbol re-export completeness**: All utility functions historically exported from `src.agent.pipeline` must remain importable directly from `src.agent.pipeline`.
4. **State cross-dependencies**: Stage modules must not import `FoodRecommendationFSM` directly; type hints should use `Any` or `Protocol` or `from __future__ import annotations` with `TYPE_CHECKING` to avoid circular import loops.
5. **Encoding integrity**: Template loading and HTML dashboard emission must strictly specify `encoding="utf-8"` on all platforms (Windows/Linux/macOS).

---

### Task 1: Decouple `src/eval/reporter.py` by Extracting HTML Template

**Files:**
- Create: `src/eval/templates/river_dashboard.html`
- Modify: `src/eval/reporter.py`
- Test: `tests/test_observability_sync.py`, `tests/test_eval.py`

**Interfaces:**
- Consumes: `summary: EvalSummary`, `output_path: Path`
- Produces: `generate_markdown_report() -> str`, `generate_html_dashboard() -> Path`, `BASE_DASHBOARD_TEMPLATE: str`

- [ ] **Step 1: Write regression test for template loading and BASE_DASHBOARD_TEMPLATE constant**

Add a test in `tests/test_eval.py` asserting `BASE_DASHBOARD_TEMPLATE` is a non-empty string starting with `<style>` and contains the core dashboard layout tags.

- [ ] **Step 2: Run test to verify current state**

Run: `pytest tests/test_eval.py -v`
Expected: PASS

- [ ] **Step 3: Extract template to `src/eval/templates/river_dashboard.html` and update `reporter.py`**

1. Move the 2,200+ line HTML/CSS/JS template string into `src/eval/templates/river_dashboard.html`.
2. In `src/eval/reporter.py`, implement helper `_load_template() -> str`:
   ```python
   TEMPLATE_PATH = Path(__file__).parent / "templates" / "river_dashboard.html"


   def _load_template() -> str:
     if not TEMPLATE_PATH.exists():
       raise FileNotFoundError(
           f"Observability dashboard template not found: {TEMPLATE_PATH}"
       )
     return TEMPLATE_PATH.read_text(encoding="utf-8")


   BASE_DASHBOARD_TEMPLATE = _load_template()
```
3. Verify `reporter.py` is reduced from 2,380 lines to < 100 lines.

- [ ] **Step 4: Run evaluation tests and compile dashboard**

Run: `pytest tests/test_eval.py tests/test_observability_sync.py -v`
Run: `python scripts/run_eval.py`
Expected: PASS with 100% compatibility.

- [ ] **Step 5: Commit Phase 1**

```bash
git add src/eval/ templates/ tests/test_eval.py
git commit -m "refactor(eval): decouple river dashboard HTML template from reporter.py"
```

---

### Task 2: Create `src/agent/stages/` Package and Extract State 1 (`intent.py`)

**Files:**
- Create: `src/agent/stages/__init__.py`
- Create: `src/agent/stages/intent.py`
- Modify: `src/agent/pipeline.py`
- Test: `tests/test_pipeline.py`, `tests/test_multi_turn.py`

**Interfaces:**
- Consumes: `fsm: Any`, `user_message: str`, `chat_history: list[dict[str, str]]`, `system_prompt: str`
- Produces: `IntentStageHandler.execute_state_1_intent()`, `IntentStageHandler.execute_follow_up()`

- [ ] **Step 1: Write unit test in `tests/test_pipeline.py` targeting Stage 1 execution**

Verify `execute_state_1_intent` parses intent correctly and handles empty/follow-up queries deterministically.

- [ ] **Step 2: Implement `src/agent/stages/intent.py`**

Extract `_sanitize_history_for_intent`, `execute_state_1_intent`, and `execute_follow_up` into `src/agent/stages/intent.py`. Use `TYPE_CHECKING` for type annotations to avoid circular imports.

- [ ] **Step 3: Update `src/agent/pipeline.py` to delegate State 1 to `intent.py`**

In `FoodRecommendationFSM`:
```python
async def execute_state_1_intent(self, user_message: str, chat_history: list[dict[str, str]], system_prompt: str) -> dict[str, Any]:
    return await execute_state_1_intent(self, user_message, chat_history, system_prompt)

async def execute_follow_up(self, user_message: str, candidate_stores: list[dict[str, Any]], session_context: dict[str, Any]) -> dict[str, Any]:
    return await execute_follow_up(self, user_message, candidate_stores, session_context)
```

- [ ] **Step 4: Run pipeline tests**

Run: `pytest tests/test_pipeline.py tests/test_multi_turn.py -v`
Expected: PASS

- [ ] **Step 5: Commit Stage 1 extraction**

```bash
git add src/agent/stages/ src/agent/pipeline.py
git commit -m "refactor(agent): extract State 1 intent and follow-up handlers to stages/intent.py"
```

---

### Task 3: Extract State 2 (`places_search.py`)

**Files:**
- Create: `src/agent/stages/places_search.py`
- Modify: `src/agent/pipeline.py`
- Test: `tests/test_pipeline.py`, `tests/test_prewarm.py`

**Interfaces:**
- Consumes: `fsm: Any`, `intent_data: dict[str, Any]`, `user_id: int | str`
- Produces: `execute_state_2_places_ladder()`, `_prefilter_potential_foodie_places()`, `resolve_search_constraints()`

- [ ] **Step 1: Verify State 2 ladder tests**

Run: `pytest tests/test_pipeline.py -k "state_2 or ladder or search" -v`
Expected: PASS

- [ ] **Step 2: Implement `src/agent/stages/places_search.py`**

Extract:
1. `resolve_search_constraints(query: str, budget_yen: Optional[int]) -> dict[str, Any]`
2. `_prefilter_potential_foodie_places(places: list[dict[str, Any]], min_rating: float) -> list[dict[str, Any]]`
3. `execute_state_2_places_ladder(fsm: Any, intent_data: dict[str, Any], user_id: int | str = 0) -> list[dict[str, Any]]`

- [ ] **Step 3: Update `src/agent/pipeline.py` to delegate State 2 & re-export `resolve_search_constraints`**

Ensure `from src.agent.pipeline import resolve_search_constraints` continues to work seamlessly.

- [ ] **Step 4: Run test suite for State 2**

Run: `pytest tests/test_pipeline.py tests/test_real_world_regressions.py -k "ladder or rescue or search" -v`
Expected: PASS

- [ ] **Step 5: Commit Stage 2 extraction**

```bash
git add src/agent/stages/places_search.py src/agent/pipeline.py
git commit -m "refactor(agent): extract State 2 places ladder search to stages/places_search.py"
```

---

### Task 4: Extract State 3 (`enrichment.py`)

**Files:**
- Create: `src/agent/stages/enrichment.py`
- Modify: `src/agent/pipeline.py`
- Test: `tests/test_pipeline.py`, `tests/test_foodie_reviews.py`, `tests/test_vector.py`

**Interfaces:**
- Consumes: `fsm: Any`, `places: list[dict[str, Any]]`, `intent_data: dict[str, Any]`
- Produces: `execute_state_3_parallel_tabelog()`, `_enrich_single_place()`, `_index_store_reviews()`, `semantic_rerank_reviews()`, `_calculate_foodie_score()`, `rank_and_select_top_stores()`

- [ ] **Step 1: Verify State 3 tests**

Run: `pytest tests/test_foodie_reviews.py tests/test_vector.py -v`
Expected: PASS

- [ ] **Step 2: Implement `src/agent/stages/enrichment.py`**

Extract Tabelog parallel scraping, entity resolution invocation, vector embedding review indexing, semantic rerank, foodie scoring formula (`_calculate_foodie_score`), and top-store ranking logic (`rank_and_select_top_stores`).

- [ ] **Step 3: Update `src/agent/pipeline.py` to delegate State 3 & helper methods**

Retain methods on `FoodRecommendationFSM` for compatibility:
- `_calculate_foodie_score`
- `rank_and_select_top_stores`
- `execute_state_3_parallel_tabelog`

- [ ] **Step 4: Run test suite for State 3**

Run: `pytest tests/test_pipeline.py tests/test_foodie_reviews.py tests/test_vector.py -v`
Expected: PASS

- [ ] **Step 5: Commit Stage 3 extraction**

```bash
git add src/agent/stages/enrichment.py src/agent/pipeline.py
git commit -m "refactor(agent): extract State 3 Tabelog enrichment and foodie ranking to stages/enrichment.py"
```

---

### Task 5: Extract State 4 (`synthesis.py`)

**Files:**
- Create: `src/agent/stages/synthesis.py`
- Modify: `src/agent/pipeline.py`
- Test: `tests/test_pipeline.py`, `tests/test_invariants.py`, `tests/test_real_world_regressions.py`

**Interfaces:**
- Consumes: `fsm: Any`, `candidate_stores: list[dict[str, Any]]`, `intent_data: dict[str, Any]`, `system_prompt: str`
- Produces: `execute_state_4_synthesis()`, `_render_template_fallback_card()`, `_sanitize_card_ratings()`, `format_payment_methods()`, `get_recommendation_card_prompt()`, `AGENT_SYSTEM_PROMPT_PATH`

- [ ] **Step 1: Verify State 4 invariant tests**

Run: `pytest tests/test_invariants.py -v`
Expected: PASS

- [ ] **Step 2: Implement `src/agent/stages/synthesis.py`**

Extract prompt formatting, payment method rendering, Telegram Markdown template fallback card generation, and rating sanitization.

- [ ] **Step 3: Update `src/agent/pipeline.py` to delegate State 4 & re-export constants/helpers**

Re-export from `src/agent/pipeline.py`:
- `get_recommendation_card_prompt`
- `format_payment_methods`
- `AGENT_SYSTEM_PROMPT_PATH`

- [ ] **Step 4: Run test suite for State 4 & regressions**

Run: `pytest tests/test_invariants.py tests/test_real_world_regressions.py -v`
Expected: PASS

- [ ] **Step 5: Commit Stage 4 extraction**

```bash
git add src/agent/stages/synthesis.py src/agent/pipeline.py
git commit -m "refactor(agent): extract State 4 card synthesis and formatting to stages/synthesis.py"
```

---

### Task 6: Full Regression Verification & Pre-push Quality Gates

**Files:**
- Entire codebase

- [ ] **Step 1: Verify file line counts and modularity**

Run PowerShell script:
```powershell
Get-ChildItem -Path src -Recurse -File -Filter *.py | ForEach-Object { [PSCustomObject]@{ Path = $_.FullName.Replace((Get-Location).Path + "\", ""); Lines = (Get-Content $_.FullName | Measure-Object -Line).Lines } } | Sort-Object -Property Lines -Descending | Select-Object -First 10
```
Expected:
- `src/eval/reporter.py` < 100 lines (down from 2,380)
- `src/agent/pipeline.py` < 300 lines (down from 1,422)
- All individual stage files < 400 lines each.

- [ ] **Step 2: Run Ruff format check**

Run: `python -m ruff format --check src/ tests/`
Expected: Clean with 0 files reformatted.

- [ ] **Step 3: Run Ruff lint check**

Run: `python -m ruff check src/ tests/`
Expected: Clean with 0 errors.

- [ ] **Step 4: Run Mypy static type check**

Run: `python -m mypy src/`
Expected: Success with no type errors.

- [ ] **Step 5: Run full Pytest regression suite**

Run: `pytest`
Expected: 130 passed, 0 failed, 0 warnings.

- [ ] **Step 6: Update documentation and sync architecture blueprint**

Update `docs/ARCHITECTURE.md` to reflect the new `src/agent/stages/` package and `src/eval/templates/` directory.

- [ ] **Step 7: Final Commit & push**

```bash
git add .
git commit -m "docs: sync architecture blueprint with modularized agent stages and template decoupling"
git push origin main
```
