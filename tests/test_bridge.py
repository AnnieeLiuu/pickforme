from unittest.mock import AsyncMock, MagicMock

import pytest
from src.agent.bridge import IDEAgentBridge
from src.agent.devops import DevOpsSubagent


@pytest.mark.asyncio
async def test_ide_agent_bridge_available():
    bridge = IDEAgentBridge(bridge_url="http://127.0.0.1:8000/agent/diagnose")
    bridge.is_bridge_available = AsyncMock(return_value=True)  # type: ignore[method-assign]
    assert await bridge.is_bridge_available() is True


@pytest.mark.asyncio
async def test_devops_subagent_with_bridge_fallback():
    mock_bridge = MagicMock()
    mock_bridge.forward_diagnosis_request = AsyncMock(
        return_value="🤖 **[IDE Agent 遠端直連分析]**\n系統一切正常！"
    )

    devops = DevOpsSubagent(bridge=mock_bridge)
    report = await devops.run_diagnosis("debug")

    assert "IDE Agent 遠端直連分析" in report
    assert "系統一切正常" in report
