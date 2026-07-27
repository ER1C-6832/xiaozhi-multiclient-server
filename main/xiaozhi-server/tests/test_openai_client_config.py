import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "providers"
    / "llm"
    / "openai"
    / "client_config.py"
)

SPEC = importlib.util.spec_from_file_location("openai_client_config_tested", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class OpenAIClientConfigTest(unittest.TestCase):
    def test_default_is_voice_bounded_and_does_not_retry(self):
        result = MODULE.build_openai_transport_config({})

        self.assertEqual(result.max_retries, 0)
        self.assertEqual(result.timeout.connect, 3.0)
        self.assertEqual(result.timeout.read, 15.0)
        self.assertEqual(result.timeout.write, 5.0)
        self.assertEqual(result.timeout.pool, 2.0)

    def test_numeric_timeout_and_explicit_retry_are_supported(self):
        result = MODULE.build_openai_transport_config(
            {"timeout": 8, "max_retries": 1}
        )

        self.assertEqual(result.max_retries, 1)
        self.assertEqual(result.timeout.connect, 8.0)
        self.assertEqual(result.timeout.read, 8.0)

    def test_structured_timeout_is_sanitized(self):
        result = MODULE.build_openai_transport_config(
            {
                "timeout": {
                    "connect": -1,
                    "read": "12",
                    "write": None,
                    "pool": 4,
                },
                "max_retries": 99,
            }
        )

        self.assertEqual(result.max_retries, 5)
        self.assertEqual(result.timeout.connect, 3.0)
        self.assertEqual(result.timeout.read, 12.0)
        self.assertEqual(result.timeout.write, 5.0)
        self.assertEqual(result.timeout.pool, 4.0)


if __name__ == "__main__":
    unittest.main()
