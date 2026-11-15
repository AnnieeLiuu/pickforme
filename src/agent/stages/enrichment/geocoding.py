# -*- coding: utf-8 -*-
"""Geocoding, walking description, and transit station alignment for Enrichment stage."""

from __future__ import annotations

import asyncio
import logging
import math
import re
from typing import TYPE_CHECKING, Any, Optional

from src.agent.stages.places_search import format_walking_description
from src.services.entity_resolver import calculate_distance_meters

if TYPE_CHECKING:
    from src.agent.pipeline import FoodRecommendationFSM

logger = logging.getLogger(__name__)

_BREADCRUMB_STATION_RE = re.compile(r"^鄰近「([^」]+)」$")


def _align_walking_and_station(
    p: dict[str, Any],
    nearest_station: Optional[str] = None,
    area_hint: Optional[str] = None,
) -> dict[str, Any]:
    """依據最近車站或在地子商圈重新排版 walking_desc，絕不拼接模糊大區域中心點米數"""
    origin = p.get("origin_name") or area_hint
    st_desc = nearest_station or p.get("nearest_station_desc")
    p["nearest_station_desc"] = st_desc
    p["walking_desc"] = format_walking_description(
        origin_name=origin,
        dist_m=int(p.get("distance_meters") or 0),
        walk_mins=int(p.get("walk_time_minutes") or 1),
        nearest_station_desc=st_desc,
        address=p.get("address"),
    )
    return p


async def _hydrate_station_walking_distances(
    fsm: FoodRecommendationFSM,
    top_stores: list[dict[str, Any]],
) -> None:
    """針對僅有車站名稱 (如 鄰近「蔵前站」) 的入選店家，透過 Google Geocoding 計算實際步行距離與分鐘數"""
    geocode_fn = getattr(getattr(fsm, "places_client", None), "geocode_location", None)
    if not callable(geocode_fn):
        return

    station_coords: dict[str, Optional[tuple[float, float]]] = {}
    for store in top_stores:
        st_desc = store.get("nearest_station_desc") or store.get("walking_desc") or ""
        m = _BREADCRUMB_STATION_RE.match(st_desc.strip())
        if not m:
            continue
        station_name = m.group(1).strip()
        s_lat = store.get("lat")
        s_lng = store.get("lng")
        if not isinstance(s_lat, (int, float)) or not isinstance(s_lng, (int, float)):
            continue

        if station_name not in station_coords:
            try:
                res = geocode_fn(f"{station_name} 日本")
                coords = await res if asyncio.iscoroutine(res) else res
                if (
                    isinstance(coords, (tuple, list))
                    and len(coords) == 2
                    and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in coords)
                ):
                    station_coords[station_name] = (float(coords[0]), float(coords[1]))
                else:
                    station_coords[station_name] = None
            except Exception as e:
                logger.debug(f"Station geocode failed for '{station_name}': {e}")
                station_coords[station_name] = None

        st_xy = station_coords.get(station_name)
        if not st_xy:
            continue
        dist_m = int(calculate_distance_meters(st_xy[0], st_xy[1], float(s_lat), float(s_lng)))
        if 0 < dist_m <= 3000:
            walk_mins = max(1, math.ceil(dist_m / 80))
            aligned_desc = f"從「{station_name}」步行約 {walk_mins} 分鐘 ({dist_m}m)"
            store["nearest_station_desc"] = aligned_desc
            store["walking_desc"] = aligned_desc
