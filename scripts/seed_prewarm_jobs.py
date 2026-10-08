import argparse
import asyncio
import logging
import sys
from pathlib import Path

# 確保專案根目錄在 sys.path 中
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.repositories.prewarm import PrewarmRepository

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


async def seed_prewarm_jobs(areas: list[str], target_count: int = 20) -> None:
    repo = PrewarmRepository()
    inserted = 0
    for area in areas:
        area_cleaned = area.strip()
        if not area_cleaned:
            continue
        try:
            job_id = await repo.create_job(area_name=area_cleaned, target_count=target_count)
            logger.info(f"Created prewarm job for '{area_cleaned}' (job_id: {job_id})")
            inserted += 1
        except Exception as e:
            logger.error(f"Failed to create job for '{area_cleaned}': {e}")

    logger.info(f"Successfully seeded {inserted} prewarm jobs into DB queue.")


def main() -> None:
    parser = argparse.ArgumentParser(description="PickForMe - 預熱區域佇列種子寫入 CLI 工具")
    parser.add_argument(
        "-a",
        "--areas",
        nargs="+",
        help="預熱區域名稱列表，例如：-a 博多 天神 心齋橋 新宿",
    )
    parser.add_argument(
        "-f",
        "--file",
        help="包含區域名稱列表的文本檔案路徑 (每行一區)",
    )
    parser.add_argument(
        "-c",
        "--count",
        type=int,
        default=20,
        help="每個區域預熱的目標餐廳數量 (預設: 20)",
    )

    args = parser.parse_args()

    areas_to_seed: list[str] = []
    if args.areas:
        areas_to_seed.extend(args.areas)

    if args.file:
        file_path = Path(args.file)
        if file_path.exists():
            areas_to_seed.extend(file_path.read_text(encoding="utf-8").splitlines())
        else:
            logger.error(f"File not found: {args.file}")

    if not areas_to_seed:
        logger.warning("No areas specified. Use --areas or --file to specify prewarm targets.")
        parser.print_help()
        return

    asyncio.run(seed_prewarm_jobs(areas=areas_to_seed, target_count=args.count))


if __name__ == "__main__":
    main()
