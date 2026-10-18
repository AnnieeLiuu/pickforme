import logging
import random
import re
import urllib.parse
from typing import Any, Optional

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]


class TabelogScraper:
    def __init__(self, timeout_sec: float = 3.0, max_retries: int = 2):
        self.timeout_sec = timeout_sec
        self.max_retries = max_retries

    def _get_headers(self) -> dict[str, str]:
        return {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
            "Referer": "https://tabelog.com/",
        }

    def _clean_tabelog_url(self, raw_url: str) -> Optional[str]:
        """將外語版或子路徑 Tabelog 網址正規化為官方標準店家首頁，非單一店家專頁則返回 None"""
        m = re.search(
            r"https?://(?:s\.|tw\.|en\.)?tabelog\.com/(?:tw/|en/)?([a-z]+/[A-Z0-9]+/[A-Z0-9]+/\d+)",
            raw_url,
        )
        if m:
            return f"https://tabelog.com/{m.group(1)}/"
        return None

    async def search_via_grounding(self, store_name: str, area_name: str = "") -> list[dict[str, Any]]:
        """當 Tabelog 官方 HTTP 爬蟲遭 Cloudflare WAF (403/Turnstile) 攔截時，自動啟動 Web Grounding 搜尋備援"""
        query = f"site:tabelog.com {store_name} {area_name}".strip()
        encoded = urllib.parse.quote_plus(query)
        search_url = f"https://search.yahoo.co.jp/search?p={encoded}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
        }
        candidates: list[dict[str, Any]] = []
        html = ""
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=6.0) as client:
                    resp = await client.get(search_url, headers=headers)
                    if resp.status_code == 200:
                        html = resp.text
                        break
                    logger.warning(f"Web Grounding search attempt {attempt + 1} failed with status {resp.status_code}")
            except Exception as e:
                logger.warning(f"Web Grounding search attempt {attempt + 1} exception: {e}")
                if attempt == 0:
                    import asyncio

                    await asyncio.sleep(0.3)

        if not html:
            return []

        soup = BeautifulSoup(html, "html.parser")
        seen_urls: set[str] = set()

        for item in soup.select("section, .sw-Card"):
            raw_classes = item.get("class")
            class_str = " ".join(raw_classes) if isinstance(raw_classes, list) else str(raw_classes or "")
            if "AnswerGourmetSpot__plan" in class_str:
                continue

            text = item.get_text(" ", strip=True)
            if "tabelog.com" not in text and "食べログ" not in text:
                continue

            link_tag = item.select_one("a[href*='tabelog.com']")
            if not link_tag:
                continue
            raw_href = link_tag.get("href")
            if not isinstance(raw_href, str):
                continue
            clean_url = self._clean_tabelog_url(raw_href)
            if not clean_url:
                continue

            title_tag = item.select_one("h3") or item.select_one(".sw-Card__title")
            raw_title = title_tag.get_text(strip=True) if title_tag else link_tag.get_text(strip=True)
            if re.search(r"^(空席状況|クチコミ|口コミ|周辺の地図|写真一覧|予約サイト)", raw_title):
                continue

            name_ja = re.split(r"[（\(\-\|\:\[]", raw_title)[0].strip()
            name_ja = re.sub(
                r"(?:の(?:ご)?予約|\s*(?:online\s*)?reservation)$", "", name_ja, flags=re.IGNORECASE
            ).strip()
            if not name_ja or "食べログ" in name_ja:
                name_ja = store_name

            # 評分 (例如：評価： 3.5)
            rating: Optional[float] = None
            rating_match = re.search(r"評価[：:\s]+([0-9]\.[0-9]{1,2})", text)
            if rating_match:
                try:
                    rating = float(rating_match.group(1))
                except ValueError:
                    rating = None

            # 評論數 (例如：1,531件のレビュー / 口コミ1531人)
            review_count: Optional[int] = None
            review_match = re.search(r"([0-9,]+)件のレビュー|口コミ([0-9,]+)人", text)
            if review_match:
                try:
                    raw_count = review_match.group(1) or review_match.group(2)
                    if raw_count:
                        review_count = int(raw_count.replace(",", ""))
                except ValueError:
                    review_count = None

            # 百名店與榮譽標章
            hyaku_matches = re.findall(r"([^\s\"\'\>]+ 百名店 \d{4})", text)
            hyakumeiten = list(dict.fromkeys(hyaku_matches))

            # 若此 URL 已記錄過，優先補全遺漏的評分與百名店資料
            if clean_url in seen_urls:
                for c in candidates:
                    if c["tabelog_url"] == clean_url:
                        if c.get("tabelog_rating") is None and rating is not None:
                            c["tabelog_rating"] = rating
                            c["review_count"] = review_count
                        if not c.get("hyakumeiten") and hyakumeiten:
                            c["hyakumeiten"] = hyakumeiten
                            c["awards"] = hyakumeiten
                continue

            candidates.append(
                {
                    "tabelog_url": clean_url,
                    "name_ja": name_ja,
                    "tabelog_rating": rating,
                    "review_count": review_count,
                    "hyakumeiten": hyakumeiten,
                    "awards": hyakumeiten,
                    "sample_reviews": [],
                    "phone": "",
                    "address": "",
                }
            )
            seen_urls.add(clean_url)
            if len(candidates) >= 5:
                break

        if candidates:
            logger.info(f"Web Grounding recovered {len(candidates)} Tabelog candidates for '{store_name}'")
        return candidates

    async def fetch_tabelog_reviews(self, store_name: str, max_reviews: int = 5) -> list[dict[str, Any]]:
        """定向提取特定店家之真實老饕口コミ (單一職責，單次 Yahoo 查詢自然全收)"""
        clean_name = re.sub(r"[（\(\-\|\:\[].*$", "", store_name).strip()
        if not clean_name:
            return []
        query = f'site:tabelog.com "{clean_name}" 口コミ'
        encoded = urllib.parse.quote_plus(query)
        search_url = f"https://search.yahoo.co.jp/search?p={encoded}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
        }
        reviews: list[dict[str, Any]] = []
        try:
            async with httpx.AsyncClient(timeout=6.0) as client:
                resp = await client.get(search_url, headers=headers)
                if resp.status_code != 200:
                    return []
                soup = BeautifulSoup(resp.text, "html.parser")
                from src.services.review_sanitizer import is_pure_metadata_noise

                for card in soup.select("section, .sw-Card"):
                    title_elem = card.select_one("h3")
                    title_txt = title_elem.get_text(strip=True) if title_elem else "食べログ 口コミ"
                    desc_tag = card.select_one(".sw-Card__description, .sw-Card__summary, p")
                    if not desc_tag:
                        continue
                    clean_text = re.sub(
                        r"https?://\S+|評価[：:\s]+[0-9\.]+|[0-9,]+件のレビュー",
                        "",
                        desc_tag.get_text(" ", strip=True),
                    ).strip()
                    # 排除「xxx さんの口コミです」空洞索引句
                    if re.search(r"^[^\n]{0,30}さんの口コミです", clean_text) and len(clean_text) < 60:
                        continue
                    if len(clean_text) > 25 and not is_pure_metadata_noise(clean_text):
                        reviews.append(
                            {
                                "source": "tabelog",
                                "title": title_txt[:40],
                                "text": clean_text,
                                "original_text": clean_text,
                            }
                        )
                        if len(reviews) >= max_reviews:
                            break
        except Exception as e:
            logger.warning(f"Error fetching tabelog reviews for '{store_name}': {e}")
        return reviews

    async def search_tabelog_candidates(
        self, store_name: str, area_name: str = "", enable_direct_scrape: bool = False
    ) -> list[dict[str, Any]]:
        """搜尋 Tabelog 候選店家頁面 (Grounding-First: 跳過純 HTTP 直爬 100% 403 撞牆，直達 Yahoo Web Grounding)"""
        if not enable_direct_scrape:
            # Grounding-First 原則：Tabelog 純 HTTP 直爬因 Cloudflare 成功率為 0%，預設直接走 Grounding 省下 400~800ms
            return await self.search_via_grounding(store_name=store_name, area_name=area_name)

        query_str = f"{area_name} {store_name}".strip()
        search_url = f"https://tabelog.com/rstLst/?vs=1&sa={httpx.QueryParams({'sk': query_str})['sk']}"

        html_content = await self._fetch_url_with_retry(search_url)
        if not html_content:
            logger.info(f"Direct scraping blocked for '{query_str}'. Falling back to Web Grounding...")
            return await self.search_via_grounding(store_name=store_name, area_name=area_name)

        parsed = self._parse_search_results(html_content)
        if not parsed:
            logger.info(f"Direct search yielded 0 results for '{query_str}'. Trying Web Grounding fallback...")
            return await self.search_via_grounding(store_name=store_name, area_name=area_name)
        return parsed

    async def scrape_restaurant_details(
        self,
        tabelog_url: str,
        fallback_candidate: Optional[dict[str, Any]] = None,
        enable_direct_scrape: bool = False,
    ) -> dict[str, Any]:
        """抓取 Tabelog 店家詳細頁面評分、預算與電話 (Grounding-First 快速融合)"""
        if not enable_direct_scrape and fallback_candidate:
            # 若已有 Grounding 提取出的候選人資料，預設直接使用，避免無效 403 請求
            return {
                "tabelog_url": tabelog_url,
                "name_ja": fallback_candidate.get("name_ja", ""),
                "tabelog_rating": fallback_candidate.get("tabelog_rating"),
                "review_count": fallback_candidate.get("review_count"),
                "price_lunch": fallback_candidate.get("price_lunch"),
                "price_dinner": fallback_candidate.get("price_dinner"),
                "phone": fallback_candidate.get("phone", ""),
                "awards": fallback_candidate.get("awards", []),
                "hyakumeiten": fallback_candidate.get("hyakumeiten", []),
                "supports_paypay": fallback_candidate.get("supports_paypay"),
                "credit_cards": fallback_candidate.get("credit_cards"),
                "sample_reviews": fallback_candidate.get("sample_reviews", []),
            }

        html_content = await self._fetch_url_with_retry(tabelog_url)
        if not html_content:
            logger.info(f"Direct store fetch blocked for '{tabelog_url}'. Using candidate metadata fallback.")
            if fallback_candidate:
                return {
                    "tabelog_url": tabelog_url,
                    "name_ja": fallback_candidate.get("name_ja", ""),
                    "tabelog_rating": fallback_candidate.get("tabelog_rating"),
                    "review_count": fallback_candidate.get("review_count"),
                    "price_lunch": fallback_candidate.get("price_lunch"),
                    "price_dinner": fallback_candidate.get("price_dinner"),
                    "phone": fallback_candidate.get("phone", ""),
                    "awards": fallback_candidate.get("awards", []),
                    "hyakumeiten": fallback_candidate.get("hyakumeiten", []),
                    "supports_paypay": fallback_candidate.get("supports_paypay"),
                    "credit_cards": fallback_candidate.get("credit_cards"),
                    "sample_reviews": fallback_candidate.get("sample_reviews", []),
                }
            return {}

        return self._parse_store_details(html_content, tabelog_url)

    async def _fetch_url_with_retry(self, url: str) -> Optional[str]:
        """包含重試 2 次 (2s 間隔) 的 Fail-Fast HTTP 請求」"""
        async with httpx.AsyncClient(timeout=self.timeout_sec, follow_redirects=True) as client:
            for attempt in range(1, self.max_retries + 1):
                try:
                    response = await client.get(url, headers=self._get_headers())
                    if response.status_code in [429, 403]:
                        logger.warning(f"Tabelog scraping blocked (HTTP {response.status_code}) on attempt {attempt}")
                        return None
                    response.raise_for_status()
                    return response.text
                except (httpx.TimeoutException, httpx.HTTPStatusError, httpx.RequestError) as e:
                    logger.warning(f"Attempt {attempt}/{self.max_retries} failed for {url}: {e}")
                    if attempt < self.max_retries:
                        await httpx.AsyncClient().get("http://httpbin.org/delay/0")  # 非阻塞微小延遲
            return None

    def _parse_search_results(self, html: str) -> list[dict[str, Any]]:
        """解析搜尋結果頁面列表"""
        soup = BeautifulSoup(html, "html.parser")
        results: list[dict[str, Any]] = []

        items = soup.select(".list-rst")
        for item in items[:5]:
            title_elem = item.select_one(".list-rst__rst-name-target")
            if not title_elem:
                continue

            url = title_elem.get("href", "")
            name = title_elem.get_text(strip=True)

            rating_elem = item.select_one(".list-rst__rating-val")
            rating: Optional[float] = None
            if rating_elem:
                try:
                    rating = float(rating_elem.get_text(strip=True))
                except ValueError:
                    rating = None

            phone_elem = item.select_one(".list-rst__tel-number")
            phone = phone_elem.get_text(strip=True) if phone_elem else ""

            address_elem = item.select_one(".list-rst__address")
            address = address_elem.get_text(strip=True) if address_elem else ""

            results.append(
                {
                    "tabelog_url": url,
                    "name_ja": name,
                    "tabelog_rating": rating,
                    "phone": phone,
                    "address": address,
                }
            )
        return results

    def _parse_store_details(self, html: str, tabelog_url: str) -> dict[str, Any]:
        """解析店家詳細頁面」"""
        soup = BeautifulSoup(html, "html.parser")

        # 評分
        rating_elem = soup.select_one(".rdheader-rating__score-val, .list-rst__rating-val")
        rating: Optional[float] = None
        if rating_elem:
            try:
                rating = float(rating_elem.get_text(strip=True))
            except ValueError:
                rating = None

        # 評論數
        count_elem = soup.select_one(".rdheader-rating__review-target .num")
        review_count: Optional[int] = None
        if count_elem:
            try:
                review_count = int(count_elem.get_text(strip=True).replace(",", ""))
            except ValueError:
                review_count = None

        # 預算 (午餐與晚餐)
        budget_elems = soup.select(".rdheader-budget__price-target")
        price_dinner = budget_elems[0].get_text(strip=True) if len(budget_elems) > 0 else None
        price_lunch = budget_elems[1].get_text(strip=True) if len(budget_elems) > 1 else None

        # 電話號碼
        phone_elem = soup.select_one(".rstinfo-table__tel-num")
        phone = phone_elem.get_text(strip=True) if phone_elem else ""

        # 店名
        name_elem = soup.select_one(".display-name, .rstinfo-table__name")
        name_ja = name_elem.get_text(strip=True) if name_elem else ""

        # 榮譽與標章 (Awards & 百名店)
        awards = [
            elem.get_text(strip=True)
            for elem in soup.select(".rdheader-official-info__award-title, .rdheader-award")
            if elem.get_text(strip=True)
        ]
        hyakumeiten = [
            elem.get_text(strip=True)
            for elem in soup.select(".hyakumeiten-badge, .rdheader-official-info")
            if elem.get_text(strip=True)
        ]

        # 支付方式解析 (カード & QRコード)
        supports_paypay: Optional[bool] = None
        credit_cards: Optional[list[str]] = None
        for tr in soup.select(".rstinfo-table tr, #contents-rstdata tr"):
            th = tr.select_one("th")
            td = tr.select_one("td")
            if not th or not td:
                continue
            th_text = th.get_text(strip=True)
            td_text = td.get_text(strip=True)

            if "QRコード" in th_text or "決済" in th_text:
                supports_paypay = "PayPay" in td_text
            elif "カード" in th_text:
                if "不可" in td_text or "利用不可" in td_text:
                    credit_cards = []
                else:
                    cards = [card for card in ["VISA", "Master", "JCB", "AMEX", "Diners"] if card in td_text]
                    credit_cards = cards if cards else (["可"] if "可" in td_text else [])

        # 口コミ評論摘錄 (解析詳細頁面首頁之熱門食評列表)
        sample_reviews = []
        for rvw_item in soup.select(".rstdtl-top-rvwlst__item, .rvw-item, .js-rvw-item")[:3]:
            title_elem = rvw_item.select_one(".rstdtl-top-rvwlst__comment-title, .rvw-item__title")
            body_elem = rvw_item.select_one(".rstdtl-top-rvwlst__comment, .rvw-item__rvw-comment")
            t_txt = title_elem.get_text(strip=True) if title_elem else ""
            b_txt = body_elem.get_text(strip=True) if body_elem else ""
            combined_txt = f"{t_txt}：{b_txt}" if t_txt and b_txt else (t_txt or b_txt)
            if combined_txt:
                sample_reviews.append(
                    {
                        "source": "tabelog",
                        "title": t_txt,
                        "text": combined_txt,
                        "original_text": combined_txt,
                    }
                )

        return {
            "tabelog_url": tabelog_url,
            "name_ja": name_ja,
            "tabelog_rating": rating,
            "review_count": review_count,
            "price_lunch": price_lunch,
            "price_dinner": price_dinner,
            "phone": phone,
            "awards": awards,
            "hyakumeiten": hyakumeiten,
            "supports_paypay": supports_paypay,
            "credit_cards": credit_cards,
            "sample_reviews": sample_reviews,
        }
