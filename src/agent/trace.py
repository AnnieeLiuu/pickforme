import logging
import time
from typing import Any, Optional
from uuid import UUID

from src.db.repositories.trace import TraceRepository

logger = logging.getLogger(__name__)


class AgentTraceLogger:
    def __init__(self, trace_repo: Optional[TraceRepository] = None):
        self.repo = trace_repo or TraceRepository()
        self.run_id: Optional[UUID] = None
        self.start_time: float = 0.0
        self.step_count: int = 0

    async def start_run(self, chat_id: Optional[int], user_query: str) -> UUID:
        """開始一個新的 Agent 執行歷程"""
        self.start_time = time.time()
        self.step_count = 0
        try:
            self.run_id = await self.repo.create_agent_run(chat_id=chat_id, user_query=user_query)
        except Exception as e:
            logger.warning(f"Failed to start agent trace run in DB: {e}")
            self.run_id = None
        return self.run_id  # type: ignore[return-value]

    async def log_step(
        self,
        action_type: str,
        tool_name: Optional[str] = None,
        tool_input: Optional[dict[str, Any]] = None,
        tool_output: Optional[dict[str, Any]] = None,
        latency_ms: Optional[int] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """紀錄單一步驟"""
        if not self.run_id:
            return

        self.step_count += 1
        try:
            await self.repo.record_agent_step(
                run_id=self.run_id,
                step_number=self.step_count,
                action_type=action_type,
                tool_name=tool_name,
                tool_input=tool_input,
                tool_output=tool_output,
                latency_ms=latency_ms,
                error_message=error_message,
            )
        except Exception as e:
            logger.warning(f"Failed to log agent step to DB: {e}")

    async def finish_run(
        self,
        status: str = "completed",
        model_used: Optional[str] = None,
        total_tokens: Optional[int] = None,
    ) -> None:
        """完成執行歷程，寫入總結時間"""
        if not self.run_id:
            return

        total_latency_ms = int((time.time() - self.start_time) * 1000)
        try:
            await self.repo.update_agent_run(
                run_id=self.run_id,
                status=status,
                model_used=model_used,
                total_tokens=total_tokens,
                latency_ms=total_latency_ms,
            )
        except Exception as e:
            logger.warning(f"Failed to finish agent run in DB: {e}")
