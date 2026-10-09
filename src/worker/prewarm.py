import asyncio
import logging
from typing import Optional

from src.db.repositories.prewarm import PrewarmRepository
from src.db.repositories.restaurant import RestaurantRepository
from src.services.entity_resolver import EntityResolver
from src.services.google_places import GooglePlacesClient
from src.services.tabelog_scraper import TabelogScraper

logger = logging.getLogger(__name__)


class PrewarmWorker:
    def __init__(
        self,
        prewarm_repo: Optional[PrewarmRepository] = None,
        places_client: Optional[GooglePlacesClient] = None,
        tabelog_scraper: Optional[TabelogScraper] = None,
        entity_resolver: Optional[EntityResolver] = None,
        restaurant_repo: Optional[RestaurantRepository] = None,
        scrape_delay_sec: float = 2.0,
    ):
        self.prewarm_repo = prewarm_repo or PrewarmRepository()
        self.places_client = places_client or GooglePlacesClient()
        self.tabelog_scraper = tabelog_scraper or TabelogScraper()
        self.entity_resolver = entity_resolver or EntityResolver()
        self.restaurant_repo = restaurant_repo or RestaurantRepository()
        self.scrape_delay_sec = scrape_delay_sec

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
            # 1. 地名轉座標
            coords = await self.places_client.geocode_location(area_name)
            if not coords:
                logger.error(f"Geocoding failed for area '{area_name}'")
                await self.prewarm_repo.update_job_status(job_id, "failed")
                return True

            lat, lng = coords

            # 2. 檢索週邊餐廳
            places = await self.places_client.search_nearby(lat=lat, lng=lng, radius=800)
            target_places = places[:target_count]
            logger.info(f"Found {len(target_places)} places for area '{area_name}'")

            # 3. 逐一進行 Tabelog 匹配與快取
            saved_count = 0
            for place in target_places:
                google_place_id = place.get("google_place_id", "")
                if not google_place_id:
                    continue

                # 檢查 DB 是否已有快取
                cached = await self.restaurant_repo.get_by_google_place_id(google_place_id)
                if cached and cached.get("tabelog_rating"):
                    saved_count += 1
                    continue

                # 無快取，進行抓取與比對
                store_name = place.get("name", "")
                candidates = await self.tabelog_scraper.search_tabelog_candidates(store_name=store_name, area_name=area_name)
                resolve_res = await self.entity_resolver.resolve_entity(place, candidates)

                if resolve_res.get("match_found"):
                    matched_cand = resolve_res["candidate"]
                    tabelog_url = matched_cand.get("tabelog_url", "")
                    details = await self.tabelog_scraper.scrape_restaurant_details(tabelog_url)
                    if not details:
                        details = matched_cand

                    await self.restaurant_repo.save_restaurant_with_tabelog(
                        place,
                        tabelog_data=details,
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

                saved_count += 1

                # 防封鎖頻率限制延遲
                if self.scrape_delay_sec > 0:
                    await asyncio.sleep(self.scrape_delay_sec)

            await self.prewarm_repo.update_job_status(job_id, "completed")
            logger.info(f"Successfully completed prewarm job for area '{area_name}' ({saved_count} stores processed)")
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
