import logging
from typing import Any, Optional

import httpx

from src.config import settings

logger = logging.getLogger(__name__)

PLACES_API_NEW_URL = "https://places.googleapis.com/v1/places:searchNearby"
GEOCODING_API_URL = "https://maps.googleapis.com/maps/api/geocode/json"


class GooglePlacesClient:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.GOOGLE_PLACES_API_KEY

    async def search_nearby(
        self,
        lat: float,
        lng: float,
        radius: int = 800,
        keyword: str = "",
        included_types: Optional[list[str]] = None,
    ) -> list[dict[str, Any]]:
        """呼叫 Google Places API (New) REST 介面搜尋附近店家"""
        if not self.api_key:
            logger.warning("GOOGLE_PLACES_API_KEY is not set. Returning empty list.")
            return []

        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self.api_key,
            "X-Goog-FieldMask": (
                "places.id,"
                "places.displayName,"
                "places.nationalPhoneNumber,"
                "places.internationalPhoneNumber,"
                "places.location,"
                "places.rating,"
                "places.userRatingCount,"
                "places.formattedAddress,"
                "places.regularOpeningHours"
            ),
        }

        body: dict[str, Any] = {
            "locationRestriction": {
                "circle": {
                    "center": {"latitude": lat, "longitude": lng},
                    "radius": float(radius),
                }
            },
            "maxResultCount": 20,
        }

        if included_types:
            body["includedTypes"] = included_types
        else:
            body["includedTypes"] = ["restaurant", "food", "ramen_restaurant", "sushi_restaurant"]

        if keyword:
            body["textQuery"] = keyword

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                response = await client.post(PLACES_API_NEW_URL, json=body, headers=headers)
                response.raise_for_status()
                data = response.json()
                raw_places = data.get("places", [])

                return [self._format_place_item(item) for item in raw_places]
            except Exception as e:
                logger.error(f"Error fetching Google Places API (New): {e}")
                return []

    async def geocode_location(self, query: str) -> Optional[tuple[float, float]]:
        """呼叫 Google Geocoding API 將文字地名轉為 (lat, lng)"""
        if not self.api_key:
            logger.warning("GOOGLE_PLACES_API_KEY is not set for Geocoding.")
            return None

        params = {
            "address": query,
            "key": self.api_key,
            "language": "ja",
        }

        async with httpx.AsyncClient(timeout=5.0) as client:
            try:
                response = await client.get(GEOCODING_API_URL, params=params)
                response.raise_for_status()
                data = response.json()
                results = data.get("results", [])
                if not results:
                    return None

                location = results[0]["geometry"]["location"]
                return (float(location["lat"]), float(location["lng"]))
            except Exception as e:
                logger.error(f"Error in Geocoding API for query '{query}': {e}")
                return None

    def _format_place_item(self, item: dict[str, Any]) -> dict[str, Any]:
        """解析 Place (New) 項目為統一結構"""
        display_name = item.get("displayName", {})
        name_text = display_name.get("text", "")
        language_code = display_name.get("languageCode", "ja")

        location = item.get("location", {})
        lat = location.get("latitude", 0.0)
        lng = location.get("longitude", 0.0)

        # 優先取國內電話，若無取國際電話
        phone = item.get("nationalPhoneNumber") or item.get("internationalPhoneNumber") or ""

        opening_hours = item.get("regularOpeningHours", {})
        open_now = opening_hours.get("openNow", True)

        return {
            "google_place_id": item.get("id", ""),
            "name": name_text,
            "language_code": language_code,
            "phone": phone,
            "lat": lat,
            "lng": lng,
            "address": item.get("formattedAddress", ""),
            "google_rating": item.get("rating"),
            "user_ratings_total": item.get("userRatingCount"),
            "open_now": open_now,
        }
