# Step A 實施計畫：Stage 2 首頁全收三級預選 ＆ Stage 3 全查 Semaphore 並發

**日期**: 2026-11-15  
**狀態**: Subagent Review Approved（已納入 5 項審查修正）  
**前置檢查點**: `0738f9c` (`src/agent/stages/enrichment/` 模組化拆分完成)

---

## 1. 目標與範圍 (Scope)

落實架構路線圖的 **Step A**，解決「Stage 2 上游截斷漏店」與「Stage 3 下游 Tier 早停丟棄候選店」的結構性衝突：

1. **Stage 2 (`src/services/tabelog_scraper.py`, `src/agent/stages/places_search.py`, `src/agent/pipeline.py`)**：
   - 取消 `_fetch_and_parse_grounding` 的 `if len(candidates) >= 5: break` 硬上限，自然全收 Yahoo Japan 第 1 頁解析結果（約 8～10 家，零額外 HTTP 請求）。
   - `discover_area_top_stores` 支援選填條件（`budget_max_jpy`、`preferences`），採 **三級優先權（Fail-Open 疑罪從無）** 排序後挑選高潛力種子（預設 `max_seeds=4`）：
     - **Tier A（明確符合）**：摘要明確標註價格下限 `<= budget_max_jpy`（或明確支援所需支付方式如 `supports_paypay` / `credit_cards`）→ 最高優先權，優先反查 Google 座標。
     - **Tier B（未知 / `None`）**：摘要因字數限制未帶出價格或支付資訊（`None`）→ **疑罪從無、中立保留**，依 Tabelog 評分與百名店加權排序，排在 Tier A 之後，交由 Stage 3 查證。
     - **Tier C（明確超標 / 違規）**：摘要明確超過 `budget_max_jpy`（或明確不符條件）→ **大幅降權墊底**（不直接 `drop()`，保留極端情況保底名單）。
   - `_hydrate_tabelog_seeds_with_places` 使用 `asyncio.Semaphore(4)` 限制併發呼叫 `search_by_text`，並相容既有不帶新參數的 mock `discover_fn`。
2. **Stage 3 (`src/agent/stages/enrichment/orchestrator.py`, `__init__.py`)**：
   - 廢除 Tier 1（前 4 家）/ Tier 2（後 2 家）早停丟棄分支，改為將 `places[:6]` **全部並發查驗**。
   - 使用 `asyncio.Semaphore(4)` 控制 `fsm._enrich_single_place` 的同時請求上限，兼顧延遲與 Tabelog 403 風控防護。
   - 移除 `should_trigger_tier2` 與 `count_high_score_stores`（並同步更新 `__init__.py` 匯出與測試）。

---

## 2. 跨模組接線與相容性設計

### 2.1 向後相容的參數傳遞
- `FoodRecommendationFSM.execute_state_2_places_ladder` 與 `execute_state_2_places_ladder` 新增可選參數：
  - `budget_max_jpy: Optional[int] = None`
  - `preferences: Optional[dict[str, Any]] = None`
- `pipeline.py`（State 2 呼叫處）傳入 `budget_max_jpy=run_ctx.budget_max_jpy` 與 `preferences=run_ctx.long_term_preferences`。
- `_hydrate_tabelog_seeds_with_places` 呼叫 `discover_fn` 時：
  - 既有單元測試常使用簡易 lambda 或固定簽名的 fake `discover_area_top_stores(area_name, cuisine, max_seeds=3)`。
  - 因此以 `try/except (ValueError, TypeError)` 包覆 `inspect.signature(discover_fn)`，僅在簽名明確支援時傳入 `budget_max_jpy` / `preferences`，否則安全回退只傳 `(area_name=..., cuisine=..., max_seeds=4)`。

### 2.2 三級排序鍵設計 (`discover_area_top_stores`)
- **依賴方向防護**：將 `_extract_min_budget_jpy` 定義於 `src/services/tabelog_scraper.py`（無向上依賴），並由 `scoring.py` 與 `src/agent/stages/enrichment/__init__.py` 重新匯出以維持測試與外部相容性。
- **Dirty Payload 防護**：排序鍵納入 `not bool(c.get("is_relocated"))`，確保 `【移転】` 舊址絕不會壓過營業中名店。
- **條件判定對齊 `schemas.py` / `scoring.py`**：
  - 預算：`effective_min = min([p for p in (lunch_min, dinner_min) if p is not None], default=None)`，嚴格以 `effective_min is not None` 判斷（確保 `"～￥999"` 解析為 `0` 時仍為 Tier A）。
  - 支付：從 `preferences.get("payment_preferences")` 讀取 `"credit_card"` 與 `"paypay"`（`credit_cards` 非空為 Tier A、`== []` 為 Tier C、`is None` 為 Tier B；`supports_paypay is True` 為 Tier A、`is False` 為 Tier C、`is None` 為 Tier B）。
  - 排序 tuple（`reverse=True`）：`(not is_relocated, tier_rank, rating + award_bonus)`：
    - 無任何預算/支付條件時：`tier_rank = 0`（全部視同一般排序）。
    - 有條件時：`Tier A`（明確符合且無違規）=`2`、`Tier B`（無違規但關鍵欄位為 `None`）=`1`、`Tier C`（明確違規）=`0`。

---

## 3. 全鏈路影響與回歸測試盤點 (E2E Blast Radius)

1. **受影響之既有測試（需同步更新斷言以符合新契約）**：
   - `tests/test_real_world_regressions.py` **Case 12**（`test_case12_smart_prefiltering_early_stopping_and_foodie_ranking`）：原第 3 段驗證 Tier 1 已有 2 家高分店時會早停（`await_count == 4`），需更新為驗證 **6 家全查（`await_count == 6`）**，且因 `Shop 5`（3.60）不再被早停跳過，Top 1 斷言同步更新為 `Shop 5`。
   - `tests/test_real_world_regressions.py` **Case 50**（`test_case_50_budget_disables_early_stopping_and_caps_google_44`）：更新 docstring 說明全面全查並維持 Google > 4.4 壓平保護。
   - `tests/test_enrichment_modularization.py`：`test_early_stopping_policy` 與 `test_orchestrator_execution_end_to_end`（`await_count == 4` 更新為 `5`）改為驗證 **全查 + Semaphore(4) 併發限流**。
2. **新增單元 / 回歸測試**：
   - `TabelogScraper._fetch_and_parse_grounding` 可解析超過 5 家（驗證第 6～8 家不被截斷）。
   - `discover_area_top_stores` 三級優先權排序測試（Tier A 明確符合 > Tier B 價格 `None` 高分名店 > Tier C 明確超標墊底；全 Tier C 時仍回傳保底名單不變空）。
   - `execute_state_3_parallel_tabelog` 驗證同時進行的 `_enrich_single_place` 最大併發數 `<= 4`（Semaphore(4) 生效），且 6 家皆完成。

---

## 4. 驗證標準

- `python -m ruff format src/ tests/`
- `python -m ruff check src/ tests/`
- `python -m mypy src/`
- `pytest`（全數通過，無回歸）
