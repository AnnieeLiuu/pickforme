"""Observability and GitLab Pages Synchronization Contract Test.

Ensures that any state machine changes (FSM States, Step Payloads, Foodie Ranking)
are always compatible with src/eval/reporter.py and produce valid HTML dashboard reports.
Enforces that architecture changes never break Pages observability visualization.
"""

from pathlib import Path

from src.eval.evaluator import CaseEvalResult, EvalSummary
from src.eval.reporter import generate_html_dashboard


def test_reporter_html_rendering_with_latest_fsm_steps(tmp_path: Path) -> None:
    """Verifies that reporter correctly formats latest FSM 4-Stage payloads into HTML."""
    sample_summary = EvalSummary(
        total_cases=1,
        tier1_intent_accuracy=1.0,
        tier2_entity_f1=1.0,
        tier3_guardrail_compliance=1.0,
        tier4_e2e_success_rate=1.0,
        latency_p50_ms=450,
        latency_p90_ms=800,
        latency_avg_ms=500,
        all_passed_gate=True,
        case_results=[
            CaseEvalResult(
                case_id="eval_sync_001",
                category="taste_rag",
                description="測試觀測性同步",
                user_message="新宿濃郁拉麵",
                tier1_pass=True,
                tier2_pass=True,
                tier3_pass=True,
                tier4_pass=True,
                latency_ms=450,
            )
        ],
    )

    live_runs = [
        {
            "run_id": "test-sync-run-001",
            "user_query": "新宿拉麵",
            "model_used": "groq/qwen3.8-27b",
            "total_tokens": 850,
            "latency_ms": 1250,
            "status": "SUCCESS",
            "created_at": "2026-10-11T12:00:00Z",
        }
    ]

    live_steps = {
        "test-sync-run-001": [
            {
                "step_number": 1,
                "action_type": "state_1_intent",
                "tool_name": "extract_intent",
                "tool_input": {"user_query": "新宿拉麵"},
                "tool_output": {"intent": "search", "location": "新宿", "cuisine": "拉麵"},
                "latency_ms": 280,
            },
            {
                "step_number": 2,
                "action_type": "state_2_places_search",
                "tool_name": "places_search_ladder",
                "tool_input": {"location": "新宿", "radius": 800},
                "tool_output": {
                    "candidate_count": 20,
                    "prefiltered_count": 6,
                    "main_candidates": 4,
                    "backup_candidates": 2,
                },
                "latency_ms": 420,
            },
            {
                "step_number": 3,
                "action_type": "state_3_tabelog_enrich",
                "tool_name": "tabelog_parallel_enrich",
                "tool_input": {"store_count": 4},
                "tool_output": {
                    "aligned_stores": [
                        {
                            "name_ja": "風雲児",
                            "tabelog_rating": 3.78,
                            "review_count": 1420,
                            "supports_paypay": True,
                            "rank_badge": "🥇",
                            "foodie_score": 3.93,
                        }
                    ]
                },
                "latency_ms": 310,
            },
            {
                "step_number": 4,
                "action_type": "state_4_synthesis",
                "tool_name": "generate_recommendation_card",
                "tool_input": {"candidates": 1},
                "tool_output": {"card": "`風雲児`", "guardrail_applied": True},
                "latency_ms": 240,
            },
        ]
    }

    output_file = tmp_path / "index.html"
    res_path = generate_html_dashboard(
        summary=sample_summary,
        output_path=output_file,
        live_runs=live_runs,
        live_steps_by_run=live_steps,
    )

    assert res_path.exists()
    content = res_path.read_text(encoding="utf-8")

    # 驗證關鍵標籤皆成功注入
    assert "PickForMe" in content
    assert "新宿濃郁拉麵" in content
    assert "test-sync-run-001" in content or "test-syn" in content
    assert "風雲児" in content
    assert "3.78" in content
    assert "state_2_places_search" in content
    assert "state_3_tabelog_enrich" in content
    assert "prefiltered_count" in content


def test_observability_depth_enhancements(tmp_path: Path) -> None:
    """Verifies that the HTML dashboard includes depth enhancements:
    1. Upgraded dynamic connection capsules with concrete algorithmic actions.
    2. Right drawer layout with architecture decisions placed prominently at the top.
    3. Left sidebar intelligent auto-focus mapping (caseHighlightNodes) for multi-case switching.
    """
    from src.eval.evaluator import CaseEvalResult, EvalSummary

    mock_summary = EvalSummary(
        total_cases=1,
        tier1_intent_accuracy=1.0,
        tier2_entity_f1=1.0,
        tier3_guardrail_compliance=1.0,
        tier4_e2e_success_rate=1.0,
        latency_p50_ms=450,
        latency_p90_ms=800,
        latency_avg_ms=500,
        all_passed_gate=True,
        case_results=[
            CaseEvalResult(
                case_id="eval_depth_001",
                category="observability_depth",
                description="測試深度可觀測性精進",
                user_message="神保町車站 飯糰",
                tier1_pass=True,
                tier2_pass=True,
                tier3_pass=True,
                tier4_pass=True,
                latency_ms=450,
            )
        ],
    )

    output_file = tmp_path / "depth_dashboard.html"
    res_path = generate_html_dashboard(
        summary=mock_summary,
        output_path=output_file,
    )
    assert res_path.exists()
    content = res_path.read_text(encoding="utf-8")

    # 1. 驗證動態連線條件膠囊文字顯性化
    assert "追問直達 · 陣列索引 [1] 映射為神保町おにぎり" in content
    assert "缺地點引導直達 · 0ms 截流防呆免發 Places 費用" in content
    assert "都道府縣行政區一票否決" in content

    # 2. 驗證右側審計抽屜架構因果解讀置頂（在 detail-input 之前出現）
    action_idx = content.find("detail-action-container")
    input_idx = content.find('id="detail-input"')
    assert action_idx != -1, "detail-action-container should exist in HTML"
    assert input_idx != -1, "detail-input should exist in HTML"
    assert action_idx < input_idx, "detail-action-container MUST appear before detail-input"
    assert "💡 關鍵架構決策 ＆ 因果解讀" in content

    # 3. 驗證智慧亮點自適應對焦 (caseHighlightNodes) 與多案例切換
    assert "caseHighlightNodes" in content
    assert "case_expand" in content
    assert "case_prewarm" in content
