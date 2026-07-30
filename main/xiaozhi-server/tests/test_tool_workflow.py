import importlib.util
import sys
import unittest
from pathlib import Path


ROUTING_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "providers"
    / "tools"
    / "tool_routing.py"
)
WORKFLOW_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "providers"
    / "tools"
    / "tool_workflow.py"
)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ROUTING = load(
    "core.providers.tools.tool_routing",
    ROUTING_PATH,
)
WORKFLOW = load("tool_workflow_tested", WORKFLOW_PATH)


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
    tool("notes_replace_content"),
    tool("notes_delete"),
    tool("notes_list_deleted"),
]


class ToolWorkflowTest(unittest.TestCase):
    def start(self, query):
        decision = ROUTING.select_candidate_tools(query, TOOLS)
        workflow = WORKFLOW.start_workflow(query, decision)
        self.assertIsNotNone(workflow)
        return workflow

    def test_delete_continuation_never_exposes_deleted_list(self):
        workflow = self.start("帮我删掉一条便签")
        route = WORKFLOW.continuation_route(workflow, TOOLS)
        names = [item["function"]["name"] for item in route.candidates]
        self.assertEqual(workflow.operation, "delete")
        self.assertEqual(names, ["notes_resolve", "notes_delete"])
        self.assertNotIn("notes_list_deleted", names)

    def test_create_survives_multiple_parameter_turns(self):
        workflow = self.start("加一条便签")
        first = WORKFLOW.continuation_route(workflow.touch(), TOOLS)
        second = WORKFLOW.continuation_route(workflow.touch().touch(), TOOLS)
        self.assertEqual(
            [item["function"]["name"] for item in first.candidates],
            ["notes_create"],
        )
        self.assertEqual(
            [item["function"]["name"] for item in second.candidates],
            ["notes_create"],
        )

    def test_search_keyword_answer_keeps_search_tool(self):
        workflow = self.start("查一条便签")
        route = WORKFLOW.continuation_route(workflow.touch(), TOOLS)
        self.assertEqual(
            [item["function"]["name"] for item in route.candidates],
            ["notes_search", "notes_resolve"],
        )

    def test_selectorless_search_is_clarified_without_tool_call(self):
        self.assertIn(
            "完整标题或关键词",
            WORKFLOW.selector_clarification("search", "查一条便签"),
        )

    def test_target_rejection_discards_previous_choice(self):
        self.assertTrue(WORKFLOW.is_target_rejection("不是这条"))
        self.assertIn(
            "上一条作废",
            WORKFLOW.selector_clarification("search", "不是这条"),
        )

    def test_update_completion_requires_real_mutation_tool(self):
        workflow = self.start("修改便签内容")
        self.assertEqual(
            WORKFLOW.completion_tools(workflow.operation),
            ("notes_replace_content",),
        )
        self.assertNotIn(
            "notes_resolve",
            WORKFLOW.completion_tools(workflow.operation),
        )

    def test_resolver_does_not_authorize_false_update_success(self):
        text, blocked = WORKFLOW.guard_unverified_text(
            "replace_content",
            [{"name": "notes_resolve", "success": True}],
            "已将便签内容修改为114",
        )
        self.assertTrue(blocked)
        self.assertIn("尚未实际完成", text)

    def test_mutation_clarification_is_not_blocked_as_false_success(self):
        original = "找到目标了，请告诉我想把正文改成什么？"
        text, blocked = WORKFLOW.guard_unverified_text(
            "replace_content",
            [{"name": "notes_resolve", "success": True}],
            original,
        )
        self.assertFalse(blocked)
        self.assertEqual(text, original)

    def test_failed_mutation_does_not_authorize_success(self):
        text, blocked = WORKFLOW.guard_unverified_text(
            "delete",
            [{"name": "notes_delete", "success": False}],
            "已成功删除标题为3的便签",
        )
        self.assertTrue(blocked)
        self.assertIn("尚未实际完成", text)

    def test_successful_mutation_authorizes_truthful_completion(self):
        original = "已将标题为3的便签内容修改为114"
        text, blocked = WORKFLOW.guard_unverified_text(
            "replace_content",
            [{"name": "notes_replace_content", "success": True}],
            original,
        )
        self.assertFalse(blocked)
        self.assertEqual(text, original)

    def test_search_progress_without_search_call_is_not_terminal(self):
        text, blocked = WORKFLOW.guard_unverified_text(
            "search",
            [],
            "正在查找手柄相关的便签",
        )
        self.assertTrue(blocked)
        self.assertIn("尚未实际执行", text)

    def test_explicit_new_operation_replaces_pending(self):
        workflow = self.start("加一条便签")
        self.assertFalse(
            WORKFLOW.should_replace_pending(workflow, "正文写项目报价")
        )
        self.assertTrue(
            WORKFLOW.should_replace_pending(workflow, "查找手柄便签")
        )


if __name__ == "__main__":
    unittest.main()
