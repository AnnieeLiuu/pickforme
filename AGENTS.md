# 🍱 PickForMe Project Guide for AI Coding Agents (AGENTS.md)

Welcome to **PickForMe** — a high-performance Japan Food AI Agent system built with FastAPI, Neon Cloud Postgres, Google Places API (New), Tabelog Scraper & Entity Resolver, and a Telegram Bot interface.

---

## 🚀 Key Architecture & Project Layout

- **`src/main.py`**: FastAPI entrypoint with health check & `/webhook/telegram` endpoints.
- **`src/bot/handlers.py`**: Telegram Webhook update handler with whitelist check, status streaming, and Markdown format (`parse_mode="Markdown"`).
- **`src/agent/core.py`**: Custom OpenAI-format LLM Agent Loop with failover (`gemini-3.8-flash` primary ➡️ `qwen/qwen3.8-27b` Groq fallback).
- **`src/agent/tools.py`**: Tool definitions (`geocode_location`, `search_nearby`, `get_tabelog_info`).
- **`src/services/tabelog_scraper.py`**: Tabelog HTML scraper parsing rating, reviews, budget, phone, `awards`, `hyakumeiten`, `supports_paypay`, and `credit_cards`.
- **`src/services/entity_resolver.py`**: Match confidence resolver between Google Places and Tabelog search candidates.
- **`src/db/schema.sql`**: Neon Postgres database DDL schema (`restaurants`, `tabelog_cache`, `chat_sessions`, `prewarm_jobs`, `agent_runs`, `agent_steps`).

---

## 📚 Key Documentation Files

- **`docs/tabelog_structure.md`**: Detailed HTML DOM structure, CSS selectors, and parsing rules for Tabelog pages.
- **`config/prompts/agent_system.md`**: System prompt for the Agent, including Telegram Markdown Code Block formatting (`\`寿々喜家 本店\``) for one-tap store name copying.

---

## 🧪 Testing & Pre-warming Commands

```bash
# Run pytest suite (26+ unit tests)
pytest

# Seed and pre-warm Tabelog cache for a target area (e.g. 横浜)
python scripts/seed_prewarm_jobs.py --areas "橫濱"
python -m src.worker.prewarm

# Run local development server
python run_server.py
```
