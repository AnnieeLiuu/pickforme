# -*- coding: utf-8 -*-
"""Unit tests for seed_specialties_to_db script and SpecialtyGuideRepository SQL tier ranking."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from scripts.seed_specialties_to_db import parse_specialties_json, seed_specialties_to_db
from src.db.repositories.specialty_guide import SpecialtyGuideRepository


@pytest.fixture
def sample_dual_format_json() -> dict:
    return {
        "神保町": {
            "area_name": "神保町",
            "aliases": ["神保町", "jimbocho"],
            "intro": "古書店街飲食文化",
            "specialties": [
                {
                    "index": 1,
                    "cuisine_name": "歐風咖哩",
                    "name_ja": "欧風カレー",
                    "history_and_culture": "咖哩名店聚集",
                    "flavor_profile": "濃郁微辣",
                }
            ],
        },
        "金澤": {
            "area_name": "金澤",
            "aliases": ["金澤", "kanazawa"],
            "categories": {
                "food": {
                    "intro": "金澤美食",
                    "specialties": [{"index": 1, "cuisine_name": "金澤關東煮"}],
                },
                "drink": {
                    "intro": "金澤銘酒",
                    "specialties": [{"index": 1, "cuisine_name": "加賀棒茶"}],
                },
                "souvenir": {
                    "intro": "金澤銘菓",
                    "specialties": [{"index": 1, "cuisine_name": "金箔蜂蜜蛋糕"}],
                },
            },
        },
    }


def test_parse_specialties_json_dual_format(sample_dual_format_json):
    records = parse_specialties_json(sample_dual_format_json)
    # 1 legacy area ('food') + 1 area with 3 categories ('food', 'drink', 'souvenir') = 4
    assert len(records) == 4

    # Check legacy
    jimbocho = [r for r in records if r["area_key"] == "神保町"]
    assert len(jimbocho) == 1
    assert jimbocho[0]["category"] == "food"
    assert jimbocho[0]["source_tier"] == "curated"
    assert jimbocho[0]["area_name"] == "神保町"
    assert "神保町" in jimbocho[0]["aliases"]

    # Check categories
    kanazawa = [r for r in records if r["area_key"] == "金澤"]
    assert len(kanazawa) == 3
    cats = {r["category"] for r in kanazawa}
    assert cats == {"food", "drink", "souvenir"}
    for r in kanazawa:
        assert r["source_tier"] == "curated"
        assert r["area_name"] == "金澤"


def test_parse_real_fixture_exactly_15_records():
    fixture_path = Path("config/local_specialties.json")
    assert fixture_path.exists(), "config/local_specialties.json must exist"
    with open(fixture_path, encoding="utf-8") as f:
        data = json.load(f)

    records = parse_specialties_json(data)
    # Exactly 11 areas in JSON: 9 single ('food') + 2 with 3 categories ('food', 'drink', 'souvenir') = 15 records
    assert len(records) == 15

    for r in records:
        assert r["source_tier"] == "curated"
        assert r["category"] in {"food", "drink", "souvenir"}
        assert len(r["specialties"]) > 0
        assert r["intro"]


@pytest.mark.asyncio
async def test_seed_specialties_to_db_dry_run():
    mock_repo = MagicMock(spec=SpecialtyGuideRepository)
    mock_repo.upsert_guide = AsyncMock()

    count = await seed_specialties_to_db(
        json_path="config/local_specialties.json",
        dry_run=True,
        repo=mock_repo,
    )

    assert count == 15
    mock_repo.upsert_guide.assert_not_called()


@pytest.mark.asyncio
async def test_seed_specialties_to_db_upsert_calls():
    mock_repo = MagicMock(spec=SpecialtyGuideRepository)
    mock_repo.upsert_guide = AsyncMock(return_value=True)

    count = await seed_specialties_to_db(
        json_path="config/local_specialties.json",
        dry_run=False,
        repo=mock_repo,
    )

    assert count == 15
    assert mock_repo.upsert_guide.call_count == 15

    # Check one of the calls
    all_calls = mock_repo.upsert_guide.call_args_list
    tiers = [call.kwargs.get("source_tier") for call in all_calls]
    assert all(t == "curated" for t in tiers)

    # Check categories coverage
    categories = [call.kwargs.get("category") for call in all_calls]
    assert categories.count("food") == 11
    assert categories.count("drink") == 2
    assert categories.count("souvenir") == 2


@pytest.mark.asyncio
async def test_specialty_guide_repository_sql_tier_ranking():
    repo = SpecialtyGuideRepository()
    captured_sql = None
    captured_params = None

    async def mock_exec_get(sql, params):
        nonlocal captured_sql, captured_params
        captured_sql = sql
        captured_params = params
        return {
            "area_key": "金澤",
            "category": "food",
            "area_name": "金澤",
            "aliases": ["金澤"],
            "intro": "金澤美食",
            "specialties": [],
            "source_tier": "curated",
            "grounding_snippets": [],
            "verified": True,
            "updated_at": None,
        }

    repo._exec_get = mock_exec_get  # type: ignore[assignment]

    res = await repo.get_guide("金澤", category="food")
    assert res is not None
    assert captured_sql is not None

    # Verify SQL tier ranking order: manual (1) > curated (2) > grounding/others (3)
    expected_order_clause = "ORDER BY CASE WHEN source_tier = 'manual' THEN 1 WHEN source_tier = 'curated' THEN 2 ELSE 3 END, updated_at DESC"
    assert expected_order_clause in " ".join(captured_sql.split())
