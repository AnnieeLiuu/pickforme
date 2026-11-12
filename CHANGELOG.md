# 🍱 Changelog

本專案的所有顯著變更皆記錄於本文件中。格式依據 [Keep a Changelog](https://keepachangelog.com/zh-TW/1.1.0/)，並遵循 [語意化版本 (Semantic Versioning)](https://semver.org/lang/zh-TW/) 規範。

---

## [Unreleased]

---

## [1.3.1] - 2026-11-12

### Fixed
- **特色導覽地名誤抓 (`src/agent/gate.py` - `clean_explore_location`)**：修復「有什麼特色美食？」、「我想找特色美食」等未指定地名問句被擷取為 `location="有什麼"` / `"我想找"`，導致導覽卡片出現錯誤地名的問題。現改為剝除口語前綴/尾綴並拒絕疑問詞，無地名時自 `working_memory` 繼承或反問地點（保護「那須」、「要町」等真實地名）。
- **過期導覽選單 (`src/db/repositories/session.py`, `src/agent/stages/intent.py`)**：修復 `suggested_cuisines` 永不清除，使數輪後單獨回覆「1」被映射回過期特色料理的問題。一般搜尋完成即清除導覽選單；無有效選單時「1/2/3」退回為追問上一輪第 N 家店。

---

## [1.3.0] - 2026-11-11

### Added
- **混合雙層日本在地特色美食與飲食文化導覽引擎 (`src/services/cuisine_guide.py`, `config/local_specialties.json`)**：
  - 支援 `explore_cuisine` 探索意圖（如「神保町有什麼特色美食？」、「淺草名物」）：當使用者尚未決定吃哪種料理時，先為其導覽當地最具代表性的 3 大名物料理與歷史文化背景。
  - **Tier 1 內建考據知識庫 (`config/local_specialties.json`)**：收錄神保町、淺草、月島、築地、新宿、橫濱、京都祇園、大阪難波、博多等熱門商圈之飲食發源史與名物，0ms 秒回、0 Token 且 100% 零歷史幻覺。
  - **Tier 2 Yahoo Japan 真實網頁 Grounding**：針對未收錄之冷門日本車站，自動抓取日本網頁真實名物摘要並以 `AreaSpecialtyGuide` Pydantic Schema 結構化提煉，嚴禁模型憑空捏造歷史年份。
  - **兩階段無縫互動與「直接推薦」快速通道**：導覽卡片整合 `working_memory.suggested_cuisines`，支援下一輪直接輸入 `1` / `2` / `3` 鎖定該特色料理進入 Tabelog 雙源獵店，或輸入「直接推薦」/「隨便」跳過分類直接檢索周邊最高分名店。
- **Tabelog 雙源獵店與反向種子保送架構 (`src/services/tabelog_scraper.py`, `src/agent/stages/places_search.py`)**：
  - 新增 `TabelogScraper.discover_area_top_stores()`，於 State 2 平行主動獵取指定地區與料理之 Tabelog 高分名店與百名店名單，並反查 Google Places 錨定精準經緯度與步行距離（標記 `tabelog_seed = True`）。
  - 重構 `prefilter_potential_foodie_places()`，賦予 Tabelog 原生種子與百名店 `+10.0` 絕對保送權重，並建立日本在地老店保護曲線（壓平 Google > 4.4 星觀光客店之超額權重），徹底根除「日本 Tabelog 3.7+ 百名店在 Google 僅 3.8 星而在 State 2 遭淘汰」之盲點。
- **PydanticAI 三大設計模式吸收 (`src/agent/schemas.py`, `src/agent/stages/intent.py`, `src/agent/pipeline.py`)**：
  - **Schema-Driven Prompt**：新增 `IntentOutput.build_json_schema_prompt()`，由 Pydantic `Field(description=...)` 單一真相來源動態生成 `INTENT_PARSER_SYSTEM_PROMPT` 規範。
  - **Semantic Result Validator**：於 `IntentOutput` 實作 `@model_validator(mode="after")` 跨欄位語意驗證，自動清理空白字串、補齊預設料理詞並攔截空問題之 `follow_up` 觸發自癒重試。
  - **Typed RunContext Container**：新增 `FSMRunContext` 強型別執行期上下文容器，收斂 `FoodRecommendationFSM.run_pipeline()` 跨階段狀態傳遞。
- **冷儲存雙重水位線自動歸檔與 Cloud Run 防刪安全鎖 (`src/services/cold_storage.py`, `src/bot/handlers.py`)**：
  - 新增 `auto_archive_by_watermark()`，於每次對話結束後非阻塞背景巡檢雙重水位線（預設熱層保留最新 `500` 筆 Runs 或 Neon DB 總容量上限 `400 MB`），僅將超出門檻之最舊紀錄轉為 Parquet 歸檔。
  - 新增 Cloud Run 無狀態容器防刪安全鎖（偵測 `K_SERVICE` 環境變數）：若 GCS 上傳未就緒而退回容器本地目錄，自動攔截 `DELETE FROM agent_runs` 並在 Telegram `/archive` 明確告警，杜絕容器縮容導致歷史資料蒸發。
- **Pre-Classifier `QuickPassGate` 0ms 入口快篩與安全絆線 (`src/agent/gate.py`)**：在進入 Stage 1 LLM 前執行 0ms 確定性正則快篩，攔截 Prompt Injection 越獄字串與超長輸入 (`>500` 字元)，並針對 `__healthcheck__`、純問候、純道謝提供 0-LLM 秒回，以及針對「指定店號追問 (`follow_up_index`)」、「純換一批 (`pure_more`)」、「日本經緯度座標 (`coords_search`)」直接路由。
- **Pydantic v2 強型別合約與 Self-Correction 自癒重試迴圈 (`src/agent/schemas.py`, `src/agent/llm_client.py`)**：定義 `IntentOutput` 與 Tool 參數 Schema；當模型輸出違反 JSON 格式或 Pydantic 邊界時，自動捕獲 `ValidationError` 並回饋模型進行 1 次自癒重試，重試失敗時平滑降級至安全預設值。
- **Two-Tier Memory 雙層記憶架構 (`src/db/repositories/session.py`)**：免改 SQL DDL，於 `chat_sessions.last_recommended_ids` JSONB 實作短期 `working_memory` (`last_cuisine`, `last_location`) 與跨輪 `long_term_preferences` (`dietary_restrictions`, `payment_preferences`)，支援「換一批」無縫繼承與長期飲食/支付偏好持久化。
- **Stage 3 硬性條件防呆感測器 (`src/agent/stages/enrichment.py` - `apply_deterministic_constraints`)**：結合當輪 `modifier` 與跨輪長期偏好，對違反硬性條件（如要求刷卡/PayPay 卻僅收現金，或忌牛/忌生食）之候選店家自動標記 `constraint_violation` 並於老饕評分扣減 `0.45` 分降權重排。
- **ADR 0002 架構決策文件**：新增 `docs/adr/0002-deterministic-agent-harness-and-two-tier-memory.md`。
- **實體對齊都道府縣一票否決硬性護欄 (Prefecture & Distance Hard-Cap Guardrail)**：於 `EntityResolver` 實裝都道府縣代碼（`kanagawa` vs `tokyo`）與 5km 物理距離硬性護欄，跨區候選直接一票否決。
- **跨語言多候選仲裁池 (Multi-Candidate Arbitration Pool)**：打破羅馬拼音西文名稱在純字元比對下的單一候選鎖定瓶頸，將通過地理護欄之 Top 3 候選全面納入仲裁，精準召回正牌老店（荒井屋 本店）。
- **行政區平滑降級階梯 (Hierarchical Area Relaxation)**：細部商圈未命中時，優先退回大城市（如「橫濱」、「新宿」），避免裸店名引發全日本 SEO 雜訊污染。
- **Google Cloud Run (asia-northeast1 東京) 生產環境上線實裝**：完成 Dockerfile 容器化與 Artifact Registry 標準遷移，正式將 PickForMe 部署至 Google Cloud Run (`https://pickforme-api-297554628555.asia-northeast1.run.app`)，並綁定 Telegram Webhook，達成 24 小時無休隨時隨地即問即答。
- **Cloud Run 自動化生產環境部署腳本 (`scripts/deploy_cloud_run.sh`)**：支援一鍵讀取環境變數、GCP 容器建構、Cloud Run 服務部署、自動綁定 Telegram Webhook 與 `--dry-run` 模擬演練。
- **GitLab CI/CD Cloud Run 自動部署 Job (`deploy-cloud-run`)**：配置於 `deploy` stage，支援 Google Cloud SDK 認證、手動觸發與 Git Tag 自動發布。
- **Pre-push Hook `--dry-run` 無副作用驗證**：支援評測無磁碟污染執行，根除 Git Push 造成的 Working Tree 髒檔案問題。

### Fixed
- 修復橫濱公園搜尋時，因純英文店名 `Araiya` 觸發純店名備援，導致店家被跨縣市張冠李戴替換為東京都澀谷區「新井屋」的嚴重實體漂移問題。

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
