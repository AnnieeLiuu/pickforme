# -*- coding: utf-8 -*-
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from src.services.cuisine_guide import AreaSpecialtyGuide, LocalSpecialtyGuideService


@pytest.fixture(autouse=True)
def clear_service_runtime_cache():
    LocalSpecialtyGuideService._runtime_cache.clear()
    yield
    LocalSpecialtyGuideService._runtime_cache.clear()


@pytest.mark.asyncio
async def test_official_domain_scrape_success_with_shift_jis_and_curated_tier():
    """Test 1: 官方自治體 (.lg.jp / .or.jp) 深度抓取成功 (支援 Shift_JIS 編碼)
    -> 提煉文化導覽 -> 寫入 DB repo source_tier='curated'。
    """
    mock_repo = MagicMock()
    mock_repo.get_guide = AsyncMock(return_value=None)
    mock_repo.upsert_guide = AsyncMock()

    mock_llm = MagicMock()
    mock_llm.call_structured_json = AsyncMock(
        return_value=(
            {
                "area_name": "軽井沢",
                "category": "food",
                "intro": "軽井沢町観光協会と自治体が推薦する代表的な信州高原グルメ文化。",
                "specialties": [
                    {
                        "index": 1,
                        "cuisine_name": "信州手打蕎麥麵",
                        "name_ja": "信州そば",
                        "search_keyword_ja": "軽井沢 信州そば 手打ち",
                        "history_and_culture": "中山道軽井沢宿の旅人に親しまれてきた清澄な伏流水で作る伝統の十割蕎麦。",
                        "flavor_profile": "蕎麥香氣濃郁、麵體爽脆滑順。",
                    },
                    {
                        "index": 2,
                        "cuisine_name": "輕井澤高原手作果醬",
                        "name_ja": "信州果実ジャム",
                        "search_keyword_ja": "軽井沢 ジャム 老舗",
                        "history_and_culture": "傳承傳教士時代的西方保存食文化，以新鮮信州蘋果與藍莓慢火手工熬煮熬製。",
                        "flavor_profile": "果肉豐潤細緻、酸甜濃郁。",
                    },
                    {
                        "index": 3,
                        "cuisine_name": "高原蔬食歐風料理",
                        "name_ja": "高原野菜・欧風料理",
                        "search_keyword_ja": "軽井沢 欧風料理 高原野菜",
                        "history_and_culture": "明治以來作為國際避暑勝地，融合在地朝採高冷野菜與歐陸西餐料理傳統。",
                        "flavor_profile": "鮮脆甘甜、醬汁濃郁細膩。",
                    },
                ],
                "source_tier": "curated",
            },
            "groq",
            300,
        )
    )

    service = LocalSpecialtyGuideService(guide_repo=mock_repo, llm_client=mock_llm)

    # 模擬 Yahoo 搜尋結果頁：包含 .lg.jp 官方自治體連結
    yahoo_search_html = """
    <html>
      <body>
        <div class="sw-Card">
          <a href="https://www.town.karuizawa.nagano.lg.jp/specialties/gourmet.html">軽井沢町公式 特産品・郷土料理</a>
        </div>
      </body>
    </html>
    """

    # 模擬官方頁面（長度 >= 150 字，以 Shift_JIS 編碼）
    official_content_str = (
        "軽井沢町役場観光経済課公式ホームページへようこそ。"
        "軽井沢町は古くから中山道の宿場町として栄え、清らかな浅間山の伏流水が育む信州そばや"
        "高原白菜、キャベツなどの高原野菜が特産品です。"
        "明治時代に避暑地として開拓されて以来、西洋の食文化が根付き、手作りジャムやハム、"
        "ソーセージなどの伝統が代々受け継がれてきました。"
        "四季折々の自然とともに、町民や観光客に親しまれる美食と豊かな食文化を誇ります。"
    )
    official_shift_jis_bytes = official_content_str.encode("shift_jis")

    async def mock_get(url, *args, **kwargs):
        resp = MagicMock(spec=httpx.Response)
        if "yahoo.co.jp" in str(url):
            resp.status_code = 200
            resp.text = yahoo_search_html
            resp.content = yahoo_search_html.encode("utf-8")
            resp.headers = {"content-type": "text/html; charset=utf-8"}
            resp.encoding = "utf-8"
        elif "karuizawa.nagano.lg.jp" in str(url):
            resp.status_code = 200
            resp.content = official_shift_jis_bytes
            resp.headers = {"content-type": "text/html; charset=Shift_JIS"}
            resp.encoding = "shift_jis"
        else:
            resp.status_code = 404
        return resp

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=mock_get)
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    with patch("httpx.AsyncClient", return_value=mock_client):
        guide = await service.get_area_guide("輕井澤", category="food")

    assert guide is not None
    assert guide.source_tier == "curated"
    assert len(guide.specialties) == 3
    assert guide.specialties[0].cuisine_name == "信州手打蕎麥麵"
    # 驗證 DB 寫入 source_tier='curated'
    mock_repo.upsert_guide.assert_awaited_once()
    upsert_kwargs = mock_repo.upsert_guide.await_args.kwargs
    assert upsert_kwargs.get("source_tier") == "curated"


@pytest.mark.asyncio
async def test_official_domain_failure_falls_back_to_yahoo_grounding():
    """Test 2: 官方自治體超時/404/PDF -> 優雅降級至 Yahoo Japan Grounding
    -> 提煉文化導覽 -> 寫入 DB repo source_tier='grounding'。
    """
    mock_repo = MagicMock()
    mock_repo.get_guide = AsyncMock(return_value=None)
    mock_repo.upsert_guide = AsyncMock()

    mock_llm = MagicMock()
    mock_llm.call_structured_json = AsyncMock(
        return_value=(
            {
                "area_name": "秩父",
                "category": "food",
                "intro": "秩父地區以草鞋豬排與味噌馬鈴薯等在地名物聞名。",
                "specialties": [
                    {
                        "index": 1,
                        "cuisine_name": "草鞋豬排丼",
                        "name_ja": "わらじカツ丼",
                        "search_keyword_ja": "秩父 わらじカツ丼",
                        "history_and_culture": "以草鞋外型為靈感的雙片酥脆炸豬排，浸泡秘製甘辛醬汁。",
                        "flavor_profile": "酥脆鮮嫩、鹹甜下飯。",
                    },
                    {
                        "index": 2,
                        "cuisine_name": "秩父味噌馬鈴薯",
                        "name_ja": "みそポテト",
                        "search_keyword_ja": "秩父 みそポテト 郷土料理",
                        "history_and_culture": "秩父傳統農耕間歇點心（小昼飯），蒸馬鈴薯裹粉油炸後淋上甘味噌醬。",
                        "flavor_profile": "外酥內綿、味噌甘甜濃郁。",
                    },
                    {
                        "index": 3,
                        "cuisine_name": "秩父蕎麥麵",
                        "name_ja": "秩父そば",
                        "search_keyword_ja": "秩父そば 手打ち",
                        "history_and_culture": "荒川上游清冽水源與晝夜溫差造就的高品質在地蕎麥。",
                        "flavor_profile": "香氣醇厚、口感彈牙爽口。",
                    },
                ],
                "source_tier": "grounding",
            },
            "gemini",
            280,
        )
    )

    service = LocalSpecialtyGuideService(guide_repo=mock_repo, llm_client=mock_llm)

    # 模擬官方深抓超時，Yahoo grounding 摘要成功回傳
    with (
        patch.object(
            service,
            "_fetch_official_authority_guide",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch.object(
            service,
            "_fetch_yahoo_grounding_snippets",
            new_callable=AsyncMock,
            return_value=[
                "秩父の名物グルメといえば「わらじカツ丼」と郷土料理「みそポテト」。",
                "小昼飯（こじゅうはん）として農家で愛されてきた歴史あるローカルフードです。",
            ],
        ),
    ):
        guide = await service.get_area_guide("秩父", category="food")

    assert guide is not None
    assert guide.source_tier == "grounding"
    assert guide.specialties[0].cuisine_name == "草鞋豬排丼"
    mock_repo.upsert_guide.assert_awaited_once()
    upsert_kwargs = mock_repo.upsert_guide.await_args.kwargs
    assert upsert_kwargs.get("source_tier") == "grounding"


@pytest.mark.asyncio
async def test_both_official_and_grounding_failure_falls_back_to_safe_template():
    """Test 3: 官方爬取與 Grounding 皆失敗 -> 優雅降級至 _build_safe_fallback_guide()。"""
    mock_repo = MagicMock()
    mock_repo.get_guide = AsyncMock(return_value=None)
    mock_repo.upsert_guide = AsyncMock()

    mock_llm = MagicMock()
    service = LocalSpecialtyGuideService(guide_repo=mock_repo, llm_client=mock_llm)

    with (
        patch.object(
            service,
            "_fetch_official_authority_guide",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch.object(
            service,
            "_fetch_yahoo_grounding_snippets",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        guide = await service.get_area_guide("無名小站", category="drink")

    assert guide is not None
    assert guide.source_tier == "fallback"
    assert guide.category == "drink"
    assert len(guide.specialties) == 3
    mock_repo.upsert_guide.assert_not_called()


@pytest.mark.asyncio
async def test_prewarm_area_culture_runs_three_categories_concurrently():
    """Test 4: prewarm_area_culture 透過 asyncio.gather 並發預熱 food / drink / souvenir 三維度。"""
    service = LocalSpecialtyGuideService()

    fake_food = AreaSpecialtyGuide(
        area_name="輕井澤",
        category="food",
        intro="輕井澤美食文化介紹",
        specialties=[
            {
                "index": 1,
                "cuisine_name": "信州蕎麥麵",
                "name_ja": "信州そば",
                "history_and_culture": "歷史悠久之信州名物",
                "flavor_profile": "爽脆滑順",
            }
        ],
        source_tier="curated",
    )
    fake_drink = AreaSpecialtyGuide(
        area_name="輕井澤",
        category="drink",
        intro="輕井澤飲品與喫茶文化介紹",
        specialties=[
            {
                "index": 1,
                "cuisine_name": "輕井澤高原啤酒",
                "name_ja": "軽井沢高原ビール",
                "history_and_culture": "在地精釀代表地酒",
                "flavor_profile": "麥香醇厚",
            }
        ],
        source_tier="curated",
    )
    fake_souvenir = AreaSpecialtyGuide(
        area_name="輕井澤",
        category="souvenir",
        intro="輕井澤伴手禮文化介紹",
        specialties=[
            {
                "index": 1,
                "cuisine_name": "手工果醬禮盒",
                "name_ja": "手作りジャム",
                "history_and_culture": "傳教士手藝百年傳承",
                "flavor_profile": "果香濃郁",
            }
        ],
        source_tier="curated",
    )

    async def mock_get_area_guide(area_name, category="food", llm_client=None):
        if category == "food":
            return fake_food
        elif category == "drink":
            return fake_drink
        elif category == "souvenir":
            return fake_souvenir
        return fake_food

    with patch.object(service, "get_area_guide", side_effect=mock_get_area_guide) as mock_get_guide:
        results = await service.prewarm_area_culture("輕井澤")

        assert isinstance(results, dict)
        assert set(results.keys()) == {"food", "drink", "souvenir"}
        assert results["food"] == fake_food
        assert results["drink"] == fake_drink
        assert results["souvenir"] == fake_souvenir
        assert mock_get_guide.await_count == 3
