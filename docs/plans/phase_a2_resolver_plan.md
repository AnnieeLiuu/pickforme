# 🍱 PickForMe - Phase A2 實作計畫：Google Places、Tabelog 爬蟲與實體匹配器 (V2.0 精確對齊版)

> **目標**：串接 Google Places API (New)、開發具備 Fail-Fast 降級策略的 Tabelog 爬蟲，並建立「電話號碼完全正規化比對 + 店名相似度 + Gemini Flash 灰色地帶判定」的跨來源實體匹配器 (Entity Resolver) 與快取 Repository。

---

## 📐 1. 精確對齊之架構與決策細節

### A. Google Places API (New) 介面
- **標準**：使用 Google Places API (New) REST 介面 (`https://places.googleapis.com/v1/places:searchNearby`)。
- **欄位控制**：傳送 `X-Goog-FieldMask` 標頭僅要求指定欄位（如 `places.id`, `places.displayName`, `places.nationalPhoneNumber`, `places.location`, `places.rating`, `places.userRatingCount`, `places.formattedAddress`, `places.regularOpeningHours`），優化延遲與費用。
- **Geocoding**：使用 Google Geocoding API (`https://maps.googleapis.com/maps/api/geocode/json`) 將地名/車站轉為經緯度。

### B. Tabelog 爬蟲與 Fail-Fast 降級策略
- **爬蟲設定**：HTTPX Async Client 搭配 User-Agent 隨機輪替，單一請求 Timeout 設為 3 秒。
- **降級機制**：重試最多 2 次 (間隔 2 秒)。若遭遇網路逾時、HTTP 429 或 403 封鎖，立刻觸發 Fail-Fast 降級——將該店家標註為「無 Tabelog 資料」，退回使用 Google Places 評分繼續回答，確保對話回應時間控制在 5 秒以內。

### C. 日本電話號碼正規化規則
1. 自動將 `+81` 國際號碼前綴轉換為日本國內 `0` 開頭 (如 `+81-3-3363-4690` -> `0333634690`)。
2. 清除所有連字號、括號與空格，轉為純數字字串。
3. 若 Tabelog 同時列出直撥電話與 050 訂位專線，任一號碼符合即高信心度判定為同一店家 (`match_method = 'phone_exact'`, `confidence = 1.0`)。

### D. LLM 灰色地帶判定 (0.60 ~ 0.85)
- **模型**：Gemini Flash。
- **設定**：`temperature = 0.0` 確保輸出穩定決定性。
- **結構化輸出**：使用 Pydantic Model 解析回傳：
  ```python
  class EntityVerifyResponse(BaseModel):
      is_same_store: bool
      confidence: float
      reason: str
  ```

---

## 🛠️ Proposed Changes

### 1. 外部服務與爬蟲層 (`src/services/`)

#### [NEW] `src/services/google_places.py`
- 非同步 HTTP Client 串接 Google Places API (New) 與 Geocoding API。
- `search_nearby(lat: float, lng: float, radius: int = 800, keyword: str = "") -> list[dict]`
- `geocode_location(query: str) -> tuple[float, float]`

#### [NEW] `src/services/tabelog_scraper.py`
- 非同步 BeautifulSoup / HTTPX 爬蟲，實作 3s Timeout 與 Fail-Fast 降級。
- `search_tabelog_candidates(store_name: str, area_name: str) -> list[dict]`
- `scrape_restaurant_details(tabelog_url: str) -> dict`

#### [NEW] `src/services/entity_resolver.py`
- **實體匹配演算法 (Entity Resolver)**：
  - Step 1: 日本電話號碼正規化與完全比對。
  - Step 2: 店名全半形清理、分店後綴移除與 Jaro-Winkler / 距離計算。
  - Step 3: Gemini Flash 結構化灰色地帶判定 (0.60 ~ 0.85)。
  - Step 4: 未配對標註「無 Tabelog 資料」。

---

### 2. 資料存取層 (`src/db/repositories/`)

#### [NEW] `src/db/repositories/restaurant.py`
- `save_restaurant_with_tabelog(...)`: 將對應成功的店家與 Tabelog 分數寫入 `restaurants` 與 `tabelog_cache` 表。
- `get_cached_restaurant_by_google_id(google_place_id: str)`: 檢查本地快取。

---

### 3. Prompt 與測試集 (`config/prompts/` & `tests/`)

#### [NEW] `config/prompts/entity_verify.md`
LLM 比對兩店家是否相同的 System Prompt。

#### [NEW] `tests/fixtures/google_places.json` & `tests/fixtures/tabelog_pages.json`
錄製真實 API/網頁回應做為 Replay Fixtures。

#### [NEW] `tests/test_google_places.py` & `tests/test_resolver.py`
- 驗證電話號碼正規化邏輯 (`+81-3-3363-4690` -> `0333634690`)。
- 驗證店名清理與實體匹配計算。

---

## 🧪 Verification Plan

### Automated Tests
```bash
python -m ruff check src/ tests/
python -m mypy src/
python -m pytest tests/ -v
```

### Manual Verification
測試知名店家（如「麵屋武藏」、「一蘭拉麵」），驗證電話比對與實體匹配成功寫入 Neon Postgres 快取。
