import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.providers.tools.device_mcp.mcp_client import MCPClient
from core.providers.tools.device_mcp.mcp_executor import DeviceMCPExecutor


def connection():
    return SimpleNamespace(
        features={"mcp": True},
        mcp_client=MCPClient(),
        websocket=SimpleNamespace(),
        config={"server": {"auth_key": "test"}},
        headers={"device-id": "test-device"},
    )


class DeviceMcpExecutionTruthTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.conn = connection()
        await self.conn.mcp_client.set_ready(True)
        self.executor = DeviceMCPExecutor(self.conn)

    async def execute_with(self, tool_name, arguments, payload):
        with patch(
            "core.providers.tools.device_mcp.mcp_executor.call_mcp_tool",
            new=AsyncMock(return_value=json.dumps(payload, ensure_ascii=False)),
        ):
            return await self.executor.execute(
                self.conn, tool_name, arguments
            )

    async def test_structured_failure_is_not_execution_success(self):
        response = await self.execute_with(
            "notes_search",
            {"query": "不存在"},
            {"status": "failed", "message": "未找到符合条件的便签"},
        )
        self.assertFalse(response.execution_succeeded)
        self.assertTrue(response.workflow_terminal)

    async def test_confirmation_is_not_completed_mutation(self):
        self.conn._mcp_target_provenance = {7: "resolved"}
        response = await self.execute_with(
            "notes_delete",
            {"note_ids": [7]},
            {
                "status": "requires_confirmation",
                "message": "需要确认",
                "requires_confirmation": True,
                "confirmation_id": "confirm-7",
            },
        )
        self.assertFalse(response.execution_succeeded)
        self.assertTrue(response.workflow_terminal)

    async def test_successful_mutation_is_explicit_execution_success(self):
        response = await self.execute_with(
            "notes_create",
            {"title": "测试", "content": "正文"},
            {
                "status": "success",
                "message": "便签已创建",
                "result": {"note_id": 8},
            },
        )
        self.assertTrue(response.execution_succeeded)
        self.assertTrue(response.workflow_terminal)

    async def test_unique_resolver_success_is_explicit(self):
        response = await self.execute_with(
            "notes_resolve",
            {"exact_title": "3"},
            {
                "status": "success",
                "message": "已唯一定位",
                "affected_note_ids": [7],
                "result": {
                    "resolution_status": "resolved",
                    "note_id": 7,
                    "title": "3",
                },
            },
        )
        self.assertTrue(response.execution_succeeded)
        self.assertIsNone(response.workflow_terminal)


if __name__ == "__main__":
    unittest.main()
