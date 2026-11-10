# PickForMe LINE Bot 雙平台整合與老饕篩選演化架構設計 (Design Document)

**日期**：2026-10-27  
**狀態**：待審核 (Pending Approval)  
**作者**：Antigravity & Annie  
**所選方案**：方案 A（雙平台並存模式：保留 Telegram 作為白箱觀測副螢幕，新增 LINE Bot 面向親友）

---

## 1. 背景與意圖 (Intent & The Why)

PickForMe 目前以後端 FastAPI 與 Telegram Bot 作為使用者互動介面。為讓家人與朋友在日本旅遊或日常生活中無須下載額外的 Telegram 即可使用美食特派員，系統新增對 **LINE Messaging API** 的支援。

同時，針對過去系統在 Google 搜尋階段「拿 Google 評分預先篩選導致日本職人老店被誤殺」的架構矛盾，本次升級**徹底廢除 Google 端星級預篩，將真正的老饕公式回歸專屬於 Tabelog**，並搭配**「方式 A：情境分類引導（Disambiguation & Quick Reply Guide）」**，達成兼顧免等延遲與 100% Tabelog 純粹口碑推薦。

---

## 2. 目標、非目標與反目標 (Goals, Non-Goals, Anti-Goals)

### 目標 (Goals)
1. **完整支援 LINE 訊息、定位與情境引導**：
   - 支援文字美食查詢（例如：「博多站附近的拉麵」、「推薦心齋橋居酒屋」）。
   - 支援 LINE 原生「傳送位置資訊」（`LocationMessage`），自動記錄經緯度並觸發周邊推薦。
   - **方式 A：主動情境分類引導**：當使用者輸入廣泛詞（如「日料」、「咖啡店」）時，系統不盲目隨機搜尋，而是反問 4 大情境選單（例如甜點下午茶、昭和復古、精品手沖、久坐歇腳），LINE 端提供 Quick Reply 快速按鈕點選。
   - **多輪擴大交通範圍引導**：推薦卡片底部主動提示「可回覆：遠一點也行 (走路15分鐘) / 換口味」，後端階梯自適應擴圈。
2. **徹底根除 Google 評分誤殺，老饕公式專屬於 Tabelog**：
   - **廢除 Google 端以星級與評論數做出的淘汰性預篩**：避免觀光客低評分誤殺了 Tabelog 3.8+ 的日本職人神店。
   - Google 階段回歸純粹職責：地理座標階梯半徑檢索、排除連鎖快餐與非美食地標。
   - 經情境分類引導後，Google 回傳之 4~8 家專門店全員進入 Tabelog 口コミ查核。
   - **真正的老饕公式作用於 Tabelog 端**：
     $$\text{Foodie Score} = \text{Tabelog Rating} + \text{榮譽加分 (百名店/金銀銅賞)} - \text{步行距離懲罰}$$
3. **Telegram 白箱實況轉播副螢幕 (Live Trace Monitor)**：
   - LINE 端親友發問時，Telegram 管理員聊天室以安全頻率（~1s 間隔，共 4 動編輯）同步動態跳動展示：
     - `🧭 第 1 步：意圖分析` ➡️ 抽取的目標地點、料理類別。
     - `🔍 第 2 步：Google Places` ➡️ 鎖定半徑與候選專門店名單。
     - `🍱 第 3 步：Tabelog 對齊` ➡️ 顯示實際比對之分數（⭐️ 3.78）與評論數。
     - `✅ 第 4 步：推薦完畢` ➡️ 耗時 ms 與已回傳 LINE 狀態。
4. **資料庫 ID 相容性遷移**：
   - 將 Neon DB 中 `chat_sessions.chat_id`、`agent_runs.chat_id`、`prewarm_jobs.chat_id` 由數值 `BIGINT` 擴展為 `VARCHAR(64)`，同時相容 Telegram 純數字 ID 與 LINE 字串 User ID。
5. **親友白名單機制**：
   - 支援 `LINE_ALLOWED_USER_IDS` 設定：留空時開放所有加好友的親友使用；填入特定 User ID 時僅允許授權成員使用。
6. **高標準品質防線**：
   - 100% 符合專案 CI 規範（Ruff format, Ruff check, Mypy type check, Pytest）。
   - 包含 Mermaid 文檔保護測試（`test_docs_mermaid.py`）與 LINE 單元測試（`test_line_bot.py`），全數採用 0ms Offline Mock。

### 非目標 (Non-Goals)
- 不更動底層實體對齊器（Entity Resolver）與防偽護欄（Scoped Anchor）核心合約。
- LINE 端第一階段優先以優雅排版的文字卡片 ＋ Quick Reply 回應，不強求繁複的 Flex Message 輪播。

### 反目標 (Anti-Goals)
- 嚴禁破壞任何既有的 Telegram 原生功能或使既有測試失效。
- 嚴禁在測試中發起 Live Network 網路連線或消耗外部 API 額度。

---

## 3. 系統限制與考量 (Constraints & Tradeoffs)

1. **LINE Reply API 費用優勢與時效**：
   - 使用者發問後，Bot 透過 `reply_token` 使用 Reply API 回應完全免費（不佔用官方帳號每月 200 則 Push Message 限制）。
   - `reply_token` 時效約為 60 秒，PickForMe 的端到端推論於 3~5 秒內完成，處於極安全的時效窗口內。
2. **Telegram 實況發訊頻率控制 (Throttling)**：
   - Telegram 限制同一訊息編輯頻率約為 1 秒 1 次。
   - 實況副螢幕僅在「State 1 完成」、「State 2 完成」、「State 3 完成」、「State 4 交付」4 個天然時間點（間隔約 0.8s~1.5s）進行更新，並加入防抖保險，100% 杜絕 HTTP 429。

---

## 4. 詳細模組設計 (Detailed Module Architecture)

### 4.1 設定檔擴充 (`src/config.py`)
```python
# LINE Bot 設定
LINE_CHANNEL_SECRET: str = ""
LINE_CHANNEL_ACCESS_TOKEN: str = ""
LINE_ALLOWED_USER_IDS: str = ""  # 逗號分隔之白名單，空字串代表全開放
```

### 4.2 資料庫綱要調整 (`src/db/schema.sql` 與遷移)
- `chat_sessions`: `chat_id VARCHAR(64) PRIMARY KEY`
- `agent_runs`: `chat_id VARCHAR(64)`
- `prewarm_jobs`: `chat_id VARCHAR(64)`
- 遷移語法：
  ```sql
  ALTER TABLE chat_sessions ALTER COLUMN chat_id TYPE VARCHAR(64) USING chat_id::text;
  ALTER TABLE agent_runs ALTER COLUMN chat_id TYPE VARCHAR(64) USING chat_id::text;
  ALTER TABLE prewarm_jobs ALTER COLUMN chat_id TYPE VARCHAR(64) USING chat_id::text;
  ```
- 更新 `SessionRepository`、`AgentTraceLogger` 與 `AgentEngine` 中 `chat_id` 的型別標註為 `Union[str, int]`。

### 4.3 LINE Webhook 路由 (`src/bot/line_webhook.py`)
- 端點：`POST /api/line/webhook`
- 驗證 Header `x-line-signature` 是否吻合。
- 使用 FastAPI `BackgroundTasks` 異步調用 `LineBotHandler.handle_events(events)`，立即回傳 `{"status": "ok"}` 給 LINE 伺服器防止超時重發。

### 4.4 LINE Bot 處理器與情境引導 (`src/bot/line_handlers.py`)
- 實作 `LineBotHandler` 類別：
  - `handle_events(events: list[dict])`: 走訪 events。
  - `handle_message_event(event)`:
    - 取得 `user_id = event["source"]["userId"]`，驗證白名單。
    - **位置訊息 (`location`)**：更新 `session_repo.update_location(chat_id=user_id, lat=lat, lng=lng)`，並回傳定位成功提示。
    - **文字訊息 (`text`)**：
      - 特殊關鍵字：`說明`、`幫助`、`/start` ➡️ 回傳使用導覽。
      - **廣泛分類引導 (方式 A)**：若偵測到廣義類別（如「咖啡店」、「日料」），直接回傳帶有 Quick Reply 按鈕的情境選項卡（1 甜點布丁、2 昭和復古、3 精品手沖、4 久坐歇腳）。
      - 一般問句與子類別 ➡️ 調用 `agent_engine.run_agent(chat_id=user_id, user_message=text, on_progress=...)` 取得推薦結果。
      - 透過 LINE Messaging API (`/v2/bot/message/reply`) 發送回覆；若超過時效則降級以 push 發送。

### 4.5 搜尋漏斗重構 (Places & Enrichment Refactor)
- **Places 階段 (`places_search.py`)**：廢除星級淘汰篩選，僅做連鎖排除與去重，保送精準專門店家進 Tabelog。
- **Enrichment 階段 (`enrichment.py`)**：老饕加權公式純粹作用於 Tabelog 評分、百名店與步行距離。
- **Telegram 實況串接**：將各 Stage 輸出的中間結構化資訊（抽取的意圖、Google Places 候選名單、Tabelog 分數）透過回調推送到管理員 Telegram，達成 4 動動態跳動展示。

---

## 5. 測試策略 (Test Strategy)

在 `tests/test_line_bot.py` 與 `tests/test_real_world_regressions.py` 實作以下單元測試（全數 Mock，0ms 離線執行）：
1. **Webhook 簽章驗證測試**：驗證合法 HMAC 簽章通過，非法簽章回傳 403。
2. **文字問句推薦流測試**：驗證接收使用者文字 ➡️ Agent 狀態機執行 ➡️ 成功呼叫 LINE Reply API。
3. **定位訊息處理測試**：驗證接收 LINE Location Message ➡️ 正確更新 session 經緯度 ➡️ 回覆確認訊息。
4. **廣義詞情境引導測試**：驗證輸入廣義「咖啡店」或「日料」時觸發情境選單及 Quick Reply Payload。
5. **老饕篩選不殺職人店測試**：驗證 Google 3.3 分但在 Tabelog 3.8 分的職人神店不會在 Places 階段被誤殺，能成功晉級前 3 名。
6. **全鏈路回歸**：包含既有測試與 `test_docs_mermaid.py` 全數保持 100% 通過。
