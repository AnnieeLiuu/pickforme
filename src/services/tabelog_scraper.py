import logging
import random
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

    async def search_tabelog_candidates(self, store_name: str, area_name: str = "") -> list[dict[str, Any]]:
        """搜尋 Tabelog 候選店家頁面 (Fail-Fast 降級)"""
        query_str = f"{area_name} {store_name}".strip()
        search_url = f"https://tabelog.com/rstLst/?vs=1&sa={httpx.QueryParams({'sk': query_str})['sk']}"

        html_content = await self._fetch_url_with_retry(search_url)
        if not html_content:
            logger.warning(f"Fail-fast: Could not fetch Tabelog search results for '{query_str}'")
            return []

        return self._parse_search_results(html_content)

    async def scrape_restaurant_details(self, tabelog_url: str) -> dict[str, Any]:
        """抓取 Tabelog 店家詳細頁面評分、預算與電話 (Fail-Fast 降級)"""
        html_content = await self._fetch_url_with_retry(tabelog_url)
        if not html_content:
            logger.warning(f"Fail-fast: Could not fetch Tabelog store details for '{tabelog_url}'")
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
        awards = [elem.get_text(strip=True) for elem in soup.select(".rdheader-official-info__award-title, .rdheader-award") if elem.get_text(strip=True)]
        hyakumeiten = [elem.get_text(strip=True) for elem in soup.select(".hyakumeiten-badge, .rdheader-official-info") if elem.get_text(strip=True)]

        # 支付方式解析 (カード & QRコード)
        supports_paypay = False
        credit_cards = []
        for tr in soup.select(".rstinfo-table tr, #contents-rstdata tr"):
            th = tr.select_one("th")
            td = tr.select_one("td")
            if not th or not td:
                continue
            th_text = th.get_text(strip=True)
            td_text = td.get_text(strip=True)

            if "QRコード" in th_text or "決済" in th_text:
                if "PayPay" in td_text:
                    supports_paypay = True
            elif "カード" in th_text:
                credit_cards = [card for card in ["VISA", "Master", "JCB", "AMEX", "Diners"] if card in td_text]

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
        }

