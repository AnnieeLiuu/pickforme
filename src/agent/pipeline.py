import inspect
import logging
import time
from typing import Any, Awaitable, Callable, Optional

from src.agent.llm_client import LLMClient
from src.agent.schemas import FSMRunContext
from src.agent.stages.enrichment import (
    apply_deterministic_constraints,
    calculate_foodie_score,
    enrich_single_place,
    format_payment_methods,
    index_store_reviews,
    rank_and_select_top_stores,
)
from src.agent.stages.enrichment import (
    execute_state_3_parallel_tabelog as stage_execute_state_3_parallel_tabelog,
)
from src.agent.stages.enrichment import (
    semantic_rerank_reviews as stage_semantic_rerank_reviews,
)
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
from src.agent.stages.places_search import (
    execute_state_2_places_ladder as stage_execute_state_2_places_ladder,
)
from src.agent.stages.places_search import (
    prefilter_potential_foodie_places,
    resolve_search_constraints,
)
from src.agent.stages.synthesis import (
    AGENT_SYSTEM_PROMPT_PATH,
    RECOMMENDATION_CARD_SYSTEM_PROMPT,
    get_recommendation_card_prompt,
    render_template_fallback_card,
    sanitize_card_ratings,
)
from src.agent.stages.synthesis import (
    execute_state_4_synthesis as stage_execute_state_4_synthesis,
)
from src.agent.trace import AgentTraceLogger
from src.db.repositories.restaurant import RestaurantRepository
from src.db.repositories.session import SessionRepository
from src.db.repositories.specialty_guide import SpecialtyGuideRepository
from src.db.repositories.vector import VectorRepository
from src.services.cuisine_guide import LocalSpecialtyGuideService
from src.services.embedding import EmbeddingService
from src.services.entity_resolver import EntityResolver
from src.services.google_places import GooglePlacesClient
from src.services.tabelog_scraper import TabelogScraper

logger = logging.getLogger(__name__)

__all__ = [
    "FoodRecommendationFSM",
    "INTENT_PARSER_SYSTEM_PROMPT",
    "RECOMMENDATION_CARD_SYSTEM_PROMPT",
    "AGENT_SYSTEM_PROMPT_PATH",
    "resolve_search_constraints",
    "format_payment_methods",
    "get_recommendation_card_prompt",
]


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
        cuisine_guide_service: Optional[LocalSpecialtyGuideService] = None,
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
        self.cuisine_guide_service = cuisine_guide_service or LocalSpecialtyGuideService(
            guide_repo=SpecialtyGuideRepository()
        )
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
        """Google 端老饕潛力智慧預篩 (委派至 stages.places_search)"""
        return prefilter_potential_foodie_places(places, max_seeds=max_seeds)

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
        """State 2: Google Places 智慧階梯檢索 (委派至 stages.places_search)"""
        return await stage_execute_state_2_places_ladder(
            self,
            lat,
            lng,
            keyword,
            origin_name=origin_name,
            exclude_place_ids=exclude_place_ids,
            max_candidates=max_candidates,
            custom_radii=custom_radii,
        )

    async def _enrich_single_place(self, place: dict[str, Any], area_hint: Optional[str] = None) -> dict[str, Any]:
        """對單一店家補全 Tabelog 評分 (委派至 stages.enrichment)"""
        return await enrich_single_place(self, place, area_hint=area_hint)

    async def _index_store_reviews(self, restaurant_id: Any, reviews: list[dict[str, Any]]) -> None:
        """非同步將評論向量化 (委派至 stages.enrichment)"""
        return await index_store_reviews(self, restaurant_id, reviews)

    async def semantic_rerank_reviews(self, user_query: str, stores: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """依據口感/氛圍語意重排 (委派至 stages.enrichment)"""
        return await stage_semantic_rerank_reviews(self, user_query, stores)

    def _calculate_foodie_score(self, store: dict[str, Any]) -> float:
        """計算單一店家老饕綜合重排得分 (委派至 stages.enrichment)"""
        return calculate_foodie_score(store)

    def rank_and_select_top_stores(self, stores: list[dict[str, Any]], top_k: int = 3) -> list[dict[str, Any]]:
        """依老饕綜合得分降序重排與去重 (委派至 stages.enrichment)"""
        return rank_and_select_top_stores(stores, top_k=top_k)

    async def execute_state_3_parallel_tabelog(
        self, places: list[dict[str, Any]], area_hint: Optional[str] = None
    ) -> list[dict[str, Any]]:
        """State 3: 自適應漸進查驗 + Tabelog 老饕綜合重排 (委派至 stages.enrichment)"""
        return await stage_execute_state_3_parallel_tabelog(self, places, area_hint=area_hint)

    def _render_template_fallback_card(
        self,
        stores: list[dict[str, Any]],
        cuisine: str,
        is_format_fallback: bool = False,
        category: Optional[str] = None,
    ) -> str:
        """LLM 連線失敗或格式回退時的純程式碼資料模板保底 (委派至 stages.synthesis)"""
        return render_template_fallback_card(stores, cuisine, is_format_fallback=is_format_fallback, category=category)

    def _sanitize_card_ratings(self, card_text: str, stores: list[dict[str, Any]]) -> str:
        """Scoped Anchor Guardrail (委派至 stages.synthesis)"""
        return sanitize_card_ratings(card_text, stores)

    async def execute_state_4_synthesis(
        self,
        stores: list[dict[str, Any]],
        cuisine: str,
        location_name: Optional[str],
        category: Optional[str] = None,
    ) -> str:
        """State 4: 推薦卡片排版合成與護欄校驗 (委派至 stages.synthesis)"""
        return await stage_execute_state_4_synthesis(self, stores, cuisine, location_name, category=category)

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

            # 分支 0: QuickPassGate 0ms 直接秒回 (問候/道謝/健康檢查/安全絆線)
            if intent == "fast_reply":
                await self.trace_logger.finish_run(
                    status="completed",
                    model_used="quick_pass_gate",
                    total_tokens=0,
                )
                return str(parsed.get("fast_reply", ""))

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

            # 分支 C2: 探索當地特色美食與飲食文化導覽 (explore_cuisine)
            if intent == "explore_cuisine":
                explore_loc = str(parsed.get("location") or "").strip()
                explore_cat = str(parsed.get("explore_category") or "food").strip().lower()
                if explore_cat not in ("food", "drink", "souvenir"):
                    explore_cat = "food"
                t0 = time.time()
                guide = await self.cuisine_guide_service.get_area_guide(
                    area_name=explore_loc,
                    category=explore_cat,  # type: ignore[arg-type]
                    llm_client=self.llm_client,
                )
                guide_card = self.cuisine_guide_service.render_guide_markdown(guide)
                latency = int((time.time() - t0) * 1000)

                if chat_id:
                    try:
                        upd_wm_fn = getattr(self.session_repo, "update_working_memory", None)
                        if callable(upd_wm_fn):
                            res = upd_wm_fn(
                                chat_id=chat_id,
                                last_location=guide.area_name,
                                suggested_cuisines=[item.get_search_keyword() for item in guide.specialties],
                                last_explore_category=guide.category,
                            )
                            if inspect.isawaitable(res):
                                await res
                    except Exception as e:
                        logger.warning(f"Failed to persist explore_cuisine working memory: {e}")

                await self.trace_logger.log_step(
                    action_type="explore_cuisine",
                    tool_name="local_specialty_guide",
                    tool_input={"area": explore_loc, "category": guide.category, "source_tier": guide.source_tier},
                    tool_output={
                        "area_name": guide.area_name,
                        "category": guide.category,
                        "specialties": [item.cuisine_name for item in guide.specialties],
                        "length": len(guide_card),
                    },
                    latency_ms=latency,
                )
                await self.trace_logger.finish_run(
                    status="completed",
                    model_used=f"cuisine_guide:{guide.source_tier}",
                    total_tokens=0,
                )
                return guide_card

            # 分支 D: 正式美食推薦 (search / more)
            coords = parsed.get("coords")
            if not coords:
                return "未能識別您的目標座標，請提供日本城市或車站名稱。"

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

            preferences: dict[str, Any] = {}
            if chat_id:
                try:
                    prefs_fn = getattr(self.session_repo, "get_long_term_preferences", None)
                    if callable(prefs_fn):
                        prefs_res = prefs_fn(chat_id)
                        prefs_val = await prefs_res if inspect.isawaitable(prefs_res) else prefs_res
                        if isinstance(prefs_val, dict):
                            preferences = prefs_val
                except Exception as e:
                    logger.warning(f"Failed to load long term preferences: {e}")

            # 封裝強型別執行期上下文容器 (FSMRunContext)
            run_ctx = FSMRunContext(
                chat_id=chat_id,
                user_message=user_message,
                user_location=user_location,
                intent=intent,
                explore_category=parsed.get("explore_category"),
                location_name=loc_name,
                coords=(float(coords[0]), float(coords[1])),
                cuisine=cuisine,
                modifier=modifier,
                search_keyword=search_keyword,
                radii_ladder=radii_ladder,
                style_tag=style_tag,
                exclude_place_ids=exclude_ids,
                long_term_preferences=preferences,
            )
            lat, lng = run_ctx.coords  # type: ignore[misc]

            # 方案 1A 二段式進度更新 (進度 1)
            if on_progress:
                target_desc = f"「{run_ctx.location_name}」" if run_ctx.location_name else "您目前位置"
                if run_ctx.intent == "more":
                    await on_progress(f"🔄 正在為您排除已看過店家，於 {target_desc} 挖掘下一梯隊高評價名店...")
                else:
                    await on_progress(f"🧭 正在為您鎖定 {target_desc} 並檢索週邊熱門美食與食べログ評分...")

            # 2. State 2: 候選店家智慧階梯檢索 (含去重排除與自適應擴圈)
            candidates = await self.execute_state_2_places_ladder(
                lat=lat,
                lng=lng,
                keyword=run_ctx.search_keyword,
                origin_name=run_ctx.location_name,
                exclude_place_ids=run_ctx.exclude_place_ids,
                custom_radii=run_ctx.radii_ladder,
            )
            if not candidates:
                await self.trace_logger.finish_run(status="completed")
                return f"抱歉，在 {run_ctx.location_name or '該地點'} 週邊暫時未檢索到更多符合「{run_ctx.search_keyword}」的高評價餐廳，建議可更換地標或料理關鍵字再次搜尋。"

            # 3. State 3: 3 店家平行 Tabelog 查驗
            enriched_stores = await self.execute_state_3_parallel_tabelog(candidates, area_hint=run_ctx.location_name)

            # 3.2 Deterministic Constraint Sensor: 根據當輪 modifier 與長期偏好進行硬性條件降權與重排
            enriched_stores = apply_deterministic_constraints(
                enriched_stores,
                modifier=run_ctx.modifier,
                preferences=run_ctx.long_term_preferences,
            )
            if any(s.get("constraint_violation") is True for s in enriched_stores):
                enriched_stores = self.rank_and_select_top_stores(enriched_stores, top_k=len(enriched_stores))

            # 3.5 pgvector 語意重排與食評亮點萃取 (RAG)
            rerank_query = f"{run_ctx.user_message} {run_ctx.modifier}" if run_ctx.modifier else run_ctx.user_message
            enriched_stores = await self.semantic_rerank_reviews(rerank_query, enriched_stores)

            # 方案 1A 二段式進度更新 (進度 2 - 準備寫卡片)
            if on_progress:
                cat_word = "名店" if run_ctx.explore_category in ("drink", "souvenir") else "餐廳"
                await on_progress(
                    f"✍️ 已精選 {len(enriched_stores)} 家高評價指標{cat_word}，正在為您生成專屬推薦卡片..."
                )

            # 4. State 4: 單輪卡片生成
            final_card = await self.execute_state_4_synthesis(
                stores=enriched_stores,
                cuisine=run_ctx.search_keyword,
                location_name=run_ctx.location_name,
                category=run_ctx.explore_category,
            )

            # 保存推薦店家與 Working Memory 至 Session 以支援後續追問與累積去重
            if run_ctx.chat_id:
                try:
                    await self.session_repo.update_recommended_ids(
                        chat_id=run_ctx.chat_id,
                        recommended_ids=enriched_stores,
                        accumulate=(run_ctx.intent == "more"),
                        last_cuisine=run_ctx.search_keyword,
                        last_location=run_ctx.location_name,
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
