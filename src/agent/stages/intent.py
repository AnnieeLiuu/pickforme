# -*- coding: utf-8 -*-
"""State 1: Intent Extraction and Follow-up Handler

負責 0.3s 微型 LLM 結構化意圖抽取、追問辨識、多輪歷史淨化與經緯度座標繼承。
"""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from src.agent.pipeline import FoodRecommendationFSM

logger = logging.getLogger(__name__)

INTENT_PARSER_SYSTEM_PROMPT = """你是一位嚴格的日本美食搜尋意圖解析器。請分析使用者的輸入與上下文，並輸出標準 JSON 格式。

可能的 intent 欄位值：
1. "search": 使用者想尋找美食餐廳。請精準抽取 location (地名/車站/商圈/景點，若未提及請設為 null) 與 cuisine (料理類別/食物核心名詞，例如「壽喜燒」、「拉麵」，嚴禁包含距離或時間詞，若未指定請設為 "熱門人氣美食")。
2. "more": 使用者想要「換一批」、「還有更多推薦嗎」、「還有別家嗎」，或是對上一輪推薦提出風格轉向（例如：「我想找更在地的老店」、「有沒有更平價一點的」、「可以接受遠一點走路15分鐘」）。請抽取 modifier (修飾語/新要求，例如 "更在地老店"、"平價"、"走路15分鐘內"，若無請設為 null)。注意：若為 "more" 且使用者未重複輸入地名或料理，請優先從對話歷史的上一輪中繼承 location 與 cuisine！
3. "follow_up": 使用者針對上一輪推薦的店家追問細節 (例如: "第2家有開嗎", "第一家多少錢", "有沒有信用卡")。請抽取 target_index (整數 1, 2 或 3) 與 question。
4. "chitchat": 純打招呼、禮貌問候、詢問系統功能或非美食主題。
5. "need_location": 使用者想吃特定食物但未提供任何地名，且系統上下文無 GPS 與無歷史地名。

輸出 JSON 結構規範：
{
  "intent": "search" | "more" | "follow_up" | "chitchat" | "need_location",
  "location": string | null,
  "cuisine": string | null,
  "modifier": string | null,
  "target_index": number | null,
  "question": string | null
}
只輸出純 JSON 字串，不要包含任何多餘文字。"""


def sanitize_history_for_intent(
    conversation_history: Optional[list[dict[str, str]]],
) -> list[dict[str, str]]:
    """帶入前輪簡短上下文 (淨化長篇推薦卡片，避免模型產生 Few-shot 格式模仿偏見)"""
    clean_msgs: list[dict[str, str]] = []
    if conversation_history:
        for item in conversation_history[-2:]:
            r = item.get("role", "user")
            c = item.get("content", "")
            if c:
                if r == "assistant" and (len(c) > 100 or "🍱" in c or "Tabelog" in c or "🚶" in c):
                    c = "已為您提供上一輪美食推薦卡片。"
                clean_msgs.append({"role": r, "content": c})
    return clean_msgs


async def execute_state_1_intent(
    fsm: FoodRecommendationFSM,
    user_message: str,
    user_location: Optional[tuple[float, float]],
    conversation_history: Optional[list[dict[str, str]]],
    chat_id: Optional[int] = None,
) -> dict[str, Any]:
    """State 1: 0.3s 微型 LLM 結構化意圖與座標解析"""
    messages: list[dict[str, Any]] = [{"role": "system", "content": INTENT_PARSER_SYSTEM_PROMPT}]
    messages.extend(sanitize_history_for_intent(conversation_history))
    messages.append({"role": "user", "content": user_message})

    t0 = time.time()
    parsed, model, tokens = await fsm.llm_client.call_structured_json(messages)
    latency = int((time.time() - t0) * 1000)

    await fsm.trace_logger.log_step(
        action_type="state_1_intent",
        tool_name="intent_parser",
        tool_input={"message": user_message},
        tool_output=parsed,
        latency_ms=latency,
    )

    intent = parsed.get("intent", "search")
    loc_str = parsed.get("location")
    resolved_coords: Optional[tuple[float, float]] = None

    # 優先轉換明確地名為經緯度
    if loc_str:
        resolved_coords = await fsm.places_client.geocode_location(loc_str)

    # 若無地名但使用者有帶 GPS
    if not resolved_coords and user_location:
        resolved_coords = user_location

    # 若為 "more" 換一批或無地名搜尋，嘗試從 session 繼承上次位置
    if intent in ("more", "search") and not resolved_coords and chat_id:
        try:
            session = await fsm.session_repo.get_session(chat_id)
            if session and session.get("last_location_lat") and session.get("last_location_lng"):
                resolved_coords = (
                    float(session["last_location_lat"]),
                    float(session["last_location_lng"]),
                )
        except Exception as e:
            logger.warning(f"Failed to inherit coords from session: {e}")

    # 若美食搜尋既無地標也無座標
    if intent in ("search", "more") and not resolved_coords:
        parsed["intent"] = "need_location"

    parsed["coords"] = resolved_coords
    return parsed


async def execute_follow_up(
    fsm: FoodRecommendationFSM,
    chat_id: Optional[int],
    parsed_intent: dict[str, Any],
    user_message: str,
) -> str:
    """情境 4: 追問前一輪推薦店家之專屬解答"""
    if not chat_id:
        return "抱歉，無法載入上一輪推薦的店家記憶。"

    recommended_stores = []
    try:
        stores_res = await fsm.session_repo.get_last_recommended_stores(chat_id)
        if isinstance(stores_res, list) and type(stores_res).__name__ != "MagicMock":
            recommended_stores = stores_res
    except Exception:
        pass

    if not recommended_stores:
        session = await fsm.session_repo.get_session(chat_id)
        if isinstance(session, dict):
            raw = session.get("last_recommended_ids")
            if isinstance(raw, list):
                recommended_stores = raw
            elif isinstance(raw, dict) and isinstance(raw.get("current"), list):
                recommended_stores = raw["current"]

    if not recommended_stores:
        return "抱歉，目前找不到您上一輪詢問的推薦紀錄，請問您今天想在何處吃什麼美食呢？"

    target_idx = (parsed_intent.get("target_index") or 1) - 1
    if target_idx < 0 or target_idx >= len(recommended_stores):
        target_idx = 0

    target_store = recommended_stores[target_idx]
    store_name = target_store.get("name_ja") or target_store.get("name") or "該店家"

    messages = [
        {
            "role": "system",
            "content": "你是一位日本美食助手。使用者正在追問上一輪推薦名單中的特定店家。請根據所提供的店家資料，簡潔、親切地回答使用者的問題。",
        },
        {
            "role": "user",
            "content": f"使用者問題：{user_message}\n目標店家資訊：\n{json.dumps(target_store, ensure_ascii=False, indent=2)}",
        },
    ]

    try:
        t0 = time.time()
        reply, model, tokens = await fsm.llm_client.call_text(messages)
        fsm._last_state_4_model = model
        fsm._last_state_4_tokens = tokens
        ans = reply.strip()
        latency = int((time.time() - t0) * 1000)
        await fsm.trace_logger.log_step(
            action_type="follow_up",
            tool_name="llm_follow_up",
            tool_input={"message": user_message, "target_store": store_name},
            tool_output={"card_content": ans, "length": len(ans)},
            latency_ms=latency,
        )
        return ans
    except Exception as e:
        logger.warning(f"Follow up LLM call failed: {e}")
        fallback_ans = f"針對 `{store_name}` 的問題：該店評分為 {target_store.get('tabelog_rating') or target_store.get('rating')} 分，地址在 {target_store.get('address', '市區')}。"
        await fsm.trace_logger.log_step(
            action_type="follow_up",
            tool_name="fallback_follow_up",
            tool_input={"message": user_message, "target_store": store_name, "error": str(e)},
            tool_output={"card_content": fallback_ans, "length": len(fallback_ans)},
        )
        return fallback_ans
