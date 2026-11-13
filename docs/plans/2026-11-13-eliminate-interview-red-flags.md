# 📋 Eliminate Interview Red Flags & Quality Refactoring Plan

## 🎯 Objective
Directly resolve the 4 critical red flags identified in code review:
1. **Fix Fake Offline Evaluation in `src/eval/evaluator.py`**:
   - Eliminate `actual = expected` cheating shortcut in `live_mode=False`.
   - Implement Golden LLM Output Replay Cassettes: run actual `execute_state_1_intent` through mock LLM returning realistic raw model outputs, rigorously verifying QuickPassGate, Pydantic Schema Validation, Semantic Validator, and Working Memory.
2. **Harmonize Doc-Code Drift in `README.md` & `src/agent/llm_client.py`**:
   - Correct outdated file names in `README.md` (`guardrail.py` -> `validator.py`, `runner.py` -> `evaluator.py`).
   - Replace fictitious model names (`gemini-3.8-flash`, `qwen/qwen3.8-27b`) with real-world production models (`gemini-2.5-flash` / `gemini-2.0-flash`, `qwen/qwen-2.5-32b`, `llama-3.3-70b-versatile`).
3. **Add "Architecture Decision & Rational Trade-offs" Chapter to `README.md` & `docs/ARCHITECTURE.md`**:
   - Proactively address Over-engineering / Resume-driven development concerns.
   - Explain why pgvector halfvec + Parquet + DuckDB were adopted as an enterprise sandbox, and clearly delineate an "MVP Lean Mode" (what to cut vs what to keep).
4. **Clarify QuickPassGate Boundaries**:
   - Re-anchor QuickPassGate to high-confidence 0ms fast-replies and security tripwires, preventing regex creep into general NLU.

---

## 🛠️ Step-by-Step Implementation Tasks

### Task 1: Refactor `src/eval/evaluator.py` with Honest Offline Replay
- Create golden fixture dictionary or `tests/fixtures/eval_llm_cassettes.json` containing the raw JSON payloads that the micro-LLM outputs for each non-gate case in `eval_cases.json`.
- In `evaluator.py`:
  - When `live_mode=False`:
    - Inject a `MockLLMClient` into `self.fsm` that replays the exact golden JSON for that `case["id"]`.
    - Run `actual = await self.fsm.execute_state_1_intent(user_message=..., conversation_history=...)` for REAL!
    - This genuinely executes:
      1. `QuickPassGate().evaluate()`
      2. Context sanitization
      3. `IntentOutput.model_validate()`
      4. `validate_semantic_consistency()`
      5. `WorkingMemory` inheritance
      6. Coordinate parsing & ambiguity checks
  - Remove lines 78-87 that set `actual = expected`.

### Task 2: Fix Model Names & Directory Tree in `README.md` & `llm_client.py`
- In `src/agent/llm_client.py`:
  - Primary Groq fallback list: `["qwen/qwen-2.5-32b", "llama-3.3-70b-versatile", "llama-3.1-8b-instant"]`
  - Gemini model: `"gemini-2.5-flash"`
- In `README.md`:
  - Update model names.
  - Update Project Layout:
    - Change `runner.py` to `evaluator.py`.
    - Change `guardrail.py` to `validator.py` and describe its true role.
  - Fix test count badge if needed.

### Task 3: Author "Architecture Rationality & Trade-offs (架構決策與理性反思)"
- Add dedicated section in `README.md` and `docs/ARCHITECTURE.md`:
  - **FSM Pipeline vs ReAct Agent**: Deterministic latency & cost vs unpredictable agent loops.
  - **Why DuckDB + Parquet + pgvector**: Explain the Lakehouse sandbox motivation for agent telemetry archiving.
  - **MVP Lean Mode**: Contrast the enterprise sandbox with a lean MVP architecture (e.g., cutting DuckDB/Parquet in low-traffic scenarios).
  - **Tabelog Scraping vs Grounding Fallback**: Document the real-world 85% cloud WAF block rate, 800ms cache latency, and 4.5s grounding fallback.

### Task 4: Regression & Evaluation Verification
- Run `python scripts/run_eval.py` to verify the new honest offline replay passes 100% legitimately.
- Run `pytest tests/test_eval.py` (and full pytest suite).
- Pre-push 7 gates verification.

