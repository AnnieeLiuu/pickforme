# -*- coding: utf-8 -*-
"""PickForMe - 在地特色美食・飲品・伴手禮 JSON 知識庫冪等同步工具 (Seed Script)

功能：
1. 讀取 `config/local_specialties.json` (或指定 JSON 檔案)。
2. 相容雙格式解析：
   - 完整三維維度 (categories: food, drink, souvenir)，如金澤、宇治。
   - 傳統頂層格式 (specialties: food)，其餘 9 區。
3. 冪等寫回 Neon Postgres `area_specialty_guides` 資料表，標記 `source_tier = 'curated'` 與 `verified = True`。
4. 支援 `--dry-run` 模式進行離線校驗。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any, Optional, Union

# 確保專案根目錄在 sys.path 中
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.repositories.specialty_guide import SpecialtyGuideRepository

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def parse_specialties_json(data: dict[str, Any]) -> list[dict[str, Any]]:
    """解析 local_specialties.json，支援 categories 多維度與 legacy top-level 格式。

    回傳標準化記錄列表，每筆記錄代表一個 (area_key, category) 實體。
    """
    records: list[dict[str, Any]] = []

    for area_key, area_data in data.items():
        if not isinstance(area_data, dict):
            continue

        area_name = str(area_data.get("area_name") or area_key).strip()
        aliases = area_data.get("aliases") or [area_name]
        if not isinstance(aliases, list):
            aliases = [str(aliases)]

        categories_map = area_data.get("categories")
        if isinstance(categories_map, dict) and categories_map:
            for cat_name, cat_data in categories_map.items():
                if not isinstance(cat_data, dict):
                    continue
                records.append(
                    {
                        "area_key": area_key,
                        "category": str(cat_name).strip().lower(),
                        "area_name": area_name,
                        "aliases": aliases,
                        "intro": str(cat_data.get("intro") or "").strip(),
                        "specialties": cat_data.get("specialties") or [],
                        "source_tier": "curated",
                        "verified": True,
                    }
                )
        elif area_data.get("specialties"):
            records.append(
                {
                    "area_key": area_key,
                    "category": "food",
                    "area_name": area_name,
                    "aliases": aliases,
                    "intro": str(area_data.get("intro") or "").strip(),
                    "specialties": area_data.get("specialties") or [],
                    "source_tier": "curated",
                    "verified": True,
                }
            )

    return records


async def seed_specialties_to_db(
    json_path: Union[str, Path] = "config/local_specialties.json",
    dry_run: bool = False,
    repo: Optional[SpecialtyGuideRepository] = None,
) -> int:
    """將 local_specialties.json 知識庫同步至 Neon Postgres。

    Args:
        json_path: JSON 檔案路徑。
        dry_run: 若為 True 僅解析與日誌輸出，不執行 DB 寫入。
        repo: SpecialtyGuideRepository 實例，方便注入 Mock。

    Returns:
        成功處理或寫入的記錄筆數。
    """
    path = Path(json_path)
    if not path.is_file():
        raise FileNotFoundError(f"JSON fixture file not found: {path}")

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    records = parse_specialties_json(data)
    total_areas = len(data)

    if dry_run:
        logger.info(f"[DRY-RUN] Found {len(records)} records across {total_areas} areas in {path} to seed.")
        for r in records:
            logger.info(
                f"[DRY-RUN] Area: {r['area_name']} ({r['area_key']}), Category: {r['category']}, "
                f"Specialties: {len(r['specialties'])}, Tier: {r['source_tier']}"
            )
        return len(records)

    guide_repo = repo or SpecialtyGuideRepository()
    success_count = 0

    for r in records:
        try:
            ok = await guide_repo.upsert_guide(
                area_key=r["area_key"],
                category=r["category"],
                area_name=r["area_name"],
                intro=r["intro"],
                specialties=r["specialties"],
                aliases=r["aliases"],
                source_tier=r["source_tier"],
                verified=r["verified"],
            )
            if ok:
                success_count += 1
                logger.debug(f"Upserted {r['area_name']} ({r['category']}) successfully.")
            else:
                logger.warning(f"Failed to upsert {r['area_name']} ({r['category']}).")
        except Exception as e:
            logger.error(f"Error upserting {r['area_name']} ({r['category']}): {e}")

    logger.info(f"Successfully seeded {success_count}/{len(records)} specialty guide records into database.")
    return success_count


def main() -> None:
    parser = argparse.ArgumentParser(description="PickForMe - 在地特色美食・飲品・伴手禮 JSON 知識庫冪等同步工具")
    parser.add_argument(
        "-f",
        "--file",
        default="config/local_specialties.json",
        help="在地特色美食導覽 JSON 檔案路徑 (預設: config/local_specialties.json)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="僅解析並輸出預覽摘要，不執行資料庫寫入",
    )

    args = parser.parse_args()
    asyncio.run(seed_specialties_to_db(json_path=args.file, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
