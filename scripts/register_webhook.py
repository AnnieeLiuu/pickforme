import argparse
import asyncio
import logging
import sys
from pathlib import Path

import httpx

# 確保專案根目錄在 sys.path 中
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


async def register_telegram_webhook(webhook_url: str) -> None:
    token = settings.TELEGRAM_BOT_TOKEN
    secret = settings.TELEGRAM_WEBHOOK_SECRET

    if not token:
        logger.error("TELEGRAM_BOT_TOKEN is not configured in .env.")
        return

    set_url = f"https://api.telegram.org/bot{token}/setWebhook"
    params = {
        "url": webhook_url,
        "secret_token": secret,
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(set_url, json=params)
        logger.info(f"setWebhook response: {resp.json()}")

        info_url = f"https://api.telegram.org/bot{token}/getWebhookInfo"
        info_resp = await client.get(info_url)
        logger.info(f"getWebhookInfo response: {info_resp.json()}")


def main() -> None:
    parser = argparse.ArgumentParser(description="PickForMe - Telegram Webhook 綁定與檢測 CLI 工具")
    parser.add_argument(
        "-u",
        "--url",
        required=True,
        help="公開 HTTPS Webhook URL (例如: https://xxx.trycloudflare.com/api/telegram/webhook)",
    )
    args = parser.parse_args()

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    asyncio.run(register_telegram_webhook(args.url))


if __name__ == "__main__":
    main()
