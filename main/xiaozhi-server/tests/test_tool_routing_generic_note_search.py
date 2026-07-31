import unittest

from core.providers.tools.tool_routing import (
    required_tool_choice,
    select_candidate_tools,
    should_wait_for_device_tools,
)


def _tool(name: str, description: str = "") -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description or name,
            "parameters": {"type": "object", "properties": {}},
        },
    }


TOOLS = [
    _tool("notes_search", "按关键词搜索便签"),
    _tool("notes_list_recent", "列出最近便签"),
    _tool("notes_resolve", "按标题或关键词定位便签"),
]


class GenericNoteRoutingTest(unittest.TestCase):
    def test_generic_note_lookup_requires_clarification(self):
        for query in (
            "查便签",
            "查一个便签",
            "帮我找一个笔记",
            "随便查一条便签",
        ):
            with self.subTest(query=query):
                decision = select_candidate_tools(query, TOOLS)
                self.assertEqual(decision.route, "chat")
                self.assertEqual(
                    decision.reason, "generic_note_search_requires_clarification"
                )
                self.assertEqual(decision.candidates, [])
                self.assertIsNone(required_tool_choice(decision))
                self.assertFalse(should_wait_for_device_tools(decision))

    def test_semantic_keyword_search_remains_deterministic(self):
        decision = select_candidate_tools("查王总报价的便签", TOOLS)
        self.assertEqual(decision.route, "tool")
        self.assertEqual(decision.reason, "note_keyword_search")
        self.assertEqual(
            decision.candidates[0]["function"]["name"], "notes_search"
        )
        self.assertEqual(
            required_tool_choice(decision),
            {"type": "function", "function": {"name": "notes_search"}},
        )

    def test_recent_note_request_uses_recent_list(self):
        decision = select_candidate_tools("查一下最近几条便签", TOOLS)
        self.assertEqual(decision.route, "tool")
        self.assertEqual(decision.reason, "recent_note_list")
        self.assertEqual(
            decision.candidates[0]["function"]["name"], "notes_list_recent"
        )


if __name__ == "__main__":
    unittest.main()
