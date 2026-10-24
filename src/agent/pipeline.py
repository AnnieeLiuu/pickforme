import asyncio
import logging
import math
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional
from uuid import UUID

from src.agent.llm_client import LLMClient
from src.agent.stages.intent import (
    INTENT_PARSER_SYSTEM_PROMPT,
    sanitize_history_for_intent,
)
from src.agent.stages.intent import (
    execute_follow_up as stage_execute_follow_up,
)
from src.agent.stages.intent import (
    execute_state_1_intent as stage_execute_state_1_intent,
)
from src.agent.trace import AgentTraceLogger
from src.agent.validator import (
    repair_card_in_place,
    repair_store_name_code_blocks,
    validate_card_tiered,
)
from src.db.repositories.restaurant import RestaurantRepository
from src.db.repositories.session import SessionRepository
from src.db.repositories.vector import VectorRepository
from src.services.embedding import EmbeddingService
from src.services.entity_resolver import EntityResolver, calculate_distance_meters
from src.services.google_places import GooglePlacesClient
from src.services.review_sanitizer import synthesize_store_recommendation_reason
from src.services.tabelog_scraper import TabelogScraper

logger = logging.getLogger(__name__)

__all__ = [
    "FoodRecommendationFSM",
    "INTENT_PARSER_SYSTEM_PROMPT",
    "AGENT_SYSTEM_PROMPT_PATH",
    "resolve_search_constraints",
    "format_payment_methods",
    "get_recommendation_card_prompt",
]

PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "config" / "prompts"
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
    """動態載入 config/prompts/agent_system.md 系統提示詞，支援即時自訂與模組保底"""
    if AGENT_SYSTEM_PROMPT_PATH.exists():
        try:
            return AGENT_SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to read prompt file {AGENT_SYSTEM_PROMPT_PATH}: {e}")
    return RECOMMENDATION_CARD_SYSTEM_PROMPT


def format_payment_methods(
    supports_paypay: Optional[bool] = None,
    credit_cards: Optional[list[str]] = None,
    accepts_credit_cards: Optional[bool] = None,
    accepts_cash_only: Optional[bool] = None,
    accepts_nfc: Optional[bool] = None,
) -> str:
    """將 Google Places (New) 與 Tabelog 支付方式雙源融合轉換為旅客友善的說明字串 (消除假陰性武斷斷言)"""
    parts: list[str] = []

    # 1. 信用卡與無現金支付判定：
    #    (a) 若 Tabelog 有明確卡別優先展示卡別
    if credit_cards and len(credit_cards) > 0:
        if credit_cards == ["可"]:
            parts.append("信用卡")
        else:
            cards_str = "、".join(credit_cards[:3])
            parts.append(f"信用卡 ({cards_str})")
    #    (b) 若 Google 標記可刷卡亦採信
    elif accepts_credit_cards is True:
        parts.append("信用卡")
    #    (c) 若 Google Places 明確標註非僅收現金 (acceptsCashOnly: False)，且未被 Tabelog 明確否定為 0 卡
    elif accepts_cash_only is False and (credit_cards is None or len(credit_cards) > 0):
        parts.append("信用卡 / 電子支付")

    # 2. 感應支付 (NFC / Apple Pay 等) 判定
    if accepts_nfc is True and "信用卡 / 電子支付" not in parts:
        parts.append("感應支付")

    # 3. PayPay 行動支付判定
    if supports_paypay is True:
        parts.append("PayPay")

    if parts:
        return "可使用 " + " / ".join(parts)

    # 4. 現金判定：必須有明確信號指出「僅收現金」
    if accepts_cash_only is True or (credit_cards is not None and len(credit_cards) == 0 and supports_paypay is False):
        return "現金為主 (不支援信用卡/行動支付，請備足現鈔)"

    # 5. 未知/未明確標記：溫和提示
    if supports_paypay is False:
        return "現金為主 (不支援 PayPay，建議備足日圓現鈔)"

    return "現金為主 (建議備足日圓現鈔)"


def resolve_search_constraints(
    cuisine: str,
    modifier: Optional[str] = None,
) -> tuple[str, list[int], Optional[str]]:
    """解析並分離料理名稱、風格修飾語與物理距離約束 (非 Hardcode，杜絕 Places 搜尋脫靶)

    Returns:
        tuple[str, list[int], Optional[str]]:
            - clean_keyword: 純淨之 Google Places 檢索關鍵字 (例: "壽喜燒")
            - radii_ladder: 物理階梯半徑清單 (例: 15分鐘步行 -> [1200, 1700, 2500])
            - style_tag: 僅供卡片或語意重排之風格標籤 (例: "老店", "平價")
    """
    clean_cuisine = (cuisine or "熱門美食").strip()

    # 1. 偵測並移除誤混入 cuisine 的空間詞
    clean_cuisine = re.sub(r"(?:走路|步行|走)?\s*\d{1,2}\s*(?:分鐘|min|mins)[^\s]*", "", clean_cuisine).strip()
    clean_cuisine = re.sub(r"(?:遠一點|近一點|大圈一點|附近)", "", clean_cuisine).strip()
    if not clean_cuisine:
        clean_cuisine = "熱門美食"

    radii_ladder = [800, 1200, 1800]
    style_tag: Optional[str] = None

    if not modifier:
        return clean_cuisine, radii_ladder, None

    mod = modifier.strip()

    # 2. 物理空間約束：提取具體分鐘數 (以日本 80m/min 步行標準換算)
    walk_match = re.search(r"(?:走路|步行|走)?\s*(\d{1,2})\s*(?:分鐘|min|mins)", mod)
    if walk_match:
        mins = int(walk_match.group(1))
        # 換算為公尺，例如 15 分鐘 -> 1200m
        base_r = max(800, min(2500, mins * 80))
        radii_ladder = [base_r, min(2500, base_r + 500), 2500]
    elif any(kw in mod for kw in ["遠一點", "大圈一點", "距離遠", "擴大", "遠些"]):
        radii_ladder = [1200, 1800, 2500]
    elif any(kw in mod for kw in ["近一點", "最近", "附近", "腳程短", "不想走"]):
        radii_ladder = [500, 800, 1200]

    # 3. 剝離空間詞，檢驗是否有實質風格/口味詞 (如 "老店", "平價", "清淡", "濃郁")
    rem_style = re.sub(r"(?:走路|步行|走)?\s*\d{1,2}\s*(?:分鐘|min|mins)[^\s]*", "", mod)
    rem_style = re.sub(
        r"(?:可以接受|距離|遠一點|近一點|大圈一點|擴大|以內|內都可以|都可以|附近|能接受)", "", rem_style
    ).strip()

    clean_keyword = clean_cuisine
    if rem_style and len(rem_style) >= 2:
        style_tag = rem_style
        # 只有實質風格詞才安全疊加到 keyword
        clean_keyword = f"{clean_cuisine} {rem_style}"

    return clean_keyword, radii_ladder, style_tag


class FoodRecommendationFSM:
    """確定性 4 階段有限狀態機 (Deterministic 4-Stage FSM Pipeline)"""

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        places_client: Optional[GooglePlacesClient] = None,
        tabelog_scraper: Optional[TabelogScraper] = None,
        entity_resolver: Optional[EntityResolver] = None,
        restaurant_repo: Optional[RestaurantRepository] = None,
        session_repo: Optional[SessionRepository] = None,
        trace_logger: Optional[AgentTraceLogger] = None,
        vector_repo: Optional[VectorRepository] = None,
        embedding_service: Optional[EmbeddingService] = None,
    ):
        self.llm_client = llm_client or LLMClient()
        self.places_client = places_client or GooglePlacesClient()
        self.tabelog_scraper = tabelog_scraper or TabelogScraper()
        self.entity_resolver = entity_resolver or EntityResolver()
        self.restaurant_repo = restaurant_repo or RestaurantRepository()
        self.session_repo = session_repo or SessionRepository()
        self.trace_logger = trace_logger or AgentTraceLogger()
        self.vector_repo = vector_repo or VectorRepository()
        self.embedding_service = embedding_service or EmbeddingService()
        self._last_state_4_model: Optional[str] = None
        self._last_state_4_tokens: int = 0

    def _sanitize_history_for_intent(
        self, conversation_history: Optional[list[dict[str, str]]]
    ) -> list[dict[str, str]]:
        """帶入前輪簡短上下文 (委派至 stages.intent)"""
        return sanitize_history_for_intent(conversation_history)

    async def execute_state_1_intent(
        self,
        user_message: str,
        user_location: Optional[tuple[float, float]],
        conversation_history: Optional[list[dict[str, str]]],
        chat_id: Optional[int] = None,
    ) -> dict[str, Any]:
        """State 1: 0.3s 微型 LLM 結構化意圖與座標解析 (委派至 stages.intent)"""
        return await stage_execute_state_1_intent(self, user_message, user_location, conversation_history, chat_id)

    def _prefilter_potential_foodie_places(
        self,
        places: list[dict[str, Any]],
        max_seeds: int = 6,
    ) -> list[dict[str, Any]]:
        """Google 端老饕潛力智慧預篩 (Smart Pre-filtering)

        依評分、評論數合理性與距離，在 0ms 內預篩出最具備 Tabelog 高分潛力的種子店家
        """
        if not places:
            return []

        def calc_pre_score(p: dict[str, Any]) -> float:
            g_rating = float(p.get("rating") or p.get("google_rating") or 3.5)
            rev_count = int(p.get("userRatingCount") or p.get("review_count") or 10)
            dist_m = float(p.get("distance_meters") or 500)

            # Google 評分權重
            rating_score = g_rating * 0.6
            # 評論數對數平滑 (上限 0.9，防大眾網紅店刷量)
            rev_score = min(math.log10(max(1, rev_count)) * 0.3, 0.9)
            # 距離懲罰：800m 內無扣分，超過 800m 每 500m 扣 0.1
            dist_penalty = max(0.0, (dist_m - 800) / 500 * 0.1) if dist_m > 800 else 0.0

            return rating_score + rev_score - dist_penalty

        sorted_places = sorted(places, key=calc_pre_score, reverse=True)
        return sorted_places[:max_seeds]

    async def execute_state_2_places_ladder(
        self,
        lat: float,
        lng: float,
        keyword: str,
        origin_name: Optional[str] = None,
        exclude_place_ids: Optional[set[str]] = None,
        max_candidates: int = 6,
        custom_radii: Optional[list[int]] = None,
    ) -> list[dict[str, Any]]:
        """State 2: Google Places 智慧階梯檢索 (800m ➔ 1200m ➔ 1800m ➔ 2500m 擴圈保底) + 潛力預篩"""
        t0 = time.time()
        candidates: list[dict[str, Any]] = []
        origin_prefix = f"從「{origin_name}」" if origin_name else "從「您目前位置」"
        exclude_set = set(exclude_place_ids or set())

        radii = custom_radii or [800, 1200, 1800]
        for r in radii:
            raw_places = await self.places_client.search_nearby(lat=lat, lng=lng, radius=r, keyword=keyword)
            # 過濾重複與已看過店家
            existing_ids = {c["google_place_id"] for c in candidates}
            for p in raw_places:
                pid = p.get("google_place_id")
                if pid and pid not in existing_ids and pid not in exclude_set:
                    p_lat = p.get("lat")
                    p_lng = p.get("lng")
                    if p_lat and p_lng:
                        dist_m = int(calculate_distance_meters(lat, lng, p_lat, p_lng))
                        walk_mins = max(1, math.ceil(dist_m / 80))
                        p["distance_meters"] = dist_m
                        p["walk_time_minutes"] = walk_mins
                        p["origin_name"] = origin_name or "您目前位置"
                        p["walking_desc"] = f"{origin_prefix}步行約 {walk_mins} 分鐘 ({dist_m}m)"
                    candidates.append(p)
                    existing_ids.add(pid)

            if len(candidates) >= 3:
                break

        # 自適應擴圈：若在 1800m 內候選店少於 3 家，自動向外擴圈至 2500m 保底
        if len(candidates) < 3:
            raw_places = await self.places_client.search_nearby(lat=lat, lng=lng, radius=2500, keyword=keyword)
            existing_ids = {c["google_place_id"] for c in candidates}
            for p in raw_places:
                pid = p.get("google_place_id")
                if pid and pid not in existing_ids and pid not in exclude_set:
                    p_lat = p.get("lat")
                    p_lng = p.get("lng")
                    if p_lat and p_lng:
                        dist_m = int(calculate_distance_meters(lat, lng, p_lat, p_lng))
                        walk_mins = max(1, math.ceil(dist_m / 80))
                        p["distance_meters"] = dist_m
                        p["walk_time_minutes"] = walk_mins
                        p["origin_name"] = origin_name or "您目前位置"
                        p["walking_desc"] = f"{origin_prefix}步行約 {walk_mins} 分鐘 ({dist_m}m)"
                        p["radius_expanded"] = True
                    candidates.append(p)
                    existing_ids.add(pid)
                if len(candidates) >= 3:
                    break

        # Google 端老饕潛力預篩：挑選最具 Tabelog 高分潛力的種子店家 (前 4 筆主選 + 2 筆備選)
        final_candidates = self._prefilter_potential_foodie_places(candidates, max_seeds=max_candidates)
        latency = int((time.time() - t0) * 1000)

        await self.trace_logger.log_step(
            action_type="state_2_places",
            tool_name="search_nearby_ladder",
            tool_input={
                "lat": lat,
                "lng": lng,
                "keyword": keyword,
                "excluded_count": len(exclude_set),
            },
            tool_output={"count": len(final_candidates)},
            latency_ms=latency,
        )
        return final_candidates

    async def _enrich_single_place(self, place: dict[str, Any], area_hint: Optional[str] = None) -> dict[str, Any]:
        """對單一店家補全 Tabelog 評分 (優先 Neon DB 快取，未命中則爬蟲+Resolver)"""
        place_id = place.get("google_place_id", "")
        store_name = place.get("name", "")

        # 1. Neon DB 快取檢查 (7 天 TTL)
        cached = None
        try:
            cached = await self.restaurant_repo.get_by_google_place_id(place_id)
        except Exception as e:
            logger.warning(f"DB cache check error for {place_id}: {e}")

        if cached and cached.get("tabelog_rating"):
            updated_at = cached.get("updated_at")
            is_fresh = True
            if updated_at and isinstance(updated_at, datetime):
                now = datetime.now(timezone.utc)
                if updated_at.tzinfo is None:
                    updated_at = updated_at.replace(tzinfo=timezone.utc)
                if (now - updated_at).days >= 7:
                    is_fresh = False

            # 快取行政區一致性防禦：避免歷史髒快取導致跨縣市漂移
            if is_fresh and cached.get("tabelog_url"):
                from src.services.entity_resolver import (
                    extract_prefecture_from_address,
                    extract_prefecture_from_tabelog_url,
                )

                cached_pref = extract_prefecture_from_tabelog_url(cached.get("tabelog_url"))
                google_pref = extract_prefecture_from_address(place.get("address"))
                if cached_pref and google_pref and cached_pref != google_pref:
                    logger.warning(
                        f"Poisoned cache detected for '{store_name}': Google in '{google_pref}' but cached Tabelog in '{cached_pref}'. Self-healing: invalidating and purging dirty record."
                    )
                    is_fresh = False
                    if cached.get("id"):
                        asyncio.create_task(self.restaurant_repo.delete_by_id(cached["id"]))

            if is_fresh:
                rid = cached.get("id")
                if rid and place.get("reviews"):
                    asyncio.create_task(self._index_store_reviews(rid, place["reviews"]))
                pay_cards = cached.get("credit_cards")
                pay_desc = format_payment_methods(
                    supports_paypay=cached.get("supports_paypay"),
                    credit_cards=pay_cards,
                    accepts_credit_cards=place.get("accepts_credit_cards"),
                    accepts_cash_only=place.get("accepts_cash_only"),
                    accepts_nfc=place.get("accepts_nfc"),
                )
                return {
                    **place,
                    "restaurant_id": str(rid) if rid else None,
                    "tabelog_url": cached.get("tabelog_url"),
                    "name_ja": cached.get("name_ja") or store_name,
                    "tabelog_rating": float(cached["tabelog_rating"]) if cached.get("tabelog_rating") else None,
                    "review_count": cached.get("review_count"),
                    "price_lunch": cached.get("price_lunch"),
                    "price_dinner": cached.get("price_dinner"),
                    "awards": cached.get("awards") or [],
                    "supports_paypay": cached.get("supports_paypay"),
                    "credit_cards": pay_cards,
                    "payment_methods_desc": pay_desc,
                    "sample_reviews": place.get("reviews") or [],
                    "rating_source": "Tabelog",
                }

        # 2. 未命中快取：執行即時爬蟲與比對
        try:
            # 自動組合商圈/地名關鍵字，精準鎖定在地分店 (如 Mawashizushi KATSU 横浜)
            search_query = store_name
            target_area = (area_hint or "").strip()
            if not target_area and place.get("address"):
                m = re.search(
                    r"(?:[^\d\s,，]{2,3}[都道府縣県])\s*([^\d\s,，]{2,6}?[市區町村島])",
                    place["address"],
                )
                if not m:
                    m = re.search(r"([^\d\s,，]{2,6}?[市區町村島])", place["address"])
                if m:
                    target_area = re.sub(r"[市區町村都道府縣県]$", "", m.group(1))

            if target_area and target_area.lower() not in store_name.lower():
                search_query = f"{store_name} {target_area}".strip()

            candidates = await self.tabelog_scraper.search_tabelog_candidates(store_name=search_query)
            match_res = (
                await self.entity_resolver.resolve_entity(
                    google_store=place,
                    tabelog_candidates=candidates,
                    allow_llm=True,
                )
                if candidates
                else None
            )

            # 階梯備援 1：若帶細部商圈搜尋未匹配，回退至大行政區/城市搜尋 (如 橫濱/新宿)，避免裸奔引發全日 SEO 污染
            if (not match_res or not match_res.get("match_found")) and search_query != store_name:
                city_anchor = ""
                if place.get("address"):
                    city_m = re.search(r"([^\d\s,，]{2,6}?(?:市|特別区|都|府|県))", place["address"])
                    if city_m:
                        city_anchor = re.sub(r"[都道府縣県市]$", "", city_m.group(1)).strip()

                broad_query = (
                    f"{store_name} {city_anchor}".strip()
                    if (city_anchor and city_anchor.lower() != target_area.lower())
                    else store_name
                )
                fallback_cands = await self.tabelog_scraper.search_tabelog_candidates(store_name=broad_query)
                if fallback_cands:
                    fb_match = await self.entity_resolver.resolve_entity(
                        google_store=place,
                        tabelog_candidates=fallback_cands,
                        allow_llm=True,
                    )
                    if fb_match and fb_match.get("match_found"):
                        match_res = fb_match

            # 階梯備援 2：若店名檢索未匹配（或候選皆為無關雜訊），且具備有效日本電話號碼，啟動電話精準檢索保底 (site:tabelog.com <phone>)
            phone_raw = (place.get("phone") or "").strip()
            if (
                (not match_res or not match_res.get("match_found"))
                and phone_raw
                and len(re.sub(r"\D", "", phone_raw)) >= 9
            ):
                phone_clean = re.sub(r"[^\d-]", "", phone_raw)
                logger.info(f"Store matching unaligned for '{store_name}'. Fallback to phone search: '{phone_clean}'")
                phone_candidates = await self.tabelog_scraper.search_via_grounding(store_name=phone_clean, area_name="")
                if phone_candidates:
                    for pc in phone_candidates:
                        if not pc.get("phone"):
                            pc["phone"] = phone_clean
                    phone_match = await self.entity_resolver.resolve_entity(
                        google_store=place,
                        tabelog_candidates=phone_candidates,
                        allow_llm=True,
                    )
                    if phone_match and phone_match.get("match_found"):
                        match_res = phone_match

            if match_res and match_res.get("match_found") and match_res.get("candidate"):
                cand = match_res["candidate"]
                tabelog_url = cand.get("tabelog_url", "")
                details = await self.tabelog_scraper.scrape_restaurant_details(tabelog_url, fallback_candidate=cand)
                rating = (
                    details.get("tabelog_rating")
                    or cand.get("tabelog_rating")
                    or details.get("rating")
                    or cand.get("rating")
                )
                rev_count = details.get("review_count") or cand.get("review_count")
                cards_list = details.get("credit_cards") or cand.get("credit_cards")
                paypay_val = details.get("supports_paypay")
                pay_desc = format_payment_methods(
                    supports_paypay=paypay_val,
                    credit_cards=cards_list,
                    accepts_credit_cards=place.get("accepts_credit_cards"),
                    accepts_cash_only=place.get("accepts_cash_only"),
                    accepts_nfc=place.get("accepts_nfc"),
                )

                # 存入快取 (包含評分、評論數與百名店榮譽、支付資訊)
                tabelog_save_data = {
                    **cand,
                    **details,
                    "tabelog_rating": rating,
                    "review_count": rev_count,
                    "tabelog_url": tabelog_url,
                    "name_ja": cand.get("name_ja") or store_name,
                    "supports_paypay": paypay_val,
                    "credit_cards": cards_list,
                }
                # 評論向量化：優先使用 Tabelog 老饕食評，Google 評論保底
                chosen_reviews = (
                    details.get("sample_reviews") or cand.get("sample_reviews") or place.get("reviews") or []
                )

                saved_id = None
                try:
                    saved_id = await self.restaurant_repo.save_restaurant_with_tabelog(
                        google_store=place,
                        tabelog_data=tabelog_save_data,
                        match_confidence=match_res.get("confidence", 0.9),
                        match_method=match_res.get("match_method", "rule_high"),
                    )
                    if saved_id and chosen_reviews:
                        asyncio.create_task(self._index_store_reviews(saved_id, chosen_reviews))
                except Exception as e:
                    logger.warning(f"Failed to upsert tabelog cache: {e}")

                return {
                    **place,
                    "restaurant_id": str(saved_id) if saved_id else None,
                    "tabelog_url": tabelog_url,
                    "name_ja": cand.get("name_ja") or store_name,
                    "tabelog_rating": rating,
                    "review_count": rev_count,
                    "price_lunch": details.get("price_lunch"),
                    "price_dinner": details.get("price_dinner"),
                    "awards": details.get("awards") or cand.get("awards") or [],
                    "supports_paypay": paypay_val,
                    "credit_cards": cards_list,
                    "payment_methods_desc": pay_desc,
                    "sample_reviews": chosen_reviews,
                    "rating_source": "Tabelog",
                }
        except Exception as e:
            logger.warning(f"Tabelog scraping / resolution failed for {store_name}: {e}")

        # 3. 爬蟲失敗或無資料保底：以 Google 評分呈現並透明標註
        fallback_id = None
        try:
            fallback_id = await self.restaurant_repo.save_restaurant_with_tabelog(google_store=place)
            if fallback_id and place.get("reviews"):
                asyncio.create_task(self._index_store_reviews(fallback_id, place["reviews"]))
        except Exception:
            pass

        fallback_reviews = place.get("reviews") or []
        place_id = place.get("google_place_id") or place.get("id")
        fallback_maps_url = place.get("google_maps_url") or (
            f"https://www.google.com/maps/place/?q=place_id:{place_id}" if place_id else None
        )
        return {
            **place,
            "restaurant_id": str(fallback_id) if fallback_id else None,
            "tabelog_url": None,
            "google_maps_url": fallback_maps_url,
            "name_ja": store_name,
            "tabelog_rating": None,
            "review_count": place.get("user_ratings_total"),
            "price_lunch": None,
            "price_dinner": None,
            "awards": [],
            "supports_paypay": None,
            "credit_cards": [],
            "payment_methods_desc": "現金為主 (建議備足日圓現鈔)",
            "sample_reviews": fallback_reviews,
            "rating_source": "Google",
        }

    async def _index_store_reviews(self, restaurant_id: Any, reviews: list[dict[str, Any]]) -> None:
        """非同步將 Google/Tabelog 評論向量化並寫入 review_embeddings (pgvector)"""
        if not reviews or not self.vector_repo or not self.embedding_service:
            return
        try:
            rid = UUID(str(restaurant_id))
            existing = await self.vector_repo.get_reviews_by_restaurant_id(rid)
            if existing:
                return

            sample_reviews = reviews[:3]
            texts = [
                r.get("text") or r.get("original_text", "")
                for r in sample_reviews
                if (r.get("text") or r.get("original_text"))
            ]
            if not texts:
                return

            embeddings = await self.embedding_service.get_embeddings_batch(texts)
            records = []
            for r, emb in zip(sample_reviews, embeddings):
                if any(emb):
                    records.append(
                        {
                            "restaurant_id": rid,
                            "review_text_ja": r.get("original_text") or r.get("text", ""),
                            "review_text_zh": r.get("text") or r.get("original_text", ""),
                            "embedding": emb,
                        }
                    )
            if records:
                await self.vector_repo.batch_insert_reviews(records)
                logger.info(f"Indexed {len(records)} review embeddings for {rid}")
        except Exception as e:
            logger.warning(f"Could not index review embeddings for {restaurant_id}: {e}")

    async def semantic_rerank_reviews(
        self,
        user_query: str,
        stores: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """依據使用者詢問之口感/氛圍，透過 pgvector Cosine 相似度檢索最契合的食評亮點"""
        if not self.embedding_service or not self.vector_repo or not stores:
            return stores

        try:
            query_vec = await self.embedding_service.get_embedding(user_query)
            if not any(query_vec):
                return stores

            valid_rids: list[UUID] = []
            store_map: dict[UUID, dict[str, Any]] = {}
            for s in stores:
                rid_str = s.get("restaurant_id")
                if rid_str:
                    try:
                        rid = UUID(str(rid_str))
                        valid_rids.append(rid)
                        store_map[rid] = s
                    except Exception:
                        pass

            if not valid_rids:
                return stores

            matches = await self.vector_repo.search_similar_reviews(
                query_embedding=query_vec,
                top_k=len(valid_rids),
                restaurant_ids=valid_rids,
            )

            for m in matches:
                rid_val = m.get("restaurant_id")
                if rid_val:
                    rid_obj = UUID(str(rid_val))
                    if rid_obj in store_map:
                        highlight = m.get("review_text_zh") or m.get("review_text_ja")
                        if highlight and not store_map[rid_obj].get("foodie_review_highlight"):
                            sim = m.get("similarity", 0.0)
                            store_map[rid_obj]["foodie_review_highlight"] = highlight
                            store_map[rid_obj]["review_similarity"] = sim

            return stores
        except Exception as e:
            logger.warning(f"Semantic reranking skipped: {e}")
            return stores

    def _calculate_foodie_score(self, store: dict[str, Any]) -> float:
        """計算單一店家老饕綜合重排得分 (Foodie Score)"""
        t_rating = store.get("tabelog_rating")
        g_rating = float(store.get("rating") or store.get("google_rating") or 3.5)
        dist_m = float(store.get("distance_meters") or 300)
        awards = store.get("awards") or []
        hyakumeiten = store.get("hyakumeiten") or []
        rev_count = int(store.get("review_count") or 0)

        # 1. 基礎評分 (Tabelog 優先，若無則降權折算上限 3.05)
        if t_rating and float(t_rating) > 0:
            base_score = float(t_rating)
        else:
            base_score = min(g_rating * 0.65, 3.05)  # Google 評分降權折算，確保 Tabelog 高分店絕對優先

        # 2. 老饕榮譽加分
        award_bonus = 0.0
        if awards or hyakumeiten:
            award_bonus += 0.15
        if rev_count >= 500:
            award_bonus += 0.05

        # 3. 距離懲罰
        dist_penalty = 0.0
        if dist_m > 1500:
            dist_penalty = 0.12
        elif dist_m > 800:
            dist_penalty = 0.05

        return base_score + award_bonus - dist_penalty

    def rank_and_select_top_stores(
        self,
        stores: list[dict[str, Any]],
        top_k: int = 3,
    ) -> list[dict[str, Any]]:
        """依老饕綜合得分降序重排，並進行嚴格去重 (Deduplication)，選取 Top K 高分店家，注入排名徽章"""
        if not stores:
            return []

        ranked = sorted(stores, key=self._calculate_foodie_score, reverse=True)

        deduped: list[dict[str, Any]] = []
        seen_tabelog_urls: set[str] = set()
        seen_names: set[str] = set()

        for s in ranked:
            t_url = s.get("tabelog_url")
            raw_name = s.get("name_ja") or s.get("name") or ""
            clean_name = re.sub(r"[\s\-_・，,（）()]+", "", raw_name).lower()

            if t_url and t_url in seen_tabelog_urls:
                continue
            if clean_name and clean_name in seen_names:
                continue

            if t_url:
                seen_tabelog_urls.add(t_url)
            if clean_name:
                seen_names.add(clean_name)

            deduped.append(s)
            if len(deduped) >= top_k:
                break

        top_stores = deduped
        rank_badges = ["🥇", "🥈", "🥉", "🏅", "🎖️"]
        for idx, s in enumerate(top_stores):
            s["rank_index"] = idx + 1
            s["rank_badge"] = rank_badges[idx] if idx < len(rank_badges) else f"#{idx + 1}"

        return top_stores

    async def execute_state_3_parallel_tabelog(
        self,
        places: list[dict[str, Any]],
        area_hint: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """State 3: 自適應漸進查驗 (Adaptive Early Stopping) + Tabelog 老饕綜合重排"""
        t0 = time.time()
        tier1_places = places[:4]
        tier2_places = places[4:6]

        # 1. Tier 1 平行查驗 (前 4 家高潛力種子)
        tier1_tasks = [self._enrich_single_place(p, area_hint=area_hint) for p in tier1_places]
        tier1_results = await asyncio.gather(*tier1_tasks)

        # 2. 品質門檻檢驗：若已有 >= 2 家達到 Tabelog 高分名店標準 (tabelog_rating >= 3.45 或百名店)
        high_score_count = sum(
            1
            for s in tier1_results
            if (s.get("tabelog_rating") and float(s["tabelog_rating"]) >= 3.45)
            or bool(s.get("awards"))
            or bool(s.get("hyakumeiten"))
        )

        final_enriched = list(tier1_results)

        # 3. 自適應決策：若品質不足且有 Tier 2 備選店家，啟動動態查驗；否則觸發 Early Stopping
        if high_score_count < 2 and tier2_places:
            logger.info(
                f"Tier 1 high-score stores count is {high_score_count} (<2). Triggering Tier 2 adaptive rescue for {len(tier2_places)} stores."
            )
            tier2_tasks = [self._enrich_single_place(p, area_hint=area_hint) for p in tier2_places]
            tier2_results = await asyncio.gather(*tier2_tasks)
            final_enriched.extend(tier2_results)
        else:
            logger.info(f"Tier 1 high-score count={high_score_count}. Early stopping satisfied, skipping Tier 2.")

        # 4. 老饕綜合加權重排，精選 Top 3
        top_stores = self.rank_and_select_top_stores(final_enriched, top_k=3)

        # 5. 為最終入選的 Top 店家平行豐富定向老饕食評 (Tabelog 口コミ絕對優先合約)
        async def _enrich_foodie_reviews(store: dict[str, Any]) -> None:
            tabelog_url = store.get("tabelog_url")
            tabelog_fetcher = getattr(self.tabelog_scraper, "fetch_tabelog_reviews", None)

            # 優先級合約：只要店家配對到 Tabelog，無條件平行檢索 Tabelog 口コミ
            if tabelog_url and callable(tabelog_fetcher):
                search_name = store.get("name_ja") or store.get("name") or ""
                tabelog_revs: list[dict[str, Any]] = []
                try:
                    res = tabelog_fetcher(search_name, max_reviews=5)
                    if asyncio.iscoroutine(res):
                        raw_revs = await res
                        if isinstance(raw_revs, list):
                            tabelog_revs = raw_revs
                    elif isinstance(res, list):
                        tabelog_revs = res
                except Exception as e:
                    logger.warning(f"Error fetching tabelog reviews for '{search_name}': {e}")

                if tabelog_revs and len(tabelog_revs) > 0:
                    # Tabelog 成功：全面覆蓋原本的 Google 評論作為首選核心
                    store["sample_reviews"] = tabelog_revs
                    first_text = tabelog_revs[0].get("text") if isinstance(tabelog_revs[0], dict) else None
                    if isinstance(first_text, str):
                        store["foodie_review_highlight"] = first_text
                    store["foodie_review_source"] = "tabelog"
                    rid = store.get("restaurant_id")
                    if rid:
                        asyncio.create_task(self._index_store_reviews(rid, tabelog_revs))
                    return

            # 若無 Tabelog 或 Tabelog 未抓到食評，回退檢查既有評論（如 Google 評論）
            existing_revs = store.get("sample_reviews") or []
            if existing_revs and isinstance(existing_revs, list) and isinstance(existing_revs[0], dict):
                src = existing_revs[0].get("source") or ("tabelog" if store.get("tabelog_url") else "google")
                store["foodie_review_source"] = src
                if not store.get("foodie_review_highlight"):
                    first_text = existing_revs[0].get("text") or existing_revs[0].get("original_text")
                    if isinstance(first_text, str):
                        store["foodie_review_highlight"] = first_text

        if top_stores:
            await asyncio.gather(*[_enrich_foodie_reviews(s) for s in top_stores])

        latency = int((time.time() - t0) * 1000)

        stores_input_summary = [
            {
                "name": p.get("name"),
                "phone": p.get("phone"),
                "google_rating": p.get("rating") or p.get("google_rating"),
            }
            for p in places
        ]

        stores_output_summary = [
            {
                "name_ja": s.get("name_ja") or s.get("name"),
                "tabelog_rating": s.get("tabelog_rating"),
                "review_count": s.get("review_count"),
                "price_lunch": s.get("price_lunch"),
                "price_dinner": s.get("price_dinner"),
                "supports_paypay": s.get("supports_paypay"),
                "credit_cards": s.get("credit_cards") or [],
                "awards": s.get("awards") or [],
                "tabelog_url": s.get("tabelog_url"),
                "sample_reviews": s.get("sample_reviews") or [],
                "rating_source": s.get("rating_source", "Tabelog"),
                "rank_badge": s.get("rank_badge"),
            }
            for s in top_stores
        ]

        await self.trace_logger.log_step(
            action_type="state_3_tabelog",
            tool_name="parallel_tabelog_enrichment",
            tool_input={"stores_count": len(places), "stores_searched": stores_input_summary},
            tool_output={"selected_count": len(top_stores), "aligned_stores": stores_output_summary},
            latency_ms=latency,
        )
        return top_stores

    def _render_template_fallback_card(
        self,
        stores: list[dict[str, Any]],
        cuisine: str,
        is_format_fallback: bool = False,
    ) -> str:
        """LLM 連線失敗或格式回退時的純程式碼資料模板保底"""
        badge = (
            f"🍱 **已為您精選「{cuisine or '熱門料理'}」指標推薦餐廳如下：**\n"
            if is_format_fallback
            else "⚠️ **AI 語意模型連線異常，已為您自動載入直出精準美食卡片：**\n"
        )
        lines = [badge]
        if not is_format_fallback:
            lines.append(f"精選「{cuisine or '熱門料理'}」指標餐廳如下：\n")
        for idx, s in enumerate(stores, 1):
            name = s.get("name_ja") or s.get("name") or "精選餐廳"
            rating_src = s.get("rating_source", "Google")
            t_rating = s.get("tabelog_rating")
            g_rating = s.get("rating") or s.get("google_rating")

            walk_desc = s.get("walking_desc")

            rank_badge = s.get("rank_badge") or (["🥇", "🥈", "🥉"][idx - 1] if idx <= 3 else f"#{idx}")
            lines.append(f"### {rank_badge} 精選推薦 {idx}：`{name}`")
            if walk_desc:
                lines.append(f"🚶 **步行距離**：{walk_desc}")
            if rating_src == "Tabelog" and t_rating:
                lines.append(f"⭐️ **Tabelog 評分**：{t_rating:.2f} 分 ({s.get('review_count', 0)} 則評價)")
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
                    s.get("google_maps_url")
                    or f"https://www.google.com/maps/place/?q=place_id:{s.get('google_place_id')}"
                )
                lines.append(f"🔗 [Google Maps 頁面]({g_url})")
            # 老饕短評 (透過 ReviewSanitizer 淨化雜訊並智慧合成，絕不輸出生硬營業時間/交通資訊)
            rev_txt, rev_badge = synthesize_store_recommendation_reason(s, cuisine=cuisine)
            lines.append(f"📍 **老饕推薦理由 ({rev_badge})**：{rev_txt}")
            lines.append("")

        if len(stores) < 3:
            lines.append(
                f"💡 **特派員提醒**：目前此範圍內精選出 {len(stores)} 家符合條件的名店；若想探索更多選擇，可隨時告訴我擴大搜尋半徑或換個商圈！\n"
            )

        return "\n".join(lines).strip()

    def _sanitize_card_ratings(self, card_text: str, stores: list[dict[str, Any]]) -> str:
        """Scoped Anchor Guardrail: 區塊錨點防偽護欄

        以店家為單位隔離切割 Markdown 區塊，透過前後雙向強關鍵字錨點精準校對與矯正數值，
        排除跨店串位與非評分數值誤傷，確保 Telegram 卡片上的評分 100% 與真實資料源一致。
        """
        if not card_text or not stores:
            return card_text

        try:
            # 1. 依序號/標題切割為獨立段落 (維持原文段落完整性)
            parts = re.split(r"(?=\n(?:###\s*\d+|\d+[\.、]|\*\*\d+[\.、]))", card_text)
            modified_parts: list[str] = []

            for part in parts:
                # 判斷這一段落屬於哪一家店
                matched_store: Optional[dict[str, Any]] = None
                for idx, s in enumerate(stores, 1):
                    name_ja = s.get("name_ja") or s.get("name") or ""
                    if (name_ja and name_ja in part) or re.search(
                        rf"(?:###\s*{idx}|\b{idx}[\.、]|\*\*{idx}[\.、])", part
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
            logger.warning(f"[Guardrail] Error during card sanitization: {e}. Keeping original card.", exc_info=True)
            return card_text

    async def execute_state_4_synthesis(
        self,
        stores: list[dict[str, Any]],
        cuisine: str,
        location_name: Optional[str],
    ) -> str:
        t0 = time.time()
        honest_notice = ""
        if len(stores) < 3:
            honest_notice = f"（特別提醒：目前此範圍內符合條件之高評價店家僅有 {len(stores)} 家，請誠實僅呈現這 {len(stores)} 家，絕對禁止無中生有自行腦補其他店家或搜尋連結，並請在特派員提醒中建議使用者若想看更多可擴大半徑或換個商圈）\n"

        prompt_content = (
            f"目標地點：{location_name or '指定區域'}\n美食類別：{cuisine or '熱門美食'}\n"
            f"候選餐廳總數：共 {len(stores)} 家 {honest_notice}\n候選餐廳詳細數據如下：\n"
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
            # 帶入食評亮點與明確標示來源 (Tabelog 口コミ / Google 評價 / 特派員推薦)
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

        messages = [
            {"role": "system", "content": get_recommendation_card_prompt()},
            {"role": "user", "content": prompt_content},
        ]

        try:
            content, model, tokens = await self.llm_client.call_text(messages)
            self._last_state_4_model = model
            self._last_state_4_tokens = tokens
            card_text = content.strip()
            if not card_text:
                raise ValueError("LLM returned empty card content")

            # 🛡️ 執行店名反引號代碼塊自動修復 (防止純格式小疏失誤殺整張卡片)
            card_text = repair_store_name_code_blocks(card_text, stores)

            # 🛡️ 執行 Scoped Anchor 毫秒級數值防偽校正護欄
            card_text = self._sanitize_card_ratings(card_text, stores)

            # 🛡️ 分級排版與真實性防禦 (Tiered Defense Guardrail)
            fatal_errors, minor_warnings = validate_card_tiered(card_text, stores)
            if fatal_errors:
                logger.warning(
                    f"[CardGuardrail] Fatal violation detected ({', '.join(fatal_errors)}). "
                    f"Falling back to deterministic template card."
                )
                self._last_state_4_model = "template_fallback"
                self._last_state_4_tokens = 0
                fallback_card = self._render_template_fallback_card(stores, cuisine, is_format_fallback=True)
                latency = int((time.time() - t0) * 1000)
                await self.trace_logger.log_step(
                    action_type="state_4_synthesis",
                    tool_name="card_synthesis_format_fallback",
                    tool_input={"stores_count": len(stores), "cuisine": cuisine, "fatal_errors": fatal_errors},
                    tool_output={"card_content": fallback_card, "length": len(fallback_card)},
                    latency_ms=latency,
                )
                return fallback_card

            if minor_warnings:
                logger.info(
                    f"[CardGuardrail] Minor violations detected ({', '.join(minor_warnings)}). "
                    f"Performing in-place surgical repair."
                )
                card_text = repair_card_in_place(card_text, stores)

            latency = int((time.time() - t0) * 1000)
            await self.trace_logger.log_step(
                action_type="state_4_synthesis",
                tool_name="card_synthesis",
                tool_input={"stores_count": len(stores), "cuisine": cuisine, "location": location_name},
                tool_output={"card_content": card_text, "length": len(card_text)},
                latency_ms=latency,
            )
            return card_text
        except Exception as e:
            logger.error(f"State 4 LLM card synthesis failed: {e}. Activating template fallback...")
            self._last_state_4_model = "template_fallback"
            self._last_state_4_tokens = 0
            fallback_card = self._render_template_fallback_card(stores, cuisine)
            latency = int((time.time() - t0) * 1000)
            await self.trace_logger.log_step(
                action_type="state_4_synthesis",
                tool_name="card_synthesis_fallback",
                tool_input={"stores_count": len(stores), "cuisine": cuisine, "error": str(e)},
                tool_output={"card_content": fallback_card, "length": len(fallback_card)},
                latency_ms=latency,
            )
            return fallback_card

    async def execute_follow_up(
        self,
        chat_id: Optional[int],
        parsed_intent: dict[str, Any],
        user_message: str,
    ) -> str:
        """情境 4: 追問前一輪推薦店家之專屬解答 (委派至 stages.intent)"""
        return await stage_execute_follow_up(self, chat_id, parsed_intent, user_message)

    async def run_pipeline(
        self,
        chat_id: Optional[int],
        user_message: str,
        user_location: Optional[tuple[float, float]] = None,
        conversation_history: Optional[list[dict[str, str]]] = None,
        on_progress: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> str:
        """FSM 狀態機執行主入口"""
        await self.trace_logger.start_run(chat_id=chat_id, user_query=user_message)

        try:
            # 1. State 1: 意圖與座標鎖定 (支援 chat_id 繼承上一輪座標)
            parsed = await self.execute_state_1_intent(
                user_message, user_location, conversation_history, chat_id=chat_id
            )
            intent = parsed.get("intent", "search")

            # 分支 A: 純閒聊 / 非美食問題
            if intent == "chitchat":
                messages = [
                    {
                        "role": "system",
                        "content": "你是日本美食特派員 PickForMe。請親切熱情地回應用戶，並引導他們告訴你想在哪個日本城市或車站尋找美食。",
                    },
                    {"role": "user", "content": user_message},
                ]
                t0 = time.time()
                reply, model, tokens = await self.llm_client.call_text(messages)
                chitchat_res = reply.strip()
                latency = int((time.time() - t0) * 1000)
                await self.trace_logger.log_step(
                    action_type="chitchat",
                    tool_name="llm_chitchat",
                    tool_input={"message": user_message},
                    tool_output={"card_content": chitchat_res, "length": len(chitchat_res)},
                    latency_ms=latency,
                )
                await self.trace_logger.finish_run(status="completed", model_used=model, total_tokens=tokens)
                return chitchat_res

            # 分支 B: 缺乏座標與地名
            if intent == "need_location":
                await self.trace_logger.finish_run(status="completed")
                return "🍱 收到！請問您想在哪個城市、車站或地標附近用餐呢？（例如輸入「新宿御苑」或點擊下方 📎 傳送您的即時位置）"

            # 分支 C: 追問上一輪名單
            if intent == "follow_up":
                answer = await self.execute_follow_up(chat_id, parsed, user_message)
                await self.trace_logger.finish_run(
                    status="completed",
                    model_used=self._last_state_4_model,
                    total_tokens=self._last_state_4_tokens,
                )
                return answer

            # 分支 D: 正式美食推薦 (search / more)
            coords = parsed.get("coords")
            if not coords:
                return "未能識別您的目標座標，請提供日本城市或車站名稱。"

            lat, lng = coords
            cuisine = parsed.get("cuisine") or "熱門美食"
            loc_name = parsed.get("location")
            modifier = parsed.get("modifier")

            # 結構化搜尋約束解析：解耦料理核心詞、物理半徑階梯與風格修飾語 (杜絕關鍵字污染)
            search_keyword, radii_ladder, style_tag = resolve_search_constraints(cuisine=cuisine, modifier=modifier)

            # 若為換一批 (intent: more)，取得過去累積看過的所有店家 ID 進行黑名單過濾
            exclude_ids: set[str] = set()
            if intent == "more" and chat_id:
                try:
                    exclude_ids = await self.session_repo.get_seen_place_ids(chat_id)
                except Exception as e:
                    logger.warning(f"Failed to load seen place ids: {e}")

            # 方案 1A 二段式進度更新 (進度 1)
            if on_progress:
                target_desc = f"「{loc_name}」" if loc_name else "您目前位置"
                if intent == "more":
                    await on_progress(f"🔄 正在為您排除已看過店家，於 {target_desc} 挖掘下一梯隊高評價名店...")
                else:
                    await on_progress(f"🧭 正在為您鎖定 {target_desc} 並檢索週邊熱門美食與食べログ評分...")

            # 2. State 2: 候選店家智慧階梯檢索 (含去重排除與自適應擴圈)
            candidates = await self.execute_state_2_places_ladder(
                lat=lat,
                lng=lng,
                keyword=search_keyword,
                origin_name=loc_name,
                exclude_place_ids=exclude_ids,
                custom_radii=radii_ladder,
            )
            if not candidates:
                await self.trace_logger.finish_run(status="completed")
                return f"抱歉，在 {loc_name or '該地點'} 週邊暫時未檢索到更多符合「{search_keyword}」的高評價餐廳，建議可更換地標或料理關鍵字再次搜尋。"

            # 3. State 3: 3 店家平行 Tabelog 查驗
            enriched_stores = await self.execute_state_3_parallel_tabelog(candidates, area_hint=loc_name)

            # 3.5 pgvector 語意重排與食評亮點萃取 (RAG)
            rerank_query = f"{user_message} {modifier}" if modifier else user_message
            enriched_stores = await self.semantic_rerank_reviews(rerank_query, enriched_stores)

            # 方案 1A 二段式進度更新 (進度 2 - 準備寫卡片)
            if on_progress:
                await on_progress(f"✍️ 已精選 {len(enriched_stores)} 家高評價指標餐廳，正在為您生成專屬推薦卡片...")

            # 4. State 4: 單輪卡片生成
            final_card = await self.execute_state_4_synthesis(
                stores=enriched_stores,
                cuisine=search_keyword,
                location_name=loc_name,
            )

            # 保存推薦店家至 Session 以支援後續追問與累積去重
            if chat_id:
                try:
                    await self.session_repo.update_recommended_ids(
                        chat_id=chat_id,
                        recommended_ids=enriched_stores,
                        accumulate=(intent == "more"),
                    )
                except Exception as e:
                    logger.warning(f"Could not persist recommended stores to session: {e}")

            await self.trace_logger.finish_run(
                status="completed",
                model_used=self._last_state_4_model,
                total_tokens=self._last_state_4_tokens,
            )
            return final_card

        except Exception as e:
            logger.error(f"FSM Pipeline execution error: {e}", exc_info=True)
            await self.trace_logger.finish_run(status="failed")
            return f"抱歉，美食推薦管線處理時遭遇問題：{str(e)}"
