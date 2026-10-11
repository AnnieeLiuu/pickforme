# 🍱 PickForMe Project Guide for AI Coding Agents (AGENTS.md)

Welcome to **PickForMe** — a high-performance Japan Food AI Agent system built with FastAPI, Neon Cloud Postgres, Google Places API (New), Tabelog Scraper & Entity Resolver, and a Telegram Bot interface.

---

## 🚀 Key Architecture & Project Layout

- **`src/main.py`**: FastAPI entrypoint with health check & `/webhook/telegram` endpoints.
- **`src/bot/handlers.py`**: Telegram Webhook update handler with whitelist check, status streaming, and Markdown format (`parse_mode="Markdown"`).
- **`src/agent/pipeline.py`**: Deterministic 4-Stage FSM Pipeline (`FoodRecommendationFSM`) executing intent extraction, ladder Places search, parallel Tabelog enrichment, and template fallback card synthesis.
- **`src/agent/llm_client.py`**: Multi-provider LLM client with dynamic failover (`LLM_PRIMARY_PROVIDER`: Groq primary ➡️ Gemini 3.8 Flash ➡️ Ollama local fallback), 429 Retry-After backoff, `call_text`, and `call_structured_json`.
- **`src/agent/core.py`**: Clean Facade interface delegating incoming requests to `FoodRecommendationFSM`.
- **`src/agent/tools.py`**: Agent tool helper and schema definitions (`geocode_location`, `search_nearby`, `get_tabelog_info`).
- **`src/services/tabelog_scraper.py`**: Tabelog HTML scraper & Yahoo Web Grounding parser (ratings, reviews, budget, awards, payments).
- **`src/services/entity_resolver.py`**: Hybrid Funnel (0ms Fast-Path rules + Micro-LLM Smart Arbiter) resolving store identities.
- **`src/db/schema.sql`**: Neon Postgres DDL schema (`restaurants`, `tabelog_cache`, `chat_sessions`, `agent_runs`, `agent_steps`).

---

## 📚 Key Documentation Files

- **`docs/ARCHITECTURE.md`**: Master architectural blueprint, system topologies, Neon DB schema DDL, Scoped Anchor guardrails, and Mermaid flows.
- **`docs/tabelog_structure.md`**: Detailed HTML DOM structure, CSS selectors, and parsing rules for Tabelog pages.
- **`config/prompts/agent_system.md`**: System prompt for the Agent, including Telegram Markdown Code Block formatting (`\`寿々喜家 本店\``) for one-tap store name copying.

---

## 🛡️ Pre-push Quality Assurance Guidelines (CI Fail Prevention)

Before issuing any `git push` to remote (GitLab CI), AI Agents and developers **MUST** run the following local formatting and quality verification steps to guarantee 100% CI pass rate:

```bash
# 1. Format code with Ruff
python -m ruff format src/ tests/

# 2. Run Ruff lint check
python -m ruff check src/ tests/

# 3. Run Mypy type check
python -m mypy src/

# 4. Run Pytest suite
pytest
```

> [!NOTE]
> A Git pre-push hook (`.git/hooks/pre-push`) is installed to automatically intercept and prevent pushes if any of the above checks fail locally.

---

## 🧪 生產錯誤修正回歸測試強制規範 (Bug-Driven Regression Test Mandate)

為徹底根除系統在迭代過程中「歷史問題死灰復燃 (Regression)」的風險，AI Agents 與開發者在排查或修復任何錯誤時 **MUST** 恪守以下規範：

1. **一處修復，必加回歸 (One Fix, One Regression Test)**：
   - 凡是在生產環境、日常使用或對話測試中發現之缺陷、邊界異常（Corner Cases）或個案修復（例如：特定店名檢索失敗、Unicode 特殊標點阻斷比對、排版遭誤殺、意圖解析異常等），**強制一律必須在 [`tests/test_real_world_regressions.py`](file:///c:/Users/User/OneDrive/文件/2026/PickForMe/tests/test_real_world_regressions.py) 或對應單元測試中新增專屬測試案例**。
   - **嚴禁只改代碼不補測試**：任何未附帶對應回歸測試的 Bug Fix，皆視為不完整且違規之提交。
2. **真實案例場景固化 (Battle-Tested Case Preservation)**：
   - 測試案例必須真實還原觸發 Bug 的原始邊界輸入（例如特定的店家名稱、Unicode 字元、對話歷史 Payload），並在 Test Docstring 中清楚註明根本原因與防禦邏輯。
3. **0ms 離線 Mock 原則 (Fast & Deterministic)**：
   - 所有回歸測試必須依循 Mock-backed 原則（使用 `AsyncMock` / `MagicMock` 模擬 LLM 及外部 API），絕不可依賴 live network 或消耗真實 API Key，保證測試能在本機與 GitLab CI 中於毫秒級內穩定 100% 通過。
4. **歷史倒退零容忍 (Zero Regression Tolerance)**：
   - 專案既有的黃金回歸測試集為最高優先級守門防線。未來任何程式重構、架構演進或更換底層模型時，若破壞了任何一項歷史回歸案例，Git Pre-push Hook 與 CI 必須直接攔截並阻止上線。

---

## 🔄 Scratch Code Promotion & Graduation SOP

Temporary scripts created in `scratch/` during exploratory phase MUST follow this lifecycle:
1. **Verification**: Validate the POC script in `scratch/`.
2. **Promotion (轉正)**: Once verified, refactor core runtime logic into `src/` modules or official CLI utilities into `scripts/` (e.g. `scripts/setup_webhook.py`).
3. **Graduation & Clean-up (退場)**: Immediately clean up temporary files in `scratch/` to prevent repository clutter.

---

## 📄 Documentation & Pages Co-Evolution Guidelines (文件與可觀測性看板同步強制規範)

Whenever modifying code, schemas, configurations, or business logic, AI Agents and developers **MUST** simultaneously review and update all related documentation before completing the task:
1. **Zero Doc Drift (零文件滯後)**: Whenever database schemas (`schema.sql`), configuration keys (`config.py`), or agent routing logic change, immediately synchronize [`docs/ARCHITECTURE.md`](file:///c:/Users/User/OneDrive/文件/2026/PickForMe/docs/ARCHITECTURE.md).
2. **Mermaid & Diagram Consistency**: Keep architectural flowcharts and State transitions in sync with implementation reality.
3. **Observability & Pages Sync (架構動 Pages 必動)**: 凡涉及 Pipeline 狀態機流轉（State）、輸入輸出 Payload 或演算法階段演進時，**MUST 同時更新 `src/eval/reporter.py` 並重新生成 GitLab Pages 儀表板 (`public/index.html`)**，確保對外展示之步驟瀑布流與系統真實實作 100% 一致。
4. **Active Plan Sync**: When completing or adjusting an implementation plan in `docs/plans/`, update its status and architectural decisions.

---

## 🛡️ AGENTS.md 防膨脹與輕量化憲章 (Anti-Bloat & Scope Control Mandate)

為避免 `AGENTS.md` 隨專案迭代被塞入過多微觀技術細節而膨脹、最終癱瘓 AI Agent 的 Context Window，**所有 AI Agents 必須嚴格遵守以下範疇界線**：

1. **憲章定位 (Meta-Guidelines Only)**：
   - `AGENTS.md` **僅能承載跨模組的全局工程規範、CI/CD 政策、質量守門流程與開發 SOP**。
2. **嚴禁堆砌微觀細節 (No Micro-Implementation Details)**：
   - **嚴禁將個別函式的程式碼筆記、正則語法小撇步、特定 HTML CSS 選擇器、API 參數技巧或特定店家修復歷程追加至本文件**。
3. **知識職責分流原則 (Separation of Concerns)**：
   - **Bug 個案防禦** ➡️ 唯一收斂至單元測試與 [`tests/test_real_world_regressions.py`](file:///c:/Users/User/OneDrive/文件/2026/PickForMe/tests/test_real_world_regressions.py)。
   - **現行系統架構 (Living State)** ➡️ 唯一收斂至 [`docs/ARCHITECTURE.md`](file:///c:/Users/User/OneDrive/文件/2026/PickForMe/docs/ARCHITECTURE.md)。
   - **重大架構取捨與技術決策 (ADR)** ➡️ 唯一收斂至 [`docs/adr/`](file:///c:/Users/User/OneDrive/文件/2026/PickForMe/docs/adr/)。
   - **版本演進與變更歷史 (Timeline)** ➡️ 唯一收斂至 [`CHANGELOG.md`](file:///c:/Users/User/OneDrive/文件/2026/PickForMe/CHANGELOG.md)。
   - **HTML 剖析規則** ➡️ 唯一收斂至 [`docs/tabelog_structure.md`](file:///c:/Users/User/OneDrive/文件/2026/PickForMe/docs/tabelog_structure.md)。
   - **函式邊界與用法** ➡️ 唯一收斂至程式碼內部 Docstring 與 Type Annotations。
4. **篇幅警戒線 (Length Limit)**：
   - 本文件行數應嚴格維持在 **105 行以內**。若非涉及專案級全局開發方針的重大修訂，**嚴禁隨意擴增此文件**。
