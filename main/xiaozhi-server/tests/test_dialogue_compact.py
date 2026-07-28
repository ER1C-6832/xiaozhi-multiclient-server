import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "core" / "utils" / "dialogue.py"
)
SPEC = importlib.util.spec_from_file_location("dialogue_tested", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class CompactDialogueTest(unittest.TestCase):
    def test_excludes_fewshot_and_tool_protocol_messages(self):
        dialogue = MODULE.Dialogue()
        dialogue.put(MODULE.Message(role="system", content="large system"))
        dialogue.put(
            MODULE.Message(role="user", content="fewshot", is_temporary=True)
        )
        dialogue.put(
            MODULE.Message(
                role="assistant",
                tool_calls=[{"id": "1", "function": {"name": "x"}}],
            )
        )
        dialogue.put(
            MODULE.Message(role="tool", tool_call_id="1", content="secret result")
        )
        dialogue.put(MODULE.Message(role="user", content="你好"))

        result = dialogue.get_compact_dialogue("compact system")

        self.assertEqual(result[0], {"role": "system", "content": "compact system"})
        self.assertEqual(result[-1], {"role": "user", "content": "你好"})
        self.assertNotIn("fewshot", str(result))
        self.assertNotIn("secret result", str(result))

    def test_history_is_bounded_from_the_newest_messages(self):
        dialogue = MODULE.Dialogue()
        for index in range(10):
            dialogue.put(MODULE.Message(role="user", content=f"message-{index}"))

        result = dialogue.get_compact_dialogue(
            "compact", max_history_messages=2, max_history_chars=100
        )

        self.assertEqual(
            [message["content"] for message in result[1:]],
            ["message-8", "message-9"],
        )


if __name__ == "__main__":
    unittest.main()
