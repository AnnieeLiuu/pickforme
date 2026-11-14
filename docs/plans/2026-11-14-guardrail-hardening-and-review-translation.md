# Plan 1: Guardrail Hardening & Background Review Translation

> **Status**: Completed & Verified (`ruff`, `mypy`, `pytest` all passed)  
> **Scope**: Bug/Review Items 1–4 (State 4 Card Synthesis, Validation, In-Place Repair, Rating Sanitization, and Background Review Translation)  
> **Methodology**: `systematic-debugging` (Root-cause verified; awaiting user approval before implementation)  
> **Design Principles**: 整體性 (Holistic pipeline alignment)、可維護性 (Shared block/name helpers, zero duplicate regex)、泛化能力 (Generalize across Markdown formats without hardcoded wordlists)

---

## Architecture & Pipeline Alignment (整體性設計)

Items 1–4 all operate on the **State 4 deterministic safety net** (`enrichment.py` → `synthesis.py` → `validator.py` → `review_sanitizer.py`):

```text
[State 3 Enrichment]
  └─ index_store_reviews() ──► review_text_ja (日文原文) + review_text_zh (Fail-Open 批次繁中翻譯)

[State 4 Synthesis & Guardrail Pipeline]
  1. LLM / Fallback 產出卡片
  2. sanitize_card_ratings()      ──► 使用共用區塊切割器 (支援 1. / ### / 🥇🥈🥉) 隔離各店並校對評分
  3. repair_store_name_code_blocks() ──► 逐店檢查是否已被反引號包覆，僅補齊缺漏店家
  4. validate_card_tiered()       ──► 嚴格 Grounding (移除 has_ascii_store 整批繞過)
                                      + 區分「標題店名反引號」與「欄位內格式反引號」
                                      + 禁評分套話檢查
  5. repair_card_in_place()       ──► 傳入 cuisine，呼叫無套話版 synthesize_store_recommendation_reason()
```

### 共用元件抽象 (Maintainability & Generalization)
為避免 `validator.py` 與 `synthesis.py` 各自維護脆弱且不一致的 Regex，在 `src/agent/validator.py`（`synthesis.py` 已有單向 import，無循環依賴）抽取兩組公開共用邏輯：
1. **`split_card_store_blocks(card_text: str) -> list[str]`**：
   - 使用多行邊界 `(?m)(?=^(?:...))`（並過濾空字串），同時支援 index 0 與換行後的店家段落起手式（`###`、`**`、排名徽章 `🥇|🥈|🥉`、數字序號 `\d+[\.、](?!\d)`、或含反引號店名的 `###` 標題行）。
   - 納入結尾區塊邊界（`💡\s*\*{0,2}特派員提醒` 與 `---`），確保文末特派員提醒獨立成段、不會被併入最後一家店的區塊而被誤改數字。
2. **`is_store_matched(candidate_text: str, store: dict[str, Any]) -> bool` / `get_store_normalized_names(store)`**：
   - 統一比對 `name_ja` / `name` / `name_en`（經 `_normalize_name` 正規化），供 `validate_card_tiered`、`repair_store_name_code_blocks` 與 `sanitize_card_ratings` 共用。

---

## Item 1: 消除 Layer 3 評分套話衝突 ＆ 補齊背景日文評論翻譯

### 1. 根因
- `validate_card_tiered` 禁止推薦理由出現純評分/評論數套話（如 `Tabelog 評分達 X.XX`、`高分名店`），但 `synthesize_store_recommendation_reason` Layer 3 與 `repair_card_in_place`（且未傳 `cuisine`）會主動產出該類套話。
- `index_store_reviews`（`src/agent/stages/enrichment.py:550-551`）已具備 `review_text_ja` 與 `review_text_zh` 雙欄位，但目前兩者皆寫入相同日文原文。

### 2. 修改方案
1. **`src/services/review_sanitizer.py` (`synthesize_store_recommendation_reason`)**：
   - **寧缺勿濫（No-Fluff Fallback）**：
     - 保留 **Layer 1 & 2**（真實繁中食評 highlight / 已翻譯繁中評論）以及 **Awards**（具體百名店／獲獎事實）。
     - 若既無真實繁中食評、亦無 `awards`，**不再硬生缺乏資訊量的評分套話或空泛形容**，直接回傳 `("", "")`。
2. **`src/agent/validator.py` & `src/agent/stages/synthesis.py` (`repair_card_in_place` / `format_fallback_cards`)**：
   - `repair_card_in_place(card_text, stores, cuisine: Optional[str] = None)` 新增 `cuisine` 參數並轉傳 `synthesize_store_recommendation_reason(..., cuisine=cuisine)`；`synthesis.py` 呼叫時補傳 `cuisine=cuisine`。
   - 當推薦理由違規（評分套話或生硬日文漏出）且 `synthesize_store_recommendation_reason` 回傳空字串（或仍含日文假名）時，**`repair_card_in_place` 直接省略（移除）該違規推薦理由行**，不再塞入空話模板；`format_fallback_cards` 亦僅在 `rev_txt` 非空時才輸出推薦理由行。
3. **`src/agent/stages/enrichment.py` (`index_store_reviews`)**：
   - 維持背景 `asyncio.create_task` 執行（0 前景延遲）。
   - 先過濾出 `valid_reviews = [r for r in reviews[:3] if (r.get("text") or r.get("original_text"))]`，確保 `valid_reviews`、`texts`、`zh_texts` 與 `embeddings` 嚴格 1:1 對齊（順帶修正原本 `zip(sample_reviews, embeddings)` 可能錯位的隱患）。
   - `review_text_ja` 永遠保留原始日文評論。
   - 對 `valid_reviews` 執行單次批次翻譯（日文 → 繁體中文）寫入 `review_text_zh`：
     - 若原文已不含日文假名（`not contains_japanese_kana(text)`），直接沿用原文免呼叫 LLM。
     - 若需翻譯，在獨立 `try ... except Exception` 與 `asyncio.wait_for(..., timeout=10.0)` 內透過 `fsm.llm_client` 單次呼叫翻譯並解析 JSON 陣列（或對應長度結構）。
     - **Fail-Open 隔離**：即使 `llm_client` 缺失、為測試用 `MagicMock`、超時、拋錯或回傳筆數不符，僅記錄 warning 並降級保留原文寫入 `review_text_zh`，絕不阻斷 `get_embeddings_batch` 與 `batch_insert_reviews`。

---

## Item 2: 移除 `has_ascii_store` 批次級 Grounding Bypass（方案 A）

### 1. 根因
- `src/agent/validator.py:198-238` 只要候選名單中**任何 1 家**為純 ASCII 店名，`has_ascii_store` 即設為 `True`，並在 L237 `if unmatched_store_cbs and not has_ascii_store:` 導致**整批卡片的所有未授權/虛構店家皆被放行**。

### 2. 修改方案（嚴格移除 `validate_card_tiered` Bypass）
- 移除 `validate_card_tiered` 中的 `has_ascii_store` 旗標與 L222-224、L237 繞過條件。
- **既有 Legacy 相容邊界**：`validate_recommendation_card`（舊版函式）目前仍被 `tests/test_real_world_regressions.py:335-370`（Case 9）測試其英日雙語模糊匹配行為，本次保持 `validate_recommendation_card` 不動，專注封堵實際生產主線 `validate_card_tiered` 的漏洞。
- 所有標題店名一律須命中候選名單的 `name_ja` / `name` / `name_en`（經 `_normalize_name` 正規化與子字串包含比對）；若 LLM 偶發翻錯，由重試與 `repair_store_name_code_blocks` 處理，絕不開整批幻覺後門。

---

## Item 3: `repair_store_name_code_blocks` 逐店檢查 ＆ 區分非店名反引號

### 1. 根因
1. `repair_store_name_code_blocks`（`src/agent/validator.py:275-277`）只要偵測到任何反引號（`if existing_cbs: return card_text`）就提早返回，導致：
   - 若卡片只有 `\`PayPay\`` 而沒有店名反引號，完全不會修補店名。
   - 若 3 家店只包了 1 家，另外 2 家不會被補包反引號。
2. `validate_card_tiered`（`src/agent/validator.py:183-195`）將卡片中**所有** 1–60 字元反引號一視同仁視為「推薦店名」，導致 `\`PayPay\``、`\`Suica\``、`\`JR新宿站\`` 等資訊性反引號觸發「推薦了未授權/虛構店家」致命錯誤。

### 2. 修改方案（不硬編碼詞彙表，採結構位置泛化判斷）
1. **逐店補包反引號 (`repair_store_name_code_blocks`)**：
   - 解析現有 `existing_cbs`，針對每家 `store` 檢查 `get_store_normalized_names(store)` 是否已存在於 `existing_cbs` 中。
   - 僅對「尚未被任何反引號覆蓋」的店家補包反引號：優先在標題/序號行尋找，跳過明細欄位行（`🚶`、`⭐️`、`💳`、`💰`、`🔗`、`📍`、`💡`），並確保匹配區段**不在**既有的 `` `[^`\n]+` `` 或 Markdown 連結 `[...](...)` 範圍內，徹底杜絕雙重包裹（如 `` ``一蘭` 新宿店` ``）或破壞連結。
2. **結構化區分店名反引號與非店名反引號 (`validate_card_tiered`)**：
   - 逐行掃描反引號出現的上下文位置（不使用硬編碼關鍵字白名單，採 **Fail-Closed 預設視為店名**）：
     - **明確視為店名主張 (Store-name claim)**：
       1. 位於店家標題/序號行（行首為 `🥇🥈🥉`、`#`、`\d+[.、]`、`**\d+`、`【`、`🍱`，或含 `店名：` / `餐廳：` / `店家：` 標籤之行），或
       2. 該反引號內容本身已匹配候選名單中的任一店名，或
       3. 位於無明確非店名屬性標籤的純文字/獨立行。
     - **視為非店名排版反引號 (Ignored formatting)**：僅當該行明確屬於非店名屬性明細（例如帶有 `支付`/`付款`/`交通`/`步行`/`預算`/`營業`/`地址`/`評分`/`推薦理由`/`點評`/`提醒` 等標籤或 `🚶`/`⭐️`/`💳`/`💰`/`🔗`/`📍`/`💡` 圖示，且**不含** `店名`/`餐廳`/`店家`/`🍱`）且未匹配任何候選店名時，才不計入 `unmatched_store_cbs` 與 `unique_cbs` 數量上限檢查。
   - **邊界防護**：`unique_cbs`、`len(unique_cbs) > len(stores)` 與空檢查僅統計「店名主張反引號」，確保整張卡只有 `\`PayPay\`` 而無店名時仍報 `缺少一鍵複製店名反引號代碼塊`，且 `- 🍱 **店名**：\`虛構拉麵\`` 100% 被攔截。

---

## Item 4: 泛化 `sanitize_card_ratings` 段落切割 Regex

### 1. 根因
- `src/agent/stages/synthesis.py:219` 使用 `re.split(r"(?=\n(?:###\s*\d+|\d+[\.、]|\*\*\d+[\.、]))", card_text)`，強制要求標題必須有數字前綴。
- 當 LLM 輸出 `🥇 **店名**`、`🥈 \`店名\``、`### 🥇 \`店名\`` 或無數字 `### \`店名\`` 時，切割失效，整張卡片被當作單一區塊，導致第 2、3 家店的評分校對被跳過或受第 1 家干擾。

### 2. 修改方案
- 改用共用函式 `split_card_store_blocks(card_text)` 切割區塊（含 index 0 起始與 `💡 特派員提醒` / `---` 結尾隔離）。
- **店家配對順序與防誤傷優化**：
  - 優先以區塊是否包含該店的標準化店名（`name_ja` / `name` / `name_en`）配對。
  - 若須退回序號/排名徽章配對，**僅比對區塊的第一行（首行標題）**，且數字序號使用 `(?!\d)` 負向先行斷言（避免第 3 家店的 `\b3\.` 誤匹配內文中的 `Tabelog 3.50` 或 `3.5 小時`）。
  - 無法歸屬任何候選店的純導言或結尾段落（如 `💡 特派員提醒`），保留原文不進行評分替換。

---

## 驗證與回歸測試計畫 (`tests/test_real_world_regressions.py`)

所有測試遵守 **0ms 離線 Mock 原則**（不連網、不消耗真實 API Key）：
1. **Item 1**：
   - 對齊 `tests/test_real_world_regressions.py` 既有測試（L680、L1242）與 `test_case_36_no_rating_boilerplate_in_sanitizer_and_in_place_repair`：驗證無真實食評且無獎項時不硬湊評分套話或空話（回傳 `("", "")` 且 `repair_card_in_place` 直接省略該行），有真實中文食評或 `awards` 時則正常保留/抽換。
   - 新增 `index_store_reviews` 背景翻譯測試（成功批次翻繁中寫入 `review_text_zh` 且保留 `review_text_ja`；以及 `llm_client` 失敗時 Fail-Open 降級保留原文）。
2. **Item 2**：
   - 驗證候選名單含純 ASCII 店名（如 `AFURI`）時，若卡片夾帶未授權虛構店名（如 `` `Fake Ramen` ``），`validate_card_tiered` 必須攔截為 `fatal_errors`。
3. **Item 3**：
   - 驗證卡片已有 `` `PayPay` `` 或僅包覆第 1 家店名時，`repair_store_name_code_blocks` 仍會補齊缺漏的店名反引號。
   - 驗證明細欄含 `` `PayPay` `` / `` `Suica` `` 不觸發虛構店家誤殺；但標題行出現未授權店名 `` `虛構名店` `` 仍觸發致命錯誤。
4. **Item 4**：
   - 驗證使用 `🥇` / `🥈` / `🥉` 或無數字 `### \`店名\`` 排版的多店卡片，第 2、3 家店的竄改評分皆能被 `sanitize_card_ratings` 精準矯正回真實數值。
5. **Pre-push Gate**：`python -m ruff format src/ tests/ && python -m ruff check src/ tests/ && python -m mypy src/ && pytest` 全數通過。
