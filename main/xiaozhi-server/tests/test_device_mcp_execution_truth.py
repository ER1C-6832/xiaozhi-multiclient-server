import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.providers.tools.device_mcp.mcp_client import MCPClient
from core.providers.tools.device_mcp.mcp_executor import (
    DeviceMCPExecutor,
    _normalize_exact_title,
)


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
        self.conn._pending_tool_workflow = SimpleNamespace(
            operation="replace_content"
        )
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
        self.assertFalse(response.workflow_terminal)

    def test_spoken_numeric_title_is_normalized(self):
        self.assertEqual(_normalize_exact_title("标题3"), "3")
        self.assertEqual(_normalize_exact_title("标题3的"), "3")
        self.assertEqual(_normalize_exact_title("标题为验收便签写入"), "验收便签写入")

    async def test_search_result_is_safe_terminal_text_not_raw_json(self):
        response = await self.execute_with(
            "notes_search",
            {"query": "验收"},
            {
                "status": "success",
                "message": "搜索完成",
                "affected_note_ids": [8],
                "result": {
                    "notes": [
                        {
                            "note_id": 8,
                            "title": "验收便签写入",
                            "snippet": "嘻嘻哈哈",
                        }
                    ]
                },
            },
        )
        self.assertEqual(response.action.name, "RESPONSE")
        self.assertTrue(response.workflow_terminal)
        self.assertIn("验收便签写入", response.response)
        self.assertNotIn("note_id", response.response)

    async def test_multiple_search_results_list_every_title_without_long_tts_body(self):
        response = await self.execute_with(
            "notes_search",
            {"query": "客户"},
            {
                "status": "success",
                "message": "搜索完成",
                "affected_note_ids": [1, 2, 3],
                "result": {
                    "notes": [
                        {
                            "note_id": 1,
                            "title": "客户样品寄送",
                            "snippet": "很长的第一条正文",
                        },
                        {
                            "note_id": 2,
                            "title": "王总屏幕报价",
                            "snippet": "很长的第二条正文",
                        },
                        {
                            "note_id": 3,
                            "title": "联系王总",
                            "snippet": "很长的第三条正文",
                        },
                    ]
                },
            },
        )
        self.assertEqual(
            response.response,
            "找到3条便签：客户样品寄送、王总屏幕报价、联系王总。",
        )
        self.assertNotIn("正文", response.response)
        self.assertNotIn("；", response.response)


if __name__ == "__main__":
    unittest.main()
