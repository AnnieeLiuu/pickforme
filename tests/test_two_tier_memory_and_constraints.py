from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from src.agent.stages.enrichment import (
    apply_deterministic_constraints,
    calculate_foodie_score,
    rank_and_select_top_stores,
)
from src.db.repositories.session import SessionRepository


@pytest.mark.asyncio
async def test_session_two_tier_memory_preserves_long_term_preferences():
    repo = SessionRepository()
    stored_json = {
        "current": [{"google_place_id": "p1", "name": "Store 1"}],
        "accumulated_seen_ids": ["p1"],
        "working_memory": {"last_cuisine": "拉麵", "last_location": "新宿"},
        "long_term_preferences": {
            "dietary_restrictions": ["不吃牛"],
            "payment_preferences": ["credit_card"],
        },
    }
    repo.get_session = AsyncMock(return_value={"chat_id": 123, "last_recommended_ids": stored_json})

    wm = await repo.get_working_memory(123)
    assert wm == {"last_cuisine": "拉麵", "last_location": "新宿"}

    prefs = await repo.get_long_term_preferences(123)
    assert "不吃牛" in prefs["dietary_restrictions"]
    assert "credit_card" in prefs["payment_preferences"]


@pytest.mark.asyncio
async def test_session_backward_compatibility_for_legacy_data():
    """Review Focus #2: Legacy None, list, JSON str, or partial dict safely fallback without KeyError/TypeError."""
    repo = SessionRepository()

    # Case 1: None session
    repo.get_session = AsyncMock(return_value=None)
    assert await repo.get_working_memory(1) == {"last_cuisine": None, "last_location": None}
    assert await repo.get_long_term_preferences(1) == {
        "dietary_restrictions": [],
        "payment_preferences": [],
    }

    # Case 2: Legacy list
    repo.get_session = AsyncMock(return_value={"chat_id": 2, "last_recommended_ids": [{"google_place_id": "p1"}]})
    assert await repo.get_working_memory(2) == {"last_cuisine": None, "last_location": None}
    assert await repo.get_long_term_preferences(2) == {
        "dietary_restrictions": [],
        "payment_preferences": [],
    }

    # Case 3: JSON string
    repo.get_session = AsyncMock(
        return_value={
            "chat_id": 3,
            "last_recommended_ids": '{"working_memory": {"last_cuisine": "壽司"}, "long_term_preferences": {"dietary_restrictions": ["不吃豬"]}}',
        }
    )
    assert await repo.get_working_memory(3) == {"last_cuisine": "壽司", "last_location": None}
    assert await repo.get_long_term_preferences(3) == {
        "dietary_restrictions": ["不吃豬"],
        "payment_preferences": [],
    }

    # Case 4: Dict missing working_memory / long_term_preferences
    repo.get_session = AsyncMock(
        return_value={"chat_id": 4, "last_recommended_ids": {"current": [], "accumulated_seen_ids": []}}
    )
    assert await repo.get_working_memory(4) == {"last_cuisine": None, "last_location": None}
    assert await repo.get_long_term_preferences(4) == {
        "dietary_restrictions": [],
        "payment_preferences": [],
    }


@pytest.mark.asyncio
async def test_update_long_term_preferences_merges_and_deduplicates():
    repo = SessionRepository()
    existing = {
        "current": [{"google_place_id": "p1"}],
        "accumulated_seen_ids": ["p1"],
        "working_memory": {"last_cuisine": "拉麵", "last_location": "澀谷"},
        "long_term_preferences": {
            "dietary_restrictions": ["不吃牛"],
            "payment_preferences": ["credit_card"],
        },
    }
    repo.get_session = AsyncMock(return_value={"chat_id": 123, "last_recommended_ids": existing})

    mock_cur = AsyncMock()
    mock_conn = MagicMock()
    mock_conn.commit = AsyncMock()
    mock_conn.cursor.return_value.__aenter__ = AsyncMock(return_value=mock_cur)
    mock_conn.cursor.return_value.__aexit__ = AsyncMock(return_value=None)
    mock_pool = MagicMock()
    mock_pool.connection.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.connection.return_value.__aexit__ = AsyncMock(return_value=None)

    with patch("src.db.repositories.session.get_db_pool", new=AsyncMock(return_value=mock_pool)):
        updated = await repo.update_long_term_preferences(
            123,
            dietary_restrictions=["不吃牛", "不吃辣"],
            payment_preferences=["paypay"],
        )

    assert updated == {
        "dietary_restrictions": ["不吃牛", "不吃辣"],
        "payment_preferences": ["credit_card", "paypay"],
    }
    assert mock_cur.execute.called


@pytest.mark.asyncio
async def test_update_recommended_ids_preserves_long_term_preferences_and_working_memory():
    """Review Focus #4: update_recommended_ids preserves long_term_preferences and working_memory."""
    repo = SessionRepository()
    existing = {
        "current": [{"google_place_id": "p1"}],
        "accumulated_seen_ids": ["p1"],
        "working_memory": {"last_cuisine": "燒肉", "last_location": "銀座"},
        "long_term_preferences": {
            "dietary_restrictions": ["不吃豬"],
            "payment_preferences": ["paypay"],
        },
    }
    repo.get_session = AsyncMock(return_value={"chat_id": 123, "last_recommended_ids": existing})

    mock_cur = AsyncMock()
    mock_conn = MagicMock()
    mock_conn.commit = AsyncMock()
    mock_conn.cursor.return_value.__aenter__ = AsyncMock(return_value=mock_cur)
    mock_conn.cursor.return_value.__aexit__ = AsyncMock(return_value=None)
    mock_pool = MagicMock()
    mock_pool.connection.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.connection.return_value.__aexit__ = AsyncMock(return_value=None)

    with patch("src.db.repositories.session.get_db_pool", new=AsyncMock(return_value=mock_pool)):
        await repo.update_recommended_ids(
            chat_id=123,
            recommended_ids=[{"google_place_id": "p2", "name": "Store 2"}],
            accumulate=True,
            last_cuisine="拉麵",
        )

    import json

    sql_args = mock_cur.execute.call_args[0][1]
    stored = json.loads(sql_args[1])
    assert stored["working_memory"]["last_cuisine"] == "拉麵"
    assert stored["working_memory"]["last_location"] == "銀座"
    assert stored["long_term_preferences"] == {
        "dietary_restrictions": ["不吃豬"],
        "payment_preferences": ["paypay"],
    }
    assert set(stored["accumulated_seen_ids"]) == {"p1", "p2"}


def test_deterministic_constraint_sensor_demotes_cash_only_when_card_requested():
    """Review Focus #5: Demote stores violating payment or dietary constraints by -0.45 without wiping out list."""
    stores = [
        {
            "name_ja": "現金拉麵店",
            "tabelog_rating": 3.75,
            "accepts_cash_only": True,
            "credit_cards": [],
            "supports_paypay": False,
            "distance_meters": 200,
        },
        {
            "name_ja": "可刷卡拉麵名店",
            "tabelog_rating": 3.65,
            "accepts_cash_only": False,
            "credit_cards": ["VISA", "Master"],
            "supports_paypay": True,
            "distance_meters": 200,
        },
    ]
    constrained = apply_deterministic_constraints(
        stores,
        modifier="要可以刷卡",
        preferences={"payment_preferences": ["credit_card"], "dietary_restrictions": []},
    )
    ranked = rank_and_select_top_stores(constrained, top_k=2)
    assert ranked[0]["name_ja"] == "可刷卡拉麵名店"
    assert ranked[1]["constraint_violation"] is True
    assert calculate_foodie_score(ranked[1]) == pytest.approx(3.75 - 0.45)


def test_deterministic_constraint_sensor_dietary_and_paypay():
    stores = [
        {
            "name_ja": "牛かつ もと村",
            "tabelog_rating": 3.80,
            "supports_paypay": False,
            "distance_meters": 200,
        },
        {
            "name_ja": "豚骨拉麵 一蘭",
            "tabelog_rating": 3.60,
            "supports_paypay": True,
            "distance_meters": 200,
        },
        {
            "name_ja": "銀座 鮨 さいとう",
            "tabelog_rating": 3.90,
            "supports_paypay": True,
            "distance_meters": 200,
        },
    ]
    constrained = apply_deterministic_constraints(
        stores,
        modifier="不吃牛、不吃生食、要用PayPay",
        preferences=None,
    )
    assert constrained[0].get("constraint_violation") is True  # 牛 + no paypay
    assert constrained[1].get("constraint_violation") is not True
    assert constrained[2].get("constraint_violation") is True  # 鮨 (生食)
