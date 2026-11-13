# -*- coding: utf-8 -*-
"""State 1: Intent Extraction and Follow-up Handler

負責 0.3s 微型 LLM 結構化意圖抽取、追問辨識、多輪歷史淨化與經緯度座標繼承。
"""

from __future__ import annotations

import inspect
import json
import logging
import re
import time
from typing import TYPE_CHECKING, Any, Optional

from src.agent.gate import QuickPassGate
from src.agent.schemas import IntentOutput, extract_preferences_from_text

if TYPE_CHECKING:
    from src.agent.pipeline import FoodRecommendationFSM

logger = logging.getLogger(__name__)


def is_prefecture_level_area(
    location: Optional[str],
) -> tuple[bool, Optional[str], Optional[list[dict[str, str]]]]:
    """判斷地點是否為超大縣級區域且缺乏具體市/車站，向後相容輔助函式"""
    if not location:
        return False, None, None
    clean = location.strip()
    # 若已有具體車站、市、區、町、條、丁目等下級或站點後綴，視為具體地點不反問
    if any(clean.endswith(s) for s in ("站", "駅", "Station", "市", "區", "区", "町", "村", "丁目", "通", "通り")):
        return False, None, None

    # 剝離「縣/県/府」一級行政區後綴 (北海道為固定固有詞不剝離)
    stripped = re.sub(r"[縣県府]$", "", clean).strip()
    if clean.endswith(("縣", "県", "府")) or stripped in ("北海道", "沖繩", "沖縄"):
        return True, stripped, None

    return False, None, None


def generate_disambiguation_prompt(loc_str: str, options: list[Any]) -> str:
    """生成廣域或模糊地點動態引導文案"""
    lines = [
        f"🍱 **{loc_str}幅員遼闊或包含多個獨立聚落，建議先鎖定具體區域搜尋！**\n",
        "請問您目前位在哪個區域或靠近哪座車站呢？",
    ]
    for idx, opt in enumerate(options, 1):
        name = opt.name if hasattr(opt, "name") else (opt.get("name", "") if isinstance(opt, dict) else str(opt))
        desc = opt.desc if hasattr(opt, "desc") else (opt.get("desc", "") if isinstance(opt, dict) else "")
        if desc:
            lines.append(f"{idx}️⃣ **{name}**（{desc}）")
        else:
            lines.append(f"{idx}️⃣ **{name}**")
    lines.append("\n💡 您可以直接回覆數字（如「1」或「2」），或告訴我具體車站/城鎮名！")
    return "\n".join(lines)


def generate_prefecture_disambiguation_prompt(pref: str, cuisine: Optional[str], options: list[dict[str, str]]) -> str:
    """生成縣級大區主動反問導引文案（向後相容包裝）"""
    return generate_disambiguation_prompt(pref, options)


INTENT_PARSER_SYSTEM_PROMPT = f"""你是一位嚴格的日本美食搜尋意圖解析器。請分析使用者的輸入與上下文，並輸出標準 JSON 格式。

可能的 intent 欄位值：
1. "search": 使用者想尋找美食餐廳、特定飲品專門店或食品伴手禮店。請精準抽取 location (地名/車站/商圈/景點，若未提及請設為 null) 與 cuisine (料理類別/食物核心名詞，例如「壽喜燒」、「拉麵」、「抹茶甜點」，嚴禁包含距離或時間詞，若未指定請設為 "熱門人氣美食")。
2. "explore_cuisine": 使用者詢問某個日本地區「有什麼特色美食」、「有什麼喝的/名茶/地酒」、「必買什麼伴手禮/名菓」等開放式文化探索問題（尚未指定具體店家）。請抽取 location，並將 explore_category 標記為 "food"(美食料理)、"drink"(飲品/茶/酒/喫茶) 或 "souvenir"(伴手禮/名產/名菓)。
3. "more": 使用者想要「換一批」、「還有更多推薦嗎」、「還有別家嗎」，或是對上一輪推薦提出風格轉向（例如：「我想找更在地的老店」、「有沒有更平價一點的」、「可以接受遠一點走路15分鐘」）。請抽取 modifier (修飾語/新要求，例如 "更在地老店"、"平價"、"走路15分鐘內"，若無請設為 null)。注意：若為 "more" 且使用者未重複輸入地名或料理，請優先從對話歷史的上一輪中繼承 location 與 cuisine！
4. "follow_up": 使用者針對上一輪推薦的店家追問細節 (例如: "第2家有開嗎", "第一家多少錢", "有沒有信用卡")。請抽取 target_index (整數 1, 2 或 3) 與 question。
5. "chitchat": 純打招呼、禮貌問候或詢問系統自身功能。
6. "need_location": 使用者想吃特定食物但未提供任何地名，且系統上下文無 GPS 與無歷史地名。
7. "non_food": 使用者詢問或尋找「非餐飲/非美食/非食品伴手禮」的實體商品購物或生活服務（例如：眼鏡/配鏡、服飾/球鞋、藥妝、電器/相機、剪髮/理髮沙龍、純飯店住宿或景點門票）。請抽取 non_food_item (例如「眼鏡」、「藥妝」、「球鞋」)。
   邊界原則：
   - 凡指涉食品、甜點、糕點、飲品或地酒名產之伴手禮（如「抹茶甜點伴手禮」、「生八橋伴手禮」），屬於 "explore_cuisine" 或 "search"，嚴禁判為 non_food。
   - 美食主題景點（如「新橫濱拉麵博物館」、「啤酒博物館」、「築地市場」）屬於 "search" 或 "explore_cuisine"，嚴禁判為 non_food。

地點模糊度判定規則 (is_location_ambiguous)：
- 若 location 過於廣域（如都道府縣「北海道」、「長野」、「靜岡」；整座大島/大區如「九州」、「四國」；或缺乏單一 800m 徒步檢索中心點之跨多市鎮廣域名詞如「靜岡溫泉街」、「長野滑雪」、「東京老街」），請將 is_location_ambiguous 設為 true，並於 ambiguity_reason 簡述原因，同時於 suggested_sub_areas 提供 2~3 個最具代表性、各具特色的具體子商圈或城鎮名（含 name、desc、search_loc）。
- 若為單一具體車站、街區、商圈或明確景點地標（例如「箱根湯本」、「熱海車站」、「新宿歌舞伎町」、「淺草雷門」），請將 is_location_ambiguous 設為 false，且 suggested_sub_areas 設為 []。

輸出 JSON 結構規範（由 IntentOutput Schema 自動生成）：
{IntentOutput.build_json_schema_prompt()}
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
    """State 1: 0ms QuickPassGate 入口快篩 + 0.3s 微型 LLM 結構化意圖與座標解析"""
    gate_res = QuickPassGate().evaluate(user_message)
    if gate_res.matched:
        if gate_res.fast_reply is not None:
            await fsm.trace_logger.log_step(
                action_type="state_1_intent",
                tool_name="quick_pass_gate",
                tool_input={"message": user_message, "rule": gate_res.rule_name},
                tool_output={"intent": "fast_reply", "fast_reply": gate_res.fast_reply},
                latency_ms=0,
            )
            return {
                "intent": "fast_reply",
                "fast_reply": gate_res.fast_reply,
                "rule_name": gate_res.rule_name,
            }
        parsed = dict(gate_res.parsed_intent or {})
        await fsm.trace_logger.log_step(
            action_type="state_1_intent",
            tool_name="quick_pass_gate",
            tool_input={"message": user_message, "rule": gate_res.rule_name},
            tool_output=parsed,
            latency_ms=0,
        )
    else:
        messages: list[dict[str, Any]] = [{"role": "system", "content": INTENT_PARSER_SYSTEM_PROMPT}]
        messages.extend(sanitize_history_for_intent(conversation_history))
        messages.append({"role": "user", "content": user_message})

        t0 = time.time()
        parsed_raw, model, tokens = await fsm.llm_client.call_structured_json(messages, schema=IntentOutput)
        parsed = dict(parsed_raw)
        latency = int((time.time() - t0) * 1000)

        await fsm.trace_logger.log_step(
            action_type="state_1_intent",
            tool_name="intent_parser",
            tool_input={"message": user_message},
            tool_output=parsed,
            latency_ms=latency,
        )

    # 確定性與 LLM 雙軌抽取長期飲食禁忌與支付偏好並持久化
    if chat_id:
        extracted_prefs = extract_preferences_from_text(user_message)
        dietary_set = list(
            dict.fromkeys(
                (extracted_prefs.get("dietary_restrictions") or []) + (parsed.get("dietary_restrictions") or [])
            )
        )
        payment_set = list(
            dict.fromkeys(
                (extracted_prefs.get("payment_preferences") or []) + (parsed.get("payment_preferences") or [])
            )
        )
        if dietary_set or payment_set:
            try:
                upd_fn = getattr(fsm.session_repo, "update_long_term_preferences", None)
                if callable(upd_fn):
                    res = upd_fn(
                        chat_id,
                        dietary_restrictions=dietary_set,
                        payment_preferences=payment_set,
                    )
                    if inspect.isawaitable(res):
                        await res
            except Exception as e:
                logger.warning(f"Failed to update long term preferences: {e}")

    intent = parsed.get("intent", "search")

    # 處理「特色美食導覽」或「縣級分區反問」後的下一輪編號點選 (specialty_index: 1/2/3) 或「直接推薦」(direct_recommend)
    if parsed.get("specialty_index") is not None:
        idx_1based = int(parsed["specialty_index"])
        wm_sel: dict[str, Any] = {}
        if chat_id:
            try:
                wm_fn = getattr(fsm.session_repo, "get_working_memory", None)
                if callable(wm_fn):
                    wm_res = wm_fn(chat_id)
                    wm_val = await wm_res if inspect.isawaitable(wm_res) else wm_res
                    if isinstance(wm_val, dict):
                        wm_sel = wm_val
            except Exception as e:
                logger.warning(f"Failed to resolve specialty selection from working memory: {e}")

        # 雙軌感知 1：優先檢查是否為「縣級超大區域分區選單 (suggested_locations)」(策略 A)
        suggested_locs = wm_sel.get("suggested_locations")
        if isinstance(suggested_locs, list) and 0 <= idx_1based - 1 < len(suggested_locs):
            target_loc = suggested_locs[idx_1based - 1]
            parsed["location"] = str(target_loc)
            parsed["cuisine"] = wm_sel.get("pending_cuisine") or "熱門人氣美食"
            parsed["intent"] = "search"
        # 雙軌感知 2：次之檢查是否為「特色料理導覽選單 (suggested_cuisines)」
        else:
            suggested = wm_sel.get("suggested_cuisines")
            if isinstance(suggested, list) and 0 <= idx_1based - 1 < len(suggested):
                parsed["cuisine"] = str(suggested[idx_1based - 1])
                if not parsed.get("location") and wm_sel.get("last_location"):
                    parsed["location"] = wm_sel["last_location"]
                if wm_sel.get("last_explore_category"):
                    parsed["explore_category"] = wm_sel["last_explore_category"]
            else:
                # 無有效導覽選單 (未曾導覽或已被一般搜尋消費)：視為針對上一輪推薦店家第 N 家之追問
                parsed.pop("specialty_index", None)
                parsed["intent"] = "follow_up"
                parsed["target_index"] = idx_1based
                parsed["question"] = user_message
                parsed["coords"] = None
                return parsed
    elif chat_id and parsed.get("direct_recommend"):
        try:
            wm_fn = getattr(fsm.session_repo, "get_working_memory", None)
            if callable(wm_fn):
                wm_res = wm_fn(chat_id)
                wm = await wm_res if inspect.isawaitable(wm_res) else wm_res
                if isinstance(wm, dict):
                    if not parsed.get("location") and wm.get("last_location"):
                        parsed["location"] = wm["last_location"]
                    last_cat = wm.get("last_explore_category") or "food"
                    parsed["explore_category"] = last_cat
                    if last_cat == "drink":
                        parsed["cuisine"] = "人気 カフェ 茶房 日本茶"
                    elif last_cat == "souvenir":
                        parsed["cuisine"] = "人気 お土産 和菓子 老舗"
                    else:
                        parsed["cuisine"] = "熱門人氣美食"
        except Exception as e:
            logger.warning(f"Failed to resolve direct recommend from working memory: {e}")
            parsed["cuisine"] = "熱門人氣美食"

    # 若上一輪為縣級反問選單，且使用者下一輪直接輸入城鎮名 (如「濱松」)，自動繼承暫存之 pending_cuisine
    if chat_id and parsed.get("location") and intent == "search":
        try:
            wm_fn = getattr(fsm.session_repo, "get_working_memory", None)
            if callable(wm_fn):
                wm_res = wm_fn(chat_id)
                wm_val = await wm_res if inspect.isawaitable(wm_res) else wm_res
                if isinstance(wm_val, dict) and wm_val.get("pending_cuisine"):
                    if not parsed.get("cuisine") or parsed.get("cuisine") in ("熱門人氣美食", "熱門美食"):
                        parsed["cuisine"] = wm_val["pending_cuisine"]
        except Exception as e:
            logger.debug(f"Failed to inherit pending_cuisine: {e}")

    # 若為 "explore_cuisine" 且未帶地名，嘗試從 Working Memory 繼承上一輪地名
    if intent == "explore_cuisine":
        # 地名防呆清洗 (防範 LLM 產生「有什麼/我想找」等口語殘留)
        if parsed.get("location"):
            from src.agent.gate import clean_explore_location

            parsed["location"] = clean_explore_location(parsed["location"])

        if not parsed.get("location") and chat_id:
            try:
                wm_fn = getattr(fsm.session_repo, "get_working_memory", None)
                if callable(wm_fn):
                    wm_res = wm_fn(chat_id)
                    wm = await wm_res if inspect.isawaitable(wm_res) else wm_res
                    if isinstance(wm, dict) and wm.get("last_location"):
                        parsed["location"] = wm["last_location"]
            except Exception as e:
                logger.warning(f"Failed to load working memory for explore_cuisine: {e}")

        if not parsed.get("explore_category"):
            parsed["explore_category"] = "food"

        if not parsed.get("location"):
            parsed["intent"] = "need_location"
        return parsed

    # 若為 "more" 且缺少料理或地名，優先從 Working Memory 繼承
    if intent == "more" and chat_id and (not parsed.get("cuisine") or not parsed.get("location")):
        try:
            wm_fn = getattr(fsm.session_repo, "get_working_memory", None)
            if callable(wm_fn):
                wm_res = wm_fn(chat_id)
                wm = await wm_res if inspect.isawaitable(wm_res) else wm_res
                if isinstance(wm, dict):
                    if not parsed.get("cuisine") and wm.get("last_cuisine"):
                        parsed["cuisine"] = wm["last_cuisine"]
                    if not parsed.get("location") and wm.get("last_location"):
                        parsed["location"] = wm["last_location"]
        except Exception as e:
            logger.warning(f"Failed to load working memory for more intent: {e}")

    # 若判定為非餐飲購物/生活服務 (non_food)，立即提早返回，杜絕 downstream geocoding 與 need_location 覆寫
    if intent == "non_food":
        item_hint = parsed.get("non_food_item") or "此類商品或生活服務"
        parsed["non_food_prompt"] = (
            f"🍱 抱歉！我是專門為您尋找日本美食與餐廳的特派員 PickForMe。\n\n"
            f"目前我僅專注於日本各地的**餐飲美食、咖啡甜點、特色茶飲/地酒與名產伴手禮**，暫無提供「{item_hint}」等非餐飲購物或生活服務的推薦喔！\n\n"
            f"如果您想品嚐當地的道地料理、排隊拉麵、燒肉或特色小吃，歡迎隨時告訴我地點與想吃的種類！"
        )
        setattr(fsm, "early_response", parsed["non_food_prompt"])
        return parsed

    loc_str = parsed.get("location")
    resolved_coords: Optional[tuple[float, float]] = parsed.get("coords")

    # 策略 A：動態廣域/多聚落模糊地點主動反問收斂 (由 LLM 世界知識判定，防範硬編碼映射)
    is_ambiguous = bool(parsed.get("is_location_ambiguous"))
    suggested_sub_areas = parsed.get("suggested_sub_areas") or []

    if intent in ("search", "more", "need_location") and loc_str and not resolved_coords:
        if is_ambiguous and len(suggested_sub_areas) >= 2:
            prompt_text = generate_disambiguation_prompt(loc_str, suggested_sub_areas)
            parsed["intent"] = "need_location"
            parsed["need_location_prompt"] = prompt_text
            suggested_loc_keys = []
            for opt in suggested_sub_areas:
                s_loc = getattr(opt, "search_loc", None) or (opt.get("search_loc") if isinstance(opt, dict) else None)
                s_name = getattr(opt, "name", None) or (opt.get("name") if isinstance(opt, dict) else str(opt))
                suggested_loc_keys.append(s_loc or s_name)
            parsed["suggested_locations"] = suggested_loc_keys
            parsed["pending_cuisine"] = parsed.get("cuisine")
            parsed["coords"] = None

            # 設置 early_response
            setattr(fsm, "early_response", prompt_text)

            # 更新 Session working memory
            if chat_id:
                try:
                    upd_fn = getattr(fsm.session_repo, "update_working_memory", None)
                    if callable(upd_fn):
                        res = upd_fn(
                            chat_id=chat_id,
                            suggested_locations=parsed["suggested_locations"],
                            pending_cuisine=parsed.get("pending_cuisine"),
                        )
                        if inspect.isawaitable(res):
                            await res
                except Exception as e:
                    logger.warning(f"Failed to persist disambiguation working memory: {e}")

            return parsed

    # 優先轉換明確地名為經緯度 (若尚未由 coords_search 提供座標)
    if not resolved_coords and loc_str:
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
