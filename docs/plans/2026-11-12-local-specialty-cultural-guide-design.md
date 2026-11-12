# 在地特色美食與飲食文化導覽設計書 (Local Specialty & Cultural Guide Design)

> **建立日期**：2026-11-12  
> **狀態**：待使用者最終確認 (Pending User Approval)  
> **架構定位**：擴充 PickForMe 在「使用者尚未決定具體料理類型」或「主動探索當地名物」時的飲食文化導覽能力，採用 **方案 C（Tier 1 內建考據知識庫 + Tier 2 即時日本網頁 Grounding 查證）** 雙層架構，在 100% 杜絕免費 LLM 捏造歷史幻覺的前提下，提供有深度的在地飲食歷史故事，並透過 `working_memory` 無縫銜接下一輪的 Top 3 餐廳檢索。

---

## 🎯 1. 核心目標與範疇 (Goals, Non-Goals & Anti-Goals)

- **Goals (目標)**：
  1. 當使用者僅提供日本地點而未指定具體料理類型（如「神保町有什麼好吃的？」、「淺草推薦吃什麼？」）或主動詢問「當地特色美食/名物」時，先提供 **3 個當地最具代表性的特色料理類型** 與 **歷史文化淵源介紹**。
  2. 底部提供清晰的雙向引導：使用者可回覆 **`1`、`2`、`3`** 或**料理名稱**鎖定類別，亦可回覆 **「直接推薦 / 隨便」** 不限類型直接找最高分餐廳。
  3. 透過 `working_memory` 記住 `last_location` 與 `suggested_cuisines`，讓下一輪回覆 `1` 或 `直接推薦` 時能以 0ms 映射並直接進入 Stage 2~4 餐廳推薦流水線。
- **Non-Goals (非目標)**：
  - 當使用者第一句話就已經明確指定料理類型（如「神保町 義大利麵」、「新宿 燒肉」）時，**不打斷**使用者，直接照既有流程輸出餐廳推薦卡。
- **Anti-Goals (絕對禁止)**：
  - **嚴禁讓免費 LLM 在無依據下憑空編造歷史故事或發源年份**。未知地區必須經過日本網頁 Grounding 查證；若查無特定歷史典故，則誠實呈現該區常見人氣料理類別，絕不瞎掰。

---

## 🔀 2. 三種候選架構方案與取捨 (Approaches & Trade-offs)

| 方案 | 運作機制 | 優缺點與取捨 | 決策 |
| :--- | :--- | :--- | :---: |
| **方案 A：純即時 Web Grounding + 快取** | 每次詢問地點皆先透過 Yahoo Japan 搜尋當地名物與歷史，再交由 LLM 整理。 | 支援全日本任意車站，但熱門景點首次查詢仍需多花 ~0.6s 網路與 LLM 延遲。 | ❌ |
| **方案 B：純靜態熱門商圈 JSON 字典** | 僅依賴內建 JSON 字典提供文化故事，未知車站退回一般搜尋。 | 熱門景點 0ms 且零幻覺，但超出字典的日本車站無法提供在地特色導覽。 | ❌ |
| **方案 C：混合雙層漏斗（黃金考據字典 + 即時 Grounding 查證）** | **Tier 1**：20+ 個日本核心旅遊商圈直接命中內建考據知識庫（0ms、0 Token、100% 道地）。<br/>**Tier 2**：未收錄之車站自動啟動 Yahoo Japan Grounding 抓取真實日文網頁摘要，再由 LLM 依 Pydantic Schema 提煉並快取。 | 兼顧熱門景點 0ms 極速體驗、100% 歷史真實性，以及全日本任意車站的長尾覆蓋率。 | ✅ **已選定** |

---

## 🛠️ 3. 模組與狀態機互動設計 (Detailed Architecture)

### 3.1 意圖識別與觸發邊界 (`src/agent/schemas.py` & `src/agent/stages/intent.py`)
1. **明確指定料理 (`cuisine` 非空且非籠統詞)**：
   - 例如：「神保町 義大利麵」、「淺草 壽喜燒」➔ 維持 `intent = "search"`，直通 Stage 2。
2. **要求直接推薦 (`"直接推薦"`、`"隨便"`、`"不限"`)**：
   - 維持 `intent = "search"`，`cuisine = "熱門人氣美食"`，直通 Stage 2。
3. **探索當地特色 / 僅有地點無具體料理 (`intent = "explore_cuisine"`)**：
   - 例如：「神保町有什麼好吃的？」、「淺草特色美食」、「月島推薦吃什麼」或僅輸入「神保町」而無具體食物詞。
   - 路由至新增的 **`LocalSpecialtyGuideService` (`src/services/cuisine_guide.py`)**。

### 3.2 雙層飲食文化導覽服務 (`src/services/cuisine_guide.py`)
- **資料結構 (`SpecialtyItem` & `AreaSpecialtyGuide` Pydantic Schema)**：
  - `area_name`: 地區名稱（如 `"神保町"`）
  - `intro`: 地區飲食文化總覽（1~2 句）
  - `specialties`: 3 個特色料理項目，每項包含：
    - `index`: `1 | 2 | 3`
    - `cuisine_name`: 料理名稱（如 `"歐風咖哩"`）
    - `name_ja`: 日文關鍵字（如 `"欧風カレー"`）
    - `history_and_culture`: 歷史文化背景與在地淵源（2~3 句考據說明）
    - `flavor_profile`: 風味與必吃特色
- **Tier 1 (`config/local_specialties.json`)**：
  - 內建東京（神保町、淺草、月島、築地、銀座、上野、新宿、澀谷、池袋、秋葉原、新橋、人形町、吉祥寺）、橫濱（中華街、關內/馬車道）、關西（京都祇園/河原町、宇治、大阪道頓堀/難波、新世界、神戶三宮）、九州與北海道（博多/天神、札幌）等代表性名物與歷史考據。
- **Tier 2 (`_fetch_and_synthesize_via_grounding`)**：
  - 當 `area_name` 未命中 Tier 1 時，呼叫 `httpx` 查詢 Yahoo Japan `"{area_name} 名物 グルメ 歴史 発祥"` 擷取前 5 筆真實網頁標題與摘要，再透過 `LLMClient.call_structured_json(schema=AreaSpecialtyGuide)` 提煉並存入記憶體快取。

### 3.3 多輪記憶銜接 (`working_memory` & `QuickPassGate`)
1. 當輸出文化導覽卡時，於 `SessionRepository` 的 `working_memory` 寫入：
   ```json
   {
     "last_location": "神保町",
     "suggested_cuisines": ["歐風咖哩", "昭和喫茶與拿坡里義大利麵", "半炒飯拉麵 (半ちゃんラーメン)"]
   }
   ```
2. 當使用者下一輪輸入：
   - **數字或序號 (`"1"`, `"2"`, `"3"`, `"第1個"`, `"第一個"`, `"選2"`)**：
     - 在 Stage 1 直接從 `working_memory["suggested_cuisines"]` 取出對應料理名稱與 `last_location`，轉為 `intent = "search"` 直奔 Stage 2 搜尋 Top 3 餐廳！
   - **「直接推薦」/「隨便」**：
     - 從 `working_memory["last_location"]` 取出地點，設 `cuisine = "熱門人氣美食"`，直奔 Stage 2！

