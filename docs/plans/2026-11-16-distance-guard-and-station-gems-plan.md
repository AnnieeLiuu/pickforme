# 距離守門與「延伸站點老店參考」設計計畫

**日期**: 2026-11-16  
**狀態**: Subagent Review Approved（已納入 5 項審查修正：`_last_distant_station_gems`、`out_of_radius_fallback` 分層回補、`dist_m is not None` 防護、`「」` 車站萃取、`validator.py` `🚉` 豁免）  
**觸發案例**: 線上 Trace `9f3157be-f350-4435-a5d4-541e6e3957b6`（「新宿御苑附近的咖啡店推薦」卻讓 1.6km 外的新大久保店 `cafe&barFACTORY45` 登上第 1 名）

---

## 1. 問題根因

1. **Stage 2 源頭 B 未檢查 `dist_m > r` (`src/agent/stages/places_search.py`)**：
   - `GooglePlacesClient.search_by_text` 使用 `locationBias`（僅偏好範圍），會把 1.6 公里外的新大久保店家塞進結果。
   - `execute_state_2_places_ladder` 在各半徑迴圈內計算 `dist_m` 後未檢查 `dist_m > r`，直接計入 `candidates` 並觸發 `len(candidates) >= 3` 提早停止擴圈。
2. **Stage 3 市區地標被誤豁免距離扣分 (`src/agent/stages/enrichment/scoring.py`)**：
   - `calculate_foodie_score` 寫成 `if not (is_wide_area_origin(...) or store.get("nearest_station_desc")):`，導致「新宿御苑」等市區具體地標只要店家有 `nearest_station_desc`（如「山手線新大久保站」），距離扣分就歸零。
3. **缺少「主推薦保附近、下方補延伸站點老店」機制**：
   - 使用者期望主推薦（Top 3）優先保留查詢地點附近的好店；若系統有找到較遠處（或非 Top 3 候選）的高評價老店與對應車站，則於卡片下方明確加註「哪個站附近還有老店」。

---

## 2. 設計方案

### 2.1 Stage 2 距離守門與超距暫存 (`src/agent/stages/places_search.py`)
- 在 `execute_state_2_places_ladder` 的半徑迴圈內（`800m / 1200m / 1800m` 與 `2500m`）：
  - 若 `dist_m > r`（且 `dist_m > 0`），**不計入當圈 `candidates`**，改存入 `demoted_fallback`（若同時品類不符則不列為高潛力延伸站點）。
  - 僅當所有半徑與 Tabelog 種子合併後 `len(candidates) < 3` 時，才從 `demoted_fallback`（優先選同品類且距離較近者）補回保底，確保 **100% 抗零結果**。
- `_hydrate_tabelog_seeds_with_places` 已具備 `if dist_m > max_radius: return None`（維持不變）。

### 2.2 Stage 3 距離扣分修正 (`src/agent/stages/enrichment/scoring.py`)
- `calculate_foodie_score` 的距離扣分豁免收斂為僅限廣域景區：
  `if not is_wide_area_origin(store.get("origin_name")):`
  （移除 `or store.get("nearest_station_desc")`，確保市區地標如「新宿御苑」不再被「山手線新大久保站」豁免距離扣分；同時完全相容 `箱根` 廣域景區回歸測試）。

### 2.3 「延伸站點老店參考」提取與 Stage 4 確定性渲染 (`orchestrator.py`, `pipeline.py`, `synthesis.py`)
1. **來源資料提取（零額外 API 請求）**：
   - 在 `pipeline.py`（或 `synthesis.py` 輔助函式）從 Stage 3 已完成查驗但**未進入 Top 3 的剩餘店家**（`remaining_enriched`）中，找出符合以下條件的店家（最多取 2 家、依不同車站去重）：
     - 未被標記 `category_mismatch` 或 `constraint_violation`；
     - 具備明確車站描述（`nearest_station_desc`）或街區資訊；
     - 屬於高評價或遠距老店（`(tabelog_rating >= 3.45 or awards or hyakumeiten)` 或 `distance_meters > 1000` 且 `tabelog_rating >= 3.3`）。
   - 將提取結果放入 `search_meta["distant_station_gems"]` 傳入 `execute_state_4_synthesis`。
2. **Stage 4 確定性渲染 (`synthesis.py`)**：
   - 在 `render_template_fallback_card` 結尾（`💡 特派員提醒` 之後或之前），若 `search_meta.get("distant_station_gems")` 非空，確定性渲染一行：
     `🚉 **延伸站點老店參考**：若願意多移動一段距離，「{station}」附近另有高評價名店 \`{name}\`（Tabelog {rating:.2f} 分）。`
   - 0 額外 LLM token、0 幻覺。

---

## 3. 驗證與回歸測試

- 確認既有 `test_enrichment_modularization.py`（`store_wide` `"箱根"`）與 `test_real_world_regressions.py`（`test_regression_foodie_score_distance_penalty_waiver_for_wide_area`）全數通過。
- 新增 **Case 54**：還原 `9f3157be`（搜新宿御苑咖啡時，1.6km 外的新大久保店在 800m 圈被暫存不搶佔名額、市區地標不再因 `nearest_station_desc` 豁免距離扣分，且遠距高分老店於卡片下方呈現 `🚉 延伸站點老店參考`）。
