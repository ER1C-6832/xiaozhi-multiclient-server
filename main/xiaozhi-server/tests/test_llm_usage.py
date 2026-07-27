import importlib.util
import json
import unittest
from pathlib import Path
from types import SimpleNamespace


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "providers"
    / "llm"
    / "usage.py"
)

SPEC = importlib.util.spec_from_file_location("llm_usage_tested", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class CapturingLogger:
    def __init__(self):
        self.messages = []

    def info(self, message):
        self.messages.append(message)


class LLMUsageTest(unittest.TestCase):
    def test_normalizes_openai_object_and_details(self):
        usage = SimpleNamespace(
            prompt_tokens=120,
            completion_tokens=30,
            total_tokens=150,
            prompt_tokens_details=SimpleNamespace(cached_tokens=80),
            completion_tokens_details=SimpleNamespace(reasoning_tokens=12),
        )

        self.assertEqual(
            MODULE.normalize_provider_usage(usage),
            {
                "input_tokens": 120,
                "output_tokens": 30,
                "total_tokens": 150,
                "cached_input_tokens": 80,
                "reasoning_tokens": 12,
            },
        )

    def test_normalizes_dashscope_style_dict(self):
        self.assertEqual(
            MODULE.normalize_provider_usage(
                {"input_tokens": 8, "output_tokens": 5}
            ),
            {
                "input_tokens": 8,
                "output_tokens": 5,
                "total_tokens": 13,
                "cached_input_tokens": None,
                "reasoning_tokens": None,
            },
        )

    def test_payload_profile_records_sizes_not_contents(self):
        profile = MODULE.build_payload_profile(
            [
                {"role": "system", "content": "secret-system"},
                {"role": "user", "content": "secret-user"},
                {"role": "tool", "content": "secret-result"},
            ],
            [{"type": "function", "function": {"name": "notes.create"}}],
        )

        serialized = json.dumps(profile, ensure_ascii=False)
        self.assertEqual(profile["message_count"], 3)
        self.assertEqual(profile["tool_count"], 1)
        self.assertGreater(profile["tool_schema_chars"], 0)
        self.assertNotIn("secret", serialized)

    def test_turn_tracker_aggregates_recursive_calls(self):
        logger = CapturingLogger()
        tracker = MODULE.LLMUsageTurnTracker(
            logger=logger,
            turn_id="turn-1",
            session_id="session-1",
            model="qwen-test",
        )
        first = tracker.next_request("initial_response")
        second = tracker.next_request("tool_followup")
        self.assertEqual(first["call_index"], 1)
        self.assertEqual(second["call_index"], 2)

        tracker.record(
            {
                "usage_source": "provider",
                "tool_count": 2,
                "input_tokens": 100,
                "output_tokens": 10,
                "total_tokens": 110,
                "cached_input_tokens": 20,
                "reasoning_tokens": 0,
            }
        )
        tracker.record(
            {
                "usage_source": "provider",
                "tool_count": 0,
                "input_tokens": 40,
                "output_tokens": 5,
                "total_tokens": 45,
                "cached_input_tokens": 0,
                "reasoning_tokens": 0,
            }
        )
        tracker.record_tool(
            tool_name="notes.search",
            argument_chars=20,
            result_chars=80,
            execution_ms=15,
            action="reqllm",
            status="completed",
            requires_llm_followup=True,
        )

        summary = tracker.finish()
        self.assertEqual(summary["api_call_count"], 2)
        self.assertEqual(summary["input_tokens"], 140)
        self.assertEqual(summary["output_tokens"], 15)
        self.assertEqual(summary["total_tokens"], 155)
        self.assertEqual(summary["tool_capable_call_count"], 1)
        self.assertEqual(summary["tool_call_count"], 1)
        self.assertEqual(summary["tool_followup_count"], 1)
        self.assertEqual(summary["tool_argument_chars"], 20)
        self.assertEqual(summary["tool_result_chars"], 80)
        self.assertEqual(summary["tool_execution_ms"], 15)
        self.assertIsNone(tracker.finish())

    def test_turn_total_is_unknown_if_provider_usage_is_missing(self):
        logger = CapturingLogger()
        tracker = MODULE.LLMUsageTurnTracker(
            logger=logger,
            turn_id="turn-2",
            session_id="session-2",
        )
        tracker.record(
            {
                "usage_source": "unavailable",
                "tool_count": 0,
                "input_tokens": None,
                "output_tokens": None,
                "total_tokens": None,
                "cached_input_tokens": None,
                "reasoning_tokens": None,
            }
        )

        summary = tracker.finish("usage_unavailable")
        self.assertIsNone(summary["total_tokens"])
        self.assertEqual(summary["provider_usage_call_count"], 0)


if __name__ == "__main__":
    unittest.main()
