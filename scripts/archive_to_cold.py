import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.connection import close_db_pool
from src.services.cold_storage import ColdStorageArchiver

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Archive old traces from Neon DB to Parquet Cold Storage.")
    parser.add_argument(
        "--days",
        type=int,
        default=14,
        help="Retention days. Traces older than this will be archived (default: 14).",
    )
    parser.add_argument(
        "--keep-hot",
        action="store_true",
        help="Do not delete traces from Neon DB after exporting to Parquet.",
    )
    args = parser.parse_args()

    logger.info(f"Starting trace archival process (retention: {args.days} days, delete_hot: {not args.keep_hot})...")

    archiver = ColdStorageArchiver()
    try:
        result = await archiver.archive_traces(
            retention_days=args.days,
            delete_after_archive=not args.keep_hot,
        )
        print("\n" + "=" * 50)
        print("📦 Cold Storage Archival Report")
        print("=" * 50)
        print(f"Status           : {result.get('status')}")
        print(f"Runs Archived    : {result.get('runs_archived', 0)}")
        print(f"Steps Archived   : {result.get('steps_archived', 0)}")
        print(f"Storage Target   : {result.get('destination')}")
        if result.get("parquet_files"):
            print("Parquet Files    :")
            for pf in result["parquet_files"]:
                print(f"  - {pf}")
        print("=" * 50 + "\n")
    finally:
        await close_db_pool()


if __name__ == "__main__":
    asyncio.run(main())
