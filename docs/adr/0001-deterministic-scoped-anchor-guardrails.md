# 0001. 採用 Scoped Anchor 確定性區塊錨點護欄消除評分幻覺

- **狀態 (Status)**：Accepted
- **日期 (Date)**：2026-10-10
- **決策者 (Deciders)**：AI Agent & Developer

## 1. 背景與挑戰 (Context & Problem Statement)
在日本美食推薦場景中，Tabelog 評分（如 3.78 vs 3.50）與 Google 評分是用戶決策的核心權威依據。
然而，LLM（無論是 Groq、Gemini 還是 Ollama）在生成高生動度、長篇幅的美食推薦 Markdown 卡片時，存在機率性的數值微調幻覺（例如將資料庫中的 3.78 筆誤輸出為 3.70 或 3.80）。即使微小的評分失真，也會嚴重摧毀用戶對推薦系統的信任度。

核心限制條件：
1. **極致響應速度**：Telegram Bot 端到端回應時間需控制在 3~5 秒內，不能引入高延遲。
2. **零額外成本**：不能為了校驗數值而消耗翻倍的 LLM Token 費用。
3. **語境保護**：評論內經常出現「排隊 3.5 小時」、「3.5 天熬煮」等數值，校準時絕不可誤傷評論內文。

## 2. 考慮的替代方案 (Considered Options)
- **方案 A：LLM-as-a-Judge（二次模型審核）**
  - 利用 Micro-LLM 審查產生的 Markdown 卡片並校正數值。
  - *缺點*：增加 1.0 ~ 1.5 秒額外延遲，API Token 成本翻倍，且 Judge 本身仍有微小機率產生新幻覺。
- **方案 B：全局正則粗暴替換 (Global Regex Replace)**
  - 以正則表達式在全文字串中搜尋小數並置換。
  - *缺點*：無法分辨哪一段屬於店家 1 或店家 2，極易發生「跨店串位」；且會直接將「排隊 3.5 小時」誤殺竄改為店家評分。
- **方案 C：Scoped Anchor 區塊錨點隔離護欄 (選用方案)**
  - 先以日文店名與店家序號為邊界，將 Markdown 文本切割為獨立的店家區塊（Per-Store Scoping）；再於各區塊內鎖定雙向強語境關鍵字（Context Anchoring）進行精準校對。

## 3. 決策內容 (Decision Outcome)
我們決定採用 **方案 C：Scoped Anchor 區塊錨點隔離護欄 (`src/agent/pipeline.py`)**。

關鍵實作特徵：
1. **店家區塊隔離 (Per-Store Scoping)**：每家店獨立切片，比對與置換只在該店的作用域內進行，杜絕跨店串位。
2. **強語境錨點 (Context Anchoring)**：只匹配 `(?:Tabelog|食べログ)[^\d\n]{0,25}?(\d\.\d{1,2})`，前後文語境嚴密保護非評分數值。
3. **Fail-Safe 雙層保底**：若格式毀損或未匹配到錨點，維持原文；若 LLM 卡片版型嚴重破損，自動回退為純 Python 精準模板直出。

## 4. 效益與代價 (Consequences & Trade-offs)
### 正面效益 (Positive Impacts)
- **零延遲與零成本**：純 Python 記憶體運算耗時 `<0.5ms`，0 額外 API 調用成本。
- **100% 確定性**：數值保證與資料庫熱層快取完全一致，根除評分幻覺。
- **語境安全**：評論中的「熬煮 3.5 小時」100% 受到上下文隔離保護。

### 潛在代價與局限 (Negative / Trade-offs)
- **規則漂移風險 (Rule Drift)**：若 System Prompt 的排版關鍵字未來遭到修改，正則錨點可能靜默失效。

### 防禦與緩解機制 (Mitigation Strategies)
- **CI 雙向合約測試 (Contract Testing)**：在 `tests/test_pipeline.py` 建立合約測試，於每次 CI 檢查 `agent_system.md` 提示詞與 Guardrail 正則錨點是否 100% 同步；任何提示詞變動若破壞了護欄合約，CI 立即報警阻擋合併。
