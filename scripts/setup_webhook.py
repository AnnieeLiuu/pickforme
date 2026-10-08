import argparse
import asyncio
import logging
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


async def set_telegram_webhook(url: str, token: str, secret_token: str) -> None:
    api_url = f"https://api.telegram.org/bot{token}/setWebhook"
    payload = {
        "url": url,
        "allowed_updates": ["message", "edited_message"],
    }
    if secret_token:
        payload["secret_token"] = secret_token

    logger.info(f"Setting Telegram Webhook to URL: '{url}'...")
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(api_url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            if data.get("ok"):
                logger.info(f"✅ Webhook successfully set! Telegram response: {data.get('description')}")
            else:
                logger.error(f"❌ Failed to set webhook: {data}")
        except Exception as e:
            logger.error(f"Error calling Telegram setWebhook API: {e}")


async def delete_telegram_webhook(token: str) -> None:
    api_url = f"https://api.telegram.org/bot{token}/deleteWebhook"
    logger.info("Deleting Telegram Webhook...")
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(api_url)
            resp.raise_for_status()
            data = resp.json()
            logger.info(f"✅ Webhook status: {data.get('description')}")
        except Exception as e:
            logger.error(f"Error deleting Telegram webhook: {e}")


def main() -> None:
    parser = argparse.ArgumentParser(description="PickForMe - Telegram Webhook 自動註冊與設定 CLI 工具")
    parser.add_argument("-u", "--url", help="Cloud Run 或公開 Webhook 網址 (例如: https://.../api/telegram/webhook)")
    parser.add_argument("-t", "--token", help="Telegram Bot Token (預設從 .env 讀取)")
    parser.add_argument("-s", "--secret", help="Telegram Webhook Secret Token (預設從 .env 讀取)")
    parser.add_argument("-d", "--delete", action="store_true", help="刪除目前已註冊的 Webhook")

    args = parser.parse_args()

    token = args.token or settings.TELEGRAM_BOT_TOKEN
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN is missing. Provide via --token or set in .env")
        return

    secret_token = args.secret or settings.TELEGRAM_WEBHOOK_SECRET

    if args.delete:
        asyncio.run(delete_telegram_webhook(token))
        return

    if not args.url:
        logger.warning("Target Webhook URL is required. Use --url <URL>")
        parser.print_help()
        return

    asyncio.run(set_telegram_webhook(url=args.url, token=token, secret_token=secret_token))


if __name__ == "__main__":
    main()
