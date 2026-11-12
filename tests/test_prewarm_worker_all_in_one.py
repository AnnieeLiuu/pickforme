from unittest.mock import AsyncMock

import pytest
from src.db.repositories.prewarm import PrewarmRepository
from src.db.repositories.restaurant import RestaurantRepository
from src.services.cuisine_guide import AreaSpecialtyGuide, LocalSpecialtyGuideService, SpecialtyItem
from src.services.entity_resolver import EntityResolver
from src.services.google_places import GooglePlacesClient
from src.services.tabelog_scraper import TabelogScraper
from src.worker.prewarm import PrewarmWorker


@pytest.fixture
def sample_culture_guides() -> dict[str, AreaSpecialtyGuide]:
    return {
        "food": AreaSpecialtyGuide(
            area_name="函館",
            category="food",
            intro="函館開港歷史孕育了洋食與港都海鮮共生文化：",
            specialties=[
                SpecialtyItem(
                    index=1,
                    cuisine_name="函館鹽味拉麵",
                    name_ja="塩ラーメン",
                    history_and_culture="日本最早開港地區之一，源自華僑傳入的清湯麵食文化。",
                    flavor_profile="澄澈金黃透亮高湯，鹽味溫潤回甘。",
                ),
                SpecialtyItem(
                    index=2,
                    cuisine_name="小丑漢堡中華雞便當",
                    name_ja="ラッキーピエロ",
                    history_and_culture="函館在地經營超過40年的傳奇靈魂速食。",
                    flavor_profile="酸甜多汁炸雞搭配熱騰騰芝麻米飯。",
                ),
                SpecialtyItem(
                    index=3,
                    cuisine_name="活烏賊刺身素麵",
                    name_ja="イカソーメン",
                    history_and_culture="津輕海峽晨捕烏賊，刀工細切如素麵沾生薑醬油。",
                    flavor_profile="肉質晶瑩剔透、鮮脆甜嫩。",
                ),
            ],
            source_tier="curated",
        ),
        "drink": AreaSpecialtyGuide(
            area_name="函館",
            category="drink",
            intro="函館特色飲品與百年喫茶風情：",
            specialties=[
                SpecialtyItem(
                    index=1,
                    cuisine_name="函館葡萄酒",
                    name_ja="函館ワイン",
                    history_and_culture="利用道南涼爽氣候與優良葡萄釀造在地銘柄。",
                    flavor_profile="果香芳醇清雅、口感平衡酸甜。",
                ),
                SpecialtyItem(
                    index=2,
                    cuisine_name="美鈴珈琲手沖",
                    name_ja="美鈴珈琲",
                    history_and_culture="北海道第一家咖啡專賣店，保留古典炭火烘焙傳統。",
                    flavor_profile="焦香深邃厚實、苦甜回甘悠長。",
                ),
                SpecialtyItem(
                    index=3,
                    cuisine_name="女子修道院修道茶",
                    name_ja="トラピスチヌ茶",
                    history_and_culture="百年修道院手作茶葉，香草配方恬淡心靈。",
                    flavor_profile="天然草本香氣、溫潤順口解膩。",
                ),
            ],
            source_tier="curated",
        ),
        "souvenir": AreaSpecialtyGuide(
            area_name="函館",
            category="souvenir",
            intro="傳承百年的港都伴手禮與西式甜點：",
            specialties=[
                SpecialtyItem(
                    index=1,
                    cuisine_name="Snaffle's起司舒芙蕾",
                    name_ja="チーズオムレット",
                    history_and_culture="北海道特級牛乳與乳酪低溫烘烤的招牌半熟甜點。",
                    flavor_profile="綿密如雪、入口即化散發濃厚乳香。",
                ),
                SpecialtyItem(
                    index=2,
                    cuisine_name="手作炭烤生烏賊仙貝",
                    name_ja="いか煎餅",
                    history_and_culture="整隻新鮮烏賊高壓鐵板鐵壓烘烤，港口名產代表。",
                    flavor_profile="酥脆扎實、濃郁烏賊海味與生醬油香。",
                ),
                SpecialtyItem(
                    index=3,
                    cuisine_name="五島軒百年咖哩派",
                    name_ja="五島軒カレーパイ",
                    history_and_culture="明治12年洋食名店秘傳咖哩包覆手工法式酥皮。",
                    flavor_profile="外皮層層酥脆、內餡辛香牛肉濃郁。",
                ),
            ],
            source_tier="curated",
        ),
    }


@pytest.mark.asyncio
async def test_all_in_one_prewarm_success(sample_culture_guides):
    """Test 1: 驗證 All-in-One Prewarm 成功並行處理 20 家餐廳快取與文化三維度，並送出精簡通知卡片"""
    mock_prewarm_repo = AsyncMock(spec=PrewarmRepository)
    mock_prewarm_repo.get_next_pending_job.return_value = {
        "id": "job_aio_1",
        "area_name": "函館",
        "target_count": 20,
        "chat_id": 1234567,
    }

    # 20 家 Google Places
    mock_places_client = AsyncMock(spec=GooglePlacesClient)
    mock_places_client.geocode_location.return_value = (41.7687, 140.7288)
    mock_places_client.search_nearby.return_value = [
        {"google_place_id": f"g_place_{i}", "name": f"函館名店_{i}", "phone": f"0138-00-{i:04d}"} for i in range(20)
    ]

    mock_scraper = AsyncMock(spec=TabelogScraper)
    mock_scraper.search_tabelog_candidates.return_value = [
        {"tabelog_url": "https://tabelog.com/hokkaido/A0105/010501/01000001/", "name_ja": "店鋪"}
    ]
    mock_scraper.scrape_restaurant_details.return_value = {
        "tabelog_rating": 3.75,
        "name_ja": "店鋪",
    }

    mock_resolver = AsyncMock(spec=EntityResolver)
    mock_resolver.resolve_entity.return_value = {
        "match_found": True,
        "candidate": {"tabelog_url": "https://tabelog.com/hokkaido/A0105/010501/01000001/", "tabelog_rating": 3.75},
        "confidence": 0.95,
        "match_method": "phone_exact",
    }

    mock_restaurant_repo = AsyncMock(spec=RestaurantRepository)
    mock_restaurant_repo.get_by_google_place_id.return_value = None

    mock_guide_service = AsyncMock(spec=LocalSpecialtyGuideService)
    mock_guide_service.prewarm_area_culture.return_value = sample_culture_guides

    mock_bot = AsyncMock()

    worker = PrewarmWorker(
        prewarm_repo=mock_prewarm_repo,
        places_client=mock_places_client,
        tabelog_scraper=mock_scraper,
        entity_resolver=mock_resolver,
        restaurant_repo=mock_restaurant_repo,
        guide_service=mock_guide_service,
        scrape_delay_sec=0,
        bot=mock_bot,
    )

    processed = await worker.process_next_job()

    assert processed is True
    # 驗證餐廳快取已處理 20 家
    assert mock_restaurant_repo.save_restaurant_with_tabelog.call_count == 20
    # 驗證文化預熱有被觸發
    mock_guide_service.prewarm_area_culture.assert_called_once_with("函館", llm_client=worker.llm_client)
    # 驗證任務完成標記
    mock_prewarm_repo.update_job_status.assert_called_once_with("job_aio_1", "completed")

    # 驗證 Telegram 卡片通知推播
    mock_bot.send_message.assert_called_once()
    call_kwargs = mock_bot.send_message.call_args[1]
    assert call_kwargs["chat_id"] == 1234567
    assert call_kwargs["parse_mode"] == "Markdown"

    msg_text = call_kwargs["text"]
    assert "20 家" in msg_text
    assert "• 🍱 特色名物：函館鹽味拉麵、小丑漢堡中華雞便當、活烏賊刺身素麵" in msg_text
    assert "• 🍵 在地飲品：函館葡萄酒、美鈴珈琲手沖、女子修道院修道茶" in msg_text
    assert "• 🎁 必買伴手禮：Snaffle's起司舒芙蕾、手作炭烤生烏賊仙貝、五島軒百年咖哩派" in msg_text
    assert "💡 您可以隨時詢問：" in msg_text
    assert "函館 特色美食" in msg_text or "函館 美食" in msg_text
    assert "函館 伴手禮" in msg_text
    # 驗證字數嚴格在限制內
    assert len(msg_text) <= 2000


@pytest.mark.asyncio
async def test_all_in_one_prewarm_partial_failure_isolation():
    """Test 2: 部分故障隔離 (Partial Failure Isolation) - 當文化預熱拋出例外時，餐廳快取照常完成，推播降級通知"""
    mock_prewarm_repo = AsyncMock(spec=PrewarmRepository)
    mock_prewarm_repo.get_next_pending_job.return_value = {
        "id": "job_aio_fail",
        "area_name": "函館",
        "target_count": 5,
        "chat_id": 999111,
    }

    mock_places_client = AsyncMock(spec=GooglePlacesClient)
    mock_places_client.geocode_location.return_value = (41.7687, 140.7288)
    mock_places_client.search_nearby.return_value = [
        {"google_place_id": f"g_place_{i}", "name": f"名店_{i}"} for i in range(5)
    ]

    mock_scraper = AsyncMock(spec=TabelogScraper)
    mock_scraper.search_tabelog_candidates.return_value = []

    mock_resolver = AsyncMock(spec=EntityResolver)
    mock_resolver.resolve_entity.return_value = {"match_found": False, "confidence": 0.0, "match_method": "no_match"}

    mock_restaurant_repo = AsyncMock(spec=RestaurantRepository)
    mock_restaurant_repo.get_by_google_place_id.return_value = None

    # 文化服務拋出網路逾時或解析例外
    mock_guide_service = AsyncMock(spec=LocalSpecialtyGuideService)
    mock_guide_service.prewarm_area_culture.side_effect = RuntimeError("Official authority scraping timeout")

    mock_bot = AsyncMock()

    worker = PrewarmWorker(
        prewarm_repo=mock_prewarm_repo,
        places_client=mock_places_client,
        tabelog_scraper=mock_scraper,
        entity_resolver=mock_resolver,
        restaurant_repo=mock_restaurant_repo,
        guide_service=mock_guide_service,
        scrape_delay_sec=0,
        bot=mock_bot,
    )

    processed = await worker.process_next_job()

    assert processed is True
    # 驗證餐廳快取依舊完整寫入 5 家
    assert mock_restaurant_repo.save_restaurant_with_tabelog.call_count == 5
    # 驗證任務依然為 completed，未被文化端例外所中斷
    mock_prewarm_repo.update_job_status.assert_called_once_with("job_aio_fail", "completed")

    # 驗證發送降級通知
    mock_bot.send_message.assert_called_once()
    call_kwargs = mock_bot.send_message.call_args[1]
    assert call_kwargs["chat_id"] == 999111
    assert "5 家" in call_kwargs["text"]
    assert "預熱完成" in call_kwargs["text"]


@pytest.mark.asyncio
async def test_all_in_one_prewarm_markdown_escaping_and_safety():
    """Test 3: 通知卡片跳脫特殊字元防崩潰 (如 _、[、]) 且長度小於 2000 字元"""
    mock_prewarm_repo = AsyncMock(spec=PrewarmRepository)
    # 地名與項目中帶有下底線與中括號等易破壞 Markdown 格式字元
    mock_prewarm_repo.get_next_pending_job.return_value = {
        "id": "job_aio_escape",
        "area_name": "札幌_大通 [特選區]",
        "target_count": 1,
        "chat_id": 555666,
    }

    mock_places_client = AsyncMock(spec=GooglePlacesClient)
    mock_places_client.geocode_location.return_value = (43.0618, 141.3545)
    mock_places_client.search_nearby.return_value = [{"google_place_id": "g_place_escape", "name": "店鋪_A [本店]"}]

    mock_scraper = AsyncMock(spec=TabelogScraper)
    mock_scraper.search_tabelog_candidates.return_value = []
    mock_resolver = AsyncMock(spec=EntityResolver)
    mock_resolver.resolve_entity.return_value = {"match_found": False}
    mock_restaurant_repo = AsyncMock(spec=RestaurantRepository)
    mock_restaurant_repo.get_by_google_place_id.return_value = None

    special_guide = {
        "food": AreaSpecialtyGuide(
            area_name="札幌_大通 [特選區]",
            category="food",
            intro="札幌大通公園周邊美食指南：",
            specialties=[
                SpecialtyItem(
                    index=1,
                    cuisine_name="味噌_拉麵 [元祖店]",
                    name_ja="味噌ラーメン",
                    history_and_culture="昭和時代發源於薄野與大通一帶。",
                    flavor_profile="濃郁豬骨味噌湯頭。",
                )
            ],
            source_tier="curated",
        )
    }

    mock_guide_service = AsyncMock(spec=LocalSpecialtyGuideService)
    mock_guide_service.prewarm_area_culture.return_value = special_guide

    mock_bot = AsyncMock()

    worker = PrewarmWorker(
        prewarm_repo=mock_prewarm_repo,
        places_client=mock_places_client,
        tabelog_scraper=mock_scraper,
        entity_resolver=mock_resolver,
        restaurant_repo=mock_restaurant_repo,
        guide_service=mock_guide_service,
        scrape_delay_sec=0,
        bot=mock_bot,
    )

    processed = await worker.process_next_job()

    assert processed is True
    mock_bot.send_message.assert_called_once()
    msg_text = mock_bot.send_message.call_args[1]["text"]

    # 確保跳脫特殊 Markdown 字元，不包含裸露的未跳脫下底線與中括號
    assert r"札幌\_大通" in msg_text or r"\_" in msg_text
    assert r"\[特選區\]" in msg_text or r"\[" in msg_text
    assert r"味噌\_拉麵" in msg_text or r"\_" in msg_text
    assert len(msg_text) <= 2000
