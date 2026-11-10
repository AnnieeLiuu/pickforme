# 🍱 God Files Decoupling & Modularization Design Document

- **Date**: 2026-10-25
- **Status**: Approved
- **Scope**: `src/eval/reporter.py` and `src/agent/pipeline.py`

---

## 1. Intent & The Why

目前專案經過多輪迭代後，出現了兩大顯著的 God File 隱患：
1. **`src/eval/reporter.py` (2,380 行, 137 KB)**：包含超過 2,200 行的 HTML/CSS/JS 內嵌字串模板 (`BASE_DASHBOARD_TEMPLATE`)，嚴重污染了 Python 報表組裝邏輯，且前端程式碼缺乏獨立語法高亮與格式化支援。
2. **`src/agent/pipeline.py` (1,422 行, 74 KB)**：承載了 4-Stage FSM 的所有細節（意圖解析、階梯式 Google Places 搜尋、並行 Tabelog 爬取與老饕評分、最終推薦卡片排版與評分校驗）。單檔邏輯過於龐雜，難以針對單一階段進行局部維護或單元測試。

本設計旨在徹底落實**單一職責原則 (Single Responsibility Principle, SRP)**，將龐大的單體檔案解耦為職責分明的子模組，同時透過 **Facade 模式**保證所有既有公開 API 與 130 個回歸測試 100% 零破壞相容。

---

## 2. Goals, Non-Goals & Anti-Goals

### 🎯 Goals
- **G1 (視圖與資料分離)**：將 `src/eval/reporter.py` 中的前端 HTML/CSS/JS 模板抽離至 `src/eval/templates/river_dashboard.html`，使 `reporter.py` 行數壓制在 100 行以內。
- **G2 (狀態機階段解耦)**：建立 `src/agent/stages/` 套件，將 4 個 State 分別抽離為獨立階段模組：
  - `stages/intent.py` (State 1: Intent extraction, multi-turn history sanitization, follow-up detection)
  - `stages/places_search.py` (State 2: Ladder Places search, radius expansion 800m->1800m->3000m, prewarm checking)
  - `stages/enrichment.py` (State 3: Parallel Tabelog scraping, entity resolution, review indexing, foodie scoring, top store ranking)
  - `stages/synthesis.py` (State 4: Card synthesis, prompt construction, payment formatting, template fallback, rating sanitization)
- **G3 (零破壞相容性)**：`src/agent/pipeline.py` 保留為 Facade，保留原有的 `FoodRecommendationFSM` 類別及所有公開方法（`execute_state_1_intent`, `execute_state_2_places_ladder`, `execute_state_3_parallel_tabelog`, `execute_state_4_synthesis`, `run_pipeline`, `execute_follow_up`）與頂層工具函式（`resolve_search_constraints`, `format_payment_methods`, `get_recommendation_card_prompt`, `AGENT_SYSTEM_PROMPT_PATH`），對外部呼叫方 100% 無感。
- **G4 (質量守門)**：130 個既有測試 100% 通過，Ruff 與 Mypy 零錯誤。

### 🚫 Non-Goals
- 不修改任何核心業務演算法或提示詞內容（Prompt tokens & ranking logic remain unchanged）。
- 不更動資料庫 Schema 或外部 API Contract。
- 不修改既有 130 個測試的斷言邏輯。

### ⚠️ Anti-Goals
- **嚴禁循環匯入 (Circular Dependency)**：`stages/` 內部嚴禁直接 `import src.agent.pipeline`。共享資料型別與 Context 必須單向依賴。
- **嚴禁過度工程化 (Over-engineering)**：不引入重量級第三方狀態機框架或動態 Event Bus，保持純粹清晰的 `async/await` 協程流轉。

---

## 3. Evaluated Approaches & Selection

### 方案 A：雙軌兩階段無感拆解（選定）
- Phase 1 先抽離 `src/eval/reporter.py` 的前端模板；
- Phase 2 再將 `src/agent/pipeline.py` 按 FSM State 拆解為 `src/agent/stages/`，並以 `pipeline.py` 作為 Facade。
- **選擇理由**：風險極低、漸進式驗證、且完全不破壞任何既有測試。

### 方案 B：輕量外圍抽離（未採用）
- 僅抽離無狀態 Helper 函式，`execute_state_*` 仍留在主檔案。
- **未採用理由**：無法根本解決 `pipeline.py` 800+ 行的職責混淆問題。

### 方案 C：物件導向狀態模式（未採用）
- 引進抽象基底類別 `BaseState` 與動態狀態轉換器。
- **未採用理由**：過度設計，破壞現有 130 個 Mock 測試之介面合約。

---

## 4. Architecture & Interface Specifications

### 4.1 `src/eval/` 結構
```
src/eval/
├── __init__.py
├── evaluator.py
├── reporter.py                   # 瘦身至 ~70 行 (僅負責載入模板與資料替換)
└── templates/
    └── river_dashboard.html      # 完整的前端 HTML/CSS/JS (2,200+ 行)
```

`src/eval/reporter.py` 介面規範：
- `generate_markdown_report(summary: EvalSummary) -> str`
- `generate_html_dashboard(summary: EvalSummary, output_path: Path, ...) -> Path`
- `BASE_DASHBOARD_TEMPLATE: str`（保留為讀取 template 內容之模組常數，確保舊程式碼向後相容）

### 4.2 `src/agent/` 結構
```
src/agent/
├── core.py
├── pipeline.py                   # Facade: 委派各 State 至 stages/，維持原類別與函式
├── tools.py
├── llm_client.py
├── validator.py
└── stages/
    ├── __init__.py
    ├── intent.py                 # State 1
    ├── places_search.py          # State 2
    ├── enrichment.py             # State 3
    └── synthesis.py              # State 4
```

---

## 5. Builder Check Assessment

- **Q1: 階段間的依賴與共用成員如何傳遞？**
  - **決策**：`FoodRecommendationFSM` 實例持有 `llm_client`, `tabelog_scraper`, `entity_resolver`, `vector_repo` 等依賴。各 stage 模組以函式或 Stage 輔助類別形式接受 `fsm: FoodRecommendationFSM` 或顯式依賴參數，避免循環參照。
- **Q2: 測試程式中直接 `from src.agent.pipeline import ...` 的相容性？**
  - **決策**：`src/agent/pipeline.py` 在頂層顯式 `__all__` 並重新匯出所有被測試引用的符號（包含 `FoodRecommendationFSM`, `resolve_search_constraints`, `format_payment_methods`, `get_recommendation_card_prompt`, `AGENT_SYSTEM_PROMPT_PATH` 等），保證 100% 原始路徑可用。
