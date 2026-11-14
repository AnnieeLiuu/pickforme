# Plan 2: EntityResolver Structured Output & Eval Guardrail Wiring

> **Status**: Completed & Verified (`ruff`, `mypy`, `pytest` all passed)  
> **Scope**: Bug/Review Items 6 & 7 (`src/services/entity_resolver.py`, `src/eval/evaluator.py`)  
> **Methodology**: `systematic-debugging` (Root-cause verified; subagent-reviewed)  
> **Design Principles**: 整體性 (Reuse existing `LLMClient.call_structured_json` & real `validator`/`synthesis` guardrails)、可維護性 (Single source of truth for JSON parsing & guardrail checks)、向後相容 (Preserve `gemini_api_key`, mockable `agent_engine`, and `eval_cases.json` compatibility)

---

## Item 6: `EntityResolver._verify_with_llm` 改用 `LLMClient.call_structured_json`

### 1. 根因
- `src/services/entity_resolver.py:593-602` 目前透過 `engine = self._get_engine()` 呼叫 `engine.call_llm_with_failover(messages)`，再手動用 Regex 剝除 ```` ```json ```` 並呼叫 `json.loads()`。
- 系統早已在 `src/agent/llm_client.py:253` 實作統一的 `LLMClient.call_structured_json(messages, schema=..., max_retries=...)`（內建 `response_format={"type": "json_object"}`、自動降級純文字解析、Markdown code block 抽取、Pydantic Schema 驗證與 Self-Correction 自癒重試）。
- `_verify_with_llm` 重造輪子導致：缺 Self-Correction 重試、型別驗證鬆散、且重複維護 JSON 解析邏輯。

### 2. 修改方案
1. **注入與存取 `LLMClient` (`src/services/entity_resolver.py`)**：
   - 使用 `TYPE_CHECKING` 匯入 `LLMClient`（避免循環依賴），`EntityResolver.__init__` 保留既有參數（`agent_engine`, `bridge`, `gemini_api_key: Optional[str] = None`）並新增可選參數 `llm_client: Optional[LLMClient] = None`。
   - 呼叫優先序：
     1. 若 `self._llm_client` 存在 ➡️ `await self._llm_client.call_structured_json(messages, schema=EntityVerifyResponse)`。
     2. 否則取得 `engine = self._get_engine()`：若 `getattr(engine, "llm_client", None)` 具備 `call_structured_json` ➡️ `await engine.llm_client.call_structured_json(messages, schema=EntityVerifyResponse)`。
     3. **測試相容降級**：若注入的是僅實作 `call_llm_with_failover` 的舊測試 Mock `agent_engine`，才降級呼叫並以 `EntityVerifyResponse.model_validate(...)` 解析。
2. **嚴格 Schema 驗證（防範 `call_structured_json` 意圖兜底字典穿透）**：
   - **關鍵邊界**：`LLMClient.call_structured_json`（`llm_client.py:320-341`）在重試皆失敗時，會回傳 intent parser 兜底字典 `{"intent": "chitchat" | "search", ...}` 而非拋錯或 `{}`。
   - 因此 `_verify_with_llm` **必須**在 `try ... except Exception` 內呼叫 `EntityVerifyResponse.model_validate(parsed_dict)`，確保當 `call_structured_json` 退回意圖兜底字典時會觸發 `ValidationError` 並安全 `return None`。
   - 若 `reason` 為空則補預設 `f"由 {model_used} 驗證完成"`。

---

## Item 7: `EvaluationEngine.evaluate_tier3_guardrail` 接上真實護欄管線

### 1. 根因
- `src/eval/evaluator.py:189-229` 目前硬寫死永遠合規的 `sample_card` 字串並做套套邏輯（Tautology）字串檢查，完全沒有呼叫真實的 `validate_card_tiered`、`sanitize_card_ratings`、`repair_store_name_code_blocks` 與 `repair_card_in_place`，導致護欄被改壞時 Eval 仍永遠顯示通過。

### 2. 修改方案
1. **分流 `follow_up` 規則與卡片護欄規則 (`src/eval/evaluator.py`)**：
   - **Follow-up 規則（`rules.get("require_target_store_context")`）**：針對 `eval_cases.json` 中的追問案例（如 `eval_006`, `eval_007`, `eval_017`，其 `expected` 無 `location` 欄位），明確檢查 `session_context.get("last_recommended_stores")` 是否有效且涵蓋 `expected["target_index"]`，不誤跑卡片合成檢查。
   - **多輪排除名單（`disallow_seen_place_ids`）**：保留現有 `seen_place_ids` 追蹤驗證。
2. **接線真實卡片護欄管線（`require_code_block` / `require_walking_distance` / `candidate_card`）**：
   - 匯入並呼叫生產主線函式：`render_template_fallback_card`（`src/agent/stages/synthesis.py:123`）、`sanitize_card_ratings`、`repair_store_name_code_blocks`、`validate_card_tiered`、`repair_card_in_place`（無循環依賴）。
   - **資料來源**：
     - 若 `case` 提供 `candidate_card`（與 `candidate_stores`）：直接對該卡片執行 `sanitize_card_ratings` / `repair_store_name_code_blocks` / `validate_card_tiered` / `repair_card_in_place`，並依 `expected_valid`（預設 `True`）驗證護欄是否正確放行或攔截（支援負面測試案例驗證護欄抓錯能力）。
     - 若為舊版 `eval_cases.json`（無 `candidate_card`）：從 `entity_match_sample` 與 `expected` 建構標準 `candidate_stores`（補齊 `walking_desc`、`rating`、`rating_source`、`payment_methods_desc` 等必要欄位，避免 `render_template_fallback_card` 因缺少欄位而被誤判缺步行距離），再交由真實 `render_template_fallback_card` → `sanitize_card_ratings` → `validate_card_tiered` 執行檢驗，確保 `tests/test_eval.py` 的 `>= 0.95` Gate 穩定且真實有效。

---

## 驗證與回歸測試計畫

1. **Item 6 測試**：
   - Mock `llm_client.call_structured_json` 回傳結構化 `EntityVerifyResponse` dict，驗證 `_verify_with_llm` 正確回傳 `EntityVerifyResponse` 且不走 `call_llm_with_failover`。
   - 測試 `call_structured_json` 回傳 intent fallback dict（`{"intent": "search", ...}`）或拋錯時，`_verify_with_llm` 安全回傳 `None`。
   - 測試僅注入舊式 mock `agent_engine`（無 `llm_client`）時仍能向後相容。
2. **Item 7 測試**：
   - 驗證 `candidate_card` 含未授權虛構店名、缺少步行距離、或竄改評分時，`evaluate_tier3_guardrail` 透過真實 `validate_card_tiered` / `sanitize_card_ratings` 精準攔截或驗證矯正結果。
   - 驗證現有 `eval_cases.json`（含 `follow_up` 與搜尋案例）通過 `tests/test_eval.py`。
3. **Pre-push Gate**：`python -m ruff format src/ tests/ && python -m ruff check src/ tests/ && python -m mypy src/ && pytest` 全數通過。
