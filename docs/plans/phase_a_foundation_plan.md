# 🍱 PickForMe - Phase A1 實作計畫：資料夾架構、GitLab CI/CD 與基礎工程

> **目標**：規劃生產級 Python 專案目錄結構、設定 Git 版本控制、配置 GitLab CI/CD (`.gitlab-ci.yml`) 自動化檢查 Pipeline（Linting、Type Check、Pytest），並建立 `pydantic-settings` 與 Neon DB 連線池。

---

## 📐 1. 完整資料夾結構規劃 (Project Directory Layout)

```text
PickForMe/
├── .gitlab-ci.yml            # GitLab CI/CD 流程設定
├── .gitignore                # Git 忽略檔案清單（金鑰、暫存檔）
├── .env.example              # 環境變數範本檔
├── README.md                 # 專案說明檔
├── pyproject.toml            # 套件依賴與 Ruff / Pytest 設定檔
├── requirements.txt          # Python 依賴套件列表
│
├── docs/                     # 專案架構文件與子實作計畫
│   ├── ARCHITECTURE.md       # 系統整體架構規格書 (V2.0)
│   └── plans/                # 各階段子實作計畫 (Implementation Plans)
│       └── phase_a_foundation_plan.md
│
├── config/
│   └── prompts/              # System Prompts Markdown 檔案庫
│       ├── agent_system.md   # Core Agent 系統 Prompt
│       └── entity_verify.md  # 實體匹配 LLM 判斷 Prompt
│
├── src/
│   ├── __init__.py
│   ├── config.py             # pydantic-settings 環境變數集中管理
│   │
│   ├── agent/                # 自研 OpenAI-Format Agent 引擎
│   │   ├── __init__.py
│   │   ├── core.py           # Agent 主迴圈與 LLM 呼叫 (Gemini/Groq)
│   │   ├── tools.py          # Agent 工具定義與註冊器
│   │   └── trace.py          # OpenTelemetry 格式 Trace 寫入器
│   │
│   ├── bot/                  # Telegram Bot 介面層
│   │   ├── __init__.py
│   │   ├── webhook.py        # FastAPI Telegram Webhook 端點
│   │   └── handlers.py       # 位置接收與訊息串流編輯處理器
│   │
│   ├── db/                   # Neon Postgres 資料層
│   │   ├── __init__.py
│   │   ├── connection.py     # psycopg3 AsyncConnectionPool 連線池
│   │   ├── schema.sql        # 資料庫 DDL 腳本 (Phase A/B 表格)
│   │   ├── init_db.py        # 初始化表格與索引建置腳本
│   │   └── repositories/     # Data Access Objects (DAO)
│   │       ├── restaurant.py # 餐廳與 Tabelog 快取存取
│   │       ├── session.py    # 對話 Session 與記憶存取
│   │       └── trace.py      # Trace 記錄存取
│   │
│   └── services/             # 外部服務與核心演算法
│       ├── __init__.py
│       ├── google_places.py  # Google Places API Client & Geocoding
│       ├── tabelog_scraper.py# Tabelog HTML 爬蟲與解析器
│       ├── entity_resolver.py# 電話優先 + 店名正規化 實體匹配器
│       └── prewarm_worker.py # prewarm_jobs 背景預熱 Worker
│
└── tests/                    # 測試集與 Replay Fixtures
    ├── __init__.py
    ├── conftest.py           # Pytest Fixtures
    ├── fixtures/             # Replay 測試資料 (JSON 檔)
    │   ├── google_places.json
    │   └── tabelog_pages.json
    ├── test_config.py        # 設定與環境變數測試
    ├── test_db.py            # Neon DB 連線與 CRUD 測試
    ├── test_resolver.py      # 實體匹配器 F1/規則測試
    └── test_agent.py         # Tool Calling 與 Agent 迴圈測試
```

---

## 🦊 2. GitLab CI/CD Pipeline 配置 (`.gitlab-ci.yml`)

建立自動化 CI/CD Pipeline，只要 `git push` 至 GitLab，就會自動跑 3 個階段的檢查：

```yaml
stages:
  - lint
  - type-check
  - test

default:
  image: python:3.11-slim
  before_script:
    - python -m pip install --upgrade pip
    - pip install -r requirements.txt

# 1. 程式碼風格與 Lint 檢查 (Ruff)
lint-job:
  stage: lint
  script:
    - pip install ruff
    - ruff check src/ tests/

# 2. 靜態型別檢查 (Mypy)
typecheck-job:
  stage: type-check
  script:
    - pip install mypy
    - mypy src/

# 3. 自動化單元與整合測試 (Pytest)
pytest-job:
  stage: test
  script:
    - pytest tests/ -v --junitxml=report.xml
  artifacts:
    when: always
    reports:
      junit: report.xml
```

---

## 👨‍💻 User Review Required

> [!IMPORTANT]
> 1. **Git / GitLab 初始化**：我們會執行 `git init` 並在本地建立分支。完成 Phase A1 驗證後，你可以將此 Repo 關聯至你的 GitLab 遠端庫 (`git remote add origin ...`) 並發起首次 Push。
> 2. **GitLab CI/CD 變數**：在 GitLab CI 測試跑 DB 相關測試時，測試預設會使用 Mock/Fixtures，不需在 GitLab 上填真正的 Neon 密鑰，保證 CI 零成本與安全性。

---

## 🛠️ Proposed Changes

### [NEW] `.gitignore`
防止金鑰與暫存檔上傳至 GitLab：
```gitignore
# API 金鑰與環境變數檔 (極重要)
.env
*.env.local
gemini-key.txt
groq-key.txt

# Python 快取與虛擬環境
__pycache__/
*.py[cod]
.pytest_cache/
.mypy_cache/
.venv/
venv/

# 測試與 CI 產生檔案
report.xml
coverage.xml
.coverage
```

### [NEW] `pyproject.toml`
設定 Ruff、Mypy 與 Pytest：
```toml
[tool.ruff]
line-length = 100
target-version = "py311"

[tool.pytest.ini_options]
minversion = "7.0"
addopts = "-ra -q"
testpaths = ["tests"]
asyncio_mode = "auto"
```

### [NEW] `src/config.py`
使用 `pydantic-settings` 進行集中管理。

### [NEW] `src/db/connection.py` & `src/db/schema.sql`
Neon Postgres 非同步連線池與 Phase A 核心 DDL 腳本。

---

## 🧪 Verification Plan

### Automated Tests
本地與 GitLab CI 將執行相同指令：
```bash
# 1. Style & Syntax
ruff check src/ tests/

# 2. Type Safety
mypy src/

# 3. Unit Tests
pytest tests/ -v
```

### Manual Verification
1. 檢查資料夾結構是否符合上述規劃。
2. 執行 `git status` 確認敏感檔（如 `.env`）被安全過濾。
3. 執行測試確認本地通過後即可 Push 至 GitLab 觸發 Pipeline。
