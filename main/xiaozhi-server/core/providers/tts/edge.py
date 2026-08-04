import os
import uuid
import edge_tts
from datetime import datetime
from core.providers.tts.base import TTSProviderBase
from core.utils.tts_latency_trace import mark_tts_latency


class TTSProvider(TTSProviderBase):
    TTS_PARAM_CONFIG = [
        ("ttsVolume", "volume", 0, 100, 50, int),
        ("ttsRate", "speech_rate", -100, 100, 0, int),
        ("ttsPitch", "pitch_rate", -100, 100, 0, int),
    ]

    def __init__(self, config, delete_audio_file):
        super().__init__(config, delete_audio_file)
        if config.get("private_voice"):
            self.voice = config.get("private_voice")
        else:
            self.voice = config.get("voice")
        self.audio_file_type = config.get("format", "mp3")

        volume = config.get("volume", "50")
        self.volume = int(volume) if volume else 50

        speech_rate = config.get("rate", "0")
        self.speech_rate = int(speech_rate) if speech_rate else 0

        pitch_rate = config.get("pitch", "0")
        self.pitch_rate = int(pitch_rate) if pitch_rate else 0

        # 应用百分比调整
        self._apply_percentage_params(config)

        self.edge_rate = f"{self.speech_rate:+}%"
        self.edge_volume = f"{self.volume:+}%"
        self.edge_pitch = f"{self.pitch_rate:+}Hz"

    def generate_filename(self, extension=".mp3"):
        return os.path.join(
            self.output_file,
            f"tts-{datetime.now().date()}@{uuid.uuid4().hex}{extension}",
        )

    async def text_to_speak(self, text, output_file):
        sentence_id = getattr(self, "current_sentence_id", None)
        mark_tts_latency(
            self.conn,
            "tts_provider_request_started",
            sentence_id=sentence_id,
            first_only=True,
            provider="edge",
            text_chars=len(text or ""),
            output_mode="file" if output_file else "memory",
        )
        try:
            communicate = edge_tts.Communicate(
                text,
                voice=self.voice,
                rate=self.edge_rate,
                volume=self.edge_volume,
                pitch=self.edge_pitch,
            )
            total_bytes = 0
            chunk_count = 0
            first_audio_seen = False
            audio_buffer = bytearray()
            file_handle = None
            if output_file:
                os.makedirs(os.path.dirname(output_file), exist_ok=True)
                file_handle = open(output_file, "wb")
            try:
                async for chunk in communicate.stream():
                    if chunk["type"] != "audio":
                        continue
                    data = chunk["data"]
                    chunk_count += 1
                    total_bytes += len(data)
                    if not first_audio_seen:
                        first_audio_seen = True
                        mark_tts_latency(
                            self.conn,
                            "tts_provider_first_audio_chunk",
                            sentence_id=sentence_id,
                            first_only=True,
                            provider="edge",
                            chunk_bytes=len(data),
                        )
                    if file_handle is not None:
                        file_handle.write(data)
                    else:
                        audio_buffer.extend(data)
            finally:
                if file_handle is not None:
                    file_handle.close()
            mark_tts_latency(
                self.conn,
                "tts_provider_audio_completed",
                sentence_id=sentence_id,
                first_only=True,
                provider="edge",
                audio_bytes=total_bytes,
                chunk_count=chunk_count,
            )
            if output_file is None:
                return bytes(audio_buffer)
            return None
        except Exception as e:
            error_msg = f"Edge TTS请求失败: {e}"
            raise Exception(error_msg) from e