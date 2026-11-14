# Plan 3: Progressive Skill Loader & Prompt Modularization

> **Status**: Completed & Verified (`ruff`, `mypy`, `pytest` all passed)  
> **Scope**: Architecture #1 (Prompt 模組化拆分) + Architecture #3 (日本在地飲食攻略 Skill)  
> **Design Principles**: 確定性場景注入 (0 額外 LLM Round-trip、Groq/Ollama 小模型友善)、Fail-Open (缺檔保底不崩潰)、職責分離 (`cuisine_guide.py` 管地區名物歷史；`skills/etiquette/` 管品類點餐與文化潛規則)

---

## Architecture Overview (Deterministic Skill Injection)

Plan 3 採用 **確定性場景注入（Stage-Triggered Injection）**：由 Python（`synthesis.py` / `intent.py`）依據當前 FSM 狀態、候選店家數、品類與地區，動態組合需要的 `.md` Skill 片段，**不增加額外 Tool-Calling 延遲**，確保 Groq / 本地 Ollama 小模型穩定執行。

```text
config/
├── prompts/
│   └── agent_system.md              # 精簡核心人格與共用語氣（移除場景專屬肥大規則）
└── skills/
    ├── scenarios/
    │   ├── card-synthesis.md        # State 4 推薦卡片格式、Grounding 與 No-Rating-As-Reason
    │   ├── sparse-results.md        # 僅 len(stores) < 3 時載入（支援 {store_count} / {honest_label} 佔位符）
    │   ├── category-drink.md        # 僅 resolve_card_category == "drink" 時載入
    │   ├── category-souvenir.md     # 僅 resolve_card_category == "souvenir" 時載入
    │   ├── wide-area-nav.md         # 僅 is_wide_area_origin(location) 時載入（支援 {location_name}）
    │   ├── follow-up-qa.md          # 單店追問分支載入（完全隔離 8 欄卡片模板）
    │   └── follow-up-comparison.md  # 多店比較分支載入（保留 4 點比較結構，隔離 8 欄模板）
    └── etiquette/                   # 平常 0 Token；依 cuisine / 地點 / query 命中注入
        ├── ramen-culture.md         # 食券機、二郎系點餐暗號、排隊/禁止分食潛規則
        ├── izakaya-etiquette.md     # お通し小菜低消、一人一杯飲料規範
        ├── yakiniku-kaiseki.md      # 預約制、人數/年齡限制、座位費與取消規範
        └── yatai-street-food.md     # 屋台現金支付、單人點餐與翻桌禮儀
```

---

## Component 1: `SkillLoader` (`src/agent/skill_loader.py`)

1. **Skill 格式（Markdown + 輕量 Metadata Header）**：
   - 使用標準 Markdown（可選頂部 `<!-- triggers: 拉麵, ラーメン, 二郎 -->` 或簡易 YAML frontmatter 解析，無須額外重型依賴）。
   - 解析與檔案讀取採記憶體快取（可選 `reload()` 方便測試）。
2. **核心 API**：
   - `load_scenario(name: str, fallback: str = "") -> str`：載入 `config/skills/scenarios/{name}.md`，若檔案不存在或讀取失敗則 Fail-Open 回傳 `fallback`。
   - `match_etiquette_skills(cuisine: str = "", location_name: str = "", user_query: str = "", max_skills: int = 2) -> list[str]`：
     - 依觸發詞（支援繁中與日文關鍵字）匹配對應的飲食文化 Skill，最多注入 `2` 份（防止多品類同時命中造成 Prompt 膨脹）。
   - `build_synthesis_system_prompt(category: str, store_count: int, location_name: str, cuisine: str = "", user_query: str = "") -> str`：
     - 重用 `synthesis.py` 的 `resolve_card_category(cuisine=cuisine, category=category)`（不重複實作關鍵字表），統一組裝 State 4 所需之模組化 System Prompt（核心卡片規則 + 僅在命中時才附加並安全代換佔位符的 `sparse-results`、`category-*`、`wide-area-nav` 與 `etiquette`）。

---

## Component 2: 拆解 `synthesis.py`、`pipeline.py` & `intent.py` Prompt

1. **`src/agent/stages/synthesis.py` & `src/agent/pipeline.py`**：
   - `execute_state_4_synthesis` 與 `FoodRecommendationFSM` 新增可選參數 `user_query: str = ""`（預設空字串以維持既有測試相容），並於 `run_pipeline()`（`pipeline.py:511`）傳入 `user_query=user_message` 以利精準匹配 `etiquette` Skill。
   - 保留 `RECOMMENDATION_CARD_SYSTEM_PROMPT` 作為檔案缺失時的模組內保底（Fail-Open），`get_recommendation_card_prompt()` 回傳 `agent_system.md` + `card-synthesis.md`，正常執行時改由 `SkillLoader.build_synthesis_system_prompt(...)` 動態組合。
   - **與 `devops.py` / 契約測試對齊**：同步更新 `src/agent/devops.py:144-156` 與 `tests/test_pipeline.py:301-312`（`test_guardrail_contract_with_agent_system_prompt`），改為檢查 `card-synthesis.md`（或組合後的基底卡片提示詞）中的 `"Tabelog"`、`"Google"`、`"步行距離"` 錨點。
   - **小模型防爆約束（`etiquette` 注入規範）**：在提示詞中明確要求將文化攻略濃縮為 **1～2 句置於文末 `💡 特派員提醒`**，嚴禁在單一店家欄位展開長篇大論或破壞 8 欄結構。
2. **`src/agent/stages/intent.py` (`execute_follow_up`)**：
   - 單店追問分支載入 `follow-up-qa.md`，多店比較分支（`len(target_indices) >= 2`）載入 `follow-up-comparison.md`（兩者皆完全不含 State 4 的 8 欄卡片格式與反引號店名要求），避免 Groq / Ollama 小模型在追問時誤吐整張推薦卡片。
   - *(預留對接 Plan 4)*：`follow-up-qa.md` 的載入與輸出介面將直接沿用於 Plan 4 的 Tool Loop。

---

## Component 3: 與既有模組的邊界與相容性

- **與 `cuisine_guide.py` 職責互補（不重疊）**：
  - `LocalSpecialtyGuideService`（`config/local_specialties.json` + Yahoo Grounding）繼續負責「**去該地區該吃什麼名物與歷史淵源**」（如金澤加賀棒茶、函館鹽味拉麵）。
  - `skills/etiquette/` 負責「**去該類型店家怎麼點餐、有什麼日本在地潛規則**」（如居酒屋お通し、食券機、屋台現金）。
- **與 Plan 1 / Plan 2 的連動（防衝突鐵則）**：
  - `card-synthesis.md` 內容必須與 Plan 1 的 `validate_card_tiered` 規範 100% 對齊（保留 `【嚴格禁止評分套話禁令】`、店名反引號只用於標題/店名欄、保留原店名）。
  - **關鍵防衝突（對齊 Plan 1 Item 3）**：`skills/etiquette/*.md` 必須明確規範**嚴禁將日文點餐暗號或文化詞彙（如 `ヤサイマシ`、`お通し`）包在反引號 `` `...` `` 內**，且攻略僅放進文末 `💡 特派員提醒` 段落，避免觸發 `validate_card_tiered` 誤判為未授權店名。
  - Plan 2 修好 `EvaluationEngine.evaluate_tier3_guardrail` 後，拆完 Prompt 可直接跑 Eval 驗證合規率未下降。

---

## 驗證與回歸測試計畫

1. **單元測試 (`tests/test_skill_loader.py`)**：
   - 驗證場景組裝：`len(stores) >= 3` 時不載入 `sparse-results`；`< 3` 時才載入；`drink` / `souvenir` / `wide_area` 各別按需載入。
   - 驗證 `match_etiquette_skills`：輸入「拉麵」只命中 `ramen-culture`，無關查詢（「義大利麵」）回傳空清單（0 Token 額外開銷）；`max_skills` 上限生效。
   - 驗證 Fail-Open：Skill 檔缺失時安全退回預設字串，不拋例外。
2. **Pipeline & Eval 整合測試**：
   - 驗證 `execute_state_4_synthesis` 與 `execute_follow_up`（Mock LLM）分別注入正確的 Skill 切片，且產出仍通過 Plan 1 強化後的 `validate_card_tiered`。
3. **Pre-push Gate**：`python -m ruff format src/ tests/ && python -m ruff check src/ tests/ && python -m mypy src/ && pytest` 全數通過。
