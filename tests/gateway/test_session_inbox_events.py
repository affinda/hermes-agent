from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import Platform
from gateway.platforms.base import SendResult
from gateway.run import GatewayRunner, SessionInboxTargetBusy
from gateway.session import SessionSource, build_session_key


@pytest.mark.asyncio
async def test_process_session_inbox_event_dispatches_internal_turn_to_source_json():
    runner = object.__new__(GatewayRunner)
    source = SessionSource(
        platform=Platform.SLACK,
        chat_id="D123",
        chat_name="Andrew DM",
        chat_type="dm",
        user_id="system:devagent",
        user_name="DevAgent",
        thread_id="1779581733.398979",
    )
    session_key = build_session_key(source)
    entry = SimpleNamespace(session_id="sess-123", session_key=session_key)

    adapter = SimpleNamespace(
        send=AsyncMock(return_value=SendResult(success=True, message_id="sent-1"))
    )
    runner.adapters = {Platform.SLACK: adapter}
    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = entry
    runner.session_store.switch_session.return_value = entry
    runner.session_store.get_entry.return_value = None
    runner.session_store.find_entry_by_session_id.return_value = None
    runner._handle_message = AsyncMock(return_value="reviewed result")
    runner._session_db = MagicMock()
    runner._evict_cached_agent = MagicMock()

    row = {
        "id": "evt_1",
        "source": "devagent",
        "kind": "external_agent.completed",
        "target_session_id": "sess-123",
        "source_json": source.to_dict(),
        "text": "[DevAgent completed: tests passed]",
        "metadata": {"devagent_session_id": "child-1"},
    }

    await runner._process_session_inbox_event(row)

    runner._handle_message.assert_awaited_once()
    synthetic = runner._handle_message.await_args.args[0]
    assert synthetic.internal is True
    assert synthetic.text == "[DevAgent completed: tests passed]"
    assert synthetic.raw_message["session_inbox_event_id"] == "evt_1"
    assert synthetic.raw_message["metadata"]["devagent_session_id"] == "child-1"
    runner.session_store.switch_session.assert_not_called()
    runner._session_db.mark_session_inbox_event_response_ready.assert_called_once_with(
        "evt_1", "reviewed result"
    )
    adapter.send.assert_awaited_once()
    assert adapter.send.await_args.kwargs["chat_id"] == "D123"
    assert adapter.send.await_args.kwargs["content"] == "reviewed result"
    assert adapter.send.await_args.kwargs["metadata"]["thread_id"] == "1779581733.398979"


@pytest.mark.asyncio
async def test_process_session_inbox_event_switches_session_when_source_key_points_elsewhere():
    runner = object.__new__(GatewayRunner)
    source = SessionSource(
        platform=Platform.SLACK,
        chat_id="D123",
        chat_type="dm",
        user_id="system:devagent",
    )
    session_key = build_session_key(source)
    current_entry = SimpleNamespace(session_id="current", session_key=session_key)
    target_entry = SimpleNamespace(session_id="target", session_key=session_key)

    adapter = SimpleNamespace(
        send=AsyncMock(return_value=SendResult(success=True, message_id="sent-1"))
    )
    runner.adapters = {Platform.SLACK: adapter}
    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = current_entry
    runner.session_store.switch_session.return_value = target_entry
    runner.session_store.get_entry.return_value = None
    runner.session_store.find_entry_by_session_id.return_value = None
    runner._handle_message = AsyncMock(return_value=None)
    runner._session_db = MagicMock()
    runner._evict_cached_agent = MagicMock()

    await runner._process_session_inbox_event(
        {
            "id": "evt_2",
            "source": "devagent",
            "kind": "external_agent.completed",
            "target_session_id": "target",
            "source_json": source.to_dict(),
            "text": "wake up",
            "metadata": {},
        }
    )

    runner.session_store.switch_session.assert_called_once_with(session_key, "target")
    runner._evict_cached_agent.assert_called_once_with(session_key)
    runner._handle_message.assert_awaited_once()
    adapter.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_session_inbox_event_defers_when_target_session_busy():
    runner = object.__new__(GatewayRunner)
    source = SessionSource(
        platform=Platform.SLACK,
        chat_id="D123",
        chat_type="dm",
        user_id="system:devagent",
    )
    session_key = build_session_key(source)
    entry = SimpleNamespace(session_id="sess-123", session_key=session_key)
    adapter = SimpleNamespace(
        send=AsyncMock(return_value=SendResult(success=True, message_id="sent-1"))
    )
    runner.adapters = {Platform.SLACK: adapter}
    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = entry
    runner.session_store.get_entry.return_value = None
    runner.session_store.find_entry_by_session_id.return_value = None
    runner._running_agents = {session_key: object()}
    runner._handle_message = AsyncMock(return_value="should not run while busy")
    runner._session_db = MagicMock()
    runner._evict_cached_agent = MagicMock()

    with pytest.raises(SessionInboxTargetBusy, match="target session is busy"):
        await runner._process_session_inbox_event(
            {
                "id": "evt_busy",
                "source": "devagent",
                "kind": "external_agent.completed",
                "target_session_id": "sess-123",
                "source_json": source.to_dict(),
                "text": "defer me",
                "metadata": {},
            }
        )

    runner._handle_message.assert_not_awaited()
    adapter.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_session_inbox_event_retries_saved_response_without_rerunning_model():
    runner = object.__new__(GatewayRunner)
    source = SessionSource(
        platform=Platform.SLACK,
        chat_id="D123",
        chat_type="dm",
        user_id="system:devagent",
    )
    session_key = build_session_key(source)
    entry = SimpleNamespace(session_id="sess-123", session_key=session_key)
    adapter = SimpleNamespace(
        send=AsyncMock(return_value=SendResult(success=True, message_id="sent-1"))
    )
    runner.adapters = {Platform.SLACK: adapter}
    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = entry
    runner.session_store.get_entry.return_value = None
    runner.session_store.find_entry_by_session_id.return_value = None
    runner._handle_message = AsyncMock(return_value="should not be used")
    runner._session_db = MagicMock()
    runner._evict_cached_agent = MagicMock()

    await runner._process_session_inbox_event(
        {
            "id": "evt_response_ready",
            "source": "devagent",
            "kind": "external_agent.completed",
            "target_session_id": "sess-123",
            "source_json": source.to_dict(),
            "text": "original wakeup",
            "metadata": {},
            "response_text": "cached response",
        }
    )

    runner._handle_message.assert_not_awaited()
    runner._session_db.mark_session_inbox_event_response_ready.assert_not_called()
    adapter.send.assert_awaited_once()
    assert adapter.send.await_args.kwargs["content"] == "cached response"


def test_resolve_session_inbox_source_can_use_active_session_id_mapping():
    runner = object.__new__(GatewayRunner)
    source = SessionSource(
        platform=Platform.SLACK,
        chat_id="D123",
        chat_type="dm",
        user_id="U123",
    )
    session_key = build_session_key(source)
    entry = SimpleNamespace(session_id="sess-123", session_key=session_key, origin=source)
    runner.session_store = MagicMock()
    runner.session_store.find_entry_by_session_id.return_value = entry

    resolved_source, resolved_key = runner._resolve_session_inbox_source(
        {"target_session_id": "sess-123", "source_json": None}
    )

    assert resolved_source == source
    assert resolved_key == session_key
