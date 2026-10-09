# Tabelog 網站結構與 HTML DOM 解析指南 (Tabelog Inspection Guide)

本文件紀錄了使用 Playwright 實地觀察 Tabelog (tabelog.com) 店家頁面時獲得的 HTML DOM 結構特徵、重點 CSS 選擇器（Selectors）、以及爬蟲擴充欄位對應規則。

---

## 一、 店家頁面頂部 Header 資訊 (RD Header)

在 Tabelog 店家詳細頁面頂部（包含評分、榮譽標章、預算與保存數）：

| 資訊名稱 | HTML 關鍵 CSS Selector | 備註 / 範例 |
| :--- | :--- | :--- |
| **店名 (日文)** | `.display-name` 或 `.rstinfo-table__name` | 例：`すし匠` |
| **Tabelog 評分** | `.rdheader-rating__score-val` | 例：`4.35` |
| **評論總數** | `.rdheader-rating__review-target .num` | 例：`436` 人 |
| **保存數 (Bookmarks)**| `.rdheader-rating__saved-target .num` | 例：`127,159` 人 |
| **Tabelog Award** | `.rdheader-official-info__award-title`, `.rdheader-award` | 金賞 `Gold 2026`, 銀賞 `Silver 2025` |
| **百名店 標章** | `.hyakumeiten-badge`, `.rdheader-official-info` | 例：`寿司 百名店 2025` |
| **午餐預算 (Lunch)** | `.rdheader-budget__price-target` (第2個) | 例：`￥10,000～￥14,999` |
| **晚餐預算 (Dinner)**| `.rdheader-budget__price-target` (第1個) | 例：`￥20,000～￥29,999` |

---

## 二、 店家詳細表格 (Store Info Table `#contents-rstdata`)

詳細資訊位於頁面中央表格 `#contents-rstdata` 或 `.rstinfo-table` 中：

### 1. 支付方式 (Payment Methods)
- **TH 標題文字**：`カード` (信用卡)、`電子マネー` (電子票證)、`QRコード決済` (QR碼)
- **信用卡**：
  - CSS Selector: `tr:has(th:contains('カード')) td`
  - 可用品牌：VISA, Master, JCB, AMEX, Diners
- **QR Code 支付 (PayPay等)**：
  - CSS Selector: `tr:has(th:contains('QRコード決済')) td`
  - 關鍵字包含：`PayPay` (可透過包含 `PayPay` 判定 `supports_paypay = True`)

### 2. 座位與環境 (Seating & Environment)
- **包廂 (個室)**：
  - TH 標題：`個室`
  - 判定邏輯：`有` / `無` (例：`有 2人可、4人可`)
- **禁菸/吸菸規定**：
  - TH 標題：`禁煙・喫煙`
  - 判定邏輯：`全席禁煙` / `喫煙可` / `分煙`

---

## 三、 評論與熱門菜色擷取策略

1. **評論不宜全文儲存的原因**：
   - 防爬反制：頻繁抓取大量評論子頁面易引發 HTTP 429 或 Cloudflare 封鎖。
   - 效益比低：90% 為個人感言與行程日記，Token 成本極高。

2. **精準擷取亮點 (Highlighting)**：
   - 提取首頁 `.rdheader-rating` 下方 Top 3 評論關鍵字或標籤。
   - 提煉熱門餐點名稱（Must-try dishes），儲存於 `raw_details.must_try_dishes`。

---

## 四、 建議之資料庫儲存結構 (Postgres `raw_details` JSONB)

```json
{
  "awards": ["Gold 2026", "Silver 2025"],
  "hyakumeiten": ["寿司 百名店 2025"],
  "saved_count": 127159,
  "payment": {
    "credit_cards": ["VISA", "Master", "JCB", "AMEX", "Diners"],
    "supports_paypay": true,
    "qr_code_details": "PayPay, d払い"
  },
  "facility": {
    "has_private_room": true,
    "smoking_policy": "全席禁煙"
  },
  "must_try_dishes": ["おまかせコース", "ばらチラシ"]
}
```
