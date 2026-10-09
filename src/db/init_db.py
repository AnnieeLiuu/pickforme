import asyncio
import logging
from pathlib import Path

import psycopg

from src.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def init_db() -> None:
    schema_path = Path(__file__).parent / "schema.sql"
    if not schema_path.exists():
        raise FileNotFoundError(f"Schema file not found at {schema_path}")

    sql_content = schema_path.read_text(encoding="utf-8")

    db_host = settings.DATABASE_URL.split("@")[-1] if "@" in settings.DATABASE_URL else "localhost"
    logger.info(f"Connecting to database host: {db_host}")
    async with await psycopg.AsyncConnection.connect(settings.DATABASE_URL) as conn:
        async with conn.cursor() as cur:
            logger.info("Executing DDL schema.sql...")
            await cur.execute(sql_content)
            await conn.commit()
    logger.info("Database schema initialized successfully!")


if __name__ == "__main__":
    import sys
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(init_db())
