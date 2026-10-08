# 🍱 PickForMe - Phase A2 實作計畫：Google Places、Tabelog 爬蟲與實體匹配器

> **目標**：串接 Google Places API、開發 Tabelog 爬蟲，並建立「電話號碼優先 + 店名正規化 + LLM 灰色地帶判定」的跨來源實體匹配器 (Entity Resolver) 與快取 Repository。

---

## 👨‍💻 User Review Required

> [!IMPORTANT]
> 1. **Google Places API Key**：此階段需要一個有效 Google Places API Key 來測試真實 API；單元測試部分會使用 Replay Fixture 檔案，不花費任何 API 費用。
> 2. **Tabelog 防封鎖**：爬蟲加入 User-Agent 隨機更換與 Rate Limiting (每請求間隔 2 秒)，確保抓取穩定度。

---

## 🛠️ Proposed Changes

### 1. 外部服務與爬蟲層 (`src/services/`)

#### [NEW] `src/services/google_places.py`
- 非同步 HTTP Client (HTTPX) 串接 Google Places API (Text Search / Nearby Search)。
- `search_nearby(lat: float, lng: float, radius: int = 800, keyword: str = "") -> list[dict]`: 搜尋附近店家。
- `geocode_location(query: str) -> tuple[float, float]`: 將「博多站」「天神」等自然語言轉為經緯度。
- 回傳資料格式化：抽取 `place_id`, `name`, `rating`, `user_ratings_total`, `address`, `phone`, `lat`, `lng`, `open_now`。

#### [NEW] `src/services/tabelog_scraper.py`
- 非同步 BeautifulSoup / HTTPX 爬蟲。
- `search_tabelog_candidates(store_name: str, area_name: str) -> list[dict]`: 搜尋候選頁面。
- `scrape_restaurant_details(tabelog_url: str) -> dict`: 解析評分 (如 3.58)、評論數、午/晚餐預算區間、電話號碼、日文店名與地址。

#### [NEW] `src/services/entity_resolver.py`
- **實體匹配演算法 (Entity Resolver)**：
  1. **電話號碼匹配 (Highest Priority)**：去除連字號與空格後，號碼一致直接成功 (`match_method = 'phone_exact'`, `confidence = 1.0`)。
  2. **店名正規化 + 距離計算**：
     - 店名統一全半形、移除「本店」「博多店」等分店後綴。
     - 計算 Jaro-Winkler 相似度與經緯度距離扣分。
     - 分數 >= 0.85 判定成功 (`match_method = 'rule_high'`)。
     - 分數 < 0.60 判定失敗。
  3. **LLM 灰色地帶判定 (0.60 ~ 0.85)**：
     - 使用 Gemini 微型 Prompt 比對是否為同一店家分支 (`match_method = 'llm_verify'`)。
  4. **未配對**：標註「無 Tabelog 資料」（Precision 優先策略）。

---

### 2. 資料存取層 (`src/db/repositories/`)

#### [NEW] `src/db/repositories/restaurant.py`
- `save_restaurant_with_tabelog(...)`: 將對應成功的店家與 Tabelog 分數寫入 `restaurants` 與 `tabelog_cache` 表。
- `get_cached_restaurant_by_google_id(google_place_id: str)`: 檢查本地快取，避免重複打 Tabelog。

---

### 3. Prompt 與測試集 (`config/prompts/` & `tests/`)

#### [NEW] `config/prompts/entity_verify.md`
LLM 比對兩店家是否相同的 Prompt。

#### [NEW] `tests/fixtures/google_places.json` & `tests/fixtures/tabelog_pages.json`
錄製真實 API/網頁回應做為 Replay Fixtures。

#### [NEW] `tests/test_google_places.py` & `tests/test_resolver.py`
- 驗證店名正規化邏輯（如「麵屋 武藏 博多店」與「面屋武蔵」）。
- 驗證電話比對與實體匹配計算。

---

## 🧪 Verification Plan

### Automated Tests
執行 pytest 單元測試與 Ruff 檢查：
```bash
python -m ruff check src/ tests/
python -m pytest tests/ -v
```

### Manual Verification
1. 測試輸入一組知名日本店家（如「一蘭拉麵 博多本店」），驗證實體匹配器能正確對應到 Tabelog 頁面並取得 3.5 以上的分數。
