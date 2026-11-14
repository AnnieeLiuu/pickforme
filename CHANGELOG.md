# 🍱 Changelog

本專案的所有顯著變更皆記錄於本文件中。格式依據 [Keep a Changelog](https://keepachangelog.com/zh-TW/1.1.0/)，並遵循 [語意化版本 (Semantic Versioning)](https://semver.org/lang/zh-TW/) 規範。

---

## [Unreleased]

### Fixed
- **推薦卡片護欄強化、結構化實體比對與評測真機接線 (`src/agent/validator.py`, `src/agent/stages/synthesis.py`, `src/agent/stages/enrichment.py`, `src/services/review_sanitizer.py`, `src/services/entity_resolver.py`, `src/eval/evaluator.py`)**：
  - 統一 `split_card_store_blocks` / `is_store_matched` 區塊切分與非店名細節反引號白名單過濾，避免誤殺 `PayPay` / `現金` 等提示；移除 Layer 3 評分套話並在無真實中文短評時採「寧缺勿濫」策略省略違規理由行。
  - 新增背景日文評論批次繁中翻譯（`_translate_reviews_batch_fail_open`），保留 `review_text_ja` 並採 Fail-Open 降級。
  - `EntityResolver._verify_with_llm` 升級為 `call_structured_json` + `EntityVerifyResponse` 驗證；`EvaluationEngine.evaluate_tier3_guardrail` 改為接線真實 `render_template_fallback_card` / `sanitize_card_ratings` / `validate_card_tiered` 管線。
- **修復雲端 LLM 模型配置錯誤並新增現役模型監測機制 (`src/agent/llm_client.py`, `src/agent/schemas.py`, `tests/test_real_world_regressions.py`, `scripts/check_live_models.py`)**：
  - **Case 35 現役模型恢復**：將先前誤改為已下線型號之配置恢復為實測可用模型（Groq: `qwen/qwen3.8-27b` ➔ `openai/gpt-oss-120b` ➔ `openai/gpt-oss-20b`；Gemini: `gemini-3.8-flash`），解決雲端全數 404/400 導致請求墜落至本機 Ollama 卡死的問題。
  - **Groq JSON 模式與 `null` 陣列零重試保護**：`call_structured_json()` 自動確保 `messages` 含 `"json"` 關鍵字以防 Groq 400；`IntentOutput` 新增 `_coerce_none_to_list` 驗證器，將模型回傳之 `null` 陣列即時轉為 `[]`，消除無謂重試延遲。
  - **Case 35.2 環境變數熱切換模型 (`src/config.py`, `.env.example`, `src/agent/llm_client.py`)**：新增 `GROQ_MODELS` 與 `GEMINI_MODEL` 設定欄位及 `get_active_groq_models()` / `get_active_gemini_model()`，支援 Cloud Run 等雲端環境免改 Code 直接透過環境變數熱切換模型清單與主模型。
  - **Live 模型存活診斷腳本 (`scripts/check_live_models.py`)**：提供一鍵對 Groq `/v1/models` 與 Gemini 端點發送真實探針之檢查腳本，提早預警官方模型下線。

### Added
- **漸進式技能架構 (`SkillLoader`)、受限追問 Tool Loop 與 `trip-scoping` 前期收斂 (`src/agent/skill_loader.py`, `config/skills/`, `src/agent/stages/intent.py`, `src/agent/validator.py`, `src/db/repositories/session.py`)**：
  - 將 `config/prompts/agent_system.md` 瘦身為核心憲章，抽離場景與日本用餐禮儀技能至 `config/skills/scenarios/*.md` 與 `config/skills/etiquette/*.md`，由 `SkillLoader` 依店家特徵與場景確定性按需注入（`max_skills=2`）。
  - 為 `execute_follow_up` 新增 `validate_follow_up_response`（含單店／多店評分矯正、偽造 Maps 連結與連續日文段落攔截）與受限 2 輪 `FollowUpDecision` Tool Loop（`detail_lookup` / `nearby_pairing`）。
  - 新增 `trip-scoping` 複合模糊需求收斂對話（上限 `scoping_turns < 2`），與既有廣域地名 `generate_disambiguation_prompt` 互補。
- **全國車站優先 (Station-First) 與反幽靈車站商圈保底 (Anti-Ghost-Station Navigation) (`src/agent/stages/places_search.py`, `src/agent/stages/enrichment.py`, `src/agent/stages/synthesis.py`, `tests/test_real_world_regressions.py`)**：
  - **Case 34.1 都會區真實車站優先採用**：徹底根除都會區（新宿、澀谷、銀座等）因非廣域景區而拋棄 Tabelog 最近車站資料之痛點。當店家爬取到「西武新宿駅徒歩3分」時，不再覆蓋為「從『新宿』步行約 7 分鐘」，一律優先展示「從『西武新宿站』步行約 3 分鐘」，全日本都會與景區無差別享受精確站點指引。
  - **Case 34.2 核心腹地 (<400m) 向後相容**：店家無細部車站且距商圈定位點近時，維持簡潔清晰之點對點步行描述（如「從『新宿』步行約 4 分鐘 (320m)」）。
  - **Case 34.3 反幽靈車站商圈街區保底 (Anti-Ghost-Station)**：使用者查詢景點或商圈地標（如「清水寺」、「道頓堀」）且無車站資料時，**絕不盲目拼接「站」捏造「清水寺站」或「道頓堀站」等虛假車站**；距定位點較遠 (>400m) 時，自動自店家日文地址精準提煉在地子街區（如「位於清水寺「產寧坂」 (距定位點約 650m)」），並具備防結巴機制（避免輸出「位於道頓堀「道頓堀」」）。
  - **Case 34.4 使用者指定站點精準尊重**：使用者明確指定具體車站出口（結尾為「站/駅/口/Station」）時，一律尊重起點標註（如「從『新宿站』步行約 4 分鐘 (280m)」）。
  - **Case 34.5 全管線與 Prompt 協同守門**：解除 `_align_walking_and_station` 僅限景區之門檻；於 State 4 推薦卡片提示詞注入通用交通導航指令，強制要求 LLM 100% 忠實照錄店家資料之步行描述，嚴禁擅自簡化為模糊的大區域。
- **4-Tier 評測體系重構：消滅「自己考自己」假象，導入真實 Replay Cassettes 回放機制 (`src/eval/evaluator.py`, `tests/fixtures/eval_llm_cassettes.json`)**：
  - **根除 `actual = expected` 評測作弊**：重構 `EvaluationEngine.evaluate_tier1_intent`，徹底移除直接賦值假評測；採用錄製卡匣（Replay Cassettes）驅動受測 FSM，真實執行 `QuickPassGate` 0ms 快篩、`IntentOutput.model_validate()` 結構合約校驗、`validate_semantic_consistency()` 語意補齊、Working Memory 跨輪繼承與座標解析。
  - **0 依賴離線隔離**：注入 `InMemorySessionRepo` 與 `MockPlacesClient.geocode_location`，在保證 0 雲端 API 消耗與 0ms 離線速度的同時，真實驗證解析管線邏輯。
- **架構決策理性反思與文件代碼漂移消滅 (`README.md`, `src/agent/llm_client.py`, `docs/ARCHITECTURE.md`)**：
  - **Over-engineering 疑慮理性回應**：於 `README.md` 深度闡述「確定性 4-Stage FSM 抑制 15s 延遲與防死循環」、「Tabelog 爬蟲 85% WAF 阻擋率催生住宅 IP 預熱與 Grounding 備援」、「pgvector halfvec 與 DuckDB 零成本冷熱分層湖倉架構」之踩坑數據，並主動提出「極簡 MVP 減法思維」（大膽標註初期可刪減 Parquet/DuckDB 與評論向量），展現架構成熟度。
  - **幽靈模型與目錄校正**：將虛構模型名稱（`gemini-3.8-flash`、`qwen3.8-27b`）全面修正為官方現役真實模型（`gemini-2.0-flash`、`llama-3.3-70b-versatile`）；校正目錄樹漂移（`guardrail.py` ➔ `validator.py`、`runner.py` ➔ `evaluator.py`），同步更新測試數量徽章至 233 題。
- **非日本境外地點地理邊界護欄 (Non-Japan Out-of-Region Guardrail) (`src/agent/schemas.py`, `src/agent/stages/intent.py`, `src/agent/pipeline.py`, `src/agent/gate.py`, `src/bot/handlers.py`, `tests/test_real_world_regressions.py`)**：
  - **Case 33 境外地理阻斷與 0ms 早期返回**：針對「台北 拉麵」、「首爾 烤肉」、「香港 點心」等非日本境內地點搜尋，於 `IntentOutput` 擴充 `intent="out_of_region"`；State 1 判定後立即短路返回，徹底杜絕下游 Google Places 境外查詢、防範 Tabelog Entity Resolver 跨國幻覺比對（如誤將台北一真亭對應至東京已閉店之一真亭），保證 0 Places API、0 Tabelog Scraper 與 0 第二道 LLM 消耗。
  - **異國料理防誤殺與漢字同名消歧義 (Contrastive Prompting)**：提示詞明確區隔「在日外國菜」（如「新宿 台灣料理」、「東京 韓式烤肉」屬於正常 `search`，嚴禁判為境外）與「同名漢字地名」（如松山、板橋、三重預設為日本，僅在附帶境外特徵詞如「台北松山」時才判為 `out_of_region`）。
  - **國境經緯度與 Telegram 定位防禦 (`is_in_japan_bounds`)**：確立日本國境經緯度判定（含極南沖繩/與那國島至極北北海道，並精準排除落於矩形內的朝鮮半島區間）；QuickPassGate 對境外座標 0ms 秒回；Telegram 原生地理位置訊息（📎 傳送定位）在境外時拒絕寫入 DB 並發送親切防禦指引。
- **多店橫向對比、行程停靠站識別與營業狀態防踩雷整合 (`src/agent/schemas.py`, `src/agent/stages/intent.py`, `src/services/google_places.py`, `src/agent/stages/enrichment.py`, `src/agent/stages/synthesis.py`)**：
  - **Case 30 多店橫向對比 (Multi-Store Comparison)**：`IntentOutput` 擴充 `target_indices: list[int]`，支援「比較 1 跟 2」、「1 跟 3 哪家比較好」等跨店追問；自動從對話 Session 記憶體撈出候選店家資料，由 LLM 生成條理分明的對照矩陣（風味口感、預算、排隊步程、客群建議）。
  - **Case 31 行程停靠站與多地名精準定錨 (Itinerary Stopover Anchoring)**：於意圖提示詞中注入行程分析原則，當句子出現起訖與中途停靠（如「從東京搭新幹線去京都，在名古屋轉車吃鰻魚」），精準定錨用餐中停地 `location="名古屋"`，杜絕起點終點誤奪。
  - **Case 32 營業時間與即時防踩雷 (Operating Hours Sensor)**：Places Client 納入 `currentOpeningHours` 與 `regularOpeningHours`；於 Deterministic Constraint Sensor 中加入 `time_constraint`（宵夜、早餐、現在）硬性判定；若店家明確已打烊 (`open_now is False`) 則降權扣分，並於推薦卡片注入 `⏰ 營業狀態：營業中 / ⚠️ 今日已打烊 / 公休` 徽章。
- **非餐飲購物與生活服務 0ms 領域邊界果斷拒絕守門 (`src/agent/schemas.py`, `src/agent/stages/intent.py`, `src/agent/pipeline.py`, `tests/test_real_world_regressions.py`)**：
  - **Case 29 迴歸防禦**：針對「東京 眼鏡」、「新宿 藥妝」、「原宿 球鞋」等非餐飲購物查詢，於 `IntentOutput` 擴充 `intent="non_food"` 與 `non_food_item` 抽取，徹底將非食品實體購物與生活服務自 `chitchat` 與 `search` 剝離。
  - **0ms Early Return 防穿透**：在 State 1 意圖判定為 `non_food` 時立即提早返回，杜絕後續 Google Places Geocoding 地名洩漏、避免無座標被誤覆寫為 `need_location`，且完全不調用第二道 LLM Chitchat 陪聊，直接秒級提供親切且堅定的領域導正訊息。
  - **文化名產與美食設施邊界保護**：嚴格規範食品伴手禮（如「抹茶甜點名菓」）與美食體驗設施（如「拉麵博物館」）繼續走標準文化探索與美食檢索，杜絕誤殺。

### Fixed
- **Langfuse 5.x OpenTelemetry 原生觀測架構適配 (`src/services/telemetry.py`)**：
  - 全面遷移至 Langfuse 5.x 之 OpenTelemetry 原生觀察體系，以 `propagate_attributes` 自動傳遞 `session_id`/`user_id`/`tags`，以 `start_observation(as_type="chain"|"span"|"generation")` 精準生成 Trace、Span 與 Generation，徹底解決舊版 client API 不相容問題。

### Added
- **Langfuse 線上可觀測性與即時鏈路追蹤整合 (`src/services/telemetry.py`, `src/agent/pipeline.py`, `src/agent/llm_client.py`, `src/config.py`)**：
  - **遵循 Langfuse 官方最佳實踐 (`github.com/langfuse/skills`)**：以對話輪次為單位建立動詞優先的靜態 Trace (`handle-user-request`)，綁定 `session_id` 與 `user_id`；以靜態 Span（`classify-intent`, `search-places`, `enrich-restaurants`, `synthesize-recommendations`, `handle-chitchat`, `explore-culture`）細緻劃分 4-Stage 狀態機與各分支；底層 LLM 呼叫記錄為 Generation，精準掌握模型降級、Token 消耗（`input`/`output`/`total`）、延遲分佈與壞案例標註。
  - **ContextVars 無侵入式傳遞**：透過 `contextvars.ContextVar` 追蹤當前活躍的 Trace/Span，完全無需改動現有 Service 與 Stage 函式簽名。
  - **Fail-Open 優雅降級與 0ms 離線 Mock 原則**：當未配置金鑰或處於 `ENVIRONMENT=test` 時自動啟用輕量級 NoOp 物件，確保單元測試 0 外部請求、0 毫秒延遲，遙測失敗時絕不中斷業務對話。
  - **FastAPI 生命週期清理**：於 `src/main.py` 關閉階段註冊 `telemetry.flush()`，確保緩衝佇列安全送出。
- **微型 LLM 動態地理歧義消解與多聚落子商圈解析 (`src/agent/schemas.py`, `src/agent/stages/intent.py`, `src/agent/core.py`, `src/bot/handlers.py`)**：
  - **Dynamic LLM-driven Disambiguation**：升級 `IntentOutput` 與 `INTENT_PARSER_SYSTEM_PROMPT`，由微型 LLM 世界知識在單一推理回合中（0 額外網路延遲）動態判定廣域或模糊多聚落區域（如「靜岡溫泉街」、「北海道」、「長野滑雪」、「九州」），自動產出 2~3 個具代表性具體子商圈（含 `name`、`desc`、`search_loc`），回傳互動式選單並存入 Working Memory。
  - **雙軌 Prewarm 門面解耦與容錯直通 (Fail-Open)**：在 `AgentEngine` 增設 `disambiguate_area` 門面方法並賦予 2 秒超時防護，Telegram `/prewarm` 指令於大區域或模糊地名時自動提示互動式選單；若遇 LLM 逾時或異常則安全降級 (fail-open) 照常排入預熱，杜絕例外崩潰。
- **FastAPI Webhook 背景非同步派發 PrewarmWorker (`src/bot/handlers.py`, `tests/test_prewarm_wide_area.py`)**：
  - 在 Telegram Bot Handler 建立預熱任務後，自動透過 `asyncio.create_task` 派發 `PrewarmWorker.process_next_job()` 在背景執行，徹底解決無獨立常駐 Worker 時任務卡在 `pending` 的問題，預熱完成自動發送 Telegram 推播卡片。
- **All-in-One 全方位快取預熱與三維度官方權威深度爬蟲 (`src/worker/prewarm.py`, `src/services/authority_scraper.py`, `src/services/cuisine_guide.py`)**：
  - **All-in-One 雙軌並發快取**：升級 `PrewarmWorker`，以 `asyncio.gather` 同步並行執行商圈前 20 家餐廳與 Tabelog 評分快取，以及在地三大文化維度 (`food` 名物、`drink` 飲品/地酒、`souvenir` 必買伴手禮) 之深度預熱。
  - **部分故障隔離 (Partial Failure Isolation)**：文化導覽若遇外部網站超時或解析失敗，以獨立 try-except 隔離，確保餐廳資料庫快取照常完成並安全降級推播，任務永不崩潰。
  - **官方權威深度爬蟲 (`OfficialAuthorityScraper`)**：於 Yahoo Japan Grounding 檢索時強制注入限定 `site:lg.jp OR site:or.jp`，鎖定日本各都道府縣廳、市町村役所與地方觀光推進機構（公益社團法人），徹底排除商業農場文；實裝 `curated` ➔ `grounding` ➔ `fallback` 三階品質降級與 SQL 優先級排序 (`source_tier = 'curated' > 'grounding' > 'fallback'`)。
  - **豐富型 Telegram 整合卡片**：預熱完成後主動推播精準摘要，呈現 20 家餐廳成果與三維度前 3 大名物亮點，並附帶一鍵複製指令引導。
- **Telegram 廣域預熱互動防護與對話狀態守門 (`src/bot/handlers.py`, `src/db/repositories/session.py`)**：
  - **縣級大區主動分流**：針對 `/prewarm <都道府縣>`（如 `/prewarm 北海道`），0ms 攔截 Places 盲搜，即刻回傳 3 大核心商圈（札幌、函館、小樽）與「全部 / 取消」互動選單。
  - **工作記憶防卡死守門**：透過 Working Memory 暫存 `pending_prewarm_options`，支援點選序號或批次「全部」排入佇列；使用者輸入「取消」乾淨退場；若轉向其他指令或一般問句，系統自動重置清理待確認狀態，絕不卡死對話流。
- **在地特色導覽種子資料庫冪等同步腳本 (`scripts/seed_specialties_to_db.py`, `src/db/repositories/specialty_guide.py`)**：
  - 實作 CLI 冪等同步工具，將 `config/local_specialties.json` 中已考據之 4 個核心區域（箱根、輕井澤、金澤、宇治）共 12 筆三維度導覽完整匯入 Neon DB `area_specialty_guides`。
  - 採用 `ON CONFLICT (area_key, category) DO UPDATE` 策略，支援 `--dry-run` 預覽與全量同步，並於資料庫層落實 `source_tier` 等級管理。
- **Pages 變更強制 Playwright 實機驗收規範與 JS 語法契約守門 (`AGENTS.md`, `tests/test_observability_sync.py`)**：
  - 在 [`AGENTS.md`](file:///c:/Users/User/OneDrive/文件/2026/PickForMe/AGENTS.md) 全局憲章第 3 條明確納入強制性守門規範：「Pipeline 狀態、Payload、模板或前端邏輯演進時，MUST 同步更新 `src/eval/reporter.py`、重新生成 `public/index.html`，並**強制使用 Playwright 進行瀏覽器實機驗收（確保 0 Console Errors、DOM 元素完整渲染且按鈕點擊交互響應正常）**，嚴禁盲信靜態 HTML 而未經實機瀏覽器驗收」。
  - 在 [`tests/test_observability_sync.py`](file:///c:/Users/User/OneDrive/文件/2026/PickForMe/tests/test_observability_sync.py) 新增 `test_dashboard_html_encoding_and_js_syntax` 契約測試，自動調用 Node VM 編譯測試 Dashboard 中的 JavaScript 代碼塊，確保在 Git Pre-push Hook 與 CI 階段即刻攔截任何語法中斷或編碼缺陷。
- **全鏈路生產技術棧全景圖入庫與架構文件同步 (`docs/assets/`, `docs/ARCHITECTURE.md`)**：
  - 將對齊 JamWithAI 生產級標準的 PickForMe 技術棧圖存入儲存庫：包含完全獨立的向量圖檔 [`docs/assets/production_ai_agent_stack.svg`](file:///c:/Users/User/OneDrive/文件/2026/PickForMe/docs/assets/production_ai_agent_stack.svg)、互動式 HTML 預覽檔 [`docs/assets/production_ai_agent_stack.html`](file:///c:/Users/User/OneDrive/文件/2026/PickForMe/docs/assets/production_ai_agent_stack.html) 以及原始參考圖 [`docs/assets/production_ai_agent_stack_reference.jpg`](file:///c:/Users/User/OneDrive/文件/2026/PickForMe/docs/assets/production_ai_agent_stack_reference.jpg)。
  - 於 [`docs/ARCHITECTURE.md`](file:///c:/Users/User/OneDrive/文件/2026/PickForMe/docs/ARCHITECTURE.md) 第 1 節（系統整體架構）嵌入該圖，完整呈現從 Entry ➔ Agent Core ➔ Data ➔ Quality ➔ Runtime ➔ Ship & Deploy 之全鏈路貫通動線。
- **Trace-to-River Adapter 實裝與動態可觀測性穿透 (`src/eval/templates/river_dashboard.html`, `public/index.html`, `tests/test_observability_sync.py`)**：
  - **真實線上對話動態串接**：左欄由原先 7 筆靜態寫死案例，全面升級為由 `pickforme-eval-summary-payload` 動態解析 Neon DB 匯入之 15 筆真實生產對話 (`live_runs`)，精確呈現使用者提問、時間戳記、執行總延遲、使用模型與步驟數。
  - **雙模式自由切換 Tab**：新增 `📡 即時線上 (15)` 與 `🎭 架構示範 (7)` 切換標籤，預設載入真實問答，同時保留 7 筆經典情境展示。
  - **FSM 河流圖動態點亮與節點審計穿透**：點擊任一真實問答即動態點亮該次執行所流經的 FSM 節點（包括 0ms QuickPassGate 快篩短路、文化特色導覽直出旁道、Places 800m 階梯檢索與 Tabelog 爬蟲接地），點擊各節點即可在右側審計抽屜查閱真實的 `tool_input`、`tool_output`、步驟耗時與 Telegram 最終推播卡片預覽。
  - **新增可觀測性契約測試 (`tests/test_observability_sync.py`)**：新增 `test_trace_to_river_adapter_integration` 單元測試，保證 Pages 儀表板模板與真實 Trace Payload 之契約永不脫節。

### Changed
- **徹底廢除硬編碼行政區字典並恪守防膨脹憲章 (`src/agent/stages/intent.py`, `docs/ARCHITECTURE.md`)**：
  - **Eradication of Hardcoded Mapping**：徹底移除 `PREFECTURE_DISAMBIGUATION_MAP` 靜態字典，全面恪守 `AGENTS.md` 憲章中「嚴禁硬編碼跨語言異體字或同義字字典，必須採倒排索引動態構建或交由語言模型/外部標準 API 解析」之鐵則。
  - **向後相容過渡橋接**：保留輕量化 `is_prefecture_level_area` 輔助函式，確保既有模組與呼叫端平滑過渡。

### Fixed
- **修復 Pages 中控台總記錄筆數徽章動態連動 (`src/eval/templates/river_dashboard.html`, `public/index.html`)**：
  - 修正左欄標題旁徽章過去寫死為「共 8 筆記錄」之缺陷，賦予 `id="badge-total-count"` 並與模式切換（即時線上 15 筆 vs 架構示範 7 筆）動態連動，經 Playwright 實機驗收切換交互與渲染皆正常。
- **修復 Pages 中控台 HTML5 標準信封與字元編碼截斷 (`src/eval/templates/river_dashboard.html`, `public/index.html`, `tests/test_eval.py`)**：
  - 補齊標準 `<!DOCTYPE html>` 與 `<meta charset="UTF-8">`，修復先前缺少 HTML5 宣告導致 Chromium 在解析 JS 字串內多位元組 Emoji (`📍` / `🍜` / `🍱`) 時產生 `SyntaxError: Invalid or unexpected token` 阻斷腳本執行的缺陷。
  - 清理 `switchCase` 函式內部冗餘代碼塊，全面恢復 JS 正常執行與 `window.onload` 自動初始化。
  - 經 Playwright 實機點擊自動化測試，驗證雙模式切換（即時線上 15 筆 vs 架構示範 7 筆）與各節點審計抽屜穿透皆 100% 正常響應。
- **架構流程圖語意完整性契約測試與 Hook 守門防護 (`tests/test_docs_mermaid.py`, `.git/hooks/pre-push`)**：
  - 新增 `test_architecture_fsm_mermaid_semantic_consistency` 契約測試：自動掃描 `docs/ARCHITECTURE.md` Section D 之 Mermaid 流程圖，強制驗證必須涵蓋 4 大 State 子圖、地理三場景 (`is_prefecture_level_area` / `is_wide_area_origin`)、消費品類三分支 (`food` / `drink` / `souvenir` / `resolve_card_category`) 與 Scoped Anchor 等核心節點，徹底杜絕「只改文字說明、漏改流程圖」之架構圖漂移。
  - 於 Git Pre-push Hook Step 6 注入 Mermaid 流程圖實質改動檢核與預警。
- **雙階日本地理導航架構：縣級超大區域主動收斂 (策略 A) + 景區最近車站導引 (方案 2) (`src/agent/stages/`, `src/services/tabelog_scraper.py`)**：
  - **Tier 1 縣級超大區域主動收斂 (策略 A)**：
    - 終結在「靜岡」、「長野」、「北海道」、「沖繩」等百公里廣域對著市公所盲搜之弊端。
    - 於 State 1 識別縣級廣域，動態產出分區特色選單（例如：1. 靜岡車站 / 2. 濱松 / 3. 沼津港）。
    - 於 Working Memory 暫存 `suggested_locations` 與 `pending_cuisine`，支援下一輪輸入「1/2/3」或城鎮名（如「濱松」）無縫繼承料理需求並鎖定檢索。
  - **Tier 2 景區最近車站提取與防錯位步行導引 (方案 2)**：
    - 徹底根除在「箱根」、「輕井澤」等度假山區輸出「從『箱根』步行約 15 分鐘 (1200m)」等抽象中心點問題。
    - 擴充 Tabelog 交通手段爬蟲與 Yahoo Grounding 車站正則萃取（`STATION_WALK_PATTERN`、`STATION_DIST_PATTERN`），支援 `正面口`、`南口`、`A1出口` 等日文出口格式，格式化為「從『強羅站』步行約 5 分鐘」。
    - 在廣域景區模式下，嚴禁將廣域中心點大跨度物理距離（如 2800m）拼接到最近車站步行後面，杜絕「5分鐘走 2.8 公里」之荒謬米數錯位 (Usain Bolt Bug)。
    - 於 `calculate_foodie_score` 豁免廣域景區與持最近車站店家的中心點距離扣分（`dist_penalty = 0.0`），防止名店因遠離町役場被無辜降權。
    - 都心商圈（新宿、澀谷、池袋、銀座）與具體車站（箱根湯本站）維持標準點對點導航，絕不誤殺降級。
  - **計畫制定與 Subagent 交叉審查規範固化 (`AGENTS.md`)**：
    - 於 `AGENTS.md` 頒布「計畫制定與 Subagent 交叉審查規範」，強制所有架構變更前由獨立 Research Subagent 全面嚴格審查，並保持文件於 88 行精簡狀態。
- **在地三大維度文化探索與 Neon DB 自動寫回生長 (`LocalSpecialtyGuideService`, `area_specialty_guides`)**：
  - 擴充文化導覽支援「吃 (`food`)」、「喝 (`drink` - 名茶/地酒/喫茶)」、「伴手禮 (`souvenir` - 名菓/禮盒)」三大面向。
  - 新增金澤（加賀棒茶、能登地酒、長生殿落雁、金箔長崎蛋糕）與宇治（玉露、手刷濃茶、茶蕎麥、茶糰子、濃茶貓舌餅）全套考據資料。
  - 新增 `SpecialtyGuideRepository` 與 Neon Postgres 資料表 `area_specialty_guides`：Tier 2 Yahoo Grounding 查證之新車站通過品質審查後自動寫回資料庫，未來查詢 0ms / 0 Token 秒回。
  - 支援跨輪無縫切換（問完「金澤吃什麼」接著問「那喝的呢？」或「有什麼伴手禮？」）與類別自適應之「直接推薦」。
  - 徹底根除 `_KANJI_FOLD_MAP` 字元土炮字典，改採 O(1) 雙向別名倒排索引（Inverted Index）；移除 `GooglePlacesClient` 脆弱之 `category_type_map`，全面改由 TextSearch 搜尋專門店。
  - 於 `AGENTS.md` 頒布「🚫 Anti-Hardcoded Mapping」憲章，杜絕 AI 見招拆招硬寫置換字典之壞習慣。
- **State 4 推薦卡片類別措辭自適應微調 (`src/agent/stages/synthesis.py`, `src/agent/pipeline.py`)**：
  - 新增 `resolve_card_category()`，支援當推薦標的為伴手禮（`souvenir`，如和菓子、名產）或茶飲（`drink`，如名茶、喫茶、地酒）時，自動將卡片稱謂由「候選餐廳」自適應為「名店／老舖／茶房」。
  - 於 LLM Prompt 注入專屬類別指導語，並同步更新純文字保底模板，防止和菓子老舖與茶房被誤稱為餐廳。

### Fixed
- **專案文檔與雲端部署環境配置校正 (`.gitlab-ci.yml`, `docs/ARCHITECTURE.md`, `README.md`, `.env.example`, `docs/tabelog_structure.md`)**：
  - 修復 `.gitlab-ci.yml` 部署環境變數名稱漂移 (`GOOGLE_MAPS_API_KEY` ➔ `GOOGLE_PLACES_API_KEY`，`TELEGRAM_ALLOWED_USERS` ➔ `TELEGRAM_ALLOWED_USER_ID`)。
  - 精簡 `docs/ARCHITECTURE.md`：移除過期開發排程甘特圖與衝突的舊版 README 預覽，更新模型名稱為 Qwen 3.8 27B。
  - 校正 `README.md`：修正 `scripts/archive_to_cold.py` 名稱與測試通過數 (`183 passed`)，建立正式 `LICENSE` (MIT) 消除死連結。
  - 同步 `.env.example`：清理未實裝的 LINE 變數，補齊現行 `LLM_PRIMARY_PROVIDER` 與 Ollama 離線推論設定。
- **雙階地理導航邊界缺陷與選單交叉污染修復 (`src/db/repositories/session.py`, `src/services/tabelog_scraper.py`, `src/agent/stages/places_search.py`)**：
  - 修復 `SessionRepository.update_working_memory` 多輪選單殘留污染：更新 `suggested_cuisines` 時互斥清空 `suggested_locations` 與 `pending_cuisine`，反之亦然，徹底杜絕特色選單遭過期縣級選單劫持。
  - 修復 `tabelog_scraper` 交通手段正則誤判：嚴格要求具備 `(?:徒歩|歩き|歩いて)` 步行關鍵字，明確排除 `(?:バス|車|タクシー|お車)` 等大眾運輸或開車，杜絕將公車 15 分鐘誤判為步行。
  - 擴充 `is_wide_area_origin` 正則剝離 `(?:溫泉|温泉)` 後綴，確保「草津溫泉」、「有馬溫泉」、「由布院溫泉」等熱門溫泉勝地能正確識別為廣域景區。
- **預設 FSM 接線持久化與土炮字典拔除 (`src/agent/pipeline.py`, `src/services/cuisine_guide.py`)**：
  - 將 `SpecialtyGuideRepository` 納入 `FoodRecommendationFSM` 預設依賴注入接線，確保生產環境 Cloud Run 自動具備 Neon DB 本地名物持久化寫回能力。
  - 徹底移除 `_KANJI_FOLD_MAP` 硬編碼漢字置換字典，改由別名倒排索引（Inverted Index）動態解析，並新增接線驗證單元測試。

---

## [1.3.1] - 2026-11-12

### Fixed
- **特色導覽地名誤抓 (`src/agent/gate.py` - `clean_explore_location`)**：修復「有什麼特色美食？」、「我想找特色美食」等未指定地名問句被擷取為 `location="有什麼"` / `"我想找"`，導致導覽卡片出現錯誤地名的問題。現改為剝除口語前綴/尾綴並拒絕疑問詞，無地名時自 `working_memory` 繼承或反問地點（保護「那須」、「要町」等真實地名）。
- **過期導覽選單 (`src/db/repositories/session.py`, `src/agent/stages/intent.py`)**：修復 `suggested_cuisines` 永不清除，使數輪後單獨回覆「1」被映射回過期特色料理的問題。一般搜尋完成即清除導覽選單；無有效選單時「1/2/3」退回為追問上一輪第 N 家店。

---

## [1.3.0] - 2026-11-11

### Added
- **Langfuse 線上可觀測性與即時鏈路追蹤整合 (`src/services/telemetry.py`, `src/agent/pipeline.py`, `src/agent/llm_client.py`, `src/config.py`)**：
  - **遵循 Langfuse 官方最佳實踐 (`github.com/langfuse/skills`)**：以對話輪次為單位建立動詞優先的靜態 Trace (`handle-user-request`)，綁定 `session_id` 與 `user_id`；以靜態 Span（`classify-intent`, `search-places`, `enrich-restaurants`, `synthesize-recommendations`, `handle-chitchat`, `explore-culture`）細緻劃分 4-Stage 狀態機與各分支；底層 LLM 呼叫記錄為 Generation，精準掌握模型降級、Token 消耗（`input`/`output`/`total`）、延遲分佈與壞案例標註。
  - **ContextVars 無侵入式傳遞**：透過 `contextvars.ContextVar` 追蹤當前活躍的 Trace/Span，完全無需改動現有 Service 與 Stage 函式簽名。
  - **Fail-Open 優雅降級與 0ms 離線 Mock 原則**：當未配置金鑰或處於 `ENVIRONMENT=test` 時自動啟用輕量級 NoOp 物件，確保單元測試 0 外部請求、0 毫秒延遲，遙測失敗時絕不中斷業務對話。
  - **FastAPI 生命週期清理**：於 `src/main.py` 關閉階段註冊 `telemetry.flush()`，確保緩衝佇列安全送出。
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
- **Langfuse 線上可觀測性與即時鏈路追蹤整合 (`src/services/telemetry.py`, `src/agent/pipeline.py`, `src/agent/llm_client.py`, `src/config.py`)**：
  - **遵循 Langfuse 官方最佳實踐 (`github.com/langfuse/skills`)**：以對話輪次為單位建立動詞優先的靜態 Trace (`handle-user-request`)，綁定 `session_id` 與 `user_id`；以靜態 Span（`classify-intent`, `search-places`, `enrich-restaurants`, `synthesize-recommendations`, `handle-chitchat`, `explore-culture`）細緻劃分 4-Stage 狀態機與各分支；底層 LLM 呼叫記錄為 Generation，精準掌握模型降級、Token 消耗（`input`/`output`/`total`）、延遲分佈與壞案例標註。
  - **ContextVars 無侵入式傳遞**：透過 `contextvars.ContextVar` 追蹤當前活躍的 Trace/Span，完全無需改動現有 Service 與 Stage 函式簽名。
  - **Fail-Open 優雅降級與 0ms 離線 Mock 原則**：當未配置金鑰或處於 `ENVIRONMENT=test` 時自動啟用輕量級 NoOp 物件，確保單元測試 0 外部請求、0 毫秒延遲，遙測失敗時絕不中斷業務對話。
  - **FastAPI 生命週期清理**：於 `src/main.py` 關閉階段註冊 `telemetry.flush()`，確保緩衝佇列安全送出。
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
- **Langfuse 線上可觀測性與即時鏈路追蹤整合 (`src/services/telemetry.py`, `src/agent/pipeline.py`, `src/agent/llm_client.py`, `src/config.py`)**：
  - **遵循 Langfuse 官方最佳實踐 (`github.com/langfuse/skills`)**：以對話輪次為單位建立動詞優先的靜態 Trace (`handle-user-request`)，綁定 `session_id` 與 `user_id`；以靜態 Span（`classify-intent`, `search-places`, `enrich-restaurants`, `synthesize-recommendations`, `handle-chitchat`, `explore-culture`）細緻劃分 4-Stage 狀態機與各分支；底層 LLM 呼叫記錄為 Generation，精準掌握模型降級、Token 消耗（`input`/`output`/`total`）、延遲分佈與壞案例標註。
  - **ContextVars 無侵入式傳遞**：透過 `contextvars.ContextVar` 追蹤當前活躍的 Trace/Span，完全無需改動現有 Service 與 Stage 函式簽名。
  - **Fail-Open 優雅降級與 0ms 離線 Mock 原則**：當未配置金鑰或處於 `ENVIRONMENT=test` 時自動啟用輕量級 NoOp 物件，確保單元測試 0 外部請求、0 毫秒延遲，遙測失敗時絕不中斷業務對話。
  - **FastAPI 生命週期清理**：於 `src/main.py` 關閉階段註冊 `telemetry.flush()`，確保緩衝佇列安全送出。
- **自研 4 階段確定性有限狀態機 (`FoodRecommendationFSM`)**：取代不可控的黑盒 Prompt Loop，確立 State 1（意圖解析）➔ State 2（Places 階梯擴圈）➔ State 3（Tabelog 平行對齊）➔ State 4（卡片合成與護欄）之確定性流水線。
- **Scoped Anchor 區塊錨點防偽護欄 (`src/agent/pipeline.py`)**：以店家為作用域隔離切片，純 Python 記憶體運算（<0.5ms）杜絕高敏感評分微調幻覺。
- **CI 雙向合約測試防禦 (`tests/test_pipeline.py`)**：自動驗證 System Prompt 排版標記與 Guardrail 正則錨點 100% 同步，徹底根除提示詞漂移。
- **4-Tier 離線評測體系與可觀測性看板**：支援 20 題黃金題庫離線回放與真實對話 Traces 切換，自動生成 GitLab Pages 現代瀑布流儀表板 (`public/index.html`)。

### Changed
- 重構 LLM Client 為多級動態容錯架構（Groq 0.7s 主力 ➡️ Gemini 3.8 Flash 自動備援 ➡️ 本機 Ollama 離線保底）。

---

## [1.0.0] - 2026-10-09

### Added
- **Langfuse 線上可觀測性與即時鏈路追蹤整合 (`src/services/telemetry.py`, `src/agent/pipeline.py`, `src/agent/llm_client.py`, `src/config.py`)**：
  - **遵循 Langfuse 官方最佳實踐 (`github.com/langfuse/skills`)**：以對話輪次為單位建立動詞優先的靜態 Trace (`handle-user-request`)，綁定 `session_id` 與 `user_id`；以靜態 Span（`classify-intent`, `search-places`, `enrich-restaurants`, `synthesize-recommendations`, `handle-chitchat`, `explore-culture`）細緻劃分 4-Stage 狀態機與各分支；底層 LLM 呼叫記錄為 Generation，精準掌握模型降級、Token 消耗（`input`/`output`/`total`）、延遲分佈與壞案例標註。
  - **ContextVars 無侵入式傳遞**：透過 `contextvars.ContextVar` 追蹤當前活躍的 Trace/Span，完全無需改動現有 Service 與 Stage 函式簽名。
  - **Fail-Open 優雅降級與 0ms 離線 Mock 原則**：當未配置金鑰或處於 `ENVIRONMENT=test` 時自動啟用輕量級 NoOp 物件，確保單元測試 0 外部請求、0 毫秒延遲，遙測失敗時絕不中斷業務對話。
  - **FastAPI 生命週期清理**：於 `src/main.py` 關閉階段註冊 `telemetry.flush()`，確保緩衝佇列安全送出。
- **FastAPI 核心後端**：提供 `/health` 健康檢查與 `/webhook/telegram` 端點。
- **Telegram Bot 互動介面**：支援同步 Webhook 訊息即時狀態編輯串流 (`🔍 搜尋...` ➔ `📊 對齊評分...` ➔ 最終推薦卡片)。
- **Neon Cloud Postgres + GCS 冷熱儲存分層**：
  - 熱層：1GB 免費防護、`halfvec` 半精度評論向量庫、7 天快取 TTL 與 14 天 Trace 自動歸檔。
  - 冷層：Snappy Parquet 封存與 DuckDB OLAP 歷程透視 CLI (`scripts/analyze_traces.py`)。
- **雙層漏斗實體對齊器 (`EntityResolver`)**：Level 1 (0ms 電話/核心品牌快篩) + Level 2 (Micro-LLM 灰區語意裁決)。
- **背景預熱 Worker (`src/worker/prewarm.py`)**：支援離峰批量預熱熱門商圈並支援 `/prewarm` 指令。
