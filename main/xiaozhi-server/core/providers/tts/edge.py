import asyncio
import os
import shutil
import time
import uuid
import edge_tts
from datetime import datetime
from config.logger import setup_logging
from core.providers.tts.base import TTSProviderBase
from core.providers.tts.dto.dto import SentenceType
from core.utils.tts import MarkdownCleaner


TAG = __name__
logger = setup_logging()


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

    def to_tts_stream(self, text, opus_handler=None):
        """Stream Edge MP3 through ffmpeg and emit Opus before synthesis ends.

        The original base implementation waits for every Edge audio chunk,
        concatenates the complete MP3, and only then starts decoding. That
        turns Edge's streaming API into a non-streaming request and adds
        several seconds before the first audible packet.
        """
        original_text = text
        cleaned_text = MarkdownCleaner.clean_markdown(text)
        if self._correct_words_pattern:
            cleaned_text = self._correct_words_pattern.sub(
                lambda match: self.correct_words[match.group(0)],
                cleaned_text,
            )
        if not cleaned_text or not cleaned_text.strip():
            return None

        try:
            asyncio.run(
                self._stream_edge_to_opus(
                    cleaned_text,
                    original_text,
                    opus_handler or self.handle_opus,
                )
            )
            return None
        except Exception as exc:
            logger.bind(tag=TAG).warning(
                "Edge流式解码失败，回退完整音频路径: "
                f"text={original_text!r}, error={exc}"
            )
            return super().to_tts_stream(original_text, opus_handler=opus_handler)

    async def _stream_edge_to_opus(self, text, original_text, opus_handler):
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("ffmpeg executable is unavailable")

        started_at = time.perf_counter()
        first_edge_ms = None
        first_pcm_ms = None
        pcm_bytes = 0
        first_packet_announced = False

        process = await asyncio.create_subprocess_exec(
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-fflags",
            "nobuffer",
            "-flags",
            "low_delay",
            "-f",
            "mp3",
            "-i",
            "pipe:0",
            "-ac",
            "1",
            "-ar",
            str(self.conn.sample_rate),
            "-f",
            "s16le",
            "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        async def feed_mp3():
            nonlocal first_edge_ms
            communicate = edge_tts.Communicate(
                text,
                voice=self.voice,
                rate=self.edge_rate,
                volume=self.edge_volume,
                pitch=self.edge_pitch,
            )
            try:
                async for chunk in communicate.stream():
                    if self.conn.client_abort:
                        break
                    if chunk["type"] != "audio":
                        continue
                    if first_edge_ms is None:
                        first_edge_ms = (
                            time.perf_counter() - started_at
                        ) * 1000
                    process.stdin.write(chunk["data"])
                    await process.stdin.drain()
            finally:
                if process.stdin and not process.stdin.is_closing():
                    process.stdin.close()
                    try:
                        await process.stdin.wait_closed()
                    except (BrokenPipeError, ConnectionResetError):
                        pass

        feeder = asyncio.create_task(feed_mp3())
        try:
            while not self.conn.client_abort:
                pcm = await process.stdout.read(4096)
                if not pcm:
                    break

                if first_pcm_ms is None:
                    first_pcm_ms = (time.perf_counter() - started_at) * 1000
                if not first_packet_announced:
                    self.tts_audio_queue.put(
                        (
                            SentenceType.FIRST,
                            None,
                            original_text,
                            getattr(self, "current_sentence_id", None),
                        )
                    )
                    first_packet_announced = True

                pcm_bytes += len(pcm)
                self.opus_encoder.encode_pcm_to_opus_stream(
                    pcm,
                    end_of_stream=False,
                    callback=opus_handler,
                )

            await feeder
            stderr = (await process.stderr.read()).decode(
                "utf-8", errors="replace"
            )
            exit_code = await process.wait()

            if self.conn.client_abort:
                return
            if exit_code != 0:
                raise RuntimeError(
                    f"ffmpeg exited with code {exit_code}: {stderr.strip()}"
                )
            if not first_packet_announced or pcm_bytes == 0:
                raise RuntimeError("Edge TTS stream produced no PCM audio")

            self.opus_encoder.encode_pcm_to_opus_stream(
                b"",
                end_of_stream=True,
                callback=opus_handler,
            )
            logger.bind(tag=TAG).info(
                "TTS_STREAM_LATENCY "
                f"provider=edge edge_first_ms={first_edge_ms:.1f} "
                f"pcm_first_ms={first_pcm_ms:.1f} pcm_bytes={pcm_bytes}"
            )
        finally:
            if not feeder.done():
                feeder.cancel()
                await asyncio.gather(feeder, return_exceptions=True)
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=2)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()

    async def text_to_speak(self, text, output_file):
        try:
            communicate = edge_tts.Communicate(
                text,
                voice=self.voice,
                rate=self.edge_rate,
                volume=self.edge_volume,
                pitch=self.edge_pitch,
            )
            if output_file:
                # 确保目录存在并创建空文件
                os.makedirs(os.path.dirname(output_file), exist_ok=True)
                with open(output_file, "wb") as f:
                    pass

                # 流式写入音频数据
                with open(output_file, "ab") as f:  # 改为追加模式避免覆盖
                    async for chunk in communicate.stream():
                        if chunk["type"] == "audio":  # 只处理音频数据块
                            f.write(chunk["data"])
            else:
                # 返回音频二进制数据
                audio_bytes = b""
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        audio_bytes += chunk["data"]
                return audio_bytes
        except Exception as e:
            error_msg = f"Edge TTS请求失败: {e}"
            raise Exception(error_msg)  # 抛出异常，让调用方捕获
