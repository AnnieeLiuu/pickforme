# -*- coding: utf-8 -*-
"""PickForMe 4-Tier LLMOps 評測與全景河流圖可觀測性看板生成器 (Observability Dashboard Reporter)

依循 docs/OBSERVABILITY_DESIGN_SYSTEM.md 規範：
- 清爽淺色主題 (Apple / Stripe / Linear Clean Light Style)
- 3-Pane 滿版工作台佈局 (290px 題庫流 + 1fr 全景河流圖 + 440px 6維審計抽屜)
- Draw.io 5 大語意形狀規範 (膠囊I/O, 菱形決策, 圓柱快取, 矩形處理, 雙綠框護欄)
- Layer 3 垂直順序流水線 (快取查驗 ➔ 實體補齊 ➔ 地理安全 ➔ 電話秒通 ➔ 快取落庫)
- 嚴格落實 Google 3.5+ 防誤殺職人名店哲學，由 Tabelog 老饕口コミ終極裁判
- 審計抽屜資訊階層：中段節點聚焦自身技術 I/O，僅交付終點展開 Telegram 最終排版卡片
"""

import json
from pathlib import Path
from typing import Any, Optional

from src.eval.evaluator import EvalSummary


def generate_markdown_report(summary: EvalSummary) -> str:
    """生成終端機與文件用 Markdown 格式評測報告"""
    status_icon = "🟢 PASSED" if summary.all_passed_gate else "🔴 FAILED"

    lines = [
        "# 🍱 PickForMe 4-Tier 離線評測體系報告 (Offline LLMOps Benchmark)",
        "",
        f"> **評測狀態**：{status_icon} (CI Gatekeeper: T1≥90%, T2≥0.90, T3≥95%)",
        f"> **總測試案例數**：{summary.total_cases} 題",
        "",
        "## 📊 核心指標看板 (Key Performance Indicators)",
        "",
        "| 層級 | 評測維度 | 目標門檻 | 實測表現 | 狀態 |",
        "| :--- | :--- | :---: | :---: | :---: |",
        f"| **Tier 1** | 意圖與參數抽取率 (Intent Accuracy) | ≥ 90.0% | **{summary.tier1_intent_accuracy * 100:.1f}%** | {'✅' if summary.tier1_intent_accuracy >= 0.90 else '❌'} |",
        f"| **Tier 2** | 實體比對解析度 (Entity Resolver F1) | ≥ 0.90 | **{summary.tier2_entity_f1:.4f}** | {'✅' if summary.tier2_entity_f1 >= 0.90 else '❌'} |",
        f"| **Tier 3** | 數值護欄與格式遵從 (Guardrail Compliance) | ≥ 95.0% | **{summary.tier3_guardrail_compliance * 100:.1f}%** | {'✅' if summary.tier3_guardrail_compliance >= 0.95 else '❌'} |",
        f"| **Tier 4** | 端到端綜合成功率 (E2E Task Success) | - | **{summary.tier4_e2e_success_rate * 100:.1f}%** | {'✅' if summary.tier4_e2e_success_rate >= 0.85 else '⚠️'} |",
        "",
        "## ⚡ 延遲基準統計 (Latency Benchmark)",
        "",
        f"- **平均延遲 (Average Latency)**：`{summary.latency_avg_ms} ms`",
        f"- **P50 延遲**：`{summary.latency_p50_ms} ms`",
        f"- **P90 延遲**：`{summary.latency_p90_ms} ms`",
        "",
        "## 📋 測試案例執行矩陣 (Cases Execution Matrix)",
        "",
        "| ID | 分類 | 測試提問句 | T1 (意圖) | T2 (比對) | T3 (護欄) | 耗時 |",
        "| :---: | :--- | :--- | :---: | :---: | :---: | :---: |",
    ]

    for c in summary.case_results:
        t1_icon = "✅" if c.tier1_pass else "❌"
        t2_icon = "✅" if c.tier2_pass else "❌"
        t3_icon = "✅" if c.tier3_pass else "❌"
        msg = c.user_message[:24] + "..." if len(c.user_message) > 24 else c.user_message
        lines.append(
            f"| `{c.case_id}` | `{c.category}` | {msg} | {t1_icon} | {t2_icon} | {t3_icon} | {c.latency_ms}ms |"
        )

    lines.append("")
    return "\n".join(lines)


# 🌟 全景河流圖 HTML 核心模板 (3-Pane Clean Light Slate Dashboard)
BASE_DASHBOARD_TEMPLATE = """<style>
/* 🌟 全域清爽現代淺色主題 (Apple / Stripe / Linear Clean Light Style) */
html, body, #frame-content, #frame-header, .header, header {
  background: #f8fafc !important;
  color: #0f172a !important;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  margin: 0;
  padding: 0;
}
.header {
  background: #ffffff !important;
  border-bottom: 1px solid #e2e8f0 !important;
}
.brand-copy, .brand a {
  color: #334155 !important;
}

/* 滿版寬螢幕工作台佈局：充分利用 100% 水平空間，杜絕左右大片空白 */
.dashboard-container {
  max-width: 1780px;
  width: 98%;
  margin: 0 auto;
  padding: 14px 10px 140px 10px;
  box-sizing: border-box;
}

/* 3-Pane 專業監控工作台格局：
   左欄 (290px)：歷史對話與評測題庫清單 (解決「只能最後三次嗎？」)
   中欄 (1fr)：高維度全景河流圖 (完整還原 5 大層級)
   右欄 (440px)：6 維審計抽屜 (永久釘選在右側，向下滾動時懸浮跟隨)
*/
.three-pane-grid {
  display: grid;
  grid-template-columns: 290px minmax(720px, 1fr) 440px;
  gap: 16px;
  align-items: start;
  width: 100%;
}

/* 流程圖節點核心樣式 */
.river-node {
  border-radius: 9px;
  padding: 10px 14px;
  position: relative;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  cursor: pointer;
  user-select: none;
  box-sizing: border-box;
}
.node-layer-tag {
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.5px;
  text-transform: uppercase;
  margin-bottom: 2px;
}
.node-title {
  font-size: 12px;
  font-weight: 700;
  line-height: 1.35;
}
.node-desc {
  font-size: 10px;
  margin-top: 2px;
}
.node-badge {
  font-size: 10px;
  margin-top: 4px;
  padding: 2px 7px;
  border-radius: 4px;
  display: inline-block;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}
.node-status-flag {
  font-size: 9px;
  font-weight: 700;
  margin-bottom: 3px;
  display: flex;
  align-items: center;
  gap: 4px;
}

/* 🌟 走過節點 (Active)：純白底 + 活潑湛藍邊框 + 輕盈微陰影 */
.river-node.node-active {
  background: #ffffff;
  border: 1.5px solid #2563eb;
  color: #0f172a;
  box-shadow: 0 2px 10px rgba(37, 99, 235, 0.1);
}
.river-node.node-active:hover {
  transform: translateY(-2px);
  border-color: #1d4ed8;
  box-shadow: 0 4px 16px rgba(37, 99, 235, 0.18);
}
.river-node.node-active .node-layer-tag {
  color: #2563eb;
}
.river-node.node-active .node-status-flag {
  color: #2563eb;
}
.river-node.node-active .node-badge {
  background: #eff6ff;
  color: #1d4ed8;
  border: 1px solid #bfdbfe;
}

/* ⚪ 未走過節點 (Bypassed / Blueprint)：
   清爽實體淺灰卡片 + 清楚的灰邊框 (絕對不再消失！)
*/
.river-node.node-inactive {
  background: #f1f5f9;
  border: 1.5px dashed #94a3b8;
  color: #475569;
}
.river-node.node-inactive:hover {
  background: #e2e8f0;
  border-color: #64748b;
  border-style: solid;
  transform: translateY(-2px);
  box-shadow: 0 3px 8px rgba(0, 0, 0, 0.05);
}
.river-node.node-inactive .node-layer-tag {
  color: #64748b;
}
.river-node.node-inactive .node-status-flag {
  color: #64748b;
}
.river-node.node-inactive .node-title {
  color: #334155;
}
.river-node.node-inactive .node-desc {
  color: #64748b;
}
.river-node.node-inactive .node-badge {
  background: #e2e8f0;
  color: #64748b;
  border: 1px solid #cbd5e1;
}

/* 🌊 垂直連接線容器 */
.stream-container {
  display: flex;
  justify-content: center;
  align-items: center;
  height: 20px;
  width: 100%;
}
.stream-line-v {
  width: 2px;
  height: 20px;
}
.stream-line-v.stream-active {
  background: repeating-linear-gradient(
    to bottom,
    #2563eb,
    #2563eb 4px,
    transparent 4px,
    transparent 8px
  );
  animation: streamFlow 0.8s linear infinite;
}
.stream-line-v.stream-inactive {
  background: repeating-linear-gradient(
    to bottom,
    #94a3b8,
    #94a3b8 3px,
    transparent 3px,
    transparent 6px
  );
}

@keyframes streamFlow {
  from { background-position: 0 0; }
  to { background-position: 0 16px; }
}

/* 🌟 SVG 流光動畫 (讓中段的 SVG 樹狀連接線也能持續動態奔流！) */
@keyframes svgStreamFlow {
  from {
    stroke-dashoffset: 20;
  }
  to {
    stroke-dashoffset: 0;
  }
}
.svg-flow-active {
  animation: svgStreamFlow 0.8s linear infinite !important;
}


/* ================= 🌟 DRAW.IO 經典流程圖形狀系統 ================= */
/* 🟡 1. 外部端點 (Terminal / I/O) - 經典全圓角膠囊形狀 */
.flow-terminal {
  border-radius: 28px !important;
  background: #f8fafc;
  border: 1.5px solid #2563eb !important;
  box-shadow: 0 2px 6px rgba(37, 99, 235, 0.08);
}

/* ⚙️ 2. 核心動作 (Process) - 標準現代矩形 */
.flow-process {
  border-radius: 8px !important;
  border-left: 4.5px solid #2563eb !important;
}

/* ◆ 3. 條件決策 (Decision) - 帶有決策標章與判斷邊界 */
.flow-decision {
  border-radius: 10px !important;
  border-left: 4.5px solid #0284c7 !important;
  position: relative;
}
.flow-decision::before {
  content: "◆ 決策判定 (DECISION)";
  position: absolute;
  top: -9px;
  right: 12px;
  background: #f0f9ff;
  border: 1px solid #7dd3fc;
  color: #0284c7;
  font-size: 8.5px;
  font-weight: 800;
  padding: 1px 7px;
  border-radius: 8px;
  letter-spacing: 0.3px;
}

/* 🗄️ 4. 資料庫儲存 (Database) - Draw.io 經典圓柱體輪廓 (Cylinder Shape) */
.flow-database {
  border-radius: 16px 16px 12px 12px !important;
  border: 1.5px solid #c084fc !important;
  background: linear-gradient(180deg, #f3e8ff 0%, #faf5ff 28%, #ffffff 100%) !important;
  position: relative;
  box-shadow: 0 2px 8px rgba(168, 85, 247, 0.09);
}
.flow-database::before {
  content: "";
  position: absolute;
  top: 4px;
  left: 12%;
  right: 12%;
  height: 4px;
  border-radius: 50%;
  background: rgba(168, 85, 247, 0.25);
}

/* 🛡️ 5. 安全護欄 (Guard) - 雙邊框盾牌防禦 */
.flow-guard {
  border-radius: 8px !important;
  border: 1.5px dashed #059669 !important;
  background: #f0fdf4 !important;
  position: relative;
}
.flow-guard::before {
  content: "🛡️ SAFETY GUARD";
  position: absolute;
  top: -8px;
  right: 10px;
  background: #ecfdf5;
  border: 1px solid #6ee7b7;
  color: #059669;
  font-size: 8px;
  font-weight: 800;
  padding: 1px 5px;
  border-radius: 6px;
  letter-spacing: 0.3px;
}


/* 🌟 右側審計抽屜專屬精緻滾動條 (保證任何螢幕比例下皆可流暢滾動到底) */
#node-inspector-drawer::-webkit-scrollbar {
  width: 7px;
}
#node-inspector-drawer::-webkit-scrollbar-track {
  background: #f1f5f9;
  border-radius: 8px;
}
#node-inspector-drawer::-webkit-scrollbar-thumb {
  background: #94a3b8;
  border-radius: 8px;
}
#node-inspector-drawer::-webkit-scrollbar-thumb:hover {
  background: #64748b;
}

/* 分層背景標籤 */
.layer-section-title {
  font-size: 10px;
  font-weight: 800;
  color: #64748b;
  text-transform: uppercase;
  letter-spacing: 0.8px;
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 12px 0 6px 0;
  width: 100%;
}
.layer-section-title::before, .layer-section-title::after {
  content: "";
  flex: 1;
  height: 1px;
  background: #e2e8f0;
}

/* 左側歷史對話條目 */
.history-item {
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 10px 12px;
  margin-bottom: 8px;
  cursor: pointer;
  transition: all 0.18s ease;
}
.history-item:hover {
  background: #f8fafc;
  border-color: #cbd5e1;
  transform: translateX(2px);
}
.history-item.active {
  background: #eff6ff;
  border: 1.5px solid #2563eb;
  box-shadow: 0 2px 8px rgba(37, 99, 235, 0.12);
}
.history-item.active .hist-title {
  color: #1d4ed8;
  font-weight: 700;
}
</style>

<div class="dashboard-container">

  <!-- 頂部 Header 與 運行健康指標 -->
  <div style="margin-bottom: 14px; display: flex; justify-content: space-between; align-items: flex-end;">
    <div>
      <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 3px;">
        <span style="background: #2563eb; color: #ffffff; font-size: 10px; font-weight: bold; padding: 2px 7px; border-radius: 4px;">OBSERVABILITY WORKSTATION</span>
        <h2 style="font-size: 21px; font-weight: 800; color: #0f172a; margin: 0; letter-spacing: -0.5px;">PickForMe 全鏈路全景河流圖中控台</h2>
      </div>
      <p style="font-size: 12px; color: #64748b; margin: 0;">
        穿透 5 大架構分層 · <strong>即時全量對話穿透盤點</strong> · <strong>走過節點動態流光</strong> · <strong>未走過節點查閱略過決策</strong> · <strong>Groq OTPM 1000 專屬額度監控</strong>
      </p>
    </div>
    <div style="font-size: 11px; color: #059669; background: #ecfdf5; border: 1px solid #a7f3d0; padding: 4px 10px; border-radius: 20px; font-weight: bold; display: flex; align-items: center; gap: 6px;">
      <span style="width: 7px; height: 7px; background: #10b981; border-radius: 50%; display: inline-block;"></span>
      系統整體運行健康 (All Services Operational · GitLab CI 100% Passed)
    </div>
  </div>

  <!-- 1. 頂部 3 大核心維運監控卡片 (滿版橫幅，清爽白底) -->
  <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; margin-bottom: 16px;">

    <!-- 監控卡 1: Groq OTPM 1000 額度防線 -->
    <div style="background: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px; padding: 14px 18px; box-shadow: 0 1px 3px rgba(0,0,0,0.04);">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
        <div style="font-size: 11px; font-weight: 700; color: #475569; text-transform: uppercase;">⚡ Groq 每分鐘 Token 上限 (OTPM 1000)</div>
        <span style="font-size: 10px; background: #ecfdf5; color: #059669; padding: 2px 8px; border-radius: 12px; font-weight: 700; border: 1px solid #a7f3d0;">安全水位 (38%)</span>
      </div>
      <div style="display: flex; align-items: baseline; gap: 8px;">
        <div style="font-size: 26px; font-weight: 800; color: #0f172a;">380</div>
        <div style="font-size: 12px; color: #64748b;">/ 1,000 Tokens · 剩餘 620</div>
      </div>
      <div style="height: 6px; width: 100%; background: #f1f5f9; border-radius: 3px; margin: 8px 0; overflow: hidden;">
        <div style="height: 100%; width: 38%; background: linear-gradient(90deg, #10b981, #0284c7); border-radius: 3px;"></div>
      </div>
      <div style="font-size: 11px; color: #64748b; display: flex; justify-content: space-between;">
        <span>主力推論: <strong>qwen3.8-27b (460ms)</strong></span>
        <span style="color: #475569;">Gemini 3.8 Flash (備援待命中)</span>
      </div>
    </div>

    <!-- 監控卡 2: Google Places API 費用與擴圈守門 -->
    <div style="background: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px; padding: 14px 18px; box-shadow: 0 1px 3px rgba(0,0,0,0.04);">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
        <div style="font-size: 11px; font-weight: 700; color: #475569; text-transform: uppercase;">🗺️ Places API 費用與階梯擴圈防線</div>
        <span style="font-size: 10px; background: #eff6ff; color: #2563eb; padding: 2px 8px; border-radius: 12px; font-weight: 700; border: 1px solid #bfdbfe;">首輪高命中</span>
      </div>
      <div style="display: flex; align-items: baseline; gap: 8px;">
        <div style="font-size: 26px; font-weight: 800; color: #0f172a;">82.5%</div>
        <div style="font-size: 12px; color: #64748b;">800m 首輪直接命中率 (省下 1800m 查詢)</div>
      </div>
      <div style="height: 6px; width: 100%; background: #f1f5f9; border-radius: 3px; margin: 8px 0; overflow: hidden;">
        <div style="height: 100%; width: 82.5%; background: linear-gradient(90deg, #2563eb, #6366f1); border-radius: 3px;"></div>
      </div>
      <div style="font-size: 11px; color: #64748b; display: flex; justify-content: space-between;">
        <span>今日呼叫: <strong>42 次</strong> (已省下 35 次擴圈)</span>
        <span style="color: #059669; font-weight: 600;">GCP Places 額度安全</span>
      </div>
    </div>

    <!-- 監控卡 3: Neon DB 1GB 容量防線 ＆ Tabelog 快取 -->
    <div style="background: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px; padding: 14px 18px; box-shadow: 0 1px 3px rgba(0,0,0,0.04);">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
        <div style="font-size: 11px; font-weight: 700; color: #475569; text-transform: uppercase;">🗄️ Neon DB 1GB 防線 ＆ 快取命中率</div>
        <span style="font-size: 10px; background: #faf5ff; color: #7c3aed; padding: 2px 8px; border-radius: 12px; font-weight: 700; border: 1px solid #ddd6fe;">冷熱分層良好</span>
      </div>
      <div style="display: flex; align-items: baseline; gap: 6px; flex-wrap: nowrap;">
        <div style="font-size: 26px; font-weight: 800; color: #0f172a; white-space: nowrap;">14.8 MB</div>
        <div style="font-size: 12px; color: #64748b; white-space: nowrap;">/ 1,024 MB 容量 (1.4% 使用率)</div>
      </div>
      <div style="height: 6px; width: 100%; background: #f1f5f9; border-radius: 3px; margin: 8px 0; overflow: hidden;">
        <div style="height: 100%; width: 1.4%; min-width: 8px; background: #7c3aed; border-radius: 3px;"></div>
      </div>
      <div style="font-size: 11px; color: #64748b; display: flex; justify-content: space-between;">
        <span>Tabelog 7天快取率: <strong style="color: #0f172a;">78.2%</strong></span>
        <span style="color: #059669; font-weight: 600;">0ms 秒級出快取</span>
      </div>
    </div>

  </div>

  <!-- 2. 主區域：3-Pane 專業監控工作台格局 -->
  <div class="three-pane-grid">

    <!-- ========== 左欄 (290px)：歷史對話與評測題庫清單 ========== -->
    <div style="background: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px; padding: 14px; box-shadow: 0 1px 3px rgba(0,0,0,0.04);">

      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; border-bottom: 1px solid #f1f5f9; padding-bottom: 8px;">
        <div style="font-size: 12px; font-weight: 800; color: #0f172a; display: flex; align-items: center; gap: 5px;">
          <span>📋 歷史對話與題庫流</span>
        </div>
        <span style="font-size: 10px; background: #f1f5f9; color: #475569; padding: 2px 6px; border-radius: 4px; font-weight: 600;">共 8 筆記錄</span>
      </div>

      <!-- 快捷情境篩選標籤 -->
      <div style="display: flex; gap: 4px; margin-bottom: 12px; flex-wrap: wrap;">
        <span style="font-size: 10px; background: #2563eb; color: #fff; padding: 2px 7px; border-radius: 4px; font-weight: 600; cursor: pointer;">全部 (8)</span>
        <span style="font-size: 10px; background: #f1f5f9; color: #475569; padding: 2px 7px; border-radius: 4px; cursor: pointer;">正常搜尋 (5)</span>
        <span style="font-size: 10px; background: #f1f5f9; color: #475569; padding: 2px 7px; border-radius: 4px; cursor: pointer;">截流 (1)</span>
        <span style="font-size: 10px; background: #f1f5f9; color: #475569; padding: 2px 7px; border-radius: 4px; cursor: pointer;">追問 (2)</span>
      </div>

      <!-- 對話列表 (多筆案例自由切換) -->
      <div style="display: flex; flex-direction: column; max-height: 820px; overflow-y: auto;">

        <!-- Case 1: 正常搜尋推薦 -->
        <div class="history-item active" id="hist-case_search" onclick="switchCase('case_search')">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 2px;">
            <span style="font-size: 10px; background: #ecfdf5; color: #059669; font-weight: 700; padding: 1px 5px; border-radius: 3px;">800m 命中</span>
            <span style="font-size: 10px; color: #94a3b8;">14:15:22</span>
          </div>
          <div class="hist-title" style="font-size: 12px; font-weight: 600; color: #0f172a; margin-bottom: 2px;">🍜 神保町車站 飯糰</div>
          <div style="font-size: 10px; color: #64748b;">5家候選 · 快取命中3/5 · ⏱️ 1,242ms</div>
        </div>

        <!-- Case 2: 缺乏地點截流 -->
        <div class="history-item" id="hist-case_no_loc" onclick="switchCase('case_no_loc')">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 2px;">
            <span style="font-size: 10px; background: #fef2f2; color: #dc2626; font-weight: 700; padding: 1px 5px; border-radius: 3px;">防呆截流</span>
            <span style="font-size: 10px; color: #94a3b8;">14:10:05</span>
          </div>
          <div class="hist-title" style="font-size: 12px; font-weight: 600; color: #0f172a; margin-bottom: 2px;">🛑 我想吃拉麵</div>
          <div style="font-size: 10px; color: #64748b;">缺地點 · 即時引導句 · ⏱️ 285ms</div>
        </div>

        <!-- Case 3: 追問上一輪記憶 -->
        <div class="history-item" id="hist-case_followup" onclick="switchCase('case_followup')">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 2px;">
            <span style="font-size: 10px; background: #faf5ff; color: #7c3aed; font-weight: 700; padding: 1px 5px; border-radius: 3px;">Session 追問</span>
            <span style="font-size: 10px; color: #94a3b8;">14:02:18</span>
          </div>
          <div class="hist-title" style="font-size: 12px; font-weight: 600; color: #0f172a; margin-bottom: 2px;">💬 第二家今天有開嗎？</div>
          <div style="font-size: 10px; color: #64748b;">命中第2家 · 零API消耗 · ⏱️ 190ms</div>
        </div>

        <!-- Case 4: 新宿 燒肉 (擴圈 1800m 救援) -->
        <div class="history-item" id="hist-case_rescue" onclick="switchCase('case_search')">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 2px;">
            <span style="font-size: 10px; background: #fffbeb; color: #d97706; font-weight: 700; padding: 1px 5px; border-radius: 3px;">1800m 擴圈</span>
            <span style="font-size: 10px; color: #94a3b8;">13:50:41</span>
          </div>
          <div class="hist-title" style="font-size: 12px; font-weight: 600; color: #0f172a; margin-bottom: 2px;">🥩 新宿站 炭火燒肉</div>
          <div style="font-size: 10px; color: #64748b;">800m僅1家 ➔ 啟動擴圈救回 · ⏱️ 1,820ms</div>
        </div>

        <!-- Case 5: 銀座 壽司 (7天快取 100% 命中) -->
        <div class="history-item" id="hist-case_cache" onclick="switchCase('case_search')">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 2px;">
            <span style="font-size: 10px; background: #ecfdf5; color: #059669; font-weight: 700; padding: 1px 5px; border-radius: 3px;">快取 100%</span>
            <span style="font-size: 10px; color: #94a3b8;">13:30:10</span>
          </div>
          <div class="hist-title" style="font-size: 12px; font-weight: 600; color: #0f172a; margin-bottom: 2px;">🍣 銀座 高級江戶前壽司</div>
          <div style="font-size: 10px; color: #64748b;">4家全部快取秒出 · ⏱️ 640ms</div>
        </div>

        <!-- Case 6: 澀谷 咖啡廳 -->
        <div class="history-item" id="hist-case_cafe" onclick="switchCase('case_search')">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 2px;">
            <span style="font-size: 10px; background: #ecfdf5; color: #059669; font-weight: 700; padding: 1px 5px; border-radius: 3px;">800m 命中</span>
            <span style="font-size: 10px; color: #94a3b8;">13:12:00</span>
          </div>
          <div class="hist-title" style="font-size: 12px; font-weight: 600; color: #0f172a; margin-bottom: 2px;">☕ 澀谷 安靜讀書咖啡</div>
          <div style="font-size: 10px; color: #64748b;">插座 Wi-Fi · ⏱️ 1,120ms</div>
        </div>

        <!-- Case 7: 淺草 壽喜燒 -->
        <div class="history-item" id="hist-case_sukiyaki" onclick="switchCase('case_search')">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 2px;">
            <span style="font-size: 10px; background: #ecfdf5; color: #059669; font-weight: 700; padding: 1px 5px; border-radius: 3px;">800m 命中</span>
            <span style="font-size: 10px; color: #94a3b8;">12:45:19</span>
          </div>
          <div class="hist-title" style="font-size: 12px; font-weight: 600; color: #0f172a; margin-bottom: 2px;">🍲 淺草 百年老店壽喜燒</div>
          <div style="font-size: 10px; color: #64748b;">Tabelog 百名店 · ⏱️ 1,310ms</div>
        </div>

      </div>

    </div>

    <!-- ========== 中欄 (1fr)：高維度全景河流圖 ========== -->
    <div id="river-flow-container" style="background: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px; padding: 22px 16px; position: relative; box-shadow: 0 1px 3px rgba(0,0,0,0.04);">

      <!-- 🌟 全域 SVG 覆蓋層 (專門用來繪製從追問節點一路直穿最底部答案卡片的 Bypass 快速旁道線) -->
      <svg id="svg-overlay" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; pointer-events: none; overflow: visible; z-index: 2;">
        <defs>
          <marker id="arrow-blue" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M 0 1 L 10 5 L 0 9 z" fill="#2563eb" />
          </marker>
          <marker id="arrow-gray" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M 0 1 L 10 5 L 0 9 z" fill="#94a3b8" />
          </marker>
        </defs>
        <!-- 左側缺地點截流直達最底部答案卡片之連線 -->
        <path id="svg-stream-noloc-bypass" d="" fill="none" stroke="#94a3b8" stroke-width="2" stroke-dasharray="5 5" marker-end="url(#arrow-gray)" />
        <!-- 右側追問分支直達最底部答案卡片之連線 -->
        <path id="svg-stream-followup-bypass" d="" fill="none" stroke="#94a3b8" stroke-width="2" stroke-dasharray="5 5" marker-end="url(#arrow-gray)" />
      </svg>

      <!-- 左側截流浮動膠囊標籤 -->
      <div id="badge-bypass-noloc-floating" style="position: absolute; display: none; z-index: 3; background: #eff6ff; border: 1.5px solid #93c5fd; color: #1d4ed8; font-size: 10px; font-weight: 800; padding: 4px 10px; border-radius: 20px; box-shadow: 0 2px 8px rgba(37,99,235,0.12); white-space: nowrap; pointer-events: none; transform: translateY(-50%);">
        ⚡ 缺地點引導直達最後答案 (45ms 省搜尋)
      </div>

      <!-- 右側追問浮動膠囊標籤 -->
      <div id="badge-bypass-floating" style="position: absolute; display: none; z-index: 3; background: #eff6ff; border: 1.5px solid #93c5fd; color: #1d4ed8; font-size: 10px; font-weight: 800; padding: 4px 10px; border-radius: 20px; box-shadow: 0 2px 8px rgba(37,99,235,0.12); white-space: nowrap; pointer-events: none; transform: translateY(-50%);">
        ⚡ 追問直達最後答案 (Bypass 0 外部消耗)
      </div>

      <div style="display: flex; flex-direction: column; align-items: center; gap: 0; position: relative;">


        <!-- 🎨 Draw.io 標準流程圖形狀圖例 (Flowchart Shapes Legend) -->
        <div style="display: flex; align-items: center; justify-content: center; gap: 14px; width: 100%; margin-bottom: 12px; padding: 6px 12px; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; font-size: 10px; color: #475569; flex-wrap: wrap;">
          <span style="font-weight: 800; color: #0f172a; display: flex; align-items: center; gap: 4px;">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#2563eb" stroke-width="2"><path d="M3 3h18v18H3z"/></svg>
            Flowchart 圖例:
          </span>
          <span style="display: inline-flex; align-items: center; gap: 4px; background: #ffffff; padding: 2px 7px; border-radius: 12px; border: 1px solid #bfdbfe; color: #1d4ed8; font-weight: 700;">
            <span style="display: inline-block; width: 12px; height: 6px; border-radius: 6px; border: 1px solid #2563eb; background: #dbeafe;"></span>
            膠囊 (Terminal I/O)
          </span>
          <span style="display: inline-flex; align-items: center; gap: 4px; background: #ffffff; padding: 2px 7px; border-radius: 4px; border: 1px solid #e2e8f0; border-left: 3px solid #2563eb; font-weight: 700;">
            <span style="display: inline-block; width: 8px; height: 8px; border: 1px solid #2563eb; background: #eff6ff;"></span>
            矩形 (Process 動作)
          </span>
          <span style="display: inline-flex; align-items: center; gap: 4px; background: #ffffff; padding: 2px 7px; border-radius: 4px; border: 1px solid #bae6fd; border-left: 3px solid #0284c7; color: #0369a1; font-weight: 700;">
            <span>◆</span>
            菱形標記 (Decision 決策)
          </span>
          <span style="display: inline-flex; align-items: center; gap: 4px; background: #ffffff; padding: 2px 7px; border-radius: 6px; border: 1px solid #d8b4fe; color: #7e22ce; font-weight: 700;">
            <span>🗄️</span>
            圓柱體 (Database 資料庫)
          </span>
          <span style="display: inline-flex; align-items: center; gap: 4px; background: #ffffff; padding: 2px 7px; border-radius: 4px; border: 1px dashed #6ee7b7; color: #047857; font-weight: 700;">
            <span>🛡️</span>
            雙邊框 (Guard 護欄)
          </span>
        </div>

        <!-- ================= LAYER 1 ================= -->
        <div class="layer-section-title">Layer 1 · 意圖識別與結構化約束解析</div>

        <!-- 節點 0: Telegram 輸入 -->
        <div id="node-user-input" onclick="clickNode('user_input')" class="river-node node-active flow-terminal" style="width: 320px;">
          <div class="node-layer-tag">ENTRYPOINT</div>
          <div class="node-title">💬 Telegram 使用者問句輸入</div>
          <div id="badge-user-input" class="node-badge">「神保町車站 飯糰」</div>
        </div>

        <!-- 連接線 1 -->
        <div class="stream-container">
          <div class="stream-line-v stream-active" id="stream-1"></div>
        </div>

        <!-- 節點 1: State 1 Intent 解析 -->
        <div id="node-state-1" onclick="clickNode('state_1')" class="river-node node-active flow-decision" style="width: 340px;">
          <div class="node-layer-tag">INTENT EXTRACTOR</div>
          <div class="node-title">🧠 State 1: Micro-LLM 結構化意圖抽取</div>
          <div id="badge-state-1" class="node-badge">intent: search | location: 神保町 | cuisine: 飯糰</div>
        </div>

        <!-- 樹狀分支分流器 (採用 SVG 精準連線，起點至終點 100% 吻合，絕無斷裂) -->
        <div style="width: 716px; margin: 0 auto; height: 32px; display: block;">
          <svg width="716" height="32" viewBox="0 0 716 32" style="display: block; overflow: visible;">
            <!-- 中心主幹線垂直連線 -->
            <line id="svg-stream-main" x1="358" y1="0" x2="358" y2="32" stroke="#2563eb" stroke-width="2" stroke-dasharray="6 4" />
            <!-- 左側截流分支 (起點 358,16 ➔ 左折 85,16 ➔ 直通 85,32) -->
            <path id="svg-stream-no-loc" d="M 358 16 L 85 16 L 85 32" fill="none" stroke="#94a3b8" stroke-width="2" stroke-dasharray="4 4" />
            <!-- 右側追問分支 (起點 358,16 ➔ 右折 631,16 ➔ 直通 631,32) -->
            <path id="svg-stream-followup" d="M 358 16 L 631 16 L 631 32" fill="none" stroke="#94a3b8" stroke-width="2" stroke-dasharray="4 4" />
          </svg>
        </div>

        <!-- 三叉分流區塊 (716px 寬度：170 + 18 + 340 + 18 + 170 = 716px) -->
        <div style="display: grid; grid-template-columns: 170px 340px 170px; gap: 18px; align-items: start; width: 716px; margin: 0 auto;">

          <!-- 分支 A: 缺乏地點截流 (完整向下流轉至推播結束) -->
          <div style="display: flex; flex-direction: column; align-items: center; width: 170px;">
            <!-- 💡 決策判定標籤 -->
            <div id="badge-route-reason-noloc" style="font-size: 9px; font-weight: 700; margin-bottom: 6px; padding: 2px 6px; border-radius: 4px; border: 1px dashed #cbd5e1; color: #64748b; background: #f8fafc; text-align: center; width: 100%; box-sizing: border-box; line-height: 1.3;">
              💡 判定: location == null
            </div>
            <div id="node-branch-no-loc" onclick="clickNode('branch_no_loc')" class="river-node node-inactive flow-guard" style="width: 170px; text-align: center;">
              <div class="node-status-flag" id="flag-branch-no-loc">⚪ 條件不符略過</div>
              <div class="node-title" style="font-size: 11px;">🛑 缺乏地標截流</div>
              <div class="node-desc">識別缺地點，啟動導引</div>
            </div>

            <!-- 向下旁道直通引導標籤 -->
            <div style="display: flex; flex-direction: column; align-items: center; margin-top: 8px;">
              <div id="tag-bypass-noloc-start" style="font-size: 9px; color: #64748b; background: #f8fafc; border: 1px dashed #cbd5e1; padding: 3px 8px; border-radius: 4px; font-weight: 700; white-space: nowrap; display: flex; align-items: center; gap: 4px;">
                <span>⚡ 旁道直通最終答案</span>
                <span>↴</span>
              </div>
            </div>
          </div>

          <!-- 主幹線 -->
          <div style="display: flex; flex-direction: column; align-items: center; width: 340px;">
            <!-- 💡 決策判定標籤 -->
            <div id="badge-route-reason-main" style="font-size: 9.5px; font-weight: 800; margin-bottom: 6px; padding: 3px 8px; border-radius: 4px; border: 1.5px solid #93c5fd; color: #1d4ed8; background: #eff6ff; text-align: center; width: 100%; box-sizing: border-box; line-height: 1.35;">
              💡 決策判定: intent == "search" 且具備明確區域「神保町」 ➔ 准予深度檢索
            </div>
            <div id="node-coords" onclick="clickNode('coords')" class="river-node node-active flow-process" style="width: 340px;">
              <div class="node-status-flag" id="flag-coords">✓ 本次已執行</div>
              <div class="node-layer-tag">CONSTRAINT RESOLVER</div>
              <div class="node-title">🧭 Google Geocoding 座標 ＆ 黑名單排除</div>
              <div class="node-badge">(35.6959, 139.7576) · 排除已看過: 0 家</div>
            </div>
          </div>

          <!-- 分支 C: 追問上一輪記憶 (直通最底部答案卡片) -->
          <div style="display: flex; flex-direction: column; align-items: center; width: 170px;">
            <!-- 💡 決策判定標籤 -->
            <div id="badge-route-reason-followup" style="font-size: 9px; font-weight: 700; margin-bottom: 6px; padding: 2px 6px; border-radius: 4px; border: 1px dashed #cbd5e1; color: #64748b; background: #f8fafc; text-align: center; width: 100%; box-sizing: border-box; line-height: 1.3;">
              💡 判定: intent == "follow_up"
            </div>
            <div id="node-branch-followup" onclick="clickNode('branch_followup')" class="river-node node-inactive" style="width: 170px; text-align: center;">
              <div class="node-status-flag" id="flag-branch-followup">⚪ 條件不符略過</div>
              <div class="node-layer-tag">SESSION FAST-PATH</div>
              <div class="node-title" style="font-size: 11px;">💬 追問記憶解答</div>
              <div class="node-badge" style="font-size: 9px; line-height: 1.3;">讀取上一輪第 2 家</div>
            </div>

            <!-- 向下旁道直通引導標籤 -->
            <div style="display: flex; flex-direction: column; align-items: center; margin-top: 8px;">
              <div id="tag-bypass-start" style="font-size: 9px; color: #64748b; background: #f8fafc; border: 1px dashed #cbd5e1; padding: 3px 8px; border-radius: 4px; font-weight: 700; white-space: nowrap; display: flex; align-items: center; gap: 4px;">
                <span>⚡ 旁道直通最終答案</span>
                <span>↴</span>
              </div>
            </div>
          </div>

        </div>

        <!-- ================= LAYER 2 ================= -->
        <div class="layer-section-title">Layer 2 · 智慧階梯搜尋與老饕潛力預篩</div>

        <div class="stream-container">
          <div class="stream-line-v stream-active" id="stream-layer-2"></div>
        </div>

        <!-- 階梯搜尋 (800m 命中 ➔ 1800m 擴圈備用) -->
        <div style="display: flex; align-items: center; gap: 8px; width: 380px;">
          <div id="node-state-2" onclick="clickNode('state_2')" class="river-node node-active flow-process" style="flex: 1;">
            <div class="node-status-flag" id="flag-state-2">✓ 本次已執行</div>
            <div class="node-layer-tag">PLACES API (JA)</div>
            <div class="node-title">🗺️ Places 800m 首輪檢索</div>
            <div class="node-badge">命中 5 家 (中止擴圈)</div>
          </div>

          <div style="display: flex; flex-direction: column; align-items: center; width: 44px;">
            <div style="font-size: 8px; color: #94a3b8; white-space: nowrap; margin-bottom: 2px;">不足3家</div>
            <div style="width: 100%; height: 2px; border-top: 2px dashed #cbd5e1;"></div>
          </div>

          <div id="node-state-2-rescue" onclick="clickNode('state_2_rescue')" class="river-node node-inactive flow-process" style="width: 110px; text-align: center; padding: 10px 8px;">
            <div class="node-status-flag" id="flag-state-2-rescue" style="justify-content: center;">⚪ 候選足略過</div>
            <div class="node-title" style="font-size: 11px;">🔄 1800m 擴圈</div>
            <div class="node-desc">備用保底</div>
          </div>
        </div>

        <div class="stream-container">
          <div class="stream-line-v stream-active" id="stream-prefilter"></div>
        </div>

        <!-- 0ms 老饕潛力預篩節點 -->
        <div id="node-prefilter" onclick="clickNode('prefilter')" class="river-node node-active flow-process" style="width: 380px;">
          <div class="node-status-flag" id="flag-prefilter">✓ 本次已執行</div>
          <div class="node-layer-tag">HEURISTIC PREFILTER · 0MS</div>
          <div class="node-title">🎯 0ms 老饕潛力預篩 (Top 4 種子 + 2 備選)</div>
          <div class="node-badge" style="background: #eff6ff; color: #1d4ed8; border: 1px solid #bfdbfe;">排除連鎖 · 寬鬆 Google 3.5+ 保底防誤殺老店</div>
        </div>

        <!-- ================= LAYER 3 (清晰垂直順序流水線) ================= -->
        <div class="layer-section-title">Layer 3 · 雙層漏斗實體對齊與快取沉澱</div>

        <div class="stream-container">
          <div class="stream-line-v stream-active" id="stream-layer-3"></div>
        </div>

        <!-- ① Step 1: 🗄️ Neon 7天快取查驗 (快取優先原則) -->
        <div id="node-cache-check" onclick="clickNode('cache_check')" class="river-node node-active flow-database" style="width: 380px;">
          <div class="node-status-flag" id="flag-cache-check">✓ 快取優先查驗</div>
          <div class="node-layer-tag">NEON DB 快取查驗</div>
          <div class="node-title">🗄️ 7天快取查驗 (tabelog_cache)</div>
          <div class="node-badge" style="background: #f3e8ff; color: #7e22ce; border: 1px solid #d8b4fe;">3 家已收錄 · 0ms 秒出免爬取</div>
        </div>

        <!-- 連接線 1 ➔ 2 帶條件標籤 -->
        <div class="stream-container" style="height: 28px; position: relative;">
          <div class="stream-line-v stream-active" id="stream-cache-to-grounding" style="height: 28px;"></div>
          <div style="position: absolute; background: #ffffff; border: 1px solid #cbd5e1; color: #64748b; font-size: 8.5px; font-weight: 700; padding: 1px 7px; border-radius: 8px; z-index: 2;">
            ⚪ 未命中快取 (Cache Miss) ➔ 聯網即時補齊
          </div>
        </div>

        <!-- ② Step 2: ⚡ Yahoo Grounding 實時檢索 -->
        <div id="node-grounding" onclick="clickNode('grounding')" class="river-node node-active flow-process" style="width: 380px;">
          <div class="node-status-flag" id="flag-grounding">✓ 即時聯網補齊</div>
          <div class="node-layer-tag">YAHOO GROUNDING 實時檢索</div>
          <div class="node-title">⚡ Yahoo Grounding 檢索</div>
          <div class="node-badge">2 家未命中快取 ➔ 即時聯網抓取網址與評分</div>
        </div>

        <!-- 連接線 2 ➔ 3 帶條件標籤 -->
        <div class="stream-container" style="height: 28px; position: relative;">
          <div class="stream-line-v stream-active" id="stream-grounding-to-geo" style="height: 28px;"></div>
          <div style="position: absolute; background: #ffffff; border: 1px solid #cbd5e1; color: #2563eb; font-size: 8.5px; font-weight: 700; padding: 1px 7px; border-radius: 8px; z-index: 2;">
            🎯 候選名單就緒 ➔ 進入實體防偽漏斗
          </div>
        </div>

        <!-- ③ Step 3: 🛡️ 都道府縣一票否決 (地理安全門檻) -->
        <div id="node-geo-veto" onclick="clickNode('geo_veto')" class="river-node node-active flow-guard" style="width: 380px;">
          <div class="node-status-flag" id="flag-geo-veto">✓ 行政區安全檢驗</div>
          <div class="node-layer-tag">SAFETY FILTER (PREFECTURE VETO)</div>
          <div class="node-title">🛡️ 都道府縣一票否決</div>
          <div class="node-badge" style="background: #ecfdf5; color: #047857; border: 1px solid #6ee7b7;">0 家跨區 (100% 東京都) · 徹底杜絕同名異地漂移</div>
        </div>

        <!-- 連接線 3 ➔ 4 帶條件標籤 -->
        <div class="stream-container" style="height: 28px; position: relative;">
          <div class="stream-line-v stream-active" id="stream-geo-to-resolver" style="height: 28px;"></div>
          <div style="position: absolute; background: #ffffff; border: 1px solid #cbd5e1; color: #059669; font-size: 8.5px; font-weight: 700; padding: 1px 7px; border-radius: 8px; z-index: 2;">
            ✓ 同行政區放行 ➔ 檢驗市話吻合
          </div>
        </div>

        <!-- ④ Step 4: 📞 市話 Level 1 吻合 (0ms 快速通關) -->
        <div id="node-resolver" onclick="clickNode('resolver')" class="river-node node-active flow-decision" style="width: 380px;">
          <div class="node-status-flag" id="flag-resolver">✓ 市話快速通關</div>
          <div class="node-layer-tag">ENTITY RESOLVER (FAST-PATH)</div>
          <div class="node-title">📞 市話 Level 1 吻合</div>
          <div class="node-badge">0ms 市話秒通 · 節省 100% Micro-LLM 仲裁成本</div>
        </div>

        <!-- 連接線 4 ➔ 5 帶條件標籤 -->
        <div class="stream-container" style="height: 28px; position: relative;">
          <div class="stream-line-v stream-active" id="stream-db-cache" style="height: 28px;"></div>
          <div style="position: absolute; background: #ffffff; border: 1px solid #cbd5e1; color: #7e22ce; font-size: 8.5px; font-weight: 700; padding: 1px 7px; border-radius: 8px; z-index: 2;">
            💾 實體精確對齊 ➔ 寫入快取庫持久化
          </div>
        </div>

        <!-- 💾 資料庫節點 1: 寫入 tabelog_cache 與 restaurants -->
        <div id="node-db-cache-write" onclick="clickNode('db_cache_write')" class="river-node node-active flow-database" style="width: 380px; background: #faf5ff; border: 1.5px solid #c084fc;">
          <div class="node-status-flag" id="flag-db-cache-write" style="color: #9333ea;">✓ 快取持久化</div>
          <div class="node-layer-tag" style="color: #9333ea;">NEON POSTGRESQL · PERSISTENCE</div>
          <div class="node-title">💾 批次落庫 tabelog_cache ＆ restaurants 表</div>
          <div class="node-badge" style="background: #f3e8ff; color: #7e22ce; border: 1px solid #d8b4fe;">UPSERT 3 家口碑評分 · 7 天免爬取 TTL</div>
        </div>

        <!-- ================= LAYER 4 ================= -->
        <div class="layer-section-title">Layer 4 · 老饕食評 100% 覆蓋與 pgvector 語意重排</div>

        <div class="stream-container">
          <div class="stream-line-v stream-active" id="stream-layer-4"></div>
        </div>

        <!-- 食評抓取 ＆ 覆寫 Google 評論 -->
        <div id="node-review-coverage" onclick="clickNode('review_coverage')" class="river-node node-active flow-process" style="width: 380px;">
          <div class="node-status-flag" id="flag-review-coverage">✓ 本次已執行</div>
          <div class="node-layer-tag">REVIEW PRECEDENCE CONTRACT</div>
          <div class="node-title">🔥 Tabelog 口コミ 100% 覆蓋 Google 評論</div>
          <div class="node-badge">抓取老饕評論 · 標記 source=tabelog · 杜絕業配</div>
        </div>

        <div class="stream-container">
          <div class="stream-line-v stream-active" id="stream-vector"></div>
        </div>

        <!-- pgvector 餘弦相似度重排 -->
        <div id="node-vector-rag" onclick="clickNode('vector_rag')" class="river-node node-active flow-process" style="width: 380px;">
          <div class="node-status-flag" id="flag-vector-rag">✓ 本次已執行</div>
          <div class="node-layer-tag">NEON PGVECTOR (768-DIM)</div>
          <div class="node-title">📐 Cosine 語意相似度口感優選重排</div>
          <div class="node-badge">依「香脆海苔 軟糯米飯」向量比對 · 挑出 Top 3</div>
        </div>

        <!-- ================= LAYER 5 ================= -->
        <div class="layer-section-title">Layer 5 · 執行期防偽熔斷護欄與 Telegram 推播</div>

        <div class="stream-container">
          <div class="stream-line-v stream-active" id="stream-layer-5"></div>
        </div>

        <!-- 護欄雙重校驗 -->
        <div style="display: grid; grid-template-columns: 180px 180px; gap: 14px; width: 380px;">
          <div id="node-scoped-anchor" onclick="clickNode('scoped_anchor')" class="river-node node-active flow-guard">
            <div class="node-status-flag" id="flag-scoped-anchor">✓ 防偽通過</div>
            <div class="node-layer-tag">SCOPED ANCHOR</div>
            <div class="node-title">🔒 評分/店名雙向校驗</div>
            <div class="node-badge">零竄改 · 零幻覺</div>
          </div>

          <div id="node-validator" onclick="clickNode('validator')" class="river-node node-active flow-guard">
            <div class="node-status-flag" id="flag-validator">✓ 護欄放行</div>
            <div class="node-layer-tag">VALIDATOR.PY</div>
            <div class="node-title">🛡️ 禁令與排版掃描</div>
            <div class="node-badge">無套話 · 格式正確</div>
          </div>
        </div>

        <div class="stream-container">
          <div class="stream-line-v stream-active" id="stream-db-session"></div>
        </div>

        <!-- 💾 資料庫節點 2: 寫入 chat_sessions 與 agent_runs -->
        <div id="node-db-session-write" onclick="clickNode('db_session_write')" class="river-node node-active flow-database" style="width: 380px; background: #faf5ff; border: 1.5px solid #c084fc;">
          <div class="node-status-flag" id="flag-db-session-write" style="color: #9333ea;">✓ 對話記憶落庫</div>
          <div class="node-layer-tag" style="color: #9333ea;">SESSION & OBSERVABILITY DB</div>
          <div class="node-title">💾 寫入 chat_sessions 記憶庫 ＆ agent_runs 追蹤</div>
          <div class="node-badge" style="background: #f3e8ff; color: #7e22ce; border: 1px solid #d8b4fe;">保存本輪 3 家候選供追問 · 記錄 1,242ms E2E 鏈路日誌</div>
        </div>

        <div class="stream-container">
          <div class="stream-line-v stream-active" id="stream-final"></div>
        </div>

        <!-- 終點推播節點：在河流圖最末端直接展開「卡片上面寫了甚麼」！ -->
        <div id="node-telegram-out" onclick="clickNode('telegram_out')" class="river-node node-active" style="width: 440px; text-align: left; padding: 14px 16px; margin-bottom: 80px; border-radius: 12px; background: #ffffff; position: relative;">
          <!-- ⚡ 左側缺地點旁道匯流入此處 -->
          <div id="badge-bypass-noloc-join" style="position: absolute; left: 14px; top: -11px; background: #eff6ff; border: 1.5px solid #2563eb; color: #1d4ed8; font-size: 9.5px; font-weight: 800; padding: 2px 9px; border-radius: 10px; box-shadow: 0 2px 6px rgba(37,99,235,0.12); display: none;">
            ⚡ 缺地點引導直達此處交付
          </div>
          <!-- ⚡ 追問旁道匯流入此處 -->
          <div id="badge-bypass-join" style="position: absolute; right: 14px; top: -11px; background: #eff6ff; border: 1.5px solid #2563eb; color: #1d4ed8; font-size: 9.5px; font-weight: 800; padding: 2px 9px; border-radius: 10px; box-shadow: 0 2px 6px rgba(37,99,235,0.12); display: none;">
            ⚡ 追問旁道直達此處交付
          </div>
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
            <div class="node-layer-tag">DELIVERY & SLA TERMINUS</div>
            <span id="badge-river-bottom-latency" style="font-size: 10px; background: #ecfdf5; color: #059669; font-weight: 800; padding: 2px 7px; border-radius: 4px; border: 1px solid #a7f3d0;">⏱️ E2E: 1,242ms</span>
          </div>
          <div class="node-title" style="font-size: 13px; font-weight: 800; color: #0f172a; margin-bottom: 8px; display: flex; align-items: center; justify-content: space-between;">
            <span>📱 成功推播 Telegram 旅人手機端</span>
            <span style="font-size: 10px; color: #2563eb; background: #eff6ff; padding: 2px 6px; border-radius: 4px; font-weight: 700;">真實卡片內容</span>
          </div>

          <!-- 卡片本體 (一秒看清卡片推薦了什麼) -->
          <div style="background: #f8fafc; border: 1.5px solid #bfdbfe; border-radius: 8px; padding: 12px 14px; position: relative;">
            <div id="river-card-text" style="font-size: 11.5px; line-height: 1.65; color: #0f172a; white-space: pre-wrap; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
            </div>
            <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 8px; border-top: 1px dashed #cbd5e1; padding-top: 6px;">
              <span style="font-size: 9.5px; color: #64748b;">寫入 Neon PostgreSQL chat_sessions 備查</span>
              <span style="font-size: 10px; color: #2563eb; font-weight: 700;">已送達旅人 ✓✓</span>
            </div>
          </div>
        </div>

      </div>
    </div>

    <!-- ========== 右欄 (440px)：永久並排、向下懸浮、支援獨立滾動的 6 維審計抽屜 ========== -->
    <div id="node-inspector-drawer" style="background: #ffffff; border: 1.5px solid #cbd5e1; border-radius: 12px; padding: 18px 20px; display: flex; flex-direction: column; box-shadow: 0 4px 16px rgba(0,0,0,0.06); position: sticky; top: 16px; max-height: calc(100vh - 92px); overflow-y: auto; padding-bottom: 50px; scroll-behavior: smooth;">

      <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #f1f5f9; padding-bottom: 12px; margin-bottom: 14px;">
        <div>
          <div style="font-size: 10px; color: #64748b; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px;">6-Dimensional Node Inspector</div>
          <div id="detail-node-title" style="font-size: 15px; font-weight: 800; color: #0f172a; margin-top: 2px;">🧠 State 1: 結構化意圖抽取</div>
        </div>
        <div id="detail-latency-tag" style="background: #ecfdf5; color: #059669; border: 1px solid #a7f3d0; font-size: 11px; padding: 3px 8px; border-radius: 6px; font-weight: 800;">
          ⏱️ 280 ms
        </div>
      </div>

      <!-- 節點描述 -->
      <div id="detail-description" style="font-size: 12px; color: #334155; line-height: 1.6; margin-bottom: 12px; background: #f8fafc; padding: 9px 12px; border-radius: 8px; border: 1px solid #e2e8f0;">
        調用 Groq qwen3.8-27b 解析結構化 JSON，解耦地點、料理核心詞與風格修飾詞。
      </div>

      <!-- 💬 旅人手機端真實推播視圖 (Telegram Message Result，讓答案清晰立即可見！) -->
      <div id="box-telegram-preview" style="margin-bottom: 14px; background: #f0f7ff; border: 1.5px solid #93c5fd; border-radius: 10px; padding: 12px 14px; box-shadow: 0 2px 6px rgba(37,99,235,0.06);">
        <div style="font-size: 11px; font-weight: 800; color: #1e40af; margin-bottom: 8px; display: flex; justify-content: space-between; align-items: center;">
          <span style="display: flex; align-items: center; gap: 5px;">
            <span style="font-size: 14px;">📱</span>
            <span>旅人手機端收到的最終解答 (Telegram Message)</span>
          </span>
          <span id="telegram-preview-tag" style="font-size: 10px; background: #2563eb; color: #ffffff; padding: 2px 7px; border-radius: 4px; font-weight: 700;">
            真實推播內容
          </span>
        </div>
        <!-- 擬真 Telegram 訊息氣泡 -->
        <div style="background: #ffffff; border: 1px solid #dbeafe; border-radius: 8px; padding: 10px 12px; position: relative; box-shadow: 0 1px 2px rgba(0,0,0,0.04);">
          <div id="detail-telegram-preview" style="font-size: 12px; line-height: 1.65; color: #0f172a; white-space: pre-wrap; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
          </div>
          <div style="display: flex; justify-content: flex-end; align-items: center; gap: 4px; margin-top: 6px;">
            <span id="detail-telegram-time" style="font-size: 10px; color: #94a3b8;">12:45 PM</span>
            <span style="font-size: 10px; color: #3b82f6;">✓✓</span>
          </div>
        </div>
      </div>

      <!-- Input -->
      <div style="margin-bottom: 10px;">
        <div style="font-size: 11px; font-weight: 700; color: #475569; margin-bottom: 4px; display: flex; justify-content: space-between;">
          <span>📥 節點輸入 (Input Payload)</span>
          <span style="font-size: 10px; color: #94a3b8;">JSON</span>
        </div>
        <pre id="detail-input" style="background: #f8fafc; border: 1px solid #e2e8f0; color: #0f172a; padding: 8px 10px; border-radius: 6px; font-size: 11px; overflow-x: auto; margin: 0; line-height: 1.45; max-height: 100px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;">{
  "user_query": "神保町車站 飯糰",
  "chat_id": 1202194852
}</pre>
      </div>

      <!-- Output -->
      <div style="margin-bottom: 12px;">
        <div style="font-size: 11px; font-weight: 700; color: #475569; margin-bottom: 4px; display: flex; justify-content: space-between;">
          <span>📤 節點輸出 (Output Payload)</span>
          <span style="font-size: 10px; color: #94a3b8;">JSON</span>
        </div>
        <pre id="detail-output" style="background: #f8fafc; border: 1px solid #e2e8f0; color: #059669; padding: 8px 10px; border-radius: 6px; font-size: 11px; overflow-x: auto; margin: 0; line-height: 1.45; max-height: 110px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;">{
  "intent": "search",
  "location": "神保町",
  "cuisine": "飯糰",
  "modifier": null
}</pre>
      </div>

      <!-- 關鍵決策與資料存取 -->
      <div style="background: #fffbeb; border: 1px solid #fef3c7; border-left: 4px solid #d97706; padding: 10px 12px; border-radius: 8px; font-size: 11px; line-height: 1.55; color: #78350f;">
        <div style="font-weight: 800; color: #b45309; margin-bottom: 3px;">⚡ 關鍵決策 ＆ 資料庫/快取動作：</div>
        <div id="detail-action-note">
          意圖為 search 且具有目標地標 ➔ <strong>決定「繼續往下」進入 Google Places 階梯搜尋</strong>；本次不需調用需要地點之截流引導。
        </div>
      </div>
      <!-- 🌟 底部滾動安全留白 (避免被瀏覽器或工作列遮擋) -->
      <div style="height: 48px; display: flex; align-items: center; justify-content: center; color: #94a3b8; font-size: 10px; margin-top: 12px; border-top: 1px dashed #e2e8f0;">
        <span>✓ 節點 6 維審計資料檢閱完畢</span>
      </div>

    </div>

  </div>

</div>

<script>
let currentCaseKey = 'case_search';

const auditDatabase = {
  case_search: {
    user_input: {
      title: "💬 Telegram 使用者問句輸入",
      latency: "0 ms",
      desc: "旅人於 Telegram Bot 發送自然語言找店需求。",
      input: { "message": "神保町車站 飯糰", "chat_id": 1202194852, "timestamp": "2026-10-24T14:15:22+09:00" },
      output: { "status": "RECEIVED", "session_id": 1202194852 },
      action: "收到旅人輸入，建立本輪 Trace Run 追蹤。"
    },
    state_1: {
      title: "🧠 State 1: Micro-LLM 結構化意圖抽取",
      latency: "280 ms",
      desc: "調用 Groq qwen3.8-27b 解析結構化意圖 JSON，嚴格解耦地點、料理核心詞與風格修飾詞。",
      input: { "message": "神保町車站 飯糰" },
      output: { "intent": "search", "location": "神保町", "cuisine": "飯糰", "modifier": null, "confidence": 0.98 },
      action: "💡 判定成功：intent == 'search' 且具備明確區域「神保町」 ➔ 准予放行，進入深度檢索流水線！"
    },
    prewarm_check: {
      title: "♨️ 區域預熱池查驗 (Prewarm Pool Cache)",
      latency: "12 ms",
      desc: "發起昂貴的外部 Places API 前，優先查驗 Neon DB prewarm_jobs 是否已存在該地標之預熱餐廳池。",
      input: { "area_name": "神保町", "cache_pool": "neon_prewarm_stores" },
      output: { "status": "CACHE_MISS", "prewarmed_count": 0, "action": "PROCEED_TO_PLACES_API" },
      action: "💡 決策原因：神保町為即時提問地標，本地預熱池未命中 ➔ 放行 Places 800m 階梯檢索。（若為已預熱之熱門區域如銀座，則 0ms 秒出並完全省去 Places API 費用！）"
    },
    coords: {
      title: "🧭 座標錨定 (Google Geocoding) ＆ 歷史黑名單排除",
      latency: "95 ms",
      desc: "將地標「神保町」解析為 WGS84 座標，並過濾旅人上一輪已看過的店家以避免重複推薦。",
      input: { "query": "神保町站", "user_history_exclude_ids": [] },
      output: { "lat": 35.6959, "lng": 139.7576, "excluded_count": 0 },
      action: "取得高精度座標 (35.6959, 139.7576)，排除已推薦店家 0 家。"
    },
    state_2: {
      title: "🗺️ Places (New) 階梯檢索 · 800m 徒步圈首輪命中",
      latency: "380 ms",
      desc: "以 800m 徒步半徑優先檢索，直接命中 5 家高品質候選，自動終止擴圈。",
      input: { "lat": 35.6959, "lng": 139.7576, "radius": 800, "keyword": "おにぎり" },
      output: { "raw_places_count": 5, "status": "HIT_SUFFICIENT", "expand_needed": false },
      action: "首輪 800m 命中 5 家 (>= 3 家標準) ➔ 省下 1800m 擴圈查詢，保護 GCP API 額度。"
    },
    state_2_rescue: {
      title: "🔄 1800m 擴圈檢索 (本次略過)",
      latency: "0 ms (略過)",
      desc: "當 800m 範圍內的候選店家不足 3 家時，系統才會自動啟動 1800m 擴圈備選機制。",
      input: { "status": "SKIPPED", "reason": "800m 已有 5 家候選店家，滿足最低推薦數量要求" },
      output: { "action": "NO_OP" },
      action: "💡 決策原因：首輪 800m 已成功找到 5 家高評價名店，系統主動跳過本節點，保護預算。"
    },
    prefilter: {
      title: "🎯 0ms 老饕潛力預篩 (Heuristic Prefilter)",
      latency: "2 ms",
      desc: "從 Places 回傳名單中，排除連鎖快餐，以寬鬆 Google 3.5+ 門檻保底防誤殺職人老店，選拔出最具老饕潛力之 Top 4 種子進入 Tabelog 口コミ漏斗。",
      input: { "raw_candidates": 5 },
      output: { "seed_tier1": 4, "rescue_tier2": 1, "filtered_out": 0 },
      action: "💡 決策原因：採取 Google 3.5+ 寬鬆保底（防止誤殺觀光客低分但 Tabelog 3.8+ 的職人老店），排除連鎖快餐後，將候選種子交由後續 Tabelog 食評定勝負！"
    },
    cache_check: {
      title: "🗄️ Neon DB 7天快取查驗",
      latency: "15 ms",
      desc: "在發起即時網路檢索前，優先在 Neon PostgreSQL tabelog_cache 查詢候選店家是否具有 7 天內有效快取。",
      input: { "candidates": ["おむすび処 結", "神保町おにぎり", "釜戶飯糰", "おにぎり小町"] },
      output: { "hit_count": 3, "miss_count": 1, "hit_rate": "75%" },
      action: "3 家老饕名店 0ms 快取秒出！僅剩下 1 家需透過外部 Yahoo Grounding 取得最新資料。"
    },
    grounding: {
      title: "⚡ Yahoo Grounding 實時檢索",
      latency: "480 ms",
      desc: "避開純 HTTP 直爬 100% 遭遇 403 阻擋的問題，透過 Yahoo Web Grounding 取得未命中快取店家之正確 Tabelog 網址與日文地址。",
      input: { "target": "おにぎり小町", "prefecture": "東京都" },
      output: { "grounded_url": "https://tabelog.com/tokyo/A1310/A131003/13250111/", "phone": "03-3291-xxxx" },
      action: "成功取得 Tabelog URL，補齊名單最後一塊拼圖。"
    },
    geo_veto: {
      title: "🛡️ 都道府縣一票否決 (Geo-Veto Guard)",
      latency: "1 ms",
      desc: "比對 Google Places 回傳都道府縣與 Tabelog 候選店家地址，若發生跨縣市漂移立即一票否決拋棄。",
      input: { "places_prefecture": "東京都", "tabelog_prefectures": ["東京都", "東京都", "東京都", "東京都"] },
      output: { "passed_count": 4, "vetoed_count": 0, "status": "ALL_MATCHED" },
      action: "4 家店家行政區完全一致，無地理漂移風險，全員放行。"
    },
    resolver: {
      title: "📞 本地市話 Level 1 吻合 (Fast-Path Pass)",
      latency: "0 ms",
      desc: "比對 Google 與 Tabelog 登記之市內電話號碼，完全一致時 0ms 判定為同店家，無需呼叫 Micro-LLM。",
      input: { "phone_pairs": [["03-3291-1111", "03-3291-1111"], ["03-5280-2222", "03-5280-2222"]] },
      output: { "fast_pass_count": 4, "llm_fallback_needed": 0 },
      action: "4 家全部透過電話號碼 100% 吻合直通！成功節省 1 次 Micro-LLM 判定呼叫與 300ms 耗時。"
    },
    db_cache_write: {
      title: "💾 批次落庫 Neon DB 快取庫 (tabelog_cache & restaurants)",
      latency: "45 ms",
      desc: "將本次新爬取的 Tabelog 口コミ評分、預算、支付方式寫入 tabelog_cache 表，並落庫 restaurants 基礎實體。",
      input: {
        "operation": "UPSERT BATCH",
        "target_tables": ["restaurants", "tabelog_cache"],
        "records_count": 3
      },
      output: {
        "status": "COMMITTED",
        "ttl_days": 7,
        "cache_invalidation": "NOW() + INTERVAL '7 days'"
      },
      action: "🗄️ Neon Postgres 寫入成功！未來 7 天內旅人查詢相同名單直接 0ms 快取命中，徹底省下爬蟲與外部 API 費用。"
    },
    review_coverage: {
      title: "🔥 Tabelog 口コミ 100% 覆蓋 (Review Precedence)",
      latency: "320 ms",
      desc: "平行呼叫 fetch_tabelog_reviews 定向爬取老饕真實日文口コミ，嚴格執行「以 Tabelog 口コミ取代 Google 評論」，標記 source=tabelog。",
      input: { "targets": 4, "coverage_policy": "STRICT_TABELOG_OVERRULE" },
      output: { "covered_shops": 4, "coverage_rate": "100%", "source_lineage": "tabelog" },
      action: "全數取得道地日文評論，成功抹除 Google 評論中的觀光客業配雜訊，血統標籤完整保留。"
    },
    vector_rag: {
      title: "📐 pgvector Cosine 餘弦相似度口感優選重排",
      latency: "110 ms",
      desc: "調用 Gemini Embedding 產生 768 維語意向量，在 Neon DB 執行 pgvector 餘弦距離檢索，篩選口感最道地之 Top 3。",
      input: { "query_vector": "香脆海苔 軟糯米飯 老店口感", "candidate_reviews_count": 12 },
      output: { "ranked_top3": ["おむすび処 結 (0.892)", "神保町おにぎり (0.865)", "釜戶飯糰 (0.841)"] },
      action: "完成老饕權重排序，精準萃取每家名店的日文口感亮點標籤。"
    },
    scoped_anchor: {
      title: "🔒 Scoped Anchor 評分/店名雙向防偽校驗",
      latency: "2 ms",
      desc: "防範 LLM 在卡片合成時幻覺造假。強制將卡片內的日文原名、Tabelog 評分與後端結構化資料進行雙向正則校對。",
      input: { "card_shop_names": ["おむすび処 結", "神保町おにぎり", "釜戶飯糰"], "card_ratings": [3.58, 3.52, 3.49] },
      output: { "tampered": false, "name_match": "100%", "rating_match": "100%" },
      action: "店名與評分零篡改！保證旅人拿到的是 100% 真實存在的日本道地老店。"
    },
    validator: {
      title: "🛡️ validator.py 執行期排版與禁令掃描",
      latency: "3 ms",
      desc: "掃描 Telegram 卡片 Markdown 排版，強制執行 No-Rating-As-Reason（嚴禁以單純分數高作為推薦理由套話），違規即觸發熔斷。",
      input: { "card_markdown_text": "已生成之推薦卡片" },
      output: { "banned_patterns_found": 0, "markdown_syntax_valid": true, "fallback_triggered": false },
      action: "無套話違規，排版符合 Telegram Markdown 規範，無需降級確定性安全模板，全線放行推播！"
    },
    db_session_write: {
      title: "💾 寫入 Session 對話記憶庫與鏈路追蹤 (chat_sessions & agent_runs)",
      latency: "25 ms",
      desc: "將本次推薦之 Top 3 店家 ID、店名、營業時間快照寫入 chat_sessions，並記錄 agent_runs 鏈路指標。",
      input: {
        "chat_id": 1202194852,
        "last_recommended_ids": ["rest_01", "rest_02", "rest_03"],
        "e2e_latency_ms": 1242
      },
      output: {
        "session_status": "UPDATED",
        "run_logged": true
      },
      action: "🗄️ 對話狀態持久化完成！為下一輪旅人追問（如「第二家今天有開嗎？」）提供 190ms 零外部消耗的記憶依據。"
    },
    telegram_out: {
      title: "📱 成功推播 Telegram 旅人端 (SLA: 1,242ms)",
      latency: "180 ms",
      desc: "透過 Telegram Bot API 推送排版精美的繁體中文推薦卡片，並將本輪推薦名單寫入 Neon PostgreSQL chat_sessions。",
      telegram_preview: `🍱 神保町 飯糰精選推薦（老饕評選 Top 3）

1️⃣ おむすび処 結（⭐️ 3.58 · Tabelog 口コミ首選）
📍 東京都千代田區神田神保町1-1
💡「米飯粒粒分明，海苔香氣撲鼻，冷掉依然美味的排隊老字號」
💰 預算：¥500 - ¥1,000 | 💳 支援電子支付

2️⃣ 神保町おにぎり（⭐️ 3.52）
📍 東京都千代田區神田神保町1-14
💡「現點現捏溫熱米飯，明太子與鮭魚用料極度紮實」
💰 預算：¥600 - ¥1,200 | 💳 支援交通系IC卡

3️⃣ 釜戶飯糰 本店（⭐️ 3.49）
📍 東京都千代田區神田神保町2-5
💡「古法柴火釜鍋炊飯，自製昆布漬物是靈魂」
💰 預算：¥800 - ¥1,500 | 💳 支援信用卡`,
      input: { "chat_id": 1202194852, "message_type": "food_recommendation_card" },
      output: { "telegram_message_id": 84920, "session_saved": true, "total_turnaround_time": "1,242 ms" },
      action: "旅人手機端即時收到卡片！3 家名單已存入 Session，旅人後續追問「第二家今天有開嗎？」可 190ms 秒答。"
    }
  },
  case_no_loc: {
    user_input: {
      title: "💬 Telegram 使用者問句輸入",
      latency: "0 ms",
      desc: "旅人發送問句：「我想吃拉麵」。",
      input: { "text": "我想吃拉麵", "user_id": 1202194852 },
      output: { "status": "received" },
      action: "收到缺乏明確行政區或車站的提問，進入意圖抽取。"
    },
    state_1: {
      title: "🧠 State 1: Micro-LLM 結構化意圖抽取",
      latency: "270 ms",
      desc: "解析結構化意圖 JSON，精準識別料理核心詞「拉麵」，但發現 location 欄位為空 (null)。",
      input: { "message": "我想吃拉麵" },
      output: { "intent": "need_location", "cuisine": "拉麵", "location": null, "is_valid_area": false },
      action: "⚡ 觸發截流保護：判定為 location == null ➔ 決定「截流防呆」，繞過後續外部 Places 搜尋與爬蟲！"
    },
    branch_no_loc: {
      title: "🛑 缺乏地標截流引導 (本次執行)",
      latency: "45 ms",
      desc: "主動防禦外部 Places API 濫用，推送親切引導句請旅人補充地點。",
      telegram_preview: `📍 請問您預計在哪個車站或區域用餐呢？

例如您可以直接輸入：
•「新宿 拉麵」
•「澀谷 燒肉」
•「淺草 壽喜燒」

我會立刻為您在該區域展開 800m 階梯搜尋，挑選 Tabelog 3.5+ 老饕口碑名店！`,
      input: { "cuisine": "拉麵", "missing": "location" },
      output: { "reply": "請問您預計在哪個車站或區域用餐呢？例如：『新宿 拉麵』或『澀谷 燒肉』" },
      action: "直接回傳 Telegram，並終止流水線。成功節省 Places API 呼叫次數 1 次與 Tabelog 爬取成本！"
    },
    prewarm_check: {
      title: "♨️ 區域預熱池查驗 (Prewarm Pool Cache)",
      latency: "12 ms",
      desc: "發起昂貴的外部 Places API 前，優先查驗 Neon DB prewarm_jobs 是否已存在該地標之預熱餐廳池。",
      input: { "area_name": "神保町", "cache_pool": "neon_prewarm_stores" },
      output: { "status": "CACHE_MISS", "prewarmed_count": 0, "action": "PROCEED_TO_PLACES_API" },
      action: "💡 決策原因：神保町為即時提問地標，本地預熱池未命中 ➔ 放行 Places 800m 階梯檢索。（若為已預熱之熱門區域如銀座，則 0ms 秒出並完全省去 Places API 費用！）"
    },
    coords: { title: "🧭 座標錨定 (本次略過)", latency: "0 ms (略過)", desc: "缺乏地點，無法定位。", input: { "status": "SKIPPED" }, output: { "action": "NO_OP" }, action: "因前置判定缺地點，避免了無效 Geocoding 查詢。" },
    state_2: { title: "🗺️ Places 800m 檢索 (本次略過)", latency: "0 ms (略過)", desc: "未進入搜尋狀態。", input: { "status": "SKIPPED" }, output: { "action": "NO_OP" }, action: "無座標可搜尋，省下 Places API 費用。" },
    state_2_rescue: { title: "🔄 1800m 擴圈 (本次略過)", latency: "0 ms (略過)", desc: "未進入搜尋狀態。", input: { "status": "SKIPPED" }, output: { "action": "NO_OP" }, action: "主流程已提早終止。" },
    prefilter: { title: "🎯 0ms 老饕潛力預篩 (本次略過)", latency: "0 ms (略過)", desc: "無候選名單。", input: { "status": "SKIPPED" }, output: { "action": "NO_OP" }, action: "無搜尋結果。" },
    cache_check: { title: "🗄️ 7天快取查驗 (本次略過)", latency: "0 ms (略過)", desc: "無名單查驗。", input: { "status": "SKIPPED" }, output: { "action": "NO_OP" }, action: "無店家候選。" },
    grounding: { title: "⚡ Grounding 檢索 (本次略過)", latency: "0 ms (略過)", desc: "無名單檢索。", input: { "status": "SKIPPED" }, output: { "action": "NO_OP" }, action: "無店家候選。" },
    geo_veto: { title: "🛡️ 都道府縣一票否決 (本次略過)", latency: "0 ms (略過)", desc: "無名單核對。", input: { "status": "SKIPPED" }, output: { "action": "NO_OP" }, action: "無店家候選。" },
    resolver: { title: "📞 市話 Level 1 吻合 (本次略過)", latency: "0 ms (略過)", desc: "無名單核對。", input: { "status": "SKIPPED" }, output: { "action": "NO_OP" }, action: "無店家候選。" },
    db_cache_write: { title: "💾 批次落庫 (本次略過)", latency: "0 ms (略過)", desc: "無名單需持久化。", input: { "status": "SKIPPED" }, output: { "action": "NO_OP" }, action: "無落庫操作。" },
    review_coverage: { title: "🔥 Tabelog 口コミ 覆蓋 (本次略過)", latency: "0 ms (略過)", desc: "無名單抓取。", input: { "status": "SKIPPED" }, output: { "action": "NO_OP" }, action: "無店家候選。" },
    vector_rag: { title: "📐 pgvector 語意重排 (本次略過)", latency: "0 ms (略過)", desc: "無名單重排。", input: { "status": "SKIPPED" }, output: { "action": "NO_OP" }, action: "無店家候選。" },
    scoped_anchor: { title: "🔒 Scoped Anchor 防偽 (本次略過)", latency: "0 ms (略過)", desc: "無名單校對。", input: { "status": "SKIPPED" }, output: { "action": "NO_OP" }, action: "無店家候選。" },
    validator: { title: "🛡️ validator.py 排版掃描 (本次略過)", latency: "0 ms (略過)", desc: "無卡片合成。", input: { "status": "SKIPPED" }, output: { "action": "NO_OP" }, action: "無店家候選。" },
    db_session_write: { title: "💾 記憶落庫 (本次略過)", latency: "0 ms (略過)", desc: "截流訊息不覆蓋推薦歷史。", input: { "status": "SKIPPED" }, output: { "action": "NO_OP" }, action: "保留上一輪記憶。" },
    telegram_out: {
      title: "📱 交付完成：推播地點引導句",
      latency: "15 ms",
      desc: "截流防呆機制直接觸發回覆，提醒旅人提供用餐車站或區域。",
      telegram_preview: `📍 請問您預計在哪個車站或區域用餐呢？

例如您可以直接輸入：
•「新宿 拉麵」
•「澀谷 燒肉」
•「淺草 壽喜燒」

我會立刻為您在該區域展開 800m 階梯搜尋，挑選 Tabelog 3.5+ 老饕口碑名店！`,
      input: { "reply": "請問您預計在哪個車站或區域用餐呢？", "chat_id": 1202194852 },
      output: { "status": "DELIVERED", "total_e2e_time": "285 ms" },
      action: "✅ 成功截流！總延遲僅 285ms，直接省下 1 次 Google Places 檢索費用與後續所有爬取成本。"
    },
    final_response: `📍 請問您預計在哪個車站或區域用餐呢？

例如您可以直接輸入：
•「新宿 拉麵」
•「澀谷 燒肉」
•「淺草 壽喜燒」

我會立刻為您在該區域展開 800m 階梯搜尋，挑選 Tabelog 3.5+ 老饕口碑名店！`
  },
  case_followup: {
    user_input: {
      title: "💬 Telegram 使用者問句輸入",
      latency: "0 ms",
      desc: "使用者在上一輪推薦後追問名單細節。",
      input: { "text": "第二家今天有開嗎？", "user_id": 1202194852, "chat_id": 1202194852 },
      output: { "status": "received" },
      action: "收到追問問句，即時通知：「正在為您翻閱店家營業資訊...」"
    },
    state_1: {
      title: "🧠 State 1: Micro-LLM 結構化意圖抽取",
      latency: "250 ms",
      desc: "精準識別出代名詞「第二家」與「營業時間」查詢意圖。",
      input: { "message": "第二家今天有開嗎？" },
      output: { "intent": "follow_up", "target_index": 2, "query_type": "opening_hours" },
      action: "⚡ 觸發追問轉向：判定為 follow_up ➔ 決定「繞過昂貴搜尋」，直走 Session 記憶解答分支！"
    },
    branch_followup: {
      title: "💬 追問記憶解答 (本次執行)",
      latency: "190 ms",
      desc: "自 Neon PostgreSQL chat_sessions 讀取上一輪神保町推薦之第 2 家店（神保町おにぎり），直接回答營業時間。",
      telegram_preview: `🍙 神保町おにぎり（上一輪推薦第 2 家）

今日（週六）營業狀態：
✅ 正常營業中！
⏰ 營業時間：11:00 - 20:00（最後點餐 19:30）
💡 今日特色：現做明太子飯糰與鮭魚飯糰供應充足，目前無公休回報。

（若需查詢其他推薦店家或重新搜尋，請隨時告訴我！）`,
      input: { "session_id": 1202194852, "target_shop": "神保町おにぎり", "current_time": "週六 12:30" },
      output: { "reply": "「神保町おにぎり」今日週六正常營業中（11:00 - 20:00），目前無公休回報！" },
      action: "零調用 Google Places，零重新爬蟲，延遲僅 190ms！同時將問答更新至 Session 歷史。"
    },
    prewarm_check: {
      title: "♨️ 區域預熱池查驗 (Prewarm Pool Cache)",
      latency: "12 ms",
      desc: "發起昂貴的外部 Places API 前，優先查驗 Neon DB prewarm_jobs 是否已存在該地標之預熱餐廳池。",
      input: { "area_name": "神保町", "cache_pool": "neon_prewarm_stores" },
      output: { "status": "CACHE_MISS", "prewarmed_count": 0, "action": "PROCEED_TO_PLACES_API" },
      action: "💡 決策原因：神保町為即時提問地標，本地預熱池未命中 ➔ 放行 Places 800m 階梯檢索。（若為已預熱之熱門區域如銀座，則 0ms 秒出並完全省去 Places API 費用！）"
    },
    coords: { title: "🧭 座標錨定 (本次略過)", latency: "0 ms (略過)", desc: "追問現成名單，無需重新定位。", input: { "status": "SKIPPED" }, output: { "action": "NO_OP" }, action: "走追問分支，省去外部 Geocoding。" },
    state_2: { title: "🗺️ Places 800m 檢索 (本次略過)", latency: "0 ms (略過)", desc: "無需重新搜尋。", input: { "status": "SKIPPED" }, output: { "action": "NO_OP" }, action: "省下 Places API 費用。" },
    state_2_rescue: { title: "🔄 1800m 擴圈 (本次略過)", latency: "0 ms (略過)", desc: "無需擴圈。", input: { "status": "SKIPPED" }, output: { "action": "NO_OP" }, action: "走追問分支。" },
    prefilter: { title: "🎯 0ms 老饕潛力預篩 (本次略過)", latency: "0 ms (略過)", desc: "已有歷史名單。", input: { "status": "SKIPPED" }, output: { "action": "NO_OP" }, action: "無需預篩。" },
    cache_check: { title: "🗄️ 7天快取查驗 (本次略過)", latency: "0 ms (略過)", desc: "直接使用 Session 資料。", input: { "status": "SKIPPED" }, output: { "action": "NO_OP" }, action: "歷史名單已在 Session。" },
    grounding: { title: "⚡ Grounding 檢索 (本次略過)", latency: "0 ms (略過)", desc: "無需網路搜尋。", input: { "status": "SKIPPED" }, output: { "action": "NO_OP" }, action: "無需網路搜尋。" },
    geo_veto: { title: "🛡️ 都道府縣一票否決 (本次略過)", latency: "0 ms (略過)", desc: "無店家候選需核對。", input: { "status": "SKIPPED" }, output: { "action": "NO_OP" }, action: "無需比對。" },
    resolver: { title: "📞 市話 Level 1 吻合 (本次略過)", latency: "0 ms (略過)", desc: "已完成對齊。", input: { "status": "SKIPPED" }, output: { "action": "NO_OP" }, action: "無需比對。" },
    db_cache_write: { title: "💾 批次落庫 (本次略過)", latency: "0 ms (略過)", desc: "無新爬取資料。", input: { "status": "SKIPPED" }, output: { "action": "NO_OP" }, action: "讀取既有記憶。" },
    review_coverage: { title: "🔥 Tabelog 口コミ 覆蓋 (本次略過)", latency: "0 ms (略過)", desc: "無需重新抓取評論。", input: { "status": "SKIPPED" }, output: { "action": "NO_OP" }, action: "無需重新抓取。" },
    vector_rag: { title: "📐 pgvector 語意重排 (本次略過)", latency: "0 ms (略過)", desc: "直接解答特定店家。", input: { "status": "SKIPPED" }, output: { "action": "NO_OP" }, action: "針對目標店解答。" },
    scoped_anchor: { title: "🔒 Scoped Anchor 防偽 (本次略過)", latency: "0 ms (略過)", desc: "非名單卡片輸出。", input: { "status": "SKIPPED" }, output: { "action": "NO_OP" }, action: "單店追問直接回覆。" },
    validator: { title: "🛡️ validator.py 排版掃描 (本次略過)", latency: "0 ms (略過)", desc: "追問文字格式簡易。", input: { "status": "SKIPPED" }, output: { "action": "NO_OP" }, action: "文字回答直接送出。" },
    db_session_write: {
      title: "💾 更新 Session 追問歷史與鏈路追蹤",
      latency: "15 ms",
      desc: "將本次追問「神保町おにぎり」營業時間解答追加至 chat_sessions 對話紀錄，並寫入 agent_runs。",
      input: { "session_id": 1202194852, "query": "第二家今天有開嗎？", "resolved": true },
      output: { "session_updated": true, "latency_logged": "190 ms" },
      action: "🗄️ 追問歷史更新完成！完整保留對話脈絡。"
    },
    telegram_out: {
      title: "📱 交付完成：推播單店營業解答",
      latency: "20 ms",
      desc: "繞過 Layer 2~4 的外部 Places 搜尋與 Tabelog 爬蟲，將 Session 查詢結果直接封裝推播 Telegram。",
      telegram_preview: `🍙 神保町おにぎり（上一輪推薦第 2 家）

今日（週六）營業狀態：
✅ 正常營業中！
⏰ 營業時間：11:00 - 20:00（最後點餐 19:30）
💡 今日特色：現做明太子飯糰與鮭魚飯糰供應充足，目前無公休回報。

（若需查詢其他推薦店家或重新搜尋，請隨時告訴我！）`,
      input: { "reply": "「神保町おにぎり」今日週六正常營業中（11:00 - 20:00）", "chat_id": 1202194852 },
      output: { "status": "DELIVERED", "total_e2e_time": "190 ms" },
      action: "✅ 旅人立即收到解答！端到端延遲僅 190ms，零外部 API 費用消耗，本輪交互圓滿結束。"
    },
    final_response: `🍙 神保町おにぎり（上一輪推薦第 2 家）

今日（週六）營業狀態：
✅ 正常營業中！
⏰ 營業時間：11:00 - 20:00（最後點餐 19:30）
💡 今日特色：現做明太子飯糰與鮭魚飯糰供應充足，目前無公休回報。

（若需查詢其他推薦店家或重新搜尋，請隨時告訴我！）`
  }
};

function switchCase(caseKey) {
  currentCaseKey = caseKey;

  // 更新左側清單 Active 狀態
  document.querySelectorAll('.history-item').forEach(item => item.classList.remove('active'));
  const activeItem = document.getElementById('hist-' + caseKey);
  if (activeItem) activeItem.classList.add('active');

  const updateNode = (id, isActive) => {
    const el = document.getElementById(id);
    if (!el) return;
    if (isActive) {
      el.classList.add('node-active');
      el.classList.remove('node-inactive');
    } else {
      el.classList.add('node-inactive');
      el.classList.remove('node-active');
    }
  };

  const updateStream = (id, isActive) => {
    const el = document.getElementById(id);
    if (!el) return;
    if (isActive) {
      el.classList.add('stream-active');
      el.classList.remove('stream-inactive');
    } else {
      el.classList.add('stream-inactive');
      el.classList.remove('stream-active');
    }
  };

  const allMainNodes = [
    'node-coords', 'node-prewarm-check', 'node-state-2', 'node-prefilter', 'node-cache-check',
    'node-grounding', 'node-geo-veto', 'node-resolver', 'node-db-cache-write',
    'node-review-coverage', 'node-vector-rag', 'node-scoped-anchor',
    'node-validator', 'node-db-session-write', 'node-telegram-out'
  ];
  const allMainStreams = [
    'stream-layer-2', 'stream-prewarm-to-places', 'stream-prefilter', 'stream-layer-3', 'stream-cache-to-grounding', 'stream-grounding-to-geo', 'stream-geo-to-resolver',
    'stream-db-cache', 'stream-layer-4', 'stream-vector', 'stream-layer-5',
    'stream-db-session', 'stream-final'
  ];

  const setDecisionBadges = (activeBranch) => {
    const bNoloc = document.getElementById('badge-route-reason-noloc');
    const bMain = document.getElementById('badge-route-reason-main');
    const bFollowup = document.getElementById('badge-route-reason-followup');
    if (!bNoloc || !bMain || !bFollowup) return;

    if (activeBranch === 'main') {
      bNoloc.innerHTML = '⚪ 條件不符: 地點明確非空';
      bNoloc.style.border = '1px dashed #cbd5e1';
      bNoloc.style.color = '#64748b';
      bNoloc.style.background = '#f8fafc';

      bMain.innerHTML = '✅ 命中決策: 搜尋意圖且具備明確區域「神保町」 ➔ 准予深度檢索';
      bMain.style.border = '1.5px solid #2563eb';
      bMain.style.color = '#1d4ed8';
      bMain.style.background = '#eff6ff';

      bFollowup.innerHTML = '⚪ 條件不符: 非追問問句';
      bFollowup.style.border = '1px dashed #cbd5e1';
      bFollowup.style.color = '#64748b';
      bFollowup.style.background = '#f8fafc';
    } else if (activeBranch === 'noloc') {
      bNoloc.innerHTML = '✅ 命中決策: location == null ➔ 截流防呆，省 Places 費用';
      bNoloc.style.border = '1.5px solid #2563eb';
      bNoloc.style.color = '#1d4ed8';
      bNoloc.style.background = '#eff6ff';

      bMain.innerHTML = '⚪ 條件不符: 缺乏車站或區域約束';
      bMain.style.border = '1px dashed #cbd5e1';
      bMain.style.color = '#64748b';
      bMain.style.background = '#f8fafc';

      bFollowup.innerHTML = '⚪ 條件不符: 非追問問句';
      bFollowup.style.border = '1px dashed #cbd5e1';
      bFollowup.style.color = '#64748b';
      bFollowup.style.background = '#f8fafc';
    } else if (activeBranch === 'followup') {
      bNoloc.innerHTML = '⚪ 條件不符: 非全新無地點提問';
      bNoloc.style.border = '1px dashed #cbd5e1';
      bNoloc.style.color = '#64748b';
      bNoloc.style.background = '#f8fafc';

      bMain.innerHTML = '⚪ 條件不符: 追問現有名單，無需重新檢索';
      bMain.style.border = '1px dashed #cbd5e1';
      bMain.style.color = '#64748b';
      bMain.style.background = '#f8fafc';

      bFollowup.innerHTML = '✅ 命中決策: 追問上一輪推薦名單 ➔ 讀取 Session 記憶直答';
      bFollowup.style.border = '1.5px solid #2563eb';
      bFollowup.style.color = '#1d4ed8';
      bFollowup.style.background = '#eff6ff';
    }
  };

  if (caseKey === 'case_search') {
    document.getElementById('badge-user-input').innerText = '「神保町車站 飯糰」';
    document.getElementById('badge-state-1').innerText = 'intent: search | location: 神保町 | cuisine: 飯糰';

    updateNode('node-user-input', true);
    updateStream('stream-1', true);
    updateNode('node-state-1', true);

    // 主幹線走過
    allMainNodes.forEach(id => updateNode(id, true));
    allMainStreams.forEach(id => updateStream(id, true));

    // 旁支未走過
    updateNode('node-state-2-rescue', false);
    updateNode('node-branch-no-loc', false);
    updateNode('node-branch-followup', false);

    // 💡 標註為什麼選擇這條路徑的判定原因
    setDecisionBadges('main');

    // SVG 線路
    document.getElementById('svg-stream-main').setAttribute('stroke', '#2563eb');
    document.getElementById('svg-stream-main').setAttribute('stroke-dasharray', '6 4');
    document.getElementById('svg-stream-main').classList.add('svg-flow-active');
    document.getElementById('svg-stream-no-loc').setAttribute('stroke', '#94a3b8');
    document.getElementById('svg-stream-no-loc').classList.remove('svg-flow-active');
    document.getElementById('svg-stream-followup').setAttribute('stroke', '#94a3b8');
    document.getElementById('svg-stream-followup').classList.remove('svg-flow-active');

    // 河流圖底部大卡片內容
    document.getElementById('river-card-text').innerText = auditDatabase.case_search.telegram_out.telegram_preview;
    document.getElementById('badge-river-bottom-latency').innerText = '⏱️ E2E: 1,242ms';

    // 關閉左右兩側旁道流光（走中間主幹線）
    setBypassState('none');

    clickNode('telegram_out');
  } else if (caseKey === 'case_no_loc') {
    document.getElementById('badge-user-input').innerText = '「我想吃拉麵」';
    document.getElementById('badge-state-1').innerText = 'intent: need_location | location: null | cuisine: 拉麵';

    updateNode('node-user-input', true);
    updateStream('stream-1', true);
    updateNode('node-state-1', true);

    // 走左側截流分支
    updateNode('node-branch-no-loc', true);

    // 主幹線與右側追問全數未走過 (Layer 2~5 全數略過)
    allMainNodes.forEach(id => updateNode(id, false));
    allMainStreams.forEach(id => updateStream(id, false));
    updateNode('node-state-2-rescue', false);
    updateNode('node-branch-followup', false);

    // 💡 標註為什麼選擇這條路徑的判定原因
    setDecisionBadges('noloc');

    // 最底部的推播大卡片亮起（作為截流的最終交付終點）
    updateNode('node-telegram-out', true);

    // 上層 SVG 線路
    document.getElementById('svg-stream-main').setAttribute('stroke', '#94a3b8');
    document.getElementById('svg-stream-main').setAttribute('stroke-dasharray', '4 4');
    document.getElementById('svg-stream-main').classList.remove('svg-flow-active');
    document.getElementById('svg-stream-no-loc').setAttribute('stroke', '#2563eb');
    document.getElementById('svg-stream-no-loc').setAttribute('stroke-dasharray', '6 4');
    document.getElementById('svg-stream-no-loc').classList.add('svg-flow-active');
    document.getElementById('svg-stream-followup').setAttribute('stroke', '#94a3b8');
    document.getElementById('svg-stream-followup').classList.remove('svg-flow-active');

    // 🌟 啟動左側缺地點截流直通最底端答案卡片的 Bypass 湛藍流光！
    setBypassState('noloc');

    // 河流圖底部大卡片內容 (截流直接回傳問句)
    document.getElementById('river-card-text').innerText = auditDatabase.case_no_loc.final_response;
    document.getElementById('badge-river-bottom-latency').innerText = '⏱️ E2E: 285ms (截流直出)';

    clickNode('branch_no_loc');
  } else if (caseKey === 'case_followup') {
    document.getElementById('badge-user-input').innerText = '「第二家今天有開嗎？」';
    document.getElementById('badge-state-1').innerText = 'intent: follow_up | target_index: 2 | query: opening_hours';

    updateNode('node-user-input', true);
    updateStream('stream-1', true);
    updateNode('node-state-1', true);

    // 走右側追問分支
    updateNode('node-branch-followup', true);

    // 主幹線與左側截流全數未走過 (Layer 2~5 全數略過)
    allMainNodes.forEach(id => updateNode(id, false));
    allMainStreams.forEach(id => updateStream(id, false));
    updateNode('node-state-2-rescue', false);
    updateNode('node-branch-no-loc', false);

    // 💡 標註為什麼選擇這條路徑的判定原因
    setDecisionBadges('followup');

    // 最底部的推播大卡片亮起（作為追問的最終交付終點）
    updateNode('node-telegram-out', true);

    // 上層 SVG 線路
    document.getElementById('svg-stream-main').setAttribute('stroke', '#94a3b8');
    document.getElementById('svg-stream-main').setAttribute('stroke-dasharray', '4 4');
    document.getElementById('svg-stream-main').classList.remove('svg-flow-active');
    document.getElementById('svg-stream-no-loc').setAttribute('stroke', '#94a3b8');
    document.getElementById('svg-stream-no-loc').classList.remove('svg-flow-active');
    document.getElementById('svg-stream-followup').setAttribute('stroke', '#2563eb');
    document.getElementById('svg-stream-followup').setAttribute('stroke-dasharray', '6 4');
    document.getElementById('svg-stream-followup').classList.add('svg-flow-active');

    // 🌟 啟動右側追問一路直通最底端答案卡片的 Bypass 湛藍流光！
    setBypassState('followup');

    // 河流圖底部大卡片內容 (Session 記憶直出)
    document.getElementById('river-card-text').innerText = auditDatabase.case_followup.final_response;
    document.getElementById('badge-river-bottom-latency').innerText = '⏱️ E2E: 190ms (Session 旁道直達)';

    clickNode('branch_followup');
  }
  setTimeout(updateBypassLine, 30);
}

// 🌟 全域旁道狀態管理 (支援左側截流、右側追問、中間主幹線)
function setBypassState(activeType) {
  const pathNoloc = document.getElementById('svg-stream-noloc-bypass');
  const badgeNolocFloating = document.getElementById('badge-bypass-noloc-floating');
  const badgeNolocJoin = document.getElementById('badge-bypass-noloc-join');
  const tagNolocStart = document.getElementById('tag-bypass-noloc-start');

  const pathFollowup = document.getElementById('svg-stream-followup-bypass');
  const badgeFollowupFloating = document.getElementById('badge-bypass-floating');
  const badgeFollowupJoin = document.getElementById('badge-bypass-join');
  const tagFollowupStart = document.getElementById('tag-bypass-start');

  if (pathNoloc) {
    if (activeType === 'noloc') {
      pathNoloc.setAttribute('stroke', '#2563eb');
      pathNoloc.setAttribute('stroke-width', '2.5');
      pathNoloc.setAttribute('stroke-dasharray', '6 4');
      pathNoloc.setAttribute('marker-end', 'url(#arrow-blue)');
      pathNoloc.classList.add('svg-flow-active');
      if (badgeNolocFloating) badgeNolocFloating.style.display = 'block';
      if (badgeNolocJoin) badgeNolocJoin.style.display = 'block';
      if (tagNolocStart) {
        tagNolocStart.style.borderColor = '#2563eb';
        tagNolocStart.style.color = '#1d4ed8';
        tagNolocStart.style.background = '#eff6ff';
      }
    } else {
      pathNoloc.setAttribute('stroke', '#cbd5e1');
      pathNoloc.setAttribute('stroke-width', '1.5');
      pathNoloc.setAttribute('stroke-dasharray', '4 4');
      pathNoloc.setAttribute('marker-end', 'url(#arrow-gray)');
      pathNoloc.classList.remove('svg-flow-active');
      if (badgeNolocFloating) badgeNolocFloating.style.display = 'none';
      if (badgeNolocJoin) badgeNolocJoin.style.display = 'none';
      if (tagNolocStart) {
        tagNolocStart.style.borderColor = '#cbd5e1';
        tagNolocStart.style.color = '#64748b';
        tagNolocStart.style.background = '#f8fafc';
      }
    }
  }

  if (pathFollowup) {
    if (activeType === 'followup') {
      pathFollowup.setAttribute('stroke', '#2563eb');
      pathFollowup.setAttribute('stroke-width', '2.5');
      pathFollowup.setAttribute('stroke-dasharray', '6 4');
      pathFollowup.setAttribute('marker-end', 'url(#arrow-blue)');
      pathFollowup.classList.add('svg-flow-active');
      if (badgeFollowupFloating) badgeFollowupFloating.style.display = 'block';
      if (badgeFollowupJoin) badgeFollowupJoin.style.display = 'block';
      if (tagFollowupStart) {
        tagFollowupStart.style.borderColor = '#2563eb';
        tagFollowupStart.style.color = '#1d4ed8';
        tagFollowupStart.style.background = '#eff6ff';
      }
    } else {
      pathFollowup.setAttribute('stroke', '#cbd5e1');
      pathFollowup.setAttribute('stroke-width', '1.5');
      pathFollowup.setAttribute('stroke-dasharray', '4 4');
      pathFollowup.setAttribute('marker-end', 'url(#arrow-gray)');
      pathFollowup.classList.remove('svg-flow-active');
      if (badgeFollowupFloating) badgeFollowupFloating.style.display = 'none';
      if (badgeFollowupJoin) badgeFollowupJoin.style.display = 'none';
      if (tagFollowupStart) {
        tagFollowupStart.style.borderColor = '#cbd5e1';
        tagFollowupStart.style.color = '#64748b';
        tagFollowupStart.style.background = '#f8fafc';
      }
    }
  }
}

// 🌟 動態精確計算左側截流與右側追問直通最底端答案卡片的貝茲曲線 Bypass 線路
function updateBypassLine() {
  const container = document.getElementById('river-flow-container');
  const target = document.getElementById('node-telegram-out');
  if (!container || !target) return;

  const cRect = container.getBoundingClientRect();
  const tRect = target.getBoundingClientRect();

  // 1. 計算右側追問旁道線
  const sourceFollowup = document.getElementById('node-branch-followup');
  const pathFollowup = document.getElementById('svg-stream-followup-bypass');
  const badgeFollowup = document.getElementById('badge-bypass-floating');
  if (sourceFollowup && pathFollowup) {
    const sRect = sourceFollowup.getBoundingClientRect();
    const startX = sRect.left + sRect.width / 2 - cRect.left;
    const startY = sRect.bottom - cRect.top + 3;
    const endX = tRect.right - cRect.left - 24;
    const endY = tRect.top - cRect.top - 2;
    const bypassX = Math.max(startX + 32, endX + 16);
    const cornerY = endY - 24;

    const d = `M ${startX} ${startY} ` +
              `L ${bypassX - 10} ${startY + 22} ` +
              `Q ${bypassX} ${startY + 32} ${bypassX} ${startY + 46} ` +
              `L ${bypassX} ${cornerY} ` +
              `Q ${bypassX} ${endY - 6} ${bypassX - 14} ${endY - 6} ` +
              `L ${endX + 14} ${endY - 6} ` +
              `Q ${endX} ${endY - 6} ${endX} ${endY}`;
    pathFollowup.setAttribute('d', d);

    if (badgeFollowup) {
      const midY = (startY + cornerY) / 2;
      badgeFollowup.style.left = `${bypassX + 10}px`;
      badgeFollowup.style.top = `${midY}px`;
    }
  }

  // 2. 計算左側缺地點旁道線
  const sourceNoloc = document.getElementById('node-branch-no-loc');
  const pathNoloc = document.getElementById('svg-stream-noloc-bypass');
  const badgeNoloc = document.getElementById('badge-bypass-noloc-floating');
  if (sourceNoloc && pathNoloc) {
    const sRect = sourceNoloc.getBoundingClientRect();
    const startX = sRect.left + sRect.width / 2 - cRect.left;
    const startY = sRect.bottom - cRect.top + 3;
    const endX = tRect.left - cRect.left + 24;
    const endY = tRect.top - cRect.top - 2;
    const bypassX = Math.min(startX - 32, endX - 16);
    const cornerY = endY - 24;

    const d = `M ${startX} ${startY} ` +
              `L ${bypassX + 10} ${startY + 22} ` +
              `Q ${bypassX} ${startY + 32} ${bypassX} ${startY + 46} ` +
              `L ${bypassX} ${cornerY} ` +
              `Q ${bypassX} ${endY - 6} ${bypassX + 14} ${endY - 6} ` +
              `L ${endX - 14} ${endY - 6} ` +
              `Q ${endX} ${endY - 6} ${endX} ${endY}`;
    pathNoloc.setAttribute('d', d);

    if (badgeNoloc) {
      const midY = (startY + cornerY) / 2;
      badgeNoloc.style.left = `${bypassX - 220}px`;
      badgeNoloc.style.top = `${midY}px`;
    }
  }
}

// 確保 case_search 的 final_response 一致
auditDatabase.case_search.final_response = auditDatabase.case_search.telegram_out.telegram_preview;

function clickNode(nodeKey) {
  const caseData = auditDatabase[currentCaseKey];
  if (!caseData || !caseData[nodeKey]) return;
  const node = caseData[nodeKey];

  document.getElementById('detail-node-title').innerText = node.title;
  document.getElementById('detail-latency-tag').innerText = `⏱️ ${node.latency}`;
  document.getElementById('detail-description').innerText = node.desc;
  document.getElementById('detail-input').innerText = JSON.stringify(node.input, null, 2);
  document.getElementById('detail-output').innerText = JSON.stringify(node.output, null, 2);
  document.getElementById('detail-action-note').innerHTML = node.action;

  // 💬 旅人端推播卡片預覽：僅在點擊最終推播交付節點 (telegram_out) 時才顯示，其他中段節點隱藏以保持聚焦
  const previewBox = document.getElementById('box-telegram-preview');
  const previewText = document.getElementById('detail-telegram-preview');
  const previewTag = document.getElementById('telegram-preview-tag');

  if (nodeKey === 'telegram_out' && node.telegram_preview) {
    previewBox.style.display = 'block';
    previewText.innerText = node.telegram_preview;
    previewBox.style.border = '2px solid #2563eb';
    previewBox.style.background = '#eff6ff';
    previewTag.innerText = '⚡ 本節點生成的最終解答推播 (Telegram)';
    previewTag.style.background = '#2563eb';
  } else {
    previewBox.style.display = 'none';
  }
}

// 預設載入 Case 1 正常搜尋推薦，並計算旁道連線
window.onload = () => {
  switchCase('case_search');
  setTimeout(updateBypassLine, 60);
};
window.onresize = () => {
  updateBypassLine();
};
</script>
"""


def generate_html_dashboard(
    summary: EvalSummary,
    output_path: Path,
    live_runs: Optional[list[dict[str, Any]]] = None,
    live_steps_by_run: Optional[dict[str, list[dict[str, Any]]]] = None,
) -> Path:
    """生成遵循 docs/OBSERVABILITY_DESIGN_SYSTEM.md 規範之 3-Pane 滿版全景河流圖現代儀表板"""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. 確保標題包含 PickForMe 4-Tier LLMOps 評測與全景河流圖中控台
    rendered_html = BASE_DASHBOARD_TEMPLATE.replace(
        "PickForMe 全鏈路全景河流圖中控台",
        "PickForMe 4-Tier LLMOps 評測與全景河流圖中控台",
    )

    # 2. 將評測案例資料與 Trace 數據序列化注入進 HTML 的 JSON 容器中
    eval_payload = {
        "summary": {
            "total_cases": summary.total_cases,
            "tier1_accuracy": summary.tier1_intent_accuracy,
            "tier2_f1": summary.tier2_entity_f1,
            "tier3_compliance": summary.tier3_guardrail_compliance,
            "tier4_success": summary.tier4_e2e_success_rate,
            "latency_avg_ms": summary.latency_avg_ms,
        },
        "case_results": [
            {
                "case_id": c.case_id,
                "category": c.category,
                "user_message": c.user_message,
                "tier1_pass": c.tier1_pass,
                "tier2_pass": c.tier2_pass,
                "tier3_pass": c.tier3_pass,
                "tier4_pass": c.tier4_pass,
                "latency_ms": c.latency_ms,
                "details": c.details,
            }
            for c in summary.case_results
        ],
        "live_runs": live_runs or [],
        "live_steps": live_steps_by_run or {},
    }

    json_str = json.dumps(eval_payload, ensure_ascii=False, indent=2)
    data_script = f'\n<script id="pickforme-eval-summary-payload" type="application/json">\n{json_str}\n</script>\n'

    if "</body>" in rendered_html:
        rendered_html = rendered_html.replace("</body>", f"{data_script}</body>")
    else:
        rendered_html += data_script

    output_path.write_text(rendered_html, encoding="utf-8")
    return output_path
