import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "providers"
    / "tools"
    / "tool_routing.py"
)
SPEC = importlib.util.spec_from_file_location("tool_routing_tested", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def tool(name, description):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": {}},
        },
    }


TOOLS = [
    tool("notes_create", "创建一条便签或待办，记录用户提供的内容。"),
    tool("notes_search", "按关键词搜索便签。"),
    tool("notes_delete", "删除明确指定的便签。"),
    tool("play_music", "播放音乐或歌曲。"),
    tool("get_weather", "查询其他城市天气。"),
]


class ToolRoutingTest(unittest.TestCase):
    def test_greeting_uses_tool_free_chat(self):
        decision = MODULE.select_candidate_tools("你好", TOOLS)
        self.assertEqual(decision.route, "chat")
        self.assertEqual(decision.candidates, [])
        self.assertEqual(decision.candidate_schema_chars, 0)

    def test_note_creation_selects_small_candidate_set(self):
        decision = MODULE.select_candidate_tools("帮我创建一个客户报价便签", TOOLS)
        names = [item["function"]["name"] for item in decision.candidates]
        self.assertEqual(decision.route, "tool")
        self.assertEqual(names[0], "notes_create")
        self.assertLessEqual(len(names), 5)

    def test_note_parameter_followup_keeps_tool_route(self):
        decision = MODULE.select_candidate_tools(
            "标题写1内容写2",
            TOOLS,
            previous_query="帮我加便签",
            previous_assistant="好的，请告诉我便签的内容",
        )
        names = [item["function"]["name"] for item in decision.candidates]
        self.assertEqual(decision.route, "tool")
        self.assertEqual(decision.reason, "tool_parameter_continuation")
        self.assertEqual(names[0], "notes_create")

    def test_unrelated_message_after_completed_tool_text_stays_chat(self):
        decision = MODULE.select_candidate_tools(
            "你好",
            TOOLS,
            previous_query="帮我加便签",
            previous_assistant="便签已经添加完成",
        )
        self.assertEqual(decision.route, "chat")
        self.assertEqual(decision.reason, "no_explicit_tool_domain")

    def test_generic_assistant_question_does_not_inherit_without_tool_domain(self):
        decision = MODULE.select_candidate_tools(
            "随便聊聊",
            TOOLS,
            previous_query="你好",
            previous_assistant="有什么可以帮助你的吗？",
        )
        self.assertEqual(decision.route, "chat")

    def test_music_does_not_expose_note_tools(self):
        decision = MODULE.select_candidate_tools("播放一首音乐", TOOLS)
        names = [item["function"]["name"] for item in decision.candidates]
        self.assertEqual(names, ["play_music"])

    def test_ambiguous_request_defaults_to_chat(self):
        decision = MODULE.select_candidate_tools("帮我处理一下这个", TOOLS)
        self.assertEqual(decision.route, "chat")
        self.assertEqual(decision.reason, "no_explicit_tool_domain")

    def test_candidate_selection_never_exceeds_schema_cap(self):
        decision = MODULE.select_candidate_tools(
            "创建便签",
            TOOLS,
            max_candidates=5,
            max_schema_chars=350,
        )
        self.assertLessEqual(decision.candidate_schema_chars, 350)


if __name__ == "__main__":
    unittest.main()
