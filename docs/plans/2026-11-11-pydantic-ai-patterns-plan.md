# PydanticAI 三大設計模式吸收與架構升級計畫 (PydanticAI Pattern Adoption Plan)

> **建立日期**：2026-11-11  
> **狀態**：🟢 已完成 (Completed & Verified)  
> **架構定位**：在不引入肥大第三方 `pydantic-ai` 套件（避免破壞既有 `Groq -> Gemini -> Ollama` 429 動態降級與 OTel Step 追蹤、維持 Cloud Run 極速冷啟動）的前提下，直接以原生 **Pydantic v2** 將 PydanticAI 最核心的三大設計模式融入 PickForMe 的確定性 4 階段 FSM。

---

## 🎯 目標與三大設計精華 (Three Core Patterns)

| 編號 | 借鏡自 PydanticAI 的設計模式 | 解決的痛點 | 目標實作位置 |
| :--- | :--- | :--- | :--- |
| **Pattern 1** | **Schema-Driven Prompt Generation**<br/>(`Field(description=...)` + 單一真相來源) | 避免在 [`src/agent/schemas.py`](file:///c:/Users/User/OneDrive/文件/2026/PickForMe/src/agent/schemas.py) 定義 `IntentOutput` 後，又要在 [`src/agent/stages/intent.py`](file:///c:/Users/User/OneDrive/文件/2026/PickForMe/src/agent/stages/intent.py) 手動維護一份重複的 JSON 結構文字，杜絕 Schema 與 Prompt 脫節 (Schema Drift)。 | [`src/agent/schemas.py`](file:///c:/Users/User/OneDrive/文件/2026/PickForMe/src/agent/schemas.py)<br/>[`src/agent/stages/intent.py`](file:///c:/Users/User/OneDrive/文件/2026/PickForMe/src/agent/stages/intent.py) |
| **Pattern 2** | **Semantic Result Validator**<br/>(`@model_validator(mode="after")` 跨欄位邏輯自癒) | 原有驗證僅檢查「單一欄位型別 (Syntax)」，未檢查「跨欄位業務矛盾 (Semantics)」。透過 `@model_validator(mode="after")` 自動清理空白字串、並在 `intent == "follow_up"` 卻遺漏 `question` 時自動觸發校正或拋出 `ValueError` 讓 `call_structured_json` 進行 Reflection 自癒重試。 | [`src/agent/schemas.py`](file:///c:/Users/User/OneDrive/文件/2026/PickForMe/src/agent/schemas.py) |
| **Pattern 3** | **Typed Dependency & State Container**<br/>(`FSMRunContext` 對應 `RunContext[Deps]`) | 將散落在 `FoodRecommendationFSM.run_pipeline` 的上下文狀態（`chat_id`、`user_message`、`user_location`、`working_memory`、`long_term_preferences`、`seen_place_ids`）封裝為強型別的 `FSMRunContext` 模型，提升狀態機可讀性與單元測試可測性。 | [`src/agent/schemas.py`](file:///c:/Users/User/OneDrive/文件/2026/PickForMe/src/agent/schemas.py)<br/>[`src/agent/pipeline.py`](file:///c:/Users/User/OneDrive/文件/2026/PickForMe/src/agent/pipeline.py) |

---

## 🛠️ 分步實作規格 (Implementation Steps)

### Task 1: `IntentOutput` 升級 — `Field` 語意描述、Schema Prompt 產生器與 `@model_validator`
**目標檔案**：[`src/agent/schemas.py`](file:///c:/Users/User/OneDrive/文件/2026/PickForMe/src/agent/schemas.py)

1. 為 [`IntentOutput`](file:///c:/Users/User/OneDrive/文件/2026/PickForMe/src/agent/schemas.py#L7-L18) 每個欄位補齊 `Field(description=...)`。
2. 新增 `@model_validator(mode="after")` 方法 `validate_semantic_consistency`：
   - 自動將 `location`、`cuisine`、`modifier`、`question` 的前後空白清除，若為空字串 `""` 則正規化為 `None`。
   - 若 `intent == "search"` 且未提供 `cuisine`，自動預設為 `"熱門人氣美食"`。
   - 若 `intent == "follow_up"` 且 `target_index` 與 `question` 皆為空，拋出 `ValueError("follow_up intent requires either target_index (1-3) or a specific question")`，觸發 LLM Reflection 重試。
3. 新增類別方法 `IntentOutput.build_json_schema_prompt() -> str`：
   - 自動從 `cls.model_json_schema()` 讀取欄位名稱、型別與 `description`，動態生成給 System Prompt 使用的結構規範區塊。

---

### Task 2: 定義 `FSMRunContext` 強型別狀態容器
**目標檔案**：[`src/agent/schemas.py`](file:///c:/Users/User/OneDrive/文件/2026/PickForMe/src/agent/schemas.py) 與 [`src/agent/pipeline.py`](file:///c:/Users/User/OneDrive/文件/2026/PickForMe/src/agent/pipeline.py)

1. 於 [`src/agent/schemas.py`](file:///c:/Users/User/OneDrive/文件/2026/PickForMe/src/agent/schemas.py) 新增 `FSMRunContext(BaseModel)`：
   ```python
   class FSMRunContext(BaseModel):
       """4 階段 FSM 執行期強型別上下文容器 (借鏡 PydanticAI RunContext)"""
       chat_id: Optional[int] = None
       user_message: str
       user_location: Optional[tuple[float, float]] = None
       intent: str = "search"
       location_name: Optional[str] = None
       coords: Optional[tuple[float, float]] = None
       cuisine: str = "熱門美食"
       modifier: Optional[str] = None
       exclude_place_ids: set[str] = Field(default_factory=set)
       long_term_preferences: dict[str, list[str]] = Field(default_factory=dict)
   ```
2. 於 [`src/agent/stages/intent.py`](file:///c:/Users/User/OneDrive/文件/2026/PickForMe/src/agent/stages/intent.py) 使用 `IntentOutput.build_json_schema_prompt()` 組裝 `INTENT_PARSER_SYSTEM_PROMPT`，達成 Single Source of Truth。
3. 於 [`src/agent/pipeline.py`](file:///c:/Users/User/OneDrive/文件/2026/PickForMe/src/agent/pipeline.py) 的 `run_pipeline` 中使用 `FSMRunContext` 收斂跨階段狀態傳遞（同時 100% 保持既有公開方法簽名向下相容）。

---

### Task 3: 單元測試與回歸驗證
**目標檔案**：[`tests/test_schemas_and_self_correction.py`](file:///c:/Users/User/OneDrive/文件/2026/PickForMe/tests/test_schemas_and_self_correction.py)

1. 測試 `IntentOutput.build_json_schema_prompt()` 能動態反映 `IntentOutput` 定義的所有欄位與描述。
2. 測試 `IntentOutput` 的 `@model_validator` 能自動清理空白字串、自動補齊預設 `cuisine`，並在 `follow_up` 邏輯矛盾時拋出 `ValidationError` 觸發 `LLMClient.call_structured_json` 自癒重試。
3. 測試 `FSMRunContext` 的建立與狀態收斂。
4. 執行完整 CI 守門檢查：
   - `python -m ruff format src/ tests/`
   - `python -m ruff check src/ tests/`
   - `python -m mypy src/`
   - `pytest`

