import time
import unittest

from core.providers.tools.tool_routing import ToolRouteDecision, select_candidate_tools
from core.providers.tools.tool_workflow import (
    PendingToolWorkflow,
    completion_tools,
    continuation_route,
    extract_exact_title,
    guard_unverified_text,
    infer_operation,
    requires_tool_followup,
    selector_clarification,
    should_replace_pending,
    start_workflow,
    workflow_completed,
)


def tool(name):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": name,
            "parameters": {"type": "object", "properties": {}},
        },
    }


TOOLS = [
    tool("notes_create"),
    tool("notes_search"),
    tool("notes_resolve"),
    tool("notes_list_recent"),
    tool("notes_list_deleted"),
    tool("notes_list_pinned"),
    tool("notes_list_todos"),
    tool("notes_list_by_tag"),
    tool("notes_get"),
    tool("notes_update_title"),
    tool("notes_replace_content"),
    tool("notes_append"),
    tool("notes_convert_type"),
    tool("notes_pin"),
    tool("notes_delete"),
    tool("notes_restore"),
    tool("tags_create"),
    tool("tags_search"),
    tool("tags_list"),
    tool("tags_delete"),
    tool("tags_bind"),
    tool("ui_open_note"),
    tool("ui_show_tag"),
    tool("ui_show_trash"),
    tool("ui_show_pinned"),
    tool("ui_show_todos"),
    tool("ui_show_note_list"),
    tool("assistant_list_pending_confirmations"),
    tool("assistant_confirm"),
    tool("assistant_reject"),
]


class ToolWorkflowV2Test(unittest.TestCase):
    def test_crud_and_short_spoken_operations_are_recognized(self):
        cases = {
            "加一条便签": "create",
            "查一条便签": "search",
            "改一条便签": "edit",
            "删一条便签": "delete",
            "恢复一条便签": "restore",
            "读取一条便签": "get",
            "把正文全部改成114514": "replace_content",
            "把标题改成验收记录": "update_title",
            "在那条后面补一句": "append",
            "确认": "confirm",
            "取消": "reject",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(infer_operation(text), expected)

    def test_tag_and_page_operations_are_recognized(self):
        cases = {
            "打开客户标签页": "show_tag",
            "看看客户标签下的便签": "list_by_tag",
            "给那条便签加客户标签": "tag_bind",
            "新建一个项目标签": "tag_create",
            "删掉测试标签": "tag_delete",
            "有哪些标签": "tag_list",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(infer_operation(text), expected)


    def test_voice_confirmation_is_a_durable_tool_workflow(self):
        for text, operation, completion in (
            ("确认", "confirm", "assistant_confirm"),
            ("取消", "reject", "assistant_reject"),
        ):
            with self.subTest(text=text):
                decision = select_candidate_tools(text, TOOLS)
                workflow = start_workflow(text, decision)
                self.assertIsNotNone(workflow)
                self.assertEqual(workflow.operation, operation)
                self.assertIn(completion, completion_tools(operation))
                route = continuation_route(workflow, TOOLS)
                self.assertEqual(route.route, "tool")

    def test_generic_search_starts_durable_workflow_without_calling_tool(self):
        decision = select_candidate_tools("查一条便签", TOOLS)
        self.assertEqual(decision.route, "chat")
        self.assertEqual(decision.reason, "generic_note_search_requires_clarification")
        workflow = start_workflow("查一条便签", decision)
        self.assertIsNotNone(workflow)
        self.assertEqual(workflow.operation, "search")
        route = continuation_route(workflow, TOOLS)
        self.assertEqual(route.route, "tool")
        self.assertEqual(
            [item["function"]["name"] for item in route.candidates],
            ["notes_search", "notes_resolve"],
        )
        self.assertIn("完整标题或关键词", selector_clarification("search", "查一条便签"))


    def test_semantic_search_keeps_deterministic_single_tool_route(self):
        decision = select_candidate_tools("查王总报价的便签", TOOLS)
        self.assertEqual(decision.reason, "note_keyword_search")
        self.assertIsNone(start_workflow("查王总报价的便签", decision))

    def test_short_delete_and_edit_requests_create_workflows(self):
        for text, operation in (("删一条便签", "delete"), ("改一条便签", "edit")):
            decision = select_candidate_tools(text, TOOLS)
            workflow = start_workflow(text, decision)
            self.assertIsNotNone(workflow)
            self.assertEqual(workflow.operation, operation)
            self.assertTrue(requires_tool_followup(workflow.operation))

    def test_edit_workflow_refines_without_losing_accumulated_context(self):
        now = time.monotonic()
        workflow = PendingToolWorkflow(
            operation="edit",
            root_query="改一条便签",
            candidate_names=("notes_resolve", "notes_update_title", "notes_replace_content"),
            created_at=now,
            updated_at=now,
            fragments=("改一条便签",),
        )
        self.assertFalse(should_replace_pending(workflow, "正文改成114514"))
        refined = workflow.touch("正文改成114514")
        self.assertEqual(refined.operation, "replace_content")
        self.assertIn("正文改成114514", refined.context_summary())
        self.assertEqual(completion_tools(refined.operation), ("notes_replace_content",))


    def test_operation_words_inside_target_do_not_replace_pending_workflow(self):
        decision = select_candidate_tools("删一条便签", TOOLS)
        workflow = start_workflow("删一条便签", decision)
        self.assertFalse(should_replace_pending(workflow, "标题是新增客户报价"))
        self.assertFalse(should_replace_pending(workflow, "内容叫删除旧数据的"))
        self.assertTrue(should_replace_pending(workflow, "改为创建一条新便签"))

    def test_target_only_followup_does_not_replace_delete_workflow(self):
        decision = select_candidate_tools("删一条便签", TOOLS)
        workflow = start_workflow("删一条便签", decision)
        self.assertFalse(should_replace_pending(workflow, "内容叫嘻嘻哈哈的"))
        touched = workflow.touch("内容叫嘻嘻哈哈的")
        self.assertEqual(touched.operation, "delete")
        self.assertIn("内容叫嘻嘻哈哈的", touched.context_summary())

    def test_numeric_title_is_exact_title_not_database_id(self):
        self.assertEqual(extract_exact_title("标题是3的"), "3")
        self.assertEqual(extract_exact_title("标题叫“114514”"), "114514")
        self.assertEqual(extract_exact_title("3"), "3")
        self.assertIsNone(extract_exact_title("王总相关"))

    def test_failed_resolver_does_not_complete_mutation(self):
        self.assertFalse(
            workflow_completed(
                "delete",
                [{"name": "notes_resolve", "success": False, "terminal": False}],
            )
        )
        self.assertFalse(
            workflow_completed(
                "delete",
                [{"name": "notes_delete", "success": False, "terminal": False}],
            )
        )
        self.assertTrue(
            workflow_completed(
                "delete",
                [{"name": "notes_delete", "success": True, "terminal": True}],
            )
        )

    def test_unverified_success_claim_is_blocked_but_clarification_is_allowed(self):
        text, blocked = guard_unverified_text("delete", [], "已为你删除这条便签")
        self.assertTrue(blocked)
        self.assertIn("尚未实际完成", text)

        original = "请告诉我要删除的便签标题或内容关键词。"
        text, blocked = guard_unverified_text("delete", [], original)
        self.assertFalse(blocked)
        self.assertEqual(text, original)

        original = "删除便签需要确认，请在客户端确认卡片中确认。"
        text, blocked = guard_unverified_text("delete", [], original)
        self.assertFalse(blocked)
        self.assertEqual(text, original)


if __name__ == "__main__":
    unittest.main()
