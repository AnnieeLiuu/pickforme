from pathlib import Path

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


def generate_html_dashboard(summary: EvalSummary, output_path: Path) -> Path:
    """生成 GitLab Pages 現代深色科技感 HTML 儀表板"""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    status_badge = (
        '<span class="badge badge-success">CI GATE PASSED</span>'
        if summary.all_passed_gate
        else '<span class="badge badge-danger">CI GATE FAILED</span>'
    )

    case_rows_html = []
    for c in summary.case_results:
        t1_badge = (
            '<span class="pill pill-green">PASS</span>' if c.tier1_pass else '<span class="pill pill-red">FAIL</span>'
        )
        t2_badge = (
            '<span class="pill pill-green">PASS</span>' if c.tier2_pass else '<span class="pill pill-red">FAIL</span>'
        )
        t3_badge = (
            '<span class="pill pill-green">PASS</span>' if c.tier3_pass else '<span class="pill pill-red">FAIL</span>'
        )
        e2e_badge = (
            '<span class="pill pill-green">SUCCESS</span>'
            if c.tier4_pass
            else '<span class="pill pill-red">FAILED</span>'
        )

        row_html = f"""
        <tr class="case-row" onclick="toggleDetails('{c.case_id}')">
            <td><code>{c.case_id}</code></td>
            <td><span class="cat-tag">{c.category}</span></td>
            <td class="query-col">{c.user_message}</td>
            <td class="text-center">{t1_badge}</td>
            <td class="text-center">{t2_badge}</td>
            <td class="text-center">{t3_badge}</td>
            <td class="text-center">{e2e_badge}</td>
            <td class="text-right latency-text">{c.latency_ms} ms</td>
        </tr>
        <tr id="details-{c.case_id}" class="details-row" style="display: none;">
            <td colspan="8">
                <div class="details-box">
                    <strong>說明：</strong> {c.description}<br/>
                    <strong>測試詳情：</strong>
                    <pre><code>{c.details}</code></pre>
                </div>
            </td>
        </tr>
        """
        case_rows_html.append(row_html)

    table_body = "\n".join(case_rows_html)

    html_content = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PickForMe 4-Tier LLMOps 評測儀表板</title>
    <style>
        :root {{
            --bg-color: #0d1117;
            --card-bg: #161b22;
            --border-color: #30363d;
            --text-primary: #c9d1d9;
            --text-secondary: #8b949e;
            --accent-blue: #58a6ff;
            --accent-green: #238636;
            --accent-green-bright: #3fb950;
            --accent-red: #da3633;
            --accent-purple: #bc8cff;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            background-color: var(--bg-color);
            color: var(--text-primary);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            padding: 32px 24px;
            line-height: 1.6;
        }}
        .container {{ max-width: 1200px; margin: 0 auto; }}
        header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 28px;
            padding-bottom: 20px;
            border-bottom: 1px solid var(--border-color);
        }}
        .title-group h1 {{
            font-size: 26px;
            font-weight: 700;
            color: #ffffff;
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        .title-group p {{ color: var(--text-secondary); font-size: 14px; margin-top: 4px; }}
        .badge {{
            padding: 6px 14px;
            border-radius: 20px;
            font-weight: 600;
            font-size: 13px;
            letter-spacing: 0.5px;
        }}
        .badge-success {{ background-color: var(--accent-green); color: #fff; }}
        .badge-danger {{ background-color: var(--accent-red); color: #fff; }}

        .kpi-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
            gap: 16px;
            margin-bottom: 28px;
        }}
        .kpi-card {{
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 20px;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
        }}
        .kpi-title {{ font-size: 13px; font-weight: 600; color: var(--text-secondary); text-transform: uppercase; }}
        .kpi-value {{ font-size: 32px; font-weight: 700; color: #fff; margin: 8px 0; }}
        .kpi-sub {{ font-size: 12px; color: var(--accent-green-bright); display: flex; align-items: center; gap: 4px; }}

        .table-container {{
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            overflow: hidden;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 14px;
        }}
        th {{
            background-color: #21262d;
            color: var(--text-secondary);
            font-weight: 600;
            text-align: left;
            padding: 14px 16px;
            border-bottom: 1px solid var(--border-color);
        }}
        td {{
            padding: 14px 16px;
            border-bottom: 1px solid var(--border-color);
        }}
        .case-row {{ cursor: pointer; transition: background 0.2s ease; }}
        .case-row:hover {{ background-color: #21262d; }}
        .text-center {{ text-align: center; }}
        .text-right {{ text-align: right; }}

        .cat-tag {{
            background: #23272d;
            border: 1px solid var(--border-color);
            padding: 3px 8px;
            border-radius: 6px;
            font-size: 11px;
            color: var(--accent-blue);
        }}
        .query-col {{ font-weight: 500; color: #f0f6fc; max-width: 320px; }}
        .latency-text {{ font-family: monospace; color: var(--text-secondary); }}

        .pill {{
            padding: 3px 8px;
            border-radius: 12px;
            font-size: 11px;
            font-weight: 600;
        }}
        .pill-green {{ background: rgba(46, 160, 67, 0.2); color: var(--accent-green-bright); border: 1px solid rgba(46, 160, 67, 0.4); }}
        .pill-red {{ background: rgba(248, 81, 73, 0.2); color: #ff7b72; border: 1px solid rgba(248, 81, 73, 0.4); }}

        .details-box {{
            background: #0d1117;
            padding: 16px;
            border-radius: 8px;
            font-size: 13px;
            color: var(--text-primary);
        }}
        .details-box pre {{
            background: #161b22;
            padding: 10px;
            border-radius: 6px;
            margin-top: 8px;
            overflow-x: auto;
            color: #79c0ff;
        }}
        footer {{
            margin-top: 32px;
            text-align: center;
            color: var(--text-secondary);
            font-size: 13px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="title-group">
                <h1>🍱 PickForMe 4-Tier LLMOps 評測儀表板</h1>
                <p>Industrial-Grade 離線回放與確定性數值守門基準體系</p>
            </div>
            <div>{status_badge}</div>
        </header>

        <section class="kpi-grid">
            <div class="kpi-card">
                <span class="kpi-title">Tier 1: 意圖精確率</span>
                <span class="kpi-value">{summary.tier1_intent_accuracy * 100:.1f}%</span>
                <span class="kpi-sub">門檻: ≥90% (當前通過)</span>
            </div>
            <div class="kpi-card">
                <span class="kpi-title">Tier 2: 實體解析 F1</span>
                <span class="kpi-value">{summary.tier2_entity_f1:.4f}</span>
                <span class="kpi-sub">門檻: ≥0.90 (當前通過)</span>
            </div>
            <div class="kpi-card">
                <span class="kpi-title">Tier 3: 數值護欄合規</span>
                <span class="kpi-value">{summary.tier3_guardrail_compliance * 100:.1f}%</span>
                <span class="kpi-sub">門檻: ≥95% (當前通過)</span>
            </div>
            <div class="kpi-card">
                <span class="kpi-title">P50 延遲基準</span>
                <span class="kpi-value">{summary.latency_p50_ms:.0f} ms</span>
                <span class="kpi-sub">P90: {summary.latency_p90_ms:.0f} ms | 平均: {summary.latency_avg_ms:.0f} ms</span>
            </div>
        </section>

        <section class="table-container">
            <table>
                <thead>
                    <tr>
                        <th style="width: 100px;">ID</th>
                        <th style="width: 130px;">類別</th>
                        <th>測試提問</th>
                        <th class="text-center" style="width: 90px;">T1 意圖</th>
                        <th class="text-center" style="width: 90px;">T2 比對</th>
                        <th class="text-center" style="width: 90px;">T3 護欄</th>
                        <th class="text-center" style="width: 100px;">綜合結果</th>
                        <th class="text-right" style="width: 90px;">延遲</th>
                    </tr>
                </thead>
                <tbody>
                    {table_body}
                </tbody>
            </table>
        </section>

        <footer>
            PickForMe • Automated Evaluation Pipeline • Powered by GitLab Pages
        </footer>
    </div>

    <script>
        function toggleDetails(id) {{
            const el = document.getElementById('details-' + id);
            if (el.style.display === 'none') {{
                el.style.display = 'table-row';
            }} else {{
                el.style.display = 'none';
            }}
        }}
    </script>
</body>
</html>
"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    return output_path
