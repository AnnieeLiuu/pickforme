import asyncio
import json
import logging
import re
from typing import Any, Optional

import httpx
from src.config import settings

logger = logging.getLogger(__name__)

GEMINI_OPENAI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
GROQ_OPENAI_URL = "https://api.groq.com/openai/v1/chat/completions"


class LLMClient:
    """統一 LLM 通訊客戶端：負責多級模型降級容錯、動態 429 退避與 JSON/文字輸出"""

    def __init__(self) -> None:
        pass

    async def _is_ollama_online(self) -> bool:
        """發送 0.5s 極速 Ping 探測本機 Ollama 服務是否啟動"""
        if not getattr(settings, "OLLAMA_ENABLED", True):
            return False
        base_url = getattr(settings, "OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=0.5) as client:
                resp = await client.get(f"{base_url}/api/tags")
                return resp.status_code == 200
        except Exception:
            return False

    async def _call_openai_compatible_api(
        self,
        url: str,
        api_key: str,
        model: str,
        messages: list[dict[str, Any]],
        response_format: Optional[dict[str, Any]] = None,
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
            "max_tokens": 800,
        }
        if response_format:
            body["response_format"] = response_format

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=body, headers=headers)
            if resp.status_code == 429:
                retry_after_str = resp.headers.get("retry-after")
                wait_time = 3.0
                if retry_after_str:
                    try:
                        wait_time = max(float(retry_after_str), 3.0)
                    except ValueError:
                        pass
                logger.warning(f"LLM API ({model}) 觸發 Rate Limit (429)，動態等待 {wait_time} 秒後重試...")
                await asyncio.sleep(wait_time)
                resp = await client.post(url, json=body, headers=headers)

            if resp.status_code >= 400:
                logger.error(f"LLM API Error ({model}) HTTP {resp.status_code}: {resp.text[:500]}")
                resp.raise_for_status()

            data = resp.json()
            choice = data.get("choices", [{}])[0]
            message_obj = choice.get("message", {})
            usage = data.get("usage", {})
            total_tokens = usage.get("total_tokens", 0)

            content_str = (message_obj.get("content") or "").strip()
            if not content_str:
                raise ValueError(f"Model {model} returned empty content")

            return message_obj, total_tokens

    async def _try_groq(
        self,
        messages: list[dict[str, Any]],
        errors: list[str],
        response_format: Optional[dict[str, Any]] = None,
    ) -> Optional[tuple[str, str, int]]:
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
                    response_format=response_format,
                    timeout=30.0,
                )
                return str(msg_resp.get("content", "")), fallback_model, tokens
            except Exception as e:
                logger.warning(f"Groq model {fallback_model} call failed: {e}")
                errors.append(f"Groq ({fallback_model}): {e}")
        return None

    async def _try_gemini(
        self,
        messages: list[dict[str, Any]],
        errors: list[str],
        response_format: Optional[dict[str, Any]] = None,
    ) -> Optional[tuple[str, str, int]]:
        """嘗試使用 Google Gemini Flash 雲端主力"""
        if not settings.GEMINI_API_KEY:
            return None
        try:
            msg_resp, tokens = await self._call_openai_compatible_api(
                url=GEMINI_OPENAI_URL,
                api_key=settings.GEMINI_API_KEY,
                model="gemini-3.8-flash",
                messages=messages,
                response_format=response_format,
                timeout=30.0,
            )
            return str(msg_resp.get("content", "")), "gemini-3.8-flash", tokens
        except Exception as e:
            logger.warning(f"Primary Gemini Flash LLM call failed: {e}. Attempting failover...")
            errors.append(f"Gemini: {e}")
        return None

    async def _try_ollama(
        self,
        messages: list[dict[str, Any]],
        errors: list[str],
        response_format: Optional[dict[str, Any]] = None,
    ) -> Optional[tuple[str, str, int]]:
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
                response_format=response_format,
                timeout=90.0,
            )
            logger.info(f"Successfully processed by local Ollama ({settings.OLLAMA_MODEL})")
            return str(msg_resp.get("content", "")), f"ollama ({settings.OLLAMA_MODEL})", tokens
        except Exception as e:
            logger.warning(f"Ollama local LLM call failed: {e}. Falling back to other providers...")
            errors.append(f"Ollama ({settings.OLLAMA_MODEL}): {e}")
        return None

    async def call_text(
        self,
        messages: list[dict[str, Any]],
        response_format: Optional[dict[str, Any]] = None,
    ) -> tuple[str, str, int]:
        """純文字問答呼叫 (回傳 content, model_used, tokens)"""
        errors: list[str] = []
        primary = getattr(settings, "LLM_PRIMARY_PROVIDER", "groq").lower()

        if primary == "groq":
            pipeline = [self._try_groq, self._try_gemini, self._try_ollama]
        elif primary == "ollama":
            pipeline = [self._try_ollama, self._try_gemini, self._try_groq]
        else:  # "gemini" or default
            pipeline = [self._try_gemini, self._try_groq, self._try_ollama]

        for try_fn in pipeline:
            result = await try_fn(messages, errors, response_format=response_format)
            if result is not None:
                return result

        raise RuntimeError(f"All LLM endpoints failed. Errors: {'; '.join(errors)}")

    async def call_structured_json(
        self,
        messages: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], str, int]:
        """結構化 JSON 輸出呼叫 (保證解析為 dict，回傳 parsed_json, model_used, tokens)"""
        try:
            # 優先嘗試強制 JSON 物件模式 (OpenAI/Groq compatible)
            content, model, tokens = await self.call_text(messages, response_format={"type": "json_object"})
        except Exception:
            # 若後端模型不支援 response_format 參數，降級為純文字解析
            content, model, tokens = await self.call_text(messages, response_format=None)

        # 嘗試從內容中解析 JSON (容許 Markdown ```json ``` 包覆)
        cleaned = content.strip()
        if "```" in cleaned:
            # 提取 code block 內的內容
            parts = cleaned.split("```")
            for part in parts:
                part = part.strip()
                if part.startswith("json"):
                    part = part[4:].strip()
                if part.startswith("{") and part.endswith("}"):
                    cleaned = part
                    break

        # 搜尋最外層的 { }
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            cleaned = cleaned[start : end + 1]

        try:
            data = json.loads(cleaned)
            if isinstance(data, dict):
                return data, model, tokens
        except json.JSONDecodeError as e:
            logger.warning(f"Could not parse LLM JSON response: {e}. Content: {content[:200]}")

        # 若解析失敗，判定是否為純問候/打招呼（Chitchat）
        # 反向原則：在日本美食導航 Bot 中，除純打招呼/禮貌問候外，任何實質文字輸入一律安全保底為 search，絕不窮舉食物名稱
        last_user_msg = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last_user_msg = str(m.get("content") or "").strip()
                break

        greeting_kw = r"(?:你好|您好|嗨|早安|午安|晚安|哈囉|hello|hi|hey|你是誰|謝謝|多謝|掰掰|再見)"
        action_kw = r"(?:吃|去|找|想|有|推薦|餐廳|美食|店|壽司|拉麵|咖喱|咖哩|燒肉)"
        is_pure_greeting = (
            bool(re.search(greeting_kw, last_user_msg, flags=re.IGNORECASE))
            and len(last_user_msg) <= 12
            and not re.search(action_kw, last_user_msg)
        )
        if is_pure_greeting:
            logger.info("JSON 解析失敗，使用者為純問候語，安全降級為 chitchat 意圖")
            return {"intent": "chitchat", "location": None, "cuisine": None}, model, tokens

        # 其餘任何實質提問/搜尋，安全保底為 search
        logger.info(f"JSON 解析失敗，使用者非純問候語 ('{last_user_msg[:30]}')，安全保底為 search 意圖")
        return {"intent": "search", "location": None, "cuisine": None}, model, tokens
