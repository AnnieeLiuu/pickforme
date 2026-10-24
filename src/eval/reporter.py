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

# 模板路徑：抽離至獨立 templates/ 目錄，保證純前端代碼之語法高亮與維護性
TEMPLATE_PATH = Path(__file__).parent / "templates" / "river_dashboard.html"


def _load_template() -> str:
    """載入全景河流圖 HTML 核心模板"""
    if not TEMPLATE_PATH.exists():
        raise FileNotFoundError(f"Observability dashboard template not found at {TEMPLATE_PATH}")
    return TEMPLATE_PATH.read_text(encoding="utf-8")


# 🌟 全景河流圖 HTML 核心模板常數 (向後相容導出)
BASE_DASHBOARD_TEMPLATE = _load_template()


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
