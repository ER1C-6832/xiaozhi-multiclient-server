import json
import os
import unittest
from unittest.mock import patch

from core.utils.tts_latency_trace import TTSLatencyTrace


class _Logger:
    def __init__(self):
        self.lines = []

    def bind(self, **_kwargs):
        return self

    def info(self, message):
        self.lines.append(message)


class _Conn:
    def __init__(self):
        self.config = {}
        self.session_id = "session-1"
        self.sentence_id = "sentence-1"
        self.logger = _Logger()


class TTSLatencyTraceTest(unittest.TestCase):
    def test_trace_is_disabled_by_default(self):
        conn = _Conn()
        trace = TTSLatencyTrace(conn)
        trace.start_turn("sentence-1", query_chars=3)
        self.assertEqual(conn.logger.lines, [])

    def test_first_only_and_summary(self):
        conn = _Conn()
        with patch.dict(os.environ, {"XIAOZHI_TTS_LATENCY_TRACE": "1"}):
            trace = TTSLatencyTrace(conn)
            trace.start_turn("sentence-1", query_chars=3, route="chat")
            trace.mark("llm_request_started", sentence_id="sentence-1")
            trace.mark("llm_first_delta", sentence_id="sentence-1", first_only=True)
            trace.mark("llm_first_delta", sentence_id="sentence-1", first_only=True)
            trace.mark("llm_stream_completed", sentence_id="sentence-1")
            trace.mark("tts_text_received", sentence_id="sentence-1", first_only=True)
            trace.mark("tts_segment_ready", sentence_id="sentence-1", first_only=True)
            trace.mark("tts_provider_request_started", sentence_id="sentence-1", first_only=True)
            trace.mark("tts_provider_first_audio_chunk", sentence_id="sentence-1", first_only=True)
            trace.mark("tts_provider_audio_completed", sentence_id="sentence-1", first_only=True)
            trace.mark("tts_first_opus", sentence_id="sentence-1", first_only=True)
            trace.mark("tts_first_ws_audio_send", sentence_id="sentence-1", first_only=True)
            trace.mark("tts_stop_sent", sentence_id="sentence-1", first_only=True)
            summary = trace.emit_summary("sentence-1")

        delta_lines = [line for line in conn.logger.lines if line.startswith("TTS_LATENCY ")]
        first_delta_lines = [line for line in delta_lines if '"stage": "llm_first_delta"' in line]
        self.assertEqual(len(first_delta_lines), 1)
        self.assertIsNotNone(summary)
        self.assertIn("turn_to_first_ws_audio_ms", summary)
        summary_line = next(
            line for line in conn.logger.lines if line.startswith("TTS_LATENCY_SUMMARY ")
        )
        parsed = json.loads(summary_line.split(" ", 1)[1])
        self.assertEqual(parsed["sentence_id"], "sentence-1")


if __name__ == "__main__":
    unittest.main()
