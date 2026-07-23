import importlib.util
import sys
import types
import unittest
from pathlib import Path

import numpy as np


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "providers"
    / "asr"
    / "sherpa_sensevoice_b2.py"
)


def load_module():
    sherpa = types.ModuleType("sherpa_onnx")
    sys.modules.setdefault("sherpa_onnx", sherpa)

    logger_module = types.ModuleType("config.logger")
    logger_module.setup_logging = lambda: None
    sys.modules.setdefault("config", types.ModuleType("config"))
    sys.modules["config.logger"] = logger_module

    base_module = types.ModuleType("core.providers.asr.base")
    base_module.ASRProviderBase = object
    sys.modules.setdefault("core", types.ModuleType("core"))
    sys.modules.setdefault("core.providers", types.ModuleType("core.providers"))
    sys.modules.setdefault("core.providers.asr", types.ModuleType("core.providers.asr"))
    sys.modules["core.providers.asr.base"] = base_module

    dto_module = types.ModuleType("core.providers.asr.dto.dto")
    dto_module.InterfaceType = types.SimpleNamespace(LOCAL="local")
    sys.modules.setdefault(
        "core.providers.asr.dto", types.ModuleType("core.providers.asr.dto")
    )
    sys.modules["core.providers.asr.dto.dto"] = dto_module

    spec = importlib.util.spec_from_file_location("sherpa_sensevoice_b2_tested", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


MODULE = load_module()


class ContextualizeSegmentsTest(unittest.TestCase):
    def test_single_segment_is_clamped_to_utterance(self):
        samples = np.arange(100, dtype=np.float32)
        boundaries = [MODULE.SpeechBoundary(10, 70)]

        result = MODULE.contextualize_segments(samples, boundaries, 20, 30)

        np.testing.assert_array_equal(result[0], samples)

    def test_context_does_not_cross_half_of_adjacent_gap(self):
        samples = np.arange(200, dtype=np.float32)
        boundaries = [
            MODULE.SpeechBoundary(20, 40),
            MODULE.SpeechBoundary(100, 40),
        ]

        result = MODULE.contextualize_segments(samples, boundaries, 50, 50)

        np.testing.assert_array_equal(result[0], samples[0:80])
        np.testing.assert_array_equal(result[1], samples[80:190])

    def test_empty_boundaries_return_no_segments(self):
        samples = np.arange(10, dtype=np.float32)
        self.assertEqual(
            MODULE.contextualize_segments(samples, [], 2, 2),
            [],
        )


if __name__ == "__main__":
    unittest.main()
