import sys
import unittest
from types import ModuleType, SimpleNamespace


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

from core.providers.llm.ollama.ollama import LLMProvider


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


class OllamaUsageStreamingTest(unittest.TestCase):
    def test_stream_requests_usage_and_records_final_chunk(self):
        stream = FakeStream(
            [
                SimpleNamespace(
                    id="ollama-1",
                    usage=None,
                    choices=[
                        SimpleNamespace(delta=SimpleNamespace(content="完成"))
                    ],
                ),
                SimpleNamespace(
                    id="ollama-1",
                    usage=SimpleNamespace(
                        prompt_tokens=15,
                        completion_tokens=2,
                        total_tokens=17,
                    ),
                    choices=[],
                ),
            ]
        )
        completions = FakeCompletions(stream)
        provider = LLMProvider.__new__(LLMProvider)
        provider.model_name = "qwen3-test"
        provider.is_qwen3 = False
        provider.client = SimpleNamespace(
            chat=SimpleNamespace(completions=completions)
        )
        events = []

        output = list(
            provider.response(
                "session-1",
                [{"role": "user", "content": "测试"}],
                usage_context={"record_usage": events.append},
            )
        )

        self.assertEqual(output, ["完成"])
        self.assertEqual(
            completions.request["stream_options"], {"include_usage": True}
        )
        self.assertTrue(stream.closed)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["provider"], "ollama")
        self.assertEqual(events[0]["total_tokens"], 17)


if __name__ == "__main__":
    unittest.main()
