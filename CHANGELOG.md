# 🍱 Changelog

本專案的所有顯著變更皆記錄於本文件中。格式依據 [Keep a Changelog](https://keepachangelog.com/zh-TW/1.1.0/)，並遵循 [語意化版本 (Semantic Versioning)](https://semver.org/lang/zh-TW/) 規範。

---

## [Unreleased]
### Added
- **Cloud Run 自動化生產環境部署腳本 (`scripts/deploy_cloud_run.sh`)**：支援一鍵讀取環境變數、GCP 容器建構、Cloud Run 服務部署、自動綁定 Telegram Webhook 與 `--dry-run` 模擬演練。
- **GitLab CI/CD Cloud Run 自動部署 Job (`deploy-cloud-run`)**：配置於 `deploy` stage，支援 Google Cloud SDK 認證、手動觸發與 Git Tag 自動發布。
- **Pre-push Hook `--dry-run` 無副作用驗證**：支援評測無磁碟污染執行，根除 Git Push 造成的 Working Tree 髒檔案問題。

---

## [1.2.0] - 2026-10-11

### Added
- **結構化搜尋約束 (Structured Search Constraints)**：將自然語言距離/步行要求（如「走路15分鐘內」）與料理類別解耦，自動換算為物理半徑階梯（`radii_ladder`），Places 搜尋關鍵字 100% 保持純淨。
- **食評雜訊濾網與推薦理由智慧合成 (`ReviewSanitizer`)**：自動剔除生硬的「營業時間 24時間営業」與「交通指南」SEO 雜訊，依結構化權威指標合成高質感老饕推薦理由。
- **Google 端老饕潛力預篩 (State 2.5)**：於 Google 記憶體端依評分、評論數與距離懲罰預篩 4 筆主選與 2 筆備選種子店家。
- **自適應漸進查驗與提早終止 (State 3.1 & 3.2)**：第一梯隊查驗達 2 家高分名店（$\ge 3.45$ 或百名店）立即觸發 Early Stopping，80% 場景 0 額外延遲。
- **老饕加權重排引擎 ($S_i$)**：精選 Top 3 店家並在卡片標題注入 `🥇/🥈/🥉` 榮譽徽章。
- **雙重機械式守門防線**：
  - Git Pre-push Hook (`.git/hooks/pre-push`) 擴充為 5 步守門，加入 Pages 儀表板編譯與防漂移檢查。
  - GitLab CI 新增 `observability-pages-gate`，確保分支與 MR 均具備儀表板編譯驗證。
- **ADR 架構決策記錄體系**：新增 `docs/adr/template.md` 與首篇決策文件 `0001-deterministic-scoped-anchor-guardrails.md`。

### Fixed
- 修復多輪追問時，修飾詞直接串入 Places 關鍵字導致召回火鍋、牛舌與拉麵等脫靶品類的問題。
- 修復模板直出卡片漏出未經清洗之營業時間與交通指引文字問題。

---

## [1.1.0] - 2026-10-10

### Added
- **自研 4 階段確定性有限狀態機 (`FoodRecommendationFSM`)**：取代不可控的黑盒 Prompt Loop，確立 State 1（意圖解析）➔ State 2（Places 階梯擴圈）➔ State 3（Tabelog 平行對齊）➔ State 4（卡片合成與護欄）之確定性流水線。
- **Scoped Anchor 區塊錨點防偽護欄 (`src/agent/pipeline.py`)**：以店家為作用域隔離切片，純 Python 記憶體運算（<0.5ms）杜絕高敏感評分微調幻覺。
- **CI 雙向合約測試防禦 (`tests/test_pipeline.py`)**：自動驗證 System Prompt 排版標記與 Guardrail 正則錨點 100% 同步，徹底根除提示詞漂移。
- **4-Tier 離線評測體系與可觀測性看板**：支援 20 題黃金題庫離線回放與真實對話 Traces 切換，自動生成 GitLab Pages 現代瀑布流儀表板 (`public/index.html`)。

### Changed
- 重構 LLM Client 為多級動態容錯架構（Groq 0.7s 主力 ➡️ Gemini 3.8 Flash 自動備援 ➡️ 本機 Ollama 離線保底）。

---

## [1.0.0] - 2026-10-09

### Added
- **FastAPI 核心後端**：提供 `/health` 健康檢查與 `/webhook/telegram` 端點。
- **Telegram Bot 互動介面**：支援同步 Webhook 訊息即時狀態編輯串流 (`🔍 搜尋...` ➔ `📊 對齊評分...` ➔ 最終推薦卡片)。
- **Neon Cloud Postgres + GCS 冷熱儲存分層**：
  - 熱層：1GB 免費防護、`halfvec` 半精度評論向量庫、7 天快取 TTL 與 14 天 Trace 自動歸檔。
  - 冷層：Snappy Parquet 封存與 DuckDB OLAP 歷程透視 CLI (`scripts/analyze_traces.py`)。
- **雙層漏斗實體對齊器 (`EntityResolver`)**：Level 1 (0ms 電話/核心品牌快篩) + Level 2 (Micro-LLM 灰區語意裁決)。
- **背景預熱 Worker (`src/worker/prewarm.py`)**：支援離峰批量預熱熱門商圈並支援 `/prewarm` 指令。
