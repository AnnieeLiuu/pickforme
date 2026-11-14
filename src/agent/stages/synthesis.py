# -*- coding: utf-8 -*-
"""State 4: Card Synthesis, Guardrail Validation and Template Fallback

負責美食推薦卡片提示詞建構、LLM 卡片生成、店名反引號代碼塊修復、
Scoped Anchor 評分防偽校正護欄、分級排版防禦 (Tiered Defense) 以及純程式碼模板保底。
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from src.agent.skill_loader import get_skill_loader
from src.agent.stages.enrichment import format_payment_methods
from src.agent.stages.places_search import is_wide_area_origin
from src.agent.validator import (
    is_store_matched,
    repair_card_in_place,
    repair_store_name_code_blocks,
    split_card_store_blocks,
    validate_card_tiered,
)
from src.services.review_sanitizer import synthesize_store_recommendation_reason

if TYPE_CHECKING:
    from src.agent.pipeline import FoodRecommendationFSM

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "config" / "prompts"
AGENT_SYSTEM_PROMPT_PATH = PROMPTS_DIR / "agent_system.md"

RECOMMENDATION_CARD_SYSTEM_PROMPT = """你是一位專業、熱情的日本美食特派員「PickForMe (美食導航員)」，請依據所提供的候選店家完整資料，以繁體中文為使用者排版生成引人入勝、專業的美食推薦卡片。

排版規範：
1. 動態精選推薦梯隊（真實呈現 1 ~ 4 家，絕不湊數）：
   - 根據給予的候選餐廳清單推薦符合條件的店家。
   - 【嚴格真實性禁令】：絕對禁止自行補充、腦補或推薦任何未列於輸入候選清單中的店家！若候選名單只有 1~2 家，就只誠實呈現該 1~2 家；店家連結必須使用輸入提供的真實連結，嚴禁自行產生 google.com/maps/search/ 搜尋字串或捏造網址！
2. 每家餐廳排版格式規範（嚴格依序）：
   - 🍱 **店名**：請務必將「日文原名」單獨放在 Code 區塊中（例如：`寿々喜家 本店`），方便 Telegram 一鍵點擊複製！
   - 🚶 **步行距離（緊接在店名最前排）**：必須明確標明起點地標，例如：`🚶 **步行距離**：從「新宿御苑」步行約 3 分鐘 (240m)`（若為 GPS 定位則為 `從「您目前位置」步行約...`），提供遊客最明確直覺的腳程參考！
   - ⭐️ **雙平台評分與老饕評價（嚴格遵守優先級）**：
     - 若有 Tabelog 評分：標註「⭐️ Tabelog X.XX 分 (XXX 則評價) / Google X.X」，且必須優先提煉 Tabelog 在地老饕對口感、湯頭、麵條、肉質或必點招牌小菜的深入點評！
     - 若 Tabelog 暫無資料：標註「⭐️ Google X.X 分 (XXX 則評價) ｜ Tabelog 暫無評分」，並提煉 Google 網友的普遍好評亮點（排隊熱度、招牌特色）。
   - 🏆 **獲獎/支付**：若有「百名店/Award」或支援「PayPay / 信用卡」請重點標註。
   - 💰 **預算區間**：午餐/晚餐預算。
   - 📍 **簡短特色與老饕推薦理由**：招牌菜色與口感層次（1~2 句）。
   - 🔗 **店家連結**：附上提供的真實連結 Markdown（例如：[Tabelog 頁面](真實網址) 或 [Google Maps 頁面](真實網址)），務必輸出 Markdown 超連結語法，不可僅輸出無超連結之純文字。
3. **特派員提醒與收斂引導（極簡凝練，嚴格限 1 ~ 2 句話）**：
   - 請在卡片末尾以「💡 特派員提醒：...」簡短作結。
   - 誠實範圍引導：若推薦店家僅有 1 ~ 2 家，請誠實說明（例如提醒目前此範圍內僅有該名店，若想探索更多可建議擴大範圍至鄰近商圈）。
   - **嚴格限制在 1 ~ 2 句話之內**，保持精煉清爽！

請保持親切、專業、條理分明。直接輸出 Markdown 卡片內容。"""


def get_recommendation_card_prompt() -> str:
    """動態載入模組化推薦卡片系統提示詞 (核心人格 + State 4 卡片規則)，支援即時自訂與模組保底"""
    loader = get_skill_loader()
    core = loader.load_core_persona()
    card_rules = loader.load_scenario("card-synthesis")
    if core and card_rules:
        return f"{core}\n\n{card_rules}"
    if card_rules:
        return card_rules
    if AGENT_SYSTEM_PROMPT_PATH.exists():
        try:
            return AGENT_SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to read prompt file {AGENT_SYSTEM_PROMPT_PATH}: {e}")
    return RECOMMENDATION_CARD_SYSTEM_PROMPT


def resolve_card_category(cuisine: str = "", category: Optional[str] = None) -> str:
    """解析推薦卡片之類別屬性 (food / drink / souvenir)

    若已明確傳入 category 則直接採用；否則依據 cuisine 核心關鍵字自動自適應推導。
    """
    if category in ("drink", "souvenir", "food"):
        return category
    c = (cuisine or "").lower()
    souvenir_keywords = (
        "伴手禮",
        "名產",
        "土產",
        "お土産",
        "みやげ",
        "和菓子",
        "銘菓",
        "點心",
        "蛋糕",
        "落雁",
        "煎餅",
        "最中",
        "羊羹",
        "大福",
    )
    drink_keywords = (
        "茶",
        "咖啡",
        "喫茶",
        "カフェ",
        "地酒",
        "日本酒",
        "酒吧",
        "飲品",
        "飲料",
        "ほうじ茶",
        "抹茶",
        "煎茶",
        "玉露",
        "清酒",
    )
    if any(k in c for k in souvenir_keywords):
        return "souvenir"
    if any(k in c for k in drink_keywords):
        return "drink"
    return "food"


def format_operating_hours(open_now: Optional[bool]) -> str:
    """格式化營業狀態標籤"""
    if open_now is True:
        return "營業中"
    elif open_now is False:
        return "⚠️ 今日已打烊 / 公休"
    return "以店家當日公告為準"


def render_template_fallback_card(
    stores: list[dict[str, Any]],
    cuisine: str,
    is_format_fallback: bool = False,
    category: Optional[str] = None,
) -> str:
    """LLM 連線失敗或格式回退時的純程式碼資料模板保底"""
    cat = resolve_card_category(cuisine=cuisine, category=category)
    if cat == "souvenir":
        badge = (
            f"🎁 **已為您精選「{cuisine or '熱門伴手禮'}」指標推薦名店如下：**\n"
            if is_format_fallback
            else "⚠️ **AI 語意模型連線異常，已為您自動載入直出精準伴手禮老舖卡片：**\n"
        )
        entity_name = "老舖／名店"
        explore_more_tip = "若想探索更多伴手禮與銘菓選擇，可隨時告訴我擴大搜尋半徑或換個商圈！"
    elif cat == "drink":
        badge = (
            f"🍵 **已為您精選「{cuisine or '特色茶飲／咖啡'}」指標推薦茶房如下：**\n"
            if is_format_fallback
            else "⚠️ **AI 語意模型連線異常，已為您自動載入直出精準名茶／飲品名店卡片：**\n"
        )
        entity_name = "茶房／名店"
        explore_more_tip = "若想探索更多茶房或飲品選擇，可隨時告訴我擴大搜尋半徑或換個商圈！"
    else:
        badge = (
            f"🍱 **已為您精選「{cuisine or '熱門料理'}」指標推薦餐廳如下：**\n"
            if is_format_fallback
            else "⚠️ **AI 語意模型連線異常，已為您自動載入直出精準美食卡片：**\n"
        )
        entity_name = "餐廳"
        explore_more_tip = "若想探索更多選擇，可隨時告訴我擴大搜尋半徑或換個商圈！"

    lines = [badge]
    if not is_format_fallback:
        lines.append(f"精選「{cuisine or '熱門推薦'}」指標{entity_name}如下：\n")
    for idx, s in enumerate(stores, 1):
        name = s.get("name_ja") or s.get("name") or "精選餐廳"
        t_rating = s.get("tabelog_rating")
        g_rating = s.get("rating") or s.get("google_rating")

        walk_desc = s.get("walking_desc")

        rank_badge = s.get("rank_badge") or (["🥇", "🥈", "🥉"][idx - 1] if idx <= 3 else f"#{idx}")
        lines.append(f"### {rank_badge} 精選推薦 {idx}：`{name}`")
        if walk_desc:
            lines.append(f"🚶 **步行距離**：{walk_desc}")
        lines.append(f"⏰ **營業狀態**：{format_operating_hours(s.get('open_now'))}")
        if t_rating:
            lines.append(f"⭐️ **Tabelog 評分**：{float(t_rating):.2f} 分 ({s.get('review_count', 0)} 則評價)")
        else:
            lines.append(f"⭐️ **評分資訊**：Google {g_rating or '4.0'} 分 (Tabelog 暫無評分，以 Google 評價為依據)")

        if s.get("price_dinner"):
            lines.append(f"💰 **預算**：晚餐 {s.get('price_dinner')}")
        pay_desc = s.get("payment_methods_desc") or format_payment_methods(
            supports_paypay=s.get("supports_paypay"),
            credit_cards=s.get("credit_cards"),
            accepts_credit_cards=s.get("accepts_credit_cards"),
            accepts_cash_only=s.get("accepts_cash_only"),
            accepts_nfc=s.get("accepts_nfc"),
        )
        if pay_desc:
            lines.append(f"💳 **支付方式**：{pay_desc}")
        if s.get("address"):
            lines.append(f"📍 **地址**：{s.get('address')}")
        if s.get("tabelog_url"):
            lines.append(f"🔗 [食べログ詳細頁面]({s.get('tabelog_url')})")
        elif s.get("google_maps_url") or s.get("google_place_id"):
            g_url = (
                s.get("google_maps_url") or f"https://www.google.com/maps/place/?q=place_id:{s.get('google_place_id')}"
            )
            lines.append(f"🔗 [Google Maps 頁面]({g_url})")
        # 老饕短評 (透過 ReviewSanitizer 淨化雜訊；無真實中文食評或獎項時寧缺勿濫)
        rev_txt, rev_badge = synthesize_store_recommendation_reason(s, cuisine=cuisine)
        if rev_txt:
            lines.append(f"📍 **老饕推薦理由 ({rev_badge})**：{rev_txt}")
        lines.append("")

    if len(stores) < 3:
        lines.append(f"💡 **特派員提醒**：目前此範圍內精選出 {len(stores)} 家符合條件的名店；{explore_more_tip}\n")

    return "\n".join(lines).strip()


def sanitize_card_ratings(card_text: str, stores: list[dict[str, Any]]) -> str:
    """Scoped Anchor Guardrail: 區塊錨點防偽護欄

    以店家為單位隔離切割 Markdown 區塊，透過前後雙向強關鍵字錨點精準校對與矯正數值，
    排除跨店串位與非評分數值誤傷，確保 Telegram 卡片上的評分 100% 與真實資料源一致。
    """
    if not card_text or not stores:
        return card_text

    try:
        # 1. 使用共用區塊切割器隔離各店家段落 (支援 1. / ### / 🥇🥈🥉 / 💡 特派員提醒)
        parts = split_card_store_blocks(card_text)
        modified_parts: list[str] = []
        rank_badges = {1: "🥇", 2: "🥈", 3: "🥉"}

        for part in parts:
            stripped = part.lstrip()
            first_line = stripped.splitlines()[0] if stripped else ""
            # 若為特派員提醒或分隔線結尾段落，直接保留不進行評分替換
            if first_line.startswith("💡") or first_line.startswith("---"):
                modified_parts.append(part)
                continue

            matched_store: Optional[dict[str, Any]] = None
            # 優先以正規化店名或原文店名在區塊中精準配對
            for s in stores:
                name_ja = s.get("name_ja") or s.get("name") or ""
                if (name_ja and name_ja in part) or is_store_matched(first_line, s):
                    matched_store = s
                    break

            # 若店名未命中，才退回首行序號或獎牌徽章配對 (嚴禁跨入內文避免 \b3\. 誤撞 3.5)
            if not matched_store:
                for idx, s in enumerate(stores, 1):
                    badge = rank_badges.get(idx)
                    if re.search(rf"(?:###\s*{idx}(?!\d)|\b{idx}[\.、](?!\d)|\*\*{idx}[\.、](?!\d))", first_line) or (
                        badge and badge in first_line
                    ):
                        matched_store = s
                        break

            if not matched_store:
                modified_parts.append(part)
                continue

            updated_part = part

            # 2. 檢驗並校正 Tabelog 評分錨點 (只作用於該店家隔離區塊)
            t_rating = matched_store.get("tabelog_rating")
            if t_rating is not None:
                t_val = float(t_rating)
                t_pattern = r"((?:Tabelog|食べログ)[^\d\n]{0,25}?)(\d\.\d{1,2})(\s*分)?"

                def _replace_tabelog(m: re.Match[str]) -> str:
                    orig_val = float(m.group(2))
                    unit = m.group(3) or ""
                    if abs(orig_val - t_val) > 0.001:
                        logger.info(
                            f"[Guardrail] Corrected Tabelog rating for '{matched_store.get('name_ja')}': "
                            f"{orig_val} -> {t_val:.2f}"
                        )
                        return f"{m.group(1)}{t_val:.2f}{unit}"
                    return m.group(0)

                updated_part = re.sub(t_pattern, _replace_tabelog, updated_part, count=1)

            # 3. 檢驗並校正 Google 評分錨點 (只作用於該店家隔離區塊)
            g_rating = matched_store.get("google_rating") or matched_store.get("rating")
            if g_rating is not None:
                g_val = float(g_rating)
                g_pattern = r"(Google[^\d\n]{0,20}?)([0-5]\.[0-9])(\s*分)?"

                def _replace_google(m: re.Match[str]) -> str:
                    orig_val = float(m.group(2))
                    unit = m.group(3) or ""
                    if abs(orig_val - g_val) > 0.001:
                        logger.info(
                            f"[Guardrail] Corrected Google rating for '{matched_store.get('name_ja')}': "
                            f"{orig_val} -> {g_val:.1f}"
                        )
                        return f"{m.group(1)}{g_val:.1f}{unit}"
                    return m.group(0)

                updated_part = re.sub(g_pattern, _replace_google, updated_part, count=1)

            modified_parts.append(updated_part)

        return "".join(modified_parts)
    except Exception as e:
        logger.warning(
            f"[Guardrail] Error during card sanitization: {e}. Keeping original card.",
            exc_info=True,
        )
        return card_text


async def execute_state_4_synthesis(
    fsm: FoodRecommendationFSM,
    stores: list[dict[str, Any]],
    cuisine: str,
    location_name: Optional[str],
    category: Optional[str] = None,
    user_query: str = "",
) -> str:
    """State 4: 推薦卡片排版合成、護欄校驗與防禦回退"""
    t0 = time.time()
    cat = resolve_card_category(cuisine=cuisine, category=category)

    if cat == "souvenir":
        category_header = "伴手禮／名物類別"
        entity_label = "候選名店／老舖"
        honest_label = "高評價名店老舖"
    elif cat == "drink":
        category_header = "特色飲品／名茶類別"
        entity_label = "候選茶房／名店"
        honest_label = "高評價茶房名店"
    else:
        category_header = "美食類別"
        entity_label = "候選餐廳"
        honest_label = "高評價店家"

    honest_notice = ""
    if len(stores) < 3:
        honest_notice = f"（特別提醒：目前此範圍內符合條件之{honest_label}僅有 {len(stores)} 家，請誠實僅呈現這 {len(stores)} 家，絕對禁止無中生有自行腦補其他店家或搜尋連結，並請在特派員提醒中建議使用者若想看更多可擴大半徑或換個商圈）\n"

    prompt_content = (
        f"目標地點：{location_name or '指定區域'}\n{category_header}：{cuisine or '熱門推薦'}\n"
        f"{entity_label}總數：共 {len(stores)} 家 {honest_notice}\n{entity_label}詳細數據如下：\n"
    )
    for idx, s in enumerate(stores, 1):
        rank_badge = s.get("rank_badge") or (["🥇", "🥈", "🥉"][idx - 1] if idx <= 3 else f"#{idx}")
        origin_label = location_name or "您目前位置"
        walk_str = s.get("walking_desc") or (
            f"從「{origin_label}」步行約 {s.get('walk_time_minutes', 3)} 分鐘 ({s.get('distance_meters', 200)}m)"
            if s.get("walk_time_minutes")
            else f"從「{origin_label}」步行約 3~5 分鐘"
        )
        pay_desc = s.get("payment_methods_desc") or format_payment_methods(
            supports_paypay=s.get("supports_paypay"),
            credit_cards=s.get("credit_cards"),
            accepts_credit_cards=s.get("accepts_credit_cards"),
            accepts_cash_only=s.get("accepts_cash_only"),
            accepts_nfc=s.get("accepts_nfc"),
        )
        t_url = s.get("tabelog_url")
        g_url = s.get("google_maps_url") or (
            f"https://www.google.com/maps/place/?q=place_id:{s.get('google_place_id')}"
            if s.get("google_place_id")
            else None
        )
        store_link = t_url or g_url
        link_src = "Tabelog" if t_url else "Google Maps"

        prompt_content += (
            f"\n店家 {idx} (老饕排名：{rank_badge})：\n"
            f"- 排名徽章: {rank_badge}\n"
            f"- 店名(日文)：{s.get('name_ja') or s.get('name')}\n"
            f"- 步行距離：{walk_str}\n"
            f"- 營業狀態：{format_operating_hours(s.get('open_now'))}\n"
            f"- 評分來源：{s.get('rating_source')}\n"
            f"- Tabelog 評分：{s.get('tabelog_rating')}\n"
            f"- Google 評分：{s.get('google_rating') or s.get('rating')}\n"
            f"- 評價人數：{s.get('review_count') or s.get('user_ratings_total')}\n"
            f"- 預算(午/晚)：{s.get('price_lunch')} / {s.get('price_dinner')}\n"
            f"- 獲獎紀錄：{s.get('awards')}\n"
            f"- 支付方式：{pay_desc}\n"
            f"- PayPay支援：{s.get('supports_paypay')}\n"
            f"- 地址：{s.get('address')}\n"
            f"- 店家專屬連結 ({link_src})：{store_link}\n"
        )
        review_text = s.get("foodie_review_highlight")
        source_type = s.get("foodie_review_source")

        if not review_text:
            sample_revs = s.get("sample_reviews") or []
            if sample_revs and isinstance(sample_revs, list):
                first_rev = sample_revs[0]
                if isinstance(first_rev, dict):
                    review_text = first_rev.get("text") or first_rev.get("original_text") or first_rev.get("zh")
                    if not source_type:
                        source_type = first_rev.get("source")
                elif isinstance(first_rev, str):
                    review_text = first_rev

        if source_type == "tabelog":
            rev_source_label = "食べログ 口コミ"
        elif source_type == "google":
            rev_source_label = "Google 在地評價"
        else:
            rev_source_label = "特派員推薦理由"

        if review_text:
            prompt_content += f"- 顧客/老饕真實評價 (來源: {rev_source_label})：{review_text}\n"

    system_prompt = get_skill_loader().build_synthesis_system_prompt(
        resolved_category=cat,
        store_count=len(stores),
        location_name=location_name,
        cuisine=cuisine,
        user_query=user_query,
        is_wide_area=is_wide_area_origin(location_name),
        fallback_card_prompt=RECOMMENDATION_CARD_SYSTEM_PROMPT,
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt_content},
    ]

    try:
        content, model, tokens = await fsm.llm_client.call_text(messages)
        fsm._last_state_4_model = model
        fsm._last_state_4_tokens = tokens
        card_text = content.strip()
        if not card_text:
            raise ValueError("LLM returned empty card content")

        # 🛡️ 執行店名反引號代碼塊自動修復
        card_text = repair_store_name_code_blocks(card_text, stores)

        # 🛡️ 執行 Scoped Anchor 毫秒級數值防偽校正護欄
        card_text = fsm._sanitize_card_ratings(card_text, stores)

        # 🛡️ 分級排版與真實性防禦 (Tiered Defense Guardrail)
        fatal_errors, minor_warnings = validate_card_tiered(card_text, stores)
        if fatal_errors:
            logger.warning(
                f"[CardGuardrail] Fatal violation detected ({', '.join(fatal_errors)}). "
                f"Falling back to deterministic template card."
            )
            fsm._last_state_4_model = "template_fallback"
            fsm._last_state_4_tokens = 0
            fallback_card = fsm._render_template_fallback_card(stores, cuisine, is_format_fallback=True, category=cat)
            latency = int((time.time() - t0) * 1000)
            await fsm.trace_logger.log_step(
                action_type="state_4_synthesis",
                tool_name="card_synthesis_format_fallback",
                tool_input={
                    "stores_count": len(stores),
                    "cuisine": cuisine,
                    "category": cat,
                    "fatal_errors": fatal_errors,
                },
                tool_output={"card_content": fallback_card, "length": len(fallback_card)},
                latency_ms=latency,
            )
            return fallback_card

        if minor_warnings:
            logger.info(
                f"[CardGuardrail] Minor violations detected ({', '.join(minor_warnings)}). "
                f"Performing in-place surgical repair."
            )
            card_text = repair_card_in_place(card_text, stores, cuisine=cuisine)

        latency = int((time.time() - t0) * 1000)
        await fsm.trace_logger.log_step(
            action_type="state_4_synthesis",
            tool_name="card_synthesis",
            tool_input={
                "stores_count": len(stores),
                "cuisine": cuisine,
                "location": location_name,
                "category": cat,
            },
            tool_output={"card_content": card_text, "length": len(card_text)},
            latency_ms=latency,
        )
        return card_text
    except Exception as e:
        logger.error(f"State 4 LLM card synthesis failed: {e}. Activating template fallback...")
        fsm._last_state_4_model = "template_fallback"
        fsm._last_state_4_tokens = 0
        fallback_card = fsm._render_template_fallback_card(stores, cuisine, category=cat)
        latency = int((time.time() - t0) * 1000)
        await fsm.trace_logger.log_step(
            action_type="state_4_synthesis",
            tool_name="card_synthesis_fallback",
            tool_input={"stores_count": len(stores), "cuisine": cuisine, "category": cat, "error": str(e)},
            tool_output={"card_content": fallback_card, "length": len(fallback_card)},
            latency_ms=latency,
        )
        return fallback_card
