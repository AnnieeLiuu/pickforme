import logging
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from src.agent.llm_client import LLMClient
from src.agent.pipeline import FoodRecommendationFSM
from src.agent.tools import AgentToolExecutor
from src.agent.trace import AgentTraceLogger

logger = logging.getLogger(__name__)


class AgentEngine:
    """Agent 核心對外 Facade 入口：內裝全新 4 階段 FSM 狀態機與統一 LLM 通訊客戶端"""

    def __init__(
        self,
        tool_executor: Optional[AgentToolExecutor] = None,
        trace_logger: Optional[AgentTraceLogger] = None,
        llm_client: Optional[LLMClient] = None,
        max_steps: int = 6,
    ):
        self.tool_executor = tool_executor or AgentToolExecutor()
        self.trace_logger = trace_logger or AgentTraceLogger()
        self.llm_client = llm_client or LLMClient()
        self.max_steps = max_steps
        self.system_prompt = self._load_system_prompt()

        # 核心驅動：FSM 狀態機流水線
        self.fsm = FoodRecommendationFSM(
            llm_client=self.llm_client,
            places_client=self.tool_executor.places_client,
            tabelog_scraper=self.tool_executor.tabelog_scraper,
            entity_resolver=self.tool_executor.entity_resolver,
            restaurant_repo=self.tool_executor.restaurant_repo,
            trace_logger=self.trace_logger,
        )

    def _load_system_prompt(self) -> str:
        """載入 Agent System Prompt 指令檔案"""
        prompt_path = Path(__file__).parent.parent.parent / "config" / "prompts" / "agent_system.md"
        if prompt_path.exists():
            return prompt_path.read_text(encoding="utf-8")
        return "你是一位日本美食特派員 PickForMe，請依據使用者地點與需求推薦美食。"

    async def _is_ollama_online(self) -> bool:
        """向 LLMClient 委託探測本機 Ollama 狀態"""
        return await self.llm_client._is_ollama_online()

    async def _call_openai_compatible_api(
        self,
        url: str,
        api_key: str,
        model: str,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        timeout: float = 60.0,
    ) -> tuple[dict[str, Any], int]:
        """相容性轉發：調用 LLMClient 底層 REST API"""
        return await self.llm_client._call_openai_compatible_api(
            url=url,
            api_key=api_key,
            model=model,
            messages=messages,
            timeout=timeout,
        )

    async def call_llm_with_failover(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
    ) -> tuple[dict[str, Any], str, int]:
        """相容性轉發：向 LLMClient 委託調用多級模型降級鏈"""
        content, model, tokens = await self.llm_client.call_text(messages)
        message_obj = {"role": "assistant", "content": content}
        return message_obj, model, tokens

    async def run_agent(
        self,
        chat_id: Optional[int],
        user_message: str,
        user_location: Optional[tuple[float, float]] = None,
        conversation_history: Optional[list[dict[str, str]]] = None,
        on_progress: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> str:
        """由確定性 4 階段 FSM 狀態機驅動的 Agent 執行主入口"""
        return await self.fsm.run_pipeline(
            chat_id=chat_id,
            user_message=user_message,
            user_location=user_location,
            conversation_history=conversation_history,
            on_progress=on_progress,
        )
