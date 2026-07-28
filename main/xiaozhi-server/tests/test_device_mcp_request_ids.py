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


def test_model_arguments_are_normalized_for_safe_note_lookup():
    assert mcp_handler.normalize_device_tool_arguments(
        "notes.search", {"query": "手柄", "limit": 1}
    ) == {"query": "手柄", "limit": 5}
    assert mcp_handler.normalize_device_tool_arguments(
        "notes.resolve", {"query": "标题是3"}
    ) == {"exact_title": "3"}
    assert mcp_handler.normalize_device_tool_arguments(
        "notes.resolve", {"query": "标题3的那条"}
    ) == {"exact_title": "3"}
    assert mcp_handler.normalize_device_tool_arguments(
        "notes.resolve", {"query": "3", "exact_title": "3"}
    ) == {"exact_title": "3"}
    assert mcp_handler.normalize_device_tool_arguments(
        "notes.resolve", {"query": "标题为3"}
    ) == {"exact_title": "3"}


def test_direct_spoken_number_cannot_be_used_as_internal_mutation_id(monkeypatch):
    calls = []

    async def must_not_call(*args, **_kwargs):
        calls.append(args)
        raise AssertionError("untrusted mutation reached the client")

    async def scenario():
        conn = make_connection()
        await conn.mcp_client.set_ready(True)
        monkeypatch.setattr(
            "core.providers.tools.device_mcp.mcp_executor.call_mcp_tool",
            must_not_call,
        )
        response = await DeviceMCPExecutor(conn).execute(
            conn, "notes_delete", {"note_ids": [3]}
        )
        assert response.action == Action.RESPONSE
        assert "尚未按便签标题唯一定位" in response.response
        assert calls == []

    asyncio.run(scenario())


def test_only_unique_resolver_output_authorizes_followup_mutation(monkeypatch):
    calls = []

    async def resolve_then_delete(_conn, _client, tool_name, args, **_kwargs):
        calls.append((tool_name, json.loads(args)))
        if tool_name == "notes_resolve":
            return json.dumps(
                {
                    "status": "success",
                    "message": "目标已唯一定位",
                    "affected_note_ids": [7],
                    "result": {
                        "resolution_status": "resolved",
                        "note_id": 7,
                        "title": "3",
                    },
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "status": "requires_confirmation",
                "message": "删除便签需要确认",
                "requires_confirmation": True,
                "confirmation_id": "confirm-title-3",
            },
            ensure_ascii=False,
        )

    async def scenario():
        conn = make_connection()
        await conn.mcp_client.set_ready(True)
        monkeypatch.setattr(
            "core.providers.tools.device_mcp.mcp_executor.call_mcp_tool",
            resolve_then_delete,
        )
        executor = DeviceMCPExecutor(conn)
        resolved = await executor.execute(
            conn, "notes_resolve", {"exact_title": "3"}
        )
        assert resolved.action == Action.REQLLM
        assert conn._mcp_target_provenance == {7: "resolved"}

        wrong = await executor.execute(conn, "notes_delete", {"note_ids": [3]})
        assert wrong.action == Action.RESPONSE
        assert "尚未按便签标题唯一定位" in wrong.response

        pending = await executor.execute(conn, "notes_delete", {"note_ids": [7]})
        assert pending.action == Action.RESPONSE
        assert "删除便签需要确认" in pending.response
        assert conn._mcp_target_provenance == {}
        assert calls == [
            ("notes_resolve", {"exact_title": "3"}),
            ("notes_delete", {"note_ids": [7]}),
        ]

    asyncio.run(scenario())


def test_structured_business_failure_keeps_public_message(monkeypatch):
    async def failed_lookup(*_args, **_kwargs):
        return json.dumps(
            {
                "status": "failed",
                "message": "未找到符合条件的便签",
                "error_code": "note_not_found",
            },
            ensure_ascii=False,
        )

    async def scenario():
        conn = make_connection()
        await conn.mcp_client.set_ready(True)
        monkeypatch.setattr(
            "core.providers.tools.device_mcp.mcp_executor.call_mcp_tool",
            failed_lookup,
        )
        response = await DeviceMCPExecutor(conn).execute(
            conn, "notes_resolve", {"query": "不存在"}
        )
        assert response.action == Action.RESPONSE
        assert response.response == "未找到符合条件的便签"

    asyncio.run(scenario())


def test_ambiguous_resolution_lists_titles_not_internal_ids(monkeypatch):
    async def ambiguous(*_args, **_kwargs):
        return json.dumps(
            {
                "status": "success",
                "message": "存在多个候选，未自动选择",
                "result": {
                    "resolution_status": "ambiguous",
                    "candidates": [
                        {"note_id": 5, "title": "手柄包装清单"},
                        {"note_id": 6, "title": "游戏手柄测试"},
                    ],
                },
            },
            ensure_ascii=False,
        )

    async def scenario():
        conn = make_connection()
        await conn.mcp_client.set_ready(True)
        monkeypatch.setattr(
            "core.providers.tools.device_mcp.mcp_executor.call_mcp_tool",
            ambiguous,
        )
        response = await DeviceMCPExecutor(conn).execute(
            conn, "notes_resolve", {"query": "手柄"}
        )
        assert response.action == Action.RESPONSE
        assert "手柄包装清单" in response.response
        assert "游戏手柄测试" in response.response
        assert "note_id" not in response.response
        assert "编号" not in response.response

    asyncio.run(scenario())


def test_successful_mutation_uses_trusted_tool_message(monkeypatch):
    async def created(*_args, **_kwargs):
        return json.dumps(
            {"status": "success", "message": "便签已创建", "result": {"note_id": 8}},
            ensure_ascii=False,
        )

    async def scenario():
        conn = make_connection()
        await conn.mcp_client.set_ready(True)
        monkeypatch.setattr(
            "core.providers.tools.device_mcp.mcp_executor.call_mcp_tool", created
        )
        response = await DeviceMCPExecutor(conn).execute(
            conn, "notes_create", {"title": "测试"}
        )
        assert response.action == Action.RESPONSE
        assert response.response == "便签已创建"

    asyncio.run(scenario())


def test_pending_mutation_opens_confirmation_card(monkeypatch):
    calls = []

    async def pending_then_show(_conn, _client, tool_name, args, **_kwargs):
        calls.append((tool_name, json.loads(args)))
        if tool_name == "notes_delete":
            return json.dumps(
                {
                    "status": "requires_confirmation",
                    "message": "删除便签需要确认",
                    "requires_confirmation": True,
                    "confirmation_id": "confirm-safe-1",
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {"status": "success", "message": "确认窗口已显示"},
            ensure_ascii=False,
        )

    async def scenario():
        conn = make_connection()
        conn._mcp_target_provenance = {7: "resolved"}
        await conn.mcp_client.set_ready(True)
        await conn.mcp_client.add_tool(
            {
                "name": "ui.show_confirmation",
                "description": "show confirmation",
                "inputSchema": {"type": "object", "properties": {}},
            }
        )
        monkeypatch.setattr(
            "core.providers.tools.device_mcp.mcp_executor.call_mcp_tool",
            pending_then_show,
        )
        response = await DeviceMCPExecutor(conn).execute(
            conn, "notes_delete", {"note_ids": [7]}
        )
        assert response.action == Action.RESPONSE
        assert "请在客户端确认卡片中确认" in response.response
        assert calls == [
            ("notes_delete", {"note_ids": [7]}),
            (
                "ui_show_confirmation",
                {"confirmation_id": "confirm-safe-1"},
            ),
        ]

    asyncio.run(scenario())
