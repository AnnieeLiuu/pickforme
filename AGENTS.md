# 🍱 PickForMe Project Guide for AI Coding Agents (AGENTS.md)

Welcome to **PickForMe** — a high-performance Japan Food AI Agent system built with FastAPI, Neon Cloud Postgres, Google Places API (New), Tabelog Scraper & Entity Resolver, and a Telegram Bot interface.

---

## 🚀 Key Architecture & Project Layout

- **`src/main.py`**: FastAPI entrypoint with health check & `/webhook/telegram` endpoints.
- **`src/bot/handlers.py`**: Telegram Webhook update handler with whitelist check, status streaming, and Markdown format (`parse_mode="Markdown"`).
- **`src/agent/core.py`**: Custom OpenAI-format LLM Agent Loop with failover (`gemini-2.0-flash` primary ➡️ `qwen/qwen3.8-27b` Groq fallback).
- **`src/agent/tools.py`**: Tool definitions (`geocode_location`, `search_nearby`, `get_tabelog_info`).
- **`src/services/tabelog_scraper.py`**: Tabelog HTML scraper parsing rating, reviews, budget, phone, `awards`, `hyakumeiten`, `supports_paypay`, and `credit_cards`.
- **`src/services/entity_resolver.py`**: Match confidence resolver between Google Places and Tabelog search candidates.
- **`src/db/schema.sql`**: Neon Postgres database DDL schema (`restaurants`, `tabelog_cache`, `chat_sessions`, `prewarm_jobs`, `agent_runs`, `agent_steps`).

---

## 📚 Key Documentation Files

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



