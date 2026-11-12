# 🍱 PickForMe Project Guide for AI Coding Agents (AGENTS.md)

Welcome to **PickForMe** — a high-performance Japan Food AI Agent system built with FastAPI, Neon Cloud Postgres, Google Places API (New), Tabelog Scraper & Entity Resolver, and a Telegram Bot interface.

---

## 🚀 Key Architecture & Project Layout

- **Core Entry & Bot**: `src/main.py` (FastAPI Webhook), `src/bot/handlers.py` (Telegram handler), `src/agent/core.py` (Facade).
- **FSM Pipeline**: `src/agent/pipeline.py` & `src/agent/stages/` (Deterministic 4-Stage FSM: intent, places_search, enrichment, synthesis).
- **Multi-Provider LLM**: `src/agent/llm_client.py` (Groq primary ➡️ Gemini Flash ➡️ Ollama fallback, 429 Retry-After backoff).
- **Services & DB**: `src/services/` (Tabelog Scraper, Entity Resolver, Cuisine Guide), `src/db/` (Neon Postgres schema & repositories).

---

## 📚 Key Documentation Files

- **`docs/ARCHITECTURE.md`**: Master blueprint, system topologies, Neon DB DDL schema, and Scoped Anchor.
- **`docs/SOP_ROOT_CAUSE_DEBUGGING.md`**: 全景河流圖 (Global Blueprint River)、6維個案盤點矩陣與5步排查 SOP。
- **`docs/tabelog_structure.md`**: Detailed HTML DOM structure, CSS selectors, and parsing rules for Tabelog pages.
- **`config/prompts/agent_system.md`**: System prompt for Agent, including one-tap store name copying.

---

## 🛡️ Pre-push Quality Assurance Guidelines (CI Fail Prevention)

Before issuing any `git push` to remote (GitLab CI), AI Agents and developers **MUST** run the following verification steps:

```bash
python -m ruff format src/ tests/ && python -m ruff check src/ tests/ && python -m mypy src/ && pytest
```

> [!NOTE]
> A Git pre-push hook (`.git/hooks/pre-push`) automatically intercepts and blocks pushes if any of the above checks fail locally.

---

## 📋 計畫制定與 Subagent 交叉審查規範 (Plan & Subagent Review Mandate)

Whenever designing architectural changes, multi-step features, or refactoring:
1. **Plan First**: Always create/update a formal plan in `docs/plans/` before writing implementation code.
2. **Autonomous Subagent Review**: Proactively invoke an independent Research Subagent to read related files and rigorously review the plan for edge cases and regressions before starting implementation.

---

## 🧪 生產錯誤修正回歸測試強制規範 (Bug-Driven Regression Test Mandate)

為徹底根除系統在迭代過程中「歷史問題死灰復燃 (Regression)」的風險，AI Agents 與開發者在排查或修復任何錯誤時 **MUST** 恪守以下規範：

1. **一處修復，必加回歸 (One Fix, One Regression Test)**：凡在生產或測試中發現之缺陷或邊界異常，**強制一律在 [`tests/test_real_world_regressions.py`](file:///c:/Users/User/OneDrive/文件/2026/PickForMe/tests/test_real_world_regressions.py) 或對應單元測試中新增專屬測試案例**。嚴禁只改代碼不補測試。
2. **真實案例場景固化 (Battle-Tested Case Preservation)**：測試案例必須真實還原觸發 Bug 的原始輸入，並在 Test Docstring 中清楚註明根本原因與防禦邏輯。
3. **0ms 離線 Mock 原則 (Fast & Deterministic)**：所有回歸測試必須依循 Mock-backed 原則（使用 `AsyncMock` / `MagicMock`），絕不依賴 live network 或消耗真實 API Key，保證毫秒級穩定通過。
4. **歷史倒退零容忍 (Zero Regression Tolerance)**：專案黃金回歸測試集為最高優先級守門防線。破壞歷史案例者，Git Pre-push Hook 與 CI 直接攔截。

---

## 🔄 Scratch Code Promotion & Graduation SOP

Temporary scripts created in `scratch/` MUST follow: 1. Verify POC in `scratch/` ➡️ 2. Promote to `src/` or `scripts/` ➡️ 3. Clean up `scratch/`.

---

## 📄 Documentation & Pages Co-Evolution Guidelines (文件與可觀測性看板同步強制規範)

Whenever modifying code, schemas, configurations, or business logic, review and update all related docs before completion:
1. **Zero Doc Drift (零文件滯後)**：Schemas (`schema.sql`)、Config (`config.py`) 或路由變更時，立即同步 [`docs/ARCHITECTURE.md`](file:///c:/Users/User/OneDrive/文件/2026/PickForMe/docs/ARCHITECTURE.md)。
2. **Mermaid & Diagram Consistency**：保持架構圖與狀態流轉圖與實作 100% 一致。
3. **Observability & Pages Sync (架構動 Pages 必動 ＆ Playwright 實機驗收強制)**：Pipeline 狀態、Payload、模板或前端邏輯演進時，**MUST 同步更新 `src/eval/reporter.py`、重新生成 `public/index.html`，並強制使用 Playwright 進行瀏覽器實機驗收（確保 0 Console Errors、DOM 元素完整渲染且按鈕點擊交互響應正常）**，嚴禁盲信靜態 HTML 而未經實機瀏覽器驗收。
4. **Active Plan Sync**：在 `docs/plans/` 完成或調整計畫時，同步更新其狀態與架構決策。

---

## 🛡️ AGENTS.md 防膨脹與輕量化憲章 (Anti-Bloat & Scope Control Mandate)

為避免 `AGENTS.md` 隨專案迭代被塞入過多微觀技術細節而膨脹、最終癱瘓 AI Agent 的 Context Window，**所有 AI Agents 必須嚴格遵守以下範疇界線**：

1. **憲章定位 (Meta-Guidelines Only)**：`AGENTS.md` **僅能承載跨模組的全局工程規範、CI/CD 政策、質量守門流程與開發 SOP**。
2. **嚴禁堆砌微觀細節與土炮字典 (No Micro-Details & No Hardcoded Mappings)**：
   - 嚴禁將個別函式筆記、正則小撇步、特定 HTML CSS 選擇器或 API 技巧追加至本文件。
   - **🚫 Anti-Hardcoded Mapping 鐵則**：嚴禁硬編碼跨語言異體字或同義字字典；必須採倒排索引動態構建，或交由語言模型/外部標準 API 解析。
3. **知識職責分流原則 (Separation of Concerns)**：
   - Bug 個案防禦 ➡️ 單元測試與 [`tests/test_real_world_regressions.py`](file:///c:/Users/User/OneDrive/文件/2026/PickForMe/tests/test_real_world_regressions.py)。
   - 排查 SOP 與全景圖 ➡️ [`docs/SOP_ROOT_CAUSE_DEBUGGING.md`](file:///c:/Users/User/OneDrive/文件/2026/PickForMe/docs/SOP_ROOT_CAUSE_DEBUGGING.md)。
   - 現行系統架構 ➡️ [`docs/ARCHITECTURE.md`](file:///c:/Users/User/OneDrive/文件/2026/PickForMe/docs/ARCHITECTURE.md)。
   - 重大架構取捨 ➡️ [`docs/adr/`](file:///c:/Users/User/OneDrive/文件/2026/PickForMe/docs/adr/) 與 [`CHANGELOG.md`](file:///c:/Users/User/OneDrive/文件/2026/PickForMe/CHANGELOG.md)。
   - HTML 剖析規則 ➡️ [`docs/tabelog_structure.md`](file:///c:/Users/User/OneDrive/文件/2026/PickForMe/docs/tabelog_structure.md)。
4. **篇幅警戒線 (Length Limit)**：
   - 本文件物理總行數（以 Python `len(open('AGENTS.md', encoding='utf-8').readlines())` 或 VS Code 編輯器真實行號為準，包含空行）應嚴格維持在 **106 行以內**。若非涉及專案級全局開發方針的重大修訂，**嚴禁隨意擴增此文件**。
