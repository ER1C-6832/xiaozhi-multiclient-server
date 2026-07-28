import asyncio
import json
from types import SimpleNamespace

from core.providers.tools.device_mcp.mcp_client import MCPClient
from core.providers.tools.device_mcp import mcp_handler
from core.providers.tools.device_mcp.mcp_executor import DeviceMCPExecutor
from plugins_func.register import Action


class FakeWebSocket:
    def __init__(self):
        self.messages = []

    async def send(self, message):
        self.messages.append(json.loads(message)["payload"])


def make_connection():
    return SimpleNamespace(
        features={"mcp": True},
        mcp_client=MCPClient(),
        websocket=FakeWebSocket(),
        config={"server": {"auth_key": "test"}},
        headers={"device-id": "test-device"},
    )


def test_control_and_tool_request_ids_never_overlap(monkeypatch):
    async def scenario():
        conn = make_connection()
        monkeypatch.setattr(mcp_handler, "get_vision_url", lambda _config: "http://vision")
        monkeypatch.setattr(
            mcp_handler.AuthToken, "generate_token", lambda _self, _device_id: "token"
        )

        await mcp_handler.send_mcp_initialize_message(conn)
        await mcp_handler.send_mcp_tools_list_request(conn)
        await mcp_handler.send_mcp_tools_list_continue_request(conn, "next-page")
        first_tool_id = await conn.mcp_client.get_next_id()

        ids = [message["id"] for message in conn.websocket.messages]
        assert ids == [1, 2, 3]
        assert first_tool_id == 4
        assert len({*ids, first_tool_id}) == 4

    asyncio.run(scenario())


def test_control_response_is_matched_by_allocated_method():
    async def scenario():
        conn = make_connection()
        request_id = await conn.mcp_client.allocate_control_request_id("tools/list")
        assert request_id == 1

        await mcp_handler.handle_mcp_message(
            conn,
            conn.mcp_client,
            {"jsonrpc": "2.0", "id": request_id, "result": {"tools": []}},
        )

        assert await conn.mcp_client.is_ready()
        assert conn.mcp_client.control_requests == {}

    asyncio.run(scenario())


def test_device_mcp_internal_error_is_not_returned_to_user(monkeypatch):
    async def fail_call(*_args, **_kwargs):
        raise mcp_handler.DeviceMCPError(
            "Request id was reused with different method or arguments"
        )

    async def scenario():
        conn = make_connection()
        await conn.mcp_client.set_ready(True)
        monkeypatch.setattr(
            "core.providers.tools.device_mcp.mcp_executor.call_mcp_tool", fail_call
        )

        response = await DeviceMCPExecutor(conn).execute(
            conn, "notes_create", {"title": "test"}
        )
        assert response.action == Action.ERROR
        assert response.response == "设备工具操作暂时没有完成，请稍后重试。"
        assert "MCP" not in response.response
        assert "Request id" not in response.response

    asyncio.run(scenario())
