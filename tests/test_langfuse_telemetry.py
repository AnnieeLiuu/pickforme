from unittest.mock import MagicMock, patch

import pytest
from src.config import Settings
from src.services.telemetry import NoOpSpan, TelemetryManager


def test_telemetry_disabled_in_test_environment():
    """驗證在 test 環境下，即使配置了 Key，is_langfuse_enabled() 依然為 False (0ms 離線保護)"""
    custom_settings = Settings(
        ENVIRONMENT="test",
        LANGFUSE_PUBLIC_KEY="pk-lf-test",
        LANGFUSE_SECRET_KEY="sk-lf-test",
    )
    assert custom_settings.is_langfuse_enabled() is False

    mgr = TelemetryManager()
    with patch("src.services.telemetry.settings", custom_settings):
        trace = mgr.start_trace(name="handle-user-request")
        assert isinstance(trace, NoOpSpan)

        span = mgr.start_span("classify-intent")
        assert isinstance(span, NoOpSpan)

        # 鏈式與 Context Manager 均安全執行
        with span:
            span.update(key="val")
            span.end()

        # 生成記錄不拋錯
        mgr.record_generation(
            name="llm-call",
            model="test-model",
            prompt="hello",
            completion="world",
        )
        mgr.end_trace(trace)


def test_telemetry_enabled_mock_client():
    """驗證在配置完備且非 test 環境下，TelemetryManager 正確轉發 Trace, Span 與 Generation"""
    custom_settings = Settings(
        ENVIRONMENT="production",
        LANGFUSE_PUBLIC_KEY="pk-lf-prod",
        LANGFUSE_SECRET_KEY="sk-lf-prod",
        LANGFUSE_HOST="https://cloud.langfuse.com",
    )
    assert custom_settings.is_langfuse_enabled() is True

    mock_client = MagicMock()
    mock_trace = MagicMock()
    mock_span = MagicMock()
    mock_gen = MagicMock()

    def mock_start_observation(name, as_type="span", **kwargs):
        if as_type == "chain":
            return mock_trace
        elif as_type == "generation":
            return mock_gen
        return mock_span

    mock_client.start_observation.side_effect = mock_start_observation

    mgr = TelemetryManager()
    mgr._client = mock_client
    mgr._initialized = True

    with (
        patch("src.services.telemetry.settings", custom_settings),
        patch("langfuse.propagate_attributes") as mock_propagate,
    ):
        mock_ctx = MagicMock()
        mock_propagate.return_value = mock_ctx

        # 1. Start Trace
        trace = mgr.start_trace(
            name="handle-user-request",
            session_id="12345",
            user_id="12345",
            input_data={"message": "東京拉麵"},
        )
        assert trace == mock_trace
        mock_client.start_observation.assert_called_with(
            name="handle-user-request",
            as_type="chain",
            input={"message": "東京拉麵"},
            metadata={},
        )

        # 2. Start Span
        span = mgr.start_span("classify-intent", input_data={"query": "東京拉麵"})
        assert span == mock_span
        mock_client.start_observation.assert_called_with(
            name="classify-intent",
            as_type="span",
            input={"query": "東京拉麵"},
            metadata={},
        )

        # 3. Record Generation under Span
        mgr.record_generation(
            name="llm-call:gemini",
            model="gemini-2.5-flash",
            prompt=[{"role": "user", "content": "hi"}],
            completion='{"intent": "search"}',
            usage={"input": 10, "output": 20, "total": 30},
            latency_ms=150.5,
        )
        mock_client.start_observation.assert_called_with(
            name="llm-call:gemini",
            as_type="generation",
            model="gemini-2.5-flash",
            input=[{"role": "user", "content": "hi"}],
            output='{"intent": "search"}',
            model_parameters={},
            usage_details={"input": 10, "output": 20, "total": 30},
            level="DEFAULT",
            status_message=None,
            metadata={"latency_ms": 150.5},
        )
        mock_gen.end.assert_called_once()

        # 4. End Span & End Trace
        mgr.end_span(span, output_data={"status": "ok"})
        mock_span.update.assert_called_once_with(output={"status": "ok"})
        mock_span.end.assert_called_once()

        mgr.end_trace(trace, output_data="最終卡片")
        mock_trace.update.assert_called_once_with(output="最終卡片")
        mock_trace.end.assert_called_once()


def test_telemetry_fail_open_resilience():
    """驗證當 Langfuse SDK 內部拋出網路拋錯或逾時時，TelemetryManager 完全靜默隔離 (Fail-Open)"""
    custom_settings = Settings(
        ENVIRONMENT="production",
        LANGFUSE_PUBLIC_KEY="pk-lf-prod",
        LANGFUSE_SECRET_KEY="sk-lf-prod",
    )
    mock_client = MagicMock()
    mock_client.start_observation.side_effect = RuntimeError("Network timeout to Langfuse Cloud")

    mgr = TelemetryManager()
    mgr._client = mock_client
    mgr._initialized = True

    with patch("src.services.telemetry.settings", custom_settings):
        # start_trace 出錯時降級為 NoOpSpan，不拋出任何例外
        trace = mgr.start_trace(name="handle-user-request")
        assert isinstance(trace, NoOpSpan)

        # 續發 span 依然安全
        span = mgr.start_span("classify-intent")
        assert isinstance(span, NoOpSpan)

        # 結束也安全
        mgr.end_span(span)
        mgr.end_trace(trace)


@pytest.mark.asyncio
async def test_llm_client_records_generation_with_telemetry():
    """驗證 LLMClient._call_openai_compatible_api 呼叫時會自動呼叫 telemetry.record_generation"""
    from src.agent.llm_client import LLMClient
    from src.services.telemetry import telemetry

    llm = LLMClient()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": "ok"}}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17},
    }

    with (
        patch("httpx.AsyncClient.post", return_value=mock_resp),
        patch.object(telemetry, "record_generation") as mock_record_gen,
    ):
        msg, tokens = await llm._call_openai_compatible_api(
            url="http://mock-llm",
            api_key="test-key",
            model="mock-model",
            messages=[{"role": "user", "content": "test"}],
        )
        assert msg["content"] == "ok"
        assert tokens == 17
        mock_record_gen.assert_called_once()
        kwargs = mock_record_gen.call_args.kwargs
        assert kwargs["name"] == "llm-call:mock-model"
        assert kwargs["model"] == "mock-model"
        assert kwargs["usage"] == {"input": 12, "output": 5, "total": 17}
