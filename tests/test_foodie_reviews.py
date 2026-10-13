from unittest.mock import AsyncMock, patch

import pytest
from src.services.tabelog_scraper import TabelogScraper


@pytest.mark.asyncio
async def test_fetch_tabelog_reviews_parsing_and_noise_filtering():
    """驗證 fetch_tabelog_reviews 能解析老饕食評並主動過濾空洞句與營業時間雜訊"""
    scraper = TabelogScraper()

    # 模擬 Yahoo 搜尋卡片 HTML：包含 1 篇空洞句、1 篇純營業時間、2 篇真人口感評論
    mock_html = """
    <div class="sw-Card">
        <h3>口コミ一覧 : パン・デ・フィロゾフ</h3>
        <p class="sw-Card__summary">パン・デ・フィロゾフのバゲット、本当に最高です。外はパリッと香ばしく、中はしっとりとしていて、小麦の風味が素晴らしい。</p>
    </div>
    <div class="sw-Card">
        <h3>『唯一無二の食パン』by ユーザーA</h3>
        <p class="sw-Card__description">パン・デ・フィロゾフへのユーザーAさんの口コミです。</p>
    </div>
    <div class="sw-Card">
        <h3>店舗情報</h3>
        <p>営業時間: 10:00 - 19:00 定休日: 月曜日 住所: 東京都新宿区東五軒町1-8</p>
    </div>
    <div class="sw-Card">
        <h3>パン・デ・フィロゾフ (神楽坂)</h3>
        <p class="sw-Card__summary">クロワッサンは生地がサクサクでバターの香りが濃厚で絶品でした。行列ができるのも納得の味です。</p>
    </div>
    """

    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.text = mock_html

    with patch("httpx.AsyncClient.get", return_value=mock_resp):
        reviews = await scraper.fetch_tabelog_reviews("Pain des Philosophes", max_reviews=5)

    # 驗證只有 2 篇真人口感評論被採納，空洞句與純營業時間被濾除
    assert len(reviews) == 2
    assert "バゲット、本当に最高です" in reviews[0]["text"]
    assert "クロワッサンは生地がサクサクで" in reviews[1]["text"]
    assert "ユーザーAさんの口コミです" not in [r["text"] for r in reviews]
    assert "営業時間: 10:00" not in [r["text"] for r in reviews]


@pytest.mark.asyncio
async def test_fetch_tabelog_reviews_graceful_fallback_on_network_error():
    """驗證當網路異常或搜尋失敗時，fetch_tabelog_reviews 能優雅返回空清單，絕不阻斷主流程"""
    scraper = TabelogScraper()

    with patch("httpx.AsyncClient.get", side_effect=Exception("Network timeout")):
        reviews = await scraper.fetch_tabelog_reviews("風雲児", max_reviews=5)

    assert reviews == []


@pytest.mark.asyncio
async def test_fetch_tabelog_reviews_empty_store_name():
    """驗證店名為空時立即返回空清單"""
    scraper = TabelogScraper()
    assert await scraper.fetch_tabelog_reviews("") == []
    assert await scraper.fetch_tabelog_reviews("   ") == []
