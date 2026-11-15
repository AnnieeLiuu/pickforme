# 純淨日文搜尋詞 (`search_keyword_ja`) 與預算軟性重排防空計畫

**日期**: 2026-11-15  
**狀態**: Completed & Verified (`ruff`, `mypy`, `pytest` 263 passed)  
**背景與根因**：
在生產追蹤 `211f181a-37fc-46f6-8172-5684f92ee249`（「藏前 咖啡 200台幣以下」）中觀察到以下問題：
1. `state_1_intent` 已將使用者語句拆為 `cuisine="咖啡"` 與 `modifier="200台幣以下"`，但 `resolve_search_constraints()` 又把 `modifier` 拼回 `clean_keyword`（變成 `"咖啡 200台幣以下"`）。
2. 送去 Google Places TextSearch (`languageCode="ja"`) 與 Tabelog 獵店 (`discover_area_top_stores`) 的搜尋詞夾帶 `"200台幣以下"`，導致 Tabelog 種子 0 家、Google Places 回傳 6 家大多是飯店與漢堡排，最後只剩 1 家旅館附設酒廊 `Nui.HOSTEL & BAR LOUNGE`。
3. 與其用易漏的 regex 逐一剝離各種預算/風格/氛圍詞，應從根本釐清職責：
   - **搜尋詞 (`search_keyword`)**：只傳送純淨的日文店種/料理核心詞（由 `IntentParser` 輸出 `search_keyword_ja`，若缺漏則退回純淨 `cuisine`），絕不拼接 `modifier`。
   - **條件詞 (`modifier` / `budget_max_jpy`)**：距離詞控制 `radii_ladder`；預算與風格條件留給 Stage 3 軟性加權重排與 Stage 4 卡片生成篩選，並確保無完全符合者時不篩成 0 家。

---

## 變更設計

### 1. Stage 1: `IntentParser` 結構化輸出 `search_keyword_ja` 與 `budget_max_jpy`
- **檔案**: `src/agent/schemas.py`, `config/prompts/intent_parser.md`
  - `IntentOutput` 新增：
    - `search_keyword_ja: Optional[str]`: 純淨日文核心搜尋詞，專供 Google Places (`languageCode="ja"`) 與 Tabelog 檢索使用（如「咖啡」→ `"カフェ"`、「拉麵」→ `"ラーメン"`、「壽喜燒」→ `"すき焼き"`）；嚴禁包含地名、預算、距離或風格修飾語。
    - `budget_max_jpy: Optional[int]`: 若使用者明確指定預算上限（含台幣約 1:5 換算為日圓，例如「200台幣以下」→ `1000`、「3000日幣內」→ `3000`、「平價」若無具體數字則設 `null` 交由語意處理），供 Stage 3 軟性排序使用。
  - 更新 `config/prompts/intent_parser.md`（若 `more` 繼承上一輪 `cuisine`，也一併繼承 `search_keyword_ja`）。

### 2. Stage 2: 純淨搜尋詞（徹底不拼 `modifier`）
- **檔案**: `src/agent/stages/places_search.py`, `src/agent/pipeline.py`, `src/agent/schemas.py` (`FSMRunContext`)
  - `resolve_search_constraints(cuisine, modifier=None, search_keyword_ja=None)`:
    - 距離詞依然解析為 `radii_ladder`。
    - `style_tag` 保留非距離的修飾語（如 `"平價一點"`、`"我想找更在地老店"`、`"200台幣以下"`）供 Stage 3/4 使用。
    - **`clean_keyword` 優先採用純淨的 `search_keyword_ja`（否則用剝除空間詞後的 `clean_cuisine`），嚴禁再將 `rem_style` / `modifier` 拼進 `clean_keyword`**。
  - `pipeline.py`:
    - `execute_state_2_places_ladder` 使用 `run_ctx.search_keyword`（如 `"カフェ"`）。
    - `execute_state_4_synthesis` 與 `no_cand_msg` 維持傳入使用者語言的 `run_ctx.cuisine`（如 `"咖啡"`），避免卡片標題變成「精選『カフェ』」而影響繁中自然度。
    - `working_memory` 一併保存 `last_search_keyword_ja`（若有）與 `last_cuisine: cuisine`，確保 `intent="more"` 換一批時能正確沿用。

### 3. Stage 3 & Stage 4: 預算軟性加權重排 + 防篩空保底
- **檔案**: `src/agent/stages/enrichment.py`, `src/agent/pipeline.py`
  - `apply_deterministic_constraints` 新增 `budget_max_jpy: Optional[int] = None`：
    - 解析店家 `price_lunch` / `price_dinner` 中的最低價位數字（如 `"～￥999"` → `999`、`"￥1,000～￥1,999"` → `1000`、`"￥4,000～￥4,999"` → `4000`）。
    - 若店家**明確有價位**且最低價位 `> budget_max_jpy`，標記 `constraint_violation = True`（`-0.45` 軟性扣分），**絕不直接刪除店家**；若價位為 `None` 則保護不扣分。
  - 確保在 `rank_and_select_top_stores(..., top_k=3)` 挑選 Top 3 **之前**（或重新重排全部候選時），預算與硬性條件已經先參與評分，避免低價符合預算的店家排在 #4～#6 被切掉。
- **檔案**: `src/agent/stages/synthesis.py` / `config/skills/scenarios/card-synthesis.md`
  - 若 `modifier` 或 `budget_max_jpy` 存在，於 `synthesis` 提示詞注入軟性篩選與保底原則：
    - 優先推薦符合條件的店家；
    - **嚴禁把所有候選淘汰成 0 家**：若 Top 3 中沒有完全符合預算／風格的店家，必須誠實說明差距並呈現最接近條件的 1～3 家。

### 4. 回歸測試同步
- **檔案**: `tests/test_real_world_regressions.py`
  - 更新 `test_case13`：驗證 `resolve_search_constraints` 不再將風格/預算詞（`"老店"`、`"平價一點"`）混入 `kw`，而是保留在 `style_tag`。
  - 新增 **Case 49 (`test_case_49_pure_ja_search_keyword_and_budget_soft_ranking`)**：
    - 驗證 `"藏前 咖啡 200台幣以下"` 在 `resolve_search_constraints` 產出純淨 `"カフェ"`（不含 `"200台幣以下"`）。
    - 驗證 `apply_deterministic_constraints(..., budget_max_jpy=1000)` 對 `￥4,000～￥4,999` 標記軟性扣分、讓 `～￥999` 咖啡店勝出排進 Top 3，且即使全部超預算時也保留候選不會變 0 家。

---

## 驗證方式
```bash
python -m ruff format src/ tests/ && python -m ruff check src/ tests/ && python -m mypy src/ && pytest
```
