import json
import logging
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

import httpx
from src.agent.tools import TOOL_SCHEMAS, AgentToolExecutor
from src.agent.trace import AgentTraceLogger
from src.config import settings

logger = logging.getLogger(__name__)

GEMINI_OPENAI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
GROQ_OPENAI_URL = "https://api.groq.com/openai/v1/chat/completions"


class AgentEngine:
    def __init__(
        self,
        tool_executor: Optional[AgentToolExecutor] = None,
        trace_logger: Optional[AgentTraceLogger] = None,
        max_steps: int = 6,
    ):
        self.tool_executor = tool_executor or AgentToolExecutor()
        self.trace_logger = trace_logger or AgentTraceLogger()
        self.max_steps = max_steps
        self.system_prompt = self._load_system_prompt()

    def _load_system_prompt(self) -> str:
        """載入 Agent System Prompt 指令檔案"""
        prompt_path = Path(__file__).parent.parent.parent / "config" / "prompts" / "agent_system.md"
        if prompt_path.exists():
            return prompt_path.read_text(encoding="utf-8")
        return "你是一位日本美食特派員 PickForMe，請依據使用者地點與需求推薦美食。"

    async def call_llm_with_failover(self, messages: list[dict[str, Any]]) -> tuple[dict[str, Any], str, int]:
        """呼叫 LLM (Gemini Flash 主力 ➡️ Groq Qwen 27B ➡️ Groq Llama 8B 多層自動降級)"""
        errors = []

        # 1. 嘗試 Gemini Flash (Primary)
        if settings.GEMINI_API_KEY:
            try:
                msg_resp, tokens = await self._call_openai_compatible_api(
                    url=GEMINI_OPENAI_URL,
                    api_key=settings.GEMINI_API_KEY,
                    model="gemini-3.8-flash",
                    messages=messages,
                    tools=TOOL_SCHEMAS,
                )
                return msg_resp, "gemini-3.8-flash", tokens
            except Exception as e:
                logger.warning(f"Primary Gemini Flash LLM call failed: {e}. Attempting failover to Groq...")
                errors.append(f"Gemini: {e}")

        # 2. 備援降級 1：Groq Qwen 3.8 27B (Fallback 1)
        if settings.GROQ_API_KEY:
            for fallback_model in ["qwen/qwen3.8-27b"]:
                try:
                    msg_resp, tokens = await self._call_openai_compatible_api(
                        url=GROQ_OPENAI_URL,
                        api_key=settings.GROQ_API_KEY,
                        model=fallback_model,
                        messages=messages,
                        tools=TOOL_SCHEMAS,
                    )
                    return msg_resp, fallback_model, tokens
                except Exception as e:
                    logger.warning(f"Groq model {fallback_model} call failed: {e}")
                    errors.append(f"Groq ({fallback_model}): {e}")

        raise RuntimeError(f"All LLM endpoints failed. Errors: {'; '.join(errors)}")

    async def _call_openai_compatible_api(
        self,
        url: str,
        api_key: str,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], int]:
        """以標準 OpenAI ChatCompletions REST API 發送請求"""
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        # 淨化 messages 格式 (轉化 None 為空字串，相容各 API 規範)
        cleaned_messages = []
        for msg in messages:
            cleaned_msg = dict(msg)
            if cleaned_msg.get("content") is None:
                cleaned_msg["content"] = ""
            cleaned_messages.append(cleaned_msg)

        body: dict[str, Any] = {
            "model": model,
            "messages": cleaned_messages,
            "temperature": 0.2,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(url, json=body, headers=headers)
            if resp.status_code == 429:
                logger.warning(f"LLM API ({model}) 觸發 Rate Limit (429)，等待 2.5 秒後進行自動重試...")
                await asyncio.sleep(2.5)
                resp = await client.post(url, json=body, headers=headers)

            if resp.status_code >= 400:
                logger.error(f"LLM API Error ({model}) HTTP {resp.status_code}: {resp.text[:500]}")
                resp.raise_for_status()

            data = resp.json()
            choice = data.get("choices", [{}])[0]
            message_obj = choice.get("message", {})
            usage = data.get("usage", {})
            total_tokens = usage.get("total_tokens", 0)

            return message_obj, total_tokens

    async def run_agent(
        self,
        chat_id: Optional[int],
        user_message: str,
        user_location: Optional[tuple[float, float]] = None,
        conversation_history: Optional[list[dict[str, str]]] = None,
        on_progress: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> str:
        """自研 OpenAI-Format Agent Loop 核心入口"""
        # 開始 Trace
        await self.trace_logger.start_run(chat_id=chat_id, user_query=user_message)

        # 組裝初始 Messages
        messages: list[dict[str, Any]] = [{"role": "system", "content": self.system_prompt}]

        # 帶入使用者位置上下文
        if user_location:
            lat, lng = user_location
            messages.append(
                {
                    "role": "system",
                    "content": f"[當前系統座標情境] 使用者目前的 GPS 座標為：緯度 {lat}, 經度 {lng}。",
                }
            )

        # 帶入前幾輪對話歷史上下文
        if conversation_history:
            for item in conversation_history:
                role = item.get("role", "user")
                content = item.get("content", "")
                if role in ["user", "assistant"] and content:
                    messages.append({"role": role, "content": content})

        messages.append({"role": "user", "content": user_message})

        total_tokens_used = 0
        last_model_used = "unknown"

        for step in range(1, self.max_steps + 1):
            if on_progress:
                await on_progress(f"🔍 PickForMe 正在思考中... (步驟 {step}/{self.max_steps})")

            t0 = time.time()
            try:
                message_obj, model_used, tokens = await self.call_llm_with_failover(messages)
                last_model_used = model_used
                total_tokens_used += tokens
                llm_latency_ms = int((time.time() - t0) * 1000)

                # 紀錄 LLM Call Step
                await self.trace_logger.log_step(
                    action_type="llm_call",
                    tool_name=None,
                    tool_input={"messages_count": len(messages)},
                    tool_output={"content_length": len(message_obj.get("content") or "")},
                    latency_ms=llm_latency_ms,
                )

            except Exception as e:
                logger.error(f"LLM call error at step {step}: {e}")
                await self.trace_logger.finish_run(status="failed")
                return f"抱歉，連線至 AI 模型遭遇問題：{str(e)}"

            # 檢查是否有 Tool Calls
            tool_calls = message_obj.get("tool_calls")

            # 1. 無 Tool Call ➡️ LLM 回覆最終答案
            if not tool_calls:
                final_answer = str(message_obj.get("content", "抱歉，無法處理您的請求。"))
                await self.trace_logger.finish_run(
                    status="completed",
                    model_used=last_model_used,
                    total_tokens=total_tokens_used,
                )
                return final_answer

            # 2. 有 Tool Calls ➡️ 執行 Tool 並寫回 Messages
            messages.append(message_obj)

            for tool_call in tool_calls:
                tool_call_id = tool_call.get("id", f"call_{int(time.time())}")
                func_obj = tool_call.get("function", {})
                tool_name = func_obj.get("name", "")
                args_str = func_obj.get("arguments", "{}")

                try:
                    tool_args = json.loads(args_str) if isinstance(args_str, str) else args_str
                except json.JSONDecodeError:
                    tool_args = {}

                # 進度通知
                if on_progress:
                    readable_names = {
                        "geocode_location": "搜尋地點座標",
                        "search_nearby": "檢索週邊美食餐廳",
                        "get_tabelog_info": "比對並獲取 Tabelog 評分",
                    }
                    display_name = readable_names.get(tool_name, tool_name)
                    await on_progress(f"📊 正在執行：{display_name}...")

                # 執行 Tool
                tool_t0 = time.time()
                tool_output_str = await self.tool_executor.execute_tool(tool_name, tool_args)
                tool_latency_ms = int((time.time() - tool_t0) * 1000)

                # Context Budgeting: 長度超過 2000 字自動截斷
                if len(tool_output_str) > 2000:
                    tool_output_str = tool_output_str[:2000] + "... [Output Truncated]"

                # 寫回 Message
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "name": tool_name,
                        "content": tool_output_str,
                    }
                )

                # 紀錄 Trace
                await self.trace_logger.log_step(
                    action_type="tool_call",
                    tool_name=tool_name,
                    tool_input=tool_args,
                    tool_output={"response_preview": tool_output_str[:200]},
                    latency_ms=tool_latency_ms,
                )

        # 超過最大步數
        await self.trace_logger.finish_run(
            status="max_steps_exceeded",
            model_used=last_model_used,
            total_tokens=total_tokens_used,
        )
        return "搜尋程序達到步數上限，已為您整理目前的搜尋結果。"
