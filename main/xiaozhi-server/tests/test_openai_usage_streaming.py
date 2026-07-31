import unittest
import sys
from types import ModuleType
from types import SimpleNamespace


class StubLogger:
    def bind(self, **kwargs):
        return self

    def debug(self, *args, **kwargs):
        pass

    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


fake_logger_module = ModuleType("config.logger")
fake_logger_module.setup_logging = lambda *args, **kwargs: StubLogger()
sys.modules.setdefault("config.logger", fake_logger_module)

from core.providers.llm.openai.openai import LLMProvider


class FakeStream:
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False

    def __iter__(self):
        return iter(self.chunks)

    def close(self):
        self.closed = True


class FakeCompletions:
    def __init__(self, stream):
        self.stream = stream
        self.request = None

    def create(self, **kwargs):
        self.request = kwargs
        return self.stream


def provider_with(chunks):
    provider = LLMProvider.__new__(LLMProvider)
    provider.model_name = "qwen-test"
    provider.base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    provider.max_tokens = 100
    provider.temperature = None
    provider.top_p = None
    provider.frequency_penalty = None
    stream = FakeStream(chunks)
    completions = FakeCompletions(stream)
    provider.client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions)
    )
    return provider, completions, stream


def usage_context(events):
    return {
        "turn_id": "turn-1",
        "session_id": "session-1",
        "call_index": 1,
        "purpose": "initial_response",
        "record_usage": events.append,
    }


class OpenAIUsageStreamingTest(unittest.TestCase):
    def test_plain_stream_requests_and_collects_usage(self):
        events = []
        provider, completions, stream = provider_with(
            [
                SimpleNamespace(
                    id="req-1",
                    usage=None,
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(content="你好")
                        )
                    ],
                ),
                SimpleNamespace(
                    id="req-1",
                    usage=SimpleNamespace(
                        prompt_tokens=20,
                        completion_tokens=2,
                        total_tokens=22,
                    ),
                    choices=[],
                ),
            ]
        )

        output = list(
            provider.response(
                "session-1",
                [{"role": "user", "content": "你好"}],
                usage_context=usage_context(events),
            )
        )

        self.assertEqual(output, ["你好"])
        self.assertEqual(
            completions.request["stream_options"], {"include_usage": True}
        )
        self.assertTrue(stream.closed)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["total_tokens"], 22)
        self.assertEqual(events[0]["tool_count"], 0)

    def test_function_stream_profiles_tools_and_collects_usage(self):
        events = []
        provider, completions, _ = provider_with(
            [
                SimpleNamespace(
                    id="req-2",
                    usage=None,
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(
                                content="",
                                tool_calls=[SimpleNamespace(index=0)],
                            )
                        )
                    ],
                ),
                SimpleNamespace(
                    id="req-2",
                    usage={
                        "input_tokens": 100,
                        "output_tokens": 8,
                        "total_tokens": 108,
                    },
                    choices=[],
                ),
            ]
        )
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "notes.create",
                    "parameters": {"type": "object"},
                },
            }
        ]

        output = list(
            provider.response_with_functions(
                "session-1",
                [{"role": "user", "content": "加便签"}],
                functions=tools,
                usage_context=usage_context(events),
                tool_choice={
                    "type": "function",
                    "function": {"name": "notes.create"},
                },
            )
        )

        self.assertEqual(len(output), 1)
        self.assertEqual(
            completions.request["stream_options"], {"include_usage": True}
        )
        self.assertEqual(
            completions.request["tool_choice"],
            {
                "type": "function",
                "function": {"name": "notes.create"},
            },
        )
        self.assertEqual(events[0]["input_tokens"], 100)
        self.assertEqual(events[0]["tool_count"], 1)
        self.assertGreater(events[0]["tool_schema_chars"], 0)

    def test_missing_usage_is_reported_without_fake_token_numbers(self):
        events = []
        provider, _, _ = provider_with(
            [
                SimpleNamespace(
                    id="req-3",
                    usage=None,
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(content="完成")
                        )
                    ],
                )
            ]
        )

        list(
            provider.response(
                "session-1",
                [{"role": "user", "content": "测试"}],
                usage_context=usage_context(events),
            )
        )

        self.assertEqual(events[0]["status"], "usage_unavailable")
        self.assertEqual(events[0]["usage_source"], "unavailable")
        self.assertIsNone(events[0]["total_tokens"])


if __name__ == "__main__":
    unittest.main()
