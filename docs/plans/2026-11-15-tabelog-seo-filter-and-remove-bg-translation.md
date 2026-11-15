# Tabelog SEO 樣板句過濾與移除背景評論翻譯計畫

**日期**: 2026-11-15  
**狀態**: Completed & Verified (`ruff`, `mypy`, `pytest` 262 passed)  
**背景與根因**：
在生產追蹤 `3dc12ef3-f582-48f2-ab5b-f925c10e59d6`（「巢鴨 咖啡」）中觀察到兩個連帶問題：
1. `fetch_tabelog_reviews`（預設 `max_reviews=5`）未過濾 Tabelog 常見的 4 類 SEO 樣板句（如「日本最大級のグルメサイト「食べログ」では...」、「...の店舗情報は食べログでチェック！」、「...への〇〇さんの 口コミ です。」、「利用金額分布を見る」），導致 `伯爵 巣鴨店` 的 `sample_reviews[0..3]` 全被樣板句佔滿，唯一真實食記被擠到 `[4]`，進而讓 `state_4_synthesis` 缺少有效食評而寫出「Tabelog 3.47 分」並觸發 `No-Rating-As-Reason` 護欄刪除推薦理由。
2. `index_store_reviews` 在背景對每家店並發呼叫 `_translate_reviews_batch_fail_open` 進行日翻中，於同一個請求視窗內與 `EntityResolver` 及前景 `state_4_synthesis` 爭搶 Groq 主力模型（`qwen/qwen3.8-27b`）RPM/TPM 配額，引發 `429 Too Many Requests` 與 ~30 秒 `Retry-After` backoff。
3. 實際上，`state_4_synthesis` 主 LLM 本身即可直接讀懂日文原文 `sample_reviews[0]` 並以繁體中文生成推薦理由；`index_store_reviews` 的向量化 (`get_embeddings_batch(texts)`) 亦直接使用原文計算，且 `semantic_rerank_reviews` 已支援 `m.get("review_text_zh") or m.get("review_text_ja")` 降級。

---

## 變更設計

### 1. 過濾 Tabelog SEO 樣板句並微調單頁抓取上限
- **檔案**: `src/services/review_sanitizer.py`
  - 在 `is_pure_metadata_noise`（或專屬樣板句正規表達式清單）加入以下 Tabelog SEO 樣板句模式（注意：**即使句子裡剛好出現 `FOOD_TASTE_KEYWORDS` 如「人気」「ランチ」「珈琲」等字眼，只要命中 Tabelog SEO 樣板句特徵，也必須判定為雜訊**，避免因關鍵字白名單放行）：
    - `日本最大級のグルメサイト「食べログ」では`
    - `の店舗情報は食べログでチェック`
    - `への[^\n]{1,40}さんの\s*口コミ\s*です`（移除原本 `tabelog_scraper.py` 中僅在 `len < 60` 時才擋的狹隘條件，改為通用過濾）
    - `利用金額分布を見る`
- **檔案**: `src/services/tabelog_scraper.py`
  - `fetch_tabelog_reviews(self, store_name: str, max_reviews: int = 8)`：預設上限從 `5` 調整為 `8`（同一頁 Yahoo Search HTML 內即可掃完，0 額外 HTTP 延遲），確保濾掉 SEO 樣板句後仍有充足真實食記供 `reviews[:3]` 使用。
  - 移除重複/過窄的 `len(clean_text) < 60` 舊條件，統一交由 `is_pure_metadata_noise(clean_text)`（及標題/內文前綴檢查）把關。

### 2. 移除背景 LLM 翻譯 (`_translate_reviews_batch_fail_open`)
- **檔案**: `src/agent/stages/enrichment.py`
  - 移除 `_translate_reviews_batch_fail_open` 函式及其在 `index_store_reviews` 中的 `asyncio.gather` 呼叫。
  - `index_store_reviews` 直接同步取用已存在的 `r.get("zh") or r.get("text_zh")`（若無則直接保存原文 `ja_text` 進 `review_text_zh`），僅呼叫 `fsm.embedding_service.get_embeddings_batch(texts)` 寫入 `review_embeddings`，達成 **0 額外 LLM 呼叫、完全不與前景搶配額**。

### 3. 回歸測試與既有測試同步
- **檔案**: `tests/test_real_world_regressions.py`
  - 更新 `test_case_37_index_store_reviews_background_translation_and_fail_open`：驗證 `index_store_reviews` 不再呼叫 `llm_client.call_text`（`assert_not_called()`），且照常寫入 `review_embeddings`（保留既有 `zh` 或原文）。
  - 新增 **Case 48 (`test_case_48_tabelog_seo_boilerplate_filtered_and_no_bg_translation`)**：重現 `伯爵 巣鴨店` 的 4 種 SEO 樣板句 + 1 則真實日文食記，驗證 `fetch_tabelog_reviews` 全部過濾樣板句只保留真實食記，且不阻斷後續流程。

### 4. 文件同步
- 檢查 `docs/ARCHITECTURE.md` 與 `CHANGELOG.md` 是否有提及背景翻譯，必要時同步更新為「直接索引原文 + 前景主 LLM 原文直譯卡片」。

---

## 驗證方式
```bash
python -m ruff format src/ tests/ && python -m ruff check src/ tests/ && python -m mypy src/ && pytest
```
