from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from src.main import app
from src.services.cold_storage import ColdStorageArchiver


@pytest.mark.asyncio
async def test_cold_storage_archiver_skipped(tmp_path):
    mock_pool = MagicMock()
    mock_conn = MagicMock()
    mock_cur = AsyncMock()

    mock_pool.connection.return_value.__aenter__.return_value = mock_conn
    mock_conn.cursor.return_value.__aenter__.return_value = mock_cur
    mock_cur.fetchall.return_value = []

    with patch("src.services.cold_storage.get_db_pool", new_callable=AsyncMock, return_value=mock_pool):
        archiver = ColdStorageArchiver(local_dir=tmp_path)
        res = await archiver.archive_traces(retention_days=14)

        assert res["status"] == "skipped"
        assert res["runs_archived"] == 0
        assert res["steps_archived"] == 0


@pytest.mark.asyncio
async def test_cold_storage_archiver_export_and_analyze(tmp_path):
    mock_pool = MagicMock()
    mock_conn = MagicMock()
    mock_conn.commit = AsyncMock()
    mock_cur = AsyncMock()

    mock_pool.connection.return_value.__aenter__.return_value = mock_conn
    mock_conn.cursor.return_value.__aenter__.return_value = mock_cur

    run_id_1 = uuid4()
    now = datetime.now(timezone.utc)

    # Mock agent_runs records
    mock_cur.fetchall.side_effect = [
        # 1st fetchall: agent_runs
        [
            {
                "run_id": run_id_1,
                "chat_id": 12345,
                "user_query": "新宿 拉麵",
                "model_used": "groq/qwen3.8-27b",
                "total_tokens": 1500,
                "latency_ms": 1200,
                "status": "completed",
                "created_at": now,
            }
        ],
        # 2nd fetchall: agent_steps
        [
            {
                "step_id": uuid4(),
                "run_id": run_id_1,
                "step_number": 1,
                "action_type": "state_1_intent",
                "tool_name": "intent_extractor",
                "tool_input": {"text": "新宿 拉麵"},
                "tool_output": {"intent": "search"},
                "latency_ms": 300,
                "error_message": None,
                "created_at": now,
            }
        ],
    ]

    with patch("src.services.cold_storage.get_db_pool", new_callable=AsyncMock, return_value=mock_pool):
        archiver = ColdStorageArchiver(local_dir=tmp_path)
        res = await archiver.archive_traces(retention_days=14, delete_after_archive=True)

        assert res["status"] == "success"
        assert res["runs_archived"] == 1
        assert res["steps_archived"] == 1
        assert len(res["parquet_files"]) == 2

        # 檢驗 DuckDB 分析引擎
        analytics = archiver.analyze_traces_duckdb()
        assert analytics["total_runs"] == 1
        assert analytics["successful_runs"] == 1
        assert analytics["failed_runs"] == 0
        assert analytics["total_tokens_used"] == 1500
        assert len(analytics["models_breakdown"]) == 1
        assert analytics["models_breakdown"][0]["model"] == "groq/qwen3.8-27b"


def test_api_admin_archive_endpoint():
    client = TestClient(app)
    mock_res = {
        "status": "skipped",
        "message": "No traces older than 14 days found.",
        "runs_archived": 0,
        "steps_archived": 0,
        "parquet_files": [],
        "destination": "none",
    }

    with patch.object(ColdStorageArchiver, "archive_traces", new_callable=AsyncMock, return_value=mock_res):
        resp = client.post("/api/admin/archive?days=14&keep_hot=false")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "skipped"
        assert data["runs_archived"] == 0
