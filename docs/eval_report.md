# 🍱 PickForMe 4-Tier 離線評測體系報告 (Offline LLMOps Benchmark)

> **評測狀態**：🟢 PASSED (CI Gatekeeper: T1≥90%, T2≥0.90, T3≥95%)
> **總測試案例數**：20 題

## 📊 核心指標看板 (Key Performance Indicators)

| 層級 | 評測維度 | 目標門檻 | 實測表現 | 狀態 |
| :--- | :--- | :---: | :---: | :---: |
| **Tier 1** | 意圖與參數抽取率 (Intent Accuracy) | ≥ 90.0% | **100.0%** | ✅ |
| **Tier 2** | 實體比對解析度 (Entity Resolver F1) | ≥ 0.90 | **1.0000** | ✅ |
| **Tier 3** | 數值護欄與格式遵從 (Guardrail Compliance) | ≥ 95.0% | **100.0%** | ✅ |
| **Tier 4** | 端到端綜合成功率 (E2E Task Success) | - | **100.0%** | ✅ |

## ⚡ 延遲基準統計 (Latency Benchmark)

- **平均延遲 (Average Latency)**：`0.1 ms`
- **P50 延遲**：`0.0 ms`
- **P90 延遲**：`1.0 ms`

## 📋 測試案例執行矩陣 (Cases Execution Matrix)

| ID | 分類 | 測試提問句 | T1 (意圖) | T2 (比對) | T3 (護欄) | 耗時 |
| :---: | :--- | :--- | :---: | :---: | :---: | :---: |
| `eval_001` | `typo_colloquial` | 新宿歌舞妓町有啥開很晚的拉麵，不要太油 | ✅ | ✅ | ✅ | 0ms |
| `eval_002` | `multi_landmark` | 想要在橫濱棒球場或橫濱車站附近的壽喜燒 | ✅ | ✅ | ✅ | 0ms |
| `eval_003` | `taste_rag` | 池袋 想要麵條偏硬有嚼勁的拉麵 | ✅ | ✅ | ✅ | 1ms |
| `eval_004` | `multi_turn_more` | 還有更多推薦嗎？ | ✅ | ✅ | ✅ | 0ms |
| `eval_005` | `multi_turn_more` | 我想找更在地的老店 | ✅ | ✅ | ✅ | 0ms |
| `eval_006` | `follow_up` | 第2家今天有開嗎？ | ✅ | ✅ | ✅ | 0ms |
| `eval_007` | `follow_up` | 第一家可以刷信用卡或 PayPay 嗎？ | ✅ | ✅ | ✅ | 0ms |
| `eval_008` | `chitchat` | 哈囉你好！請問你可以幫我做什麼？ | ✅ | ✅ | ✅ | 0ms |
| `eval_009` | `need_location` | 我想吃炸豬排 | ✅ | ✅ | ✅ | 0ms |
| `eval_010` | `typo_colloquial` | 澀谷平價居酒屋，預算3000日幣內 | ✅ | ✅ | ✅ | 0ms |
| `eval_011` | `taste_rag` | 京都 祇園 適合長輩吃的清淡湯豆腐 | ✅ | ✅ | ✅ | 0ms |
| `eval_012` | `typo_colloquial` | 原宿竹下通排隊人氣可麗餅 | ✅ | ✅ | ✅ | 0ms |
| `eval_013` | `multi_turn_more` | 有沒有更便宜一點的？ | ✅ | ✅ | ✅ | 0ms |
| `eval_014` | `chitchat` | 明天東京會下雨嗎？ | ✅ | ✅ | ✅ | 0ms |
| `eval_015` | `multi_landmark` | 秋葉原跟御茶之水中間有名的日式咖哩 | ✅ | ✅ | ✅ | 1ms |
| `eval_016` | `taste_rag` | 新宿 想要牛舌厚切多汁的炭火燒肉 | ✅ | ✅ | ✅ | 0ms |
| `eval_017` | `follow_up` | 第3家可以線上訂位或打電話預約嗎？ | ✅ | ✅ | ✅ | 0ms |
| `eval_018` | `typo_colloquial` | 秋葉 一個人也能吃的燒肉定食 | ✅ | ✅ | ✅ | 0ms |
| `eval_019` | `multi_turn_more` | 換一批，但不要排隊太久的 | ✅ | ✅ | ✅ | 0ms |
| `eval_020` | `need_location` | 好想吃鰻魚飯喔！ | ✅ | ✅ | ✅ | 0ms |
