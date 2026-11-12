import asyncio
import logging
import re
from typing import Any, Optional

from src.agent.llm_client import LLMClient
from src.config import settings
from src.db.repositories.prewarm import PrewarmRepository
from src.db.repositories.restaurant import RestaurantRepository
from src.services.cuisine_guide import LocalSpecialtyGuideService
from src.services.entity_resolver import EntityResolver
from src.services.google_places import GooglePlacesClient
from src.services.tabelog_scraper import TabelogScraper
from telegram import Bot

logger = logging.getLogger(__name__)


def _escape_markdown(text: str) -> str:
    """跳脫 Telegram Markdown v1 特殊字元 (如 _、*、`、[、]) 避免格式錯誤"""
    return re.sub(r"([_*`\[\]])", r"\\\1", text)


def _extract_specialty_names(guide_obj: Any) -> str:
    """從導覽物件 (AreaSpecialtyGuide 或 dict) 提取前 3 個特色名稱並以頓號連結"""
    if not guide_obj:
        return ""
    specs = getattr(guide_obj, "specialties", None)
    if not specs and isinstance(guide_obj, dict):
        specs = guide_obj.get("specialties")
    if not specs:
        return ""
    names: list[str] = []
    for s in specs[:3]:
        if isinstance(s, dict):
            name = s.get("cuisine_name") or s.get("name_ja") or ""
        else:
            name = getattr(s, "cuisine_name", "") or getattr(s, "name_ja", "")
        if name:
            names.append(name)
    return "、".join(names)


class PrewarmWorker:
    def __init__(
        self,
        prewarm_repo: Optional[PrewarmRepository] = None,
        places_client: Optional[GooglePlacesClient] = None,
        tabelog_scraper: Optional[TabelogScraper] = None,
        entity_resolver: Optional[EntityResolver] = None,
        restaurant_repo: Optional[RestaurantRepository] = None,
        scrape_delay_sec: float = 2.0,
        bot: Optional[Bot] = None,
        llm_client: Optional[LLMClient] = None,
        guide_service: Optional[LocalSpecialtyGuideService] = None,
    ):
        self.prewarm_repo = prewarm_repo or PrewarmRepository()
        self.places_client = places_client or GooglePlacesClient()
        self.tabelog_scraper = tabelog_scraper or TabelogScraper()
        self.entity_resolver = entity_resolver or EntityResolver()
        self.restaurant_repo = restaurant_repo or RestaurantRepository()
        self.scrape_delay_sec = scrape_delay_sec
        self.bot = bot or (Bot(token=settings.TELEGRAM_BOT_TOKEN) if settings.TELEGRAM_BOT_TOKEN else None)
        self.llm_client = llm_client
        self.guide_service = guide_service or LocalSpecialtyGuideService(llm_client=self.llm_client)

    async def process_next_job(self) -> bool:
        """從佇列鎖定並處理下一筆 pending 預熱任務"""
        job = await self.prewarm_repo.get_next_pending_job()
        if not job:
            return False

        job_id = job["id"]
        area_name = job["area_name"]
        target_count = job.get("target_count", 20)

        logger.info(f"Starting prewarm job for area '{area_name}' (target_count={target_count}, job_id={job_id})")

        try:
            # 1. 支援「地名 食物類別」格式 (例如: "新宿御苑 拉麵")
            parts = area_name.split()
            loc_query = parts[0]
            keyword = " ".join(parts[1:]) if len(parts) > 1 else ""

            # 地名轉座標
            coords = await self.places_client.geocode_location(loc_query)
            if not coords:
                coords = await self.places_client.geocode_location(area_name)
            if not coords:
                logger.error(f"Geocoding failed for area '{area_name}'")
                await self.prewarm_repo.update_job_status(job_id, "failed")
                return True

            lat, lng = coords

            # 2. 檢索週邊餐廳 (若有指定食物類別則帶入過濾)
            if keyword:
                places = await self.places_client.search_nearby(lat=lat, lng=lng, radius=800, keyword=keyword)
            else:
                places = await self.places_client.search_nearby(lat=lat, lng=lng, radius=800)
            target_places = places[:target_count]
            logger.info(f"Found {len(target_places)} places for area '{area_name}' (keyword='{keyword}')")

            # 3. 並發執行：Tabelog 匹配快取 + 官方文化深度導覽預熱 (具備部分故障隔離 Partial Failure Isolation)
            async def _scrape_restaurants() -> int:
                count = 0
                for place in target_places:
                    google_place_id = place.get("google_place_id", "")
                    if not google_place_id:
                        continue

                    # 檢查 DB 是否已有快取
                    cached = await self.restaurant_repo.get_by_google_place_id(google_place_id)
                    if cached and cached.get("tabelog_rating"):
                        count += 1
                        continue

                    # 無快取，進行抓取與比對
                    store_name = place.get("name", "")
                    candidates = await self.tabelog_scraper.search_tabelog_candidates(
                        store_name=store_name, area_name=area_name
                    )
                    resolve_res = await self.entity_resolver.resolve_entity(place, candidates, allow_llm=True)

                    if resolve_res.get("match_found"):
                        matched_cand = resolve_res["candidate"]
                        tabelog_url = matched_cand.get("tabelog_url", "")
                        details = await self.tabelog_scraper.scrape_restaurant_details(
                            tabelog_url, fallback_candidate=matched_cand
                        )
                        merged_details = {
                            **matched_cand,
                            **(details or {}),
                            "tabelog_url": tabelog_url,
                            "name_ja": matched_cand.get("name_ja") or store_name,
                        }
                        if not merged_details.get("tabelog_rating"):
                            merged_details["tabelog_rating"] = matched_cand.get("tabelog_rating")

                        await self.restaurant_repo.save_restaurant_with_tabelog(
                            place,
                            tabelog_data=merged_details,
                            match_confidence=resolve_res.get("confidence", 0.0),
                            match_method=resolve_res.get("match_method", "rule_high"),
                        )
                    else:
                        await self.restaurant_repo.save_restaurant_with_tabelog(
                            place,
                            tabelog_data=None,
                            match_confidence=resolve_res.get("confidence", 0.0),
                            match_method=resolve_res.get("match_method", "no_match"),
                        )

                    count += 1

                    # 防封鎖頻率限制延遲
                    if self.scrape_delay_sec > 0:
                        await asyncio.sleep(self.scrape_delay_sec)
                return count

            async def _prewarm_culture() -> Optional[dict[str, Any]]:
                if not self.guide_service:
                    return None
                try:
                    return await self.guide_service.prewarm_area_culture(loc_query, llm_client=self.llm_client)
                except Exception as ce:
                    logger.warning(f"Culture prewarm failed for '{loc_query}': {ce}", exc_info=True)
                    return None

            saved_count, culture_guides = await asyncio.gather(
                _scrape_restaurants(),
                _prewarm_culture(),
            )

            await self.prewarm_repo.update_job_status(job_id, "completed")
            logger.info(f"Successfully completed prewarm job for area '{area_name}' ({saved_count} stores processed)")

            # 若任務有綁定使用者 chat_id，主動推播 Telegram 完成通知
            chat_id = job.get("chat_id")
            if self.bot and chat_id:
                safe_area = _escape_markdown(area_name)
                food_names = _extract_specialty_names(culture_guides.get("food")) if culture_guides else ""
                drink_names = _extract_specialty_names(culture_guides.get("drink")) if culture_guides else ""
                souvenir_names = _extract_specialty_names(culture_guides.get("souvenir")) if culture_guides else ""

                if culture_guides and (food_names or drink_names or souvenir_names):
                    culture_lines = []
                    if food_names:
                        culture_lines.append(f"• 🍱 特色名物：{_escape_markdown(food_names)}")
                    if drink_names:
                        culture_lines.append(f"• 🍵 在地飲品：{_escape_markdown(drink_names)}")
                    if souvenir_names:
                        culture_lines.append(f"• 🎁 必買伴手禮：{_escape_markdown(souvenir_names)}")

                    notify_text = (
                        f"🔔 **【{safe_area}】全方位快取預熱完成！**\n\n"
                        f"🍽️ 推薦餐廳：已備妥 **{saved_count} 家** Tabelog 頂級名店\n\n"
                        f"{chr(10).join(culture_lines)}\n\n"
                        f"💡 您可以隨時詢問：「{safe_area} 特色美食」、「{safe_area} 喝的」或「{safe_area} 伴手禮」查看完整文化典故！"
                    )
                else:
                    notify_text = (
                        f"🔔 **【{safe_area}】美食快取預熱完成！**\n\n"
                        f"🍽️ 推薦餐廳：已備妥 **{saved_count} 家** Tabelog 頂級名店與評分資料。\n\n"
                        f"💡 您現在可以隨時詢問：「推薦 {safe_area} 的美食」，享受秒速回覆體驗！"
                    )

                # 長度安全防護 (不超過 2000 字元)
                if len(notify_text) > 2000:
                    notify_text = notify_text[:1990] + "..."

                try:
                    await self.bot.send_message(chat_id=chat_id, text=notify_text, parse_mode="Markdown")
                    logger.info(f"Sent prewarm completion notification to chat {chat_id}")
                except Exception as notify_err:
                    logger.warning(f"Failed to send Markdown notification to chat {chat_id}: {notify_err}")
                    try:
                        await self.bot.send_message(chat_id=chat_id, text=notify_text)
                    except Exception as fallback_err:
                        logger.warning(f"Failed to send plain text notification to chat {chat_id}: {fallback_err}")

            return True

        except Exception as e:
            logger.error(f"Error processing prewarm job {job_id} for '{area_name}': {e}", exc_info=True)
            await self.prewarm_repo.update_job_status(job_id, "failed")
            return True

    async def run_loop(self, poll_interval_sec: float = 5.0) -> None:
        """持續輪詢處理佇列任務」"""
        logger.info("Prewarm worker loop started...")
        while True:
            processed = await self.process_next_job()
            if not processed:
                await asyncio.sleep(poll_interval_sec)


if __name__ == "__main__":
    import sys

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    worker = PrewarmWorker()
    asyncio.run(worker.run_loop())
