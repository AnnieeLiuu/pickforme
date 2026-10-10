# 🍱 PickForMe - 日本旅遊美食 AI 代理人 (Japan Food AI Agent)

[![Python 3.11](https://img.shields.io/badge/Python-3.11-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-green.svg)](https://fastapi.tiangolo.com/)
[![Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Type Checked](https://img.shields.io/badge/mypy-checked-blue.svg)](http://mypy-lang.org/)
[![Tests](https://img.shields.io/badge/pytest-54%20passed-success.svg)](https://docs.pytest.org/)

> **專案定位**：專為日本旅遊打造的高效能智慧美食 Agent 系統。解決遊客外出時「距離近、評分高、有開門、在地評價真實」的痛點。結合 Google Places API、Tabelog 爬蟲與實體比對器、Neon Serverless Postgres 以及 Telegram Bot 介面。

---

## 🌟 核心特色 (Core Highlights)

1. **確定性 4 階段有限狀態機 (4-Stage FSM Pipeline)**：
   - 捨棄脆弱、不可控的傳統多輪 LLM Tool-Calling 迴圈，改採 **`FoodRecommendationFSM`** 狀態機流水線。
   - **State 1 (意圖解析)**：0.3s Micro-LLM 結構化 JSON 提煉（無論地名前後順序或口語句型均可精準解析；若為延續追問則 0.5s 秒答）。
   - **State 2 (階梯搜尋)**：Google Places 智慧階梯搜尋（800m ➔ 1200m ➔ 1800m）並精算步行距離與分鐘數。
   - **State 3 (平行對齊)**：`asyncio.gather` 平行抓取與 Neon 快取比對，遇 403 WAF 阻擋秒級降級至 Web Grounding 備援取得真實評分與百名店標章。
   - **State 4 (單次合成)**：單次 Prompt 生成高美感卡片，日文原名 Code 區塊與步行距離置頂。
2. **零幻覺確定性數值護欄與防漂移合約測試 (Deterministic Guardrail & Contract Testing)**：
   - **Scoped Anchor 區塊錨點隔離**：自主研發 <0.5ms 毫秒級確定性護欄，按店家獨立切割片段並以前後強語境錨點精確核對校正，確保評分與距離 100% 精準無幻覺，徹底杜絕內文非評分數值誤傷。
   - **CI Prompt-Code 合約測試**：在 CI Pipeline 實作雙向合約檢核，若提示詞範本演更引發關鍵字漂移，自動報警阻擋 Commit，根除傳統正則護欄隨時間腐化的維護盲點。
3. **多級雲端+地端 LLM 容錯降級鏈 (Multi-Tier LLM Failover)**：
   - **主力**：Groq `qwen/qwen3.8-27b`（極速推論，延遲 ~0.7s）。
   - **備援**：Gemini 3.8 Flash（Google AI Studio 免費額度）。
   - **離線保底**：本機 Ollama `qwen2.5:3b`（0 API 費用、斷網仍可運行）。
   - 支援動態讀取 `Retry-After` 標頭之退避重試與全自動降級。
4. **高精準實體匹配器 (Hybrid Entity Resolver)**：
   - **電話優先 (Phone-First)**：日文電話號碼完全比對直接獲得 100% 信心度。
   - **店名正規化**：支援漢字新舊字體轉換（如「藏」轉「蔵」、「之」轉「の」）、半角假名轉換與分店後綴消除。
   - **灰區裁決**：0.60 ~ 0.85 相似度區間支援微型 Prompt 驗證。
5. **極致 Telegram 行動端體驗**：
   - **兩段式即時進度更新**：鎖定地點 ➔ 彙整評分 ➔ 直出卡片，100% 規避 Telegram 429 頻率限制。
   - **一秒複製店名**：日文原名獨立置於 Code 區塊（例：\`寿々喜家 本店\`），點擊即可複製並切換至 Google Maps 導航。
   - **歷史記憶與追問**：推薦店家編號即時存入 `chat_sessions` JSONB，支援「那第 2 家有開嗎？」即時問答。

---

## 📐 系統架構圖 (Architecture)

```mermaid
graph TD
    User(["Telegram App / 每次發送位置與問句"]) -->|Webhook| Server["FastAPI 後端 @ Cloud Run"]
    Server --> FSM["自研 4 階段 FSM 狀態機 (FoodRecommendationFSM)"]
    
    subgraph MultiTierLLM ["🧠 多級 LLM 推論核心 (LLMClient)"]
        Groq["🥇 Groq qwen3.8-27b (0.7s 極速主力)"]
        Gemini["🥈 Gemini 3.8 Flash (429 自動備援)"]
        Ollama["🥉 本機 Ollama qwen2.5 (離線保底)"]
        Groq -.->|429/異常降級| Gemini
        Gemini -.->|離線降級| Ollama
    end

    subgraph DeterministicPipeline ["⚡ 確定性 4 階段狀態機流水線"]
        FSM -->|1. 意圖辨識| S1["State 1: 使用者意圖與追問判斷"]
        S1 -->|2. 地圖搜尋| S2["State 2: Google Places 智慧階梯搜尋<br/>(TextSearch 專門店 ➔ 800m~1800m)"]
        S2 -->|3. 雙平台對齊| S3["State 3: Tabelog 雙平台實體對齊<br/>(asyncio.gather 3~5家平行處理 + Grounding 備援)"]
        S3 -->|4. 生成卡片| S4["State 4: 最終美食卡片排版合成<br/>(日文店名Code區塊 + 🚶步行距離)"]
        
        %% 明確標註 LLM 在哪裡被調用
        S1 ==>|"【LLM 呼叫 1】0.3s Micro-LLM 結構化意圖抽取"| MultiTierLLM
        S3 -.->|"【LLM 呼叫 2 (可選)】灰區 0.6~0.85 實體比對裁決"| MultiTierLLM
        S4 ==>|"【LLM 呼叫 3】單次 Prompt 高美感推薦卡片生成"| MultiTierLLM
        
        S4 --> Guardrail["🛡️ Scoped Anchor 毫秒級防偽護欄<br/>(0.1ms 數值校驗與自動矯正)"]
        Guardrail --> OutputCard["輸出零幻覺推薦卡片"]
        S4 -.->|LLM全斷線防護| Fallback["純 Python 模板引擎直出保底"]
    end
    
    subgraph StorageTiering ["🗄️ 冷熱資料儲存分層"]
        S3 --> HotDB[("Neon Postgres 熱層: 快取/餐廳/對話歷史")]
        S3 -.->|原始網頁存檔| ColdStorage[("GCS 冷層: Parquet / 壓縮 JSON")]
        FSM -->|OTel 格式 Trace| HotDB
    end

    subgraph AsyncWorker ["⚙️ 背景非同步處理"]
        HotDB -->|prewarm_jobs 佇列| LocalWorker["本機 Worker / 家用 IP 批次預熱"]
    end
```

---

## 📁 專案目錄結構 (Project Layout)

```text
PickForMe/
├── config/
│   └── prompts/            # Agent System Prompt 與比對提示詞
├── docs/
│   ├── ARCHITECTURE.md     # 系統主架構圖與 Neon DB Schema DDL
│   ├── tabelog_structure.md# Tabelog DOM 解析規則與 CSS 選擇器
│   └── plans/              # 各階段實作計畫書與進度
├── scripts/
│   ├── agent_bridge_server.py # IDE Agent 遠端協同橋接伺服器
│   ├── seed_prewarm_jobs.py   # 日本熱門景點預熱腳本
│   └── setup_webhook.py       # Telegram Webhook 註冊工具
├── src/
│   ├── agent/              # Agent 核心
│   │   ├── pipeline.py     # 4 階段 FSM 狀態機 (FoodRecommendationFSM)
│   │   ├── llm_client.py   # 統一多模型 LLM 客戶端 (Groq/Gemini/Ollama)
│   │   ├── core.py         # AgentEngine Facade 對外入口
│   │   ├── devops.py       # DevOps 遠端診斷 Subagent
│   │   ├── tools.py        # 工具註冊與執行輔助
│   │   └── trace.py        # OTel 格式 Trace 紀錄器
│   ├── bot/                # Telegram Webhook 與訊息處理
│   ├── db/                 # Neon Postgres 倉儲層 (Session, Restaurant, Prewarm)
│   ├── services/           # 外部服務 (Google Places, Tabelog, Entity Resolver)
│   ├── worker/             # 本機非同步預熱 Worker (SKIP LOCKED 佇列)
│   ├── config.py           # Pydantic Settings 環境設定
│   └── main.py             # FastAPI 啟動入口
├── tests/                  # 48 項單元與整合測試 (Mock 驅動，CI 100% 通過)
└── pyproject.toml          # Ruff, Mypy 與專案相依設定
```

---

## 🚀 快速上手 (Quick Start)

### 1. 安裝環境相依

```bash
# 建議使用 Python 3.11 虛擬環境
pip install -r requirements.txt
```

### 2. 環境變數設定 (`.env`)

複製 `.env.example` 為 `.env` 並填入憑證：

```ini
# FastAPI
ENVIRONMENT=development
PORT=8000

# Neon Serverless Postgres
DATABASE_URL=postgresql://user:password@ep-cool-cloud.neon.tech/neondb?sslmode=require

# LLM Providers (支援自動容錯)
LLM_PRIMARY_PROVIDER=groq
GROQ_API_KEY=gsk_...
GEMINI_API_KEY=AIza...
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:3b

# Google API
GOOGLE_PLACES_API_KEY=AIza...

# Telegram Bot
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_ALLOWED_USER_ID=0     # 0 代表允許所有人，或輸入特定 Telegram User ID
TELEGRAM_WEBHOOK_SECRET=your_secret
```

### 3. 本地啟動伺服器

```bash
# 啟動 FastAPI 伺服器
python run_server.py
```

### 4. 執行背景預熱 Worker

```bash
# 本地非同步認領 prewarm_jobs 佇列進行快取預熱
python -m src.worker.prewarm
```

---

## 🛡️ 程式碼品質與 CI 驗證 (Quality Assurance)

本專案配置 Git pre-push hook 與 GitLab CI Pipeline，推送前請確保通過以下四大檢查：

```bash
# 1. 程式碼自動格式化
python -m ruff format src/ tests/

# 2. 靜態語法檢查與 Linter
python -m ruff check src/ tests/

# 3. 嚴格型別檢查
python -m mypy src/

# 4. 單元與整合測試套件
pytest
```

---

## 📜 授權條款 (License)

本專案採用 [MIT License](LICENSE) 授權。
