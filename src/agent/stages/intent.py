# -*- coding: utf-8 -*-
"""State 1: Intent Extraction and Follow-up Handler

負責 0.3s 微型 LLM 結構化意圖抽取、追問辨識、多輪歷史淨化與經緯度座標繼承。
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
import time
from typing import TYPE_CHECKING, Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field
from src.agent.gate import QuickPassGate
from src.agent.schemas import IntentOutput, extract_preferences_from_text
from src.agent.skill_loader import get_skill_loader
from src.agent.validator import validate_follow_up_response

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


class FollowUpDecision(BaseModel):
    """追問 Tool Loop 決策合約"""

    action: Literal["answer", "detail_lookup", "nearby_pairing"] = Field(
        default="answer",
        description="answer: 既有資料足夠直接回答 | detail_lookup: 需查評論向量庫或詳細快取 | nearby_pairing: 需查周邊400m續攤店家",
    )
    tool_query: Optional[str] = Field(
        default=None,
        description="呼叫 detail_lookup 或 nearby_pairing 時的檢索關鍵字（例如「素食 推車」或「咖啡廳 甜點」）",
    )


_COMPOUND_SCOPING_RE = re.compile(r"(?:長輩|老人|小孩|嬰兒|親子|不排隊|免排隊|清淡|素食|過敏|預算|包廂|約會|慶生|輪椅)")


def _has_compound_scoping_constraints(user_message: str, parsed: dict[str, Any]) -> bool:
    """判斷是否屬於帶有複合旅遊限制條件且需前期收斂的模糊查詢"""
    if _COMPOUND_SCOPING_RE.search(user_message or ""):
        return True
    modifier = str(parsed.get("modifier") or "")
    return bool(modifier and _COMPOUND_SCOPING_RE.search(modifier))


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
   行程停靠站規則：若句子包含從起點到終點之轉乘/停留用餐語句（例如「從東京去京都，在名古屋轉車吃鰻魚」或「搭新幹線去大阪，在名古屋中停」），請明確將用餐/中停地設為 location（如 "名古屋"），切勿誤設為出發地或目的地！
   時間約束抽取：若提及「宵夜/深夜/夜食」，請設 time_constraint="late_night"；若提及「早餐/早點/早午餐」，設 time_constraint="breakfast"；若提及「現在/營業中/現在有開嗎」，設 time_constraint="now"。
2. "explore_cuisine": 使用者詢問某個日本地區「有什麼特色美食」、「有什麼喝的/名茶/地酒」、「必買什麼伴手禮/名菓」等開放式文化探索問題（尚未指定具體店家）。請抽取 location，並將 explore_category 標記為 "food"(美食料理)、"drink"(飲品/茶/酒/喫茶) 或 "souvenir"(伴手禮/名產/名菓)。
3. "more": 使用者想要「換一批」、「還有更多推薦嗎」、「還有別家嗎」，或是對上一輪推薦提出風格轉向（例如：「我想找更在地的老店」、「有沒有更平價一點的」、「可以接受遠一點走路15分鐘」）。請抽取 modifier (修飾語/新要求，例如 "更在地老店"、"平價"、"走路15分鐘內"，若無請設為 null)。注意：若為 "more" 且使用者未重複輸入地名或料理，請優先從對話歷史的上一輪中繼承 location 與 cuisine！
4. "follow_up": 使用者針對上一輪推薦的店家追問細節 (例如: "第2家有開嗎", "第一家多少錢")，或是要求比較多個店家 (例如: "比較 1 跟 2", "1 跟 3 哪家比較好", "第一家跟第二家差在哪")。
   - 若為單店追問，請填入 target_index 與 target_indices=[target_index]。
   - 若為多店對比，請填入 target_indices（例如 [1, 2] 或 [1, 3]）與 question。
5. "chitchat": 純打招呼、禮貌問候或詢問系統自身功能。
6. "need_location": 使用者想吃特定食物但未提供任何地名，且系統上下文無 GPS 與無歷史地名。
7. "non_food": 使用者詢問或尋找「非餐飲/非美食/非食品伴手禮」的實體商品購物或生活服務（例如：眼鏡/配鏡、服飾/球鞋、藥妝、電器/相機、剪髮/理髮沙龍、純飯店住宿或景點門票）。請抽取 non_food_item (例如「眼鏡」、「藥妝」、「球鞋」)。
   邊界原則：
   - 凡指涉食品、甜點、糕點、飲品或地酒名產之伴手禮（如「抹茶甜點伴手禮」、「生八橋伴手禮」），屬於 "explore_cuisine" 或 "search"，嚴禁判為 non_food。
   - 美食主題景點（如「新橫濱拉麵博物館」、「啤酒博物館」、「築地市場」）屬於 "search" 或 "explore_cuisine"，嚴禁判為 non_food。
8. "out_of_region": 使用者想尋找的地點、城市或國家不在日本境內（例如：台北、首爾、香港、紐約、巴黎、倫敦、曼谷、上海、新加坡、台南、台中、高雄等非日本地區）。請抽取 location，並將 intent 設為 "out_of_region"（即使使用者想吃的料理是拉麵或壽司，只要目標地點非日本即屬此類）。
   邊界與消歧義原則：
   - 異國料理防誤殺：若料理名稱包含外國名詞（如「台灣牛肉麵」、「韓式烤肉」、「美式漢堡」、「泰式料理」），但目標地點位於日本境內（如「新宿 台灣料理」、「東京 韓式烤肉」）或未指名地點，屬於合法的日本美食搜尋，絕對嚴禁判定為 out_of_region，必須判為 "search"（如 cuisine="台灣料理", location="新宿"）。
   - 漢字同名地名消歧義：台日之間少數漢字同名地名（如「松山」、「板橋」、「三重」、「中山」），若問句無任何境外特徵詞（如「松山 鯛魚飯」、「板橋 拉麵」、「三重縣 燒肉」），預設視為日本境內地點（判定為 "search"）；只有在問句明確包含境外特徵詞（如「台北松山」、「新北板橋」、「捷運中山站」、「夜市」）時，才判為 "out_of_region"。

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

    # 若判定為非日本境外地點 (out_of_region)，立即提早返回，杜絕 downstream geocoding 與 need_location 覆寫
    if intent == "out_of_region":
        loc_hint = parsed.get("location") or "該地區"
        parsed["out_of_region_prompt"] = (
            f"🍱 抱歉！我是專注於**日本美食與特色餐廳**的特派員 PickForMe。\n\n"
            f"您查詢的「{loc_hint}」不在日本境內，目前我暫時無法提供當地的餐廳推薦喔！\n\n"
            f"💡 請告訴我你想在哪個**日本城市、車站或商圈**尋找美食（例如：東京、大阪、京都、福岡、札幌、沖繩）？"
        )
        setattr(fsm, "early_response", parsed["out_of_region_prompt"])
        return parsed

    loc_str = parsed.get("location")
    resolved_coords: Optional[tuple[float, float]] = parsed.get("coords")

    # 策略 A0：複合旅遊需求前期收斂 (Trip Scoping，優先於純廣域反問，最多 2 輪避免無限追問)
    is_ambiguous = bool(parsed.get("is_location_ambiguous"))
    suggested_sub_areas = parsed.get("suggested_sub_areas") or []
    needs_concrete_area = (not loc_str) or (is_ambiguous and len(suggested_sub_areas) >= 2)

    if (
        intent in ("search", "more", "need_location")
        and not resolved_coords
        and not user_location
        and needs_concrete_area
    ):
        if _has_compound_scoping_constraints(user_message, parsed):
            scoping_turns = 0
            if chat_id:
                try:
                    wm_fn = getattr(fsm.session_repo, "get_working_memory", None)
                    if callable(wm_fn):
                        wm_res = wm_fn(chat_id)
                        wm_val = await wm_res if inspect.isawaitable(wm_res) else wm_res
                        if isinstance(wm_val, dict) and isinstance(wm_val.get("trip_scoping"), dict):
                            scoping_turns = int(wm_val["trip_scoping"].get("scoping_turns", 0))
                except Exception as e:
                    logger.warning(f"Failed to read trip_scoping working memory: {e}")

            if scoping_turns < 2:
                scoping_skill = get_skill_loader().load_scenario(
                    "trip-scoping",
                    fallback="請簡潔親切地確認使用者具體想前往的日本車站或商圈，以及預算或飲食偏好（1～2 句）。",
                )
                scoping_prompt = (
                    f"🍱 了解您的同行與用餐需求！為了幫您精準鎖定最合適的名店，方便確認一下：\n"
                    f"1. 您主要會在**{loc_str or '日本'}哪個車站或商圈**一帶活動呢？（例如東京車站、淺草、新宿）\n"
                    f"2. 大概的**每人預算**大概抓多少呢？"
                )
                call_text = getattr(fsm.llm_client, "call_text", None)
                if callable(call_text):
                    try:
                        res = call_text(
                            [
                                {"role": "system", "content": scoping_skill},
                                {
                                    "role": "user",
                                    "content": f"使用者需求：{user_message}\n已抽取地點：{loc_str or '未指定'}",
                                },
                            ]
                        )
                        if inspect.isawaitable(res):
                            reply, _, _ = await asyncio.wait_for(res, timeout=8.0)
                            if isinstance(reply, str) and len(reply.strip()) >= 8:
                                scoping_prompt = reply.strip()
                    except Exception as e:
                        logger.warning(f"Trip-scoping LLM prompt fallback triggered: {e}")

                parsed["intent"] = "need_location"
                parsed["need_location_prompt"] = scoping_prompt
                parsed["coords"] = None
                setattr(fsm, "early_response", scoping_prompt)

                if chat_id:
                    try:
                        upd_fn = getattr(fsm.session_repo, "update_working_memory", None)
                        if callable(upd_fn):
                            res = upd_fn(
                                chat_id=chat_id,
                                trip_scoping={
                                    "scoping_turns": scoping_turns + 1,
                                    "original_query": user_message,
                                },
                            )
                            if inspect.isawaitable(res):
                                await res
                    except Exception as e:
                        logger.warning(f"Failed to persist trip_scoping working memory: {e}")
                return parsed

    # 策略 A：動態廣域/多聚落模糊地點主動反問收斂 (由 LLM 世界知識判定，防範硬編碼映射)
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

    target_indices = parsed_intent.get("target_indices") or []
    if not target_indices and parsed_intent.get("target_index") is not None:
        target_indices = [parsed_intent["target_index"]]

    # 多店橫向對比分支 (如「比較 1 跟 2」)
    if len(target_indices) >= 2:
        valid_stores: list[tuple[int, dict[str, Any]]] = []
        for idx in target_indices:
            pos = idx - 1
            if 0 <= pos < len(recommended_stores):
                valid_stores.append((idx, recommended_stores[pos]))

        if len(valid_stores) >= 2:
            store_descs = []
            store_names = []
            for num, s in valid_stores:
                s_name = s.get("name_ja") or s.get("name") or f"第 {num} 家"
                store_names.append(s_name)
                store_descs.append(f"### 選手 {num}：{s_name}\n{json.dumps(s, ensure_ascii=False, indent=2)}")

            messages = [
                {
                    "role": "system",
                    "content": get_skill_loader().build_follow_up_system_prompt(is_comparison=True),
                },
                {
                    "role": "user",
                    "content": f"使用者問題：{user_message}\n候選店家資訊：\n" + "\n\n".join(store_descs),
                },
            ]
            try:
                t0 = time.time()
                compared_stores = [s for _, s in valid_stores]
                reply, model, tokens = await fsm.llm_client.call_text(messages)
                fsm._last_state_4_model = model
                fsm._last_state_4_tokens = tokens
                ans, fu_errors = validate_follow_up_response(reply.strip(), compared_stores)
                if fu_errors:
                    logger.warning(f"[FollowUp Validation] Multi-store issues: {fu_errors}, retrying once...")
                    retry_msgs = messages + [
                        {"role": "assistant", "content": reply},
                        {
                            "role": "user",
                            "content": f"請修正以下輸出問題後重新回答：{', '.join(fu_errors)}。嚴禁出現日文長句或搜尋連結。",
                        },
                    ]
                    reply2, model2, tokens2 = await fsm.llm_client.call_text(retry_msgs)
                    fsm._last_state_4_model = model2
                    fsm._last_state_4_tokens += tokens2
                    ans2, fu_errors2 = validate_follow_up_response(reply2.strip(), compared_stores)
                    if not fu_errors2:
                        ans = ans2
                    else:
                        ans = f"為您對比 {' 與 '.join(store_names)}：建議可依目前距離與偏好口味挑選，詳細營業與付款資訊如上輪卡片所列。"

                latency = int((time.time() - t0) * 1000)
                await fsm.trace_logger.log_step(
                    action_type="follow_up_comparison",
                    tool_name="llm_follow_up_comparison",
                    tool_input={"message": user_message, "compared_stores": store_names},
                    tool_output={"response": ans, "length": len(ans)},
                    latency_ms=latency,
                )
                return ans
            except Exception as e:
                logger.warning(f"Failed to generate multi-store comparison: {e}")

    # 單店追問分支 (支援受限 2 輪 Tool Loop：detail_lookup / nearby_pairing + 輸出驗證)
    target_idx = (target_indices[0] if target_indices else (parsed_intent.get("target_index") or 1)) - 1
    if target_idx < 0 or target_idx >= len(recommended_stores):
        target_idx = 0

    target_store = recommended_stores[target_idx]
    store_name = target_store.get("name_ja") or target_store.get("name") or "該店家"
    loader = get_skill_loader()
    system_prompt = loader.build_follow_up_system_prompt(is_comparison=False)
    extra_context = ""
    extra_allowed_stores: list[dict[str, Any]] = []
    tool_used = "llm_follow_up"

    # Round 1：僅在目標店家至少具備一種可用工具的必要欄位時，才透過 call_structured_json 判斷工具路由
    can_detail_lookup = bool(
        (target_store.get("restaurant_id") or target_store.get("id"))
        or (target_store.get("sample_reviews") or target_store.get("reviews"))
    )
    can_nearby_pairing = bool(
        isinstance(target_store.get("lat"), (int, float))
        and isinstance(target_store.get("lng"), (int, float))
        and target_store.get("lat")
        and target_store.get("lng")
    )

    decision = FollowUpDecision(action="answer")
    call_structured = getattr(fsm.llm_client, "call_structured_json", None)
    if (can_detail_lookup or can_nearby_pairing) and callable(call_structured):
        try:
            dec_msgs = [
                {
                    "role": "system",
                    "content": (
                        "請判斷回答使用者對目標店家的追問是否需要呼叫工具。\n"
                        "- 若目標店家 JSON 已有答案（營業時間、地址、價格、支付），選 answer。\n"
                        "- 若詢問素食/過敏/嬰兒推車/座位/排隊實況等細節，選 detail_lookup。\n"
                        "- 若詢問吃完附近續攤/甜點/咖啡廳/酒吧，選 nearby_pairing。"
                    ),
                },
                {
                    "role": "user",
                    "content": f"問題：{user_message}\n店家摘要：{json.dumps(target_store, ensure_ascii=False)[:1200]}",
                },
            ]
            dec_res = call_structured(dec_msgs, schema=FollowUpDecision)
            if inspect.isawaitable(dec_res):
                raw_dec, _, _ = await asyncio.wait_for(dec_res, timeout=6.0)
                decision = FollowUpDecision.model_validate(raw_dec)
        except Exception as e:
            logger.debug(f"Follow-up decision degraded to direct answer: {e}")
            decision = FollowUpDecision(action="answer")

    # 執行受限工具 (上限 1 次工具呼叫，含超時與 Fail-Safe 隔離)
    if decision.action == "detail_lookup":
        tool_used = "follow_up_detail_lookup"
        detail_skill = loader.load_scenario("detail-lookup")
        if detail_skill:
            system_prompt = f"{system_prompt}\n\n{detail_skill}"
        review_snippets: list[str] = []
        rid = target_store.get("restaurant_id") or target_store.get("id")
        vector_repo = getattr(fsm, "vector_repo", None)
        if rid and vector_repo and callable(getattr(vector_repo, "get_reviews_by_restaurant_id", None)):
            try:
                v_res = vector_repo.get_reviews_by_restaurant_id(UUID(str(rid)))
                if inspect.isawaitable(v_res):
                    db_revs = await asyncio.wait_for(v_res, timeout=8.0)
                    for r in db_revs or []:
                        if isinstance(r, dict):
                            txt = r.get("review_text_zh") or r.get("review_text_ja")
                            if txt:
                                review_snippets.append(str(txt))
            except Exception as e:
                logger.warning(f"Follow-up detail_lookup vector_repo failed (Fail-Safe): {e}")
        if not review_snippets:
            for r in target_store.get("sample_reviews") or target_store.get("reviews") or []:
                if isinstance(r, dict):
                    txt = r.get("text_zh") or r.get("zh") or r.get("text")
                    if txt:
                        review_snippets.append(str(txt))
        if review_snippets:
            extra_context = "\n\n【在地評論摘要】\n" + "\n".join(f"- {s}" for s in review_snippets[:5])

    elif decision.action == "nearby_pairing":
        tool_used = "follow_up_nearby_pairing"
        nearby_skill = loader.load_scenario("nearby-pairing")
        if nearby_skill:
            system_prompt = f"{system_prompt}\n\n{nearby_skill}"
        lat = target_store.get("lat")
        lng = target_store.get("lng")
        places_client = getattr(fsm, "places_client", None)
        if (
            lat
            and lng
            and isinstance(lat, (int, float))
            and isinstance(lng, (int, float))
            and places_client
            and callable(getattr(places_client, "search_nearby", None))
        ):
            try:
                p_res = places_client.search_nearby(
                    lat=float(lat),
                    lng=float(lng),
                    keyword=decision.tool_query or "咖啡 甜點",
                    radius=400,
                )
                if inspect.isawaitable(p_res):
                    nearby_list = await asyncio.wait_for(p_res, timeout=8.0)
                    if isinstance(nearby_list, list) and nearby_list:
                        extra_allowed_stores = nearby_list[:5]
                        nearby_lines = [
                            f"- {n.get('name_ja') or n.get('name')} (評分: {n.get('rating', 'N/A')}, 地址: {n.get('address', '鄰近區域')})"
                            for n in extra_allowed_stores
                        ]
                        extra_context = "\n\n【方圓 400m 內真實周邊店家】\n" + "\n".join(nearby_lines)
            except Exception as e:
                logger.warning(f"Follow-up nearby_pairing places_client failed (Fail-Safe): {e}")

    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": f"使用者問題：{user_message}\n目標店家資訊：\n{json.dumps(target_store, ensure_ascii=False, indent=2)}{extra_context}",
        },
    ]

    fallback_ans = f"針對 `{store_name}` 的問題：該店評分為 {target_store.get('tabelog_rating') or target_store.get('rating')} 分，地址在 {target_store.get('address', '市區')}。"

    try:
        t0 = time.time()
        reply, model, tokens = await fsm.llm_client.call_text(messages)
        fsm._last_state_4_model = model
        fsm._last_state_4_tokens = tokens
        ans, fu_errors = validate_follow_up_response(
            reply.strip(),
            [target_store],
            extra_allowed_stores=extra_allowed_stores,
        )
        if fu_errors:
            logger.warning(f"[FollowUp Validation] Single-store issues: {fu_errors}, retrying once...")
            retry_msgs = messages + [
                {"role": "assistant", "content": reply},
                {
                    "role": "user",
                    "content": f"請修正以下問題後重新簡潔回答：{', '.join(fu_errors)}。嚴禁出現日文長句或搜尋連結。",
                },
            ]
            reply2, model2, tokens2 = await fsm.llm_client.call_text(retry_msgs)
            fsm._last_state_4_model = model2
            fsm._last_state_4_tokens += tokens2
            ans2, fu_errors2 = validate_follow_up_response(
                reply2.strip(),
                [target_store],
                extra_allowed_stores=extra_allowed_stores,
            )
            ans = ans2 if not fu_errors2 else fallback_ans

        latency = int((time.time() - t0) * 1000)
        await fsm.trace_logger.log_step(
            action_type="follow_up",
            tool_name=tool_used,
            tool_input={"message": user_message, "target_store": store_name, "action": decision.action},
            tool_output={"card_content": ans, "length": len(ans)},
            latency_ms=latency,
        )
        return ans
    except Exception as e:
        logger.warning(f"Follow up LLM call failed: {e}")
        await fsm.trace_logger.log_step(
            action_type="follow_up",
            tool_name="fallback_follow_up",
            tool_input={"message": user_message, "target_store": store_name, "error": str(e)},
            tool_output={"card_content": fallback_ans, "length": len(fallback_ans)},
        )
        return fallback_ans
