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

    def warning(self, message):
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
                "tool_input_tokens": None,
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
                "tool_input_tokens": None,
            },
        )

    def test_normalizes_gemini_usage_metadata(self):
        usage = SimpleNamespace(
            prompt_token_count=21,
            candidates_token_count=7,
            total_token_count=31,
            cached_content_token_count=0,
            thoughts_token_count=3,
            tool_use_prompt_token_count=2,
        )

        self.assertEqual(
            MODULE.normalize_provider_usage(usage),
            {
                "input_tokens": 21,
                "output_tokens": 7,
                "total_tokens": 31,
                "cached_input_tokens": 0,
                "reasoning_tokens": 3,
                "tool_input_tokens": 2,
            },
        )

    def test_finds_nested_sse_usage(self):
        payload = {
            "event": "message_end",
            "metadata": {
                "usage": {
                    "prompt_tokens": 19,
                    "completion_tokens": 4,
                    "total_tokens": 23,
                }
            },
        }

        usage = MODULE.find_usage_candidate(payload)
        self.assertEqual(usage["total_tokens"], 23)

    def test_negative_provider_counts_are_unknown(self):
        normalized = MODULE.normalize_provider_usage(
            {"prompt_tokens": -1, "completion_tokens": -1, "total_tokens": -1}
        )
        self.assertIsNone(normalized["input_tokens"])
        self.assertIsNone(normalized["output_tokens"])
        self.assertIsNone(normalized["total_tokens"])

        event = MODULE.create_usage_event(
            usage={
                "prompt_tokens": -1,
                "completion_tokens": -1,
                "total_tokens": -1,
            },
            model="xinference-test",
            provider="xinference",
            usage_context=None,
            payload_profile={},
            latency_ms=1,
        )
        self.assertEqual(event["usage_source"], "unavailable")
        self.assertEqual(event["status"], "usage_unavailable")

    def test_stream_recorder_emits_exactly_once(self):
        logger = CapturingLogger()
        recorded = []
        recorder = MODULE.StreamUsageRecorder(
            logger=logger,
            model="test-model",
            provider="test-provider",
            dialogue=[{"role": "user", "content": "do not log me"}],
            usage_context={"record_usage": recorded.append},
        )
        recorder.capture(
            SimpleNamespace(
                id="request-1",
                usage=SimpleNamespace(
                    prompt_tokens=10,
                    completion_tokens=2,
                    total_tokens=12,
                ),
            )
        )

        first = recorder.emit()
        second = recorder.emit()

        self.assertEqual(first["request_id"], "request-1")
        self.assertEqual(first["total_tokens"], 12)
        self.assertIsNone(second)
        self.assertEqual(len(recorded), 1)

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

    def test_budget_caps_output_and_blocks_extra_llm_call(self):
        logger = CapturingLogger()
        tracker = MODULE.LLMUsageTurnTracker(
            logger=logger,
            turn_id="turn-budget-calls",
            session_id="session-budget",
            budget_config={
                "enabled": True,
                "max_llm_calls_per_turn": 1,
                "max_output_tokens_per_request": 120,
            },
        )

        self.assertEqual(tracker.effective_max_output_tokens(500), 120)
        self.assertEqual(tracker.next_request("initial_response")["call_index"], 1)
        with self.assertRaises(MODULE.TokenBudgetExceeded) as caught:
            tracker.next_request("tool_followup")

        self.assertEqual(caught.exception.reason, "llm_call_limit")
        summary = tracker.finish()
        self.assertEqual(summary["status"], "budget_blocked")
        self.assertEqual(summary["budget_reason"], "llm_call_limit")
        self.assertEqual(summary["llm_calls_started"], 1)

    def test_tool_budget_allows_legitimate_four_stage_chain(self):
        tracker = MODULE.LLMUsageTurnTracker(
            logger=CapturingLogger(),
            turn_id="turn-budget-workflow",
            session_id="session-budget",
            budget_config={
                "enabled": True,
                "max_total_tokens_per_turn": 6000,
                "max_llm_calls_per_turn": 4,
                "max_tool_calls_per_turn": 6,
            },
        )

        purposes = (
            "initial_response",
            "resolver_followup",
            "mutation_followup",
            "final_summary",
        )
        self.assertEqual(
            [tracker.next_request(purpose)["call_index"] for purpose in purposes],
            [1, 2, 3, 4],
        )
        tracker.authorize_tools(2)
        tracker.record_tool(
            tool_name="notes_resolve",
            argument_chars=10,
            result_chars=50,
            execution_ms=5,
            action="REQLLM",
            status="completed",
            requires_llm_followup=True,
        )
        tracker.record_tool(
            tool_name="notes_replace_content",
            argument_chars=20,
            result_chars=30,
            execution_ms=5,
            action="RESPONSE",
            status="completed",
            requires_llm_followup=False,
        )

        with self.assertRaises(MODULE.TokenBudgetExceeded) as caught:
            tracker.next_request("unexpected_loop")
        self.assertEqual(caught.exception.reason, "llm_call_limit")

    def test_budget_blocks_tools_after_real_token_limit(self):
        logger = CapturingLogger()
        tracker = MODULE.LLMUsageTurnTracker(
            logger=logger,
            turn_id="turn-budget-token",
            session_id="session-budget",
            budget_config={
                "enabled": True,
                "max_total_tokens_per_turn": 100,
                "max_tool_calls_per_turn": 4,
            },
        )
        tracker.next_request("initial_response")
        tracker.record(
            {
                "usage_source": "provider",
                "tool_count": 2,
                "input_tokens": 95,
                "output_tokens": 10,
                "total_tokens": 105,
                "cached_input_tokens": 0,
                "reasoning_tokens": 0,
                "tool_input_tokens": 0,
            }
        )

        with self.assertRaises(MODULE.TokenBudgetExceeded) as caught:
            tracker.authorize_tools(2)

        self.assertEqual(caught.exception.reason, "turn_token_limit")
        summary = tracker.finish()
        self.assertEqual(summary["known_total_tokens"], 105)
        self.assertEqual(summary["budget_status"], "blocked")

    def test_budget_rejects_whole_tool_batch_before_execution(self):
        tracker = MODULE.LLMUsageTurnTracker(
            logger=CapturingLogger(),
            turn_id="turn-budget-tools",
            session_id="session-budget",
            budget_config={
                "enabled": True,
                "max_tool_calls_per_turn": 2,
            },
        )

        with self.assertRaises(MODULE.TokenBudgetExceeded) as caught:
            tracker.authorize_tools(3)

        self.assertEqual(caught.exception.reason, "tool_call_limit")
        self.assertEqual(tracker.finish()["tool_call_count"], 0)

    def test_budget_summary_callback_is_safe_and_explicit(self):
        summaries = []
        tracker = MODULE.LLMUsageTurnTracker(
            logger=CapturingLogger(),
            turn_id="turn-budget-summary",
            session_id="session-budget",
            model="provider-model",
            budget_config={"enabled": True, "client_telemetry": True},
            summary_callback=summaries.append,
            output_cap_enforced=True,
        )
        tracker.next_request("initial_response")
        tracker.record(
            {
                "usage_source": "provider",
                "tool_count": 0,
                "input_tokens": 10,
                "output_tokens": 2,
                "total_tokens": 12,
                "cached_input_tokens": None,
                "reasoning_tokens": None,
                "tool_input_tokens": None,
            }
        )
        summary = tracker.finish()

        self.assertEqual(summaries, [summary])
        self.assertTrue(summary["budget_enabled"])
        self.assertEqual(summary["budget_status"], "within_budget")
        self.assertEqual(summary["known_total_tokens"], 12)
        self.assertTrue(summary["provider_usage_complete"])
        self.assertTrue(summary["output_cap_enforced"])

    def test_disabled_budget_preserves_unrestricted_call_count(self):
        tracker = MODULE.LLMUsageTurnTracker(
            logger=CapturingLogger(),
            turn_id="turn-budget-disabled",
            session_id="session-budget",
            budget_config={
                "enabled": False,
                "max_llm_calls_per_turn": 1,
            },
        )

        self.assertEqual(tracker.next_request("initial_response")["call_index"], 1)
        self.assertEqual(tracker.next_request("tool_followup")["call_index"], 2)
        self.assertEqual(tracker.effective_max_output_tokens(500), 500)

    def test_client_payload_uses_strict_safe_allowlist(self):
        payload = MODULE.build_client_usage_payload(
            {
                "turn_id": "turn-public",
                "model": "model-public",
                "total_tokens": 10,
                "budget_enabled": True,
                "prompt": "must-not-leak",
                "api_key": "must-not-leak",
                "tool_arguments": "must-not-leak",
            },
            "session-public",
        )
        serialized = json.dumps(payload, ensure_ascii=False)

        self.assertEqual(payload["type"], "token_usage")
        self.assertEqual(payload["session_id"], "session-public")
        self.assertEqual(payload["usage"]["total_tokens"], 10)
        self.assertNotIn("must-not-leak", serialized)
        self.assertNotIn("prompt", payload["usage"])

    def test_chat_profile_blocks_any_tool_schema_before_provider_call(self):
        tracker = MODULE.LLMUsageTurnTracker(
            logger=CapturingLogger(),
            turn_id="turn-chat",
            session_id="session-chat",
            budget_config={
                "enabled": True,
                "profiles": {
                    "chat": {
                        "max_total_tokens_per_turn": 2000,
                        "max_llm_calls_per_turn": 1,
                        "max_tool_calls_per_turn": 0,
                        "max_output_tokens_per_request": 80,
                        "max_tools_per_request": 0,
                        "max_tool_schema_chars_per_request": 0,
                        "max_message_chars_per_request": 5000,
                    }
                },
            },
            budget_profile="chat",
        )

        with self.assertRaises(MODULE.TokenBudgetExceeded) as captured:
            tracker.authorize_payload(
                [{"role": "user", "content": "你好"}],
                [{"type": "function", "function": {"name": "unexpected"}}],
                purpose="initial_response",
            )

        self.assertEqual(captured.exception.reason, "request_tool_count_limit")

    def test_tool_profile_enforces_schema_size_before_provider_call(self):
        tracker = MODULE.LLMUsageTurnTracker(
            logger=CapturingLogger(),
            turn_id="turn-tool",
            session_id="session-tool",
            budget_config={
                "enabled": True,
                "profiles": {
                    "tool": {
                        "max_tool_schema_chars_per_request": 40,
                        "max_tools_per_request": 5,
                    }
                },
            },
            budget_profile="tool",
        )

        with self.assertRaises(MODULE.TokenBudgetExceeded) as captured:
            tracker.authorize_payload(
                [{"role": "user", "content": "创建便签"}],
                [
                    {
                        "type": "function",
                        "function": {
                            "name": "notes_create",
                            "description": "x" * 100,
                        },
                    }
                ],
                purpose="initial_response",
            )

        self.assertEqual(captured.exception.reason, "request_tool_schema_limit")

    def test_summary_exposes_route_counts_without_tool_names(self):
        tracker = MODULE.LLMUsageTurnTracker(
            logger=CapturingLogger(),
            turn_id="turn-route",
            session_id="session-route",
            budget_config={"enabled": True},
            budget_profile="tool",
            route_metadata={
                "request_route": "tool",
                "routing_reason": "explicit_tool_candidates",
                "available_tool_count": 38,
                "selected_tool_count": 2,
                "selected_tool_schema_chars": 900,
            },
        )
        tracker.next_request("initial_response")
        tracker.record(
            {
                "total_tokens": 500,
                "input_tokens": 480,
                "output_tokens": 20,
                "usage_source": "provider",
            }
        )
        summary = tracker.finish()
        payload = MODULE.build_client_usage_payload(summary, "session-route")

        self.assertEqual(payload["usage"]["budget_profile"], "tool")
        self.assertEqual(payload["usage"]["selected_tool_count"], 2)
        self.assertNotIn("selected_tool_names", payload["usage"])


if __name__ == "__main__":
    unittest.main()
