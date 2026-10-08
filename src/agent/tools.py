import json
import logging
from typing import Any, Optional

from src.db.repositories.restaurant import RestaurantRepository
from src.services.entity_resolver import EntityResolver
from src.services.google_places import GooglePlacesClient
from src.services.tabelog_scraper import TabelogScraper

logger = logging.getLogger(__name__)

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "geocode_location",
            "description": "將日本地名、車站名或地標轉換為經緯度座標",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "日本地名或車站名稱，例如 '博多駅', '心齋橋', '新宿'",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_nearby",
            "description": "搜尋指定經緯度座標週邊的美食餐廳",
            "parameters": {
                "type": "object",
                "properties": {
                    "lat": {"type": "number", "description": "緯度 (Latitude)"},
                    "lng": {"type": "number", "description": "經度 (Longitude)"},
                    "radius": {
                        "type": "integer",
                        "description": "搜尋半徑 (公尺)，預設 800",
                    },
                    "keyword": {
                        "type": "string",
                        "description": "美食或料理關鍵字，例如 '拉麵', '燒肉', '居酒屋'",
                    },
                },
                "required": ["lat", "lng"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_tabelog_info",
            "description": "傳入 Google Place ID，比對並取得對應的 Tabelog 評分、評價人數、預算區間與連結",
            "parameters": {
                "type": "object",
                "properties": {
                    "google_place_id": {
                        "type": "string",
                        "description": "Google Place ID",
                    },
                    "store_name": {
                        "type": "string",
                        "description": "Google 店家名稱 (選填，可加速搜尋)",
                    },
                    "phone": {
                        "type": "string",
                        "description": "Google 店家電話 (選填)",
                    },
                    "lat": {"type": "number", "description": "店家緯度 (選填)"},
                    "lng": {"type": "number", "description": "店家經度 (選填)"},
                },
                "required": ["google_place_id"],
            },
        },
    },
]


class AgentToolExecutor:
    def __init__(
        self,
        places_client: Optional[GooglePlacesClient] = None,
        tabelog_scraper: Optional[TabelogScraper] = None,
        entity_resolver: Optional[EntityResolver] = None,
        restaurant_repo: Optional[RestaurantRepository] = None,
    ):
        self.places_client = places_client or GooglePlacesClient()
        self.tabelog_scraper = tabelog_scraper or TabelogScraper()
        self.entity_resolver = entity_resolver or EntityResolver()
        self.restaurant_repo = restaurant_repo or RestaurantRepository()

    async def execute_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """執行指定的 Agent 工具，並將結果序列化為 JSON 字串回傳"""
        logger.info(f"Executing tool '{tool_name}' with args: {arguments}")
        try:
            if tool_name == "geocode_location":
                return await self._handle_geocode_location(arguments)
            elif tool_name == "search_nearby":
                return await self._handle_search_nearby(arguments)
            elif tool_name == "get_tabelog_info":
                return await self._handle_get_tabelog_info(arguments)
            else:
                return json.dumps({"error": f"Unknown tool name: {tool_name}"}, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error executing tool '{tool_name}': {e}", exc_info=True)
            return json.dumps({"error": f"Tool execution failed: {str(e)}"}, ensure_ascii=False)

    async def _handle_geocode_location(self, args: dict[str, Any]) -> str:
        query = args.get("query", "")
        if not query:
            return json.dumps({"error": "Query string is required"}, ensure_ascii=False)

        coords = await self.places_client.geocode_location(query)
        if not coords:
            return json.dumps({"error": f"Could not geocode query '{query}'"}, ensure_ascii=False)

        lat, lng = coords
        return json.dumps({"query": query, "lat": lat, "lng": lng}, ensure_ascii=False)

    async def _handle_search_nearby(self, args: dict[str, Any]) -> str:
        lat = float(args.get("lat", 0.0))
        lng = float(args.get("lng", 0.0))
        radius = int(args.get("radius", 800))
        keyword = args.get("keyword", "")

        places = await self.places_client.search_nearby(lat=lat, lng=lng, radius=radius, keyword=keyword)
        # 上下文控管：最多回傳前 10 家，精簡欄位
        top_places = places[:10]
        return json.dumps({"count": len(top_places), "places": top_places}, ensure_ascii=False)

    async def _handle_get_tabelog_info(self, args: dict[str, Any]) -> str:
        google_place_id = args.get("google_place_id", "")
        if not google_place_id:
            return json.dumps({"error": "google_place_id is required"}, ensure_ascii=False)

        # 1. 檢查 DB 是否已有快取
        cached = None
        try:
            cached = await self.restaurant_repo.get_by_google_place_id(google_place_id)
        except Exception as e:
            logger.warning(f"DB lookup failed for place_id {google_place_id}: {e}")

        if cached and cached.get("tabelog_rating"):
            return json.dumps(
                {
                    "google_place_id": google_place_id,
                    "tabelog_url": cached.get("tabelog_url"),
                    "name_ja": cached.get("name_ja"),
                    "tabelog_rating": float(cached["tabelog_rating"]) if cached.get("tabelog_rating") else None,
                    "review_count": cached.get("review_count"),
                    "price_lunch": cached.get("price_lunch"),
                    "price_dinner": cached.get("price_dinner"),
                    "match_confidence": float(cached["match_confidence"]) if cached.get("match_confidence") else 1.0,
                    "cached": True,
                },
                ensure_ascii=False,
            )

        # 2. 無快取，進行實體比對與抓取
        store_name = args.get("store_name", "")
        phone = args.get("phone", "")
        lat = args.get("lat", 0.0)
        lng = args.get("lng", 0.0)

        google_store = {
            "google_place_id": google_place_id,
            "name": store_name,
            "phone": phone,
            "lat": lat,
            "lng": lng,
        }

        # 搜尋 Tabelog 候選清單
        candidates = await self.tabelog_scraper.search_tabelog_candidates(store_name=store_name)
        resolve_res = await self.entity_resolver.resolve_entity(google_store, candidates)

        if not resolve_res.get("match_found"):
            # 儲存無比對結果狀態
            try:
                await self.restaurant_repo.save_restaurant_with_tabelog(
                    google_store,
                    tabelog_data=None,
                    match_confidence=resolve_res.get("confidence", 0.0),
                    match_method=resolve_res.get("match_method", "no_match"),
                )
            except Exception as e:
                logger.warning(f"Could not save un-matched restaurant: {e}")

            return json.dumps(
                {
                    "google_place_id": google_place_id,
                    "tabelog_rating": None,
                    "match_found": False,
                    "reason": resolve_res.get("reason", "No match found on Tabelog"),
                },
                ensure_ascii=False,
            )

        matched_cand = resolve_res["candidate"]
        tabelog_url = matched_cand.get("tabelog_url", "")

        # 抓取詳細資料
        details = await self.tabelog_scraper.scrape_restaurant_details(tabelog_url)
        if not details:
            details = matched_cand

        # 寫入資料庫
        try:
            await self.restaurant_repo.save_restaurant_with_tabelog(
                google_store,
                tabelog_data=details,
                match_confidence=resolve_res.get("confidence", 0.0),
                match_method=resolve_res.get("match_method", "rule_high"),
            )
        except Exception as e:
            logger.warning(f"Could not save matched restaurant: {e}")

        return json.dumps(
            {
                "google_place_id": google_place_id,
                "tabelog_url": details.get("tabelog_url"),
                "name_ja": details.get("name_ja"),
                "tabelog_rating": details.get("tabelog_rating"),
                "review_count": details.get("review_count"),
                "price_lunch": details.get("price_lunch"),
                "price_dinner": details.get("price_dinner"),
                "match_confidence": resolve_res.get("confidence"),
                "match_method": resolve_res.get("match_method"),
            },
            ensure_ascii=False,
        )
