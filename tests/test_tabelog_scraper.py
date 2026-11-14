import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from src.services.tabelog_scraper import (
    TabelogScraper,
    _extract_budget_from_snippet,
    _extract_genre_and_relocated,
    _extract_payment_from_snippet,
)


def test_parse_search_results():
    scraper = TabelogScraper()
    fixture_path = Path(__file__).parent / "fixtures" / "tabelog_pages.json"
    data = json.loads(fixture_path.read_text(encoding="utf-8"))

    results = scraper._parse_search_results(data["search_html"])
    assert len(results) == 1
    assert results[0]["name_ja"] == "創始面屋武蔵"
    assert results[0]["tabelog_rating"] == 3.75
    assert results[0]["phone"] == "03-3363-4690"


def test_parse_store_details():
    scraper = TabelogScraper()
    fixture_path = Path(__file__).parent / "fixtures" / "tabelog_pages.json"
    data = json.loads(fixture_path.read_text(encoding="utf-8"))

    url = "https://tabelog.com/tokyo/A1304/A130401/13001013/"
    details = scraper._parse_store_details(data["details_html"], url)
    assert details["name_ja"] == "創始面屋武蔵"
    assert details["tabelog_rating"] == 3.75
    assert details["review_count"] == 850
    assert details["phone"] == "03-3363-4690"


def test_clean_tabelog_url():
    scraper = TabelogScraper()
    raw_tw = "https://tabelog.com/tw/tokyo/A1304/A130402/13050544/"
    assert scraper._clean_tabelog_url(raw_tw) == "https://tabelog.com/tokyo/A1304/A130402/13050544/"

    raw_sub = "https://tabelog.com/tokyo/A1319/A131902/13160998/dtlrvwlst/B356102671/?type=0"
    assert scraper._clean_tabelog_url(raw_sub) == "https://tabelog.com/tokyo/A1319/A131902/13160998/"


@pytest.mark.asyncio
async def test_scrape_restaurant_details_fallback_when_blocked():
    scraper = TabelogScraper()
    candidate = {
        "name_ja": "餃子の福包 新宿店",
        "tabelog_rating": 3.49,
        "review_count": 1500,
        "hyakumeiten": ["餃子 百名店 2021"],
    }
    with patch.object(scraper, "_fetch_url_with_retry", new=AsyncMock(return_value=None)):
        res = await scraper.scrape_restaurant_details(
            "https://tabelog.com/tokyo/A1304/A130402/13050544/",
            fallback_candidate=candidate,
        )
        assert res["tabelog_rating"] == 3.49
        assert res["review_count"] == 1500
        assert "餃子 百名店 2021" in res["hyakumeiten"]


@pytest.mark.asyncio
async def test_search_candidates_grounding_fallback_when_blocked():
    scraper = TabelogScraper()
    mock_candidates = [
        {
            "tabelog_url": "https://tabelog.com/tokyo/A1304/A130402/13050544/",
            "name_ja": "餃子の福包 新宿店",
            "tabelog_rating": 3.49,
            "review_count": 1500,
            "hyakumeiten": [],
            "awards": [],
            "phone": "",
            "address": "",
        }
    ]
    with patch.object(scraper, "_fetch_url_with_retry", new=AsyncMock(return_value=None)):
        with patch.object(scraper, "search_via_grounding", new=AsyncMock(return_value=mock_candidates)):
            results = await scraper.search_tabelog_candidates("餃子の福包 新宿店", "新宿")
            assert len(results) == 1
            assert results[0]["tabelog_rating"] == 3.49
            assert results[0]["name_ja"] == "餃子の福包 新宿店"


def test_extract_budget_from_snippet_lunch_and_dinner():
    snippet = "店舗基本情報 ; ￥6,000～￥7,999. ￥1,000～￥1,999. 利用金額分布を見る · 評価： 3.4 - 139件のレビュー"
    lunch, dinner = _extract_budget_from_snippet(snippet)
    assert lunch == "￥1,000～￥1,999"
    assert dinner == "￥6,000～￥7,999"

    lunch_only, dinner_only = _extract_budget_from_snippet("昼の予算: ～￥999")
    assert lunch_only == "～￥999"
    assert dinner_only is None


@pytest.mark.asyncio
async def test_search_via_grounding_extracts_budget_and_fallbacks():
    scraper = TabelogScraper()
    yahoo_html = """
    <html><body>
      <section class="sw-Card">
        <h3><a href="https://tabelog.com/tokyo/A1304/A130401/13261234/">大衆すき焼き北斗 新宿中央東口店 - 食べログ</a></h3>
        <p>店舗基本情報 ; ￥6,000～￥7,999. ￥1,000～￥1,999. 評価： 3.4 - 139件のレビュー</p>
      </section>
    </body></html>
    """
    mock_resp_budget = MagicMock(status_code=200, text=yahoo_html)
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp_budget)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("src.services.tabelog_scraper.httpx.AsyncClient", return_value=mock_client):
        cands = await scraper.search_via_grounding("大衆すき焼き 北斗 新宿中央東口店")
        assert len(cands) == 1
        assert cands[0]["tabelog_rating"] == 3.4
        assert cands[0]["price_dinner"] == "￥6,000～￥7,999"
        assert cands[0]["price_lunch"] == "￥1,000～￥1,999"
        called_url = mock_client.get.call_args.args[0]
        assert "%E4%BA%88%E7%AE%97" in called_url  # 予算

    # 當含「予算」搜尋無結果時，應自動退回不含「予算」的查詢
    empty_resp = MagicMock(status_code=200, text="<html><body></body></html>")
    fallback_html = """
    <html><body>
      <section class="sw-Card">
        <h3><a href="https://tabelog.com/tokyo/A1304/A130401/13000001/">小巷老店</a></h3>
        <p>食べログ 評価： 3.52 - 40件のレビュー</p>
      </section>
    </body></html>
    """
    fallback_resp = MagicMock(status_code=200, text=fallback_html)
    mock_client_fb = AsyncMock()
    mock_client_fb.get = AsyncMock(side_effect=[empty_resp, fallback_resp])
    mock_client_fb.__aenter__ = AsyncMock(return_value=mock_client_fb)
    mock_client_fb.__aexit__ = AsyncMock(return_value=False)

    with patch("src.services.tabelog_scraper.httpx.AsyncClient", return_value=mock_client_fb):
        fb_cands = await scraper.search_via_grounding("小巷老店")
        assert len(fb_cands) == 1
        assert fb_cands[0]["name_ja"] == "小巷老店"
        assert mock_client_fb.get.call_count == 2


def test_extract_payment_genre_and_relocated_from_snippet():
    snippet = "店舗基本情報 ; ￥6,000～￥7,999. ￥1,000～￥1,999. カード可. （VISA、Master、JCB、AMEX、Diners）. 電子マネー不可. QRコード決済可."
    paypay, cards = _extract_payment_from_snippet(snippet)
    assert paypay is True
    assert cards == ["VISA", "Master", "JCB", "AMEX", "Diners"]

    genre, relocated = _extract_genre_and_relocated(
        "ななや 静岡店 - 新静岡/ジェラート・アイスクリーム | 食べログ", snippet
    )
    assert genre == "ジェラート・アイスクリーム"
    assert relocated is False

    _, relocated_old = _extract_genre_and_relocated("【移転】ななや 静岡店 - 食べログ", snippet)
    assert relocated_old is True


@pytest.mark.asyncio
async def test_fetch_detail_snippets_filters_boilerplate():
    scraper = TabelogScraper()
    html = """
    <html><body>
      <section class="sw-Card">
        <p>日本最大級のグルメサイト「食べログ」では、 ななや 静岡店 の 口コミ 563件の口コミを掲載中。</p>
      </section>
      <section class="sw-Card">
        <p>食べログ 店舗基本情報：全席禁煙、個室なし、予約不可、抹茶ジェラートNo.7が一番人気で濃厚。</p>
      </section>
    </body></html>
    """
    mock_resp = MagicMock(status_code=200, text=html)
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("src.services.tabelog_scraper.httpx.AsyncClient", return_value=mock_client):
        snippets = await scraper.fetch_detail_snippets("ななや 静岡店")
        assert len(snippets) == 1
        assert "全席禁煙" in snippets[0]
