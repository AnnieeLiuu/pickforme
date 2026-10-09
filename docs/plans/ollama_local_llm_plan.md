# 🦙 Ollama 本地 LLM 大腦與 IDE Agent Bridge 整合計畫書 (ollama_local_llm_plan.md)

## 🎯 系統目標 (Goal & Background)

當使用者電腦開著 (PC ON) 且啟動本機 Ollama (`qwen2.5:7b`) 時，PickForMe 的 Agent 工具鏈（包括 Telegram 即時美食對話、背景 Prewarm 預熱與灰色地帶實體比對）將優先調用本機 GPU/CPU 大腦推論，實現 **100% 免費、0 API Key 消耗、無 Rate Limit 配額限制**。

當電腦關閉 (PC OFF) 或 Ollama 未啟動時，系統自動無縫降級至雲端 Gemini 2.0 Flash ➡️ Groq 多模型降級鏈。

---

## 🛠️ 核心架構與降級流程 (Architecture & Failover Chain)

```mermaid
flowchart TD
    A[使用者 Telegram 訊息 / Prewarm 任務] --> B[LLM 呼叫入口 call_llm_with_failover]
    B --> C{0.5s Ping 本地 Ollama\nGET http://localhost:11434/api/tags}
    
    C -- 🟢 在線 (PC ON) --> D[呼叫 Ollama OpenAI REST API\nhttp://localhost:11434/v1\nModel: qwen2.5:7b]
    D -- 成功 --> E[回傳推論結果 & 工具調用\n(標記 model = 'ollama (qwen2.5:7b)')]
    
    C -- 🔴 離線/超時 (PC OFF) --> F[1. 呼叫 Gemini 2.0 Flash\n(Google AI Studio 每日 1500 次免費層)]
    D -- 失敗/例外 --> F
    
    F -- 成功 --> G[回傳 Gemini 結果]
    F -- 429 / 失敗 --> H[2. 呼叫 Groq 多模型降級鏈\nqwen27b -> llama70b -> gemma9b]
    H --> I[回傳 Groq 結果]
```

---

## 📋 變更模組與檔案 (Modified Modules)

### 1. 配置管理 ([src/config.py](file:///c:/Users/User/OneDrive/%E6%96%87%E4%BB%B6/2026/PickForMe/src/config.py))
- `OLLAMA_BASE_URL`: `http://localhost:11434`
- `OLLAMA_MODEL`: `qwen2.5:7b`
- `OLLAMA_ENABLED`: `True`

### 2. Agent 大腦模組 ([src/agent/core.py](file:///c:/Users/User/OneDrive/%E6%96%87%E4%BB%B6/2026/PickForMe/src/agent/core.py))
- 實作 `_is_ollama_online()`：發送 `httpx.get("http://localhost:11434/api/tags", timeout=0.5)` 快速探測。
- 在 `call_llm_with_failover` 加入 Step 0：若 Ollama 在線，發送 REST API 請求至 `http://localhost:11434/v1/chat/completions`。
- 若 Ollama 拋出例外，自動捕捉並記錄警告，繼續執行後續 Gemini 2.0 Flash ➡️ Groq 降級。

### 3. 預熱任務整合 ([src/worker/prewarm.py](file:///c:/Users/User/OneDrive/%E6%96%87%E4%BB%B6/2026/PickForMe/src/worker/prewarm.py))
- 更新 `process_next_job`：將 `resolve_entity(..., allow_llm=True)` 恢復支援 LLM（當 PC ON 時走 Ollama；PC OFF 時走 Gemini 2.0 Flash）。

### 4. 單元測試 ([tests/test_agent.py](file:///c:/Users/User/OneDrive/%E6%96%87%E4%BB%B6/2026/PickForMe/tests/test_agent.py))
- 新增 `test_agent_engine_ollama_primary` 與 `test_agent_engine_groq_primary` 單元測試：驗證依設定檔順序優先選擇對應 LLM，並具備完整多級降級容錯。

---

## 📊 實測基準與架構演進 (Benchmarks & Architectural Evolution)

### 1. 硬體限制與延遲實測 (Intel Iris Xe CPU)
- **Ollama 7B (純 CPU)**：單輪推論 >90s，觸發超時。
- **Ollama 3B (純 CPU)**：單輪推論 18~40s，多輪 context 下偶發輸出空白字串問題。
- **雲端 Groq (qwen3.8-27b)**：推論時間 **0.6 ~ 0.8s**，秒級極速回應，配額充沛 (999+ requests/day)。

### 2. 最終決策 (Architecture Decision)
- 將 `LLM_PRIMARY_PROVIDER` 預設設為 `"groq"`，在保證極致使用者體驗（<2秒回傳卡片）的前提下，保留本機 Ollama 作為離線/斷網時的保底備援大腦。

---

## 🧪 驗證與測試指令

```bash
python -m ruff format src/ tests/
python -m ruff check src/ tests/
python -m mypy src/
pytest
```
- 單元測試結果：`35 passed in 3.77s` (100% 通過)
