from unittest.mock import AsyncMock

import pytest
from src.db.repositories.prewarm import PrewarmRepository
from src.worker.prewarm import PrewarmWorker


@pytest.mark.asyncio
async def test_prewarm_worker_no_job():
    mock_repo = AsyncMock(spec=PrewarmRepository)
    mock_repo.get_next_pending_job.return_value = None

    worker = PrewarmWorker(prewarm_repo=mock_repo)
    processed = await worker.process_next_job()

    assert processed is False
    mock_repo.get_next_pending_job.assert_called_once()


@pytest.mark.asyncio
async def test_prewarm_worker_process_job_success():
    mock_repo = AsyncMock(spec=PrewarmRepository)
    mock_repo.get_next_pending_job.return_value = {
        "id": "job_123",
        "area_name": "博多駅",
        "target_count": 2,
    }

    mock_places_client = AsyncMock()
    mock_places_client.geocode_location.return_value = (33.5902, 130.4207)
    mock_places_client.search_nearby.return_value = [
        {"google_place_id": "p1", "name": "一蘭 博多店", "phone": "092-123-4567"},
        {"google_place_id": "p2", "name": "元祖長浜屋", "phone": "092-765-4321"},
    ]

    mock_scraper = AsyncMock()
    mock_scraper.search_tabelog_candidates.return_value = [
        {"tabelog_url": "https://tabelog.com/p1", "name_ja": "一蘭 博多店"}
    ]
    mock_scraper.scrape_restaurant_details.return_value = {
        "tabelog_url": "https://tabelog.com/p1",
        "name_ja": "一蘭 博多店",
        "tabelog_rating": 3.65,
    }

    mock_resolver = AsyncMock()
    mock_resolver.resolve_entity.return_value = {
        "match_found": True,
        "candidate": {"tabelog_url": "https://tabelog.com/p1"},
        "confidence": 1.0,
        "match_method": "phone_exact",
    }

    mock_restaurant_repo = AsyncMock()
    mock_restaurant_repo.get_by_google_place_id.return_value = None

    worker = PrewarmWorker(
        prewarm_repo=mock_repo,
        places_client=mock_places_client,
        tabelog_scraper=mock_scraper,
        entity_resolver=mock_resolver,
        restaurant_repo=mock_restaurant_repo,
        scrape_delay_sec=0.01,  # 測試微小延遲
    )

    processed = await worker.process_next_job()

    assert processed is True
    mock_places_client.geocode_location.assert_called_once_with("博多駅")
    mock_places_client.search_nearby.assert_called_once_with(lat=33.5902, lng=130.4207, radius=800)
    mock_repo.update_job_status.assert_called_once_with("job_123", "completed")


@pytest.mark.asyncio
async def test_prewarm_worker_geocoding_failed():
    mock_repo = AsyncMock(spec=PrewarmRepository)
    mock_repo.get_next_pending_job.return_value = {
        "id": "job_fail_123",
        "area_name": "未知無效地名",
        "target_count": 5,
    }

    mock_places_client = AsyncMock()
    mock_places_client.geocode_location.return_value = None

    worker = PrewarmWorker(prewarm_repo=mock_repo, places_client=mock_places_client)
    processed = await worker.process_next_job()

    assert processed is True
    mock_repo.update_job_status.assert_called_once_with("job_fail_123", "failed")


@pytest.mark.asyncio
async def test_prewarm_worker_with_food_category():
    mock_repo = AsyncMock(spec=PrewarmRepository)
    mock_repo.get_next_pending_job.return_value = {
        "id": "job_ramen",
        "area_name": "新宿御苑 拉麵",
        "target_count": 5,
    }

    mock_places_client = AsyncMock()
    mock_places_client.geocode_location.return_value = (35.685, 139.710)
    mock_places_client.search_nearby.return_value = []

    worker = PrewarmWorker(prewarm_repo=mock_repo, places_client=mock_places_client)
    processed = await worker.process_next_job()

    assert processed is True
    mock_places_client.geocode_location.assert_called_once_with("新宿御苑")
    mock_places_client.search_nearby.assert_called_once_with(lat=35.685, lng=139.710, radius=800, keyword="拉麵")
