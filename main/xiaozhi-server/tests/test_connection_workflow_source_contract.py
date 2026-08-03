import unittest
from pathlib import Path


CONNECTION = Path(__file__).resolve().parents[1] / "core" / "connection.py"


class ConnectionWorkflowSourceContractTest(unittest.TestCase):
    def test_connection_uses_durable_workflow_and_tool_budget(self) -> None:
        text = CONNECTION.read_text(encoding="utf-8")
        self.assertIn("WORKFLOW_CORRECTNESS_V2", text)
        self.assertIn("pending = start_workflow(query, self._active_tool_route)", text)
        self.assertIn("requires_tool_followup(workflow_operation)", text)
        self.assertIn("self._turn_workflow_operation = None", text)
        self.assertIn(
            'getattr(self, "_pending_tool_workflow", None) is not None',
            text,
        )

    def test_tool_free_text_is_buffered_and_globally_guarded(self) -> None:
        text = CONNECTION.read_text(encoding="utf-8")
        self.assertIn("Buffer tool-free output until the complete sentence", text)
        self.assertIn(
            "if functions is None and not tool_call_flag and content_arguments:",
            text,
        )
        self.assertIn(
            "content_arguments = self._guard_unverified_tool_text(content_arguments)",
            text,
        )


if __name__ == "__main__":
    unittest.main()
