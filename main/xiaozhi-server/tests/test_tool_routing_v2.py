import unittest

from core.providers.tools.tool_routing import (
    required_tool_choice,
    select_candidate_tools,
)


def tool(name, description=""):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description or name,
            "parameters": {"type": "object", "properties": {}},
        },
    }


TOOLS = [
    tool("notes_resolve"),
    tool("notes_search"),
    tool("notes_list_recent"),
    tool("notes_list_deleted"),
    tool("notes_list_pinned"),
    tool("notes_list_todos"),
    tool("notes_create"),
    tool("notes_update_title"),
    tool("notes_replace_content"),
    tool("notes_delete"),
    tool("tags_search"),
    tool("tags_bind"),
    tool("ui_show_tag"),
    tool("assistant_list_pending_confirmations"),
    tool("assistant_confirm"),
    tool("assistant_reject"),
]


class ToolRoutingV2Test(unittest.TestCase):
    def test_generic_search_clarifies_but_semantic_search_is_forced(self):
        generic = select_candidate_tools("查一条便签", TOOLS)
        self.assertEqual(generic.route, "chat")
        self.assertEqual(generic.reason, "generic_note_search_requires_clarification")

        semantic = select_candidate_tools("查王总报价的便签", TOOLS)
        self.assertEqual(semantic.route, "tool")
        self.assertEqual(semantic.reason, "note_keyword_search")
        self.assertEqual(semantic.candidates[0]["function"]["name"], "notes_search")
        self.assertEqual(
            required_tool_choice(semantic),
            {"type": "function", "function": {"name": "notes_search"}},
        )

    def test_short_delete_and_edit_stay_in_note_domain(self):
        for query in ("删一条便签", "改一条便签", "编辑一条笔记"):
            with self.subTest(query=query):
                decision = select_candidate_tools(query, TOOLS)
                self.assertEqual(decision.route, "tool")
                names = [item["function"]["name"] for item in decision.candidates]
                self.assertTrue(any(name.startswith("notes_") for name in names))


    def test_unscoped_content_word_does_not_open_note_tools(self):
        decision = select_candidate_tools("这个内容怎么样", TOOLS)
        self.assertEqual(decision.route, "chat")
        self.assertEqual(decision.reason, "no_explicit_tool_domain")

    def test_confirmation_stays_tool_capable(self):
        decision = select_candidate_tools("确认", TOOLS)
        self.assertEqual(decision.route, "tool")
        names = [item["function"]["name"] for item in decision.candidates]
        self.assertIn("assistant_confirm", names)

    def test_tag_page_request_exposes_tag_and_ui_candidates(self):
        decision = select_candidate_tools("打开客户标签页", TOOLS)
        self.assertEqual(decision.route, "tool")
        names = [item["function"]["name"] for item in decision.candidates]
        self.assertIn("ui_show_tag", names)

    def test_deterministic_lists(self):
        cases = {
            "最近几条便签": ("recent_note_list", "notes_list_recent"),
            "回收站里的便签": ("deleted_note_list", "notes_list_deleted"),
            "有哪些置顶便签": ("pinned_note_list", "notes_list_pinned"),
            "待办便签列表": ("todo_note_list", "notes_list_todos"),
        }
        for query, (reason, name) in cases.items():
            with self.subTest(query=query):
                decision = select_candidate_tools(query, TOOLS)
                self.assertEqual(decision.reason, reason)
                self.assertEqual(decision.candidates[0]["function"]["name"], name)


if __name__ == "__main__":
    unittest.main()
