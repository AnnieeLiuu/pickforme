import asyncio
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

    async def _is_ollama_online(self) -> bool:
        """發送 0.5s 極速 Ping 探測本機 Ollama 服務是否啟動」"""
        if not getattr(settings, "OLLAMA_ENABLED", True):
            return False
        base_url = getattr(settings, "OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=0.5) as client:
                resp = await client.get(f"{base_url}/api/tags")
                return resp.status_code == 200
        except Exception:
            return False

    async def _try_groq(
        self, messages: list[dict[str, Any]], errors: list[str]
    ) -> Optional[tuple[dict[str, Any], str, int]]:
        """嘗試使用雲端 Groq 多模型"""
        if not settings.GROQ_API_KEY:
            return None
        for fallback_model in ["qwen/qwen3.8-27b", "openai/gpt-oss-120b", "openai/gpt-oss-20b"]:
            try:
                msg_resp, tokens = await self._call_openai_compatible_api(
                    url=GROQ_OPENAI_URL,
                    api_key=settings.GROQ_API_KEY,
                    model=fallback_model,
                    messages=messages,
                    tools=TOOL_SCHEMAS,
                    timeout=30.0,
                )
                return msg_resp, fallback_model, tokens
            except Exception as e:
                logger.warning(f"Groq model {fallback_model} call failed: {e}")
                errors.append(f"Groq ({fallback_model}): {e}")
        return None

    async def _try_gemini(
        self, messages: list[dict[str, Any]], errors: list[str]
    ) -> Optional[tuple[dict[str, Any], str, int]]:
        """嘗試使用 Google Gemini Flash 雲端主力"""
        if not settings.GEMINI_API_KEY:
            return None
        try:
            msg_resp, tokens = await self._call_openai_compatible_api(
                url=GEMINI_OPENAI_URL,
                api_key=settings.GEMINI_API_KEY,
                model="gemini-3.8-flash",
                messages=messages,
                tools=TOOL_SCHEMAS,
                timeout=30.0,
            )
            return msg_resp, "gemini-3.8-flash", tokens
        except Exception as e:
            logger.warning(f"Primary Gemini Flash LLM call failed: {e}. Attempting failover...")
            errors.append(f"Gemini: {e}")
        return None

    async def _try_ollama(
        self, messages: list[dict[str, Any]], errors: list[str]
    ) -> Optional[tuple[dict[str, Any], str, int]]:
        """嘗試使用本機 Ollama 離線模型"""
        if not await self._is_ollama_online():
            return None
        try:
            ollama_url = f"{settings.OLLAMA_BASE_URL.rstrip('/')}/v1/chat/completions"
            msg_resp, tokens = await self._call_openai_compatible_api(
                url=ollama_url,
                api_key="ollama",
                model=settings.OLLAMA_MODEL,
                messages=messages,
                tools=TOOL_SCHEMAS,
                timeout=90.0,
            )
            logger.info(f"Successfully processed by local Ollama ({settings.OLLAMA_MODEL})")
            return msg_resp, f"ollama ({settings.OLLAMA_MODEL})", tokens
        except Exception as e:
            logger.warning(f"Ollama local LLM call failed: {e}. Falling back to other providers...")
            errors.append(f"Ollama ({settings.OLLAMA_MODEL}): {e}")
        return None

    async def call_llm_with_failover(self, messages: list[dict[str, Any]]) -> tuple[dict[str, Any], str, int]:
        """呼叫 LLM (依據 LLM_PRIMARY_PROVIDER 設定排列優先序，自動多級降級容錯)"""
        errors: list[str] = []
        primary = getattr(settings, "LLM_PRIMARY_PROVIDER", "groq").lower()

        if primary == "groq":
            pipeline = [self._try_groq, self._try_gemini, self._try_ollama]
        elif primary == "ollama":
            pipeline = [self._try_ollama, self._try_gemini, self._try_groq]
        else:  # "gemini" or default
            pipeline = [self._try_gemini, self._try_groq, self._try_ollama]

        for try_fn in pipeline:
            result = await try_fn(messages, errors)
            if result is not None:
                return result

        raise RuntimeError(f"All LLM endpoints failed. Errors: {'; '.join(errors)}")

    async def _call_openai_compatible_api(
        self,
        url: str,
        api_key: str,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        timeout: float = 60.0,
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

        async with httpx.AsyncClient(timeout=timeout) as client:
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

            # 防禦空回應：若模型既無 content 亦無 tool_calls，視為無效回應觸發 Failover
            content_str = (message_obj.get("content") or "").strip()
            tool_calls = message_obj.get("tool_calls")
            if not content_str and not tool_calls:
                raise ValueError(f"Model {model} returned empty response (no content and no tool_calls)")

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
        tools_executed: list[str] = []

        for step in range(1, self.max_steps + 1):
            if on_progress:
                if step == 1:
                    await on_progress("🧭 正在理解您的美食需求與分析所在座標...")
                elif "get_tabelog_info" in tools_executed:
                    await on_progress("✍️ 正在綜整日本食べログ真實評價與指標，為您生成專屬美食推薦卡片...")
                elif "search_nearby" in tools_executed:
                    await on_progress("🔍 已取得周邊候選店家，正在挑選指標餐廳準備查詢食べログ口碑...")
                elif "geocode_location" in tools_executed:
                    await on_progress("📍 已鎖定目標地理座標，正在準備掃描周邊人氣美食...")
                else:
                    await on_progress(f"🧠 正在深度思考下一步最佳推薦策略 (第 {step}/{self.max_steps} 步)...")

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
                raw_content = message_obj.get("content") or ""
                final_answer = str(raw_content).strip() or "抱歉，目前無法處理您的請求，請稍後再試。"
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
                if not isinstance(tool_args, dict):
                    tool_args = {}

                # 階段性進度通知
                if on_progress:
                    if tool_name == "geocode_location":
                        addr = tool_args.get("address") or tool_args.get("query") or "目標地點"
                        await on_progress(f"📍 正在解析「{addr}」的精確地理座標...")
                    elif tool_name == "search_nearby":
                        kw = tool_args.get("keyword") or "美食"
                        await on_progress(f"📡 正在開啟地圖雷達，檢索周邊「{kw}」高評價餐廳清單...")
                    elif tool_name == "get_tabelog_info":
                        s_name = tool_args.get("store_name") or "店家"
                        await on_progress(f"🇯🇵 正在連線日本食べログ (Tabelog) 查閱「{s_name}」老饕評分與獲獎紀錄...")
                    elif tool_name == "extract_user_preferences":
                        await on_progress("🎯 正在提取您的用餐預算、口味偏好與限制條件...")
                    else:
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
                tools_executed.append(tool_name)
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
