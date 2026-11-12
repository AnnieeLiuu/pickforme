# -*- coding: utf-8 -*-
"""LocalSpecialtyGuideService: 雙層在地特色美食與飲食文化導覽服務

採用方案 C (Tier 1 內建考據知識庫 + Tier 2 即時日本網頁 Grounding 查證)：
1. Tier 1 (`config/local_specialties.json`)：熱門日本商圈 0ms 秒回、0 Token、100% 零歷史幻覺。
2. Tier 2 (Yahoo Japan Grounding)：未收錄車站自動抓取日本真實網頁摘要，結合 Pydantic Schema 提煉，
   嚴禁模型在無依據下憑空捏造歷史年份或發源故事。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import urllib.parse
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Optional

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

from src.db.repositories.specialty_guide import SpecialtyGuideRepository

if TYPE_CHECKING:
    from src.agent.llm_client import LLMClient

logger = logging.getLogger(__name__)

DEFAULT_SPECIALTIES_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "local_specialties.json"

KANJI_FOLD: dict[str, str] = {
    "輕": "軽",
    "澤": "沢",
    "橫": "横",
    "濱": "浜",
    "淺": "浅",
    "廣": "広",
    "澀": "渋",
    "瀧": "滝",
    "龍": "竜",
    "國": "国",
    "樂": "楽",
    "藏": "蔵",
    "鐵": "鉄",
    "關": "関",
}

CATEGORY_OFFICIAL_KEYWORDS: dict[Literal["food", "drink", "souvenir"], str] = {
    "food": "観光協会 特産品 郷土料理 名物",
    "drink": "観光協会 地酒 銘茶 喫茶 飲み物",
    "souvenir": "観光協会 お土産 銘菓 和菓子 老舗",
}


class SpecialtyItem(BaseModel):
    """單一在地特色美食/飲品/伴手禮與歷史文化項目"""

    index: int = Field(..., ge=1, le=3, description="推薦序號 1, 2 或 3")
    cuisine_name: str = Field(..., min_length=1, description="繁體中文特色料理/品名（如「歐風咖哩」或「加賀棒茶」）")
    name_ja: str = Field(..., min_length=1, description="日文名稱（如「欧風カレー」或「加賀棒茶」）")
    search_keyword_ja: Optional[str] = Field(
        default=None,
        description="日文搜尋專用關鍵字（如「加賀棒茶 ほうじ茶」），供 Places 與 Tabelog 檢索，杜絕中文括號干擾",
    )
    history_and_culture: str = Field(
        ...,
        min_length=5,
        description="該品項與當地的歷史文化背景或在地飲食風俗（必須基於真實考據或檢索摘要，嚴禁憑空捏造年份）",
    )
    flavor_profile: str = Field(..., min_length=3, description="口感層次/包裝禮盒/必嚐亮點")

    def get_search_keyword(self) -> str:
        """取得供搜尋引擎使用的純淨日文關鍵字"""
        if self.search_keyword_ja and self.search_keyword_ja.strip():
            return self.search_keyword_ja.strip()
        cleaned = re.sub(r"[（\(].*?[）\)]", "", self.name_ja).strip()
        cleaned = cleaned.replace("・", " ").replace("、", " ")
        return cleaned or self.cuisine_name


class AreaSpecialtyGuide(BaseModel):
    """地區特色導覽結構化輸出合約 (支援 food / drink / souvenir 三維度)"""

    area_name: str = Field(..., min_length=1, description="日本地區或車站名稱")
    category: Literal["food", "drink", "souvenir"] = Field(
        default="food",
        description="探索維度：food(美食) | drink(特色飲品/茶酒/喫茶) | souvenir(伴手禮/銘菓)",
    )
    intro: str = Field(..., min_length=5, description="該地區該維度之文化總覽介紹（1~2句）")
    specialties: list[SpecialtyItem] = Field(..., min_length=1, max_length=3, description="3 大代表性項目清單")
    source_tier: Literal["curated", "manual", "grounding", "fallback"] = Field(
        default="curated",
        description="知識來源層級：curated(內建考據) | manual(DB手動) | grounding(網頁實時檢索) | fallback(通用保底)",
    )


class LocalSpecialtyGuideService:
    """混合雙層日本在地特色導覽引擎 (內建考據 + DB 持久化 + Yahoo Grounding)"""

    _runtime_cache: dict[tuple[str, str], AreaSpecialtyGuide] = {}

    def __init__(
        self,
        json_path: Optional[Path | str] = None,
        guide_repo: Optional[SpecialtyGuideRepository] = None,
        llm_client: Optional[LLMClient] = None,
    ):
        if json_path is not None:
            self.json_path = Path(json_path) if isinstance(json_path, str) else json_path
        else:
            self.json_path = DEFAULT_SPECIALTIES_PATH
        self._curated_db: dict[str, dict[str, Any]] = self._load_curated_db()
        self.guide_repo = guide_repo
        self.llm_client = llm_client
        self._alias_index: dict[str, str] = self._build_alias_index()

    def _normalize_kanji(self, text: str) -> str:
        """安全進行新字體漢字轉化，以便搜尋日本自治體 (.lg.jp / .or.jp) 官網"""
        res = text
        for trad, shin in KANJI_FOLD.items():
            res = res.replace(trad, shin)
        return res

    def _build_alias_index(self) -> dict[str, str]:
        """建立 O(1) 雙向別名倒排索引 (Inverted Index)，拒絕字元硬編碼替換"""
        index: dict[str, str] = {}
        for canonical_key, payload in self._curated_db.items():
            norm_canon = self._strip_area_suffix(canonical_key).lower()
            if norm_canon:
                index[norm_canon] = canonical_key
            for alias in payload.get("aliases", []):
                norm_a = self._strip_area_suffix(str(alias)).lower()
                if norm_a:
                    index[norm_a] = canonical_key
        return index

    def _load_curated_db(self) -> dict[str, dict[str, Any]]:
        if not self.json_path.exists():
            return {}
        try:
            data = json.loads(self.json_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception as e:
            logger.warning(f"Failed to load local_specialties.json: {e}")
            return {}

    def _strip_area_suffix(self, raw_area: str) -> str:
        """僅安全剝除地點後綴詞，不進行任何危險的跨語言字元替換"""
        cleaned = re.sub(r"(?:車站|駅|站|周邊|附近|商圈|地區|エリア)$", "", (raw_area or "").strip()).strip()
        return cleaned or raw_area.strip()

    def _match_tier_1_curated(
        self, area_name: str, category: Literal["food", "drink", "souvenir"] = "food"
    ) -> Optional[AreaSpecialtyGuide]:
        norm = self._strip_area_suffix(area_name).lower()
        if not norm:
            return None

        # 1. 優先透過倒排索引 O(1) 精準定位正規地區名稱
        canonical_key = self._alias_index.get(norm)

        # 2. 若無精確匹配，進行長度 >= 2 的子字串倒排匹配 (如「兼六園」匹配「金澤」)
        if not canonical_key:
            for alias_term, c_key in self._alias_index.items():
                if len(alias_term) >= 2 and (alias_term in norm or norm in alias_term):
                    canonical_key = c_key
                    break

        if not canonical_key or canonical_key not in self._curated_db:
            return None

        payload = self._curated_db[canonical_key]
        area_display = payload.get("area_name", canonical_key)
        cats = payload.get("categories")
        if isinstance(cats, dict) and category in cats:
            sub = cats[category]
            return AreaSpecialtyGuide.model_validate(
                {
                    "area_name": area_display,
                    "category": category,
                    "intro": sub.get("intro", payload.get("intro", "")),
                    "specialties": sub.get("specialties", []),
                    "source_tier": "curated",
                }
            )
        if category == "food" and payload.get("specialties"):
            return AreaSpecialtyGuide.model_validate(
                {
                    "area_name": area_display,
                    "category": "food",
                    "intro": payload.get("intro", ""),
                    "specialties": payload.get("specialties", []),
                    "source_tier": "curated",
                }
            )
        return None

    async def _fetch_yahoo_grounding_snippets(
        self, area_name: str, category: Literal["food", "drink", "souvenir"] = "food"
    ) -> list[str]:
        """查詢 Yahoo Japan 擷取該地區特定維度之真實網頁摘要"""
        clean_area = self._strip_area_suffix(area_name)
        if category == "drink":
            query = f"{clean_area} 名物 飲み物 お茶 地酒 喫茶 歴史 人気"
        elif category == "souvenir":
            query = f"{clean_area} お土産 名物 和菓子 銘菓 老舗 歴史 人気"
        else:
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
            logger.debug(f"Yahoo grounding for cuisine guide failed ({area_name}, {category}): {e}")
        return snippets[:5]

    def _build_safe_fallback_guide(
        self, area_name: str, category: Literal["food", "drink", "souvenir"] = "food"
    ) -> AreaSpecialtyGuide:
        clean_area = self._strip_area_suffix(area_name)
        if category == "drink":
            return AreaSpecialtyGuide(
                area_name=clean_area,
                category="drink",
                intro=f"「{clean_area}」街區散策之餘，不妨走入在地茶寮與喫茶老店，以下為您整理最具代表性的 3 大在地飲品體驗：",
                specialties=[
                    SpecialtyItem(
                        index=1,
                        cuisine_name="在地焙茶與日本煎茶",
                        name_ja="ほうじ茶・日本茶",
                        search_keyword_ja="日本茶 茶房 ほうじ茶",
                        history_and_culture=f"日本街區鄰近茶道傳統或在地茶商，{clean_area}周邊亦有堅持現泡宇治茶、靜岡茶或在地炒焙茶葉之日本茶專門店。",
                        flavor_profile="茶湯清透甘潤、入口烘焙香氣悠長回甘。",
                    ),
                    SpecialtyItem(
                        index=2,
                        cuisine_name="昭和懷舊純喫茶手沖咖啡",
                        name_ja="純喫茶・ネルドリップ珈琲",
                        search_keyword_ja="純喫茶 ネルドリップ 珈琲",
                        history_and_culture="保留昭和時代皮革沙發與紅磚裝潢的老派喫茶店，以法蘭絨手沖深焙咖啡成為下町文人的歇腳處。",
                        flavor_profile="苦甜濃醇、香氣深邃，適合搭配經典焦糖布丁。",
                    ),
                    SpecialtyItem(
                        index=3,
                        cuisine_name="在地銘柄地酒與角打品飲",
                        name_ja="地酒・日本酒バー",
                        search_keyword_ja="地酒 日本酒 角打ち",
                        history_and_culture=f"匯聚鄰近縣市酒造名產，{clean_area}老酒販亦有小酌文化（角打），提供新鮮開瓶的純米酒。",
                        flavor_profile="米香甘美、冷飲芳醇辛口。",
                    ),
                ],
                source_tier="fallback",
            )
        elif category == "souvenir":
            return AreaSpecialtyGuide(
                area_name=clean_area,
                category="souvenir",
                intro=f"「{clean_area}」匯聚了職人代代傳承的銘菓老舖，以下為您整理最適合自用與送禮的 3 大經典伴手禮：",
                specialties=[
                    SpecialtyItem(
                        index=1,
                        cuisine_name="傳統日式和菓子與最中",
                        name_ja="和菓子・もなか",
                        search_keyword_ja="和菓子 もなか 老舗",
                        history_and_culture=f"百年老舖堅持選用十勝產紅豆慢火熬煮豆餡，{clean_area}和菓子凝聚了江戶與町家文化。",
                        flavor_profile="酥脆糯米外殼包覆細膩甘甜紅豆餡，包裝典雅尊貴。",
                    ),
                    SpecialtyItem(
                        index=2,
                        cuisine_name="炭烤手作仙貝與米菓禮盒",
                        name_ja="手焼き煎餅・米菓",
                        search_keyword_ja="手焼き煎餅 米菓 専門店",
                        history_and_culture="採用日本國產糯米，以備長炭一片片手工烘烤並浸泡生醬油，老舖手作傳承數代。",
                        flavor_profile="米香焦脆扎實、醬油鹹香回甘，保存期長極適合當作伴手禮。",
                    ),
                    SpecialtyItem(
                        index=3,
                        cuisine_name="在地限定洋菓子與長崎蛋糕",
                        name_ja="カステラ・焼き菓子",
                        search_keyword_ja="カステラ 焼き菓子 ギフト",
                        history_and_culture="和洋折衷的經典點心，嚴選優質雞蛋與和三盆糖烘焙，是老少咸宜的人氣銘菓。",
                        flavor_profile="口感濕潤綿密、蛋香濃醇，底層糖粒更添脆甜層次。",
                    ),
                ],
                source_tier="fallback",
            )
        else:
            return AreaSpecialtyGuide(
                area_name=clean_area,
                category="food",
                intro=f"「{clean_area}」匯集了深受日本在地人喜愛的職人料理與商店街飲食文化，以下為您整理當地最受推崇的 3 大代表性美食品類：",
                specialties=[
                    SpecialtyItem(
                        index=1,
                        cuisine_name="職人拉麵與沾麵",
                        name_ja="ラーメン・つけ麺",
                        search_keyword_ja="ラーメン つけ麺",
                        history_and_culture=f"日本車站周邊發展出獨具特色的在地拉麵激戰區，{clean_area}一帶亦聚集了講究高湯熬製與自製麵條的實力派專門店。",
                        flavor_profile="從清澈甘醇的淡麗醬油湯頭到濃郁雞白湯、魚介沾麵皆有代表名店。",
                    ),
                    SpecialtyItem(
                        index=2,
                        cuisine_name="炭火燒肉與日式居酒屋料理",
                        name_ja="焼肉・居酒屋",
                        search_keyword_ja="焼肉 居酒屋",
                        history_and_culture=f"下班後的「暖簾酒場文化」是日本街區的生活靈魂，{clean_area}周邊保留了道地的炭火串燒、和牛燒肉與季節小菜酒場。",
                        flavor_profile="備長炭直火鎖住肉汁香氣，搭配生啤與主廚當日手作料理。",
                    ),
                    SpecialtyItem(
                        index=3,
                        cuisine_name="傳統日式定食與洋食老店",
                        name_ja="定食・洋食・とんかつ",
                        search_keyword_ja="とんかつ 洋食 定食",
                        history_and_culture="融合明治以來和洋折衷傳統的町內食堂，提供現炸厚切豬排、手工漢堡排與時令魚料理。",
                        flavor_profile="金黃酥脆麵衣搭配炊煮飽滿的日本國產越光米與味噌湯。",
                    ),
                ],
                source_tier="fallback",
            )

    async def _fetch_official_authority_guide(
        self,
        area_name: str,
        category: Literal["food", "drink", "souvenir"] = "food",
        llm_client: Optional[LLMClient] = None,
    ) -> Optional[AreaSpecialtyGuide]:
        """向日本官方自治體與觀光協會 (.lg.jp / .or.jp) 進行權威深度抓取"""
        client_to_use = llm_client or self.llm_client
        if client_to_use is None:
            return None

        clean_area = self._strip_area_suffix(area_name)
        area_ja = self._normalize_kanji(clean_area)
        canonical = self._alias_index.get(clean_area.lower()) or self._alias_index.get(area_ja.lower())
        if canonical:
            area_ja = self._normalize_kanji(canonical)

        cat_kw = CATEGORY_OFFICIAL_KEYWORDS.get(category, CATEGORY_OFFICIAL_KEYWORDS["food"])
        query = f"{area_ja} {cat_kw} site:lg.jp OR site:or.jp"
        encoded = urllib.parse.quote_plus(query)
        search_url = f"https://search.yahoo.co.jp/search?p={encoded}"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
        }

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                search_resp = await client.get(search_url, headers=headers)
                if search_resp.status_code != 200:
                    return None

                soup = BeautifulSoup(search_resp.text, "html.parser")
                target_url: Optional[str] = None
                for a_tag in soup.find_all("a", href=True):
                    href = str(a_tag["href"]).strip()
                    if not (href.startswith("http://") or href.startswith("https://")):
                        continue
                    if "yahoo.co.jp" in href:
                        continue
                    if ".pdf" in href.lower():
                        continue
                    try:
                        parsed = urllib.parse.urlparse(href)
                        netloc = parsed.netloc.lower()
                        if (
                            netloc.endswith(".lg.jp")
                            or netloc.endswith(".or.jp")
                            or ".lg.jp/" in href
                            or ".or.jp/" in href
                        ):
                            target_url = href
                            break
                    except Exception:
                        continue

                if not target_url:
                    return None

                page_resp = await client.get(target_url, headers=headers)
                if page_resp.status_code != 200:
                    return None

                # 處理日文編碼 (UTF-8, Shift_JIS, CP932, EUC-JP)
                content_type = page_resp.headers.get("content-type", "").lower()
                if "shift_jis" in content_type or "sjis" in content_type:
                    enc_order = ["shift_jis", "cp932", "utf-8", "euc-jp"]
                elif "euc-jp" in content_type:
                    enc_order = ["euc-jp", "utf-8", "shift_jis"]
                else:
                    enc_order = ["utf-8", "shift_jis", "cp932", "euc-jp"]

                html_text = ""
                for enc in enc_order:
                    try:
                        html_text = page_resp.content.decode(enc)
                        break
                    except (UnicodeDecodeError, LookupError):
                        continue
                if not html_text:
                    html_text = page_resp.content.decode("shift_jis", errors="replace")

                page_soup = BeautifulSoup(html_text, "html.parser")
                for tag in page_soup(["script", "style", "nav", "footer", "header", "noscript", "svg"]):
                    tag.decompose()

                raw_text = page_soup.get_text(" ", strip=True)
                clean_text = re.sub(r"\s+", " ", raw_text).strip()

                if len(clean_text) < 150:
                    return None

                category_desc = (
                    "特色飲品、名茶、地酒或經典喫茶文化"
                    if category == "drink"
                    else ("代表性伴手禮、名產、和菓子或名菓禮盒" if category == "souvenir" else "在地特色美食名物")
                )
                sys_prompt = (
                    f"你是一位博學且嚴謹的日本飲食文化史學家與在地導覽員。\n"
                    f"請「嚴格根據下方日本官方自治體或觀光協會（.lg.jp / .or.jp）之權威網頁內容」，為使用者整理該地區最具代表性的 3 種{category_desc}與歷史文化故事。\n"
                    "【鐵則】：\n"
                    "1. 絕對禁止憑空捏造未在官方資料或真實歷史中出現的年份、店名或發源傳說。\n"
                    "2. 每個品項請包含繁體中文名稱 (cuisine_name)、日文名稱 (name_ja) 與日文純淨搜尋關鍵字 (search_keyword_ja，不可含中文括號)。\n"
                    "3. 若為伴手禮，flavor_profile 請描述口感層次、包裝寓意或禮盒特色；若為飲品請描述香氣、風味與品飲亮點。\n"
                    "4. 請輸出符合 AreaSpecialtyGuide 規範之純 JSON 字串（specialties 必須恰好包含 index 1, 2, 3 共三項）。"
                )
                user_prompt = f"目標地區：{clean_area}\n探索類別：{category}\n官方權威網頁內容：\n{clean_text[:2500]}"

                data, _, _ = await client_to_use.call_structured_json(
                    messages=[
                        {"role": "system", "content": sys_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    schema=AreaSpecialtyGuide,
                )

                if isinstance(data, dict) and data.get("specialties") and len(data["specialties"]) == 3:
                    data["category"] = category
                    data["source_tier"] = "curated"
                    guide = AreaSpecialtyGuide.model_validate(data)
                    cache_key = (clean_area, category)
                    self._runtime_cache[cache_key] = guide

                    if self.guide_repo is not None:
                        try:
                            await self.guide_repo.upsert_guide(
                                area_key=clean_area,
                                category=category,
                                area_name=guide.area_name,
                                intro=guide.intro,
                                specialties=[s.model_dump() for s in guide.specialties],
                                aliases=[clean_area, guide.area_name, area_ja],
                                source_tier="curated",
                                grounding_snippets=[clean_text[:300]],
                                verified=True,
                            )
                        except Exception as db_e:
                            logger.warning(f"Failed to write-back official guide to DB ({clean_area}): {db_e}")

                    return guide
        except Exception as e:
            logger.warning(f"Official authority guide scraping failed ({area_name}, {category}): {e}")

        return None

    async def _fetch_and_synthesize_via_grounding(
        self,
        area_name: str,
        category: Literal["food", "drink", "souvenir"] = "food",
        llm_client: Optional[LLMClient] = None,
    ) -> Optional[AreaSpecialtyGuide]:
        """Tier 2 Yahoo Japan 即時網頁 Grounding 降級合成"""
        client_to_use = llm_client or self.llm_client
        if client_to_use is None:
            return None

        clean_area = self._strip_area_suffix(area_name)
        cache_key = (clean_area, category)
        snippets = await self._fetch_yahoo_grounding_snippets(clean_area, category=category)
        if len(snippets) < 2:
            return None

        grounding_context = "\n".join(f"- {s}" for s in snippets)
        category_desc = (
            "特色飲品、名茶、地酒或經典喫茶文化"
            if category == "drink"
            else ("代表性伴手禮、名產、和菓子或名菓禮盒" if category == "souvenir" else "在地特色美食名物")
        )
        sys_prompt = (
            f"你是一位博學且嚴謹的日本飲食文化史學家與在地導覽員。\n"
            f"請「嚴格根據下方提供的日本網頁檢索摘要」，為使用者整理該地區最具代表性的 3 種{category_desc}與歷史文化故事。\n"
            "【鐵則】：\n"
            "1. 絕對禁止憑空捏造未在檢索摘要或真實歷史中出現的年份、店名或發源傳說。\n"
            "2. 每個品項請包含繁體中文名稱 (cuisine_name)、日文名稱 (name_ja) 與日文純淨搜尋關鍵字 (search_keyword_ja，不可含中文括號)。\n"
            "3. 若為伴手禮，flavor_profile 請描述口感層次、包裝寓意或禮盒特色；若為飲品請描述香氣、風味與品飲亮點。\n"
            "4. 請輸出符合 AreaSpecialtyGuide 規範之純 JSON 字串（specialties 必須恰好包含 index 1, 2, 3 共三項）。"
        )
        user_prompt = f"目標地區：{clean_area}\n探索類別：{category}\n\n日本網頁真實檢索摘要：\n{grounding_context}"
        try:
            data, _, _ = await client_to_use.call_structured_json(
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                schema=AreaSpecialtyGuide,
            )
            if isinstance(data, dict) and data.get("specialties") and len(data["specialties"]) == 3:
                data["category"] = category
                data["source_tier"] = "grounding"
                guide = AreaSpecialtyGuide.model_validate(data)
                self._runtime_cache[cache_key] = guide

                # 品質把關後非阻塞寫回 Neon DB
                if self.guide_repo is not None:
                    try:
                        await self.guide_repo.upsert_guide(
                            area_key=clean_area,
                            category=category,
                            area_name=guide.area_name,
                            intro=guide.intro,
                            specialties=[s.model_dump() for s in guide.specialties],
                            aliases=[clean_area, guide.area_name],
                            source_tier="grounding",
                            grounding_snippets=snippets,
                            verified=False,
                        )
                    except Exception as db_e:
                        logger.warning(f"Failed to write-back guide to DB ({clean_area}): {db_e}")

                return guide
        except Exception as e:
            logger.warning(f"Tier 2 grounding synthesis failed for {clean_area} ({category}): {e}")

        return None

    async def get_area_guide(
        self,
        area_name: str,
        category: Literal["food", "drink", "souvenir"] = "food",
        llm_client: Optional[LLMClient] = None,
    ) -> AreaSpecialtyGuide:
        """取得指定日本地區與類別之導覽 (Tier 1 Curated -> 快取 -> Neon DB -> 官方深抓 -> Tier 2 Grounding -> 保底)"""
        clean_area = self._strip_area_suffix(area_name)
        cache_key = (clean_area, category)

        # 1. Tier 1 黃金考據知識庫 (0ms, 最權威)
        curated = self._match_tier_1_curated(clean_area, category=category)
        if curated is not None:
            return curated

        # 2. 執行期記憶體快取
        if cache_key in self._runtime_cache:
            return self._runtime_cache[cache_key]

        # 3. Neon DB 知識庫 (支援手動維護 manual、官方自治體 curated 或過往寫回 grounding)
        if self.guide_repo is not None:
            try:
                db_record = await self.guide_repo.get_guide(area_query=clean_area, category=category)
                if db_record and db_record.get("specialties"):
                    guide_obj = AreaSpecialtyGuide.model_validate(
                        {
                            "area_name": db_record.get("area_name", clean_area),
                            "category": category,
                            "intro": db_record.get("intro", ""),
                            "specialties": db_record.get("specialties", []),
                            "source_tier": db_record.get("source_tier", "grounding"),
                        }
                    )
                    self._runtime_cache[cache_key] = guide_obj
                    return guide_obj
            except Exception as e:
                logger.warning(f"DB lookup for specialty guide failed ({clean_area}, {category}): {e}")

        client_to_use = llm_client or self.llm_client

        # 4. 官方自治體/觀光協會 (.lg.jp / .or.jp) 深度抓取
        if client_to_use is not None:
            official_guide = await self._fetch_official_authority_guide(
                clean_area, category=category, llm_client=client_to_use
            )
            if official_guide is not None:
                return official_guide

            # 5. Tier 2 Yahoo Japan 真實網頁 Grounding 降級
            grounding_guide = await self._fetch_and_synthesize_via_grounding(
                clean_area, category=category, llm_client=client_to_use
            )
            if grounding_guide is not None:
                return grounding_guide

        # 6. 安全保底 (不寫回 DB、不快取)
        return self._build_safe_fallback_guide(clean_area, category=category)

    async def prewarm_area_culture(
        self, area_name: str, llm_client: Optional[LLMClient] = None
    ) -> dict[str, AreaSpecialtyGuide]:
        """並發預熱特定地區之 food, drink, souvenir 三大文化面向，並保證部分故障隔離"""
        client = llm_client or self.llm_client
        clean_area = self._strip_area_suffix(area_name)
        categories: list[Literal["food", "drink", "souvenir"]] = ["food", "drink", "souvenir"]

        results = await asyncio.gather(
            self.get_area_guide(clean_area, category="food", llm_client=client),
            self.get_area_guide(clean_area, category="drink", llm_client=client),
            self.get_area_guide(clean_area, category="souvenir", llm_client=client),
            return_exceptions=True,
        )

        guides: dict[str, AreaSpecialtyGuide] = {}
        for cat, res in zip(categories, results):
            if isinstance(res, AreaSpecialtyGuide):
                guides[cat] = res
            else:
                logger.warning(f"Prewarm culture category {cat} failed for {clean_area}: {res}")
                guides[cat] = self._build_safe_fallback_guide(clean_area, category=cat)

        return guides

    def render_guide_markdown(self, guide: AreaSpecialtyGuide) -> str:
        """將 AreaSpecialtyGuide 渲染為富含文化厚度且支援互動點選之 Telegram Markdown 卡片"""
        badges = ["1️⃣", "2️⃣", "3️⃣"]
        if guide.category == "drink":
            title = f"🍵 **【{guide.area_name}・在地特色飲品與喫茶酒場文化導覽】**\n"
            detail_label = "😋 **風味與品飲亮點**"
            target_store_desc = "茶寮、喫茶老店或地酒館"
        elif guide.category == "souvenir":
            title = f"🎁 **【{guide.area_name}・必買伴手禮與百年老舖名菓導覽】**\n"
            detail_label = "🎁 **伴手禮特色與風味**"
            target_store_desc = "老舖本店或伴手禮名店"
        else:
            title = f"🏮 **【{guide.area_name}・在地特色美食與飲食文化導覽】**\n"
            detail_label = "😋 **風味與必吃亮點**"
            target_store_desc = "排隊名店"

        lines = [title, f"{guide.intro}\n"]

        for idx, item in enumerate(guide.specialties[:3]):
            badge = badges[idx] if idx < len(badges) else f"{item.index}."
            lines.append(f"### {badge} `{item.cuisine_name}` （`{item.name_ja}`）")
            lines.append(f"📜 **歷史與在地文化**：{item.history_and_culture}")
            lines.append(f"{detail_label}：{item.flavor_profile}\n")

        lines.append("---")
        if guide.category == "drink":
            lines.append("💡 **想尋找哪一種在地好滋味？**")
            lines.append(f"👉 請直接回覆 **`1`**、**`2`**、**`3`**，我立刻為您推薦周邊最受好評的 {target_store_desc}！")
            lines.append("👉 想看其他面向？可隨時詢問 **「有什麼特色美食？」** 或 **「有什麼伴手禮？」**。")
        elif guide.category == "souvenir":
            lines.append("💡 **想入手哪一款經典銘菓？**")
            lines.append(
                f"👉 請直接回覆 **`1`**、**`2`**、**`3`**，我立刻為您標定附近的 {target_store_desc} 與購買地標！"
            )
            lines.append("👉 想看其他面向？可隨時詢問 **「有什麼特色美食？」** 或 **「那喝的呢？」**。")
        else:
            lines.append("💡 **想先品嚐哪一種在地風味？**")
            lines.append(
                "👉 請直接回覆 **`1`**、**`2`**、**`3`**（或料理名），我立刻為您啟動 Tabelog 獵出該類別的 Top 3 名店！"
            )
            lines.append(
                "👉 若不限特定類型，可回覆 **「直接推薦」**；想看飲品或伴手禮也可直接詢問 **「那喝的呢？」** 或 **「有什麼伴手禮？」**！"
            )

        return "\n".join(lines).strip()
