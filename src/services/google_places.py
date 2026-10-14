import logging
from typing import Any, Optional

import httpx

from src.config import settings

logger = logging.getLogger(__name__)

PLACES_API_NEW_URL = "https://places.googleapis.com/v1/places:searchNearby"
PLACES_API_TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
GEOCODING_API_URL = "https://maps.googleapis.com/maps/api/geocode/json"


class GooglePlacesClient:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.GOOGLE_PLACES_API_KEY

    async def search_by_text(
        self,
        query: str,
        lat: Optional[float] = None,
        lng: Optional[float] = None,
        radius: int = 1500,
        max_results: int = 10,
    ) -> list[dict[str, Any]]:
        """呼叫 Google Places API (New) Text Search 搜尋專門餐廳"""
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
                "places.regularOpeningHours,"
                "places.paymentOptions,"
                "places.reviews"
            ),
            "X-Goog-LanguageCode": "ja",
        }

        body: dict[str, Any] = {
            "textQuery": query,
            "maxResultCount": min(max_results, 20),
            "languageCode": "ja",
        }

        if lat is not None and lng is not None:
            body["locationBias"] = {
                "circle": {
                    "center": {"latitude": lat, "longitude": lng},
                    "radius": float(radius),
                }
            }

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                response = await client.post(PLACES_API_TEXT_SEARCH_URL, json=body, headers=headers)
                response.raise_for_status()
                data = response.json()
                raw_places = data.get("places", [])
                return [self._format_place_item(item) for item in raw_places]
            except Exception as e:
                logger.error(f"Error fetching Google Places TextSearch: {e}")
                return []

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
                "places.regularOpeningHours,"
                "places.paymentOptions,"
                "places.reviews"
            ),
            "X-Goog-LanguageCode": "ja",
        }

        body: dict[str, Any] = {
            "locationRestriction": {
                "circle": {
                    "center": {"latitude": lat, "longitude": lng},
                    "radius": float(radius),
                }
            },
            "maxResultCount": 20,
            "languageCode": "ja",
        }

        # 若指定了美食/料理關鍵字，優先使用 TextSearch 確保精準命中專門店 (杜絕百貨公司或無關地標)
        if keyword and not included_types:
            text_results = await self.search_by_text(query=keyword, lat=lat, lng=lng, radius=radius, max_results=20)
            if text_results:
                return text_results

        if included_types:
            body["includedTypes"] = included_types
        elif keyword:
            category_type_map = {
                "拉麵": ["ramen_restaurant"],
                "沾麵": ["ramen_restaurant"],
                "ramen": ["ramen_restaurant"],
                "壽司": ["sushi_restaurant"],
                "sushi": ["sushi_restaurant"],
                "燒肉": ["barbecue_restaurant", "yakiniku_restaurant"],
                "yakiniku": ["barbecue_restaurant", "yakiniku_restaurant"],
                "居酒屋": ["japanese_restaurant", "bar"],
                "izakaya": ["japanese_restaurant", "bar"],
                "咖啡": ["cafe"],
                "甜點": ["dessert_shop", "bakery"],
            }
            matched_types = []
            for k, types in category_type_map.items():
                if k in keyword.lower():
                    matched_types.extend(types)
            if matched_types:
                body["includedTypes"] = list(dict.fromkeys(matched_types))
            else:
                body["includedTypes"] = ["restaurant", "japanese_restaurant"]
        else:
            body["includedTypes"] = ["restaurant", "ramen_restaurant", "sushi_restaurant", "japanese_restaurant"]

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

        reviews_list: list[dict[str, Any]] = []
        for r in item.get("reviews", []):
            text_obj = r.get("text", {}) or {}
            orig_obj = r.get("originalText", {}) or {}
            r_text = text_obj.get("text", "")
            r_orig = orig_obj.get("text", "") or r_text
            if r_text or r_orig:
                reviews_list.append(
                    {
                        "text": r_text,
                        "original_text": r_orig,
                        "rating": r.get("rating"),
                        "author": (r.get("authorAttribution") or {}).get("displayName", ""),
                        "source": "google",
                    }
                )

        # 支付選項 (Google Places API New 原生支付資訊)
        payment_options = item.get("paymentOptions", {})
        accepts_credit_cards = payment_options.get("acceptsCreditCards")
        accepts_cash_only = payment_options.get("acceptsCashOnly")
        accepts_nfc = payment_options.get("acceptsNfc")

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
            "payment_options": payment_options,
            "accepts_credit_cards": accepts_credit_cards,
            "accepts_cash_only": accepts_cash_only,
            "accepts_nfc": accepts_nfc,
            "reviews": reviews_list,
        }
