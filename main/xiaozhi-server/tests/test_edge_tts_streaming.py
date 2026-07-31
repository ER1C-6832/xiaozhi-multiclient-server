import asyncio
import queue
import unittest
from unittest.mock import AsyncMock, patch

from core.providers.tts.dto.dto import SentenceType
from core.providers.tts.edge import TTSProvider


class _FakeStdin:
    def __init__(self):
        self.data = bytearray()
        self.closed = False

    def write(self, data):
        self.data.extend(data)

    async def drain(self):
        return None

    def is_closing(self):
        return self.closed

    def close(self):
        self.closed = True

    async def wait_closed(self):
        return None


class _FakeReader:
    def __init__(self, chunks):
        self.chunks = list(chunks)

    async def read(self, _size=-1):
        return self.chunks.pop(0) if self.chunks else b""


class _FakeProcess:
    def __init__(self):
        self.stdin = _FakeStdin()
        self.stdout = _FakeReader([b"\x00\x00" * 960, b""])
        self.stderr = _FakeReader([b""])
        self.returncode = None

    async def wait(self):
        self.returncode = 0
        return 0

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9


class _FakeCommunicate:
    async def stream(self):
        yield {"type": "audio", "data": b"fake-mp3"}


class _FakeEncoder:
    def __init__(self):
        self.calls = []

    def encode_pcm_to_opus_stream(self, pcm, end_of_stream, callback):
        self.calls.append((pcm, end_of_stream))
        if pcm:
            callback(b"opus")


class EdgeStreamingTest(unittest.TestCase):
    def test_first_event_precedes_incremental_opus_and_flushes_encoder(self):
        provider = TTSProvider.__new__(TTSProvider)
        provider.conn = type(
            "Connection",
            (),
            {"sample_rate": 16000, "client_abort": False},
        )()
        provider.voice = "test"
        provider.edge_rate = "+0%"
        provider.edge_volume = "+0%"
        provider.edge_pitch = "+0Hz"
        provider.current_sentence_id = "sentence-1"
        provider.tts_audio_queue = queue.Queue()
        provider.opus_encoder = _FakeEncoder()
        emitted = []
        process = _FakeProcess()

        with (
            patch(
                "core.providers.tts.edge.shutil.which",
                return_value="/usr/bin/ffmpeg",
            ),
            patch(
                "core.providers.tts.edge.asyncio.create_subprocess_exec",
                new=AsyncMock(return_value=process),
            ),
            patch(
                "core.providers.tts.edge.edge_tts.Communicate",
                return_value=_FakeCommunicate(),
            ),
        ):
            asyncio.run(
                provider._stream_edge_to_opus(
                    "延迟测试正常",
                    "延迟测试正常",
                    emitted.append,
                )
            )

        first_event = provider.tts_audio_queue.get_nowait()
        self.assertEqual(first_event[0], SentenceType.FIRST)
        self.assertEqual(first_event[2], "延迟测试正常")
        self.assertEqual(first_event[3], "sentence-1")
        self.assertEqual(emitted, [b"opus"])
        self.assertEqual(provider.opus_encoder.calls[0][1], False)
        self.assertEqual(provider.opus_encoder.calls[-1], (b"", True))
        self.assertEqual(bytes(process.stdin.data), b"fake-mp3")


if __name__ == "__main__":
    unittest.main()
