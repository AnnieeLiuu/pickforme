# -*- coding: utf-8 -*-
"""Unit & Integration Tests for Wide-Area Interactive Prewarm Guardrail."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from src.agent.core import AgentEngine
from src.bot.handlers import TelegramBotHandler
from src.db.repositories.session import SessionRepository


class InMemorySessionRepo:
    """Lightweight in-memory session repository for fast, deterministic testing."""

    def __init__(self) -> None:
        self.wm: dict[str, object] = {}
        self.history: list[dict[str, str]] = []

    async def get_working_memory(self, chat_id: int) -> dict[str, object]:
        return dict(self.wm)

    async def update_working_memory(self, chat_id: int, **kwargs: object) -> dict[str, object]:
        if kwargs.get("clear_pending_prewarm") or kwargs.get("pending_prewarm_options") == {}:
            self.wm.pop("pending_prewarm_options", None)
        elif kwargs.get("pending_prewarm_options") is not None:
            self.wm["pending_prewarm_options"] = kwargs["pending_prewarm_options"]
        for k in ["last_location", "last_cuisine", "suggested_cuisines", "suggested_locations", "pending_cuisine"]:
            if k in kwargs and kwargs[k] is not None:
                self.wm[k] = kwargs[k]
        return dict(self.wm)

    async def get_session(self, chat_id: int) -> dict[str, object]:
        return {"working_memory": dict(self.wm)}

    async def get_recent_history(self, chat_id: int, max_turns: int = 3) -> list[dict[str, str]]:
        return list(self.history)

    async def add_conversation_turn(self, chat_id: int, user_message: str, assistant_response: str) -> None:
        self.history.append({"user": user_message, "assistant": assistant_response})


def _create_update(text: str, chat_id: int = 12345, update_id: int = 100) -> dict[str, object]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "from": {"id": chat_id},
            "chat": {"id": chat_id},
            "text": text,
        },
    }


@pytest.mark.asyncio
async def test_prewarm_wide_area_prompt_hokkaido():
    """Test 1: /prewarm 北海道 detects wide-area, saves pending options, and prompts user."""
    mock_bot = AsyncMock()
    mock_prewarm = AsyncMock()
    session_repo = InMemorySessionRepo()
    agent_engine = AsyncMock()
    agent_engine.disambiguate_area.return_value = (
        True,
        "北海道",
        [
            {"name": "札幌", "desc": "札幌拉麵", "search_loc": "札幌車站"},
            {"name": "函館", "desc": "函館海鮮", "search_loc": "函館車站"},
            {"name": "小樽", "desc": "小樽壽司", "search_loc": "小樽車站"},
        ],
    )

    handler = TelegramBotHandler(
        bot=mock_bot,
        session_repo=session_repo,  # type: ignore[arg-type]
        prewarm_repo=mock_prewarm,
        agent_engine=agent_engine,
    )

    with patch("src.config.settings.TELEGRAM_ALLOWED_USER_ID", 0):
        await handler.handle_update(_create_update("/prewarm 北海道", update_id=101))

    # Prewarm job should NOT be created yet
    mock_prewarm.create_job.assert_not_called()

    # Working memory should have pending_prewarm_options
    wm = await session_repo.get_working_memory(12345)
    assert "pending_prewarm_options" in wm
    pending = wm["pending_prewarm_options"]
    assert isinstance(pending, dict)
    assert pending["pref"] == "北海道"
    assert len(pending["options"]) == 3

    # Bot should send disambiguation prompt
    mock_bot.send_message.assert_called_once()
    sent_text = mock_bot.send_message.call_args[1]["text"]
    assert "北海道幅員遼闊" in sent_text
    assert "札幌" in sent_text
    assert "函館" in sent_text
    assert "小樽" in sent_text
    assert "全部" in sent_text
    assert "取消" in sent_text


@pytest.mark.asyncio
async def test_prewarm_wide_area_select_option():
    """Test 2: Following prompt, user inputs '1' -> creates job for 靜岡市區 and clears state."""
    mock_bot = AsyncMock()
    mock_prewarm = AsyncMock()
    mock_worker = AsyncMock()
    mock_worker.process_next_job.return_value = True
    mock_prewarm.create_job.return_value = uuid4()
    session_repo = InMemorySessionRepo()
    agent_engine = AsyncMock()
    agent_engine.disambiguate_area.return_value = (
        True,
        "靜岡",
        [
            {"name": "靜岡市區", "desc": "靜岡車站周邊", "search_loc": "靜岡車站"},
            {"name": "熱海", "desc": "熱海海景溫泉海鮮", "search_loc": "熱海車站"},
            {"name": "濱松", "desc": "濱名湖鰻魚飯與濱松餃子", "search_loc": "濱松車站"},
        ],
    )

    handler = TelegramBotHandler(
        bot=mock_bot,
        session_repo=session_repo,  # type: ignore[arg-type]
        prewarm_repo=mock_prewarm,
        agent_engine=agent_engine,
        prewarm_worker=mock_worker,
    )

    with patch("src.config.settings.TELEGRAM_ALLOWED_USER_ID", 0):
        # Step 1: trigger wide area
        await handler.handle_update(_create_update("預熱 靜岡", update_id=201))
        assert "pending_prewarm_options" in (await session_repo.get_working_memory(12345))

        # Step 2: user selects '1'
        mock_bot.reset_mock()
        await handler.handle_update(_create_update("1", update_id=202))
        await asyncio.sleep(0)

    # Should create job for option 1 (靜岡車站)
    mock_prewarm.create_job.assert_called_once()
    mock_worker.process_next_job.assert_called_once()
    call_args = mock_prewarm.create_job.call_args[1]
    assert "靜岡" in call_args["area_name"]
    assert call_args["chat_id"] == 12345

    # pending_prewarm_options should be cleared
    wm = await session_repo.get_working_memory(12345)
    assert "pending_prewarm_options" not in wm

    # Bot sends confirmation
    mock_bot.send_message.assert_called_once()
    assert "排入 Neon 預熱佇列" in mock_bot.send_message.call_args[1]["text"]


@pytest.mark.asyncio
async def test_prewarm_wide_area_select_all():
    """Test 3: Following prompt, user inputs '全部' -> creates prewarm jobs for all sub-areas."""
    mock_bot = AsyncMock()
    mock_prewarm = AsyncMock()
    mock_worker = AsyncMock()
    mock_worker.process_next_job.return_value = True
    mock_prewarm.create_job.side_effect = [uuid4(), uuid4(), uuid4()]
    session_repo = InMemorySessionRepo()
    agent_engine = AsyncMock()
    agent_engine.disambiguate_area.return_value = (
        True,
        "北海道",
        [
            {"name": "札幌", "desc": "札幌拉麵", "search_loc": "札幌車站"},
            {"name": "函館", "desc": "函館海鮮", "search_loc": "函館車站"},
            {"name": "小樽", "desc": "小樽壽司", "search_loc": "小樽車站"},
        ],
    )

    handler = TelegramBotHandler(
        bot=mock_bot,
        session_repo=session_repo,  # type: ignore[arg-type]
        prewarm_repo=mock_prewarm,
        agent_engine=agent_engine,
        prewarm_worker=mock_worker,
    )

    with patch("src.config.settings.TELEGRAM_ALLOWED_USER_ID", 0):
        # Step 1: trigger wide area
        await handler.handle_update(_create_update("/prewarm 北海道", update_id=301))
        assert "pending_prewarm_options" in (await session_repo.get_working_memory(12345))

        # Step 2: user selects '全部'
        mock_bot.reset_mock()
        await handler.handle_update(_create_update("全部", update_id=302))
        await asyncio.sleep(0)

    # Should create 3 jobs
    assert mock_prewarm.create_job.call_count == 3
    assert mock_worker.process_next_job.call_count == 3
    created_areas = [call[1]["area_name"] for call in mock_prewarm.create_job.call_args_list]
    assert any("札幌" in a for a in created_areas)
    assert any("函館" in a for a in created_areas)
    assert any("小樽" in a for a in created_areas)

    # pending_prewarm_options cleared
    wm = await session_repo.get_working_memory(12345)
    assert "pending_prewarm_options" not in wm

    # Confirmation sent
    mock_bot.send_message.assert_called_once()
    sent_text = mock_bot.send_message.call_args[1]["text"]
    assert "全數 3 個商圈" in sent_text or "全部 3 個商圈" in sent_text


@pytest.mark.asyncio
async def test_prewarm_wide_area_cancel():
    """Test 4: Following prompt, user inputs '取消' or '/cancel' -> cancels and replies."""
    mock_bot = AsyncMock()
    mock_prewarm = AsyncMock()
    session_repo = InMemorySessionRepo()
    agent_engine = AsyncMock()
    agent_engine.disambiguate_area.return_value = (
        True,
        "長野",
        [
            {"name": "長野市區", "desc": "善光寺、長野站", "search_loc": "長野車站"},
            {"name": "松本", "desc": "松本城、信州蕎麥麵", "search_loc": "松本車站"},
            {"name": "輕井澤", "desc": "度假村、法式料理", "search_loc": "輕井澤車站"},
        ],
    )

    handler = TelegramBotHandler(
        bot=mock_bot,
        session_repo=session_repo,  # type: ignore[arg-type]
        prewarm_repo=mock_prewarm,
        agent_engine=agent_engine,
    )

    with patch("src.config.settings.TELEGRAM_ALLOWED_USER_ID", 0):
        # Step 1: trigger wide area
        await handler.handle_update(_create_update("/prewarm 長野", update_id=401))
        assert "pending_prewarm_options" in (await session_repo.get_working_memory(12345))

        # Step 2: user inputs '取消'
        mock_bot.reset_mock()
        await handler.handle_update(_create_update("取消", update_id=402))

    mock_prewarm.create_job.assert_not_called()
    wm = await session_repo.get_working_memory(12345)
    assert "pending_prewarm_options" not in wm
    mock_bot.send_message.assert_called_once()
    assert "已取消預熱任務。" in mock_bot.send_message.call_args[1]["text"]


@pytest.mark.asyncio
async def test_prewarm_wide_area_fallback_on_new_command_or_query():
    """Test 5: Following prompt, user inputs a new command or normal query -> clears state and proceeds."""
    mock_bot = AsyncMock()
    mock_prewarm = AsyncMock()
    session_repo = InMemorySessionRepo()
    agent_engine = AsyncMock()
    agent_engine.disambiguate_area.side_effect = [
        (
            True,
            "北海道",
            [
                {"name": "札幌", "desc": "札幌拉麵", "search_loc": "札幌車站"},
                {"name": "函館", "desc": "函館海鮮", "search_loc": "函館車站"},
            ],
        ),
        (
            True,
            "靜岡",
            [
                {"name": "靜岡市區", "desc": "靜岡車站", "search_loc": "靜岡車站"},
                {"name": "熱海", "desc": "熱海溫泉", "search_loc": "熱海車站"},
            ],
        ),
    ]
    agent_engine.run_agent.return_value = "推薦的新宿燒肉店家..."

    handler = TelegramBotHandler(
        bot=mock_bot,
        session_repo=session_repo,  # type: ignore[arg-type]
        prewarm_repo=mock_prewarm,
        agent_engine=agent_engine,
    )

    with patch("src.config.settings.TELEGRAM_ALLOWED_USER_ID", 0):
        # Step 1: trigger wide area
        await handler.handle_update(_create_update("/prewarm 北海道", update_id=501))
        assert "pending_prewarm_options" in (await session_repo.get_working_memory(12345))

        # Case A: user inputs /status
        mock_bot.reset_mock()
        await handler.handle_update(_create_update("/status", update_id=502))
        # Status command was executed, pending state was cleared
        mock_bot.send_message.assert_called_once()
        assert "系統健康狀態總覽" in mock_bot.send_message.call_args[1]["text"]
        wm = await session_repo.get_working_memory(12345)
        assert "pending_prewarm_options" not in wm

        # Re-arm pending state
        await handler.handle_update(_create_update("/prewarm 靜岡", update_id=503))
        assert "pending_prewarm_options" in (await session_repo.get_working_memory(12345))

        # Case B: user inputs normal food query '新宿 燒肉'
        mock_bot.reset_mock()
        await handler.handle_update(_create_update("新宿 燒肉", update_id=504))
        # Agent engine was executed, pending state was cleared
        agent_engine.run_agent.assert_called_once()
        wm = await session_repo.get_working_memory(12345)
        assert "pending_prewarm_options" not in wm


@pytest.mark.asyncio
async def test_prewarm_regular_area_direct_creation():
    """Test 6: /prewarm 箱根 -> directly creates 1 prewarm job without disambiguation prompt."""
    mock_bot = AsyncMock()
    mock_prewarm = AsyncMock()
    mock_worker = AsyncMock()
    mock_worker.process_next_job.return_value = True
    mock_prewarm.create_job.return_value = uuid4()
    session_repo = InMemorySessionRepo()
    agent_engine = AsyncMock()
    agent_engine.disambiguate_area.return_value = (False, "箱根", [])

    handler = TelegramBotHandler(
        bot=mock_bot,
        session_repo=session_repo,  # type: ignore[arg-type]
        prewarm_repo=mock_prewarm,
        agent_engine=agent_engine,
        prewarm_worker=mock_worker,
    )

    with patch("src.config.settings.TELEGRAM_ALLOWED_USER_ID", 0):
        await handler.handle_update(_create_update("/prewarm 箱根", update_id=601))
        await asyncio.sleep(0)  # Yield loop to allow background task to execute

    # Job created directly for 箱根
    mock_prewarm.create_job.assert_called_once_with(area_name="箱根", target_count=20, chat_id=12345)
    mock_worker.process_next_job.assert_called_once()
    wm = await session_repo.get_working_memory(12345)
    assert "pending_prewarm_options" not in wm
    mock_bot.send_message.assert_called_once()
    assert "已成功將「箱根」排入 Neon 預熱佇列！" in mock_bot.send_message.call_args[1]["text"]


@pytest.mark.asyncio
async def test_session_repository_update_working_memory_pending_prewarm():
    """Test SessionRepository.update_working_memory properly persists and clears pending_prewarm_options."""
    repo = SessionRepository()
    captured_payloads: list[dict[str, object]] = []

    class _MockCursor:
        async def __aenter__(self) -> _MockCursor:
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

        async def execute(self, sql: str, params: tuple[object, ...]) -> None:
            import json

            chat_id, data_str = params
            captured_payloads.append(json.loads(str(data_str)))

    class _MockConnection:
        async def __aenter__(self) -> _MockConnection:
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

        def cursor(self) -> _MockCursor:
            return _MockCursor()

        async def commit(self) -> None:
            pass

    class _MockPool:
        def connection(self) -> _MockConnection:
            return _MockConnection()

    mock_pool = _MockPool()
    with (
        patch("src.db.repositories.session.get_db_pool", AsyncMock(return_value=mock_pool)),
        patch.object(repo, "get_session", AsyncMock(return_value={})),
    ):
        # 1. Update with pending_prewarm_options
        options = {"pref": "北海道", "options": [{"name": "札幌", "search_loc": "札幌車站"}]}
        wm = await repo.update_working_memory(chat_id=888, pending_prewarm_options=options)
        assert wm.get("pending_prewarm_options") == options
        assert captured_payloads[-1]["working_memory"]["pending_prewarm_options"] == options

        # 2. Clear with clear_pending_prewarm=True
        with patch.object(repo, "get_session", AsyncMock(return_value={"last_recommended_ids": captured_payloads[-1]})):
            wm_cleared = await repo.update_working_memory(chat_id=888, clear_pending_prewarm=True)
            assert "pending_prewarm_options" not in wm_cleared


@pytest.mark.asyncio
async def test_prewarm_wide_area_shizuoka_onsen():
    """Test 7: /prewarm 靜岡溫泉街 dynamically disambiguates into 熱海/修善寺 options."""
    mock_bot = AsyncMock()
    mock_prewarm = AsyncMock()
    session_repo = InMemorySessionRepo()
    agent_engine = AsyncMock()
    agent_engine.disambiguate_area.return_value = (
        True,
        "靜岡溫泉街",
        [
            {"name": "熱海溫泉街", "desc": "海景布丁", "search_loc": "熱海溫泉"},
            {"name": "修善寺溫泉街", "desc": "古湯蕎麥麵", "search_loc": "修善寺"},
        ],
    )
    handler = TelegramBotHandler(
        bot=mock_bot,
        session_repo=session_repo,  # type: ignore[arg-type]
        prewarm_repo=mock_prewarm,
        agent_engine=agent_engine,
    )
    with patch("src.config.settings.TELEGRAM_ALLOWED_USER_ID", 0):
        await handler.handle_update(_create_update("/prewarm 靜岡溫泉街", update_id=701))

    mock_prewarm.create_job.assert_not_called()
    wm = await session_repo.get_working_memory(12345)
    assert "pending_prewarm_options" in wm
    pending = wm["pending_prewarm_options"]
    assert pending["pref"] == "靜岡溫泉街"
    assert len(pending["options"]) == 2
    mock_bot.send_message.assert_called_once()
    sent_text = mock_bot.send_message.call_args[1]["text"]
    assert "熱海溫泉街" in sent_text
    assert "修善寺溫泉街" in sent_text


@pytest.mark.asyncio
async def test_prewarm_wide_area_fail_open_on_timeout_or_error():
    """Test 8: If disambiguate_area times out or raises exception, fail open to direct creation."""
    mock_bot = AsyncMock()
    mock_prewarm = AsyncMock()
    mock_worker = AsyncMock()
    mock_worker.process_next_job.return_value = True
    mock_prewarm.create_job.return_value = uuid4()
    session_repo = InMemorySessionRepo()
    agent_engine = AsyncMock()
    agent_engine.disambiguate_area.side_effect = TimeoutError("LLM call timed out")

    handler = TelegramBotHandler(
        bot=mock_bot,
        session_repo=session_repo,  # type: ignore[arg-type]
        prewarm_repo=mock_prewarm,
        agent_engine=agent_engine,
        prewarm_worker=mock_worker,
    )
    with patch("src.config.settings.TELEGRAM_ALLOWED_USER_ID", 0):
        await handler.handle_update(_create_update("/prewarm 熊本", update_id=801))
        await asyncio.sleep(0)

    mock_prewarm.create_job.assert_called_once_with(area_name="熊本", target_count=20, chat_id=12345)
    mock_worker.process_next_job.assert_called_once()
    wm = await session_repo.get_working_memory(12345)
    assert "pending_prewarm_options" not in wm


@pytest.mark.asyncio
async def test_agent_engine_disambiguate_area_ambiguous():
    """Test AgentEngine.disambiguate_area correctly parses structured LLM output for ambiguous concepts."""
    llm_client = AsyncMock()
    llm_client.call_structured_json.return_value = (
        {
            "is_location_ambiguous": True,
            "ambiguity_reason": "靜岡縣內溫泉眾多且分散",
            "suggested_sub_areas": [
                {"name": "熱海溫泉街", "desc": "海景布丁", "search_loc": "熱海溫泉"},
                {"name": "修善寺溫泉街", "desc": "古湯蕎麥麵", "search_loc": "修善寺"},
            ],
        },
        "test-model",
        50,
    )
    engine = AgentEngine(llm_client=llm_client)
    is_ambig, area_name, options = await engine.disambiguate_area("靜岡溫泉街")

    assert is_ambig is True
    assert area_name == "靜岡溫泉街"
    assert len(options) == 2
    assert options[0]["name"] == "熱海溫泉街"
    assert options[0]["search_loc"] == "熱海溫泉"
    assert options[1]["name"] == "修善寺溫泉街"


@pytest.mark.asyncio
async def test_agent_engine_disambiguate_area_unambiguous():
    """Test AgentEngine.disambiguate_area returns unambiguous for specific spot."""
    llm_client = AsyncMock()
    llm_client.call_structured_json.return_value = (
        {
            "is_location_ambiguous": False,
            "ambiguity_reason": None,
            "suggested_sub_areas": [],
        },
        "test-model",
        20,
    )
    engine = AgentEngine(llm_client=llm_client)
    is_ambig, area_name, options = await engine.disambiguate_area("箱根湯本")

    assert is_ambig is False
    assert area_name == "箱根湯本"
    assert options == []


@pytest.mark.asyncio
async def test_agent_engine_disambiguate_area_fail_open_on_exception():
    """Test AgentEngine.disambiguate_area safely catches exceptions and returns fail-open default."""
    llm_client = AsyncMock()
    llm_client.call_structured_json.side_effect = RuntimeError("Service unavailable")

    engine = AgentEngine(llm_client=llm_client)
    is_ambig, area_name, options = await engine.disambiguate_area("九州")

    assert is_ambig is False
    assert area_name == "九州"
    assert options == []
