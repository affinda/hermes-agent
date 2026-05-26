"""Slack MEDIA delivery tests for tools/send_message_tool.py."""

import asyncio
import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from gateway.config import Platform
from tools.send_message_tool import _send_slack, _send_to_platform, send_message_tool


def _run_async_immediately(coro):
    return asyncio.run(coro)


def test_slack_send_message_media_paths_are_extracted_and_forwarded(tmp_path):
    media_path = tmp_path / "native.png"
    media_path.write_bytes(b"fake-png")
    slack_cfg = SimpleNamespace(enabled=True, token="xoxb-test", extra={})
    config = SimpleNamespace(
        platforms={Platform.SLACK: slack_cfg},
        get_home_channel=lambda _platform: None,
    )

    with patch.dict(os.environ, {"HERMES_MEDIA_ALLOW_DIRS": str(tmp_path)}, clear=False), \
         patch("gateway.config.load_gateway_config", return_value=config), \
         patch("tools.interrupt.is_interrupted", return_value=False), \
         patch("model_tools._run_async", side_effect=_run_async_immediately), \
         patch("tools.send_message_tool._send_to_platform", new=AsyncMock(return_value={"success": True})) as send_mock, \
         patch("gateway.mirror.mirror_to_session", return_value=True):
        result = json.loads(
            send_message_tool(
                {
                    "action": "send",
                    "target": "slack:C123ABCDEF",
                    "message": f"caption\nMEDIA:{media_path}",
                }
            )
        )

    assert result["success"] is True
    send_mock.assert_awaited_once_with(
        Platform.SLACK,
        slack_cfg,
        "C123ABCDEF",
        "caption",
        thread_id=None,
        media_files=[(str(media_path), False)],
        force_document=False,
    )


def test_send_to_platform_routes_slack_media_to_file_upload(tmp_path):
    media_path = tmp_path / "native.png"
    media_path.write_bytes(b"fake-png")
    slack_cfg = SimpleNamespace(enabled=True, token="xoxb-test", extra={})
    send_mock = AsyncMock(return_value={"success": True, "files_uploaded": 1})

    with patch("tools.send_message_tool._send_slack", new=send_mock):
        result = asyncio.run(
            _send_to_platform(
                Platform.SLACK,
                slack_cfg,
                "C123ABCDEF",
                "caption",
                media_files=[(str(media_path), False)],
                thread_id="171.000001",
            )
        )

    assert result["success"] is True
    send_mock.assert_awaited_once_with(
        "xoxb-test",
        "C123ABCDEF",
        "caption",
        thread_id="171.000001",
        media_files=[(str(media_path), False)],
        force_document=False,
    )


def test_send_slack_uploads_media_with_initial_comment_and_thread(tmp_path, monkeypatch):
    media_path = tmp_path / "native.png"
    media_path.write_bytes(b"fake-png")

    class FakeClient:
        instances = []

        def __init__(self, token):
            self.token = token
            self.files_upload_v2 = AsyncMock(return_value={"ts": "171.000002"})
            FakeClient.instances.append(self)

    async_client_mod = SimpleNamespace(AsyncWebClient=FakeClient)
    monkeypatch.setitem(sys.modules, "slack_sdk.web.async_client", async_client_mod)

    result = asyncio.run(
        _send_slack(
            "xoxb-test",
            "C123ABCDEF",
            "caption",
            thread_id="171.000001",
            media_files=[(str(media_path), False)],
        )
    )

    assert result == {
        "success": True,
        "platform": "slack",
        "chat_id": "C123ABCDEF",
        "message_id": "171.000002",
        "files_uploaded": 1,
    }
    assert FakeClient.instances[0].token == "xoxb-test"
    FakeClient.instances[0].files_upload_v2.assert_awaited_once_with(
        channel="C123ABCDEF",
        file_uploads=[{"file": str(media_path), "filename": "native.png"}],
        initial_comment="caption",
        thread_ts="171.000001",
    )
