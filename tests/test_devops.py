from unittest.mock import AsyncMock, MagicMock

import pytest
from src.agent.devops import DevOpsSubagent


@pytest.mark.asyncio
async def test_devops_subagent_run_diagnosis():
    # Mock AgentEngine
    mock_agent_engine = MagicMock()
    mock_agent_engine.call_llm_with_failover = AsyncMock(
        return_value=(
            {"content": "### 1. 🛠️ 現狀診斷摘要\n系統運作正常，DB 連線良好。"},
            "gemini-3.8-flash",
            150,
        )
    )

    devops = DevOpsSubagent(agent_engine=mock_agent_engine)

    # 執行診斷
    report = await devops.run_diagnosis("@debug 幫我排查伺服器")

    assert "DevOps 診斷報告" in report
    assert "gemini-3.8-flash" in report
    assert "系統運作正常" in report
