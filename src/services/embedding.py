import logging
from typing import Any, Optional

import httpx

from src.config import settings

logger = logging.getLogger(__name__)

GEMINI_EMBEDDING_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:embedContent"
GEMINI_BATCH_EMBEDDING_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:batchEmbedContents"
)


class EmbeddingService:
    """Gemini 向量生成服務 (支援 768 維 halfvec 投影)"""

    def __init__(self, api_key: Optional[str] = None, timeout_sec: float = 15.0):
        self.api_key = api_key or settings.GEMINI_API_KEY
        self.timeout_sec = timeout_sec

    async def get_embedding(self, text: str, dimensions: int = 768) -> list[float]:
        """產生單一文本之語意向量 (預設 768 維 Matryoshka 截斷)"""
        clean_text = text.strip()
        if not clean_text:
            return [0.0] * dimensions

        if not self.api_key:
            logger.warning("GEMINI_API_KEY is not configured; returning zero vector.")
            return [0.0] * dimensions

        url = f"{GEMINI_EMBEDDING_URL}?key={self.api_key}"
        payload: dict[str, Any] = {
            "model": "models/gemini-embedding-001",
            "content": {"parts": [{"text": clean_text}]},
            "output_dimensionality": dimensions,
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout_sec) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    values: list[float] = data.get("embedding", {}).get("values", [])
                    return values
                else:
                    logger.error(f"Gemini embedding API returned status {resp.status_code}: {resp.text}")
                    return [0.0] * dimensions
        except Exception as e:
            logger.error(f"Failed to generate embedding for text: {e}")
            return [0.0] * dimensions

    async def get_embeddings_batch(self, texts: list[str], dimensions: int = 768) -> list[list[float]]:
        """批次產生多筆文本之語意向量"""
        if not texts:
            return []

        if not self.api_key:
            logger.warning("GEMINI_API_KEY is not configured; returning zero vectors.")
            return [[0.0] * dimensions for _ in texts]

        # 淨化文本
        clean_texts = [t.strip() if t.strip() else " " for t in texts]

        url = f"{GEMINI_BATCH_EMBEDDING_URL}?key={self.api_key}"
        payload: dict[str, Any] = {
            "requests": [
                {
                    "model": "models/gemini-embedding-001",
                    "content": {"parts": [{"text": t}]},
                    "output_dimensionality": dimensions,
                }
                for t in clean_texts
            ]
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout_sec) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    embeddings_data = data.get("embeddings", [])
                    return [emb.get("values", [0.0] * dimensions) for emb in embeddings_data]
                else:
                    logger.error(f"Gemini batch embedding API returned status {resp.status_code}: {resp.text}")
                    return [[0.0] * dimensions for _ in texts]
        except Exception as e:
            logger.error(f"Failed to generate batch embeddings: {e}")
            return [[0.0] * dimensions for _ in texts]
