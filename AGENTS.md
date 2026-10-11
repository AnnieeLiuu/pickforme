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




