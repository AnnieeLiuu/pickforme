# 全品類通用前置防線計畫：Stage 1 `target_place_types` ＆ Stage 2/3 通用類型過濾

**日期**: 2026-11-16  
**狀態**: Subagent Review Approved（已納入 4 項審查修正：`null` 轉空串列、`FSMRunContext` ＆ `working_memory` 多輪「換一批」繼承、`drink` 探索直推預設類型、粗粒度大類互斥 `tabelog_genre` 檢查）  
**目標**: 移除 `category_guard.py` 中的「咖啡專用特判」，改由 Stage 1 LLM 動態輸出 Google Places API (New) 標準 `target_place_types`，讓 Stage 2 在上游即可對**所有料理品類**進行精準前置過濾。

---

## 1. 架構設計

### 1.1 Stage 1 意圖契約擴充 (`src/agent/schemas.py`, `config/prompts/intent_parser.md`)
- `IntentOutput` 新增欄位：
  `target_place_types: list[str] = Field(default_factory=list, description="...")`
  （預設空串列，確保所有既有測試、Fallback 與 `QuickPassGate` 100% 向後相容）。
- `config/prompts/intent_parser.md` 補充精簡指引：當使用者指定具體料理或店型時，填入 1～4 個 Google Places API (New) 標準類型（例如：咖啡 `["cafe", "coffee_shop"]`、拉麵 `["ramen_restaurant"]`、壽司 `["sushi_restaurant"]`、居酒屋 `["japanese_izakaya_restaurant", "izakaya"]`、酒吧 `["bar", "pub"]`、燒肉/牛排 `["steak_house", "yakiniku_restaurant"]`、麵包甜點 `["bakery", "dessert_shop"]`）；若為泛稱「美食／餐廳」則留空 `[]`。
- **多輪「換一批 (`more`)」與追問繼承**：確認 `pipeline.py` / `session_repo` 若有 `working_memory`，確保 `target_place_types` 可於同輪傳遞並在需要時安全退回 `[]`（Fail-Open）。

### 1.2 通用 `check_category_mismatch` (`src/services/category_guard.py`)
移除咖啡專用字詞與黑名單，改採**與品類無關的通用集合比對**：
1. 若 `target_place_types` 為空（`[]` / `None`），直接回傳 `(False, None)`（Fail-Open）。
2. **Google `primary_type` / `types` 通用判定**：
   - 若 `primary_type` 與 `types` 皆缺失 → Fail-Open `(False, None)`。
   - 若 `primary_type in targets` → 直接放行 `(False, None)`。
   - 若 `primary_type` 屬於「明確獨立專賣／夜生活業態集合」（如 `hookah_bar`, `night_club`, `bar`, `pub`, `japanese_izakaya_restaurant`, `izakaya`, `ramen_restaurant`, `sushi_restaurant`, `cafe`, `coffee_shop`, `bakery`, `steak_house` 等）且 `primary_type not in targets` → **判定不符** `(True, ...)`，**不再因次要 `types` 塞入標籤而放行**（例如 `C.Stand` `primary_type="hookah_bar"` 即使 `types` 含 `"cafe"` 仍直接擋掉）。
   - 若 `primary_type` 為泛用分類（如 `"restaurant"`, `"japanese_restaurant"`, `"food"`, `"point_of_interest"`）：
     - 若 `types_set & targets` 非空 → 放行（保護複合型早午餐咖啡廳或一般日本料理店）；
     - 僅當 `targets` 全為非正餐專賣店（如 `cafe`/`coffee_shop`/`bakery`/`bar`/`hookah_bar`）且 `types_set & targets` 為空時 → 判定不符。
3. **Tabelog 兜底**：保留通用 `tabelog_genre` 與目標明顯衝突之防護（僅於明確存在非目標專賣分類時觸發，無須維護大型同義字字典）。

### 1.3 上下游接線 (`pipeline.py`, `places_search.py`, `orchestrator.py`, `scoring.py`)
- `pipeline.py` 從 `parsed.get("target_place_types")` 取得 `target_place_types`（存入 `run_ctx`），傳入 `execute_state_2_places_ladder` 與 `execute_state_3_parallel_tabelog` → `apply_deterministic_constraints`（皆為選填參數 `Optional[list[str]] = None`）。
- Stage 2 在源頭 B 半徑迴圈內直接依 `target_place_types` 過濾（`mismatch_fallback` 僅於全半徑跑完仍不足 3 家時保底補回），確保好名單在最前面就被選進來。

---

## 2. 風險與測試計畫

- 更新 `tests/test_real_world_regressions.py` **Case 53**：驗證通用 `target_place_types` 在「咖啡（擋掉 `japanese_izakaya_restaurant` 與 `hookah_bar` 就算 `types` 含 `cafe`）」、「拉麵（擋掉 `japanese_izakaya_restaurant`）」、「酒吧（`target_place_types=["bar"]` 正常保留酒吧與居酒屋）」三種場景皆成立。
- 確認 `tests/test_schemas_and_self_correction.py`、`tests/test_pipeline.py` 與既有 282 個測試全數通過。
