# Plan 4: Follow-up Output Validation, Tool Loop & Trip-Scoping

> **Status**: Completed & Verified (`ruff`, `mypy`, `pytest` all passed)  
> **Scope**: Bug/Review Item 5 (`execute_follow_up` 缺乏輸出驗證) + Architecture #2 (追問 Tool Loop) + Architecture #4 (`trip-scoping` 前期收斂對話)  
> **Design Principles**: 跨模型相容 (以 `call_structured_json` 驅動受限 Tool Loop，不依賴各家原生 function-calling 差異)、有界延遲 (最多 2 輪工具呼叫 + Fail-Safe 降級)、互補而非取代 (`generate_disambiguation_prompt` 保留給純廣域地名)

---

## Component 1: `execute_follow_up` 輸出驗證護欄 (Item 5)

### 1. 根因
- `src/agent/stages/intent.py:431-524` 中的多店比較分支與單店追問分支，在呼叫 `fsm.llm_client.call_text(messages)` 後**未經任何驗證**即直接回傳給 Telegram 使用者。
- 可能風險：空字串/敷衍回覆、生硬日文評論洩漏、偽造 Google Maps 搜尋連結、或竄改目標店家的 Tabelog / Google 評分數值。

### 2. 修改方案 (`validate_follow_up_response`)
- 在 `src/agent/validator.py` 新增 `validate_follow_up_response(response_text: str, stores: list[dict[str, Any]], extra_allowed_stores: Optional[list[dict[str, Any]]] = None) -> tuple[str, list[str]]`：
  1. **基本完整性**：禁止空回覆或異常過短回覆（不套用 State 4 的 8 欄卡片標記或反引號店名強制檢查）。
  2. **防偽造搜尋連結**：重用與 `validate_card_tiered` 一致的偽造連結檢測（禁止 `google.com/maps/search`、`maps.google.com/?q=` 等）。
  3. **防實質日文句子洩漏**：僅攔截括號/店名之外**連續 8 個字元以上**的日文假名段落，避免對單純料理專有名詞（如 `お通し`、`ラーメン`）誤殺。
  4. **單店／多店評分一致性**：
     - 多店比較（`len(stores) >= 2`）呼叫 `sanitize_card_ratings`。
     - 單店純文字追問（`len(stores) == 1`，往往無標題前綴或僅稱「這家店」）改呼叫共用正則提煉出的 `sanitize_single_store_ratings(text, stores[0])`，避免因缺乏標題前綴而被靜默跳過。
- 若驗證出現致命問題：自動重試 1 次或安全降級至確定性 `fallback_ans`（絕不回傳幻覺或壞掉的文字）。

---

## Component 2: 輕量追問 Tool Loop (`detail-lookup` ＆ `nearby-pairing`) (Arch #2)

### 1. 架構設計（`call_structured_json` 驅動，上限 2 輪）
因為 `LLMClient` 跨 Groq / Gemini / Ollama 運行，小模型對原生 OpenAI `tools` 參數相容性不一，採用 **`LLMClient.call_structured_json(..., schema=FollowUpDecision)`** 實作確定性受限迴圈：

```text
[Follow-up 進入]
  └─ Round 1: `call_structured_json(..., schema=FollowUpDecision)` + `FollowUpDecision.model_validate(...)` 驗證（防範 intent 兜底字典穿透，失敗則安全降級為 "answer"）
       ├─ "answer": 資訊足夠（如地址/價格/營業時間）➡️ 直接回答並通過 validate_follow_up_response()
       ├─ "detail_lookup": 需深查飲食禁忌/親子/環境細節 ➡️ 透過 Plan 3 SkillLoader 載入 `config/skills/scenarios/detail-lookup.md`
       │    └─ 若 `rid` 存在 ➡️ 查 `fsm.vector_repo.get_reviews_by_restaurant_id(UUID(str(rid)))`（含 Plan 1 的 `review_text_zh`），否則退回 `fsm.restaurant_repo` / `target_store` 快取評論
       └─ "nearby_pairing": 需順遊/續攤（咖啡/甜點/步行路線） ➡️ 透過 `SkillLoader` 載入 `config/skills/scenarios/nearby-pairing.md`
            └─ 檢查 `lat`/`lng` 為有效非零浮點數後，呼叫 `fsm.places_client.search_nearby(lat=..., lng=..., radius=400, ...)`
  └─ Round 2 (若 Round 1 有查工具): 彙整工具結果 + `SkillLoader.load_scenario("follow-up-qa")` 產出最終回覆 ➡️ validate_follow_up_response()
```

### 2. Fail-Safe 邊界
- **單次 Tool 超時 / 失敗**：所有 repo / places 工具呼叫皆包在 `asyncio.wait_for(..., timeout=8.0)` 與 `try/except` 內（相容離線測試 `MagicMock`），失敗時直接降級為「僅依現有 `target_store` 資料誠實回答（若無該資訊則明確告知未載明，嚴禁猜測）」。
- **重用 Plan 1 & Plan 3**：`detail_lookup` 直接受惠於 Plan 1 存入的 `review_text_zh`；新 Skill 檔（`detail-lookup.md`、`nearby-pairing.md`、`trip-scoping.md`）統一交由 Plan 3 的 `SkillLoader.load_scenario` 載入。

---

## Component 3: 複雜旅遊需求前期收斂 (`trip-scoping`) (Arch #4)

### 1. 與現有 `generate_disambiguation_prompt` 的分工與優先序
- **純廣域地名**（如「北海道拉麵」「沖繩美食」，無複合條件）：**繼續使用** `generate_disambiguation_prompt`（0ms、0 Token、1～3 個代表子商圈選項秒選）。
- **模糊複合需求**（例如「下週帶長輩去東京、不想排隊、想吃清淡午餐」且缺具體車站/預算）：
  - **路由優先序**（於 `intent.py:334-366` 明確定義）：當請求同時具備「複合限制條件（同行對象/飲食限制/預算）」且缺乏具體車站/街區時（優先於單純 `is_location_ambiguous` / `need_location`），透過 `SkillLoader.load_scenario("trip-scoping")` 進入 `trip-scoping` 分支。
  - **狀態持久化 (`SessionRepository.working_memory`)**：於 `SessionRepository.update_working_memory` 新增 `trip_scoping: Optional[dict[str, Any]] = None` 與 `clear_trip_scoping: bool = False`，並於 `update_recommended_ids` 搜尋完成後自動清除。
  - **防無限追問上限**：在 `working_memory` 追蹤 `scoping_turns <= 2`；一旦 `location` 具體化或達 2 輪上限，立即強制轉回標準 4 階段 FSM 執行最佳猜測搜尋。

---

## 驗證與回歸測試計畫

1. **Item 5 測試**：
   - 驗證 `execute_follow_up` 產出偽造搜尋連結或生硬日文洩漏時，會被 `validate_follow_up_response` 攔截並降級/修正。
2. **Tool Loop 測試（0ms Mock）**：
   - Mock `call_structured_json` 觸發 `detail_lookup`（命中向量庫中文評論）與 `nearby_pairing`（命中周邊座標搜尋），驗證工具最多呼叫上限與超時 Fail-Safe。
3. **Trip-Scoping 測試**：
   - 驗證純廣域地名仍走 `generate_disambiguation_prompt`；複合模糊需求載入 `trip-scoping.md` 並在收斂後正確轉交 FSM `search`。
4. **Pre-push Gate**：`python -m ruff format src/ tests/ && python -m ruff check src/ tests/ && python -m mypy src/ && pytest` 全數通過。
