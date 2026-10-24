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

- [x] **Step 1: Write regression test for template loading and BASE_DASHBOARD_TEMPLATE constant**
- [x] **Step 2: Run test to verify current state**
- [x] **Step 3: Extract template to `src/eval/templates/river_dashboard.html` and update `reporter.py`**
- [x] **Step 4: Run evaluation tests and compile dashboard**
- [x] **Step 5: Commit Phase 1**

---

### Task 2: Create `src/agent/stages/` Package and Extract State 1 (`intent.py`)

- [x] **Step 1: Write unit test in `tests/test_pipeline.py` targeting Stage 1 execution**
- [x] **Step 2: Implement `src/agent/stages/intent.py`**
- [x] **Step 3: Update `src/agent/pipeline.py` to delegate State 1 to `intent.py`**
- [x] **Step 4: Run pipeline tests**
- [x] **Step 5: Commit Stage 1 extraction**

---

### Task 3: Extract State 2 (`places_search.py`)

- [x] **Step 1: Verify State 2 ladder tests**
- [x] **Step 2: Implement `src/agent/stages/places_search.py`**
- [x] **Step 3: Update `src/agent/pipeline.py` to delegate State 2 & re-export `resolve_search_constraints`**
- [x] **Step 4: Run test suite for State 2**
- [x] **Step 5: Commit Stage 2 extraction**

---

### Task 4: Extract State 3 (`enrichment.py`)

- [x] **Step 1: Verify State 3 tests**
- [x] **Step 2: Implement `src/agent/stages/enrichment.py`**
- [x] **Step 3: Update `src/agent/pipeline.py` to delegate State 3 & helper methods**
- [x] **Step 4: Run test suite for State 3**
- [x] **Step 5: Commit Stage 3 extraction**

---

### Task 5: Extract State 4 (`synthesis.py`)

- [x] **Step 1: Verify State 4 invariant tests**
- [x] **Step 2: Implement `src/agent/stages/synthesis.py`**
- [x] **Step 3: Update `src/agent/pipeline.py` to delegate State 4 & re-export constants/helpers**
- [x] **Step 4: Run test suite for State 4 & regressions**
- [x] **Step 5: Commit Stage 4 extraction**

---

### Task 6: Full Regression Verification & Pre-push Quality Gates

- [x] **Step 1: Verify file line counts and modularity**
- [x] **Step 2: Run Ruff format check**
- [x] **Step 3: Run Ruff lint check**
- [x] **Step 4: Run Mypy static type check**
- [x] **Step 5: Run full Pytest regression suite**
- [x] **Step 6: Update documentation and sync architecture blueprint**
- [x] **Step 7: Final Commit & push**
