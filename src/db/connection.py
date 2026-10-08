import logging
from typing import AsyncGenerator
import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from src.config import settings

logger = logging.getLogger(__name__)

# 非同步連線池全域實例
db_pool: AsyncConnectionPool | None = None


async def get_db_pool() -> AsyncConnectionPool:
    global db_pool
    if db_pool is None:
        logger.info("Initializing psycopg AsyncConnectionPool...")
        db_pool = AsyncConnectionPool(
            conninfo=settings.DATABASE_URL,
            open=False,
            min_size=1,
            max_size=10,
            kwargs={"row_factory": dict_row},
        )
        await db_pool.open()
    return db_pool


async def close_db_pool() -> None:
    global db_pool
    if db_pool is not None:
        logger.info("Closing psycopg AsyncConnectionPool...")
        await db_pool.close()
        db_pool = None


async def get_db_connection() -> AsyncGenerator[psycopg.AsyncConnection, None]:
    pool = await get_db_pool()
    async with pool.connection() as conn:
        yield conn
