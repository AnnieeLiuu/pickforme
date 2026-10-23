#!/usr/bin/env python3
"""即時線上 Cloud Run 容器診斷日誌拉取工具 (Fetch Live Diagnostics Logs).

透過 TELEGRAM_WEBHOOK_SECRET 安全呼叫 Cloud Run /internal/diagnostics/logs 端點，
直接在本地端印出伺服器最近之 WARNING 與 ERROR 日誌，無需 GCP 金鑰。

使用方式:
    python scripts/fetch_live_logs.py [--limit 30] [--level WARNING]
"""

import argparse
import json
import sys
import urllib.parse
import urllib.request
from urllib.error import HTTPError, URLError

from src.config import settings

CLOUD_RUN_BASE_URL = "https://pickforme-api-297554628555.asia-northeast1.run.app"


def fetch_live_logs(limit: int = 50, min_level: str = "WARNING", base_url: str = CLOUD_RUN_BASE_URL) -> None:
    secret = settings.TELEGRAM_WEBHOOK_SECRET
    if not secret:
        print("❌ 錯誤: 本機 .env 未配置 TELEGRAM_WEBHOOK_SECRET", file=sys.stderr)
        sys.exit(1)

    query = urllib.parse.urlencode(
        {
            "secret": secret,
            "limit": limit,
            "min_level": min_level,
        }
    )
    target_url = f"{base_url.rstrip('/')}/internal/diagnostics/logs?{query}"

    print(f"📡 正在從 {base_url} 拉取最近 {limit} 筆 [{min_level}+] 容器診斷日誌...")
    try:
        req = urllib.request.Request(target_url, headers={"User-Agent": "PickForMe-Diagnostics-CLI"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            logs = data.get("logs", [])
            print(f"✅ 成功獲取 {len(logs)} 筆日誌 (環境: {data.get('environment')})：\n")
            if not logs:
                print("🟢 目前伺服器運作健康，無任何 WARNING 或 ERROR 異常！")
                return

            for idx, entry in enumerate(logs, 1):
                level = entry.get("level", "INFO")
                color = "\033[33m" if level == "WARNING" else "\033[31m"
                reset = "\033[0m"
                print(f"[{idx}] {entry.get('timestamp')} {color}[{level}]{reset} {entry.get('logger')}:")
                print(f"    {entry.get('message')}")
                if entry.get("exception"):
                    print(f"    {entry.get('exception')}")
                print("-" * 60)
    except HTTPError as e:
        print(f"❌ HTTP 請求失敗 ({e.code}): {e.read().decode('utf-8')}", file=sys.stderr)
        sys.exit(1)
    except URLError as e:
        print(f"❌ 連線失敗: {e.reason}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch Cloud Run live diagnostics logs")
    parser.add_argument("--limit", type=int, default=30, help="最多取得筆數 (預設: 30)")
    parser.add_argument("--level", type=str, default="WARNING", help="最低等級 (預設: WARNING, 可選 ERROR)")
    parser.add_argument("--url", type=str, default=CLOUD_RUN_BASE_URL, help="目標 API 基礎網址")
    args = parser.parse_args()

    fetch_live_logs(limit=args.limit, min_level=args.level, base_url=args.url)
