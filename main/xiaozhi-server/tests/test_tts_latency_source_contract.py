from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class TTSLatencySourceContractTest(unittest.TestCase):
    def test_connection_marks_llm_boundaries(self):
        source = (ROOT / "core" / "connection.py").read_text(encoding="utf-8")
        self.assertIn('"llm_request_started"', source)
        self.assertIn('"llm_first_delta"', source)
        self.assertIn('"llm_stream_completed"', source)

    def test_tts_and_websocket_boundaries_are_marked(self):
        base = (ROOT / "core" / "providers" / "tts" / "base.py").read_text(encoding="utf-8")
        edge = (ROOT / "core" / "providers" / "tts" / "edge.py").read_text(encoding="utf-8")
        sender = (ROOT / "core" / "handle" / "sendAudioHandle.py").read_text(encoding="utf-8")
        self.assertIn('"tts_text_received"', base)
        self.assertIn('"tts_segment_ready"', base)
        self.assertIn('"tts_first_opus"', base)
        self.assertIn('"tts_provider_first_audio_chunk"', edge)
        self.assertIn('"tts_first_ws_audio_send"', sender)


if __name__ == "__main__":
    unittest.main()
