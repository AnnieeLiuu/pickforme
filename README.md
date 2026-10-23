# 🍱 PickForMe - 日本旅遊美食 AI 代理人 (Japan Food AI Agent)

[![Python 3.11](https://img.shields.io/badge/Python-3.11-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-green.svg)](https://fastapi.tiangolo.com/)
[![Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Type Checked](https://img.shields.io/badge/mypy-checked-blue.svg)](http://mypy-lang.org/)
[![Tests](https://img.shields.io/badge/pytest-73%20passed-success.svg)](https://docs.pytest.org/)

> **專案定位**：專為日本旅遊打造的高效能智慧美食 Agent 系統。解決遊客外出時「距離近、評分高、有開門、在地評價真實」的痛點。結合 Google Places API、Tabelog 爬蟲與實體比對器、Neon Serverless Postgres 以及 Telegram Bot 介面。

---

## 🌟 核心特色 (Core Highlights)

1. **確定性 4 階段有限狀態機 (4-Stage FSM Pipeline)**：
   - 捨棄脆弱、不可控的傳統多輪 LLM Tool-Calling 迴圈，改採 **`FoodRecommendationFSM`** 狀態機流水線。
   - **State 1 (意圖解析)**：0.3s Micro-LLM 結構化 JSON 提煉（無論地名前後順序或口語句型均可精準解析；若為延續追問則 0.5s 秒答）。
   - **State 2 (階梯搜尋)**：Google Places 智慧階梯搜尋（800m ➔ 1200m ➔ 1800m）並精算步行距離與分鐘數。
   - **State 3 (平行對齊)**：`asyncio.gather` 平行抓取與 Neon 快取比對，遇 403 WAF 阻擋秒級降級至 Web Grounding 備援取得真實評分與百名店標章。
   - **State 4 (單次合成)**：單次 Prompt 生成高美感卡片，日文原名 Code 區塊與步行距離置頂。
2. **4-Tier 離線評測體系與 GitLab Pages 儀表板 (LLMOps Benchmark & Gatekeeping)**：
   - **全面覆蓋型黃金題庫**：收錄 20 組真實旅遊邊界場景（地名錯字、多地標、長尾口感、多輪換批、特定店家追問、缺地標反問）。
   - **量化守門 (Gatekeeper)**：嚴格驗證 Tier 1 意圖抽取率 (100%)、Tier 2 實體解析 F1 (1.00)、Tier 3 護欄合規 (100%)，未達標自動阻擋 Merge。
   - **視覺化儀表板**：自動編譯現代深色科技感 HTML 儀表板 (`public/index.html`)，無縫串接 GitLab Pages 自動化發布。
3. **多輪推薦換批、去重防撞店與自適應擴圈 (Multi-Turn Exploration & Deduplication)**：
   - **已看過店家累積排除**：當使用者詢問「還有更多推薦嗎」或「換一批」時，系統自 `chat_sessions` 自動累積歷史看過之 Place ID，新清單保證不撞店。
   - **偏好修飾詞疊加繼承**：使用者表達「我想找更在地的老店」或「更平價一點」時，自動繼承上一輪地標與料理主軸，並將修飾詞疊加至關鍵字檢索與評論向量重排。
   - **自適應 2500m 擴圈保底**：當原始半徑內候選店家已推薦完畢時，自動擴展搜尋至 2500m，確保旅人不中斷探索。
4. **pgvector Halfvec (768 維) 語意評論重排 (Vector RAG Re-ranking)**：
   - 整合 Neon Postgres `halfvec(768)` 向量儲存，儲存 Tabelog 在地老饕評論嵌入。
   - 支援依使用者個人化口感/偏好修飾詞，進行混合語意相似度加權重排。
5. **冷熱儲存分層與 DuckDB OLAP 追蹤深潛 (Cold Storage & Deep-Dive Analytics)**：
   - **分層歸檔**：線上交易資料留存 Neon 熱層，歷史 Trace 自動排程壓縮轉存至 Parquet 冷層。
   - **終端分析報表**：透過 `scripts/analyze_traces.py` 整合 DuckDB OLAP 引擎，支援一鍵檢視管線總耗時、Token 統計、以及完整 Pipeline 瀑布流（包含各階段 Input, Output, 各自耗時深潛）。
6. **雙平台支付方式標記 (Payment Badges)**：
   - 即時擷取並結構化標註 PayPay、信用卡支援狀況，或友善提醒「現金為主 (建議備足日圓現鈔)」，避免旅人結帳尷尬。
7. **零幻覺確定性數值護欄與防漂移合約測試 (Deterministic Guardrail & Contract Testing)**：
   - **Scoped Anchor 區塊錨點隔離**：自主研發 <0.5ms 毫秒級確定性護欄，按店家獨立切割片段並以前後強語境錨點精確核對校正，確保評分與距離 100% 精準無幻覺。
   - **CI Prompt-Code 合約測試**：在 CI Pipeline 實作雙向合約檢核，若提示詞範本演更引發關鍵字漂移，自動報警阻擋 Commit。
8. **多級雲端+地端 LLM 容錯降級鏈 (Multi-Tier LLM Failover)**：
   - **主力**：Groq `qwen/qwen3.8-27b`（極速推論，延遲 ~0.7s）。
   - **備援**：Gemini 3.8 Flash（Google AI Studio 免費額度）。
   - **離線保底**：本機 Ollama `qwen2.5:3b`（0 API 費用、斷網仍可運行）。
   - 支援動態讀取 `Retry-After` 標頭之退避重試與全自動降級。

---

## 📐 系統架構圖 (Architecture)

> [!TIP]
> 欲查閱端到端「高精度多維全景河流圖 (Global Blueprint River)」、6 維個案深入盤點矩陣與根因排查 SOP，請參閱 **[`docs/SOP_ROOT_CAUSE_DEBUGGING.md`](file:///c:/Users/User/OneDrive/文件/2026/PickForMe/docs/SOP_ROOT_CAUSE_DEBUGGING.md)**。更多架構深潛見 **[`docs/ARCHITECTURE.md`](file:///c:/Users/User/OneDrive/文件/2026/PickForMe/docs/ARCHITECTURE.md)**。

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
        FSM -->|1. 意圖辨識| S1["State 1: 使用者意圖與追問判斷 (繼承歷史/換批/modifier)"]
        S1 -->|2. 地圖搜尋| S2["State 2: Google Places 智慧階梯搜尋<br/>(800m~1800m ➔ 2500m 擴圈保底 + 排除已看過)"]
        S2 -->|3. 雙平台對齊| S3["State 3: Tabelog 雙平台實體對齊<br/>(asyncio.gather 平行處理 + 支付標記 + pgvector 重排)"]
        S3 -->|4. 生成卡片| S4["State 4: 最終美食卡片排版合成<br/>(日文店名Code區塊 + 🚶起點步行距離 + 💳支付)"]
        
        S1 ==>|"【LLM 呼叫 1】0.3s Micro-LLM 結構化意圖抽取"| MultiTierLLM
        S3 -.->|"【LLM 呼叫 2 (可選)】灰區 0.6~0.85 實體比對裁決"| MultiTierLLM
        S4 ==>|"【LLM 呼叫 3】單次 Prompt 高美感推薦卡片生成"| MultiTierLLM
        
        S4 --> Guardrail["🛡️ Scoped Anchor 毫秒級防偽護欄<br/>(0.1ms 數值校驗與自動矯正)"]
        Guardrail --> OutputCard["輸出零幻覺推薦卡片"]
        S4 -.->|LLM全斷線防護| Fallback["純 Python 模板引擎直出保底"]
    end
    
    subgraph StorageTiering ["🗄️ 冷熱資料儲存分層與向量引擎"]
        S3 --> HotDB[("Neon Postgres 熱層: 快取/餐廳/對話歷史")]
        S3 --> VectorDB[("pgvector: halfvec(768) 評論語意索引")]
        FSM -->|OTel 格式 Trace| HotDB
        HotDB -.->|"背景歸檔 (Parquet)"| ColdStorage[("本機/GCS 冷層: Parquet 壓縮檔")]
        ColdStorage --> DuckDBEngine["🦆 DuckDB OLAP 分析引擎 (analyze_traces.py)"]
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
│   ├── analyze_traces.py      # DuckDB OLAP 分析與 Pipeline 深潛報表
│   ├── archive_cold_storage.py# 冷資料 Parquet 批次歸檔工具
│   ├── seed_prewarm_jobs.py   # 日本熱門景點預熱腳本
│   └── setup_webhook.py       # Telegram Webhook 註冊工具
├── src/
│   ├── agent/              # Agent 核心
│   │   ├── pipeline.py     # 4 階段 FSM 狀態機 (FoodRecommendationFSM)
│   │   ├── llm_client.py   # 統一多模型 LLM 客戶端 (Groq/Gemini/Ollama)
│   │   ├── core.py         # AgentEngine Facade 對外入口
│   │   ├── guardrail.py    # 確定性數值護欄
│   │   ├── devops.py       # DevOps 遠端診斷 Subagent
│   │   ├── tools.py        # 工具註冊與執行輔助
│   │   └── trace.py        # OTel 格式 Trace 紀錄器
│   ├── bot/                # Telegram Webhook 與訊息處理
│   ├── db/                 # Neon Postgres 倉儲層 (Session, Restaurant, Vector, Prewarm)
│   ├── services/           # 外部服務 (Google Places, Tabelog, Embedding, Entity Resolver)
│   ├── worker/             # 本機非同步預熱 Worker (SKIP LOCKED 佇列)
│   ├── config.py           # Pydantic Settings 環境設定
│   └── main.py             # FastAPI 啟動入口
├── tests/                  # 71 項單元與整合測試 (Mock 驅動，CI 100% 通過)
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

### 4. 執行背景預熱 Worker 與效能追蹤分析

```bash
# 本地非同步認領 prewarm_jobs 佇列進行快取預熱
python -m src.worker.prewarm

# 檢視最新一次推薦的管線各步驟詳細耗時與 Input/Output
python scripts/analyze_traces.py --latest
```

### 5. 執行 4-Tier 離線評測與生成 GitLab Pages 儀表板

```bash
# 100% 確定性離線回放並產出 public/index.html 儀表板
python scripts/run_eval.py

# 亦可連線真實外部 LLM/API 進行即時抽檢
python scripts/run_eval.py --live
```

---

## 🛡️ 程式碼品質與 CI 驗證 (Quality Assurance)

本專案配置 Git pre-push hook 與 GitLab CI Pipeline，推送前請確保通過以下四大檢查：

```bash
# 1. 程式碼自動格式化
python -m ruff format src/ tests/ scripts/

# 2. 靜態語法檢查與 Linter
python -m ruff check src/ tests/ scripts/

# 3. 嚴格型別檢查
python -m mypy src/

# 4. 單元與整合測試套件
pytest
```

---

## 📜 授權條款 (License)

本專案採用 [MIT License](LICENSE) 授權。
