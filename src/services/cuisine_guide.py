# -*- coding: utf-8 -*-
"""LocalSpecialtyGuideService: 雙層在地特色美食與飲食文化導覽服務

採用方案 C (Tier 1 內建考據知識庫 + Tier 2 即時日本網頁 Grounding 查證)：
1. Tier 1 (`config/local_specialties.json`)：熱門日本商圈 0ms 秒回、0 Token、100% 零歷史幻覺。
2. Tier 2 (Yahoo Japan Grounding)：未收錄車站自動抓取日本真實網頁摘要，結合 Pydantic Schema 提煉，
   嚴禁模型在無依據下憑空捏造歷史年份或發源故事。
"""

from __future__ import annotations

import json
import logging
import re
import urllib.parse
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Optional

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from src.agent.llm_client import LLMClient

logger = logging.getLogger(__name__)

DEFAULT_SPECIALTIES_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "local_specialties.json"


class SpecialtyItem(BaseModel):
    """單一在地特色美食與歷史文化項目"""

    index: int = Field(..., ge=1, le=3, description="推薦序號 1, 2 或 3")
    cuisine_name: str = Field(..., min_length=1, description="繁體中文特色料理名稱（如「歐風咖哩」）")
    name_ja: str = Field(..., min_length=1, description="日文檢索關鍵字（如「欧風カレー」）")
    history_and_culture: str = Field(
        ...,
        min_length=5,
        description="該料理與當地的歷史文化背景或在地飲食風俗（必須基於真實考據或檢索摘要，嚴禁憑空捏造年份）",
    )
    flavor_profile: str = Field(..., min_length=3, description="口感層次與經典吃法亮點")


class AreaSpecialtyGuide(BaseModel):
    """地區飲食文化導覽結構化輸出合約"""

    area_name: str = Field(..., min_length=1, description="日本地區或車站名稱")
    intro: str = Field(..., min_length=5, description="該地區飲食文化總覽介紹（1~2句）")
    specialties: list[SpecialtyItem] = Field(..., min_length=1, max_length=3, description="3 大代表性特色料理清單")
    source_tier: Literal["curated", "grounding", "fallback"] = Field(
        default="curated",
        description="知識來源層級：curated(內建考據) | grounding(日本網頁實時檢索) | fallback(通用保底)",
    )


class LocalSpecialtyGuideService:
    """混合雙層日本在地特色美食與飲食文化導覽引擎"""

    _runtime_cache: dict[str, AreaSpecialtyGuide] = {}

    def __init__(self, json_path: Optional[Path] = None):
        self.json_path = json_path or DEFAULT_SPECIALTIES_PATH
        self._curated_db: dict[str, dict[str, Any]] = self._load_curated_db()

    def _load_curated_db(self) -> dict[str, dict[str, Any]]:
        if not self.json_path.exists():
            return {}
        try:
            data = json.loads(self.json_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception as e:
            logger.warning(f"Failed to load local_specialties.json: {e}")
            return {}

    def _normalize_area_key(self, raw_area: str) -> str:
        cleaned = re.sub(r"(?:車站|駅|站|周邊|附近|商圈|地區|エリア)$", "", (raw_area or "").strip()).strip()
        return cleaned or raw_area.strip()

    def _match_tier_1_curated(self, area_name: str) -> Optional[AreaSpecialtyGuide]:
        norm = self._normalize_area_key(area_name).lower()
        if not norm:
            return None

        for canonical_key, payload in self._curated_db.items():
            aliases = [str(a).lower() for a in payload.get("aliases", [])]
            if (
                norm == canonical_key.lower()
                or norm in aliases
                or any(a in norm or norm in a for a in aliases if len(a) >= 2)
            ):
                return AreaSpecialtyGuide.model_validate({**payload, "source_tier": "curated"})
        return None

    async def _fetch_yahoo_grounding_snippets(self, area_name: str) -> list[str]:
        """查詢 Yahoo Japan 擷取該地區真實名物與飲食文化網頁摘要"""
        clean_area = self._normalize_area_key(area_name)
        query = f"{clean_area} 名物 グルメ 発祥 歴史 人気料理"
        encoded = urllib.parse.quote_plus(query)
        url = f"https://search.yahoo.co.jp/search?p={encoded}"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
        }
        snippets: list[str] = []
        try:
            async with httpx.AsyncClient(timeout=4.5) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code != 200:
                    return []
                soup = BeautifulSoup(resp.text, "html.parser")
                for elem in soup.select("section, .sw-Card")[:6]:
                    txt = elem.get_text(" ", strip=True)
                    if txt and len(txt) > 25:
                        snippets.append(txt[:300])
        except Exception as e:
            logger.debug(f"Yahoo grounding for cuisine guide failed ({area_name}): {e}")
        return snippets[:5]

    def _build_safe_fallback_guide(self, area_name: str) -> AreaSpecialtyGuide:
        clean_area = self._normalize_area_key(area_name)
        return AreaSpecialtyGuide(
            area_name=clean_area,
            intro=f"「{clean_area}」匯集了深受日本在地人喜愛的職人料理與商店街飲食文化，以下為您整理當地最受推崇的 3 大代表性美食品類：",
            specialties=[
                SpecialtyItem(
                    index=1,
                    cuisine_name="職人拉麵與沾麵",
                    name_ja="ラーメン・つけ麺",
                    history_and_culture=f"日本車站周邊發展出獨具特色的在地拉麵激戰區，{clean_area}一帶亦聚集了講究高湯熬製與自製麵條的實力派專門店。",
                    flavor_profile="從清澈甘醇的淡麗醬油湯頭到濃郁雞白湯、魚介沾麵皆有代表名店。",
                ),
                SpecialtyItem(
                    index=2,
                    cuisine_name="炭火燒肉與日式居酒屋料理",
                    name_ja="焼肉・居酒屋",
                    history_and_culture=f"下班後的「暖簾酒場文化」是日本街區的生活靈魂，{clean_area}周邊保留了道地的炭火串燒、和牛燒肉與季節小菜酒場。",
                    flavor_profile="備長炭直火鎖住肉汁香氣，搭配生啤與主廚當日手作料理。",
                ),
                SpecialtyItem(
                    index=3,
                    cuisine_name="傳統日式定食與洋食老店",
                    name_ja="定食・洋食・とんかつ",
                    history_and_culture="融合明治以來和洋折衷傳統的町內食堂，提供現炸厚切豬排、手工漢堡排與時令魚料理。",
                    flavor_profile="金黃酥脆麵衣搭配炊煮飽滿的日本國產越光米與味噌湯。",
                ),
            ],
            source_tier="fallback",
        )

    async def get_area_guide(
        self,
        area_name: str,
        llm_client: Optional[LLMClient] = None,
    ) -> AreaSpecialtyGuide:
        """取得指定日本地區之 3 大特色美食與歷史文化導覽 (Tier 1 內建 -> 快取 -> Tier 2 Grounding -> 保底)"""
        clean_area = self._normalize_area_key(area_name)

        # 1. Tier 1 黃金考據知識庫 (0ms)
        curated = self._match_tier_1_curated(clean_area)
        if curated is not None:
            return curated

        # 2. 執行期記憶體快取
        if clean_area in self._runtime_cache:
            return self._runtime_cache[clean_area]

        # 3. Tier 2 Yahoo Japan 真實網頁 Grounding + LLM 結構化提煉
        if llm_client is not None:
            snippets = await self._fetch_yahoo_grounding_snippets(clean_area)
            if snippets:
                grounding_context = "\n".join(f"- {s}" for s in snippets)
                sys_prompt = (
                    "你是一位博學且嚴謹的日本飲食文化史學家與美食導覽員。\n"
                    "請「嚴格根據下方提供的日本網頁檢索摘要」，為使用者整理該地區最具代表性的 3 種特色美食（名物）與飲食歷史文化背景。\n"
                    "【鐵則】：\n"
                    "1. 絕對禁止憑空捏造未在檢索摘要或真實歷史中出現的年份、店名或發源傳說。\n"
                    "2. 若檢索摘要中僅提及特定熱門料理類別而無發源典故，請誠實描述該地區的街區氛圍與飲食特色，不可瞎掰歷史。\n"
                    "3. 請輸出符合 AreaSpecialtyGuide 規範之純 JSON 字串（specialties 必須包含 index 1, 2, 3 共三項）。"
                )
                user_prompt = f"目標日本地區：{clean_area}\n\n日本網頁真實檢索摘要：\n{grounding_context}"
                try:
                    data, _, _ = await llm_client.call_structured_json(
                        messages=[
                            {"role": "system", "content": sys_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        schema=AreaSpecialtyGuide,
                    )
                    if isinstance(data, dict) and data.get("specialties"):
                        data["source_tier"] = "grounding"
                        guide = AreaSpecialtyGuide.model_validate(data)
                        self._runtime_cache[clean_area] = guide
                        return guide
                except Exception as e:
                    logger.warning(f"Tier 2 grounding synthesis failed for {clean_area}: {e}")

        # 4. 安全保底
        fallback_guide = self._build_safe_fallback_guide(clean_area)
        return fallback_guide

    def render_guide_markdown(self, guide: AreaSpecialtyGuide) -> str:
        """將 AreaSpecialtyGuide 渲染為富含文化厚度且支援互動點選之 Telegram Markdown 卡片"""
        badges = ["1️⃣", "2️⃣", "3️⃣"]
        lines = [
            f"🏮 **【{guide.area_name}・在地特色美食與飲食文化導覽】**\n",
            f"{guide.intro}\n",
        ]

        for idx, item in enumerate(guide.specialties[:3]):
            badge = badges[idx] if idx < len(badges) else f"{item.index}."
            lines.append(f"### {badge} `{item.cuisine_name}` （`{item.name_ja}`）")
            lines.append(f"📜 **歷史與在地文化**：{item.history_and_culture}")
            lines.append(f"😋 **風味與必吃亮點**：{item.flavor_profile}\n")

        lines.append("---")
        lines.append("💡 **想先品嚐哪一種在地風味？**")
        lines.append(
            "👉 請直接回覆 **`1`**、**`2`**、**`3`**（或輸入料理名稱），我立刻為您啟動 Tabelog 雙源獵店找出該類別的 Top 3 名店！"
        )
        lines.append("👉 若不限特定類型，也可直接回覆 **「直接推薦」** 由我為您挑選周邊最高分餐廳！")
        return "\n".join(lines).strip()
