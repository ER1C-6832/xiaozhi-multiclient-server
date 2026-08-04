import unittest

from tools.tts_pcm_pipeline_probe import _tone_pcm


class TTSPCMPipelineProbeTest(unittest.TestCase):
    def test_tone_pcm_size(self):
        pcm = _tone_pcm(24000, 1000, 440.0)
        self.assertEqual(len(pcm), 24000 * 2)


if __name__ == "__main__":
    unittest.main()
