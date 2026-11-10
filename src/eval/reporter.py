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

# 🌟 Agent Harness 步驟工具顯示標籤映射表
STEP_TOOL_LABELS: dict[str, str] = {
    "quick_pass_gate": "⚡ 0ms QuickPassGate 快篩",
    "intent_parser": "🧠 Pydantic IntentOutput (Self-Correction)",
    "extract_intent": "🧠 Pydantic IntentOutput (Self-Correction)",
    "places_search_ladder": "🗺️ Places 智慧階梯檢索",
    "parallel_tabelog_enrichment": "📊 Tabelog 平行查驗 + Constraint Sensor",
    "tabelog_parallel_enrich": "📊 Tabelog 平行查驗 + Constraint Sensor",
    "card_synthesis": "🛡️ Scoped Anchor 卡片合成",
    "generate_recommendation_card": "🛡️ Scoped Anchor 卡片合成",
    "llm_follow_up": "💬 Two-Tier Memory 追問直答",
    "llm_chitchat": "💬 親切禮貌引導回覆",
}

HARNESS_ARCHITECTURE_BADGES: list[dict[str, str]] = [
    {
        "id": "quick_pass_gate",
        "label": "⚡ 0ms QuickPassGate 快篩",
        "desc": "0ms 確定性攔截問候/道謝/店號追問/純換一批/座標直搜與 Prompt Injection 安全絆線",
    },
    {
        "id": "pydantic_self_correction",
        "label": "🛡️ Pydantic Self-Correction",
        "desc": "IntentOutput & Tool Args 結構化合約驗證 + ValidationError 自動重試自癒迴圈",
    },
    {
        "id": "two_tier_memory",
        "label": "🧠 Two-Tier Memory",
        "desc": "chat_sessions JSONB 雙層記憶：working_memory 短期繼承 + long_term_preferences 長期飲食/支付偏好",
    },
    {
        "id": "constraint_sensor",
        "label": "🎯 Deterministic Constraint Sensor",
        "desc": "Stage 3 硬性飲食禁忌與支付偏好感測器，違規自動標記 constraint_violation 並扣減 0.45 分重排",
    },
]


def format_step_tool_name(tool_name: str) -> str:
    """將步驟 tool_name 格式化為可觀測性看板的友善顯示標籤"""
    return STEP_TOOL_LABELS.get(tool_name, tool_name)


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
        "## 🛡️ Agent Harness 架構防護矩陣 (Harness Capabilities)",
        "",
        *[f"- **{b['label']}**：{b['desc']}" for b in HARNESS_ARCHITECTURE_BADGES],
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

    # 1. 確保標題與 Agent Harness 強化標籤注入至全景河流圖中控台
    rendered_html = BASE_DASHBOARD_TEMPLATE.replace(
        "PickForMe 全鏈路全景河流圖中控台",
        "PickForMe 4-Tier LLMOps 評測與全景河流圖中控台",
    )
    rendered_html = rendered_html.replace(
        "<strong>Groq OTPM 1000 專屬額度監控</strong>",
        "<strong>Groq OTPM 1000 專屬額度監控</strong> · <strong>⚡ 0ms QuickPassGate 快篩</strong> · <strong>🛡️ Pydantic Self-Correction</strong> · <strong>🧠 Two-Tier Memory</strong> · <strong>🎯 Deterministic Constraint Sensor</strong>",
    )
    rendered_html = rendered_html.replace(
        "🧠 State 1: Micro-LLM 結構化意圖抽取",
        "🧠 State 1: QuickPassGate 0ms 快篩 + Micro-LLM 結構化意圖抽取 (Pydantic Self-Correction)",
    )
    rendered_html = rendered_html.replace(
        "💾 寫入 chat_sessions 記憶庫 ＆ agent_runs 追蹤",
        "💾 寫入 chat_sessions Two-Tier Memory 記憶庫 ＆ agent_runs 追蹤",
    )

    formatted_steps_by_run: dict[str, list[dict[str, Any]]] = {}
    if live_steps_by_run:
        for run_id, steps in live_steps_by_run.items():
            formatted_steps_by_run[run_id] = [
                {
                    **step,
                    "tool_label": format_step_tool_name(str(step.get("tool_name", ""))),
                }
                for step in steps
            ]

    # 2. 將評測案例資料、Harness 架構標籤與 Trace 數據序列化注入進 HTML 的 JSON 容器中
    eval_payload = {
        "summary": {
            "total_cases": summary.total_cases,
            "tier1_accuracy": summary.tier1_intent_accuracy,
            "tier2_f1": summary.tier2_entity_f1,
            "tier3_compliance": summary.tier3_guardrail_compliance,
            "tier4_success": summary.tier4_e2e_success_rate,
            "latency_avg_ms": summary.latency_avg_ms,
        },
        "harness_badges": HARNESS_ARCHITECTURE_BADGES,
        "step_tool_labels": STEP_TOOL_LABELS,
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
        "live_steps": formatted_steps_by_run,
    }

    json_str = json.dumps(eval_payload, ensure_ascii=False, indent=2)
    data_script = f'\n<script id="pickforme-eval-summary-payload" type="application/json">\n{json_str}\n</script>\n'

    if "</body>" in rendered_html:
        rendered_html = rendered_html.replace("</body>", f"{data_script}</body>")
    else:
        rendered_html += data_script

    output_path.write_text(rendered_html, encoding="utf-8")
    return output_path
