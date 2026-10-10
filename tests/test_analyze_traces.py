from scripts.analyze_traces import (
    _handle_inspection_flow,
    format_json_block,
    inspect_single_run,
)


def test_format_json_block():
    assert format_json_block(None) == "None"
    assert format_json_block({"a": 1}) == '{\n  "a": 1\n}'
    assert format_json_block('{"b": 2}') == '{\n  "b": 2\n}'
    assert format_json_block("not_json") == "not_json"


def test_inspect_single_run_captures_card(capsys):
    run = {
        "run_id": "test-uuid-1234",
        "user_query": "新宿 美味拉麵",
        "status": "completed",
        "latency_ms": 1500,
        "model_used": "groq/llama-3.3-70b-versatile",
        "total_tokens": 450,
        "created_at": "2026-10-10T12:00:00",
    }
    steps = [
        {
            "step_number": 1,
            "action_type": "state_1_intent",
            "tool_name": "intent_parser",
            "tool_input": {"message": "新宿 美味拉麵"},
            "tool_output": {"intent": "search", "location": "新宿", "cuisine": "拉麵"},
            "latency_ms": 300,
        },
        {
            "step_number": 4,
            "action_type": "state_4_synthesis",
            "tool_name": "card_synthesis",
            "tool_input": {"stores_count": 3},
            "tool_output": {"card_content": "### 1. `寿々喜家 本店`\n💳 **支付方式**：可使用信用卡 / PayPay"},
            "latency_ms": 600,
        },
    ]

    inspect_single_run(run, steps)
    captured = capsys.readouterr().out

    assert "PickForMe 單次問答端到端歷程透視報告" in captured
    assert "新宿 美味拉麵" in captured
    assert "寿々喜家 本店" in captured
    assert "💳 **支付方式**：可使用信用卡 / PayPay" in captured
    assert "state_1_intent" in captured
    assert "card_synthesis" in captured


def test_handle_inspection_flow_latest(capsys):
    runs = [
        {"run_id": "run-1", "user_query": "最新問答", "latency_ms": 100},
        {"run_id": "run-2", "user_query": "較早問答", "latency_ms": 200},
    ]
    steps_by_run = {
        "run-1": [
            {
                "step_number": 1,
                "action_type": "state_1_intent",
                "tool_name": "intent_parser",
                "tool_input": {},
                "tool_output": {"card_content": "這是最新卡片"},
                "latency_ms": 50,
            }
        ]
    }

    _handle_inspection_flow(
        runs=runs,
        steps_by_run=steps_by_run,
        inspect_latest=True,
        inspect_idx=None,
        inspect_id=None,
    )
    captured = capsys.readouterr().out
    assert "最新問答" in captured
    assert "這是最新卡片" in captured
