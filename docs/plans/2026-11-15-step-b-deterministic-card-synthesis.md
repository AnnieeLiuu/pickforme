# Step B 實施計畫：Stage 4 LLM 精簡 JSON + Python 確定性組卡片與違規原因透傳

**日期**: 2026-11-15  
**狀態**: Subagent Review Approved（已納入 5 項測試相容性與邊界防護）  
**前置檢查點**: `1adb472` (Step A 完成)

---

## 1. 目標與範圍 (Scope)

徹底解決 `max_tokens: 800` 的長度拉扯與幻覺風險，並將上游確切狀態（違規原因、候選數不足原因）確定性透傳到使用者卡片：

1. **Structured Constraint Warnings (`src/agent/stages/enrichment/scoring.py`)**：
   - 保留 `store["constraint_violation"] = True`（維持 100% 向後相容）。
   - 新增 `store["constraint_warnings"]: list[dict[str, str]]`（或結構化清單），精確記錄每家店違反的項目與細節，包含：
     - `budget`：例如 `"略高於預算（￥1,000）"`
     - `payment`：例如 `"不符刷卡／行動支付需求"`
     - `hours`：例如 `"目前非營業時段／未明確支援指定時段"`
     - `dietary`：例如 `"店名或餐點含忌口關鍵字（牛／豬／生食／辣）"`
2. **Upstream Search Metadata (`src/agent/stages/places_search.py`, `src/agent/pipeline.py`)**：
   - 在 `pipeline.py` 記錄簡潔的 `search_meta`（例如 `stage2_candidate_count`、`stage3_enriched_count`、`top_count`），讓 Stage 4 在推薦不足 3 家時能確定性說明「搜尋半徑內僅找到 N 家高關聯店家」或「其餘店家於篩選/去重後未入選」，取代推測。
3. **LLM 短評 JSON + Python 確定性組裝 (`src/agent/stages/synthesis.py`, `config/skills/scenarios/card-synthesis.md`)**：
   - **LLM 輸出 Schema（約 250～350 tokens）**：
     ```json
     {
       "stores": [
         {
           "index": 1,
           "reason": "1～2 句老饕推薦理由（口感/湯頭/特色提煉）",
           "must_order": "3～6 字招牌菜色（若評論無具體菜名則必須為 null）"
         }
       ],
       "tip": "1 句在地實用小提醒"
     }
     ```
   - **Python 確定性組卡片**：
     - 100% 由 Python 模板拼裝：`rank_badge` + `` `name_ja` ``（一鍵複製）、`🚶 步行距離`、`⏰ 營業狀態`、`⭐️ 雙評分`、`💰 預算`（含 `budget` warning 標註）、`💳 支付方式`（含 `payment` warning 標註）、`📍 地址`、`🔗 連結`、`🍽️ 必點推薦`（僅當 `must_order` 非空時渲染）、`📍 老饕推薦理由`、`💡 特派員提醒`。
     - 若入選店家全部帶有 `constraint_warnings`，Python 直接在開頭加上明確提示行。
     - 若 `len(stores) < 3`，Python 直接依據 `search_meta` 在結尾提醒行說明家數不足原因。
   - **雙層 Fail-Open 保底**：
     - 若 LLM JSON 解析失敗、超時、或漏掉某個 `index`，Python 仍照常完整渲染所有店家的客觀欄位，缺漏的 `reason` 回退使用 `synthesize_store_recommendation_reason`（或無評論則略過）。
4. **Provider 動態 `max_tokens` (`src/agent/llm_client.py`)**：
   - `call_text` 新增選填參數 `max_tokens: int = 800`（Groq 維持 800 保護 1000 OTPM，Gemini fallback 視呼叫端需求放寬至 1500）。

---

## 2. 既有測試與文件同步盤點

1. **Mock `call_text` 的既有 Stage 4 測試**：
   - `tests/test_pipeline.py`、`tests/test_cuisine_guide.py`、`tests/test_skill_loader.py`、`tests/test_real_world_regressions.py`（如 Case 16、Case 44 等）目前對 `execute_state_4_synthesis` Mock 回傳 Markdown 字串。
   - 審查階段需盤點：哪些測試是在測 `execute_state_4_synthesis` 的卡片生成（應更新 Mock 回傳 JSON 或驗證新組卡行為），哪些是在測 `sanitize_card_ratings` / `validate_card_tiered` 護欄本身（應保留函式與護欄單元測試不遺失）。
2. **文件與 Observability 同步 (`AGENTS.md` 規範)**：
   - 確認 `config/skills/scenarios/card-synthesis.md` 與 `src/eval/reporter.py` / `public/index.html` 是否需要同步更新，並以 Playwright 實機驗收。

---

## 3. 驗證標準

- `python -m ruff format src/ tests/`
- `python -m ruff check src/ tests/`
- `python -m mypy src/`
- `pytest`（全數通過，包含新增 Step B 回歸測試 Case 52）
