# 0002. 採用三維度確定性 Agent Harness（QuickPassGate、Pydantic 自癒與 JSONB 雙層記憶）強化管線可靠性

- **狀態 (Status)**：Accepted
- **日期 (Date)**：2026-11-10
- **決策者 (Deciders)**：AI Agent & Developer

## 1. 背景與挑戰 (Context & Problem Statement)
在生產級日本美食推薦場景中，單純依賴自然語言 Prompt 與無狀態 LLM 呼叫面臨三大系統性挑戰：
1. **入口延遲與 Token 浪費**：使用者常輸入「你好」、「謝謝」、「換一批」或「第 2 家可以刷卡嗎？」等高確定性短句，或是惡意長文本 / Prompt Injection。若每則訊息皆無差別送入 Stage 1 Intent LLM（甚至在 `chitchat` 分支觸發第 2 次 LLM 呼叫），不僅浪費 Token 更增加 0.5s~1.5s 延遲。
2. **結構化輸出脆弱性與硬性條件脫鉤**：`json.loads()` 無法保證枚舉值與數值邊界（如 `target_index` 必須為 `1~3`、經緯度必須在日本國境內）；同時，當使用者要求「可刷卡 / PayPay」或「不吃牛」時，純靠評分排序可能將「僅收現金 (`accepts_cash_only=True`)」的店家排在第一名。
3. **記憶視窗遺忘 (Context Rot)**：對話歷史 (`conversation_history`) 僅保留最近 3 輪以控制 Context 長度，導致使用者在第 1 輪宣告的飲食禁忌（如「不吃牛」）或支付偏好在第 4 輪後被滑動視窗洗掉，且單純輸入「換一批」時缺乏結構化的上一輪料理與地點狀態可繼承。

## 2. 考慮的替代方案 (Considered Options)
- **方案 A：改寫為全自主 ReAct Tool-Calling Loop + 獨立 User Profile 資料表**
  - 由單一大模型透過多輪 Function Calling 自主決定何時查記憶、何時搜餐廳、何時過濾條件，並新增 PostgreSQL `user_preferences` 資料表。
  - *缺點*：開放式 ReAct Loop 容易產生多輪無效重試導致 Telegram 端延遲突破 10 秒；且修改雲端 DB DDL Schema 需額外維護遷移腳本與跨表 Join 成本。
- **方案 B：僅在 Webhook 處理器 (`handlers.py`) 硬編碼正則，並把歷史對話拉長至 20 輪**
  - 在 Telegram Handler 攔截簡單指令，並透過拉長 `conversation_history` 保留使用者偏好。
  - *缺點*：未來整合 LINE Bot 時需重複實作攔截邏輯，且無法寫入 `AgentTraceLogger` 追蹤；拉長歷史對話會造成嚴重的 Token 膨脹與 Context Rot。
- **方案 C：FSM 內聚式三維度 Agent Harness（選用方案）**
  - 在維持 4-Stage FSM 確定性控制流的前提下，於 Stage 1 前置注入 **0ms `QuickPassGate`**、於 `LLMClient` 注入 **Pydantic v2 Schema + Self-Correction 自癒重試迴圈**、於 `chat_sessions.last_recommended_ids` JSONB 內實作免改 DDL 的 **Two-Tier Memory（Working + Long-Term Memory）**，並在 Stage 3 注入 **Deterministic Constraint Sensor**。

## 3. 決策內容 (Decision Outcome)
我們決定採用 **方案 C：FSM 內聚式三維度 Agent Harness**，核心架構設計如下：

1. **0ms `QuickPassGate` 與安全絆線 (`src/agent/gate.py`)**：
   - 於 Stage 1 入口以嚴格正則錨點 (`^...$`) 攔截超長輸入 (`>500` 字)、Prompt Injection 越獄字串、`__healthcheck__`、純問候/道謝（0-LLM 直接回傳模板），並將「第 N 家追問」、「純換一批」與「日本經緯度座標」0ms 轉換為結構化 Intent，僅將含複雜修飾語的自然語言（如「換一批更平價的拉麵」）放行給 LLM。
2. **Pydantic v2 合約與 Self-Correction 自癒迴圈 (`src/agent/schemas.py`, `src/agent/llm_client.py`)**：
   - 以 `IntentOutput`、`GeocodeArgs`、`SearchNearbyArgs`、`TabelogInfoArgs` 定義嚴格型別與日本地理邊界（`20.0<=lat<=46.0, 122.0<=lng<=154.0`）。
   - 當模型輸出觸發 `ValidationError` 時，自動將錯誤明細組裝回對話進行 **最多 1 次 Self-Correction Retry**；若仍失敗則平穩退回確定性安全保底（Fail-Safe Fallback），保證 Pipeline 永不因格式異常崩潰。
3. **免 DDL 遷移之雙層記憶與 Stage 3 硬性條件感測 (`src/db/repositories/session.py`, `src/agent/stages/enrichment.py`)**：
   - 複用 `chat_sessions.last_recommended_ids` JSONB 結構，分離出短期 `working_memory` (`last_cuisine`, `last_location`) 與長期 `long_term_preferences` (`dietary_restrictions`, `payment_preferences`)，100% 向下相容舊版格式。
   - Stage 3 `apply_deterministic_constraints` 自動比對候選店家屬性與使用者硬性條件，對違規店家標記 `constraint_violation = True` 並於 `calculate_foodie_score` 扣減 `0.45` 分重新排序。

## 4. 效益與代價 (Consequences & Trade-offs)
### 正面效益 (Positive Impacts)
- **極致效能與成本節省**：常見問候/道謝、店號追問與純換一批達成 **0ms 意圖解析與 0 Token 消耗**（問候語更直接省下 2 次 LLM 呼叫）。
- **零停機向下相容**：完全不需執行 SQL `ALTER TABLE`，雙層記憶即可跨輪、跨日持久化飲食禁忌與支付偏好。
- **軟性降權防空清單**：Stage 3 採 `-0.45` 重度扣分降權而非粗暴刪除，既確保符合刷卡/飲食條件的店家優先出線，又防止在極端偏遠地區因全數店家資料未知而導致推薦清單被清空。

### 潛在代價與局限 (Negative / Trade-offs)
- **自癒重試延遲**：當模型首次輸出違反 Pydantic Schema 時，觸發 1 次 Self-Correction 重試會增加約 0.4s~0.7s 延遲（但僅在模型出錯時觸發，常態下為 0 額外開銷）。

### 防禦與緩解機制 (Mitigation Strategies)
- **上限鎖定 (`max_retries=1`)**：自癒迴圈嚴格限制最多重試 1 次，超限立即走純程式碼安全保底，杜絕無限重試風暴。
- **全量離線回歸測試**：透過 `tests/test_quick_pass_gate.py`、`tests/test_schemas_and_self_correction.py`、`tests/test_two_tier_memory_and_constraints.py` 與 `tests/test_real_world_regressions.py` 鎖定所有邊界條件。

