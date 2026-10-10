from pathlib import Path

import pytest
from src.eval.evaluator import EvaluationEngine
from src.eval.reporter import generate_html_dashboard, generate_markdown_report


@pytest.mark.asyncio
async def test_evaluation_engine_offline_gatekeeper():
    """測試 4-Tier 離線評測體系符合 CI 硬性門檻"""
    engine = EvaluationEngine(live_mode=False)
    cases = engine.load_cases()
    assert len(cases) >= 15, "評測題庫數量應至少具備 15 題以上典型場景"

    summary = await engine.run_evaluation()

    # 驗證四大門檻
    assert summary.tier1_intent_accuracy >= 0.90, f"Tier 1 意圖抽取率未達標: {summary.tier1_intent_accuracy}"
    assert summary.tier2_entity_f1 >= 0.90, f"Tier 2 實體解析 F1 未達標: {summary.tier2_entity_f1}"
    assert summary.tier3_guardrail_compliance >= 0.95, f"Tier 3 護欄合規率未達標: {summary.tier3_guardrail_compliance}"
    assert summary.all_passed_gate is True, "整體 CI Gatekeeper 應為通過"


def test_reporter_generation(tmp_path: Path):
    """測試 Markdown 與 HTML 報告生成器"""
    from src.eval.evaluator import CaseEvalResult, EvalSummary

    mock_summary = EvalSummary(
        total_cases=1,
        tier1_intent_accuracy=1.0,
        tier2_entity_f1=1.0,
        tier3_guardrail_compliance=1.0,
        tier4_e2e_success_rate=1.0,
        latency_avg_ms=12.0,
        latency_p50_ms=12.0,
        latency_p90_ms=12.0,
        all_passed_gate=True,
        case_results=[
            CaseEvalResult(
                case_id="eval_test",
                category="test",
                description="測試案例",
                user_message="測試",
                tier1_pass=True,
                tier2_pass=True,
                tier3_pass=True,
                tier4_pass=True,
                latency_ms=12,
                details={},
            )
        ],
    )

    md = generate_markdown_report(mock_summary)
    assert "# 🍱 PickForMe 4-Tier 離線評測體系報告" in md
    assert "CI GATE PASSED" or "PASSED" in md

    html_file = tmp_path / "index.html"
    out = generate_html_dashboard(mock_summary, html_file)
    assert out.exists()
    content = out.read_text(encoding="utf-8")
    assert "PickForMe 4-Tier LLMOps" in content
    assert "eval_test" in content
