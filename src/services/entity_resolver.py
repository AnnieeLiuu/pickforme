import json
import logging
import math
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# 常見 繁體中文 ↔ 日文新字體/簡體 漢字對照表
KANJI_NORMALIZE_MAP = {
    "麵": "面",
    "藏": "蔵",
    "燒": "焼",
    "醬": "醤油",
    "國": "国",
    "樂": "楽",
    "廣": "広",
    "澤": "沢",
    "瀧": "滝",
    "龍": "竜",
}


class EntityVerifyResponse(BaseModel):
    is_same_store: bool
    confidence: float
    reason: str


def normalize_japanese_phone(phone: Optional[str]) -> str:
    """日本電話號碼正規化：+81 轉 0，移除所有非數字字元"""
    if not phone:
        return ""

    cleaned = phone.strip()
    if cleaned.startswith("+81"):
        cleaned = "0" + cleaned[3:]
    elif cleaned.startswith("81") and len(cleaned) > 9:
        cleaned = "0" + cleaned[2:]

    return re.sub(r"\D", "", cleaned)


def normalize_store_name(name: str) -> str:
    """店名正規化：全形轉半形、漢字同義轉譯、去除標點與空格"""
    if not name:
        return ""

    # 1. 繁日漢字同義轉譯
    for trad, jp in KANJI_NORMALIZE_MAP.items():
        name = name.replace(trad, jp)

    # 2. 全形英數/符號轉半形 (Unicode Offset 0xFEE0)
    result = []
    for char in name:
        code = ord(char)
        if 0xFF01 <= code <= 0xFF5E:
            result.append(chr(code - 0xFEE0))
        elif code == 0x3000:
            result.append(" ")
        else:
            result.append(char)
    normalized = "".join(result).lower()

    # 3. 移除中日半全形空白與常見標點
    normalized = re.sub(r"[\s\-\_\·\・\(\)\[\]「」『』【】]", "", normalized)
    return normalized


def calculate_distance_meters(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Haversine 經緯度半徑距離 (公尺)"""
    r = 6371000.0  # 地球半徑 (公尺)
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lng2 - lng1)

    a = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return r * c


def calculate_string_similarity(s1: str, s2: str) -> float:
    """字串相似度比例 (0.0 ~ 1.0)"""
    norm1 = normalize_store_name(s1)
    norm2 = normalize_store_name(s2)
    if not norm1 or not norm2:
        return 0.0
    if norm1 in norm2 or norm2 in norm1:
        return 0.90
    return SequenceMatcher(None, norm1, norm2).ratio()


class EntityResolver:
    def __init__(self, agent_engine: Optional[Any] = None, gemini_api_key: Optional[str] = None):
        self._agent_engine = agent_engine

    def _get_engine(self) -> Any:
        if self._agent_engine is None:
            from src.agent.core import AgentEngine
            from src.agent.tools import AgentToolExecutor

            # 使用獨立的 AgentToolExecutor(entity_resolver=self) 防止循環遞迴
            tool_executor = AgentToolExecutor(entity_resolver=self)
            self._agent_engine = AgentEngine(tool_executor=tool_executor)
        return self._agent_engine

    async def resolve_entity(
        self, google_store: dict[str, Any], tabelog_candidates: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """電話優先 + 店名正規化 + LLM 灰區判定之實體匹配流程"""
        if not tabelog_candidates:
            return {"match_found": False, "match_method": "no_candidates", "confidence": 0.0}

        google_phone = normalize_japanese_phone(google_store.get("phone"))
        google_name = google_store.get("name", "")
        google_lat = google_store.get("lat", 0.0)
        google_lng = google_store.get("lng", 0.0)

        best_candidate: Optional[dict[str, Any]] = None
        best_score = 0.0

        # Step 1: 電話號碼完全比對 (Highest Priority)
        if google_phone:
            for cand in tabelog_candidates:
                cand_phone = normalize_japanese_phone(cand.get("phone"))
                if cand_phone and (
                    google_phone == cand_phone or google_phone in cand_phone or cand_phone in google_phone
                ):
                    return {
                        "match_found": True,
                        "candidate": cand,
                        "match_method": "phone_exact",
                        "confidence": 1.0,
                        "reason": f"電話號碼完全吻合 ({google_phone})",
                    }

        # Step 2: 店名相似度 + 距離計算
        for cand in tabelog_candidates:
            sim_score = calculate_string_similarity(google_name, cand.get("name_ja", ""))

            cand_lat = cand.get("lat")
            cand_lng = cand.get("lng")
            dist_penalty = 0.0
            if cand_lat and cand_lng and google_lat and google_lng:
                dist = calculate_distance_meters(google_lat, google_lng, cand_lat, cand_lng)
                if dist > 1000:
                    dist_penalty = 0.3
                elif dist > 300:
                    dist_penalty = 0.1

            final_rule_score = max(0.0, sim_score - dist_penalty)
            if final_rule_score > best_score:
                best_score = final_rule_score
                best_candidate = cand

        # 高信心度直接通過
        if best_candidate and best_score >= 0.85:
            return {
                "match_found": True,
                "candidate": best_candidate,
                "match_method": "rule_high",
                "confidence": round(best_score, 2),
                "reason": f"店名極高度相似 ({round(best_score, 2)})",
            }

        # Step 3: LLM 灰色地帶判定 (0.60 ~ 0.85)
        if best_candidate and 0.60 <= best_score < 0.85:
            llm_result = await self._verify_with_llm(google_store, best_candidate)
            if llm_result and llm_result.is_same_store:
                return {
                    "match_found": True,
                    "candidate": best_candidate,
                    "match_method": "llm_verify",
                    "confidence": llm_result.confidence,
                    "reason": llm_result.reason,
                }

        return {
            "match_found": False,
            "candidate": None,
            "match_method": "no_match",
            "confidence": round(best_score, 2),
            "reason": "未找到符合高精確度門檻之 Tabelog 店家",
        }

    async def _verify_with_llm(
        self, google_store: dict[str, Any], tabelog_candidate: dict[str, Any]
    ) -> Optional[EntityVerifyResponse]:
        """使用 AgentEngine 多模型自動降級進行灰色地帶比對 (Gemini ➡️ Groq Failover)"""
        prompt_path = Path(__file__).parent.parent.parent / "config" / "prompts" / "entity_verify.md"
        system_instruction = prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else "比對兩家餐廳是否相同"

        user_content = f"""
Google 店家資料:
- 名稱: {google_store.get("name")}
- 地址: {google_store.get("address")}
- 電話: {google_store.get("phone")}
- 座標: ({google_store.get("lat")}, {google_store.get("lng")})

Tabelog 店家資料:
- 名稱: {tabelog_candidate.get("name_ja")}
- 地址: {tabelog_candidate.get("address")}
- 電話: {tabelog_candidate.get("phone")}
"""

        try:
            prompt = (
                f"{system_instruction}\n\n"
                f"{user_content}\n\n"
                "請嚴格只回傳以下 JSON 格式（切勿包含任何 Markdown 標記或額外文字）：\n"
                '{"is_same_store": true, "confidence": 0.95, "reason": "店名相同且座標吻合"}'
            )
            messages = [{"role": "user", "content": prompt}]
            engine = self._get_engine()
            msg_obj, model_used, _ = await engine.call_llm_with_failover(messages)
            raw_text = msg_obj.get("content", "").strip()
            cleaned_text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text, flags=re.MULTILINE).strip()
            data = json.loads(cleaned_text)
            return EntityVerifyResponse(
                is_same_store=bool(data.get("is_same_store", False)),
                confidence=float(data.get("confidence", 0.0)),
                reason=str(data.get("reason", f"由 {model_used} 驗證完成")),
            )
        except Exception as e:
            logger.error(f"Error calling AgentEngine for entity verification: {e}")
            return None
