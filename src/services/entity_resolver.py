import json
import logging
import math
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel

from src.agent.bridge import IDEAgentBridge

logger = logging.getLogger(__name__)

# 常見 繁體中文 ↔ 日文新字體/簡體 漢字對照表 (維持嚴格 1:1 漢字映射，避免多字語意擴展)
KANJI_NORMALIZE_MAP = {
    "麵": "面",
    "藏": "蔵",
    "燒": "焼",
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


PREFECTURE_MAP = {
    "東京都": "tokyo",
    "東京": "tokyo",
    "神奈川県": "kanagawa",
    "神奈川": "kanagawa",
    "大阪府": "osaka",
    "大阪": "osaka",
    "京都府": "kyoto",
    "京都": "kyoto",
    "北海道": "hokkaido",
    "愛知県": "aichi",
    "愛知": "aichi",
    "福岡県": "fukuoka",
    "福岡": "fukuoka",
    "千葉県": "chiba",
    "千葉": "chiba",
    "埼玉県": "saitama",
    "埼玉": "saitama",
    "兵庫県": "hyogo",
    "兵庫": "hyogo",
    "広島県": "hiroshima",
    "広島": "hiroshima",
    "宮城県": "miyagi",
    "宮城": "miyagi",
    "静岡県": "shizuoka",
    "静岡": "shizuoka",
    "沖縄県": "okinawa",
    "沖縄": "okinawa",
}


def extract_prefecture_from_tabelog_url(url: Optional[str]) -> Optional[str]:
    """從 Tabelog URL 提取都道府縣代碼 (例如 https://tabelog.com/kanagawa/... -> kanagawa)"""
    if not url:
        return None
    m = re.search(r"tabelog\.com/(?:[a-z]{2}/)?([a-z]+)/", url)
    if m:
        pref = m.group(1).lower()
        if pref not in {"rstlst", "japan"}:
            return pref
    return None


def extract_prefecture_from_address(address: Optional[str]) -> Optional[str]:
    """從中日文/英文地址中提取都道府縣代碼 (例如 神奈川県 -> kanagawa, Kanagawa -> kanagawa)"""
    if not address:
        return None
    for kanji, code in PREFECTURE_MAP.items():
        if kanji in address:
            return code
    addr_lower = address.lower()
    for code in set(PREFECTURE_MAP.values()):
        if re.search(rf"\b{code}\b", addr_lower):
            return code
    return None


STORE_STOP_WORDS = {
    "restaurant",
    "restaurants",
    "cafe",
    "bar",
    "dining",
    "kitchen",
    "bistro",
    "grill",
    "pub",
    "curry",
    "bakery",
    "shop",
    "store",
    "reservation",
    "south",
    "north",
    "east",
    "west",
    "indian",
    "japanese",
    "chinese",
    "italian",
    "food",
    "foods",
    "lounge",
    "house",
    "spot",
}


def calculate_string_similarity(s1: str, s2: str) -> float:
    """字串相似度比例 (0.0 ~ 1.0)，支援子字串包含與核心品牌詞 Token 比對"""
    norm1 = normalize_store_name(s1)
    norm2 = normalize_store_name(s2)
    if not norm1 or not norm2:
        return 0.0
    if norm1 in norm2 or norm2 in norm1:
        return 0.90

    base_ratio = SequenceMatcher(None, norm1, norm2).ratio()

    # 核心品牌詞比對 (排除通用料理/餐廳品類詞，如 NAKSHATRA vs Nakshatra Restaurant)
    raw_tokens1 = re.findall(r"[a-z0-9]+", s1.lower())
    raw_tokens2 = re.findall(r"[a-z0-9]+", s2.lower())
    core1 = {t for t in raw_tokens1 if t not in STORE_STOP_WORDS and len(t) >= 3}
    core2 = {t for t in raw_tokens2 if t not in STORE_STOP_WORDS and len(t) >= 3}

    if core1 and core2:
        if core1 == core2:
            return max(base_ratio, 0.90)
        if core1.issubset(core2) or core2.issubset(core1):
            return max(base_ratio, 0.88)
    elif raw_tokens1 and raw_tokens2:
        # 短店名停用詞保護 (例如 Curry House, The Bakery 避免核心 Token 被清空為 0 分)
        s_raw1 = set(raw_tokens1)
        s_raw2 = set(raw_tokens2)
        if s_raw1 == s_raw2:
            return max(base_ratio, 0.88)
        if s_raw1.issubset(s_raw2) or s_raw2.issubset(s_raw1):
            return max(base_ratio, 0.85)

    return base_ratio


class EntityResolver:
    def __init__(
        self,
        agent_engine: Optional[Any] = None,
        bridge: Optional[IDEAgentBridge] = None,
        gemini_api_key: Optional[str] = None,
    ):
        self._agent_engine = agent_engine
        self.bridge = bridge or IDEAgentBridge()

    def _get_engine(self) -> Any:
        if self._agent_engine is None:
            from src.agent.core import AgentEngine
            from src.agent.tools import AgentToolExecutor

            # 使用獨立的 AgentToolExecutor(entity_resolver=self) 防止循環遞迴
            tool_executor = AgentToolExecutor(entity_resolver=self)
            self._agent_engine = AgentEngine(tool_executor=tool_executor)
        return self._agent_engine

    async def resolve_entity(
        self,
        google_store: dict[str, Any],
        tabelog_candidates: list[dict[str, Any]],
        allow_llm: bool = True,
    ) -> dict[str, Any]:
        """電話優先 + 店名正規化 + LLM 灰區判定之實體匹配流程 (預熱模式下 allow_llm=False 避開 API Key 消耗)"""
        if not tabelog_candidates:
            return {"match_found": False, "match_method": "no_candidates", "confidence": 0.0}

        google_phone = normalize_japanese_phone(google_store.get("phone"))
        google_name = google_store.get("name", "")
        google_lat = google_store.get("lat", 0.0)
        google_lng = google_store.get("lng", 0.0)
        google_pref = extract_prefecture_from_address(google_store.get("address"))

        # Step 0: 地理硬性護欄過濾 (Geo Hard-Veto Guardrail)
        # 過濾明顯跨縣市 (例如 神奈川 vs 東京) 或距離大於 5km 之異地候選人，徹底杜絕全日同名 SEO 名店污染
        valid_candidates: list[dict[str, Any]] = []
        for cand in tabelog_candidates:
            cand_url = cand.get("tabelog_url", "")
            cand_pref = extract_prefecture_from_tabelog_url(cand_url) or extract_prefecture_from_address(
                cand.get("address")
            )

            # 1. 都道府縣一票否決
            if google_pref and cand_pref and google_pref != cand_pref:
                logger.info(
                    f"Prefecture mismatch: Google '{google_name}' ({google_pref}) vs Tabelog '{cand.get('name_ja')}' ({cand_pref}). Vetoed."
                )
                continue

            # 2. 距離硬性上限一票否決 (> 5,000m)
            cand_lat = cand.get("lat")
            cand_lng = cand.get("lng")
            if cand_lat and cand_lng and google_lat and google_lng:
                dist = calculate_distance_meters(google_lat, google_lng, cand_lat, cand_lng)
                if dist > 5000:
                    logger.info(
                        f"Distance hard-cap exceeded ({round(dist)}m > 5000m): '{google_name}' vs '{cand.get('name_ja')}'. Vetoed."
                    )
                    continue

            valid_candidates.append(cand)

        if not valid_candidates:
            return {
                "match_found": False,
                "candidate": None,
                "match_method": "geo_vetoed",
                "confidence": 0.0,
                "reason": "所有 Tabelog 候選店家皆因都道府縣不符或距離過遠被一票否決",
            }

        best_candidate: Optional[dict[str, Any]] = None
        best_score = 0.0

        # Step 1: 電話號碼完全比對 (Highest Priority)
        if google_phone:
            for cand in valid_candidates:
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
        for cand in valid_candidates:
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

        # Step 2.5: 搜尋引擎首位主幹信任 (Top 1 Core Match)
        # 僅適用於通過地理護欄之首位候選人
        if valid_candidates:
            top_cand = valid_candidates[0]
            top_name_norm = normalize_store_name(top_cand.get("name_ja", ""))
            google_name_norm = normalize_store_name(google_name)
            if top_name_norm and (top_name_norm in google_name_norm or google_name_norm in top_name_norm):
                return {
                    "match_found": True,
                    "candidate": top_cand,
                    "match_method": "top1_core_match",
                    "confidence": 0.92,
                    "reason": f"搜尋引擎首位店家主幹完全吻合 ({top_cand.get('name_ja')})",
                }

        # Step 3: 多候選仲裁池 (Multi-Candidate Arbitration Pool)
        # 當為西文/羅馬拼音跨語言案例時，打破單一候選鎖定瓶頸，評估通過地理護欄之前 3 名候選
        has_latin = bool(re.search(r"[a-zA-Z]", google_name))
        pool_to_evaluate: list[dict[str, Any]] = []

        if has_latin:
            # 跨語言羅馬拼音：將前 3 名候選全數列入仲裁池，若 best_candidate 存在則置頂
            for c in valid_candidates[:3]:
                if c not in pool_to_evaluate:
                    pool_to_evaluate.append(c)
        else:
            target = best_candidate or (valid_candidates[0] if valid_candidates else None)
            if target and (0.35 <= best_score < 0.85):
                pool_to_evaluate.append(target)

        if pool_to_evaluate:
            if not allow_llm:
                # 預熱/離線純規則模式：相似度 >= 0.70 直接採納為中等信心匹配
                if best_score >= 0.70 and best_candidate:
                    return {
                        "match_found": True,
                        "candidate": best_candidate,
                        "match_method": "rule_medium",
                        "confidence": round(best_score, 2),
                        "reason": f"預熱純規則中度匹配成功 ({round(best_score, 2)})",
                    }
            else:
                for candidate in pool_to_evaluate:
                    llm_result = await self._verify_with_llm(google_store, candidate)
                    if llm_result and llm_result.is_same_store:
                        return {
                            "match_found": True,
                            "candidate": candidate,
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
        """優先使用 IDE Agent Bridge (PC 開著時) 進行灰色地帶比對，離線時自動降級為雲端多模型 LLM"""
        # 1. PC 開著時：轉發至 IDE Agent Bridge 大腦處理
        try:
            if await self.bridge.is_bridge_available():
                res_dict = await self.bridge.forward_entity_verify_request(google_store, tabelog_candidate)
                if res_dict:
                    return EntityVerifyResponse(
                        is_same_store=bool(res_dict.get("is_same_store", False)),
                        confidence=float(res_dict.get("confidence", 0.0)),
                        reason=str(res_dict.get("reason", "由 IDE Agent 大腦比對完成")),
                    )
        except Exception as e:
            logger.warning(f"IDE Agent 比對失敗，降級至雲端 API: {e}")

        # 2. 離線狀況 (電腦關著)：使用雲端多模型降級機制 (Gemini ➡️ Groq Failover)
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
