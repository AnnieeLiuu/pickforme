# 🍱 PickForMe 全鏈路可觀測性設計憲章 (OBSERVABILITY_DESIGN_SYSTEM.md)

本文件定義 **PickForMe GitLab / GitHub Pages 全景河流圖可觀測性看板 (`public/index.html`)** 之核心設計哲學、視覺語意標準、日本餐飲領域邏輯與資訊階層規範。

---

## 🎨 1. 核心視覺哲學 (Visual Philosophy)

1. **清爽現代淺色主題 (Apple / Stripe / Linear Clean Light Style)**：
   - 採用 Slate 米白底色（`#f8fafc` 畫布、`#ffffff` 卡片），徹底告別刺眼純白與沉重黑底。
   - 文字階層清晰：標題 `#0f172a`（極黑高對比）、標籤 `#475569`、邊框 `#e2e8f0`。
2. **3-Pane 滿版無留白工作台 (Full-Bleed 3-Pane Layout)**：
   - **左欄 (290px)**：歷史對話與題庫流（8 組代表性邊界案例，支援分類篩選）。
   - **中欄 (1fr, min 720px)**：5 大架構層全景河流圖（SVG 動態奔流流光貫通到底）。
   - **右欄 (440px)**：6 維節點審計抽屜（獨立平滑滾動，永久並排懸浮，無遮擋邊界）。
3. **動態奔流點點線 (Dynamic Streaming SVG Lines)**：
   - 走過路徑：`#2563eb` 湛藍虛線以 0.8s 關鍵幀高速奔流。
   - 未走過路徑：`#cbd5e1` 淺灰細虛線，保持架構全景輪廓，絕不憑空斷線。

---

## 📐 2. Draw.io 5 大語意形狀系統 (Semantic Shape System)

依循國際標準流程圖規範，使任何工程師或評審在「零原始碼閱讀」下，透過形狀即可一眼辨識架構職責：

| 語意形狀 | 適用架構節點 | 視覺設計與 CSS 規範 |
| :--- | :--- | :--- |
| **🟡 膠囊形 (Terminal I/O)** | 💬 Telegram 問句輸入<br>📱 成功推播 Telegram 終端 | 全圓角膠囊 (`border-radius: 28px`)，象徵外部請求入口與終端交付出口。 |
| **🔷 菱形標記 (Decision)** | 🧠 State 1 意圖抽取分流<br>📞 市話 Level 1 吻合判定 | 右上角配戴 `◆ 決策判定 (DECISION)` 菱形徽章，左側 4.5px 決策天藍邊條。 |
| **🟣 圓柱體 (Database)** | 🗄️ Neon 7天快取查驗<br>♨️ 區域預熱池查驗 (Prewarm)<br>💾 批次落庫 tabelog_cache | 經典圓柱拱頂輪廓，紫色 Neon 漸層背景 (`#faf5ff`) 與 3 層磁碟硬碟圖標。 |
| **🔵 標準矩形 (Process)** | 🧭 Geocoding 座標錨定<br>🗺️ Places 800m 階梯搜尋<br>🎯 0ms 老饕預篩<br>⚡ Yahoo 實時補齊<br>📐 pgvector 語意重排 | 標準動作方塊 (`border-radius: 8px`)，左側 4.5px 深藍處理條，代表純運算與網路 I/O。 |
| **🛡️ 雙綠框盾牌 (Safety Guard)** | 🛑 缺乏地標防呆截流<br>🔒 Scoped Anchor 防偽校驗<br>🛡️ validator.py 排版禁令 | 翡翠綠虛線雙邊框 (`border: 2px dashed #059669`)，右上角標註 `🛡️ SAFETY GUARD`。 |

---

## 🌊 3. Layer 3 垂直順序流水線規範 (Sequential Funnel Flow)

拒絕無因果關係的 2x2 平鋪方塊，嚴格落實「由上而下、層層過濾、層層沉澱」的垂直河流：

- **Step 1: 🗄️ 7 天快取查驗** ➔ *(連線條件膠囊: `⚪ 未命中快取 ➔ 即時聯網補齊`)*
- ➔ **Step 2: ⚡ Yahoo Grounding 實時檢索** ➔ *(連線條件膠囊: `🎯 候選就緒 ➔ 進入實體防偽漏斗`)*
- ➔ **Step 3: 🛡️ 都道府縣一票否決** ➔ *(連線條件膠囊: `✓ 同行政區放行 ➔ 檢驗市話吻合`)*
- ➔ **Step 4: 📞 市話 Level 1 吻合** ➔ *(連線條件膠囊: `💾 實體精確對齊 ➔ 寫入快取庫持久化`)*
- ➔ **Step 5: 💾 批次落庫 tabelog_cache ＆ restaurants 表**

---

## 🍜 4. 日本餐飲生態與老饕防誤殺原則 (Foodie Precedence)

1. **嚴禁武斷設定「Google 4.0+ 一刀切硬門檻」**：
   - 日本大量頂級職人神店（頑固老爹、排隊規矩嚴格、座位狹窄），常因觀光客給負評而導致 Google 評分僅 3.5~3.7，但在 Tabelog 上老饕卻給出 3.8+ 百名店神級高分。
   - **系統底層策略**：以 **Google 3.5+ 寬鬆保底防誤殺老店**，排除連鎖快餐後保留候選。
2. **終極裁判權上繳 Tabelog (Review Precedence Contract)**：
   - Google 僅作為「800m 物理距離探針與市話取得器」，最終推薦順序與口感重排 100% 由 Tabelog 老饕口コミ與評分定奪。

---

## 📱 5. 審計抽屜資訊階層分工 (Information Hierarchy)

1. **中段所有節點 (Process / Guard / DB)**：
   - **完全隱藏最終答案卡片**，抽屜純粹聚焦展示該節點自身的技術數據（Input/Output JSON、耗時、決策筆記與 SQL 操作），絕不喧賓奪主。
2. **終端推播節點 (`telegram_out`)**：
   - 頂部完整展開 **「💬 旅人手機端收到的最終解答 (Telegram Message Result)」** 繁中排版預覽，達成工程指標與旅人成果的雙重驗收。
