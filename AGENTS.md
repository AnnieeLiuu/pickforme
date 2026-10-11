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
- **`src/services/tabelog_scraper.py`**: Tabelog HTML scraper parsing rating, reviews, budget, phone, `awards`, `hyakumeiten`, `supports_paypay`, and `credit_cards`.
- **`src/services/entity_resolver.py`**: Match confidence resolver between Google Places and Tabelog search candidates.
- **`src/db/schema.sql`**: Neon Postgres database DDL schema (`restaurants`, `tabelog_cache`, `chat_sessions`, `prewarm_jobs`, `agent_runs`, `agent_steps`).

---

## 📚 Key Documentation Files

- **`docs/ARCHITECTURE.md`**: Master architectural blueprint, system topologies, Neon DB schema DDL, and end-to-end Mermaid flowcharts.
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
   - 測試案例必須真實還原觸發 Bug 的原始邊界輸入（例如特定的店家名稱、Unicode 字元、對話歷史 Payload），並在 Test Docstring 中清楚註明該戰役的根本原因與防禦邏輯。

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

## ⚙️ New Feature CI Impact Evaluation Guidelines (新功能 CI 評估規範)

Whenever designing or implementing a new feature or module, AI Agents and developers **MUST** perform a CI Evaluation:

1. **Mockable Core Logic (適合進 CI)**:
   - Core algorithms, data models, handlers, and client bridges (e.g., `IDEAgentBridge`) **MUST** have corresponding unit tests in `tests/test_*.py` using AsyncMock/MagicMock.
   - These tests MUST run fast and offline without external dependencies (no live API keys, no network calls) so GitLab CI pipeline passes deterministically.

2. **Environment-Dependent Features (不硬塞進 CI)**:
   - Live local servers (e.g., `agent_bridge_server.py`), real Cloud Postgres DB connections, or live tunnel endpoints are NOT required to run as live services inside GitLab CI runners.
   - Test their integration via mock-backed unit tests instead to keep CI fast and stable.

---

## 📄 Documentation Co-Evolution Guidelines (文件與程式碼同步強制規範)

Whenever modifying code, schemas, configurations, or business logic, AI Agents and developers **MUST** simultaneously review and update all related documentation before completing the task:

1. **Zero Doc Drift (零文件滯後)**:
   - Never update code without synchronizing related documentation.
   - Whenever database schemas (`schema.sql`), configuration keys (`config.py`), or agent routing logic change, immediately synchronize [docs/ARCHITECTURE.md](file:///c:/Users/User/OneDrive/文件/2026/PickForMe/docs/ARCHITECTURE.md).
2. **Mermaid & Diagram Consistency (圖表一致性)**:
   - If an architectural flow changes (e.g. LLM failover hierarchy, bot command routing), update the Mermaid flowcharts in `docs/ARCHITECTURE.md` to reflect reality.
3. **Active Plan & Spec Sync (計畫與驗證狀態同步)**:
   - When completing or adjusting an implementation plan in `docs/plans/`, update its status, benchmarks, and architectural decisions to prevent misleading future agent invocations.

---

## 🐍 Python 3.11+ 正則表達式旗標安全規範 (Regex Flag Safety)

在 Python 3.11+ 中，正則表達式引擎針對全域旗標實施嚴格檢查：
- **禁止中段旗標**：嚴禁在正則模式中間、括號內或分支中插入全域旗標（如 `(?:A|(?i)B)`），此寫法會直接引發 `re.error: global flags not at the start of the expression` 執行期崩潰。
- **標準傳參方式**：所有大小寫不敏感之正則操作，**強制一律傳遞 `flags=re.IGNORECASE` 參數**（例如 `re.sub(pattern, repl, string, flags=re.IGNORECASE)`），或將 `(?i)` 明確置於整個 Pattern 最開頭。

---

## 🎯 雙層漏斗實體對齊設計原則 (Hybrid Funnel Entity Resolution)

在處理跨平台店家對齊（Google Places ↔ Tabelog）時，嚴禁單純仰賴靜態 Regex 窮舉或純字面 SequenceMatcher，必須落實「雙層漏斗」：
1. **Level 1: 0ms 規則快篩 (Fast-Path)**：
   - 電話號碼完全吻合，或去除品類詞後之核心品牌 Token 高度相符（信心度 `>= 0.85`），或搜尋引擎首位店家主幹完全吻合者，**0ms 直接判定通過**，不消耗任何外部 LLM 配額與延遲。
2. **Level 2: LLM 智能裁決 (Smart Arbiter)**：
   - 字串相似度落在灰色模糊區間（`0.40 <= score < 0.85`，如中英日混雜、異構分店名、長品類修飾詞）者，自動啟動高速 Micro-LLM（Groq Llama 3.3 / Gemini 3.8 Flash）進行語意判定，以低延遲（<300ms）確保 100% 召回率。

---

## 🧹 Web Grounding 搜尋卡片防污染規範 (Search Card Sanitation)

當因 Cloudflare WAF (403) 降級至 Web Grounding（如 Yahoo! JAPAN 搜尋卡片）抓取時：
1. **過濾預約/導航掛件**：自動跳過包含 `AnswerGourmetSpot__plan` 等預約方案小部件，以及標題以 `空席状況`、`クチコミ`、`予約サイト`、`周辺の地図` 開頭的非店家卡片。
2. **清理預約後綴詞**：提取店名時，需自動剝離 `のご予約`、`の予約`、`Reservation` 等雜訊後綴。
3. **高品質卡片融合 (Card Merging)**：若首張碰到的卡片缺少評分，後續遇見同店家但具備評分與食評之卡片時，應主動更新並補全候選資料。

---

## 📞 電話號碼檢索備援規範 (Phone Grounding Fallback)

當 Google Places 店名為連綴羅馬拼音（如 `Densetsunosutadonya Yokohamanishiguchiten`）或特殊譯名，導致「店名 + 商圈」與「純店名」檢索候選數皆為 0 筆時：
- **電話檢索兜底**：只要 Google Places 提供日本市話/手機號碼（有效數字 `>= 9` 碼），系統**強制自動以 `site:tabelog.com <phone>` 執行 Web Grounding 檢索**。
- **0ms 秒級命中**：經電話檢索命中之候選者，於 `EntityResolver` 中受 Level 1 電話完全吻合規則保護，直接給予 1.0 信心度通過，確保極端店名下的 100% 召回率。

---

## 🛡️ 卡片驗證防誤殺規範 (Fuzzy Guardrail Validation)

卡片版型驗證器 (`validator.py`) 檢查反引號一鍵複製店名時：
1. **字形與繁日容錯**：必須去除空白與特殊符號，相容繁日漢字（「橫」vs「横」）與商場譯名差異（「Joinus」vs「ジョイナス」）。
2. **跨語言還原保護**：當 Google Places 為純羅馬拼音、而 LLM 自發性還原為道地日文漢字店名時，只要代碼塊為合理長度之合法短語，**嚴禁粗暴判定格式違規並作廢卡片**。
3. **模板直出評論防污染**：任何觸發模板直出兜底時，原始食評文字必須截斷在 120 字以內，嚴禁向用戶輸出未經總結的數百字英文/日文流水帳。




