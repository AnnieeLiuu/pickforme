from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from src.db.repositories.vector import VectorRepository
from src.services.embedding import EmbeddingService


@pytest.mark.asyncio
async def test_embedding_service_single_text():
    mock_vec = [0.05] * 768
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"embedding": {"values": mock_vec}}

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp):
        service = EmbeddingService(api_key="mock_gemini_key")
        result = await service.get_embedding("麵條偏硬，濃郁豚骨湯頭")

        assert len(result) == 768
        assert result[0] == 0.05


@pytest.mark.asyncio
async def test_embedding_service_empty_text():
    service = EmbeddingService(api_key="mock_gemini_key")
    result = await service.get_embedding("   ")
    assert len(result) == 768
    assert all(v == 0.0 for v in result)


@pytest.mark.asyncio
async def test_embedding_service_batch():
    mock_vec1 = [0.01] * 768
    mock_vec2 = [0.02] * 768
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"embeddings": [{"values": mock_vec1}, {"values": mock_vec2}]}

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp):
        service = EmbeddingService(api_key="mock_gemini_key")
        results = await service.get_embeddings_batch(["拉麵", "壽司"])

        assert len(results) == 2
        assert len(results[0]) == 768
        assert results[0][0] == 0.01
        assert results[1][0] == 0.02


@pytest.mark.asyncio
async def test_vector_repository_insert():
    mock_id = uuid4()
    mock_pool = MagicMock()
    mock_conn = MagicMock()
    mock_cur = AsyncMock()

    mock_pool.connection.return_value.__aenter__.return_value = mock_conn
    mock_conn.cursor.return_value.__aenter__.return_value = mock_cur
    mock_cur.fetchone.return_value = {"id": mock_id}

    with patch("src.db.repositories.vector.get_db_pool", new_callable=AsyncMock, return_value=mock_pool):
        repo = VectorRepository()
        dummy_vec = [0.01] * 768
        inserted = await repo.insert_review_embedding(
            restaurant_id=uuid4(),
            review_text_ja="麺が硬めで美味い",
            embedding=dummy_vec,
            review_text_zh="麵條偏硬好吃",
        )
        assert inserted == mock_id
        assert mock_cur.execute.called


@pytest.mark.asyncio
async def test_vector_repository_search_similar():
    mock_pool = MagicMock()
    mock_conn = MagicMock()
    mock_cur = AsyncMock()

    mock_pool.connection.return_value.__aenter__.return_value = mock_conn
    mock_conn.cursor.return_value.__aenter__.return_value = mock_cur
    mock_cur.fetchall.return_value = [
        {
            "id": uuid4(),
            "restaurant_id": uuid4(),
            "review_text_ja": "スープが濃厚で絶品",
            "review_text_zh": "湯頭濃郁美味",
            "restaurant_name_ja": "風雲児",
            "tabelog_url": "https://tabelog.com/tokyo/A1304/A130401/13044091/",
            "similarity": 0.88,
        }
    ]

    with patch("src.db.repositories.vector.get_db_pool", new_callable=AsyncMock, return_value=mock_pool):
        repo = VectorRepository()
        query_vec = [0.02] * 768
        results = await repo.search_similar_reviews(
            query_embedding=query_vec,
            top_k=3,
            restaurant_ids=[uuid4()],
        )
        assert len(results) == 1
        assert results[0]["restaurant_name_ja"] == "風雲児"
        assert results[0]["similarity"] == 0.88


@pytest.mark.asyncio
async def test_fsm_semantic_rerank_reviews():
    from src.agent.pipeline import FoodRecommendationFSM

    mock_vec_repo = AsyncMock(spec=VectorRepository)
    mock_embed = AsyncMock(spec=EmbeddingService)

    mock_embed.get_embedding.return_value = [0.03] * 768

    test_rid = uuid4()
    mock_vec_repo.search_similar_reviews.return_value = [
        {
            "id": uuid4(),
            "restaurant_id": test_rid,
            "review_text_ja": "麺のコシが強くスープが絡む",
            "review_text_zh": "麵條勁道很有嚼勁，湯頭濃郁吸附力強",
            "restaurant_name_ja": "風雲児",
            "similarity": 0.92,
        }
    ]

    fsm = FoodRecommendationFSM(
        vector_repo=mock_vec_repo,
        embedding_service=mock_embed,
    )

    stores = [
        {"name_ja": "風雲児", "restaurant_id": str(test_rid)},
        {"name_ja": "普通拉麵店", "restaurant_id": str(uuid4())},
    ]

    reranked = await fsm.semantic_rerank_reviews("想找麵條勁道有嚼勁的沾麵", stores)

    assert len(reranked) == 2
    assert reranked[0]["foodie_review_highlight"] == "麵條勁道很有嚼勁，湯頭濃郁吸附力強"
    assert reranked[0]["review_similarity"] == 0.92
    assert "foodie_review_highlight" not in reranked[1]
