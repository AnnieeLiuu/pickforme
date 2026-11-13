import asyncio
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from src.agent.llm_client import LLMClient
from src.agent.pipeline import FoodRecommendationFSM
from src.agent.schemas import AreaDisambiguationOutput
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

    async def disambiguate_area(self, area: str) -> tuple[bool, Optional[str], list[dict[str, str]]]:
        """判定區域是否為廣域都道府縣、大型海島/地區或缺乏單一 800m 檢索中心之模糊概念。

        若過於模糊，動態回傳 2~3 個具代表性的子商圈建議選項；若發生例外或超時，安全降級 (fail-open) 回傳 (False, area, [])。
        """
        if not area or not area.strip():
            return False, area, []

        cleaned_area = area.strip()
        system_msg = {
            "role": "system",
            "content": (
                "你是一位精通日本地理與美食的專家。請判斷使用者輸入的地點/區域是否為廣域都道府縣、大型海島/地區、或缺乏單一 800m 檢索中心點之模糊概念（例如：靜岡溫泉街、北海道、長野滑雪、九州、關西）。\n\n"
                "【判定標準】\n"
                "1. 若地點為具體車站、單一商圈、明確小鎮或特定景點（例如：箱根湯本、新宿歌舞伎町、熱海車站、淺草雷門、道頓堀），請設 is_location_ambiguous=false，suggested_sub_areas=[]。\n"
                "2. 若地點為整片都道府縣、大島、跨多城鎮之溫泉鄉/滑雪場概念等廣域地點，請設 is_location_ambiguous=true，並在 suggested_sub_areas 提供 2~3 個最具代表性且具備明確 800m 檢索中心（車站或核心街區）的候選點，每個選項包含 name（子商圈名）、desc（特色料理亮點簡述）、search_loc（具體檢索地標如「札幌車站」）。\n\n"
                "輸出必須為符合規範的 JSON 物件。"
            ),
        }
        user_msg = {
            "role": "user",
            "content": f"請評估以下日本地點是否需要消歧義商圈：\n地點：{cleaned_area}",
        }

        try:
            coro = self.llm_client.call_structured_json(
                messages=[system_msg, user_msg],
                schema=AreaDisambiguationOutput,
                max_retries=1,
            )
            parsed_data, _, _ = await asyncio.wait_for(coro, timeout=2.0)
            is_ambig = bool(parsed_data.get("is_location_ambiguous", False))
            raw_options = parsed_data.get("suggested_sub_areas") or []
            options_dicts: list[dict[str, str]] = []
            for opt in raw_options:
                if isinstance(opt, dict) and opt.get("name"):
                    name = str(opt["name"]).strip()
                    desc = str(opt.get("desc") or "").strip()
                    search_loc = str(opt.get("search_loc") or name).strip()
                    options_dicts.append({"name": name, "desc": desc, "search_loc": search_loc})
            return is_ambig, cleaned_area, options_dicts
        except Exception as e:
            logger.warning(f"Area disambiguation failed or timed out for '{area}', falling open: {e}")
            return False, area, []
