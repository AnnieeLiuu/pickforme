"""系統安全內部診斷與記憶體日誌緩衝區 (Internal Diagnostics & In-Memory Log Buffer).

在不依賴外部 GCP Key 的前提下，提供極輕量之記憶體日誌暫存 (最多保留 200 筆 WARNING/ERROR)，
並供內部安全診斷端點透過 TELEGRAM_WEBHOOK_SECRET 認證查詢，維持架構分離與可觀測性。
"""

import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any


class InMemoryLogBuffer(logging.Handler):
    """記憶體循環日誌緩衝區 Handler"""

    def __init__(self, maxlen: int = 200) -> None:
        super().__init__(level=logging.WARNING)
        self.buffer: deque[dict[str, Any]] = deque(maxlen=maxlen)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            log_entry = {
                "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
            if record.exc_info:
                log_entry["exception"] = self.format(record)
            self.buffer.append(log_entry)
        except Exception:
            self.handleError(record)

    def get_logs(self, limit: int = 50, min_level: str = "WARNING") -> list[dict[str, Any]]:
        """獲取最近日誌，可依等級過濾"""
        level_num = getattr(logging, min_level.upper(), logging.WARNING)
        matched: list[dict[str, Any]] = []

        # 由新至舊遍歷
        for entry in reversed(self.buffer):
            entry_level_num = getattr(logging, entry.get("level", "INFO"), logging.INFO)
            if entry_level_num >= level_num:
                matched.append(entry)
            if len(matched) >= limit:
                break

        return matched

    def clear(self) -> None:
        self.buffer.clear()


# 全域單例
log_buffer = InMemoryLogBuffer()
