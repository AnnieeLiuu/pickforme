import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from src.agent.llm_client import LLMClient
from src.agent.trace import AgentTraceLogger
from src.db.repositories.restaurant import RestaurantRepository
from src.db.repositories.session import SessionRepository
from src.services.entity_resolver import EntityResolver
from src.services.google_places import GooglePlacesClient
from src.services.tabelog_scraper import TabelogScraper

logger = logging.getLogger(__name__)

INTENT_PARSER_SYSTEM_PROMPT = """你是一位嚴格的日本美食搜尋意圖解析器。請分析使用者的輸入與上下文，並輸出標準 JSON 格式。

可能的 intent 欄位值：
1. "search": 使用者想尋找美食餐廳。請精準抽取 location (地名/車站/商圈/景點，若未提及請設為 null) 與 cuisine (料理類別/關鍵字，若未指定請設為 "熱門人氣美食")。
2. "follow_up": 使用者針對上一輪推薦的店家追問細節 (例如: "第2家有開嗎", "第一家多少錢", "有沒有信用卡")。請抽取 target_index (整數 1, 2 或 3) 與 question。
3. "chitchat": 純打招呼、禮貌問候、詢問系統功能或非美食主題。
4. "need_location": 使用者想吃特定食物但未提供任何地名，且系統上下文無 GPS。

輸出 JSON 結構規範：
{
  "intent": "search" | "follow_up" | "chitchat" | "need_location",
  "location": string | null,
  "cuisine": string | null,
  "target_index": number | null,
  "question": string | null
}
只輸出純 JSON 字串，不要包含任何多餘文字。"""

RECOMMENDATION_CARD_SYSTEM_PROMPT = """你是一位專業、熱情的日本美食特派員「PickForMe (美食導航員)」，請依據所提供的候選店家完整資料，以繁體中文為使用者排版生成引人入勝、專業的美食推薦卡片。

排版規範：
1. 請依據候選資料推薦 3 ~ 5 家精選餐廳（預設呈現 3 ~ 4 家差異化風格名店，如歷史老舖、平價排隊名店、特色專門店）。
2. 每家餐廳必須包含：
   - 🍱 **店名**：請務必將「日文原名」單獨放在 Code 區塊中（例如：`寿々喜家 本店`），方便 Telegram 一鍵點擊複製！
   - ⭐️ **評分與老饕評價（嚴格遵守優先級）**：
     - 若有 Tabelog 評分：標註「⭐️ Tabelog X.XX 分 (XXX 則評價) / Google X.X」，且必須優先提煉 Tabelog 在地老饕對口感、湯頭、麵條、肉質或必點招牌小菜的深入點評！
     - 若 Tabelog 暫無資料：標註「⭐️ Google X.X 分 (XXX 則評價) ｜ Tabelog 暫無評分」，並提煉 Google 網友的普遍好評亮點（排隊熱度、招牌特色）。
   - 🏆 **獲獎/支付**：若有「百名店/Award」或支援「PayPay / 信用卡」請重點標註。
   - 💰 **預算區間**：午餐/晚餐預算。
   - 📍 **簡短特色與老饕推薦理由**：招牌菜色與口感層次。
   - 🔗 **店家連結**：附上 Google 地圖或 Tabelog 連結。
3. 寬鬆查詢之收斂引導：若查詢品類範圍較廣（如新宿拉麵、燒肉等選擇極多），請在卡片末尾貼心附上 1~2 句縮小範圍引導（例如：「💡 該區域拉麵流派眾多！若您偏好特定口味（如濃郁豚骨、清爽魚介、或想找支援刷卡的店家），可直接告訴我為您即時收斂！」）。

請保持親切、專業、條理分明。直接輸出 Markdown 卡片內容。"""


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
    ):
        self.llm_client = llm_client or LLMClient()
        self.places_client = places_client or GooglePlacesClient()
        self.tabelog_scraper = tabelog_scraper or TabelogScraper()
        self.entity_resolver = entity_resolver or EntityResolver()
        self.restaurant_repo = restaurant_repo or RestaurantRepository()
        self.session_repo = session_repo or SessionRepository()
        self.trace_logger = trace_logger or AgentTraceLogger()

    async def execute_state_1_intent(
        self,
        user_message: str,
        user_location: Optional[tuple[float, float]],
        conversation_history: Optional[list[dict[str, str]]],
    ) -> dict[str, Any]:
        """State 1: 0.3s 微型 LLM 結構化意圖與座標解析"""
        messages: list[dict[str, Any]] = [{"role": "system", "content": INTENT_PARSER_SYSTEM_PROMPT}]

        # 帶入前輪簡短上下文
        if conversation_history:
            for item in conversation_history[-2:]:
                r = item.get("role", "user")
                c = item.get("content", "")
                if c:
                    messages.append({"role": r, "content": c})

        messages.append({"role": "user", "content": user_message})

        t0 = time.time()
        parsed, model, tokens = await self.llm_client.call_structured_json(messages)
        latency = int((time.time() - t0) * 1000)

        await self.trace_logger.log_step(
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
            resolved_coords = await self.places_client.geocode_location(loc_str)

        # 若無地名但使用者有帶 GPS
        if not resolved_coords and user_location:
            resolved_coords = user_location

        # 若美食搜尋既無地標也無座標
        if intent == "search" and not resolved_coords:
            parsed["intent"] = "need_location"

        parsed["coords"] = resolved_coords
        return parsed

    async def execute_state_2_places_ladder(
        self,
        lat: float,
        lng: float,
        keyword: str,
    ) -> list[dict[str, Any]]:
        """State 2: Google Places 智慧階梯檢索 (800m ➔ 1200m ➔ 1800m)"""
        t0 = time.time()
        candidates: list[dict[str, Any]] = []

        radii = [800, 1200, 1800]
        for r in radii:
            raw_places = await self.places_client.search_nearby(lat=lat, lng=lng, radius=r, keyword=keyword)
            # 過濾重複
            existing_ids = {c["google_place_id"] for c in candidates}
            for p in raw_places:
                if p.get("google_place_id") and p["google_place_id"] not in existing_ids:
                    candidates.append(p)
                    existing_ids.add(p["google_place_id"])

            if len(candidates) >= 3:
                break

        # 最多取前 3~5 家優質候選 (預設精選 4 家)
        final_candidates = candidates[:4]
        latency = int((time.time() - t0) * 1000)

        await self.trace_logger.log_step(
            action_type="state_2_places",
            tool_name="search_nearby_ladder",
            tool_input={"lat": lat, "lng": lng, "keyword": keyword},
            tool_output={"count": len(final_candidates)},
            latency_ms=latency,
        )
        return final_candidates

    async def _enrich_single_place(self, place: dict[str, Any]) -> dict[str, Any]:
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

            if is_fresh:
                return {
                    **place,
                    "tabelog_url": cached.get("tabelog_url"),
                    "name_ja": cached.get("name_ja") or store_name,
                    "tabelog_rating": float(cached["tabelog_rating"]) if cached.get("tabelog_rating") else None,
                    "review_count": cached.get("review_count"),
                    "price_lunch": cached.get("price_lunch"),
                    "price_dinner": cached.get("price_dinner"),
                    "awards": cached.get("awards") or [],
                    "supports_paypay": cached.get("supports_paypay"),
                    "rating_source": "Tabelog",
                }

        # 2. 未命中快取：執行即時爬蟲與比對
        try:
            candidates = await self.tabelog_scraper.search_tabelog_candidates(store_name=store_name)
            match_res = await self.entity_resolver.resolve_entity(
                google_store=place,
                tabelog_candidates=candidates,
                allow_llm=False,  # 0 API 消耗，嚴守速度
            )

            if match_res and match_res.get("match_found") and match_res.get("candidate"):
                cand = match_res["candidate"]
                tabelog_url = cand.get("tabelog_url", "")
                details = await self.tabelog_scraper.scrape_restaurant_details(tabelog_url)
                rating = details.get("rating") or cand.get("rating")
                rev_count = details.get("review_count") or cand.get("review_count")

                # 存入快取
                try:
                    await self.restaurant_repo.save_restaurant_with_tabelog(
                        google_store=place,
                        tabelog_data={
                            **details,
                            "tabelog_url": tabelog_url,
                            "name_ja": cand.get("name_ja") or store_name,
                        },
                        match_confidence=match_res.get("confidence", 0.9),
                        match_method=match_res.get("match_method", "rule_high"),
                    )
                except Exception as e:
                    logger.warning(f"Failed to upsert tabelog cache: {e}")

                return {
                    **place,
                    "tabelog_url": tabelog_url,
                    "name_ja": cand.get("name_ja") or store_name,
                    "tabelog_rating": rating,
                    "review_count": rev_count,
                    "price_lunch": details.get("price_lunch"),
                    "price_dinner": details.get("price_dinner"),
                    "awards": details.get("awards") or cand.get("awards") or [],
                    "supports_paypay": details.get("supports_paypay"),
                    "rating_source": "Tabelog",
                }
        except Exception as e:
            logger.warning(f"Tabelog scraping / resolution failed for {store_name}: {e}")

        # 3. 爬蟲失敗或無資料保底：以 Google 評分呈現並透明標註
        return {
            **place,
            "tabelog_url": None,
            "name_ja": store_name,
            "tabelog_rating": None,
            "review_count": place.get("user_ratings_total"),
            "price_lunch": None,
            "price_dinner": None,
            "awards": [],
            "supports_paypay": None,
            "rating_source": "Google",
        }

    async def execute_state_3_parallel_tabelog(
        self,
        places: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """State 3: 3 店家平行 Tabelog 查驗 (asyncio.gather 並行加速)"""
        t0 = time.time()
        tasks = [self._enrich_single_place(p) for p in places]
        enriched_results = await asyncio.gather(*tasks)
        latency = int((time.time() - t0) * 1000)

        await self.trace_logger.log_step(
            action_type="state_3_tabelog",
            tool_name="parallel_tabelog_enrichment",
            tool_input={"stores_count": len(places)},
            tool_output={"enriched_count": len(enriched_results)},
            latency_ms=latency,
        )
        return list(enriched_results)

    def _render_template_fallback_card(self, stores: list[dict[str, Any]], cuisine: str) -> str:
        """LLM 連線失敗時的純程式碼資料模板保底"""
        lines = [
            "⚠️ **AI 語意模型連線異常，已為您自動載入直出精準美食卡片：**\n",
            f"精選「{cuisine or '熱門料理'}」指標餐廳如下：\n",
        ]
        for idx, s in enumerate(stores, 1):
            name = s.get("name_ja") or s.get("name") or "精選餐廳"
            rating_src = s.get("rating_source", "Google")
            t_rating = s.get("tabelog_rating")
            g_rating = s.get("rating") or s.get("google_rating")

            lines.append(f"### {idx}. `{name}`")
            if rating_src == "Tabelog" and t_rating:
                lines.append(f"⭐️ **Tabelog 評分**：{t_rating:.2f} 分 ({s.get('review_count', 0)} 則評價)")
            else:
                lines.append(f"⭐️ **評分資訊**：Google {g_rating or '4.0'} 分 (Tabelog 暫無評分，以 Google 評價為依據)")

            if s.get("price_dinner"):
                lines.append(f"💰 **預算**：晚餐 {s.get('price_dinner')}")
            if s.get("address"):
                lines.append(f"📍 **地址**：{s.get('address')}")
            if s.get("tabelog_url"):
                lines.append(f"🔗 [食べログ詳細頁面]({s.get('tabelog_url')})")
            lines.append("")

        return "\n".join(lines).strip()

    async def execute_state_4_synthesis(
        self,
        stores: list[dict[str, Any]],
        cuisine: str,
        location_name: Optional[str],
    ) -> str:
        """State 4: 單輪 LLM 卡片生成 (含 LLM 異常時的純模板保底)"""
        t0 = time.time()
        prompt_content = (
            f"目標地點：{location_name or '指定區域'}\n美食類別：{cuisine or '熱門美食'}\n\n候選餐廳詳細數據如下：\n"
        )
        for idx, s in enumerate(stores, 1):
            prompt_content += (
                f"\n店家 {idx}：\n"
                f"- 店名(日文)：{s.get('name_ja') or s.get('name')}\n"
                f"- 評分來源：{s.get('rating_source')}\n"
                f"- Tabelog 評分：{s.get('tabelog_rating')}\n"
                f"- Google 評分：{s.get('google_rating') or s.get('rating')}\n"
                f"- 評價人數：{s.get('review_count') or s.get('user_ratings_total')}\n"
                f"- 預算(午/晚)：{s.get('price_lunch')} / {s.get('price_dinner')}\n"
                f"- 獲獎紀錄：{s.get('awards')}\n"
                f"- PayPay支援：{s.get('supports_paypay')}\n"
                f"- 地址：{s.get('address')}\n"
                f"- Tabelog網址：{s.get('tabelog_url')}\n"
            )

        messages = [
            {"role": "system", "content": RECOMMENDATION_CARD_SYSTEM_PROMPT},
            {"role": "user", "content": prompt_content},
        ]

        try:
            content, model, tokens = await self.llm_client.call_text(messages)
            card_text = content.strip()
            if not card_text:
                raise ValueError("LLM returned empty card content")

            latency = int((time.time() - t0) * 1000)
            await self.trace_logger.log_step(
                action_type="state_4_synthesis",
                tool_name="card_synthesis",
                tool_input={"stores_count": len(stores)},
                tool_output={"length": len(card_text)},
                latency_ms=latency,
            )
            return card_text
        except Exception as e:
            logger.error(f"State 4 LLM card synthesis failed: {e}. Activating template fallback...")
            return self._render_template_fallback_card(stores, cuisine)

    async def execute_follow_up(
        self,
        chat_id: Optional[int],
        parsed_intent: dict[str, Any],
        user_message: str,
    ) -> str:
        """情境 4: 追問前一輪推薦店家之專屬解答"""
        if not chat_id:
            return "抱歉，無法載入上一輪推薦的店家記憶。"

        session = await self.session_repo.get_session(chat_id)
        if not session or not session.get("last_recommended_ids"):
            return "抱歉，目前找不到您上一輪詢問的推薦紀錄，請問您今天想在何處吃什麼美食呢？"

        recommended_stores = session.get("last_recommended_ids") or []
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
            reply, _, _ = await self.llm_client.call_text(messages)
            return reply.strip()
        except Exception as e:
            logger.warning(f"Follow up LLM call failed: {e}")
            return f"針對 `{store_name}` 的問題：該店評分為 {target_store.get('tabelog_rating') or target_store.get('rating')} 分，地址在 {target_store.get('address', '市區')}。"

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
            # 1. State 1: 意圖與座標鎖定
            parsed = await self.execute_state_1_intent(user_message, user_location, conversation_history)
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
                reply, model, tokens = await self.llm_client.call_text(messages)
                await self.trace_logger.finish_run(status="completed", model_used=model, total_tokens=tokens)
                return reply.strip()

            # 分支 B: 缺乏座標與地名
            if intent == "need_location":
                await self.trace_logger.finish_run(status="completed")
                return "🍱 收到！請問您想在哪個城市、車站或地標附近用餐呢？（例如輸入「新宿御苑」或點擊下方 📎 傳送您的即時位置）"

            # 分支 C: 追問上一輪名單
            if intent == "follow_up":
                answer = await self.execute_follow_up(chat_id, parsed, user_message)
                await self.trace_logger.finish_run(status="completed")
                return answer

            # 分支 D: 正式美食推薦 (search)
            coords = parsed.get("coords")
            if not coords:
                return "未能識別您的目標座標，請提供日本城市或車站名稱。"

            lat, lng = coords
            cuisine = parsed.get("cuisine") or "熱門美食"
            loc_name = parsed.get("location")

            # 方案 1A 二段式進度更新 (進度 1)
            if on_progress:
                target_desc = f"「{loc_name}」" if loc_name else "您目前位置"
                await on_progress(f"🧭 正在為您鎖定 {target_desc} 並檢索週邊熱門美食與食べログ評分...")

            # 2. State 2: 候選店家智慧階梯檢索
            candidates = await self.execute_state_2_places_ladder(lat=lat, lng=lng, keyword=cuisine)
            if not candidates:
                await self.trace_logger.finish_run(status="completed")
                return f"抱歉，在 {loc_name or '該地點'} 週邊暫時未檢索到符合「{cuisine}」的高評價餐廳，建議可放寬關鍵字或擴大範圍再次搜尋。"

            # 3. State 3: 3 店家平行 Tabelog 查驗
            enriched_stores = await self.execute_state_3_parallel_tabelog(candidates)

            # 方案 1A 二段式進度更新 (進度 2 - 準備寫卡片)
            if on_progress:
                await on_progress("✍️ 已精選 3 家高評價指標餐廳，正在為您生成專屬推薦卡片...")

            # 4. State 4: 單輪卡片生成
            final_card = await self.execute_state_4_synthesis(
                stores=enriched_stores,
                cuisine=cuisine,
                location_name=loc_name,
            )

            # 保存推薦店家至 Session 以支援後續追問
            if chat_id:
                try:
                    await self.session_repo.update_recommended_ids(chat_id=chat_id, recommended_ids=enriched_stores)
                except Exception as e:
                    logger.warning(f"Could not persist recommended stores to session: {e}")

            await self.trace_logger.finish_run(status="completed")
            return final_card

        except Exception as e:
            logger.error(f"FSM Pipeline execution error: {e}", exc_info=True)
            await self.trace_logger.finish_run(status="failed")
            return f"抱歉，美食推薦管線處理時遭遇問題：{str(e)}"
