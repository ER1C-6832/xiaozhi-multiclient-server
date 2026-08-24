import importlib.util
import sys
import threading
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch


class _Logger:
    def bind(self, **kwargs):
        return self

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


def _load_llm_factory():
    logger_module = types.ModuleType("config.logger")
    logger_module.setup_logging = lambda: _Logger()
    previous = sys.modules.get("config.logger")
    sys.modules["config.logger"] = logger_module
    try:
        path = Path(__file__).parents[1] / "core" / "utils" / "llm.py"
        spec = importlib.util.spec_from_file_location("llm_factory_tested", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if previous is None:
            sys.modules.pop("config.logger", None)
        else:
            sys.modules["config.logger"] = previous


class LLMFactoryThreadSafetyTests(unittest.TestCase):
    def test_concurrent_first_import_never_exposes_partial_module(self):
        factory = _load_llm_factory()
        module_name = "core.providers.llm.openai.openai"
        original = sys.modules.pop(module_name, None)
        import_started = threading.Event()
        release_import = threading.Event()

        class Provider:
            def __init__(self, config):
                self.config = config

        def controlled_import(name):
            existing = sys.modules.get(name)
            if existing is not None:
                return existing
            partial = types.ModuleType(name)
            sys.modules[name] = partial
            import_started.set()
            self.assertTrue(release_import.wait(timeout=2))
            partial.LLMProvider = Provider
            return partial

        try:
            with patch.object(factory.os.path, "exists", return_value=True), patch.object(
                factory.importlib, "import_module", side_effect=controlled_import
            ):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    first = executor.submit(factory.create_instance, "openai", {"id": 1})
                    self.assertTrue(import_started.wait(timeout=2))
                    second = executor.submit(factory.create_instance, "openai", {"id": 2})
                    release_import.set()
                    instances = [first.result(timeout=2), second.result(timeout=2)]
            self.assertEqual([item.config["id"] for item in instances], [1, 2])
        finally:
            if original is None:
                sys.modules.pop(module_name, None)
            else:
                sys.modules[module_name] = original

    def test_connection_has_fail_closed_llm_unavailable_guard(self):
        source = (Path(__file__).parents[1] / "core" / "connection.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("if self.llm is None:", source)
        self.assertIn('"type": "error"', source)
        self.assertIn('"code": "llm_unavailable"', source)
        self.assertIn("Critical LLM unavailable", source)


if __name__ == "__main__":
    unittest.main()
