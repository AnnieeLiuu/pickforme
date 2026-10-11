import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))
if sys.platform == "win32":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from src.db.connection import close_db_pool, get_db_pool
from src.services.entity_resolver import (
    extract_prefecture_from_address,
    extract_prefecture_from_tabelog_url,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


async def run_cache_remediation(dry_run: bool = True) -> int:
    """掃描資料庫中所有的快取紀錄，識別並清理跨縣市污染資料"""
    print("=" * 65)
    print("🍱 PickForMe 資料庫快取資料治理與巡檢工具 (Cache Remediation)")
    print(f"模式: {'🔍 模擬掃描 (DRY-RUN)' if dry_run else '🛠️ 正式修復清除 (FIX)'}")
    print("=" * 65)

    pool = await get_db_pool()
    dirty_records: list[dict[str, Any]] = []

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            # 查詢所有帶有 tabelog_url 的餐廳主表記錄
            await cur.execute("""
                SELECT id, google_place_id, name_ja, name_en, address, tabelog_url
                FROM restaurants
                WHERE tabelog_url IS NOT NULL;
            """)
            rows = await cur.fetchall()
            print(f"📊 總計掃描 {len(rows)} 筆快取店家資料...\n")

            for r in rows:
                g_addr = r.get("address")
                t_url = r.get("tabelog_url")
                g_pref = extract_prefecture_from_address(g_addr)
                t_pref = extract_prefecture_from_tabelog_url(t_url)

                if g_pref and t_pref and g_pref != t_pref:
                    dirty_records.append(
                        {
                            "id": r["id"],
                            "name": r.get("name_ja") or r.get("name_en"),
                            "g_pref": g_pref,
                            "t_pref": t_pref,
                            "tabelog_url": t_url,
                            "address": g_addr,
                        }
                    )

            if not dirty_records:
                print("✅ 完美！未發現任何跨縣市被污染的歷史快取記錄。")
                await close_db_pool()
                return 0

            print(f"⚠️ 發現 {len(dirty_records)} 筆跨縣市污染異常記錄：")
            for idx, d in enumerate(dirty_records, 1):
                print(f"  {idx}. [{d['name']}] (ID: {d['id']})")
                print(f"     - Google 地區  : {d['g_pref']} ({d['address'][:40]}...)")
                print(f"     - Tabelog 地區 : {d['t_pref']} ({d['tabelog_url']})")

            if not dry_run:
                print("\n🛠️ 開始執行清理作業...")
                dirty_ids = [d["id"] for d in dirty_records]
                await cur.execute(
                    """
                    DELETE FROM restaurants
                    WHERE id = ANY(%s);
                """,
                    (dirty_ids,),
                )
                await conn.commit()
                print(f"🎉 成功清理 {len(dirty_ids)} 筆污染記錄！資料庫已恢復乾淨狀態。")
            else:
                print("\n💡 提示: 目前為 --dry-run 模擬模式。若要實際清理，請加上 --fix 參數：")
                print("   python scripts/remediate_cache.py --fix")

    await close_db_pool()
    return len(dirty_records)


def main() -> None:
    parser = argparse.ArgumentParser(description="PickForMe 歷史快取資料治理與跨縣市污染巡檢工具")
    parser.add_argument(
        "--fix",
        action="store_true",
        help="實際執行刪除修復 (預設為只掃描不刪除的 dry-run 模式)",
    )

    args = parser.parse_args()
    dry_run = not args.fix
    asyncio.run(run_cache_remediation(dry_run=dry_run))


if __name__ == "__main__":
    main()
