# LINE Bot 雙平台整合與老饕篩選演化實作計畫 (Implementation Plan)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 為 PickForMe 新增 LINE Bot 支援、主動情境分類引導（Quick Reply）、Telegram 白箱實況副螢幕，並將老饕評分公式從 Google 端的評分預篩重構為專屬於 Tabelog 的純粹老饕重排。

**Architecture:** 
1. 資料層：將 `chat_sessions`, `agent_runs`, `prewarm_jobs` 的 `chat_id` 遷移為 `VARCHAR(64)`，無縫相容 Telegram 與 LINE。
2. 搜尋與評分層：廢除 Google 端星級預篩，在 State 1 增加廣義詞「情境分類引導 (方式 A)」；符合專門店的候選全數進入 Tabelog，以「Tabelog 評分 ＋ 百名店榮譽 － 距離懲罰」定勝負。
3. 渠道層：新增 `/api/line/webhook`、簽章驗證與事件處理器（`LineBotHandler`），並在 `AgentEngine` 串接 Telegram 白箱實況副螢幕（4 動動態跳動）。

**Tech Stack:** FastAPI, line-bot-sdk (or httpx lightweight signature + messaging API), Neon Cloud Postgres, Groq/Gemini LLMClient, pytest, ruff, mypy.

**Spec:** [`docs/superpowers/specs/2026-10-26-line-bot-integration-design.md`](file:///c:/Users/User/OneDrive/文件/2026/PickForMe/docs/superpowers/specs/2026-10-26-line-bot-integration-design.md)

## Global Constraints

- **Python Runtime:** Python 3.11+, 嚴格遵循全型別標註 (`from __future__ import annotations` / typing)。
- **LINE API Policy:** 採用免費 Reply API (`/v2/bot/message/reply`)，零費用負擔；簽章以 `X-Line-Signature` HMAC-SHA256 驗證。
- **Zero Regression Rule:** 既有 Telegram 所有功能與 `tests/` 測試集必須 100% 保持通過，所有新測試必須遵循 0ms Offline Mock 原則。
- **Lint & Type Check:** 必須通過 `ruff check`, `ruff format`, `mypy src/`。

## Review Focus

1. LINE User ID 為字串（如 `U4af4980...`），若傳入預期 `int` 的 DB 查詢不可拋出 `DataError`。
2. 非法或篡改之 `X-Line-Signature` 必須回傳 HTTP 403 Forbidden，不可洩漏伺服器內部錯誤。
3. 使用者輸入廣義「咖啡店」或「日料」時，系統必須觸發情境選單，不應在第一步直接執行無效的大雜燴搜尋。
4. Google 端搜尋不應有硬性的 `rating >= 3.5` 截斷，觀光客給低分但 Tabelog 高分的職人店必須能順利進入 Tabelog 核驗。
5. Telegram 實況轉播在更新時不可觸發 Telegram 429 Too Many Requests，更新間隔需有防抖保護（>= 0.8s）。

---

### Task 1: 資料庫與 Repository chat_id 型態擴展 (VARCHAR 64)

**Files:**
- Modify: `src/db/schema.sql:34-62`
- Modify: `src/db/repositories/session.py:10-70`
- Modify: `src/agent/trace.py:15-45`
- Test: `tests/test_db_migration.py`

**Interfaces:**
- Consumes: Neon DB Pool `get_db_pool()`
- Produces: `SessionRepository.get_session(chat_id: Union[str, int])`, `AgentTraceLogger.start_run(chat_id: Optional[Union[str, int]])`

- [ ] **Step 1: Write the failing test**

```python
import pytest
from src.db.repositories.session import SessionRepository

@pytest.mark.asyncio
async def test_session_repository_supports_string_chat_id():
    repo = SessionRepository()
    # 測試傳入 LINE 字串 User ID (如 U4af4980629...)
    line_user_id = "U4af49806293847281920394857610293"
    session = await repo.get_session(line_user_id)
    # 應正常回傳 None 或 dict，不拋出資料型別錯誤
    assert session is None or isinstance(session, dict)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_db_migration.py -v`
Expected: FAIL if database schema or type annotation restricts chat_id to integer.

- [ ] **Step 3: Update `src/db/schema.sql`, `session.py`, `trace.py`**

1. 在 `src/db/schema.sql` 將 `chat_sessions.chat_id`, `agent_runs.chat_id`, `prewarm_jobs.chat_id` 改為 `VARCHAR(64)`。
2. 在 `src/db/repositories/session.py` 將所有 `chat_id: int` 型別標註改為 `Union[str, int]`。
3. 在 `src/agent/trace.py` 將 `chat_id: Optional[int]` 改為 `Optional[Union[str, int]]`。
4. 執行 ALTER TABLE 語句同步本地/雲端 DB。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_db_migration.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/db/schema.sql src/db/repositories/session.py src/agent/trace.py tests/test_db_migration.py
git commit -m "feat(db): expand chat_id to varchar(64) to support both Telegram and LINE user IDs"
```

---

### Task 2: 廢除 Google 星級預篩，重構為純粹 Tabelog 老饕加權

**Files:**
- Modify: `src/agent/stages/places_search.py:80-185`
- Modify: `src/agent/stages/enrichment.py:75-120`
- Test: `tests/test_pure_tabelog_ranking.py`

**Interfaces:**
- Consumes: `places: list[dict]` from Google Places
- Produces: `clean_places: list[dict]` 保送專門店進 Tabelog; `calculate_foodie_score(store)` 專屬以 Tabelog 分數為主計算老饕得分。

- [ ] **Step 1: Write the failing test**

```python
import pytest
from src.agent.stages.places_search import prefilter_potential_foodie_places
from src.agent.stages.enrichment import calculate_foodie_score

def test_google_prefilter_does_not_kill_low_google_rating_shokunin_store():
    # 職人老店：Google 僅 3.3 分，但非連鎖店
    places = [
        {"name": "職人神店", "google_rating": 3.3, "user_ratings_total": 50, "distance_meters": 200},
        {"name": "連鎖快餐店", "google_rating": 4.2, "user_ratings_total": 800, "distance_meters": 300},
    ]
    # 新版預篩：不再以星級砍店，職人神店必須獲得保留晉級進 Tabelog 查驗
    seeds = prefilter_potential_foodie_places(places, max_seeds=6)
    seed_names = [s["name"] for s in seeds]
    assert "職人神店" in seed_names

def test_tabelog_score_is_supreme():
    store_a = {"name": "Google 高分無 Tabelog", "google_rating": 4.8, "tabelog_rating": None, "distance_meters": 100}
    store_b = {"name": "Tabelog 百名店", "google_rating": 3.4, "tabelog_rating": 3.82, "awards": ["百名店"], "distance_meters": 300}
    score_a = calculate_foodie_score(store_a)
    score_b = calculate_foodie_score(store_b)
    assert score_b > score_a
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pure_tabelog_ranking.py -v`
Expected: FAIL

- [ ] **Step 3: Refactor `places_search.py` and `enrichment.py`**

1. 修改 `prefilter_potential_foodie_places`：移除 `g_rating * 0.6` 的一刀切淘汰邏輯，改為主要排除連鎖店與遠距離無效店家，保護職人老店全數進入 Tabelog 檢核。
2. 確保 `calculate_foodie_score` 中，Tabelog 評分與百名店享有決定性權重。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_pure_tabelog_ranking.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent/stages/places_search.py src/agent/stages/enrichment.py tests/test_pure_tabelog_ranking.py
git commit -m "refactor(agent): eliminate google rating prefiltering in favor of pure tabelog ranking"
```

---

### Task 3: 廣義詞「方式 A：主動情境分類引導」(Disambiguation Guide)

**Files:**
- Modify: `src/agent/stages/intent.py:19-75`
- Create: `src/agent/stages/disambiguation.py`
- Modify: `src/agent/pipeline.py:200-245`
- Test: `tests/test_disambiguation.py`

**Interfaces:**
- Consumes: `user_message: str`, `cuisine: str`
- Produces: `is_broad_category(cuisine) -> bool`, `build_disambiguation_options(location, cuisine) -> dict`

- [ ] **Step 1: Write the failing test**

```python
import pytest
from src.agent.stages.disambiguation import is_broad_category, get_category_options

def test_broad_category_detection():
    assert is_broad_category("咖啡店") is True
    assert is_broad_category("日料") is True
    assert is_broad_category("日本料理") is True
    assert is_broad_category("博多拉麵") is False

def test_disambiguation_options_content():
    options = get_category_options("咖啡店", location="新宿三丁目")
    assert "情境" in options["title"]
    assert len(options["choices"]) >= 4
    # 包含甜點、復古、手沖、久坐等選項
    labels = [c["label"] for c in options["choices"]]
    assert any("甜點" in l for l in labels)
    assert any("昭和" in l or "復古" in l for l in labels)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_disambiguation.py -v`
Expected: FAIL

- [ ] **Step 3: Implement `src/agent/stages/disambiguation.py` and hook into FSM State 1**

1. 定義 `is_broad_category(category: str) -> bool`。
2. 實作 `get_category_options(category: str, location: str) -> dict`，包含文字說明、選項編號與 Quick Reply payload。
3. 在 `pipeline.py` 當意圖為 search 且 cuisine 為廣義詞時，若無指定細分風格，回傳情境引導選單。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_disambiguation.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent/stages/disambiguation.py src/agent/stages/intent.py src/agent/pipeline.py tests/test_disambiguation.py
git commit -m "feat(agent): add category disambiguation guide (Option A) for broad dining queries"
```

---

### Task 4: LINE Bot Webhook、簽章驗證與事件處理器

**Files:**
- Modify: `src/config.py:10-40`
- Create: `src/bot/line_webhook.py`
- Create: `src/bot/line_handlers.py`
- Modify: `src/main.py:10-65`
- Test: `tests/test_line_bot.py`

**Interfaces:**
- Consumes: FastAPI Request, `settings.LINE_CHANNEL_SECRET`, `settings.LINE_CHANNEL_ACCESS_TOKEN`
- Produces: `/api/line/webhook` endpoint, `LineBotHandler.handle_event(event)`

- [ ] **Step 1: Write the failing test**

```python
import hmac
import hashlib
import json
import pytest
from httpx import AsyncClient
from src.main import app
from src.config import settings

@pytest.mark.asyncio
async def test_line_webhook_signature_verification():
    secret = "test_channel_secret"
    settings.LINE_CHANNEL_SECRET = secret
    body = json.dumps({"events": []}).encode("utf-8")
    valid_signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    import base64
    valid_sig_b64 = base64.b64encode(valid_signature).decode("utf-8")

    async with AsyncClient(app=app, base_url="http://test") as ac:
        # 正確簽章 -> 200
        res = await ac.post("/api/line/webhook", content=body, headers={"x-line-signature": valid_sig_b64})
        assert res.status_code == 200

        # 錯誤簽章 -> 403
        res_bad = await ac.post("/api/line/webhook", content=body, headers={"x-line-signature": "bad_sig"})
        assert res_bad.status_code == 403
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_line_bot.py -v`
Expected: FAIL with 404 Not Found (router not mounted yet)

- [ ] **Step 3: Implement `src/config.py`, `line_webhook.py`, `line_handlers.py`, and mount in `main.py`**

1. 在 `src/config.py` 加入 `LINE_CHANNEL_SECRET`, `LINE_CHANNEL_ACCESS_TOKEN`, `LINE_ALLOWED_USER_IDS`。
2. 在 `src/bot/line_webhook.py` 實作標準 HMAC-SHA256 驗證端點，以 BackgroundTasks 非同步分派給 Handler。
3. 在 `src/bot/line_handlers.py` 實作 `LineBotHandler`：
   - 處理文字（支援一般搜尋、情境選單回應與 Quick Reply）。
   - 處理位置（更新 session 經緯度）。
   - 使用 LINE Messaging API (`/v2/bot/message/reply`) 發送回覆。
4. 在 `src/main.py` 引入並掛載 `app.include_router(line_router)`。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_line_bot.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/config.py src/bot/line_webhook.py src/bot/line_handlers.py src/main.py tests/test_line_bot.py
git commit -m "feat(line): implement LINE Messaging API webhook and event handler with signature verification"
```

---

### Task 5: Telegram 白箱實況副螢幕動態轉播 (Live Trace Monitor)

**Files:**
- Modify: `src/bot/handlers.py:230-295`
- Modify: `src/agent/pipeline.py:200-330`
- Modify: `src/bot/line_handlers.py`
- Test: `tests/test_live_trace_monitor.py`

**Interfaces:**
- Consumes: FSM Stage Progress callback `on_progress(status_text: str)`
- Produces: Telegram 動態跳動訊息（4 動展示：意圖、Places 名單、Tabelog 評分、完成狀態）

- [ ] **Step 1: Write the failing test**

```python
import pytest
from unittest.mock import AsyncMock
from src.agent.pipeline import FoodRecommendationFSM

@pytest.mark.asyncio
async def test_live_trace_monitor_step_callbacks():
    mock_on_progress = AsyncMock()
    # 測試執行 FSM pipeline 時，回調函式按天然節奏接收各階段的結構化文字
    # 驗證至少被呼叫 3 次以上 (意圖、搜尋、Tabelog)
    # mock execution ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_live_trace_monitor.py -v`
Expected: FAIL

- [ ] **Step 3: Implement structured progress streaming in FSM and hook Telegram Live Monitor**

1. 在 `execute_state_1_intent` 完成時回報：目標地點、料理類別。
2. 在 `execute_state_2_places_ladder` 完成時回報：搜尋半徑與尋獲候選店家名單。
3. 在 `execute_state_3_parallel_tabelog` 完成時回報：對齊之 Tabelog 分數與評論數。
4. 當 LINE 收到親友問答時，若有配置 `TELEGRAM_ALLOWED_USER_ID`，透過 Telegram Bot 即時建立轉播泡泡並動態編輯。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_live_trace_monitor.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bot/handlers.py src/agent/pipeline.py src/bot/line_handlers.py tests/test_live_trace_monitor.py
git commit -m "feat(monitor): add white-box live trace monitor streaming intermediate steps to Telegram"
```

---

### Task 6: 全系統整合驗證、文檔同步與 Pre-push 品質保證

**Files:**
- Modify: `docs/ARCHITECTURE.md`
- Modify: `README.md`
- Test: 全量測試套件 (`pytest tests/`)

- [ ] **Step 1: Run complete unit test suite**

Run: `pytest tests/ -v`
Expected: 100% PASS (包含既有測試、Mermaid 測試、LINE 測試、純 Tabelog 測試)

- [ ] **Step 2: Synchronize Documentation**

更新 [`docs/ARCHITECTURE.md`](file:///c:/Users/User/OneDrive/文件/2026/PickForMe/docs/ARCHITECTURE.md) 與 [`README.md`](file:///c:/Users/User/OneDrive/文件/2026/PickForMe/README.md)，更新系統架構圖加入 LINE Webhook 與情境分類引導。

- [ ] **Step 3: Run Pre-push Quality Assurance Steps**

```bash
python -m ruff format src/ tests/
python -m ruff check src/ tests/
python -m mypy src/
pytest
```
Expected: 100% Clean!

- [ ] **Step 4: Final Commit**

```bash
git add docs/ README.md
git commit -m "docs: synchronize architecture and readme with LINE bot dual-platform integration"
```
