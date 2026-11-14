# -*- coding: utf-8 -*-
"""Plan 3 Progressive SkillLoader 單元與整合測試 (0ms 離線測試)"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from src.agent.pipeline import FoodRecommendationFSM
from src.agent.skill_loader import SkillLoader


def test_skill_loader_scenario_assembly_and_token_isolation():
    loader = SkillLoader()

    # 1. 一般美食 >= 3 家：不應載入 sparse-results / drink / souvenir / wide-area
    prompt_food = loader.build_synthesis_system_prompt(
        resolved_category="food",
        store_count=3,
        location_name="新宿",
        cuisine="義大利麵",
        is_wide_area=False,
    )
    assert "State 4 推薦卡片格式與 Grounding 規範" in prompt_food
    assert "少店數誠實引導規範" not in prompt_food
    assert "特色飲品／名茶茶房" not in prompt_food
    assert "伴手禮／銘菓名產" not in prompt_food
    assert "景區/廣域導航指引" not in prompt_food
    assert "在地飲食文化補充" not in prompt_food

    # 2. 少店數 + 飲品 + 廣域：按需載入對應切片與佔位符
    prompt_drink_sparse = loader.build_synthesis_system_prompt(
        resolved_category="drink",
        store_count=2,
        location_name="箱根",
        cuisine="抹茶",
        is_wide_area=True,
    )
    assert "僅有 2 家" in prompt_drink_sparse
    assert "特色茶房／名店" in prompt_drink_sparse
    assert "特色飲品／名茶茶房" in prompt_drink_sparse
    assert "箱根" in prompt_drink_sparse
    assert "景區/廣域導航指引" in prompt_drink_sparse


def test_skill_loader_etiquette_matching_and_cap():
    loader = SkillLoader()

    # 命中拉麵文化 (且禁反引號)
    ramen_skills = loader.match_etiquette_skills(cuisine="二郎系拉麵", location_name="新宿")
    assert len(ramen_skills) == 1
    assert "食券機" in ramen_skills[0]
    assert "嚴禁將日文點餐詞彙放進反引號內" in ramen_skills[0]

    # 無關料理不應載入任何攻略 (0 Token)
    none_skills = loader.match_etiquette_skills(cuisine="義大利麵", location_name="銀座")
    assert none_skills == []

    # 同時命中多品類時受 max_skills 限制
    capped = loader.match_etiquette_skills(
        cuisine="拉麵",
        user_query="想去居酒屋和屋台吃燒肉",
        max_skills=2,
    )
    assert len(capped) == 2


def test_skill_loader_follow_up_isolation_and_fail_open(tmp_path: Path):
    loader = SkillLoader()
    fu_qa = loader.build_follow_up_system_prompt(is_comparison=False)
    fu_comp = loader.build_follow_up_system_prompt(is_comparison=True)

    assert "無須重新輸出完整的 8 欄推薦卡片模板" in fu_qa
    assert "State 4 推薦卡片格式" not in fu_qa
    assert "風味特色與口感差異" in fu_comp
    assert "State 4 推薦卡片格式" not in fu_comp

    # Fail-Open：路徑不存在時不拋例外並退回保底字串
    missing_loader = SkillLoader(
        agent_system_path=tmp_path / "missing.md",
        skills_dir=tmp_path / "no_skills",
    )
    fallback_prompt = missing_loader.build_synthesis_system_prompt(
        resolved_category="food",
        store_count=3,
        location_name="新宿",
        fallback_card_prompt="FALLBACK_PROMPT",
    )
    assert "FALLBACK_PROMPT" in fallback_prompt


@pytest.mark.asyncio
async def test_state4_and_follow_up_use_modular_skill_prompts():
    fsm = FoodRecommendationFSM(
        places_client=MagicMock(),
        tabelog_scraper=MagicMock(),
        entity_resolver=MagicMock(),
        llm_client=MagicMock(),
    )
    fsm.llm_client.call_text = AsyncMock(
        return_value=(
            "1. `麵匠 竹虎`\n"
            "🚶 **步行距離**：從「新宿站」步行約 3 分鐘\n"
            "⭐️ **雙平台評分**：Tabelog 3.65 / Google 4.3\n"
            "💳 **支付方式**：現金\n"
            "🔗 [Tabelog 頁面](https://tabelog.com/1)\n",
            "test-model",
            100,
        )
    )
    stores = [
        {
            "name": "麵匠 竹虎",
            "name_ja": "麺匠 竹虎",
            "tabelog_rating": 3.65,
            "walking_desc": "從「新宿站」步行約 3 分鐘",
            "payment_methods_desc": "現金",
            "tabelog_url": "https://tabelog.com/1",
        }
    ]
    await fsm.execute_state_4_synthesis(
        stores=stores,
        cuisine="拉麵",
        location_name="新宿",
        user_query="新宿二郎系拉麵推薦",
    )
    sent_system = fsm.llm_client.call_text.call_args[0][0][0]["content"]
    assert "在地飲食文化補充 - 日本拉麵點餐潛規則" in sent_system
    assert "僅有 1 家" in sent_system
